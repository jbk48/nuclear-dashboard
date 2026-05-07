"""
claude_analyst.py — Read-only analytical agent with tool-calling.

Owns:
- _ANALYST_SYSTEM        : analyst system prompt
- _ANALYST_TOOLS         : tool definitions (14 read-only tools)
- All _tool_*()          : tool implementations backed by SQLite queries
- _execute_analyst_tool(): tool dispatcher
- call_claude_analyst()  : main entry point — runs the tool-calling loop
- Session-state helpers  : _active_proj_dict(), _active_proj_lookup(),
                           _ACTIVE_SENTINEL, _analyst_resolve_scenario(),
                           _analyst_resolve_region()
- DB helpers             : _analyst_db(), _region_where()
- Supplier lookup        : _SUPPLIER_NAMES, _supplier_label()

Imports from:
- config  : REGIONS, DB_PATH
"""
import json
import sqlite3 as _sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import REGIONS

# ── Sentinel & session-state helpers ─────────────────────────────────────────

_ACTIVE_SENTINEL = "__active__"


def _active_proj_dict() -> dict | None:
    """Return the live custom projection {region: DataFrame} if one exists, else None.

    Priority:
    1. wi_proj_all  — reactor-level overrides (+ any custom levers) already applied
    2. _custom_projection — custom lever settings only (no reactor overrides)
    Both are populated by the Scenario Lab when the user clicks Apply.
    Format: {region: DataFrame} with columns
        [year, capacity_operating_gw, retirements_this_year_gw,
         additions_this_year_gw, capacity_retired_ytd_gw,
         capacity_added_ytd_gw, is_bottom_up]
    """
    try:
        import streamlit as _st
        wi = _st.session_state.get("wi_proj_all")
        if wi:
            return wi
        cp = _st.session_state.get("_custom_projection")
        if cp:
            return cp
    except Exception:
        pass
    return None


def _active_proj_lookup(proj_dict: dict, region: str, year: int,
                        col: str = "capacity_operating_gw") -> float | None:
    """Return a single value from an active projection dict."""
    df = proj_dict.get(region)
    if df is None:
        return None
    row = df[df["year"] == year]
    if row.empty or col not in row.columns:
        return None
    v = row.iloc[0][col]
    return None if (v != v) else round(float(v), 3)  # NaN → None


def _analyst_resolve_scenario(scenario: str) -> str:
    """Map natural language scenario name to DB scenario_id or _ACTIVE_SENTINEL."""
    try:
        import streamlit as _st
        preset = _st.session_state.get("preset_selector", "base") or "base"
        _has_active = _active_proj_dict() is not None
        active_db = preset if preset not in ("custom", "") else "base"
    except Exception:
        preset = "base"
        _has_active = False
        active_db = "base"

    s = scenario.lower().strip()
    _active_aliases = {"active", "current", "selected", "custom",
                       "this scenario", "current scenario", "the scenario"}
    if s in _active_aliases:
        return _ACTIVE_SENTINEL if _has_active else active_db

    _map = {
        "base": "base", "default": "base", "current policy": "base",
        "optimistic": "optimistic", "best": "optimistic", "high": "optimistic",
        "conservative": "conservative", "moderate": "conservative",
        "decline": "decline", "worst": "decline", "low": "decline",
        "accelerated": "decline", "historical": "base",
    }
    return _map.get(s, "base")


def _analyst_resolve_region(region: str) -> str:
    """Normalize a region string to a DB region name."""
    _valid = {r.lower(): r for r in (REGIONS + ["Global"])}
    low = region.lower().strip()
    if low in _valid:
        return _valid[low]
    _aliases = {
        "us": "United States", "usa": "United States", "america": "United States",
        "uk": "United Kingdom", "britain": "United Kingdom", "england": "United Kingdom",
        "world": "Global", "worldwide": "Global", "all regions": "Global", "all": "Global",
        "eu": "Rest of Western Europe",
    }
    return _aliases.get(low, region)


def _analyst_db():
    from config import DB_PATH
    return _sqlite3.connect(DB_PATH)


def _region_where(reg: str) -> tuple[str, list]:
    """Return (WHERE clause fragment, params) for filtering reactors by region or country."""
    if reg in (REGIONS + ["Global"]):
        if reg == "Global":
            return "1=1", []
        return "region = ?", [reg]
    # Treat as country name (case-insensitive substring)
    return "UPPER(country) LIKE UPPER(?)", [f"%{reg}%"]


# ── Supplier lookup ───────────────────────────────────────────────────────────

_SUPPLIER_NAMES: dict[str, str] = {
    "WH":        "Westinghouse",
    "FRAM":      "Framatome",
    "AEM":       "Atomenergomash (Rosatom)",
    "GE":        "GE Hitachi",
    "NPCIL":     "NPCIL (India)",
    "OH/AECL":   "Ontario Hydro / AECL",
    "DHICKOPC":  "Dongfang Electric",
    "DEC":       "Dongfang Electric Corp.",
    "ŠKODA":     "Škoda JS",
    "CFHI":      "China First Heavy Industries",
    "CE":        "Combustion Engineering",
    "CNNC":      "CNNC (China)",
    "NPIC":      "NPIC (China)",
    "AEE":       "Atomenergoexport (Rosatom)",
    "NPC":       "NPC (China)",
    "AECL":      "Atomic Energy of Canada",
    "PAIP":      "PAIP (China)",
    "B&W":       "Babcock & Wilcox",
    "KEPCO":     "KEPCO E&C",
    "KWU":       "KWU / Siemens",
    "CNEA":      "CNEA (Argentina)",
    "SNERDI":    "SNERDI (China)",
    "SNPEMC":    "SNPEMC (China)",
    "SENPE":     "SENPE (China)",
    "ROSATOM":   "Rosatom",
    "ANSALDO":   "Ansaldo Nucleare",
    "SMP":       "SMP",
    "ENUSA":     "ENUSA",
    "MINATOM":   "Minatom (Russia)",
}


def _supplier_label(code: str) -> str:
    """Return human-readable OEM name, falling back to raw code."""
    if not code:
        return "Unknown"
    return _SUPPLIER_NAMES.get(code.strip().upper(), code.strip())


