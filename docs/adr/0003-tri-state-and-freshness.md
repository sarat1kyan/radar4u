# 3. Tri-state attributes and freshness classes

Status: accepted

## Context
Providers rarely state every amenity. Treating missing data as "no" hides good options; treating it as
"yes" misleads people. Similar ambiguity exists between a timetable, a cached fare and a bookable offer.

## Decision
- Every boolean-like attribute is `Tri` (yes / no / unknown).
- Requirements evaluate to met / unmet / unverified. Exact matches need every requirement met.
- Every result carries `Provenance` with a `Freshness`: live, recent, cached estimate, timetable,
  directory, fixture. Rendering is driven by it ("timetable only", "estimate", "demo data").

## Consequences
Result lists have three groups and say what is unconfirmed. Ranking can never promote an unverified
result above an exact one.
