"""Standalone tests: the logistics package must work with zero domain (Foxhole) code,
and the parameterized hooks (produced_only, effort_weights, vehicle specs) must drive
behavior that used to be hard-coded."""

import numpy as np
from logistics import (
    LeontiefRequest,
    Limits,
    Objective,
    Recipe,
    RecipeMatrix,
    Target,
    solve_leontief,
    solve_lp,
    solve_milp,
    trip_constraint,
    trips_for_mixed_load,
    trips_needed,
)


def _matrix(items: list[str], recipes: list[Recipe]) -> RecipeMatrix:
    idx = {name: i for i, name in enumerate(items)}
    A = np.zeros((len(items), len(recipes)))
    for j, r in enumerate(recipes):
        for it, amt in r.outputs.items():
            A[idx[it], j] += amt
        for it, amt in r.inputs.items():
            A[idx[it], j] -= amt
    return RecipeMatrix(items=items, recipes=recipes, A=A)


def test_lp_basic_min_raw():
    m = _matrix(
        ["ore", "widget"],
        [Recipe(id="w#1@shop", source="shop", rank=1, outputs={"widget": 1}, inputs={"ore": 10})],
    )
    res = solve_lp(m, [Target(item="widget", quantity=5)])
    assert res.status == "optimal"
    assert res.runs["w#1@shop"] == 5.0
    assert abs(res.raw_used["ore"] - 50.0) < 1e-6


def test_effort_weights_drive_min_mining_time():
    # Two routes to a widget; effort_weights decides which raw is "cheaper".
    m = _matrix(
        ["a", "b", "widget"],
        [
            Recipe(id="w#1@s", source="s", rank=1, outputs={"widget": 1}, inputs={"a": 10}),
            Recipe(id="w#2@s", source="s", rank=2, outputs={"widget": 1}, inputs={"b": 10}),
        ],
    )
    # Make 'a' expensive in effort -> solver prefers 'b'.
    lim = Limits(effort_weights={"a": 5.0, "b": 0.1})
    res = solve_lp(
        m, [Target(item="widget", quantity=1)], objective=Objective.MIN_MINING_TIME, limits=lim
    )
    assert res.status == "optimal"
    assert res.raw_used.get("b", 0) > 0
    assert res.raw_used.get("a", 0) < 1e-9


def test_produced_only_forbids_raw_intake():
    # 'energy' is only produced by a generator; produced_only forbids taking it raw.
    m = _matrix(
        ["fuel", "energy", "widget"],
        [
            Recipe(
                id="gen#1@plant",
                source="plant",
                rank=1,
                outputs={"energy": 100},
                inputs={"fuel": 1},
            ),
            Recipe(
                id="w#1@shop", source="shop", rank=1, outputs={"widget": 1}, inputs={"energy": 50}
            ),
        ],
    )
    lim = Limits(produced_only={"energy"})
    res = solve_lp(m, [Target(item="widget", quantity=1)], limits=lim)
    assert res.status == "optimal"
    # energy must be produced, never taken raw
    assert "energy" not in res.raw_used
    assert res.runs.get("gen#1@plant", 0) > 0


def test_milp_whole_runs():
    m = _matrix(
        ["ore", "widget"],
        [
            Recipe(
                id="w#1@shop",
                source="shop",
                rank=1,
                outputs={"widget": 3},
                inputs={"ore": 10},
                seconds=10.0,
            )
        ],
    )
    res = solve_milp(m, [Target(item="widget", quantity=5)])
    assert res.status == "optimal"
    # 5 widgets at 3/run -> need 2 whole runs
    assert res.runs["w#1@shop"] == 2.0


def test_hauling_spec_driven():
    assert trips_needed({"ore": 2000.0}, slots=20, stack=100) == 1
    assert trips_needed({"ore": 2001.0}, slots=20, stack=100) == 2
    assert trips_needed({}, slots=20, stack=100) == 0
    c = trip_constraint(5.0, slots=20, stack=100, hauled_items={"ore", "coal"})
    assert c.upper == 5.0
    assert c.coeffs["ore"] == 1.0 / (100 * 20)
    plan = trips_for_mixed_load({"ore": 2500.0}, slots=20, stack=100)
    assert sum(t["ore"] for t in plan) == 25  # ceil(2500/100) slots


def test_leontief_balance():
    # 1 unit of B requires 0.5 of A; demand 10 B.
    req = LeontiefRequest(
        items=["A", "B"],
        coefficients_matrix=[[0.0, 0.5], [0.0, 0.0]],
        external_demand={"B": 10.0},
    )
    out = solve_leontief(req)
    assert out["gross_production_rate"]["B"] == 10.0
    assert out["gross_production_rate"]["A"] == 5.0
