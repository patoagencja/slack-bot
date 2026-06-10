"""
jobs/correction_probability.py — Correction Probability Dashboard

Scores the probability of a >20% S&P500 correction across 3 layers:
  Layer 1 — Economic Fundamentals  (max 50 pts, F1a/F1b/F1c/F2)
  Layer 2 — Market Leading Indicators (max 30 pts, R1-R5)
  Layer 3 — Sentiment                  (max 20 pts, S1-S4)

DNA cap rule: if all 3 economic pillars (F1a+F1b+F1c) = 0 → total capped at 35.

Status levels:
  SPOKOJNIE  0-20
  CZUJNOŚĆ  21-40
  OSTROŻNOŚĆ 41-60
  UWAGA      61-80
  ALARM      81-100
"""

import os
import json
import logging
import datetime
import re

import requests as _requests
import yfinance as yf

import _ctx

logger = logging.getLogger(__name__)

# ── FRED ──────────────────────────────────────────────────────────────────────
_FRED_KEY  = os.environ.get("FRED_API_KEY", "")
_FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

# ── Tavily ────────────────────────────────────────────────────────────────────
try:
    from tavily import TavilyClient
    _TAVILY_KEY = os.environ.get("TAVILY_API_KEY", "")
    _tavily = TavilyClient(api_key=_TAVILY_KEY) if _TAVILY_KEY else None
except ImportError:
    _tavily = None

# ── History persistence ───────────────────────────────────────────────────────
_HIST_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "correction_history.json")
_MAX_HIST  = 8


