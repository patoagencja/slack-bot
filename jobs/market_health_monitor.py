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
import warnings

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


def _score_vix_structure() -> tuple[int, str]:
    """B1: VIX term structure (leading) + SKEW index.
    VIX spot alone is lagging. Term structure & SKEW anticipate moves by 2-6 weeks.
    """
    vix_spot = _yf_latest("^VIX")
    vix3m    = _yf_latest("^VIX3M")

    # Term structure signal — graduated by depth
    structure_pts = 0
    structure_label = "brak danych"
    if vix_spot and vix3m:
        diff = vix3m - vix_spot  # positive = contango, negative = backwardation
        if diff > 2:
            structure_pts   = 4
            structure_label = f"🟢 Contango VIX {vix_spot:.1f} / VIX3M {vix3m:.1f} (+{diff:.1f}) — rynek spokojny"
        elif diff > 0.5:
            structure_pts   = 2
            structure_label = f"🟢 Lekki contango VIX {vix_spot:.1f} / VIX3M {vix3m:.1f} (+{diff:.1f})"
        elif diff >= -0.5:
            structure_pts   = 0
            structure_label = f"🟡 VIX ≈ VIX3M ({vix_spot:.1f} / {vix3m:.1f}, diff {diff:+.1f}) — struktury zbliżone"
        elif diff >= -2:
            structure_pts   = -2
            structure_label = f"⚠️ Lekka backwardation VIX {vix_spot:.1f} > VIX3M {vix3m:.1f} ({diff:.1f}) — krótkoterm. strach rośnie"
        elif diff >= -5:
            structure_pts   = -4
            structure_label = f"🔴 Wyraźna backwardation VIX {vix_spot:.1f} >> VIX3M {vix3m:.1f} ({diff:.1f}) — alarm 1-3 tyg."
        else:
            structure_pts   = -6
            structure_label = f"🔴🔴 GŁĘBOKA BACKWARDATION VIX {vix_spot:.1f} >> VIX3M {vix3m:.1f} ({diff:.1f}) — rynek w panice"
    elif vix_spot:
        if vix_spot < 18:
            structure_pts, structure_label = 3, f"🟢 VIX {vix_spot:.1f} (VIX3M niedostępne)"
        elif vix_spot < 25:
            structure_pts, structure_label = 1, f"🟡 VIX {vix_spot:.1f} — lekki stres"
        else:
            structure_pts, structure_label = -3, f"🔴 VIX {vix_spot:.1f} — podwyższony"

    # SKEW signal — graduated by level (old: flat >140 = -3; new: 6-step scale)
    skew_pts = 0
    skew_label = ""
    skew_snippet = _tavily_snippet("CBOE SKEW index tail risk options latest 2026", 200)
    import re as _re
    m = _re.search(r'\b(1[0-9][0-9]|[2-9][0-9])\b', skew_snippet)
    skew_val = int(m.group()) if m else None
    if skew_val:
        if skew_val > 160:
            skew_pts   = -4
            skew_label = f" | SKEW {skew_val} 🔴🔴 — ekstremalny poziom (historycznie >170 poprzedza większe korekty)"
        elif skew_val > 150:
            skew_pts   = -2
            skew_label = f" | SKEW {skew_val} 🔴 — instytucje się zabezpieczają (podwyższony, nie ekstremalny)"
        elif skew_val > 140:
            skew_pts   = -1
            skew_label = f" | SKEW {skew_val} 🟡 — lekko podwyższony, monitoruj"
        elif skew_val >= 130:
            skew_pts   = 0
            skew_label = f" | SKEW {skew_val} 🟡 — na górnej granicy normy"
        elif skew_val >= 120:
            skew_pts   = 1
            skew_label = f" | SKEW {skew_val} 🟢 — normalny poziom ochrony"
        else:
            skew_pts   = 2
            skew_label = f" | SKEW {skew_val} 🟢 — niski poziom strachu"

    total_pts = max(-8, structure_pts + skew_pts)
    return total_pts, f"{structure_label}{skew_label}"


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
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
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
    """B4: HYG/TLT credit spread proxy — leading indicator, anticipates equity moves 2-4 weeks.
    Detect 2-week rising trend (not just snapshot level).
    """
    hyg = _yf_close("HYG", period="3mo")
    tlt = _yf_close("TLT", period="3mo")
    if len(hyg) < 20 or len(tlt) < 20:
        snippet = _tavily_snippet("HYG LQD credit spreads high yield tightening widening 2026", 200)
        sl = snippet.lower()
        if any(w in sl for w in ("tighten", "narrow", "zawężają")):
            return 4, "🟢 Spready kredytowe zawężają się (leading — akcje opóźnione 2-4 tyg.)"
        if any(w in sl for w in ("widen", "blow", "rozszerzają")):
            return -4, "🔴 Spready kredytowe rosną (OSTRZEŻENIE — akcje zazwyczaj reagują z opóźnieniem)"
        return 1, "Credit spreads: neutralne"

    # Build daily HYG/TLT ratio series
    n = min(len(hyg), len(tlt))
    ratios = [hyg[-(n - i)] / tlt[-(n - i)] for i in range(n - 1, -1, -1)]
    ratio_now  = ratios[-1]
    ratio_10d  = sum(ratios[-10:]) / 10 if len(ratios) >= 10 else ratio_now
    ratio_20d  = sum(ratios[-20:]) / 20

    pct_vs_20d = (ratio_now / ratio_20d - 1) * 100
    pct_vs_10d = (ratio_now / ratio_10d - 1) * 100

    # 2-week consecutive decline in ratio = spreads rising = warning
    trend_weeks = 0
    if len(ratios) >= 10 and ratios[-5] < ratios[-10]:
        trend_weeks += 1
    if len(ratios) >= 5 and ratios[-1] < ratios[-5]:
        trend_weeks += 1

    if pct_vs_20d > 1 and pct_vs_10d > 0:
        return 4, f"🟢 HYG/TLT {pct_vs_20d:+.1f}% vs 20d — spready zawężają się (risk-on)"
    if pct_vs_20d > -1:
        return 2, f"🟡 HYG/TLT {pct_vs_20d:+.1f}% vs 20d — stabilne"
    if trend_weeks >= 2:
        return -5, f"🔴 HYG/TLT {pct_vs_20d:.1f}% vs 20d — SPREADY ROSNĄ 2 TYG. — akcje opóźnione!"
    if pct_vs_20d < -3:
        return -5, f"🔴 HYG/TLT {pct_vs_20d:.1f}% — gwałtowne rozszerzenie!"
    return -3, f"⚠️ HYG/TLT {pct_vs_20d:.1f}% vs 20d — spready rosną ({trend_weeks}/2 tygodnie)"


