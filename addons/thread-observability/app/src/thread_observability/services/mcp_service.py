"""MCP service process for add-on MCP-like endpoints."""

from __future__ import annotations

import os

import uvicorn

from thread_observability.api.mcp_tools import create_mcp_app
from thread_observability.logging_setup import configure_logging


def run_mcp_service() -> None:
    """Start MCP API service."""
    log_level = os.getenv("THREAD_OBS_LOG_LEVEL", "info")
    configure_logging("mcp", log_level)
    port = int(os.getenv("THREAD_OBS_MCP_PORT", "8100"))
    app = create_mcp_app()
    uvicorn.run(app, host="0.0.0.0", port=port, log_level=log_level)
