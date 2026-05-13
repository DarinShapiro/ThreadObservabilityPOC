"""HTTP API tests for assessment history + run-now endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient

from thread_observability.api import http_api
from thread_observability.api.http_api import create_core_app
from thread_observability.config import AssessmentConfig, ThreadObsConfig


def test_assessment_history_endpoint_paginates(store) -> None:
    for idx in range(3):
        store.record_assessment_run(
            verdict="ok",
            severity="watch",
            confidence=0.5,
            headline=f"run {idx}",
        )

    client = TestClient(create_core_app())
    payload = client.get("/v1/assessment/history?limit=2&offset=0").json()
    assert payload["count"] == 2
    assert payload["has_more"] is True
    assert len(payload["runs"]) == 2


def test_assessment_state_endpoint_exposes_scheduler_fields(monkeypatch) -> None:
    cfg = ThreadObsConfig(assessment=AssessmentConfig(enabled=True))
    monkeypatch.setattr(http_api, "get_config", lambda: cfg)

    client = TestClient(create_core_app())
    payload = client.get("/v1/assessment/state").json()

    assert payload["enabled"] is True
    assert "last_check_at" in payload
    assert "next_check_at" in payload
    assert "calls_today" in payload
    assert "daily_budget" in payload
    assert "probation_checks_remaining" in payload
    assert payload["reason"]


def test_assessment_run_now_endpoint_executes_when_enabled(store, monkeypatch) -> None:
    cfg = ThreadObsConfig(assessment=AssessmentConfig(enabled=True))
    monkeypatch.setattr(http_api, "get_config", lambda: cfg)

    client = TestClient(create_core_app())
    payload = client.post(
        "/v1/assessment/run-now",
        json={"model": "claude-sonnet-4-5"},
    ).json()

    assert payload["ok"] is True
    assert payload["result"]["envelope"]["finding_type"] == "no_agent"
    history = store.list_assessment_runs(limit=5)
    assert len(history) == 1
    assert history[0]["model_name"] == "claude-sonnet-4-5"