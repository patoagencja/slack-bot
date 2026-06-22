"""
investing/formatting.py — render PositionPlan jako NORMALNY tekst po polsku.

Użytkownik nie jest traderem-quantem: zero kodów (READY_TO_ENTER, PULLBACK_
CONTINUATION, R/R, „heat"), zero angielskiego, zero kart-tabelek. Bot ma napisać
po ludzku, jak człowiek tłumaczący koledze: co to za spółka, czy wchodzić, gdzie
kupić, gdzie uciekać, ile kupić i dlaczego — pełnymi zdaniami, z każdą liczbą
wyjaśnioną.

Publiczne API bez zmian: format_plan(plan) -> str.
"""

from __future__ import annotations

import re

from .schemas import PositionPlan


# ── Werdykt po ludzku ─────────────────────────────────────────────────────────
_VERDICT = {
    "READY_TO_ENTER":   "✅ *{t} — można wchodzić*",
    "WAIT_FOR_TRIGGER": "⏳ *{t} — jeszcze poczekaj*",
    "NO_TRADE":         "❌ *{t} — odpuść na teraz*",
    "DATA_INCOMPLETE":  "⚠️ *{t} — brakuje mi świeżych danych*",
}

# Setup → jedno zdanie „dlaczego" zwykłym językiem.
_SETUP_WHY = {
    "BREAKOUT":              "{t} wybija się w górę z dłuższej konsolidacji — kupujący przejmują kontrolę.",
    "PULLBACK_CONTINUATION": "{t} jest w trendzie wzrostowym i właśnie cofnęła się do ważnej średniej — to często dobry moment na zakup.",
    "BASE_BUILDING":         "{t} spokojnie buduje bazę pod ewentualny większy ruch w górę.",
    "MEAN_REVERSION":        "{t} spadła ostatnio za mocno i ma szansę technicznie odbić.",
    "WYCKOFF_REVERSAL":      "{t} wygląda na próbę odwrócenia trendu po wcześniejszej przecenie.",
    "EVENT_DRIVEN":          "{t} ma przed sobą wydarzenie, które może mocno ruszyć kursem.",
    "NO_VALID_SETUP":        "{t} nie układa się w żaden wyraźny, opłacalny scenariusz.",
}

_REGIME_PHRASE = {
    "BULL":      "szeroki rynek sprzyja",
    "CAUTION":   "na rynku jest niepewnie, więc warto być ostrożnym",
    "DEFENSIVE": "rynek jest słaby, lepiej grać defensywnie",
    "BEAR":      "rynek jest w odwrocie (bessa)",
}

# Surowe nazwy pól danych → zwykłe słowa.
_FIELD_PL = {
    "price": "kurs", "quote": "kurs",
    "bars": "notowania", "daily_bars": "notowania",
    "fundamentals": "dane finansowe",
    "earnings": "termin wyników", "earnings_date": "termin wyników",
    "atr": "zmienność", "rs": "siła względem rynku", "rs_line": "siła względem rynku",
    "macro": "dane makro", "sector": "dane sektora", "volume": "obrót", "news": "newsy",
}


# ── Helpery liczbowe (format polski: spacja w tysiącach, przecinek dziesiętny) ──
def _val(x):
    return getattr(x, "value", x)


def _usd(v, dp=None):
    """210.69 -> '210,69 $', 10745 -> '10 745 $'."""
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    if dp is None:
        dp = 0 if abs(x - round(x)) < 1e-9 else 2
    s = f"{x:,.{dp}f}".replace(",", " ").replace(".", ",")
    return f"{s} $"


def _pct(x, dp=1):
    if x is None:
        return None
    try:
        return f"{float(x):.{dp}f}".replace(".", ",") + "%"
    except (TypeError, ValueError):
        return None


def _x_times(rr):
    """R/R 2.6 -> '2,6 razy tyle, czym ryzykujesz'."""
    if rr is None:
        return None
    try:
        return f"{round(float(rr), 1):g}".replace(".", ",") + " razy tyle, czym ryzykujesz"
    except (TypeError, ValueError):
        return None


def _age(seconds):
    if seconds is None:
        return ""
    try:
        s = float(seconds)
    except (TypeError, ValueError):
        return ""
    if s < 90:
        return "przed chwilą"
    m = s / 60.0
    if m < 90:
        return f"sprzed ~{int(round(m))} min"
    h = m / 60.0
    if h < 48:
        return f"sprzed ~{int(round(h))} godz."
    return f"sprzed ~{int(round(h / 24))} dni"


def _clip(text, n=200):
    if not text:
        return ""
    t = " ".join(str(text).split())
    if len(t) <= n:
        return t
    cut = t[:n]
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > 40 else cut).rstrip(",.;: ") + "…"


