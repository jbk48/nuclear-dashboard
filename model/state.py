"""
state.py — Unified ProjectionState.

Build once per (scenario_id, what_if_overrides) pair via
build_projection_state().  Pass the result to get_tech_projection(),
run_projection(), and get_what_if_country_capacity() so those functions
never re-derive the same inputs or apply overrides independently.

Override application happens exactly once: inside _apply_overrides().
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from config import DB_PATH, BASELINE_YEAR
from model.retirement import build_retirement_schedule
from model.pipeline import build_pipeline


@dataclass
class ProjectionState:
    """
    All pre-computed inputs needed by any projection consumer.

    fleet               : list of reactor dicts with ALL fields needed by any
                          consumer (run_projection, get_tech_projection,
                          get_what_if_country_capacity).
                          status / capacity / restart_date overrides pre-applied.
    pipeline            : pipeline dicts; probability / date overrides pre-applied;
                          synthetic builds appended.
    retirement_schedule : {reactor_id → retirement_year}; overrides pre-applied.
    historical_all      : {region → {year → capacity_gw}} for tech-group scaling.
    global_additions    : {year → gw} from the projections table — post-2040 split.
    post2040_by_region  : {region → gw_per_year} for top-down projection phase.
    region_adders       : {(year, region) → gw} manual capacity adders.
    what_if_overrides   : stored for reference / display only.
    """
    scenario_id:         str
    scenario_params:     dict
    retirement_schedule: dict
    fleet:               list   # list[dict]
    pipeline:            list   # list[dict]
    historical_all:      dict   # dict[str, dict[int, float]]
    global_additions:    dict   # dict[int, float]
    post2040_by_region:  dict   # dict[str, float]
    region_adders:       dict   # dict[tuple, float]
    what_if_overrides:   dict | None = None


def build_projection_state(
    scenario_id: str,
    what_if_overrides: dict | None = None,
    db_path: Path = DB_PATH,
) -> ProjectionState:
    """
    Open DB once → read all inputs → close DB → apply overrides in-memory.

    This is the only entry point for constructing a ProjectionState.
    Downstream projection functions receive a ready-to-use state and never
    open the database or apply overrides themselves.
    """
    # Deferred import avoids the circular dependency:
    #   state.py  → projection.py (helpers)
    #   projection.py → state.py (ProjectionState type hint)
    from model.projection import (
        get_scenario_params,
        get_regional_post2040,
        get_region_adders,
    )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # ── Scenario parameters ────────────────────────────────────────────
        params = get_scenario_params(conn, scenario_id)

        # ── Retirement schedule ────────────────────────────────────────────
        retirement_schedule: dict[str, int] = build_retirement_schedule(
            conn, scenario_id
        )

        # ── Operating fleet — full field set for every consumer ───────────
        cur = conn.cursor()
        cur.execute("""
            SELECT reactor_id, country, region, status,
                   reactor_type, is_smr,
                   COALESCE(restart_capacity_mw, net_capacity_mw, 0) AS cap_mw,
                   restart_date,
                   long_term_shutdown_date                             AS lts_date,
                   CAST(SUBSTR(COALESCE(commercial_operation_date,
                                        first_grid_date), 1, 4)
                        AS INTEGER)                                    AS comm_year,
                   retirement_date_used
            FROM reactors
            WHERE status IN ('Operating', 'LongTermShutdown', 'Restarted')
        """)
        fleet: list[dict] = [dict(row) for row in cur.fetchall()]

        # ── Pipeline (already plain dicts from build_pipeline) ─────────────
        pipeline: list[dict] = build_pipeline(conn, scenario_id)

        # ── Historical capacity for all regions (tech-group scaling) ────────
        cur.execute("""
            SELECT region, year, capacity_gw
            FROM historical_capacity
            ORDER BY region, year
        """)
        historical_all: dict[str, dict[int, float]] = {}
        for row in cur.fetchall():
            historical_all.setdefault(row["region"], {})[row["year"]] = (
                row["capacity_gw"]
            )

        # ── Post-2040 parameters ───────────────────────────────────────────
        global_rate        = params.get("post2040_global_growth_gw_per_year") or 0.0
        post2040_by_region = get_regional_post2040(conn, scenario_id, global_rate)
        region_adders      = get_region_adders(conn, scenario_id)

        # ── Global additions for post-2040 tech split ─────────────────────
        cur.execute("""
            SELECT year, additions_this_year_gw
            FROM projections
            WHERE scenario_id = ? AND region = 'Global'
            ORDER BY year
        """, (scenario_id,))
        global_additions: dict[int, float] = {
            row[0]: row[1] for row in cur.fetchall()
        }

        # ── Fleet promotions: pull extra reactors while DB is open ─────────
        # (PermanentShutdown / other non-fleet reactors that gain fleet-level
        #  overrides like restart_date must be fetched before the connection closes.)
        if what_if_overrides:
            _FLEET_OVERRIDE_FIELDS = {"status", "capacity_mw", "restart_date"}
            reactor_ov = {
                k: v for k, v in what_if_overrides.items() if k != "__synthetic__"
            }
            fleet_ids = {r["reactor_id"] for r in fleet}
            extra_ids = [
                rid for rid in reactor_ov
                if rid not in fleet_ids
                and bool(_FLEET_OVERRIDE_FIELDS & reactor_ov[rid].keys())
            ]
            if extra_ids:
                placeholders = ",".join("?" * len(extra_ids))
                cur.execute(f"""
                    SELECT reactor_id, country, region, status,
                           reactor_type, is_smr,
                           COALESCE(restart_capacity_mw, net_capacity_mw, 0) AS cap_mw,
                           restart_date,
                           long_term_shutdown_date                             AS lts_date,
                           CAST(SUBSTR(COALESCE(commercial_operation_date,
                                                first_grid_date), 1, 4)
                                AS INTEGER)                                    AS comm_year,
                           retirement_date_used
                    FROM reactors
                    WHERE reactor_id IN ({placeholders})
                """, extra_ids)
                fleet.extend(dict(row) for row in cur.fetchall())

    finally:
        conn.close()

    # ── Apply all overrides in-memory (DB is now closed) ──────────────────
    if what_if_overrides:
        _apply_overrides(fleet, pipeline, retirement_schedule, what_if_overrides)

    return ProjectionState(
        scenario_id=scenario_id,
        scenario_params=params,
        retirement_schedule=retirement_schedule,
        fleet=fleet,
        pipeline=pipeline,
        historical_all=historical_all,
        global_additions=global_additions,
        post2040_by_region=post2040_by_region,
        region_adders=region_adders,
        what_if_overrides=what_if_overrides,
    )


# ── Override application ───────────────────────────────────────────────────

def _apply_overrides(
    fleet: list[dict],
    pipeline: list[dict],
    retirement_schedule: dict,
    overrides: dict,
) -> None:
    """
    Mutate fleet, pipeline, and retirement_schedule in-place to reflect
    what_if_overrides.

    This is the ONE place in the entire codebase where what-if overrides
    are applied to model inputs.  All other functions receive state that
    has already been through here.
    """
    reactor_ov = {k: v for k, v in overrides.items() if k != "__synthetic__"}

    # 1. Retirement year
    for rid, ov in reactor_ov.items():
        if "retirement_year" in ov:
            retirement_schedule[rid] = int(ov["retirement_year"])

    # 2. Fleet overrides: capacity_mw, status, restart_date
    for row in fleet:
        rid = row.get("reactor_id")
        if rid and rid in reactor_ov:
            ov = reactor_ov[rid]
            if "capacity_mw"  in ov:
                row["cap_mw"]      = float(ov["capacity_mw"])
            if "status"       in ov:
                row["status"]      = ov["status"]
            if "restart_date" in ov:
                row["restart_date"] = ov["restart_date"]

    # 3. Pipeline overrides: probability, online year
    for r in pipeline:
        rid = r.get("reactor_id")
        if rid and rid in reactor_ov:
            ov = reactor_ov[rid]
            if "expected_online_year" in ov:
                r["effective_online_year"] = int(ov["expected_online_year"])
            if "pipeline_probability" in ov:
                r["expected_capacity_mw"] = (
                    r.get("net_capacity_mw", 0) * float(ov["pipeline_probability"])
                )

    # 4. Synthetic new-build batches → append to pipeline
    if "__synthetic__" in overrides:
        for batch in overrides["__synthetic__"]:
            reg     = batch.get("region")
            cap_mw  = float(batch.get("capacity_mw", 0))
            per_yr  = int(batch.get("per_year", 1))
            start   = int(batch.get("start_year", BASELINE_YEAR + 1))
            n_years = int(batch.get("n_years", 1))
            if reg and cap_mw > 0:
                for yr_off in range(n_years):
                    pipeline.append({
                        "reactor_id":            f"__synth_{reg}_{start + yr_off}__",
                        "country":               reg,   # region as placeholder country
                        "region":                reg,
                        "reactor_type":          "PWR",
                        "is_smr":                0,
                        "effective_online_year":  start + yr_off,
                        "expected_capacity_mw":   cap_mw * per_yr,
                        "net_capacity_mw":        cap_mw * per_yr,
                    })
