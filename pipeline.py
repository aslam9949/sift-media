"""Normalizer + rule-based prefilter + router/formatter.

Dual-Track Smart Factory: VIP lane (Rank 8+) bypasses aggressive dedup;
Standard lane goes through MinHash dedup and clickbait filtering.
Google News URLs are unwrapped via RSS <source> tag when available.

Constraint 4 lives here: title + short snippet + link ONLY. Never full text.
"""
from __future__ import annotations

import hashlib
import html
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse, urlunparse

from datasketch import MinHash, MinHashLSH

import config
import db

log = logging.getLogger("pipeline")

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_TRACKING = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid", "ref", "oc"}

CATEGORY_KEYWORDS = {
    "crypto_news": ["bitcoin", "btc", "ethereum", "eth", "crypto", "blockchain", "altcoin", "stablecoin", "binance", "solana", "xrp", "defi", "token"],
    "gold_news": ["gold", "bullion", "xau", "precious metal", "silver", "comex gold", "gold price"],
    "geopolitics": [
        "war", "ceasefire", "invasion", "airstrike", "military strike", "missile", "drone strike",
        "troops", "offensive", "border clash", "conflict", "escalation", "truce", "peace talks",
        "sanctions", "tariff war", "trade war", "export controls", "embargo", "blockade",
        "nato", "united nations", "un security council", "brics", "g7", "g20", "opec",
        "diplomacy", "diplomatic", "summit", "treaty", "bilateral", "foreign minister",
        "foreign policy", "geopolitical", "ambassador", "envoy",
        "ukraine", "russia", "moscow", "kremlin", "putin", "zelensky",
        "israel", "gaza", "palestine", "hamas", "hezbollah", "lebanon", "west bank",
        "iran", "tehran", "syria", "yemen", "houthi", "red sea", "strait of hormuz",
        "china", "beijing", "taiwan", "taiwan strait", "south china sea", "xi jinping",
        "north korea", "pyongyang", "kim jong",
        "pakistan", "islamabad", "afghanistan", "taliban", "loc", "line of control",
        "venezuela", "sudan", "sahel", "myanmar",
    ],
    "us_politics": ["trump", "biden", "congress", "senate", "white house", "republican", "democrat", "supreme court", "washington", "capitol"],
    "india_politics": ["modi", "bjp", "congress party", "lok sabha", "rajya sabha", "parliament", "india government", "rahul gandhi", "delhi", "cabinet"],
    "us_stock_market": ["s&p", "nasdaq", "dow jones", "wall street", "fed", "federal reserve", "earnings", "stocks", "treasury", "nyse"],
    "india_market_ipo": ["sensex", "nifty", "nse", "bse", "sebi", "rbi", "ipo", "rupee", "dalal street"],
    "us_market_ipo": ["ipo", "s-1", "listing", "public offering", "nasdaq debut"],
}

GEO_STRONG = [
    "airstrike", "air strike", "missile strike", "drone strike", "ceasefire", "invasion",
    "war", "troops deployed", "sanctions", "nato", "un security council", "peace talks",
    "border clash", "military operation", "taiwan strait", "south china sea",
    "strait of hormuz", "red sea", "nuclear talks", "arms deal", "treaty",
]

# ------------------------------------------------------------- MinHash LSH
# Global LSH index for O(1) near-duplicate detection.
# threshold=0.85 means two items with Jaccard ≥ 0.85 are treated as dupes.
_LSH = MinHashLSH(threshold=0.85, num_perm=128)


def _make_minhash(text: str) -> MinHash:
    """Produce a MinHash signature from a tokenised title."""
    m = MinHash(num_perm=128)
    for tok in re.findall(r"[a-z0-9]+", text.lower()):
        m.update(tok.encode())
    return m


def is_duplicate_fast(title: str, event_id: str) -> bool:
    """Check + insert into the global LSH index in O(1) amortized.

    Returns True if a near-duplicate (Jaccard ≥ 0.85) is already present.
    """
    mh = _make_minhash(title)
    dupes = _LSH.query(mh)
    if dupes:
        log.debug("MinHash duplicate: %s matches %s", event_id[:30], dupes[0][:30])
        return True
    _LSH.insert(event_id, mh)
    return False


