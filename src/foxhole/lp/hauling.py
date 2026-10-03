"""Story 7 — hauling capacity. OWNER: story-7."""

from __future__ import annotations

import math

from foxhole.lp.model import ExtraConstraint

# Raw-resource vehicles: slots and units per slot (loose resources stack to 100).
# Corrected: R-5b "Sisyphus" Hauler has 14 slots per wiki vehicles table (was 20).
DEFAULT_VEHICLES: dict[str, tuple[int, int]] = {
    "Dunne Loadlugger 3c": (20, 100),
    "R-5b “Sisyphus” Hauler": (14, 100),
}

# Raw resources that must be physically hauled from extraction sites.
_HAULED_ITEMS = frozenset({"Salvage", "Components", "Sulfur", "Coal"})


def trips_needed(raw_used: dict[str, float], vehicle: str = "Dunne Loadlugger 3c") -> int:
    """Whole trips to haul raw_used: each item fills ceil(qty/stack) slots; trips =
    ceil(total_slots / slots). Unknown vehicle -> ValueError."""
    if vehicle not in DEFAULT_VEHICLES:
        raise ValueError(f"Unknown vehicle: {vehicle!r}")
    truck_slots, stack = DEFAULT_VEHICLES[vehicle]
    total_slots = sum(math.ceil(qty / stack) for qty in raw_used.values() if qty > 0)
    if total_slots == 0:
        return 0
    return math.ceil(total_slots / truck_slots)


def trip_constraint(max_trips: float, vehicle: str = "Dunne Loadlugger 3c") -> ExtraConstraint:
    """Linear (continuous) cap on hauled raw: sum(r_i / (stack*slots)) <= max_trips, for the
    hauled resources (Salvage, Components, Sulfur, Coal). For solve_lp's limits.extra."""
    if vehicle not in DEFAULT_VEHICLES:
        raise ValueError(f"Unknown vehicle: {vehicle!r}")
    truck_slots, stack = DEFAULT_VEHICLES[vehicle]
    coeff = 1.0 / (stack * truck_slots)
    return ExtraConstraint(
        name=f"hauling_trips_{vehicle}",
        coeffs={item: coeff for item in _HAULED_ITEMS},
        upper=max_trips,
    )


def trips_for_mixed_load(
    raw_used: dict[str, float], vehicle: str = "Dunne Loadlugger 3c"
) -> list[dict[str, int]]:
    """Per-trip load plan filling trucks greedily (largest slot-count item first).

    Returns a list of dicts {item: slots_loaded_this_trip}.
    """
    if vehicle not in DEFAULT_VEHICLES:
        raise ValueError(f"Unknown vehicle: {vehicle!r}")
    truck_slots, stack = DEFAULT_VEHICLES[vehicle]

    slots_needed: dict[str, int] = {
        item: math.ceil(qty / stack) for item, qty in raw_used.items() if qty > 0
    }
    if not slots_needed:
        return []

    # Descending by slots so largest item fills first.
    order = sorted(slots_needed, key=lambda item: slots_needed[item], reverse=True)
    remaining = dict(slots_needed)

    trips: list[dict[str, int]] = []
    while any(remaining[item] > 0 for item in order):
        trip: dict[str, int] = {}
        capacity = truck_slots
        for item in order:
            if remaining[item] <= 0 or capacity == 0:
                continue
            take = min(remaining[item], capacity)
            trip[item] = take
            remaining[item] -= take
            capacity -= take
        trips.append(trip)

    return trips
