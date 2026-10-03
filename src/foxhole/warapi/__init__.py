"""Foxhole War API integration module."""

from foxhole.warapi.client import WarApiClient
from foxhole.warapi.constants import (
    DEFAULT_SHARD,
    ICON_CATEGORIES,
    MAP_ICON_NAMES,
    SHARDS,
    MapFlags,
    get_icon_name,
)
from foxhole.warapi.models import (
    GlobalCasualties,
    MapData,
    MapItem,
    MapTextItem,
    VictoryTownStatus,
    WarReport,
    WarState,
)

__all__ = [
    "DEFAULT_SHARD",
    "ICON_CATEGORIES",
    "MAP_ICON_NAMES",
    "SHARDS",
    "GlobalCasualties",
    "MapData",
    "MapFlags",
    "MapItem",
    "MapTextItem",
    "VictoryTownStatus",
    "WarApiClient",
    "WarReport",
    "WarState",
    "get_icon_name",
]
