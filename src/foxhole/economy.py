"""Precompiled Curried Leontief Input-Output Economy Solver for Foxhole.

Curries the full Foxhole technical coefficients matrix A at compile/initialization time
and precomputes the Leontief multiplier matrix L = (I - A)^(-1).
Enables O(N^2) instantaneous Bill of Materials (BOM) solving for arbitrary production demand.
"""

from __future__ import annotations

import difflib
import math
import re
from enum import StrEnum

import numpy as np
from pydantic import BaseModel, Field


class ItemCategory(StrEnum):
    """Broad classification of Foxhole commodities and sector outputs."""

    RAW_RESOURCE = "raw_resource"
    REFINED_MATERIAL = "refined_material"
    FACILITY_MATERIAL = "facility_material"
    VEHICLE = "vehicle"
    SMALL_ARMS = "small_arms"
    AMMUNITION = "ammunition"
    STRUCTURE = "structure"


class ItemDefinition(BaseModel):
    """Complete specification of a sector good or recipe in the Foxhole economy."""

    name: str = Field(description="Canonical display name of the item or vehicle")
    category: ItemCategory = Field(description="Category of economic good")
    inputs: dict[str, float] = Field(
        default_factory=dict,
        description="Direct recipe inputs: {input_item_name: units_required_per_unit_output}",
    )
    facility_type: str | None = Field(
        default=None,
        description="Production building/facility (e.g. Garage, Small Assembly Station, Refinery)",
    )
    crafting_time_sec: float | None = Field(
        default=None,
        description="Cycle production time in seconds",
    )
    yield_per_craft: float = Field(
        default=1.0,
        description="Units produced per cycle",
    )
    description: str | None = Field(
        default=None,
        description="Brief flavor or gameplay context",
    )
    crate_size: int | None = Field(
        default=None,
        description="Units per crate when the facility only produces whole crates",
    )


# ---------------------------------------------------------------------------
# Canonical Foxhole Economy Recipe Graph
# ---------------------------------------------------------------------------

