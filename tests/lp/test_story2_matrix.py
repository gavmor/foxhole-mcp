"""Tests for story-2: build_recipe_matrix.

Story-4 (power) is not yet implemented; consumed_per_run / produced_per_run are
monkeypatched throughout using the natural formula:
  consumed = InputPower * ProductionTime
  produced = OutputAmount * ProductionTime
"""

from __future__ import annotations

import numpy as np
import pytest

from foxhole.lp.matrix import build_recipe_matrix
from foxhole.lp.model import EXTRACTION_SOURCES, POWER, RecipeMatrix

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _mp_consumed(monkeypatch: pytest.MonkeyPatch) -> None:
    """consumed_per_run = InputPower * ProductionTime (0 if either blank)."""

    def _consumed(row: dict[str, str]) -> float:
        pw = row.get("InputPower", "").strip()
        pt = row.get("ProductionTime", "").strip()
        return float(pw) * float(pt) if pw and pt else 0.0

    monkeypatch.setattr("foxhole.lp.power.consumed_per_run", _consumed)


def _mp_produced(monkeypatch: pytest.MonkeyPatch) -> None:
    """produced_per_run = OutputAmount * ProductionTime."""

    def _produced(row: dict[str, str]) -> float:
        oa = row.get("OutputAmount", "").strip()
        pt = row.get("ProductionTime", "").strip()
        return float(oa) * float(pt) if oa and pt else 0.0

    monkeypatch.setattr("foxhole.lp.power.produced_per_run", _produced)


def _matrix_no_power(
    monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, str]], **kw
) -> RecipeMatrix:
    _mp_consumed(monkeypatch)
    _mp_produced(monkeypatch)
    return build_recipe_matrix(rows, include_power=False, **kw)


def _matrix_with_power(
    monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, str]], **kw
) -> RecipeMatrix:
    _mp_consumed(monkeypatch)
    _mp_produced(monkeypatch)
    return build_recipe_matrix(rows, include_power=True, **kw)


def _recipe_by_id(mat: RecipeMatrix, rid: str):
    idx = mat.recipe_index[rid]
    return mat.recipes[idx]


def _col(mat: RecipeMatrix, rid: str) -> dict[str, float]:
    """Return {item: net_amount} for a single recipe column (non-zero entries)."""
    j = mat.recipe_index[rid]
    return {mat.items[i]: mat.A[i, j] for i in range(len(mat.items)) if mat.A[i, j] != 0}


# ---------------------------------------------------------------------------
# 1. Basic structure
# ---------------------------------------------------------------------------


def test_returns_recipe_matrix_type(monkeypatch, production_rows):
    mat = _matrix_no_power(monkeypatch, production_rows)
    assert isinstance(mat, RecipeMatrix)


def test_matrix_shape_consistent(monkeypatch, production_rows):
    mat = _matrix_no_power(monkeypatch, production_rows)
    assert mat.A.shape == (len(mat.items), len(mat.recipes))
    assert mat.A.dtype == np.float64


def test_ids_are_unique(monkeypatch, production_rows):
    mat = _matrix_no_power(monkeypatch, production_rows)
    ids = [r.id for r in mat.recipes]
    assert len(ids) == len(set(ids))


def test_items_sorted(monkeypatch, production_rows):
    mat = _matrix_no_power(monkeypatch, production_rows)
    assert mat.items == sorted(mat.items)


# ---------------------------------------------------------------------------
# 2. Extraction rows excluded by default
# ---------------------------------------------------------------------------


def test_extraction_excluded_by_default(monkeypatch, production_rows):
    mat = _matrix_no_power(monkeypatch, production_rows)
    for recipe in mat.recipes:
        assert not recipe.extraction, f"{recipe.id} should have been excluded"


def test_extraction_sources_not_in_recipes(monkeypatch, production_rows):
    mat = _matrix_no_power(monkeypatch, production_rows)
    for recipe in mat.recipes:
        assert recipe.source not in EXTRACTION_SOURCES


def test_extraction_included_when_flag_set(monkeypatch, production_rows):
    mat = _matrix_no_power(monkeypatch, production_rows, include_extraction=True)
    extraction_recipes = [r for r in mat.recipes if r.extraction]
    assert len(extraction_recipes) > 0


