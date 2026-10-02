"""Tests for reading foxhole-stockpiles exports and planning net of inventory."""

import json
import sys
import types

import pytest

from foxhole import stockpiles
from foxhole.cargo import CargoStore, build_registry
from foxhole.economy import CurriedEconomySolver
from foxhole.stockpiles import inventory, load_raw_stockpiles, read_stockpiles
from foxhole.tools.stockpiles import StockpileTools

ITEMS = [
    {
        "page": "Basic Materials",
        "name": "Basic Materials",
        "codename": "Cloth",
        "crate_amount": "100",
    },
    {"page": "Salvage", "name": "Salvage", "codename": "Metal", "crate_amount": "100"},
    {
        "page": "No.2 Loughcaster",
        "name": "No.2 Loughcaster",
        "codename": "RifleW",
        "crate_amount": "20",
    },
    {
        "page": "Soldier Supplies",
        "name": "Soldier Supplies",
        "codename": "SoldierSupplies",
        "crate_amount": "10",
    },
    {"page": "Rare Materials", "name": "Rare Materials", "codename": "", "crate_amount": ""},
    {"page": "Mystery", "name": "Mystery Crate", "codename": "MysteryBox", "crate_amount": ""},
]
VEHICLES = [{"page": "Dunne Transport", "name": "Dunne Transport", "codename": "TruckW"}]


def prod(output, inputs, *, amount="1", crate="", is_crate="0", source="Factory"):
    row = {
        "Output": output,
        "Source": source,
        "OutputAmount": amount,
        "CrateCapacity": crate,
        "IsCrateOutput": is_crate,
        "RecipeRank": "1",
        "OutputType": "item",
        "ProductionTime": "10",
    }
    for i, (name, qty) in enumerate(inputs.items(), 1):
        row[f"InputItem{i}"], row[f"InputItem{i}Amount"] = name, str(qty)
    return row


PRODUCTION = [
    prod("Basic Materials", {"Salvage": 2}, crate="100", source="Refinery"),
    prod("Soldier Supplies", {"Basic Materials": 80}, crate="10", is_crate="1"),
]


@pytest.fixture
def store(monkeypatch):
    s = CargoStore({"itemdata": ITEMS, "vehicles": VEHICLES, "Production": PRODUCTION})
    monkeypatch.setattr(stockpiles, "get_cargo_store", lambda: s)
    return s


# The webhook/file payload documented in foxhole-stockpiles docs/webhooks.md
WEBHOOK_PAYLOAD = {
    "name": "Logi",
    "type": "Seaport",
    "timestamp": "2024-01-04T09:00:00",
    "resolution": "1920x1080",
    "items": [
        {"code": "RifleW", "quantity": 120, "crated": True, "confidence": 0.92},
        {"code": "TruckW", "quantity": 3, "crated": False, "confidence": 0.95},
    ],
}


def write(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content if isinstance(content, str) else json.dumps(content))
    return path


def test_single_payload_resolves_codenames_and_crates(tmp_path, store):
    (snap,) = read_stockpiles(write(tmp_path, "logi.json", WEBHOOK_PAYLOAD))
    assert snap.name == "Logi" and snap.type == "Seaport"
    rifle, truck = snap.entries
    assert (rifle.name, rifle.crated, rifle.crate_size, rifle.units) == (
        "No.2 Loughcaster",
        True,
        20,
        2400,
    )
    assert (truck.name, truck.kind, truck.units) == ("Dunne Transport", "vehicle", 3)
    assert snap.warnings == []


def test_wrapped_and_list_json(tmp_path, store):
    wrapped = write(
        tmp_path, "a.json", {"stockpiles": [WEBHOOK_PAYLOAD, {**WEBHOOK_PAYLOAD, "name": "B"}]}
    )
    bare = write(tmp_path, "b.json", [WEBHOOK_PAYLOAD])
    assert [s["name"] for s in load_raw_stockpiles(wrapped)] == ["Logi", "B"]
    assert len(load_raw_stockpiles(bare)) == 1


def test_csv_with_header_and_tsv_without(tmp_path, store):
    header = "Stockpile Name,Stockpile Type,Code,Crated,Quantity,Confidence,Shard,Ingame Time"
    csv_text = f"{header}\nLogi,Seaport,Cloth,1,3,0.99,Able,\nLogi,Seaport,Metal,0,250,,Able,\nAlt,Bunker,Cloth,0,40,,,\n"
    (logi, alt) = read_stockpiles(write(tmp_path, "s.csv", csv_text))
    assert [(e.name, e.units) for e in logi.entries] == [("Basic Materials", 300), ("Salvage", 250)]
    assert alt.name == "Alt"

    tsv = write(tmp_path, "s.tsv", "Logi\tSeaport\tSoldierSupplies\t1\t2\t\t\t\n")
    (snap,) = read_stockpiles(tsv)
    assert snap.entries[0].units == 20


def test_csv_bad_row_reports_line(tmp_path, store):
    with pytest.raises(ValueError, match="line 1"):
        load_raw_stockpiles(write(tmp_path, "bad.csv", "only,three,cols\n"))


def test_warnings_for_unreadable_unknown_and_uncrateable(tmp_path, store):
    pile = {
        "name": "X",
        "items": [
            {"code": "Cloth", "quantity": -1},
            {"code": "NotARealCode", "quantity": 5},
            {"code": "MysteryBox", "quantity": 2, "crated": True},
            {"code": "RareMaterials", "quantity": 4},
        ],
    }
    (snap,) = read_stockpiles(write(tmp_path, "w.json", pile))
    joined = " | ".join(snap.warnings)
    assert "Cloth: quantity unreadable" in joined
    assert "NotARealCode: no wiki entry" in joined
    assert "MysteryBox: crated but crate size unknown" in joined
    assert snap.entries[3].name == "Rare Materials"  # CodeName with no wiki codename
    assert inventory([snap]) == {"Rare Materials": 4.0}


