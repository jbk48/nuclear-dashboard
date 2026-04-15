"""
api.py — Clean Python API for the dashboard layer.

The dashboard (Streamlit or React) must ONLY call these functions.
It never queries the database directly.
"""
import sqlite3
import pandas as pd
from pathlib import Path
from config import DB_PATH, BASELINE_YEAR, PROJECTION_END_YEAR
from model.projection import run_projection
from model.retirement import build_retirement_schedule
from model.pipeline import build_pipeline
from model.validate import run_validation


def _conn(db_path: Path = DB_PATH) -> sqlite3.Connection:
    return sqlite3.connect(db_path)


# ── Projections ────────────────────────────────────────────────────────────

def get_projection(
    scenario_id: str = "base",
    region: str = "Global",
    db_path: Path = DB_PATH,
) -> pd.DataFrame:
    """
    Return annual projection for a scenario and region.
    Columns: year, capacity_operating_gw, retirements_this_year_gw,
             additions_this_year_gw, adders_this_year_gw, is_bottom_up
    """
    conn = _conn(db_path)
    df = pd.read_sql_query("""
        SELECT year, capacity_operating_gw, capacity_retired_ytd_gw,
               capacity_added_ytd_gw, retirements_this_year_gw,
               additions_this_year_gw, adders_this_year_gw, is_bottom_up
        FROM projections
        WHERE scenario_id = ? AND region = ?
        ORDER BY year
    """, conn, params=(scenario_id, region))
    conn.close()
    return df


def compute_projection(
    scenario_id: str,
    db_path: Path = DB_PATH,
) -> pd.DataFrame:
    """Re-run projection for a scenario and return results."""
    conn = _conn(db_path)
    rows = run_projection(conn, scenario_id)
    conn.close()
    return pd.DataFrame(rows)


def get_all_scenarios(db_path: Path = DB_PATH) -> pd.DataFrame:
    """Return all scenarios."""
    conn = _conn(db_path)
    df = pd.read_sql_query("SELECT * FROM scenarios ORDER BY scenario_id", conn)
    conn.close()
    return df


# ── What-If projection ────────────────────────────────────────────────────

def run_what_if_projection(
    scenario_id: str,
    what_if_overrides: dict,
    region: str = "Global",
    db_path: Path = DB_PATH,
) -> pd.DataFrame:
    """
    Run a projection with in-memory reactor overrides and return results.
    Does NOT write to the projections table — purely ephemeral.

    what_if_overrides format:
        { reactor_id: { field: value, ... }, ... }
    Supported fields per reactor:
        retirement_year (int)
        status          (str)  e.g. "Restarted", "LongTermShutdown"
        restart_date    (str)  e.g. "2028-01"
        capacity_mw     (float)
    """
    conn = _conn(db_path)
    rows = run_projection(
        conn, scenario_id,
        what_if_overrides=what_if_overrides,
        write_to_db=False,
    )
    conn.close()
    df = pd.DataFrame(rows)
    if region != "Global":
        df = df[df["region"] == region]
    else:
        df = df[df["region"] == "Global"]
    return df[["year", "capacity_operating_gw", "retirements_this_year_gw",
               "additions_this_year_gw", "is_bottom_up"]].sort_values("year")


def get_reactor_options(
    db_path: Path = DB_PATH,
) -> pd.DataFrame:
    """
    Return reactors available for what-if overrides.
    Includes Operating, Restarted, LongTermShutdown, and PermanentShutdown
    (so users can model restarts of recently-closed plants).
    Columns: reactor_id, name, country, region, status, net_capacity_mw,
             retirement_date_used, restart_date, commercial_operation_date
    """
    conn = _conn(db_path)
    df = pd.read_sql_query("""
        SELECT reactor_id, name, country, region, status,
               COALESCE(net_capacity_mw, 0) as net_capacity_mw,
               retirement_date_used, restart_date,
               commercial_operation_date
        FROM reactors
        WHERE status IN ('Operating','Restarted','LongTermShutdown','PermanentShutdown','Suspended')
        ORDER BY region, country, name
    """, conn)
    conn.close()
    return df


# ── Historical data ────────────────────────────────────────────────────────

def get_historical(
    region: str = "Global",
    db_path: Path = DB_PATH,
) -> pd.DataFrame:
    """Return historical capacity data (2005–2024)."""
    conn = _conn(db_path)
    df = pd.read_sql_query("""
        SELECT year, capacity_gw, num_reactors, source
        FROM historical_capacity
        WHERE region = ?
        ORDER BY year
    """, conn, params=(region,))
    conn.close()
    return df


