# -*- coding: utf-8 -*-
"""Reproduce the Jeju baseline issues (pre-fix). Run from repo root."""
import asyncio
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from nowhere import terrain
from nowhere.walk import water_ahead_km, nearest_ocean_km_and_bearing
import nowhere.server as server_mod


def grid_probe(lat, lon):
    s = terrain.surface(lat, lon)
    e = terrain.elevation(lat, lon)
    return f"surface={s!r} elev={e:.0f}m"


async def main():
    print("=== BASELINE (pre-fix) ===")
    # 1) Jeju mid-island: what does the 1deg grid say?
    for name, (lat, lon) in [("Jeju mid (33.3803,126.5483)", (33.3803, 126.5483)),
                             ("Jeju south (33.2454,126.5483)", (33.2454, 126.5483))]:
        print(f"{name}: {grid_probe(lat, lon)}")

    # 2) nearest ocean from the south point (bug: 1300 km)
    km, brg = nearest_ocean_km_and_bearing(33.2454, 126.5483, max_km=50.0)
    print(f"nearest_ocean_km_and_bearing(33.2454,126.5483, max50) = {km} km @ {brg} deg")
    wide_km, wide_brg = server_mod._wide_coast_scan(33.2454, 126.5483)
    print(f"_wide_coast_scan(33.2454,126.5483) = {wide_km} km @ {wide_brg} deg")

    # 3) toward_sea bearing from mid-island
    from nowhere.walk import _pick_semantic_bearing
    brg, delta = _pick_semantic_bearing(33.3803, 126.5483, "toward_sea", 2.0)
    print(f"toward_sea pick from mid-island: bearing={brg} (should be ~south)")

    # 4) geocode Seogwipo variants
    from nowhere import geocode
    for q in ("Seogwipo", "西归浦", "서귀포시"):
        r = await geocode.lookup(q)
        print(f"geocode({q!r}) = {r}")

    # 5) surface along a southward walk path
    print("Southward walk surfaces (1km steps from 33.3803,126.5483):")
    lat, lon = 33.3803, 126.5483
    for d in (1, 3, 5, 8, 10, 12, 14, 15):
        nlat, nlon = terrain.destination(lat, lon, 180.0, d)
        print(f"  {d:>2} km -> ({nlat:.3f},{nlon:.3f}) {terrain.surface(nlat, nlon)!r} elev={terrain.elevation(nlat,nlon):.0f}")


asyncio.run(main())
