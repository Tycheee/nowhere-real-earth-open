# 济州岛复现: 修复前 vs 修复后

基准: 上游 `f0803c1053e5f97a6f44bab26e15043d71825ec7`
探针脚本: `tools/repro_jeju.py`(修复前) / `tools/repro_jeju_after.py`(修复后)

## 1. 海洋距离判定

| 探针 | 修复前 | 修复后 |
|---|---|---|
| `nearest_ocean_km_and_bearing(33.2454, 126.5483, 50 km)` | `None`(1° 网格整格是森林) | **2.0 km @ 180°(南)**, source=landmask, confidence=1.0 |
| `_wide_coast_scan(33.2454, 126.5483)` | **1300.0 km @ 135°**(50 km 步长跳过近岸) | **2.0 km @ 180°**, source=landmask |

## 2. toward_sea 方向选择

| 探针 | 修复前 | 修复后 |
|---|---|---|
| 从济州岛中部(33.3803,126.5483) `toward_sea` | bearing=**0°(北)** — 20 km 内找不到海, 退化为海拔代理 | bearing=**180°(南)**, 南岸 17 km 处有海 |

## 3. 地名解析

| 查询 | 修复前 | 修复后 |
|---|---|---|
| `Seogwipo` | (33.2528, 126.561) 在线 Nominatim 命中 | (33.2528, 126.561) **离线** places_patch |
| `西归浦` | (33.4953, **132.3286**)(日本四国, 静默解析到错误国家) | (33.2528, 126.561) ✓ |
| `서귀포시` | (33.2528, 126.561) | (33.2528, 126.561) ✓ |
| `西归浦市` | 无法识别(后缀未剥离) | (33.2528, 126.561) ✓ |
| `walk_to("西归浦市")` | not_found | 15 km 旅程, 正常到达 |

## 4. 沿南向步行的地表序列

修复前后 1° 网格都把整段路径判为 `forest`; 差别在于**海洋感知**:

- 修复前: 任何方向 20 km 内找不到海 → 内陆森林叙事永无止境。
- 修复后: 南向 17 km 见海; `walk(toward_sea)` 一路向南,
  2 步后踏入海面(surface=water_ocean, 海拔 0 m, sea_ahead_km 逐步减小)。

## 5. 实时端到端输出(修复后实测)

```
open_door("西归浦")
  position: 33.2528, 126.561   weather: 24.2°C (source=openmeteo)
  coast: nearest_ocean 4 km @ 135°(东南)  source=landmask
  observed_environment + narrative_event 字段齐备

walk(toward_sea)  # 第 2 步
  位置 33.2221, 126.5762   surface=water_ocean  海拔 0 m
  "空气里有咸味了,海就在前面。"   sea_ahead_km=2.0

walk_to("汉拿山")  → 北边 14 公里, 正常到达
```

## 6. 修复前后完整探针输出

`tools/repro_jeju.py`(修复前, 在基准提交上运行):

```
Jeju mid (33.3803,126.5483): surface='forest' elev=300m
Jeju south (33.2454,126.5483): surface='forest' elev=300m
nearest_ocean_km_and_bearing(33.2454,126.5483, max50) = None km @ None deg
_wide_coast_scan(33.2454,126.5483) = 1300.0 km @ 135.0 deg
toward_sea pick from mid-island: bearing=0.0 (should be ~south)
geocode('Seogwipo') = (33.252845, 126.5610278)
geocode('西归浦') = (33.495271, 132.328557)      ← 日本
geocode('서귀포시') = (33.252845, 126.5610278)
Southward walk: 1..15 km 全程 'forest' elev=300
```

`tools/repro_jeju_after.py`(本版):

```
Jeju mid (33.3803,126.5483): surface='forest' elev=300m
Jeju south (33.2454,126.5483): surface='forest' elev=300m
nearest_ocean_km_and_bearing = 2.0 km @ 180.0 deg
nearest_ocean (rich) = {'km': 2.0, 'bearing': 180.0, 'source': 'landmask', 'confidence': 1.0}
_wide_coast_scan = {'km': 2.0, 'bearing': 180.0, 'source': 'landmask', 'confidence': 1.0}
toward_sea pick from mid-island: bearing=180.0 (want ~south/180)
geocode('Seogwipo') = (33.2528, 126.561)
geocode('西归浦') = (33.2528, 126.561)          ← 济州岛 ✓
geocode('서귀포시') = (33.2528, 126.561)
geocode('西归浦市') = (33.2528, 126.561)
water_ahead S from mid = {'km': 17.0, 'water': 'ocean', 'source': 'landmask', 'confidence': 1.0}
```
