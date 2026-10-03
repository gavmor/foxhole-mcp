"""Story 5 — integer plans (scipy.optimize.milp). OWNER: story-5."""

from __future__ import annotations

from foxhole.lp.model import Limits, LPResult, Mode, Objective, RecipeMatrix, Target


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
    """Same model as solve_lp, with integrality:

    - whole_runs: recipe run counts are integers (whole crates / batches / plant burns).
    - Facility count y_s (integer) per source s with sum_j runs_j * seconds_j <=
      y_s * time_window_seconds; max_facilities caps y_s. facilities in the result are y_s.
    - facility_weight adds facility_weight * sum(y_s) to the objective (trade raw for
      fewer machines). Use scipy.optimize.milp; reuse story-3 building blocks if exported,
      but do not edit solve.py.
    """
    raise NotImplementedError("story-5")
