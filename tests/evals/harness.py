"""In-process harness for foxhole-mcp evaluation using Agno and DSPy."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, cast

from agno.agent import Agent
from agno.models.google import Gemini
from agno.tools.mcp import MCPTools
from deepeval.test_case import MCPServer, ToolCall
from mcp.client import Client

from foxhole.server import server

# Ensure Google GenAI / Gemini credentials are mapped
if "GOOGLE_API_KEY" not in os.environ and "GEMINI_API_KEY" in os.environ:
    os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]

if "FOXHOLE_CARGO_DIR" not in os.environ:
    os.environ["FOXHOLE_CARGO_DIR"] = str(Path.home() / ".cache" / "foxhole" / "cargo")

AGENT_MODEL_NAME = os.environ.get("AGENT_MODEL_NAME", "gemini-3.1-flash-lite-preview")


def compact_tool_dict(tool: Any) -> dict[str, Any]:
    """Compact tool summary for MCPUseMetric to stay well within TPM token limits."""
    name = getattr(tool, "name", "")
    description = (getattr(tool, "description", "") or "").strip().split("\n")[0]
    schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None) or {}
    if isinstance(schema, dict):
        parameters = list(schema.get("properties", {}).keys())
    elif hasattr(schema, "properties") and isinstance(schema.properties, dict):
        parameters = list(schema.properties.keys())
    else:
        parameters = []
    return {
        "name": name,
        "description": description,
        "parameters": parameters,
    }


async def run_agno_agent(
    prompt: str,
    model_name: str = AGENT_MODEL_NAME,
) -> tuple[str, list[ToolCall], MCPServer]:
    """Run an in-process Agno agent with Foxhole MCP tools."""
    async with Client(server) as client:
        # Avoid calling session.initialize() because mcp.client.Client has already done handshake
        mcp_tools = MCPTools(session=client.session, exclude_tools=["edit_wiki_page"])
        await mcp_tools.build_tools()
        mcp_tools._initialized = True

        tools_resp = await client.list_tools()
        active_tools = [t for t in tools_resp.tools if t.name != "edit_wiki_page"]
        compact_tools = [compact_tool_dict(t) for t in active_tools]
        mcp_server = MCPServer(server_name="foxhole", available_tools=compact_tools)

        agent = Agent(
            model=Gemini(id=model_name, temperature=0.0),
            tools=[mcp_tools],
            instructions=(
                "You are an expert assistant for the game Foxhole. "
                "Use the provided tools to retrieve accurate game data, statistics, and calculations. "
                "You must query tools before answering questions about gameplay mechanics, river navigation, structures, drawbridges, vehicles, combat, or recipes; never guess from internal memory. "
                "For Bill of Materials (BOM), raw resource calculations (Salvage, Components, Sulfur, Coal), and squad kit requirements, "
                "call calculate_required_resources(demand={...}) directly as your very first step in a single call, passing all requested items and their listed quantities as a single dictionary (e.g. demand={'Niska Mk. I': 1, '12.7mm': 40, ...}). Do NOT query get_production_cost or get_item_stats for individual items first, and do not multiply ammo counts into individual bullet rounds. "
                "When asked how many resources are needed to produce N items 'in whole crates' (e.g. 15 Soldier Supplies in whole crates), "
                "call calculate_required_resources(demand={'item_name': N}, round_to_crates=True) directly with the item count N (not N crates). "
                "For Mass Production Factory orders and queue discounts, call calculate_mpf_cost directly. "
                "For hauling truck trips, call calculate_hauling_trips directly. "
                "For vehicle, weapon, ammunition, or structure stats (specifications, HP, armor, inventory slots), use get_vehicle_stats, get_item_stats, or get_structure_stats. "
                "For structure queries asking about a specific tier (e.g. 'Tier 2 Observation Bunker', 'Tier 2 Bunker'), "
                "query the structure name with its tier or inspect the 'tier_stats' field. "
                "For bridge or drawbridge river navigation questions, query 'Drawbridge' with get_structure_stats or get_page_overview to inspect operational notes and interaction rules. "
                "For logistics storage facilities (Seaports and Storage Depots), understand that they are wholesale logistics structures that only store and dispense packaged/crated inventory; they do not have uncrating functionality or 'Retrieve as Item' options in their UI menus. Uncrating items requires retrieving the crates, transporting them, and submitting them to a Base stockpile (Town Hall, Relic Base, or Bunker Base), where items unpack into individual units for retrieval. "
                "For geography, hex layouts, and waterway hydrology/topology between regions (such as Nevish Line -> Callum's Cape -> Speaking Woods), note that the river corridor meanders south from Callum's Cape and enters Speaking Woods through its southern boundary (not a naive western border crossing). Along this water route, positions like Stem are encountered before deeper eastern/northeastern hubs like Tine. Never recommend destinations that lie past the target destination as intermediate stopping points or waypoints, and recognize that macro map hex positions do not match actual river channel entry directions."
                "Distinguish carefully between basic structures and base variants (for example, a 'Bunker' vs a 'Bunker Base' are different structures with different HP). "
                "Always report the stat of the exact structure requested directly and concisely without guessing. "
                "Never hallucinate game mechanics or non-existent tools."
            ),
            markdown=True,
        )

        resp = None
        for attempt in range(7):
            try:
                resp = await agent.arun(prompt)
                content = str(resp.content) if resp and resp.content else ""
                if (
                    "ClientResponse" in content
                    or "Too Many Requests" in content
                    or "503" in content
                    or "Service Unavailable" in content
                    or "RESOURCE_EXHAUSTED" in content
                ):
                    raise RuntimeError(f"Agno agent returned transient API error: {content}")
                break
            except Exception as exc:
                err_str = str(exc)
                is_transient = (
                    "429" in err_str
                    or "503" in err_str
                    or "RESOURCE_EXHAUSTED" in err_str
                    or "UNAVAILABLE" in err_str
                    or "quota" in err_str.lower()
                    or "transient API error" in err_str
                )
                if is_transient and attempt < 6:
                    import re

                    m_retry = re.search(r"retry\s+in\s+([0-9\.]+)\s*s", err_str, re.I)
                    if not m_retry:
                        m_retry = re.search(r"'retryDelay':\s*'([0-9]+)s'", err_str)
                    if m_retry:
                        wait_time = float(m_retry.group(1)) + 1.0
                    else:
                        wait_time = (
                            60.0 + (10.0 * attempt)
                            if (
                                "429" in err_str
                                or "quota" in err_str.lower()
                                or "RESOURCE_EXHAUSTED" in err_str
                                or "Too Many Requests" in err_str
                            )
                            else (4.0 * (attempt + 1))
                        )
                    await asyncio.sleep(wait_time)
                    continue
                raise

        actual_output = str(resp.content) if resp and resp.content else ""

        tools_called: list[ToolCall] = []
        for t in (resp.tools if resp else None) or []:
            output_str = str(t.result) if t.result is not None else ""
            tools_called.append(
                ToolCall(
                    name=t.tool_name or "",
                    input_parameters=t.tool_args or {},
                    output=output_str,
                )
            )

        return actual_output, tools_called, mcp_server


def run_agno_agent_sync(
    prompt: str,
    model_name: str = AGENT_MODEL_NAME,
) -> tuple[str, list[ToolCall], MCPServer]:
    """Synchronous wrapper for run_agno_agent."""
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(run_agno_agent(prompt, model_name))
    else:
        # If already running in an event loop
        import nest_asyncio

        nest_asyncio.apply()
        return loop.run_until_complete(run_agno_agent(prompt, model_name))


async def run_dspy_agent(
    prompt: str,
    model_name: str = AGENT_MODEL_NAME,
) -> tuple[str, list[ToolCall], MCPServer]:
    """Run an in-process DSPy ReAct agent with Foxhole MCP tools."""
    import dspy

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    dspy_model = model_name if model_name.startswith("gemini/") else f"gemini/{model_name}"
    lm = dspy.LM(dspy_model, api_key=api_key)
    dspy.configure(lm=lm)

    async with Client(server) as client:
        tools_resp = await client.list_tools()
        active_tools = [t for t in tools_resp.tools if t.name != "edit_wiki_page"]
        compact_tools = [compact_tool_dict(t) for t in active_tools]
        mcp_server = MCPServer(server_name="foxhole", available_tools=compact_tools)

        dspy_tools = [dspy.Tool.from_mcp_tool(client.session, t) for t in active_tools]

        react = dspy.ReAct(
            cast(Any, "question -> answer"),
            tools=cast(Any, dspy_tools),
            max_iters=6,
        )

        pred = None
        for attempt in range(5):
            try:
                pred = await react.acall(question=prompt)
                break
            except Exception as exc:
                err_str = str(exc)
                is_transient = (
                    "429" in err_str
                    or "503" in err_str
                    or "RESOURCE_EXHAUSTED" in err_str
                    or "UNAVAILABLE" in err_str
                    or "quota" in err_str.lower()
                )
                if is_transient and attempt < 4:
                    import re

                    m_retry = re.search(r"retry\s+in\s+([0-9\.]+)\s*s", err_str, re.I)
                    if not m_retry:
                        m_retry = re.search(r"'retryDelay':\s*'([0-9]+)s'", err_str)
                    if m_retry:
                        wait_time = float(m_retry.group(1)) + 1.0
                    else:
                        wait_time = (
                            20.0 * (attempt + 1)
                            if (
                                "429" in err_str
                                or "quota" in err_str.lower()
                                or "RESOURCE_EXHAUSTED" in err_str
                            )
                            else (4.0 * (attempt + 1))
                        )
                    await asyncio.sleep(wait_time)
                    continue
                raise

        actual_output = str(pred.answer) if hasattr(pred, "answer") and pred.answer else ""

        traj = pred.get("trajectory", {}) if hasattr(pred, "get") else {}
        tools_called: list[ToolCall] = []
        idx = 0
        while f"tool_name_{idx}" in traj:
            t_name = traj[f"tool_name_{idx}"]
            t_args = traj.get(f"tool_args_{idx}", {})
            t_obs = traj.get(f"observation_{idx}", "")
            if t_name != "finish":
                tools_called.append(
                    ToolCall(
                        name=t_name,
                        input_parameters=t_args if isinstance(t_args, dict) else {},
                        output=str(t_obs),
                    )
                )
            idx += 1

        return actual_output, tools_called, mcp_server


def run_dspy_agent_sync(
    prompt: str,
    model_name: str = AGENT_MODEL_NAME,
) -> tuple[str, list[ToolCall], MCPServer]:
    """Synchronous wrapper for run_dspy_agent."""
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(run_dspy_agent(prompt, model_name))
    else:
        import nest_asyncio

        nest_asyncio.apply()
        return loop.run_until_complete(run_dspy_agent(prompt, model_name))


def run_agent_sync(
    prompt: str,
    harness: str | None = None,
    model_name: str = AGENT_MODEL_NAME,
) -> tuple[str, list[ToolCall], MCPServer]:
    """Unified runner choosing between Agno and DSPy harnesses."""
    selected = harness or os.environ.get("EVAL_HARNESS", "agno").lower()
    if selected == "dspy":
        return run_dspy_agent_sync(prompt, model_name)
    return run_agno_agent_sync(prompt, model_name)
