"""Tests for the Tier 4 ``analyze_node`` bundled consultant tool."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from thread_observability.pipeline import analyze_node as an_mod
from thread_observability.pipeline import playbooks as pb_mod
from thread_observability.storage.sqlite_store import SQLiteStore


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def test_analyze_unknown_eui_returns_structure_with_node_none(
    store: SQLiteStore,
) -> None:
    pb_mod.reset_cache_for_tests()
    res = an_mod.analyze_node("aa" * 8, store=store)
    assert res["node"] is None
    assert res["open_issues"] == []
    assert res["recent_issues"] == []
    assert res["neighbors"] == []
    assert res["timeline"]["count"] == 0
    assert res["playbooks"] == []
    assert "baselines" in res


def test_analyze_with_open_issue_attaches_matching_playbooks(
    store: SQLiteStore,
) -> None:
    pb_mod.reset_cache_for_tests()
    eui = "deadbeefcafe0001"
    store.upsert_node_metadata(eui64=eui, friendly_name="Bulb", role="end_device")
    store.open_issue(
        kind="parent_churn",
        severity="warn",
        eui64=eui,
        evidence={"changes": 5},
    )
    res = an_mod.analyze_node(eui, store=store)
    assert res["node"] is not None
    assert len(res["open_issues"]) == 1
    assert res["matched_issue_kinds"] == ["parent_churn"]
    pb_ids = {p["id"] for p in res["playbooks"]}
    # parent_churn playbook itself must be included; observer_suppressed
    # also covers parent_churn and should be in the result.
    assert "parent_churn" in pb_ids
    assert "observer_suppressed" in pb_ids


def test_analyze_baselines_compare_recent_vs_prior(store: SQLiteStore) -> None:
    pb_mod.reset_cache_for_tests()
    eui = "deadbeefcafe0001"
    store.upsert_node_metadata(eui64=eui, friendly_name="Router", role="router")
    now = datetime(2026, 5, 12, 12, 0, 0, tzinfo=UTC)

    # 3 parent_change events in the recent 7-day window.
    for i in range(3):
        store.insert_event(
            eui64=eui,
            type="parent_change",
            ts=_iso(now - timedelta(days=1, hours=i)),
        )
    # 1 parent_change in the prior 7-day window.
    store.insert_event(
        eui64=eui,
        type="parent_change",
        ts=_iso(now - timedelta(days=10)),
    )

    res = an_mod.analyze_node(eui, store=store, baseline_days=7, now=now)
    b = res["baselines"]
    assert b["parent_change_count_recent"] == 3
    assert b["parent_change_count_prior"] == 1
    assert b["parent_change_delta"] == 2


def test_analyze_timeline_includes_events_for_node(store: SQLiteStore) -> None:
    pb_mod.reset_cache_for_tests()
    eui = "deadbeefcafe0001"
    store.upsert_node_metadata(eui64=eui, friendly_name="X", role="end_device")
    now = datetime(2026, 5, 12, 12, 0, 0, tzinfo=UTC)
    store.insert_event(eui64=eui, type="attach", ts=_iso(now - timedelta(hours=1)))
    store.insert_event(
        eui64=eui, type="parent_change", ts=_iso(now - timedelta(minutes=30))
    )

    res = an_mod.analyze_node(eui, store=store, timeline_hours=24, now=now)
    kinds = [r["kind"] for r in res["timeline"]["rows"]]
    assert "attach" in kinds
    assert "parent_change" in kinds


def test_analyze_binds_global_partition_split_issue_via_evidence(
    store: SQLiteStore,
) -> None:
    """A global ``partition_split`` issue whose evidence lists the EUI
    as a singleton-partition member must show up in ``open_issues``."""
    pb_mod.reset_cache_for_tests()
    lonely = "aa" * 8
    majority = "bb" * 8
    store.upsert_node_metadata(eui64=lonely, friendly_name="Lonely", role="router")
    store.upsert_node_metadata(eui64=majority, friendly_name="Big", role="router")
    store.open_issue(
        kind="partition_split",
        severity="warning",
        eui64=None,
        evidence={
            "partition_count": 2,
            "partitions": [
                {"partition_id": 1, "member_count": 1, "members": [lonely]},
                {"partition_id": 2, "member_count": 1, "members": [majority]},
            ],
        },
    )
    res = an_mod.analyze_node(lonely, store=store)
    assert len(res["open_issues"]) == 1
    assert res["open_issues"][0]["kind"] == "partition_split"
    assert res["open_issues"][0].get("implicated_via") == "evidence"
    assert "partition_split" in res["matched_issue_kinds"]
    pb_ids = {p["id"] for p in res["playbooks"]}
    assert "partition_split" in pb_ids


def test_analyze_does_not_implicate_unrelated_node_in_global_issue(
    store: SQLiteStore,
) -> None:
    """A node NOT named in a global issue's evidence must not pick it up."""
    pb_mod.reset_cache_for_tests()
    inside = "aa" * 8
    outside = "cc" * 8
    store.upsert_node_metadata(eui64=outside, friendly_name="Outside", role="router")
    store.open_issue(
        kind="partition_split",
        severity="warning",
        eui64=None,
        evidence={
            "partition_count": 2,
            "partitions": [
                {"partition_id": 1, "member_count": 1, "members": [inside]},
                {"partition_id": 2, "member_count": 1, "members": ["bb" * 8]},
            ],
        },
    )
    res = an_mod.analyze_node(outside, store=store)
    assert res["open_issues"] == []
    assert res["matched_issue_kinds"] == []


