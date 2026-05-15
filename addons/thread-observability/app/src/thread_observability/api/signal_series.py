"""Event-backed signal time-series tools.

Exposes historical per-node RSSI/LQI samples from the canonical events table.
This is event-driven telemetry, not a continuous radio poll, so sparse series
mean the backend did not observe signal-bearing events in that window.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ..storage.sqlite_store import get_store
from ..utils.datetime import parse_iso_datetime

DEFAULT_LOOKBACK_HOURS = 24


def _resolve_window(since: str | None, until: str | None) -> tuple[str, str]:
    now = datetime.now(tz=UTC)
    until_dt = parse_iso_datetime(until) or now
    if since:
        since_dt = parse_iso_datetime(since) or (until_dt - timedelta(hours=DEFAULT_LOOKBACK_HOURS))
    else:
        since_dt = until_dt - timedelta(hours=DEFAULT_LOOKBACK_HOURS)
    return since_dt.isoformat(), until_dt.isoformat()


def _bucket_5min(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    bucket_order: list[str] = []
    for row in samples:
        dt = parse_iso_datetime(str(row.get("observed_at") or ""))
        if dt is None:
            continue
        bucket_minute = (dt.minute // 5) * 5
        bucket_dt = dt.replace(minute=bucket_minute, second=0, microsecond=0)
        key = bucket_dt.isoformat()
        if key not in buckets:
            buckets[key] = []
            bucket_order.append(key)
        buckets[key].append(row)

    out: list[dict[str, Any]] = []
    for key in bucket_order:
        members = buckets[key]
        rssi_values = [float(row["rssi"]) for row in members if isinstance(row.get("rssi"), (int, float))]
        lqi_values = [float(row["lqi"]) for row in members if isinstance(row.get("lqi"), (int, float))]
        out.append(
            {
                "observed_at": key,
                "rssi": round(sum(rssi_values) / len(rssi_values), 3) if rssi_values else None,
                "lqi": round(sum(lqi_values) / len(lqi_values), 3) if lqi_values else None,
                "sample_count": len(members),
            }
        )
    return out


def _metric_summary(series: list[dict[str, Any]], key: str) -> dict[str, Any]:
    values = [float(row[key]) for row in series if isinstance(row.get(key), (int, float))]
    if not values:
        return {}
    first = values[0]
    last = values[-1]
    return {
        "first": first,
        "last": last,
        "delta": round(last - first, 3),
        "min": min(values),
        "max": max(values),
        "avg": round(sum(values) / len(values), 3),
    }


def get_signal_series(
    *,
    eui64: str,
    since: str | None = None,
    until: str | None = None,
    resolution: str = "raw",
) -> dict[str, Any]:
    """Return event-backed RSSI/LQI time-series for one node."""
    if not eui64:
        return {"error": "eui64 is required", "series": [], "metrics": {}}
    res = resolution if resolution in {"raw", "5min"} else "raw"
    since_iso, until_iso = _resolve_window(since, until)
    rows = get_store().get_signal_samples(eui64=eui64, since=since_iso, until=until_iso)
    raw_series = [
        {
            "observed_at": row["ts"],
            "event_type": row.get("type"),
            "parent_eui64": row.get("parent_eui64"),
            "rssi": row.get("rssi"),
            "lqi": row.get("lqi"),
        }
        for row in rows
    ]
    series = _bucket_5min(raw_series) if res == "5min" else raw_series
    return {
        "eui64": eui64,
        "since": since_iso,
        "until": until_iso,
        "resolution": res,
        "series": series,
        "metrics": {
            "rssi": _metric_summary(series, "rssi"),
            "lqi": _metric_summary(series, "lqi"),
        },
        "sample_count": len(series),
        "event_sample_count": len(raw_series),
    }