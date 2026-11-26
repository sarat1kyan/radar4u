"""Bot process: dispatcher, background radar worker and notification delivery."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import ssl

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    ErrorEvent,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
    WebAppInfo,
)

from radar import i18n
from radar.app import App
from radar.bot import render
from radar.bot.handlers import booking, builder, common, radars, search
from radar.bot.middlewares import UserMiddleware
from radar.monitoring.engine import Notifier
from radar.storage.models import Notification, User

log = logging.getLogger(__name__)


class BotSender:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def send(self, telegram_id: int, text: str, buttons: list[list[tuple[str, str]]] | None = None) -> bool:
        markup = None
        if buttons:
            markup = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=t, url=u) for t, u in row] for row in buttons]
            )
        try:
            await self.bot.send_message(telegram_id, text, reply_markup=markup, disable_web_page_preview=True)
            return True
        except TelegramForbiddenError:
            return True  # user blocked the bot; don't retry forever
        except TelegramAPIError:
            log.warning("delivery failed", exc_info=True)
            return False


async def render_notifications(user: User, batch: list[Notification]) -> tuple[str, list[list[tuple[str, str]]] | None]:
    lang = user.language
    texts = [render.notification_text(n.kind, n.payload, lang) for n in batch]
    if len(batch) > 1:
        texts.insert(0, "<b>" + i18n.t("radar.digest.title", lang, count=len(batch)) + "</b>")
    links = [(n.payload.get("title") or i18n.t("result.details", lang))[:40] for n in batch]
    buttons = [
        [(label, n.payload["link"])]
        for label, n in zip(links, batch, strict=True)
        if isinstance(n.payload.get("link"), str) and n.payload["link"].startswith("https://")
    ][:5]
    return "\n\n".join(texts)[:4000], buttons or None


def build_storage(app: App) -> BaseStorage:
    if app.settings.redis_url:
        from aiogram.fsm.storage.redis import RedisStorage

        return RedisStorage.from_url(app.settings.redis_url)
    return MemoryStorage()


def build_dispatcher(app: App) -> Dispatcher:
    dp = Dispatcher(storage=build_storage(app))
    mw = UserMiddleware(app)
    dp.message.outer_middleware(mw)
    dp.callback_query.outer_middleware(mw)
    # Order matters: the free-text search handler must come last.
    routers = (common.router, builder.router, radars.router, booking.router, search.router)
    for r in routers:
        # Routers are module singletons; allow rebuilding a dispatcher (tests, webhook + polling tools).
        r._parent_router = None
    dp.include_routers(*routers)

    @dp.errors()
    async def on_error(event: ErrorEvent) -> bool:
        log.exception("update failed", exc_info=event.exception)
        upd = event.update
        target = upd.message or (upd.callback_query.message if upd.callback_query else None)
        if target is not None:
            with contextlib.suppress(Exception):
                await target.answer(i18n.t("error.generic", "en"))
        return True

    return dp


def build_bot(app: App) -> Bot:
    token = app.settings.bot_token
    if token is None:
        raise SystemExit("BOT_API is not set")
    session = AiohttpSession()
    # aiogram pins certifi's CA bundle; honour SSL_CERT_FILE so deployments behind a TLS-inspecting
    # egress proxy (corporate networks, some PaaS sandboxes) can still reach api.telegram.org.
    ca_file = os.environ.get("SSL_CERT_FILE")
    if ca_file and os.path.exists(ca_file):
        session._connector_init["ssl"] = ssl.create_default_context(cafile=ca_file)
    return Bot(token.get_secret_value(), session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


async def setup_bot_profile(bot: Bot, app: App) -> None:
    for lang in i18n.available():
        cmds = [
            BotCommand(command="start", description=i18n.t("menu.search", lang)),
            BotCommand(command="radars", description=i18n.t("menu.radars", lang)),
            BotCommand(command="settings", description=i18n.t("menu.settings", lang)),
            BotCommand(command="location", description=i18n.t("location.button", lang)),
            BotCommand(command="help", description=i18n.t("menu.help", lang)),
            BotCommand(command="privacy", description=i18n.t("menu.privacy", lang)),
        ]
        await bot.set_my_commands(cmds, language_code=None if lang == "en" else lang)
    if app.settings.webapp_url:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="RADAR", web_app=WebAppInfo(url=app.settings.webapp_url))
        )


async def worker_loop(app: App, bot: Bot, stop: asyncio.Event) -> None:
    notifier = Notifier(app.db, BotSender(bot), render_notifications)
    while not stop.is_set():
        try:
            n = await app.engine.tick()
            await notifier.deliver()
            if n:
                log.info("checked %d radars", n)
        except Exception:
            log.exception("worker tick failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=app.settings.monitor_tick_s)


async def run_polling(app: App, with_worker: bool = True) -> None:
    bot = build_bot(app)
    dp = build_dispatcher(app)
    if not app.settings.database_url.startswith("postgresql"):
        await app.db.create_all()
    await setup_bot_profile(bot, app)
    stop = asyncio.Event()
    worker = asyncio.create_task(worker_loop(app, bot, stop)) if with_worker else None
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        stop.set()
        if worker:
            await worker
        await bot.session.close()
        await app.close()
