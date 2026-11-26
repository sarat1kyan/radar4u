# Progress

Last updated: 2026-11-26

## Done and verified

| Area | Verification |
|---|---|
| Core model (Money, provenance, tri-state, intent, vocabulary) | unit tests |
| Rule parser, 9 languages, dates, refinement | unit tests + 30-case regression dataset (`tests/eval`) |
| Geo resolution (gazetteer, Nominatim, Photon, IANA zones) | unit tests + live test |
| HTTP client (token bucket, breaker, jittered retries) | unit tests |
| OSM venues via Overpass, opening hours evaluation | contract tests + live run (Yerevan, Tbilisi) |
| Transitous rail/bus/ferry with station resolution | contract tests + live runs (Frankfurt-Berlin, Munich-Vienna) |
| FX (ECB + open.er-api) | unit tests + live test |
| Duffel, Aviasales data, LiteAPI, Ticketmaster, partner feeds | contract tests only (no keys available) |
| Pipeline: concurrency, deadline, partial results, dedupe, filters, ranking | unit tests + live runs |
| Radars: scheduling, baseline, alerts, dedupe, quiet hours, backoff, error state, expiry, quota | integration tests on SQLite and PostgreSQL 16 |
| Booking: revalidation, nonce, expiry, price change, idempotency, ownership, hand-off | integration tests (demo provider) |
| Itineraries: overlaps, tight connections, closed venues, weather, budget, replace-and-shift | unit tests |
| Telegram bot flows | end-to-end tests through the real dispatcher with a fake Telegram session; live start against @radar4ubot (polling, commands registered in 9 languages) |
| API + initData auth + Mini App | integration tests; Mini App rendered in headless Chromium against a running API |
| Migrations | `alembic check` clean on SQLite and PostgreSQL |
| Docker image | built and run: health check, Mini App, migrations, non-root user |
| Static checks | ruff, mypy --strict clean |

## Known limitations

- **Restaurant and appointment availability**: no public reservation API exists for the target markets.
  RADAR shows verified venue data, opening hours (clearly not as availability) and hands off to the
  venue's website or phone.
- **Rail and bus prices**: Transitous gives timetables only. Ticket prices need a distribution partner.
- **Multimodal trips** combine flight sources and ground timetables side by side; RADAR does not yet stitch
  a flight and a ferry into one priced itinerary.
- **Hotels**: room-level amenities (bed type, bathroom) are not in LiteAPI's rates response, so those
  requirements stay "not confirmed" until the hotel details call is added.
- **Public Overpass mirrors** are slow and sometimes unavailable; failures are reported as partial
  results. Production should run its own Overpass instance (required anyway for commercial use).
- **Travel time between plan items** is a distance-based estimate (walking/city transit) and labelled
  as such.
- **Rate limits** are enforced per process. Before running several workers, move the token buckets to
  Redis (Nominatim's 1 request per second is global).
- **Translations** were produced for all 9 languages; Armenian, Georgian and Kazakh strings with
  placeholders would benefit from a native speaker's review.
- **Real estate and shopping** have no live source until a partner feed is configured.

## Pending integrations

| Provider | What is needed |
|---|---|
| Duffel | test token to record live payloads, then account verification for live mode |
| LiteAPI | sandbox key, then production key |
| Ticketmaster | free key |
| Aviasales Data API | free partner token |
| OpenTable / TheFork / regional reservation systems | partner agreement |
| Booking.com Demand / Expedia Rapid | affiliate or partner agreement |
| Regional real-estate marketplaces | data feed agreement |
| Rail distribution (Trainline Partner Solutions, Rail Europe) | partner agreement |

## Next steps

See [docs/ROADMAP.md](docs/ROADMAP.md). Immediate: run the keyed adapters live, shared Redis rate
limiter, hotel room details, itinerary editing in the Mini App.
