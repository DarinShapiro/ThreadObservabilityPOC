"""Tests for the SQLite store (schema, events, issues)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from thread_observability.storage.sqlite_store import SQLiteStore


def test_migrations_apply(store: SQLiteStore) -> None:
    assert store.schema_version == 5
    stats = store.stats()
    assert stats["schema_version"] == 5
    assert stats["row_counts"]["events"] == 0


def test_insert_event_creates_node(store: SQLiteStore) -> None:
    eid = store.insert_event(eui64="aa" * 8, type="attach", rssi=-60, lqi=200)
    assert eid >= 1
    node = store.get_node("aa" * 8)
    assert node is not None
    assert node["last_seen"]


def test_query_events_filters(store: SQLiteStore) -> None:
    store.insert_event(eui64="11" * 8, type="attach")
    store.insert_event(eui64="22" * 8, type="attach_failed")
    store.insert_event(eui64="11" * 8, type="parent_change", parent_eui64="22" * 8)

    by_node = store.query_events(eui64="11" * 8)
    assert {e["type"] for e in by_node} == {"attach", "parent_change"}

    by_type = store.query_events(event_type="attach_failed")
    assert len(by_type) == 1 and by_type[0]["eui64"] == "22" * 8


def test_issue_dedupe_and_close(store: SQLiteStore) -> None:
    first = store.open_issue(kind="parent_churn", severity="warn", eui64="11" * 8,
                             evidence={"count": 3})
    second = store.open_issue(kind="parent_churn", severity="warn", eui64="11" * 8,
                              evidence={"count": 5})
    assert first == second, "dedupe should return same id"

    active = store.list_active_issues()
    assert len(active) == 1
    assert active[0]["evidence"]["count"] == 5

    assert store.close_issue(first) is True
    assert store.list_active_issues() == []
    assert store.close_issue(first) is False, "double-close is a no-op"


def test_query_events_since(store: SQLiteStore) -> None:
    old = (datetime.now(tz=UTC) - timedelta(hours=2)).isoformat()
    new = datetime.now(tz=UTC).isoformat()
    store.insert_event(eui64="11" * 8, type="attach", ts=old)
    store.insert_event(eui64="11" * 8, type="attach", ts=new)

    recent = store.query_events(since=(datetime.now(tz=UTC) - timedelta(hours=1)).isoformat())
    assert len(recent) == 1
    assert recent[0]["ts"] == new


def test_links_replace_and_list(store: SQLiteStore) -> None:
    A = "aa" * 8
    B = "bb" * 8
    C = "cc" * 8
    n = store.replace_links_for_reporter(A, "neighbor_table", [
        {"neighbor_eui64": B, "rssi_avg": -50},
        {"neighbor_eui64": C, "rssi_avg": -60, "is_child": 1},
    ])
    assert n == 2
    rows = store.list_links()
    assert len(rows) == 2
    # Replace overwrites prior entries for the same (reporter, source).
    store.replace_links_for_reporter(A, "neighbor_table", [
        {"neighbor_eui64": B, "rssi_avg": -45},
    ])
    rows = store.list_links()
    assert len(rows) == 1
    assert rows[0]["rssi_avg"] == -45
    # Different source coexists.
    store.replace_links_for_reporter(A, "route_table", [
        {"neighbor_eui64": C, "path_cost": 1},
    ])
    assert len(store.list_links()) == 2
    assert len(store.list_links(source="route_table")) == 1


def test_set_node_diagnostics(store: SQLiteStore) -> None:
    A = "aa" * 8
    store.upsert_node_metadata(eui64=A)
    ok = store.set_node_diagnostics(
        A, partition_id=1234, leader_router_id=0,
        routing_role="leader", active_routers=3, channel=15, weighting=64,
    )
    assert ok is True
    nodes = {n["eui64"]: n for n in store.list_nodes()}
    assert nodes[A]["partition_id"] == 1234
    assert nodes[A]["routing_role"] == "leader"
    assert nodes[A]["channel"] == 15
    assert nodes[A]["diag_updated_at"] is not None


def test_bump_last_referenced_creates_node(store: SQLiteStore) -> None:
    eui = "bb" * 8
    n = store.bump_last_referenced([eui])
    assert n == 1
    node = store.get_node(eui)
    assert node is not None
    assert node["last_referenced_at"] is not None
    assert node["is_phantom"] == 0


def test_sweep_phantoms_marks_old_and_clears_fresh(store: SQLiteStore) -> None:
    old_eui = "cc" * 8
    fresh_eui = "dd" * 8
    store.bump_last_referenced([old_eui, fresh_eui])
    # Backdate one row to look stale.
    stale_ts = (datetime.now(tz=UTC) - timedelta(hours=48)).isoformat()
    with store._tx() as conn:  # noqa: SLF001
        conn.execute(
            "UPDATE nodes SET last_referenced_at = ? WHERE eui64 = ?",
            (stale_ts, old_eui),
        )
    result = store.sweep_phantoms(threshold_seconds=24 * 3600)
    assert result["marked"] >= 1
    assert {n["eui64"] for n in store.list_phantom_nodes()} == {old_eui}

    # Bump the stale one back; it should clear.
    store.bump_last_referenced([old_eui])
    result2 = store.sweep_phantoms(threshold_seconds=24 * 3600)
    assert result2["cleared"] >= 1
    assert store.list_phantom_nodes() == []


def test_purge_phantom_nodes_removes_links(store: SQLiteStore) -> None:
    A = "ee" * 8
    B = "ff" * 8
    store.bump_last_referenced([A, B])
    store.replace_links_for_reporter(A, "neighbor_table", [
        {"neighbor_eui64": B, "rssi_avg": -55, "is_child": True},
    ])
    # Mark A as phantom via stale ts.
    stale = (datetime.now(tz=UTC) - timedelta(hours=48)).isoformat()
    with store._tx() as conn:  # noqa: SLF001
        conn.execute("UPDATE nodes SET last_referenced_at = ? WHERE eui64 = ?", (stale, A))
    store.sweep_phantoms(threshold_seconds=24 * 3600)
    result = store.purge_phantom_nodes()
    assert result["deleted_nodes"] >= 1
    assert result["deleted_links"] >= 1
    assert store.get_node(A) is None
