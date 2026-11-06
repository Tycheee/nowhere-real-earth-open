# -*- coding: utf-8 -*-
"""Verify the deployed streamable-HTTP MCP endpoint with a real MCP handshake."""
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


async def main():
    from fastmcp import Client
    async with Client("http://127.0.0.1:8097/mcp") as client:
        tools = await client.list_tools()
        names = sorted(t.name for t in tools)
        print("tool count:", len(names))
        print("tools:", ", ".join(names))
        # 用真实 MCP 调用验证济州岛流程
        res = await client.call_tool("open_door", {"to": "西归浦"})
        content = res.content
        text = next((c.text for c in content if getattr(c, "text", None)), "")
        print("\n-- open_door(西归浦) --")
        print(text[:400])
        res2 = await client.call_tool("walk", {"direction": "toward_sea", "distance_km": 2.0})
        text2 = next((c.text for c in res2.content if getattr(c, "text", None)), "")
        print("\n-- walk(toward_sea) --")
        print(text2[:400])


asyncio.run(main())
