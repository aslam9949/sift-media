"""India + US economic calendar — keyless, official-date catalog.

Horizon: rolling 30 days in the DB.
Alerts: full explainer 3 days before; short 'TODAY' ping on the day for high-impact.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pipeline

# date, name, country (US|IN), importance 1-5, event_type, time_label, notes
# Official / widely published 2026 dates. Horizon filter applied at seed time.
CATALOG: list[tuple[str, str, str, int, str, str, str]] = [
    # ---- US
    ("2026-08-26", "US GDP Q2 Second Estimate", "US", 5, "gdp", "08:30 ET · 18:00 IST", "Q2 2026 · BEA"),
    ("2026-08-26", "US PCE / Personal Income (Jul)", "US", 5, "pce", "08:30 ET · 18:00 IST", "July 2026 · Fed preferred inflation gauge"),
    ("2026-09-01", "ISM Manufacturing PMI (Aug)", "US", 3, "ism", "10:00 ET · 19:30 IST", "August 2026"),
    ("2026-09-01", "JOLTS Job Openings (Jul)", "US", 3, "jolts", "10:00 ET · 19:30 IST", "July 2026"),
    ("2026-09-04", "US Jobs Report / NFP (Aug)", "US", 5, "nfp", "08:30 ET · 18:00 IST", "August 2026"),
    ("2026-09-07", "US Labor Day (markets closed)", "US", 2, "holiday", "All day", "NYSE / Nasdaq closed"),
    ("2026-09-10", "US PPI (Aug)", "US", 4, "ppi", "08:30 ET · 18:00 IST", "August 2026"),
    ("2026-09-11", "US CPI (Aug)", "US", 5, "cpi", "08:30 ET · 18:00 IST", "August 2026"),
    ("2026-09-16", "US Retail Sales (Aug)", "US", 3, "retail", "08:30 ET · 18:00 IST", "August 2026"),
    ("2026-09-16", "FOMC Rate Decision", "US", 5, "fomc", "14:00 ET · 23:30 IST", "Sep 15–16 meeting · projections / dot plot"),
    ("2026-09-30", "US GDP Q2 Third Estimate", "US", 4, "gdp", "08:30 ET · 18:00 IST", "Q2 2026"),
    ("2026-09-30", "US PCE Inflation (Aug)", "US", 5, "pce", "08:30 ET · 18:00 IST", "August 2026"),
    ("2026-10-02", "US Jobs Report / NFP (Sep)", "US", 5, "nfp", "08:30 ET · 18:00 IST", "September 2026"),
    ("2026-10-14", "US CPI (Sep)", "US", 5, "cpi", "08:30 ET · 18:00 IST", "September 2026"),
    ("2026-10-29", "US GDP Q3 Advance + PCE (Sep)", "US", 5, "gdp", "08:30 ET · 18:00 IST", "Q3 advance / September PCE"),
    # ---- India
    ("2026-08-28", "India IIP (Jul)", "IN", 3, "india_iip", "17:30 IST", "July 2026 · MoSPI"),
    ("2026-08-31", "India GDP Q1 FY27", "IN", 5, "india_gdp", "17:30 IST", "Apr–Jun 2026 · MoSPI"),
    ("2026-09-12", "India CPI (Aug)", "IN", 5, "india_cpi", "17:30 IST", "August 2026 · MoSPI"),
    ("2026-09-28", "India IIP (Aug)", "IN", 3, "india_iip", "17:30 IST", "August 2026 · MoSPI"),
    ("2026-10-07", "RBI MPC Decision", "IN", 5, "rbi", "10:00 IST", "Oct 5–7 meeting · repo rate + stance"),
    ("2026-10-12", "India CPI (Sep)", "IN", 5, "india_cpi", "17:30 IST", "September 2026 · MoSPI"),
]

# Generic mechanism copy — no invented numbers, no buy/sell advice.
EFFECTS: dict[str, dict[str, str]] = {
    "nfp": {
        "why": "The jobs report is the Fed's main labor gauge. A hot print keeps US rates higher for longer; a miss fuels cut bets.",
        "market": "Hot NFP → US yields and the dollar usually rise, Nasdaq often weaker. Soft NFP → risk-on in US stocks. India: a stronger dollar / higher US yields can weigh on FIIs and the rupee.",
        "gold": "Hot jobs = firmer dollar = gold typically under pressure. Soft jobs = gold usually bid.",
        "btc": "Tracks risk appetite: cut bets help BTC; a hot print and a strong dollar usually hurt.",
    },
    "cpi": {
        "why": "US CPI is the headline inflation print. It directly feeds the next Fed decision and real yields.",
        "market": "Hot CPI → yields up, growth stocks down. Cool CPI → risk-on. India FIIs often follow the US yield move the same day.",
        "gold": "Cool inflation is usually bullish for gold (lower real yields). Hot CPI is typically gold-negative.",
        "btc": "Soft CPI / cut bets tend to lift BTC. Hot CPI tightens financial conditions and often knocks crypto.",
    },
    "pce": {
        "why": "PCE is the Fed's preferred inflation gauge — more important to FOMC than CPI for the policy path.",
        "market": "Hot PCE = higher-for-longer. Cool PCE = easier financial conditions for US and India risk assets.",
        "gold": "Cool PCE (lower real yields) supports gold. Hot PCE does the opposite.",
        "btc": "Same risk-on / risk-off as CPI, often a bit quieter unless the miss is large.",
    },
    "ppi": {
        "why": "PPI is a pipeline inflation print. It often sets the tone the day before CPI.",
        "market": "A hot PPI can pull forward CPI nerves and lift yields. A cool PPI eases those nerves.",
        "gold": "Usually a milder version of the CPI gold reaction.",
        "btc": "Usually a milder version of the CPI crypto reaction.",
    },
    "fomc": {
        "why": "The Fed sets the world's benchmark rate. The decision, dots, and press conference reprice every risk asset.",
        "market": "Hawkish hold/hike → dollar and yields up, stocks and India FIIs under pressure. Dovish cut/signal → risk-on globally.",
        "gold": "Dovish Fed is typically gold-positive (weaker dollar, lower real yields). Hawkish Fed is gold-negative.",
        "btc": "Liquidity event: dovish surprise often lifts BTC hard; hawkish surprise often dumps it.",
    },
    "gdp": {
        "why": "GDP is the growth scorecard. A strong print supports earnings; a miss raises slowdown fears.",
        "market": "Strong GDP + cool inflation = goldilocks for stocks. Weak GDP can hit cyclicals and the rupee if global growth fears rise.",
        "gold": "Weak growth can help gold (safe haven). Strong growth with hot inflation can hurt it via yields.",
        "btc": "Follows the growth/liquidity mix — usually less jumpy than NFP or CPI unless the miss is huge.",
    },
    "retail": {
        "why": "Retail sales show how hard the US consumer is still spending — a big chunk of US GDP.",
        "market": "Strong sales support cyclicals; a collapse feeds recession trades and can spill into India IT/export names.",
        "gold": "Mild. A hard growth scare can bid gold.",
        "btc": "Mild risk-on / risk-off. Rarely the main driver.",
    },
    "jolts": {
        "why": "Job openings show how tight the US labor market still is. The Fed watches the openings-to-unemployed ratio.",
        "market": "Hot openings = stickier wages in the Fed's mind. Cooling openings ease rate fears.",
        "gold": "Mild, via the rate-path channel.",
        "btc": "Mild risk tone.",
    },
    "ism": {
        "why": "ISM Manufacturing is a quick factory-cycle pulse. Above 50 = expansion, below 50 = contraction.",
        "market": "A sharp drop can hit cyclicals and copper-sensitive India names. A rebound supports risk.",
        "gold": "Mild. A deep contraction scare can bid gold.",
        "btc": "Mild risk tone.",
    },
    "holiday": {
        "why": "US markets are shut. Liquidity is thin; India still trades, so gap risk into the next US session is higher.",
        "market": "Expect quieter US-linked moves today. Watch the next US session for catch-up.",
        "gold": "Often calmer on the US cash close; Asia can still move it.",
        "btc": "Crypto still trades 24/7 — thin US desks can mean sharper wicks.",
    },
    "india_cpi": {
        "why": "India CPI is the RBI's inflation target print. It is the main input into the next MPC rate decision.",
        "market": "Hot CPI → rate-cut bets fade, banks/rate-sensitives can wobble, rupee can firm if RBI stays hawkish. Cool CPI → rate-cut hopes, Nifty rate-sensitives bid.",
        "gold": "INR gold: a weaker rupee after a hot print can lift local gold even if dollar gold is flat.",
        "btc": "Indirect. A risk-off India tape plus a strong dollar is usually a headwind for BTC in INR terms.",
    },
    "india_gdp": {
        "why": "GDP is India's growth scorecard. It shapes FII flows, the RBI's growth vs inflation trade-off, and the Nifty earnings mood.",
        "market": "A strong print supports cyclicals, banks, and the rupee. A miss can hit domestic cyclicals and invite FII caution.",
        "gold": "Mild. A growth scare with rupee weakness can lift INR gold.",
        "btc": "Mild. India-specific GDP rarely drives BTC unless it lands with a global risk-off day.",
    },
    "india_iip": {
        "why": "IIP is monthly factory output. It is a growth nowcast between GDP prints.",
        "market": "A big miss can pressure industrials and metal names. A beat supports the domestic-growth tape.",
        "gold": "Usually small. Watch only if it pairs with a weak rupee.",
        "btc": "Usually small.",
    },
    "rbi": {
        "why": "The MPC sets the repo rate and stance. That reprices Indian banks, the rupee, bonds, and gold in INR.",
        "market": "Hike / hawkish hold → banks mixed, rate-sensitives down, rupee often firmer. Cut / dovish → Nifty rate-sensitives bid, rupee can soften.",
        "gold": "Dovish RBI / weaker rupee typically lifts INR gold. Hawkish RBI can cap local gold even if dollar gold is firm.",
        "btc": "Indirect via rupee and India risk appetite. A surprise cut can spill into local crypto demand.",
    },
}

COUNTRY_LABEL = {"US": "🇺🇸 US", "IN": "🇮🇳 India"}
COUNTRY_CHANNEL = {
    "US": "us_econ_calendar",
    "IN": "india_econ_calendar",
}


def events_for_horizon(start: date, days: int) -> list[dict[str, Any]]:
    end = start + timedelta(days=days)
    out: list[dict[str, Any]] = []
    for d, name, country, imp, etype, time_label, notes in CATALOG:
        dt = date.fromisoformat(d)
        if start <= dt <= end:
            out.append({
                "date": d,
                "event_name": name,
                "country": country,
                "importance": imp,
                "event_type": etype,
                "time_label": time_label,
                "notes": notes,
            })
    out.sort(key=lambda e: (e["date"], 0 if e["country"] == "IN" else 1, -e["importance"]))
    return out


def effects_for(event_type: str) -> dict[str, str]:
    return EFFECTS.get(event_type or "", {
        "why": "This is a scheduled macro print. Watch the surprise vs what the market already priced.",
        "market": "A hotter-than-feared print tightens conditions; a miss eases them.",
        "gold": "Usually via the dollar and real yields.",
        "btc": "Usually via risk appetite and the dollar.",
    })


def _stars(n: int) -> str:
    n = max(1, min(5, int(n or 3)))
    return "★" * n + "☆" * (5 - n)


def _imp_word(n: int) -> str:
    n = int(n or 3)
    if n >= 5:
        return "High impact"
    if n >= 4:
        return "Elevated"
    if n >= 3:
        return "Medium"
    return "Low"


def format_month(events: list[dict[str, Any]], country: str, start: date, days: int) -> str:
    end = start + timedelta(days=days)
    flag = COUNTRY_LABEL.get(country, country)
    lines = [
        f"🗓 <b>{flag} Economic Calendar</b> · next {days} days",
        f"<i>{start.strftime('%d %b')} – {end.strftime('%d %b %Y')}</i>",
        "",
    ]
    by_day: dict[str, list[dict[str, Any]]] = {}
    for ev in events:
        by_day.setdefault(ev["date"], []).append(ev)
    if not by_day:
        lines.append("No high-impact prints in this window.")
        return "\n".join(lines)
    for day in sorted(by_day):
        dt = date.fromisoformat(day)
        lines.append(f"<b>{dt.strftime('%a %d %b')}</b>")
        for ev in by_day[day]:
            time = pipeline.esc(ev.get("time_label") or "")
            name = pipeline.esc(ev.get("event_name") or "")
            lines.append(f"  {_stars(ev.get('importance', 3))} {name}")
            if time:
                lines.append(f"  <i>{time}</i>")
        lines.append("")
    lines.append("⏰ Full alert fires <b>3 days before</b> each event (why it matters + markets / gold / BTC).")
    lines.append("High-impact names also get a short <b>TODAY</b> ping on the morning of.")
    return "\n".join(lines).strip()


def format_t3(ev: dict[str, Any]) -> str:
    fx = effects_for(ev.get("event_type") or "")
    flag = COUNTRY_LABEL.get(ev.get("country") or "", ev.get("country") or "")
    day = date.fromisoformat(ev["date"]).strftime("%a, %d %b %Y")
    lines = [
        f"⏰ <b>3 DAYS OUT</b> · {flag} Economic Calendar",
        f"📅 {pipeline.esc(day)}" + (f" · {pipeline.esc(ev.get('time_label') or '')}" if ev.get("time_label") else ""),
        "",
        f"🔴 <b>{pipeline.esc(ev.get('event_name') or '')}</b>",
        f"{_stars(ev.get('importance', 3))} {_imp_word(ev.get('importance', 3))}",
    ]
    if ev.get("notes"):
        lines.append(f"<i>{pipeline.esc(ev['notes'])}</i>")
    lines += [
        "",
        "📋 <b>Why it matters</b>",
        pipeline.esc(fx["why"]),
        "",
        "📈 <b>Markets</b>",
        pipeline.esc(fx["market"]),
        "",
        "🥇 <b>Gold</b>",
        pipeline.esc(fx["gold"]),
        "",
        "₿ <b>BTC</b>",
        pipeline.esc(fx["btc"]),
        "",
        "<i>Not a trade call — mechanism only. Watch the surprise vs what is already priced.</i>",
    ]
    return "\n".join(lines)


def format_today(ev: dict[str, Any]) -> str:
    fx = effects_for(ev.get("event_type") or "")
    flag = COUNTRY_LABEL.get(ev.get("country") or "", ev.get("country") or "")
    day = date.fromisoformat(ev["date"]).strftime("%a, %d %b %Y")
    lines = [
        f"⏰ <b>TODAY</b> · {flag} Economic Calendar",
        f"📅 {pipeline.esc(day)}" + (f" · {pipeline.esc(ev.get('time_label') or '')}" if ev.get("time_label") else ""),
        "",
        f"🔴 <b>{pipeline.esc(ev.get('event_name') or '')}</b>",
        f"{_stars(ev.get('importance', 3))} {_imp_word(ev.get('importance', 3))}",
        "",
        "📋 " + pipeline.esc(fx["why"]),
        "",
        "<i>Markets / gold / BTC: same channels as the 3-day alert. Watch the surprise, not the headline.</i>",
    ]
    return "\n".join(lines)