def _missing_words(items):
    out, seen = [], set()
    for it in (items or []):
        key = re.sub(r"\(STALE\)|\(MISSING\)|\(ERROR\)", "", str(it)).strip()
        label = _FIELD_PL.get(key, key.replace("_", " "))
        if label and label not in seen:
            seen.add(label)
            out.append(label)
    return out


def _sector_phrase(s):
    if not s:
        return ""
    if re.search(r"\+\s*\d", s):
        return "branża tej spółki radzi sobie ostatnio lepiej niż reszta giełdy"
    if re.search(r"-\s*\d+([.,]\d+)?\s*%", s):
        return "branża tej spółki radzi sobie ostatnio słabiej niż reszta giełdy"
    return ""


def _earnings_phrase(plan):
    d = plan.days_to_earnings
    if d is None:
        return ""
    if d <= 12:
        return (f"Uwaga: wyniki kwartalne ma już za {d} dni — tuż przed nimi kurs potrafi mocno "
                f"skoczyć w dowolną stronę, więc to dodatkowe ryzyko.")
    return f"Wyniki kwartalne ma dopiero za {d} dni, więc nic pilnego nie wisi."


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
        return f"⚠️ *{getattr(plan, 'ticker', '?')}* — nie udało się złożyć opisu ({e})."


def _render(plan: PositionPlan) -> str:
    status = _val(plan.decision_status)
    t = plan.ticker
    head = _VERDICT.get(status, "• *{t}*").format(t=t)

    if status == "READY_TO_ENTER":
        body = _ready(plan)
    elif status == "WAIT_FOR_TRIGGER":
        body = _wait(plan)
    elif status == "DATA_INCOMPLETE":
        body = _incomplete(plan)
    else:
        body = _no_trade(plan)

    return head + "\n" + "\n".join(body)


def _why(plan):
    return _SETUP_WHY.get(_val(plan.setup_type), "{t}").format(t=plan.ticker)


def _background(plan, lead="*Tło:* "):
    """Wspólny akapit kontekstu: rynek + branża + (skrótowo) wyniki."""
    bits = []
    reg = _REGIME_PHRASE.get(_val(plan.market_regime))
    if reg:
        bits.append(reg)
    sec = _sector_phrase(plan.sector_rotation)
    if sec:
        bits.append(sec)
    out = []
    if bits:
        sentence = bits[0][0].upper() + bits[0][1:]
        if len(bits) > 1:
            sentence += ", a " + ", ".join(bits[1:])
        out.append(lead + sentence + ".")
    earn = _earnings_phrase(plan)
    if earn:
        out.append(earn)
    return out


def _entry_sentence(plan):
    lo, hi = plan.entry_zone_low, plan.entry_zone_high
    if lo is not None and hi is not None:
        s = f"*Kupuj* w przedziale {_usd(lo)} – {_usd(hi)}."
    elif plan.entry_trigger is not None:
        s = f"*Kupuj* dopiero powyżej {_usd(plan.entry_trigger)}"
        if plan.current_price is not None:
            s += f" (kurs jest teraz przy {_usd(plan.current_price)})"
        s += "."
    elif plan.current_price is not None:
        s = f"*Kupuj* w okolicy bieżącej ceny {_usd(plan.current_price)}."
    else:
        s = "*Kupuj* ostrożnie, blisko poziomu wejścia."
    if plan.max_chase_price is not None:
        s += f" Nie goń, jeśli wystrzeli powyżej {_usd(plan.max_chase_price)}."
    return s


def _stop_sentence(plan):
    if plan.technical_stop is None:
        return None
    s = f"*Linia obrony (stop): {_usd(plan.technical_stop)}* — jeśli kurs tam spadnie, sprzedajesz i wychodzisz"
    if plan.current_price:
        drop = (plan.current_price - plan.technical_stop) / plan.current_price * 100
        if drop > 0:
            s += f" ze stratą ok. {_pct(drop)}"
    s += ". To chroni kapitał przed większym osunięciem."
    return s


def _targets_sentence(plan):
    pts = [p for p in (plan.target_1, plan.target_2, plan.target_3) if p is not None]
    if not pts:
        return None
    if len(pts) == 1:
        s = f"*Cel zarobku:* {_usd(pts[0])}."
    else:
        s = "*Cele zarobku:* najpierw " + _usd(pts[0]) + ", potem " + ", ".join(_usd(p) for p in pts[1:]) + "."
    times = _x_times(plan.rr_target_1)
    if times:
        s += f" Przy pierwszym celu zarabiasz ok. {times} — układ jest opłacalny."
    return s


def _size_sentence(plan):
    if not plan.recommended_quantity:
        return None
    s = f"*Ile kupić:* ok. {plan.recommended_quantity} akcji"
    if plan.recommended_position_value is not None:
        s += f" za ~{_usd(plan.recommended_position_value)}"
    if plan.recommended_portfolio_pct is not None:
        s += f" (czyli {_pct(plan.recommended_portfolio_pct)} portfela)"
    s += "."
    if plan.risk_budget is not None:
        s += f" Tak dobrane, żeby w najgorszym razie stracić nie więcej niż ~{_usd(plan.risk_budget)}."
    return s


