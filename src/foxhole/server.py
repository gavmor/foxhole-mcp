"""Foxhole MCP Server orchestrator combining MediaWiki tools, War API telemetry, and factory optimization."""

import logging

from mcp.server.mcpserver import MCPServer

from foxhole.prompts import (
    bill_of_materials,
    combat_intel,
    frontline_intel,
    logistics_plan,
    production_planner,
    register_prompts,
    strategic_war_overview,
)
from foxhole.telemetry import apply_telemetry_mode, setup_telemetry
from foxhole.tools.production import (
    calculate_required_resources,
    default_fetch_recipes,
    plan_production,
    register_production_tools,
)
from foxhole.tools.warapi import (
    WarApiTools,
    default_war_tools,
    get_active_maps,
    get_map_intel,
    get_victory_town_status,
    get_war_casualties,
    get_war_status,
)
from foxhole.tools.wiki import (
    WikiTools,
    default_wiki_tools,
    get_item_stats,
    get_page_overview,
    get_production_cost,
    get_structure_stats,
    get_vehicle_stats,
    search_foxhole_wiki,
)

logger = logging.getLogger(__name__)


def create_server(
    name: str = "foxhole",
    description: str = "Foxhole MCP server combining MediaWiki data with live War API telemetry",
    version: str = "0.2.0",
    wiki_tools: WikiTools | None = None,
    war_tools: WarApiTools | None = None,
    telemetry_mode: str | None = None,
    telemetry: bool | None = None,
) -> MCPServer:
    """Create and configure a Foxhole MCPServer instance with all tools and prompts."""
    setup_telemetry(service_name=name, telemetry_mode=telemetry_mode, enabled=telemetry)

    mcp_server = MCPServer(
        name=name,
        description=description,
        version=version,
    )

    (wiki_tools or default_wiki_tools).register(mcp_server)
    (war_tools or default_war_tools).register(mcp_server)
    register_production_tools(mcp_server)
    register_prompts(mcp_server)

    apply_telemetry_mode(mcp_server, mode=telemetry_mode, enabled=telemetry)

    return mcp_server


# Default server instance
server = create_server()

# Backward-compatible references
client = default_wiki_tools.client
war_client = default_war_tools.war_client
_resolve_title = default_wiki_tools.resolve_title
_fetch_recipes = default_fetch_recipes

__all__ = [
    "_fetch_recipes",
    "_resolve_title",
    "bill_of_materials",
    "calculate_required_resources",
    "client",
    "combat_intel",
    "create_server",
    "frontline_intel",
    "get_active_maps",
    "get_item_stats",
    "get_map_intel",
    "get_page_overview",
    "get_production_cost",
    "get_structure_stats",
    "get_vehicle_stats",
    "get_victory_town_status",
    "get_war_casualties",
    "get_war_status",
    "logistics_plan",
    "plan_production",
    "production_planner",
    "search_foxhole_wiki",
    "server",
    "setup_telemetry",
    "strategic_war_overview",
    "war_client",
]
