"""Rich-message views for search results, detail cards and plans.

Each function returns (rich document, classic HTML fallback, inline keyboard).
"""

from __future__ import annotations

from datetime import date
from urllib.parse import quote

from aiogram.types import InlineKeyboardMarkup

from radar import i18n
from radar.bot import keyboards as kb
from radar.bot import render, ui
from radar.core.provenance import Freshness, Tri, utcnow
from radar.domain.common import Domain
from radar.domain.intents import SearchIntent
from radar.domain.results import BookingMode, LinkKind
from radar.itinerary.planner import ItemStatus, Itinerary
from radar.search.filters import Tier
from radar.search.pipeline import SearchOutcome
from radar.search.ranking import Ranked

PAGE = render.PAGE_SIZE


def T(key: str, lang: str, **kw: object) -> str:
    return i18n.t(key, lang, **kw)


def _tier_title(o: SearchOutcome, tier: Tier, lang: str) -> str:
    if tier is Tier.EXACT:
        return T("search.exact_header", lang, count=len(o.exact))
    if tier is Tier.UNVERIFIED:
        return T("search.unverified_header", lang, count=len(o.unverified))
    return T("search.alternatives_header", lang)


def _meta(r: Ranked, intent: SearchIntent, lang: str, clock: str) -> list[str]:
    res = r.result
    bits: list[str] = []
    if res.subtitle and not res.legs:
        bits.append(ui.esc(res.subtitle, 60))
    if res.distance_m is not None and not res.legs:
        bits.append(i18n.distance(res.distance_m, lang))
    if res.starts_at and intent.domain is Domain.EVENT:
        bits.append(i18n.fdatetime(res.starts_at, lang, clock))
    if intent.domain in (Domain.DINING, Domain.WELLNESS):
        if "open_at" in r.report.met:
            bits.append(T("result.open_now", lang))
        elif "open_at" in r.report.unmet:
            bits.append(T("result.closed_then", lang))
    if res.rating:
        bits.append(f"{i18n.number(res.rating.value, lang)}/{i18n.number(res.rating.scale, lang, '0')}")
    if isinstance(res.facts.get("stars"), int):
        bits.append(T("result.stars", lang, count=res.facts["stars"]))
    return bits


