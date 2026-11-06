from __future__ import annotations

import sqlite3

import pytest

from nowhere import amap, live_location, providers


@pytest.fixture
def clock():
    return [1_700_000_000.0]


@pytest.fixture
def store(tmp_path, clock):
    return live_location.LocationStore(tmp_path / "location.sqlite3", clock=lambda: clock[0])


def test_token_is_hashed_and_approximate_coordinates_are_rounded(store):
    session, token = store.create_session(precision="approximate")
    raw = store.path.read_bytes()
    assert token.encode() not in raw

    location = store.add_location(
        session["id"],
        token,
        {
            "latitude": 33.252812,
            "longitude": 126.561234,
            "accuracy_meters": 7.5,
        },
    )
    assert location["latitude"] == 33.25
    assert location["longitude"] == 126.56
    assert location["accuracy_meters"] == 7.5

    with sqlite3.connect(store.path) as db:
        digest = db.execute("SELECT write_token_hash FROM share_sessions").fetchone()[0]
    assert len(digest) == 64
    assert digest != token


def test_wrong_token_stop_expiry_and_retention(store, clock):
    session, token = store.create_session(duration_minutes=5, retention_hours=1)
    with pytest.raises(live_location.LocationError, match="凭证") as caught:
        store.add_location(session["id"], "wrong", {"latitude": 33, "longitude": 126})
    assert caught.value.code == "invalid_write_token"

    store.add_location(session["id"], token, {"latitude": 33, "longitude": 126})
    result = store.stop_session(session["id"])
    assert result["session"]["stopped_at"] == clock[0]
    with pytest.raises(live_location.LocationError) as stopped:
        store.add_location(session["id"], token, {"latitude": 33, "longitude": 126})
    assert stopped.value.code == "session_stopped"

    clock[0] += 7200
    purged = store.purge()
    assert purged == {"locations": 1, "sessions": 1}
    assert store.get_session(session["id"]) is None


def test_expired_session_rejects_new_positions(store, clock):
    session, token = store.create_session(duration_minutes=5)
    clock[0] += 301
    with pytest.raises(live_location.LocationError) as caught:
        store.add_location(session["id"], token, {"latitude": 33, "longitude": 126})
    assert caught.value.code == "session_expired"


def test_timeline_is_newest_first_and_can_be_erased(store, clock):
    session, token = store.create_session()
    store.add_location(session["id"], token, {"latitude": 33.1, "longitude": 126.1})
    clock[0] += 5
    store.add_location(session["id"], token, {"latitude": 33.2, "longitude": 126.2})
    points = store.timeline(session["id"])
    assert [point["latitude"] for point in points] == [33.2, 33.1]
    result = store.stop_session(session["id"], erase_history=True)
    assert result["erased_location_count"] == 2
    assert store.latest_location(session["id"]) is None


def test_jeju_stays_wgs84_but_mainland_is_transformed():
    assert amap.wgs84_to_gcj02(33.2528, 126.561) == (33.2528, 126.561)
    transformed = amap.wgs84_to_gcj02(39.9042, 116.4074)
    assert transformed != (39.9042, 116.4074)
    assert amap.OVERSEAS_WARNING in amap.AmapClient.warning(33.2528, 126.561)


@pytest.mark.asyncio
async def test_amap_key_stays_in_params_and_jeju_is_not_shifted(monkeypatch):
    captured = {}

    async def fake_fetch(url, **kwargs):
        captured["url"] = url
        captured["params"] = kwargs["params"]
        return {
            "status": "1",
            "regeocode": {
                "formatted_address": "Jeju-do",
                "addressComponent": {"country": "대한민국", "city": []},
            },
        }

    monkeypatch.setattr(providers, "fetch_json", fake_fetch)
    result = await amap.AmapClient("secret-key").reverse_geocode(33.2528, 126.561)
    assert "secret-key" not in captured["url"]
    assert captured["params"]["key"] == "secret-key"
    assert captured["params"]["location"] == "126.561,33.2528"
    assert result["coverage_warning"] == amap.OVERSEAS_WARNING


@pytest.mark.asyncio
async def test_amap_geocoded_destination_is_not_transformed_twice(monkeypatch):
    requests = []

    async def fake_fetch(url, **kwargs):
        requests.append((url, kwargs["params"]))
        if url.endswith("/v3/geocode/geo"):
            return {"status": "1", "geocodes": [{"location": "116.413,39.912"}]}
        return {
            "status": "1",
            "route": {"paths": [{"distance": "1200", "duration": "900", "steps": []}]},
        }

    monkeypatch.setattr(providers, "fetch_json", fake_fetch)
    result = await amap.AmapClient("key").route_to_address(
        (39.9042, 116.4074), "天安门", "walking"
    )
    route_params = requests[-1][1]
    assert route_params["destination"] == "116.413,39.912"
    assert result["distance_meters"] == 1200

