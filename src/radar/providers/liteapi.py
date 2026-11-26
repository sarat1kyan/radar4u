"""LiteAPI (Nuitee) hotel rates, prebook (revalidation) and booking.

Docs: https://docs.nuitee.com (formerly docs.liteapi.travel). Auth header
X-API-Key; a sandbox key (prefix sand_) is free from the dashboard and selects
the sandbox environment on the same hosts. Prebook re-prices an offer and
must be called before book.

Room-level amenities (bed type, bathroom) are not part of the rates payload,
so those requirements stay UNKNOWN here and cannot produce a strict match
until hotel/room details are fetched; property-level and rate-level facts
(board, refundability, taxes) are mapped.

Status in this repository: implemented against the documented endpoints,
fields parsed defensively, covered by contract tests. Not run live here.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from radar.config import get_settings
from radar.core.errors import BookingError, ProviderError
from radar.core.money import Money, MoneyError
from radar.core.provenance import Freshness, Provenance, Tri, utcnow
from radar.domain.common import BudgetUnit, Domain, GeoPoint
from radar.domain.intents import SearchIntent
from radar.domain.results import Availability, BookingMode, Link, LinkKind, Price, PriceKind, Rating, Result
from radar.providers.base import AccessLevel, Capability, Provider, SearchContext
from radar.providers.http import ProviderHttp

SEARCH_BASE = "https://api.liteapi.travel/v3.0"
BOOK_BASE = "https://book.liteapi.travel/v3.0"


def _money(obj: Any) -> Money | None:
    if isinstance(obj, list):
        obj = obj[0] if obj else None
    if not isinstance(obj, dict):
        return None
    try:
        return Money.of(str(obj["amount"]), str(obj["currency"]))
    except (KeyError, MoneyError):
        return None


def rate_to_result(
    hotel_rates: dict[str, Any], hotel: dict[str, Any] | None, nights: int, origin: GeoPoint | None, sandbox: bool
) -> Result | None:
    room_types = hotel_rates.get("roomTypes") or []
    if not room_types:
        return None
    rt = room_types[0]
    rates = rt.get("rates") or []
    rate = rates[0] if rates else {}
    total = _money(rt.get("offerRetailRate")) or _money((rate.get("retailRate") or {}).get("total"))
    if total is None:
        return None
    excluded = Decimal(0)
    excluded_cur = total.currency
    taxes = (rate.get("retailRate") or {}).get("taxesAndFees") or []
    for t in taxes:
        if t.get("included") is False and (m := _money(t)):
            excluded += m.amount
            excluded_cur = m.currency
    refundable = ((rate.get("cancellationPolicies") or {}).get("refundableTag") or "").upper()
    board = str(rate.get("boardType") or rate.get("boardName") or "").upper()
    hotel = hotel or {}
    point = None
    if hotel.get("latitude") is not None and hotel.get("longitude") is not None:
        point = GeoPoint(lat=float(hotel["latitude"]), lon=float(hotel["longitude"]))
    rating = None
    if isinstance(hotel.get("rating"), int | float) and hotel.get("rating"):
        rating = Rating(value=float(hotel["rating"]), scale=10.0, count=hotel.get("reviewCount"), source="LiteAPI")
    attrs = {
        "free_cancellation": Tri.YES if refundable == "RFN" else Tri.NO if refundable == "NRFN" else Tri.UNKNOWN,
        "refundable": Tri.YES if refundable == "RFN" else Tri.NO if refundable == "NRFN" else Tri.UNKNOWN,
        "breakfast": Tri.YES
        if board in ("BB", "HB", "FB", "AI") or "BREAKFAST" in board
        else (Tri.NO if board in ("RO", "ROOM ONLY") else Tri.UNKNOWN),
        "pay_at_property": Tri.UNKNOWN,
    }
    return Result(
        domain=Domain.STAY,
        title=str(hotel.get("name") or hotel_rates.get("hotelId")),
        subtitle=str(rate.get("name") or rt.get("name") or ""),
        point=point,
        address=hotel.get("address"),
        distance_m=round(origin.distance_m(point)) if origin and point else None,
        price=Price(
            amount=total,
            unit=BudgetUnit.TOTAL,
            kind=PriceKind.OFFER,
            includes_taxes=Tri.YES if taxes and excluded == 0 else Tri.UNKNOWN,
            payable_at_property=Money.of(excluded, excluded_cur) if excluded > 0 else None,
            unknown_fees=not taxes,
        ),
        rating=rating,
        attributes=attrs,
        availability=Availability.CONFIRMED,
        booking=BookingMode.DIRECT,
        links=[Link(kind=LinkKind.SOURCE, url="https://www.liteapi.travel", label="LiteAPI")],
        facts={
            "offer_id": rt.get("offerId"),
            "hotel_id": hotel_rates.get("hotelId"),
            "nights": nights,
            "board": board or None,
            "stars": hotel.get("stars") or hotel.get("starRating"),
            "sandbox": sandbox,
        },
        provenance=Provenance(
            provider="liteapi",
            freshness=Freshness.FIXTURE if sandbox else Freshness.LIVE,
            source_url="https://www.liteapi.travel",
            attribution="LiteAPI" + (" (sandbox)" if sandbox else ""),
            external_id=str(rt.get("offerId") or hotel_rates.get("hotelId")),
            retrieved_at=utcnow(),
        ),
    )


class LiteApiProvider(Provider):
    name = "liteapi"
    display_name = "LiteAPI"
    access = AccessLevel.SANDBOX
    docs_url = "https://docs.nuitee.com"
    capabilities = (
        Capability(
            Domain.STAY,
            supports_booking=True,
            supports_revalidation=True,
            monitoring_allowed=True,
            min_monitor_interval_s=6 * 3600,
            cost_units=3.0,
        ),
    )

    def __init__(self, key: str | None = None, http: ProviderHttp | None = None) -> None:
        s = get_settings()
        self.key = key or (s.liteapi_key.get_secret_value() if s.liteapi_key else None)
        self.http = http or ProviderHttp("liteapi", rate_per_s=3, burst=3, max_retries=1, timeout_s=40)

    def configured(self) -> bool:
        return bool(self.key)

    @property
    def sandbox(self) -> bool:
        return bool(self.key and self.key.startswith(("sand_", "sandbox_")))

    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": str(self.key), "Accept": "application/json", "Content-Type": "application/json"}

    async def search(self, intent: SearchIntent, ctx: SearchContext) -> list[Result]:
        self.ensure_configured()
        ref = intent.location
        if not (ref and ref.place and intent.stay):
            return []
        children = [max(0, a) for a in intent.party.children_ages]
        per_room_adults = max(1, intent.party.adults // max(1, intent.party.rooms))
        occupancies = [
            {"adults": per_room_adults, "children": children if i == 0 else []} for i in range(intent.party.rooms)
        ]
        body = {
            "checkin": intent.stay.check_in.isoformat(),
            "checkout": intent.stay.check_out.isoformat(),
            "occupancies": occupancies,
            "currency": ctx.currency.upper(),
            "guestNationality": str(ctx.extra.get("nationality") or (ref.place.country_code or "US")),
            "latitude": ref.place.point.lat,
            "longitude": ref.place.point.lon,
            "radius": ref.radius_m or 5000,
            "includeHotelData": True,
            "limit": min(50, ctx.max_results * 2),
        }
        resp = await self.http.request(
            "POST", f"{SEARCH_BASE}/hotels/rates", json=body, headers=self._headers(), retry_on_post=True
        )
        payload = resp.json()
        hotels = {str(h.get("id")): h for h in payload.get("hotels") or []}
        sandbox = bool(payload.get("sandbox", self.sandbox))
        out = []
        for hr in payload.get("data") or []:
            r = rate_to_result(hr, hotels.get(str(hr.get("hotelId"))), intent.stay.nights, ref.place.point, sandbox)
            if r is not None:
                out.append(r)
        return out

    async def revalidate(self, result: Result, ctx: SearchContext) -> Result:
        """Prebook: re-prices the offer and returns a copy carrying the prebook id."""
        self.ensure_configured()
        offer_id = result.facts.get("offer_id")
        if not offer_id:
            raise BookingError("offer has no id", code="offer_gone")
        try:
            resp = await self.http.request(
                "POST",
                f"{BOOK_BASE}/rates/prebook",
                json={"offerId": offer_id, "usePaymentSdk": False},
                headers=self._headers(),
            )
        except ProviderError as exc:
            raise BookingError("offer is no longer available", code="offer_gone") from exc
        data = resp.json().get("data") or {}
        price = _money({"amount": data.get("price"), "currency": data.get("currency")}) if data.get("price") else None
        fresh = result.model_copy(deep=True)
        if price is not None and fresh.price is not None:
            fresh.price = fresh.price.model_copy(update={"amount": price})
        fresh.facts["prebook_id"] = data.get("prebookId")
        fresh.facts["cancellation_changed"] = bool(data.get("cancellationChanged"))
        fresh.provenance = fresh.provenance.model_copy(update={"retrieved_at": utcnow()})
        return fresh

    async def book(
        self, prebooked: Result, holder: dict[str, str], guests: list[dict[str, Any]], idempotency_key: str
    ) -> dict[str, Any]:
        self.ensure_configured()
        body = {
            "prebookId": prebooked.facts.get("prebook_id"),
            "holder": holder,
            "guests": guests,
            "payment": {"method": "ACC_CREDIT_CARD"},
            "clientReference": idempotency_key,
        }
        try:
            resp = await self.http.request("POST", f"{BOOK_BASE}/rates/book", json=body, headers=self._headers())
        except ProviderError as exc:
            raise BookingError(f"hotel booking failed ({exc.code})") from exc
        data = resp.json().get("data") or {}
        status = str(data.get("status") or "").upper()
        return {
            "reference": data.get("hotelConfirmationCode") or data.get("bookingId"),
            "booking_id": data.get("bookingId"),
            "status": "confirmed" if status == "CONFIRMED" else "failed" if status == "CANCELED" else "submitted",
        }

    async def aclose(self) -> None:
        await self.http.aclose()
