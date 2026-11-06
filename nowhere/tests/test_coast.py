# -*- coding: utf-8 -*-
"""高精度海陆/海岸判断回归测试 (R2, 济州岛验收项 1/2)。

Jeju coordinates used in the original field test:
- mid-island start: 33.3803, 126.5483
- south point (near Seogwipo / south coast): 33.2454, 126.5483
"""
from __future__ import annotations

import numpy as np
import pytest

from nowhere import coast, terrain
from nowhere.walk import (
    _pick_semantic_bearing,
    nearest_ocean,
    nearest_ocean_km_and_bearing,
    water_ahead,
    water_ahead_km,
)
import nowhere.server as server_mod

JEJU_MID = (33.3803, 126.5483)
JEJU_SOUTH = (33.2454, 126.5483)


class TestJejuOcean:
    def test_south_point_ocean_within_reasonable_distance(self):
        """验收 1: 33.2454,126.5483 在合理距离内识别到海洋, 绝不能是 1300 km。"""
        hit = nearest_ocean(*JEJU_SOUTH, max_km=50.0)
        assert hit is not None
        assert hit["km"] is not None and hit["km"] < 30.0, hit
        assert hit["km"] != 1300.0
        assert hit["source"] == "landmask"  # 高精度源, 不是 1° 网格兜底
        assert hit["confidence"] >= 1.0

    def test_wide_coast_scan_no_1300(self):
        """_wide_coast_scan 不能跳过近岸命中 1300 km 外的水。"""
        hit = server_mod._wide_coast_scan(*JEJU_SOUTH)
        assert hit is not None
        assert hit["km"] < 50.0, hit
        assert hit["source"] == "landmask"

    def test_toward_sea_bearing_points_south(self):
        """验收 2: 济州岛中部向南 toward_sea, 方位应大体指向南部海岸。"""
        bearing, _ = _pick_semantic_bearing(*JEJU_MID, "toward_sea", 2.0)
        # south half-plane: 135°..225°
        assert 135.0 <= bearing <= 225.0, bearing

    def test_legacy_api_compatible(self):
        km, brg = nearest_ocean_km_and_bearing(*JEJU_SOUTH, max_km=50.0)
        assert km is not None and km < 30.0
        assert brg is not None

    def test_water_ahead_rich_reports_source(self):
        hit = water_ahead(*JEJU_MID, 180.0, 30.0)
        assert hit is not None
        assert set(hit) >= {"km", "water", "source", "confidence"}
        assert hit["water"] == "ocean"

    def test_walk_into_water_blocked_gate(self):
        """水域阻挡闸门: 从南端往南走 5 km 会踏进海。"""
        lat2, lon2 = terrain.destination(*JEJU_SOUTH, 180.0, 5.0)
        assert terrain.is_water(lat2, lon2)


class TestIslandClassification:
    @pytest.mark.parametrize("name,lat,lon", [
        ("Jeju", 33.3803, 126.5483),
        ("Hawaii big island", 19.6, -155.5),
        ("Honolulu", 21.31, -157.86),
        ("Singapore", 1.35, 103.82),
        ("Taiwan", 23.7, 121.0),
        ("Malta", 35.9, 14.4),
        ("Hainan", 19.2, 109.7),
    ])
    def test_islands_are_land(self, name, lat, lon):
        c = coast.classify(lat, lon)
        assert c["is_land"], f"{name} should be land, got {c}"
        assert c["source"] == "landmask"

    def test_open_sea_is_ocean(self):
        c = coast.classify(33.1, 126.8)
        assert c["water"] == "ocean"

    def test_lake_classified_fresh(self):
        c = coast.classify(46.43, 6.55)  # Lake Geneva
        assert c["water"] == "lake"

    def test_surface_override_on_jeju_coast(self):
        """terrain.surface 在近海应被 landmask 覆盖为 water_ocean。"""
        assert terrain.surface(33.1, 126.8) == "water_ocean"
        assert terrain.surface(*JEJU_MID) != "water_ocean"
        assert terrain.elevation(33.1, 126.8) == 0.0


