"""Button-driven search builder.

One panel message that edits itself step by step: category, place, date
(with a calendar), time, guests, filters, then Search. Nothing has to be
typed; the only text input is an optional city name.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, time, timedelta
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, ReplyKeyboardRemove

from radar import i18n
from radar.app import App
from radar.bot import keyboards as kb
from radar.bot import render, ui
from radar.bot.handlers.search import execute
from radar.core.timeutil import now_in, tz
from radar.domain.common import DateWindow, Domain, LocationRef, Place, SortMode
from radar.domain.intents import SearchIntent
from radar.nlu.rules import _required_missing
from radar.search.draft import CATEGORIES, FILTERS, GRID, SORTS, STEPS, build_intent, draft_from_intent
from radar.storage import repo
from radar.storage.models import User

router = Router(name="builder")


class SB(CallbackData, prefix="sb"):
    a: str
    v: str = ""


class BuilderInput(StatesGroup):
    city = State()


def T(key: str, lang: str, **kw: Any) -> str:
    return i18n.t(key, lang, **kw)


def cat_label(cat: str, lang: str) -> str:
    return T(f"cat.{cat}", lang)


def cb(a: str, v: str = "") -> str:
    return SB(a=a, v=v).pack()


# --- state ------------------------------------------------------------------------------------------


async def draft(state: FSMContext) -> dict[str, Any]:
    return dict((await state.get_data()).get("sb") or {})


async def save(state: FSMContext, d: dict[str, Any]) -> None:
    await state.update_data(sb=d)


def next_step(d: dict[str, Any]) -> str:
    dom = Domain(d["domain"])
    for st in STEPS[dom]:
        if st == "filters":
            return "filters"
        if st not in d:
            return st
    return "filters"


def places(user: User, ctx_current: dict[str, Any] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in [ctx_current, user.home_place, *(user.favorites or [])]:
        if p and p.get("canonical_id") not in {x.get("canonical_id") for x in out}:
            out.append(p)
    return out[:6]


# --- rendering --------------------------------------------------------------------------------------


def _when_label(d: dict[str, Any], lang: str, clock: str) -> str:
    if "day" not in d:
        return ""
    day = date.fromisoformat(d["day"])
    s = i18n.fdate(day, lang, "EEE d MMM")
    if d.get("time"):
        s += ", " + i18n.ftime(time.fromisoformat(d["time"]), lang, clock)
    if d.get("nights"):
        s += " + " + T("result.nights", lang, count=d["nights"])
    return s


def summary_rows(d: dict[str, Any], lang: str, clock: str) -> list[list[str]]:
    rows: list[list[str]] = []
    if d.get("cat") and d["cat"] != "any":
        rows.append([T("sb.what", lang), ui.esc(cat_label(d["cat"], lang))])
    if d.get("city"):
        rows.append([T("sb.where", lang), ui.esc(d["city"]["name"])])
    if d.get("origin"):
        rows.append([T("sb.from", lang), ui.esc(d["origin"]["name"])])
    if d.get("dest"):
        rows.append([T("sb.to", lang), ui.esc(d["dest"]["name"])])
    if w := _when_label(d, lang, clock):
        rows.append([T("sb.when", lang), w])
    if d.get("pax"):
        rows.append([T("sb.who", lang), T("wizard.party", lang, count=d["pax"])])
    if d.get("rooms"):
        rows.append([T("sb.rooms", lang), f"{d['rooms']}+"])
    if d.get("flags"):
        rows.append([T("sb.must", lang), ", ".join(T(f"constraint.{f}", lang) for f in d["flags"])])
    return rows


def panel(d: dict[str, Any], user: User, current: dict[str, Any] | None) -> tuple[ui.Rich, str, InlineKeyboardMarkup]:
    lang, clock = user.language, user.time_format
    dom = Domain(d["domain"])
    step = d.get("step") or next_step(d)
    r = ui.Rich().h(T(f"domain.{dom.value}", lang), 2)
    rows = summary_rows(d, lang, clock)
    if rows:
        r.table(rows, striped=False, compact=True)
    prompt = T(f"sb.ask.{step}", lang)
    r.p(f"<b>{prompt}</b>")
    fallback = (
        f"<b>{T(f'domain.{dom.value}', lang)}</b>\n" + "\n".join(f"{a}: {b}" for a, b in rows) + f"\n\n<b>{prompt}</b>"
    )
    return r, fallback, keyboard(step, d, user, current)


def _grid(items: list[tuple[str, str, str | None]], cols: int) -> list[list[tuple[str, str, str | None]]]:
    return [items[i : i + cols] for i in range(0, len(items), cols)]


def keyboard(step: str, d: dict[str, Any], user: User, current: dict[str, Any] | None) -> InlineKeyboardMarkup:
    lang = user.language
    dom = Domain(d["domain"])
    rows: list[list[tuple[str, str, str | None]]] = []
    if step == "cat":
        items = [
            (cat_label(c, lang), cb("cat", c), "primary" if d.get("cat") == c else None)
            for c in CATEGORIES.get(dom, [])
        ]
        rows = _grid(items, 2 if dom in (Domain.PLAN,) else 3)
    elif step in ("city", "origin", "dest"):
        ps = places(user, current)
        items = [(p["name"][:30], cb(step, str(i)), None) for i, p in enumerate(ps)]
        rows = _grid(items, 3)
        rows.append(
            [(T("sb.near_me", lang), cb(step, "near"), None), (T("sb.other_city", lang), cb(step, "type"), None)]
        )
    elif step == "day":
        z = tz(user.timezone)
        today = now_in(user.timezone).date()
        sat = today + timedelta(days=(5 - today.weekday()) % 7)
        rows = [
            [
                (T("search.today", lang), cb("day", today.isoformat()), None),
                (T("search.tomorrow", lang), cb("day", (today + timedelta(days=1)).isoformat()), None),
            ],
            [
                (i18n.fdate(sat, lang, "EEE d MMM"), cb("day", sat.isoformat()), None),
                (T("sb.pick_date", lang), cb("cal", today.strftime("%Y-%m")), "primary"),
            ],
        ]
        del z
    elif step == "cal":
        rows = calendar_rows(d.get("cal") or now_in(user.timezone).strftime("%Y-%m"), user)
    elif step == "time":
        if dom.is_transport:
            slots = ["06:00", "08:00", "10:00", "12:00", "14:00", "16:00", "18:00", "20:00", "22:00"]
        else:
            slots = ["09:00", "11:00", "12:00", "13:00", "14:00", "17:00", "18:00", "19:00", "20:00", "21:00", "22:00"]
        items = [
            (
                s if user.time_format == "24h" else i18n.ftime(time.fromisoformat(s), lang, "12h"),
                cb("time", s.replace(":", "")),
                "primary" if d.get("time") == s else None,
            )
            for s in slots
        ]
        items.append((T("sb.any_time", lang), cb("time", "any"), None))
        rows = _grid(items, 4)
    elif step == "pax":
        rows = _grid(
            [(str(n), cb("pax", str(n)), "primary" if d.get("pax") == n else None) for n in (1, 2, 3, 4, 5, 6, 8, 10)],
            4,
        )
    elif step == "nights":
        rows = _grid(
            [(T("result.nights", lang, count=n), cb("nights", str(n)), None) for n in (1, 2, 3, 4, 5, 7, 10, 14)], 4
        )
    elif step == "rooms":
        rows = _grid([(f"{n}+", cb("rooms", str(n)), None) for n in (1, 2, 3, 4)], 4)
    elif step == "filters":
        on = set(d.get("flags", []))
        items = [
            (T(f"constraint.{f}", lang), cb("flag", f), "success" if f in on else None) for f in FILTERS.get(dom, [])
        ]
        rows = _grid(items, 2)
        sort = d.get("sort", "best")
        rows.append(
            [
                (T(f"sort.{m.value}", lang), cb("sort", m.value), "primary" if sort == m.value else None)
                for m in SORTS[dom.is_transport]
            ]
        )
        rows.append([(T("search.go", lang), cb("go"), "success")])
    nav: list[tuple[str, str, str | None]] = [
        (T("common.back", lang), cb("back"), None),
        (T("common.cancel", lang), cb("x"), "danger"),
    ]
    rows.append(nav)
    return ui.kb(rows)


def calendar_rows(month: str, user: User) -> list[list[tuple[str, str, str | None]]]:
    lang = user.language
    y, m = (int(x) for x in month.split("-"))
    today = now_in(user.timezone).date()
    first = date(y, m, 1)
    rows: list[list[tuple[str, str, str | None]]] = []
    prev_m = (first - timedelta(days=1)).strftime("%Y-%m")
    next_m = (first + timedelta(days=32)).replace(day=1).strftime("%Y-%m")
    can_prev = (y, m) > (today.year, today.month)
    rows.append(
        [
            ("<", cb("cal", prev_m) if can_prev else cb("noop"), None),
            (i18n.fdate(first, lang, "LLLL y"), cb("noop"), None),
            (">", cb("cal", next_m), None),
        ]
    )
    rows.append(
        [
            (i18n.fdate(first + timedelta(days=(i - first.weekday()) % 7), lang, "EEEEE"), cb("noop"), None)
            for i in range(7)
        ]
    )
    for week in calendar.Calendar(firstweekday=0).monthdayscalendar(y, m):
        row: list[tuple[str, str, str | None]] = []
        for dnum in week:
            if dnum == 0:
                row.append((" ", cb("noop"), None))
                continue
            dd = date(y, m, dnum)
            if dd < today:
                row.append((str(dnum), cb("noop"), None))
            else:
                row.append((str(dnum), cb("day", dd.isoformat()), "primary" if dd == today else None))
        rows.append(row)
    return rows


async def show(target: Message, state: FSMContext, user: User, *, edit: bool) -> None:
    d = await draft(state)
    current = (await state.get_data()).get("current")
    r, fallback, markup = panel(d, user, current)
    msg = await (ui.edit(target, r, fallback, markup) if edit else ui.send(target, r, fallback, markup))
    d["panel"] = msg.message_id
    await save(state, d)


# --- entry points -----------------------------------------------------------------------------------


async def home_view(app: App, user: User) -> tuple[ui.Rich, str, InlineKeyboardMarkup]:
    async with app.db.session() as s:
        u = await s.get(User, user.id)
        rows = await repo.recent_searches(s, user, 3)
    recent = []
    for row in rows:
        label = render.intent_summary(SearchIntent.model_validate(row.intent), user.language).split("\n")[0]
        recent.append((row.id, re.sub(r"<[^>]+>", "", label)[:48]))
    return home(u or user, app.settings.webapp_url, recent)


def home(
    user: User, webapp_url: str | None, recent: list[tuple[int, str]] | None = None
) -> tuple[ui.Rich, str, InlineKeyboardMarkup]:
    lang = user.language
    city = (user.home_place or {}).get("name")
    r = ui.Rich().h("RADAR", 1).p(f"<i>{T('home.tagline', lang)}</i>")
    if city:
        r.p(T("home.city", lang, city=ui.esc(city)))
    r.p(T("home.hint", lang))
    items = [
        (
            T(f"domain.{d.value}", lang),
            cb("dom", d.value),
            "primary" if d in (Domain.DINING, Domain.STAY, Domain.RAIL) else None,
        )
        for d in GRID
    ]
    rows = _grid(items, 3)
    rows.append([(T("home.near_me_now", lang), cb("near"), "success")])
    if recent:
        r.h(T("home.recent", lang), 4)
        for sid, label in recent:
            rows.append([(label, cb("rerun", str(sid)), None)])
    rows.append(
        [
            (T("menu.radars", lang), kb.MenuCb(a="radars").pack(), None),
            (T("menu.settings", lang), kb.MenuCb(a="settings").pack(), None),
        ]
    )
    markup = ui.kb(rows)
    if webapp_url:
        from aiogram.types import InlineKeyboardButton, WebAppInfo

        markup.inline_keyboard.append(
            [InlineKeyboardButton(text=T("menu.open_app", lang), web_app=WebAppInfo(url=webapp_url), style="success")]
        )
    fallback = f"<b>RADAR</b>\n<i>{T('home.tagline', lang)}</i>\n\n{T('home.hint', lang)}"
    return r, fallback, markup


@router.message(Command("search", "menu"))
async def cmd_home(message: Message, app: App, user: User, state: FSMContext) -> None:
    await state.set_state(None)
    r, fb, markup = await home_view(app, user)
    await ui.send(message, r, fb, markup)


@router.callback_query(kb.MenuCb.filter(F.a.in_({"search", "home"})))
async def menu_home(cb_: CallbackQuery, app: App, user: User, state: FSMContext) -> None:
    await state.set_state(None)
    if isinstance(cb_.message, Message):
        r, fb, markup = await home_view(app, user)
        await ui.edit(cb_.message, r, fb, markup)
    await cb_.answer()


@router.callback_query(SB.filter())
async def on_sb(cq: CallbackQuery, callback_data: SB, app: App, user: User, state: FSMContext) -> None:
    msg = cq.message if isinstance(cq.message, Message) else None
    if msg is None:
        await cq.answer()
        return
    a, v = callback_data.a, callback_data.v
    if a == "noop":
        await cq.answer()
        return
    d = await draft(state)
    data = await state.get_data()
    current = data.get("current")
    if a == "x":
        await save(state, {})
        r, fb, markup = await home_view(app, user)
        await ui.edit(msg, r, fb, markup)
        await cq.answer()
        return
    if a in ("edit", "rerun"):
        async with app.db.session() as s:
            row = await repo.get_search(s, user, int(v)) if v.isdigit() else None
        if row is None:
            await cq.answer(T("error.not_found", user.language), show_alert=True)
            return
        intent = SearchIntent.model_validate(row.intent)
        await cq.answer()
        if a == "rerun":
            await execute(msg, app, user, intent)
            return
        d = draft_from_intent(intent)
        d["step"] = "filters"
        await save(state, d)
        await show(msg, state, user, edit=False)
        return
    if a == "near":
        await cq.answer()
        if current:
            await execute(msg, app, user, near_now_intent(Place.model_validate(current), user))
            return
        await save(state, {"domain": Domain.DINING.value, "await": "nearnow"})
        await msg.answer(T("search.share_location", user.language), reply_markup=kb.location_request(user.language))
        return
    if a == "dom":
        d = {"domain": v, "flags": [], "sort": "best"}
        if Domain(v) not in CATEGORIES:
            d["cat"] = "any"
    elif not d.get("domain"):
        await cq.answer(T("error.not_found", user.language), show_alert=True)
        return
    dom = Domain(d["domain"])
    if a == "back":
        order = STEPS[dom]
        done = [s for s in order if s in d and s != "filters"]
        if d.get("step") in ("cal",):
            d["step"] = "day"
        elif done:
            d.pop(done[-1], None)
            d.pop("step", None)
        else:
            r, fb, markup = await home_view(app, user)
            await ui.edit(msg, r, fb, markup)
            await save(state, {})
            await cq.answer()
            return
    elif a == "cat":
        d["cat"] = v
    elif a in ("city", "origin", "dest"):
        if v == "near":
            d["step"] = a
            await save(state, {**d, "await": a})
            await msg.answer(T("location.ask", user.language), reply_markup=kb.location_request(user.language))
            await cq.answer()
            return
        if v == "type":
            await save(state, {**d, "await": a})
            await state.set_state(BuilderInput.city)
            await msg.answer(T("settings.ask_city", user.language))
            await cq.answer()
            return
        ps = places(user, current)
        idx = int(v)
        if idx < len(ps):
            d[a] = ps[idx]
    elif a == "cal":
        d["cal"] = v
        d["step"] = "cal"
    elif a == "day":
        d["day"] = v
        d.pop("step", None)
    elif a == "time":
        d["time"] = None if v == "any" else f"{v[:2]}:{v[2:]}"
    elif a == "pax":
        d["pax"] = int(v)
    elif a == "nights":
        d["nights"] = int(v)
    elif a == "rooms":
        d["rooms"] = int(v)
    elif a == "flag":
        flags = list(d.get("flags", []))
        flags.remove(v) if v in flags else flags.append(v)
        d["flags"] = flags
    elif a == "sort":
        d["sort"] = v
    elif a == "go":
        await cq.answer()
        intent = build_intent(d, user)
        intent.missing = _required_missing(intent)
        if intent.missing:
            d.pop("step", None)
            await save(state, d)
            await show(msg, state, user, edit=True)
            return
        await save(state, {})
        await execute(msg, app, user, intent, edit_message=msg)
        return
    if a not in ("cal",):
        d.pop("step", None) if a != "back" else None
    if "step" not in d:
        d["step"] = next_step(d)
    await save(state, d)
    await show(msg, state, user, edit=True)
    await cq.answer()


@router.message(BuilderInput.city, F.text)
async def on_city_text(message: Message, app: App, user: User, state: FSMContext) -> None:
    d = await draft(state)
    field = d.pop("await", "city")
    ref = await app.resolver.resolve(message.text or "", language=user.language)
    if ref.place is None:
        await message.answer(T("error.unknown_place", user.language, place=ui.esc(message.text or "")))
        return
    await state.set_state(None)
    d[field] = ref.place.model_dump(mode="json")
    d.pop("step", None)
    d["step"] = next_step(d)
    await save(state, d)
    await show(message, state, user, edit=False)


async def on_builder_location(message: Message, app: App, state: FSMContext, user: User, place: Place) -> bool:
    """Called by the location handler; returns True if the builder consumed it."""
    d = await draft(state)
    field = d.pop("await", None)
    if not field or not d.get("domain"):
        return False
    if field == "nearnow":
        await save(state, {})
        await message.answer(T("location.saved", user.language), reply_markup=ReplyKeyboardRemove())
        await execute(message, app, user, near_now_intent(place, user))
        return True
    d[field] = place.model_dump(mode="json")
    d.pop("step", None)
    d["step"] = next_step(d)
    await save(state, d)
    await message.answer("OK", reply_markup=ReplyKeyboardRemove())
    await show(message, state, user, edit=False)
    return True


def near_now_intent(place: Place, user: User) -> SearchIntent:
    """Restaurants and cafes around the user that are open right now."""
    now = now_in(place.timezone or user.timezone).replace(second=0, microsecond=0)
    intent = SearchIntent(
        domain=Domain.DINING,
        language=user.language,
        location=LocationRef(text=place.name, place=place, radius_m=1500),
        when=DateWindow(start=now, end=now),
        sort=SortMode.CLOSEST,
    )
    intent.set_requirement("open_at")
    return intent