def results_view(
    o: SearchOutcome, sid: int, lang: str, page: int = 0, clock: str = "24h"
) -> tuple[ui.Rich, str, InlineKeyboardMarkup | None]:
    intent = o.intent
    fallback, pages = render.outcome_text(o, lang, page, clock)
    r = ui.Rich()
    r.h(render.intent_summary(intent, lang, clock).split("\n")[0], 3)
    extra = render.intent_summary(intent, lang, clock).split("\n")[1:]
    if extra:
        r.p("<i>" + "<br>".join(extra) + "</i>")
    if o.no_coverage:
        r.p(render.coverage_text(o, lang))
        return r, fallback, ui.kb([[(T("search.new", lang), kb.MenuCb(a="home").pack(), "primary")]])
    if not o.ranked:
        r.p(T("search.no_results", lang))
    chunk = o.ranked[page * PAGE : (page + 1) * PAGE]
    current_tier: Tier | None = None
    transport = intent.domain.is_transport
    rows: list[list[str]] = []
    for n, item in enumerate(chunk, start=page * PAGE + 1):
        if item.tier is not current_tier:
            if rows:
                _flush_table(r, rows, lang)
                rows = []
            current_tier = item.tier
            r.h(_tier_title(o, item.tier, lang), 4)
        res = item.result
        if transport:
            dep = i18n.ftime(res.starts_at, lang, clock) if res.starts_at else "?"
            arr = i18n.ftime(res.ends_at, lang, clock) if res.ends_at else "?"
            if res.starts_at and res.ends_at and res.ends_at.date() != res.starts_at.date():
                arr += " +1"
            dur = render._duration(res.duration_min, lang)
            chg = T("result.direct", lang) if res.transfers == 0 else str(res.transfers or 0)
            price = render.price_text(item, lang) or "-"
            rows.append([f"<b>{n}</b>", f"<b>{dep}</b>", arr, dur, chg, ui.esc(res.title, 40), price])
            continue
        title = f"{n}. {ui.esc(res.title, 70)}"
        if res.provenance.freshness is Freshness.FIXTURE:
            title += " <i>(demo)</i>"
        r.h(title, 4)
        lines = [" | ".join(_meta(item, intent, lang, clock))]
        if (pt := render.price_text(item, lang)) is not None:
            lines.append(f"<b>{pt}</b>")
        if item.report.unverified:
            lines.append(
                "<i>"
                + T("result.unconfirmed_short", lang, items=render.labels(item.report.unverified, intent, lang))
                + "</i>"
            )
        if item.tier is Tier.ALTERNATIVE and item.report.unmet:
            lines.append(T("result.unmet", lang, items=render.labels(item.report.unmet, intent, lang)))
        r.p("<br>".join(x for x in lines if x))
    if rows:
        _flush_table(r, rows, lang)
    notes = []
    if o.provider_errors:
        notes.append(T("search.partial", lang, providers=", ".join(sorted(o.provider_errors))))
    if any(i.report.unverified for i in chunk):
        notes.append(T("result.unverified_note", lang))
    srcs = sorted({i.result.provenance.attribution or i.result.provenance.provider for i in chunk})
    if srcs:
        age = max((utcnow() - i.result.provenance.retrieved_at).total_seconds() for i in chunk)
        notes.append(T("result.source", lang, source=ui.esc("; ".join(srcs), 300), age=i18n.age(age, lang)))
    if pages > 1:
        notes.append(T("search.page", lang, page=page + 1, pages=pages))
    if notes:
        r.footer("<br>".join(notes))

    nums = [
        (str(n), kb.ResultCb(a="d", s=sid, i=i.result.id).pack(), "primary" if i.tier is Tier.EXACT else None)
        for n, i in enumerate(chunk, start=page * PAGE + 1)
    ]
    kb_rows: list[list[tuple[str, str, str | None]]] = [nums] if nums else []
    nav: list[tuple[str, str, str | None]] = []
    if page > 0:
        nav.append(("<", kb.PageCb(s=sid, p=page - 1).pack(), None))
    if page + 1 < pages:
        nav.append((T("search.more", lang), kb.PageCb(s=sid, p=page + 1).pack(), None))
    if nav:
        kb_rows.append(nav)
    kb_rows.append(
        [
            (T("search.sort", lang), kb.SortCb(s=sid, m="").pack(), None),
            (T("search.watch", lang), kb.WatchCb(s=sid, k="").pack(), "success"),
        ]
    )
    kb_rows.append([(T("search.new", lang), kb.MenuCb(a="home").pack(), None)])
    return r, fallback, ui.kb(kb_rows)


def _flush_table(r: ui.Rich, rows: list[list[str]], lang: str) -> None:
    header = [
        "#",
        T("table.depart", lang),
        T("table.arrive", lang),
        T("table.duration", lang),
        T("table.changes", lang),
        T("table.service", lang),
        T("table.price", lang),
    ]
    if all(row[-1] == "-" for row in rows):
        header = header[:-1]
        rows = [row[:-1] for row in rows]
    r.table(rows, header, striped=True, compact=True)


