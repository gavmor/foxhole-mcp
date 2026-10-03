"""Shared fixtures."""

import pytest

from foxhole.cargo import reset_cargo_store
from foxhole.economy import reset_economy_solver


@pytest.fixture(autouse=True)
def isolated_cargo_cache(tmp_path, monkeypatch):
    """Point the cargo cache at an empty dir so tests never depend on a local sync."""
    monkeypatch.setenv("FOXHOLE_CARGO_DIR", str(tmp_path / "cargo"))
    reset_economy_solver()
    reset_cargo_store()
    yield
    reset_economy_solver()
    reset_cargo_store()
