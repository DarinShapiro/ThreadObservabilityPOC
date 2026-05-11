"""Deterministic Thread anomaly reasoner.

Scans the SQLite event stream and opens/closes issues via the issues
table. All rules are deterministic and side-effect-isolated: the only
mutation is ``open_issue`` / ``close_issue`` calls on the store.

Rules (v1):

* ``parent_churn`` (warn) — a node emitted >= 3 ``parent_change`` events
  within the last :data:`PARENT_CHURN_WINDOW_MIN` minutes.
* ``attach_failures`` (warn) — a node emitted >= 2 ``attach_failed``
  events within the last :data:`ATTACH_FAIL_WINDOW_MIN` minutes.
* ``offline_node`` (crit) — a node has not been seen for at least
  :data:`OFFLINE_THRESHOLD_MIN` minutes despite having been seen before.

Each run also auto-closes issues whose triggering condition no longer
holds (e.g. churn dropped below threshold, node came back online).
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from ..storage.sqlite_store import SQLiteStore, get_store

PARENT_CHURN_WINDOW_MIN = 30
PARENT_CHURN_THRESHOLD = 3

ATTACH_FAIL_WINDOW_MIN = 15
ATTACH_FAIL_THRESHOLD = 2

OFFLINE_THRESHOLD_MIN = 30


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def run_reasoner(
    *,
    now: datetime | None = None,
    store: SQLiteStore | None = None,
) -> dict[str, Any]:
    """Run all rules once and reconcile open issues.

    Returns a summary dict with the lists of newly-opened, still-open and
    auto-closed issue ids per rule.
    """
    s = store or get_store()
    now_dt = now or datetime.now(tz=UTC)

    opened: list[int] = []
    closed: list[int] = []
    skipped: list[int] = []

    # ---- gather raw inputs in one lock ----
    churn_window = _iso(now_dt - timedelta(minutes=PARENT_CHURN_WINDOW_MIN))
    attach_window = _iso(now_dt - timedelta(minutes=ATTACH_FAIL_WINDOW_MIN))
    offline_cutoff = _iso(now_dt - timedelta(minutes=OFFLINE_THRESHOLD_MIN))

    with s._lock:  # noqa: SLF001
        churn_rows = s._conn.execute(  # noqa: SLF001
            "SELECT eui64, COUNT(*) AS c FROM events"
            " WHERE type = 'parent_change' AND ts >= ?"
            " GROUP BY eui64",
            (churn_window,),
        ).fetchall()

        attach_rows = s._conn.execute(  # noqa: SLF001
            "SELECT eui64, COUNT(*) AS c FROM events"
            " WHERE type = 'attach_failed' AND ts >= ?"
            " GROUP BY eui64",
            (attach_window,),
        ).fetchall()

        node_rows = s._conn.execute(  # noqa: SLF001
            "SELECT eui64, last_seen FROM nodes WHERE last_seen IS NOT NULL"
        ).fetchall()

    churn_counts: Counter[str] = Counter({r["eui64"]: int(r["c"]) for r in churn_rows})
    attach_counts: Counter[str] = Counter({r["eui64"]: int(r["c"]) for r in attach_rows})
    offline_nodes = {r["eui64"]: r["last_seen"] for r in node_rows if r["last_seen"] < offline_cutoff}

    active = s.list_active_issues()
    active_by_key: dict[tuple[str, str | None], dict[str, Any]] = {
        (i["kind"], i.get("eui64")): i for i in active
    }

    def _emit(kind: str, severity: str, eui64: str | None, evidence: dict[str, Any]) -> None:
        issue_id = s.open_issue(kind=kind, severity=severity, eui64=eui64, evidence=evidence)
        if (kind, eui64) in active_by_key:
            skipped.append(issue_id)
        else:
            opened.append(issue_id)

    # ---- parent_churn ----
    seen_keys: set[tuple[str, str | None]] = set()
    for eui, count in churn_counts.items():
        if count >= PARENT_CHURN_THRESHOLD:
            seen_keys.add(("parent_churn", eui))
            _emit(
                "parent_churn",
                "warn",
                eui,
                {
                    "count": count,
                    "window_minutes": PARENT_CHURN_WINDOW_MIN,
                    "threshold": PARENT_CHURN_THRESHOLD,
                },
            )

    # ---- attach_failures ----
    for eui, count in attach_counts.items():
        if count >= ATTACH_FAIL_THRESHOLD:
            seen_keys.add(("attach_failures", eui))
            _emit(
                "attach_failures",
                "warn",
                eui,
                {
                    "count": count,
                    "window_minutes": ATTACH_FAIL_WINDOW_MIN,
                    "threshold": ATTACH_FAIL_THRESHOLD,
                },
            )

    # ---- offline_node ----
    for eui, last_seen in offline_nodes.items():
        seen_keys.add(("offline_node", eui))
        _emit(
            "offline_node",
            "crit",
            eui,
            {"last_seen": last_seen, "threshold_minutes": OFFLINE_THRESHOLD_MIN},
        )

    # ---- auto-close issues whose trigger no longer holds ----
    managed_kinds = {"parent_churn", "attach_failures", "offline_node"}
    for (kind, eui), issue in active_by_key.items():
        if kind not in managed_kinds:
            continue
        if (kind, eui) in seen_keys:
            continue
        if s.close_issue(int(issue["id"])):
            closed.append(int(issue["id"]))

    return {
        "ran_at": _iso(now_dt),
        "opened": opened,
        "still_open": skipped,
        "closed": closed,
        "rules": {
            "parent_churn": {
                "window_minutes": PARENT_CHURN_WINDOW_MIN,
                "threshold": PARENT_CHURN_THRESHOLD,
            },
            "attach_failures": {
                "window_minutes": ATTACH_FAIL_WINDOW_MIN,
                "threshold": ATTACH_FAIL_THRESHOLD,
            },
            "offline_node": {"threshold_minutes": OFFLINE_THRESHOLD_MIN},
        },
    }
