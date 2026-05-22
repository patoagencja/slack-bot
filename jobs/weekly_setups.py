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

        # Hard filters: RSI 35-72, above MA50, quality >= 1
        if not (35 <= rsi <= 72):
            return None
        if not tech.get("above_ma50"):
            return None
        if pattern["quality"] < 1:
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
    "Kryteria: RSI 35-72, powyżej MA50, wyraźny pattern techniczny, R/R ≥ 2:1.\n"
    "Dla krypto: nie wchodzisz po pompie >20% w 7d.\n"
    "Jeśli kandydatów jest mało — wybierz najlepszych spośród dostępnych (nawet 1-2).\n"
    "ODPOWIADASZ TYLKO W JSON (tablica, puste [] jeśli naprawdę brak setupów):\n"
    '[{"ticker":"...","pattern":"...","entry":0.0,"target":0.0,"stop":0.0,'
    '"target_pct":0.0,"stop_pct":0.0,"rr":0.0,'
    '"window_days":5,"catalyst":"...","reason":"1 zdanie po polsku"}]'
)


def _pick_top_setups(candidates: list[dict], macro: dict, limit: int = 5) -> list[dict]:
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

    top_n = max(3, min(limit, 15))
    prompt = (
        f"Makro: {macro.get('sentiment','?')} — {macro.get('main_risk','')}\n\n"
        "Kandydaci na swing setupy tego tygodnia:\n"
        + "\n".join(lines)
        + f"\n\nWybierz TOP {top_n} najlepszych zagrań i zwróć JSON."
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


def _scan_candidates() -> tuple[list[dict], dict, float | None]:
    """Collect all passing candidates + macro + btc_dom. Shared by send_* functions."""
    macro   = fetch_macro_briefing()
    btc_dom = _fetch_btc_dominance()
    candidates = []
    for ticker in WATCHLIST:
        s = _analyze_setup_ticker(ticker)
        if s:
            candidates.append(s)
    coins = _fetch_top_coins(20)
    for coin in coins:
        s = _analyze_setup_coin(coin, btc_dom)
        if s:
            candidates.append(s)
    return candidates, macro, btc_dom


def send_weekly_setups(limit: int = 5):
    """Post weekly swing setups to #inwestowanie."""
    try:
        today  = datetime.datetime.now().strftime("%d.%m.%Y")
        end_dt = (datetime.datetime.now() + datetime.timedelta(days=5)).strftime("%d.%m.%Y")

        candidates, macro, btc_dom = _scan_candidates()

        s_emoji = {"RISK-ON": "🟢", "RISK-OFF": "🔴", "NEUTRALNY": "🟡"}.get(macro.get("sentiment",""), "🟡")
        dom_str = f"{btc_dom}%" if btc_dom is not None else "N/A"
        header = (
            f"🎯 *Weekly Setups — {today} → {end_dt}*\n"
            f"{s_emoji} Makro: {macro.get('sentiment','?')}  |  "
            f"₿ Dominance: {dom_str}\n"
            f"_{macro.get('main_risk','')}_"
        )
        _ctx.app.client.chat_postMessage(channel=STOCK_CHANNEL_ID, text=header)

        setups = _pick_top_setups(candidates, macro, limit=limit)

        if not setups:
            _ctx.app.client.chat_postMessage(
                channel=STOCK_CHANNEL_ID,
                text=(
                    f"📭 Brak wyraźnych setupów w tym tygodniu "
                    f"({len(candidates)} kandydatów przeszło filtry, żaden nie wygrał).\n"
                    f"Rynek może być zbyt zmienny lub wszystkie spółki poza MA50."
                ),
            )
            return

        attachments = [_format_setup_attachment(i + 1, s) for i, s in enumerate(setups)]
        _ctx.app.client.chat_postMessage(
            channel=STOCK_CHANNEL_ID,
            text=f"🎯 *TOP {len(setups)} zagrań na {today} → {end_dt}*",
            attachments=attachments,
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


def send_scan_setups():
    """Post ALL passing candidates sorted by score — no Claude filter."""
    try:
        today  = datetime.datetime.now().strftime("%d.%m.%Y")
        candidates, macro, btc_dom = _scan_candidates()

        s_emoji = {"RISK-ON": "🟢", "RISK-OFF": "🔴", "NEUTRALNY": "🟡"}.get(macro.get("sentiment",""), "🟡")
        dom_str = f"{btc_dom}%" if btc_dom is not None else "N/A"

        if not candidates:
            _ctx.app.client.chat_postMessage(
                channel=STOCK_CHANNEL_ID,
                text="📭 Scan nie znalazł żadnych kandydatów (RSI 35-72, powyżej MA50).",
            )
            return

        sorted_c = sorted(candidates, key=lambda x: x.get("score", 0), reverse=True)

        # Split into stocks and crypto
        stocks = [c for c in sorted_c if not c.get("is_crypto")]
        cryptos = [c for c in sorted_c if c.get("is_crypto")]

        def _score_emoji(score):
            if score >= 5: return "🟢"
            if score >= 3: return "🟡"
            return "🔴"

        def _stock_line(i, c):
            rr  = c.get("rr", {})
            pat = c.get("pattern", {})
            em  = _score_emoji(c.get("score", 0))
            return (
                f"{em} *{i}. {c['ticker']}*  RSI:{c['rsi']}  "
                f"_{pat.get('pattern','?')}_  "
                f"Wejście:${rr.get('entry','?')}  Cel:+{rr.get('target_pct','?')}%  "
                f"R/R:{rr.get('rr_ratio','?')}:1"
            )

        def _crypto_line(i, c):
            em = _score_emoji(c.get("score", 0))
            return (
                f"{em} *{i}. {c['ticker']}*  #{c['rank']}  "
                f"${c['price']}  7d:{c['chg7d']:+.1f}%  "
                f"odATH:{c['pct_ath']}%"
            )

        stock_lines  = [_stock_line(i + 1, c) for i, c in enumerate(stocks)]
        crypto_lines = [_crypto_line(i + 1, c) for i, c in enumerate(cryptos)]

        header = (
            f"🔍 *Swing Scan — {today}*\n"
            f"{s_emoji} Makro: {macro.get('sentiment','?')}  |  "
            f"₿ Dominance: {dom_str}  |  "
            f"{len(sorted_c)} kandydatów\n"
            f"🟢 score≥5  🟡 score 3-4  🔴 score<3  |  "
            f"_`/swing TICKER` po szczegóły_"
        )
        _ctx.app.client.chat_postMessage(channel=STOCK_CHANNEL_ID, text=header)

        if stock_lines:
            _ctx.app.client.chat_postMessage(
                channel=STOCK_CHANNEL_ID,
                text="*📈 Spółki:*\n" + "\n".join(stock_lines),
            )
        if crypto_lines:
            _ctx.app.client.chat_postMessage(
                channel=STOCK_CHANNEL_ID,
                text="*₿ Krypto:*\n" + "\n".join(crypto_lines),
            )

        _ctx.app.client.chat_postMessage(
            channel=STOCK_CHANNEL_ID,
            text="⚠️ _Scan surowy — brak oceny Claude. Nie inwestuj więcej niż możesz stracić._",
        )
        logger.info("send_scan_setups: done, %d candidates", len(sorted_c))
    except Exception as e:
        logger.error("send_scan_setups failed: %s", e)


_DEEP_SWING_SYSTEM = (
    "Jesteś doświadczonym swing traderem i analitykiem rynkowym. "
    "Piszesz po polsku, zwięźle ale konkretnie. "
    "Twoja analiza ma pomóc zdecydować: czy wchodzić w to zagranie TERAZ, CZEKAĆ, czy je OMIJAĆ. "
    "Format odpowiedzi (Markdown/Slack mrkdwn):\n"
    "1. *Kontekst makro* — jak makro wpływa na ten ticker\n"
    "2. *Setup techniczny* — dlaczego ten pattern jest lub nie jest przekonujący\n"
    "3. *Katalizatory* — co może ruszyć cenę w górę w ciągu 1-2 tygodni\n"
    "4. *Ryzyka* — co może zniszczyć setup\n"
    "5. *Werdykt* — WCHODZĘ / CZEKAM / OMIJAM + 1 zdanie uzasadnienia\n"
    "Maksymalnie 250 słów. Pisz jak do kolegi tradera, nie jak raport."
)


def analyze_single_swing(ticker: str) -> str:
    """
    Deep swing analysis for one ticker with macro context, technicals, news, and Claude verdict.
    Returns formatted Slack text.
    """
    ticker = ticker.upper().strip()

    # ── Crypto path ──────────────────────────────────────────────────────────
    coin_data = None
    coins = _fetch_top_coins(50)
    for c in coins:
        if (c.get("symbol") or "").upper() == ticker:
            coin_data = c
            break

    if coin_data:
        btc_dom = _fetch_btc_dominance()
        macro   = fetch_macro_briefing()
        chg7d   = coin_data.get("price_change_percentage_7d_in_currency") or 0
        price   = coin_data.get("current_price", 0)
        name    = coin_data.get("name", ticker)
        rank    = coin_data.get("market_cap_rank", "?")
        pct_ath = round((price / coin_data["ath"] - 1) * 100, 1) if coin_data.get("ath") else "?"

        if chg7d > 20:
            return f"🚫 *{ticker}* — anty-pump: wzrost +{chg7d:.0f}% w 7d. Poczekaj na cofkę."

        # Tavily news
        news = ""
        if _tavily:
            try:
                r = _tavily.search(f"{name} {ticker} crypto price catalyst news 2026", max_results=3)
                news = " | ".join((x.get("content") or "")[:150] for x in (r.get("results") or []))[:400]
            except Exception:
                pass

        dom_str = f"{btc_dom}%" if btc_dom is not None else "N/A"
        user_msg = (
            f"Ticker: {ticker} ({name})  Rank: #{rank}\n"
            f"Cena: ${price}  |  7d: {chg7d:+.1f}%  |  od ATH: {pct_ath}%\n"
            f"BTC Dominance: {dom_str}\n"
            f"Makro: {macro.get('sentiment','?')} — {macro.get('summary','')[:200]}\n"
            f"Główne ryzyko makro: {macro.get('main_risk','')}\n"
            f"Newsy/katalizatory: {news or 'brak'}\n"
        )

        try:
            resp = _ctx.claude.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=500,
                system=_DEEP_SWING_SYSTEM,
                messages=[{"role": "user", "content": user_msg}],
            )
            analysis = resp.content[0].text.strip()
        except Exception as e:
            analysis = f"_(błąd Claude: {e})_"

        header = (
            f"🎯 *{ticker}* ({name})  #{rank}  "
            f"${price}  7d:{chg7d:+.1f}%  odATH:{pct_ath}%"
        )
        return f"{header}\n\n{analysis}"

    # ── Stock path ────────────────────────────────────────────────────────────
    try:
        hist = yf.Ticker(ticker).history(period="6mo", interval="1d")
    except Exception:
        hist = None

    if hist is None or hist.empty:
        return f"❌ *{ticker}* — nie znaleziono danych. Sprawdź symbol."

    closes = hist["Close"].tolist()
    if len(closes) < 22:
        return f"❌ *{ticker}* — za mało danych historycznych."

    rsi     = round(_calc_rsi(closes), 1)
    tech    = _calc_technicals(closes)
    pattern = _detect_pattern(closes, ticker)
    rr      = _calc_rr(closes)
    price   = round(closes[-1], 2)
    chg1m   = round((closes[-1] / closes[-22] - 1) * 100, 1) if len(closes) >= 22 else "?"
    chg3m   = round((closes[-1] / closes[0] - 1) * 100, 1)

    macro = fetch_macro_briefing()

    # Tavily: news + catalyst
    news = ""
    if _tavily:
        try:
            r = _tavily.search(
                f"{ticker} stock earnings catalyst news week 2026", max_results=4
            )
            news = " | ".join((x.get("content") or "")[:180] for x in (r.get("results") or []))[:500]
        except Exception:
            pass

    above_ma50  = "✅" if tech.get("above_ma50") else "❌"
    above_ma200 = "✅" if tech.get("above_ma200") else "❌"
    golden      = "🟡 Golden cross" if tech.get("golden_cross") else ("💀 Death cross" if tech.get("death_cross") else "brak krzyża")

    user_msg = (
        f"Ticker: {ticker}\n"
        f"Cena: ${price}  |  1m: {chg1m:+}%  |  3m: {chg3m:+}%\n"
        f"RSI-14: {rsi}  |  MA50: {above_ma50}  |  MA200: {above_ma200}  |  {golden}\n"
        f"Pattern: {pattern.get('pattern','?')}  (quality={pattern.get('quality',0)})\n"
        f"Wejście: ${rr.get('entry','?')}  Cel: ${rr.get('target','?')} (+{rr.get('target_pct','?')}%)  "
        f"Stop: ${rr.get('stop','?')} ({rr.get('stop_pct','?')}%)  R/R: {rr.get('rr_ratio','?')}:1\n"
        f"Makro: {macro.get('sentiment','?')} — {macro.get('summary','')[:200]}\n"
        f"Główne ryzyko makro: {macro.get('main_risk','')}\n"
        f"Newsy/katalizatory: {news or 'brak'}\n"
    )

    try:
        resp = _ctx.claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            system=_DEEP_SWING_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        analysis = resp.content[0].text.strip()
    except Exception as e:
        analysis = f"_(błąd Claude: {e})_"

    header = (
        f"🎯 *{ticker}*  ${price}  "
        f"RSI:{rsi}  MA50:{above_ma50}  MA200:{above_ma200}\n"
        f"_{pattern.get('pattern','?')}_  |  "
        f"Wejście:${rr.get('entry','?')}  Cel:+{rr.get('target_pct','?')}%  "
        f"Stop:{rr.get('stop_pct','?')}%  R/R:{rr.get('rr_ratio','?')}:1"
    )
    return f"{header}\n\n{analysis}"