def _score_smart_money_flow() -> tuple[int, str]:
    """B5: Smart money flow — SPY price/volume analysis + institutional flow Tavily.
    Distribution: price rises on falling volume (institutions selling into strength).
    Accumulation: price falls on rising volume (institutions buying weakness).
    """
    spy_hist = yf.Ticker("SPY").history(period="30d", auto_adjust=True)
    if len(spy_hist) >= 10:
        closes  = list(spy_hist["Close"])
        volumes = list(spy_hist["Volume"])
        # Last 10 sessions
        price_chg_10d = (closes[-1] / closes[-10] - 1) * 100
        vol_now_avg   = sum(volumes[-5:])  / 5
        vol_prev_avg  = sum(volumes[-15:-5]) / 10 if len(volumes) >= 15 else vol_now_avg
        vol_ratio = vol_now_avg / vol_prev_avg if vol_prev_avg > 0 else 1.0

        if price_chg_10d > 1 and vol_ratio < 0.85:
            spy_signal = -3
            spy_label  = f"⚠️ SPY +{price_chg_10d:.1f}% na wolumenie -{(1-vol_ratio)*100:.0f}% — dystrybucja (instytucje sprzedają)"
        elif price_chg_10d < -1 and vol_ratio > 1.2:
            spy_signal = 3
            spy_label  = f"🟢 SPY {price_chg_10d:.1f}% na wolumenie +{(vol_ratio-1)*100:.0f}% — akumulacja (instytucje kupują)"
        elif price_chg_10d > 1 and vol_ratio >= 1.1:
            spy_signal = 3
            spy_label  = f"🟢 SPY +{price_chg_10d:.1f}% na rosnącym wolumenie (conviction)"
        else:
            spy_signal = 1
            spy_label  = f"🟡 SPY {price_chg_10d:+.1f}% / vol ratio {vol_ratio:.2f} — neutralny"
    else:
        spy_signal, spy_label = 1, "SPY: brak danych"

    # Institutional flow from Tavily
    snippet = _tavily_snippet("institutional investors equity selling buying flows 2026", 200)
    sl = snippet.lower()
    if any(w in sl for w in ("selling", "reducing", "distribution", "outflow")):
        inst_signal, inst_label = -2, " | Instytucje: sprzedają"
    elif any(w in sl for w in ("buying", "accumulating", "inflow", "adding")):
        inst_signal, inst_label = 2, " | Instytucje: akumulują"
    else:
        inst_signal, inst_label = 0, ""

    total = max(-5, min(4, spy_signal + inst_signal))
    return total, f"{spy_label}{inst_label}"


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


