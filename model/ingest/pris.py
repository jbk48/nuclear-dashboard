"""
pris.py — Ingest IAEA PRIS Excel tables (RDS-2 2024 edition).

Tables consumed:
  Table 14 — Operational reactors (423 units)
  Table 13 — Reactors under construction (66 units)
  Table 15 — Reactors in suspended operation / LTS (28 units)
  Table 12 — Reactors planned for construction (46 units)
  Table 16 — Permanently shut down reactors (217 units)
  Table 17 — Reactors in decommissioning (has License Expiration Date)
  Table 8  — Median construction times by country/period (for online-year estimation)
"""
import sqlite3
import re
from datetime import date, datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config import DB_PATH, RAW_PRIS_DIR, BASELINE_YEAR, REGION_MAP

import openpyxl


# ── Helpers ────────────────────────────────────────────────────────────────

def pris_path(table_num: int) -> Path:
    return RAW_PRIS_DIR / f"2024_Table{table_num}.xlsx"


def parse_pris_date(raw) -> str | None:
    """
    Convert PRIS date strings like '1968-5 ' or '2014-6 ' to 'YYYY-MM'.
    Also handles already-parsed integers/floats (e.g., year only).
    Returns None if unparseable.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s in ("-", "N/A", ""):
        return None
    # Pattern: YYYY-M or YYYY-MM (with optional trailing spaces)
    m = re.match(r"^(\d{4})-(\d{1,2})\s*$", s)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if 1950 <= year <= 2060 and 1 <= month <= 12:
            return f"{year}-{month:02d}"
    # Plain year only
    m2 = re.match(r"^(\d{4})\s*$", s)
    if m2:
        return f"{m2.group(1)}-01"
    return None


def date_to_year(date_str: str | None) -> int | None:
    """Extract 4-digit year from a 'YYYY-MM' string."""
    if date_str and len(date_str) >= 4:
        try:
            return int(date_str[:4])
        except ValueError:
            pass
    return None


def compute_age(commercial_op_date: str | None, baseline_year: int = BASELINE_YEAR) -> float | None:
    """Years operating as of Dec 31 of baseline_year."""
    if not commercial_op_date:
        return None
    try:
        parts = commercial_op_date.split("-")
        year = int(parts[0])
        month = int(parts[1]) if len(parts) > 1 else 1
        # Use Dec 31 of baseline year as reference point
        baseline_date = date(baseline_year, 12, 31)
        op_date = date(year, month, 1)
        delta = baseline_date - op_date
        return round(delta.days / 365.25, 1)
    except Exception:
        return None


def get_region(country: str | None) -> str:
    """Map PRIS country name to dashboard region."""
    if not country:
        return "Unknown"
    return REGION_MAP.get(country.strip().upper(), "Emerging & Rest")


def make_reactor_id(code: str | None, name: str | None) -> str:
    """Clean up the PRIS reactor code into a usable ID."""
    if code and str(code).strip():
        # e.g. 'AR -1   ' → 'AR-1'
        return re.sub(r"\s+", "", str(code).strip()).upper()
    if name:
        return re.sub(r"[^A-Z0-9]", "_", str(name).strip().upper())
    return f"UNKNOWN_{id(code)}"


def read_worksheet(path: Path) -> list[tuple]:
    """Load all rows from the first worksheet as a list of tuples."""
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active
    return list(ws.iter_rows(values_only=True))


def iter_reactor_rows(rows: list[tuple], data_start_row: int, col_map: dict) -> list[dict]:
    """
    Iterate data rows, forward-filling the Country column (PRIS omits it
    for all but the first reactor of each country).
    col_map: {field_name: 0-based column index}
    """
    records = []
    current_country = None
    for row in rows[data_start_row - 1:]:
        # Skip completely empty rows
        if all(v is None for v in row):
            continue
        # Forward-fill country
        country_raw = row[col_map["country"]] if col_map["country"] < len(row) else None
        if country_raw and str(country_raw).strip():
            current_country = str(country_raw).strip().upper()
        rec = {"country": current_country}
        for field, idx in col_map.items():
            if field == "country":
                continue
            rec[field] = row[idx] if idx < len(row) else None
        records.append(rec)
    return records


# ── Table 14 — Operational reactors ───────────────────────────────────────

def load_table14() -> list[dict]:
    """
    Columns (0-based):
    0=None, 1=Country, 2=Code, 3=Name, 4=Type, 5=Model,
    6=Thermal, 7=Gross, 8=Net, 9=Operator, 10=NSSS,
    11=ConstStart, 12=GridConn, 13=CommOp, 14=EAF%, 15=UCF%
    """
    rows = read_worksheet(pris_path(14))
    col_map = {
        "country": 1, "code": 2, "name": 3, "type": 4, "model": 5,
        "thermal": 6, "gross": 7, "net": 8, "operator": 9, "nsss": 10,
        "const_start": 11, "grid_conn": 12, "comm_op": 13,
        "eaf_pct": 14, "ucf_pct": 15,
    }
    raw_records = iter_reactor_rows(rows, data_start_row=7, col_map=col_map)
    reactors = []
    for r in raw_records:
        code = r.get("code")
        name = r.get("name")
        if not name and not code:
            continue
        rid = make_reactor_id(code, name)
        comm_op = parse_pris_date(r.get("comm_op"))
        reactors.append({
            "reactor_id":               rid,
            "name":                     str(name).strip() if name else None,
            "country":                  r["country"],
            "region":                   get_region(r["country"]),
            "status":                   "Operating",
            "net_capacity_mw":          r.get("net"),
            "gross_capacity_mw":        r.get("gross"),
            "thermal_capacity_mw":      r.get("thermal"),
            "reactor_type":             str(r.get("type", "")).strip() or None,
            "reactor_model":            str(r.get("model", "")).strip() or None,
            "operator":                 str(r.get("operator", "")).strip() or None,
            "nsss_supplier":            str(r.get("nsss", "")).strip() or None,
            "construction_start_date":  parse_pris_date(r.get("const_start")),
            "first_grid_date":          parse_pris_date(r.get("grid_conn")),
            "commercial_operation_date": comm_op,
            "age_at_baseline_years":    compute_age(comm_op),
            "eaf_pct":                  r.get("eaf_pct"),
            "ucf_pct":                  r.get("ucf_pct"),
            "pipeline_probability":     None,
            "data_source":              "PRIS_T14",
            "last_updated":             str(date.today()),
        })
    return reactors


# ── Table 13 — Under construction ─────────────────────────────────────────

def load_table13() -> list[dict]:
    """
    Columns (0-based):
    0=None, 1=Country, 2=None(empty), 3=Code, 4=Name, 5=Type, 6=Model,
    7=Thermal, 8=Gross, 9=Net, 10=Operator, 11=NSSS,
    12=ConstStart, 13=FirstCrit(exp), 14=GridConn(exp), 15=CommOp(exp)
    Note: Table 13 has an extra empty column at index 2 vs Table 14.
    """
    rows = read_worksheet(pris_path(13))
    col_map = {
        "country": 1, "code": 3, "name": 4, "type": 5, "model": 6,
        "thermal": 7, "gross": 8, "net": 9, "operator": 10, "nsss": 11,
        "const_start": 12, "first_crit_exp": 13, "grid_conn_exp": 14,
    }
    raw_records = iter_reactor_rows(rows, data_start_row=7, col_map=col_map)
    reactors = []
    for r in raw_records:
        code = r.get("code")
        name = r.get("name")
        if not name and not code:
            continue
        rid = make_reactor_id(code, name)
        grid_exp = parse_pris_date(r.get("grid_conn_exp"))
        online_year = date_to_year(grid_exp)
        reactors.append({
            "reactor_id":               rid,
            "name":                     str(name).strip() if name else None,
            "country":                  r["country"],
            "region":                   get_region(r["country"]),
            "status":                   "UnderConstruction",
            "net_capacity_mw":          r.get("net"),
            "gross_capacity_mw":        r.get("gross"),
            "thermal_capacity_mw":      r.get("thermal"),
            "reactor_type":             str(r.get("type", "")).strip() or None,
            "reactor_model":            str(r.get("model", "")).strip() or None,
            "operator":                 str(r.get("operator", "")).strip() or None,
            "nsss_supplier":            str(r.get("nsss", "")).strip() or None,
            "construction_start_date":  parse_pris_date(r.get("const_start")),
            "first_criticality_date":   parse_pris_date(r.get("first_crit_exp")),
            "first_grid_date":          grid_exp,
            "expected_online_year":     online_year,
            "expected_online_year_source": "PRIS" if online_year else None,
            "pipeline_probability":     0.95,   # default UC rate
            "data_source":              "PRIS_T13",
            "last_updated":             str(date.today()),
        })
    return reactors


# ── Table 15 — Suspended / Long-Term Shutdown ─────────────────────────────

def load_table15() -> list[dict]:
    """
    Same structure as Table 14 — leading None col A, data from col 1.
    """
    rows = read_worksheet(pris_path(15))
    col_map = {
        "country": 1, "code": 2, "name": 3, "type": 4, "model": 5,
        "thermal": 6, "gross": 7, "net": 8, "operator": 9, "nsss": 10,
        "const_start": 11, "grid_conn": 12, "comm_op": 13,
    }
    raw_records = iter_reactor_rows(rows, data_start_row=7, col_map=col_map)
    reactors = []
    for r in raw_records:
        code = r.get("code")
        name = r.get("name")
        if not name and not code:
            continue
        # Check if code is actually in the 0-based index 1 (after shift)
        # Table 15 starts country at col 1, so let's check the first real data row
        rid = make_reactor_id(code, name)
        comm_op = parse_pris_date(r.get("comm_op"))
        reactors.append({
            "reactor_id":               rid,
            "name":                     str(name).strip() if name else None,
            "country":                  r["country"],
            "region":                   get_region(r["country"]),
            "status":                   "LongTermShutdown",
            "net_capacity_mw":          r.get("net"),
            "gross_capacity_mw":        r.get("gross"),
            "thermal_capacity_mw":      r.get("thermal"),
            "reactor_type":             str(r.get("type", "")).strip() or None,
            "reactor_model":            str(r.get("model", "")).strip() or None,
            "operator":                 str(r.get("operator", "")).strip() or None,
            "nsss_supplier":            str(r.get("nsss", "")).strip() or None,
            "construction_start_date":  parse_pris_date(r.get("const_start")),
            "first_grid_date":          parse_pris_date(r.get("grid_conn")),
            "commercial_operation_date": comm_op,
            "age_at_baseline_years":    compute_age(comm_op),
            "pipeline_probability":     None,
            "data_source":              "PRIS_T15",
            "last_updated":             str(date.today()),
        })
    return reactors


# ── Table 12 — Planned for construction ───────────────────────────────────

def load_table12() -> list[dict]:
    """
    Table 12 column layout (0-based) — all tables have leading None col A:
    0=None, 1=Country (first row of group, else None), 2=Code, 3=Name,
    4=Type, 5=Model, 6=Thermal, 7=Gross, 8=Net, 9=Operator, 10=NSSS,
    11=Expected Construction Start

    Subsequent rows for same country have None in col 1.
    """
    rows = read_worksheet(pris_path(12))

    def safe_float(v):
        try:
            return float(v) if v is not None and str(v).strip() not in ("", "-") else None
        except (ValueError, TypeError):
            return None

    reactors = []
    current_country = None
    for row in rows[6:]:  # data starts row 7 (0-based 6)
        if all(v is None for v in row):
            continue
        # Col 1 = country (if present), col 2 = code, col 3 = name
        country_raw = row[1] if len(row) > 1 else None
        code_raw    = row[2] if len(row) > 2 else None
        name_raw    = row[3] if len(row) > 3 else None

        if country_raw and str(country_raw).strip():
            current_country = str(country_raw).strip().upper()

        code = str(code_raw).strip() if code_raw else None
        name = str(name_raw).strip() if name_raw else None

        if not code and not name:
            continue
        # Skip footnote rows (no code pattern)
        if name and name.lower().startswith("note"):
            continue

        rid = make_reactor_id(code, name)
        thermal  = safe_float(row[6] if len(row) > 6 else None)
        gross    = safe_float(row[7] if len(row) > 7 else None)
        net_mw   = safe_float(row[8] if len(row) > 8 else None)
        exp_start = parse_pris_date(row[11] if len(row) > 11 else None)

        reactors.append({
            "reactor_id":               rid,
            "name":                     name,
            "country":                  current_country,
            "region":                   get_region(current_country),
            "status":                   "Planned",
            "net_capacity_mw":          net_mw,
            "gross_capacity_mw":        gross,
            "thermal_capacity_mw":      thermal,
            "construction_start_date":  exp_start,
            "pipeline_probability":     0.70,
            "data_source":              "PRIS_T12",
            "last_updated":             str(date.today()),
        })
    return reactors


# ── Table 17 — Decommissioning (License Expiration Dates) ─────────────────

def load_table17() -> dict[str, str]:
    """
    Table 17 layout (0-based):
    0=None, 1=Country, 2=Ref.No, 3=Unit Name, 4=Shutdown date,
    5=Shutdown reason, 6=Decom.strategy, 7=Current decom.phase,
    8=Current fuel mgmt phase, 9=Decom.licensee, 10=License Expiration

    Returns {reactor_id: license_expiry_year_str}.
    """
    rows = read_worksheet(pris_path(17))
    license_dates: dict[str, str] = {}
    current_country = None
    for row in rows[5:]:   # data starts row 6 (0-based 5)
        if all(v is None for v in row):
            continue
        # Col 1 = country (forward-fill), col 2 = code, col 10 = license expiry
        country_raw = row[1] if len(row) > 1 else None
        code_raw    = row[2] if len(row) > 2 else None
        lic_exp     = row[10] if len(row) > 10 else None

        if country_raw and str(country_raw).strip():
            val = str(country_raw).strip()
            # Skip if this looks like a reactor code, not a country
            if not re.match(r"^[A-Z]{2}\s*-\s*\d+", val):
                current_country = val.upper()

        code = str(code_raw).strip() if code_raw else ""
        if lic_exp is not None:
            try:
                year = int(float(str(lic_exp).strip()))
                if 2000 <= year <= 2100:
                    rid = make_reactor_id(code, None)
                    if rid and rid != "UNKNOWN_":
                        license_dates[rid] = str(year)
            except (ValueError, TypeError):
                pass
    return license_dates


# ── Table 8 — Median construction times ────────────────────────────────────

def load_table8() -> dict[str, float]:
    """
    Table 8 layout (0-based) — leading None col A:
    0=None, 1=Country, then 7 period pairs:
    2=No(1991-95), 3=Months(1991-95), 4=No(1996-00), 5=Months(1996-00),
    ..., 14=No(2021-24), 15=Months(2021-24)

    Returns {country_upper: median_construction_months} using most recent
    period with data.
    """
    rows = read_worksheet(pris_path(8))
    # Months columns (0-based): 3,5,7,9,11,13,15 for periods 91-95...21-24
    MONTHS_COLS = [3, 5, 7, 9, 11, 13, 15]
    country_times: dict[str, float] = {}
    for row in rows[6:]:   # data rows start at row 7
        if all(v is None for v in row):
            continue
        country = str(row[1]).strip().upper() if len(row) > 1 and row[1] else None
        if not country or country in ("", "COUNTRY"):
            continue
        # Most recent period first
        best_months = None
        for mo_col in reversed(MONTHS_COLS):
            if mo_col < len(row):
                val = row[mo_col]
                if val is not None and str(val).strip() not in ("", "-", "None"):
                    try:
                        m = float(val)
                        if m > 0:
                            best_months = m
                            break
                    except (ValueError, TypeError):
                        pass
        if best_months:
            country_times[country] = best_months
    return country_times


# ── Main ingestion function ────────────────────────────────────────────────

def ingest_pris(conn: sqlite3.Connection) -> None:
    """Load all PRIS tables into the reactors table."""
    print("  Loading Table 14 (operating)...")
    operating = load_table14()
    print(f"    → {len(operating)} operating reactors")

    print("  Loading Table 13 (under construction)...")
    uc = load_table13()
    print(f"    → {len(uc)} UC reactors")

    print("  Loading Table 15 (suspended/LTS)...")
    lts = load_table15()
    print(f"    → {len(lts)} LTS reactors")

    print("  Loading Table 12 (planned)...")
    planned = load_table12()
    print(f"    → {len(planned)} planned reactors")

    print("  Loading Table 17 (decommissioning license dates)...")
    lic_dates = load_table17()
    print(f"    → {len(lic_dates)} license expiry dates found")

    print("  Loading Table 8 (construction times)...")
    build_times = load_table8()
    print(f"    → {len(build_times)} countries with construction time data")

    all_reactors = operating + uc + lts + planned

    # Estimate expected_online_year for UC/Planned units missing a grid date
    for r in all_reactors:
        if r["status"] in ("UnderConstruction", "Planned") and not r.get("expected_online_year"):
            country = (r.get("country") or "").upper()
            const_start = r.get("construction_start_date")
            build_months = build_times.get(country, 84)  # default 84 months (~7 yr)
            if const_start:
                start_year = date_to_year(const_start)
                if start_year:
                    r["expected_online_year"] = start_year + round(build_months / 12)
                    r["expected_online_year_source"] = "Estimated"

    # Insert all reactors
    cur = conn.cursor()
    fields = [
        "reactor_id", "name", "country", "region", "status",
        "net_capacity_mw", "gross_capacity_mw", "thermal_capacity_mw",
        "restart_capacity_mw", "reactor_type", "reactor_model",
        "owner", "operator", "nsss_supplier",
        "construction_start_date", "first_criticality_date",
        "first_grid_date", "commercial_operation_date",
        "age_at_baseline_years",
        "long_term_shutdown_date", "restart_date", "actual_shutdown_date",
        "planned_shutdown_date_declared", "license_expiry_date_derived",
        "retirement_date_used", "retirement_tier", "design_life_years",
        "expected_online_year", "expected_online_year_source",
        "pipeline_probability", "construction_delay_years",
        "eaf_pct", "ucf_pct", "is_smr",
        "latitude", "longitude", "data_source", "last_updated", "notes",
    ]
    placeholders = ",".join("?" for _ in fields)
    sql = f"INSERT OR REPLACE INTO reactors ({','.join(fields)}) VALUES ({placeholders})"

    inserted = 0
    for r in all_reactors:
        values = tuple(r.get(f) for f in fields)
        try:
            cur.execute(sql, values)
            inserted += 1
        except Exception as e:
            print(f"    WARNING: Could not insert {r.get('reactor_id')}: {e}")
    conn.commit()
    print(f"  ✓ Inserted {inserted} reactor records into DB")

    # Log data source
    cur.execute("""
        INSERT OR REPLACE INTO data_sources_log (source_name, data_as_of_date, ingestion_date, version_note)
        VALUES (?, ?, ?, ?)
    """, ("PRIS", "2024-12-31", str(date.today()), "IAEA RDS-2 2024 edition (Tables 12-15, 17)"))
    conn.commit()
