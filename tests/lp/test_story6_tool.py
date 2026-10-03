"""Story 6 — tests for OptimizeTools.optimize_production.

All LP sub-stories (matrix, solve, integer, hauling) raise NotImplementedError and are
monkeypatched with fakes. Tests verify parameter mapping, error handling, output shape,
and stockpile / hauling plumbing.
"""

from __future__ import annotations

import numpy as np
import pytest

from foxhole.lp.model import (
    ExtraConstraint,
    LPResult,
    Mode,
    Objective,
    Recipe,
    RecipeMatrix,
)
from foxhole.tools.optimize import OptimizeTools

# ---------------------------------------------------------------------------
# Helpers: minimal fakes for the un-implemented stories
# ---------------------------------------------------------------------------


def _make_matrix(items=None, recipes=None):
    """Minimal 2x2 RecipeMatrix for Basic Materials produced from Salvage."""
    if items is None:
        items = ["Salvage", "Basic Materials"]
    if recipes is None:
        recipes = [
            Recipe(
                id="Basic Materials#1@Refinery",
                source="Refinery",
                rank=1,
                outputs={"Basic Materials": 50.0},
                inputs={"Salvage": 100.0},
                seconds=60.0,
                extraction=False,
            )
        ]
    n = len(items)
    m = len(recipes)
    A = np.zeros((n, m), dtype=float)
    idx = {name: i for i, name in enumerate(items)}
    for j, r in enumerate(recipes):
        for name, qty in r.outputs.items():
            if name in idx:
                A[idx[name], j] += qty
        for name, qty in r.inputs.items():
            if name in idx:
                A[idx[name], j] -= qty
    return RecipeMatrix(items=items, recipes=recipes, A=A)


