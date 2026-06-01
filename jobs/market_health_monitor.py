"""
jobs/market_health_monitor.py — Bear Market & Correction Early Warning System.

DNA Rynków philosophy:
"Korekta 10% w 12 miesięcy = prawie pewna (80%).
Nie ma to znaczenia dopóki nie towarzyszą jej pogarszające się dane gospodarcze.
Każda recesja ma 3 spójne elementy — śledź je codziennie."

Scoring: 20 wskaźników → 0-100 pkt → tryb rynku
3 filary recesji: produkcja, sprzedaż detaliczna, bezrobocie
"""

import os
import json
import logging
import datetime
import time as _time

import requests as _requests
import pandas as pd
import yfinance as yf

import _ctx

logger = logging.getLogger(__name__)

# ── Channel ───────────────────────────────────────────────────────────────────
STOCK_CHANNEL_ID = os.environ.get("SLACK_STOCK_CHANNEL", "C0B5LA4Q064")

# ── FRED API ──────────────────────────────────────────────────────────────────
_FRED_KEY = os.environ.get("FRED_API_KEY", "")
_FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

# ── Tavily ────────────────────────────────────────────────────────────────────
try:
    from tavily import TavilyClient
    _TAVILY_KEY = os.environ.get("TAVILY_API_KEY", "")
    _tavily = TavilyClient(api_key=_TAVILY_KEY) if _TAVILY_KEY else None
except ImportError:
    _tavily = None

# ── State persistence ─────────────────────────────────────────────────────────
_STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "market_health_state.json")


