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


async def _post(path: str, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        # Send an empty JSON body by default if none provided; Supervisor expects this.
        body = json_body if json_body is not None else {}
        resp = await client.post(
            f"{SUPERVISOR_URL}{path}",
            headers=_headers(),
            json=body,
        )
        resp.raise_for_status()
        try:
            return resp.json()
        except Exception:
            return {"status": "ok"}


async def _post_self_disruptive(path: str, action: str) -> dict[str, Any]:
    """POST to a Supervisor endpoint that may stop/restart this add-on.

    For operations like restart/rebuild, the current process can terminate
    before the HTTP response fully arrives. Treat transport disconnects as
    accepted rather than hard failures.
    """
    try:
        return await _post(path)
    except httpx.RequestError as exc:
        return {
            "status": "accepted",
            "action": action,
            "note": "request interrupted after dispatch; expected for self lifecycle action",
            "error": exc.__class__.__name__,
        }


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


async def get_addon_logs(lines: int = 200, slug: str | None = None) -> list[str]:
    """Return the last *lines* lines of an add-on's container log.

    If slug is None, returns logs for this add-on (self). Otherwise fetches logs
    for the add-on with the given slug.
    """
    path = f"/addons/{slug}/logs" if slug else "/addons/self/logs"
    text = await _get_text(path)
    return text.splitlines()[-lines:]


async def get_supervisor_logs(lines: int = 200) -> list[str]:
    """Return the last *lines* lines of the Supervisor log."""
    text = await _get_text("/supervisor/logs")
    return text.splitlines()[-lines:]


async def restart_addon() -> dict[str, Any]:
    """Restart this add-on via Supervisor (fast; does not rebuild image)."""
    return await _post_self_disruptive("/addons/self/restart", action="restart")


async def rebuild_addon() -> dict[str, Any]:
    """Rebuild this add-on from its repository source, then restart."""
    return await _post_self_disruptive("/addons/self/rebuild", action="rebuild")


async def reload_store() -> dict[str, Any]:
    """Force Supervisor to re-scan all add-on repositories.

    Use this right after pushing a new version to the upstream repo so
    Supervisor sees the new ``config.yaml`` version without waiting for its
    next periodic poll.
    """
    return await _post("/store/reload")


async def get_core_info() -> dict[str, Any]:
    """Return Supervisor's view of Home Assistant Core (version, state, arch)."""
    return await _get_json("/core/info")


async def get_supervisor_info() -> dict[str, Any]:
    """Return Supervisor self-info (version, arch, channel, timezone)."""
    return await _get_json("/supervisor/info")


async def check_for_update() -> dict[str, Any]:
    """Reload the store, then report current vs latest version for this add-on.

    Returns ``{current, latest, update_available, auto_update, state}``.
    """
    try:
        await reload_store()
    except Exception:  # noqa: BLE001
        # Store reload is best-effort; we still want the current info.
        pass
    info = await _get_json("/addons/self/info")
    return {
        "current": info.get("version"),
        "latest": info.get("version_latest"),
        "update_available": info.get("update_available"),
        "auto_update": info.get("auto_update"),
        "state": info.get("state"),
    }


async def _resolve_store_slug(self_slug: str) -> tuple[str, str | None]:
    """Resolve the Supervisor-store slug for this add-on.

    ``/addons/self/info`` returns the *installed* slug, which on a local
    repo includes the repo hash prefix (e.g. ``9e5048e8_thread-observability``).
    The store-side endpoint ``/store/addons/{slug}/update`` expects the
    *store* slug, which is the entry's ``slug`` field as advertised by
    ``/store/addons``. These can differ across Supervisor versions, and
    using the wrong one has been observed to cause Supervisor to interpret
    the call as a fresh install of a non-existent add-on, silently clearing
    the installed instance.

    Returns ``(store_slug, repository_slug)``. Falls back to ``self_slug``
    if the store listing cannot be reached or no match is found, with
    ``repository_slug`` set to ``None``.
    """
    try:
        store = await _get_json("/store/addons")
    except Exception:  # noqa: BLE001
        return self_slug, None
    addons = store.get("addons") if isinstance(store, dict) else None
    if not isinstance(addons, list):
        return self_slug, None
    # Strategy: match by installed=True + identical version, else by name
    # suffix (drop the repo-hash prefix), else by exact slug.
    suffix = self_slug.split("_", 1)[1] if "_" in self_slug else self_slug
    candidates: list[dict[str, Any]] = []
    for entry in addons:
        if not isinstance(entry, dict):
            continue
        if entry.get("slug") == self_slug or entry.get("slug") == suffix:
            candidates.append(entry)
    # Prefer installed=True entries.
    candidates.sort(key=lambda e: (not e.get("installed", False), e.get("slug", "")))
    if candidates:
        winner = candidates[0]
        return winner.get("slug", self_slug), winner.get("repository")
    return self_slug, None


def _addon_update_entity_id(name: str | None, slug: str) -> str:
    """Compute the HA ``update.*`` entity id for a Supervisor add-on.

    Home Assistant's ``hassio`` integration creates one entity per add-on
    named ``update.<slugified_name>_update``. For "Thread Observability"
    this is ``update.thread_observability_update``. The slugifier mirrors
    HA Core's: lowercased, non-alphanumerics replaced by underscores,
    collapsed/trimmed.
    """
    import re
    source = (name or slug or "addon").lower()
    s = re.sub(r"[^a-z0-9]+", "_", source).strip("_")
    return f"update.{s}_update"


async def _post_ha_core_service(
    domain: str,
    service: str,
    data: dict[str, Any],
    token: str,
    *,
    timeout: float = 30.0,
) -> tuple[int, str]:
    """POST a service call directly to HA Core, NOT through Supervisor.

    Supervisor's API security middleware blacklists ``/core/api/hassio/...``
    and strips/replaces ``Authorization`` on its ``/core`` proxy, so we
    cannot use the user's admin token through Supervisor. Instead we hit
    HA Core directly at ``http://homeassistant:8123`` (the standard
    hassio Docker-network alias for the HA Core container). The user's
    long-lived admin token authenticates as that admin user, which has
    permission to call the modern ``update.install`` service.

    The token is never logged or surfaced in tool output.
    """
    base = os.getenv("HA_CORE_URL", "http://homeassistant.local.hass.io:8123")
    url = f"{base}/api/services/{domain}/{service}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, json=data)
        body = ""
        try:
            body = resp.text
        except Exception:  # noqa: BLE001
            body = ""
        return resp.status_code, body


async def update_addon(dry_run: bool = False) -> dict[str, Any]:
    """Update this add-on to the latest version available in the store.

    **Supervisor self-update is forbidden via every in-process path.**
    Empirical findings from inside the add-on container:

    1. ``POST /store/addons/{slug}/update`` (Supervisor direct):
       ``403 "App {slug} can't update itself!"`` \u2014 self-update guard.
    2. ``POST /core/api/services/hassio/addon_update`` (HA Core service):
       ``400`` \u2014 the service does not exist on modern HA (the
       ``hassio`` domain only registers start/stop/restart, not
       ``addon_update``).
    3. ``POST /core/api/hassio/addons/{slug}/update`` (HA Core hassio
       HTTP proxy through Supervisor): blocked by Supervisor's API
       security middleware: ``".../update is blacklisted!"``.

    **Working path (Home Assistant 2024.6+).** Each Supervisor-managed
    add-on now has a per-add-on ``update.<name>_update`` entity. Calling
    the ``update.install`` service on that entity triggers the update
    through the modern integration plumbing, NOT through any of the
    blacklisted paths.

    This must be called with an admin user's long-lived access token,
    supplied via the ``ha_admin_token`` add-on option. We POST directly
    to HA Core (``http://homeassistant:8123``), bypassing Supervisor's
    proxy entirely so the token reaches HA Core unmodified.

    If ``ha_admin_token`` is not set, we fall back to the architectural
    workaround: force a store reload, enable ``auto_update``, and report
    ``status="queued"`` so the caller knows Supervisor's sweep will land
    the new version on its (uncontrollable) schedule.

    With ``dry_run=True``, returns the resolution + planned request
    without actually invoking ``update.install``.
    """
    from ..config import get_config  # lazy import to avoid circulars

    try:
        await reload_store()
    except Exception:  # noqa: BLE001
        pass

    info = await _get_json("/addons/self/info")
    self_slug = info.get("slug")
    if not self_slug:
        raise RuntimeError("could not determine addon slug from /addons/self/info")

    store_slug, repository = await _resolve_store_slug(self_slug)
    current = info.get("version")
    latest = info.get("version_latest")
    update_available = bool(info.get("update_available"))
    auto_update_now = bool(info.get("auto_update"))
    addon_name = info.get("name")
    entity_id = _addon_update_entity_id(addon_name, store_slug)

    cfg = get_config()
    has_admin_token = bool(cfg.ha_admin_token)

    base = {
        "action": "update",
        "self_slug": self_slug,
        "store_slug": store_slug,
        "repository": repository,
        "current": current,
        "latest": latest,
        "update_available": update_available,
        "auto_update": auto_update_now,
        "entity_id": entity_id,
        "via": "ha_core_update_install" if has_admin_token else "auto_update_queue",
        "ha_admin_token_configured": has_admin_token,
    }

    if dry_run:
        return {**base, "status": "dry_run", "performed": False}

    if not update_available:
        return {
            **base,
            "status": "already_latest",
            "performed": False,
            "note": "No update available; nothing to do.",
        }

    # --- Primary path: HA Core update.install via admin token. --------
    if has_admin_token:
        try:
            status, body = await _post_ha_core_service(
                "update", "install",
                {"entity_id": entity_id},
                cfg.ha_admin_token,
            )
        except httpx.RequestError as exc:
            return {
                **base,
                "status": "transport_error",
                "performed": "unknown",
                "error_class": exc.__class__.__name__,
                "error": str(exc),
                "note": (
                    "POST to HA Core /api/services/update/install was "
                    "interrupted. Supervisor may have begun the update "
                    "before the response completed (this is the normal "
                    "success signature when the add-on restarts itself "
                    "mid-call). Poll ha_get_addon_state to confirm."
                ),
            }
        if status in (200, 201):
            return {
                **base,
                "status": "ok",
                "performed": True,
                "http_status": status,
                "note": (
                    "HA Core accepted update.install on entity "
                    f"{entity_id}. Supervisor will pull/build the new "
                    "image and restart this add-on asynchronously \u2014 the "
                    "MCP connection will drop shortly. Poll "
                    "ha_get_addon_state to confirm the new version."
                ),
            }
        # Non-2xx \u2014 fall through to error and *then* the queued fallback.
        return {
            **base,
            "status": "http_error",
            "performed": False,
            "http_status": status,
            "response_body": body[:500],
            "note": (
                "HA Core rejected update.install. Verify (1) ha_admin_token "
                "is a long-lived access token for an admin user, (2) "
                "HA Core is reachable at HA_CORE_URL (default "
                "http://homeassistant.local.hass.io:8123), (3) the entity "
                f"{entity_id} exists \u2014 query /api/states to confirm."
            ),
        }

    # --- Fallback: ensure auto_update on, then return 'queued'. -------
    auto_update_set: dict[str, Any] = {"changed": False, "now": auto_update_now}
    if not auto_update_now:
        try:
            await set_auto_update(True)
            auto_update_set = {"changed": True, "now": True}
        except Exception as exc:  # noqa: BLE001
            auto_update_set = {
                "changed": False, "now": auto_update_now, "error": str(exc),
            }

    return {
        **base,
        "status": "queued",
        "performed": False,
        "auto_update_set": auto_update_set,
        "note": (
            "No ha_admin_token configured, so we cannot call HA Core's "
            "update.install service directly. Store has been reloaded and "
            "auto_update is ON; Supervisor's periodic sweep will land the "
            "new version. To enable immediate self-update via MCP, set the "
            "'ha_admin_token' option to a long-lived access token for an "
            "admin HA user (Profile \u2192 Security \u2192 Long-lived Access Tokens)."
        ),
    }



async def set_auto_update(enabled: bool) -> dict[str, Any]:
    """Toggle Supervisor's auto-update flag for this add-on."""
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        resp = await client.post(
            f"{SUPERVISOR_URL}/addons/self/options",
            headers=_headers(),
            json={"auto_update": bool(enabled)},
        )
        resp.raise_for_status()
        try:
            return resp.json()
        except Exception:
            return {"status": "ok", "auto_update": bool(enabled)}


async def reinstall_addon(slug: str) -> dict[str, Any]:
    """Uninstall then reinstall this add-on by slug.

    WARNING: this terminates the process making the call. The HTTP response
    will be cut off mid-flight once Supervisor stops the container. Caller
    should treat a connection-reset as the expected success signal and poll
    ``get_addon_info`` afterwards to confirm the new install.
    """
    async with httpx.AsyncClient(timeout=120.0) as client:
        # /addons/self/uninstall works while we're still running.
        await client.post(
            f"{SUPERVISOR_URL}/addons/self/uninstall", headers=_headers()
        )
        # After uninstall the "self" alias is gone; use the slug.
        resp = await client.post(
            f"{SUPERVISOR_URL}/store/addons/{slug}/install",
            headers=_headers(),
        )
        resp.raise_for_status()
        try:
            return resp.json()
        except Exception:
            return {"status": "ok", "action": "reinstall", "slug": slug}


_THREAD_DATASETS_CACHE: dict[str, Any] = {"expires_at": 0.0, "data": None}
_THREAD_DATASETS_TTL_S = 300.0


async def list_thread_datasets() -> dict[str, Any]:
    """Return the Thread Border Router credential datasets known to HA.

    Uses the Home Assistant Core WebSocket API (``thread/list_datasets``)
    via the Supervisor proxy at ``ws://supervisor/core/websocket``. The
    ``SUPERVISOR_TOKEN`` doubles as a long-lived HA access token through
    that proxy.

    Cached for 5 minutes — datasets change rarely and the WS handshake is
    not free. Required so a consultant can correlate a node's
    ``extended_pan_id`` (now persisted per-node in v0.9.46) against the
    credentials HA still has on file.
    """
    import json as _json  # noqa: PLC0415
    import time as _time  # noqa: PLC0415
    from datetime import UTC as _UTC, datetime as _dt  # noqa: PLC0415

    now_mono = _time.monotonic()
    cached_data = _THREAD_DATASETS_CACHE.get("data")
    if cached_data is not None and _THREAD_DATASETS_CACHE.get("expires_at", 0.0) > now_mono:
        return {**cached_data, "cached": True}

    try:
        import websockets  # type: ignore[import-not-found]  # noqa: PLC0415
    except ImportError as exc:
        raise SupervisorUnavailable(
            "websockets package not installed; cannot reach HA core WS"
        ) from exc

    token = _token()
    ws_url = SUPERVISOR_URL.replace("http://", "ws://").replace("https://", "wss://")
    ws_url = f"{ws_url}/core/websocket"

    datasets: list[dict[str, Any]] = []
    try:
        async with websockets.connect(ws_url, open_timeout=10.0) as ws:
            hello = _json.loads(await ws.recv())
            if hello.get("type") != "auth_required":
                raise SupervisorUnavailable(
                    f"unexpected HA WS greeting: {hello.get('type')!r}"
                )
            await ws.send(_json.dumps({"type": "auth", "access_token": token}))
            auth_resp = _json.loads(await ws.recv())
            if auth_resp.get("type") != "auth_ok":
                raise SupervisorUnavailable(
                    f"HA WS auth failed: {auth_resp.get('message') or auth_resp}"
                )
            await ws.send(_json.dumps({"id": 1, "type": "thread/list_datasets"}))
            result = _json.loads(await ws.recv())
            if not result.get("success"):
                err = result.get("error") or {}
                raise SupervisorUnavailable(
                    f"thread/list_datasets failed: {err.get('message') or err}"
                )
            payload = result.get("result") or {}
            raw = payload.get("datasets") if isinstance(payload, dict) else payload
            if isinstance(raw, list):
                for d in raw:
                    if not isinstance(d, dict):
                        continue
                    epid = d.get("extended_pan_id")
                    if isinstance(epid, str):
                        epid_norm = epid.lower().removeprefix("0x").rjust(16, "0")
                    else:
                        epid_norm = None
                    datasets.append({
                        "dataset_id": d.get("dataset_id"),
                        "preferred": bool(d.get("preferred")),
                        "preferred_border_agent_id": d.get("preferred_border_agent_id"),
                        "network_name": d.get("network_name"),
                        "extended_pan_id": epid_norm,
                        "extended_pan_id_raw": d.get("extended_pan_id"),
                        "channel": d.get("channel"),
                        "pan_id": d.get("pan_id"),
                        "source": d.get("source"),
                        "created": d.get("created"),
                    })
    except SupervisorUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SupervisorUnavailable(
            f"could not reach HA core WS at {ws_url}: {exc}"
        ) from exc

    result_obj = {
        "datasets": datasets,
        "count": len(datasets),
        "fetched_at": _dt.now(tz=_UTC).isoformat(),
        "cache_ttl_seconds": int(_THREAD_DATASETS_TTL_S),
    }
    _THREAD_DATASETS_CACHE["data"] = dict(result_obj)
    _THREAD_DATASETS_CACHE["expires_at"] = now_mono + _THREAD_DATASETS_TTL_S
    return {**result_obj, "cached": False}


async def get_ha_device_registry() -> list[dict[str, Any]]:
    """Attempt to fetch Home Assistant's device registry via REST API.

    Requires ``homeassistant_api: true`` in config.yaml and valid HA token.
    Falls back to empty list if HA is unreachable or returns no devices.
    """
    try:
        # Try to reach HA's REST API directly (assumes HA is accessible on network).
        # This is best-effort; if it fails, we fall back to local SQLite metadata.
        ha_url = os.getenv("HOMEASSISTANT_URL", "http://homeassistant.local:8123")
        ha_token = os.getenv("HOMEASSISTANT_TOKEN", "")
        if not ha_token:
            return []
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{ha_url}/api/config/device_registry/list",
                headers={"Authorization": f"Bearer {ha_token}", "Accept": "application/json"},
            )
            if resp.status_code == 200:
                devices = resp.json()
                return devices if isinstance(devices, list) else []
    except Exception:
        pass
    return []


