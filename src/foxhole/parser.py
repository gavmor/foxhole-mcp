"""Parser for extracting structured game statistics from Foxhole wikitext."""

import re
from typing import Any

import wikitextparser as wtp

from foxhole.models import (
    Armament,
    ItemStats,
    PageContent,
    ProductionRecipe,
    StructureStats,
    VehicleStats,
)


def _safe_int(val: Any) -> int | None:
    """Safely parse an integer from wikitext value."""
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    # Match first integer sequence if any
    m = re.search(r"[-+]?\d+", s)
    if m:
        try:
            return int(m.group(0))
        except ValueError:
            return None
    return None


def _safe_float(val: Any) -> float | None:
    """Safely parse a float from wikitext value."""
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    m = re.search(r"[-+]?\d+(?:\.\d+)?", s)
    if m:
        try:
            return float(m.group(0))
        except ValueError:
            return None
    return None


def _clean_wiki_markup(text: str) -> str:
    """Remove wikitext artifacts, templates, and links while preserving plain readable text."""
    if not text:
        return ""

    # Replace [[Target|Label]] -> Label, and [[Target]] -> Target
    cleaned = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", text)

    # Remove {{Disp|...}}, {{Key|...}}, etc., keeping inner meaningful text if helpful
    cleaned = re.sub(r"\{\{Disp\|([^|}]+)[^}]*\}\}", r"\1", cleaned)
    cleaned = re.sub(r"\{\{DamageTypeDisp\|([^|}]+)[^}]*\}\}", r"\1", cleaned)
    cleaned = re.sub(r"\{\{VehicleDisp\|([^|}]+)[^}]*\}\}", r"\1", cleaned)
    cleaned = re.sub(r"\{\{[^}]+\}\}", "", cleaned)

    # Remove HTML tags & comments
    cleaned = re.sub(r"<!--.*?-->", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<[^>]+>", "", cleaned)

    # Remove bold/italic markup
    cleaned = re.sub(r"'{2,5}", "", cleaned)

    # Clean up whitespace
    lines = [line.strip() for line in cleaned.splitlines()]
    clean_lines = [line for line in lines if line]
    return "\n".join(clean_lines)


def _get_infobox_args(
    parsed: wtp.WikiText, infobox_name_pattern: str
) -> tuple[str | None, dict[str, str]]:
    """Locate matching infobox template and return its name and dict of key-value arguments."""
    pattern = re.compile(infobox_name_pattern, re.IGNORECASE)
    for template in parsed.templates:
        t_name = template.name.strip()
        if pattern.search(t_name):
            args: dict[str, str] = {}
            for arg in template.arguments:
                arg_name = arg.name.strip()
                arg_val = arg.value.strip()
                args[arg_name] = arg_val
            return t_name, args
    return None, {}


def _extract_quote(parsed: wtp.WikiText) -> str | None:
    """Extract the in-game quote if available."""
    for template in parsed.templates:
        if template.name.strip().lower() == "quote":
            if template.arguments:
                val = template.arguments[0].value.strip()
                return _clean_wiki_markup(val)
    return None


def _extract_armaments(args: dict[str, str]) -> list[Armament]:
    """Extract all A1_, A2_, A3_ armaments from infobox arguments."""
    armaments: list[Armament] = []
    # Up to 6 armament slots
    for i in range(1, 7):
        name_key = f"A{i}_ArmamentName"
        if args.get(name_key):
            armaments.append(
                Armament(
                    name=args[name_key],
                    ammo=args.get(f"A{i}_AmmoName"),
                    reload_time=_safe_float(args.get(f"A{i}_ReloadTime")),
                    firing_time=_safe_float(args.get(f"A{i}_FiringTime")),
                    range_max=args.get(f"A{i}_RangeMax"),
                    range_effective=args.get(f"A{i}_RangeEffective"),
                    fire_rate=_safe_float(args.get(f"A{i}_FireRate")),
                    magazine_size=_safe_int(args.get(f"A{i}_MagazineSize")),
                    traverse=args.get(f"A{i}_Traverse"),
                    firing_arc=args.get(f"A{i}_FiringArc"),
                )
            )
    return armaments


def _extract_production_recipes(args: dict[str, str]) -> list[ProductionRecipe]:
    """Extract PRD1_, PRD2_, PRD3_ production recipes from infobox arguments."""
    recipes: list[ProductionRecipe] = []
    for i in range(1, 6):
        source_key = f"PRD{i}_Source"
        if source_key not in args or not args[source_key]:
            continue

        source = args[source_key]
        category = args.get(f"PRD{i}_ProductionCategory")
        inputs: dict[str, int] = {}

        # Look for up to 6 input items
        for j in range(1, 7):
            item_key = f"PRD{i}_InputItem{j}"
            amt_key = f"PRD{i}_InputItem{j}Amount"
            if args.get(item_key):
                amt = _safe_int(args.get(amt_key)) or 1
                inputs[args[item_key]] = amt

        input_vehicle = args.get(f"PRD{i}_InputVehicle")
        input_power = _safe_float(args.get(f"PRD{i}_InputPower"))
        output_amount = _safe_int(args.get(f"PRD{i}_OutputAmount"))
        production_time = _safe_float(args.get(f"PRD{i}_ProductionTime"))
        is_mpfable = args.get(f"PRD{i}_IsMPFable") == "1"

        recipes.append(
            ProductionRecipe(
                source=source,
                category=category,
                inputs=inputs,
                input_vehicle=input_vehicle,
                input_power=input_power,
                output_amount=output_amount,
                production_time_sec=production_time,
                is_mpfable=is_mpfable,
            )
        )
    return recipes


def parse_vehicle(title: str, wikitext: str) -> VehicleStats | None:
    """Parse vehicle specifications from wikitext."""
    parsed = wtp.parse(wikitext)
    _, args = _get_infobox_args(parsed, r"Vehicle\s+Infobox")
    if not args:
        return None

    name = args.get("name", title)
    quote = _extract_quote(parsed)

    # Subsystems
    subsystems: dict[str, float] = {}
    for key, label in [
        ("disable_chance_tracks", "tracks"),
        ("disable_chance_fueltank", "fuel_tank"),
        ("disable_chance_turret", "turret"),
        ("disable_chance_turret2", "turret_secondary"),
    ]:
        val = _safe_float(args.get(key))
        if val is not None:
            subsystems[label] = val

    # Faction normalization
    faction = args.get("faction", "Both")
    if faction.lower() in ("war", "warden"):
        faction = "Warden"
    elif faction.lower() in ("col", "colonial"):
        faction = "Colonial"

    return VehicleStats(
        name=name,
        codename=args.get("codename"),
        faction=faction,
        vehicle_type=args.get("type"),
        health=_safe_int(args.get("vehicle_hp")),
        armor_type=args.get("armour_type"),
        armor_health=_safe_int(args.get("armour_hp")),
        min_pen_chance=_safe_float(args.get("min_pen_chance")),
        max_pen_chance=_safe_float(args.get("max_pen_chance")),
        disable_threshold=_safe_float(args.get("disable")),
        disable_subsystems=subsystems,
        repair_bmats=_safe_int(args.get("repair")),
        crew=_safe_int(args.get("crew")),
        passengers=_safe_int(args.get("passengers")),
        inventory_slots=_safe_int(args.get("slots")),
        fuel_capacity=_safe_float(args.get("fuelcap")),
        fuel_rate=_safe_float(args.get("fuelrate")),
        speed_on_road=_safe_float(args.get("speed")),
        speed_off_road=_safe_float(args.get("offspeed")),
        armaments=_extract_armaments(args),
        production=_extract_production_recipes(args),
        description=quote,
        wiki_url=f"https://foxhole.wiki.gg/wiki/{title.replace(' ', '_')}",
    )


def parse_item(title: str, wikitext: str) -> ItemStats | None:
    """Parse item, weapon, or ammunition specifications from wikitext."""
    parsed = wtp.parse(wikitext)
    _, args = _get_infobox_args(parsed, r"Item\s+Infobox")
    if not args:
        return None

    name = args.get("name", title)
    quote = _extract_quote(parsed)

    faction = args.get("faction", "Both")
    if faction.lower() in ("war", "warden"):
        faction = "Warden"
    elif faction.lower() in ("col", "colonial"):
        faction = "Colonial"

    return ItemStats(
        name=name,
        codename=args.get("codename"),
        faction=faction,
        item_type=args.get("type"),
        category=args.get("category") or args.get("ItemCategory"),
        equipment_slot=args.get("EquipmentSlot") or args.get("slot"),
        damage=args.get("damage"),
        damage_type=args.get("damage_type"),
        fire_rate=_safe_float(args.get("fire_rate")),
        range_effective=_safe_float(args.get("range_effective")),
        range_max=_safe_float(args.get("range_max")),
        magazine=_safe_int(args.get("magazine")),
        reload_time=_safe_float(args.get("reload")),
        ammo=args.get("ammo"),
        crate_amount=_safe_int(args.get("crate_amount")),
        encumbrance=_safe_float(args.get("encumbrance")),
        production=_extract_production_recipes(args),
        description=quote,
        wiki_url=f"https://foxhole.wiki.gg/wiki/{title.replace(' ', '_')}",
    )


def parse_structure(title: str, wikitext: str) -> StructureStats | None:
    """Parse structure specifications from wikitext."""
    parsed = wtp.parse(wikitext)
    _, args = _get_infobox_args(parsed, r"Structure\s+Infobox")
    if not args:
        return None

    name = args.get("name", title)
    quote = _extract_quote(parsed)

    faction = args.get("faction", "Both")
    if faction.lower() in ("war", "warden"):
        faction = "Warden"
    elif faction.lower() in ("col", "colonial"):
        faction = "Colonial"

    return StructureStats(
        name=name,
        codename=args.get("codename"),
        faction=faction,
        structure_type=args.get("type") or args.get("construction_type"),
        health=_safe_int(args.get("structure_hp")),
        armor_type=args.get("armour_type"),
        decay_duration=_safe_float(args.get("decay_duration")),
        repair_cost=_safe_int(args.get("repair")),
        armaments=_extract_armaments(args),
        production=_extract_production_recipes(args),
        description=quote,
        wiki_url=f"https://foxhole.wiki.gg/wiki/{title.replace(' ', '_')}",
    )


def parse_page_content(title: str, wikitext: str) -> PageContent:
    """Parse general article wikitext into structured overview and clean text sections."""
    parsed = wtp.parse(wikitext)
    quote = _extract_quote(parsed)

    # Detect infobox type if present
    infobox_type = None
    structured_args = None
    for template in parsed.templates:
        t_name = template.name.strip()
        if "infobox" in t_name.lower():
            infobox_type = t_name
            structured_args = {arg.name.strip(): arg.value.strip() for arg in template.arguments}
            break

    # Extract clean text sections
    sections: dict[str, str] = {}
    for section in parsed.sections:
        s_title = section.title.strip() if section.title else "Overview"
        # Skip edit or nav sections
        if s_title.lower() in ("see also", "references", "external links", "gallery"):
            continue
        clean_sec = _clean_wiki_markup(section.contents)
        if clean_sec:
            sections[s_title] = clean_sec

    summary = quote or ""
    if not summary and "Overview" in sections:
        # First non-empty paragraph of Overview
        paras = sections["Overview"].split("\n\n")
        if paras:
            summary = paras[0]

    return PageContent(
        title=title,
        summary=summary,
        infobox_type=infobox_type,
        structured_data=structured_args,
        sections=sections,
        wiki_url=f"https://foxhole.wiki.gg/wiki/{title.replace(' ', '_')}",
    )
