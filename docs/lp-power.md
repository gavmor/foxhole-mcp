# LP Power Model

Power in the LP planner is a commodity called `"Power (MW·s)"` (the constant `POWER` in `model.py`).
It is produced by power-plant recipes and consumed by any recipe that draws electrical power from the
facility grid.  All functions live in `src/foxhole/lp/power.py`.

---

## Energy units

| Quantity | Unit | Description |
|---|---|---|
| Power draw | MW | Instantaneous watts while a recipe runs |
| Run time | s | Real seconds for one production cycle |
| Energy | **MW·s** | MW × s — the commodity traded in the LP |

A **MW·s** is a megawatt-second (one megawatt of power sustained for one second).  It equals
one megajoule, but the game uses MW·s throughout, so the planner does too.

---

## Consumed energy per run

```
consumed_per_run(row) = InputPower (MW)  ×  ProductionTime (s)
```

`InputPower` is the facility's grid draw in megawatts while the recipe is active.
A blank or missing field means the recipe is unpowered and returns 0.

**Hand-checked examples** (from `tests/lp/data/production_subset.json`):

| Facility | Recipe | InputPower | ProductionTime | MW·s |
|---|---|---|---|---|
| Materials Factory | Construction Materials rank 1 | 2 MW | 25 s | **50** |
| Coal Refinery | Coke rank 1 | 3 MW | 270 s | **810** |
| Oil Refinery | Heavy Oil rank 1 | 1.5 MW | 55 s | **82.5** |
| Oil Well | Oil rank 2 | 2 MW | 26 s | **52** |

---

## Produced energy per run

```
produced_per_run(row) = OutputAmount (MW)  ×  ProductionTime (s)
                        only when Output == "Facility Power", else 0
```

`OutputAmount` for power-plant rows is in MW (the wiki column `OutputUnit` contains `"MW"`).

**Hand-checked examples:**

| Facility | Fuel | Rank | OutputAmount | ProductionTime | MW·s |
|---|---|---|---|---|---|
| Diesel Power Plant | Diesel | 1 | 5 MW | 45 s | **225** |
| Diesel Power Plant | Coal | 2 | 5 MW | 90 s | **450** |
| Diesel Power Plant | Petrol | 3 | 12 MW | 90 s | **1 080** |
| Power Station | Coal | 4 | 10 MW | 90 s | **900** |
| Power Station | Coke | 6 | 16 MW | 120 s | **1 920** |
| Power Station | Heavy Oil | 7 | 16 MW | 120 s | **1 920** |

---

## plants_needed

```python
plants_needed(mw, plant_row) -> int
```

Returns `ceil(mw / output_mw)` — the minimum whole number of plants whose combined
capacity meets or exceeds the `mw` demand.

**Example:** 25 MW demand from Power Station rank 7 (16 MW each) → `ceil(25/16) = 2`.

---

## fuel_per_hour

```python
fuel_per_hour(mw, plant_row) -> dict[str, float]
```

Returns the quantity of each fuel item burned per real hour to supply `mw` MW
continuously.  Derivation:

```
energy_per_run  = produced_per_run(plant_row)          # MW·s
runs_per_hour   = (mw × 3 600) / energy_per_run
fuel_per_hour[item] = item_amount_per_run × runs_per_hour
```

**Example — Diesel Power Plant rank 1, 5 MW load:**

```
energy_per_run = 5 × 45 = 225 MW·s
runs_per_hour  = (5 × 3 600) / 225 = 80
Diesel/hr      = 25 × 80 = 2 000 L
```

**Example — Power Station rank 4 (Coal + Water), 10 MW load:**

```
energy_per_run = 10 × 90 = 900 MW·s
runs_per_hour  = (10 × 3 600) / 900 = 40
Coal/hr        = 30 × 40 = 1 200
Water/hr       =  1 × 40 =    40
```

---

## Energy model and assumptions

### Plants burn only for power drawn (Update 1.56)

Before Update 1.56, power plants ran at full output regardless of demand and any
surplus was wasted.  Since 1.56, **production scales proportionally to the
power actually consumed**: a plant producing 5 MW that only needs to supply 3 MW
runs at 60 % of its rated cycle rate and burns 60 % of its normal fuel.

Consequences for the LP model:

1. **`fuel_per_hour` is demand-linear** — it does not depend on plant count.
   Doubling demand exactly doubles fuel.  This makes fuel a proper LP commodity.

2. **`plants_needed` is a ceiling operation** — you need enough plants that their
   *capacity* meets demand; excess capacity is idle (zero fuel burn) when demand
   is lower.

3. **No idle-fuel cost** — a plant running below rated output does not burn fuel
   for the unused headroom.  Only the fraction actually delivered is charged.

### What the model does NOT capture

- **Start-up / shut-down transients** — the wiki describes steady-state behaviour;
  transient costs are negligible for LP planning horizons.
- **Facility placement and cable losses** — the planner treats the power grid as
  lossless.  Real grids can have cable distance limits, but those are not in the
  wiki Production table.
- **Mixed plant fleets** — `plants_needed` and `fuel_per_hour` operate on a single
  plant row.  A fleet mixing Diesel and Power Station plants must be handled by the
  calling LP layer (story-3 matrix builder), which can assign power as an LP
  commodity with multiple supply recipes.
