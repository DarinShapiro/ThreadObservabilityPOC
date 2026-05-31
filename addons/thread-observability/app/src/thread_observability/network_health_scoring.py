"""Deterministic network health scoring helpers.

These functions intentionally stay pure and endpoint-free so the scoring
model can be validated with fixtures before any HTTP or MCP surface is
implemented around it.
"""

from __future__ import annotations

from typing import Any


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def normalize_rssi(rssi: float | int | None) -> float | None:
    if rssi is None:
        return None
    return round(_clamp((float(rssi) + 95.0) / 40.0), 4)


def normalize_lqi(lqi: float | int | None, *, max_lqi: int = 3) -> float | None:
    if lqi is None:
        return None
    ceiling = 255 if max_lqi > 3 else max(1, max_lqi)
    return round(_clamp(float(lqi) / float(ceiling)), 4)


def normalize_retry_delta(retry_delta: float | int | None, *, ceiling: int = 20) -> float | None:
    if retry_delta is None:
        return None
    penalty = _clamp(float(retry_delta) / float(max(1, ceiling)))
    return round(1.0 - penalty, 4)


def normalize_freshness(age_seconds: float | int | None, *, stale_after_seconds: int = 1800) -> float | None:
    if age_seconds is None:
        return None
    return round(_clamp(1.0 - (float(age_seconds) / float(max(1, stale_after_seconds)))), 4)


def normalize_symmetry(rssi_forward: float | int | None, rssi_reverse: float | int | None) -> float:
    if rssi_forward is None or rssi_reverse is None:
        return 0.5
    return round(_clamp(1.0 - (abs(float(rssi_forward) - float(rssi_reverse)) / 20.0)), 4)


def _weighted_score(parts: list[tuple[float, float | None]]) -> float:
    weighted = [(weight, value) for weight, value in parts if value is not None]
    if not weighted:
        return 0.0
    total_weight = sum(weight for weight, _ in weighted)
    return round(sum(weight * float(value) for weight, value in weighted) / total_weight, 4)


def _health_band(score: float) -> str:
    if score >= 0.8:
        return "healthy"
    if score >= 0.6:
        return "watch"
    if score >= 0.4:
        return "investigate"
    return "critical"


def _confidence(
    *,
    age_seconds: float | int | None = None,
    one_way_only: bool = False,
    missing_retry_data: bool = False,
    topology_disagreement: bool = False,
) -> float:
    confidence = 1.0
    if age_seconds is not None and age_seconds > 600:
        confidence -= 0.15
    if age_seconds is not None and age_seconds > 1800:
        confidence = min(confidence, 0.25)
    if one_way_only:
        confidence -= 0.20
    if missing_retry_data:
        confidence -= 0.10
    if topology_disagreement:
        confidence -= 0.20
    return round(_clamp(confidence), 4)


def score_edge_quality(
    *,
    rssi: float | int | None = None,
    lqi: float | int | None = None,
    retry_delta: float | int | None = None,
    age_seconds: float | int | None = None,
    reverse_rssi: float | int | None = None,
    lqi_scale: int = 3,
) -> dict[str, Any]:
    rssi_n = normalize_rssi(rssi)
    lqi_n = normalize_lqi(lqi, max_lqi=lqi_scale)
    retry_n = normalize_retry_delta(retry_delta)
    freshness_n = normalize_freshness(age_seconds)
    symmetry_n = normalize_symmetry(rssi, reverse_rssi)
    score = _weighted_score([
        (0.35, rssi_n),
        (0.25, lqi_n),
        (0.20, retry_n),
        (0.10, freshness_n),
        (0.10, symmetry_n),
    ])
    stale = age_seconds is not None and float(age_seconds) > 1800
    band = "unknown_stale" if stale else (
        "strong" if score >= 0.75 else "usable" if score >= 0.50 else "weak" if score >= 0.25 else "critical"
    )
    reason_codes: list[str] = []
    if rssi is not None and float(rssi) < -85:
        reason_codes.append("WEAK_RSSI")
    if lqi is not None and float(lqi) <= (1 if lqi_scale <= 3 else 85):
        reason_codes.append("POOR_LQI")
    if retry_delta is not None and float(retry_delta) >= 5:
        reason_codes.append("HIGH_RETRY_RATE")
    if reverse_rssi is None:
        reason_codes.append("ONE_WAY_LINK_EVIDENCE")
    elif abs(float(rssi or 0) - float(reverse_rssi)) > 10:
        reason_codes.append("ASYMMETRIC_LINK")
    if stale:
        reason_codes.append("STALE_LINK_DATA")
    return {
        "score": score,
        "band": band,
        "confidence": _confidence(
            age_seconds=age_seconds,
            one_way_only=reverse_rssi is None,
            missing_retry_data=retry_delta is None,
        ),
        "reason_codes": reason_codes,
        "components": {
            "rssi": rssi_n,
            "lqi": lqi_n,
            "retry": retry_n,
            "freshness": freshness_n,
            "symmetry": symmetry_n,
        },
    }


