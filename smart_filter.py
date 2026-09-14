"""Smart filter — b.ai (OpenAI-compatible) DeepSeek calls.

Slow lane: batched, 2-way  (TRD 5a)
Fast lane: per-item, 3-way (TRD 5b)

Fail-soft policy:
  * slow lane  -> LLM down means keep-all (rule dedup already ran; better a
                  slightly noisy feed than a dead one)
  * fast lane  -> LLM down means REVIEW (never auto-post an unjudged market
                  item; spec says favour review over guessing)
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any

import config
import pipeline  # for clean_text and link helpers used in fallback formatting

log = logging.getLogger("smart_filter")

_client = None
_llm_lock = threading.Lock()
_last_call = 0.0


def _pace() -> None:
    """Serialize + space out LLM calls — b.ai returns 429 under concurrency."""
    global _last_call
    with _llm_lock:
        gap = config.LLM_MIN_GAP_SEC - (time.monotonic() - _last_call)
        if gap > 0:
            time.sleep(gap)
        _last_call = time.monotonic()

SLOW_PROMPT = """You are a news filtering and deduplication engine for a Telegram news aggregation
bot. You will be given a numbered batch of candidate news items (title, source
domain, and target category) collected from RSS feeds and Google News.

Your job is QUALITY over volume. A quiet, high-signal feed beats a flood.

