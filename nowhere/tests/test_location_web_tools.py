from __future__ import annotations

import httpx
from starlette.applications import Starlette

from nowhere import live_location
from nowhere.location_web import location_routes
import nowhere.server as server


def _fresh_store(monkeypatch, tmp_path):
    monkeypatch.setenv("NOWHERE_HOME", str(tmp_path))
    live_location.reset_store_for_tests()
    return live_location.get_store()


async def test_mobile_api_requires_capability_and_stops_cleanly(monkeypatch, tmp_path):
    store = _fresh_store(monkeypatch, tmp_path)
    session, token = store.create_session(label="济州岛", precision="approximate")
    app = Starlette(routes=location_routes())

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        page = await client.get(f"/share/{session['id']}#token=never-sent")
        assert page.status_code == 200
        assert "never-sent" not in page.text
        assert page.headers["referrer-policy"] == "no-referrer"

        assert (await client.get(f"/api/location/share/{session['id']}/status")).status_code == 401
        headers = {"Authorization": f"Bearer {token}"}
        status = await client.get(f"/api/location/share/{session['id']}/status", headers=headers)
        assert status.status_code == 200
        assert status.json()["session"]["label"] == "济州岛"

        uploaded = await client.post(
            f"/api/location/share/{session['id']}/positions",
            headers=headers,
            json={"latitude": 33.2528, "longitude": 126.561, "accuracy_m": 8},
        )
        assert uploaded.status_code == 202
        assert store.latest_location(session["id"])["latitude"] == 33.25

        stopped = await client.post(f"/api/location/share/{session['id']}/stop", headers=headers)
        assert stopped.status_code == 200
        rejected = await client.post(
            f"/api/location/share/{session['id']}/positions",
            headers=headers,
            json={"latitude": 33, "longitude": 126},
        )
        assert rejected.status_code == 410


def test_start_tool_puts_token_only_in_fragment(monkeypatch, tmp_path):
    _fresh_store(monkeypatch, tmp_path)
    monkeypatch.setattr(server, "_web_url", "https://travel.example")
    result = server.start_location_share_impl(duration_minutes=10)
    assert "error" not in result["data"]
    url = result["data"]["share_url"]
    assert url.startswith("https://travel.example/share/")
    assert "#token=" in url
    assert "?token=" not in url


def test_non_https_public_share_is_rejected(monkeypatch, tmp_path):
    _fresh_store(monkeypatch, tmp_path)
    monkeypatch.setenv("NOWHERE_SHARE_PUBLIC_URL", "http://travel.example")
    monkeypatch.setattr(server, "_web_url", None)
    result = server.start_location_share_impl()
    assert result["data"]["error"] == "https_required"


async def test_all_original_tools_remain_and_location_tools_are_registered():
    names = {tool.name for tool in await server.mcp.list_tools()}
    assert len(names) == 36
    assert {"open_door", "walk", "where_am_i"} <= names
    assert {
        "start_location_share",
        "get_live_location",
        "location_timeline",
        "stop_location_share",
        "nearby_places",
        "route_from_shared_location",
        "location_privacy_status",
        "join_shared_location",
    } <= names

