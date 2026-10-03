"""Tests for story 1: sync recipe byproducts (SecondaryOutput / TertiaryOutput fields)."""

from foxhole.cargo import PRODUCTION_FIELDS, build_registry, recipe_from_row


def _find(rows: list[dict], output: str, rank: int) -> dict:
    return next(r for r in rows if r.get("Output") == output and r.get("RecipeRank") == str(rank))


def test_production_fields_include_byproduct_columns() -> None:
    assert "SecondaryOutput" in PRODUCTION_FIELDS
    assert "SecondaryOutputAmount" in PRODUCTION_FIELDS
    assert "TertiaryOutput" in PRODUCTION_FIELDS
    assert "TertiaryOutputAmount" in PRODUCTION_FIELDS


def test_no_byproducts_when_fields_absent() -> None:
    row: dict[str, str] = {
        "Source": "Factory",
        "Output": "Basic Materials",
        "OutputAmount": "1",
        "RecipeRank": "1",
    }
    recipe = recipe_from_row(row)
    assert recipe.byproducts == {}


def test_no_byproducts_when_fields_empty() -> None:
    row: dict[str, str] = {
        "Source": "Factory",
        "Output": "Basic Materials",
        "OutputAmount": "1",
        "RecipeRank": "1",
        "SecondaryOutput": "",
        "SecondaryOutputAmount": "",
        "TertiaryOutput": "",
        "TertiaryOutputAmount": "",
    }
    recipe = recipe_from_row(row)
    assert recipe.byproducts == {}


def test_coke_rank2_has_sulfur_byproduct(production_rows: list[dict]) -> None:
    """Coal Refinery rank 2: 200 Coal -> 165 Coke + 15 Sulfur."""
    row = _find(production_rows, "Coke", 2)
    recipe = recipe_from_row(row)
    assert recipe.byproducts == {"Sulfur": 15.0}
    assert recipe.output_amount == 165
    assert recipe.inputs == {"Coal": 200}


def test_coke_rank3_has_heavy_oil_byproduct(production_rows: list[dict]) -> None:
    """Coal Refinery rank 3: 300 Coal + 100 Water -> 260 Coke + 60 Heavy Oil."""
    row = _find(production_rows, "Coke", 3)
    recipe = recipe_from_row(row)
    assert recipe.byproducts == {"Heavy Oil": 60.0}
    assert recipe.output_amount == 260


def test_metal_beam_rank2_has_construction_materials_byproduct(production_rows: list[dict]) -> None:
    """Materials Factory rank 2: 25 Salvage -> 1 Metal Beam + 1 Construction Materials."""
    row = _find(production_rows, "Metal Beam", 2)
    recipe = recipe_from_row(row)
    assert recipe.byproducts == {"Construction Materials": 1.0}
    assert recipe.inputs == {"Salvage": 25}
    assert recipe.output_amount == 1


def test_power_station_rank7_has_sulfur_byproduct(production_rows: list[dict]) -> None:
    """Power Station rank 7: 50 Heavy Oil -> 16 Facility Power + 5 Sulfur."""
    row = _find(production_rows, "Facility Power", 7)
    recipe = recipe_from_row(row)
    assert recipe.byproducts == {"Sulfur": 5.0}
    assert recipe.output_amount == 16


def test_rank1_rows_have_no_byproducts(production_rows: list[dict]) -> None:
    """No rank-1 row in the fixture has a byproduct."""
    rank1 = [r for r in production_rows if r.get("RecipeRank") == "1"]
    assert len(rank1) > 0
    for row in rank1:
        recipe = recipe_from_row(row)
        assert recipe.byproducts == {}, (
            f"{row.get('Output')} rank 1 from {row.get('Source')} should have no byproducts"
        )


def test_build_registry_uses_rank1_only_and_inputs_unchanged(production_rows: list[dict]) -> None:
    """build_registry uses rank-1 only; byproduct support must not alter its inputs."""
    registry = build_registry(production_rows)

    # Basic Materials rank 1 (Refinery): 2 Salvage -> 1 Basic Materials
    assert "Basic Materials" in registry
    assert registry["Basic Materials"].inputs == {"Salvage": 2.0}

    # Coke rank 1 (Coal Refinery): 200 Coal -> 180 Coke, no byproduct
    assert "Coke" in registry
    expected_coal_per_unit = 200 / 180
    assert abs(registry["Coke"].inputs["Coal"] - expected_coal_per_unit) < 1e-9

    # Construction Materials rank 1 (Materials Factory): 10 Salvage -> 1 unit
    assert "Construction Materials" in registry
    assert registry["Construction Materials"].inputs == {"Salvage": 10.0}


def test_build_registry_rank1_outputs_have_no_byproduct_in_row(production_rows: list[dict]) -> None:
    """Explicit check: every rank-1 row fed into build_registry has no byproducts in fixture."""
    from foxhole.cargo import EXTRACTION_SOURCES

    rank1_non_extraction = [
        r
        for r in production_rows
        if r.get("RecipeRank") == "1" and r.get("Source") not in EXTRACTION_SOURCES
    ]
    assert len(rank1_non_extraction) > 0
    for row in rank1_non_extraction:
        assert row.get("SecondaryOutput", "") == "", (
            f"{row.get('Output')} rank 1 has unexpected SecondaryOutput"
        )
