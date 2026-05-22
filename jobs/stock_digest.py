"""
jobs/stock_digest.py — Full stock analysis digest for Sebol bot.

Fetches price/fundamentals via yfinance, news + insider via Tavily, analysis via Claude.
Scheduled Mon-Fri at 13:00 UTC to post to #inwestowanie (C0B5LA4Q064).
"""

import os
import json
import re
import logging
import datetime
import time as _time
from collections import Counter

# Capital flow — imported lazily to avoid circular imports at module level
_capital_flow = None

def _get_capital_flow():
    global _capital_flow
    if _capital_flow is None:
        try:
            import jobs.capital_flow as _capital_flow
        except Exception:
            pass
    return _capital_flow

import requests as _requests
import pandas as pd
import numpy as np
import yfinance as yf

import _ctx

logger = logging.getLogger(__name__)

# ── Tavily optional import ────────────────────────────────────────────────────
try:
    from tavily import TavilyClient
    _TAVILY_KEY = os.environ.get("TAVILY_API_KEY", "")
    _tavily = TavilyClient(api_key=_TAVILY_KEY) if _TAVILY_KEY else None
except ImportError:
    _tavily = None

# ── Watchlist ─────────────────────────────────────────────────────────────────
WATCHLIST = [
    "SPOT", "NVDA", "MSFT", "META", "AMZN", "AMD", "AVGO", "CRWD", "SNOW", "ADBE",
    "CRM", "NOW", "ORCL", "ANET", "AXON", "ISRG", "MCO", "TDG", "MELI", "APP",
    "MU", "ASML", "NKE", "LULU", "UBER", "TTD", "BABA", "NVO", "HOOD", "RACE",
    "CMG", "FTNT", "SNPS", "PATH", "RBRK", "NU", "SNAP", "TEM", "MARA", "MSTR",
    "ALAB", "LITE", "UNH", "IBM", "APH", "NOC", "CCJ", "UEC", "DNN", "UUUU",
    "SE", "GRAB", "TDOC", "PGY", "DECK", "USAR", "EOSE", "S", "DLO", "RYCEY",
    "SYNA", "GFS", "PRM", "PSIX", "BA",
    # Space & Defense
    "RKLB", "ASTS", "LUNR", "PL", "RDW", "IRDM",
]

# ── Sector mapping ────────────────────────────────────────────────────────────
TICKER_SECTORS = {
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
    "RKLB": "Space/Defense", "ASTS": "Space/Defense", "LUNR": "Space/Defense",
    "PL": "Space/Defense", "RDW": "Space/Defense", "IRDM": "Space/Defense",
    "CCJ": "Nuclear/Energy", "UEC": "Nuclear/Energy", "DNN": "Nuclear/Energy",
    "UUUU": "Nuclear/Energy", "EOSE": "Nuclear/Energy",
    "RYCEY": "Aerospace", "BA": "Aerospace",
    "USAR": "Other", "PRM": "Other", "PSIX": "Other",
}


# ── RSI ───────────────────────────────────────────────────────────────────────
def _calc_rsi(closes, period=14):
    deltas = pd.Series(closes).diff().dropna()
    gains = deltas.clip(lower=0).rolling(period).mean()
    losses = (-deltas.clip(upper=0)).rolling(period).mean()
    rs = gains / losses.replace(0, float("nan"))
    return float(100 - 100 / (1 + rs.iloc[-1]))


# ── Technical levels (MA50/MA200, golden/death cross, support) ────────────────
def _calc_technicals(closes: list) -> dict:
    s = pd.Series(closes)
    price = s.iloc[-1]

    ma50 = ma200 = None
    above_ma50 = above_ma200 = None
    if len(s) >= 50:
        ma50 = float(s.rolling(50).mean().iloc[-1])
        above_ma50 = bool(price > ma50)
    if len(s) >= 200:
        ma200 = float(s.rolling(200).mean().iloc[-1])
        above_ma200 = bool(price > ma200)

    golden_cross = death_cross = False
    if len(s) >= 205:
        ma50_s = s.rolling(50).mean()
        ma200_s = s.rolling(200).mean()
        for i in range(-5, -1):
            if (ma50_s.iloc[i - 1] <= ma200_s.iloc[i - 1] and
                    ma50_s.iloc[i] > ma200_s.iloc[i]):
                golden_cross = True
            elif (ma50_s.iloc[i - 1] >= ma200_s.iloc[i - 1] and
                    ma50_s.iloc[i] < ma200_s.iloc[i]):
                death_cross = True

    support_30d = float(s.iloc[-30:].min()) if len(s) >= 30 else None
    pct_from_support = round((price - support_30d) / support_30d * 100, 1) if support_30d else None

    return {
        "ma50": round(ma50, 2) if ma50 else None,
        "ma200": round(ma200, 2) if ma200 else None,
        "above_ma50": above_ma50,
        "above_ma200": above_ma200,
        "golden_cross": golden_cross,
        "death_cross": death_cross,
        "support_30d": round(support_30d, 2) if support_30d else None,
        "pct_from_support": pct_from_support,
    }


# ── Earnings proximity ────────────────────────────────────────────────────────
def _check_earnings_soon(ticker_obj) -> int | None:
    """Returns days until next earnings if within 14 days, else None."""
    try:
        cal = ticker_obj.calendar
        if cal is None:
            return None
        today = datetime.date.today()
        # yfinance returns calendar as a DataFrame or dict depending on version
        if hasattr(cal, "columns"):
            if "Earnings Date" in cal.columns:
                ed = cal["Earnings Date"].iloc[0]
            else:
                return None
        elif isinstance(cal, dict):
            ed = cal.get("Earnings Date")
            if isinstance(ed, (list, tuple)) and ed:
                ed = ed[0]
        else:
            return None
        if ed is None:
            return None
        ts = pd.Timestamp(ed)
        days = (ts.date() - today).days
        return days if 0 <= days <= 14 else None
    except Exception:
        return None


# ── Seasonality ───────────────────────────────────────────────────────────────
def _seasonality_note() -> str | None:
    month = datetime.datetime.now().month
    if month in (5, 6, 7, 8, 9):
        return "Sell in May — maj-wrzesień historycznie słabszy dla tech"
    if month in (10, 11, 12):
        return "Q4 rally — październik-grudzień sprzyja akcjom"
    if month == 1:
        return "Efekt stycznia — sprzyja small caps"
    return None


# ── QQQ 30-day return (cached per digest run) ─────────────────────────────────
def _fetch_qqq_30d() -> float | None:
    try:
        hist = yf.Ticker("QQQ").history(period="35d")
        if len(hist) >= 30:
            return round((float(hist["Close"].iloc[-1]) / float(hist["Close"].iloc[-30]) - 1) * 100, 2)
    except Exception as e:
        logger.warning("QQQ fetch error: %s", e)
    return None


# ── Asset category mapping ────────────────────────────────────────────────────
_CATEGORY_MAP = {
    "CRYPTO_PROXY":          ["MSTR", "MARA", "HOOD"],
    "URANIUM":               ["UEC", "DNN", "UUUU", "CCJ"],
    "DEFENSE":               ["NOC", "BA", "TDG"],
    "SPACE_DEFENSE":         ["RKLB", "ASTS", "LUNR", "PL", "RDW", "IRDM"],
    "BIOTECH_HEALTH":        ["TEM", "ISRG", "UNH", "NVO", "TDOC"],
    "EMERGING_MARKETS":      ["BABA", "SE", "GRAB", "MELI", "NU", "DLO"],
    "CONSUMER_DISCRETIONARY":["LULU", "NKE", "DECK", "CMG", "RACE"],
}
# Tickers with tariff/supply-chain risk beyond CONSUMER_DISCRETIONARY
_TARIFF_RISK_TICKERS = {"LULU", "NKE", "DECK", "RACE", "SNAP", "AAPL", "CMG"}

ASSET_CATEGORY: dict[str, str] = {}
for _cat, _tickers in _CATEGORY_MAP.items():
    for _t in _tickers:
        ASSET_CATEGORY[_t] = _cat

CATEGORY_LABELS = {
    "CRYPTO_PROXY":          "₿ Crypto Proxy",
    "URANIUM":               "☢️ Uranium",
    "DEFENSE":               "🛡 Defense",
    "SPACE_DEFENSE":         "🚀 Space/Defense",
    "BIOTECH_HEALTH":        "💊 Biotech/Health",
    "EMERGING_MARKETS":      "🌍 Emerging Markets",
    "CONSUMER_DISCRETIONARY":"🛍 Consumer",
    "STANDARD_TECH":         "💻 Tech",
}


def get_category(ticker: str) -> str:
    return ASSET_CATEGORY.get(ticker, "STANDARD_TECH")


