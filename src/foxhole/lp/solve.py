"""Story 3 — continuous LP core (scipy.optimize.linprog, HiGHS). OWNER: story-3."""

from __future__ import annotations

from foxhole.lp.model import Limits, LPResult, Mode, Objective, RecipeMatrix, Target


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
    """Choose recipe run counts x >= 0 (one per recipe) and raw intake r >= 0 (one per raw
    item) so that for every item: A x + r + s >= d, where d = targets (times Z for
    MAX_THROUGHPUT) and s = inventory (free supply, QUANTITY mode only).

    - Raw intake is allowed only for matrix.raw_items (plus POWER is never raw: it must be
      produced). Items in limits.banned_items get no raw intake and their recipes (any that
      consume or produce them) are fixed to 0. allowed_recipes / denied_recipes likewise.
    - limits.max_raw caps r; limits.extra adds sum(coeffs * r) <= upper.
    - MIN_RAW minimises sum(raw_weights.get(i, 1) * r_i). MIN_MINING_TIME minimises
      sum(MINING_SECONDS_PER_UNIT.get(i, 0) * r_i) + tiny epsilon * sum(r) (tie-break).
      MAX_THROUGHPUT maximises Z subject to caps (RATE mode; error if no caps bound it).
    - Fill LPResult: runs (>1e-9), raw_used, produced, consumed, surplus (net beyond
      targets), inventory_used, facilities = sum over recipes of runs * seconds /
      time_window_seconds grouped by source (fractional), power_mw in RATE mode,
      status 'infeasible'/'unbounded' with empty fields when the solver says so.
    - Unknown target items raise ValueError.
    """
    raise NotImplementedError("story-3")
