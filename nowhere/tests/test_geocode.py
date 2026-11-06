# -*- coding: utf-8 -*-
"""地名解析测试 (R2, 济州岛验收项 3/7)。"""
from __future__ import annotations

import pytest

from nowhere import geocode

SEOGWIPO = (33.2528, 126.561)


def _dist_km(a, b):
    from nowhere.terrain import haversine_km
    return haversine_km(a[0], a[1], b[0], b[1])


class TestSeogwipoAliases:
    @pytest.mark.asyncio
    async def test_three_languages_resolve_same_city(self):
        """验收 3: Seogwipo / 西归浦 / 서귀포시 解析到同一城市。"""
        r_en = await geocode.lookup("Seogwipo")
        r_zh = await geocode.lookup("西归浦")
        r_ko = await geocode.lookup("서귀포시")
        assert r_en is not None and r_zh is not None and r_ko is not None
        for r in (r_en, r_zh, r_ko):
            assert _dist_km(r, SEOGWIPO) < 10.0, r

    @pytest.mark.asyncio
    async def test_zh_not_resolved_to_japan(self):
        """「西归浦」绝不能被静默解析到日本 (基线 bug: 33.49,132.33)。"""
        r = await geocode.lookup("西归浦")
        assert r is not None
        assert abs(r[1] - 126.56) < 5.0, r

    @pytest.mark.asyncio
    async def test_suffixed_zh_name(self):
        r = await geocode.lookup("西归浦市")
        assert r is not None
        assert _dist_km(r, SEOGWIPO) < 10.0, r

    @pytest.mark.asyncio
    async def test_jeju_entities(self):
        """验收 4 数据: 济州岛/济州市/西归浦/汉拿山 及三种语言别名。"""
        for name in ("济州岛", "Jeju Island", "제주도", "济州市", "Jeju City",
                     "제주시", "西归浦", "Seogwipo", "서귀포시",
                     "汉拿山", "Hallasan", "한라산"):
            r = await geocode.lookup(name)
            assert r is not None, name
            assert 33.0 <= r[0] <= 33.6 and 126.3 <= r[1] <= 126.7, (name, r)


class TestOfflineAndFailure:
    @pytest.mark.asyncio
    async def test_offline_when_nominatim_down(self, monkeypatch):
        """验收 7: 在线服务失败时仍可运行并明确使用 fallback。"""

        async def _boom(place, near_cc):
            raise ConnectionError("network down")

        monkeypatch.setattr(geocode, "_nominatim_rich", _boom)
        r = await geocode.lookup_rich("西归浦")
        assert r is not None and r["lat"] is not None
        assert r["source"] in ("places_patch_or_db", "geonames_offline", "disk_cache", "memory")
        # 未知地名在线挂掉 → 明确失败而不是抛异常
        assert await geocode.lookup("不存在的虚构地方xyz") is None

    @pytest.mark.asyncio
    async def test_disk_cache_serves_without_network(self, monkeypatch):
        """磁盘缓存: 命中后离线可用, 不重复打在线服务。"""
        calls = {"n": 0}

        async def _fake_nominatim(place, near_cc):
            calls["n"] += 1
            return {"result": {"lat": 10.0, "lon": 20.0, "display_name": "Testville, XX"},
                    "candidates": [], "ambiguous": False}

        monkeypatch.setattr(geocode, "_nominatim_rich", _fake_nominatim)
        r1 = await geocode.lookup_rich("Testville")
        assert r1["source"] == "nominatim" and r1["lat"] == 10.0
        # 清内存缓存, 模拟重启后只靠磁盘缓存
        geocode._geocode_cache.clear()
        monkeypatch.setattr(geocode, "_nominatim_rich", _boom_raises)
        r2 = await geocode.lookup_rich("Testville")
        assert r2 is not None and r2["lat"] == 10.0
        assert r2["source"] == "disk_cache"
        assert calls["n"] == 1


async def _boom_raises(place, near_cc):
    raise ConnectionError("down")


class TestAmbiguity:
    @pytest.mark.asyncio
    async def test_ambiguous_returns_candidates_not_wrong_country(self, monkeypatch):
        """模糊地名不得静默解析到错误国家: 返回候选或明确失败。"""
        async def _ambiguous(place, near_cc):
            return {
                "result": None,
                "ambiguous": True,
                "candidates": [
                    {"lat": 33.2528, "lon": 126.561, "display_name": "Seogwipo-si, Jeju, KR"},
                    {"lat": 33.495, "lon": 132.328, "display_name": "Nishikigo, Shikoku, JP"},
                ],
            }

        monkeypatch.setattr(geocode, "_nominatim_rich", _ambiguous)
        # 用不在离线库里的地名走在线路径 (西归浦 已在 places_patch 本地命中)
        rich = await geocode.lookup_rich("Ambiguousville")
        assert rich is not None
        assert rich["ambiguous"] is True
        assert rich["lat"] is None
        assert len(rich["candidates"]) == 2
        # 兼容入口返回 None (明确失败)
        assert await geocode.lookup("Ambiguousville") is None

    @pytest.mark.asyncio
    async def test_near_context_prefers_local_country(self, monkeypatch):
        """near 上下文应优先同国家候选。"""
        scored = geocode._pick_candidate(
            [
                {"lat": 33.2528, "lon": 126.561, "display_name": "Seogwipo-si, KR",
                 "addresstype": "city", "importance": 0.4, "name": "Seogwipo-si"},
                {"lat": 33.495, "lon": 132.328, "display_name": "Nishikigo, JP",
                 "addresstype": "town", "importance": 0.4, "name": "Nishikigo"},
            ],
            "seogwipo", "KR",
        )
        assert scored["ambiguous"] is False
        assert abs(scored["result"]["lon"] - 126.561) < 0.01


