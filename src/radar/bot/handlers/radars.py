"""Creating and managing radars from the chat."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from radar import i18n
from radar.app import App
from radar.bot import keyboards as kb
from radar.bot import render
from radar.core.errors import QuotaExceeded, ValidationFailed
from radar.core.money import Money
from radar.core.provenance import utcnow
from radar.core.timeutil import aware
from radar.domain.intents import MonitorKind, MonitorSpec, SearchIntent
from radar.monitoring.engine import RadarDraft
from radar.nlu.rules import _money_mentions
from radar.storage import repo
from radar.storage.models import Radar, User

router = Router(name="radars")


class ThresholdForm(StatesGroup):
    amount = State()


def _schedule(interval_s: int, lang: str) -> str:
    hours = max(1, round(interval_s / 3600))
    if hours == 1:
        return i18n.t("radar.schedule.hourly", lang)
    if hours >= 24:
        return i18n.t("radar.schedule.daily", lang)
    return i18n.t("radar.schedule.every", lang, hours=hours)


def _condition(r: Radar, lang: str) -> str:
    if r.monitor_kind == MonitorKind.PRICE_BELOW.value and r.threshold_amount and r.threshold_currency:
        return i18n.t(
            "radar.condition.price_below",
            lang,
            threshold=i18n.money(Money.of(r.threshold_amount, r.threshold_currency), lang),
        )
    return i18n.t(f"radar.condition.{r.monitor_kind}", lang)


def parse_amount(text: str, default_currency: str) -> Money | None:
    found = _money_mentions(text)
    if found:
        return found[0][0]
    cleaned = text.strip().replace(" ", "").replace(",", ".")
    try:
        return Money.of(cleaned, default_currency) if cleaned and float(cleaned) > 0 else None
    except (ValueError, ArithmeticError):
        return None


async def create_radar(msg: Message, app: App, user: User, intent: SearchIntent, monitor: MonitorSpec) -> None:
    lang = user.language
    name = render.intent_summary(intent, lang).split("\n")[0]
    name = name.replace("<b>", "").replace("</b>", "")[:120]
    try:
        radar = await app.radars.create(user, RadarDraft(intent=intent, monitor=monitor, name=name))
    except QuotaExceeded:
        await msg.answer(i18n.t("radar.quota", lang, limit=app.settings.max_radars_per_user))
        return
    except ValidationFailed as exc:
        key = "radar.not_supported" if exc.code == "monitoring_not_supported" else "radar.threshold_invalid"
        await msg.answer(i18n.t(key, lang))
        return
    await msg.answer(
        i18n.t("radar.created", lang, schedule=_schedule(radar.interval_s, lang), condition=_condition(radar, lang))
    )


@router.callback_query(kb.WatchCb.filter())
async def on_watch(cb: CallbackQuery, callback_data: kb.WatchCb, app: App, user: User, state: FSMContext) -> None:
    lang = user.language
    async with app.db.session() as s:
        row = await repo.get_search(s, user, callback_data.s)
    if row is None or not isinstance(cb.message, Message):
        await cb.answer(i18n.t("error.not_found", lang), show_alert=True)
        return
    intent = SearchIntent.model_validate(row.intent)
    await cb.answer()
    if not callback_data.k:
        if intent.monitor is not None and (
            intent.monitor.kind is not MonitorKind.PRICE_BELOW or intent.monitor.threshold is not None
        ):
            await create_radar(cb.message, app, user, intent, intent.monitor)
            return
        has_price = any(r.get("result", {}).get("price") for r in row.results)
        await cb.message.answer(
            i18n.t("radar.ask_kind", lang), reply_markup=kb.watch_kinds(callback_data.s, lang, has_price)
        )
        return
    if callback_data.k == "p":
        await state.set_state(ThresholdForm.amount)
        await state.update_data(radar_sid=callback_data.s, radar_id=None)
        await cb.message.answer(i18n.t("radar.ask_threshold_text", lang))
        return
    kind = MonitorKind.AVAILABILITY if callback_data.k == "a" else MonitorKind.NEW_RESULTS
    await create_radar(cb.message, app, user, intent, MonitorSpec(kind=kind))


@router.message(ThresholdForm.amount, F.text)
async def on_threshold(message: Message, app: App, user: User, state: FSMContext) -> None:
    lang = user.language
    amount = parse_amount(message.text or "", user.currency)
    if amount is None:
        await message.answer(i18n.t("radar.threshold_invalid", lang))
        return
    data = await state.get_data()
    await state.set_state(None)
    if data.get("radar_id"):
        r = await app.radars.update_threshold(user, int(data["radar_id"]), amount)
        await message.answer(i18n.t("settings.saved", lang) if r else i18n.t("error.not_found", lang))
        return
    async with app.db.session() as s:
        row = await repo.get_search(s, user, int(data.get("radar_sid", 0)))
    if row is None:
        await message.answer(i18n.t("error.not_found", lang))
        return
    intent = SearchIntent.model_validate(row.intent)
    await create_radar(message, app, user, intent, MonitorSpec(kind=MonitorKind.PRICE_BELOW, threshold=amount))


@router.message(Command("radars"))
@router.callback_query(kb.MenuCb.filter(F.a == "radars"))
async def on_list(event: Message | CallbackQuery, app: App, user: User) -> None:
    msg = event.message if isinstance(event, CallbackQuery) else event
    lang = user.language
    async with app.db.session() as s:
        radars = await repo.list_radars(s, user)
    if isinstance(event, CallbackQuery):
        await event.answer()
    if not isinstance(msg, Message):
        return
    if not radars:
        await msg.answer(i18n.t("radar.list_empty", lang))
        return
    await msg.answer(i18n.t("radar.list_header", lang))
    now = utcnow()
    for r in radars:
        text = i18n.t(
            "radar.list_item",
            lang,
            name=render.esc(r.name, 120),
            condition=_condition(r, lang),
            state=i18n.t(f"radar.state.{r.state}", lang),
        )
        if r.last_success_at:
            text += "\n" + i18n.t(
                "radar.last_checked", lang, age=i18n.age((now - aware(r.last_success_at)).total_seconds(), lang)
            )
        await msg.answer(text, reply_markup=kb.radar_actions(r.id, r.state, lang))


@router.callback_query(kb.RadarCb.filter())
async def on_radar_action(
    cb: CallbackQuery, callback_data: kb.RadarCb, app: App, user: User, state: FSMContext
) -> None:
    lang = user.language
    a = callback_data.a
    if a == "t":
        await state.set_state(ThresholdForm.amount)
        await state.update_data(radar_id=callback_data.r, radar_sid=None)
        await cb.answer()
        if isinstance(cb.message, Message):
            await cb.message.answer(i18n.t("radar.ask_threshold_text", lang))
        return
    new_state = {"p": "paused", "r": "active", "d": "deleted"}[a]
    r = await app.radars.set_state(user, callback_data.r, new_state)
    if r is None:
        await cb.answer(i18n.t("error.not_found", lang), show_alert=True)
        return
    key = {"paused": "radar.paused", "active": "radar.resumed", "deleted": "radar.deleted"}[new_state]
    await cb.answer(i18n.t(key, lang))
    if isinstance(cb.message, Message):
        if new_state == "deleted":
            await cb.message.edit_reply_markup(reply_markup=None)
        else:
            await cb.message.edit_reply_markup(reply_markup=kb.radar_actions(r.id, r.state, lang))
