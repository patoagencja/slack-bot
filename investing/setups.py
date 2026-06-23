"""
investing/setups.py — the Setup Classifier.

Before any sizing or decision, the current technical situation is classified as
exactly ONE of:

    BREAKOUT | PULLBACK_CONTINUATION | BASE_BUILDING | MEAN_REVERSION |
    WYCKOFF_REVERSAL | EVENT_DRIVEN | NO_VALID_SETUP

Crucially, the per-method signals (Minervini / Wyckoff / VCP / bounce / CAN SLIM)
are NOT summed into one universal score. Each setup carries its own qualification
rules, trigger, stop, targets and cancel conditions. RSI is only one extension
feature and can never, by itself, force NO_TRADE / WAIT.
"""

from __future__ import annotations

from typing import Optional, Sequence

from . import config, indicators as ind
from .schemas import SetupClassification, SetupType


def _round(x: Optional[float], n: int = 2) -> Optional[float]:
    return round(x, n) if x is not None else None


def _targets_from(entry: float, stop: float, measured_move: Optional[float] = None) -> list[float]:
    """R-multiple targets (2R / 3.5R / 5R). If a measured move exceeds T1 it is
    promoted to T1 so the first target reflects real structure."""
    risk = max(entry - stop, 1e-9)
    t1 = entry + 2.0 * risk
    if measured_move and (entry + measured_move) > t1:
        t1 = entry + measured_move
    t2 = entry + 3.5 * risk
    t3 = entry + 5.0 * risk
    t2 = max(t2, t1 + 1.5 * risk)
    t3 = max(t3, t2 + 1.5 * risk)
    return [round(t1, 2), round(t2, 2), round(t3, 2)]


# ─────────────────────────────────────────────────────────────────────────────
# Individual setup evaluators. Each returns (SetupClassification | None).
# `feat` is the shared feature bundle built in classify().
# ─────────────────────────────────────────────────────────────────────────────
def _eval_breakout(feat: dict) -> Optional[SetupClassification]:
    ext = feat["ext"]
    base = feat["base"]
    rs = feat["rs"]
    price = ext["price"]
    atr = ext.get("atr")
    pivot = ext.get("pivot")
    if not (atr and pivot):
        return None

    reasons, qualifies = [], True
    # base validity
    base_len = base.get("base_length") or 0
    if base_len < 20:
        qualifies = False
        reasons.append(f"baza zbyt krótka ({base_len} sesji)")
    else:
        reasons.append(f"baza {base_len} sesji")
    # base must be reasonably TIGHT — a very wide range isn't a base, and its low
    # would make the stop (and R-multiple targets) absurd.
    depth = base.get("base_depth_pct")
    if depth is not None and depth > config.MAX_BREAKOUT_BASE_DEPTH_PCT:
        qualifies = False
        reasons.append(f"baza za szeroka ({depth}% > {config.MAX_BREAKOUT_BASE_DEPTH_PCT}%) — to nie ciasna konsolidacja")
    if base.get("volatility_contraction"):
        reasons.append("kontrakcja zmienności ✓")
    else:
        qualifies = False
        reasons.append("brak kontrakcji zmienności")
    if feat.get("volume_contraction"):
        reasons.append("kontrakcja wolumenu w bazie ✓")
    # relative strength
    rs63 = rs.get("rs63_broad")
    if rs63 is not None and rs63 > 0:
        reasons.append(f"RS63 +{rs63}")
    elif rs63 is not None:
        qualifies = False
        reasons.append(f"słaby RS63 ({rs63})")
    # proximity to pivot. Being over-extended ABOVE the pivot does NOT invalidate
    # the setup — it just means we don't chase (decision engine -> WAIT). But being
    # far BELOW the pivot DOES: it's not a near-term breakout, so we must not tell
    # the user to "buy on a break above X" when X is far above the current price.
    dist_atr = ext.get("dist_from_pivot_atr")
    max_chase = round(pivot + config.MAX_CHASE_ATR * atr, 2)
    below_atr = (pivot - price) / atr if atr else 0.0
    below_pct = (pivot - price) / price if price else 0.0
    if below_atr > config.MAX_BELOW_PIVOT_ATR or below_pct > config.MAX_BELOW_PIVOT_PCT:
        qualifies = False
        reasons.append(
            f"cena {round(below_pct*100,1)}% pod pivotem {round(pivot,2)} — za daleko do wybicia, "
            "to nie wejście breakout (czekaj aż zbuduje bazę bliżej oporu)")
    elif dist_atr is not None and dist_atr > config.MAX_CHASE_ATR:
        reasons.append(f"rozciągnięta od pivotu (+{dist_atr} ATR > {config.MAX_CHASE_ATR}) — nie gonić")
    # volume confirmation (only required once price is at/over pivot)
    vr = ext.get("volume_ratio_1d")
    if price >= pivot and vr is not None and vr < 1.3:
        reasons.append(f"słabe potwierdzenie wolumenem ({vr:.2f}x)")

    base_low = base.get("base_low")
    structure_stop = base_low if base_low else pivot - 1.0 * atr
    # stop = failed-breakout invalidation, with ATR as buffer (not the sole basis)
    stop = round(min(pivot - 0.5 * atr, structure_stop + 0.0), 2)
    entry_low = round(pivot, 2)
    entry_high = max_chase
    entry_ref = round(min(max(price, entry_low), entry_high), 2)
    mm = (base.get("base_high", pivot) - base.get("base_low", pivot)) if base.get("base_low") else None
    targets = _targets_from(entry_ref, stop, measured_move=mm)

    return SetupClassification(
        setup_type=SetupType.BREAKOUT,
        qualifies=qualifies,
        score=_breakout_score(feat, qualifies),
        trigger=round(pivot, 2),
        stop=stop,
        targets=targets,
        entry_zone=(entry_low, entry_high),
        max_chase=max_chase,
        cancel_conditions=[
            f"Zamknięcie z powrotem poniżej pivotu {round(pivot,2)} (failed breakout)",
            "Wybicie bez wolumenu > 1.3x średniej",
            "Utrata RS63 vs benchmark",
        ],
        recheck_conditions=[
            f"Cena wejdzie w strefę {entry_low}-{entry_high}",
            "Pojawi się świeca wybicia z wolumenem",
        ],
        features={"pivot": pivot, "atr": atr, "dist_from_pivot_atr": dist_atr,
                  "base_depth_pct": base.get("base_depth_pct")},
        reasons=reasons,
    )


