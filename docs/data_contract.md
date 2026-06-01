# Data Contract — Nuclear Capacity Dashboard

What the model requires from any reactor data source. Fields are grouped into three tiers:

- **Required** — model will crash or produce wrong projections without this
- **Conditionally required** — required for specific features; model degrades gracefully if absent
- **Display only** — used in charts, labels, and Claude context; model runs fine without it

Map your data source fields to these names before building `ingest.py`. If a required field has no direct equivalent in your source, the "If missing" column explains the safest fallback.

---

## Table 1: `reactors` — one row per reactor unit

### Tier 1 — Required (projection engine breaks without these)

| Field | Type | Valid values | Notes | If missing |
|---|---|---|---|---|
| `reactor_id` | TEXT PK | Any stable unique string | Used as the key for retirement schedule, overrides, pipeline lookup. Must never change between ingest runs. | Cannot be omitted |
| `name` | TEXT | Free text | Reactor name shown in UI and Claude context | Cannot be omitted |
| `country` | TEXT | Free text, upper-case preferred | Used in retirement rule lookup (`UPPER(country) = UPPER(rr.country)`) | Cannot be omitted |
| `region` | TEXT | Must be one of the 13 dashboard regions defined in `config.REGIONS` | All projection aggregations are by region. Any reactor with an unrecognised region is silently excluded from projections. | Cannot be omitted |
| `status` | TEXT | `Operating`, `UnderConstruction`, `Planned`, `Proposed`, `LongTermShutdown`, `Restarted`, `PermanentShutdown` | Controls which model path a reactor takes. Spelling must match exactly (case-sensitive). | Cannot be omitted |
| `net_capacity_mw` | REAL | Positive float, MW | Core capacity figure. Used in all GW calculations. | Cannot be omitted |
| `retirement_date_used` | TEXT | `YYYY-MM-DD` or `YYYY-MM` | **The field the model actually uses for retirement timing.** Must be populated for every `Operating`/`Restarted`/`LongTermShutdown` unit. If your source has only one retirement date field, put it here. | Reactor gets retirement year 2080 (effectively never retires) |
| `commercial_operation_date` | TEXT | `YYYY-MM-DD` or `YYYY-MM` | Used to derive commission year for: (a) de facto extension baseline calculation, (b) technology breakdown historical charts, (c) country capacity historical queries. | Fall back to `first_grid_date`; if both null, reactor excluded from age/extension logic |

### Tier 2 — Required for correct retirement / extension logic

| Field | Type | Valid values | Notes | If missing |
|---|---|---|---|---|
| `retirement_tier` | INTEGER | `1`, `2`, or `3` | Controls how extension policy is applied. **Tier 1** = declared date, never changed by scenario policy. **Tier 2** = licence-expiry derived, adjusted by country rules. **Tier 3** = design-life assumption, most adjustable. | Defaults to Tier 3 (most permissive); if your source has declared shutdown dates treat them as Tier 1 |
| `expected_online_year` | INTEGER | 2020–2060 | Required for `UnderConstruction`, `Planned`, `Proposed` reactors — this is when they enter the projection as capacity. | Pipeline reactor excluded from projection entirely |
| `is_smr` | INTEGER | `0` or `1` | Separates SMR pipeline realization rates from large reactor rates. Also drives technology breakdown charts. | Defaults to 0 (large reactor) — all pipeline treated as large reactors |

### Tier 3 — Conditionally required for specific features

| Field | Type | Notes | Feature affected if missing |
|---|---|---|---|
| `first_grid_date` | TEXT | `YYYY-MM` or `YYYY-MM-DD` | Fallback when `commercial_operation_date` is null for commission year derivation | Historical country capacity queries may be incomplete |
| `restart_date` | TEXT | `YYYY-MM` | For `Restarted` / `LongTermShutdown` reactors — controls when they re-enter the operating fleet in projections | LTS reactors never re-enter fleet; Restarted reactors operate from 2024 regardless of actual restart year |
| `restart_capacity_mw` | REAL | MW | Net capacity after restart if different from original. `COALESCE(restart_capacity_mw, net_capacity_mw)` is used throughout. | Falls back to `net_capacity_mw` — fine for most reactors |
| `long_term_shutdown_date` | TEXT | `YYYY-MM` | Used in historical country capacity queries to exclude LTS units from years before they went dark | LTS reactors included in historical capacity even for years they weren't contributing |
| `actual_shutdown_date` | TEXT | `YYYY-MM` | Used in `get_full_reactor_download()` for the exported data table. Not used in projections. | Download table shows retirement_date_used instead |
| `pipeline_probability` | REAL | 0.0–1.0 | Per-unit probability override. If NULL, scenario-level rates apply. Used in Claude context builder to show `p=xx%` beside pipeline reactors. | Scenario-level rates apply (expected behavior for most reactors) |
| `construction_delay_years` | REAL | 0–15 | Per-unit additional delay on top of scenario delay adder. | Treated as 0 (no per-unit delay) |
| `latitude` / `longitude` | REAL | Decimal degrees | Required for World Map tab. | Map tab shows no markers |
| `eaf_pct` / `ucf_pct` | REAL | 0–100 | Electrical availability factor and unit capacity factor. Used in fleet profile analytics only. | Analyst tool `get_fleet_profile` returns null for capacity factor fields |

