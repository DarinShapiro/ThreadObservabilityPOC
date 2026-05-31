"""Read-only network health builder.

This module composes existing deterministic surfaces into a single
network-health view without introducing any new HTTP or MCP endpoint.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from . import network_health_scoring as scoring
from .pipeline.nodes import list_nodes_enriched
from .pipeline.topology import _find_articulation_points, build_topology, derive_graph_diagnostics
from .storage.sqlite_store import SQLiteStore, get_store
from .utils.datetime import parse_iso_datetime

_ROUTER_ROLES = {"leader", "router", "reed"}


def _finding_affected_nodes(fact: dict[str, Any]) -> list[str]:
    keys = ("eui64", "parent_eui64", "reporter_eui64", "neighbor_eui64", "from", "to")
    affected_nodes: list[str] = []
    for key in keys:
        value = str(fact.get(key) or "").strip()
        if value and value not in affected_nodes:
            affected_nodes.append(value)
    return affected_nodes


def _node_age_seconds(last_seen: str | None, *, now: datetime) -> float | None:
    parsed = parse_iso_datetime(last_seen)
    if parsed is None:
        return None
    return max(0.0, (now - parsed).total_seconds())


def _counter_deltas(
    store: SQLiteStore,
    *,
    eui64: str,
    window: timedelta,
    now: datetime,
) -> dict[str, Any]:
    samples = store.get_counter_samples(
        eui64=eui64,
        since=(now - window).isoformat(),
        until=now.isoformat(),
        limit=2000,
    )
    if len(samples) < 2:
        return {"sample_count": len(samples), "deltas": {}}
    first = samples[0].get("counters") or {}
    last = samples[-1].get("counters") or {}
    deltas: dict[str, Any] = {}
    for key in set(first) | set(last):
        a = first.get(key)
        b = last.get(key)
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
            continue
        diff = b - a
        deltas[key] = None if diff < 0 else diff
    return {"sample_count": len(samples), "deltas": deltas}


def _raw_reverse_rssi_lookup(store: SQLiteStore) -> dict[tuple[str, str, str], float]:
    lookup: dict[tuple[str, str, str], float] = {}
    for row in store.list_links():
        reporter = str(row.get("reporter_eui64") or "").strip()
        neighbor = str(row.get("neighbor_eui64") or "").strip()
        source = str(row.get("source") or "").strip()
        rssi = row.get("rssi_avg")
        if reporter and neighbor and source and isinstance(rssi, (int, float)):
            lookup[(reporter, neighbor, source)] = float(rssi)
    return lookup


def _topology_findings(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for fact in derive_graph_diagnostics(snapshot):
        finding = {
            "finding_id": fact.get("kind"),
            "severity": fact.get("severity"),
            "reason_code": str(fact.get("kind") or "").upper(),
            "title": fact.get("title"),
            "summary": fact.get("detail"),
            "affected_nodes": _finding_affected_nodes(fact),
            "evidence": [fact],
        }
        findings.append(finding)
    return findings


def _placement_candidates(
    *,
    snapshot: dict[str, Any],
    node_rows: dict[str, dict[str, Any]],
    node_records: list[dict[str, Any]],
    router_rows: dict[str, dict[str, Any]],
    edge_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    node_record_by_eui = {
        str(row.get("eui64") or ""): row
        for row in node_records
        if isinstance(row, dict) and row.get("eui64")
    }
    peer_neighbors: dict[str, set[str]] = {}
    child_neighbors: dict[str, set[str]] = {}
    for link in snapshot.get("links") or []:
        source_eui = str(link.get("from") or "").strip()
        target_eui = str(link.get("to") or "").strip()
        edge_class = str(link.get("edge_class") or "").strip()
        if not source_eui or not target_eui:
            continue
        if edge_class == "peer":
            peer_neighbors.setdefault(source_eui, set()).add(target_eui)
            peer_neighbors.setdefault(target_eui, set()).add(source_eui)
        elif edge_class == "child":
            child_neighbors.setdefault(source_eui, set()).add(target_eui)

    def _candidate_location_label(focal_eui64: str, impacted_peers: list[str], *, kind: str) -> str:
        node = node_rows.get(focal_eui64) or {}
        focal_name = str(node.get("friendly_name") or focal_eui64).strip()
        area_name = str(node.get("area_name") or "").strip()
        peer_names = [
            str((node_rows.get(peer) or {}).get("friendly_name") or peer).strip()
            for peer in impacted_peers[:2]
        ]
        if kind == "choke_point" and peer_names:
            return f"between {focal_name} and {peer_names[0]}"
        if area_name and focal_name:
            return f"{area_name} near {focal_name}"
        if area_name:
            return area_name
        return focal_name

    candidates: list[dict[str, Any]] = []
    for finding in _topology_findings(snapshot):
        fact = (finding.get("evidence") or [{}])[0]
        kind = fact.get("kind")
        if kind not in {"intermediary_router_opportunity", "choke_point", "low_path_diversity"}:
            continue
        eui64 = str(fact.get("eui64") or fact.get("parent_eui64") or "").strip()
        if not eui64:
            continue
        node = node_rows.get(eui64) or {}
        router = router_rows.get(eui64) or {}
        node_record = node_record_by_eui.get(eui64) or {}
        router_metrics = node_record.get("metrics") if isinstance(node_record.get("metrics"), dict) else {}
        target_neighbors = int(router.get("target_strong_neighbors") or 1)
        strong_neighbor_count = int(router_metrics.get("strong_neighbor_count") or 0)
        alternate_path_count = int(router_metrics.get("alternate_path_count") or 0)
        bridge_dependency = float(router_metrics.get("bridge_dependency") or 0.0)
        weak_backbone = "WEAK_BACKBONE_PATH" in (router.get("reason_codes") or [])
        peer_euis = sorted(peer_neighbors.get(eui64, set()))
        child_euis = sorted(child_neighbors.get(eui64, set()))
        weak_peer_edges = [
            row for row in edge_rows
            if row.get("edge_class") == "peer"
            and eui64 in {row.get("source_eui64"), row.get("target_eui64")}
            and float(row.get("score") or 0.0) < 0.5
        ]
        affected_nodes: list[str] = []
        for impacted in [eui64, *peer_euis, *child_euis]:
            if impacted and impacted not in affected_nodes:
                affected_nodes.append(impacted)
        redundancy_delta = min(1.0, max(0.0, float(target_neighbors - strong_neighbor_count) / float(max(1, target_neighbors))))
        path_diversity_delta = min(
            1.0,
            max(
                0.3 if alternate_path_count >= 1 else 0.7,
                0.35 + (0.15 * min(2, len(weak_peer_edges))) if kind == "intermediary_router_opportunity" else 0.0,
                0.6 if kind == "low_path_diversity" else 0.0,
            ),
        )
        bottleneck_reduction = min(
            1.0,
            max(
                0.75 if kind == "choke_point" else 0.0,
                bridge_dependency,
                0.45 if weak_backbone else 0.0,
            ),
        )
        affected_nodes_norm = min(1.0, float(len(affected_nodes)) / 6.0)
        score_delta = scoring.score_placement_opportunity(
            redundancy_delta=redundancy_delta,
            path_diversity_delta=path_diversity_delta,
            bottleneck_reduction=bottleneck_reduction,
            affected_nodes_norm=affected_nodes_norm,
        )
        reason_codes: list[str] = []
        if redundancy_delta >= 0.25 or kind != "choke_point":
            reason_codes.append("ADD_ROUTER_FOR_ALTERNATE_PATH")
        if bottleneck_reduction >= 0.5:
            reason_codes.append("RELIEVE_BOTTLENECK_ROUTER")
        if weak_backbone or weak_peer_edges:
            reason_codes.append("REINFORCE_WEAK_BACKBONE_PATH")
        confidence = min(
            0.92,
            0.55
            + (0.10 if node.get("area_name") else 0.0)
            + (0.10 if len(affected_nodes) >= 2 else 0.0)
            + (0.07 if kind in {"choke_point", "intermediary_router_opportunity"} else 0.0)
            + (0.05 if weak_peer_edges else 0.0),
        )
        assumptions = [
            f"Assumes a mains-powered Thread router can be installed near {_candidate_location_label(eui64, peer_euis, kind=kind)}.",
        ]
        if weak_peer_edges:
            assumptions.append("Assumes the new router can form a cleaner peer link than the currently weak backbone path.")
        if len(affected_nodes) > 1:
            assumptions.append(
                "Expected to reduce single-path dependence for "
                + ", ".join(
                    str((node_rows.get(impacted) or {}).get("friendly_name") or impacted)
                    for impacted in affected_nodes[:3]
                )
                + (" and nearby nodes." if len(affected_nodes) > 3 else ".")
            )
        candidates.append(
            {
                "candidate_id": f"candidate-{kind}-{eui64[-6:]}",
                "location_label": _candidate_location_label(eui64, peer_euis, kind=kind),
                "recommendation_type": "mains_powered_thread_router",
                "device_examples": ["thread outlet", "thread plug"],
                "score_delta": score_delta,
                "redundancy_delta": round(redundancy_delta, 4),
                "path_diversity_delta": round(path_diversity_delta, 4),
                "bottleneck_reduction": round(bottleneck_reduction, 4),
                "affected_nodes": affected_nodes,
                "bottlenecks_reduced": [eui64] if kind == "choke_point" else [],
                "reason_codes": reason_codes,
                "assumptions": assumptions,
                "confidence": round(confidence, 4),
            }
        )
    candidates.sort(key=lambda row: row["score_delta"], reverse=True)
    return candidates


def build_network_health(*, store: SQLiteStore | None = None) -> dict[str, Any]:
    s = store or get_store()
    now = datetime.now(tz=UTC)
    snapshot = build_topology(store=s, include_phantoms=False)
    nodes = list_nodes_enriched(store=s, include_signal_strength=True, include_phantoms=False)
    node_by_eui = {str(node.get("eui64") or ""): node for node in nodes if node.get("eui64")}
    reverse_rssi_lookup = _raw_reverse_rssi_lookup(s)

    partition_adjacency: dict[int, dict[str, set[str]]] = {}
    peer_quality_by_node: dict[str, list[float]] = {}
    usable_peers_by_node: dict[str, set[str]] = {}
    strong_peers_by_node: dict[str, set[str]] = {}
    edge_rows: list[dict[str, Any]] = []

    for link in snapshot.get("links") or []:
        source_eui = str(link.get("from") or "").strip()
        target_eui = str(link.get("to") or "").strip()
        source_kind = str(link.get("source") or "").strip()
        if not source_eui or not target_eui:
            continue
        rssi = link.get("rssi_avg")
        if rssi is None:
            rssi = link.get("rssi_last")
        lqi = link.get("lqi_in") if link.get("lqi_in") is not None else link.get("lqi_out")
        reverse_rssi = reverse_rssi_lookup.get((target_eui, source_eui, source_kind))
        scored = scoring.score_edge_quality(
            rssi=rssi,
            lqi=lqi,
            age_seconds=link.get("age_seconds"),
            reverse_rssi=reverse_rssi,
            lqi_scale=255 if isinstance(lqi, (int, float)) and float(lqi) > 3 else 3,
        )
        edge_row = {
            "source_eui64": source_eui,
            "target_eui64": target_eui,
            "edge_class": link.get("edge_class"),
            "score": scored["score"],
            "band": scored["band"],
            "confidence": scored["confidence"],
            "reason_codes": scored["reason_codes"],
            "metrics": {
                "rssi": rssi,
                "lqi": lqi,
                "retry_delta_1h": None,
                "age_seconds": link.get("age_seconds"),
                "reverse_rssi": reverse_rssi,
                "symmetry": scored["components"]["symmetry"],
                "path_cost": link.get("path_cost"),
                "is_bridge": False,
            },
            "evidence": [],
        }
        edge_rows.append(edge_row)
        if link.get("edge_class") != "peer":
            continue
        source_partition = node_by_eui.get(source_eui, {}).get("partition_id")
        target_partition = node_by_eui.get(target_eui, {}).get("partition_id")
        if not isinstance(source_partition, int) or source_partition != target_partition:
            continue
        partition_adjacency.setdefault(source_partition, {}).setdefault(source_eui, set()).add(target_eui)
        partition_adjacency.setdefault(source_partition, {}).setdefault(target_eui, set()).add(source_eui)
        peer_quality_by_node.setdefault(source_eui, []).append(float(scored["score"]))
        peer_quality_by_node.setdefault(target_eui, []).append(float(scored["score"]))
        if scored["score"] >= 0.50:
            usable_peers_by_node.setdefault(source_eui, set()).add(target_eui)
            usable_peers_by_node.setdefault(target_eui, set()).add(source_eui)
        if scored["score"] >= 0.75:
            strong_peers_by_node.setdefault(source_eui, set()).add(target_eui)
            strong_peers_by_node.setdefault(target_eui, set()).add(source_eui)

    articulation_by_partition: dict[int, set[str]] = {}
    for partition_id, adjacency in partition_adjacency.items():
        if adjacency:
            articulation_by_partition[partition_id] = _find_articulation_points(adjacency)

    router_rows: dict[str, dict[str, Any]] = {}
    node_records: list[dict[str, Any]] = []
    router_redundancy_hits = 0
    router_path_diversity_hits = 0
    router_redundancy_scores: list[float] = []
    router_path_scores: list[float] = []

    for node in nodes:
        eui64 = str(node.get("eui64") or "").strip()
        if not eui64:
            continue
        role = str(node.get("routing_role") or "")
        age_seconds = _node_age_seconds(node.get("last_seen"), now=now)
        delta_1h = _counter_deltas(s, eui64=eui64, window=timedelta(hours=1), now=now)
        delta_24h = _counter_deltas(s, eui64=eui64, window=timedelta(hours=24), now=now)
        if role in _ROUTER_ROLES:
            partition_id = node.get("partition_id")
            strong_neighbor_count = len(strong_peers_by_node.get(eui64, set()))
            usable_neighbor_count = len(usable_peers_by_node.get(eui64, set()))
            alternate_path_count = max(0, usable_neighbor_count - 1)
            best_path_quality = max(peer_quality_by_node.get(eui64, [0.0]))
            articulation = bool(
                isinstance(partition_id, int)
                and eui64 in articulation_by_partition.get(partition_id, set())
            )
            router_result = scoring.score_router_health(
                strong_neighbor_count=strong_neighbor_count,
                alternate_path_count=alternate_path_count,
                best_path_quality=best_path_quality,
                retry_delta=delta_1h["deltas"].get("tx_retry_count"),
                age_seconds=age_seconds,
                articulation_risk=articulation,
                bridge_dependency=1.0 if articulation else 0.0,
                router_count=sum(1 for candidate in nodes if str(candidate.get("routing_role") or "") in _ROUTER_ROLES),
            )
            router_rows[eui64] = router_result
            router_redundancy_scores.append(float(router_result["components"]["redundancy"]))
            router_path_scores.append(float(router_result["components"]["path"]))
            if strong_neighbor_count >= int(router_result["target_strong_neighbors"]):
                router_redundancy_hits += 1
            if alternate_path_count >= 1:
                router_path_diversity_hits += 1
            node_record = {
                "eui64": eui64,
                "friendly_name": node.get("friendly_name"),
                "role": node.get("routing_role"),
                "device_kind": node.get("device_kind"),
                "score": router_result["score"],
                "band": router_result["band"],
                "confidence": router_result["confidence"],
                "reason_codes": router_result["reason_codes"],
                "metrics": {
                    "strong_neighbor_count": strong_neighbor_count,
                    "usable_neighbor_count": usable_neighbor_count,
                    "alternate_path_count": alternate_path_count,
                    "best_path_quality": round(best_path_quality, 4),
                    "retry_delta_1h": delta_1h["deltas"].get("tx_retry_count"),
                    "articulation_risk": articulation,
                    "bridge_dependency": 1.0 if articulation else 0.0,
                },
                "evidence": [],
            }
        else:
            parent_eui64 = node.get("parent_eui64")
            parent_edge = next(
                (
                    row for row in edge_rows
                    if row["source_eui64"] == parent_eui64 and row["target_eui64"] == eui64
                ),
                None,
            )
            parent_quality = float(parent_edge["score"]) if parent_edge else 0.5
            parent_router_health = float(router_rows.get(str(parent_eui64 or ""), {}).get("score", 0.5))
            end_result = scoring.score_end_device_health(
                parent_edge_quality=parent_quality,
                parent_change_delta_24h=int(delta_24h["deltas"].get("parent_change_count") or 0),
                parent_router_health=parent_router_health,
                retry_delta=delta_1h["deltas"].get("tx_retry_count"),
            )
            node_record = {
                "eui64": eui64,
                "friendly_name": node.get("friendly_name"),
                "role": node.get("routing_role"),
                "device_kind": node.get("device_kind"),
                "score": end_result["score"],
                "band": end_result["band"],
                "confidence": end_result["confidence"],
                "reason_codes": end_result["reason_codes"],
                "metrics": {
                    "parent_eui64": parent_eui64,
                    "parent_edge_quality": round(parent_quality, 4),
                    "parent_change_delta_24h": int(delta_24h["deltas"].get("parent_change_count") or 0),
                    "parent_router_health": round(parent_router_health, 4),
                    "retry_delta_1h": delta_1h["deltas"].get("tx_retry_count"),
                },
                "evidence": [],
            }
        node_records.append(node_record)

    router_count = max(1, len(router_rows))
    edge_scores = [float(row["score"]) for row in edge_rows if row.get("edge_class") in {"peer", "child"}]
    node_scores = [float(row["score"]) for row in node_records]
    confidence_scores = [float(row["confidence"]) for row in node_records] + [float(row["confidence"]) for row in edge_rows]

    network_result = scoring.score_network_health(
        router_redundancy=(sum(router_redundancy_scores) / len(router_redundancy_scores)) if router_redundancy_scores else 0.0,
        path_diversity=(sum(router_path_scores) / len(router_path_scores)) if router_path_scores else 0.0,
        link_quality=(sum(edge_scores) / len(edge_scores)) if edge_scores else 0.0,
        stability=(sum(node_scores) / len(node_scores)) if node_scores else 0.0,
        bottleneck_penalty=(sum(1.0 for row in router_rows.values() if "ARTICULATION_ROUTER" in row["reason_codes"]) / float(router_count)),
        partition_penalty=1.0 if snapshot.get("split") else 0.0,
        confidence_avg=(sum(confidence_scores) / len(confidence_scores)) if confidence_scores else 0.0,
    )
    if snapshot.get("split") and "PARTITION_RISK" not in network_result["reason_codes"]:
        network_result["reason_codes"].append("PARTITION_SPLIT")

    findings = _topology_findings(snapshot)
    placements = _placement_candidates(
        snapshot=snapshot,
        node_rows=node_by_eui,
        node_records=node_records,
        router_rows=router_rows,
        edge_rows=edge_rows,
    )
    return {
        "computed_at": now.isoformat(),
        "as_of": snapshot.get("computed_at"),
        "score": network_result["score"],
        "band": network_result["band"],
        "confidence": network_result["confidence"],
        "summary": {
            "router_count": len(router_rows),
            "end_device_count": sum(1 for row in node_records if row.get("device_kind") not in {"router", "reed"} and row.get("role") not in _ROUTER_ROLES),
            "strong_router_target": 2 if len(router_rows) >= 3 else max(1, len(router_rows) - 1),
            "router_redundancy_pct": round(float(router_redundancy_hits) / float(router_count), 4),
            "path_diversity_pct": round(float(router_path_diversity_hits) / float(router_count), 4),
            "distinct_partitions": len(snapshot.get("partitions") or []),
            "data_freshness_seconds": 0,
        },
        "component_scores": network_result["components"],
        "reason_codes": network_result["reason_codes"],
        "nodes": node_records,
        "edges": edge_rows,
        "findings": findings,
        "placement_candidates": placements,
    }