# ── Analyst system prompt ─────────────────────────────────────────────────────

_ANALYST_SYSTEM = """\
You are an analytical assistant for a nuclear capacity projection dashboard (2024–2050).
You have live access to reactor data, scenario projections, historical capacity figures,
and IEA/IAEA benchmark reference lines via the provided tools.

## Your job
Answer the user's questions using the tools — NEVER answer quantitative questions
from memory or training knowledge. Always retrieve the actual numbers.

## How to respond
1. Call the relevant tool(s) to get the numbers.
2. Give a direct, concise answer (2–4 sentences) citing the specific figures,
   scenario, and year. Round to 1 decimal place in prose.
3. If the data reveals a strategically notable observation — a gap vs. a target,
   a concentration risk, a retirement cliff, or a scenario inflection — add 1–2
   sentences of strategic context clearly framed as interpretation. Skip this if
   it adds no real value.
4. If the user asks you to *apply* a change to the model (e.g. "retire all French
   reactors"), note that scenario changes are handled via the controls in the Lab
   and offer to describe which levers to use — but do not attempt the change yourself.

## Scenarios
- base: Current policy, medium pipeline realization (default)
- optimistic: Extended operations, high pipeline realization
- conservative: Moderate extensions, under-construction reactors only
- decline: No new extensions, accelerated retirement
- active / current / custom: the live scenario the user has built — includes any
  lever changes AND reactor-level overrides (e.g. early retirements, restarts)
  applied in the Scenario Lab. ALWAYS prefer "active" when the user asks about
  "this scenario", "the current scenario", "what I've set up", or any phrasing
  that refers to changes they've just made.

## Regions
Global | United States | China | France | Russia | United Kingdom | East Asia |
Eastern Europe | Canada & Mexico | South Asia | Rest of Western Europe |
Southeast Asia | Emerging & Rest

## Benchmarks
- IAEA RDS-1 2025: Low, High, Observed (2024 anchor)
- IEA WEO 2024: STEPS, APS, LowNuclearCase
Note: IEA/IAEA figures use total installed capacity (includes LTS reactors).
This model tracks operating capacity. The ~4 GW gap at 2024 is methodological.

## Key model facts
- Baseline year: 2024 | Bottom-up phase: 2024–2040 | Top-down: 2041–2050
- Historical data available: 2005–2024
"""


# ── Tool definitions ──────────────────────────────────────────────────────────

