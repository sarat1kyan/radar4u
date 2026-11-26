import httpx
import pytest
import respx

from radar.core.errors import CircuitOpen, ProviderRateLimited, ProviderUnavailable
from radar.providers.http import BreakerState, CircuitBreaker, ProviderHttp, backoff_delay


def test_backoff_is_bounded():
    for attempt in range(10):
        assert 0 <= backoff_delay(attempt, base=0.5, cap=4) <= 4


def test_breaker_opens_and_half_opens():
    b = CircuitBreaker(failure_threshold=2, reset_after_s=0)
    b.record_failure()
    b.record_failure()
    assert b.state is BreakerState.OPEN
    assert b.allow() and b.state is BreakerState.HALF_OPEN
    b.record_failure()
    assert b.state is BreakerState.OPEN


@respx.mock
async def test_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr("radar.providers.http.backoff_delay", lambda *a, **k: 0)
    route = respx.get("https://x.test/a").mock(side_effect=[httpx.Response(503), httpx.Response(200, json={"ok": 1})])
    h = ProviderHttp("t", rate_per_s=100, burst=10, max_retries=2)
    assert await h.get_json("https://x.test/a") == {"ok": 1}
    assert route.call_count == 2
    await h.aclose()


@respx.mock
async def test_rate_limited_raises(monkeypatch):
    monkeypatch.setattr("radar.providers.http.asyncio.sleep", _nosleep)
    respx.get("https://x.test/b").mock(return_value=httpx.Response(429, headers={"retry-after": "1"}))
    h = ProviderHttp("t", rate_per_s=100, burst=10, max_retries=1)
    with pytest.raises(ProviderRateLimited):
        await h.get_json("https://x.test/b")
    await h.aclose()


@respx.mock
async def test_breaker_blocks_after_failures(monkeypatch):
    monkeypatch.setattr("radar.providers.http.asyncio.sleep", _nosleep)
    respx.get("https://x.test/c").mock(return_value=httpx.Response(500))
    h = ProviderHttp("t", rate_per_s=100, burst=10, max_retries=0)
    h.breaker.failure_threshold = 2
    for _ in range(2):
        with pytest.raises(ProviderUnavailable):
            await h.get_json("https://x.test/c")
    with pytest.raises(CircuitOpen):
        await h.get_json("https://x.test/c")
    await h.aclose()


async def _nosleep(_s):
    return None
