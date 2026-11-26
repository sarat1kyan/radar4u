from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from radar import i18n
from radar.app import App
from radar.storage import repo


class UserMiddleware(BaseMiddleware):
    """Loads (or creates) the user row and applies a per-user rate limit."""

    def __init__(self, app: App) -> None:
        self.app = app
        self._hits: dict[int, list[float]] = {}

    def _allow(self, uid: int) -> bool:
        now = time.monotonic()
        window = [t for t in self._hits.get(uid, []) if now - t < 60]
        if len(window) >= self.app.settings.user_rate_per_minute:
            self._hits[uid] = window
            return False
        window.append(now)
        self._hits[uid] = window
        if len(self._hits) > 50_000:
            self._hits.clear()
        return True

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = getattr(event, "from_user", None)
        if tg_user is None or tg_user.is_bot:
            return None
        async with self.app.db.session() as s:
            user = await repo.get_or_create_user(s, tg_user.id, i18n.normalize_lang(tg_user.language_code))
        if not self._allow(tg_user.id):
            text = i18n.t("error.rate_limited", user.language)
            if isinstance(event, CallbackQuery):
                await event.answer(text, show_alert=False)
            elif isinstance(event, Message):
                await event.answer(text)
            return None
        data["user"] = user
        data["app"] = self.app
        return await handler(event, data)
