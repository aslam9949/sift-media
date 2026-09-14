"""NSE India fetcher — keyless. Session cookie handled internally (not an API key).

All 7 feeds from the TRD:
  1. bulk/block deals          5. shareholding pattern
  2. corporate announcements   6. FII/DII daily flows
  3. IPO (mainboard + SME)     7. circulars
  4. corporate actions

Uses a direct requests.Session with NSE cookie bootstrap — same technique
nsepython uses, but without depending on its (frequently stale) endpoints.
Self-throttled; every feed records its own last-run timestamp.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

import requests

import config
import db

log = logging.getLogger("fetch.nse")

HOME = "https://www.nseindia.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"{HOME}/market-data/live-equity-market",
    "Connection": "keep-alive",
}

_session: requests.Session | None = None
_session_at = 0.0
SESSION_TTL = 240  # refresh cookies every 4 min


def _get_session(force: bool = False) -> requests.Session:
    """Bootstrap/refresh the NSE cookie jar."""
    global _session, _session_at
    if _session is None or force or (time.time() - _session_at) > SESSION_TTL:
        s = requests.Session()
        s.headers.update(HEADERS)
        for url in (HOME, f"{HOME}/market-data/securities-available-for-trading"):
            try:
                s.get(url, timeout=20)
            except Exception as exc:
                log.debug("cookie bootstrap %s: %s", url, exc)
        _session, _session_at = s, time.time()
    return _session


def _api(path: str, retries: int = 2) -> Any:
    """GET an NSE JSON endpoint, refreshing cookies on failure."""
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            s = _get_session(force=attempt > 0)
            r = s.get(f"{HOME}/api/{path}", timeout=30)
            if r.status_code in (401, 403):
                raise RuntimeError(f"HTTP {r.status_code} (cookie stale)")
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"NSE {path}: {last}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _num(x: Any) -> float | None:
    if x is None or x == "":
        return None
    try:
        return float(str(x).replace(",", "").replace("₹", "").strip())
    except (TypeError, ValueError):
        return None


def _wrap(name: str, expected_min: int, builder) -> list[dict[str, Any]]:
    """Run one feed builder, record health, never raise."""
    db.register_fetcher(name, expected_min, "fast")
    try:
        items = builder()
        db.record_run(name, ok=True, items=len(items))
        log.info("%s: %d items", name, len(items))
        return items
    except Exception as exc:
        db.record_run(name, ok=False, error=str(exc))
        log.warning("%s FAILED: %s", name, exc)
        return []


# ------------------------------------------------------- 1. bulk/block deals
def bulk_block_deals() -> list[dict[str, Any]]:
    def build() -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for kind, path in (("bulk_deal", "snapshot-capital-market-largedeal"),):
            data = _api(path)
            for key, label in (("BULK_DEALS_DATA", "bulk_deal"), ("BLOCK_DEALS_DATA", "block_deal"),
                               ("SHORT_DEALS_DATA", "short_selling")):
                for row in (data.get(key) or []):
                    sym = row.get("symbol") or row.get("BD_SYMBOL") or ""
                    client = row.get("name") or row.get("BD_CLIENT_NAME") or ""
                    qty = row.get("qty") or row.get("BD_QTY_TRD") or ""
                    buysell = row.get("buySell") or row.get("BD_BUY_SELL") or ""
                    price = row.get("watp") or row.get("BD_TP_WATP") or ""
                    if not sym:
                        continue
                    qty_n = _num(qty)
                    price_n = _num(price)
                    notional = (qty_n * price_n) if qty_n and price_n else None
                    title = f"{sym}: {label.replace('_',' ').title()} — {client} {buysell} {qty} @ ₹{price}".strip()
                    out.append({
                        "title": title, "url": f"{HOME}/report-detail/display-bulk-and-block-deals",
                        "summary": f"{client} {buysell} {qty} shares of {sym} at ₹{price}",
                        "source": "NSE", "category": "india_market_ipo", "event_type": label,
                        "company": sym, "date": row.get("date") or _now(), "published_at": _now(),
                        "fetcher": "nse_deals",
                        "qty": qty_n, "price": price_n, "notional": notional,
                        "event_key": f"nse:{label}:{sym}:{client}:{qty}:{row.get('date','')}",
                    })
        return out
    return _wrap("nse_deals", 30, build)


# ------------------------------------------- 2. corporate announcements
def announcements() -> list[dict[str, Any]]:
    def build() -> list[dict[str, Any]]:
        data = _api("corporate-announcements?index=equities")
        rows = data if isinstance(data, list) else (data.get("data") or [])
        out = []
        for row in rows[:60]:
            sym = row.get("symbol") or ""
            subject = (row.get("desc") or row.get("sm_name") or row.get("attchmntText") or "").strip()
            if not sym or not subject:
                continue
            out.append({
                "title": f"{sym}: {subject[:180]}",
                "url": row.get("attchmntFile") or f"{HOME}/companies-listing/corporate-filings-announcements",
                "summary": (row.get("attchmntText") or subject)[:300],
                "source": "NSE", "category": "india_market_ipo", "event_type": "earnings",
                "company": sym, "date": row.get("an_dt") or _now(), "published_at": _now(),
                "fetcher": "nse_announcements",
                "event_key": f"nse:ann:{sym}:{row.get('an_dt','')}:{subject[:60]}",
            })
        return out
    return _wrap("nse_announcements", 30, build)


# --------------------------------------------- 3. IPO mainboard + SME
def ipos() -> list[dict[str, Any]]:
    def build() -> list[dict[str, Any]]:
        out = []
        for path, board in (("ipo-current-issue", "mainboard"), ("ipo-current-issue?market=sme", "sme")):
            try:
                rows = _api(path)
            except Exception as exc:
                log.debug("ipo %s: %s", board, exc)
                continue
            rows = rows if isinstance(rows, list) else (rows.get("data") or [])
            for row in rows:
                sym = row.get("symbol") or row.get("companyName") or ""
                if not sym:
                    continue
                issue = f"{row.get('issueStartDate','')} → {row.get('issueEndDate','')}"
                price = row.get("issuePrice") or row.get("priceBand") or ""
                out.append({
                    "title": f"IPO ({board}): {row.get('companyName') or sym} — band {price}, {issue}",
                    "url": f"{HOME}/market-data/all-upcoming-issues-ipo",
                    "summary": f"Series: {row.get('series','')} | Issue size: {row.get('issueSize','')} | {issue}",
                    "source": "NSE", "category": "india_market_ipo", "event_type": "ipo",
                    "company": sym, "date": row.get("issueStartDate") or _now(), "published_at": _now(),
                    "fetcher": "nse_ipo",
                    "event_key": f"nse:ipo:{board}:{sym}:{row.get('issueStartDate','')}",
                })
        return out
    return _wrap("nse_ipo", 120, build)


# --------------------------------------------------- 4. corporate actions
def corporate_actions() -> list[dict[str, Any]]:
    def build() -> list[dict[str, Any]]:
        data = _api("corporates-corporateActions?index=equities")
        rows = data if isinstance(data, list) else (data.get("data") or [])
        out = []
        for row in rows[:60]:
            sym = row.get("symbol") or ""
            purpose = (row.get("subject") or row.get("purpose") or "").strip()
            if not sym or not purpose:
                continue
            out.append({
                "title": f"{sym}: corporate action — {purpose[:150]}",
                "url": f"{HOME}/companies-listing/corporate-filings-actions",
                "summary": f"Ex-date: {row.get('exDate','')} | Record: {row.get('recDate','')} | {purpose}"[:300],
                "source": "NSE", "category": "india_market_ipo", "event_type": "corp_action",
                "company": sym, "date": row.get("exDate") or _now(), "published_at": _now(),
                "fetcher": "nse_corp_actions",
                "event_key": f"nse:ca:{sym}:{row.get('exDate','')}:{purpose[:50]}",
            })
        return out
    return _wrap("nse_corp_actions", 240, build)


# ------------------------------------------------ 5. shareholding pattern
# NSE retired the bulk shareholding-pattern JSON endpoint (all known paths 404
# as of Aug 2026); it is now only queryable per-symbol behind the UI. We track
# it via SAST/insider-style filings surfaced in announcements instead, and use
# the quarterly financial-results feed below as the substantive earnings source.
SHP_CANDIDATES = [
    "corporate-share-holdings-pattern?index=equities",
    "corporates-shp?index=equities",
    "corporate-shp-promoter?index=equities",
]


def shareholding() -> list[dict[str, Any]]:
    def build() -> list[dict[str, Any]]:
        last: Exception | None = None
        for path in SHP_CANDIDATES:
            try:
                data = _api(path, retries=0)
            except Exception as exc:
                last = exc
                continue
            rows = data if isinstance(data, list) else (data.get("data") or [])
            out = []
            for row in rows[:40]:
                sym = row.get("symbol") or row.get("name") or ""
                if not sym:
                    continue
                out.append({
                    "title": f"{sym}: shareholding pattern filed ({row.get('as_on_date') or row.get('period','')})",
                    "url": f"{HOME}/companies-listing/corporate-filings-shareholding-pattern",
                    "summary": f"Promoter: {row.get('pr_and_prgrp','')}% | Public: {row.get('public_val','')}%"[:300],
                    "source": "NSE", "category": "india_market_ipo", "event_type": "shareholding",
                    "company": sym, "date": row.get("as_on_date") or _now(), "published_at": _now(),
                    "fetcher": "nse_shareholding",
                    "event_key": f"nse:shp:{sym}:{row.get('as_on_date','')}",
                })
            return out
        raise RuntimeError(f"no working shareholding endpoint ({last})")

    return _wrap("nse_shareholding", 720, build)


# ------------------------------------- 5b. quarterly financial results (earnings)
def financial_results() -> list[dict[str, Any]]:
    def build() -> list[dict[str, Any]]:
        rows = _api("corporates-financial-results?index=equities&period=Quarterly")
        rows = rows if isinstance(rows, list) else (rows.get("data") or [])

        def when(row: dict[str, Any]) -> datetime:
            """Parse NSE's '30-Jul-2026 17:17:53' — string sort is wrong here."""
            raw = str(row.get("broadCastDate") or row.get("filingDate") or "").strip()
            for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y"):
                try:
                    return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
            return datetime.min.replace(tzinfo=timezone.utc)

        rows = sorted(rows, key=when, reverse=True)
        out = []
        for row in rows[:50]:
            sym = row.get("symbol") or row.get("companyName") or ""
            if not sym:
                continue
            period = f"{row.get('fromDate','')}→{row.get('toDate','')}".strip("→")
            filed = row.get("broadCastDate") or row.get("filingDate") or ""
            out.append({
                "title": f"{sym}: quarterly results filed for {period or row.get('financialYear','')}",
                "url": row.get("xbrl") or row.get("naVal") or f"{HOME}/companies-listing/corporate-filings-financial-results",
                "summary": (f"{row.get('companyName','')} | "
                            f"{'consolidated' if row.get('consolidated')=='Consolidated' else 'standalone'}"
                            f" | audited={row.get('audited','')} | broadcast {filed}")[:300],
                "source": "NSE", "category": "india_market_ipo", "event_type": "earnings",
                "company": sym, "date": filed or _now(), "published_at": _now(),
                "fetcher": "nse_results",
                "event_key": f"nse:results:{sym}:{row.get('fromDate','')}:{row.get('toDate','')}:{row.get('consolidated','')}",
            })
        return out

    return _wrap("nse_results", 180, build)