def _load_history() -> list:
    try:
        with open(_HIST_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def _save_history(entries: list):
    try:
        os.makedirs(os.path.dirname(_HIST_FILE), exist_ok=True)
        with open(_HIST_FILE, "w") as f:
            json.dump(entries[-_MAX_HIST:], f, indent=2)
    except Exception as e:
        logger.warning("correction_probability: save_history failed: %s", e)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fred(series: str, limit: int = 13) -> list:
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


def _yf_close(ticker: str, period: str = "6mo") -> list:
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


def _tavily_snippet(query: str, chars: int = 400) -> str:
    if _tavily is None:
        return ""
    try:
        r = _tavily.search(query, max_results=3)
        return " ".join((x.get("content") or "")[:150] for x in (r.get("results") or []))[:chars]
    except Exception:
        return ""


# ── Layer 1: Economic Fundamentals (max 50 pts) ───────────────────────────────

def _f1a_indpro() -> tuple[int, str]:
    """Industrial production — consecutive monthly declines."""
    vals = _fred("INDPRO", limit=6)
    if len(vals) < 3:
        return 0, "F1a INDPRO: brak danych"
    declines = 0
    for i in range(len(vals) - 1, 0, -1):
        if vals[i] < vals[i - 1]:
            declines += 1
        else:
            break
    if declines >= 3:
        return 15, f"🔴 INDPRO: {declines} kolejne spadki"
    if declines == 2:
        return 8, f"🟡 INDPRO: {declines} kolejne spadki"
    if declines == 1:
        return 5, f"⚠️ INDPRO: {declines} spadek"
    return 0, f"🟢 INDPRO: {vals[-1]:.1f} — stabilna"


def _f1b_rsxfs() -> tuple[int, str]:
    """Retail sales — consecutive monthly declines."""
    vals = _fred("RSXFS", limit=6)
    if len(vals) < 3:
        return 0, "F1b RSXFS: brak danych"
    declines = 0
    for i in range(len(vals) - 1, 0, -1):
        if vals[i] < vals[i - 1]:
            declines += 1
        else:
            break
    if declines >= 2:
        return 15, f"🔴 Sprzedaż: {declines} kolejne spadki"
    if declines == 1:
        return 5, f"⚠️ Sprzedaż: {declines} spadek"
    return 0, f"🟢 Sprzedaż: {vals[-1]:.1f}B — stabilna"


def _f1c_icsa() -> tuple[int, str]:
    """Initial jobless claims — level + trend."""
    vals = _fred("ICSA", limit=10)
    if len(vals) < 4:
        return 0, "F1c ICSA: brak danych"
    latest = vals[-1]
    increases = 0
    for i in range(len(vals) - 1, 0, -1):
        if vals[i] > vals[i - 1]:
            increases += 1
        else:
            break
    if latest > 350_000 or increases >= 8:
        return 15, f"🔴 Zasiłki: {int(latest):,} — rośnie {increases} tyg."
    if latest > 300_000 or increases >= 4:
        return 10, f"🟡 Zasiłki: {int(latest):,} — rośnie {increases} tyg."
    if latest > 260_000 or increases >= 2:
        return 5, f"⚠️ Zasiłki: {int(latest):,} — rośnie {increases} tyg."
    return 0, f"🟢 Zasiłki: {int(latest):,} — stabilne"


def _f2_yield_curve() -> tuple[int, str]:
    """Yield curve T10Y2Y — inversion depth + reinversion signal."""
    vals = _fred("T10Y2Y", limit=10)
    if len(vals) < 2:
        return 0, "F2 T10Y2Y: brak danych"

    spread = vals[-1]
    # Detect reinversion: was negative for last N readings, now positive
    was_negative = all(v < 0 for v in vals[-5:-1]) if len(vals) >= 5 else False
    just_turned_positive = spread >= 0 and was_negative

    if just_turned_positive:
        return 5, f"🔴 T10Y2Y: {spread:+.2f}% — REINWERSJA (najsilniejszy sygnał historyczny!)"
    if spread < -0.5:
        return 5, f"🔴 T10Y2Y: {spread:+.2f}% — głęboka inwersja"
    if spread < 0:
        return 3, f"🟡 T10Y2Y: {spread:+.2f}% — inwersja"
    if spread < 0.5:
        return 0, f"🟡 T10Y2Y: {spread:+.2f}% — spłaszczona"
    return 0, f"🟢 T10Y2Y: {spread:+.2f}% — normalna"


# ── Layer 2: Market Leading Indicators (max 30 pts) ───────────────────────────

def _r1_vix_structure() -> tuple[int, str]:
    """VIX term structure: VIX vs VIX3M."""
    vix_spot = _yf_latest("^VIX")
    vix3m    = _yf_latest("^VIX3M")
    if vix_spot is None:
        return 0, "R1 VIX: brak danych"

    if vix3m is None:
        # Fallback: use spot level only
        if vix_spot > 35:
            return 8, f"🔴 VIX {vix_spot:.1f} — panic territory"
        if vix_spot > 25:
            return 4, f"🟡 VIX {vix_spot:.1f} — podwyższony"
        return 0, f"🟢 VIX {vix_spot:.1f} — normalny"

    diff = vix3m - vix_spot  # positive = contango (healthy)
    if diff < -5:
        return 8, f"🔴 VIX struktura: głęboka backwardation {diff:+.1f} (panic)"
    if diff < -2:
        return 6, f"🔴 VIX struktura: backwardation {diff:+.1f}"
    if diff < 0:
        return 4, f"🟡 VIX struktura: lekka backwardation {diff:+.1f}"
    if diff < 0.5:
        return 2, f"🟡 VIX struktura: flat {diff:+.1f}"
    return 0, f"🟢 VIX struktura: contango {diff:+.1f} (zdrowy)"


def _r2_skew() -> tuple[int, str]:
    """SKEW Index — tail risk hedging."""
    snippet = _tavily_snippet("CBOE SKEW index current value today 2026", 300)
    m = re.search(r'\b(1[0-9]{2}(?:\.\d+)?)\b', snippet)
    skew_val = float(m.group()) if m else None

    if skew_val is None:
        return 0, "R2 SKEW: brak danych"
    if skew_val > 160:
        return 6, f"🔴 SKEW {skew_val:.0f} — ekstremalny (instytucje na max obronie)"
    if skew_val > 150:
        return 4, f"🔴 SKEW {skew_val:.0f} — wysoki (instytucje zabezpieczają)"
    if skew_val > 140:
        return 2, f"🟡 SKEW {skew_val:.0f} — lekko podwyższony"
    if skew_val >= 130:
        return 0, f"🟡 SKEW {skew_val:.0f} — górna granica normy"
    return 0, f"🟢 SKEW {skew_val:.0f} — spokojny"


def _r3_hyg_spreads() -> tuple[int, str]:
    """HYG/TLT ratio as credit spread proxy."""
    hyg = _yf_close("HYG", period="6mo")
    tlt = _yf_close("TLT", period="6mo")

    if not hyg or not tlt or len(hyg) < 20 or len(tlt) < 20:
        return 0, "R3 Credit spreads: brak danych"

    # Ratio: lower = spreads widening (risk-off)
    ratio_now   = hyg[-1] / tlt[-1]
    ratio_1m    = (sum(hyg[-20:]) / 20) / (sum(tlt[-20:]) / 20)
    ratio_3m    = (sum(hyg) / len(hyg)) / (sum(tlt) / len(tlt))

    pct_vs_1m = (ratio_now / ratio_1m - 1) * 100
    pct_vs_3m = (ratio_now / ratio_3m - 1) * 100

    if pct_vs_1m < -5 and pct_vs_3m < -8:
        return 8, f"🔴 Credit spreads: gwałtowne rozszerzenie ({pct_vs_1m:.1f}% vs 1M)"
    if pct_vs_1m < -3:
        return 5, f"🟡 Credit spreads: rozszerzają się ({pct_vs_1m:.1f}% vs 1M)"
    if pct_vs_1m < -1.5:
        return 2, f"⚠️ Credit spreads: lekkie rozszerzenie ({pct_vs_1m:.1f}% vs 1M)"
    return 0, f"🟢 Credit spreads: stabilne ({pct_vs_1m:+.1f}% vs 1M)"


def _r4_smart_money() -> tuple[int, str]:
    """Smart money flow: QQQ vs Defensive ETFs (XLU/XLP/XLV)."""
    qqq = _yf_close("QQQ", period="3mo")
    xlu = _yf_close("XLU", period="3mo")
    xlp = _yf_close("XLP", period="3mo")

    if not qqq or not xlu or not xlp or len(qqq) < 20:
        return 0, "R4 Smart money: brak danych"

    def pct_change(closes):
        if len(closes) < 20:
            return 0.0
        return (closes[-1] / closes[-20] - 1) * 100

    qqq_1m   = pct_change(qqq)
    def_avg  = (pct_change(xlu) + pct_change(xlp)) / 2
    rotation = qqq_1m - def_avg  # negative = rotation to defensive

    if rotation < -10:
        return 4, f"🔴 Rotacja defensywna: QQQ {qqq_1m:+.1f}% vs DEF {def_avg:+.1f}%"
    if rotation < -5:
        return 2, f"🟡 Lekka rotacja defensywna: QQQ {qqq_1m:+.1f}% vs DEF {def_avg:+.1f}%"
    if rotation > 5:
        return 0, f"🟢 Risk-on: QQQ dominuje ({rotation:+.1f}% vs DEF)"
    return 1, f"⚠️ Mieszana rotacja: QQQ {qqq_1m:+.1f}% vs DEF {def_avg:+.1f}%"


def _r5_sp500_ma200() -> tuple[int, str]:
    """S&P 500 vs 200-day MA."""
    spy = _yf_close("SPY", period="1y")
    if not spy or len(spy) < 200:
        return 0, "R5 SPY/MA200: brak danych"

    price = spy[-1]
    ma200 = sum(spy[-200:]) / 200
    pct   = (price / ma200 - 1) * 100

    if pct < -15:
        return 4, f"🔴 SPY {pct:.1f}% poniżej MA200 — bessa"
    if pct < -5:
        return 3, f"🟡 SPY {pct:.1f}% poniżej MA200"
    if pct < 0:
        return 1, f"⚠️ SPY {pct:.1f}% poniżej MA200"
    if pct < 5:
        return 0, f"🟢 SPY {pct:+.1f}% nad MA200 (niski bufor)"
    return 0, f"🟢 SPY {pct:+.1f}% nad MA200 (zdrowy)"


# ── Layer 3: Sentiment (max 20 pts) ──────────────────────────────────────────

def _s1_naaim() -> tuple[int, str]:
    """NAAIM Exposure Index — active manager positioning."""
    snippet = _tavily_snippet("NAAIM exposure index current reading 2026", 300)
    m = re.search(r'\b(\d{1,3}(?:\.\d+)?)\b', snippet)
    naaim = float(m.group()) if m and float(m.group()) <= 200 else None

    if naaim is None:
        return 0, "S1 NAAIM: brak danych"
    if naaim > 95:
        return 5, f"🔴 NAAIM {naaim:.0f} — euphoria (max exposure)"
    if naaim > 80:
        return 3, f"🟡 NAAIM {naaim:.0f} — agresywnie długi"
    if naaim < 30:
        return 0, f"🟢 NAAIM {naaim:.0f} — pesymizm (contrarian bullish)"
    return 2, f"⚠️ NAAIM {naaim:.0f} — umiarkowanie długi"


def _s2_bofa_cash() -> tuple[int, str]:
    """BofA Fund Manager Survey — cash levels as contrarian indicator."""
    snippet = _tavily_snippet("BofA Bank of America fund manager survey cash levels 2026", 400)
    m = re.search(r'(\d(?:\.\d+)?)\s*%\s*(?:cash|gotówka)', snippet, re.IGNORECASE)
    cash_pct = float(m.group(1)) if m else None

    if cash_pct is None:
        return 0, "S2 BofA Cash: brak danych"
    if cash_pct < 3.5:
        return 5, f"🔴 BofA Cash {cash_pct:.1f}% — za nisko (historyczny sell signal)"
    if cash_pct < 4.0:
        return 2, f"🟡 BofA Cash {cash_pct:.1f}% — niski"
    if cash_pct > 5.0:
        return 0, f"🟢 BofA Cash {cash_pct:.1f}% — wysoki (contrarian bullish)"
    return 0, f"🟡 BofA Cash {cash_pct:.1f}% — normalny"


def _s3_sp500_euphoria() -> tuple[int, str]:
    """S&P 500 RSI + distance from ATH."""
    spy = _yf_close("SPY", period="3mo")
    if not spy or len(spy) < 14:
        return 0, "S3 Euphoria: brak danych"

    # RSI
    gains, losses = [], []
    for i in range(1, len(spy)):
        d = spy[i] - spy[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_gain = sum(gains[-14:]) / 14
    avg_loss = sum(losses[-14:]) / 14
    rsi = 100 - (100 / (1 + avg_gain / avg_loss)) if avg_loss > 0 else 100

    # ATH check
    ath = max(spy)
    pct_from_ath = (spy[-1] / ath - 1) * 100

    if rsi > 78 and pct_from_ath > -3:
        return 5, f"🔴 Euphoria: RSI {rsi:.0f}, {pct_from_ath:.1f}% od ATH"
    if rsi > 72:
        return 2, f"🟡 Overbought: RSI {rsi:.0f}"
    if rsi < 35:
        return 0, f"🟢 Oversold: RSI {rsi:.0f} (contrarian bullish)"
    return 0, f"🟢 RSI {rsi:.0f} — neutralny"


def _s4_put_call() -> tuple[int, str]:
    """CBOE Total Put/Call ratio."""
    snippet = _tavily_snippet("CBOE put call ratio total equity options 2026", 300)
    m = re.search(r'\b(0\.\d+|\d\.\d+)\b', snippet)
    pc_ratio = float(m.group()) if m else None

    if pc_ratio is None:
        return 0, "S4 Put/Call: brak danych"
    if pc_ratio < 0.65:
        return 5, f"🔴 Put/Call {pc_ratio:.2f} — zbyt optymistyczny (top signal)"
    if pc_ratio < 0.80:
        return 2, f"🟡 Put/Call {pc_ratio:.2f} — lekko optymistyczny"
    if pc_ratio > 1.20:
        return 0, f"🟢 Put/Call {pc_ratio:.2f} — fear (contrarian bullish)"
    return 0, f"🟡 Put/Call {pc_ratio:.2f} — neutralny"


# ── Historical comparison via Claude ─────────────────────────────────────────

def _get_historical_comparison(total: int, details: list) -> str:
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=os.environ.get("CLAUDE_API_KEY", ""))
        summary_lines = "\n".join(f"- {d}" for d in details)
        prompt = (
            f"Jesteś analitykiem rynków. Oto aktualny profil ryzyka korekty S&P500:\n\n"
            f"Score: {total}/100\n{summary_lines}\n\n"
            f"Porównaj do historycznych analogii:\n"
            f"- Październik 2023 (korekta 11%, filary zielone, VIX backwardation chwilowa → okazja)\n"
            f"- Styczeń 2022 (początek bessy, filary zaczynały słabnąć, SKEW wysoki 6 tygodni wcześniej)\n"
            f"- Marzec 2020 (COVID crash, nagły, filary zielone → flash crash, nie bessa)\n\n"
            f"W 2 zdaniach po polsku: do którego historycznego momentu to najbardziej przypomina i co to oznacza dla inwestora."
        )
        r = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        return r.content[0].text.strip()
    except Exception as e:
        logger.warning("correction_probability: historical comparison failed: %s", e)
        return ""


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN COMPUTE FUNCTION
# ═══════════════════════════════════════════════════════════════════════════════

def compute_correction_probability() -> dict:
    """
    Returns a dict with all layer scores, total, status, and sub-indicator details.
    """
    # Layer 1 — Economic Fundamentals
    f1a_pts, f1a_txt = _f1a_indpro()
    f1b_pts, f1b_txt = _f1b_rsxfs()
    f1c_pts, f1c_txt = _f1c_icsa()
    f2_pts,  f2_txt  = _f2_yield_curve()
    layer1_raw = f1a_pts + f1b_pts + f1c_pts + f2_pts

    # Layer 2 — Market Leading Indicators
    r1_pts, r1_txt = _r1_vix_structure()
    r2_pts, r2_txt = _r2_skew()
    r3_pts, r3_txt = _r3_hyg_spreads()
    r4_pts, r4_txt = _r4_smart_money()
    r5_pts, r5_txt = _r5_sp500_ma200()
    layer2_raw = r1_pts + r2_pts + r3_pts + r4_pts + r5_pts

    # Layer 3 — Sentiment
    s1_pts, s1_txt = _s1_naaim()
    s2_pts, s2_txt = _s2_bofa_cash()
    s3_pts, s3_txt = _s3_sp500_euphoria()
    s4_pts, s4_txt = _s4_put_call()
    layer3_raw = s1_pts + s2_pts + s3_pts + s4_pts

    # Cap layers at their maximums
    layer1 = min(50, layer1_raw)
    layer2 = min(30, layer2_raw)
    layer3 = min(20, layer3_raw)
    total_raw = layer1 + layer2 + layer3

    # DNA cap: if all 3 economic pillars = 0 → max 35
    dna_cap_applied = (f1a_pts == 0 and f1b_pts == 0 and f1c_pts == 0)
    total = min(35, total_raw) if dna_cap_applied else total_raw

    # Status
    if total <= 20:
        status = "SPOKOJNIE"
        status_emoji = "🟢"
    elif total <= 40:
        status = "CZUJNOŚĆ"
        status_emoji = "🟡"
    elif total <= 60:
        status = "OSTROŻNOŚĆ"
        status_emoji = "🟠"
    elif total <= 80:
        status = "UWAGA"
        status_emoji = "🔴"
    else:
        status = "ALARM"
        status_emoji = "🚨"

    details = [f1a_txt, f1b_txt, f1c_txt, f2_txt,
               r1_txt, r2_txt, r3_txt, r4_txt, r5_txt,
               s1_txt, s2_txt, s3_txt, s4_txt]

    historical = _get_historical_comparison(total, details)

    # Persist to history
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    hist = _load_history()
    hist.append({"score": total, "status": status, "ts": ts})
    _save_history(hist)

    return {
        "total": total,
        "total_raw": total_raw,
        "dna_cap_applied": dna_cap_applied,
        "status": status,
        "status_emoji": status_emoji,
        "layer1": layer1,
        "layer2": layer2,
        "layer3": layer3,
        "indicators": {
            "f1a": (f1a_pts, f1a_txt),
            "f1b": (f1b_pts, f1b_txt),
            "f1c": (f1c_pts, f1c_txt),
            "f2":  (f2_pts,  f2_txt),
            "r1":  (r1_pts,  r1_txt),
            "r2":  (r2_pts,  r2_txt),
            "r3":  (r3_pts,  r3_txt),
            "r4":  (r4_pts,  r4_txt),
            "r5":  (r5_pts,  r5_txt),
            "s1":  (s1_pts,  s1_txt),
            "s2":  (s2_pts,  s2_txt),
            "s3":  (s3_pts,  s3_txt),
            "s4":  (s4_pts,  s4_txt),
        },
        "historical_comparison": historical,
        "ts": ts,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# FORMATTERS
# ═══════════════════════════════════════════════════════════════════════════════

def format_correction_dashboard(result: dict) -> str:
    """Full /korekta output with all layers and indicators."""
    ind = result["indicators"]
    today = datetime.date.today().strftime("%d.%m.%Y")
    cap_note = "\n_⚡ DNA CAP zastosowany: filary gospodarcze OK → max 35 pkt_" if result["dna_cap_applied"] else ""

    # Trend from history
    hist = _load_history()
    trend_line = ""
    if len(hist) >= 2:
        prev = hist[-2]["score"]
        delta = result["total"] - prev
        sign = "+" if delta >= 0 else ""
        arrow = "📈" if delta > 2 else ("📉" if delta < -2 else "➡️")
        trend_line = f"\n_Poprzedni odczyt: {prev} | Zmiana: {sign}{delta} pkt {arrow}_"

    lines = [
        f"📉 *Correction Probability Dashboard — {today}*",
        f"*Score: {result['total']}/100 — {result['status_emoji']} {result['status']}*{cap_note}{trend_line}",
        "",
        f"*🏗️ Layer 1: Fundamenty gospodarcze — {result['layer1']}/50 pkt*",
        f"  {ind['f1a'][1]}",
        f"  {ind['f1b'][1]}",
        f"  {ind['f1c'][1]}",
        f"  {ind['f2'][1]}",
        "",
        f"*📊 Layer 2: Wskaźniki wyprzedzające rynek — {result['layer2']}/30 pkt*",
        f"  {ind['r1'][1]}",
        f"  {ind['r2'][1]}",
        f"  {ind['r3'][1]}",
        f"  {ind['r4'][1]}",
        f"  {ind['r5'][1]}",
        "",
        f"*😱 Layer 3: Sentyment — {result['layer3']}/20 pkt*",
        f"  {ind['s1'][1]}",
        f"  {ind['s2'][1]}",
        f"  {ind['s3'][1]}",
        f"  {ind['s4'][1]}",
        "",
        "*Interpretacja:*",
        "  0-20 🟢 SPOKOJNIE — korekta możliwa ale mało prawdopodobna duża",
        "  21-40 🟡 CZUJNOŚĆ — normalny poziom ryzyka, obserwuj filary",
        "  41-60 🟠 OSTROŻNOŚĆ — ryzyko rośnie, rozważ hedge",
        "  61-80 🔴 UWAGA — wysokie ryzyko, redukuj ryzyko portfela",
        "  81-100 🚨 ALARM — ekstremalne ryzyko, DNA: 2 czerwone filary = bessa",
    ]

    if result.get("historical_comparison"):
        lines += ["", f"*📚 Historyczna analogia:*", f"_{result['historical_comparison']}_"]

    return "\n".join(lines)


def format_correction_brief() -> str:
    """One-liner for Morning Brief integration. Uses cached history if available."""
    hist = _load_history()
    if hist:
        last = hist[-1]
        score = last["score"]
        status = last["status"]
        ts = last.get("ts", "")
    else:
        return ""

    if score <= 20:
        emoji = "🟢"
    elif score <= 40:
        emoji = "🟡"
    elif score <= 60:
        emoji = "🟠"
    elif score <= 80:
        emoji = "🔴"
    else:
        emoji = "🚨"

    return f"{emoji} *Ryzyko korekty >20%:* {score}/100 — {status} _(użyj /korekta po szczegóły)_"


def format_history_table() -> str:
    """Tabela historyczna dla /korekta historia."""
    hist = _load_history()
    if not hist:
        return "📭 Brak historii — uruchom /korekta aby zacząć śledzić."

    lines = ["📉 *Historia Correction Probability*", ""]
    for entry in reversed(hist[-8:]):
        score = entry["score"]
        status = entry.get("status", "?")
        ts = entry.get("ts", "?")
        if score <= 20:
            e = "🟢"
        elif score <= 40:
            e = "🟡"
        elif score <= 60:
            e = "🟠"
        elif score <= 80:
            e = "🔴"
        else:
            e = "🚨"
        lines.append(f"  {ts} — {e} {score}/100 {status}")

    # Simple trend
    if len(hist) >= 2:
        first_score = hist[-min(8, len(hist))]["score"]
        last_score  = hist[-1]["score"]
        delta = last_score - first_score
        trend_str = f"📈 Rośnie (+{delta})" if delta > 3 else (f"📉 Spada ({delta})" if delta < -3 else "➡️ Stabilne")
        lines += ["", f"_Trend ostatnie {min(8, len(hist))} odczytów: {trend_str}_"]

    return "\n".join(lines)


def send_correction_dashboard():
    """Send full dashboard to #inwestowanie. Entry point for scheduled/manual use."""
    from jobs.stock_digest import STOCK_CHANNEL_ID
    try:
        result = compute_correction_probability()
        text   = format_correction_dashboard(result)
        for chunk in [text[i:i + 3900] for i in range(0, len(text), 3900)]:
            _ctx.app.client.chat_postMessage(channel=STOCK_CHANNEL_ID, text=chunk)
        logger.info("send_correction_dashboard: done, score=%s", result["total"])
    except Exception as e:
        logger.error("send_correction_dashboard failed: %s", e)
