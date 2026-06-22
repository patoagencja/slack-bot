"""
investing/formatting.py — czytelny render PositionPlan do Slacka.

Zasady (po skardze na nieczytelność poprzedniej wersji):
  • Pierwsza linia = decyzja (READY / WAIT / NO TRADE / DATA INCOMPLETE) + ticker.
  • Druga linia = jedno zdanie „co z tym zrobić" (po ludzku, bez kodów).
  • Dla wejścia (READY/WAIT) — zwarty blok PLAN WEJŚCIA z konkretnymi liczbami,
    potem krótki KONTEKST. Żadnych enumów typu PULLBACK_CONTINUATION / BULL /
    REDUCE_BEFORE_EVENT — tłumaczymy na polski.
  • Dla NO TRADE / DATA INCOMPLETE — krótko: dlaczego i co zrobić, bez planu
    i bez długiego eseju.

Publiczne API bez zmian: format_plan(plan) -> str.
"""

from __future__ import annotations

import re

from .schemas import PositionPlan


# ── Słowniki etykiet (klucz = surowa .value enuma) ──────────────────────────────
_STATUS = {
    "READY_TO_ENTER":   "🟢 READY TO ENTER",
    "WAIT_FOR_TRIGGER": "🟡 WAIT FOR TRIGGER",
    "NO_TRADE":         "🔴 NO TRADE",
    "DATA_INCOMPLETE":  "⚪ DATA INCOMPLETE",
}

_SETUP = {
    "BREAKOUT":               "wybicie z konsolidacji",
    "PULLBACK_CONTINUATION":  "cofnięcie w trendzie wzrostowym",
    "BASE_BUILDING":          "budowa bazy",
    "MEAN_REVERSION":         "powrót do średniej",
    "WYCKOFF_REVERSAL":       "odwrócenie (Wyckoff)",
    "EVENT_DRIVEN":           "zagranie pod wydarzenie",
    "NO_VALID_SETUP":         "brak ważnego setupu",
}

_REGIME = {
    "BULL":      "🟢 hossa",
    "CAUTION":   "🟡 ostrożnie",
    "DEFENSIVE": "🟠 defensywnie",
    "BEAR":      "🔴 bessa",
    "UNKNOWN":   "❔ nieznany",
}

_EVENT = {
    "HOLD_THROUGH_EVENT":  "trzymać przez wydarzenie",
    "REDUCE_BEFORE_EVENT": "zredukować przed wydarzeniem",
    "EXIT_BEFORE_EVENT":   "wyjść przed wydarzeniem",
    "EVENT_STRATEGY":      "zagranie pod wydarzenie",
    "NO_EVENT_RISK":       "brak ryzyka wydarzenia",
}

_ASSET = {
    "EQUITY":       "akcja",
    "ETF":          "ETF",
    "CRYPTO_PROXY": "proxy krypto",
    "ADR":          "ADR",
}

# Surowe nazwy pól danych → po ludzku (używane w decision_reason i missing_data).
_FIELD_PL = {
    "daily_bars":    "świece dzienne",
    "earnings_date": "data wyników",
    "fundamentals":  "fundamenty",
    "rs_line":       "siła względna",
    "bars":          "świece (dane cenowe)",
    "quote":         "kurs",
    "price":         "kurs",
    "atr":           "zmienność (ATR)",
    "rs":            "siła względna",
    "macro":         "dane makro",
    "sector":        "dane sektora",
    "volume":        "wolumen",
    "news":          "newsy",
    "earnings":      "data wyników",
}
# najdłuższe klucze najpierw, żeby "daily_bars" złapać przed "bars"
_FIELD_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(_FIELD_PL, key=len, reverse=True)) + r")\b"
)


# ── Helpery ──────────────────────────────────────────────────────────────────────
def _val(x):
    return getattr(x, "value", x)


def _label(d: dict, x) -> str:
    v = _val(x)
    if v in d:
        return d[v]
    return str(v).replace("_", " ").lower() if v else "—"


def _money(v, nd: int = 2) -> str:
    if v is None:
        return "—"
    try:
        return f"${float(v):,.{nd}f}"
    except (TypeError, ValueError):
        return f"${v}"


