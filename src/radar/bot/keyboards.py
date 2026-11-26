"""Inline keyboards and compact callback payloads (Telegram caps them at 64 bytes)."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from radar import i18n
from radar.domain.common import Domain, SortMode
from radar.domain.results import BookingMode, LinkKind, Result


class ResultCb(CallbackData, prefix="r"):
    a: str  # d details, m map, b book
    s: int
    i: str


class PageCb(CallbackData, prefix="p"):
    s: int
    p: int


class SortCb(CallbackData, prefix="o"):
    s: int
    m: str


class WatchCb(CallbackData, prefix="w"):
    s: int
    k: str  # "" ask, p price, a availability, n new results


class RadarCb(CallbackData, prefix="rd"):
    a: str  # p pause, r resume, d delete, t threshold
    r: int


class BookCb(CallbackData, prefix="b"):
    a: str  # c confirm, x cancel
    b: int
    n: str = ""


class LangCb(CallbackData, prefix="l"):
    c: str


class SetCb(CallbackData, prefix="st"):
    k: str
    v: str = ""


class WizCb(CallbackData, prefix="z"):
    st: str
    v: str


class MenuCb(CallbackData, prefix="m"):
    a: str


CURRENCIES = ["EUR", "USD", "AMD", "GEL", "KZT", "GBP", "CHF", "PLN", "CZK", "RUB"]


def main_menu(lang: str, webapp_url: str | None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=i18n.t("menu.search", lang), callback_data=MenuCb(a="search"))
    kb.button(text=i18n.t("menu.radars", lang), callback_data=MenuCb(a="radars"))
    kb.button(text=i18n.t("menu.settings", lang), callback_data=MenuCb(a="settings"))
    kb.button(text=i18n.t("menu.help", lang), callback_data=MenuCb(a="help"))
    if webapp_url:
        kb.button(text=i18n.t("menu.open_app", lang), web_app=WebAppInfo(url=webapp_url))
    kb.adjust(2)
    return kb.as_markup()


def languages() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for code, native in i18n.available().items():
        kb.button(text=native, callback_data=LangCb(c=code))
    kb.adjust(3)
    return kb.as_markup()


def location_request(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=i18n.t("location.button", lang), request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def results(sid: int, ids: list[str], page: int, pages: int, lang: str, start_index: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for n, rid in enumerate(ids, start=start_index):
        kb.button(text=str(n), callback_data=ResultCb(a="d", s=sid, i=rid))
    kb.adjust(len(ids) or 1)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="<", callback_data=PageCb(s=sid, p=page - 1).pack()))
    if page + 1 < pages:
        nav.append(
            InlineKeyboardButton(text=i18n.t("search.more", lang), callback_data=PageCb(s=sid, p=page + 1).pack())
        )
    if nav:
        kb.row(*nav)
    kb.row(
        InlineKeyboardButton(text=i18n.t("search.sort", lang), callback_data=SortCb(s=sid, m="").pack()),
        InlineKeyboardButton(text=i18n.t("search.watch", lang), callback_data=WatchCb(s=sid, k="").pack()),
    )
    return kb.as_markup()


def sort_modes(sid: int, domain: Domain, lang: str) -> InlineKeyboardMarkup:
    modes = [SortMode.BEST, SortMode.CHEAPEST]
    if domain.is_transport:
        modes += [SortMode.FASTEST, SortMode.EARLIEST, SortMode.FLEXIBLE]
    else:
        modes += [SortMode.RATED, SortMode.CLOSEST, SortMode.VALUE]
    kb = InlineKeyboardBuilder()
    for m in modes:
        kb.button(text=i18n.t(f"sort.{m.value}", lang), callback_data=SortCb(s=sid, m=m.value))
    kb.adjust(2)
    return kb.as_markup()


def result_actions(sid: int, r: Result, lang: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for kind, key in (
        (LinkKind.BOOKING, "result.book"),
        (LinkKind.WEBSITE, "result.website"),
        (LinkKind.MENU, "result.menu"),
    ):
        link = r.link(kind)
        if link and link.url.startswith("https://"):
            kb.button(text=i18n.t(key, lang), url=link.url)
    if r.booking is BookingMode.DIRECT:
        kb.button(text=i18n.t("result.book", lang), callback_data=ResultCb(a="b", s=sid, i=r.id))
    if r.point is not None:
        kb.button(text=i18n.t("result.map", lang), callback_data=ResultCb(a="m", s=sid, i=r.id))
    src = r.link(LinkKind.SOURCE)
    if src and src.url.startswith("https://"):
        kb.button(text=i18n.t("result.source_link", lang), url=src.url)
    kb.adjust(2)
    return kb.as_markup()


def watch_kinds(sid: int, lang: str, has_price: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if has_price:
        kb.button(
            text=i18n.t("radar.condition.price_below", lang, threshold="..."), callback_data=WatchCb(s=sid, k="p")
        )
    kb.button(text=i18n.t("radar.any_new", lang), callback_data=WatchCb(s=sid, k="n"))
    kb.button(text=i18n.t("radar.any_change", lang), callback_data=WatchCb(s=sid, k="a"))
    kb.adjust(1)
    return kb.as_markup()


def radar_actions(radar_id: int, state: str, lang: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if state in ("active", "error"):
        kb.button(text=i18n.t("radar.pause", lang), callback_data=RadarCb(a="p", r=radar_id))
    else:
        kb.button(text=i18n.t("radar.resume", lang), callback_data=RadarCb(a="r", r=radar_id))
    kb.button(text=i18n.t("radar.edit_threshold", lang), callback_data=RadarCb(a="t", r=radar_id))
    kb.button(text=i18n.t("radar.delete", lang), callback_data=RadarCb(a="d", r=radar_id))
    kb.adjust(3)
    return kb.as_markup()


def booking_confirm(booking_id: int, nonce: str, lang: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=i18n.t("booking.confirm", lang), callback_data=BookCb(a="c", b=booking_id, n=nonce))
    kb.button(text=i18n.t("booking.cancel", lang), callback_data=BookCb(a="x", b=booking_id))
    kb.adjust(1)
    return kb.as_markup()


def url_button(text: str, url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, url=url)]])


def settings(lang: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=i18n.t("settings.language", lang), callback_data=SetCb(k="lang"))
    kb.button(text=i18n.t("settings.currency", lang), callback_data=SetCb(k="cur"))
    kb.button(text=i18n.t("settings.time_format", lang), callback_data=SetCb(k="clock"))
    kb.button(text=i18n.t("settings.city", lang, city="..."), callback_data=SetCb(k="city"))
    kb.button(text=i18n.t("settings.quiet_hours", lang, range="..."), callback_data=SetCb(k="quiet"))
    kb.button(text=i18n.t("settings.digest", lang, mode="..."), callback_data=SetCb(k="digest"))
    kb.adjust(2)
    return kb.as_markup()


def choices(key: str, options: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for label, value in options:
        kb.button(text=label, callback_data=SetCb(k=key, v=value))
    kb.adjust(3)
    return kb.as_markup()


def wizard_domains(lang: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for d in (
        Domain.DINING,
        Domain.STAY,
        Domain.RAIL,
        Domain.FLIGHT,
        Domain.EVENT,
        Domain.WELLNESS,
        Domain.GROUND,
        Domain.REAL_ESTATE,
        Domain.PLAN,
    ):
        kb.button(text=i18n.t(f"domain.{d.value}", lang), callback_data=WizCb(st="d", v=d.value))
    kb.adjust(2)
    return kb.as_markup()


def wizard_options(step: str, options: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for label, value in options:
        kb.button(text=label, callback_data=WizCb(st=step, v=value))
    kb.adjust(3)
    return kb.as_markup()