def _make_result(
    status: str = "optimal",
    objective: str = "min_raw",
    objective_value: float | None = 100.0,
    mode: str = "quantity",
    runs: dict[str, float] | None = None,
    raw_used: dict[str, float] | None = None,
    produced: dict[str, float] | None = None,
    consumed: dict[str, float] | None = None,
    surplus: dict[str, float] | None = None,
    inventory_used: dict[str, float] | None = None,
) -> LPResult:
    return LPResult(
        status=status,
        objective=objective,
        objective_value=objective_value,
        mode=mode,
        runs=runs if runs is not None else {"Basic Materials#1@Refinery": 2.0},
        raw_used=raw_used if raw_used is not None else {"Salvage": 200.0},
        produced=produced if produced is not None else {"Basic Materials": 100.0},
        consumed=consumed if consumed is not None else {},
        surplus=surplus if surplus is not None else {},
        inventory_used=inventory_used if inventory_used is not None else {},
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tool():
    return OptimizeTools()


@pytest.fixture
def fake_rows(production_rows):
    return production_rows


@pytest.fixture
def patch_lp(monkeypatch, fake_rows):
    """Patch load_production, build_recipe_matrix, and solve_lp with fakes."""
    matrix = _make_matrix()
    result = _make_result()

    monkeypatch.setattr("foxhole.cargo.load_production", lambda *a, **kw: fake_rows)
    monkeypatch.setattr(
        "foxhole.lp.matrix.build_recipe_matrix",
        lambda rows, **kw: matrix,
    )
    monkeypatch.setattr(
        "foxhole.lp.solve.solve_lp",
        lambda matrix, targets, **kw: result,
    )
    return {"matrix": matrix, "result": result}


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_bad_objective_returns_error_dict(self, tool, monkeypatch, fake_rows):
        monkeypatch.setattr("foxhole.cargo.load_production", lambda *a, **kw: fake_rows)
        out = await tool.optimize_production({"Basic Materials": 100}, objective="explode")
        assert "error" in out
        assert "objective" in out["error"].lower() or "explode" in out["error"]

    @pytest.mark.asyncio
    async def test_bad_mode_returns_error_dict(self, tool, monkeypatch, fake_rows):
        monkeypatch.setattr("foxhole.cargo.load_production", lambda *a, **kw: fake_rows)
        out = await tool.optimize_production({"Basic Materials": 100}, mode="warp_speed")
        assert "error" in out
        assert "mode" in out["error"].lower() or "warp_speed" in out["error"]

    @pytest.mark.asyncio
    async def test_no_production_cache_returns_error(self, tool, monkeypatch):
        monkeypatch.setattr("foxhole.cargo.load_production", lambda *a, **kw: None)
        out = await tool.optimize_production({"Basic Materials": 100})
        assert "error" in out
        assert "cargo-sync" in out["error"] or "not cached" in out["error"].lower()

    @pytest.mark.asyncio
    async def test_error_dict_not_exception(self, tool, monkeypatch, fake_rows):
        """Bad inputs must return a dict with 'error', never raise."""
        monkeypatch.setattr("foxhole.cargo.load_production", lambda *a, **kw: fake_rows)
        out = await tool.optimize_production({"Basic Materials": 100}, objective="bad")
        assert isinstance(out, dict)
        assert "error" in out


# ---------------------------------------------------------------------------
# Parameter mapping
# ---------------------------------------------------------------------------


class TestParameterMapping:
    @pytest.mark.asyncio
    async def test_objective_passed_to_solve(self, tool, monkeypatch, fake_rows):
        captured = {}
        matrix = _make_matrix()

        monkeypatch.setattr("foxhole.cargo.load_production", lambda *a, **kw: fake_rows)
        monkeypatch.setattr("foxhole.lp.matrix.build_recipe_matrix", lambda *a, **kw: matrix)

        def fake_solve(m, targets, *, objective, mode, limits, inventory, time_window_seconds):
            captured["objective"] = objective
            captured["mode"] = mode
            captured["limits"] = limits
            return _make_result()

        monkeypatch.setattr("foxhole.lp.solve.solve_lp", fake_solve)

        await tool.optimize_production(
            {"Basic Materials": 50},
            objective="min_mining_time",
            mode="rate",
        )

        assert captured["objective"] == Objective.MIN_MINING_TIME
        assert captured["mode"] == Mode.RATE

    @pytest.mark.asyncio
    async def test_max_raw_and_banned_items_passed(self, tool, monkeypatch, fake_rows):
        captured = {}
        matrix = _make_matrix()
        monkeypatch.setattr("foxhole.cargo.load_production", lambda *a, **kw: fake_rows)
        monkeypatch.setattr("foxhole.lp.matrix.build_recipe_matrix", lambda *a, **kw: matrix)

        def fake_solve(m, targets, *, objective, mode, limits, inventory, time_window_seconds):
            captured["limits"] = limits
            return _make_result()

        monkeypatch.setattr("foxhole.lp.solve.solve_lp", fake_solve)

        await tool.optimize_production(
            {"Basic Materials": 50},
            max_raw={"Salvage": 200.0},
            banned_items=["Coal"],
        )

        lim = captured["limits"]
        assert lim.max_raw == {"Salvage": 200.0}
        assert "Coal" in lim.banned_items

    @pytest.mark.asyncio
    async def test_deny_recipes_passed(self, tool, monkeypatch, fake_rows):
        captured = {}
        matrix = _make_matrix()
        monkeypatch.setattr("foxhole.cargo.load_production", lambda *a, **kw: fake_rows)
        monkeypatch.setattr("foxhole.lp.matrix.build_recipe_matrix", lambda *a, **kw: matrix)

        def fake_solve(m, targets, *, objective, mode, limits, inventory, time_window_seconds):
            captured["limits"] = limits
            return _make_result()

        monkeypatch.setattr("foxhole.lp.solve.solve_lp", fake_solve)

        await tool.optimize_production(
            {"Basic Materials": 50},
            deny_recipes=["Basic Materials#2@Refinery"],
        )

        assert "Basic Materials#2@Refinery" in captured["limits"].denied_recipes

    @pytest.mark.asyncio
    async def test_integer_routes_to_milp(self, tool, monkeypatch, fake_rows):
        called = {}
        matrix = _make_matrix()
        monkeypatch.setattr("foxhole.cargo.load_production", lambda *a, **kw: fake_rows)
        monkeypatch.setattr("foxhole.lp.matrix.build_recipe_matrix", lambda *a, **kw: matrix)
        monkeypatch.setattr(
            "foxhole.lp.solve.solve_lp",
            lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should use milp")),
        )

        def fake_milp(
            m, targets, *, objective, mode, limits, inventory, time_window_seconds, max_facilities
        ):
            called["milp"] = True
            called["max_facilities"] = max_facilities
            return _make_result()

        monkeypatch.setattr("foxhole.lp.integer.solve_milp", fake_milp)

        await tool.optimize_production(
            {"Basic Materials": 50},
            integer=True,
            max_facilities={"Refinery": 3},
        )

        assert called.get("milp")
        assert called["max_facilities"] == {"Refinery": 3}

    @pytest.mark.asyncio
    async def test_time_window_passed(self, tool, monkeypatch, fake_rows):
        captured = {}
        matrix = _make_matrix()
        monkeypatch.setattr("foxhole.cargo.load_production", lambda *a, **kw: fake_rows)
        monkeypatch.setattr("foxhole.lp.matrix.build_recipe_matrix", lambda *a, **kw: matrix)

        def fake_solve(m, targets, *, objective, mode, limits, inventory, time_window_seconds):
            captured["tw"] = time_window_seconds
            return _make_result()

        monkeypatch.setattr("foxhole.lp.solve.solve_lp", fake_solve)
        await tool.optimize_production({"Basic Materials": 50}, time_window_seconds=7200.0)
        assert captured["tw"] == 7200.0


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------


class TestOutputShape:
    @pytest.mark.asyncio
    async def test_returns_all_lpresult_fields(self, tool, patch_lp):
        out = await tool.optimize_production({"Basic Materials": 100})

        # All LPResult model fields must be present
        result_fields = set(LPResult.model_fields.keys())
        for field in result_fields:
            assert field in out, f"Missing LPResult field: {field}"

    @pytest.mark.asyncio
    async def test_summary_present_and_nonempty(self, tool, patch_lp):
        out = await tool.optimize_production({"Basic Materials": 100})
        assert "summary" in out
        assert isinstance(out["summary"], str)
        assert len(out["summary"]) > 0

    @pytest.mark.asyncio
    async def test_recipes_chosen_present(self, tool, patch_lp):
        out = await tool.optimize_production({"Basic Materials": 100})
        assert "recipes_chosen" in out
        assert isinstance(out["recipes_chosen"], list)

    @pytest.mark.asyncio
    async def test_recipes_chosen_shape(self, tool, patch_lp):
        out = await tool.optimize_production({"Basic Materials": 100})
        for entry in out["recipes_chosen"]:
            assert "recipe_id" in entry
            assert "runs" in entry
            assert "reason" in entry

    @pytest.mark.asyncio
    async def test_status_in_result(self, tool, patch_lp):
        out = await tool.optimize_production({"Basic Materials": 100})
        assert out["status"] == "optimal"

    @pytest.mark.asyncio
    async def test_runs_in_result(self, tool, patch_lp):
        out = await tool.optimize_production({"Basic Materials": 100})
        assert out["runs"] == {"Basic Materials#1@Refinery": 2.0}

    @pytest.mark.asyncio
    async def test_raw_used_in_result(self, tool, patch_lp):
        out = await tool.optimize_production({"Basic Materials": 100})
        assert out["raw_used"] == {"Salvage": 200.0}

    @pytest.mark.asyncio
    async def test_infeasible_result_passthrough(self, tool, monkeypatch, fake_rows):
        matrix = _make_matrix()
        infeasible = _make_result(status="infeasible", objective_value=None, runs={}, raw_used={})
        monkeypatch.setattr("foxhole.cargo.load_production", lambda *a, **kw: fake_rows)
        monkeypatch.setattr("foxhole.lp.matrix.build_recipe_matrix", lambda *a, **kw: matrix)
        monkeypatch.setattr("foxhole.lp.solve.solve_lp", lambda *a, **kw: infeasible)

        out = await tool.optimize_production({"Basic Materials": 100})
        assert out["status"] == "infeasible"
        assert "infeasible" in out["summary"].lower()


# ---------------------------------------------------------------------------
# Stockpile integration
# ---------------------------------------------------------------------------


class TestStockpile:
    @pytest.mark.asyncio
    async def test_use_stockpile_passes_inventory(self, tool, monkeypatch, fake_rows):
        captured = {}
        matrix = _make_matrix()
        monkeypatch.setattr("foxhole.cargo.load_production", lambda *a, **kw: fake_rows)
        monkeypatch.setattr("foxhole.lp.matrix.build_recipe_matrix", lambda *a, **kw: matrix)

        def fake_solve(m, targets, *, objective, mode, limits, inventory, time_window_seconds):
            captured["inventory"] = inventory
            return _make_result()

        monkeypatch.setattr("foxhole.lp.solve.solve_lp", fake_solve)
        monkeypatch.setattr(
            "foxhole.stockpiles.read_stockpiles",
            lambda path, hex_name=None, **kw: [],
        )
        monkeypatch.setattr(
            "foxhole.stockpiles.inventory",
            lambda snaps: {"Salvage": 50.0},
        )

        await tool.optimize_production(
            {"Basic Materials": 100},
            use_stockpile=True,
            hex_name="Farranac Coast",
        )

        assert captured["inventory"] == {"Salvage": 50.0}

    @pytest.mark.asyncio
    async def test_no_stockpile_passes_none_inventory(self, tool, monkeypatch, fake_rows):
        captured = {}
        matrix = _make_matrix()
        result = _make_result()
        monkeypatch.setattr("foxhole.cargo.load_production", lambda *a, **kw: fake_rows)
        monkeypatch.setattr("foxhole.lp.matrix.build_recipe_matrix", lambda *a, **kw: matrix)

        def fake_solve(m, targets, *, objective, mode, limits, inventory, time_window_seconds):
            captured["inventory"] = inventory
            return result

        monkeypatch.setattr("foxhole.lp.solve.solve_lp", fake_solve)

        await tool.optimize_production({"Basic Materials": 100}, use_stockpile=False)
        assert captured.get("inventory") is None

    @pytest.mark.asyncio
    async def test_stockpile_failure_does_not_crash(self, tool, monkeypatch, fake_rows):
        """If stockpile reading fails, tool should still succeed (skip inventory)."""
        matrix = _make_matrix()
        monkeypatch.setattr("foxhole.cargo.load_production", lambda *a, **kw: fake_rows)
        monkeypatch.setattr("foxhole.lp.matrix.build_recipe_matrix", lambda *a, **kw: matrix)
        monkeypatch.setattr("foxhole.lp.solve.solve_lp", lambda *a, **kw: _make_result())
        monkeypatch.setattr(
            "foxhole.stockpiles.read_stockpiles",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("no save file")),
        )

        out = await tool.optimize_production({"Basic Materials": 100}, use_stockpile=True)
        assert "error" not in out or out.get("status") == "optimal"


