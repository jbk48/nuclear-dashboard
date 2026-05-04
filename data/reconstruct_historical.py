"""
Reconstruct historical nuclear capacity from reactor fleet data.

Steps:
1. Add missing US retired reactors (2005-2022) to the reactors table
2. Fix missing shutdown dates: France Fessenheim, South Korea Kori-1 / Wolsong-1, Taiwan
3. Run year-by-year reconstruction for 2005-2024 by region
4. Compare reconstructed values against current interpolated historical_capacity
5. Update historical_capacity with reconstructed values
"""

import sqlite3
import sys
from datetime import date

DB_PATH = "/Users/jackkochansky/Documents/AI/nuclear-dashboard/data/nuclear.db"

# ─── 1. US Retired Reactors (post-2005, verified from NRC / IAEA records) ─────
# Fields: reactor_id, name, country, region, status, net_cap_mw,
#         commercial_operation_date, actual_shutdown_date, reactor_type, reactor_model
US_RETIRED = [
    # id           name                   COD         Shutdown     MWe   type      model
    ("US-RET-01", "CRYSTAL RIVER-3",     "1977-03",  "2013-02-05", 860,  "PWR",   "B&W LOWERD"),
    ("US-RET-02", "KEWAUNEE",            "1974-06",  "2013-05-07", 556,  "PWR",   "WH 2-LOOP"),
    ("US-RET-03", "SAN ONOFRE-2",        "1983-08",  "2013-06-07", 1070, "PWR",   "CE 2-LOOP"),
    ("US-RET-04", "SAN ONOFRE-3",        "1984-04",  "2013-06-07", 1080, "PWR",   "CE 2-LOOP"),
    ("US-RET-05", "VERMONT YANKEE",      "1972-11",  "2014-12-29", 620,  "BWR",   "GE-4"),
    ("US-RET-06", "FORT CALHOUN",        "1973-09",  "2016-10-24", 479,  "PWR",   "CE 1-LOOP"),
    ("US-RET-07", "OYSTER CREEK",        "1969-12",  "2018-09-17", 636,  "BWR",   "GE-1"),
    ("US-RET-08", "PILGRIM-1",           "1972-12",  "2019-05-31", 688,  "BWR",   "GE-3"),
    ("US-RET-09", "THREE MILE ISLAND-1", "1974-09",  "2019-09-20", 837,  "PWR",   "B&W LOWERD"),
    ("US-RET-10", "INDIAN POINT-2",      "1974-08",  "2020-04-30", 1028, "PWR",   "WH 4-LOOP"),
    ("US-RET-11", "DUANE ARNOLD",        "1975-02",  "2020-08-27", 601,  "BWR",   "GE-4"),
    ("US-RET-12", "INDIAN POINT-3",      "1976-08",  "2021-04-30", 1041, "PWR",   "WH 4-LOOP"),
    ("US-RET-13", "PALISADES",           "1971-12",  "2022-05-20", 805,  "PWR",   "CE 2-LOOP"),
]

# ─── 2. Missing Shutdown Dates ─────────────────────────────────────────────────
SHUTDOWN_DATE_FIXES = [
    # (name, actual_shutdown_date)
    ("FESSENHEIM-1",  "2020-02-22"),   # France — first French reactor closure
    ("FESSENHEIM-2",  "2020-06-29"),   # France
    ("KORI-1",        "2017-06-19"),   # South Korea (first Korean closure)
    ("WOLSONG-1",     "2019-12-24"),   # South Korea (contested, confirmed 2019)
    # Taiwan — all five phased out 2018-2023
    ("CHINSHAN-1",    "2018-12-05"),
    ("CHINSHAN-2",    "2019-07-17"),
    ("KUOSHENG-1",    "2021-07-01"),
    ("KUOSHENG-2",    "2023-03-14"),
    ("MAANSHAN-1",    "2023-05-17"),
]


