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
    "SecondaryOutput",
    "SecondaryOutputAmount",
    "TertiaryOutput",
    "TertiaryOutputAmount",
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
    byproducts: dict[str, float] = {}
    for prefix in ("Secondary", "Tertiary"):
        bp_name = row.get(f"{prefix}Output", "").strip()
        bp_amount = _num(row.get(f"{prefix}OutputAmount"))
        if bp_name and bp_amount:
            byproducts[bp_name] = bp_amount
    return ProductionRecipe(
        source=row.get("Source", ""),
        category=row.get("ProductionCategory") or None,
        inputs=inputs,
        input_vehicle=row.get("InputVehicle") or None,
        input_power=_num(row.get("InputPower")),
        output_amount=int(output_amount) if output_amount is not None else None,
        production_time_sec=_num(row.get("ProductionTime")),
        is_mpfable=row.get("IsMPFable") == "1",
        byproducts=byproducts,
    )


ROMAN_TIERS = {"i": "1", "ii": "2", "iii": "3"}


def _tier_variants(name: str) -> list[str]:
    """Generate normalized tier variants for a name.

    Handles:
    - 'Tier N X', 'Tier <Roman> X', 'TN X' -> 'X (Tier N)'
    - 'X Tier N', 'X Tier <Roman>', 'X TN' -> 'X (Tier N)'
    - 'X (Tier N)' -> 'Tier N X', 'X Tier N', 'TN X', 'X TN'
    """
    variants = [name]
    s = name.strip()

    # Pattern 1: Tier N <Name> or TN <Name> or Tier <Roman> <Name> (optionally separated by dash/colon)
    m1 = re.match(r"^(?:tier\s*([1-3]|i{1,3})|t([1-3]))(?:\s*[-:]\s*|\s+)(.+)$", s, re.IGNORECASE)
    if m1:
        tier = m1.group(1) or m1.group(2)
        tier = ROMAN_TIERS.get(tier.lower(), tier)
        base = m1.group(3).lstrip(" -:").strip()
        variants.extend(
            [f"{base} (Tier {tier})", f"{base} Tier {tier}", f"{base} T{tier}", f"T{tier} {base}"]
        )

    # Pattern 2: <Name> Tier N or <Name> TN or <Name> Tier <Roman> (optionally separated by dash/colon)
    m2 = re.match(r"^(.+?)(?:\s*[-:]\s*|\s+)(?:tier\s*([1-3]|i{1,3})|t([1-3]))$", s, re.IGNORECASE)
    if m2:
        base = m2.group(1).rstrip(" -:").strip()
        tier = m2.group(2) or m2.group(3)
        tier = ROMAN_TIERS.get(tier.lower(), tier)
        variants.extend(
            [f"{base} (Tier {tier})", f"Tier {tier} {base}", f"T{tier} {base}", f"{base} T{tier}"]
        )

    # Pattern 3: <Name> (Tier N)
    m3 = re.match(r"^(.+?)\s*\(\s*(?:tier\s*([1-3]|i{1,3})|t([1-3]))\s*\)$", s, re.IGNORECASE)
    if m3:
        base = m3.group(1).rstrip(" -:").strip()
        tier = m3.group(2) or m3.group(3)
        tier = ROMAN_TIERS.get(tier.lower(), tier)
        variants.extend(
            [f"Tier {tier} {base}", f"T{tier} {base}", f"{base} Tier {tier}", f"{base} T{tier}"]
        )

    return list(dict.fromkeys(variants))


