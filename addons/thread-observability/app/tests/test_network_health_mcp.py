from __future__ import annotations

import asyncio
from pathlib import Path

from thread_observability.api import mcp_tools

from .scenarios.loader import load_scenario, seed_store


_FIXTURE_DIR = Path(__file__).parent / "scenarios" / "fixtures"


def _load_fixture(name: str) -> dict[str, object]:
    return load_scenario(_FIXTURE_DIR / f"{name}.json")


def test_network_health_tools_registered_in_mcp_catalog() -> None:
    names = {tool["name"] for tool in mcp_tools.TOOL_DEFS}
    assert {"get_network_health", "get_placement_candidates"}.issubset(names)
    assert {"get_network_health", "get_placement_candidates"}.issubset(mcp_tools._READ_TOOLS)


def test_get_network_health_mcp_tool_wraps_network_health_payload(store) -> None:
    scenario = _load_fixture("single_otbr_three_routers")
    seed_store(store, scenario)

    payload = asyncio.run(mcp_tools._dispatch_and_wrap("get_network_health", {}))

    assert payload["data"]["summary"]["router_count"] == 3
    assert isinstance(payload["data"]["findings"], list)
    assert payload["meta"]["tool"] == "get_network_health"


def test_get_placement_candidates_mcp_tool_wraps_candidate_payload(store) -> None:
    scenario = _load_fixture("single_otbr_three_routers")
    seed_store(store, scenario)

    payload = asyncio.run(mcp_tools._dispatch_and_wrap("get_placement_candidates", {}))

    assert isinstance(payload["data"]["candidates"], list)
    assert payload["data"]["candidates"]
    assert any(len(candidate["affected_nodes"]) > 1 for candidate in payload["data"]["candidates"])
    assert payload["meta"]["tool"] == "get_placement_candidates"