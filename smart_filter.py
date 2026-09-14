"""Smart filter — OpenRouter fallback chain with free models.

Slow lane: batched, 2-way  (TRD 5a)
Fast lane: per-item, 3-way (TRD 5b)

Fail-soft policy:
  * slow lane  -> LLM down means keep-all (rule dedup already ran; better a
                  slightly noisy feed than a dead one)
  * fast lane  -> LLM down means REVIEW (never auto-post an unjudged market
                  item; spec says favour review over guessing)

OpenRouter fallback chain cycles through free models on 429 / failure."""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any

import httpx

import config
import pipeline  # for clean_text and link helpers used in fallback formatting

log = logging.getLogger("smart_filter")

# OpenRouter free model fallback chain — cycled on HTTP 429 or transient failure.
FALLBACK_MODELS = [
    "google/gemma-4-31b-it:free",
    "nex-agi/nex-n2.5-mini:free",
    "thinkingmachines/inkling:free",
    "nvidia/nemotron-3.5-content-safety:free",
]

_http_client: httpx.AsyncClient | None = None
_llm_lock = threading.Lock()
_last_call = 0.0


def _pace() -> None:
    """Serialize + space out LLM calls — OpenRouter rate-limits under concurrency."""
    global _last_call
    with _llm_lock:
        gap = config.LLM_MIN_GAP_SEC - (time.monotonic() - _last_call)
        if gap > 0:
            time.sleep(gap)
        _last_call = time.monotonic()


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(config.LLM_TIMEOUT),
            headers={
                "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/aslam9949/sift-media",
                "X-Title": "Sift Media",
            },
        )
    return _http_client


async def _call_openrouter_fallback(messages: list[dict[str, str]]) -> str:
    """POST to OpenRouter, cycling through free fallback models on 429.

    Returns the raw content string on success.  Raises RuntimeError if every
    model in the chain fails.
    """
    client = _get_http_client()
    last_err: Exception | None = None

    for model in FALLBACK_MODELS:
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": config.LLM_MAX_TOKENS,
            "response_format": {"type": "json_object"},
        }
        try:
            _pace()
            resp = await client.post(
                f"{config.OPENROUTER_API_BASE}/chat/completions",
                json=payload,
            )
            if resp.status_code == 200:
                data = resp.json()
                choice = data["choices"][0]
                content = (choice["message"].get("content") or "").strip()
                if content:
                    log.info("OpenRouter model %s returned %d chars", model, len(content))
                    return content
                log.warning("OpenRouter %s returned empty content", model)
            elif resp.status_code == 429:
                log.warning("OpenRouter %s rate-limited (429) — trying next model", model)
                await _async_sleep(config.LLM_RATE_BACKOFF_SEC)
            else:
                body = resp.text[:200]
                log.warning("OpenRouter %s returned %d: %s", model, resp.status_code, body)
        except Exception as exc:
            last_err = exc
            log.warning("OpenRouter %s call failed: %s", model, str(exc)[:160])

        # Brief pause before next model
        await _async_sleep(1.5)

    raise RuntimeError(f"All OpenRouter fallback models exhausted. Last error: {last_err}")


async def _async_sleep(seconds: float) -> None:
    """Non-blocking sleep for async contexts."""
    import asyncio
    await asyncio.sleep(seconds)