# ── BTC data (for CRYPTO_PROXY) ───────────────────────────────────────────────
def _fetch_btc_data() -> dict:
    try:
        btc = yf.Ticker("BTC-USD")
        info = btc.info
        price = info.get("regularMarketPrice") or info.get("currentPrice") or 0.0
        hist  = btc.history(period="1y")
        closes = hist["Close"].tolist()
        rsi    = round(_calc_rsi(closes), 1) if len(closes) >= 15 else None
        ma200  = round(float(pd.Series(closes).rolling(200).mean().iloc[-1]), 0) if len(closes) >= 200 else None
        above_ma200 = (closes[-1] > ma200) if (closes and ma200) else None
        bullish = bool(above_ma200 and (rsi is None or rsi < 70))
        return {
            "price":        round(price, 0),
            "rsi":          rsi,
            "ma200":        ma200,
            "above_ma200":  above_ma200,
            "bullish":      bullish,
        }
    except Exception as e:
        logger.warning("BTC data error: %s", e)
        return {}


# ── Quarterly fundamental trends ─────────────────────────────────────────────
def _fetch_quarterly_trends(ticker_obj) -> dict:
    """
    Returns revenue deceleration, margin decline, and deterioration flag.
    All from yfinance quarterly financials — no extra API calls.
    """
    result = {
        "revenue_decelerating": False,
        "margin_declining":     False,
        "deteriorating":        False,
        "revenue_growth_qtrs":  [],
        "gross_margin_qtrs":    [],
        "details":              "",
    }
    try:
        try:
            q = ticker_obj.quarterly_income_stmt
        except Exception:
            q = getattr(ticker_obj, "quarterly_financials", None)
        if q is None or q.empty:
            return result

        # ── Revenue deceleration (QoQ growth rate trend) ──
        rev_row = None
        for lbl in ("Total Revenue", "TotalRevenue", "Revenue"):
            if lbl in q.index:
                rev_row = q.loc[lbl].dropna().sort_index()
                break
        if rev_row is not None and len(rev_row) >= 3:
            vals = rev_row.iloc[-4:].values if len(rev_row) >= 4 else rev_row.values
            growth = [
                round((vals[i] - vals[i - 1]) / abs(vals[i - 1]) * 100, 1)
                for i in range(1, len(vals))
                if vals[i - 1] != 0
            ]
            result["revenue_growth_qtrs"] = growth
            if len(growth) >= 2:
                result["revenue_decelerating"] = all(
                    growth[i] < growth[i - 1] for i in range(1, len(growth))
                )

        # ── Gross margin decline (2+ consecutive quarters falling) ──
        gp_row = rev_match = None
        for lbl in ("Gross Profit", "GrossProfit"):
            if lbl in q.index:
                gp_row = q.loc[lbl].dropna().sort_index()
                break
        for lbl in ("Total Revenue", "TotalRevenue", "Revenue"):
            if lbl in q.index:
                rev_match = q.loc[lbl].dropna().sort_index()
                break
        if gp_row is not None and rev_match is not None:
            common = gp_row.index.intersection(rev_match.index)[-4:]
            if len(common) >= 3:
                margins = [
                    round(float(gp_row[c]) / float(rev_match[c]) * 100, 1)
                    for c in common if rev_match[c] != 0
                ]
                result["gross_margin_qtrs"] = margins
                declines = sum(1 for i in range(1, len(margins)) if margins[i] < margins[i - 1])
                result["margin_declining"] = declines >= 2

        signals = sum([result["revenue_decelerating"], result["margin_declining"]])
        result["deteriorating"] = signals >= 2
        if result["deteriorating"]:
            result["details"] = (
                f"Revenue zwalnia {result['revenue_growth_qtrs']} + "
                f"marże brutto {result['gross_margin_qtrs']}"
            )
        elif result["revenue_decelerating"]:
            result["details"] = f"Revenue QoQ: {result['revenue_growth_qtrs']}"
        elif result["margin_declining"]:
            result["details"] = f"Marże brutto: {result['gross_margin_qtrs']}"

    except Exception as e:
        logger.warning("Quarterly trends error: %s", e)
    return result


# ── Sector context cache (one call per sector per digest run) ─────────────────
_sector_cache: dict[str, str] = {}


def _fetch_sector_context(sector: str) -> str:
    if _tavily is None or not sector:
        return ""
    if sector in _sector_cache:
        return _sector_cache[sector]
    try:
        r = _tavily.search(f"{sector} sector outlook headwinds tailwinds 2026", max_results=2)
        ctx = " ".join((x.get("content") or "")[:150] for x in (r.get("results") or []))[:300]
        _sector_cache[sector] = ctx
        return ctx
    except Exception as e:
        logger.warning("Sector context error for %s: %s", sector, e)
        return ""


# ── Tavily: category-aware queries ────────────────────────────────────────────
def _fetch_news(ticker: str, category: str = "STANDARD_TECH") -> list:
    """Primary Tavily call — category-aware, includes guidance signal."""
    if _tavily is None:
        return []
    _queries = {
        "CRYPTO_PROXY":          f"{ticker} bitcoin holdings NAV premium discount 2026",
        "URANIUM":               f"uranium spot price 2026 {ticker} nuclear SMR contracts production",
        "DEFENSE":               f"{ticker} defense contracts NATO budget 2026 backlog",
        "SPACE_DEFENSE":         f"{ticker} launch manifest contracts NASA DoD 2026",
        "BIOTECH_HEALTH":        f"{ticker} FDA pipeline GLP-1 approval clinical trial 2026",
        "EMERGING_MARKETS":      f"{ticker} regulatory risk USD currency geopolitical 2026",
        "CONSUMER_DISCRETIONARY":f"{ticker} same store sales inventory comparable sales 2026",
        "STANDARD_TECH":         f"{ticker} stock news insider guidance earnings beat miss 2026",
    }
    query = _queries.get(category, _queries["STANDARD_TECH"])
    try:
        results  = _tavily.search(query=query, max_results=3)
        snippets = []
        for r in (results.get("results") or [])[:3]:
            snippets.append({
                "title":   r.get("title", ""),
                "content": (r.get("content") or "")[:200],
            })
        return snippets
    except Exception as e:
        logger.warning("Tavily error for %s: %s", ticker, e)
        return []


def _fetch_extra_signals(ticker: str, category: str) -> dict:
    """Additional targeted Tavily calls for guidance, tariffs, US sales."""
    if _tavily is None:
        return {}
    out = {}

    def _search(key: str, query: str, chars: int = 300):
        try:
            r = _tavily.search(query, max_results=2)
            out[key] = " ".join((x.get("content") or "")[:150] for x in (r.get("results") or []))[:chars]
        except Exception as e:
            logger.warning("Extra signal %s for %s: %s", key, ticker, e)

    # Guidance — all tickers
    _search("guidance", f"{ticker} guidance lowered raised outlook forecast 2026")

    # Tariffs — consumer & other exposed tickers
    if category == "CONSUMER_DISCRETIONARY" or ticker in _TARIFF_RISK_TICKERS:
        _search("tariffs", f"{ticker} tariffs supply chain China import costs 2026")

    # US domestic sales — consumer focused
    if category == "CONSUMER_DISCRETIONARY":
        _search("us_sales", f"{ticker} US domestic sales revenue decline slowdown 2026")

    return out


# ── Claude system prompts per category ───────────────────────────────────────
_BASE_JSON_SCHEMA = (
    'Odpowiadasz TYLKO w JSON bez żadnego tekstu przed/po:\n'
    '{"fundamentals_score": 1-5, "timing_score": 1-5, "macro_risk": "low"/"medium"/"high",\n'
    ' "reasoning": "max 2 zdania po polsku",\n'
    ' "verdict": "KUP"/"CZEKAJ"/"OMIJAJ",\n'
    ' "confidence": "LOW"/"MEDIUM"/"HIGH",\n'
    ' "bull_case": "1 zdanie — co musi się wydarzyć żeby spółka urosła",\n'
    ' "bear_case": "1 zdanie — co może pójść źle"\n'
    '}\n'
    'confidence=HIGH gdy wszystkie sygnały zgodne; MEDIUM gdy większość zgodna; '
    'LOW gdy sprzeczne sygnały lub brak kluczowych danych.\n'
    'bull_case i bear_case ZAWSZE wymagane — wymuszają myślenie w obu kierunkach.'
)

