# Master Build Prompt — Aslam News Media Telegram Bot

Paste this whole file into your AI coding tool (Claude Code, Cursor, etc.) to start building. Read it fully before writing any code. **Build in the phase order below — confirm each phase works before moving to the next one.** Do not skip ahead.

## Context
I'm building a Telegram bot system that aggregates news and market data into topic-based channels, replacing a WhatsApp community I currently run manually. Full spec is in `prd.md` (what/why) and `trd.md` (architecture/sources/schema) — read both if provided alongside this prompt.

## Hard constraints — do not violate these
1. **Language: Python only.**
2. **No paid/registered API for news or India-market data.** RSS, Google News RSS, SEC EDGAR, and NSE/BSE must all be keyless. NSE's session-cookie handling is fine (that's not a key) — use `nsepython` or the `nse` package, which handles that internally.
3. **OpenRouter + DeepSeek V4 Flash is the only paid API allowed**, and it's used solely for smart filtering. Model slug: `deepseek/deepseek-v4-flash`. Base URL: `https://openrouter.ai/api/v1`. Key from env var `OPENROUTER_API_KEY`.
4. **Never post full article text** — title + short snippet + link only, every time, regardless of source.
5. **All Telegram sends go through a rate-limit-safe queue** — never a raw send loop. Respect: ~1 msg/sec per chat, ~20/min per group, ~30/sec global.
6. **Two separate bots, not one:** Bot #1 (slow lane, general news) and Bot #2 (fast lane, markets/alerts). Do not merge them.
7. **Every fetcher must record a last-successful-run timestamp** to the DB from Phase 1 onward — this feeds the health monitor built in Phase 4. Build the timestamp write in now, not as a later retrofit.
8. **GDELT and Moneycontrol/Chittorgarh are excluded by default** — still under review. Do not add them unless I explicitly say so.

## Architecture
- **Bot #1 (Slow Lane):** scheduler (15–30 min) → fetcher → normalizer → rule-based prefilter (exact-URL dedup, keyword scope, `rapidfuzz` for near-duplicates) → batched smart filter → router (category → channel) → send queue → Telegram
- **Bot #2 (Fast Lane):** scheduler (1–5 min) → fetcher → per-item smart filter (3-way: keep/reject/review) → router → send queue → Telegram; `review` decisions go to a private admin channel instead of any public one
- **Health Monitor:** hourly job comparing each fetcher's last-run timestamp to its expected schedule → alerts the same private admin channel if something's gone stale
- Shared SQLite DB (start here; Postgres later if needed) for dedup tracking, market events, calendar events, and fetcher timestamps — see `trd.md` section 4 for schema

## Tech stack
`python-telegram-bot` or `aiogram` · `feedparser` · `APScheduler` (or cron) · SQLite · `rapidfuzz` · `openai` python package (for the OpenRouter-compatible LLM call) · `nsepython`/`nse` for NSE data

## Build phases

**Phase 1 — MVP, slow lane only**
- 1 bot, 2–3 categories, 5–8 hand-picked RSS feeds (no Google News, no markets yet)
- Poll every 15–30 min, dedup by URL hash only (no smart filter yet), post to a single test channel
- Confirm the fetch → dedup → send loop works end-to-end before adding any intelligence
- Ask me for the exact RSS feed URLs and the test channel's bot token/chat ID before starting

**Phase 2 — Smart filter, slow lane**
- Add `rapidfuzz` prefilter dedup
- Add the batched smart-filter call (system prompt in `trd.md` section 5a) before routing

**Phase 3 — Fast lane / markets**
- Build Bot #2: SEC EDGAR, Finnhub, NSE (all 7 feeds: bulk/block deals, earnings/announcements, IPO mainboard+SME, corporate actions, shareholding pattern, FII/DII flows, circulars), BSE cross-check
- Per-item smart filter with the 3-way system prompt (`trd.md` section 5b) — `review` decisions route to the private admin channel

**Phase 4 — Manual calendar + health monitoring**
- Manual economic-calendar table + daily job checking for events exactly 3 days out
- Hourly health check comparing last-run timestamps, alerting the admin channel on staleness

**Phase 5 — Remaining categories**
- Roll out all remaining categories/channels only once Phases 1–4 are proven and running clean

## Before you start
Ask me for:
- The exact RSS feed URLs for Phase 1
- Bot tokens and channel IDs (test channel + eventual admin channel)
- Confirmation on anything in `trd.md` section 8 (open items) that affects what you're about to build
-


# PRD — Aslam News Media Telegram Bot

## 1. Overview
An automated Telegram news-and-markets aggregation system, replicating and improving on the existing WhatsApp community "Aslam News Media" (12 topic-based groups: crypto, politics, economic calendars, gold, stock market, daily news, etc.). Adds AI-powered smart filtering and dedicated depth on India + US market data that manual curation can't scale to.