class TestRateLimit:
    @pytest.mark.asyncio
    async def test_rate_limited_wait(self):
        """两次在线请求之间至少间隔 1.2s (Nominatim 政策)。"""
        import time
        geocode._last_nominatim_call = 0.0
        t0 = time.monotonic()
        await geocode._rate_limited_wait()
        t1 = time.monotonic()
        # 第一次冷启动: 间隔计数从 0 开始 → 无等待; 验证状态已更新即可
        assert geocode._last_nominatim_call > 0
        await geocode._rate_limited_wait()
        t2 = time.monotonic()
        assert t2 - t1 >= 1.0  # 第二次必须等够限流间隔


class TestCacheContext:
    """R3/P0-2: 缓存键必须包含位置上下文(near_cc), 同名地名互不污染。"""

    @pytest.mark.asyncio
    async def test_memory_cache_isolated_by_country(self, monkeypatch):
        async def _fake(place, near_cc):
            coords = {"US": (40.0, -75.0), "CN": (30.0, 115.0)}[near_cc]
            return {"result": {"lat": coords[0], "lon": coords[1],
                               "display_name": f"Springfield, {near_cc}"},
                    "candidates": [], "ambiguous": False}

        monkeypatch.setattr(geocode, "_nominatim_rich", _fake)
        r_us = await geocode.lookup_rich("Springfield", near=(40.0, -75.0))
        r_cn = await geocode.lookup_rich("Springfield", near=(30.0, 115.0))
        assert r_us["lat"] == 40.0 and r_us["lon"] == -75.0
        assert r_cn["lat"] == 30.0 and r_cn["lon"] == 115.0
        # 在线服务挂掉后, 两个国家的内存缓存各自命中, 不互相污染
        monkeypatch.setattr(geocode, "_nominatim_rich", _boom_raises)
        r_us2 = await geocode.lookup_rich("Springfield", near=(41.0, -74.0))
        r_cn2 = await geocode.lookup_rich("Springfield", near=(31.0, 114.0))
        assert r_us2["lat"] == 40.0 and r_us2["source"] == "memory"
        assert r_cn2["lat"] == 30.0 and r_cn2["source"] == "memory"

    @pytest.mark.asyncio
    async def test_disk_cache_isolated_by_country(self, monkeypatch, tmp_path):
        import json
        import time as _time
        cdir = tmp_path / "gc"
        cdir.mkdir()
        monkeypatch.setattr(geocode, "_CACHE_PATH", cdir / "geocode_cache.json")
        geocode._disk_cache = None
        geocode._disk_dirty = False

        async def _fake(place, near_cc):
            coords = {"US": (40.0, -75.0), "CN": (30.0, 115.0)}[near_cc]
            return {"result": {"lat": coords[0], "lon": coords[1],
                               "display_name": f"Springfield, {near_cc}"},
                    "candidates": [], "ambiguous": False}

        monkeypatch.setattr(geocode, "_nominatim_rich", _fake)
        await geocode.lookup_rich("Springfield", near=(40.0, -75.0))
        await geocode.lookup_rich("Springfield", near=(30.0, 115.0))
        # 模拟重启: 清内存缓存, 在线服务挂掉, 只靠磁盘缓存
        geocode._geocode_cache.clear()
        monkeypatch.setattr(geocode, "_nominatim_rich", _boom_raises)
        r_us = await geocode.lookup_rich("Springfield", near=(41.0, -74.0))
        r_cn = await geocode.lookup_rich("Springfield", near=(31.0, 114.0))
        assert r_us is not None and r_us["lat"] == 40.0
        assert r_cn is not None and r_cn["lat"] == 30.0

    @pytest.mark.asyncio
    async def test_old_version_disk_cache_ignored(self, monkeypatch, tmp_path):
        """旧版(v1, 键不含 near_cc)缓存必须整体失效, 不得错误命中。"""
        import json
        import time as _time
        cdir = tmp_path / "gc"
        cdir.mkdir()
        cache_file = cdir / "geocode_cache.json"
        cache_file.write_text(json.dumps({
            "version": 1,
            "entries": {
                "springfield": {"lat": 1.0, "lon": 2.0, "ts": _time.time()},
            },
        }), encoding="utf-8")
        monkeypatch.setattr(geocode, "_CACHE_PATH", cache_file)
        geocode._disk_cache = None
        geocode._disk_dirty = False
        geocode._geocode_cache.clear()
        monkeypatch.setattr(geocode, "_nominatim_rich", _boom_raises)
        r = await geocode.lookup_rich("Springfield", near=(40.0, -75.0))
        assert r is None  # 旧条目绝不能被当成新查询的结果

    @pytest.mark.asyncio
    async def test_negative_cache_isolated_by_country(self, monkeypatch):
        async def _fail_us(place, near_cc):
            if near_cc == "US":
                raise ConnectionError("down in US")
            return {"result": {"lat": 30.0, "lon": 115.0,
                               "display_name": "X, CN"},
                    "candidates": [], "ambiguous": False}

        monkeypatch.setattr(geocode, "_nominatim_rich", _fail_us)
        assert await geocode.lookup_rich("Fuzzville", near=(40.0, -75.0)) is None
        # US 的负缓存不该挡住 CN 的成功查询
        r_cn = await geocode.lookup_rich("Fuzzville", near=(30.0, 115.0))
        assert r_cn is not None and r_cn["lat"] == 30.0
