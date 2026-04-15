"""
projection.py — Core annual capacity projection engine.

Implements the hybrid bottom-up (2024–2040) / top-down (2040–2050) model.

Bottom-up formula (per year, per region):
  capacity(year) = Σ[operating units not yet retired by year-end]
                 + Σ[pipeline units × probability with online_year ≤ year]
                 + Σ[region_adders for year]

Top-down (post-transition_year):
  capacity(year) = capacity(year-1)
                 - retirements_from_existing_fleet(year)   ← still unit-level
                 + post2040_new_build(year)                ← scenario parameter
                 + region_adders(year)

Continuity condition enforced: capacity_top_down(transition_year) ≡
    capacity_bottom_up(transition_year)  [guaranteed by design]
"""
import sqlite3
from collections import defaultdict
from config import BASELINE_YEAR, PROJECTION_END_YEAR, REGIONS
from model.retirement import build_retirement_schedule
from model.pipeline import build_pipeline


def get_scenario_params(conn: sqlite3.Connection, scenario_id: str) -> dict:
    cur = conn.cursor()
    cur.execute("SELECT * FROM scenarios WHERE scenario_id = ?", (scenario_id,))
    cols = [d[0] for d in cur.description]
    row  = cur.fetchone()
    if not row:
        raise ValueError(f"Unknown scenario: {scenario_id}")
    return dict(zip(cols, row))


def get_regional_post2040(conn: sqlite3.Connection, scenario_id: str,
                           global_gw_yr: float) -> dict[str, float]:
    """Return {region: gw_per_year} for post-2040 growth."""
    cur = conn.cursor()
    cur.execute("""
        SELECT region, post2040_growth_gw_per_year
        FROM scenario_regional_params
        WHERE scenario_id = ? AND post2040_growth_gw_per_year IS NOT NULL
    """, (scenario_id,))
    regional = {row[0]: row[1] for row in cur.fetchall()}

    if not regional:
        # Fallback: use base-case regional proportions scaled to this scenario's global rate.
        # This preserves realistic geographic distribution rather than an even split.
        cur.execute("""
            SELECT region, post2040_growth_gw_per_year
            FROM scenario_regional_params
            WHERE scenario_id = 'base' AND post2040_growth_gw_per_year IS NOT NULL
        """)
        base_rows = {row[0]: max(0.0, row[1] or 0.0) for row in cur.fetchall()}
        base_total = sum(base_rows.values())
        if base_total > 0:
            regional = {r: global_gw_yr * (base_rows.get(r, 0.0) / base_total)
                        for r in REGIONS}
        else:
            # Last-resort even split
            per_region = global_gw_yr / len(REGIONS)
            regional = {r: per_region for r in REGIONS}
    else:
        # Fill in missing regions with 0
        for r in REGIONS:
            if r not in regional:
                regional[r] = 0.0
    return regional


def get_region_adders(conn: sqlite3.Connection, scenario_id: str) -> dict[tuple, float]:
    """Return {(year, region): gw} for manual adders."""
    cur = conn.cursor()
    cur.execute("""
        SELECT year, region, SUM(capacity_gw) FROM region_adders
        WHERE scenario_id = ? GROUP BY year, region
    """, (scenario_id,))
    return {(yr, reg): gw for yr, reg, gw in cur.fetchall()}