def _ready(plan):
    L = [_why(plan), "", "*Jak to rozegrać:*"]
    for line in (_entry_sentence(plan), _stop_sentence(plan),
                 _targets_sentence(plan), _size_sentence(plan)):
        if line:
            L.append("• " + line)
    L.append("")
    L += _background(plan)
    risk = _first(plan.bear_case)
    if risk:
        L.append(f"*Największe ryzyko:* {_clip(risk, 220)}")
    return L


def _wait(plan):
    L = [_why(plan) + " Brakuje jednak potwierdzenia — nie wchodź na ślepo.", ""]

    # dlaczego jeszcze nie — po ludzku
    reason = _humanized_wait_reason(plan)
    if reason:
        L.append(reason)

    # gdy warunek się spełni — zwięzły plan
    plan_bits = []
    if plan.entry_zone_low is not None and plan.entry_zone_high is not None:
        plan_bits.append(f"kupno {_usd(plan.entry_zone_low)} – {_usd(plan.entry_zone_high)}")
    if plan.technical_stop is not None:
        plan_bits.append(f"linia obrony {_usd(plan.technical_stop)}")
    pts = [p for p in (plan.target_1, plan.target_2) if p is not None]
    if pts:
        plan_bits.append("cele " + " i ".join(_usd(p) for p in pts))
    if plan_bits:
        L.append("*Gdy to się stanie, plan jest taki:* " + ", ".join(plan_bits) + ".")
    if plan.thesis_invalidation is not None:
        L.append(f"*Odpuść,* jeśli kurs spadnie poniżej {_usd(plan.thesis_invalidation)} — wtedy cały pomysł się psuje.")

    L.append("")
    L += _background(plan)
    return L


def _humanized_wait_reason(plan):
    raw = plan.decision_reason or ""
    if "R/R" in raw or "wymagane" in raw:
        return ("*Dlaczego jeszcze nie:* przy obecnej cenie potencjalny zysk jest za mały w stosunku "
                "do ryzyka. Lepiej poczekać na niższe, korzystniejsze wejście.")
    if "Wydarzenie" in raw or (plan.days_to_earnings is not None and plan.days_to_earnings <= 12):
        d = plan.days_to_earnings
        return ("*Dlaczego jeszcze nie:* tuż przed wynikami"
                + (f" (za {d} dni)" if d is not None else "")
                + " lepiej nie wchodzić pełną pozycją — poczekaj, aż miną.")
    if plan.entry_trigger is not None:
        return (f"*Na co czekać:* aż kurs wyraźnie przebije {_usd(plan.entry_trigger)} — to będzie sygnał, "
                "że ruch w górę faktycznie rusza.")
    return "*Dlaczego jeszcze nie:* układ jest obiecujący, ale brak potwierdzenia wejścia."


def _no_trade(plan):
    L = [_why(plan)]
    raw = plan.decision_reason or ""
    if "limit" in raw.lower():
        L.append("Wejście oznaczałoby zbyt duże skupienie ryzyka w jednym miejscu (przekroczone limity portfela).")
    elif "pozycj" in raw.lower() or "budże" in raw.lower():
        L.append("Przy bezpiecznej wielkości pozycja wychodzi praktycznie zerowa — nie ma czego grać.")
    L.append("Lepiej poczekać na wyraźniejszą okazję.")
    L.append("")
    why_not = _first(plan.bear_case)
    if why_not:
        L.append(f"*Główny powód ostrożności:* {_clip(why_not, 220)}")
    L += _background(plan)
    return L


def _incomplete(plan):
    L = []
    miss = _missing_words(plan.missing_data)
    miss_txt = (": " + ", ".join(miss)) if miss else ""
    age = _age(plan.price_delay_seconds)
    age_txt = f" ({age})" if age else ""
    L.append(f"Nie dam teraz konkretnego planu, bo dane są nieaktualne{age_txt}{miss_txt}.")
    L.append(f"Odśwież za chwilę — napisz `/wejscie {plan.ticker}`, a policzę wszystko na bieżących cenach.")

    # zgrubny, ostrożny kontekst
    ctx = []
    if _val(plan.setup_type) != "NO_VALID_SETUP":
        ctx.append(_why(plan).rstrip("."))
    reg = _REGIME_PHRASE.get(_val(plan.market_regime))
    if reg:
        ctx.append(reg)
    if plan.days_to_earnings is not None:
        ctx.append(f"wyniki kwartalne za {plan.days_to_earnings} dni")
    if ctx:
        L.append("")
        L.append("*Tyle wiem na teraz (do potwierdzenia):* " + "; ".join(ctx) + ".")
    return L
