"""Unit tests for Foxhole wikitext parser."""

from foxhole.parser import (
    _clean_wiki_markup,
    _safe_float,
    _safe_int,
    parse_item,
    parse_page_content,
    parse_structure,
    parse_vehicle,
)

SAMPLE_VEHICLE_WIKITEXT = """
{{Vehicle Infobox
| codename                          = MediumTankW
| name                              = Silverhand - Mk. IV
| variants                          = Silverhand Chieftain - Mk. VI
| faction                           = War
| type                              = Assault Tank
| vehicle_hp                        = 3100
| armour_type                       = Tier2Tank
| armour_hp                         = 17000
| min_pen_chance                    = 25
| max_pen_chance                    = 67
| disable_chance_tracks             = 30
| disable_chance_fueltank           = 20
| disable_chance_turret             = 20
| disable                           = 30
| repair                            = 170
| crew                              = 4
| slots                             = 1
| fuelcap                           = 325
| fuelrate                          = 13.5
| speed                             = 7.75
| offspeed                          = 5.42
| A1_ArmamentName                   = 40mm Cannon
| A1_AmmoName                       = 40mm
| A1_ReloadTime                     = 5.5
| A1_RangeMax                       = 40
| A1_FireRate                       = 8
| A2_ArmamentName                   = 68mm Cannon
| A2_AmmoName                       = 68mm
| A2_ReloadTime                     = 4.5
| A2_RangeMax                       = 35
| PRD1_Source                       = Garage
| PRD1_InputItem1                   = Refined Materials
| PRD1_InputItem1Amount             = 160
| PRD1_IsMPFable                    = 1
}}
{{Quote|The Silverhand assault tank is fitted with destructive dual-barrel armaments.|In-game description}}
The '''Silverhand - Mk. IV''' is an assault tank.
"""

SAMPLE_ITEM_WIKITEXT = """
{{Item Infobox
| codename                          = RifleW
| name                              = No.2 Loughcaster
| faction                           = War
| type                              = Rifle
| category                          = Small Arms
| EquipmentSlot                     = Primary
| fire_rate                         = 55
| range_effective                   = 27
| range_max                         = 40
| magazine                          = 12
| reload                            = 3.5
| ammo                              = 7.62mm
| encumbrance                       = 100
| crate_amount                      = 20
| PRD1_Source                       = Factory
| PRD1_InputItem1                   = Basic Materials
| PRD1_InputItem1Amount             = 100
| PRD1_ProductionTime               = 70
| PRD1_IsMPFable                    = 1
}}
{{Quote|Standard-issue Warden rifle.|In-game description}}
The '''No.2 Loughcaster''' is the standard rifle.
"""

SAMPLE_STRUCTURE_WIKITEXT = """
{{Structure Infobox
| codename                          = LRArtillery
| name                              = Storm Cannon
| faction                           = Both
| construction_type                 = Large Structure
| structure_hp                      = 2550
| armour_type                       = Tier3Structure
| decay_duration                    = 67.11
| repair                            = 1200
| A1_ArmamentName                   = Storm Cannon
| A1_AmmoName                       = 300mm
| A1_ReloadTime                     = 4.5
| A1_RangeMax                       = 400-1000
| PRD1_Source                       = Large Structure Foundation
| PRD1_InputItem1                   = Construction Parts
| PRD1_InputItem1Amount             = 1
}}
{{Quote|A heavy fixed position artillery piece.|In-game description}}
"""


def test_safe_converters():
    assert _safe_int("150") == 150
    assert _safe_int(" 200 ") == 200
    assert _safe_int("310 x {{Disp}}") == 310
    assert _safe_int(None) is None
    assert _safe_int("") is None

    assert _safe_float("13.5") == 13.5
    assert _safe_float("81.25") == 81.25
    assert _safe_float(None) is None


def test_clean_wiki_markup():
    raw = "The '''Silverhand''' uses [[7.62mm]] ammunition and {{Disp|conmats|onlyImage=1}}."
    cleaned = _clean_wiki_markup(raw)
    assert "Silverhand" in cleaned
    assert "7.62mm" in cleaned
    assert "'''" not in cleaned
    assert "[[" not in cleaned


def test_parse_vehicle():
    v = parse_vehicle("Silverhand - Mk. IV", SAMPLE_VEHICLE_WIKITEXT)
    assert v is not None
    assert v.name == "Silverhand - Mk. IV"
    assert v.faction == "Warden"
    assert v.health == 3100
    assert v.armor_type == "Tier2Tank"
    assert v.armor_health == 17000
    assert v.min_pen_chance == 25.0
    assert v.max_pen_chance == 67.0
    assert v.disable_threshold == 30.0
    assert v.disable_subsystems["tracks"] == 30.0
    assert v.disable_subsystems["fuel_tank"] == 20.0
    assert v.crew == 4
    assert v.fuel_capacity == 325.0

    # Check armaments
    assert len(v.armaments) == 2
    assert v.armaments[0].name == "40mm Cannon"
    assert v.armaments[0].ammo == "40mm"
    assert v.armaments[0].reload_time == 5.5
    assert v.armaments[1].name == "68mm Cannon"
    assert v.armaments[1].ammo == "68mm"

    # Check production
    assert len(v.production) == 1
    assert v.production[0].source == "Garage"
    assert v.production[0].inputs == {"Refined Materials": 160}
    assert v.production[0].is_mpfable is True


def test_parse_item():
    item = parse_item("No.2 Loughcaster", SAMPLE_ITEM_WIKITEXT)
    assert item is not None
    assert item.name == "No.2 Loughcaster"
    assert item.faction == "Warden"
    assert item.category == "Small Arms"
    assert item.equipment_slot == "Primary"
    assert item.ammo == "7.62mm"
    assert item.magazine == 12
    assert item.crate_amount == 20
    assert item.range_effective == 27.0
    assert len(item.production) == 1
    assert item.production[0].source == "Factory"
    assert item.production[0].inputs == {"Basic Materials": 100}


def test_parse_structure():
    s = parse_structure("Storm Cannon", SAMPLE_STRUCTURE_WIKITEXT)
    assert s is not None
    assert s.name == "Storm Cannon"
    assert s.health == 2550
    assert s.armor_type == "Tier3Structure"
    assert s.repair_cost == 1200
    assert len(s.armaments) == 1
    assert s.armaments[0].ammo == "300mm"
    assert s.armaments[0].range_max == "400-1000"


def test_parse_page_content():
    content = parse_page_content("Silverhand - Mk. IV", SAMPLE_VEHICLE_WIKITEXT)
    assert content.title == "Silverhand - Mk. IV"
    assert "Silverhand" in content.summary
    assert content.infobox_type == "Vehicle Infobox"
    assert content.structured_data is not None
    assert content.structured_data.get("armour_type") == "Tier2Tank"
