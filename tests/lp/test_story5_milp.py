"""Tests for story-5: MILP production planner (scipy.optimize.milp)."""

from __future__ import annotations

import numpy as np
import pytest

from foxhole.lp.integer import solve_milp
from foxhole.lp.model import Recipe, RecipeMatrix, Target

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _recipe(
    name: str,
    source: str,
    rank: int,
    outputs: dict[str, float],
    inputs: dict[str, float],
    seconds: float | None = None,
    *,
    crate_output: bool = False,
    crate_size: int | None = None,
) -> Recipe:
    """Build a Recipe with id "<name>#<rank>@<source>"."""
    return Recipe(
        id=f"{name}#{rank}@{source}",
        source=source,
        rank=rank,
        outputs=outputs,
        inputs=inputs,
        seconds=seconds,
        crate_output=crate_output,
        crate_size=crate_size,
    )


def _matrix(items: list[str], recipes: list[Recipe]) -> RecipeMatrix:
    """Build RecipeMatrix from explicit items + recipes, computing A from outputs/inputs."""
    item_idx = {name: i for i, name in enumerate(items)}
    n_i, n_r = len(items), len(recipes)
    A = np.zeros((n_i, n_r), dtype=np.float64)
    for j, rec in enumerate(recipes):
        for item, qty in rec.outputs.items():
            A[item_idx[item], j] += qty
        for item, qty in rec.inputs.items():
            A[item_idx[item], j] -= qty
    return RecipeMatrix(items=items, recipes=recipes, A=A)


# ---------------------------------------------------------------------------
# Test 1: Whole runs round crate recipes up
# ---------------------------------------------------------------------------


def test_whole_runs_crate_rounding() -> None:
    """Soldier Supplies: target 15, crate=10, so 2 runs -> 20 units, surplus 5.

    From wiki: Factory recipe, IsCrateOutput=1, CrateCapacity=10, OutputAmount=1,
    ProductionTime=80s, InputItem1=Basic Materials 80.
    Per run: produces 10 Soldier Supplies, consumes 80 Basic Materials.
    """
    items = ["Basic Materials", "Soldier Supplies"]
    recipes = [
        _recipe(
            "Soldier Supplies",
            "Factory",
            1,
            outputs={"Soldier Supplies": 10},
            inputs={"Basic Materials": 80},
            seconds=80.0,
            crate_output=True,
            crate_size=10,
        )
    ]
    mat = _matrix(items, recipes)

    result = solve_milp(
        mat,
        [Target(item="Soldier Supplies", quantity=15)],
        whole_runs=True,
    )

    assert result.status == "optimal"
    recipe_id = "Soldier Supplies#1@Factory"
    assert recipe_id in result.runs
    assert result.runs[recipe_id] == 2.0
    # 2 runs * 10 units = 20 produced, surplus = 20 - 15 = 5
    assert result.surplus.get("Soldier Supplies", 0.0) == pytest.approx(5.0)
    # raw intake: 2 runs * 80 Bmats = 160
    assert result.raw_used.get("Basic Materials", 0.0) == pytest.approx(160.0)


# ---------------------------------------------------------------------------
# Test 2: Facility count (1085 Cmats → y = 8 Materials Factories)
# ---------------------------------------------------------------------------


def test_facility_count_construction_materials() -> None:
    """1085 Construction Materials at 25 s/run → needs 1085 runs.

    Total machine-time: 1085 * 25 = 27125 s.
    27125 / 3600 = 7.534... → ceil → 8 Materials Factories.

    From wiki: rank-1 recipe, IsCrateOutput=0, OutputAmount=1, ProductionTime=25s,
    InputItem1=Salvage 10.
    """
    items = ["Salvage", "Construction Materials"]
    recipes = [
        _recipe(
            "Construction Materials",
            "Materials Factory",
            1,
            outputs={"Construction Materials": 1},
            inputs={"Salvage": 10},
            seconds=25.0,
        )
    ]
    mat = _matrix(items, recipes)

    result = solve_milp(
        mat,
        [Target(item="Construction Materials", quantity=1085)],
        time_window_seconds=3600.0,
        whole_runs=True,
    )

    assert result.status == "optimal"
    assert result.runs.get("Construction Materials#1@Materials Factory", 0.0) == pytest.approx(
        1085.0
    )
    # 1085 runs * 25 s = 27125 s total; 27125/3600 = 7.534... → y = 8
    assert result.facilities.get("Materials Factory", 0.0) == pytest.approx(8.0)
    assert result.raw_used.get("Salvage", 0.0) == pytest.approx(10850.0)


# ---------------------------------------------------------------------------
# Test 3: max_facilities makes 1-hour target infeasible, 4-hour feasible
# ---------------------------------------------------------------------------