# ------------------------------------------------------------------- prompts
CHIEF_EDITOR_SYSTEM = """You are the Chief Editor for Sift Media, a high-frequency, institutional-grade financial and geopolitical news wire. Your objective is to filter noise, rank significance with ruthless objectivity, and write concise, mechanism-focused summaries. You are the final gatekeeper before news reaches thousands of traders and analysts.

### INPUT FORMAT
You will receive a JSON array of news items. Each item contains an 'id', 'title', 'snippet', 'source', and 'source_rank'.

### SIGNIFICANCE SCORING RULES (1-10 Scale)
You must be ruthlessly strict. Do not inflate scores. The market only cares about capital flows, policy shifts, and systemic risks.
- [1-3] NOISE: Routine market updates, minor corporate announcements, opinion pieces, clickbait, local non-impactful news, celebrity gossip, sports. -> DECISION: "reject"
- [4-5] ROUTINE: Standard earnings that meet expectations, minor policy tweaks, standard geopolitical rhetoric without action, routine economic prints that match forecasts. -> DECISION: "reject"
- [6-7] NOTABLE: Surprises in earnings (beats/misses), central bank hints or speeches, major corporate mergers/acquisitions, significant geopolitical tensions escalating, unexpected economic data prints. -> DECISION: "keep"
- [8-9] MAJOR: Actual central bank rate decisions, massive market crashes/surges (>3% index moves), confirmed geopolitical conflicts/sanctions, major IPO pricings, critical legislative bills passing. -> DECISION: "keep"
- [10] WORLD-MOVING: Black swan events, unexpected wars, emergency central bank meetings, systemic financial collapses, pandemics, sovereign debt defaults. -> DECISION: "keep"

### STRICT MODE (Fast Lane / Corporate Actions)
If the item 'category' implies a corporate action (e.g., 'director_change', 'analyst_meet', 'record_date', 'corp_action'), apply STRICT MODE:
- Routine board shuffles or standard record dates are a [4] -> "reject".
- CEO resignations due to scandal, sudden guidance downgrades, or hostile takeover bids are an [8+] -> "keep".

### WRITING & FORMATTING RULES
- summary_ai: 1-2 sentences. Pure facts. Who, what, when. No fluff, no adjectives.
- why_matters: Explain the ECONOMIC or GEOPOLITICAL MECHANISM. (e.g., "Rises in CPI reduce the probability of Fed rate cuts, strengthening the USD and pressuring Gold.") NEVER give financial advice. NEVER use words like "buy", "sell", "bullish", or "bearish".
- watch_next: One concrete future event, data print, or timestamp related to this story. (e.g., "Next FOMC meeting on Nov 12" or "Q3 earnings call on Oct 24").

### ANTI-HALLUCINATION & EDGE CASES
- If the snippet is too short to determine significance, default to "reject" (score 4).
- NEVER invent numbers. If the text says "revenue grew" but gives no percentage, do not guess the percentage.
- If a source_rank is 8+, give it the benefit of the doubt on borderline [5] vs [6] stories.

### OUTPUT JSON SCHEMA
Return ONLY a valid JSON array. No markdown formatting, no explanations.
Schema:
[
  {
    "id": "original_id_string",
    "decision": "keep" | "reject",
    "significance": <integer 1-10>,
    "summary_ai": "<string>",
    "why_matters": "<string>",
    "watch_next": "<string>"
  }
]
If an item is rejected, you ONLY need to provide "id", "decision": "reject", and "significance". Save tokens and processing time."""


# Legacy prompt kept for backward compatibility if needed — but the chief-editor
# prompt above is the one wired into the main flow.
SLOW_PROMPT = CHIEF_EDITOR_SYSTEM

