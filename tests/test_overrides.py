"""
test_overrides.py — Unit tests for dashboard/overrides.py.

WhatIfOverrides is the typed layer that sits between Streamlit session state
(a list of {reactor_id, field, value} dicts) and the projection engine
(which expects {reactor_id: {field: value}}).

The round-trip guarantee: from_session_state() → to_raw_dict() must produce
the same output as the old _build_wi_overrides_dict() helper that it replaced.
These tests verify that guarantee and cover all field types.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.overrides import ReactorOverride, SyntheticBatch, WhatIfOverrides


# ── ReactorOverride ───────────────────────────────────────────────────────────

class TestReactorOverride:

    def test_empty_by_default(self):
        ov = ReactorOverride()
        assert ov.is_empty

    def test_is_empty_false_when_field_set(self):
        ov = ReactorOverride(retirement_year=2033)
        assert not ov.is_empty

    def test_to_dict_omits_none_fields(self):
        ov = ReactorOverride(retirement_year=2033)
        d  = ov.to_dict()
        assert d == {"retirement_year": 2033}
        assert "capacity_mw" not in d

    def test_to_dict_all_fields(self):
        ov = ReactorOverride(
            retirement_year=2033,
            capacity_mw=900.0,
            status="LongTermShutdown",
            restart_date="2030-01-01",
            pipeline_probability=0.8,
            expected_online_year=2029,
        )
        d = ov.to_dict()
        assert d["retirement_year"]      == 2033
        assert d["capacity_mw"]          == 900.0
        assert d["status"]               == "LongTermShutdown"
        assert d["restart_date"]         == "2030-01-01"
        assert d["pipeline_probability"] == 0.8
        assert d["expected_online_year"] == 2029

    def test_to_dict_empty_is_empty_dict(self):
        assert ReactorOverride().to_dict() == {}


# ── SyntheticBatch ────────────────────────────────────────────────────────────

class TestSyntheticBatch:

    def test_to_dict_all_fields(self):
        sb = SyntheticBatch(region="China", capacity_mw=500.0,
                            per_year=2, start_year=2030, n_years=3)
        d  = sb.to_dict()
        assert d["region"]      == "China"
        assert d["capacity_mw"] == 500.0
        assert d["per_year"]    == 2
        assert d["start_year"]  == 2030
        assert d["n_years"]     == 3


# ── WhatIfOverrides ───────────────────────────────────────────────────────────

class TestWhatIfOverridesEmpty:

    def test_empty_by_default(self):
        wi = WhatIfOverrides()
        assert wi.is_empty

    def test_empty_to_raw_dict(self):
        assert WhatIfOverrides().to_raw_dict() == {}

    def test_reactor_count_zero(self):
        assert WhatIfOverrides().reactor_count() == 0

    def test_retirement_year_overrides_empty(self):
        assert WhatIfOverrides().retirement_year_overrides() == []


class TestFromSessionState:
    """
    from_session_state() parses the Streamlit session format:
        [{"reactor_id": "CA-22", "field": "retirement_year", "value": 2033}, ...]
    """

    def test_single_retirement_override(self):
        wi = WhatIfOverrides.from_session_state(
            [{"reactor_id": "CA-22", "field": "retirement_year", "value": 2033}],
            [],
        )
        assert "CA-22" in wi.reactor_overrides
        assert wi.reactor_overrides["CA-22"].retirement_year == 2033

    def test_multiple_fields_same_reactor(self):
        """Two entries for the same reactor_id both land on the same override."""
        wi = WhatIfOverrides.from_session_state(
            [
                {"reactor_id": "R1", "field": "retirement_year", "value": 2028},
                {"reactor_id": "R1", "field": "capacity_mw",     "value": 750.0},
            ],
            [],
        )
        assert wi.reactor_count() == 1
        ov = wi.reactor_overrides["R1"]
        assert ov.retirement_year == 2028
        assert ov.capacity_mw     == 750.0

    def test_multiple_reactors(self):
        wi = WhatIfOverrides.from_session_state(
            [
                {"reactor_id": "R1", "field": "retirement_year",      "value": 2028},
                {"reactor_id": "R2", "field": "pipeline_probability", "value": 0.5},
            ],
            [],
        )
        assert wi.reactor_count() == 2
        assert wi.reactor_overrides["R1"].retirement_year == 2028
        assert wi.reactor_overrides["R2"].pipeline_probability == 0.5

    def test_all_field_types_parsed(self):
        wi = WhatIfOverrides.from_session_state(
            [
                {"reactor_id": "R1", "field": "retirement_year",      "value": 2033},
                {"reactor_id": "R1", "field": "capacity_mw",          "value": 900.0},
                {"reactor_id": "R1", "field": "status",               "value": "LongTermShutdown"},
                {"reactor_id": "R1", "field": "restart_date",         "value": "2030-01-01"},
                {"reactor_id": "R1", "field": "pipeline_probability",  "value": 0.8},
                {"reactor_id": "R1", "field": "expected_online_year", "value": 2031},
            ],
            [],
        )
        ov = wi.reactor_overrides["R1"]
        assert ov.retirement_year      == 2033
        assert ov.capacity_mw          == 900.0
        assert ov.status               == "LongTermShutdown"
        assert ov.restart_date         == "2030-01-01"
        assert ov.pipeline_probability == 0.8
        assert ov.expected_online_year == 2031

    def test_synthetic_batch_parsed(self):
        wi = WhatIfOverrides.from_session_state(
            [],
            [{"region": "China", "capacity_mw": 500.0,
              "per_year": 1, "start_year": 2030, "n_years": 2}],
        )
        assert len(wi.synthetic_batches) == 1
        sb = wi.synthetic_batches[0]
        assert sb.region      == "China"
        assert sb.capacity_mw == 500.0
        assert sb.per_year    == 1
        assert sb.start_year  == 2030
        assert sb.n_years     == 2

    def test_empty_inputs_gives_empty_object(self):
        wi = WhatIfOverrides.from_session_state([], [])
        assert wi.is_empty


class TestToRawDictRoundTrip:
    """
    to_raw_dict() must reproduce exactly what the old _build_wi_overrides_dict()
    function produced, so the projection engine receives the same input.
    """

    def test_retirement_override_roundtrip(self):
        session = [{"reactor_id": "CA-22", "field": "retirement_year", "value": 2033}]
        result  = WhatIfOverrides.from_session_state(session, []).to_raw_dict()
        assert result == {"CA-22": {"retirement_year": 2033}}

    def test_pipeline_probability_roundtrip(self):
        session = [{"reactor_id": "P1", "field": "pipeline_probability", "value": 0.0}]
        result  = WhatIfOverrides.from_session_state(session, []).to_raw_dict()
        assert result == {"P1": {"pipeline_probability": 0.0}}

    def test_multiple_fields_roundtrip(self):
        session = [
            {"reactor_id": "R1", "field": "retirement_year", "value": 2028},
            {"reactor_id": "R1", "field": "capacity_mw",     "value": 750.0},
        ]
        result = WhatIfOverrides.from_session_state(session, []).to_raw_dict()
        assert result == {"R1": {"retirement_year": 2028, "capacity_mw": 750.0}}

    def test_synthetic_batch_roundtrip(self):
        batches = [{"region": "China", "capacity_mw": 500.0,
                    "per_year": 2, "start_year": 2030, "n_years": 3}]
        result  = WhatIfOverrides.from_session_state([], batches).to_raw_dict()
        assert "__synthetic__" in result
        assert result["__synthetic__"] == batches

    def test_mixed_reactor_and_synthetic_roundtrip(self):
        session = [{"reactor_id": "R1", "field": "retirement_year", "value": 2033}]
        batches = [{"region": "China", "capacity_mw": 100.0,
                    "per_year": 1, "start_year": 2029, "n_years": 1}]
        result  = WhatIfOverrides.from_session_state(session, batches).to_raw_dict()
        assert "R1"            in result
        assert "__synthetic__" in result

    def test_empty_override_excluded_from_raw_dict(self):
        """An override with all None fields must not appear in to_raw_dict()."""
        wi = WhatIfOverrides(
            reactor_overrides={"R1": ReactorOverride()}   # all fields None
        )
        assert "R1" not in wi.to_raw_dict()

    def test_multiple_reactors_roundtrip(self):
        session = [
            {"reactor_id": "R1", "field": "retirement_year",      "value": 2028},
            {"reactor_id": "R2", "field": "pipeline_probability", "value": 0.5},
        ]
        result = WhatIfOverrides.from_session_state(session, []).to_raw_dict()
        assert result["R1"] == {"retirement_year": 2028}
        assert result["R2"] == {"pipeline_probability": 0.5}


class TestInspectionHelpers:

    def test_reactor_count(self):
        session = [
            {"reactor_id": "R1", "field": "retirement_year", "value": 2028},
            {"reactor_id": "R2", "field": "retirement_year", "value": 2030},
        ]
        wi = WhatIfOverrides.from_session_state(session, [])
        assert wi.reactor_count() == 2

    def test_retirement_year_overrides_list(self):
        session = [
            {"reactor_id": "R1", "field": "retirement_year",      "value": 2028},
            {"reactor_id": "R2", "field": "pipeline_probability", "value": 0.5},
        ]
        wi    = WhatIfOverrides.from_session_state(session, [])
        pairs = wi.retirement_year_overrides()
        assert len(pairs) == 1
        assert pairs[0] == ("R1", 2028)

    def test_is_empty_false_with_reactor_override(self):
        session = [{"reactor_id": "R1", "field": "retirement_year", "value": 2028}]
        wi = WhatIfOverrides.from_session_state(session, [])
        assert not wi.is_empty

    def test_is_empty_false_with_synthetic_batch(self):
        batches = [{"region": "China", "capacity_mw": 100.0,
                    "per_year": 1, "start_year": 2030, "n_years": 1}]
        wi = WhatIfOverrides.from_session_state([], batches)
        assert not wi.is_empty
