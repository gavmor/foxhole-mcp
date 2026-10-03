"""Read and archive Foxhole client game logs; parse sessions, regions, and crossings.

Logs do not contain kills, deaths, building, crafting, item pickups, or chat. Only
session connections, hex region entries, border crossings, deploys, and queue events
are available here.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from foxhole.ingame_time import ingame_label
from foxhole.stockpiles import FOXHOLE_APP_ID, steam_libraries

PROTON_LOG_DIR = (
    f"steamapps/compatdata/{FOXHOLE_APP_ID}/pfx/drive_c/users/steamuser"
    "/AppData/Local/Foxhole/Saved/Logs"
)
DEFAULT_ARCHIVE_DIR = "~/Documents/The 56th/logs"

_HEADER_RE = re.compile(
    r"Log file open,\s*(\d{2}/\d{2}/\d{2})\s+(\d{2}:\d{2}:\d{2})", re.IGNORECASE
)
_LINE_RE = re.compile(
    r"^\[(\d{4})\.(\d{2})\.(\d{2})-(\d{2})\.(\d{2})\.(\d{2}):(\d{3})\]\[\s*\d+\](.+)$"
)

# Privacy redaction patterns
_STEAM_ID_RE = re.compile(r"\b7656119\d{10}\b")
_B64_ID_RE = re.compile(r"\bNzY1NjEx[A-Za-z0-9+/=]{10,}\b")
_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b")

# Gameplay event patterns (all from LogClient: lines)
_JOIN_RE = re.compile(r"LogClient: Joining shard (\d+) \(LIVE\)")
_AUTH_RE = re.compile(r"LogClient: Authenticated in-game for (\w+)-(\d+)")
_TRAVEL_RE = re.compile(
    r"LogClient: Server travel success, Travel Id: (\d+), connecting to server (\w+)-(\d+)"
)
_QUEUE_RE = re.compile(
    r"LogClient: Server travel resulted in queue: (\d+), party size (\d+), dry run (\d+)"
)
_DEPLOY_RE = re.compile(
    r"LogClient: Warning: UAsyncTaskManager::Tick "
    r"\(FExternalWarService::DeploySuccessNotification"
)


def redact(text: str) -> str:
    """Remove Steam IDs, base64 Steam IDs, and IP addresses from text."""
    text = _STEAM_ID_RE.sub("[STEAMID]", text)
    text = _B64_ID_RE.sub("[STEAMID_B64]", text)
    text = _IP_RE.sub("[IP]", text)
    return text


def region_to_hex(region: str) -> str:
    """Convert a log server name to the War API hex name.

    Log region names lack the 'Hex' suffix (e.g. 'SpeakingWoods' → 'SpeakingWoodsHex').
    Known exceptions that keep their name as-is: MarbanHollow and HomeRegion* servers.
    """
    if region == "MarbanHollow" or region.startswith("HomeRegion"):
        return region
    return region if region.endswith("Hex") else region + "Hex"


@dataclass
class LogEvent:
    """A single parsed event from a Foxhole game log."""

    kind: str  # session_start | region_enter | border_crossing | queue | deploy
    utc: datetime
    shard: int | None = None
    region: str | None = None  # War API hex name (e.g. 'SpeakingWoodsHex')
    raw_region: str | None = None  # server name as seen in log
    extra: dict[str, Any] = field(default_factory=dict)


class RegionStay(BaseModel):
    """Time spent in a single hex region within a session."""

    hex: str = Field(description="War API hex name (e.g. 'SpeakingWoodsHex')")
    shard: int
    entered_utc: str
    entered_ingame: str | None = None
    left_utc: str | None = None
    left_ingame: str | None = None
    duration_seconds: float | None = None


class SessionTimeline(BaseModel):
    """One play session with its ordered region history and event counts."""

    shard: int
    started_utc: str
    started_ingame: str | None = None
    ended_utc: str | None = None
    ended_ingame: str | None = None
    duration_seconds: float | None = None
    regions: list[RegionStay] = Field(default_factory=list)
    crossings: int = Field(default=0, description="Hex border crossings")
    deploys: int = Field(default=0, description="Deploy (respawn) events")
    source_file: str = ""


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def find_log_dirs() -> list[Path]:
    """Foxhole log directories on this machine.

    Checks Steam/Proton libraries (including Flatpak and extra vdf libraries) and
    Windows %LOCALAPPDATA%. FOXHOLE_LOG_PATH overrides discovery entirely.
    """
    if override := os.getenv("FOXHOLE_LOG_PATH"):
        return [Path(override).expanduser()]
    dirs: list[Path] = []
    for lib in steam_libraries():
        d = lib / PROTON_LOG_DIR
        if d.is_dir():
            dirs.append(d)
    if local := os.getenv("LOCALAPPDATA"):
        d = Path(local) / "Foxhole" / "Saved" / "Logs"
        if d.is_dir():
            dirs.append(d)
    return dirs


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------


def _parse_open_time(text: str) -> datetime | None:
    """Parse 'Log file open, MM/DD/YY HH:MM:SS' header into a naive local datetime."""
    for line in text.splitlines()[:5]:
        m = _HEADER_RE.match(line.lstrip("﻿"))
        if m:
            try:
                return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%m/%d/%y %H:%M:%S")
            except ValueError:
                return None
    return None


def _open_time_key(dt: datetime) -> str:
    return dt.strftime("%Y.%m.%d-%H.%M.%S")


def _scan_archive(dest: Path) -> dict[str, Path]:
    """Map open_time_key → existing file path for all War*.log files in dest."""
    known: dict[str, Path] = {}
    for f in sorted(dest.glob("War*.log")):
        try:
            text = f.read_text(encoding="utf-8-sig", errors="ignore")
        except OSError:
            continue
        t = _parse_open_time(text)
        if t:
            known.setdefault(_open_time_key(t), f)
    return known


def archive_logs(
    dest: str | Path | None = None,
    source_dirs: list[Path] | None = None,
) -> dict[str, Any]:
    """Copy Foxhole game logs into an archive directory, deduplicated by session open time.

    Safe to run repeatedly: existing files are never overwritten. The live War.log and its
    rotation backup (same 'Log file open' timestamp) are treated as the same session and
    stored only once, keeping the larger (more complete) copy.

    Names each session by when it opened, using the 'Log file open' local-time header.

    Args:
        dest: Archive directory (default: FOXHOLE_LOG_ARCHIVE env var, then DEFAULT_ARCHIVE_DIR)
        source_dirs: Override auto-discovered Steam/Proton log directories
    """
    if dest is None:
        dest = Path(os.getenv("FOXHOLE_LOG_ARCHIVE", DEFAULT_ARCHIVE_DIR)).expanduser()
    dest = Path(dest).expanduser()
    dest.mkdir(parents=True, exist_ok=True)

    if source_dirs is None:
        source_dirs = find_log_dirs()

    if not source_dirs:
        return {
            "dest": str(dest),
            "archived": [],
            "skipped_existing": [],
            "total_sessions": 0,
            "warning": (
                "No Foxhole log directories found. "
                "Set FOXHOLE_LOG_PATH to point at the Logs directory."
            ),
        }

    # Collect candidate sources keyed by open time; prefer larger (more complete) file
    candidates: dict[str, tuple[Path, int]] = {}
    for src_dir in source_dirs:
        for pattern in ("War.log", "War-backup-*.log"):
            for src in src_dir.glob(pattern):
                try:
                    text = src.read_text(encoding="utf-8-sig", errors="ignore")
                except OSError:
                    continue
                t = _parse_open_time(text)
                if t is None:
                    continue
                key = _open_time_key(t)
                size = src.stat().st_size
                if key not in candidates or size > candidates[key][1]:
                    candidates[key] = (src, size)

    existing = _scan_archive(dest)
    archived: list[str] = []
    skipped: list[str] = []
    for key in sorted(candidates):
        dst_name = f"War-backup-{key}.log"
        if key in existing:
            skipped.append(dst_name)
            continue
        dst = dest / dst_name
        if dst.exists():
            skipped.append(dst_name)
            continue
        src_path, _ = candidates[key]
        shutil.copy2(str(src_path), str(dst))
        archived.append(dst_name)

    return {
        "dest": str(dest),
        "archived": archived,
        "skipped_existing": skipped,
        "total_sessions": len(candidates),
    }


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _utc(yr: str, mo: str, dy: str, hh: str, mm: str, ss: str, ms: str) -> datetime:
    return datetime(
        int(yr),
        int(mo),
        int(dy),
        int(hh),
        int(mm),
        int(ss),
        int(ms) * 1000,
        tzinfo=UTC,
    )


def parse_log(text: str) -> list[LogEvent]:
    """Parse a Foxhole War.log file text into typed events.

    Yields: session_start, region_enter, border_crossing, queue, deploy.
    All timestamps are UTC (from the bracket format, not the local-time header).
    Privacy-sensitive fields (Steam IDs, IPs) appear in the raw log lines but are
    not surfaced in any event field here.
    """
    events: list[LogEvent] = []
    for line in text.splitlines():
        m = _LINE_RE.match(line)
        if not m:
            continue
        utc = _utc(*m.group(1, 2, 3, 4, 5, 6, 7))
        rest = m.group(8)

        if jm := _JOIN_RE.search(rest):
            events.append(LogEvent(kind="session_start", utc=utc, shard=int(jm.group(1))))
        elif am := _AUTH_RE.search(rest):
            raw = am.group(1)
            events.append(
                LogEvent(
                    kind="region_enter",
                    utc=utc,
                    shard=int(am.group(2)),
                    region=region_to_hex(raw),
                    raw_region=raw,
                )
            )
        elif tm := _TRAVEL_RE.search(rest):
            raw = tm.group(2)
            events.append(
                LogEvent(
                    kind="border_crossing",
                    utc=utc,
                    shard=int(tm.group(3)),
                    region=region_to_hex(raw),
                    raw_region=raw,
                    extra={"travel_id": int(tm.group(1))},
                )
            )
        elif qm := _QUEUE_RE.search(rest):
            events.append(
                LogEvent(
                    kind="queue",
                    utc=utc,
                    extra={
                        "queue_size": int(qm.group(1)),
                        "party_size": int(qm.group(2)),
                        "dry_run": int(qm.group(3)) != 0,
                    },
                )
            )
        elif _DEPLOY_RE.search(rest):
            events.append(LogEvent(kind="deploy", utc=utc))
    return events


# ---------------------------------------------------------------------------
# Timeline builder
# ---------------------------------------------------------------------------


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _dur(a: datetime, b: datetime) -> float:
    return round((b - a).total_seconds(), 1)


def _label(dt: datetime) -> str | None:
    return ingame_label(dt.timestamp())


class _SessionBuilder:
    """Stateful accumulator that converts a flat event stream into SessionTimelines."""

    def __init__(self, source_file: str) -> None:
        self.source_file = source_file
        self.sessions: list[SessionTimeline] = []
        self.session_start: datetime | None = None
        self.session_shard: int = 0
        self.region_start: datetime | None = None
        self.region_hex: str | None = None
        self.region_shard: int = 0
        self.regions: list[RegionStay] = []
        self.crossings: int = 0
        self.deploys: int = 0

    def close_region(self, until: datetime | None) -> None:
        region_hex = self.region_hex
        region_start = self.region_start
        if region_hex is None or region_start is None:
            return
        self.regions.append(
            RegionStay(
                hex=region_hex,
                shard=self.region_shard,
                entered_utc=_iso(region_start),
                entered_ingame=_label(region_start),
                left_utc=_iso(until) if until else None,
                left_ingame=_label(until) if until else None,
                duration_seconds=_dur(region_start, until) if until else None,
            )
        )
        self.region_start = self.region_hex = None

    def close_session(self, until: datetime | None) -> None:
        session_start = self.session_start
        if session_start is None:
            return
        self.close_region(until)
        self.sessions.append(
            SessionTimeline(
                shard=self.session_shard,
                started_utc=_iso(session_start),
                started_ingame=_label(session_start),
                ended_utc=_iso(until) if until else None,
                ended_ingame=_label(until) if until else None,
                duration_seconds=_dur(session_start, until) if until else None,
                regions=list(self.regions),
                crossings=self.crossings,
                deploys=self.deploys,
                source_file=self.source_file,
            )
        )
        self.session_start = None
        self.regions = []
        self.crossings = 0
        self.deploys = 0

    def feed(self, event: LogEvent) -> None:
        if event.kind == "session_start":
            self.close_session(event.utc)
            self.session_start = event.utc
            self.session_shard = event.shard or 0
        elif event.kind == "region_enter":
            self.close_region(event.utc)
            self.region_start = event.utc
            self.region_hex = event.region or "Unknown"
            self.region_shard = event.shard or self.session_shard
        elif event.kind == "border_crossing" and self.session_start is not None:
            self.crossings += 1
        elif event.kind == "deploy" and self.session_start is not None:
            self.deploys += 1


def build_timeline(events: list[LogEvent], source_file: str = "") -> list[SessionTimeline]:
    """Convert a flat event list into per-session timelines.

    Each 'Joining shard' opens a new session. Region stays close when the next
    region is entered or the session ends. Crossings and deploys are counted per session.
    """
    builder = _SessionBuilder(source_file)
    for event in events:
        builder.feed(event)
    builder.close_session(None)
    return builder.sessions


# ---------------------------------------------------------------------------
# Combined loader
# ---------------------------------------------------------------------------


def load_sessions(
    archive_dir: str | Path | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[SessionTimeline]:
    """Load and combine sessions from all War*.log files in an archive directory.

    Deduplicates sessions appearing in multiple files (same 'Log file open' timestamp),
    keeping the more complete copy (the one with an ended_utc, if available).

    Args:
        archive_dir: Log archive directory (default: FOXHOLE_LOG_ARCHIVE, then DEFAULT_ARCHIVE_DIR)
        since: Only sessions starting at or after this UTC datetime
        until: Only sessions starting before this UTC datetime
    """
    if archive_dir is None:
        archive_dir = Path(os.getenv("FOXHOLE_LOG_ARCHIVE", DEFAULT_ARCHIVE_DIR)).expanduser()
    adir = Path(archive_dir).expanduser()
    if not adir.is_dir():
        return []

    all_sessions: list[SessionTimeline] = []
    for f in sorted(adir.glob("War*.log")):
        try:
            text = f.read_text(encoding="utf-8-sig", errors="ignore")
        except OSError:
            continue
        for session in build_timeline(parse_log(text), source_file=f.name):
            dt = datetime.fromisoformat(session.started_utc)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            if since is not None and dt < since:
                continue
            if until is not None and dt > until:
                continue
            all_sessions.append(session)

    # Deduplicate: same started_utc → prefer the copy with ended_utc (more complete)
    seen: dict[str, SessionTimeline] = {}
    for s in all_sessions:
        key = s.started_utc
        if key not in seen or (s.ended_utc is not None and seen[key].ended_utc is None):
            seen[key] = s

    return sorted(seen.values(), key=lambda s: s.started_utc)
