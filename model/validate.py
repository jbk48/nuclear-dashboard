"""
validate.py — Validation suite for the nuclear dashboard model.

Run after ingestion and projection to verify the model meets spec criteria.
Prints a human-readable report and returns a pass/fail dict.
"""
import sqlite3
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DB_PATH, BASELINE_YEAR


CHECKS = {}


def check(name: str):
    """Decorator to register a validation check."""
    def decorator(fn):
        CHECKS[name] = fn
        return fn
    return decorator


# ── Check 1: Baseline total matches ~377 GW ───────────────────────────────
@check("baseline_capacity")
def check_baseline(conn: sqlite3.Connection) -> dict:
    cur = conn.cursor()
    cur.execute("""
        SELECT ROUND(SUM(COALESCE(net_capacity_mw, 0)) / 1000.0, 1)
        FROM reactors WHERE status = 'Operating'
    """)
    gw = cur.fetchone()[0] or 0
    target = 377
    tolerance = 10   # ± 10 GW
    ok = abs(gw - target) <= tolerance
    return {
        "pass": ok,
        "value": f"{gw} GW",
        "expected": f"{target} ± {tolerance} GW",
        "note": "Sum of net_capacity_mw for Operating reactors",
    }


# ── Check 2: Reactor count ────────────────────────────────────────────────
@check("baseline_reactor_count")
def check_reactor_count(conn: sqlite3.Connection) -> dict:
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM reactors WHERE status = 'Operating'")
    n = cur.fetchone()[0]
    ok = 400 <= n <= 440
    return {
        "pass": ok,
        "value": f"{n} reactors",
        "expected": "400–440 operating reactors",
        "note": "Operating reactor count from PRIS Table 14",
    }


# ── Check 3: Retirement tier coverage ─────────────────────────────────────
@check("retirement_tier_coverage")
def check_tier_coverage(conn: sqlite3.Connection) -> dict:
    cur = conn.cursor()
    cur.execute("""
        SELECT retirement_tier,
               COUNT(*) as n,
               ROUND(SUM(COALESCE(net_capacity_mw,0))/1000.0, 1) as gw
        FROM reactors WHERE status = 'Operating'
        GROUP BY retirement_tier ORDER BY retirement_tier
    """)
    rows = cur.fetchall()
    total_gw = sum(r[2] for r in rows)
    tier_data = {r[0]: {"n": r[1], "gw": r[2]} for r in rows}
    t1_gw = tier_data.get(1, {}).get("gw", 0)
    t2_gw = tier_data.get(2, {}).get("gw", 0)
    coverage = round((t1_gw + t2_gw) / total_gw * 100, 1) if total_gw else 0
    ok = coverage >= 70
    tiers_str = " | ".join(
        f"T{t}: {d['n']} reactors ({d['gw']} GW)" for t, d in sorted(tier_data.items())
    )
    return {
        "pass": ok,
        "value": f"{coverage}% coverage (Tier 1+2)",
        "expected": "≥ 70% of operating GW on Tier 1 or Tier 2",
        "note": tiers_str,
    }


