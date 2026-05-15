"""Signal time-series API and MCP tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from thread_observability.api import mcp_tools
from thread_observability.api import signal_series
from thread_observability.api.http_api import create_core_app


def test_get_signal_samples_oldest_first_within_window(store):
    base = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
    for index in range(4):
        store.insert_event(
            eui64="NODE",
            type="parent_response",
            ts=(base + timedelta(minutes=index)).isoformat(),
            rssi=-70 + index,
            lqi=180 + index,
        )
    rows = store.get_signal_samples(
        eui64="NODE",
        since=(base + timedelta(minutes=1)).isoformat(),
        until=(base + timedelta(minutes=2)).isoformat(),
    )
    assert [(row["rssi"], row["lqi"]) for row in rows] == [(-69, 181), (-68, 182)]


def test_get_signal_series_returns_series_and_metrics(store):
    base = datetime.now(tz=UTC) - timedelta(minutes=10)
    store.insert_event(eui64="N1", type="parent_response", ts=base.isoformat(), rssi=-75, lqi=170)
    store.insert_event(eui64="N1", type="parent_response", ts=(base + timedelta(minutes=2)).isoformat(), rssi=-70, lqi=180)
    store.insert_event(eui64="N1", type="parent_response", ts=(base + timedelta(minutes=4)).isoformat(), rssi=-68, lqi=190)

    out = signal_series.get_signal_series(eui64="N1")
    assert out["sample_count"] == 3
    assert len(out["series"]) == 3
    assert out["metrics"]["rssi"]["delta"] == 7.0
    assert out["metrics"]["lqi"]["delta"] == 20.0
    assert out["metrics"]["rssi"]["first"] == -75.0
    assert out["metrics"]["rssi"]["last"] == -68.0


def test_get_signal_series_5min_resolution_buckets(store):
    base = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
    store.insert_event(eui64="B1", type="parent_response", ts=base.isoformat(), rssi=-80, lqi=150)
    store.insert_event(eui64="B1", type="parent_response", ts=(base + timedelta(minutes=1)).isoformat(), rssi=-70, lqi=170)
    store.insert_event(eui64="B1", type="parent_response", ts=(base + timedelta(minutes=6)).isoformat(), rssi=-60, lqi=190)

    out = signal_series.get_signal_series(
        eui64="B1",
        since=(base - timedelta(minutes=1)).isoformat(),
        until=(base + timedelta(minutes=10)).isoformat(),
        resolution="5min",
    )
    assert len(out["series"]) == 2
    assert out["series"][0]["rssi"] == -75.0
    assert out["series"][0]["lqi"] == 160.0
    assert out["series"][0]["sample_count"] == 2


def test_get_signal_series_requires_eui64(store):  # noqa: ARG001
    out = signal_series.get_signal_series(eui64="")
    assert "error" in out


def test_signal_series_registered_in_mcp_catalog():
    names = {tool["name"] for tool in mcp_tools.TOOL_DEFS}
    assert "get_signal_series" in names
    assert "get_signal_series" in mcp_tools._READ_TOOLS


def test_signal_series_http_endpoint_returns_series(store):
    base = datetime.now(tz=UTC) - timedelta(minutes=5)
    store.insert_event(eui64="aa" * 8, type="parent_response", ts=base.isoformat(), rssi=-72, lqi=175)
    store.insert_event(eui64="aa" * 8, type="parent_response", ts=(base + timedelta(minutes=1)).isoformat(), rssi=-68, lqi=185)

    client = TestClient(create_core_app())
    response = client.get(f"/v1/signals/{'aa' * 8}/series")

    assert response.status_code == 200
    body = response.json()
    assert body["sample_count"] == 2
    assert body["metrics"]["rssi"]["delta"] == 4.0