_SYSTEM_PROMPTS = {
    "CRYPTO_PROXY": (
        "Jesteś analitykiem krypto i crypto-equity. Ta spółka to CRYPTO PROXY — "
        "jej wycena jest zdeterminowana przez Bitcoina, NIE przez P/E ani marże.\n"
        "Oceń wyłącznie:\n"
        "1) Trend BTC: czy BTC > MA200? RSI BTC < 70 = nie przegrzany\n"
        "2) Lewar: ile BTC na akcję (NAV), premium/discount do NAV — wysoki premium = ryzyko\n"
        "3) Scenario: historycznie MSTR rośnie 3-4x gdy BTC x2\n"
        "4) Timing RSI spółki + short interest\n"
        "KUP = BTC bullish (>MA200) + RSI BTC < 70 + rozsądny premium do NAV\n"
        "CZEKAJ = BTC sideways lub wysoki premium do NAV\n"
        "OMIJAJ = BTC poniżej MA200 lub RSI BTC > 80\n"
        "W reasoning ZAWSZE podaj aktualny kurs BTC i czy jest bullish/bearish.\n"
        + _BASE_JSON_SCHEMA
    ),
    "URANIUM": (
        "Jesteś analitykiem surowcowym specjalizującym się w uranie i energetyce jądrowej.\n"
        "NIE oceniaj przez standardowe P/E — uranium miners mają cykliczną rentowność.\n"
        "Oceń przez:\n"
        "1) Spot price uranu (trend wzrostowy = tailwind)\n"
        "2) Pipeline kontraktów długoterminowych spółki\n"
        "3) Ekspozycja na nuclear renaissance / SMR (Small Modular Reactors)\n"
        "4) Polityczne tailwindy: dekarbonizacja, AI data centers = wzrost popytu na prąd\n"
        "5) Koszty produkcji vs spot price (czy spółka jest profitable przy aktualnych cenach)\n"
        "Geopolityka = TAILWIND dla nuclear, nie ryzyko.\n"
        + _BASE_JSON_SCHEMA
    ),
    "DEFENSE": (
        "Jesteś analitykiem sektora obronnego.\n"
        "Oceń przez:\n"
        "1) Cykl obronny: budżety NATO, wydatki rządowe (rosnące = tailwind)\n"
        "2) Backlog kontraktów i visibility przychodów\n"
        "3) Geopolityka jako TAILWIND — nie ryzyko\n"
        "4) Wycena vs peers (P/E w defense zwykle 15-25x = normalne)\n"
        "5) Dywidenda i buybacki jako element zwrotu\n"
        "Nie karz spółki za 'wysokie' P/E jeśli backlog i cykl uzasadniają premię.\n"
        + _BASE_JSON_SCHEMA
    ),
    "SPACE_DEFENSE": (
        "Jesteś analitykiem sektora kosmicznego i new-space defense.\n"
        "WIĘKSZOŚĆ tych spółek to pre-profit lub early-revenue — NIE oceniaj przez P/E.\n"
        "Używaj EV/Revenue jako głównej metryki wyceny.\n"
        "Oceń przez:\n"
        "1) Launch manifest / backlog kontraktów (NASA, DoD, komercyjne) — kluczowy wskaźnik\n"
        "2) Revenue mix: rządowe (stabilne, przewidywalne) vs komercyjne (wyższy potencjał)\n"
        "3) Burn rate i runway gotówkowy — ile kwartałów bez dofinansowania\n"
        "4) Kamienie milowe technologiczne (udane misje = re-rating w górę, awarie = w dół)\n"
        "5) Konkurencja SpaceX jako benchmark — czy spółka ma realną niszę\n"
        "6) Tailwindy: Low Earth Orbit economy, satellite broadband, DoD 'proliferated LEO'\n"
        "KUP = rosnący backlog + rządowy kontrakt zakotwiczony + burn rate pod kontrolą\n"
        "CZEKAJ = dobre perspektywy ale brak konkretnych kontraktów lub wysoki burn\n"
        "OMIJAJ = burn rate > 4 kwartały runway lub brak realnej niszy vs SpaceX\n"
        + _BASE_JSON_SCHEMA
    ),
    "BIOTECH_HEALTH": (
        "Jesteś analitykiem sektora healthcare i biotech.\n"
        "Oceń przez:\n"
        "1) Pipeline produktowy i FDA approvals (catalyst risk/opportunity)\n"
        "2) Dla NVO: ekspozycja na GLP-1 market share, competition from LLY\n"
        "3) Dla insurerów (UNH): medical loss ratio, regulatory risk\n"
        "4) Dla telehealth (TDOC): unit economics, customer retention\n"
        "5) Generics risk dla spółek patentowych\n"
        "6) Standardowe fundamenty tam gdzie mają sens\n"
        + _BASE_JSON_SCHEMA
    ),
    "EMERGING_MARKETS": (
        "Jesteś analitykiem rynków wschodzących.\n"
        "Oceń przez:\n"
        "1) Fundamenty spółki (wzrost, marże, wycena)\n"
        "2) Ryzyko walutowe USD (silny USD = headwind dla EM)\n"
        "3) Ryzyko regulacyjne kraju:\n"
        "   - Chiny (BABA, inne): ryzyko regulacyjne CCP, delisting risk = osobna flaga\n"
        "   - Azja SEA (SE, GRAB): ryzyko niższe, ale polityka lokalna\n"
        "   - LATAM (MELI, NU, DLO): ryzyko walutowe, inflation\n"
        "4) Geopolityka (de-coupling USA-Chiny = dodatkowe ryzyko dla spółek chińskich)\n"
        "W reasoning zaznacz zawsze ryzyko kraju jako osobną wzmiankę.\n"
        + _BASE_JSON_SCHEMA
    ),
    "CONSUMER_DISCRETIONARY": (
        "Jesteś analitykiem sektora consumer discretionary.\n"
        "Oceniaj przez:\n"
        "1) Same-store/comparable sales growth — to ważniejszy wskaźnik niż YoY revenue\n"
        "2) Average selling price trend — czy obniżają ceny żeby sprzedać? (zły znak)\n"
        "3) Poziom zapasów — wysokie zapasy = zła sprzedaż, przyszłe wyprzedaże\n"
        "4) Cła i supply chain (szczególnie produkcja w Chinach/Azji)\n"
        "5) Siła konsumenta USA (core target market)\n"
        "6) Czy problemy są firmowe czy sektorowe (cały retail słaby = inny kontekst)\n"
        "Dla RACE (Ferrari): oceniaj przez order book, waitlist, pricing power (inna liga niż NKE/LULU)\n"
        "Dla CMG: comparable restaurant sales, traffic vs ticket size\n"
        + _BASE_JSON_SCHEMA
    ),
    "STANDARD_TECH": (
        "Jesteś analitykiem inwestycyjnym. Analizujesz spółki pod kątem:\n"
        "1) Fundamentów (wycena vs sektor, wzrost, marże, EV/EBITDA)\n"
        "2) Timingu (RSI > 75 = przegrzana, MA50/MA200, golden/death cross, blisko ATH)\n"
        "3) Ryzyka makro (Fed, VIX, sezonowość, short interest, insider activity)\n"
        "4) Relative strength vs QQQ (>20% w 30d = prawdopodobnie przegrzana)\n"
        "Uwzględnij czy spółka ma earnings w ciągu 14 dni.\n"
        + _BASE_JSON_SCHEMA
    ),
}

_FALLBACK_ANALYSIS = {
    "fundamentals_score": 3,
    "timing_score": 3,
    "macro_risk": "medium",
    "reasoning": "Brak analizy.",
    "verdict": "CZEKAJ",
    "confidence": "LOW",
    "bull_case": "Brak danych.",
    "bear_case": "Brak danych.",
}