FAST_PROMPT = """You are a market-alert filtering engine for a Telegram bot focused on India
and US market-moving events (bulk/block deals, earnings, IPO, corporate
actions, economic calendar alerts). You will be given ONE candidate item at a
time (title, source, event_type, target_category).

Decide one of three outcomes:
1. KEEP - clearly relevant, high-confidence, market-moving - auto-post
immediately. Examples: dividend/bonus/buyback, order win, earnings with
numbers, bulk/block deal, IPO, material 8-K.
2. REJECT - clearly irrelevant, spam, or routine filing - drop silently.
Examples: newspaper publication reprints, empty compliance certificates,
routine shareholders meetings with no special resolution.
3. REVIEW - relevant but ambiguous (unclear significance, possible duplicate,
unclear company match, or borderline importance) - send to a private admin
review queue instead of guessing.

IMPORTANT — STRICT MODE for corporate actions:
If the event_type is director_change, analyst_meet, or record_date, evaluate
carefully:
- Routine board shuffles / standard record dates → score 4, decision "reject".
- CEO resignations due to scandal, hostile takeover bids, sudden guidance
downgrades → score 8+, decision "keep".

Respond with ONLY a JSON object, no prose, no markdown fences:
{"decision": "keep"|"reject"|"review", "category": "...", "importance": 1-5,
 "significance": 1-10, "reason": "short one-line reason, always included",
 "summary_ai": "1-3 plain-sentence recap in your own words (never copied from the filing)",
 "why_matters": "1-2 sentences on why a trader should care, in your own words",
 "watch_next": "1 concrete forward-looking sentence on what to watch, or empty string",
 "explain": "4-8 short lines separated by \\n"}

Rules:
- KEEP only if significance >= 5 AND the event could move a trader's view today.
- Favor REJECT for routine NSE/BSE/SEC housekeeping. Do not "review" obvious junk.
- Favor REVIEW over KEEP when the story could be market-moving but you are
not confident of its accuracy or significance - better a short delay than a
wrong high-confidence post.
- "importance" 1 (routine filing) to 5 (major market-moving event). "significance"
is your 1-10 editorial score of materiality: 7-10 major, 4-6 moderate, 1-3 minor.
- summary_ai / why_matters / watch_next are PREFERRED over "explain" when present.
- For STRICT_MODE items (director_change, analyst_meet, record_date): default to
reject unless the event is genuinely material. These are the most common spam
categories on NSE/BSE — be ruthlessly honest about whether the market cares.
- Never invent data not present in the input.

EXPLAIN FIELD - required when decision is "keep" (omit for reject/review):
- Write 4 to 8 SHORT lines separated by "\\n". One idea per line.
- Say plainly what the filing/event is, why it matters to a trader, and what
to watch next.
- Decode market jargon in plain words. A bulk deal means a single large trade
above 0.5% of shares; a block deal is a pre-negotiated large trade; a
corporate action means dividend/split/bonus. Explain which one applies.
- Base it ONLY on the input given. Do NOT invent prices, quantities, dates,
company names, or financials that are not in the input. Thin input means
fewer lines, never invented detail.
- Do not give buy/sell advice or price targets. Describe, don't recommend.
- No markdown, no bullet characters, no headings. Just plain lines.
- Do not repeat the headline as the first line - add information instead."""