def test_analyze_surfaces_duplicate_physical_identity(store: SQLiteStore) -> None:
    """v0.9.46: nodes sharing vendor/product/serial are reported as duplicates."""
    pb_mod.reset_cache_for_tests()
    eui_a = "aa" * 8
    eui_b = "bb" * 8
    eui_c = "cc" * 8
    # Two rows for the same physical device (a was re-commissioned as b).
    store.upsert_node_metadata(
        eui64=eui_a, friendly_name="Foyer Light (stale)",
        vendor_id=4488, product_id=12345, serial_number="SN-XYZ-001",
    )
    store.upsert_node_metadata(
        eui64=eui_b, friendly_name="Foyer Light",
        vendor_id=4488, product_id=12345, serial_number="SN-XYZ-001",
    )
    # Unrelated device — must not appear in the duplicate set.
    store.upsert_node_metadata(
        eui64=eui_c, friendly_name="Kitchen Light",
        vendor_id=4488, product_id=12345, serial_number="SN-DIFFERENT",
    )

    res = an_mod.analyze_node(eui_b, store=store)
    phys = res["physical_identity"]
    assert phys is not None
    assert phys["vendor_id"] == 4488
    assert phys["product_id"] == 12345
    assert phys["serial_number"] == "SN-XYZ-001"
    assert phys["duplicate_count"] == 2
    other_euis = {o["eui64"] for o in phys["other_instances"]}
    assert other_euis == {eui_a}


def test_analyze_physical_identity_none_when_no_basic_info(
    store: SQLiteStore,
) -> None:
    """Nodes without BasicInformation data yield ``physical_identity`` None."""
    pb_mod.reset_cache_for_tests()
    eui = "dd" * 8
    store.upsert_node_metadata(eui64=eui, friendly_name="Plain")
    res = an_mod.analyze_node(eui, store=store)
    assert res["physical_identity"] is None


def test_analyze_includes_same_partition_peer_comparison(store: SQLiteStore) -> None:
    pb_mod.reset_cache_for_tests()
    subject = "11" * 8
    peer_a = "22" * 8
    peer_b = "33" * 8
    now = datetime(2026, 5, 12, 12, 0, 0, tzinfo=UTC)

    store.upsert_node_metadata(eui64=subject, friendly_name="Subject", role="router")
    store.upsert_node_metadata(eui64=peer_a, friendly_name="Peer A", role="router")
    store.upsert_node_metadata(eui64=peer_b, friendly_name="Peer B", role="router")
    store.set_node_diagnostics(subject, partition_id=42, routing_role="router")
    store.set_node_diagnostics(peer_a, partition_id=42, routing_role="router")
    store.set_node_diagnostics(peer_b, partition_id=42, routing_role="router")

    for offset_days in (1, 2, 3):
        store.insert_event(eui64=subject, type="parent_change", ts=_iso(now - timedelta(days=offset_days)))
    store.insert_event(eui64=peer_a, type="parent_change", ts=_iso(now - timedelta(days=1)))

    res = an_mod.analyze_node(subject, store=store, baseline_days=7, now=now)
    peer = res["peer_comparison"]

    assert peer is not None
    assert peer["partition_id"] == 42
    assert peer["peer_count"] == 2
    assert peer["subject_parent_change_count_recent"] == 3
    assert peer["more_unstable_than_partition_peers"] is True
    assert peer["top_partition_peers_by_parent_change"][0]["eui64"] == peer_a

