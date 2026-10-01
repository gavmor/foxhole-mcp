"""Tests for dieselpunk telegraph and wire dispatch MCP tools."""

from unittest.mock import AsyncMock

import pytest

from foxhole.server import create_server
from foxhole.tools.dispatches import DispatchesTools
from foxhole.warapi.models import (
    GlobalCasualties,
    MapData,
    MapItem,
    MapTextItem,
    VictoryTownStatus,
    WarReport,
    WarState,
)


@pytest.fixture
def mock_war_client():
    client = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_get_flash_dispatch_basic(mock_war_client):
    # Setup mock data
    mock_war_client.get_dynamic_map_data.return_value = MapData(
        map_name="DeadLandsHex",
        regionId=1,
        scorchedVictoryTowns=0,
        map_items=[
            MapItem(
                teamId="WARDENS",
                iconType=56,
                icon_name="Town Base 1",
                x=0.42,
                y=0.48,
                flags=1,
                is_victory_base=True,
            ),
            MapItem(
                teamId="WARDENS",
                iconType=45,
                icon_name="Relic Base",
                x=0.45,
                y=0.40,
                flags=0,
            ),
            MapItem(
                teamId="COLONIALS",
                iconType=56,
                icon_name="Town Base 1",
                x=0.49,
                y=0.85,
                flags=0,
            ),
            MapItem(
                teamId="WARDENS",
                iconType=17,
                icon_name="Refinery",
                x=0.41,
                y=0.49,
                flags=0,
            ),
        ],
        map_text_items=[],
        version=1,
    )
    mock_war_client.get_static_map_data.return_value = MapData(
        map_name="DeadLandsHex",
        regionId=1,
        scorchedVictoryTowns=0,
        map_items=[],
        map_text_items=[
            MapTextItem(text="Abandoned Ward", x=0.42, y=0.48, mapMarkerType="Major"),
            MapTextItem(text="The Spine", x=0.50, y=0.50, mapMarkerType="Minor"),
        ],
        version=1,
    )
    mock_war_client.get_war_report.return_value = WarReport(
        map_name="DeadLandsHex",
        totalEnlistments=15000,
        colonialCasualties=12400,
        wardenCasualties=9800,
        dayOfWar=34,
        version=1,
    )

    tools = DispatchesTools(mock_war_client)
    dispatch = await tools.get_flash_dispatch("DeadLandsHex", shard="live-1")

    assert "ZCZC MXA" in dispatch
    assert "NNNN" in dispatch
    assert "DATELINE: DEAD LANDS (ABANDONED WARD) —" in dispatch
    assert "WARDEN EXPEDITIONARY FORCES" in dispatch
    assert "12,400 COLONIAL" in dispatch
    assert "9,800 WARDEN" in dispatch
    assert "WAR DAY 34" in dispatch
    assert "REFINERY AT ABANDONED WARD" in dispatch
    assert "STOP" in dispatch


@pytest.mark.asyncio
async def test_get_flash_dispatch_scorched(mock_war_client):
    mock_war_client.get_dynamic_map_data.return_value = MapData(
        map_name="CallahansPassageHex",
        regionId=2,
        scorchedVictoryTowns=1,
        map_items=[
            MapItem(
                teamId="NONE",
                iconType=56,
                icon_name="Town Base 1",
                x=0.53,
                y=0.46,
                flags=17,  # IS_VICTORY_BASE | IS_SCORCHED
                is_victory_base=True,
                is_scorched=True,
            ),
        ],
        map_text_items=[],
        version=1,
    )
    mock_war_client.get_static_map_data.return_value = MapData(
        map_name="CallahansPassageHex",
        regionId=2,
        scorchedVictoryTowns=1,
        map_items=[],
        map_text_items=[
            MapTextItem(text="Lochan Berth", x=0.53, y=0.46, mapMarkerType="Major"),
        ],
        version=1,
    )
    mock_war_client.get_war_report.return_value = None

    tools = DispatchesTools(mock_war_client)
    dispatch = await tools.get_flash_dispatch("CallahansPassageHex")

    assert "CALLAHAN'S PASSAGE" in dispatch
    assert "CATASTROPHIC DESTRUCTION CONFIRMED AT LOCHAN BERTH" in dispatch
    assert "SCORCHED TO BEDROCK" in dispatch