def add_us_retired(conn):
    c = conn.cursor()
    added = 0
    skipped = 0
    for (rid, name, cod, shutdown, mw, rtype, rmodel) in US_RETIRED:
        c.execute("SELECT reactor_id FROM reactors WHERE reactor_id=?", (rid,))
        if c.fetchone():
            skipped += 1
            continue
        c.execute("""
            INSERT INTO reactors
              (reactor_id, name, country, region, status,
               net_capacity_mw, reactor_type, reactor_model,
               commercial_operation_date, actual_shutdown_date,
               data_source)
            VALUES (?,?,?,?,?, ?,?,?, ?,?, ?)
        """, (
            rid, name, "United States", "United States", "PermanentShutdown",
            mw, rtype, rmodel,
            cod, shutdown,
            "NRC_PRIS_manual"
        ))
        added += 1
    conn.commit()
    print(f"  US retired reactors: {added} added, {skipped} already present")


def fix_shutdown_dates(conn):
    c = conn.cursor()
    updated = 0
    for (name, sd) in SHUTDOWN_DATE_FIXES:
        c.execute("""
            UPDATE reactors SET actual_shutdown_date=?
            WHERE name=? AND actual_shutdown_date IS NULL
        """, (sd, name))
        if c.rowcount:
            print(f"  Fixed shutdown date: {name} → {sd}")
            updated += 1
        else:
            c.execute("SELECT actual_shutdown_date FROM reactors WHERE name=?", (name,))
            row = c.fetchone()
            if row is None:
                print(f"  WARNING: reactor '{name}' not found in DB")
            else:
                print(f"  {name} already has date {row[0]}, skipped")
    conn.commit()
    print(f"  Shutdown dates fixed: {updated}")


def reconstruct(conn):
    """
    For each year 2005-2024 and each region:
    Sum net_capacity_mw/1000 for reactors where:
      - commercial_operation_date year <= year (reactor was online)
      - AND one of:
        (a) status in ('Operating', 'Restarted')   [still running today]
        (b) status = 'PermanentShutdown' AND actual_shutdown_date year > year
        (c) status = 'LongTermShutdown' AND year < 2011  [Japan approximation]
           OR (status = 'LongTermShutdown' AND long_term_shutdown_date IS NOT NULL
               AND CAST(SUBSTR(long_term_shutdown_date,1,4) AS INT) > year)
        (d) status = 'Restarted' AND year >= restart_date year  [already handled by (a)]
             OR (status = 'Restarted' AND year < 2011)          [was operating pre-LTS]

    Japan-specific logic (Fukushima):
    - All Japan reactors that are LTS or Restarted are assumed to have gone
      offline starting 2011. Restarted reactors count again from restart_year.
    - This is an approximation; some KK units were already in pre-Fukushima
      maintenance LTS, but for 2005-2010 they were broadly all contributing.
    """
    c = conn.cursor()
    years = list(range(2005, 2025))

    # Pull all reactor data we need
    c.execute("""
        SELECT reactor_id, name, country, region, status,
               net_capacity_mw,
               commercial_operation_date,
               actual_shutdown_date,
               long_term_shutdown_date,
               restart_date
        FROM reactors
        WHERE status IN ('Operating','PermanentShutdown','LongTermShutdown','Restarted')
          AND net_capacity_mw IS NOT NULL
          AND commercial_operation_date IS NOT NULL
    """)
    reactors = c.fetchall()

    def year_of(d):
        if d is None:
            return None
        try:
            return int(str(d)[:4])
        except Exception:
            return None

    results = {}  # (year, region) -> (capacity_gw, count)

    for row in reactors:
        (rid, name, country, region, status,
         cap_mw, cod, shutdown_date, lts_date, restart_date_val) = row

        cod_year = year_of(cod)
        if cod_year is None:
            continue

        shutdown_year = year_of(shutdown_date)
        lts_year = year_of(lts_date)
        restart_year = year_of(restart_date_val)

        is_japan = (country in ("JAPAN", "Japan"))

        for yr in years:
            if cod_year > yr:
                continue  # not commissioned yet

            active = False

            if status == "Operating":
                active = True

            elif status == "PermanentShutdown":
                if shutdown_year is None:
                    # Unknown shutdown date: conservatively exclude
                    # (we don't know when it shut down)
                    active = False
                else:
                    active = (shutdown_year > yr)

            elif status == "LongTermShutdown":
                if is_japan:
                    # Japan post-Fukushima approximation:
                    # all LTS reactors went offline effectively 2011
                    active = (yr < 2011)
                elif lts_year is not None:
                    active = (lts_year > yr)
                else:
                    # Ukraine Chernobyl-era: shut pre-2005, so irrelevant for 2005+
                    # Conservatively exclude if unknown
                    active = False

            elif status == "Restarted":
                if is_japan:
                    # Was operating before 2011; offline 2011 to restart_year-1
                    if yr < 2011:
                        active = True
                    elif restart_year is not None and yr >= restart_year:
                        active = True
                    else:
                        active = False
                else:
                    # Non-Japan restarted: treat as operating (status = Restarted means it's back online)
                    active = True

            if active:
                key = (yr, region)
                if key not in results:
                    results[key] = [0.0, 0]
                results[key][0] += cap_mw / 1000.0
                results[key][1] += 1

    return results


