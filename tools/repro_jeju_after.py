# -*- coding: utf-8 -*-
"""Post-fix Jeju verification. Run from repo root."""
import asyncio
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from nowhere import terrain
from nowhere.walk import water_ahead_km, nearest_ocean_km_and_bearing, nearest_ocean, water_ahead
import nowhere.server as server_mod


def grid_probe(lat, lon):
    s = terrain.surface(lat, lon)
    e = terrain.elevation(lat, lon)
    return f"surface={s!r} elev={e:.0f}m"


async def main():
    print("=== AFTER FIX ===")
    for name, (lat, lon) in [("Jeju mid (33.3803,126.5483)", (33.3803, 126.5483)),
                             ("Jeju south (33.2454,126.5483)", (33.2454, 126.5483))]:
        print(f"{name}: {grid_probe(lat, lon)}")

    km, brg = nearest_ocean_km_and_bearing(33.2454, 126.5483, max_km=50.0)
    print(f"nearest_ocean_km_and_bearing = {km} km @ {brg} deg")
    print(f"nearest_ocean (rich) = {nearest_ocean(33.2454, 126.5483, 50)}")
    wide = server_mod._wide_coast_scan(33.2454, 126.5483)
    print(f"_wide_coast_scan = {wide}")

    from nowhere.walk import _pick_semantic_bearing
    brg, delta = _pick_semantic_bearing(33.3803, 126.5483, "toward_sea", 2.0)
    print(f"toward_sea pick from mid-island: bearing={brg} (want ~south/180)")

    from nowhere import geocode
    for q in ("Seogwipo", "西归浦", "서귀포시", "西归浦市"):
        r = await geocode.lookup(q)
        print(f"geocode({q!r}) = {r}")

    print("Southward walk surfaces (1km steps from 33.3803,126.5483):")
    lat, lon = 33.3803, 126.5483
    for d in (1, 3, 5, 8, 10, 12, 14, 15):
        nlat, nlon = terrain.destination(lat, lon, 180.0, d)
        print(f"  {d:>2} km -> ({nlat:.3f},{nlon:.3f}) {terrain.surface(nlat, nlon)!r} elev={terrain.elevation(nlat,nlon):.0f}")

    # water ahead detail
    print("water_ahead S from mid:", water_ahead(33.3803, 126.5483, 180, 30))


asyncio.run(main())
