# nowhere-real-earth — 实现说明与架构选择 (v0.1)

基于上游 [yuyixuanfu/nowhere](https://github.com/yuyixuanfu/nowhere)
基准提交 `f0803c1053e5f97a6f44bab26e15043d71825ec7`(CC BY-NC 4.0, 作者: 旋复)
的增强版。目标是修复济州岛实测暴露的四类问题, 并保持全球扩展能力。

## 修改总览

| 模块 | 文件 | 改动 |
|---|---|---|
| 海陆掩膜 | `nowhere/coast.py`(新) | Natural Earth 10m 位压缩掩膜, land/ocean/lake 分类, 带 `source`/`confidence` |
| 掩膜构建 | `tools/build_landmask.py`(新) | 可重复下载+栅格化脚本; 基础 1/60° 掩膜已随仓库提交(1.4 MB) |
| 海陆数据 | `nowhere/data/landmask.npz`(新) | 已提交的 1/60° 掩膜 |
| 地表判定 | `nowhere/terrain.py` | `surface()`/`elevation()` 在池数据之后、瓦片/网格之前优先用掩膜判水; 海洋格海拔=0 |
| 走路物理 | `nowhere/walk.py` | `water_ahead_km`/`nearest_ocean_km_and_bearing` 改为掩膜优先; 新增带来源的 `water_ahead()`/`nearest_ocean()`; 保留旧签名兼容 |
| 服务器 | `nowhere/server.py` | `_wide_coast_scan` 自适应步长重写; toward_sea 预检带 `coast_source`; wait 温度叙事加来源护栏; `walk_to`/`open_door` 接入在线 geocoder 与歧义提示; 输出新增 `observed_environment`/`narrative_event`; 新增 `--http` 传输 |
| 地名解析 | `nowhere/geocode.py` | Nominatim 磁盘缓存(30 天 TTL)、1.2 s 限流、limit=5 打分、歧义防护(跨国家近分 → 明确失败+候选)、OpenCC 繁→简、`lookup_rich()` |
| 地名数据 | `nowhere/data/places_patch.json` | 济州岛/济州市/西归浦/汉拿山 三语别名共 15 条 |
| 地名查找 | `nowhere/places.py` | 后缀剥离表加"市" |
| 叙事校验 | `nowhere/facts.py`(新) | 时刻/温度/地名距离/地形相容 4 条规则, 按句替换为通用描写 |
| 天气渲染 | `nowhere/describe.py` | 温差叙事仅限同源在线数据 |
| 测试 | `nowhere/tests/`(新) | 50 个用例, 含济州岛全部验收项 |
| 部署 | `Dockerfile`、`docker-compose.yml`、`INSTALL.md` | 容器化与 MCP 接入文档 |

## 关键架构决策

### 1. 高精度海陆判断: 位压缩栅格掩膜, 而非运行时矢量查询

- **数据源**: Natural Earth 10m(公共领域, 无许可负担)。GSHHG 更精细但
  体积大一个量级; OSM 陆地数据是 ODbL, 引入许可复杂度。
- **存储**: 1/60° 格心采样的位压缩 `npz`, 仅 1.4 MB, 单点查询 O(1),
  无 shapely 运行时依赖; 1/120° 可用脚本自建(`landmask_hi.npz` 自动优先)。
- **语义**: `ocean = ~land & ~lakes` — 内陆大湖不会被 toward_sea 当成海;
  `include_fresh` 挡水闸门 = 掩膜湖泊 ∪ 网格淡水。
- **降级链**: landmask(confidence=1.0) → grid_tiny(confidence=0.5)。
  所有查询结果携带 `source`, 测试和调用方都能判断是否走了兜底。
- **原网格保留**为最终 fallback, 未删除任何旧功能。

### 2. 地名解析: 本地优先 + 磁盘缓存 + 歧义防护

- 链: special_places → places.db/patch → GeoNames 离线包 → **磁盘缓存** →
  Nominatim(limit=5, ≥1.2 s 间隔, 5 s 超时)。
- "西归浦"基线 bug 的根因: Nominatim `limit=1` 静默取第一名(日本四国)。
  现在取 5 个候选打分(名称精确匹配/行政类型/importance), 并给与
  `near` 同国家的候选 +2; 若最优两名跨国家且分数差 <0.5 → 判歧义,
  返回候选列表、明确失败, 绝不静默选错国家。
- 在线服务只是增强, 不是依赖: 断网时本地补丁与磁盘缓存仍然可用,
  每一步移动(walk/walk_to)都不会因为 geocoder 挂掉而失败。

### 3. 叙事事实校验: 按句替换, 而非整段重写

- `facts.validate()` 在 open_door/walk/wait 文本合成后运行:
  - R1 时刻: 句中钟点须符合当地时间(±3 h)且与时刻词一致;
    傍晚六点=18:00 的十二小时制换算已处理。
  - R2 温度: 具体数字须与当前天气一致(±3°C); 气候估算来源不允许数字;
    水温/坡度度数不误伤。
  - R3 地名: 近景句式("眼前/脚下/就在")中的已知地名超过 30 km → 替换。
  - R4 地形: 海岸/河流/湖泊/城市描写的近景句式与地形证据不符 → 替换。
- 失败按**句**替换为不含具体事实的通用描写(复用上游软填充风格),
  保留随机偶遇与文学性; 每次校验产出报告。
- 结果结构区分 `observed_environment`(地理事实: 坐标/地表/天气/天空/
  海岸来源)与 `narrative_event`(校验后的文学生成 + 违例计数)。
- wait 温度叙事单独加护栏: 数字差值只允许"同源在线数据"; 跨源(
  实时 21.7°C 在线 vs 气候估算 6°C)一律改用无数字体感句 — 这正是
  实测"气温降到 6°C"的直接来源。

### 4. 兼容性取舍

- `water_ahead_km`、`nearest_ocean_km_and_bearing` 签名与返回值不变,
  新能力放在 `water_ahead`/`nearest_ocean`(dict) 里。
- 28 个 MCP 工具全部保留, 存档格式未动, `NOWHERE_HOME`/`NOWHERE_QWEATHER_KEY`
  等环境变量语义不变。
- 新增依赖: **零个运行时依赖**(coast/facts 只用 numpy, 构建脚本的
  pillow/pyshp 不进运行时)。

## 测试

```bash
python -m pytest nowhere/tests/ -q        # 50 passed
```

覆盖济州岛验收项 1-7(见 `nowhere/tests/`):
1. 南端 2 km 内识别海洋, 绝不 1300 km
2. toward_sea 指向南方海岸
3. 三语"西归浦"解析一致
4. 21.7°C 天气不得出现 6°C 文本
5. 傍晚不得出现"清晨六点"
6. 远地标不得写成眼前景物
7. 离线/在线失败仍可运行, fallback 带来源标记
