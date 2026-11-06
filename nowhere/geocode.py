"""地理编码: 本地(补丁/GeoNames) → 磁盘缓存 → Nominatim 在线(限流+歧义防护)。

离线源: nowhere/data/packs/cities15000.txt(全球 1.5 万+ 城镇,
中文名在 alternatenames 列,按人口排序取最大)。

R2 (nowhere-real-earth) changes:

* **磁盘缓存** — Nominatim 命中写 ``<NOWHERE_HOME|~>/.nowhere/geocode_cache.json``
  (TTL 30 天), 重启后仍可用, 离线也保留在线解析成果。
* **限流** — 遵守 Nominatim 使用政策(≤1 req/s): 每次在线请求前至少间隔 1.2s,
  等待由 asyncio.sleep 完成, 不打穿限流。
* **歧义防护** — 在线查询取 limit=5 并对候选打分(名称精确匹配/行政类型/
  importance), 首选与 ``near`` 同国家的候选; 当最优两名候选分数接近且国家
  不同 → 视为歧义, 明确失败并返回候选列表, 绝不静默解析到错误国家
  (「西归浦」曾被解析到日本四国)。
* **多语言别名** — 查询先经 OpenCC 繁→简归一化; 中文/英文/当地语言别名
  由 places_patch 与 GeoNames alternatenames 覆盖。
"""

from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import threading
import time
import urllib.parse
from typing import Final

import httpx

from nowhere.providers import _get_client

logger = logging.getLogger(__name__)

_PACK_PATH = pathlib.Path(__file__).resolve().parent / "data" / "packs" / "cities15000.txt"
_SPECIAL_PATH = pathlib.Path(__file__).resolve().parent / "data" / "special_places.json"

# ── 磁盘缓存 (R2) ────────────────────────────────────────────────────

def cache_dir() -> pathlib.Path:
    """数据目录: NOWHERE_HOME > ~/.nowhere。与 state.py 的落盘目录一致。"""
    import os
    env = os.environ.get("NOWHERE_HOME", "").strip()
    if env:
        return pathlib.Path(env)
    return pathlib.Path.home() / ".nowhere"


_CACHE_PATH = cache_dir() / "geocode_cache.json"
_CACHE_TTL_SECONDS: Final = 30 * 24 * 3600  # 30 天
_CACHE_MAX_ENTRIES: Final = 2000
# R3: v2 起缓存键包含 near_cc (同名地名在不同国家的解析隔离)。
# v1 条目不含该信息, 读入时按不兼容整体丢弃, 避免旧条目错误命中。
_CACHE_VERSION: Final = 2

_disk_cache: dict | None = None
_disk_lock = threading.RLock()  # _disk_put 在持锁时调用 _load_disk_cache, 需可重入
_disk_dirty = False


def _load_disk_cache() -> dict:
    global _disk_cache, _disk_dirty
    with _disk_lock:
        if _disk_cache is not None:
            return _disk_cache
        _disk_cache = {}
        try:
            if _CACHE_PATH.exists():
                raw = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
                # 版本不符(旧 v1 缓存) → 整文件视为过期, 下次保存时覆盖
                if isinstance(raw, dict) and raw.get("version") == _CACHE_VERSION \
                        and isinstance(raw.get("entries"), dict):
                    _disk_cache = raw["entries"]
                else:
                    _disk_dirty = True
        except Exception:
            logger.warning("geocode 磁盘缓存读取失败, 按空缓存继续", exc_info=True)
        return _disk_cache


def _save_disk_cache() -> None:
    global _disk_dirty
    with _disk_lock:
        if not _disk_dirty:
            return
        try:
            _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = _CACHE_PATH.with_suffix(".tmp")
            payload = {"version": _CACHE_VERSION, "entries": _disk_cache or {}}
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(_CACHE_PATH)
            _disk_dirty = False
        except Exception:
            logger.warning("geocode 磁盘缓存写入失败", exc_info=True)


