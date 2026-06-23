"""
investing/universe.py — ticker universe, sector/narrative maps, benchmarks and
ticker auto-detection. Pure data + regex; no heavy deps.
"""

from __future__ import annotations

import re
from typing import Optional

from . import config

BROAD_BENCHMARK = "SPY"

WATCHLIST = [
    "SPOT", "NVDA", "MSFT", "META", "AMZN", "AMD", "AVGO", "CRWD", "SNOW", "ADBE",
    "CRM", "NOW", "ORCL", "ANET", "AXON", "ISRG", "MCO", "TDG", "MELI", "APP",
    "MU", "ASML", "NKE", "LULU", "UBER", "TTD", "BABA", "NVO", "HOOD", "RACE",
    "CMG", "FTNT", "SNPS", "PATH", "RBRK", "NU", "SNAP", "TEM", "MARA", "MSTR",
    "ALAB", "LITE", "UNH", "IBM", "APH", "NOC", "CCJ", "UEC", "DNN", "UUUU",
    "SE", "GRAB", "TDOC", "PGY", "DECK", "USAR", "EOSE", "S", "DLO", "RYCEY",
    "SYNA", "GFS", "PRM", "PSIX", "BA", "RKLB", "ASTS", "LUNR", "PL", "RDW", "IRDM",
]

SECTOR_OF = {
    "NVDA": "AI/Semis", "AMD": "AI/Semis", "AVGO": "AI/Semis", "ALAB": "AI/Semis",
    "ASML": "AI/Semis", "MU": "AI/Semis", "SNPS": "AI/Semis", "GFS": "AI/Semis",
    "LITE": "AI/Semis", "SYNA": "AI/Semis", "APH": "AI/Semis",
    "MSFT": "Tech/Cloud", "CRM": "Tech/Cloud", "NOW": "Tech/Cloud", "ORCL": "Tech/Cloud",
    "ADBE": "Tech/Cloud", "SNOW": "Tech/Cloud", "IBM": "Tech/Cloud", "PATH": "Tech/Cloud",
    "RBRK": "Tech/Cloud", "S": "Tech/Cloud",
    "CRWD": "Cybersecurity", "FTNT": "Cybersecurity",
    "ANET": "Networking",
    "META": "Social/Ads", "SNAP": "Social/Ads", "TTD": "Social/Ads",
    "AMZN": "E-commerce", "MELI": "E-commerce", "SE": "E-commerce",
    "BABA": "E-commerce", "GRAB": "E-commerce",
    "APP": "AI Apps", "TEM": "AI Apps", "SPOT": "AI Apps",
    "MSTR": "Crypto", "MARA": "Crypto", "HOOD": "Crypto",
    "NKE": "Consumer", "LULU": "Consumer", "RACE": "Consumer",
    "CMG": "Consumer", "DECK": "Consumer", "UBER": "Consumer",
    "ISRG": "Healthcare", "UNH": "Healthcare", "TDOC": "Healthcare", "NVO": "Healthcare",
    "MCO": "Financial", "NU": "Financial", "DLO": "Financial", "PGY": "Financial",
    "NOC": "Defense", "TDG": "Defense", "AXON": "Defense",
    "KTOS": "Defense", "LMT": "Defense", "RTX": "Defense", "GD": "Defense",
    "AVAV": "Defense", "LHX": "Defense", "HII": "Defense",
    "RKLB": "Space/Defense", "ASTS": "Space/Defense", "LUNR": "Space/Defense",
    "PL": "Space/Defense", "RDW": "Space/Defense", "IRDM": "Space/Defense",
    "CCJ": "Nuclear/Energy", "UEC": "Nuclear/Energy", "DNN": "Nuclear/Energy",
    "UUUU": "Nuclear/Energy", "EOSE": "Nuclear/Energy",
    "RYCEY": "Aerospace", "BA": "Aerospace",
}

SECTOR_BENCHMARK = {
    "AI/Semis": "SMH", "Tech/Cloud": "XLK", "Cybersecurity": "CIBR",
    "Networking": "XLK", "Social/Ads": "XLC", "E-commerce": "XLY",
    "AI Apps": "XLK", "Crypto": "BITQ", "Consumer": "XLY", "Healthcare": "XLV",
    "Financial": "XLF", "Defense": "ITA", "Space/Defense": "ARKX",
    "Nuclear/Energy": "URA", "Aerospace": "ITA",
}

NARRATIVE_OF = {
    "MSTR": "Crypto", "MARA": "Crypto", "HOOD": "Crypto",
    "NVDA": "AI", "AMD": "AI", "ALAB": "AI", "MU": "AI", "AVGO": "AI",
    "CCJ": "Nuclear", "UEC": "Nuclear", "DNN": "Nuclear", "UUUU": "Nuclear",
    "NOC": "Defense", "TDG": "Defense", "AXON": "Defense", "BA": "Defense",
    "RKLB": "Space", "ASTS": "Space", "LUNR": "Space", "PL": "Space",
    "NVO": "GLP-1", "ISRG": "MedTech",
}

# ETFs known to the universe (for asset-type tagging)
KNOWN_ETFS = set(SECTOR_BENCHMARK.values()) | {"SPY", "QQQ", "IWM"}


