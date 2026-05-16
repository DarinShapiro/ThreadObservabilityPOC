"""Tests for the Tier 4 topology snapshot + diff."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from thread_observability.pipeline import topology as topology_mod
from thread_observability.pipeline import topology_snapshot as ts_mod
from thread_observability.storage.sqlite_store import SQLiteStore


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _seed_two_nodes(store: SQLiteStore) -> tuple[str, str]:
    """Insert two registered nodes so build_topology returns something."""
    a = "aa" * 8
    b = "bb" * 8
    store.upsert_node_metadata(eui64=a, friendly_name="A", role="router")
    store.upsert_node_metadata(eui64=b, friendly_name="B", role="end_device")
    return a, b


def test_capture_snapshot_writes_then_skips_when_unchanged(store: SQLiteStore) -> None:
    _seed_two_nodes(store)
    t0 = datetime(2026, 5, 12, 12, 0, 0, tzinfo=UTC)
    first = ts_mod.capture_snapshot(store, now=t0)
    assert first["written"] is True
    assert first["snapshot_id"] is not None

    # Same topology, well within the heartbeat window → skip.
    second = ts_mod.capture_snapshot(
        store, now=t0 + timedelta(minutes=5)
    )
    assert second["written"] is False
    assert second["snapshot_id"] is None
    assert second["snapshot_hash"] == first["snapshot_hash"]

    # Past the heartbeat (default 60m) → heartbeat row.
    third = ts_mod.capture_snapshot(
        store, now=t0 + timedelta(minutes=61)
    )
    assert third["written"] is True


def test_capture_snapshot_writes_when_topology_changes(store: SQLiteStore) -> None:
    a, b = _seed_two_nodes(store)
    t0 = datetime(2026, 5, 12, 12, 0, 0, tzinfo=UTC)
    first = ts_mod.capture_snapshot(store, now=t0)
    assert first["written"] is True

    # Add a third node; hash should differ → new row even within heartbeat.
    store.upsert_node_metadata(eui64="cc" * 8, friendly_name="C", role="router")
    second = ts_mod.capture_snapshot(
        store, now=t0 + timedelta(minutes=1)
    )
    assert second["written"] is True
    assert second["snapshot_hash"] != first["snapshot_hash"]


def test_diff_topology_reports_added_and_removed_nodes(store: SQLiteStore) -> None:
    a, b = _seed_two_nodes(store)
    t0 = datetime(2026, 5, 12, 12, 0, 0, tzinfo=UTC)
    snap_a = ts_mod.capture_snapshot(store, now=t0)

    # Add a new node, then capture again.
    new_eui = "cc" * 8
    store.upsert_node_metadata(eui64=new_eui, friendly_name="C", role="router")
    snap_b = ts_mod.capture_snapshot(store, now=t0 + timedelta(minutes=1))

    diff = ts_mod.diff_topology(
        store,
        snapshot_id_a=snap_a["snapshot_id"],
        snapshot_id_b=snap_b["snapshot_id"],
    )
    added_euis = {n["eui64"] for n in diff["added_nodes"]}
    assert new_eui in added_euis
    assert diff["summary"]["added_node_count"] >= 1
    assert diff["summary"]["removed_node_count"] == 0


def test_diff_topology_reports_role_change(store: SQLiteStore) -> None:
    a, b = _seed_two_nodes(store)
    t0 = datetime(2026, 5, 12, 12, 0, 0, tzinfo=UTC)
    snap_a = ts_mod.capture_snapshot(store, now=t0)

    # Promote B from end_device to router.
    store.upsert_node_metadata(eui64=b, role="router")
    snap_b = ts_mod.capture_snapshot(store, now=t0 + timedelta(minutes=1))

    diff = ts_mod.diff_topology(
        store,
        snapshot_id_a=snap_a["snapshot_id"],
        snapshot_id_b=snap_b["snapshot_id"],
    )
    changed_euis = {n["eui64"] for n in diff["changed_nodes"]}
    assert b in changed_euis
    target = next(n for n in diff["changed_nodes"] if n["eui64"] == b)
    assert "role" in target["changes"]
    assert target["changes"]["role"]["from"] == "end_device"
    assert target["changes"]["role"]["to"] == "router"


def test_diff_topology_missing_snapshot_returns_error(store: SQLiteStore) -> None:
    out = ts_mod.diff_topology(store, snapshot_id_a=9999, snapshot_id_b=9998)
    assert out.get("error") == "snapshot_not_found"
    assert out["a_found"] is False
    assert out["b_found"] is False


def test_list_topology_snapshots_returns_summary_no_body(store: SQLiteStore) -> None:
    _seed_two_nodes(store)
    t0 = datetime(2026, 5, 12, 12, 0, 0, tzinfo=UTC)
    snapshot = topology_mod.build_topology(store=store)
    snapshot["partitions"] = [
        {
            "partition_id": 1234,
            "leader_eui64": "aa" * 8,
            "member_count": 2,
            "channel": 15,
        }
    ]
    store.insert_topology_snapshot(
        snapshot=snapshot,
        snapshot_hash=ts_mod._canonicalize_snapshot_for_hash(snapshot),
        captured_at=t0.isoformat(),
    )
    summaries = store.list_topology_snapshots()
    assert len(summaries) == 1
    s = summaries[0]
    # No snapshot_json key in the summary.
    assert "snapshot_json" not in s
    assert "snapshot" not in s
    assert s["node_count"] >= 1
    assert s["partition_channels"] == [15]
