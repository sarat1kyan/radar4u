"""Ranking with explanations.

Order is always tier first (exact, unverified, alternative), then a weighted
score inside the tier. Weights depend on the sort mode. Ratings from different
sources are not treated as equivalent: each is normalised and then discounted
by how many reviews back it. Sponsored placement does not exist as an input.

Every ranked result carries structured reasons (i18n key + params) that state
verifiable facts, not confidence percentages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from radar.core.money import ConvertedMoney
from radar.core.provenance import Freshness, Tri
from radar.domain.common import SortMode
from radar.domain.intents import SearchIntent
from radar.domain.results import Availability, Result
from radar.search.filters import MatchReport, Tier
from radar.search.status import open_status, reference_time

FRESHNESS_SCORE = {
    Freshness.LIVE: 1.0,
    Freshness.RECENT: 0.9,
    Freshness.TIMETABLE: 0.7,
    Freshness.CACHED_ESTIMATE: 0.55,
    Freshness.DIRECTORY: 0.6,
    Freshness.FIXTURE: 0.3,
}

WEIGHTS: dict[SortMode, dict[str, float]] = {
    SortMode.BEST: {
        "constraints": 3,
        "prefs": 1.5,
        "richness": 1.2,
        "category": 1.0,
        "open": 1.5,
        "price": 1,
        "distance": 1,
        "rating": 1,
        "duration": 1,
        "time_fit": 1,
        "freshness": 0.7,
        "flex": 0.4,
        "info": 0.6,
    },
    SortMode.CHEAPEST: {"price": 6, "constraints": 1, "info": 0.3},
    SortMode.RATED: {"rating": 6, "constraints": 1, "info": 0.3},
    SortMode.CLOSEST: {"distance": 6, "constraints": 1, "info": 0.3},
    SortMode.FASTEST: {"duration": 6, "constraints": 1, "time_fit": 0.5},
    SortMode.EARLIEST: {"earliest": 6, "constraints": 1},
    SortMode.FLEXIBLE: {"flex": 5, "constraints": 1, "price": 0.5},
    SortMode.VALUE: {"price": 3, "rating": 3, "constraints": 1, "distance": 0.5},
}

Reason = tuple[str, dict[str, Any]]


@dataclass
class Ranked:
    result: Result
    report: MatchReport
    score: float = 0.0
    reasons: list[Reason] = field(default_factory=list)
    converted: ConvertedMoney | None = None

    @property
    def tier(self) -> Tier:
        return self.report.tier


def _norm(value: float | None, lo: float, hi: float, invert: bool = False) -> float:
    if value is None:
        return 0.35  # unknown: below average, never best
    if hi <= lo:
        return 1.0
    x = (value - lo) / (hi - lo)
    x = max(0.0, min(1.0, x))
    return 1.0 - x if invert else x


def rating_strength(result: Result) -> float | None:
    """Bayesian-style shrinkage toward the middle of the scale by review count."""
    r = result.rating
    if r is None:
        return None
    n = r.count or 0
    prior, k = 0.7, 25
    return (r.normalized * n + prior * k) / (n + k)


def richness(r: Result) -> float:
    """How complete a listing is. Places with hours, a website, a phone and a photo are usually
    real, active businesses; a bare name pin often is not."""
    f = r.facts
    score = 0.0
    score += 0.3 if isinstance(f.get("opening_hours"), str) else 0.0
    score += 0.25 if any(lk.kind.value in ("website", "booking") for lk in r.links) else 0.0
    score += 0.15 if any(lk.kind.value == "phone" for lk in r.links) else 0.0
    score += 0.15 if f.get("photo") else 0.0
    score += 0.1 if r.address else 0.0
    score += 0.05 if f.get("cuisine") or f.get("stars") else 0.0
    return min(1.0, score)


def open_fit(r: Result, at: datetime) -> float:
    state, _ = open_status(r, at)
    return {"open": 1.0, "24_7": 1.0, "closed": 0.0}.get(state or "", 0.4)


def category_fit(r: Result, intent: SearchIntent) -> float:
    """Exact cuisine/type match beats a place that merely lists it among many."""
    wanted = [c.split(":", 1)[1] for c in intent.categories if c.startswith("cuisine:")]
    if not wanted:
        return 0.5
    have = [c.split(":", 1)[1] for c in r.categories if c.startswith("cuisine:")]
    if not have:
        return 0.2
    if have[0] in wanted:
        return 1.0
    return 0.6 if any(w in have for w in wanted) else 0.0


def _price_value(item: Ranked) -> float | None:
    if item.report.price_in_budget_ccy is not None:
        return float(item.report.price_in_budget_ccy.amount)
    if item.converted is not None:
        return float(item.converted.converted.amount)
    if item.result.price is not None:
        return float(item.result.price.amount.amount)
    return None


def _ts(dt: datetime | None) -> float | None:
    return dt.timestamp() if dt else None


def rank(items: list[Ranked], intent: SearchIntent, sort: SortMode | None = None) -> list[Ranked]:
    mode = sort or intent.sort
    w = WEIGHTS[mode]
    prices = [p for p in (_price_value(i) for i in items) if p is not None]
    dists = [i.result.distance_m for i in items if i.result.distance_m is not None]
    durs = [d for d in (i.result.duration_min for i in items) if d is not None]
    starts = [t for t in (_ts(i.result.starts_at) for i in items) if t is not None]
    p_lo, p_hi = (min(prices), max(prices)) if prices else (0.0, 0.0)
    d_lo, d_hi = (min(dists), max(dists)) if dists else (0.0, 0.0)
    u_lo, u_hi = (min(durs), max(durs)) if durs else (0.0, 0.0)
    s_lo, s_hi = (min(starts), max(starts)) if starts else (0.0, 0.0)
    target = _ts(intent.when.start) if intent.when and intent.when.start else None
    at = reference_time(intent)
    venue = intent.domain.is_venue

    for it in items:
        r, rep = it.result, it.report
        n_req = len(rep.met) + len(rep.unmet) + len(rep.unverified)
        comp: dict[str, float] = {
            "constraints": (len(rep.met) + 0.4 * len(rep.unverified)) / n_req if n_req else 1.0,
            "prefs": (len(rep.pref_met) + 0.3 * len(rep.pref_unknown))
            / max(1, len(rep.pref_met) + len(rep.pref_unmet) + len(rep.pref_unknown)),
            "price": _norm(_price_value(it), p_lo, p_hi, invert=True),
            "distance": _norm(r.distance_m, d_lo, d_hi, invert=True),
            "rating": rating_strength(r) or 0.3,
            "duration": _norm(r.duration_min, u_lo, u_hi, invert=True),
            "earliest": _norm(_ts(r.starts_at), s_lo, s_hi, invert=True),
            "freshness": FRESHNESS_SCORE.get(r.provenance.freshness, 0.5),
            "flex": 1.0
            if r.attr("free_cancellation") is Tri.YES or r.attr("refundable") is Tri.YES
            else 0.0
            if r.attr("refundable") is Tri.NO
            else 0.4,
            "info": 1.0 - min(1.0, len(rep.unverified) * 0.25 + (0.2 if r.price and r.price.unknown_fees else 0)),
            "time_fit": 0.5,
            "richness": richness(r),
            "category": category_fit(r, intent),
            "open": open_fit(r, at) if venue else 0.5,
        }
        if target is not None and r.starts_at is not None:
            comp["time_fit"] = max(0.0, 1.0 - abs(r.starts_at.timestamp() - target) / (4 * 3600))
        if r.availability is Availability.CONFIRMED:
            comp["freshness"] = min(1.0, comp["freshness"] + 0.2)
        it.score = round(sum(w.get(k, 0) * v for k, v in comp.items()), 4)
        it.reasons = explain(it, intent, comp, mode, p_lo)

    items.sort(key=lambda i: (i.tier, -i.score, i.result.title))
    return items


def explain(it: Ranked, intent: SearchIntent, comp: dict[str, float], mode: SortMode, p_lo: float) -> list[Reason]:
    r, rep = it.result, it.report
    reasons: list[Reason] = []
    met = [k for k in rep.met if k not in ("budget", "time_window")]
    if met:
        reasons.append(("result.met", {"items": met}))
    if rep.unverified:
        reasons.append(("result.unverified", {"items": rep.unverified}))
    if rep.unmet:
        reasons.append(("result.unmet", {"items": rep.unmet}))
    if "budget" in rep.met:
        reasons.append(("reason.within_budget", {}))
    pv = _price_value(it)
    if pv is not None and pv == p_lo:
        reasons.append(("reason.cheapest", {}))
    if r.rating and r.rating.count and r.rating.count >= 50 and r.rating.normalized >= 0.85:
        reasons.append(("reason.well_reviewed", {"count": r.rating.count, "source": r.rating.source}))
    if r.distance_m is not None and comp["distance"] >= 0.95:
        reasons.append(("reason.closest", {}))
    if r.legs and r.transfers == 0:
        reasons.append(("reason.direct", {}))
    if rep.pref_met:
        reasons.append(("reason.preferences", {"items": rep.pref_met}))
    return reasons
