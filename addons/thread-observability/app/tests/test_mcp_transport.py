from __future__ import annotations

import json

from fastapi.testclient import TestClient

from thread_observability.api import mcp_tools
from thread_observability.api.mcp_tools import create_mcp_app


def _decode_sse_bytes(payload: bytes) -> tuple[str, dict[str, object]]:
    text = payload.decode("utf-8")
    lines = [line for line in text.splitlines() if line]
    event_name = next(line.split(": ", 1)[1] for line in lines if line.startswith("event: "))
    data = next(line.split(": ", 1)[1] for line in lines if line.startswith("data: "))
    return event_name, json.loads(data)


def test_mcp_sse_endpoint_event_and_tool_roundtrip(monkeypatch) -> None:
    app = create_mcp_app()
    client = TestClient(app)

    async def fake_dispatch_and_wrap(name: str, arguments: dict[str, object]) -> dict[str, object]:
        assert name == "get_health_snapshot"
        assert arguments == {}
        return {"data": {"status": "ok"}, "meta": {"tool": name}}

    monkeypatch.setattr(mcp_tools, "_dispatch_and_wrap", fake_dispatch_and_wrap)

    session_id, queue, endpoint_payload = app.state.register_mcp_sse_session()
    event_name, payload = _decode_sse_bytes(app.state.encode_mcp_sse_event("endpoint", endpoint_payload))
    assert event_name == "endpoint"
    assert payload["session_id"] == session_id
    assert payload["url"] == f"/mcp/messages/{session_id}"

    posted = client.post(
        payload["url"],
        json={
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "get_health_snapshot", "arguments": {}},
        },
    )
    assert posted.status_code == 202

    queued = queue.get_nowait()
    assert queued["id"] == 7
    assert queued["result"]["content"][0]["type"] == "text"
    result = json.loads(queued["result"]["content"][0]["text"])
    assert result["data"]["status"] == "ok"


def test_mcp_sse_sessions_are_isolated() -> None:
    app = create_mcp_app()
    client = TestClient(app)

    session_a, queue_a, payload_a = app.state.register_mcp_sse_session()
    session_b, queue_b, payload_b = app.state.register_mcp_sse_session()
    assert payload_a["url"] == f"/mcp/messages/{session_a}"
    assert payload_b["url"] == f"/mcp/messages/{session_b}"

    post_a = client.post(
        payload_a["url"],
        json={"jsonrpc": "2.0", "id": 101, "method": "initialize", "params": {}},
    )
    post_b = client.post(
        payload_b["url"],
        json={"jsonrpc": "2.0", "id": 202, "method": "initialize", "params": {}},
    )
    assert post_a.status_code == 202
    assert post_b.status_code == 202

    body_a = queue_a.get_nowait()
    body_b = queue_b.get_nowait()
    assert body_a["id"] == 101
    assert body_b["id"] == 202
    assert queue_a.empty()
    assert queue_b.empty()
    assert body_a["result"]["transport"]["sse"] == "/mcp/sse"
    assert body_b["result"]["transport"]["sse"] == "/mcp/sse"


def test_mcp_streamable_http_keeps_legacy_jsonrpc_contract() -> None:
    app = create_mcp_app()
    client = TestClient(app)
    request = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}

    legacy = client.post("/mcp", json=request)
    streamable = client.post("/mcp/stream", json=request)

    assert legacy.status_code == 200
    assert streamable.status_code == 200
    legacy_payload = legacy.json()["result"]
    streamable_payload = streamable.json()["result"]
    assert legacy_payload["protocolVersion"] == streamable_payload["protocolVersion"]
    assert legacy_payload["capabilities"] == streamable_payload["capabilities"]
    assert streamable_payload["transport"]["streamable_http_post"] == "/mcp/stream"