# ---------------------------------------------------------------------------
# Hauling constraint
# ---------------------------------------------------------------------------


class TestHauling:
    @pytest.mark.asyncio
    async def test_max_trips_adds_extra_constraint(self, tool, monkeypatch, fake_rows):
        captured_limits = {}
        matrix = _make_matrix()
        monkeypatch.setattr("foxhole.cargo.load_production", lambda *a, **kw: fake_rows)
        monkeypatch.setattr("foxhole.lp.matrix.build_recipe_matrix", lambda *a, **kw: matrix)

        def fake_solve(m, targets, *, objective, mode, limits, inventory, time_window_seconds):
            captured_limits["extra"] = limits.extra
            return _make_result()

        monkeypatch.setattr("foxhole.lp.solve.solve_lp", fake_solve)
        monkeypatch.setattr(
            "foxhole.lp.hauling.trip_constraint",
            lambda max_trips, vehicle="Dunne Loadlugger 3c": ExtraConstraint(
                name="hauling",
                coeffs={"Salvage": 1.0 / 2000},
                upper=max_trips,
            ),
        )
        monkeypatch.setattr(
            "foxhole.lp.hauling.trips_needed",
            lambda raw_used, vehicle="Dunne Loadlugger 3c": 3,
        )

        out = await tool.optimize_production({"Basic Materials": 100}, max_trips=5.0)

        assert len(captured_limits["extra"]) == 1
        assert captured_limits["extra"][0].name == "hauling"
        assert out.get("trips_needed") == 3

    @pytest.mark.asyncio
    async def test_max_trips_notimplemented_is_silenced(self, tool, monkeypatch, fake_rows):
        """If hauling story isn't done, max_trips should be skipped without crashing."""
        matrix = _make_matrix()
        monkeypatch.setattr("foxhole.cargo.load_production", lambda *a, **kw: fake_rows)
        monkeypatch.setattr("foxhole.lp.matrix.build_recipe_matrix", lambda *a, **kw: matrix)
        monkeypatch.setattr("foxhole.lp.solve.solve_lp", lambda *a, **kw: _make_result())

        monkeypatch.setattr(
            "foxhole.lp.hauling.trip_constraint",
            lambda *a, **kw: (_ for _ in ()).throw(NotImplementedError()),
        )

        out = await tool.optimize_production({"Basic Materials": 100}, max_trips=5.0)
        # Should not have an error from hauling, and no crash
        assert isinstance(out, dict)


