from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from thread_observability.api.http_api import create_core_app
from thread_observability.config import AIConfig, ChatConfig, RetentionConfig, ThreadObsConfig
from thread_observability.services import direct_chat


_PROMPT_FIXTURE = Path(__file__).parent / "fixtures" / "chat_prompt_regression.json"


def _chat_enabled_config(
    *,
    ai: AIConfig | None = None,
    chat: ChatConfig | None = None,
    retention: RetentionConfig | None = None,
) -> ThreadObsConfig:
    return ThreadObsConfig(
        ai=ai
        or AIConfig(
            enabled=True,
            provider="cerebras",
            chat_backend="direct",
            model="llama-4-scout",
            api_key="secret",
        ),
        chat=chat or ChatConfig(enabled=True),
        retention=retention or RetentionConfig(),
    )


def _load_prompt_cases() -> list[dict[str, str]]:
    payload = json.loads(_PROMPT_FIXTURE.read_text(encoding="utf-8"))
    prompts = payload.get("prompts")
    assert isinstance(prompts, list)
    return [row for row in prompts if isinstance(row, dict)]


@pytest.mark.parametrize("case", _load_prompt_cases(), ids=lambda case: str(case["id"]))
def test_chat_prompt_regression_runs_through_api_without_page_context(
    monkeypatch: pytest.MonkeyPatch,
    case: dict[str, str],
) -> None:
    cfg = _chat_enabled_config()
    seen: list[tuple[str, str]] = []

    async def fake_direct_turn(*, target, message: str, rendered_message: str, conversation_id: str | None):  # noqa: ANN001
        seen.append((message, rendered_message))
        assert target.provider == "cerebras"
        assert conversation_id is not None
        assert message == case["prompt"]
        assert rendered_message.endswith(f"User message: {case['prompt']}")
        assert "Page context:" not in rendered_message
        assert "graph_diagnostics" not in rendered_message
        assert "active_tab" not in rendered_message
        return {
            "conversation_id": str(conversation_id),
            "agent_id": target.agent_id,
            "response": {"text": f"handled::{case['id']}", "card": None},
            "tool_calls": [],
            "duration_ms": 1,
            "model": target.model,
            "streaming": False,
        }

    import thread_observability.api.http_api as http_api

    monkeypatch.setattr(http_api, "get_config", lambda: cfg)
    monkeypatch.setattr(direct_chat, "direct_chat_turn", fake_direct_turn)
    client = TestClient(create_core_app())

    response = client.post("/v1/chat/turn", json={"message": case["prompt"]})

    assert response.status_code == 200
    assert response.json()["response"]["text"] == f"handled::{case['id']}"
    assert len(seen) == 1