"""
test_state.py — Tests for model/state.py and the ProjectionState fast path.

The core guarantee of Phase 1 is that every tab in the dashboard shares a
single ProjectionState built once per render cycle, and that using this
state produces numerically identical results to the direct (no-state) path
that reads from the DB on every call.

These tests exist specifically to catch regressions in that guarantee —
i.e. if a future change makes the state fast path diverge from the direct
path, these tests will fail.

Test structure
──────────────
TestProjectionStateStructure   — ProjectionState fields are populated correctly
TestStatePathConsistency       — state path == direct path (no overrides)
TestStatePathWithOverrides     — state path == direct path (with what-if overrides)
TestApplyOverridesUnit         — _apply_overrides mutates inputs correctly
TestStateGuards                — error guards (write_to_db=True with state, etc.)
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import BASELINE_YEAR
from model.state import ProjectionState, build_projection_state, _apply_overrides
from model.projection import run_projection


# ── Helpers ───────────────────────────────────────────────────────────────────

def _rows(results: list[dict], region: str) -> dict[int, float]:
    """Return {year: capacity_gw} for one region."""
    return {
        r["year"]: r["capacity_operating_gw"]
        for r in results if r["region"] == region
    }


def _global(results: list[dict], year: int) -> float:
    row = next(r for r in results if r["year"] == year and r["region"] == "Global")
    return row["capacity_operating_gw"]


# ── TestProjectionStateStructure ──────────────────────────────────────────────

class TestProjectionStateStructure:
    """build_projection_state returns a correctly populated ProjectionState."""

    def test_returns_projection_state_instance(self, db_path):
        state = build_projection_state("test_sc", db_path=db_path)
        assert isinstance(state, ProjectionState)

    def test_scenario_id_stored(self, db_path):
        state = build_projection_state("test_sc", db_path=db_path)
        assert state.scenario_id == "test_sc"

    def test_fleet_contains_operating_reactors(self, db_path):
        state = build_projection_state("test_sc", db_path=db_path)
        fleet_ids = {r["reactor_id"] for r in state.fleet}
        assert "US_OP_1"  in fleet_ids
        assert "US_OP_2"  in fleet_ids
        assert "US_TIER2" in fleet_ids

    def test_fleet_excludes_pipeline_reactors(self, db_path):
        """Pipeline reactors are not in fleet — they live in state.pipeline."""
        state = build_projection_state("test_sc", db_path=db_path)
        fleet_ids = {r["reactor_id"] for r in state.fleet}
        assert "CHINA_UC_1" not in fleet_ids
        assert "US_PLAN_1"  not in fleet_ids

    def test_pipeline_contains_expected_reactors(self, db_path):
        state = build_projection_state("test_sc", db_path=db_path)
        pipeline_ids = {r["reactor_id"] for r in state.pipeline}
        assert "CHINA_UC_1" in pipeline_ids
        assert "US_PLAN_1"  in pipeline_ids

    def test_retirement_schedule_populated(self, db_path):
        state = build_projection_state("test_sc", db_path=db_path)
        assert "US_OP_1"  in state.retirement_schedule
        assert "US_OP_2"  in state.retirement_schedule
        assert "US_TIER2" in state.retirement_schedule

    def test_retirement_schedule_tier1_dates_correct(self, db_path):
        state = build_projection_state("test_sc", db_path=db_path)
        assert state.retirement_schedule["US_OP_1"] == 2060
        assert state.retirement_schedule["US_OP_2"] == 2030

    def test_scenario_params_populated(self, db_path):
        state = build_projection_state("test_sc", db_path=db_path)
        assert isinstance(state.scenario_params, dict)
        assert "extension_policy_global" in state.scenario_params

    def test_what_if_overrides_none_by_default(self, db_path):
        state = build_projection_state("test_sc", db_path=db_path)
        assert state.what_if_overrides is None

    def test_what_if_overrides_stored_when_given(self, db_path):
        ov = {"US_OP_2": {"retirement_year": 2035}}
        state = build_projection_state("test_sc", what_if_overrides=ov, db_path=db_path)
        assert state.what_if_overrides == ov

    def test_unknown_scenario_raises(self, db_path):
        with pytest.raises(ValueError, match="Unknown scenario"):
            build_projection_state("no_such_scenario", db_path=db_path)


# ── TestStatePathConsistency ──────────────────────────────────────────────────

class TestStatePathConsistency:
    """
    The state fast path must produce numerically identical output to the
    standard (direct DB) path for every year and region.

    This is the core guarantee of the Phase 1 architecture.  If these tests
    fail, the tabs would be showing inconsistent numbers.
    """

    def test_global_capacity_identical_no_overrides(self, db, db_path):
        """Global capacity matches across both paths for every projected year."""
        direct = run_projection(db, "test_sc", end_year=2034, write_to_db=False)
        state  = build_projection_state("test_sc", db_path=db_path)
        via_state = run_projection(db, "test_sc", end_year=2034,
                                   state=state, write_to_db=False)

        direct_global  = _rows(direct,    "Global")
        state_global   = _rows(via_state, "Global")
        assert set(direct_global.keys()) == set(state_global.keys()), \
            "State path produced a different set of years than the direct path"
        for year, cap in direct_global.items():
            assert cap == pytest.approx(state_global[year], abs=0.001), \
                f"Capacity diverged at Global/{year}: direct={cap:.4f}, state={state_global[year]:.4f}"

    def test_regional_capacity_identical_no_overrides(self, db, db_path):
        """Per-region capacity matches across both paths."""
        direct    = run_projection(db, "test_sc", end_year=2034, write_to_db=False)
        state     = build_projection_state("test_sc", db_path=db_path)
        via_state = run_projection(db, "test_sc", end_year=2034,
                                   state=state, write_to_db=False)

        for region in ("United States", "China"):
            d_rows = _rows(direct,    region)
            s_rows = _rows(via_state, region)
            for year, cap in d_rows.items():
                assert cap == pytest.approx(s_rows[year], abs=0.001), \
                    f"Capacity diverged at {region}/{year}"

    def test_retirements_identical(self, db, db_path):
        """retirements_this_year_gw is identical across both paths."""
        direct    = run_projection(db, "test_sc", end_year=2034, write_to_db=False)
        state     = build_projection_state("test_sc", db_path=db_path)
        via_state = run_projection(db, "test_sc", end_year=2034,
                                   state=state, write_to_db=False)

        for row_d in direct:
            if row_d["region"] != "Global":
                continue
            yr  = row_d["year"]
            ret_d = row_d["retirements_this_year_gw"]
            ret_s = next(
                r["retirements_this_year_gw"]
                for r in via_state
                if r["year"] == yr and r["region"] == "Global"
            )
            assert ret_d == pytest.approx(ret_s, abs=0.001), \
                f"Retirements diverged at Global/{yr}"

    def test_additions_identical(self, db, db_path):
        """additions_this_year_gw is identical across both paths."""
        direct    = run_projection(db, "test_sc", end_year=2034, write_to_db=False)
        state     = build_projection_state("test_sc", db_path=db_path)
        via_state = run_projection(db, "test_sc", end_year=2034,
                                   state=state, write_to_db=False)

        for row_d in direct:
            if row_d["region"] != "Global":
                continue
            yr    = row_d["year"]
            add_d = row_d["additions_this_year_gw"]
            add_s = next(
                r["additions_this_year_gw"]
                for r in via_state
                if r["year"] == yr and r["region"] == "Global"
            )
            assert add_d == pytest.approx(add_s, abs=0.001), \
                f"Additions diverged at Global/{yr}"


# ── TestStatePathWithOverrides ────────────────────────────────────────────────

class TestStatePathWithOverrides:
    """
    When what-if overrides are applied, the state path and the direct path
    must still agree.  This tests that _apply_overrides in state.py matches
    the inline override logic in run_projection's standard path.
    """

    def test_retirement_override_consistent(self, db, db_path):
        """Early retirement override gives same results via both paths."""
        ov = {"US_OP_1": {"retirement_year": 2027}}
        direct    = run_projection(db, "test_sc", what_if_overrides=ov,
                                   end_year=2030, write_to_db=False)
        state     = build_projection_state("test_sc", what_if_overrides=ov,
                                           db_path=db_path)
        via_state = run_projection(db, "test_sc", end_year=2030,
                                   state=state, write_to_db=False)

        for year in (2026, 2027, 2028, 2029, 2030):
            assert _global(direct, year) == pytest.approx(_global(via_state, year), abs=0.001), \
                f"Override consistency failed at year {year}"

    def test_pipeline_cancellation_consistent(self, db, db_path):
        """Pipeline cancellation gives same results via both paths."""
        ov = {"CHINA_UC_1": {"pipeline_probability": 0.0}}
        direct    = run_projection(db, "test_sc", what_if_overrides=ov,
                                   end_year=2030, write_to_db=False)
        state     = build_projection_state("test_sc", what_if_overrides=ov,
                                           db_path=db_path)
        via_state = run_projection(db, "test_sc", end_year=2030,
                                   state=state, write_to_db=False)

        for year in (2024, 2028, 2029, 2030):
            assert _global(direct, year) == pytest.approx(_global(via_state, year), abs=0.001), \
                f"Pipeline cancellation consistency failed at year {year}"

    def test_capacity_override_consistent(self, db, db_path):
        """capacity_mw override gives same results via both paths."""
        ov = {"US_OP_1": {"capacity_mw": 500.0}}
        direct    = run_projection(db, "test_sc", what_if_overrides=ov,
                                   end_year=2028, write_to_db=False)
        state     = build_projection_state("test_sc", what_if_overrides=ov,
                                           db_path=db_path)
        via_state = run_projection(db, "test_sc", end_year=2028,
                                   state=state, write_to_db=False)

        for year in (2024, 2026, 2028):
            assert _global(direct, year) == pytest.approx(_global(via_state, year), abs=0.001), \
                f"Capacity override consistency failed at year {year}"

    def test_synthetic_build_consistent(self, db, db_path):
        """Synthetic new-build gives same results via both paths."""
        ov = {"__synthetic__": [
            {"region": "China", "capacity_mw": 500.0, "per_year": 1,
             "start_year": 2030, "n_years": 2}
        ]}
        direct    = run_projection(db, "test_sc", what_if_overrides=ov,
                                   end_year=2034, write_to_db=False)
        state     = build_projection_state("test_sc", what_if_overrides=ov,
                                           db_path=db_path)
        via_state = run_projection(db, "test_sc", end_year=2034,
                                   state=state, write_to_db=False)

        for year in (2029, 2030, 2031, 2032):
            assert _global(direct, year) == pytest.approx(_global(via_state, year), abs=0.001), \
                f"Synthetic build consistency failed at year {year}"

    def test_combined_overrides_consistent(self, db, db_path):
        """Multiple simultaneous overrides give same results via both paths."""
        ov = {
            "US_OP_2":    {"retirement_year": 2035},
            "CHINA_UC_1": {"pipeline_probability": 0.5},
            "__synthetic__": [
                {"region": "China", "capacity_mw": 200.0, "per_year": 1,
                 "start_year": 2029, "n_years": 1}
            ],
        }
        direct    = run_projection(db, "test_sc", what_if_overrides=ov,
                                   end_year=2034, write_to_db=False)
        state     = build_projection_state("test_sc", what_if_overrides=ov,
                                           db_path=db_path)
        via_state = run_projection(db, "test_sc", end_year=2034,
                                   state=state, write_to_db=False)

        for year in range(2024, 2035):
            assert _global(direct, year) == pytest.approx(_global(via_state, year), abs=0.001), \
                f"Combined overrides consistency failed at year {year}"


# ── TestApplyOverridesUnit ────────────────────────────────────────────────────

class TestApplyOverridesUnit:
    """
    Unit tests for _apply_overrides — the single function responsible for
    mutating fleet/pipeline/retirement_schedule in-place.
    """

    def _sample_state(self):
        fleet = [
            {"reactor_id": "R1", "region": "US", "status": "Operating",
             "cap_mw": 1000.0, "restart_date": None},
            {"reactor_id": "R2", "region": "US", "status": "Operating",
             "cap_mw": 500.0,  "restart_date": None},
        ]
        pipeline = [
            {"reactor_id": "P1", "region": "China",
             "net_capacity_mw": 1000.0, "expected_capacity_mw": 1000.0,
             "effective_online_year": 2028},
        ]
        schedule = {"R1": 2060, "R2": 2030}
        return fleet, pipeline, schedule

    def test_retirement_year_updated(self):
        fleet, pipeline, schedule = self._sample_state()
        _apply_overrides(fleet, pipeline, schedule,
                         {"R2": {"retirement_year": 2027}})
        assert schedule["R2"] == 2027
        assert schedule["R1"] == 2060   # untouched

    def test_capacity_mw_updated(self):
        fleet, pipeline, schedule = self._sample_state()
        _apply_overrides(fleet, pipeline, schedule,
                         {"R1": {"capacity_mw": 750.0}})
        r1 = next(r for r in fleet if r["reactor_id"] == "R1")
        assert r1["cap_mw"] == 750.0
        r2 = next(r for r in fleet if r["reactor_id"] == "R2")
        assert r2["cap_mw"] == 500.0   # untouched

    def test_pipeline_probability_scales_expected_capacity(self):
        fleet, pipeline, schedule = self._sample_state()
        _apply_overrides(fleet, pipeline, schedule,
                         {"P1": {"pipeline_probability": 0.5}})
        p1 = pipeline[0]
        assert p1["expected_capacity_mw"] == pytest.approx(500.0, abs=0.01)

    def test_pipeline_probability_zero_zeroes_capacity(self):
        fleet, pipeline, schedule = self._sample_state()
        _apply_overrides(fleet, pipeline, schedule,
                         {"P1": {"pipeline_probability": 0.0}})
        assert pipeline[0]["expected_capacity_mw"] == pytest.approx(0.0, abs=0.001)

    def test_expected_online_year_updated(self):
        fleet, pipeline, schedule = self._sample_state()
        _apply_overrides(fleet, pipeline, schedule,
                         {"P1": {"expected_online_year": 2032}})
        assert pipeline[0]["effective_online_year"] == 2032

    def test_synthetic_builds_appended(self):
        fleet, pipeline, schedule = self._sample_state()
        original_len = len(pipeline)
        ov = {"__synthetic__": [
            {"region": "China", "capacity_mw": 500.0, "per_year": 2,
             "start_year": 2030, "n_years": 3},
        ]}
        _apply_overrides(fleet, pipeline, schedule, ov)
        # 3 years × 2 per_year... but n_years=3 means 3 batches appended
        assert len(pipeline) == original_len + 3

    def test_synthetic_zero_capacity_not_appended(self):
        fleet, pipeline, schedule = self._sample_state()
        original_len = len(pipeline)
        ov = {"__synthetic__": [
            {"region": "China", "capacity_mw": 0.0, "per_year": 1,
             "start_year": 2030, "n_years": 2},
        ]}
        _apply_overrides(fleet, pipeline, schedule, ov)
        assert len(pipeline) == original_len   # nothing added

    def test_empty_overrides_no_mutation(self):
        fleet, pipeline, schedule = self._sample_state()
        _apply_overrides(fleet, pipeline, schedule, {})
        assert schedule == {"R1": 2060, "R2": 2030}
        assert fleet[0]["cap_mw"] == 1000.0

    def test_override_for_unknown_reactor_does_not_crash(self):
        """Overriding a reactor_id not in fleet/pipeline silently skips it."""
        fleet, pipeline, schedule = self._sample_state()
        _apply_overrides(fleet, pipeline, schedule,
                         {"GHOST_REACTOR": {"retirement_year": 2033}})
        # Schedule only gains the key if it was already present or gets added
        # The implementation adds it regardless — that's OK (schedule is just a dict)
        # but fleet and pipeline should be unchanged
        assert fleet[0]["cap_mw"] == 1000.0
        assert pipeline[0]["expected_capacity_mw"] == 1000.0

    def test_multiple_overrides_applied_independently(self):
        fleet, pipeline, schedule = self._sample_state()
        _apply_overrides(fleet, pipeline, schedule, {
            "R1": {"capacity_mw": 800.0},
            "R2": {"retirement_year": 2028},
            "P1": {"pipeline_probability": 0.25},
        })
        r1 = next(r for r in fleet if r["reactor_id"] == "R1")
        r2 = next(r for r in fleet if r["reactor_id"] == "R2")
        assert r1["cap_mw"]    == pytest.approx(800.0, abs=0.01)
        assert schedule["R2"]  == 2028
        assert pipeline[0]["expected_capacity_mw"] == pytest.approx(250.0, abs=0.01)


# ── TestStateGuards ───────────────────────────────────────────────────────────

class TestStateGuards:

    def test_write_to_db_true_with_state_raises(self, db, db_path):
        """Passing state= with write_to_db=True must raise ValueError."""
        state = build_projection_state("test_sc", db_path=db_path)
        with pytest.raises(ValueError, match="write_to_db=True is not supported"):
            run_projection(db, "test_sc", state=state, write_to_db=True)

    def test_state_path_produces_same_row_count(self, db, db_path):
        """State and direct paths produce the same number of output rows."""
        direct    = run_projection(db, "test_sc", end_year=2034, write_to_db=False)
        state     = build_projection_state("test_sc", db_path=db_path)
        via_state = run_projection(db, "test_sc", end_year=2034,
                                   state=state, write_to_db=False)
        assert len(direct) == len(via_state)
