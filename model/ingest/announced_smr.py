"""
announced_smr.py — Named high-confidence SMR project announcements.

Adds specifically-named corporate SMR projects that are in advanced stages
of commitment (NRC permits, corporate PPAs, site preparation).  These are
more granular than the WNA generic pipeline proxies and can be overlaid on
the pipeline funnel chart by name.

Double-counting strategy:
  - Natrium (USA, Planned): WNA shows 0 US Planned MW → genuinely net-new.
  - Google/Kairos + X-energy/Amazon (USA, Proposed): WNA's 25 generic US
    Proposed entries already represent the US SMR pipeline; this module
    DELETES a matching number of WNA generic entries and replaces them with
    named ones, keeping total US Proposed MW approximately constant.
  - OPG Darlington Unit 1 (Canada, Planned): WNA has 2 generic Canada Planned
    entries (~400 MW total) that likely represent different projects; Darlington
    BWRX-300 is 1200 MW and more advanced → added as net-new Planned.
  - OPG Darlington Units 2-4 (Canada, Proposed): WNA Canada Proposed has 12
    generic entries; replace 3 of them with the named Darlington units.

Scenario inclusion:
  All entries are inserted with standard status flags.  Scenario realization
  rates then determine how much of each tier is built:
    Planned  → Optimistic 85%, Base 70%, Conservative 55%, Decline 40%
    Proposed → Optimistic 50%, Base 30%, Conservative 15%, Decline 5%
  This naturally reflects user intent (named projects appear in base/optimistic
  but are mostly absent in conservative/decline).

Sources (all as of 2026-03-20):
  TerraPower Natrium: NRC construction permit issued 2024-01; DOE ATB award;
                      plant to serve Wyoming coal retirement, ~2030 COD.
  OPG Darlington BWRX-300: CNSC licence application accepted; site prep
                            underway; OPG planning 4 units 2034–2038.
  Google / Kairos Power PPA: firm PPA signed 2024-10; Kairos NRC construction
                              permit target 2025; 500 MWe fleet.
  Amazon / X-energy: term sheet / equity investment 2024; X-energy NRC design
                     cert pending; 5 GW ambition by 2039 (reduced here to
                     credible near-term 600 MWe tranche).
"""
import sqlite3
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config import DB_PATH, REGION_MAP


