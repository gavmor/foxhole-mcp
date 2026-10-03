"""Story 7 — hauling capacity. OWNER: story-7."""

from __future__ import annotations

from foxhole.lp.model import ExtraConstraint

# Raw-resource vehicles: slots and units per slot (loose resources stack to 100).
DEFAULT_VEHICLES: dict[str, tuple[int, int]] = {
    "Dunne Loadlugger 3c": (20, 100),
    "R-5b “Sisyphus” Hauler": (20, 100),
}


def trips_needed(raw_used: dict[str, float], vehicle: str = "Dunne Loadlugger 3c") -> int:
    """Whole trips to haul raw_used: each item fills ceil(qty/stack) slots; trips =
    ceil(total_slots / slots). Unknown vehicle -> ValueError."""
    raise NotImplementedError("story-7")


def trip_constraint(max_trips: float, vehicle: str = "Dunne Loadlugger 3c") -> ExtraConstraint:
    """Linear (continuous) cap on hauled raw: sum(r_i / (stack*slots)) <= max_trips, for the
    hauled resources (Salvage, Components, Sulfur, Coal). For solve_lp's limits.extra."""
    raise NotImplementedError("story-7")