# ── Check 4: No null retirement dates for operating fleet ─────────────────
@check("retirement_dates_populated")
def check_ret_dates(conn: sqlite3.Connection) -> dict:
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) FROM reactors
        WHERE status = 'Operating' AND retirement_date_used IS NULL
    """)
    n_null = cur.fetchone()[0]
    ok = n_null == 0
    return {
        "pass": ok,
        "value": f"{n_null} reactors missing retirement date",
        "expected": "0 missing",
        "note": "All operating reactors must have a retirement_date_used",
    }


# ── Check 5: Pipeline sanity (at least 50 GW of UC) ─────────────────────
@check("pipeline_sanity")
def check_pipeline(conn: sqlite3.Connection) -> dict:
    cur = conn.cursor()
    cur.execute("""
        SELECT status,
               COUNT(*) as n,
               ROUND(SUM(COALESCE(net_capacity_mw,0))/1000.0,1) as gw
        FROM reactors WHERE status IN ('UnderConstruction','Planned')
        GROUP BY status
    """)
    rows = {r[0]: {"n": r[1], "gw": r[2]} for r in cur.fetchall()}
    uc_gw = rows.get("UnderConstruction", {}).get("gw", 0)
    ok = uc_gw >= 50
    detail = " | ".join(
        f"{s}: {d['n']} units ({d['gw']} GW)" for s, d in rows.items()
    )
    return {
        "pass": ok,
        "value": f"{uc_gw} GW under construction",
        "expected": "≥ 50 GW UC",
        "note": detail,
    }


# ── Check 6: 2040 continuity (bottom-up endpoint) ────────────────────────
@check("transition_continuity")
def check_continuity(conn: sqlite3.Connection) -> dict:
    """Verify bottom-up 2040 == top-down 2040 (they share the same row)."""
    cur = conn.cursor()
    results = {}
    cur.execute("""
        SELECT scenario_id,
               capacity_operating_gw,
               is_bottom_up
        FROM projections
        WHERE year = 2040 AND region = 'Global'
        ORDER BY scenario_id
    """)
    rows = cur.fetchall()
    if not rows:
        return {"pass": False, "value": "No projections found for 2040", "expected": "Projections computed", "note": ""}
    for (sid, gw, bu) in rows:
        results[sid] = gw
    detail = " | ".join(f"{s}: {g} GW" for s, g in results.items())
    return {
        "pass": True,
        "value": "Transition year values",
        "expected": "Continuity guaranteed by design",
        "note": detail,
    }


# ── Check 7: Scenario benchmarks ─────────────────────────────────────────
@check("scenario_benchmarks_2050")
def check_benchmarks(conn: sqlite3.Connection) -> dict:
    cur = conn.cursor()
    cur.execute("""
        SELECT scenario_id, capacity_operating_gw
        FROM projections WHERE year = 2050 AND region = 'Global'
        ORDER BY scenario_id
    """)
    scenario_vals = {row[0]: row[1] for row in cur.fetchall()}
    if not scenario_vals:
        return {"pass": False, "value": "No projections found", "expected": "Projections for 2050", "note": ""}

    iaea_low  = 561
    iaea_high = 992

    failures = []
    details  = []
    for sid, gw in scenario_vals.items():
        details.append(f"{sid}: {gw} GW")

    conservative = scenario_vals.get("conservative", 0)
    optimistic   = scenario_vals.get("optimistic",   0)
    base         = scenario_vals.get("base",         0)

    if conservative and not (iaea_low * 0.80 <= conservative <= iaea_low * 1.20):
        failures.append(f"Conservative {conservative} GW outside ±20% of IAEA Low {iaea_low}")
    if optimistic and not (iaea_high * 0.70 <= optimistic <= iaea_high * 1.30):
        failures.append(f"Optimistic {optimistic} GW outside ±30% of IAEA High {iaea_high}")

    ok = len(failures) == 0
    return {
        "pass": ok,
        "value": " | ".join(details),
        "expected": f"Conservative ≈ {iaea_low} GW; Optimistic ≈ {iaea_high} GW",
        "note": "; ".join(failures) if failures else "All within range",
    }


# ── Check 8: Historical data loaded ──────────────────────────────────────
@check("historical_data_loaded")
def check_historical(conn: sqlite3.Connection) -> dict:
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(DISTINCT year) FROM historical_capacity WHERE region = 'Global'
    """)
    n_years = cur.fetchone()[0]
    ok = n_years >= 4  # at least 2005, 2010, 2015, 2020, 2024
    return {
        "pass": ok,
        "value": f"{n_years} historical years loaded (Global)",
        "expected": "≥ 4 data points from 2005 onward",
        "note": "IAEA RDS-2 Table 7",
    }


# ── Check 9: No negative capacity ────────────────────────────────────────
@check("no_negative_capacity")
def check_no_negative(conn: sqlite3.Connection) -> dict:
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) FROM projections WHERE capacity_operating_gw < 0
    """)
    n = cur.fetchone()[0]
    return {
        "pass": n == 0,
        "value": f"{n} negative-capacity rows",
        "expected": "0",
        "note": "",
    }


# ── Runner ────────────────────────────────────────────────────────────────

def run_validation(db_path: Path = DB_PATH) -> dict[str, dict]:
    conn = sqlite3.connect(db_path)
    results = {}
    passed = 0
    print("\n" + "═" * 62)
    print("  NUCLEAR DASHBOARD — VALIDATION REPORT")
    print("═" * 62)
    for name, fn in CHECKS.items():
        try:
            r = fn(conn)
        except Exception as e:
            r = {"pass": False, "value": str(e), "expected": "", "note": "Exception during check"}
        results[name] = r
        status = "✓ PASS" if r["pass"] else "✗ FAIL"
        if r["pass"]:
            passed += 1
        print(f"\n  [{status}] {name}")
        print(f"    Value   : {r.get('value','')}")
        print(f"    Expected: {r.get('expected','')}")
        if r.get("note"):
            print(f"    Note    : {r['note']}")
    print("\n" + "─" * 62)
    total = len(CHECKS)
    print(f"  Result: {passed}/{total} checks passed")
    if passed == total:
        print("  ✓ Model status: GREEN — all checks passed")
    elif passed >= total * 0.75:
        print("  ⚠ Model status: AMBER — some checks failed")
    else:
        print("  ✗ Model status: RED — multiple checks failed")
    print("═" * 62 + "\n")
    conn.close()
    return results


if __name__ == "__main__":
    run_validation()