For each item, judge:
1. RELEVANT - does the title genuinely match its target category, not just
coincidentally contain a keyword? (e.g. "Gold's Gym opens new branch" is NOT
gold-market news)
2. QUALITY - reject pure clickbait, opinion/op-ed pieces, stock-picker blogs,
listicles, and low-substance roundups (e.g. "5 things to know today", "top 10
stocks to buy") unless clearly high-value wire news
3. DUPLICATE - group items reporting the same underlying story/event even if
worded differently, and pick the single best representative (prefer the
higher-quality/more recognizable source domain)
4. MATERIALITY - only keep stories a serious reader would care about today

CATEGORY BOUNDARIES - these three are easy to confuse, be strict:
- "geopolitics" = relations BETWEEN countries: wars, ceasefires, military
action, sanctions, treaties, alliances (NATO/UN/BRICS), diplomacy, summits,
border disputes, trade wars between nations.
- "us_politics" = US DOMESTIC politics: Congress, elections, party fights,
Supreme Court, US budget/legislation.
- "india_politics" = India DOMESTIC politics: Parliament, state elections,
party politics, domestic policy.
A story about one country striking, sanctioning, or negotiating with ANOTHER
country is geopolitics, even when a domestic leader is the subject. A story
about a leader's standing at home is domestic politics.

Respond with ONLY a JSON array, no prose, no markdown fences. One object per
input item id:

[
  {"id": 3, "keep": true, "category": "gold_news", "duplicate_of": null, "importance": 3,
   "summary_ai": "Gold pushed to a fresh record as traders priced in a September rate cut.",
   "why_matters": "A weaker dollar and steady central-bank buying keep the uptrend intact.",
   "watch_next": "This week's US inflation print - a hot number could stall the rally.",
   "significance": 7,
   "explain": "Gold pushed to a fresh record as traders priced in a September rate cut.\\nA weaker dollar makes bullion cheaper for overseas buyers, so demand rose.\\nCentral banks have also kept buying through the year, tightening supply.\\nFor Indian buyers the move is amplified by a soft rupee.\\nWatch this week's US inflation print - a hot number would stall the rally."},
  {"id": 4, "keep": false, "reason": "off-topic"},
  {"id": 5, "keep": true, "category": "gold_news", "duplicate_of": 3, "importance": 2}
]

Rules:
- KEEP only if relevant AND not clickbait AND significance >= 5. If unsure, keep=false.
- "summary_ai": 1-3 plain-sentence recap of what happened, in your own words
  (never copied from the article). "why_matters": 1-2 sentences on why a reader
  should care. "watch_next": 1 concrete, forward-looking sentence on the next
  development to watch, or "" if none. These three are PREFERRED over "explain".
- "explain" is the OLD fallback field - still include it (4-8 short lines), used
  only if summary_ai is empty.
- "significance" is YOUR editorial score of how material the story is: 7-10
  major (wars, big policy, market-moving), 4-6 moderate, 1-3 minor. Score the
  event, not the headline hype. Do not inflate to force a keep.
- "duplicate_of" is the id of the item you consider the canonical version of the
  same story; null if unique.
- "importance" is 1 (routine) to 5 (major/market-moving) - be conservative.
- Never invent ids not present in the input.
- Prefer wire/major newsrooms (Reuters, BBC, TOI, NDTV, ET, Livemint) over
  unknown Google News syndicates when picking a duplicate winner.

EXPLAIN / SUMMARY FIELD RULES - whenever keep is true:
- Base it ONLY on the title, snippet and source given to you. Do NOT invent
numbers, quotes, dates, or names that are not in the input. If the input is
thin, write fewer lines rather than inventing detail.
- Never copy the article's sentences verbatim - summarise in your own words.
- No markdown, no bullet characters, no headings. Just plain lines.
- Do not repeat the headline as the first line - add information instead."""

FAST_PROMPT = """You are a market-alert filtering engine for a Telegram bot focused on India
and US market-moving events (bulk/block deals, earnings, IPO, corporate
actions, economic calendar alerts). You will be given ONE candidate item at a
time (title, source, event_type, target_category).

Decide one of three outcomes:
1. KEEP - clearly relevant, high-confidence, market-moving - auto-post
immediately. Examples: dividend/bonus/buyback, order win, earnings with
numbers, bulk/block deal, IPO, material 8-K.
2. REJECT - clearly irrelevant, spam, or routine filing - drop silently.
Examples: newspaper publication reprints, director changes, empty analyst-meet
updates, shareholders meeting with no special resolution, compliance certificates.
3. REVIEW - relevant but ambiguous (unclear significance, possible duplicate,
unclear company match, or borderline importance) - send to a private admin
review queue instead of guessing

Respond with ONLY a JSON object, no prose, no markdown fences:
{"decision": "keep", "category": "...", "importance": 1-5, "significance": 1-10, "reason": "short one-line reason, always included",
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


def _get_client():
    global _client
    if _client is None:
        from openai import OpenAI

        _client = OpenAI(
            api_key=config.LLM_API_KEY,
            base_url=config.LLM_BASE_URL,
            timeout=config.LLM_TIMEOUT,
            max_retries=2,
        )
    return _client


def _extract_json(text: str) -> Any:
    """Models sometimes wrap output in fences or prose — dig the JSON out."""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # If the model was asked for an array, don't let the object-fallback below
    # return just the first element — try array repair first.
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

    # Truncated output (hit the token cap mid-object). Repair rather than
    # discard: close any open string, then close open braces/brackets.
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

    # Track structure while respecting strings and escapes.
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
    # Drop a dangling ", key": or trailing comma before closing.
    candidate = re.sub(r",\s*\"[^\"]*\"\s*:\s*$", "", candidate)
    candidate = re.sub(r",\s*$", "", candidate)
    for opener in reversed(stack):
        candidate += "]" if opener == "[" else "}"

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        parsed = None

    # If the source fragment was an array, the result must stay an array — a
    # brace-balanced repair can otherwise yield just the first object.
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


def _chat(system: str, user: str, attempts: int = 3) -> str:
    """Call the model, retrying when it returns empty content.

    This is a reasoning model: it spends tokens on an internal
    `reasoning_content` channel before emitting the real answer. Occasionally a
    response comes back with reasoning but an EMPTY content field. That's
    transient, so retry rather than treating it as a hard failure.
    """
    last_err: Exception | None = None
    for i in range(attempts):
        try:
            _pace()
            resp = _get_client().chat.completions.create(
                model=config.LLM_MODEL,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0,
                max_tokens=config.LLM_MAX_TOKENS,
            )
            choice = resp.choices[0]
            content = (choice.message.content or "").strip()
            if content:
                return content
            last_err = RuntimeError(
                f"empty content (finish={choice.finish_reason}, "
                f"completion_tokens={getattr(resp.usage, 'completion_tokens', '?')})"
            )
            log.warning("empty model content on try %d/%d — retrying", i + 1, attempts)
        except Exception as exc:
            last_err = exc
            is_429 = "429" in str(exc) or "rate" in str(exc).lower()
            log.warning("LLM call failed on try %d/%d: %s", i + 1, attempts, str(exc)[:160])
            if is_429 and i < attempts - 1:
                # Rate limited — back off hard before retrying.
                time.sleep(config.LLM_RATE_BACKOFF_SEC * (2 ** i))
                continue
        if i < attempts - 1:
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"LLM produced no usable output after {attempts} tries: {last_err}")


# --------------------------------------------------------------- slow lane
def clean_explain(raw: Any, min_lines: int = 2, max_lines: int = 8) -> str:
    """Normalise the model's explainer into 2-8 clean plain-text lines.

    Strips markdown/bullet noise, drops empties, caps line and total length.
    Returns "" if there's nothing usable, and callers must treat "" as
    "post without an explainer" rather than failing.
    """
    if not isinstance(raw, str):
        return ""
    text = raw.replace("\\n", "\n")
    out: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        # strip leading bullets / numbering / markdown headings
        line = re.sub(r"^\s*(?:[-*•·>]+|\d+[.)])\s*", "", line)
        line = re.sub(r"^#{1,6}\s*", "", line)
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)   # **bold**
        line = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", line)  # *italic*
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


