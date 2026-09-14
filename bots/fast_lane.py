"""Bot #2 — Fast Lane (markets/alerts).

scheduler → fetcher → PER-ITEM 3-way smart filter → router → send queue
  keep   → public category channel
  review → private admin channel (never guessed publicly)
  reject → dropped silently (still recorded, so we don't re-judge it)

Runs on BOT2_TOKEN when present; falls back to BOT1_TOKEN in single-bot mode.

director_change, analyst_meet, and record_date are no longer blind-rejected.
They go through the LLM in STRICT_MODE — only genuinely market-moving events survive.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import config
import db
import pipeline
import smart_filter
from fetch import bse, nse, sec_edgar
from send_queue import get_queue

log = logging.getLogger("fast_lane")

PRIORITY = {
    "bulk_deal": 0, "block_deal": 0, "ipo": 1, "earnings": 2,
    "corp_action": 3, "fii_dii": 3, "short_selling": 4,
    "shareholding": 5, "circular": 6,
}


def collect() -> list[dict[str, Any]]:
    """Gather from every market source. One source failing never stops the rest."""
    raw: list[dict[str, Any]] = []
    for name, fn in (("sec", sec_edgar.fetch_all), ("nse", nse.fetch_all), ("bse", bse.fetch_all)):
        try:
            raw.extend(fn())
        except Exception as exc:
            log.error("%s collector crashed: %s", name, exc)
    return raw


def _collapse_deals(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """One bulk/block card per symbol per cycle — keep the largest notional."""
    best: dict[str, dict[str, Any]] = {}
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for it in items:
        et = (it.get("event_type") or "").lower()
        if et not in ("bulk_deal", "block_deal"):
            kept.append(it)
            continue
        co = (it.get("company") or "").strip().upper()
        if not co:
            kept.append(it)
            continue
        prev = best.get(co)
        try:
            n = float(it.get("notional") or 0)
        except (TypeError, ValueError):
            n = 0.0
        try:
            pn = float(prev.get("notional") or 0) if prev else -1
        except (TypeError, ValueError):
            pn = -1
        if prev is None or n > pn:
            if prev:
                dropped.append(prev)
            best[co] = it
        else:
            dropped.append(it)
    kept.extend(best.values())
    return kept, dropped


async def run_cycle() -> dict[str, int]:
    stats = {"raw": 0, "new": 0, "keep": 0, "review": 0, "reject": 0}

    raw = collect()
    stats["raw"] = len(raw)

    # Dedup on event_key (source-native identity) before spending any LLM calls.
    fresh: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for r in raw:
        key = r.get("event_key") or r.get("url", "")
        if not key or key in seen_keys or db.market_event_seen(key):
            continue
        seen_keys.add(key)
        item = pipeline.normalize(r, default_category="india_market_ipo")
        if not item:
            continue
        item["event_key"] = key
        item["event_type"] = r.get("event_type")
        item["company"] = r.get("company")
        item["date"] = r.get("date")
        item["notional"] = r.get("notional")
        item["qty"] = r.get("qty")
        item["price"] = r.get("price")
        fresh.append(item)

    fresh, collapsed = _collapse_deals(fresh)
    for item in collapsed:
        db.save_market_event(
            {**item, "event_key": item["event_key"]},
            decision="reject",
            importance=1,
        )
        stats["reject"] += 1
        log.info("deal collapse drop %s | %s", item.get("company"), (item.get("title") or "")[:70])

    fresh.sort(key=lambda i: PRIORITY.get(i.get("event_type") or "", 9))
    fresh = fresh[: config.FAST_MAX_ITEMS_PER_CYCLE]
    stats["new"] = len(fresh)

    queue = get_queue(config.FAST_LANE_TOKEN, "fast")
    queue.start()
    admin_chat = config.route_channel("admin_health")

    # Gate 2: rule-based routing.
    # needs_llm items (director_change, analyst_meet, record_date) now pass
    # through to the LLM instead of being blind-rejected.
    need_llm: list[dict[str, Any]] = []
    ruled: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for item in fresh:
        ruling = pipeline.rule_fast_decision(item)
        if ruling == "reject":
            ruled.append((item, {
                "decision": "reject",
                "category": item.get("category", "general"),
                "importance": 1,
                "significance": 1,
                "reason": "rule: routine filing",
            }))
        elif ruling == "needs_llm":
            # Pass through to LLM — STRICT_MODE flag is appended inside filter_item
            need_llm.append(item)
        else:
            need_llm.append(item)

    # Per-item LLM filtering — now async via OpenRouter
    verdicts: list[dict[str, Any]] = []
    if need_llm:
        coros = [smart_filter.filter_item(item) for item in need_llm]
        verdicts = await asyncio.gather(*coros)

    judged = list(zip(need_llm, verdicts)) + ruled

    for item, verdict in judged:
        decision = verdict["decision"]
        cat = verdict.get("category") or item.get("category") or "general"
        item["category"] = cat

        db.save_market_event(
            {**item, "event_key": item["event_key"]},
            decision=decision,
            importance=verdict.get("importance", 0),
        )

        if decision == "keep":
            chat = pipeline.resolve_channel(cat)
            if db.posted_count_for_chat(chat) >= config.CHANNEL_MAX_PER_HOUR:
                log.info("chat cap hit (%s) — not posting: %s", cat, (item.get("title") or "")[:70])
                stats["reject"] += 1
                continue
            await queue.enqueue(chat, pipeline.build_market_message(item, verdict), priority=1)
            db.mark_market_posted(item["event_key"])
            stats["keep"] += 1
        elif decision == "review":
            if db.review_count() > config.REVIEW_MAX_PER_HOUR:
                log.info("review cap hit — dropping: %s", (item.get("title") or "")[:70])
                stats["reject"] += 1
                continue
            await queue.enqueue(admin_chat, pipeline.build_review_message(item, verdict), priority=2)
            stats["review"] += 1
        else:
            stats["reject"] += 1

    log.info("fast cycle: %s", stats)
    return stats