# ------------------------------------------------------------- normalizing
def clean_text(raw: str | None) -> str:
    if not raw:
        return ""
    return _WS.sub(" ", html.unescape(_TAG.sub(" ", raw))).strip()


def make_snippet(raw: str | None, limit: int | None = None) -> str:
    """Short snippet only — hard cap, cut on a word boundary. Never full text."""
    limit = limit or config.SNIPPET_MAX
    text = clean_text(raw)
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(".,;:") + "…"


def canonical_url(url: str) -> str:
    """Strip tracking params; unwrap Google News redirect links when possible."""
    if not url:
        return ""
    url = url.strip()
    try:
        p = urlparse(url)
        if "news.google.com" in p.netloc:
            qs = parse_qs(p.query)
            for key in ("url", "u"):
                if key in qs and qs[key]:
                    return canonical_url(qs[key][0])
        kept = [(k, v) for k, v in parse_qs(p.query).items() if k.lower() not in _TRACKING]
        query = "&".join(f"{k}={v[0]}" for k, v in kept if v)
        return urlunparse((p.scheme, p.netloc, p.path.rstrip("/") or "/", "", query, ""))
    except Exception:
        return url


def norm_title(title: str) -> str:
    t = clean_text(title).lower()
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return _WS.sub(" ", t).strip()


def source_domain(url: str, fallback: str = "") -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else (host or fallback)
    except Exception:
        return fallback


def normalize(raw: dict[str, Any], default_category: str = "general") -> dict[str, Any] | None:
    """Turn a raw fetcher dict into the canonical item shape."""
    title = clean_text(raw.get("title"))
    url = canonical_url(raw.get("url", ""))
    if not title or not url:
        return None
    return {
        "title": title,
        "title_norm": norm_title(title),
        "url": url,
        "snippet": make_snippet(raw.get("summary") or raw.get("snippet")),
        "source": raw.get("source") or source_domain(url),
        "category": raw.get("category") or default_category,
        "published_at": raw.get("published_at") or "",
        "event_type": raw.get("event_type"),
        "fetcher": raw.get("fetcher"),
    }


# --------------------------------------------------------- Google News unwrap
# When the RSS <source> tag carries the real publisher (e.g. "Reuters") but the
# URL host is news.google.com, we can fix the source rank without an HTTP redirect.

_TLD_STRIP = re.compile(r"\.(com|net|org|io|co\.uk|com\.au|co\.in|in)\b", re.I)


def extract_real_publisher(entry: dict[str, Any]) -> tuple[str, str]:
    """Read RSS <source> tag to find the real publisher domain.

    Returns (publisher_name, domain).  Falls back to parsing the URL host if
    the source tag is missing or empty.
    """
    raw_source = entry.get("rss_source") or {}
    publisher = ""
    if isinstance(raw_source, dict):
        publisher = raw_source.get("title", "").strip()
    if not publisher and isinstance(raw_source, str):
        publisher = raw_source.strip()

    # Derive a clean domain from the publisher name
    domain = ""
    if publisher:
        domain = publisher.lower()
        domain = re.sub(r"[^a-z0-9.]+", ".", domain)
        domain = re.sub(r"\.{2,}", ".", domain)
        domain = domain.strip(".")
        # Strip TLDs: "bloomberg.com" → "bloomberg"
        domain = _TLD_STRIP.sub("", domain)

    if not domain and entry.get("url"):
        domain = source_domain(entry["url"])

    return publisher, domain


