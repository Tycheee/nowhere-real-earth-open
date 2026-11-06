# -*- coding: utf-8 -*-
"""Build the offline land/ocean/lake landmask from Natural Earth 10m data.

Usage
-----
.. code-block:: bash

    python tools/build_landmask.py                # 1/60° -> nowhere/data/landmask.npz (reproduces the committed baseline)
    python tools/build_landmask.py --res 120      # 1/120° (~0.93 km) -> landmask_hi.npz (optional, auto-preferred)

What it does
------------
1. Downloads ``ne_10m_land.zip`` and ``ne_10m_lakes.zip`` (public domain,
   https://www.naturalearthdata.com) from the official CDN into a cache dir.
2. Rasterizes land polygons and lake polygons onto a global lon/lat grid
   (cell-center sampling) using Pillow's C polygon fill.
3. Derives the ocean mask as the complement of (land | lakes).
4. Saves a bit-packed ``.npz`` (~1.4 MB at 1/60°) consumed by
   ``nowhere/coast.py``.  ``--res 120`` builds are saved as
   ``landmask_hi.npz`` and are picked up automatically.

Build-time only dependencies: pillow, pyshp.  Runtime needs numpy only.

Licenses: Natural Earth = public domain; derived mask inherits no
restrictions.  See nowhere/data/landmask_README.md for provenance.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import zipfile
import urllib.request

import numpy as np

# pillow/pyshp 是构建期可选依赖: 延迟到使用函数内导入,
# 便于测试直接 import 本模块检查参数默认值而无需安装它们。

_CDN = "https://naciscdn.org/naturalearth/10m/physical"
_FILES = {
    "land": "ne_10m_land.zip",
    "lakes": "ne_10m_lakes.zip",
}
_DEFAULT_CACHE = pathlib.Path(__file__).resolve().parent / ".cache" / "naturalearth"

# Natural Earth 10m bounds: lon [-180, 180], lat [-90, 90]


def _download(name: str, cache: pathlib.Path) -> pathlib.Path:
    cache.mkdir(parents=True, exist_ok=True)
    zip_path = cache / _FILES[name]
    if not zip_path.exists():
        url = f"{_CDN}/{_FILES[name]}"
        print(f"[landmask] downloading {url} …")
        urllib.request.urlretrieve(url, zip_path)
        print(f"[landmask] saved {zip_path}")
    return zip_path


def _read_polygons(zip_path: pathlib.Path) -> list[dict]:
    """Return polygons from a Natural Earth shapefile.

    Each entry is ``{"outer": ring, "holes": [ring, ...]}`` where a ring is
    a list of ``(lon, lat)`` tuples.  MultiPolygon parts become separate
    entries.  Ring role is decided by orientation (shapefile convention:
    outer rings clockwise = negative signed area in y-up coords) and holes
    are assigned to the outer ring that contains them.
    """
    out: list[dict] = []
    with zipfile.ZipFile(zip_path) as zf:
        import io
        import shapefile  # pyshp, build-time only
        reader = shapefile.Reader(
            shp=io.BytesIO(zf.read(zip_path.stem + ".shp")),
            shx=io.BytesIO(zf.read(zip_path.stem + ".shx")),
            dbf=io.BytesIO(zf.read(zip_path.stem + ".dbf")),
        )
        for sr in reader.shapeRecords():
            shape = sr.shape
            parts = list(shape.parts) + [len(shape.points)]
            rings = []
            for i in range(len(parts) - 1):
                ring = [(float(p[0]), float(p[1])) for p in shape.points[parts[i]:parts[i + 1]]]
                if ring and ring[0] == ring[-1]:
                    ring = ring[:-1]
                if len(ring) >= 3:
                    rings.append(ring)
            if not rings:
                continue

            def _signed_area(ring):
                s = 0.0
                for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
                    s += x1 * y2 - x2 * y1
                return s / 2.0

            def _contains(outer, inner):
                # even-odd ray cast; inner must be non-empty
                x, y = inner[0]
                inside = False
                for (x1, y1), (x2, y2) in zip(outer, outer[1:] + outer[:1]):
                    if (y1 > y) != (y2 > y):
                        x_cross = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
                        if x_cross > x:
                            inside = not inside
                return inside

            outers = [r for r in rings if _signed_area(r) < 0]   # clockwise
            holes = [r for r in rings if _signed_area(r) >= 0]    # counter-clockwise
            for outer in outers:
                owned_holes = [h for h in holes if _contains(outer, h)]
                out.append({"outer": outer, "holes": owned_holes})
    return out


def _build_layer(polygons: list[dict], res: float, n_lon: int, n_lat: int,
                 label: str) -> np.ndarray:
    """Rasterize polygons to a boolean array [n_lat, n_lon] (row 0 = 90N)."""
    from PIL import Image, ImageDraw  # build-time only
    img = Image.new("1", (n_lon, n_lat), 0)
    draw = ImageDraw.Draw(img)
    scale_x = n_lon / 360.0
    scale_y = n_lat / 180.0
    for poly in polygons:
        outer_pts = [((lon + 180.0) * scale_x, (90.0 - lat) * scale_y)
                     for lon, lat in poly["outer"]]
        draw.polygon(outer_pts, fill=1)
        for hole in poly["holes"]:
            hole_pts = [((lon + 180.0) * scale_x, (90.0 - lat) * scale_y)
                        for lon, lat in hole]
            draw.polygon(hole_pts, fill=0)  # holes erase
    arr = np.array(img, dtype=bool)  # copy: PIL buffer is read-only
    filled = int(arr.sum())
    print(f"[landmask] {label}: {filled} cells (~{filled * res * res * 12321 / 1e6:.1f}M km²)")
    return arr


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--res", type=float, default=60.0,
                        help="cells per degree; default 60 = ~1.85 km (committed landmask.npz). "
                             "120 = ~0.93 km (landmask_hi.npz)")
    parser.add_argument("--out", type=str, default=None,
                        help="output .npz path (default: landmask.npz at --res 60, landmask_hi.npz above)")
    parser.add_argument("--cache", type=str, default=str(_DEFAULT_CACHE),
                        help="download cache directory")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    res = float(args.res)
    if res < 1 or res > 600:
        print("[landmask] --res must be between 1 and 600", file=sys.stderr)
        sys.exit(2)
    n_lat = int(round(180.0 * res))
    n_lon = int(round(360.0 * res))

    root = pathlib.Path(__file__).resolve().parent.parent
    if args.out:
        out_path = pathlib.Path(args.out)
    else:
        # R3: 默认 1/60° 精确重现提交的基础掩膜; 更高分辨率才是 landmask_hi。
        name = "landmask.npz" if res <= 60 else "landmask_hi.npz"
        out_path = root / "nowhere" / "data" / name

    land_polys = _read_polygons(_download("land", pathlib.Path(args.cache)))
    lakes_polys = _read_polygons(_download("lakes", pathlib.Path(args.cache)))
    print(f"[landmask] {len(land_polys)} land features, {len(lakes_polys)} lake features")

    land = _build_layer(land_polys, res, n_lon, n_lat, "land")
    lakes = _build_layer(lakes_polys, res, n_lon, n_lat, "lakes")
    # NE 10m land does not carve lakes out of its polygons — lakes override land.
    land &= ~lakes
    ocean = ~land & ~lakes

    def _pack(arr: np.ndarray) -> np.ndarray:
        return np.packbits(arr, axis=1, bitorder="little")

    np.savez_compressed(
        out_path,
        ocean=_pack(ocean),
        lakes=_pack(lakes),
        land=_pack(land),
        res=np.array(res),
        n_lat=np.array(n_lat),
        n_lon=np.array(n_lon),
        version=np.array("nowhere-real-earth-landmask-v1"),
        source=np.array("Natural Earth 10m land/lakes, public domain"),
    )
    size_mb = out_path.stat().st_size / 1e6
    print(f"[landmask] wrote {out_path} ({size_mb:.1f} MB, {n_lat}x{n_lon} @ 1/{res:g} deg)")


if __name__ == "__main__":
    main()