def _load_state() -> dict:
    try:
        with open(_STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state: dict):
    try:
        os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
        with open(_STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.warning("market_health_monitor: save_state failed: %s", e)


# ── FRED helper ───────────────────────────────────────────────────────────────

def _fred(series: str, limit: int = 13) -> list[float]:
    """Return last `limit` observations for a FRED series, newest last."""
    if not _FRED_KEY:
        return []
    try:
        resp = _requests.get(
            _FRED_BASE,
            params={
                "series_id": series,
                "api_key": _FRED_KEY,
                "file_type": "json",
                "sort_order": "desc",
                "limit": limit,
            },
            timeout=10,
        )
        obs = resp.json().get("observations", [])
        vals = []
        for o in reversed(obs):
            v = o.get("value", ".")
            if v != ".":
                try:
                    vals.append(float(v))
                except ValueError:
                    pass
        return vals
    except Exception as e:
        logger.warning("FRED %s error: %s", series, e)
        return []


# ── Tavily helper ─────────────────────────────────────────────────────────────

def _tavily_snippet(query: str, chars: int = 400) -> str:
    if _tavily is None:
        return ""
    try:
        r = _tavily.search(query, max_results=3)
        return " ".join((x.get("content") or "")[:150] for x in (r.get("results") or []))[:chars]
    except Exception:
        return ""


# ── yfinance price helper ─────────────────────────────────────────────────────

def _yf_close(ticker: str, period: str = "6mo") -> list[float]:
    try:
        hist = yf.Ticker(ticker).history(period=period, auto_adjust=True)
        if hist.empty:
            return []
        return list(hist["Close"].dropna())
    except Exception:
        return []


def _yf_latest(ticker: str) -> float | None:
    closes = _yf_close(ticker, period="5d")
    return closes[-1] if closes else None


# ═══════════════════════════════════════════════════════════════════════════════
# 3 FILARY RECESJI
# ═══════════════════════════════════════════════════════════════════════════════

def _pillar_industrial_production() -> dict:
    """FILAR 1: Industrial Production (FRED: INDPRO). Monthly."""
    vals = _fred("INDPRO", limit=6)
    if len(vals) < 3:
        return {"status": "grey", "label": "?", "detail": "brak danych"}

    # Count consecutive monthly declines (newest-last)
    declines = 0
    for i in range(len(vals) - 1, 0, -1):
        if vals[i] < vals[i - 1]:
            declines += 1
        else:
            break

    trend = f"{vals[-1]:.1f} (ind. {'+' if vals[-1] >= vals[-2] else ''}{vals[-1] - vals[-2]:.2f} mies.)"
    if declines >= 3:
        return {"status": "red", "label": "🔴", "detail": f"Spadek {declines} mies. z rzędu — {trend}"}
    if declines >= 2:
        return {"status": "yellow", "label": "🟡", "detail": f"Spadek {declines} mies. z rzędu — {trend}"}
    return {"status": "green", "label": "🟢", "detail": trend}


def _pillar_retail_sales() -> dict:
    """FILAR 2: Retail Sales ex-Food Services (FRED: RSXFS). Monthly."""
    vals = _fred("RSXFS", limit=6)
    if len(vals) < 3:
        return {"status": "grey", "label": "?", "detail": "brak danych"}

    declines = 0
    for i in range(len(vals) - 1, 0, -1):
        if vals[i] < vals[i - 1]:
            declines += 1
        else:
            break

    trend = f"${vals[-1]:.1f}B ({'+' if vals[-1] >= vals[-2] else ''}{vals[-1] - vals[-2]:.1f}B mies.)"
    if declines >= 2:
        return {"status": "yellow", "label": "🟡", "detail": f"Spadek {declines} mies. z rzędu — {trend}"}
    return {"status": "green", "label": "🟢", "detail": trend}


def _pillar_jobless_claims() -> dict:
    """FILAR 3: Initial Jobless Claims (FRED: ICSA). Weekly."""
    vals = _fred("ICSA", limit=10)
    if len(vals) < 4:
        return {"status": "grey", "label": "?", "detail": "brak danych"}

    latest = vals[-1]
    # Count consecutive weekly increases
    increases = 0
    for i in range(len(vals) - 1, 0, -1):
        if vals[i] > vals[i - 1]:
            increases += 1
        else:
            break

    trend = f"{int(latest):,} tyg."
    if latest > 350_000 or increases >= 8:
        return {"status": "red", "label": "🔴", "detail": f"KRYTYCZNE: {trend}, rośnie {increases} tyg."}
    if latest > 300_000 or increases >= 4:
        return {"status": "yellow", "label": "🟡", "detail": f"OSTRZEŻENIE: {trend}, rośnie {increases} tyg."}
    return {"status": "green", "label": "🟢", "detail": trend}


def evaluate_recession_pillars() -> dict:
    """Return status of all 3 recession pillars."""
    return {
        "industrial_production": _pillar_industrial_production(),
        "retail_sales":          _pillar_retail_sales(),
        "jobless_claims":        _pillar_jobless_claims(),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 20 WSKAŹNIKÓW — SCORING
# ═══════════════════════════════════════════════════════════════════════════════

def _score_jobless_claims() -> tuple[int, str]:
    vals = _fred("ICSA", limit=10)
    if not vals:
        return 2, "brak danych"
    latest = vals[-1]
    increases = sum(1 for i in range(len(vals) - 1, 0, -1) if vals[i] > vals[i - 1])
    if latest < 220_000 and increases == 0:
        return 5, f"🟢 {int(latest):,} i spada (BULLISH)"
    if latest <= 280_000:
        return 3, f"🟡 {int(latest):,} stabilne"
    if latest > 350_000 or increases >= 8:
        return -5, f"🔴 {int(latest):,} — trend wzrostowy {increases} tyg. (BEARISH)"
    return 0, f"⚠️ {int(latest):,} — rośnie {increases} tyg."


def _score_yield_curve() -> tuple[int, str]:
    vals = _fred("T10Y2Y", limit=5)
    if not vals:
        return 1, "brak danych"
    spread = vals[-1]
    prev = vals[-2] if len(vals) >= 2 else spread
    if spread > 0.5 and spread > prev:
        return 5, f"🟢 {spread:+.2f}% i rośnie (normalna krzywa)"
    if spread > 0:
        return 2, f"🟡 {spread:+.2f}% — spłaszczona"
    if spread > -0.5:
        return 0, f"⚠️ {spread:+.2f}% — inwersja"
    return -3, f"🔴 {spread:+.2f}% — głęboka inwersja (sygnał recesji)"


def _score_ism_pmi() -> tuple[int, str]:
    snippet = _tavily_snippet("ISM manufacturing PMI latest reading 2026", 300)
    # Parse PMI number from snippet
    import re
    m = re.search(r'\b(3\d|4\d|5\d|6\d)\.\d\b', snippet)
    pmi = float(m.group()) if m else None
    if pmi is None:
        return 2, "ISM PMI: brak danych"
    if pmi > 55:
        return 5, f"🟢 ISM PMI {pmi} (ekspansja)"
    if pmi >= 50:
        return 3, f"🟡 ISM PMI {pmi} (umiarkowana ekspansja)"
    if pmi >= 45:
        return 0, f"⚠️ ISM PMI {pmi} (kontrakcja)"
    return -3, f"🔴 ISM PMI {pmi} (głęboka kontrakcja)"


def _score_cpi() -> tuple[int, str]:
    vals = _fred("CPIAUCSL", limit=13)
    if len(vals) < 13:
        return 1, "brak danych CPI"
    # YoY CPI
    yoy = (vals[-1] / vals[-13] - 1) * 100
    prev_yoy = (vals[-2] / vals[-14] - 1) * 100 if len(vals) >= 14 else yoy
    falling = yoy < prev_yoy
    if yoy <= 3 and falling:
        return 4, f"🟢 CPI {yoy:.1f}% i spada (Fed może ciąć)"
    if yoy <= 3:
        return 2, f"🟡 CPI {yoy:.1f}% stabilne"
    if yoy <= 4:
        return 0, f"⚠️ CPI {yoy:.1f}% — podwyższone"
    return -3, f"🔴 CPI {yoy:.1f}% i rośnie (Fed musi podnosić)"


def _score_fed_policy() -> tuple[int, str]:
    snippet = _tavily_snippet("Federal Reserve interest rate decision hike cut pause 2026", 300)
    sl = snippet.lower()
    if any(w in sl for w in ("cut", "cuts", "obniżka", "obniżki", "pause", "hold", "pauza")):
        return 4, "🟢 Fed: cięcia/pauza (risk-on)"
    if any(w in sl for w in ("hike", "raise", "podwyżka", "tightening")):
        return -2, "🔴 Fed: podwyżki stóp"
    if any(w in sl for w in ("emergency", "crisis", "nagłe")):
        return -5, "🔴 Fed: emergency move"
    return 2, "🟡 Fed: neutralny"


def _score_gdp() -> tuple[int, str]:
    vals = _fred("GDP", limit=5)
    if len(vals) < 2:
        snippet = _tavily_snippet("US GDP growth rate quarterly 2026", 200)
        return 2, f"GDP: {snippet[:80] or 'brak danych'}"
    # QoQ annualised growth approximation
    qoq_ann = (vals[-1] / vals[-2] - 1) * 400
    if qoq_ann > 3:
        return 4, f"🟢 GDP {qoq_ann:.1f}% ann. (solidny wzrost)"
    if qoq_ann >= 2:
        return 3, f"🟢 GDP {qoq_ann:.1f}% ann."
    if qoq_ann >= 1:
        return 1, f"🟡 GDP {qoq_ann:.1f}% ann. (słaby wzrost)"
    if qoq_ann >= 0:
        return 0, f"⚠️ GDP {qoq_ann:.1f}% ann. (stagnacja)"
    return -5, f"🔴 GDP {qoq_ann:.1f}% — recesja techniczna"


def _score_retail_sales() -> tuple[int, str]:
    vals = _fred("RSXFS", limit=6)
    if len(vals) < 3:
        return 1, "brak danych retail"
    gains = 0
    declines = 0
    for i in range(len(vals) - 1, max(len(vals) - 4, 0), -1):
        if vals[i] > vals[i - 1]:
            gains += 1
        else:
            declines += 1
    if gains >= 3:
        return 4, f"🟢 Retail Sales {vals[-1]:.1f}B — rośnie {gains} mies."
    if declines >= 2:
        return -3, f"🔴 Retail Sales {vals[-1]:.1f}B — spada {declines} mies."
    return 2, f"🟡 Retail Sales {vals[-1]:.1f}B — stabilne"


def _score_vix() -> tuple[int, str]:
    closes = _yf_close("^VIX", period="1mo")
    if not closes:
        return 1, "VIX: brak danych"
    vix = closes[-1]
    if vix < 15:
        return 5, f"🟢 VIX {vix:.1f} — spokój (risk-on)"
    if vix < 20:
        return 3, f"🟢 VIX {vix:.1f} — normalny"
    if vix < 28:
        return 1, f"🟡 VIX {vix:.1f} — lekki stres"
    if vix < 35:
        return -2, f"⚠️ VIX {vix:.1f} — strach (redukuj ryzyko)"
    return -5, f"🔴 VIX {vix:.1f} — PANIKA (historycznie blisko dna!)"


def _score_sp500_ma200() -> tuple[int, str]:
    closes = _yf_close("^GSPC", period="1y")
    if len(closes) < 200:
        return 1, "S&P500: brak danych"
    price = closes[-1]
    ma200 = sum(closes[-200:]) / 200
    pct = (price / ma200 - 1) * 100
    if pct > 5:
        return 4, f"🟢 S&P500 {pct:+.1f}% powyżej MA200 (trend wzrostowy)"
    if pct >= 0:
        return 2, f"🟡 S&P500 {pct:+.1f}% powyżej MA200"
    if pct > -10:
        return -3, f"⚠️ S&P500 {pct:+.1f}% poniżej MA200"
    return -5, f"🔴 S&P500 {pct:+.1f}% poniżej MA200 (bear trend)"


def _score_market_breadth() -> tuple[int, str]:
    """% of sample S&P500 stocks above MA50."""
    _SAMPLE = [
        "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "BRK-B", "LLY",
        "AVGO", "JPM", "UNH", "V", "XOM", "TSLA", "MA", "PG", "JNJ",
        "HD", "COST", "MRK", "ABBV", "CRM", "CVX", "NFLX", "BAC",
        "AMD", "WMT", "KO", "TMO", "MCD", "INTC", "IBM", "GS", "CAT",
        "DIS", "ADBE", "CSCO", "VZ", "PEP", "ORCL", "ACN", "NOW",
        "INTU", "AMAT", "ISRG", "TXN", "HON", "QCOM", "LOW", "SPGI",
    ]
    above = 0
    total = 0
    try:
        raw = yf.download(_SAMPLE, period="65d", progress=False, auto_adjust=True)
        if isinstance(raw.columns, pd.MultiIndex):
            closes_df = raw["Close"]
        else:
            closes_df = raw[["Close"]].rename(columns={"Close": _SAMPLE[0]})
        for t in closes_df.columns:
            col = closes_df[t].dropna()
            if len(col) < 50:
                continue
            total += 1
            if col.iloc[-1] > col.rolling(50).mean().iloc[-1]:
                above += 1
    except Exception as e:
        logger.warning("Market breadth error: %s", e)
        return 1, "Market Breadth: brak danych"
    if total == 0:
        return 1, "Market Breadth: brak danych"
    pct = above / total * 100
    if pct > 70:
        return 4, f"🟢 Breadth {pct:.0f}% powyżej MA50 (szeroka hossa)"
    if pct >= 50:
        return 2, f"🟡 Breadth {pct:.0f}% powyżej MA50"
    if pct >= 30:
        return 0, f"⚠️ Breadth {pct:.0f}% powyżej MA50 (wąska hossa)"
    return -4, f"🔴 Breadth {pct:.0f}% powyżej MA50 (szeroka słabość)"


def _score_credit_spreads() -> tuple[int, str]:
    hyg = _yf_close("HYG", period="3mo")
    tlt = _yf_close("TLT", period="3mo")
    if len(hyg) < 20 or len(tlt) < 20:
        snippet = _tavily_snippet("HYG LQD credit spreads high yield tightening widening 2026", 200)
        sl = snippet.lower()
        if any(w in sl for w in ("tighten", "narrow", "zawężają")):
            return 4, "🟢 Spready kredytowe zawężają się"
        if any(w in sl for w in ("widen", "blow", "rozszerzają")):
            return -3, "🔴 Spready kredytowe rosną"
        return 1, "Credit spreads: neutralne"
    ratio_now  = hyg[-1] / tlt[-1]
    ratio_20d  = (sum(hyg[-20:]) / 20) / (sum(tlt[-20:]) / 20)
    pct_chg = (ratio_now / ratio_20d - 1) * 100
    if pct_chg > 1:
        return 4, f"🟢 HYG/TLT ratio +{pct_chg:.1f}% (spready zawężają się)"
    if pct_chg > -1:
        return 2, f"🟡 HYG/TLT ratio {pct_chg:+.1f}% (stabilne)"
    if pct_chg > -3:
        return -3, f"⚠️ HYG/TLT ratio {pct_chg:.1f}% (spready rosną)"
    return -5, f"🔴 HYG/TLT ratio {pct_chg:.1f}% (gwałtowne rozszerzenie!)"


def _score_positioning() -> tuple[int, str]:
    snippet = _tavily_snippet("hedge fund equity exposure positioning discretionary systematic 2026", 300)
    sl = snippet.lower()
    if "low" in sl and "systematic" in sl:
        return 4, "🟢 Discretionary LOW — jest paliwo do wzrostów"
    if any(w in sl for w in ("underweight", "low exposure", "defensive")):
        return 3, "🟢 Fundusze defensywnie — dużo gotówki na boku"
    if any(w in sl for w in ("overweight", "high exposure", "max long")):
        return 0, "⚠️ Fundusze maksymalnie zaangażowane — mało paliwa"
    return 2, "🟡 Pozycjonowanie neutralne"


def _score_put_call_ratio() -> tuple[int, str]:
    snippet = _tavily_snippet("put call ratio equity options market sentiment 2026", 200)
    import re
    m = re.search(r'(\d+\.\d+)', snippet)
    pcr = float(m.group(1)) if m else None
    if pcr is None:
        return 2, "Put/Call ratio: brak danych"
    if pcr > 1.3:
        return 2, f"🟡 P/C ratio {pcr:.2f} — panika (dno blisko)"
    if pcr >= 1.0:
        return 4, f"🟢 P/C ratio {pcr:.2f} — dużo hedgingu (dobry sygnał)"
    if pcr >= 0.7:
        return 3, f"🟢 P/C ratio {pcr:.2f} — normalny"
    return 0, f"⚠️ P/C ratio {pcr:.2f} — za dużo optymizmu"


def _score_seasonality() -> tuple[int, str]:
    month = datetime.date.today().month
    _STRONG = {1, 4, 10, 11, 12}
    _WEAK   = {8, 9}
    _MAY_SEP = {5, 6, 7, 8, 9}
    _MONTH_NAMES = {
        1: "styczeń", 2: "luty", 3: "marzec", 4: "kwiecień",
        5: "maj", 6: "czerwiec", 7: "lipiec", 8: "sierpień",
        9: "wrzesień", 10: "październik", 11: "listopad", 12: "grudzień",
    }
    name = _MONTH_NAMES[month]
    if month in _WEAK:
        return -2, f"⚠️ Sezonowość: {name} — historycznie najsłabszy miesiąc"
    if month in _MAY_SEP:
        return -1, f"🟡 Sezonowość: {name} — Sell in May effect"
    if month in _STRONG:
        return 2, f"🟢 Sezonowość: {name} — historycznie silny miesiąc"
    return 1, f"🟡 Sezonowość: {name} — neutralna"


def _score_fund_manager_survey() -> tuple[int, str]:
    snippet = _tavily_snippet("BofA fund manager survey cash levels equity allocation 2026", 300)
    import re
    m = re.search(r'(\d+\.?\d*)\s*%\s*(?:cash|gotówk)', snippet, re.IGNORECASE)
    cash_pct = float(m.group(1)) if m else None
    sl = snippet.lower()
    if cash_pct and cash_pct > 5:
        return 4, f"🟢 BofA FMS: gotówka {cash_pct:.1f}% — dużo paliwa do wzrostów"
    if cash_pct and cash_pct >= 4:
        return 2, f"🟡 BofA FMS: gotówka {cash_pct:.1f}%"
    if cash_pct and cash_pct < 4:
        return -2, f"🔴 BofA FMS: gotówka {cash_pct:.1f}% — wszyscy kupieni"
    if any(w in sl for w in ("underweight", "bearish", "defensive")):
        return 3, "🟢 FMS: fundusze niedoważone equities"
    if any(w in sl for w in ("overweight", "bullish", "max")):
        return -2, "⚠️ FMS: fundusze przeciążone equities"
    return 2, "🟡 FMS: neutralny sentyment"


def _score_fear_greed() -> tuple[int, str]:
    snippet = _tavily_snippet("CNN fear greed index market sentiment score 2026", 200)
    import re
    m = re.search(r'\b(\d{1,3})\b', snippet)
    score = int(m.group(1)) if m and 0 <= int(m.group(1)) <= 100 else None
    sl = snippet.lower()
    if score is None:
        if "extreme fear" in sl:
            return 4, "🟢 Fear & Greed: EXTREME FEAR (okazja kupna)"
        if "fear" in sl:
            return 3, "🟢 Fear & Greed: FEAR"
        if "extreme greed" in sl:
            return -3, "🔴 Fear & Greed: EXTREME GREED (ryzyko korekty)"
        if "greed" in sl:
            return 0, "⚠️ Fear & Greed: GREED"
        return 2, "🟡 Fear & Greed: neutralny"
    if score < 20:
        return 4, f"🟢 Fear & Greed {score} — EXTREME FEAR (okazja!)"
    if score < 40:
        return 3, f"🟢 Fear & Greed {score} — FEAR"
    if score < 60:
        return 2, f"🟡 Fear & Greed {score} — NEUTRAL"
    if score < 80:
        return 0, f"⚠️ Fear & Greed {score} — GREED"
    return -3, f"🔴 Fear & Greed {score} — EXTREME GREED (ryzyko korekty)"


def _score_retail_flows() -> tuple[int, str]:
    snippet = _tavily_snippet("retail investor flows equity buying selling sentiment 2026", 200)
    sl = snippet.lower()
    if any(w in sl for w in ("outflow", "selling", "odpływ", "sprzedają")):
        return 3, "🟢 Retail: odpływy (contrarian — blisko dna)"
    if any(w in sl for w in ("record inflow", "buying frenzy", "massive inflow")):
        return -2, "⚠️ Retail: rekordy napływów (late-cycle behavior)"
    if any(w in sl for w in ("inflow", "buying", "napływ")):
        return -2, "⚠️ Retail: duże napływy (late-cycle)"
    return 1, "🟡 Retail flows: neutralne"


def _score_dxy() -> tuple[int, str]:
    closes = _yf_close("DX-Y.NYB", period="3mo")
    if len(closes) < 20:
        return 1, "DXY: brak danych"
    dxy_now = closes[-1]
    dxy_20d = sum(closes[-20:]) / 20
    pct = (dxy_now / dxy_20d - 1) * 100
    if pct < -1:
        return 3, f"🟢 DXY {dxy_now:.1f} — słabnie ({pct:+.1f}%) tailwind dla EM/ryzyko"
    if pct < 1:
        return 1, f"🟡 DXY {dxy_now:.1f} — stabilny ({pct:+.1f}%)"
    return -2, f"🔴 DXY {dxy_now:.1f} — mocny ({pct:+.1f}%) headwind dla EM"


def _score_gold_vs_equities() -> tuple[int, str]:
    gld = _yf_close("GLD", period="3mo")
    spy = _yf_close("SPY", period="3mo")
    if len(gld) < 20 or len(spy) < 20:
        return 1, "Gold/SPY: brak danych"
    ratio_now  = gld[-1]  / spy[-1]
    ratio_20d  = (sum(gld[-20:]) / 20) / (sum(spy[-20:]) / 20)
    pct = (ratio_now / ratio_20d - 1) * 100
    if pct < -1:
        return 2, f"🟢 Gold underperforms SPY {pct:+.1f}% (risk-on)"
    if pct < 1:
        return 1, f"🟡 Gold/SPY ratio neutralny ({pct:+.1f}%)"
    return -2, f"🔴 Gold bije equities +{pct:.1f}% (risk-off, ucieczka do bezpieczeństwa)"


def _score_treasury_yield() -> tuple[int, str]:
    vals = _fred("DGS10", limit=70)
    if len(vals) < 2:
        closes = _yf_close("^TNX", period="6mo")
        if len(closes) < 2:
            return 1, "10Y yield: brak danych"
        vals = closes
    yield_now = vals[-1]
    # Compare to ~65 sessions ago (≈ 1 quarter)
    yield_qtr_ago = vals[-65] if len(vals) >= 65 else vals[0]
    bp_change = (yield_now - yield_qtr_ago) * 100
    if abs(bp_change) <= 20:
        return 3, f"🟢 10Y yield {yield_now:.2f}% — stabilny ({bp_change:+.0f}bp kwartał)"
    if bp_change < 50:
        return 1, f"🟡 10Y yield {yield_now:.2f}% — rośnie powoli ({bp_change:+.0f}bp kw.)"
    return -2, f"🔴 10Y yield {yield_now:.2f}% — szybki wzrost ({bp_change:+.0f}bp kw.) — kompresja mnożników"


# ═══════════════════════════════════════════════════════════════════════════════
# GŁÓWNA FUNKCJA SCORINGU
# ═══════════════════════════════════════════════════════════════════════════════

# Raw score range: min ≈ -49, max ≈ +62 → normalise to 0-100
_RAW_MIN = -49.0
_RAW_MAX =  62.0

_INDICATORS = [
    # (fn, weight_factor, label)
    # Kategoria A: Makro (35%)
    (_score_jobless_claims,    1.0, "A1. Jobless Claims"),
    (_score_yield_curve,       1.0, "A2. Yield Curve"),
    (_score_ism_pmi,           1.0, "A3. ISM PMI"),
    (_score_cpi,               0.9, "A4. CPI"),
    (_score_fed_policy,        0.9, "A5. Fed Policy"),
    (_score_gdp,               0.9, "A6. GDP"),
    (_score_retail_sales,      0.9, "A7. Retail Sales"),
    # Kategoria B: Rynkowe (35%)
    (_score_vix,               1.0, "B1. VIX"),
    (_score_sp500_ma200,       1.0, "B2. S&P500/MA200"),
    (_score_market_breadth,    1.0, "B3. Market Breadth"),
    (_score_credit_spreads,    1.0, "B4. Credit Spreads"),
    (_score_positioning,       0.8, "B5. Positioning"),
    (_score_put_call_ratio,    0.8, "B6. Put/Call Ratio"),
    (_score_seasonality,       0.5, "B7. Sezonowość"),
    # Kategoria C: Sentyment (30%)
    (_score_fund_manager_survey, 0.9, "C1. BofA FMS"),
    (_score_fear_greed,          1.0, "C2. Fear & Greed"),
    (_score_retail_flows,        0.8, "C3. Retail Flows"),
    (_score_dxy,                 0.8, "C4. DXY"),
    (_score_gold_vs_equities,    0.7, "C5. Gold/Equities"),
    (_score_treasury_yield,      1.0, "C6. 10Y Yield"),
]


def compute_market_health() -> dict:
    """Compute all 20 indicators and return structured result dict."""
    results = []
    raw_total = 0.0
    weight_total = 0.0

    for fn, weight, label in _INDICATORS:
        try:
            pts, detail = fn()
        except Exception as e:
            logger.warning("Indicator %s error: %s", label, e)
            pts, detail = 0, f"błąd: {e}"
        results.append({"label": label, "pts": pts, "detail": detail, "weight": weight})
        raw_total += pts * weight
        weight_total += weight

    raw_weighted = raw_total / weight_total if weight_total else 0

    # Normalise to 0-100
    norm_min = _RAW_MIN
    norm_max = _RAW_MAX
    score = int(round((raw_weighted - norm_min) / (norm_max - norm_min) * 100))
    score = max(0, min(100, score))

    if score >= 75:
        mode = "🟢 BULL MODE"
        action = "Rynek zdrowy. Korekty to okazje do kupna. Trzymaj pełną ekspozycję."
    elif score >= 55:
        mode = "🟡 CAUTION MODE"
        action = "Rynek OK ale są ryzyka. Redukuj najbardziej ryzykowne pozycje. Trzymaj 10-20% gotówki."
    elif score >= 35:
        mode = "🟠 DEFENSIVE MODE"
        action = "Wiele sygnałów ostrzegawczych. Redukuj ekspozycję do 50-60%. Gotówka jest pozycją."
    else:
        mode = "🔴 BEAR MODE"
        action = "Większość wskaźników negatywna. Minimalizuj ekspozycję. Rozważ hedging lub short pozycje."

    pillars = evaluate_recession_pillars()
    red_pillars = sum(1 for p in pillars.values() if p["status"] == "red")
    recession_alert = red_pillars >= 2

    positives = sorted([r for r in results if r["pts"] > 0], key=lambda x: -x["pts"] * x["weight"])
    negatives = sorted([r for r in results if r["pts"] < 0], key=lambda x:  x["pts"] * x["weight"])

    return {
        "score":            score,
        "mode":             mode,
        "action":           action,
        "pillars":          pillars,
        "recession_alert":  recession_alert,
        "red_pillars":      red_pillars,
        "indicators":       results,
        "top_positives":    positives[:3],
        "top_negatives":    negatives[:3],
        "timestamp":        datetime.datetime.now().isoformat(),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SLACK FORMATTERS
# ═══════════════════════════════════════════════════════════════════════════════

def _pillar_line(name: str, data: dict) -> str:
    return f"  {data['label']} {name}: {data['detail']}"


def format_health_header(result: dict) -> str:
    """Short header for daily digest (top-of-message)."""
    p = result["pillars"]
    top_pos = " | ".join(r["detail"] for r in result["top_positives"][:2])
    top_neg = " | ".join(r["detail"] for r in result["top_negatives"][:2])
    recession_line = "\n⚠️ *RECESJA ALERT* — 2+ filary czerwone!" if result["recession_alert"] else ""

    return (
        f"🏥 *Market Health Score: {result['score']}/100 — {result['mode']}*"
        f"{recession_line}\n\n"
        f"*3 Filary Recesji DNA:*\n"
        f"{_pillar_line('Produkcja przemysłowa', p['industrial_production'])}\n"
        f"{_pillar_line('Sprzedaż detaliczna',   p['retail_sales'])}\n"
        f"{_pillar_line('Wnioski o zasiłek',      p['jobless_claims'])}\n\n"
        f"*Top sygnały:*\n"
        f"  ✅ {top_pos or '—'}\n"
        f"  ⚠️ {top_neg or 'brak negatywnych sygnałów'}\n\n"
        f"*Implikacja dla portfela:* {result['action']}"
    )


def format_health_full(result: dict) -> str:
    """Full /zdrowie output — all 20 indicators."""
    p = result["pillars"]
    today = datetime.date.today().strftime("%d.%m.%Y")
    recession_line = "\n\n⚠️ *RECESJA ALERT — 2+ filary czerwone! Zmień strategię.*" if result["recession_alert"] else ""

    lines = [
        f"🏥 *Market Health Monitor — {today}*",
        f"*Score: {result['score']}/100 — {result['mode']}*{recession_line}",
        "",
        "*3 Filary Recesji DNA Rynków:*",
        _pillar_line("Produkcja przemysłowa", p["industrial_production"]),
        _pillar_line("Sprzedaż detaliczna",   p["retail_sales"]),
        _pillar_line("Wnioski o zasiłek",      p["jobless_claims"]),
        "",
        "*📊 Wszystkie wskaźniki:*",
    ]
    for r in result["indicators"]:
        sign = "+" if r["pts"] > 0 else ""
        lines.append(f"  {r['label']}: {sign}{r['pts']} pkt — {r['detail']}")

    lines += [
        "",
        f"*Implikacja dla portfela:*",
        f"  {result['action']}",
        "",
        "*Korekta vs Bessa — pamiętaj:*",
        "  📉 Korekta (10-20%) + zielone filary = OKAZJA. Nie sprzedawaj.",
        "  🐻 Bessa + 2 czerwone filary = REALNA BESSA. Redukuj.",
        "  ⚡ VIX>40 + zielone filary = FLASH CRASH. Najlepszy moment wejścia.",
    ]
    return "\n".join(lines)


def format_recession_pillars(result: dict | None = None) -> str:
    """Short /recesja output — only 3 pillars + trend."""
    if result is None:
        pillars = evaluate_recession_pillars()
    else:
        pillars = result["pillars"]
    p = pillars
    red = sum(1 for v in p.values() if v["status"] == "red")
    yellow = sum(1 for v in p.values() if v["status"] == "yellow")

    if red >= 2:
        verdict = "🔴 *RECESJA ALERT* — 2+ filary czerwone. Zmień strategię portfela."
    elif red == 1 or yellow >= 2:
        verdict = "🟡 *OSTROŻNOŚĆ* — sygnały ostrzegawcze. Monitoruj uważnie."
    else:
        verdict = "🟢 *HOSSA* — filary zdrowe. Korekty to okazje do kupna."

    return (
        f"🔍 *3 Filary Recesji DNA Rynków*\n\n"
        f"{_pillar_line('Produkcja przemysłowa', p['industrial_production'])}\n"
        f"{_pillar_line('Sprzedaż detaliczna',   p['retail_sales'])}\n"
        f"{_pillar_line('Wnioski o zasiłek',      p['jobless_claims'])}\n\n"
        f"{verdict}"
    )


def format_vix_analysis() -> str:
    """Short /vix output."""
    closes = _yf_close("^VIX", period="3mo")
    if not closes:
        return "⚠️ Nie mogę pobrać danych VIX."
    vix = closes[-1]
    vix_1m = sum(closes[-20:]) / 20 if len(closes) >= 20 else vix
    vix_3m = sum(closes) / len(closes)

    if vix > 40:
        interpretation = (
            "⚡ *PANIKA* — historycznie NAJLEPSZY moment wejścia.\n"
            "Fundamenty raczej OK? To flash crash. Kupuj stopniowo."
        )
    elif vix > 35:
        interpretation = (
            "🔴 *EKSTREMALNY STRACH* — dno może być blisko.\n"
            "Sprawdź filary recesji (/recesja) zanim zagrasz pod odbicie."
        )
    elif vix > 28:
        interpretation = (
            "⚠️ *STRACH* — redukuj ryzyko, ale nie panikuj.\n"
            "Historycznie VIX 28-35 = korekta, nie bessa (bez złych filarów)."
        )
    elif vix > 20:
        interpretation = "🟡 *LEKKI STRES* — normalny rynek z podwyższoną zmiennością."
    elif vix > 15:
        interpretation = "🟢 *NORMALNY* — zdrowe otoczenie dla risk-on."
    else:
        interpretation = (
            "🟢 *SPOKÓJ* — risk-on, rynek pewny siebie.\n"
            "Uwaga: niski VIX = mało zabezpieczeń = możliwy nagły spike."
        )

    return (
        f"😱 *VIX — Indeks Strachu*\n\n"
        f"Aktualny: *{vix:.1f}*\n"
        f"Średnia 1M: {vix_1m:.1f}\n"
        f"Średnia 3M: {vix_3m:.1f}\n\n"
        f"{interpretation}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ALERT SYSTEM
# ═══════════════════════════════════════════════════════════════════════════════

def check_and_send_alerts(new_result: dict):
    """Compare new score to persisted state and fire alerts if thresholds crossed."""
    state = _load_state()
    prev_score  = state.get("last_score")
    prev_mode   = state.get("last_mode", "")
    prev_pillars = state.get("last_pillars", {})

    alerts = []

    # Score drop >15 pts in a week
    if prev_score is not None:
        delta = new_result["score"] - prev_score
        if delta <= -15:
            alerts.append(
                f"📉 Score spadł o {abs(delta)} pkt "
                f"({prev_score} → {new_result['score']})"
            )

    # Mode change
    if prev_mode and prev_mode != new_result["mode"]:
        alerts.append(f"🔄 Tryb zmieniony: {prev_mode} → {new_result['mode']}")

    # Pillar status change
    new_pillars = {k: v["status"] for k, v in new_result["pillars"].items()}
    _NAMES = {
        "industrial_production": "Produkcja przemysłowa",
        "retail_sales":          "Sprzedaż detaliczna",
        "jobless_claims":        "Wnioski o zasiłek",
    }
    for key, new_status in new_pillars.items():
        old_status = prev_pillars.get(key, "green")
        if old_status != new_status:
            alerts.append(
                f"{'🔴' if new_status == 'red' else '🟡' if new_status == 'yellow' else '🟢'} "
                f"Filar *{_NAMES[key]}* zmienił status: {old_status} → {new_status}"
            )

    # VIX thresholds
    try:
        vix_pts, vix_detail = _score_vix()
        vix_val = float(vix_detail.split("VIX")[1].split()[0]) if "VIX" in vix_detail else None
        prev_vix = state.get("last_vix")
        if vix_val:
            for threshold in (28, 35):
                if prev_vix and prev_vix < threshold <= vix_val:
                    alerts.append(f"⚠️ VIX przebił {threshold} ({vix_val:.1f})")
            state["last_vix"] = vix_val
    except Exception:
        pass

    # 2+ red pillars
    if new_result["recession_alert"] and not state.get("recession_alert_sent"):
        alerts.append("⚠️ *RECESJA ALERT* — 2+ filary czerwone jednocześnie!")
        state["recession_alert_sent"] = True
    elif not new_result["recession_alert"]:
        state["recession_alert_sent"] = False

    if alerts:
        body = "\n".join(f"  • {a}" for a in alerts)
        msg = (
            f"🚨 *MARKET HEALTH ALERT*\n\n"
            f"{body}\n\n"
            f"Poprzedni score: {prev_score or '?'} → Obecny score: {new_result['score']}\n"
            f"Tryb: {new_result['mode']}\n"
            f"Działanie: {new_result['action']}"
        )
        try:
            _ctx.app.client.chat_postMessage(channel=STOCK_CHANNEL_ID, text=msg)
        except Exception as e:
            logger.error("Alert post failed: %s", e)

    # Persist new state
    state.update({
        "last_score":   new_result["score"],
        "last_mode":    new_result["mode"],
        "last_pillars": new_pillars,
        "last_updated": new_result["timestamp"],
    })
    _save_state(state)


# ═══════════════════════════════════════════════════════════════════════════════
# SCHEDULED JOBS & PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def run_market_health() -> dict:
    """Compute market health, check alerts, cache result. Returns result dict."""
    result = compute_market_health()
    check_and_send_alerts(result)
    return result


def send_daily_health_header():
    """Post brief Market Health header to #inwestowanie (called before stock digest)."""
    try:
        result = run_market_health()
        text = format_health_header(result)
        _ctx.app.client.chat_postMessage(channel=STOCK_CHANNEL_ID, text=text)
    except Exception as e:
        logger.error("send_daily_health_header failed: %s", e)


def get_health_header_text() -> str:
    """Return header text for embedding in digest without posting separately."""
    try:
        result = run_market_health()
        return format_health_header(result)
    except Exception as e:
        logger.error("get_health_header_text failed: %s", e)
        return ""


def run_zdrowie_command() -> str:
    """Full market health for /zdrowie command."""
    result = run_market_health()
    return format_health_full(result)


def run_recesja_command() -> str:
    """/recesja — only 3 pillars."""
    pillars = evaluate_recession_pillars()
    red = sum(1 for v in pillars.values() if v["status"] == "red")
    yellow = sum(1 for v in pillars.values() if v["status"] == "yellow")
    if red >= 2:
        verdict = "🔴 *RECESJA ALERT* — 2+ filary czerwone. Zmień strategię portfela."
    elif red == 1 or yellow >= 2:
        verdict = "🟡 *OSTROŻNOŚĆ* — sygnały ostrzegawcze. Monitoruj uważnie."
    else:
        verdict = "🟢 *HOSSA* — filary zdrowe. Korekty to okazje do kupna."
    p = pillars
    return (
        f"🔍 *3 Filary Recesji DNA Rynków*\n\n"
        f"{_pillar_line('Produkcja przemysłowa', p['industrial_production'])}\n"
        f"{_pillar_line('Sprzedaż detaliczna',   p['retail_sales'])}\n"
        f"{_pillar_line('Wnioski o zasiłek',      p['jobless_claims'])}\n\n"
        f"{verdict}"
    )


def run_vix_command() -> str:
    """/vix — VIX with interpretation."""
    return format_vix_analysis()