def get_real_rank(real_domain: str) -> int:
    """Map an unwrapped domain to a hardcoded reliability rank.

    Elite / VIP   8–10
    Standard      5–7
    Blocked       0
    Unknown       5 (default)
    """
    if not real_domain:
        return 5
    d = real_domain.lower().strip()
    # Domain-level matches
    for dom, score in DOMAIN_RELIABILITY.items():
        if d == dom or d.endswith("." + dom):
            return score
    # Brand-name matches via SOURCE_RELIABILITY keys
    for name, score in SOURCE_RELIABILITY.items():
        if name.lower() == d or name.lower().replace(" ", "") == d:
            return score
    return DEFAULT_RELIABILITY


# -------------------------------------------------------------- prefilter
def guess_category(item: dict[str, Any]) -> str:
    """Keyword scope check — only used when the fetcher didn't pin a category."""
    cat = item.get("category")
    if cat and cat != "general":
        return cat
    blob = f"{item.get('title','')} {item.get('snippet','')}".lower()
    if any(w in blob for w in GEO_STRONG):
        return "geopolitics"
    best, score = "daily_news", 0
    for candidate, words in CATEGORY_KEYWORDS.items():
        hits = sum(1 for w in words if w in blob)
        if hits > score:
            best, score = candidate, hits
    return best if score else "daily_news"


def prefilter_and_cap(items: list[dict[str, Any]], max_total: int = 150) -> list[dict[str, Any]]:
    """Dual-Track Smart Capping — replaces the old blind Top-40 sort.

    Split items into VIP lane (Rank ≥ 8) and Standard lane (Rank < 8).
      • VIPs bypass clickbait checks and fuzzy dedup.  Only exact-URL-hash
        duplicates (within the same batch, and vs DB) are dropped.
      • Standards go through strict clickbait regex and MinHash dedup.
      • Authority dedup: when two items are near-dupes, keep the higher-rank one.
      • VIP slots (max 50) filled first, newest-first.
        Remaining capacity (max 100) filled with Standard, sorted by rank desc
        then newest-first.

    Returns the final capped list, with each item's source rank attached.
    """
    batch_hashes: set[str] = set()
    vip_items: list[dict[str, Any]] = []
    std_items: list[dict[str, Any]] = []

    for item in items:
        h = db.url_hash(item["url"])
        if h in batch_hashes:
            continue
        if db.is_seen(item["url"]):
            continue
        batch_hashes.add(h)

        # Determine source rank
        pub, real_dom = extract_real_publisher(item)
        rank = source_reliability(item.get("source"), item.get("url"))
        item["source_rank"] = rank

        # Blocked sources: drop entirely
        if rank == 0:
            log.info("blocked source dropped: %s | %s", item.get("source"), (item.get("title") or "")[:70])
            db.mark_seen(item, lane="slow", posted=False)
            continue

        if rank >= 8:
            # VIP lane: only exact URL dupe check (already done above)
            item["category"] = guess_category(item)
            vip_items.append(item)
        else:
            # Standard lane: clickbait filter first
            if is_clickbait(item.get("title")):
                log.info("clickbait dropped: %s", (item.get("title") or "")[:70])
                db.mark_seen(item, lane="slow", posted=False)
                continue
            if is_blocked_source(item.get("source"), item.get("url")):
                log.info("blocked source dropped: %s | %s", item.get("source"), (item.get("title") or "")[:70])
                db.mark_seen(item, lane="slow", posted=False)
                continue
            item["category"] = guess_category(item)
            std_items.append(item)

    # Authority dedup on standard lane: MinHash + keep higher rank
    unique_std: list[dict[str, Any]] = []
    for item in std_items:
        tn = item["title_norm"]
        event_id = db.url_hash(item["url"])[:32]
        if not is_duplicate_fast(tn or item["title"], event_id):
            unique_std.append(item)
        else:
            # Find the existing dupe and keep the higher-rank one
            for idx, existing in enumerate(unique_std):
                existing_id = db.url_hash(existing["url"])[:32]
                # Quick title similarity check (MinHash already matched)
                if existing["title"][:30].lower() in item["title"].lower() or \
                   item["title"][:30].lower() in existing["title"].lower():
                    if item.get("source_rank", 5) > existing.get("source_rank", 5):
                        unique_std[idx] = item  # swap in the higher-rank source
                    break
            log.debug("Standard-lane near-dupe handled: %s", item["title"][:70])

    # Sort: VIP newest-first; Standard rank-desc then newest
    vip_items.sort(key=lambda i: i.get("published_at") or "", reverse=True)
    unique_std.sort(key=lambda i: (-i.get("source_rank", 5), i.get("published_at") or ""), reverse=False)
    # fix: negative rank sort means we need descending rank, so -rank ascending

    # Correct sort: high rank first, then newest
    unique_std.sort(key=lambda i: (i.get("source_rank", 5), i.get("published_at") or ""), reverse=True)

    vip_take = min(len(vip_items), 50)
    std_take = min(len(unique_std), max_total - vip_take)

    vip_out = vip_items[:vip_take]
    std_out = unique_std[:std_take]

    vip_out.sort(key=lambda i: i.get("published_at") or "", reverse=True)

    log.info(
        "prefilter_and_cap: %d in → vip:%d(%d) + std:%d(%d) = %d total (cap %d)",
        len(items), len(vip_items), len(vip_out),
        len(std_items), len(std_out),
        len(vip_out) + len(std_out), max_total,
    )
    return vip_out + std_out