def sector_of(ticker: str) -> str:
    return SECTOR_OF.get(ticker.upper(), "Other")


def narrative_of(ticker: str) -> str:
    return NARRATIVE_OF.get(ticker.upper(), sector_of(ticker))


def sector_benchmark(ticker: str) -> Optional[str]:
    return SECTOR_BENCHMARK.get(sector_of(ticker))


def is_known_ticker(ticker: str) -> bool:
    return ticker.upper() in WATCHLIST or ticker.upper() in KNOWN_ETFS


# ── Auto-detection ───────────────────────────────────────────────────────────────
_CASHTAG = re.compile(r"\$([A-Za-z]{1,5})\b")
_WORD = re.compile(r"\b([A-Z]{2,5})\b")


def detect_tickers(text: str, max_tickers: int = config.MAX_TICKERS_PER_MESSAGE) -> list[str]:
    """Detect up to ``max_tickers`` tickers. ``$NVDA`` style is always honored;
    bare uppercase words only count when they're unambiguous watchlist tickers.
    Order is preserved and duplicates removed; never silently drops beyond the cap
    without the caller knowing (returns the capped list — caller reports the rest).
    """
    found: list[str] = []

    def _add(sym: str):
        sym = sym.upper()
        if sym not in found:
            found.append(sym)

    for m in _CASHTAG.finditer(text):
        _add(m.group(1))
    for m in _WORD.finditer(text):
        if m.group(1) in WATCHLIST:
            _add(m.group(1))

    return found[:max_tickers]


def detect_tickers_overflow(text: str, max_tickers: int = config.MAX_TICKERS_PER_MESSAGE) -> list[str]:
    """Tickers detected beyond the cap, so the caller can tell the user explicitly."""
    seen: list[str] = []
    for m in _CASHTAG.finditer(text):
        s = m.group(1).upper()
        if s not in seen:
            seen.append(s)
    for m in _WORD.finditer(text):
        if m.group(1) in WATCHLIST and m.group(1) not in seen:
            seen.append(m.group(1))
    return seen[max_tickers:]


# Common company-name → ticker aliases, so `/wejscie nvidia` works like `/wejscie NVDA`.
COMPANY_ALIASES = {
    "nvidia": "NVDA", "microsoft": "MSFT", "meta": "META", "facebook": "META",
    "amazon": "AMZN", "apple": "AAPL", "google": "GOOGL", "alphabet": "GOOGL",
    "tesla": "TSLA", "microstrategy": "MSTR", "strategy": "MSTR", "palantir": "PLTR",
    "broadcom": "AVGO", "micron": "MU", "netflix": "NFLX", "spotify": "SPOT",
    "uber": "UBER", "snowflake": "SNOW", "crowdstrike": "CRWD", "salesforce": "CRM",
    "servicenow": "NOW", "oracle": "ORCL", "adobe": "ADBE", "arista": "ANET",
    "intuitive": "ISRG", "mercadolibre": "MELI", "applovin": "APP", "rocketlab": "RKLB",
    "rocket": "RKLB", "asml": "ASML", "cameco": "CCJ", "novonordisk": "NVO",
    "ferrari": "RACE", "chipotle": "CMG", "lululemon": "LULU", "nike": "NKE",
    "boeing": "BA", "marathon": "MARA", "robinhood": "HOOD", "coinbase": "COIN",
}

_RISK_RE = re.compile(r"risk\s*=\s*([0-9]*\.?[0-9]+)", re.I)
_AMOUNT_RE = re.compile(r"\b(\d{3,})\b")
_TICKER_TOKEN = re.compile(r"^[A-Za-z]{1,5}$")


def parse_entry_command(text: str, max_tickers: int = config.MAX_TICKERS_PER_MESSAGE) -> dict:
    """Parse `/wejscie` text into {tickers, amount, risk, overflow, rejected}.

    Rules: `risk=` and a >=3-digit number are pulled out first. Remaining tokens
    become tickers only if they are a whole 1-5 letter symbol (``$NVDA``/``NVDA``)
    or a known company-name alias (``nvidia`` -> ``NVDA``). 6+ letter words that
    aren't aliases are reported in ``rejected`` (never silently turned into a
    garbage substring ticker)."""
    text = text or ""
    risk = None
    m = _RISK_RE.search(text)
    if m:
        risk = float(m.group(1))
        text = (text[:m.start()] + " " + text[m.end():])

    amount = None
    am = _AMOUNT_RE.search(text)
    if am:
        amount = float(am.group(1))
        text = (text[:am.start()] + " " + text[am.end():])

    seen: list[str] = []
    rejected: list[str] = []
    for raw in text.split():
        tok = raw.strip().lstrip("$")
        if not tok:
            continue
        alias = COMPANY_ALIASES.get(tok.lower())
        if alias:
            sym = alias
        elif _TICKER_TOKEN.match(tok):
            sym = tok.upper()
        else:
            if tok.lower() not in (r.lower() for r in rejected):
                rejected.append(tok)
            continue
        if sym not in seen:
            seen.append(sym)

    return {
        "tickers": seen[:max_tickers],
        "overflow": seen[max_tickers:],
        "amount": amount,
        "risk": risk,
        "rejected": rejected,
    }