# ── Benchmarks ────────────────────────────────────────────────────────────

def get_benchmarks(
    region: str = "Global",
    db_path: Path = DB_PATH,
) -> pd.DataFrame:
    """Return all benchmark reference points for a region."""
    conn = _conn(db_path)
    df = pd.read_sql_query("""
        SELECT source, scenario_name, year, capacity_gw
        FROM benchmarks WHERE region = ?
        ORDER BY source, scenario_name, year
    """, conn, params=(region,))
    conn.close()
    return df


# ── Reactor data ───────────────────────────────────────────────────────────

def get_reactor_list(
    status: list[str] | None = None,
    region: str | None = None,
    db_path: Path = DB_PATH,
) -> pd.DataFrame:
    """Return reactor list with optional filters."""
    conn = _conn(db_path)
    where_clauses = []
    params = []
    if status:
        placeholders = ",".join("?" for _ in status)
        where_clauses.append(f"status IN ({placeholders})")
        params.extend(status)
    if region:
        where_clauses.append("region = ?")
        params.append(region)
    where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    df = pd.read_sql_query(
        f"SELECT * FROM reactors {where} ORDER BY region, country, name",
        conn, params=params
    )
    conn.close()
    return df


def get_full_reactor_download(db_path: Path = DB_PATH) -> pd.DataFrame:
    """
    Return a clean, user-facing DataFrame of all reactors for the full-dataset download.
    Includes operating, pipeline, and long-term-shutdown units.
    Columns: Name, Country, Region, Capacity (MW), OEM, Tech Type, Status,
             SMR, Construction Start Year, Online Year, Retirement Year, Restart Year.
    """
    conn = _conn(db_path)
    df = pd.read_sql_query("""
        SELECT
            r.name,
            r.country,
            r.region,
            r.net_capacity_mw,
            r.nsss_supplier                                            AS oem,
            r.reactor_type,
            r.is_smr,
            r.status,
            CAST(SUBSTR(r.construction_start_date, 1, 4) AS INTEGER)  AS construction_start_year,
            COALESCE(
                CAST(SUBSTR(r.commercial_operation_date, 1, 4) AS INTEGER),
                CAST(SUBSTR(r.first_grid_date, 1, 4) AS INTEGER),
                r.expected_online_year
            )                                                          AS online_year,
            CAST(SUBSTR(
                COALESCE(r.actual_shutdown_date, r.retirement_date_used), 1, 4
            ) AS INTEGER)                                              AS retirement_year,
            CAST(SUBSTR(r.restart_date, 1, 4) AS INTEGER)             AS restart_year,
            -- Max retirement year = commissioning year + max possible life per country rules
            CASE
                WHEN COALESCE(
                    CAST(SUBSTR(r.commercial_operation_date, 1, 4) AS INTEGER),
                    CAST(SUBSTR(r.first_grid_date, 1, 4) AS INTEGER)
                ) IS NOT NULL
                THEN COALESCE(
                    CAST(SUBSTR(r.commercial_operation_date, 1, 4) AS INTEGER),
                    CAST(SUBSTR(r.first_grid_date, 1, 4) AS INTEGER)
                ) + COALESCE(rr.max_possible_life_years, 60)
                ELSE NULL
            END                                                        AS max_retirement_year
        FROM reactors r
        LEFT JOIN retirement_rules rr ON UPPER(r.country) = UPPER(rr.country)
        ORDER BY r.region, r.country, r.name
    """, conn)
    conn.close()

    # Human-readable status labels
    status_map = {
        "Operating":          "Active",
        "LongTermShutdown":   "Long-Term Shutdown",
        "UnderConstruction":  "Under Construction",
        "Planned":            "Planned",
        "Proposed":           "Proposed",
    }
    df["status"] = df["status"].map(status_map).fillna(df["status"])

    # Detailed tech type labels
    tech_map = {
        "PWR":  "Pressurized Water Reactor (light-water)",
        "BWR":  "Boiling Water Reactor (light-water)",
        "PHWR": "Pressurized Heavy Water Reactor (CANDU-type)",
        "FBR":  "Fast Breeder Reactor (sodium-cooled)",
        "SFR":  "Sodium Fast Reactor (sodium-cooled)",
        "FHR":  "Fluoride Salt-Cooled High Temp Reactor (molten salt)",
        "HTGR": "High-Temp Gas-Cooled Reactor (pebble bed)",
    }
    df["tech_type"] = df["reactor_type"].map(tech_map).fillna(df["reactor_type"])
    # SMR overrides
    smr_mask = df["is_smr"] == 1
    smr_type_map = {
        "PWR":  "Small Modular PWR (pressurized light-water)",
        "BWR":  "Small Modular BWR (boiling light-water)",
        "HTGR": "Small Modular HTGR (pebble bed, gas-cooled)",
        "FHR":  "Small Modular FHR (molten salt-cooled)",
        "SFR":  "Small Modular SFR (sodium-cooled)",
    }
    for rtype, label in smr_type_map.items():
        df.loc[smr_mask & (df["reactor_type"] == rtype), "tech_type"] = label

    df["smr"] = df["is_smr"].map({1: "Yes", 0: "No"})
    df["oem"] = df["oem"].fillna("").replace("", "—")

    output = df[[
        "name", "country", "region", "net_capacity_mw", "oem",
        "tech_type", "status", "smr",
        "construction_start_year", "online_year",
        "retirement_year", "max_retirement_year", "restart_year",
    ]].copy()
    output.columns = [
        "Name", "Country", "Region", "Capacity (MW)", "OEM",
        "Tech Type", "Status", "SMR",
        "Construction Start Year", "Online Year",
        "Retirement Year", "Max Retirement Year", "Restart Year",
    ]
    return output


