"""Turns a message into a validated, location-resolved SearchIntent.

Everything here is deterministic: the multilingual rule parser reads the text,
follow-ups refine the previous intent, missing places are filled from the
user's saved context and resolved through the geocoder. Anything the parser
cannot read is reported as missing so the bot can offer buttons for it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime

from radar.core.timeutil import now_in, tz
from radar.domain.common import LocationRef, Place
from radar.domain.intents import SearchIntent
from radar.geo.resolver import GeoResolver
from radar.nlu.refine import is_followup, refine
from radar.nlu.rules import _required_missing, parse

log = logging.getLogger(__name__)


@dataclass
class UserContext:
    language: str = "en"
    currency: str = "EUR"
    timezone: str | None = None
    home: Place | None = None
    # Set only after the user explicitly shared location for this session.
    current: Place | None = None
    favorites: list[Place] = field(default_factory=list)

    @property
    def anchor(self) -> Place | None:
        return self.current or self.home


@dataclass
class Interpretation:
    intent: SearchIntent
    source: str
    confidence: float
    refined: bool = False
    used_context_location: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def needs_clarification(self) -> bool:
        return bool(self.intent.missing)


class Interpreter:
    def __init__(self, resolver: GeoResolver) -> None:
        self.resolver = resolver

    def _today(self, ctx: UserContext) -> tuple[date, str]:
        zone = (ctx.anchor.timezone if ctx.anchor else None) or ctx.timezone or "UTC"
        return now_in(zone).date(), zone

    async def interpret(
        self,
        text: str,
        ctx: UserContext,
        previous: SearchIntent | None = None,
        *,
        today: date | None = None,
        online: bool = True,
    ) -> Interpretation:
        text = text.strip()[:2000]
        base_today, zone = self._today(ctx)
        today = today or base_today

        fresh = parse(text, today=today, zone=zone, ui_language=ctx.language, now=now_in(zone))
        if previous is not None and is_followup(text, previous, fresh):
            intent = refine(previous, text, today=today, zone=zone)
            result = Interpretation(intent=intent, source="rules", confidence=1.0, refined=True)
        else:
            result = Interpretation(intent=fresh.intent, source="rules", confidence=fresh.confidence)

        await self._complete(result, ctx, online=online)
        return result

    async def _complete(self, result: Interpretation, ctx: UserContext, *, online: bool) -> None:
        intent = result.intent
        anchor = ctx.anchor
        if "near:me" in intent.categories and ctx.current is not None and not intent.domain.is_transport:
            intent.location = LocationRef(text=ctx.current.name, place=ctx.current, radius_m=1500)
            result.used_context_location = True
        # Fill gaps from authorized context instead of asking.
        if anchor is not None:
            if intent.domain.is_transport and intent.origin is None and intent.destination is not None:
                intent.origin = LocationRef(text=anchor.name, place=anchor)
                result.used_context_location = True
            elif not intent.domain.is_transport and intent.location is None and intent.destination is None:
                intent.location = LocationRef(text=anchor.name, place=anchor)
                result.used_context_location = True

        for attr in ("location", "origin", "destination"):
            ref: LocationRef | None = getattr(intent, attr)
            if ref is None or ref.resolved:
                continue
            bias = anchor.country_code if anchor else None
            resolved = await self.resolver.resolve(ref.text, language=ctx.language, country_bias=bias, online=online)
            if resolved.place is None:
                result.notes.append(f"unresolved:{attr}:{ref.text}")
                setattr(intent, attr, None)
            else:
                resolved.radius_m = ref.radius_m
                setattr(intent, attr, resolved)

        _reanchor_timezone(intent)
        intent.missing = _required_missing(intent)
        if any(n.startswith("unresolved:") for n in result.notes):
            for n in result.notes:
                if n.startswith("unresolved:"):
                    key = n.split(":")[1]
                    key = "location" if key == "location" else key
                    if key not in intent.missing:
                        intent.missing.append(key)


def _reanchor_timezone(intent: SearchIntent) -> None:
    """Times the user typed are wall-clock times at the place they are about."""
    ref = intent.origin if intent.domain.is_transport else (intent.location or intent.destination)
    zone = ref.place.timezone if ref and ref.place else None
    if not zone or intent.when is None:
        return

    z = tz(zone)

    def fix(dt: datetime | None) -> datetime | None:
        return dt.replace(tzinfo=z) if dt is not None else None

    intent.when = intent.when.model_copy(update={"start": fix(intent.when.start), "end": fix(intent.when.end)})
