"""
Add UK and Russia retired reactors to the reactors table,
then re-run fleet reconstruction for those two regions only
and update historical_capacity.

UK: 8 Magnox (closed 2006-2012) + 6 AGR (closed 2021-2022)
Russia: 4 VVER/RBMK units (closed 2016-2020)
"""
import sqlite3

DB_PATH = "/Users/jackkochansky/Documents/AI/nuclear-dashboard/data/nuclear.db"

# ─── UK Retired ───────────────────────────────────────────────────────────────
# (id, name, net_mw, cod, shutdown, rtype, rmodel)
UK_RETIRED = [
    # Magnox stations still operating in 2005
    ("UK-RET-01", "DUNGENESS A-1",   225, "1965-03", "2006-12-31", "GCR", "MAGNOX"),
    ("UK-RET-02", "DUNGENESS A-2",   225, "1965-12", "2006-12-31", "GCR", "MAGNOX"),
    ("UK-RET-03", "SIZEWELL A-1",    210, "1966-03", "2006-12-31", "GCR", "MAGNOX"),
    ("UK-RET-04", "SIZEWELL A-2",    210, "1966-09", "2006-12-31", "GCR", "MAGNOX"),
    ("UK-RET-05", "OLDBURY A-1",     217, "1968-01", "2012-02-29", "GCR", "MAGNOX"),
    ("UK-RET-06", "OLDBURY A-2",     217, "1968-09", "2012-06-30", "GCR", "MAGNOX"),
    ("UK-RET-07", "WYLFA-1",         490, "1971-11", "2012-04-25", "GCR", "MAGNOX"),
    ("UK-RET-08", "WYLFA-2",         490, "1971-11", "2012-12-30", "GCR", "MAGNOX"),
    # AGR stations closed 2021-2022
    ("UK-RET-09", "DUNGENESS B-1",   545, "1983-04", "2021-06-23", "AGR", "AGR"),
    ("UK-RET-10", "DUNGENESS B-2",   545, "1985-09", "2021-08-31", "AGR", "AGR"),
    ("UK-RET-11", "HUNTERSTON B-1",  490, "1976-02", "2022-01-07", "AGR", "AGR"),
    ("UK-RET-12", "HUNTERSTON B-2",  490, "1977-03", "2022-01-26", "AGR", "AGR"),
    ("UK-RET-13", "HINKLEY POINT B-1", 475, "1976-10", "2022-08-01", "AGR", "AGR"),
    ("UK-RET-14", "HINKLEY POINT B-2", 475, "1976-09", "2022-08-01", "AGR", "AGR"),
]

# ─── Russia Retired ───────────────────────────────────────────────────────────
RU_RETIRED = [
    ("RU-RET-01", "NOVOVORONEZH-3", 385, "1971-06", "2016-10-16", "PWR", "VVER-440"),
    ("RU-RET-02", "NOVOVORONEZH-4", 385, "1972-12", "2017-02-28", "PWR", "VVER-440"),
    ("RU-RET-03", "LENINGRAD-1",    925, "1974-12", "2018-12-21", "LWGR", "RBMK-1000"),
    ("RU-RET-04", "LENINGRAD-2",    925, "1976-04", "2020-11-10", "LWGR", "RBMK-1000"),
]


def insert_retired(conn, reactors, country, region):
    c = conn.cursor()
    added = skipped = 0
    for (rid, name, mw, cod, shutdown, rtype, rmodel) in reactors:
        c.execute("SELECT reactor_id FROM reactors WHERE reactor_id=?", (rid,))
        if c.fetchone():
            skipped += 1
            continue
        c.execute("""
            INSERT INTO reactors
              (reactor_id, name, country, region, status,
               net_capacity_mw, reactor_type, reactor_model,
               commercial_operation_date, actual_shutdown_date, data_source)
            VALUES (?,?,?,?,?, ?,?,?, ?,?, ?)
        """, (rid, name, country, region, "PermanentShutdown",
              mw, rtype, rmodel, cod, shutdown, "IAEA_PRIS_manual"))
        added += 1
    conn.commit()
    print(f"  {country}: {added} added, {skipped} already present")


