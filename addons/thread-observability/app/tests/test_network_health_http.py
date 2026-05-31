from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from thread_observability.api.http_api import create_core_app

from .scenarios.loader import load_scenario, seed_store


_FIXTURE_DIR = Path(__file__).parent / "scenarios" / "fixtures"


def _load_fixture(name: str) -> dict[str, object]:
    return load_scenario(_FIXTURE_DIR / f"{name}.json")


def test_network_health_endpoint_exposes_contract_shape(store) -> None:
    scenario = _load_fixture("single_otbr_three_routers")
    seed_store(store, scenario)

    client = TestClient(create_core_app())
    response = client.get("/v1/network/health")

    assert response.status_code == 200
    payload = response.json()
    assert {
        "computed_at",
        "as_of",
        "score",
        "band",
        "confidence",
        "summary",
        "component_scores",
        "reason_codes",
        "nodes",
        "edges",
        "findings",
    }.issubset(payload)
    assert "placement_candidates" not in payload
    assert payload["summary"]["router_count"] == 3
    assert 0.0 <= payload["score"] <= 1.0
    assert isinstance(payload["nodes"], list)
    assert isinstance(payload["edges"], list)
    assert isinstance(payload["findings"], list)
    if payload["findings"]:
        assert "affected_nodes" in payload["findings"][0]


def test_network_placement_candidates_endpoint_wraps_candidates(store) -> None:
    scenario = _load_fixture("single_otbr_three_routers")
    seed_store(store, scenario)

    client = TestClient(create_core_app())
    response = client.get("/v1/network/placement-candidates")

    assert response.status_code == 200
    payload = response.json()
    assert {"computed_at", "as_of", "confidence", "candidates"}.issubset(payload)
    assert isinstance(payload["candidates"], list)
    if payload["candidates"]:
        candidate = payload["candidates"][0]
        assert "candidate_id" in candidate
        assert "recommendation_type" in candidate
        assert isinstance(candidate["reason_codes"], list)