"""Foxhole MediaWiki MCP Server & structured game data package."""

__version__ = "0.1.0"

from foxhole.client import FoxholeWikiClient
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

__all__ = [
    "Armament",
    "FoxholeWikiClient",
    "ItemStats",
    "PageContent",
    "ProductionRecipe",
    "SearchResult",
    "StructureStats",
    "VehicleStats",
    "__version__",
    "server",
]
