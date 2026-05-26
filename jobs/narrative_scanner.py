"""
jobs/narrative_scanner.py — Narrative Momentum Scanner for Sebol.

Detects emerging investment narratives BEFORE they hit Bloomberg covers.
Weekly scan every Friday; also available on-demand via /narracje command.
"""

import os
import json
import re
import logging
import datetime

import _ctx

logger = logging.getLogger(__name__)

try:
    from tavily import TavilyClient
    _TAVILY_KEY = os.environ.get("TAVILY_API_KEY", "")
    _tavily = TavilyClient(api_key=_TAVILY_KEY) if _TAVILY_KEY else None
except ImportError:
    _tavily = None

STOCK_CHANNEL_ID = os.environ.get("SLACK_STOCK_CHANNEL", "C0B5LA4Q064")

# ── Watchlist sectors for halo effect matching ────────────────────────────────
_SECTOR_BENEFICIARIES = {
    "space":         ["RKLB", "ASTS", "LUNR", "PL", "RDW", "IRDM"],
    "nuclear":       ["CCJ", "UEC", "DNN", "UUUU"],
    "crypto":        ["MSTR", "MARA", "HOOD"],
    "ai":            ["NVDA", "APP", "TTD", "CRWD", "NOW", "AMD", "ALAB", "MSFT"],
    "glp1":          ["NVO", "ISRG", "TEM"],
    "defense":       ["NOC", "TDG", "AXON", "BA"],
    "fintech_em":    ["NU", "DLO", "MELI", "SE", "GRAB"],
    "cybersecurity": ["CRWD", "FTNT", "S", "RBRK"],
    "semis":         ["NVDA", "AVGO", "AMD", "MU", "ASML", "ALAB"],
}

# ── Narrative search config ───────────────────────────────────────────────────
_NARRATIVE_QUERIES = {
    "ipo_catalyst": [
        "major IPO upcoming 2026 sector stocks rally",
        "SpaceX IPO space sector stocks rally 2026",
        "pre-IPO sector momentum beneficiaries 2026",
        "Stripe Klarna IPO fintech sector impact 2026",
    ],
    "regulatory_catalyst": [
        "FDA approval upcoming biotech breakthrough 2026",
        "FCC approval satellite spectrum 2026",
        "nuclear SMR approval NRC regulatory 2026",
        "crypto Bitcoin ETF approval SEC 2026",
        "autonomous vehicles FSD regulatory approval 2026",
    ],
    "geopolitical_tailwind": [
        "defense budget increase NATO countries 2026",
        "space race government contracts awards 2026",
        "energy independence nuclear uranium policy 2026",
        "AI chips export controls beneficiaries 2026",
    ],
    "tech_inflection": [
        "AI deployment enterprise revenue monetization 2026",
        "agentic AI software spending acceleration 2026",
        "quantum computing commercial breakthrough 2026",
        "nuclear fusion power commercial milestone 2026",
    ],
    "early_signals": [
        "institutional investor interest emerging sector 2026",
        "analyst coverage initiation new sector theme 2026",
        "hedge fund positioning sector rotation 2026",
        "options unusual activity sector ETF 2026",
    ],
}

# ── Sector-specific deep-dive queries ─────────────────────────────────────────
_SECTOR_QUERIES = {
    "space":         ["space economy SpaceX IPO sector rally 2026", "satellite broadband DoD LEO contracts 2026"],
    "nuclear":       ["nuclear renaissance SMR approval utility contracts 2026", "uranium spot price AI data center demand 2026"],
    "crypto":        ["Bitcoin institutional inflows ETF approval 2026", "crypto regulation clarity SEC 2026"],
    "ai":            ["agentic AI enterprise software monetization 2026", "AI infrastructure capex hyperscaler spending 2026"],
    "glp1":          ["GLP-1 obesity drug market share expansion 2026", "weight loss drug supply chain beneficiaries 2026"],
    "defense":       ["NATO defense budget increase procurement 2026", "hypersonic missile autonomous systems contracts 2026"],
    "fintech_em":    ["fintech emerging markets growth credit penetration 2026", "Latin America digital banking NU DLO 2026"],
    "cybersecurity": ["cybersecurity enterprise spending AI threat 2026", "ransomware government mandates CRWD 2026"],
    "semis":         ["semiconductor AI chip demand TSMC NVDA supply 2026", "advanced packaging CoWoS AVGO ALAB 2026"],
}