def test_extraction_outputs_become_raw_items(monkeypatch, production_rows):
    """When extractions are excluded, their outputs (Salvage, Coal, etc.) are raw."""
    mat = _matrix_no_power(monkeypatch, production_rows)
    raw = mat.raw_items
    # Salvage and Coal come from extraction sources only in this fixture
    assert "Salvage" in raw
    assert "Coal" in raw


# ---------------------------------------------------------------------------
# 3. Hand-checked recipe: Basic Materials#1@Refinery
# ---------------------------------------------------------------------------

# Fixture: Source=Refinery, InputItem1=Salvage, Amount=2, Output=Basic Materials,
# IsCrateOutput=0, OutputAmount=1, RecipeRank=1
# Expected: +1 Basic Materials, -2 Salvage


def test_basic_materials_recipe_id(monkeypatch, production_rows):
    mat = _matrix_no_power(monkeypatch, production_rows)
    assert "Basic Materials#1@Refinery" in mat.recipe_index


def test_basic_materials_column(monkeypatch, production_rows):
    mat = _matrix_no_power(monkeypatch, production_rows)
    col = _col(mat, "Basic Materials#1@Refinery")
    assert col["Basic Materials"] == pytest.approx(1.0)
    assert col["Salvage"] == pytest.approx(-2.0)


def test_basic_materials_recipe_fields(monkeypatch, production_rows):
    mat = _matrix_no_power(monkeypatch, production_rows)
    r = _recipe_by_id(mat, "Basic Materials#1@Refinery")
    assert r.source == "Refinery"
    assert r.rank == 1
    assert not r.crate_output
    assert r.seconds == pytest.approx(0.48)
    assert not r.extraction


# ---------------------------------------------------------------------------
# 4. Hand-checked recipe: Soldier Supplies#1@Factory (IsCrateOutput=1)
# ---------------------------------------------------------------------------

# Fixture: Source=Factory, InputItem1=Basic Materials, Amount=80,
# Output=Soldier Supplies, IsCrateOutput=1, CrateCapacity=10, OutputAmount=1, RecipeRank=1
# Expected: +10 Soldier Supplies, -80 Basic Materials


def test_soldier_supplies_crate_output(monkeypatch, production_rows):
    mat = _matrix_no_power(monkeypatch, production_rows)
    col = _col(mat, "Soldier Supplies#1@Factory")
    assert col["Soldier Supplies"] == pytest.approx(10.0)
    assert col["Basic Materials"] == pytest.approx(-80.0)


def test_soldier_supplies_recipe_flags(monkeypatch, production_rows):
    mat = _matrix_no_power(monkeypatch, production_rows)
    r = _recipe_by_id(mat, "Soldier Supplies#1@Factory")
    assert r.crate_output is True
    assert r.crate_size == 10
    assert r.seconds == pytest.approx(80.0)


# ---------------------------------------------------------------------------
# 5. Hand-checked recipe: Coke#2@Coal Refinery (byproduct Sulfur)
# ---------------------------------------------------------------------------

# Fixture: Source=Coal Refinery, InputItem1=Coal, Amount=200, InputPower=3,
# Output=Coke, OutputAmount=165, SecondaryOutput=Sulfur, SecondaryOutputAmount=15,
# RecipeRank=2, ProductionTime=270
# Expected (no power): +165 Coke, +15 Sulfur, -200 Coal


def test_coke_rank2_byproduct(monkeypatch, production_rows):
    mat = _matrix_no_power(monkeypatch, production_rows)
    col = _col(mat, "Coke#2@Coal Refinery")
    assert col["Coke"] == pytest.approx(165.0)
    assert col["Sulfur"] == pytest.approx(15.0)
    assert col["Coal"] == pytest.approx(-200.0)
    assert POWER not in col


def test_coke_rank2_with_power(monkeypatch, production_rows):
    """consumed = InputPower * ProductionTime = 3 * 270 = 810 MW·s."""
    mat = _matrix_with_power(monkeypatch, production_rows)
    col = _col(mat, "Coke#2@Coal Refinery")
    assert col["Coke"] == pytest.approx(165.0)
    assert col["Sulfur"] == pytest.approx(15.0)
    assert col["Coal"] == pytest.approx(-200.0)
    assert col[POWER] == pytest.approx(-810.0)


# ---------------------------------------------------------------------------
# 6. Power plant rows (Facility Power)
# ---------------------------------------------------------------------------

