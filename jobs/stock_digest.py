"""
jobs/stock_digest.py — Full stock analysis digest for Sebol bot.

Fetches price/fundamentals via yfinance, news via Tavily, analysis via Claude.
Scheduled Mon-Fri at 13:00 to post to SLACK_STOCK_CHANNEL.
"""

import os
import json
import re
import logging
from datetime import datetime

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

# ── RSI calculation ───────────────────────────────────────────────────────────
def _calc_rsi(closes, period=14):
    deltas = pd.Series(closes).diff().dropna()
    gains = deltas.clip(lower=0).rolling(period).mean()
    losses = (-deltas.clip(upper=0)).rolling(period).mean()
    rs = gains / losses.replace(0, float('nan'))
    return float(100 - 100 / (1 + rs.iloc[-1]))


# ── Tavily news fetch ─────────────────────────────────────────────────────────
def _fetch_news(ticker: str) -> list:
    """Fetch top 3 news snippets for a ticker via Tavily. Returns [] on error."""
    if _tavily is None:
        return []
    try:
        results = _tavily.search(
            query=f"{ticker} stock news analysis 2026",
            max_results=3,
        )
        snippets = []
        for r in (results.get("results") or [])[:3]:
            title = r.get("title", "")
            content = (r.get("content") or "")[:200]
            snippets.append({"title": title, "content": content})
        return snippets
    except Exception as e:
        logger.warning("Tavily error for %s: %s", ticker, e)
        return []


