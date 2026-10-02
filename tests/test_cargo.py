"""Tests for the Cargo table sync and the registry built from it."""

import json

import httpx
import pytest

from foxhole.cargo import (
    CargoStore,
    build_aliases,
    build_registry,
    get_cargo_store,
    load_production,
    load_table,
    sync_all,
)
from foxhole.client import FoxholeWikiClient, WikiError
from foxhole.economy import CurriedEconomySolver, ItemCategory, get_economy_solver
from foxhole.tools.wiki import WikiTools


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


def must[T](value: T | None) -> T:
    assert value is not None
    return value


ITEMS = [
    {
        "page": "Gas Mask",
        "name": "Gas Mask Filter",
        "codename": "GasMaskFilter",
        "faction": "",
        "crate amount": "",
        "aliases": "filter,filters",
        "version": "",
    },
    {"page": "Old Rifle", "name": "Rifle", "codename": "", "version": "deprecated"},
    {"page": "Rifle", "name": "Rifle", "codename": "RifleW", "faction": "War", "version": ""},
]
VEHICLES = [{"page": "Truck", "name": "Truck", "codename": "TruckW", "vehicle hp": "900"}]


def cargo_handler(request):
    params = request.url.params
    if params["action"] == "cargofields":
        return httpx.Response(200, json={"cargofields": {"name": {}, "codename": {}}})
    table = params["tables"]
    rows = {"Production": ROWS, "itemdata": ITEMS, "vehicles": VEHICLES}.get(table, [])
    return httpx.Response(200, json={"cargoquery": [{"title": r} for r in rows]})


async def test_cargo_query_restores_underscores():
    rows = await mock_client(cargo_handler).cargo_query("vehicles", ["vehicle_hp"])
    assert rows[0]["vehicle_hp"] == "900"


async def test_sync_all_feeds_solver_and_store(tmp_path, monkeypatch):
    monkeypatch.setenv("FOXHOLE_CARGO_DIR", str(tmp_path))
    counts = await sync_all(mock_client(cargo_handler))
    assert counts == {"Production": len(ROWS), "itemdata": 3, "vehicles": 1, "structures": 0}
    assert json.loads((tmp_path / "production.json").read_text())["table"] == "Production"
    assert load_production() == ROWS
    assert must(load_table("itemdata"))[0]["crate_amount"] == ""

    solver = get_economy_solver()
    assert solver.solve({"Gas Mask Filter": 20}).raw_resources == {"Salvage": 200.0}
    # itemdata aliases become solver synonyms
    assert solver.resolve_item_name("filters") == "Gas Mask Filter"
    assert get_cargo_store() is not None


def test_store_lookup_by_name_codename_alias():
    store = CargoStore({"itemdata": ITEMS, "vehicles": VEHICLES, "Production": ROWS})
    for query in ("Gas Mask Filter", "gas-mask filter", "GasMaskFilter", "filter"):
        assert must(store.find("itemdata", query))["name"] == "Gas Mask Filter"
    # Deprecated rows lose to live ones with the same name
    assert must(store.find("itemdata", "Rifle"))["codename"] == "RifleW"
    assert must(store.find("vehicles", "TruckW"))["name"] == "Truck"
    assert store.find("itemdata", "nonexistent") is None


def test_store_stats_use_production_table():
    store = CargoStore({"itemdata": ITEMS, "Production": ROWS})
    item = must(store.item("filter"))
    assert item.name == "Gas Mask Filter"
    assert item.faction == "Both"
    assert item.wiki_url.endswith("/Gas_Mask")
    assert [(r.source, r.inputs) for r in item.production] == [
        ("Factory", {"Basic Materials": 100})
    ]
    assert store.crate_capacity("Gas Mask Filter") == 20
    assert must(store.item("Rifle")).faction == "Warden"


def test_build_aliases():
    assert build_aliases(ITEMS) == {"filter": "Gas Mask Filter", "filters": "Gas Mask Filter"}


class NoNetworkClient(FoxholeWikiClient):
    async def get_page_data(self, *args, **kwargs):
        raise AssertionError("page fetch despite cache hit")

    async def opensearch(self, *args, **kwargs):
        raise AssertionError("opensearch despite cache hit")


async def test_tools_answer_from_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("FOXHOLE_CARGO_DIR", str(tmp_path))
    await sync_all(mock_client(cargo_handler))
    tools = WikiTools(client=NoNetworkClient())

    cost = await tools.get_production_cost("Gas Mask Filter")
    assert cost["entity_type"] == "item"
    assert cost["crate_amount"] == 20
    assert cost["production_recipes"][0]["inputs"] == {"Basic Materials": 100}

    truck = await tools.get_vehicle_stats("TruckW")
    assert truck["name"] == "Truck"
    assert truck["health"] == 900


def test_falls_back_without_cache():
    assert load_production() is None
    assert get_cargo_store() is None
    assert "00MS “Stinger”" in get_economy_solver().registry
