"""Traced DSPy ReAct agent runner with OpenTelemetry and DeepEval session logging."""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import uuid
from pathlib import Path
from typing import Any

from foxhole.telemetry import get_tracer, setup_telemetry
from tests.evals.harness import AGENT_MODEL_NAME, run_dspy_agent

tracer = get_tracer("foxhole.agent.dspy")


def get_default_session_dir() -> Path:
    base = Path(__file__).resolve().parent.parent.parent / "tests" / "evals" / "sessions"
    base.mkdir(parents=True, exist_ok=True)
    return base


def log_test_case_to_session(
    prompt: str,
    actual_output: str,
    tools_called: list[Any],
    session_file: Path,
    session_id: str,
) -> None:
    """Log the agent interaction as a DeepEval-compatible LLMTestCase."""
    try:
        from deepeval.test_case import LLMTestCase, ToolCall

        tool_calls = [
            ToolCall(
                name=t.name,
                input_parameters=t.input_parameters if isinstance(t.input_parameters, dict) else {},
                output=str(t.output),
            )
            for t in tools_called
        ]
        test_case = LLMTestCase(
            input=prompt,
            actual_output=actual_output,
            tools_called=tool_calls,
            additional_metadata={
                "session_id": session_id,
                "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
                "agent": "dspy_react",
            },
        )
        with open(session_file, "a", encoding="utf-8") as f:
            f.write(test_case.model_dump_json() + "\n")
    except Exception as e:
        # Fallback to plain JSON if deepeval is not installed or import fails
        entry = {
            "input": prompt,
            "actual_output": actual_output,
            "tools_called": [
                {"name": t.name, "input_parameters": t.input_parameters, "output": str(t.output)}
                for t in tools_called
            ],
            "additional_metadata": {
                "session_id": session_id,
                "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
                "agent": "dspy_react",
                "error": str(e),
            },
        }
        with open(session_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


async def run_traced_turn(
    prompt: str,
    session_id: str,
    session_file: Path,
    model_name: str = AGENT_MODEL_NAME,
) -> tuple[str, list[Any]]:
    """Execute a single DSPy ReAct turn wrapped in OpenTelemetry spans."""
    from opentelemetry import trace

    # Ensure TracerProvider is initialized with active telemetry configuration
    setup_telemetry()

    with tracer.start_as_current_span(
        "agent.dspy.turn",
        kind=trace.SpanKind.INTERNAL,
    ) as span:
        span.set_attribute("gen_ai.system", "dspy")
        span.set_attribute("gen_ai.agent.name", "foxhole-coordinator")
        span.set_attribute("gen_ai.session.id", session_id)
        span.set_attribute("gen_ai.prompt", prompt)

        # Run DSPy ReAct agent
        actual_output, tools_called, _ = await run_dspy_agent(prompt, model_name=model_name)

        # Record generation span attributes
        span.set_attribute("gen_ai.completion", actual_output)
        span.set_attribute("gen_ai.tool_calls.count", len(tools_called))

        # Record child tool spans
        for tc in tools_called:
            with tracer.start_as_current_span(
                f"tool_call.{tc.name}",
                kind=trace.SpanKind.CLIENT,
            ) as tool_span:
                tool_span.set_attribute("gen_ai.tool.name", tc.name)
                tool_span.set_attribute("gen_ai.tool.parameters", json.dumps(tc.input_parameters))
                tool_span.set_attribute("gen_ai.tool.output", str(tc.output)[:2000])

        # Durably record for DeepEval ingestion
        log_test_case_to_session(prompt, actual_output, tools_called, session_file, session_id)

        return actual_output, tools_called


async def run_agent_cli(
    prompt: str | None = None,
    interactive: bool = False,
    model_name: str = AGENT_MODEL_NAME,
    session_id: str | None = None,
) -> None:
    """Run the DSPy agent either one-shot or as an interactive REPL."""
    session_id = session_id or uuid.uuid4().hex[:12]
    session_dir = get_default_session_dir()
    session_file = session_dir / f"sortie_{session_id}.jsonl"

    if prompt and not interactive:
        # One-shot mode
        print(f"\033[1;34m[Foxhole Sortie {session_id}]\033[0m Tactical query: {prompt}")
        output, tools = await run_traced_turn(prompt, session_id, session_file, model_name)
        if tools:
            print(f"\033[2mTools invoked: {', '.join(t.name for t in tools)}\033[0m")
        print("\n" + output)
        print(f"\n\033[2mTurn captured for DeepEval: {session_file}\033[0m")
        return

    def print_banner(s_id: str, s_file: Path) -> None:
        print("\033[1;32m═══════════════════════════════════════════════════════════════\033[0m")
        print("\033[1;32m⚔️  Foxhole Tactical Coordinator (DSPy ReAct + OpenTelemetry)\033[0m")
        print(f"\033[2mSession ID : {s_id}\033[0m")
        print(f"\033[2mEval Log   : {s_file}\033[0m")
        print(f"\033[2mModel      : {model_name}\033[0m")
        print("\033[2mCommands   : /new [name], /status, /clear, or 'exit'/'quit'\033[0m")
        print("\033[1;32m═══════════════════════════════════════════════════════════════\033[0m\n")

    # Interactive REPL mode (ideal for aoe tmux panes)
    print_banner(session_id, session_file)

    # If an initial prompt was passed, run it first
    if prompt:
        print(f"\033[1;34m>>> {prompt}\033[0m")
        output, tools = await run_traced_turn(prompt, session_id, session_file, model_name)
        if tools:
            print(f"\033[2m🔧 Tools invoked: {', '.join(t.name for t in tools)}\033[0m")
        print("\n" + output + "\n")

    loop = asyncio.get_running_loop()
    while True:
        try:
            user_input = await loop.run_in_executor(None, input, "\033[1;36mfoxhole>\033[0m ")
        except (EOFError, KeyboardInterrupt):
            print("\n\033[2mEnding sortie session. Good luck on the front, soldier!\033[0m")
            break

        query = user_input.strip()
        if not query:
            continue
        if query.lower() in ("exit", "quit", ":q", "q"):
            print("\033[2mEnding sortie session. Traces & evals preserved.\033[0m")
            break

        # Built-in slash commands
        if query.startswith(("/new", "/reset")):
            parts = query.split(maxsplit=1)
            new_id = (
                parts[1].strip() if len(parts) > 1 and parts[1].strip() else uuid.uuid4().hex[:12]
            )
            session_id = new_id
            session_file = session_dir / f"sortie_{session_id}.jsonl"
            print(
                "\n\033[1;32m═══════════════════════════════════════════════════════════════\033[0m"
            )
            print("\033[1;32m🔄 Started new sortie session\033[0m")
            print(f"\033[2mSession ID : {session_id}\033[0m")
            print(f"\033[2mEval Log   : {session_file}\033[0m")
            print(
                "\033[1;32m═══════════════════════════════════════════════════════════════\033[0m\n"
            )
            continue

        if query == "/status":
            print("\n\033[1;34m[Session Status]\033[0m")
            print(f"  Session ID : {session_id}")
            print(f"  Eval Log   : {session_file}")
            print(f"  Model      : {model_name}")
            print("  Tracer     : OpenTelemetry (dspy)\n")
            continue

        if query == "/clear":
            print("\033[2J\033[H", end="")
            print_banner(session_id, session_file)
            continue

        if query in ("/help", "/?"):
            print("\n\033[1;34m[Available Commands]\033[0m")
            print("  /new [id]   - Start a fresh sortie session with a new ID and eval log")
            print("  /status     - Display current session configuration and log paths")
            print("  /clear      - Clear terminal screen and reprint session banner")
            print("  exit / quit - End current sortie session\n")
            continue

        print("\033[2mAnalyzing battlefield intelligence...\033[0m")
        try:
            output, tools = await run_traced_turn(query, session_id, session_file, model_name)
            if tools:
                print(f"\033[2m🔧 Tools invoked: {', '.join(t.name for t in tools)}\033[0m")
            print("\n" + output + "\n")
        except Exception as exc:
            print(f"\033[1;31mError during execution: {exc}\033[0m\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the traced DSPy ReAct agent for Foxhole with OpenTelemetry and DeepEval capture."
    )
    parser.add_argument("prompt", nargs="?", default=None, help="Tactical query to ask the agent")
    parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="Run in interactive REPL mode (default if no prompt provided)",
    )
    parser.add_argument(
        "-m",
        "--model",
        default=AGENT_MODEL_NAME,
        help=f"Model name for the DSPy agent (default: {AGENT_MODEL_NAME})",
    )
    parser.add_argument(
        "-s",
        "--session-id",
        default=None,
        help="Custom session identifier for tracking traces and evaluation logs",
    )
    args = parser.parse_args()

    interactive_mode = args.interactive or (args.prompt is None)
    asyncio.run(
        run_agent_cli(
            prompt=args.prompt,
            interactive=interactive_mode,
            model_name=args.model,
            session_id=args.session_id,
        )
    )


if __name__ == "__main__":
    main()
