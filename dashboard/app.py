"""
app.py — Streamlit entry point for the Nuclear Capacity Dashboard.

Run with:
    cd nuclear-dashboard
    streamlit run dashboard/app.py
"""
import json
import sys
import uuid
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from config import REGIONS, BASELINE_YEAR, PROJECTION_END_YEAR
from dashboard.levers import render_sidebar_panel

from dashboard.cache import (
    load_all_projections,
    load_historical_all,
    load_benchmarks,
    load_reactors,
    load_vintage,
    load_full_reactor_download,
    load_projection_state,
    to_excel_bytes,
)
from dashboard.helpers import (
    build_wi_overrides_dict,
    sum_projections,
    sum_historical,
    gw_from_df,
    build_scenario_export,
)
from dashboard.tab_renderers import (
    RenderContext,
    render_tab1_global,
    render_tab2_breakdown,
    render_tab3_retirements,
    render_tab4_additions,
    render_tab5_country,
    render_tab6_map,
    render_tab7_lab,
)

# ── Page config ────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Nuclear Capacity Dashboard",
    page_icon="⚛️",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ── Password gate ─────────────────────────────────────────────────────────
def _check_password() -> bool:
    """Show a password prompt and return True only when correct."""
    if st.session_state.get("_authenticated"):
        return True

    st.markdown("## ⚛️ Nuclear Capacity Dashboard")
    pwd = st.text_input("Password", type="password", key="_pwd_input",
                        placeholder="Enter password to continue")
    if st.button("Enter", type="primary"):
        try:
            expected = st.secrets.get("APP_PASSWORD", "")
        except Exception:
            expected = ""
        if pwd == expected:
            st.session_state["_authenticated"] = True
            st.rerun()
        else:
            st.error("Incorrect password — please try again.")
    st.markdown("---")
    st.markdown("""
An interactive scenario modeling tool for global nuclear power capacity from 2024 to 2050,
built on unit-level reactor data from IAEA PRIS. Charts are scenario outputs, not forecasts —
they reflect the assumptions you configure. *For research and educational use only.*

**Four default scenarios are included for reference:**
- **Decline** — no new life extensions; US/Japan fleet retires on current licenses; conservative pipeline
- **Conservative** — modest extensions; only under-construction reactors included in pipeline
- **Base** *(default)* — current country-specific extension policies; medium pipeline realization
- **Optimistic** — broad life extensions; high pipeline realization including proposed projects

**Seven tabs offer a range of data breakdowns and customization options:**
- 📈 **Global Projection** — total capacity 2005–2050 with benchmark overlays (IAEA, IEA)
- 📊 **Capacity Breakdown** — stacked by region or technology
- 📉 **Retirements** — which reactors retire and when
- ➕ **New Additions** — pipeline funnel and construction timelines
- 🏳️ **Country Snapshot** — per-country capacity at a chosen year
- 🗺️ **World Map** — geographic view of the operating fleet
- 🔬 **Scenario Lab** — build a custom scenario and see the delta vs the preset

**Use the Scenario Lab to model detailed assumptions and what-ifs:**
Adjust levers (life extension policy, pipeline rates, SMR deployment, post-2040 growth) and
click **▶ Apply Scenario** to recompute. Use the **What-If** panel to override individual
reactors or add synthetic new-build batches. A built-in Claude assistant can generate overrides
from plain-language descriptions (e.g. *"retire all Canadian reactors in 2033"*).

**Other notes**
- Most charts have a **⬇ Data** download button for the underlying numbers
- The sidebar **Download Scenario + Settings** exports a 4-sheet Excel with your full lever configuration
- The geography filter (sidebar) restricts all charts and KPIs to selected regions
- Post-2040 projections switch to a top-down growth-rate model; treat those years as directional only
""")
    st.markdown("**by Jack Kochansky with Claude Code**")
    return False

if not _check_password():
    st.stop()


# ── Custom CSS: widen sidebar to ~35% ─────────────────────────────────────
st.markdown("""
<style>
    [data-testid="stSidebar"] {
        min-width: 380px;
        max-width: 440px;
    }
    [data-testid="stSidebar"] > div:first-child {
        padding-top: 1rem;
    }
    .stTabs [data-baseweb="tab-list"] { gap: 6px; }
    .stTabs [data-baseweb="tab"] { padding: 6px 14px; font-size: 13px; }
    footer { visibility: hidden; }
</style>
""", unsafe_allow_html=True)


