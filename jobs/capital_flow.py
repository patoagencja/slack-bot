"""
jobs/capital_flow.py — Sektor rotation tracker & capital flow snapshot.

Provides:
  build_capital_flow_snapshot() -> dict   (cached per day)
  send_capital_flow_snapshot()            (posts to #inwestowanie)
  get_ticker_flow(ticker) -> str          (INFLOW / OUTFLOW / NEUTRAL)
  format_capital_flow_block() -> str      (Slack mrkdwn section for digest header)
"""

import datetime
import logging

import yfinance as yf
import _ctx
from config.constants import CHANNEL_CLIENT_MAP

logger = logging.getLogger(__name__)

# ── Channel ───────────────────────────────────────────────────────────────────
STOCK_CHANNEL_ID = "C0B5LA4Q064"  # #inwestowanie

# ── Sector ETFs ───────────────────────────────────────────────────────────────
SECTOR_ETFS: dict[str, str] = {
    "XLK":  "Tech",
    "XLV":  "Healthcare",
    "XLE":  "Energia",
    "XLF":  "Finanse",
    "XLI":  "Industrials",
    "XLC":  "Komunikacja",
    "XLY":  "Consumer Discret.",
    "XLP":  "Consumer Staples",
    "XLB":  "Materiały",
    "XLRE": "Real Estate",
    "XLU":  "Utilities",
    "ITA":  "Defense/Aero",
    "ARKK": "Innowacje/Growth",
    "GLD":  "Złoto",
    "USO":  "Ropa",
}

# ── Ticker → primary ETF mapping ─────────────────────────────────────────────
_TICKER_ETF_MAP: dict[str, str] = {
    # Tech (XLK)
    "NVDA": "XLK", "MSFT": "XLK", "AMD": "XLK", "AVGO": "XLK",
    "ALAB": "XLK", "MU": "XLK", "ASML": "XLK", "SNPS": "XLK",
    "GFS": "XLK", "SYNA": "XLK", "APH": "XLK", "LITE": "XLK",
    "IBM": "XLK", "CRM": "XLK", "NOW": "XLK", "ORCL": "XLK",
    "ADBE": "XLK", "SNOW": "XLK", "PATH": "XLK", "RBRK": "XLK",
    "S": "XLK", "ANET": "XLK", "CRWD": "XLK", "FTNT": "XLK",
    "AXON": "XLK", "TEM": "XLK", "APP": "XLK", "SPOT": "XLK",
    "TTD": "XLK",
    # Communication (XLC)
    "META": "XLC", "SNAP": "XLC",
    # Healthcare (XLV)
    "UNH": "XLV", "NVO": "XLV", "ISRG": "XLV", "TDOC": "XLV",
    # Financials (XLF)
    "MCO": "XLF", "NU": "XLF", "DLO": "XLF", "PGY": "XLF", "HOOD": "XLF",
    # Consumer Discretionary (XLY)
    "NKE": "XLY", "LULU": "XLY", "CMG": "XLY", "DECK": "XLY",
    "RACE": "XLY", "UBER": "XLY", "AMZN": "XLY", "MELI": "XLY",
    "SE": "XLY", "GRAB": "XLY", "BABA": "XLY",
    # Defense / Aerospace (ITA)
    "NOC": "ITA", "TDG": "ITA", "BA": "ITA", "RYCEY": "ITA",
    "RKLB": "ITA", "ASTS": "ITA", "LUNR": "ITA", "PL": "ITA",
    "RDW": "ITA", "IRDM": "ITA",
    # Materials / Uranium (XLB)
    "CCJ": "XLB", "UEC": "XLB", "DNN": "XLB", "UUUU": "XLB",
    # Energy (XLE)
    "EOSE": "XLE",
    # Innovation / Crypto proxy (ARKK)
    "MARA": "ARKK", "MSTR": "ARKK",
}

# ── Tavily ────────────────────────────────────────────────────────────────────
try:
    from tavily import TavilyClient as _TavilyClient
    import os as _os
    _TAVILY_KEY = _os.environ.get("TAVILY_API_KEY", "")
    _tavily = _TavilyClient(_TAVILY_KEY) if _TAVILY_KEY else None
except Exception:
    _tavily = None

# ── Cache (reset daily) ───────────────────────────────────────────────────────
_snapshot_cache: dict = {}
_snapshot_date: str = ""


# ── ETF performance ───────────────────────────────────────────────────────────

def fetch_sector_etf_performance() -> dict[str, float]:
    """Returns {etf: 5d_pct_change} for all sector ETFs."""
    result = {}
    tickers = list(SECTOR_ETFS.keys())
    try:
        data = yf.download(tickers, period="10d", interval="1d", progress=False, auto_adjust=True)
        closes = data["Close"] if "Close" in data else data
        for etf in tickers:
            try:
                series = closes[etf].dropna()
                if len(series) >= 5:
                    pct = round((series.iloc[-1] / series.iloc[-6] - 1) * 100, 2)
                    result[etf] = pct
            except Exception:
                pass
    except Exception as e:
        logger.warning("ETF batch download error: %s", e)
        # Fallback: one by one
        for etf in tickers:
            try:
                h = yf.Ticker(etf).history(period="10d")
                closes_s = h["Close"].dropna().tolist()
                if len(closes_s) >= 5:
                    result[etf] = round((closes_s[-1] / closes_s[-6] - 1) * 100, 2)
            except Exception:
                pass
    return result