# Fixture: Diesel Power Plant#1@Diesel Power Plant, Output=Facility Power,
# OutputAmount=5, ProductionTime=45 -> produced = 5*45 = 225 MW·s
# Diesel Power Plant#2@Diesel Power Plant, Output=Facility Power,
# OutputAmount=5, ProductionTime=90, InputItem1=Coal, Amount=60 -> produced=450, consumed=0 (no InputPower)


def test_power_plant_outputs_power_commodity(monkeypatch, production_rows):
    mat = _matrix_with_power(monkeypatch, production_rows)
    # Diesel Power Plant rank-1 uses Diesel (25 L), no InputPower, produces 5 MW for 45 s
    assert "Facility Power#1@Diesel Power Plant" in mat.recipe_index
    col = _col(mat, "Facility Power#1@Diesel Power Plant")
    # produced_per_run = 5 * 45 = 225
    assert col[POWER] == pytest.approx(225.0)
    assert "Facility Power" not in col


def test_power_plant_no_power_mode_keeps_facility_power(monkeypatch, production_rows):
    """When include_power=False, power-plant output stays as 'Facility Power'."""
    mat = _matrix_no_power(monkeypatch, production_rows)
    col = _col(mat, "Facility Power#1@Diesel Power Plant")
    assert "Facility Power" in col
    assert POWER not in col


# ---------------------------------------------------------------------------
# 7. include_power=False: POWER never in matrix
# ---------------------------------------------------------------------------


def test_no_power_in_items_when_disabled(monkeypatch, production_rows):
    mat = _matrix_no_power(monkeypatch, production_rows)
    assert POWER not in mat.items


def test_power_in_items_when_enabled(monkeypatch, production_rows):
    mat = _matrix_with_power(monkeypatch, production_rows)
    assert POWER in mat.items


# ---------------------------------------------------------------------------
# 8. Powered non-power-plant row: Assembly Materials I#1@Materials Factory
# ---------------------------------------------------------------------------

# Fixture: Source=Materials Factory, InputItem1=Salvage 15, InputItem2=Coke 75,
# InputPower=2, ProductionTime=60, Output=Assembly Materials I, OutputAmount=1,
# IsCrateOutput=0, RecipeRank=1
# consumed = 2*60 = 120 MW·s


def test_powered_recipe_power_consumed(monkeypatch, production_rows):
    mat = _matrix_with_power(monkeypatch, production_rows)
    col = _col(mat, "Assembly Materials I#1@Materials Factory")
    assert col["Assembly Materials I"] == pytest.approx(1.0)
    assert col["Salvage"] == pytest.approx(-15.0)
    assert col["Coke"] == pytest.approx(-75.0)
    assert col[POWER] == pytest.approx(-120.0)


def test_powered_recipe_no_power_mode(monkeypatch, production_rows):
    mat = _matrix_no_power(monkeypatch, production_rows)
    col = _col(mat, "Assembly Materials I#1@Materials Factory")
    assert POWER not in col
    assert col["Assembly Materials I"] == pytest.approx(1.0)
    assert col["Salvage"] == pytest.approx(-15.0)
    assert col["Coke"] == pytest.approx(-75.0)


# ---------------------------------------------------------------------------
# 9. InputVehicle counts 1 unit
# ---------------------------------------------------------------------------


VEHICLE_ROW = {
    "page": "Widget A",
    "Source": "Small Assembly Station",
    "InputItem1": "Construction Materials",
    "InputItem1Amount": "5",
    "InputItem2": "",
    "InputItem2Amount": "",
    "InputItem3": "",
    "InputItem3Amount": "",
    "InputItem4": "",
    "InputItem4Amount": "",
    "InputItem5": "",
    "InputItem5Amount": "",
    "InputItem6": "",
    "InputItem6Amount": "",
    "InputVehicle": "Dunne Transport",
    "InputPower": "",
    "Output": "Widget A",
    "OutputType": "item",
    "OutputAmount": "1",
    "IsCrateOutput": "0",
    "CrateCapacity": "",
    "ProductionTime": "180",
    "Faction": "Both",
    "IsMPFable": "",
    "RecipeRank": "1",
}


