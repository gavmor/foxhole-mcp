"""Story 5 — integer plans (scipy.optimize.milp). OWNER: story-5."""

from __future__ import annotations

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp  # type: ignore[import-untyped]

from foxhole.lp.model import (
    MINING_SECONDS_PER_UNIT,
    Limits,
    LPResult,
    Mode,
    Objective,
    RecipeMatrix,
    Target,
)

_MILP_STATUS: dict[int, str] = {
    0: "optimal",
    2: "infeasible",
    3: "unbounded",
}


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
    lim = limits or Limits()
    inv = inventory or {}
    max_fac = max_facilities or {}

    item_idx = matrix.item_index
    n_items = len(matrix.items)
    n_recipes = len(matrix.recipes)

    for t in targets:
        if t.item not in item_idx:
            raise ValueError(f"Unknown target item: {t.item!r}")

    # Demand vector
    demand = np.zeros(n_items)
    for t in targets:
        demand[item_idx[t.item]] += t.quantity

    # Inventory offset: eff_demand = max(0, demand - inventory)
    inv_vec = np.zeros(n_items)
    for item, qty in inv.items():
        if item in item_idx and qty > 0:
            inv_vec[item_idx[item]] += float(qty)
    eff_demand = np.maximum(0.0, demand - inv_vec)

    # Raw items (can take external intake r)
    raw_items = matrix.raw_items - lim.banned_items
    raw_list = sorted(raw_items)
    raw_idx_map = {item: k for k, item in enumerate(raw_list)}
    n_raw = len(raw_list)

    # Unique sources (insertion order)
    src_seen: set[str] = set()
    all_sources: list[str] = []
    for rec in matrix.recipes:
        if rec.source not in src_seen:
            all_sources.append(rec.source)
            src_seen.add(rec.source)
    source_idx = {s: k for k, s in enumerate(all_sources)}
    n_sources = len(all_sources)

    has_z = objective == Objective.MAX_THROUGHPUT
    # Variable layout: [x (n_r) | r (n_raw) | y (n_s) | z (0 or 1)]
    x0, r0, y0 = 0, n_recipes, n_recipes + n_raw
    z_i = n_recipes + n_raw + n_sources
    n_vars = z_i + (1 if has_z else 0)

    # Disabled recipes
    disabled: set[int] = set()
    for j, rec in enumerate(matrix.recipes):
        if rec.id in lim.denied_recipes:
            disabled.add(j)
            continue
        if lim.allowed_recipes is not None and rec.id not in lim.allowed_recipes:
            disabled.add(j)
            continue
        for banned in lim.banned_items:
            if banned in rec.outputs or banned in rec.inputs:
                disabled.add(j)
                break

    # Objective vector
    c = np.zeros(n_vars)
    if objective == Objective.MIN_RAW:
        for k, item in enumerate(raw_list):
            c[r0 + k] = lim.raw_weights.get(item, 1.0)
    elif objective == Objective.MIN_MINING_TIME:
        for k, item in enumerate(raw_list):
            c[r0 + k] = MINING_SECONDS_PER_UNIT.get(item, 1e-6)
    elif objective == Objective.MAX_THROUGHPUT:
        c[z_i] = -1.0  # minimize -Z = maximize Z
    for s in range(n_sources):
        c[y0 + s] += facility_weight

    # Variable bounds
    lb_arr = np.zeros(n_vars)
    ub_arr = np.full(n_vars, np.inf)
    for j in disabled:
        ub_arr[x0 + j] = 0.0
    for k, item in enumerate(raw_list):
        if item in lim.max_raw:
            ub_arr[r0 + k] = float(lim.max_raw[item])
    for source, cap in max_fac.items():
        if source in source_idx:
            ub_arr[y0 + source_idx[source]] = float(cap)

    # Integrality: 1 = integer, 0 = continuous
    integ = np.zeros(n_vars, dtype=np.intp)
    if whole_runs:
        integ[x0 : x0 + n_recipes] = 1
    integ[y0 : y0 + n_sources] = 1

    # Constraints
    cons: list[LinearConstraint] = []

    # Balance: A*x + r [- Z*d] >= eff_demand
    A_bal = np.zeros((n_items, n_vars))
    A_bal[:, x0 : x0 + n_recipes] = matrix.A
    for k, item in enumerate(raw_list):
        A_bal[item_idx[item], r0 + k] = 1.0
    if has_z:
        for i in range(n_items):
            A_bal[i, z_i] = -demand[i]
    cons.append(LinearConstraint(A_bal, lb=eff_demand, ub=np.inf))

    # Facility time: sum_j(seconds_j * x_j) for source s <= y_s * time_window
    if n_sources > 0:
        A_fac = np.zeros((n_sources, n_vars))
        for j, rec in enumerate(matrix.recipes):
            if j not in disabled and rec.seconds is not None:
                A_fac[source_idx[rec.source], x0 + j] = rec.seconds
        for s in range(n_sources):
            A_fac[s, y0 + s] = -time_window_seconds
        cons.append(LinearConstraint(A_fac, lb=-np.inf, ub=0.0))

    # Extra raw-intake caps
    for extra in lim.extra:
        row = np.zeros(n_vars)
        for item, coeff in extra.coeffs.items():
            if item in raw_idx_map:
                row[r0 + raw_idx_map[item]] = coeff
        cons.append(LinearConstraint(row.reshape(1, -1), lb=-np.inf, ub=extra.upper))

    bounds = Bounds(lb=lb_arr, ub=ub_arr)
    res = milp(c=c, constraints=cons, integrality=integ, bounds=bounds)

    status = _MILP_STATUS.get(res.status, res.message)
    if res.status != 0 or res.x is None:
        return LPResult(status=status, objective=str(objective), mode=str(mode))

    sol = res.x
    x_sol = sol[x0 : x0 + n_recipes]
    r_sol = sol[r0 : r0 + n_raw]
    y_sol = sol[y0 : y0 + n_sources]

    # Runs
    runs: dict[str, float] = {}
    for j, rec in enumerate(matrix.recipes):
        if x_sol[j] > 1e-9:
            runs[rec.id] = float(x_sol[j])

    # Raw used
    raw_used: dict[str, float] = {}
    for k, item in enumerate(raw_list):
        if r_sol[k] > 1e-9:
            raw_used[item] = float(r_sol[k])

    # Inventory used (inventory that covered explicit demand)
    inventory_used: dict[str, float] = {}
    for i, item in enumerate(matrix.items):
        used = min(inv_vec[i], demand[i])
        if used > 1e-9:
            inventory_used[item] = float(used)

    # Produced and consumed
    A = matrix.A
    produced: dict[str, float] = {}
    consumed: dict[str, float] = {}
    gross_out = (np.maximum(0.0, A) @ x_sol).tolist()
    gross_in = (np.maximum(0.0, -A) @ x_sol).tolist()
    for i, item in enumerate(matrix.items):
        if gross_out[i] > 1e-9:
            produced[item] = float(gross_out[i])
        if gross_in[i] > 1e-9:
            consumed[item] = float(gross_in[i])

    # Surplus: net from recipes + raw intake - effective demand
    surplus: dict[str, float] = {}
    net_vec = A @ x_sol
    for i, item in enumerate(matrix.items):
        r_contrib = r_sol[raw_idx_map[item]] if item in raw_idx_map else 0.0
        slack = float(net_vec[i]) + r_contrib - eff_demand[i]
        if slack > 1e-9:
            surplus[item] = slack

    # Facilities (integer y values)
    facilities: dict[str, float] = {}
    for s, source in enumerate(all_sources):
        v = round(float(y_sol[s]))
        if v > 0:
            facilities[source] = float(v)

    obj_value = float(res.fun)
    throughput: float | None = None
    if has_z:
        throughput = float(sol[z_i])
        obj_value = throughput

    return LPResult(
        status="optimal",
        objective=str(objective),
        objective_value=obj_value,
        mode=str(mode),
        runs=runs,
        raw_used=raw_used,
        produced=produced,
        consumed=consumed,
        surplus=surplus,
        inventory_used=inventory_used,
        facilities=facilities,
        throughput=throughput,
    )
