"""RSS + Google News RSS fetchers. Keyless. Records last-run per constraint 7."""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

import feedparser

import db
import feeds

log = logging.getLogger("fetch.rss")

# Some publishers (business-standard confirmed) serve MALFORMED XML to custom
# User-Agent strings but valid XML to a browser UA. So use a plain browser UA as
# the primary, and fall back to the identifying one only if that fails.
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
USER_AGENT_ALT = "Mozilla/5.0 (compatible; AslamNewsMedia/1.0; +https://t.me/Aslamnewsmedia_bot)"


def _published(entry: Any) -> str:
    """Real publish time, or "" when the feed didn't give one.

    Deliberately does NOT fall back to now() — a fabricated timestamp would be
    rendered to users as fact. Callers show "recent" when this is empty.
    """
    for key in ("published_parsed", "updated_parsed"):
        val = getattr(entry, key, None) or entry.get(key) if hasattr(entry, "get") else None
        if val:
            try:
                return datetime.fromtimestamp(time.mktime(val), tz=timezone.utc).isoformat()
            except Exception:
                pass
    return ""


def fetch_one(name: str, url: str, category: str, expected_min: int = 30,
              attempts: int = 2) -> list[dict[str, Any]]:
    """Fetch a single feed. Always records a run outcome; never raises.

    Some publishers (e.g. business-standard) intermittently serve malformed XML
    — a plain retry usually gets a clean copy, so don't mark it failed on the
    first bad parse. feedparser is also lenient: if it recovered entries despite
    a bozo flag, we use them rather than throwing the batch away.
    """
    db.register_fetcher(name, expected_min, "slow")
    last_err: Exception | None = None

    for attempt in range(attempts):
        try:
            # Alternate the UA between tries — a malformed-XML response is often
            # UA-specific rather than a genuine publisher outage.
            agent = USER_AGENT if attempt % 2 == 0 else USER_AGENT_ALT
            parsed = feedparser.parse(
                url, agent=agent, request_headers={"Cache-Control": "no-cache"}
            )
            entries = getattr(parsed, "entries", []) or []
            if not entries:
                raise RuntimeError(getattr(parsed, "bozo_exception", None) or "no entries returned")

            items: list[dict[str, Any]] = []
            for e in entries[:40]:
                link = e.get("link") or ""
                title = e.get("title") or ""
                if not link or not title:
                    continue
                summary = e.get("summary") or e.get("description") or ""
                src = (e.get("source", {}) or {})
                src_title = src.get("title") or name
                # NOTE: source.href only carries the publisher's HOMEPAGE (e.g.
                # reuters.com), not a deep article link, so we keep the
                # news.google.com blob as the clickable URL — it resolves to the
                # actual article. We still use src_title for the label so the
                # attribution is the real outlet (Reuters, not "AOL.com").
                items.append(
                    {
                        "title": title,
                        "url": link,
                        "summary": summary,
                        "category": category,
                        "source": src_title,
                        "published_at": _published(e),
                        "fetcher": name,
                    }
                )

            db.record_run(name, ok=True, items=len(items))
            if getattr(parsed, "bozo", 0) and items:
                log.info("%s: %d items (recovered from malformed XML)", name, len(items))
            else:
                log.info("%s: %d items", name, len(items))
            return items

        except Exception as exc:
            last_err = exc
            if attempt < attempts - 1:
                log.debug("%s parse failed (%s) — retrying", name, exc)
                time.sleep(2)

    db.record_run(name, ok=False, error=str(last_err))
    log.warning("%s FAILED after %d tries: %s", name, attempts, last_err)
    return []


def fetch_all_rss(max_workers: int = 6) -> list[dict[str, Any]]:
    jobs = list(feeds.RSS_FEEDS) + [
        (name, feeds.google_news_url(query), cat) for name, query, cat in feeds.GOOGLE_NEWS_QUERIES
    ]
    out: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(fetch_one, n, u, c) for n, u, c in jobs]
        for f in futures:
            try:
                out.extend(f.result(timeout=90))
            except Exception as exc:
                log.error("feed worker error: %s", exc)
    log.info("rss total: %d raw items from %d feeds", len(out), len(jobs))
    return out