# ── Named project definitions ─────────────────────────────────────────────────
# Each dict maps to one reactor row.
ANNOUNCED_SMRS = [
    # ── USA ────────────────────────────────────────────────────────────────────
    {
        "reactor_id":            "NAMED_TerraPower_Natrium_1",
        "name":                  "TerraPower Natrium (Kemmerer, WY)",
        "country":               "UNITED STATES OF AMERICA",
        "status":                "Planned",           # NRC construction permit issued
        "net_capacity_mw":       345,
        "expected_online_year":  2030,
        "reactor_type":          "SFR",               # Sodium-Fast Reactor
        "is_smr":                1,
        "pipeline_probability":  0.90,
        "data_source":           "TerraPower; NRC construction permit Jan 2024",
        # Natrium is genuinely net-new (WNA US Planned = 0 MW); no WNA deletion needed
        "delete_wna_proposed_n": 0,
    },
    {
        "reactor_id":            "NAMED_Kairos_Google_1",
        "name":                  "Kairos KP-FHR (Google PPA, Site A)",
        "country":               "UNITED STATES OF AMERICA",
        "status":                "Proposed",
        "net_capacity_mw":       100,
        "expected_online_year":  2035,
        "reactor_type":          "FHR",               # Fluoride High-Temperature Reactor
        "is_smr":                1,
        "pipeline_probability":  0.65,
        "data_source":           "Google-Kairos PPA Oct 2024; Kairos NRC application",
        "delete_wna_proposed_n": 0,                   # see bulk delete below
    },
    {
        "reactor_id":            "NAMED_Kairos_Google_2",
        "name":                  "Kairos KP-FHR (Google PPA, Site B)",
        "country":               "UNITED STATES OF AMERICA",
        "status":                "Proposed",
        "net_capacity_mw":       100,
        "expected_online_year":  2036,
        "reactor_type":          "FHR",
        "is_smr":                1,
        "pipeline_probability":  0.60,
        "data_source":           "Google-Kairos PPA Oct 2024",
        "delete_wna_proposed_n": 0,
    },
    {
        "reactor_id":            "NAMED_Kairos_Google_3",
        "name":                  "Kairos KP-FHR (Google PPA, Site C)",
        "country":               "UNITED STATES OF AMERICA",
        "status":                "Proposed",
        "net_capacity_mw":       100,
        "expected_online_year":  2037,
        "reactor_type":          "FHR",
        "is_smr":                1,
        "pipeline_probability":  0.55,
        "data_source":           "Google-Kairos PPA Oct 2024",
        "delete_wna_proposed_n": 0,
    },
    {
        "reactor_id":            "NAMED_Kairos_Google_4",
        "name":                  "Kairos KP-FHR (Google PPA, Site D)",
        "country":               "UNITED STATES OF AMERICA",
        "status":                "Proposed",
        "net_capacity_mw":       100,
        "expected_online_year":  2037,
        "reactor_type":          "FHR",
        "is_smr":                1,
        "pipeline_probability":  0.50,
        "data_source":           "Google-Kairos PPA Oct 2024",
        "delete_wna_proposed_n": 0,
    },
    {
        "reactor_id":            "NAMED_Kairos_Google_5",
        "name":                  "Kairos KP-FHR (Google PPA, Site E)",
        "country":               "UNITED STATES OF AMERICA",
        "status":                "Proposed",
        "net_capacity_mw":       100,
        "expected_online_year":  2038,
        "reactor_type":          "FHR",
        "is_smr":                1,
        "pipeline_probability":  0.45,
        "data_source":           "Google-Kairos PPA Oct 2024",
        "delete_wna_proposed_n": 0,
    },
    {
        "reactor_id":            "NAMED_Xenergy_Amazon_1",
        "name":                  "X-energy Xe-100 (Amazon PPA, Unit 1)",
        "country":               "UNITED STATES OF AMERICA",
        "status":                "Proposed",
        "net_capacity_mw":       200,
        "expected_online_year":  2033,
        "reactor_type":          "HTGR",              # High-Temp Gas Reactor
        "is_smr":                1,
        "pipeline_probability":  0.60,
        "data_source":           "Amazon-X-energy equity investment 2024; X-energy NRC DC",
        "delete_wna_proposed_n": 0,
    },
    {
        "reactor_id":            "NAMED_Xenergy_Amazon_2",
        "name":                  "X-energy Xe-100 (Amazon PPA, Unit 2)",
        "country":               "UNITED STATES OF AMERICA",
        "status":                "Proposed",
        "net_capacity_mw":       200,
        "expected_online_year":  2035,
        "reactor_type":          "HTGR",
        "is_smr":                1,
        "pipeline_probability":  0.55,
        "data_source":           "Amazon-X-energy equity investment 2024",
        "delete_wna_proposed_n": 0,
    },
    {
        "reactor_id":            "NAMED_Xenergy_Amazon_3",
        "name":                  "X-energy Xe-100 (Amazon PPA, Unit 3)",
        "country":               "UNITED STATES OF AMERICA",
        "status":                "Proposed",
        "net_capacity_mw":       200,
        "expected_online_year":  2036,
        "reactor_type":          "HTGR",
        "is_smr":                1,
        "pipeline_probability":  0.50,
        "data_source":           "Amazon-X-energy equity investment 2024",
        "delete_wna_proposed_n": 0,
    },
    # ── Canada ─────────────────────────────────────────────────────────────────
    {
        "reactor_id":            "NAMED_OPG_Darlington_BWRX_1",
        "name":                  "OPG Darlington BWRX-300 Unit 1",
        "country":               "CANADA",
        "status":                "Planned",           # site prep underway; CNSC licence application
        "net_capacity_mw":       300,
        "expected_online_year":  2034,
        "reactor_type":          "BWR",
        "is_smr":                1,
        "pipeline_probability":  0.85,
        "data_source":           "OPG; CNSC licence application submitted; site prep underway",
        "delete_wna_proposed_n": 0,                   # net-new Planned; WNA Canada Planned ~400 MW different projects
    },
    {
        "reactor_id":            "NAMED_OPG_Darlington_BWRX_2",
        "name":                  "OPG Darlington BWRX-300 Unit 2",
        "country":               "CANADA",
        "status":                "Proposed",
        "net_capacity_mw":       300,
        "expected_online_year":  2036,
        "reactor_type":          "BWR",
        "is_smr":                1,
        "pipeline_probability":  0.65,
        "data_source":           "OPG multi-unit plan (4 units at Darlington)",
        "delete_wna_proposed_n": 0,                   # see bulk delete below
    },
    {
        "reactor_id":            "NAMED_OPG_Darlington_BWRX_3",
        "name":                  "OPG Darlington BWRX-300 Unit 3",
        "country":               "CANADA",
        "status":                "Proposed",
        "net_capacity_mw":       300,
        "expected_online_year":  2037,
        "reactor_type":          "BWR",
        "is_smr":                1,
        "pipeline_probability":  0.60,
        "data_source":           "OPG multi-unit plan",
        "delete_wna_proposed_n": 0,
    },
    {
        "reactor_id":            "NAMED_OPG_Darlington_BWRX_4",
        "name":                  "OPG Darlington BWRX-300 Unit 4",
        "country":               "CANADA",
        "status":                "Proposed",
        "net_capacity_mw":       300,
        "expected_online_year":  2038,
        "reactor_type":          "BWR",
        "is_smr":                1,
        "pipeline_probability":  0.55,
        "data_source":           "OPG multi-unit plan",
        "delete_wna_proposed_n": 0,
    },
]

