"""
investing — rebuilt investment skill for the Sebol bot.

Decision architecture (deterministic, audited):
    data gateway -> providers (DataPoints w/ provenance) -> data-quality gate
    -> setup classifier -> sizing / event-risk / portfolio-risk / market-health
    -> deterministic decision engine -> PositionPlan -> SQLite persistence

The LLM contributes qualitative judgement only; it never sets a price, stop,
score, quantity or the final status.

Imports here are lazy so that lightweight consumers (e.g. ``from investing.config
import CLAUDE_MODEL_PRIMARY`` in the legacy job files) don't pull in pydantic /
the full decision stack.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "build_position_plan",
    "format_plan",
    "PositionPlan",
    "DecisionStatus",
    "SetupType",
    "EventPlan",
    "AssetType",
    "MarketRegime",
    "LLMSchemaError",
]

_SCHEMA_NAMES = {"PositionPlan", "DecisionStatus", "SetupType", "EventPlan",
                 "AssetType", "MarketRegime", "LLMSchemaError"}


def build_position_plan(*args, **kwargs):
    from .entry import build_position_plan as _impl
    return _impl(*args, **kwargs)


def format_plan(plan) -> str:
    from .formatting import format_plan as _impl
    return _impl(plan)


def __getattr__(name: str) -> Any:          # PEP 562 lazy attribute access
    if name in _SCHEMA_NAMES:
        from . import schemas
        return getattr(schemas, name)
    raise AttributeError(f"module 'investing' has no attribute {name!r}")