## 2. Goals
- Deliver categorized, deduplicated, relevant news and market updates automatically
- Prioritize **speed and accuracy** for market-moving India + US data — this is the special-focus area
- Keep manual effort minimal — only the macro economic calendar is manually maintained (monthly)
- Solo-dev, AI-assisted ("vibe coded") build — architecture must stay simple enough to debug by prompting, not just by reading code

## 3. Non-Goals (v1)
- Not a public multi-tenant SaaS — built for this one community first
- No full-article reproduction — title + short snippet + link only (copyright)
- No automated macro-economic calendar scraping — deliberately manual by choice
- No per-user subscription management yet (v2 candidate)

## 4. Target Categories / Channels
Crypto News · US Politics · Indian Politics · US Economic Calendar · Indian Economic Calendar · Gold News · US Stock Market · Daily News · India Market/IPO · US Market/IPO · Economic Calendar Alerts · Admin/Health (private, not public-facing)

## 5. Core Features
| Feature | Purpose |
|---|---|
| Multi-source ingestion | RSS, Google News, SEC EDGAR, Finnhub, NSE, BSE (GDELT + Moneycontrol/Chittorgarh: **status undecided**, default excluded) |
| AI smart filtering | Relevance + quality + dedup judgment via DeepSeek V4 Flash |
| Two-speed pipeline | Slow lane (general news, batched, 15-30 min) / Fast lane (markets, per-item, 1-5 min) |
| 3-way fast-lane decision | keep (auto-post) / reject (drop) / **needs-review** (borderline → private admin channel, never guessed publicly) |
| Manual economic calendar | User-maintained table, automatic 3-day-advance alert |
| Health monitoring | Hourly check on last-successful-run per source; alerts admin channel if one goes silent |
| Rate-limit-safe delivery | Queued sends, never a raw loop, respects Telegram's per-chat/global limits |

## 6. MVP Scope
1 bot, 2–3 categories, 5–8 hand-picked RSS feeds only. Poll every 15–30 min, dedup by URL hash (no smart filter yet), post to a single test channel. Goal: confirm you'd actually read this daily before building anything else.

## 7. Full-Version Scope
All categories/channels · two-bot fast/slow architecture · full India+US market source set · AI smart filtering with review tier · health monitoring · manual calendar alerts.

## 8. Success Criteria
- You read your own feed daily instead of falling back to manual WhatsApp curation
- Spam/false-positive rate low enough that channels don't feel noisy
- No India/US market-moving event missed within its lane's polling window
- A source going silent is caught within an hour, not discovered days later

## 9. Known Risks
- Google News RSS and NSE's endpoints are unofficial/undocumented — can change without notice
- Telegram flood limits require a real send queue
- LLM filtering cost scales with volume — currently cheap, worth monitoring as categories grow

## 10. Open Decisions
- GDELT: keep (for future trend/volume signal) or cut entirely — **undecided**
- Moneycontrol/Chittorgarh: verify RSS/API exists before committing either way



# TRD — Aslam News Media Telegram Bot

Companion to `prd.md`. Diagram reference: `backend-architecture.mermaid` (previously shared, kept in sync with this doc).

## 1. Architecture Summary
Two independent bots sharing one database:

- **Bot #1 — Slow Lane (news):** scheduler (15–30 min) → fetcher → normalizer → rule-based prefilter (exact-URL dedup, keyword scope) → **batched** smart filter → router → send queue → Telegram
- **Bot #2 — Fast Lane (markets/alerts):** scheduler (1–5 min) → fetcher → **per-item** smart filter (3-way decision) → router → send queue → Telegram; borderline items → private admin channel instead
- **Health Monitor:** hourly cron comparing each fetcher's last-successful-run timestamp against its expected schedule → alerts admin channel if stale
- Both lanes read/write the same DB (dedup tracking + last-run timestamps)

## 2. Tech Stack
- **Language:** Python — best-documented for this exact use case, most AI-tool-fluent (relevant since this is built via AI-assisted/"vibe" coding)
- **Telegram:** `python-telegram-bot` or `aiogram`
- **RSS parsing:** `feedparser`
- **Scheduling:** `APScheduler` or plain cron
- **DB:** SQLite to start, Postgres if volume grows
- **Fuzzy matching:** `rapidfuzz` (rule-based prefilter dedup)
- **LLM client:** `openai` python package, pointed at OpenRouter (`base_url="https://openrouter.ai/api/v1"`, model `deepseek/deepseek-v4-flash`)
- **India market data:** `nsepython` or `nse` unofficial package — handles NSE's session-cookie requirement internally (not an API key)