_ANALYST_TOOLS = [
    {
        "name": "get_capacity",
        "description": (
            "Get nuclear capacity (GW) for a specific scenario, region, and year. "
            "Covers historical years (2005–2023) and projection years (2024–2050)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "scenario": {
                    "type": "string",
                    "description": "base | optimistic | conservative | decline | active | historical",
                },
                "region": {
                    "type": "string",
                    "description": (
                        "Region or 'Global'. Options: Global, United States, China, France, "
                        "Russia, United Kingdom, East Asia, Eastern Europe, Canada & Mexico, "
                        "South Asia, Rest of Western Europe, Southeast Asia, Emerging & Rest"
                    ),
                },
                "year": {"type": "integer", "description": "Year 2005–2050"},
            },
            "required": ["scenario", "region", "year"],
        },
    },
    {
        "name": "get_timeseries",
        "description": (
            "Get year-by-year capacity (GW) for a scenario and region over a range. "
            "Combines historical and projection data transparently."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "scenario":   {"type": "string", "description": "base/optimistic/conservative/decline/active"},
                "region":     {"type": "string", "description": "Region or Global"},
                "start_year": {"type": "integer", "description": "Start year (2005–2050)"},
                "end_year":   {"type": "integer", "description": "End year (2005–2050)"},
            },
            "required": ["scenario", "region", "start_year", "end_year"],
        },
    },
    {
        "name": "compare_scenarios",
        "description": (
            "Compare capacity across all four scenarios (decline/conservative/base/optimistic) "
            "for a given region and year. Returns GW values and the spread."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "region": {"type": "string", "description": "Region or Global"},
                "year":   {"type": "integer", "description": "Year 2024–2050"},
            },
            "required": ["region", "year"],
        },
    },
    {
        "name": "get_benchmark",
        "description": (
            "Get IEA/IAEA benchmark reference capacity values for a given year. "
            "Sources: IEA WEO 2024 (STEPS/APS/LowNuclearCase) and "
            "IAEA RDS-1 2025 (Low/High/Observed)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "year":   {"type": "integer", "description": "Year (benchmarks span 2024–2050)"},
                "region": {"type": "string",  "description": "Usually 'Global'"},
            },
            "required": ["year"],
        },
    },
    {
        "name": "get_regional_breakdown",
        "description": (
            "Get capacity (GW) for every region in a given scenario and year, "
            "with each region's percentage share of the global total."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "scenario": {"type": "string", "description": "base/optimistic/conservative/decline/active"},
                "year":     {"type": "integer", "description": "Year 2024–2050"},
            },
            "required": ["scenario", "year"],
        },
    },
    {
        "name": "get_retirements",
        "description": (
            "Get total capacity retired (GW) per year for a scenario and region "
            "over a range of years. Includes peak retirement year."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "scenario":   {"type": "string"},
                "region":     {"type": "string"},
                "start_year": {"type": "integer"},
                "end_year":   {"type": "integer"},
            },
            "required": ["scenario", "region", "start_year", "end_year"],
        },
    },
    {
        "name": "get_additions",
        "description": (
            "Get total capacity added (GW) per year from the pipeline for a scenario "
            "and region over a range of years."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "scenario":   {"type": "string"},
                "region":     {"type": "string"},
                "start_year": {"type": "integer"},
                "end_year":   {"type": "integer"},
            },
            "required": ["scenario", "region", "start_year", "end_year"],
        },
    },
    {
        "name": "find_crossover",
        "description": (
            "Find the year when capacity in region_a first exceeds capacity in region_b "
            "(or exceeds a GW threshold like '500 GW'). "
            "Returns the crossover year or null if no crossover within 2024–2050."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "scenario": {"type": "string"},
                "region_a": {"type": "string", "description": "First region"},
                "region_b": {
                    "type": "string",
                    "description": "Second region, or a GW threshold (e.g. '500 GW')",
                },
            },
            "required": ["scenario", "region_a", "region_b"],
        },
    },
    {
        "name": "get_reactor_info",
        "description": (
            "Look up a specific reactor by name (partial match). "
            "Returns capacity, country, region, status, commissioning year, "
            "retirement date, and reactor type."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Reactor name or partial name (e.g. 'Palo Verde', 'Hinkley')",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "get_fleet_summary",
        "description": (
            "Get fleet statistics for a region or country: reactor count, "
            "total capacity (GW), average commissioning year, and status breakdown."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "region": {
                    "type": "string",
                    "description": "Region name, country name, or 'Global'",
                },
            },
            "required": ["region"],
        },
    },
    {
        "name": "get_technology_breakdown",
        "description": (
            "Get a breakdown of reactor technologies (PWR, BWR, VVER, CANDU, AGR, etc.) "
            "and/or OEM/supplier (Westinghouse, GE, Rosatom/AEM, Framatome, etc.) "
            "for a region or country. Can filter by status (Operating, UnderConstruction, "
            "Planned, Proposed, or 'all'). Returns counts, total GW, and share percentages. "
            "Use this for questions about reactor types, technology mix, or who built the fleet."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "region": {
                    "type": "string",
                    "description": "Region name, country name, or 'Global'",
                },
                "status": {
                    "type": "string",
                    "description": (
                        "Filter by status: 'Operating', 'UnderConstruction', 'Planned', "
                        "'Proposed', or 'all' (default: 'Operating')"
                    ),
                },
                "group_by": {
                    "type": "string",
                    "description": (
                        "'type' to group by reactor_type (PWR/BWR/etc.), "
                        "'supplier' to group by OEM/nsss_supplier, "
                        "'model' for detailed reactor model. Default: 'type'."
                    ),
                },
            },
            "required": ["region"],
        },
    },
    {
        "name": "get_fleet_profile",
        "description": (
            "Get a detailed profile of a fleet for a region or country: age distribution "
            "(decade buckets), average and median age, average capacity factor (eaf_pct), "
            "SMR vs large reactor split, and retirement tier breakdown. "
            "Use this for questions about fleet age, utilization, or how many SMRs exist."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "region": {
                    "type": "string",
                    "description": "Region name, country name, or 'Global'",
                },
                "status": {
                    "type": "string",
                    "description": "Status filter: 'Operating' (default), 'all', or a specific status.",
                },
            },
            "required": ["region"],
        },
    },
    {
        "name": "get_pipeline_detail",
        "description": (
            "Get a detailed list of pipeline reactors (Under Construction, Planned, or Proposed) "
            "for a region or country. Returns name, country, capacity, expected online year, "
            "SMR flag, supplier (where available), and pipeline probability. "
            "Use this for questions like 'what is under construction in South Korea?' or "
            "'how many SMRs are planned in the US?'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "region": {
                    "type": "string",
                    "description": "Region name, country name, or 'Global'",
                },
                "status_filter": {
                    "type": "string",
                    "description": (
                        "Which pipeline stage to show: 'UnderConstruction', 'Planned', "
                        "'Proposed', or 'all' (default: 'all')"
                    ),
                },
                "smr_only": {
                    "type": "boolean",
                    "description": "If true, return only SMR reactors. Default: false.",
                },
            },
            "required": ["region"],
        },
    },
    {
        "name": "get_retirement_schedule",
        "description": (
            "Get the projected retirement schedule for a region or country: which reactors "
            "retire in which year, total GW retiring per year, and retirement tier breakdown. "
            "Use this for questions like 'when do US reactors retire?' or "
            "'how much capacity does France lose by 2040?'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "region": {
                    "type": "string",
                    "description": "Region name, country name, or 'Global'",
                },
                "start_year": {
                    "type": "integer",
                    "description": "Start year for the schedule (default: 2025)",
                },
                "end_year": {
                    "type": "integer",
                    "description": "End year for the schedule (default: 2050)",
                },
            },
            "required": ["region"],
        },
    },
]


# ── Tool implementations ──────────────────────────────────────────────────────

def _tool_get_capacity(scenario: str, region: str, year: int) -> dict:
    sc  = _analyst_resolve_scenario(scenario)
    reg = _analyst_resolve_region(region)

    # ── Active/custom scenario: read from session state ───────────────────
    if sc == _ACTIVE_SENTINEL and year >= 2024:
        proj = _active_proj_dict()
        if proj:
            val = _active_proj_lookup(proj, reg, year)
            if val is not None:
                return {"scenario": "active (custom)", "region": reg, "year": year,
                        "capacity_gw": round(val, 2), "source": "session_state"}
        return {"error": f"No active projection data for region='{reg}', year={year}"}

    conn = _analyst_db()
    c = conn.cursor()
    if year >= 2024:
        c.execute(
            "SELECT capacity_operating_gw FROM projections "
            "WHERE scenario_id=? AND region=? AND year=?",
            (sc, reg, year),
        )
        row = c.fetchone()
        if row:
            conn.close()
            return {"scenario": sc, "region": reg, "year": year,
                    "capacity_gw": round(row[0], 2), "source": "projection"}
    c.execute(
        "SELECT capacity_gw FROM historical_capacity WHERE region=? AND year=?",
        (reg, year),
    )
    row = c.fetchone()
    conn.close()
    if row:
        return {"scenario": "historical", "region": reg, "year": year,
                "capacity_gw": round(row[0], 2), "source": "historical"}
    return {"error": f"No data for scenario='{sc}', region='{reg}', year={year}"}