def _build_user_msg(ticker: str, fin: dict, news: list, category: str) -> str:
    tech    = fin.get("technicals", {})
    qtrd    = fin.get("quarterly_trends", {})
    extras  = fin.get("extra_signals", {})
    sector  = fin.get("sector", "")

    news_text = (
        "\n\nNewsy/kontekst:\n" + "\n".join(f"- {n['title']}: {n['content']}" for n in news)
    ) if news else "\n\n(brak newsów)"

    ma_status = []
    if tech.get("above_ma50")  is not None: ma_status.append("powyżej MA50"  if tech["above_ma50"]  else "poniżej MA50")
    if tech.get("above_ma200") is not None: ma_status.append("powyżej MA200" if tech["above_ma200"] else "poniżej MA200")
    if tech.get("golden_cross"): ma_status.append("GOLDEN CROSS")
    if tech.get("death_cross"):  ma_status.append("DEATH CROSS")

    base = (
        f"Ticker: {ticker} | Kategoria: {category} | Sektor: {sector}\n"
        f"Cena: ${fin.get('price','N/A')} ({fin.get('change_pct','N/A'):+}%) | "
        f"52w ATH: ${fin.get('high52w','N/A')} ({fin.get('pct_from_high','N/A')}% od ATH)\n"
        f"RSI-14: {fin.get('rsi','N/A')} | MA: {', '.join(ma_status) or 'N/A'}\n"
        f"Short interest: {fin.get('shortPercentOfFloat','N/A')} | "
        f"RS vs QQQ 30d: {fin.get('rs_vs_qqq','N/A')}% | "
        f"Earnings za: {fin.get('earnings_days','N/A')} dni\n"
        f"Sezonowość: {fin.get('seasonality','brak')}\n"
    )

    # ── Fundamentals by category ──
    if category == "CRYPTO_PROXY":
        btc = fin.get("btc_data", {})
        base += (
            f"\n--- BTC DATA ---\n"
            f"BTC: ${btc.get('price','N/A')} | RSI_BTC: {btc.get('rsi','N/A')} | "
            f"BTC>MA200: {btc.get('above_ma200','N/A')} | Bullish: {btc.get('bullish','N/A')}\n"
        )
        if fin.get("mstr_nav"):
            nav = fin["mstr_nav"]
            base += (
                f"MSTR BTC/akcję≈{nav.get('btc_per_share_approx','N/A')} | "
                f"NAV/akcję≈${nav.get('nav_per_share_approx','N/A')}\n"
                f"NAV kontekst: {nav.get('tavily_context','brak')}\n"
            )
    else:
        base += (
            f"PE: {fin.get('trailingPE','N/A')} | Fwd PE: {fin.get('forwardPE','N/A')} | "
            f"EV/EBITDA: {fin.get('enterpriseToEbitda','N/A')}\n"
            f"Marża netto: {fin.get('profitMargins','N/A')} | Rev growth YoY: {fin.get('revenueGrowth','N/A')}\n"
        )

    # ── Quarterly trend data ──
    if qtrd.get("details"):
        deteriorating_label = "⚠️ DETERIORATING FUNDAMENTALS" if qtrd.get("deteriorating") else ""
        base += (
            f"\n--- QUARTERLY TRENDS ---\n"
            f"Revenue deceleration: {qtrd.get('revenue_decelerating')} | "
            f"Margin decline 2+ qtrs: {qtrd.get('margin_declining')}\n"
            f"Rev growth Q/Q: {qtrd.get('revenue_growth_qtrs')} | "
            f"Gross margins: {qtrd.get('gross_margin_qtrs')}\n"
        )
        if deteriorating_label:
            base += f"{deteriorating_label}\n"

    # ── Extra signals: guidance, tariffs, US sales ──
    if extras.get("guidance"):
        base += f"\nGuidance/outlook: {extras['guidance'][:250]}\n"
    if extras.get("tariffs"):
        base += f"Tariff/supply chain risk: {extras['tariffs'][:200]}\n"
    if extras.get("us_sales"):
        base += f"US domestic sales: {extras['us_sales'][:200]}\n"

    # ── Sector context ──
    sector_ctx = fin.get("sector_context", "")
    if sector_ctx:
        base += f"\nKontekst sektora ({sector}): {sector_ctx[:200]}\n"

    # ── Capital flow signal ──
    flow = fin.get("sector_flow", "NEUTRAL")
    if flow != "NEUTRAL":
        flow_label = "🟢 SECTOR_INFLOW — kapitał napływa do tego sektora" if flow == "INFLOW" else "🔴 SECTOR_OUTFLOW — kapitał odpływa z tego sektora"
        base += f"\nCapital flow: {flow_label}\n"

    return base + news_text


def _claude_analyze(ticker: str, fin: dict, news: list, category: str = "STANDARD_TECH") -> dict:
    system   = _SYSTEM_PROMPTS.get(category, _SYSTEM_PROMPTS["STANDARD_TECH"])
    user_msg = _build_user_msg(ticker, fin, news, category)
    try:
        response = _ctx.claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=450,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = response.content[0].text.strip()
        m   = re.search(r"\{.*\}", raw, re.DOTALL)
        result = json.loads(m.group() if m else raw)
        # Ensure new fields exist even if model skipped them
        result.setdefault("confidence", "MEDIUM")
        result.setdefault("bull_case",  "Brak danych.")
        result.setdefault("bear_case",  "Brak danych.")
        return result
    except Exception as e:
        logger.warning("Claude analysis error for %s: %s", ticker, e)
        return dict(_FALLBACK_ANALYSIS)


# ── Public API ────────────────────────────────────────────────────────────────

def analyze_ticker(ticker: str, qqq_30d: float | None = None, btc_data: dict | None = None) -> dict:
    """Fetch data + news + Claude analysis for one ticker. Returns dict with all fields."""
    category   = get_category(ticker)
    ticker_obj = yf.Ticker(ticker)
    info       = ticker_obj.info

    # ── Price ──
    price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("ask") or 0.0
    if not price:
        raise ValueError(f"Ticker '{ticker}' nie istnieje lub yfinance nie zwrócił danych cenowych. Sprawdź symbol (np. TEM zamiast TEMPUS).")
    prev = info.get("previousClose") or info.get("regularMarketPreviousClose") or price
    change_pct = round((price - prev) / prev * 100, 2) if prev else 0.0

    high52w = info.get("fiftyTwoWeekHigh") or 0.0
    pct_from_high = round((price - high52w) / high52w * 100, 2) if high52w else None
    near_ath = bool(price >= 0.95 * high52w) if high52w else False

    # ── History-based indicators ──
    rsi = None
    technicals = {}
    hist_30d_return = None
    try:
        hist = ticker_obj.history(period="1y")
        closes = hist["Close"].tolist()
        if len(closes) >= 15:
            rsi = round(_calc_rsi(closes), 1)
        technicals = _calc_technicals(closes)
        if len(closes) >= 30:
            hist_30d_return = round((closes[-1] / closes[-30] - 1) * 100, 2)
    except Exception as e:
        logger.warning("History error for %s: %s", ticker, e)

    # ── RS vs QQQ ──
    rs_vs_qqq = None
    if hist_30d_return is not None and qqq_30d is not None:
        rs_vs_qqq = round(hist_30d_return - qqq_30d, 2)

    # ── Short interest ──
    short_pct = info.get("shortPercentOfFloat")
    if short_pct and short_pct < 1:
        short_pct = round(short_pct * 100, 1)
    elif short_pct:
        short_pct = round(short_pct, 1)

    # ── Earnings ──
    earnings_days = _check_earnings_soon(ticker_obj)

    fin = {
        "price": round(price, 2),
        "change_pct": change_pct,
        "high52w": round(high52w, 2) if high52w else None,
        "pct_from_high": pct_from_high,
        "near_ath": near_ath,
        "rsi": rsi,
        "technicals": technicals,
        "rs_vs_qqq": rs_vs_qqq,
        "shortPercentOfFloat": short_pct,
        "earnings_days": earnings_days,
        "seasonality": _seasonality_note(),
        "trailingPE": info.get("trailingPE"),
        "forwardPE": info.get("forwardPE"),
        "enterpriseToEbitda": info.get("enterpriseToEbitda"),
        "profitMargins": info.get("profitMargins"),
        "revenueGrowth": info.get("revenueGrowth"),
        "sector": TICKER_SECTORS.get(ticker, "Other"),
        "category": category,
    }

    # ── Quarterly fundamental trends (yfinance, no extra API cost) ──
    fin["quarterly_trends"] = _fetch_quarterly_trends(ticker_obj)

    # ── Sector context (cached per sector) ──
    fin["sector_context"] = _fetch_sector_context(fin["sector"])

    # ── Capital flow signal ──
    cf = _get_capital_flow()
    fin["sector_flow"] = cf.get_ticker_flow(ticker) if cf else "NEUTRAL"

    # ── Category-specific extras ──
    if category == "CRYPTO_PROXY":
        fin["btc_data"] = btc_data if btc_data is not None else _fetch_btc_data()
        if ticker == "MSTR" and _tavily:
            try:
                r = _tavily.search(
                    "MicroStrategy MSTR bitcoin holdings BTC per share NAV 2026",
                    max_results=3,
                )
                ctx = " ".join((x.get("content") or "")[:200] for x in (r.get("results") or []))[:500]
                shares    = info.get("sharesOutstanding") or 0
                btc_price = fin["btc_data"].get("price") or 0
                btc_held_approx = 214_400  # static estimate; Tavily context corrects Claude
                fin["mstr_nav"] = {
                    "btc_per_share_approx": round(btc_held_approx / shares, 4) if shares else None,
                    "nav_per_share_approx": round(btc_held_approx * btc_price / shares, 2) if shares and btc_price else None,
                    "tavily_context": ctx,
                }
            except Exception as e:
                logger.warning("MSTR NAV fetch error: %s", e)

    # ── Extra signals: guidance + tariffs + US sales ──
    fin["extra_signals"] = _fetch_extra_signals(ticker, category)

    news     = _fetch_news(ticker, category)
    analysis = _claude_analyze(ticker, fin, news, category)

    # ── Automatic score adjustments ──
    qtrd = fin["quarterly_trends"]
    if qtrd.get("deteriorating"):
        old = analysis.get("fundamentals_score", 3)
        analysis["fundamentals_score"] = max(1, old - 2)
        analysis["flags"] = analysis.get("flags", []) + ["⚠️ DETERIORATING FUNDAMENTALS"]
        logger.info("%s: fundamentals_score auto-reduced %d→%d (deteriorating qtrs)", ticker, old, analysis["fundamentals_score"])

    guidance_text = (fin["extra_signals"].get("guidance") or "").lower()
    if any(w in guidance_text for w in ("lowered", "cut", "reduced", "below", "obniżono", "obniżył")):
        analysis["flags"] = analysis.get("flags", []) + ["🔴 GUIDANCE OBNIŻONY"]
        old_v = analysis.get("verdict", "CZEKAJ")
        if old_v == "KUP":
            analysis["verdict"] = "CZEKAJ"
            logger.info("%s: verdict KUP→CZEKAJ due to lowered guidance", ticker)

    tariff_text = (fin["extra_signals"].get("tariffs") or "").lower()
    if any(w in tariff_text for w in ("tariff", "cło", "supply chain disruption", "higher costs", "import costs")):
        analysis["flags"] = analysis.get("flags", []) + ["⚠️ RYZYKO CEŁ/SUPPLY CHAIN"]

    # ── Capital flow timing adjustment ──
    flow = fin.get("sector_flow", "NEUTRAL")
    if flow == "INFLOW":
        old_t = analysis.get("timing_score", 3)
        analysis["timing_score"] = min(5, old_t + 1)
        analysis["flags"] = analysis.get("flags", []) + ["🟢 SECTOR_INFLOW"]
    elif flow == "OUTFLOW":
        old_t = analysis.get("timing_score", 3)
        analysis["timing_score"] = max(1, old_t - 1)
        analysis["flags"] = analysis.get("flags", []) + ["🔴 SECTOR_OUTFLOW"]

    return {**fin, "news": news, "analysis": analysis}


