"""High-precision land / ocean / lake classification (nowhere-real-earth).

Replaces the 1° grid as the *primary* land-sea oracle, with the original
grid kept strictly as a last-resort fallback.

Data
----
``nowhere/data/landmask.npz`` (committed, built by ``tools/build_landmask.py``
from Natural Earth 10m — public domain) holds two bit-packed rasters:

* ``ocean`` — cells covered by NE 10m ocean polygons (holes = islands)
* ``lakes`` — cells covered by NE 10m lakes polygons (Caspian, Great Lakes…)

Both rasters are cell-center classifications; the committed baseline
resolution is 1/60° (~1.85 km at the equator, 10800×21600 cells) and can be
raised to 1/120° with the build script (a file named ``landmask_hi.npz`` is
loaded automatically if present).  ``tools/build_landmask.py`` run without
arguments reproduces the committed baseline exactly.

Every public query returns a ``source`` and a ``confidence`` so callers and
tests can see whether the answer came from a high-res mask or the 1° grid
fallback:

* ``landmask_hi`` / ``landmask`` — Natural Earth 10m, confidence 1.0
* ``grid1`` — the original 1° grid, confidence 0.5
"""

from __future__ import annotations

import math
import pathlib
import threading
from typing import Final

import numpy as np

# ── constants ────────────────────────────────────────────────────────

_DATA_DIR: Final = pathlib.Path(__file__).resolve().parent / "data"
_LANDMASK_PATHS: Final[tuple[pathlib.Path, ...]] = (
    _DATA_DIR / "landmask_hi.npz",   # optional, built with --res 60 or 120
    _DATA_DIR / "landmask.npz",      # committed baseline (1/60°)
)

_SOURCE_HI: Final = "landmask_hi"
_SOURCE_MED: Final = "landmask"
_SOURCE_GRID: Final = "grid1"

_CACHE_LOCK = threading.Lock()

_MASK: dict | None = None  # {"ocean": ndarray, "lakes": ndarray, "res": float, "source": str}


def _bit_get(arr: np.ndarray, row: int, col: int) -> bool:
    """Read one bit from a bit-packed uint8 raster (row-major, LSB first)."""
    byte = int(arr[row, col >> 3])
    return bool((byte >> (col & 7)) & 1)


def _load_mask() -> dict | None:
    """Load the best available landmask. Returns None when no mask exists."""
    global _MASK
    with _CACHE_LOCK:
        if _MASK is not None:
            return _MASK
        for path in _LANDMASK_PATHS:
            if not path.exists():
                continue
            try:
                with np.load(path) as data:
                    ocean = data["ocean"]
                    lakes = data["lakes"] if "lakes" in data.files else None
                    res = float(data["res"])
                    nlat = int(data["n_lat"])
                    nlon = int(data["n_lon"])
                # np.load already validated the arrays; only publish when shapes
                # match the bit-packed geometry to avoid half-broken masks.
                expected_cols = (nlon + 7) // 8
                if ocean.shape != (nlat, expected_cols):
                    continue
                if lakes is not None and lakes.shape != (nlat, expected_cols):
                    lakes = None
                _MASK = {
                    "ocean": ocean,
                    "lakes": lakes,
                    "res": res,
                    "n_lat": nlat,
                    "n_lon": nlon,
                    "source": _SOURCE_HI if path.name.startswith("landmask_hi") else _SOURCE_MED,
                }
                return _MASK
            except Exception:
                continue
        _MASK = {}
        return _MASK


def mask_info() -> dict:
    """Describe the active landmask (for diagnostics / ``where_am_i``).

    ``cells_per_deg`` 是每度格数(1/60° 掩膜 = 60), 分辨率即 1/cells_per_deg 度。
    """
    m = _load_mask()
    if not m:
        return {"available": False, "source": _SOURCE_GRID}
    return {
        "available": True,
        "source": m["source"],
        "cells_per_deg": m["res"],
        "res_deg": 1.0 / m["res"],
        "n_lat": m["n_lat"],
        "n_lon": m["n_lon"],
    }


def _cell_of(mask: dict, lat: float, lon: float) -> tuple[int, int]:
    """Map lat/lon to (row, col) in the landmask."""
    row = int((90.0 - lat) * mask["res"])
    col = int(((lon + 180.0) % 360.0) * mask["res"])
    row = max(0, min(row, mask["n_lat"] - 1))
    col = max(0, min(col, mask["n_lon"] - 1))
    return row, col


