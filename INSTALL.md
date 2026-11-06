# nowhere-real-earth — 安装 / 启动 / MCP 接入

## 环境要求

- Python ≥ 3.11(在 3.13 上验证通过)
- 可选: Docker(见文末)

## 安装

```bash
cd nowhere-real-earth
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"     # Windows
# source .venv/bin/pip install -e ".[dev]"  # Linux/macOS
```

## 数据

- 基础海陆掩膜(`nowhere/data/landmask.npz`, **1/60°**, 约 1.85 km/格)已随仓库
  分发, 安装即用。
- 无参数重建可精确重现基础掩膜(逐位一致); 可选更高分辨率:

```bash
.venv\Scripts\pip install pillow pyshp      # 仅构建期依赖
python tools/build_landmask.py              # 重建 1/60° 基础 landmask.npz
python tools/build_landmask.py --res 120    # 1/120° → landmask_hi.npz, 自动优先加载
```

## 试玩方式

### 方式一: 命令行

```bash
python -m nowhere.playground
🌀 > open 济州岛
🌀 > walk toward_sea
🌀 > walkto 西归浦
```

### 方式二: MCP stdio(推荐, 与上游一致)

在 AI 客户端 MCP 配置中加入:

```json
{
  "mcpServers": {
    "nowhere-real-earth": {
      "command": "C:\\路径\\nowhere-real-earth\\.venv\\Scripts\\python",
      "args": ["-m", "nowhere.server", "--web"],
      "cwd": "C:\\路径\\nowhere-real-earth"
    }
  }
}
```

`--web` 会同时启动网页旁观者(随机或指定端口, URL 经 MCP 指令回传)。

### 方式三: Streamable-HTTP MCP(独立私有地址, 无需 stdio)

```bash
python -m nowhere.server --http 8097
```

同一个进程还提供同行定位所需的 `/share/{session}`、`/location-assets/*` 和
`/api/location/share/*`；不会挂载旁观者的世界状态修改接口。

客户端配置:

```json
{
  "mcpServers": {
    "nowhere-real-earth-http": {
      "url": "http://127.0.0.1:8097/mcp"
    }
  }
}
```

支持 streamable-http 的客户端(ZCode 等)可直接连。`--host 0.0.0.0`
可绑到局域网供其他机器访问——**不要这么做**, 见下方安全边界。

### 方式四: Docker

```bash
docker compose up -d        # streamable-HTTP MCP, 仅 127.0.0.1:8097/mcp
docker compose logs -f
```

容器内数据保存在 `./runtime`(gitignore 已排除)。

## 手机定位同行 + 高德

生产部署至少配置公开 HTTPS 地址；高德 key 可选，不配置时基础定位分享、轨迹和
`join_shared_location` 仍可用，只有地址/附近/路线降级：

```bash
export NOWHERE_SHARE_PUBLIC_URL="https://travel.example.com"
export NOWHERE_AMAP_KEY="你的高德 Web Service key"
python -m nowhere.server --host 127.0.0.1 --http 8097
```

1. MCP 调用 `start_location_share`。
2. 用手机打开返回的 `share_url`，主动按开始并允许定位。
3. MCP 可调用 `get_live_location`、`nearby_places`、
   `route_from_shared_location` 或 `join_shared_location`。
4. 手机页或 MCP 都能停止；`stop_location_share(..., erase_history=true)` 会同时擦除轨迹。

分享凭证只出现在 URL fragment，浏览器打开后立即清除；API 用 Bearer header 发送，
SQLite 仅保存 SHA-256 摘要。精确位置与乌有乡原有 `WorldState`/旅程存档彼此独立，
默认会话 4 小时、位置保留 24 小时，均可在建会话时缩短。

调用 `join_shared_location` 会明确将该位置作为乌有乡的新旅程起点，写入常规旅程
存档；删除定位历史不会删除这份旅程存档。保留清理在读取时和每 15 分钟执行。

济州岛等中国大陆外位置保持 WGS-84，不做 GCJ-02 偏移。高德 Web Service 在海外
可能不返回地址、POI 或路线，这是上游覆盖限制，工具会给出明确警告并保留原坐标。

## 安全与部署边界 (single-user server)

本服务是 **single-user server**: `_state` 为进程内全局单实例, 所有
调用方共享同一份世界状态和存档; 没有任何鉴权或多租户隔离。请把它
当作"你自己的单人游戏进程", 而不是多人共享服务。

- **默认只让宿主 loopback 可见** — CLI 默认 `127.0.0.1`；容器内监听
  `0.0.0.0` 以便端口转发，但 Compose 只映射 `127.0.0.1:8097:8097`。
  绑定非 loopback 时服务器会打印警告。
- **不要把无鉴权端点直接暴露到公网/局域网。** 任何远程部署必须同时满足:
  1. **HTTPS**(TLS 终结于可信反向代理, 如 Caddy/Nginx);
  2. **访问控制**, 任选其一:
     - 反向代理层 Basic Auth / **bearer token** 校验(Nginx `auth_request`
       或 Caddy `forward_auth`, 只在验证通过后转发 `/mcp`);
     - Cloudflare Access / Tailscale 等零信任隧道(推荐, 无需公网端口)。
  3. 不要提交任何真实 token/secret 到仓库; 用环境变量或 secrets manager 注入。
- 远程方案示例(示意, 非生产配置): Caddy 前置 `basic_auth` + 反向代理到
  容器内 `127.0.0.1:8097`, 由 Caddy 自动签发 TLS 证书。
- 手机分享只能从 HTTPS 页面取得 GPS。公开反代只应放行 `/share/*`、
  `/location-assets/*`、`/api/location/share/*`；`/mcp` 必须另加访问控制。
- 若需要多人游玩, 请为每个用户起独立进程 + 独立 `NOWHERE_HOME`,
  并在文档中明确不支持多租户。

## 环境变量

| 变量 | 用途 |
|---|---|
| `NOWHERE_HOME` | 存档/磁盘缓存目录, 默认 `~/.nowhere` |
| `NOWHERE_QWEATHER_KEY` | 可选, 和风天气 key(不填自动用 Open-Meteo→气候估算) |
| `NOWHERE_PUBLIC_URL` | `--web` 对外公布 URL 覆盖 |
| `NOWHERE_SHARE_PUBLIC_URL` | 手机定位分享的完整 HTTPS origin（优先于旧变量） |
| `NOWHERE_AMAP_KEY` | 可选，高德 Web Service key，只在服务端使用 |
| `NOWHERE_AMAP_BASE_URL` | 可选，高德兼容服务地址；远程地址必须 HTTPS |

## 验证安装

```bash
python -m pytest nowhere/tests/ -q
python tools/repro_jeju_after.py       # 济州岛修复后探针输出
```