def compare_and_report(conn, reconstructed):
    c = conn.cursor()
    c.execute("SELECT year, region, capacity_gw FROM historical_capacity ORDER BY region, year")
    current = {(r[0], r[1]): r[2] for r in c.fetchall()}

    regions = sorted(set(r for (_, r) in current.keys()))

    print("\n" + "="*90)
    print(f"{'REGION':<26} {'YEAR':<6} {'INTERP (GW)':>12} {'RECONST (GW)':>13} {'DELTA (GW)':>11}  {'NOTE'}")
    print("="*90)

    big_gaps = []
    all_rows = []

    for region in regions:
        if region == "Global":
            continue
        for yr in range(2005, 2025):
            interp = current.get((yr, region))
            rec_val, rec_count = reconstructed.get((yr, region), (None, 0))
            if interp is None:
                continue

            if rec_val is not None:
                delta = rec_val - interp
                note = ""
                if abs(delta) >= 2.0:
                    note = "⚠️  LARGE GAP"
                    big_gaps.append((region, yr, interp, rec_val, delta))
                all_rows.append((region, yr, interp, rec_val, delta, note))
            else:
                all_rows.append((region, yr, interp, None, None, "— no fleet data"))

    # Print only years with notable differences or boundary years
    printed_regions = set()
    for (region, yr, interp, rec_val, delta, note) in all_rows:
        show = False
        if abs(delta or 0) >= 0.5:
            show = True
        if yr in (2005, 2010, 2015, 2020, 2024):
            show = True
        if note and "no fleet" in note:
            show = True
        if show:
            if region not in printed_regions:
                print(f"\n  {region}")
                printed_regions.add(region)
            if rec_val is not None:
                print(f"    {'':24} {yr:<6} {interp:>12.3f} {rec_val:>13.3f} {delta:>+11.3f}  {note}")
            else:
                print(f"    {'':24} {yr:<6} {interp:>12.3f} {'—':>13}  {note}")

    # Summary of large gaps
    if big_gaps:
        print("\n" + "="*90)
        print("⚠️  JARRING DIFFERENCES (|delta| >= 2 GW) — review before updating DB:")
        print("="*90)
        for (region, yr, interp, rec, delta) in sorted(big_gaps, key=lambda x: abs(x[4]), reverse=True):
            print(f"  {region:<26} {yr}  interp={interp:.2f}  recon={rec:.2f}  Δ={delta:+.2f} GW")

    return all_rows


