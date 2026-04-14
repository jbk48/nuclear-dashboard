"""
wna_pipeline.py — Ingest WNA "Plans for New Reactors Worldwide" pipeline data.

Source: world-nuclear.org/information-library/current-and-future-generation/
        plans-for-new-reactors-worldwide  (retrieved 2026-03-17)

Adds two tiers of pipeline units not covered by PRIS:
  Tier A — WNA "Planned" units net of what PRIS already holds → status="Planned"
  Tier B — WNA "Proposed" units for countries with credible programmes
            → status="Proposed"

To avoid double-counting, the script queries the current DB for each country's
existing Planned/UC GW and only inserts the DELTA above what PRIS provided.

Expected online years are estimated from country-specific build momentum.
Units are batched into ~1–1.5 GW representative entries.
"""
import sqlite3
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config import DB_PATH, BASELINE_YEAR, REGION_MAP


# ── WNA country-level data (retrieved 2026-03-17) ─────────────────────────
# Format: country_upper: (planned_n, planned_mw, proposed_n, proposed_mw)
WNA_COUNTRY_DATA: dict[str, tuple[int, int, int, int]] = {
    "ARGENTINA":              (0,      0,    4,   1200),
    "ARMENIA":                (0,      0,    1,   1060),
    "BANGLADESH":             (0,      0,    2,   2400),
    "BULGARIA":               (2,   2500,    0,      0),
    "CANADA":                 (2,    400,   12,   6400),
    "CHINA":                  (42, 48131,  144, 171875),
    "CZECH REPUBLIC":         (3,   2570,    4,   3340),
    "CZECHIA":                (3,   2570,    4,   3340),
    "ESTONIA":                (0,      0,    2,    600),
    "FRANCE":                 (2,   3300,    4,   6600),
    "INDIA":                  (14,  8275,   26,  30800),
    "INDONESIA":              (0,      0,    2,    500),
    "IRAN, ISLAMIC REPUBLIC OF": (2, 1417,  4,   5000),
    "IRAN,ISL.REP":           (2,   1417,    4,   5000),
    "JAPAN":                  (0,      0,    1,   1385),
    "KAZAKHSTAN":             (2,   2400,    2,   2400),
    "KOREA, REPUBLIC OF":     (1,   1400,    0,      0),
    "REPUBLIC OF KOREA":      (1,   1400,    0,      0),
    "NETHERLANDS":            (0,      0,    2,   2650),
    "POLAND":                 (7,   4950,   22,   8800),
    "ROMANIA":                (8,   1902,    0,      0),
    "RUSSIA":                 (23, 21655,    9,    754),
    "RUSSIAN FEDERATION":     (23, 21655,    9,    754),
    "SAUDI ARABIA":           (0,      0,    2,   2800),
    "SLOVAKIA":               (0,      0,    1,   1200),
    "SLOVENIA":               (0,      0,    1,   1200),
    "SOUTH AFRICA":           (0,      0,    2,   2500),
    "SWEDEN":                 (5,   1500,    2,   2500),
    "TURKEY":                 (0,      0,    8,   9600),
    "UKRAINE":                (2,   2500,    7,   8750),
    "UNITED KINGDOM":         (3,   3830,   16,   2740),
    "UNITED STATES OF AMERICA": (0,    0,   25,   7540),
    "UZBEKISTAN":             (4,   2110,    0,      0),
    "VIET NAM":               (0,      0,    4,   1000),
    "VIETNAM":                (0,      0,    4,   1000),
}

# Countries whose Proposed programme is credible enough to include:
# (Fraction of WNA Proposed MW to add; rest is too speculative for model)
PROPOSED_INCLUSION_FRACTION: dict[str, float] = {
    "CHINA":                  0.25,   # Enormous pipeline; highly speculative beyond confirmed plans
    "INDIA":                  0.50,   # Long-standing expansion programme; partially credible
    "RUSSIA":                 1.00,   # Rosatom floating/land projects; reasonably well-documented
    "RUSSIAN FEDERATION":     1.00,
    "UNITED STATES OF AMERICA": 1.00, # SMR + new large plant applications
    "UNITED KINGDOM":         1.00,   # Government-backed new build
    "FRANCE":                 1.00,   # EPR2 programme
    "CANADA":                 1.00,   # SMR roadmap
    "POLAND":                 1.00,   # AP1000 + SMR programme
    "UKRAINE":                0.75,
    "TURKEY":                 0.50,   # Akkuyu built; Sinop uncertain
    "CZECH REPUBLIC":         1.00,
    "CZECHIA":                1.00,
    "SWEDEN":                 1.00,
    "SAUDI ARABIA":           0.75,
    "SOUTH AFRICA":           0.50,
    "KAZAKHSTAN":             1.00,
    "NETHERLANDS":            1.00,
    "SLOVAKIA":               1.00,
    "SLOVENIA":               1.00,
    "ROMANIA":                1.00,
    "ARMENIA":                0.75,
    "BULGARIA":               1.00,
    "ARGENTINA":              1.00,
    "INDONESIA":              0.50,
    "VIET NAM":               0.50,
    "VIETNAM":                0.50,
    "ESTONIA":                0.75,
}

