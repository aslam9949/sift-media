#!/usr/bin/env python3
"""Sift Media Live Desk — read-only newspaper over aslam_news.db."""
from __future__ import annotations

import json
import sqlite3
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
DB = Path("/root/aslam-news-media/data/aslam_news.db")
PORT = 8780
IST = timezone(timedelta(hours=5, minutes=30))

sys.path.insert(0, "/root/aslam-news-media")
from pipeline import source_reliability  # noqa: E402

NAV = [
    ("front", "FRONT", None),
    ("daily", "DAILY", "daily_news"),
    ("world", "WORLD", "geopolitics"),
    ("gold", "GOLD", "gold_news"),
    ("india_calendar", "INDIA CALENDAR", "india_econ_calendar"),
    ("india_markets", "INDIA MARKETS", "india_market_ipo"),
    ("india_politics", "INDIA POLITICS", "india_politics"),
    ("us_calendar", "US CALENDAR", "us_econ_calendar"),
    ("us_politics", "US POLITICS", "us_politics"),
    ("us_markets", "US MARKETS", "us_stock_market"),
]
CAT_LABEL = {k: lab for k, lab, _ in NAV}
DB_TO_NAV = {db: k for k, _, db in NAV if db}

_lock = threading.Lock()
_cache = {"at": 0.0, "payload": None}


def _ist(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(IST)
    except Exception:
        return None


def _ago(iso: str | None) -> str:
    dt = _ist(iso)
    if not dt:
        return ""
    sec = int((datetime.now(IST) - dt).total_seconds())
    if sec < 60:
        return "just now"
    if sec < 3600:
        return f"{sec // 60}M AGO"
    if sec < 86400:
        h = sec // 3600
        return f"{h}H AGO"
    d = sec // 86400
    return f"{d}D AGO"


def _fmt_ist(iso: str | None) -> str:
    dt = _ist(iso)
    if not dt:
        return ""
    return dt.strftime("%d %b %Y, %H:%M IST").upper().replace(" 0", " ")


def _card(row: sqlite3.Row) -> dict:
    src = row["source"] or ""
    url = row["url"] or ""
    rel = source_reliability(src, url)
    cat = row["category"] or ""
    nav = DB_TO_NAV.get(cat, cat)
    return {
        "title": row["title"] or "",
        "url": url,
        "source": src,
        "category": cat,
        "nav": nav,
        "nav_label": CAT_LABEL.get(nav, (cat or "").replace("_", " ").upper()),
        "rel": rel,
        "significance": row["significance"],
        "posted_at": row["posted_at"],
        "when": _fmt_ist(row["posted_at"]),
        "ago": _ago(row["posted_at"]),
        "summary": row["summary_ai"] or "",
    }


def _open() -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=8)
    con.row_factory = sqlite3.Row
    return con


def build_state() -> dict:
    con = _open()
    try:
        total = con.execute("SELECT COUNT(*) FROM seen_urls WHERE posted_at IS NOT NULL").fetchone()[0]
        rows = con.execute(
            """SELECT title, url, source, category, significance, posted_at, summary_ai
               FROM seen_urls WHERE posted_at IS NOT NULL
               ORDER BY posted_at DESC LIMIT 220"""
        ).fetchall()
        cards = [_card(r) for r in rows]
        cal = [
            {
                "date": r["date"],
                "name": r["event_name"],
                "country": r["country"],
                "time": r["time_label"] or "",
                "importance": r["importance"],
            }
            for r in con.execute(
                """SELECT date, event_name, country, time_label, importance
                   FROM calendar_events WHERE date >= date('now','-1 day')
                   ORDER BY date, time_label LIMIT 16"""
            )
        ]
        mkt = [
            {
                "title": r["title"] or r["company"] or "",
                "source": r["source"] or "",
                "type": r["event_type"] or "",
                "when": _fmt_ist(r["posted_at"]),
                "ago": _ago(r["posted_at"]),
            }
            for r in con.execute(
                """SELECT title, company, source, event_type, posted_at
                   FROM market_events WHERE posted_at IS NOT NULL
                   ORDER BY posted_at DESC LIMIT 12"""
            )
        ]
    finally:
        con.close()

    now = datetime.now(IST)
    nxt = next((x for x in cal if x["date"] >= now.strftime("%Y-%m-%d")), cal[0] if cal else None)
    return {
        "updated": now.strftime("%a, %-d %b, %-I:%M %p IST"),
        "clock": now.strftime("%a, %-d %b, %-I:%M %p IST"),
        "next_print": nxt,
        "count": total,
        "cards": cards,
        "calendar": cal,
        "markets": mkt,
        "nav": [{"id": k, "label": lab} for k, lab, _ in NAV],
    }


def state() -> dict:
    import time

    now = time.time()
    with _lock:
        if _cache["payload"] and now - _cache["at"] < 45:
            return _cache["payload"]
        payload = build_state()
        _cache["at"] = now
        _cache["payload"] = payload
        return payload


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/state":
            body = json.dumps(state()).encode()
            return self._send(200, body, "application/json; charset=utf-8")
        mapping = {
            "/": HERE / "index.html",
            "/index.html": HERE / "index.html",
            "/style.css": HERE / "style.css",
            "/app.js": HERE / "app.js",
        }
        fp = mapping.get(path)
        if not fp or not fp.exists():
            return self._send(404, b"not found", "text/plain")
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
        }[fp.suffix]
        return self._send(200, fp.read_bytes(), ctype)


def main():
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Sift Media desk http://0.0.0.0:{PORT}/", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
