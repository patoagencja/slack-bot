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


# ── Tavily: news + insider + revenue surprise in one search ───────────────────
def _fetch_news(ticker: str) -> list:
    """One Tavily call per ticker covering news, insider activity, earnings surprises."""
    if _tavily is None:
        return []
    try:
        results = _tavily.search(
            query=f"{ticker} stock news insider SEC Form 4 earnings beat miss 2026",
            max_results=3,
        )
        snippets = []
        for r in (results.get("results") or [])[:3]:
            snippets.append({
                "title": r.get("title", ""),
                "content": (r.get("content") or "")[:200],
            })
        return snippets
    except Exception as e:
        logger.warning("Tavily error for %s: %s", ticker, e)
        return []


# ── Claude analysis ───────────────────────────────────────────────────────────
_SYSTEM_PROMPT = (
    "Jesteś analitykiem inwestycyjnym. Analizujesz spółki pod kątem:\n"
    "1) Fundamentów (wycena vs sektor, wzrost, marże, EV/EBITDA)\n"
    "2) Timingu (RSI > 75 = przegrzana, MA50/MA200 status, golden/death cross, blisko ATH)\n"
    "3) Ryzyka makro (Fed policy, VIX, sezonowość, short interest, insider activity)\n"
    "4) Relative strength vs QQQ (spółka bije rynek o > 20% w 30 dni = prawdopodobnie przegrzana)\n"
    "Uwzględnij też czy spółka ma earnings w ciągu 14 dni — to podnosi ryzyko.\n"
    "Odpowiadasz TYLKO w JSON bez żadnego tekstu przed/po: "
    '{"fundamentals_score": 1-5, "timing_score": 1-5, "macro_risk": "low"/"medium"/"high", '
    '"reasoning": "max 2 zdania po polsku", "verdict": "KUP"/"CZEKAJ"/"OMIJAJ"}'
)

_FALLBACK_ANALYSIS = {
    "fundamentals_score": 3,
    "timing_score": 3,
    "macro_risk": "medium",
    "reasoning": "Brak analizy.",
    "verdict": "CZEKAJ",
}


def _claude_analyze(ticker: str, fin: dict, news: list) -> dict:
    tech = fin.get("technicals", {})
    news_text = ("\n\nNewsy/insider/earnings surprises:\n" +
                 "\n".join(f"- {n['title']}: {n['content']}" for n in news)
                 ) if news else "\n\n(brak newsów)"

    ma_status = []
    if tech.get("above_ma50") is not None:
        ma_status.append("powyżej MA50" if tech["above_ma50"] else "poniżej MA50")
    if tech.get("above_ma200") is not None:
        ma_status.append("powyżej MA200" if tech["above_ma200"] else "poniżej MA200")
    if tech.get("golden_cross"):
        ma_status.append("GOLDEN CROSS (bullish)")
    if tech.get("death_cross"):
        ma_status.append("DEATH CROSS (bearish)")

    user_msg = (
        f"Ticker: {ticker}\n"
        f"Cena: ${fin.get('price', 'N/A')} ({fin.get('change_pct', 'N/A'):+}%)\n"
        f"52w High: ${fin.get('high52w', 'N/A')} | % od ATH: {fin.get('pct_from_high', 'N/A')}%\n"
        f"Blisko ATH (<5%): {fin.get('near_ath', False)}\n"
        f"RSI-14: {fin.get('rsi', 'N/A')}\n"
        f"MA: {', '.join(ma_status) or 'N/A'} | Wsparcie 30d: ${tech.get('support_30d', 'N/A')}\n"
        f"Trailing PE: {fin.get('trailingPE', 'N/A')} | Fwd PE: {fin.get('forwardPE', 'N/A')} | EV/EBITDA: {fin.get('enterpriseToEbitda', 'N/A')}\n"
        f"Marża netto: {fin.get('profitMargins', 'N/A')} | Revenue growth YoY: {fin.get('revenueGrowth', 'N/A')}\n"
        f"Short interest: {fin.get('shortPercentOfFloat', 'N/A')}\n"
        f"RS vs QQQ (30d): {fin.get('rs_vs_qqq', 'N/A')}%\n"
        f"Earnings za {fin.get('earnings_days', 'N/A')} dni\n"
        f"Sezonowość: {fin.get('seasonality', 'brak')}"
        f"{news_text}"
    )

    try:
        response = _ctx.claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = response.content[0].text.strip()
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        return json.loads(m.group() if m else raw)
    except Exception as e:
        logger.warning("Claude analysis error for %s: %s", ticker, e)
        return dict(_FALLBACK_ANALYSIS)


# ── Public API ────────────────────────────────────────────────────────────────

