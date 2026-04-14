"""
schema.py — Creates all SQLite tables for the nuclear dashboard database.
Run this once (or to reset): python -m model.schema
"""
import sqlite3
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DB_PATH


DDL = """
-- ── 1. Core unit-level reactor table ────────────────────────────────────
CREATE TABLE IF NOT EXISTS reactors (
    reactor_id                      TEXT PRIMARY KEY,
    name                            TEXT NOT NULL,
    country                         TEXT,
    region                          TEXT,
    status                          TEXT,   -- Operating|UnderConstruction|Planned|Proposed|
                                            -- LongTermShutdown|Restarted|PermanentShutdown
    net_capacity_mw                 REAL,
    gross_capacity_mw               REAL,
    thermal_capacity_mw             REAL,
    restart_capacity_mw             REAL,   -- post-restart net capacity if different
    reactor_type                    TEXT,   -- PWR|BWR|PHWR|RBMK|SMR|GCR|FBR|...
    reactor_model                   TEXT,
    owner                           TEXT,
    operator                        TEXT,
    nsss_supplier                   TEXT,
    construction_start_date         TEXT,   -- YYYY-MM (partial dates stored as text)
    first_criticality_date          TEXT,
    first_grid_date                 TEXT,
    commercial_operation_date       TEXT,
    age_at_baseline_years           REAL,   -- computed at ingestion
    long_term_shutdown_date         TEXT,   -- when entered LTS
    restart_date                    TEXT,   -- when restarted from LTS
    actual_shutdown_date            TEXT,   -- for permanently shut units
    planned_shutdown_date_declared  TEXT,   -- Tier 1: officially declared
    license_expiry_date_derived     TEXT,   -- Tier 2: derived from country rules
    retirement_date_used            TEXT,   -- MODEL USES THIS: Tier1 → Tier2 → Tier3
    retirement_tier                 INTEGER, -- 1|2|3
    design_life_years               INTEGER DEFAULT 40,
    expected_online_year            INTEGER, -- pipeline units
    expected_online_year_source     TEXT,    -- PRIS|WNA|Operator|Estimated
    pipeline_probability            REAL,    -- 0.0–1.0; NULL for operating units
    construction_delay_years        REAL DEFAULT 0,
    eaf_pct                         REAL,    -- electrical availability factor (10yr avg)
    ucf_pct                         REAL,    -- unit capacity factor (10yr avg)
    is_smr                          INTEGER DEFAULT 0,
    latitude                        REAL,
    longitude                       REAL,
    data_source                     TEXT,
    last_updated                    TEXT,
    notes                           TEXT
);

-- ── 2. Country-level retirement rules (Tier 2 parameters) ────────────────
CREATE TABLE IF NOT EXISTS retirement_rules (
    country                     TEXT PRIMARY KEY,
    baseline_license_years      INTEGER,
    typical_extension_years     INTEGER,
    total_assumed_life_years    INTEGER,   -- baseline + typical extension
    max_possible_life_years     INTEGER,   -- theoretical max under current law
    extension_unit_years        INTEGER,   -- discrete increment (10 or 20)
    notes                       TEXT
);

-- ── 3. Scenario configuration (global parameters) ────────────────────────
CREATE TABLE IF NOT EXISTS scenarios (
    scenario_id                         TEXT PRIMARY KEY,
    name                                TEXT NOT NULL,
    description                         TEXT,
    extension_policy_global             TEXT DEFAULT 'HistoricalRate',
                                        -- None|HistoricalRate|Moderate|MaximumAllowed
    pipeline_uc_realization_rate        REAL DEFAULT 0.95,
    pipeline_planned_realization_rate   REAL DEFAULT 0.70,
    pipeline_proposed_realization_rate  REAL DEFAULT 0.30,
    smr_realization_rate_override       REAL,   -- NULL = use stage default
    smr_uc_rate                         REAL,   -- per-stage SMR override (UC)
    smr_planned_rate                    REAL,   -- per-stage SMR override (Planned)
    smr_proposed_rate                   REAL,   -- per-stage SMR override (Proposed)
    construction_delay_adder_years      REAL DEFAULT 0,
    post2040_new_build_is_regional      INTEGER DEFAULT 1,
    post2040_global_growth_gw_per_year  REAL DEFAULT 15,
    transition_year                     INTEGER DEFAULT 2040,
    is_user_defined                     INTEGER DEFAULT 0,
    created_at                          TEXT DEFAULT (datetime('now'))
);

-- ── 4. Per-region overrides within a scenario ─────────────────────────────
CREATE TABLE IF NOT EXISTS scenario_regional_params (
    scenario_id                         TEXT,
    region                              TEXT,
    extension_policy                    TEXT,   -- NULL = use scenario global
    pipeline_uc_realization_rate        REAL,   -- NULL = use scenario global
    pipeline_planned_realization_rate   REAL,
    pipeline_proposed_realization_rate  REAL,
    pipeline_rate_source                TEXT,   -- REQUIRED if rates are non-NULL
    post2040_growth_gw_per_year         REAL,   -- NULL = use scenario global
    phase_out_by_year                   INTEGER,-- NULL = no phase-out
    PRIMARY KEY (scenario_id, region)
);

-- ── 5. Manual user-defined capacity adders ───────────────────────────────
CREATE TABLE IF NOT EXISTS region_adders (
    adder_id    TEXT PRIMARY KEY,
    scenario_id TEXT,
    region      TEXT,
    year        INTEGER,
    capacity_gw REAL,   -- positive = addition; negative = early retirement
    label       TEXT
);

-- ── 6. Historical observed capacity (2005–2024) ───────────────────────────
CREATE TABLE IF NOT EXISTS historical_capacity (
    year        INTEGER,
    region      TEXT,
    capacity_gw REAL,
    num_reactors INTEGER,
    source      TEXT DEFAULT 'IAEA_RDS2',
    PRIMARY KEY (year, region)
);

-- ── 7. Pre-computed annual projections per scenario ───────────────────────
CREATE TABLE IF NOT EXISTS projections (
    scenario_id             TEXT,
    year                    INTEGER,
    region                  TEXT,
    capacity_operating_gw   REAL,
    capacity_retired_ytd_gw REAL,
    capacity_added_ytd_gw   REAL,
    retirements_this_year_gw REAL,
    additions_this_year_gw  REAL,
    adders_this_year_gw     REAL DEFAULT 0,
    is_bottom_up            INTEGER DEFAULT 1,  -- 1=bottom-up; 0=top-down
    PRIMARY KEY (scenario_id, year, region)
);

-- ── 8. External benchmark reference points ───────────────────────────────
CREATE TABLE IF NOT EXISTS benchmarks (
    source          TEXT,
    scenario_name   TEXT,
    year            INTEGER,
    region          TEXT,
    capacity_gw     REAL,
    PRIMARY KEY (source, scenario_name, year, region)
);

-- ── 9. Data source vintage log ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS data_sources_log (
    source_name     TEXT PRIMARY KEY,
    data_as_of_date TEXT,
    ingestion_date  TEXT,
    version_note    TEXT
);
"""


def create_schema(db_path: Path = DB_PATH, reset: bool = False) -> None:
    """Create all tables. If reset=True, drop and recreate."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    if reset:
        print("  Dropping existing tables...")
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        for (tbl,) in cur.fetchall():
            cur.execute(f"DROP TABLE IF EXISTS {tbl}")
        conn.commit()
    conn.executescript(DDL)
    conn.commit()
    conn.close()
    print(f"  Schema created at: {db_path}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="Drop and recreate all tables")
    args = parser.parse_args()
    create_schema(reset=args.reset)
