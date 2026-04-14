"""
merge.py — Post-ingestion reconciliation: deduplication, retirement tier
           assignment, and SMR flagging.

Called after all source-specific ingest scripts have run. Operates entirely
on the database — reads, computes, and writes back.
"""
import sqlite3
import re
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config import DB_PATH, BASELINE_YEAR


# ── Retirement rules (Tier 2) ──────────────────────────────────────────────
# country (upper) → (total_assumed_life_yr, max_possible_life_yr, ext_unit_yr)
_US  = (60, 80, 20)
_UK  = (45, 55, 10)
_KR  = (40, 60, 20)
_CZ  = (40, 60, 20)
RETIREMENT_RULES: dict[str, tuple[int, int, int]] = {
    # United States — PRIS abbreviates as "USA"
    "UNITED STATES OF AMERICA": _US,
    "USA":                      _US,
    "FRANCE":                   (50, 60, 10),
    "JAPAN":                    (40, 60, 20),
    # United Kingdom — PRIS abbreviates as "UK"
    "UNITED KINGDOM":           _UK,
    "UK":                       _UK,
    "RUSSIA":                   (45, 60, 15),
    "RUSSIAN FEDERATION":       (45, 60, 15),
    "CHINA":                    (40, 50, 10),
    "INDIA":                    (40, 60, 20),
    "PAKISTAN":                 (40, 50, 10),
    # Korea — PRIS uses multiple spellings
    "SOUTH KOREA":              _KR,
    "KOREA, REPUBLIC OF":       _KR,
    "KOREA,REP.OF":             _KR,
    "KOREA,REPUBLIC OF":        _KR,
    "REPUBLIC OF KOREA":        _KR,
    "CANADA":                   (40, 60, 10),   # CANDU; refurbishment adds ~30 yr
    "UKRAINE":                  (40, 60, 20),
    "BELGIUM":                  (40, 50, 10),
    "FINLAND":                  (40, 60, 20),
    "SPAIN":                    (40, 50, 10),
    "SWEDEN":                   (40, 60, 20),
    "SWITZERLAND":              (40, 60, 20),
    # Czech Republic — PRIS uses multiple spellings
    "CZECH REPUBLIC":           _CZ,
    "CZECH REP.":               _CZ,
    "CZECHIA":                  _CZ,
    "HUNGARY":                  (40, 60, 20),
    "SLOVAKIA":                 (40, 60, 20),
    "ROMANIA":                  (40, 60, 20),
    "BULGARIA":                 (40, 60, 20),
    "SLOVENIA":                 (40, 60, 20),
    "NETHERLANDS":              (40, 60, 20),
    "ARMENIA":                  (30, 40, 10),
    "BRAZIL":                   (40, 60, 20),
    "ARGENTINA":                (40, 60, 20),
    "SOUTH AFRICA":             (40, 60, 20),
    "IRAN, ISLAMIC REPUBLIC OF":(40, 60, 20),
    "IRAN,ISL.REP":             (40, 60, 20),
    # UAE — PRIS abbreviates as "UAE"
    "UNITED ARAB EMIRATES":     (60, 60,  0),
    "UAE":                      (60, 60,  0),
    "BANGLADESH":               (60, 60,  0),
    "BELARUS":                  (60, 60,  0),
    # Taiwan — PRIS uses comma-separated form without space
    "TAIWAN, CHINA":            (40, 40,  0),   # phase-out policy
    "TAIWAN,CHINA":             (40, 40,  0),
    "TAIWAN":                   (40, 40,  0),
    "MEXICO":                   (40, 60, 20),
}
DEFAULT_RULE = (40, 60, 20)  # fallback for unknown countries

# Known SMR models (partial matching)
SMR_MODEL_KEYWORDS = [
    "carem", "smr", "nuscale", "bwrx", "ap300", "hpr10", "acpr50",
    "afr", "xe-100", "htgr", "pbr", "msr", "molten", "thorium",
    "kairos", "terrestrial", "oklo", "natrium", "voygr",
]
SMR_CAPACITY_THRESHOLD_MW = 300  # net MW; anything below this flagged as potential SMR


def year_from_date(date_str: str | None) -> int | None:
    if date_str and len(date_str) >= 4:
        try:
            return int(date_str[:4])
        except ValueError:
            return None
    return None


def compute_tier2_retirement(comm_op_year: int | None, country: str) -> str | None:
    """Return Tier 2 retirement date (YYYY-01) based on country rules."""
    if comm_op_year is None:
        return None
    total_life, _, _ = RETIREMENT_RULES.get(country.upper(), DEFAULT_RULE)
    ret_year = comm_op_year + total_life
    return f"{ret_year}-01"


def compute_tier3_retirement(comm_op_year: int | None) -> str | None:
    """Tier 3 fallback: 40 years from commercial operation."""
    if comm_op_year is None:
        return None
    return f"{comm_op_year + 40}-01"


