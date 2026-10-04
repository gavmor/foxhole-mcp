"""Foxhole data model for the LP planner.

The generic types live in the standalone `logistics` package. This module re-exports them
and defines the Foxhole-specific constants that the generic package deliberately does NOT
carry (they are injected into `logistics` via `Limits` / adapter wrappers).

Units
-----
- Item quantities are single units (a crate of 20 rifles counts as 20).
- Power is the commodity `POWER` measured in MW·s (energy). A recipe drawing P MW for a run of
  T seconds consumes P*T of it; a Diesel Power Plant run producing 5 MW for 45 s yields 225.
- "Quantity mode" plans totals (e.g. one squad's kit). "Rate mode" plans per real hour.
"""

from __future__ import annotations

from logistics.model import (
    ExtraConstraint,
    Limits,
    LPResult,
    Mode,
    Objective,
    Recipe,
    RecipeMatrix,
    Target,
)

POWER = "Power (MW·s)"

# Hand-mining effort per unit (wiki: Hammer 5/stroke @1.1 s on Salvage & Coal;
# Sledge Hammer 2/stroke @1.6 s on Components & Sulfur). Used by Objective.MIN_MINING_TIME.
MINING_SECONDS_PER_UNIT: dict[str, float] = {
    "Salvage": 1.1 / 5,
    "Coal": 1.1 / 5,
    "Components": 1.6 / 2,
    "Sulfur": 1.6 / 2,
}

# Sources whose recipes pull resources from the world (mines, harvesters, wells, pumps).
EXTRACTION_SOURCES = frozenset(
    {
        "Salvage Mine",
        "Component Mine",
        "Sulfur Mine",
        "Oil Well",
        "Water Pump",
        "Offshore Platform",
        "Stationary Harvester (Salvage)",
        "Stationary Harvester (Components)",
        "Stationary Harvester (Sulfur)",
        "Stationary Harvester (Coal)",
    }
)

__all__ = [
    "EXTRACTION_SOURCES",
    "MINING_SECONDS_PER_UNIT",
    "POWER",
    "ExtraConstraint",
    "LPResult",
    "Limits",
    "Mode",
    "Objective",
    "Recipe",
    "RecipeMatrix",
    "Target",
]
