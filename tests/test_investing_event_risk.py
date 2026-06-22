"""Event-risk tests — binary events block full entry without a plan."""

import datetime as dt

from investing import event_risk
from investing.schemas import Catalyst, CatalystKind, EventPlan, SetupType


def test_imminent_earnings_blocks_full_entry():
    a = event_risk.assess(earnings_date=dt.date.today() + dt.timedelta(days=5),
                          days_to_earnings=5, catalysts=[], setup_type=SetupType.BREAKOUT)
    assert a.has_binary_event
    assert a.blocks_full_entry
    assert a.event_plan == EventPlan.REDUCE_BEFORE_EVENT


def test_far_earnings_no_event_risk():
    a = event_risk.assess(earnings_date=dt.date.today() + dt.timedelta(days=70),
                          days_to_earnings=70, catalysts=[], setup_type=SetupType.BREAKOUT)
    assert not a.has_binary_event
    assert a.event_plan == EventPlan.NO_EVENT_RISK
    assert not a.blocks_full_entry


def test_event_driven_setup_uses_event_strategy():
    a = event_risk.assess(earnings_date=dt.date.today() + dt.timedelta(days=3),
                          days_to_earnings=3, catalysts=[], setup_type=SetupType.EVENT_DRIVEN)
    assert a.event_plan == EventPlan.EVENT_STRATEGY
    assert not a.blocks_full_entry


def test_near_regulatory_catalyst_is_binary():
    cat = Catalyst(description="FDA decision", kind=CatalystKind.REGULATORY,
                   timeframe="next week")
    a = event_risk.assess(earnings_date=None, days_to_earnings=None,
                          catalysts=[cat], setup_type=SetupType.BREAKOUT)
    assert a.has_binary_event
    assert a.blocks_full_entry
