from __future__ import annotations


class RadarError(Exception):
    """Base for errors that are safe to describe to a user."""

    code = "error"

    def __init__(self, message: str = "", *, code: str | None = None) -> None:
        super().__init__(message or self.__class__.__name__)
        if code:
            self.code = code


class ProviderError(RadarError):
    code = "provider_error"

    def __init__(self, provider: str, message: str, *, retryable: bool = False, code: str | None = None):
        super().__init__(message, code=code)
        self.provider = provider
        self.retryable = retryable


class ProviderUnavailable(ProviderError):
    code = "provider_unavailable"


class ProviderRateLimited(ProviderError):
    code = "rate_limited"

    def __init__(self, provider: str, retry_after_s: float | None = None):
        super().__init__(provider, "rate limited", retryable=True)
        self.retry_after_s = retry_after_s


class CircuitOpen(ProviderError):
    code = "circuit_open"


class NotConfigured(ProviderError):
    code = "not_configured"


class ValidationFailed(RadarError):
    code = "validation_failed"


class QuotaExceeded(RadarError):
    code = "quota_exceeded"


class BookingError(RadarError):
    code = "booking_error"


class PriceChanged(BookingError):
    code = "price_changed"
