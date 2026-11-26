# 5. No scraping of restricted marketplaces

Status: accepted

## Context
Regional real-estate and classifieds sites have no public APIs. Scraping them would breach their terms
and republish content we have no right to.

## Decision
Listings enter RADAR only through partner feeds (documented JSON format), user submissions, or the user
opening the marketplace itself. The registry lists these marketplaces as partner-only so coverage
messages are honest.

## Consequences
Real-estate coverage depends on partnerships. The adapter, monitoring (new listing, price change,
stale detection) and UI are ready for the first feed.
