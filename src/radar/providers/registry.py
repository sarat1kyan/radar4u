"""Capability registry: which providers can answer which request, where.

Selection is deterministic: domain + country + category coverage, credentials
present, commercial-use terms respected. When nothing is eligible the
registry explains why (no provider for the domain, not configured, not
licensed for commercial use, outside coverage) so the user gets an honest
coverage message instead of an empty list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from radar.config import Settings, get_settings
from radar.domain.common import Domain
from radar.domain.intents import SearchIntent
from radar.providers.base import AccessLevel, Provider


@dataclass
class Eligibility:
    providers: list[Provider] = field(default_factory=list)
    # provider name -> reason it was skipped
    skipped: dict[str, str] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not self.providers


# What exists in the market for each domain but needs a commercial agreement.
# Shown in coverage messages and the provider matrix; never queried.
PARTNER_ONLY: dict[Domain, list[str]] = {
    Domain.DINING: ["OpenTable", "TheFork", "SevenRooms"],
    Domain.STAY: ["Booking.com Demand API", "Expedia Rapid"],
    Domain.RAIL: ["Trainline Partner Solutions", "Rail Europe", "SNCF Connect"],
    Domain.REAL_ESTATE: ["List.am", "Myhome.ge", "SS.ge", "Krisha.kz"],
}


class ProviderRegistry:
    def __init__(self, providers: list[Provider], settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.providers = providers

    def get(self, name: str) -> Provider | None:
        return next((p for p in self.providers if p.name == name), None)

    def _commercial_ok(self, p: Provider) -> bool:
        if not self.settings.commercial or p.commercial_use_allowed:
            return True
        if p.name == "osm" and self.settings.overpass_self_hosted:
            return True
        return p.name == "transitous" and self.settings.transitous_self_hosted

    def eligible(self, intent: SearchIntent, *, monitoring: bool = False) -> Eligibility:
        ref = intent.primary_location()
        country = ref.place.country_code if ref and ref.place else None
        result = Eligibility()
        for p in self.providers:
            caps = [c for c in p.capabilities if c.domain is intent.domain]
            if not caps:
                continue
            if p.access is AccessLevel.FIXTURE and not self.settings.fixtures_allowed:
                result.skipped[p.name] = "fixtures_disabled"
                continue
            if not p.configured():
                result.skipped[p.name] = "not_configured"
                continue
            if not self._commercial_ok(p):
                result.skipped[p.name] = "non_commercial_terms"
                continue
            covering = [c for c in caps if c.covers(intent.domain, country, intent.categories)]
            if not covering:
                result.skipped[p.name] = "outside_coverage"
                continue
            if monitoring and not any(c.monitoring_allowed for c in covering):
                result.skipped[p.name] = "monitoring_not_permitted"
                continue
            result.providers.append(p)
        # A live source exists: don't pad results with demo data.
        if any(p.access is not AccessLevel.FIXTURE for p in result.providers):
            result.providers = [p for p in result.providers if p.access is not AccessLevel.FIXTURE]
        return result

    def min_interval_s(self, intent: SearchIntent) -> int:
        intervals = [
            c.min_monitor_interval_s
            for p in self.eligible(intent, monitoring=True).providers
            for c in p.capabilities
            if c.domain is intent.domain and c.monitoring_allowed
        ]
        return max([self.settings.min_radar_interval_s, *intervals]) if intervals else 0

    def capability_matrix(self) -> list[dict[str, Any]]:
        rows = [p.describe() for p in self.providers]
        for domain, names in PARTNER_ONLY.items():
            for n in names:
                rows.append(
                    {
                        "name": n,
                        "display_name": n,
                        "access": AccessLevel.PARTNER.value,
                        "configured": False,
                        "domains": [domain.value],
                        "booking": False,
                        "monitoring": False,
                        "commercial_use_allowed": True,
                        "docs_url": None,
                    }
                )
        return rows

    async def aclose(self) -> None:
        for p in self.providers:
            await p.aclose()


def build_registry(settings: Settings | None = None) -> ProviderRegistry:
    from radar.providers.duffel import DuffelProvider
    from radar.providers.feeds import ListingFeedProvider, load_feed_configs
    from radar.providers.fixtures import FixtureProvider
    from radar.providers.liteapi import LiteApiProvider
    from radar.providers.osm_places import OsmPlacesProvider
    from radar.providers.ticketmaster import TicketmasterProvider
    from radar.providers.transitous import TransitousProvider
    from radar.providers.travelpayouts import TravelpayoutsProvider

    s = settings or get_settings()
    providers: list[Provider] = [
        OsmPlacesProvider(),
        TransitousProvider(),
        DuffelProvider(),
        TravelpayoutsProvider(),
        LiteApiProvider(),
        TicketmasterProvider(),
    ]
    providers += [ListingFeedProvider(cfg) for cfg in load_feed_configs()]
    if s.fixtures_allowed:
        providers.append(FixtureProvider())
    return ProviderRegistry(providers, s)
