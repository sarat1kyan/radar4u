"""Drive the real aiogram dispatcher with synthetic updates and a fake Telegram session."""

from datetime import UTC, datetime

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import (
    AnswerCallbackQuery,
    EditMessageText,
    SendMessage,
    SendRichMessage,
    SendVenue,
    TelegramMethod,
)
from aiogram.types import CallbackQuery, Chat, Message, Update
from aiogram.types import User as TgUser

from radar.app import build_app
from radar.bot.main import build_dispatcher
from radar.providers.registry import ProviderRegistry
from radar.storage import repo

TOKEN = "123456:TEST-TOKEN-not-real"
CHAT = Chat(id=42, type="private")
TG_USER = TgUser(id=42, is_bot=False, first_name="T", language_code="en")


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls: list[TelegramMethod] = []
        self._mid = 100

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, SendMessage | EditMessageText | SendVenue | SendRichMessage):
            self._mid += 1
            return Message(message_id=self._mid, date=datetime.now(UTC), chat=CHAT, text=_text(method) or "venue").as_(
                bot
            )
        if isinstance(method, AnswerCallbackQuery):
            return True
        return True

    async def stream_content(self, *a, **k):  # pragma: no cover
        raise NotImplementedError

    async def close(self):
        pass

    def texts(self):
        return [_text(c) for c in self.calls if isinstance(c, SendMessage | EditMessageText | SendRichMessage)]


def _text(method):
    rich = getattr(method, "rich_message", None)
    if rich is not None:
        return rich.html
    return getattr(method, "text", None)


@pytest.fixture
async def harness(settings, fixtures, monkeypatch):
    settings.bot_token = None
    app = build_app(settings, registry=ProviderRegistry([fixtures], settings))
    app.db = __import__("radar.storage.db", fromlist=["Database"]).Database("sqlite+aiosqlite:///:memory:")
    app.radars.db = app.engine.db = app.bookings.db = app.db
    await app.db.create_all()
    session = FakeSession()
    bot = Bot(TOKEN, session=session)
    dp = build_dispatcher(app)
    n = {"u": 0}

    async def send_text(text):
        n["u"] += 1
        msg = Message(message_id=n["u"], date=datetime.now(UTC), chat=CHAT, from_user=TG_USER, text=text)
        await dp.feed_update(bot, Update(update_id=n["u"], message=msg))

    async def send_location(lat, lon):
        from aiogram.types import Location

        n["u"] += 1
        msg = Message(
            message_id=n["u"],
            date=datetime.now(UTC),
            chat=CHAT,
            from_user=TG_USER,
            location=Location(latitude=lat, longitude=lon),
        )
        await dp.feed_update(bot, Update(update_id=n["u"], message=msg))

    send_text.location = send_location

    async def press(data):
        n["u"] += 1
        src = Message(message_id=1, date=datetime.now(UTC), chat=CHAT, text="x")
        cb = CallbackQuery(id=str(n["u"]), from_user=TG_USER, chat_instance="c", data=data, message=src)
        await dp.feed_update(bot, Update(update_id=n["u"], callback_query=cb))

    yield app, session, send_text, press
    await app.db.dispose()


def markups(session):
    return [c.reply_markup for c in session.calls if getattr(c, "reply_markup", None) is not None]


def buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]


async def test_onboarding_then_search_then_details_and_booking(harness):
    app, session, send_text, press = harness
    await send_text("/start")
    assert "language" in session.texts()[-1].lower()
    await press("l:en")
    await send_text("Berlin")  # default city
    async with app.db.session() as s:
        u = await repo.get_or_create_user(s, 42)
    assert u.onboarded and u.home_place["name"] == "Berlin"

    await send_text("hotel from December 21 to December 23 for two with wifi")
    texts = session.texts()
    assert any("Searching in Berlin" in t for t in texts)
    final = texts[-1]
    assert "(demo)" in final and final.startswith("<h3>Hotels and stays")
    kb = markups(session)[-1]
    first = buttons(kb)[0].callback_data
    assert first.startswith("r:d:")

    await press(first)
    card = session.texts()[-1]
    assert "Source:" in card and "DEMO DATA" in card
    actions = buttons(markups(session)[-1])
    book = next(b for b in actions if b.callback_data and b.callback_data.startswith("r:b:"))
    await press(book.callback_data)
    summary = session.texts()[-1]
    assert "Please check before I place this booking" in summary
    confirm = buttons(markups(session)[-1])[0].callback_data
    await press(confirm)
    assert "Confirmed. Reference: DEMO-" in session.texts()[-1]


