"""Synthetic demo data seeder.

Builds a small but realistic-looking Thread network in SQLite so the UI
and reasoner have something to show before live ingestion (Phase 2.5)
lands. Idempotent: re-running upserts node metadata and appends events.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ..storage.sqlite_store import SQLiteStore, get_store


DEMO_NODES: list[dict[str, Any]] = [
    {"eui64": "1111111111111111", "friendly_name": "Border Router",   "role": "leader",        "area": "Office"},
    {"eui64": "2222222222222222", "friendly_name": "Kitchen Router",  "role": "router",        "area": "Kitchen"},
    {"eui64": "3333333333333333", "friendly_name": "Living Router",   "role": "router",        "area": "Living Room"},
    {"eui64": "4444444444444444", "friendly_name": "Bedroom Plug",    "role": "end_device",    "area": "Bedroom"},
    {"eui64": "5555555555555555", "friendly_name": "Garage Sensor",   "role": "sleepy",        "area": "Garage"},
]

# child -> parent topology
DEMO_LINKS: list[tuple[str, str]] = [
    ("2222222222222222", "1111111111111111"),
    ("3333333333333333", "1111111111111111"),
    ("4444444444444444", "3333333333333333"),
    ("5555555555555555", "2222222222222222"),
]


def seed_demo_topology(
    *,
    include_anomalies: bool = True,
    store: SQLiteStore | None = None,
) -> dict[str, Any]:
    """Populate SQLite with a deterministic demo topology + recent events.

    Returns a summary describing what was inserted.
    """
    s = store or get_store()
    now = datetime.now(tz=UTC)

    for n in DEMO_NODES:
        s.upsert_node_metadata(
            eui64=n["eui64"],
            friendly_name=n["friendly_name"],
            area=n["area"],
            role=n["role"],
        )

    event_ids: list[int] = []
    # baseline: one attach per child node, 10 min ago
    base_ts = now - timedelta(minutes=10)
    for child, parent in DEMO_LINKS:
        eid = s.insert_event(
            eui64=child,
            type="attach",
            ts=base_ts.isoformat(),
            parent_eui64=parent,
            rssi=-65,
            lqi=210,
            payload={"source": "seed_demo_topology"},
        )
        event_ids.append(eid)

    anomaly_summary: dict[str, Any] = {}
    if include_anomalies:
        # parent churn on bedroom plug
        churn_node = "4444444444444444"
        for i in range(4):
            t = (now - timedelta(minutes=20 - i * 4)).isoformat()
            eid = s.insert_event(
                eui64=churn_node,
                type="parent_change",
                ts=t,
                parent_eui64="2222222222222222" if i % 2 else "3333333333333333",
                payload={"source": "seed_demo_topology"},
            )
            event_ids.append(eid)
        # attach failures on garage sensor
        fail_node = "5555555555555555"
        for i in range(3):
            t = (now - timedelta(minutes=10 - i * 3)).isoformat()
            eid = s.insert_event(
                eui64=fail_node,
                type="attach_failed",
                ts=t,
                payload={"source": "seed_demo_topology", "reason": "timeout"},
            )
            event_ids.append(eid)
        anomaly_summary = {
            "parent_churn_node": churn_node,
            "attach_failures_node": fail_node,
        }

    return {
        "seeded_at": now.isoformat(),
        "node_count": len(DEMO_NODES),
        "link_count": len(DEMO_LINKS),
        "event_ids": event_ids,
        "anomalies": anomaly_summary,
    }