def _score_rotation() -> tuple[int, str]:
    """B8: QQQ vs defensive rotation (XLU + GLD).
    When QQQ underperforms defensives 2+ weeks BEFORE VIX spikes:
    capital quietly rotating out of risk — one of the best early warning signals.
    """
    qqq = _yf_close("QQQ", period="3mo")
    xlu = _yf_close("XLU", period="3mo")
    gld = _yf_close("GLD", period="3mo")
    if len(qqq) < 15 or len(xlu) < 15 or len(gld) < 15:
        snippet = _tavily_snippet("tech growth vs defensive utilities rotation stock market 2026", 200)
        sl = snippet.lower()
        if any(w in sl for w in ("defensive", "utilities", "rotation out of tech")):
            return -3, "⚠️ Rotacja do defensywnych — risk-off sygnał"
        return 1, "Rotacja: brak danych"

    # 10-day relative performance
    qqq_10d = (qqq[-1] / qqq[-10] - 1) * 100
    xlu_10d = (xlu[-1] / xlu[-10] - 1) * 100
    gld_10d = (gld[-1] / gld[-10] - 1) * 100
    def_avg  = (xlu_10d + gld_10d) / 2
    spread_10d = qqq_10d - def_avg

    # 20-day trend
    qqq_20d  = (qqq[-1] / qqq[-20] - 1) * 100
    def_20d  = ((xlu[-1] / xlu[-20] - 1) + (gld[-1] / gld[-20] - 1)) / 2 * 100
    spread_20d = qqq_20d - def_20d

    if spread_10d > 3 and spread_20d > 0:
        return 3, f"🟢 QQQ bije defensywne +{spread_10d:.1f}% (10d) — risk-on, rotacja do wzrostowych"
    if spread_10d > 0:
        return 1, f"🟡 QQQ nieznacznie lepszy {spread_10d:+.1f}% (10d) — neutralny"
    if spread_10d < -3 and spread_20d < 0:
        return -3, f"🔴 QQQ gorzej od defensywnych {spread_10d:.1f}% (10d) / {spread_20d:.1f}% (20d) — cicha rotacja risk-off!"
    return -1, f"🟡 QQQ lekko słabszy {spread_10d:.1f}% (10d) — obserwuj"


def _score_naaim() -> tuple[int, str]:
    """C7: NAAIM Exposure Index — active manager equity exposure.
    >80%: everyone bought in, little fuel left.
    Rapid drop from >80% to <60%: institutions massively reducing = ALARM.
    """
    snippet = _tavily_snippet("NAAIM exposure index active managers equity allocation latest 2026", 300)
    import re as _re
    m = _re.search(r'\b(\d{2,3}(?:\.\d+)?)\b', snippet)
    naaim = float(m.group(1)) if m and float(m.group(1)) <= 200 else None
    sl = snippet.lower()

    if naaim is None:
        if any(w in sl for w in ("bearish", "reducing", "defensive")):
            return 3, "🟢 NAAIM: aktywni zarządzający defensywnie (paliwo do wzrostów)"
        if any(w in sl for w in ("bullish", "max", "overweight")):
            return -2, "⚠️ NAAIM: zarządzający maksymalnie zaangażowani"
        return 1, "NAAIM: brak danych"

    if naaim < 40:
        return 4, f"🟢 NAAIM {naaim:.0f}% — duże niedoważenie, dużo gotówki na boku"
    if naaim < 60:
        return 2, f"🟢 NAAIM {naaim:.0f}% — normalne zaangażowanie"
    if naaim < 80:
        return 0, f"🟡 NAAIM {naaim:.0f}% — podwyższone zaangażowanie, mniej paliwa"
    return -3, f"🔴 NAAIM {naaim:.0f}% — wszyscy kupieni! Mało paliwa do wzrostów, ryzyko korekty"


