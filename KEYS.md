# Getting the API keys

Everything RADAR needs for restaurants, cafes, spas, museums, hotels (directory), trains, buses, ferries,
maps, photos, geocoding and currency works **with no key at all**. The four keys below are free and
unlock live prices and booking for flights and hotels, cached fare alerts, and ticketed events.

Put every key in `.env` (never commit it), restart, and run `radar providers` to see
`"configured": true`.

Checked against the official sites on 2026-11-26.

## 1. Duffel - flights (live offers and booking)

1. Sign up at **https://dashboard.duffel.com/join** (about a minute, free).
2. In the dashboard header open **More -> Developers -> Access tokens** and create a **test** token
   (read-write).
3. It starts with `duffel_test_`. Put it in `.env`:
   ```
   DUFFEL_ACCESS_TOKEN=duffel_test_...
   ```
- Test mode is free and safe: it returns the sandbox airline "Duffel Airways" (code ZZ), so prices and
  schedules are not real. RADAR labels these results as test data.
- Live mode (real airlines) requires completing Duffel's account verification in the dashboard.
- Limits are per endpoint and reported in `ratelimit-*` headers; live search is about 10 requests per minute.

## 2. LiteAPI (Nuitee Connect) - hotels (live rates and booking)

1. Create an account at **https://connect.nuitee.com** (no credit card needed).
2. In the sidebar open **Developer tools -> API Keys** and copy the **sandbox** key (starts with `sand_`).
   ```
   LITEAPI_KEY=sand_...
   ```
- The sandbox returns test inventory; RADAR labels it as sandbox data.
- A production key needs a credit card and a payout method on the account.

## 3. Ticketmaster Discovery - events with tickets

1. Register at **https://developer-account.ticketmaster.com/user/register**.
2. Registration creates a default app. Open your name (top right) -> **My Apps**, expand the app and copy the
   **Consumer Key**. That is the API key.
   ```
   TICKETMASTER_API_KEY=...
   ```
- Free: 5,000 calls per day, about 2 requests per second.
- Coverage: North America, UK, Ireland, Australia, New Zealand and parts of Europe. It does not cover
  Armenia, Georgia or Kazakhstan; there RADAR uses venues from OpenStreetMap.

## 4. Travelpayouts / Aviasales Data API - flight price alerts

1. Sign up at **https://www.travelpayouts.com** as an affiliate (free). The token is issued automatically.
2. Log in and open **Profile -> API token**.
   ```
   TRAVELPAYOUTS_TOKEN=...
   ```
- These are cached prices found by Aviasales users in the last 2-7 days, ideal for "tell me when it drops
  below X" radars. RADAR always labels them as estimates and links to Aviasales to buy.
- 600 requests per minute on the price endpoints RADAR uses.

## Free sources that need nothing

| What | Source |
|---|---|
| Restaurants, cafes, bars, spas, salons, museums, theatres, hotels, hostels | OpenStreetMap (Overpass) |
| Venue photos | Wikimedia Commons (via OSM tags) |
| Trains, buses, ferries with platforms and realtime | Transitous |
| City search, current location names | Nominatim, Photon, built-in gazetteer |
| Currency conversion | ECB, open.er-api.com |
| Weather for plans | Open-Meteo |
| Maps in the Mini App | OpenStreetMap tiles |

## What has no API anywhere

- **Rental listings in Armenia, Georgia and Kazakhstan** (List.am, Myhome.ge, SS.ge, Krisha.kz): no API,
  no feed. The sites forbid automated collection, so RADAR does not scrape them. If any of these sites
  ever publishes a feed, `RADAR_LISTING_FEEDS` takes it without code changes.
- **Restaurant table availability and rail ticket prices**: only through the operators' own booking
  systems. RADAR links to the venue or operator page for these.

## Commercial use

The free public tiers of Transitous, Open-Meteo and the public Overpass servers are for non-commercial
use. If RADAR makes money, set `RADAR_COMMERCIAL=true` and point the URLs at your own instances
(both are open source and self-hostable).