# ── Double-counting cleanup ───────────────────────────────────────────────────
# Named Proposed projects that overlap with WNA generic entries.
# We delete this many WNA generic Proposed rows per country so total MW
# stays approximately constant after adding the named entries.
#
# USA: Adding 5×100 MW Kairos (500 MW) + 3×200 MW X-energy (600 MW) = 1100 MW
#      WNA generic US Proposed unit_mw ≈ 302 MW → delete 4 entries (~1208 MW)
#      Net: -108 MW (negligible on a 7.5 GW US Proposed pool)
#
# Canada: Adding 3×300 MW OPG Darlington Proposed (900 MW)
#         WNA generic Canada unit_mw ≈ 533 MW → delete 2 entries (~1067 MW)
#         Net: -167 MW (conservative; keeps total Canada Proposed slightly lower)
WNA_GENERIC_DELETIONS = {
    "UNI": 4,   # USA — prefix used in WNA_PR_UNI_* reactor_ids
    "CAN": 2,   # Canada — prefix used in WNA_PR_CAN_* reactor_ids
}


def run_announced_smr(conn: sqlite3.Connection) -> None:
    """Insert named SMR projects and clean up equivalent WNA generic entries."""
    cur = conn.cursor()
    inserted = 0
    skipped = 0

    for proj in ANNOUNCED_SMRS:
        country = proj["country"]
        region = REGION_MAP.get(country.upper(), "Emerging & Rest")
        cur.execute("""
            INSERT OR REPLACE INTO reactors
            (reactor_id, name, country, region, status,
             net_capacity_mw, reactor_type,
             expected_online_year, expected_online_year_source,
             pipeline_probability,
             is_smr, data_source, last_updated)
            VALUES (?, ?, ?, ?, ?,
                    ?, ?,
                    ?, ?,
                    ?,
                    ?, ?, ?)
        """, (
            proj["reactor_id"],
            proj["name"],
            country,
            region,
            proj["status"],
            proj["net_capacity_mw"],
            proj["reactor_type"],
            proj["expected_online_year"],
            "CorporateAnnouncement",
            proj["pipeline_probability"],
            proj["is_smr"],
            proj["data_source"],
            str(date.today()),
        ))
        if cur.rowcount:
            inserted += 1
        else:
            skipped += 1

    # ── Remove equivalent WNA generic Proposed entries to avoid double-counting
    total_deleted = 0
    for country_prefix, n_delete in WNA_GENERIC_DELETIONS.items():
        # Fetch WNA generic Proposed IDs for this country prefix, ordered by year desc
        # (remove later/more speculative entries first)
        cur.execute("""
            SELECT reactor_id FROM reactors
            WHERE reactor_id LIKE ? AND status = 'Proposed'
            ORDER BY expected_online_year DESC
            LIMIT ?
        """, (f"WNA_PR_{country_prefix}_%", n_delete))
        ids_to_delete = [row[0] for row in cur.fetchall()]
        if ids_to_delete:
            cur.execute(
                f"DELETE FROM reactors WHERE reactor_id IN ({','.join('?'*len(ids_to_delete))})",
                ids_to_delete
            )
            total_deleted += len(ids_to_delete)

    conn.commit()

    # Log data source
    cur.execute("""
        INSERT OR REPLACE INTO data_sources_log
        (source_name, data_as_of_date, ingestion_date, version_note)
        VALUES (?, ?, ?, ?)
    """, (
        "CorporateSMR",
        "2026-03-20",
        str(date.today()),
        "Named corporate SMR announcements: TerraPower Natrium, OPG Darlington BWRX-300, "
        "Google/Kairos KP-FHR, Amazon/X-energy Xe-100"
    ))
    conn.commit()

    print(f"  ✓ Named SMR projects: {inserted} inserted, {skipped} replaced, "
          f"{total_deleted} WNA generic entries removed (double-count cleanup)")
    _print_named_summary(conn)


def _print_named_summary(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("""
        SELECT name, status, net_capacity_mw, expected_online_year, region
        FROM reactors
        WHERE reactor_id LIKE 'NAMED_%'
        ORDER BY expected_online_year, name
    """)
    rows = cur.fetchall()
    if rows:
        print("    Named projects:")
        for name, status, mw, yr, region in rows:
            print(f"      {yr}  {status:<9}  {mw:>4} MW  {name}  [{region}]")
