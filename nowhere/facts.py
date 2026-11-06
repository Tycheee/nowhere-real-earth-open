"""叙事事实校验器 (nowhere-real-earth, R2).

在场景文字输出前做轻量事实校验。校验失败按句替换为不含具体事实的
通用描写, 而不是删掉整个场景 — 保留随机偶遇与文学性。

规则
----
R1 时刻: 明确钟点必须符合当地时间与 ``sky.phase``;
        时刻词(清晨/傍晚/深夜…)不得与句中钟点矛盾。
R2 温度: 明确温度必须与当前天气一致(±3°C);
        天气来源为气候估算(climate)时, 叙事不得出现具体数字温度。
R3 地名: 出现在"眼前/脚下"等近景句式中的具体地名,
        距离当前位置超过 30 km 视为穿帮。
R4 地形: 海岸/沙滩/河流/湖泊/城市等环境描写与当前地形类型相容
        (只在近景句式中强校验, 远景描写保留文学自由)。

输出结构: 调用方在结果中区分
``observed_environment``(地理/天气/天空事实)与
``narrative_event``(校验后的文学生成, 附校验报告)。
"""

from __future__ import annotations

import re
from typing import Any

# ── 时刻词 → 允许的钟点区间(当地时间) ─────────────────────────────

_MOMENT_WORDS: dict[str, tuple[int, int]] = {
    "清晨": (4, 7), "凌晨": (0, 5), "黎明": (4, 7), "拂晓": (4, 7),
    "上午": (8, 11), "早上": (5, 9),
    "正午": (11, 13), "中午": (11, 13), "晌午": (11, 13),
    "下午": (13, 17), "午后": (13, 17),
    "傍晚": (17, 19), "黄昏": (17, 20), "日暮": (17, 20), "晚上": (18, 28),
    "深夜": (22, 24), "午夜": (23, 25), "半夜": (22, 26), "夜里": (20, 28),
}

_CN_NUM: dict[str, int] = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}

_HOUR_RE = re.compile(r"([0-9０-９]{1,2}|[一二两三四五六七八九十]{1,3})\s*点")
_TEMP_RE = re.compile(r"([0-9０-９]+(?:\.[0-9０-９]+)?)\s*(?:度|°C|℃)")
_PROXIMITY_WORDS = ("眼前", "脚下", "就在", "旁边", "身边", "头顶", "环绕", "扑面",
                    "脚边", "手边", "近处", "跟前", "贴着")

_SOFT_FILLERS = [
    "你顿了顿,又看了一眼。",
    "风吹过来,你回过神。",
    "脚步没停。",
    "你眨了眨眼,继续走。",
    "空气里有什么变了,你说不上来。",
]


def _fullwidth_to_ascii_num(s: str) -> str:
    table = str.maketrans("０１２３４５６７８９", "0123456789")
    return s.translate(table)


def parse_hour(s: str) -> int | None:
    """Extract an explicit clock hour (e.g. 6, 六, 15) from a sentence."""
    m = _HOUR_RE.search(_fullwidth_to_ascii_num(s))
    if not m:
        return None
    token = m.group(1)
    if token.isdigit():
        return int(token) % 24
    # Chinese numerals: 六=6, 十六=16, 二十三=23, 凌晨三点=3
    val = 0
    if token == "十":
        return 10
    if "十" in token:
        left, _, right = token.partition("十")
        val = (_CN_NUM.get(left, 1) if left else 1) * 10 + _CN_NUM.get(right, 0)
    else:
        val = _CN_NUM.get(token, -1)
    if val < 0 or val > 23:
        return None
    return val


def _hour_in_range(hour: int, lo: int, hi: int) -> bool:
    if lo <= hi:
        return lo <= hour <= hi
    return hour >= lo or hour <= (hi % 24)  # wraps midnight


