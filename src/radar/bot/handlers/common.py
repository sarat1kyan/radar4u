"""Onboarding, help, privacy, settings and location."""

from __future__ import annotations

from datetime import time

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder

from radar import i18n
from radar.app import App
from radar.bot import keyboards as kb
from radar.bot import render
from radar.domain.common import Place
from radar.storage import repo
from radar.storage.models import User

router = Router(name="common")


class Onboarding(StatesGroup):
    city = State()


class SettingsForm(StatesGroup):
    city = State()


async def _save(app: App, user: User, **fields: object) -> User:
    async with app.db.session() as s:
        u = await s.get(User, user.id)
        assert u is not None
        for k, v in fields.items():
            setattr(u, k, v)
        return u


@router.message(CommandStart())
async def on_start(message: Message, app: App, user: User, state: FSMContext) -> None:
    await state.clear()
    if not user.onboarded:
        await message.answer(i18n.t("start.pick_language", user.language), reply_markup=kb.languages())
        return
    await message.answer(
        i18n.t("start.welcome", user.language), reply_markup=kb.main_menu(user.language, app.settings.webapp_url)
    )


@router.callback_query(kb.LangCb.filter())
async def on_language(cb: CallbackQuery, callback_data: kb.LangCb, app: App, user: User, state: FSMContext) -> None:
    lang = i18n.normalize_lang(callback_data.c)
    user = await _save(app, user, language=lang)
    await cb.answer(i18n.t("settings.saved", lang))
    if not isinstance(cb.message, Message):
        return
    if not user.onboarded:
        await state.set_state(Onboarding.city)
        await cb.message.answer(i18n.t("start.pick_city", lang), reply_markup=kb.location_request(lang))
    else:
        await cb.message.answer(
            i18n.t("settings.saved", lang), reply_markup=kb.main_menu(lang, app.settings.webapp_url)
        )


async def _set_home_from_text(app: App, user: User, text: str) -> Place | None:
    ref = await app.resolver.resolve(text, language=user.language)
    if ref.place is None:
        return None
    favs = [f for f in (user.favorites or []) if f.get("canonical_id") != ref.place.canonical_id]
    await _save(
        app,
        user,
        home_place=ref.place.model_dump(mode="json"),
        timezone=ref.place.timezone,
        favorites=[ref.place.model_dump(mode="json"), *favs][:8],
    )
    return ref.place


@router.message(Onboarding.city, F.text)
async def on_onboarding_city(message: Message, app: App, user: User, state: FSMContext) -> None:
    place = await _set_home_from_text(app, user, message.text or "")
    if place is None:
        await message.answer(i18n.t("error.unknown_place", user.language, place=render.esc(message.text or "")))
        return
    await _finish_onboarding(message, app, user, state)


@router.message(Onboarding.city, F.location)
async def on_onboarding_location(message: Message, app: App, user: User, state: FSMContext) -> None:
    await on_location(message, app, user, state)
    await _finish_onboarding(message, app, user, state)


async def _finish_onboarding(message: Message, app: App, user: User, state: FSMContext) -> None:
    await state.set_state(None)
    user = await _save(app, user, onboarded=True)
    await message.answer(i18n.t("start.done", user.language), reply_markup=ReplyKeyboardRemove())
    await message.answer(
        i18n.t("start.welcome", user.language), reply_markup=kb.main_menu(user.language, app.settings.webapp_url)
    )


@router.message(Command("help"))
@router.callback_query(kb.MenuCb.filter(F.a == "help"))
async def on_help(event: Message | CallbackQuery, user: User) -> None:
    msg = event.message if isinstance(event, CallbackQuery) else event
    if isinstance(msg, Message):
        await msg.answer(i18n.t("help.text", user.language))
    if isinstance(event, CallbackQuery):
        await event.answer()


@router.message(Command("privacy"))
async def on_privacy(message: Message, user: User) -> None:
    await message.answer(i18n.t("privacy.text", user.language))


@router.message(Command("forget"))
async def on_forget(message: Message, user: User) -> None:
    b = InlineKeyboardBuilder()
    b.button(text=i18n.t("common.yes", user.language), callback_data=kb.SetCb(k="forget", v="yes"))
    b.button(text=i18n.t("common.no", user.language), callback_data=kb.SetCb(k="forget", v="no"))
    await message.answer(i18n.t("forget.confirm", user.language), reply_markup=b.as_markup())


@router.message(Command("location"))
async def on_location_cmd(message: Message, user: User) -> None:
    await message.answer(i18n.t("location.ask", user.language), reply_markup=kb.location_request(user.language))