def _ticker_ma_str(tech: dict) -> str:
    parts = []
    if tech.get("above_ma50") is not None:
        parts.append("✅MA50" if tech["above_ma50"] else "❌MA50")
    if tech.get("above_ma200") is not None:
        parts.append("✅MA200" if tech["above_ma200"] else "❌MA200")
    if tech.get("golden_cross"):
        parts.append("🌟GoldenX")
    if tech.get("death_cross"):
        parts.append("💀DeathX")
    return " ".join(parts) if parts else "–"


def _ticker_flags(data: dict) -> list[str]:
    flags = []
    if data.get("near_ath"):
        flags.append(f"⚠️ Blisko ATH ({data.get('pct_from_high', '?')}%)")
    if data.get("earnings_days") is not None:
        flags.append(f"📅 Earnings za {data['earnings_days']} dni")
    short = data.get("shortPercentOfFloat")
    if short and short > 15:
        flags.append(f"⚠️ Short {short}%")
    rs = data.get("rs_vs_qqq")
    if rs and rs > 20:
        flags.append(f"🔥 RS vs QQQ: +{rs}%")
    elif rs and rs < -20:
        flags.append(f"📉 RS vs QQQ: {rs}%")
    season = data.get("seasonality")
    if season:
        flags.append(f"📅 {season}")
    return flags


def format_ticker_attachment(ticker: str, data: dict) -> dict:
    """Format one ticker as a Slack attachment (colored sidebar + Block Kit fields)."""
    try:
        price    = data.get("price", "N/A")
        cp       = data.get("change_pct", 0)
        rsi      = data.get("rsi")
        tpe      = data.get("trailingPE")
        fpe      = data.get("forwardPE")
        tech     = data.get("technicals", {})
        category = data.get("category", "STANDARD_TECH")
        cat_label = CATEGORY_LABELS.get(category, category)
        analysis = data.get("analysis") or dict(_FALLBACK_ANALYSIS)
        verdict  = analysis.get("verdict", "CZEKAJ")
        verdict_emoji = {"KUP": "🟢", "CZEKAJ": "🟡", "OMIJAJ": "🔴"}.get(verdict, "⚪")
        color    = {"KUP": "#2eb886", "CZEKAJ": "#e6b833", "OMIJAJ": "#e01e5a"}.get(verdict, "#aaaaaa")

        sign   = "+" if cp >= 0 else ""
        ma_str = _ticker_ma_str(tech)
        flags  = _ticker_flags(data)

        # ── Build fields depending on category ──
        if category == "CRYPTO_PROXY":
            btc = data.get("btc_data", {})
            btc_str  = f"${btc.get('price','N/A')} RSI={btc.get('rsi','?')} {'🟢BTC bullish' if btc.get('bullish') else '🔴BTC bearish'}"
            nav  = data.get("mstr_nav", {})
            nav_str  = f"~${nav.get('nav_per_share_approx','N/A')}/akcję" if nav else "N/A"
            fields = [
                {"type": "mrkdwn", "text": f"*RSI spółki*\n{rsi if rsi else 'N/A'}"},
                {"type": "mrkdwn", "text": f"*Bitcoin*\n{btc_str}"},
                {"type": "mrkdwn", "text": f"*NAV (approx)*\n{nav_str}"},
                {"type": "mrkdwn", "text": f"*Technikalia*\n{ma_str}"},
                {"type": "mrkdwn", "text": f"*Fundamenty*\n{analysis.get('fundamentals_score','?')}/5"},
                {"type": "mrkdwn", "text": f"*Ryzyko*\n{analysis.get('macro_risk','?')}"},
            ]
        elif category == "URANIUM":
            fields = [
                {"type": "mrkdwn", "text": f"*RSI*\n{rsi if rsi else 'N/A'}"},
                {"type": "mrkdwn", "text": f"*PE / Fwd PE*\n{round(tpe,1) if tpe else 'N/A'} / {round(fpe,1) if fpe else 'N/A'}"},
                {"type": "mrkdwn", "text": f"*Technikalia*\n{ma_str}"},
                {"type": "mrkdwn", "text": f"*Timing*\n{analysis.get('timing_score','?')}/5"},
                {"type": "mrkdwn", "text": f"*Ryzyko*\n{analysis.get('macro_risk','?')}"},
                {"type": "mrkdwn", "text": f"*Fundamenty*\n{analysis.get('fundamentals_score','?')}/5"},
            ]
        else:
            fields = [
                {"type": "mrkdwn", "text": f"*RSI*\n{rsi if rsi else 'N/A'}"},
                {"type": "mrkdwn", "text": f"*PE / Fwd PE*\n{round(tpe,1) if tpe else 'N/A'} / {round(fpe,1) if fpe else 'N/A'}"},
                {"type": "mrkdwn", "text": f"*Fundamenty*\n{analysis.get('fundamentals_score','?')}/5"},
                {"type": "mrkdwn", "text": f"*Timing*\n{analysis.get('timing_score','?')}/5"},
                {"type": "mrkdwn", "text": f"*Ryzyko makro*\n{analysis.get('macro_risk','?')}"},
                {"type": "mrkdwn", "text": f"*Technikalia*\n{ma_str}"},
            ]

        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{ticker}* ${price} ({sign}{cp}%)  {verdict_emoji} *{verdict}*  `{cat_label}`",
                },
                "fields": fields,
            }
        ]

        # ── Auto-flags from score adjustments ──
        auto_flags = analysis.get("flags", [])
        all_flags  = flags + auto_flags
        if all_flags:
            blocks.append({
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": "  ·  ".join(all_flags)}],
            })

        # MSTR: BTC scenario block
        if ticker == "MSTR" and data.get("btc_data", {}).get("price"):
            btc_p  = data["btc_data"]["price"]
            nav    = data.get("mstr_nav", {})
            nav_ps = nav.get("nav_per_share_approx") if nav else None
            if nav_ps:
                scenario_150k = round(nav_ps * (150_000 / btc_p), 0)
                blocks.append({
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text":
                        f"📐 *BTC $150k scenario:* NAV/akcję ~${scenario_150k:.0f} "
                        f"(MSTR historycznie handluje z 1.5-2x premią do NAV)"
                    }],
                })

        # ── Reasoning + confidence ──
        reasoning  = analysis.get("reasoning", "")
        confidence = analysis.get("confidence", "MEDIUM")
        conf_emoji = {"HIGH": "🟢", "MEDIUM": "🟡", "LOW": "🔴"}.get(confidence, "🟡")
        if reasoning:
            conf_note = f"  ·  {conf_emoji} Pewność: {confidence}" + (
                "  ·  ⚠️ Zrób własny research" if confidence == "LOW" else ""
            )
            blocks.append({
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"_{reasoning[:280]}_{conf_note}"}],
            })

        # ── Bull / Bear case ──
        bull = analysis.get("bull_case", "")
        bear = analysis.get("bear_case", "")
        if bull or bear:
            bull_bear_text = ""
            if bull: bull_bear_text += f"🐂 {bull}"
            if bear: bull_bear_text += f"  ·  🐻 {bear}" if bull else f"🐻 {bear}"
            blocks.append({
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": bull_bear_text[:300]}],
            })

        return {"color": color, "blocks": blocks}
    except Exception as e:
        logger.warning("format_ticker_attachment error for %s: %s", ticker, e)
        return {"color": "#aaaaaa", "text": f"{ticker} — błąd formatowania: {e}"}


