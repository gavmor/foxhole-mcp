"""MCP tools for generating Foxhole dieselpunk telegraph and wire service dispatches."""

import hashlib
import logging
import math
from datetime import UTC, datetime

from foxhole.tools.base import BaseToolProvider
from foxhole.warapi import DEFAULT_SHARD, WarApiClient
from foxhole.warapi.models import MapItem, MapTextItem

logger = logging.getLogger(__name__)


def _clean_hex_name(map_name: str) -> str:
    """Format hex identifier into human-readable telegraph dateline name."""
    name = map_name
    if name.endswith("Hex"):
        name = name[:-3]

    # Insert spaces before capital letters if CamelCase
    formatted = []
    for i, char in enumerate(name):
        if char.isupper() and i > 0 and not name[i - 1].isupper() and not name[i - 1] == " ":
            formatted.append(" ")
        formatted.append(char)
    res = "".join(formatted).upper().strip()

    # Common canonical adjustments
    res = res.replace("CALLAHANS", "CALLAHAN'S")
    res = res.replace("LINN MERCY", "LINN OF MERCY")
    return res


def _find_nearest_location(x: float, y: float, text_items: list[MapTextItem]) -> str:
    """Find the nearest named town or landmark to given coordinates."""
    if not text_items:
        return "FORWARD SECTOR"

    best_name = text_items[0].text
    best_dist = float("inf")

    for item in text_items:
        dist = math.hypot(item.x - x, item.y - y)
        if dist < best_dist:
            best_dist = dist
            best_name = item.text

    return best_name.upper()


def _format_zulu_timestamp(now: datetime | None = None) -> tuple[str, str]:
    """Generate a standard military telegram timestamp (e.g. 141630Z OCT) and sequence code."""
    dt = now or datetime.now(tz=UTC)
    day = f"{dt.day:02d}"
    time_str = f"{dt.hour:02d}{dt.minute:02d}Z"
    month = dt.strftime("%b").upper()
    timestamp = f"{day}{time_str} {month}"

    # Deterministic sequence code
    seed = f"{dt.day}-{dt.hour}-{dt.minute}"
    seq_num = int(hashlib.md5(seed.encode()).hexdigest()[:3], 16) % 900 + 100
    return timestamp, f"{seq_num:03d}"


