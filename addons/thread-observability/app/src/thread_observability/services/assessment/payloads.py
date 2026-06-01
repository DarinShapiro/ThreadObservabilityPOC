from __future__ import annotations

from typing import Any

from ...storage.sqlite_store import SQLiteStore, get_store


def build_network_ai_assessment_payload(
    *,
    network_health_payload: dict[str, Any],
    store: SQLiteStore | None = None,
) -> dict[str, Any] | None:
    subject = store or get_store()
    latest_run = next(iter(subject.list_assessment_runs(limit=1)), None)
    active_finding = next(iter(subject.list_assessment_findings(state="open", limit=1)), None)
    if not latest_run and not active_finding:
        return None

    source = active_finding or latest_run or {}
    evidence = list((active_finding or {}).get("evidence") or [])
    assessed_at = (
        (active_finding or {}).get("last_seen_at")
        or (active_finding or {}).get("created_at")
        or (latest_run or {}).get("assessed_at")
    )
    status = "active" if active_finding else "recent"
    return {
        "status": status,
        "based_on": {
            "network_health_computed_at": network_health_payload.get("computed_at"),
            "as_of": network_health_payload.get("as_of"),
            "assessment_run_at": (latest_run or {}).get("assessed_at"),
        },
        "headline": source.get("headline"),
        "verdict": (latest_run or {}).get("verdict"),
        "severity": source.get("severity") or (latest_run or {}).get("severity"),
        "confidence": source.get("confidence") if source.get("confidence") is not None else (latest_run or {}).get("confidence"),
        "finding_type": source.get("finding_type"),
        "finding_id": (active_finding or {}).get("finding_id") or (latest_run or {}).get("finding_id"),
        "finding_key": (active_finding or {}).get("finding_key") or (latest_run or {}).get("finding_key"),
        "node_eui64": source.get("node_eui64"),
        "assessed_at": assessed_at,
        "suggested_starter_prompt": (active_finding or {}).get("suggested_starter_prompt"),
        "evidence": evidence,
    }