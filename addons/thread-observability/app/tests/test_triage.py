"""Phase 3 (0.9.57) triage tools."""

from __future__ import annotations

import asyncio
from typing import Any

from thread_observability.api import triage
from thread_observability.api import supervisor_client


# --- get_pipeline_health -----------------------------------------------------

def test_get_pipeline_health_empty_store(store):  # noqa: ARG001
    out = triage.get_pipeline_health(limit=10)
    assert out["recent_ticks"] == []
    assert out["summary"]["recent_tick_count"] == 0
    assert out["summary"]["consecutive_failed_ticks"] == 0
    assert out["summary"]["stages_currently_failing"] == []


def test_get_pipeline_health_returns_recent_ticks(store):
    # Two healthy ticks, then a failing one (newest), then another healthy.
    store.record_pipeline_tick({
        "started_at": "2025-01-01T00:00:00+00:00",
        "finished_at": "2025-01-01T00:00:01+00:00",
        "duration_seconds": 1.0,
        "stages": {"network_data": {"ok": True}, "diagnostics": {"ok": True}},
    })
    store.record_pipeline_tick({
        "started_at": "2025-01-01T00:01:00+00:00",
        "finished_at": "2025-01-01T00:01:02+00:00",
        "duration_seconds": 2.0,
        "stages": {"network_data": {"ok": True}, "diagnostics": {"ok": False, "error": "x"}},
        "error": "stages failed: diagnostics",
    })
    out = triage.get_pipeline_health(limit=10)
    assert out["summary"]["recent_tick_count"] == 2
    # Latest tick (newest first) is the failing one.
    assert out["summary"]["consecutive_failed_ticks"] == 1
    assert "diagnostics" in out["summary"]["stages_currently_failing"]
    assert out["summary"]["avg_duration_seconds"] == 1.5


# --- get_environment ---------------------------------------------------------

class _AsyncReturn:
    """Sentinel: when called like a coro, returns the wrapped value."""

    def __init__(self, value: Any):
        self.value = value

    async def __call__(self, *a, **kw):  # noqa: ARG002
        return self.value


def _patch_supervisor(monkeypatch, *, core=None, sup=None, addons=None):
    monkeypatch.setattr(
        supervisor_client, "get_core_info",
        _AsyncReturn(core or {"version": "2026.5.1", "state": "running", "arch": "aarch64"}),
    )
    monkeypatch.setattr(
        supervisor_client, "get_supervisor_info",
        _AsyncReturn(sup or {"version": "2025.10.1", "arch": "aarch64", "channel": "stable", "timezone": "UTC"}),
    )
    monkeypatch.setattr(
        supervisor_client, "_get_json",
        _AsyncReturn({"addons": addons or [
            {"slug": "core_openthread_border_router", "name": "OpenThread Border Router", "version": "1.2.3", "state": "started"},
            {"slug": "core_matter_server", "name": "Matter Server", "version": "5.6.7", "state": "started"},
        ]}),
    )


def test_get_environment_blocks_present(store, monkeypatch):  # noqa: ARG001
    _patch_supervisor(monkeypatch)
    env = asyncio.run(triage.get_environment(addon_version="0.9.57"))
    assert env["addon"]["version"] == "0.9.57"
    assert env["addon"]["schema_version"] >= 18
    assert env["addon"]["mcp_protocol_version"] == "2024-11-05"
    assert env["home_assistant"]["core_version"] == "2026.5.1"
    assert env["home_assistant"]["supervisor_version"] == "2025.10.1"
    assert env["otbr"]["slug"] == "core_openthread_border_router"
    assert env["matter_server"]["slug"] == "core_matter_server"
    assert "network" in env
    assert "pipeline" in env


def test_get_environment_handles_supervisor_failure(store, monkeypatch):  # noqa: ARG001
    async def boom(*a, **kw):  # noqa: ARG001
        raise RuntimeError("supervisor offline")
    monkeypatch.setattr(supervisor_client, "get_core_info", boom)
    monkeypatch.setattr(supervisor_client, "get_supervisor_info", boom)
    monkeypatch.setattr(supervisor_client, "_get_json", boom)
    env = asyncio.run(triage.get_environment(addon_version="0.9.57"))
    # The HA section keys exist but versions are None when calls fail.
    assert env["home_assistant"]["core_version"] is None
    assert "error" in env["otbr"]
    assert "error" in env["matter_server"]


# --- start_triage ------------------------------------------------------------

def test_start_triage_recommends_analyze_node_on_open_issue(store, monkeypatch):
    _patch_supervisor(monkeypatch)
    store.open_issue(kind="wrong_network", severity="warn", eui64="AABBCCDDEEFF0011")
    out = asyncio.run(triage.start_triage(addon_version="0.9.57"))
    assert out["active_issues_count"] == 1
    first = out["recommended_next"][0]
    assert first["tool"] == "analyze_node"
    assert first["arguments"]["eui64"] == "AABBCCDDEEFF0011"
    assert "wrong_network" in first["reason"]


def test_start_triage_empty_recommended_when_clean(store, monkeypatch):  # noqa: ARG001
    _patch_supervisor(monkeypatch)
    out = asyncio.run(triage.start_triage(addon_version="0.9.57"))
    assert out["active_issues_count"] == 0
    # No issues, no stale pipeline → fallback is get_mesh_state suggestion.
    assert len(out["recommended_next"]) == 1
    assert out["recommended_next"][0]["tool"] == "get_mesh_state"


# --- mcp_tools catalog -------------------------------------------------------

def test_phase3_tools_registered():
    from thread_observability.api import mcp_tools

    names = {t["name"] for t in mcp_tools.TOOL_DEFS}
    assert {"start_triage", "get_environment", "get_pipeline_health"}.issubset(names)
    # All three are read tools (envelope-wrapped).
    assert {"start_triage", "get_environment", "get_pipeline_health"}.issubset(mcp_tools._READ_TOOLS)