# ── Session initialisation ────────────────────────────────────────────────
# Each browser session gets its own unique custom-scenario ID so concurrent
# users never overwrite each other's DB rows.
if "_custom_scenario_id" not in st.session_state:
    from model.api import cleanup_stale_custom_scenarios
    st.session_state["_custom_scenario_id"] = f"custom_{uuid.uuid4().hex[:8]}"
    cleanup_stale_custom_scenarios(max_age_hours=2)

_SESSION_CUSTOM_ID: str = st.session_state["_custom_scenario_id"]


# ── Sidebar lever panel ────────────────────────────────────────────────────
state, _ = render_sidebar_panel()

# ── Load cached data ───────────────────────────────────────────────────────
all_projections  = load_all_projections()
historical_all   = load_historical_all()
benchmarks       = load_benchmarks()
reactors         = load_reactors()
vintage          = load_vintage()

sc_id = state.scenario_id


# ── Custom projection from session state ──────────────────────────────────
custom_projection: dict | None = st.session_state.get("_custom_projection")

# Clear custom if the user switched presets without clicking Apply in the Lab
if st.session_state.get("_custom_for_scenario") != sc_id:
    st.session_state.pop("_custom_projection", None)
    custom_projection = None

if custom_projection is not None:
    _active_db_id = _SESSION_CUSTOM_ID
else:
    _active_db_id = sc_id

active_key = "custom" if custom_projection is not None else sc_id


# ── Geography filter ───────────────────────────────────────────────────────
sel_regions = [r for r in REGIONS if r in state.selected_regions]
if not sel_regions:
    sel_regions = list(REGIONS)
geo_filtered      = len(sel_regions) < len(REGIONS)
filtered_reactors = reactors[reactors["region"].isin(sel_regions)] if geo_filtered else reactors


# ── What-if state ──────────────────────────────────────────────────────────
_wi_proj_all = st.session_state.get("wi_proj_all")
_wi_active   = (
    _wi_proj_all is not None
    and (bool(st.session_state.get("wi_overrides")) or bool(st.session_state.get("wi_synthetic")))
)

# Build override JSON once; used as cache key and passed into RenderContext.
_wi_ov_json = (
    json.dumps(build_wi_overrides_dict(), sort_keys=True) if _wi_active else ""
)

# Build a single ProjectionState for this render cycle when what-if is active.
# All tabs share this instance so DB reads happen at most once per cycle.
_proj_state = load_projection_state(_active_db_id, _wi_ov_json) if _wi_active else None


# ── Projection source selection ────────────────────────────────────────────
_proj_source_dict = (
    _wi_proj_all          if _wi_active              else
    custom_projection     if custom_projection is not None else
    all_projections[sc_id]
)

proj_global_display = (
    sum_projections(_proj_source_dict, sel_regions) if geo_filtered
    else _proj_source_dict.get("Global", all_projections[sc_id]["Global"])
)
hist_global_display = (
    sum_historical(historical_all, sel_regions) if geo_filtered
    else historical_all["Global"]
)

proj_by_region = {r: _proj_source_dict[r] for r in sel_regions if r in _proj_source_dict}
hist_by_region = {r: historical_all[r]    for r in sel_regions if r in historical_all}

# Pre-customisation baseline for the Scenario Lab diff chart.
# Always the raw preset projection so the diff shows the delta from the preset,
# not from a previous custom run.
_lab_base_global = (
    sum_projections(all_projections[sc_id], sel_regions) if geo_filtered
    else all_projections[sc_id].get("Global", pd.DataFrame())
)

# Comparison projections (geography-aware)
if geo_filtered:
    compare_projs = {
        cid: sum_projections(all_projections[cid], sel_regions)
        for cid in state.compare_scenarios
        if cid in all_projections
    }
else:
    compare_projs = {
        cid: all_projections[cid]["Global"]
        for cid in state.compare_scenarios
        if cid in all_projections
    }
compare_projs[active_key] = proj_global_display


