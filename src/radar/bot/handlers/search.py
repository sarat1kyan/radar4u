"""Free-text search, clarifications, the structured wizard, result browsing and plans."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time as _time
from datetime import datetime, time, timedelta

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from radar import i18n
from radar.app import App
from radar.bot import keyboards as kb
from radar.bot import render, richrender, ui
from radar.core.provenance import utcnow
from radar.core.timeutil import aware, now_in, tz
from radar.domain.common import DateWindow, Domain, LocationRef, Place, SortMode, StayDates
from radar.domain.intents import SearchIntent
from radar.geo.resolver import GAZ
from radar.nlu.interpreter import UserContext
from radar.nlu.rules import _required_missing
from radar.providers.base import SearchContext
from radar.search.pipeline import SearchOutcome
from radar.search.ranking import rank
from radar.storage import repo
from radar.storage.models import Itinerary as ItineraryRow
from radar.storage.models import User

log = logging.getLogger(__name__)
router = Router(name="search")

REFINE_WINDOW = timedelta(hours=2)


class Wizard(StatesGroup):
    city = State()
    destination = State()


async def user_context(user: User, state: FSMContext) -> UserContext:
    data = await state.get_data()
    current = Place.model_validate(data["current"]) if data.get("current") else None
    favorites = [Place.model_validate(p) for p in (user.favorites or [])]
    return UserContext(
        language=user.language,
        currency=user.currency,
        timezone=user.timezone,
        home=repo.user_home(user),
        current=current,
        favorites=favorites,
    )


def search_ctx(user: User) -> SearchContext:
    return SearchContext(user_id=user.id, locale=user.language, currency=user.currency)


async def _previous_intent(app: App, user: User) -> SearchIntent | None:
    async with app.db.session() as s:
        last = await repo.last_search(s, user)
    if last is None:
        return None
    if aware(last.created_at) < utcnow() - REFINE_WINDOW:
        return None
    return SearchIntent.model_validate(last.intent)


async def ask_clarification(message: Message, app: App, user: User, intent: SearchIntent, ctx: UserContext) -> None:
    lang = user.language
    async with app.db.session() as s:
        await repo.save_search(s, user, intent, [], {"pending": True})
    first = intent.missing[0]
    if first == "domain":
        await message.answer(
            i18n.t("clarify.domain", lang),
            reply_markup=kb.wizard_options(
                "pd",
                [
                    (i18n.t(f"domain.{d.value}", lang), d.value)
                    for d in (Domain.DINING, Domain.STAY, Domain.RAIL, Domain.FLIGHT, Domain.EVENT, Domain.WELLNESS)
                ],
            ),
        )
        return
    if first in ("location", "origin", "destination"):
        opts = [(p.name, str(i)) for i, p in enumerate(context_places(ctx))]
        markup = kb.wizard_options(f"p{first[0]}", opts[:6]) if opts and first != "destination" else None
        await message.answer(i18n.t(f"clarify.{first}", lang), reply_markup=markup)
        return
    if first in ("date", "dates"):
        await message.answer(
            i18n.t(f"clarify.{first}", lang),
            reply_markup=kb.wizard_options(
                "pt",
                [
                    (i18n.t("search.today", lang), "0"),
                    (i18n.t("search.tomorrow", lang), "1"),
                    (i18n.t("search.weekend", lang), "w"),
                ],
            ),
        )
        return
    await message.answer(i18n.t(f"clarify.{first}", lang))


@router.message(StateFilter(None), F.text & ~F.text.startswith("/"))
async def on_text(message: Message, app: App, user: User, state: FSMContext) -> None:
    text = message.text or ""
    ctx = await user_context(user, state)
    previous = await _previous_intent(app, user)
    try:
        interp = await app.interpreter.interpret(text, ctx, previous)
    except Exception:
        log.exception("interpretation failed")
        await message.answer(i18n.t("error.generic", user.language))
        return
    intent = interp.intent
    for note in interp.notes:
        if note.startswith("unresolved:"):
            await message.answer(i18n.t("error.unknown_place", user.language, place=render.esc(note.split(":", 2)[2])))
    if intent.missing:
        await ask_clarification(message, app, user, intent, ctx)
        return
    if interp.used_context_location and (ref := intent.primary_location()) is not None and ref.place:
        await message.answer(i18n.t("search.used_home_city", user.language, city=render.esc(ref.place.name)))
    await execute(message, app, user, intent)


async def execute(
    message: Message, app: App, user: User, intent: SearchIntent, *, edit_message: Message | None = None
) -> None:
    lang = user.language
    if intent.domain is Domain.PLAN:
        await run_plan(message, app, user, intent, edit_message=edit_message)
        return
    eligible = app.registry.eligible(intent)
    names = ", ".join(p.display_name for p in eligible.providers) or "-"
    wait = (
        ui.Rich()
        .h(render.intent_summary(intent, lang, user.time_format).split("\n")[0], 3)
        .p("<i>" + i18n.t("search.progress", lang, sources=render.esc(names)) + "</i>")
    )
    wait_text = i18n.t("search.progress", lang, sources=names)
    if edit_message is not None:
        progress = await ui.edit(edit_message, wait, wait_text)
    else:
        progress = await ui.send(message, wait, wait_text)
    last_edit = [0.0]

    async def on_progress(name: str, done: int, total: int) -> None:
        if done >= total or _time.monotonic() - last_edit[0] < 1.5:
            return
        last_edit[0] = _time.monotonic()
        with contextlib.suppress(TelegramBadRequest):
            await ui.edit(progress, wait.__class__(parts=[*wait.parts, f"<p>{done}/{total}</p>"]), wait_text)

    outcome = await app.pipeline.run(intent, search_ctx(user), on_progress=on_progress)
    async with app.db.session() as s:
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
        await repo.audit(
            s, user.id, "search", domain=intent.domain.value, results=len(outcome.ranked), ms=int(outcome.total_ms)
        )
    await show_page(progress, user, row.id, outcome, 0, edit=True)


async def show_page(msg: Message, user: User, sid: int, outcome: SearchOutcome, page: int, *, edit: bool) -> None:
    r, fallback, markup = richrender.results_view(outcome, sid, user.language, page, user.time_format)
    if edit:
        await ui.edit(msg, r, fallback, markup)
    else:
        await ui.send(msg, r, fallback, markup)


async def load_outcome(app: App, user: User, sid: int) -> SearchOutcome | None:
    async with app.db.session() as s:
        row = await repo.get_search(s, user, sid)
    if row is None or not row.results:
        return None
    return render.outcome_from_session(SearchIntent.model_validate(row.intent), row.results, row.summary)


@router.callback_query(F.data.regexp(r"^tp:\d+:[+-]$"))
async def on_time_shift(cb: CallbackQuery, app: App, user: User) -> None:
    """Earlier / Later departures: shift the window by two hours and search again in place."""
    _, sid, sign = (cb.data or "").split(":")
    async with app.db.session() as s:
        row = await repo.get_search(s, user, int(sid))
    if row is None or not isinstance(cb.message, Message):
        await cb.answer(i18n.t("error.not_found", user.language), show_alert=True)
        return
    intent = SearchIntent.model_validate(row.intent)
    delta = timedelta(hours=2 if sign == "+" else -2)
    ref = intent.origin or intent.location
    zone = ref.place.timezone if ref and ref.place else user.timezone
    now = now_in(zone).replace(second=0, microsecond=0)
    base = intent.when.start if intent.when and intent.when.start else now
    base_end = intent.when.end if intent.when and intent.when.end else base + timedelta(hours=4)
    start = max(now, base + delta) if sign == "-" else base + delta
    end = base_end + delta
    intent.when = DateWindow(start=start, end=max(start, end))
    intent.arrive_by = False
    await cb.answer()
    await execute(cb.message, app, user, intent, edit_message=cb.message)


@router.callback_query(kb.PageCb.filter())
async def on_page(cb: CallbackQuery, callback_data: kb.PageCb, app: App, user: User) -> None:
    outcome = await load_outcome(app, user, callback_data.s)
    if outcome is None or not isinstance(cb.message, Message):
        await cb.answer(i18n.t("error.not_found", user.language), show_alert=True)
        return
    await show_page(cb.message, user, callback_data.s, outcome, callback_data.p, edit=True)
    await cb.answer()


@router.callback_query(kb.SortCb.filter())
async def on_sort(cb: CallbackQuery, callback_data: kb.SortCb, app: App, user: User) -> None:
    outcome = await load_outcome(app, user, callback_data.s)
    if outcome is None or not isinstance(cb.message, Message):
        await cb.answer(i18n.t("error.not_found", user.language), show_alert=True)
        return
    if not callback_data.m:
        await cb.message.edit_reply_markup(
            reply_markup=kb.sort_modes(callback_data.s, outcome.intent.domain, user.language)
        )
        await cb.answer()
        return
    outcome.ranked = rank(outcome.ranked, outcome.intent, SortMode(callback_data.m))
    async with app.db.session() as s:
        row = await repo.get_search(s, user, callback_data.s)
        if row is not None:
            row.results = render.dump_ranked(outcome.ranked)
    await show_page(cb.message, user, callback_data.s, outcome, 0, edit=True)
    await cb.answer(i18n.t(f"sort.{callback_data.m}", user.language))


@router.callback_query(kb.ResultCb.filter(F.a.in_({"d", "m"})))
async def on_result(cb: CallbackQuery, callback_data: kb.ResultCb, app: App, user: User) -> None:
    outcome = await load_outcome(app, user, callback_data.s)
    item = outcome.find(callback_data.i) if outcome else None
    if outcome is None or item is None or not isinstance(cb.message, Message):
        await cb.answer(i18n.t("error.not_found", user.language), show_alert=True)
        return
    r = item.result
    if callback_data.a == "m" and r.point is not None:
        await cb.message.answer_venue(
            latitude=r.point.lat,
            longitude=r.point.lon,
            title=r.title[:100],
            address=(r.address or r.subtitle or "")[:100] or " ",
        )
    else:
        rich, fallback, markup = richrender.card_view(
            item, outcome.intent, callback_data.s, user.language, user.time_format
        )
        await ui.send(cb.message, rich, fallback, markup)
    await cb.answer()


# --- clarification answers via buttons ------------------------------------------------------------


@router.callback_query(kb.WizCb.filter(F.st.in_({"pd", "pl", "po", "pt"})))
async def on_clarify(cb: CallbackQuery, callback_data: kb.WizCb, app: App, user: User, state: FSMContext) -> None:
    async with app.db.session() as s:
        last = await repo.last_search(s, user)
    if last is None or not isinstance(cb.message, Message):
        await cb.answer(i18n.t("error.not_found", user.language), show_alert=True)
        return
    intent = SearchIntent.model_validate(last.intent)
    ctx = await user_context(user, state)
    v = callback_data.v
    if callback_data.st == "pd":
        intent = (await app.interpreter.interpret(intent.raw_text, ctx, None)).intent
        intent.domain = Domain(v)
        intent.requirements = [c for c in intent.requirements if intent.domain in c.definition.domains]
    elif callback_data.st in ("pl", "po"):
        place = _place_by_id(v, ctx)
        if place is not None:
            ref = LocationRef(text=place.name, place=place)
            if callback_data.st == "po":
                intent.origin = ref
            else:
                intent.location = ref
    elif callback_data.st == "pt":
        _apply_quick_date(intent, v, ctx)
    intent.missing = _required_missing(intent)
    await cb.answer()
    if intent.missing:
        await ask_clarification(cb.message, app, user, intent, ctx)
    else:
        await execute(cb.message, app, user, intent)


def context_places(ctx: UserContext) -> list[Place]:
    """Saved places in a stable order; buttons refer to them by index (callback data can't hold ':')."""
    out: list[Place] = []
    for p in [ctx.current, ctx.home, *ctx.favorites]:
        if p is not None and p.canonical_id not in {x.canonical_id for x in out}:
            out.append(p)
    return out[:6]


def _place_by_id(pid: str, ctx: UserContext) -> Place | None:
    places = context_places(ctx)
    if pid.isdigit() and int(pid) < len(places):
        return places[int(pid)]
    e = GAZ.by_id(pid)
    return e.to_place() if e else None


def _apply_quick_date(intent: SearchIntent, v: str, ctx: UserContext) -> None:
    ref = intent.primary_location()
    zone = (ref.place.timezone if ref and ref.place else None) or ctx.timezone
    today = now_in(zone).date()
    if v == "w":
        start = today + timedelta(days=(5 - today.weekday()) % 7)
        end = start + timedelta(days=1)
    else:
        start = end = today + timedelta(days=int(v))
    if intent.domain is Domain.STAY:
        intent.stay = StayDates(check_in=start, check_out=end if end > start else start + timedelta(days=1))
    else:
        z = tz(zone)
        intent.when = DateWindow(
            start=datetime.combine(start, time(0, 0), z), end=datetime.combine(end, time(23, 59), z)
        )


# --- plans ----------------------------------------------------------------------------------------


async def run_plan(
    message: Message, app: App, user: User, intent: SearchIntent, *, edit_message: Message | None = None
) -> None:
    lang = user.language
    wait = ui.Rich().p("<i>" + i18n.t("plan.building", lang) + "</i>")
    if edit_message is not None:
        progress = await ui.edit(edit_message, wait, i18n.t("plan.building", lang))
    else:
        progress = await ui.send(message, wait, i18n.t("plan.building", lang))
    try:
        itinerary = await asyncio.wait_for(
            app.planner.build(intent, search_ctx(user)), timeout=app.settings.search_timeout_s * 3
        )
    except (TimeoutError, ValueError):
        await progress.edit_text(i18n.t("error.generic", lang))
        return
    async with app.db.session() as s:
        s.add(ItineraryRow(user_id=user.id, title=itinerary.title, data=itinerary.model_dump(mode="json")))
        await repo.save_search(s, user, intent, [], {"plan": True})
    rich, fallback = richrender.itinerary_view(itinerary, lang, user.time_format)
    await ui.edit(progress, rich, fallback, ui.kb([[(i18n.t("search.new", lang), kb.MenuCb(a="home").pack(), None)]]))
