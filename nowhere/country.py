"""离线国家码查询——cities15000 就近推断 + Natural Earth 国家网格兜底。

数据:
- nowhere/data/packs/cities15000.txt (gitignored 资源包, 可选): 4 万城市
  就近推断, 精度最高。
- nowhere/data/country_grid.npz (随仓库分发, 1/2° ISO-A2 栅格): 包缺失时
  的离线兜底, 保证 near 上下文与 geocode 缓存隔离在无资源包时仍可用。
两者都不可用则返回 None, 调用方走自己的降级路径, 不炸。
"""

from __future__ import annotations

import math
import pathlib
import threading

_PACK_PATH = pathlib.Path(__file__).resolve().parent / "data" / "packs" / "cities15000.txt"
_GRID_PATH = pathlib.Path(__file__).resolve().parent / "data" / "country_grid.npz"

_cities: list[tuple[float, float, str]] | None = None
_loaded = False

# ── country_grid fallback ────────────────────────────────────────────

_grid: object | None = None
_grid_lock = threading.Lock()


def _load_grid() -> dict | None:
    """Load the country grid once. Returns None when unavailable/corrupt."""
    global _grid
    with _grid_lock:
        if _grid is not None:
            return _grid or None
        _grid = {}
        try:
            if _GRID_PATH.exists():
                import numpy as np
                with np.load(_GRID_PATH, allow_pickle=True) as data:
                    grid = data["grid"]
                    codes = list(data["codes"])
                    res = float(data["res"])
                _grid = {"grid": grid, "codes": codes, "res": res}
        except Exception:
            _grid = {}
        return _grid or None


def _grid_lookup(lat: float, lon: float) -> str | None:
    """ISO-A2 from the country grid; None for ocean cells / no grid.

    离岸点只向外搜 1 格(2°), 再远返回 None — 宁可不给上下文,
    也不把大海对岸的国家当成当前国家。
    """
    g = _load_grid()
    if g is None:
        return None
    grid, codes, res = g["grid"], g["codes"], g["res"]
    n_lat, n_lon = grid.shape
    base_r = int((90.0 - lat) * res)
    base_c = int(((lon + 180.0) % 360.0) * res)
    for d in range(0, 2):  # 0 = 本格, 1 = 相邻 3×3
        for dr in (-d, 0, d):
            for dc in (-d, 0, d):
                r = base_r + dr
                c = base_c + dc
                if not (0 <= r < n_lat) or not (0 <= c < n_lon):
                    continue
                idx = int(grid[r, c])
                if 0 < idx < len(codes) and codes[idx]:
                    return codes[idx]
    return None


def _load() -> None:
    global _cities, _loaded
    if _loaded:
        return
    # 先在局部构建,成功后再原子赋值: 中途失败不会留下"已加载"的半成品缓存
    cities: list[tuple[float, float, str]] = []
    if _PACK_PATH.exists():
        try:
            with open(_PACK_PATH, encoding="utf-8") as f:
                for line in f:
                    parts = line.rstrip("\n").split("\t")
                    if len(parts) < 9:
                        continue
                    try:
                        cities.append((float(parts[4]), float(parts[5]), parts[8]))
                    except ValueError:
                        continue  # intentionally ignored: malformed coordinate in city data
        except (OSError, UnicodeDecodeError):
            # 包损坏与包缺失同口径降级(模块头约定: 不炸),不留半填充缓存
            cities = []
    _cities = cities
    _loaded = True


def country_code_of(lat: float, lon: float) -> str | None:
    """返回最近城市的 ISO 国家码(如 "VN"); 数据包缺失时用国家网格兜底。

    坐标非法(NaN/inf/越界)同样返回 None,与"包缺失"可区分开靠调用方自查;
    4 万城市线性扫,几毫秒,够用了。
    """
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    _load()
    if _cities:
        best_cc: str | None = None
        best_d = math.inf
        cos_lat = math.cos(math.radians(lat))
        for clat, clon, cc in _cities:
            dlat = clat - lat
            dlon = clon - lon
            # Wrap longitude at date line (e.g. 179 to -179 is 2 degrees, not 358)
            if dlon > 180:
                dlon -= 360
            elif dlon < -180:
                dlon += 360
            d = dlat ** 2 + (dlon * cos_lat) ** 2
            if d < best_d:
                best_d = d
                best_cc = cc
        return best_cc
    # R3: 资源包缺失 → Natural Earth 国家网格兜底 (offline, 随仓库分发)
    return _grid_lookup(lat, lon)
