# -*- coding: utf-8 -*-
"""R3/P0-2 支撑: 离线国家码网格兜底 (pack 缺失时 near_cc 仍可用)。"""
from __future__ import annotations

import pytest

from nowhere import country


class TestCountryGridFallback:
    """本机无 cities15000 资源包 → 全部走 Natural Earth 国家网格兜底。"""

    @pytest.mark.parametrize("name,lat,lon,expected", [
        ("New York", 40.0, -75.0, "US"),
        ("Beijing", 30.0, 115.0, "CN"),
        ("Jeju", 33.38, 126.55, "KR"),
        ("Seoul", 37.57, 126.98, "KR"),
        ("Paris", 48.86, 2.35, "FR"),
        ("Berlin", 52.52, 13.40, "DE"),
        ("Sydney", -33.87, 151.21, "AU"),
    ])
    def test_major_cities(self, name, lat, lon, expected):
        assert country.country_code_of(lat, lon) == expected, name

    def test_far_offshore_returns_none(self):
        # 南太平洋远海: 2° 邻域内无陆地 → 不给上下文而不是猜错国家
        assert country.country_code_of(-30.0, -100.0) is None

    def test_invalid_coords_none(self):
        assert country.country_code_of(float("nan"), 0.0) is None
        assert country.country_code_of(95.0, 0.0) is None

    def test_corrupt_grid_degrades_to_none(self, monkeypatch, tmp_path):
        bad = tmp_path / "country_grid.npz"
        bad.write_bytes(b"garbage")
        monkeypatch.setattr(country, "_GRID_PATH", bad)
        monkeypatch.setattr(country, "_grid", None)
        assert country.country_code_of(40.0, -75.0) is None
