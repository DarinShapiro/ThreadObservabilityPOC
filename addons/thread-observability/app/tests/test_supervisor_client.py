"""Tests for Supervisor API client edge cases used by MCP tools.

After 0.9.53, ``update_addon`` has two paths:

* If ``ha_admin_token`` is set in addon options, it calls HA Core's
  ``update.install`` service directly on the per-addon update entity,
  bypassing Supervisor entirely. This is the only path that actually
  triggers an update from inside the addon (Supervisor blacklists every
  in-process self-update endpoint).
* Otherwise it falls back to forcing ``auto_update=true`` and returning
  ``status="queued"`` so Supervisor's periodic sweep lands the update.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from thread_observability.api import supervisor_client as sc


def test_list_conversation_agents_falls_back_to_entities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_ws(command: str, *, timeout: float = 10.0) -> object:  # noqa: ARG001
        raise RuntimeError("ws unavailable")

    async def fake_core_get(path: str, *, timeout: float = sc.DEFAULT_TIMEOUT) -> object:  # noqa: ARG001
        assert path == "/core/api/states"
        return [
            {
                "entity_id": "conversation.claude",
                "attributes": {"friendly_name": "Claude"},
            },
            {"entity_id": "light.kitchen", "attributes": {}},
        ]

    monkeypatch.setattr(sc, "_core_ws_command", fake_ws)
    monkeypatch.setattr(sc, "_core_get_json", fake_core_get)

    result = asyncio.run(sc.list_conversation_agents())
    assert result["count"] == 1
    assert result["agents"][0]["agent_id"] == "conversation.claude"
    assert result["source"] == "entity_scan"


def test_conversation_process_raises_no_agent_for_agent_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = httpx.Request("POST", "http://supervisor/core/api/conversation/process")
    response = httpx.Response(400, request=request, text="No default agent configured")

    async def fake_core_post(path: str, json_body: dict[str, object], *, timeout: float = 60.0) -> object:  # noqa: ARG001
        raise httpx.HTTPStatusError("boom", request=request, response=response)

    monkeypatch.setattr(sc, "_core_post_json", fake_core_post)

    with pytest.raises(sc.NoConversationAgentConfigured):
        asyncio.run(sc.conversation_process(text="hello"))


def _patch_config(monkeypatch: pytest.MonkeyPatch, ha_admin_token: str = "") -> None:
    """Make ``get_config()`` (lazy-imported inside update_addon) return a stub."""
    import thread_observability.config as cfg_mod

    def fake_get_config() -> SimpleNamespace:
        return SimpleNamespace(ha_admin_token=ha_admin_token)

    monkeypatch.setattr(cfg_mod, "get_config", fake_get_config)


def test_update_addon_no_update_available(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)

    async def fake_reload_store() -> dict[str, str]:
        return {"status": "ok"}

    async def fake_get_json(path: str) -> dict[str, object]:
        if path == "/addons/self/info":
            return {
                "slug": "9e5048e8_thread-observability",
                "name": "Thread Observability",
                "version": "0.9.1",
                "version_latest": "0.9.1",
                "update_available": False,
                "auto_update": True,
            }
        if path == "/store/addons":
            return {"addons": [
                {"slug": "thread-observability", "installed": True, "repository": "9e5048e8"},
            ]}
        raise AssertionError(f"unexpected GET: {path}")

    monkeypatch.setattr(sc, "reload_store", fake_reload_store)
    monkeypatch.setattr(sc, "_get_json", fake_get_json)

    result = asyncio.run(sc.update_addon())
    assert result["status"] == "already_latest"
    assert result["performed"] is False
    assert result["current"] == "0.9.1"
    assert result["latest"] == "0.9.1"
    assert result["store_slug"] == "thread-observability"
    assert result["entity_id"] == "update.thread_observability_update"


def test_update_addon_with_token_calls_ha_core_update_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, object], str]] = []
    _patch_config(monkeypatch, ha_admin_token="LLT_TEST_TOKEN")

    async def fake_reload_store() -> dict[str, str]:
        return {"status": "ok"}

    async def fake_get_json(path: str) -> dict[str, object]:
        if path == "/addons/self/info":
            return {
                "slug": "9e5048e8_thread-observability",
                "name": "Thread Observability",
                "version": "0.9.52",
                "version_latest": "0.9.53",
                "update_available": True,
                "auto_update": True,
            }
        if path == "/store/addons":
            return {"addons": [
                {"slug": "thread-observability", "installed": True, "repository": "9e5048e8"},
            ]}
        raise AssertionError(f"unexpected GET: {path}")

    async def fake_post_service(
        domain: str, service: str, data: dict[str, object], token: str,
        *, timeout: float = 30.0,
    ) -> tuple[int, str]:
        calls.append((domain, service, data, token))
        return 200, "[]"

    monkeypatch.setattr(sc, "reload_store", fake_reload_store)
    monkeypatch.setattr(sc, "_get_json", fake_get_json)
    monkeypatch.setattr(sc, "_post_ha_core_service", fake_post_service)

    result = asyncio.run(sc.update_addon())
    assert calls == [(
        "update", "install",
        {"entity_id": "update.thread_observability_update"},
        "LLT_TEST_TOKEN",
    )]
    assert result["status"] == "ok"
    assert result["performed"] is True
    assert result["http_status"] == 200
    assert result["via"] == "ha_core_update_install"
    # Token must never leak into the result.
    assert "LLT_TEST_TOKEN" not in str(result)


def test_update_addon_without_token_queues_via_auto_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_config(monkeypatch, ha_admin_token="")
    auto_update_calls: list[bool] = []

    async def fake_reload_store() -> dict[str, str]:
        return {"status": "ok"}

    async def fake_get_json(path: str) -> dict[str, object]:
        if path == "/addons/self/info":
            return {
                "slug": "9e5048e8_thread-observability",
                "name": "Thread Observability",
                "version": "0.9.52",
                "version_latest": "0.9.53",
                "update_available": True,
                "auto_update": False,
            }
        if path == "/store/addons":
            return {"addons": [
                {"slug": "thread-observability", "installed": True, "repository": "9e5048e8"},
            ]}
        raise AssertionError(f"unexpected GET: {path}")

    async def fake_set_auto_update(enabled: bool) -> dict[str, object]:
        auto_update_calls.append(enabled)
        return {"status": "ok"}

    async def should_not_post_service(*args: object, **kwargs: object) -> tuple[int, str]:
        raise AssertionError("must not call HA Core when token absent")

    monkeypatch.setattr(sc, "reload_store", fake_reload_store)
    monkeypatch.setattr(sc, "_get_json", fake_get_json)
    monkeypatch.setattr(sc, "set_auto_update", fake_set_auto_update)
    monkeypatch.setattr(sc, "_post_ha_core_service", should_not_post_service)

    result = asyncio.run(sc.update_addon())
    assert result["status"] == "queued"
    assert result["performed"] is False
    assert result["via"] == "auto_update_queue"
    assert result["ha_admin_token_configured"] is False
    assert auto_update_calls == [True]
    assert result["auto_update_set"]["changed"] is True


def test_update_addon_dry_run_does_not_post(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch, ha_admin_token="LLT_TEST_TOKEN")

    async def fake_reload_store() -> dict[str, str]:
        return {"status": "ok"}

    async def fake_get_json(path: str) -> dict[str, object]:
        if path == "/addons/self/info":
            return {
                "slug": "9e5048e8_thread-observability",
                "name": "Thread Observability",
                "version": "0.9.52",
                "version_latest": "0.9.53",
                "update_available": True,
                "auto_update": True,
            }
        if path == "/store/addons":
            return {"addons": [
                {"slug": "thread-observability", "installed": True, "repository": "9e5048e8"},
            ]}
        raise AssertionError(f"unexpected GET: {path}")

    async def should_not_post_service(*args: object, **kwargs: object) -> tuple[int, str]:
        raise AssertionError("dry_run must not call HA Core")

    monkeypatch.setattr(sc, "reload_store", fake_reload_store)
    monkeypatch.setattr(sc, "_get_json", fake_get_json)
    monkeypatch.setattr(sc, "_post_ha_core_service", should_not_post_service)

    result = asyncio.run(sc.update_addon(dry_run=True))
    assert result["status"] == "dry_run"
    assert result["performed"] is False
    assert result["update_available"] is True
    assert result["entity_id"] == "update.thread_observability_update"
    assert result["via"] == "ha_core_update_install"


def test_update_addon_falls_back_to_self_slug_when_store_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If /store/addons can't be read, fall back to the self_slug rather than raising."""
    _patch_config(monkeypatch)

    async def fake_reload_store() -> dict[str, str]:
        return {"status": "ok"}

    async def fake_get_json(path: str) -> dict[str, object]:
        if path == "/addons/self/info":
            return {
                "slug": "9e5048e8_thread-observability",
                "name": "Thread Observability",
                "version": "0.9.1",
                "version_latest": "0.9.1",
                "update_available": False,
                "auto_update": True,
            }
        if path == "/store/addons":
            raise httpx.ConnectError("supervisor unreachable")
        raise AssertionError(f"unexpected GET: {path}")

    monkeypatch.setattr(sc, "reload_store", fake_reload_store)
    monkeypatch.setattr(sc, "_get_json", fake_get_json)

    result = asyncio.run(sc.update_addon(dry_run=True))
    assert result["store_slug"] == "9e5048e8_thread-observability"
    # The slugified addon name still produces a stable entity_id
    # regardless of what the store slug resolved to.
    assert result["entity_id"] == "update.thread_observability_update"


