"""
jobs/weekly_setups.py — Swing trade setups for the coming week.

Scheduled: Friday 16:00 UTC.
Commands:  /swing, /swing {TICKER}
"""

import re
import json
import logging
import datetime

import yfinance as yf
import pandas as pd

import _ctx
from jobs.stock_digest import (
    WATCHLIST,
    _calc_rsi,
    _calc_technicals,
    _fetch_btc_dominance,
    _fetch_top_coins,
    fetch_macro_briefing,
    STOCK_CHANNEL_ID,
)

try:
    from tavily import TavilyClient as _TavilyClient
    import os as _os
    _TAVILY_KEY = _os.environ.get("TAVILY_API_KEY", "")
    _tavily = _TavilyClient(api_key=_TAVILY_KEY) if _TAVILY_KEY else None
except ImportError:
    _tavily = None

logger = logging.getLogger(__name__)


# ── Pattern detection ─────────────────────────────────────────────────────────

def _detect_pattern(closes: list, ticker: str = "") -> dict:
    """Identify technical setup from price history."""
    if len(closes) < 22:
        return {"pattern": "insufficient_data", "quality": 0}

    price = closes[-1]
    s     = pd.Series(closes)

    ma50  = float(s.rolling(50).mean().iloc[-1]) if len(closes) >= 50 else float(s.mean())
    ma200 = float(s.rolling(200).mean().iloc[-1]) if len(closes) >= 200 else None

    above_ma50  = price > ma50
    above_ma200 = (price > ma200) if ma200 else None

    # 3-week range (15 trading days)
    recent_high = max(closes[-15:])
    recent_low  = min(closes[-15:])
    range_pct   = (recent_high - recent_low) / recent_low * 100 if recent_low else 0

    # Prior move: last 30 trading days
    prior_move = (closes[-1] / closes[-30] - 1) * 100 if len(closes) >= 30 else 0

    # Breakout: today's price > 15-day high (excluding today)
    is_breakout = len(closes) >= 16 and price > max(closes[-16:-1])

    # Flag: strong prior move + tight consolidation
    is_flag = prior_move > 10 and range_pct < 6

    # MA50 bounce: within 3% of MA50, price above MA50
    pct_from_ma50 = (price - ma50) / ma50 * 100
    is_ma50_bounce = above_ma50 and 0 < pct_from_ma50 < 4

    # MA200 bounce
    pct_from_ma200 = (price - ma200) / ma200 * 100 if ma200 else None
    is_ma200_bounce = (ma200 and above_ma200 and pct_from_ma200 is not None
                       and 0 < pct_from_ma200 < 4)

    if is_breakout and above_ma50:
        pattern = "Breakout ponad opór 15-dniowy"
        quality = 4
    elif is_flag and above_ma50:
        pattern = f"Flag — ruch +{prior_move:.0f}% + konsolidacja {range_pct:.1f}%"
        quality = 3
    elif is_ma200_bounce:
        pattern = f"Odbicie od MA200 (${ma200:.0f})"
        quality = 3
    elif is_ma50_bounce:
        pattern = f"Odbicie od MA50 (${ma50:.0f})"
        quality = 2
    else:
        pattern = "Brak wyraźnego setupu"
        quality = 0

    return {
        "pattern":        pattern,
        "quality":        quality,
        "above_ma50":     above_ma50,
        "above_ma200":    above_ma200,
        "pct_from_ma50":  round(pct_from_ma50, 1),
        "prior_move_30d": round(prior_move, 1),
        "range_pct":      round(range_pct, 1),
    }


def _calc_rr(closes: list) -> dict:
    """Entry / target / stop / R:R from price history."""
    if len(closes) < 20:
        return {}
    price      = closes[-1]
    support    = min(closes[-20:])
    resistance = max(closes[-23:-3]) if len(closes) >= 23 else max(closes[:-1])
    stop_price = round(price * 0.955, 2)          # -4.5%
    target     = round(max(resistance, price * 1.09), 2)  # resistance or +9%
    tgt_pct    = round((target - price) / price * 100, 1)
    stp_pct    = round((stop_price - price) / price * 100, 1)
    rr         = round(tgt_pct / abs(stp_pct), 1) if stp_pct else 0
    return {
        "entry":      round(price, 2),
        "target":     target,
        "target_pct": tgt_pct,
        "stop":       stop_price,
        "stop_pct":   stp_pct,
        "rr_ratio":   rr,
    }


# ── Per-ticker setup analysis ─────────────────────────────────────────────────

