"""MCP JSON-RPC 2.0 server + REST API for Thread Observability add-on."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

MCP_PROTOCOL_VERSION = "2024-11-05"
LOG_PATH = Path(os.getenv("THREAD_OBS_LOG_FILE", "/data/thread-observability/addon.log"))
LOG_TAIL_LINES = 200


def _utc_now() -> str:
    return datetime.now(tz=UTC).isoformat()


def _tail_log(n: int = LOG_TAIL_LINES) -> list[str]:
    """Return up to n lines from the tail of the add-on log file."""
    candidates = [
        LOG_PATH,
        Path("/run/uncaught-logs/current"),
    ]
    for path in candidates:
        if path.exists():
            try:
                lines = path.read_text(errors="replace").splitlines()
                return lines[-n:]
            except OSError:
                continue
    return ["[no log file found]"]


# ---------------------------------------------------------------------------
# REST tool registry (also used by MCP JSON-RPC handler)
# ---------------------------------------------------------------------------

class ToolCallRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "get_network_topology",
        "description": "Return current Thread network topology snapshot (nodes and links).",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_active_issues",
        "description": "Return all active Thread network issues detected by the reasoner.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_health_snapshot",
        "description": "Return current health snapshot including data freshness age.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_recent_logs",
        "description": "Return recent add-on log lines for live troubleshooting.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "lines": {
                    "type": "integer",
                    "description": "Number of log lines to return (default 100, max 200).",
                    "default": 100,
                }
            },
            "required": [],
        },
    },
]

_TOOL_MAP = {t["name"]: t for t in TOOL_DEFS}


def _dispatch_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Execute a tool and return its result payload."""
    if name == "get_network_topology":
        return {"nodes": [], "links": [], "note": "ingestion not yet implemented"}
    if name == "list_active_issues":
        return {"issues": [], "note": "reasoner not yet implemented"}
    if name == "get_health_snapshot":
        return {
            "status": "ok",
            "data_age_seconds": None,
            "note": "ingestion not yet implemented",
            "checked_at": _utc_now(),
        }
    if name == "get_recent_logs":
        n = min(int(arguments.get("lines", 100)), LOG_TAIL_LINES)
        lines = _tail_log(n)
        return {"lines": lines, "count": len(lines), "source": str(LOG_PATH)}
    raise ValueError(f"Unknown tool: {name}")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

def create_mcp_app() -> FastAPI:
    app = FastAPI(title="Thread Observability MCP", version="0.1.0")

    # ── simple REST convenience endpoints ────────────────────────────────────

    @app.get("/")
    def root() -> dict[str, str]:
        return {"service": "mcp", "name": "thread-observability", "version": "0.1.0"}

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "mcp", "checked_at": _utc_now()}

    @app.get("/mcp/tools")
    def list_tools_rest() -> dict[str, object]:
        return {"tools": TOOL_DEFS, "count": len(TOOL_DEFS)}

    @app.post("/mcp/call/{tool_name}")
    def call_tool_rest(tool_name: str, request: ToolCallRequest) -> dict[str, object]:
        if tool_name not in _TOOL_MAP:
            raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_name}")
        result = _dispatch_tool(tool_name, request.arguments)
        return {"tool": tool_name, "result": result, "called_at": _utc_now()}

    # ── MCP JSON-RPC 2.0 endpoint (VS Code MCP client) ───────────────────────

    @app.post("/mcp")
    async def mcp_jsonrpc(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}},
                status_code=400,
            )

        req_id = body.get("id")
        method = body.get("method", "")
        params = body.get("params", {})

        def ok(result: Any) -> JSONResponse:
            return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": result})

        def err(code: int, message: str) -> JSONResponse:
            return JSONResponse({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})

        if method == "initialize":
            return ok({
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "thread-observability", "version": "0.1.0"},
            })

        if method == "notifications/initialized":
            return JSONResponse({}, status_code=204)

        if method == "tools/list":
            return ok({"tools": TOOL_DEFS})

        if method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            if tool_name not in _TOOL_MAP:
                return err(-32602, f"Unknown tool: {tool_name}")
            result = _dispatch_tool(tool_name, arguments)
            return ok({"content": [{"type": "text", "text": str(result)}]})

        return err(-32601, f"Method not found: {method}")

    return app
