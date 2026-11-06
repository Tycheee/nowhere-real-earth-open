# -*- coding: utf-8 -*-
"""R3/P0-3: landmask 分辨率事实唯一性 + 构建脚本默认可复现。"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import numpy as np
import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
DATA_DIR = REPO_ROOT / "nowhere" / "data"
BUILD_SCRIPT = REPO_ROOT / "tools" / "build_landmask.py"
NE_CACHE = REPO_ROOT / "tools" / ".cache" / "naturalearth"

EXPECTED_RES = 1.0 / 60.0
EXPECTED_CELLS_PER_DEG = 60.0
EXPECTED_SHAPE = (10800, 21600)  # (n_lat, n_lon)


class TestCommittedMaskResolution:
    def test_committed_mask_is_1_over_60(self):
        with np.load(DATA_DIR / "landmask.npz") as d:
            assert float(d["res"]) == EXPECTED_CELLS_PER_DEG  # npz 存每度格数
            assert (int(d["n_lat"]), int(d["n_lon"])) == EXPECTED_SHAPE
            for layer in ("ocean", "lakes", "land"):
                assert d[layer].shape == (EXPECTED_SHAPE[0], EXPECTED_SHAPE[1] // 8)

    def test_mask_info_reports_1_over_60(self):
        from nowhere import coast
        info = coast.mask_info()
        assert info["cells_per_deg"] == EXPECTED_CELLS_PER_DEG
        assert info["res_deg"] == pytest.approx(EXPECTED_RES)
        assert info["n_lat"] == EXPECTED_SHAPE[0]
        assert info["n_lon"] == EXPECTED_SHAPE[1]


class TestBuildScriptDefaults:
    def test_default_res_is_60(self):
        from tools.build_landmask import build_parser
        parser = build_parser()
        args = parser.parse_args([])
        assert args.res == 60.0, "--res 默认必须是 60(与提交的基础掩膜一致)"

    def test_default_output_name_is_baseline(self, tmp_path, monkeypatch):
        """无参数运行时输出应落在 landmask.npz, 而不是 landmask_hi.npz。"""
        from tools.build_landmask import build_parser
        parser = build_parser()
        args = parser.parse_args([])
        name = "landmask.npz" if args.res <= 60 else "landmask_hi.npz"
        assert name == "landmask.npz"

    def test_res_120_names_hi(self):
        from tools.build_landmask import build_parser
        parser = build_parser()
        args = parser.parse_args(["--res", "120"])
        name = "landmask.npz" if args.res <= 60 else "landmask_hi.npz"
        assert name == "landmask_hi.npz"


class TestRebuildReproducibility:
    @pytest.mark.slow
    def test_default_build_reproduces_committed_mask(self, tmp_path):
        """用默认参数重建, 应与已提交 landmask.npz 逐位一致。

        需要本地 Natural Earth 缓存(CI 无缓存时跳过, 不依赖外网)。
        """
        if not (NE_CACHE / "ne_10m_land.zip").exists() \
                or not (NE_CACHE / "ne_10m_lakes.zip").exists():
            pytest.skip("Natural Earth 缓存不在本地(离线 CI 环境)")
        pytest.importorskip("PIL")
        pytest.importorskip("shapefile")
        out = tmp_path / "rebuilt.npz"
        r = subprocess.run(
            [sys.executable, "-X", "utf8", str(BUILD_SCRIPT),
             "--out", str(out), "--cache", str(NE_CACHE)],
            capture_output=True, text=True, timeout=900,
        )
        assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
        with np.load(out) as a, np.load(DATA_DIR / "landmask.npz") as b:
            assert float(a["res"]) == pytest.approx(float(b["res"]))
            assert (int(a["n_lat"]), int(a["n_lon"])) == (int(b["n_lat"]), int(b["n_lon"]))
            for layer in ("ocean", "lakes", "land"):
                assert np.array_equal(a[layer], b[layer]), f"{layer} 层重建结果不一致"
