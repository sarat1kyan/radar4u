"""Booking from a result card: revalidate, show summary, explicit confirm."""

from __future__ import annotations

import contextlib

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from radar import i18n
from radar.app import App
from radar.booking.service import REQUIRED_FIELDS, BookingState
from radar.bot import keyboards as kb
from radar.bot import render
from radar.bot.handlers.search import load_outcome, search_ctx
from radar.core.errors import BookingError
from radar.core.money import Money
from radar.domain.results import Result
from radar.storage.models import Booking, User

router = Router(name="booking")


class TravellerForm(StatesGroup):
    field = State()


def _summary(b: Booking, lang: str) -> str:
    r = Result.model_validate(b.result)
    lines = [f"<b>{render.esc(r.title, 120)}</b>"]
    if r.subtitle:
        lines.append(render.esc(r.subtitle, 120))
    if r.starts_at:
        lines.append(i18n.fdatetime(r.starts_at, lang))
    conditions = []
    for key in ("free_cancellation", "refundable", "breakfast", "checked_bag"):
        v = r.attr(key)
        if v.value != "unknown":
            answer = i18n.t("common.yes" if v.value == "yes" else "common.no", lang)
            conditions.append(f"{render.constraint_label(key, lang)}: {answer}")
    if r.price and r.price.payable_at_property:
        conditions.append(i18n.t("result.pay_at_property", lang, amount=i18n.money(r.price.payable_at_property, lang)))
    price = (
        i18n.money(Money.of(b.price_amount, b.price_currency), lang)
        if b.price_amount and b.price_currency
        else i18n.t("common.unknown", lang)
    )
    text = i18n.t(
        "booking.summary",
        lang,
        summary="\n".join(lines),
        price=price,
        conditions="; ".join(conditions) or i18n.t("common.unknown", lang),
    )
    if r.provenance.freshness.value == "fixture":
        text += "\n<b>" + i18n.t("result.fixture", lang) + "</b>"
    return text


@router.callback_query(kb.ResultCb.filter(F.a == "b"))
async def on_book(cb: CallbackQuery, callback_data: kb.ResultCb, app: App, user: User) -> None:
    lang = user.language
    outcome = await load_outcome(app, user, callback_data.s)
    item = outcome.find(callback_data.i) if outcome else None
    if item is None or not isinstance(cb.message, Message):
        await cb.answer(i18n.t("error.not_found", lang), show_alert=True)
        return
    await cb.answer(i18n.t("booking.revalidating", lang))
    b = await app.bookings.start(user, item.result, search_ctx(user))
    if b.state == BookingState.HANDOFF.value:
        if b.handoff_url:
            await cb.message.answer(
                i18n.t("booking.handoff", lang),
                reply_markup=kb.url_button(i18n.t("result.website", lang), b.handoff_url),
            )
        else:
            await cb.message.answer(i18n.t("booking.not_enabled", lang))
        return
    if b.state == BookingState.FAILED.value:
        await cb.message.answer(i18n.t("booking.unavailable", lang))
        return
    if app.bookings.price_changed(b) and item.result.price is not None and b.price_amount and b.price_currency:
        await cb.message.answer(
            i18n.t(
                "booking.price_changed",
                lang,
                old=i18n.money(item.result.price.amount, lang),
                new=i18n.money(Money.of(b.price_amount, b.price_currency), lang),
            )
        )
    await cb.message.answer(_summary(b, lang), reply_markup=kb.booking_confirm(b.id, b.confirm_nonce or "", lang))


@router.callback_query(kb.BookCb.filter())
async def on_book_action(cb: CallbackQuery, callback_data: kb.BookCb, app: App, user: User, state: FSMContext) -> None:
    lang = user.language
    msg = cb.message if isinstance(cb.message, Message) else None
    if callback_data.a == "x":
        await app.bookings.cancel(user, callback_data.b)
        await cb.answer()
        if msg:
            await msg.edit_reply_markup(reply_markup=None)
            await msg.answer(i18n.t("booking.cancelled", lang))
        return
    async with app.db.session() as s:
        b = await s.get(Booking, callback_data.b)
    if b is None or b.user_id != user.id:
        await cb.answer(i18n.t("error.not_found", lang), show_alert=True)
        return
    fields = list(REQUIRED_FIELDS.get(b.provider, ()))
    await cb.answer()
    if msg:
        await msg.edit_reply_markup(reply_markup=None)
    if fields:
        await state.set_state(TravellerForm.field)
        await state.update_data(bk={"id": b.id, "nonce": callback_data.n, "fields": fields, "values": {}})
        if msg:
            await msg.answer(i18n.t("booking.details_needed", lang, provider=render.esc(b.provider)))
            await msg.answer(i18n.t("booking.ask_field", lang, field=i18n.t(f"booking.field.{fields[0]}", lang)))
        return
    if msg:
        await _submit(msg, app, user, b.id, callback_data.n, {})


@router.message(TravellerForm.field, F.text)
async def on_traveller_field(message: Message, app: App, user: User, state: FSMContext) -> None:
    lang = user.language
    data = (await state.get_data()).get("bk") or {}
    fields: list[str] = data.get("fields", [])
    values: dict[str, str] = data.get("values", {})
    current = fields[len(values)]
    values[current] = (message.text or "").strip()[:100]
    # Traveller details never stay in the chat history longer than needed.
    with contextlib.suppress(Exception):  # best effort: old messages or missing rights
        await message.delete()
    if len(values) < len(fields):
        await state.update_data(bk={**data, "values": values})
        await message.answer(
            i18n.t("booking.ask_field", lang, field=i18n.t(f"booking.field.{fields[len(values)]}", lang))
        )
        return
    await state.update_data(bk=None)
    await state.set_state(None)
    await _submit(message, app, user, int(data["id"]), str(data["nonce"]), values)


async def _submit(msg: Message, app: App, user: User, booking_id: int, nonce: str, values: dict[str, str]) -> None:
    lang = user.language
    try:
        b = await app.bookings.confirm(user, booking_id, nonce, values)
    except BookingError as exc:
        await msg.answer(i18n.t("booking.failed", lang, reason=render.esc(str(exc), 200)))
        return
    if b.state == BookingState.CONFIRMED.value:
        await msg.answer(i18n.t("booking.confirmed", lang, reference=render.esc(b.reference or "-")))
    elif b.state == BookingState.SUBMITTED.value:
        await msg.answer(i18n.t("booking.submitted", lang, provider=render.esc(b.provider)))
    else:
        await msg.answer(
            i18n.t("booking.failed", lang, reason=render.esc(b.events[-1].get("error", "-") if b.events else "-"))
        )
