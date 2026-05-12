"""Tests for the SQLite store (schema, events, issues)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from thread_observability.storage.sqlite_store import SQLiteStore


def test_migrations_apply(store: SQLiteStore) -> None:
    assert store.schema_version == 9
    stats = store.stats()
    assert stats["schema_version"] == 9
    assert stats["row_counts"]["events"] == 0


def test_insert_event_updates_known_node_only(store: SQLiteStore) -> None:
    """Registry-first (v9): events only update existing node rows.

    Unknown EUIs never get auto-inserted from event ingestion — they
    belong on the link side via the ``neighbor_known`` flag, not as
    phantom nodes.
    """
    eui = "aa" * 8
    # Unknown EUI: event records but no node row created.
    eid = store.insert_event(eui64=eui, type="attach", rssi=-60, lqi=200)
    assert eid >= 1
    assert store.get_node(eui) is None

    # Once registered, subsequent events update last_seen on the row.
    store.upsert_node_metadata(eui64=eui)
    store.insert_event(eui64=eui, type="attach", rssi=-55, lqi=210)
    node = store.get_node(eui)
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


def test_bump_last_referenced_skips_unknown_and_touches_known(
    store: SQLiteStore,
) -> None:
    """Registry-first (v9): ``bump_last_referenced`` is UPDATE-only.

    Unknown EUIs (not in the registry-driven ``nodes`` table) are
    silently skipped; they surface via ``links.neighbor_known = 0``
    instead.
    """
    unknown = "bb" * 8
    known = "cc" * 8
    # Unknown EUI: no row created, count is 0.
    assert store.bump_last_referenced([unknown]) == 0
    assert store.get_node(unknown) is None

    # Known EUI: row touched, count is 1.
    store.upsert_node_metadata(eui64=known)
    assert store.bump_last_referenced([known]) == 1
    node = store.get_node(known)
    assert node is not None
    assert node["last_referenced_at"] is not None
    assert node["is_phantom"] == 0


def test_sweep_phantoms_marks_old_and_clears_fresh(store: SQLiteStore) -> None:
    old_eui = "cc" * 8
    fresh_eui = "dd" * 8
    # Registry-first (v9): pre-seed both as mesh-only nodes (no device_id)
    # so ``bump_last_referenced`` can touch them and phantom-sweep logic
    # applies. In production these rows would be created by the HA
    # registry sync; for legacy phantom-sweep semantics we seed bare rows.
    store.upsert_node_metadata(eui64=old_eui)
    store.upsert_node_metadata(eui64=fresh_eui)
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
    store.upsert_node_metadata(eui64=A)
    store.upsert_node_metadata(eui64=B)
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


def test_reset_data_wipes_cache_tables_preserves_schema(store: SQLiteStore) -> None:
    A = "aa" * 8
    B = "bb" * 8
    # Seed some state across the cache tables.
    store.upsert_node_metadata(eui64=A)
    store.upsert_node_metadata(eui64=B)
    store.insert_event(eui64=A, type="attach", rssi=-50)
    store.bump_last_referenced([A, B])
    store.replace_links_for_reporter(A, "neighbor_table", [
        {"neighbor_eui64": B, "rssi_avg": -55, "is_child": True},
    ])
    store.open_issue(kind="weak_link", severity="warn", eui64=A)
    assert store.stats()["row_counts"]["nodes"] >= 1

    deleted = store.reset_data()
    assert deleted >= 1

    counts = store.stats()["row_counts"]
    assert counts["nodes"] == 0
    assert counts["links"] == 0
    assert counts["events"] == 0
    assert counts["issues"] == 0
    # Schema migrations still recorded.
    assert store.schema_version == 9


def test_upsert_node_metadata_persists_ha_fields(store: SQLiteStore) -> None:
    eui = "cc" * 8
    store.upsert_node_metadata(
        eui64=eui,
        friendly_name="Kitchen Plug",
        device_id="abc123",
        area_id="kitchen",
        area_name="Kitchen",
        manufacturer="Eve",
        model="Energy",
        sw_version="2.1.0",
        hw_version="1",
        ha_device_path="/config/devices/device/abc123",
    )
    node = store.get_node(eui)
    assert node is not None
    assert node["friendly_name"] == "Kitchen Plug"
    assert node["area_id"] == "kitchen"
    assert node["area_name"] == "Kitchen"
    # Legacy `area` mirrors area_name for backwards compatibility.
    assert node["area"] == "Kitchen"
    assert node["manufacturer"] == "Eve"
    assert node["model"] == "Energy"
    assert node["sw_version"] == "2.1.0"
    assert node["hw_version"] == "1"
    assert node["ha_device_path"] == "/config/devices/device/abc123"

    # COALESCE semantics: partial update must not wipe existing fields.
    store.upsert_node_metadata(eui64=eui, friendly_name="Kitchen Plug v2")
    node2 = store.get_node(eui)
    assert node2["friendly_name"] == "Kitchen Plug v2"
    assert node2["manufacturer"] == "Eve"
    assert node2["area_name"] == "Kitchen"


def test_sweep_stale_links_deletes_old_rows(store: SQLiteStore) -> None:
    A = "11" * 8
    B = "22" * 8
    store.replace_links_for_reporter(A, "neighbor_table", [
        {"neighbor_eui64": B, "rssi_avg": -60, "is_child": False},
    ])
    # Force the observed_at into the past.
    stale = (datetime.now(tz=UTC) - timedelta(seconds=3600)).isoformat()
    with store._tx() as conn:  # noqa: SLF001
        conn.execute("UPDATE links SET observed_at = ?", (stale,))

    # TTL too generous: nothing should be evicted.
    assert store.sweep_stale_links(ttl_seconds=7200) == 0
    assert len(store.list_links()) == 1

    # TTL tight: row evicted.
    assert store.sweep_stale_links(ttl_seconds=900) == 1
    assert store.list_links() == []


def test_recompute_node_statuses_state_machine(store: SQLiteStore) -> None:
    fresh = "aa" * 8       # online: referenced now, registered
    stale = "bb" * 8       # offline: referenced 1h ago, registered
    dead = "cc" * 8        # phantom: referenced 48h ago, no device_id
    unreg = "dd" * 8       # unregistered: never referenced, no device_id
    registered_old = "ee" * 8  # offline: registered, last ref 48h ago (never goes phantom)

    # Registered nodes (have device_id).
    store.upsert_node_metadata(eui64=fresh, friendly_name="Fresh", device_id="d1")
    store.upsert_node_metadata(eui64=stale, friendly_name="Stale", device_id="d2")
    store.upsert_node_metadata(eui64=registered_old, friendly_name="Old", device_id="d3")
    # Mesh-only nodes (no device_id). Registry-first (v9): bump is now
    # UPDATE-only, so the rows must be created explicitly first.
    store.upsert_node_metadata(eui64=dead)
    store.upsert_node_metadata(eui64=unreg)
    store.bump_last_referenced([dead, unreg])
    # Clear unreg's last_referenced_at so it really has none.
    with store._tx() as conn:  # noqa: SLF001
        conn.execute("UPDATE nodes SET last_referenced_at = NULL WHERE eui64 = ?", (unreg,))

    now = datetime.now(tz=UTC)
    fresh_ts = now.isoformat()
    stale_ts = (now - timedelta(hours=1)).isoformat()
    dead_ts = (now - timedelta(hours=48)).isoformat()
    with store._tx() as conn:  # noqa: SLF001
        conn.execute("UPDATE nodes SET last_referenced_at = ? WHERE eui64 = ?", (fresh_ts, fresh))
        conn.execute("UPDATE nodes SET last_referenced_at = ? WHERE eui64 = ?", (stale_ts, stale))
        conn.execute("UPDATE nodes SET last_referenced_at = ? WHERE eui64 = ?", (dead_ts, dead))
        conn.execute("UPDATE nodes SET last_referenced_at = ? WHERE eui64 = ?", (dead_ts, registered_old))

    summary = store.recompute_node_statuses(offline_seconds=900, phantom_seconds=24 * 3600)
    assert summary["online"] == 1
    # stale + registered_old are both offline (registered, recent-ish or old).
    assert summary["offline"] == 2
    assert summary["unregistered"] == 1
    assert summary["phantom"] == 1

    assert store.get_node(fresh)["status"] == "online"
    assert store.get_node(stale)["status"] == "offline"
    assert store.get_node(registered_old)["status"] == "offline"  # protected
    assert store.get_node(unreg)["status"] == "unregistered"
    assert store.get_node(dead)["status"] == "phantom"
    # is_phantom mirrors status=='phantom' for backwards compat.
    assert store.get_node(dead)["is_phantom"] == 1
    assert store.get_node(stale)["is_phantom"] == 0


def test_purge_expired_nodes_preserves_ha_registered(store: SQLiteStore) -> None:
    keep = "11" * 8
    purge = "22" * 8
    store.upsert_node_metadata(eui64=keep, friendly_name="Keep", device_id="x")
    # Registry-first (v9): seed the mesh-only row explicitly; bump no
    # longer auto-creates unknown EUIs.
    store.upsert_node_metadata(eui64=purge)
    store.bump_last_referenced([purge])
    very_old = (datetime.now(tz=UTC) - timedelta(days=90)).isoformat()
    with store._tx() as conn:  # noqa: SLF001
        conn.execute("UPDATE nodes SET last_referenced_at = ? WHERE eui64 = ?", (very_old, keep))
        conn.execute("UPDATE nodes SET last_referenced_at = ? WHERE eui64 = ?", (very_old, purge))
    store.recompute_node_statuses(offline_seconds=900, phantom_seconds=24 * 3600)
    # `keep` is HA-registered: offline forever. `purge` is mesh-only: phantom.
    assert store.get_node(keep)["status"] == "offline"
    assert store.get_node(purge)["status"] == "phantom"

    result = store.purge_expired_nodes(max_offline_seconds=30 * 86400)
    assert result["deleted_nodes"] == 1
    assert purge in result["euis"]
    # HA-registered preserved.
    assert store.get_node(keep) is not None
    assert store.get_node(purge) is None
