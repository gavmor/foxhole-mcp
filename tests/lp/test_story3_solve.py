"""Story 3 tests — continuous LP core (solve_lp).

All RecipeMatrix objects are built by hand (no build_recipe_matrix dependency).
Numeric expected values are hand-checked against the problem setup.
"""

from __future__ import annotations

import numpy as np
import pytest

from foxhole.economy import CurriedEconomySolver
from foxhole.lp.model import (
    MINING_SECONDS_PER_UNIT,
    Limits,
    Mode,
    Objective,
    Recipe,
    RecipeMatrix,
    Target,
)
from foxhole.lp.solve import (
    _active_recipe_mask,
    _raw_item_indices,
    build_balance_constraints,
    solve_lp,
)

# ---------------------------------------------------------------------------
# Shared small matrix helpers
# ---------------------------------------------------------------------------


def _make_recipe(
    item_id: str,
    outputs: dict[str, float],
    inputs: dict[str, float],
    source: str = "Factory",
    seconds: float | None = 60.0,
    rank: int = 1,
) -> Recipe:
    return Recipe(
        id=f"{item_id}#{rank}@{source}",
        source=source,
        rank=rank,
        outputs=outputs,
        inputs=inputs,
        seconds=seconds,
    )


def _matrix_from_recipes(items: list[str], recipes: list[Recipe]) -> RecipeMatrix:
    """Build a RecipeMatrix from explicit items and Recipe objects."""
    item_idx = {name: i for i, name in enumerate(items)}
    n_items = len(items)
    n_recipes = len(recipes)
    A = np.zeros((n_items, n_recipes), dtype=float)
    for j, r in enumerate(recipes):
        for name, qty in r.outputs.items():
            if name in item_idx:
                A[item_idx[name], j] += qty
        for name, qty in r.inputs.items():
            if name in item_idx:
                A[item_idx[name], j] -= qty
    return RecipeMatrix(items=items, recipes=recipes, A=A)


# ---------------------------------------------------------------------------
# (a) ORACLE — LP raw intake matches Leontief for a chain with one recipe per item
# ---------------------------------------------------------------------------


class TestOracle:
    """
    Chain: Salvage -> Basic Materials (BM) -> Soldier Supplies (SS)
    Economy.py ratios:
      BM: 2 Salvage per 1 BM  (Refinery)
      SS: 8 BM per 1 SS       (Factory)
    Target: 10 SS
    Expected: 80 BM runs, 160 Salvage raw.
    """

    @pytest.fixture
    def ss_matrix(self) -> RecipeMatrix:
        items = ["Salvage", "Basic Materials", "Soldier Supplies"]
        r_bm = _make_recipe(
            "Basic Materials",
            outputs={"Basic Materials": 1},
            inputs={"Salvage": 2},
            source="Refinery",
            seconds=0.48,
        )
        r_ss = _make_recipe(
            "Soldier Supplies",
            outputs={"Soldier Supplies": 1},
            inputs={"Basic Materials": 8},
            source="Factory",
            seconds=4.0,
        )
        return _matrix_from_recipes(items, [r_bm, r_ss])

    def test_raw_matches_leontief(self, ss_matrix: RecipeMatrix) -> None:
        result = solve_lp(ss_matrix, [Target(item="Soldier Supplies", quantity=10)])
        assert result.status == "optimal"

        # Compare with Leontief solver over equivalent registry
        from foxhole.economy import ItemCategory, ItemDefinition

        registry = {
            "Salvage": ItemDefinition(name="Salvage", category=ItemCategory.RAW_RESOURCE),
            "Basic Materials": ItemDefinition(
                name="Basic Materials",
                category=ItemCategory.REFINED_MATERIAL,
                inputs={"Salvage": 2.0},
                facility_type="Refinery",
                crafting_time_sec=0.48,
            ),
            "Soldier Supplies": ItemDefinition(
                name="Soldier Supplies",
                category=ItemCategory.SMALL_ARMS,
                inputs={"Basic Materials": 8.0},
                facility_type="Factory",
                crafting_time_sec=4.0,
            ),
        }
        solver = CurriedEconomySolver(registry=registry, synonyms={})
        plan = solver.solve({"Soldier Supplies": 10})

        lp_salvage = result.raw_used.get("Salvage", 0.0)
        leontief_salvage = plan.raw_resources.get("Salvage", 0.0)
        assert abs(lp_salvage - leontief_salvage) < 1e-6, (
            f"LP Salvage={lp_salvage}, Leontief={leontief_salvage}"
        )

    def test_oracle_exact_values(self, ss_matrix: RecipeMatrix) -> None:
        result = solve_lp(ss_matrix, [Target(item="Soldier Supplies", quantity=10)])
        assert result.status == "optimal"
        # 10 SS x 8 BM/SS = 80 BM runs
        assert abs(result.raw_used.get("Salvage", 0) - 160.0) < 1e-6
        # BM recipe runs = 80
        bm_id = "Basic Materials#1@Refinery"
        assert abs(result.runs.get(bm_id, 0) - 80.0) < 1e-6
        # SS recipe runs = 10
        ss_id = "Soldier Supplies#1@Factory"
        assert abs(result.runs.get(ss_id, 0) - 10.0) < 1e-6
        # Objective value = 160 (Salvage with weight 1)
        assert abs((result.objective_value or 0) - 160.0) < 1e-6


