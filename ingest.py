"""
ingest.py — Main data ingestion entry point.

Usage:
  python ingest.py            # Full ingest (skips projection)
  python ingest.py --reset    # Drop and recreate DB, then ingest
  python ingest.py --project  # Ingest + run projections for all scenarios
  python ingest.py --validate # Ingest + project + validate

Sequence:
  1. Create/reset schema
  2. PRIS tables (operating, UC, suspended, planned)
  3. Historical capacity (Table 5, Table 7)
  4. IAEA RDS-1 benchmarks
  5. WNA pipeline (Planned + Proposed net-new vs PRIS)
  6. Merge: retirement tier assignment + SMR flagging
  7. Load scenario presets
  8. (Optional) Run projections
  9. (Optional) Validate
"""
import sys
import sqlite3
import argparse
from pathlib import Path

from config import DB_PATH
from model.schema import create_schema
from model.ingest.pris import ingest_pris
from model.ingest.historical import ingest_historical
from model.ingest.benchmarks import ingest_benchmarks
from model.ingest.merge import run_merge
from model.ingest.wna_pipeline import run_wna_pipeline
from model.ingest.announced_smr import run_announced_smr
from model.scenarios import load_scenarios
from model.projection import run_projection
from model.validate import run_validation


def run_ingestion(reset: bool = False) -> sqlite3.Connection:
    print("\n" + "═" * 62)
    print("  NUCLEAR DASHBOARD — DATA INGESTION")
    print("═" * 62)

    print("\n[1/7] Creating schema...")
    create_schema(reset=reset)

    conn = sqlite3.connect(DB_PATH)

    print("\n[2/7] Ingesting PRIS reactor data...")
    ingest_pris(conn)

    print("\n[3/7] Ingesting historical capacity...")
    ingest_historical(conn)

    print("\n[4/8] Ingesting benchmarks...")
    ingest_benchmarks(conn)

    print("\n[5/8] Ingesting WNA pipeline (Planned + Proposed)...")
    run_wna_pipeline(conn)

    print("\n[5b/8] Adding named corporate SMR announcements...")
    run_announced_smr(conn)

    print("\n[6/8] Running merge (retirement tiers, SMR flags)...")
    run_merge(conn)

    print("\n[7/8] Loading scenario presets...")
    load_scenarios(conn)

    print("\n[8/8] Ingestion complete.")
    _print_summary(conn)

    return conn


def _print_summary(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    print("\n" + "─" * 62)
    print("  DATABASE SUMMARY")
    print("─" * 62)

    # Reactor counts by status
    cur.execute("""
        SELECT status, COUNT(*), ROUND(SUM(COALESCE(net_capacity_mw,0))/1000.0,1)
        FROM reactors GROUP BY status ORDER BY status
    """)
    for status, n, gw in cur.fetchall():
        print(f"  {status:<25} {n:>5} reactors   {gw or 0:>7.1f} GW")

    # Retirement tier distribution for operating fleet
    print()
    cur.execute("""
        SELECT retirement_tier, COUNT(*),
               ROUND(SUM(COALESCE(net_capacity_mw,0))/1000.0,1)
        FROM reactors WHERE status='Operating'
        GROUP BY retirement_tier ORDER BY retirement_tier
    """)
    for tier, n, gw in cur.fetchall():
        tier_label = {1:"Tier 1 (Declared)", 2:"Tier 2 (Derived)", 3:"Tier 3 (Fallback)"}
        print(f"  {tier_label.get(tier, f'Tier {tier}'):<25} {n:>5} reactors   {gw or 0:>7.1f} GW")

    # Historical years
    print()
    cur.execute("""
        SELECT MIN(year), MAX(year), COUNT(DISTINCT year)
        FROM historical_capacity WHERE region='Global'
    """)
    mn, mx, cnt = cur.fetchone()
    print(f"  Historical data: {mn}–{mx} ({cnt} years)")

    # Benchmarks
    cur.execute("SELECT COUNT(DISTINCT source) FROM benchmarks")
    n_sources = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM benchmarks")
    n_bench = cur.fetchone()[0]
    print(f"  Benchmarks: {n_bench} points from {n_sources} sources")

    print("─" * 62)


def main():
    parser = argparse.ArgumentParser(
        description="Nuclear dashboard data ingestion pipeline"
    )
    parser.add_argument("--reset",    action="store_true", help="Reset DB before ingesting")
    parser.add_argument("--project",  action="store_true", help="Run projections after ingestion")
    parser.add_argument("--validate", action="store_true", help="Validate after projection")
    args = parser.parse_args()

    conn = run_ingestion(reset=args.reset)

    if args.project or args.validate:
        print("\n[Projections] Running all scenario projections...")
        from model.api import get_all_scenarios
        import pandas as pd
        scenarios_df = get_all_scenarios()
        for _, row in scenarios_df.iterrows():
            sid = row["scenario_id"]
            print(f"  Computing scenario: {sid}...")
            results = run_projection(conn, sid)
            global_2050 = next(
                (r["capacity_operating_gw"] for r in results
                 if r["year"] == 2050 and r["region"] == "Global"),
                None
            )
            global_2040 = next(
                (r["capacity_operating_gw"] for r in results
                 if r["year"] == 2040 and r["region"] == "Global"),
                None
            )
            print(f"    2040: {global_2040} GW | 2050: {global_2050} GW")

    conn.close()

    if args.validate:
        run_validation(DB_PATH)


if __name__ == "__main__":
    main()
