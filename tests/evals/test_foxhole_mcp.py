"""End-to-end evaluation suite for foxhole-mcp using DeepEval and in-process agent harness."""

import time

import pytest
from deepeval import assert_test
from deepeval.dataset import EvaluationDataset, Golden
from deepeval.metrics import BaseMetric
from deepeval.test_case import LLMTestCase, ToolCall

from tests.evals.harness import run_agent_sync
from tests.evals.metrics import factual_correctness_metric, mcp_use_metric

# Load committed dataset of goldens
dataset = EvaluationDataset()
dataset.add_goldens_from_json_file(file_path="tests/evals/.dataset.json")


@pytest.mark.parametrize(
    "golden", dataset.goldens, ids=lambda g: getattr(g, "name", None) or g.input[:30]
)
def test_foxhole_mcp_golden(golden: Golden):
    """Run golden prompt against in-process Foxhole MCP server and evaluate with DeepEval metrics."""
    time.sleep(15)
    actual_output, tools_called, mcp_server = run_agent_sync(golden.input)

    expected_tools = (
        [ToolCall(name=t["name"]) if isinstance(t, dict) else t for t in golden.expected_tools]
        if golden.expected_tools
        else None
    )

    test_case = LLMTestCase(
        input=golden.input,
        actual_output=actual_output,
        expected_output=golden.expected_output,
        expected_tools=expected_tools,
        tools_called=tools_called,
        mcp_servers=[mcp_server] if golden.expected_tools else None,
    )

    metrics: list[BaseMetric] = [factual_correctness_metric]
    if golden.expected_tools:
        metrics.append(mcp_use_metric)

    assert_test(test_case=test_case, metrics=metrics)