def reconstruct_region(conn, region):
    """Return {year: (capacity_gw, count)} for a single region."""
    c = conn.cursor()
    c.execute("""
        SELECT reactor_id, status, net_capacity_mw,
               commercial_operation_date, actual_shutdown_date,
               long_term_shutdown_date, restart_date
        FROM reactors
        WHERE region=?
          AND status IN ('Operating','PermanentShutdown','LongTermShutdown','Restarted')
          AND net_capacity_mw IS NOT NULL
          AND commercial_operation_date IS NOT NULL
    """, (region,))
    reactors = c.fetchall()

    def yr(d):
        return int(d[:4]) if d else None

    results = {}
    for year in range(2005, 2025):
        cap_mw = 0.0
        count = 0
        for (rid, status, mw, cod, shutdown, lts_date, restart_date) in reactors:
            cod_year = yr(cod)
            if cod_year is None or cod_year > year:
                continue
            active = False
            if status == "Operating":
                active = True
            elif status == "Restarted":
                active = True
            elif status == "PermanentShutdown":
                sd = yr(shutdown)
                active = sd is not None and sd > year
            elif status == "LongTermShutdown":
                ld = yr(lts_date)
                active = ld is not None and ld > year
            if active:
                cap_mw += mw
                count += 1
        results[year] = (round(cap_mw / 1000.0, 3), count)
    return results


def update_region(conn, region, recon):
    c = conn.cursor()
    for year, (cap_gw, count) in recon.items():
        c.execute("""
            UPDATE historical_capacity
            SET capacity_gw=?, num_reactors=?, source='PRIS_fleet_reconstruction'
            WHERE year=? AND region=?
        """, (cap_gw, count, year, region))
    conn.commit()


def recompute_global(conn):
    c = conn.cursor()
    for yr in range(2005, 2025):
        c.execute("""SELECT SUM(capacity_gw) FROM historical_capacity
                     WHERE year=? AND region != 'Global'""", (yr,))
        s = c.fetchone()[0]
        if s:
            c.execute("""UPDATE historical_capacity SET capacity_gw=?
                         WHERE year=? AND region='Global'""",
                      (round(s, 3), yr))
    conn.commit()


def compare(old_rows, new_rows, region):
    print(f"\n  {region}:")
    print(f"    {'Year':<6} {'Old (GW)':>10} {'New (GW)':>10} {'Delta':>8}  Note")
    for yr in range(2005, 2025):
        old = old_rows.get(yr)
        new, cnt = new_rows.get(yr, (None, 0))
        if old is None or new is None:
            continue
        d = new - old
        flag = " ← step" if abs(d) >= 0.3 else ""
        print(f"    {yr:<6} {old:>10.3f} {new:>10.3f} {d:>+8.3f}{flag}")


def main():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    print("── Step 1: Add retired reactors ─────────────────────────────────────────")
    insert_retired(conn, UK_RETIRED, "United Kingdom", "United Kingdom")
    insert_retired(conn, RU_RETIRED, "RUSSIA",         "Russia")

    print("\n── Step 2: Reconstruct & compare ────────────────────────────────────────")
    for region in ("United Kingdom", "Russia"):
        # Snapshot old values
        c.execute("SELECT year, capacity_gw FROM historical_capacity WHERE region=?", (region,))
        old = {row[0]: row[1] for row in c.fetchall()}

        new = reconstruct_region(conn, region)
        compare(old, new, region)
        update_region(conn, region, new)

    recompute_global(conn)
    print("\n── Done. historical_capacity updated for UK and Russia. ─────────────────")

    # Summary: verify final UK and Russia key years
    for region in ("United Kingdom", "Russia"):
        print(f"\n  Final {region}:")
        c.execute("""SELECT year, capacity_gw FROM historical_capacity
                     WHERE region=? ORDER BY year""", (region,))
        prev = None
        for (yr, cap) in c.fetchall():
            d = f"  ({cap-prev:+.3f})" if prev is not None else ""
            flag = " ←" if prev is not None and abs(cap - prev) >= 0.3 else ""
            print(f"    {yr}: {cap:.3f} GW{d}{flag}")
            prev = cap

    conn.close()


if __name__ == "__main__":
    main()
