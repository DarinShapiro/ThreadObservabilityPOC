"""Topology graph builder for Thread Observability.

Builds a deterministic network snapshot from the SQLite ``events`` and
``nodes`` tables. The snapshot is computed on demand (Phase 2); a future
phase may cache snapshots and recompute on a scheduler tick.

Snapshot shape::

    {
        "computed_at": ISO8601,
        "freshness_minutes": int,
        "node_count": int,
        "link_count": int,
        "nodes": [
            {
                "eui64": str,
                "friendly_name": str | None,
                "role": str | None,
                "last_seen": ISO8601 | None,
                "parent_eui64": str | None,
                "last_rssi": int | None,
                "last_lqi": int | None,
                "stale": bool,         # last_seen older than freshness window
            },
            ...
        ],
        "links": [{"child": eui64, "parent": eui64}, ...],
    }

Link inference rule (v1): for each node, take the most recent
``attach`` / ``parent_change`` event within the freshness window; if its
``parent_eui64`` is set, emit a directed child→parent edge.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ..storage.sqlite_store import SQLiteStore, get_store

FRESHNESS_DEFAULT_MINUTES = 60


def build_topology(
    *,
    freshness_minutes: int = FRESHNESS_DEFAULT_MINUTES,
    store: SQLiteStore | None = None,
) -> dict[str, Any]:
    """Return a topology snapshot computed from the events/nodes tables."""
    s = store or get_store()
    now = datetime.now(tz=UTC)
    cutoff = (now - timedelta(minutes=freshness_minutes)).isoformat()

    with s._lock:  # noqa: SLF001 - intentional: same package
        rows = s._conn.execute(  # noqa: SLF001
            """
            SELECT
              n.eui64,
              n.friendly_name,
              n.role,
              n.last_seen,
              (SELECT e.parent_eui64
                 FROM events e
                WHERE e.eui64 = n.eui64
                  AND e.type IN ('attach', 'parent_change')
                  AND e.ts >= ?
                ORDER BY e.ts DESC, e.id DESC
                LIMIT 1) AS parent_eui64,
              (SELECT e.rssi
                 FROM events e
                WHERE e.eui64 = n.eui64
                  AND e.rssi IS NOT NULL
                ORDER BY e.ts DESC, e.id DESC
                LIMIT 1) AS last_rssi,
              (SELECT e.lqi
                 FROM events e
                WHERE e.eui64 = n.eui64
                  AND e.lqi IS NOT NULL
                ORDER BY e.ts DESC, e.id DESC
                LIMIT 1) AS last_lqi
            FROM nodes n
            ORDER BY n.eui64
            """,
            (cutoff,),
        ).fetchall()

    nodes: list[dict[str, Any]] = []
    links: list[dict[str, str]] = []
    for row in rows:
        d = dict(row)
        last_seen = d.get("last_seen")
        stale = bool(last_seen and last_seen < cutoff)
        nodes.append(
            {
                "eui64": d["eui64"],
                "friendly_name": d.get("friendly_name"),
                "role": d.get("role"),
                "last_seen": last_seen,
                "parent_eui64": d.get("parent_eui64"),
                "last_rssi": d.get("last_rssi"),
                "last_lqi": d.get("last_lqi"),
                "stale": stale,
            }
        )
        parent = d.get("parent_eui64")
        if parent:
            links.append({"child": d["eui64"], "parent": parent})

    return {
        "computed_at": now.isoformat(),
        "freshness_minutes": freshness_minutes,
        "node_count": len(nodes),
        "link_count": len(links),
        "nodes": nodes,
        "links": links,
    }
