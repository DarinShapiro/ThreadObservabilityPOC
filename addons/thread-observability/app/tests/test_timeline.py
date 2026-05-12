"""Tests for the Tier 4 unified timeline."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from thread_observability.pipeline import timeline as timeline_mod
from thread_observability.storage.sqlite_store import SQLiteStore


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def test_query_timeline_merges_three_sources_newest_first(store: SQLiteStore) -> None:
    """A timeline window should emit events + issue lifecycle + observer
    windows interleaved by timestamp, newest first."""
    t0 = datetime(2026, 5, 12, 12, 0, 0, tzinfo=UTC)
    eui = "deadbeefcafe0001"

    # 12:00 — observer outage starts.
    obs_id = store.insert_observer_event(
        source="addon:core_openthread_border_router",
        kind="outage",
        started_at=_iso(t0),
    )
    # 12:05 — node parent_change event.
    store.insert_event(
        eui64=eui,
        type="parent_change",
        ts=_iso(t0 + timedelta(minutes=5)),
        payload={"from": "aaaa", "to": "bbbb"},
    )
    # 12:10 — issue opens.
    iid = store.open_issue(
        kind="parent_churn",
        severity="warn",
        eui64=eui,
        evidence={"changes": 5},
    )
    # open_issue stamps "now"; force a deterministic opened_at for the test.
    _set_issue_times(
        store,
        iid,
        opened_at=_iso(t0 + timedelta(minutes=10)),
        closed_at=_iso(t0 + timedelta(minutes=15)),
    )

    # 12:20 — observer outage ends.
    store.close_observer_event(obs_id, ended_at=_iso(t0 + timedelta(minutes=20)))

    res = timeline_mod.query_timeline(
        store,
        since=_iso(t0 - timedelta(minutes=1)),
        until=_iso(t0 + timedelta(minutes=30)),
    )
    rows = res["rows"]
    # Expect 5 timeline entries (outage open, parent_change, issue.opened,
    # issue.closed, outage ended).
    assert res["count"] == 5
    # Newest first.
    timestamps = [r["ts"] for r in rows]
    assert timestamps == sorted(timestamps, reverse=True)
    kinds = [r["kind"] for r in rows]
    assert "observer.outage" in kinds
    assert "observer.outage.ended" in kinds
    assert "parent_change" in kinds
    assert "issue.opened" in kinds
    assert "issue.closed" in kinds


def test_query_timeline_filters_by_eui64_and_kinds(store: SQLiteStore) -> None:
    t0 = datetime(2026, 5, 12, 12, 0, 0, tzinfo=UTC)
    eui_a = "deadbeefcafe0001"
    eui_b = "deadbeefcafe0002"
    store.insert_event(eui64=eui_a, type="attach", ts=_iso(t0))
    store.insert_event(eui64=eui_b, type="attach", ts=_iso(t0 + timedelta(seconds=1)))
    store.insert_event(
        eui64=eui_a, type="parent_change", ts=_iso(t0 + timedelta(seconds=2))
    )

    only_a = timeline_mod.query_timeline(
        store,
        since=_iso(t0 - timedelta(minutes=1)),
        eui64=eui_a,
    )
    assert all(r["eui64"] == eui_a for r in only_a["rows"])
    assert only_a["count"] == 2

    only_attach = timeline_mod.query_timeline(
        store,
        since=_iso(t0 - timedelta(minutes=1)),
        kinds=["attach"],
    )
    assert {r["kind"] for r in only_attach["rows"]} == {"attach"}
    assert only_attach["count"] == 2


def test_query_timeline_source_allow_list(store: SQLiteStore) -> None:
    t0 = datetime(2026, 5, 12, 12, 0, 0, tzinfo=UTC)
    eui = "deadbeefcafe0001"
    store.insert_event(eui64=eui, type="attach", ts=_iso(t0))
    store.insert_observer_event(
        source="addon:self", kind="start", started_at=_iso(t0)
    )

    res = timeline_mod.query_timeline(
        store,
        since=_iso(t0 - timedelta(minutes=1)),
        sources=["events"],
    )
    assert {r["source"] for r in res["rows"]} == {"events"}


def _set_issue_times(
    store: SQLiteStore,
    issue_id: int,
    *,
    opened_at: str,
    closed_at: str | None = None,
) -> None:
    """Force opened_at/closed_at on an existing issue (test helper).

    The public ``open_issue``/``close_issue`` APIs stamp "now"; for
    deterministic timeline tests we need explicit timestamps.
    """
    with store._lock:  # noqa: SLF001 — test-only helper
        store._conn.execute(  # noqa: SLF001
            "UPDATE issues SET opened_at = ?, closed_at = ? WHERE id = ?",
            (opened_at, closed_at, issue_id),
        )
        store._conn.commit()  # noqa: SLF001