async def test_refinement_and_clarification(harness):
    app, session, send_text, press = harness
    await send_text("hotel in Rome")  # dates missing -> ask
    assert "Which dates" in session.texts()[-1]
    await send_text("from 21.12 to 24.12")
    assert session.texts()[-1].startswith("<h3>Hotels and stays")
    await send_text("make it cheaper")
    # budget was unset, so cheaper means sorting by price
    assert session.texts()[-1].startswith("<h3>Hotels and stays")


async def test_watch_creates_radar_and_lists_it(harness):
    app, session, send_text, press = harness
    await send_text("hotel in Berlin from December 21 to December 23")
    watch = next(b for b in buttons(markups(session)[-1]) if b.callback_data.startswith("w:"))
    await press(watch.callback_data)
    kinds = buttons(markups(session)[-1])
    await press(kinds[0].callback_data)  # price below -> asks amount
    await send_text("150 EUR")
    assert "Radar on" in session.texts()[-1]
    await send_text("/radars")
    assert any("price drops below" in t for t in session.texts()[-2:])


async def test_untrusted_text_is_escaped(harness):
    app, session, send_text, press = harness
    await send_text("<b>hotel</b> in <i>Paris</i> from December 21 to December 23")
    assert "<i>Paris</i>" not in session.texts()[-1]


async def test_unsupported_domain_reports_coverage(harness):
    app, session, send_text, press = harness
    await send_text("train from Frankfurt to Berlin tomorrow")
    assert "None of my connected providers cover this request yet" in session.texts()[-1]


async def test_button_builder_with_saved_city(harness):
    """Regression: saved-city buttons used place ids containing ':' and crashed the callback."""
    app, session, send_text, press = harness
    await send_text("/start")
    await press("l:en")
    await send_text("Berlin")
    home = markups(session)[-1]
    stay = next(b for b in buttons(home) if b.callback_data == "sb:dom:stay")
    assert stay.style == "primary"
    await press(stay.callback_data)
    cats = buttons(markups(session)[-1])
    await press(next(b.callback_data for b in cats if b.callback_data == "sb:cat:property.hotel"))
    cities = buttons(markups(session)[-1])
    berlin = next(b for b in cities if b.text == "Berlin")
    assert ":" not in berlin.callback_data.split(":", 2)[2]
    await press(berlin.callback_data)
    await press("sb:cal:2026-12")  # calendar
    cal = buttons(markups(session)[-1])
    assert any(b.callback_data == "sb:day:2026-12-21" for b in cal)
    await press("sb:day:2026-12-21")
    await press("sb:nights:2")
    await press("sb:pax:2")
    await press("sb:flag:wifi")
    flags = buttons(markups(session)[-1])
    assert next(b for b in flags if b.callback_data == "sb:flag:wifi").style == "success"
    await press("sb:go:")
    final = session.texts()[-1]
    assert final.startswith("<h3>Hotels and stays") and "Berlin" in final and "(demo)" in final
    first = next(b for b in buttons(markups(session)[-1]) if b.callback_data.startswith("r:d:"))
    await press(first.callback_data)
    card = session.texts()[-1]
    assert "<h2>" in card and "<tg-map" in card and "<table" in card