# ---------------------------------------------------------------------------
# (b) Alternatives — Gravel from Coal (5:1) vs Salvage (6:1)
# ---------------------------------------------------------------------------


class TestAlternatives:
    """
    Gravel recipe A: 5 Coal  -> 1 Gravel
    Gravel recipe B: 6 Salvage -> 1 Gravel
    MIN_RAW weight = 1 for both, so Coal is cheaper (5 < 6).
    """

    @pytest.fixture
    def gravel_matrix(self) -> RecipeMatrix:
        items = ["Coal", "Salvage", "Gravel"]
        r_coal = _make_recipe(
            "Gravel",
            outputs={"Gravel": 1},
            inputs={"Coal": 5},
            source="Crusher",
            seconds=30.0,
            rank=1,
        )
        r_salv = _make_recipe(
            "Gravel",
            outputs={"Gravel": 1},
            inputs={"Salvage": 6},
            source="Crusher",
            seconds=30.0,
            rank=2,
        )
        return _matrix_from_recipes(items, [r_coal, r_salv])

    def test_prefers_coal_by_default(self, gravel_matrix: RecipeMatrix) -> None:
        result = solve_lp(gravel_matrix, [Target(item="Gravel", quantity=1)])
        assert result.status == "optimal"
        # Coal route: 5 raw units vs Salvage route: 6 raw units
        assert abs(result.raw_used.get("Coal", 0) - 5.0) < 1e-6
        assert result.raw_used.get("Salvage", 0) < _ZERO_TOL

    def test_banned_coal_uses_salvage(self, gravel_matrix: RecipeMatrix) -> None:
        limits = Limits(banned_items={"Coal"})
        result = solve_lp(gravel_matrix, [Target(item="Gravel", quantity=1)], limits=limits)
        assert result.status == "optimal"
        assert abs(result.raw_used.get("Salvage", 0) - 6.0) < 1e-6
        assert result.raw_used.get("Coal", 0) < _ZERO_TOL

    def test_max_raw_zero_coal_uses_salvage(self, gravel_matrix: RecipeMatrix) -> None:
        limits = Limits(max_raw={"Coal": 0.0})
        result = solve_lp(gravel_matrix, [Target(item="Gravel", quantity=1)], limits=limits)
        assert result.status == "optimal"
        assert abs(result.raw_used.get("Salvage", 0) - 6.0) < 1e-6
        assert result.raw_used.get("Coal", 0) < _ZERO_TOL


_ZERO_TOL = 1e-9


# ---------------------------------------------------------------------------
# (c) Byproduct reduces raw use
# ---------------------------------------------------------------------------


