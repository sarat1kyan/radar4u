# Roadmap

## Now (done in this iteration)

- Domain model, intent contract, constraint vocabulary, tri-state facts, freshness classes.
- Button-first search panel, rule parser for 9 languages, date parsing, refinement.
- Gazetteer for launch markets + Nominatim/Photon resolution.
- Adapters: OSM venues, Transitous journeys, FX, weather, Duffel, Aviasales data, LiteAPI,
  Ticketmaster, partner feeds, demo fixtures.
- Pipeline with partial results, dedupe, strict evaluation, ranking with reasons.
- Radars with scheduling, backoff, dedupe, quiet hours, digests, quotas, expiry.
- Booking state machine, itinerary planner.
- Telegram bot with rich-message UI, API, redesigned Mini App (map, photos, detail sheets), 9 UI languages.
- Docker, compose, CI with PostgreSQL.

## Next (0.2)

1. Run Duffel, LiteAPI, Ticketmaster and Aviasales adapters live with real keys; record sanitised
   payloads as contract fixtures.
2. Shared rate limiter in Redis so several workers respect global limits (Nominatim 1 rps).
3. Room-level amenities for stays (hotel details call) so bed type and bathroom can be verified.
4. Itinerary editing in the Mini App (replace slot, lock item) on top of `planner.replace`.
5. Multi-passenger flight booking form; passenger profiles stored encrypted with explicit consent.
6. Price history charts for radars where provider terms allow storing history.
7. Self-hosted Overpass and MOTIS deployment guide for commercial operation.

## Later

- Restaurant reservations once a partner agreement exists (OpenTable / TheFork / regional systems).
- Real-estate partner feeds for Armenia, Georgia, Kazakhstan.
- Rail ticketing through a distribution partner.
- GTFS-based routing for walking/transit time between itinerary items (replacing the estimate).
- Evaluation dataset for ranking with offline regression scoring.
- More UI languages (uk, az, uz, ky, pl, cs, el, pt, nl) - catalogs only, no code changes.
