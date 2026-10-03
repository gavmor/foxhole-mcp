"""Unit tests for the production planner (BOM rollup with Leontief fallback)."""

import importlib

import pytest

from foxhole.models import ProductionRecipe
from foxhole.planner import plan_production

# Recipes as parsed from foxhole.wiki.gg infoboxes.
RECIPES = {
    "Dunne Transport": [ProductionRecipe(source="Garage", inputs={"Basic Materials": 100})],
    "Basic Materials": [
        ProductionRecipe(source="Refinery", inputs={"Salvage": 2}, production_time_sec=0.48)
    ],
    "Explosive Powder": [
        ProductionRecipe(source="Refinery", inputs={"Salvage": 5}, production_time_sec=9.375)
    ],
    "40mm": [
        ProductionRecipe(
            source="Factory",
            inputs={"Basic Materials": 160, "Explosive Powder": 240},
            production_time_sec=200.0,
        ),
        ProductionRecipe(
            source="Infantry Kit Factory",
            inputs={"Construction Materials": 175, "Explosive Powder": 1320},
            output_amount=10,
        ),
    ],
    "Construction Materials": [
        ProductionRecipe(source="Materials Factory", inputs={"Salvage": 10}),
        ProductionRecipe(
            source="Materials Factory",
            category="Metal Press",
            inputs={"Salvage": 15, "Petrol": 25},
            output_amount=3,
        ),
    ],
    "Petrol": [ProductionRecipe(source="Oil Refinery", inputs={"Oil": 50}, output_amount=50)],
    "Diesel": [ProductionRecipe(source="Refinery", inputs={"Salvage": 10}, output_amount=100)],
    "Silverhand - Mk. IV": [ProductionRecipe(source="Garage", inputs={"Refined Materials": 2})],
    "Silverhand Chieftain - Mk. VI": [
        ProductionRecipe(
            source="Small Assembly Station",
            inputs={"Construction Materials": 5},
            input_vehicle="Silverhand - Mk. IV",
        )
    ],
    "Refined Materials": [ProductionRecipe(source="Refinery", inputs={"Components": 20})],
}
ALIASES = {"dunne": "Dunne Transport", "chieftain": "Silverhand Chieftain - Mk. VI"}


async def fake_fetch(name):
    title = ALIASES.get(name.lower(), name)
    if title in RECIPES:
        return title, RECIPES[title]
    return None


@pytest.mark.asyncio
async def test_simple_chain_rolls_up_to_raw():
    plan = await plan_production("dunne", 3, fake_fetch)
    assert plan["target"] == "Dunne Transport"
    assert plan["method"] == "bom_rollup"
    assert plan["raw_materials"] == {"Salvage": 600.0}
    steps = {s["item"]: s for s in plan["production_steps"]}
    assert steps["Basic Materials"]["batches"] == 300
    assert plan["facility_load"]["Garage"]["batches"] == 3


@pytest.mark.asyncio
async def test_shared_inputs_accumulate_and_alternatives_reported():
    plan = await plan_production("40mm", 2, fake_fetch)
    # 2 * (160 bmats * 2 salvage + 240 emats * 5 salvage)
    assert plan["raw_materials"] == {"Salvage": 3040.0}
    assert [a["index"] for a in plan["alternative_recipes"]["40mm"]] == [0, 1]


@pytest.mark.asyncio
async def test_recipe_choice_and_batch_rounding():
    plan = await plan_production(
        "Construction Materials", 10, fake_fetch, recipe_choice={"Construction Materials": 1}
    )
    steps = {s["item"]: s for s in plan["production_steps"]}
    # 10 cmats at 3 per batch -> 4 batches, 2 surplus
    assert steps["Construction Materials"]["batches"] == 4
    assert steps["Construction Materials"]["produced"] == 12
    assert plan["raw_materials"] == {"Salvage": 60.0, "Oil": 100.0}


@pytest.mark.asyncio
async def test_input_vehicle_is_expanded():
    plan = await plan_production("chieftain", 1, fake_fetch)
    assert plan["raw_materials"] == {"Salvage": 50.0, "Components": 40.0}


@pytest.mark.asyncio
async def test_override_as_leaf_and_unresolved_inputs():
    plan = await plan_production(
        "Dunne Transport", 1, fake_fetch, recipe_overrides={"Basic Materials": {}}
    )
    assert plan["raw_materials"] == {"Basic Materials": 100.0}

    plan = await plan_production(
        "Dunne Transport", 1, fake_fetch, recipe_overrides={"Basic Materials": {"Mystery": 1}}
    )
    assert plan["unresolved_inputs"] == {"Mystery": 100.0}


@pytest.mark.asyncio
async def test_feedback_loop_uses_leontief():
    # Salvage Mine: 1 Diesel -> 9 Salvage; Refinery: 10 Salvage -> 100 Diesel.
    plan = await plan_production(
        "Basic Materials", 90, fake_fetch, recipe_overrides={"Salvage": {"Diesel": 1 / 9}}
    )
    assert plan["method"] == "leontief"
    steps = {s["item"]: s for s in plan["production_steps"]}
    # Salvage s = 180 + 0.1 * s/9 ... s = 180 / (1 - 1/90)
    assert steps["Salvage"]["required"] == pytest.approx(180 / (1 - 1 / 90), abs=1e-3)


@pytest.mark.asyncio
async def test_errors():
    with pytest.raises(ValueError, match="quantity"):
        await plan_production("Dunne Transport", 0, fake_fetch)
    with pytest.raises(ValueError, match="out of range"):
        await plan_production("40mm", 1, fake_fetch, recipe_choice={"40mm": 5})


@pytest.mark.asyncio
async def test_mcp_tool_returns_error_json(monkeypatch):
    server = importlib.import_module("foxhole.server")

    monkeypatch.setattr(server, "_fetch_recipes", fake_fetch)
    data = await server.plan_production("Dunne Transport", 0)
    assert "error" in data
    data = await server.plan_production("dunne", 2)
    assert data["raw_materials"] == {"Salvage": 400.0}
