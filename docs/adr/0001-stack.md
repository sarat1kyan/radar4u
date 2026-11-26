# 1. Python, aiogram, FastAPI, SQLAlchemy

Status: accepted (2026-11-26)

## Context
The product is I/O bound (many slow third-party calls), runs inside Telegram, and needs a typed domain
model that both a chat bot and an HTTP API can share.

## Decision
- Python 3.12+ with asyncio end to end.
- aiogram 3 for the bot (async, typed, FSM, supports current Bot API).
- FastAPI for the Mini App API, webhook and health endpoints.
- Pydantic v2 for every domain object that crosses a boundary.
- SQLAlchemy 2 async with Alembic; PostgreSQL in production, SQLite for tests and local runs.
- httpx for outbound HTTP behind one shared client wrapper.

## Consequences
One language and one model layer for bot, API and worker. The test suite runs without external services
and again on PostgreSQL in CI.
