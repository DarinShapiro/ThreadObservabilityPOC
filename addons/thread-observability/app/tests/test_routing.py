"""Tests for the server-side route walker and neighbors enricher."""

from __future__ import annotations

from thread_observability.pipeline import routing
from thread_observability.pipeline.nodes import list_nodes_enriched  # noqa: F401

# Reuse the topology setup from test_nodes.
from .test_nodes import _setup_three_router_partition  # type: ignore[import-not-found]


def test_walk_route_to_otbr_direct(store) -> None:
    otbr, rb, _ = _setup_three_router_partition(store)
    result = routing.walk_route_to_otbr(rb, store=store)
    assert result["complete"] is True
    assert result["otbr_eui64"] == otbr
    assert result["issues"] == []
    assert result["hop_count"] == 2
    assert [h["eui64"] for h in result["hops"]] == [rb, otbr]
    assert result["hops"][1]["is_otbr"] is True
    assert result["hops"][1]["path_cost"] == 1


def test_walk_route_to_otbr_multihop(store) -> None:
    otbr, rb, rc = _setup_three_router_partition(store)
    result = routing.walk_route_to_otbr(rc, store=store)
    assert result["complete"] is True
    assert result["hop_count"] == 3
    assert [h["eui64"] for h in result["hops"]] == [rc, rb, otbr]
    # Last hop is the OTBR; the middle hop is Router B with path_cost=2
    # (the route_table row from rc → otbr reports total cost 2).
    assert result["hops"][1]["eui64"] == rb
    assert result["hops"][2]["is_otbr"] is True


def test_walk_route_no_otbr(store) -> None:
    # Empty store has no OTBR.
    result = routing.walk_route_to_otbr("aa" * 8, store=store)
    assert result["complete"] is False
    assert result["otbr_eui64"] is None
    assert any(i["code"] == "no_otbr" for i in result["issues"])


def test_walk_route_self_is_otbr(store) -> None:
    otbr, _, _ = _setup_three_router_partition(store)
    result = routing.walk_route_to_otbr(otbr, store=store)
    assert result["complete"] is True
    assert result["hop_count"] == 1
    assert result["hops"][0]["is_otbr"] is True
    assert any(i["code"] == "self_is_otbr" for i in result["issues"])


def test_find_otbr(store) -> None:
    otbr, _, _ = _setup_three_router_partition(store)
    found = routing.find_otbr(store=store)
    assert found is not None
    assert found["eui64"] == otbr


def test_list_neighbors_enriched(store) -> None:
    otbr, rb, rc = _setup_three_router_partition(store)
    # Add a neighbor_table entry so the neighbors list is non-empty.
    store.replace_links_for_reporter(rb, "neighbor_table", [
        {"neighbor_eui64": otbr, "rssi_avg": -55, "lqi_in": 240, "is_child": False,
         "rx_on_when_idle": True, "full_thread_device": True, "full_network_data": True},
        {"neighbor_eui64": rc, "rssi_avg": -70, "lqi_in": 180, "is_child": False},
    ])
    out = routing.list_neighbors_enriched(rb, store=store)
    assert out["reporter_eui64"] == rb
    assert out["reporter_name"] == "Router B"
    assert out["neighbor_count"] == 2
    assert out["route_count"] == 3
    # OTBR neighbor should be enriched with its friendly name.
    otbr_row = next(n for n in out["neighbors"] if n["neighbor_eui64"] == otbr)
    assert otbr_row["name"] == "HA Yellow OTBR"
    assert otbr_row["rx_on_when_idle"] == 1
    assert otbr_row["full_thread_device"] == 1
    # Route to rc: next_hop_router_id=12 (rc itself) → next_hop_eui64=rc.
    rc_route = next(r for r in out["routes"] if r["neighbor_eui64"] == rc)
    assert rc_route["next_hop_router_id"] == 12
    assert rc_route["next_hop_eui64"] == rc
    assert rc_route["next_hop_name"] == "Router C"


def test_topology_edge_class(store) -> None:
    """`/v1/topology` links must come back with `edge_class` populated."""
    from thread_observability.pipeline import topology

    otbr, rb, rc = _setup_three_router_partition(store)
    # Add a neighbor_table row both directions so the dedup kicks in.
    store.replace_links_for_reporter(rb, "neighbor_table", [
        {"neighbor_eui64": rc, "rssi_avg": -70, "is_child": False},
    ])
    store.replace_links_for_reporter(rc, "neighbor_table", [
        {"neighbor_eui64": rb, "rssi_avg": -72, "is_child": False},
    ])
    snap = topology.build_topology(store=store)
    classes = {ln["edge_class"] for ln in snap["links"]}
    assert "peer" in classes  # rb<->rc collapsed to one peer edge
    assert "route" in classes  # route_table entries
    # Only one peer edge for the rb/rc pair.
    peer_edges = [ln for ln in snap["links"] if ln["edge_class"] == "peer"]
    assert len(peer_edges) == 1
