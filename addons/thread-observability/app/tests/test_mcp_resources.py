from __future__ import annotations

from fastapi.testclient import TestClient

from thread_observability.api.mcp_tools import RESOURCE_DEFS, create_mcp_app


def test_mcp_resources_rest_list_and_read() -> None:
    client = TestClient(create_mcp_app())

    listed = client.get("/mcp/resources")
    assert listed.status_code == 200
    payload = listed.json()
    assert payload["count"] == len(RESOURCE_DEFS)
    assert any(resource["name"] == "glossary" for resource in payload["resources"])

    glossary = client.get("/mcp/resources/glossary")
    assert glossary.status_code == 200
    glossary_payload = glossary.json()
    assert glossary_payload["resource"]["uri"] == "thread-observability://glossary"
    assert "Thread Observability Glossary" in glossary_payload["contents"]


def test_mcp_jsonrpc_resources_support() -> None:
    client = TestClient(create_mcp_app())

    initialize = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert initialize.status_code == 200
    init_payload = initialize.json()["result"]
    assert "resources" in init_payload["capabilities"]

    listed = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "resources/list", "params": {}})
    assert listed.status_code == 200
    resources = listed.json()["result"]["resources"]
    assert any(resource["uri"] == "thread-observability://glossary" for resource in resources)

    read = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "resources/read",
            "params": {"uri": "thread-observability://glossary"},
        },
    )
    assert read.status_code == 200
    contents = read.json()["result"]["contents"]
    assert contents[0]["uri"] == "thread-observability://glossary"
    assert "Matter" in contents[0]["text"]
