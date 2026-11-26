"""Free-text search, clarifications, the structured wizard, result browsing and plans."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time as _time
from datetime import datetime, time, timedelta
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from radar import i18n
from radar.app import App
from radar.bot import keyboards as kb
from radar.bot import render
from radar.core.provenance import utcnow
from radar.core.timeutil import aware, now_in, tz
from radar.domain.common import DateWindow, Domain, LocationRef, Party, Place, SortMode, StayDates
from radar.domain.intents import PlanKind, SearchIntent
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
        places = [p for p in [ctx.current, ctx.home, *ctx.favorites] if p is not None]
        seen: set[str] = set()
        opts = []
        for p in places:
            if p.canonical_id not in seen and len(p.canonical_id) <= 40:
                seen.add(p.canonical_id)
                opts.append((p.name, p.canonical_id))
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


async def execute(message: Message, app: App, user: User, intent: SearchIntent) -> None:
    lang = user.language
    if intent.domain is Domain.PLAN:
        await run_plan(message, app, user, intent)
        return
    eligible = app.registry.eligible(intent)
    names = ", ".join(p.display_name for p in eligible.providers) or "-"
    progress = await message.answer(i18n.t("search.progress", lang, sources=names))
    last_edit = [0.0]

    async def on_progress(name: str, done: int, total: int) -> None:
        if done >= total or _time.monotonic() - last_edit[0] < 1.5:
            return
        last_edit[0] = _time.monotonic()
        with contextlib.suppress(TelegramBadRequest):
            await progress.edit_text(i18n.t("search.progress", lang, sources=names) + f" {done}/{total}")

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
    text, pages = render.outcome_text(outcome, user.language, page, user.time_format)
    chunk = outcome.ranked[page * render.PAGE_SIZE : (page + 1) * render.PAGE_SIZE]
    markup = (
        kb.results(sid, [r.result.id for r in chunk], page, pages, user.language, page * render.PAGE_SIZE + 1)
        if outcome.ranked
        else None
    )
    try:
        if edit:
            await msg.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        else:
            await msg.answer(text, reply_markup=markup, disable_web_page_preview=True)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            await msg.answer(text, reply_markup=markup, disable_web_page_preview=True)


async def load_outcome(app: App, user: User, sid: int) -> SearchOutcome | None:
    async with app.db.session() as s:
        row = await repo.get_search(s, user, sid)
    if row is None or not row.results:
        return None
    return render.outcome_from_session(SearchIntent.model_validate(row.intent), row.results, row.summary)


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
        await cb.message.answer(
            render.result_card(item, outcome.intent, user.language, user.time_format),
            reply_markup=kb.result_actions(callback_data.s, r, user.language),
            disable_web_page_preview=True,
        )
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


def _place_by_id(pid: str, ctx: UserContext) -> Place | None:
    for p in [ctx.current, ctx.home, *ctx.favorites]:
        if p is not None and p.canonical_id == pid:
            return p
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


# --- structured search wizard ---------------------------------------------------------------------


@router.callback_query(kb.MenuCb.filter(F.a == "search"))
async def on_menu_search(cb: CallbackQuery, user: User, state: FSMContext) -> None:
    await state.set_state(None)
    if isinstance(cb.message, Message):
        await cb.message.answer(
            i18n.t("search.pick_domain", user.language), reply_markup=kb.wizard_domains(user.language)
        )
    await cb.answer()


@router.callback_query(kb.WizCb.filter(F.st.in_({"d", "c", "t", "n", "k"})))
async def on_wizard(cb: CallbackQuery, callback_data: kb.WizCb, app: App, user: User, state: FSMContext) -> None:
    data = await state.get_data()
    wiz: dict[str, Any] = dict(data.get("wiz") or {})
    ctx = await user_context(user, state)
    st, v = callback_data.st, callback_data.v
    if st == "d":
        wiz = {"domain": v}
    elif st == "c":
        wiz["place"] = v
    elif st == "t":
        wiz["date"] = v
    elif st == "n":
        wiz["party"] = int(v)
    elif st == "k":
        wiz["plan_kind"] = v
    await state.update_data(wiz=wiz)
    await cb.answer()
    if isinstance(cb.message, Message):
        await _wizard_next(cb.message, app, user, state, wiz, ctx)


async def _wizard_next(
    msg: Message, app: App, user: User, state: FSMContext, wiz: dict[str, Any], ctx: UserContext
) -> None:
    lang = user.language
    domain = Domain(wiz["domain"])
    if "place" not in wiz:
        places = [p for p in [ctx.current, ctx.home, *ctx.favorites] if p is not None]
        opts = list(dict.fromkeys((p.name, p.canonical_id) for p in places if len(p.canonical_id) <= 40))[:6]
        await state.set_state(Wizard.city)
        key = "clarify.origin" if domain.is_transport else "search.ask_city"
        await msg.answer(i18n.t(key, lang), reply_markup=kb.wizard_options("c", opts) if opts else None)
        return
    if domain.is_transport and "destination" not in wiz:
        await state.set_state(Wizard.destination)
        await msg.answer(i18n.t("clarify.destination", lang))
        return
    if domain is Domain.PLAN and "plan_kind" not in wiz:
        await msg.answer(
            i18n.t("search.pick_domain", lang),
            reply_markup=kb.wizard_options(
                "k",
                [
                    (k.value.replace("_", " "), k.value)
                    for k in (PlanKind.ROMANTIC_WEEKEND, PlanKind.CITY_TRIP, PlanKind.EVENING_DATE, PlanKind.DAY_TRIP)
                ],
            ),
        )
        return
    if "date" not in wiz:
        opts = [
            (i18n.t("search.today", lang), "0"),
            (i18n.t("search.tomorrow", lang), "1"),
            (i18n.t("search.weekend", lang), "w"),
        ]
        if domain in (Domain.EVENT, Domain.REAL_ESTATE, Domain.SHOPPING, Domain.WELLNESS):
            opts.append((i18n.t("search.any_date", lang), "-"))
        await msg.answer(i18n.t("search.ask_date", lang), reply_markup=kb.wizard_options("t", opts))
        return
    if "party" not in wiz and domain in (Domain.DINING, Domain.STAY, Domain.WELLNESS, Domain.PLAN):
        await msg.answer(
            i18n.t("search.ask_party", lang),
            reply_markup=kb.wizard_options(
                "n", [(i18n.t("wizard.party", lang, count=n), str(n)) for n in (1, 2, 3, 4, 6)]
            ),
        )
        return
    await state.update_data(wiz=None)
    await state.set_state(None)
    intent = await _wizard_intent(app, wiz, ctx)
    if intent is None:
        await msg.answer(i18n.t("error.unknown_place", lang, place=render.esc(wiz.get("place", ""))))
        return
    await execute(msg, app, user, intent)


async def _resolve(app: App, value: str, ctx: UserContext) -> LocationRef | None:
    p = _place_by_id(value, ctx)
    if p is not None:
        return LocationRef(text=p.name, place=p)
    ref = await app.resolver.resolve(value, language=ctx.language)
    return ref if ref.place else None


async def _wizard_intent(app: App, wiz: dict[str, Any], ctx: UserContext) -> SearchIntent | None:
    domain = Domain(wiz["domain"])
    intent = SearchIntent(domain=domain, language=ctx.language, party=Party(adults=int(wiz.get("party", 1))))
    loc = await _resolve(app, wiz["place"], ctx)
    if loc is None:
        return None
    if domain.is_transport:
        intent.origin = loc
        dest = await _resolve(app, wiz["destination"], ctx)
        if dest is None:
            return None
        intent.destination = dest
    else:
        intent.location = loc
    if domain is Domain.PLAN:
        intent.plan_kind = PlanKind(wiz.get("plan_kind", "city_trip"))
    if wiz.get("date") not in (None, "-"):
        _apply_quick_date(intent, wiz["date"], ctx)
    if domain is Domain.GROUND:
        intent.categories.append("mode:multimodal")
    intent.missing = _required_missing(intent)
    return intent


@router.message(Wizard.city, F.text)
async def on_wizard_city(message: Message, app: App, user: User, state: FSMContext) -> None:
    data = await state.get_data()
    wiz = dict(data.get("wiz") or {})
    wiz["place"] = (message.text or "").strip()[:120]
    await state.update_data(wiz=wiz)
    await state.set_state(None)
    await _wizard_next(message, app, user, state, wiz, await user_context(user, state))


@router.message(Wizard.destination, F.text)
async def on_wizard_destination(message: Message, app: App, user: User, state: FSMContext) -> None:
    data = await state.get_data()
    wiz = dict(data.get("wiz") or {})
    wiz["destination"] = (message.text or "").strip()[:120]
    await state.update_data(wiz=wiz)
    await state.set_state(None)
    await _wizard_next(message, app, user, state, wiz, await user_context(user, state))


# --- plans ----------------------------------------------------------------------------------------


async def run_plan(message: Message, app: App, user: User, intent: SearchIntent) -> None:
    progress = await message.answer(i18n.t("plan.building", user.language))
    try:
        itinerary = await asyncio.wait_for(
            app.planner.build(intent, search_ctx(user)), timeout=app.settings.search_timeout_s * 3
        )
    except (TimeoutError, ValueError):
        await progress.edit_text(i18n.t("error.generic", user.language))
        return
    async with app.db.session() as s:
        s.add(ItineraryRow(user_id=user.id, title=itinerary.title, data=itinerary.model_dump(mode="json")))
        await repo.save_search(s, user, intent, [], {"plan": True})
    await progress.edit_text(
        render.itinerary_text(itinerary, user.language, user.time_format), disable_web_page_preview=True
    )
