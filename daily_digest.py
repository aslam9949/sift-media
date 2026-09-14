"""Daily PDF digest (morning + evening) — rendered like a real newspaper.

Collects the day's important news (items at/above the significance floor posted
in the just-closed window) and lays them out as a single multi-section PDF with
a masthead, dateline, a lead story, and per-category sections. Sent to the
digest channel as a document.

Reuses the per-item AI summaries already stored in the DB, so the digest costs
NO extra LLM calls.
"""
from __future__ import annotations

import asyncio
import io
import logging
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
)

import config
import db
import pipeline

log = logging.getLogger("daily_digest")

# ---- palette (clean, print-like) -------------------------------------------
INK = colors.HexColor("#111111")
NAVY = colors.HexColor("#1a3c5e")
RULE = colors.HexColor("#cccccc")
MUTED = colors.HexColor("#666666")
SECTION_BG = colors.HexColor("#f3f5f7")

RED = colors.HexColor("#b3261e")     # HIGH significance
ORANGE = colors.HexColor("#c77700")  # MED
GREEN = colors.HexColor("#2e7d32")   # LOW

SECTION_ORDER = [
    "geopolitics",
    "india_politics",
    "us_politics",
    "india_market_ipo",
    "us_market_ipo",
    "us_stock_market",
    "gold_news",
    "crypto_news",
    "india_econ_calendar",
    "us_econ_calendar",
    "daily_news",
]


def _safe(text: str | None) -> str:
    if not text:
        return ""
    out = []
    for ch in str(text):
        o = ord(ch)
        if 32 <= o <= 126 or o in (0x2018, 0x2019, 0x201C, 0x201D, 0x2013, 0x2014, 0x2026):
            out.append(ch)
        elif o > 126:
            out.append("?")
        else:
            out.append(ch)
    return "".join(out)


def _sig_color(sig: int) -> colors.Color:
    sig = max(1, min(10, int(sig or 5)))
    return RED if sig >= 7 else (ORANGE if sig >= 4 else GREEN)


def _sig_word(sig: int) -> str:
    sig = max(1, min(10, int(sig or 5)))
    return "HIGH" if sig >= 7 else ("MEDIUM" if sig >= 4 else "LOW")


# ---- styles ----------------------------------------------------------------
def _styles() -> dict:
    ss = getSampleStyleSheet()
    return {
        "masthead": ParagraphStyle("masthead", parent=ss["Normal"], fontName="Helvetica-Bold",
                                    fontSize=30, leading=32, textColor=INK, alignment=TA_CENTER),
        "mast_sub": ParagraphStyle("mast_sub", parent=ss["Normal"], fontSize=10, leading=12,
                                   textColor=MUTED, alignment=TA_CENTER),
        "dateline": ParagraphStyle("dateline", parent=ss["Normal"], fontSize=8.5, leading=11,
                                   textColor=MUTED, alignment=TA_CENTER),
        "lead_title": ParagraphStyle("lead_title", parent=ss["Normal"], fontName="Helvetica-Bold",
                                     fontSize=18, leading=22, textColor=INK, spaceAfter=4),
        "lead_meta": ParagraphStyle("lead_meta", parent=ss["Normal"], fontSize=8.5, textColor=MUTED, spaceAfter=4),
        "lead_body": ParagraphStyle("lead_body", parent=ss["Normal"], fontSize=10.5, leading=15,
                                    alignment=TA_JUSTIFY, textColor=INK),
        "sec_head": ParagraphStyle("sec_head", parent=ss["Normal"], fontName="Helvetica-Bold",
                                   fontSize=14, leading=16, textColor=NAVY, spaceBefore=10, spaceAfter=2),
        "story_title": ParagraphStyle("story_title", parent=ss["Normal"], fontName="Helvetica-Bold",
                                      fontSize=11.5, leading=14, textColor=INK, spaceAfter=2),
        "story_meta": ParagraphStyle("story_meta", parent=ss["Normal"], fontSize=8, textColor=MUTED, spaceAfter=2),
        "body": ParagraphStyle("body", parent=ss["Normal"], fontSize=9.5, leading=13.5,
                               alignment=TA_JUSTIFY, textColor=INK, spaceAfter=2),
        "label": ParagraphStyle("label", parent=ss["Normal"], fontName="Helvetica-Bold",
                                fontSize=8.5, leading=11, textColor=NAVY, spaceAfter=1),
        "url": ParagraphStyle("url", parent=ss["Normal"], fontSize=7.5, textColor=colors.HexColor("#1a5fb4"),
                              spaceAfter=2),
        "empty": ParagraphStyle("empty", parent=ss["Normal"], fontSize=10, leading=14, textColor=MUTED),
    }


def _article(it: dict, st: dict, lead: bool = False) -> list:
    """Build the flowables for one story."""
    sig = int(it.get("significance") or 5)
    sc = _sig_color(sig)
    src = pipeline.pretty_source(it.get("source"))
    rel = pipeline.source_reliability(it.get("source"), it.get("url"))
    title = _safe(it.get("title", "(untitled)"))
    meta = f'<font color="{sc.hexval()}">&#8226;</font> {_sig_word(sig)} significance &bull; {sig}/10 &nbsp;|&nbsp; <font color="{sc.hexval()}">&#8226;</font> {_safe(src)} &bull; reliability {rel}/10'

    flow: list = []
    if lead:
        flow.append(Paragraph(f'<font color="{sc.hexval()}">&#8226;</font> {title}', st["lead_title"]))
    else:
        flow.append(Paragraph(f'<font color="{sc.hexval()}">&#8226;</font> {title}', st["story_title"]))
    flow.append(Paragraph(meta, st["lead_meta"] if lead else st["story_meta"]))

    summary = _safe(it.get("summary_ai"))
    if summary:
        flow.append(Paragraph(f'<b>What happened:</b> {summary}', st["body"] if lead else st["body"]))
    why = _safe(it.get("why_matters"))
    if why:
        flow.append(Paragraph(f'<b>Why it matters:</b> {why}', st["body"]))
    watch = _safe(it.get("watch_next"))
    if watch:
        flow.append(Paragraph(f'<b>Watch next:</b> {watch}', st["body"]))
    url = it.get("url") or ""
    if url:
        flow.append(Paragraph(_safe(url), st["url"]))
    return flow


