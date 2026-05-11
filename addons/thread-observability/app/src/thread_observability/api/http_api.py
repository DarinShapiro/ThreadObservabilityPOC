"""Core HTTP API for Thread Observability add-on."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI


def _utc_now() -> str:
    return datetime.now(tz=UTC).isoformat()


def create_core_app() -> FastAPI:
    """Create the core FastAPI application."""
    app = FastAPI(title="Thread Observability Core API", version="0.1.0")

    @app.get("/")
    def root() -> dict[str, str]:
        return {
            "service": "core",
            "name": "thread-observability",
            "version": "0.1.0",
        }

    @app.get("/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": "core",
            "checked_at": _utc_now(),
        }

    @app.get("/v1/health/snapshot")
    def health_snapshot() -> dict[str, object]:
        return {
            "snapshot_id": "scaffold-snapshot",
            "computed_at": _utc_now(),
            "data_age_seconds": 0,
            "summary": {
                "healthy_nodes": 0,
                "degraded_nodes": 0,
                "offline_nodes": 0,
            },
            "active_issues": [],
        }

    @app.get("/v1/issues/active")
    def list_active_issues() -> dict[str, object]:
        return {
            "count": 0,
            "issues": [],
            "computed_at": _utc_now(),
        }

    @app.get("/v1/topology")
    def topology_snapshot() -> dict[str, object]:
        return {
            "nodes": [],
            "links": [],
            "computed_at": _utc_now(),
        }

    return app
