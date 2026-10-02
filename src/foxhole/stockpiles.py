"""Read stockpile inventories exported by xurxogr/foxhole-stockpiles.

foxhole-stockpiles (https://github.com/xurxogr/foxhole-stockpiles, MIT) extracts in-game
stockpiles by OCR, clipboard, or `.sav` file and writes them as JSON, CSV or TSV. Items are
keyed by the game's internal CodeName (e.g. `MGAmmo`, `Cloth`), which matches the `codename`
field of the wiki Cargo tables, so contents resolve straight onto wiki names and recipes.

Supported inputs:
- JSON: `{"stockpiles": [...]}` (the app's file/webhook payload), a bare list, or one stockpile
- CSV/TSV: the app's export, with or without its header row
- `.sav`: Foxhole save files (pinned stockpiles), via the optional `fs-sav` parser
  (`uv sync --extra stockpiles`)
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from foxhole.cargo import ITEM_TABLE, STRUCTURE_TABLE, VEHICLE_TABLE, CargoStore, get_cargo_store

# Column order of the foxhole-stockpiles CSV/TSV export (core/settings/.../csv_format.py)
CSV_FIELDS = [
    "stockpile_name",
    "stockpile_type",
    "code",
    "crated",
    "quantity",
    "confidence",
    "shard",
    "ingame_timestamp",
]
CSV_HEADERS = [
    "Stockpile Name",
    "Stockpile Type",
    "Code",
    "Crated",
    "Quantity",
    "Confidence",
    "Shard",
    "Ingame Time",
]

# CodeNames whose wiki row carries no codename
CODE_ALIASES = {"RareMaterials": "Rare Materials"}


class StockpileEntry(BaseModel):
    """One line of a stockpile, resolved to a wiki name and counted in single units."""

    code: str = Field(description="Game CodeName as exported (e.g. 'MGAmmo')")
    name: str | None = Field(default=None, description="Wiki display name, if resolved")
    kind: str | None = Field(default=None, description="item, vehicle or structure")
    quantity: int = Field(description="Quantity as exported (crates if crated)")
    crated: bool = Field(default=False, description="Whether the quantity counts crates")
    crate_size: int | None = Field(default=None, description="Units per crate, if known")
    units: float | None = Field(
        default=None, description="Single units (quantity * crate size when crated)"
    )
    confidence: float | None = Field(default=None, description="OCR match confidence (0-1)")


class StockpileSnapshot(BaseModel):
    """A stockpile's contents in wiki terms."""

    name: str = ""
    type: str | None = None
    hex: str | None = None
    is_reserve: bool = False
    faction: str | None = None
    timestamp: str | None = None
    entries: list[StockpileEntry] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _raw_from_json(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict) and "stockpiles" in data:
        data = data["stockpiles"]
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ValueError("JSON is not a foxhole-stockpiles export")
    return [s for s in data if isinstance(s, dict)]


def _raw_from_csv(text: str, delimiter: str) -> list[dict[str, Any]]:
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    if rows and [c.strip() for c in rows[0]] == CSV_HEADERS:
        rows = rows[1:]
    piles: dict[tuple[str, str], dict[str, Any]] = {}
    for line_no, row in enumerate(rows, 1):
        if not any(cell.strip() for cell in row):
            continue
        if len(row) != len(CSV_FIELDS):
            raise ValueError(f"line {line_no}: expected {len(CSV_FIELDS)} columns, got {len(row)}")
        rec = dict(zip(CSV_FIELDS, row, strict=True))
        pile = piles.setdefault(
            (rec["stockpile_name"], rec["stockpile_type"]),
            {"name": rec["stockpile_name"], "type": rec["stockpile_type"] or None, "items": []},
        )
        pile["items"].append(
            {
                "code": rec["code"],
                "quantity": int(rec["quantity"]),
                "crated": rec["crated"].strip() in ("1", "true", "True"),
                "confidence": float(rec["confidence"]) if rec["confidence"] else None,
            }
        )
    return list(piles.values())


