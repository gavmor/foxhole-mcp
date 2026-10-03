"""MCP tools for Foxhole game log access: archive and session timeline."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from foxhole.gamelogs import DEFAULT_ARCHIVE_DIR, archive_logs, load_sessions
from foxhole.tools.base import BaseToolProvider

_NOT_LOGGED = (
    "Game logs do NOT contain: kills, deaths, building, crafting, item pickups, or chat. "
    "Only recorded: session connections, hex region entries, border crossings, and deploys."
)


class GameLogTools(BaseToolProvider):
    """Tools for accessing Foxhole client game logs."""

    async def archive_game_logs(
        self,
        dest: str | None = None,
    ) -> dict[str, Any]:
        """Copy Foxhole game logs from Steam/Proton into the archive directory.

        Idempotent and non-destructive: existing files are never overwritten. The live
        War.log and its rotation backup are recognised as the same session (by 'Log file
        open' timestamp) and stored only once, keeping the more complete copy.

        Note: game logs do not contain kills, deaths, building, crafting, items, or chat.
        They record session connections, hex region entries, border crossings, and deploys.

        Args:
            dest: Archive directory (default: FOXHOLE_LOG_ARCHIVE env var, then
                  ~/Documents/The 56th/logs)
        """
        return archive_logs(dest=dest)

    async def get_session_timeline(
        self,
        since: str | None = None,
        until: str | None = None,
        archive_dir: str | None = None,
    ) -> dict[str, Any]:
        """Session timeline from archived Foxhole game logs.

        Returns sessions with ordered hex region entries, time spent in each region,
        border crossing counts, and deploy counts. In-game timestamps ("Day N, HHMM Hours")
        are added when the clock is calibrated for the relevant war.

        Note: game logs do NOT contain kills, deaths, building, crafting, items, or chat.

        Args:
            since: Only sessions starting at or after this ISO datetime (UTC)
            until: Only sessions starting before this ISO datetime (UTC)
            archive_dir: Log archive directory (default: FOXHOLE_LOG_ARCHIVE env var,
                         then ~/Documents/The 56th/logs)
        """
        since_dt: datetime | None = None
        until_dt: datetime | None = None
        try:
            if since:
                since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
                if since_dt.tzinfo is None:
                    since_dt = since_dt.replace(tzinfo=UTC)
            if until:
                until_dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
                if until_dt.tzinfo is None:
                    until_dt = until_dt.replace(tzinfo=UTC)
        except ValueError as e:
            return {"error": f"Invalid datetime string: {e}"}

        sessions = load_sessions(archive_dir=archive_dir, since=since_dt, until=until_dt)
        return {
            "sessions": [s.model_dump(exclude_none=True) for s in sessions],
            "total": len(sessions),
            "archive_dir": archive_dir or DEFAULT_ARCHIVE_DIR,
            "note": _NOT_LOGGED,
        }


default_game_log_tools = GameLogTools()


def __getattr__(name: str) -> Any:
    return getattr(default_game_log_tools, name)