def _tool_get_timeseries(scenario: str, region: str, start_year: int, end_year: int) -> dict:
    sc  = _analyst_resolve_scenario(scenario)
    reg = _analyst_resolve_region(region)
    data: dict = {}

    # Historical portion (always from DB)
    if start_year < 2024:
        conn = _analyst_db()
        c = conn.cursor()
        c.execute(
            "SELECT year, capacity_gw FROM historical_capacity "
            "WHERE region=? AND year BETWEEN ? AND ? ORDER BY year",
            (reg, start_year, min(end_year, 2023)),
        )
        for yr, gw in c.fetchall():
            data[yr] = round(gw, 2)
        conn.close()

    # Projection portion
    if end_year >= 2024:
        if sc == _ACTIVE_SENTINEL:
            proj = _active_proj_dict()
            if proj:
                df = proj.get(reg)
                if df is not None:
                    for _, row in df.iterrows():
                        yr = int(row["year"])
                        if max(start_year, 2024) <= yr <= end_year:
                            v = row["capacity_operating_gw"]
                            if v == v:  # not NaN
                                data[yr] = round(float(v), 2)
        else:
            conn = _analyst_db()
            c = conn.cursor()
            c.execute(
                "SELECT year, capacity_operating_gw FROM projections "
                "WHERE scenario_id=? AND region=? AND year BETWEEN ? AND ? ORDER BY year",
                (sc, reg, max(start_year, 2024), end_year),
            )
            for yr, gw in c.fetchall():
                data[yr] = round(gw, 2)
            conn.close()

    if not data:
        return {"error": f"No data for {sc}/{reg}/{start_year}–{end_year}"}
    sc_label = "active (custom)" if sc == _ACTIVE_SENTINEL else sc
    return {"scenario": sc_label, "region": reg,
            "start_year": start_year, "end_year": end_year, "data": data}


def _tool_compare_scenarios(region: str, year: int) -> dict:
    reg = _analyst_resolve_region(region)
    conn = _analyst_db()
    c = conn.cursor()
    results: dict = {}
    for sc in ("decline", "conservative", "base", "optimistic"):
        c.execute(
            "SELECT capacity_operating_gw FROM projections "
            "WHERE scenario_id=? AND region=? AND year=?",
            (sc, reg, year),
        )
        row = c.fetchone()
        if row:
            results[sc] = round(row[0], 2)
    conn.close()
    # Add active/custom scenario if one exists
    proj = _active_proj_dict()
    if proj:
        val = _active_proj_lookup(proj, reg, year)
        if val is not None:
            results["active (custom)"] = round(val, 2)
    if not results:
        return {"error": f"No projection data for region='{reg}', year={year}"}
    vals = list(results.values())
    return {
        "region": reg, "year": year, "scenarios": results,
        "spread_gw": round(max(vals) - min(vals), 2),
    }


def _tool_get_benchmark(year: int, region: str = "Global") -> dict:
    reg = _analyst_resolve_region(region) if region else "Global"
    conn = _analyst_db()
    c = conn.cursor()
    c.execute(
        "SELECT source, scenario_name, capacity_gw FROM benchmarks "
        "WHERE region=? AND year=? ORDER BY source, scenario_name",
        (reg, year),
    )
    rows = c.fetchall()
    conn.close()
    if not rows:
        return {"error": f"No benchmark data for region='{reg}', year={year}"}
    return {
        "region": reg, "year": year,
        "benchmarks": {f"{src} / {sc}": round(gw, 1) for src, sc, gw in rows},
    }


def _tool_get_regional_breakdown(scenario: str, year: int) -> dict:
    sc = _analyst_resolve_scenario(scenario)

    if sc == _ACTIVE_SENTINEL:
        proj = _active_proj_dict()
        if not proj:
            return {"error": "No active custom scenario found in session."}
        data = {}
        for reg in REGIONS:
            val = _active_proj_lookup(proj, reg, year)
            if val is not None:
                data[reg] = round(val, 2)
        if not data:
            return {"error": f"No active projection data for year={year}"}
        total  = round(sum(data.values()), 2)
        shares = {r: round(v / total * 100, 1) for r, v in data.items()} if total else {}
        return {"scenario": "active (custom)", "year": year,
                "regions": dict(sorted(data.items(), key=lambda x: -x[1])),
                "shares_pct": shares, "total_gw": total}

    conn = _analyst_db()
    c = conn.cursor()
    c.execute(
        "SELECT region, capacity_operating_gw FROM projections "
        "WHERE scenario_id=? AND year=? AND region != 'Global' "
        "ORDER BY capacity_operating_gw DESC",
        (sc, year),
    )
    rows = c.fetchall()
    conn.close()
    if not rows:
        return {"error": f"No data for scenario='{sc}', year={year}"}
    data   = {reg: round(gw, 2) for reg, gw in rows}
    total  = round(sum(data.values()), 2)
    shares = {reg: round(gw / total * 100, 1) for reg, gw in data.items()} if total else {}
    return {"scenario": sc, "year": year, "regions": data,
            "shares_pct": shares, "total_gw": total}


def _tool_get_retirements(scenario: str, region: str, start_year: int, end_year: int) -> dict:
    sc  = _analyst_resolve_scenario(scenario)
    reg = _analyst_resolve_region(region)

    if sc == _ACTIVE_SENTINEL:
        proj = _active_proj_dict()
        if not proj:
            return {"error": "No active custom scenario found in session."}
        df = proj.get(reg)
        if df is None:
            return {"error": f"No active projection data for region='{reg}'"}
        sub  = df[(df["year"] >= start_year) & (df["year"] <= end_year)]
        data = {int(r["year"]): round(float(r["retirements_this_year_gw"]), 3)
                for _, r in sub.iterrows()
                if r["retirements_this_year_gw"] == r["retirements_this_year_gw"]}
        if not data:
            return {"error": f"No retirement data in active scenario for {reg}/{start_year}–{end_year}"}
        peak = max(data, key=data.get)
        return {"scenario": "active (custom)", "region": reg,
                "retirements_by_year_gw": data,
                "total_retired_gw": round(sum(data.values()), 2),
                "peak_year": peak, "peak_gw": data[peak]}

    conn = _analyst_db()
    c = conn.cursor()
    c.execute(
        "SELECT year, retirements_this_year_gw FROM projections "
        "WHERE scenario_id=? AND region=? AND year BETWEEN ? AND ? ORDER BY year",
        (sc, reg, start_year, end_year),
    )
    rows = c.fetchall()
    conn.close()
    if not rows:
        return {"error": f"No retirement data for {sc}/{reg}/{start_year}–{end_year}"}
    data = {yr: round(gw, 3) for yr, gw in rows}
    peak = max(data, key=data.get)
    return {"scenario": sc, "region": reg,
            "retirements_by_year_gw": data,
            "total_retired_gw": round(sum(data.values()), 2),
            "peak_year": peak, "peak_gw": data[peak]}


