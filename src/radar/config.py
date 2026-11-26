"""Runtime configuration.

Everything comes from environment variables (optionally a local .env file that is
never committed). Provider credentials are optional: an adapter without its
credentials reports itself as unconfigured and is skipped by the registry.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    env: Literal["dev", "test", "prod"] = Field("dev", alias="RADAR_ENV")
    log_level: str = Field("INFO", alias="RADAR_LOG_LEVEL")

    # Telegram. BOT_API is kept as the variable name for the bot token.
    bot_token: SecretStr | None = Field(None, alias="BOT_API")
    webapp_url: str | None = Field(None, alias="RADAR_WEBAPP_URL")
    # Mini App initData older than this is rejected.
    webapp_auth_max_age_s: int = Field(86400, alias="RADAR_WEBAPP_AUTH_MAX_AGE")

    database_url: str = Field("sqlite+aiosqlite:///./radar.db", alias="RADAR_DATABASE_URL")
    redis_url: str | None = Field(None, alias="RADAR_REDIS_URL")

    api_host: str = Field("0.0.0.0", alias="RADAR_API_HOST")  # noqa: S104 - container default
    api_port: int = Field(8080, alias="RADAR_API_PORT")

    # Identification sent to public OSM-family services, as their usage policies require.
    http_user_agent: str = Field("radar4u/0.1 (+https://github.com/sarat1kyan/radar4u)", alias="RADAR_HTTP_USER_AGENT")
    http_timeout_s: float = Field(20.0, alias="RADAR_HTTP_TIMEOUT")

    # Public data services (overridable for self-hosting).
    nominatim_url: str = Field("https://nominatim.openstreetmap.org", alias="RADAR_NOMINATIM_URL")
    photon_url: str = Field("https://photon.komoot.io", alias="RADAR_PHOTON_URL")
    overpass_urls: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "https://overpass-api.de/api/interpreter",
            "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
            "https://overpass.private.coffee/api/interpreter",
        ],
        alias="RADAR_OVERPASS_URLS",
    )
    transitous_url: str = Field("https://api.transitous.org", alias="RADAR_TRANSITOUS_URL")
    open_meteo_url: str = Field("https://api.open-meteo.com", alias="RADAR_OPEN_METEO_URL")
    fx_ecb_url: str = Field("https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml", alias="RADAR_FX_ECB_URL")
    fx_open_er_url: str = Field("https://open.er-api.com/v6/latest", alias="RADAR_FX_OPEN_ER_URL")

    # Commercial providers. All optional.
    duffel_token: SecretStr | None = Field(None, alias="DUFFEL_ACCESS_TOKEN")
    travelpayouts_token: SecretStr | None = Field(None, alias="TRAVELPAYOUTS_TOKEN")
    liteapi_key: SecretStr | None = Field(None, alias="LITEAPI_KEY")
    ticketmaster_key: SecretStr | None = Field(None, alias="TICKETMASTER_API_KEY")

    # Language understanding. "rules" works offline; LLM backends are optional.
    nlu_backend: Literal["rules", "anthropic", "openai"] = Field("rules", alias="RADAR_NLU_BACKEND")
    anthropic_api_key: SecretStr | None = Field(None, alias="RADAR_ANTHROPIC_API_KEY")
    anthropic_model: str = Field("claude-opus-5-5", alias="RADAR_ANTHROPIC_MODEL")
    openai_api_key: SecretStr | None = Field(None, alias="RADAR_OPENAI_API_KEY")
    openai_base_url: str = Field("https://api.openai.com/v1", alias="RADAR_OPENAI_BASE_URL")
    openai_model: str | None = Field(None, alias="RADAR_OPENAI_MODEL")

    # Set when RADAR earns money (ads, subscriptions, commissions). Providers whose free tier
    # is non-commercial are then skipped unless their URL points at a self-hosted instance.
    commercial: bool = Field(False, alias="RADAR_COMMERCIAL")
    overpass_self_hosted: bool = Field(False, alias="RADAR_OVERPASS_SELF_HOSTED")
    transitous_self_hosted: bool = Field(False, alias="RADAR_TRANSITOUS_SELF_HOSTED")

    # Development fixtures. Never enabled in prod; results are always labeled.
    enable_fixtures: bool = Field(False, alias="RADAR_ENABLE_FIXTURES")

    # Monitoring engine.
    monitor_tick_s: int = Field(30, alias="RADAR_MONITOR_TICK")
    monitor_batch: int = Field(20, alias="RADAR_MONITOR_BATCH")
    max_radars_per_user: int = Field(10, alias="RADAR_MAX_RADARS_PER_USER")
    min_radar_interval_s: int = Field(1800, alias="RADAR_MIN_RADAR_INTERVAL")

    search_timeout_s: float = Field(25.0, alias="RADAR_SEARCH_TIMEOUT")
    user_rate_per_minute: int = Field(20, alias="RADAR_USER_RATE_PER_MINUTE")

    @field_validator("overpass_urls", mode="before")
    @classmethod
    def _split_urls(cls, v: object) -> object:
        if isinstance(v, str):
            return [u.strip() for u in v.split(",") if u.strip()]
        return v

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"

    @property
    def fixtures_allowed(self) -> bool:
        return self.enable_fixtures and not self.is_prod


@lru_cache
def get_settings() -> Settings:
    return Settings()
