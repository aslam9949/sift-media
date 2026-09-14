#!/usr/bin/env python3
"""Aslam News Media — entrypoint.

Runs both lanes (separate schedulers, separate bot tokens when BOT2 is set),
the hourly health monitor, and the daily calendar alert.

    python main.py run                # run everything on schedule
    python main.py once slow          # one slow-lane cycle
    python main.py once fast          # one fast-lane cycle
    python main.py health             # one health sweep
    python main.py calendar [days]    # one calendar check
    python main.py status             # post a status summary to admin
    python main.py test-send          # send a test message to the channel
    python main.py add-event DATE "NAME" [COUNTRY] [IMPORTANCE] [NOTES]
    python main.py list-events
"""
from __future__ import annotations

import asyncio
import logging
import sys

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import config
import db
import monitor
from bots import fast_lane, slow_lane
import daily_digest
from send_queue import get_queue


def setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)-14s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    for noisy in ("apscheduler.executors.default", "httpx", "httpcore", "openai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


log = logging.getLogger("main")


def preflight() -> bool:
    ok = True
    if not config.BOT1_TOKEN:
        log.error("BOT1_TOKEN missing — cannot send anything")
        ok = False
    if not config.DEFAULT_CHANNEL:
        log.error("SLOW_LANE_CHANNEL_ID missing — nowhere to post")
        ok = False
    if config.SINGLE_BOT_MODE:
        log.warning("BOT2_TOKEN not set — SINGLE-BOT MODE (both lanes on Bot #1)")
    if not config.LLM_ENABLED:
        log.warning("No LLM key — slow lane passes all, fast lane routes to review")
    if not config.ADMIN_CHANNEL_ID:
        log.warning("No ADMIN_CHANNEL_ID/ADMIN_DM_ID — review+health go to the default channel")
    if config.DRY_RUN:
        log.warning("DRY_RUN=1 — nothing will actually be sent")
    log.info("channels: %d categories mapped", len(set(config.CHANNEL_MAP.values())))
    return ok


async def _guard(coro_fn, label: str):
    """Never let a scheduled job's exception kill the scheduler."""
    try:
        await coro_fn()
    except Exception:
        log.exception("job %s crashed", label)


async def _job_slow() -> None:
    await _guard(slow_lane.run_cycle, "slow")


async def _job_fast() -> None:
    await _guard(fast_lane.run_cycle, "fast")


async def _job_health() -> None:
    await _guard(monitor.check_health, "health")


async def _job_calendar() -> None:
    await _guard(monitor.check_calendar, "calendar")


async def _job_digest_morning() -> None:
    await _guard(daily_digest.morning_digest, "digest_morning")


async def _job_digest_evening() -> None:
    await _guard(daily_digest.evening_digest, "digest_evening")


async def run_forever() -> None:
    db.init()
    if not preflight():
        sys.exit(1)

    sched = AsyncIOScheduler(timezone="UTC")

    # Pass coroutine FUNCTIONS directly. Passing a sync lambda that calls
    # asyncio.create_task() fails with "no running event loop", because
    # APScheduler runs sync callables in a thread pool, not on the loop.
    # And never pass next_run_time=None — that permanently parks the job.
    sched.add_job(
        _job_slow, IntervalTrigger(minutes=config.SLOW_INTERVAL_MIN),
        id="slow_lane", max_instances=1, coalesce=True, misfire_grace_time=300,
    )
    sched.add_job(
        _job_fast, IntervalTrigger(minutes=config.FAST_INTERVAL_MIN),
        id="fast_lane", max_instances=1, coalesce=True, misfire_grace_time=120,
    )
    sched.add_job(
        _job_health, IntervalTrigger(minutes=config.HEALTH_INTERVAL_MIN),
        id="health", max_instances=1, coalesce=True, misfire_grace_time=600,
    )
    sched.add_job(
        _job_calendar, CronTrigger(hour=config.CALENDAR_HOUR, minute=0),
        id="calendar", max_instances=1, coalesce=True, misfire_grace_time=3600,
    )
    sched.add_job(
        _job_digest_morning, CronTrigger(hour=config.DIGEST_MORNING_HOUR, minute=30),
        id="digest_morning", max_instances=1, coalesce=True, misfire_grace_time=3600,
    )
    sched.add_job(
        _job_digest_evening, CronTrigger(hour=config.DIGEST_EVENING_HOUR, minute=30),
        id="digest_evening", max_instances=1, coalesce=True, misfire_grace_time=3600,
    )
    sched.start()

    for job in sched.get_jobs():
        log.info("scheduled %s -> next run %s", job.id, job.next_run_time)

    log.info(
        "started — slow every %dm, fast every %dm, health every %dm, calendar daily %02d:00 UTC, "
        "digest %02d:30 + %02d:30 UTC",
        config.SLOW_INTERVAL_MIN, config.FAST_INTERVAL_MIN,
        config.HEALTH_INTERVAL_MIN, config.CALENDAR_HOUR,
        config.DIGEST_MORNING_HOUR, config.DIGEST_EVENING_HOUR,
    )

    # Kick both lanes once at boot so we don't wait a whole interval.
    asyncio.create_task(_job_slow())
    asyncio.create_task(_job_fast())

    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("shutting down")
    finally:
        sched.shutdown(wait=False)
        for q in ("slow", "fast"):
            try:
                await get_queue(config.BOT1_TOKEN, q).close()
            except Exception:
                pass


async def _drain(name: str) -> None:
    q = get_queue(config.BOT1_TOKEN if name == "slow" else config.FAST_LANE_TOKEN, name)
    q.start()
    await q.join()
    log.info("[%s] queue drained — sent=%d failed=%d", name, q.sent, q.failed)
    await q.close()


async def once(lane: str) -> None:
    db.init()
    preflight()
    if lane == "slow":
        print(await slow_lane.run_cycle())
    else:
        print(await fast_lane.run_cycle())
    await _drain(lane)


async def health_once() -> None:
    db.init()
    print(await monitor.check_health())
    await _drain("fast")


async def calendar_once(days: int | None) -> None:
    db.init()
    print("events alerted:", await monitor.check_calendar(days))
    await _drain("fast")


async def status_once() -> None:
    db.init()
    print(await monitor.send_status())
    await _drain("fast")


async def test_send() -> None:
    db.init()
    preflight()
    q = get_queue(config.BOT1_TOKEN, "slow")
    q.start()
    await q.enqueue(
        config.DEFAULT_CHANNEL,
        "✅ Sift Media — connection test\n\nBot is wired up and the send queue works.",
        priority=0,
    )
    await q.join()
    print(f"sent={q.sent} failed={q.failed}")
    await q.close()


def main() -> None:
    setup_logging()
    args = sys.argv[1:]
    cmd = args[0] if args else "run"

    if cmd == "run":
        asyncio.run(run_forever())
    elif cmd == "once":
        lane = args[1] if len(args) > 1 else "slow"
        if lane not in ("slow", "fast"):
            sys.exit("usage: main.py once [slow|fast]")
        asyncio.run(once(lane))
    elif cmd == "health":
        asyncio.run(health_once())
    elif cmd == "calendar":
        asyncio.run(calendar_once(int(args[1]) if len(args) > 1 else None))
    elif cmd == "status":
        asyncio.run(status_once())
    elif cmd == "test-send":
        asyncio.run(test_send())
    elif cmd == "add-event":
        if len(args) < 3:
            sys.exit('usage: main.py add-event YYYY-MM-DD "Event name" [COUNTRY] [1-5] [notes]')
        db.init()
        added = db.add_calendar_event(
            args[1], args[2],
            args[3] if len(args) > 3 else "",
            int(args[4]) if len(args) > 4 else 3,
            args[5] if len(args) > 5 else "",
        )
        print("added" if added else "already exists")
    elif cmd == "list-events":
        db.init()
        conn = db.connect()
        rows = conn.execute("SELECT * FROM calendar_events ORDER BY date").fetchall()
        if not rows:
            print("(no calendar events — add with: main.py add-event)")
        for r in rows:
            flag = "alerted" if r["alerted_at"] else "pending"
            print(f"{r['date']}  [{r['country'] or '--'}]  {r['event_name']}  ★{r['importance']}  ({flag})")
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