def _tool_get_additions(scenario: str, region: str, start_year: int, end_year: int) -> dict:
    sc  = _analyst_resolve_scenario(scenario)
    reg = _analyst_resolve_region(region)

    if sc == _ACTIVE_SENTINEL:
        proj = _active_proj_dict()
        if not proj:
            return {"error": "No active custom scenario found in session."}
        df = proj.get(reg)
        if df is None:
            return {"error": f"No active projection data for region='{reg}'"}
        sub  = df[(df["year"] >= start_year) & (df["year"] <= end_year)]
        data = {int(r["year"]): round(float(r["additions_this_year_gw"]), 3)
                for _, r in sub.iterrows()
                if r["additions_this_year_gw"] == r["additions_this_year_gw"]}
        if not data:
            return {"error": f"No additions data in active scenario for {reg}/{start_year}–{end_year}"}
        return {"scenario": "active (custom)", "region": reg,
                "additions_by_year_gw": data,
                "total_added_gw": round(sum(data.values()), 2)}

    conn = _analyst_db()
    c = conn.cursor()
    c.execute(
        "SELECT year, additions_this_year_gw FROM projections "
        "WHERE scenario_id=? AND region=? AND year BETWEEN ? AND ? ORDER BY year",
        (sc, reg, start_year, end_year),
    )
    rows = c.fetchall()
    conn.close()
    if not rows:
        return {"error": f"No additions data for {sc}/{reg}/{start_year}–{end_year}"}
    data = {yr: round(gw, 3) for yr, gw in rows}
    return {"scenario": sc, "region": reg,
            "additions_by_year_gw": data,
            "total_added_gw": round(sum(data.values()), 2)}


def _tool_find_crossover(scenario: str, region_a: str, region_b: str) -> dict:
    import re as _re
    sc    = _analyst_resolve_scenario(scenario)
    reg_a = _analyst_resolve_region(region_a)

    def _get_series(reg: str) -> dict:
        if sc == _ACTIVE_SENTINEL:
            proj = _active_proj_dict()
            if not proj:
                return {}
            df = proj.get(reg)
            if df is None:
                return {}
            return {int(r["year"]): float(r["capacity_operating_gw"])
                    for _, r in df.iterrows()
                    if 2024 <= int(r["year"]) <= 2050
                    and r["capacity_operating_gw"] == r["capacity_operating_gw"]}
        conn = _analyst_db()
        c = conn.cursor()
        c.execute(
            "SELECT year, capacity_operating_gw FROM projections "
            "WHERE scenario_id=? AND region=? AND year BETWEEN 2024 AND 2050 ORDER BY year",
            (sc, reg),
        )
        result = {yr: gw for yr, gw in c.fetchall()}
        conn.close()
        return result

    sc_label = "active (custom)" if sc == _ACTIVE_SENTINEL else sc
    series_a = _get_series(reg_a)

    _thresh = _re.match(r"(\d+(?:\.\d+)?)\s*(?:GW|gw)?$", region_b.strip())
    if _thresh:
        threshold = float(_thresh.group(1))
        if not series_a:
            return {"error": f"No data for region='{reg_a}'"}
        for yr in sorted(series_a):
            if series_a[yr] >= threshold:
                return {"scenario": sc_label, "region": reg_a, "threshold_gw": threshold,
                        "crossover_year": yr,
                        "capacity_at_crossover_gw": round(series_a[yr], 2)}
        return {"scenario": sc_label, "region": reg_a, "threshold_gw": threshold,
                "crossover_year": None,
                "note": f"{reg_a} does not reach {threshold} GW by 2050",
                "max_by_2050_gw": round(max(series_a.values()), 2)}

    reg_b = _analyst_resolve_region(region_b)
    series_b = _get_series(reg_b)
    if not series_a or not series_b:
        return {"error": "Missing data for one of the regions"}
    for yr in sorted(set(series_a) & set(series_b)):
        if series_a[yr] >= series_b[yr]:
            return {"scenario": sc_label, "region_a": reg_a, "region_b": reg_b,
                    "crossover_year": yr,
                    "a_gw": round(series_a[yr], 2), "b_gw": round(series_b[yr], 2)}
    last = max(set(series_a) & set(series_b))
    return {"scenario": sc_label, "region_a": reg_a, "region_b": reg_b,
            "crossover_year": None,
            "note": f"{reg_a} does not exceed {reg_b} by 2050 under {sc_label}",
            f"{reg_a}_2050_gw": round(series_a.get(last, 0), 2),
            f"{reg_b}_2050_gw": round(series_b.get(last, 0), 2)}


def _tool_get_reactor_info(name: str) -> dict:
    conn = _analyst_db()
    c = conn.cursor()
    c.execute(
        "SELECT name, country, region, status, net_capacity_mw, "
        "commercial_operation_date, retirement_date_used, "
        "actual_shutdown_date, restart_date, reactor_type, is_smr "
        "FROM reactors WHERE UPPER(name) LIKE UPPER(?) ORDER BY name LIMIT 10",
        (f"%{name}%",),
    )
    rows = c.fetchall()
    conn.close()
    if not rows:
        return {"error": f"No reactors found matching '{name}'"}
    reactors = []
    for (rname, country, region, status, cap_mw, cod, ret_date,
         shutdown, restart, rtype, is_smr) in rows:
        reactors.append({
            "name": rname, "country": country, "region": region,
            "status": status, "capacity_mw": cap_mw,
            "online_year": int(cod[:4]) if cod else None,
            "retirement_date": ret_date, "shutdown_date": shutdown,
            "restart_date": restart, "reactor_type": rtype, "is_smr": bool(is_smr),
        })
    return {"query": name, "matches": len(reactors), "reactors": reactors}


