#!/usr/bin/env python3
"""Build the Sift Media deep system report PDF from live code facts."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape as html_escape
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String

OUT = Path("/root/aslam-news-media/docs/Sift-Media-System-Report.pdf")
IST = timezone(timedelta(hours=5, minutes=30))

INK = colors.HexColor("#1a1410")
GOLD = colors.HexColor("#c4a574")
CREAM = colors.HexColor("#f4ead8")
RULE = colors.HexColor("#c4a574")
MUTED = colors.HexColor("#5c5348")
ROW = colors.HexColor("#fbf7f0")
BOX = colors.HexColor("#2b241c")


def styles():
    base = getSampleStyleSheet()
    s = {
        "cover_kicker": ParagraphStyle(
            "cover_kicker", parent=base["Normal"], fontName="Times-Italic",
            fontSize=11, textColor=GOLD, alignment=TA_CENTER, spaceAfter=8,
        ),
        "cover_title": ParagraphStyle(
            "cover_title", parent=base["Normal"], fontName="Times-Bold",
            fontSize=28, leading=34, textColor=INK, alignment=TA_CENTER, spaceAfter=8,
        ),
        "cover_sub": ParagraphStyle(
            "cover_sub", parent=base["Normal"], fontName="Times-Italic",
            fontSize=12, textColor=MUTED, alignment=TA_CENTER, spaceAfter=6,
        ),
        "h1": ParagraphStyle(
            "h1", parent=base["Heading1"], fontName="Times-Bold",
            fontSize=16, leading=20, textColor=INK, spaceBefore=14, spaceAfter=8,
            borderPadding=0,
        ),
        "h2": ParagraphStyle(
            "h2", parent=base["Heading2"], fontName="Times-Bold",
            fontSize=12.5, leading=16, textColor=INK, spaceBefore=10, spaceAfter=5,
        ),
        "body": ParagraphStyle(
            "body", parent=base["Normal"], fontName="Times-Roman",
            fontSize=9.5, leading=13, textColor=INK, alignment=TA_JUSTIFY, spaceAfter=6,
        ),
        "note": ParagraphStyle(
            "note", parent=base["Normal"], fontName="Times-Italic",
            fontSize=9, leading=12, textColor=MUTED, spaceAfter=8,
        ),
        "cell": ParagraphStyle(
            "cell", parent=base["Normal"], fontName="Times-Roman",
            fontSize=8, leading=10.5, textColor=INK,
        ),
        "cellb": ParagraphStyle(
            "cellb", parent=base["Normal"], fontName="Times-Bold",
            fontSize=8, leading=10.5, textColor=INK,
        ),
        "mono": ParagraphStyle(
            "mono", parent=base["Code"], fontName="Courier",
            fontSize=7.5, leading=10, textColor=INK, backColor=ROW,
            leftIndent=4, rightIndent=4, spaceBefore=4, spaceAfter=8,
        ),
        "foot": ParagraphStyle(
            "foot", parent=base["Normal"], fontName="Times-Italic",
            fontSize=8, textColor=MUTED, alignment=TA_CENTER,
        ),
        "caption": ParagraphStyle(
            "caption", parent=base["Normal"], fontName="Times-Italic",
            fontSize=8, textColor=MUTED, alignment=TA_CENTER, spaceBefore=2, spaceAfter=10,
        ),
    }
    return s


def P(text, st, key="body"):
    return Paragraph(text, st[key])


def bullets(items, st):
    return ListFlowable(
        [ListItem(Paragraph(x, st["body"]), leftIndent=12, bulletColor=INK) for x in items],
        bulletType="bullet",
        start="•",
        leftIndent=16,
        bulletFontName="Times-Bold",
        bulletFontSize=9,
        spaceAfter=6,
    )


def table(headers, rows, st, col_widths=None):
    head = [Paragraph(h, st["cellb"]) for h in headers]
    body = [[Paragraph(html_escape(str(c)), st["cell"]) for c in r] for r in rows]
    data = [head] + body
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), GOLD),
        ("TEXTCOLOR", (0, 0), (-1, 0), INK),
        ("BACKGROUND", (0, 1), (-1, -1), ROW),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [ROW, colors.white]),
        ("GRID", (0, 0), (-1, -1), 0.3, GOLD),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def _box(d, x, y, w, h, label, sub=""):
    d.add(Rect(x, y, w, h, rx=4, ry=4, fillColor=CREAM, strokeColor=GOLD, strokeWidth=1.2))
    d.add(String(x + w / 2, y + h / 2 + (4 if sub else 0), label,
                 fontName="Times-Bold", fontSize=8, fillColor=INK, textAnchor="middle"))
    if sub:
        d.add(String(x + w / 2, y + h / 2 - 10, sub,
                     fontName="Times-Italic", fontSize=6.5, fillColor=MUTED, textAnchor="middle"))


def _arrow(d, x1, y1, x2, y2):
    d.add(Line(x1, y1, x2, y2, strokeColor=INK, strokeWidth=1))
    # small head
    d.add(Polygon([x2, y2, x2 - 6, y2 + 3.5, x2 - 6, y2 - 3.5],
                  fillColor=INK, strokeColor=INK, strokeWidth=0.2))


def diagram_system() -> Drawing:
    """End-to-end factory. Coordinates in points, origin bottom-left."""
    W, H = 500, 210
    d = Drawing(W, H)
    d.add(Rect(0, 0, W, H, fillColor=colors.HexColor("#0b0907"), strokeColor=GOLD, strokeWidth=0.8))
    d.add(String(12, 190, "FIG 1  —  Sift Media factory (source to Telegram)",
                 fontName="Times-Bold", fontSize=9, fillColor=GOLD))

    # row 1 sources
    srcs = [
        (12, 130, 90, 42, "RSS feeds", "English newsrooms"),
        (112, 130, 90, 42, "Google News", "keyword RSS"),
        (212, 130, 90, 42, "NSE / BSE", "India filings"),
        (312, 130, 90, 42, "SEC EDGAR", "8-K / S-1 / 424B4"),
        (412, 130, 76, 42, "Calendar", "hardcoded 2026"),
    ]
    for args in srcs:
        _box(d, *args)

    _box(d, 70, 70, 110, 40, "Slow lane", "every 20 min")
    _box(d, 210, 70, 110, 40, "Fast lane", "every 3 min")
    _box(d, 350, 70, 110, 40, "Calendar / digest", "daily jobs")

    _box(d, 30, 14, 130, 40, "SQLite book", "aslam_news.db")
    _box(d, 185, 14, 130, 40, "Send queue", "only path out")
    _box(d, 340, 14, 140, 40, "Telegram + Desk", "cards + newspaper")

    for x in (57, 157, 257, 357):
        d.add(Line(x + 20, 130, 125 if x < 200 else 265 if x < 300 else 405, 110,
                   strokeColor=GOLD, strokeWidth=0.7))
    d.add(Line(125, 70, 95, 54, strokeColor=GOLD, strokeWidth=0.8))
    d.add(Line(265, 70, 250, 54, strokeColor=GOLD, strokeWidth=0.8))
    d.add(Line(405, 70, 410, 54, strokeColor=GOLD, strokeWidth=0.8))
    d.add(Line(160, 34, 185, 34, strokeColor=GOLD, strokeWidth=1))
    d.add(Line(315, 34, 340, 34, strokeColor=GOLD, strokeWidth=1))
    return d


def diagram_gates() -> Drawing:
    W, H = 500, 230
    d = Drawing(W, H)
    d.add(Rect(0, 0, W, H, fillColor=colors.HexColor("#0b0907"), strokeColor=GOLD, strokeWidth=0.8))
    d.add(String(12, 210, "FIG 2  —  Filter gates (most items die here)",
                 fontName="Times-Bold", fontSize=9, fillColor=GOLD))
    steps = [
        (20, 150, 90, 46, "1. Fetch", "raw items"),
        (130, 150, 90, 46, "2. Clean", "title+snippet"),
        (240, 150, 90, 46, "3. Hard junk", "block / dup"),
        (350, 150, 130, 46, "4. AI editor", "keep if sig >= 6"),
        (70, 70, 110, 46, "5. Rank source", "Bloomberg=10"),
        (200, 70, 110, 46, "6. 8/hour cap", "per Telegram chat"),
        (330, 70, 140, 46, "7. Queue + send", "HTML card"),
        (160, 12, 180, 40, "8. Write the book", "desk + PDF read this"),
    ]
    for args in steps:
        _box(d, *args)
    for x1, x2 in ((110, 130), (220, 240), (330, 350)):
        d.add(Line(x1, 173, x2, 173, strokeColor=GOLD, strokeWidth=1))
    d.add(Line(415, 150, 415, 116, strokeColor=GOLD, strokeWidth=1))
    d.add(Line(415, 93, 340, 93, strokeColor=GOLD, strokeWidth=1))
    d.add(Line(200, 93, 180, 93, strokeColor=GOLD, strokeWidth=1))
    d.add(Line(125, 70, 125, 52, strokeColor=GOLD, strokeWidth=1))
    d.add(Line(125, 32, 160, 32, strokeColor=GOLD, strokeWidth=1))
    return d


def diagram_surfaces() -> Drawing:
    W, H = 500, 160
    d = Drawing(W, H)
    d.add(Rect(0, 0, W, H, fillColor=colors.HexColor("#0b0907"), strokeColor=GOLD, strokeWidth=0.8))
    d.add(String(12, 140, "FIG 3  —  Three surfaces, one book",
                 fontName="Times-Bold", fontSize=9, fillColor=GOLD))
    _box(d, 185, 78, 130, 48, "aslam_news.db", "read/write: bot only")
    _box(d, 20, 18, 140, 48, "Telegram cards", "the publisher")
    _box(d, 180, 18, 140, 48, "Live Desk :8780", "newspaper, read-only")
    _box(d, 340, 18, 140, 48, "PDF digest", "AM + PM, no extra AI")
    d.add(Line(250, 78, 90, 66, strokeColor=GOLD, strokeWidth=1))
    d.add(Line(250, 78, 250, 66, strokeColor=GOLD, strokeWidth=1))
    d.add(Line(250, 78, 410, 66, strokeColor=GOLD, strokeWidth=1))
    return d


def header_footer(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(INK)
    canvas.rect(0, A4[1] - 14 * mm, A4[0], 14 * mm, fill=1, stroke=0)
    canvas.setFillColor(GOLD)
    canvas.setFont("Times-Italic", 9)
    canvas.drawString(16 * mm, A4[1] - 9 * mm, "Sift Media  ·  System report")
    canvas.setFont("Times-Roman", 8)
    canvas.drawRightString(A4[0] - 16 * mm, A4[1] - 9 * mm, "Internal  ·  13 Sep 2026")
    canvas.setFillColor(GOLD)
    canvas.rect(0, 0, A4[0], 12 * mm, fill=1, stroke=0)
    canvas.setFillColor(INK)
    canvas.setFont("Times-Italic", 8)
    canvas.drawString(16 * mm, 4.5 * mm, "From live code on this VPS  ·  no secrets")
    canvas.drawRightString(A4[0] - 16 * mm, 4.5 * mm, f"Page {doc.page}")
    canvas.restoreState()


def build():
    st = styles()
    now = datetime.now(IST).strftime("%A, %d %B %Y, %I:%M %p IST")
    story = []

    story.append(Spacer(1, 28 * mm))
    story.append(P("SIFT MEDIA", st, "cover_kicker"))
    story.append(P("Full system report", st, "cover_title"))
    story.append(P("Every source, every gate, every language, every end message.", st, "cover_sub"))
    story.append(P(f"Drawn from live code on 13 September 2026. Printed {now}.", st, "cover_sub"))
    story.append(Spacer(1, 8 * mm))
    story.append(P(
        "Public brand is <b>Sift Media</b>. Folders on disk are still "
        "<font face='Courier'>/root/aslam-news-media</font> (bot) and "
        "<font face='Courier'>/root/aslam-media-desk</font> (website). "
        "Telegram channel names and the BotFather username were not renamed.",
        st, "body",
    ))
    story.append(PageBreak())

    # 1
    story.append(P("1. What this project is", st, "h1"))
    story.append(P(
        "Sift Media is a news factory, not a newsroom with writers. A Python service "
        "pulls public feeds, throws most items away, asks a language model to keep only "
        "serious stories, then posts a short English card to Telegram. A website called "
        "Live Desk is a newspaper view of the same book. It never fetches news itself.",
        st, "body",
    ))
    story.append(P(
        "There is one running bot (single-bot mode: BOT2_TOKEN is empty). systemd unit "
        "<font face='Courier'>aslam-news-media.service</font> keeps it alive. The desk "
        "is a separate process on port 8780, not systemd.",
        st, "body",
    ))
    story.append(diagram_system())
    story.append(P("Sources at the top. Two lanes in the middle. One book, one send queue, two public faces at the bottom.", st, "caption"))

    story.append(P("2. Runtime and stack", st, "h1"))
    story.append(P("Where it lives", st, "h2"))
    story.append(table(
        ["Piece", "Fact"],
        [
            ["VPS", "CloudOnFire KVM vpsaevm9ub75 · public IP 160.250.224.8 · Linux"],
            ["Bot code", "/root/aslam-news-media  ·  Python 3 venv"],
            ["Website", "/root/aslam-media-desk  ·  port 8780"],
            ["Database", "/root/aslam-news-media/data/aslam_news.db  (SQLite)"],
            ["Logs", "/root/aslam-news-media/data/bot.log"],
            ["Service", "aslam-news-media.service  (enabled, restart=always)"],
            ["Desk HTTPS", "Cloudflare quick tunnel (hostname dies on reboot)"],
            ["Desk HTTP", "http://160.250.224.8:8780"],
        ],
        st, [40 * mm, 130 * mm],
    ))
    story.append(Spacer(1, 3 * mm))
    story.append(P("Languages of the system (this is the language section)", st, "h2"))
    story.append(P(
        "People language and machine language are different. Both are English-first.",
        st, "body",
    ))
    story.append(table(
        ["Layer", "Language / format"],
        [
            ["Human output (Telegram, desk, PDF)", "English only. No Hindi/Tamil generation in this bot."],
            ["RSS / Google News", "English publishers. Google queries use hl=en-US, gl=US, ceid=US:en."],
            ["HTTP headers to NSE/BSE", "Accept-Language: en-US,en;q=0.9"],
            ["Calendar times", "US prints shown as ET + IST. IST in AM/PM for chat answers."],
            ["Live Desk clock", "IST, 12-hour style on the API clock string."],
            ["Code", "Python 3. Libraries: aiogram 3, APScheduler, feedparser, rapidfuzz, openai SDK, httpx, requests, nsepython (present), reportlab, pymupdf."],
            ["Telegram markup", "HTML parse mode. Title is the hyperlink. Link preview off."],
            ["LLM JSON", "English keys: keep, decision, summary_ai, why_matters, watch_next, significance."],
            ["Not this project", "The IG Tamil/Hinglish translator bot is a different repo (/root/ig-tamil-bot)."],
        ],
        st, [48 * mm, 122 * mm],
    ))
    story.append(Spacer(1, 3 * mm))
    story.append(P("Python packages (requirements.txt)", st, "h2"))
    story.append(Preformatted(
        "aiogram>=3.13     feedparser>=6.0    APScheduler>=3.10\n"
        "rapidfuzz>=3.9    openai>=1.40       httpx>=0.27\n"
        "requests>=2.31    python-dotenv      nsepython>=0.1.9\n"
        "reportlab>=4.0    pymupdf>=1.24",
        st["mono"],
    ))

    story.append(P("3. The clock: what runs, how often", st, "h1"))
    story.append(table(
        ["Job", "When (UTC)", "IST", "What it does"],
        [
            ["slow_lane", "every 20 min", "—", "RSS + Google News → news cards"],
            ["fast_lane", "every 3 min", "—", "NSE + BSE + SEC → market alerts"],
            ["health", "every 60 min", "—", "If a fetcher goes stale, ping admin"],
            ["calendar", "08:00 UTC", "13:30 IST", "T-3 explainers + TODAY pings"],
            ["digest_morning", "02:30 UTC", "08:00 IST", "PDF of last ~14h kept stories"],
            ["digest_evening", "13:30 UTC", "19:00 IST", "PDF of last ~11h kept stories"],
            ["boot kick", "on start", "—", "One slow + one fast cycle immediately"],
        ],
        st, [32 * mm, 32 * mm, 28 * mm, 78 * mm],
    ))
    story.append(P(
        "APScheduler is AsyncIOScheduler. Jobs are coroutine functions (not lambdas). "
        "A crash in one job cannot kill the scheduler. Both lanes start once at boot "
        "so you do not wait a full interval after a restart.",
        st, "body",
    ))

    story.append(P("4. Every source we use", st, "h1"))
    story.append(P(
        "No paid data vendor. No GDELT, Moneycontrol, or Finnhub (those flags stay off "
        "until you say GO). Full article text is never stored. Only title, a short snippet "
        "(cap 280 characters), source, category, and link.",
        st, "body",
    ))

    story.append(P("4.1 Slow lane — RSS newsrooms (English)", st, "h2"))
    story.append(table(
        ["Internal name", "Publisher", "Category", "Feed"],
        [
            ["coindesk", "CoinDesk", "crypto_news", "coindesk.com RSS"],
            ["cointelegraph", "Cointelegraph", "crypto_news", "cointelegraph.com/rss"],
            ["decrypt", "Decrypt", "crypto_news", "decrypt.co/feed"],
            ["cnbc_markets", "CNBC", "us_stock_market", "CNBC markets RSS"],
            ["cnbc_finance", "CNBC", "us_stock_market", "CNBC finance RSS"],
            ["marketwatch_top", "MarketWatch", "us_stock_market", "Dow Jones MW top stories"],
            ["yahoo_finance", "Yahoo Finance", "us_stock_market", "finance.yahoo.com RSS"],
            ["npr_politics", "NPR", "us_politics", "NPR politics RSS"],
            ["thehill", "The Hill", "us_politics", "thehill.com feed"],
            ["et_markets", "Economic Times", "india_market_ipo", "ET markets RSS"],
            ["et_stocks", "Economic Times", "india_market_ipo", "ET stocks RSS"],
            ["et_ipo", "Economic Times", "india_market_ipo", "ET IPO RSS"],
            ["livemint_markets", "Livemint", "india_market_ipo", "livemint.com/rss/markets"],
            ["bs_markets", "Business Standard", "india_market_ipo", "BS markets RSS"],
            ["thehindu_business", "The Hindu", "india_market_ipo", "thehindu.com business RSS"],
            ["ndtv_india", "NDTV", "india_politics", "NDTV India feed"],
            ["toi_india", "Times of India", "india_politics", "TOI India RSS"],
            ["bbc_mideast", "BBC", "geopolitics", "BBC Middle East RSS"],
            ["dw_world", "DW", "geopolitics", "DW world RDF"],
            ["france24_world", "France 24", "geopolitics", "france24.com/en/rss"],
            ["thediplomat", "The Diplomat", "geopolitics", "thediplomat.com/feed"],
            ["reuters_world_gnews", "Reuters via GNews", "geopolitics", "Google News site:reuters.com world"],
            ["bbc_world", "BBC", "daily_news", "BBC world RSS"],
            ["aljazeera", "Al Jazeera", "daily_news", "aljazeera.com RSS"],
            ["reuters_via_gnews", "Reuters via GNews", "daily_news", "Google News site:reuters.com 24h"],
        ],
        st, [38 * mm, 38 * mm, 38 * mm, 56 * mm],
    ))
    story.append(P(
        "Business Standard is known to serve broken XML to custom User-Agents and valid XML "
        "to a browser UA. The RSS fetcher uses a browser UA and retries the other UA if the feed is empty.",
        st, "note",
    ))

    story.append(P("4.2 Slow lane — Google News keyword searches (unofficial, English)", st, "h2"))
    story.append(P(
        "These are news.google.com/rss/search URLs. Unofficial, redirect-encoded links, "
        "about 100 items cap. Links are unwrapped when possible.",
        st, "body",
    ))
    story.append(table(
        ["Name", "Query (when:1d unless noted)", "Category"],
        [
            ["gnews_gold", "gold price OR bullion OR XAU", "gold_news"],
            ["gnews_crypto", "bitcoin OR ethereum OR crypto regulation", "crypto_news"],
            ["gnews_us_ipo", "IPO pricing OR IPO debut OR S-1 filing", "us_market_ipo"],
            ["gnews_india_ipo", "India IPO OR SME IPO OR NSE listing", "india_market_ipo"],
            ["gnews_fed", "Federal Reserve OR FOMC OR US inflation CPI", "us_econ_calendar"],
            ["gnews_rbi", "RBI policy OR India CPI OR India GDP", "india_econ_calendar"],
            ["gnews_us_pol", "US Congress OR White House policy", "us_politics"],
            ["gnews_in_pol", "India government policy OR Parliament session", "india_politics"],
            ["gnews_geo_conflict", "ceasefire OR airstrike OR military offensive OR border clash", "geopolitics"],
            ["gnews_geo_diplomacy", "sanctions OR peace talks OR NATO OR UN Security Council", "geopolitics"],
            ["gnews_geo_asia", "Taiwan OR South China Sea OR North Korea tensions", "geopolitics"],
            ["gnews_geo_india", "India Pakistan OR India China border OR India foreign policy", "geopolitics"],
        ],
        st, [40 * mm, 95 * mm, 35 * mm],
    ))

    story.append(P("4.3 Fast lane — NSE India (keyless, cookie session)", st, "h2"))
    story.append(P(
        "Host https://www.nseindia.com/api/... Browser User-Agent, cookie bootstrap every 4 minutes, "
        "1 second sleep between feeds. Shareholding-pattern JSON was removed by NSE (404s) so it is "
        "not scheduled. Shareholding still can appear inside announcements.",
        st, "body",
    ))
    story.append(table(
        ["Fetcher", "What", "Event type"],
        [
            ["nse_deals", "Bulk + block deals", "bulk_deal / block_deal"],
            ["nse_announcements", "Corporate announcements", "announcement"],
            ["nse_ipo", "Mainboard + SME IPO", "ipo"],
            ["nse_corp_actions", "Dividend / bonus / split etc.", "corp_action"],
            ["nse_results", "Quarterly financial results (top 50)", "earnings"],
            ["nse_fii_dii", "FII/DII daily rupee flows", "fii_dii"],
            ["nse_circulars", "Exchange circulars (top 40)", "circular"],
        ],
        st, [40 * mm, 80 * mm, 50 * mm],
    ))

    story.append(P("4.4 Fast lane — BSE India (secondary, keyless)", st, "h2"))
    story.append(P(
        "api.bseindia.com with browser Referer/Origin. Shape is messy (object, double-encoded JSON, "
        "or 'No Record Found!'). Empty on weekends is treated as success so health does not false-alarm. "
        "Used as a cross-check; NSE is primary. Dedup later via event_key + fuzzy title.",
        st, "body",
    ))

    story.append(P("4.5 Fast lane — US SEC EDGAR (official, free)", st, "h2"))
    story.append(table(
        ["Form", "Category", "Event", "Endpoint"],
        [
            ["8-K", "us_stock_market", "earnings / material event", "sec.gov browse-edgar atom, 40 latest"],
            ["S-1", "us_market_ipo", "ipo", "same, type=S-1"],
            ["424B4", "us_market_ipo", "ipo (priced)", "same, type=424B4"],
        ],
        st, [28 * mm, 40 * mm, 48 * mm, 54 * mm],
    ))
    story.append(P(
        "Needs a descriptive User-Agent (config SEC_EDGAR_UA). Throttled under the public 10 req/s limit. "
        "Empty boilerplate S-1 titles and empty 8-Ks are rule-rejected later.",
        st, "note",
    ))

    story.append(P("4.6 Economic calendar (not a live scrape)", st, "h2"))
    story.append(P(
        "Dates are a hardcoded 2026 catalog in fetch/econ_calendar.py (BEA, BLS, FOMC, MoSPI, RBI). "
        "It is not a live BLS pull. Horizon is rolling 30 days. Same-day prints are included. "
        "GDP and PCE at the same timestamp are two rows. Empty event_type rows are skipped "
        "(old dummy rows used to fire false TODAY pings).",
        st, "body",
    ))
    story.append(P(
        "US series in the catalog include NFP, CPI, PPI, PCE, GDP estimates, retail sales, "
        "ISM PMI, JOLTS, FOMC, Labor Day. India: CPI, IIP, GDP, RBI MPC. "
        "T-3 = full card (why it matters + markets / gold / BTC mechanism, no invented numbers, "
        "no buy/sell). TODAY = short ping if importance >= 4. "
        "Chat answers after a print should show Actual vs Forecast vs Previous when known; "
        "if Investing/TE/FF show a blank Actual, the bot must say blank, not invent a tweet.",
        st, "body",
    ))

    story.append(P("4.7 Explicitly NOT used", st, "h2"))
    story.append(bullets([
        "GDELT, Moneycontrol, Chittorgarh, Finnhub (ENABLE_* flags default 0).",
        "Paid FMP / FRED.",
        "Full-page scrapes or article body storage.",
        "X/Twitter guest-token tricks (out of scope for this bot).",
        "Varaha desk on :8766 (equity picks, different product).",
    ], st))

    story.append(P("5. Path of one news item (slow lane)", st, "h1"))
    story.append(diagram_gates())
    story.append(P("A typical slow cycle pulls 1,000+ raw items and may queue under 10 cards.", st, "caption"))
    story.append(bullets([
        "rss.fetch_all_rss() hits every RSS + Google News query (fail-soft per feed).",
        "pipeline.normalize: strip HTML, canonical URL, 280-char snippet, keyword category guess. Geopolitics strong-words (airstrike, sanctions, NATO…) beat 'Trump' so a strike on another country is not US domestic politics.",
        "pipeline.prefilter: URL hash already in seen_urls → drop. rapidfuzz title near-duplicate in 72h window → drop. Blocked domains/names → drop and mark seen. Clickbait regex → drop.",
        "Newest first, then cap MAX_ITEMS_PER_CYCLE (40) before the AI.",
        "smart_filter.filter_batch in groups of 10. JSON keep/drop + summary_ai / why_matters / watch_next / significance.",
        "Keep only if the model says keep AND significance >= 6 (config POST_MIN_SIGNIFICANCE). Note: the prompt text still says 5 in one place; the code floor is 6.",
        "db.mark_seen BEFORE send. If this chat already posted 8 times in the last hour, skip.",
        "send_queue.enqueue HTML card. db.mark_posted after enqueue. Rejects also mark_seen so they are never re-judged.",
    ], st))

    story.append(P("6. Path of one market item (fast lane)", st, "h1"))
    story.append(bullets([
        "collect() runs SEC, then NSE, then BSE. One crash cannot stop the others.",
        "Collapse bulk/block: one card per symbol per cycle, largest notional. Skip under ₹10 crore.",
        "Rule-reject without AI: newspaper publication, director change, empty analyst meet, Record Date, empty S-1/8-K boilerplate. Keep dividends, bonus, buyback, order wins, earnings-with-numbers, priced NSE IPOs, material 8-K.",
        "Each remaining item is its own LLM call (3-way: keep / reject / review). Workers capped (FAST_FILTER_WORKERS, env 2).",
        "keep → public category channel after chat cap. review → admin chat, separate cap 4/hour. reject → silent, still recorded.",
        "posted_at on market rows is set only after a real send (otherwise the cap lies).",
    ], st))

    story.append(P("If the LLM is down", st, "h2"))
    story.append(P(
        "Slow lane fail-soft: keep-wire-only (reliability >= 8), not keep-all. "
        "Fast lane fail-soft: review, never auto-post an unjudged market item. "
        "systemd 'active' is not proof the editor is alive. Check the tail of bot.log.",
        st, "body",
    ))

    story.append(P("7. Source ranks (hardcoded, never the LLM)", st, "h1"))
    story.append(P(
        "Missing brand = 5. That 5 is a hole, not a grade. Google News 'Bloomberg.com' plus a "
        "news.google.com URL must still score 10 (TLD strip + ignore Google as host).",
        st, "body",
    ))
    story.append(table(
        ["Score", "Who"],
        [
            ["10", "Bloomberg, SEC EDGAR"],
            ["9", "Reuters, AP, AFP, BBC, WSJ, FT, NSE, BSE"],
            ["8", "TOI, NDTV / NDTV Profit, ET, Livemint, BS, Hindu, HT, Indian Express, CNBC / TV18, Forbes / Forbes India, Barron's, NYT, WaPo, Telegraph, Guardian, Al Jazeera, DW, France 24, NPR, Diplomat"],
            ["7", "MarketWatch, The Hill, CoinDesk, Cointelegraph, Decrypt, Investing.com / Investing.com India"],
            ["6", "Yahoo Finance, TheStreet"],
            ["5", "Anything not in the table (even a famous masthead we forgot)"],
            ["0 block", "Motley Fool, Insider Monkey, 24/7 Wall St, Barchart, Benzinga, Seeking Alpha"],
        ],
        st, [28 * mm, 142 * mm],
    ))

    story.append(P("8. The end message (what Telegram actually shows)", st, "h1"))
    story.append(P(
        "The publisher's sentences are not copied. The body is the model's own words "
        "(or a short snippet fallback). The title is the hyperlink. Raw URLs are hidden.",
        st, "body",
    ))
    story.append(P("Full card (most categories)", st, "h2"))
    story.append(Preformatted(
        "● Significance: 7/10  ·  🥇 Gold\n"
        "\n"
        "Gold pushed to a fresh record as traders priced in a rate cut   ← title = link\n"
        "\n"
        "📰 Reuters  ·  ⭐️ Reliability 9/10\n"
        "📅 13 Sep 2026, 04:10 PM IST\n"
        "\n"
        "📋 What happened\n"
        "…summary_ai, 1–3 sentences, own words…\n"
        "\n"
        "❗ Why it matters\n"
        "…mechanism, not vibes. No buy/sell…\n"
        "\n"
        "👀 Watch next\n"
        "…one concrete next print or event…\n"
        "\n"
        "🔗 Read more  ·  reuters.com",
        st["mono"],
    ))
    story.append(P(
        "Daily News uses a compact card (that channel used to drown in volume). "
        "Market cards add an event_type line (bulk_deal, ipo, 8-K). "
        "If Telegram rejects the HTML, the queue strips tags and resends plain text once.",
        st, "body",
    ))
    story.append(P("Telegram category labels", st, "h2"))
    story.append(table(
        ["DB category", "Label on the card", "Typical channel"],
        [
            ["crypto_news", "crypto", "CHANNEL_CRYPTO_NEWS"],
            ["geopolitics", "geopolitics", "CHANNEL_GEOPOLITICS"],
            ["gold_news", "Gold", "CHANNEL_GOLD_NEWS"],
            ["india_politics", "India Politics", "CHANNEL_INDIA_POLITICS"],
            ["us_politics", "US Politics", "CHANNEL_US_POLITICS"],
            ["india_market_ipo", "India Market / IPO", "CHANNEL_INDIA_MARKET_IPO"],
            ["us_stock_market", "US Stock Market", "CHANNEL_US_STOCK_MARKET"],
            ["us_market_ipo", "US Market / IPO", "falls back to default if unset"],
            ["india_econ_calendar", "India Economic Calendar", "CHANNEL_INDIA_ECON_CALENDAR"],
            ["us_econ_calendar", "US Economic Calendar", "CHANNEL_US_ECON_CALENDAR"],
            ["daily_news", "Daily News", "default / SLOW_LANE_CHANNEL_ID"],
            ["admin_health", "Admin / Health", "ADMIN_DM_ID"],
        ],
        st, [48 * mm, 52 * mm, 70 * mm],
    ))
    story.append(P(
        "Unset categories fall back to SLOW_LANE_CHANNEL_ID. Review + health go to ADMIN_DM_ID. "
        "Channel numeric IDs live in .env (not printed here).",
        st, "note",
    ))

    story.append(P("Send queue limits (the only path to Telegram)", st, "h2"))
    story.append(table(
        ["Limit", "Value", "Why"],
        [
            ["Global", "~25 / sec", "Telegram hard cap ~30/s"],
            ["Per chat", "~1 / sec", "do not burst a group"],
            ["Per chat / min", "19", "groups cap at 20/min"],
            ["Editorial / hour", "8 public posts per chat", "news + markets share this"],
            ["Admin review / hour", "4", "stop review dumps"],
            ["RetryAfter", "honoured", "Telegram flood control"],
            ["Priority", "0 alerts, 5 news", "calendar/health jump the queue"],
        ],
        st, [42 * mm, 48 * mm, 80 * mm],
    ))

    story.append(P("9. The book (database)", st, "h1"))
    story.append(table(
        ["Table", "Role"],
        [
            ["seen_urls", "Every slow-lane URL. Hash unique. Stores title, AI fields, significance, posted_at, lane."],
            ["market_events", "Fast-lane events. event_key unique. decision keep/reject/review. posted_at only after send."],
            ["calendar_events", "Seeded 30-day catalog. UNIQUE(date, event_name, country). alerted_at / dayof_alerted_at."],
            ["fetcher_last_run", "Health: last success, last error, items_seen, expected_min, lane."],
            ["health_alerts", "When we already pinged admin about a dead fetcher."],
        ],
        st, [42 * mm, 128 * mm],
    ))
    story.append(P(
        "URL identity is SHA-256 of the canonical URL. Mark seen before send so a crash cannot double-post. "
        "Desk opens the file as SQLite mode=ro.",
        st, "body",
    ))

    story.append(P("10. The website (Live Desk)", st, "h1"))
    story.append(diagram_surfaces())
    story.append(P("The website is not a second factory. It is a window.", st, "caption"))
    story.append(P(
        "Code: backend.py (stdlib ThreadingHTTPServer) + index.html + style.css + app.js. "
        "Masthead is now <b>Sift Media</b> · LIVE DESK · IST clock. "
        "Nav: FRONT, DAILY, WORLD, GOLD, INDIA CALENDAR, INDIA MARKETS, INDIA POLITICS, "
        "US CALENDAR, US POLITICS, US MARKETS. Tabs WIRE / DIGEST. "
        "The N/10 kicker is source reliability, not LLM significance (often NULL). "
        "API /api/state JSON, refresh about every minute. Phone: swipe nav, 44px taps, stack on small screens. "
        "CSP is strict (self only). Not systemd — after a VPS reboot, start backend.py again. "
        "trycloudflare hostnames die on reboot; IP:8780 is the backup.",
        st, "body",
    ))

    story.append(P("11. PDF digest", st, "h1"))
    story.append(P(
        "No extra LLM call. Reads stored summary_ai / why_matters / watch_next. "
        "Masthead now SIFT MEDIA. Morning window ~14h, evening ~11h, significance floor 6, "
        "skip the file if fewer than 4 items. Sent as a Telegram document via a throwaway Bot "
        "then session.close(). Must not call asyncio.run() from the live scheduler loop "
        "(that bug sent 0 PDFs). Must not close the shared send-queue session.",
        st, "body",
    ))

    story.append(P("12. AI editor (current wiring)", st, "h1"))
    story.append(table(
        ["Knob", "Live value 13 Sep 2026"],
        [
            ["Provider actually used", "b.ai  https://api.b.ai/v1"],
            ["Model", "deepseek-v4-flash-vision-exp  (reasoning + vision model used as text JSON filter)"],
            ["OpenRouter on this bot", "NOT wired. No OPENROUTER_API_KEY in aslam-news-media/.env"],
            ["Wallet", "b.ai balance=0  →  HTTP 400 insufficient_user_quota"],
            ["Fail-soft seen in log", "slow keep-wire-only; fast keep: 0"],
            ["Timeout / max tokens", "120s / 8000 (reasoning budget)"],
            ["Pace", "LLM_MIN_GAP_SEC 3.5  (b.ai 429s if concurrent)"],
            ["Original spec", "OpenRouter deepseek/deepseek-v4-flash — never landed on this disk after VPS move"],
        ],
        st, [50 * mm, 120 * mm],
    ))
    story.append(P(
        "OpenRouter free models ARE used by the separate Manus bot (/root/veritas-manus), "
        "not by Sift. Switching Sift needs an explicit GO: copy the key, point base URL at "
        "openrouter.ai, pick a :free model that can emit JSON, restart the service, prove one keep/reject.",
        st, "note",
    ))

    story.append(P("13. Files map", st, "h1"))
    story.append(table(
        ["File", "Job"],
        [
            ["main.py", "Scheduler + CLI (run / once / health / calendar / digest)"],
            ["config.py", "All knobs from env. Missing cred disables a feature, never crashes."],
            ["feeds.py", "RSS + Google News list"],
            ["fetch/rss.py, nse.py, bse.py, sec_edgar.py, econ_calendar.py", "Collectors"],
            ["pipeline.py", "Clean, category, ranks, prefilter, HTML cards, channel route"],
            ["smart_filter.py", "LLM JSON keep/drop + explainers"],
            ["bots/slow_lane.py, fast_lane.py", "One cycle each"],
            ["send_queue.py", "Only Telegram exit"],
            ["db.py", "SQLite + migrations"],
            ["daily_digest.py", "PDF"],
            ["monitor.py", "Health + calendar alerts"],
            ["aslam-media-desk/*", "Read-only newspaper"],
        ],
        st, [55 * mm, 115 * mm],
    ))

    story.append(P("14. Live status at print time", st, "h1"))
    story.append(bullets([
        "Bot systemd: active (running since 11 Sep 2026).",
        "Fetchers: NSE and SEC still pulling (hundreds of NSE items per fast cycle).",
        "LLM filter: dead (b.ai wallet 0).",
        "Live Desk: HTTP 200, brand Sift Media, ~9,400 posts in the book.",
        "Public name locked: Sift Media. Folders/channels still aslam-*.",
        "GitHub PAT is on the VPS for aslam9949; this project is not a git repo and was not pushed.",
    ], st))

    story.append(P("15. One-sentence memory", st, "h1"))
    story.append(P(
        "English public feeds go in. Rules kill junk. An editor model (currently offline) "
        "keeps only significance 6+. At most 8 cards an hour leave through one queue. "
        "Telegram is the publisher. SQLite is the book. The website is the newspaper of that book.",
        st, "body",
    ))
    story.append(Spacer(1, 6 * mm))
    story.append(P(
        "End of report. No API keys, chat IDs, or tokens are printed here.",
        st, "note",
    ))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUT),
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=20 * mm,
        bottomMargin=18 * mm,
        title="Sift Media — Full system report",
        author="Sift Media / Hermes",
        subject="Sources, languages, pipeline, website, Telegram end messages",
    )
    doc.build(story, onFirstPage=header_footer, onLaterPages=header_footer)
    print(OUT, "pages", doc.page, "bytes", OUT.stat().st_size)


if __name__ == "__main__":
    build()
