"""Sync wiki Cargo tables to a local cache and build the economy registry from them.

The wiki maintainers ask that bulk consumers pull Cargo tables once through the API
instead of scraping rendered pages. The Production table holds every recipe, with one
RecipeRank 1 row per output, which maps directly onto the Leontief solver's
one-recipe-per-good model.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from foxhole.client import FoxholeWikiClient
from foxhole.economy import ItemCategory, ItemDefinition

PRODUCTION_TABLE = "Production"
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


def production_cache_path(cache_dir: Path | None = None) -> Path:
    return (cache_dir or default_cache_dir()) / "production.json"


async def sync_production(
    client: FoxholeWikiClient, cache_dir: Path | None = None
) -> tuple[Path, int]:
    """Download the full Production table and write it to the cache. Returns (path, rows)."""
    rows = await client.cargo_query(PRODUCTION_TABLE, PRODUCTION_FIELDS)
    path = production_cache_path(cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"table": PRODUCTION_TABLE, "fetched_at": time.time(), "rows": rows}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1))
    tmp.replace(path)
    return path, len(rows)


def load_production(cache_dir: Path | None = None) -> list[dict[str, str]] | None:
    """Load cached Production rows, or None if no cache exists."""
    path = production_cache_path(cache_dir)
    if not path.exists():
        return None
    return json.loads(path.read_text())["rows"]


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
            crate_size=int(crate_capacity) if crate_capacity else None,
        )

    for name in sorted(referenced - registry.keys()):
        registry[name] = ItemDefinition(name=name, category=ItemCategory.RAW_RESOURCE)

    return registry