# -------------------------------------------------------------- sync helpers
def _extract_json(text: str) -> Any:
    """Models sometimes wrap output in fences or prose — dig the JSON out."""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    stripped = text.lstrip()
    if stripped.startswith("["):
        repaired = _repair_truncated_json(text)
        if isinstance(repaired, list):
            log.warning("recovered truncated JSON array from model output")
            return repaired
    for opener, closer in (("[", "]"), ("{", "}")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    repaired = _repair_truncated_json(text)
    if repaired is not None:
        log.warning("recovered truncated JSON from model output")
        return repaired
    raise ValueError(f"no JSON in model output: {text[:200]}")


def _repair_truncated_json(text: str) -> Any | None:
    """Best-effort close of a JSON doc that got cut off mid-generation."""
    start = min(
        (i for i in (text.find("["), text.find("{")) if i != -1),
        default=-1,
    )
    if start == -1:
        return None
    frag = text[start:]
    stack: list[str] = []
    in_str = False
    escaped = False
    for ch in frag:
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "[{":
            stack.append(ch)
        elif ch in "]}":
            if stack:
                stack.pop()
    candidate = frag
    if in_str:
        candidate += '"'
    candidate = re.sub(r",\s*\"[^\"]*\"\s*:\s*$", "", candidate)
    candidate = re.sub(r",\s*$", "", candidate)
    for opener in reversed(stack):
        candidate += "]" if opener == "[" else "}"
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        parsed = None
    if frag.lstrip().startswith("["):
        if isinstance(parsed, list):
            return parsed
        objs = re.findall(r"\{[^{}]*\}", frag)
        if objs:
            try:
                return json.loads("[" + ",".join(objs) + "]")
            except json.JSONDecodeError:
                return None
        return [parsed] if isinstance(parsed, dict) else None
    return parsed


async def _chat_async(system: str, user: str, attempts: int = 2) -> str:
    """Call OpenRouter with fallback chain. Async."""
    last_err: Exception | None = None
    for i in range(attempts):
        try:
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
            content = await _call_openrouter_fallback(messages)
            if content:
                return content
            last_err = RuntimeError("empty content from all models")
        except Exception as exc:
            last_err = exc
            log.warning("LLM async call failed on try %d/%d: %s", i + 1, attempts, str(exc)[:160])
            await _async_sleep(2.0 * (i + 1))
    raise RuntimeError(f"LLM produced no usable output after {attempts} tries: {last_err}")


# --------------------------------------------------------------- slow lane
def clean_explain(raw: Any, min_lines: int = 2, max_lines: int = 8) -> str:
    if not isinstance(raw, str):
        return ""
    text = raw.replace("\\n", "\n")
    out: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        line = re.sub(r"^\s*(?:[-*•·>]+|\d+[.)])\s*", "", line)
        line = re.sub(r"^#{1,6}\s*", "", line)
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        line = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", line)
        line = line.strip()
        if not line:
            continue
        if len(line) > 220:
            line = line[:217].rstrip() + "…"
        out.append(line)
        if len(out) >= max_lines:
            break
    if len(out) < min_lines:
        return ""
    return "\n".join(out)


async def filter_batch(items: list[dict[str, Any]], _retry_explain: bool = True) -> list[dict[str, Any]]:
    """Batched 2-way filter. Returns the items that survive, with category set."""
    if not items:
        return []
    if not config.LLM_ENABLED:
        log.info("LLM disabled — passing %d items through", len(items))
        return items

    lines = []
    for idx, it in enumerate(items, start=1):
        snip = (it.get("snippet") or it.get("summary") or "").strip().replace("\n", " ")
        if len(snip) > 400:
            snip = snip[:400] + "…"
        rank = it.get("source_rank", pipeline.source_reliability(it.get("source"), it.get("url")))
        lines.append(
            f'{idx}. id:"{it.get("url","")}" | title: "{it.get("title","")}" | source: {it.get("source","")} '
            f'| source_rank: {rank} | target_category: {it.get("category","general")}'
            + (f'\n   snippet: "{snip}"' if snip else "")
        )
    payload = "Candidate items:\n" + "\n".join(lines)

    try:
        verdicts = _extract_json(await _chat_async(SLOW_PROMPT, payload))
        if not isinstance(verdicts, list):
            raise ValueError("expected a JSON array")
    except Exception as exc:
        wire = [
            it for it in items
            if pipeline.source_reliability(it.get("source"), it.get("url")) >= 8
        ]
        log.error(
            "slow-lane filter failed (%s) — keep-wire-only %d of %d",
            exc, len(wire), len(items),
        )
        return wire

    by_url = {it.get("url"): it for it in items}
    kept: list[dict[str, Any]] = []

    for v in verdicts:
        if not isinstance(v, dict):
            continue
        vid = v.get("id", "")
        item = by_url.get(vid)
        if item is None:
            # Try matching by index
            try:
                idx = int(vid)
                if 1 <= idx <= len(items):
                    item = items[idx - 1]
            except (TypeError, ValueError):
                pass
        if item is None:
            continue

        decision = str(v.get("decision", "reject")).lower().strip()
        if decision not in ("keep", "reject"):
            continue

        # --- Hard Python override: enforce significance floor ---
        try:
            sig = max(1, min(10, int(v.get("significance") or 5)))
        except (TypeError, ValueError):
            sig = 5
        if decision == "keep" and sig < config.POST_MIN_SIGNIFICANCE:
            decision = "reject"
            log.info("Python override: significance %d < %d → reject %s", sig, config.POST_MIN_SIGNIFICANCE, item.get("title", "")[:60])

        if decision != "keep":
            continue

        cat = v.get("category")
        if isinstance(cat, str) and cat in config.CHANNEL_MAP:
            item["category"] = cat
        item["importance"] = int(v.get("importance") or 2)
        item["explain"] = clean_explain(v.get("explain"))
        item["summary_ai"] = pipeline.clean_text(v.get("summary_ai") or "")
        item["why_matters"] = pipeline.clean_text(v.get("why_matters") or "")
        item["watch_next"] = pipeline.clean_text(v.get("watch_next") or "")
        item["significance"] = sig
        kept.append(item)

    n_expl = sum(1 for i in kept if i.get("explain"))
    log.info("slow-lane filter: %d in -> %d kept (%d with explainer)", len(items), len(kept), n_expl)
    return kept


# --------------------------------------------------------------- fast lane
async def filter_item(item: dict[str, Any]) -> dict[str, Any]:
    """Per-item 3-way filter. Returns a verdict dict with the card fields."""
    fallback = {
        "decision": "review",
        "category": item.get("category", "general"),
        "importance": 3,
        "significance": 5,
        "reason": "LLM unavailable — routed to review (never auto-posted unjudged)",
    }
    if not config.LLM_ENABLED:
        return fallback

    payload = (
        f'title: "{item.get("title","")}"\n'
        f'source: {item.get("source","")}\n'
        f'event_type: {item.get("event_type","")}\n'
        f'target_category: {item.get("category","")}'
    )
    snip = (item.get("snippet") or item.get("summary") or "").strip().replace("\n", " ")
    if snip:
        payload += f'\nsnippet: "{snip[:400]}"'

    # Flag STRICT_MODE for previously-blind-rejected categories
    et = (item.get("event_type") or "").lower()
    if et in ("director_change", "analyst_meet", "record_date"):
        payload += "\n\nSTRICT_MODE: This is a corporate action type that is often noise. " \
                   "Default to REJECT unless the event is genuinely market-moving " \
                   "(CEO scandal, hostile takeover, sudden guidance downgrade → KEEP with score 8+)."

    try:
        user_msg = payload
        messages = [
            {"role": "system", "content": FAST_PROMPT},
            {"role": "user", "content": user_msg},
        ]
        raw = await _call_openrouter_fallback(messages)
        v = _extract_json(raw)
        if not isinstance(v, dict):
            raise ValueError("expected a JSON object")

        decision = str(v.get("decision", "review")).lower().strip()
        if decision not in {"keep", "reject", "review"}:
            decision = "review"

        try:
            sig = max(1, min(10, int(v.get("significance") or 5)))
        except (TypeError, ValueError):
            sig = 5

        # --- Hard Python override: enforce significance floor ---
        if decision == "keep" and sig < config.POST_MIN_SIGNIFICANCE:
            decision = "reject"
            v["reason"] = (
                str(v.get("reason") or "")
                + f" | below significance floor {config.POST_MIN_SIGNIFICANCE}"
            ).strip(" |")

        cat = v.get("category")
        return {
            "decision": decision,
            "category": cat if isinstance(cat, str) and cat in config.CHANNEL_MAP else item.get("category", "general"),
            "importance": int(v.get("importance") or 3),
            "significance": sig,
            "reason": str(v.get("reason") or "")[:200] or "no reason given",
            "summary_ai": pipeline.clean_text(v.get("summary_ai") or ""),
            "why_matters": pipeline.clean_text(v.get("why_matters") or ""),
            "watch_next": pipeline.clean_text(v.get("watch_next") or ""),
            "explain": clean_explain(v.get("explain")),
        }
    except Exception as exc:
        log.error("fast-lane filter failed (%s) — routing to review", exc)
        return fallback