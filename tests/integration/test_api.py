import json
import time

import httpx
import pytest

from radar.api.app import create_app
from radar.api.auth import InitDataError, sign_init_data, validate_init_data
from radar.app import build_app
from radar.providers.registry import ProviderRegistry
from radar.storage.db import Database

TOKEN = "123456:TEST-TOKEN-not-real"


def init_data(uid=77, age=0, token=TOKEN):
    return sign_init_data(
        {
            "auth_date": str(int(time.time()) - age),
            "query_id": "q",
            "user": json.dumps({"id": uid, "first_name": "A", "language_code": "de"}),
        },
        token,
    )


def test_init_data_validation():
    u = validate_init_data(init_data(), TOKEN, 3600)
    assert u.id == 77 and u.language_code == "de"
    with pytest.raises(InitDataError):
        validate_init_data(init_data(token="999:other"), TOKEN, 3600)
    with pytest.raises(InitDataError):
        validate_init_data(init_data(age=7200), TOKEN, 3600)
    tampered = init_data().replace("%22id%22%3A+77", "%22id%22%3A+78")
    with pytest.raises(InitDataError):
        validate_init_data(tampered, TOKEN, 3600)


@pytest.fixture
async def client(settings, fixtures):
    from pydantic import SecretStr

    settings.bot_token = SecretStr(TOKEN)
    app = build_app(settings, registry=ProviderRegistry([fixtures], settings))
    app.db = Database("sqlite+aiosqlite:///:memory:")
    app.radars.db = app.engine.db = app.bookings.db = app.db
    await app.db.create_all()
    api = create_app(app)
    async with api.router.lifespan_context(api):
        transport = httpx.ASGITransport(app=api)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    await app.db.dispose()


async def test_auth_required(client):
    assert (await client.get("/api/v1/me")).status_code == 401
    assert (await client.get("/api/v1/me", headers={"Authorization": "tma bogus"})).status_code == 401


async def test_search_and_radar_roundtrip(client):
    h = {"Authorization": "tma " + init_data()}
    me = (await client.get("/api/v1/me", headers=h)).json()
    assert me["language"] == "de" and "hy" in me["languages"]
    r = await client.post(
        "/api/v1/search", headers=h, json={"text": "hotel in Berlin from December 21 to December 23 for two"}
    )
    body = r.json()
    assert r.status_code == 200 and body["counts"]["exact"] >= 1
    item = body["items"][0]
    assert item["provenance"]["freshness"] == "fixture" and item["price"]["currency"]
    assert body["headers"]["exact"].startswith(str(body["counts"]["exact"]))
    created = await client.post(
        "/api/v1/radars", headers=h, json={"session_id": body["session_id"], "kind": "price_below", "threshold": "300"}
    )
    assert created.status_code == 201
    rid = created.json()["id"]
    assert (await client.patch(f"/api/v1/radars/{rid}", headers=h, json={"state": "paused"})).json()[
        "state"
    ] == "paused"
    other = {"Authorization": "tma " + init_data(uid=88)}
    assert (await client.patch(f"/api/v1/radars/{rid}", headers=other, json={"state": "active"})).status_code == 404
    assert (await client.delete(f"/api/v1/radars/{rid}", headers=h)).status_code == 204


async def test_clarification_and_public_endpoints(client):
    h = {"Authorization": "tma " + init_data()}
    r = (await client.post("/api/v1/search", headers=h, json={"text": "hotel in Rome"})).json()
    assert r["missing"] == ["dates"] and r["question"]
    assert (await client.get("/healthz")).json()["status"] == "ok"
    caps = (await client.get("/api/v1/capabilities")).json()
    assert any(p["access"] == "partner" for p in caps["providers"])
    strings = (await client.get("/api/v1/i18n/hy")).json()
    assert strings["menu.search"] != "Search"
    page = await client.get("/app")
    assert page.status_code == 200 and "telegram-web-app.js" in page.text
    assert "frame-ancestors" in page.headers["content-security-policy"]
    assert "radar_searches_total" in (await client.get("/metrics")).text


async def test_webhook_rejects_without_secret(client):
    assert (await client.post("/telegram/webhook", json={})).status_code == 403


async def test_catalog_and_structured_draft_search(client):
    h = {"Authorization": "tma " + init_data()}
    cat = (await client.get("/api/v1/catalog", headers=h)).json()
    stay = next(d for d in cat["domains"] if d["id"] == "stay")
    assert stay["steps"][0] == "cat" and any(f["id"] == "wifi" for f in stay["filters"])
    berlin = (await client.get("/api/v1/places", params={"q": "Berlin"}, headers=h)).json()[0]
    assert berlin["timezone"] == "Europe/Berlin"
    body = {
        "draft": {
            "domain": "stay",
            "cat": "property.hotel",
            "city": berlin,
            "day": "2026-12-21",
            "nights": 2,
            "pax": 2,
            "flags": ["wifi", "not-a-flag"],
            "sort": "cheapest",
        }
    }
    r = (await client.post("/api/v1/search", headers=h, json=body)).json()
    assert r["counts"]["exact"] >= 1 and r["items"][0]["price"]
    bad = await client.post("/api/v1/search", headers=h, json={"draft": {"domain": "nope"}})
    assert bad.status_code == 422
