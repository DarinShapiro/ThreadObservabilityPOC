"""Tests for the Tier 3 observer-events ingestor."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest

from thread_observability.api import supervisor_client
from thread_observability.pipeline import observer_events
from thread_observability.storage.sqlite_store import SQLiteStore


@pytest.fixture(autouse=True)
def _reset_caches():
    """Clear the module-level caches between tests so each test starts
    from a clean slate — otherwise prior runs' ``_last_state`` would
    leak in and the "first observation" branch wouldn't trip.
    """
    observer_events._reset_state_for_tests()
    yield
    observer_events._reset_state_for_tests()


def test_record_self_start_inserts_point_in_time_event(store: SQLiteStore) -> None:
    ev_id = observer_events.record_self_start(store, version="0.9.44")
    assert ev_id > 0
    latest = store.get_latest_observer_event("addon:self")
    assert latest is not None
    assert latest["kind"] == "start"
    # Point-in-time markers have started_at == ended_at.
    assert latest["started_at"] == latest["ended_at"]
    assert latest["details"] == {"version": "0.9.44"}


def test_poll_first_observation_caches_no_event(store: SQLiteStore) -> None:
    """The first time we see each addon, we just cache its state and
    emit no event — otherwise every cold start would falsely log an
    outage for every tracked slug."""

    async def fake_get_json(path: str) -> dict[str, Any]:
        return {"state": "started"}

    with patch.object(supervisor_client, "_get_json", new=fake_get_json):
        summary = asyncio.run(observer_events.poll_supervisor_addons(store))

    assert summary["polled"] == len(observer_events.TRACKED_SLUGS)
    assert summary["opened"] == 0
    assert summary["closed"] == 0
    # No observer rows should have been written.
    for slug in observer_events.TRACKED_SLUGS:
        src = f"addon:{slug}" if slug != "self" else "addon:self"
        assert store.get_latest_observer_event(src) is None


def test_poll_started_to_stopped_opens_event_and_back_closes_it(
    store: SQLiteStore,
) -> None:
    state: dict[str, str] = {"value": "started"}

    async def fake_get_json(path: str) -> dict[str, Any]:
        return {"state": state["value"]}

    with patch.object(supervisor_client, "_get_json", new=fake_get_json):
        # First poll: caches "started" for every slug.
        asyncio.run(observer_events.poll_supervisor_addons(store))
        # Transition: everything stopped.
        state["value"] = "stopped"
        out_summary = asyncio.run(observer_events.poll_supervisor_addons(store))
        assert out_summary["opened"] == len(observer_events.TRACKED_SLUGS)
        # One open outage row per slug, ended_at NULL.
        for slug in observer_events.TRACKED_SLUGS:
            src = f"addon:{slug}" if slug != "self" else "addon:self"
            latest = store.get_latest_observer_event(src)
            assert latest is not None
            assert latest["kind"] == "outage"
            assert latest["ended_at"] is None

        # Recovery: back to started → events close.
        state["value"] = "started"
        in_summary = asyncio.run(observer_events.poll_supervisor_addons(store))
        assert in_summary["closed"] == len(observer_events.TRACKED_SLUGS)
        for slug in observer_events.TRACKED_SLUGS:
            src = f"addon:{slug}" if slug != "self" else "addon:self"
            latest = store.get_latest_observer_event(src)
            assert latest is not None
            assert latest["ended_at"] is not None


def test_poll_supervisor_error_counts_but_does_not_raise(
    store: SQLiteStore,
) -> None:
    async def fake_get_json(path: str) -> dict[str, Any]:
        raise RuntimeError("supervisor unreachable")

    with patch.object(supervisor_client, "_get_json", new=fake_get_json):
        summary = asyncio.run(observer_events.poll_supervisor_addons(store))
    assert summary["errors"] == len(observer_events.TRACKED_SLUGS)
    assert summary["opened"] == 0
    assert summary["closed"] == 0
