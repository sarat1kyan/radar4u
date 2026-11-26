"""Provider adapter contract and capability descriptors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from radar.core.errors import NotConfigured
from radar.domain.common import Domain
from radar.domain.intents import SearchIntent
from radar.domain.results import Result


class AccessLevel(StrEnum):
    # Public endpoint, usable within its published policy without an account.
    PUBLIC = "public"
    # Self-serve API key / token from the provider.
    SELF_SERVE_KEY = "self_serve_key"
    # Sandbox self-serve, production needs approval.
    SANDBOX = "sandbox"
    # Commercial agreement required; adapter contract only.
    PARTNER = "partner"
    # Clearly labeled development data.
    FIXTURE = "fixture"


@dataclass(frozen=True, slots=True)
class Capability:
    domain: Domain
    # Category tokens this capability can answer (empty = whole domain).
    categories: frozenset[str] = frozenset()
    # ISO country codes; None = global coverage claimed by the provider.
    countries: frozenset[str] | None = None
    supports_booking: bool = False
    supports_revalidation: bool = False
    # Provider terms allow scheduled repeated queries (radars).
    monitoring_allowed: bool = False
    min_monitor_interval_s: int = 3600
    # Rough relative cost per call, used for cost-aware monitoring.
    cost_units: float = 1.0

    def covers(self, domain: Domain, country: str | None, categories: list[str]) -> bool:
        if domain is not self.domain:
            return False
        if self.countries is not None and (country is None or country.upper() not in self.countries):
            return False
        if self.categories and categories:
            return any(c in self.categories or c.split(":")[0] + ":*" in self.categories for c in categories)
        return True


@dataclass
class SearchContext:
    user_id: int | None = None
    locale: str = "en"
    currency: str = "EUR"
    deadline: datetime | None = None
    max_results: int = 30
    # Set by the monitoring engine so adapters can choose cheaper paths.
    monitoring: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


class Provider(ABC):
    name: str = "provider"
    display_name: str = "Provider"
    access: AccessLevel = AccessLevel.PUBLIC
    # False when the provider's terms forbid commercial use of its free tier.
    commercial_use_allowed: bool = True
    docs_url: str | None = None
    attribution: str | None = None
    capabilities: tuple[Capability, ...] = ()

    def configured(self) -> bool:
        return True

    def ensure_configured(self) -> None:
        if not self.configured():
            raise NotConfigured(self.name, f"{self.display_name} credentials are not configured")

    @abstractmethod
    async def search(self, intent: SearchIntent, ctx: SearchContext) -> list[Result]: ...

    async def revalidate(self, result: Result, ctx: SearchContext) -> Result:
        """Re-fetch a single offer right before booking. Default: unsupported."""
        raise NotImplementedError

    async def aclose(self) -> None:  # noqa: B027 - optional hook
        pass

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "access": self.access.value,
            "configured": self.configured(),
            "docs_url": self.docs_url,
            "domains": sorted({c.domain.value for c in self.capabilities}),
            "booking": any(c.supports_booking for c in self.capabilities),
            "monitoring": any(c.monitoring_allowed for c in self.capabilities),
            "commercial_use_allowed": self.commercial_use_allowed,
        }