def _analyze_setup_ticker(ticker: str) -> dict | None:
    """
    Fetch yfinance data, detect pattern, check RSI/MA criteria.
    Returns None if no valid setup.
    """
    try:
        t       = yf.Ticker(ticker)
        info    = t.info
        price   = info.get("currentPrice") or info.get("regularMarketPrice") or 0
        if not price:
            return None
        hist    = t.history(period="1y")
        closes  = hist["Close"].tolist()
        if len(closes) < 22:
            return None

        rsi     = round(_calc_rsi(closes), 1)
        tech    = _calc_technicals(closes)
        pattern = _detect_pattern(closes, ticker)
        rr      = _calc_rr(closes)

        # Hard filters: RSI 40-68, above MA50, quality >= 2
        if not (40 <= rsi <= 68):
            return None
        if not tech.get("above_ma50"):
            return None
        if pattern["quality"] < 2:
            return None

        # Catalyst (Tavily)
        catalyst = ""
        if _tavily:
            try:
                r = _tavily.search(
                    f"{ticker} catalyst event launch partnership earnings week 2026",
                    max_results=2,
                )
                catalyst = " ".join((x.get("content") or "")[:120] for x in (r.get("results") or []))[:250]
            except Exception:
                pass

        return {
            "ticker":   ticker,
            "price":    price,
            "rsi":      rsi,
            "pattern":  pattern,
            "rr":       rr,
            "tech":     tech,
            "catalyst": catalyst,
            "score":    pattern["quality"] + (1 if rr.get("rr_ratio", 0) >= 2.0 else 0)
                        + (1 if catalyst else 0)
                        + (1 if tech.get("golden_cross") else 0),
        }
    except Exception as e:
        logger.warning("Setup analysis error %s: %s", ticker, e)
        return None


def _analyze_setup_coin(coin: dict, btc_dominance: float | None) -> dict | None:
    """Setup analysis for a CoinGecko coin. Anti-pump: >20% in 7d = skip."""
    chg7d = coin.get("price_change_percentage_7d_in_currency") or 0
    if chg7d > 20:
        return None  # anti-pump rule
    chg24   = coin.get("price_change_percentage_24h") or 0
    pct_ath = None
    ath     = coin.get("ath") or 0
    price   = coin.get("current_price") or 0
    if ath:
        pct_ath = round((price - ath) / ath * 100, 1)
    rank    = coin.get("market_cap_rank", 99)

    # Require: 7d change between -5% and +15%, not too close to ATH
    if not (-5 <= chg7d <= 15):
        return None
    if pct_ath is not None and pct_ath > -8:
        return None

    catalyst = ""
    if _tavily:
        try:
            sym = (coin.get("symbol") or "").upper()
            r   = _tavily.search(f"{sym} crypto catalyst upcoming week 2026", max_results=1)
            catalyst = " ".join((x.get("content") or "")[:120] for x in (r.get("results") or []))[:200]
        except Exception:
            pass

    score = 2
    if btc_dominance and btc_dominance < 50:
        score += 1  # alt season
    if catalyst:
        score += 1
    if rank <= 10:
        score += 1

    return {
        "ticker":   (coin.get("symbol") or "?").upper(),
        "name":     coin.get("name", ""),
        "price":    price,
        "chg7d":    chg7d,
        "chg24":    chg24,
        "pct_ath":  pct_ath,
        "rank":     rank,
        "catalyst": catalyst,
        "score":    score,
        "is_crypto": True,
    }


# ── Claude picks top setups ───────────────────────────────────────────────────

_SWING_SYSTEM = (
    "Jesteś traderem swing. Wybierasz TOP 3-5 zagrań tygodniowych.\n"
    "Kryteria: RSI 40-68, powyżej MA50, wyraźny pattern techniczny, R/R ≥ 2:1.\n"
    "Dla krypto: nie wchodzisz po pompie >20% w 7d.\n"
    "ODPOWIADASZ TYLKO W JSON (tablica):\n"
    '[{"ticker":"...","pattern":"...","entry":0,"target":0,"stop":0,"rr":0,'
    '"window_days":5,"catalyst":"...","reason":"1 zdanie po polsku"}]'
)


