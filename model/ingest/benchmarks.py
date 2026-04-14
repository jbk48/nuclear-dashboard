"""
benchmarks.py — Ingest IAEA RDS-1 2025 projection benchmarks.

Tables consumed (from IAEA Projections 2050 folder):
  Table 3 — Global nuclear capacity Low/High projections (2030, 2040, 2050)
  Table 5 — Regional nuclear capacity Low/High projections (same years)
  Table 8 — Northern America capacity Low/High
  Table 11 — Nothern/Western/Southern Europe
  Table 14 — Eastern Europe
  ... (all regional capacity tables)

Also inserts the IEA WEO 2024 benchmarks as hard-coded constants
(not available as Excel locally; values from the spec / publication).
"""
import sqlite3
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config import DB_PATH, RAW_PROJ_DIR

import openpyxl


def proj_path(table_num: int) -> Path:
    return RAW_PROJ_DIR / f"2025_Table{table_num}.xlsx"


def read_rows(path: Path) -> list[tuple]:
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active
    return list(ws.iter_rows(values_only=True))


def parse_capacity_table(rows: list[tuple], region_label: str) -> list[dict]:
    """
    Generic parser for RDS-1 regional capacity tables (GW(e)).
    Expected structure:
      Row 3 (0-based 2): [label, 2024, 2030, 2040, 2050]
      Row 4 (0-based 3): [blank, blank, Low, High, Low, High, Low, High]
      Row 5 (0-based 4): Nuclear row (or Total first, then Nuclear)

    Returns list of {source, scenario_name, year, region, capacity_gw} dicts.
    """
    records = []
    # Find the row with "Nuclear" label
    nuclear_row = None
    for row in rows:
        row_vals = [str(v).strip() if v else "" for v in row]
        if any("nuclear" in v.lower() for v in row_vals if v):
            # Find the first column that says "Nuclear" and isn't a header title
            for v in row_vals:
                if v.lower().startswith("nuclear") and len(v) < 20:
                    nuclear_row = row
                    break
            if nuclear_row:
                break

    if nuclear_row is None:
        return records

    # The year header should be 2 rows above in a standard table
    # Years: 2024=index where 2024 appears, then pairs (Low/High) for 2030, 2040, 2050
    # From our inspection: [Nuclear, 377_or_regional, Low30, High30, Low40, High40, Low50, High50]
    vals = [v for v in nuclear_row if v is not None]
    # Filter to numeric values only
    nums = []
    for v in nuclear_row:
        if isinstance(v, (int, float)) and v is not None:
            nums.append(float(v))

    # Standard layout: [2024_val, 2030_low, 2030_high, 2040_low, 2040_high, 2050_low, 2050_high]
    if len(nums) >= 7:
        mapping = [
            (2024, "Observed",  nums[0]),
            (2030, "Low",       nums[1]),
            (2030, "High",      nums[2]),
            (2040, "Low",       nums[3]),
            (2040, "High",      nums[4]),
            (2050, "Low",       nums[5]),
            (2050, "High",      nums[6]),
        ]
        for year, scenario, gw in mapping:
            records.append({
                "source":        "IAEA_RDS1_2025",
                "scenario_name": scenario,
                "year":          year,
                "region":        region_label,
                "capacity_gw":   round(gw, 2),
            })
    return records


# IAEA regional table numbers for capacity (every 3rd table starting at 8)
# Table 7=NorthAm_Energy, 8=NorthAm_Capacity, 9=NorthAm_Production
# Table 10=LatAm_Energy, 11=LatAm_Capacity, ...
REGIONAL_CAPACITY_TABLES = {
    "Northern America":                     8,
    "Latin America and the Caribbean":      11,
    "Northern Western and Southern Europe": 14,
    "Eastern Europe":                       17,
    "Africa":                               20,
    "Western Asia":                         23,
    "Southern Asia":                        26,
    "Central and Eastern Asia":             29,
    "South-eastern Asia":                   32,
    "Oceania":                              35,
}


