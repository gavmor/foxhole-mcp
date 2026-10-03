"""Pydantic data models for Foxhole War API responses."""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from foxhole.warapi.constants import MapFlags, get_icon_name


class WarState(BaseModel):
    """Current state of the World Conquest for a given shard."""

    war_id: str = Field(alias="warId", description="Unique identifier for the war")
    war_number: int = Field(alias="warNumber", description="Current war number")
    winner: str = Field(description="Winning faction: NONE, WARDENS, or COLONIALS")
    conquest_start_time: int | None = Field(
        default=None,
        alias="conquestStartTime",
        description="Unix timestamp in ms when conquest started",
    )
    conquest_end_time: int | None = Field(
        default=None,
        alias="conquestEndTime",
        description="Unix timestamp in ms when conquest ended",
    )
    resistance_start_time: int | None = Field(
        default=None,
        alias="resistanceStartTime",
        description="Unix timestamp in ms when resistance phase started",
    )
    scheduled_conquest_end_time: int | None = Field(
        default=None,
        alias="scheduledConquestEndTime",
        description="Scheduled end time for short conquest",
    )
    required_victory_towns: int = Field(
        default=32, alias="requiredVictoryTowns", description="Base victory towns needed to win"
    )
    short_required_victory_towns: int = Field(
        default=0,
        alias="shortRequiredVictoryTowns",
        description="Short conquest victory towns needed",
    )

    @property
    def is_active(self) -> bool:
        """True if the conquest is actively ongoing (not finished or in resistance)."""
        return (
            self.winner == "NONE"
            and self.conquest_end_time is None
            and self.resistance_start_time is None
        )

    @property
    def status_display(self) -> str:
        """Human-readable status label."""
        if self.is_active:
            return "Active Conquest"
        if self.resistance_start_time is not None:
            return f"Resistance Phase (Winner: {self.winner})"
        if self.winner != "NONE":
            return f"Conquest Concluded (Winner: {self.winner})"
        return "Pre-War / Preparing"

    @property
    def start_datetime(self) -> str | None:
        """Formatted start timestamp in UTC."""
        if self.conquest_start_time:
            dt = datetime.fromtimestamp(self.conquest_start_time / 1000.0, tz=UTC)
            return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        return None


class WarReport(BaseModel):
    """Enlistment and casualty figures for a specific map region."""

    map_name: str = Field(description="Map hex identifier")
    total_enlistments: int = Field(
        alias="totalEnlistments", description="Total player spawns/enlistments"
    )
    colonial_casualties: int = Field(alias="colonialCasualties", description="Colonial deaths")
    warden_casualties: int = Field(alias="wardenCasualties", description="Warden deaths")
    day_of_war: int = Field(alias="dayOfWar", description="Current in-game day count")
    version: int = Field(default=0, description="Version counter")

    @property
    def total_casualties(self) -> int:
        return self.colonial_casualties + self.warden_casualties


class MapItem(BaseModel):
    """Individual world structure, base, resource node, or objective on a map."""

    team_id: str = Field(
        alias="teamId", description="Controlling faction: NONE, WARDENS, or COLONIALS"
    )
    icon_type: int = Field(alias="iconType", description="Numerical icon type code")
    icon_name: str = Field(default="", description="Decoded name of the icon structure/node")
    x: float = Field(description="Normalized map horizontal coordinate (0.0 to 1.0)")
    y: float = Field(description="Normalized map vertical coordinate (0.0 to 1.0)")
    flags: int = Field(default=0, description="Bitmask of map flags")
    is_victory_base: bool = Field(
        default=False, description="Whether this structure is a victory town"
    )
    is_build_site: bool = Field(
        default=False, description="Whether this structure is an unbuilt foundation"
    )
    is_scorched: bool = Field(
        default=False, description="Whether this town base has been rocket-scorched"
    )
    is_town_claimed: bool = Field(default=False, description="Whether this town is claimed")

    @classmethod
    def from_api_item(cls, data: dict[str, Any]) -> "MapItem":
        flags_val = data.get("flags", 0)
        icon_type = data.get("iconType", 0)
        return cls(
            teamId=data.get("teamId", "NONE"),
            iconType=icon_type,
            icon_name=get_icon_name(icon_type),
            x=data.get("x", 0.0),
            y=data.get("y", 0.0),
            flags=flags_val,
            is_victory_base=bool(flags_val & MapFlags.IS_VICTORY_BASE),
            is_build_site=bool(flags_val & MapFlags.IS_BUILD_SITE),
            is_scorched=bool(flags_val & MapFlags.IS_SCORCHED),
            is_town_claimed=bool(flags_val & MapFlags.IS_TOWN_CLAIMED),
        )


class MapTextItem(BaseModel):
    """Text label or sub-region marker on the map."""

    text: str = Field(description="Town or region name as displayed in-game")
    x: float = Field(description="Normalized horizontal coordinate")
    y: float = Field(description="Normalized vertical coordinate")
    map_marker_type: str = Field(alias="mapMarkerType", description="Major or Minor location label")


class MapData(BaseModel):
    """Full map state data (static or dynamic) for a map region."""

    map_name: str = Field(description="Hex name")
    region_id: int = Field(alias="regionId", description="Internal region ID")
    scorched_victory_towns: int = Field(
        default=0,
        alias="scorchedVictoryTowns",
        description="Count of destroyed victory towns in region",
    )
    map_items: list[MapItem] = Field(
        default_factory=list, description="Interactive and structural map points"
    )
    map_text_items: list[MapTextItem] = Field(
        default_factory=list, description="Text labels and sub-regions"
    )
    last_updated: int | None = Field(default=None, alias="lastUpdated", description="Timestamp ms")
    version: int = Field(default=0, description="Map version counter")


class GlobalCasualties(BaseModel):
    """Aggregated war casualty statistics across all hexes."""

    shard: str = Field(description="Active shard name")
    day_of_war: int = Field(
        default=0, description="Highest current day of war across reporting hexes"
    )
    total_enlistments: int = Field(description="Sum of all player spawns")
    colonial_casualties: int = Field(description="Total Colonial player deaths")
    warden_casualties: int = Field(description="Total Warden player deaths")
    total_casualties: int = Field(description="Combined global deaths")
    casualty_diff: int = Field(
        description="Warden deaths minus Colonial deaths (positive means more Warden losses)"
    )
    most_active_fronts: list[dict[str, Any]] = Field(
        default_factory=list, description="Top frontlines ranked by casualties"
    )


class VictoryTownStatus(BaseModel):
    """Current count of victory towns captured by each faction."""

    shard: str = Field(description="Active shard name")
    required_to_win: int = Field(
        description="Static victory towns needed according to war configuration"
    )
    effective_required_to_win: int = Field(
        description="Actual victory towns needed after subtracting scorched towns"
    )
    warden_captured: int = Field(description="Victory towns currently held by Wardens")
    colonial_captured: int = Field(description="Victory towns currently held by Colonials")
    scorched_count: int = Field(description="Total scorched victory towns")
    unclaimed_or_neutral: int = Field(description="Victory towns currently unclaimed or contested")