def _breakout_score(feat: dict, qualifies: bool) -> float:
    if not qualifies:
        return 0.0
    s = 50.0
    ext, base, rs = feat["ext"], feat["base"], feat["rs"]
    if base.get("volatility_contraction"):
        s += 12
    if feat.get("volume_contraction"):
        s += 8
    rs63 = rs.get("rs63_broad")
    if rs63 and rs63 > 5:
        s += 12
    elif rs63 and rs63 > 0:
        s += 6
    vr = ext.get("volume_ratio_1d")
    if vr and vr >= 1.5:
        s += 10
    pr = rs.get("pct_rank_universe")
    if pr and pr >= 80:
        s += 8
    return min(100.0, s)


def _eval_pullback(feat: dict) -> Optional[SetupClassification]:
    ext = feat["ext"]
    rs = feat["rs"]
    closes = feat["closes"]
    price = ext["price"]
    atr = ext.get("atr")
    ma20, ma50 = ext.get("ma20"), ext.get("ma50")
    ma200 = ind.sma(closes, 200)
    if not (atr and ma20 and ma50):
        return None

    reasons, qualifies = [], True
    # established uptrend (daily). weekly trend approximated by MA50>MA200.
    uptrend = price > ma50 and (ma200 is None or ma50 > ma200)
    if uptrend:
        reasons.append("trend wzrostowy (cena>MA50, MA50>MA200) ✓")
    else:
        qualifies = False
        reasons.append("brak nadrzędnego trendu wzrostowego")
    # pullback into support zone (MA20 / MA50 / last breakout)
    near_ma20 = abs(price - ma20) / ma20 <= 0.03
    near_ma50 = abs(price - ma50) / ma50 <= 0.04
    if near_ma20 or near_ma50:
        reasons.append("korekta do wsparcia (MA20/MA50) ✓")
    else:
        qualifies = False
        reasons.append("cena nie jest przy wsparciu")
    # declining volume during pullback
    if feat.get("volume_contraction"):
        reasons.append("malejący wolumen w korekcie ✓")
    # higher-low structure
    if feat.get("higher_low"):
        reasons.append("struktura higher-low ✓")
    elif feat.get("higher_low") is False:
        qualifies = False
        reasons.append("złamana struktura higher-low")
    # RS maintained
    rs63 = rs.get("rs63_broad")
    if rs63 is not None and rs63 < 0:
        reasons.append(f"RS osłabł ({rs63})")

    # stop from STRUCTURE (recent higher-low / MA50), ATR only as buffer
    swing_low = min(feat["lows"][-20:]) if len(feat["lows"]) >= 20 else ma50
    structure = min(swing_low, ma50)
    stop = round(structure - 0.3 * atr, 2)
    # trigger = reclaim of prior day's high (continuation confirmation)
    trigger = round(max(feat["highs"][-3:]), 2)
    entry_low = round(min(ma20, price), 2)
    entry_high = round(trigger, 2)
    entry_ref = round(price, 2)
    targets = _targets_from(entry_ref, stop)

    return SetupClassification(
        setup_type=SetupType.PULLBACK_CONTINUATION,
        qualifies=qualifies,
        score=(60.0 if qualifies else 0.0)
              + (10 if feat.get("higher_low") else 0)
              + (10 if feat.get("volume_contraction") else 0),
        trigger=trigger,
        stop=stop,
        targets=targets,
        entry_zone=(entry_low, entry_high),
        max_chase=round(trigger + 0.5 * atr, 2),
        cancel_conditions=[
            f"Zamknięcie poniżej struktury higher-low / {stop}",
            "Wzrost wolumenu na spadkach (dystrybucja)",
            "Utrata MA50 z rosnącym wolumenem",
        ],
        recheck_conditions=[
            f"Odbicie i reclaim {trigger} z wolumenem",
            "Świeca odwrócenia na wsparciu",
        ],
        features={"ma20": ma20, "ma50": ma50, "ma200": ma200, "atr": atr},
        reasons=reasons,
    )


