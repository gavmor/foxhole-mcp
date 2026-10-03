"""Shared data model for the linear-programming production planner (epic/lp-planner).

This module is the CONTRACT between stories. It is implemented, not stubbed: stories import
these types and must not change existing fields (add optional ones only, with defaults).

Units
-----
- Item quantities are single units (a crate of 20 rifles counts as 20).
- Power is the commodity `POWER` measured in MW·s (energy). A recipe drawing P MW for a run of
  T seconds consumes P*T of it; a Diesel Power Plant run producing 5 MW for 45 s yields 225.
- "Quantity mode" plans totals (e.g. one squad's kit). "Rate mode" plans per real hour.
"""

from __future__ import annotations

from enum import StrEnum

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

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


class Recipe(BaseModel):
    """One production recipe (one wiki Production row), normalised to a single run."""

    id: str = Field(description='Stable id: "<Output>#<RecipeRank>@<Source>"')
    source: str = Field(description="Facility, e.g. 'Refinery', 'Materials Factory'")
    rank: int = Field(description="Wiki RecipeRank (1 = primary)")
    outputs: dict[str, float] = Field(description="Units produced per run (byproducts included)")
    inputs: dict[str, float] = Field(
        description="Units consumed per run; includes POWER if powered"
    )
    seconds: float | None = Field(default=None, description="Real seconds per run, if known")
    power_mw: float = Field(default=0.0, description="MW drawn while running")
    crate_output: bool = Field(default=False, description="Run yields whole crates (IsCrateOutput)")
    crate_size: int | None = Field(default=None, description="Units per crate when crate_output")
    extraction: bool = Field(default=False, description="Source is in EXTRACTION_SOURCES")


class RecipeMatrix(BaseModel):
    """Items x recipes. A[i, j] = net units of item i per run of recipe j (out - in)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    items: list[str]
    recipes: list[Recipe]
    A: np.ndarray = Field(description="shape (len(items), len(recipes)), float64")
    extractable: set[str] = Field(
        default_factory=set,
        description="Items mines/harvesters yield (always obtainable raw, even if a recipe "
        "also makes them, e.g. Components by recycling or Sulfur as a byproduct)",
    )

    @property
    def item_index(self) -> dict[str, int]:
        return {name: i for i, name in enumerate(self.items)}

    @property
    def recipe_index(self) -> dict[str, int]:
        return {r.id: j for j, r in enumerate(self.recipes)}

    @property
    def raw_items(self) -> set[str]:
        """Items obtainable from outside: those no recipe in the matrix produces, plus
        anything extractable (mined/harvested), whatever else also produces it."""
        produced = {i for i in range(len(self.items)) if (self.A[i] > 0).any()}
        unproduced = {name for i, name in enumerate(self.items) if i not in produced}
        return unproduced | (self.extractable & set(self.items))


class Objective(StrEnum):
    MIN_RAW = "min_raw"  # minimise weighted raw-resource intake (Limits.raw_weights, default 1)
    MIN_MINING_TIME = "min_mining_time"  # minimise hand-mining seconds (MINING_SECONDS_PER_UNIT)
    MAX_THROUGHPUT = "max_throughput"  # maximise Z, the number of target bundles (rate mode)


class Mode(StrEnum):
    QUANTITY = "quantity"  # targets are totals
    RATE = "rate"  # targets and caps are per real hour


class Target(BaseModel):
    item: str
    quantity: float = Field(description="Units (QUANTITY) or units per hour (RATE)")


class ExtraConstraint(BaseModel):
    """Linear cap on raw intake: sum(coeffs[item] * raw_used[item]) <= upper."""

    name: str
    coeffs: dict[str, float]
    upper: float


class Limits(BaseModel):
    max_raw: dict[str, float] = Field(
        default_factory=dict, description="Cap on raw intake per item (same unit as mode)"
    )
    banned_items: set[str] = Field(
        default_factory=set, description="Never consume or produce these (e.g. {'Coal'})"
    )
    allowed_recipes: set[str] | None = Field(
        default=None, description="If set, only these recipe ids may run"
    )
    denied_recipes: set[str] = Field(default_factory=set, description="Recipe ids that may not run")
    raw_weights: dict[str, float] = Field(
        default_factory=dict, description="Per-unit cost of raw items for MIN_RAW (default 1.0)"
    )
    extra: list[ExtraConstraint] = Field(default_factory=list)


class LPResult(BaseModel):
    status: str = Field(description="'optimal', 'infeasible', 'unbounded' or an error message")
    objective: str
    objective_value: float | None = None
    mode: str = Mode.QUANTITY
    runs: dict[str, float] = Field(default_factory=dict, description="Recipe id -> runs (>0 only)")
    raw_used: dict[str, float] = Field(default_factory=dict, description="Raw intake per item")
    produced: dict[str, float] = Field(default_factory=dict, description="Gross output per item")
    consumed: dict[str, float] = Field(default_factory=dict, description="Internal use per item")
    surplus: dict[str, float] = Field(
        default_factory=dict, description="Output beyond targets (byproducts, crate rounding)"
    )
    inventory_used: dict[str, float] = Field(default_factory=dict)
    throughput: float | None = Field(default=None, description="Z for MAX_THROUGHPUT")
    facilities: dict[str, float] = Field(
        default_factory=dict,
        description="Source -> machines needed (fractional in LP, whole in MILP)",
    )
    power_mw: float | None = Field(default=None, description="Average MW drawn (rate mode)")
    notes: list[str] = Field(default_factory=list)