ECONOMY_REGISTRY: dict[str, ItemDefinition] = {
    # --- Raw Resources (Tier 0: Extracted from world/fields, 0 recipe inputs) ---
    "Salvage": ItemDefinition(
        name="Salvage",
        category=ItemCategory.RAW_RESOURCE,
        description="Primary raw scrap gathered from Salvage Fields and Mines",
    ),
    "Components": ItemDefinition(
        name="Components",
        category=ItemCategory.RAW_RESOURCE,
        description="Metallic component scrap used for high-tier metallurgy and armor",
    ),
    "Sulfur": ItemDefinition(
        name="Sulfur",
        category=ItemCategory.RAW_RESOURCE,
        description="Raw chemical element extracted for explosives and propellants",
    ),
    "Coal": ItemDefinition(
        name="Coal",
        category=ItemCategory.RAW_RESOURCE,
        description="Raw fossil fuel used for coke, blast furnaces, and facility power",
    ),
    "Crude Oil": ItemDefinition(
        name="Crude Oil",
        category=ItemCategory.RAW_RESOURCE,
        description="Unrefined petroleum extracted from oil wells",
    ),
    "Water": ItemDefinition(
        name="Water",
        category=ItemCategory.RAW_RESOURCE,
        description="Liquid resource pumped from lakes or wells for facility processing",
    ),
    # --- Refined Primary & Fuel Materials (Tier 1: Refinery / Fuel Stations) ---
    "Basic Materials": ItemDefinition(
        name="Basic Materials",
        category=ItemCategory.REFINED_MATERIAL,
        inputs={"Salvage": 2.0},  # Refinery ratio: 100 Salvage -> 50 Bmats (2:1)
        facility_type="Refinery",
        crafting_time_sec=1.0,
        description="Universal construction and manufacturing commodity (Bmats)",
    ),
    "Refined Materials": ItemDefinition(
        name="Refined Materials",
        category=ItemCategory.REFINED_MATERIAL,
        inputs={"Components": 20.0},  # Refinery ratio: 100 Components -> 5 Rmats (20:1)
        facility_type="Refinery",
        crafting_time_sec=4.0,
        description="Hardened alloy ingots for combat vehicles and heavy equipment (Rmats)",
    ),
    "Explosive Powder": ItemDefinition(
        name="Explosive Powder",
        category=ItemCategory.REFINED_MATERIAL,
        inputs={"Salvage": 5.0},  # Refinery ratio: 10 Salvage -> 2 Emats (5:1)
        facility_type="Refinery",
        crafting_time_sec=2.0,
        description="Explosive propellant for shells, grenades, and charges (Emats)",
    ),
    "Heavy Explosive Powder": ItemDefinition(
        name="Heavy Explosive Powder",
        category=ItemCategory.REFINED_MATERIAL,
        inputs={"Sulfur": 20.0},  # Refinery ratio: 100 Sulfur -> 5 HEmats (20:1)
        facility_type="Refinery",
        crafting_time_sec=5.0,
        description="High-yield chemical explosive for heavy artillery and demolition (HEmats)",
    ),
    "Diesel": ItemDefinition(
        name="Diesel",
        category=ItemCategory.REFINED_MATERIAL,
        inputs={"Salvage": 10.0},
        facility_type="Refinery",
        crafting_time_sec=2.0,
        description="Common vehicle and generator fuel",
    ),
    "Heavy Oil": ItemDefinition(
        name="Heavy Oil",
        category=ItemCategory.RAW_RESOURCE,
        inputs={"Crude Oil": 1.0},
        facility_type="Oil Refinery",
        crafting_time_sec=10.0,
        description="Dense petroleum derivative used in facility manufacturing",
    ),
    "Petrol": ItemDefinition(
        name="Petrol",
        category=ItemCategory.REFINED_MATERIAL,
        inputs={"Crude Oil": 1.0},
        facility_type="Oil Refinery",
        crafting_time_sec=8.0,
        description="High-octane fuel for heavy tanks, speed boosts, and stationary engines",
    ),
    "Coke": ItemDefinition(
        name="Coke",
        category=ItemCategory.RAW_RESOURCE,
        inputs={"Coal": 1.14},  # 200 Coal -> 175 Coke (~1.14 Coal per Coke)
        facility_type="Coal Liquefier",
        crafting_time_sec=15.0,
        description="Carbonaceous fuel derived from baked coal for blast furnaces",
    ),
    # --- Facility Advanced Materials (Tier 2: Factories & Smelters) ---
    "Construction Materials": ItemDefinition(
        name="Construction Materials",
        category=ItemCategory.FACILITY_MATERIAL,
        inputs={"Salvage": 10.0},
        facility_type="Materials Factory",
        crafting_time_sec=25.0,
        description="Fundamental building blocks for facility modifications and pads (Cmats)",
    ),
    "Processed Construction Materials": ItemDefinition(
        name="Processed Construction Materials",
        category=ItemCategory.FACILITY_MATERIAL,
        inputs={"Construction Materials": 1.0, "Components": 3.0, "Heavy Oil": 2.0},
        facility_type="Metalworks Factory",
        crafting_time_sec=50.0,
        description="High-strength composite plating for armor upgrades and heavy facilities (PCmats)",
    ),
    "Steel Construction Materials": ItemDefinition(
        name="Steel Construction Materials",
        category=ItemCategory.FACILITY_MATERIAL,
        inputs={"Processed Construction Materials": 2.0, "Coke": 3.0, "Heavy Oil": 1.0},
        facility_type="Blast Furnace",
        crafting_time_sec=90.0,
        description="Ultra-grade forged steel for super tanks and battle tanks (Steel)",
    ),
    "Assembly Materials I": ItemDefinition(
        name="Assembly Materials I",
        category=ItemCategory.FACILITY_MATERIAL,
        inputs={"Construction Materials": 1.0, "Heavy Oil": 1.0},
        facility_type="Materials Factory",
        crafting_time_sec=30.0,
        description="Tier 1 assembly kit for light vehicle variants",
    ),
    "Assembly Materials II": ItemDefinition(
        name="Assembly Materials II",
        category=ItemCategory.FACILITY_MATERIAL,
        inputs={"Construction Materials": 1.0, "Coke": 1.0},
        facility_type="Materials Factory",
        crafting_time_sec=30.0,
        description="Tier 2 assembly kit for utility variants",
    ),
    "Assembly Materials III": ItemDefinition(
        name="Assembly Materials III",
        category=ItemCategory.FACILITY_MATERIAL,
        inputs={"Processed Construction Materials": 1.0, "Coke": 1.0},
        facility_type="Metalworks Factory",
        crafting_time_sec=45.0,
        description="Tier 3 assembly kit for medium combat chassis",
    ),
    "Assembly Materials IV": ItemDefinition(
        name="Assembly Materials IV",
        category=ItemCategory.FACILITY_MATERIAL,
        inputs={"Processed Construction Materials": 1.0, "Heavy Oil": 1.0},
        facility_type="Metalworks Factory",
        crafting_time_sec=45.0,
        description="Tier 4 assembly kit for advanced assault tanks and heavy modifications",
    ),
    "Assembly Materials V": ItemDefinition(
        name="Assembly Materials V",
        category=ItemCategory.FACILITY_MATERIAL,
        inputs={"Steel Construction Materials": 1.0, "Heavy Oil": 1.0},
        facility_type="Blast Furnace",
        crafting_time_sec=60.0,
        description="Tier 5 assembly kit for Super Heavy Tanks and capital components",
    ),
    "Pipe": ItemDefinition(
        name="Pipe",
        category=ItemCategory.FACILITY_MATERIAL,
        inputs={"Construction Materials": 3.0},
        facility_type="Materials Factory",
        crafting_time_sec=20.0,
        description="Fluid conduit segment for logistics plumbing",
    ),
    # --- Motorcycles & Scouts ---
    "03MM “Caster”": ItemDefinition(
        name="03MM “Caster”",
        category=ItemCategory.VEHICLE,
        inputs={"Basic Materials": 85.0},
        facility_type="Garage",
        crafting_time_sec=30.0,
        description="Colonial standard motorcycle with passenger sidecar",
    ),
    "00T “Bourne”": ItemDefinition(
        name="00T “Bourne”",
        category=ItemCategory.VEHICLE,
        inputs={"Basic Materials": 85.0},
        facility_type="Garage",
        crafting_time_sec=30.0,
        description="Warden standard motorcycle with passenger sidecar",
    ),
    "00MS “Stinger”": ItemDefinition(
        name="00MS “Stinger”",
        category=ItemCategory.VEHICLE,
        inputs={"03MM “Caster”": 1.0, "Construction Materials": 5.0},
        facility_type="Small Assembly Station",
        crafting_time_sec=180.0,
        description="Colonial bike-mounted machine gun variant armed with 7.92mm MG",
    ),
    "Kivela Power Wheel 80-1": ItemDefinition(
        name="Kivela Power Wheel 80-1",
        category=ItemCategory.VEHICLE,
        inputs={"00T “Bourne”": 1.0, "Construction Materials": 5.0},
        facility_type="Small Assembly Station",
        crafting_time_sec=180.0,
        description="Warden rugged all-terrain motorcycle variant",
    ),
    # --- Logistics & Utility Vehicles ---
    "Dunne Transport": ItemDefinition(
        name="Dunne Transport",
        category=ItemCategory.VEHICLE,
        inputs={"Basic Materials": 100.0},
        facility_type="Garage",
        crafting_time_sec=40.0,
        description="Warden standard cargo truck",
    ),
    "R-1 Hauler": ItemDefinition(
        name="R-1 Hauler",
        category=ItemCategory.VEHICLE,
        inputs={"Basic Materials": 100.0},
        facility_type="Garage",
        crafting_time_sec=40.0,
        description="Colonial standard cargo truck",
    ),
    "Dunne Fuel Tanker": ItemDefinition(
        name="Dunne Fuel Tanker",
        category=ItemCategory.VEHICLE,
        inputs={"Basic Materials": 100.0},
        facility_type="Garage",
        crafting_time_sec=40.0,
        description="Dedicated liquid transport truck",
    ),
    "Dunne Loadlugger 3c": ItemDefinition(
        name="Dunne Loadlugger 3c",
        category=ItemCategory.VEHICLE,
        inputs={"Dunne Transport": 1.0, "Construction Materials": 5.0},
        facility_type="Small Assembly Station",
        crafting_time_sec=180.0,
        description="Warden dump truck variant for loose material hauling",
    ),
    "BMS - Universal Assembly Rig": ItemDefinition(
        name="BMS - Universal Assembly Rig",
        category=ItemCategory.VEHICLE,
        inputs={"Basic Materials": 100.0},
        facility_type="Garage",
        crafting_time_sec=40.0,
        description="Construction Vehicle (CV) for building bases and field infrastructure",
    ),
    "Flatbed Truck": ItemDefinition(
        name="Flatbed Truck",
        category=ItemCategory.VEHICLE,
        inputs={"Basic Materials": 125.0},
        facility_type="Garage",
        crafting_time_sec=50.0,
        description="Heavy logistics platform for shipping containers and field guns",
    ),
    "Crane": ItemDefinition(
        name="Crane",
        category=ItemCategory.VEHICLE,
        inputs={"Basic Materials": 125.0},
        facility_type="Garage",
        crafting_time_sec=50.0,
        description="Mobile crane for loading and unloading shippable objects",
    ),
    "R-5 “Eurystheus” Armoured Car": ItemDefinition(
        name="R-5 “Eurystheus” Armoured Car",
        category=ItemCategory.VEHICLE,
        inputs={"Refined Materials": 35.0},
        facility_type="Garage",
        crafting_time_sec=45.0,
        description="Colonial early-war reconnaissance armored car",
    ),
    # --- Light Tanks & Tankettes ---
    "Devitt Mk. III": ItemDefinition(
        name="Devitt Mk. III",
        category=ItemCategory.VEHICLE,
        inputs={"Refined Materials": 140.0},
        facility_type="Garage",
        crafting_time_sec=60.0,
        description="Warden 40mm light tank chassis",
    ),
    "Devitt Ironhide Mk. IV": ItemDefinition(
        name="Devitt Ironhide Mk. IV",
        category=ItemCategory.VEHICLE,
        inputs={
            "Devitt Mk. III": 1.0,
            "Processed Construction Materials": 10.0,
            "Assembly Materials I": 5.0,
        },
        facility_type="Large Assembly Station",
        crafting_time_sec=240.0,
        description="Warden up-armored light tank variant",
    ),
    'H-5 "Hatchet"': ItemDefinition(
        name='H-5 "Hatchet"',
        category=ItemCategory.VEHICLE,
        inputs={"Refined Materials": 140.0},
        facility_type="Garage",
        crafting_time_sec=60.0,
        description="Colonial 40mm light combat tank",
    ),
    'H-8 "Kranesca"': ItemDefinition(
        name='H-8 "Kranesca"',
        category=ItemCategory.VEHICLE,
        inputs={
            'H-5 "Hatchet"': 1.0,
            "Processed Construction Materials": 10.0,
            "Assembly Materials I": 5.0,
        },
        facility_type="Large Assembly Station",
        crafting_time_sec=240.0,
        description="Colonial boosted-engine light tank variant",
    ),
    # --- Medium Tanks & Assault Vehicles ---
    "Silverhand - Mk. IV": ItemDefinition(
        name="Silverhand - Mk. IV",
        category=ItemCategory.VEHICLE,
        inputs={"Refined Materials": 170.0},
        facility_type="Garage",
        crafting_time_sec=90.0,
        description="Warden dual-gun heavy assault tank (40mm turret + 68mm hull)",
    ),
    "Silverhand Chieftain - Mk. VI": ItemDefinition(
        name="Silverhand Chieftain - Mk. VI",
        category=ItemCategory.VEHICLE,
        inputs={
            "Silverhand - Mk. IV": 1.0,
            "Processed Construction Materials": 15.0,
            "Assembly Materials IV": 10.0,
        },
        facility_type="Large Assembly Station",
        crafting_time_sec=300.0,
        description="Warden siege tank armed with 250mm mortar and twin 12.7mm turret",
    ),
    "Silverhand Lordscar - Mk. X": ItemDefinition(
        name="Silverhand Lordscar - Mk. X",
        category=ItemCategory.VEHICLE,
        inputs={
            "Silverhand - Mk. IV": 1.0,
            "Steel Construction Materials": 20.0,
            "Assembly Materials V": 10.0,
        },
        facility_type="Large Assembly Station",
        crafting_time_sec=600.0,
        description="Warden open-top tank destroyer armed with 94.5mm heavy cannon",
    ),
    "Falchion": ItemDefinition(
        name="Falchion",
        category=ItemCategory.VEHICLE,
        inputs={"Refined Materials": 135.0},
        facility_type="Garage",
        crafting_time_sec=75.0,
        description="Colonial mass-production medium tank (MPF bonus chassis)",
    ),
    "Spatha": ItemDefinition(
        name="Spatha",
        category=ItemCategory.VEHICLE,
        inputs={
            "Falchion": 1.0,
            "Processed Construction Materials": 20.0,
            "Assembly Materials IV": 15.0,
        },
        facility_type="Large Assembly Station",
        crafting_time_sec=300.0,
        description="Colonial up-gunned 40mm assault medium tank with high fire rate",
    ),
    "Talos": ItemDefinition(
        name="Talos",
        category=ItemCategory.VEHICLE,
        inputs={
            "Falchion": 1.0,
            "Processed Construction Materials": 25.0,
            "Assembly Materials IV": 20.0,
        },
        facility_type="Large Assembly Station",
        crafting_time_sec=360.0,
        description="Colonial heavy tank conversion fitted with 75mm artillery cannon",
    ),
    "Bardiche - 86K-a": ItemDefinition(
        name="Bardiche - 86K-a",
        category=ItemCategory.VEHICLE,
        inputs={"Refined Materials": 165.0},
        facility_type="Garage",
        crafting_time_sec=80.0,
        description="Colonial heavy brawler tank equipped with 68mm gun and coaxial MG",
    ),
    "Ranseur - 86K-c": ItemDefinition(
        name="Ranseur - 86K-c",
        category=ItemCategory.VEHICLE,
        inputs={
            "Bardiche - 86K-a": 1.0,
            "Processed Construction Materials": 15.0,
            "Assembly Materials IV": 10.0,
        },
        facility_type="Large Assembly Station",
        crafting_time_sec=300.0,
        description="Colonial rocket artillery quad-launcher tank variant",
    ),
    "Gallagher Outlaw Mk. II": ItemDefinition(
        name="Gallagher Outlaw Mk. II",
        category=ItemCategory.VEHICLE,
        inputs={"Refined Materials": 165.0},
        facility_type="Garage",
        crafting_time_sec=75.0,
        description="Warden long-range cruiser tank with boost mechanism",
    ),
    "Gallagher Highwayman Mk. III": ItemDefinition(
        name="Gallagher Highwayman Mk. III",
        category=ItemCategory.VEHICLE,
        inputs={
            "Gallagher Outlaw Mk. II": 1.0,
            "Processed Construction Materials": 15.0,
            "Assembly Materials IV": 10.0,
        },
        facility_type="Large Assembly Station",
        crafting_time_sec=300.0,
        description="Warden flanker tank armed with twin 20mm and 12.7mm turret",
    ),
    # --- Heavy & Super Heavy Tanks ---
    "Flood Juggernaut Mk. VII": ItemDefinition(
        name="Flood Juggernaut Mk. VII",
        category=ItemCategory.VEHICLE,
        inputs={
            "Refined Materials": 200.0,
            "Processed Construction Materials": 35.0,
            "Steel Construction Materials": 20.0,
        },
        facility_type="Large Assembly Station",
        crafting_time_sec=1800.0,
        description="Warden Battle Tank armed with 75mm high-velocity main gun",
    ),
    "Lance-36": ItemDefinition(
        name="Lance-36",
        category=ItemCategory.VEHICLE,
        inputs={
            "Refined Materials": 200.0,
            "Processed Construction Materials": 35.0,
            "Steel Construction Materials": 20.0,
        },
        facility_type="Large Assembly Station",
        crafting_time_sec=1800.0,
        description="Colonial Battle Tank armed with 75mm cannon",
    ),
    "Cullen Predator Mk. III": ItemDefinition(
        name="Cullen Predator Mk. III",
        category=ItemCategory.VEHICLE,
        inputs={
            "Steel Construction Materials": 200.0,
            "Assembly Materials V": 100.0,
            "Processed Construction Materials": 40.0,
        },
        facility_type="Large Assembly Station",
        crafting_time_sec=3600.0,
        description="Warden Super Heavy Tank armed with 94.5mm twin guns and grenade launchers",
    ),
    "Ares": ItemDefinition(
        name="Ares",
        category=ItemCategory.VEHICLE,
        inputs={
            "Steel Construction Materials": 200.0,
            "Assembly Materials V": 100.0,
            "Processed Construction Materials": 40.0,
        },
        facility_type="Large Assembly Station",
        crafting_time_sec=3600.0,
        description="Colonial Super Heavy Tank armed with twin 75mm turret",
    ),
    # --- Artillery & Field Weapons ---
    "Huber Exalt 150mm": ItemDefinition(
        name="Huber Exalt 150mm",
        category=ItemCategory.VEHICLE,
        inputs={"Refined Materials": 175.0},
        facility_type="Garage",
        crafting_time_sec=100.0,
        description="Warden heavy 150mm field artillery piece",
    ),
    "50-500 “Thunderbolt” 150mm": ItemDefinition(
        name="50-500 “Thunderbolt” 150mm",
        category=ItemCategory.VEHICLE,
        inputs={"Refined Materials": 175.0},
        facility_type="Garage",
        crafting_time_sec=100.0,
        description="Colonial heavy 150mm long-range artillery piece",
    ),
    "Balfour Falconu 250mm": ItemDefinition(
        name="Balfour Falconu 250mm",
        category=ItemCategory.VEHICLE,
        inputs={"Refined Materials": 120.0},
        facility_type="Garage",
        crafting_time_sec=75.0,
        description="Warden 250mm siege mortar field carriage",
    ),
    # --- Weapons & Infantry Supplies (Normalized per unit) ---
    "No.2 Loughcaster": ItemDefinition(
        name="No.2 Loughcaster",
        category=ItemCategory.SMALL_ARMS,
        inputs={"Basic Materials": 5.0},  # 100 Bmats per crate of 20
        facility_type="Factory",
        crafting_time_sec=3.0,
        description="Standard issue Warden bolt-action rifle",
    ),
    "Argenti r.II": ItemDefinition(
        name="Argenti r.II",
        category=ItemCategory.SMALL_ARMS,
        inputs={"Basic Materials": 5.0},  # 100 Bmats per crate of 20
        facility_type="Factory",
        crafting_time_sec=3.0,
        description="Standard issue Colonial semi-automatic rifle",
    ),
    "Blakerow 871": ItemDefinition(
        name="Blakerow 871",
        category=ItemCategory.SMALL_ARMS,
        inputs={"Basic Materials": 7.0},  # 140 Bmats per crate of 20
        facility_type="Factory",
        crafting_time_sec=3.5,
        description="Warden rapid-fire carbine",
    ),
    "Fuscina G.I.": ItemDefinition(
        name="Fuscina G.I.",
        category=ItemCategory.SMALL_ARMS,
        inputs={"Basic Materials": 7.0},  # 140 Bmats per crate of 20
        facility_type="Factory",
        crafting_time_sec=3.5,
        description="Colonial 3-round burst assault rifle",
    ),
    "Malone Mk. 9": ItemDefinition(
        name="Malone Mk. 9",
        category=ItemCategory.SMALL_ARMS,
        inputs={
            "Basic Materials": 10.0,
            "Refined Materials": 2.0,
        },  # 100 Bmat + 20 Rmat per crate of 10
        facility_type="Factory",
        crafting_time_sec=5.0,
        description="Warden 12.7mm heavy sustained-fire machine gun",
    ),
    "Gast Machine Gun": ItemDefinition(
        name="Gast Machine Gun",
        category=ItemCategory.SMALL_ARMS,
        inputs={
            "Basic Materials": 10.0,
            "Refined Materials": 2.0,
        },  # 100 Bmat + 20 Rmat per crate of 10
        facility_type="Factory",
        crafting_time_sec=5.0,
        description="Colonial 12.7mm heavy machine gun",
    ),
    "Soldier Supplies": ItemDefinition(
        name="Soldier Supplies",
        category=ItemCategory.SMALL_ARMS,
        inputs={"Basic Materials": 8.0},  # 80 Bmats per crate of 10 (shirts)
        facility_type="Factory",
        crafting_time_sec=4.0,
        description="Spawn tickets (Shirts) required for respawning at bases",
    ),
    "Bandage": ItemDefinition(
        name="Bandage",
        category=ItemCategory.SMALL_ARMS,
        inputs={"Basic Materials": 1.6},  # 80 Bmats per crate of 50
        facility_type="Factory",
        crafting_time_sec=1.5,
        description="First aid item to stop bleeding",
    ),
    "First Aid Kit": ItemDefinition(
        name="First Aid Kit",
        category=ItemCategory.SMALL_ARMS,
        inputs={"Basic Materials": 6.0},  # 60 Bmats per crate of 10
        facility_type="Factory",
        crafting_time_sec=3.0,
        description="Medical pack to heal wounded soldiers",
    ),
    # --- Ammunition (Normalized per unit) ---
    "7.62mm": ItemDefinition(
        name="7.62mm",
        category=ItemCategory.AMMUNITION,
        inputs={"Basic Materials": 2.0},  # 80 Bmats per crate of 40
        facility_type="Factory",
        crafting_time_sec=2.0,
        description="Standard rifle ammunition clips",
    ),
    "7.92mm": ItemDefinition(
        name="7.92mm",
        category=ItemCategory.AMMUNITION,
        inputs={"Basic Materials": 4.0},  # 120 Bmats per crate of 30
        facility_type="Factory",
        crafting_time_sec=2.5,
        description="Submachine gun and light vehicle machine gun ammunition",
    ),
    "12.7mm": ItemDefinition(
        name="12.7mm",
        category=ItemCategory.AMMUNITION,
        inputs={"Basic Materials": 5.0},  # 100 Bmats per crate of 20
        facility_type="Factory",
        crafting_time_sec=3.0,
        description="Heavy machine gun and anti-infantry vehicle ammunition",
    ),
    "40mm": ItemDefinition(
        name="40mm",
        category=ItemCategory.AMMUNITION,
        inputs={
            "Basic Materials": 8.0,
            "Explosive Powder": 6.0,
        },  # 160 Bmat + 120 Emat per crate of 20
        facility_type="Factory",
        crafting_time_sec=4.0,
        description="Standard light and medium combat tank shells",
    ),
    "68mm": ItemDefinition(
        name="68mm",
        category=ItemCategory.AMMUNITION,
        inputs={
            "Basic Materials": 6.0,
            "Explosive Powder": 6.0,
        },  # 120 Bmat + 120 Emat per crate of 20
        facility_type="Factory",
        crafting_time_sec=4.0,
        description="Dedicated anti-tank penetrator shells",
    ),
    "120mm": ItemDefinition(
        name="120mm",
        category=ItemCategory.AMMUNITION,
        inputs={
            "Basic Materials": 12.0,
            "Explosive Powder": 3.0,
        },  # 60 Bmat + 15 Emat per crate of 5
        facility_type="Factory",
        crafting_time_sec=5.0,
        description="Light artillery barrage shells",
    ),
    "150mm": ItemDefinition(
        name="150mm",
        category=ItemCategory.AMMUNITION,
        inputs={
            "Basic Materials": 24.0,
            "Heavy Explosive Powder": 5.0,
        },  # 120 Bmat + 25 HEmat per crate of 5
        facility_type="Factory",
        crafting_time_sec=8.0,
        description="Heavy siege artillery shells",
    ),
    "250mm": ItemDefinition(
        name="250mm",
        category=ItemCategory.AMMUNITION,
        inputs={
            "Basic Materials": 24.0,
            "Heavy Explosive Powder": 25.0,
        },  # 120 Bmat + 125 HEmat per crate of 5
        facility_type="Factory",
        crafting_time_sec=10.0,
        description="Heavy spigot mortar demolition charges",
    ),
    "300mm": ItemDefinition(
        name="300mm",
        category=ItemCategory.AMMUNITION,
        inputs={
            "Basic Materials": 150.0,
            "Heavy Explosive Powder": 30.0,
            "Construction Materials": 10.0,
        },
        facility_type="Large Assembly Station",
        crafting_time_sec=300.0,
        description="Storm Cannon strategic long-range orbital artillery shells",
    ),
    "RPG Shell": ItemDefinition(
        name="RPG Shell",
        category=ItemCategory.AMMUNITION,
        inputs={
            "Basic Materials": 4.0,
            "Explosive Powder": 3.0,
        },  # 60 Bmat + 45 Emat per crate of 15
        facility_type="Factory",
        crafting_time_sec=3.0,
        description="Rocket propelled anti-tank grenade",
    ),
    "Bomba": ItemDefinition(
        name="Bomba",
        category=ItemCategory.AMMUNITION,
        inputs={
            "Basic Materials": 5.0,
            "Explosive Powder": 1.0,
        },  # 100 Bmat + 20 Emat per crate of 20
        facility_type="Factory",
        crafting_time_sec=2.5,
        description="Colonial Bomastone shrapnel grenade",
    ),
    "Harpa": ItemDefinition(
        name="Harpa",
        category=ItemCategory.AMMUNITION,
        inputs={
            "Basic Materials": 5.0,
            "Explosive Powder": 1.0,
        },  # 100 Bmat + 20 Emat per crate of 20
        facility_type="Factory",
        crafting_time_sec=2.5,
        description="Warden A3 Harpa fragmentation grenade",
    ),
    "Mammon 96": ItemDefinition(
        name="Mammon 96",
        category=ItemCategory.AMMUNITION,
        inputs={
            "Basic Materials": 5.0,
            "Explosive Powder": 2.0,
        },  # 100 Bmat + 40 Emat per crate of 20
        facility_type="Factory",
        crafting_time_sec=2.5,
        description="Early war high explosive anti-structure stick grenade",
    ),
    # --- Fortifications & Structures ---
    "Storm Cannon": ItemDefinition(
        name="Storm Cannon",
        category=ItemCategory.STRUCTURE,
        inputs={
            "Basic Materials": 300.0,
            "Refined Materials": 100.0,
            "Construction Materials": 50.0,
        },
        facility_type="Bunker Base Construction",
        crafting_time_sec=3600.0,
        description="Strategic cross-hex super artillery fortification",
    ),
}