class TestByproduct:
    """
    Recipe R: 4 Salvage -> 1 Widget (primary) + 1 Gadget (byproduct)
    Targets: Widget=5, Gadget=5
    Running 5 of R produces 5 Widget AND 5 Gadget (byproduct covers Gadget demand).
    Expected: r_Salvage = 20 (not 20+something for Gadget separately).
    """

    @pytest.fixture
    def byproduct_matrix(self) -> RecipeMatrix:
        items = ["Salvage", "Widget", "Gadget"]
        r = _make_recipe(
            "Widget",
            outputs={"Widget": 1, "Gadget": 1},
            inputs={"Salvage": 4},
            source="Mill",
            seconds=20.0,
        )
        return _matrix_from_recipes(items, [r])

    def test_byproduct_satisfies_target(self, byproduct_matrix: RecipeMatrix) -> None:
        targets = [Target(item="Widget", quantity=5), Target(item="Gadget", quantity=5)]
        result = solve_lp(byproduct_matrix, targets)
        assert result.status == "optimal"
        # 5 runs x 4 Salvage/run = 20 Salvage
        assert abs(result.raw_used.get("Salvage", 0) - 20.0) < 1e-6
        # No surplus (byproduct exactly meets Gadget demand)
        assert result.surplus.get("Gadget", 0) < 1e-6
        assert result.surplus.get("Widget", 0) < 1e-6

    def test_byproduct_surplus_when_excess(self, byproduct_matrix: RecipeMatrix) -> None:
        """When Widget target > Gadget target, excess Gadget appears as surplus."""
        targets = [Target(item="Widget", quantity=5), Target(item="Gadget", quantity=2)]
        result = solve_lp(byproduct_matrix, targets)
        assert result.status == "optimal"
        # Still need 5 runs for Widget; produces 5 Gadget, demand 2 -> surplus 3
        assert abs(result.raw_used.get("Salvage", 0) - 20.0) < 1e-6
        assert abs(result.surplus.get("Gadget", 0) - 3.0) < 1e-6


# ---------------------------------------------------------------------------
# (d) Inventory nets demand
# ---------------------------------------------------------------------------


class TestInventory:
    """
    10 SS target, 5 SS in inventory -> only 5 SS need to be produced.
    LP should use inventory to halve the Salvage requirement.
    """

    @pytest.fixture
    def ss_matrix(self) -> RecipeMatrix:
        items = ["Salvage", "Basic Materials", "Soldier Supplies"]
        r_bm = _make_recipe(
            "Basic Materials",
            outputs={"Basic Materials": 1},
            inputs={"Salvage": 2},
            source="Refinery",
            seconds=0.48,
        )
        r_ss = _make_recipe(
            "Soldier Supplies",
            outputs={"Soldier Supplies": 1},
            inputs={"Basic Materials": 8},
            source="Factory",
            seconds=4.0,
        )
        return _matrix_from_recipes(items, [r_bm, r_ss])

    def test_inventory_reduces_production(self, ss_matrix: RecipeMatrix) -> None:
        result = solve_lp(
            ss_matrix,
            [Target(item="Soldier Supplies", quantity=10)],
            inventory={"Soldier Supplies": 5},
        )
        assert result.status == "optimal"
        # Only 5 SS need to be produced (inventory covers other 5)
        # 5 SS x 8 BM x 2 Salvage = 80 Salvage
        assert abs(result.raw_used.get("Salvage", 0) - 80.0) < 1e-6

    def test_inventory_used_field(self, ss_matrix: RecipeMatrix) -> None:
        result = solve_lp(
            ss_matrix,
            [Target(item="Soldier Supplies", quantity=10)],
            inventory={"Soldier Supplies": 5},
        )
        assert result.status == "optimal"
        assert abs(result.inventory_used.get("Soldier Supplies", 0) - 5.0) < 1e-6

    def test_full_inventory_no_raw(self, ss_matrix: RecipeMatrix) -> None:
        """When inventory exactly covers target, no raw resources needed."""
        result = solve_lp(
            ss_matrix,
            [Target(item="Soldier Supplies", quantity=10)],
            inventory={"Soldier Supplies": 10},
        )
        assert result.status == "optimal"
        assert not result.raw_used
        assert abs(result.inventory_used.get("Soldier Supplies", 0) - 10.0) < 1e-6


