from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from thread_observability import network_health
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
    split_mesh = next(finding for finding in payload["findings"] if finding["finding_id"] == "split_mesh")
    assert split_mesh["title"] == "Orphan Router D is attached to the wrong partition"
    assert "Orphan Router D is alone on partition 2000" in (split_mesh.get("summary") or "")
    assert "Recommission Orphan Router D" in (split_mesh.get("recommended_action") or "")
    assert payload["band"] != "healthy"


def test_network_health_builder_replays_reed_child_fixture(store) -> None:
    scenario = _load_fixture("reed_attached_as_child")
    seed_store(store, scenario)

    payload = build_network_health(store=store)

    reed = next(node for node in payload["nodes"] if node["eui64"] == "ffffffffffffffff")
    assert reed["metrics"]["parent_eui64"] == "aaaaaaaaaaaaaaaa"
    assert reed["metrics"]["parent_edge_quality"] > 0.5
    assert "MARGINAL_PARENT_LINK" not in reed["reason_codes"]


def test_data_freshness_seconds_uses_snapshot_timestamp() -> None:
    now = datetime(2026, 5, 30, 22, 15, 0, tzinfo=UTC)

    assert network_health._data_freshness_seconds("2026-05-30T22:14:42Z", now=now) == 18
    assert network_health._data_freshness_seconds(None, now=now) == 0


def test_intermediary_candidate_only_gets_alternate_path_reason_for_material_gap(monkeypatch) -> None:
    monkeypatch.setattr(
        network_health,
        "_topology_findings",
        lambda _snapshot: [
            {
                "evidence": [
                    {
                        "kind": "intermediary_router_opportunity",
                        "eui64": "aaaaaaaaaaaaaaaa",
                    }
                ]
            }
        ],
    )

    candidates = network_health._placement_candidates(
        snapshot={"links": [{"from": "aaaaaaaaaaaaaaaa", "to": "bbbbbbbbbbbbbbbb", "edge_class": "peer"}]},
        node_rows={
            "aaaaaaaaaaaaaaaa": {"friendly_name": "Router A"},
            "bbbbbbbbbbbbbbbb": {"friendly_name": "Router B"},
        },
        node_records=[
            {
                "eui64": "aaaaaaaaaaaaaaaa",
                "metrics": {
                    "strong_neighbor_count": 1,
                    "alternate_path_count": 1,
                    "bridge_dependency": 0.0,
                },
            }
        ],
        router_rows={
            "aaaaaaaaaaaaaaaa": {
                "target_strong_neighbors": 1,
                "reason_codes": [],
            }
        },
        edge_rows=[
            {
                "edge_class": "peer",
                "source_eui64": "aaaaaaaaaaaaaaaa",
                "target_eui64": "bbbbbbbbbbbbbbbb",
                "score": 0.8,
            }
        ],
    )

    assert candidates
    assert "ADD_ROUTER_FOR_ALTERNATE_PATH" not in candidates[0]["reason_codes"]