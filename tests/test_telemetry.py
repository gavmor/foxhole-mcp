"""Comprehensive tests for OpenTelemetry instrumentation and tracing in Foxhole MCP server."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import anyio
import pytest
from mcp.client.session import ClientSession
from mcp.server.mcpserver import MCPServer
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.trace import StatusCode

from foxhole.server import create_server
from foxhole.telemetry import (
    InMemorySpanExporter,
    instrument_httpx_client,
    reset_telemetry,
    setup_telemetry,
    suppress_fastmcp_telemetry,
    uninstrument_httpx_client,
)
from foxhole.tools.warapi import WarApiTools
from foxhole.tools.wiki import WikiTools


@pytest.fixture(autouse=True)
def clean_telemetry():
    """Ensure OpenTelemetry state is clean before and after each test."""
    reset_telemetry()
    yield
    reset_telemetry()


@asynccontextmanager
async def run_in_memory_session(server: MCPServer) -> AsyncGenerator[ClientSession, None]:
    """Helper to run an MCPServer and connected ClientSession using in-memory streams."""
    client_send, server_receive = anyio.create_memory_object_stream(50)
    server_send, client_receive = anyio.create_memory_object_stream(50)

    async with anyio.create_task_group() as tg:
        tg.start_soon(
            server._lowlevel_server.run,
            server_receive,
            server_send,
            server._lowlevel_server.create_initialization_options(),
        )
        async with ClientSession(client_receive, client_send) as session:
            await session.initialize()
            yield session
        await client_send.aclose()
        await server_send.aclose()


# ---------------------------------------------------------------------------
# Setup & Configuration Tests
# ---------------------------------------------------------------------------


def test_setup_telemetry_defaults_and_resource():
    """Verify default setup_telemetry creates TracerProvider with service.name='foxhole'."""
    exporter = InMemorySpanExporter()
    provider = setup_telemetry(exporter=exporter)
    assert isinstance(provider, TracerProvider)
    assert provider.resource.attributes.get("service.name") == "foxhole"


def test_setup_telemetry_custom_service_name_and_attributes():
    """Verify custom service_name and resource_attributes are correctly set."""
    exporter = InMemorySpanExporter()
    provider = setup_telemetry(
        service_name="custom-foxhole",
        exporter=exporter,
        resource_attributes={"deployment.environment": "testing", "custom.attr": 42},
    )
    assert isinstance(provider, TracerProvider)
    assert provider.resource.attributes.get("service.name") == "custom-foxhole"
    assert provider.resource.attributes.get("deployment.environment") == "testing"
    assert provider.resource.attributes.get("custom.attr") == 42


def test_setup_telemetry_respects_otel_service_name(monkeypatch):
    """Verify OTEL_SERVICE_NAME environment variable is respected."""
    monkeypatch.setenv("OTEL_SERVICE_NAME", "env-foxhole-service")
    exporter = InMemorySpanExporter()
    provider = setup_telemetry(exporter=exporter)
    assert isinstance(provider, TracerProvider)
    assert provider.resource.attributes.get("service.name") == "env-foxhole-service"


def test_setup_telemetry_exporters_and_processors():
    """Verify various exporter string shortcuts and span processors."""
    # Memory string
    p_mem = setup_telemetry(exporter="memory", force=True)
    assert isinstance(p_mem, TracerProvider)

    # Console string
    p_cons = setup_telemetry(exporter="console", force=True)
    assert isinstance(p_cons, TracerProvider)

    # OTLP string
    p_otlp = setup_telemetry(exporter="otlp", force=True)
    assert isinstance(p_otlp, TracerProvider)

    # None string
    p_none = setup_telemetry(exporter="none", force=True)
    assert isinstance(p_none, TracerProvider)

    # Direct processor instance
    custom_exporter = InMemorySpanExporter()
    custom_proc = SimpleSpanProcessor(custom_exporter)
    p_proc = setup_telemetry(span_processor=custom_proc, force=True)
    assert isinstance(p_proc, TracerProvider)

    # Invalid string raises ValueError
    with pytest.raises(ValueError, match="Unknown exporter: invalid"):
        setup_telemetry(exporter="invalid", force=True)


def test_setup_telemetry_respects_otel_traces_exporter_env(monkeypatch):
    """Verify OTEL_TRACES_EXPORTER environment variable."""
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "console")
    p = setup_telemetry(force=True)
    assert isinstance(p, TracerProvider)

    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "none")
    p2 = setup_telemetry(force=True)
    assert isinstance(p2, TracerProvider)


def test_setup_telemetry_respects_otlp_endpoint_env(monkeypatch):
    """Verify OTEL_EXPORTER_OTLP_ENDPOINT defaults exporter to OTLP."""
    monkeypatch.delenv("OTEL_TRACES_EXPORTER", raising=False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    p = setup_telemetry(force=True)
    assert isinstance(p, TracerProvider)


def test_setup_telemetry_disabled_by_env(monkeypatch):
    """Verify OTEL_SDK_DISABLED disables telemetry configuration."""
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    p = setup_telemetry()
    assert p is None


def test_setup_telemetry_disabled_by_mode(monkeypatch):
    """Verify FASTMCP_TELEMETRY_MODE=off disables telemetry configuration."""
    monkeypatch.setenv("FASTMCP_TELEMETRY_MODE", "off")
    p = setup_telemetry()
    assert p is None

    p2 = setup_telemetry(telemetry_mode="off")
    assert p2 is None


def test_httpx_instrumentation_lifecycle():
    """Verify instrument_httpx_client and uninstrument_httpx_client are idempotent."""
    instrument_httpx_client()
    instrument_httpx_client()  # No-op second call
    uninstrument_httpx_client()
    uninstrument_httpx_client()  # No-op second call


# ---------------------------------------------------------------------------
# Tool Telemetry Spans
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wiki_tool_spans():
    """Verify OpenTelemetry spans and MCP semantic conventions for MediaWiki tools."""
    exporter = InMemorySpanExporter()
    setup_telemetry(exporter=exporter)

    mock_wiki_client = AsyncMock()
    mock_wiki_client.search.return_value = []
    mock_wiki_client.get_page_data.return_value = {
        "title": "Ahti Convalescent",
        "wikitext": "{{Item Infobox | name = Ahti | ammo = None }}",
    }
    mock_wiki_client.opensearch.return_value = []
    wiki_tools = WikiTools(client=mock_wiki_client)

    server = create_server(wiki_tools=wiki_tools)

    async with run_in_memory_session(server) as session:
        await session.call_tool("search_foxhole_wiki", {"query": "Ambulance"})
        await session.call_tool("get_item_stats", {"name": "Ahti Convalescent"})

    spans = exporter.get_finished_spans()
    search_spans = [s for s in spans if s.name == "tools/call search_foxhole_wiki"]
    assert len(search_spans) == 1
    s = search_spans[0]
    assert s.attributes is not None
    assert s.attributes.get("mcp.method.name") == "tools/call"
    assert s.attributes.get("gen_ai.operation.name") == "execute_tool"
    assert s.attributes.get("gen_ai.tool.name") == "search_foxhole_wiki"
    assert "jsonrpc.request.id" in s.attributes
    assert s.status.status_code == StatusCode.UNSET

    item_spans = [s for s in spans if s.name == "tools/call get_item_stats"]
    assert len(item_spans) == 1
    assert item_spans[0].attributes is not None
    assert item_spans[0].attributes.get("gen_ai.tool.name") == "get_item_stats"


@pytest.mark.asyncio
async def test_warapi_tool_spans():
    """Verify OpenTelemetry spans and MCP semantic conventions for War API tools."""
    exporter = InMemorySpanExporter()
    setup_telemetry(exporter=exporter)

    mock_war_client = AsyncMock()
    mock_war_client.get_maps.return_value = ["DeadlandsHex", "HeartlandsHex"]
    war_tools = WarApiTools(war_client=mock_war_client)

    server = create_server(war_tools=war_tools)

    async with run_in_memory_session(server) as session:
        await session.call_tool("get_active_maps", {})

    spans = exporter.get_finished_spans()
    map_spans = [s for s in spans if s.name == "tools/call get_active_maps"]
    assert len(map_spans) == 1
    s = map_spans[0]
    assert s.attributes is not None
    assert s.attributes.get("mcp.method.name") == "tools/call"
    assert s.attributes.get("gen_ai.operation.name") == "execute_tool"
    assert s.attributes.get("gen_ai.tool.name") == "get_active_maps"


@pytest.mark.asyncio
async def test_production_tool_spans_and_child_spans():
    """Verify OpenTelemetry spans and custom child spans for production & BOM tools."""
    exporter = InMemorySpanExporter()
    setup_telemetry(exporter=exporter)

    server = create_server()

    async with run_in_memory_session(server) as session:
        await session.call_tool(
            "calculate_required_resources",
            {"demand": {"Basic Materials": 50.0}, "include_machine_counts": True},
        )

    spans = exporter.get_finished_spans()
    server_spans = [s for s in spans if s.name == "tools/call calculate_required_resources"]
    assert len(server_spans) == 1

    child_spans = [s for s in spans if s.name == "calculate_required_resources.solve"]
    assert len(child_spans) == 1
    cs = child_spans[0]
    assert cs.attributes is not None
    assert cs.attributes.get("foxhole.include_machines") is True
    demand_keys = cs.attributes.get("foxhole.demand_keys")
    assert isinstance(demand_keys, (list, tuple))
    assert "Basic Materials" in demand_keys
    # Child span must be parented by the server span
    assert cs.parent is not None
    assert cs.parent.span_id == server_spans[0].context.span_id


@pytest.mark.asyncio
async def test_plan_production_spans():
    """Verify child span emitted during plan_production tool execution."""
    exporter = InMemorySpanExporter()
    setup_telemetry(exporter=exporter)

    server = create_server()

    with patch(
        "foxhole.server._fetch_recipes",
        AsyncMock(return_value=("Basic Materials", [])),
    ):
        async with run_in_memory_session(server) as session:
            await session.call_tool(
                "plan_production", {"target": "Basic Materials", "quantity": 10}
            )

    spans = exporter.get_finished_spans()
    server_spans = [s for s in spans if s.name == "tools/call plan_production"]
    assert len(server_spans) == 1

    solve_spans = [s for s in spans if s.name == "plan_production.solve"]
    assert len(solve_spans) == 1
    assert solve_spans[0].attributes is not None
    assert solve_spans[0].attributes.get("foxhole.target") == "Basic Materials"
    assert solve_spans[0].attributes.get("foxhole.quantity") == 10.0


# ---------------------------------------------------------------------------
# Prompt Telemetry Spans
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_spans():
    """Verify OpenTelemetry spans and MCP semantic conventions for prompts/get."""
    exporter = InMemorySpanExporter()
    setup_telemetry(exporter=exporter)

    server = create_server()

    async with run_in_memory_session(server) as session:
        await session.get_prompt("combat_intel", {"vehicle_or_weapon": "Silverhand"})
        await session.get_prompt(
            "logistics_plan", {"item_name": "Soldier Supplies", "requested_amount": "50"}
        )
        await session.get_prompt("bill_of_materials", {"item_or_vehicle": "Falchion"})
        await session.get_prompt("production_planner", {"target": "Dunne Transport"})

    spans = exporter.get_finished_spans()

    combat_spans = [s for s in spans if s.name == "prompts/get combat_intel"]
    assert len(combat_spans) == 1
    assert combat_spans[0].attributes is not None
    assert combat_spans[0].attributes.get("mcp.method.name") == "prompts/get"
    assert combat_spans[0].attributes.get("gen_ai.prompt.name") == "combat_intel"

    logistics_spans = [s for s in spans if s.name == "prompts/get logistics_plan"]
    assert len(logistics_spans) == 1
    assert logistics_spans[0].attributes is not None
    assert logistics_spans[0].attributes.get("gen_ai.prompt.name") == "logistics_plan"

    bom_spans = [s for s in spans if s.name == "prompts/get bill_of_materials"]
    assert len(bom_spans) == 1
    assert bom_spans[0].attributes is not None
    assert bom_spans[0].attributes.get("gen_ai.prompt.name") == "bill_of_materials"

    plan_spans = [s for s in spans if s.name == "prompts/get production_planner"]
    assert len(plan_spans) == 1
    assert plan_spans[0].attributes is not None
    assert plan_spans[0].attributes.get("gen_ai.prompt.name") == "production_planner"


# ---------------------------------------------------------------------------
# Telemetry Modes & Interoperability
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_telemetry_mode_off():
    """Verify that FASTMCP_TELEMETRY_MODE=off disables server span emission."""
    exporter = InMemorySpanExporter()
    setup_telemetry(exporter=exporter)

    server = create_server(telemetry_mode="off")

    async with run_in_memory_session(server) as session:
        await session.call_tool("calculate_required_resources", {"demand": {"Basic Materials": 10}})

    spans = exporter.get_finished_spans()
    server_spans = [s for s in spans if s.name.startswith("tools/call")]
    assert len(server_spans) == 0


@pytest.mark.asyncio
async def test_telemetry_mode_propagation_only():
    """Verify that FASTMCP_TELEMETRY_MODE=propagation_only omits server spans but runs child spans."""
    exporter = InMemorySpanExporter()
    setup_telemetry(exporter=exporter)

    server = create_server(telemetry_mode="propagation_only")

    async with run_in_memory_session(server) as session:
        await session.call_tool("calculate_required_resources", {"demand": {"Basic Materials": 10}})

    spans = exporter.get_finished_spans()
    server_spans = [s for s in spans if s.name.startswith("tools/call")]
    assert len(server_spans) == 0

    # Child custom span is still generated
    child_spans = [s for s in spans if s.name == "calculate_required_resources.solve"]
    assert len(child_spans) == 1


@pytest.mark.asyncio
async def test_suppress_fastmcp_telemetry_context_manager():
    """Verify suppress_fastmcp_telemetry context manager suppresses FastMCP server spans."""
    exporter = InMemorySpanExporter()
    setup_telemetry(exporter=exporter)

    server = create_server()

    with suppress_fastmcp_telemetry():
        async with run_in_memory_session(server) as session:
            await session.call_tool(
                "calculate_required_resources", {"demand": {"Basic Materials": 10}}
            )

    spans = exporter.get_finished_spans()
    server_spans = [s for s in spans if s.name.startswith("tools/call")]
    assert len(server_spans) == 0

    child_spans = [s for s in spans if s.name == "calculate_required_resources.solve"]
    assert len(child_spans) == 1


@pytest.mark.asyncio
async def test_tool_error_span_status():
    """Verify that a tool error or validation failure marks span status as ERROR."""
    exporter = InMemorySpanExporter()
    setup_telemetry(exporter=exporter)

    server = create_server()

    async with run_in_memory_session(server) as session:
        # Invalid arguments for calculate_required_resources
        try:
            await session.call_tool("calculate_required_resources", {"invalid_arg": 123})
        except Exception:
            pass

    spans = exporter.get_finished_spans()
    error_spans = [s for s in spans if s.name == "tools/call calculate_required_resources"]
    assert len(error_spans) == 1
    assert error_spans[0].status.status_code == StatusCode.ERROR