def update_historical_capacity(conn, reconstructed, all_rows):
    """
    Replace historical_capacity values with reconstructed ones for regions where
    the reconstruction is considered reliable.

    Reliable: United States, France, Rest of Western Europe, East Asia (partial),
              Eastern Europe, Canada & Mexico, South Asia, Emerging & Rest
    Flag-only (data gaps too large): Russia (missing retired reactors)
    """
    c = conn.cursor()

    # Regions we will update (we have good reactor fleet coverage)
    update_regions = {
        "United States",
        "France",
        "Rest of Western Europe",
        "East Asia",          # note Japan approximation
        "Eastern Europe",
        "Canada & Mexico",
        "United Kingdom",
        "South Asia",
        "Southeast Asia",
        "Emerging & Rest",
    }
    # Russia and China: missing retired/decommissioned reactors — do not update
    skip_regions = {"Russia", "China"}

    updated = 0
    skipped = 0
    for (region, yr, interp, rec_val, delta, note) in all_rows:
        if rec_val is None:
            skipped += 1
            continue
        if region in skip_regions:
            skipped += 1
            continue
        if region not in update_regions:
            skipped += 1
            continue

        # Count reactors for this region/year
        _, rec_count = reconstructed.get((yr, region), (None, 0))

        c.execute("""
            UPDATE historical_capacity
            SET capacity_gw=?, num_reactors=?, source='PRIS_fleet_reconstruction'
            WHERE year=? AND region=?
        """, (round(rec_val, 3), rec_count, yr, region))
        updated += 1

    conn.commit()
    print(f"\nUpdated {updated} historical_capacity rows from fleet reconstruction.")
    print(f"Skipped {skipped} rows (Russia, China, or no fleet data).")


def main():
    conn = sqlite3.connect(DB_PATH)

    print("\n── Step 1: Add US retired reactors ──────────────────────────────────────")
    add_us_retired(conn)

    print("\n── Step 2: Fix missing shutdown dates ───────────────────────────────────")
    fix_shutdown_dates(conn)

    print("\n── Step 3: Reconstruct capacity from fleet ──────────────────────────────")
    reconstructed = reconstruct(conn)
    total_regions = len(set(r for (_, r) in reconstructed))
    total_pts = len(reconstructed)
    print(f"  Reconstructed {total_pts} region-year points across {total_regions} regions")

    print("\n── Step 4: Compare interpolated vs reconstructed ────────────────────────")
    all_rows = compare_and_report(conn, reconstructed)

    print("\n── Step 5: Update historical_capacity ───────────────────────────────────")
    update_historical_capacity(conn, reconstructed, all_rows)

    # Recompute Global as sum of all regions
    c = conn.cursor()
    for yr in range(2005, 2025):
        c.execute("""
            SELECT SUM(capacity_gw), SUM(num_reactors) FROM historical_capacity
            WHERE year=? AND region != 'Global'
        """, (yr,))
        row = c.fetchone()
        if row and row[0]:
            c.execute("""
                UPDATE historical_capacity SET capacity_gw=?, num_reactors=?
                WHERE year=? AND region='Global'
            """, (round(row[0], 3), row[1], yr))
    conn.commit()
    print("  Global totals recomputed from regional sums.")

    # Print final US comparison
    print("\n── US Historical Capacity: Interpolated vs Reconstructed ────────────────")
    c.execute("""
        SELECT h.year,
               h.capacity_gw AS new_val,
               h.source
        FROM historical_capacity h
        WHERE region='United States'
        ORDER BY year
    """)
    rows = c.fetchall()
    print(f"  {'Year':<6} {'Capacity (GW)':>14}  {'Source'}")
    prev = None
    for (yr, cap, src) in rows:
        delta_str = ""
        if prev is not None:
            d = cap - prev
            delta_str = f"  ({d:+.3f})"
        print(f"  {yr:<6} {cap:>14.3f}{delta_str}  {src}")
        prev = cap

    conn.close()
    print("\nDone. historical_capacity updated in nuclear.db.")


if __name__ == "__main__":
    main()