# ---------------------------------------------------------------------------
# Common Aliases & Natural Language Search Synonyms
# ---------------------------------------------------------------------------

SYNONYM_MAP: dict[str, str] = {
    # Bike mounted MG & motorcycles
    "bike-mounted machine gun": "00MS “Stinger”",
    "bike mounted machine gun": "00MS “Stinger”",
    "machine gun bike": "00MS “Stinger”",
    "mg bike": "00MS “Stinger”",
    "stinger": "00MS “Stinger”",
    "00ms stinger": "00MS “Stinger”",
    "00ms": "00MS “Stinger”",
    "caster": "03MM “Caster”",
    "03mm caster": "03MM “Caster”",
    "03mm": "03MM “Caster”",
    "motorcycle": "03MM “Caster”",
    "bourne": "00T “Bourne”",
    "00t bourne": "00T “Bourne”",
    "00t": "00T “Bourne”",
    "kivela": "Kivela Power Wheel 80-1",
    "power wheel": "Kivela Power Wheel 80-1",
    # Commodities & Refined Materials
    "bmat": "Basic Materials",
    "bmats": "Basic Materials",
    "basic material": "Basic Materials",
    "rmat": "Refined Materials",
    "rmats": "Refined Materials",
    "refined material": "Refined Materials",
    "emat": "Explosive Powder",
    "emats": "Explosive Powder",
    "explosives": "Explosive Powder",
    "hemat": "Heavy Explosive Powder",
    "hemats": "Heavy Explosive Powder",
    "heavy explosive": "Heavy Explosive Powder",
    "cmat": "Construction Materials",
    "cmats": "Construction Materials",
    "construction material": "Construction Materials",
    "pcmat": "Processed Construction Materials",
    "pcmats": "Processed Construction Materials",
    "processed construction material": "Processed Construction Materials",
    "steel": "Steel Construction Materials",
    "scmat": "Steel Construction Materials",
    "scmats": "Steel Construction Materials",
    "am1": "Assembly Materials I",
    "am i": "Assembly Materials I",
    "assembly materials 1": "Assembly Materials I",
    "am2": "Assembly Materials II",
    "am ii": "Assembly Materials II",
    "assembly materials 2": "Assembly Materials II",
    "am3": "Assembly Materials III",
    "am iii": "Assembly Materials III",
    "assembly materials 3": "Assembly Materials III",
    "am4": "Assembly Materials IV",
    "am iv": "Assembly Materials IV",
    "assembly materials 4": "Assembly Materials IV",
    "am5": "Assembly Materials V",
    "am v": "Assembly Materials V",
    "assembly materials 5": "Assembly Materials V",
    "scrap": "Salvage",
    "comps": "Components",
    "component": "Components",
    "sulphur": "Sulfur",
    "crude": "Crude Oil",
    "oil": "Crude Oil",
    # Vehicles & Tanks
    "spatha": "Spatha",
    "falchion": "Falchion",
    "talos": "Talos",
    "silverhand": "Silverhand - Mk. IV",
    "svh": "Silverhand - Mk. IV",
    "chieftain": "Silverhand Chieftain - Mk. VI",
    "silverhand chieftain": "Silverhand Chieftain - Mk. VI",
    "lordscar": "Silverhand Lordscar - Mk. X",
    "std": "Silverhand Lordscar - Mk. X",
    "devitt": "Devitt Mk. III",
    "ironhide": "Devitt Ironhide Mk. IV",
    "hatchet": 'H-5 "Hatchet"',
    "kranesca": 'H-8 "Kranesca"',
    "outlaw": "Gallagher Outlaw Mk. II",
    "highwayman": "Gallagher Highwayman Mk. III",
    "bardiche": "Bardiche - 86K-a",
    "ranseur": "Ranseur - 86K-c",
    "predator": "Cullen Predator Mk. III",
    "sht": "Cullen Predator Mk. III",
    "ares": "Ares",
    "bt": "Flood Juggernaut Mk. VII",
    "juggernaut": "Flood Juggernaut Mk. VII",
    "lance": "Lance-36",
    # Logistics
    "truck": "Dunne Transport",
    "dunne": "Dunne Transport",
    "hauler": "R-1 Hauler",
    "cv": "BMS - Universal Assembly Rig",
    "construction vehicle": "BMS - Universal Assembly Rig",
    "flatbed": "Flatbed Truck",
    "tanker": "Dunne Fuel Tanker",
    "fuel truck": "Dunne Fuel Tanker",
    # Weapons & Ammo
    "loughcaster": "No.2 Loughcaster",
    "argenti": "Argenti r.II",
    "blakerow": "Blakerow 871",
    "fuscina": "Fuscina G.I.",
    "malone": "Malone Mk. 9",
    "gast": "Gast Machine Gun",
    "mg": "Gast Machine Gun",
    "shirts": "Soldier Supplies",
    "shirt": "Soldier Supplies",
    "ss": "Soldier Supplies",
    "supplies": "Soldier Supplies",
    "mammon": "Mammon 96",
    "he grenade": "Mammon 96",
    "frag": "Harpa",
    "bomastone": "Bomba",
    "boma": "Bomba",
    "storm cannon": "Storm Cannon",
    "sc": "Storm Cannon",
}


