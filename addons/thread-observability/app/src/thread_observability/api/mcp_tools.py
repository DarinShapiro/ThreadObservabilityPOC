"""MCP JSON-RPC 2.0 server + REST API for Thread Observability add-on."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import supervisor_client

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
        "description": "Return recent add-on log lines from the add-on's internal file logger.",
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
    {
        "name": "ha_get_addon_state",
        "description": (
            "Return Supervisor's view of this add-on: install state, current version, "
            "latest available version, boot/watchdog flags, ingress URL, and raw info. "
            "Use this from VS Code to verify a deploy without opening the HA UI."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "ha_get_addon_logs",
        "description": (
            "Return the tail of the Supervisor container log for this add-on. "
            "Captures s6-overlay/startup output that the in-process Python logger misses. "
            "Use this to diagnose crash loops or boot failures."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "lines": {
                    "type": "integer",
                    "description": "Lines to return (default 200, max 1000).",
                    "default": 200,
                }
            },
            "required": [],
        },
    },
    {
        "name": "ha_get_supervisor_logs",
        "description": (
            "Return the tail of the Home Assistant Supervisor's own log. "
            "Useful for diagnosing why Supervisor rejected or killed the add-on "
            "(permissions, port conflicts, AppArmor, image pull failures)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "lines": {
                    "type": "integer",
                    "description": "Lines to return (default 200, max 1000).",
                    "default": 200,
                }
            },
            "required": [],
        },
    },
    {
        "name": "ha_restart_addon",
        "description": (
            "Ask Supervisor to restart this add-on (fast; no image rebuild). "
            "Use after config or option changes to verify behaviour without a full deploy."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "ha_rebuild_addon",
        "description": (
            "Ask Supervisor to rebuild this add-on from its repository source, then restart. "
            "Use after pushing a new commit so VS Code can complete the change\u2192deploy\u2192observe "
            "loop without manual uninstall/reinstall."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "ha_check_for_update",
        "description": (
            "Force Supervisor to re-scan add-on repositories, then report current vs "
            "latest version. Returns {current, latest, update_available, auto_update, state}. "
            "Use right after pushing a new version bump to avoid waiting for Supervisor's "
            "periodic poll."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "ha_update_addon",
        "description": (
            "Update this add-on to the latest version available in the store "
            "(equivalent to clicking 'Update' in the HA UI). Supervisor pulls the new "
            "image / rebuilds from source and restarts. Pair with ha_check_for_update first."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "ha_set_auto_update",
        "description": (
            "Enable or disable Supervisor's auto-update flag for this add-on."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean", "description": "True to enable, false to disable."}
            },
            "required": ["enabled"],
        },
    },
    {
        "name": "ha_reinstall_addon",
        "description": (
            "Uninstall then reinstall this add-on from the store. Destructive: clears the "
            "add-on container and terminates the MCP process making the call (the HTTP "
            "response will be cut short). Treat connection-reset as expected success and "
            "poll ha_get_addon_state afterwards."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
]

_TOOL_MAP = {t["name"]: t for t in TOOL_DEFS}


async def _dispatch_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
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

    # ---- Supervisor-backed dev-loop tools ---------------------------------
    if name == "ha_get_addon_state":
        try:
            return await supervisor_client.get_addon_info()
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc), "hint": "Supervisor unreachable; running outside HA?"}
    if name == "ha_get_addon_logs":
        n = max(1, min(int(arguments.get("lines", 200)), 1000))
        try:
            lines = await supervisor_client.get_addon_logs(n)
            return {"lines": lines, "count": len(lines), "source": "supervisor:/addons/self/logs"}
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}
    if name == "ha_get_supervisor_logs":
        n = max(1, min(int(arguments.get("lines", 200)), 1000))
        try:
            lines = await supervisor_client.get_supervisor_logs(n)
            return {"lines": lines, "count": len(lines), "source": "supervisor:/supervisor/logs"}
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}
    if name == "ha_restart_addon":
        try:
            res = await supervisor_client.restart_addon()
            return {"action": "restart", "result": res, "requested_at": _utc_now()}
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}
    if name == "ha_rebuild_addon":
        try:
            res = await supervisor_client.rebuild_addon()
            return {"action": "rebuild", "result": res, "requested_at": _utc_now()}
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}
    if name == "ha_check_for_update":
        try:
            return await supervisor_client.check_for_update()
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}
    if name == "ha_update_addon":
        try:
            res = await supervisor_client.update_addon()
            return {"action": "update", "result": res, "requested_at": _utc_now()}
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}
    if name == "ha_set_auto_update":
        enabled = bool(arguments.get("enabled", False))
        try:
            res = await supervisor_client.set_auto_update(enabled)
            return {"action": "set_auto_update", "enabled": enabled, "result": res}
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}
    if name == "ha_reinstall_addon":
        try:
            res = await supervisor_client.reinstall_addon("thread-observability")
            return {"action": "reinstall", "result": res, "requested_at": _utc_now()}
        except Exception as exc:  # noqa: BLE001
            # Connection reset mid-uninstall is the expected success path.
            return {"action": "reinstall", "note": "connection terminated (expected)",
                    "error": str(exc)}

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
    async def call_tool_rest(tool_name: str, request: ToolCallRequest) -> dict[str, object]:
        if tool_name not in _TOOL_MAP:
            raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_name}")
        result = await _dispatch_tool(tool_name, request.arguments)
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
            result = await _dispatch_tool(tool_name, arguments)
            import json as _json
            return ok({"content": [{"type": "text", "text": _json.dumps(result, default=str)}]})

        return err(-32601, f"Method not found: {method}")

    return app
