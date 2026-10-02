"""Sync wiki Cargo tables to a local cache, and serve stats and recipes from it.

The wiki maintainers ask that bulk consumers pull Cargo tables once through the API
instead of scraping rendered pages. The Production table holds every recipe, with one
RecipeRank 1 row per output, which maps directly onto the Leontief solver's
one-recipe-per-good model. The itemdata, vehicles and structures tables use the same
field names as the page infoboxes, so they feed the existing stat models.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from foxhole.client import FoxholeWikiClient
from foxhole.economy import ItemCategory, ItemDefinition, reset_economy_solver
from foxhole.models import ItemStats, ProductionRecipe, StructureStats, VehicleStats
from foxhole.parser import item_from_args, structure_from_args, vehicle_from_args

PRODUCTION_TABLE = "Production"
ITEM_TABLE = "itemdata"
VEHICLE_TABLE = "vehicles"
STRUCTURE_TABLE = "structures"
SYNCED_TABLES = (PRODUCTION_TABLE, ITEM_TABLE, VEHICLE_TABLE, STRUCTURE_TABLE)
MAX_INPUTS = 6

PRODUCTION_FIELDS = [
    "_pageName=page",
    "Source",
    "ProductionCategory",
    *(f"InputItem{i}{suffix}" for i in range(1, MAX_INPUTS + 1) for suffix in ("", "Amount")),
    "InputVehicle",
    "InputPower",
    "Output",
    "OutputType",
    "OutputAmount",
    "IsCrateOutput",
    "CrateCapacity",
    "ProductionTime",
    "Faction",
    "IsMPFable",
    "RecipeRank",
]

# Sources that pull resources out of the world. Their recipes (e.g. Salvage Mine: Diesel ->
# Salvage) would fold fuel loops into every bill of materials, so their outputs stay raw.
EXTRACTION_SOURCES = {
    "Salvage Mine",
    "Component Mine",
    "Sulfur Mine",
    "Oil Well",
    "Water Pump",
    "Offshore Platform",
    "Stationary Harvester (Salvage)",
    "Stationary Harvester (Components)",
    "Stationary Harvester (Sulfur)",
    "Stationary Harvester (Coal)",
}
REFINERY_SOURCES = {"Refinery", "Coal Refinery", "Oil Refinery"}
FACILITY_SOURCES = {"Materials Factory", "Metalworks Factory", "Concrete Mixer"}


def default_cache_dir() -> Path:
    """Cache directory: $FOXHOLE_CARGO_DIR, else $XDG_CACHE_HOME/foxhole/cargo."""
    if env := os.getenv("FOXHOLE_CARGO_DIR"):
        return Path(env)
    xdg = os.getenv("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(xdg) / "foxhole" / "cargo"


def table_cache_path(table: str, cache_dir: Path | None = None) -> Path:
    return (cache_dir or default_cache_dir()) / f"{table.lower()}.json"


async def sync_table(
    client: FoxholeWikiClient, table: str, cache_dir: Path | None = None
) -> tuple[Path, int]:
    """Download a whole Cargo table and write it to the cache. Returns (path, rows).

    Production uses a fixed field list (the registry builder depends on it); the stat
    tables take every field the wiki defines.
    """
    if table == PRODUCTION_TABLE:
        fields = PRODUCTION_FIELDS
    else:
        fields = ["_pageName=page", *await client.cargo_fields(table)]
    rows = await client.cargo_query(table, fields)
    path = table_cache_path(table, cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"table": table, "fetched_at": time.time(), "rows": rows}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1))
    tmp.replace(path)
    return path, len(rows)


async def sync_all(client: FoxholeWikiClient, cache_dir: Path | None = None) -> dict[str, int]:
    """Sync every table the server uses. Returns {table: row_count}."""
    counts = {}
    for table in SYNCED_TABLES:
        _, counts[table] = await sync_table(client, table, cache_dir)
    reset_cargo_store()
    reset_economy_solver()
    return counts


def load_table(table: str, cache_dir: Path | None = None) -> list[dict[str, str]] | None:
    """Load cached rows for a table, or None if it has not been synced."""
    path = table_cache_path(table, cache_dir)
    if not path.exists():
        return None
    return json.loads(path.read_text())["rows"]


def load_production(cache_dir: Path | None = None) -> list[dict[str, str]] | None:
    """Load cached Production rows, or None if no cache exists."""
    return load_table(PRODUCTION_TABLE, cache_dir)


def _num(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _category(row: dict[str, str]) -> ItemCategory:
    source = row.get("Source", "")
    if source in REFINERY_SOURCES:
        return ItemCategory.REFINED_MATERIAL
    if source in FACILITY_SOURCES:
        return ItemCategory.FACILITY_MATERIAL
    output_type = row.get("OutputType", "")
    if output_type == "vehicle":
        return ItemCategory.VEHICLE
    if output_type == "structure":
        return ItemCategory.STRUCTURE
    if "Ammunition" in row.get("ProductionCategory", ""):
        return ItemCategory.AMMUNITION
    return ItemCategory.SMALL_ARMS


def build_registry(rows: list[dict[str, str]]) -> dict[str, ItemDefinition]:
    """Convert Production rows into a solver registry keyed by output name.

    Uses the RecipeRank 1 recipe for each output. Input amounts are normalised to a single
    unit of output: crate recipes (IsCrateOutput) yield OutputAmount * CrateCapacity units.
    Anything consumed but not produced (or only extracted) becomes a raw resource.
    """
    registry: dict[str, ItemDefinition] = {}
    referenced: set[str] = set()

    for row in rows:
        output = row.get("Output", "").strip()
        if not output or row.get("RecipeRank") != "1" or row.get("Source") in EXTRACTION_SOURCES:
            continue

        crate_capacity = _num(row.get("CrateCapacity"))
        units = _num(row.get("OutputAmount")) or 1.0
        if row.get("IsCrateOutput") == "1" and crate_capacity:
            units *= crate_capacity

        inputs: dict[str, float] = {}
        for i in range(1, MAX_INPUTS + 1):
            name = row.get(f"InputItem{i}", "").strip()
            amount = _num(row.get(f"InputItem{i}Amount"))
            if name and amount:
                inputs[name] = inputs.get(name, 0.0) + amount / units
        if vehicle := row.get("InputVehicle", "").strip():
            inputs[vehicle] = inputs.get(vehicle, 0.0) + 1.0 / units
        referenced.update(inputs)

        registry[output] = ItemDefinition(
            name=output,
            category=_category(row),
            inputs=inputs,
            facility_type=row.get("Source") or None,
            crafting_time_sec=_num(row.get("ProductionTime")),
            yield_per_craft=units,
            crate_size=int(crate_capacity)
            if (crate_capacity and row.get("IsCrateOutput") == "1")
            else None,
        )

    for name in sorted(referenced - registry.keys()):
        registry[name] = ItemDefinition(name=name, category=ItemCategory.RAW_RESOURCE)

    return registry


def build_aliases(item_rows: list[dict[str, str]]) -> dict[str, str]:
    """Map lowercase itemdata aliases (e.g. 'amats1') to item names, for solver synonyms."""
    aliases: dict[str, str] = {}
    for row in item_rows:
        name = row.get("name", "").strip()
        for alias in (row.get("aliases") or "").split(","):
            if name and alias.strip():
                aliases.setdefault(alias.strip().lower(), name)
    return aliases


# ---------------------------------------------------------------------------
# Stats lookups served from the cache
# ---------------------------------------------------------------------------


def _norm(text: str) -> str:
    text = re.sub(r"[\"'“”‘’`\-_.]", " ", text.lower())  # noqa: RUF001
    return " ".join(text.split())


def _int_or_float(value: float) -> int | float:
    return int(value) if value.is_integer() else value


def recipe_from_row(row: dict[str, str]) -> ProductionRecipe:
    inputs: dict[str, int | float] = {}
    for i in range(1, MAX_INPUTS + 1):
        name = row.get(f"InputItem{i}", "").strip()
        amount = _num(row.get(f"InputItem{i}Amount"))
        if name:
            inputs[name] = _int_or_float(amount) if amount is not None else 1
    output_amount = _num(row.get("OutputAmount"))
    return ProductionRecipe(
        source=row.get("Source", ""),
        category=row.get("ProductionCategory") or None,
        inputs=inputs,
        input_vehicle=row.get("InputVehicle") or None,
        input_power=_num(row.get("InputPower")),
        output_amount=int(output_amount) if output_amount is not None else None,
        production_time_sec=_num(row.get("ProductionTime")),
        is_mpfable=row.get("IsMPFable") == "1",
    )


class CargoStore:
    """Name-indexed view over the synced stat tables and their Production recipes."""

    def __init__(self, tables: dict[str, list[dict[str, str]]]) -> None:
        self._index: dict[str, dict[str, list[dict[str, str]]]] = {}
        for table in (ITEM_TABLE, VEHICLE_TABLE, STRUCTURE_TABLE):
            index: dict[str, list[dict[str, str]]] = {}
            for raw in tables.get(table) or []:
                row = {k: v for k, v in raw.items() if v not in ("", None)}
                keys = [row.get("name", ""), row.get("page", ""), row.get("codename", "")]
                keys += row.get("aliases", "").split(",")
                for key in {_norm(k) for k in keys if k.strip()}:
                    index.setdefault(key, []).append(row)
            self._index[table] = index

        self._recipes: dict[str, list[dict[str, str]]] = {}
        for row in tables.get(PRODUCTION_TABLE) or []:
            if output := row.get("Output", "").strip():
                self._recipes.setdefault(_norm(output), []).append(row)
        for rows in self._recipes.values():
            rows.sort(key=lambda r: _num(r.get("RecipeRank")) or 99)

    def has(self, table: str) -> bool:
        return bool(self._index.get(table))

    def find(self, table: str, name: str) -> dict[str, str] | None:
        """Exact (normalised) match on name, page, codename or alias; live rows first."""
        matches = self._index.get(table, {}).get(_norm(name), [])
        if not matches:
            return None
        exact = [r for r in matches if _norm(r.get("name", "")) == _norm(name)] or matches
        live = [r for r in exact if r.get("version") not in ("deprecated", "None")]
        return (live or exact)[0]

    def recipes(self, output: str) -> list[ProductionRecipe]:
        return [recipe_from_row(r) for r in self._recipes.get(_norm(output), [])]

    def crate_capacity(self, output: str) -> int | None:
        for row in self._recipes.get(_norm(output), []):
            if size := _num(row.get("CrateCapacity")):
                return int(size)
        return None

    def _stats(self, table: str, name: str, build: Any) -> Any:
        row = self.find(table, name)
        if row is None:
            return None
        title = row.get("page") or row.get("name", name)
        return build(title, row, None, self.recipes(row.get("name", title)))

    def vehicle(self, name: str) -> VehicleStats | None:
        return self._stats(VEHICLE_TABLE, name, vehicle_from_args)

    def item(self, name: str) -> ItemStats | None:
        return self._stats(ITEM_TABLE, name, item_from_args)

    def structure(self, name: str) -> StructureStats | None:
        return self._stats(STRUCTURE_TABLE, name, structure_from_args)


_STORE: CargoStore | None = None


def get_cargo_store() -> CargoStore | None:
    """The cached store, or None until `foxhole cargo-sync` has been run."""
    global _STORE
    if _STORE is None:
        tables = {t: rows for t in SYNCED_TABLES if (rows := load_table(t))}
        if not tables:
            return None
        _STORE = CargoStore(tables)
    return _STORE


def reset_cargo_store() -> None:
    global _STORE
    _STORE = None
