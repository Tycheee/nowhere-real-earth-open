# -*- coding: utf-8 -*-
"""End-to-end Jeju trial: open_door to Seogwipo, then walk toward the sea.

Uses the real weather API (falls back to climate estimate offline).
Run: .venv\Scripts\python -X utf8 tools\e2e_jeju.py
"""
import asyncio
import io
import os
import sys
import tempfile

# 持久化隔离: 不污染真实存档
os.environ["NOWHERE_HOME"] = tempfile.mkdtemp(prefix="nowhere-e2e-")

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from nowhere import state as state_mod
import nowhere.server as server_mod


async def main():
    server_mod._state = state_mod.WorldState()
    print("=" * 60)
    print("1) open_door('西归浦')")
    r = await server_mod.open_door_impl(to="西归浦")
    print(r["text"][:600])
    data = r["data"]
    print("\n-- observed_environment --")
    obs = data.get("observed_environment", {})
    print("position:", obs.get("position"))
    print("surface:", obs.get("surface"), "| elev:", obs.get("elevation_m"))
    print("weather:", obs.get("weather"))
    print("coast:", obs.get("coast"))
    print("narrative_event:", data.get("narrative_event"))

    print("=" * 60)
    print("2) walk('toward_sea') x3 from near Seogwipo")
    for i in range(3):
        r = await server_mod.walk_impl(direction="toward_sea", distance_km=2.0)
        d = r["data"]
        print(f"\n-- step {i + 1} --")
        print("pos:", d["position"], "| surface:", d["step"].get("new_surface"))
        if d.get("rejected"):
            print("rejected:", d["rejected"], "nearest_sea_km:", d.get("nearest_sea_km"))
        print(r["text"][:400])
        if "facts_violations" in d.get("narrative_event", {}):
            print("facts_violations:", d["narrative_event"]["facts_violations"])

    print("=" * 60)
    print("3) walk_to('汉拿山') (should resolve)")
    r = await server_mod.walk_to_impl("汉拿山")
    print(r["text"][:300])
    print("err:", (r.get("data") or {}).get("error"))


asyncio.run(main())
