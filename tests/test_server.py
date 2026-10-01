"""Unit tests for Foxhole MCP Server tools."""

import pytest

from foxhole.server import (
    get_item_stats,
    get_production_cost,
    get_vehicle_stats,
    search_foxhole_wiki,
    server,
)


@pytest.mark.asyncio
async def test_mcp_server_registration():
    """Verify all expected tools and prompts are registered on MCPServer."""
    tools = await server.list_tools()
    tool_names = [t.name for t in tools]
    assert "search_foxhole_wiki" in tool_names
    assert "get_vehicle_stats" in tool_names
    assert "get_item_stats" in tool_names
    assert "get_structure_stats" in tool_names
    assert "get_production_cost" in tool_names
    assert "get_page_overview" in tool_names
    assert "edit_wiki_page" in tool_names
    assert "plan_production" in tool_names
    assert "calculate_required_resources" in tool_names
    assert "solve_leontief" not in tool_names

    prompts = await server.list_prompts()
    prompt_names = [p.name for p in prompts]
    assert "combat_intel" in prompt_names
    assert "logistics_plan" in prompt_names
    assert "production_planner" in prompt_names
    assert "bill_of_materials" in prompt_names
    assert "leontief_facility_planner" not in prompt_names


@pytest.mark.asyncio
async def test_search_foxhole_wiki():
    """Test live search query on foxhole.wiki.gg."""
    data = await search_foxhole_wiki("Bunker", limit=3)
    assert data["query"] == "Bunker"
    assert "results" in data
    assert len(data["results"]) > 0


@pytest.mark.asyncio
async def test_get_vehicle_stats_live():
    """Test vehicle parsing through MCP tool with live wiki data."""
    data = await get_vehicle_stats("Silverhand - Mk. IV")
    assert data["name"] == "Silverhand - Mk. IV"
    assert data["faction"] == "Warden"
    assert data["health"] == 3100
    assert data["armor_type"] == "Tier2Tank"
    assert len(data["armaments"]) >= 1


@pytest.mark.asyncio
async def test_get_item_stats_live():
    """Test item parsing through MCP tool with live wiki data."""
    data = await get_item_stats("No.2 Loughcaster")
    assert data["name"] == "No.2 Loughcaster"
    assert data["ammo"] == "7.62mm"
    assert data["crate_amount"] == 20


@pytest.mark.asyncio
async def test_get_production_cost_live():
    """Test production recipe lookup through MCP tool."""
    data = await get_production_cost("Dunne Transport")
    assert "production_recipes" in data
    assert len(data["production_recipes"]) > 0
    assert data["production_recipes"][0]["source"] == "Garage"


@pytest.mark.asyncio
async def test_create_server_custom_components():
    """Verify that create_server accepts custom tools instances (loose coupling)."""
    from unittest.mock import AsyncMock

    from foxhole.server import create_server
    from foxhole.tools.warapi import WarApiTools
    from foxhole.tools.wiki import WikiTools

    mock_wiki_client = AsyncMock()
    mock_wiki_client.search.return_value = []
    mock_wiki_client.get_page_data.return_value = {
        "title": "CustomItem",
        "wikitext": "{{Item Infobox | name = CustomItem | ammo = 9mm }}",
    }
    mock_wiki_client.opensearch.return_value = []

    mock_war_client = AsyncMock()
    mock_war_client.get_maps.return_value = ["TestHex"]

    custom_wiki = WikiTools(client=mock_wiki_client)
    custom_war = WarApiTools(war_client=mock_war_client)

    custom_server = create_server(
        name="custom-foxhole",
        description="Custom Test Server",
        version="1.0.0",
        wiki_tools=custom_wiki,
        war_tools=custom_war,
    )

    tools = await custom_server.list_tools()
    tool_names = [t.name for t in tools]
    assert "search_foxhole_wiki" in tool_names
    assert "edit_wiki_page" in tool_names
    assert "get_active_maps" in tool_names
    assert "plan_production" in tool_names

    # Test invoking with mocked war client
    res = await custom_war.get_active_maps()
    assert res["maps"] == ["TestHex"]

    # Test wiki resolution and item stats with mocked wiki client
    res_item = await custom_wiki.get_item_stats("CustomItem")
    assert res_item["name"] == "CustomItem"
    assert res_item["ammo"] == "9mm"


