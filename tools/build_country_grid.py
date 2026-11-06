# -*- coding: utf-8 -*-
"""Build the offline country-code grid from Natural Earth 10m admin_0 data.

    python tools/build_country_grid.py          # -> nowhere/data/country_grid.npz

Produces a coarse (1/2°, ~130 KB) ISO-A2 country-code raster consumed by
``nowhere/country.country_code_of`` as the **offline fallback** when the
optional GeoNames cities15000 pack is absent.  Cell centers only: points
offshore have code 0 (unknown) and callers degrade gracefully.

Build-time only dependencies: pillow, pyshp.  Runtime needs numpy only.
Licenses: Natural Earth = public domain; derived grid inherits no restrictions.
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import urllib.request
import zipfile

import numpy as np

_CDN = "https://naciscdn.org/naturalearth/10m/cultural"
_ZIP = "ne_10m_admin_0_countries.zip"
_DEFAULT_CACHE = pathlib.Path(__file__).resolve().parent / ".cache" / "naturalearth"

# 无主权/联合主权的行: ISO_A2 为 -99 → 置 0(未知)
_UNKNOWN = {"-99", ""}


def _read_features(zip_path: pathlib.Path) -> list[tuple[str, list[dict]]]:
    """Return [(iso_a2, [{"outer": ring, "holes": [...]}...]), ...]."""
    import io
    import shapefile  # pyshp, build-time only

    out: list[tuple[str, list[dict]]] = []
    with zipfile.ZipFile(zip_path) as zf:
        reader = shapefile.Reader(
            shp=io.BytesIO(zf.read(zip_path.stem + ".shp")),
            shx=io.BytesIO(zf.read(zip_path.stem + ".shx")),
            dbf=io.BytesIO(zf.read(zip_path.stem + ".dbf")),
        )
        fields = [f[0] for f in reader.fields[1:]]
        iso_idx = fields.index("ISO_A2") if "ISO_A2" in fields else None
        iso_eh_idx = fields.index("ISO_A2_EH") if "ISO_A2_EH" in fields else None
        if iso_idx is None:
            print("[country-grid] ISO_A2 field not found", file=sys.stderr)
            sys.exit(2)
        for sr in reader.shapeRecords():
            iso = str(sr.record[iso_idx]).strip()
            # 法/英/挪等国家的本土被拆成 ISO_A2=-99, 但 ISO_A2_EH 有效
            if iso in _UNKNOWN and iso_eh_idx is not None:
                iso = str(sr.record[iso_eh_idx]).strip()
            if iso in _UNKNOWN:
                continue
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
                x, y = inner[0]
                inside = False
                for (x1, y1), (x2, y2) in zip(outer, outer[1:] + outer[:1]):
                    if (y1 > y) != (y2 > y):
                        x_cross = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
                        if x_cross > x:
                            inside = not inside
                return inside

            outers = [r for r in rings if _signed_area(r) < 0]
            holes = [r for r in rings if _signed_area(r) >= 0]
            polys = []
            for outer in outers:
                polys.append({"outer": outer,
                              "holes": [h for h in holes if _contains(outer, h)]})
            out.append((iso, polys))
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--res", type=float, default=2.0,
                        help="cells per degree (default 2 = 1/2°, ~55 km)")
    parser.add_argument("--out", type=str, default=None,
                        help="output .npz path (default: nowhere/data/country_grid.npz)")
    parser.add_argument("--cache", type=str, default=str(_DEFAULT_CACHE),
                        help="download cache directory")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    res = float(args.res)
    n_lat = int(round(180.0 * res))
    n_lon = int(round(360.0 * res))

    cache = pathlib.Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)
    zip_path = cache / _ZIP
    if not zip_path.exists():
        print(f"[country-grid] downloading {_CDN}/{_ZIP} …")
        urllib.request.urlretrieve(f"{_CDN}/{_ZIP}", zip_path)

    features = _read_features(zip_path)
    print(f"[country-grid] {len(features)} countries")

    # 8× 超采样 + 多数表决: 直接按文件顺序覆盖会让边境格整体归给后绘制的
    # 邻国(首尔格被朝鲜覆盖), 多数表决按实际覆盖面积取赢家。
    from PIL import Image, ImageDraw  # build-time only
    SUPERSAMPLE = 8
    res_big = res * SUPERSAMPLE
    n_lat_big = n_lat * SUPERSAMPLE
    n_lon_big = n_lon * SUPERSAMPLE
    img = Image.new("I", (n_lon_big, n_lat_big), 0)
    draw = ImageDraw.Draw(img)
    scale_x = n_lon_big / 360.0
    scale_y = n_lat_big / 180.0
    codes: list[str] = [""]
    index: dict[str, int] = {}
    for iso, polys in features:
        idx = index.get(iso)
        if idx is None:
            idx = len(codes)
            index[iso] = idx
            codes.append(iso)
        for poly in polys:
            pts = [((lon + 180.0) * scale_x, (90.0 - lat) * scale_y)
                   for lon, lat in poly["outer"]]
            draw.polygon(pts, fill=idx)
            for hole in poly["holes"]:
                hpts = [((lon + 180.0) * scale_x, (90.0 - lat) * scale_y)
                        for lon, lat in hole]
                draw.polygon(hpts, fill=0)
    big = np.array(img, dtype=np.uint16)
    blocks = big.reshape(n_lat, SUPERSAMPLE, n_lon, SUPERSAMPLE) \
               .transpose(0, 2, 1, 3) \
               .reshape(n_lat * n_lon, SUPERSAMPLE * SUPERSAMPLE)
    max_code = len(codes)  # bincount 桶数上界

    def _mode_row(row: np.ndarray) -> int:
        counts = np.bincount(row, minlength=max_code)
        # 只在陆地票里表决: 小于半格的小岛(如济州)不能因海洋票占多数被抹掉
        counts[0] = 0
        return int(np.argmax(counts))

    modes = np.apply_along_axis(_mode_row, 1, blocks)
    grid = modes.reshape(n_lat, n_lon).astype(np.uint16)

    root = pathlib.Path(__file__).resolve().parent.parent
    out_path = pathlib.Path(args.out) if args.out else root / "nowhere" / "data" / "country_grid.npz"
    np.savez_compressed(
        out_path,
        grid=grid,
        codes=np.array(codes, dtype=object),
        res=np.array(res),
        n_lat=np.array(n_lat),
        n_lon=np.array(n_lon),
        version=np.array("nowhere-real-earth-country-grid-v1"),
        source=np.array("Natural Earth 10m admin_0_countries, public domain"),
    )
    filled = int((grid > 0).sum())
    print(f"[country-grid] wrote {out_path} ({out_path.stat().st_size / 1e6:.2f} MB, "
          f"{n_lat}x{n_lon}, {filled} land cells, {len(codes) - 1} countries)")


if __name__ == "__main__":
    main()
