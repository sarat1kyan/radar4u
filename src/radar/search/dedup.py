"""Cross-provider de-duplication.

Two venue results are the same place when their normalised names match and
they are within 80 m. Two transport results are the same journey when they
have the same service numbers and departure. The surviving record is the one
with the strongest freshness; links are merged so no source disappears.
"""

from __future__ import annotations

import re
import unicodedata

from radar.domain.results import Result
from radar.search.ranking import FRESHNESS_SCORE


def _norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.casefold())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\[demo\]\s*", "", s)
    return re.sub(r"[^\w]+", "", s)


def _key(r: Result) -> str | None:
    if r.legs:
        services = "+".join((leg.service or leg.mode).replace(" ", "").upper() for leg in r.legs)
        start = r.starts_at.isoformat() if r.starts_at else ""
        return f"j:{services}:{start}"
    return None


def dedupe(results: list[Result]) -> list[Result]:
    out: list[Result] = []
    by_key: dict[str, int] = {}
    for r in results:
        k = _key(r)
        if k is not None:
            if k in by_key:
                _merge(out, by_key[k], r)
                continue
            by_key[k] = len(out)
            out.append(r)
            continue
        name = _norm_name(r.title)
        dup = None
        if r.point is not None:
            for i, o in enumerate(out):
                if o.point is not None and _norm_name(o.title) == name and o.point.distance_m(r.point) < 80:
                    dup = i
                    break
        if dup is None:
            out.append(r)
        else:
            _merge(out, dup, r)
    return out


def _merge(out: list[Result], idx: int, new: Result) -> None:
    old = out[idx]
    keep, other = (
        (new, old)
        if FRESHNESS_SCORE[new.provenance.freshness] > FRESHNESS_SCORE[old.provenance.freshness]
        else (old, new)
    )
    urls = {lk.url for lk in keep.links}
    keep.links += [lk for lk in other.links if lk.url not in urls]
    for k, v in other.attributes.items():
        if keep.attributes.get(k) is None or keep.attributes[k].value == "unknown":
            keep.attributes[k] = v
    keep.facts.setdefault("also_from", []).append(other.provenance.provider)
    out[idx] = keep