# ---------------------------------------------------- 6. FII/DII flows
def fii_dii() -> list[dict[str, Any]]:
    def build() -> list[dict[str, Any]]:
        rows = _api("fiidiiTradeReact")
        rows = rows if isinstance(rows, list) else (rows.get("data") or [])
        out = []
        for row in rows:
            cat = row.get("category") or ""
            date = row.get("date") or ""
            if not cat:
                continue
            buy, sell, net = row.get("buyValue"), row.get("sellValue"), row.get("netValue")
            out.append({
                "title": f"{cat} flows {date}: net ₹{net} cr (buy ₹{buy} cr / sell ₹{sell} cr)",
                "url": f"{HOME}/reports/fii-dii",
                "summary": f"{cat} on {date} — buy ₹{buy} cr, sell ₹{sell} cr, net ₹{net} cr",
                "source": "NSE", "category": "india_market_ipo", "event_type": "fii_dii",
                "company": cat, "date": date or _now(), "published_at": _now(),
                "fetcher": "nse_fii_dii",
                "event_key": f"nse:fiidii:{cat}:{date}",
            })
        return out
    return _wrap("nse_fii_dii", 240, build)


# ------------------------------------------------------- 7. circulars
def circulars() -> list[dict[str, Any]]:
    def build() -> list[dict[str, Any]]:
        data = _api("circulars")
        rows = (data.get("data") if isinstance(data, dict) else data) or []
        out = []
        for row in rows[:40]:
            subject = (row.get("sub_cat") or row.get("circFilename") or row.get("subject") or "").strip()
            if not subject:
                continue
            out.append({
                "title": f"NSE circular: {subject[:170]}",
                "url": row.get("circFilename") or f"{HOME}/resources/exchange-communication-circulars",
                "summary": f"Dept: {row.get('circDepartment','')} | {row.get('circular_date','')}"[:300],
                "source": "NSE", "category": "india_market_ipo", "event_type": "circular",
                "company": "NSE", "date": row.get("circular_date") or _now(), "published_at": _now(),
                "fetcher": "nse_circulars",
                "event_key": f"nse:circ:{row.get('circNumber') or subject[:60]}",
            })
        return out
    return _wrap("nse_circulars", 240, build)


FEEDS = [bulk_block_deals, announcements, ipos, corporate_actions,
         financial_results, fii_dii, circulars]

# nse_shareholding is intentionally NOT in FEEDS: NSE removed the bulk
# shareholding-pattern JSON endpoint (every known path 404s as of Aug 2026), so
# scheduling it would just spam the health monitor with a permanent failure.
# Shareholding changes still surface through corporate announcements. If NSE
# restores a bulk endpoint, add the path to SHP_CANDIDATES and re-add it here.


def fetch_all() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for fn in FEEDS:
        out.extend(fn())
        time.sleep(1.0)   # self-throttle; NSE publishes no limit
    log.info("nse total: %d items", len(out))
    return out