async def test_edit_rerun_and_time_shift(harness):
    app, session, send_text, press = harness
    await send_text("flight from Yerevan to Rome on December 5")
    results = buttons(markups(session)[-1])
    later = next(b for b in results if b.callback_data.endswith(":+") and b.callback_data.startswith("tp:"))
    edit = next(b for b in results if b.callback_data.startswith("sb:edit:"))
    assert edit.style == "primary"
    await press(later.callback_data)
    assert "Rome" in session.texts()[-1]
    await press(edit.callback_data)
    panel = session.texts()[-1]
    assert "Yerevan" in panel and "Rome" in panel  # draft restored from the typed search
    await press("m:home")  # home screen (this user skipped onboarding)
    home = buttons(markups(session)[-1])
    rerun = next(b for b in home if b.callback_data.startswith("sb:rerun:"))
    await press(rerun.callback_data)
    assert "Rome" in session.texts()[-1]


async def test_open_near_me_uses_shared_location(harness):
    app, session, send_text, press = harness
    app.resolver.reverse = _fake_reverse  # no network in tests
    await press("m:home")
    near = next(b for b in buttons(markups(session)[-1]) if b.callback_data == "sb:near:")
    assert near.style == "success"
    await press(near.callback_data)
    assert "Share your location" in session.texts()[-1]
    await send_text.location(40.1811, 44.5136)
    assert session.texts()[-1].startswith("<h3>Restaurants")


async def _fake_reverse(lat, lon, language="en"):
    from radar.geo.resolver import GAZ

    return GAZ.lookup("Yerevan").to_place()


async def _fake_current(point):
    from radar.providers.weather import CurrentWeather

    return CurrentWeather(condition="rain", temperature=12.0, feels_like=9.0, wind_kmh=20, is_day=True)


def _seed_fx(app):
    from decimal import Decimal

    rates = {"EUR": Decimal(1), "USD": Decimal("1.08"), "AMD": Decimal("420.5")}
    app.fx.seed("ecb", rates, datetime.now(UTC))


async def test_location_opens_travel_dashboard_and_quick_actions(harness):
    app, session, send_text, press = harness
    app.resolver.reverse = _fake_reverse
    app.weather.current = _fake_current
    _seed_fx(app)
    await send_text.location(40.1811, 44.5136)
    dash = session.texts()[-1]
    assert "Local time" in dash and "911" in dash and "AMD" in dash and "12°" in dash and "<tg-map" in dash
    btns = buttons(markups(session)[-1])
    sos = next(b for b in btns if b.copy_text is not None)
    assert sos.copy_text.text == "911" and sos.style == "danger"
    pharmacy = next(b for b in btns if b.callback_data == "tv:q:pharmacy")
    await press(pharmacy.callback_data)
    # fixtures-only registry: no source covers essentials, and the bot says so honestly
    assert "None of my connected providers cover this request yet" in session.texts()[-1]
    await press("tv:q:hotel")
    assert session.texts()[-1].startswith("<h3>Hotels and stays")


async def test_travel_button_without_location_asks_then_runs(harness):
    app, session, send_text, press = harness
    app.resolver.reverse = _fake_reverse
    app.weather.current = _fake_current
    _seed_fx(app)
    await press("m:home")
    travel_btn = next(b for b in buttons(markups(session)[-1]) if b.callback_data == "tv:home:")
    await press(travel_btn.callback_data)
    assert "Share your location" in session.texts()[-1]
    await send_text.location(40.1811, 44.5136)
    assert "Local time" in session.texts()[-1]


async def test_currency_conversion_text(harness):
    app, session, send_text, press = harness
    _seed_fx(app)
    await send_text("100 usd to eur")
    out = session.texts()[-1]
    assert "92.59" in out and "€" in out
    await press("tv:fx:")
    assert "Send an amount" in session.texts()[-1]
    await send_text("50")
    assert "€" in session.texts()[-1] or "$" in session.texts()[-1]


async def test_result_buttons_named_and_pager(harness):
    app, session, send_text, press = harness
    await send_text("hotel in Berlin from December 21 to December 23")
    btns = buttons(markups(session)[-1])
    first = next(b for b in btns if b.callback_data and b.callback_data.startswith("r:d:"))
    assert first.text.startswith("➊ ") and len(first.text) > 4
    disabled = [b for b in btns if b.disabled is not None]
    if disabled:  # more than one page: indicator and the impossible "previous" are greyed out
        assert any("/" in b.text for b in disabled)
