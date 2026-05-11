"""MCP service process for add-on MCP-like endpoints."""

from __future__ import annotations

import os

import uvicorn

from thread_observability.api.mcp_tools import create_mcp_app


def run_mcp_service() -> None:
    """Start MCP API service."""
    port = int(os.getenv("THREAD_OBS_MCP_PORT", "8100"))
    app = create_mcp_app()
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