# ── Sidebar: scenario export button ───────────────────────────────────────
st.sidebar.markdown("---")
st.sidebar.markdown("### 💾 Export Scenario")
_export_filename = (
    f"nuclear_scenario_{active_key}_{pd.Timestamp.today().strftime('%Y-%m-%d')}.xlsx"
)
st.sidebar.download_button(
    label="⬇ Download Scenario + Settings",
    data=build_scenario_export(state, active_key, proj_global_display, proj_by_region),
    file_name=_export_filename,
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    use_container_width=True,
    help=(
        "Download the current scenario as a 4-sheet Excel file: "
        "lever settings, global projection, regional breakdown, and model description."
    ),
)
if custom_projection is not None:
    st.sidebar.caption("📌 Exporting custom lever settings + projections.")
else:
    st.sidebar.caption(f"📌 Exporting **{active_key.title()}** preset + projections.")


# ── Header ─────────────────────────────────────────────────────────────────
st.markdown("## ⚛️ Global Nuclear Capacity Dashboard")
col_h1, col_h2, col_h3, col_h4 = st.columns(4)

kpi_suffix = " ★" if geo_filtered else ""
kpi_help   = " (selected regions only)" if geo_filtered else ""

baseline_row = proj_global_display[proj_global_display["year"] == BASELINE_YEAR]
row_2030     = proj_global_display[proj_global_display["year"] == 2030]
row_2040     = proj_global_display[proj_global_display["year"] == 2040]
end_row      = proj_global_display[proj_global_display["year"] == PROJECTION_END_YEAR]

with col_h1:
    st.metric(f"2024 Baseline{kpi_suffix}", f"{gw_from_df(baseline_row):.0f} GW",
              help=f"Current operating capacity (PRIS 2024){kpi_help}")
with col_h2:
    val30 = gw_from_df(row_2030)
    st.metric(f"2030 Projection{kpi_suffix}", f"{val30:.0f} GW",
              delta=f"{val30 - gw_from_df(baseline_row):+.0f} GW vs 2024",
              help=kpi_help.strip() or None)
with col_h3:
    val40 = gw_from_df(row_2040)
    st.metric(f"2040 (Transition){kpi_suffix}", f"{val40:.0f} GW",
              delta=f"{val40 - gw_from_df(baseline_row):+.0f} GW vs 2024",
              help=kpi_help.strip() or None)
with col_h4:
    val50 = gw_from_df(end_row)
    st.metric(f"2050 Projection{kpi_suffix}", f"{val50:.0f} GW",
              delta=f"{val50 - gw_from_df(baseline_row):+.0f} GW vs 2024",
              help=kpi_help.strip() or None)

_dl_col1, _dl_col2 = st.columns([6, 1])
with _dl_col2:
    _reactor_dl_df = load_full_reactor_download()
    st.download_button(
        label="⬇ Full Reactor List",
        data=to_excel_bytes(_reactor_dl_df, "All Reactors"),
        file_name="nuclear_reactor_list.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        help=f"Download all {len(_reactor_dl_df):,} reactors (operating, pipeline, long-term shutdown)",
        use_container_width=True,
    )

st.markdown("---")

# Geography filter banner
if geo_filtered:
    excluded = [r for r in REGIONS if r not in sel_regions]
    st.info(
        f"🌍 **Geography filter active** — showing {len(sel_regions)} of {len(REGIONS)} regions. "
        f"All charts, KPIs ★, and historical data reflect selected regions only. "
        f"Excluded: {', '.join(excluded)}."
    )

# What-if active banner
if _wi_active:
    _n_overrides = len(st.session_state.get("wi_overrides", []))
    _n_synthetic = len(st.session_state.get("wi_synthetic", []))
    _parts = []
    if _n_overrides:
        _parts.append(f"{_n_overrides} reactor override{'s' if _n_overrides != 1 else ''}")
    if _n_synthetic:
        _parts.append(f"{_n_synthetic} synthetic build batch{'es' if _n_synthetic != 1 else ''}")
    st.warning(
        f"🔬 **What-If mode active** — all charts reflect your scenario "
        f"({', '.join(_parts)}). Go to the **Scenario Lab** tab to edit or clear.",
        icon=None,
    )


