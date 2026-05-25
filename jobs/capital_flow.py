"""
jobs/capital_flow.py — Sektor rotation tracker & capital flow snapshot.

Provides:
  build_capital_flow_snapshot() -> dict   (cached per day)
  send_capital_flow_snapshot()            (posts to #inwestowanie)
  get_ticker_flow(ticker) -> str          (INFLOW / OUTFLOW / NEUTRAL)
  format_capital_flow_block() -> str      (Slack mrkdwn section for digest header)
"""

import os
import json
import datetime
import logging

import yfinance as yf
import _ctx
from config.constants import CHANNEL_CLIENT_MAP

logger = logging.getLogger(__name__)

# ── Channel ───────────────────────────────────────────────────────────────────
STOCK_CHANNEL_ID = "C0B5LA4Q064"  # #inwestowanie

# ── History file ──────────────────────────────────────────────────────────────
_HISTORY_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "capital_flow_history.json")
_MAX_HISTORY_DAYS = 30

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


# ── Daily ETF data (1d return + dollar volume) ───────────────────────────────

def fetch_etf_daily_data() -> dict[str, dict]:
    """
    For each sector ETF fetch today's 1d return and estimated dollar volume.
    Returns {etf: {pct_1d, dollar_volume_m, vs_avg_30d}}
    """
    tickers = list(SECTOR_ETFS.keys())
    result: dict[str, dict] = {}
    try:
        data = yf.download(tickers, period="35d", interval="1d", progress=False, auto_adjust=True)
        closes  = data["Close"]  if "Close"  in data else data
        volumes = data["Volume"] if "Volume" in data else None

        for etf in tickers:
            try:
                c = closes[etf].dropna()
                if len(c) < 2:
                    continue
                pct_1d = round((c.iloc[-1] / c.iloc[-2] - 1) * 100, 2)

                dv_m = None
                avg_30d_m = None
                if volumes is not None:
                    v = volumes[etf].dropna()
                    if len(v) >= 2:
                        today_dv = float(v.iloc[-1]) * float(c.iloc[-1]) / 1e6
                        avg_dv   = float(v.iloc[-30:].mean()) * float(c.iloc[-1]) / 1e6 if len(v) >= 10 else None
                        dv_m     = round(today_dv, 0)
                        avg_30d_m = round(avg_dv, 0) if avg_dv else None

                result[etf] = {
                    "pct_1d":       pct_1d,
                    "dollar_volume_m": dv_m,
                    "avg_30d_m":    avg_30d_m,
                    "vs_avg":       round(dv_m / avg_30d_m, 2) if (dv_m and avg_30d_m and avg_30d_m > 0) else None,
                }
            except Exception:
                pass
    except Exception as e:
        logger.warning("ETF daily data error: %s", e)
    return result


# ── History store ─────────────────────────────────────────────────────────────

def _load_history() -> dict:
    try:
        os.makedirs(os.path.dirname(_HISTORY_FILE), exist_ok=True)
        if os.path.exists(_HISTORY_FILE):
            with open(_HISTORY_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_history(history: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_HISTORY_FILE), exist_ok=True)
        # Keep only last N days
        cutoff = (datetime.datetime.now() - datetime.timedelta(days=_MAX_HISTORY_DAYS)).strftime("%Y-%m-%d")
        pruned = {d: v for d, v in history.items() if d >= cutoff}
        with open(_HISTORY_FILE, "w") as f:
            json.dump(pruned, f, indent=2)
    except Exception as e:
        logger.warning("capital_flow history save error: %s", e)


def _append_daily_to_history(today: str, etf_perf: dict, daily_data: dict) -> dict:
    """Merge today's data into history file and return full history."""
    history = _load_history()
    history[today] = {
        etf: {
            "pct_5d":  etf_perf.get(etf),
            "pct_1d":  daily_data.get(etf, {}).get("pct_1d"),
            "dv_m":    daily_data.get(etf, {}).get("dollar_volume_m"),
            "vs_avg":  daily_data.get(etf, {}).get("vs_avg"),
        }
        for etf in SECTOR_ETFS
    }
    _save_history(history)
    return history


# ── Streak computation ────────────────────────────────────────────────────────

def compute_streaks(history: dict) -> dict[str, dict]:
    """
    For each ETF compute:
      streak_days   — consecutive days of same direction (positive = inflow, negative = outflow)
      streak_dir    — "INFLOW" | "OUTFLOW"
      avg_dv_m      — average daily dollar volume during streak (millions)
      momentum      — "rośnie" | "maleje" | "stabilny" (is the daily % getting bigger or smaller)
      total_move    — cumulative % change over streak
    """
    dates = sorted(history.keys(), reverse=True)  # newest first
    streaks: dict[str, dict] = {}

    for etf in SECTOR_ETFS:
        streak_days = 0
        streak_dir  = None
        dv_values   = []
        daily_pcts  = []

        for date in dates:
            day = history[date].get(etf, {})
            pct_1d = day.get("pct_1d")
            if pct_1d is None:
                break
            direction = "INFLOW" if pct_1d > 0 else "OUTFLOW"
            if streak_dir is None:
                streak_dir = direction
            if direction != streak_dir:
                break
            streak_days += 1
            daily_pcts.append(pct_1d)
            if day.get("dv_m"):
                dv_values.append(day["dv_m"])

        if not streak_dir or streak_days == 0:
            streaks[etf] = {"streak_days": 0, "streak_dir": "NEUTRAL"}
            continue

        avg_dv   = round(sum(dv_values) / len(dv_values)) if dv_values else None
        total_mv = round(sum(daily_pcts), 1)

        # Momentum: compare first half vs second half of streak pcts
        momentum = "stabilny"
        if len(daily_pcts) >= 4:
            half = len(daily_pcts) // 2
            # daily_pcts is newest-first, so recent = first half
            recent_avg = sum(abs(p) for p in daily_pcts[:half]) / half
            older_avg  = sum(abs(p) for p in daily_pcts[half:]) / half
            if recent_avg > older_avg * 1.2:
                momentum = "przyspiesza"
            elif recent_avg < older_avg * 0.8:
                momentum = "zwalnia"

        streaks[etf] = {
            "streak_days": streak_days,
            "streak_dir":  streak_dir,
            "avg_dv_m":    avg_dv,
            "momentum":    momentum,
            "total_move":  total_mv,
        }

    return streaks


