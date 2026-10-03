"""Tests for story-4: power as a commodity (MW·s)."""

from __future__ import annotations

import pytest

from foxhole.lp.power import (
    POWER_OUTPUT,
    consumed_per_run,
    fuel_per_hour,
    plants_needed,
    produced_per_run,
)

# -- helpers ------------------------------------------------------------------


def _row(production_rows: list[dict[str, str]], source: str, rank: int) -> dict[str, str]:
    for r in production_rows:
        if r["Source"] == source and r["RecipeRank"] == str(rank):
            return r
    raise KeyError(f"No fixture row: source={source!r} rank={rank}")


def _row_by_page(
    production_rows: list[dict[str, str]], page: str, source: str, rank: int
) -> dict[str, str]:
    for r in production_rows:
        if r["page"] == page and r["Source"] == source and r["RecipeRank"] == str(rank):
            return r
    raise KeyError(f"No fixture row: page={page!r} source={source!r} rank={rank}")


def _power_row(production_rows: list[dict[str, str]], source: str, rank: int) -> dict[str, str]:
    row = _row(production_rows, source, rank)
    assert row["Output"] == POWER_OUTPUT, f"Not a power row: {row['Output']!r}"
    return row


# -- consumed_per_run ---------------------------------------------------------


class TestConsumedPerRun:
    def test_construction_materials_mat_factory_rank1(self, production_rows):
        # Materials Factory, Construction Materials, rank 1: 2 MW x 25 s = 50 MW*s
        row = _row_by_page(production_rows, "Construction Materials", "Materials Factory", 1)
        assert row["Output"] == "Construction Materials"
        assert row["InputPower"] == "2"
        assert row["ProductionTime"] == "25"
        assert consumed_per_run(row) == pytest.approx(50.0)

    def test_coke_coal_refinery_rank1(self, production_rows):
        # Coal Refinery, Coke, rank 1: 3 MW x 270 s = 810 MW*s
        row = _row(production_rows, "Coal Refinery", 1)
        assert consumed_per_run(row) == pytest.approx(810.0)

    def test_oil_well_rank2(self, production_rows):
        # Oil Well, rank 2: 2 MW x 26 s = 52 MW*s
        row = _row(production_rows, "Oil Well", 2)
        assert consumed_per_run(row) == pytest.approx(52.0)

    def test_oil_refinery_heavy_oil_rank1(self, production_rows):
        # Oil Refinery, Heavy Oil, rank 1: 1.5 MW x 55 s = 82.5 MW*s
        row = _row(production_rows, "Oil Refinery", 1)
        assert consumed_per_run(row) == pytest.approx(82.5)

    def test_unpowered_factory_row_returns_zero(self, production_rows):
        # Factory 7.92mm rank 1 has blank InputPower
        row = _row(production_rows, "Factory", 1)
        assert row["InputPower"] == ""
        assert consumed_per_run(row) == pytest.approx(0.0)

    def test_blank_production_time_returns_zero(self):
        # Hammer-type rows have no ProductionTime
        row = {"InputPower": "5", "ProductionTime": "", "Output": "Widget", "Source": "Garage"}
        assert consumed_per_run(row) == pytest.approx(0.0)

    def test_blank_input_power_returns_zero(self):
        row = {"InputPower": "", "ProductionTime": "60", "Output": "Gravel"}
        assert consumed_per_run(row) == pytest.approx(0.0)

    def test_missing_fields_returns_zero(self):
        assert consumed_per_run({}) == pytest.approx(0.0)


# -- produced_per_run ---------------------------------------------------------


class TestProducedPerRun:
    def test_diesel_power_plant_rank1_diesel(self, production_rows):
        # Diesel Power Plant rank 1: 5 MW x 45 s = 225 MW*s
        row = _power_row(production_rows, "Diesel Power Plant", 1)
        assert row["InputItem1"] == "Diesel"
        assert row["OutputAmount"] == "5"
        assert row["ProductionTime"] == "45"
        assert produced_per_run(row) == pytest.approx(225.0)

    def test_diesel_power_plant_rank2_coal(self, production_rows):
        # Diesel Power Plant rank 2 (Coal): 5 MW x 90 s = 450 MW*s
        row = _power_row(production_rows, "Diesel Power Plant", 2)
        assert row["InputItem1"] == "Coal"
        assert produced_per_run(row) == pytest.approx(450.0)

    def test_diesel_power_plant_rank3_petrol(self, production_rows):
        # Diesel Power Plant rank 3 (Petrol): 12 MW x 90 s = 1080 MW*s
        row = _power_row(production_rows, "Diesel Power Plant", 3)
        assert row["InputItem1"] == "Petrol"
        assert produced_per_run(row) == pytest.approx(1080.0)

    def test_power_station_rank7_heavy_oil(self, production_rows):
        # Power Station rank 7 (Heavy Oil): 16 MW x 120 s = 1920 MW*s
        row = _power_row(production_rows, "Power Station", 7)
        assert row["InputItem1"] == "Heavy Oil"
        assert row["ProductionTime"] == "120"
        assert produced_per_run(row) == pytest.approx(1920.0)

    def test_power_station_rank6_coke(self, production_rows):
        # Power Station rank 6 (Coke): 16 MW x 120 s = 1920 MW*s
        row = _power_row(production_rows, "Power Station", 6)
        assert row["InputItem1"] == "Coke"
        assert produced_per_run(row) == pytest.approx(1920.0)

    def test_power_station_rank4_coal(self, production_rows):
        # Power Station rank 4 (Coal): 10 MW x 90 s = 900 MW*s
        row = _power_row(production_rows, "Power Station", 4)
        assert row["InputItem1"] == "Coal"
        assert produced_per_run(row) == pytest.approx(900.0)

    def test_non_power_plant_factory_returns_zero(self, production_rows):
        row = _row(production_rows, "Factory", 1)
        assert produced_per_run(row) == pytest.approx(0.0)

    def test_non_power_plant_refinery_returns_zero(self, production_rows):
        row = _row(production_rows, "Refinery", 1)
        assert produced_per_run(row) == pytest.approx(0.0)

    def test_non_power_plant_extraction_returns_zero(self, production_rows):
        row = _row(production_rows, "Oil Well", 1)
        assert produced_per_run(row) == pytest.approx(0.0)


