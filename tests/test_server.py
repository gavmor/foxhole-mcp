"""Unit tests for Foxhole MCP Server tools."""

import json

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
    res_str = await search_foxhole_wiki("Bunker", limit=3)
    data = json.loads(res_str)
    assert data["query"] == "Bunker"
    assert "results" in data
    assert len(data["results"]) > 0


@pytest.mark.asyncio
async def test_get_vehicle_stats_live():
    """Test vehicle parsing through MCP tool with live wiki data."""
    res_str = await get_vehicle_stats("Silverhand - Mk. IV")
    data = json.loads(res_str)
    assert data["name"] == "Silverhand - Mk. IV"
    assert data["faction"] == "Warden"
    assert data["health"] == 3100
    assert data["armor_type"] == "Tier2Tank"
    assert len(data["armaments"]) >= 1


@pytest.mark.asyncio
async def test_get_item_stats_live():
    """Test item parsing through MCP tool with live wiki data."""
    res_str = await get_item_stats("No.2 Loughcaster")
    data = json.loads(res_str)
    assert data["name"] == "No.2 Loughcaster"
    assert data["ammo"] == "7.62mm"
    assert data["crate_amount"] == 20


@pytest.mark.asyncio
async def test_get_production_cost_live():
    """Test production recipe lookup through MCP tool."""
    res_str = await get_production_cost("Dunne Transport")
    data = json.loads(res_str)
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
    parsed = json.loads(res)
    assert parsed["maps"] == ["TestHex"]

    # Test wiki resolution and item stats with mocked wiki client
    res_item = await custom_wiki.get_item_stats("CustomItem")
    parsed_item = json.loads(res_item)
    assert parsed_item["name"] == "CustomItem"
    assert parsed_item["ammo"] == "9mm"