def _vehicle_aliases(name: str) -> list[str]:
    """Extract common nicknames and designations for vehicle names."""
    aliases = [name]
    quotes = re.findall(r'["\u201c\'\u2018]([^"\u201d\'\u2019]+)["\u201d\'\u2019]', name)
    aliases.extend(quotes)

    cleaned = re.sub(r"^[A-Z0-9]+[a-z]?[\-_][a-z0-9]+[a-z]?\s+", "", name, flags=re.I)
    cleaned_no_quotes = re.sub(r'["\u201c\'\u2018\u201d\u2019]', "", cleaned).strip()
    if cleaned_no_quotes and len(cleaned_no_quotes) > 2:
        aliases.append(cleaned_no_quotes)

    if " - " in name:
        parts = [p.strip() for p in name.split(" - ") if p.strip()]
        aliases.extend(parts)
        for p in parts:
            p_no_model = re.sub(r"\s+Mk\.?\s+[IVX0-9]+", "", p, flags=re.I).strip()
            if p_no_model and len(p_no_model) > 2:
                aliases.append(p_no_model)

    m_brand = re.match(r"^(Dunne|BMS|Devitt|Gallant|Noble|Silverhand)\s+(.+)$", name, re.I)
    if m_brand:
        rest = m_brand.group(2).strip()
        aliases.append(rest)
        rest_no_suffix = re.sub(r"\s+[0-9]+[a-z]?$", "", rest, flags=re.I).strip()
        rest_no_suffix = re.sub(r"\s+Mk\.?\s+[IVX0-9]+$", "", rest_no_suffix, flags=re.I).strip()
        if rest_no_suffix and len(rest_no_suffix) > 2:
            aliases.append(rest_no_suffix)

    return list(dict.fromkeys(a for a in aliases if len(a) > 1))


class CargoStore:
    """Name-indexed view over the synced stat tables and their Production recipes."""

    def __init__(self, tables: dict[str, list[dict[str, str]]]) -> None:
        self._index: dict[str, dict[str, list[dict[str, str]]]] = {}
        for table in (ITEM_TABLE, VEHICLE_TABLE, STRUCTURE_TABLE):
            index: dict[str, list[dict[str, str]]] = {}
            for raw in tables.get(table) or []:
                row = {k: v for k, v in raw.items() if v not in ("", None)}
                raw_keys = [row.get("name", ""), row.get("page", ""), row.get("codename", "")]
                raw_keys += row.get("aliases", "").split(",")
                all_keys: list[str] = []
                for k in raw_keys:
                    if not k.strip():
                        continue
                    all_keys.append(k)
                    all_keys.extend(_tier_variants(k))
                    if table == VEHICLE_TABLE:
                        all_keys.extend(_vehicle_aliases(k))
                for key in {_norm(k) for k in all_keys if k.strip()}:
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
        candidates = [name]
        candidates.extend(_tier_variants(name))
        if table == VEHICLE_TABLE:
            candidates.extend(_vehicle_aliases(name))

        for cand in candidates:
            matches = self._index.get(table, {}).get(_norm(cand), [])
            if not matches:
                continue
            exact = [r for r in matches if _norm(r.get("name", "")) == _norm(cand)] or matches
            live = [r for r in exact if r.get("version") not in ("deprecated", "None")]
            return (live or exact)[0]

        return None

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
        stats: StructureStats | None = self._stats(STRUCTURE_TABLE, name, structure_from_args)
        if stats is None:
            return None
        clean_name = re.sub(r"\s*\((?:Tier\s*[1-3]|T[1-3])\)", "", stats.name, flags=re.I).strip()
        clean_name = re.sub(r"\s+(?:Tier\s*[1-3]|T[1-3])$", "", clean_name, flags=re.I).strip()
        tier_stats: dict[str, dict[str, Any]] = {}
        for t_num in ("1", "2", "3"):
            cand = f"{clean_name} (Tier {t_num})"
            row = self.find(STRUCTURE_TABLE, cand)
            if row:
                hp_val = _num(row.get("structure_hp") or row.get("hp"))
                rep_val = _num(row.get("repair"))
                tier_stats[f"Tier {t_num}"] = {
                    "health": int(hp_val) if hp_val is not None else None,
                    "armor_type": row.get("armour_type"),
                    "repair_cost": int(rep_val) if rep_val is not None else None,
                    "name": row.get("name"),
                }
        if len(tier_stats) > 1:
            stats.tier_stats = tier_stats
        return stats


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
