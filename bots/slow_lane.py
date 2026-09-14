"""Bot #1 — Slow Lane (general news).

scheduler → fetcher → normalizer → rule prefilter (URL dedup + rapidfuzz)
→ BATCHED smart filter → router → send queue → Telegram
"""
from __future__ import annotations

import logging
from typing import Any

import config
import db
import pipeline
import smart_filter
from fetch import rss
from send_queue import get_queue

log = logging.getLogger("slow_lane")


def _batches(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


async def run_cycle() -> dict[str, int]:
    """One full slow-lane pass. Returns counters for logging/tests."""
    stats = {"raw": 0, "normalized": 0, "prefiltered": 0, "kept": 0, "queued": 0}

    raw = rss.fetch_all_rss()
    stats["raw"] = len(raw)

    normalized = [n for n in (pipeline.normalize(r) for r in raw) if n]
    stats["normalized"] = len(normalized)

    candidates = pipeline.prefilter(normalized)
    stats["prefiltered"] = len(candidates)

    # Newest first, then cap the cycle so one burst can't flood the channels.
    candidates.sort(key=lambda i: i.get("published_at") or "", reverse=True)
    candidates = candidates[: config.MAX_ITEMS_PER_CYCLE]

    kept: list[dict[str, Any]] = []
    for batch in _batches(candidates, config.SLOW_BATCH_SIZE):
        kept.extend(smart_filter.filter_batch(batch))
    stats["kept"] = len(kept)

    queue = get_queue(config.BOT1_TOKEN, "slow")
    queue.start()

    for item in kept:
        # Claim the URL BEFORE sending so a crash mid-cycle can't double-post.
        if not db.mark_seen(item, lane="slow", posted=False):
            continue
        cat = item.get("category") or "general"
        chat_id = pipeline.resolve_channel(cat)
        if db.posted_count_for_chat(chat_id) >= config.CHANNEL_MAX_PER_HOUR:
            log.info("chat cap hit (%s) — not posting: %s", cat, (item.get("title") or "")[:70])
            continue
        await queue.enqueue(chat_id, pipeline.build_message(item), priority=5)
        db.mark_posted(item["url"])
        stats["queued"] += 1

    # Record every item we rejected too, so we never re-evaluate it next cycle.
    kept_urls = {i["url"] for i in kept}
    for item in candidates:
        if item["url"] not in kept_urls:
            db.mark_seen(item, lane="slow", posted=False)

    log.info("slow cycle: %s", stats)
    return stats
