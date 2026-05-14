"""Tests for the Background Diagnostics SQLite schema (#18-#23)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from thread_observability.storage.sqlite_store import SQLiteStore


def test_schema_v23_tables_exist(store: SQLiteStore) -> None:
    assert store.schema_version >= 23
    with store._lock:
        names = {
            r[0]
            for r in store._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert {
        "assessment_schedule",
        "assessment_findings",
        "assessment_feedback",
        "assessment_runs",
    } <= names


def test_upsert_schedule_initial_defaults(store: SQLiteStore) -> None:
    row = store.upsert_assessment_schedule({})
    assert row["state"] == "probation"
    assert row["consecutive_ok"] == 0
    assert row["budget_calls_used"] == 0


def test_upsert_schedule_merges_partial(store: SQLiteStore) -> None:
    store.upsert_assessment_schedule({"state": "steady"})
    row = store.upsert_assessment_schedule({"consecutive_ok": 5})
    assert row["state"] == "steady"
    assert row["consecutive_ok"] == 5


def test_upsert_finding_dedup_bumps_seen(store: SQLiteStore) -> None:
    a = store.upsert_assessment_finding(
        finding_id="evid-a",
        finding_key="kkk",
        verdict="investigate",
        severity="investigate",
        confidence=0.6,
        headline="parent flap",
        evidence=[{"tool": "x", "key_finding": "y"}],
    )
    assert a["finding_id"] == "evid-a"
    assert a["seen_count"] == 1
    b = store.upsert_assessment_finding(
        finding_id="evid-b",
        finding_key="kkk",
        verdict="investigate",
        severity="investigate",
        confidence=0.8,
        headline="parent flap (more)",
        evidence=[],
    )
    # same finding_key -> dedup hit, original id returned
    assert b["finding_id"] == "evid-a"
    assert b["seen_count"] == 2
    assert b["confidence"] == 0.8


def test_clear_findings_by_key(store: SQLiteStore) -> None:
    store.upsert_assessment_finding(
        finding_id="evid-c",
        finding_key="kk2",
        verdict="investigate",
        severity="investigate",
        confidence=0.5,
        headline="weak link",
        evidence=[{"tool": "a", "key_finding": "b"}],
    )
    n = store.clear_assessment_findings_by_key("kk2")
    assert n == 1
    rows = store.list_assessment_findings(state="cleared")
    assert any(r["finding_key"] == "kk2" for r in rows)


def test_dismiss_finding_suppresses_key(store: SQLiteStore) -> None:
    store.upsert_assessment_finding(
        finding_id="evid-d",
        finding_key="kk3",
        verdict="investigate",
        severity="investigate",
        confidence=0.5,
        headline="something",
        evidence=[{"tool": "a", "key_finding": "b"}],
    )
    store.dismiss_assessment_finding("evid-d", suppress_seconds=60)
    assert store.is_finding_key_suppressed("kk3")
    past = (datetime.now(tz=UTC) + timedelta(seconds=3600)).isoformat()
    # check well after suppress window
    assert not store.is_finding_key_suppressed("kk3", at=past)


def test_feedback_summary(store: SQLiteStore) -> None:
    f = store.upsert_assessment_finding(
        finding_id="evid-f",
        finding_key="kk4",
        verdict="investigate",
        severity="investigate",
        confidence=0.7,
        headline="x",
        evidence=[{"tool": "a", "key_finding": "b"}],
        finding_type="parent_flapping",
    )
    store.record_assessment_feedback(
        finding_id=f["finding_id"],
        outcome="resolved",
        finding_type="parent_flapping",
    )
    summary = store.assessment_feedback_summary()
    assert summary["total_findings"] == 1
    assert summary["by_outcome"]["resolved"] == 1


def test_assessment_runs_history_round_trip(store: SQLiteStore) -> None:
    run = store.record_assessment_run(
        verdict="watch",
        severity="watch",
        confidence=0.7,
        headline="check this link",
        finding_key="abc",
        finding_id="evid-1",
        finding_type="link_quality_drop",
        node_eui64="AA",
        parse_attempts=1,
        duration_seconds=1.25,
        model_name="claude-sonnet-4-5",
    )
    rows = store.list_assessment_runs(limit=10)
    assert rows[0]["id"] == run["id"]
    assert rows[0]["model_name"] == "claude-sonnet-4-5"
    assert rows[0]["headline"] == "check this link"


def test_chat_turn_stats_round_trip(store: SQLiteStore) -> None:
    row = store.record_chat_turn_stat(
        conversation_id="direct-123",
        recorded_at="2026-05-13T18:00:00Z",
        backend="direct",
        agent_id="direct:cerebras",
        model_name="llama3.1-8b",
        status="ok",
        error_kind=None,
        duration_ms=420,
        tool_call_count=2,
        had_page_context=True,
        selected_node_eui64="e6684b9903e8970f",
        active_tab="network",
    )

    assert row["backend"] == "direct"
    summary = store.get_chat_turn_stats()
    assert summary["total_turns"] == 1
    assert summary["by_backend"] == {"direct": 1}
    assert summary["by_status"] == {"ok": 1}
    assert summary["page_context_turns"] == 1
    assert summary["recent_turns"][0]["selected_node_eui64"] == "e6684b9903e8970f"
