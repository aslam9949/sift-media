"""BSE India fetcher — keyless. Cross-check for announcements & IPO.

BSE's api.bseindia.com needs a browser-ish Referer/Origin but no key. It is
NOT well behaved: the same request may return a JSON object, a JSON string
containing JSON (double-encoded), or the bare text "No Record Found!".
Weekends/holidays legitimately return nothing. All of that is handled here and
treated as a successful-but-empty run, so the health monitor doesn't cry wolf.

Secondary source only — dedup against NSE happens downstream via event_key
plus the fuzzy title prefilter.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

import db

log = logging.getLogger("fetch.bse")

API = "https://api.bseindia.com/BseIndiaAPI/api"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.bseindia.com/corporates/ann.html",
    "Origin": "https://www.bseindia.com",
}
EMPTY_MARKERS = ("no record found", "no records found")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rows(payload: Any) -> list[dict[str, Any]]:
    """Unwrap BSE's inconsistent shapes into a list of dict rows."""
    for _ in range(3):  # peel double/triple-encoded JSON strings
        if isinstance(payload, str):
            text = payload.strip()
            if not text or any(m in text.lower() for m in EMPTY_MARKERS):
                return []
            if not text.startswith(("{", "[")):
                return []
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                return []
        else:
            break
    if isinstance(payload, dict):
        for key in ("Table", "Table1", "data", "d"):
            val = payload.get(key)
            if isinstance(val, list):
                return [r for r in val if isinstance(r, dict)]
            if isinstance(val, str):
                return _rows(val)
        return []
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    return []


def _get(path: str, params: dict[str, Any], attempts: int = 3) -> list[dict[str, Any]]:
    """GET with retries — BSE intermittently returns empty for valid requests."""
    last: Exception | None = None
    for i in range(attempts):
        try:
            r = requests.get(f"{API}/{path}", params=params, headers=HEADERS, timeout=30)
            r.raise_for_status()
            try:
                payload: Any = r.json()
            except ValueError:
                payload = r.text
            rows = _rows(payload)
            if rows:
                return rows
            last = RuntimeError("empty response")
        except Exception as exc:
            last = exc
        time.sleep(1.5 * (i + 1))
    log.debug("bse %s: no rows (%s)", path, last)
    return []


def announcements(lookback_days: int = 4) -> list[dict[str, Any]]:
    """Corporate announcements — cross-check against NSE."""
    name = "bse_announcements"
    db.register_fetcher(name, 30, "fast")
    try:
        today = datetime.now(timezone.utc)
        prev = today - timedelta(days=lookback_days)
        rows = _get(
            "AnnGetData/w",
            {
                "strCat": "-1",
                "strPrevDate": prev.strftime("%Y%m%d"),
                "strToDate": today.strftime("%Y%m%d"),
                "strScrip": "",
                "strSearch": "P",
                "strType": "C",
                "pageno": 1,
            },
        )
        if not rows:  # older API shape as a fallback
            rows = _get(
                "AnnSubCategoryGetData/w",
                {
                    "pageno": 1, "strCat": "-1",
                    "strPrevDate": prev.strftime("%Y%m%d"),
                    "strToDate": today.strftime("%Y%m%d"),
                    "strScrip": "", "strSearch": "P", "strType": "C", "subcategory": "-1",
                },
            )

        out: list[dict[str, Any]] = []
        for row in rows[:60]:
            sym = str(row.get("SLONGNAME") or row.get("SCRIP_CD") or "").strip()
            head = str(row.get("NEWSSUB") or row.get("HEADLINE") or row.get("MORE") or "").strip()
            if not sym or not head:
                continue
            attach = str(row.get("ATTACHMENTNAME") or "").strip()
            url = (f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{attach}"
                   if attach else "https://www.bseindia.com/corporates/ann.html")
            news_id = row.get("NEWSID") or row.get("NEWS_DT") or head[:60]
            out.append({
                "title": f"{sym}: {head[:180]}",
                "url": url,
                "summary": str(row.get("MORE") or head)[:300],
                "source": "BSE",
                "category": "india_market_ipo",
                "event_type": "earnings",
                "company": sym,
                "date": row.get("NEWS_DT") or _now(),
                "published_at": _now(),
                "fetcher": name,
                "event_key": f"bse:ann:{news_id}",
            })

        # Empty is a legitimate outcome on weekends/holidays — still a success.
        db.record_run(name, ok=True, items=len(out))
        log.info("%s: %d items", name, len(out))
        return out
    except Exception as exc:
        db.record_run(name, ok=False, error=str(exc))
        log.warning("%s FAILED: %s", name, exc)
        return []


def fetch_all() -> list[dict[str, Any]]:
    return announcements()