# ── Build RenderContext ────────────────────────────────────────────────────
ctx = RenderContext(
    # Scenario
    state=state,
    sc_id=sc_id,
    active_key=active_key,
    custom_projection=custom_projection,
    session_custom_id=_SESSION_CUSTOM_ID,
    active_db_id=_active_db_id,
    # Projections
    proj_global_display=proj_global_display,
    proj_by_region=proj_by_region,
    hist_global_display=hist_global_display,
    hist_by_region=hist_by_region,
    compare_projs=compare_projs,
    proj_source_dict=_proj_source_dict,
    lab_base_global=_lab_base_global,
    benchmarks=benchmarks,
    # Geography
    sel_regions=sel_regions,
    geo_filtered=geo_filtered,
    filtered_reactors=filtered_reactors,
    # What-if
    wi_active=_wi_active,
    wi_ov_json=_wi_ov_json,
    proj_state=_proj_state,
)


# ── Chart tabs ─────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5, tab6, tab_lab = st.tabs([
    "📈 Global Projection",
    "📊 Capacity Breakdown",
    "📉 Retirements",
    "➕ New Additions",
    "🏳️ Country Snapshot",
    "🗺️ World Map",
    "🔬 Scenario Lab",
])

with tab1:
    render_tab1_global(ctx)

with tab2:
    render_tab2_breakdown(ctx)

with tab3:
    render_tab3_retirements(ctx)

with tab4:
    render_tab4_additions(ctx)

with tab5:
    render_tab5_country(ctx)

with tab6:
    render_tab6_map(ctx)

with tab_lab:
    render_tab7_lab(ctx)


# ── Footer & Disclaimer ────────────────────────────────────────────────────
st.markdown("---")

