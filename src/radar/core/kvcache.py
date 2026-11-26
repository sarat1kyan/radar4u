"""Small persistent key-value cache (SQLite file) for slow, stable upstream data such as
OpenStreetMap directory queries and Wikidata lookups. Survives restarts; safe to delete."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any


class KVCache:
    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        default = Path(os.environ.get("RADAR_CACHE_DIR", Path.home() / ".cache" / "radar")) / "cache.sqlite3"
        self.path = Path(path) if path else default
        self._conn: sqlite3.Connection | None = None

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT, ts REAL)")
        return self._conn

    def _get(self, key: str, ttl_s: float) -> Any | None:
        row = self._db().execute("SELECT v, ts FROM kv WHERE k = ?", (key,)).fetchone()
        if row is None or time.time() - row[1] > ttl_s:
            return None
        return json.loads(row[0])

    def _set(self, key: str, value: Any) -> None:
        self._db().execute(
            "INSERT OR REPLACE INTO kv (k, v, ts) VALUES (?, ?, ?)",
            (key, json.dumps(value, ensure_ascii=False), time.time()),
        )

    async def get(self, key: str, ttl_s: float) -> Any | None:
        try:
            return await asyncio.to_thread(self._get, key, ttl_s)
        except (sqlite3.Error, OSError, ValueError):
            return None

    async def set(self, key: str, value: Any) -> None:
        with contextlib.suppress(sqlite3.Error, OSError, TypeError):
            await asyncio.to_thread(self._set, key, value)
