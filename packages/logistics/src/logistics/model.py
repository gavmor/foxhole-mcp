"""Shared data model for the recipe-matrix production planner.

Domain-agnostic. A "recipe" turns some input items into some output items in one run; the
planner chooses how many runs of each recipe (and how much raw intake) satisfy a set of
output targets under an objective and linear constraints. Nothing here knows about any
particular game or economy -- callers supply the recipes and (via :class:`Limits`) any
domain-specific weights or restrictions.

Units
-----
- Item quantities are plain numbers in whatever unit the caller uses consistently.
- "Quantity mode" plans totals; "rate mode" plans per unit of time (``time_window_seconds``).
- Any commodity that must be *produced* and never taken as raw intake (e.g. an energy
  commodity) is named in ``Limits.produced_only``.
"""

from __future__ import annotations

from enum import StrEnum

import numpy as np
from pydantic import BaseModel, ConfigDict, Field


class Recipe(BaseModel):
    """One production recipe, normalised to a single run."""

    id: str = Field(description='Stable id, e.g. "<Output>#<Rank>@<Source>"')
    source: str = Field(description="Facility/process that runs the recipe")
    rank: int = Field(description="Rank among recipes for the same output (1 = primary)")
    outputs: dict[str, float] = Field(description="Units produced per run (byproducts included)")
    inputs: dict[str, float] = Field(description="Units consumed per run")
    seconds: float | None = Field(default=None, description="Time per run, if known")
    power_mw: float = Field(default=0.0, description="Rate drawn while running (rate-mode reports)")
    crate_output: bool = Field(default=False, description="Run yields whole crates/batches")
    crate_size: int | None = Field(default=None, description="Units per crate when crate_output")
    extraction: bool = Field(
        default=False, description="Source pulls item(s) from an external/raw supply"
    )


class RecipeMatrix(BaseModel):
    """Items x recipes. A[i, j] = net units of item i per run of recipe j (out - in)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    items: list[str]
    recipes: list[Recipe]
    A: np.ndarray = Field(description="shape (len(items), len(recipes)), float64")
    extractable: set[str] = Field(
        default_factory=set,
        description="Items always obtainable raw (mined/harvested/imported), even if a recipe "
        "also makes them",
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
        anything extractable (always obtainable raw), whatever else also produces it."""
        produced = {i for i in range(len(self.items)) if (self.A[i] > 0).any()}
        unproduced = {name for i, name in enumerate(self.items) if i not in produced}
        return unproduced | (self.extractable & set(self.items))


class Objective(StrEnum):
    MIN_RAW = "min_raw"  # minimise weighted raw-resource intake (Limits.raw_weights, default 1)
    MIN_MINING_TIME = "min_mining_time"  # minimise weighted raw intake by Limits.effort_weights
    MAX_THROUGHPUT = "max_throughput"  # maximise Z, the number of target bundles (rate mode)


class Mode(StrEnum):
    QUANTITY = "quantity"  # targets are totals
    RATE = "rate"  # targets and caps are per time window


class Target(BaseModel):
    item: str
    quantity: float = Field(description="Units (QUANTITY) or units per time window (RATE)")


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
        default_factory=set, description="Never consume or produce these"
    )
    allowed_recipes: set[str] | None = Field(
        default=None, description="If set, only these recipe ids may run"
    )
    denied_recipes: set[str] = Field(default_factory=set, description="Recipe ids that may not run")
    raw_weights: dict[str, float] = Field(
        default_factory=dict, description="Per-unit cost of raw items for MIN_RAW (default 1.0)"
    )
    produced_only: set[str] = Field(
        default_factory=set,
        description="Items that may never be taken as raw intake; they must be produced by a "
        "recipe (e.g. an energy commodity)",
    )
    effort_weights: dict[str, float] = Field(
        default_factory=dict,
        description="Per-raw-item weight for the MIN_MINING_TIME objective (weighted raw "
        "minimisation, e.g. hand-gathering time per unit)",
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
    power_mw: float | None = Field(default=None, description="Average rate drawn (rate mode)")
    notes: list[str] = Field(default_factory=list)
