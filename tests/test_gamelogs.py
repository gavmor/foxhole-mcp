"""Tests for gamelogs: parsing, archival, timeline, and redaction.

All fixtures are synthetic and redacted — no real player Steam IDs, IPs, or log content.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from foxhole.gamelogs import (
    LogEvent,
    _parse_open_time,
    archive_logs,
    build_timeline,
    load_sessions,
    parse_log,
    redact,
    region_to_hex,
)

# ---------------------------------------------------------------------------
# Synthetic log fixtures (redacted, generated from the real format)
# ---------------------------------------------------------------------------

# Single session: join → auth HomeRegion → auth SpeakingWoods via travel
LOG_ONE_SESSION = """\
﻿Log file open, 09/29/26 14:43:42
[2026.09.29-21.43.42:000][  0]LogClient: Example startup noise
[2026.09.29-21.46.38:320][867]LogClient: Joining shard 5 (LIVE)
[2026.09.29-21.46.39:154][917]LogClient: Session 24458 expires in 7199.846000s
[2026.09.29-21.47.13:064][754]LogClient: Authenticated in-game for HomeRegionWE-5
[2026.09.29-21.47.05:061][471]LogClient: Warning: UAsyncTaskManager::Tick (FExternalWarService::DeploySuccessNotification_915) - Task took 0.006517s
[2026.09.29-23.29.34:916][269]LogClient: Start travel countdown
[2026.09.29-23.29.40:632][612]LogClient: Server travel success, Travel Id: 0, connecting to server SpeakingWoods-5
[2026.09.29-23.29.52:418][952]LogClient: Authenticated in-game for SpeakingWoods-5
"""

# Two sessions in one file (second join closes the first)
LOG_TWO_SESSIONS = """\
Log file open, 09/29/26 14:43:42
[2026.09.29-21.46.38:320][867]LogClient: Joining shard 5 (LIVE)
[2026.09.29-21.47.13:064][754]LogClient: Authenticated in-game for HomeRegionWE-5
[2026.09.30-19.27.43:957][220]LogClient: Joining shard 5 (LIVE)
[2026.09.30-19.28.00:000][300]LogClient: Authenticated in-game for SpeakingWoods-5
[2026.09.30-19.35.00:000][400]LogClient: Server travel success, Travel Id: 1, connecting to server MooringCounty-5
[2026.09.30-19.35.30:000][450]LogClient: Authenticated in-game for MooringCounty-5
"""

# Log with queue event
LOG_WITH_QUEUE = """\
Log file open, 10/01/26 10:00:00
[2026.10.01-17.48.00:000][100]LogClient: Joining shard 5 (LIVE)
[2026.10.01-17.48.33:067][667]LogClient: Server travel resulted in queue: 6, party size 1, dry run 0
[2026.10.01-17.49.33:067][700]LogClient: Server travel resulted in queue: 3, party size 1, dry run 1
[2026.10.01-17.50.00:000][800]LogClient: Authenticated in-game for ClansheadValley-5
"""

# Log with privacy-sensitive content
LOG_WITH_SENSITIVE = """\
Log file open, 10/02/26 15:50:45
[2026.10.02-22.50.45:000][  0]LogClient: ID: 76561197991180592 (d41d8cd98f00b204) (NzY1NjExOTc5OTExODA1OTI=)
[2026.10.02-22.50.46:000][  1]LogClient: connecting to 10.0.0.42:7778
[2026.10.02-22.50.47:000][  2]LogClient: Joining shard 5 (LIVE)
"""

# Log for MarbanHollow (no Hex suffix)
LOG_MARBAN = """\
Log file open, 10/02/26 06:00:00
[2026.10.02-13.00.00:000][  1]LogClient: Joining shard 5 (LIVE)
[2026.10.02-13.01.00:000][  2]LogClient: Authenticated in-game for MarbanHollow-5
"""


# ---------------------------------------------------------------------------
# _parse_open_time
# ---------------------------------------------------------------------------


def test_parse_open_time_basic() -> None:
    dt = _parse_open_time("Log file open, 09/29/26 14:43:42\n")
    assert dt == datetime(2026, 9, 29, 14, 43, 42)


def test_parse_open_time_with_bom() -> None:
    # BOM prefix is stripped
    dt = _parse_open_time("﻿Log file open, 10/02/26 19:02:58\n")
    assert dt == datetime(2026, 10, 2, 19, 2, 58)


def test_parse_open_time_missing() -> None:
    dt = _parse_open_time("not a log file\n[2026.09.29-21.46.38:320][867]LogClient: noise\n")
    assert dt is None


def test_parse_open_time_is_local_not_utc() -> None:
    # The header timestamp is local time (14:43:42), while bracket lines are UTC (21:46 UTC)
    dt = _parse_open_time(LOG_ONE_SESSION)
    assert dt is not None
    assert dt.hour == 14  # local, not the UTC bracket hours


# ---------------------------------------------------------------------------
# region_to_hex
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("log_name", "expected"),
    [
        ("SpeakingWoods", "SpeakingWoodsHex"),
        ("MooringCounty", "MooringCountyHex"),
        ("ClansheadValley", "ClansheadValleyHex"),
        ("WeatheredExpanse", "WeatheredExpanseHex"),
        ("Clahstra", "ClahstraHex"),
        ("MarbanHollow", "MarbanHollow"),  # exception: no Hex suffix
        ("HomeRegionW", "HomeRegionW"),  # home region: no suffix
        ("HomeRegionE", "HomeRegionE"),
        ("HomeRegionWE", "HomeRegionWE"),
        ("SpeakingWoodsHex", "SpeakingWoodsHex"),  # already has suffix → unchanged
    ],
)
def test_region_to_hex(log_name: str, expected: str) -> None:
    assert region_to_hex(log_name) == expected


# ---------------------------------------------------------------------------
# redact
# ---------------------------------------------------------------------------


def test_redact_steam_id() -> None:
    text = "ID: 76561197991180592 something"
    result = redact(text)
    assert "76561197991180592" not in result
    assert "[STEAMID]" in result


def test_redact_b64_steam_id() -> None:
    text = "ID: NzY1NjExOTc5OTExODA1OTI= (base64)"
    result = redact(text)
    assert "NzY1NjExOTc5OTExODA1OTI=" not in result
    assert "[STEAMID_B64]" in result


def test_redact_ip() -> None:
    text = "connecting to 10.0.0.42:7778"
    result = redact(text)
    assert "10.0.0.42" not in result
    assert "[IP]" in result


def test_redact_leaves_other_content() -> None:
    text = "LogClient: Joining shard 5 (LIVE)"
    assert redact(text) == text


# ---------------------------------------------------------------------------
# parse_log
# ---------------------------------------------------------------------------


def test_parse_log_session_start() -> None:
    events = parse_log(LOG_ONE_SESSION)
    starts = [e for e in events if e.kind == "session_start"]
    assert len(starts) == 1
    s = starts[0]
    assert s.shard == 5
    assert s.utc == datetime(2026, 9, 29, 21, 46, 38, 320000, tzinfo=UTC)


def test_parse_log_region_enter() -> None:
    events = parse_log(LOG_ONE_SESSION)
    entries = [e for e in events if e.kind == "region_enter"]
    assert len(entries) == 2
    assert entries[0].raw_region == "HomeRegionWE"
    assert entries[0].region == "HomeRegionWE"  # no Hex suffix for home regions
    assert entries[1].raw_region == "SpeakingWoods"
    assert entries[1].region == "SpeakingWoodsHex"


def test_parse_log_border_crossing() -> None:
    events = parse_log(LOG_ONE_SESSION)
    crossings = [e for e in events if e.kind == "border_crossing"]
    assert len(crossings) == 1
    c = crossings[0]
    assert c.raw_region == "SpeakingWoods"
    assert c.region == "SpeakingWoodsHex"
    assert c.extra["travel_id"] == 0


def test_parse_log_deploy() -> None:
    events = parse_log(LOG_ONE_SESSION)
    deploys = [e for e in events if e.kind == "deploy"]
    assert len(deploys) == 1


def test_parse_log_queue() -> None:
    events = parse_log(LOG_WITH_QUEUE)
    queues = [e for e in events if e.kind == "queue"]
    assert len(queues) == 2
    q0 = queues[0]
    assert q0.extra["queue_size"] == 6
    assert q0.extra["party_size"] == 1
    assert q0.extra["dry_run"] is False
    q1 = queues[1]
    assert q1.extra["dry_run"] is True


def test_parse_log_marban_no_hex() -> None:
    events = parse_log(LOG_MARBAN)
    entries = [e for e in events if e.kind == "region_enter"]
    assert entries[0].region == "MarbanHollow"


def test_parse_log_utc_not_local() -> None:
    # The header says 14:43:42 local; the bracket lines carry 21:46 UTC
    events = parse_log(LOG_ONE_SESSION)
    start = next(e for e in events if e.kind == "session_start")
    assert start.utc.hour == 21  # UTC, not 14 local


def test_parse_log_no_sensitive_in_events() -> None:
    # Steam IDs and IPs appear in raw log lines but are not in event fields
    events = parse_log(LOG_WITH_SENSITIVE)
    for e in events:
        for val in [e.region, e.raw_region]:
            if val:
                assert "76561197" not in val
                assert "10.0.0" not in val


# ---------------------------------------------------------------------------
# build_timeline
# ---------------------------------------------------------------------------


def test_build_timeline_single_session() -> None:
    events = parse_log(LOG_ONE_SESSION)
    sessions = build_timeline(events, source_file="test.log")
    assert len(sessions) == 1
    s = sessions[0]
    assert s.shard == 5
    assert s.crossings == 1
    assert s.deploys == 1
    assert s.source_file == "test.log"
    assert s.ended_utc is None  # last session in file has no end


def test_build_timeline_region_order() -> None:
    events = parse_log(LOG_ONE_SESSION)
    sessions = build_timeline(events)
    regions = sessions[0].regions
    assert len(regions) == 2
    assert regions[0].hex == "HomeRegionWE"
    assert regions[1].hex == "SpeakingWoodsHex"


def test_build_timeline_region_duration() -> None:
    events = parse_log(LOG_ONE_SESSION)
    sessions = build_timeline(events)
    r0 = sessions[0].regions[0]
    # HomeRegionWE entered at 21:47:13, left when SpeakingWoods travel starts at 23:29:40
    # (border_crossing is at 23:29:40, but region_enter closes at the Authenticated event 23:29:52)
    # Actually border_crossing doesn't close the region; the next region_enter does.
    # So HomeRegionWE left at 23:29:52 (the SpeakingWoods Authenticated line)
    assert r0.hex == "HomeRegionWE"
    assert r0.duration_seconds is not None
    assert r0.duration_seconds > 0


def test_build_timeline_two_sessions() -> None:
    events = parse_log(LOG_TWO_SESSIONS)
    sessions = build_timeline(events)
    assert len(sessions) == 2
    # First session: closed when second join fires
    s0 = sessions[0]
    assert s0.ended_utc is not None
    assert len(s0.regions) == 1
    assert s0.regions[0].hex == "HomeRegionWE"
    # Second session: has two regions
    s1 = sessions[1]
    assert len(s1.regions) == 2
    assert s1.regions[0].hex == "SpeakingWoodsHex"
    assert s1.regions[1].hex == "MooringCountyHex"
    assert s1.crossings == 1


def test_build_timeline_empty() -> None:
    assert build_timeline([]) == []


def test_build_timeline_events_before_session() -> None:
    # Events appearing before any "Joining shard" are ignored
    events = [
        LogEvent(
            kind="region_enter",
            utc=datetime(2026, 9, 29, 21, 0, 0, tzinfo=UTC),
            shard=5,
            region="SpeakingWoodsHex",
            raw_region="SpeakingWoods",
        )
    ]
    sessions = build_timeline(events)
    assert sessions == []


# ---------------------------------------------------------------------------
# archive_logs
# ---------------------------------------------------------------------------


def test_archive_logs_basic(tmp_path: Path) -> None:
    log = (
        "Log file open, 09/29/26 14:43:42\n"
        "[2026.09.29-21.46.38:320][867]LogClient: Joining shard 5 (LIVE)\n"
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "War.log").write_text(log)
    dest = tmp_path / "archive"

    result = archive_logs(dest=dest, source_dirs=[src])

    assert result["total_sessions"] == 1
    assert len(result["archived"]) == 1
    archived_name = result["archived"][0]
    assert archived_name.startswith("War-backup-2026.09.29-14.43.42")
    assert (dest / archived_name).exists()


def test_archive_logs_idempotent(tmp_path: Path) -> None:
    log = (
        "Log file open, 09/29/26 14:43:42\n"
        "[2026.09.29-21.46.38:320][867]LogClient: Joining shard 5 (LIVE)\n"
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "War.log").write_text(log)
    dest = tmp_path / "archive"

    r1 = archive_logs(dest=dest, source_dirs=[src])
    r2 = archive_logs(dest=dest, source_dirs=[src])

    assert len(r1["archived"]) == 1
    assert len(r2["archived"]) == 0  # already present
    assert len(r2["skipped_existing"]) == 1


def test_archive_logs_deduplication(tmp_path: Path) -> None:
    # Same open_time in two files (War.log and its rotation backup) → only one archived
    log = (
        "Log file open, 09/29/26 14:43:42\n"
        "[2026.09.29-21.46.38:320][867]LogClient: Joining shard 5 (LIVE)\n"
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "War.log").write_text(log)
    (src / "War-backup-2026.09.30-00.53.29.log").write_text(log)  # same open_time
    dest = tmp_path / "archive"

    result = archive_logs(dest=dest, source_dirs=[src])

    assert result["total_sessions"] == 1
    assert len(result["archived"]) == 1


def test_archive_logs_prefers_larger_file(tmp_path: Path) -> None:
    # When two files share an open_time, the larger one wins
    small = "Log file open, 09/29/26 14:43:42\n" + "x" * 10
    large = "Log file open, 09/29/26 14:43:42\n" + "x" * 100
    src = tmp_path / "src"
    src.mkdir()
    (src / "War.log").write_text(small)
    (src / "War-backup-2026.09.30-00.53.29.log").write_text(large)
    dest = tmp_path / "archive"

    result = archive_logs(dest=dest, source_dirs=[src])
    assert len(result["archived"]) == 1
    archived_path = dest / result["archived"][0]
    assert archived_path.stat().st_size == len(large.encode())


def test_archive_logs_no_sources(tmp_path: Path) -> None:
    result = archive_logs(dest=tmp_path / "archive", source_dirs=[])
    assert "warning" in result
    assert result["total_sessions"] == 0


def test_archive_logs_names_by_open_time(tmp_path: Path) -> None:
    log = (
        "Log file open, 10/02/26 15:50:45\n"
        "[2026.10.02-22.50.45:000][  1]LogClient: Joining shard 5 (LIVE)\n"
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "War-backup-2026.10.02-17.13.06.log").write_text(log)  # rotation-time name
    dest = tmp_path / "archive"

    result = archive_logs(dest=dest, source_dirs=[src])
    # Should be named by the OPEN time (15:50:45), not the rotation time (17:13:06)
    assert len(result["archived"]) == 1
    assert "15.50.45" in result["archived"][0]


def test_archive_compatible_with_existing_archive(tmp_path: Path) -> None:
    # Simulate the user's existing archive: files named by rotation time
    existing_log = (
        "Log file open, 09/29/26 14:43:42\n"
        "[2026.09.29-21.46.38:320][867]LogClient: Joining shard 5 (LIVE)\n"
    )
    dest = tmp_path / "archive"
    dest.mkdir()
    # User's existing file uses rotation time as name
    (dest / "War-backup-2026.09.30-00.53.29.log").write_text(existing_log)

    # New source with the same session (same open_time)
    src = tmp_path / "src"
    src.mkdir()
    (src / "War.log").write_text(existing_log)

    result = archive_logs(dest=dest, source_dirs=[src])
    # Already in archive by open_time scan → skip
    assert len(result["archived"]) == 0
    assert len(result["skipped_existing"]) == 1


# ---------------------------------------------------------------------------
# load_sessions
# ---------------------------------------------------------------------------


def test_load_sessions_from_archive(tmp_path: Path) -> None:
    log = LOG_ONE_SESSION
    (tmp_path / "War-backup-2026.09.29-14.43.42.log").write_text(log)

    sessions = load_sessions(archive_dir=tmp_path)
    assert len(sessions) == 1
    assert sessions[0].shard == 5


def test_load_sessions_deduplication(tmp_path: Path) -> None:
    # Same session in two differently-named files → deduplicated
    log = LOG_ONE_SESSION
    (tmp_path / "War-backup-2026.09.29-14.43.42.log").write_text(log)
    (tmp_path / "War-backup-2026.09.30-00.53.29.log").write_text(log)  # rotation-time copy

    sessions = load_sessions(archive_dir=tmp_path)
    assert len(sessions) == 1


def test_load_sessions_since_filter(tmp_path: Path) -> None:
    (tmp_path / "War-backup-2026.09.29-14.43.42.log").write_text(LOG_ONE_SESSION)

    cutoff = datetime(2026, 9, 30, 0, 0, 0, tzinfo=UTC)
    sessions = load_sessions(archive_dir=tmp_path, since=cutoff)
    assert sessions == []  # session started before the cutoff


def test_load_sessions_empty_dir(tmp_path: Path) -> None:
    sessions = load_sessions(archive_dir=tmp_path)
    assert sessions == []


def test_load_sessions_missing_dir() -> None:
    sessions = load_sessions(archive_dir="/nonexistent/path/12345")
    assert sessions == []


# ---------------------------------------------------------------------------
# Integration: parse → timeline → archive round-trip
# ---------------------------------------------------------------------------


def test_full_round_trip(tmp_path: Path) -> None:
    src = tmp_path / "logs"
    src.mkdir()
    (src / "War.log").write_text(LOG_TWO_SESSIONS)

    dest = tmp_path / "archive"
    ar = archive_logs(dest=dest, source_dirs=[src])
    assert len(ar["archived"]) == 1

    sessions = load_sessions(archive_dir=dest)
    assert len(sessions) == 2
    # Check region names are War API names
    assert sessions[0].regions[0].hex == "HomeRegionWE"
    assert sessions[1].regions[0].hex == "SpeakingWoodsHex"
    assert sessions[1].regions[1].hex == "MooringCountyHex"
