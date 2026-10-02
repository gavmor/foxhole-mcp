"""Read stockpile inventories exported by xurxogr/foxhole-stockpiles.

foxhole-stockpiles (https://github.com/xurxogr/foxhole-stockpiles, MIT) extracts in-game
stockpiles by OCR, clipboard, or `.sav` file and writes them as JSON, CSV or TSV. Items are
keyed by the game's internal CodeName (e.g. `MGAmmo`, `Cloth`), which matches the `codename`
field of the wiki Cargo tables, so contents resolve straight onto wiki names and recipes.

Supported inputs:
- JSON: `{"stockpiles": [...]}` (the app's file/webhook payload), a bare list, or one stockpile
- CSV/TSV: the app's export, with or without its header row
- `.sav`: Foxhole save files (pinned stockpiles), via the optional `fs-sav` parser
  (`uv sync --extra stockpiles`). The save is found automatically (Steam/Proton on Linux,
  Flatpak Steam, extra Steam libraries, Windows), read once into memory so a write in
  progress is never parsed, and compared with the previous read to report changes.

The game records a stockpile's contents in the save only once it is pinned and has been
opened in game.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from foxhole.cargo import (
    ITEM_TABLE,
    STRUCTURE_TABLE,
    VEHICLE_TABLE,
    CargoStore,
    default_cache_dir,
    get_cargo_store,
)

FOXHOLE_APP_ID = "505460"
PROTON_SAVE_DIR = (
    f"steamapps/compatdata/{FOXHOLE_APP_ID}/pfx/drive_c/users/steamuser"
    "/AppData/Local/Foxhole/Saved/SaveGames"
)
STEAM_ROOTS = (
    "~/.steam/steam",
    "~/.steam/root",
    "~/.steam/debian-installation",
    "~/.local/share/Steam",
    "~/.var/app/com.valvesoftware.Steam/.local/share/Steam",
)

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


class SaveFile(BaseModel):
    """A Foxhole map save holding pinned stockpiles."""

    path: str
    modified_ingame: str | None = Field(default=None, description='e.g. "Day 27, 0627 Hours"')
    modified: str = Field(description="Last write time (local, ISO 8601)")
    age_seconds: float = Field(description="Seconds since the game last wrote it")
    size_bytes: int


class StockpileSnapshot(BaseModel):
    """A stockpile's contents in wiki terms."""

    key: str = Field(default="", description="Stable identity: type:hex:coords:name")
    name: str = ""
    type: str | None = None
    hex: str | None = None
    coords: dict[str, float] | None = None
    is_reserve: bool = False
    faction: str | None = None
    timestamp_ingame: str | None = Field(default=None, description="When the save last saw it")
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


def _ingame(t: float) -> str | None:
    from foxhole.ingame_time import ingame_label

    return ingame_label(t)


def _ingame_iso(stamp: Any) -> str | None:
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        dt = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:  # foxhole-stockpiles exports local naive times
        dt = dt.astimezone()
    return _ingame(dt.timestamp())


def _steam_libraries() -> list[Path]:
    """Steam roots plus any extra libraries listed in their libraryfolders.vdf."""
    libs: list[Path] = []
    for root in (Path(r).expanduser() for r in STEAM_ROOTS):
        if not root.exists():
            continue
        libs.append(root)
        vdf = root / "steamapps" / "libraryfolders.vdf"
        if vdf.exists():
            libs += [
                Path(m) for m in re.findall(r'"path"\s+"([^"]+)"', vdf.read_text(errors="ignore"))
            ]
    return libs