# ── Claude analysis ───────────────────────────────────────────────────────────
_SYSTEM_PROMPT = (
    "Jesteś analitykiem inwestycyjnym. Analizujesz spółki pod kątem:\n"
    "1) Fundamentów (wycena vs sektor, wzrost, marże)\n"
    "2) Timingu (nie wchodzisz w spółki na ATH przez wiele tygodni z rzędu, RSI > 75 = przegrzana)\n"
    "3) Ryzyka makro (Fed policy, VIX, ekspozycja na bańkę AI)\n"
    'Odpowiadasz TYLKO w JSON bez żadnego tekstu przed/po: '
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
    """Call Claude to analyze a ticker. Returns analysis dict."""
    news_text = ""
    if news:
        news_text = "\n\nNewsy:\n" + "\n".join(
            f"- {n['title']}: {n['content']}" for n in news
        )
    else:
        news_text = "\n\n(brak newsów)"

    user_msg = (
        f"Ticker: {ticker}\n"
        f"Cena: {fin.get('price', 'N/A')} USD\n"
        f"Zmiana dzienna: {fin.get('change_pct', 'N/A')}%\n"
        f"52w High: {fin.get('high52w', 'N/A')}\n"
        f"% od 52w High: {fin.get('pct_from_high', 'N/A')}%\n"
        f"RSI (14): {fin.get('rsi', 'N/A')}\n"
        f"Trailing PE: {fin.get('trailingPE', 'N/A')}\n"
        f"Forward PE: {fin.get('forwardPE', 'N/A')}\n"
        f"EV/EBITDA: {fin.get('enterpriseToEbitda', 'N/A')}\n"
        f"Marża zysku: {fin.get('profitMargins', 'N/A')}\n"
        f"Wzrost przychodów: {fin.get('revenueGrowth', 'N/A')}\n"
        f"Blisko ATH: {fin.get('near_ath', False)}"
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
        # Extract JSON — handle cases where model wraps in markdown
        json_match = re.search(r'\{.*\}', raw, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        return json.loads(raw)
    except Exception as e:
        logger.warning("Claude analysis error for %s: %s", ticker, e)
        return dict(_FALLBACK_ANALYSIS)


# ── Public API ────────────────────────────────────────────────────────────────

def analyze_ticker(ticker: str) -> dict:
    """Fetch data + news + Claude analysis for one ticker. Returns dict with all fields."""
    ticker_obj = yf.Ticker(ticker)
    info = ticker_obj.info

    # Price data
    price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("ask") or 0.0
    prev_close = info.get("previousClose") or info.get("regularMarketPreviousClose") or price
    change_pct = round((price - prev_close) / prev_close * 100, 2) if prev_close else 0.0

    high52w = info.get("fiftyTwoWeekHigh") or 0.0
    pct_from_high = round((price - high52w) / high52w * 100, 2) if high52w else None
    near_ath = bool(price >= 0.95 * high52w) if high52w else False

    # RSI from 1y history (needs at least 15 closes)
    rsi = None
    try:
        hist = ticker_obj.history(period="1y")
        if len(hist) >= 15:
            rsi = round(_calc_rsi(hist["Close"].tolist()), 1)
    except Exception as e:
        logger.warning("RSI calc error for %s: %s", ticker, e)

    fin = {
        "price": round(price, 2),
        "change_pct": change_pct,
        "high52w": round(high52w, 2) if high52w else None,
        "pct_from_high": pct_from_high,
        "near_ath": near_ath,
        "rsi": rsi,
        "trailingPE": info.get("trailingPE"),
        "forwardPE": info.get("forwardPE"),
        "enterpriseToEbitda": info.get("enterpriseToEbitda"),
        "profitMargins": info.get("profitMargins"),
        "revenueGrowth": info.get("revenueGrowth"),
    }

    news = _fetch_news(ticker)
    analysis = _claude_analyze(ticker, fin, news)

    return {**fin, "news": news, "analysis": analysis}


def format_ticker_slack(ticker: str, data: dict) -> str:
    """Format one ticker's analysis as a Slack message block (plain text)."""
    try:
        price = data.get("price", "N/A")
        change_pct = data.get("change_pct", 0)
        rsi = data.get("rsi")
        trailing_pe = data.get("trailingPE")
        forward_pe = data.get("forwardPE")

        analysis = data.get("analysis") or dict(_FALLBACK_ANALYSIS)
        fund_score = analysis.get("fundamentals_score", "?")
        timing_score = analysis.get("timing_score", "?")
        macro_risk = analysis.get("macro_risk", "?")
        reasoning = analysis.get("reasoning", "Brak analizy.")
        verdict = analysis.get("verdict", "CZEKAJ")

        sign = "+" if change_pct >= 0 else ""
        rsi_str = f"{rsi}" if rsi is not None else "N/A"
        pe_str = f"{round(trailing_pe, 1)}" if trailing_pe is not None else "N/A"
        fwd_pe_str = f"{round(forward_pe, 1)}" if forward_pe is not None else "N/A"

        lines = [
            f"{ticker} ${price} ({sign}{change_pct}%) | RSI: {rsi_str} | PE: {pe_str} | Fwd PE: {fwd_pe_str}",
            f"Fundamenty: {fund_score}/5 | Timing: {timing_score}/5 | Ryzyko: {macro_risk}",
            f"_{reasoning}_",
            f"→ *{verdict}*",
        ]
        return "\n".join(lines)
    except Exception as e:
        logger.warning("format_ticker_slack error for %s: %s", ticker, e)
        return f"{ticker} — błąd formatowania: {e}"


def run_stock_digest(tickers: list = None) -> str:
    """Run full digest for tickers list (default: WATCHLIST). Returns full Slack message."""
    if tickers is None:
        tickers = WATCHLIST

    today = datetime.now().strftime("%d.%m.%Y")
    lines = [f"📊 *Stock Digest — {today}*", ""]

    near_ath_tickers = []

    for ticker in tickers:
        try:
            data = analyze_ticker(ticker)
            lines.append(format_ticker_slack(ticker, data))
            lines.append("---")
            if data.get("near_ath"):
                near_ath_tickers.append(ticker)
        except Exception as e:
            logger.warning("Skipping %s due to error: %s", ticker, e)

    if near_ath_tickers:
        lines.append(f"⚠️ *Blisko ATH (< 5%):* {', '.join(near_ath_tickers)}")

    return "\n".join(lines)


STOCK_CHANNEL_ID = os.environ.get("SLACK_STOCK_CHANNEL", "C0B5LA4Q064")


def send_stock_digest():
    """Scheduled job: runs digest and posts to #inwestowanie. Mon-Fri 13:00 UTC."""
    try:
        channel = STOCK_CHANNEL_ID
        if not channel:
            logger.warning("send_stock_digest: no channel configured (SLACK_STOCK_CHANNEL / GENERAL_CHANNEL_ID)")
            return

        msg = run_stock_digest()

        # Slack message limit is 40000 chars; split if needed
        chunk_size = 3900
        chunks = [msg[i:i + chunk_size] for i in range(0, len(msg), chunk_size)]
        for chunk in chunks:
            _ctx.app.client.chat_postMessage(channel=channel, text=chunk)

        logger.info("send_stock_digest: posted %d chunk(s) to %s", len(chunks), channel)
    except Exception as e:
        logger.error("send_stock_digest failed: %s", e)
