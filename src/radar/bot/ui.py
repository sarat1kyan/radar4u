"""Rich message building and delivery.

Bot API 10.1+ rich messages give us headings, tables, maps, photos, expandable
sections and button rows inside the message. Every screen is built as rich
HTML plus a classic HTML fallback: if a client or the API rejects the rich
version, the classic one is sent instead, so a formatting problem can never
leave the user without an answer.
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass, field

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup, InputRichMessage, Message

log = logging.getLogger(__name__)

STYLES = ("primary", "success", "danger")


def esc(s: object, limit: int = 200) -> str:
    text = str(s)
    if len(text) > limit:
        text = text[: limit - 1] + "..."
    return html.escape(text, quote=False)


def attr(s: object) -> str:
    return html.escape(str(s), quote=True)


@dataclass
class Rich:
    """Accumulates a rich HTML document."""

    parts: list[str] = field(default_factory=list)

    def h(self, text: str, level: int = 3) -> Rich:
        self.parts.append(f"<h{level}>{text}</h{level}>")
        return self

    def p(self, text: str) -> Rich:
        if text:
            self.parts.append(f"<p>{text}</p>")
        return self

    def hr(self) -> Rich:
        self.parts.append("<hr/>")
        return self

    def img(self, url: str, caption: str | None = None) -> Rich:
        if not url.startswith("https://"):
            return self
        if caption:
            self.parts.append(f'<figure><img src="{attr(url)}"/><figcaption>{caption}</figcaption></figure>')
        else:
            self.parts.append(f'<img src="{attr(url)}"/>')
        return self

    def map(self, lat: float, lon: float, zoom: int = 15, caption: str | None = None) -> Rich:
        m = f'<tg-map lat="{lat:.6f}" long="{lon:.6f}" zoom="{zoom}"/>'
        self.parts.append(f"<figure>{m}<figcaption>{caption}</figcaption></figure>" if caption else m)
        return self

    def table(
        self,
        rows: list[list[str]],
        header: list[str] | None = None,
        *,
        striped: bool = True,
        compact: bool = True,
        bordered: bool = False,
    ) -> Rich:
        if not rows:
            return self
        flags = " ".join(f for f, on in (("bordered", bordered), ("striped", striped), ("compact", compact)) if on)
        out = [f"<table {flags}>" if flags else "<table>"]
        if header:
            out.append("<tr>" + "".join(f"<th>{c}</th>" for c in header) + "</tr>")
        for r in rows:
            out.append("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>")
        out.append("</table>")
        self.parts.append("".join(out))
        return self

    def details(self, summary: str, body: str, *, open_: bool = False) -> Rich:
        self.parts.append(f"<details{' open' if open_ else ''}><summary>{summary}</summary>{body}</details>")
        return self

    def quote(self, body: str, *, expandable: bool = False) -> Rich:
        self.parts.append(f"<blockquote{' expandable' if expandable else ''}>{body}</blockquote>")
        return self

    def buttons(self, buttons: list[tuple[str, str, str, str | None]], align: str = "left") -> Rich:
        """buttons: (label, kind, value, style). kind: callback | url | copy | webapp."""
        out = []
        for label, kind, value, style in buttons[:8]:
            st = f' style="{style}"' if style else ""
            if kind == "callback":
                out.append(f'<tg-button type="callback_data" data="{attr(value)}"{st}>{esc(label, 40)}</tg-button>')
            elif kind == "url" and value.startswith(("https://", "http://", "tg://")):
                out.append(f'<tg-button type="url" url="{attr(value)}"{st}>{esc(label, 40)}</tg-button>')
            elif kind == "copy":
                out.append(f'<tg-button type="copy_text" text="{attr(value)}"{st}>{esc(label, 40)}</tg-button>')
            elif kind == "webapp" and value.startswith("https://"):
                out.append(f'<tg-button type="web_app" url="{attr(value)}"{st}>{esc(label, 40)}</tg-button>')
        if out:
            self.parts.append(f'<tg-button-row align="{align}">{"".join(out)}</tg-button-row>')
        return self

    def footer(self, text: str) -> Rich:
        self.parts.append(f"<footer>{text}</footer>")
        return self

    def html(self) -> str:
        return "".join(self.parts)[:32000]


def kb(rows: list[list[tuple[str, str, str | None]]]) -> InlineKeyboardMarkup:
    """rows of (label, callback_or_url, style). Values starting with https:// become URL buttons."""
    out: list[list[InlineKeyboardButton]] = []
    for row in rows:
        r = []
        for label, value, style in row:
            st = style if style in STYLES else None
            if value.startswith("https://"):
                r.append(InlineKeyboardButton(text=label, url=value, style=st))
            elif value.startswith("copy:"):
                r.append(InlineKeyboardButton(text=label, copy_text=CopyTextButton(text=value[5:][:256]), style=st))
            else:
                r.append(InlineKeyboardButton(text=label, callback_data=value, style=st))
        if r:
            out.append(r)
    return InlineKeyboardMarkup(inline_keyboard=out)


async def send(target: Message, rich: Rich, fallback: str, markup: InlineKeyboardMarkup | None = None) -> Message:
    try:
        return await target.answer_rich(rich_message=InputRichMessage(html=rich.html()), reply_markup=markup)
    except TelegramBadRequest as exc:
        log.warning("rich message rejected, using classic: %s", exc)
        return await target.answer(fallback[:4000], reply_markup=markup, disable_web_page_preview=True)


async def edit(target: Message, rich: Rich, fallback: str, markup: InlineKeyboardMarkup | None = None) -> Message:
    """Edit a bot message in place; if editing is impossible, send a new one."""
    try:
        res = await target.edit_text(rich_message=InputRichMessage(html=rich.html()), reply_markup=markup)
        return res if isinstance(res, Message) else target
    except TelegramBadRequest as exc:
        if "not modified" in str(exc):
            return target
        log.info("rich edit rejected (%s), trying classic", exc)
    try:
        res = await target.edit_text(fallback[:4000], reply_markup=markup, disable_web_page_preview=True)
        return res if isinstance(res, Message) else target
    except TelegramBadRequest as exc:
        if "not modified" in str(exc):
            return target
        return await send(target, rich, fallback, markup)


async def draft(target: Message, chat_id: int, draft_id: int, rich: Rich) -> bool:
    """Stream an animated draft preview (Bot API 10.1+, private chats). Returns False if unsupported."""
    bot = target.bot
    if bot is None:
        return False
    try:
        return bool(
            await bot.send_rich_message_draft(
                chat_id=chat_id, draft_id=draft_id, rich_message=InputRichMessage(html=rich.html())
            )
        )
    except (TelegramBadRequest, TelegramAPIError) as exc:
        log.info("draft preview unavailable: %s", exc)
        return False
