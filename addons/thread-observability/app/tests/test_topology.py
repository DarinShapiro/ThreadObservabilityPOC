"""Tests for the topology graph builder."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from thread_observability.pipeline.topology import build_topology
from thread_observability.storage.sqlite_store import SQLiteStore


def test_topology_empty(store: SQLiteStore) -> None:
    snap = build_topology(store=store)
    assert snap["node_count"] == 0
    assert snap["link_count"] == 0
    assert snap["nodes"] == []
    assert snap["links"] == []


def test_topology_links_from_attach(store: SQLiteStore) -> None:
    store.upsert_node_metadata(eui64="aa" * 8, friendly_name="Leader", role="leader")
    store.upsert_node_metadata(eui64="bb" * 8, friendly_name="Child",  role="router")
    store.insert_event(eui64="bb" * 8, type="attach",
                       parent_eui64="aa" * 8, rssi=-55, lqi=240)

    snap = build_topology(store=store)
    assert snap["node_count"] == 2
    assert snap["link_count"] == 1
    link = snap["links"][0]
    assert link == {"child": "bb" * 8, "parent": "aa" * 8}
    child = next(n for n in snap["nodes"] if n["eui64"] == "bb" * 8)
    assert child["parent_eui64"] == "aa" * 8
    assert child["last_rssi"] == -55
    assert child["last_lqi"] == 240


def test_topology_uses_latest_parent_change(store: SQLiteStore) -> None:
    store.upsert_node_metadata(eui64="cc" * 8)
    store.insert_event(eui64="cc" * 8, type="attach",
                       ts=(datetime.now(tz=UTC) - timedelta(minutes=10)).isoformat(),
                       parent_eui64="aa" * 8)
    store.insert_event(eui64="cc" * 8, type="parent_change",
                       ts=(datetime.now(tz=UTC) - timedelta(minutes=2)).isoformat(),
                       parent_eui64="bb" * 8)

    snap = build_topology(store=store)
    child = next(n for n in snap["nodes"] if n["eui64"] == "cc" * 8)
    assert child["parent_eui64"] == "bb" * 8


def test_topology_stale_window(store: SQLiteStore) -> None:
    # Node with an old attach event outside the freshness window
    store.upsert_node_metadata(eui64="dd" * 8)
    store.insert_event(eui64="dd" * 8, type="attach",
                       ts=(datetime.now(tz=UTC) - timedelta(hours=3)).isoformat(),
                       parent_eui64="aa" * 8)

    snap = build_topology(store=store, freshness_minutes=60)
    child = next(n for n in snap["nodes"] if n["eui64"] == "dd" * 8)
    assert child["parent_eui64"] is None, "old parent edge should not be inferred"
    assert snap["link_count"] == 0