def score_router_health(
    *,
    strong_neighbor_count: int,
    alternate_path_count: int,
    best_path_quality: float,
    retry_delta: float | int | None = None,
    age_seconds: float | int | None = None,
    articulation_risk: bool = False,
    bridge_dependency: float = 0.0,
    router_count: int = 3,
) -> dict[str, Any]:
    target = min(2, max(1, int(router_count) - 1))
    redundancy = _clamp(float(strong_neighbor_count) / float(max(1, target)))
    path = (0.60 * _clamp(best_path_quality)) + (0.40 * min(1.0, float(alternate_path_count)))
    stability = (0.70 * (normalize_retry_delta(retry_delta) if retry_delta is not None else 0.5)) + (
        0.30 * (normalize_freshness(age_seconds) if age_seconds is not None else 0.5)
    )
    bottleneck = 1.0 - _clamp((0.7 * (1.0 if articulation_risk else 0.0)) + (0.3 * float(bridge_dependency)))
    score = round((0.40 * redundancy) + (0.25 * path) + (0.20 * stability) + (0.15 * bottleneck), 4)
    if articulation_risk and alternate_path_count < 1 and _clamp(best_path_quality) < 0.5:
        score = min(score, 0.39)
    reason_codes: list[str] = []
    if strong_neighbor_count < target:
        reason_codes.append("LOW_ROUTER_REDUNDANCY")
    if alternate_path_count < 1:
        reason_codes.append("NO_ALTERNATE_PATH")
    if _clamp(best_path_quality) < 0.5:
        reason_codes.append("WEAK_BACKBONE_PATH")
    if articulation_risk:
        reason_codes.append("ARTICULATION_ROUTER")
    if float(bridge_dependency) >= 0.5:
        reason_codes.append("HIGH_BOTTLENECK_CENTRALITY")
    return {
        "score": score,
        "band": _health_band(score),
        "confidence": _confidence(age_seconds=age_seconds, missing_retry_data=retry_delta is None),
        "reason_codes": reason_codes,
        "target_strong_neighbors": target,
        "components": {
            "redundancy": round(redundancy, 4),
            "path": round(path, 4),
            "stability": round(stability, 4),
            "bottleneck": round(bottleneck, 4),
        },
    }


def score_end_device_health(
    *,
    parent_edge_quality: float,
    parent_change_delta_24h: int,
    parent_router_health: float,
    retry_delta: float | int | None = None,
) -> dict[str, Any]:
    parent_stability = 1.0 - _clamp(float(parent_change_delta_24h) / 3.0)
    retry_n = normalize_retry_delta(retry_delta) if retry_delta is not None else 0.5
    score = round(
        (0.45 * _clamp(parent_edge_quality))
        + (0.25 * parent_stability)
        + (0.20 * _clamp(parent_router_health))
        + (0.10 * retry_n),
        4,
    )
    reason_codes: list[str] = []
    if _clamp(parent_edge_quality) < 0.5:
        reason_codes.append("MARGINAL_PARENT_LINK")
    if parent_change_delta_24h >= 3:
        reason_codes.append("PARENT_FLAPPING")
    if _clamp(parent_router_health) < 0.6:
        reason_codes.append("FRAGILE_PARENT_ROUTER")
    return {
        "score": score,
        "band": _health_band(score),
        "confidence": _confidence(missing_retry_data=retry_delta is None),
        "reason_codes": reason_codes,
        "components": {
            "parent_edge_quality": round(_clamp(parent_edge_quality), 4),
            "parent_stability": round(parent_stability, 4),
            "parent_router_health": round(_clamp(parent_router_health), 4),
            "retry": round(retry_n, 4),
        },
    }


def score_network_health(
    *,
    router_redundancy: float,
    path_diversity: float,
    link_quality: float,
    stability: float,
    bottleneck_penalty: float,
    partition_penalty: float,
    confidence_avg: float,
) -> dict[str, Any]:
    score = round(
        (0.30 * _clamp(router_redundancy))
        + (0.20 * _clamp(path_diversity))
        + (0.20 * _clamp(link_quality))
        + (0.15 * _clamp(stability))
        + (0.10 * (1.0 - _clamp(bottleneck_penalty)))
        + (0.05 * (1.0 - _clamp(partition_penalty))),
        4,
    )
    reason_codes: list[str] = []
    if _clamp(router_redundancy) < 0.6:
        reason_codes.append("LOW_NETWORK_REDUNDANCY")
    if _clamp(bottleneck_penalty) >= 0.5:
        reason_codes.append("OVERCONCENTRATED_ROUTING")
    if _clamp(partition_penalty) >= 0.5:
        reason_codes.append("PARTITION_RISK")
    if _clamp(confidence_avg) < 0.5:
        reason_codes.append("LOW_CONFIDENCE_SNAPSHOT")
    return {
        "score": score,
        "band": _health_band(score),
        "confidence": round(_clamp(confidence_avg), 4),
        "reason_codes": reason_codes,
        "components": {
            "router_redundancy": round(_clamp(router_redundancy), 4),
            "path_diversity": round(_clamp(path_diversity), 4),
            "link_quality": round(_clamp(link_quality), 4),
            "stability": round(_clamp(stability), 4),
            "bottleneck_penalty": round(_clamp(bottleneck_penalty), 4),
            "partition_penalty": round(_clamp(partition_penalty), 4),
        },
    }


def score_placement_opportunity(
    *,
    redundancy_delta: float,
    path_diversity_delta: float,
    bottleneck_reduction: float,
    affected_nodes_norm: float,
) -> float:
    return round(
        (0.35 * _clamp(redundancy_delta))
        + (0.30 * _clamp(path_diversity_delta))
        + (0.20 * _clamp(bottleneck_reduction))
        + (0.15 * _clamp(affected_nodes_norm)),
        4,
    )