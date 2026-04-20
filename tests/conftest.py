"""
conftest.py — Shared pytest fixtures for nuclear-dashboard model tests.

Creates a fully-in-memory SQLite database (using the production schema DDL)
populated with a small, deterministic set of reactors and scenarios.
Each test function gets a fresh connection so tests are fully isolated.

Test data summary
─────────────────
Scenarios
  'base'    CurrentPolicy, all pipeline rates 1.0, 0 GW/yr post-2040, transition 2040
  'test_sc' same as base (used as the primary test subject)

Retirement rules
  US    max_life 80 yr, ext_unit 20 yr
  CHINA max_life 60 yr, ext_unit 20 yr

Operating reactors
  US_OP_1    1 000 MW  United States  Tier 1  retires 2060  (well into the future)
  US_OP_2      500 MW  United States  Tier 1  retires 2030  (used to test retirement)
  US_TIER2     600 MW  United States  Tier 2  comm 1975, ret_used 2015 (past — will
                                               be advanced past BASELINE_YEAR 2024)

Pipeline reactors
  CHINA_UC_1  1 000 MW  China          UnderConstruction  online 2028
  US_PLAN_1     800 MW  United States  Planned            online 2032

PermanentShutdown
  US_PERM_1    700 MW  United States  (for restart / fleet-promotion tests)

Expected global capacity under 'test_sc' (rates = 1.0, no delay, 0 post-2040 growth):
  2024  →  2.1 GW  (US_OP_1 + US_OP_2 + US_TIER2; CHINA_UC_1 not yet online)
  2028  →  3.1 GW  (+ CHINA_UC_1)
  2030  →  2.6 GW  (US_OP_2 retires)
  2032  →  3.4 GW  (+ US_PLAN_1)
"""

import sqlite3
import sys
from pathlib import Path

import pytest

# Make the repo root importable so model.* and config work
sys.path.insert(0, str(Path(__file__).parent.parent))

from model.schema import DDL  # noqa: E402  (must come after sys.path insert)


def _make_db() -> sqlite3.Connection:
    """Return an in-memory SQLite connection pre-loaded with test fixtures."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL)

    # ── Scenarios ──────────────────────────────────────────────────────────
    _sc_fields = """
        scenario_id, name, extension_policy_global,
        pipeline_uc_realization_rate, pipeline_planned_realization_rate,
        pipeline_proposed_realization_rate,
        construction_delay_adder_years, post2040_global_growth_gw_per_year,
        transition_year
    """
    _sc_values = "(?, ?, ?, ?, ?, ?, ?, ?, ?)"

    # 'base' is required by get_regional_post2040() as a fallback
    conn.execute(
        f"INSERT INTO scenarios ({_sc_fields}) VALUES {_sc_values}",
        ("base", "Base", "CurrentPolicy", 1.0, 1.0, 1.0, 0, 0.0, 2040),
    )
    conn.execute(
        f"INSERT INTO scenarios ({_sc_fields}) VALUES {_sc_values}",
        ("test_sc", "Test", "CurrentPolicy", 1.0, 1.0, 1.0, 0, 0.0, 2040),
    )

    # ── Retirement rules ───────────────────────────────────────────────────
    _rr = """
        INSERT INTO retirement_rules (
            country, baseline_license_years, typical_extension_years,
            total_assumed_life_years, max_possible_life_years, extension_unit_years
        ) VALUES (?, ?, ?, ?, ?, ?)
    """
    conn.execute(_rr, ("US",    40, 20, 60, 80, 20))
    conn.execute(_rr, ("CHINA", 40, 20, 60, 60, 20))

    # ── Operating / LTS reactors ───────────────────────────────────────────
    _r = """
        INSERT INTO reactors (
            reactor_id, name, country, region, status,
            net_capacity_mw, commercial_operation_date,
            retirement_date_used, retirement_tier, is_smr
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    # Tier 1 — declared dates, never adjusted by extension policy
    conn.execute(_r, ("US_OP_1", "US Operating 1", "US", "United States",
                      "Operating", 1000.0, "1990-01-01", "2060-01-01", 1, 0))
    conn.execute(_r, ("US_OP_2", "US Operating 2", "US", "United States",
                      "Operating", 500.0, "1985-01-01", "2030-01-01", 1, 0))

    # Tier 2 — past date (2015), will be advanced by advance_past_baseline
    # comm 1975, max_life 80 → US rules advance to 2035 at baseline,
    # ExtendedOperations → 1975+80=2055
    conn.execute(_r, ("US_TIER2", "US Tier2 Reactor", "US", "United States",
                      "Operating", 600.0, "1975-01-01", "2015-01-01", 2, 0))

    # ── Pipeline reactors ──────────────────────────────────────────────────
    _p = """
        INSERT INTO reactors (
            reactor_id, name, country, region, status,
            net_capacity_mw, expected_online_year, is_smr
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """
    conn.execute(_p, ("CHINA_UC_1", "China UC 1",  "CHINA", "China",
                      "UnderConstruction", 1000.0, 2028, 0))
    conn.execute(_p, ("US_PLAN_1",  "US Planned 1", "US",   "United States",
                      "Planned", 800.0, 2032, 0))

    # ── PermanentShutdown reactor ──────────────────────────────────────────
    conn.execute("""
        INSERT INTO reactors (
            reactor_id, name, country, region, status, net_capacity_mw, is_smr
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """, ("US_PERM_1", "US Permanent 1", "US", "United States",
          "PermanentShutdown", 700.0, 0))

    conn.commit()
    return conn


@pytest.fixture
def db():
    """Fresh in-memory DB for each test — fully isolated."""
    conn = _make_db()
    yield conn
    conn.close()
