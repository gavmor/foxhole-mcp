"""Foxhole MediaWiki MCP Server & War API telemetry package."""

__version__ = "0.2.0"

from foxhole.client import FoxholeWikiClient
from foxhole.economy import (
    CurriedEconomySolver,
    ItemCategory,
    ItemDefinition,
    ProductionPlan,
    get_economy_solver,
)
from foxhole.leontief import (
    LeontiefRequest,
    LeontiefResponse,
    MachineCount,
    MachineSpec,
    solve_curried_economy,
    solve_leontief,
)
from foxhole.models import (
    Armament,
    ItemStats,
    PageContent,
    ProductionRecipe,
    SearchResult,
    StructureStats,
    VehicleStats,
)
from foxhole.planner import plan_production
from foxhole.server import create_server, server
from foxhole.warapi import (
    DEFAULT_SHARD,
    ICON_CATEGORIES,
    MAP_ICON_NAMES,
    SHARDS,
    GlobalCasualties,
    MapData,
    MapFlags,
    MapItem,
    MapTextItem,
    VictoryTownStatus,
    WarApiClient,
    WarReport,
    WarState,
    get_icon_name,
)

__all__ = [
    "DEFAULT_SHARD",
    "ICON_CATEGORIES",
    "MAP_ICON_NAMES",
    "SHARDS",
    "Armament",
    "CurriedEconomySolver",
    "FoxholeWikiClient",
    "GlobalCasualties",
    "ItemCategory",
    "ItemDefinition",
    "ItemStats",
    "LeontiefRequest",
    "LeontiefResponse",
    "MachineCount",
    "MachineSpec",
    "MapData",
    "MapFlags",
    "MapItem",
    "MapTextItem",
    "PageContent",
    "ProductionPlan",
    "ProductionRecipe",
    "SearchResult",
    "StructureStats",
    "VehicleStats",
    "VictoryTownStatus",
    "WarApiClient",
    "WarReport",
    "WarState",
    "__version__",
    "create_server",
    "get_economy_solver",
    "get_icon_name",
    "plan_production",
    "server",
    "solve_curried_economy",
    "solve_leontief",
]
