"""Tests for story-7 hauling capacity (src/foxhole/lp/hauling.py)."""

from __future__ import annotations

import math

import pytest

from foxhole.lp.hauling import DEFAULT_VEHICLES, trip_constraint, trips_for_mixed_load, trips_needed

# ---------------------------------------------------------------------------
# Hand-checked reference orders (from story spec)
# ---------------------------------------------------------------------------

# Niska order: 6,315 Salvage + 2,100 Components + 200 Coal
# slots: ceil(6315/100)=64 + ceil(2100/100)=21 + ceil(200/100)=2 = 87 slots
# trips: ceil(87/20) = 5
NISKA = {"Salvage": 6315.0, "Components": 2100.0, "Coal": 200.0}

# FMC order: 22,570 Salvage + 5,400 Components
# slots: ceil(22570/100)=226 + ceil(5400/100)=54 = 280 slots
# trips: ceil(280/20) = 14
FMC = {"Salvage": 22570.0, "Components": 5400.0}


# ---------------------------------------------------------------------------
# DEFAULT_VEHICLES correctness
# ---------------------------------------------------------------------------


def test_loadlugger_slots():
    assert DEFAULT_VEHICLES["Dunne Loadlugger 3c"] == (20, 100)


def test_sisyphus_slots():
    # Wiki vehicles table reports 14 slots; the original stub had 20 (wrong).
    assert DEFAULT_VEHICLES["R-5b “Sisyphus” Hauler"] == (14, 100)


# ---------------------------------------------------------------------------
# trips_needed
# ---------------------------------------------------------------------------


def test_trips_niska_loadlugger():
    assert trips_needed(NISKA) == 5


def test_trips_fmc_loadlugger():
    assert trips_needed(FMC) == 14


def test_trips_niska_sisyphus():
    # 87 slots / 14 = ceil(6.21) = 7
    assert trips_needed(NISKA, "R-5b “Sisyphus” Hauler") == 7


def test_trips_fmc_sisyphus():
    # 280 slots / 14 = 20
    assert trips_needed(FMC, "R-5b “Sisyphus” Hauler") == 20


def test_trips_empty():
    assert trips_needed({}) == 0


def test_trips_single_item_exact():
    # 2000 Salvage -> 20 slots exactly -> 1 trip in Loadlugger
    assert trips_needed({"Salvage": 2000.0}) == 1


def test_trips_single_item_overflow():
    # 2001 Salvage -> 21 slots -> 2 trips in Loadlugger
    assert trips_needed({"Salvage": 2001.0}) == 2


def test_trips_unknown_vehicle():
    with pytest.raises(ValueError, match="Unknown vehicle"):
        trips_needed({"Salvage": 100.0}, vehicle="Ghost Truck")


# ---------------------------------------------------------------------------
# trip_constraint
# ---------------------------------------------------------------------------


def test_trip_constraint_loadlugger():
    c = trip_constraint(5.0)
    assert c.upper == 5.0
    assert c.name == "hauling_trips_Dunne Loadlugger 3c"
    # coeff = 1 / (100 * 20) = 0.0005 for each hauled item
    expected_coeff = 1 / 2000
    for item in ("Salvage", "Components", "Sulfur", "Coal"):
        assert item in c.coeffs
        assert math.isclose(c.coeffs[item], expected_coeff), (
            f"{item}: {c.coeffs[item]} != {expected_coeff}"
        )


def test_trip_constraint_sisyphus():
    c = trip_constraint(7.0, "R-5b “Sisyphus” Hauler")
    assert c.upper == 7.0
    # coeff = 1 / (100 * 14) ≈ 0.000714...
    expected_coeff = 1 / 1400
    for item in ("Salvage", "Components", "Sulfur", "Coal"):
        assert math.isclose(c.coeffs[item], expected_coeff)


def test_trip_constraint_unknown_vehicle():
    with pytest.raises(ValueError, match="Unknown vehicle"):
        trip_constraint(3.0, vehicle="Ghost Truck")


# ---------------------------------------------------------------------------
# trips_for_mixed_load
# ---------------------------------------------------------------------------


def test_mixed_load_niska_trip_count():
    plan = trips_for_mixed_load(NISKA)
    assert len(plan) == 5


def test_mixed_load_niska_all_slots_accounted():
    # Total slots from plan must equal total slots needed.
    plan = trips_for_mixed_load(NISKA)
    total_loaded = sum(slots for trip in plan for slots in trip.values())
    # 64 + 21 + 2 = 87 slots
    assert total_loaded == 87


def test_mixed_load_niska_no_trip_exceeds_capacity():
    plan = trips_for_mixed_load(NISKA)
    for i, trip in enumerate(plan):
        assert sum(trip.values()) <= 20, f"Trip {i} exceeds capacity: {trip}"


def test_mixed_load_niska_trip1_is_pure_salvage():
    # Salvage is largest (64 slots); first trip should fill entirely with Salvage.
    plan = trips_for_mixed_load(NISKA)
    assert plan[0] == {"Salvage": 20}


def test_mixed_load_niska_last_trip():
    # Trip 5: 5 slots Components + 2 slots Coal (Salvage exhausted after trip 4).
    plan = trips_for_mixed_load(NISKA)
    assert plan[-1] == {"Components": 5, "Coal": 2}


def test_mixed_load_fmc_trip_count():
    plan = trips_for_mixed_load(FMC)
    assert len(plan) == 14


def test_mixed_load_fmc_all_slots_accounted():
    plan = trips_for_mixed_load(FMC)
    total_loaded = sum(slots for trip in plan for slots in trip.values())
    # 226 + 54 = 280 slots
    assert total_loaded == 280


def test_mixed_load_empty():
    assert trips_for_mixed_load({}) == []


def test_mixed_load_single_item():
    # 250 Salvage -> 3 slots; 1 trip
    plan = trips_for_mixed_load({"Salvage": 250.0})
    assert len(plan) == 1
    assert plan[0] == {"Salvage": 3}


def test_mixed_load_unknown_vehicle():
    with pytest.raises(ValueError, match="Unknown vehicle"):
        trips_for_mixed_load({"Salvage": 100.0}, vehicle="Ghost Truck")
