# Architecture

## Overview

```
 Telegram chat ──► aiogram Dispatcher ──┐
 Mini App (WebView) ──► FastAPI ────────┤
                                        ▼
                               Interpreter (nlu/)
                     rules parser  ──►  optional LLM backend
                                        │  SearchIntent (validated)
                                        ▼
                        GeoResolver (gazetteer, Nominatim, Photon)
                                        ▼
                         SearchPipeline (search/pipeline.py)
            ProviderRegistry ─► providers queried concurrently under a deadline
                                        ▼
                    dedupe ─► strict evaluation ─► ranking ─► outcome
                                        ▼
             render (bot) / serialize (API)      storage (PostgreSQL)
                                        ▲
     RadarEngine (monitoring/) ── scheduled runs of the same pipeline ── Notifier
     BookingService (booking/) ── revalidate ─ confirm(nonce) ─ provider book
     Planner (itinerary/) ── fills template slots with pipeline results ─ validate
```

`radar.app.build_app()` is the composition root. The bot, the API and the worker all get the same
services from it.

## Modules

| Path | Responsibility |
|---|---|
| `core/` | `Money` (Decimal + ISO code, floats rejected), `Provenance` and `Freshness`, tri-state `Tri`, errors, time helpers |
| `domain/` | `SearchIntent`, `Result`, the constraint vocabulary shared by parser, LLM schema, providers and filters |
| `geo/` | Gazetteer of launch-market cities in every script, Nominatim/Photon resolution with caching and 1 rps, IANA zone lookup |
| `nlu/` | Rule parser (9 languages), date parser, conversational refinement, LLM backends behind one contract |
| `providers/` | One adapter per source, the shared HTTP client (token bucket, circuit breaker, jittered retries), registry |
| `search/` | Pipeline, filters (met / unmet / unverified), ranking with reasons, dedupe, metrics |
| `monitoring/` | Radar service (creation rules, quotas), engine (scheduling, diffing, backoff), notifier |
| `booking/` | Booking state machine |
| `itinerary/` | Plan builder, validator, replace-and-shift |
| `storage/` | SQLAlchemy models, data access helpers; Alembic migrations in `/migrations` |
| `i18n/` | JSON catalogs, ICU-style plurals with CLDR rules, Babel formatting |
| `bot/` | aiogram handlers, keyboards, HTML rendering, worker loop |
| `api/` | FastAPI app, Telegram initData auth, JSON views |
| `webapp/` | Mini App (plain HTML/CSS/JS, no build step) |

## The intent contract

Everything downstream consumes `SearchIntent`:

- `domain`, `categories` (controlled tokens like `cuisine:sushi`, `event:museum`, `mode:ferry`)
- `location` / `origin` / `destination` as `LocationRef` (raw text plus resolved `Place` with canonical id,
  coordinates, country and IANA zone)
- `when` (tz-aware window) or `stay` (check-in/out), `arrive_by`
- `party`, `budget` (Money + unit: total, person, night, month)
- `requirements` and `preferences`, both lists of `Constraint` from the shared vocabulary
- `sort`, `monitor`, `plan_kind`, `missing`

Times are wall-clock at the place being searched. "Tomorrow at 18:00 in Yerevan" is 18:00 Asia/Yerevan
regardless of where the user or the server is.

## Language understanding

1. The rule parser always runs. It is deterministic, offline and covers the common shapes of requests in
   en, ru, hy, ka, kk, de, fr, it, es, including inflected city names.
2. If its confidence is low and an LLM backend is configured, the backend gets the user's text (and the
   previous intent for follow-ups) and must return `LLMIntent`, a flat schema with enums. The SDK's
   structured output validates it; then deterministic code converts it to `SearchIntent`, re-checking
   every value against the vocabulary and domain.
3. Any backend failure, refusal or invalid output falls back to the rule result.
4. The model has no tools, no network access through us, and never sees provider content.