def _tool_get_fleet_summary(region: str) -> dict:
    reg  = _analyst_resolve_region(region)
    conn = _analyst_db()
    c    = conn.cursor()
    if reg in (REGIONS + ["Global"]):
        where  = "1=1" if reg == "Global" else "region = ?"
        params: list = [] if reg == "Global" else [reg]
    else:
        where  = "UPPER(country) LIKE UPPER(?)"
        params = [f"%{reg}%"]
    c.execute(
        f"SELECT status, COUNT(*), SUM(net_capacity_mw), "
        f"AVG(CAST(SUBSTR(COALESCE(commercial_operation_date, first_grid_date), 1, 4) AS INTEGER)) "
        f"FROM reactors WHERE {where} GROUP BY status",
        params,
    )
    rows = c.fetchall()
    conn.close()
    if not rows:
        return {"error": f"No fleet data for '{region}'"}
    by_status: dict = {}
    op_cap = op_count = 0
    for status, count, cap_mw, avg_cod in rows:
        by_status[status] = {
            "count": count,
            "capacity_gw": round((cap_mw or 0) / 1000, 2),
            "avg_commissioning_year": round(avg_cod) if avg_cod else None,
        }
        if status in ("Operating", "Restarted"):
            op_cap   += cap_mw or 0
            op_count += count
    return {"region": region,
            "operating_gw": round(op_cap / 1000, 2),
            "operating_count": op_count,
            "by_status": by_status}


def _tool_get_technology_breakdown(
    region: str, status: str = "Operating", group_by: str = "type"
) -> dict:
    reg  = _analyst_resolve_region(region)
    conn = _analyst_db()
    c    = conn.cursor()

    where_frag, params = _region_where(reg)

    stat_norm = status.strip().lower()
    if stat_norm == "all":
        stat_clause = ""
    elif stat_norm in ("operating", "restarted"):
        stat_clause = " AND status IN ('Operating','Restarted')"
    else:
        _stat_map = {
            "underconstruction": "UnderConstruction",
            "under construction": "UnderConstruction",
            "uc": "UnderConstruction",
            "planned": "Planned",
            "proposed": "Proposed",
            "shutdown": "PermanentShutdown",
        }
        db_status = _stat_map.get(stat_norm, status)
        stat_clause = f" AND status = '{db_status}'"

    grp_map = {
        "type":     "reactor_type",
        "supplier": "nsss_supplier",
        "model":    "reactor_model",
    }
    grp_col = grp_map.get(group_by.lower().strip(), "reactor_type")

    c.execute(
        f"SELECT {grp_col}, COUNT(*) as cnt, "
        f"ROUND(SUM(net_capacity_mw)/1000.0, 2) as gw "
        f"FROM reactors "
        f"WHERE {where_frag}{stat_clause} "
        f"  AND net_capacity_mw IS NOT NULL "
        f"GROUP BY {grp_col} "
        f"ORDER BY cnt DESC",
        params,
    )
    rows = c.fetchall()
    conn.close()
    if not rows:
        return {"error": f"No technology data for region='{reg}', status='{status}'"}

    total_cnt = sum(r[1] for r in rows)
    total_gw  = sum(r[2] for r in rows if r[2])
    breakdown = []
    for (key, cnt, gw) in rows:
        label = _supplier_label(key) if group_by == "supplier" else (key or "Unknown")
        breakdown.append({
            "label": label,
            "raw_code": key,
            "count": cnt,
            "capacity_gw": round(gw or 0, 2),
            "share_pct": round(cnt / total_cnt * 100, 1) if total_cnt else 0,
        })
    return {
        "region": reg, "status_filter": status, "group_by": group_by,
        "total_reactors": total_cnt, "total_gw": round(total_gw, 2),
        "breakdown": breakdown,
        "note": (
            "Supplier data available for Operating and UnderConstruction reactors only. "
            "Planned/Proposed reactors typically do not have supplier assignments yet."
            if group_by == "supplier" else ""
        ),
    }


def _tool_get_fleet_profile(region: str, status: str = "Operating") -> dict:
    reg  = _analyst_resolve_region(region)
    conn = _analyst_db()
    c    = conn.cursor()

    where_frag, params = _region_where(reg)
    stat_norm = status.strip().lower()
    if stat_norm in ("operating", "default", ""):
        stat_clause = " AND status IN ('Operating','Restarted')"
    elif stat_norm == "all":
        stat_clause = ""
    else:
        stat_clause = f" AND status = '{status}'"

    c.execute(
        f"SELECT "
        f"  net_capacity_mw, "
        f"  commercial_operation_date, "
        f"  eaf_pct, ucf_pct, "
        f"  is_smr, "
        f"  retirement_tier, "
        f"  retirement_date_used "
        f"FROM reactors "
        f"WHERE {where_frag}{stat_clause} "
        f"  AND net_capacity_mw IS NOT NULL",
        params,
    )
    rows = c.fetchall()
    conn.close()
    if not rows:
        return {"error": f"No fleet profile data for region='{reg}'"}

    current_year = 2025
    ages, eafs, ucfs = [], [], []
    smr_count = smr_gw = large_count = large_gw = 0
    age_buckets: dict[str, int] = {}
    tier_counts: dict[str, int] = {}

    for (cap_mw, cod, eaf, ucf, is_smr, tier, ret_date) in rows:
        if cod:
            try:
                commission_year = int(str(cod)[:4])
                age = current_year - commission_year
                ages.append(age)
                decade = f"{(commission_year // 10) * 10}s"
                age_buckets[decade] = age_buckets.get(decade, 0) + 1
            except Exception:
                pass

        if eaf is not None:
            eafs.append(eaf)
        if ucf is not None:
            ucfs.append(ucf)

        cap = cap_mw or 0
        if is_smr:
            smr_count += 1
            smr_gw    += cap / 1000
        else:
            large_count += 1
            large_gw    += cap / 1000

        t_label = f"Tier {tier}" if tier is not None else "Unknown"
        tier_counts[t_label] = tier_counts.get(t_label, 0) + 1

    def _median(lst):
        if not lst:
            return None
        s = sorted(lst)
        n = len(s)
        return round(s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2, 1)

    return {
        "region": reg,
        "status_filter": status,
        "total_reactors": len(rows),
        "age": {
            "avg_years": round(sum(ages) / len(ages), 1) if ages else None,
            "median_years": _median(ages),
            "min_years": min(ages) if ages else None,
            "max_years": max(ages) if ages else None,
            "by_decade_commissioned": dict(sorted(age_buckets.items())),
        },
        "capacity_factor": {
            "avg_eaf_pct": round(sum(eafs) / len(eafs), 1) if eafs else None,
            "avg_ucf_pct": round(sum(ucfs) / len(ucfs), 1) if ucfs else None,
            "reactors_with_eaf_data": len(eafs),
        },
        "smr_vs_large": {
            "smr_count": smr_count,
            "smr_gw": round(smr_gw, 2),
            "large_count": large_count,
            "large_gw": round(large_gw, 2),
        },
        "retirement_tiers": tier_counts,
        "note": (
            "Tier 1 = declared/near-term shutdown, Tier 2 = licence expiry within 10 yrs, "
            "Tier 3 = design life extension assumed."
        ),
    }


