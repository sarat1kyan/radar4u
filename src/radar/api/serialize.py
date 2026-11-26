"""JSON views for the Mini App. Facts and their provenance travel together."""

from __future__ import annotations

from typing import Any

from radar import i18n
from radar.bot.render import constraint_label, intent_summary, labels, reason_text
from radar.domain.intents import SearchIntent
from radar.search.draft import draft_from_intent
from radar.search.pipeline import SearchOutcome
from radar.search.ranking import Ranked
from radar.search.status import open_status, reference_time


def ranked_view(r: Ranked, intent: SearchIntent, lang: str) -> dict[str, Any]:
    res = r.result
    state, change = open_status(res, reference_time(intent))
    status = None
    if state == "24_7":
        status = {"state": "open", "label": i18n.t("status.open_24_7", lang)}
    elif state == "open":
        status = {
            "state": "open",
            "label": i18n.t("status.open_until", lang, time=change.strftime("%H:%M"))
            if change
            else i18n.t("status.open", lang),
        }
    elif state == "closed":
        status = {
            "state": "closed",
            "label": i18n.t(
                "status.closed_opens",
                lang,
                time=change.strftime("%a %H:%M")
                if change and change.date() != reference_time(intent).date()
                else change.strftime("%H:%M"),
            )
            if change
            else i18n.t("status.closed", lang),
        }
    features = [
        constraint_label(k, lang)
        for k, v in res.attributes.items()
        if v.value == "yes" and k not in ("direct", "deal.rent", "deal.sale", "condition.new")
    ][:4]
    return {
        "status": status,
        "features": features,
        "has_website": any(lk.kind.value in ("website", "booking") for lk in res.links),
        "has_phone": any(lk.kind.value == "phone" for lk in res.links),
        "id": res.id,
        "tier": r.tier.name.lower(),
        "domain": res.domain.value,
        "title": res.title,
        "subtitle": res.subtitle,
        "address": res.address,
        "point": res.point.model_dump() if res.point else None,
        "distance_m": res.distance_m,
        "distance": i18n.distance(res.distance_m, lang) if res.distance_m is not None else None,
        "starts_at": res.starts_at.isoformat() if res.starts_at else None,
        "ends_at": res.ends_at.isoformat() if res.ends_at else None,
        "duration_min": res.duration_min,
        "transfers": res.transfers,
        "legs": [leg.model_dump(mode="json") for leg in res.legs],
        "price": {
            "amount": str(res.price.amount.amount),
            "currency": res.price.amount.currency,
            "formatted": i18n.money(res.price.amount, lang),
            "kind": res.price.kind.value,
            "unit": res.price.unit.value,
            "unknown_fees": res.price.unknown_fees,
            "payable_at_property": i18n.money(res.price.payable_at_property, lang)
            if res.price.payable_at_property
            else None,
        }
        if res.price
        else None,
        "converted": i18n.money(r.converted.converted, lang) if r.converted else None,
        "rating": res.rating.model_dump() if res.rating else None,
        "availability": res.availability.value,
        "booking": res.booking.value,
        "links": [lk.model_dump() for lk in res.links if lk.url.startswith(("https://", "tel:"))],
        "met": labels(r.report.met, intent, lang),
        "unverified": labels(r.report.unverified, intent, lang),
        "unmet": labels(r.report.unmet, intent, lang),
        "reasons": [reason_text(k, p, intent, lang) for k, p in r.reasons if k.startswith("reason.")],
        "opening_hours": res.facts.get("opening_hours") if isinstance(res.facts.get("opening_hours"), str) else None,
        "photo": res.facts.get("photo") if isinstance(res.facts.get("photo"), str) else None,
        "stars": res.facts.get("stars") if isinstance(res.facts.get("stars"), int) else None,
        "phone": next((lk.url[4:] for lk in res.links if lk.url.startswith("tel:")), None),
        "unverified_keys": r.report.unverified,
        "met_keys": r.report.met,
        "unmet_keys": r.report.unmet,
        "duration": i18n.t(
            "result.duration", lang, hours=res.duration_min // 60, minutes=f"{res.duration_min % 60:02d}"
        )
        if res.duration_min is not None
        else None,
        "provenance": {
            "provider": res.provenance.provider,
            "freshness": res.provenance.freshness.value,
            "retrieved_at": res.provenance.retrieved_at.isoformat(),
            "attribution": res.provenance.attribution,
            "source_url": res.provenance.source_url,
        },
    }


def outcome_view(o: SearchOutcome, lang: str, session_id: int | None = None) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "draft": draft_from_intent(o.intent),
        "summary": intent_summary(o.intent, lang),
        "domain": o.intent.domain.value,
        "missing": o.intent.missing,
        "counts": {"exact": len(o.exact), "unverified": len(o.unverified), "alternatives": len(o.alternatives)},
        "headers": {
            "exact": i18n.t("search.exact_header", lang, count=len(o.exact)),
            "unverified": i18n.t("search.unverified_header", lang, count=len(o.unverified)),
            "alternative": i18n.t("search.alternatives_header", lang),
        },
        "items": [ranked_view(r, o.intent, lang) for r in o.ranked[:40]],
        "provider_errors": o.provider_errors,
        "skipped": o.skipped,
        "queried": o.queried,
        "took_ms": round(o.total_ms),
    }
