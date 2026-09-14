"""Health monitor + manual economic-calendar alerts.

Health: hourly, compares each fetcher's last_success_at against its expected
schedule (grace = 3x expected interval, floor 45 min). Stale sources alert the
private admin channel, with a 6-hour cooldown per fetcher so a long outage
doesn't spam.

Elite Source Health: Rank 8+ sources that haven't yielded an item in > 2 hours
trigger an immediate Telegram alert to the admin chat, bypassing the standard
6-hour health cooldown. This catches Bloomberg/Reuters/NSE failures fast.

Calendar: daily, alerts on events exactly N days out (default 3), from the
user-maintained calendar_events table.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

import config
import db
import pipeline
from send_queue import get_queue

log = logging.getLogger("monitor")

# Sources whose feed IDs are watched for elite staleness (>2h = alert).
# Some of these have high-importance names but may appear in our fetcher
# table under different internal ids — we match by the source name.
ELITE_FETCHER_NAMES: set[str] = {
    "bbc_world", "bbc_mideast", "reuters_via_gnews", "reuters_world_gnews",
    "aljazeera", "dw_world", "france24_world", "cnbc_finance",
    "cnbc_markets", "et_markets", "et_stocks", "et_ipo",
    "ndtv_india", "toi_india", "thediplomat",
    "livemint_markets", "bs_markets", "thehindu_business",
    "nse", "bse", "sec",
}
ELITE_STALENESS_HOURS = 2


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _age_min(ts: str | None) -> float | None:
    dt = _parse(ts)
    if dt is None:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds() / 60.0


async def _check_elite_sources() -> int:
    """Immediate alert for elite (Rank 8+) sources stale > 2 hours.

    Bypasses the standard 6-hour alert cooldown — if Bloomberg/Reuters/NSE
    goes dark, we want to know within 2 hours, not 6.
    """
    fetchers = db.all_fetchers()
    alerts = 0
    stale_lines: list[str] = []

    for f in fetchers:
        name = f["fetcher_name"]
        if name not in ELITE_FETCHER_NAMES:
            continue
        age = _age_min(f.get("last_success_at"))
        if age is None:
            # Never ran — standard health check handles this, don't double-alert
            continue
        if age > (ELITE_STALENESS_HOURS * 60):
            # Already alerted within the last 2 hours for this specific source?
            # Use a tighter 2-hour cooldown for elite sources specifically.
            if db.alerted_recently(f"elite:{name}", hours=ELITE_STALENESS_HOURS):
                continue
            err = (f.get("last_error") or "").strip()
            line = f"⚡ <b>ELITE SOURCE STALE</b> • {name} — {age:.0f} min since last success"
            if err:
                line += f"\n    last error: {err[:160]}"
            stale_lines.append(line)
            db.log_health_alert(f"elite:{name}")
            alerts += 1

    if stale_lines:
        msg = "🚨 <b>ELITE SOURCE ALERT</b> — Rank 8+ feeds may be down\n\n" + "\n".join(stale_lines)
        queue = get_queue(config.FAST_LANE_TOKEN, "fast")
        queue.start()
        await queue.enqueue(config.route_channel("admin_health"), msg, priority=0)
        log.warning("elite source alert: %d stale", alerts)

    return alerts


async def check_health() -> dict[str, int]:
    """Hourly staleness sweep. Returns counters."""
    stats = {"checked": 0, "stale": 0, "alerted": 0, "never_ran": 0}
    fetchers = db.all_fetchers()
    stale_lines: list[str] = []

    for f in fetchers:
        name = f["fetcher_name"]
        stats["checked"] += 1
        expected = int(f.get("expected_min") or 30)
        grace = max(expected * 3, 45)
        age = _age_min(f.get("last_success_at"))

        if age is None:
            stats["never_ran"] += 1
            reason = "never completed a successful run"
        elif age > grace:
            reason = f"last success {age:.0f} min ago (expected every ~{expected} min)"
        else:
            continue

        stats["stale"] += 1
        if db.alerted_recently(name, hours=6):
            continue
        err = (f.get("last_error") or "").strip()
        line = f"• {name} — {reason}"
        if err:
            line += f"\n    last error: {err[:160]}"
        stale_lines.append(line)
        db.log_health_alert(name)
        stats["alerted"] += 1

    if stale_lines:
        msg = "🛠 HEALTH ALERT — stale fetchers\n\n" + "\n".join(stale_lines)
        queue = get_queue(config.FAST_LANE_TOKEN, "fast")
        queue.start()
        await queue.enqueue(config.route_channel("admin_health"), msg, priority=0)

    # Also run the elite check every health cycle
    elite_alerts = await _check_elite_sources()
    if elite_alerts:
        stats["alerted"] += elite_alerts

    log.info("health check: %s (+%d elite)", stats, elite_alerts)
    return stats


async def check_calendar(days_ahead: int | None = None) -> int:
    """Seed 30-day calendar, post weekly month view, T-3 explainer, TODAY ping."""
    from fetch import econ_calendar as cal

    days_ahead = days_ahead if days_ahead is not None else config.CALENDAR_LOOKAHEAD_DAYS
    horizon = config.CALENDAR_HORIZON_DAYS
    today = date.today()
    seeded = 0
    for ev in cal.events_for_horizon(today, horizon):
        db.upsert_calendar_event(ev)
        seeded += 1
    db.record_run("econ_calendar_seed", True, items=seeded)
    log.info("calendar: seeded %d events for next %d days", seeded, horizon)

    queue = get_queue(config.FAST_LANE_TOKEN, "fast")
    queue.start()
    sent = 0

    last_month = None
    for f in db.all_fetchers():
        if f.get("fetcher_name") == "econ_calendar_month":
            last_month = f.get("last_success_at")
            break
    age_days = 999
    if last_month:
        dt = _parse(last_month)
        if dt:
            age_days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400
    if age_days >= config.CALENDAR_MONTH_EVERY_DAYS:
        start_s = today.isoformat()
        end_s = (today + timedelta(days=horizon)).isoformat()
        for country, cat in (("IN", "india_econ_calendar"), ("US", "us_econ_calendar")):
            rows = db.calendar_events_range(start_s, end_s, country=country)
            msg = cal.format_month(rows, country, today, horizon)
            chat = config.route_channel(cat)
            await queue.enqueue(chat, msg, priority=1)
            sent += 1
        db.record_run("econ_calendar_month", True, items=2)
        log.info("calendar: posted 30-day boards")

    target = (today + timedelta(days=days_ahead)).isoformat()
    t3 = [e for e in db.calendar_events_on(target, unalerted_only=True) if e.get("event_type")]
    for ev in t3:
        cat = cal.COUNTRY_CHANNEL.get(ev.get("country") or "", "econ_calendar_alerts")
        await queue.enqueue(config.route_channel(cat), cal.format_t3(ev), priority=1)
        sent += 1
    if t3:
        db.mark_calendar_alerted([e["id"] for e in t3])
        log.info("calendar: T-3 alerted %d events for %s", len(t3), target)
    else:
        log.info("calendar: nothing 3 days out on %s", target)

    t0 = [e for e in db.calendar_dayof_due(today.isoformat(), min_importance=4) if e.get("event_type")]
    for ev in t0:
        cat = cal.COUNTRY_CHANNEL.get(ev.get("country") or "", "econ_calendar_alerts")
        await queue.enqueue(config.route_channel(cat), cal.format_today(ev), priority=0)
        sent += 1
    if t0:
        db.mark_calendar_dayof([e["id"] for e in t0])
        log.info("calendar: TODAY pinged %d events", len(t0))

    return sent


async def send_status() -> str:
    """On-demand summary — handy for a manual health check from the CLI."""
    s = db.stats()
    lines = ["📊 STATUS", "", f"seen_urls: {s['seen_urls']} (posted {s['posted']})",
             f"market_events: {s['market_events']}", f"calendar_events: {s['calendar_events']}",
             f"fetchers tracked: {s['fetchers']}", "", "Fetchers:"]
    for f in sorted(db.all_fetchers(), key=lambda x: x["fetcher_name"]):
        age = _age_min(f.get("last_success_at"))
        state = f"{age:.0f}m ago" if age is not None else "NEVER"
        lines.append(f"• {f['fetcher_name']}: {state} (items {f.get('items_seen') or 0})")
    msg = "\n".join(lines)
    queue = get_queue(config.FAST_LANE_TOKEN, "fast")
    queue.start()
    await queue.enqueue(config.route_channel("admin_health"), msg, priority=0)
    return msg