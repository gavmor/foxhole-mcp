"""Hauling capacity: slot-packing of raw resources into fixed-capacity vehicles.

Domain-agnostic. A vehicle is described by ``slots`` (how many stacks it carries) and
``stack`` (units per stack). Callers that work with named vehicles keep their own
name -> (slots, stack) table and pass the resolved spec in.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

from logistics.model import ExtraConstraint


def trips_needed(raw_used: dict[str, float], *, slots: int, stack: int) -> int:
    """Whole trips to haul raw_used: each item fills ceil(qty/stack) slots; trips =
    ceil(total_slots / slots)."""
    total_slots = sum(math.ceil(qty / stack) for qty in raw_used.values() if qty > 0)
    if total_slots == 0:
        return 0
    return math.ceil(total_slots / slots)


def trip_constraint(
    max_trips: float,
    *,
    slots: int,
    stack: int,
    hauled_items: Iterable[str],
    name: str = "hauling_trips",
) -> ExtraConstraint:
    """Linear (continuous) cap on hauled raw: sum(r_i / (stack*slots)) <= max_trips, over the
    given hauled_items. For solve_lp's limits.extra."""
    coeff = 1.0 / (stack * slots)
    return ExtraConstraint(
        name=name,
        coeffs={item: coeff for item in hauled_items},
        upper=max_trips,
    )


def trips_for_mixed_load(
    raw_used: dict[str, float], *, slots: int, stack: int
) -> list[dict[str, int]]:
    """Per-trip load plan filling vehicles greedily (largest slot-count item first).

    Returns a list of dicts {item: slots_loaded_this_trip}.
    """
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
        capacity = slots
        for item in order:
            if remaining[item] <= 0 or capacity == 0:
                continue
            take = min(remaining[item], capacity)
            trip[item] = take
            remaining[item] -= take
            capacity -= take
        trips.append(trip)

    return trips