def test_max_facilities_infeasible_then_feasible() -> None:
    """max_facilities={"Materials Factory": 2} with 1085-Cmat target.

    1-hour window: 25*x <= 2*3600=7200 → x <= 288, but need x=1085 → infeasible.
    4-hour window: 25*x <= 2*14400=28800 → x <= 1152 ≥ 1085 → feasible, y=2.
    """
    items = ["Salvage", "Construction Materials"]
    recipes = [
        _recipe(
            "Construction Materials",
            "Materials Factory",
            1,
            outputs={"Construction Materials": 1},
            inputs={"Salvage": 10},
            seconds=25.0,
        )
    ]
    mat = _matrix(items, recipes)

    # 1-hour window with 2-machine cap → infeasible
    result_1h = solve_milp(
        mat,
        [Target(item="Construction Materials", quantity=1085)],
        time_window_seconds=3600.0,
        whole_runs=True,
        max_facilities={"Materials Factory": 2},
    )
    assert result_1h.status == "infeasible"

    # 4-hour window with 2-machine cap → feasible, y=2
    result_4h = solve_milp(
        mat,
        [Target(item="Construction Materials", quantity=1085)],
        time_window_seconds=14400.0,
        whole_runs=True,
        max_facilities={"Materials Factory": 2},
    )
    assert result_4h.status == "optimal"
    assert result_4h.facilities.get("Materials Factory", 0.0) == pytest.approx(2.0)
    assert result_4h.runs.get("Construction Materials#1@Materials Factory", 0.0) == pytest.approx(
        1085.0
    )


# ---------------------------------------------------------------------------
# Test 4: facility_weight trades raw for fewer machines
# ---------------------------------------------------------------------------


def test_facility_weight_prefers_fewer_machines() -> None:
    """Two Cmat recipes; facility_weight shifts optimizer from rank-1 to rank-2.

    Rank-1 (wiki rank=1): 10 Salvage → 1 Cmat, 25s
    Rank-2 (wiki rank=2): 15 Salvage + 25 Petrol → 3 Cmat, 25s

    Target: 300 Cmat, time_window=60s.

    Rank-1 plan: 300 runs, Salvage=3000, machine-time=7500s → y=125 machines.
    Rank-2 plan: 100 runs, Salvage=1500+Petrol=2500, machine-time=2500s → y=42 machines.

    facility_weight=0: raw cost R1=3000 < R2=4000 → rank-1 wins.
    facility_weight=100: R1=3000+125*100=15500 vs R2=4000+42*100=8200 → rank-2 wins.
    """
    items = ["Salvage", "Petrol", "Construction Materials"]
    recipe_r1 = _recipe(
        "Construction Materials",
        "Materials Factory",
        1,
        outputs={"Construction Materials": 1},
        inputs={"Salvage": 10},
        seconds=25.0,
    )
    recipe_r2 = _recipe(
        "Construction Materials",
        "Materials Factory",
        2,
        outputs={"Construction Materials": 3},
        inputs={"Salvage": 15, "Petrol": 25},
        seconds=25.0,
    )
    mat = _matrix(items, [recipe_r1, recipe_r2])

    # facility_weight=0: minimize raw only → rank-1 preferred (3000 < 4000)
    result_no_fw = solve_milp(
        mat,
        [Target(item="Construction Materials", quantity=300)],
        time_window_seconds=60.0,
        whole_runs=True,
        facility_weight=0.0,
    )
    assert result_no_fw.status == "optimal"
    # rank-1 used: 300 runs, rank-2 unused
    assert result_no_fw.runs.get(
        "Construction Materials#1@Materials Factory", 0.0
    ) == pytest.approx(300.0)
    assert result_no_fw.runs.get(
        "Construction Materials#2@Materials Factory", 0.0
    ) == pytest.approx(0.0, abs=1e-6)
    assert result_no_fw.facilities.get("Materials Factory", 0.0) == pytest.approx(125.0)

    # facility_weight=100: penalize machines → rank-2 preferred (8200 < 15500)
    result_fw = solve_milp(
        mat,
        [Target(item="Construction Materials", quantity=300)],
        time_window_seconds=60.0,
        whole_runs=True,
        facility_weight=100.0,
    )
    assert result_fw.status == "optimal"
    # rank-2 used: 100 runs
    assert result_fw.runs.get("Construction Materials#2@Materials Factory", 0.0) == pytest.approx(
        100.0
    )
    assert result_fw.runs.get("Construction Materials#1@Materials Factory", 0.0) == pytest.approx(
        0.0, abs=1e-6
    )
    assert result_fw.facilities.get("Materials Factory", 0.0) == pytest.approx(42.0)
