"""Tier 4: persisted topology snapshots + diff.

Adds a pipeline stage that periodically captures the live topology
(produced by :mod:`thread_observability.pipeline.topology`) into the
``topology_snapshots`` table, and a deterministic diff function that
expresses "what changed between two snapshots" in a structured form an
AI consultant can reason over.

The hash skip-write rule: if the canonical fingerprint of the current
topology matches the most-recent snapshot's hash AND it was captured
within the heartbeat window, we don't write a duplicate row. Otherwise
we write. This keeps the table sparse (one row per real change) but
guarantees we still produce at least one row per heartbeat so callers
can prove "we were observing at time T."
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from ..storage.sqlite_store import SQLiteStore, get_store
from . import topology as topology_mod


# How long between forced heartbeat snapshots even when nothing changed.
SNAPSHOT_HEARTBEAT_MINUTES = 60


def _canonicalize_snapshot_for_hash(snapshot: dict[str, Any]) -> str:
    """Build a stable fingerprint of the snapshot content.

    We deliberately exclude ``computed_at`` (always changes) and any
    other volatile per-call fields. Nodes and links are sorted by their
    natural keys and reduced to the fields that actually express
    topology — friendly_name churn or last_seen drift shouldn't trigger
    a "new" snapshot.
    """
    nodes = sorted(
        (
            {
                "eui64": n.get("eui64"),
                "role": n.get("role"),
                "routing_role": n.get("routing_role"),
                "partition_id": n.get("partition_id"),
                "parent_eui64": n.get("parent_eui64"),
            }
            for n in (snapshot.get("nodes") or [])
        ),
        key=lambda n: n.get("eui64") or "",
    )
    links = sorted(
        (
            {
                "from": ln.get("from"),
                "to": ln.get("to"),
                "source": ln.get("source"),
                "is_child": ln.get("is_child"),
            }
            for ln in (snapshot.get("links") or [])
        ),
        key=lambda ln: (
            ln.get("from") or "",
            ln.get("to") or "",
            ln.get("source") or "",
        ),
    )
    partitions = sorted(
        (
            {
                "partition_id": p.get("partition_id"),
                "leader_eui64": p.get("leader_eui64"),
                "member_count": p.get("member_count"),
            }
            for p in (snapshot.get("partitions") or [])
        ),
        key=lambda p: p.get("partition_id") or -1,
    )
    payload = json.dumps(
        {"nodes": nodes, "links": links, "partitions": partitions},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def capture_snapshot(
    store: SQLiteStore | None = None,
    *,
    heartbeat_minutes: int = SNAPSHOT_HEARTBEAT_MINUTES,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compute, hash, and (conditionally) persist a topology snapshot.

    Returns ``{"snapshot_id": int|None, "snapshot_hash": str,
    "written": bool, "reason": str}``. ``snapshot_id`` is None and
    ``written`` is False when we skipped the write because the previous
    snapshot was identical and recent.
    """
    s = store or get_store()
    snap = topology_mod.build_topology(store=s)
    fingerprint = _canonicalize_snapshot_for_hash(snap)

    now_dt = now or datetime.now(tz=UTC)
    cutoff = (now_dt - timedelta(minutes=heartbeat_minutes)).isoformat()

    latest = s.get_latest_topology_snapshot()
    if (
        latest
        and latest.get("snapshot_hash") == fingerprint
        and (latest.get("captured_at") or "") >= cutoff
    ):
        return {
            "snapshot_id": None,
            "snapshot_hash": fingerprint,
            "written": False,
            "reason": "unchanged_within_heartbeat",
        }

    sid = s.insert_topology_snapshot(
        snapshot=snap,
        snapshot_hash=fingerprint,
        captured_at=now_dt.isoformat(),
    )
    return {
        "snapshot_id": sid,
        "snapshot_hash": fingerprint,
        "written": True,
        "reason": "heartbeat" if latest and latest.get("snapshot_hash") == fingerprint else "changed",
    }


def diff_topology(
    store: SQLiteStore,
    *,
    snapshot_id_a: int,
    snapshot_id_b: int,
) -> dict[str, Any]:
    """Compute a structured diff between two snapshots by id.

    ``a`` is the older / baseline snapshot, ``b`` the newer / candidate
    one. The result lists added/removed nodes, added/removed/changed
    links, and per-node role/partition transitions, plus the two
    snapshots' identifying metadata so a caller can prove what was
    compared.
    """
    a = store.get_topology_snapshot(snapshot_id_a)
    b = store.get_topology_snapshot(snapshot_id_b)
    if not a or not b:
        return {
            "error": "snapshot_not_found",
            "snapshot_id_a": snapshot_id_a,
            "snapshot_id_b": snapshot_id_b,
            "a_found": bool(a),
            "b_found": bool(b),
        }

    a_snap = a.get("snapshot") or {}
    b_snap = b.get("snapshot") or {}

    a_nodes = {n.get("eui64"): n for n in (a_snap.get("nodes") or []) if n.get("eui64")}
    b_nodes = {n.get("eui64"): n for n in (b_snap.get("nodes") or []) if n.get("eui64")}

    added_nodes = [b_nodes[k] for k in b_nodes.keys() - a_nodes.keys()]
    removed_nodes = [a_nodes[k] for k in a_nodes.keys() - b_nodes.keys()]

    changed_nodes: list[dict[str, Any]] = []
    for eui, bn in b_nodes.items():
        an = a_nodes.get(eui)
        if not an:
            continue
        diffs: dict[str, Any] = {}
        for field in ("role", "routing_role", "partition_id", "parent_eui64"):
            if an.get(field) != bn.get(field):
                diffs[field] = {"from": an.get(field), "to": bn.get(field)}
        if diffs:
            changed_nodes.append({"eui64": eui, "changes": diffs})

    def _link_key(ln: dict[str, Any]) -> tuple[str, str, str]:
        return (
            str(ln.get("from") or ""),
            str(ln.get("to") or ""),
            str(ln.get("source") or ""),
        )

    a_links = {_link_key(ln): ln for ln in (a_snap.get("links") or [])}
    b_links = {_link_key(ln): ln for ln in (b_snap.get("links") or [])}

    added_links = [b_links[k] for k in b_links.keys() - a_links.keys()]
    removed_links = [a_links[k] for k in a_links.keys() - b_links.keys()]

    return {
        "snapshot_a": {
            "id": a.get("id"),
            "captured_at": a.get("captured_at"),
            "snapshot_hash": a.get("snapshot_hash"),
        },
        "snapshot_b": {
            "id": b.get("id"),
            "captured_at": b.get("captured_at"),
            "snapshot_hash": b.get("snapshot_hash"),
        },
        "added_nodes": added_nodes,
        "removed_nodes": removed_nodes,
        "changed_nodes": changed_nodes,
        "added_links": added_links,
        "removed_links": removed_links,
        "summary": {
            "added_node_count": len(added_nodes),
            "removed_node_count": len(removed_nodes),
            "changed_node_count": len(changed_nodes),
            "added_link_count": len(added_links),
            "removed_link_count": len(removed_links),
        },
    }
