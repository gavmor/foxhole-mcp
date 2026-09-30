"""Pydantic data models for structured Foxhole game entities."""

from typing import Any

from pydantic import BaseModel, Field


class ProductionRecipe(BaseModel):
    """Manufacturing recipe for vehicles, weapons, structures, or items."""

    source: str = Field(
        description="Production facility or building (e.g., Factory, Garage, Small Assembly Station)"
    )
    category: str | None = Field(
        default=None, description="Category queue within the production source"
    )
    inputs: dict[str, int] = Field(
        default_factory=dict, description="Input materials and required quantities"
    )
    input_vehicle: str | None = Field(
        default=None, description="Base vehicle chassis required for facility conversion"
    )
    input_power: float | None = Field(
        default=None, description="Power requirement in MegaWatts (MW)"
    )
    output_amount: int | None = Field(
        default=None, description="Number of items or crates produced"
    )
    production_time_sec: float | None = Field(
        default=None, description="Production time in seconds"
    )
    is_mpfable: bool = Field(
        default=False, description="Whether item can be produced in the Mass Production Factory"
    )


class Armament(BaseModel):
    """Weapon or defensive system mounted on a vehicle or structure."""

    name: str = Field(description="Name of the weapon/armament")
    ammo: str | None = Field(default=None, description="Compatible ammunition type")
    reload_time: float | None = Field(default=None, description="Reload time in seconds")
    firing_time: float | None = Field(default=None, description="Firing cycle duration in seconds")
    range_max: str | None = Field(default=None, description="Maximum engagement range in meters")
    range_effective: str | None = Field(default=None, description="Effective range in meters")
    fire_rate: float | None = Field(default=None, description="Rate of fire (rounds per minute)")
    magazine_size: int | None = Field(
        default=None, description="Ammo capacity in the weapon magazine"
    )
    traverse: str | None = Field(default=None, description="Turret or gun traverse degrees/arc")
    firing_arc: str | None = Field(default=None, description="Horizontal firing arc in degrees")


class VehicleStats(BaseModel):
    """Parsed specifications and combat profile for Foxhole vehicles."""

    name: str = Field(description="Vehicle display name")
    codename: str | None = Field(default=None, description="Internal code name")
    faction: str = Field(default="Both", description="Faction alignment: Warden, Colonial, or Both")
    vehicle_type: str | None = Field(
        default=None, description="Vehicle classification (e.g., Assault Tank, Truck)"
    )
    health: int | None = Field(default=None, description="Base vehicle hit points (HP)")
    armor_type: str | None = Field(
        default=None, description="Armor damage classification (e.g., Tier2Tank, LightVehicle)"
    )
    armor_health: int | None = Field(
        default=None, description="Armor hit points before degradation"
    )
    min_pen_chance: float | None = Field(
        default=None, description="Minimum penetration chance percentage (at max armor)"
    )
    max_pen_chance: float | None = Field(
        default=None, description="Maximum penetration chance percentage (at 0 armor)"
    )
    disable_threshold: float | None = Field(
        default=None, description="HP percentage threshold where vehicle becomes disabled"
    )
    disable_subsystems: dict[str, float] = Field(
        default_factory=dict, description="Subsystem disable chances (tracks, turret, fuel)"
    )
    repair_bmats: int | None = Field(
        default=None, description="Basic Materials required to repair from 0 to 100% HP"
    )
    crew: int | None = Field(default=None, description="Crew required to operate all primary roles")
    passengers: int | None = Field(default=None, description="Passenger seating capacity")
    inventory_slots: int | None = Field(default=None, description="Inventory cargo slot count")
    fuel_capacity: float | None = Field(default=None, description="Fuel tank capacity (liters)")
    fuel_rate: float | None = Field(
        default=None, description="Fuel consumption rate (liters per kilometer)"
    )
    speed_on_road: float | None = Field(default=None, description="Top forward speed on road (m/s)")
    speed_off_road: float | None = Field(
        default=None, description="Top forward speed off-road (m/s)"
    )
    armaments: list[Armament] = Field(
        default_factory=list, description="Mounted weapons and defensive turrets"
    )
    production: list[ProductionRecipe] = Field(
        default_factory=list, description="Production recipes and costs"
    )
    description: str | None = Field(default=None, description="In-game quote or overview")
    wiki_url: str = Field(description="URL to the Foxhole wiki page")