def _streak_label(etf: str, streaks: dict) -> str:
    """Short label like '4d↑ $1.8B/d zwalnia' or '2d↓'"""
    s = streaks.get(etf, {})
    days = s.get("streak_days", 0)
    if days == 0:
        return ""
    arrow  = "↑" if s["streak_dir"] == "INFLOW" else "↓"
    dv     = f" ~${s['avg_dv_m']/1000:.1f}B/d" if s.get("avg_dv_m") and s["avg_dv_m"] >= 100 else ""
    mom    = f" {s['momentum']}" if s.get("momentum") and s["momentum"] != "stabilny" else ""
    return f"*{days}d{arrow}*{dv}{mom}"


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
    '"global_what_it_means":"wyjaśnienie dla laika: co to znaczy i co robić — max 2 zdania, po polsku, konkretnie",'
    '"rotate_from":"sektor/ETF z którego warto wychodzić, np. XLE Ropa",'
    '"rotate_to":"sektor/ETF do którego warto rotować, np. ITA Defense",'
    '"new_money":"gdzie wrzucić nowy kapitał teraz — konkretny sektor lub ETF + 1 zdanie dlaczego"}\n'
    "W crypto_winners i crypto_losers podaj KONKRETNE nazwy coinów (tickery), nie kategorie. "
    "rotate_from/rotate_to/new_money muszą być KONKRETNE i actionable — podaj ETF lub nazwę sektora. "
    "global_what_it_means musi być praktyczne — napisz jakbyś tłumaczył osobie która nie zna finansów. "
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

    etf_perf   = fetch_sector_etf_performance()
    daily_data = fetch_etf_daily_data()
    news       = fetch_capital_flow_news()
    snapshot   = _build_flow_snapshot(etf_perf, news)
    snapshot["date"] = today

    # Save to history and compute streaks
    history            = _append_daily_to_history(today, etf_perf, daily_data)
    snapshot["streaks"]     = compute_streaks(history)
    snapshot["daily_data"]  = daily_data

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


def _etf_label(etf: str, pct: float, streaks: dict) -> str:
    examples = _ETF_EXAMPLES.get(etf, [])
    ex_str   = f" _{', '.join(examples)}_" if examples else ""
    streak   = _streak_label(etf, streaks)
    st_str   = f"  {streak}" if streak else ""
    return f"{etf} {SECTOR_ETFS.get(etf, etf)} {pct:+.1f}%{ex_str}{st_str}"


# ── Format for digest header ──────────────────────────────────────────────────

def format_capital_flow_block(snapshot: dict | None = None) -> str:
    """Returns a compact Slack mrkdwn block for use at the top of digests."""
    if snapshot is None:
        snapshot = _snapshot_cache
    if not snapshot:
        return ""

    today    = snapshot.get("date", datetime.datetime.now().strftime("%d.%m.%Y"))
    etf_perf = snapshot.get("etf_perf", {})
    streaks  = snapshot.get("streaks", {})

    sorted_etfs = sorted(etf_perf.items(), key=lambda x: x[1], reverse=True)
    top3    = [_etf_label(e, p, streaks) for e, p in sorted_etfs[:3]]
    bottom3 = [_etf_label(e, p, streaks) for e, p in sorted_etfs[-3:]]

    # Build notable streaks section (>= 3 days)
    notable = []
    for etf, s in sorted(streaks.items(), key=lambda x: x[1].get("streak_days", 0), reverse=True):
        if s.get("streak_days", 0) >= 3:
            direction = "napływa" if s["streak_dir"] == "INFLOW" else "odpływa"
            dv = f", ~${s['avg_dv_m']/1000:.1f}B/dzień" if s.get("avg_dv_m") and s["avg_dv_m"] >= 100 else ""
            mom = f", {s['momentum']}" if s.get("momentum") and s["momentum"] != "stabilny" else ""
            notable.append(f"• {etf} {SECTOR_ETFS.get(etf, etf)}: {direction} od *{s['streak_days']} dni*{dv}{mom}")

    lines = [
        f"💰 *Gdzie płynie kapitał — {today}*",
        "",
        "*Akcje (5d):*",
        f"🟢 Wygrywa: {' | '.join(top3)}",
        f"🔴 Przegrywa: {' | '.join(bottom3)}",
        f"→ Rotacja: {snapshot.get('rotation_summary', '—')}",
    ]

    if notable:
        lines += ["", "*📊 Trwające przepływy (≥3 dni):*"] + notable

    rotate_from = snapshot.get("rotate_from", "")
    rotate_to   = snapshot.get("rotate_to", "")
    new_money   = snapshot.get("new_money", "")

    lines += [
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

    if rotate_from or rotate_to or new_money:
        lines += ["", "*🔄 Co zrobić z kasą:*"]
        if rotate_from and rotate_to:
            lines.append(f"↪️ Rotacja: wyjdź z *{rotate_from}* → wejdź w *{rotate_to}*")
        if new_money:
            lines.append(f"💵 Nowy kapitał: {new_money}")

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
