"""Thin async client for the Home Assistant Supervisor REST API.

Used by MCP tools to give VS Code a live view into add-on state and logs
without manual UI round-trips. Requires the add-on to be granted
``hassio_api: true`` (and for privileged operations ``hassio_role: manager``)
in ``config.yaml``.

The Supervisor injects ``SUPERVISOR_TOKEN`` and exposes its API at
``http://supervisor``.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

SUPERVISOR_URL = os.getenv("SUPERVISOR_URL", "http://supervisor")
SUPERVISOR_TOKEN_ENV = "SUPERVISOR_TOKEN"
DEFAULT_TIMEOUT = 10.0


class SupervisorUnavailable(RuntimeError):
    """Raised when the Supervisor API cannot be reached or auth is missing."""


def _token() -> str:
    token = os.getenv(SUPERVISOR_TOKEN_ENV)
    if not token:
        raise SupervisorUnavailable(
            f"{SUPERVISOR_TOKEN_ENV} not set; running outside Supervisor?"
        )
    return token


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_token()}",
        "Accept": "application/json",
    }


async def _get_json(path: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        resp = await client.get(f"{SUPERVISOR_URL}{path}", headers=_headers())
        resp.raise_for_status()
        payload = resp.json()
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload if isinstance(payload, dict) else {"value": payload}


async def _get_text(path: str) -> str:
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        resp = await client.get(
            f"{SUPERVISOR_URL}{path}",
            headers={"Authorization": f"Bearer {_token()}", "Accept": "text/plain"},
        )
        resp.raise_for_status()
        return resp.text


async def _post(path: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(f"{SUPERVISOR_URL}{path}", headers=_headers())
        resp.raise_for_status()
        try:
            return resp.json()
        except Exception:
            return {"status": "ok"}


async def get_addon_info() -> dict[str, Any]:
    """Return Supervisor's view of this add-on (state, version, boot, etc.)."""
    info = await _get_json("/addons/self/info")
    # Surface the most useful fields up top for AI consumption.
    summary_keys = (
        "name", "slug", "version", "version_latest", "update_available",
        "state", "boot", "auto_update", "watchdog", "ingress", "ingress_url",
        "hostname", "available", "protected", "stage",
    )
    summary = {k: info[k] for k in summary_keys if k in info}
    return {"summary": summary, "raw": info}


async def get_addon_logs(lines: int = 200) -> list[str]:
    """Return the last *lines* lines of the add-on's container log."""
    text = await _get_text("/addons/self/logs")
    return text.splitlines()[-lines:]


async def get_supervisor_logs(lines: int = 200) -> list[str]:
    """Return the last *lines* lines of the Supervisor log."""
    text = await _get_text("/supervisor/logs")
    return text.splitlines()[-lines:]


async def restart_addon() -> dict[str, Any]:
    """Restart this add-on via Supervisor (fast; does not rebuild image)."""
    return await _post("/addons/self/restart")


async def rebuild_addon() -> dict[str, Any]:
    """Rebuild this add-on from its repository source, then restart."""
    return await _post("/addons/self/rebuild")