def _eval_base_building(feat: dict) -> Optional[SetupClassification]:
    """Tight base forming but not yet at a pivot trigger -> WAIT_FOR_TRIGGER."""
    base = feat["base"]
    ext = feat["ext"]
    atr, pivot = ext.get("atr"), ext.get("pivot")
    if not (atr and pivot):
        return None
    depth = base.get("base_depth_pct")
    length = base.get("base_length") or 0
    qualifies = bool(length >= 15 and depth is not None and depth < 35
                     and base.get("volatility_contraction"))
    reasons = [f"baza {length} sesji, głębokość {depth}%"]
    max_chase = round(pivot + config.MAX_CHASE_ATR * atr, 2)
    return SetupClassification(
        setup_type=SetupType.BASE_BUILDING,
        qualifies=qualifies,
        score=40.0 if qualifies else 0.0,
        trigger=round(pivot, 2),
        stop=round(base.get("base_low", pivot - atr), 2),
        targets=_targets_from(round(pivot, 2), round(base.get("base_low", pivot - atr), 2)),
        entry_zone=(round(pivot, 2), max_chase),
        max_chase=max_chase,
        cancel_conditions=[f"Przebicie dołu bazy {base.get('base_low')}"],
        recheck_conditions=[f"Wybicie ponad pivot {round(pivot,2)}"],
        features={"pivot": pivot, "atr": atr, "base_depth_pct": depth},
        reasons=reasons,
    )


def _eval_mean_reversion(feat: dict) -> Optional[SetupClassification]:
    ext = feat["ext"]
    closes = feat["closes"]
    price = ext["price"]
    atr = ext.get("atr")
    ma50 = ext.get("ma50")
    ma200 = ind.sma(closes, 200)
    rsi = ext.get("rsi14")
    if not (atr and ma50):
        return None
    # quality uptrend + deeply oversold pullback + nascent reversal
    uptrend = (ma200 is None or ma50 > ma200) and price > (ma200 or 0)
    oversold = rsi is not None and rsi < 35
    reversal = len(closes) >= 2 and closes[-1] > closes[-2]
    qualifies = bool(uptrend and oversold and reversal)
    stop = round(min(feat["lows"][-5:]) - 0.3 * atr, 2)
    entry_ref = round(price, 2)
    return SetupClassification(
        setup_type=SetupType.MEAN_REVERSION,
        qualifies=qualifies,
        score=45.0 if qualifies else 0.0,
        trigger=round(price, 2),
        stop=stop,
        targets=_targets_from(entry_ref, stop),
        entry_zone=(round(price - 0.5 * atr, 2), round(price + 0.5 * atr, 2)),
        max_chase=round(price + 0.5 * atr, 2),
        cancel_conditions=[f"Zamknięcie poniżej {stop}", "Brak odwrócenia w 3 sesje"],
        recheck_conditions=["Potwierdzenie odwrócenia świecą + wolumenem"],
        features={"rsi14": rsi, "ma50": ma50, "ma200": ma200, "atr": atr},
        reasons=[f"RSI {rsi}, odbicie od wsparcia w trendzie"],
    )