class TestFallback:
    def test_grid_fallback_when_no_mask(self, monkeypatch, reset_coast):
        """掩膜缺失时降级到 1° 网格, 且来源标记为 grid1。"""
        monkeypatch.setattr(coast, "_LANDMASK_PATHS", ())
        c = coast.classify(*JEJU_MID)
        assert c["source"] == "grid1"
        assert c["confidence"] < 1.0
        # 功能仍可用: nearest_ocean 不抛异常
        hit = nearest_ocean(*JEJU_SOUTH, max_km=50.0)
        assert hit is None or hit["source"] == "grid1"

    def test_mask_info(self):
        info = coast.mask_info()
        assert info["available"] is True
        assert info["source"] == "landmask"
        # R3: 提交的基础掩膜是 1/60°(每度 60 格), 10800×21600, 不是"分辨率>0"即可
        assert info["cells_per_deg"] == 60.0
        assert info["res_deg"] == pytest.approx(1.0 / 60.0)
        assert info["n_lat"] == 10800
        assert info["n_lon"] == 21600


class TestNoRecursionWhenMaskMissing:
    """R3/P0-1: 掩膜缺失或损坏时, coast↔terrain 不得互相递归。"""

    def test_surface_no_recursion_no_mask(self, monkeypatch, reset_coast):
        monkeypatch.setattr(coast, "_LANDMASK_PATHS", ())
        calls: list[int] = []
        orig = coast.classify

        def counting(lat, lon):
            calls.append(1)
            return orig(lat, lon)

        monkeypatch.setattr(coast, "classify", counting)
        s = terrain.surface(*JEJU_MID)
        assert isinstance(s, str)
        # 每次 surface() 只允许进入一次 classify —— 递归实现会是数百次
        assert len(calls) == 1, f"coast.classify called {len(calls)} times"

    def test_elevation_no_recursion_no_mask(self, monkeypatch, reset_coast):
        monkeypatch.setattr(coast, "_LANDMASK_PATHS", ())
        e = terrain.elevation(*JEJU_MID)
        assert isinstance(e, (int, float))

    def test_corrupt_mask_falls_back_to_grid(self, monkeypatch, reset_coast, tmp_path):
        bad = tmp_path / "landmask.npz"
        bad.write_bytes(b"this is not a numpy archive")
        monkeypatch.setattr(coast, "_LANDMASK_PATHS", (bad,))
        c = coast.classify(*JEJU_MID)
        assert c["source"] == "grid1"
        assert c["confidence"] < 1.0

    def test_scan_ahead_no_mask_completes(self, monkeypatch, reset_coast):
        monkeypatch.setattr(coast, "_LANDMASK_PATHS", ())
        hit = coast.scan_ahead(*JEJU_MID, 180.0, 20.0)
        assert hit is None or hit["source"] == "grid1"

    def test_wide_scan_no_mask_completes(self, monkeypatch, reset_coast):
        monkeypatch.setattr(coast, "_LANDMASK_PATHS", ())
        hit = coast.wide_scan(*JEJU_SOUTH, max_km=100.0)
        assert hit is None or hit["source"] == "grid1"

    def test_artifact_gate_uses_no_coast_elevation(self, monkeypatch, reset_coast):
        """Card 64 闸门用 no-coast 海拔 primitive — 海拔>1000 的假海洋格
        在无掩膜时仍被过滤, 不会被海洋格海拔=0 的公共接口骗过。"""
        import numpy as np
        monkeypatch.setattr(coast, "_LANDMASK_PATHS", ())

        def _fake_surface(lat, lon, *a):
            return "water_ocean"

        def _fake_elev(lat, lon, *a):
            return 2500.0 if lat > 30.0 else 0.0

        monkeypatch.setattr(terrain, "_surface_no_coast", _fake_surface)
        monkeypatch.setattr(terrain, "_elevation_no_coast", _fake_elev)
        # 高原假海洋格 → 闸门过滤
        assert not coast._grid_ocean_vec(np.array([31.0]), np.array([88.0]))[0]
        # 海平面真海洋格 → 保留
        assert coast._grid_ocean_vec(np.array([10.0]), np.array([88.0]))[0]


class TestNoGridArtifacts:
    def test_high_plateau_not_ocean(self):
        """青藏高原内部不能因为网格伪影被判成海。"""
        c = coast.classify(31.5, 88.0)
        assert c["is_land"], c

    def test_water_ahead_fresh_include(self):
        """include_fresh=True 时日内瓦湖也应命中(淡水)。"""
        # 日内瓦湖北岸 (46.47, 6.58), 向南 ~3-5 km 到湖面
        hit = water_ahead(46.47, 6.58, 180.0, 12.0, include_fresh=True)
        assert hit is not None
        assert hit["water"] in ("lake", "ocean")
