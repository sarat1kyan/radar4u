# 7. Mini App without a build step

Status: accepted

## Context
The Mini App is a thin view over the API. A JS toolchain would add dependencies and supply-chain
surface for little benefit at this stage.

## Decision
Plain HTML, CSS and JavaScript served by FastAPI, Telegram theme variables for styling, `textContent`
for all provider strings, strict CSP. UI strings come from the same JSON catalogs via `/api/v1/i18n`.

## Consequences
Zero front-end dependencies. If the UI grows (maps, rich itinerary editing), revisit with a framework.
