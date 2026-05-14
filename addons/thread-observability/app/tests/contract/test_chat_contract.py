"""Contract tests for /v1/chat/* responses."""

from __future__ import annotations

import pytest

from thread_observability.api import supervisor_client
from thread_observability.api import http_api
from thread_observability.api.schemas import ChatAgentsResponse, ChatTurnResponse
from thread_observability.config import ChatConfig, ThreadObsConfig


def test_chat_agents_contract(client, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_list_agents() -> dict[str, object]:
        return {
            "count": 1,
            "source": "ws",
            "agents": [{"agent_id": "conversation.claude", "name": "Claude", "source": "ws"}],
        }

    monkeypatch.setattr(supervisor_client, "list_conversation_agents", fake_list_agents)
    monkeypatch.setattr(http_api, "get_config", lambda: ThreadObsConfig(chat=ChatConfig(enabled=True)))
    r = client.get("/v1/chat/agents")
    assert r.status_code == 200
    body = ChatAgentsResponse.model_validate(r.json())
    assert body.count == 1


def test_chat_turn_contract(client, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_process(*, text: str, conversation_id: str | None = None, agent_id: str | None = None) -> dict[str, object]:  # noqa: ARG001
        return {
            "conversation_id": "conv-1",
            "agent_id": "conversation.claude",
            "response": {
                "speech": {"plain": {"speech": "hello from HA"}},
                "data": {"tool_calls": [], "model": "claude-sonnet-4.5"},
            },
        }

    monkeypatch.setattr(supervisor_client, "conversation_process", fake_process)
    monkeypatch.setattr(http_api, "get_config", lambda: ThreadObsConfig(chat=ChatConfig(enabled=True)))
    r = client.post("/v1/chat/turn", json={"message": "hello"})
    assert r.status_code == 200
    body = ChatTurnResponse.model_validate(r.json())
    assert body.response.text == "hello from HA"
    assert body.streaming is False