def check_moment(sent: str, local_hour: int | None, phase: str) -> str | None:
    """R1: moment word vs explicit hour vs local time. Returns violation or None."""
    hour = parse_hour(sent)
    if hour is None:
        return None
    # 傍晚六点=18:00, 晚上七点=19:00, 晚上十点=22:00 — 傍晚/晚上时段的
    # 1-12 点按 12 小时制换算成下午/夜间钟点
    if any(w in sent for w in ("晚上",)) and 1 <= hour <= 12:
        hour = (hour % 12) + 12
    elif any(w in sent for w in ("傍晚", "黄昏", "日暮")) and 1 <= hour <= 8:
        hour += 12
    if local_hour is not None:
        # 钟点必须贴近当地时间(±3h); 极昼极夜由 phase 兜底, 不做例外
        if abs(((hour - local_hour) + 12) % 24 - 12) > 3:
            return f"钟点 {hour} 与当地时间 {local_hour} 不符"
    for word, (lo, hi) in _MOMENT_WORDS.items():
        if word in sent:
            if not _hour_in_range(hour, lo, hi):
                return f"「{word}」与钟点 {hour} 矛盾"
            break
    return None


_TEMP_CONTEXT_WORDS = ("气温", "空气", "温度", "降到", "升到", "降了", "升了",
                       "冷了", "暖了", "凉了", "热了", "冻", "严寒", "酷暑")


def check_temperature(sent: str, temp_c: float | None, source: str | None) -> str | None:
    """R2: explicit temperatures vs current weather. Returns violation or None."""
    sent_fw = _fullwidth_to_ascii_num(sent)
    has_unit = ("°C" in sent_fw) or ("℃" in sent_fw)
    # "坡 12 度"/"水温 12 度" 不是气温: 无温度语境词且无 °C/℃ 符号时跳过
    if not has_unit and not any(w in sent for w in _TEMP_CONTEXT_WORDS):
        return None
    matches = list(_TEMP_RE.finditer(sent_fw))
    if not matches:
        return None
    if source == "climate":
        # 气候估算不是"当前天气数据": 叙事不得给出具体数字温度
        return "气候估算来源下不得出现具体数字温度"
    if temp_c is None:
        return None
    for m in matches:
        # 水温/海水温度不属于空气温度校验范围
        pre = sent_fw[max(0, m.start() - 2):m.start()]
        if "水" in pre:
            continue
        try:
            value = float(m.group(1))
        except ValueError:
            continue
        if abs(value - temp_c) > 3.0:
            return f"文本温度 {value}°C 与天气 {temp_c}°C 矛盾"
    return None


# ── R3/R4: 地名距离与地形相容 ───────────────────────────────────────

def _known_place_coords(name: str) -> tuple[float, float] | None:
    from nowhere import places
    hit = places.find(name)
    if hit is not None:
        return (float(hit["lat"]), float(hit["lon"]))
    return None


def _has_proximity(sent: str) -> bool:
    return any(w in sent for w in _PROXIMITY_WORDS)


def check_place_distance(sent: str, lat: float, lon: float) -> str | None:
    """R3: 近景句式中的地名必须离得近。"""
    if not _has_proximity(sent):
        return None
    from nowhere import places
    from nowhere.terrain import haversine_km
    for name in _find_place_names(sent):
        hit = places.find(name)
        if hit is None:
            continue
        dist = haversine_km(lat, lon, float(hit["lat"]), float(hit["lon"]))
        if dist > 30.0:
            return f"地标 {name} 距离 {dist:.0f} km, 不应出现在近景"
    return None


_PLACE_NAME_RE = re.compile(r"[\u4e00-\u9fffA-Za-z\u3131-\u318e\uac00-\ud7af·-]{2,20}")


def _find_place_names(sent: str) -> list[str]:
    """在句子里找已知地名(places_patch 键)。整句正则匹配会把
    「埃菲尔铁塔就在你眼前」抓成单个假名, 所以这里反向匹配已知名。"""
    from nowhere import places
    try:
        patch_keys = list(places._patch().keys())
    except Exception:
        patch_keys = []
    names = []
    for name in patch_keys:
        if len(name) >= 2 and name in sent and name not in names:
            names.append(name)
    return names


# 环境描写关键词 → 需要的证据
_COAST_WORDS = ("海岸", "沙滩", "海滩", "海浪", "礁石", "海风", "浪花", "潮水", "咸味", "海滨")
_RIVER_WORDS = ("河流", "河水", "小溪", "溪水", "河面", "江面", "河岸", "溪流")
_LAKE_WORDS = ("湖面", "湖水", "湖畔", "湖边", "湖泊")
_CITY_WORDS = ("街道", "马路", "路灯", "霓虹", "车流", "楼群", "柏油路", "人行道")