@router.message(F.location)
async def on_location(message: Message, app: App, user: User, state: FSMContext) -> None:
    loc = message.location
    if loc is None:
        return
    ref = await app.resolver.resolve(f"{loc.latitude:.5f},{loc.longitude:.5f}", language=user.language)
    if ref.place is not None:
        # Kept only in the conversation state, never written to the database.
        await state.update_data(current=ref.place.model_dump(mode="json"))
    await message.answer(i18n.t("location.saved", user.language), reply_markup=ReplyKeyboardRemove())


@router.message(Command("settings"))
@router.callback_query(kb.MenuCb.filter(F.a == "settings"))
async def on_settings(event: Message | CallbackQuery, user: User) -> None:
    msg = event.message if isinstance(event, CallbackQuery) else event
    lang = user.language
    home = repo.user_home(user)
    quiet = (
        f"{user.quiet_start:%H:%M}-{user.quiet_end:%H:%M}"
        if user.quiet_start and user.quiet_end
        else i18n.t("settings.quiet_off", lang)
    )
    text = "\n".join(
        [
            f"<b>{i18n.t('settings.header', lang)}</b>",
            f"{i18n.t('settings.language', lang)}: {i18n.available().get(lang, lang)}",
            f"{i18n.t('settings.currency', lang)}: {user.currency}",
            i18n.t("settings.city", lang, city=render.esc(home.name if home else "-")),
            f"{i18n.t('settings.time_format', lang)}: {i18n.t('settings.time_' + user.time_format, lang)}",
            i18n.t("settings.quiet_hours", lang, range=quiet),
            i18n.t("settings.digest", lang, mode=i18n.t(f"settings.digest.{user.digest_mode}", lang)),
        ]
    )
    if isinstance(msg, Message):
        await msg.answer(text, reply_markup=kb.settings(lang))
    if isinstance(event, CallbackQuery):
        await event.answer()


@router.callback_query(kb.SetCb.filter())
async def on_setting(cb: CallbackQuery, callback_data: kb.SetCb, app: App, user: User, state: FSMContext) -> None:
    lang, k, v = user.language, callback_data.k, callback_data.v
    msg = cb.message if isinstance(cb.message, Message) else None
    if k == "forget":
        if v == "yes":
            async with app.db.session() as s:
                u = await s.get(User, user.id)
                if u is not None:
                    await repo.forget_user(s, u)
            await state.clear()
            if msg:
                await msg.answer(i18n.t("forget.done", lang))
        await cb.answer()
        return
    if k == "lang":
        if msg:
            await msg.answer(i18n.t("start.pick_language", lang), reply_markup=kb.languages())
    elif k == "cur" and not v:
        if msg:
            await msg.answer(
                i18n.t("settings.currency", lang), reply_markup=kb.choices("cur", [(c, c) for c in kb.CURRENCIES])
            )
    elif k == "cur":
        await _save(app, user, currency=v)
        await cb.answer(i18n.t("settings.saved", lang))
        return
    elif k == "clock":
        await _save(app, user, time_format="12h" if user.time_format == "24h" else "24h")
        await cb.answer(i18n.t("settings.saved", lang))
        return
    elif k == "city":
        await state.set_state(SettingsForm.city)
        if msg:
            await msg.answer(i18n.t("settings.ask_city", lang))
    elif k == "quiet" and not v:
        if msg:
            await msg.answer(
                i18n.t("settings.quiet_hours", lang, range="..."),
                reply_markup=kb.choices(
                    "quiet",
                    [
                        (i18n.t("settings.quiet_off", lang), "off"),
                        ("22:00-08:00", "22-8"),
                        ("23:00-07:00", "23-7"),
                        ("00:00-09:00", "0-9"),
                    ],
                ),
            )
    elif k == "quiet":
        if v == "off":
            await _save(app, user, quiet_start=None, quiet_end=None)
        else:
            a, b = (int(x) for x in v.split("-"))
            await _save(app, user, quiet_start=time(a), quiet_end=time(b))
        await cb.answer(i18n.t("settings.saved", lang))
        return
    elif k == "digest" and not v:
        if msg:
            await msg.answer(
                i18n.t("settings.digest", lang, mode="..."),
                reply_markup=kb.choices(
                    "digest", [(i18n.t(f"settings.digest.{m}", lang), m) for m in ("immediate", "daily", "weekly")]
                ),
            )
    elif k == "digest":
        await _save(app, user, digest_mode=v)
        await cb.answer(i18n.t("settings.saved", lang))
        return
    await cb.answer()


@router.message(SettingsForm.city, F.text)
async def on_settings_city(message: Message, app: App, user: User, state: FSMContext) -> None:
    place = await _set_home_from_text(app, user, message.text or "")
    if place is None:
        await message.answer(i18n.t("error.unknown_place", user.language, place=render.esc(message.text or "")))
        return
    await state.set_state(None)
    await message.answer(
        i18n.t("settings.saved", user.language)
        + " "
        + i18n.t("settings.city", user.language, city=render.esc(place.name))
    )
