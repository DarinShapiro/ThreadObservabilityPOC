"""Minimal MCP-like API surface for the scaffold."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


def _utc_now() -> str:
    return datetime.now(tz=UTC).isoformat()


class ToolCallRequest(BaseModel):
    """Tool call payload."""

    arguments: dict[str, Any] = Field(default_factory=dict)


TOOLS = [
	{
		"name": "get_network_topology",
		"description": "Return current topology snapshot.",
	},
	{
		"name": "list_active_issues",
		"description": "Return active issue list.",
	},
	{
		"name": "get_health_snapshot",
		"description": "Return current health snapshot.",
	},
]


def create_mcp_app() -> FastAPI:
    """Create a minimal MCP-style FastAPI app for tool listing/calling."""
    app = FastAPI(title="Thread Observability MCP API", version="0.1.0")

    @app.get("/")
    def root() -> dict[str, str]:
        return {
            "service": "mcp",
            "name": "thread-observability",
            "version": "0.1.0",
        }

    @app.get("/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": "mcp",
            "checked_at": _utc_now(),
        }

    @app.get("/mcp/tools")
    def list_tools() -> dict[str, object]:
        return {"tools": TOOLS, "count": len(TOOLS)}

    @app.post("/mcp/call/{tool_name}")
    def call_tool(tool_name: str, request: ToolCallRequest) -> dict[str, object]:
        known = {tool["name"] for tool in TOOLS}
        if tool_name not in known:
            raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_name}")

        return {
            "tool": tool_name,
            "arguments": request.arguments,
            "result": {"status": "not_implemented", "reason": "scaffold"},
            "called_at": _utc_now(),
        }

    return app