def _num(v) -> str:
    """Liczba bez zbędnych zer: 2.60 → 2.6, 5.0 → 5."""
    if v is None:
        return "—"
    try:
        return f"{round(float(v), 2):g}"
    except (TypeError, ValueError):
        return str(v)


def _pct01(v) -> str:
    """0.92 → '92%'."""
    if v is None:
        return "—"
    try:
        return f"{float(v) * 100:.0f}%"
    except (TypeError, ValueError):
        return "—"


def _age(seconds) -> str:
    if seconds is None:
        return ""
    try:
        s = float(seconds)
    except (TypeError, ValueError):
        return ""
    if s < 90:
        return "teraz"
    minutes = s / 60.0
    if minutes < 90:
        return f"~{int(round(minutes))} min"
    hours = minutes / 60.0
    if hours < 48:
        return f"~{int(round(hours))} h"
    return f"~{int(round(hours / 24))} dni"


def _humanize(text: str) -> str:
    if not text:
        return ""
    return _FIELD_RE.sub(lambda m: _FIELD_PL[m.group(1)], text)


def _humanize_fields(items) -> list[str]:
    out, seen = [], set()
    for it in (items or []):
        label = _FIELD_PL.get(it, str(it).replace("_", " "))
        if label not in seen:
            seen.add(label)
            out.append(label)
    return out


def _first(seq):
    for x in (seq or []):
        if x:
            return x
    return None


# ── Render ────────────────────────────────────────────────────────────────────
def format_plan(plan: PositionPlan) -> str:
    try:
        return _render(plan)
    except Exception as e:  # nigdy nie wywal handlera Slacka
        return f"⚪ *{getattr(plan, 'ticker', '?')}* — błąd renderowania planu ({e})"


def _render(plan: PositionPlan) -> str:
    status = _val(plan.decision_status)
    asset = _label(_ASSET, plan.asset_type)
    regime = _label(_REGIME, plan.market_regime)
    price_str = _money(plan.current_price)
    age = _age(plan.price_delay_seconds)
    actionable = status in ("READY_TO_ENTER", "WAIT_FOR_TRIGGER")

    L: list[str] = []
    L.append(f"*{_STATUS.get(status, status)}* — *{plan.ticker}*  ·  {asset}")
    reason = _humanize(plan.decision_reason or "")
    if reason:
        L.append(f"_{reason}_")

    if actionable:
        _plan_block(plan, L, price_str, age, regime)
    elif status == "DATA_INCOMPLETE":
        _incomplete_block(plan, L, price_str, age, regime)
    else:  # NO_TRADE
        _no_trade_block(plan, L, price_str, regime)

    return "\n".join(L)