with st.expander("ℹ️ About this dashboard & methodology", expanded=False):
    st.markdown("""
**What this is**

An interactive scenario modelling tool for global nuclear power capacity from 2024 to 2050,
built on unit-level reactor data from IAEA PRIS. Charts and projections are
**scenario outputs, not predictions** — they reflect the assumptions you set via the levers.
For research and educational use only. A full methodology document is available separately.

---

**Model structure**

The model operates in two phases:
- **2024–2040 (bottom-up):** Every reactor is tracked individually. Retirement dates are derived
  from country-specific licensing rules (Tier 2) or declared dates (Tier 1). Pipeline reactors
  enter service at their expected online year, subject to the realization rate and any
  construction delay adder you apply.
- **2040–2050 (top-down):** Unit-level retirements continue, but new additions are driven by the
  global GW/yr rate calibrated to the selected benchmark, distributed across regions using
  IAEA/IEA regional proportions. **2024 baseline:** last full IAEA PRIS vintage; historical data
  from 2005 gives two decades of context.

---

**Scenario assumptions — what each scenario actually does**

| | 🔴 Decline | 🟠 Conservative | 🔵 Base | 🟢 Optimistic |
|---|---|---|---|---|
| **Life extension** | None (AcceleratedRetirement) | ~50% eligible units (CurrentPolicy) | ~50% eligible units (CurrentPolicy) | 100% to max life (ExtendedOperations) |
| **US exception** | Same — no extra extensions | ExtendedOperations¹ | ExtendedOperations¹ | ExtendedOperations |
| **Pipeline: UC** | 100% | 100% | 100% | 100% |
| **Pipeline: Planned** | 0% | 100% | 100% | 100% |
| **Pipeline: Proposed** | 0% | 0% | 0% | 100% |
| **Construction delay** | +3 years | +2 years | 0 years | 0 years |
| **Post-2040 new build** | ~10 GW/yr | ~35 GW/yr | ~30 GW/yr | ~57 GW/yr |
| **2050 capacity** | ~250 GW | ~561 GW | ~647 GW | ~992 GW |
| **Calibration benchmark** | IEA Low Nuclear | IAEA Low (RDS-1) | IEA STEPS (WEO 2024) | IAEA High (RDS-1) |

¹ *All US plants treated as ExtendedOperations in Base/Conservative: NRC licence renewal data shows
virtually all operational US plants already hold 60-year licences, with subsequent licence renewal
(SLR) applications in progress for 80-year operation. Applying a 50% CurrentPolicy haircut would
understate approvals already granted.*

---

**Pipeline realization — what "High / Medium / Low" means**

- **High** (Optimistic): Under Construction 100% + Planned 100% + Proposed 100%
- **Medium** (Base, Conservative): Under Construction 100% + Planned 100% + Proposed 0%
- **Low** (Decline): Under Construction 100% only; Planned and Proposed excluded

Pipeline realization is **binary per reactor** (fully in or fully out) in preset scenarios,
not a fractional probability. Custom scenarios in the Scenario Lab can use intermediate values.

---

**Country-level retirement rules (selected)**

| Country | Baseline licence | Max life (current law) | Key driver |
|---------|-----------------|----------------------|------------|
| United States | 40 yr | 80 yr (NRC renewals in 2×20yr steps) | NRC licence renewal; nearly all plants at 60yr |
| France | 40 yr | ~60 yr (ASN 10-yr reviews) | Periodic safety reviews; 60yr policy under development |
| China | 40 yr | ~50 yr (extensions not yet routine) | Young fleet; extensions not standardised |
| Russia | 30 yr | 60 yr (15yr extension increments) | Rosatom standard; 60yr operating target |
| Japan | 40 yr | 80 yr (2023 law) | NRA post-Fukushima rules; 2023 law allows beyond 60yr |
| South Korea | 40 yr | 60 yr | NSSC case-by-case |
| United Kingdom | ~35 yr | ~55 yr | ONR lifecycle licence; no fixed maximum |
| Germany | — | Phase-out complete Apr 2023 | No modelled extensions |
| Taiwan | 40 yr | 40 yr (no extensions) | Government phase-out policy |

---

**Benchmark alignment**

| Benchmark | 2030 | 2040 | 2050 | Matched scenario |
|-----------|------|------|------|-----------------|
| IEA Low Nuclear Case | — | — | ~250 GW | Decline |
| IAEA Low Case (RDS-1 2025) | 425 GW | 519 GW | ~561 GW | Conservative |
| IEA STEPS (WEO 2024) | 513 GW | 586 GW | ~647 GW | Base |
| IEA APS (WEO 2024) | 551 GW | 748 GW | ~874 GW | (reference only) |
| IAEA High Case (RDS-1 2025) | 445 GW | 710 GW | ~992 GW | Optimistic |

Only the **2050 terminal value** is explicitly calibrated per scenario. Intermediate years
(2030, 2040) are determined by the bottom-up fleet simulation and may differ from the
benchmark's own intermediate milestones.

---

**Key limitations**

- Retirement dates are rule-based, not market-driven — economic early retirements (e.g. German
  Energiewende) may not be captured for future scenarios unless added as what-if overrides.
- Post-2040 projections are top-down and directional only; treat with appropriate uncertainty.
- Pipeline data reflects IAEA PRIS/WNA/WNN as of end-2024; 2025+ announcements not automatically included.
- SMRs are included where in PRIS/WNA pipeline; future commercial SMR scale-up is folded into
  the post-2040 growth rate, not modelled at the unit level.

---

**Data sources**

| Source | Coverage |
|--------|----------|
| [IAEA PRIS](https://pris.iaea.org/) | Operating & pipeline reactors worldwide; commissioning/retirement dates |
| [IAEA RDS-1 2025](https://www.iaea.org/publications/15510/nuclear-power-reactors-in-the-world) | Benchmark scenarios Low/High at 2030/2040/2050 |
| [IEA World Energy Outlook 2024](https://www.iea.org/reports/world-energy-outlook-2024) | STEPS/APS/Low Nuclear benchmarks |
| [WNA Reactor Database](https://www.world-nuclear.org/) | Pipeline supplemental data |
| National regulators (NRC, ASN, ONR, etc.) | Country-specific extension rules |

**Disclaimer:** This tool is for informational and research purposes only. Scenario outputs
should not be used as the basis for investment, policy, or engineering decisions without
independent verification.
    """)

st.caption(
    f"Data: {vintage}  ·  "
    "Model: bottom-up unit-level (2024–2040) → top-down growth bridge (2040–2050)  ·  "
    "For research & educational use only — scenario outputs, not forecasts"
)

st.markdown("**by Jack Kochansky with Claude Code**")