def _disk_get(key: str) -> dict | None:
    cache = _load_disk_cache()
    entry = cache.get(key)
    if not isinstance(entry, dict):
        return None
    ts = entry.get("ts", 0)
    if time.time() - ts > _CACHE_TTL_SECONDS:
        return None
    if not isinstance(entry.get("lat"), (int, float)) or not isinstance(entry.get("lon"), (int, float)):
        return None
    return entry


def _disk_put(key: str, entry: dict) -> None:
    global _disk_dirty
    with _disk_lock:
        cache = _load_disk_cache()
        cache[key] = {**entry, "ts": time.time()}
        if len(cache) > _CACHE_MAX_ENTRIES:
            # 简单淘汰: 按时间戳丢最旧的一半
            items = sorted(cache.items(), key=lambda kv: kv[1].get("ts", 0))
            for old_key, _ in items[: len(items) // 2]:
                cache.pop(old_key, None)
        _disk_dirty = True
    _save_disk_cache()


def reset_disk_cache() -> None:
    """清空内存与磁盘缓存 (测试用)。"""
    global _disk_cache, _disk_dirty
    with _disk_lock:
        _disk_cache = {}
        _disk_dirty = True
        try:
            if _CACHE_PATH.exists():
                _CACHE_PATH.unlink()
        except OSError:
            pass


# ── 限流 (R2) ────────────────────────────────────────────────────────

_MIN_INTERVAL_SECONDS: Final = 1.2  # Nominatim policy: max 1 req/s
_last_nominatim_call: float = 0.0
_rate_lock = asyncio.Lock()


async def _rate_limited_wait() -> None:
    global _last_nominatim_call
    async with _rate_lock:
        now = time.monotonic()
        wait = _last_nominatim_call + _MIN_INTERVAL_SECONDS - now
        if wait > 0:
            await asyncio.sleep(wait)
        _last_nominatim_call = time.monotonic()


# ── 查询归一化 (R2: 繁→简) ───────────────────────────────────────────

_OPENCC = None
_opencc_attempted = False


def _normalize(place: str) -> str:
    global _OPENCC, _opencc_attempted
    q = place.strip().lower()
    if not _opencc_attempted:
        _opencc_attempted = True
        try:
            from opencc import OpenCC
            _OPENCC = OpenCC("t2s")
        except Exception:
            _OPENCC = None
    if _OPENCC is not None:
        try:
            q = _OPENCC.convert(q)
        except Exception:
            pass
    return q


# ── 内存缓存 ─────────────────────────────────────────────────────────

# Cache geocode results to avoid re-scanning places.db / cities15000 on every call
_geocode_cache: dict[str, tuple[float, float] | None] = {}
_GEOCODE_CACHE_MAX: Final = 500
_special_places: dict[str, dict] | None = None

# 负缓存: Nominatim 的一次瞬时故障不该被固化成永久 None,
# 但也不必每次都重打 5s 超时的请求 —— 短 TTL 后允许重试
_negative_cache: dict[str, float] = {}
_NEGATIVE_TTL: Final = 60.0

# cities15000 预处理行: (name_lower, ascii_lower, 别名tokens, pop, cc, coords)
# 只在首次离线查找时加载并规范化一次, 之后查询不再重复 open/.lower()
_city_rows: list[tuple[str, str, list[str], int, str, tuple[float, float]]] | None = None


def clear_cache() -> None:
    """Clear the geocode cache (for testing)."""
    global _city_rows
    _geocode_cache.clear()
    _negative_cache.clear()
    # _city_rows 是按 _PACK_PATH 派生的解析缓存 —— 测试会把 _PACK_PATH
    # monkeypatch 到不存在的路径, 不重置则解析结果残留, "离线源全缺 → None"
    # 的判定被前序测试污染(见 test_geocode_none_when_down_and_no_pack)。
    _city_rows = None
    # places 侧的派生缓存同理: 测试把 _DB monkeypatch 到不存在的路径,
    # 但已建立的 sqlite 连接与补丁索引仍指向真库, 离线降级路径会照常命中。
    # clear_cache 是"清空 geocode 的查找缓存", 而 places 是它的下游查找源,
    # 故在此一并重置; 否则 _DB 的替换对已缓存连接不生效。
    from nowhere import places as _places

    _places._conn_instance = None
    _places._PATCH_CACHE = None
    _places._PATCH_LOWER_CACHE = None


def _load_special() -> dict[str, dict]:
    """Load special_places.json once and cache."""
    global _special_places
    if _special_places is None:
        if _SPECIAL_PATH.exists():
            _special_places = json.loads(_SPECIAL_PATH.read_text(encoding="utf-8"))
        else:
            _special_places = {}
    return _special_places


def _load_city_rows() -> list[tuple[str, str, list[str], int, str, tuple[float, float]]]:
    global _city_rows
    if _city_rows is None:
        rows: list[tuple[str, str, list[str], int, str, tuple[float, float]]] = []
        if _PACK_PATH.exists():
            with open(_PACK_PATH, encoding="utf-8") as f:
                for line in f:
                    parts = line.rstrip("\n").split("\t")
                    if len(parts) < 15:
                        continue
                    try:
                        pop = int(parts[14] or 0)
                    except ValueError:
                        pop = 0
                    try:
                        coords = (float(parts[4]), float(parts[5]))
                    except ValueError:
                        continue  # 坐标非数值的坏行整行跳过
                    alts = [t.strip() for t in parts[3].lower().split(",") if t.strip()]
                    cc = parts[8].strip().upper() if len(parts) > 8 else ""
                    rows.append((parts[1].lower(), parts[2].lower(), alts, pop, cc, coords))
        _city_rows = rows
    return _city_rows


def _offline_lookup(place: str) -> tuple[float, float] | None:
    """在 cities15000 里查地名。精确名 > 别名包含,同优先级取人口最多。"""
    best = _offline_best(place)
    return best["coords"] if best else None


def _offline_best(place: str, near: tuple[float, float] | None = None) -> dict | None:
    """Offline candidates with ambiguity + near-country preference."""
    q = _normalize(place)
    if not q:
        return None

    from nowhere import country as _country_mod

    near_cc = None
    if near is not None:
        try:
            near_cc = _country_mod.country_code_of(near[0], near[1])
        except Exception:
            near_cc = None

    cands: list[dict] = []  # {score, coords, cc, name}
    for name, ascii_name, alts, pop, cc, coords in _load_city_rows():
        score = 0.0
        if q == name or q == ascii_name:
            score = 4.0
        elif q in name or q in ascii_name:
            score = 2.0
        else:
            # 别名按 token 匹配: 整词相等 > 词内包含(防"喀什"撞上"马拉喀什")
            for token in alts:
                if token == q:
                    score = max(score, 3.0)
                    break
                if q in token:
                    score = max(score, 1.0)
        if score == 0.0:
            continue
        if near_cc and cc == near_cc:
            score += 2.0
        cands.append({"score": score * 1e12 + pop, "coords": coords, "cc": cc, "name": name})

    if not cands:
        return None
    cands.sort(key=lambda c: c["score"], reverse=True)
    best = cands[0]
    return {"coords": best["coords"], "cc": best["cc"], "name": best["name"]}


# ── Nominatim 在线 (R2: limit=5 + 打分 + 歧义防护) ──────────────────

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


def _score_candidate(c: dict, q: str, near_cc: str | None) -> float:
    """Score one Nominatim candidate against the normalized query."""
    score = 0.0
    name = (c.get("name") or "").lower()
    dn = (c.get("display_name") or "").lower()
    if q == name:
        score += 4.0
    elif q in name or name in q:
        score += 3.0
    if q in dn:
        score += 1.0
    at = c.get("addresstype", "")
    if at in ("city", "town", "village", "municipality", "island", "islet"):
        score += 1.5
    elif at in ("administrative", "county", "state", "province", "region"):
        score += 1.0
    try:
        score += float(c.get("importance") or 0) * 2.0
    except (TypeError, ValueError):
        pass
    if near_cc:
        # country of candidate is embedded in display_name; Nominatim also
        # returns address in newer API versions — fall back to text search.
        cc = (c.get("address") or {}).get("country_code", "")
        if not cc:
            # last two tokens of display_name are usually "Country" or "CountryCode"
            tokens = dn.split(",")
            for tok in reversed(tokens[:4]):
                tok = tok.strip()
                if tok == near_cc or tok == near_cc.lower():
                    cc = tok
                    break
        if cc and cc.upper() == near_cc:
            score += 2.0
    return score


def _pick_candidate(data: list[dict], q: str, near_cc: str | None) -> dict:
    """Return {"result": {...}|None, "candidates": [...], "ambiguous": bool}."""
    scored = [{"cand": c, "score": _score_candidate(c, q, near_cc)} for c in data[:5]]
    scored.sort(key=lambda s: s["score"], reverse=True)
    candidates = [s["cand"] for s in scored]
    if not scored:
        return {"result": None, "candidates": candidates, "ambiguous": False}
    top, runner = scored[0], (scored[1] if len(scored) > 1 else None)
    top_cc = (top["cand"].get("address") or {}).get("country_code", "") or _cc_from_display(top["cand"])
    runner_cc = (runner["cand"].get("address") or {}).get("country_code", "") or _cc_from_display(runner["cand"]) if runner else ""
    ambiguous = bool(
        runner is not None
        and top_cc and runner_cc and top_cc != runner_cc
        and (top["score"] - runner["score"]) < 0.5
    )
    return {"result": None if ambiguous else top["cand"], "candidates": candidates, "ambiguous": ambiguous}


def _cc_from_display(cand: dict) -> str:
    dn = (cand.get("display_name") or "").split(",")
    tail = [t.strip() for t in dn[-3:]]
    # ISO codes in nominatim display_name appear as bare 2-letter tokens
    import re
    for tok in reversed(tail):
        if re.fullmatch(r"[A-Za-z]{2}", tok):
            return tok.upper()
    return ""


async def _nominatim_rich(place: str, near_cc: str | None) -> dict:
    """Online Nominatim with limit=5, rate limiting, and ambiguity guard."""
    q = _normalize(place)
    params = {
        "q": place,
        "format": "json",
        "limit": 5,
        "accept-language": "zh-CN,zh,en,ko,ja",
    }
    await _rate_limited_wait()
    client = _get_client()
    resp = await client.get(_NOMINATIM_URL, params=params, timeout=5.0,
                            headers={"User-Agent": "nowhere-real-earth/0.1 (MCP game; contact: local)"})
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError("nominatim response must be a list")
    return _pick_candidate(data, q, near_cc)


# ── public API ───────────────────────────────────────────────────────


async def lookup(place: str, near: tuple[float, float] | None = None) -> tuple[float, float] | None:
    """Return ``(lat, lon)`` for *place*, or ``None`` on failure / no result.

    链: special_places → places.db → cities15000 → 磁盘缓存 → Nominatim(限流)。

    R2: *near* 提供时优先解析到附近国家; 歧义时明确返回 None。
    """
    rich = await lookup_rich(place, near=near)
    if rich is None or rich.get("lat") is None or rich.get("lon") is None:
        return None
    return (rich["lat"], rich["lon"])


async def lookup_rich(place: str, near: tuple[float, float] | None = None) -> dict | None:
    """Return rich geocode result: lat/lon/source/display_name/cc/ambiguous/candidates.

    ``ambiguous=True`` 时 lat/lon 为 None 但 ``candidates`` 有内容 —
    调用方应把候选展示给玩家, 而不是静默选一个。

    R3: 所有缓存键 = normalized place + near_cc — 同名地名在不同国家
    上下文的解析结果互不污染(memory/disk/negative 三级一致)。
    """
    from nowhere import country as _country_mod
    near_cc = None
    if near is not None:
        try:
            near_cc = _country_mod.country_code_of(near[0], near[1])
        except Exception:
            near_cc = None
    key = f"{_normalize(place)}|cc:{near_cc or ''}"
    if len(_geocode_cache) > _GEOCODE_CACHE_MAX:
        _geocode_cache.clear()  # simple eviction: clear all when full
    if key in _geocode_cache:
        coords = _geocode_cache[key]
        return {"lat": coords[0], "lon": coords[1], "source": "memory"} if coords else None

    # Special places (continents, oceans, poles, etc.)
    special = _load_special()
    sp = special.get(place) or special.get(_normalize(place)) or special.get(place.strip())
    # 缺字段/空 dict 条目跳过, KeyError 不该穿透给调用方
    if isinstance(sp, dict) and isinstance(sp.get("lat"), (int, float)) \
            and isinstance(sp.get("lon"), (int, float)):
        result = (float(sp["lat"]), float(sp["lon"]))
        _geocode_cache[key] = result
        return {"lat": result[0], "lon": result[1], "source": "special_places"}

    # Offline sources first (fast)
    from nowhere import places

    hit = places.find(place)
    if hit is not None:
        result = (hit["lat"], hit["lon"])
        _geocode_cache[key] = result
        return {"lat": result[0], "lon": result[1], "source": "places_patch_or_db"}

    # 整包逐行扫描是同步 CPU/IO, 放线程外避免阻塞事件循环
    offline = await asyncio.to_thread(_offline_best, place, near)
    if offline is not None:
        result = (offline["coords"][0], offline["coords"][1])
        _geocode_cache[key] = result
        return {"lat": result[0], "lon": result[1],
                "source": "geonames_offline", "cc": offline["cc"], "name": offline["name"]}

    if key in _negative_cache and time.monotonic() - _negative_cache[key] < _NEGATIVE_TTL:
        return None

    # 磁盘缓存: 离线也能复用过去的在线解析 (R2)
    disk = _disk_get(key)
    if disk is not None:
        result = (float(disk["lat"]), float(disk["lon"]))
        _geocode_cache[key] = result
        return {"lat": result[0], "lon": result[1],
                "source": "disk_cache", "display_name": disk.get("display_name", "")}

    # Nominatim last (slow, rate-limited, ambiguity-guarded)
    try:
        rich = await _nominatim_rich(place, near_cc)
    except Exception as exc:
        # 网络失败(含 ConnectionError/超时)与解析缺陷都只记日志, 不进穿透
        logger.debug("nominatim lookup failed for %r: %s", place, exc)
        _negative_cache[key] = time.monotonic()
        return None

    if rich["ambiguous"]:
        # 明确失败: 候选跨国家且分数接近, 不替玩家做决定 (R2)
        cands = []
        for c in rich["candidates"]:
            try:
                cands.append({
                    "lat": float(c["lat"]), "lon": float(c["lon"]),
                    "name": c.get("display_name", ""),
                    "cc": _cc_from_display(c),
                })
            except (KeyError, TypeError, ValueError):
                continue
        return {"lat": None, "lon": None, "source": "nominatim",
                "ambiguous": True, "candidates": cands}

    cand = rich["result"]
    if cand is None:
        _negative_cache[key] = time.monotonic()
        return None
    try:
        result = (float(cand["lat"]), float(cand["lon"]))
    except (KeyError, TypeError, ValueError):
        _negative_cache[key] = time.monotonic()
        return None
    _geocode_cache[key] = result
    _disk_put(key, {"lat": result[0], "lon": result[1],
                    "display_name": cand.get("display_name", "")})
    return {"lat": result[0], "lon": result[1], "source": "nominatim",
            "display_name": cand.get("display_name", ""),
            "cc": _cc_from_display(cand)}
