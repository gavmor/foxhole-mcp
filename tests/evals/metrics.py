"""DeepEval metrics for foxhole-mcp evaluations."""

import asyncio
import os
import time
from typing import Any

from deepeval.metrics import BaseMetric, GEval, MCPUseMetric
from deepeval.models import GeminiModel
from deepeval.test_case import SingleTurnParams

# Ensure Google GenAI / Gemini credentials are mapped
if "GOOGLE_API_KEY" not in os.environ and "GEMINI_API_KEY" in os.environ:
    os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]

JUDGE_MODEL_NAME = os.environ.get("JUDGE_MODEL_NAME", "gemini-3.1-flash-lite-preview")


class ResilientGeminiModel(GeminiModel):
    """GeminiModel with automatic retry on 429 rate limit and 503 unavailable spikes."""

    async def a_generate(self, *args: Any, **kwargs: Any) -> Any:
        import re

        for attempt in range(7):
            try:
                return await super().a_generate(*args, **kwargs)
            except Exception as exc:
                err_str = str(exc)
                if (
                    "503" in err_str
                    or "429" in err_str
                    or "UNAVAILABLE" in err_str
                    or "RESOURCE_EXHAUSTED" in err_str
                    or "quota" in err_str.lower()
                ) and attempt < 6:
                    m_retry = re.search(r"retry\s+in\s+([0-9\.]+)\s*s", err_str, re.I)
                    if not m_retry:
                        m_retry = re.search(r"'retryDelay':\s*'([0-9]+)s'", err_str)
                    if m_retry:
                        sleep_s = float(m_retry.group(1)) + 1.0
                    else:
                        sleep_s = (
                            60.0
                            if (
                                "429" in err_str
                                or "quota" in err_str.lower()
                                or "RESOURCE_EXHAUSTED" in err_str
                            )
                            else (4.0 * (attempt + 1))
                        )
                    await asyncio.sleep(sleep_s)
                    continue
                raise
        raise RuntimeError("a_generate retries exhausted")

    def generate(self, *args: Any, **kwargs: Any) -> Any:
        import re

        for attempt in range(7):
            try:
                return super().generate(*args, **kwargs)
            except Exception as exc:
                err_str = str(exc)
                if (
                    "503" in err_str
                    or "429" in err_str
                    or "UNAVAILABLE" in err_str
                    or "RESOURCE_EXHAUSTED" in err_str
                    or "quota" in err_str.lower()
                ) and attempt < 6:
                    m_retry = re.search(r"retry\s+in\s+([0-9\.]+)\s*s", err_str, re.I)
                    if not m_retry:
                        m_retry = re.search(r"'retryDelay':\s*'([0-9]+)s'", err_str)
                    if m_retry:
                        sleep_s = float(m_retry.group(1)) + 1.0
                    else:
                        sleep_s = (
                            60.0
                            if (
                                "429" in err_str
                                or "quota" in err_str.lower()
                                or "RESOURCE_EXHAUSTED" in err_str
                            )
                            else (4.0 * (attempt + 1))
                        )
                    time.sleep(sleep_s)
                    continue
                raise
        raise RuntimeError("generate retries exhausted")


def get_judge_model() -> GeminiModel:
    return ResilientGeminiModel(model=JUDGE_MODEL_NAME)


# Factual correctness evaluation comparing actual output to verified ground truth
factual_correctness_metric = GEval(
    name="Factual Correctness",
    criteria=(
        "Determine if the actual output factually satisfies the user's question, "
        "matching the verified facts and values specified in expected_output. "
        "For game calculations, verify numbers match expected values. "
        "For mechanics and rules, penalize hallucinations of non-existent requirements "
        "(such as requiring tools or disembarking when not needed)."
    ),
    evaluation_params=[
        SingleTurnParams.INPUT,
        SingleTurnParams.ACTUAL_OUTPUT,
        SingleTurnParams.EXPECTED_OUTPUT,
    ],
    model=get_judge_model(),
    async_mode=False,
)

# MCP Use evaluation scoring tool selection, arguments, and efficiency
mcp_use_metric = MCPUseMetric(
    model=get_judge_model(),
    threshold=0.5,
    async_mode=False,
)

EVAL_METRICS: list[BaseMetric] = [
    factual_correctness_metric,
    mcp_use_metric,
]