def _score_earnings_revisions() -> tuple[int, str]:
    """C8: S&P500 aggregate earnings revision momentum.
    More downgrades than upgrades 3+ weeks = fundamentals deteriorating before price reacts.
    Anticipates correction 4-8 weeks ahead.
    """
    snippet = _tavily_snippet("S&P500 earnings revisions up down ratio analyst upgrades downgrades 2026", 300)
    sl = snippet.lower()
    import re as _re

    # Look for up/down ratio or directional language
    m_ratio = _re.search(r'(\d+(?:\.\d+)?)\s*(?:to|:)\s*(\d+(?:\.\d+)?)\s*(?:up|down|ratio)', sl)
    if m_ratio:
        up_n, dn_n = float(m_ratio.group(1)), float(m_ratio.group(2))
        ratio = up_n / dn_n if dn_n > 0 else 1.5
        if ratio > 1.5:
            return 4, f"🟢 Rewizje EPS: {up_n:.0f} w górę vs {dn_n:.0f} w dół — fundamenty poprawiają się"
        if ratio > 1.0:
            return 2, f"🟡 Rewizje EPS: lekka przewaga wzrostów ({ratio:.1f}x)"
        if ratio > 0.7:
            return -1, f"⚠️ Rewizje EPS: więcej obniżek ({ratio:.1f}x)"
        return -4, f"🔴 Rewizje EPS: silna fala obniżek ({ratio:.1f}x) — fundamenty się pogarszają"

    if any(w in sl for w in ("more upgrades", "positive revisions", "raising estimates", "beat")):
        return 3, "🟢 Rewizje EPS: przewaga wzrostów — fundamenty OK"
    if any(w in sl for w in ("more downgrades", "cutting estimates", "negative revisions", "miss")):
        return -3, "🔴 Rewizje EPS: przewaga obniżek — OSTRZEŻENIE (wyprzedza korektę 4-8 tyg.)"
    if any(w in sl for w in ("mixed", "balanced")):
        return 1, "🟡 Rewizje EPS: mieszane sygnały"
    return 1, "🟡 Rewizje EPS: brak wyraźnego sygnału"


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

# Raw score range (22 indicators): min ≈ -58, max ≈ +73 → normalise to 0-100
_RAW_MIN = -58.0
_RAW_MAX =  73.0

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
    # Kategoria B: Rynkowe / Early Warning (35%)
    (_score_vix_structure,     1.2, "B1. VIX Structure + SKEW"),   # leading, higher weight
    (_score_sp500_ma200,       1.0, "B2. S&P500/MA200"),
    (_score_market_breadth,    1.0, "B3. Market Breadth"),
    (_score_credit_spreads,    1.2, "B4. Credit Spreads (leading)"),# leading, higher weight
    (_score_smart_money_flow,  1.0, "B5. Smart Money Flow"),
    (_score_put_call_ratio,    0.8, "B6. Put/Call Ratio"),
    (_score_seasonality,       0.5, "B7. Sezonowość"),
    (_score_rotation,          1.1, "B8. QQQ vs Defensive Rotation"), # leading
    # Kategoria C: Sentyment (30%)
    (_score_fund_manager_survey, 0.9, "C1. BofA FMS"),
    (_score_fear_greed,          1.0, "C2. Fear & Greed"),
    (_score_retail_flows,        0.8, "C3. Retail Flows"),
    (_score_dxy,                 0.8, "C4. DXY"),
    (_score_gold_vs_equities,    0.7, "C5. Gold/Equities"),
    (_score_treasury_yield,      1.0, "C6. 10Y Yield"),
    (_score_naaim,               1.0, "C7. NAAIM Exposure"),
    (_score_earnings_revisions,  1.1, "C8. EPS Revision Momentum"), # leading
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

    if score >= 70:
        mode = "🟢 BULL MODE"
        action = "Rynek zdrowy. Korekty to okazje do kupna. Trzymaj pełną ekspozycję."
    elif score >= 50:
        mode = "🟡 CAUTION MODE"
        action = "Rynek OK ale są ryzyka. Redukuj najbardziej ryzykowne pozycje. Trzymaj 10-20% gotówki."
    elif score >= 30:
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


