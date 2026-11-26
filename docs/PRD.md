# RADAR - Product Requirements

Find anything. Anywhere.

## 1. Problem

Finding a real-world service still means juggling a dozen sites: one for tables, one for trains, one for
hotels, a marketplace for flats, a separate app for price alerts. Each speaks its own filter language,
none of them understands "a romantic sushi place for two tomorrow at six under 30,000 dram", and none of
them watches the market for you across categories.

Chat assistants promise to fix this but tend to make things up: slots that don't exist, menus nobody
published, prices from last year. For decisions that cost money that is worse than no answer.

## 2. Product

RADAR is a Telegram-native concierge that:

1. understands a request in plain language (nine UI languages, mixed-language input),
2. turns it into a structured, validated search with hard requirements kept apart from wishes,
3. queries the real providers that cover that place and category,
4. shows ranked results with the source and age of every fact, and says plainly what it could not verify,
5. books or hands off to the official booking page, always behind an explicit confirmation,
6. keeps watching ("radars") and alerts on material changes,
7. assembles multi-part plans from real places and checks they actually fit together.

Everything is also reachable without typing, through buttons and the Mini App.

## 3. Users and first markets

- People in and travelling between Armenia, Georgia, Kazakhstan and the wider Caucasus and CIS region.
- Travellers and residents in the EU, UK and Switzerland.
- Expats searching for long-term rentals and services in a city they don't know yet.

No country is hard-coded. Any city resolves through geocoding; coverage gaps are reported honestly.

## 4. Principles (non-negotiable)

| Principle | How the product enforces it |
|---|---|
| Facts come from providers, never from a model | LLMs only produce a `SearchIntent`. They never see or produce inventory, prices, hours or availability. |
| Unknown stays unknown | Attributes are tri-state (yes / no / unknown). An unknown requirement can never make a result an exact match. |
| Opening hours are not availability | Directory results always show "availability not checked" and a note that hours are not table availability. |
| Cached fares are not offers | Freshness classes: live, recent, cached estimate, timetable, directory, fixture. Estimates are labelled in results and alerts. |
| Submitted is not confirmed | Booking states are explicit; "submitted" is reported as such until the provider confirms. |
| No silent relaxing | Results are grouped: exact, unconfirmed, alternatives that miss stated requirements (with what they miss). |
| No autonomous spending | Radars never book. Every booking needs a fresh revalidation and a one-time confirmation nonce. |
| Provider content is data | Listing descriptions and reviews are escaped, length-limited and never sent to a model. |

## 5. Scope by domain

| Domain | Discovery | Availability | Booking | Monitoring |
|---|---|---|---|---|
| Restaurants, cafes, bars | Live (OpenStreetMap) | Not available without partner API | Hand-off to venue site or phone | Not useful on directory data |
| Hotels and short stays | Via LiteAPI (key) | Live rates | Direct via LiteAPI (prebook + book) | Price / availability radars |
| Long-term real estate | Partner feeds only | Per feed | Hand-off | New listing, price change, stale detection |
| Flights | Duffel (live), Aviasales data (cached) | Live offers (Duffel) | Direct via Duffel orders | Price radars on cached fares |
| Trains | Transitous timetables | Not available (no public ticket API) | Hand-off to operator | Not offered |
| Buses, ferries, multimodal | Transitous + flight sources | Timetable only | Hand-off | Not offered |
| Events and activities | Venues (OSM), events (Ticketmaster key) | Sale status only | Hand-off | New events |
| Spa and appointments | Venues (OSM) | Needs partner API | Hand-off | Not useful on directory data |
| Shopping | Partner feeds | Per feed | Hand-off | Back-in-stock, price |
| Plans | Built from the above | Per item | Per item | Not yet |

Where a column says "partner API", the adapter contract and capability flags exist; integration starts
when an agreement is in place. See `PROVIDERS.md`.

## 6. Key flows

1. **Natural-language search.** Message, optional one-tap clarification, progress, grouped results,
   details card, actions.
2. **Structured search.** Domain, city, date, party size by buttons. Same pipeline.
3. **Refinement.** "cheaper", "actually Paris", "for three", "add a balcony", "without parking": the
   previous intent is edited, not reset.
4. **Watch.** From any result list. Price threshold, availability or new results. The schedule is set
   by the slowest permitted provider rate.
5. **Book.** Revalidate, show the exact total and conditions, confirm with a nonce, report the
   provider's real status.
6. **Plan.** Weekend, city trip, evening date, day trip and more. Real venues, validated timing,
   budget tally with unknown prices counted, weather flags.

## 7. Non-functional requirements

- p50 search under 5 s for directory and timetable sources, hard deadline 25 s with partial results.
- One provider failing never fails a search.
- Respect every provider's published rate and usage policy; cache where required (Nominatim).
- Privacy: minimal storage, location not persisted, `/forget` erases everything, audit log without
  message text.
- All money in Decimal with ISO currency codes; converted prices shown next to the original with the
  rate's source and date.
- Horizontal scaling: stateless API and bot workers, radar workers claim jobs with `SKIP LOCKED`.

## 8. Success metrics

- Share of searches with at least one exact match.
- Share of results where the user opened a source or booking link.
- Radar precision: alerts acted on / alerts sent.
- Clarification rate (lower is better) and refinement rate (higher means people trust iteration).
- Zero incidents of a fabricated fact reaching a user (tracked through reports and audits).

## 9. Out of scope for now

Payments held by RADAR, loyalty programmes, group chats, voice input, user reviews.
