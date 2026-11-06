# nowhere-real-earth v0.1 — 交付文档

> 交付日期: 2026-10-18
> 上游: [yuyixuanfu/nowhere](https://github.com/yuyixuanfu/nowhere) @ `f0803c1053e5f97a6f44bab26e15043d71825ec7`
> 许可: CC BY-NC 4.0（署名: 旋复 + Portal 项目链接，非商用；本分支为演绎作品，已标注修改）
> 状态: ✅ v0.1 完成；✅ R3 稳定性收尾完成（76 个自动化测试全部通过，私有 MCP 已部署可试玩）

---

## R3 稳定性收尾（本页最新）

基线: 私有仓库 `Tycheee/nowhere-real-earth` @ `4469ae8`。修复审查发现的问题:

| 问题 | 根因 | 修复 |
|---|---|---|
| 掩膜缺失时递归 | `coast.classify`(无掩膜)→`_grid_cover`→`terrain.surface`→`coast.classify`, 靠 RecursionError 兜底 | `terrain` 抽出 `_surface_no_coast`/`_elevation_no_coast` primitive(完全不调 coast), coast 的网格兜底与 walk 的 Card 64 闸门全部改走 primitive |
| geocode 缓存忽略位置 | memory/disk/negative 以 normalized name 为 key, 同名地名跨国家互污染 | 三级缓存键统一为 `name + near_cc`; 磁盘缓存升级 v2(旧条目整体失效); `near_cc` 在缓存查询前计算 |
| near_cc 依赖可选资源包 | `country_code_of` 依赖 gitignored 的 cities15000, 缺失时恒为 None → 上下文功能静默失效 | 新增 Natural Earth 10m admin_0 国家网格兜底(`country_grid.npz`, 10 KB, 公共领域, 8× 超采样多数表决), `tools/build_country_grid.py` 可重建 |
| landmask 分辨率表述不一 | 文档称 1/60°, 脚本默认 `--res 30` 且输出命名规则冲突 | 脚本默认 60, 无参数重建与提交的 `landmask.npz` 逐位一致(测试校验); `--res 120` 才生成 `landmask_hi.npz`; 全部文档同步 |
| wheel 缺数据文件 | package-data 漏 `content.db`(运行时读取) | 打包规则补 `content.db`; wheel smoke 从站点包外目录验证济州岛流程 |
| 无 CI | — | `.github/workflows/ci.yml`: 3.11/3.12 pytest + wheel 构建安装 smoke + Docker build + MCP 握手(28 tools), 全部不依赖外网服务 |
| 文档 28/30 不一致 | 上游注释写 30 | 全部统一为实测的 **28** |
| 网络暴露 | Docker `0.0.0.0` 无鉴权 | 默认 loopback + Compose `127.0.0.1:8097:8097` + 非本机绑定警告 + single-user 标注 + 远程部署安全方案文档 |

## 1. 一句话说明

这是"乌有乡"的增强分支：修复济州岛实测发现的四类问题（1° 网格海陆误判、地名解析到错误国家、叙事与实时天气/时间冲突、济州岛数据缺失），保持全球扩展能力与上游玩法/存档/MCP 接口完全兼容。

## 2. 验收结果（全部通过）

| # | 验收项 | 修复前 | 修复后 | 测试位置 |
|---|---|---|---|---|
| 1 | (33.2454, 126.5483) 识别海洋 | `None` / 宽扫描 **1300 km** | **2.0 km @ 正南**（landmask, confidence 1.0） | `test_coast.py` |
| 2 | 岛中部 `toward_sea` 朝南 | 朝北（0°） | 朝南（180°） | `test_coast.py` / `test_jeju_regression.py` |
| 3 | 三语"西归浦"一致 | 「西归浦」→ 日本四国 | Seogwipo/西归浦/서귀포시/西归浦市 全部一致 | `test_geocode.py` |
| 4 | 21.7°C 不出现 6°C | "气温降到 6°C" | 跨源/估算温度不给数字 | `test_facts.py` |
| 5 | 傍晚不出现"清晨六点" | 出现 | 按句替换为通用描写 | `test_facts.py` |
| 6 | 远地标不进近景 | 出现 | ≥30 km 的近景地名句被替换 | `test_facts.py` |
| 7 | 离线/在线失败可运行 | — | 全程降级链 + `source`/`confidence` 标记 | `test_geocode.py` / `test_coast.py` |

**测试命令与结果**：

```bash
python -m pytest nowhere/tests/ -q
# 76 passed (R3: 含递归回归/缓存上下文/分辨率/重建可复现/国家网格等新增用例)
```

## 3. 现在就可以试玩（已部署）

私有 MCP 已在本机运行并完成真实握手验证（28 个工具全部可用）：

| 项 | 值 |
|---|---|
| Streamable-HTTP MCP 地址 | `http://127.0.0.1:8097/mcp` |
| 客户端配置 | `{"mcpServers": {"nowhere-real-earth-http": {"url": "http://127.0.0.1:8097/mcp"}}}` |
| 存档隔离目录 | `nowhere-real-earth\runtime`（不影响你其他实例） |
| 重启命令 | `cd nowhere-real-earth && .venv\Scripts\python -m nowhere.server --http 8097` |

已实测可玩流程：`open_door("西归浦")` → 真实天气 24.2°C（openmeteo 源）→ `walk("toward_sea")` 两步入海（surface=water_ocean，海拔 0）→ `walk_to("汉拿山")` 到达。命令行试玩 `python -m nowhere.playground` 同样验证通过。

## 4. 核心改动

| 模块 | 说明 |
|---|---|
| `nowhere/coast.py`（新） | Natural Earth 10m 海陆/湖泊位压缩掩膜（1/60°，1.4 MB 已随仓库），land/ocean/lake 三类，全部查询带 `source`+`confidence`；1° 网格为最终兜底（`grid1`） |
| `nowhere/data/landmask.npz`（新） | 已提交的掩膜数据；`tools/build_landmask.py` 可重建/升分辨率（1/120°） |
| `nowhere/terrain.py` | `surface()` 掩膜优先判水；海洋格海拔=0 |
| `nowhere/walk.py` | `water_ahead_km`/`nearest_ocean_km_and_bearing` 掩膜优先（旧签名不变）；新增 `water_ahead()`/`nearest_ocean()` 带来源 rich API |
| `nowhere/server.py` | `_wide_coast_scan` 自适应步长重写；toward_sea 预检带 `coast_source`；wait 温度叙事加来源护栏；`walk_to`/`open_door` 接入在线 geocoder+歧义提示；输出新增 `observed_environment`/`narrative_event`；新增 `--http` 传输 |
| `nowhere/geocode.py` | 磁盘缓存（30 天 TTL）、Nominatim ≤1 req/s 限流、limit=5 打分、跨国家歧义防护（返回候选而非静默选错）、OpenCC 繁→简、`lookup_rich()` |
| `nowhere/facts.py`（新） | 叙事事实校验 4 条规则（时刻/温度/地名距离/地形相容），按句替换、带报告 |
| `nowhere/describe.py` | 温差叙事仅限同源在线数据 |
| `nowhere/data/places_patch.json` | 济州岛/济州市/西归浦/汉拿山 三语别名 15 条 |
| `nowhere/places.py` | 后缀剥离支持"市" |

**依赖变化**：运行时零新增依赖（只用已有 numpy）；pillow/pyshp 仅为掩膜构建脚本的可选依赖。28 个 MCP 工具、存档格式、`NOWHERE_HOME`/`NOWHERE_QWEATHER_KEY` 等全部保持兼容。

## 5. 交付文件清单

| 文件 | 内容 |
|---|---|
| `README.md` | 上游说明 + 本分支说明与署名 |
| `DELIVERY.md` | 本文档 |
| `IMPLEMENTATION.md` | 修改说明与架构决策 |
| `INSTALL.md` | 安装 / 启动 / MCP 接入（stdio、streamable-HTTP、Docker 三种方式） |
| `KNOWN_LIMITATIONS.md` | 已知限制（掩膜精度、地名解析边界、校验器范围等） |
| `docs/jeju_before_after.md` | 济州岛修复前后完整对比输出 |
| `Dockerfile` / `docker-compose.yml` / `.dockerignore` | 容器化部署（`docker compose up -d`） |
| `nowhere/tests/` | 76 个自动化测试（coast/facts/geocode/jeju_regression/country_grid/landmask_rebuild/wheel_smoke） |
| `tools/build_landmask.py` | 掩膜可重复构建脚本 |
| `tools/repro_jeju.py` / `tools/repro_jeju_after.py` | 修复前/后探针脚本 |
| `nowhere/data/landmask_README.md` | 掩膜数据来源与许可说明 |

## 6. 已知限制（摘要）

- 里海按 Natural Earth 官方分类为 sea（非 lake）；<2 km 岛礁在 1/60° 掩膜不可见（可建 1/120°）。
- 在线 geocoder 断网时，冷僻地名可能解析失败（明确失败，不猜）；限流 1.2 s/次。
- 叙事校验器只强校验近景句式与已知地名，保留远景文学自由。
- 上游遗留：无瓦片/无 DEM 包时内陆海拔为网格填充值（~300 m），海洋格海拔已修正为 0。

详见 `KNOWN_LIMITATIONS.md`。

## 7. 备注

- 本机无 git，未执行分支提交；项目为独立目录，可直接推送到新 fork（未覆盖上游仓库）。
- Docker 配置已提供但未在本机运行（本机 WSL mirrored 网络下 Docker bridge 会破坏现有代理，工作区硬约束）；在其他机器可直接 `docker compose up -d`。
