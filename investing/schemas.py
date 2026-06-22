"""
investing/schemas.py — Pydantic schemas & enums for the rebuilt investment skill.

Two responsibilities:

1.  Typed, validated structures for everything that crosses a module boundary or
    gets persisted — most importantly :class:`PositionPlan`, the deterministic
    output of the decision engine.

2.  The *qualitative-only* schema the LLM is allowed to fill
    (:class:`LLMQualitative`). The LLM never returns prices, scores, quantities
    or a final status — only qualitative judgement that the deterministic engine
    consumes. This is enforced structurally: those fields simply do not exist on
    the LLM schema.
"""

from __future__ import annotations

import datetime as _dt
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


# ─────────────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────────────
class DecisionStatus(str, Enum):
    READY_TO_ENTER = "READY_TO_ENTER"
    WAIT_FOR_TRIGGER = "WAIT_FOR_TRIGGER"
    NO_TRADE = "NO_TRADE"
    DATA_INCOMPLETE = "DATA_INCOMPLETE"


class DataStatus(str, Enum):
    OK = "OK"
    STALE = "STALE"
    MISSING = "MISSING"
    ERROR = "ERROR"


# Explicit sentinels — a missing/unknown value is NEVER silently coerced to a
# neutral number. These travel through features and into `missing_data`.
class DataSentinel(str, Enum):
    DATA_INCOMPLETE = "DATA_INCOMPLETE"
    DATA_STALE = "DATA_STALE"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class AssetType(str, Enum):
    EQUITY = "EQUITY"
    ETF = "ETF"
    CRYPTO_PROXY = "CRYPTO_PROXY"   # MSTR-like proxies priced off an underlying asset
    ADR = "ADR"


class SetupType(str, Enum):
    BREAKOUT = "BREAKOUT"
    PULLBACK_CONTINUATION = "PULLBACK_CONTINUATION"
    BASE_BUILDING = "BASE_BUILDING"
    MEAN_REVERSION = "MEAN_REVERSION"
    WYCKOFF_REVERSAL = "WYCKOFF_REVERSAL"
    EVENT_DRIVEN = "EVENT_DRIVEN"
    NO_VALID_SETUP = "NO_VALID_SETUP"


class EventPlan(str, Enum):
    HOLD_THROUGH_EVENT = "HOLD_THROUGH_EVENT"
    REDUCE_BEFORE_EVENT = "REDUCE_BEFORE_EVENT"
    EXIT_BEFORE_EVENT = "EXIT_BEFORE_EVENT"
    EVENT_STRATEGY = "EVENT_STRATEGY"
    NO_EVENT_RISK = "NO_EVENT_RISK"


class MarketRegime(str, Enum):
    BULL = "BULL"
    CAUTION = "CAUTION"
    DEFENSIVE = "DEFENSIVE"
    BEAR = "BEAR"
    UNKNOWN = "UNKNOWN"


class CatalystKind(str, Enum):
    EARNINGS = "EARNINGS"
    PRODUCT = "PRODUCT"
    REGULATORY = "REGULATORY"
    MACRO = "MACRO"
    MNA = "MNA"
    GUIDANCE = "GUIDANCE"
    ANALYST = "ANALYST"
    OTHER = "OTHER"


# ─────────────────────────────────────────────────────────────────────────────
# Data quality
# ─────────────────────────────────────────────────────────────────────────────
class DataPoint(BaseModel):
    """A single fetched value carrying full provenance (P0.4).

    No value enters the decision engine without ``source``, ``as_of``,
    ``fetched_at``, ``age_seconds`` and a ``status``.
    """

    name: str
    value: Optional[Any] = None
    source: str = "unknown"
    as_of: Optional[_dt.datetime] = None
    fetched_at: Optional[_dt.datetime] = None
    age_seconds: Optional[float] = None
    status: DataStatus = DataStatus.MISSING
    note: str = ""

    def ok(self) -> bool:
        return self.status == DataStatus.OK and self.value is not None

    def usable(self) -> bool:
        """OK or STALE-but-present; MISSING/ERROR are never usable."""
        return self.value is not None and self.status in (DataStatus.OK, DataStatus.STALE)


class SetupClassification(BaseModel):
    setup_type: SetupType
    qualifies: bool
    score: float = 0.0                 # 0-100, *within this setup's own rubric*
    trigger: Optional[float] = None
    stop: Optional[float] = None
    targets: list[float] = Field(default_factory=list)
    entry_zone: Optional[tuple[float, float]] = None
    max_chase: Optional[float] = None
    cancel_conditions: list[str] = Field(default_factory=list)
    recheck_conditions: list[str] = Field(default_factory=list)
    features: dict[str, Any] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)


class SizingResult(BaseModel):
    risk_budget: float = 0.0
    risk_per_share: float = 0.0
    estimated_slippage: float = 0.0
    shares_by_risk: int = 0
    shares_by_position_cap: int = 0
    shares_by_liquidity: int = 0
    final_quantity: int = 0
    position_value: float = 0.0
    portfolio_pct: float = 0.0
    binding_constraint: str = ""


class EventRiskAssessment(BaseModel):
    earnings_date: Optional[_dt.date] = None
    days_to_earnings: Optional[int] = None
    has_binary_event: bool = False
    event_kinds: list[CatalystKind] = Field(default_factory=list)
    event_plan: EventPlan = EventPlan.NO_EVENT_RISK
    blocks_full_entry: bool = False
    notes: list[str] = Field(default_factory=list)