def ingest_benchmarks(conn: sqlite3.Connection) -> None:
    """Load IAEA RDS-1 and IEA WEO benchmark data."""
    cur = conn.cursor()
    inserted = 0

    # ── IAEA RDS-1: Global (Table 3) ────────────────────────────────────────
    print("  Loading RDS-1 Table 3 (global projections)...")
    rows = read_rows(proj_path(3))
    global_records = parse_capacity_table(rows, "Global")
    for rec in global_records:
        cur.execute("""
            INSERT OR REPLACE INTO benchmarks (source, scenario_name, year, region, capacity_gw)
            VALUES (?, ?, ?, ?, ?)
        """, (rec["source"], rec["scenario_name"], rec["year"], rec["region"], rec["capacity_gw"]))
        inserted += 1
    print(f"    → {len(global_records)} global benchmark points")

    # ── IAEA RDS-1: Regional (Table 5 + individual regional tables) ──────────
    print("  Loading RDS-1 Table 5 (regional projections)...")
    rows5 = read_rows(proj_path(5))
    # Table 5 has all regions in one table
    # Structure: Col 0=Region, then 2024, 2030L, 2030H, 2040L, 2040H, 2050L, 2050H
    for row in rows5[4:]:   # data starts row 5 (0-based 4)
        if all(v is None for v in row):
            continue
        region_label = str(row[0]).strip() if row[0] else ""
        if not region_label or region_label.lower() in ("region", "world total", ""):
            # World Total handled separately
            if "world" in region_label.lower():
                pass
            continue
        nums = [float(v) for v in row[1:] if isinstance(v, (int, float)) and v is not None]
        if len(nums) >= 7:
            for year, scenario, gw in [
                (2024, "Observed", nums[0]),
                (2030, "Low",      nums[1]), (2030, "High", nums[2]),
                (2040, "Low",      nums[3]), (2040, "High", nums[4]),
                (2050, "Low",      nums[5]), (2050, "High", nums[6]),
            ]:
                cur.execute("""
                    INSERT OR REPLACE INTO benchmarks (source, scenario_name, year, region, capacity_gw)
                    VALUES (?, ?, ?, ?, ?)
                """, ("IAEA_RDS1_2025", scenario, year, region_label, round(gw, 2)))
                inserted += 1

    # ── IEA WEO 2024 benchmarks (hard-coded from publication) ───────────────
    print("  Loading IEA WEO 2024 benchmarks (hard-coded)...")
    iea_benchmarks = [
        # Global figures from IEA WEO 2024
        ("IEA_WEO2024", "STEPS",          2024, "Global", 377),
        ("IEA_WEO2024", "STEPS",          2030, "Global", 513),  # ~513 GW per WEO 2024
        ("IEA_WEO2024", "STEPS",          2040, "Global", 586),
        ("IEA_WEO2024", "STEPS",          2050, "Global", 647),
        ("IEA_WEO2024", "APS",            2024, "Global", 377),
        ("IEA_WEO2024", "APS",            2030, "Global", 551),  # ~551 GW per WEO 2024
        ("IEA_WEO2024", "APS",            2040, "Global", 748),
        ("IEA_WEO2024", "APS",            2050, "Global", 874),
        ("IEA_WEO2024", "LowNuclearCase", 2024, "Global", 377),
        ("IEA_WEO2024", "LowNuclearCase", 2050, "Global", 250),  # approx 3% of gen
    ]
    for row in iea_benchmarks:
        cur.execute("""
            INSERT OR REPLACE INTO benchmarks (source, scenario_name, year, region, capacity_gw)
            VALUES (?, ?, ?, ?, ?)
        """, row)
        inserted += 1

    conn.commit()
    print(f"  ✓ Inserted {inserted} benchmark records")

    cur.execute("""
        INSERT OR REPLACE INTO data_sources_log (source_name, data_as_of_date, ingestion_date, version_note)
        VALUES (?, ?, ?, ?)
    """, ("IAEA_RDS1", "2025-01-01", str(date.today()),
          "IAEA RDS-1 2025 edition — Tables 3, 5 (global and regional projections)"))
    cur.execute("""
        INSERT OR REPLACE INTO data_sources_log (source_name, data_as_of_date, ingestion_date, version_note)
        VALUES (?, ?, ?, ?)
    """, ("IEA_WEO2024", "2024-10-01", str(date.today()),
          "IEA World Energy Outlook 2024 — STEPS, APS, Low Nuclear Case (hard-coded)"))
    conn.commit()