def card_view(
    item: Ranked, intent: SearchIntent, sid: int, lang: str, clock: str = "24h"
) -> tuple[ui.Rich, str, InlineKeyboardMarkup]:
    res = item.result
    fallback = render.result_card(item, intent, lang, clock)
    r = ui.Rich()
    photo = res.facts.get("photo")
    if isinstance(photo, str):
        r.img(photo)
    r.h(ui.esc(res.title, 120), 2)
    if res.provenance.freshness is Freshness.FIXTURE:
        r.p("<b>" + T("result.fixture", lang) + "</b>")
    sub = [x for x in (res.subtitle, res.address) if x]
    if sub:
        r.p(" | ".join(ui.esc(x, 120) for x in sub))

    facts: list[list[str]] = []
    if (pt := render.price_text(item, lang)) is not None:
        facts.append([T("table.price", lang), f"<b>{pt}</b>"])
    if res.rating:
        facts.append(
            [
                T("table.rating", lang),
                T(
                    "result.rating",
                    lang,
                    value=i18n.number(res.rating.value, lang),
                    scale=i18n.number(res.rating.scale, lang, "0"),
                    count=res.rating.count or "?",
                    source=ui.esc(res.rating.source, 30),
                ),
            ]
        )
    if isinstance(res.facts.get("stars"), int):
        facts.append([T("table.class", lang), T("result.stars", lang, count=res.facts["stars"])])
    if res.distance_m is not None:
        facts.append([T("table.distance", lang), i18n.distance(res.distance_m, lang)])
    hours = res.facts.get("opening_hours")
    if isinstance(hours, str):
        facts.append([T("table.hours", lang), ui.esc(hours, 120)])
    if res.starts_at and not res.legs:
        facts.append([T("sb.when", lang), i18n.fdatetime(res.starts_at, lang, clock)])
    if isinstance(bed := res.facts.get("bedrooms"), int):
        facts.append([T("sb.rooms", lang), T("result.bedrooms", lang, count=bed)])
    facts.append([T("table.availability", lang), T(f"result.availability.{res.availability.value}", lang)])
    r.table(facts, striped=True, compact=True)

    if res.legs:
        legs = []
        for leg in res.legs:
            t1 = i18n.ftime(leg.origin.time, lang, clock) if leg.origin.time else "?"
            t2 = i18n.ftime(leg.destination.time, lang, clock) if leg.destination.time else "?"
            plat = ui.esc(leg.origin.platform, 8) if leg.origin.platform else ""
            legs.append([f"<b>{t1}</b>", ui.esc(leg.origin.name, 40), plat, ui.esc(leg.service or leg.mode, 30)])
            legs.append([f"<b>{t2}</b>", ui.esc(leg.destination.name, 40), "", ""])
        r.table(
            legs,
            [T("table.time", lang), T("table.stop", lang), T("table.platform", lang), T("table.service", lang)],
            striped=False,
            compact=True,
        )
        for c in res.connections:
            if c.self_transfer is Tri.YES:
                r.p("<b>" + T("result.self_transfer", lang) + "</b>")
            if c.station_change:
                r.p(T("result.station_change", lang, station=ui.esc(c.at, 60)))
        if res.provenance.freshness is Freshness.TIMETABLE:
            r.p("<i>" + T("result.timetable_only", lang) + "</i>")

    checks = []
    for key in item.report.met:
        checks.append(f'<li><input type="checkbox" checked>{render.constraint_label(key, lang)}</li>')
    for key in item.report.unverified:
        checks.append(
            f'<li><input type="checkbox">{render.constraint_label(key, lang)} <i>({T("common.unknown", lang)})</i></li>'
        )
    for key in item.report.unmet:
        checks.append(f'<li><input type="checkbox"><s>{render.constraint_label(key, lang)}</s></li>')
    if checks:
        r.h(T("card.requirements", lang), 4)
        r.parts.append("<ul>" + "".join(checks) + "</ul>")

    why = [render.reason_text(k, p, intent, lang) for k, p in item.reasons if k.startswith("reason.")]
    if why:
        r.p("<i>" + T("result.why", lang, reasons="; ".join(why)) + "</i>")
    if res.point is not None and not res.legs:
        r.map(res.point.lat, res.point.lon, 16)
    if intent.domain in (Domain.DINING, Domain.WELLNESS):
        r.p("<i>" + T("result.hours_not_availability", lang) + "</i>")
    age = (utcnow() - res.provenance.retrieved_at).total_seconds()
    src = T(
        "result.source",
        lang,
        source=ui.esc(res.provenance.attribution or res.provenance.provider, 120),
        age=i18n.age(age, lang),
    )
    if isinstance(photo, str) and "wikimedia" in photo:
        src += "<br>" + T("card.photo_credit", lang)
    r.footer(src)

    rows: list[list[tuple[str, str, str | None]]] = []
    links: list[tuple[str, str, str | None]] = []
    if res.booking is BookingMode.DIRECT:
        links.append((T("result.book", lang), kb.ResultCb(a="b", s=sid, i=res.id).pack(), "success"))
    for kind, key in (
        (LinkKind.BOOKING, "result.book"),
        (LinkKind.WEBSITE, "result.website"),
        (LinkKind.MENU, "result.menu"),
    ):
        link = res.link(kind)
        if link and link.url.startswith("https://"):
            links.append((T(key, lang), link.url, "primary" if kind is LinkKind.BOOKING else None))
    if links:
        rows.append(links[:3])
    row2: list[tuple[str, str, str | None]] = []
    if res.point is not None:
        dest = quote(f"{res.point.lat:.6f},{res.point.lon:.6f}")
        row2.append((T("card.route", lang), f"https://www.google.com/maps/dir/?api=1&destination={dest}", None))
    phone = res.link(LinkKind.PHONE)
    if phone:
        row2.append((T("card.copy_phone", lang), "copy:" + phone.url.removeprefix("tel:"), None))
    src_link = res.link(LinkKind.SOURCE)
    if src_link and src_link.url.startswith("https://"):
        row2.append((T("result.source_link", lang), src_link.url, None))
    if row2:
        rows.append(row2)
    rows.append([(T("search.watch", lang), kb.WatchCb(s=sid, k="").pack(), None)])
    return r, fallback, ui.kb(rows)