_SECTOR_LABELS = {
    "space":         "🚀 Space/Defense",
    "nuclear":       "☢️ Nuclear/Energy",
    "crypto":        "₿ Crypto",
    "ai":            "🤖 AI/Tech",
    "glp1":          "💊 GLP-1/Biotech",
    "defense":       "🛡 Defense",
    "fintech_em":    "🌍 Fintech EM",
    "cybersecurity": "🔐 Cybersecurity",
    "semis":         "💡 Semis",
}


def _tavily_search(query: str, max_results: int = 3) -> str:
    """Run one Tavily search, return condensed text."""
    if _tavily is None:
        return ""
    try:
        r = _tavily.search(query, max_results=max_results)
        return " ".join((x.get("content") or "")[:200] for x in (r.get("results") or []))[:500]
    except Exception as e:
        logger.warning("Tavily narrative search error (%s): %s", query[:50], e)
        return ""


def _gather_narrative_signals() -> dict[str, str]:
    """Run all narrative Tavily searches. Returns {category: combined_text}."""
    signals = {}
    for category, queries in _NARRATIVE_QUERIES.items():
        parts = []
        for q in queries:
            text = _tavily_search(q, max_results=2)
            if text:
                parts.append(text)
        signals[category] = " | ".join(parts)[:800]
    return signals


def _gather_sector_signals(sector: str) -> str:
    """Deep dive search for a specific sector."""
    queries = _SECTOR_QUERIES.get(sector, [f"{sector} stocks outlook momentum 2026"])
    parts = []
    for q in queries:
        text = _tavily_search(q, max_results=3)
        if text:
            parts.append(text)
    return " | ".join(parts)[:1000]


def _claude_narrative_scan(signals: dict[str, str]) -> dict:
    """Send all signals to Claude → structured narrative radar."""
    signals_text = "\n\n".join(
        f"[{cat.upper()}]\n{text}"
        for cat, text in signals.items()
        if text
    )

    prompt = (
        f"Dzisiaj: {datetime.datetime.now().strftime('%d.%m.%Y')}\n\n"
        f"Poniżej zebrane sygnały z internetu o pojawiających się narracjach inwestycyjnych:\n\n"
        f"{signals_text}\n\n"
        "Przeanalizuj te sygnały i zidentyfikuj narracje inwestycyjne według ich stadium:\n"
        "- HEATING: właśnie się rozgrzewa — pierwsze sygnały, rynek jeszcze nie zdyskontował\n"
        "- HOT: w pełni rozgrzana — duże ruchy już za nami, ryzyko korekty\n"
        "- COOLING: traci impet — unikaj nowych wejść\n"
        "- COLD: brak aktywnej narracji\n\n"
        "Odpowiedz TYLKO w JSON:\n"
        "{\n"
        '  "heating": [\n'
        '    {\n'
        '      "name": "Krótka nazwa narracji",\n'
        '      "status": "HEATING",\n'
        '      "catalyst": "Co konkretnie się dzieje — 1-2 zdania",\n'
        '      "sectors": ["space", "nuclear", ...],\n'
        '      "window": "Szacowany czas okna okazji np. 2-4 tygodnie",\n'
        '      "risk": "Główne ryzyko 1 zdanie"\n'
        "    }\n"
        "  ],\n"
        '  "hot": [\n'
        '    {"name": "...", "status": "HOT", "catalyst": "...", "sectors": [], "risk": "..."}\n'
        "  ],\n"
        '  "cooling": ["Narracja która traci impet", ...],\n'
        '  "watch_next_week": ["Co może się rozgrzać w przyszłym tygodniu", ...],\n'
        '  "summary": "2-3 zdania ogólnego podsumowania rynkowego"\n'
        "}"
    )

    try:
        resp = _ctx.claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        m = re.search(r"\{[\s\S]*\}", raw)
        return json.loads(m.group() if m else raw)
    except Exception as e:
        logger.error("Claude narrative scan error: %s", e)
        return {
            "heating": [], "hot": [], "cooling": [],
            "watch_next_week": [],
            "summary": "Błąd analizy — spróbuj ponownie.",
        }


