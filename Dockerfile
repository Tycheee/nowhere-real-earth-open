# nowhere-real-earth 容器镜像 (single-user server)
# 本地用法: docker compose up -d  → http://127.0.0.1:8097/mcp (仅本机可访问)
#
# 安全: 该服务无鉴权、全局单实例；镜像内监听容器网卡，但宿主只应发布到 loopback。
# 不要直接把本镜像暴露到公网; 远程部署必须置于 HTTPS 反向代理 +
# 访问控制(参见 INSTALL.md「安全与部署边界」)之后。
FROM python:3.12-slim

WORKDIR /srv/nowhere

# 完整拷贝源码与离线数据(含 landmask.npz)
COPY . .

RUN pip install --no-cache-dir .

ENV NOWHERE_HOME=/srv/nowhere/runtime \
    PYTHONUNBUFFERED=1

EXPOSE 8097

# 容器内监听所有接口，宿主侧仍由 Compose 只发布到 127.0.0.1。
# /mcp 与定位分享页同进程运行；公网入口必须由 HTTPS 反代控制。
CMD ["python", "-m", "nowhere.server", "--host", "0.0.0.0", "--http", "8097"]
