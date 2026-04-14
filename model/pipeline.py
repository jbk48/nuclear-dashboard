"""
pipeline.py — Pipeline probability weighting and expected online year logic.

For each UC/Planned/Proposed reactor, computes:
  - effective_probability: the realization rate (scenario-adjusted)
  - effective_online_year: expected online year + construction delay adder
  - expected_capacity_mw: net_capacity_mw × effective_probability
"""
import sqlite3
from config import BASELINE_YEAR


def build_pipeline(
    conn: sqlite3.Connection,
    scenario_id: str,
) -> list[dict]:
    """
    Returns a list of pipeline reactor dicts with effective_probability,
    effective_online_year, and expected_capacity_mw computed for the
    given scenario.
    """
    cur = conn.cursor()

    # Get scenario global rates and delay
    cur.execute("""
        SELECT pipeline_uc_realization_rate,
               pipeline_planned_realization_rate,
               pipeline_proposed_realization_rate,
               COALESCE(smr_uc_rate, smr_realization_rate_override),
               COALESCE(smr_planned_rate, smr_realization_rate_override),
               COALESCE(smr_proposed_rate, smr_realization_rate_override),
               construction_delay_adder_years
        FROM scenarios WHERE scenario_id = ?
    """, (scenario_id,))
    row = cur.fetchone()
    if not row:
        raise ValueError(f"Unknown scenario: {scenario_id}")
    uc_rate, planned_rate, proposed_rate, smr_uc_rate, smr_planned_rate, smr_proposed_rate, delay = row

    # Get pipeline reactors with regional overrides merged in
    cur.execute("""
        SELECT r.reactor_id, r.name, r.country, r.region, r.status,
               r.net_capacity_mw, r.expected_online_year,
               r.expected_online_year_source, r.construction_delay_years,
               r.is_smr, r.reactor_type,
               COALESCE(srp.pipeline_uc_realization_rate,       ?) as uc_r,
               COALESCE(srp.pipeline_planned_realization_rate,  ?) as pl_r,
               COALESCE(srp.pipeline_proposed_realization_rate, ?) as pr_r,
               COALESCE(srp.phase_out_by_year, NULL)            as phase_out
        FROM reactors r
        LEFT JOIN scenario_regional_params srp
            ON srp.scenario_id = ? AND srp.region = r.region
        WHERE r.status IN ('UnderConstruction', 'Planned', 'Proposed')
    """, (uc_rate, planned_rate, proposed_rate, scenario_id))

    pipeline = []
    for row in cur.fetchall():
        (rid, name, country, region, status, cap_mw, online_yr, online_src,
         unit_delay, smr_flag, reactor_type, uc_r, pl_r, pr_r, phase_out) = row

        # Determine base probability
        if status == "UnderConstruction":
            base_prob = uc_r
        elif status == "Planned":
            base_prob = pl_r
        else:
            base_prob = pr_r

        # SMR per-stage override
        if smr_flag:
            if status == "UnderConstruction" and smr_uc_rate is not None:
                base_prob = smr_uc_rate
            elif status == "Planned" and smr_planned_rate is not None:
                base_prob = smr_planned_rate
            elif status == "Proposed" and smr_proposed_rate is not None:
                base_prob = smr_proposed_rate

        # Effective online year with delay
        eff_delay = (delay or 0) + (unit_delay or 0)
        eff_year = (online_yr + round(eff_delay)) if online_yr else None

        # Stale UC estimates: if behind schedule or due in baseline year,
        # assume near-term completion starting the following year.
        # This ensures year-2024 is always a clean operating-fleet snapshot
        # regardless of which scenario's delay adder is applied.
        if eff_year and eff_year <= BASELINE_YEAR:
            eff_year = BASELINE_YEAR + 1

        # Phase-out check: if region is phased out before online_year, set prob=0
        if phase_out and eff_year and eff_year > phase_out:
            base_prob = 0.0

        if cap_mw and base_prob and eff_year:
            exp_cap_mw = round(cap_mw * base_prob, 1)
        else:
            exp_cap_mw = 0.0

        pipeline.append({
            "reactor_id":             rid,
            "name":                   name,
            "country":                country,
            "region":                 region,
            "status":                 status,
            "net_capacity_mw":        cap_mw,
            "reactor_type":           reactor_type,
            "effective_probability":  base_prob,
            "effective_online_year":  eff_year,
            "expected_capacity_mw":   exp_cap_mw,
            "is_smr":                 smr_flag,
        })
    return pipeline
