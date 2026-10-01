"""Foxhole MCP tools modular components."""

from foxhole.tools.production import (
    calculate_required_resources,
    default_fetch_recipes,
    plan_production,
    register_production_tools,
    solve_leontief,
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
    resolve_title,
    search_foxhole_wiki,
)

__all__ = [
    "WarApiTools",
    "WikiTools",
    "calculate_required_resources",
    "default_fetch_recipes",
    "default_war_tools",
    "default_wiki_tools",
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
    "plan_production",
    "register_production_tools",
    "resolve_title",
    "search_foxhole_wiki",
    "solve_leontief",
]