def format_ticker_slack(ticker: str, data: dict) -> str:
    """Plain-text fallback (used by /watchlist respond)."""
    try:
        price = data.get("price", "N/A")
        cp = data.get("change_pct", 0)
        rsi = data.get("rsi")
        tpe = data.get("trailingPE")
        fpe = data.get("forwardPE")
        tech = data.get("technicals", {})
        analysis = data.get("analysis") or dict(_FALLBACK_ANALYSIS)
        verdict = analysis.get("verdict", "CZEKAJ")
        verdict_emoji = {"KUP": "🟢", "CZEKAJ": "🟡", "OMIJAJ": "🔴"}.get(verdict, "⚪")
        sign = "+" if cp >= 0 else ""

        lines = [
            f"*{ticker}* ${price} ({sign}{cp}%) | RSI: {rsi if rsi else 'N/A'}"
            f" | PE: {round(tpe,1) if tpe else 'N/A'} | Fwd PE: {round(fpe,1) if fpe else 'N/A'}",
        ]
        ma_str = _ticker_ma_str(tech)
        if ma_str != "–":
            lines.append(ma_str)
        lines.extend(_ticker_flags(data))
        lines.append(
            f"Fundamenty: {analysis.get('fundamentals_score','?')}/5"
            f" | Timing: {analysis.get('timing_score','?')}/5"
            f" | Ryzyko: {analysis.get('macro_risk','?')}"
        )
        lines.append(f"_{analysis.get('reasoning', 'Brak analizy.')}_")
        lines.append(f"→ {verdict_emoji} *{verdict}*")
        return "\n".join(lines)
    except Exception as e:
        logger.warning("format_ticker_slack error for %s: %s", ticker, e)
        return f"{ticker} — błąd formatowania: {e}"


def _concentration_risk_section(results: list[tuple]) -> str:
    """Build concentration risk section from list of (ticker, data) tuples."""
    verdicts = Counter()
    sectors = Counter()
    for ticker, data in results:
        v = (data.get("analysis") or {}).get("verdict", "CZEKAJ")
        verdicts[v] += 1
        sectors[data.get("sector", "Other")] += 1

    total = sum(verdicts.values())
    lines = ["", "📊 *Watchlist breakdown:*"]
    lines.append(
        f"🟢 KUP: {verdicts['KUP']} | 🟡 CZEKAJ: {verdicts['CZEKAJ']} | 🔴 OMIJAJ: {verdicts['OMIJAJ']}"
    )

    top_sectors = sectors.most_common(3)
    sector_parts = [f"{s}: {c}" for s, c in top_sectors]
    lines.append(f"Sektory: {' | '.join(sector_parts)}")

    # Concentration warning
    for sector, count in sectors.most_common(1):
        pct = count / total * 100 if total else 0
        if pct > 40:
            lines.append(f"⚠️ *{sector}* stanowi {pct:.0f}% watchlisty — wysokie ryzyko koncentracji")

    return "\n".join(lines)


def run_stock_digest(tickers: list = None) -> str:
    """Run full digest. Returns full Slack message string."""
    if tickers is None:
        tickers = WATCHLIST

    today = datetime.datetime.now().strftime("%d.%m.%Y")
    qqq_30d = _fetch_qqq_30d()
    season = _seasonality_note()

    header_parts = [f"📊 *Stock Digest — {today}*"]
    if qqq_30d is not None:
        header_parts.append(f"QQQ 30d: {'+' if qqq_30d >= 0 else ''}{qqq_30d}%")
    if season:
        header_parts.append(f"📅 {season}")
    lines = [" | ".join(header_parts), ""]

    near_ath_tickers = []
    results = []

    for ticker in tickers:
        try:
            data = analyze_ticker(ticker, qqq_30d=qqq_30d)
            lines.append(format_ticker_slack(ticker, data))
            lines.append("---")
            if data.get("near_ath"):
                near_ath_tickers.append(ticker)
            results.append((ticker, data))
        except Exception as e:
            logger.warning("Skipping %s due to error: %s", ticker, e)

    if near_ath_tickers:
        lines.append(f"⚠️ *Blisko ATH (< 5%):* {', '.join(near_ath_tickers)}")

    if results:
        lines.append(_concentration_risk_section(results))

    return "\n".join(lines)


# ── Channel & scheduler entry point ──────────────────────────────────────────
STOCK_CHANNEL_ID = os.environ.get("SLACK_STOCK_CHANNEL", "C0B5LA4Q064")


def send_stock_digest(tickers: list = None):
    """Posts rich Block Kit cards to #inwestowanie (detailed per-ticker view)."""
    if tickers is None:
        tickers = WATCHLIST
    try:
        today = datetime.datetime.now().strftime("%d.%m.%Y")
        qqq_30d = _fetch_qqq_30d()
        season  = _seasonality_note()
        # Fetch BTC once — shared across all CRYPTO_PROXY tickers
        btc_data = _fetch_btc_data()

        header = f"📊 *Stock Digest — {today}*"
        if qqq_30d is not None:
            header += f"  |  QQQ 30d: {'+' if qqq_30d >= 0 else ''}{qqq_30d}%"
        if btc_data.get("price"):
            header += f"  |  ₿ ${btc_data['price']:,.0f} {'🟢' if btc_data.get('bullish') else '🔴'}"
        if season:
            header += f"  |  📅 {season}"

        _ctx.app.client.chat_postMessage(channel=STOCK_CHANNEL_ID, text=header)

        batch: list[dict] = []
        results: list[tuple] = []
        near_ath_tickers: list[str] = []

        def _flush(b):
            if b:
                _ctx.app.client.chat_postMessage(
                    channel=STOCK_CHANNEL_ID,
                    text=" ",
                    attachments=b,
                )

        for ticker in tickers:
            try:
                data = analyze_ticker(ticker, qqq_30d=qqq_30d, btc_data=btc_data)
                batch.append(format_ticker_attachment(ticker, data))
                results.append((ticker, data))
                if data.get("near_ath"):
                    near_ath_tickers.append(ticker)
                if len(batch) >= 8:
                    _flush(batch)
                    batch = []
            except Exception as e:
                logger.warning("Skipping %s: %s", ticker, e)

        _flush(batch)

        # Summary
        summary_lines = []
        if near_ath_tickers:
            summary_lines.append(f"⚠️ *Blisko ATH (<5%):* {', '.join(near_ath_tickers)}")
        if results:
            summary_lines.append(_concentration_risk_section(results))
        if summary_lines:
            _ctx.app.client.chat_postMessage(
                channel=STOCK_CHANNEL_ID,
                text="\n".join(summary_lines),
            )

        logger.info("send_stock_digest: done, %d tickers posted", len(results))
    except Exception as e:
        logger.error("send_stock_digest failed: %s", e)


# ── Summary digest (one message) ─────────────────────────────────────────────