def _tool_get_pipeline_detail(
    region: str, status_filter: str = "all", smr_only: bool = False
) -> dict:
    reg  = _analyst_resolve_region(region)
    conn = _analyst_db()
    c    = conn.cursor()

    where_frag, params = _region_where(reg)

    stat_norm = status_filter.strip().lower()
    if stat_norm == "all":
        stat_clause = " AND status IN ('UnderConstruction','Planned','Proposed')"
    else:
        _stat_map = {
            "underconstruction": "UnderConstruction",
            "under construction": "UnderConstruction",
            "uc": "UnderConstruction",
            "planned": "Planned",
            "proposed": "Proposed",
        }
        db_status = _stat_map.get(stat_norm, status_filter)
        stat_clause = f" AND status = '{db_status}'"

    smr_clause = " AND is_smr = 1" if smr_only else ""

    c.execute(
        f"SELECT name, country, region, status, net_capacity_mw, "
        f"  expected_online_year, is_smr, nsss_supplier, "
        f"  pipeline_probability, construction_start_date, reactor_type, reactor_model "
        f"FROM reactors "
        f"WHERE {where_frag}{stat_clause}{smr_clause} "
        f"  AND net_capacity_mw IS NOT NULL "
        f"ORDER BY status, expected_online_year, country, name",
        params,
    )
    rows = c.fetchall()
    conn.close()
    if not rows:
        return {"error": f"No pipeline data for region='{reg}', filter='{status_filter}'"}

    reactors = []
    by_status: dict[str, dict] = {}
    for (name, country, rgn, status, cap_mw, online_yr, is_smr,
         supplier, prob, constr_start, rtype, rmodel) in rows:
        reactors.append({
            "name": name,
            "country": country,
            "status": status,
            "capacity_mw": cap_mw,
            "expected_online_year": online_yr,
            "is_smr": bool(is_smr),
            "reactor_type": rtype,
            "reactor_model": rmodel,
            "supplier": _supplier_label(supplier) if supplier else None,
            "pipeline_probability": prob,
            "construction_start": constr_start,
        })
        s = by_status.setdefault(status, {"count": 0, "capacity_gw": 0.0, "smr_count": 0})
        s["count"]       += 1
        s["capacity_gw"] += (cap_mw or 0) / 1000
        if is_smr:
            s["smr_count"] += 1

    for s in by_status.values():
        s["capacity_gw"] = round(s["capacity_gw"], 2)

    return {
        "region": reg,
        "status_filter": status_filter,
        "smr_only": smr_only,
        "total_reactors": len(reactors),
        "total_gw": round(sum(r["capacity_mw"] or 0 for r in reactors) / 1000, 2),
        "by_status_summary": by_status,
        "reactors": reactors,
        "note": "Supplier data only available for Under Construction reactors.",
    }


def _tool_get_retirement_schedule(
    region: str, start_year: int = 2025, end_year: int = 2050
) -> dict:
    reg  = _analyst_resolve_region(region)
    conn = _analyst_db()
    c    = conn.cursor()

    where_frag, params = _region_where(reg)

    c.execute(
        f"SELECT name, country, net_capacity_mw, "
        f"  retirement_date_used, retirement_tier, "
        f"  commercial_operation_date, reactor_type "
        f"FROM reactors "
        f"WHERE {where_frag} "
        f"  AND status IN ('Operating','Restarted') "
        f"  AND net_capacity_mw IS NOT NULL "
        f"  AND retirement_date_used IS NOT NULL "
        f"ORDER BY retirement_date_used",
        params,
    )
    rows = c.fetchall()
    conn.close()
    if not rows:
        return {"error": f"No retirement schedule data for region='{reg}'"}

    by_year: dict[int, dict] = {}
    tier_gw: dict[str, float] = {}
    outside_window: list = []

    for (name, country, cap_mw, ret_date, tier, cod, rtype) in rows:
        try:
            ret_year = int(str(ret_date)[:4])
        except Exception:
            continue

        tier_label = f"Tier {tier}" if tier is not None else "Unknown"
        cap_gw = (cap_mw or 0) / 1000
        tier_gw[tier_label] = tier_gw.get(tier_label, 0.0) + cap_gw

        if ret_year < start_year or ret_year > end_year:
            outside_window.append({"name": name, "country": country,
                                    "retirement_year": ret_year, "capacity_gw": round(cap_gw, 3)})
            continue

        bucket = by_year.setdefault(ret_year, {"capacity_gw": 0.0, "reactors": []})
        bucket["capacity_gw"]  += cap_gw
        bucket["reactors"].append({
            "name": name, "country": country,
            "capacity_gw": round(cap_gw, 3),
            "tier": tier_label,
            "reactor_type": rtype,
            "online_year": int(str(cod)[:4]) if cod else None,
        })

    for yr_data in by_year.values():
        yr_data["capacity_gw"] = round(yr_data["capacity_gw"], 2)

    total_gw = sum(d["capacity_gw"] for d in by_year.values())
    peak_year = max(by_year, key=lambda y: by_year[y]["capacity_gw"]) if by_year else None

    return {
        "region": reg,
        "window": f"{start_year}–{end_year}",
        "total_retiring_gw": round(total_gw, 2),
        "total_retiring_reactors": sum(len(d["reactors"]) for d in by_year.values()),
        "peak_retirement_year": peak_year,
        "peak_gw": round(by_year[peak_year]["capacity_gw"], 2) if peak_year else None,
        "by_year": {str(yr): by_year[yr] for yr in sorted(by_year)},
        "tier_gw_breakdown": {k: round(v, 2) for k, v in sorted(tier_gw.items())},
        "reactors_outside_window": len(outside_window),
        "note": (
            "Tier 1 = declared/near-term shutdown, Tier 2 = licence expiry-based, "
            "Tier 3 = design-life extension assumed. Dates reflect base-case model assumptions."
        ),
    }