def _raw_from_sav(path: Path) -> list[dict[str, Any]]:
    try:
        import fs_sav  # type: ignore[import-not-found]
    except ImportError as e:
        raise ValueError(
            "Reading .sav files needs the optional fs-sav parser: uv sync --extra stockpiles"
        ) from e
    try:
        raw = fs_sav.parse_save(str(path), with_items=True)
    except RuntimeError as e:  # fs-sav reports corrupt/unsupported files this way
        raise ValueError(f"Could not read save file {path.name}: {e}") from e
    if not isinstance(raw, list):
        raise ValueError(f"fs_sav.parse_save returned {type(raw).__name__}, expected list")
    return raw


def load_raw_stockpiles(path: str | Path) -> list[dict[str, Any]]:
    """Read a foxhole-stockpiles export or Foxhole .sav file into raw stockpile dicts."""
    path = Path(path).expanduser()
    if not path.exists():
        raise ValueError(f"No such file: {path}")
    suffix = path.suffix.lower()
    if suffix == ".sav":
        return _raw_from_sav(path)
    text = path.read_text(encoding="utf-8-sig")
    if suffix == ".json" or text.lstrip().startswith(("{", "[")):
        return _raw_from_json(json.loads(text))
    if suffix == ".tsv" or (suffix != ".csv" and "\t" in text.splitlines()[0]):
        return _raw_from_csv(text, "\t")
    return _raw_from_csv(text, ",")


def _resolve(store: CargoStore | None, code: str) -> tuple[str | None, str | None, int | None]:
    """Map a CodeName to (wiki name, kind, crate size) via the synced Cargo tables."""
    if store is None:
        return None, None, None
    lookup = CODE_ALIASES.get(code, code)
    for table, kind in (
        (ITEM_TABLE, "item"),
        (VEHICLE_TABLE, "vehicle"),
        (STRUCTURE_TABLE, "structure"),
    ):
        row = store.find(table, lookup)
        if row is None:
            continue
        name = row.get("name", lookup)
        size = row.get("crate_amount")
        crate = int(float(size)) if size else store.crate_capacity(name)
        return name, kind, crate
    return None, None, None


def snapshot(raw: dict[str, Any], store: CargoStore | None) -> StockpileSnapshot:
    snap = StockpileSnapshot(
        name=raw.get("name") or "",
        type=raw.get("type") or None,
        hex=raw.get("hex"),
        is_reserve=bool(raw.get("is_reserve", False)),
        faction=raw.get("faction"),
        timestamp=raw.get("timestamp"),
    )
    if store is None:
        snap.warnings.append("Wiki data not synced (run `foxhole cargo-sync`); names unresolved")
    for item in raw.get("items", []):
        code = str(item.get("code", "Unknown"))
        quantity = int(item.get("quantity", 0))
        crated = bool(item.get("crated", False))
        name, kind, crate = _resolve(store, code)
        units: float | None = float(quantity)
        if quantity < 0:
            units = None
            snap.warnings.append(f"{code}: quantity unreadable (OCR); skipped")
        elif crated:
            units = float(quantity * crate) if crate else None
            if crate is None:
                snap.warnings.append(f"{code}: crated but crate size unknown; skipped")
        if name is None and store is not None:
            snap.warnings.append(f"{code}: no wiki entry for this CodeName")
        snap.entries.append(
            StockpileEntry(
                code=code,
                name=name,
                kind=kind,
                quantity=quantity,
                crated=crated,
                crate_size=crate,
                units=units,
                confidence=item.get("confidence"),
            )
        )
    return snap


def read_stockpiles(
    path: str | Path,
    names: list[str] | None = None,
    hex_name: str | None = None,
    include_reserves: bool = True,
) -> list[StockpileSnapshot]:
    """Load, filter and resolve stockpiles from a file."""
    store = get_cargo_store()
    wanted = {n.lower() for n in names} if names else None
    out = []
    for raw in load_raw_stockpiles(path):
        if wanted is not None and (raw.get("name") or "").lower() not in wanted:
            continue
        if hex_name and (raw.get("hex") or "").lower() != hex_name.lower():
            continue
        if not include_reserves and raw.get("is_reserve"):
            continue
        out.append(snapshot(raw, store))
    return out


def inventory(snapshots: list[StockpileSnapshot]) -> dict[str, float]:
    """Total single units per wiki name across stockpiles (unresolved/unreadable lines skipped)."""
    totals: dict[str, float] = {}
    for snap in snapshots:
        for entry in snap.entries:
            if entry.name and entry.units:
                totals[entry.name] = totals.get(entry.name, 0.0) + entry.units
    return totals