def _grid_cover(lat: float, lon: float) -> str:
    """Cover string from the original 1° grid (fallback oracle).

    R3: uses ``terrain._surface_no_coast`` — the primitive that never
    consults ``coast``.  Calling the public ``terrain.surface`` here would
    re-enter :func:`classify` and recurse until ``RecursionError`` when the
    landmask is missing or corrupt.
    """
    from nowhere import terrain
    return terrain._surface_no_coast(lat, lon)


def classify(lat: float, lon: float) -> dict:
    """Classify one point.

    Returns
    ``{"water": "ocean" | "lake" | None, "is_land": bool,
      "source": str, "confidence": float}``

    ``water=None`` means land.  When no landmask is installed the answer
    comes from the 1° grid with ``confidence=0.5``.
    """
    m = _load_mask()
    if m:
        row, col = _cell_of(m, lat, lon)
        if _bit_get(m["ocean"], row, col):
            return {"water": "ocean", "is_land": False, "source": m["source"], "confidence": 1.0}
        if m["lakes"] is not None and _bit_get(m["lakes"], row, col):
            return {"water": "lake", "is_land": False, "source": m["source"], "confidence": 1.0}
        return {"water": None, "is_land": True, "source": m["source"], "confidence": 1.0}
    cover = _grid_cover(lat, lon)
    if cover == "water_ocean":
        return {"water": "ocean", "is_land": False, "source": _SOURCE_GRID, "confidence": 0.5}
    if cover == "water_fresh":
        return {"water": "lake", "is_land": False, "source": _SOURCE_GRID, "confidence": 0.5}
    return {"water": None, "is_land": True, "source": _SOURCE_GRID, "confidence": 0.5}


def is_ocean(lat: float, lon: float) -> bool:
    return classify(lat, lon)["water"] == "ocean"


def is_land(lat: float, lon: float) -> bool:
    return classify(lat, lon)["is_land"]


def is_water_any(lat: float, lon: float) -> bool:
    """Ocean or lake (freshwater).  Used by the walk water-blocking gate."""
    return classify(lat, lon)["water"] is not None


# ── scan helpers ──────────────────────────────────────────────────────