def _plan_block(plan, L, price_str, age, regime):
    L.append("")
    L.append("*📋 Plan wejścia*")

    setup = _label(_SETUP, plan.setup_type)
    horizon = f"  ·  horyzont {plan.horizon_sessions} sesji" if plan.horizon_sessions else ""
    L.append(f"• *Setup:* {setup}{horizon}")

    price_line = f"• *Cena:* {price_str}"
    if age:
        price_line += f"  ·  _{age}_"
    L.append(price_line)

    if plan.entry_zone_low is not None or plan.entry_zone_high is not None:
        L.append(f"• *Strefa wejścia:* {_money(plan.entry_zone_low)}–{_money(plan.entry_zone_high)}")

    trig = []
    if plan.entry_trigger is not None:
        trig.append(f"*Trigger:* {_money(plan.entry_trigger)}")
    if plan.max_chase_price is not None:
        trig.append(f"*Nie gonić powyżej:* {_money(plan.max_chase_price)}")
    if trig:
        L.append("• " + "   ·   ".join(trig))

    stop_bits = []
    if plan.technical_stop is not None:
        s = f"*Stop:* {_money(plan.technical_stop)}"
        if plan.current_price:
            d = (plan.technical_stop - plan.current_price) / plan.current_price * 100
            s += f" _({d:+.1f}%)_"
        stop_bits.append(s)
    if plan.thesis_invalidation is not None:
        stop_bits.append(f"*Unieważnienie tezy:* {_money(plan.thesis_invalidation)}")
    if plan.risk_per_share is not None:
        stop_bits.append(f"ryzyko {_money(plan.risk_per_share)}/akcję")
    if stop_bits:
        L.append("• " + "   ·   ".join(stop_bits))

    targets = []
    for n, (t, rr) in enumerate(
        [(plan.target_1, plan.rr_target_1),
         (plan.target_2, plan.rr_target_2),
         (plan.target_3, plan.rr_target_3)], start=1
    ):
        if t is not None:
            piece = f"T{n} {_money(t)}"
            if rr is not None:
                piece += f" _(R/R {_num(rr)})_"
            targets.append(piece)
    if targets:
        L.append("• *Targety:* " + "  ·  ".join(targets))

    if plan.recommended_quantity:
        size = f"• *Pozycja:* {plan.recommended_quantity} szt"
        if plan.recommended_position_value is not None:
            size += f"  ≈ {_money(plan.recommended_position_value, nd=0)}"
        if plan.recommended_portfolio_pct is not None:
            size += f"  _({_num(plan.recommended_portfolio_pct)}% portfela)_"
        if plan.risk_budget is not None:
            size += f"  ·  budżet ryzyka {_money(plan.risk_budget, nd=0)}"
        L.append(size)

    # ── Kontekst ──
    L.append("")
    L.append("*🧭 Kontekst*")
    reg = f"• *Reżim:* {regime}"
    if plan.macro_impact:
        reg += f"  —  {plan.macro_impact}"
    L.append(reg)
    if plan.sector_rotation:
        L.append(f"• *Sektor:* {plan.sector_rotation}")
    if plan.days_to_earnings is not None:
        L.append(f"• *Earnings:* za {plan.days_to_earnings} dni  →  {_label(_EVENT, plan.event_plan)}")
    elif _val(plan.event_plan) not in (None, "NO_EVENT_RISK"):
        L.append(f"• *Wydarzenie:* {_label(_EVENT, plan.event_plan)}")
    dq = f"• *Jakość danych:* {_pct01(plan.data_quality_score)}"
    if plan.signal_confidence is not None:
        dq += f"  ·  pewność sygnału {_pct01(plan.signal_confidence)}"
    L.append(dq)
    risk = _first(plan.bear_case) or plan.correlation_warning
    if risk:
        L.append(f"• ⚠️ *Główne ryzyko:* {risk}")

    bull = _first(plan.bull_case)
    if bull:
        L.append(f"🐂 {bull}")


def _incomplete_block(plan, L, price_str, age, regime):
    L.append("")
    miss = _humanize_fields(plan.missing_data)
    if miss:
        L.append(f"• *Do odświeżenia:* {', '.join(miss)}")
    L.append(f"• *Jakość danych:* {_pct01(plan.data_quality_score)} — ponów `/wejscie {plan.ticker}` za chwilę")
    if plan.current_price is not None:
        cl = f"• *Cena (nieaktualna):* {price_str}"
        if age:
            cl += f"  ·  _{age}_"
        L.append(cl)

    work = []
    if _val(plan.setup_type) != "NO_VALID_SETUP":
        work.append(f"setup roboczy: {_label(_SETUP, plan.setup_type)}")
    work.append(f"reżim: {regime}")
    if plan.sector_rotation:
        work.append(f"sektor: {plan.sector_rotation}")
    if plan.days_to_earnings is not None:
        work.append(f"earnings za {plan.days_to_earnings} dni")
    if work:
        L.append("")
        L.append("_Kontekst roboczy (niepotwierdzony):_")
        for w in work:
            L.append(f"• {w}")


def _no_trade_block(plan, L, price_str, regime):
    L.append("")
    L.append(f"• *Setup:* {_label(_SETUP, plan.setup_type)}")
    if plan.current_price is not None:
        L.append(f"• *Cena:* {price_str}")
    reg = f"• *Reżim:* {regime}"
    if plan.sector_rotation:
        reg += f"  ·  *Sektor:* {plan.sector_rotation}"
    L.append(reg)
    why = _first(plan.bear_case)
    if why:
        L.append(f"• *Dlaczego nie teraz:* {why}")
