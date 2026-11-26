from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from radar.booking.service import BookingService, BookingState
from radar.core.errors import BookingError
from radar.core.provenance import utcnow
from radar.nlu.rules import parse
from radar.providers.base import SearchContext
from radar.storage import repo
from radar.storage.models import AuditLog
from tests.conftest import TODAY


async def setup(db, pipeline):
    async with db.session() as s:
        user = await repo.get_or_create_user(s, 7, "en")
    out = await pipeline.run(
        parse("hotel in Berlin from December 21 to December 23", today=TODAY).intent, SearchContext(currency="EUR")
    )
    return user, out.ranked[0].result


async def test_confirm_flow_is_idempotent(db, pipeline, registry):
    user, result = await setup(db, pipeline)
    svc = BookingService(db, registry)
    b = await svc.start(user, result, SearchContext())
    assert b.state == BookingState.AWAITING_CONFIRMATION and b.confirm_nonce
    with pytest.raises(BookingError):
        await svc.confirm(user, b.id, "wrong-nonce")
    nonce = b.confirm_nonce
    done = await svc.confirm(user, b.id, nonce)
    assert done.state == BookingState.CONFIRMED and done.reference.startswith("DEMO-")
    again = await svc.confirm(user, b.id, nonce)  # double tap
    assert again.reference == done.reference
    async with db.session() as s:
        actions = [a.action for a in (await s.execute(select(AuditLog))).scalars().all()]
    assert actions.count("booking.submit") == 1


async def test_price_change_is_surfaced(db, pipeline, registry, fixtures):
    user, result = await setup(db, pipeline)
    fixtures.price_shift = Decimal(25)
    b = await BookingService(db, registry).start(user, result, SearchContext())
    assert BookingService.price_changed(b)
    assert Decimal(b.price_amount) == result.price.amount.amount + 25


async def test_expired_confirmation(db, pipeline, registry):
    user, result = await setup(db, pipeline)
    svc = BookingService(db, registry)
    b = await svc.start(user, result, SearchContext())
    with pytest.raises(BookingError) as e:
        await svc.confirm(user, b.id, b.confirm_nonce, now=utcnow() + timedelta(minutes=30))
    assert e.value.code == "expired"


async def test_handoff_when_no_booking_api(db, pipeline, registry):
    user, result = await setup(db, pipeline)
    result.booking = result.booking.HANDOFF
    from radar.domain.results import Link, LinkKind

    result.links.append(Link(kind=LinkKind.WEBSITE, url="https://hotel.example"))
    b = await BookingService(db, registry).start(user, result, SearchContext())
    assert b.state == BookingState.HANDOFF and b.handoff_url == "https://hotel.example"


async def test_other_user_cannot_confirm(db, pipeline, registry):
    user, result = await setup(db, pipeline)
    svc = BookingService(db, registry)
    b = await svc.start(user, result, SearchContext())
    async with db.session() as s:
        mallory = await repo.get_or_create_user(s, 666)
    with pytest.raises(BookingError):
        await svc.confirm(mallory, b.id, b.confirm_nonce)
