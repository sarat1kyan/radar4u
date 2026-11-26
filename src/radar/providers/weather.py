"""Daily forecast from Open-Meteo, used by the itinerary planner to flag
outdoor activities on rainy days. Free tier is non-commercial (CC BY 4.0
attribution required); commercial deployments need an Open-Meteo API plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from radar.config import get_settings
from radar.core.errors import ProviderError
from radar.domain.common import GeoPoint
from radar.providers.http import ProviderHttp

ATTRIBUTION = "Weather data by Open-Meteo.com (CC BY 4.0)"

# WMO weather interpretation codes -> coarse buckets we can translate.
_WMO: dict[int, str] = {0: "clear", 1: "clear", 2: "cloudy", 3: "cloudy", 45: "fog", 48: "fog"}
for _c in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82):
    _WMO[_c] = "rain"
for _c in (71, 73, 75, 77, 85, 86):
    _WMO[_c] = "snow"
for _c in (95, 96, 99):
    _WMO[_c] = "storm"


@dataclass(frozen=True)
class DayForecast:
    day: date
    condition: str
    t_min: float | None
    t_max: float | None
    precip_probability: int | None

    @property
    def outdoor_ok(self) -> bool | None:
        if self.condition in ("rain", "snow", "storm"):
            return False
        return not (self.precip_probability is not None and self.precip_probability >= 60)


class WeatherService:
    commercial_use_allowed = False

    def __init__(self, http: ProviderHttp | None = None) -> None:
        self.http = http or ProviderHttp("open_meteo", rate_per_s=2, burst=2, max_retries=1)

    async def daily(self, point: GeoPoint, start: date, end: date) -> list[DayForecast]:
        params = {
            "latitude": f"{point.lat:.4f}",
            "longitude": f"{point.lon:.4f}",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "timezone": "auto",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        }
        try:
            data = await self.http.get_json(f"{get_settings().open_meteo_url}/v1/forecast", params=params)
        except ProviderError:
            return []
        d = data.get("daily") or {}
        out = []
        for i, day in enumerate(d.get("time", [])):

            def pick(key: str, i: int = i) -> float | None:
                vals = d.get(key) or []
                return vals[i] if i < len(vals) else None

            code = pick("weather_code")
            prob = pick("precipitation_probability_max")
            out.append(
                DayForecast(
                    day=date.fromisoformat(day),
                    condition=_WMO.get(int(code), "unknown") if code is not None else "unknown",
                    t_min=pick("temperature_2m_min"),
                    t_max=pick("temperature_2m_max"),
                    precip_probability=int(prob) if prob is not None else None,
                )
            )
        return out

    async def aclose(self) -> None:
        await self.http.aclose()
