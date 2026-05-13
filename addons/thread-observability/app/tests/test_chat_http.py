"""Tests for the HA conversation proxy endpoints (#10)."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from thread_observability.api import supervisor_client
from thread_observability.api.http_api import create_core_app


def test_chat_agents_endpoint_returns_agent_list(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_list_agents() -> dict[str, object]:
        return {
            "count": 1,
            "source": "ws",
            "agents": [{"agent_id": "conversation.claude", "name": "Claude", "source": "ws"}],
        }

    monkeypatch.setattr(supervisor_client, "list_conversation_agents", fake_list_agents)
    client = TestClient(create_core_app())

    response = client.get("/v1/chat/agents")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["agents"][0]["agent_id"] == "conversation.claude"


def test_chat_turn_success_shapes_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_process(*, text: str, conversation_id: str | None = None, agent_id: str | None = None) -> dict[str, object]:
        assert "Page context:" in text
        assert conversation_id == "conv-1"
        assert agent_id == "conversation.claude"
        return {
            "conversation_id": "conv-1",
            "agent_id": "conversation.claude",
            "response": {
                "speech": {"plain": {"speech": "Two partitions are present."}},
                "data": {
                    "tool_calls": [{"name": "start_triage"}],
                    "model": "claude-sonnet-4.5",
                },
            },
        }

    monkeypatch.setattr(supervisor_client, "conversation_process", fake_process)
    client = TestClient(create_core_app())

    response = client.post(
        "/v1/chat/turn",
        json={
            "message": "Why are there two partitions right now?",
            "conversation_id": "conv-1",
            "agent_id": "conversation.claude",
            "page_context": {"page": "dashboard"},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"] == "conv-1"
    assert body["response"]["text"] == "Two partitions are present."
    assert body["tool_calls"][0]["name"] == "start_triage"
    assert body["model"] == "claude-sonnet-4.5"
    assert body["streaming"] is False


def test_chat_turn_returns_412_when_no_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_process(*, text: str, conversation_id: str | None = None, agent_id: str | None = None) -> dict[str, object]:  # noqa: ARG001
        raise supervisor_client.NoConversationAgentConfigured("No default agent configured")

    monkeypatch.setattr(supervisor_client, "conversation_process", fake_process)
    client = TestClient(create_core_app())

    response = client.post("/v1/chat/turn", json={"message": "hello"})
    assert response.status_code == 412


def test_chat_turn_returns_502_for_upstream_http_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx.Request("POST", "http://supervisor/core/api/conversation/process")
    upstream = httpx.Response(500, request=request, text="agent crashed")

    async def fake_process(*, text: str, conversation_id: str | None = None, agent_id: str | None = None) -> dict[str, object]:  # noqa: ARG001
        raise httpx.HTTPStatusError("boom", request=request, response=upstream)

    monkeypatch.setattr(supervisor_client, "conversation_process", fake_process)
    client = TestClient(create_core_app())

    response = client.post("/v1/chat/turn", json={"message": "hello"})
    assert response.status_code == 502


def test_chat_turn_rejects_streaming_for_now() -> None:
    client = TestClient(create_core_app())
    response = client.post(
        "/v1/chat/turn",
        json={"message": "hello", "streaming": True},
    )
    assert response.status_code == 501