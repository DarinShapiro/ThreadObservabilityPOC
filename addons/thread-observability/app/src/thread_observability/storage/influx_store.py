"""Time-series store abstraction.

v1 ships with two backends:

* ``InfluxDBStore`` \u2014 thin HTTP client targeting an InfluxDB v2 instance
  (typically the official ``influxdb`` HA add-on); writes line-protocol over
  ``/api/v2/write`` and queries Flux over ``/api/v2/query``.
* ``SQLiteFallbackStore`` \u2014 used when Influx is not reachable. Persists
  numeric samples into a small ``samples`` table inside the main SQLite DB so
  the rest of the system keeps working in single-process deployments.

Selection is automatic via :func:`get_timeseries_store` based on
``ThreadObsConfig.influx`` settings; callers receive an instance that satisfies
the same ``write_point`` / ``query_range`` interface.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Iterable

import httpx

from .sqlite_store import get_store

log = logging.getLogger(__name__)


def _utc_now_ns() -> int:
    return int(datetime.now(tz=UTC).timestamp() * 1_000_000_000)


@dataclass(slots=True)
class InfluxSettings:
    url: str = os.getenv("INFLUX_URL", "http://a0d7b954-influxdb:8086")
    org: str = os.getenv("INFLUX_ORG", "thread-observability")
    bucket: str = os.getenv("INFLUX_BUCKET", "thread")
    token: str = os.getenv("INFLUX_TOKEN", "")
    timeout: float = 5.0


class TimeseriesUnavailable(RuntimeError):
    """Raised when neither Influx nor the fallback store is usable."""


class InfluxDBStore:
    """HTTP client for InfluxDB v2 line protocol."""

    def __init__(self, settings: InfluxSettings) -> None:
        self.settings = settings

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "text/plain; charset=utf-8"}
        if self.settings.token:
            h["Authorization"] = f"Token {self.settings.token}"
        return h

    async def health(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.settings.timeout) as client:
            r = await client.get(f"{self.settings.url}/health")
        try:
            payload = r.json()
        except Exception:  # noqa: BLE001
            payload = {"raw": r.text}
        return {"status_code": r.status_code, "payload": payload}

    async def write_point(
        self,
        *,
        measurement: str,
        tags: dict[str, str] | None = None,
        fields: dict[str, float | int | bool | str],
        ts_ns: int | None = None,
    ) -> None:
        line = _to_line_protocol(measurement, tags or {}, fields, ts_ns or _utc_now_ns())
        url = f"{self.settings.url}/api/v2/write"
        params = {"org": self.settings.org, "bucket": self.settings.bucket, "precision": "ns"}
        async with httpx.AsyncClient(timeout=self.settings.timeout) as client:
            r = await client.post(url, headers=self._headers(), params=params, content=line)
            r.raise_for_status()

    async def query_range(
        self,
        *,
        measurement: str,
        field: str,
        eui64: str | None = None,
        minutes: int = 60,
    ) -> list[dict[str, Any]]:
        flux = (
            f'from(bucket: "{self.settings.bucket}") '
            f"|> range(start: -{minutes}m) "
            f'|> filter(fn: (r) => r._measurement == "{measurement}") '
            f'|> filter(fn: (r) => r._field == "{field}")'
        )
        if eui64:
            flux += f' |> filter(fn: (r) => r.eui64 == "{eui64}")'
        url = f"{self.settings.url}/api/v2/query"
        params = {"org": self.settings.org}
        headers = {**self._headers(), "Content-Type": "application/vnd.flux"}
        async with httpx.AsyncClient(timeout=self.settings.timeout) as client:
            r = await client.post(url, headers=headers, params=params, content=flux)
            r.raise_for_status()
            return _parse_flux_csv(r.text)


def _to_line_protocol(
    measurement: str,
    tags: dict[str, str],
    fields: dict[str, float | int | bool | str],
    ts_ns: int,
) -> str:
    tag_str = ",".join(
        f"{_lp_escape(k)}={_lp_escape(str(v))}" for k, v in sorted(tags.items())
    )
    field_parts: list[str] = []
    for k, v in fields.items():
        if isinstance(v, bool):
            field_parts.append(f"{_lp_escape(k)}={'true' if v else 'false'}")
        elif isinstance(v, (int,)):
            field_parts.append(f"{_lp_escape(k)}={v}i")
        elif isinstance(v, float):
            field_parts.append(f"{_lp_escape(k)}={v}")
        else:
            esc = str(v).replace("\\", "\\\\").replace('"', '\\"')
            field_parts.append(f'{_lp_escape(k)}="{esc}"')
    prefix = f"{_lp_escape(measurement)}"
    if tag_str:
        prefix += f",{tag_str}"
    return f"{prefix} {','.join(field_parts)} {ts_ns}"


def _lp_escape(s: str) -> str:
    return s.replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")


def _parse_flux_csv(text: str) -> list[dict[str, Any]]:
    # Lightweight CSV parser tailored for Flux annotated CSV; sufficient for
    # internal use. For production we may swap in csv.reader with annotation
    # awareness.
    out: list[dict[str, Any]] = []
    header: list[str] | None = None
    for raw in text.splitlines():
        if not raw or raw.startswith("#"):
            continue
        parts = raw.split(",")
        if header is None:
            header = parts
            continue
        out.append(dict(zip(header, parts, strict=False)))
    return out


# ---------------------------------------------------------------------------
# SQLite fallback
# ---------------------------------------------------------------------------

_FALLBACK_DDL = """
CREATE TABLE IF NOT EXISTS samples (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    measurement TEXT NOT NULL,
    eui64       TEXT,
    field       TEXT NOT NULL,
    value       REAL NOT NULL,
    tags_json   TEXT
);
CREATE INDEX IF NOT EXISTS idx_samples_lookup
    ON samples(measurement, field, eui64, ts DESC);
