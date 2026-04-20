"""
test_retirement.py — Unit tests for model/retirement.py

Covers:
  - advance_past_baseline   (pure function, no DB)
  - deterministic_extend    (pure function, no DB)
  - get_extended_retirement (needs DB for country rules)
  - build_retirement_schedule (integration, uses DB fixture)
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import BASELINE_YEAR
from model.retirement import (
    advance_past_baseline,
    deterministic_extend,
    get_extended_retirement,
    build_retirement_schedule,
)


# ── advance_past_baseline ─────────────────────────────────────────────────────

class TestAdvancePastBaseline:

    def test_future_date_unchanged(self):
        """A retirement year already in the future is returned as-is."""
        result = advance_past_baseline(
            ret_year=2035, comm_year=1990, max_life=80, ext_unit=20
        )
        assert result == 2035

    def test_exactly_baseline_advances(self):
        """A retirement year exactly at BASELINE_YEAR must be advanced."""
        result = advance_past_baseline(
            ret_year=BASELINE_YEAR, comm_year=1970, max_life=80, ext_unit=20
        )
        assert result > BASELINE_YEAR

    def test_past_date_advances_correctly(self):
        """
        comm 1975, raw Tier 2 ret = 2015, US rules (max 80, unit 20).
        2015 ≤ 2024 → advance once: min(2015+20, 1975+80) = min(2035, 2055) = 2035.
        2035 > 2024 → stop.
        """
        result = advance_past_baseline(
            ret_year=2015, comm_year=1975, max_life=80, ext_unit=20
        )
        assert result == 2035

    def test_multiple_advances_needed(self):
        """
        comm 1930, raw ret 1990, max_life 60 (cap at 1990), ext_unit 20.
        1990 ≤ 2024 → try next: min(1990+20, 1930+60) = min(2010, 1990) = 1990.
        Stuck (next_yr <= adjusted) → hard floor = BASELINE_YEAR + 1.
        """
        result = advance_past_baseline(
            ret_year=1990, comm_year=1930, max_life=60, ext_unit=20
        )
        assert result == BASELINE_YEAR + 1

    def test_advance_bounded_by_max_life(self):
        """Advancement never exceeds comm_year + max_life."""
        result = advance_past_baseline(
            ret_year=2000, comm_year=1960, max_life=70, ext_unit=20
        )
        # 2000 ≤ 2024 → advance to min(2020, 2030) = 2020 ≤ 2024
        # → advance to min(2040, 2030) = 2030 > 2024 → stop at 2030
        assert result == 2030
        assert result <= 1960 + 70


# ── deterministic_extend ──────────────────────────────────────────────────────

class TestDeterministicExtend:

    def test_fraction_zero_never_extends(self):
        """With fraction=0.0 no reactor should receive an extension."""
        for rid in ("A001", "B002", "C003", "D004", "E005"):
            assert deterministic_extend(rid, 0.0) is False

    def test_fraction_one_always_extends(self):
        """With fraction=1.0 every reactor should receive an extension."""
        for rid in ("A001", "B002", "C003", "D004", "E005"):
            assert deterministic_extend(rid, 1.0) is True

    def test_deterministic_same_reactor(self):
        """The same reactor always gets the same answer for a given fraction."""
        rid = "REACTOR_XYZ_42"
        frac = 0.55
        result1 = deterministic_extend(rid, frac)
        result2 = deterministic_extend(rid, frac)
        assert result1 == result2

    def test_different_fractions_same_reactor(self):
        """Increasing fraction monotonically increases chance of extension."""
        rid = "STABLE_REACTOR"
        # At fraction=0 → False; at fraction=1 → True
        assert deterministic_extend(rid, 0.0) is False
        assert deterministic_extend(rid, 1.0) is True

    def test_different_reactors_different_results(self):
        """A mix of reactor IDs should not all give the same result at 0.5."""
        results = [deterministic_extend(f"REACTOR_{i:04d}", 0.5) for i in range(20)]
        # At 50% we'd expect roughly half True; definitely not all identical
        assert len(set(results)) == 2, "Expected both True and False in a 20-reactor sample at 50%"


# ── get_extended_retirement ───────────────────────────────────────────────────

class TestGetExtendedRetirement:

    def test_tier1_never_adjusted(self, db):
        """Tier 1 (declared) dates are returned unchanged under any policy."""
        for policy in ("CurrentPolicy", "ExtendedOperations", "AcceleratedRetirement"):
            result = get_extended_retirement(
                reactor_id="US_OP_1",
                comm_op_year=1990,
                current_ret_year=2060,
                retirement_tier=1,
                country="US",
                extension_policy=policy,
                conn=db,
            )
            assert result == 2060, f"Tier 1 date changed under policy={policy}"

    def test_extended_operations_reaches_max_life(self, db):
        """
        ExtendedOperations sets retirement to comm_year + max_life.
        US max_life = 80 yr. comm 1975 → expected 2055.
        """
        result = get_extended_retirement(
            reactor_id="US_TIER2",
            comm_op_year=1975,
            current_ret_year=2035,   # effective baseline (already advanced)
            retirement_tier=2,
            country="US",
            extension_policy="ExtendedOperations",
            conn=db,
        )
        assert result == 1975 + 80   # 2055

    def test_extended_operations_cannot_reduce(self, db):
        """ExtendedOperations uses max() — can never decrease retirement date."""
        result = get_extended_retirement(
            reactor_id="US_TIER2",
            comm_op_year=1975,
            current_ret_year=2060,   # already beyond max_life date (2055)
            retirement_tier=2,
            country="US",
            extension_policy="ExtendedOperations",
            conn=db,
        )
        assert result >= 2060

    def test_accelerated_retirement_no_change(self, db):
        """AcceleratedRetirement returns current_ret_year unchanged."""
        result = get_extended_retirement(
            reactor_id="US_TIER2",
            comm_op_year=1975,
            current_ret_year=2035,
            retirement_tier=2,
            country="US",
            extension_policy="AcceleratedRetirement",
            conn=db,
        )
        assert result == 2035

    def test_current_policy_within_bounds(self, db):
        """
        CurrentPolicy result must be ≥ current_ret_year and ≤ comm + max_life.
        """
        result = get_extended_retirement(
            reactor_id="US_TIER2",
            comm_op_year=1975,
            current_ret_year=2035,
            retirement_tier=2,
            country="US",
            extension_policy="CurrentPolicy",
            conn=db,
        )
        assert result >= 2035
        assert result <= 1975 + 80   # 2055

    def test_unknown_country_uses_defaults(self, db):
        """Unknown country falls back to 40/60/20 defaults without crashing."""
        result = get_extended_retirement(
            reactor_id="SOME_REACTOR",
            comm_op_year=1990,
            current_ret_year=2040,
            retirement_tier=2,
            country="ATLANTIS",
            extension_policy="ExtendedOperations",
            conn=db,
        )
        # Default max_life = 60 → 1990+60 = 2050
        assert result == 1990 + 60


# ── build_retirement_schedule ─────────────────────────────────────────────────

class TestBuildRetirementSchedule:

    def test_returns_dict_for_all_operating_reactors(self, db):
        """Schedule covers all Operating and LTS reactors in the DB."""
        schedule = build_retirement_schedule(db, "test_sc")
        # US_OP_1, US_OP_2, US_TIER2 are all Operating
        for rid in ("US_OP_1", "US_OP_2", "US_TIER2"):
            assert rid in schedule, f"{rid} missing from retirement schedule"

    def test_pipeline_reactors_excluded(self, db):
        """Pipeline reactors (UnderConstruction, Planned) are not scheduled."""
        schedule = build_retirement_schedule(db, "test_sc")
        assert "CHINA_UC_1" not in schedule
        assert "US_PLAN_1"  not in schedule

    def test_tier1_dates_unchanged(self, db):
        """Tier 1 dates survive build_retirement_schedule unchanged."""
        schedule = build_retirement_schedule(db, "test_sc")
        assert schedule["US_OP_1"] == 2060
        assert schedule["US_OP_2"] == 2030

    def test_tier2_past_date_advanced(self, db):
        """
        US_TIER2: comm 1975, raw ret_used 2015.
        After advance_past_baseline with US rules: ret = 2035.
        CurrentPolicy then either keeps 2035 or extends to 2055.
        Either way, result > BASELINE_YEAR.
        """
        schedule = build_retirement_schedule(db, "test_sc")
        assert schedule["US_TIER2"] > BASELINE_YEAR
        assert schedule["US_TIER2"] in (2035, 2055)   # the two possible outcomes

    def test_floor_applied(self, db):
        """No retirement year in the schedule should be ≤ BASELINE_YEAR."""
        schedule = build_retirement_schedule(db, "test_sc")
        for rid, yr in schedule.items():
            assert yr > BASELINE_YEAR, (
                f"{rid} has retirement year {yr} ≤ BASELINE_YEAR {BASELINE_YEAR}"
            )

    def test_unknown_scenario_raises(self, db):
        """Requesting a non-existent scenario raises ValueError."""
        with pytest.raises(ValueError, match="Unknown scenario"):
            build_retirement_schedule(db, "nonexistent_scenario")