def check_terrain_compat(sent: str, env: dict) -> str | None:
    """R4: 环境描写与地形相容(仅近景句式强校验)。"""
    if not _has_proximity(sent):
        return None
    lat = env.get("lat", 0.0)
    lon = env.get("lon", 0.0)
    surface = env.get("surface", "")
    water_features = env.get("water_features") or []

    if any(w in sent for w in _COAST_WORDS):
        from nowhere import coast
        if surface == "water_ocean":
            return None
        near = coast.nearest_ocean(lat, lon, max_km=30.0)
        if near is None:
            return "近景海岸描写但 30 km 内无海洋"
    if any(w in sent for w in _RIVER_WORDS):
        types = {f.get("type") for f in water_features if isinstance(f, dict)}
        if "river" not in types and "stream" not in types and surface not in ("water_fresh",):
            from nowhere import coast
            if coast.classify(lat, lon)["water"] != "lake":
                return "近景河流描写但无河流水文数据支持"
    if any(w in sent for w in _LAKE_WORDS):
        types = {f.get("type") for f in water_features if isinstance(f, dict)}
        if "lake" not in types and "reservoir" not in types and surface != "water_fresh":
            from nowhere import coast
            if coast.classify(lat, lon)["water"] != "lake":
                return "近景湖泊描写但无湖泊水文数据支持"
    if any(w in sent for w in _CITY_WORDS):
        from nowhere import terrain
        if surface != "urban" and not terrain.urban_nearby(lat, lon, km=15.0):
            return "近景城市描写但 15 km 内无城市"
    return None


# ── 主入口 ───────────────────────────────────────────────────────────

def validate(text: str, env: dict) -> tuple[str, list[dict]]:
    """校验并清洗场景文本。

    Returns ``(cleaned_text, report)``; report entries:
    ``{"rule": str, "sentence": str, "detail": str}`` for every replaced
    sentence. 文本无问题时不改变内容。
    """
    if not text:
        return text, []
    weather = env.get("weather") or {}
    sky = env.get("sky") or {}
    local_hour = env.get("local_hour")
    phase = sky.get("phase", "day")
    temp_c = weather.get("temp_c")
    temp_source = weather.get("source")

    from nowhere.describe import _split_sentences
    sentences = _split_sentences(text)
    report: list[dict] = []
    filler_idx = 0

    for i, sent in enumerate(sentences):
        stripped = sent.strip()
        if not stripped:
            continue
        detail = None
        rule = None
        detail = check_moment(sent, local_hour, phase)
        if detail:
            rule = "time"
        if detail is None:
            detail = check_temperature(sent, temp_c, temp_source)
            if detail:
                rule = "temperature"
        if detail is None:
            detail = check_place_distance(sent, env.get("lat", 0.0), env.get("lon", 0.0))
            if detail:
                rule = "place_distance"
        if detail is None:
            detail = check_terrain_compat(sent, env)
            if detail:
                rule = "terrain_compat"
        if detail is not None:
            sentences[i] = _SOFT_FILLERS[filler_idx % len(_SOFT_FILLERS)]
            filler_idx += 1
            report.append({"rule": rule or "unknown", "sentence": stripped, "detail": detail})

    if not report:
        return text, []
    result = "".join(sentences)
    if result and not result.endswith(("。", "！", "？", "」")):
        result += "。"
    return result, report


def observed_environment(env: dict, lat: float, lon: float) -> dict:
    """Build the ``observed_environment`` block for tool results (R2)."""
    from nowhere import coast
    near = coast.nearest_ocean(lat, lon, max_km=50.0)
    water = coast.classify(lat, lon)
    return {
        "position": {"lat": lat, "lon": lon},
        "surface": env.get("surface"),
        "elevation_m": env.get("elevation"),
        "weather": env.get("weather"),
        "sky": env.get("sky"),
        "water_features": env.get("water_features"),
        "coast": {
            "water_type": water.get("water"),
            "source": water.get("source"),
            "confidence": water.get("confidence"),
            "nearest_ocean": near,
        },
    }