# ── Data vintage ──────────────────────────────────────────────────────────

def get_data_vintage(db_path: Path = DB_PATH) -> dict[str, str]:
    """
    Returns a dict of {source_name: vintage_string} for the dashboard footer.
    Example: {"PRIS": "as of 2024-12-31", "IEA_WEO2024": "as of 2024-10-01"}
    """
    conn = _conn(db_path)
    cur = conn.cursor()
    cur.execute("""
        SELECT source_name, data_as_of_date, version_note
        FROM data_sources_log ORDER BY source_name
    """)
    rows = cur.fetchall()
    conn.close()
    return {row[0]: f"as of {row[1]} ({row[2]})" for row in rows}


def get_data_vintage_string(db_path: Path = DB_PATH) -> str:
    """Single-line vintage string for dashboard footer."""
    vintage = get_data_vintage(db_path)
    parts = [f"{src}: {v.split('(')[0].strip()}" for src, v in vintage.items()]
    return " · ".join(parts)


# ── Manual adders ─────────────────────────────────────────────────────────

def _apply_smr_acceleration(
    region_dfs: dict,
    start_year: int,
    gw_per_year: float,
    transition_year: int = 2040,
    regional_weights: dict | None = None,
) -> dict:
    """
    Post-processing: add pre-2040 SMR acceleration (beyond announced pipeline)
    to projection DataFrames. gw_per_year is the global rate distributed across
    regions using regional_weights (fractions summing to 1). If not provided,
    falls back to an even split across non-Global regions.
    """
    if gw_per_year <= 0:
        return region_dfs

    non_global = [r for r in region_dfs if r != "Global"]

    # Build per-region rates from weights, or fall back to even split
    if regional_weights:
        weight_total = sum(regional_weights.get(r, 0.0) for r in non_global)
        if weight_total > 0:
            region_rates = {
                r: gw_per_year * (regional_weights.get(r, 0.0) / weight_total)
                for r in non_global
            }
        else:
            per_region = gw_per_year / len(non_global) if non_global else 0.0
            region_rates = {r: per_region for r in non_global}
    else:
        per_region = gw_per_year / len(non_global) if non_global else 0.0
        region_rates = {r: per_region for r in non_global}

    result = {}
    for region, df in region_dfs.items():
        df = df.copy().sort_values("year").reset_index(drop=True)
        rate = gw_per_year if region == "Global" else region_rates.get(region, 0.0)
        cumulative = 0.0
        for i in range(len(df)):
            yr = int(df.at[i, "year"])
            if start_year <= yr <= transition_year:
                df.at[i, "additions_this_year_gw"] += rate
                cumulative += rate
            if yr >= start_year:
                df.at[i, "capacity_operating_gw"] += cumulative
        result[region] = df
    return result


