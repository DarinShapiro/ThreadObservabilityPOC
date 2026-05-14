"""Topology graph builder for Thread Observability.

Builds a deterministic network snapshot from the SQLite ``nodes`` and
``links`` tables. Links are populated by ``discover_and_sync`` from the
Matter Thread Network Diagnostics cluster (NeighborTable + RouteTable),
which gives us true mesh adjacencies — not just IPv6 endpoints seen on
the wire.

Snapshot shape::

    {
        "computed_at": ISO8601,
        "freshness_minutes": int,
        "node_count": int,
        "link_count": int,
        "split": bool,                 # multiple distinct partition_ids
        "partitions": [
            {"partition_id": int, "leader_eui64": str|None,
             "member_count": int, "members": [eui64, ...]},
            ...
        ],
        "nodes": [
            {
                "eui64": str,
                "friendly_name": str | None,
                "role": str | None,
                "routing_role": str | None,
                "partition_id": int | None,
                "last_seen": ISO8601 | None,
                "parent_eui64": str | None,   # if known via is_child/event
                "last_rssi": int | None,
                "last_lqi": int | None,
                "stale": bool,
            },
            ...
        ],
        "links": [
            {
                "from": eui64,             # reporter
                "to":   eui64,             # neighbor
                "source": "neighbor_table"|"route_table",
                "rssi_avg": int | None,
                "lqi_in":   int | None,
                "lqi_out":  int | None,
                "is_child": int | None,
                "path_cost": int | None,
                "tags": [str, ...],        # "weak_link","high_error","asymmetric"
            },
            ...
        ],
    }
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ..storage.sqlite_store import SQLiteStore, get_store

FRESHNESS_DEFAULT_MINUTES = 60
WEAK_LINK_RSSI_DBM = -85
HIGH_ERROR_PERCENT = 10  # frame_error_rate / message_error_rate threshold
ASYMMETRY_DB = 10        # |A->B rssi - B->A rssi| > this is asymmetric


def _find_articulation_points(adjacency: dict[str, set[str]]) -> set[str]:
    discovery: dict[str, int] = {}
    low: dict[str, int] = {}
    parent: dict[str, str | None] = {}
    articulation: set[str] = set()
    time = 0

    def dfs(node: str) -> None:
        nonlocal time
        time += 1
        discovery[node] = time
        low[node] = time
        child_count = 0
        for neighbor in sorted(adjacency.get(node, set())):
            if neighbor not in discovery:
                parent[neighbor] = node
                child_count += 1
                dfs(neighbor)
                low[node] = min(low[node], low[neighbor])
                if parent.get(node) is None and child_count > 1:
                    articulation.add(node)
                if parent.get(node) is not None and low[neighbor] >= discovery[node]:
                    articulation.add(node)
            elif neighbor != parent.get(node):
                low[node] = min(low[node], discovery[neighbor])

    for node in sorted(adjacency):
        if node not in discovery:
            parent[node] = None
            dfs(node)
    return articulation


def build_topology(
    *,
    freshness_minutes: int = FRESHNESS_DEFAULT_MINUTES,
    include_phantoms: bool = False,
    store: SQLiteStore | None = None,
) -> dict[str, Any]:
    """Return a topology snapshot computed from nodes + links tables.

    By default, phantom nodes (no recent reference in any router's neighbor
    or route table) are excluded along with any links touching them. Set
    ``include_phantoms=True`` to see them, e.g. for manual cleanup workflows.
    """
    s = store or get_store()
    now = datetime.now(tz=UTC)
    cutoff = (now - timedelta(minutes=freshness_minutes)).isoformat()

    phantom_filter = "" if include_phantoms else " WHERE n.status <> 'phantom'"

    with s._lock:  # noqa: SLF001 - intentional: same package
        rows = s._conn.execute(  # noqa: SLF001
            f"""
            SELECT
              n.eui64,
              n.friendly_name,
              n.area_name,
              n.role,
              n.routing_role,
              n.partition_id,
              n.leader_router_id,
              n.vendor_id,
              n.product_id,
              n.serial_number,
              n.last_seen,
              n.status,
              n.last_referenced_at,
              (SELECT e.parent_eui64
                 FROM events e
                WHERE e.eui64 = n.eui64
                  AND e.type IN ('attach', 'parent_change')
                  AND e.ts >= ?
                ORDER BY e.ts DESC, e.id DESC
                LIMIT 1) AS parent_event_eui64,
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
            {phantom_filter}
            ORDER BY n.eui64
            """,
            (cutoff,),
        ).fetchall()

    all_links_raw = s.list_links()
    # When filtering phantoms, drop links touching them so the graph stays
    # coherent (no edges into nonexistent nodes).
    if not include_phantoms:
        visible_euis = {dict(r)["eui64"] for r in rows}
        all_links_raw = [
            ln for ln in all_links_raw
            if ln.get("reporter_eui64") in visible_euis
            and ln.get("neighbor_eui64") in visible_euis
        ]

    # Build asymmetry lookup: (reporter, neighbor, source) -> rssi_avg
    rssi_by_edge: dict[tuple[str, str, str], int] = {}
    for ln in all_links_raw:
        r = ln.get("rssi_avg")
        if isinstance(r, int):
            rssi_by_edge[(ln["reporter_eui64"], ln["neighbor_eui64"], ln["source"])] = r

    # Derive parent_eui64 from neighbor_table is_child=1 entries: if X reports Y
    # as a child, then Y's parent is X. (Mesh routers only see direct children.)
    parent_of: dict[str, str] = {}
    for ln in all_links_raw:
        if ln.get("source") == "neighbor_table" and ln.get("is_child"):
            parent_of[ln["neighbor_eui64"]] = ln["reporter_eui64"]

    nodes: list[dict[str, Any]] = []
    partitions: dict[int, list[str]] = {}
    leaders_by_partition: dict[int, str] = {}
    for row in rows:
        d = dict(row)
        last_seen = d.get("last_seen")
        stale = bool(last_seen and last_seen < cutoff)
        eui = d["eui64"]
        parent = parent_of.get(eui) or d.get("parent_event_eui64")
        pid = d.get("partition_id")
        if isinstance(pid, int):
            partitions.setdefault(pid, []).append(eui)
            if d.get("routing_role") == "leader":
                leaders_by_partition.setdefault(pid, eui)
        nodes.append(
            {
                "eui64": eui,
                "friendly_name": d.get("friendly_name"),
                "area_name": d.get("area_name"),
                "role": d.get("role"),
                "routing_role": d.get("routing_role"),
                "partition_id": pid,
                "leader_router_id": d.get("leader_router_id"),
                "vendor_id": d.get("vendor_id"),
                "product_id": d.get("product_id"),
                "serial_number": d.get("serial_number"),
                "last_seen": last_seen,
                "parent_eui64": parent,
                "last_rssi": d.get("last_rssi"),
                "last_lqi": d.get("last_lqi"),
                "stale": stale,
                "is_phantom": d.get("status") == "phantom",
                "status": d.get("status"),
                "last_referenced_at": d.get("last_referenced_at"),
            }
        )

    # Project links with tags.
    # Edge classification + dedup: router-router neighbor_table pairs reported
    # by both ends collapse to a single 'peer' edge so renderers don't have
    # to do it. Other edges keep their direction.
    router_roles = {"leader", "router", "reed"}
    role_by_eui = {n["eui64"]: n.get("routing_role") for n in nodes}
    seen_peer_keys: set[str] = set()
    links: list[dict[str, Any]] = []
    for ln in all_links_raw:
        rep = ln["reporter_eui64"]
        nei = ln["neighbor_eui64"]
        src = ln["source"]
        tags: list[str] = []
        rssi_avg = ln.get("rssi_avg")
        if isinstance(rssi_avg, int) and rssi_avg < WEAK_LINK_RSSI_DBM:
            tags.append("weak_link")
        fer = ln.get("frame_error_rate")
        mer = ln.get("message_error_rate")
        if (isinstance(fer, int) and fer > HIGH_ERROR_PERCENT) or (
            isinstance(mer, int) and mer > HIGH_ERROR_PERCENT
        ):
            tags.append("high_error")
        # Asymmetry: compare with the reverse-direction rssi if present.
        reverse = rssi_by_edge.get((nei, rep, src))
        if isinstance(rssi_avg, int) and isinstance(reverse, int):
            if abs(rssi_avg - reverse) > ASYMMETRY_DB:
                tags.append("asymmetric")

        is_child = bool(ln.get("is_child"))
        a_router = role_by_eui.get(rep) in router_roles
        b_router = role_by_eui.get(nei) in router_roles
        is_peer = (
            src == "neighbor_table"
            and a_router and b_router
            and not is_child
        )
        if is_peer:
            peer_key = "peer:" + "|".join(sorted([rep, nei]))
            if peer_key in seen_peer_keys:
                continue
            seen_peer_keys.add(peer_key)
            edge_class = "peer"
        elif is_child:
            edge_class = "child"
        elif src == "route_table":
            edge_class = "route"
        else:
            edge_class = "other"

        links.append(
            {
                "from": rep,
                "to": nei,
                "source": src,
                "edge_class": edge_class,
                "rssi_avg": rssi_avg,
                "rssi_last": ln.get("rssi_last"),
                "lqi_in": ln.get("lqi_in"),
                "lqi_out": ln.get("lqi_out"),
                "is_child": ln.get("is_child"),
                "age_seconds": ln.get("age_seconds"),
                "frame_error_rate": fer,
                "message_error_rate": mer,
                "path_cost": ln.get("path_cost"),
                "link_established": ln.get("link_established"),
                "rx_on_when_idle": ln.get("rx_on_when_idle"),
                "full_thread_device": ln.get("full_thread_device"),
                "tags": tags,
            }
        )

    partition_summary = [
        {
            "partition_id": pid,
            "leader_eui64": leaders_by_partition.get(pid),
            "member_count": len(members),
            "members": members,
        }
        for pid, members in sorted(partitions.items())
    ]

    return {
        "computed_at": now.isoformat(),
        "freshness_minutes": freshness_minutes,
        "node_count": len(nodes),
        "link_count": len(links),
        "split": len(partitions) > 1,
        "partitions": partition_summary,
        "nodes": nodes,
        "links": links,
    }


def derive_graph_diagnostics(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Derive simple graph-backed diagnostic facts from a topology snapshot.

    These are intentionally conservative, evidence-bound summaries that UI and
    AI can both consume without re-implementing topology heuristics client-side.
    """
    nodes = list(snapshot.get("nodes") or [])
    links = list(snapshot.get("links") or [])
    facts: list[dict[str, Any]] = []
    router_roles = {"leader", "router", "reed"}

    if snapshot.get("split") and int(snapshot.get("node_count") or 0) > 0:
        facts.append({
            "kind": "split_mesh",
            "severity": "warn",
            "title": "The mesh is split across multiple partitions",
            "detail": f"{len(snapshot.get('partitions') or [])} partitions are currently visible in the topology graph.",
        })

    weak_links = [
        link for link in links
        if "weak_link" in (link.get("tags") or []) or "high_error" in (link.get("tags") or [])
    ]
    if weak_links:
        facts.append({
            "kind": "weak_links",
            "severity": "warn",
            "title": "Weak or error-prone links are present",
            "detail": f"{len(weak_links)} graph edges are tagged weak_link or high_error in the retained topology.",
        })

    children_by_parent: dict[str, list[str]] = {}
    for node in nodes:
        parent = node.get("parent_eui64")
        eui = node.get("eui64")
        if parent and eui:
            children_by_parent.setdefault(str(parent), []).append(str(eui))
    dependent_parents = [
        {"parent_eui64": parent, "child_count": len(children)}
        for parent, children in children_by_parent.items()
        if len(children) >= 2
    ]
    dependent_parents.sort(key=lambda row: row["child_count"], reverse=True)
    if dependent_parents:
        top = dependent_parents[0]
        facts.append({
            "kind": "subtree_dependency",
            "severity": "warn",
            "title": "A single parent is carrying multiple child devices",
            "detail": f"Parent {top['parent_eui64']} currently has {top['child_count']} child devices attached, which reduces path diversity for that subtree.",
            "parent_eui64": top["parent_eui64"],
            "child_count": top["child_count"],
        })

    router_partition_counts: dict[int, int] = {}
    for node in nodes:
        pid = node.get("partition_id")
        if isinstance(pid, int) and node.get("routing_role") in router_roles:
            router_partition_counts[pid] = router_partition_counts.get(pid, 0) + 1

    healthy_peer_neighbors: dict[str, set[str]] = {}
    total_peer_neighbors: dict[str, set[str]] = {}
    peer_neighbors_by_partition: dict[int, dict[str, set[str]]] = {}
    for link in links:
        if link.get("edge_class") != "peer":
            continue
        left = str(link.get("from") or "").strip()
        right = str(link.get("to") or "").strip()
        if not left or not right:
            continue
        total_peer_neighbors.setdefault(left, set()).add(right)
        total_peer_neighbors.setdefault(right, set()).add(left)
        left_partition = next((n.get("partition_id") for n in nodes if n.get("eui64") == left), None)
        right_partition = next((n.get("partition_id") for n in nodes if n.get("eui64") == right), None)
        if isinstance(left_partition, int) and left_partition == right_partition:
            part_adj = peer_neighbors_by_partition.setdefault(left_partition, {})
            part_adj.setdefault(left, set()).add(right)
            part_adj.setdefault(right, set()).add(left)
        tags = set(link.get("tags") or [])
        if not ({"weak_link", "high_error"} & tags):
            healthy_peer_neighbors.setdefault(left, set()).add(right)
            healthy_peer_neighbors.setdefault(right, set()).add(left)

    choke_points: list[dict[str, Any]] = []
    for partition_id, adjacency in peer_neighbors_by_partition.items():
        if len(adjacency) < 3:
            continue
        for eui64 in sorted(_find_articulation_points(adjacency)):
            choke_points.append({
                "partition_id": partition_id,
                "eui64": eui64,
                "peer_degree": len(adjacency.get(eui64, set())),
            })
    choke_points.sort(key=lambda row: (-row["peer_degree"], row["eui64"]))
    if choke_points:
        top = choke_points[0]
        facts.append({
            "kind": "choke_point",
            "severity": "warn",
            "title": "A router is acting as a choke point in the mesh",
            "detail": (
                f"Router {top['eui64']} is a peer-graph articulation point in partition {top['partition_id']}, "
                "so losing it would split otherwise-connected router paths."
            ),
            "eui64": top["eui64"],
            "partition_id": top["partition_id"],
            "peer_degree": top["peer_degree"],
        })

    low_diversity_candidates: list[dict[str, Any]] = []
    for node in nodes:
        eui = str(node.get("eui64") or "").strip()
        pid = node.get("partition_id")
        routing_role = node.get("routing_role")
        if not eui or not isinstance(pid, int) or routing_role not in router_roles:
            continue
        if router_partition_counts.get(pid, 0) < 3:
            continue
        healthy_count = len(healthy_peer_neighbors.get(eui, set()))
        if healthy_count > 1:
            continue
        low_diversity_candidates.append(
            {
                "eui64": eui,
                "partition_id": pid,
                "routing_role": routing_role,
                "healthy_peer_count": healthy_count,
                "total_peer_count": len(total_peer_neighbors.get(eui, set())),
            }
        )
    low_diversity_candidates.sort(key=lambda row: (row["healthy_peer_count"], row["total_peer_count"], row["eui64"]))
    if low_diversity_candidates:
        top = low_diversity_candidates[0]
        facts.append({
            "kind": "low_path_diversity",
            "severity": "warn",
            "title": "A router has limited healthy peer-path diversity",
            "detail": (
                f"Router {top['eui64']} has {top['healthy_peer_count']} healthy peer links in partition "
                f"{top['partition_id']}, which leaves little routing redundancy if that peer degrades."
            ),
            "eui64": top["eui64"],
            "partition_id": top["partition_id"],
            "healthy_peer_count": top["healthy_peer_count"],
            "total_peer_count": top["total_peer_count"],
        })
        weak_low_diversity_candidates = []
        for candidate in low_diversity_candidates:
            weak_links_for_candidate = [
                link for link in links
                if link.get("edge_class") == "peer"
                and candidate["eui64"] in {link.get("from"), link.get("to")}
                and ({"weak_link", "high_error"} & set(link.get("tags") or []))
            ]
            if weak_links_for_candidate:
                weak_low_diversity_candidates.append((candidate, weak_links_for_candidate))
        if weak_low_diversity_candidates:
            candidate, _weak_links_for_candidate = weak_low_diversity_candidates[0]
            facts.append({
                "kind": "intermediary_router_opportunity",
                "severity": "warn",
                "title": "An intermediary router would likely improve this path",
                "detail": (
                    f"Router {candidate['eui64']} has only {candidate['healthy_peer_count']} healthy peer path and its remaining "
                    "peer connectivity is weak or error-prone, so adding a nearby intermediary router is more likely "
                    "to help than treating this as a mesh-wide instability first."
                ),
                "eui64": candidate["eui64"],
                "partition_id": candidate["partition_id"],
            })

    return facts
