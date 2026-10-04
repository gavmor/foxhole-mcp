"""Foxhole adapter over the generic `logistics` LP solver.

Injects the Foxhole domain constants (POWER is produced-only; MINING_SECONDS_PER_UNIT are the
effort weights for MIN_MINING_TIME) so callers get Foxhole behavior without passing them.
"""

from __future__ import annotations

# Re-exported for callers/tests that reach into the solver internals.
from logistics.solve import (  # noqa: F401
    _active_recipe_mask,
    _raw_item_indices,
    build_balance_constraints,
)
from logistics.solve import solve_lp as _solve_lp

from foxhole.lp.model import (
    MINING_SECONDS_PER_UNIT,
    POWER,
    Limits,
    LPResult,
    Mode,
    Objective,
    RecipeMatrix,
    Target,
)


def _with_foxhole_limits(limits: Limits | None) -> Limits:
    """Return a copy of limits with POWER produced-only and mining effort weights applied
    (caller-supplied values win)."""
    base = limits or Limits()
    return base.model_copy(
        update={
            "produced_only": base.produced_only | {POWER},
            "effort_weights": {**MINING_SECONDS_PER_UNIT, **base.effort_weights},
        }
    )


def solve_lp(
    matrix: RecipeMatrix,
    targets: list[Target],
    *,
    objective: Objective = Objective.MIN_RAW,
    mode: Mode = Mode.QUANTITY,
    limits: Limits | None = None,
    inventory: dict[str, float] | None = None,
    time_window_seconds: float = 3600.0,
) -> LPResult:
    """Foxhole-flavored solve_lp: POWER is never raw, MIN_MINING_TIME uses hand-mining times."""
    return _solve_lp(
        matrix,
        targets,
        objective=objective,
        mode=mode,
        limits=_with_foxhole_limits(limits),
        inventory=inventory,
        time_window_seconds=time_window_seconds,
    )