# -- plants_needed ------------------------------------------------------------


class TestPlantsNeeded:
    def test_exact_multiple(self, production_rows):
        # 10 MW demand, 5 MW per plant -> exactly 2
        row = _power_row(production_rows, "Diesel Power Plant", 1)
        assert plants_needed(10.0, row) == 2

    def test_fractional_rounds_up(self, production_rows):
        # 6 MW demand, 5 MW per plant -> ceil(6/5) = 2
        row = _power_row(production_rows, "Diesel Power Plant", 1)
        assert plants_needed(6.0, row) == 2

    def test_single_plant_sufficient(self, production_rows):
        # 1 MW demand, 5 MW per plant -> 1
        row = _power_row(production_rows, "Diesel Power Plant", 1)
        assert plants_needed(1.0, row) == 1

    def test_power_station_16mw_over_demand(self, production_rows):
        # 25 MW demand, 16 MW per plant -> ceil(25/16) = 2
        row = _power_row(production_rows, "Power Station", 7)
        assert plants_needed(25.0, row) == 2

    def test_power_station_16mw_exact(self, production_rows):
        # 16 MW demand, 16 MW per plant -> 1
        row = _power_row(production_rows, "Power Station", 7)
        assert plants_needed(16.0, row) == 1

    def test_raises_for_non_power_row(self, production_rows):
        row = _row(production_rows, "Factory", 1)
        with pytest.raises(ValueError, match="Not a power-plant row"):
            plants_needed(5.0, row)


# -- fuel_per_hour ------------------------------------------------------------


class TestFuelPerHour:
    def test_diesel_power_plant_rank1_at_full_load(self, production_rows):
        # 5 MW from Diesel Power Plant rank 1 (Diesel, 25 L, 225 MW*s/run)
        # runs/hr = 5*3600/225 = 80; diesel/hr = 25*80 = 2000 L
        row = _power_row(production_rows, "Diesel Power Plant", 1)
        assert fuel_per_hour(5.0, row) == pytest.approx({"Diesel": 2000.0})

    def test_diesel_power_plant_rank1_half_load(self, production_rows):
        # 2.5 MW -> fuel scales linearly: 1000 L Diesel/hr
        row = _power_row(production_rows, "Diesel Power Plant", 1)
        assert fuel_per_hour(2.5, row) == pytest.approx({"Diesel": 1000.0})

    def test_power_station_rank7_heavy_oil_full_load(self, production_rows):
        # 16 MW from Power Station rank 7 (Heavy Oil, 50 L, 1920 MW*s/run)
        # runs/hr = 16*3600/1920 = 30; heavy_oil/hr = 50*30 = 1500 L
        row = _power_row(production_rows, "Power Station", 7)
        assert fuel_per_hour(16.0, row) == pytest.approx({"Heavy Oil": 1500.0})

    def test_power_station_rank4_multi_fuel(self, production_rows):
        # 10 MW from Power Station rank 4 (Coal 30, Water 1, 900 MW*s/run)
        # runs/hr = 10*3600/900 = 40; coal = 30*40 = 1200; water = 1*40 = 40
        row = _power_row(production_rows, "Power Station", 4)
        assert row["InputItem1"] == "Coal"
        assert row["InputItem2"] == "Water"
        assert fuel_per_hour(10.0, row) == pytest.approx({"Coal": 1200.0, "Water": 40.0})

    def test_fuel_scales_linearly_with_demand(self, production_rows):
        # Doubling demand doubles fuel consumption
        row = _power_row(production_rows, "Power Station", 7)
        single = fuel_per_hour(8.0, row)
        double = fuel_per_hour(16.0, row)
        assert double["Heavy Oil"] == pytest.approx(2 * single["Heavy Oil"])

    def test_raises_for_non_power_row(self, production_rows):
        row = _row(production_rows, "Factory", 1)
        with pytest.raises(ValueError, match="Not a power-plant row"):
            fuel_per_hour(5.0, row)