def run_projection(
    conn: sqlite3.Connection,
    scenario_id: str,
    start_year: int = BASELINE_YEAR,
    end_year: int = PROJECTION_END_YEAR,
    what_if_overrides: dict | None = None,
    write_to_db: bool = True,
) -> list[dict]:
    """
    Compute annual capacity projection for the given scenario.
    Returns a list of row dicts matching the projections table schema.
    Also writes results to the projections table.
    """
    params = get_scenario_params(conn, scenario_id)
    transition_year = params.get("transition_year") or 2040

    # ── Build inputs ─────────────────────────────────────────────────────
    retirement_schedule = build_retirement_schedule(conn, scenario_id)   # {rid: ret_year}
    pipeline            = build_pipeline(conn, scenario_id)              # list of dicts
    adders              = get_region_adders(conn, scenario_id)           # {(yr,reg): gw}

    post2040_by_region = get_regional_post2040(
        conn, scenario_id, params["post2040_global_growth_gw_per_year"]
    )

    # ── Load operating fleet with capacities ─────────────────────────────
    cur = conn.cursor()
    cur.execute("""
        SELECT reactor_id, region, status,
               COALESCE(restart_capacity_mw, net_capacity_mw, 0) as cap_mw,
               restart_date
        FROM reactors
        WHERE status IN ('Operating', 'LongTermShutdown', 'Restarted')
    """)
    operating_fleet = list(cur.fetchall())

    # ── Apply what-if overrides (in-memory only, DB untouched) ───────────
    if what_if_overrides:
        reactor_overrides = {k: v for k, v in what_if_overrides.items()
                             if k != "__synthetic__"}

        # Retirement year overrides feed directly into the schedule
        for rid, ov in reactor_overrides.items():
            if "retirement_year" in ov:
                retirement_schedule[rid] = ov["retirement_year"]

        # Load any overridden reactors not currently in the operating fleet
        # (e.g. PermanentShutdown units being restarted in the what-if)
        fleet_ids = {r[0] for r in operating_fleet}
        extra_ids = [rid for rid in reactor_overrides if rid not in fleet_ids]
        if extra_ids:
            placeholders = ",".join("?" * len(extra_ids))
            cur.execute(f"""
                SELECT reactor_id, region, status,
                       COALESCE(restart_capacity_mw, net_capacity_mw, 0) as cap_mw,
                       restart_date
                FROM reactors WHERE reactor_id IN ({placeholders})
            """, extra_ids)
            operating_fleet.extend(cur.fetchall())

        # Fleet overrides: status / capacity / restart_date
        new_fleet = []
        for (rid, reg, status, cap_mw, restart_date) in operating_fleet:
            if rid in reactor_overrides:
                ov = reactor_overrides[rid]
                status       = ov.get("status",       status)
                cap_mw       = ov.get("capacity_mw",  cap_mw)
                restart_date = ov.get("restart_date", restart_date)
            new_fleet.append((rid, reg, status, cap_mw, restart_date))
        operating_fleet = new_fleet

        # Pipeline overrides: adjust effective_online_year or probability
        if any("expected_online_year" in v or "pipeline_probability" in v
               for v in reactor_overrides.values()):
            new_pipeline = []
            for r in pipeline:
                rid = r.get("reactor_id")
                if rid and rid in reactor_overrides:
                    ov = reactor_overrides[rid]
                    r = dict(r)
                    if "expected_online_year" in ov:
                        r["effective_online_year"] = int(ov["expected_online_year"])
                    if "pipeline_probability" in ov:
                        r["expected_capacity_mw"] = (
                            r.get("net_capacity_mw", 0) * float(ov["pipeline_probability"])
                        )
                new_pipeline.append(r)
            pipeline = new_pipeline

    # Group pipeline by (region, online_year) — for per-year reporting
    pipeline_by_region_year: dict[tuple, float] = defaultdict(float)
    for r in pipeline:
        yr  = r["effective_online_year"]
        reg = r["region"]
        mw  = r["expected_capacity_mw"]
        if yr and reg and mw:
            pipeline_by_region_year[(reg, yr)] += mw

    # ── Inject synthetic new-build batches ────────────────────────────────
    if what_if_overrides and "__synthetic__" in what_if_overrides:
        for batch in what_if_overrides["__synthetic__"]:
            reg      = batch.get("region")
            cap_mw   = float(batch.get("capacity_mw", 0))
            per_year = int(batch.get("per_year", 1))
            start    = int(batch.get("start_year", BASELINE_YEAR + 1))
            n_years  = int(batch.get("n_years", 1))
            if reg and cap_mw > 0:
                for yr_offset in range(n_years):
                    pipeline_by_region_year[(reg, start + yr_offset)] += cap_mw * per_year

    # Precompute cumulative pipeline MW per region — for every (region, year) pair
    # we want the total expected capacity that has come online *through* that year.
    # A reactor commissioned in 2025 is still contributing capacity in 2030, 2035 …
    # so we must accumulate, not just take the current-year slice.
    all_pipeline_years = sorted(set(yr for (reg, yr) in pipeline_by_region_year
                                    if yr is not None))
    # Build {region: {year: cumulative_mw}} prefix sums
    cumulative_pipeline_by_region: dict[str, dict[int, float]] = {}
    for reg in set(r["region"] for r in pipeline if r.get("region")):
        running = 0.0
        cumulative_pipeline_by_region[reg] = {}
        for yr in all_pipeline_years:
            running += pipeline_by_region_year.get((reg, yr), 0.0)
            cumulative_pipeline_by_region[reg][yr] = running

    def _cum_pipeline(region: str, year: int) -> float:
        """Return total expected pipeline MW online in `region` through `year`."""
        d = cumulative_pipeline_by_region.get(region)
        if not d:
            return 0.0
        # Find the largest key ≤ year in the sorted dict
        result = 0.0
        for k, v in d.items():
            if k <= year:
                result = v   # running total — take the latest (largest key ≤ year)
        return result

    # ── Projection loop ───────────────────────────────────────────────────
    results: list[dict] = []

    # Track cumulative retirements and additions per region
    cumulative_retired: dict[str, float] = defaultdict(float)
    cumulative_added:   dict[str, float] = defaultdict(float)

    # Top-down carry-over capacity per region (starts at bottom-up 2040 value)
    topdown_capacity: dict[str, float] = {}

    for year in range(start_year, end_year + 1):
        is_bottom_up = 1 if year <= transition_year else 0

        # ── Per-region calculation ────────────────────────────────────────
        for region in REGIONS + ["Global"]:
            if region == "Global":
                # Aggregate from all sub-regions computed this year
                this_year = [r for r in results if r["year"] == year and r["region"] != "Global"]
                cap_gw = sum(r["capacity_operating_gw"]    for r in this_year)
                ret_gw = sum(r["retirements_this_year_gw"] for r in this_year)
                add_gw = sum(r["additions_this_year_gw"]   for r in this_year)
                adr_gw = sum(r["adders_this_year_gw"]      for r in this_year)
                # Update Global cumulative trackers (mirrors what sub-regions do)
                cumulative_retired["Global"] = round(
                    cumulative_retired.get("Global", 0) + ret_gw, 3)
                cumulative_added["Global"] = round(
                    cumulative_added.get("Global", 0) + add_gw + adr_gw, 3)
                results.append({
                    "scenario_id":              scenario_id,
                    "year":                     year,
                    "region":                   "Global",
                    "capacity_operating_gw":    round(cap_gw, 3),
                    "capacity_retired_ytd_gw":  cumulative_retired["Global"],
                    "capacity_added_ytd_gw":    cumulative_added["Global"],
                    "retirements_this_year_gw": round(ret_gw, 3),
                    "additions_this_year_gw":   round(add_gw, 3),
                    "adders_this_year_gw":      round(adr_gw, 3),
                    "is_bottom_up":             is_bottom_up,
                })
                continue

            if is_bottom_up:
                # ── Bottom-up ─────────────────────────────────────────────
                # Operating capacity = sum of reactors not yet retired
                op_mw = 0.0
                ret_mw_this_year = 0.0
                for (rid, reg, status, cap_mw, restart_date) in operating_fleet:
                    if reg != region:
                        continue
                    # LTS reactors are dark unless they have a restart_date ≤ current year
                    if status == "LongTermShutdown":
                        if not restart_date:
                            continue
                        if int(restart_date[:4]) > year:
                            continue
                    ret_year = retirement_schedule.get(rid)
                    if ret_year is None or ret_year > year:
                        op_mw += cap_mw
                    elif ret_year == year:
                        # Retires this year — count in retirements; not in operating
                        ret_mw_this_year += cap_mw

                # Pipeline: capacity that has come online in this region through year
                # (cumulative — reactors commissioned in prior years are still running)
                cum_pipe_mw      = _cum_pipeline(region, year)
                add_mw_this_year = pipeline_by_region_year.get((region, year), 0.0)

                # Manual adders
                adr_mw = adders.get((year, region), 0.0) * 1000  # convert GW→MW

                total_cap_gw = round((op_mw + cum_pipe_mw + adr_mw) / 1000.0, 3)
                ret_gw  = round(ret_mw_this_year / 1000.0, 3)
                add_gw  = round(add_mw_this_year / 1000.0, 3)
                adr_gw  = round(adr_mw / 1000.0, 3)

                cumulative_retired[region] = round(
                    cumulative_retired.get(region, 0) + ret_gw, 3)
                cumulative_added[region]   = round(
                    cumulative_added.get(region, 0) + add_gw + adr_gw, 3)

                # Anchor top-down starting point at transition year
                if year == transition_year:
                    topdown_capacity[region] = total_cap_gw

            else:
                # ── Top-down ──────────────────────────────────────────────
                # Continuity: previous year's capacity is the starting point
                prev = topdown_capacity.get(region, 0.0)

                # Unit-level retirements continue from retirement schedule
                ret_mw_this_year = 0.0
                for (rid, reg, _status, cap_mw, _restart) in operating_fleet:
                    if reg == region and retirement_schedule.get(rid) == year:
                        ret_mw_this_year += cap_mw
                ret_gw = round(ret_mw_this_year / 1000.0, 3)

                # New build from scenario parameter
                add_gw = round(post2040_by_region.get(region, 0.0), 3)

                # Manual adders
                adr_gw = round(adders.get((year, region), 0.0), 3)

                total_cap_gw = round(max(prev - ret_gw + add_gw + adr_gw, 0), 3)
                topdown_capacity[region] = total_cap_gw

                cumulative_retired[region] = round(
                    cumulative_retired.get(region, 0) + ret_gw, 3)
                cumulative_added[region]   = round(
                    cumulative_added.get(region, 0) + add_gw + adr_gw, 3)

            results.append({
                "scenario_id":              scenario_id,
                "year":                     year,
                "region":                   region,
                "capacity_operating_gw":    total_cap_gw,
                "capacity_retired_ytd_gw":  cumulative_retired.get(region, 0),
                "capacity_added_ytd_gw":    cumulative_added.get(region, 0),
                "retirements_this_year_gw": ret_gw,
                "additions_this_year_gw":   add_gw,
                "adders_this_year_gw":      adr_gw,
                "is_bottom_up":             is_bottom_up,
            })

    # ── Write to DB (skipped for what-if runs) ────────────────────────────
    if write_to_db:
        cur.executemany("""
            INSERT OR REPLACE INTO projections
            (scenario_id, year, region, capacity_operating_gw,
             capacity_retired_ytd_gw, capacity_added_ytd_gw,
             retirements_this_year_gw, additions_this_year_gw,
             adders_this_year_gw, is_bottom_up)
            VALUES (:scenario_id, :year, :region, :capacity_operating_gw,
                    :capacity_retired_ytd_gw, :capacity_added_ytd_gw,
                    :retirements_this_year_gw, :additions_this_year_gw,
                    :adders_this_year_gw, :is_bottom_up)
        """, results)
        conn.commit()
    return results
