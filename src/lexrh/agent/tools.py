"""How the agent reaches its tools: through the MCP server, never by importing the logic.

``InProcessTools`` calls the MCP server object directly (same tool layer, no process spawn:
used in tests and by default). ``StdioMcpTools`` starts the server as a subprocess and talks
the real protocol over stdio.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any, Protocol

from mcp.server.mcpserver import MCPServer


class Tools(Protocol):
    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


def _unwrap(structured: dict[str, Any] | None) -> dict[str, Any]:
    data = structured or {}
    inner = data.get("result", data)
    return inner if isinstance(inner, dict) else {"result": inner}


class InProcessTools:
    def __init__(self, server: MCPServer) -> None:
        self._server = server

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = asyncio.run(self._server.call_tool(name, arguments))
        return _unwrap(getattr(result, "structured_content", None))


class StdioMcpTools:
    """One subprocess per call: simple and isolated, slow for search_law (loads the reranker)."""

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return asyncio.run(self._call(name, arguments))

    @staticmethod
    async def _call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=sys.executable, args=["-m", "lexrh.mcp_server.server"]
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, arguments)
            if result.is_error:
                raise RuntimeError(f"tool {name} failed")
            return _unwrap(result.structured_content)