# ---------------------------------------------------------------------------
# recipes_chosen logic
# ---------------------------------------------------------------------------


class TestRecipesChosen:
    @pytest.mark.asyncio
    async def test_only_recipe_reason(self, tool, monkeypatch, fake_rows):
        """Single recipe for output -> reason says 'only recipe'."""
        matrix = _make_matrix()  # one recipe for Basic Materials
        result = _make_result(runs={"Basic Materials#1@Refinery": 2.0})

        monkeypatch.setattr("foxhole.cargo.load_production", lambda *a, **kw: fake_rows)
        monkeypatch.setattr("foxhole.lp.matrix.build_recipe_matrix", lambda *a, **kw: matrix)
        monkeypatch.setattr("foxhole.lp.solve.solve_lp", lambda *a, **kw: result)

        out = await tool.optimize_production({"Basic Materials": 100})

        chosen = {e["recipe_id"]: e for e in out["recipes_chosen"]}
        assert "Basic Materials#1@Refinery" in chosen
        assert "only" in chosen["Basic Materials#1@Refinery"]["reason"].lower()

    @pytest.mark.asyncio
    async def test_alternative_recipe_reason(self, tool, monkeypatch, fake_rows):
        """Two recipes for same output -> chosen one cites the alternative."""
        r1 = Recipe(
            id="Basic Materials#1@Refinery",
            source="Refinery",
            rank=1,
            outputs={"Basic Materials": 50.0},
            inputs={"Salvage": 100.0},
            seconds=60.0,
        )
        r2 = Recipe(
            id="Basic Materials#2@Refinery",
            source="Refinery",
            rank=2,
            outputs={"Basic Materials": 25.0},
            inputs={"Salvage": 60.0},
            seconds=40.0,
        )
        items = ["Salvage", "Basic Materials"]
        A = np.zeros((2, 2), dtype=float)
        A[1, 0] = 50.0
        A[0, 0] = -100.0
        A[1, 1] = 25.0
        A[0, 1] = -60.0
        matrix = RecipeMatrix(items=items, recipes=[r1, r2], A=A)
        result = _make_result(runs={"Basic Materials#1@Refinery": 2.0})

        monkeypatch.setattr("foxhole.cargo.load_production", lambda *a, **kw: fake_rows)
        monkeypatch.setattr("foxhole.lp.matrix.build_recipe_matrix", lambda *a, **kw: matrix)
        monkeypatch.setattr("foxhole.lp.solve.solve_lp", lambda *a, **kw: result)

        out = await tool.optimize_production({"Basic Materials": 100})

        chosen = {e["recipe_id"]: e for e in out["recipes_chosen"]}
        assert "Basic Materials#1@Refinery" in chosen
        reason = chosen["Basic Materials#1@Refinery"]["reason"]
        assert "alternative" in reason.lower() or "Basic Materials#2@Refinery" in reason


