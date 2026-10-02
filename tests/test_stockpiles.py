"""Tests for reading foxhole-stockpiles exports and planning net of inventory."""

import json
import os
import sys
import time
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

    def parse_save_bytes(data, **kwargs):
        calls.append((data, kwargs))
        return [
            {
                "name": "Pinned",
                "type": "StorageDepot",
                "hex": "SpeakingWoodsHex",
                "is_reserve": True,
                "items": [{"code": "Cloth", "quantity": 2, "crated": True}],
            }
        ]

    monkeypatch.setitem(
        sys.modules, "fs_sav", types.SimpleNamespace(parse_save_bytes=parse_save_bytes)
    )
    (snap,) = read_stockpiles(write(tmp_path, "MapData.sav", "binary"))
    # Parsed from an in-memory snapshot; no with_items (it drops empty pinned stockpiles)
    assert calls == [(b"binary", {})]
    assert (snap.hex, snap.is_reserve, snap.entries[0].units) == ("SpeakingWoodsHex", True, 200)


def test_sav_errors_are_clean(tmp_path, store, monkeypatch):
    def boom(data, **kwargs):
        raise RuntimeError("Failed to parse save file")

    monkeypatch.setitem(sys.modules, "fs_sav", types.SimpleNamespace(parse_save_bytes=boom))
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

    plan = await StockpileTools().plan_from_stockpile({"Soldier Supplies": 20}, path=path)
    assert plan["stockpiles_read"] == ["Tine"]
    assert plan["inventory_used"] == {"Basic Materials": 100.0}
    assert plan["raw_resources"] == {"Salvage": 120.0}  # 160 bmats needed, 60 short

    missing = await StockpileTools().read_stockpile(str(tmp_path / "nope.json"))
    assert "error" in missing


# --------------------------------------------------------------------- save discovery


PROTON = (
    "steamapps/compatdata/505460/pfx/drive_c/users/steamuser/AppData/Local/Foxhole/Saved/SaveGames"
)


def make_save(base, name="123_MapData.sav", age=0):
    d = base / PROTON
    d.mkdir(parents=True, exist_ok=True)
    f = d / name
    f.write_bytes(b"sav")
    if age:
        t = time.time() - age
        os.utime(f, (t, t))
    return f


def test_find_saves_across_steam_layouts(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("FOXHOLE_SAVE_PATH", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    native = make_save(tmp_path / ".local/share/Steam", age=3600)
    flatpak = make_save(tmp_path / ".var/app/com.valvesoftware.Steam/.local/share/Steam", age=60)
    extra_lib = tmp_path / "games/SteamLibrary"
    extra = make_save(extra_lib, name="456_MapData.sav")
    vdf = tmp_path / ".local/share/Steam/steamapps/libraryfolders.vdf"
    vdf.write_text(f'"libraryfolders" {{ "1" {{ "path" "{extra_lib}" }} }}')
    make_save(tmp_path / ".local/share/Steam", name="UserData.sav")  # not a map save

    found = [s.path for s in stockpiles.find_save_files()]
    assert found == [
        str(extra.resolve()),
        str(flatpak.resolve()),
        str(native.resolve()),
    ]  # newest first
    assert stockpiles.default_save_path() == extra.resolve()


def test_save_path_override_and_none_found(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("FOXHOLE_SAVE_PATH", raising=False)
    with pytest.raises(ValueError, match=r"No Foxhole MapData\.sav found"):
        stockpiles.default_save_path()
    custom = tmp_path / "elsewhere" / "My_MapData.sav"
    custom.parent.mkdir()
    custom.write_bytes(b"x")
    monkeypatch.setenv("FOXHOLE_SAVE_PATH", str(custom.parent))
    assert stockpiles.default_save_path() == custom.resolve()


def test_read_retries_while_game_writes(tmp_path, monkeypatch):
    f = tmp_path / "m.sav"
    f.write_bytes(b"old")
    real_read = type(f).read_bytes
    n = {"reads": 0}

    def flaky_read(self):
        n["reads"] += 1
        if n["reads"] == 1:  # the game rewrites the file mid-read
            self.write_bytes(b"newer content")
            return b"old"
        return real_read(self)

    monkeypatch.setattr(type(f), "read_bytes", flaky_read)
    monkeypatch.setattr(stockpiles.time, "sleep", lambda s: None)
    assert stockpiles._read_stable(f) == b"newer content"
    assert n["reads"] == 2


# --------------------------------------------------------------------- change tracking


def pile(name, items, hex_name="SpeakingWoodsHex"):
    return {
        "name": name,
        "type": "Seaport",
        "hex": hex_name,
        "coords": {"x": 0.7, "y": 0.3},
        "items": items,
    }


def test_changes_baseline_then_deltas(tmp_path, store):
    path = tmp_path / "s.json"
    path.write_text(json.dumps([pile("Tine", [{"code": "Cloth", "quantity": 2, "crated": True}])]))
    first = stockpiles.diff_since_last(read_stockpiles(path), "src")
    assert first["baseline"] is True and first["changes"] == []

    path.write_text(
        json.dumps(
            [
                pile(
                    "Tine",
                    [
                        {"code": "Cloth", "quantity": 1, "crated": True},
                        {"code": "Metal", "quantity": 300},
                    ],
                ),
                pile(
                    "Front",
                    [{"code": "SoldierSupplies", "quantity": 3, "crated": True}],
                    "LinnMercyHex",
                ),
            ]
        )
    )
    second = stockpiles.diff_since_last(read_stockpiles(path), "src")
    by_name = {c["name"]: c for c in second["changes"]}
    assert second["baseline"] is False
    assert by_name["Tine"]["status"] == "changed"
    assert by_name["Tine"]["deltas"] == {"Basic Materials": -100.0, "Salvage": 300.0}
    assert by_name["Front"] == {
        **by_name["Front"],
        "status": "added",
        "deltas": {"Soldier Supplies": 30.0},
    }

    path.write_text(
        json.dumps(
            [
                pile(
                    "Tine",
                    [
                        {"code": "Cloth", "quantity": 1, "crated": True},
                        {"code": "Metal", "quantity": 300},
                    ],
                )
            ]
        )
    )
    third = stockpiles.diff_since_last(read_stockpiles(path), "src")
    assert [(c["name"], c["status"], c["deltas"]) for c in third["changes"]] == [
        ("Front", "removed", {"Soldier Supplies": -30.0})
    ]
    assert stockpiles.diff_since_last(read_stockpiles(path), "src")["changes"] == []


async def test_tools_default_to_newest_save(tmp_path, store, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("FOXHOLE_SAVE_PATH", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    make_save(tmp_path / ".steam/steam")
    monkeypatch.setitem(
        sys.modules,
        "fs_sav",
        types.SimpleNamespace(parse_save_bytes=lambda data, **kw: [pile("", [])]),
    )
    tools = StockpileTools()

    saves = await tools.find_foxhole_saves()
    assert len(saves["saves"]) == 1 and "pinned" in saves["note"]

    read = await tools.read_stockpile()
    assert read["source"].endswith("123_MapData.sav")
    assert "opened in game" in read["note"]  # empty pinned stockpile explained

    changes = await tools.stockpile_changes()
    assert changes["baseline"] is True