def cleanup_stale_custom_scenarios(
    db_path: Path = DB_PATH,
    max_age_hours: int = 2,
) -> None:
    """
    Delete custom_* scenarios (and their associated projections/regional params)
    that were created more than max_age_hours ago. Safe to call at session startup.
    Preset scenarios (decline/conservative/base/optimistic) are never touched.
    """
    conn = _conn(db_path)
    try:
        cur = conn.cursor()
        cur.execute("""
            DELETE FROM projections
            WHERE scenario_id LIKE 'custom_%'
              AND scenario_id IN (
                  SELECT scenario_id FROM scenarios
                  WHERE scenario_id LIKE 'custom_%'
                    AND created_at IS NOT NULL
                    AND created_at < datetime('now', ? || ' hours')
              )
        """, (f'-{max_age_hours}',))
        cur.execute("""
            DELETE FROM scenario_regional_params
            WHERE scenario_id LIKE 'custom_%'
              AND scenario_id IN (
                  SELECT scenario_id FROM scenarios
                  WHERE scenario_id LIKE 'custom_%'
                    AND created_at IS NOT NULL
                    AND created_at < datetime('now', ? || ' hours')
              )
        """, (f'-{max_age_hours}',))
        cur.execute("""
            DELETE FROM scenarios
            WHERE scenario_id LIKE 'custom_%'
              AND created_at IS NOT NULL
              AND created_at < datetime('now', ? || ' hours')
        """, (f'-{max_age_hours}',))
        conn.commit()
    finally:
        conn.close()


def write_and_run_custom_scenario(
    extension_policy: str,
    pipeline_uc_rate: float,
    pipeline_planned_rate: float,
    pipeline_proposed_rate: float,
    construction_delay_adder: float,
    post2040_global_gw: float,
    smr_uc_rate: float = 1.0,
    smr_planned_rate: float = 1.0,
    smr_proposed_rate: float = 0.0,
    china_post2040_gw: float = 12.0,
    smr_accel_start_year: int = 2040,
    smr_accel_gw_per_year: float = 0.0,
    transition_year: int = 2040,
    scenario_id: str = "custom",
    db_path: Path = DB_PATH,
) -> dict[str, pd.DataFrame]:
    """
    Write a custom scenario to the DB and run its projection.
    Returns {region: df} for Global + all regions.

    scenario_id should be a session-unique value (e.g. 'custom_<uuid>') so that
    concurrent sessions don't overwrite each other's rows in the DB.
    china_post2040_gw is carved out of the global rate; the remainder is
    distributed proportionally to the base-case regional params.
    """
    from config import REGIONS
    conn = _conn(db_path)
    try:
        cur = conn.cursor()

        # Upsert the custom scenario row
        cur.execute("""
            INSERT OR REPLACE INTO scenarios (
                scenario_id, name, description,
                extension_policy_global,
                pipeline_uc_realization_rate,
                pipeline_planned_realization_rate,
                pipeline_proposed_realization_rate,
                smr_uc_rate,
                smr_planned_rate,
                smr_proposed_rate,
                smr_realization_rate_override,
                construction_delay_adder_years,
                post2040_new_build_is_regional,
                post2040_global_growth_gw_per_year,
                transition_year,
                is_user_defined,
                created_at
            ) VALUES (
                ?, 'Custom', 'User-defined scenario',
                ?, ?, ?, ?, ?, ?, ?, NULL, ?, 0, ?, ?, 1, datetime('now')
            )
        """, (
            scenario_id,
            extension_policy,
            pipeline_uc_rate, pipeline_planned_rate, pipeline_proposed_rate,
            smr_uc_rate, smr_planned_rate, smr_proposed_rate,
            construction_delay_adder,
            post2040_global_gw,
            transition_year,
        ))

        # Write per-region post-2040 rates
        # Non-China regions are distributed proportionally to the base case regional params
        # (rather than evenly), preserving the base case geographic distribution at any scale.
        cur.execute("DELETE FROM scenario_regional_params WHERE scenario_id = ?", (scenario_id,))
        china_gw = max(0.0, min(china_post2040_gw, post2040_global_gw))
        other_regions = [r for r in REGIONS if r != "China"]
        remaining = max(0.0, post2040_global_gw - china_gw)

        # Load base case proportions AND extension policies for all regions.
        # Extension policies capture calibrated per-region retirement rules
        # (e.g., US uses MaximumAllowed reflecting NRC 20-year renewal practice)
        # and must be preserved in custom scenarios so the retirement schedule
        # matches the pre-computed scenarios.
        cur.execute("""
            SELECT region, post2040_growth_gw_per_year, extension_policy
            FROM scenario_regional_params
            WHERE scenario_id = 'base'
        """)
        base_all_rows = cur.fetchall()
        base_regional   = {row[0]: max(0.0, row[1] or 0.0) for row in base_all_rows if row[0] != "China"}
        base_ext_policy = {row[0]: row[2] for row in base_all_rows}  # None is fine — means "use global"
        base_non_china_total = sum(base_regional.values())

        if base_non_china_total > 0:
            regional_rows = [(scenario_id, "China", china_gw, base_ext_policy.get("China"))] + [
                (scenario_id, r,
                 remaining * (base_regional.get(r, 0.0) / base_non_china_total),
                 base_ext_policy.get(r))
                for r in other_regions
            ]
        else:
            # Fallback to even split if base case has no regional params
            per_other = remaining / len(other_regions) if other_regions else 0.0
            regional_rows = [(scenario_id, "China", china_gw, base_ext_policy.get("China"))] + [
                (scenario_id, r, per_other, base_ext_policy.get(r)) for r in other_regions
            ]
        cur.executemany("""
            INSERT INTO scenario_regional_params (scenario_id, region, post2040_growth_gw_per_year, extension_policy)
            VALUES (?, ?, ?, ?)
        """, regional_rows)
        conn.commit()

        # Re-run projection
        run_projection(conn, scenario_id)

        # Load results
        result = {}
        for region in REGIONS + ["Global"]:
            result[region] = pd.read_sql_query("""
                SELECT year, capacity_operating_gw, capacity_retired_ytd_gw,
                       capacity_added_ytd_gw, retirements_this_year_gw,
                       additions_this_year_gw, adders_this_year_gw, is_bottom_up
                FROM projections
                WHERE scenario_id = ? AND region = ?
                ORDER BY year
            """, conn, params=(scenario_id, region))

    finally:
        conn.close()

    # Apply pre-2040 SMR acceleration (post-processing, in-memory only)
    # Use the same regional weights as the custom scenario's post-2040 distribution
    if smr_accel_gw_per_year > 0:
        smr_weights = {r: v for r, v in zip(
            [row[0] for row in regional_rows],
            [row[2] for row in regional_rows],
        )}
        result = _apply_smr_acceleration(
            result, smr_accel_start_year, smr_accel_gw_per_year,
            transition_year, regional_weights=smr_weights,
        )

    return result