def analyze_ticker(ticker: str, qqq_30d: float | None = None) -> dict:
    """Fetch data + news + Claude analysis for one ticker. Returns dict with all fields."""
    ticker_obj = yf.Ticker(ticker)
    info = ticker_obj.info

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
    }

    news = _fetch_news(ticker)
    analysis = _claude_analyze(ticker, fin, news)
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
        price = data.get("price", "N/A")
        cp = data.get("change_pct", 0)
        rsi = data.get("rsi")
        tpe = data.get("trailingPE")
        fpe = data.get("forwardPE")
        tech = data.get("technicals", {})
        analysis = data.get("analysis") or dict(_FALLBACK_ANALYSIS)
        verdict = analysis.get("verdict", "CZEKAJ")
        verdict_emoji = {"KUP": "🟢", "CZEKAJ": "🟡", "OMIJAJ": "🔴"}.get(verdict, "⚪")
        color = {"KUP": "#2eb886", "CZEKAJ": "#e6b833", "OMIJAJ": "#e01e5a"}.get(verdict, "#aaaaaa")

        sign = "+" if cp >= 0 else ""
        ma_str = _ticker_ma_str(tech)
        flags = _ticker_flags(data)

        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{ticker}* ${price} ({sign}{cp}%)  {verdict_emoji} *{verdict}*",
                },
                "fields": [
                    {"type": "mrkdwn", "text": f"*RSI*\n{rsi if rsi else 'N/A'}"},
                    {"type": "mrkdwn", "text": f"*PE / Fwd PE*\n{round(tpe,1) if tpe else 'N/A'} / {round(fpe,1) if fpe else 'N/A'}"},
                    {"type": "mrkdwn", "text": f"*Fundamenty*\n{analysis.get('fundamentals_score','?')}/5"},
                    {"type": "mrkdwn", "text": f"*Timing*\n{analysis.get('timing_score','?')}/5"},
                    {"type": "mrkdwn", "text": f"*Ryzyko makro*\n{analysis.get('macro_risk','?')}"},
                    {"type": "mrkdwn", "text": f"*Technikalia*\n{ma_str}"},
                ],
            }
        ]

        if flags:
            blocks.append({
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": "  ·  ".join(flags)}],
            })

        reasoning = analysis.get("reasoning", "")
        if reasoning:
            blocks.append({
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"_{reasoning[:300]}_"}],
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
    """Scheduled job: posts rich Block Kit cards to #inwestowanie. Mon-Fri 13:00 UTC."""
    if tickers is None:
        tickers = WATCHLIST
    try:
        today = datetime.datetime.now().strftime("%d.%m.%Y")
        qqq_30d = _fetch_qqq_30d()
        season = _seasonality_note()

        header = f"📊 *Stock Digest — {today}*"
        if qqq_30d is not None:
            header += f"  |  QQQ 30d: {'+' if qqq_30d >= 0 else ''}{qqq_30d}%"
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
                data = analyze_ticker(ticker, qqq_30d=qqq_30d)
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

    lines_data = []
    near_ath = []

    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            info = t.info
            price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("ask") or 0.0
            if not price:
                continue
            prev  = info.get("previousClose") or price
            chg   = round((price - prev) / prev * 100, 2) if prev else 0.0
            pe    = info.get("trailingPE")
            fpe   = info.get("forwardPE")
            rev_g = info.get("revenueGrowth")
            margin = info.get("profitMargins")
            high52 = info.get("fiftyTwoWeekHigh") or 0
            pct_ath = round((price - high52) / high52 * 100, 2) if high52 else None

            rsi = above50 = above200 = golden = death = None
            try:
                hist   = t.history(period="200d")
                closes = hist["Close"].tolist()
                if len(closes) >= 15:
                    rsi = round(_calc_rsi(closes), 1)
                tech   = _calc_technicals(closes)
                above50  = tech.get("above_ma50")
                above200 = tech.get("above_ma200")
                golden   = tech.get("golden_cross")
                death    = tech.get("death_cross")
            except Exception:
                pass

            parts = [f"{ticker}: ${price} ({chg:+.1f}%)"]
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
            lines_data.append(" | ".join(parts))
        except Exception as e:
            logger.warning("Summary fetch %s: %s", ticker, e)

    if not lines_data:
        return "⚠️ Brak danych — sprawdź połączenie z yfinance."

    qqq_info = f"QQQ 30d: {'+' if (qqq_30d or 0) >= 0 else ''}{qqq_30d}%" if qqq_30d is not None else ""
    season_info = f"Sezonowość: {season}" if season else ""

    prompt = (
        f"Dzisiaj: {today}. {qqq_info}. {season_info}\n\n"
        f"Dane dla {len(lines_data)} spółek z watchlisty:\n"
        + "\n".join(lines_data)
        + """

Napisz JEDEN zwięzły raport inwestycyjny w formacie Slack markdown. Pogrupuj wszystkie spółki w trzy sekcje:

🟢 *WARTE UWAGI — dobre wejście teraz:*
• *TICKER* $cena — 1 zdanie: dlaczego warto (RSI, wycena, momentum, technicals)

🟡 *CZEKAJ — dobra spółka, ale zły timing lub za drogo:*
• *TICKER* $cena — 1 zdanie: co konkretnie hamuje (RSI wysoki, blisko ATH, za drogie PE)

🔴 *OMIJAJ — słabe fundamenty lub ekstremalnie przewartościowane:*
• *TICKER* $cena — 1 zdanie: konkretny powód

Zasady grupowania:
- WARTE UWAGI: RSI < 65, nie w top 5% ATH, przyzwoite fundamenty LUB wyraźny techniczny sygnał wejścia
- CZEKAJ: dobre fundamenty ale RSI > 68 lub < 3% od ATH lub brak danych cenowych
- OMIJAJ: ujemna rentowność bez wzrostu, crypto-proxy bez fundamentów, PE > 200 bez uzasadnienia wzrostem

Na końcu ZAWSZE dodaj:
⚠️ *Blisko ATH (<5%):* [lista lub "brak"]
📊 *Watchlist:* X warte uwagi | Y czekaj | Z omijaj

Pisz po polsku. Uzasadnienia krótkie i konkretne — używaj liczb z danych."""
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
    if season:
        header += f"  |  📅 {season}"

    return f"{header}\n\n{body}"


def send_summary_digest(tickers: list = None):
    """Post one-message summary digest to #inwestowanie."""
    try:
        msg = run_summary_digest(tickers)
        chunks = [msg[i:i+3900] for i in range(0, len(msg), 3900)]
        for chunk in chunks:
            _ctx.app.client.chat_postMessage(channel=STOCK_CHANNEL_ID, text=chunk)
        logger.info("send_summary_digest: done")
    except Exception as e:
        logger.error("send_summary_digest failed: %s", e)