### Tier 4 — Display only (Claude context, labels, download)

| Field | Type | Notes |
|---|---|---|
| `reactor_type` | TEXT | `PWR`, `BWR`, `PHWR`, `VVER`, `RBMK`, `GCR`, `FBR`, etc. Used for technology breakdown charts and Claude context tags like `[PWR·AP-1000]`. Defaults to "Other" tech group if null. |
| `reactor_model` | TEXT | E.g. `AP-1000`, `EPR`, `VVER-1200`. Display only in Claude context. |
| `nsss_supplier` | TEXT | E.g. `WH`, `FRAM`, `AEM`, `GE`. Used in technology breakdown (`group_by='supplier'`) and Claude context `oem=WH`. Readable names resolved via `_SUPPLIER_NAMES` dict in `claude_analyst.py`. |
| `owner` / `operator` | TEXT | Display only in reactor download. |
| `design_life_years` | INTEGER | Default 40. Used as ultimate fallback for Tier 3 retirement if no other date is available. If you have this data, populate it. |
| `data_source` | TEXT | Provenance label. Not used in model. |
| `last_updated` | TEXT | Provenance label. Not used in model. |
| `notes` | TEXT | Free text. Not used in model. |

---

## Table 2: `retirement_rules` — one row per country

Required for Tier 2 retirement logic. If a country has no row, fallback is `max_life=60yr, ext_unit=20yr`.

| Field | Type | Notes |
|---|---|---|
| `country` | TEXT PK | Must match `UPPER(reactors.country)` exactly |
| `baseline_license_years` | INTEGER | Nominal design life (typically 40) |
| `typical_extension_years` | INTEGER | Single extension increment (10 or 20 typically) |
| `total_assumed_life_years` | INTEGER | `baseline + typical_extension` |
| `max_possible_life_years` | INTEGER | Regulatory maximum (US=80, France=60, Japan=80 post-GX) |
| `extension_unit_years` | INTEGER | Discrete increment per extension round (10 or 20) |

If your data source does not have this level of regulatory detail, populate the table with conservative estimates (max=60, unit=20 for all countries) and set all reactors to Tier 1. This disables the probabilistic extension machinery entirely.

---

## Table 3: `historical_capacity` — one row per (year, region)

Required for the historical lines on all charts (2005–2024). If absent, charts show projection-only from 2024.

| Field | Type | Notes |
|---|---|---|
| `year` | INTEGER | 2005–2024 |
| `region` | TEXT | Must match `config.REGIONS` or `"Global"` |
| `capacity_gw` | REAL | Observed operating capacity in GW |
| `num_reactors` | INTEGER | Count of operating reactors that year. Display only. |
| `source` | TEXT | Provenance label. Default `'IAEA_RDS2'`. |

If you cannot source historical data by region, derive it: run the projection model backwards using `get_what_if_country_capacity()` for each historical year, or use IAEA RDS-2 annual reports.

---

## Table 4: `benchmarks` — one row per (source, scenario, year, region)

Required for benchmark overlay lines on the Global Projection chart. If absent, chart renders without overlays (no crash).

| Field | Type | Notes |
|---|---|---|
| `source` | TEXT | E.g. `'IEA WEO 2024'`, `'IAEA RDS-1 2025'` |
| `scenario_name` | TEXT | E.g. `'STEPS'`, `'APS'`, `'Low'`, `'High'` |
| `year` | INTEGER | 2024–2050 |
| `region` | TEXT | Usually `'Global'`; regional breakdown available from IAEA |
| `capacity_gw` | REAL | Reference capacity in GW |

---

## Table 5: `scenarios` — one row per scenario

Pre-loaded by `model/scenarios.py:load_scenarios()`. You do not derive these from a source — you define them based on your analytical framework. See `docs/scenario_calibration.md` for the reference implementation's values and calibration methodology.

---

## Quick checklist for a new data source

Before running `python ingest.py --project`, verify:

- [ ] Every `Operating`/`Restarted`/`LongTermShutdown` reactor has `retirement_date_used` populated
- [ ] Every `UnderConstruction`/`Planned`/`Proposed` reactor has `expected_online_year` populated and `net_capacity_mw` > 0
- [ ] All `region` values exactly match `config.REGIONS` (check with `SELECT DISTINCT region FROM reactors WHERE region NOT IN (...)`)
- [ ] All `status` values are one of the 7 valid strings (exact case)
- [ ] `reactor_id` values are stable across ingest runs (not auto-incremented integers that change on re-ingest)
- [ ] `retirement_tier` is set — if unsure, use `1` for reactors with a known declared date, `3` for everything else
- [ ] `retirement_rules` table has a row for each country with significant operating capacity
- [ ] `historical_capacity` covers 2005–2024 for all regions and `"Global"`

---

## Simplest possible working dataset

If you want to get the model running quickly on a new data source before full field mapping is complete, the absolute minimum required fields are:

```
reactors:  reactor_id, name, country, region, status, net_capacity_mw,
           retirement_date_used (Operating only), expected_online_year (Pipeline only)
scenarios: loaded by model/scenarios.py — no source data needed
historical_capacity: can be stub zeros to start
retirement_rules: can be empty (falls back to 60yr/20yr default)
```

Set `retirement_tier = 1` on all operating reactors to disable extension logic until you have `retirement_rules` populated and calibrated.