# ---------------------------------------------------------------------------
# Pydantic Output Models
# ---------------------------------------------------------------------------


class MachineRequirement(BaseModel):
    """Calculated facility building count needed to satisfy production demand."""

    item: str = Field(description="Item produced by this machine")
    facility_type: str = Field(description="Name/type of facility or station")
    cycle_time_seconds: float = Field(description="Base craft cycle time in seconds")
    fractional_machines: float = Field(description="Exact fractional machine count needed")
    integer_machines: int = Field(description="Ceiling integer machine count needed")


class ProductionPlan(BaseModel):
    """Complete solved bill of materials and factory plan from curried Leontief solver."""

    requested_demand: dict[str, float] = Field(description="Original user query demand")
    resolved_demand: dict[str, float] = Field(
        description="Resolved canonical item names and quantities"
    )
    raw_resources: dict[str, float] = Field(
        description="Primary raw resources required (Salvage, Components, Sulfur, Coal, Crude Oil)"
    )
    refined_materials: dict[str, float] = Field(
        description="Refinery output materials required (Basic Materials, Refined Materials, Explosives)"
    )
    facility_materials: dict[str, float] = Field(
        description="Facility products required (Construction Materials, PCmats, Steel, Assembly Mats)"
    )
    intermediate_goods: dict[str, float] = Field(
        description="Intermediate vehicles, chassis, or equipment consumed internally"
    )
    gross_production: dict[str, float] = Field(
        description="Total gross production vector x across all affected sectors"
    )
    internal_consumption: dict[str, float] = Field(
        description="Internal consumption vector c = Ax consumed by downstream processes"
    )
    machines: dict[str, MachineRequirement] | None = Field(
        default=None,
        description="Required physical machine counts if requested",
    )
    crates: dict[str, int] | None = Field(
        default=None,
        description="Whole crates needed for each demanded item that is produced in crates",
    )
    inventory_used: dict[str, float] | None = Field(
        default=None,
        description="Units drawn from the supplied inventory instead of being produced",
    )
    summary: str = Field(description="Human-readable executive summary of the bill of materials")