def _pick_top_setups(candidates: list[dict], macro: dict) -> list[dict]:
    if not candidates:
        return []

    # Sort by score, take top 15 for Claude
    top15 = sorted(candidates, key=lambda x: x.get("score", 0), reverse=True)[:15]

    lines = []
    for c in top15:
        if c.get("is_crypto"):
            lines.append(
                f"{c['ticker']} [CRYPTO rank#{c['rank']}]: ${c['price']} | "
                f"7d={c['chg7d']:+.1f}% | odATH={c['pct_ath']}% | "
                f"Catalyst: {c['catalyst'][:80] or 'brak'}"
            )
        else:
            rr  = c.get("rr", {})
            pat = c.get("pattern", {})
            lines.append(
                f"{c['ticker']} [STOCK]: ${c['price']} | RSI={c['rsi']} | "
                f"Pattern={pat.get('pattern','')} | "
                f"Entry={rr.get('entry')} Target={rr.get('target')} Stop={rr.get('stop')} "
                f"R/R={rr.get('rr_ratio')} | "
                f"Catalyst: {c['catalyst'][:80] or 'brak'}"
            )

    prompt = (
        f"Makro: {macro.get('sentiment','?')} — {macro.get('main_risk','')}\n\n"
        "Kandydaci na swing setupy tego tygodnia:\n"
        + "\n".join(lines)
        + "\n\nWybierz TOP 3-5 najlepszych zagrań i zwróć JSON."
    )

    try:
        resp = _ctx.claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=800,
            system=_SWING_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        m   = re.search(r"\[.*\]", raw, re.DOTALL)
        return json.loads(m.group() if m else "[]")
    except Exception as e:
        logger.warning("Pick top setups error: %s", e)
        return []


# ── Formatting ────────────────────────────────────────────────────────────────

def _format_setup_block(i: int, s: dict) -> str:
    lines = [
        f"*{i}. {s.get('ticker','')}*  —  Pattern: _{s.get('pattern','?')}_",
        f"   Wejście: ${s.get('entry','?')}  |  Cel: +{s.get('rr',{}).get('target_pct','?')}% → ${s.get('target','?')}  |  Stop: ${s.get('stop','?')} ({s.get('rr',{}).get('stop_pct','?')}%)",
        f"   R/R: {s.get('rr','?')}:1  |  Okno: {s.get('window_days',5)} dni",
    ]
    if s.get("catalyst"):
        lines.append(f"   Katalizator: {s['catalyst'][:120]}")
    lines.append(f"   _{s.get('reason','')}_")
    return "\n".join(lines)


def _format_setup_attachment(i: int, s: dict) -> dict:
    color  = "#2eb886"
    ticker = s.get("ticker", "?")
    rr_val = s.get("rr", "?")
    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f"*{i}. {ticker}* — _{s.get('pattern','?')}_"},
            "fields": [
                {"type": "mrkdwn", "text": f"*Wejście*\n${s.get('entry','?')}"},
                {"type": "mrkdwn", "text": f"*Cel (+{s.get('target_pct','?')}%)*\n${s.get('target','?')}"},
                {"type": "mrkdwn", "text": f"*Stop*\n${s.get('stop','?')} ({s.get('stop_pct','?')}%)"},
                {"type": "mrkdwn", "text": f"*R/R*\n{rr_val}:1"},
                {"type": "mrkdwn", "text": f"*Okno*\n{s.get('window_days',5)} dni"},
                {"type": "mrkdwn", "text": f"*Katalizator*\n{(s.get('catalyst') or 'brak')[:80]}"},
            ],
        }
    ]
    if s.get("reason"):
        blocks.append({"type": "context",
                        "elements": [{"type": "mrkdwn", "text": f"_{s['reason']}_"}]})
    return {"color": color, "blocks": blocks}


# ── Public entry points ───────────────────────────────────────────────────────

def run_weekly_setups(include_crypto: bool = True) -> list[dict]:
    """Scan WATCHLIST + crypto, detect setups, return Claude-picked list."""
    macro     = fetch_macro_briefing()
    btc_dom   = _fetch_btc_dominance()
    candidates = []

    for ticker in WATCHLIST:
        s = _analyze_setup_ticker(ticker)
        if s:
            candidates.append(s)

    if include_crypto:
        coins = _fetch_top_coins(20)
        for coin in coins:
            s = _analyze_setup_coin(coin, btc_dom)
            if s:
                candidates.append(s)

    return _pick_top_setups(candidates, macro)