def test_input_vehicle_counts_one(monkeypatch):
    _mp_consumed(monkeypatch)
    _mp_produced(monkeypatch)
    mat = build_recipe_matrix([VEHICLE_ROW], include_power=False)
    col = _col(mat, "Widget A#1@Small Assembly Station")
    assert col["Dunne Transport"] == pytest.approx(-1.0)
    assert col["Construction Materials"] == pytest.approx(-5.0)
    assert col["Widget A"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 10. Rows with no Output are skipped
# ---------------------------------------------------------------------------


def test_rows_with_no_output_skipped(monkeypatch):
    _mp_consumed(monkeypatch)
    _mp_produced(monkeypatch)
    rows = [
        {"Output": "", "Source": "Factory", "RecipeRank": "1"},
        {
            "Output": "Iron",
            "Source": "Refinery",
            "InputItem1": "Ore",
            "InputItem1Amount": "2",
            "OutputAmount": "1",
            "IsCrateOutput": "0",
            "RecipeRank": "1",
        },
    ]
    mat = build_recipe_matrix(rows, include_power=False)
    assert len(mat.recipes) == 1
    assert mat.recipes[0].id == "Iron#1@Refinery"


# ---------------------------------------------------------------------------
# 11. ID deduplication with suffix
# ---------------------------------------------------------------------------


def test_duplicate_id_gets_suffix(monkeypatch):
    _mp_consumed(monkeypatch)
    _mp_produced(monkeypatch)
    base_row = {
        "Output": "Iron",
        "Source": "Refinery",
        "OutputAmount": "1",
        "IsCrateOutput": "0",
        "RecipeRank": "1",
        "InputItem1": "Ore",
        "InputItem1Amount": "2",
    }
    mat = build_recipe_matrix([base_row, base_row], include_power=False)
    ids = [r.id for r in mat.recipes]
    assert ids[0] == "Iron#1@Refinery"
    assert ids[1] == "Iron#1@Refinery_2"
    assert len(set(ids)) == 2


# ---------------------------------------------------------------------------
# 12. Minimal matrix values (net = outputs - inputs)
# ---------------------------------------------------------------------------


def test_matrix_net_values_sign(monkeypatch, production_rows):
    """Outputs positive, inputs negative in A."""
    mat = _matrix_no_power(monkeypatch, production_rows)
    ri = mat.recipe_index
    ii = mat.item_index
    j_bm = ri["Basic Materials#1@Refinery"]
    assert mat.A[ii["Basic Materials"], j_bm] > 0
    assert mat.A[ii["Salvage"], j_bm] < 0


# ---------------------------------------------------------------------------
# 13. raw_items reflects extraction exclusion
# ---------------------------------------------------------------------------


def test_raw_items_includes_salvage_coal_when_no_extraction(monkeypatch, production_rows):
    mat = _matrix_no_power(monkeypatch, production_rows)
    raw = mat.raw_items
    assert "Salvage" in raw
    assert "Coal" in raw


def test_raw_items_does_not_include_basic_materials(monkeypatch, production_rows):
    mat = _matrix_no_power(monkeypatch, production_rows)
    assert "Basic Materials" not in mat.raw_items


def test_raw_items_fewer_when_extraction_included(monkeypatch, production_rows):
    mat_no = _matrix_no_power(monkeypatch, production_rows)
    mat_ex = _matrix_no_power(monkeypatch, production_rows, include_extraction=True)
    # With extractions, fewer items lack a producing recipe
    assert len(mat_ex.raw_items) <= len(mat_no.raw_items)


# ---------------------------------------------------------------------------
# 14. recipe.power_mw stores InputPower (not consumed)
# ---------------------------------------------------------------------------


def test_recipe_power_mw_field(monkeypatch, production_rows):
    mat = _matrix_no_power(monkeypatch, production_rows)
    r = _recipe_by_id(mat, "Coke#2@Coal Refinery")
    assert r.power_mw == pytest.approx(3.0)


def test_recipe_power_mw_zero_when_unpowered(monkeypatch, production_rows):
    mat = _matrix_no_power(monkeypatch, production_rows)
    r = _recipe_by_id(mat, "Basic Materials#1@Refinery")
    assert r.power_mw == pytest.approx(0.0)


def test_extractable_items_stay_raw_despite_other_producers(production_rows):
    """Components are mined (Component Mine) but also recycled at a Metalworks; Sulfur is mined
    but also a Coal Refinery byproduct. Both must remain obtainable raw."""
    from foxhole.lp.matrix import build_recipe_matrix

    m = build_recipe_matrix(production_rows, include_power=False)
    assert {"Components", "Sulfur", "Salvage", "Coal"} <= m.raw_items
    assert any(r.outputs.get("Sulfur", 0) > 0 for r in m.recipes)  # a byproduct recipe exists
    assert "Basic Materials" not in m.raw_items  # refined goods are never raw
