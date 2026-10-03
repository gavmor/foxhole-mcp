# LP Production Planner — `optimize_production`

The `optimize_production` MCP tool plans facility production for the game Foxhole using linear programming. Unlike the simpler Leontief solver (`plan_production`), this tool works across **all wiki recipes simultaneously**, handles byproducts and alternative recipes, enforces resource caps, and can solve for integer (whole-crate) quantities.

---

## Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `targets` | `dict[str, float]` | required | Items to produce and their quantities, e.g. `{"Basic Materials": 500}`. Names are fuzzy-matched (aliases like `bmats` work). |
| `objective` | `str` | `"min_raw"` | What to optimise. See [Objectives](#objectives). |
| `mode` | `str` | `"quantity"` | `"quantity"` = produce totals; `"rate"` = plan per real hour. |
| `max_raw` | `dict[str, float]` | none | Cap on how much of each raw resource may be consumed, e.g. `{"Salvage": 10000}`. |
| `banned_items` | `list[str]` | none | Items that may never be produced or consumed (e.g. `["Coal"]` to avoid Coal chains). |
| `deny_recipes` | `list[str]` | none | Recipe IDs to exclude, e.g. `["Basic Materials#2@Refinery"]`. |
| `integer` | `bool` | `false` | If `true`, recipe run counts are integers (MILP). Slower but gives whole-crate plans. |
| `time_window_seconds` | `float` | `3600` | Planning horizon in seconds. Used to calculate facility counts. |
| `max_facilities` | `dict[str, int]` | none | Machine caps per source when `integer=true`, e.g. `{"Refinery": 4}`. |
| `use_stockpile` | `bool` | `false` | If `true`, read the local stockpile file and net existing inventory against demand. |
| `hex_name` | `str` | none | Filter stockpile to this hex (requires `use_stockpile=true`). |
| `max_trips` | `float` | none | Cap total raw-resource haul to this many truck trips. |

### Objectives

- `min_raw` — minimise total raw resource intake (weighted by `Limits.raw_weights`, default 1 per unit). Best for minimising mining.
- `min_mining_time` — minimise hand-mining seconds (uses wiki timing: 1.1 s / 5 Salvage, 1.6 s / 2 Components, etc.). Best when you plan to hand-mine.
- `max_throughput` — maximise production rate given fixed resource caps (requires `mode="rate"` and at least one `max_raw` cap to bound the problem).

---

## Return value

The tool returns a JSON object with all `LPResult` fields plus:

- `summary` — human-readable one-liner summarising status, objective value, and key resources.
- `recipes_chosen` — list of `{recipe_id, runs, reason}` entries for every recipe that ran, noting any alternatives that existed but were not selected.
- `trips_needed` — (only when `max_trips` is set) integer count of truck trips actually needed.

---

## Worked Examples

### 1. Minimise salvage to produce 500 Basic Materials

```json
{
  "targets": {"bmats": 500},
  "objective": "min_raw"
}
```

The solver finds the cheapest path from Salvage through the Refinery. Returns `raw_used: {"Salvage": 1000}` (2:1 ratio), one recipe run, and a summary confirming the optimal objective value.

---

### 2. Plan 40mm shells for an assault — rate mode, cap components

A frontline push needs 40mm shells as fast as possible for one hour, but the Components supply is limited.

```json
{
  "targets": {"40mm": 200},
  "mode": "rate",
  "objective": "max_throughput",
  "max_raw": {"Salvage": 5000, "Components": 300}
}
```

The solver maximises how many 200-shell bundles fit within the caps. `throughput` in the result shows how many full bundles per hour are achievable, and `facilities` shows the Refinery / Factory counts needed.

---

### 3. Whole-crate plan for a vehicle push (MILP)

Produce exactly whole crates of Assembly Materials I for a tank push, using at most 2 Materials Factories.

```json
{
  "targets": {"Assembly Materials I": 10},
  "integer": true,
  "max_facilities": {"Materials Factory": 2},
  "time_window_seconds": 3600
}
```

With `integer=true`, recipe runs are rounded up to whole numbers so the plan maps directly onto facility queues. `facilities` shows integer machine counts.

---

### 4. Net existing stockpile, avoid Coal

The region already has Salvage and Coke in stock. Produce Basic Materials to reach 1 000, drawing from stock first, and never touch Coal chains.

```json
{
  "targets": {"Basic Materials": 1000},
  "use_stockpile": true,
  "hex_name": "Callahan's Passage",
  "banned_items": ["Coal"],
  "objective": "min_raw"
}
```

The solver reads the local stockpile file, filters to Callahan's Passage, nets inventory, and only plans production for the shortfall. `inventory_used` in the result shows what was drawn from stock.

---

### 5. Hauling-constrained ammo push

A team has 3 truck trips to haul raw resources to the front. Plan 150mm shell production within that hauling budget.

```json
{
  "targets": {"150mm": 50},
  "max_trips": 3,
  "objective": "min_raw"
}
```

A linear constraint is added capping the total truck slots used by raw intake. `trips_needed` in the result reports the actual trips the plan requires. If the cap makes the plan infeasible, `status` will be `"infeasible"`.
