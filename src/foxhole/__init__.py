"""Foxhole MediaWiki MCP Server & War API telemetry package."""

__version__ = "0.2.0"

from foxhole.client import FoxholeWikiClient
from foxhole.leontief import (
    LeontiefRequest,
    LeontiefResponse,
    MachineCount,
    MachineSpec,
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
from foxhole.server import server
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
    "FoxholeWikiClient",
    "GlobalCasualties",
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
    "ProductionRecipe",
    "SearchResult",
    "StructureStats",
    "VehicleStats",
    "VictoryTownStatus",
    "WarApiClient",
    "WarReport",
    "WarState",
    "__version__",
    "get_icon_name",
    "server",
    "solve_leontief",
]
