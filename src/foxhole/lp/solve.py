"""Story 3 — continuous LP core (scipy.optimize.linprog, HiGHS). OWNER: story-3."""

from __future__ import annotations

from collections import defaultdict

import numpy as np
from scipy.optimize import linprog

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

_EPSILON = 1e-8  # tie-break weight for MIN_MINING_TIME so all raw items carry a tiny penalty
_ZERO_TOL = 1e-9  # values below this are treated as zero in results


def _active_recipe_mask(matrix: RecipeMatrix, limits: Limits) -> list[bool]:
    """True for each recipe that is allowed to run given the current limits."""
    item_idx = matrix.item_index
    allowed = limits.allowed_recipes
    denied = limits.denied_recipes
    banned = limits.banned_items

    mask: list[bool] = []
    for j, recipe in enumerate(matrix.recipes):
        if recipe.id in denied:
            mask.append(False)
            continue
        if allowed is not None and recipe.id not in allowed:
            mask.append(False)
            continue
        touches = any(
            item_idx.get(b) is not None and matrix.A[item_idx[b], j] != 0.0 for b in banned
        )
        mask.append(not touches)
    return mask


def _raw_item_indices(matrix: RecipeMatrix, limits: Limits) -> list[int]:
    """Indices (into matrix.items) of items eligible for raw intake.

    Excludes POWER (must be produced by a recipe) and banned items.
    """
    banned = limits.banned_items
    return [
        i
        for i, name in enumerate(matrix.items)
        if name in matrix.raw_items and name != POWER and name not in banned
    ]