def test_filters(tmp_path, store):
    piles = {
        "stockpiles": [
            {"name": "Front", "hex": "LinnMercyHex", "is_reserve": False, "items": []},
            {"name": "Rear", "hex": "SpeakingWoodsHex", "is_reserve": True, "items": []},
        ]
    }
    path = write(tmp_path, "f.json", piles)
    assert [s.name for s in read_stockpiles(path, names=["rear"])] == ["Rear"]
    assert [s.name for s in read_stockpiles(path, hex_name="linnmercyhex")] == ["Front"]
    assert [s.name for s in read_stockpiles(path, include_reserves=False)] == ["Front"]


def test_sav_uses_fs_sav(tmp_path, store, monkeypatch):
    calls = []

    def parse_save(path, **kwargs):
        calls.append(kwargs)
        return [
            {
                "name": "Pinned",
                "type": "StorageDepot",
                "hex": "SpeakingWoodsHex",
                "is_reserve": True,
                "items": [{"code": "Cloth", "quantity": 2, "crated": True}],
            }
        ]

    monkeypatch.setitem(sys.modules, "fs_sav", types.SimpleNamespace(parse_save=parse_save))
    (snap,) = read_stockpiles(write(tmp_path, "MapData.sav", "binary"))
    assert calls == [{}]  # with_items would drop empty pinned stockpiles
    assert (snap.hex, snap.is_reserve, snap.entries[0].units) == ("SpeakingWoodsHex", True, 200)


def test_sav_errors_are_clean(tmp_path, store, monkeypatch):
    def boom(path, **kwargs):
        raise RuntimeError("Failed to parse save file")

    monkeypatch.setitem(sys.modules, "fs_sav", types.SimpleNamespace(parse_save=boom))
    with pytest.raises(ValueError, match="Could not read save file"):
        read_stockpiles(write(tmp_path, "x.sav", "junk"))

    monkeypatch.setitem(sys.modules, "fs_sav", None)  # import fails
    with pytest.raises(ValueError, match="--extra stockpiles"):
        read_stockpiles(write(tmp_path, "y.sav", "junk"))


def test_missing_file(store):
    with pytest.raises(ValueError, match="No such file"):
        read_stockpiles("/nonexistent/stock.json")


# --------------------------------------------------------------------- inventory netting


@pytest.fixture
def solver():
    return CurriedEconomySolver(registry=build_registry(PRODUCTION))


def test_netting_finished_goods_then_intermediates(solver):
    plan = solver.solve(
        {"Soldier Supplies": 15},
        inventory={"Soldier Supplies": 5, "Basic Materials": 100},
    )
    # 5 shirts issued from stock; 10 made from 80 bmats, all covered by stock
    assert plan.inventory_used == {"Soldier Supplies": 5.0, "Basic Materials": 80.0}
    assert plan.raw_resources == {}
    assert plan.crates == {"Soldier Supplies": 1}


def test_netting_partial_cover_produces_shortfall(solver):
    plan = solver.solve({"Soldier Supplies": 10}, inventory={"Basic Materials": 30})
    assert plan.refined_materials == {"Basic Materials": 50.0}
    assert plan.raw_resources == {"Salvage": 100.0}
    assert plan.inventory_used == {"Basic Materials": 30.0}


def test_netting_rounds_only_the_shortfall_to_crates(solver):
    plan = solver.solve(
        {"Soldier Supplies": 12}, inventory={"Soldier Supplies": 5}, round_to_crates=True
    )
    # 7 short -> one crate of 10 -> 80 bmats -> 160 salvage
    assert plan.crates == {"Soldier Supplies": 1}
    assert plan.raw_resources == {"Salvage": 160.0}


def test_inventory_fully_covers_demand(solver):
    plan = solver.solve({"Soldier Supplies": 4}, inventory={"Soldier Supplies": 10})
    assert plan.gross_production == {}
    assert plan.inventory_used == {"Soldier Supplies": 4.0}


def test_inventory_names_are_not_fuzzy_matched(solver):
    plan = solver.solve(
        {"Soldier Supplies": 10}, inventory={"Basic Mats": 500, "soldier supplies": 3}
    )
    assert plan.inventory_used == {"Soldier Supplies": 3.0}  # case-insensitive only


def test_no_inventory_matches_plain_solve(solver):
    a = solver.solve({"Soldier Supplies": 10})
    b = solver.solve({"Soldier Supplies": 10}, inventory={})
    assert a.raw_resources == b.raw_resources == {"Salvage": 160.0}
    assert b.inventory_used is None


# --------------------------------------------------------------------- tools


async def test_plan_from_stockpile_tool(tmp_path, store, monkeypatch):
    import foxhole.tools.stockpiles as tool_mod

    s = CurriedEconomySolver(registry=build_registry(PRODUCTION))
    monkeypatch.setattr(tool_mod, "get_economy_solver", lambda: s)
    pile = {"name": "Tine", "items": [{"code": "Cloth", "quantity": 1, "crated": True}]}
    path = str(write(tmp_path, "tine.json", pile))

    read = await StockpileTools().read_stockpile(path)
    assert read["inventory"] == {"Basic Materials": 100.0}

    plan = await StockpileTools().plan_from_stockpile(path, {"Soldier Supplies": 20})
    assert plan["stockpiles_read"] == ["Tine"]
    assert plan["inventory_used"] == {"Basic Materials": 100.0}
    assert plan["raw_resources"] == {"Salvage": 120.0}  # 160 bmats needed, 60 short

    missing = await StockpileTools().read_stockpile(str(tmp_path / "nope.json"))
    assert "error" in missing
