"""HTTP API for the Mini App, health/metrics endpoints and the optional
Telegram webhook."""

from __future__ import annotations

import hmac
import logging
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from datetime import time as dtime
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import text

from radar import __version__, i18n
from radar.api.auth import InitDataError, WebAppUser, validate_init_data
from radar.api.serialize import outcome_view
from radar.app import App, build_app
from radar.bot import render
from radar.core.errors import QuotaExceeded, ValidationFailed
from radar.core.money import Money, MoneyError
from radar.core.timeutil import aware
from radar.domain.common import Domain, Place
from radar.domain.intents import MonitorKind, MonitorSpec, SearchIntent
from radar.monitoring.engine import RadarDraft
from radar.nlu.interpreter import UserContext
from radar.nlu.rules import _required_missing
from radar.providers.base import SearchContext
from radar.search.draft import CATEGORIES, FILTERS, GRID, SORTS, STEPS, build_intent
from radar.search.metrics import METRICS
from radar.storage import repo
from radar.storage.models import Itinerary as ItineraryRow
from radar.storage.models import User

log = logging.getLogger(__name__)
WEBAPP_DIR = Path(__file__).resolve().parent.parent / "webapp"


class SearchBody(BaseModel):
    text: str | None = Field(None, min_length=1, max_length=2000)
    draft: dict[str, Any] | None = None
    refine: bool = True


class RadarBody(BaseModel):
    session_id: int
    kind: MonitorKind
    threshold: str | None = Field(None, max_length=40)
    currency: str | None = Field(None, min_length=3, max_length=3)


class RadarPatch(BaseModel):
    state: str = Field(..., pattern="^(active|paused)$")


class MePatch(BaseModel):
    language: str | None = Field(None, max_length=8)
    currency: str | None = Field(None, min_length=3, max_length=3)
    time_format: str | None = Field(None, pattern="^(24h|12h)$")


def _clean_draft(d: dict[str, Any]) -> dict[str, Any]:
    """Only known keys with sane types; places are re-validated as Place objects."""
    out: dict[str, Any] = {"domain": Domain(str(d["domain"])).value}
    for k in ("cat", "day", "time", "sort"):
        if isinstance(d.get(k), str) and len(d[k]) <= 40:
            out[k] = d[k]
    for k in ("pax", "nights", "rooms"):
        if isinstance(d.get(k), int) and 0 < d[k] <= 60:
            out[k] = d[k]
    if isinstance(d.get("flags"), list):
        out["flags"] = [f for f in d["flags"] if isinstance(f, str)][:20]
    for k in ("city", "origin", "dest"):
        if isinstance(d.get(k), dict):
            out[k] = Place.model_validate(d[k]).model_dump(mode="json")
    if out.get("day"):
        date.fromisoformat(out["day"])
    if out.get("time"):
        dtime.fromisoformat(out["time"])
    return out