def run_summary_digest(tickers: list = None) -> str:
    """Fetch yfinance for all tickers, one Claude call → single grouped summary."""
    if tickers is None:
        tickers = WATCHLIST

    today = datetime.datetime.now().strftime("%d.%m.%Y")
    qqq_30d = _fetch_qqq_30d()
    season = _seasonality_note()

    btc_data    = _fetch_btc_data()
    lines_data  = []
    near_ath    = []

    for ticker in tickers:
        try:
            category = get_category(ticker)
            t    = yf.Ticker(ticker)
            info = t.info
            price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("ask") or 0.0
            if not price:
                continue
            prev   = info.get("previousClose") or price
            chg    = round((price - prev) / prev * 100, 2) if prev else 0.0
            pe     = info.get("trailingPE")
            fpe    = info.get("forwardPE")
            rev_g  = info.get("revenueGrowth")
            margin = info.get("profitMargins")
            high52 = info.get("fiftyTwoWeekHigh") or 0
            pct_ath = round((price - high52) / high52 * 100, 2) if high52 else None

            rsi = above50 = above200 = golden = death = None
            try:
                hist   = t.history(period="200d")
                closes = hist["Close"].tolist()
                if len(closes) >= 15:
                    rsi = round(_calc_rsi(closes), 1)
                tech     = _calc_technicals(closes)
                above50  = tech.get("above_ma50")
                above200 = tech.get("above_ma200")
                golden   = tech.get("golden_cross")
                death    = tech.get("death_cross")
            except Exception:
                pass

            parts = [f"{ticker}[{category}]: ${price} ({chg:+.1f}%)"]
            if rsi     is not None: parts.append(f"RSI={rsi}")
            if pe      is not None: parts.append(f"PE={round(pe,0):.0f}")
            if fpe     is not None: parts.append(f"FwdPE={round(fpe,0):.0f}")
            if rev_g   is not None: parts.append(f"RevGrowth={round(rev_g*100,0):.0f}%")
            if margin  is not None: parts.append(f"Margin={round(margin*100,0):.0f}%")
            if above50  is not None: parts.append("MA50=" + ("✅" if above50  else "❌"))
            if above200 is not None: parts.append("MA200=" + ("✅" if above200 else "❌"))
            if golden:  parts.append("GoldenCross")
            if death:   parts.append("DeathCross")
            if pct_ath is not None:
                parts.append(f"odATH={pct_ath}%")
                if pct_ath > -5:
                    near_ath.append(ticker)

            # Category extras inline
            if category == "CRYPTO_PROXY" and btc_data.get("price"):
                parts.append(f"BTC=${btc_data['price']:,.0f} RSI_BTC={btc_data.get('rsi','?')} BTC_bullish={btc_data.get('bullish','?')}")
            lines_data.append(" | ".join(parts))
        except Exception as e:
            logger.warning("Summary fetch %s: %s", ticker, e)

    if not lines_data:
        return "⚠️ Brak danych — sprawdź połączenie z yfinance."

    qqq_info    = f"QQQ 30d: {'+' if (qqq_30d or 0) >= 0 else ''}{qqq_30d}%" if qqq_30d is not None else ""
    season_info = f"Sezonowość: {season}" if season else ""
    btc_info    = f"BTC: ${btc_data.get('price','N/A'):,} RSI={btc_data.get('rsi','?')} {'BULLISH' if btc_data.get('bullish') else 'BEARISH'}" if btc_data.get("price") else ""

    prompt = (
        f"Dzisiaj: {today}. {qqq_info}. {btc_info}. {season_info}\n\n"
        f"Dane dla {len(lines_data)} spółek (format: TICKER[KATEGORIA]: dane):\n"
        + "\n".join(lines_data)
        + """

Napisz JEDEN raport inwestycyjny w formacie Slack markdown. Pogrupuj wszystkie spółki w trzy sekcje:

🟢 *WARTE UWAGI — dobre wejście teraz:*
• *TICKER* $cena — 1 zdanie uzasadnienia

🟡 *CZEKAJ — dobra spółka, zły timing lub za drogo:*
• *TICKER* $cena — 1 zdanie uzasadnienia

🔴 *OMIJAJ:*
• *TICKER* $cena — 1 zdanie uzasadnienia

ZASADY GRUPOWANIA PER KATEGORIA:
- STANDARD_TECH: RSI<65 + nie blisko ATH + fundamenty OK = WARTE UWAGI
- CRYPTO_PROXY (MSTR, MARA, HOOD): oceniaj TYLKO przez BTC trend (nie PE/marże). BTC bullish+RSI_BTC<70=KUP. Zaznacz aktualny kurs BTC w reasoning.
- URANIUM (UEC,DNN,UUUU,CCJ): oceniaj przez spot uranu i nuclear renaissance, nie przez PE. Tailwind = rosnący popyt AI/data centers.
- DEFENSE (NOC,BA,TDG): geopolityka=TAILWIND, backlog kontraktów, budżety NATO rosnące.
- SPACE_DEFENSE (RKLB,ASTS,LUNR,PL,RDW,IRDM): pre-profit = oceniaj EV/Revenue nie PE. Kluczowe: backlog NASA/DoD, burn rate, kamienie milowe misji.
- BIOTECH_HEALTH (TEM,ISRG,UNH,NVO,TDOC): pipeline/FDA/GLP-1 market share. NVO przez ekspozycję na GLP-1.
- EMERGING_MARKETS (BABA,SE,GRAB,MELI,NU,DLO): uwzględnij ryzyko USD i kraju. Chiny=osobna flaga ryzyka regulacyjnego.
- CONSUMER_DISCRETIONARY (LULU,NKE,DECK,CMG,RACE): comparable sales, zapasy, cła Chiny. RACE=osobna liga (pricing power, order book). Jeśli cały sektor consumer słabo — kontekstualizuj.
FLAGI AUTOMATYCZNE (jeśli widoczne w danych): ⚠️ DETERIORATING FUNDAMENTALS, 🔴 GUIDANCE OBNIŻONY, ⚠️ RYZYKO CEŁ — uwzględnij je w uzasadnieniu.

Na końcu ZAWSZE:
⚠️ *Blisko ATH (<5%):* [lista lub "brak"]
📊 *Watchlist:* X warte uwagi | Y czekaj | Z omijaj

Pisz po polsku. Krótko, konkretnie, liczby z danych."""
    )

    try:
        resp = _ctx.claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2500,
            messages=[{"role": "user", "content": prompt}],
        )
        body = resp.content[0].text.strip()
    except Exception as e:
        logger.error("Claude summary digest error: %s", e)
        body = "❌ Błąd analizy Claude — spróbuj ponownie."

    header = f"📊 *Stock Digest — {today}*"
    if qqq_30d is not None:
        header += f"  |  QQQ 30d: {'+' if qqq_30d >= 0 else ''}{qqq_30d}%"
    if btc_data.get("price"):
        header += f"  |  ₿ ${btc_data['price']:,.0f} {'🟢' if btc_data.get('bullish') else '🔴'}"
    if season:
        header += f"  |  📅 {season}"

    return f"{header}\n\n{body}"


# ── CoinGecko ─────────────────────────────────────────────────────────────────