# Legacy wrapper — called by slow_lane.run_cycle so it compiles without a change there.
# (slow_lane.py is updated to call prefilter_and_cap directly now.)
def prefilter(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return prefilter_and_cap(items, max_total=150)


# ------------------------------------------------------------------ router
CATEGORY_LABELS = {
    "crypto_news": "🪙 Crypto",
    "geopolitics": "🌍 Geopolitics",
    "us_politics": "🇺🇸 US Politics",
    "india_politics": "🇮🇳 India Politics",
    "us_econ_calendar": "🗓 US Economic Calendar",
    "india_econ_calendar": "🗓 India Economic Calendar",
    "gold_news": "🥇 Gold",
    "us_stock_market": "📈 US Stock Market",
    "daily_news": "📰 Daily News",
    "india_market_ipo": "🇮🇳 India Market / IPO",
    "us_market_ipo": "🇺🇸 US Market / IPO",
    "econ_calendar_alerts": "⏰ Calendar Alert",
    "admin_health": "🛠 Admin / Health",
    "general": "📰 News",
}


def resolve_channel(category: str | None) -> str:
    return config.route_channel(category)


def esc(text: str | None) -> str:
    return html.escape(str(text or ""), quote=False)


def short_url(url: str, limit: int = 34) -> str:
    if not url:
        return ""
    try:
        p = urlparse(url)
        host = p.netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        path = (p.path or "").rstrip("/")
        label = host + path
        if len(label) <= limit:
            return label
        return host + "/…"
    except Exception:
        return url[:limit]


def link(url: str, text: str) -> str:
    return f'<a href="{html.escape(url, quote=True)}">{esc(text)}</a>'


SOURCE_LABELS = {
    "bbc_mideast": "BBC Middle East",
    "bbc_world": "BBC World",
    "dw_world": "DW",
    "france24_world": "France 24",
    "thediplomat": "The Diplomat",
    "reuters_world_gnews": "Reuters",
    "reuters_via_gnews": "Reuters",
    "aljazeera": "Al Jazeera",
    "cnbc_finance": "CNBC",
    "cnbc_markets": "CNBC",
    "marketwatch_top": "MarketWatch",
    "thehill": "The Hill",
    "toi_india": "Times of India",
    "ndtv_india": "NDTV",
    "livemint_markets": "Livemint",
    "bs_markets": "Business Standard",
    "thehindu_business": "The Hindu BusinessLine",
    "et_markets": "Economic Times",
    "et_stocks": "Economic Times",
    "et_ipo": "Economic Times",
    "yahoo_finance": "Yahoo Finance",
    "npr_politics": "NPR",
    "coindesk": "CoinDesk",
    "cointelegraph": "Cointelegraph",
    "decrypt": "Decrypt",
}


def pretty_source(name: str | None) -> str:
    if not name:
        return ""
    key = str(name).strip()
    if key in SOURCE_LABELS:
        return SOURCE_LABELS[key]
    cleaned = re.sub(r"^gnews_", "", key).replace("_", " ").strip()
    return cleaned.title() if cleaned.islower() else cleaned or key


def _norm_name(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().casefold()


SOURCE_RELIABILITY = {
    "Reuters": 9, "Associated Press": 9, "AP": 9, "AFP": 9,
    "BBC World": 9, "BBC Middle East": 9, "BBC": 9,
    "SEC EDGAR": 10, "NSE": 9, "BSE": 9,
    "Bloomberg": 10, "Bloomberg News": 10,
    "The Wall Street Journal": 9, "Wall Street Journal": 9, "WSJ": 9,
    "Financial Times": 9, "FT": 9,
    "Times of India": 8, "The Times of India": 8, "TOI": 8,
    "NDTV": 8,
    "Economic Times": 8, "The Economic Times": 8,
    "Livemint": 8, "Mint": 8,
    "Business Standard": 8,
    "The Hindu BusinessLine": 8, "The Hindu": 8,
    "Al Jazeera": 8,
    "CNBC": 8, "CNBC TV18": 8, "CNBCTV18": 8,
    "NDTV Profit": 8,
    "Forbes": 8, "Forbes India": 8,
    "The Diplomat": 8, "DW": 8, "France 24": 8, "NPR": 8,
    "Hindustan Times": 8, "The Hindustan Times": 8,
    "Indian Express": 8, "The Indian Express": 8,
    "Barron's": 8, "Barrons": 8,
    "New York Times": 8, "The New York Times": 8, "NYT": 8,
    "Washington Post": 8, "The Washington Post": 8,
    "The Telegraph": 8, "The Guardian": 8, "Guardian": 8,
    "MarketWatch": 7, "The Hill": 7,
    "CoinDesk": 7, "Cointelegraph": 7, "Decrypt": 7,
    "Investing.com": 7, "Investing.com India": 7,
    "Yahoo Finance": 6, "TheStreet": 6, "The Street": 6,
}
DEFAULT_RELIABILITY = 5

DOMAIN_RELIABILITY = {
    "reuters.com": 9, "bbc.co.uk": 9, "bbc.com": 9,
    "apnews.com": 9, "afp.com": 9, "sec.gov": 10,
    "bloomberg.com": 10, "wsj.com": 9, "ft.com": 9,
    "nseindia.com": 9, "bseindia.com": 9,
    "timesofindia.indiatimes.com": 8, "toi.in": 8,
    "ndtv.com": 8, "economictimes.indiatimes.com": 8,
    "livemint.com": 8, "business-standard.com": 8,
    "thehindubusinessline.com": 8, "thehindu.com": 8,
    "aljazeera.com": 8, "cnbc.com": 8, "cnbctv18.com": 8,
    "ndtvprofit.com": 8, "forbes.com": 8, "forbesindia.com": 8,
    "thediplomat.com": 8, "dw.com": 8, "france24.com": 8,
    "npr.org": 8, "hindustantimes.com": 8, "indianexpress.com": 8,
    "barrons.com": 8, "nytimes.com": 8, "washingtonpost.com": 8,
    "telegraph.co.uk": 8, "theguardian.com": 8,
    "marketwatch.com": 7, "thehill.com": 7,
    "coindesk.com": 7, "cointelegraph.com": 7, "decrypt.co": 7,
    "investing.com": 7, "finance.yahoo.com": 6, "yahoo.com": 6,
    "thestreet.com": 6,
}

BLOCKED_DOMAINS = {
    "fool.com", "insidermonkey.com", "247wallst.com",
    "247wallstreet.com", "barchart.com", "benzinga.com",
    "seekingalpha.com",
}
BLOCKED_NAMES = {
    "motley fool", "the motley fool", "insider monkey",
    "24/7 wall st.", "24/7 wall st", "247 wall st",
    "barchart", "benzinga", "seeking alpha",
}

_CLICKBAIT = [
    re.compile(r"\b\d+\s+(things|reasons|ways|stocks|cryptos?)\s+to\s+(know|watch|buy|own)", re.I),
    re.compile(r"\byou won'?t believe\b", re.I),
    re.compile(r"\bwhat you need to know\b", re.I),
    re.compile(r"\bthis (one )?stock (could|might|will)\b", re.I),
    re.compile(r"\b(millionaire|get rich|secret)\b", re.I),
    re.compile(r"\b(top|best)\s+\d+\s+(stocks|gains|losers)\b", re.I),
]

# Fast-lane auto-reject patterns — only literal garbage now.
# director_change, analyst_meet, record_date moved to NEEDS_LLM_JUDGMENT.
_FAST_REJECT = [
    re.compile(r"copy of newspaper publication", re.I),
    re.compile(r"newspaper publication", re.I),
    re.compile(r"compliance[- ]certificate", re.I),
    re.compile(r"shareholders['’]? meeting", re.I),
    re.compile(r"con\.?\s*call updates", re.I),
    re.compile(r"outcome of (the )?agm", re.I),
    re.compile(r"updates on annual general meeting", re.I),
]

# These were blind-rejected before; now they go to the LLM with STRICT_MODE.
NEEDS_LLM_JUDGMENT = [
    re.compile(r"change in director", re.I),
    re.compile(r"analysts?/institutional investor meet", re.I),
    re.compile(r"\brecord date\b", re.I),
]

_SEC_BOILER = re.compile(r"^(8-K|S-1|F-1|424B4)\s+filing:", re.I)
_MATERIAL_8K = re.compile(
    r"earnings|results|guidance|merger|acquisit|offering|bankrupt|restatement|"
    r"\bceo\b|resign|dividend|buyback|going concern",
    re.I,
)


def _host_from(source: str | None, url: str | None) -> str:
    host = source_domain(url or "")
    if host:
        return host
    raw = (source or "").strip().lower()
    if "." in raw and " " not in raw:
        return raw[4:] if raw.startswith("www.") else raw
    return ""


def _domain_score(host: str) -> int | None:
    if not host:
        return None
    host = host.lower()
    if host in DOMAIN_RELIABILITY:
        return DOMAIN_RELIABILITY[host]
    for dom, score in DOMAIN_RELIABILITY.items():
        if host.endswith("." + dom):
            return score
    return None


def is_blocked_source(source: str | None, url: str | None = None) -> bool:
    pretty = _norm_name(pretty_source(source))
    raw = _norm_name(source or "")
    if pretty in BLOCKED_NAMES or raw in BLOCKED_NAMES:
        return True
    host = _host_from(source, url)
    if not host:
        return False
    if host in BLOCKED_DOMAINS:
        return True
    return any(host == d or host.endswith("." + d) for d in BLOCKED_DOMAINS)


def is_clickbait(title: str | None) -> bool:
    t = title or ""
    return any(p.search(t) for p in _CLICKBAIT)


_TLD_SUFFIX = re.compile(r"\.(com|net|org|io|co\.uk|com\.au|co\.in|in)$", re.I)
_GOOGLE_HOSTS = {"news.google.com", "google.com", "news.google.co.in"}


def _brand_score(source: str | None) -> int | None:
    cands = {_norm_name(pretty_source(source)), _norm_name(source or "")}
    extra = set()
    for cand in cands:
        stripped = _TLD_SUFFIX.sub("", cand)
        if stripped and stripped != cand:
            extra.add(stripped)
    cands |= extra
    cands.discard("")
    table = [(_norm_name(k), v) for k, v in SOURCE_RELIABILITY.items()]
    best: int | None = None
    best_len = 0
    for kn, score in table:
        if not kn:
            continue
        for cand in cands:
            if cand == kn or cand.startswith(kn + " "):
                if len(kn) > best_len:
                    best, best_len = score, len(kn)
    return best


def source_reliability(source: str | None, url: str | None = None) -> int:
    if is_blocked_source(source, url):
        return 0
    hit = _brand_score(source)
    if hit is not None:
        return hit
    host = _host_from(source, url)
    if host in _GOOGLE_HOSTS:
        host = _host_from(source, None)
    dom = _domain_score(host)
    if dom is not None:
        return dom
    return DEFAULT_RELIABILITY


def rule_fast_decision(item: dict[str, Any]) -> str | None:
    """Return 'reject' for routine market filings; 'needs_llm' for moved-to-LLM types; None = ask LLM."""
    title = item.get("title") or ""
    et = (item.get("event_type") or "").lower()

    # Pure garbage — still auto-reject
    if any(p.search(title) for p in _FAST_REJECT):
        return "reject"

    # Previously blind-rejected — now flagged for LLM STRICT_MODE
    if any(p.search(title) for p in NEEDS_LLM_JUDGMENT):
        return "needs_llm"

    if et == "ipo" and _SEC_BOILER.search(title):
        return "reject"
    if _SEC_BOILER.match(title) and et == "earnings":
        blob = f"{title} {item.get('snippet') or ''}"
        if not _MATERIAL_8K.search(blob):
            return "reject"
    if et in ("bulk_deal", "block_deal"):
        company = (item.get("company") or "").strip()
        if company and db.deal_posted_today(company):
            return "reject"
        try:
            notional = float(item.get("notional") or 0)
        except (TypeError, ValueError):
            notional = 0.0
        if notional and notional < config.DEAL_MIN_NOTIONAL_INR:
            return "reject"
    return None


IST = timezone(timedelta(hours=5, minutes=30))


def format_published(iso: str | None) -> str:
    if not iso:
        return "recent"
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return "recent"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(IST)
    stamp = local.strftime("%d %b %Y, %H:%M IST")
    delta = datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
    secs = int(delta.total_seconds())
    if secs < 0:
        return stamp
    if secs < 3600:
        ago = f"{max(secs // 60, 1)}m ago"
    elif secs < 86400:
        ago = f"{secs // 3600}h ago"
    else:
        ago = f"{secs // 86400}d ago"
    return f"{stamp} · {ago}"


COMPACT_CATEGORIES = {
    c.strip() for c in (config._env("COMPACT_CATEGORIES", "daily_news") or "").split(",") if c.strip()
}


def significance_dot(score: int) -> str:
    return "🔴" if score >= 7 else ("🟠" if score >= 4 else "🟡")


def render_explain(raw: str | None) -> str:
    if not raw:
        return ""
    lines = [l.strip() for l in str(raw).split("\n") if l.strip()]
    return "\n".join(f"▸ {esc(l)}" for l in lines)


def build_message(item: dict[str, Any]) -> str:
    cat = item.get("category", "general")
    label = CATEGORY_LABELS.get(cat, "📰 News")
    src = pretty_source(item.get("source"))
    url = item.get("url") or ""
    title = item.get("title", "")

    if cat in COMPACT_CATEGORIES:
        head = f"<b>{esc(label)}</b>" + (f" · <i>{esc(src)}</i>" if src else "")
        parts = [head, "", f"<b>{link(url, title)}</b>" if url else f"<b>{esc(title)}</b>"]
        explain = render_explain(item.get("explain"))
        if explain:
            parts += ["", explain]
        elif item.get("snippet"):
            parts += ["", esc(make_snippet(item["snippet"]))]
        if url:
            parts += ["", f"🔗 {link(url, 'Read more')} · <i>{esc(pretty_source(item.get('source')))}</i>"]
        return "\n".join(parts).strip()

    sig = int(item.get("significance") or item.get("importance") or 5)
    sig = max(1, min(10, sig))
    rel = source_reliability(item.get("source"), item.get("url"))

    lines = [f"{significance_dot(sig)} <b>Significance: {sig}/10</b> · {esc(label)}", ""]
    lines.append(f"<b>{link(url, title)}</b>" if url else f"<b>{esc(title)}</b>")
    lines.append("")
    meta = f"📰 <b>{esc(src or 'Unknown')}</b> · ⭐️ Reliability {rel}/10"
    lines.append(meta)
    lines.append(f"📅 {esc(format_published(item.get('published_at')))}")

    summary = clean_text(item.get("summary_ai") or "")
    if summary:
        lines += ["", "📋 <b>What happened</b>", esc(summary)]
    elif item.get("explain"):
        lines += ["", "📋 <b>What happened</b>", render_explain(item.get("explain"))]
    elif item.get("snippet"):
        lines += ["", "📋 <b>What happened</b>", esc(make_snippet(item["snippet"]))]

    why = clean_text(item.get("why_matters") or "")
    if why:
        lines += ["", "❗ <b>Why it matters</b>", esc(why)]

    watch = clean_text(item.get("watch_next") or "")
    if watch:
        lines += ["", "👀 <b>Watch next</b>", esc(watch)]

    if url:
        lines += ["", f"🔗 {link(url, 'Read more')} · <i>{esc(pretty_source(item.get('source')))}</i>"]
    return "\n".join(lines).strip()


def build_market_message(item: dict[str, Any], verdict: dict[str, Any]) -> str:
    cat = verdict.get("category", "general")
    label = CATEGORY_LABELS.get(cat, "📊 Market")
    src = pretty_source(item.get("source"))
    url = item.get("url") or ""
    title = item.get("title", "")

    sig = int(verdict.get("significance") or verdict.get("importance") or 5)
    sig = max(1, min(10, sig))
    rel = source_reliability(item.get("source"), item.get("url"))

    lines = [f"{significance_dot(sig)} <b>Significance: {sig}/10</b> · {esc(label)}", ""]
    if item.get("event_type"):
        lines.append(f"<code>{esc(item['event_type'])}</code>")
    lines.append(f"<b>{link(url, title)}</b>" if url else f"<b>{esc(title)}</b>")
    lines.append("")
    lines.append(f"📰 <b>{esc(src or 'Unknown')}</b> · ⭐️ Reliability {rel}/10")
    lines.append(f"📅 {esc(format_published(item.get('published_at')))}")

    summary = clean_text(verdict.get("summary_ai") or "")
    if summary:
        lines += ["", "📋 <b>What happened</b>", esc(summary)]
    elif verdict.get("explain"):
        lines += ["", "📋 <b>What happened</b>", render_explain(verdict.get("explain"))]
    elif item.get("snippet"):
        lines += ["", "📋 <b>What happened</b>", esc(make_snippet(item["snippet"]))]

    why = clean_text(verdict.get("why_matters") or "")
    if why:
        lines += ["", "❗ <b>Why it matters</b>", esc(why)]

    watch = clean_text(verdict.get("watch_next") or "")
    if watch:
        lines += ["", "👀 <b>Watch next</b>", esc(watch)]

    if url:
        lines += ["", f"🔗 {link(url, 'Open filing')} · <i>{esc(pretty_source(item.get('source')))}</i>"]
    return "\n".join(lines).strip()


def build_review_message(item: dict[str, Any], verdict: dict[str, Any]) -> str:
    url = item.get("url") or ""
    title = item.get("title", "")
    lines = [
        "🟣 <b>NEEDS REVIEW</b> <i>(not posted publicly)</i>",
        "",
        f"<b>category:</b> {esc(verdict.get('category'))}",
        f"<b>event_type:</b> {esc(item.get('event_type') or '-')}",
        f"<b>importance:</b> {esc(verdict.get('importance'))}",
        f"<b>reason:</b> {esc(verdict.get('reason'))}",
        f"<b>source:</b> {esc(pretty_source(item.get('source')))}",
        "",
        f"<b>{link(url, title)}</b>" if url else f"<b>{esc(title)}</b>",
    ]
    if url:
        lines += ["", f"🔗 {link(url, 'Review source')} · <i>{esc(pretty_source(item.get('source')))}</i>"]
    return "\n".join(lines).strip()