## 3. Data Sources
| Source | Access | Covers |
|---|---|---|
| RSS Feeds (CNBC, Reuters, etc.) | No key | General news |
| Google News RSS (`news.google.com/rss/search`) | No key, unofficial/fragile, ~100-item cap, redirect-encoded links | General news, keyword search |
| GDELT Doc 2.0 API (`api.gdeltproject.org/api/v2/doc/doc`) | No key | Global headline search — **status undecided, may be cut** |
| SEC EDGAR | Official, free, no key, requires `User-Agent` header, 10 req/sec limit | 8-K (earnings), S-1 (IPO) — US |
| Finnhub | Free tier, **requires registered key**, 60 calls/min | Earnings Calendar, IPO Calendar — US |
| NSE India (unofficial) | No key, session-cookie handled by library | Bulk/block deals + short-selling, corporate announcements (earnings), IPO (mainboard + SME/EMERGE), corporate actions (dividends/splits/bonus), shareholding pattern, FII/DII daily flows, circulars |
| BSE India | No key | Cross-check for announcements & IPO |
| Moneycontrol / Chittorgarh | Unverified | India IPO tracking — **confirm RSS/API exists before use** |
| Manual economic calendar | User-maintained table | Macro events (CPI, Fed/RBI decisions) — updated monthly, checked daily for 3-day-ahead alerts |

## 4. Data Schema (rough)
```
articles / seen_urls: id, url_hash, source, category, title, published_at, posted_at
market_events: id, event_type (bulk_deal|earnings|ipo|corp_action|shareholding|fii_dii|circular),
               company, date, source, posted_at
calendar_events: date, event_name, country, importance   -- manually entered
fetcher_last_run: fetcher_name, last_success_at           -- feeds the health monitor
```

## 5. Smart Filter Contracts

### 5a. Slow lane — batched, 2-way (keep/reject)
```
You are a news filtering and deduplication engine for a Telegram news aggregation
bot. You will be given a numbered batch of candidate news items (title, source
domain, and target category) collected from RSS feeds, Google News, and GDELT.

For each item, judge:
1. RELEVANT - does the title genuinely match its target category, not just
coincidentally contain a keyword? (e.g. "Gold's Gym opens new branch" is NOT
gold-market news)
2. QUALITY - reject pure clickbait, opinion/op-ed pieces, and low-substance
roundups (e.g. "5 things to know today") unless clearly high-value
3. DUPLICATE - group items reporting the same underlying story/event even if
worded differently, and pick the single best representative (prefer the
higher-quality/more recognizable source domain)

Respond with ONLY a JSON array, no prose, no markdown fences. One object per
input item id:

[
  {"id": 3, "keep": true, "category": "gold_news", "duplicate_of": null, "importance": 3},
  {"id": 4, "keep": false, "reason": "off-topic"},
  {"id": 5, "keep": true, "category": "gold_news", "duplicate_of": 3, "importance": 2}
]

Rules:
- "duplicate_of" is the id of the item you consider the canonical version of
the same story; null if unique.
- "importance" is 1 (routine) to 5 (major/market-moving) - be conservative,
most items should land at 2-3.
- Never invent ids not present in the input.
- If unsure about relevance, keep=false. A spammy feed is worse than a missed
story - favor precision over recall.
```

### 5b. Fast lane — per-item, 3-way (keep/reject/review)
```
You are a market-alert filtering engine for a Telegram bot focused on India
and US market-moving events (bulk/block deals, earnings, IPO, corporate
actions, economic calendar alerts). You will be given ONE candidate item at a
time (title, source, event_type, target_category).

Decide one of three outcomes:
1. KEEP - clearly relevant, high-confidence, market-moving - auto-post
immediately
2. REJECT - clearly irrelevant, spam, or duplicate of something already
posted - drop silently
3. REVIEW - relevant but ambiguous (unclear significance, possible duplicate,
unclear company match, or borderline importance) - send to a private admin
review queue instead of guessing

Respond with ONLY a JSON object, no prose, no markdown fences:
{"decision": "keep", "category": "...", "importance": 1-5, "reason": "short one-line reason, always included"}

Rules:
- Favor REVIEW over REJECT when genuinely uncertain - dropping a real market
event silently is worse than asking a human to glance at it once.
- Favor REVIEW over KEEP when the story could be market-moving but you are
not confident of its accuracy or significance - better a short delay than a
wrong high-confidence post.
- "importance" 1 (routine filing) to 5 (major market-moving event).
- Never invent data not present in the input.
```

## 6. Rate Limits
- Telegram: ~1 msg/sec per chat, ~20/min per group, ~30/sec global — queue required, never a raw send loop
- SEC EDGAR: 10 req/sec
- Finnhub free tier: 60 calls/min
- NSE: no published limit — self-throttle anyway, and handle cookie refresh gracefully

## 7. Non-Functional Requirements
- **Reliability:** retry/backoff per fetcher; health monitor must alert within 1 hour of a source going stale
- **Accuracy:** fast lane favors precision over recall; borderline cases go to human review, never auto-posted on a guess
- **Cost control:** batch LLM calls where latency allows (slow lane); per-item calls reserved for the low-volume fast lane
- **Legal:** title + short snippet + link only — never full article text, regardless of source

## 8. Open Items
- GDELT: keep (for future trend/volume signal) or cut — undecided
- Moneycontrol/Chittorgarh: confirm RSS/API availability before committing
- Per-user subscription management: not yet designed — v2 candidate
- 