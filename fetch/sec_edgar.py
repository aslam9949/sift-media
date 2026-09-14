"""SEC EDGAR fetcher — official, free, keyless. Needs a User-Agent header.

Covers US: 8-K (earnings/material events) and S-1 (IPO filings).
Self-throttled well under the published 10 req/sec limit.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any
from xml.etree import ElementTree as ET

import requests

import config
import db

log = logging.getLogger("fetch.sec")

ATOM = "{http://www.w3.org/2005/Atom}"
BASE = "https://www.sec.gov/cgi-bin/browse-edgar"

FORM_TYPES = [
    ("8-K", "us_stock_market", "earnings"),
    ("S-1", "us_market_ipo", "ipo"),
    ("424B4", "us_market_ipo", "ipo"),
]


def _headers() -> dict[str, str]:
    return {"User-Agent": config.SEC_EDGAR_UA, "Accept-Encoding": "gzip, deflate", "Host": "www.sec.gov"}


def fetch_form(form_type: str, category: str, event_type: str, count: int = 40) -> list[dict[str, Any]]:
    name = f"sec_{form_type.lower()}"
    db.register_fetcher(name, config.FAST_INTERVAL_MIN * 4, "fast")
    items: list[dict[str, Any]] = []
    try:
        resp = requests.get(
            BASE,
            params={"action": "getcurrent", "type": form_type, "company": "", "dateb": "",
                    "owner": "include", "count": count, "output": "atom"},
            headers=_headers(),
            timeout=30,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        for entry in root.findall(f"{ATOM}entry"):
            title = (entry.findtext(f"{ATOM}title") or "").strip()
            link_el = entry.find(f"{ATOM}link")
            link = (link_el.get("href") if link_el is not None else "") or ""
            updated = (entry.findtext(f"{ATOM}updated") or "").strip()
            summary = (entry.findtext(f"{ATOM}summary") or "").strip()
            if not title or not link:
                continue
            company = title.split(" - ", 1)[-1].split("(")[0].strip() if " - " in title else title
            items.append(
                {
                    "title": f"{form_type} filing: {title}",
                    "url": link,
                    "summary": summary[:300],
                    "source": "SEC EDGAR",
                    "category": category,
                    "event_type": event_type,
                    "company": company,
                    "date": updated or datetime.now(timezone.utc).isoformat(),
                    "published_at": updated or datetime.now(timezone.utc).isoformat(),
                    "fetcher": name,
                    "event_key": f"sec:{form_type}:{link}",
                }
            )
        db.record_run(name, ok=True, items=len(items))
        log.info("%s: %d filings", name, len(items))
    except Exception as exc:
        db.record_run(name, ok=False, error=str(exc))
        log.warning("%s FAILED: %s", name, exc)
    return items


def fetch_all() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for form, cat, ev in FORM_TYPES:
        out.extend(fetch_form(form, cat, ev))
        time.sleep(0.4)   # well under 10 req/sec
    return out
