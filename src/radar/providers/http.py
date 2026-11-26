"""Outbound HTTP with the protections every provider call needs.

Per provider: a token bucket (respecting the provider's published rate policy),
a circuit breaker, and retries with exponential backoff and full jitter. Response
bodies are treated as untrusted data by callers; nothing here interprets them.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import httpx

from radar.config import get_settings
from radar.core.errors import CircuitOpen, ProviderError, ProviderRateLimited, ProviderUnavailable


class TokenBucket:
    """Async token bucket. rate = tokens per second, burst = capacity."""

    def __init__(self, rate: float, burst: int = 1) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        self.rate = rate
        self.capacity = float(max(1, burst))
        self._tokens = self.capacity
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
        self._updated = now

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                self._refill()
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                await asyncio.sleep((1 - self._tokens) / self.rate)


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    failure_threshold: int = 5
    reset_after_s: float = 60.0
    state: BreakerState = BreakerState.CLOSED
    failures: int = 0
    opened_at: float = 0.0

    def allow(self) -> bool:
        if self.state is BreakerState.OPEN:
            if time.monotonic() - self.opened_at >= self.reset_after_s:
                self.state = BreakerState.HALF_OPEN
                return True
            return False
        return True

    def record_success(self) -> None:
        self.failures = 0
        self.state = BreakerState.CLOSED

    def record_failure(self) -> None:
        self.failures += 1
        if self.state is BreakerState.HALF_OPEN or self.failures >= self.failure_threshold:
            self.state = BreakerState.OPEN
            self.opened_at = time.monotonic()


def backoff_delay(attempt: int, base: float = 0.5, cap: float = 20.0) -> float:
    """Full-jitter exponential backoff (AWS architecture blog variant)."""
    return random.uniform(0, min(cap, base * (2**attempt)))  # noqa: S311 - not crypto


@dataclass
class ProviderStats:
    calls: int = 0
    failures: int = 0
    total_latency_ms: float = 0.0
    last_error: str | None = None
    last_ok_at: float | None = None

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / self.calls if self.calls else 0.0


@dataclass
class ProviderHttp:
    provider: str
    rate_per_s: float = 5.0
    burst: int = 5
    max_retries: int = 2
    timeout_s: float | None = None
    connect_timeout_s: float = 6.0
    headers: Mapping[str, str] = field(default_factory=dict)
    client: httpx.AsyncClient | None = None

    def __post_init__(self) -> None:
        self.bucket = TokenBucket(self.rate_per_s, self.burst)
        self.breaker = CircuitBreaker()
        self.stats = ProviderStats()

    def _client(self) -> httpx.AsyncClient:
        if self.client is None:
            s = get_settings()
            self.client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_s or s.http_timeout_s, connect=self.connect_timeout_s),
                headers={"User-Agent": s.http_user_agent, **dict(self.headers)},
                follow_redirects=True,
            )
        return self.client

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        data: Any = None,
        headers: Mapping[str, str] | None = None,
        retry_on_post: bool = False,
    ) -> httpx.Response:
        if not self.breaker.allow():
            raise CircuitOpen(self.provider, "temporarily disabled after repeated failures")
        idempotent = method.upper() in {"GET", "HEAD"} or retry_on_post
        attempts = self.max_retries + 1 if idempotent else 1
        last_exc: Exception | None = None
        for attempt in range(attempts):
            await self.bucket.acquire()
            started = time.monotonic()
            try:
                resp = await self._client().request(method, url, params=params, json=json, data=data, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                self._fail(f"transport: {type(exc).__name__}")
            else:
                self.stats.calls += 1
                self.stats.total_latency_ms += (time.monotonic() - started) * 1000
                if resp.status_code == 429:
                    retry_after = _retry_after(resp)
                    self._fail("429")
                    if attempt + 1 < attempts:
                        await asyncio.sleep(min(retry_after or backoff_delay(attempt), 30.0))
                        continue
                    raise ProviderRateLimited(self.provider, retry_after)
                if resp.status_code >= 500:
                    last_exc = ProviderUnavailable(self.provider, f"upstream status {resp.status_code}", retryable=True)
                    self._fail(str(resp.status_code))
                elif resp.status_code >= 400:
                    # Client errors are our fault or a credentials issue; do not trip the breaker.
                    self.stats.failures += 1
                    self.stats.last_error = str(resp.status_code)
                    raise ProviderError(
                        self.provider, f"upstream rejected request ({resp.status_code})", code="bad_request"
                    )
                else:
                    self.breaker.record_success()
                    self.stats.last_ok_at = time.time()
                    return resp
            if attempt + 1 < attempts:
                await asyncio.sleep(backoff_delay(attempt))
        if isinstance(last_exc, ProviderError):
            raise last_exc
        raise ProviderUnavailable(self.provider, "upstream unreachable", retryable=True) from last_exc

    def _fail(self, reason: str) -> None:
        self.stats.failures += 1
        self.stats.last_error = reason
        self.breaker.record_failure()

    async def get_json(self, url: str, **kw: Any) -> Any:
        resp = await self.request("GET", url, **kw)
        try:
            return resp.json()
        except ValueError as exc:
            raise ProviderError(self.provider, "invalid JSON from upstream") from exc

    async def aclose(self) -> None:
        if self.client is not None:
            await self.client.aclose()
            self.client = None


def _retry_after(resp: httpx.Response) -> float | None:
    value = resp.headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None