# ── Tool dispatcher ───────────────────────────────────────────────────────────

def _execute_analyst_tool(tool_name: str, tool_input: dict) -> str:
    try:
        if tool_name == "get_capacity":
            result = _tool_get_capacity(
                tool_input["scenario"], tool_input["region"], int(tool_input["year"]))
        elif tool_name == "get_timeseries":
            result = _tool_get_timeseries(
                tool_input["scenario"], tool_input["region"],
                int(tool_input["start_year"]), int(tool_input["end_year"]))
        elif tool_name == "compare_scenarios":
            result = _tool_compare_scenarios(
                tool_input["region"], int(tool_input["year"]))
        elif tool_name == "get_benchmark":
            result = _tool_get_benchmark(
                int(tool_input["year"]), tool_input.get("region", "Global"))
        elif tool_name == "get_regional_breakdown":
            result = _tool_get_regional_breakdown(
                tool_input["scenario"], int(tool_input["year"]))
        elif tool_name == "get_retirements":
            result = _tool_get_retirements(
                tool_input["scenario"], tool_input["region"],
                int(tool_input["start_year"]), int(tool_input["end_year"]))
        elif tool_name == "get_additions":
            result = _tool_get_additions(
                tool_input["scenario"], tool_input["region"],
                int(tool_input["start_year"]), int(tool_input["end_year"]))
        elif tool_name == "find_crossover":
            result = _tool_find_crossover(
                tool_input["scenario"], tool_input["region_a"], tool_input["region_b"])
        elif tool_name == "get_reactor_info":
            result = _tool_get_reactor_info(tool_input["name"])
        elif tool_name == "get_fleet_summary":
            result = _tool_get_fleet_summary(tool_input["region"])
        elif tool_name == "get_technology_breakdown":
            result = _tool_get_technology_breakdown(
                tool_input["region"],
                tool_input.get("status", "Operating"),
                tool_input.get("group_by", "type"),
            )
        elif tool_name == "get_fleet_profile":
            result = _tool_get_fleet_profile(
                tool_input["region"],
                tool_input.get("status", "Operating"),
            )
        elif tool_name == "get_pipeline_detail":
            result = _tool_get_pipeline_detail(
                tool_input["region"],
                tool_input.get("status_filter", "all"),
                bool(tool_input.get("smr_only", False)),
            )
        elif tool_name == "get_retirement_schedule":
            result = _tool_get_retirement_schedule(
                tool_input["region"],
                int(tool_input.get("start_year", 2025)),
                int(tool_input.get("end_year", 2050)),
            )
        else:
            result = {"error": f"Unknown tool: {tool_name}"}
    except Exception as _e:
        result = {"error": f"Tool error: {_e}"}
    return json.dumps(result)


# ── Analyst agent call ────────────────────────────────────────────────────────

def call_claude_analyst(
    user_message: str,
    api_key: str,
    chat_history: list,
) -> dict:
    """
    Run the read-only analytical agent using tool-calling.
    Returns {message: str, actions: [], _analyst: True}.
    """
    import anthropic as _ant

    try:
        import streamlit as _st
        _active_sc = _st.session_state.get("preset_selector", "base")
    except Exception:
        _active_sc = "base"

    system = _ANALYST_SYSTEM + f"\nCurrently active scenario: **{_active_sc}**\n"
    client = _ant.Anthropic(api_key=api_key)

    messages: list = []
    for h in chat_history[-8:]:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": user_message})

    _models_to_try = [
        "claude-sonnet-4-5-20250929",
        "claude-sonnet-4-20250514",
        "claude-3-5-sonnet-20241022",
        "claude-haiku-4-5-20251001",
    ]

    for _model in _models_to_try:
        try:
            loop_msgs = list(messages)
            resp = None
            for _iteration in range(6):  # safety cap
                resp = client.messages.create(
                    model      = _model,
                    max_tokens = 2048,
                    system     = system,
                    tools      = _ANALYST_TOOLS,
                    messages   = loop_msgs,
                )

                if resp.stop_reason == "end_turn":
                    text = next(
                        (b.text for b in resp.content if hasattr(b, "text")),
                        "I couldn't generate a response. Please try rephrasing.",
                    )
                    return {"message": text, "actions": [], "_analyst": True}

                if resp.stop_reason == "tool_use":
                    tool_results = []
                    for block in resp.content:
                        if block.type == "tool_use":
                            tool_results.append({
                                "type":        "tool_result",
                                "tool_use_id": block.id,
                                "content":     _execute_analyst_tool(block.name, block.input),
                            })
                    loop_msgs.append({"role": "assistant", "content": resp.content})
                    loop_msgs.append({"role": "user",      "content": tool_results})
                    continue

                break  # unexpected stop reason

            # Max iterations or unexpected stop — return whatever text is available
            if resp is not None:
                text = next(
                    (b.text for b in resp.content if hasattr(b, "text")),
                    "Reached tool call limit. Please try a simpler question.",
                )
                return {"message": text, "actions": [], "_analyst": True}

        except Exception as _e:
            if "not_found" not in str(_e).lower():
                return {"message": f"Error: {_e}", "actions": [], "_analyst": True}
            continue  # try next model

    return {
        "message": "No accessible Claude model found. Please check your API key.",
        "actions": [], "_analyst": True,
    }