def _destinations(lat: float, lon: float, bearing_deg: float,
                  dists_km: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Vectorized destination points along one bearing."""
    from nowhere.terrain import _EARTH_RADIUS_KM
    lat_r = math.radians(lat)
    lon_r = math.radians(lon)
    brng_r = math.radians(bearing_deg)
    angular = dists_km / _EARTH_RADIUS_KM
    sin_lat = math.sin(lat_r)
    cos_lat = math.cos(lat_r)
    new_lat = np.arcsin(
        sin_lat * np.cos(angular) + cos_lat * np.sin(angular) * math.cos(brng_r)
    )
    new_lon = lon_r + np.arctan2(
        math.sin(brng_r) * np.sin(angular) * cos_lat,
        np.cos(angular) - sin_lat * np.sin(new_lat),
    )
    return np.degrees(new_lat), ((np.degrees(new_lon) + 180.0) % 360.0) - 180.0


def _lookup_vec(mask: dict, layer: str, lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    """Vectorized bitmask lookup → boolean array (bit-packed, LSB first)."""
    arr = mask[layer]
    if arr is None:
        return np.zeros(lats.shape, dtype=bool)
    rows = ((90.0 - lats) * mask["res"]).astype(np.int64)
    cols = (((lons + 180.0) % 360.0) * mask["res"]).astype(np.int64)
    rows = np.clip(rows, 0, mask["n_lat"] - 1)
    cols = np.clip(cols, 0, mask["n_lon"] - 1)
    flat = rows * mask["n_lon"] + cols
    # Per-point byte/bit extraction — O(n), no full-mask unpacking.
    flat_arr = arr.ravel()
    byte = flat_arr[flat >> 3].astype(np.uint8)
    return ((byte >> (flat & 7)) & 1).astype(bool)


def _lookup_ocean_vec(mask: dict, lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    """Vectorized ocean-mask lookup → boolean array."""
    return _lookup_vec(mask, "ocean", lats, lons)


def _lookup_lakes_vec(mask: dict, lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    return _lookup_vec(mask, "lakes", lats, lons)


def _grid_ocean_vec(lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    """Vectorized ocean check against the 1° grid fallback.

    Card 64 legacy gate: grid "water_ocean" cells above 1000 m are artifacts
    (Tibet/Andes), not real ocean.

    R3: uses ``terrain._surface_no_coast`` / ``_elevation_no_coast`` — the
    public functions would re-enter ``coast`` (recursion) and the public
    elevation's sea-level override would defeat the artifact gate.
    """
    from nowhere import terrain
    out = np.zeros(lats.shape, dtype=bool)
    for i in range(lats.shape[0]):
        lat_i, lon_i = float(lats[i]), float(lons[i])
        if terrain._surface_no_coast(lat_i, lon_i) == "water_ocean":
            if terrain._elevation_no_coast(lat_i, lon_i) > 1000:
                continue
            out[i] = True
    return out


def _grid_water_any_vec(lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    from nowhere import terrain
    out = np.zeros(lats.shape, dtype=bool)
    for i in range(lats.shape[0]):
        out[i] = terrain._surface_no_coast(
            float(lats[i]), float(lons[i])).startswith("water")
    return out


def scan_ahead(lat: float, lon: float, bearing_deg: float, max_km: float,
               step_km: float = 1.0, include_fresh: bool = False) -> dict | None:
    """First water hit along a bearing, sampled every *step_km*.

    Returns ``{"km": float, "water": "ocean"|"lake", "source": str,
    "confidence": float}`` or None.  ``include_fresh=True`` also matches
    freshwater cells from the grid fallback (small rivers/lakes the
    landmask can't see).
    """
    if max_km < step_km:
        return None
    dists = np.arange(step_km, max_km + 1e-9, step_km)
    lats, lons = _destinations(lat, lon, bearing_deg, dists)

    m = _load_mask()
    if m:
        ocean_hits = _lookup_ocean_vec(m, lats, lons)
        idx = int(np.argmax(ocean_hits)) if ocean_hits.any() else -1
        if idx >= 0:
            return {"km": float(dists[idx]), "water": "ocean",
                    "source": m["source"], "confidence": 1.0}
        lake_hits = _lookup_lakes_vec(m, lats, lons)
        idx = int(np.argmax(lake_hits)) if lake_hits.any() else -1
        if idx >= 0:
            return {"km": float(dists[idx]), "water": "lake",
                    "source": m["source"], "confidence": 1.0}
        if include_fresh:
            grid_hits = _grid_water_any_vec(lats, lons)
            idx = int(np.argmax(grid_hits)) if grid_hits.any() else -1
            if idx >= 0:
                return {"km": float(dists[idx]), "water": "lake",
                        "source": _SOURCE_GRID, "confidence": 0.5}
        return None

    if include_fresh:
        hits = _grid_water_any_vec(lats, lons)
    else:
        hits = _grid_ocean_vec(lats, lons)
    idx = int(np.argmax(hits)) if hits.any() else -1
    if idx < 0:
        return None
    return {"km": float(dists[idx]),
            "water": "ocean" if not include_fresh else "lake",
            "source": _SOURCE_GRID, "confidence": 0.5}


def nearest_ocean(lat: float, lon: float, max_km: float = 50.0) -> dict | None:
    """Nearest ocean over 8 compass bearings.

    Returns ``{"km", "bearing", "source", "confidence"}`` or None.
    """
    best: dict | None = None
    for i in range(8):
        bearing = i * 45.0
        hit = scan_ahead(lat, lon, bearing, max_km, step_km=1.0)
        if hit is not None and (best is None or hit["km"] < best["km"]):
            best = {"km": hit["km"], "bearing": bearing,
                    "source": hit["source"], "confidence": hit["confidence"]}
    return best


def wide_scan(lat: float, lon: float, max_km: float = 5000.0) -> dict | None:
    """Far-ocean scan with adaptive steps (fine near, coarse far).

    Avoids the original 50-km-step bug: near coasts the mask is probed
    every ~2 km up to 100 km, then 10 km up to 500 km, then 25 km to
    *max_km*.  With the landmask an island 3 km away can no longer be
    skipped and blamed on an ocean 1300 km away.

    Card 64 (grid fallback only): above 3000 m origin elevation, grid
    "ocean" hits closer than 500 km are artifacts and are ignored.
    """
    schedule = [(2.0, 100.0), (10.0, 500.0), (25.0, max_km)]
    from nowhere import terrain as _t
    origin_elev = _t.elevation(lat, lon)
    min_believable_km = 500.0 if origin_elev > 3000 else 0.0
    best: dict | None = None
    for i in range(8):
        bearing = i * 45.0
        for step, cap in schedule:
            if cap <= 0:
                continue
            hit = scan_ahead(lat, lon, bearing, min(cap, max_km), step_km=step)
            if hit is not None:
                if hit["source"] == _SOURCE_GRID and hit["km"] < min_believable_km:
                    break  # Card 64: high-altitude grid artifact
                if best is None or hit["km"] < best["km"]:
                    best = {"km": hit["km"], "bearing": bearing,
                            "source": hit["source"], "confidence": hit["confidence"]}
                break
    return best
