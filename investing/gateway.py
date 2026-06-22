"""
investing/gateway.py — one central data gateway for every external source.

Provides, per source: retry with exponential backoff + jitter, soft timeout,
circuit breaker, a simple rate-limit budget, a TTL cache (in-memory + optional
cross-process via persistence.api_cache), a fallback hook and an explicit
degraded mode. Providers never call yfinance/FRED/Tavily directly — they go
through :func:`fetch`.
"""

from __future__ import annotations

import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from . import config

# Injectable for tests (avoid real sleeping / randomness).
_SLEEP: Callable[[float], None] = time.sleep
_RANDOM: Callable[[], float] = random.random


class CircuitOpen(RuntimeError):
    pass


class GatewayError(RuntimeError):
    pass


@dataclass
class CircuitBreaker:
    threshold: int
    cooldown: float
    failures: int = 0
    opened_at: Optional[float] = None
    _now: Callable[[], float] = time.monotonic

    def allow(self) -> bool:
        if self.opened_at is None:
            return True
        if self._now() - self.opened_at >= self.cooldown:
            return True              # half-open: allow a trial
        return False

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = self._now()

    def is_open(self) -> bool:
        return not self.allow()


@dataclass
class RateLimiter:
    max_calls: int
    window: float = 60.0
    _calls: list = field(default_factory=list)
    _now: Callable[[], float] = time.monotonic

    def allow(self) -> bool:
        now = self._now()
        self._calls = [t for t in self._calls if now - t < self.window]
        if len(self._calls) >= self.max_calls:
            return False
        self._calls.append(now)
        return True


class Gateway:
    def __init__(self) -> None:
        self._breakers: dict[str, CircuitBreaker] = {}
        self._limiters: dict[str, RateLimiter] = {}
        self._cache: dict[str, tuple[float, Any]] = {}     # key -> (expiry_monotonic, value)
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="gw")

    def breaker(self, source: str) -> CircuitBreaker:
        with self._lock:
            if source not in self._breakers:
                self._breakers[source] = CircuitBreaker(
                    threshold=config.GATEWAY["cb_threshold"],
                    cooldown=config.GATEWAY["cb_cooldown"],
                )
            return self._breakers[source]

    def limiter(self, source: str, max_calls: int = 60) -> RateLimiter:
        with self._lock:
            if source not in self._limiters:
                self._limiters[source] = RateLimiter(max_calls=max_calls)
            return self._limiters[source]

    # ── cache ──
    def _cache_get(self, key: str) -> tuple[bool, Any]:
        with self._lock:
            item = self._cache.get(key)
        if item and item[0] > time.monotonic():
            return True, item[1]
        return False, None

    def _cache_set(self, key: str, value: Any, ttl: float) -> None:
        with self._lock:
            self._cache[key] = (time.monotonic() + ttl, value)

    def fetch(
        self,
        source: str,
        key: str,
        fn: Callable[[], Any],
        *,
        kind: str = "default",
        ttl: Optional[float] = None,
        timeout: Optional[float] = None,
        fallback: Optional[Callable[[], Any]] = None,
        max_retries: Optional[int] = None,
        rate_limit: int = 60,
    ) -> Any:
        """Fetch through the gateway. Returns the value, or the fallback's value in
        degraded mode. Raises GatewayError only when there is no fallback."""
        ttl = ttl if ttl is not None else config.TTL.get(kind, {"fresh": 60})["fresh"]
        timeout = timeout if timeout is not None else config.GATEWAY["timeout"]
        max_retries = max_retries if max_retries is not None else config.GATEWAY["max_retries"]

        hit, val = self._cache_get(key)
        if hit:
            return val

        breaker = self.breaker(source)
        if breaker.is_open():
            return self._degraded(source, key, fallback, "circuit open")

        limiter = self.limiter(source, rate_limit)
        if not limiter.allow():
            return self._degraded(source, key, fallback, "rate limit exceeded")

        last_exc: Optional[Exception] = None
        for attempt in range(max_retries + 1):
            try:
                value = self._with_timeout(fn, timeout)
                breaker.record_success()
                self._cache_set(key, value, ttl)
                return value
            except Exception as exc:           # noqa: BLE001 — gateway boundary
                last_exc = exc
                breaker.record_failure()
                if breaker.is_open():
                    break
                if attempt < max_retries:
                    self._backoff(attempt)
        return self._degraded(source, key, fallback, f"failed after retries: {last_exc}")

    def _with_timeout(self, fn: Callable[[], Any], timeout: float) -> Any:
        fut = self._executor.submit(fn)
        try:
            return fut.result(timeout=timeout)
        except FutureTimeout as e:
            fut.cancel()
            raise TimeoutError(f"timeout after {timeout}s") from e

    def _backoff(self, attempt: int) -> None:
        base = config.GATEWAY["base_backoff"]
        cap = config.GATEWAY["max_backoff"]
        delay = min(cap, base * (2 ** attempt))
        delay += _RANDOM() * config.GATEWAY["jitter"]
        _SLEEP(delay)

    def _degraded(self, source: str, key: str, fallback: Optional[Callable[[], Any]], why: str) -> Any:
        if fallback is not None:
            try:
                return fallback()
            except Exception as e:  # noqa: BLE001
                raise GatewayError(f"{source}:{key} degraded ({why}); fallback failed: {e}") from e
        raise GatewayError(f"{source}:{key} degraded: {why}")


# Module-level singleton.
_GATEWAY: Optional[Gateway] = None


def gateway() -> Gateway:
    global _GATEWAY
    if _GATEWAY is None:
        _GATEWAY = Gateway()
    return _GATEWAY


def fetch(source: str, key: str, fn: Callable[[], Any], **kw) -> Any:
    return gateway().fetch(source, key, fn, **kw)
