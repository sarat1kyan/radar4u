"""Travel mode: what someone standing in an unfamiliar city needs first.

Everything here is assembled from sources that are already part of RADAR
(reverse geocoding, Open-Meteo, the FX service, OpenStreetMap, Transitous)
plus two small static tables that are easy to audit: emergency numbers and
country currencies (the latter from CLDR via Babel).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime

from babel.numbers import get_territory_currencies

from radar.core.money import FxRate
from radar.core.timeutil import now_in
from radar.domain.common import Place
from radar.providers.fx import FxService
from radar.providers.weather import CurrentWeather, WeatherService

# Unified and service numbers. Sources: national government sites and the EU 112 list.
# Only countries we have checked are listed; anything else shows the GSM-wide 112 hint.
EMERGENCY: dict[str, dict[str, str]] = {
    # EU / EEA / UK / CH: 112 everywhere
    **{
        cc: {"all": "112"}
        for cc in (
            *("AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV"),
            *("LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE", "IS", "LI", "NO", "CH"),
        )
    },
    "GB": {"all": "999", "alt": "112"},
    "AM": {"all": "911", "alt": "112"},
    "GE": {"all": "112"},
    "KZ": {"all": "112", "police": "102", "ambulance": "103"},
    "AZ": {"all": "112", "police": "102", "ambulance": "103"},
    "RU": {"all": "112", "police": "102", "ambulance": "103"},
    "BY": {"all": "112", "police": "102", "ambulance": "103"},
    "UA": {"all": "112", "police": "102", "ambulance": "103"},
    "UZ": {"all": "112", "police": "102", "ambulance": "103"},
    "KG": {"all": "112", "police": "102", "ambulance": "103"},
    "TR": {"all": "112"},
    "RS": {"police": "192", "ambulance": "194", "alt": "112"},
    "ME": {"all": "112"},
    "AL": {"all": "112"},
    "MD": {"all": "112"},
    "US": {"all": "911"},
    "CA": {"all": "911"},
    "MX": {"all": "911"},
    "AE": {"police": "999", "ambulance": "998"},
    "IL": {"police": "100", "ambulance": "101"},
    "EG": {"police": "122", "ambulance": "123"},
    "TH": {"police": "191", "ambulance": "1669", "alt": "1155"},
    "JP": {"police": "110", "ambulance": "119"},
    "KR": {"police": "112", "ambulance": "119"},
    "CN": {"police": "110", "ambulance": "120"},
    "IN": {"all": "112"},
    "AU": {"all": "000", "alt": "112"},
    "NZ": {"all": "111"},
}


def emergency(cc: str | None) -> dict[str, str] | None:
    return EMERGENCY.get((cc or "").upper())


def emergency_primary(cc: str | None) -> str:
    e = emergency(cc) or {}
    return e.get("all") or e.get("police") or "112"


def country_currency(cc: str | None) -> str | None:
    if not cc:
        return None
    try:
        cur = get_territory_currencies(cc.upper(), tender=True, non_tender=False)
    except Exception:  # unknown territory codes raise various Babel errors
        return None
    return cur[0] if cur else None


@dataclass
class Snapshot:
    place: Place
    local_time: datetime
    offset_h: float | None  # local minus home, hours
    weather: CurrentWeather | None = None
    currency: str | None = None
    rate: FxRate | None = None  # 1 user currency = rate local
    emergency: dict[str, str] | None = None
    notes: list[str] = field(default_factory=list)


async def snapshot(
    place: Place,
    *,
    home_tz: str,
    user_currency: str,
    weather: WeatherService,
    fx: FxService,
    timeout_s: float = 6.0,
) -> Snapshot:
    zone = place.timezone or home_tz
    local = now_in(zone)
    home = now_in(home_tz)
    lo, ho = local.utcoffset(), home.utcoffset()
    offset = (lo - ho).total_seconds() / 3600 if lo is not None and ho is not None else None
    snap = Snapshot(place=place, local_time=local, offset_h=offset, emergency=emergency(place.country_code))
    snap.currency = country_currency(place.country_code)

    async def _wx() -> None:
        snap.weather = await weather.current(place.point)

    async def _fx() -> None:
        if snap.currency and snap.currency != user_currency.upper():
            snap.rate = await fx.rate(user_currency, snap.currency)

    try:
        await asyncio.wait_for(asyncio.gather(_wx(), _fx(), return_exceptions=True), timeout_s)
    except TimeoutError:
        snap.notes.append("timeout")
    return snap
