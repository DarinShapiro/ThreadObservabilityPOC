"""Tests for the health snapshot builder."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from thread_observability.health import build_health_snapshot
from thread_observability.storage.sqlite_store import SQLiteStore


def test_health_empty_store(store: SQLiteStore) -> None:
    snap = build_health_snapshot(store=store)
    assert snap["status"] == "ok"
    assert snap["summary"]["total_nodes"] == 0
    assert snap["active_issues"]["count"] == 0
    assert snap["data_age_seconds"] is None


def test_health_classifies_nodes(store: SQLiteStore) -> None:
    now = datetime.now(tz=UTC)
    store.insert_event(eui64="aa" * 8, type="attach", ts=now.isoformat())
    store.insert_event(eui64="bb" * 8, type="attach",
                       ts=(now - timedelta(minutes=10)).isoformat())
    store.insert_event(eui64="cc" * 8, type="attach",
                       ts=(now - timedelta(hours=2)).isoformat())
    snap = build_health_snapshot(store=store)
    s = snap["summary"]
    assert s["healthy_nodes"] == 1
    assert s["stale_nodes"] == 1
    assert s["offline_nodes"] == 1
    assert s["total_nodes"] == 3


def test_health_reflects_critical_issues(store: SQLiteStore) -> None:
    store.open_issue(kind="offline_node", severity="crit", eui64="aa" * 8)
    snap = build_health_snapshot(store=store)
    assert snap["status"] == "critical"
    assert snap["active_issues"]["by_severity"]["crit"] == 1
