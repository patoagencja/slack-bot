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

def _cur_year() -> int:
    """Current year — queries derive dates dynamically (no hardcoded year)."""
    import datetime as _d
    return _d.datetime.now().year


# ── Central Claude model config (no hardcoded model strings) ──
try:
    from investing.config import CLAUDE_MODEL_PRIMARY
except Exception:  # pragma: no cover - defensive fallback
    import os as _os
    CLAUDE_MODEL_PRIMARY = _os.environ.get("CLAUDE_MODEL_PRIMARY", "claude-sonnet-4-6")


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
        f"major IPO upcoming {_cur_year()} sector stocks rally",
        f"SpaceX IPO space sector stocks rally {_cur_year()}",
        f"pre-IPO sector momentum beneficiaries {_cur_year()}",
        f"Stripe Klarna IPO fintech sector impact {_cur_year()}",
    ],
    "regulatory_catalyst": [
        f"FDA approval upcoming biotech breakthrough {_cur_year()}",
        f"FCC approval satellite spectrum {_cur_year()}",
        f"nuclear SMR approval NRC regulatory {_cur_year()}",
        f"crypto Bitcoin ETF approval SEC {_cur_year()}",
        f"autonomous vehicles FSD regulatory approval {_cur_year()}",
    ],
    "geopolitical_tailwind": [
        f"defense budget increase NATO countries {_cur_year()}",
        f"space race government contracts awards {_cur_year()}",
        f"energy independence nuclear uranium policy {_cur_year()}",
        f"AI chips export controls beneficiaries {_cur_year()}",
    ],
    "tech_inflection": [
        f"AI deployment enterprise revenue monetization {_cur_year()}",
        f"agentic AI software spending acceleration {_cur_year()}",
        f"quantum computing commercial breakthrough {_cur_year()}",
        f"nuclear fusion power commercial milestone {_cur_year()}",
    ],
    "early_signals": [
        f"institutional investor interest emerging sector {_cur_year()}",
        f"analyst coverage initiation new sector theme {_cur_year()}",
        f"hedge fund positioning sector rotation {_cur_year()}",
        f"options unusual activity sector ETF {_cur_year()}",
    ],
}

# ── Sector-specific deep-dive queries ─────────────────────────────────────────
_SECTOR_QUERIES = {
    "space":         [f"space economy SpaceX IPO sector rally {_cur_year()}", f"satellite broadband DoD LEO contracts {_cur_year()}"],
    "nuclear":       [f"nuclear renaissance SMR approval utility contracts {_cur_year()}", f"uranium spot price AI data center demand {_cur_year()}"],
    "crypto":        [f"Bitcoin institutional inflows ETF approval {_cur_year()}", f"crypto regulation clarity SEC {_cur_year()}"],
    "ai":            [f"agentic AI enterprise software monetization {_cur_year()}", f"AI infrastructure capex hyperscaler spending {_cur_year()}"],
    "glp1":          [f"GLP-1 obesity drug market share expansion {_cur_year()}", f"weight loss drug supply chain beneficiaries {_cur_year()}"],
    "defense":       [f"NATO defense budget increase procurement {_cur_year()}", f"hypersonic missile autonomous systems contracts {_cur_year()}"],
    "fintech_em":    [f"fintech emerging markets growth credit penetration {_cur_year()}", f"Latin America digital banking NU DLO {_cur_year()}"],
    "cybersecurity": [f"cybersecurity enterprise spending AI threat {_cur_year()}", f"ransomware government mandates CRWD {_cur_year()}"],
    "semis":         [f"semiconductor AI chip demand TSMC NVDA supply {_cur_year()}", f"advanced packaging CoWoS AVGO ALAB {_cur_year()}"],
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
    queries = _SECTOR_QUERIES.get(sector, [f"{sector} stocks outlook momentum {_cur_year()}"])
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
            model=CLAUDE_MODEL_PRIMARY,
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
            model=CLAUDE_MODEL_PRIMARY,
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