def itinerary_view(it: Itinerary, lang: str, clock: str = "24h") -> tuple[ui.Rich, str]:
    fallback = render.itinerary_text(it, lang, clock)
    r = ui.Rich().h(T("plan.header", lang, title=ui.esc(it.title, 80)), 2)
    days: dict[str, list[list[str]]] = {}
    for item in sorted(it.items, key=lambda i: i.start):
        key = item.start.date().isoformat()
        when = f"{i18n.ftime(item.start, lang, clock)}-{i18n.ftime(item.end, lang, clock)}"
        if item.status is ItemStatus.EMPTY:
            days.setdefault(key, []).append([when, f"<i>{T('plan.empty_slot', lang)}</i>", ""])
            continue
        price = i18n.money(item.price, lang) if item.price else ""
        status = T(f"plan.item.{item.status.value}", lang)
        days.setdefault(key, []).append([when, f"<b>{ui.esc(item.title, 60)}</b><br><i>{status}</i>", price])
    for n, (day, rows) in enumerate(days.items(), start=1):
        head = f"{T('plan.day', lang, n=n)} - {i18n.fdate(date.fromisoformat(day), lang)}"
        if w := it.weather.get(day):
            head += f" - {T(f'weather.{w}', lang)}"
        r.h(head, 4)
        r.table(rows, striped=True, compact=True)
    if it.budget is not None:
        total, unknown = it.budget_status()
        r.p(
            T(
                "plan.budget",
                lang,
                spent=i18n.money(total, lang) if total else "0",
                budget=i18n.money(it.budget, lang),
                unknown=unknown,
            )
        )
    if it.warnings:
        items = []
        for w_ in it.warnings:
            params = dict(w_.params)
            if "weather" in params:
                params["weather"] = T(f"weather.{params['weather']}", lang)
            items.append(
                "<li>" + T(f"plan.warning.{w_.code}", lang, **{k: ui.esc(v, 80) for k, v in params.items()}) + "</li>"
            )
        r.parts.append("<ul>" + "".join(items) + "</ul>")
    return r, fallback
