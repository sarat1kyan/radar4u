# Provider capability matrix

Checked against official documentation on 2026-11-26. "Verified here" means the adapter was run against
the real endpoint from this repository during development; "contract" means it is implemented against
the documented schema and covered by tests with representative payloads, but no credentials were
available to run it live.

## Integrated

| Provider | Domains | Access | Gives | Does not give | Booking | Radars | Status |
|---|---|---|---|---|---|---|---|
| OpenStreetMap via Overpass | dining, wellness, event venues | Public, no key | names, coordinates, address, cuisine, opening hours, website, phone, amenity tags | ratings, prices, menus, availability | hand-off | no (directory data) | verified here |
| Transitous (MOTIS 2) | rail, bus, coach, ferry | Public, no key, **non-commercial** | timetabled journeys, platforms, realtime where feeds have it | prices, seats | hand-off to operator | no | verified here |
| Nominatim / Photon | geocoding | Public, 1 rps, caching required | places worldwide | - | - | - | verified here |
| ECB reference rates | FX | Public | ~30 currencies, working days | AMD, GEL, KZT | - | - | verified here |
| open.er-api.com | FX | Public, attribution required, no redistribution | AMD, GEL, KZT and others, daily | - | - | - | verified here |
| Open-Meteo | weather for plans | Public, **non-commercial** | daily forecast | - | - | - | contract |
| Duffel | flights | Self-serve, test mode free; live needs verification | live offers, conditions, bags, revalidation, orders | - | direct | no (look-to-book cost) | contract |
| Aviasales Data API (Travelpayouts) | flights | Free partner token | cached fares from the last ~48 h, deep link | live availability | hand-off | yes, every 3 h | contract |
| LiteAPI (Nuitee) | hotels | Self-serve, sandbox key free | live rates, taxes, refundability, board, prebook, book | room-level amenities in the rates call | direct | yes, every 6 h | contract |
| Ticketmaster Discovery v2 | events | Free key, 5000/day | events, venues, price ranges, sale status | seat availability | hand-off | yes, every 6 h | contract |
| Partner listing feeds | real estate, shopping | Per agreement | whatever the feed publishes | - | hand-off | yes | contract (generic format) |
| Demo fixtures | stay, flight, real estate, shopping | Dev only | labelled demo data | anything real | demo | demo | for development |

Notes:

- Non-commercial free tiers (Transitous, Open-Meteo, public Overpass instances, OSRM demo) are skipped
  automatically when `RADAR_COMMERCIAL=true`, unless the URL points at a self-hosted instance and the
  matching `*_SELF_HOSTED` flag is set.
- Overpass: the main instance asks heavy and commercial users to self-host. RADAR tries the mirror that
  answered last first, trips a per-mirror breaker after two failures and caches responses for an hour.
- Nominatim forbids autocomplete; RADAR only geocodes submitted text and caches every answer.
- Transitous asks for an identifying User-Agent and coordination above ~720 plan requests a day.
- Duffel test-mode results are labelled as test data, never as live offers.
- LiteAPI sandbox results are labelled as sandbox data.

## Researched, not integrated (and why)

| Provider | Reason |
|---|---|
| Amadeus Self-Service | Decommissioned; keys disabled and portal closed 17 July 2026. Only the contract-based Enterprise APIs remain. |
| Booking.com Demand API | Managed affiliate partners only; key issued by an account manager. |
| Expedia Rapid | Partner programme; production keys after a site review. |
| OpenTable, TheFork, SevenRooms, Resy | Partner or POS-integration programmes; no public reservation API. |
| List.am, Myhome.ge, SS.ge, Krisha.kz | No public API or developer programme. RADAR does not scrape them. |
| Deutsche Bahn API Marketplace | Timetables only (no prices); Transitous already covers DB timetables. |
| Trainline, Rail Europe, SNCF Connect | B2B partner programmes; no public price or booking API. |
| OSRM public demo | Non-commercial, best effort; RADAR estimates in-city travel time instead and labels it an estimate. |

## Onboarding a credentialed provider

1. Get the key (links in the table above), add it to `.env` (never to the repository).
2. Restart. `radar providers` prints the matrix with `configured: true`.
3. Run `radar search "..."` for a request in that domain and check the results' freshness label.
4. For booking providers, start in sandbox/test mode. The booking flow labels sandbox data and the demo
   banner stays on until a live key is used.
5. Only then switch to live keys.

## Adding a new provider

1. Implement `Provider.search()` mapping the payload into `Result` with an honest `Freshness`,
   tri-state attributes and `Provenance`. Never infer an amenity that the payload doesn't state.
2. Declare `capabilities` (domain, categories, countries, booking, revalidation, whether scheduled
   queries are permitted and how often, relative cost) and `commercial_use_allowed`.
3. Use `ProviderHttp` with the provider's published rate.
4. Add contract tests with a representative payload in `tests/fixtures`.
5. Register it in `providers/registry.py`.
