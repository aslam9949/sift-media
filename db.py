"""SQLite layer — schema per TRD section 4, plus fetcher health timestamps."""
from __future__ import annotations

import hashlib
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import config

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_urls (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    url_hash     TEXT UNIQUE NOT NULL,
    url          TEXT,
    source       TEXT,
    category     TEXT,
    title        TEXT,
    title_norm   TEXT,
    published_at TEXT,
    summary_ai   TEXT,
    why_matters  TEXT,
    watch_next   TEXT,
    significance INTEGER,
    posted_at    TEXT,
    lane         TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_seen_created ON seen_urls(created_at);
CREATE INDEX IF NOT EXISTS idx_seen_hash    ON seen_urls(url_hash);

CREATE TABLE IF NOT EXISTS market_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key  TEXT UNIQUE,
    event_type TEXT,
    company    TEXT,
    date       TEXT,
    source     TEXT,
    title      TEXT,
    url        TEXT,
    decision   TEXT,
    importance INTEGER,
    posted_at  TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mkt_created ON market_events(created_at);

CREATE TABLE IF NOT EXISTS calendar_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    date       TEXT NOT NULL,
    event_name TEXT NOT NULL,
    country    TEXT,
    importance INTEGER DEFAULT 3,
    notes      TEXT,
    alerted_at TEXT,
    UNIQUE(date, event_name, country)
);

CREATE TABLE IF NOT EXISTS fetcher_last_run (
    fetcher_name    TEXT PRIMARY KEY,
    last_success_at TEXT,
    last_attempt_at TEXT,
    last_error      TEXT,
    items_seen      INTEGER DEFAULT 0,
    expected_min    INTEGER DEFAULT 30,
    lane            TEXT
);

CREATE TABLE IF NOT EXISTS health_alerts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    fetcher    TEXT,
    alerted_at TEXT
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def url_hash(url: str) -> str:
    return hashlib.sha256((url or "").strip().lower().encode()).hexdigest()


def connect() -> sqlite3.Connection:
    """Open the DB, apply schema, and migrate any missing columns.

    executesscript with IF NOT EXISTS creates a fresh DB but does NOT add
    columns to an already-existing table, so we ALTER for backwards compat.
    """
    global _conn
    if _conn is None:
        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
        _migrate(_conn)
        _conn.commit()
    return _conn


_NEW_COLUMNS = [
    ("summary_ai", "TEXT"),
    ("why_matters", "TEXT"),
    ("watch_next", "TEXT"),
    ("significance", "INTEGER"),
]

_CAL_COLUMNS = [
    ("event_type", "TEXT"),
    ("time_label", "TEXT"),
    ("dayof_alerted_at", "TEXT"),
]