# Planned online-year windows by country (start_yr, end_yr)
# For Planned: typically 6–16 years out from 2024
# For Proposed: typically 12–26 years out
PLANNED_WINDOW: dict[str, tuple[int, int]] = {
    "CHINA":                  (2030, 2040),
    "RUSSIA":                 (2030, 2042),
    "RUSSIAN FEDERATION":     (2030, 2042),
    "INDIA":                  (2030, 2040),
    "ROMANIA":                (2030, 2038),
    "FRANCE":                 (2035, 2040),
    "CZECH REPUBLIC":         (2035, 2042),
    "CZECHIA":                (2035, 2042),
    "POLAND":                 (2033, 2042),
    "UNITED KINGDOM":         (2035, 2045),
    "SWEDEN":                 (2034, 2044),
    "BULGARIA":               (2033, 2038),
    "KOREA, REPUBLIC OF":     (2031, 2036),
    "REPUBLIC OF KOREA":      (2031, 2036),
    "IRAN, ISLAMIC REPUBLIC OF": (2030, 2038),
    "IRAN,ISL.REP":           (2030, 2038),
    "KAZAKHSTAN":             (2033, 2040),
    "UZBEKISTAN":             (2030, 2038),
    "HUNGARY":                (2031, 2035),
    "CANADA":                 (2034, 2042),
}
DEFAULT_PLANNED_WINDOW = (2032, 2042)

PROPOSED_WINDOW: dict[str, tuple[int, int]] = {
    "CHINA":                  (2035, 2050),
    "INDIA":                  (2035, 2050),
    "RUSSIA":                 (2038, 2050),
    "RUSSIAN FEDERATION":     (2038, 2050),
    "UNITED STATES OF AMERICA": (2032, 2045),
    "UNITED KINGDOM":         (2038, 2050),
    "FRANCE":                 (2038, 2050),
    "CANADA":                 (2035, 2048),
    "POLAND":                 (2038, 2050),
    "UKRAINE":                (2033, 2048),
    "TURKEY":                 (2035, 2050),
    "CZECH REPUBLIC":         (2040, 2050),
    "CZECHIA":                (2040, 2050),
    "SWEDEN":                 (2040, 2050),
    "SAUDI ARABIA":           (2038, 2050),
    "SOUTH AFRICA":           (2038, 2050),
    "KAZAKHSTAN":             (2040, 2050),
    "NETHERLANDS":            (2035, 2045),
    "SLOVAKIA":               (2038, 2048),
    "SLOVENIA":               (2038, 2048),
    "ARMENIA":                (2040, 2050),
    "ARGENTINA":              (2035, 2048),
    "INDONESIA":              (2040, 2050),
    "VIET NAM":               (2038, 2050),
    "VIETNAM":                (2038, 2050),
    "ESTONIA":                (2038, 2048),
    "BULGARIA":               (2035, 2045),
    "ROMANIA":                (2035, 2048),
    "KAZAKHSTAN":             (2038, 2050),
}
DEFAULT_PROPOSED_WINDOW = (2038, 2050)

# Typical reactor unit size (MW net) by country/programme
TYPICAL_UNIT_MW: dict[str, int] = {
    "CHINA":                  1100,
    "RUSSIA":                  900,
    "RUSSIAN FEDERATION":      900,
    "INDIA":                   700,
    "UNITED STATES OF AMERICA": 300,  # SMR-weighted
    "UNITED KINGDOM":           170,  # SMR + GW-scale mix
    "FRANCE":                  1500,  # EPR2
    "CANADA":                   300,  # SMR roadmap
    "POLAND":                   700,
    "ROMANIA":                  700,
    "CZECH REPUBLIC":           900,
    "CZECHIA":                  900,
    "SWEDEN":                   300,
    "BULGARIA":                1250,
    "UKRAINE":                 1250,
    "TURKEY":                  1200,
    "KAZAKHSTAN":              1200,
    "UZBEKISTAN":              1200,
    "NETHERLANDS":             1325,
    "SAUDI ARABIA":            1400,
    "SOUTH AFRICA":            1250,
    "ARMENIA":                 1060,
    "ARGENTINA":                300,
    "ESTONIA":                  300,
}
DEFAULT_UNIT_MW = 1000


def _unit_mw(country: str) -> int:
    return TYPICAL_UNIT_MW.get(country.upper(), DEFAULT_UNIT_MW)