class DispatchesTools(BaseToolProvider):
    """Generates period-authentic dieselpunk wire dispatches and propaganda cables."""

    def __init__(self, war_client: WarApiClient | None = None) -> None:
        self.war_client = war_client or WarApiClient()

    async def get_flash_dispatch(
        self,
        map_name: str,
        shard: str = DEFAULT_SHARD,
    ) -> str:
        """Generate an authentic breaking tactical news wire dispatch (cablese teleprinter format) for a map hex.

        Reports dynamic base control, contested points, casualties, scorched earth,
        and logistical status in period-accurate telegraphic cablese syntax (ZCZC/NNNN).

        Args:
            map_name: Hex name (e.g. 'DeadLandsHex', 'CallahansPassageHex', 'WestgateHex')
            shard: Target shard: 'live-1', 'live-2', 'live-3', or 'dev'
        """
        dynamic_data = await self.war_client.get_dynamic_map_data(map_name, shard=shard)
        static_data = await self.war_client.get_static_map_data(map_name, shard=shard)
        war_report = await self.war_client.get_war_report(map_name, shard=shard)

        if not dynamic_data and not static_data and not war_report:
            return (
                f"ZCZC ERR001 {datetime.now(tz=UTC).strftime('%d%H%MZ %b').upper()}\n"
                f"URGENT / WIRE RELAY FAILED STOP\n"
                f"NO TELEGRAPH LINK ESTABLISHED WITH SECTOR '{map_name.upper()}' STOP\n"
                "RADIO SILENCE OR SEVERED COPPER LINES SUSPECTED STOP AR\n\nNNNN"
            )

        timestamp, seq_code = _format_zulu_timestamp()
        hex_display = _clean_hex_name(map_name)

        text_items = static_data.map_text_items if static_data else []
        items = dynamic_data.map_items if dynamic_data else []

        # Find primary major town / forward wirehead
        primary_hub = "FORWARD LINE"
        if text_items:
            majors = [t.text.upper() for t in text_items if t.map_marker_type == "Major"]
            if majors:
                primary_hub = majors[0]
            else:
                primary_hub = text_items[0].text.upper()

        # Classify bases and key infrastructure
        warden_bases: list[MapItem] = []
        colonial_bases: list[MapItem] = []
        scorched_bases: list[MapItem] = []
        build_sites: list[MapItem] = []
        logistics_hubs: list[MapItem] = []

        for item in items:
            name = item.icon_name
            if "Base" in name or name in ("Town Hall", "Keep", "Fort"):
                if item.is_scorched:
                    scorched_bases.append(item)
                elif item.is_build_site:
                    build_sites.append(item)
                elif item.team_id == "WARDENS":
                    warden_bases.append(item)
                elif item.team_id == "COLONIALS":
                    colonial_bases.append(item)
            elif name in ("Refinery", "Storage Facility", "Seaport", "Factory"):
                logistics_hubs.append(item)

        total_warden = len(warden_bases)
        total_colonial = len(colonial_bases)

        # Tactical Situation assessment
        lines: list[str] = []
        if total_warden > total_colonial:
            balance_note = (
                f"WARDEN EXPEDITIONARY FORCES HOLD TACTICAL ADVANTAGE WITH {total_warden} SECURED STRONGHOLDS "
                f"AGAINST {total_colonial} DEFENDED ENEMY REDOUBTS"
            )
        elif total_colonial > total_warden:
            balance_note = (
                f"COLONIAL REPUBLIC LEGIONS PRESS FORWARD ASSAULT OCCUPYING {total_colonial} FORTIFIED BASES "
                f"VERSUS {total_warden} DEFENDING WARDEN GARRISONS"
            )
        else:
            balance_note = f"FIERCE DEADLOCK CONFRONTATION WITH BOTH ARMIES CONTESTING SECTOR AT {total_warden} GARRISONS EACH"

        lines.append(f"{balance_note} STOP")

        # Scorched Earth / Build Sites
        if scorched_bases:
            loc = _find_nearest_location(scorched_bases[0].x, scorched_bases[0].y, text_items)
            lines.append(
                f"CATASTROPHIC DESTRUCTION CONFIRMED AT {loc} // VICTORY STRUCTURE SCORCHED TO BEDROCK TO DENY ENEMY ADVANCE STOP"
            )
        elif build_sites:
            loc = _find_nearest_location(build_sites[0].x, build_sites[0].y, text_items)
            lines.append(
                f"INTENSE SAPPER ACTIVITY DETECTED NEAR {loc} // CONTESTED BASE SITE LEVELED AND UNDER HURRIED RECONSTRUCTION STOP"
            )
        elif items:
            # Report on primary contested front point
            sample_base = (warden_bases or colonial_bases)[0]
            loc = _find_nearest_location(sample_base.x, sample_base.y, text_items)
            lines.append(
                f"HEAVY CLASHES REPORTED AROUND STRATEGIC AXIS OF {loc} WITH FIELD ARTILLERY SATURATION STOP"
            )

        # Casualties and Enlistments
        if war_report:
            w_cas = war_report.warden_casualties
            c_cas = war_report.colonial_casualties
            day = war_report.day_of_war
            tot_cas = war_report.total_casualties
            lines.append(
                f"WAR DAY {day} REGIONAL LOSSES TOTAL {tot_cas:,} CONFIRMED CASUALTIES // "
                f"{w_cas:,} WARDEN AND {c_cas:,} COLONIAL LOSSES RECORDED AT LOCAL AID POSTS STOP "
                "FURTHER CASUALTY DETAILS WITHHELD UNDER WARTIME DEFENSE ORDINANCE SIX-BEE STOP"
            )
        else:
            lines.append("LOCAL FIELD CASUALTY AUDIT PENDING DISPATCH RUNNER ARRIVAL STOP")

        # Logistics state
        intact_logi = [
            f"{i.icon_name.upper()} AT {_find_nearest_location(i.x, i.y, text_items)}"
            for i in logistics_hubs
            if i.team_id != "NONE"
        ]
        if intact_logi:
            lines.append(
                f"CRITICAL SUPPLY INFRASTRUCTURE VERIFIED OPERATIONAL: {', '.join(intact_logi[:2])} STOP "
                "RESUPPLY CONVOYS URGED TO MAINTAIN ARMORED ESCORT THROUGH DEAD GROUND STOP"
            )
        else:
            lines.append(
                "REGIONAL SUPPLY DEPOTS INACCESSIBLE // LOGISTICS TRANSPORTS REDIRECTED TO REAR RESERVES STOP"
            )

        body_text = "\n".join(lines)

        censor_code = f"BD-{(int(seq_code) % 89 + 10)}"
        stamp_num = f"{(int(seq_code) % 9 + 1):02d}-CLEAR"

        dispatch = f"""ZCZC MXA{seq_code} {timestamp}
PRIORITY / PRESS WIRE RELAY VIA MORSE CENTRAL POST {primary_hub}
TO: EDITORIAL DESK // ALL REGIONAL PRINT OFFICES
PASS MILITARY CENSOR {censor_code} APPROVED // STAMP {stamp_num}

DATELINE: {hex_display} ({primary_hub}) —

{body_text}

MORE TO FOLLOW ON WIRE THREE EXTREME STATIC STOP AR

NNNN"""
        return dispatch

    async def get_propaganda_wire(
        self,
        faction: str = "Warden",
        shard: str = DEFAULT_SHARD,
    ) -> str:
        """Generate a strategic homefront propaganda wire dispatch (cablese teleprinter format).

        Broadcast from either the Warden Archon Press or Colonial Mesean News Bureau
        using live World Conquest war state, casualty statistics, and victory town progress.

        Args:
            faction: Faction perspective: 'Warden' (Caoivish/Archon) or 'Colonial' (Mesean/Republic)
            shard: Target shard: 'live-1', 'live-2', 'live-3', or 'dev'
        """
        war_state = await self.war_client.get_war_state(shard=shard)
        vt_status = await self.war_client.get_victory_town_status(shard=shard)
        casualties = await self.war_client.get_global_casualties(shard=shard, top_n=3)

        timestamp, seq_code = _format_zulu_timestamp()
        faction_clean = faction.strip().lower()
        is_warden = not faction_clean.startswith("col")

        war_num = war_state.war_number if war_state else 0
        day_of_war = casualties.day_of_war if casualties else 1
        req_vt = vt_status.required_to_win if vt_status else 32
        w_vt = vt_status.warden_captured if vt_status else 0
        c_vt = vt_status.colonial_captured if vt_status else 0

        tot_w_cas = casualties.warden_casualties if casualties else 0
        tot_c_cas = casualties.colonial_casualties if casualties else 0
        active_fronts = casualties.most_active_fronts if casualties else []

        top_front_names = [
            _clean_hex_name(f["map_name"])
            for f in active_fronts
            if isinstance(f, dict) and "map_name" in f
        ]
        top_fronts_str = ", ".join(top_front_names) if top_front_names else "THE CENTRAL THEATER"

        lines: list[str] = []

        if is_warden:
            header = f"""ZCZC REG{seq_code} {timestamp}
TRANSMISSION: ARCHON PRESS WIRELESS SERVICE // FREQUENCY 4200 KC
FOR IMMEDIATE REVISION AND MORNING BROADSHEET INCLUSION
PASS IMPERIAL WARDEN CENSORSHIP BUREAU // STAMP 09-CLEAR

DATELINE: KIRKNELL REAR-GUARD DEPOT —"""

            lines.append(
                f"THE MINISTRY OF WAR COMMENDS THE INDOMITABLE VALOR OF THE NORTHERN CALLAHAN RESERVES "
                f"ON CONQUEST DAY {day_of_war} OF WAR {war_num} STOP"
            )

            # Victory towns spin
            if w_vt >= c_vt:
                lines.append(
                    f"OUR ARMIES FIRMLY ANCHOR {w_vt} OF {req_vt} REQUIRED VICTORY TOWNS AGAINST SOUTHERN INCURSIONS STOP "
                    "ADVANCING ECHELONS SECURE CRUCIAL BASTIONS ALONG SACRED SOIL STOP"
                )
            else:
                lines.append(
                    f"DESPITE HEAVY AGGRESSOR SWELLS OUR WARDS OCCUPY {w_vt} CITADELS MAINTAINING IMPREGNABLE FORTRESS POSITIONS STOP "
                    "REINFORCING COLUMNS MOBILIZE ACROSS CALLAHAN'S REACH STOP"
                )

            # Casualty spin
            lines.append(
                f"AGGRESSOR CASUALTIES NATIONWIDE SURPASS {tot_c_cas:,} KILLED IN FOOLHARDY INVASION PUSHES STOP "
                f"THE BUTCHER'S BILL AT {top_fronts_str} HAS BROKEN THE SPINE OF SOUTHERN COHORTS STOP"
            )

            # Homefront production
            lines.append(
                "YOUTH AND INDUSTRIAL VOLUNTEERS IN BACKLINE FACTORIES EXCEED EXPEDITIONARY MUNITION AND REFINED METAL QUOTAS STOP "
                "EVERY HAMMER STRIKE EXPELS THE INVADER STOP"
            )

            # Security alert & Motto
            lines.append(
                "GENERAL STAFF CAUTIONS CITIZENS TO REPORT UNREGISTERED RADIO-WIRE RECEIVERS PROMPTLY STOP "
                "CARELESS LIPS BURN SUPPLY LINES STOP"
            )
            lines.append("LONG LIVE THE ARCHON STOP FOR CAOIVA AND CALLAHAN STOP AR\n\nNNNN")

        else:
            header = f"""ZCZC REP{seq_code} {timestamp}
TRANSMISSION: MESEAN TRIBUNE WIRE POOL // FREQUENCY 7350 KC
FOR IMMEDIATE REVISION AND MORNING BROADSHEET INCLUSION
PASS REPUBLIC CENSORATE LEGION-SECTOR // STAMP 04-CLEAR

DATELINE: THERIZO REVOLUTIONARY ASSEMBLY PRESS BUREAU —"""

            lines.append(
                f"THE MESEAN CENTRAL COMMITTEE SALUTES THE ADVANCING LEGIONS OF THE REPUBLIC "
                f"ON OPERATIONAL DAY {day_of_war} OF WORLD CAMPAIGN {war_num} STOP"
            )

            # Victory towns spin
            if c_vt >= w_vt:
                lines.append(
                    f"THE REPUBLIC LIBERATES {c_vt} STRATEGIC VICTORY TOWNS TOWARD THE FINAL OBJECTIVE OF {req_vt} STOP "
                    "CORRUPT IMPERIAL BULWARKS CONTINUE TO CRUMBLE BEFORE PROLETARIAN COHORTS STOP"
                )
            else:
                lines.append(
                    f"BRAVE ALLIED LEGIONS OCCUPY {c_vt} CRITICAL OBJECTIVES ENGAGING IN HEROIC GRINDING ACTIONS STOP "
                    "FRESH DIVISIONS MARCH FROM SOUTHERN CAMPS TO CRUSH THE NORTHERN DESPOT STOP"
                )

            # Casualty spin
            lines.append(
                f"IMPERIAL ENEMY LOSSES MOUNT RAPIDLY WITH OVER {tot_w_cas:,} CONFIRMED NORTHERN FORCES REMOVED FROM BATTLE STOP "
                f"UNBREAKABLE RESISTANCE ACROSS {top_fronts_str} PROVES REPUBLIC RESOLVE STOP"
            )

            # Homefront production
            lines.append(
                "SOUTHERN FOUNDRIES AND WORKSHOPS OPERATE AT CONTINUOUS PEAK CAPACITY EXCEEDING BMAT AND SHELL TARGETS STOP "
                "LOGISTICS TRUCKERS HAUL NIGHT AND DAY FOR LIBERATION STOP"
            )

            # Security alert & Motto
            lines.append(
                "CITIZEN GUARDS DIRECTED TO VIGILANTLY DETAIN SUSPECTED ROYALIST SABOTEURS AND CONTRABAND TRANSMITTERS STOP"
            )
            lines.append(
                "FOR THE REPUBLIC AND THE FEDERATION STOP DEATH TO TYRANTS STOP AR\n\nNNNN"
            )

        body_text = "\n\n".join(lines)
        return f"{header}\n\n{body_text}"


default_dispatches_tools = DispatchesTools()


def __getattr__(name: str):
    """PEP 562: delegate attribute access to the default singleton instance."""
    return getattr(default_dispatches_tools, name)


def __dir__():
    """PEP 562: return dir of the default singleton instance merged with module attributes."""
    attrs = set(globals().keys())
    attrs.update(dir(default_dispatches_tools))
    return sorted(attrs)
