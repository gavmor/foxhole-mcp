"""Tests for the Cargo table sync and the registry built from it."""

import json

import httpx
import pytest

from foxhole.cargo import build_registry, load_production, sync_production
from foxhole.client import FoxholeWikiClient, WikiError
from foxhole.economy import CurriedEconomySolver, ItemCategory, get_economy_solver


def row(output, source, inputs, *, amount="1", crate="", is_crate="0", rank="1", **extra):
    r = {
        "Output": output,
        "Source": source,
        "OutputAmount": amount,
        "CrateCapacity": crate,
        "IsCrateOutput": is_crate,
        "RecipeRank": rank,
        "OutputType": "item",
        "ProductionCategory": "",
        "ProductionTime": "10",
    }
    for i, (name, qty) in enumerate(inputs.items(), 1):
        r[f"InputItem{i}"], r[f"InputItem{i}Amount"] = name, str(qty)
    r.update(extra)
    return r


ROWS = [
    row("Basic Materials", "Refinery", {"Salvage": 2}, crate="100"),
    row("Diesel", "Refinery", {"Salvage": 10}, amount="100"),
    row("Salvage", "Salvage Mine", {"Diesel": 1}, amount="9"),
    row("Soldier Supplies", "Factory", {"Basic Materials": 80}, crate="10", is_crate="1"),
    row("Soldier Supplies", "Hospital", {"Critically Wounded Soldier": 1}, rank="2"),
    row("Gas Mask Filter", "Factory", {"Basic Materials": 100}, crate="20", is_crate="1"),
    row("Truck", "Garage", {"Basic Materials": 100}, OutputType="vehicle"),
]


def test_build_registry_normalises_per_unit():
    reg = build_registry(ROWS)
    assert reg["Basic Materials"].inputs == {"Salvage": 2.0}
    assert reg["Diesel"].inputs == {"Salvage": 0.1}
    # Crate recipes yield OutputAmount * CrateCapacity units
    assert reg["Soldier Supplies"].inputs == {"Basic Materials": 8.0}
    assert reg["Soldier Supplies"].crate_size == 10
    assert reg["Soldier Supplies"].facility_type == "Factory"
    assert reg["Truck"].category == ItemCategory.VEHICLE
    assert reg["Basic Materials"].category == ItemCategory.REFINED_MATERIAL


def test_build_registry_keeps_extraction_outputs_raw():
    reg = build_registry(ROWS)
    assert reg["Salvage"].category == ItemCategory.RAW_RESOURCE
    assert reg["Salvage"].inputs == {}
    # Only rank-2 recipes reference it, so it never enters the registry
    assert "Critically Wounded Soldier" not in reg


def test_recipe_output_differs_from_page():
    """The filter recipe lives on the Gas Mask page; we key on Output, not the page."""
    rows = [row("Gas Mask Filter", "Factory", {"Basic Materials": 100}, crate="20", is_crate="1")]
    rows[0]["page"] = "Gas Mask"
    assert "Gas Mask Filter" in build_registry(rows)


def test_round_to_crates():
    solver = CurriedEconomySolver(registry=build_registry(ROWS))
    plan = solver.solve({"Soldier Supplies": 15})
    assert plan.crates == {"Soldier Supplies": 2}
    assert plan.raw_resources == {"Salvage": 240.0}

    plan = solver.solve({"Soldier Supplies": 15}, round_to_crates=True)
    assert plan.resolved_demand == {"Soldier Supplies": 20.0}
    assert plan.raw_resources == {"Salvage": 320.0}


def mock_client(handler):
    return FoxholeWikiClient(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_cargo_query_paginates():
    offsets = []

    def handler(request):
        params = request.url.params
        assert params["action"] == "cargoquery"
        offset = int(params["offset"])
        offsets.append(offset)
        n = 2 if offset == 0 else 1
        return httpx.Response(
            200, json={"cargoquery": [{"title": {"i": str(offset + k)}} for k in range(n)]}
        )

    rows = await mock_client(handler).cargo_query("Production", ["Output"], page_size=2)
    assert offsets == [0, 2]
    assert [r["i"] for r in rows] == ["0", "1", "2"]


async def test_cargo_query_raises_on_api_error():
    def handler(request):
        return httpx.Response(200, json={"error": {"info": "bad field"}})

    with pytest.raises(WikiError, match="bad field"):
        await mock_client(handler).cargo_query("Production", ["Nope"])


async def test_sync_feeds_solver(tmp_path, monkeypatch):
    monkeypatch.setenv("FOXHOLE_CARGO_DIR", str(tmp_path))

    def handler(request):
        return httpx.Response(200, json={"cargoquery": [{"title": r} for r in ROWS]})

    path, count = await sync_production(mock_client(handler))
    assert count == len(ROWS)
    assert json.loads(path.read_text())["table"] == "Production"
    assert load_production() == ROWS
    assert get_economy_solver().solve({"Gas Mask Filter": 20}).raw_resources == {"Salvage": 200.0}


def test_solver_falls_back_without_cache():
    assert load_production() is None
    assert "00MS “Stinger”" in get_economy_solver().registry
