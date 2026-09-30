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

    prompts = await server.list_prompts()
    prompt_names = [p.name for p in prompts]
    assert "combat_intel" in prompt_names
    assert "logistics_plan" in prompt_names


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