@pytest.mark.asyncio
async def test_get_flash_dispatch_offline(mock_war_client):
    mock_war_client.get_dynamic_map_data.return_value = None
    mock_war_client.get_static_map_data.return_value = None
    mock_war_client.get_war_report.return_value = None

    tools = DispatchesTools(mock_war_client)
    dispatch = await tools.get_flash_dispatch("WestgateHex")

    assert "ZCZC ERR001" in dispatch
    assert "WIRE RELAY FAILED STOP" in dispatch
    assert "WESTGATE" in dispatch


@pytest.mark.asyncio
async def test_get_propaganda_wire_warden(mock_war_client):
    mock_war_client.get_war_state.return_value = WarState(
        warId="war-118",
        warNumber=118,
        winner="NONE",
        requiredVictoryTowns=32,
    )
    mock_war_client.get_victory_town_status.return_value = VictoryTownStatus(
        shard="live-1",
        warden_captured=18,
        colonial_captured=14,
        required_to_win=32,
        effective_required_to_win=32,
        scorched_count=0,
        unclaimed_or_neutral=0,
    )
    mock_war_client.get_global_casualties.return_value = GlobalCasualties(
        shard="live-1",
        day_of_war=22,
        total_enlistments=850000,
        colonial_casualties=450000,
        warden_casualties=410000,
        total_casualties=860000,
        casualty_diff=-40000,
        most_active_fronts=[{"map_name": "DeadLandsHex"}],
    )

    tools = DispatchesTools(mock_war_client)
    wire = await tools.get_propaganda_wire(faction="Warden")

    assert "ARCHON PRESS WIRELESS SERVICE" in wire
    assert "KIRKNELL REAR-GUARD DEPOT" in wire
    assert "CONQUEST DAY 22 OF WAR 118" in wire
    assert "18 OF 32 REQUIRED VICTORY TOWNS" in wire
    assert "DEAD LANDS" in wire
    assert "LONG LIVE THE ARCHON" in wire
    assert "NNNN" in wire


@pytest.mark.asyncio
async def test_get_propaganda_wire_colonial(mock_war_client):
    mock_war_client.get_war_state.return_value = WarState(
        warId="war-118",
        warNumber=118,
        winner="NONE",
        requiredVictoryTowns=32,
    )
    mock_war_client.get_victory_town_status.return_value = VictoryTownStatus(
        shard="live-1",
        warden_captured=15,
        colonial_captured=17,
        required_to_win=32,
        effective_required_to_win=32,
        scorched_count=0,
        unclaimed_or_neutral=0,
    )
    mock_war_client.get_global_casualties.return_value = GlobalCasualties(
        shard="live-1",
        day_of_war=22,
        total_enlistments=850000,
        colonial_casualties=420000,
        warden_casualties=460000,
        total_casualties=880000,
        casualty_diff=40000,
        most_active_fronts=[{"map_name": "WestgateHex"}],
    )

    tools = DispatchesTools(mock_war_client)
    wire = await tools.get_propaganda_wire(faction="Colonial")

    assert "MESEAN TRIBUNE WIRE POOL" in wire
    assert "THERIZO REVOLUTIONARY ASSEMBLY" in wire
    assert "OPERATIONAL DAY 22 OF WORLD CAMPAIGN 118" in wire
    assert "17 STRATEGIC VICTORY TOWNS" in wire
    assert "WESTGATE" in wire
    assert "FOR THE REPUBLIC AND THE FEDERATION" in wire
    assert "NNNN" in wire


@pytest.mark.asyncio
async def test_server_dispatches_tool_registration():
    server = create_server()
    tools = await server.list_tools()
    tool_names = [t.name for t in tools]

    assert "get_flash_dispatch" in tool_names
    assert "get_propaganda_wire" in tool_names