def build_pdf(items: list[dict[str, Any]], label: str, window: tuple[str, str]) -> bytes:
    buf = io.BytesIO()
    st = _styles()

    doc = BaseDocTemplate(
        buf, pagesize=A4,
        leftMargin=16 * mm, rightMargin=16 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
        title=f"Sift Media - {label} Digest",
        author="Sift Media",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame])])

    w_start, w_end = window
    ist_start = datetime.fromisoformat(w_start.replace("Z", "+00:00")).astimezone(
        timezone(timedelta(hours=5, minutes=30)))
    ist_now = datetime.now(timezone(timedelta(hours=5, minutes=30)))

    story: list = []
    # ---- Masthead ----
    story.append(Paragraph("SIFT MEDIA", st["masthead"]))
    story.append(Paragraph(f"{label.upper()} EDITION", st["mast_sub"]))
    story.append(Spacer(1, 3))
    story.append(HRFlowable(width="100%", thickness=2, color=INK, spaceAfter=3))
    dateline = (f"{ist_now.strftime('%A, %d %B %Y')} &nbsp;&middot;&nbsp; "
                f"Times of India-edition style &nbsp;&middot;&nbsp; "
                f"Window {ist_start.strftime('%d %b %H:%M')}–{ist_now.strftime('%H:%M')} IST")
    story.append(Paragraph(dateline, st["dateline"]))
    story.append(HRFlowable(width="100%", thickness=0.6, color=RULE, spaceBefore=3, spaceAfter=8))

    if not items:
        story.append(Paragraph("No significant stories in this window.", st["empty"]))
        doc.build(story)
        return buf.getvalue()

    # ---- Lead story (highest significance) ----
    lead = items[0]
    story.append(Paragraph("LEAD STORY", st["label"]))
    story.append(KeepTogether(_article(lead, st, lead=True)))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=0.6, color=RULE, spaceBefore=2, spaceAfter=8))

    # ---- Sectioned remainder ----
    rest = items[1:]
    by_cat: "OrderedDict[str, list[dict[str, Any]]]" = OrderedDict()
    for cat in SECTION_ORDER:
        by_cat[cat] = []
    by_cat["other"] = []
    for it in rest:
        cat = it.get("category", "other")
        if cat not in by_cat:
            cat = "other"
        by_cat[cat].append(it)

    sections_drawn = 0
    for cat, cat_items in by_cat.items():
        if not cat_items:
            continue
        cat_label = pipeline.CATEGORY_LABELS.get(cat, cat.replace("_", " ").title())
        block: list = [Paragraph(cat_label.upper(), st["sec_head"]),
                       HRFlowable(width="100%", thickness=1, color=NAVY, spaceAfter=5)]
        for it in cat_items:
            block.append(KeepTogether(_article(it, st) + [Spacer(1, 7)]))
        story.append(KeepTogether(block))
        sections_drawn += 1

    doc.build(story)
    return buf.getvalue()


# --------------------------------------------------------------- scheduling
async def run(window_hours: int, label: str) -> int:
    if not config.DIGEST_CHANNEL_ID:
        log.warning("digest: DIGEST_CHANNEL_ID not set — skipping")
        return 0
    now = datetime.now(timezone.utc)
    w_end = now.isoformat()
    w_start = (now - timedelta(hours=window_hours)).isoformat()

    items = db.digest_items(w_start, w_end, config.DIGEST_MIN_SIGNIFICANCE)
    if len(items) < config.DIGEST_MIN_ITEMS:
        log.info("digest %s: only %d items (< %d) — skipping PDF", label, len(items), config.DIGEST_MIN_ITEMS)
        return 0

    # build_pdf is CPU-bound (ReportLab). Offload it to the executor so it never
    # blocks the live scheduler loop — never asyncio.run() here.
    loop = asyncio.get_running_loop()
    pdf_bytes = await loop.run_in_executor(None, build_pdf, items, label, (w_start, w_end))
    fname = f"aslam_news_{label.lower().replace(' ', '_')}_{now.strftime('%Y%m%d_%H%M')}.pdf"

    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode
    from aiogram.types import BufferedInputFile

    # Own Bot session — never asyncio.run() on the live scheduler loop,
    # and never close the shared send_queue bot.
    bot = Bot(
        token=config.BOT1_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )
    try:
        await bot.send_document(
            chat_id=config.DIGEST_CHANNEL_ID,
            document=BufferedInputFile(pdf_bytes, filename=fname),
            caption=f"🗞 {label} Digest — {len(items)} important stories (significance ≥ {config.DIGEST_MIN_SIGNIFICANCE}).",
        )
        log.info("digest %s: sent %d stories (%d bytes)", label, len(items), len(pdf_bytes))
    finally:
        await bot.session.close()
    return len(items)


async def morning_digest() -> int:
    return await run(14, "Morning")


async def evening_digest() -> int:
    return await run(11, "Evening")
