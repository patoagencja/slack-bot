"""JSON schema / Pydantic validation tests."""

import datetime as dt

from investing.schemas import (DataPoint, DataStatus, DecisionStatus, LLMQualitative,
                                PositionPlan, SetupType, AssetType)


def test_position_plan_json_schema_has_core_fields():
    schema = PositionPlan.model_json_schema()
    props = schema["properties"]
    for field in ("decision_status", "entry_trigger", "thesis_invalidation",
                  "technical_stop", "rr_target_1", "recommended_quantity",
                  "data_quality_score", "missing_data", "event_plan"):
        assert field in props, f"missing {field} in PositionPlan schema"


def test_llm_schema_has_no_numeric_decision_fields():
    """The LLM schema must structurally exclude price/stop/score/qty/status."""
    props = LLMQualitative.model_json_schema()["properties"]
    forbidden = {"price", "entry", "stop", "score", "quantity", "decision_status",
                 "target_1", "risk_per_share", "recommended_quantity"}
    assert forbidden.isdisjoint(props.keys())


def test_llm_qualitative_coerces_string_to_list():
    q = LLMQualitative.model_validate({"bull_case": "single string", "bear_case": None})
    assert q.bull_case == ["single string"]
    assert q.bear_case == []


def test_datapoint_usable_semantics():
    ok = DataPoint(name="x", value=1.0, status=DataStatus.OK)
    stale = DataPoint(name="x", value=1.0, status=DataStatus.STALE)
    missing = DataPoint(name="x", value=None, status=DataStatus.MISSING)
    assert ok.ok() and ok.usable()
    assert not stale.ok() and stale.usable()
    assert not missing.ok() and not missing.usable()


def test_position_plan_round_trips_json():
    plan = PositionPlan(ticker="NVDA", strategy="POSITION_20_90", horizon_sessions=45,
                        decision_status=DecisionStatus.NO_TRADE, setup_type=SetupType.NO_VALID_SETUP,
                        asset_type=AssetType.EQUITY, earnings_date=dt.date(2026, 9, 1))
    data = plan.model_dump(mode="json")
    again = PositionPlan.model_validate(data)
    assert again.ticker == "NVDA"
    assert again.decision_status == DecisionStatus.NO_TRADE