def _spread_online_years(n_units: int, window: tuple[int, int]) -> list[int]:
    """Spread n_units evenly across [start, end] window."""
    start, end = window
    span = max(end - start, 1)
    return [start + round(i * span / max(n_units - 1, 1)) for i in range(n_units)]


def _get_pris_pipeline_mw(conn: sqlite3.Connection, country_upper: str) -> dict[str, float]:
    """Return {status: total_mw} for UC + Planned in DB for this country."""
    cur = conn.cursor()
    cur.execute("""
        SELECT status, SUM(COALESCE(net_capacity_mw, 0))
        FROM reactors
        WHERE upper(country) = ? AND status IN ('UnderConstruction', 'Planned', 'Proposed')
        GROUP BY status
    """, (country_upper,))
    return {row[0]: row[1] for row in cur.fetchall()}


def _insert_reactor(cur, reactor_id: str, name: str, country: str, status: str,
                    net_mw: float, online_yr: int, reactor_type: str = "PWR"):
    region = REGION_MAP.get(country.upper(), "Emerging & Rest")
    cur.execute("""
        INSERT OR IGNORE INTO reactors
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
        reactor_id, name, country, region, status,
        net_mw, reactor_type,
        online_yr, "WNA",
        0.70 if status == "Planned" else 0.30,
        1 if net_mw < 300 else 0,
        "WNA_Pipeline",
        str(date.today()),
    ))


def run_wna_pipeline(conn: sqlite3.Connection) -> None:
    """Ingest WNA pipeline data, adding net-new units beyond PRIS."""
    cur = conn.cursor()
    inserted_planned = 0
    inserted_proposed = 0

    for country_upper, (pl_n, pl_mw, pr_n, pr_mw) in WNA_COUNTRY_DATA.items():
        # Skip duplicate country keys (e.g. RUSSIAN FEDERATION = RUSSIA)
        if country_upper in ("RUSSIAN FEDERATION", "CZECHIA", "IRAN,ISL.REP",
                              "REPUBLIC OF KOREA", "VIETNAM"):
            continue

        pris = _get_pris_pipeline_mw(conn, country_upper)
        unit_mw = _unit_mw(country_upper)

        # ── Planned: add delta above PRIS Planned ────────────────────────────
        pris_planned_mw = pris.get("Planned", 0.0)
        delta_planned_mw = max(pl_mw - pris_planned_mw, 0)
        if delta_planned_mw > 200:   # threshold: ignore trivial deltas
            n_units = max(1, round(delta_planned_mw / unit_mw))
            window = PLANNED_WINDOW.get(country_upper, DEFAULT_PLANNED_WINDOW)
            years = _spread_online_years(n_units, window)
            for i, yr in enumerate(years):
                rid = f"WNA_PL_{country_upper[:3]}_{yr}_{i+1:02d}"
                name = f"{country_upper.title()} Planned-{yr}-{i+1}"
                mw = round(delta_planned_mw / n_units)
                _insert_reactor(cur, rid, name, country_upper, "Planned", mw, yr)
                inserted_planned += 1

        # ── Proposed: include a fraction for credible programmes ─────────────
        fraction = PROPOSED_INCLUSION_FRACTION.get(country_upper, 0.0)
        effective_pr_mw = round(pr_mw * fraction)
        if effective_pr_mw > 200:
            n_units = max(1, round(effective_pr_mw / unit_mw))
            # Cap China at 60 units to avoid overwhelming pipeline chart
            if country_upper == "CHINA":
                n_units = min(n_units, 40)
            window = PROPOSED_WINDOW.get(country_upper, DEFAULT_PROPOSED_WINDOW)
            years = _spread_online_years(n_units, window)
            for i, yr in enumerate(years):
                rid = f"WNA_PR_{country_upper[:3]}_{yr}_{i+1:02d}"
                name = f"{country_upper.title()} Proposed-{yr}-{i+1}"
                mw = round(effective_pr_mw / n_units)
                _insert_reactor(cur, rid, name, country_upper, "Proposed", mw, yr)
                inserted_proposed += 1

    conn.commit()

    # Log data source
    cur.execute("""
        INSERT OR REPLACE INTO data_sources_log
        (source_name, data_as_of_date, ingestion_date, version_note)
        VALUES (?, ?, ?, ?)
    """, ("WNA", "2026-01-01", str(date.today()),
          "WNA Plans for New Reactors Worldwide (retrieved 2026-03-17)"))
    conn.commit()

    print(f"  ✓ WNA pipeline: added {inserted_planned} Planned + {inserted_proposed} Proposed entries")
    _print_summary(conn)


def _print_summary(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("""
        SELECT status, COUNT(*), ROUND(SUM(net_capacity_mw)/1000.0, 1)
        FROM reactors WHERE status IN ('UnderConstruction','Planned','Proposed')
        GROUP BY status
    """)
    for row in cur.fetchall():
        print(f"    {row[0]}: {row[1]} units, {row[2]} GW")