def find_save_files() -> list[SaveFile]:
    """Foxhole map saves on this machine, newest first.

    `FOXHOLE_SAVE_PATH` (a file or a folder) overrides discovery.
    """
    candidates: list[Path] = []
    if override := os.getenv("FOXHOLE_SAVE_PATH"):
        target = Path(override).expanduser()
        candidates += [target] if target.is_file() else list(target.glob("*MapData.sav"))
    else:
        dirs = [lib / PROTON_SAVE_DIR for lib in _steam_libraries()]
        if local := os.getenv("LOCALAPPDATA"):  # Windows
            dirs.append(Path(local) / "Foxhole" / "Saved" / "SaveGames")
        for d in dirs:
            if d.is_dir():
                candidates += d.glob("*MapData.sav")
    seen: dict[Path, Path] = {}
    for c in candidates:
        seen.setdefault(c.resolve(), c)
    now = time.time()
    saves = []
    for real in seen:
        st = real.stat()
        saves.append(
            SaveFile(
                path=str(real),
                modified_ingame=_ingame(st.st_mtime),
                modified=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_mtime)),
                age_seconds=round(now - st.st_mtime, 1),
                size_bytes=st.st_size,
            )
        )
    return sorted(saves, key=lambda s: s.age_seconds)


def default_save_path() -> Path:
    saves = find_save_files()
    if not saves:
        raise ValueError(
            "No Foxhole MapData.sav found. Pin a stockpile in game, or set FOXHOLE_SAVE_PATH."
        )
    return Path(saves[0].path)


def _read_stable(path: Path, attempts: int = 5) -> bytes:
    """Read the whole file, retrying if the game rewrote it mid-read."""
    for _ in range(attempts):
        before = path.stat()
        data = path.read_bytes()
        after = path.stat()
        unchanged = (before.st_mtime_ns, before.st_size) == (after.st_mtime_ns, after.st_size)
        if unchanged and len(data) == after.st_size:
            return data
        time.sleep(0.2)
    raise ValueError(f"{path.name} kept changing while being read; try again shortly")


def _raw_from_sav(path: Path) -> list[dict[str, Any]]:
    try:
        import fs_sav  # type: ignore[import-not-found]
    except ImportError as e:
        raise ValueError(
            "Reading .sav files needs the optional fs-sav parser: uv sync --extra stockpiles"
        ) from e
    data = _read_stable(path)
    try:
        # No with_items: that flag *filters out* empty stockpiles, which hides pinned ones
        raw = fs_sav.parse_save_bytes(data)
    except RuntimeError as e:  # fs-sav reports corrupt/unsupported files this way
        raise ValueError(f"Could not read save file {path.name}: {e}") from e
    if not isinstance(raw, list):
        raise ValueError(f"fs_sav.parse_save returned {type(raw).__name__}, expected list")
    return raw


def load_raw_stockpiles(path: str | Path | None = None) -> list[dict[str, Any]]:
    """Read a foxhole-stockpiles export or Foxhole .sav file into raw stockpile dicts.

    With no path, reads the newest Foxhole save found on this machine.
    """
    path = default_save_path() if path is None else Path(path).expanduser()
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


def stockpile_key(raw: dict[str, Any]) -> str:
    """Identity matching foxhole-stockpiles' Stockpile.to_key(): type:hex:coords:name."""
    c = raw.get("coords") or {}
    coords = f"{c.get('x', 0):.4f},{c.get('y', 0):.4f}" if c else "0,0"
    return f"{raw.get('type')}:{raw.get('hex')}:{coords}:{raw.get('name') or ''}"


