# -*- coding: utf-8 -*-
"""MCP 启动/握手 smoke test (P1-5)。

用法:
    python tools/mcp_handshake.py [url] [expected_tools]

默认连接 http://127.0.0.1:8097/mcp, 校验 tools/list 成功且工具数为 36
(与 @mcp.tool 注册数一致)。带重试等待服务器就绪。
"""
from __future__ import annotations

import asyncio
import sys

DEFAULT_URL = "http://127.0.0.1:8097/mcp"
EXPECTED_TOOLS = 36
RETRIES = 20
RETRY_DELAY = 1.5


async def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    expected = int(sys.argv[2]) if len(sys.argv) > 2 else EXPECTED_TOOLS
    from fastmcp import Client

    last_err: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            async with Client(url) as client:
                tools = await client.list_tools()
                names = sorted(t.name for t in tools)
                print(f"[mcp-handshake] tools/list OK: {len(names)} tools")
                print("[mcp-handshake] " + ", ".join(names))
                if len(names) != expected:
                    print(f"[mcp-handshake] FAIL: expected {expected} tools, got {len(names)}",
                          file=sys.stderr)
                    sys.exit(1)
                print(f"[mcp-handshake] PASS: {len(names)} tools")
                return
        except Exception as exc:  # 服务器未就绪 → 重试
            last_err = exc
            print(f"[mcp-handshake] attempt {attempt}/{RETRIES} failed: {exc!r}")
            await asyncio.sleep(RETRY_DELAY)
    print(f"[mcp-handshake] FAIL: could not connect: {last_err!r}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
