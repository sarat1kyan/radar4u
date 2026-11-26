# RADAR

Find anything. Anywhere.

RADAR is a Telegram bot and Mini App that searches real providers for restaurants, stays, flights,
trains, buses and ferries, events, appointments, rentals and products, then keeps watching for you.
You describe what you need in your own words (or tap through a few buttons); RADAR works out the
requirements, asks only for what is missing, queries the sources that cover that place, and shows
ranked results with where every fact came from.

It is strict about facts:

- Every price, time and attribute comes from a provider and carries its source and age.
- Missing information stays "not confirmed"; it never becomes a yes.
- Opening hours are not shown as table availability, timetables not as tickets, cached fares not as
  offers, and a submitted booking is not called confirmed.
- Language models (optional) only turn text into a search request. They never produce or see listings.

## What works today

| Area | Source | State |
|---|---|---|
| Restaurants, cafes, bars, spas, salons, museums, theatres, venues | OpenStreetMap via Overpass | live, no key |
| Trains, coaches, buses, ferries (timetables) | Transitous | live, no key, non-commercial |
| Geocoding any city, aliases in 9 languages | built-in gazetteer + Nominatim / Photon | live |
| Currency conversion | ECB + open.er-api.com | live |
| Flights (live offers, booking) | Duffel | needs token |
| Flight price alerts (cached fares) | Aviasales Data API | needs token |
| Hotels (live rates, booking) | LiteAPI | needs key |
| Events with tickets | Ticketmaster Discovery | needs key |
| Long-term rentals, shopping | partner feeds | needs a feed |

See [docs/PROVIDERS.md](docs/PROVIDERS.md) for the full capability matrix, what each source does and does
not provide, and which marketplaces need a commercial agreement first.

UI languages: English, Russian, Armenian, Georgian, Kazakh, German, French, Italian, Spanish.
Adding one is dropping a JSON file into `src/radar/i18n/locales/`.

## Examples

```
sushi restaurant for two in Yerevan tomorrow at 18:00, romantic, max 30,000 AMD
apartment in Berlin for two adults from December 21 to 27, queen-size bed, free wifi, max EUR 600 total
train from Frankfurt to Berlin arriving before 14:00 tomorrow, prefer direct
cheapest way from Yerevan to Sicily next month, flights, trains, ferries and buses
spa near Wiesbaden on Saturday for two
long-term apartment in Tbilisi, two bedrooms, balcony, pets ok, up to USD 800 a month
notify me when a direct flight Yerevan to Rome drops below EUR 100
plan a romantic weekend for two in Paris for EUR 700, excluding transport
```

Follow-ups edit the current search: "cheaper", "actually Paris", "for three", "add a balcony",
"without parking", "at 20:00 instead".

## Quick start

Requirements: Python 3.12 or 3.13.

```bash
git clone https://github.com/sarat1kyan/radar4u.git
cd radar4u
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # put your bot token in BOT_API
radar bot                     # long polling + radar worker, SQLite by default
```

Try the pipeline without Telegram:

```bash
radar search "train from Munich to Vienna tomorrow at 9:00, direct"
radar search "vegan cafe in Tbilisi"
radar providers               # capability matrix as JSON
```

### With Docker

```bash
cp .env.example .env          # fill BOT_API, optionally RADAR_WEBAPP_URL and provider keys
docker compose up --build
```

This starts PostgreSQL, Redis, runs migrations, then the bot and the API (Mini App at
`http://localhost:8080/app`, API docs at `/api/docs`, metrics at `/metrics`).

### Mini App

Serve the API over HTTPS, set `RADAR_WEBAPP_URL=https://your-host/app`, and register the URL with
@BotFather. The bot then shows an "Open app" button and sets the chat menu button. The API authenticates
every request with Telegram `initData`.

### Webhook instead of polling

```bash
radar api --webhook-url https://your-host
```

Telegram's secret-token header is generated per start and checked on every update. Run `radar worker`
separately for radars in this mode.

## Configuration

Everything is environment variables; see [.env.example](.env.example). The important ones:

| Variable | Meaning |
|---|---|
| `BOT_API` | Telegram bot token |
| `RADAR_DATABASE_URL` | `postgresql+asyncpg://...` in production, SQLite by default |
| `RADAR_REDIS_URL` | optional, keeps conversation state across restarts and instances |
| `RADAR_WEBAPP_URL` | public HTTPS URL of the Mini App |
| `RADAR_COMMERCIAL` | `true` if the deployment earns money; skips non-commercial free tiers |
| `RADAR_NLU_BACKEND` | `rules` (default), `anthropic` or `openai` |
| `DUFFEL_ACCESS_TOKEN`, `LITEAPI_KEY`, `TICKETMASTER_API_KEY`, `TRAVELPAYOUTS_TOKEN` | optional providers |
| `RADAR_ENABLE_FIXTURES` | labelled demo data for domains without a live provider (never in prod) |

## Development

```bash
ruff check src tests && ruff format --check src tests
mypy                                   # strict
pytest                                 # unit + integration on SQLite
RADAR_TEST_DATABASE_URL=postgresql+asyncpg://radar:radar@localhost/radar_test pytest tests/integration
pytest -m live tests/live              # hits real public endpoints
alembic revision --autogenerate -m "..." && alembic upgrade head
```

The parser has a regression dataset in `tests/eval/parser_cases.json`; add a case for every parser fix.

## Documentation

- [Product requirements](docs/PRD.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Providers and onboarding](docs/PROVIDERS.md)
- [Decisions (ADRs)](docs/adr)
- [Roadmap](docs/ROADMAP.md)
- [Progress and known limitations](PROGRESS.md)
- [Security](SECURITY.md)

## Data and attribution

Map data (c) OpenStreetMap contributors, ODbL. Journey data via Transitous
([sources](https://transitous.org/sources/)). Weather data by Open-Meteo.com (CC BY 4.0). Exchange rates
from the European Central Bank and "Rates By Exchange Rate API". Event data by Ticketmaster where
configured.

## License

MIT, see [LICENSE](LICENSE).
