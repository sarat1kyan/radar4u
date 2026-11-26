"""Telegram Mini App authentication.

The Mini App sends Telegram.WebApp.initData in the Authorization header as
"tma <initData>". Validation per core.telegram.org/bots/webapps:
data_check_string = all fields except hash, sorted, "key=value" joined by \\n;
secret = HMAC_SHA256(key="WebAppData", msg=bot_token);
valid iff hex(HMAC_SHA256(key=secret, msg=data_check_string)) == hash.
auth_date older than the configured max age is rejected (replay window).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode


class InitDataError(ValueError):
    pass


@dataclass(frozen=True)
class WebAppUser:
    id: int
    language_code: str | None
    first_name: str | None
    raw: dict[str, Any]


def sign_init_data(fields: dict[str, str], bot_token: str) -> str:
    """Build a signed initData string (used by tests and local development tools)."""
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": digest})


def validate_init_data(init_data: str, bot_token: str, max_age_s: int, now: float | None = None) -> WebAppUser:
    if not init_data or len(init_data) > 4096:
        raise InitDataError("missing or oversized init data")
    pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=False)
    data = dict(pairs)
    if len(data) != len(pairs):
        raise InitDataError("duplicate keys")
    received = data.pop("hash", None)
    if not received:
        raise InitDataError("no hash")
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        raise InitDataError("bad signature")
    try:
        auth_date = int(data.get("auth_date", "0"))
    except ValueError as exc:
        raise InitDataError("bad auth_date") from exc
    if (now or time.time()) - auth_date > max_age_s:
        raise InitDataError("init data expired")
    try:
        user = json.loads(data.get("user", "{}"))
        uid = int(user["id"])
    except (ValueError, KeyError, TypeError) as exc:
        raise InitDataError("no user") from exc
    return WebAppUser(id=uid, language_code=user.get("language_code"), first_name=user.get("first_name"), raw=user)
