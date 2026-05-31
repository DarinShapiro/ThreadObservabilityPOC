from __future__ import annotations

from pathlib import Path

from thread_observability.network_health import build_network_health

from .scenarios.loader import load_scenario, seed_store


_FIXTURE_DIR = Path(__file__).parent / "scenarios" / "fixtures"


def _load_fixture(name: str) -> dict[str, object]:
    return load_scenario(_FIXTURE_DIR / f"{name}.json")


def test_network_health_builder_replays_single_otbr_three_routers(store) -> None:
    scenario = _load_fixture("single_otbr_three_routers")
    seed_store(store, scenario)

    payload = build_network_health(store=store)

    assert payload["summary"]["router_count"] == 3
    assert payload["summary"]["distinct_partitions"] == 1
    assert "PARTITION_RISK" not in payload["reason_codes"]
    assert payload["score"] >= 0.5
    assert any(node["device_kind"] == "router" for node in payload["nodes"])
    assert any(edge["edge_class"] == "peer" for edge in payload["edges"])
    assert payload["placement_candidates"]
    assert any(len(candidate["affected_nodes"]) > 1 for candidate in payload["placement_candidates"])
    assert any(
        "REINFORCE_WEAK_BACKBONE_PATH" in candidate["reason_codes"]
        or "RELIEVE_BOTTLENECK_ROUTER" in candidate["reason_codes"]
        for candidate in payload["placement_candidates"]
    )


def test_network_health_builder_replays_split_mesh_fixture(store) -> None:
    scenario = _load_fixture("network_split_two_partitions")
    seed_store(store, scenario)

    payload = build_network_health(store=store)

    assert payload["summary"]["distinct_partitions"] == 2
    assert any(code in payload["reason_codes"] for code in {"PARTITION_RISK", "PARTITION_SPLIT"})
    assert any(finding["finding_id"] == "split_mesh" for finding in payload["findings"])
    assert payload["band"] != "healthy"


def test_network_health_builder_replays_reed_child_fixture(store) -> None:
    scenario = _load_fixture("reed_attached_as_child")
    seed_store(store, scenario)

    payload = build_network_health(store=store)

    reed = next(node for node in payload["nodes"] if node["eui64"] == "ffffffffffffffff")
    assert reed["metrics"]["parent_eui64"] == "aaaaaaaaaaaaaaaa"
    assert reed["metrics"]["parent_edge_quality"] > 0.5
    assert "MARGINAL_PARENT_LINK" not in reed["reason_codes"]