def _eval_wyckoff(feat: dict) -> Optional[SetupClassification]:
    """Spring / false breakdown below support + recovery on low volume."""
    closes, lows, vols = feat["closes"], feat["lows"], feat["volumes"]
    ext = feat["ext"]
    atr = ext.get("atr")
    if not atr or len(closes) < 40:
        return None
    support = min(lows[-40:-5]) if len(lows) >= 45 else min(lows[:-5] or lows)
    recent_low = min(lows[-5:])
    recovered = closes[-1] > support
    undercut = recent_low < support
    low_vol = (ind.volume_ratio(vols, 5) or 99) < 1.0
    qualifies = bool(undercut and recovered and low_vol)
    stop = round(recent_low - 0.2 * atr, 2)
    entry_ref = round(closes[-1], 2)
    return SetupClassification(
        setup_type=SetupType.WYCKOFF_REVERSAL,
        qualifies=qualifies,
        score=45.0 if qualifies else 0.0,
        trigger=round(support, 2),
        stop=stop,
        targets=_targets_from(entry_ref, stop),
        entry_zone=(round(support, 2), round(support + 0.5 * atr, 2)),
        max_chase=round(support + 0.7 * atr, 2),
        cancel_conditions=[f"Zamknięcie poniżej {stop} (spring failed)"],
        recheck_conditions=["Test wsparcia z malejącym wolumenem"],
        features={"support": support, "atr": atr},
        reasons=["false breakdown + recovery na niskim wolumenie"],
    )


def _eval_event_driven(feat: dict) -> Optional[SetupClassification]:
    """Imminent binary catalyst is the dominant driver."""
    if not feat.get("imminent_binary_event"):
        return None
    ext = feat["ext"]
    atr = ext.get("atr") or 0.0
    price = ext["price"]
    stop = round(price - 1.5 * atr, 2) if atr else None
    return SetupClassification(
        setup_type=SetupType.EVENT_DRIVEN,
        qualifies=True,
        score=35.0,
        trigger=round(price, 2),
        stop=stop,
        targets=_targets_from(price, stop) if stop else [],
        entry_zone=(round(price - 0.5 * atr, 2), round(price + 0.5 * atr, 2)) if atr else None,
        max_chase=round(price + 0.5 * atr, 2) if atr else None,
        cancel_conditions=["Brak jawnego planu na wydarzenie binarne"],
        recheck_conditions=["Po rozliczeniu wydarzenia i ustabilizowaniu zmienności"],
        features={"atr": atr},
        reasons=["dominującym driverem jest bliskie wydarzenie binarne"],
    )


# Evaluation order = tie-break priority for equally-scoring setups.
_EVALUATORS = [
    _eval_breakout,
    _eval_pullback,
    _eval_wyckoff,
    _eval_mean_reversion,
    _eval_event_driven,
    _eval_base_building,
]


def build_features(
    closes: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    volumes: Sequence[float],
    rs: dict,
    *,
    imminent_binary_event: bool = False,
) -> dict:
    return {
        "closes": list(closes),
        "highs": list(highs),
        "lows": list(lows),
        "volumes": list(volumes),
        "rs": rs or {},
        "ext": ind.extension_metrics(closes, highs, lows, volumes),
        "base": ind.base_stats(closes, highs, lows),
        "volume_contraction": ind.volume_contraction(volumes),
        "higher_low": ind.higher_low(lows),
        "imminent_binary_event": imminent_binary_event,
    }


def classify(feat: dict) -> SetupClassification:
    """Pick exactly one setup. Highest qualifying score wins; ties broken by the
    evaluator order. If nothing qualifies, return the best *non-qualifying*
    candidate's params under NO_VALID_SETUP so the user still sees the levels."""
    evaluated: list[SetupClassification] = []
    for fn in _EVALUATORS:
        try:
            c = fn(feat)
        except Exception:
            c = None
        if c is not None:
            evaluated.append(c)

    qualifying = [c for c in evaluated if c.qualifies]
    if qualifying:
        return max(qualifying, key=lambda c: c.score)

    # nothing qualifies -> NO_VALID_SETUP, but keep the closest candidate's levels
    if evaluated:
        best = max(evaluated, key=lambda c: c.score)
        best.setup_type = SetupType.NO_VALID_SETUP
        best.qualifies = False
        if "brak kwalifikującego się setupu" not in best.reasons:
            best.reasons.insert(0, "brak kwalifikującego się setupu")
        return best
    return SetupClassification(
        setup_type=SetupType.NO_VALID_SETUP,
        qualifies=False,
        reasons=["niewystarczające dane techniczne do klasyfikacji setupu"],
    )