def snapshot(raw: dict[str, Any], store: CargoStore | None) -> StockpileSnapshot:
    snap = StockpileSnapshot(
        key=stockpile_key(raw),
        name=raw.get("name") or "",
        type=raw.get("type") or None,
        hex=raw.get("hex"),
        coords=raw.get("coords"),
        is_reserve=bool(raw.get("is_reserve", False)),
        faction=raw.get("faction"),
        timestamp_ingame=_ingame_iso(raw.get("timestamp")),
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
    path: str | Path | None = None,
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


# ---------------------------------------------------------------------------
# Change tracking between reads (the MCP stand-in for `fs-sav watch --diff`)
# ---------------------------------------------------------------------------


class StockpileChange(BaseModel):
    key: str
    type: str | None = None
    hex: str | None = None
    name: str = ""
    status: str = Field(description="added, removed or changed")
    deltas: dict[str, float] = Field(
        default_factory=dict, description="Units gained (+) or lost (-) per wiki name or code"
    )


def _state_path() -> Path:
    return default_cache_dir().parent / "stockpiles" / "last_seen.json"


def _contents(snap: StockpileSnapshot) -> dict[str, float]:
    out: dict[str, float] = {}
    for e in snap.entries:
        label = e.name or e.code
        out[label] = out.get(label, 0.0) + (e.units if e.units is not None else e.quantity)
    return out


def _record(snap: StockpileSnapshot) -> dict[str, Any]:
    return {"type": snap.type, "hex": snap.hex, "name": snap.name, "items": _contents(snap)}


def _change(
    key: str, rec: dict[str, Any], status: str, deltas: dict[str, float]
) -> StockpileChange:
    return StockpileChange(
        key=key, type=rec["type"], hex=rec["hex"], name=rec["name"], status=status, deltas=deltas
    )


def diff_since_last(snaps: list[StockpileSnapshot], source: str) -> dict[str, Any]:
    """Compare snapshots with the previous read of the same source, then remember them."""
    path = _state_path()
    state: dict[str, Any] = json.loads(path.read_text()) if path.exists() else {}
    prev: dict[str, Any] | None = state.get(source)
    current: dict[str, dict[str, Any]] = {s.key: _record(s) for s in snaps}

    changes: list[StockpileChange] = []
    if prev is not None:
        before: dict[str, dict[str, Any]] = prev["stockpiles"]
        for key in sorted(before.keys() | current.keys()):
            old, new = before.get(key), current.get(key)
            if old is None and new is not None:
                changes.append(_change(key, new, "added", dict(new["items"])))
            elif new is None and old is not None:
                changes.append(
                    _change(key, old, "removed", {k: -v for k, v in old["items"].items()})
                )
            elif old is not None and new is not None:
                o: dict[str, float] = old["items"]
                n: dict[str, float] = new["items"]
                deltas = {
                    k: round(n.get(k, 0.0) - o.get(k, 0.0), 2) for k in sorted(o.keys() | n.keys())
                }
                deltas = {k: d for k, d in deltas.items() if d}
                if deltas:
                    changes.append(_change(key, new, "changed", deltas))

    state[source] = {"read_at": time.time(), "stockpiles": current}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state))
    return {
        "baseline": prev is None,
        "previous_read_ingame": _ingame(prev["read_at"]) if prev else None,
        "previous_read_at": (
            time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(prev["read_at"])) if prev else None
        ),
        "changes": [c.model_dump(exclude_none=True) for c in changes],
    }


# ---------------------------------------------------------------------------
# Desired vs available: quota diffs
# ---------------------------------------------------------------------------


class QuotaLine(BaseModel):
    name: str = Field(description="Wiki name")
    requested_as: str = Field(description="Name as given in the quota")
    resolved_by: str = Field(description="exact, alias/codename, or fuzzy (check these)")
    desired: float
    available: float
    delta: float = Field(description="available - desired (negative = short)")
    status: str = Field(description="short, met or surplus")
    crate_size: int | None = None
    crates_short: int | None = Field(
        default=None, description="Whole crates to make up a shortfall"
    )
    crates_spare: int | None = Field(default=None, description="Whole crates beyond the quota")


def load_quota(desired: dict[str, float] | str | Path) -> dict[str, float]:
    """A quota as a dict, or a JSON file holding {name: qty} or {"desired": {name: qty}}."""
    if isinstance(desired, dict):
        return {str(k): float(v) for k, v in desired.items()}
    path = Path(desired).expanduser()
    if not path.exists():
        raise ValueError(f"No such quota file: {path}")
    data = json.loads(path.read_text())
    data = data.get("desired", data) if isinstance(data, dict) else data
    if not isinstance(data, dict):
        raise ValueError('Quota file must hold {name: quantity} or {"desired": {...}}')
    return {str(k): float(v) for k, v in data.items()}