Follow-ups go through `nlu/refine.py`, which parses the new message in the context of the previous domain
and merges only what changed. Negations ("without wifi") drop constraints; "cheaper" lowers the budget by
20% or switches to price sorting.

## Search pipeline

1. `ProviderRegistry.eligible(intent)` picks providers by domain, country and category coverage, skips
   unconfigured ones and those whose free terms forbid commercial use (when `RADAR_COMMERCIAL=true`),
   and never mixes demo fixtures with live sources. Skips carry a reason for the coverage message.
2. Each provider runs as its own task under a shared deadline. A progress callback fires per provider.
   Timeouts cancel the stragglers; their names are reported.
3. Results are de-duplicated across sources (same normalised name within 80 m, or same services and
   departure for journeys).
4. Prices are converted for display (ECB, then open.er-api) and, separately, into the budget currency for
   evaluation. Original prices are kept.
5. `filters.evaluate` turns every requirement, the time window, the budget and availability into
   met / unmet / unverified. Tier = exact, unverified or alternative.
6. `ranking.rank` sorts by tier first, then a weighted score whose weights depend on the sort mode. Ratings
   are shrunk toward the middle by review count so 5.0 from two reviews does not beat 4.6 from 900.
   Reasons are structured (`i18n key + params`), not percentages.

## Monitoring

- `RadarService.create` checks the threshold, finds providers that allow scheduled queries, sets the
  interval to the slowest of our floor and every provider's minimum (scaled by cost), enforces the per-user
  quota and sets expiry to the watched date or 30 days.
- `RadarEngine.tick` claims due radars (`FOR UPDATE SKIP LOCKED` on PostgreSQL), runs the pipeline in
  monitoring mode restricted to the radar's providers, diffs against stored observations and writes
  notifications with a dedupe key. Failures back off exponentially with full jitter; five in a row move the
  radar to `error` (still retried, slower).
- Only exact matches alert. New-result radars treat the first run as a baseline. Price alerts include the
  previous value when we stored one.
- `Notifier.deliver` waits out quiet hours, sends up to three alerts separately and groups the rest, and
  sends digest users one message at 09:00 local.

## Booking

States: `draft -> awaiting_confirmation -> submitted -> confirmed | failed`, plus `cancelled` and
`handoff`. `start()` revalidates with the provider and issues a nonce valid for 10 minutes; a price change
is shown before the user can confirm. `confirm()` checks owner, state, nonce and expiry, collects the
traveller fields the provider needs, submits with an idempotency key, and records the provider's status.
A double tap returns the existing booking.

## Data model

`users`, `search_sessions` (last few intents and serialised results for button callbacks), `radars`,
`radar_observations` (price history capped at 30 points), `notifications` (unique dedupe key),
`bookings` (event log in JSON), `itineraries`, `audit_log` (actions only, no message text). Money columns
are decimal strings plus currency.

## Security

- Telegram Mini App requests carry `initData`; the API verifies the HMAC with the bot token and rejects
  data older than `RADAR_WEBAPP_AUTH_MAX_AGE`. Duplicate keys and oversized payloads are rejected.
- Webhook mode requires Telegram's secret token header, compared in constant time.
- All provider text is HTML-escaped before it reaches Telegram, inserted with `textContent` in the Mini App,
  and served under a CSP that only allows our origin and telegram.org.
- httpx request logging is turned down so tokens in URLs never land in logs.
- Per-user rate limits in the bot and the API; per-provider token buckets and circuit breakers outbound.
- The container runs as an unprivileged user.

## Scaling notes

- API and bot are stateless apart from FSM state, which moves to Redis when `RADAR_REDIS_URL` is set.
- Radar workers scale horizontally on PostgreSQL thanks to `SKIP LOCKED`.
- Provider rate limits are per process today; a shared Redis token bucket is on the roadmap before
  running many workers against strict providers (Nominatim's 1 rps is global).