def _build_trend_context(state: dict, new_score: int) -> str:
    """Compute 4-week score trend from history. Returns 1-line summary."""
    history = state.get("score_history", [])
    if not history:
        return ""
    prev_score = history[-1].get("score")
    delta = new_score - prev_score if prev_score is not None else None
    delta_str = (f"{delta:+d} pkt" if delta is not None else "")

    # 4-week trend: compare oldest vs newest in history
    trend = ""
    if len(history) >= 2:
        oldest = history[0].get("score", new_score)
        if new_score > oldest + 5:
            trend = "📈 rosnący"
        elif new_score < oldest - 5:
            trend = "📉 spadający"
        else:
            trend = "➡️ stabilny"

    parts = []
    if prev_score is not None:
        parts.append(f"Poprzedni odczyt: {prev_score}")
    if delta_str:
        parts.append(f"Zmiana: {delta_str}")
    if trend:
        parts.append(f"Trend 4 tygodnie: {trend}")
    return " | ".join(parts)


def _update_score_history(state: dict, score: int, timestamp: str):
    """Append score to rolling 5-entry history (≈ 4 weeks of daily readings)."""
    history = state.get("score_history", [])
    history.append({"score": score, "ts": timestamp})
    state["score_history"] = history[-5:]


# ═══════════════════════════════════════════════════════════════════════════════
# SLACK FORMATTERS
# ═══════════════════════════════════════════════════════════════════════════════