def _resolve_name(store: CargoStore | None, name: str) -> tuple[str | None, str]:
    """Wiki name for a quota entry: exact name, then alias/codename, then solver fuzzy match."""
    if store is not None:
        for table in (ITEM_TABLE, VEHICLE_TABLE, STRUCTURE_TABLE):
            row = store.find(table, name)
            if row is not None:
                how = (
                    "exact"
                    if row.get("name", "").lower() == name.strip().lower()
                    else "alias/codename"
                )
                return row.get("name", name), how
    try:
        from foxhole.economy import get_economy_solver

        return get_economy_solver().resolve_item_name(name), "fuzzy"
    except ValueError:
        return None, "unresolved"


def quota_diff(
    desired: dict[str, float],
    snaps: list[StockpileSnapshot],
    crates: bool = False,
    include_unlisted: bool = False,
) -> dict[str, Any]:
    """Compare desired stock levels with what the stockpiles hold.

    `crates=True` reads the desired quantities as crates and reports in crates; otherwise
    everything is single units.
    """
    store = get_cargo_store()
    have = inventory(snaps)
    lines: list[QuotaLine] = []
    unresolved: list[str] = []
    wanted: dict[str, float] = {}
    for requested, qty in desired.items():
        name, how = _resolve_name(store, requested)
        if name is None:
            unresolved.append(requested)
            continue
        size = _resolve(store, name)[2] if store else None
        if crates and not size:
            unresolved.append(f"{requested} (no crate size known)")
            continue
        units = qty * size if crates and size else qty
        wanted[name] = wanted.get(name, 0.0) + units
        lines.append(
            QuotaLine(
                name=name,
                requested_as=requested,
                resolved_by=how,
                desired=units,
                available=0,
                delta=0,
                status="",
                crate_size=size,
            )
        )

    out_lines: dict[str, QuotaLine] = {}
    for line in lines:  # merge duplicates that resolved to the same item
        if line.name in out_lines:
            continue
        units_wanted = wanted[line.name]
        units_have = have.get(line.name, 0.0)
        delta = units_have - units_wanted
        scale = line.crate_size if crates and line.crate_size else 1
        line.desired = round(units_wanted / scale, 2)
        line.available = round(units_have / scale, 2)
        line.delta = round(delta / scale, 2)
        line.status = "short" if delta < -1e-9 else "surplus" if delta > 1e-9 else "met"
        if line.crate_size and line.crate_size > 1:  # liquids report a crate size of 1
            if delta < 0:
                line.crates_short = math.ceil(-delta / line.crate_size - 1e-9)
            elif delta > 0:
                line.crates_spare = math.floor(delta / line.crate_size + 1e-9)
        out_lines[line.name] = line

    result: dict[str, Any] = {
        "unit": "crates" if crates else "units",
        "items": [x.model_dump(exclude_none=True) for x in out_lines.values()],
        "shortfall": {x.name: -x.delta for x in out_lines.values() if x.status == "short"},
        "surplus": {x.name: x.delta for x in out_lines.values() if x.status == "surplus"},
        "counts": {
            s: sum(1 for x in out_lines.values() if x.status == s)
            for s in ("short", "met", "surplus")
        },
    }
    if crates:  # the solver works in units
        result["shortfall_units"] = {
            x.name: round(-x.delta * (x.crate_size or 1), 2)
            for x in out_lines.values()
            if x.status == "short"
        }
    if include_unlisted:
        result["unlisted"] = {k: round(v, 2) for k, v in sorted(have.items()) if k not in wanted}
    if unresolved:
        result["unresolved"] = unresolved
    return result
