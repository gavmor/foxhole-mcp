"""Evaluation suite for imported session traces using DeepEval and traced-harness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from deepeval.test_case import LLMTestCase
from traced_harness.eval import (
    evaluate_trace,
    to_deepeval_test_cases,
)


@pytest.fixture
def sample_trace_file(tmp_path: Path) -> Path:
    trace_file = tmp_path / "sample_trace.jsonl"
    turn1 = {
        "input": "Where is the shipyard?",
        "actual_output": "The shipyard is at Gutter.",
        "tools_called": [
            {
                "name": "get_structure_stats",
                "input_parameters": {"structure_name": "Shipyard"},
                "output": '{"hp": 5000, "title": "Shipyard"}',
            }
        ],
        "additional_metadata": {"session_id": "test-1"},
    }
    turn2 = {
        "input": "Rebuild defense around Blackcoat Way",
        "actual_output": "Pillboxes need garrisons.",
        "tools_called": [
            {
                "name": "get_map_intel",
                "input_parameters": {"map_name": "BlackcoatHex"},
                "output": '{"error": "Could not retrieve map telemetry for \'BlackcoatHex\'"}',
            },
            {
                "name": "search_foxhole_wiki",
                "input_parameters": {"query": "AT Pillbox"},
                "output": '{"results": ["AT Pillbox"]}',
            },
        ],
        "additional_metadata": {"session_id": "test-1"},
    }
    with open(trace_file, "w", encoding="utf-8") as f:
        f.write(json.dumps(turn1) + "\n")
        f.write(json.dumps(turn2) + "\n")
    return trace_file


def test_import_exact_trace_to_deepeval(sample_trace_file: Path):
    """Verify exact trace imports directly into DeepEval LLMTestCase objects without re-running."""
    test_cases = to_deepeval_test_cases(sample_trace_file)

    assert len(test_cases) == 2
    for tc in test_cases:
        assert isinstance(tc, LLMTestCase)
        assert tc.input
        assert tc.actual_output
        assert tc.tools_called

    tc2 = test_cases[1]
    assert len(tc2.tools_called) == 2
    assert tc2.tools_called[0].name == "get_map_intel"
    assert tc2.tools_called[0].input_parameters == {"map_name": "BlackcoatHex"}
    assert "error" in tc2.tools_called[0].output


def test_evaluate_trace_peripheral_health(sample_trace_file: Path):
    """Verify traced-harness evaluates peripheral error rates on loaded traces."""
    report = evaluate_trace(sample_trace_file)

    assert report.total_turns == 2
    assert report.total_tool_calls == 3
    assert len(report.failed_tool_calls) == 1

    turn_idx, failed_tool = report.failed_tool_calls[0]
    assert turn_idx == 1
    assert failed_tool.name == "get_map_intel"
    assert failed_tool.input_parameters == {"map_name": "BlackcoatHex"}
    assert "Could not retrieve map telemetry" in (failed_tool.error_message or "")


def test_live_the_56th_trace_evaluation_if_exists():
    """Evaluate actual active session trace from The 56th if present."""
    trace_path = Path(
        "/home/user/Documents/The 56th/sessions/trace_3a56600d-0a6b-46d6-89df-31e0db91e7a5.jsonl"
    )
    if not trace_path.exists():
        pytest.skip("Active session trace not found on disk")

    report = evaluate_trace(trace_path)
    assert report.total_turns == 3
    assert len(report.failed_tool_calls) == 1

    # Ensure the failure is precisely captured as an agentic peripheral parameter error
    _, tool = report.failed_tool_calls[0]
    assert tool.name == "get_map_intel"
    assert tool.input_parameters.get("map_name") == "BlackcoatHex"


def test_fresh_the_56th_trace_58776d7ae841_evaluation_if_exists():
    """Evaluate fresh failure case session trace (58776d7ae841) from The 56th if present."""
    trace_path = Path("/home/user/Documents/The 56th/sessions/trace_58776d7ae841.jsonl")
    if not trace_path.exists():
        trace_path = Path("tests/evals/sessions/trace_58776d7ae841.jsonl")
    if not trace_path.exists():
        pytest.skip("Fresh session trace not found on disk")

    report = evaluate_trace(trace_path)
    assert report.total_turns == 2
    assert len(report.failed_tool_calls) == 1

    # Ensure peripheral failure is captured as sub-location parameter hallucination
    turn_idx, tool = report.failed_tool_calls[0]
    assert turn_idx == 1
    assert tool.name == "get_map_intel"
    assert tool.input_parameters.get("map_name") == "SouredFieldsHex"
    assert "Could not retrieve map telemetry for 'SouredFieldsHex'" in (tool.error_message or "")

    # DeepEval test case conversion
    test_cases = to_deepeval_test_cases(trace_path)
    assert len(test_cases) == 2
    assert "Strider" in test_cases[0].input
    assert "Soured Fields" in test_cases[1].input


def test_first_failure_cascade_pruning(tmp_path: Path):
    """Verify first-failure stopping behavior from 'Evals for AI Engineers' (Ch. 3 & 8)."""
    trace_file = tmp_path / "cascading_trace.jsonl"
    turn0 = {"input": "t0", "actual_output": "out0", "tools_called": []}
    turn1 = {
        "input": "t1",
        "actual_output": "out1",
        "tools_called": [{"name": "tool_fail", "output": '{"error": "fail"}'}],
    }
    turn2 = {
        "input": "t2",
        "actual_output": "out2",
        "tools_called": [{"name": "tool_downstream", "output": '{"error": "polluted"}'}],
    }
    with open(trace_file, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(t) + "\n" for t in (turn0, turn1, turn2))

    # By default, evaluates turn 0 and 1, stopping at turn 1 and pruning turn 2
    report = evaluate_trace(trace_file, stop_at_first_failure=True)
    assert report.total_turns == 3
    assert report.evaluated_turns == 2
    assert report.polluted_turns_count == 1
    assert report.first_failure_turn == 1
    assert len(report.failed_tool_calls) == 1

    # DeepEval test cases stop at turn 1
    test_cases = to_deepeval_test_cases(trace_file, stop_at_first_failure=True)
    assert len(test_cases) == 2
    assert test_cases[0].input == "t0"
    assert test_cases[1].input == "t1"
