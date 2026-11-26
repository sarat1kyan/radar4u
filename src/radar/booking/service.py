"""Booking state machine.

    draft -> awaiting_confirmation -> submitted -> confirmed | failed
                 |   ^ (price changed: re-confirm)
                 v   |
              cancelled            handoff (provider has no booking API)

Rules enforced here, not in the UI:
  * the offer is revalidated with the provider right before asking for
    confirmation, and again implicitly by the provider when booking;
  * confirmation needs a one-time nonce issued with the summary the user saw,
    and it expires after a few minutes;
  * a changed price always goes back to the user;
  * every booking carries an idempotency key so retries never double-book;
  * "submitted" is never reported as "confirmed".
No language model is involved anywhere in this module.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from radar.core.errors import BookingError, ProviderError
from radar.core.provenance import utcnow
from radar.core.timeutil import aware
from radar.domain.results import BookingMode, LinkKind, Result
from radar.providers.base import SearchContext
from radar.providers.registry import ProviderRegistry
from radar.storage import repo
from radar.storage.db import Database
from radar.storage.models import Booking, User

CONFIRM_TTL = timedelta(minutes=10)


class BookingState(StrEnum):
    DRAFT = "draft"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    SUBMITTED = "submitted"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    HANDOFF = "handoff"


# Traveller details each direct-booking provider needs, beyond nothing.
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "fixtures": (),
    "liteapi": ("first_name", "last_name", "email", "phone"),
    "duffel": ("given_name", "family_name", "born_on", "gender", "title", "email", "phone_number"),
}


def _event(b: Booking, kind: str, **data: Any) -> None:
    b.events = [*b.events, {"at": utcnow().isoformat(), "event": kind, **data}]
    b.updated_at = utcnow()


class BookingService:
    def __init__(self, db: Database, registry: ProviderRegistry) -> None:
        self.db = db
        self.registry = registry

    async def start(self, user: User, result: Result, ctx: SearchContext) -> Booking:
        """Revalidate and return a booking awaiting the user's explicit confirmation (or a handoff)."""
        provider = self.registry.get(result.provenance.provider)
        handoff = result.link(LinkKind.BOOKING) or result.link(LinkKind.WEBSITE)
        async with self.db.session() as s:
            b = Booking(
                user_id=user.id,
                provider=result.provenance.provider,
                state=BookingState.DRAFT.value,
                result=result.model_dump(mode="json"),
                idempotency_key=uuid.uuid4().hex,
                events=[],
            )
            s.add(b)
            if (
                result.booking is not BookingMode.DIRECT
                or provider is None
                or not any(c.supports_booking for c in provider.capabilities)
            ):
                b.state = BookingState.HANDOFF.value
                b.handoff_url = handoff.url if handoff else None
                _event(b, "handoff", url=b.handoff_url)
                await s.flush()
                return b
            try:
                fresh = await provider.revalidate(result, ctx)
            except (BookingError, ProviderError, NotImplementedError) as exc:
                b.state = BookingState.FAILED.value
                _event(b, "revalidation_failed", error=getattr(exc, "code", type(exc).__name__))
                await s.flush()
                return b
            b.result = fresh.model_dump(mode="json")
            old_price = result.price.amount if result.price else None
            new_price = fresh.price.amount if fresh.price else None
            if new_price is not None:
                b.price_amount, b.price_currency = str(new_price.amount), new_price.currency
            b.state = BookingState.AWAITING_CONFIRMATION.value
            b.confirm_nonce = secrets.token_urlsafe(12)
            b.confirm_expires_at = utcnow() + CONFIRM_TTL
            _event(
                b,
                "revalidated",
                price_changed=bool(old_price and new_price and old_price != new_price),
                old=str(old_price.amount) if old_price else None,
                new=b.price_amount,
            )
            await repo.audit(s, user.id, "booking.quote", provider=b.provider)
            await s.flush()
            return b

    @staticmethod
    def price_changed(b: Booking) -> bool:
        return any(e.get("event") == "revalidated" and e.get("price_changed") for e in b.events[-1:])

    async def confirm(
        self,
        user: User,
        booking_id: int,
        nonce: str,
        travellers: dict[str, str] | None = None,
        now: datetime | None = None,
    ) -> Booking:
        now = now or utcnow()
        async with self.db.session() as s:
            b = await s.get(Booking, booking_id)
            if b is None or b.user_id != user.id:
                raise BookingError("booking not found", code="not_found")
            if b.state in (BookingState.SUBMITTED.value, BookingState.CONFIRMED.value):
                return b  # idempotent: a double tap never books twice
            if b.state != BookingState.AWAITING_CONFIRMATION.value:
                raise BookingError("booking is not awaiting confirmation", code="bad_state")
            if not b.confirm_nonce or not secrets.compare_digest(b.confirm_nonce, nonce):
                raise BookingError("confirmation does not match the summary shown", code="bad_nonce")
            if b.confirm_expires_at is None or aware(b.confirm_expires_at) < now:
                b.state = BookingState.CANCELLED.value
                _event(b, "confirmation_expired")
                raise BookingError("confirmation expired, please start again", code="expired")
            missing = [f for f in REQUIRED_FIELDS.get(b.provider, ()) if not (travellers or {}).get(f)]
            if missing:
                raise BookingError(f"missing traveller details: {', '.join(missing)}", code="details_missing")
            b.confirm_nonce = None
            b.state = BookingState.SUBMITTED.value
            _event(b, "submitted")
            await repo.audit(s, user.id, "booking.submit", provider=b.provider, booking_id=b.id)
            await s.flush()
            result = Result.model_validate(b.result)
            try:
                outcome = await self._book(b.provider, result, travellers or {}, b.idempotency_key)
            except (BookingError, ProviderError) as exc:
                b.state = BookingState.FAILED.value
                _event(b, "failed", error=getattr(exc, "code", "error"))
                return b
            b.reference = outcome.get("reference")
            status = outcome.get("status")
            b.state = {"confirmed": BookingState.CONFIRMED, "failed": BookingState.FAILED}.get(
                str(status), BookingState.SUBMITTED
            ).value
            _event(b, "provider_response", status=status)
            await repo.audit(s, user.id, f"booking.{b.state}", provider=b.provider, booking_id=b.id)
            return b

    async def cancel(self, user: User, booking_id: int) -> Booking | None:
        async with self.db.session() as s:
            b = await s.get(Booking, booking_id)
            if b is None or b.user_id != user.id:
                return None
            if b.state in (BookingState.DRAFT.value, BookingState.AWAITING_CONFIRMATION.value):
                b.state = BookingState.CANCELLED.value
                b.confirm_nonce = None
                _event(b, "cancelled")
            return b

    async def _book(self, provider_name: str, result: Result, travellers: dict[str, str], key: str) -> dict[str, Any]:
        p = self.registry.get(provider_name)
        if p is None:
            raise BookingError("provider unavailable", code="provider_unavailable")
        if provider_name == "fixtures":
            return await p.book(result, key)  # type: ignore[attr-defined, no-any-return]
        if provider_name == "liteapi":
            holder = {k: travellers[k] for k in ("first_name", "last_name", "email", "phone")}
            holder = {
                "firstName": holder["first_name"],
                "lastName": holder["last_name"],
                "email": holder["email"],
                "phone": holder["phone"],
            }
            guests = [
                {
                    "occupancyNumber": 1,
                    "firstName": holder["firstName"],
                    "lastName": holder["lastName"],
                    "email": holder["email"],
                }
            ]
            return await p.book(result, holder, guests, key)  # type: ignore[attr-defined, no-any-return]
        if provider_name == "duffel":
            pax_ids = result.facts.get("passenger_ids") or []
            if len(pax_ids) != 1:
                raise BookingError(
                    "multi-passenger flight booking needs a details form per passenger", code="details_missing"
                )
            pax = {"id": pax_ids[0], **{k: travellers[k] for k in REQUIRED_FIELDS["duffel"]}}
            return await p.create_order(result, [pax], key)  # type: ignore[attr-defined, no-any-return]
        raise BookingError("direct booking not implemented for this provider", code="not_supported")
