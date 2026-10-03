"""Tests for the in-game clock (one in-game day per real hour)."""

import math

import pytest

from foxhole import ingame_time as igt

START_MS = 1_790_874_000_447  # War 141 conquestStartTime
START = START_MS / 1000
HOUR = 3600.0


def fresh():
    return igt.ensure_war(None, "war-141", "live-1", START_MS)


def test_day_of_war_bounds_the_phase():
    # Live War API: dayOfWar 27 at 25.864 h after the start
    s = igt.observe_day(fresh(), 27, START + 25.864 * HOUR)
    assert s.lo == pytest.approx(0.136) and s.hi == pytest.approx(1.136)
    clock = igt.to_ingame(s, START + 25.864 * HOUR)
    assert clock is not None and not clock.calibrated
    assert clock.plus_minus_minutes == pytest.approx(720)  # half a day either way


def test_observations_tighten_the_interval():
    s = fresh()
    s = igt.observe_day(s, 27, START + 25.864 * HOUR)
    s = igt.observe_day(s, 27, START + 26.40 * HOUR)  # still day 27: phase < 0.6
    s = igt.observe_day(s, 28, START + 26.60 * HOUR)  # rolled over: phase >= 0.4
    assert (s.lo, s.hi) == (pytest.approx(0.4), pytest.approx(0.6))
    assert s.observations == 3


def test_hud_reading_pins_the_clock():
    s = igt.observe_day(fresh(), 27, START + 25.864 * HOUR)
    # "Day 27, 0627" read at 25.8 h after the start
    s = igt.calibrate(s, 27, "0627", START + 25.8 * HOUR, note="Day 27, 0627")
    assert s.pinned == pytest.approx(26 + 387 / 1440 - 25.8)
    later = igt.to_ingame(s, START + 25.8 * HOUR + 2.5 * 60)  # one in-game hour later
    assert later is not None and (later.label, later.calibrated) == ("Day 27, 0727 Hours", True)


def test_contradictory_reading_is_rejected():
    s = igt.observe_day(fresh(), 27, START + 25.864 * HOUR)
    with pytest.raises(ValueError, match="contradicts"):
        igt.calibrate(s, 25, "1200", START + 25.864 * HOUR)
    with pytest.raises(ValueError, match="military time"):
        igt.calibrate(s, 27, "2460", START + 25.864 * HOUR)


def test_pin_dropped_when_api_contradicts_it():
    s = igt.observe_day(fresh(), 27, START + 25.864 * HOUR)
    s = igt.calibrate(s, 27, "0627", START + 25.8 * HOUR)
    s = igt.observe_day(s, 29, START + 26.0 * HOUR)  # impossible two-day jump
    assert s.pinned is None and s.pinned_note == "dropped: contradicted by dayOfWar"


def test_new_war_resets_calibration():
    s = igt.calibrate(
        igt.observe_day(fresh(), 27, START + 25.864 * HOUR), 27, "0627", START + 25.8 * HOUR
    )
    other = igt.ensure_war(s, "war-142", "live-1", START_MS + 10**9)
    assert other.pinned is None and math.isinf(other.lo)


def test_offline_label_from_saved_state():
    assert igt.ingame_label(START + HOUR) is None  # nothing saved yet
    s = igt.observe_day(fresh(), 27, START + 25.864 * HOUR)
    igt.save_state(s)
    # uncalibrated: clock too vague, so give the day (or both candidates near a rollover)
    assert igt.ingame_label(START + 25.864 * HOUR) in ("Day 27", "Day 26 or 27", "Day 27 or 28")
    igt.save_state(igt.calibrate(s, 27, "0627", START + 25.8 * HOUR))
    assert igt.ingame_label(START + 25.8 * HOUR) == "Day 27, 0627 Hours"
    assert igt.ingame_label(START - 60) is None  # before this war


def test_minute_precision_reading_widens_uncertainty():
    s = igt.observe_day(fresh(), 27, START + 25.864 * HOUR)
    s = igt.calibrate(s, 27, "0627", START + 25.8 * HOUR, real_seconds_uncertain=30)
    clock = igt.to_ingame(s, START + 25.8 * HOUR)
    assert clock is not None and clock.plus_minus_minutes == pytest.approx(13.5)  # 1.5 + 12