_MKT_COLUMNS = [
    ("category", "TEXT"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(seen_urls)")}
    for name, ctype in _NEW_COLUMNS:
        if name not in cols:
            try:
                conn.execute(f"ALTER TABLE seen_urls ADD COLUMN {name} {ctype}")
            except sqlite3.OperationalError:
                pass  # column appeared between check and ALTER — ignore
    cal_cols = {r["name"] for r in conn.execute("PRAGMA table_info(calendar_events)")}
    for name, ctype in _CAL_COLUMNS:
        if name not in cal_cols:
            try:
                conn.execute(f"ALTER TABLE calendar_events ADD COLUMN {name} {ctype}")
            except sqlite3.OperationalError:
                pass
    mkt_cols = {r["name"] for r in conn.execute("PRAGMA table_info(market_events)")}
    for name, ctype in _MKT_COLUMNS:
        if name not in mkt_cols:
            try:
                conn.execute(f"ALTER TABLE market_events ADD COLUMN {name} {ctype}")
            except sqlite3.OperationalError:
                pass


def init() -> None:
    connect()


# ------------------------------------------------------------------ dedup
def is_seen(url: str) -> bool:
    c = connect()
    with _lock:
        row = c.execute("SELECT 1 FROM seen_urls WHERE url_hash=?", (url_hash(url),)).fetchone()
    return row is not None


def mark_seen(item: dict[str, Any], lane: str = "slow", posted: bool = False) -> bool:
    """Insert into seen_urls. Returns False if it was already there."""
    c = connect()
    with _lock:
        try:
            c.execute(
                """INSERT INTO seen_urls
                   (url_hash,url,source,category,title,title_norm,published_at,
                    summary_ai,why_matters,watch_next,significance,posted_at,lane,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    url_hash(item.get("url", "")),
                    item.get("url"),
                    item.get("source"),
                    item.get("category"),
                    item.get("title"),
                    item.get("title_norm"),
                    item.get("published_at"),
                    item.get("summary_ai"),
                    item.get("why_matters"),
                    item.get("watch_next"),
                    item.get("significance"),
                    now_iso() if posted else None,
                    lane,
                    now_iso(),
                ),
            )
            c.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def mark_posted(url: str) -> None:
    c = connect()
    with _lock:
        c.execute("UPDATE seen_urls SET posted_at=? WHERE url_hash=?", (now_iso(), url_hash(url)))
        c.commit()


def posted_count_for_category(category: str, hours: float = 1.0) -> int:
    """How many items were publicly posted in this category in the last N hours."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    c = connect()
    with _lock:
        row = c.execute(
            """SELECT COUNT(*) AS n FROM seen_urls
               WHERE category=? AND posted_at IS NOT NULL AND posted_at>=?""",
            (category or "", cutoff),
        ).fetchone()
    return int(row["n"] if row else 0)


def posted_count_for_chat(chat_id: str, hours: float = 1.0) -> int:
    """Public posts (news + markets) that landed in this Telegram chat recently."""
    cats = [c for c, dest in config.CHANNEL_MAP.items() if str(dest) == str(chat_id)]
    if not cats or not chat_id:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    placeholders = ",".join("?" * len(cats))
    c = connect()
    with _lock:
        slow = c.execute(
            f"""SELECT COUNT(*) AS n FROM seen_urls
                WHERE posted_at IS NOT NULL AND posted_at>=?
                  AND category IN ({placeholders})""",
            [cutoff, *cats],
        ).fetchone()
        fast = c.execute(
            f"""SELECT COUNT(*) AS n FROM market_events
                WHERE posted_at IS NOT NULL AND posted_at>=?
                  AND coalesce(category, 'india_market_ipo') IN ({placeholders})""",
            [cutoff, *cats],
        ).fetchone()
    return int(slow["n"] if slow else 0) + int(fast["n"] if fast else 0)


def review_count(hours: float = 1.0) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    c = connect()
    with _lock:
        row = c.execute(
            """SELECT COUNT(*) AS n FROM market_events
               WHERE decision='review' AND created_at>=?""",
            (cutoff,),
        ).fetchone()
    return int(row["n"] if row else 0)


def deal_posted_today(company: str) -> bool:
    """True if a bulk/block deal for this symbol already went out today (IST)."""
    if not (company or "").strip():
        return False
    c = connect()
    with _lock:
        row = c.execute(
            """SELECT 1 FROM market_events
               WHERE upper(company)=upper(?)
                 AND event_type IN ('bulk_deal', 'block_deal')
                 AND posted_at IS NOT NULL
                 AND date(posted_at, '+5 hours', '+30 minutes')
                     = date('now', '+5 hours', '+30 minutes')
               LIMIT 1""",
            (company.strip(),),
        ).fetchone()
    return row is not None


def recent_titles(hours: int | None = None) -> list[tuple[str, str]]:
    """(title_norm, url) posted recently — for rapidfuzz near-duplicate checks."""
    hours = hours or config.DEDUP_WINDOW_HOURS
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    c = connect()
    with _lock:
        rows = c.execute(
            "SELECT title_norm, url FROM seen_urls WHERE created_at>=? AND title_norm IS NOT NULL",
            (cutoff,),
        ).fetchall()
    return [(r["title_norm"], r["url"]) for r in rows]


def digest_items(window_start: str, window_end: str, min_significance: int) -> list[dict[str, Any]]:
    """Items posted in [window_start, window_end] at/above the significance floor.

    Used by the daily PDF digest. Excludes items whose source/snippet are empty
    so the digest only carries real, summarised stories.
    """
    c = connect()
    with _lock:
        rows = c.execute(
            """SELECT id, url, source, category, title, summary_ai, why_matters,
                      watch_next, significance, published_at
               FROM seen_urls
               WHERE posted_at IS NOT NULL
                 AND posted_at >= ?
                 AND posted_at <  ?
                 AND significance >= ?
                 AND summary_ai IS NOT NULL AND summary_ai <> ''
               ORDER BY significance DESC, category, id DESC""",
            (window_start, window_end, min_significance),
        ).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------ market events
def market_event_seen(event_key: str) -> bool:
    c = connect()
    with _lock:
        row = c.execute("SELECT 1 FROM market_events WHERE event_key=?", (event_key,)).fetchone()
    return row is not None


def save_market_event(ev: dict[str, Any], decision: str, importance: int = 0) -> bool:
    c = connect()
    with _lock:
        try:
            c.execute(
                """INSERT INTO market_events
                   (event_key,event_type,company,date,source,title,url,decision,importance,posted_at,created_at,category)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    ev.get("event_key"),
                    ev.get("event_type"),
                    ev.get("company"),
                    ev.get("date"),
                    ev.get("source"),
                    ev.get("title"),
                    ev.get("url"),
                    decision,
                    importance,
                    None,  # posted_at only after a real send
                    now_iso(),
                    ev.get("category"),
                ),
            )
            c.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def mark_market_posted(event_key: str) -> None:
    if not event_key:
        return
    c = connect()
    with _lock:
        c.execute(
            "UPDATE market_events SET posted_at=? WHERE event_key=? AND posted_at IS NULL",
            (now_iso(), event_key),
        )
        c.commit()


# ---------------------------------------------------------- fetcher health
def register_fetcher(name: str, expected_min: int, lane: str) -> None:
    c = connect()
    with _lock:
        c.execute(
            """INSERT INTO fetcher_last_run (fetcher_name,expected_min,lane)
               VALUES (?,?,?)
               ON CONFLICT(fetcher_name) DO UPDATE SET expected_min=excluded.expected_min,
                                                       lane=excluded.lane""",
            (name, expected_min, lane),
        )
        c.commit()


def record_run(name: str, ok: bool, items: int = 0, error: str | None = None) -> None:
    """Constraint 7: every fetcher records a last-successful-run timestamp."""
    c = connect()
    with _lock:
        c.execute("INSERT OR IGNORE INTO fetcher_last_run (fetcher_name) VALUES (?)", (name,))
        if ok:
            c.execute(
                "UPDATE fetcher_last_run SET last_success_at=?, last_attempt_at=?, items_seen=?, last_error=NULL WHERE fetcher_name=?",
                (now_iso(), now_iso(), items, name),
            )
        else:
            c.execute(
                "UPDATE fetcher_last_run SET last_attempt_at=?, last_error=? WHERE fetcher_name=?",
                (now_iso(), (error or "")[:400], name),
            )
        c.commit()


def all_fetchers() -> list[dict[str, Any]]:
    c = connect()
    with _lock:
        rows = c.execute("SELECT * FROM fetcher_last_run").fetchall()
    return [dict(r) for r in rows]


def alerted_recently(fetcher: str, hours: int = 6) -> bool:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    c = connect()
    with _lock:
        row = c.execute(
            "SELECT 1 FROM health_alerts WHERE fetcher=? AND alerted_at>=?", (fetcher, cutoff)
        ).fetchone()
    return row is not None


def log_health_alert(fetcher: str) -> None:
    c = connect()
    with _lock:
        c.execute("INSERT INTO health_alerts (fetcher,alerted_at) VALUES (?,?)", (fetcher, now_iso()))
        c.commit()


# --------------------------------------------------------- economic calendar
def add_calendar_event(date: str, name: str, country: str = "", importance: int = 3, notes: str = "") -> bool:
    return upsert_calendar_event({
        "date": date, "event_name": name, "country": country,
        "importance": importance, "notes": notes,
    })


def upsert_calendar_event(ev: dict[str, Any]) -> bool:
    """Insert or refresh metadata. Never clears alert timestamps."""
    c = connect()
    with _lock:
        c.execute(
            """INSERT INTO calendar_events
               (date,event_name,country,importance,notes,event_type,time_label)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(date, event_name, country) DO UPDATE SET
                 importance=excluded.importance,
                 notes=excluded.notes,
                 event_type=COALESCE(excluded.event_type, event_type),
                 time_label=COALESCE(excluded.time_label, time_label)""",
            (
                ev.get("date"),
                ev.get("event_name"),
                ev.get("country") or "",
                int(ev.get("importance") or 3),
                ev.get("notes") or "",
                ev.get("event_type") or "",
                ev.get("time_label") or "",
            ),
        )
        c.commit()
        return True


def calendar_events_on(date_str: str, unalerted_only: bool = True) -> list[dict[str, Any]]:
    c = connect()
    q = "SELECT * FROM calendar_events WHERE date=?"
    args: list[Any] = [date_str]
    if unalerted_only:
        q += " AND alerted_at IS NULL"
    q += " ORDER BY importance DESC, id"
    with _lock:
        rows = c.execute(q, args).fetchall()
    return [dict(r) for r in rows]


def calendar_events_range(start: str, end: str, country: str | None = None) -> list[dict[str, Any]]:
    c = connect()
    q = "SELECT * FROM calendar_events WHERE date>=? AND date<=?"
    args: list[Any] = [start, end]
    if country:
        q += " AND country=?"
        args.append(country)
    q += " ORDER BY date, importance DESC, id"
    with _lock:
        rows = c.execute(q, args).fetchall()
    return [dict(r) for r in rows]


def calendar_dayof_due(date_str: str, min_importance: int = 4) -> list[dict[str, Any]]:
    c = connect()
    with _lock:
        rows = c.execute(
            """SELECT * FROM calendar_events
               WHERE date=? AND importance>=? AND dayof_alerted_at IS NULL
               ORDER BY importance DESC, id""",
            (date_str, min_importance),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_calendar_alerted(ids: Iterable[int]) -> None:
    c = connect()
    with _lock:
        c.executemany("UPDATE calendar_events SET alerted_at=? WHERE id=?", [(now_iso(), i) for i in ids])
        c.commit()


def mark_calendar_dayof(ids: Iterable[int]) -> None:
    c = connect()
    with _lock:
        c.executemany(
            "UPDATE calendar_events SET dayof_alerted_at=? WHERE id=?",
            [(now_iso(), i) for i in ids],
        )
        c.commit()


def stats() -> dict[str, int]:
    c = connect()
    with _lock:
        return {
            "seen_urls": c.execute("SELECT COUNT(*) FROM seen_urls").fetchone()[0],
            "posted": c.execute("SELECT COUNT(*) FROM seen_urls WHERE posted_at IS NOT NULL").fetchone()[0],
            "market_events": c.execute("SELECT COUNT(*) FROM market_events").fetchone()[0],
            "calendar_events": c.execute("SELECT COUNT(*) FROM calendar_events").fetchone()[0],
            "fetchers": c.execute("SELECT COUNT(*) FROM fetcher_last_run").fetchone()[0],
        }