"""


class SQLiteFallbackStore:
    """Drop-in fallback that persists numeric samples in the main SQLite DB."""

    def __init__(self) -> None:
        self._sqlite = get_store()
        # Ensure the ``samples`` table exists; safe to call repeatedly.
        with self._sqlite._lock:  # noqa: SLF001 - shared connection on purpose
            self._sqlite._conn.executescript(_FALLBACK_DDL)  # noqa: SLF001

    async def health(self) -> dict[str, Any]:
        return {"status_code": 200, "payload": {"backend": "sqlite-fallback"}}

    async def write_point(
        self,
        *,
        measurement: str,
        tags: dict[str, str] | None = None,
        fields: dict[str, float | int | bool | str],
        ts_ns: int | None = None,
    ) -> None:
        tags = tags or {}
        ts = (
            datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=UTC).isoformat()
            if ts_ns
            else datetime.now(tz=UTC).isoformat()
        )
        eui64 = tags.get("eui64")
        tags_json = json.dumps({k: v for k, v in tags.items() if k != "eui64"})
        with self._sqlite._tx() as conn:  # noqa: SLF001
            for fname, fval in fields.items():
                try:
                    fnum = float(fval)
                except (TypeError, ValueError):
                    continue  # fallback only stores numeric samples
                conn.execute(
                    "INSERT INTO samples(ts, measurement, eui64, field, value, tags_json)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (ts, measurement, eui64, fname, fnum, tags_json),
                )

    async def query_range(
        self,
        *,
        measurement: str,
        field: str,
        eui64: str | None = None,
        minutes: int = 60,
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT ts, value FROM samples "
            "WHERE measurement = ? AND field = ? "
            "AND ts >= datetime('now', ?) "
        )
        params: list[Any] = [measurement, field, f"-{int(minutes)} minutes"]
        if eui64:
            sql += "AND eui64 = ? "
            params.append(eui64)
        sql += "ORDER BY ts DESC LIMIT 5000"
        with self._sqlite._lock:  # noqa: SLF001
            rows: Iterable[sqlite3.Row] = self._sqlite._conn.execute(sql, params).fetchall()  # noqa: SLF001
        return [{"ts": r[0], "value": r[1]} for r in rows]


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

_ts_store: InfluxDBStore | SQLiteFallbackStore | None = None


async def get_timeseries_store(settings: InfluxSettings | None = None) -> InfluxDBStore | SQLiteFallbackStore:
    """Return a working time-series backend, preferring Influx when reachable."""
    global _ts_store
    if _ts_store is not None:
        return _ts_store
    settings = settings or InfluxSettings()
    if settings.token and settings.url:
        candidate = InfluxDBStore(settings)
        try:
            h = await candidate.health()
            if 200 <= int(h.get("status_code", 0)) < 300:
                _ts_store = candidate
                log.info("timeseries backend: InfluxDB at %s", settings.url)
                return _ts_store
        except Exception as exc:  # noqa: BLE001
            log.info("InfluxDB unreachable (%s); using SQLite fallback", exc)
    _ts_store = SQLiteFallbackStore()
    log.info("timeseries backend: SQLite fallback")
    return _ts_store


async def timeseries_health() -> dict[str, Any]:
    store = await get_timeseries_store()
    h = await store.health()
    h["backend"] = "influx" if isinstance(store, InfluxDBStore) else "sqlite-fallback"
    return h
