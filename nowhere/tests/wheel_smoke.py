# -*- coding: utf-8 -*-
"""Wheel 安装完整性 smoke test (P0-4)。

必须能在**任意工作目录**、只依赖已安装 wheel 的情况下运行:
    python -m nowhere.tests.wheel_smoke

检查:
1. 关键运行时数据文件随 wheel 分发 (landmask.npz / grid_tiny.npz / content.db / static)。
2. 济州岛基本流程可离线走通 (open_door 西归浦 → walk toward_sea)。
3. 输出结构含 observed_environment / narrative_event。

不依赖真实外网服务(天气/电台/水文全部 mock)。
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import tempfile


def _fail(msg: str) -> "None":
    print(f"[wheel-smoke] FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    # 持久化隔离: 不污染真实存档
    os.environ["NOWHERE_HOME"] = tempfile.mkdtemp(prefix="nowhere-wheel-smoke-")

    from nowhere import server as server_mod, state as state_mod

    # ── 1. 数据文件随 wheel 分发 ─────────────────────────────────────
    import nowhere
    pkg_dir = pathlib.Path(nowhere.__file__).resolve().parent
    required = [
        pkg_dir / "data" / "landmask.npz",
        pkg_dir / "data" / "country_grid.npz",
        pkg_dir / "data" / "grid_tiny.npz",
        pkg_dir / "data" / "offline" / "cities_major.json",
        pkg_dir / "content.db",
        pkg_dir / "static" / "index.html",
    ]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        _fail("wheel 缺少运行时文件: " + ", ".join(missing))
    print("[wheel-smoke] runtime data files present")

    # ── 2. 济州岛离线流程 ────────────────────────────────────────────
    async def _none(*a, **k):
        return None

    async def _empty(*a, **k):
        return []

    import nowhere.hydrology as hydrology_mod
    import nowhere.water as water_mod
    import nowhere.weather as weather_mod
    hydrology_mod.nearby_water = _empty
    water_mod.sea_surface_temp = _none
    water_mod.marine_life = _none
    server_mod._get_radio = _none  # 电台: 离线兜底仍可用, 这里直接跳过在线探测

    async def _fixed_weather(lat, lon, **kw):
        return {"temp_c": 21.7, "feels_c": 21.0, "wind_ms": 2.0, "humidity": 60,
                "precip": "none", "cloud": 10.0, "text": "晴", "source": "openmeteo"}

    weather_mod.current = _fixed_weather

    async def _run() -> None:
        server_mod._state = state_mod.WorldState()
        r = await server_mod.open_door_impl(to="西归浦")
        data = r.get("data") or {}
        pos = data.get("position") or {}
        assert abs(pos.get("lat", 0) - 33.2528) < 0.01, r
        assert "observed_environment" in data, "open_door 结果缺 observed_environment"
        assert "narrative_event" in data, "open_door 结果缺 narrative_event"
        coast = data["observed_environment"].get("coast") or {}
        assert coast.get("nearest_ocean", {}).get("km", 999) < 50, "西归浦 50 km 内必须有海"
        r2 = await server_mod.walk_impl(direction="toward_sea", distance_km=2.0)
        d2 = r2.get("data") or {}
        assert "rejected" not in d2, r2
        print("[wheel-smoke] open_door(西归浦) + walk(toward_sea) OK")
        print(f"[wheel-smoke] pos={pos} coast={coast['nearest_ocean']}")

    asyncio.run(_run())
    print("[wheel-smoke] PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
