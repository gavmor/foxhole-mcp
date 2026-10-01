"""Unit and integration tests for Foxhole War API integration."""

import pytest

from foxhole.server import (
    get_active_maps,
    get_map_intel,
    get_victory_town_status,
    get_war_casualties,
    get_war_status,
)
from foxhole.warapi.constants import (
    ICON_CATEGORIES,
    MapFlags,
    get_icon_name,
)
from foxhole.warapi.models import (
    MapItem,
    WarReport,
    WarState,
)

SAMPLE_WAR_STATE = {
    "warId": "9b33b555-e587-4915-89a5-50eb37f2949d",
    "warNumber": 140,
    "winner": "NONE",
    "conquestStartTime": 1563291629686,
    "conquestEndTime": None,
    "resistanceStartTime": None,
    "scheduledConquestEndTime": None,
    "requiredVictoryTowns": 32,
    "shortRequiredVictoryTowns": 0,
}

SAMPLE_MAP_ITEM_RAW = {
    "teamId": "WARDENS",
    "iconType": 56,  # Town Base 1
    "x": 0.4065,
    "y": 0.4973,
    "flags": 0x01 | 0x20,  # IsVictoryBase | IsTownClaimed
}


def test_map_flags():
    assert MapFlags.IS_VICTORY_BASE == 0x01
    assert MapFlags.IS_BUILD_SITE == 0x04
    assert MapFlags.IS_SCORCHED == 0x10
    assert MapFlags.IS_TOWN_CLAIMED == 0x20


def test_icon_names():
    assert get_icon_name(59) == "Storm Cannon"
    assert get_icon_name(34) == "Factory"
    assert get_icon_name(17) == "Refinery"
    assert get_icon_name(51) == "Mass Production Factory"
    assert get_icon_name(52) == "Seaport"
    assert get_icon_name(56) == "Town Base 1"
    assert get_icon_name(88) == "Aircraft Depot"
    assert get_icon_name(91) == "Aircraft Runway (T1)"
    assert "Unknown" in get_icon_name(9999)


def test_icon_categories():
    assert 56 in ICON_CATEGORIES["bases"]
    assert 34 in ICON_CATEGORIES["logistics"]
    assert 21 in ICON_CATEGORIES["resources"]  # Component Field
    assert 59 in ICON_CATEGORIES["defenses"]  # Storm Cannon
    assert 72 in ICON_CATEGORIES["rockets"]  # Rocket Site With Rocket
    assert 88 in ICON_CATEGORIES["aircraft"]


def test_war_state_model():
    ws = WarState.model_validate(SAMPLE_WAR_STATE)
    assert ws.war_number == 140
    assert ws.war_id == "9b33b555-e587-4915-89a5-50eb37f2949d"
    assert ws.winner == "NONE"
    assert ws.required_victory_towns == 32
    assert ws.is_active is True
    assert ws.status_display == "Active Conquest"
    assert ws.start_datetime is not None


def test_map_item_decoding():
    item = MapItem.from_api_item(SAMPLE_MAP_ITEM_RAW)
    assert item.team_id == "WARDENS"
    assert item.icon_type == 56
    assert item.icon_name == "Town Base 1"
    assert item.is_victory_base is True
    assert item.is_town_claimed is True
    assert item.is_scorched is False
    assert item.is_build_site is False


def test_war_report_model():
    wr = WarReport(
        map_name="DeadLandsHex",
        totalEnlistments=148,
        colonialCasualties=202,
        wardenCasualties=222,
        dayOfWar=2,
        version=1,
    )
    assert wr.map_name == "DeadLandsHex"
    assert wr.total_casualties == 424
    assert wr.day_of_war == 2


@pytest.mark.asyncio
async def test_get_war_status_tool():
    res = await get_war_status(shard="live-1")
    assert "war_number" in res
    assert "status" in res
    assert res["shard"] == "live-1"


@pytest.mark.asyncio
async def test_get_active_maps_tool():
    res = await get_active_maps(shard="live-1")
    assert res["total_maps"] > 0
    assert "DeadLandsHex" in res["maps"]


@pytest.mark.asyncio
async def test_get_war_casualties_tool():
    res = await get_war_casualties(map_name="DeadLandsHex", shard="live-1")
    assert res["map_name"] == "DeadLandsHex"
    assert "colonial_casualties" in res
    assert "warden_casualties" in res


@pytest.mark.asyncio
async def test_get_map_intel_tool():
    res = await get_map_intel("DeadLandsHex", shard="live-1")
    assert res["map_name"] == "DeadLandsHex"
    assert "major_locations" in res
    assert "Abandoned Ward" in res["major_locations"]


@pytest.mark.asyncio
async def test_get_victory_town_status_tool():
    res = await get_victory_town_status(shard="live-1")
    assert "required_to_win" in res
    assert res["required_to_win"] > 0
