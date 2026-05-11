"""Discover Thread device names from Home Assistant's device registry.

Home Assistant maintains a device registry with IEEE addresses for Thread,
Zigbee, and other radio devices. This module fetches that registry and
correlates IEEE addresses with our extracted EUI64 nodes to populate
friendly names and device IDs automatically.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from ..storage.sqlite_store import SQLiteStore, get_store

log = logging.getLogger(__name__)

# HA config directory - typically /config in the addon environment
HA_CONFIG_DIR = Path(os.getenv("HA_CONFIG_DIR", "/config"))
DEVICE_REGISTRY_PATH = HA_CONFIG_DIR / ".storage" / "core.device_registry"

# Matter server stores per-node operational data; we use it to bridge
# Matter node_id (present in HA device registry as an identifier) to the
# Thread EUI64 we extract from OTBR. Paths vary by HA version; we try several.
MATTER_STORAGE_CANDIDATES = [
    HA_CONFIG_DIR / "matter_server" / "server.json",
    HA_CONFIG_DIR / "matter_server" / "server_storage.json",
    HA_CONFIG_DIR / ".storage" / "core.matter_server",
]

# Thread-only connection types (we intentionally do NOT include zigbee here).
_THREAD_CONN_TYPES = ("thread", "ieee802154")


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


def _extract_matter_node_id(value: str) -> str | None:
    """Extract a Matter node id from a device-registry identifier value.

    HA Matter devices typically expose identifiers like:
      ["matter", "<fabric_id>-<node_id>"]
      ["matter", "<fabric_id>-<node_id>-<endpoint_id>"]
      ["matter", "<node_id>"]
    We treat the node_id as the segment after the first '-'. If only one
    segment is present, we use it directly. Result is returned as a string
    so it can be used as a dict key consistently.
    """
    if not value:
        return None
    parts = value.split("-")
    if len(parts) >= 2:
        candidate = parts[1]
    else:
        candidate = parts[0]
    candidate = candidate.strip()
    return candidate or None


def _load_matter_node_bridge() -> dict[str, str]:
    """Load a Matter node_id -> Thread EUI64 mapping from matter-server storage.

    Matter server persists per-node operational data including the device's
    Thread/IPv6 addresses. We parse known storage locations defensively; any
    parse failure returns an empty mapping (callers degrade gracefully).
    """
    bridge: dict[str, str] = {}
    for path in MATTER_STORAGE_CANDIDATES:
        try:
            if not path.exists():
                continue
            with open(path, "r") as f:
                data = json.load(f)
        except Exception as exc:  # noqa: BLE001
            log.debug("Matter storage %s unreadable: %s", path, exc)
            continue
        nodes_blob = data.get("nodes") if isinstance(data, dict) else None
        if not isinstance(nodes_blob, dict):
            # HA core storage wraps payload under data.* sometimes
            nodes_blob = (
                (data.get("data") or {}).get("nodes")
                if isinstance(data, dict)
                else None
            )
        if not isinstance(nodes_blob, dict):
            continue
        for node_id, node in nodes_blob.items():
            if not isinstance(node, dict):
                continue
            # Try common spots for an EUI64 / extended address.
            ext = (
                node.get("extended_address")
                or node.get("extendedAddress")
                or (node.get("thread") or {}).get("extended_address")
                or (node.get("network") or {}).get("extended_address")
            )
            if ext:
                try:
                    bridge[str(node_id)] = _normalize_ieee(str(ext))
                    continue
                except Exception:  # noqa: BLE001
                    pass
            # Fallback: derive EUI64 from an operational mesh-local IPv6 IID.
            addrs = (
                node.get("ip_addresses")
                or node.get("addresses")
                or (node.get("thread") or {}).get("addresses")
                or []
            )
            if isinstance(addrs, list):
                for addr in addrs:
                    eui = _eui64_from_ipv6(str(addr))
                    if eui:
                        bridge[str(node_id)] = eui
                        break
        if bridge:
            log.debug(
                "Loaded Matter bridge from %s (%d entries)",
                path, len(bridge),
            )
            break
    return bridge


def _eui64_from_ipv6(addr: str) -> str | None:
    """Derive a 16-hex EUI64 from a Thread mesh IPv6 address if possible."""
    if not addr or ":" not in addr:
        return None
    parts = addr.split(":")
    if len(parts) < 4:
        return None
    last4 = parts[-4:]
    if not all(0 < len(p) <= 4 and all(c in "0123456789abcdefABCDEF" for c in p) for p in last4):
        return None
    try:
        return _normalize_ieee("".join(p.zfill(4) for p in last4))
    except Exception:  # noqa: BLE001
        return None


async def fetch_device_registry() -> list[dict[str, Any]]:
    """Fetch Thread device/node info from OTBR REST API + HA device registry.
    
    The OTBR addon exposes a /api/topology endpoint that returns information
    about all Thread nodes in the network, including their extended addresses (EUI64).
    The HA device registry provides friendly names and device IDs for those nodes.
    
    This function fetches both sources and merges them:
    - OTBR topology: authoritative node list with role and rloc info
    - HA device registry: friendly names and device metadata
    
    Returns a merged list of dicts combining both sources.
    """
    import httpx
    
    # Try OTBR API first for node topology
    otbr_nodes: dict[str, dict[str, Any]] = {}
    otbr_endpoints = [
        "http://supervisor:9203/addon/core_openthread_border_router/api/topology",  # Via Supervisor
        "http://otbr:8080/api/topology",  # Direct if accessible
    ]
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for endpoint in otbr_endpoints:
                try:
                    resp = await client.get(
                        endpoint,
                        headers={"Accept": "application/json"},
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        log.debug(
                            "Thread topology fetched from %s",
                            endpoint,
                        )
                        # Convert OTBR topology response to dict keyed by EUI64
                        if isinstance(data, dict):
                            topology = data.get("topology", {})
                            nodes = topology.get("nodes", [])
                            for node in nodes:
                                ext_addr = node.get("extendedAddress")
                                if ext_addr:
                                    try:
                                        eui = _normalize_ieee(str(ext_addr))
                                        otbr_nodes[eui] = {
                                            "extendedAddress": ext_addr,
                                            "rloc": node.get("rloc"),
                                            "role": node.get("role"),
                                        }
                                    except Exception as exc:
                                        log.debug("Failed to parse OTBR node %s: %s", ext_addr, exc)
                            if otbr_nodes:
                                log.debug("Discovered %d Thread nodes from OTBR topology", len(otbr_nodes))
                                break
                except Exception as exc:
                    log.debug("OTBR endpoint %s failed: %s", endpoint, exc)
                    continue
    except Exception as exc:
        log.warning("Failed to fetch OTBR topology: %s", exc)
    
    # Now fetch device registry to get friendly names and metadata.
    # Thread-only: we no longer match zigbee connections.
    reg_devices = _fallback_device_registry()
    registry_by_eui: dict[str, dict[str, Any]] = {}
    registry_by_matter_node: dict[str, dict[str, Any]] = {}
    for dev in reg_devices:
        dev_meta = {
            "device_id": dev.get("id"),
            "name": dev.get("name"),
            "name_by_user": dev.get("name_by_user"),
            "manufacturer": dev.get("manufacturer"),
            "model": dev.get("model"),
            "area_id": dev.get("area_id"),
            "primary_config_entry": dev.get("primary_config_entry"),
        }
        # Primary path: direct Thread connection on the device.
        connections = dev.get("connections", [])
        matched_thread_conn = False
        for conn_type, conn_id in connections:
            if conn_type in _THREAD_CONN_TYPES:
                try:
                    eui = _normalize_ieee(str(conn_id))
                    registry_by_eui[eui] = dict(dev_meta)
                    matched_thread_conn = True
                    break  # Use first Thread connection found
                except Exception as exc:
                    log.debug("Failed to parse connection %s: %s", conn_id, exc)
        # Secondary path: Matter identifier on the device (we bridge to EUI64 later).
        if not matched_thread_conn:
            for ident in dev.get("identifiers", []) or []:
                # identifiers entries look like ["matter", "<fabric_id>-<node_id>-<endpoint_id>"]
                try:
                    domain, value = ident[0], ident[1]
                except (IndexError, TypeError):
                    continue
                if domain != "matter" or not value:
                    continue
                node_id = _extract_matter_node_id(str(value))
                if node_id is None:
                    continue
                registry_by_matter_node[node_id] = dict(dev_meta)
                log.debug(
                    "Found Matter-only registry device: node_id=%s name=%s",
                    node_id, dev.get("name_by_user") or dev.get("name"),
                )
    if registry_by_matter_node:
        # Bridge Matter node_id -> EUI64 using matter-server storage.
        bridge = _load_matter_node_bridge()
        for node_id, meta in registry_by_matter_node.items():
            eui = bridge.get(node_id)
            if eui:
                registry_by_eui.setdefault(eui, meta)
                log.debug(
                    "Bridged Matter node_id=%s -> eui=%s name=%s",
                    node_id, eui, meta.get("name_by_user") or meta.get("name"),
                )
            else:
                log.debug(
                    "No EUI64 bridge for Matter node_id=%s (matter-server storage missing)",
                    node_id,
                )
    
    if registry_by_eui:
        log.debug("Loaded device registry with %d Thread devices", len(registry_by_eui))
    
    # Merge: OTBR nodes are the primary source, supplemented with registry data
    merged: dict[str, dict[str, Any]] = {}
    
    # Add OTBR nodes with any matching registry data
    for eui, otbr_data in otbr_nodes.items():
        merged[eui] = {**otbr_data}
        if eui in registry_by_eui:
            merged[eui].update(registry_by_eui[eui])
    
    # Add registry-only devices (not discovered from OTBR)
    for eui, reg_data in registry_by_eui.items():
        if eui not in merged:
            merged[eui] = reg_data
    
    # Convert to list format for downstream processing
    return list(merged.values())


def _fallback_device_registry() -> list[dict[str, Any]]:
    """Fallback: read device registry from .storage JSON file.
    
    If OTBR API is unavailable, read directly from HA's device registry file.
    """
    try:
        if not DEVICE_REGISTRY_PATH.exists():
            log.warning(
                "Device registry file not found at %s; ensure HA config dir is mounted",
                DEVICE_REGISTRY_PATH,
            )
            return []
        
        with open(DEVICE_REGISTRY_PATH, "r") as f:
            data = json.load(f)
        
        # The file structure is {"version": 1, "key": "...", "data": {"devices": [...]}}
        devices = data.get("data", {}).get("devices", [])
        log.debug(
            "Device registry loaded from %s: %d devices",
            DEVICE_REGISTRY_PATH,
            len(devices),
        )
        return devices
    except FileNotFoundError:
        log.warning("Device registry file not found at %s", DEVICE_REGISTRY_PATH)
        return []
    except json.JSONDecodeError as exc:
        log.warning("Failed to parse device registry JSON: %s", exc)
        return []
    except Exception as exc:
        log.warning("Failed to fetch device registry fallback: %s", exc)
        return []


def _extract_thread_devices(devices: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Extract Thread devices from OTBR topology or device registry.

    Returns a dict mapping EUI64 → {role, rloc, ...}
    
    Handles two formats:
    1. OTBR topology nodes: {"extendedAddress": "...", "rloc": ..., "role": ...}
    2. Device registry devices: {"connections": [["thread", "..."], ...], ...}
    """
    out: dict[str, dict[str, Any]] = {}
    
    for dev in devices:
        # Check if this is an OTBR topology node (has extendedAddress)
        if "extendedAddress" in dev:
            ext_addr = dev.get("extendedAddress")
            if ext_addr:
                try:
                    eui = _normalize_ieee(str(ext_addr))
                    out[eui] = {
                        "role": dev.get("role"),
                        "rloc": dev.get("rloc"),
                    }
                    log.debug(
                        "Found Thread node from OTBR: eui=%s role=%s",
                        eui,
                        dev.get("role"),
                    )
                except Exception as exc:
                    log.debug("Failed to parse OTBR node %s: %s", ext_addr, exc)
        
        # Otherwise, check if this is a device registry device (has connections)
        connections = dev.get("connections", [])
        for conn_type, conn_id in connections:
            # Thread-only: do not match zigbee.
            if conn_type in _THREAD_CONN_TYPES:
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
                        "Found Thread device from registry: eui=%s name=%s",
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