def create_app(app_ctx: App | None = None) -> FastAPI:
    holder: dict[str, App] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        a = app_ctx or build_app()
        holder["app"] = a
        if not a.db.is_postgres:
            await a.db.create_all()
        yield
        if app_ctx is None:
            await a.close()

    api = FastAPI(
        title="RADAR", version=__version__, lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json"
    )
    hits: dict[int, list[float]] = {}

    def get_app() -> App:
        return holder["app"]

    async def current_user(authorization: Annotated[str | None, Header()] = None, a: App = Depends(get_app)) -> User:
        token = a.settings.bot_token
        if token is None:
            raise HTTPException(503, "bot token not configured")
        if not authorization or not authorization.startswith("tma "):
            raise HTTPException(401, "missing Telegram init data")
        try:
            wu: WebAppUser = validate_init_data(
                authorization[4:], token.get_secret_value(), a.settings.webapp_auth_max_age_s
            )
        except InitDataError as exc:
            raise HTTPException(401, str(exc)) from exc
        now = time.monotonic()
        window = [t for t in hits.get(wu.id, []) if now - t < 60]
        if len(window) >= a.settings.user_rate_per_minute:
            raise HTTPException(429, "slow down")
        hits[wu.id] = [*window, now]
        async with a.db.session() as s:
            return await repo.get_or_create_user(s, wu.id, i18n.normalize_lang(wu.language_code))

    @api.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Response:
        resp: Response = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path.startswith("/app"):
            resp.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self' https://telegram.org https://unpkg.com; "
                "style-src 'self' 'unsafe-inline' https://unpkg.com; "
                "img-src 'self' data: https:; connect-src 'self'; frame-ancestors https://web.telegram.org "
                "https://*.telegram.org",
            )
        return resp

    @api.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @api.get("/readyz")
    async def readyz(a: App = Depends(get_app)) -> dict[str, Any]:
        async with a.db.session() as s:
            await s.execute(text("SELECT 1"))
        return {"status": "ready", "providers": {p.name: p.configured() for p in a.registry.providers}}

    @api.get("/metrics", response_class=PlainTextResponse)
    async def metrics() -> str:
        return METRICS.render()

    @api.get("/api/v1/capabilities")
    async def capabilities(a: App = Depends(get_app)) -> dict[str, Any]:
        return {"providers": a.registry.capability_matrix(), "languages": i18n.available()}

    @api.get("/api/v1/i18n/{lang}")
    async def ui_strings(lang: str) -> dict[str, str]:
        code = i18n.normalize_lang(lang)
        return {**i18n.catalog("en"), **i18n.catalog(code)}

    @api.get("/api/v1/me")
    async def me(user: User = Depends(current_user)) -> dict[str, Any]:
        home = repo.user_home(user)
        return {
            "language": user.language,
            "currency": user.currency,
            "time_format": user.time_format,
            "home": home.model_dump(mode="json") if home else None,
            "languages": i18n.available(),
        }

    @api.patch("/api/v1/me")
    async def patch_me(body: MePatch, user: User = Depends(current_user), a: App = Depends(get_app)) -> dict[str, str]:
        async with a.db.session() as s:
            u = await s.get(User, user.id)
            assert u is not None
            if body.language:
                u.language = i18n.normalize_lang(body.language)
            if body.currency:
                u.currency = body.currency.upper()
            if body.time_format:
                u.time_format = body.time_format
        return {"status": "saved"}

    @api.post("/api/v1/search")
    async def search(body: SearchBody, user: User = Depends(current_user), a: App = Depends(get_app)) -> dict[str, Any]:
        ctx = UserContext(
            language=user.language, currency=user.currency, timezone=user.timezone, home=repo.user_home(user)
        )
        if body.draft is not None:
            try:
                intent = build_intent(_clean_draft(body.draft), user)
            except (KeyError, ValueError) as exc:
                raise HTTPException(422, "invalid draft") from exc
            intent.missing = _required_missing(intent)
        elif body.text:
            previous = None
            if body.refine:
                async with a.db.session() as s:
                    last = await repo.last_search(s, user)
                previous = SearchIntent.model_validate(last.intent) if last else None
            intent = (await a.interpreter.interpret(body.text, ctx, previous)).intent
        else:
            raise HTTPException(422, "text or draft required")
        if intent.missing:
            async with a.db.session() as s:
                row = await repo.save_search(s, user, intent, [], {"pending": True})
            return {
                "session_id": row.id,
                "missing": intent.missing,
                "question": i18n.t(f"clarify.{intent.missing[0]}", user.language),
                "summary": render.intent_summary(intent, user.language),
            }
        sctx = SearchContext(user_id=user.id, locale=user.language, currency=user.currency)
        outcome = await a.pipeline.run(intent, sctx)
        async with a.db.session() as s:
            row = await repo.save_search(
                s,
                user,
                intent,
                render.dump_ranked(outcome.ranked),
                {
                    "errors": outcome.provider_errors,
                    "skipped": outcome.skipped,
                    "queried": outcome.queried,
                    "partner_only": outcome.partner_only,
                },
            )
        view = outcome_view(outcome, user.language, row.id)
        if outcome.no_coverage:
            view["coverage"] = render.coverage_text(outcome, user.language)
        return view

    @api.get("/api/v1/catalog")
    async def catalog(user: User = Depends(current_user)) -> dict[str, Any]:
        lang = user.language
        return {
            "domains": [
                {
                    "id": d.value,
                    "label": i18n.t(f"domain.{d.value}", lang),
                    "steps": STEPS[d],
                    "categories": [{"id": c, "label": i18n.t(f"cat.{c}", lang)} for c in CATEGORIES.get(d, [])],
                    "filters": [{"id": f, "label": i18n.t(f"constraint.{f}", lang)} for f in FILTERS.get(d, [])],
                    "sorts": [{"id": m.value, "label": i18n.t(f"sort.{m.value}", lang)} for m in SORTS[d.is_transport]],
                }
                for d in GRID
            ],
            "places": [p for p in [user.home_place, *(user.favorites or [])] if p][:6],
        }

    @api.get("/api/v1/places")
    async def places(q: str, user: User = Depends(current_user), a: App = Depends(get_app)) -> list[dict[str, Any]]:
        q = q.strip()[:80]
        if len(q) < 2:
            return []
        if m := re.fullmatch(r"(-?\d{1,2}\.\d+),\s*(-?\d{1,3}\.\d+)", q):
            place = await a.resolver.reverse(float(m.group(1)), float(m.group(2)), language=user.language)
            return [place.model_dump(mode="json")]
        ref = await a.resolver.resolve(q, language=user.language)
        found = [ref.place, *ref.alternatives] if ref.place else []
        return [p.model_dump(mode="json") for p in found if p is not None][:5]

    @api.get("/api/v1/radars")
    async def list_radars(user: User = Depends(current_user), a: App = Depends(get_app)) -> list[dict[str, Any]]:
        async with a.db.session() as s:
            rows = await repo.list_radars(s, user)
        return [
            {
                "id": r.id,
                "name": r.name,
                "state": r.state,
                "kind": r.monitor_kind,
                "domain": r.domain,
                "threshold": f"{r.threshold_amount} {r.threshold_currency}" if r.threshold_amount else None,
                "interval_s": r.interval_s,
                "last_success_at": aware(r.last_success_at).isoformat() if r.last_success_at else None,
                "expires_at": aware(r.expires_at).isoformat(),
            }
            for r in rows
        ]

    @api.post("/api/v1/radars", status_code=201)
    async def create_radar(
        body: RadarBody, user: User = Depends(current_user), a: App = Depends(get_app)
    ) -> dict[str, Any]:
        async with a.db.session() as s:
            row = await repo.get_search(s, user, body.session_id)
        if row is None:
            raise HTTPException(404, "search not found")
        intent = SearchIntent.model_validate(row.intent)
        threshold = None
        if body.threshold:
            try:
                threshold = Money.of(body.threshold, body.currency or user.currency)
            except MoneyError as exc:
                raise HTTPException(422, "invalid threshold") from exc
        try:
            radar = await a.radars.create(
                user,
                RadarDraft(
                    intent,
                    MonitorSpec(kind=body.kind, threshold=threshold),
                    render.intent_summary(intent, user.language).split("\n")[0][:120],
                ),
            )
        except QuotaExceeded as exc:
            raise HTTPException(409, exc.code) from exc
        except ValidationFailed as exc:
            raise HTTPException(422, exc.code) from exc
        return {"id": radar.id, "interval_s": radar.interval_s, "state": radar.state}

    @api.patch("/api/v1/radars/{radar_id}")
    async def patch_radar(
        radar_id: int, body: RadarPatch, user: User = Depends(current_user), a: App = Depends(get_app)
    ) -> dict[str, str]:
        r = await a.radars.set_state(user, radar_id, body.state)
        if r is None:
            raise HTTPException(404, "radar not found")
        return {"state": r.state}

    @api.delete("/api/v1/radars/{radar_id}", status_code=204)
    async def delete_radar(radar_id: int, user: User = Depends(current_user), a: App = Depends(get_app)) -> Response:
        r = await a.radars.set_state(user, radar_id, "deleted")
        if r is None:
            raise HTTPException(404, "radar not found")
        return Response(status_code=204)

    @api.get("/api/v1/plans")
    async def plans(user: User = Depends(current_user), a: App = Depends(get_app)) -> list[dict[str, Any]]:
        from sqlalchemy import select

        async with a.db.session() as s:
            rows = (
                (
                    await s.execute(
                        select(ItineraryRow)
                        .where(ItineraryRow.user_id == user.id)
                        .order_by(ItineraryRow.id.desc())
                        .limit(10)
                    )
                )
                .scalars()
                .all()
            )
        return [{"id": r.id, "title": r.title, "data": r.data} for r in rows]

    @api.post("/telegram/webhook")
    async def telegram_webhook(
        request: Request,
        a: App = Depends(get_app),
        x_telegram_bot_api_secret_token: Annotated[str | None, Header()] = None,
    ) -> dict[str, bool]:
        secret = request.app.state.webhook_secret if hasattr(request.app.state, "webhook_secret") else None
        if (
            not secret
            or not x_telegram_bot_api_secret_token
            or not hmac.compare_digest(secret, x_telegram_bot_api_secret_token)
        ):
            raise HTTPException(403, "forbidden")
        from aiogram.types import Update

        update = Update.model_validate(await request.json(), context={"bot": request.app.state.bot})
        await request.app.state.dispatcher.feed_update(request.app.state.bot, update)
        return {"ok": True}

    if WEBAPP_DIR.exists():
        api.mount("/app/static", StaticFiles(directory=WEBAPP_DIR), name="webapp")

        @api.get("/app")
        async def webapp_index() -> FileResponse:
            return FileResponse(WEBAPP_DIR / "index.html")

    return api
