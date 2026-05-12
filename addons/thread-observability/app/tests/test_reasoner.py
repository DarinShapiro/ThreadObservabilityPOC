"""Tests for the deterministic anomaly reasoner."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from thread_observability.pipeline.reasoner import (
    ATTACH_FAIL_THRESHOLD,
    OFFLINE_THRESHOLD_MIN,
    PARENT_CHURN_THRESHOLD,
    run_reasoner,
)
from thread_observability.storage.sqlite_store import SQLiteStore


def _now() -> datetime:
    return datetime.now(tz=UTC)


def test_reasoner_no_events(store: SQLiteStore) -> None:
    out = run_reasoner(store=store)
    assert out["opened"] == []
    assert out["closed"] == []
    assert store.list_active_issues() == []


def test_parent_churn_opens_issue(store: SQLiteStore) -> None:
    eui = "11" * 8
    for i in range(PARENT_CHURN_THRESHOLD):
        store.insert_event(
            eui64=eui,
            type="parent_change",
            ts=(_now() - timedelta(minutes=i)).isoformat(),
            parent_eui64="aa" * 8,
        )
    out = run_reasoner(store=store)
    assert len(out["opened"]) == 1
    issues = store.list_active_issues()
    assert len(issues) == 1
    assert issues[0]["kind"] == "parent_churn"
    assert issues[0]["eui64"] == eui
    assert issues[0]["evidence"]["count"] == PARENT_CHURN_THRESHOLD


def test_parent_churn_dedup(store: SQLiteStore) -> None:
    eui = "11" * 8
    for i in range(PARENT_CHURN_THRESHOLD + 1):
        store.insert_event(eui64=eui, type="parent_change",
                           ts=(_now() - timedelta(minutes=i)).isoformat(),
                           parent_eui64="aa" * 8)
    run_reasoner(store=store)
    second = run_reasoner(store=store)
    assert second["opened"] == []
    assert len(second["still_open"]) == 1
    assert len(store.list_active_issues()) == 1


def test_attach_failures_open_and_close(store: SQLiteStore) -> None:
    eui = "22" * 8
    for i in range(ATTACH_FAIL_THRESHOLD):
        store.insert_event(eui64=eui, type="attach_failed",
                           ts=(_now() - timedelta(minutes=i)).isoformat())
    first = run_reasoner(store=store)
    assert len(first["opened"]) == 1
    issue_id = first["opened"][0]

    # Advance time so the failure events fall outside the window;
    # rerun and confirm the attach_failures issue auto-closes. (The node
    # will also flip to offline in that future, which is correct behaviour.)
    far_future = _now() + timedelta(hours=2)
    closed = run_reasoner(store=store, now=far_future)
    assert issue_id in closed["closed"]
    still_open = store.list_active_issues()
    assert all(i["kind"] != "attach_failures" for i in still_open)


def test_offline_node_opens_crit_issue(store: SQLiteStore) -> None:
    eui = "33" * 8
    old = (_now() - timedelta(minutes=OFFLINE_THRESHOLD_MIN + 5)).isoformat()
    # Registry-first (v9): event ingestion no longer auto-creates node
    # rows. Seed the node first so insert_event can UPDATE its last_seen.
    store.upsert_node_metadata(eui64=eui)
    store.insert_event(eui64=eui, type="attach", ts=old)
    out = run_reasoner(store=store)
    assert len(out["opened"]) == 1
    issues = store.list_active_issues()
    assert issues[0]["kind"] == "offline_node"
    assert issues[0]["severity"] == "crit"