def is_smr(model: str | None, capacity_mw: float | None) -> bool:
    """Flag reactor as SMR if model name matches known SMR keywords or capacity is very small."""
    if model:
        model_lower = model.lower()
        if any(kw in model_lower for kw in SMR_MODEL_KEYWORDS):
            return True
    if capacity_mw and capacity_mw < SMR_CAPACITY_THRESHOLD_MW:
        # Small units may be SMR; flag for review
        return True
    return False


def load_retirement_rules(conn: sqlite3.Connection) -> None:
    """Populate the retirement_rules reference table."""
    cur = conn.cursor()
    rules_data = [
        ("UNITED STATES OF AMERICA", 40, 20, 60, 80, 20,
         "NRC: 40yr initial + 20yr renewals (no cap). Currently up to 80yr approved."),
        ("USA", 40, 20, 60, 80, 20, "PRIS abbreviation — same as UNITED STATES OF AMERICA."),
        ("FRANCE",    40, 10, 50, 60, 10,
         "ASN 10-yr periodic safety reviews; 60yr being explored by EDF/govt."),
        ("JAPAN",     40, 20, 40, 60, 20,
         "40yr default (NRA post-Fukushima); +20yr extension possible; 2023 law allows longer."),
        ("UNITED KINGDOM", 35, 10, 45, 55, 10,
         "Lifecycle licence (no fixed term); ONR periodic review; ~40-50yr typical."),
        ("UK", 35, 10, 45, 55, 10, "PRIS abbreviation — same as UNITED KINGDOM."),
        ("RUSSIA",    30, 15, 45, 60, 15,
         "30yr initial + 15yr extensions common for VVER; Rosatom targets 60yr."),
        ("RUSSIAN FEDERATION", 30, 15, 45, 60, 15, "Same as RUSSIA."),
        ("CHINA",     40,  0, 40, 50, 10,
         "40yr design life; extensions not yet routinely approved; fleet is young."),
        ("INDIA",     40, 20, 40, 60, 20,
         "AERB 40yr initial; extensions evaluated case-by-case."),
        ("SOUTH KOREA", 40, 20, 40, 60, 20,
         "NSSC 40yr; extensions on case-by-case."),
        ("KOREA, REPUBLIC OF", 40, 20, 40, 60, 20, "See SOUTH KOREA."),
        ("KOREA,REP.OF", 40, 20, 40, 60, 20, "PRIS abbreviation — see SOUTH KOREA."),
        ("KOREA,REPUBLIC OF", 40, 20, 40, 60, 20, "PRIS abbreviation — see SOUTH KOREA."),
        ("CANADA",    30, 30, 40, 60, 10,
         "~30yr initial; CANDU refurbishment adds ~30yr (well-documented by utilities)."),
        ("UKRAINE",   30, 20, 40, 60, 20, "Soviet-era VVER; 30yr initial + extensions."),
        ("BELGIUM",   40, 10, 40, 50, 10, "FANC approvals; current policy targets 50yr for Doel/Tihange."),
        ("FINLAND",   40, 20, 40, 60, 20, "STUK; extensions for Loviisa."),
        ("SWEDEN",    40, 20, 40, 60, 20, "SSM; extensions planned for Ringhals/Forsmark."),
        ("SWITZERLAND", 40, 20, 40, 60, 20, "ENSI; no mandatory phase-out age."),
        ("CZECH REPUBLIC", 40, 20, 40, 60, 20, "SUJB; Dukovany/Temelin extensions planned."),
        ("CZECH REP.", 40, 20, 40, 60, 20, "PRIS abbreviation — see CZECH REPUBLIC."),
        ("CZECHIA",   40, 20, 40, 60, 20, "See CZECH REPUBLIC."),
        ("HUNGARY",   30, 20, 40, 60, 20, "Paks 1-4 extended to ~50yr; Paks II under construction."),
        ("SLOVAKIA",  30, 20, 40, 60, 20, "UJD; extensions for Mochovce/Bohunice."),
        ("ROMANIA",   30, 20, 40, 60, 20, "CNCAN; Cernavoda refurbishment plan."),
        ("BULGARIA",  30, 10, 40, 60, 20, "NSC."),
        ("SLOVENIA",  40, 20, 40, 60, 20, "URSJV; Krsko life extension to ~60yr."),
        ("ARGENTINA", 40, 20, 40, 60, 20, "ARN; Embalse refurbishment extended life."),
        ("BRAZIL",    40, 20, 40, 60, 20, "CNEN; Angra extensions."),
        ("SOUTH AFRICA", 40, 20, 40, 60, 20, "NNR; Koeberg extended to 60yr."),
        ("TAIWAN, CHINA", 40, 0, 40, 40, 0, "Phase-out policy; no extensions."),
        ("TAIWAN,CHINA", 40, 0, 40, 40, 0, "PRIS abbreviation — see TAIWAN, CHINA."),
        ("TAIWAN",    40,  0, 40, 40,  0, "Phase-out policy; no extensions."),
        ("UNITED ARAB EMIRATES", 60, 0, 60, 60, 0, "Barakah: 60yr design life; first units new."),
        ("UAE", 60, 0, 60, 60, 0, "PRIS abbreviation — see UNITED ARAB EMIRATES."),
        ("MEXICO",    40, 20, 40, 60, 20, "CNSNS; Laguna Verde extensions."),
        ("PAKISTAN",  40, 10, 40, 50, 10, "PNRA; older units Chasma/Karachi."),
        ("IRAN, ISLAMIC REPUBLIC OF", 40, 20, 40, 60, 20, "INRA; Bushehr."),
        ("IRAN,ISL.REP", 40, 20, 40, 60, 20, "PRIS abbreviation — see IRAN."),
        ("BANGLADESH", 60, 0, 60, 60, 0, "Rooppur: 60yr design life."),
        ("BELARUS",   60, 0, 60, 60, 0, "Ostrovets: 60yr design life."),
    ]
    cur.executemany("""
        INSERT OR REPLACE INTO retirement_rules
        (country, baseline_license_years, typical_extension_years,
         total_assumed_life_years, max_possible_life_years, extension_unit_years, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, rules_data)
    conn.commit()
    print(f"  ✓ Loaded {len(rules_data)} retirement rules")


def assign_retirement_tiers(conn: sqlite3.Connection) -> None:
    """
    For every Operating, LongTermShutdown, and Restarted reactor:
    1. Check for Tier 1 declared date (planned_shutdown_date_declared)
    2. Else compute Tier 2 from country rules
    3. Else Tier 3 (40yr default)
    Writes retirement_date_used and retirement_tier.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT reactor_id, country, commercial_operation_date,
               planned_shutdown_date_declared, status, net_capacity_mw, reactor_model,
               first_grid_date
        FROM reactors
        WHERE status IN ('Operating', 'LongTermShutdown', 'Restarted')
    """)
    rows = cur.fetchall()
    updated = 0
    for (rid, country, comm_op, declared, status, cap_mw, model, first_grid) in rows:
        comm_year = year_from_date(comm_op)

        # Fall back to first_grid_date year when commercial_operation_date is absent.
        # Brand-new reactors (e.g. Flamanville-3, Mochovce-3) achieve grid connection
        # months before commercial operation is formally declared; first_grid_date is a
        # reliable proxy for the purpose of retirement date estimation.
        if comm_year is None and first_grid:
            comm_year = year_from_date(first_grid)

        # SMR flag
        smr_flag = 1 if is_smr(model, cap_mw) else 0

        if declared and year_from_date(declared):
            tier = 1
            ret_date = declared
        elif comm_year and country:
            ret_t2 = compute_tier2_retirement(comm_year, country)
            if ret_t2:
                tier = 2
                ret_date = ret_t2
            else:
                tier = 3
                ret_date = compute_tier3_retirement(comm_year)
        else:
            tier = 3
            ret_date = None  # can't compute without COD or first_grid_date

        cur.execute("""
            UPDATE reactors
            SET retirement_date_used = ?,
                retirement_tier = ?,
                is_smr = ?
            WHERE reactor_id = ?
        """, (ret_date, tier, smr_flag, rid))
        updated += 1

    conn.commit()
    tier_counts = {}
    cur.execute("SELECT retirement_tier, COUNT(*) FROM reactors WHERE status='Operating' GROUP BY retirement_tier")
    for tier, cnt in cur.fetchall():
        tier_counts[tier] = cnt
    print(f"  ✓ Retirement tiers assigned: {tier_counts}")

    # Report total GW by tier
    cur.execute("""
        SELECT retirement_tier,
               ROUND(SUM(COALESCE(net_capacity_mw, 0)) / 1000.0, 1) as gw
        FROM reactors
        WHERE status = 'Operating'
        GROUP BY retirement_tier
        ORDER BY retirement_tier
    """)
    for (tier, gw) in cur.fetchall():
        print(f"    Tier {tier}: {gw} GW")


def flag_smrs(conn: sqlite3.Connection) -> None:
    """Flag SMR units in the pipeline (UC, Planned, Proposed)."""
    cur = conn.cursor()
    cur.execute("""
        SELECT reactor_id, reactor_model, net_capacity_mw FROM reactors
        WHERE status IN ('UnderConstruction', 'Planned', 'Proposed')
        AND is_smr IS NULL OR is_smr = 0
    """)
    rows = cur.fetchall()
    flagged = 0
    for (rid, model, cap_mw) in rows:
        if is_smr(model, cap_mw):
            cur.execute("UPDATE reactors SET is_smr = 1 WHERE reactor_id = ?", (rid,))
            flagged += 1
    conn.commit()
    if flagged:
        print(f"  ✓ Flagged {flagged} additional SMR units in pipeline")


def run_merge(conn: sqlite3.Connection) -> None:
    """Run all merge/reconciliation steps."""
    print("  Loading retirement rules...")
    load_retirement_rules(conn)
    print("  Assigning retirement tiers to operating fleet...")
    assign_retirement_tiers(conn)
    print("  Flagging SMR units...")
    flag_smrs(conn)
