# -*- coding: utf-8 -*-
"""Add Jeju entities + aliases to places_patch.json (idempotent)."""
import json
import io
import pathlib

p = pathlib.Path(__file__).resolve().parent.parent / "nowhere" / "data" / "places_patch.json"
d = json.loads(p.read_text(encoding="utf-8"))

jeju = {
    "济州岛": {"lat": 33.36, "lon": 126.53, "type": "岛屿"},
    "Jeju Island": {"lat": 33.36, "lon": 126.53, "type": "岛屿"},
    "제주도": {"lat": 33.36, "lon": 126.53, "type": "岛屿"},
    "济州": {"lat": 33.4996, "lon": 126.5312, "type": "城市"},
    "济州市": {"lat": 33.4996, "lon": 126.5312, "type": "城市"},
    "Jeju City": {"lat": 33.4996, "lon": 126.5312, "type": "城市"},
    "제주시": {"lat": 33.4996, "lon": 126.5312, "type": "城市"},
    "西归浦": {"lat": 33.2528, "lon": 126.561, "type": "城市"},
    "西归浦市": {"lat": 33.2528, "lon": 126.561, "type": "城市"},
    "Seogwipo": {"lat": 33.2528, "lon": 126.561, "type": "城市"},
    "서귀포시": {"lat": 33.2528, "lon": 126.561, "type": "城市"},
    "汉拿山": {"lat": 33.3617, "lon": 126.5297, "type": "山"},
    "Hallasan": {"lat": 33.3617, "lon": 126.5297, "type": "山"},
    "한라산": {"lat": 33.3617, "lon": 126.5297, "type": "山"},
    "汉拿山国家公园": {"lat": 33.39, "lon": 126.53, "type": "保护区"},
}
d.update(jeju)
with io.open(p, "w", encoding="utf-8") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)
print("total entries:", len(d))
print("jeju keys present:", all(k in d for k in jeju))
