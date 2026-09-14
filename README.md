# Sift Media — Telegram bot + live desk

Public brand: **Sift Media**. Code folders on the VPS stay `aslam-*`.
Live desk (Bloomberg-style, port 8780) is in `desk/`.
`.env`, `.venv/`, and `data/` are **not** in this repo.

Automated news + markets aggregation. Built to `prd.md` / `trd.md`.

## Quick start

```bash
cd /root/aslam-news-media

# run everything on schedule (what the service does)
.venv/bin/python main.py run

# one-off cycles for testing
.venv/bin/python main.py once slow      # general news pass
.venv/bin/python main.py once fast      # markets pass
.venv/bin/python main.py health         # staleness sweep
.venv/bin/python main.py calendar       # 3-day-ahead calendar alert
.venv/bin/python main.py status         # post a status summary to admin
.venv/bin/python main.py test-send      # prove Telegram wiring works

# rehearse without sending anything
DRY_RUN=1 .venv/bin/python main.py once slow
```

## Service

```bash
cp aslam-news-media.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now aslam-news-media
systemctl status aslam-news-media
tail -f data/bot.log
```

## Economic calendar (the one manual part)

Update monthly. Alerts fire automatically 3 days ahead.

```bash
.venv/bin/python main.py add-event 2026-09-15 "FOMC Rate Decision" US 5 "consensus hold"
.venv/bin/python main.py list-events
```

## Architecture

| Lane | Trigger | Filter | Destination |
|---|---|---|---|
| Slow (news) | every 20 min | batched, 2-way keep/reject | category channels |
| Fast (markets) | every 3 min | per-item, 3-way keep/reject/**review** | channels + admin for review |
| Health monitor | hourly | — | admin channel if a source goes stale |
| Calendar | daily 08:00 UTC | — | calendar-alerts channel |

Flow: `fetch → normalize → prefilter (URL hash + rapidfuzz) → smart filter → router → send queue → Telegram`

Every send goes through `send_queue.py`, which enforces ~25/sec global,
~1/sec per chat, 19/min per chat, with backoff and Telegram `RetryAfter`
handling. There is no raw send loop anywhere.

## Files

```
config.py         all knobs, env-driven; missing creds disable, never crash
db.py             SQLite schema + dedup + fetcher_last_run health timestamps
send_queue.py     rate-limit-safe queue (the only path to Telegram)
smart_filter.py   b.ai DeepSeek calls; both prompt contracts from trd.md
pipeline.py       normalizer, prefilter, router, message formatting
feeds.py          RSS + Google News source list
fetch/rss.py      RSS + Google News fetchers
fetch/sec_edgar.py  US 8-K / S-1 / 424B4 (keyless, User-Agent required)
fetch/nse.py      NSE feeds (deals, announcements, IPO, corp actions, results, FII/DII, circulars)
fetch/bse.py      BSE announcements (cross-check)
bots/slow_lane.py Bot #1 cycle
bots/fast_lane.py Bot #2 cycle
monitor.py        health monitor + calendar alerts
main.py           scheduler + CLI
```

## Source status (verified 2026-08-23)

| Source | Status |
|---|---|
| RSS (21 feeds) + Google News (8 queries) | ✅ live, ~790 items/cycle |
| SEC EDGAR 8-K / S-1 / 424B4 | ✅ live, 65 filings |
| NSE bulk/block deals + short selling | ✅ live, 250 items |
| NSE announcements | ✅ live |
| NSE IPO (mainboard + SME) | ✅ live |
| NSE corporate actions | ✅ live |
| NSE quarterly results (earnings) | ✅ live, 50 items |
| NSE FII/DII flows | ✅ live |
| NSE circulars | ✅ live |
| NSE shareholding pattern | ⚠️ endpoint removed by NSE — see note in `fetch/nse.py` |
| BSE announcements | ✅ endpoint works; returns empty on weekends/holidays |
| GDELT, Moneycontrol/Chittorgarh | ⛔ excluded by instruction (`ENABLE_GDELT=1` to revisit) |
| Finnhub | ⛔ dropped (no signup); SEC EDGAR covers US earnings/IPO |

## Config notes

- **Single-bot mode:** with no `BOT2_TOKEN`, both lanes run on Bot #1 with
  separate schedulers. Drop a `BOT2_TOKEN` into `.env` and restart — the lanes
  split automatically, no code change.
- **Channels:** everything currently routes to `SLOW_LANE_CHANNEL_ID`. To split
  by category, set `CHANNEL_CRYPTO_NEWS`, `CHANNEL_GOLD_NEWS`, etc. (see
  `config.CATEGORIES` for the full list of names).
- **Admin:** review items + health alerts go to `ADMIN_CHANNEL_ID`, falling
  back to `ADMIN_DM_ID`. Currently the owner's DM.
- **Copyright:** `SNIPPET_MAX` caps snippets at 280 chars. Title + snippet +
  link only, enforced in `pipeline.build_message`.
- **LLM:** `deepseek-v4-flash-vision-exp` via b.ai. It is a *reasoning* model —
  it spends completion tokens internally before answering, so `LLM_MAX_TOKENS`
  is 8000 and empty responses are retried 3x. Slow lane batches 10 at a time;
  fast lane judges 6 items concurrently.

## Fail-soft behaviour

- One fetcher dying never stops the others; the failure is recorded and the
  health monitor reports it.
- LLM down → slow lane keeps everything (rule dedup already ran); fast lane
  routes to **review**, never auto-posting an unjudged market item.
- Items are marked seen *before* sending, so a crash mid-cycle can't double-post.
- Fast-lane verdicts are persisted before sending, so a crash can't re-spend
  LLM calls on the same event.
- Malformed HTML is retried once as plain text rather than dropping the post.

## Gotchas hit during the build (don't regress these)

1. **APScheduler + async jobs.** Pass coroutine *functions* to `add_job`
   directly. Passing a sync `lambda` that calls `asyncio.create_task()` throws
   `RuntimeError: no running event loop`, because APScheduler runs sync
   callables in a thread pool, not on the event loop.
2. **Never pass `next_run_time=None`** to `add_job` — it permanently parks the
   job. It looks like "start immediately" but means the opposite. Symptom: the
   service is `active`, logs no errors, and silently never runs a cycle. The
   startup log now prints each job's real next-run time so this is visible.
3. **Business Standard serves malformed XML to custom User-Agents** but valid
   XML to a browser UA. `fetch/rss.py` uses a browser UA primarily and
   alternates on retry.
4. **b.ai rate-limits concurrent calls (429).** Calls are globally paced
   (`LLM_MIN_GAP_SEC`) with hard backoff on 429. Concurrency above ~2 workers
   triggers it constantly.
5. **b.ai is a reasoning model** — it spends completion tokens internally, so
   `LLM_MAX_TOKENS` must be generous (8000) or the answer truncates to empty.
   Empty content is retried 3x.
6. **BSE returns inconsistent shapes** — dict, double-encoded JSON string, or
   the bare text `No Record Found!`. Empty on weekends/holidays is normal and
   is recorded as a *success* so the health monitor doesn't false-alarm.