# ---------------------------------------------------------------------------
# (e) Infeasible — target needs banned item
# ---------------------------------------------------------------------------


class TestInfeasible:
    """
    Only recipe for Widget consumes banned item Coal.
    Widget is not raw -> no raw intake variable for Widget.
    Constraint: Widget production >= 1 cannot be satisfied.
    """

    @pytest.fixture
    def widget_matrix(self) -> RecipeMatrix:
        items = ["Coal", "Widget"]
        r = _make_recipe(
            "Widget",
            outputs={"Widget": 1},
            inputs={"Coal": 3},
            source="Factory",
            seconds=10.0,
        )
        return _matrix_from_recipes(items, [r])

    def test_banned_input_infeasible(self, widget_matrix: RecipeMatrix) -> None:
        limits = Limits(banned_items={"Coal"})
        result = solve_lp(widget_matrix, [Target(item="Widget", quantity=1)], limits=limits)
        assert result.status == "infeasible"
        # Result fields are empty on infeasible
        assert not result.runs
        assert not result.raw_used

    def test_no_recipe_no_raw_infeasible(self) -> None:
        """An item that is not raw and has no recipe producing it is infeasible."""
        items = ["Ore", "Metal"]
        # Only recipe: Metal -> produces Ore (reverse direction, weird)
        r = _make_recipe("Metal", outputs={"Metal": 1}, inputs={"Ore": 2}, source="Mill")
        m = _matrix_from_recipes(items, [r])
        # Target: Ore, but Ore has no producer recipe and no raw intake route here
        # Actually Ore IS in raw_items (no recipe produces Ore in this matrix)
        # Let's target Metal with Ore banned
        limits = Limits(banned_items={"Ore"})
        result = solve_lp(m, [Target(item="Metal", quantity=1)], limits=limits)
        assert result.status == "infeasible"


# ---------------------------------------------------------------------------
# (f) MAX_THROUGHPUT in RATE mode with a max_raw cap
# ---------------------------------------------------------------------------


class TestMaxThroughput:
    """
    Recipe: 6 Salvage -> 1 Gravel (per run)
    max_raw: Salvage = 600 (per time window)
    Target bundle: 1 Gravel
    MAX Z = 600 / 6 = 100 (100 bundles of 1 Gravel per time window)
    """

    @pytest.fixture
    def gravel_matrix(self) -> RecipeMatrix:
        items = ["Salvage", "Gravel"]
        r = _make_recipe(
            "Gravel",
            outputs={"Gravel": 1},
            inputs={"Salvage": 6},
            source="Crusher",
            seconds=30.0,
        )
        return _matrix_from_recipes(items, [r])

    def test_max_throughput_z_value(self, gravel_matrix: RecipeMatrix) -> None:
        limits = Limits(max_raw={"Salvage": 600.0})
        result = solve_lp(
            gravel_matrix,
            [Target(item="Gravel", quantity=1)],
            objective=Objective.MAX_THROUGHPUT,
            mode=Mode.RATE,
            limits=limits,
        )
        assert result.status == "optimal"
        assert result.throughput is not None
        # 600 Salvage / 6 per Gravel = 100 Gravel bundles
        assert abs(result.throughput - 100.0) < 1e-6

    def test_max_throughput_objective_value(self, gravel_matrix: RecipeMatrix) -> None:
        limits = Limits(max_raw={"Salvage": 600.0})
        result = solve_lp(
            gravel_matrix,
            [Target(item="Gravel", quantity=1)],
            objective=Objective.MAX_THROUGHPUT,
            mode=Mode.RATE,
            limits=limits,
        )
        assert result.status == "optimal"
        assert result.objective_value is not None
        assert abs(result.objective_value - 100.0) < 1e-6

    def test_max_throughput_uses_all_raw(self, gravel_matrix: RecipeMatrix) -> None:
        """At Z=100, should use exactly 600 Salvage."""
        limits = Limits(max_raw={"Salvage": 600.0})
        result = solve_lp(
            gravel_matrix,
            [Target(item="Gravel", quantity=1)],
            objective=Objective.MAX_THROUGHPUT,
            mode=Mode.RATE,
            limits=limits,
        )
        assert result.status == "optimal"
        assert abs(result.raw_used.get("Salvage", 0) - 600.0) < 1e-6

    def test_max_throughput_two_caps(self, gravel_matrix: RecipeMatrix) -> None:
        """Tighter cap limits Z further."""
        limits = Limits(max_raw={"Salvage": 300.0})
        result = solve_lp(
            gravel_matrix,
            [Target(item="Gravel", quantity=1)],
            objective=Objective.MAX_THROUGHPUT,
            mode=Mode.RATE,
            limits=limits,
        )
        assert result.status == "optimal"
        assert result.throughput is not None
        assert abs(result.throughput - 50.0) < 1e-6


