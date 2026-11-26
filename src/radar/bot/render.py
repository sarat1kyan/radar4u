"""Text rendering for Telegram (HTML parse mode).

All provider text is untrusted: it is HTML-escaped and length-limited here,
and every card states where its data came from and how old it is.
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any

from radar import i18n
from radar.core.money import Money
from radar.core.provenance import Freshness, Tri, utcnow
from radar.domain.common import BudgetUnit, Domain
from radar.domain.intents import Constraint, SearchIntent
from radar.domain.results import Availability, PriceKind, Result
from radar.itinerary.planner import ItemStatus, Itinerary
from radar.search.filters import Tier
from radar.search.pipeline import SearchOutcome
from radar.search.ranking import Ranked

PAGE_SIZE = 5


def esc(s: object, limit: int = 200) -> str:
    text = str(s)
    if len(text) > limit:
        text = text[: limit - 1] + "..."
    return html.escape(text, quote=False)


def constraint_label(key: str, lang: str, value: object = None) -> str:
    k = f"constraint.{key}"
    return i18n.t(k, lang, value=value) if i18n.has(k) else key


def labels(keys: list[str], intent: SearchIntent | None, lang: str) -> str:
    out = []
    for k in keys:
        c: Constraint | None = intent.requirement(k) if intent else None
        if c is None and intent is not None:
            c = next((p for p in intent.preferences if p.key == k), None)
        out.append(constraint_label(k, lang, c.value if c else None))
    return ", ".join(out)


def money(m: Money, lang: str) -> str:
    return i18n.money(m, lang)


def intent_summary(intent: SearchIntent, lang: str, clock: str = "24h") -> str:
    parts = [i18n.t(f"domain.{intent.domain.value}", lang)]
    cats = [
        c.split(":", 1)[1].replace("_", " ")
        for c in intent.categories
        if c.split(":", 1)[0] in ("cuisine", "place", "event", "property")
    ]
    if cats:
        parts.append(", ".join(cats[:3]))
    if intent.domain.is_transport:
        if intent.origin and intent.destination:
            parts.append(f"{esc(_place_name(intent.origin))} → {esc(_place_name(intent.destination))}")
    elif (loc := intent.location or intent.destination) is not None:
        parts.append(esc(_place_name(loc)))
    if intent.stay:
        parts.append(f"{i18n.fdate(intent.stay.check_in, lang)} - {i18n.fdate(intent.stay.check_out, lang)}")
    elif intent.when and intent.when.start:
        s = intent.when.start
        if (s.hour, s.minute) == (0, 0) and not intent.arrive_by:
            parts.append(i18n.fdate(s, lang))
        elif intent.arrive_by and intent.when.end:
            parts.append(
                f"{i18n.fdate(s, lang)}, "
                + constraint_label("arrive_before", lang, i18n.ftime(intent.when.end, lang, clock))
            )
        else:
            parts.append(i18n.fdatetime(s, lang, clock))
    if intent.party.size > 1 or intent.domain in (Domain.DINING, Domain.STAY):
        parts.append(i18n.t("wizard.party", lang, count=intent.party.size))
    if intent.budget.max:
        parts.append("<= " + money(intent.budget.max, lang))
    line = " | ".join(parts)
    if intent.requirements:
        line += "\n+ " + labels([c.key for c in intent.requirements], intent, lang)
    if intent.preferences:
        line += "\n~ " + labels([c.key for c in intent.preferences], intent, lang)
    return line


def _place_name(ref: Any) -> str:
    return str(ref.place.name if ref.place else ref.text)


def _duration(minutes: int | None, lang: str) -> str:
    if minutes is None:
        return ""
    return i18n.t("result.duration", lang, hours=minutes // 60, minutes=f"{minutes % 60:02d}")


def price_text(r: Ranked, lang: str) -> str | None:
    p = r.result.price
    if p is None:
        return None
    base = money(p.amount, lang)
    if p.unit is BudgetUnit.NIGHT:
        base = i18n.t("result.per_night", lang, price=base)
    elif p.unit is BudgetUnit.MONTH:
        base = i18n.t("result.per_month", lang, price=base)
    if p.kind is PriceKind.FROM:
        base = i18n.t("result.price_from", lang, price=base)
    if r.converted is not None:
        base += " (" + i18n.t("result.price_converted", lang, converted=money(r.converted.converted, lang)) + ")"
    extras = []
    if p.kind is PriceKind.ESTIMATE:
        extras.append(i18n.t("result.estimate_short", lang))
    if p.payable_at_property is not None:
        extras.append(i18n.t("result.pay_at_property", lang, amount=money(p.payable_at_property, lang)))
    if p.unknown_fees and p.kind is not PriceKind.ESTIMATE:
        extras.append(i18n.t("result.taxes_unknown", lang))
    return base + (f" [{'; '.join(extras)}]" if extras else "")


def _times(r: Result, lang: str, clock: str) -> str:
    if not (r.starts_at and r.ends_at):
        return ""
    s = f"{i18n.ftime(r.starts_at, lang, clock)} → {i18n.ftime(r.ends_at, lang, clock)}"
    if r.ends_at.date() != r.starts_at.date():
        s += " (+1)"
    return s + f" ({_duration(r.duration_min, lang)})"


def result_line(n: int, r: Ranked, intent: SearchIntent, lang: str, clock: str = "24h") -> str:
    res = r.result
    head = f"<b>{n}. {esc(res.title, 80)}</b>"
    bits: list[str] = []
    if res.legs:
        bits.append(_times(res, lang, clock))
        bits.append(
            i18n.t("result.direct", lang)
            if res.transfers == 0
            else i18n.t("result.transfers", lang, count=res.transfers or 0)
        )
    elif res.starts_at and intent.domain is Domain.EVENT:
        bits.append(i18n.fdatetime(res.starts_at, lang, clock))
    if res.subtitle and not res.legs:
        bits.append(esc(res.subtitle, 60))
    if res.distance_m is not None and not res.legs:
        bits.append(i18n.distance(res.distance_m, lang))
    if (pt := price_text(r, lang)) is not None:
        bits.append(pt)
    if res.rating:
        bits.append(f"{i18n.number(res.rating.value, lang)}/{i18n.number(res.rating.scale, lang, '0')}")
    line = head + ("\n   " + " | ".join(b for b in bits if b) if bits else "")
    if r.report.unverified:
        line += "\n   " + i18n.t("result.unconfirmed_short", lang, items=labels(r.report.unverified, intent, lang))
    if r.tier is Tier.ALTERNATIVE and r.report.unmet:
        line += "\n   " + i18n.t("result.unmet", lang, items=labels(r.report.unmet, intent, lang))
    if res.provenance.freshness is Freshness.FIXTURE:
        line += "\n   <i>" + i18n.t("result.fixture", lang) + "</i>"
    return line


def outcome_text(o: SearchOutcome, lang: str, page: int = 0, clock: str = "24h") -> tuple[str, int]:
    """Returns (text, pages)."""
    intent = o.intent
    lines = [i18n.t("search.understood", lang, summary=intent_summary(intent, lang, clock))]
    if o.no_coverage:
        lines.append("")
        lines.append(coverage_text(o, lang))
        return "\n".join(lines), 0
    items = o.ranked
    pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, pages - 1)
    chunk = items[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    if page == 0:
        lines.append("")
        if o.exact:
            lines.append(i18n.t("search.exact_header", lang, count=len(o.exact)))
        elif o.unverified:
            lines.append(i18n.t("search.unverified_header", lang, count=len(o.unverified)))
        elif o.alternatives:
            lines.append(i18n.t("search.no_results", lang) + " " + i18n.t("search.alternatives_header", lang))
        else:
            lines.append(i18n.t("search.no_results", lang))
    shown_alt_header = page > 0
    for idx, r in enumerate(chunk, start=page * PAGE_SIZE + 1):
        if r.tier is Tier.ALTERNATIVE and not shown_alt_header and (o.exact or o.unverified):
            lines.append("\n" + i18n.t("search.alternatives_header", lang))
            shown_alt_header = True
        lines.append(result_line(idx, r, intent, lang, clock))
    if o.provider_errors:
        lines.append("\n<i>" + i18n.t("search.partial", lang, providers=", ".join(sorted(o.provider_errors))) + "</i>")
    if any(r.report.unverified for r in chunk):
        lines.append("<i>" + i18n.t("result.unverified_note", lang) + "</i>")
    srcs = sorted({r.result.provenance.attribution or r.result.provenance.provider for r in chunk})
    if srcs:
        lines.append(
            "<i>"
            + i18n.t("result.source", lang, source=esc("; ".join(srcs), 300), age=i18n.age(_oldest_age(chunk), lang))
            + "</i>"
        )
    if pages > 1:
        lines.append(i18n.t("search.page", lang, page=page + 1, pages=pages))
    return "\n".join(lines), pages


def _oldest_age(items: list[Ranked]) -> float:
    now = utcnow()
    return max((now - r.result.provenance.retrieved_at).total_seconds() for r in items) if items else 0.0


def coverage_text(o: SearchOutcome, lang: str) -> str:
    details = []
    by_reason: dict[str, list[str]] = {}
    for name, reason in o.skipped.items():
        by_reason.setdefault(reason, []).append(name)
    if by_reason.get("not_configured"):
        details.append(i18n.t("search.coverage.not_configured", lang, names=", ".join(by_reason["not_configured"])))
    if by_reason.get("non_commercial_terms"):
        details.append(
            i18n.t("search.coverage.non_commercial", lang, names=", ".join(by_reason["non_commercial_terms"]))
        )
    return i18n.t("search.no_coverage", lang, details=" ".join(details))


def result_card(r: Ranked, intent: SearchIntent, lang: str, clock: str = "24h") -> str:
    res = r.result
    lines = [f"<b>{esc(res.title, 120)}</b>"]
    if res.provenance.freshness is Freshness.FIXTURE:
        lines.append("<b>" + i18n.t("result.fixture", lang) + "</b>")
    if res.subtitle:
        lines.append(esc(res.subtitle, 160))
    if res.address:
        lines.append(esc(res.address, 160))
    if res.distance_m is not None:
        lines.append(i18n.t("result.distance", lang, distance=i18n.distance(res.distance_m, lang)))
    if res.starts_at and not res.legs:
        lines.append(i18n.fdatetime(res.starts_at, lang, clock))
    for leg in res.legs:
        t1 = i18n.ftime(leg.origin.time, lang, clock) if leg.origin.time else "?"
        t2 = i18n.ftime(leg.destination.time, lang, clock) if leg.destination.time else "?"
        plat = (
            f", {i18n.t('result.platform', lang, platform=esc(leg.origin.platform, 10))}" if leg.origin.platform else ""
        )
        svc = " ".join(x for x in (leg.service, f"({leg.carrier})" if leg.carrier else None) if x)
        lines.append(f"{t1} {esc(leg.origin.name, 60)}{plat}\n   {esc(svc, 80)}\n{t2} {esc(leg.destination.name, 60)}")
        if leg.cancelled:
            lines.append("<b>" + i18n.t("result.cancelled", lang) + "</b>")
    for c in res.connections:
        if c.self_transfer is Tri.YES:
            lines.append(i18n.t("result.self_transfer", lang))
        if c.station_change:
            lines.append(i18n.t("result.station_change", lang, station=esc(c.at, 60)))
    if res.legs:
        lines.append(_times(res, lang, clock))
        if res.facts.get("realtime"):
            lines.append(i18n.t("result.realtime", lang))
        if res.provenance.freshness is Freshness.TIMETABLE:
            lines.append("<i>" + i18n.t("result.timetable_only", lang) + "</i>")
    if (pt := price_text(r, lang)) is not None:
        lines.append(i18n.t("result.price", lang, price=pt))
    if res.price and res.price.kind is PriceKind.ESTIMATE:
        lines.append("<i>" + i18n.t("result.price_estimate", lang) + "</i>")
    if res.rating:
        lines.append(
            i18n.t(
                "result.rating",
                lang,
                value=i18n.number(res.rating.value, lang),
                scale=i18n.number(res.rating.scale, lang, "0"),
                count=res.rating.count or "?",
                source=esc(res.rating.source, 40),
            )
        )
    if isinstance(bed := res.facts.get("bedrooms"), int):
        lines.append(i18n.t("result.bedrooms", lang, count=bed))
    if isinstance(area := res.facts.get("area_m2"), int | float):
        lines.append(i18n.t("result.area", lang, value=i18n.number(area, lang)))
    hours = res.facts.get("opening_hours")
    if intent.domain in (Domain.DINING, Domain.WELLNESS):
        if "open_at" in r.report.met:
            lines.append(i18n.t("result.open_now", lang))
        elif "open_at" in r.report.unmet:
            lines.append(i18n.t("result.closed_then", lang))
        elif not hours:
            lines.append(i18n.t("result.hours_unknown", lang))
        if isinstance(hours, str):
            lines.append(esc(hours, 120))
        lines.append("<i>" + i18n.t("result.hours_not_availability", lang) + "</i>")
    lines.append(i18n.t(f"result.availability.{res.availability.value}", lang))
    if r.report.met:
        lines.append(i18n.t("result.met", lang, items=labels(r.report.met, intent, lang)))
    if r.report.unverified:
        lines.append(i18n.t("result.unverified", lang, items=labels(r.report.unverified, intent, lang)))
    if r.report.unmet:
        lines.append(i18n.t("result.unmet", lang, items=labels(r.report.unmet, intent, lang)))
    why = [reason_text(k, p, intent, lang) for k, p in r.reasons if k.startswith("reason.")]
    if why:
        lines.append(i18n.t("result.why", lang, reasons="; ".join(why)))
    age = (utcnow() - res.provenance.retrieved_at).total_seconds()
    lines.append(
        "<i>"
        + i18n.t(
            "result.source",
            lang,
            source=esc(res.provenance.attribution or res.provenance.provider, 120),
            age=i18n.age(age, lang),
        )
        + "</i>"
    )
    return "\n".join(lines)


def reason_text(key: str, params: dict[str, Any], intent: SearchIntent, lang: str) -> str:
    p = dict(params)
    if "items" in p:
        p["items"] = labels(list(p["items"]), intent, lang)
    return i18n.t(key, lang, **p)


# --- storage of ranked results in the search session ------------------------------------------


def dump_ranked(items: list[Ranked], limit: int = 40) -> list[dict[str, Any]]:
    out = []
    for r in items[:limit]:
        out.append(
            {
                "result": r.result.model_dump(mode="json"),
                "met": r.report.met,
                "unmet": r.report.unmet,
                "unverified": r.report.unverified,
                "pref_met": r.report.pref_met,
                "pref_unmet": r.report.pref_unmet,
                "pref_unknown": r.report.pref_unknown,
                "price_in_budget": r.report.price_in_budget_ccy.model_dump(mode="json")
                if r.report.price_in_budget_ccy
                else None,
                "converted": r.converted.as_dict() if r.converted else None,
                "score": r.score,
                "reasons": [[k, v] for k, v in r.reasons],
            }
        )
    return out


def load_ranked(rows: list[dict[str, Any]]) -> list[Ranked]:
    from datetime import datetime as _dt
    from decimal import Decimal

    from radar.core.money import ConvertedMoney, FxRate
    from radar.search.filters import MatchReport

    out = []
    for row in rows:
        rep = MatchReport(
            met=row["met"],
            unmet=row["unmet"],
            unverified=row["unverified"],
            pref_met=row["pref_met"],
            pref_unmet=row["pref_unmet"],
            pref_unknown=row["pref_unknown"],
            price_in_budget_ccy=Money.model_validate(row["price_in_budget"]) if row.get("price_in_budget") else None,
        )
        conv = None
        if c := row.get("converted"):
            orig, cv = Money.model_validate(c["original"]), Money.model_validate(c["converted"])
            conv = ConvertedMoney(
                orig,
                cv,
                FxRate(
                    orig.currency, cv.currency, Decimal(c["rate"]), _dt.fromisoformat(c["rate_as_of"]), c["rate_source"]
                ),
            )
        out.append(
            Ranked(
                result=Result.model_validate(row["result"]),
                report=rep,
                score=row["score"],
                reasons=[(k, v) for k, v in row["reasons"]],
                converted=conv,
            )
        )
    return out


def outcome_from_session(intent: SearchIntent, rows: list[dict[str, Any]], summary: dict[str, Any]) -> SearchOutcome:
    o = SearchOutcome(intent=intent, ranked=load_ranked(rows))
    o.provider_errors = summary.get("errors", {})
    o.skipped = summary.get("skipped", {})
    o.queried = summary.get("queried", [])
    o.partner_only = summary.get("partner_only", [])
    return o


# --- notifications --------------------------------------------------------------------------------


def notification_text(kind: str, payload: dict[str, Any], lang: str) -> str:
    title = esc(payload.get("title") or "", 100)
    if kind == "expired":
        return i18n.t("radar.expired", lang, name=esc(payload.get("name", ""), 80))
    lines = [f"<b>{i18n.t('radar.alert.title', lang, name=esc(payload.get('name', ''), 80))}</b>"]
    new = Money.model_validate(payload["new"]) if payload.get("new") else None
    old = Money.model_validate(payload["old"]) if payload.get("old") else None
    if kind == "price_drop" and new is not None:
        lines.append(title)
        if old is not None:
            lines.append(i18n.t("radar.alert.price_drop", lang, old=money(old, lang), new=money(new, lang)))
        else:
            lines.append(i18n.t("result.price", lang, price=money(new, lang)))
    elif kind == "now_available":
        lines.append(i18n.t("radar.alert.now_available", lang, title=title))
    elif kind == "new_result":
        lines.append(i18n.t("radar.alert.new_result", lang, title=title))
        if new is not None:
            lines.append(i18n.t("result.price", lang, price=money(new, lang)))
    if payload.get("freshness") == Freshness.CACHED_ESTIMATE.value:
        lines.append("<i>" + i18n.t("result.price_estimate", lang) + "</i>")
    if payload.get("freshness") == Freshness.FIXTURE.value:
        lines.append("<i>" + i18n.t("result.fixture", lang) + "</i>")
    met = [m for m in payload.get("met", []) if m not in ("time_window",)]
    if met:
        lines.append(i18n.t("radar.alert.why", lang, reasons=", ".join(constraint_label(m, lang) for m in met)))
    if payload.get("retrieved_at"):
        when = datetime.fromisoformat(payload["retrieved_at"])
        lines.append(
            "<i>"
            + i18n.t(
                "radar.alert.checked",
                lang,
                time=i18n.age((utcnow() - when).total_seconds(), lang),
                source=esc(payload.get("source") or "", 80),
            )
            + "</i>"
        )
    return "\n".join(lines)


# --- itineraries ----------------------------------------------------------------------------------


def itinerary_text(it: Itinerary, lang: str, clock: str = "24h") -> str:
    lines = [f"<b>{i18n.t('plan.header', lang, title=esc(it.title, 80))}</b>"]
    start_day = min((i.start.date() for i in it.items), default=None)
    current_day = None
    for item in sorted(it.items, key=lambda i: i.start):
        day = (item.start.date() - start_day).days + 1 if start_day else 1
        if day != current_day:
            current_day = day
            wx = it.weather.get(item.start.date().isoformat())
            head = f"\n<b>{i18n.t('plan.day', lang, n=day)}</b> - {i18n.fdate(item.start, lang)}"
            if wx:
                head += " - " + i18n.t("plan.weather", lang, summary=i18n.t(f"weather.{wx}", lang))
            lines.append(head)
        status = i18n.t(f"plan.item.{item.status.value}", lang) if item.status is not ItemStatus.EMPTY else ""
        when = f"{i18n.ftime(item.start, lang, clock)}-{i18n.ftime(item.end, lang, clock)}"
        if item.status is ItemStatus.EMPTY:
            lines.append(f"{when} {i18n.t('plan.empty_slot', lang)}")
            continue
        text = f"{when} {esc(item.title, 80)} <i>({status})</i>"
        if item.price is not None:
            text += f" - {money(item.price, lang)}"
        if item.note and "unverified" in item.note:
            text += f" <i>[{i18n.t('plan.unverified_item', lang)}]</i>"
        lines.append(text)
        if item.note and "times_estimated" in item.note:
            lines.append("<i>" + i18n.t("plan.estimated_times", lang) + "</i>")
    if it.budget is not None:
        total, unknown = it.budget_status()
        lines.append(
            "\n"
            + i18n.t(
                "plan.budget",
                lang,
                spent=money(total, lang) if total else "0",
                budget=money(it.budget, lang),
                unknown=unknown,
            )
        )
    for w in it.warnings:
        params = dict(w.params)
        if "weather" in params:
            params["weather"] = i18n.t(f"weather.{params['weather']}", lang)
        lines.append("! " + i18n.t(f"plan.warning.{w.code}", lang, **{k: esc(v, 80) for k, v in params.items()}))
    return "\n".join(lines)


def availability_label(a: Availability, lang: str) -> str:
    return i18n.t(f"result.availability.{a.value}", lang)