def _pillar_line(name: str, data: dict) -> str:
    detail = data["detail"]
    if detail == "brak danych":
        detail = "brak danych FRED (ustaw FRED_API_KEY)"
    return f"  {data['label']} {name}: {detail}"


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

    # Trend line from persisted history
    state = _load_state()
    trend_ctx = _build_trend_context(state, result["score"])
    trend_line = f"\n_{trend_ctx}_" if trend_ctx else ""

    lines = [
        f"🏥 *Market Health Monitor — {today}*",
        f"*Score: {result['score']}/100 — {result['mode']}*{recession_line}{trend_line}",
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

    # Extract early warning indicators for separate section
    _ew_labels = {"B1. VIX Structure + SKEW", "B4. Credit Spreads (leading)",
                  "B8. QQQ vs Defensive Rotation", "C8. EPS Revision Momentum"}
    ew_results = [r for r in result["indicators"] if r["label"] in _ew_labels]

    lines += [
        "",
        "*📡 Early Warning Signals (wyprzedzają VIX o 2-6 tyg.):*",
    ]
    for r in ew_results:
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
        "  ⚡ VIX>40 + backwardation odwrócona + filary zielone = FLASH CRASH. Najlepszy moment wejścia.",
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
    """Full /vix output — term structure + SKEW + spot level interpretation."""
    vix_spot = _yf_latest("^VIX")
    vix3m    = _yf_latest("^VIX3M")
    closes   = _yf_close("^VIX", period="3mo")

    if not vix_spot:
        return "⚠️ Nie mogę pobrać danych VIX."

    vix_1m = sum(closes[-20:]) / 20 if len(closes) >= 20 else vix_spot
    vix_3m = sum(closes) / len(closes) if closes else vix_spot

    # Term structure
    if vix3m:
        diff = vix3m - vix_spot
        if diff > 2:
            ts_line  = f"🟢 *Contango* — VIX3M {vix3m:.1f} > VIX {vix_spot:.1f} (+{diff:.1f})"
            ts_interp = "Rynek spokojny, nikt się nie boi krótkoterminowo. Normalny stan."
        elif diff >= 0:
            ts_line  = f"🟡 *Contango płaski* — VIX3M {vix3m:.1f} ≈ VIX {vix_spot:.1f} ({diff:+.1f})"
            ts_interp = "Rynek zaczyna się bać krótkoterminowo. Monitoruj."
        elif diff > -2:
            ts_line  = f"⚠️ *Backwardation* — VIX {vix_spot:.1f} > VIX3M {vix3m:.1f} ({diff:.1f})"
            ts_interp = "Rynek boi się TERAZ bardziej niż przyszłości. Ostrzeżenie 1-3 tygodnie."
        else:
            ts_line  = f"🔴 *BACKWARDATION GŁĘBOKA* — VIX {vix_spot:.1f} >> VIX3M {vix3m:.1f} ({diff:.1f})"
            ts_interp = "ALARM — rynek w panice krótkoterminowej. Historycznie wyprzedza duże spadki."
    else:
        ts_line  = f"VIX spot: {vix_spot:.1f} (VIX3M niedostępne)"
        ts_interp = "Nie można ocenić term structure."

    # SKEW
    skew_snippet = _tavily_snippet("CBOE SKEW index tail risk options latest 2026", 200)
    import re as _re
    m_skew = _re.search(r'\b(1[0-9][0-9])\b', skew_snippet)
    skew_val = int(m_skew.group()) if m_skew else None
    if skew_val:
        if skew_val > 160:
            skew_line = (f"🔴🔴 SKEW {skew_val} — ekstremalny poziom "
                         f"(historycznie >170 poprzedza większe korekty; obecny poziom = poważny sygnał)")
        elif skew_val > 150:
            skew_line = (f"🔴 SKEW {skew_val} — podwyższony ale nie ekstremalny "
                         f"(historycznie SKEW >170 poprzedza większe korekty; obecny poziom = ostrożność, nie panika)")
        elif skew_val > 140:
            skew_line = f"🟡 SKEW {skew_val} — lekko podwyższony, monitoruj (norma 110-130)"
        elif skew_val >= 130:
            skew_line = f"🟡 SKEW {skew_val} — na górnej granicy normy"
        elif skew_val >= 120:
            skew_line = f"🟢 SKEW {skew_val} — normalny poziom ochrony"
        else:
            skew_line = f"🟢 SKEW {skew_val} — spokój, niski poziom strachu"
    else:
        skew_line = "SKEW: brak danych"

    # VIX spot interpretation (context, NOT the primary signal)
    if vix_spot > 40:
        spot_interp = "⚡ *PANIKA* — historycznie NAJLEPSZY moment wejścia (jeśli filary zielone!)"
    elif vix_spot > 35:
        spot_interp = "🔴 *EKSTREMALNY STRACH* — sprawdź /recesja przed graniem pod odbicie"
    elif vix_spot > 28:
        spot_interp = "⚠️ *STRACH* — zazwyczaj korekta (nie bessa, jeśli filary OK)"
    elif vix_spot > 20:
        spot_interp = "🟡 *LEKKI STRES* — normalny rynek z podwyższoną zmiennością"
    elif vix_spot > 15:
        spot_interp = "🟢 *NORMALNY* — zdrowe otoczenie risk-on"
    else:
        spot_interp = "🟢 *SPOKÓJ* — niski VIX = mało zabezpieczeń = możliwy nagły spike"

    # Combined signal
    if vix3m and vix_spot > vix3m and (skew_val or 0) > 130:
        combined = "🔴 *ŁĄCZNY SYGNAŁ: WARNING* — backwardation + SKEW podwyższony"
    elif vix3m and vix_spot > vix3m:
        combined = "⚠️ *ŁĄCZNY SYGNAŁ: CAUTION* — backwardation (monitoruj uważnie)"
    elif (skew_val or 0) > 140:
        combined = "⚠️ *ŁĄCZNY SYGNAŁ: CAUTION* — SKEW wysoki mimo spokojnej struktury"
    else:
        combined = "🟢 *ŁĄCZNY SYGNAŁ: CLEAR* — brak wczesnych ostrzeżeń"

    return (
        f"📡 *VIX Early Warning System*\n\n"
        f"*Term Structure (LEADING — wyprzedza 1-3 tyg.):*\n"
        f"  {ts_line}\n"
        f"  _{ts_interp}_\n\n"
        f"*SKEW (LEADING — wyprzedza 2-6 tyg.):*\n"
        f"  {skew_line}\n\n"
        f"*VIX Spot (lagging — kontekst):*\n"
        f"  Aktualny: *{vix_spot:.1f}* | Śr. 1M: {vix_1m:.1f} | Śr. 3M: {vix_3m:.1f}\n"
        f"  _{spot_interp}_\n\n"
        f"{combined}"
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

    # Pillar status change — ignore grey (= brak danych FRED), only real changes matter
    new_pillars = {k: v["status"] for k, v in new_result["pillars"].items()}
    _NAMES = {
        "industrial_production": "Produkcja przemysłowa",
        "retail_sales":          "Sprzedaż detaliczna",
        "jobless_claims":        "Wnioski o zasiłek",
    }
    _PILLAR_EXPLAIN = {
        "industrial_production": "fabryki produkują mniej — wczesny sygnał spowolnienia",
        "retail_sales":          "Polacy/Amerykanie mniej wydają — konsumpcja hamuje",
        "jobless_claims":        "coraz więcej osób traci pracę — rynek pracy się psuje",
    }
    for key, new_status in new_pillars.items():
        old_status = prev_pillars.get(key, "grey")
        # Skip grey→grey or any→grey (just missing data, not a real signal)
        if new_status == "grey":
            continue
        if old_status == new_status:
            continue
        emoji = "🔴" if new_status == "red" else "🟡" if new_status == "yellow" else "🟢"
        explain = _PILLAR_EXPLAIN.get(key, "")
        alerts.append(
            f"{emoji} Filar *{_NAMES[key]}* zmienił status: {old_status} → {new_status}"
            + (f"\n    _(co to znaczy: {explain})_" if explain else "")
        )

    # Leading indicator alerts (fire BEFORE VIX spikes)
    try:
        # VIX term structure — alert only on meaningful backwardation (>2 pts)
        vix_spot = _yf_latest("^VIX")
        vix3m    = _yf_latest("^VIX3M")
        prev_structure = state.get("last_vix_structure", "contango")
        if vix_spot and vix3m:
            diff = vix3m - vix_spot
            # Granular structure classification
            if diff > 2:
                curr_structure = "contango"
            elif diff >= -0.5:
                curr_structure = "flat"
            elif diff >= -2:
                curr_structure = "backwardation_light"
            elif diff >= -5:
                curr_structure = "backwardation_strong"
            else:
                curr_structure = "backwardation_deep"

            # Alert only when meaningful backwardation: diff < -2
            if curr_structure in ("backwardation_strong", "backwardation_deep") and \
               prev_structure not in ("backwardation_strong", "backwardation_deep"):
                alerts.append(
                    f"📡 *VIX: wyraźna backwardation* — VIX {vix_spot:.1f} > VIX3M {vix3m:.1f} (różnica {abs(diff):.1f} pkt)\n"
                    f"    _(rynek boi się najbliższych tygodni bardziej niż kolejnych miesięcy. "
                    f"Lekka backwardation <2 pkt to szum; wyraźna >2 pkt to wczesny sygnał — historycznie wyprzedza duże spadki o 1-3 tyg.)_"
                )
            elif prev_structure in ("contango",) and curr_structure == "flat":
                # Gentle heads-up — don't fire an alarm, just note
                logger.info("VIX structure flattening: %s → %s (diff %.1f), no alert", prev_structure, curr_structure, diff)

            state["last_vix_structure"] = curr_structure
            state["last_vix"] = vix_spot

        # SKEW alert — only at extreme levels (>160), with historical context
        skew_snippet = _tavily_snippet("CBOE SKEW index tail risk latest 2026", 150)
        import re as _re
        m_skew = _re.search(r'\b(1[0-9][0-9])\b', skew_snippet)
        if m_skew:
            skew_val = int(m_skew.group())
            prev_skew = state.get("last_skew", 120)
            if prev_skew < 160 <= skew_val:
                alerts.append(
                    f"📡 *SKEW Index przekroczył 160* (obecnie {skew_val})\n"
                    f"    _SKEW {skew_val} — podwyższony ale historycznie dopiero >170 poprzedzało większe korekty. "
                    f"Obecny poziom = ostrożność, nie panika. "
                    f"Duże fundusze kupują opcje put — ubezpieczają się. Normalne wartości: 110-130._"
                )
            elif 140 < skew_val <= 160 and prev_skew <= 140:
                # Only log, no alert — this was the old threshold, now just informational
                logger.info("SKEW entered 140-160 zone (%d→%d), below 160 alert threshold", prev_skew, skew_val)
            state["last_skew"] = skew_val

        # Credit spreads — alert only on significant 2-week widening
        credit_pts, credit_detail = _score_credit_spreads()
        prev_credit = state.get("last_credit_pts", 2)
        if credit_pts <= -5 and prev_credit > -3:
            alerts.append(
                f"📡 *Spready kredytowe istotnie rosną* — {credit_detail[:80]}\n"
                f"    _(rynek wycenia wyższe ryzyko bankructw firm. Akcje reagują z 2-4 tyg. opóźnieniem. "
                f"Alert tylko przy silnym 2-tygodniowym trendzie — graniczna zmiana nie wystarczy.)_"
            )
        state["last_credit_pts"] = credit_pts

        # Rotation — QQQ underperforming defensives
        rot_pts, rot_detail = _score_rotation()
        prev_rot = state.get("last_rotation_pts", 1)
        if rot_pts <= -3 and prev_rot > -1:
            alerts.append(
                f"📡 *Kapitał cicho ucieka z growth do defensywnych*\n"
                f"    _({rot_detail[:80]})\n"
                f"    Fundusze rotują do prądu/złota/farmacji bez paniki w VIX — to wczesny sygnał.)_"
            )
        state["last_rotation_pts"] = rot_pts

    except Exception as e:
        logger.warning("Leading indicator alert check error: %s", e)

    # 2+ red pillars
    if new_result["recession_alert"] and not state.get("recession_alert_sent"):
        alerts.append("⚠️ *RECESJA ALERT* — 2+ filary czerwone jednocześnie!")
        state["recession_alert_sent"] = True
    elif not new_result["recession_alert"]:
        state["recession_alert_sent"] = False

    # Build trend context string
    trend_ctx = _build_trend_context(state, new_result["score"])

    if alerts:
        body = "\n".join(f"  • {a}" for a in alerts)
        score_line = (
            f"Score: {prev_score} → {new_result['score']}/100"
            if prev_score is not None
            else f"Score: {new_result['score']}/100"
        )
        _MODE_EXPLAIN = {
            "BULL MODE":      "rynek zdrowy — korekty to okazje do kupna",
            "CAUTION MODE":   "warto trzymać 10-20% gotówki, być selektywnym",
            "DEFENSIVE MODE": "redukuj ryzyko do 50-60% ekspozycji, gotówka to pozycja",
            "BEAR MODE":      "minimalizuj ekspozycję, rozważ hedging",
        }
        mode_explain = _MODE_EXPLAIN.get(new_result["mode"], "")
        msg = (
            f"🚨 *MARKET HEALTH ALERT*\n\n"
            f"{body}\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📊 {score_line} — *{new_result['mode']}*\n"
            + (f"_{mode_explain}_\n" if mode_explain else "")
            + (f"📈 _{trend_ctx}_\n" if trend_ctx else "")
            + f"\n💼 *Co robić:* {new_result['action']}"
        )
        try:
            _ctx.app.client.chat_postMessage(channel=STOCK_CHANNEL_ID, text=msg)
        except Exception as e:
            logger.error("Alert post failed: %s", e)

    # Persist new state (including rolling score history)
    _update_score_history(state, new_result["score"], new_result["timestamp"])
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