# ---------------------------------------------------------------------------
# (g) MIN_MINING_TIME prefers Salvage over Components (lower seconds/unit)
# ---------------------------------------------------------------------------


class TestMinMiningTime:
    """
    MINING_SECONDS_PER_UNIT: Salvage = 1.1/5 = 0.22 s/unit, Components = 1.6/2 = 0.8 s/unit

    Two equivalent Widget recipes:
      A: 10 Salvage  -> 1 Widget   (mining time = 10 x 0.22 = 2.2 s)
      B:  5 Components -> 1 Widget (mining time =  5 x 0.80 = 4.0 s)

    MIN_MINING_TIME should pick recipe A (lower mining time).
    """

    @pytest.fixture
    def widget_matrix(self) -> RecipeMatrix:
        items = ["Salvage", "Components", "Widget"]
        r_salv = _make_recipe(
            "Widget",
            outputs={"Widget": 1},
            inputs={"Salvage": 10},
            source="Workshop",
            seconds=30.0,
            rank=1,
        )
        r_comp = _make_recipe(
            "Widget",
            outputs={"Widget": 1},
            inputs={"Components": 5},
            source="Workshop",
            seconds=30.0,
            rank=2,
        )
        return _matrix_from_recipes(items, [r_salv, r_comp])

    def test_prefers_salvage_route(self, widget_matrix: RecipeMatrix) -> None:
        result = solve_lp(
            widget_matrix,
            [Target(item="Widget", quantity=1)],
            objective=Objective.MIN_MINING_TIME,
        )
        assert result.status == "optimal"
        # Salvage route chosen: 10 Salvage used, 0 Components
        assert abs(result.raw_used.get("Salvage", 0) - 10.0) < 1e-6
        assert result.raw_used.get("Components", 0) < _ZERO_TOL

    def test_objective_is_mining_time(self, widget_matrix: RecipeMatrix) -> None:
        result = solve_lp(
            widget_matrix,
            [Target(item="Widget", quantity=1)],
            objective=Objective.MIN_MINING_TIME,
        )
        assert result.status == "optimal"
        # Objective = 10 x (0.22 + epsilon) ~= 2.2
        expected = 10 * (MINING_SECONDS_PER_UNIT["Salvage"] + 1e-8)
        assert abs((result.objective_value or 0) - expected) < 1e-5

    def test_banishing_salvage_falls_back_to_components(self, widget_matrix: RecipeMatrix) -> None:
        limits = Limits(banned_items={"Salvage"})
        result = solve_lp(
            widget_matrix,
            [Target(item="Widget", quantity=1)],
            objective=Objective.MIN_MINING_TIME,
            limits=limits,
        )
        assert result.status == "optimal"
        assert abs(result.raw_used.get("Components", 0) - 5.0) < 1e-6


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestHelpers:
    @pytest.fixture
    def simple_matrix(self) -> RecipeMatrix:
        items = ["Coal", "Salvage", "Gravel"]
        r_coal = _make_recipe("Gravel", outputs={"Gravel": 1}, inputs={"Coal": 5}, rank=1)
        r_salv = _make_recipe("Gravel", outputs={"Gravel": 1}, inputs={"Salvage": 6}, rank=2)
        return _matrix_from_recipes(items, [r_coal, r_salv])

    def test_active_recipe_mask_banned(self, simple_matrix: RecipeMatrix) -> None:
        limits = Limits(banned_items={"Coal"})
        mask = _active_recipe_mask(simple_matrix, limits)
        # Recipe 0 (uses Coal) is blocked; Recipe 1 (uses Salvage) is allowed
        assert mask == [False, True]

    def test_active_recipe_mask_denied(self, simple_matrix: RecipeMatrix) -> None:
        recipe_id = simple_matrix.recipes[0].id
        limits = Limits(denied_recipes={recipe_id})
        mask = _active_recipe_mask(simple_matrix, limits)
        assert mask == [False, True]

    def test_active_recipe_mask_allowed(self, simple_matrix: RecipeMatrix) -> None:
        recipe_id = simple_matrix.recipes[1].id
        limits = Limits(allowed_recipes={recipe_id})
        mask = _active_recipe_mask(simple_matrix, limits)
        assert mask == [False, True]

    def test_raw_item_indices(self, simple_matrix: RecipeMatrix) -> None:
        limits = Limits()
        raw_idx = _raw_item_indices(simple_matrix, limits)
        raw_names = [simple_matrix.items[i] for i in raw_idx]
        # Coal and Salvage are raw (no recipe produces them)
        assert set(raw_names) == {"Coal", "Salvage"}

    def test_raw_item_indices_excludes_banned(self, simple_matrix: RecipeMatrix) -> None:
        limits = Limits(banned_items={"Coal"})
        raw_idx = _raw_item_indices(simple_matrix, limits)
        raw_names = [simple_matrix.items[i] for i in raw_idx]
        assert "Coal" not in raw_names
        assert "Salvage" in raw_names

    def test_build_balance_constraints_shape(self, simple_matrix: RecipeMatrix) -> None:
        limits = Limits()
        active_cols = [0, 1]
        raw_indices = _raw_item_indices(simple_matrix, limits)
        n_items = len(simple_matrix.items)
        n_raw = len(raw_indices)
        n_active = len(active_cols)
        demand = np.zeros(n_items)
        inv = np.zeros(n_items)
        A_ub, b_ub = build_balance_constraints(simple_matrix, demand, inv, raw_indices, active_cols)
        assert A_ub.shape == (n_items, n_active + n_raw)
        assert b_ub.shape == (n_items,)

    def test_build_balance_constraints_z_mode(self, simple_matrix: RecipeMatrix) -> None:
        limits = Limits()
        active_cols = [0, 1]
        raw_indices = _raw_item_indices(simple_matrix, limits)
        n_items = len(simple_matrix.items)
        n_raw = len(raw_indices)
        n_active = len(active_cols)
        demand = np.zeros(n_items)
        demand[2] = 1.0  # Gravel = 1
        inv = np.zeros(n_items)
        A_ub, _b_ub = build_balance_constraints(
            simple_matrix, demand, inv, raw_indices, active_cols, include_z=True
        )
        # +1 for Z variable
        assert A_ub.shape == (n_items, n_active + n_raw + 1)
        # Z column equals demand_vec
        np.testing.assert_array_equal(A_ub[:, -1], demand)

    def test_unknown_target_raises(self, simple_matrix: RecipeMatrix) -> None:
        with pytest.raises(ValueError, match=r"not in matrix\.items"):
            solve_lp(simple_matrix, [Target(item="Unobtainium", quantity=1)])


# ---------------------------------------------------------------------------
# Facilities calculation test
# ---------------------------------------------------------------------------


class TestFacilities:
    def test_facilities_fractional(self) -> None:
        """facilities[source] = x_j * seconds_j / time_window_seconds."""
        items = ["Salvage", "BM"]
        r = _make_recipe(
            "BM",
            outputs={"BM": 1},
            inputs={"Salvage": 2},
            source="Refinery",
            seconds=3600.0,  # 1 hour per run -> 10 runs needs 10 machines
        )
        m = _matrix_from_recipes(items, [r])
        result = solve_lp(m, [Target(item="BM", quantity=10)], time_window_seconds=3600.0)
        assert result.status == "optimal"
        # 10 runs x 3600s / 3600s window = 10.0 fractional machines
        assert abs(result.facilities.get("Refinery", 0) - 10.0) < 1e-6
