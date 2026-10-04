"""Foxhole adapter over the generic `logistics` MILP solver.

Injects MINING_SECONDS_PER_UNIT as the MIN_MINING_TIME effort weights. Note: the MILP path
historically does NOT treat POWER as produced-only, so produced_only is left untouched here.
"""

from __future__ import annotations

from logistics.integer import solve_milp as _solve_milp

from foxhole.lp.model import (
    MINING_SECONDS_PER_UNIT,
    Limits,
    LPResult,
    Mode,
    Objective,
    RecipeMatrix,
    Target,
)


def solve_milp(
    matrix: RecipeMatrix,
    targets: list[Target],
    *,
    objective: Objective = Objective.MIN_RAW,
    mode: Mode = Mode.QUANTITY,
    limits: Limits | None = None,
    inventory: dict[str, float] | None = None,
    time_window_seconds: float = 3600.0,
    whole_runs: bool = True,
    max_facilities: dict[str, int] | None = None,
    facility_weight: float = 0.0,
) -> LPResult:
    """Foxhole-flavored solve_milp: MIN_MINING_TIME uses hand-mining times."""
    base = limits or Limits()
    fox_limits = base.model_copy(
        update={"effort_weights": {**MINING_SECONDS_PER_UNIT, **base.effort_weights}}
    )
    return _solve_milp(
        matrix,
        targets,
        objective=objective,
        mode=mode,
        limits=fox_limits,
        inventory=inventory,
        time_window_seconds=time_window_seconds,
        whole_runs=whole_runs,
        max_facilities=max_facilities,
        facility_weight=facility_weight,
    )