def filter_batch(items: list[dict[str, Any]], _retry_explain: bool = True) -> list[dict[str, Any]]:
    """Batched 2-way filter. Returns the items that survive, with category set."""
    if not items:
        return []
    if not config.LLM_ENABLED:
        log.info("LLM disabled — passing %d items through", len(items))
        return items

    lines = []
    for idx, it in enumerate(items, start=1):
        # Include the snippet — the model needs real context to write a useful
        # explainer, and it also sharpens the relevance judgement.
        snip = (it.get("snippet") or it.get("summary") or "").strip().replace("\n", " ")
        if len(snip) > 400:
            snip = snip[:400] + "…"
        lines.append(
            f'{idx}. title: "{it.get("title","")}" | source: {it.get("source","")} '
            f'| target_category: {it.get("category","general")}'
            + (f'\n   snippet: "{snip}"' if snip else "")
        )
    payload = "Candidate items:\n" + "\n".join(lines)

    try:
        verdicts = _extract_json(_chat(SLOW_PROMPT, payload))
        if not isinstance(verdicts, list):
            raise ValueError("expected a JSON array")
    except Exception as exc:
        # Fail-soft: only pass wire/major newsrooms (rel >= 8). Never dump junk.
        wire = [
            it for it in items
            if pipeline.source_reliability(it.get("source"), it.get("url")) >= 8
        ]
        log.error(
            "slow-lane filter failed (%s) — keep-wire-only %d of %d",
            exc, len(wire), len(items),
        )
        return wire

    by_id = {i: it for i, it in enumerate(items, start=1)}
    kept: list[dict[str, Any]] = []
    canonical_seen: set[int] = set()

    for v in verdicts:
        if not isinstance(v, dict):
            continue
        try:
            vid = int(v.get("id"))
        except (TypeError, ValueError):
            continue
        item = by_id.get(vid)
        if item is None or not v.get("keep"):
            continue
        dup_of = v.get("duplicate_of")
        if dup_of is not None:
            try:
                if int(dup_of) != vid:
                    continue  # a non-canonical duplicate — drop
            except (TypeError, ValueError):
                pass
        if vid in canonical_seen:
            continue
        canonical_seen.add(vid)
        cat = v.get("category")
        if isinstance(cat, str) and cat in config.CHANNEL_MAP:
            item["category"] = cat
        item["importance"] = int(v.get("importance") or 2)
        item["explain"] = clean_explain(v.get("explain"))
        item["summary_ai"] = pipeline.clean_text(v.get("summary_ai") or "")
        item["why_matters"] = pipeline.clean_text(v.get("why_matters") or "")
        item["watch_next"] = pipeline.clean_text(v.get("watch_next") or "")
        try:
            item["significance"] = max(1, min(10, int(v.get("significance") or 5)))
        except (TypeError, ValueError):
            item["significance"] = 5
        if item["significance"] < config.POST_MIN_SIGNIFICANCE:
            continue
        kept.append(item)

    n_expl = sum(1 for i in kept if i.get("explain"))
    n_card = sum(1 for i in kept if i.get("summary_ai") or i.get("explain"))

    # The model sometimes omits the "explain"/"summary_ai" fields. Retry once
    # with a reminder — but only when the CARD BODY is missing (summary_ai or
    # explain). If summary_ai is already there we don't need the fallback, so
    # no point spending another call.
    if kept and n_card == 0 and _retry_explain:
        log.warning("batch returned no explainers — retrying once with a reminder")
        try:
            reminder = payload + (
                "\n\nIMPORTANT: your previous response omitted the required "
                '"explain" field. Return the same JSON array again and include a '
                '4-8 line "explain" string for EVERY item where keep is true.'
            )
            again = _extract_json(_chat(SLOW_PROMPT, reminder))
            if isinstance(again, list):
                by_url = {i.get("url"): i for i in kept}
                idx_to_item = {i: it for i, it in enumerate(items, start=1)}
                filled = 0
                for v in again:
                    if not isinstance(v, dict):
                        continue
                    try:
                        vid = int(v.get("id"))
                    except (TypeError, ValueError):
                        continue
                    src = idx_to_item.get(vid)
                    if src is None:
                        continue
                    target = by_url.get(src.get("url"))
                    if target is None or target.get("explain"):
                        continue
                    ex = clean_explain(v.get("explain"))
                    if ex:
                        target["explain"] = ex
                        filled += 1
                n_expl = sum(1 for i in kept if i.get("explain"))
                log.info("explainer retry filled %d item(s)", filled)
        except Exception as exc:
            log.warning("explainer retry failed (%s) — posting without explainers", exc)

    log.info("slow-lane filter: %d in -> %d kept (%d with explainer)", len(items), len(kept), n_expl)
    return kept


# --------------------------------------------------------------- fast lane
def filter_item(item: dict[str, Any]) -> dict[str, Any]:
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
    try:
        v = _extract_json(_chat(FAST_PROMPT, payload))
        if not isinstance(v, dict):
            raise ValueError("expected a JSON object")
        decision = str(v.get("decision", "review")).lower().strip()
        if decision not in {"keep", "reject", "review"}:
            decision = "review"
        cat = v.get("category")
        try:
            sig = max(1, min(10, int(v.get("significance") or 5)))
        except (TypeError, ValueError):
            sig = 5
        if decision == "keep" and sig < config.POST_MIN_SIGNIFICANCE:
            decision = "reject"
            v["reason"] = (
                str(v.get("reason") or "")
                + f" | below significance floor {config.POST_MIN_SIGNIFICANCE}"
            ).strip(" |")
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
