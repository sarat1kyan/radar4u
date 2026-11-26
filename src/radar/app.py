"""Composition root: builds every service once and hands them to the bot,
the API and the worker. Nothing else constructs infrastructure."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from radar.booking.service import BookingService
from radar.config import Settings, get_settings
from radar.geo.resolver import GeoResolver
from radar.itinerary.planner import Planner
from radar.monitoring.engine import RadarEngine, RadarService
from radar.nlu.interpreter import Interpreter
from radar.providers.fx import FxService
from radar.providers.registry import ProviderRegistry, build_registry
from radar.providers.weather import WeatherService
from radar.search.pipeline import SearchPipeline
from radar.storage.db import Database


@dataclass
class App:
    settings: Settings
    db: Database
    resolver: GeoResolver
    interpreter: Interpreter
    registry: ProviderRegistry
    fx: FxService
    weather: WeatherService
    pipeline: SearchPipeline
    radars: RadarService
    engine: RadarEngine
    bookings: BookingService
    planner: Planner

    async def close(self) -> None:
        await self.registry.aclose()
        await self.resolver.aclose()
        await self.fx.aclose()
        await self.weather.aclose()
        await self.db.dispose()


def configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs full request URLs at INFO, which would include the bot token and API keys.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)


def build_app(settings: Settings | None = None, registry: ProviderRegistry | None = None) -> App:
    s = settings or get_settings()
    db = Database(s.database_url)
    resolver = GeoResolver()
    registry = registry or build_registry(s)
    fx = FxService()
    weather = WeatherService()
    pipeline = SearchPipeline(registry, fx, timeout_s=s.search_timeout_s)
    return App(
        settings=s,
        db=db,
        resolver=resolver,
        interpreter=Interpreter(resolver),
        registry=registry,
        fx=fx,
        weather=weather,
        pipeline=pipeline,
        radars=RadarService(db, pipeline, s),
        engine=RadarEngine(db, pipeline, s),
        bookings=BookingService(db, registry),
        planner=Planner(pipeline, weather),
    )
