"""Evals-specific pytest fixtures."""

import os
from pathlib import Path

import pytest

from foxhole.cargo import reset_cargo_store
from foxhole.economy import reset_economy_solver


@pytest.fixture(autouse=True)
def isolated_cargo_cache():
    """Ensure eval suite uses real synced Cargo store."""
    cargo_dir = os.environ.get("FOXHOLE_CARGO_DIR") or str(
        Path.home() / ".cache" / "foxhole" / "cargo"
    )
    os.environ["FOXHOLE_CARGO_DIR"] = cargo_dir
    reset_economy_solver()
    reset_cargo_store()
    yield
    reset_economy_solver()
    reset_cargo_store()