@pytest.mark.asyncio
async def test_create_server_modular_extensions():
    """Verify that create_server accepts arbitrary modular extensions conforming to ServerExtension protocol."""
    from mcp.server.mcpserver import MCPServer

    from foxhole.server import create_server

    class CustomExtension:
        def register(self, s: MCPServer) -> None:
            def dummy_tool(x: int) -> int:
                return x * 2

            s.add_tool(dummy_tool)

    def callable_extension(s: MCPServer) -> None:
        def another_tool(y: str) -> str:
            return y.upper()

        s.add_tool(another_tool)

    custom_server = create_server(extensions=[CustomExtension(), callable_extension])
    tools = await custom_server.list_tools()
    tool_names = [t.name for t in tools]
    assert "dummy_tool" in tool_names
    assert "another_tool" in tool_names
    # Standard tools are not loaded when custom extensions list is explicitly provided
    assert "search_foxhole_wiki" not in tool_names


def test_server_dynamic_attribute_delegation():
    """Verify PEP 562 dynamic attribute lookup on foxhole.server without hardcoded re-exports."""
    import importlib

    server_mod = importlib.import_module("foxhole.server")

    # Tool functions dynamically resolved from foxhole.tools
    assert callable(server_mod.search_foxhole_wiki)
    assert callable(server_mod.get_vehicle_stats)
    assert callable(server_mod.plan_production)
    assert callable(server_mod.get_flash_dispatch)

    # Prompt functions dynamically resolved from foxhole.prompts
    assert callable(server_mod.combat_intel)
    assert callable(server_mod.logistics_plan)

    # Dir includes dynamically resolved attributes
    dir_entries = dir(server_mod)
    assert "search_foxhole_wiki" in dir_entries
    assert "combat_intel" in dir_entries

    with pytest.raises(AttributeError):
        _ = server_mod.non_existent_symbol


@pytest.mark.asyncio
async def test_mount_mcp_server():
    """Verify mount_mcp_server copies tools, prompts, and resources from sub-server to target."""
    from mcp.server.mcpserver import MCPServer

    from foxhole.server import mount_mcp_server

    root_server = MCPServer(name="root")
    sub_server = MCPServer(name="sub")

    @sub_server.tool()
    def sub_tool(val: str) -> str:
        """A sub-server tool."""
        return f"sub:{val}"

    @sub_server.prompt()
    def sub_prompt(topic: str) -> str:
        """A sub-server prompt."""
        return f"Tell me about {topic}"

    mount_mcp_server(root_server, sub_server)

    tools = await root_server.list_tools()
    assert "sub_tool" in [t.name for t in tools]

    prompts = await root_server.list_prompts()
    assert "sub_prompt" in [p.name for p in prompts]

    from mcp.types import CallToolResult

    # Verify tool execution on root server
    res = await root_server.call_tool("sub_tool", {"val": "test"})
    assert isinstance(res, CallToolResult)
    assert not res.is_error
    assert res.structured_content == {"result": "sub:test"}


@pytest.mark.asyncio
async def test_create_server_composite_mcpserver():
    """Verify create_server mounts sub-server MCPServer instances passed as extensions."""
    from mcp.server.mcpserver import MCPServer
    from mcp.types import CallToolResult

    from foxhole.server import create_server

    sub_server = MCPServer(name="submodule")

    @sub_server.tool()
    def custom_sub_tool(count: int) -> int:
        return count + 10

    composite_server = create_server(extensions=[sub_server])

    tools = await composite_server.list_tools()
    tool_names = [t.name for t in tools]
    assert "custom_sub_tool" in tool_names
    assert "search_foxhole_wiki" not in tool_names

    res = await composite_server.call_tool("custom_sub_tool", {"count": 5})
    assert isinstance(res, CallToolResult)
    assert not res.is_error
    assert res.structured_content == {"result": 15}