# ── Tavily news ───────────────────────────────────────────────────────────────

def _search(query: str) -> str:
    if not _tavily:
        return ""
    try:
        r = _tavily.search(query, max_results=2)
        return " ".join((x.get("content") or "")[:200] for x in (r.get("results") or []))[:350]
    except Exception:
        return ""


def fetch_capital_flow_news() -> dict[str, str]:
    """7 Tavily searches → dict of {topic: text}."""
    return {
        "sector_rotation": _search("sector rotation capital flow ETF inflows 2026 this week"),
        "sector_outperform": _search("which sectors are outperforming SP500 this week 2026"),
        "institutional": _search("institutional money flow sectors this week 2026"),
        "etf_flows": _search("ETF fund flows technology healthcare energy defense 2026"),
        "crypto_rotation": _search("crypto sector rotation DeFi AI gaming RWA 2026"),
        "crypto_btc_alt": _search("bitcoin ethereum altcoin capital rotation this week 2026"),
        "global_flow": _search("emerging markets vs US equity flows bonds equities rotation 2026"),
    }


# ── Claude synthesis ──────────────────────────────────────────────────────────

_FLOW_SYSTEM = (
    "Jesteś analitykiem przepływów kapitału. Odpowiadasz WYŁĄCZNIE w JSON:\n"
    '{"rotation_summary":"1 zdanie co się dzieje z kapitałem w akcjach",'
    '"top_sectors":["ETF1 +X%","ETF2 +X%","ETF3 +X%"],'
    '"bottom_sectors":["ETF1 -X%","ETF2 -X%","ETF3 -X%"],'
    '"sector_signals":{"XLK":"INFLOW|OUTFLOW|NEUTRAL",...},'
    '"crypto_winners":"konkretne coiny np. BTC, ETH, SOL",'
    '"crypto_losers":"konkretne coiny np. DOGE, SHIB, AVAX",'
    '"crypto_sentiment":"RISK-ON|RISK-OFF|NEUTRALNY",'
    '"global_summary":"1 zdanie co się dzieje globalnie",'
    '"global_what_it_means":"wyjaśnienie dla laika: co to znaczy i co robić — max 2 zdania, po polsku, konkretnie (np. kapitał wraca do USA = trzymaj US equities, unikaj złota i ropy)"}\n'
    "W crypto_winners i crypto_losers podaj KONKRETNE nazwy coinów (tickery), nie kategorie. "
    "global_what_it_means musi być praktyczne i zrozumiałe — napisz jakbyś tłumaczył osobie która nie zna finansów. "
    "sector_signals musi zawierać ocenę dla każdego ETF z listy. "
    "INFLOW = wygrywa vs SPY (top tercyl), OUTFLOW = przegrywa (bottom tercyl), NEUTRAL = środek."
)


def _fetch_top_coins_simple(limit: int = 20) -> str:
    """Returns a simple string listing top coins with 7d performance."""
    try:
        import requests
        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": limit,
            "page": 1,
            "price_change_percentage": "7d",
        }
        r = requests.get(url, params=params, timeout=10)
        coins = r.json()
        lines = []
        for c in coins:
            sym  = (c.get("symbol") or "").upper()
            chg7 = c.get("price_change_percentage_7d_in_currency") or 0
            lines.append(f"{sym} {chg7:+.1f}%")
        return ", ".join(lines)
    except Exception:
        return ""


def _build_flow_snapshot(etf_perf: dict, news: dict) -> dict:
    sorted_etfs = sorted(etf_perf.items(), key=lambda x: x[1], reverse=True)
    etf_lines = "\n".join(
        f"{etf} ({SECTOR_ETFS[etf]}): {pct:+.2f}%"
        for etf, pct in sorted_etfs
    )

    news_text = "\n".join(f"{k}: {v[:200]}" for k, v in news.items() if v)

    top_coins = _fetch_top_coins_simple(20)
    coins_section = f"\n\nTop 20 krypto (7d):\n{top_coins}" if top_coins else ""

    prompt = (
        f"ETF 5-dniowe wyniki:\n{etf_lines}\n\n"
        f"Newsy o przepływach:\n{news_text}"
        f"{coins_section}\n\n"
        "Wykonaj analizę sektor rotation i zwróć JSON."
    )

    try:
        resp = _ctx.claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=600,
            system=_FLOW_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        import re, json
        raw = resp.content[0].text.strip()
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        result = json.loads(m.group() if m else "{}")
    except Exception as e:
        logger.warning("Capital flow Claude error: %s", e)
        result = {}

    result["etf_perf"] = etf_perf
    return result


