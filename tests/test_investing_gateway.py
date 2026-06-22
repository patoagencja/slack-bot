"""Gateway tests — retry, circuit breaker, fallback / degraded mode, TTL cache."""

import pytest

from investing import gateway
from investing.gateway import CircuitBreaker, Gateway, GatewayError, RateLimiter


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(gateway, "_SLEEP", lambda s: None)
    monkeypatch.setattr(gateway, "_RANDOM", lambda: 0.0)


def test_retry_then_success():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient")
        return 42

    gw = Gateway()
    val = gw.fetch("src", "k1", flaky, max_retries=3, ttl=0)
    assert val == 42
    assert calls["n"] == 3


def test_exhausted_retries_raises_without_fallback():
    def always_fail():
        raise RuntimeError("nope")

    gw = Gateway()
    with pytest.raises(GatewayError):
        gw.fetch("src", "k2", always_fail, max_retries=2, ttl=0)


def test_fallback_used_in_degraded_mode():
    def always_fail():
        raise RuntimeError("nope")

    gw = Gateway()
    val = gw.fetch("src", "k3", always_fail, max_retries=1, ttl=0,
                   fallback=lambda: "DEGRADED")
    assert val == "DEGRADED"


def test_circuit_breaker_opens_and_short_circuits():
    gw = Gateway()
    gw._breakers["src"] = CircuitBreaker(threshold=3, cooldown=1000)
    calls = {"n": 0}

    def fail():
        calls["n"] += 1
        raise RuntimeError("x")

    # 3 failing calls (max_retries=0) -> breaker opens
    for _ in range(3):
        with pytest.raises(GatewayError):
            gw.fetch("src", f"k{_}", fail, max_retries=0, ttl=0)
    assert gw._breakers["src"].is_open()

    before = calls["n"]
    # next call must short-circuit (fn not invoked)
    with pytest.raises(GatewayError):
        gw.fetch("src", "k_after", fail, max_retries=0, ttl=0)
    assert calls["n"] == before  # fn was NOT called


def test_circuit_breaker_recovers_after_cooldown():
    cb = CircuitBreaker(threshold=2, cooldown=0.0)
    cb.record_failure(); cb.record_failure()
    # cooldown 0 -> immediately allowed again (half-open)
    assert cb.allow()
    cb.record_success()
    assert cb.failures == 0


def test_cache_returns_without_calling_fn():
    gw = Gateway()
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return "v"

    assert gw.fetch("src", "ck", fn, ttl=100) == "v"
    assert gw.fetch("src", "ck", fn, ttl=100) == "v"
    assert calls["n"] == 1  # second call served from cache


def test_rate_limiter_blocks_excess():
    rl = RateLimiter(max_calls=2, window=1000)
    assert rl.allow() and rl.allow()
    assert not rl.allow()
