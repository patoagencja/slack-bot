"""
investing/data_quality.py — the Data Quality Gate (P0.4).

Every fetched value is wrapped in a :class:`DataPoint` carrying source / as_of /
fetched_at / age_seconds / status. Missing data is NEVER coerced into a neutral
number — it surfaces as an explicit sentinel and, when a value required by the
chosen strategy is missing or stale, the gate forbids READY_TO_ENTER.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Optional

from . import config
from .schemas import DataPoint, DataSentinel, DataStatus


def _utcnow() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _aware(ts: Optional[_dt.datetime]) -> Optional[_dt.datetime]:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=_dt.timezone.utc)
    return ts


def make_datapoint(
    name: str,
    value: Any,
    *,
    source: str,
    kind: str,
    as_of: Optional[_dt.datetime] = None,
    fetched_at: Optional[_dt.datetime] = None,
    error: Optional[str] = None,
) -> DataPoint:
    """Construct a DataPoint and derive age + status from the TTL table for ``kind``.

    ``kind`` keys into :data:`config.TTL` (e.g. "quote", "daily_bars"). A value of
    ``None`` is MISSING (or ERROR if an ``error`` is supplied). Age beyond the
    ``max`` TTL is STALE; between ``fresh`` and ``max`` is also STALE; under
    ``fresh`` is OK.
    """
    now = _utcnow()
    fetched_at = _aware(fetched_at) or now
    as_of = _aware(as_of) or fetched_at

    if error is not None:
        return DataPoint(name=name, value=None, source=source, as_of=as_of,
                         fetched_at=fetched_at, age_seconds=None,
                         status=DataStatus.ERROR, note=str(error)[:300])

    if value is None:
        return DataPoint(name=name, value=None, source=source, as_of=as_of,
                         fetched_at=fetched_at, age_seconds=None,
                         status=DataStatus.MISSING, note="no value")

    age = max(0.0, (now - as_of).total_seconds())
    ttl = config.TTL.get(kind, {"fresh": 3600, "max": 24 * 3600})
    if age > ttl["max"]:
        status = DataStatus.STALE
        note = f"age {int(age)}s > max {ttl['max']}s"
    elif age > ttl["fresh"]:
        status = DataStatus.STALE
        note = f"age {int(age)}s > fresh {ttl['fresh']}s"
    else:
        status = DataStatus.OK
        note = ""
    return DataPoint(name=name, value=value, source=source, as_of=as_of,
                     fetched_at=fetched_at, age_seconds=age, status=status, note=note)


class GateResult:
    """Outcome of evaluating a bundle of DataPoints against a strategy's needs."""

    def __init__(self) -> None:
        self.score: float = 0.0
        self.missing: list[str] = []          # required, MISSING/ERROR
        self.stale: list[str] = []            # required, STALE
        self.sentinels: dict[str, str] = {}   # field -> DataSentinel value
        self.can_enter: bool = False
        self.reasons: list[str] = []

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "missing": self.missing,
            "stale": self.stale,
            "sentinels": self.sentinels,
            "can_enter": self.can_enter,
            "reasons": self.reasons,
        }


def evaluate(
    points: dict[str, DataPoint],
    *,
    required: list[str],
    optional: Optional[list[str]] = None,
) -> GateResult:
    """Score data quality and decide whether READY_TO_ENTER is permissible.

    ``required`` fields gate entry: any MISSING/ERROR -> DATA_INCOMPLETE; any
    STALE -> DATA_STALE; either way ``can_enter`` is False. ``optional`` fields
    only affect the quality score.
    """
    optional = optional or []
    res = GateResult()

    considered = list(required) + list(optional)
    if not considered:
        res.can_enter = True
        res.score = 1.0
        return res

    # Weighting: required fields count double toward the quality score.
    weight_total = 0.0
    weight_ok = 0.0

    for field in considered:
        is_required = field in required
        w = 2.0 if is_required else 1.0
        weight_total += w
        dp = points.get(field)

        if dp is None or dp.status in (DataStatus.MISSING, DataStatus.ERROR) or dp.value is None:
            if is_required:
                res.missing.append(field)
                res.sentinels[field] = (
                    DataSentinel.SOURCE_UNAVAILABLE.value
                    if (dp and dp.status == DataStatus.ERROR)
                    else DataSentinel.DATA_INCOMPLETE.value
                )
            else:
                res.sentinels[field] = DataSentinel.UNKNOWN.value
            continue

        if dp.status == DataStatus.STALE:
            if is_required:
                res.stale.append(field)
                res.sentinels[field] = DataSentinel.DATA_STALE.value
            # stale still earns partial credit
            weight_ok += w * 0.5
            continue

        # OK
        weight_ok += w

    res.score = round(weight_ok / weight_total, 4) if weight_total else 0.0

    if res.missing:
        res.reasons.append(f"Brak wymaganych danych: {', '.join(res.missing)}")
    if res.stale:
        res.reasons.append(f"Nieaktualne wymagane dane: {', '.join(res.stale)}")

    res.can_enter = (
        not res.missing
        and not res.stale
        and res.score >= config.MIN_DATA_QUALITY_FOR_ENTRY
    )
    if not res.can_enter and not res.reasons:
        res.reasons.append(
            f"Data quality {res.score:.2f} < próg {config.MIN_DATA_QUALITY_FOR_ENTRY:.2f}"
        )
    return res