# ── Cache & public build ──────────────────────────────────────────────────────

def build_capital_flow_snapshot(force: bool = False) -> dict:
    """Build (or return cached) capital flow snapshot. Cached per calendar day."""
    global _snapshot_cache, _snapshot_date
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    if not force and _snapshot_date == today and _snapshot_cache:
        return _snapshot_cache

    etf_perf = fetch_sector_etf_performance()
    news     = fetch_capital_flow_news()
    snapshot = _build_flow_snapshot(etf_perf, news)
    snapshot["date"] = today

    _snapshot_cache = snapshot
    _snapshot_date  = today
    return snapshot


# ── Ticker flow lookup ────────────────────────────────────────────────────────

def get_ticker_flow(ticker: str) -> str:
    """Returns 'INFLOW', 'OUTFLOW', or 'NEUTRAL' for a ticker based on its sector ETF."""
    etf = _TICKER_ETF_MAP.get(ticker)
    if not etf or not _snapshot_cache:
        return "NEUTRAL"
    signals = _snapshot_cache.get("sector_signals", {})
    return signals.get(etf, "NEUTRAL")


# ── ETF → representative market tickers ──────────────────────────────────────

_ETF_EXAMPLES: dict[str, list[str]] = {
    "XLK":  ["AAPL", "NVDA", "MSFT"],
    "XLV":  ["JNJ", "UNH", "LLY"],
    "XLE":  ["XOM", "CVX", "SLB"],
    "XLF":  ["JPM", "BAC", "GS"],
    "XLI":  ["CAT", "HON", "UPS"],
    "XLC":  ["META", "GOOG", "NFLX"],
    "XLY":  ["AMZN", "TSLA", "HD"],
    "XLP":  ["PG", "KO", "WMT"],
    "XLB":  ["LIN", "APD", "FCX"],
    "XLRE": ["AMT", "PLD", "SPG"],
    "XLU":  ["NEE", "DUK", "SO"],
    "ITA":  ["RTX", "LMT", "NOC"],
    "ARKK": ["TSLA", "COIN", "ROKU"],
    "GLD":  ["GLD", "GDX", "NEM"],
    "USO":  ["XOM", "CVX", "OXY"],
}


def _etf_label(etf: str, pct: float) -> str:
    examples = _ETF_EXAMPLES.get(etf, [])
    ex_str = f" _{', '.join(examples)}_" if examples else ""
    return f"{etf} {SECTOR_ETFS.get(etf, etf)} {pct:+.1f}%{ex_str}"


# ── Format for digest header ──────────────────────────────────────────────────

def format_capital_flow_block(snapshot: dict | None = None) -> str:
    """Returns a compact Slack mrkdwn block for use at the top of digests."""
    if snapshot is None:
        snapshot = _snapshot_cache
    if not snapshot:
        return ""

    today    = snapshot.get("date", datetime.datetime.now().strftime("%d.%m.%Y"))
    etf_perf = snapshot.get("etf_perf", {})

    sorted_etfs = sorted(etf_perf.items(), key=lambda x: x[1], reverse=True)
    top3    = [_etf_label(e, p) for e, p in sorted_etfs[:3]]
    bottom3 = [_etf_label(e, p) for e, p in sorted_etfs[-3:]]

    lines = [
        f"💰 *Gdzie płynie kapitał — {today}*",
        "",
        "*Akcje (5d):*",
        f"🟢 Wygrywa: {' | '.join(top3)}",
        f"🔴 Przegrywa: {' | '.join(bottom3)}",
        f"→ Rotacja: {snapshot.get('rotation_summary', '—')}",
        "",
        "*Krypto:*",
        f"🟢 Wygrywa: {snapshot.get('crypto_winners', '—')}",
        f"🔴 Przegrywa: {snapshot.get('crypto_losers', '—')}",
        f"→ Sentyment: {snapshot.get('crypto_sentiment', '—')}",
        "",
        "*Globalnie:*",
        f"→ {snapshot.get('global_summary', '—')}",
        f"💡 *Co to znaczy:* {snapshot.get('global_what_it_means', '—')}",
    ]
    return "\n".join(lines)


# ── Slack post ────────────────────────────────────────────────────────────────

def send_capital_flow_snapshot(force: bool = False):
    """Fetch fresh data and post capital flow snapshot to #inwestowanie."""
    try:
        snapshot = build_capital_flow_snapshot(force=force)
        text = format_capital_flow_block(snapshot)
        if not text:
            _ctx.app.client.chat_postMessage(
                channel=STOCK_CHANNEL_ID,
                text="❌ Nie udało się pobrać danych o przepływach kapitału.",
            )
            return

        _ctx.app.client.chat_postMessage(channel=STOCK_CHANNEL_ID, text=text)
        logger.info("send_capital_flow_snapshot: done")
    except Exception as e:
        logger.error("send_capital_flow_snapshot failed: %s", e)
