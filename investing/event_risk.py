"""
investing/event_risk.py — explicit event-risk rules.

Covers earnings, investor day, FDA/biotech catalysts, regulatory decisions, major
macro prints, splits/issuance, lock-up expiry and guidance updates. By default a
full new position may NOT be opened ahead of a binary event without an explicit
plan, so the engine surfaces one of:

    HOLD_THROUGH_EVENT | REDUCE_BEFORE_EVENT | EXIT_BEFORE_EVENT |
    EVENT_STRATEGY | NO_EVENT_RISK
"""

from __future__ import annotations

import datetime as _dt
from typing import Optional, Sequence

from . import config
from .schemas import (Catalyst, CatalystKind, EventPlan, EventRiskAssessment,
                      SetupType)

# Calendar-day proxy for a "sessions" blackout window (~5 sessions ≈ 7 days).
_SESSIONS_TO_DAYS = 7.0 / 5.0

_BINARY_KINDS = {CatalystKind.EARNINGS, CatalystKind.REGULATORY, CatalystKind.GUIDANCE}


def assess(
    *,
    earnings_date: Optional[_dt.date],
    days_to_earnings: Optional[int],
    catalysts: Sequence[Catalyst],
    setup_type: SetupType,
    blackout_sessions: int = config.EVENT_BLACKOUT_SESSIONS,
) -> EventRiskAssessment:
    blackout_days = blackout_sessions * _SESSIONS_TO_DAYS
    a = EventRiskAssessment(earnings_date=earnings_date, days_to_earnings=days_to_earnings)

    earnings_imminent = days_to_earnings is not None and 0 <= days_to_earnings <= blackout_days
    if earnings_imminent:
        a.has_binary_event = True
        a.event_kinds.append(CatalystKind.EARNINGS)
        a.notes.append(f"Earnings za {days_to_earnings} dni (okno blackout {blackout_sessions} sesji)")

    for c in catalysts:
        if c.kind in _BINARY_KINDS and _is_near(c.timeframe):
            a.has_binary_event = True
            if c.kind not in a.event_kinds:
                a.event_kinds.append(c.kind)
            a.notes.append(f"Wydarzenie binarne: {c.description[:80]} ({c.timeframe})")
        elif c.kind in (CatalystKind.MNA, CatalystKind.MACRO):
            a.notes.append(f"Ryzyko wydarzenia (nie-binarne): {c.description[:80]}")

    # Decide the plan
    if not a.has_binary_event:
        a.event_plan = EventPlan.NO_EVENT_RISK
        a.blocks_full_entry = False
        return a

    if setup_type == SetupType.EVENT_DRIVEN:
        # the event IS the thesis — explicit event strategy, defined risk required
        a.event_plan = EventPlan.EVENT_STRATEGY
        a.blocks_full_entry = False
        a.notes.append("Setup event-driven: wymagana zdefiniowana wielkość/struktura na wydarzenie")
    else:
        # a normal swing/position setup must not take full size into a binary event
        a.event_plan = EventPlan.REDUCE_BEFORE_EVENT
        a.blocks_full_entry = True
        a.notes.append("Pełna nowa pozycja zablokowana przed wydarzeniem binarnym — "
                       "wejdź po wydarzeniu lub zredukuj wielkość (starter)")
    return a


def _is_near(timeframe: str) -> bool:
    """Heuristic: treat near-term qualitative timeframes as inside the window."""
    if not timeframe:
        return False
    t = timeframe.lower()
    near_tokens = ("today", "tomorrow", "this week", "next week", "days", "imminent",
                   "dziś", "jutro", "tydzień", "tygodni", "dni", "wkrótce", "this month")
    return any(tok in t for tok in near_tokens)
