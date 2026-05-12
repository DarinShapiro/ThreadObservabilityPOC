"""Tests for the Tier 4 playbook corpus loader."""

from __future__ import annotations

import pytest

from thread_observability.pipeline import playbooks as pb_mod


@pytest.fixture(autouse=True)
def _reset_cache():
    pb_mod.reset_cache_for_tests()
    yield
    pb_mod.reset_cache_for_tests()


def test_list_playbooks_returns_corpus_summaries() -> None:
    res = pb_mod.list_playbooks()
    assert res["count"] >= 10
    # Each summary has exactly id/title/applies_to (no full body leakage).
    for entry in res["playbooks"]:
        assert set(entry.keys()) == {"id", "title", "applies_to"}
        assert isinstance(entry["applies_to"], list)


def test_lookup_by_id_returns_full_entry() -> None:
    res = pb_mod.lookup_playbook(playbook_id="offline_node")
    assert res["count"] == 1
    entry = res["matches"][0]
    assert entry["id"] == "offline_node"
    # Full entry has remediation + evidence + references.
    assert isinstance(entry["remediation_steps"], list)
    assert isinstance(entry["evidence_to_collect"], list)
    assert isinstance(entry["references"], list)


def test_lookup_by_kind_returns_all_matching() -> None:
    res = pb_mod.lookup_playbook(kind="parent_churn")
    ids = [m["id"] for m in res["matches"]]
    # The dedicated parent_churn playbook plus at least the observer-suppressed
    # cross-cutting one and the rf_coexistence one should match.
    assert "parent_churn" in ids
    assert "observer_suppressed" in ids
    assert "rf_coexistence" in ids


def test_lookup_by_query_substring() -> None:
    res = pb_mod.lookup_playbook(query="battery")
    ids = [m["id"] for m in res["matches"]]
    assert "sed_battery_drain" in ids


def test_lookup_unknown_returns_empty() -> None:
    res = pb_mod.lookup_playbook(playbook_id="does_not_exist")
    assert res["matches"] == []
    assert res["count"] == 0


def test_lookup_for_kinds_dedupes_across_overlap() -> None:
    """observer_suppressed applies to many kinds; if we ask for two of
    those kinds, the entry should appear exactly once."""
    out = pb_mod.lookup_for_kinds(["parent_churn", "offline_node"])
    obs = [p for p in out if p["id"] == "observer_suppressed"]
    assert len(obs) == 1