# ---------------------------------------------------------------------------
# Name resolution via economy solver
# ---------------------------------------------------------------------------


class TestNameResolution:
    @pytest.mark.asyncio
    async def test_alias_resolved_to_canonical(self, tool, monkeypatch, fake_rows):
        """'bmats' alias should resolve to 'Basic Materials'."""
        captured_targets = {}
        matrix = _make_matrix()
        monkeypatch.setattr("foxhole.cargo.load_production", lambda *a, **kw: fake_rows)
        monkeypatch.setattr("foxhole.lp.matrix.build_recipe_matrix", lambda *a, **kw: matrix)

        def fake_solve(m, targets, *, objective, mode, limits, inventory, time_window_seconds):
            captured_targets["items"] = [t.item for t in targets]
            return _make_result()

        monkeypatch.setattr("foxhole.lp.solve.solve_lp", fake_solve)

        await tool.optimize_production({"bmats": 500})
        assert "Basic Materials" in captured_targets["items"]

    @pytest.mark.asyncio
    async def test_unknown_target_item_returns_error(self, tool, monkeypatch, fake_rows):
        matrix = _make_matrix()
        monkeypatch.setattr("foxhole.cargo.load_production", lambda *a, **kw: fake_rows)
        monkeypatch.setattr("foxhole.lp.matrix.build_recipe_matrix", lambda *a, **kw: matrix)

        out = await tool.optimize_production({"xyzzy_totally_fake_item_zzz": 100})
        assert "error" in out


async def test_real_data_excludes_non_gatherable_raw_by_default(monkeypatch, production_rows):
    """Integration: shirts come from Salvage, not 'raw' Critically Wounded Soldiers."""
    import foxhole.cargo as cargo
    from foxhole.tools.optimize import OptimizeTools

    monkeypatch.setattr(cargo, "load_production", lambda *a, **k: production_rows)
    tools = OptimizeTools()
    plan = await tools.optimize_production({"Soldier Supplies": 15}, integer=True)
    assert plan["status"] == "optimal"
    assert plan["raw_used"] == {"Salvage": 320.0}  # 2 crates x 80 BM x 2 Salvage
    assert plan["surplus"] == {"Soldier Supplies": 5.0}
    allowed = await tools.optimize_production(
        {"Soldier Supplies": 15}, allow_raw=["Critically Wounded Soldier"]
    )
    assert "Critically Wounded Soldier" in allowed["raw_used"]
