"""
scenarios.py — Pre-defined scenario presets and scenario management.

The four built-in scenarios correspond to the spec:
  decline      → Accelerated retirement (no extensions beyond nominal life),
                 high pipeline uncertainty.
                 Anchors to IEA Low Nuclear Case (~250 GW 2050).
  conservative → Current policy (reactors operate to currently stated/approved
                 lifetimes, country-specific extension rates).
                 Anchors to IAEA Low Case (~561 GW 2050).
  base         → Current policy extensions, default pipeline rates, robust growth.
                 Anchors to IEA STEPS (~647 GW 2050).
  optimistic   → Extended operations (maximum regulatory lifetime per country),
                 aggressive pipeline, accelerated growth.
                 Anchors to IAEA High Case (~992 GW 2050).

─── Extension policy framework ─────────────────────────────────────────────────
Three policy tiers replace the old probabilistic fractions:

  AcceleratedRetirement — Operate to original nominal design life only.
                          Known phase-outs (Belgium, UK AGRs) applied as facts.
  CurrentPolicy         — Operate to currently approved license / stated national
                          policy. Per-reactor overrides where known; country-specific
                          probabilities for the remainder.
  ExtendedOperations    — Operate to maximum regulatory lifetime per country.
                          US: 80yr (NRC SLR), Japan: 80yr (GX Decarbonization),
                          France: 60yr (ASN pathway), Russia: 70yr.

─── Post-2040 growth rate calibration ─────────────────────────────────────────
Rates are calibrated so each scenario reaches its 2050 benchmark:

  Scenario     2050 target   Post-2040 gross rate
  decline        ~250 GW          ~10 GW/yr
  conservative   ~561 GW          ~35 GW/yr
  base           ~647 GW          ~30 GW/yr
  optimistic     ~992 GW          ~57 GW/yr
"""
import sqlite3
from datetime import date
from config import DB_PATH, REGIONS

# ── Extension policy → per-country probability of extension ───────────────────
# Used by retirement.py for reactors NOT in reactor_retirement_overrides.
# AcceleratedRetirement (0.0) and ExtendedOperations (1.0) are handled directly
# in get_extended_retirement(); only CurrentPolicy uses this dict.
EXTENSION_POLICY_FRACTIONS = {
    "CurrentPolicy": {
        "UNITED STATES":          0.95,  # NRC approves nearly every renewal application
        "UNITED STATES OF AMERICA": 0.95,
        "FRANCE":                 0.65,  # Grand Carénage; ASN 50-yr reviews underway
        "RUSSIA":                 0.85,  # Rosatom standard practice
        "SOUTH KOREA":            0.70,  # NRC-K approves but political pressure on older units
        "KOREA, REPUBLIC OF":     0.70,
        "CHINA":                  0.85,  # Government support, young fleet
        "CANADA":                 0.80,  # OPG/NB Power refurbishment programmes
        "UKRAINE":                0.70,
        "INDIA":                  0.65,  # AERB renewal practice; execution constraints
        "FINLAND":                0.80,
        "SWEDEN":                 0.70,
        "UNITED KINGDOM":         0.25,  # AGR fleet retiring; only new PWRs extending
        "BELGIUM":                0.40,  # Only Doel-4/Tihange-3 extended; 3 units closing 2025
        "JAPAN":                  0.40,  # Restart recovery underway; local consent barriers
        "DEFAULT":                0.55,
    },
}