# ---------------------------------------------------------------------------
# Curried Leontief Economy Solver
# ---------------------------------------------------------------------------


class CurriedEconomySolver:
    """Precomputes the Leontief multiplier matrix L = (I - A)^(-1) for the Foxhole economy.

    Currying the N x N technical coefficients matrix A into the solver allows solving
    arbitrary bill-of-materials and facility scaling requests in O(N^2) time with
    exact numerical stability.
    """

    def __init__(
        self,
        registry: dict[str, ItemDefinition] | None = None,
        synonyms: dict[str, str] | None = None,
    ) -> None:
        self.registry = registry or ECONOMY_REGISTRY
        # Drop aliases pointing at items this registry does not define
        self.synonyms = {k: v for k, v in (synonyms or SYNONYM_MAP).items() if v in self.registry}

        # 1. Build canonical item list and index mappings
        self.items: list[str] = list(self.registry.keys())
        self.item_to_idx: dict[str, int] = {item: idx for idx, item in enumerate(self.items)}
        self.idx_to_item: dict[int, str] = {idx: item for idx, item in enumerate(self.items)}
        self.n: int = len(self.items)

        # 2. Build Technical Coefficients Matrix A (N x N)
        self.A: np.ndarray = np.zeros((self.n, self.n), dtype=float)
        for out_name, defn in self.registry.items():
            j = self.item_to_idx[out_name]
            for in_name, amount in defn.inputs.items():
                if in_name not in self.item_to_idx:
                    raise ValueError(
                        f"Unknown ingredient '{in_name}' referenced in recipe for '{out_name}'."
                    )
                i = self.item_to_idx[in_name]
                self.A[i, j] = amount

        # 3. Form (I - A) and precalculate the Leontief Inverse L = (I - A)^(-1)
        identity = np.eye(self.n, dtype=float)
        self.M: np.ndarray = identity - self.A

        try:
            self.L: np.ndarray = np.linalg.inv(self.M)
        except np.linalg.LinAlgError as e:
            raise RuntimeError(
                f"Singular Leontief matrix; economy contains an uninvertible loop: {e}"
            ) from e

        # 4. Numerical validation of Hawkins-Simon condition
        if np.any(self.L < -1e-9):
            raise RuntimeError(
                "Hawkins-Simon condition violated: precompiled matrix produced negative multipliers."
            )

        # Clean numerical jitter
        self.L = np.where(np.isclose(self.L, 0.0, atol=1e-12), 0.0, self.L)

        # 5. Pre-index categories for rapid partitioning
        self.raw_items: set[str] = {
            item for item, d in self.registry.items() if d.category == ItemCategory.RAW_RESOURCE
        }
        self.refined_items: set[str] = {
            item for item, d in self.registry.items() if d.category == ItemCategory.REFINED_MATERIAL
        }
        self.facility_items: set[str] = {
            item
            for item, d in self.registry.items()
            if d.category == ItemCategory.FACILITY_MATERIAL
        }

    def _normalize_string(self, text: str) -> str:
        """Strip quotes, hyphens, and whitespace for fuzzy lookup."""
        t = text.lower().strip()
        t = re.sub(r'["\'“”‘’`\-_\.]', " ", t)  # noqa: RUF001
        return " ".join(t.split())

    def resolve_item_name(self, name: str) -> str:
        """Resolve a user-provided name or alias into a canonical economy item name.

        Handles:
        1. Exact canonical name match
        2. Exact synonym match
        3. Normalized punctuation/case match
        4. Substring containment match
        5. Closest fuzzy match via difflib
        """
        # Exact match
        if name in self.item_to_idx:
            return name

        # Strip leading quantity or crate prefixes (e.g. '1 Niska Mk. I' -> 'Niska Mk. I', '40 crates of 12.7mm' -> '12.7mm')
        cleaned = re.sub(r"^\s*\d+\s*(?:crates?\s+of\s+|x\s+)?", "", name, flags=re.I).strip()
        if cleaned and cleaned in self.item_to_idx:
            return cleaned

        # Lowercase exact match
        target_name = cleaned or name
        lower_name = target_name.strip().lower()
        if lower_name in self.synonyms:
            return self.synonyms[lower_name]

        # Case-insensitive registry search
        for item in self.items:
            if item.lower() == lower_name:
                return item

        # Normalized string search
        norm_query = self._normalize_string(target_name)
        for syn_k, syn_v in self.synonyms.items():
            if self._normalize_string(syn_k) == norm_query:
                return syn_v

        for item in self.items:
            if self._normalize_string(item) == norm_query:
                return item

        # Substring search in canonical items or synonyms
        candidates = []
        for syn_k, syn_v in self.synonyms.items():
            if (
                norm_query in self._normalize_string(syn_k)
                or self._normalize_string(syn_k) in norm_query
            ):
                candidates.append(syn_v)
        if candidates:
            return candidates[0]

        for item in self.items:
            if norm_query in self._normalize_string(item):
                candidates.append(item)
        if candidates:
            return candidates[0]

        # Fuzzy search fallback
        all_keys = list(self.synonyms.keys()) + [item.lower() for item in self.items]
        matches = difflib.get_close_matches(lower_name, all_keys, n=1, cutoff=0.55)
        if matches:
            match = matches[0]
            if match in self.synonyms:
                return self.synonyms[match]
            for item in self.items:
                if item.lower() == match:
                    return item

        raise ValueError(
            f"Could not resolve item '{name}' in Foxhole economy. Supported items include: "
            f"00MS “Stinger”, Spatha, Silverhand, Falchion, Basic Materials, 40mm, etc."
        )

    def _inventory_index(self, name: str) -> int | None:
        """Exact or case-insensitive registry match; stock is never fuzzy-matched."""
        if name in self.item_to_idx:
            return self.item_to_idx[name]
        lower = name.strip().lower()
        for item, idx in self.item_to_idx.items():
            if item.lower() == lower:
                return idx
        return None

    def _net_production(self, d_vec: np.ndarray, stock: np.ndarray) -> np.ndarray:
        """Least production x >= 0 with x = max(0, d + A x - stock).

        Iterates upward from zero; with a productive (Hawkins-Simon) matrix this converges,
        in at most depth-of-graph steps when the recipe graph is acyclic.
        """
        x = np.zeros(self.n, dtype=float)
        for _ in range(10 * self.n + 100):
            x_next = np.maximum(0.0, d_vec + self.A @ x - stock)
            if np.allclose(x_next, x, atol=1e-9, rtol=0.0):
                return x_next
            x = x_next
        raise RuntimeError("Inventory netting did not converge")

    def solve(
        self,
        demand: dict[str, float],
        include_machine_counts: bool = False,
        time_window_seconds: float | None = None,
        round_to_crates: bool = False,
        inventory: dict[str, float] | None = None,
    ) -> ProductionPlan:
        """Solve the curried Leontief balance equation x = L * d for target production demand.

        Args:
            demand: Dictionary of target outputs {item_name: quantity}
            include_machine_counts: If True, calculates required assembly/factory counts
            time_window_seconds: Production time budget (default: 3600 seconds = 1 hour)
            round_to_crates: Round each demanded crate-produced item up to whole crates
                before solving, since facilities cannot queue partial crates
            inventory: Units already on hand {item_name: quantity}, e.g. from a stockpile.
                Netted against demanded items first (before crate rounding), then against
                every intermediate and raw input, so only the shortfall is produced.

        Returns:
            Structured ProductionPlan with full BOM down to raw Salvage/Components.
        """
        resolved_demand: dict[str, float] = {}
        d_vec = np.zeros(self.n, dtype=float)

        for raw_item, qty in demand.items():
            if qty <= 0:
                continue
            canonical = self.resolve_item_name(raw_item)
            resolved_demand[canonical] = resolved_demand.get(canonical, 0.0) + float(qty)

        if not resolved_demand:
            raise ValueError("No positive demand quantities were specified.")

        stock = np.zeros(self.n, dtype=float)
        for name, qty in (inventory or {}).items():
            idx = self._inventory_index(name)
            if idx is not None and qty > 0:
                stock[idx] += float(qty)
        stock_on_hand = stock.copy()

        crates: dict[str, int] = {}
        for item, qty in resolved_demand.items():
            idx = self.item_to_idx[item]
            # Finished goods already in stock are issued before anything is made
            taken = min(stock[idx], qty)
            stock[idx] -= taken
            qty -= taken
            size = self.registry[item].crate_size
            if qty > 0 and size and self.registry[item].category != ItemCategory.RAW_RESOURCE:
                crates[item] = math.ceil(qty / size - 1e-9)
                if round_to_crates:
                    qty = float(crates[item] * size)
            # Report what is delivered: stock issued plus (possibly crate-rounded) production
            resolved_demand[item] = taken + qty
            d_vec[idx] += qty

        if inventory:
            x_vec = self._net_production(d_vec, stock)
        else:
            # Matrix-vector multiplication x = L * d (Instantaneous O(N^2))
            x_vec = self.L @ d_vec

        # Internal consumption c = A * x = x - d
        c_vec = self.A @ x_vec

        # Clean numerical precision
        x_vec = np.where(np.isclose(x_vec, 0.0, atol=1e-9), 0.0, x_vec)
        c_vec = np.where(np.isclose(c_vec, 0.0, atol=1e-9), 0.0, c_vec)

        # Categorize results
        raw_resources: dict[str, float] = {}
        refined_materials: dict[str, float] = {}
        facility_materials: dict[str, float] = {}
        intermediate_goods: dict[str, float] = {}
        gross_production: dict[str, float] = {}
        internal_consumption: dict[str, float] = {}

        for idx, item in enumerate(self.items):
            gross = float(x_vec[idx])
            consumed = float(c_vec[idx])

            if gross > 0:
                gross_production[item] = round(gross, 2)
            if consumed > 0:
                internal_consumption[item] = round(consumed, 2)

            if gross > 0:
                cat = self.registry[item].category
                if cat == ItemCategory.RAW_RESOURCE:
                    raw_resources[item] = round(gross, 2)
                elif cat == ItemCategory.REFINED_MATERIAL:
                    refined_materials[item] = round(gross, 2)
                elif cat == ItemCategory.FACILITY_MATERIAL:
                    facility_materials[item] = round(gross, 2)
                elif consumed > 0:
                    intermediate_goods[item] = round(consumed, 2)

        # Machine requirement calculation
        machines: dict[str, MachineRequirement] | None = None
        if include_machine_counts:
            machines = {}
            # Default time window: 3600 seconds (1 hour)
            t_window = (
                time_window_seconds if (time_window_seconds and time_window_seconds > 0) else 3600.0
            )
            for idx, item in enumerate(self.items):
                gross = float(x_vec[idx])
                if gross <= 0:
                    continue
                defn = self.registry[item]
                if defn.facility_type and defn.crafting_time_sec:
                    # Required rate per second = gross / t_window
                    rate_per_sec = gross / t_window
                    effective_speed = defn.yield_per_craft * 1.0
                    raw_count = (rate_per_sec * defn.crafting_time_sec) / effective_speed
                    machines[item] = MachineRequirement(
                        item=item,
                        facility_type=defn.facility_type,
                        cycle_time_seconds=defn.crafting_time_sec,
                        fractional_machines=round(raw_count, 3),
                        integer_machines=math.ceil(raw_count - 1e-9),
                    )

        inventory_used: dict[str, float] = {}
        if inventory:
            # Need for each good = final demand + downstream consumption; stock covers the gap
            need = d_vec + self.A @ x_vec
            leftover_used = np.minimum(stock, need)
            used_vec = (stock_on_hand - stock) + leftover_used
            for idx, item in enumerate(self.items):
                if used_vec[idx] > 1e-9:
                    inventory_used[item] = round(float(used_vec[idx]), 2)

        # Generate human-readable summary
        summary_lines = []
        demand_str = ", ".join(f"{v}x {k}" for k, v in resolved_demand.items())
        summary_lines.append(f"Production Plan for {demand_str}:")

        if raw_resources:
            raw_str = ", ".join(f"{v:g} {k}" for k, v in sorted(raw_resources.items()))
            summary_lines.append(f"• Total Raw Resources: {raw_str}")
        if refined_materials:
            ref_str = ", ".join(f"{v:g} {k}" for k, v in sorted(refined_materials.items()))
            summary_lines.append(f"• Refined Materials: {ref_str}")
        if facility_materials:
            fac_str = ", ".join(f"{v:g} {k}" for k, v in sorted(facility_materials.items()))
            summary_lines.append(f"• Facility Materials: {fac_str}")
        if intermediate_goods:
            inter_str = ", ".join(f"{v:g} {k}" for k, v in sorted(intermediate_goods.items()))
            summary_lines.append(f"• Intermediate Goods: {inter_str}")
        if inventory_used:
            used_str = ", ".join(f"{v:g} {k}" for k, v in sorted(inventory_used.items()))
            summary_lines.append(f"• Drawn From Inventory: {used_str}")

        summary = "\n".join(summary_lines)

        return ProductionPlan(
            requested_demand=demand,
            resolved_demand=resolved_demand,
            raw_resources=raw_resources,
            refined_materials=refined_materials,
            facility_materials=facility_materials,
            intermediate_goods=intermediate_goods,
            gross_production=gross_production,
            internal_consumption=internal_consumption,
            machines=machines,
            crates=crates or None,
            inventory_used=inventory_used or None,
            summary=summary,
        )


# Global singleton instance cached at startup
_SOLVER_INSTANCE: CurriedEconomySolver | None = None


def get_economy_solver() -> CurriedEconomySolver:
    """Retrieve or initialize the curried Foxhole economy solver singleton.

    Uses the synced wiki Production table (`foxhole cargo-sync`) when cached, falling back
    to the built-in registry otherwise.
    """
    global _SOLVER_INSTANCE
    if _SOLVER_INSTANCE is None:
        from foxhole.cargo import (
            ITEM_TABLE,
            build_aliases,
            build_registry,
            load_production,
            load_table,
        )

        rows = load_production()
        registry = build_registry(rows) if rows else None
        synonyms = {**build_aliases(load_table(ITEM_TABLE) or []), **SYNONYM_MAP}
        _SOLVER_INSTANCE = CurriedEconomySolver(registry=registry, synonyms=synonyms)
    return _SOLVER_INSTANCE


def reset_economy_solver() -> None:
    """Drop the cached solver so the next call reloads (e.g. after a cargo sync)."""
    global _SOLVER_INSTANCE
    _SOLVER_INSTANCE = None
