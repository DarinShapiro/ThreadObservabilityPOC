"""Core service process for add-on HTTP APIs."""

from __future__ import annotations

import os

import uvicorn

from thread_observability.api.http_api import create_core_app


def run_core_service() -> None:
    """Start core ingestion/enrichment/scheduler API service."""
    port = int(os.getenv("THREAD_OBS_CORE_PORT", "8099"))
    app = create_core_app()
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
