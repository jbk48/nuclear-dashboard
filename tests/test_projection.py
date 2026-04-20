"""
test_projection.py — Integration tests for model/projection.py

Tests the full run_projection() engine including what-if override logic.

Expected global capacity under 'test_sc' (all pipeline rates = 1.0, no delay):
  2024  →  1.5 GW  (US_OP_1 1000 MW + US_OP_2 500 MW)
  2028  →  2.5 GW  (+ CHINA_UC_1 1000 MW online)
  2030  →  2.0 GW  (US_OP_2 retires)
  2032  →  2.8 GW  (+ US_PLAN_1 800 MW online)

Key regression tests:
  - Cancelling a pipeline reactor (pipeline_probability=0) must REDUCE (not
    increase) future capacity — regression for the bug where the cancelled
    reactor was silently promoted into the operating fleet via the extra_ids
    path, adding its full MW to every year from 2024 onwards.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import BASELINE_YEAR
from model.projection import run_projection


# ── Helpers ───────────────────────────────────────────────────────────────────

def _global(results: list[dict], year: int) -> float:
    """Return global capacity (GW) for a given year."""
    row = next(
        (r for r in results if r["year"] == year and r["region"] == "Global"),
        None,
    )
    assert row is not None, f"No Global row for year {year}"
    return row["capacity_operating_gw"]


def _region(results: list[dict], year: int, region: str) -> float:
    """Return regional capacity (GW) for a given year."""
    row = next(
        (r for r in results if r["year"] == year and r["region"] == region),
        None,
    )
    assert row is not None, f"No row for region={region}, year={year}"
    return row["capacity_operating_gw"]


def _retirements(results: list[dict], year: int) -> float:
    """Return global retirements (GW) in a given year."""
    row = next(
        (r for r in results if r["year"] == year and r["region"] == "Global"),
        None,
    )
    assert row is not None
    return row["retirements_this_year_gw"]


def _base(db, end_year: int = 2035):
    """Run a base (no overrides) projection for test_sc."""
    return run_projection(db, "test_sc", end_year=end_year, write_to_db=False)


# ── Shape and completeness ────────────────────────────────────────────────────

class TestProjectionShape:

    def test_returns_list_of_dicts(self, db):
        results = _base(db)
        assert isinstance(results, list)
        assert all(isinstance(r, dict) for r in results)

    def test_contains_global_region(self, db):
        results = _base(db)
        years_with_global = {r["year"] for r in results if r["region"] == "Global"}
        assert BASELINE_YEAR in years_with_global
        assert 2035 in years_with_global

    def test_baseline_year_present(self, db):
        results = _base(db)
        years = {r["year"] for r in results}
        assert BASELINE_YEAR in years

    def test_required_fields_present(self, db):
        results = _base(db)
        required = {
            "year", "region", "capacity_operating_gw",
            "retirements_this_year_gw", "additions_this_year_gw",
            "is_bottom_up",
        }
        for row in results[:5]:
            missing = required - set(row.keys())
            assert not missing, f"Missing fields: {missing}"

    def test_unknown_scenario_raises(self, db):
        with pytest.raises(ValueError, match="Unknown scenario"):
            run_projection(db, "no_such_scenario", write_to_db=False)


# ── Baseline capacity values ──────────────────────────────────────────────────

class TestBaselineCapacity:

    def test_initial_global_capacity(self, db):
        """
        2024: US_OP_1 (1 GW) + US_OP_2 (0.5 GW) + US_TIER2 (0.6 GW) = 2.1 GW.
        CHINA_UC_1 is not online until 2028.
        """
        results = _base(db)
        assert _global(results, 2024) == pytest.approx(2.1, abs=0.01)

    def test_initial_us_capacity(self, db):
        """US has 2.1 GW at baseline (US_OP_1 + US_OP_2 + US_TIER2)."""
        results = _base(db)
        assert _region(results, 2024, "United States") == pytest.approx(2.1, abs=0.01)

    def test_initial_china_capacity_zero(self, db):
        """China has no operating reactors at baseline — pipeline not yet online."""
        results = _base(db)
        assert _region(results, 2024, "China") == pytest.approx(0.0, abs=0.01)

    def test_capacity_nonnegative(self, db):
        """Capacity should never be negative in any region or year."""
        results = _base(db, end_year=2040)
        for row in results:
            assert row["capacity_operating_gw"] >= 0.0, (
                f"Negative capacity: region={row['region']}, year={row['year']}"
            )


# ── Pipeline comes online ─────────────────────────────────────────────────────

class TestPipelineOnline:

    def test_china_uc_online_in_2028(self, db):
        """CHINA_UC_1 (1 GW) comes online in 2028; China capacity jumps."""
        results = _base(db, end_year=2030)
        china_2027 = _region(results, 2027, "China")
        china_2028 = _region(results, 2028, "China")
        assert china_2027 == pytest.approx(0.0, abs=0.01)
        assert china_2028 == pytest.approx(1.0, abs=0.01)

    def test_us_planned_online_in_2032(self, db):
        """US_PLAN_1 (0.8 GW) comes online in 2032."""
        results = _base(db, end_year=2034)
        us_2031 = _region(results, 2031, "United States")
        us_2032 = _region(results, 2032, "United States")
        # By 2031: US_OP_2 has retired (2030), US_OP_1 + US_TIER2 = 1.6 GW
        assert us_2031 == pytest.approx(1.6, abs=0.01)
        # 2032: +US_PLAN_1 = 2.4 GW
        assert us_2032 == pytest.approx(2.4, abs=0.01)

    def test_global_capacity_after_both_pipeline(self, db):
        """At 2032 the global total should be 3.4 GW (US 2.4 + China 1.0)."""
        results = _base(db, end_year=2034)
        assert _global(results, 2032) == pytest.approx(3.4, abs=0.01)


# ── Retirement fires at correct year ─────────────────────────────────────────

class TestRetirements:

    def test_us_op2_retires_in_2030(self, db):
        """US_OP_2 (0.5 GW) retires in 2030; US capacity drops by 0.5 GW."""
        results = _base(db, end_year=2034)
        # 2029: US_OP_1 + US_OP_2 + US_TIER2 = 2.1 GW
        assert _region(results, 2029, "United States") == pytest.approx(2.1, abs=0.01)
        # 2030: US_OP_2 retires → US drops by 0.5 GW → 1.6 GW
        assert _region(results, 2030, "United States") == pytest.approx(1.6, abs=0.01)

    def test_retirement_recorded_in_correct_year(self, db):
        """Global retirements_this_year_gw should spike in 2030."""
        results = _base(db, end_year=2034)
        assert _retirements(results, 2029) == pytest.approx(0.0, abs=0.01)
        assert _retirements(results, 2030) == pytest.approx(0.5, abs=0.01)
        assert _retirements(results, 2031) == pytest.approx(0.0, abs=0.01)

    def test_retired_reactor_absent_from_subsequent_years(self, db):
        """Once US_OP_2 retires in 2030 it must not reappear."""
        results = _base(db, end_year=2034)
        us_2031 = _region(results, 2031, "United States")
        us_2030 = _region(results, 2030, "United States")
        # US_PLAN_1 is not online until 2032, so US should hold flat 2030→2031
        assert us_2031 == pytest.approx(us_2030, abs=0.01)


# ── What-if: pipeline cancellation (regression) ───────────────────────────────

class TestPipelineCancellationRegression:
    """
    Regression suite for the bug where applying pipeline_probability=0 via
    what_if_overrides caused the cancelled reactor to be added to the operating
    fleet (via the extra_ids path), inflating capacity rather than reducing it.

    Bug root: extra_ids collected ALL overridden reactors not in fleet_ids,
    including pipeline reactors whose only override was pipeline_probability=0.
    Once in operating_fleet they contributed full MW from year 0 with no
    gating, since only LongTermShutdown status triggered a restart_date check.

    Fix: extra_ids is now restricted to reactors with fleet-level override fields
    (status / capacity_mw / restart_date).  A pure pipeline_probability override
    no longer causes fleet promotion.
    """

    def test_cancelled_pipeline_does_not_inflate_current_capacity(self, db):
        """
        2024 capacity must be IDENTICAL with and without the cancellation.
        CHINA_UC_1 isn't online until 2028; cancelling it must not add its
        1 GW to the operating fleet in 2024.
        """
        base = _base(db, end_year=2030)
        wi   = run_projection(
            db, "test_sc",
            what_if_overrides={"CHINA_UC_1": {"pipeline_probability": 0.0}},
            end_year=2030,
            write_to_db=False,
        )
        assert _global(wi, 2024) == pytest.approx(_global(base, 2024), abs=0.001), (
            "Cancelling a pipeline reactor inflated current capacity — "
            "the fleet-promotion bug has re-appeared."
        )

    def test_cancelled_pipeline_reduces_future_capacity(self, db):
        """
        After the planned online year (2028), the cancelled scenario must have
        LESS capacity than the base scenario.
        """
        base = _base(db, end_year=2030)
        wi   = run_projection(
            db, "test_sc",
            what_if_overrides={"CHINA_UC_1": {"pipeline_probability": 0.0}},
            end_year=2030,
            write_to_db=False,
        )
        base_2029 = _global(base, 2029)
        wi_2029   = _global(wi,   2029)
        assert wi_2029 < base_2029, (
            f"Expected cancelled scenario ({wi_2029} GW) < base ({base_2029} GW) "
            "at 2029, but capacity was not reduced."
        )

    def test_cancelled_pipeline_exact_capacity_drop(self, db):
        """
        The difference at 2029 should be exactly CHINA_UC_1's capacity (1.0 GW).
        """
        base = _base(db, end_year=2030)
        wi   = run_projection(
            db, "test_sc",
            what_if_overrides={"CHINA_UC_1": {"pipeline_probability": 0.0}},
            end_year=2030,
            write_to_db=False,
        )
        delta = _global(base, 2029) - _global(wi, 2029)
        assert delta == pytest.approx(1.0, abs=0.01), (
            f"Expected 1.0 GW drop from cancellation, got {delta:.3f} GW."
        )

    def test_partial_cancellation_proportional(self, db):
        """
        Setting pipeline_probability=0.5 should halve the reactor's contribution
        (500 MW instead of 1000 MW), making the delta ~0.5 GW.
        """
        base = _base(db, end_year=2030)
        wi   = run_projection(
            db, "test_sc",
            what_if_overrides={"CHINA_UC_1": {"pipeline_probability": 0.5}},
            end_year=2030,
            write_to_db=False,
        )
        delta = _global(base, 2029) - _global(wi, 2029)
        assert delta == pytest.approx(0.5, abs=0.01)

    def test_no_op_override_equals_base(self, db):
        """
        An empty overrides dict must produce output identical to no overrides.
        """
        base = run_projection(db, "test_sc", end_year=2030, write_to_db=False)
        wi   = run_projection(
            db, "test_sc",
            what_if_overrides={},
            end_year=2030,
            write_to_db=False,
        )
        for year in (2024, 2028, 2030):
            assert _global(wi, year) == pytest.approx(_global(base, year), abs=0.001)


# ── What-if: retirement year override ────────────────────────────────────────

class TestRetirementOverride:

    def test_early_retirement_reduces_capacity(self, db):
        """
        Overriding US_OP_1's retirement to 2027 should remove 1 GW from US
        in 2028 (one year after the override).
        """
        base = _base(db, end_year=2030)
        wi   = run_projection(
            db, "test_sc",
            what_if_overrides={"US_OP_1": {"retirement_year": 2027}},
            end_year=2030,
            write_to_db=False,
        )
        # 2028: US_OP_1 should be retired — US drops by 1 GW relative to base
        us_base_2028 = _region(base, 2028, "United States")
        us_wi_2028   = _region(wi,   2028, "United States")
        assert us_wi_2028 == pytest.approx(us_base_2028 - 1.0, abs=0.01)

    def test_retirement_fires_in_override_year(self, db):
        """
        The retirement should appear in retirements_this_year_gw for 2027,
        not the original 2060.
        """
        wi = run_projection(
            db, "test_sc",
            what_if_overrides={"US_OP_1": {"retirement_year": 2027}},
            end_year=2030,
            write_to_db=False,
        )
        ret_2027 = next(
            r["retirements_this_year_gw"]
            for r in wi if r["year"] == 2027 and r["region"] == "Global"
        )
        assert ret_2027 == pytest.approx(1.0, abs=0.01)

    def test_late_retirement_increases_capacity(self, db):
        """
        Overriding US_OP_2's retirement from 2030 to 2035 keeps it in the
        fleet longer — global capacity at 2031 should be 0.5 GW higher.
        """
        base = _base(db, end_year=2034)
        wi   = run_projection(
            db, "test_sc",
            what_if_overrides={"US_OP_2": {"retirement_year": 2035}},
            end_year=2034,
            write_to_db=False,
        )
        assert _global(wi, 2031) == pytest.approx(_global(base, 2031) + 0.5, abs=0.01)


# ── What-if: synthetic new build ─────────────────────────────────────────────

class TestSyntheticBuild:

    def test_synthetic_adds_capacity(self, db):
        """
        Adding a synthetic batch of 500 MW/yr × 2 years in China from 2030
        should increase China's capacity by 0.5 GW in 2030 and 1.0 GW in 2031.
        """
        base = _base(db, end_year=2034)
        synthetic = [{"region": "China", "capacity_mw": 500.0,
                      "per_year": 1, "start_year": 2030, "n_years": 2}]
        wi = run_projection(
            db, "test_sc",
            what_if_overrides={"__synthetic__": synthetic},
            end_year=2034,
            write_to_db=False,
        )
        china_base_2030 = _region(base, 2030, "China")
        china_wi_2030   = _region(wi,   2030, "China")
        china_base_2031 = _region(base, 2031, "China")
        china_wi_2031   = _region(wi,   2031, "China")

        assert china_wi_2030 == pytest.approx(china_base_2030 + 0.5, abs=0.01)
        assert china_wi_2031 == pytest.approx(china_base_2031 + 1.0, abs=0.01)

    def test_synthetic_global_increase(self, db):
        """Synthetic build is reflected in the Global aggregate."""
        base = _base(db, end_year=2034)
        synthetic = [{"region": "China", "capacity_mw": 1000.0,
                      "per_year": 1, "start_year": 2030, "n_years": 1}]
        wi = run_projection(
            db, "test_sc",
            what_if_overrides={"__synthetic__": synthetic},
            end_year=2034,
            write_to_db=False,
        )
        assert _global(wi, 2030) == pytest.approx(_global(base, 2030) + 1.0, abs=0.01)

    def test_synthetic_zero_capacity_no_effect(self, db):
        """A zero-MW synthetic batch should not change any output."""
        base = _base(db, end_year=2030)
        synthetic = [{"region": "China", "capacity_mw": 0.0,
                      "per_year": 1, "start_year": 2028, "n_years": 3}]
        wi = run_projection(
            db, "test_sc",
            what_if_overrides={"__synthetic__": synthetic},
            end_year=2030,
            write_to_db=False,
        )
        for year in (2028, 2029, 2030):
            assert _global(wi, year) == pytest.approx(_global(base, year), abs=0.001)

    def test_synthetic_does_not_affect_pre_start_years(self, db):
        """Synthetic capacity added from 2030 must not appear before 2030."""
        base = _base(db, end_year=2034)
        synthetic = [{"region": "China", "capacity_mw": 500.0,
                      "per_year": 1, "start_year": 2030, "n_years": 3}]
        wi = run_projection(
            db, "test_sc",
            what_if_overrides={"__synthetic__": synthetic},
            end_year=2034,
            write_to_db=False,
        )
        assert _region(wi, 2028, "China") == pytest.approx(
            _region(base, 2028, "China"), abs=0.001
        )


# ── What-if: fleet-type overrides (restart / capacity) ───────────────────────

class TestFleetOverrides:

    def test_capacity_override_scales_contribution(self, db):
        """
        Overriding US_OP_1's capacity_mw from 1000 to 500 should halve its
        contribution, dropping US capacity by 0.5 GW from 2024 onwards.
        """
        base = _base(db, end_year=2028)
        wi   = run_projection(
            db, "test_sc",
            what_if_overrides={"US_OP_1": {"capacity_mw": 500.0}},
            end_year=2028,
            write_to_db=False,
        )
        us_base_2024 = _region(base, 2024, "United States")
        us_wi_2024   = _region(wi,   2024, "United States")
        assert us_wi_2024 == pytest.approx(us_base_2024 - 0.5, abs=0.01)

    def test_fleet_override_does_not_affect_pipeline(self, db):
        """
        A capacity_mw override on an operating reactor must not change pipeline
        additions (CHINA_UC_1 should still come online at 1.0 GW in 2028).
        """
        wi = run_projection(
            db, "test_sc",
            what_if_overrides={"US_OP_1": {"capacity_mw": 500.0}},
            end_year=2030,
            write_to_db=False,
        )
        # China capacity at 2028 should still reflect CHINA_UC_1 at full 1.0 GW
        assert _region(wi, 2028, "China") == pytest.approx(1.0, abs=0.01)

    def test_permanent_shutdown_not_promoted_by_pipeline_override(self, db):
        """
        US_PERM_1 is PermanentShutdown. Applying only pipeline_probability=0
        must NOT promote it into the operating fleet (it's not a pipeline
        reactor — this tests that the extra_ids guard rejects pure-pipeline
        overrides on any non-fleet reactor too).
        """
        base = _base(db, end_year=2026)
        wi   = run_projection(
            db, "test_sc",
            what_if_overrides={"US_PERM_1": {"pipeline_probability": 0.0}},
            end_year=2026,
            write_to_db=False,
        )
        # US_PERM_1 should not contribute any capacity
        assert _global(wi, 2024) == pytest.approx(_global(base, 2024), abs=0.001)
        assert _global(wi, 2026) == pytest.approx(_global(base, 2026), abs=0.001)