def build_balance_constraints(
    matrix: RecipeMatrix,
    demand_vec: np.ndarray,
    inventory_vec: np.ndarray,
    raw_indices: list[int],
    active_cols: list[int],
    *,
    include_z: bool = False,
) -> tuple[np.ndarray, np.ndarray]:
    """Build (A_ub, b_ub) for the item-balance constraints.

    Variable layout: [x_active_0..J'-1, r_0..Iraw-1, (Z if include_z)]

    The balance requirement is: A[:,active] @ x_active + r + inventory >= demand
    Rewritten for linprog (A_ub @ vars <= b_ub):
        -A[:,active] @ x_active - r <= inventory - demand   (when include_z=False)
        -A[:,active] @ x_active - r + demand * Z <= inventory  (when include_z=True)

    Returns A_ub (n_items, n_vars) and b_ub (n_items,).
    """
    n_items = len(matrix.items)
    J_active = len(active_cols)
    Iraw = len(raw_indices)
    n_vars = J_active + Iraw + (1 if include_z else 0)

    A_ub = np.zeros((n_items, n_vars), dtype=float)
    for col, j in enumerate(active_cols):
        A_ub[:, col] = -matrix.A[:, j]
    for k, raw_i in enumerate(raw_indices):
        A_ub[raw_i, J_active + k] = -1.0
    if include_z:
        A_ub[:, -1] = demand_vec  # moves demand * Z to the LHS
        b_ub = inventory_vec.copy()
    else:
        b_ub = inventory_vec - demand_vec
    return A_ub, b_ub


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
    if limits is None:
        limits = Limits()

    item_idx = matrix.item_index
    n_items = len(matrix.items)

    # Validate target items
    for t in targets:
        if t.item not in item_idx:
            raise ValueError(f"Target item {t.item!r} not in matrix.items")

    # Build demand vector
    demand_vec = np.zeros(n_items, dtype=float)
    for t in targets:
        demand_vec[item_idx[t.item]] += t.quantity

    # Inventory (QUANTITY mode only)
    inventory_vec = np.zeros(n_items, dtype=float)
    if mode == Mode.QUANTITY and inventory:
        for name, qty in inventory.items():
            if name in item_idx and qty > 0:
                inventory_vec[item_idx[name]] += float(qty)

    # Recipe filtering
    active_mask = _active_recipe_mask(matrix, limits)
    active_cols = [j for j, ok in enumerate(active_mask) if ok]
    J_active = len(active_cols)

    # Raw intake variables
    raw_indices = _raw_item_indices(matrix, limits)
    raw_item_names = [matrix.items[i] for i in raw_indices]
    Iraw = len(raw_indices)

    is_max_tp = objective == Objective.MAX_THROUGHPUT
    n_vars = J_active + Iraw + (1 if is_max_tp else 0)

    # Early-exit: no variables means no production is possible.
    if n_vars == 0:
        net_available = inventory_vec - demand_vec
        if np.any(net_available < -_ZERO_TOL):
            return LPResult(status="infeasible", objective=str(objective), mode=str(mode))
        return LPResult(
            status="optimal",
            objective=str(objective),
            objective_value=0.0,
            mode=str(mode),
            inventory_used={
                matrix.items[i]: float(min(inventory_vec[i], demand_vec[i]))
                for i in range(n_items)
                if demand_vec[i] > _ZERO_TOL
            },
        )

    # ---- Variable bounds ----
    bounds: list[tuple[float, float | None]] = []
    for _ in active_cols:
        bounds.append((0.0, None))
    for name in raw_item_names:
        ub = limits.max_raw.get(name)
        bounds.append((0.0, ub))
    if is_max_tp:
        bounds.append((0.0, None))  # Z >= 0

    # ---- Objective vector ----
    c = np.zeros(n_vars, dtype=float)
    if objective == Objective.MIN_RAW:
        for k, name in enumerate(raw_item_names):
            c[J_active + k] = limits.raw_weights.get(name, 1.0)
    elif objective == Objective.MIN_MINING_TIME:
        for k, name in enumerate(raw_item_names):
            c[J_active + k] = MINING_SECONDS_PER_UNIT.get(name, 0.0) + _EPSILON
    else:  # MAX_THROUGHPUT
        c[-1] = -1.0  # maximise Z = minimise -Z

    # ---- Balance constraints ----
    A_ub, b_ub = build_balance_constraints(
        matrix,
        demand_vec,
        inventory_vec,
        raw_indices,
        active_cols,
        include_z=is_max_tp,
    )

    # Extra linear caps on raw intake
    raw_name_to_k = {name: k for k, name in enumerate(raw_item_names)}
    for extra in limits.extra:
        row = np.zeros(n_vars, dtype=float)
        for item_name, coeff in extra.coeffs.items():
            k = raw_name_to_k.get(item_name)
            if k is not None:
                row[J_active + k] = coeff
        A_ub = np.vstack([A_ub, row[np.newaxis, :]])
        b_ub = np.append(b_ub, extra.upper)

    # ---- Solve ----
    result = linprog(c, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method="highs")

    # ---- Status handling ----
    if result.status == 2:
        return LPResult(status="infeasible", objective=str(objective), mode=str(mode))
    if result.status == 3:
        return LPResult(status="unbounded", objective=str(objective), mode=str(mode))
    if result.status not in (0, 1):
        return LPResult(status=result.message, objective=str(objective), mode=str(mode))

    x_sol = result.x

    # Extract recipe run counts (full J-length, zeros for inactive)
    x_runs = np.zeros(len(matrix.recipes), dtype=float)
    for col, j in enumerate(active_cols):
        x_runs[j] = max(0.0, x_sol[col])

    # Extract raw intake per raw item
    r_raw = np.array([max(0.0, x_sol[J_active + k]) for k in range(Iraw)], dtype=float)

    # Z throughput multiplier
    Z = float(x_sol[-1]) if is_max_tp else None

    # ---- Item flows ----
    net_recipe = matrix.A @ x_runs  # net per item: positive = produced, negative = consumed

    produced_vec = np.zeros(n_items, dtype=float)
    consumed_vec = np.zeros(n_items, dtype=float)
    for idx in range(n_items):
        for j in range(len(matrix.recipes)):
            contrib = matrix.A[idx, j] * x_runs[j]
            if contrib > 0:
                produced_vec[idx] += contrib
            elif contrib < 0:
                consumed_vec[idx] -= contrib

    raw_used_vec = np.zeros(n_items, dtype=float)
    for k, raw_i in enumerate(raw_indices):
        raw_used_vec[raw_i] = r_raw[k]

    # Inventory used: amount drawn from stock to cover the gap between recipe+raw and demand
    inv_used_vec = np.zeros(n_items, dtype=float)
    if mode == Mode.QUANTITY:
        for idx in range(n_items):
            gap = demand_vec[idx] - (net_recipe[idx] + raw_used_vec[idx])
            if gap > _ZERO_TOL:
                inv_used_vec[idx] = min(inventory_vec[idx], gap)

    # Surplus: production beyond what targets require
    surplus_vec = np.maximum(
        0.0,
        net_recipe + raw_used_vec + inv_used_vec - demand_vec,
    )

    # ---- Build result dicts ----
    runs_dict = {
        recipe.id: float(x_runs[j])
        for j, recipe in enumerate(matrix.recipes)
        if x_runs[j] > _ZERO_TOL
    }
    raw_used_dict = {
        raw_item_names[k]: float(r_raw[k]) for k in range(Iraw) if r_raw[k] > _ZERO_TOL
    }
    produced_dict = {
        matrix.items[idx]: float(produced_vec[idx])
        for idx in range(n_items)
        if produced_vec[idx] > _ZERO_TOL
    }
    consumed_dict = {
        matrix.items[idx]: float(consumed_vec[idx])
        for idx in range(n_items)
        if consumed_vec[idx] > _ZERO_TOL
    }
    surplus_dict = {
        matrix.items[idx]: float(surplus_vec[idx])
        for idx in range(n_items)
        if surplus_vec[idx] > _ZERO_TOL
    }
    inventory_used_dict = {
        matrix.items[idx]: float(inv_used_vec[idx])
        for idx in range(n_items)
        if inv_used_vec[idx] > _ZERO_TOL
    }

    # Facilities: fractional machine count per source
    facilities: dict[str, float] = defaultdict(float)
    for j, recipe in enumerate(matrix.recipes):
        if x_runs[j] > _ZERO_TOL and recipe.seconds is not None:
            facilities[recipe.source] += x_runs[j] * recipe.seconds / time_window_seconds

    # Power draw (rate mode only)
    power_mw: float | None = None
    if mode == Mode.RATE:
        total = sum(
            x_runs[j] * recipe.power_mw * (recipe.seconds or 0.0) / time_window_seconds
            for j, recipe in enumerate(matrix.recipes)
            if x_runs[j] > _ZERO_TOL
        )
        if total > _ZERO_TOL:
            power_mw = total

    # Objective value (negated back for MAX_THROUGHPUT)
    if is_max_tp:
        obj_val = float(Z) if Z is not None else None
    else:
        obj_val = float(result.fun)

    return LPResult(
        status="optimal",
        objective=str(objective),
        objective_value=obj_val,
        mode=str(mode),
        runs=runs_dict,
        raw_used=raw_used_dict,
        produced=produced_dict,
        consumed=consumed_dict,
        surplus=surplus_dict,
        inventory_used=inventory_used_dict,
        throughput=Z,
        facilities=dict(facilities),
        power_mw=power_mw,
    )