def test_update_addon_transport_error_is_not_silent_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Transport errors during update.install must be surfaced, not coerced to silent success."""
    _patch_config(monkeypatch, ha_admin_token="LLT_TEST_TOKEN")

    async def fake_reload_store() -> dict[str, str]:
        return {"status": "ok"}

    async def fake_get_json(path: str) -> dict[str, object]:
        if path == "/addons/self/info":
            return {
                "slug": "9e5048e8_thread-observability",
                "name": "Thread Observability",
                "version": "0.9.52",
                "version_latest": "0.9.53",
                "update_available": True,
                "auto_update": True,
            }
        if path == "/store/addons":
            return {"addons": [
                {"slug": "thread-observability", "installed": True, "repository": "9e5048e8"},
            ]}
        raise AssertionError(f"unexpected GET: {path}")

    async def fake_post_service(*args: object, **kwargs: object) -> tuple[int, str]:
        request = httpx.Request("POST", "http://homeassistant:8123/api/services/update/install")
        raise httpx.ReadError("connection closed", request=request)

    monkeypatch.setattr(sc, "reload_store", fake_reload_store)
    monkeypatch.setattr(sc, "_get_json", fake_get_json)
    monkeypatch.setattr(sc, "_post_ha_core_service", fake_post_service)

    result = asyncio.run(sc.update_addon())
    assert result["status"] == "transport_error"
    assert result["performed"] == "unknown"
    assert result["error_class"] == "ReadError"
    assert "ha_get_addon_state" in result["note"]


def test_update_addon_http_error_includes_status_and_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_config(monkeypatch, ha_admin_token="LLT_TEST_TOKEN")

    async def fake_reload_store() -> dict[str, str]:
        return {"status": "ok"}

    async def fake_get_json(path: str) -> dict[str, object]:
        if path == "/addons/self/info":
            return {
                "slug": "9e5048e8_thread-observability",
                "name": "Thread Observability",
                "version": "0.9.52",
                "version_latest": "0.9.53",
                "update_available": True,
                "auto_update": True,
            }
        if path == "/store/addons":
            return {"addons": [
                {"slug": "thread-observability", "installed": True, "repository": "9e5048e8"},
            ]}
        raise AssertionError(f"unexpected GET: {path}")

    async def fake_post_service(*args: object, **kwargs: object) -> tuple[int, str]:
        return 401, '{"message": "Unauthorized"}'

    monkeypatch.setattr(sc, "reload_store", fake_reload_store)
    monkeypatch.setattr(sc, "_get_json", fake_get_json)
    monkeypatch.setattr(sc, "_post_ha_core_service", fake_post_service)

    result = asyncio.run(sc.update_addon())
    assert result["status"] == "http_error"
    assert result["http_status"] == 401
    assert "Unauthorized" in result["response_body"]


def test_rebuild_addon_accepts_disconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_post(path: str, json_body: dict[str, object] | None = None) -> dict[str, object]:
        request = httpx.Request("POST", f"http://supervisor{path}")
        raise httpx.ReadError("connection closed", request=request)

    monkeypatch.setattr(sc, "_post", fake_post)

    result = asyncio.run(sc.rebuild_addon())
    assert result["status"] == "accepted"
    assert result["action"] == "rebuild"
    assert "interrupted" in result["note"]