def send_weekly_setups():
    """Post weekly swing setups to #inwestowanie (Friday 16:00 UTC)."""
    try:
        today  = datetime.datetime.now().strftime("%d.%m.%Y")
        end_dt = (datetime.datetime.now() + datetime.timedelta(days=5)).strftime("%d.%m.%Y")
        macro  = fetch_macro_briefing()
        btc_dom = _fetch_btc_dominance()
        s_emoji = {"RISK-ON": "🟢", "RISK-OFF": "🔴", "NEUTRALNY": "🟡"}.get(macro.get("sentiment",""), "🟡")

        header = (
            f"🎯 *Weekly Setups — {today} → {end_dt}*\n"
            f"{s_emoji} Makro: {macro.get('sentiment','?')}  |  "
            f"₿ Dominance: {btc_dom}%\n"
            f"_{macro.get('main_risk','')}_"
        )
        _ctx.app.client.chat_postMessage(channel=STOCK_CHANNEL_ID, text=header)

        # Scan in background (already called from background thread)
        macro_cached = macro  # already fetched
        btc_dom_cached = btc_dom
        candidates = []
        for ticker in WATCHLIST:
            s = _analyze_setup_ticker(ticker)
            if s:
                candidates.append(s)
        coins = _fetch_top_coins(20)
        for coin in coins:
            s = _analyze_setup_coin(coin, btc_dom_cached)
            if s:
                candidates.append(s)

        setups = _pick_top_setups(candidates, macro_cached)

        if not setups:
            _ctx.app.client.chat_postMessage(
                channel=STOCK_CHANNEL_ID,
                text="📭 Brak wyraźnych setupów w tym tygodniu — rynek sideways lub za drogo.",
            )
            return

        attachments = [_format_setup_attachment(i + 1, s) for i, s in enumerate(setups)]
        _ctx.app.client.chat_postMessage(
            channel=STOCK_CHANNEL_ID, text=" ", attachments=attachments
        )
        _ctx.app.client.chat_postMessage(
            channel=STOCK_CHANNEL_ID,
            text=(
                "⚠️ _To są setupy techniczne oparte na danych historycznych. "
                "Zawsze ustaw stop-loss przed wejściem. "
                "Nie inwestuj więcej niż możesz stracić._"
            ),
        )
        logger.info("send_weekly_setups: done, %d setups", len(setups))
    except Exception as e:
        logger.error("send_weekly_setups failed: %s", e)


def analyze_single_swing(ticker: str) -> str:
    """
    Quick swing analysis for one ticker (stocks or crypto symbol).
    Returns formatted Slack text.
    """
    ticker = ticker.upper().strip()

    # Try crypto first if looks like a coin symbol
    coin_data = None
    coins = _fetch_top_coins(50)
    for c in coins:
        if (c.get("symbol") or "").upper() == ticker:
            coin_data = c
            break

    if coin_data:
        btc_dom = _fetch_btc_dominance()
        s = _analyze_setup_coin(coin_data, btc_dom)
        if s is None:
            chg7d = coin_data.get("price_change_percentage_7d_in_currency") or 0
            if chg7d > 20:
                return f"🚫 *{ticker}* — anty-pump: wzrost +{chg7d:.0f}% w 7d. Poczekaj na cofkę."
            return f"🟡 *{ticker}* — brak wyraźnego setupu swing w tym tygodniu."
        rr = s.get("rr") or {}
        return (
            f"🎯 *{ticker}* ({s['name']}) #{s['rank']}\n"
            f"Cena: ${s['price']} | 7d: {s['chg7d']:+.1f}% | od ATH: {s['pct_ath']}%\n"
            f"Stop: -4.5% | Cel: +9% | R/R: ~2:1\n"
            + (f"Katalizator: {s['catalyst'][:100]}" if s.get("catalyst") else "Katalizator: brak")
        )

    # Stock analysis
    setup = _analyze_setup_ticker(ticker)
    if not setup:
        return f"🟡 *{ticker}* — brak setupu: RSI poza zakresem 40-68, poniżej MA50, lub niewystarczające dane."

    pat = setup.get("pattern", {})
    rr  = setup.get("rr", {})
    lines = [
        f"🎯 *{ticker}* — Setup: _{pat.get('pattern','?')}_",
        f"RSI: {setup['rsi']} | MA50: {'✅' if pat.get('above_ma50') else '❌'} | MA200: {'✅' if pat.get('above_ma200') else '❌'}",
        f"Wejście: ${rr.get('entry','?')} | Cel: ${rr.get('target','?')} (+{rr.get('target_pct','?')}%) | Stop: ${rr.get('stop','?')} ({rr.get('stop_pct','?')}%)",
        f"R/R: {rr.get('rr_ratio','?')}:1 | Okno: 5 dni",
    ]
    if setup.get("catalyst"):
        lines.append(f"Katalizator: {setup['catalyst'][:120]}")
    return "\n".join(lines)