def add_region_adder(
    scenario_id: str,
    region: str,
    year: int,
    capacity_gw: float,
    label: str = "",
    db_path: Path = DB_PATH,
) -> str:
    """Add a manual capacity adder row. Returns the adder_id."""
    import uuid
    adder_id = str(uuid.uuid4())[:8]
    conn = _conn(db_path)
    conn.execute("""
        INSERT INTO region_adders (adder_id, scenario_id, region, year, capacity_gw, label)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (adder_id, scenario_id, region, year, capacity_gw, label))
    conn.commit()
    conn.close()
    return adder_id


# ── Country-level capacity snapshot ───────────────────────────────────────

def get_country_capacity(
    year: int = 2026,
    scenario_id: str = "base",
    db_path: Path = DB_PATH,
) -> pd.DataFrame:
    """
    Returns per-country capacity snapshot for a given year under a scenario.

    For year >= BASELINE_YEAR (2024): forward-looking — uses scenario retirement
    schedule + realization-weighted pipeline.
    For year < BASELINE_YEAR: historical — uses actual commissioning/retirement
    dates from the reactor table (no pipeline; retired reactors already decommissioned
    before 2024 are not in the DB, so figures are a lower bound for earlier years).

    Columns: country, region, operating_gw, pipeline_gw, total_gw
    """
    from model.retirement import build_retirement_schedule
    from model.pipeline import build_pipeline

    conn = _conn(db_path)
    cur = conn.cursor()

    # ── Historical mode (pre-2024) ─────────────────────────────────────────
    if year < BASELINE_YEAR:
        cur.execute("""
            SELECT country, region,
                   COALESCE(restart_capacity_mw, net_capacity_mw, 0) as cap_mw
            FROM reactors
            WHERE status IN ('Operating', 'LongTermShutdown', 'Restarted')
              AND CAST(SUBSTR(COALESCE(commercial_operation_date, first_grid_date),
                              1, 4) AS INTEGER) <= ?
              AND (retirement_date_used IS NULL
                   OR CAST(SUBSTR(retirement_date_used, 1, 4) AS INTEGER) > ?)
              AND (
                  -- Non-LTS/Restarted reactors: always include if commissioned & not retired
                  status = 'Operating'
                  OR (
                      status IN ('LongTermShutdown', 'Restarted')
                      AND (
                          -- Include if LTS hadn't started yet in this year
                          long_term_shutdown_date IS NULL
                          OR CAST(SUBSTR(long_term_shutdown_date, 1, 4) AS INTEGER) > ?
                          -- OR include if it had been restarted by this year
                          OR (restart_date IS NOT NULL
                              AND CAST(SUBSTR(restart_date, 1, 4) AS INTEGER) <= ?)
                      )
                  )
              )
        """, (year, year, year, year))
        operating_rows = [{"country": r[0], "region": r[1], "cap_mw": r[2]}
                          for r in cur.fetchall()]
        pipeline_rows = []
        conn.close()

        df_op = pd.DataFrame(operating_rows) if operating_rows else pd.DataFrame(
            columns=["country", "region", "cap_mw"])
        df_pipe = pd.DataFrame(columns=["country", "region", "cap_mw"])
        op_agg = (df_op.groupby(["country", "region"])["cap_mw"].sum()
                  .reset_index().rename(columns={"cap_mw": "operating_mw"}))
        merged = op_agg.copy()
        merged["operating_gw"] = (merged["operating_mw"] / 1000).round(3)
        merged["pipeline_gw"]  = 0.0
        merged["total_gw"]     = merged["operating_gw"]
        return (merged[["country", "region", "operating_gw", "pipeline_gw", "total_gw"]]
                .sort_values("total_gw", ascending=False)
                .reset_index(drop=True))

    # ── Forward-looking mode (2024+) ───────────────────────────────────────
    # Build retirement schedule for this scenario
    retirement_schedule = build_retirement_schedule(conn, scenario_id)

    # Load operating fleet
    cur.execute("""
        SELECT reactor_id, country, region, status,
               COALESCE(restart_capacity_mw, net_capacity_mw, 0) as cap_mw,
               restart_date
        FROM reactors
        WHERE status IN ('Operating', 'LongTermShutdown', 'Restarted')
    """)
    operating_rows = []
    for rid, country, region, status, cap_mw, restart_date in cur.fetchall():
        if status == "LongTermShutdown":
            if not restart_date or int(restart_date[:4]) > year:
                continue
        ret_year = retirement_schedule.get(rid)
        if ret_year is None or ret_year > year:
            operating_rows.append({"country": country, "region": region, "cap_mw": cap_mw})

    # Build pipeline and filter to reactors online by this year
    pipeline = build_pipeline(conn, scenario_id)
    pipeline_rows = []
    for r in pipeline:
        eff_yr = r.get("effective_online_year")
        if eff_yr and eff_yr <= year:
            pipeline_rows.append({
                "country": r["country"],
                "region": r["region"],
                "cap_mw": r["expected_capacity_mw"],
            })

    conn.close()

    # Aggregate by country
    df_op = pd.DataFrame(operating_rows) if operating_rows else pd.DataFrame(
        columns=["country", "region", "cap_mw"])
    df_pipe = pd.DataFrame(pipeline_rows) if pipeline_rows else pd.DataFrame(
        columns=["country", "region", "cap_mw"])

    op_agg = (df_op.groupby(["country", "region"])["cap_mw"].sum()
              .reset_index().rename(columns={"cap_mw": "operating_mw"}))
    pipe_agg = (df_pipe.groupby(["country", "region"])["cap_mw"].sum()
                .reset_index().rename(columns={"cap_mw": "pipeline_mw"}))

    merged = op_agg.merge(pipe_agg, on=["country", "region"], how="outer").fillna(0)
    merged["operating_gw"] = (merged["operating_mw"] / 1000).round(3)
    merged["pipeline_gw"]  = (merged["pipeline_mw"]  / 1000).round(3)
    merged["total_gw"]     = (merged["operating_gw"] + merged["pipeline_gw"]).round(3)

    return (merged[["country", "region", "operating_gw", "pipeline_gw", "total_gw"]]
            .sort_values("total_gw", ascending=False)
            .reset_index(drop=True))


# ── Technology projection ─────────────────────────────────────────────────

# Tech group classification — SMR checked first (cross-cuts all reactor types)
TECH_GROUPS = ["PWR", "BWR", "PHWR", "SMR", "Other"]
TECH_COLORS = {
    "PWR":   "#1f77b4",
    "BWR":   "#2ca02c",
    "PHWR":  "#ff7f0e",
    "SMR":   "#9467bd",
    "Other": "#7f7f7f",
}


def _classify_tech(reactor_type: str | None, is_smr: int | None) -> str:
    """Map a reactor's type + SMR flag to one of the five display groups."""
    if is_smr:
        return "SMR"
    t = (reactor_type or "").upper()
    if t == "PWR":
        return "PWR"
    if t == "BWR":
        return "BWR"
    if t == "PHWR":
        return "PHWR"
    return "Other"


def get_tech_projection(
    scenario_id: str = "base",
    regions: list | None = None,
    smr_post2040_share: float = 0.20,
    historical_start: int = 2005,
    smr_accel_start_year: int = 0,
    smr_accel_gw_per_year: float = 0.0,
    db_path: Path = DB_PATH,
) -> pd.DataFrame:
    """
    Return year-by-year capacity by technology group (PWR/BWR/PHWR/SMR/Other)
    for historical_start–2050.

    Historical (historical_start to BASELINE_YEAR-1): unit-level using actual
        commissioning/retirement dates. LTS reactors excluded in years before shutdown,
        included after restart.
    Bottom-up (BASELINE_YEAR–2040): unit-level with scenario retirement schedule + pipeline.
    Top-down (2041–2050): 2040 fleet composition carried forward; retirements unit-level;
        new additions split by smr_post2040_share + 2040 non-SMR proportions.

    If regions is provided, only reactors in those regions are included.

    Returns DataFrame: year, tech_group, capacity_gw, is_bottom_up
        is_bottom_up: 1=bottom-up/historical, 0=top-down
    """
    conn = _conn(db_path)
    try:
        retirement_schedule = build_retirement_schedule(conn, scenario_id)
        pipeline = build_pipeline(conn, scenario_id)
        transition_year = get_scenario_params_simple(conn, scenario_id)

        cur = conn.cursor()

        region_filter = ""
        params_fleet: list = []
        if regions:
            placeholders = ",".join("?" * len(regions))
            region_filter = f"AND region IN ({placeholders})"
            params_fleet = list(regions)

        # Current fleet (operating/LTS/restarted) with tech info + dates for historical
        cur.execute(f"""
            SELECT reactor_id, region, status,
                   reactor_type, is_smr,
                   COALESCE(restart_capacity_mw, net_capacity_mw, 0) as cap_mw,
                   restart_date,
                   long_term_shutdown_date,
                   CAST(SUBSTR(COALESCE(commercial_operation_date, first_grid_date), 1, 4)
                        AS INTEGER) as comm_year,
                   retirement_date_used
            FROM reactors
            WHERE status IN ('Operating', 'LongTermShutdown', 'Restarted')
            {region_filter}
        """, params_fleet)
        fleet = cur.fetchall()

        # Pipeline with tech info
        pipe_filtered = [
            r for r in pipeline
            if regions is None or r["region"] in regions
        ]

        # Historical reference totals for scaling (region-filtered if needed)
        if regions:
            # Sum historical_capacity for the requested regions
            placeholders2 = ",".join("?" * len(regions))
            cur.execute(f"""
                SELECT year, SUM(capacity_gw)
                FROM historical_capacity
                WHERE region IN ({placeholders2})
                GROUP BY year
            """, list(regions))
        else:
            cur.execute("""
                SELECT year, capacity_gw
                FROM historical_capacity
                WHERE region = 'Global'
            """)
        historical_totals: dict[int, float] = {row[0]: row[1] for row in cur.fetchall()}

        # Global additions from projections table for post-2040
        cur.execute("""
            SELECT year, additions_this_year_gw
            FROM projections
            WHERE scenario_id = ? AND region = 'Global'
            ORDER BY year
        """, (scenario_id,))
        global_additions = {row[0]: row[1] for row in cur.fetchall()}

    finally:
        conn.close()

    rows = []
    topdown_cap: dict[str, float] = {}

    for year in range(historical_start, PROJECTION_END_YEAR + 1):
        is_historical = year < BASELINE_YEAR
        is_top_down   = year > transition_year
        tech_cap: dict[str, float] = {g: 0.0 for g in TECH_GROUPS}

        if is_historical:
            # ── Historical: actual commissioning/retirement dates ──────────
            # NOTE: retirement_date_used is a planning field (base retirement before
            # life extensions) — do NOT use it to exclude reactors from historical
            # years.  The only true historical exclusion is long_term_shutdown_date.
            for (rid, region, status, rtype, is_smr, cap_mw,
                 restart_date, lts_date, comm_year, ret_date_used) in fleet:
                if comm_year is None or comm_year > year:
                    continue
                # LTS handling
                if status in ("LongTermShutdown", "Restarted"):
                    if lts_date:
                        lts_year = int(lts_date[:4])
                        rst_year = int(restart_date[:4]) if restart_date else None
                        if lts_year <= year:
                            if not rst_year or rst_year > year:
                                continue
                    else:
                        # NULL long_term_shutdown_date: we don't know when LTS started.
                        # Conservative: exclude LTS reactors from all historical years;
                        # Restarted with no lts_date are treated as operating.
                        if status == "LongTermShutdown":
                            continue
                group = _classify_tech(rtype, is_smr)
                tech_cap[group] += cap_mw / 1000.0

            # Scale to match actual observed total (corrects for retired reactors
            # absent from DB — e.g., German/UK/US units that shut down before 2024)
            hist_ref = historical_totals.get(year)
            if hist_ref:
                tech_sum = sum(tech_cap.values())
                if tech_sum > 0:
                    scale = hist_ref / tech_sum
                    for g in TECH_GROUPS:
                        tech_cap[g] = tech_cap[g] * scale

        elif not is_top_down:
            # ── Bottom-up: scenario retirement schedule + pipeline ─────────
            for (rid, region, status, rtype, is_smr, cap_mw,
                 restart_date, *_rest) in fleet:
                if status == "LongTermShutdown":
                    if not restart_date or int(restart_date[:4]) > year:
                        continue
                ret_year = retirement_schedule.get(rid)
                if ret_year is None or ret_year > year:
                    group = _classify_tech(rtype, is_smr)
                    tech_cap[group] += cap_mw / 1000.0
            for r in pipe_filtered:
                eff_yr = r.get("effective_online_year")
                if eff_yr and eff_yr <= year:
                    group = _classify_tech(r.get("reactor_type"), r.get("is_smr"))
                    tech_cap[group] += r["expected_capacity_mw"] / 1000.0
            # Mirror _apply_smr_acceleration: add cumulative pre-2040 SMR boost
            if smr_accel_gw_per_year > 0 and smr_accel_start_year > 0 and year >= smr_accel_start_year:
                smr_cumulative = smr_accel_gw_per_year * (year - smr_accel_start_year + 1)
                tech_cap["SMR"] += smr_cumulative
            if year == transition_year:
                topdown_cap = dict(tech_cap)

        else:
            # ── Top-down: carry forward + unit retirements + new build ────
            ret_by_tech: dict[str, float] = {g: 0.0 for g in TECH_GROUPS}
            for (rid, region, status, rtype, is_smr, cap_mw, *_rest) in fleet:
                if retirement_schedule.get(rid) == year:
                    group = _classify_tech(rtype, is_smr)
                    ret_by_tech[group] += cap_mw / 1000.0

            global_add  = global_additions.get(year, 0.0)
            smr_add     = global_add * smr_post2040_share
            non_smr_add = global_add * (1.0 - smr_post2040_share)
            non_smr_2040  = {g: topdown_cap.get(g, 0.0) for g in TECH_GROUPS if g != "SMR"}
            non_smr_total = sum(non_smr_2040.values())

            for group in TECH_GROUPS:
                add = smr_add if group == "SMR" else (
                    non_smr_add * (non_smr_2040.get(group, 0.0) / non_smr_total)
                    if non_smr_total > 0 else 0.0
                )
                topdown_cap[group] = max(
                    0.0, topdown_cap.get(group, 0.0) - ret_by_tech[group] + add
                )
            tech_cap = dict(topdown_cap)

        bu_flag = 0 if is_top_down else 1
        for group in TECH_GROUPS:
            rows.append({
                "year":         year,
                "tech_group":   group,
                "capacity_gw":  round(tech_cap[group], 3),
                "is_bottom_up": bu_flag,
            })

    return pd.DataFrame(rows)


def get_scenario_params_simple(conn, scenario_id: str) -> int:
    """Return transition_year for a scenario (lightweight helper)."""
    cur = conn.cursor()
    cur.execute("SELECT transition_year FROM scenarios WHERE scenario_id = ?", (scenario_id,))
    row = cur.fetchone()
    return int(row[0]) if row and row[0] else 2040


# ── Validation ────────────────────────────────────────────────────────────

def validate(db_path: Path = DB_PATH) -> dict[str, dict]:
    """Run validation suite and return results dict."""
    return run_validation(db_path)
