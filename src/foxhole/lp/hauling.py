"""Foxhole adapter over the generic `logistics` hauling (slot-packing) functions.

Holds the Foxhole vehicle table and hauled-resource set, and exposes the vehicle-name API the
rest of foxhole uses, delegating the math to `logistics.hauling`.
"""

from __future__ import annotations

from logistics.hauling import trip_constraint as _trip_constraint
from logistics.hauling import trips_for_mixed_load as _trips_for_mixed_load
from logistics.hauling import trips_needed as _trips_needed
from logistics.model import ExtraConstraint

# Raw-resource vehicles: slots and units per slot (loose resources stack to 100).
# Corrected: R-5b "Sisyphus" Hauler has 14 slots per wiki vehicles table (was 20).
DEFAULT_VEHICLES: dict[str, tuple[int, int]] = {
    "Dunne Loadlugger 3c": (20, 100),
    "R-5b “Sisyphus” Hauler": (14, 100),
}

# Raw resources that must be physically hauled from extraction sites.
_HAULED_ITEMS = frozenset({"Salvage", "Components", "Sulfur", "Coal"})


def _spec(vehicle: str) -> tuple[int, int]:
    if vehicle not in DEFAULT_VEHICLES:
        raise ValueError(f"Unknown vehicle: {vehicle!r}")
    return DEFAULT_VEHICLES[vehicle]


def trips_needed(raw_used: dict[str, float], vehicle: str = "Dunne Loadlugger 3c") -> int:
    """Whole trips to haul raw_used with the named vehicle. Unknown vehicle -> ValueError."""
    slots, stack = _spec(vehicle)
    return _trips_needed(raw_used, slots=slots, stack=stack)


def trip_constraint(max_trips: float, vehicle: str = "Dunne Loadlugger 3c") -> ExtraConstraint:
    """Linear cap on hauled raw (Salvage, Components, Sulfur, Coal) for solve_lp's limits.extra."""
    slots, stack = _spec(vehicle)
    return _trip_constraint(
        max_trips,
        slots=slots,
        stack=stack,
        hauled_items=_HAULED_ITEMS,
        name=f"hauling_trips_{vehicle}",
    )


def trips_for_mixed_load(
    raw_used: dict[str, float], vehicle: str = "Dunne Loadlugger 3c"
) -> list[dict[str, int]]:
    """Per-trip load plan filling the named vehicle greedily (largest slot-count item first)."""
    slots, stack = _spec(vehicle)
    return _trips_for_mixed_load(raw_used, slots=slots, stack=stack)