def _claude_sector_dive(sector: str, signals: str) -> dict:
    """Deep-dive Claude analysis for a specific sector narrative."""
    label = _SECTOR_LABELS.get(sector, sector)
    beneficiaries = _SECTOR_BENEFICIARIES.get(sector, [])

    prompt = (
        f"Sektor: {label}\n"
        f"Spółki w watchliście dla tego sektora: {', '.join(beneficiaries)}\n\n"
        f"Zebrane sygnały z internetu:\n{signals}\n\n"
        "Zrób deep-dive tej narracji i odpowiedz TYLKO w JSON:\n"
        "{\n"
        '  "status": "HEATING"/"HOT"/"COOLING"/"COLD",\n'
        '  "catalyst": "Główny katalizator — 2-3 zdania",\n'
        '  "best_positioned": ["TICKER1", "TICKER2"],\n'
        '  "outside_watchlist": "Spółki spoza watchlisty warte uwagi — 1 zdanie",\n'
        '  "window": "Szacowany czas okna okazji",\n'
        '  "entry_logic": "Jak wchodzić — 1-2 zdania",\n'
        '  "risk": "Główne ryzyko — 1 zdanie",\n'
        '  "summary": "2-3 zdania podsumowania"\n'
        "}"
    )

    try:
        resp = _ctx.claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        m = re.search(r"\{[\s\S]*\}", raw)
        return json.loads(m.group() if m else raw)
    except Exception as e:
        logger.error("Claude sector dive error (%s): %s", sector, e)
        return {"status": "COLD", "summary": "Błąd analizy.", "catalyst": "", "best_positioned": [], "risk": ""}


# ── Formatting ────────────────────────────────────────────────────────────────

def _status_emoji(status: str) -> str:
    return {"HEATING": "🔥", "HOT": "♨️", "COOLING": "❄️", "COLD": "💤"}.get(status, "📌")


def format_narrative_radar(scan: dict) -> str:
    """Format full narrative radar for Slack."""
    today = datetime.datetime.now().strftime("%d.%m.%Y")
    lines = [f"🔭 *Narrative Radar — {today}*\n"]

    # Summary
    summary = scan.get("summary", "")
    if summary:
        lines.append(f"_{summary}_\n")

    # Heating narratives
    heating = scan.get("heating", [])
    hot = scan.get("hot", [])

    if heating:
        lines.append("🔥 *ROZGRZEWAJĄCE SIĘ NARRACJE:*")
        for i, n in enumerate(heating, 1):
            sectors = n.get("sectors", [])
            beneficiaries = []
            for s in sectors:
                beneficiaries.extend(_SECTOR_BENEFICIARIES.get(s, []))
            # Deduplicate preserving order
            seen = set()
            deduped = [t for t in beneficiaries if not (t in seen or seen.add(t))]

            lines.append(f"\n{i}. *{n.get('name', '?')}* — {_status_emoji('HEATING')} HEATING")
            lines.append(f"   Katalizator: {n.get('catalyst', '')}")
            if deduped:
                lines.append(f"   Beneficjenci z watchlisty: {', '.join(deduped[:6])}")
            if n.get("window"):
                lines.append(f"   Okno okazji: {n['window']}")
            if n.get("risk"):
                lines.append(f"   ⚠️ Ryzyko: {n['risk']}")

    if hot:
        lines.append("\n♨️ *GORĄCE NARRACJE (ryzyko przegrzania):*")
        for n in hot:
            lines.append(f"• *{n.get('name', '?')}* — {n.get('catalyst', '')}")
            if n.get("risk"):
                lines.append(f"  ⚠️ {n['risk']}")

    # Cooling
    cooling = scan.get("cooling", [])
    if cooling:
        lines.append("\n❄️ *STYGNĄCE NARRACJE:*")
        for c in cooling:
            lines.append(f"• {c}")

    # Watch next week
    watch = scan.get("watch_next_week", [])
    if watch:
        lines.append("\n💡 *OBSERWUJ W PRZYSZŁYM TYGODNIU:*")
        for w in watch:
            lines.append(f"• {w}")

    lines.append(f"\n_Wygenerowano przez Sebol • {datetime.datetime.now().strftime('%d.%m.%Y %H:%M')}_")
    return "\n".join(lines)


