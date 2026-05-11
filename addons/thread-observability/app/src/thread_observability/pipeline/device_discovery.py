"""Discover Thread device names from Home Assistant's device registry.

Home Assistant maintains a device registry with IEEE addresses for Thread,
Zigbee, and other radio devices. This module fetches that registry and
correlates IEEE addresses with our extracted EUI64 nodes to populate
friendly names and device IDs automatically.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

from ..storage.sqlite_store import SQLiteStore, get_store

log = logging.getLogger(__name__)

SUPERVISOR_URL = os.getenv("SUPERVISOR_URL", "http://supervisor")
SUPERVISOR_TOKEN_ENV = "SUPERVISOR_TOKEN"
DEFAULT_TIMEOUT = 10.0


def _token() -> str:
    token = os.getenv(SUPERVISOR_TOKEN_ENV)
    if not token:
        raise RuntimeError(f"{SUPERVISOR_TOKEN_ENV} not set; running outside Supervisor?")
    return token


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_token()}",
        "Accept": "application/json",
    }


def _normalize_ieee(ieee_str: str) -> str:
    """Normalize IEEE address to 16-char lowercase hex (EUI64 format).

    Handles formats like:
    - c6:b7:7f:58:e5:ac:ee:d4 → c6b77f58e5aceed4
    - c6b77f58e5aceed4 → c6b77f58e5aceed4
    - 0xc6b77f58e5aceed4 → c6b77f58e5aceed4
    """
    # Strip hex prefix if present
    if ieee_str.startswith("0x"):
        ieee_str = ieee_str[2:]
    # Remove colons/dashes
    ieee_str = ieee_str.replace(":", "").replace("-", "")
    return ieee_str.lower().zfill(16)[-16:]


async def fetch_device_registry() -> list[dict[str, Any]]:
    """Fetch the device registry from Home Assistant."""
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(
                f"{SUPERVISOR_URL}/api/config/device_registry",
                headers=_headers(),
            )
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:
        log.warning("Failed to fetch device registry: %s", exc)
        return []

    # The response is typically {"devices": [...]} or a list directly
    if isinstance(payload, dict):
        devices = payload.get("devices", [])
    else:
        devices = payload if isinstance(payload, list) else []
    return devices


def _extract_thread_devices(devices: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Extract Thread devices from registry, keyed by normalized IEEE address.

    Returns a dict mapping EUI64 → {device_id, name, manufacturer, model, ...}
    """
    out: dict[str, dict[str, Any]] = {}
    for dev in devices:
        # Look for connections that contain Thread/IEEE address info
        connections = dev.get("connections", [])
        for conn_type, conn_id in connections:
            # Thread devices typically use "thread" or "zigbee" connection types
            # and have IEEE addresses; some integrations also use "ieee802154"
            if conn_type in ("thread", "zigbee", "ieee802154"):
                try:
                    eui = _normalize_ieee(str(conn_id))
                    out[eui] = {
                        "device_id": dev.get("id"),
                        "name": dev.get("name"),
                        "name_by_user": dev.get("name_by_user"),
                        "manufacturer": dev.get("manufacturer"),
                        "model": dev.get("model"),
                        "area_id": dev.get("area_id"),
                        "primary_config_entry": dev.get("primary_config_entry"),
                    }
                    log.debug(
                        "Found Thread device: eui=%s name=%s",
                        eui,
                        dev.get("name_by_user") or dev.get("name"),
                    )
                except Exception as exc:
                    log.debug("Failed to parse connection %s: %s", conn_id, exc)
    return out


async def discover_and_sync(store: SQLiteStore | None = None) -> dict[str, Any]:
    """Fetch device registry and sync metadata to nodes.

    Returns a summary of matches found and updated.
    """
    s = store or get_store()
    try:
        devices = await fetch_device_registry()
    except Exception as exc:
        log.exception("device discovery failed: %s", exc)
        return {"error": str(exc), "matched": 0, "updated": 0}

    thread_devs = _extract_thread_devices(devices)
    if not thread_devs:
        log.info("No Thread devices found in device registry")
        return {"matched": 0, "updated": 0, "devices": {}}

    # Correlate with our nodes
    nodes = s.list_nodes()
    updated = 0
    matches: dict[str, dict[str, Any]] = {}

    for node in nodes:
        eui = node.get("eui64")
        if not eui:
            continue
        if eui in thread_devs:
            dev = thread_devs[eui]
            # Use name_by_user (user-set) if available, else the auto name
            friendly_name = dev.get("name_by_user") or dev.get("name")
            device_id = dev.get("device_id")
            matches[eui] = {
                "friendly_name": friendly_name,
                "device_id": device_id,
                "manufacturer": dev.get("manufacturer"),
                "model": dev.get("model"),
            }
            # Update the node with metadata
            try:
                s.set_node_metadata(
                    eui64=eui,
                    friendly_name=friendly_name,
                    device_id=device_id,
                )
                updated += 1
                log.info(
                    "Updated node %s with device name '%s'",
                    eui, friendly_name,
                )
            except Exception as exc:
                log.warning("Failed to update node %s: %s", eui, exc)

    log.info(
        "device discovery: scanned %d devices, found %d matches, updated %d nodes",
        len(devices), len(matches), updated,
    )
    return {
        "devices_scanned": len(devices),
        "matched": len(matches),
        "updated": updated,
        "matches": matches,
    }


def discover_and_sync_sync(store: SQLiteStore | None = None) -> dict[str, Any]:
    """Synchronous wrapper for discover_and_sync (for non-async contexts)."""
    return asyncio.run(discover_and_sync(store))
