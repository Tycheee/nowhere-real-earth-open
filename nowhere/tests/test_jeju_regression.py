# -*- coding: utf-8 -*-
"""济州岛端到端回归 (R2 验收项 1-7 的集成验证)。"""
from __future__ import annotations

import pytest

from nowhere import state as state_mod, terrain, walk as walk_mod
import nowhere.server as server_mod

JEJU_MID = (33.3803, 126.5483)
JEJU_SOUTH = (33.2454, 126.5483)


def _canned_env(lat, lon):
    return {
        "elevation": 200.0,
        "surface": terrain.surface(lat, lon),
        "weather": {"temp_c": 21.7, "feels_c": 21.0, "wind_ms": 2.0, "humidity": 60,
                    "precip": "none", "cloud": 10.0, "text": "晴", "source": "openmeteo"},
        "sky": {"phase": "day", "sun_alt": 30.0},
        "radio": None,
        "water_features": [],
    }


def _fresh_state(lat, lon):
    s = state_mod.WorldState()
    s.pos = (lat, lon)
    s.path = [{"lat": lat, "lon": lon, "elevation": 200.0, "dist_km": 0.0}]
    return s


def _mock_network(monkeypatch):
    """Cut all online calls so walk_impl runs fully offline in tests."""

    async def _none(*a, **k):
        return None

    async def _empty(*a, **k):
        return []

    import nowhere.hydrology as hydrology_mod
    import nowhere.water as water_mod
    monkeypatch.setattr(hydrology_mod, "nearby_water", _empty)
    monkeypatch.setattr(water_mod, "sea_surface_temp", _none)
    monkeypatch.setattr(water_mod, "marine_life", _none)


class TestWalkTowardSea:
    def test_step_toward_sea_from_mid_island(self):
        st = _fresh_state(*JEJU_MID)
        result = walk_mod.step(st, None, "toward_sea", 2.0)
        assert result["blocked"] is False
        assert st.pos is not None
        new_lat, _ = st.pos
        # 朝南走: 纬度下降
        assert new_lat < JEJU_MID[0]
        assert 135.0 <= (st.heading or 0) <= 225.0

    def test_step_into_sea_from_south_point(self):
        """从 33.2454 向南 5 km 会入海, 水面判定来自 landmask。"""
        st = _fresh_state(*JEJU_SOUTH)
        result = walk_mod.step(st, 180.0, None, 5.0)
        assert result["entered_water"] is True or st.mode == "water"


class TestServerWalkImpl:
    @pytest.mark.asyncio
    async def test_walk_impl_toward_sea_jeju(self, monkeypatch):
        """server.walk_impl('toward_sea') 在济州岛不再被 50km 拒绝。"""
        st = _fresh_state(*JEJU_MID)
        monkeypatch.setattr(server_mod, "_state", st)
        _mock_network(monkeypatch)

        async def _fake_env(lat, lon, dt):
            return _canned_env(lat, lon), False

        monkeypatch.setattr(server_mod, "_gather_env_cached", _fake_env)
        r = await server_mod.walk_impl(direction="toward_sea", distance_km=2.0)
        assert "rejected" not in (r.get("data") or {})
        assert r.get("text")
        data = r["data"]
        # R2: 输出结构区分地理事实与文学叙事
        assert "observed_environment" in data
        assert "narrative_event" in data
        obs = data["observed_environment"]
        assert obs["coast"]["nearest_ocean"]["km"] < 50

    @pytest.mark.asyncio
    async def test_walk_impl_toward_sea_rejected_far_inland(self, monkeypatch):
        """远离海岸(乌鲁木齐)时 toward_sea 拒绝, 且带来源标记。"""
        st = _fresh_state(43.8256, 87.6168)
        monkeypatch.setattr(server_mod, "_state", st)
        _mock_network(monkeypatch)

        async def _fake_env(lat, lon, dt):
            return _canned_env(lat, lon), False

        monkeypatch.setattr(server_mod, "_gather_env_cached", _fake_env)
        r = await server_mod.walk_impl(direction="toward_sea", distance_km=2.0)
        data = r.get("data") or {}
        assert data.get("rejected") == "toward_sea"
        assert data.get("coast_source") == "landmask"
        assert data.get("nearest_sea_km", 0) > 500


class TestWalkToSeogwipo:
    @pytest.mark.asyncio
    async def test_walk_to_seogwipo_zh(self, monkeypatch):
        """walk_to('西归浦市') 应识别目标而不是 not_found。"""
        st = _fresh_state(*JEJU_MID)
        monkeypatch.setattr(server_mod, "_state", st)
        # walk_to 距离 ~15km < 50km → 进入旅程叙事而不是 too_far/not_found
        r = await server_mod.walk_to_impl("西归浦市")
        err = (r.get("data") or {}).get("error")
        assert err != "not_found", r
        assert err != "ambiguous", r

    @pytest.mark.asyncio
    async def test_walk_to_seogwipo_en(self, monkeypatch):
        st = _fresh_state(*JEJU_MID)
        monkeypatch.setattr(server_mod, "_state", st)
        r = await server_mod.walk_to_impl("Seogwipo")
        err = (r.get("data") or {}).get("error")
        assert err != "not_found", r
