"""Foxhole in-game time: the Map Screen's "Day N, HHMM Hours" clock.

A 24-hour in-game day lasts one real hour (wiki: Day-Night Cycle), so the clock is
    day_float(t) = (t - conquestStartTime) / 1h + phase
    day = floor(day_float) + 1,  clock = frac(day_float) * 24h
The War API gives the war start and, in every `warReport`, the integer `dayOfWar`, but not
the phase (day 1 does not begin at the war's start). Each `dayOfWar` observation bounds
the phase to a one-day interval and the bounds tighten as observations accumulate,
especially ones near a day rollover. A reading of the in-game clock (`calibrate`) pins it
exactly. The calibration is kept per war in the cache so offline tools can convert too.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from foxhole.cargo import default_cache_dir

REAL_SECONDS_PER_DAY = 3600.0  # one in-game day per real hour
HUD_TOLERANCE_DAYS = 1.5 / (24 * 60)  # a HUD reading is good to about ±1.5 in-game minutes


class ClockState(BaseModel):
    war_id: str
    shard: str
    start_ms: int
    lo: float = Field(default=-math.inf, description="Lower bound on phase (days)")
    hi: float = Field(default=math.inf, description="Upper bound on phase (days)")
    pinned: float | None = Field(default=None, description="Phase from an in-game clock reading")
    pinned_note: str | None = None
    pinned_spread: float = Field(default=0.0, description="Uncertainty of the pin (days)")
    observations: int = 0


class InGameTime(BaseModel):
    day: int
    hhmm: str
    label: str = Field(description='e.g. "Day 27, 0627 Hours"')
    plus_minus_minutes: float = Field(description="Uncertainty in in-game minutes")
    calibrated: bool = Field(description="Pinned to an in-game clock reading")


def _path() -> Path:
    return default_cache_dir().parent / "ingame_clock.json"


def _load_all() -> dict[str, Any]:
    p = _path()
    return json.loads(p.read_text()) if p.exists() else {}


def load_state(shard: str = "live-1") -> ClockState | None:
    raw = _load_all().get(shard)
    if not raw:
        return None
    for k in ("lo", "hi"):  # JSON has no infinity
        raw[k] = float(raw[k]) if raw.get(k) is not None else (-math.inf if k == "lo" else math.inf)
    return ClockState.model_validate(raw)


def save_state(state: ClockState) -> None:
    data = _load_all()
    raw = state.model_dump()
    raw["lo"] = None if math.isinf(state.lo) else state.lo
    raw["hi"] = None if math.isinf(state.hi) else state.hi
    data[state.shard] = raw
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1))


def _elapsed_days(state: ClockState, t: float) -> float:
    return (t - state.start_ms / 1000.0) / REAL_SECONDS_PER_DAY


def ensure_war(state: ClockState | None, war_id: str, shard: str, start_ms: int) -> ClockState:
    """Start fresh calibration when the war changes."""
    if state is None or state.war_id != war_id or state.start_ms != start_ms:
        return ClockState(war_id=war_id, shard=shard, start_ms=start_ms)
    return state


def observe_day(state: ClockState, day_of_war: int, t: float) -> ClockState:
    """Narrow the phase interval with a `dayOfWar` value seen at Unix time t."""
    e = _elapsed_days(state, t)
    new_lo, new_hi = day_of_war - 1 - e, day_of_war - e
    lo, hi = max(state.lo, new_lo), min(state.hi, new_hi)
    if (
        lo >= hi
    ):  # contradicts earlier bounds (e.g. a rollover reported late): restart from this one
        lo, hi = new_lo, new_hi
    state.lo, state.hi = lo, hi
    state.observations += 1
    if state.pinned is not None and not (
        lo - HUD_TOLERANCE_DAYS <= state.pinned < hi + HUD_TOLERANCE_DAYS
    ):
        state.pinned, state.pinned_note = None, "dropped: contradicted by dayOfWar"
    return state


def calibrate(
    state: ClockState,
    day: int,
    hhmm: str,
    t: float,
    note: str = "",
    real_seconds_uncertain: float = 0.0,
) -> ClockState:
    """Pin the phase from an in-game clock reading ("Day 27, 0627") taken at Unix time t.

    `real_seconds_uncertain` is how imprecisely t is known (e.g. 30 if only the minute is).
    """
    hh, mm = int(hhmm[:2]), int(hhmm[2:])
    if not (0 <= hh < 24 and 0 <= mm < 60):
        raise ValueError(f"Bad in-game time {hhmm!r}; use military time like 0627")
    phase = (day - 1) + (hh * 60 + mm) / (24 * 60) - _elapsed_days(state, t)
    if not (state.lo - HUD_TOLERANCE_DAYS <= phase < state.hi + HUD_TOLERANCE_DAYS):
        raise ValueError(
            f"Day {day}, {hhmm} at that moment contradicts the War API's dayOfWar "
            f"(phase must be in [{state.lo:.3f}, {state.hi:.3f}) days, reading gives {phase:.3f})."
        )
    state.pinned, state.pinned_note = phase, note or None
    state.pinned_spread = HUD_TOLERANCE_DAYS + real_seconds_uncertain / REAL_SECONDS_PER_DAY
    return state


def to_ingame(state: ClockState, t: float) -> InGameTime | None:
    """In-game day and clock at Unix time t, or None if the phase is still unbounded."""
    if state.pinned is not None:
        phase, spread = state.pinned, state.pinned_spread or HUD_TOLERANCE_DAYS
    elif math.isfinite(state.lo) and math.isfinite(state.hi):
        phase, spread = (state.lo + state.hi) / 2, (state.hi - state.lo) / 2
    else:
        return None
    d = _elapsed_days(state, t) + phase + 1e-9  # guard float error at exact minute boundaries
    day = math.floor(d) + 1
    minutes = math.floor((d - math.floor(d)) * 24 * 60 + 1e-6)
    hhmm = f"{minutes // 60:02d}{minutes % 60:02d}"
    return InGameTime(
        day=day,
        hhmm=hhmm,
        label=f"Day {day}, {hhmm} Hours",
        plus_minus_minutes=round(spread * 24 * 60, 1),
        calibrated=state.pinned is not None,
    )


def ingame_label(t: float, shard: str = "live-1") -> str | None:
    """Best-effort "Day N, HHMM Hours" for Unix time t from the saved calibration (offline)."""
    state = load_state(shard)
    if state is None or t < state.start_ms / 1000.0:
        return None
    clock = to_ingame(state, t)
    if clock is None:
        return None
    if clock.plus_minus_minutes <= 90:
        return clock.label
    e = _elapsed_days(state, t)  # clock too uncertain: give the day, or both candidates
    first, last = math.floor(e + state.lo) + 1, math.floor(e + state.hi - 1e-9) + 1
    return f"Day {first}" if first == last else f"Day {first} or {last}"


def now() -> float:
    return time.time()