def format_sector_dive(sector: str, dive: dict) -> str:
    """Format deep-dive result for a specific sector."""
    label = _SECTOR_LABELS.get(sector, sector)
    status = dive.get("status", "COLD")
    status_e = _status_emoji(status)
    lines = [f"🔭 *Narrative Deep Dive — {label}* {status_e} *{status}*\n"]

    if dive.get("summary"):
        lines.append(f"_{dive['summary']}_\n")

    if dive.get("catalyst"):
        lines.append(f"*Katalizator:*\n{dive['catalyst']}\n")

    best = dive.get("best_positioned", [])
    if best:
        lines.append(f"*Najlepiej pozycjonowane (watchlista):* {', '.join(best)}")

    if dive.get("outside_watchlist"):
        lines.append(f"*Spoza watchlisty:* {dive['outside_watchlist']}")

    if dive.get("window"):
        lines.append(f"*Okno okazji:* {dive['window']}")

    if dive.get("entry_logic"):
        lines.append(f"*Jak wchodzić:* {dive['entry_logic']}")

    if dive.get("risk"):
        lines.append(f"*⚠️ Ryzyko:* {dive['risk']}")

    return "\n".join(lines)


# ── Public API ────────────────────────────────────────────────────────────────

def run_narrative_scan() -> str:
    """Run full narrative scan and return formatted Slack text."""
    logger.info("🔭 Uruchamiam narrative momentum scan...")
    signals = _gather_narrative_signals()
    scan = _claude_narrative_scan(signals)
    return format_narrative_radar(scan)


def run_sector_dive(sector_input: str) -> str:
    """Deep dive for a specific sector. sector_input can be partial (e.g. 'space', 'nuclear')."""
    sector_input = sector_input.lower().strip()
    # Map common aliases
    _aliases = {
        "space": "space", "kosmiczny": "space", "kosmos": "space", "rklb": "space",
        "nuclear": "nuclear", "nuklear": "nuclear", "uranium": "nuclear", "uran": "nuclear",
        "crypto": "crypto", "krypto": "crypto", "bitcoin": "crypto", "btc": "crypto",
        "ai": "ai", "sztuczna": "ai", "tech": "ai",
        "glp": "glp1", "glp1": "glp1", "biotech": "glp1", "nvo": "glp1",
        "defense": "defense", "defence": "defense", "obronny": "defense", "obrona": "defense",
        "fintech": "fintech_em", "em": "fintech_em", "latam": "fintech_em",
        "cyber": "cybersecurity", "cybersecurity": "cybersecurity",
        "semis": "semis", "chips": "semis", "semiconductor": "semis",
    }
    sector = _aliases.get(sector_input)
    if not sector:
        # Try partial match on keys
        for key in _SECTOR_QUERIES:
            if sector_input in key or key in sector_input:
                sector = key
                break
    if not sector:
        available = ", ".join(_SECTOR_LABELS.keys())
        return f"⚠️ Nieznany sektor: *{sector_input}*\nDostępne: {available}"

    logger.info("🔭 Deep dive: %s", sector)
    signals = _gather_sector_signals(sector)
    dive = _claude_sector_dive(sector, signals)
    return format_sector_dive(sector, dive)


def send_narrative_radar():
    """Weekly scheduler entry — posts to #inwestowanie every Friday."""
    try:
        text = run_narrative_scan()
        chunks = [text[i:i + 3900] for i in range(0, len(text), 3900)]
        for chunk in chunks:
            _ctx.app.client.chat_postMessage(channel=STOCK_CHANNEL_ID, text=chunk)
        logger.info("✅ Narrative radar wysłany!")
    except Exception as e:
        logger.error("send_narrative_radar failed: %s", e)