class PortfolioImpact(BaseModel):
    sector: str = "UNKNOWN"
    narrative: str = "UNKNOWN"
    sector_exposure_before: float = 0.0
    sector_exposure_after: float = 0.0
    narrative_exposure_after: float = 0.0
    single_name_pct_after: float = 0.0
    heat_before: float = 0.0
    heat_after: float = 0.0
    portfolio_beta_after: Optional[float] = None
    correlation_warning: Optional[str] = None
    limit_breaches: list[str] = Field(default_factory=list)


class MarketContext(BaseModel):
    regime: MarketRegime = MarketRegime.UNKNOWN
    health_score: Optional[float] = None       # 0-100, percentile/z normalized
    health_percentile: Optional[float] = None
    health_zscore: Optional[float] = None
    macro_impact: str = ""                      # sector-specific, not blanket
    sector_rotation: str = ""                   # momentum, NOT "inflows"
    required_rr: float = 0.0
    size_multiplier: float = 1.0
    notes: list[str] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# LLM qualitative output — the ONLY thing Claude is permitted to produce.
# Deliberately contains no price / score / quantity / status field.
# ─────────────────────────────────────────────────────────────────────────────
class Catalyst(BaseModel):
    description: str
    kind: CatalystKind = CatalystKind.OTHER
    direction: str = "neutral"     # bullish | bearish | neutral
    timeframe: str = ""            # e.g. "next 2 weeks", "this quarter"


class LLMQualitative(BaseModel):
    """Qualitative judgement extracted from news / filings by the LLM.

    Strictly no numerical score, price, stop, quantity or final status — those
    are computed deterministically by code (P0.3).
    """

    thesis_summary: str = ""
    bull_case: list[str] = Field(default_factory=list)
    bear_case: list[str] = Field(default_factory=list)
    catalysts: list[Catalyst] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    thesis_invalidation_qualitative: list[str] = Field(
        default_factory=list,
        description="Qualitative events that would break the thesis (not price levels).",
    )
    news_recency_note: str = ""

    @field_validator("bull_case", "bear_case", "contradictions",
                     "thesis_invalidation_qualitative", mode="before")
    @classmethod
    def _coerce_list(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            return [v] if v.strip() else []
        return v


# ─────────────────────────────────────────────────────────────────────────────
# The deterministic deliverable
# ─────────────────────────────────────────────────────────────────────────────
class PositionPlan(BaseModel):
    """The audited, deterministic output of the decision engine.

    The whole point of the rebuild: the bot helps *build a trade*, not describe a
    company. A status of READY_TO_ENTER is impossible without a concrete entry and
    a thesis-invalidation level.
    """

    ticker: str
    asset_type: AssetType = AssetType.EQUITY
    strategy: str
    horizon_sessions: int

    decision_status: DecisionStatus
    decision_reason: str = ""

    current_price: Optional[float] = None
    price_as_of: Optional[_dt.datetime] = None
    price_delay_seconds: Optional[float] = None

    entry_zone_low: Optional[float] = None
    entry_zone_high: Optional[float] = None
    entry_trigger: Optional[float] = None
    max_chase_price: Optional[float] = None

    thesis_invalidation: Optional[float] = None
    technical_stop: Optional[float] = None
    estimated_slippage: Optional[float] = None
    risk_per_share: Optional[float] = None

    target_1: Optional[float] = None
    target_2: Optional[float] = None
    target_3: Optional[float] = None
    rr_target_1: Optional[float] = None
    rr_target_2: Optional[float] = None
    rr_target_3: Optional[float] = None

    risk_budget: Optional[float] = None
    recommended_quantity: Optional[int] = None
    recommended_position_value: Optional[float] = None
    recommended_portfolio_pct: Optional[float] = None

    earnings_date: Optional[_dt.date] = None
    days_to_earnings: Optional[int] = None
    event_risk: bool = False
    event_plan: EventPlan = EventPlan.NO_EVENT_RISK

    market_regime: MarketRegime = MarketRegime.UNKNOWN
    macro_impact: str = ""
    sector_rotation: str = ""

    portfolio_sector_exposure_before: Optional[float] = None
    portfolio_sector_exposure_after: Optional[float] = None
    portfolio_heat_before: Optional[float] = None
    portfolio_heat_after: Optional[float] = None
    correlation_warning: Optional[str] = None

    setup_type: SetupType = SetupType.NO_VALID_SETUP
    data_quality_score: float = 0.0
    signal_confidence: float = 0.0
    missing_data: list[str] = Field(default_factory=list)

    bull_case: list[str] = Field(default_factory=list)
    bear_case: list[str] = Field(default_factory=list)
    conditions_to_cancel: list[str] = Field(default_factory=list)
    conditions_to_recheck: list[str] = Field(default_factory=list)

    # provenance / reproducibility (recorded with every record)
    created_at: _dt.datetime = Field(default_factory=lambda: _dt.datetime.now(_dt.timezone.utc))
    config_version: str = ""
    code_version: str = ""
    model_version: str = ""
    feature_snapshot: dict[str, Any] = Field(default_factory=dict)


class LLMSchemaError(RuntimeError):
    """Raised when the LLM response fails schema validation after the repair retry."""
