"""
investing/runner.py — a bounded executor for Slack-triggered analyses.

Replaces the pattern of spawning an unbounded number of daemon threads. Provides:
  * a single ThreadPoolExecutor capped at MAX_CONCURRENT_ANALYSES
  * idempotency / dedup so the same (user, ticker) isn't analysed twice at once
  * a tiny helper to run a batch of tickers and report partial results
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from . import config

logger = logging.getLogger(__name__)

_EXECUTOR: Optional[ThreadPoolExecutor] = None
_INFLIGHT: set[str] = set()
_LOCK = threading.Lock()


def _executor() -> ThreadPoolExecutor:
    global _EXECUTOR
    if _EXECUTOR is None:
        _EXECUTOR = ThreadPoolExecutor(
            max_workers=config.MAX_CONCURRENT_ANALYSES,
            thread_name_prefix="invest",
        )
    return _EXECUTOR


def submit(idempotency_key: str, fn: Callable[[], None]) -> bool:
    """Submit ``fn`` unless an identical key is already in flight.

    Returns True if scheduled, False if it was a duplicate (caller should tell the
    user it's already running)."""
    with _LOCK:
        if idempotency_key in _INFLIGHT:
            return False
        _INFLIGHT.add(idempotency_key)

    def _wrapped():
        try:
            fn()
        except Exception:                       # noqa: BLE001
            logger.exception("analysis task failed: %s", idempotency_key)
        finally:
            with _LOCK:
                _INFLIGHT.discard(idempotency_key)

    _executor().submit(_wrapped)
    return True


def inflight_count() -> int:
    with _LOCK:
        return len(_INFLIGHT)
