"""Foxhole Leontief adapter.

The generic Leontief IO solver and models live in the `logistics` package; re-exported here.
`solve_curried_economy` stays Foxhole-specific (it loads the curried Foxhole economy matrix).
"""

from typing import Any

from logistics.leontief import (
    LeontiefRequest,
    LeontiefResponse,
    MachineCount,
    MachineSpec,
    solve_leontief,
)

__all__ = [
    "LeontiefRequest",
    "LeontiefResponse",
    "MachineCount",
    "MachineSpec",
    "solve_curried_economy",
    "solve_leontief",
]


def solve_curried_economy(
    demand: dict[str, float],
    include_machine_counts: bool = False,
    time_window_seconds: float | None = None,
) -> dict[str, Any]:
    """Solve the curried Foxhole Leontief economy matrix for arbitrary production demand."""
    from foxhole.economy import get_economy_solver

    solver = get_economy_solver()
    plan = solver.solve(
        demand=demand,
        include_machine_counts=include_machine_counts,
        time_window_seconds=time_window_seconds,
    )
    return plan.model_dump(exclude_none=True)
