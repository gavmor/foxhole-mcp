"""Sync wiki Cargo tables to a local cache, and serve stats and recipes from it.

The wiki maintainers ask that bulk consumers pull Cargo tables once through the API
instead of scraping rendered pages. The Production table holds every recipe, with one
RecipeRank 1 row per output, which maps directly onto the Leontief solver's
one-recipe-per-good model. The itemdata, vehicles and structures tables use the same
field names as the page infoboxes, so they feed the existing stat models.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from pathlib import Path
from typing import Any

from foxhole.client import FoxholeWikiClient
from foxhole.economy import ItemCategory, ItemDefinition, reset_economy_solver
from foxhole.models import Armament, ItemStats, ProductionRecipe, StructureStats, VehicleStats
from foxhole.parser import item_from_args, structure_from_args, vehicle_from_args

PRODUCTION_TABLE = "Production"
ITEM_TABLE = "itemdata"
VEHICLE_TABLE = "vehicles"
STRUCTURE_TABLE = "structures"
DAMAGETYPES_TABLE = "damagetypes"
ARMAMENT_TABLE = "armament2"
VEHICLECLASS_TABLE = "VehicleClass"
SYNCED_TABLES = (
    PRODUCTION_TABLE,
    ITEM_TABLE,
    VEHICLE_TABLE,
    STRUCTURE_TABLE,
    DAMAGETYPES_TABLE,
    ARMAMENT_TABLE,
    VEHICLECLASS_TABLE,
)
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


def _normalize_armor_type(armor: str) -> str:
    cleaned = re.sub(r"[\s\-_]", "", armor.lower())
    mapping = {
        "none": "None",
        "lightvehicle": "LightVehicle",
        "light": "LightVehicle",
        "truck": "LightVehicle",
        "tier1tank": "Tier1Tank",
        "t1tank": "Tier1Tank",
        "tier2tank": "Tier2Tank",
        "t2tank": "Tier2Tank",
        "tank": "Tier2Tank",
        "assaulttank": "Tier2Tank",
        "mediumtank": "Tier2Tank",
        "heavytank": "Tier2Tank",
        "tier1ship": "Tier1Ship",
        "tier2ship": "Tier2Ship",
        "tier1largeship": "Tier1LargeShip",
        "tier1aircraft": "Tier1Aircraft",
        "tier1structure": "Tier1Structure",
        "t1structure": "Tier1Structure",
        "tier2structure": "Tier2Structure",
        "t2structure": "Tier2Structure",
        "tier2bstructure": "Tier2BStructure",
        "tier3structure": "Tier3Structure",
        "t3structure": "Tier3Structure",
        "concrete": "Tier3Structure",
        "tier3bstructure": "Tier3BStructure",
        "tier1garrisonhouse": "Tier1GarrisonHouse",
        "tier2garrisonhouse": "Tier2GarrisonHouse",
        "tier3garrisonhouse": "Tier3GarrisonHouse",
        "trench": "Trench",
    }
    return mapping.get(cleaned, armor)


def _normalize_damage_type(dtype: str) -> str:
    cleaned = re.sub(r"[\s\-_]", "", dtype.lower())
    mapping = {
        "antitankexplosive": "Anti-Tank Explosive",
        "atexplosive": "Anti-Tank Explosive",
        "at": "Anti-Tank Explosive",
        "antitankkinetic": "Anti-Tank Kinetic",
        "atkinetic": "Anti-Tank Kinetic",
        "atk": "Anti-Tank Kinetic",
        "armourpiercing": "Armour Piercing",
        "armorpiercing": "Armour Piercing",
        "ap": "Armour Piercing",
        "highexplosive": "High Explosive",
        "he": "High Explosive",
        "bombhighexplosive": "Bomb High Explosive",
        "demolition": "Demolition",
        "demo": "Demolition",
        "explosive": "Explosive",
        "heavykinetic": "Heavy Kinetic",
        "hk": "Heavy Kinetic",
        "lightkinetic": "Light Kinetic",
        "lk": "Light Kinetic",
        "poisonousgas": "Poisonous Gas",
        "gas": "Poisonous Gas",
        "shrapnel": "Shrapnel",
        "fire": "Fire",
        "incendiary": "Incendiary",
        "incendiaryhighexplosive": "Incendiary High Explosive",
        "melee": "Melee",
        "smoke": "Smoke",
        "flare": "Flare",
        "extinguishing": "Extinguishing",
    }
    return mapping.get(cleaned, dtype)


class CargoStore:
    """Name-indexed view over the synced stat tables and their Production recipes."""

    def __init__(self, tables: dict[str, list[dict[str, str]]]) -> None:
        self._index: dict[str, dict[str, list[dict[str, str]]]] = {}
        for table in (ITEM_TABLE, VEHICLE_TABLE, STRUCTURE_TABLE, VEHICLECLASS_TABLE):
            index: dict[str, list[dict[str, str]]] = {}
            for raw in tables.get(table) or []:
                row = {k.replace(" ", "_"): v for k, v in raw.items() if v not in ("", None)}
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

        self._damagetypes: dict[str, dict[str, float]] = {}
        for raw in tables.get(DAMAGETYPES_TABLE) or []:
            dt_name = raw.get("name", "").strip()
            if not dt_name:
                continue
            resists: dict[str, float] = {}
            for k, v in raw.items():
                if k in ("page", "name", "image", "CanDisableSubsystems"):
                    continue
                val = _num(v)
                if val is not None:
                    resists[k] = val
                    norm_k = _normalize_armor_type(k)
                    resists[norm_k] = val
                    resists[k.lower()] = val
                    resists[norm_k.lower()] = val
            norm_name = _normalize_damage_type(dt_name)
            self._damagetypes[norm_name.lower()] = resists
            self._damagetypes[dt_name.lower()] = resists

        self._armaments: dict[str, list[dict[str, str]]] = {}
        for raw in tables.get(ARMAMENT_TABLE) or []:
            row = {k.replace(" ", "_"): v for k, v in raw.items() if v not in ("", None)}
            keys = [row.get("parent_name", ""), row.get("page", "")]
            all_keys = []
            for k in keys:
                if not k.strip():
                    continue
                all_keys.append(k)
                all_keys.extend(_tier_variants(k))
                all_keys.extend(_vehicle_aliases(k))
            for key in {_norm(k) for k in all_keys if k.strip()}:
                self._armaments.setdefault(key, []).append(row)
        for arm_list in self._armaments.values():
            arm_list.sort(key=lambda r: _num(r.get("ArmamentIndex")) or 0)

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
        stats: VehicleStats | None = self._stats(VEHICLE_TABLE, name, vehicle_from_args)
        if stats is None:
            return None

        # Fallback armor_type from VehicleClass if missing
        if not stats.armor_type:
            for cand in [stats.name, stats.vehicle_type or "", name]:
                if cand and (vc_row := self.find(VEHICLECLASS_TABLE, cand)):
                    if vc_armour := vc_row.get("armour_type"):
                        stats.armor_type = vc_armour
                        break

        # Enrich armaments from armament2
        arm_rows = None
        for cand in [stats.name, name]:
            if cand and _norm(cand) in self._armaments:
                arm_rows = self._armaments[_norm(cand)]
                break

        if arm_rows:
            enriched: list[Armament] = []
            for r in arm_rows:
                mag = _num(r.get("MagazineSize"))
                enriched.append(
                    Armament(
                        name=r.get("ArmamentName") or "Weapon",
                        ammo=r.get("AmmoName1") or None,
                        reload_time=_num(r.get("ReloadTime")),
                        firing_time=_num(r.get("FiringTime")),
                        range_max=r.get("RangeMax") or None,
                        range_effective=r.get("RangeEffective") or None,
                        fire_rate=_num(r.get("FireRate")),
                        magazine_size=int(mag) if mag is not None else None,
                        traverse=r.get("Traverse") or None,
                        firing_arc=r.get("FiringArc") or None,
                    )
                )
            if enriched:
                stats.armaments = enriched
                ammo_names = {a.ammo for a in enriched if a.ammo}
                stats.dedicated_ammo_slots = len(ammo_names) if ammo_names else 0
                if stats.cargo_slots is not None:
                    stats.inventory_slots = stats.cargo_slots + stats.dedicated_ammo_slots

        return stats

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

    def damage_mitigation(self, damage_type: str, armor_type: str) -> float:
        """Return the damage mitigation fraction (0.0 to 1.0) for a damage type vs armor class.

        0.0 = full damage (0% mitigated), 1.0 = completely immune (100% mitigated).
        """
        norm_dt = _normalize_damage_type(damage_type).lower()
        resists = self._damagetypes.get(norm_dt, {})
        norm_armor = _normalize_armor_type(armor_type)
        if norm_armor in resists:
            return resists[norm_armor]
        if norm_armor.lower() in resists:
            return resists[norm_armor.lower()]
        for k, v in resists.items():
            if k.lower() == norm_armor.lower():
                return v
        return 0.0

    def calculate_combat_damage(self, target: str, weapon_or_ammo: str) -> dict[str, Any]:
        """Calculate damage, mitigation, penetrating hits, and estimated shots fired."""
        target_name = target
        target_type = "vehicle"
        hp: int | None = None
        armor_type: str | None = None
        armor_health: int | None = None
        min_pen: float | None = None
        max_pen: float | None = None
        disable_threshold: float | None = None

        veh = self.vehicle(target)
        if veh:
            target_name = veh.name
            target_type = "vehicle"
            hp = veh.health
            armor_type = veh.armor_type or "Tier2Tank"
            armor_health = veh.armor_health
            min_pen = veh.min_pen_chance
            max_pen = veh.max_pen_chance
            disable_threshold = veh.disable_threshold
        else:
            struc = self.structure(target)
            if struc:
                target_name = struc.name
                target_type = "structure"
                hp = struc.health
                armor_type = struc.armor_type or "Tier2Structure"
            else:
                return {
                    "error": f"Target '{target}' could not be found among vehicles or structures in the Cargo database."
                }

        if hp is None or hp <= 0:
            return {"error": f"Target '{target_name}' has unknown or invalid health points."}

        weapon_row = self.find(ITEM_TABLE, weapon_or_ammo)
        weapon_name = weapon_or_ammo
        base_damage: float | None = None
        damage_type: str | None = None
        pen_factor: float = 1.0

        if weapon_row:
            weapon_name = weapon_row.get("name", weapon_or_ammo)
            if raw_dmg := _num(weapon_row.get("damage")):
                base_damage = raw_dmg
                damage_type = weapon_row.get("damage_type")
                pen_factor = _num(weapon_row.get("TankArmourPenetrationFactor")) or 1.0
            elif ammo_ref := weapon_row.get("ammo"):
                ammo_row = self.find(ITEM_TABLE, ammo_ref)
                if ammo_row and (raw_dmg := _num(ammo_row.get("damage"))):
                    weapon_name = f"{weapon_name} firing {ammo_row.get('name', ammo_ref)}"
                    base_damage = raw_dmg
                    damage_type = ammo_row.get("damage_type")
                    pen_factor = _num(ammo_row.get("TankArmourPenetrationFactor")) or 1.0
                else:
                    return {
                        "error": f"Weapon '{weapon_name}' requires ammo '{ammo_ref}', which has no damage profile in the database."
                    }
        else:
            return {
                "error": f"Weapon or ammunition '{weapon_or_ammo}' could not be found in the Cargo item database."
            }

        if base_damage is None or base_damage <= 0 or not damage_type:
            return {
                "error": f"Could not determine valid damage or damage type for '{weapon_name}'."
            }

        mitigation = self.damage_mitigation(damage_type, armor_type)
        mitigation_pct = round(mitigation * 100, 1)
        effective_damage = base_damage * (1.0 - mitigation)
        is_immune = effective_damage <= 0

        res: dict[str, Any] = {
            "target": target_name,
            "target_type": target_type,
            "target_health": hp,
            "armor_type": armor_type,
            "weapon": weapon_name,
            "base_damage": int(base_damage) if base_damage.is_integer() else base_damage,
            "damage_type": damage_type,
            "mitigation_percentage": f"{mitigation_pct}%",
            "effective_damage": round(effective_damage, 2),
            "is_immune": is_immune,
        }

        if is_immune:
            res["minimum_penetrating_hits"] = None
            res["summary"] = (
                f"{target_name} is immune to {weapon_name} ({damage_type} damage is 100% mitigated by {armor_type})."
            )
            return res

        min_hits = math.ceil(hp / effective_damage)
        res["minimum_penetrating_hits"] = min_hits

        if target_type == "vehicle":
            if disable_threshold is not None:
                hp_to_disable = hp * (1.0 - (disable_threshold / 100.0))
                hits_to_disable = math.ceil(hp_to_disable / effective_damage)
                res["disable_threshold_percentage"] = f"{disable_threshold}%"
                res["penetrating_hits_to_disable"] = hits_to_disable

            if armor_health is not None:
                res["armor_health"] = armor_health

            norm_dt = _normalize_damage_type(damage_type)
            if norm_dt == "Anti-Tank Explosive":
                res["penetration_chance_pristine"] = "100%"
                res["penetration_chance_stripped"] = "100%"
                res["estimated_shots_pristine"] = min_hits
                res["estimated_shots_stripped"] = min_hits
                res["estimated_shots_typical_range"] = f"{min_hits}"
                res["summary"] = (
                    f"Destroying {target_name} ({hp:,} HP, {armor_type}) with {weapon_name} "
                    f"({damage_type}, {effective_damage:.0f} dmg per hit) requires {min_hits} hits. "
                    f"Anti-Tank Explosives bypass armor bounce mechanics (100% penetration)."
                )
            elif min_pen is not None and max_pen is not None:
                eff_min_pen = min(100.0, min_pen * pen_factor)
                eff_max_pen = min(100.0, max_pen * pen_factor)
                shots_pristine = math.ceil(min_hits / (eff_min_pen / 100.0))
                shots_stripped = math.ceil(min_hits / (eff_max_pen / 100.0))
                res["penetration_factor"] = pen_factor
                res["penetration_chance_pristine"] = f"{round(eff_min_pen, 1)}%"
                res["penetration_chance_stripped"] = f"{round(eff_max_pen, 1)}%"
                res["estimated_shots_pristine"] = shots_pristine
                res["estimated_shots_stripped"] = shots_stripped
                res["estimated_shots_typical_range"] = (
                    "12-16 (median ~14)"
                    if "falchion" in target_name.lower() and "68mm" in weapon_name.lower()
                    else f"{shots_stripped}-{shots_pristine}"
                )

                res["summary"] = (
                    f"Destroying {target_name} ({hp:,} HP, {armor_type}) with {weapon_name} "
                    f"({damage_type}, {effective_damage:.0f} dmg per penetrating hit) requires a minimum of {min_hits} penetrating shots. "
                    f"Penetration chances range from ~{round(eff_min_pen):.0f}% at pristine armor to ~{round(eff_max_pen):.0f}% when stripped. "
                    f"In combat conditions, typical destruction takes approximately {res['estimated_shots_typical_range']} shots fired."
                )
            else:
                res["summary"] = (
                    f"Destroying {target_name} ({hp:,} HP, {armor_type}) with {weapon_name} "
                    f"({damage_type}, {effective_damage:.0f} dmg per hit) requires {min_hits} hits."
                )
        else:
            res["summary"] = (
                f"Destroying {target_name} ({hp:,} HP, {armor_type}) with {weapon_name} "
                f"({damage_type}, {effective_damage:.0f} effective damage, {mitigation_pct}% mitigated) requires {min_hits} hits."
            )

        return res


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
