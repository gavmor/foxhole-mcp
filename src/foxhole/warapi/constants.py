"""War API constants, icon types, map flags, and shard URLs."""

from enum import IntFlag

SHARDS: dict[str, str] = {
    "live-1": "https://war-service-live.foxholeservices.com/api",
    "live-2": "https://war-service-live-2.foxholeservices.com/api",
    "live-3": "https://war-service-live-3.foxholeservices.com/api",
    "dev": "https://war-service-dev.foxholeservices.com/api",
}

DEFAULT_SHARD = "live-1"


class MapFlags(IntFlag):
    """Bitmask flags for dynamic and static map items."""

    NONE = 0x00
    IS_VICTORY_BASE = 0x01
    IS_HOME_BASE = 0x02  # Deprecated in Update 29
    IS_BUILD_SITE = 0x04
    IS_SCORCHED = 0x10
    IS_TOWN_CLAIMED = 0x20


MAP_ICON_NAMES: dict[int, str] = {
    5: "Static Base 1",
    6: "Static Base 2",
    7: "Static Base 3",
    8: "Forward Base 1",
    9: "Forward Base 2",
    10: "Forward Base 3",
    11: "Hospital",
    12: "Vehicle Factory",
    13: "Armory",
    14: "Supply Station",
    15: "Workshop",
    16: "Manufacturing Plant",
    17: "Refinery",
    18: "Shipyard",
    19: "Engineering Center",
    20: "Salvage Field",
    21: "Component Field",
    22: "Fuel Field",
    23: "Sulfur Field",
    24: "World Map Tent",
    25: "Travel Tent",
    26: "Training Area",
    27: "Special Base (Keep)",
    28: "Observation Tower",
    29: "Fort",
    30: "Troop Ship",
    32: "Sulfur Mine",
    33: "Storage Facility",
    34: "Factory",
    35: "Garrison Station",
    36: "Ammo Factory",
    37: "Rocket Site",
    38: "Salvage Mine",
    39: "Construction Yard",
    40: "Component Mine",
    41: "Oil Well",
    45: "Relic Base 1",
    46: "Relic Base 2",
    47: "Relic Base 3",
    51: "Mass Production Factory",
    52: "Seaport",
    53: "Coastal Gun",
    54: "Soul Factory",
    56: "Town Base 1",
    57: "Town Base 2",
    58: "Town Base 3",
    59: "Storm Cannon",
    60: "Intel Center",
    61: "Coal Field",
    62: "Oil Field",
    70: "Rocket Target",
    71: "Rocket Ground Zero",
    72: "Rocket Site With Rocket",
    75: "Facility Mine Oil Rig",
    83: "Weather Station",
    84: "Mortar House",
    88: "Aircraft Depot",
    89: "Aircraft Factory",
    90: "Aircraft Radar",
    91: "Aircraft Runway (T1)",
    92: "Aircraft Runway (T2)",
}

# Categories for filtering map icons
ICON_CATEGORIES: dict[str, set[int]] = {
    "bases": {8, 27, 29, 45, 46, 47, 56, 57, 58},
    "logistics": {17, 18, 33, 34, 39, 51, 52},
    "resources": {20, 21, 22, 23, 32, 38, 40, 61, 62, 75},
    "defenses": {28, 35, 53, 59, 60, 83, 84},
    "rockets": {37, 70, 71, 72},
    "aircraft": {88, 89, 90, 91, 92},
}


def get_icon_name(icon_type: int) -> str:
    """Return readable name for a map icon ID."""
    return MAP_ICON_NAMES.get(icon_type, f"Unknown Icon ({icon_type})")
