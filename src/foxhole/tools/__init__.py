"""Foxhole MCP tools modular components."""

from foxhole.tools.base import BaseToolProvider
from foxhole.tools.dispatches import (
    DispatchesTools,
    default_dispatches_tools,
    get_flash_dispatch,
    get_propaganda_wire,
)
from foxhole.tools.gamelogs import GameLogTools, default_game_log_tools
from foxhole.tools.optimize import OptimizeTools, default_optimize_tools
from foxhole.tools.production import (
    ProductionTools,
    calculate_required_resources,
    default_fetch_recipes,
    default_production_tools,
    plan_production,
    register_production_tools,
)
from foxhole.tools.stockpiles import StockpileTools, default_stockpile_tools
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
    edit_wiki_page,
    get_item_stats,
    get_page_overview,
    get_production_cost,
    get_structure_stats,
    get_vehicle_stats,
    resolve_title,
    search_foxhole_wiki,
)

# Standard tuple of default tool providers implementing the .register(server) protocol
DEFAULT_TOOL_PROVIDERS: tuple[
    WikiTools,
    WarApiTools,
    DispatchesTools,
    ProductionTools,
    StockpileTools,
    OptimizeTools,
    GameLogTools,
] = (
    default_wiki_tools,
    default_war_tools,
    default_dispatches_tools,
    default_production_tools,
    default_stockpile_tools,
    default_optimize_tools,
    default_game_log_tools,
)

__all__ = [
    "DEFAULT_TOOL_PROVIDERS",
    "BaseToolProvider",
    "DispatchesTools",
    "GameLogTools",
    "OptimizeTools",
    "ProductionTools",
    "StockpileTools",
    "WarApiTools",
    "WikiTools",
    "calculate_required_resources",
    "default_dispatches_tools",
    "default_fetch_recipes",
    "default_game_log_tools",
    "default_optimize_tools",
    "default_production_tools",
    "default_stockpile_tools",
    "default_war_tools",
    "default_wiki_tools",
    "edit_wiki_page",
    "get_active_maps",
    "get_flash_dispatch",
    "get_item_stats",
    "get_map_intel",
    "get_page_overview",
    "get_production_cost",
    "get_propaganda_wire",
    "get_structure_stats",
    "get_vehicle_stats",
    "get_victory_town_status",
    "get_war_casualties",
    "get_war_status",
    "plan_production",
    "register_production_tools",
    "resolve_title",
    "search_foxhole_wiki",
]
