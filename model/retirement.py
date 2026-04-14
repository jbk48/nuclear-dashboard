"""
retirement.py — Retirement date logic.

Given a scenario's extension_policy, adjusts retirement_date_used for
Tier 2 and Tier 3 reactors (Tier 1 declared dates are never changed).

Extension policies:
  AcceleratedRetirement → operate to original nominal design life only;
                          no new extensions beyond de-facto already grandfathered
  CurrentPolicy         → operate to currently approved license / stated national
                          policy; per-reactor overrides where known, then
                          country-specific probabilities for the remainder
  ExtendedOperations    → maximum regulatory lifetime per country
                          (US: 80yr, Japan: 80yr GX legislation, France: 60yr, etc.)

Per-reactor overrides (reactor_retirement_overrides table) take precedence
over all probabilistic logic for reactors where the decision is explicitly known.

"Eligible" means the reactor has not yet reached its extension limit
(i.e., current retirement year + extension_unit_years ≤ comm_year + max_possible_life_years).

─── De facto extension handling ───────────────────────────────────────────────
Many reactors currently operating in 2024 have a Tier 2 date in the past (e.g.,
a French reactor built 1970 has a 50yr nominal life → ret_year = 2020).  The
fact that they are still in the operating fleet proves they have already received
at least one extension in practice.

Before applying the scenario's extension policy, we therefore advance the
nominal Tier 2 date by successive extension increments until it clears
BASELINE_YEAR.  This "effective baseline retirement" is used as the input to
get_extended_retirement() rather than the raw Tier 2 date.  The scenario policy
then decides whether to grant one FURTHER increment on top of this.

Example (France 1970, "AcceleratedRetirement" scenario):
  raw Tier 2 = 2020  →  effective baseline = 2030 (one 10yr increment applied)
  "AcceleratedRetirement" policy adds nothing  →  scheduled retirement = 2030

Example (France 1970, "CurrentPolicy" scenario):
  effective baseline = 2030
  France fraction = 65%: hash-based roll → yes: retirement = 2040, no: retirement = 2030
"""
import sqlite3
import hashlib
from config import BASELINE_YEAR
from model.scenarios import EXTENSION_POLICY_FRACTIONS


def year_from_date(d: str | None) -> int | None:
    if d and len(d) >= 4:
        try:
            return int(d[:4])
        except ValueError:
            return None
    return None


def deterministic_extend(reactor_id: str, fraction: float) -> bool:
    """
    Determine whether a given reactor receives an extension.
    Uses a deterministic hash of the reactor_id so results are reproducible
    without storing per-reactor flags. The same reactor always gets the same
    answer for a given fraction.
    """
    h = int(hashlib.md5(reactor_id.encode()).hexdigest(), 16)
    return (h % 1000) / 1000.0 < fraction


def advance_past_baseline(
    ret_year: int,
    comm_year: int,
    max_life: int,
    ext_unit: int,
) -> int:
    """
    Advance ret_year by successive extension increments until it exceeds
    BASELINE_YEAR, bounded by comm_year + max_life.

    This represents the de facto extensions already received by a reactor
    that is still operating past its nominal Tier 2 date.

    If even the maximum life is ≤ BASELINE_YEAR (very old reactor), returns
    BASELINE_YEAR + 1 as a hard floor (they're demonstrably still alive).
    """
    if ret_year > BASELINE_YEAR:
        return ret_year  # already in the future — nothing to do

    adjusted = ret_year
    while adjusted <= BASELINE_YEAR:
        next_yr = min(adjusted + ext_unit, comm_year + max_life)
        if next_yr <= adjusted:
            # Stuck at max life and still in the past — hard floor
            return BASELINE_YEAR + 1
        adjusted = next_yr

    return adjusted


def get_extended_retirement(
    reactor_id: str,
    comm_op_year: int,
    current_ret_year: int,
    retirement_tier: int,
    country: str,
    extension_policy: str,
    conn: sqlite3.Connection,
) -> int:
    """
    Return the (possibly extended) retirement year for a single reactor.

    current_ret_year should already be the effective baseline (i.e., past
    de facto extensions have been applied via advance_past_baseline).
    Tier 1 dates are returned unchanged.
    """
    if retirement_tier == 1:
        return current_ret_year  # declared — never adjust

    # Look up country rules
    cur = conn.cursor()
    cur.execute("""
        SELECT total_assumed_life_years, max_possible_life_years, extension_unit_years
        FROM retirement_rules WHERE country = ?
    """, (country.upper(),))
    row = cur.fetchone()
    if row:
        total_assumed, max_life, ext_unit = row
    else:
        total_assumed, max_life, ext_unit = 40, 60, 20

    if extension_policy == "AcceleratedRetirement":
        return current_ret_year  # no new extensions beyond de-facto baseline

    if extension_policy == "ExtendedOperations":
        new_ret_year = comm_op_year + max_life
        return max(current_ret_year, new_ret_year)

    # CurrentPolicy: country-specific probability (deterministic per reactor)
    country_fractions = EXTENSION_POLICY_FRACTIONS.get("CurrentPolicy", {})
    fraction = country_fractions.get(
        (country or "").upper(),
        country_fractions.get("DEFAULT", 0.55)
    )
    if not deterministic_extend(reactor_id, fraction):
        return current_ret_year

    # Reactor gets one additional extension increment, bounded by max_life
    new_ret_year = min(current_ret_year + ext_unit, comm_op_year + max_life)
    return new_ret_year


