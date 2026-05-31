from __future__ import annotations

import json
from pathlib import Path

from thread_observability import network_health_scoring as scoring


def _cases() -> dict[str, object]:
    fixture_path = Path(__file__).parent / "fixtures" / "network_health_scoring_cases.json"
    return json.loads(fixture_path.read_text(encoding="utf-8"))


def _assert_case(result: dict[str, object], expected: dict[str, object]) -> None:
    if "band" in expected:
        assert result["band"] == expected["band"]
    if "score_min" in expected:
        assert float(result["score"]) >= float(expected["score_min"])
    if "score_max" in expected:
        assert float(result["score"]) <= float(expected["score_max"])
    for code in expected.get("reason_codes_present", []):
        assert code in result["reason_codes"]
    for code in expected.get("reason_codes_absent", []):
        assert code not in result["reason_codes"]


def test_edge_scoring_cases_from_fixture() -> None:
    cases = _cases()
    for case in cases["edge_cases"]:
        result = scoring.score_edge_quality(**case["input"])
        _assert_case(result, case["expect"])


def test_router_scoring_cases_from_fixture() -> None:
    cases = _cases()
    for case in cases["router_cases"]:
        result = scoring.score_router_health(**case["input"])
        _assert_case(result, case["expect"])


def test_end_device_scoring_cases_from_fixture() -> None:
    cases = _cases()
    for case in cases["end_device_cases"]:
        result = scoring.score_end_device_health(**case["input"])
        _assert_case(result, case["expect"])


def test_network_scoring_cases_from_fixture() -> None:
    cases = _cases()
    for case in cases["network_cases"]:
        result = scoring.score_network_health(**case["input"])
        _assert_case(result, case["expect"])


def test_placement_opportunity_case_from_fixture() -> None:
    case = _cases()["placement_case"]
    score = scoring.score_placement_opportunity(**case["input"])
    assert score >= case["expect"]["score_min"]
    assert score <= case["expect"]["score_max"]