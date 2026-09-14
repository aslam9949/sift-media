"""Central config — every knob in one place, all env-driven.

Design rule: a missing credential DISABLES a feature, it never crashes the app.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _env(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip()


def _int(key: str, default: int) -> int:
    try:
        return int(_env(key) or default)
    except ValueError:
        return default


# ---------------------------------------------------------------- Telegram
BOT1_TOKEN = _env("BOT1_TOKEN")                 # slow lane (general news)
BOT2_TOKEN = _env("BOT2_TOKEN")                 # fast lane; falls back to BOT1
FAST_LANE_TOKEN = BOT2_TOKEN or BOT1_TOKEN
SINGLE_BOT_MODE = not BOT2_TOKEN

DEFAULT_CHANNEL = _env("SLOW_LANE_CHANNEL_ID")
ADMIN_CHANNEL_ID = _env("ADMIN_CHANNEL_ID") or _env("ADMIN_DM_ID")

# Daily PDF digest (morning + evening): channel, significance floor, hours.
DIGEST_CHANNEL_ID = _env("DIGEST_CHANNEL_ID")
DIGEST_MIN_SIGNIFICANCE = _int("DIGEST_MIN_SIGNIFICANCE", 6)
DIGEST_MORNING_HOUR = _int("DIGEST_MORNING_HOUR", 2)   # 02:00 UTC = 07:30 IST
DIGEST_EVENING_HOUR = _int("DIGEST_EVENING_HOUR", 13)  # 13:00 UTC = 18:30 IST
DIGEST_MIN_ITEMS = _int("DIGEST_MIN_ITEMS", 4)  # skip the PDF if fewer than this

# Category -> channel. Unset categories fall back to DEFAULT_CHANNEL.
CATEGORIES = [
    "crypto_news",
    "geopolitics",
    "us_politics",
    "india_politics",
    "us_econ_calendar",
    "india_econ_calendar",
    "gold_news",
    "us_stock_market",
    "daily_news",
    "india_market_ipo",
    "us_market_ipo",
    "econ_calendar_alerts",
]

CHANNEL_MAP = {cat: _env(f"CHANNEL_{cat.upper()}") or DEFAULT_CHANNEL for cat in CATEGORIES}
CHANNEL_MAP["general"] = DEFAULT_CHANNEL
CHANNEL_MAP["admin_health"] = ADMIN_CHANNEL_ID or DEFAULT_CHANNEL


def route_channel(category: str | None) -> str:
    return CHANNEL_MAP.get(category or "general") or DEFAULT_CHANNEL


# ---------------------------------------------------------------- LLM (b.ai)
LLM_API_KEY = _env("BAI_API_KEY") or _env("OPENROUTER_API_KEY")
LLM_BASE_URL = _env("BAI_BASE_URL", "https://api.b.ai/v1")
LLM_MODEL = _env("BAI_MODEL", "deepseek-v4-flash-vision-exp")
LLM_ENABLED = bool(LLM_API_KEY) or bool(_env("OPENROUTER_API_KEY"))
OPENROUTER_API_KEY = _env("OPENROUTER_API_KEY")
OPENROUTER_API_BASE = _env("OPENROUTER_API_BASE", "https://openrouter.ai/api/v1")
LLM_TIMEOUT = _int("LLM_TIMEOUT", 120)
# Reasoning model: it burns completion tokens on an internal reasoning channel
# before emitting JSON. Budget generously or the answer gets truncated to empty.
LLM_MAX_TOKENS = _int("LLM_MAX_TOKENS", 8000)
SLOW_BATCH_SIZE = _int("SLOW_BATCH_SIZE", 10)
# b.ai rate-limits concurrent calls (429). Pace them and back off hard on 429.
LLM_MIN_GAP_SEC = float(_env("LLM_MIN_GAP_SEC", "2.0"))
LLM_RATE_BACKOFF_SEC = float(_env("LLM_RATE_BACKOFF_SEC", "8"))

# ---------------------------------------------------------------- Database
DB_PATH = Path(_env("DB_PATH") or (BASE_DIR / "data" / "aslam_news.db"))

# ---------------------------------------------------------------- Scheduling
SLOW_INTERVAL_MIN = _int("SLOW_INTERVAL_MIN", 20)     # 15-30 min per spec
FAST_INTERVAL_MIN = _int("FAST_INTERVAL_MIN", 3)      # 1-5 min per spec
HEALTH_INTERVAL_MIN = _int("HEALTH_INTERVAL_MIN", 60)
CALENDAR_HOUR = _int("CALENDAR_HOUR", 8)              # daily 3-day-ahead check
CALENDAR_LOOKAHEAD_DAYS = _int("CALENDAR_LOOKAHEAD_DAYS", 3)
CALENDAR_HORIZON_DAYS = _int("CALENDAR_HORIZON_DAYS", 30)
CALENDAR_MONTH_EVERY_DAYS = _int("CALENDAR_MONTH_EVERY_DAYS", 7)

# ---------------------------------------------------------------- Rate limits
RATE_GLOBAL_PER_SEC = float(_env("RATE_GLOBAL_PER_SEC", "25"))   # <30/s
RATE_PER_CHAT_SEC = float(_env("RATE_PER_CHAT_SEC", "1.1"))      # ~1 msg/s
RATE_PER_CHAT_PER_MIN = _int("RATE_PER_CHAT_PER_MIN", 19)        # <20/min
SEC_EDGAR_UA = _env("SEC_EDGAR_UA", "AslamNewsMedia/1.0 (contact: telegram @Aslamnewsmedia_bot)")

# ---------------------------------------------------------------- Content
SNIPPET_MAX = _int("SNIPPET_MAX", 280)   # never full article text
MAX_ITEMS_PER_CYCLE = _int("MAX_ITEMS_PER_CYCLE", 40)
FAST_FILTER_WORKERS = _int("FAST_FILTER_WORKERS", 2)   # concurrent per-item LLM calls
FAST_MAX_ITEMS_PER_CYCLE = _int("FAST_MAX_ITEMS_PER_CYCLE", 12)
FUZZY_THRESHOLD = _int("FUZZY_THRESHOLD", 88)
DEDUP_WINDOW_HOURS = _int("DEDUP_WINDOW_HOURS", 72)
# Public post floor: LLM significance below this is dropped (news + markets).
# 6 = digest floor. 5 was flooding (~half of news cards were borderline 5s).
POST_MIN_SIGNIFICANCE = _int("POST_MIN_SIGNIFICANCE", 6)
# Flood gate: max public posts per Telegram chat per rolling hour
# (news + market alerts share the same counter).
CHANNEL_MAX_PER_HOUR = _int("CHANNEL_MAX_PER_HOUR", 8)
# Admin "review" queue cap — those were 227 extra pings in 2 days.
REVIEW_MAX_PER_HOUR = _int("REVIEW_MAX_PER_HOUR", 4)
# NSE bulk/block: skip tiny prints. ₹10 crore notional.
DEAL_MIN_NOTIONAL_INR = float(_env("DEAL_MIN_NOTIONAL_INR", "100000000"))

# Excluded by explicit instruction — do not enable without the user's say-so.
ENABLE_GDELT = _env("ENABLE_GDELT", "0") == "1"
ENABLE_MONEYCONTROL = _env("ENABLE_MONEYCONTROL", "0") == "1"
ENABLE_FINNHUB = _env("ENABLE_FINNHUB", "0") == "1"   # dropped: no signup

DRY_RUN = _env("DRY_RUN", "0") == "1"
LOG_LEVEL = _env("LOG_LEVEL", "INFO")