def build_retirement_schedule(
    conn: sqlite3.Connection,
    scenario_id: str,
) -> dict[str, int]:
    """
    Build a {reactor_id: retirement_year} mapping for all operating/LTS reactors
    under the given scenario's extension policy.

    Returns the adjusted retirement years (does NOT write back to DB;
    scenario-specific retirements are computed at projection time).
    """
    cur = conn.cursor()

    # Get scenario extension policy
    cur.execute("""
        SELECT extension_policy_global FROM scenarios WHERE scenario_id = ?
    """, (scenario_id,))
    row = cur.fetchone()
    if not row:
        raise ValueError(f"Unknown scenario: {scenario_id}")
    global_policy = row[0]

    # Cache all country retirement rules for efficient lookup
    cur.execute("""
        SELECT country, max_possible_life_years, extension_unit_years
        FROM retirement_rules
    """)
    country_rules: dict[str, tuple[int, int]] = {
        r[0].upper(): (r[1], r[2]) for r in cur.fetchall()
    }
    DEFAULT_RULES = (60, 20)  # max_life, ext_unit fallback

    # Get operating + LTS reactors
    cur.execute("""
        SELECT r.reactor_id, r.country, r.region,
               r.commercial_operation_date,
               r.retirement_date_used,
               r.retirement_tier,
               COALESCE(srp.extension_policy, ?) as effective_policy
        FROM reactors r
        LEFT JOIN scenario_regional_params srp
            ON srp.scenario_id = ? AND srp.region = r.region
        WHERE r.status IN ('Operating', 'LongTermShutdown', 'Restarted')
    """, (global_policy, scenario_id))

    rows = cur.fetchall()
    schedule: dict[str, int] = {}

    for (rid, country, region, comm_op, ret_date, tier, policy) in rows:
        # ── Step 0: per-reactor override table (explicit per-scenario decisions) ──
        # Takes precedence over all probabilistic logic.
        try:
            cur.execute("""
                SELECT retirement_year FROM reactor_retirement_overrides
                WHERE reactor_id = ? AND scenario_policy = ?
            """, (rid, policy))
            override_row = cur.fetchone()
            if override_row:
                schedule[rid] = override_row[0]
                continue
        except Exception:
            pass  # table may not exist in older DB versions

        comm_year = year_from_date(comm_op)
        ret_year  = year_from_date(ret_date)

        if ret_year is None:
            # No retirement date computable — assume very long life (2080)
            schedule[rid] = 2080
            continue

        if comm_year is None:
            # No COD known; can't advance — use raw date or BASELINE_YEAR + 1
            schedule[rid] = max(ret_year, BASELINE_YEAR + 1)
            continue

        # ── Step 1: advance past BASELINE_YEAR (de facto extensions) ────────
        # For Tier 1 (declared) dates, skip advancement — they're future dates.
        if (tier or 3) != 1 and ret_year <= BASELINE_YEAR:
            max_life, ext_unit = country_rules.get(
                (country or "").upper(), DEFAULT_RULES
            )
            effective_ret_year = advance_past_baseline(
                ret_year, comm_year, max_life, ext_unit
            )
        else:
            effective_ret_year = ret_year

        # ── Step 2: apply scenario extension policy ──────────────────────────
        adj_ret = get_extended_retirement(
            reactor_id=rid,
            comm_op_year=comm_year,
            current_ret_year=effective_ret_year,
            retirement_tier=tier or 3,
            country=country or "",
            extension_policy=policy or "None",
            conn=conn,
        )

        # ── Step 3: final safety floor ───────────────────────────────────────
        # Should not be needed after step 1, but guard against edge cases
        # (e.g., Tier 1 declared dates in the past, Tier 3 with no COD).
        if adj_ret <= BASELINE_YEAR:
            adj_ret = BASELINE_YEAR + 1

        schedule[rid] = adj_ret

    return schedule