def _fetch_top_coins(limit: int = 20) -> list[dict]:
    """Top coins by market cap from CoinGecko free API (no key required)."""
    try:
        resp = _requests.get(
            "https://api.coingecko.com/api/v3/coins/markets",
            params={
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": limit,
                "page": 1,
                "sparkline": False,
                "price_change_percentage": "24h,7d",
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning("CoinGecko coins/markets error: %s", e)
        return []


def _fetch_btc_dominance() -> float | None:
    try:
        resp = _requests.get("https://api.coingecko.com/api/v3/global", timeout=10)
        resp.raise_for_status()
        return round(resp.json()["data"]["market_cap_percentage"].get("btc", 0), 1)
    except Exception as e:
        logger.warning("CoinGecko global error: %s", e)
        return None


# ── Macro briefing ─────────────────────────────────────────────────────────────

def fetch_macro_briefing() -> dict:
    """7 Tavily searches → one Claude call → macro context dict."""
    fallback = {"sentiment": "NEUTRALNY", "summary": "Brak danych makro.", "main_risk": ""}
    if _tavily is None:
        return fallback
    _queries = [
        "US stock market macro outlook this week 2026",
        "Federal Reserve interest rates decision 2026",
        "VIX volatility index level market fear 2026",
        "US recession probability economic indicators 2026",
        "geopolitical risk trade war tariffs market impact 2026",
        "crypto market bitcoin institutional flow 2026",
        "dollar index DXY trend 2026",
    ]
    snippets = []
    for q in _queries:
        try:
            r = _tavily.search(q, max_results=1)
            for item in (r.get("results") or [])[:1]:
                snippets.append(f"• {(item.get('content') or '')[:160]}")
        except Exception:
            pass
    if not snippets:
        return fallback
    try:
        resp = _ctx.claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=350,
            messages=[{"role": "user", "content": (
                "Na podstawie poniższych informacji makroekonomicznych oceń aktualny sentyment rynku:\n\n"
                + "\n".join(snippets)
                + '\n\nOdpowiedz TYLKO w JSON:\n'
                '{"sentiment": "RISK-ON"/"RISK-OFF"/"NEUTRALNY", '
                '"summary": "2-3 zdania po polsku o najważniejszych czynnikach", '
                '"main_risk": "1 zdanie o głównym ryzyku tygodnia"}'
            )}],
        )
        raw = resp.content[0].text.strip()
        m   = re.search(r"\{.*\}", raw, re.DOTALL)
        return json.loads(m.group() if m else raw)
    except Exception as e:
        logger.warning("Macro briefing synthesis error: %s", e)
        return {"sentiment": "NEUTRALNY", "summary": " ".join(snippets[:3])[:300], "main_risk": ""}


def send_macro_briefing():
    """Post standalone macro briefing to #inwestowanie (/makro command)."""
    try:
        today  = datetime.datetime.now().strftime("%d.%m.%Y")
        macro  = fetch_macro_briefing()
        btc    = _fetch_btc_data()
        dom    = _fetch_btc_dominance()
        qqq_30d = _fetch_qqq_30d()

        s_emoji = {"RISK-ON": "🟢", "RISK-OFF": "🔴", "NEUTRALNY": "🟡"}.get(macro.get("sentiment",""), "🟡")
        lines = [
            f"🌍 *Makro Briefing — {today}*",
            f"{s_emoji} Sentyment: *{macro.get('sentiment','NEUTRALNY')}*",
        ]
        if qqq_30d is not None:
            lines.append(f"📈 QQQ 30d: {'+' if qqq_30d >= 0 else ''}{qqq_30d}%")
        if btc.get("price"):
            lines.append(f"₿ BTC: ${btc['price']:,.0f}  RSI={btc.get('rsi','?')}  {'🟢 Bullish' if btc.get('bullish') else '🔴 Bearish'}")
        if dom is not None:
            lines.append(f"₿ Dominance: {dom}%")
        if macro.get("summary"):
            lines.append(f"\n{macro['summary']}")
        if macro.get("main_risk"):
            lines.append(f"\n⚠️ *Główne ryzyko:* {macro['main_risk']}")

        _ctx.app.client.chat_postMessage(channel=STOCK_CHANNEL_ID, text="\n".join(lines))
        logger.info("send_macro_briefing: done")
    except Exception as e:
        logger.error("send_macro_briefing failed: %s", e)


# ── Crypto analysis ────────────────────────────────────────────────────────────

_CRYPTO_SYSTEM = (
    "Jesteś analitykiem krypto. Filozofia:\n"
    "- KUPUJ na cofce, nie po pompie (+20% w 7d = CZEKAJ)\n"
    "- BTC dominance rośnie → altcoiny mają trudniej\n"
    "- TIER1 (rank≤5): institutional flow > retail hype\n"
    "- Blisko ATH (<10% od ATH) = CZEKAJ na korektę\n"
    "- Unusual volume bez newsów = suspicious\n"
    "Odpowiadasz TYLKO w JSON:\n"
    '{"fundamentals_score":1-5,"timing_score":1-5,"macro_risk":"low"/"medium"/"high",'
    '"confidence":"LOW"/"MEDIUM"/"HIGH","reasoning":"max 2 zdania po polsku",'
    '"bull_case":"1 zdanie","bear_case":"1 zdanie","verdict":"KUP"/"CZEKAJ"/"OMIJAJ"}'
)


def analyze_coin(coin: dict, btc_dominance: float | None, macro: dict) -> dict:
    """Full analysis for one CoinGecko coin dict. Returns enriched dict."""
    symbol  = (coin.get("symbol") or "?").upper()
    name    = coin.get("name", symbol)
    price   = coin.get("current_price", 0)
    chg24   = coin.get("price_change_percentage_24h") or 0
    chg7d   = coin.get("price_change_percentage_7d_in_currency") or 0
    mcap    = coin.get("market_cap", 0)
    rank    = coin.get("market_cap_rank", 99)
    vol24   = coin.get("total_volume", 0)
    ath     = coin.get("ath") or 0
    pct_ath = round((price - ath) / ath * 100, 1) if ath else None

    is_tier1    = rank <= 5
    pumped      = bool(chg7d > 20)
    near_ath    = bool(pct_ath is not None and pct_ath > -10)
    unusual_vol = bool(mcap and vol24 and vol24 > mcap * 0.1)

    news_ctx = ""
    if _tavily and is_tier1:
        try:
            r = _tavily.search(f"{symbol} {name} institutional inflows narrative 2026", max_results=2)
            news_ctx = " ".join((x.get("content") or "")[:150] for x in (r.get("results") or []))[:300]
        except Exception:
            pass

    prompt = (
        f"Coin: {symbol} ({name}) | Rank: #{rank}\n"
        f"Cena: ${price} | 24h: {chg24:+.1f}% | 7d: {chg7d:+.1f}%\n"
        f"MCap: ${mcap:,.0f} | % od ATH: {pct_ath}%\n"
        f"BTC dominance: {btc_dominance}% | Kategoria: {'TIER1' if is_tier1 else 'ALTCOIN'}\n"
        f"Anti-pump: {pumped} | Near ATH: {near_ath} | Unusual vol: {unusual_vol}\n"
        f"Makro: {macro.get('sentiment','?')}\n"
    )
    if news_ctx:
        prompt += f"Kontekst: {news_ctx}\n"

    try:
        resp = _ctx.claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=350,
            system=_CRYPTO_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        raw    = resp.content[0].text.strip()
        m      = re.search(r"\{.*\}", raw, re.DOTALL)
        result = json.loads(m.group() if m else raw)
        result.setdefault("confidence", "MEDIUM")
        result.setdefault("bull_case", "")
        result.setdefault("bear_case", "")
        if pumped and result.get("verdict") == "KUP":
            result["verdict"]   = "CZEKAJ"
            result["reasoning"] = "[anti-pump] " + result.get("reasoning", "")
        return {**coin, "analysis": result, "pct_ath": pct_ath, "pumped": pumped}
    except Exception as e:
        logger.warning("Crypto analysis error %s: %s", symbol, e)
        return {**coin, "analysis": dict(_FALLBACK_ANALYSIS), "pct_ath": pct_ath, "pumped": pumped}


def format_coin_attachment(coin_data: dict) -> dict:
    """Format one coin as a Slack attachment."""
    analysis = coin_data.get("analysis") or dict(_FALLBACK_ANALYSIS)
    symbol   = (coin_data.get("symbol") or "?").upper()
    name     = coin_data.get("name", symbol)
    price    = coin_data.get("current_price", 0)
    chg24    = coin_data.get("price_change_percentage_24h") or 0
    chg7d    = coin_data.get("price_change_percentage_7d_in_currency") or 0
    rank     = coin_data.get("market_cap_rank", "?")
    pct_ath  = coin_data.get("pct_ath")
    verdict  = analysis.get("verdict", "CZEKAJ")
    v_emoji  = {"KUP": "🟢", "CZEKAJ": "🟡", "OMIJAJ": "🔴"}.get(verdict, "⚪")
    color    = {"KUP": "#2eb886", "CZEKAJ": "#e6b833", "OMIJAJ": "#e01e5a"}.get(verdict, "#aaaaaa")
    sign24   = "+" if chg24 >= 0 else ""
    sign7    = "+" if chg7d  >= 0 else ""

    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f"*{symbol}* ({name}) #{rank}  ${price:,.4g}  {v_emoji} *{verdict}*"},
            "fields": [
                {"type": "mrkdwn", "text": f"*24h*\n{sign24}{chg24:.1f}%"},
                {"type": "mrkdwn", "text": f"*7d*\n{sign7}{chg7d:.1f}%"},
                {"type": "mrkdwn", "text": f"*Od ATH*\n{pct_ath}%" if pct_ath else "*Od ATH*\nN/A"},
                {"type": "mrkdwn", "text": f"*Fundamenty*\n{analysis.get('fundamentals_score','?')}/5"},
                {"type": "mrkdwn", "text": f"*Timing*\n{analysis.get('timing_score','?')}/5"},
                {"type": "mrkdwn", "text": f"*Pewność*\n{analysis.get('confidence','?')}"},
            ],
        }
    ]
    flags = []
    if coin_data.get("pumped"):
        flags.append("⚠️ PUMP >20% w 7d")
    if pct_ath and pct_ath > -10:
        flags.append(f"⚠️ Blisko ATH ({pct_ath}%)")
    if flags:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": "  ·  ".join(flags)}]})

    reasoning = analysis.get("reasoning", "")
    confidence = analysis.get("confidence", "MEDIUM")
    conf_emoji = {"HIGH": "🟢", "MEDIUM": "🟡", "LOW": "🔴"}.get(confidence, "🟡")
    if reasoning:
        note = f"  ·  {conf_emoji} {confidence}" + ("  ·  ⚠️ Zrób własny research" if confidence == "LOW" else "")
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"_{reasoning[:280]}_{note}"}]})

    bull = analysis.get("bull_case", "")
    bear = analysis.get("bear_case", "")
    if bull or bear:
        txt = (f"🐂 {bull}" if bull else "") + (f"  ·  🐻 {bear}" if bear and bull else f"🐻 {bear}" if bear else "")
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": txt[:280]}]})

    return {"color": color, "blocks": blocks}


def send_crypto_digest(limit: int = 20):
    """Post top-coin analysis to #inwestowanie."""
    try:
        btc_dom = _fetch_btc_dominance()
        macro   = fetch_macro_briefing()
        coins   = _fetch_top_coins(limit)
        if not coins:
            _ctx.app.client.chat_postMessage(channel=STOCK_CHANNEL_ID, text="⚠️ Brak danych z CoinGecko.")
            return
        today = datetime.datetime.now().strftime("%d.%m.%Y")
        header = f"₿ *Crypto Digest — {today}*  |  BTC dom: {btc_dom}%  |  Makro: {macro.get('sentiment','?')}"
        _ctx.app.client.chat_postMessage(channel=STOCK_CHANNEL_ID, text=header)

        batch = []
        for coin in coins:
            try:
                data = analyze_coin(coin, btc_dom, macro)
                batch.append(format_coin_attachment(data))
                if len(batch) >= 5:
                    _ctx.app.client.chat_postMessage(channel=STOCK_CHANNEL_ID, text=" ", attachments=batch)
                    batch = []
            except Exception as e:
                logger.warning("Skipping coin %s: %s", coin.get("symbol"), e)
        if batch:
            _ctx.app.client.chat_postMessage(channel=STOCK_CHANNEL_ID, text=" ", attachments=batch)
        logger.info("send_crypto_digest: done")
    except Exception as e:
        logger.error("send_crypto_digest failed: %s", e)


def send_summary_digest(tickers: list = None):
    """Post capital flow snapshot + one-message summary digest to #inwestowanie."""
    try:
        # ── Capital flow header ──
        cf = _get_capital_flow()
        if cf:
            try:
                snapshot = cf.build_capital_flow_snapshot()
                flow_block = cf.format_capital_flow_block(snapshot)
                if flow_block:
                    _ctx.app.client.chat_postMessage(channel=STOCK_CHANNEL_ID, text=flow_block)
            except Exception as e:
                logger.warning("Capital flow header error: %s", e)

        # ── Main digest ──
        msg = run_summary_digest(tickers)
        chunks = [msg[i:i+3900] for i in range(0, len(msg), 3900)]
        for chunk in chunks:
            _ctx.app.client.chat_postMessage(channel=STOCK_CHANNEL_ID, text=chunk)
        logger.info("send_summary_digest: done")
    except Exception as e:
        logger.error("send_summary_digest failed: %s", e)