# ── Built-in scenarios ─────────────────────────────────────────────────────
PRESET_SCENARIOS = [
    {
        "scenario_id":                        "decline",
        "name":                               "Decline",
        "description":                        (
            "Reactors operate to original nominal design life only (Accelerated "
            "Retirement). No new extensions beyond already-approved Tier-1 dates. "
            "High pipeline uncertainty, significant construction delays. "
            "Minimal new build post-2040. Fleet attrition accelerates. "
            "Anchors to IEA Low Nuclear Case (~250 GW by 2050)."
        ),
        "extension_policy_global":            "AcceleratedRetirement",
        "pipeline_uc_realization_rate":       0.90,
        "pipeline_planned_realization_rate":  0.40,
        "pipeline_proposed_realization_rate": 0.05,
        "smr_realization_rate_override":      None,
        "construction_delay_adder_years":     3.0,
        "post2040_new_build_is_regional":     0,
        "post2040_global_growth_gw_per_year": 10.49,
        "transition_year":                    2040,
        "is_user_defined":                    0,
    },
    {
        "scenario_id":                        "conservative",
        "name":                               "Conservative",
        "description":                        (
            "Reactors operate to currently approved licenses and stated national "
            "policies (Current Policy). Country-specific extension rates reflect "
            "actual regulatory track records. Cautious pipeline realization. "
            "Modest post-2040 new build. "
            "Anchors to IAEA Low Case (~561 GW by 2050)."
        ),
        "extension_policy_global":            "CurrentPolicy",
        "pipeline_uc_realization_rate":       0.92,
        "pipeline_planned_realization_rate":  0.55,
        "pipeline_proposed_realization_rate": 0.15,
        "smr_realization_rate_override":      None,
        "construction_delay_adder_years":     2.0,
        "post2040_new_build_is_regional":     0,
        "post2040_global_growth_gw_per_year": 29.14,
        "transition_year":                    2040,
        "is_user_defined":                    0,
    },
    {
        "scenario_id":                        "base",
        "name":                               "Base",
        "description":                        (
            "Reactors operate to currently approved licenses and stated national "
            "policies (Current Policy). Default pipeline realization rates. "
            "Steady post-2040 new build reflecting current policy momentum, "
            "consistent with stated government targets. "
            "Anchors to IEA STEPS (~647 GW by 2050)."
        ),
        "extension_policy_global":            "CurrentPolicy",
        "pipeline_uc_realization_rate":       1.0,
        "pipeline_planned_realization_rate":  1.0,
        "pipeline_proposed_realization_rate": 0.0,
        "smr_realization_rate_override":      None,
        "construction_delay_adder_years":     0.0,
        "post2040_new_build_is_regional":     0,
        "post2040_global_growth_gw_per_year": 29.67,
        "transition_year":                    2040,
        "is_user_defined":                    0,
    },
    {
        "scenario_id":                        "optimistic",
        "name":                               "Optimistic",
        "description":                        (
            "All eligible reactors receive maximum life extension permitted under "
            "current national law (Extended Operations): US to 80yr, Japan to 80yr "
            "(GX Decarbonization legislation), France to 60yr (ASN pathway), "
            "Russia to 70yr. High pipeline realization. Accelerated post-2040 "
            "new build reflecting a sustained nuclear renaissance. "
            "Anchors to IAEA High Case (~992 GW by 2050)."
        ),
        "extension_policy_global":            "ExtendedOperations",
        "pipeline_uc_realization_rate":       0.95,
        "pipeline_planned_realization_rate":  0.85,
        "pipeline_proposed_realization_rate": 0.50,
        "smr_realization_rate_override":      None,
        "construction_delay_adder_years":     0.0,
        "post2040_new_build_is_regional":     0,
        "post2040_global_growth_gw_per_year": 55.62,
        "transition_year":                    2040,
        "is_user_defined":                    0,
    },
]

# ── Regional post-2040 growth rates for Base scenario (GW/yr by region) ──
# Calibrated to reach IEA STEPS ~647 GW at 2050.
# These are overridden by values in scenario_regional_params where set.
BASE_REGIONAL_POST2040 = {
    "United States":            5.1665,
    "Canada & Mexico":          1.0333,
    "France":                   1.0333,
    "United Kingdom":           1.0333,
    "Rest of Western Europe":   0.6889,
    "Eastern Europe":           1.3777,
    "Russia":                   3.4443,
    "China":                    8.6108,
    "South Asia":               4.1332,
    "East Asia":                1.3777,
    "Southeast Asia":           0.6889,
    "Emerging & Rest":          1.7222,
}


def load_scenarios(conn: sqlite3.Connection) -> None:
    """Insert all preset scenarios into the DB."""
    cur = conn.cursor()
    fields = [
        "scenario_id", "name", "description", "extension_policy_global",
        "pipeline_uc_realization_rate", "pipeline_planned_realization_rate",
        "pipeline_proposed_realization_rate", "smr_realization_rate_override",
        "construction_delay_adder_years", "post2040_new_build_is_regional",
        "post2040_global_growth_gw_per_year", "transition_year", "is_user_defined",
    ]
    placeholders = ",".join("?" for _ in fields)
    sql = f"INSERT OR REPLACE INTO scenarios ({','.join(fields)}) VALUES ({placeholders})"
    for sc in PRESET_SCENARIOS:
        cur.execute(sql, tuple(sc[f] for f in fields))

    # Insert regional params for base scenario (post-2040 growth rates)
    for region, rate in BASE_REGIONAL_POST2040.items():
        cur.execute("""
            INSERT OR REPLACE INTO scenario_regional_params
            (scenario_id, region, post2040_growth_gw_per_year)
            VALUES (?, ?, ?)
        """, ("base", region, rate))

    # US extension policy override for Base scenario:
    # The reactor_retirement_overrides table handles per-reactor US decisions.
    # ExtendedOperations set here as regional fallback for any US reactor
    # not in the override table (e.g., future additions).
    cur.execute("""
        UPDATE scenario_regional_params
        SET extension_policy     = 'ExtendedOperations',
            pipeline_rate_source = 'NRC licence renewal data: virtually all US plants at 60yr; SLR pathway to 80yr'
        WHERE scenario_id = 'base' AND region = 'United States'
    """)

    conn.commit()
    print(f"  ✓ Loaded {len(PRESET_SCENARIOS)} scenario presets")
