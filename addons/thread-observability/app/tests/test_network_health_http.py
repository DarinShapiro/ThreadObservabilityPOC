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
        "ai_assessment",
    }.issubset(payload)
    assert "placement_candidates" not in payload
    assert payload["summary"]["router_count"] == 3
    assert 0.0 <= payload["score"] <= 1.0
    assert isinstance(payload["nodes"], list)
    assert isinstance(payload["edges"], list)
    assert isinstance(payload["findings"], list)
    assert payload["ai_assessment"] is None
    if payload["findings"]:
        assert "affected_nodes" in payload["findings"][0]


def test_network_health_endpoint_exposes_ai_assessment_companion_payload(store) -> None:
    scenario = _load_fixture("single_otbr_three_routers")
    seed_store(store, scenario)
    store.upsert_assessment_finding(
        finding_id="finding-1",
        finding_key="partition-risk-main",
        verdict="investigate",
        severity="investigate",
        confidence=0.83,
        finding_type="partition_anomaly",
        headline="AI assessment: a router appears isolated from the main mesh",
        evidence=[
            {"tool": "get_network_health", "key_finding": "distinct_partitions = 2"},
            {"tool": "list_all_nodes", "key_finding": "one router is alone on partition 2000"},
        ],
        suggested_starter_prompt="Why does one router look isolated from the main partition?",
        node_eui64="dddddddddddddddd",
    )
    store.record_assessment_run(
        verdict="investigate",
        severity="investigate",
        confidence=0.83,
        headline="AI assessment: a router appears isolated from the main mesh",
        finding_key="partition-risk-main",
        finding_id="finding-1",
        finding_type="partition_anomaly",
        node_eui64="dddddddddddddddd",
        parse_attempts=1,
        duration_seconds=0.41,
        suppressed=False,
        dedup_hit=False,
        cleared_count=0,
        model_name="ha-agent",
    )

    client = TestClient(create_core_app())
    response = client.get("/v1/network/health")

    assert response.status_code == 200
    payload = response.json()
    assessment = payload["ai_assessment"]
    assert assessment["status"] == "active"
    assert assessment["headline"] == "AI assessment: a router appears isolated from the main mesh"
    assert assessment["verdict"] == "investigate"
    assert assessment["finding_type"] == "partition_anomaly"
    assert assessment["node_eui64"] == "dddddddddddddddd"
    assert assessment["suggested_starter_prompt"] == "Why does one router look isolated from the main partition?"
    assert len(assessment["evidence"]) == 2
    assert assessment["based_on"]["network_health_computed_at"] == payload["computed_at"]
    assert assessment["based_on"]["as_of"] == payload["as_of"]


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