"""
config.py — Central configuration for the nuclear dashboard model.
All file paths and key constants live here.
"""
import os
from pathlib import Path

# ── Repo root ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent

# ── Database ───────────────────────────────────────────────────────────────
DB_PATH = ROOT / "data" / "nuclear.db"

# ── Raw data directories (source files, never modified) ───────────────────
# These are only used by ingest.py (one-time data loading, not needed at runtime).
# Override via environment variables when re-ingesting on a new machine:
#   export PRIS_DATA_DIR=/path/to/IAEA_PRIS
#   export PROJ_DATA_DIR=/path/to/IAEA_Projections
RAW_PRIS_DIR  = Path(os.environ.get("PRIS_DATA_DIR",  ROOT / "data" / "raw" / "pris"))
RAW_PROJ_DIR  = Path(os.environ.get("PROJ_DATA_DIR",  ROOT / "data" / "raw" / "projections"))
RAW_WNA_DIR   = ROOT / "data" / "raw" / "wna"
RAW_BENCH_DIR = ROOT / "data" / "raw" / "benchmarks"

# ── Processed staging directory ───────────────────────────────────────────
PROCESSED_DIR = ROOT / "data" / "processed"

# ── Model constants ────────────────────────────────────────────────────────
BASELINE_YEAR = 2024
HISTORICAL_START_YEAR = 2005
TRANSITION_YEAR = 2040          # Bottom-up → top-down switch
PROJECTION_END_YEAR = 2050

# ── Default pipeline probability weights ──────────────────────────────────
DEFAULT_PROB = {
    "UnderConstruction": 0.95,
    "Planned":           0.70,
    "Proposed":          0.30,
}

# ── Region definitions ────────────────────────────────────────────────────
# Maps PRIS country names (upper-case) → dashboard region label
REGION_MAP: dict[str, str] = {
    # United States — PRIS uses both full name and abbreviation
    "UNITED STATES OF AMERICA": "United States",
    "USA":                      "United States",
    # Canada & Mexico
    "CANADA":  "Canada & Mexico",
    "MEXICO":  "Canada & Mexico",
    # France
    "FRANCE":  "France",
    # United Kingdom — PRIS uses both full name and abbreviation
    "UNITED KINGDOM": "United Kingdom",
    "UK":             "United Kingdom",
    # Rest of Western Europe
    "BELGIUM":     "Rest of Western Europe",
    "FINLAND":     "Rest of Western Europe",
    "NETHERLANDS": "Rest of Western Europe",
    "SPAIN":       "Rest of Western Europe",
    "SWEDEN":      "Rest of Western Europe",
    "SWITZERLAND": "Rest of Western Europe",
    "GERMANY":     "Rest of Western Europe",
    "ITALY":       "Rest of Western Europe",
    "AUSTRIA":     "Rest of Western Europe",
    # Eastern Europe — multiple PRIS spellings for Czech Republic
    "CZECH REPUBLIC": "Eastern Europe",
    "CZECH REP.":     "Eastern Europe",
    "CZECHIA":        "Eastern Europe",
    "HUNGARY":        "Eastern Europe",
    "POLAND":         "Eastern Europe",
    "ROMANIA":        "Eastern Europe",
    "SLOVAKIA":       "Eastern Europe",
    "SLOVENIA":       "Eastern Europe",
    "UKRAINE":        "Eastern Europe",
    "BULGARIA":       "Eastern Europe",
    "LITHUANIA":      "Eastern Europe",
    "ESTONIA":        "Eastern Europe",
    # Russia
    "RUSSIA":             "Russia",
    "RUSSIAN FEDERATION": "Russia",
    # China
    "CHINA": "China",
    # South Asia
    "INDIA":      "South Asia",
    "PAKISTAN":   "South Asia",
    "BANGLADESH": "South Asia",
    # East Asia — PRIS uses comma-separated names without space in some tables
    "JAPAN":               "East Asia",
    "KOREA, REPUBLIC OF":  "East Asia",
    "KOREA,REP.OF":        "East Asia",
    "KOREA,REPUBLIC OF":   "East Asia",
    "REPUBLIC OF KOREA":   "East Asia",
    "TAIWAN, CHINA":       "East Asia",
    "TAIWAN,CHINA":        "East Asia",
    "TAIWAN":              "East Asia",
    # Southeast Asia
    "VIET NAM":    "Southeast Asia",
    "VIETNAM":     "Southeast Asia",
    "INDONESIA":   "Southeast Asia",
    "THAILAND":    "Southeast Asia",
    "PHILIPPINES": "Southeast Asia",
    "MALAYSIA":    "Southeast Asia",
    # Emerging & Rest (includes LatAm, Middle East, Africa, Central Asia)
    "UNITED ARAB EMIRATES":     "Emerging & Rest",
    "UAE":                      "Emerging & Rest",
    "TURKEY":                   "Emerging & Rest",
    "TÜRKIYE":                  "Emerging & Rest",
    "EGYPT":                    "Emerging & Rest",
    "SAUDI ARABIA":             "Emerging & Rest",
    "JORDAN":                   "Emerging & Rest",
    "IRAN, ISLAMIC REPUBLIC OF":"Emerging & Rest",
    "IRAN,ISL.REP":             "Emerging & Rest",
    "IRAN":                     "Emerging & Rest",
    "KAZAKHSTAN":               "Emerging & Rest",
    "UZBEKISTAN":               "Emerging & Rest",
    "ARMENIA":                  "Emerging & Rest",
    "BRAZIL":                   "Emerging & Rest",
    "ARGENTINA":                "Emerging & Rest",
    "SOUTH AFRICA":             "Emerging & Rest",
    "NIGERIA":                  "Emerging & Rest",
    "KENYA":                    "Emerging & Rest",
    "GHANA":                    "Emerging & Rest",
    "MOROCCO":                  "Emerging & Rest",
    "BELARUS":                  "Emerging & Rest",
}

REGIONS = sorted(set(REGION_MAP.values()))

# Maps IAEA RDS-1 region labels → our dashboard regions (for benchmark splitting)
# Where one IAEA region maps to multiple dashboard regions, we split by 2024 capacity share
IAEA_TO_DASHBOARD_REGIONS: dict[str, list[str]] = {
    "Northern America":                     ["United States", "Canada & Mexico"],
    "Latin America and the Caribbean":      ["Emerging & Rest"],
    "Northern Western and Southern Europe": ["France", "United Kingdom", "Rest of Western Europe"],
    "Eastern Europe":                       ["Eastern Europe"],
    "Africa":                               ["Emerging & Rest"],
    "Western Asia":                         ["Emerging & Rest"],
    "Southern Asia":                        ["South Asia"],
    "Central and Eastern Asia":             ["China", "East Asia"],
    "South-eastern Asia":                   ["Southeast Asia"],
    "Oceania":                              ["Emerging & Rest"],
}
