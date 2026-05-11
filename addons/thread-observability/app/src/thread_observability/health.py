"""Shared health-snapshot computation.

Produces a consolidated view of network state used by both the HTTP API
(`/v1/health/snapshot`) and the MCP `get_health_snapshot` tool. All
inputs come from SQLite so the result is deterministic and cheap.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from .storage.sqlite_store import SQLiteStore, get_store

STALE_THRESHOLD_MIN = 5
OFFLINE_THRESHOLD_MIN = 30


def build_health_snapshot(*, store: SQLiteStore | None = None) -> dict[str, Any]:
    s = store or get_store()
    now = datetime.now(tz=UTC)
    stale_cutoff = (now - timedelta(minutes=STALE_THRESHOLD_MIN)).isoformat()
    offline_cutoff = (now - timedelta(minutes=OFFLINE_THRESHOLD_MIN)).isoformat()

    with s._lock:  # noqa: SLF001
        node_rows = s._conn.execute(  # noqa: SLF001
            "SELECT eui64, last_seen FROM nodes"
        ).fetchall()
        newest = s._conn.execute(  # noqa: SLF001
            "SELECT MAX(ts) FROM events"
        ).fetchone()[0]

    healthy = stale = offline = 0
    for r in node_rows:
        ls = r["last_seen"]
        if not ls:
            offline += 1
        elif ls < offline_cutoff:
            offline += 1
        elif ls < stale_cutoff:
            stale += 1
        else:
            healthy += 1

    active = s.list_active_issues()
    by_sev: dict[str, int] = {}
    for i in active:
        by_sev[i["severity"]] = by_sev.get(i["severity"], 0) + 1

    data_age: float | None = None
    if newest:
        try:
            data_age = (now - datetime.fromisoformat(newest)).total_seconds()
        except ValueError:
            data_age = None

    overall = "ok"
    if by_sev.get("crit"):
        overall = "critical"
    elif by_sev.get("warn") or offline:
        overall = "degraded"

    return {
        "computed_at": now.isoformat(),
        "status": overall,
        "data_age_seconds": data_age,
        "summary": {
            "healthy_nodes": healthy,
            "stale_nodes": stale,
            "offline_nodes": offline,
            "total_nodes": len(node_rows),
        },
        "active_issues": {
            "count": len(active),
            "by_severity": by_sev,
        },
    }
