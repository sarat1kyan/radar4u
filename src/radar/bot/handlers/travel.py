"""Travel mode: one screen for "I just landed somewhere".

Share a location (or tap Here and now) and the bot answers with local time,
weather, the local currency and its rate, the emergency number, and a grid
of one-tap actions: food that is open now, cash, pharmacy, SIM, the way to
the airport or main station, a currency converter.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal
from typing import Any

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from radar import i18n, travel
from radar.app import App
from radar.bot import keyboards as kb
from radar.bot import ui
from radar.bot.handlers.search import execute
from radar.core.errors import ProviderError
from radar.core.money import Money
from radar.core.timeutil import now_in
from radar.domain.common import (
    DateWindow,
    Domain,
    LocationRef,
    Party,
    Place,
    PlaceKind,
    SortMode,
    StayDates,
)
from radar.domain.intents import SearchIntent
from radar.geo.resolver import GAZ
from radar.nlu.fx_query import parse_conversion
from radar.providers.osm_places import OsmPlacesProvider
from radar.storage.models import User

log = logging.getLogger(__name__)
router = Router(name="travel")


class TV(CallbackData, prefix="tv"):
    a: str
    v: str = ""


class TravelInput(StatesGroup):
    route_to = State()
    fx = State()


def T(key: str, lang: str, **kw: Any) -> str:
    return i18n.t(key, lang, **kw)


def cb(a: str, v: str = "") -> str:
    return TV(a=a, v=v).pack()


# key -> (icon, domain, categories, radius_m, open now, sort)
QUICK: dict[str, tuple[str, Domain, list[str], int, bool, SortMode]] = {
    "eat": ("🍽", Domain.DINING, [], 1500, True, SortMode.BEST),
    "coffee": ("☕", Domain.DINING, ["place:cafe"], 1200, True, SortMode.CLOSEST),
    "bar": ("🍸", Domain.DINING, ["place:bar", "place:pub"], 1500, True, SortMode.BEST),
    "sights": ("🏛", Domain.EVENT, ["event:sights"], 3000, False, SortMode.BEST),
    "hotel": ("🏨", Domain.STAY, [], 3000, False, SortMode.BEST),
    "supermarket": ("🛒", Domain.ESSENTIALS, ["place:supermarket"], 1500, True, SortMode.CLOSEST),
    "pharmacy": ("💊", Domain.ESSENTIALS, ["place:pharmacy"], 2000, True, SortMode.CLOSEST),
    "atm": ("🏧", Domain.ESSENTIALS, ["place:atm"], 1200, False, SortMode.CLOSEST),
    "exchange": ("💱", Domain.ESSENTIALS, ["place:exchange"], 2500, True, SortMode.CLOSEST),
    "toilets": ("🚻", Domain.ESSENTIALS, ["place:toilets"], 1200, False, SortMode.CLOSEST),
    "hospital": ("🏥", Domain.ESSENTIALS, ["place:hospital"], 6000, False, SortMode.CLOSEST),
    "police": ("👮", Domain.ESSENTIALS, ["place:police"], 4000, False, SortMode.CLOSEST),
    "sim": ("📶", Domain.ESSENTIALS, ["place:sim"], 2500, True, SortMode.CLOSEST),
    "fuel": ("⛽", Domain.ESSENTIALS, ["place:fuel"], 4000, False, SortMode.CLOSEST),
    "laundry": ("🧺", Domain.ESSENTIALS, ["place:laundry"], 3000, True, SortMode.CLOSEST),
    "embassy": ("🏳", Domain.ESSENTIALS, ["place:embassy"], 15000, False, SortMode.CLOSEST),
}
GRID = [
    ["eat", "coffee", "bar"],
    ["sights", "hotel", "supermarket"],
    ["pharmacy", "atm", "exchange"],
    ["toilets", "hospital", "police"],
    ["sim", "fuel", "laundry"],
]
WEATHER_ICON = {"clear": "☀", "cloudy": "☁", "fog": "🌫", "rain": "🌧", "snow": "🌨", "storm": "⛈"}


def quick_intent(key: str, place: Place, user: User) -> SearchIntent:
    _icon, domain, cats, radius, open_now, sort = QUICK[key]
    zone = place.timezone or user.timezone
    now = now_in(zone).replace(second=0, microsecond=0)
    intent = SearchIntent(
        domain=domain,
        language=user.language,
        categories=list(cats),
        location=LocationRef(text=place.name, place=place, radius_m=radius),
        sort=sort,
    )
    if domain is Domain.STAY:
        intent.stay = StayDates(check_in=now.date(), check_out=now.date() + timedelta(days=1))
        intent.party = Party(adults=1)
    elif open_now:
        intent.when = DateWindow(start=now, end=now)
        intent.set_requirement("open_at")
    return intent


def town_of(place: Place) -> str:
    """'Kentron, Yerevan' -> 'Yerevan' (reverse geocoding names district first)."""
    return place.name.split(",")[-1].strip() or place.name


# --- dashboard --------------------------------------------------------------------------------------


def _offset_label(hours: float | None, lang: str) -> str | None:
    if hours is None or abs(hours) < 0.01:
        return None
    sign = "+" if hours > 0 else "−"
    h = abs(hours)
    val = f"{int(h)}" if h == int(h) else f"{h:g}"
    return T("travel.offset", lang, offset=f"{sign}{val}")


def dashboard(snap: travel.Snapshot, user: User) -> tuple[ui.Rich, str, InlineKeyboardMarkup]:
    lang, clock = user.language, user.time_format
    p = snap.place
    r = ui.Rich().h(f"📍 {ui.esc(p.name, 80)}", 2)
    rows: list[list[str]] = []
    when = f"<b>{i18n.ftime(snap.local_time, lang, clock)}</b>, {i18n.fdate(snap.local_time, lang, 'EEE d MMM')}"
    if off := _offset_label(snap.offset_h, lang):
        when += f" <i>({off})</i>"
    rows.append([T("travel.local_time", lang), when])
    if (w := snap.weather) is not None and w.temperature is not None:
        icon = WEATHER_ICON.get(w.condition, "")
        txt = f"{icon} <b>{round(w.temperature)}°</b> {T(f'weather.{w.condition}', lang)}"
        if w.feels_like is not None and abs(w.feels_like - w.temperature) >= 2:
            txt += ", " + T("travel.feels_like", lang, t=round(w.feels_like))
        rows.append([T("travel.weather", lang), txt])
    if snap.currency:
        cur = snap.currency
        if snap.rate is not None:
            one = Money(amount=Decimal(1), currency=user.currency)
            conv = snap.rate.convert(one).converted
            cur += f" · 1 {user.currency} = {i18n.money(conv, lang)}"
        rows.append([T("travel.currency", lang), cur])
    e = snap.emergency
    if e:
        bits = []
        if "all" in e:
            bits.append(f"<b>{e['all']}</b>")
        if "police" in e:
            bits.append(f"{T('travel.police', lang)} <b>{e['police']}</b>")
        if "ambulance" in e:
            bits.append(f"{T('travel.ambulance', lang)} <b>{e['ambulance']}</b>")
        if "alt" in e:
            bits.append(f"<i>{e['alt']}</i>")
        rows.append([T("travel.emergency", lang), " · ".join(bits)])
    else:
        rows.append([T("travel.emergency", lang), T("travel.emergency_unknown", lang)])
    r.table(rows, striped=False, compact=True)
    r.map(p.point.lat, p.point.lon, 15)
    r.p(f"<i>{T('travel.hint', lang)}</i>")
    r.footer(T("travel.sources", lang))

    btn: list[list[tuple[str, str, str | None]]] = []
    for row in GRID:
        btn.append([(f"{QUICK[k][0]} {T(f'travel.q.{k}', lang)}", cb("q", k), None) for k in row])
    btn.append(
        [
            (f"✈ {T('travel.to_airport', lang)}", cb("go", "airport"), "primary"),
            (f"🚉 {T('travel.to_station', lang)}", cb("go", "station"), "primary"),
        ]
    )
    btn.append(
        [
            (f"🧭 {T('travel.route_to', lang)}", cb("go", "ask"), None),
            (f"💱 {T('travel.convert', lang)}", cb("fx"), None),
        ]
    )
    sos = travel.emergency_primary(p.country_code)
    btn.append(
        [
            (f"🆘 {T('travel.copy_sos', lang, number=sos)}", f"copy:{sos}", "danger"),
            (f"🏳 {T('travel.q.embassy', lang)}", cb("q", "embassy"), None),
        ]
    )
    btn.append(
        [
            (f"🔄 {T('travel.refresh', lang)}", cb("home"), None),
            (f"🏠 {T('travel.home', lang)}", kb.MenuCb(a="home").pack(), None),
        ]
    )
    fallback = f"<b>{ui.esc(p.name)}</b>\n" + "\n".join(f"{a}: {b}" for a, b in rows if not b.startswith("<i>"))
    return r, fallback, ui.kb(btn)


async def show_dashboard(target: Message, app: App, user: User, place: Place, *, edit: bool = False) -> None:
    snap = await travel.snapshot(
        place, home_tz=user.timezone or "UTC", user_currency=user.currency, weather=app.weather, fx=app.fx
    )
    r, fb, markup = dashboard(snap, user)
    await (ui.edit(target, r, fb, markup) if edit else ui.send(target, r, fb, markup))


async def current_place(state: FSMContext) -> Place | None:
    cur = (await state.get_data()).get("current")
    return Place.model_validate(cur) if cur else None


async def ask_location(msg: Message, state: FSMContext, user: User, then: str) -> None:
    await state.update_data(travel_then=then)
    await msg.answer(T("travel.share_location", user.language), reply_markup=kb.location_request(user.language))


# --- routes -----------------------------------------------------------------------------------------


async def nearest_airport(app: App, place: Place) -> Place | None:
    osm = app.registry.get("osm")
    if not isinstance(osm, OsmPlacesProvider):
        return None
    try:
        found = await osm.nearest(place.point, '["aeroway"="aerodrome"]["iata"]', 70_000, limit=40)
    except ProviderError:
        return None
    # The city's own airport (known IATA codes from the gazetteer) beats a closer one across a border;
    # then international airports; then distance.
    city = GAZ.nearest(place.point)
    known = set(city.iata) if city else set()

    def score(f: dict[str, Any]) -> tuple[int, int, float]:
        tags = f["tags"]
        intl = "international" in (tags.get("aerodrome", "") + tags.get("aerodrome:type", ""))
        return (0 if tags["iata"] in known else 1, 0 if intl else 1, float(f["distance_m"]))

    usable = [
        f for f in found if f["tags"].get("aerodrome") not in ("private", "military") and not f["tags"].get("military")
    ]
    if not usable:
        return None
    best = min(usable, key=score)
    tags = best["tags"]
    return Place(
        canonical_id=f"iata:{tags['iata']}",
        name=tags.get("name:en") or tags.get("name") or tags["iata"],
        kind=PlaceKind.AIRPORT,
        country_code=place.country_code,
        point=best["point"],
        timezone=place.timezone,
        iata=tags["iata"],
        source="osm",
    )


def route_intent(origin: Place, dest: Place, user: User) -> SearchIntent:
    now = now_in(origin.timezone or user.timezone).replace(second=0, microsecond=0)
    intent = SearchIntent(
        domain=Domain.GROUND,
        language=user.language,
        origin=LocationRef(text=origin.name, place=origin),
        destination=LocationRef(text=dest.name, place=dest),
        when=DateWindow(start=now, end=now + timedelta(hours=3)),
        sort=SortMode.FASTEST,
    )
    return intent


async def run_route(msg: Message, app: App, user: User, origin: Place, dest_key: str) -> None:
    lang = user.language
    dest: Place | None
    if dest_key == "airport":
        dest = await nearest_airport(app, origin)
        if dest is None:
            await msg.answer(T("travel.no_airport", lang))
            return
    else:
        ref = await app.resolver.resolve(town_of(origin), language=lang)
        dest = ref.place
        if dest is None:
            await msg.answer(T("error.unknown_place", lang, place=ui.esc(town_of(origin))))
            return
        dest = dest.model_copy(update={"name": T("travel.main_station", lang, city=dest.name)})
    origin = origin.model_copy(update={"kind": PlaceKind.COORDINATES})
    await execute(msg, app, user, route_intent(origin, dest, user))


# --- currency ---------------------------------------------------------------------------------------


async def conversion_view(
    app: App, user: User, amount: Decimal, base: str, quotes: list[str]
) -> tuple[ui.Rich, str, InlineKeyboardMarkup] | None:
    lang = user.language
    src = Money(amount=amount, currency=base)
    rows: list[list[str]] = []
    as_of, source = None, None
    for q in dict.fromkeys(x for x in quotes if x and x != base):
        rate = await app.fx.rate(base, q)
        if rate is None:
            continue
        rows.append([f"<b>{i18n.money(rate.convert(src).converted, lang)}</b>", f"1 {base} = {rate.rate:.4g} {q}"])
        as_of, source = rate.as_of, rate.source
    if not rows:
        return None
    r = ui.Rich().h(f"💱 {i18n.money(src, lang)}", 3).table(rows, striped=True, compact=True)
    if as_of is not None:
        r.footer(T("travel.fx_source", lang, source=ui.esc(source or ""), date=i18n.fdate(as_of, lang, "d MMM y")))
    copy = rows[0][0].replace("<b>", "").replace("</b>", "")
    markup = ui.kb(
        [
            [
                (f"⧉ {T('travel.copy_amount', lang)}", f"copy:{copy}", None),
                (f"💱 {T('travel.convert', lang)}", cb("fx"), None),
            ],
        ]
    )
    fb = f"{i18n.money(src, lang)}\n" + "\n".join(f"= {a.replace('<b>', '').replace('</b>', '')}" for a, _ in rows)
    return r, fb, markup


def targets(user: User, local: str | None, explicit: str | None, base: str) -> list[str]:
    if explicit:
        return [explicit]
    out = [c for c in (local, user.currency, "EUR", "USD") if c and c != base]
    return list(dict.fromkeys(out))[:3]


@router.message(StateFilter(None), F.text.func(lambda t: parse_conversion(t) is not None))
async def on_conversion_text(message: Message, app: App, user: User, state: FSMContext) -> None:
    conv = parse_conversion(message.text or "")
    if conv is None:
        return
    here = await current_place(state)
    local = travel.country_currency(here.country_code) if here else None
    view = await conversion_view(app, user, conv.amount, conv.base, targets(user, local, conv.quote, conv.base))
    if view is None:
        await message.answer(T("travel.fx_unavailable", user.language))
        return
    await ui.send(message, *view)


@router.message(TravelInput.fx, F.text)
async def on_fx_input(message: Message, app: App, user: User, state: FSMContext) -> None:
    text = (message.text or "").strip()
    data = await state.get_data()
    base = data.get("fx_base") or user.currency
    conv = parse_conversion(text) or parse_conversion(f"{text} {base}")
    if conv is None:
        await message.answer(T("travel.fx_ask", user.language, currency=base))
        return
    await state.set_state(None)
    local = data.get("fx_local")
    view = await conversion_view(app, user, conv.amount, conv.base, targets(user, local, conv.quote, conv.base))
    if view is None:
        await message.answer(T("travel.fx_unavailable", user.language))
        return
    await ui.send(message, *view)


@router.message(TravelInput.route_to, F.text)
async def on_route_text(message: Message, app: App, user: User, state: FSMContext) -> None:
    await state.set_state(None)
    here = await current_place(state)
    ref = await app.resolver.resolve(
        message.text or "", language=user.language, country_bias=here.country_code if here else None
    )
    if ref.place is None or here is None:
        await message.answer(T("error.unknown_place", user.language, place=ui.esc(message.text or "")))
        return
    origin = here.model_copy(update={"kind": PlaceKind.COORDINATES})
    await execute(message, app, user, route_intent(origin, ref.place, user))


# --- callbacks --------------------------------------------------------------------------------------


@router.callback_query(TV.filter())
async def on_tv(cq: CallbackQuery, callback_data: TV, app: App, user: User, state: FSMContext) -> None:
    msg = cq.message if isinstance(cq.message, Message) else None
    if msg is None:
        await cq.answer()
        return
    a, v = callback_data.a, callback_data.v
    lang = user.language
    here = await current_place(state)
    if a == "fx":
        local = travel.country_currency(here.country_code) if here else None
        base = local if local and local != user.currency else user.currency
        await state.update_data(fx_base=base, fx_local=local)
        await state.set_state(TravelInput.fx)
        await cq.answer()
        await msg.answer(T("travel.fx_ask", lang, currency=base))
        return
    if here is None:
        await cq.answer()
        await ask_location(msg, state, user, f"{a}:{v}")
        return
    if a == "home":
        await cq.answer(T("travel.updating", lang))
        await show_dashboard(msg, app, user, here, edit=True)
        return
    if a == "q" and v in QUICK:
        await cq.answer()
        await execute(msg, app, user, quick_intent(v, here, user))
        return
    if a == "go":
        await cq.answer()
        if v == "ask":
            await state.set_state(TravelInput.route_to)
            await msg.answer(T("travel.route_ask", lang))
            return
        await run_route(msg, app, user, here, v)
        return
    await cq.answer()


async def on_travel_location(message: Message, app: App, user: User, state: FSMContext, place: Place) -> None:
    """Location shared outside the search panel: run the pending travel action or show the dashboard."""
    then = (await state.get_data()).get("travel_then") or "home:"
    await state.update_data(travel_then=None)
    a, _, v = then.partition(":")
    if a == "q" and v in QUICK:
        await execute(message, app, user, quick_intent(v, place, user))
    elif a == "go" and v in ("airport", "station"):
        await run_route(message, app, user, place, v)
    else:
        await show_dashboard(message, app, user, place)