class ItemStats(BaseModel):
    """Parsed specifications for infantry weapons, equipment, tools, and ammunition."""

    name: str = Field(description="Item display name")
    codename: str | None = Field(default=None, description="Internal code name")
    faction: str = Field(default="Both", description="Faction alignment: Warden, Colonial, or Both")
    item_type: str | None = Field(
        default=None, description="Item type (e.g., Rifle, Shell, Grenade)"
    )
    category: str | None = Field(
        default=None, description="Category (e.g., Small Arms, Heavy Ammunition)"
    )
    equipment_slot: str | None = Field(
        default=None, description="Equipped slot: Primary, Secondary, Third, or Large Item"
    )
    damage: str | None = Field(default=None, description="Base damage output or damage range")
    damage_type: str | None = Field(
        default=None, description="Damage type (e.g., Light Kinetic, Heavy Kinetic, Explosive)"
    )
    fire_rate: float | None = Field(default=None, description="Rounds per minute")
    range_effective: float | None = Field(default=None, description="Effective range in meters")
    range_max: float | None = Field(default=None, description="Maximum reach in meters")
    magazine: int | None = Field(default=None, description="Magazine capacity")
    reload_time: float | None = Field(default=None, description="Reload duration in seconds")
    ammo: str | None = Field(default=None, description="Required ammunition type")
    crate_amount: int | None = Field(
        default=None, description="Units contained in a single logistical crate"
    )
    encumbrance: float | None = Field(default=None, description="Weight/encumbrance percentage")
    production: list[ProductionRecipe] = Field(
        default_factory=list, description="Manufacturing recipes"
    )
    description: str | None = Field(default=None, description="In-game quote or overview")
    wiki_url: str = Field(description="URL to the Foxhole wiki page")


class StructureStats(BaseModel):
    """Parsed specifications for player-built defenses, bases, and world structures."""

    name: str = Field(description="Structure display name")
    codename: str | None = Field(default=None, description="Internal code name")
    faction: str = Field(default="Both", description="Faction alignment: Warden, Colonial, or Both")
    structure_type: str | None = Field(
        default=None, description="Structure type (e.g., Player Manned Defense, Bunker)"
    )
    health: int | None = Field(default=None, description="Structure hit points (HP)")
    armor_type: str | None = Field(
        default=None, description="Armor damage classification (e.g., Tier3Structure)"
    )
    decay_duration: float | None = Field(
        default=None, description="Decay duration in hours without supply"
    )
    repair_cost: int | None = Field(default=None, description="Repair cost in Basic Materials")
    armaments: list[Armament] = Field(
        default_factory=list, description="Mounted weapons and artillery"
    )
    production: list[ProductionRecipe] = Field(
        default_factory=list, description="Construction and upgrade costs"
    )
    description: str | None = Field(default=None, description="In-game quote or overview")
    wiki_url: str = Field(description="URL to the Foxhole wiki page")


class SearchResult(BaseModel):
    """Article match from Foxhole wiki search."""

    title: str = Field(description="Article title")
    pageid: int = Field(description="MediaWiki internal page ID")
    snippet: str = Field(description="Search snippet with context")
    wordcount: int = Field(description="Total word count of the article")
    url: str = Field(description="Direct URL to the article on foxhole.wiki.gg")


class PageContent(BaseModel):
    """General article content with clean section text and extracted structured metadata."""

    title: str = Field(description="Article title")
    summary: str = Field(description="Brief summary or in-game description")
    infobox_type: str | None = Field(default=None, description="Detected infobox type, if present")
    structured_data: dict[str, Any] | None = Field(
        default=None, description="Extracted template fields"
    )
    sections: dict[str, str] = Field(
        default_factory=dict, description="Clean markdown/text by section header"
    )
    wiki_url: str = Field(description="Direct URL to the article on foxhole.wiki.gg")
