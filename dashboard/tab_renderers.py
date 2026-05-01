"""
tab_renderers.py — One render function per dashboard tab.

Each function is called inside a ``with tab_X:`` block in app.py.
All shared render-cycle variables are bundled in RenderContext so the
function signatures stay stable as the dashboard grows.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
import streamlit as st

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import BASELINE_YEAR, PROJECTION_END_YEAR, REGIONS
from model.api import (
    run_what_if_all_regions,
    write_and_run_custom_scenario,
    get_what_if_country_capacity,
)
from dashboard.cache import (
    to_excel_bytes,
    load_tech_projection,
    load_country_capacity,
    load_full_reactor_download,
)
from dashboard.helpers import (
    build_wi_overrides_dict,
    levers_match_preset,
    build_scenario_bullets,
)
from dashboard.charts import (
    chart1_global_projection,
    chart4_regional,
    chart4_technology,
    chart5_retirements,
    chart6_additions,
    chart7_country_bar,
    chart_map,
    chart_what_if_diff,
)
from dashboard.levers import render_lab_panel, PRESET_DEFAULTS

if TYPE_CHECKING:
    from model.state import ProjectionState
    from dashboard.levers import ScenarioState


# ── Shared render context ─────────────────────────────────────────────────────

@dataclass
class RenderContext:
    """All render-cycle variables shared across tab renderer functions.

    Built once per Streamlit render cycle in app.py and passed unchanged
    to each tab's render function.
    """
    # Scenario
    state:             "ScenarioState"
    sc_id:             str
    active_key:        str
    custom_projection: dict | None
    session_custom_id: str
    active_db_id:      str

    # Projections
    proj_global_display: pd.DataFrame
    proj_by_region:      dict
    hist_global_display: pd.DataFrame
    hist_by_region:      dict
    compare_projs:       dict
    proj_source_dict:    dict   # wi_proj_all when wi_active, else preset
    lab_base_global:     pd.DataFrame
    benchmarks:          pd.DataFrame

    # Geography
    sel_regions:       list
    geo_filtered:      bool
    filtered_reactors: pd.DataFrame

    # What-if
    wi_active:  bool
    wi_ov_json: str              # JSON string; "" when not active
    proj_state: "ProjectionState | None"


# ── Tab 1: Global Projection ──────────────────────────────────────────────────

def render_tab1_global(ctx: RenderContext) -> None:
    if ctx.geo_filtered:
        show_benchmarks = {k: False for k in
                           ["IAEA Low", "IAEA High", "IEA STEPS", "IEA APS", "IEA Low Nuclear"]}
    else:
        show_benchmarks = {
            "IAEA Low":        ctx.state.show_iaea_low,
            "IAEA High":       ctx.state.show_iaea_high,
            "IEA STEPS":       ctx.state.show_iea_steps,
            "IEA APS":         ctx.state.show_iea_aps,
            "IEA Low Nuclear": ctx.state.show_iea_low_nuclear,
        }
    if ctx.custom_projection is not None:
        st.info("Custom lever settings applied. Preset lines available via comparison selector.")
    if ctx.geo_filtered:
        st.caption(
            f"📊 Showing sum of {len(ctx.sel_regions)} selected regions — benchmark lines hidden "
            "(global comparisons not applicable to regional subsets)."
        )

    _base_for_diff = None
    if ctx.wi_active:
        _base_for_diff = ctx.lab_base_global

    fig1 = chart1_global_projection(
        projections=ctx.compare_projs,
        historical=ctx.hist_global_display if ctx.state.show_historical else pd.DataFrame(),
        benchmarks=ctx.benchmarks,
        active_scenario=ctx.active_key,
        compare_scenarios=ctx.state.compare_scenarios,
        show_historical=ctx.state.show_historical,
        show_benchmarks=show_benchmarks,
        show_transition_marker=ctx.state.show_transition_marker,
        base_df=_base_for_diff,
    )
    _t1c1, _t1c2 = st.columns([8, 1])
    with _t1c1:
        st.plotly_chart(fig1, use_container_width=True)
        st.caption(
            "**2040 transition:** projections switch from bottom-up (individual reactor data — "
            "retirements + announced pipeline only) to top-down (global new-build rate lever). "
            "**Benchmark gap:** IEA/IAEA reference scenarios assume 100–200+ GW of capacity not yet "
            "announced or under licence — this model includes only reactors with a known construction "
            "or licence decision. The gap is by design and expected to widen through 2040. "
            "IEA STEPS/APS 2030 markers (~513/551 GW) serve as near-term cross-checks."
        )
        if ctx.active_key in ("decline", "custom") and (
            ctx.active_key == "decline"
            or ctx.state.extension_policy_global == "AcceleratedRetirement"
        ):
            st.caption(
                "ℹ️ **No New Extensions policy:** Existing approved licenses are honored in full; "
                "zero new extension rounds are granted beyond what regulators have already approved. "
                "US reactors (60-yr licenses) retire through 2029–2045; Japanese reactors follow "
                "their current approved terms. The gradual capacity decline reflects this staggered "
                "retirement schedule — not an immediate phase-out."
            )
    with _t1c2:
        _t1_dl = ctx.proj_global_display[
            ["year", "capacity_operating_gw", "retirements_this_year_gw", "additions_this_year_gw"]
        ].copy()
        _t1_dl.columns = ["Year", "Capacity (GW)", "Retirements (GW)", "Additions (GW)"]
        _t1_dl = _t1_dl.round(2)
        st.download_button(
            "⬇ Data",
            data=to_excel_bytes(_t1_dl, "Global Projection"),
            file_name="global_projection.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )

    with st.expander("Show projection data table"):
        _tbl_src = ctx.proj_global_display if not ctx.proj_global_display.empty else pd.DataFrame()
        display_df = _tbl_src[
            ["year", "capacity_operating_gw", "retirements_this_year_gw",
             "additions_this_year_gw", "is_bottom_up"]
        ].copy()
        display_df.columns = ["Year", "Capacity (GW)", "Retirements (GW)", "Additions (GW)", "Bottom-Up"]
        display_df["Capacity (GW)"]    = display_df["Capacity (GW)"].round(1)
        display_df["Retirements (GW)"] = display_df["Retirements (GW)"].round(2)
        display_df["Additions (GW)"]   = display_df["Additions (GW)"].round(2)
        display_df["Bottom-Up"]        = display_df["Bottom-Up"].map({1: "Yes", 0: "No"})
        st.dataframe(display_df, use_container_width=True, hide_index=True)


# ── Tab 2: Capacity Breakdown ─────────────────────────────────────────────────

def render_tab2_breakdown(ctx: RenderContext) -> None:
    breakdown_by = st.radio(
        "View by",
        options=["regional", "technology"],
        format_func=lambda x: (
            "Geography (by region)" if x == "regional"
            else "Technology (PWR / BWR / PHWR / SMR / Other)"
        ),
        horizontal=True,
        key="breakdown_split",
    )

    _t4c1, _t4c2 = st.columns([8, 1])

    if breakdown_by == "regional":
        fig4 = chart4_regional(
            projections_by_region=ctx.proj_by_region,
            historical_by_region=ctx.hist_by_region,
            regions=ctx.sel_regions,
            scenario_id=ctx.active_key,
            show_historical=ctx.state.show_historical,
        )
        with _t4c1:
            st.plotly_chart(fig4, use_container_width=True)
            st.caption(
                "Historical regional totals (shaded area, pre-2025) are derived from 5-year IAEA PRIS "
                "country snapshots with linear interpolation between survey years. The Global total "
                "(Tab 1) uses annual IAEA statistics and may differ from the regional sum in years "
                "affected by sudden capacity changes (e.g., Japan post-Fukushima 2011–2014) or "
                "countries not reported individually by IAEA (e.g., Taiwan). "
                "Post-2025 projections are scenario-specific."
            )
        with _t4c2:
            _t4_rows = []
            for _r in ctx.sel_regions:
                _df_r = ctx.proj_source_dict.get(_r, pd.DataFrame())
                if _df_r.empty:
                    continue
                for _, _row in _df_r.iterrows():
                    _t4_rows.append({
                        "Region":           _r,
                        "Year":             int(_row["year"]),
                        "Capacity (GW)":    round(float(_row["capacity_operating_gw"]), 2),
                        "Retirements (GW)": round(float(_row["retirements_this_year_gw"]), 2),
                        "Additions (GW)":   round(float(_row["additions_this_year_gw"]), 2),
                    })
            _t4_dl = pd.DataFrame(_t4_rows)
            st.download_button(
                "⬇ Data",
                data=to_excel_bytes(_t4_dl, "Regional Breakdown"),
                file_name="regional_breakdown.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )

        with st.expander("Show regional capacity table (2024 / 2030 / 2040 / 2050)"):
            def _val(df_r: pd.DataFrame, yr: int) -> float:
                row = df_r[df_r["year"] == yr]
                return float(row["capacity_operating_gw"].iloc[0]) if not row.empty else 0.0

            rows = []
            for region in REGIONS:
                df_r = ctx.proj_source_dict.get(region, pd.DataFrame())
                if df_r.empty:
                    continue
                rows.append({
                    "Region":    region,
                    "2024 (GW)": round(_val(df_r, 2024), 1),
                    "2030 (GW)": round(_val(df_r, 2030), 1),
                    "2040 (GW)": round(_val(df_r, 2040), 1),
                    "2050 (GW)": round(_val(df_r, 2050), 1),
                })
            if rows:
                tbl = pd.DataFrame(rows)
                # Global row uses proj_global_display
                def _gw(df: pd.DataFrame, yr: int) -> float:
                    row = df[df["year"] == yr]
                    return float(row["capacity_operating_gw"].iloc[0]) if not row.empty else 0.0
                tbl.loc[len(tbl)] = {
                    "Region":    "GLOBAL",
                    "2024 (GW)": round(_gw(ctx.proj_global_display, 2024), 1),
                    "2030 (GW)": round(_gw(ctx.proj_global_display, 2030), 1),
                    "2040 (GW)": round(_gw(ctx.proj_global_display, 2040), 1),
                    "2050 (GW)": round(_gw(ctx.proj_global_display, 2050), 1),
                }
                st.dataframe(tbl, use_container_width=True, hide_index=True)

    else:
        # Technology breakdown
        _regions_key = str(tuple(sorted(ctx.sel_regions))) if ctx.geo_filtered else "all"
        with st.spinner("Computing technology breakdown…"):
            tech_df = load_tech_projection(
                scenario_id=ctx.active_db_id,
                regions_key=_regions_key,
                smr_post2040_share=ctx.state.smr_post2040_share,
                smr_accel_start_year=ctx.state.smr_accel_start_year if ctx.state.smr_accel_gw_per_year > 0 else 0,
                smr_accel_gw_per_year=ctx.state.smr_accel_gw_per_year,
                what_if_overrides_json=ctx.wi_ov_json if ctx.wi_active else "",
            )
        st.caption(
            "Post-2040 technology split is estimated: SMR share from the lever; "
            "PWR/BWR/PHWR/Other proportions extrapolated from the 2040 fleet composition."
        )
        fig4t = chart4_technology(
            tech_df=tech_df,
            historical=ctx.hist_global_display if ctx.state.show_historical else pd.DataFrame(),
            scenario_id=ctx.active_key,
            show_historical=ctx.state.show_historical,
        )
        with _t4c1:
            st.plotly_chart(fig4t, use_container_width=True)
        with _t4c2:
            _t4t_dl = tech_df.pivot_table(
                index="year", columns="tech_group", values="capacity_gw"
            ).reset_index()
            _t4t_dl.columns.name = None
            _t4t_dl = _t4t_dl.round(2)
            st.download_button(
                "⬇ Data",
                data=to_excel_bytes(_t4t_dl, "Technology Breakdown"),
                file_name="technology_breakdown.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )

        with st.expander("Show technology capacity table (2024 / 2030 / 2040 / 2050)"):
            _tech_pivot = tech_df[tech_df["year"].isin([2024, 2030, 2040, 2050])].pivot_table(
                index="tech_group", columns="year", values="capacity_gw"
            ).reset_index()
            _tech_pivot.columns = ["Technology"] + [str(c) + " (GW)" for c in _tech_pivot.columns[1:]]
            _tech_pivot = _tech_pivot.round(1)
            st.dataframe(_tech_pivot, use_container_width=True, hide_index=True)


# ── Tab 3: Retirements ────────────────────────────────────────────────────────

def render_tab3_retirements(ctx: RenderContext) -> None:
    st.caption(
        "Annual retirements shown as negative values. Stacked by region — "
        "areas reflect gross capacity removed each year."
    )
    fig5 = chart5_retirements(
        projections_by_region=ctx.proj_by_region,
        regions=ctx.sel_regions,
        scenario_id=ctx.active_key,
    )
    _t5c1, _t5c2 = st.columns([8, 1])
    with _t5c1:
        st.plotly_chart(fig5, use_container_width=True)
    with _t5c2:
        _t5_rows = []
        for _r in ctx.sel_regions:
            _df_r = ctx.proj_source_dict.get(_r, pd.DataFrame())
            if _df_r.empty:
                continue
            for _, _row in _df_r.iterrows():
                if _row["year"] <= 2024:
                    continue
                _t5_rows.append({
                    "Region":           _r,
                    "Year":             int(_row["year"]),
                    "Retirements (GW)": round(float(_row["retirements_this_year_gw"]), 3),
                })
        _t5_dl = pd.DataFrame(_t5_rows)
        st.download_button(
            "⬇ Data",
            data=to_excel_bytes(_t5_dl, "Retirements"),
            file_name="retirements_by_region.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )


# ── Tab 4: New Additions ──────────────────────────────────────────────────────

def render_tab4_additions(ctx: RenderContext) -> None:
    split_by = st.radio(
        "View by",
        options=["geography", "technology"],
        format_func=lambda x: (
            "Geography (by region)" if x == "geography"
            else "Technology (SMR vs Large)"
        ),
        horizontal=True,
        key="additions_split",
    )

    _tech_df_for_chart6: pd.DataFrame | None = None
    if split_by == "technology":
        _regions_key_t3 = str(tuple(sorted(ctx.sel_regions))) if ctx.geo_filtered else "all"
        with st.spinner("Computing technology breakdown…"):
            _tech_df_for_chart6 = load_tech_projection(
                scenario_id=ctx.active_db_id,
                regions_key=_regions_key_t3,
                smr_post2040_share=ctx.state.smr_post2040_share,
                smr_accel_start_year=ctx.state.smr_accel_start_year if ctx.state.smr_accel_gw_per_year > 0 else 0,
                smr_accel_gw_per_year=ctx.state.smr_accel_gw_per_year,
                what_if_overrides_json=ctx.wi_ov_json if ctx.wi_active else "",
            )

    _no_date = ctx.filtered_reactors[
        (ctx.filtered_reactors["status"] == "Planned")
        & (ctx.filtered_reactors["expected_online_year"].isna())
    ]
    _nd_info_text = None
    if not _no_date.empty:
        _nd_gw = _no_date["net_capacity_mw"].sum() / 1000
        _nd_countries = _no_date["country"].value_counts().head(4)
        _nd_summary = ", ".join(f"{c} ({n})" for c, n in _nd_countries.items())
        _nd_info_text = (
            f"ℹ️ **{len(_no_date)} Planned reactors ({_nd_gw:.1f} GW) have no announced "
            f"construction-start date and are excluded from all projections.** "
            f"Largest groups: {_nd_summary}. "
            f"These units will appear in projections once an expected online year is confirmed."
        )

    fig6 = chart6_additions(
        projections_by_region=ctx.proj_by_region,
        reactors=ctx.filtered_reactors,
        regions=ctx.sel_regions,
        scenario_id=ctx.active_key,
        split_by=split_by,
        smr_post2040_share=ctx.state.smr_post2040_share,
        tech_df=_tech_df_for_chart6,
    )
    _t6c1, _t6c2 = st.columns([8, 1])
    with _t6c1:
        st.plotly_chart(fig6, use_container_width=True)
        st.caption(
            "Annual new capacity additions. Post-2040 SMR share set by the SMR lever. "
            "Pre-2040 SMR share derived from the scenario-adjusted pipeline "
            "(technology split) or summed by region (geography split)."
        )
        if _nd_info_text:
            st.info(_nd_info_text)
    with _t6c2:
        _t6_rows = []
        for _r in ctx.sel_regions:
            _df_r = ctx.proj_source_dict.get(_r, pd.DataFrame())
            if _df_r.empty:
                continue
            for _, _row in _df_r.iterrows():
                if _row["year"] <= 2024:
                    continue
                _t6_rows.append({
                    "Region":        _r,
                    "Year":          int(_row["year"]),
                    "Additions (GW)":round(float(_row["additions_this_year_gw"]), 3),
                })
        _t6_dl = pd.DataFrame(_t6_rows)
        st.download_button(
            "⬇ Additions",
            data=to_excel_bytes(_t6_dl, "New Additions"),
            file_name="new_additions_by_region.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
        _pipe_dl = ctx.filtered_reactors[ctx.filtered_reactors["status"].isin(
            ["UnderConstruction", "Planned", "Proposed"])].copy()
        _pipe_cols = ["name", "country", "region", "status", "net_capacity_mw",
                      "expected_online_year", "reactor_type", "is_smr"]
        _pipe_cols = [c for c in _pipe_cols if c in _pipe_dl.columns]
        _pipe_dl = _pipe_dl[_pipe_cols].sort_values(["status", "expected_online_year", "country"])
        _pipe_dl.columns = ["Name", "Country", "Region", "Status", "Capacity (MW)",
                            "Expected Online Year", "Reactor Type", "SMR"]
        st.download_button(
            "⬇ Pipeline",
            data=to_excel_bytes(_pipe_dl, "Pipeline Reactors"),
            file_name="pipeline_reactors.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )


# ── Tab 5: Country Snapshot ───────────────────────────────────────────────────

def render_tab5_country(ctx: RenderContext) -> None:
    col71, col72 = st.columns([1, 3])
    with col71:
        snapshot_year = st.select_slider(
            "Snapshot year",
            options=list(range(2005, 2051)),
            value=2026,
            key="country_snapshot_year",
        )
    with col72:
        st.caption(
            f"Country-level nuclear capacity for **{snapshot_year}** under the "
            f"**{ctx.active_key.title()}** scenario. Operating fleet + pipeline additions "
            f"expected online by that year (weighted by realization rates)."
        )

    with st.spinner("Loading country data…"):
        if ctx.wi_active:
            country_df = get_what_if_country_capacity(
                year=snapshot_year, scenario_id=ctx.active_db_id,
                state=ctx.proj_state,
            )
        else:
            country_df = load_country_capacity(
                year=snapshot_year, scenario_id=ctx.active_db_id,
            )

    if "country" in country_df.columns:
        country_df = country_df.copy()
        country_df["country"] = country_df["country"].str.title()

    fig7 = chart7_country_bar(
        country_df=country_df,
        year=snapshot_year,
        scenario_id=ctx.active_key,
        regions=ctx.sel_regions,
    )
    _t7c1, _t7c2 = st.columns([8, 1])
    with _t7c1:
        st.plotly_chart(fig7, use_container_width=True)
    with _t7c2:
        _t7_dl = country_df.copy()
        if ctx.sel_regions:
            _t7_dl = _t7_dl[_t7_dl["region"].isin(ctx.sel_regions)]
        _t7_dl = _t7_dl[_t7_dl["total_gw"] > 0].sort_values("total_gw", ascending=False)
        _t7_dl.columns = [c.replace("_", " ").title() for c in _t7_dl.columns]
        st.download_button(
            "⬇ Data",
            data=to_excel_bytes(_t7_dl, f"Country Snapshot {snapshot_year}"),
            file_name=f"country_snapshot_{snapshot_year}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )

    with st.expander("Show country data table"):
        tbl7 = country_df.copy()
        if ctx.sel_regions:
            tbl7 = tbl7[tbl7["region"].isin(ctx.sel_regions)]
        tbl7 = tbl7[tbl7["total_gw"] > 0].sort_values("total_gw", ascending=False)
        tbl7.columns = [c.replace("_", " ").title() for c in tbl7.columns]
        st.dataframe(tbl7, use_container_width=True, hide_index=True)


# ── Tab 6: World Map ──────────────────────────────────────────────────────────

def render_tab6_map(ctx: RenderContext) -> None:
    _mc1, _mc2, _mc3 = st.columns([2, 2, 5])
    with _mc1:
        map_year = st.select_slider(
            "Year",
            options=list(range(2005, 2051)),
            value=2024,
            key="map_year",
        )
    with _mc2:
        map_metric = st.radio(
            "Show",
            options=["total_gw", "operating_gw", "pipeline_gw"],
            format_func=lambda x: {
                "total_gw":     "Total (operating + pipeline)",
                "operating_gw": "Operating only",
                "pipeline_gw":  "Pipeline only",
            }[x],
            key="map_metric",
        )
    with _mc3:
        if map_year < 2024:
            st.caption(
                f"Historical view ({map_year}): showing reactors operating based on "
                "actual commissioning/retirement dates. Retired reactors not in the "
                "current database are excluded."
            )
        else:
            st.caption(
                f"Projection view ({map_year}): operating fleet accounting for "
                "retirements + pipeline additions weighted by realization rates "
                f"under the **{ctx.active_key.title()}** scenario."
            )

    with st.spinner("Loading map data…"):
        if ctx.wi_active:
            map_country_df = get_what_if_country_capacity(
                year=map_year, scenario_id=ctx.active_db_id,
                state=ctx.proj_state,
            )
        else:
            map_country_df = load_country_capacity(
                year=map_year, scenario_id=ctx.active_db_id,
            )

    if ctx.geo_filtered:
        map_country_df = map_country_df[map_country_df["region"].isin(ctx.sel_regions)]

    fig_map = chart_map(
        country_df=map_country_df,
        year=map_year,
        metric=map_metric,
        scenario_id=ctx.active_key,
    )
    _tm1, _tm2 = st.columns([8, 1])
    with _tm1:
        st.plotly_chart(fig_map, use_container_width=True)
    with _tm2:
        _map_dl = map_country_df[map_country_df[map_metric] > 0].copy()
        _map_dl = _map_dl.sort_values(map_metric, ascending=False)
        _map_dl.columns = [c.replace("_", " ").title() for c in _map_dl.columns]
        st.download_button(
            "⬇ Data",
            data=to_excel_bytes(_map_dl, f"Map {map_year}"),
            file_name=f"world_map_{map_year}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )


# ── Tab 7: Scenario Lab ───────────────────────────────────────────────────────

def render_tab7_lab(ctx: RenderContext) -> None:
    st.subheader("🔬 Scenario Lab")

    _lab_preset   = st.session_state.get("preset_selector", "base")
    _lab_sc_id    = _lab_preset if _lab_preset != "custom" else "base"
    _lab_defaults = PRESET_DEFAULTS.get(
        _lab_sc_id if _lab_sc_id != "custom" else "base",
        PRESET_DEFAULTS["base"],
    )

    lab_submitted = render_lab_panel(_lab_sc_id, _lab_defaults)

    if lab_submitted:
        # Step 1: recompute macro levers if they differ from preset
        if not levers_match_preset(ctx.state, ctx.sc_id):
            with st.spinner("Computing custom projection…"):
                custom_proj = write_and_run_custom_scenario(
                    extension_policy=ctx.state.extension_policy_global,
                    pipeline_uc_rate=ctx.state.pipeline_uc_rate,
                    pipeline_planned_rate=ctx.state.pipeline_planned_rate,
                    pipeline_proposed_rate=ctx.state.pipeline_proposed_rate,
                    construction_delay_adder=ctx.state.construction_delay_adder,
                    post2040_global_gw=ctx.state.post2040_global_growth_gw,
                    smr_uc_rate=ctx.state.smr_uc_rate,
                    smr_planned_rate=ctx.state.smr_planned_rate,
                    smr_proposed_rate=ctx.state.smr_proposed_rate,
                    china_post2040_gw=ctx.state.china_post2040_gw,
                    smr_accel_start_year=ctx.state.smr_accel_start_year,
                    smr_accel_gw_per_year=ctx.state.smr_accel_gw_per_year,
                    scenario_id=ctx.session_custom_id,
                )
            st.session_state["_custom_projection"] = custom_proj
            st.session_state["_custom_for_scenario"] = ctx.sc_id
            active_scenario_id = ctx.session_custom_id
        else:
            st.session_state.pop("_custom_projection", None)
            active_scenario_id = ctx.sc_id

        # Step 2: run what-if overrides on top
        wi_dict = build_wi_overrides_dict()
        if wi_dict:
            with st.spinner("Running what-if projection across all regions…"):
                try:
                    wi_all = run_what_if_all_regions(
                        scenario_id=active_scenario_id,
                        what_if_overrides=wi_dict,
                    )
                    st.session_state["wi_proj_all"] = wi_all
                    st.session_state["wi_result"]   = wi_all.get("Global")
                except Exception as e:
                    st.error(f"Projection failed: {e}")
        else:
            st.session_state["wi_proj_all"] = None
            st.session_state["wi_result"]   = None

        st.rerun()

    # ── Diff chart + bullet summary ───────────────────────────────────────────
    _has_wi_result     = st.session_state.get("wi_result") is not None
    _has_customisation = (
        _has_wi_result
        or bool(st.session_state.get("wi_overrides"))
        or bool(st.session_state.get("wi_synthetic"))
        or not levers_match_preset(ctx.state, ctx.active_key)
    )

    if _has_customisation and not ctx.lab_base_global.empty:
        wi_proj_for_chart = (
            st.session_state["wi_result"]
            if _has_wi_result
            else ctx.proj_global_display
        )

        _base_cols = ["year", "capacity_operating_gw",
                      "retirements_this_year_gw", "additions_this_year_gw", "is_bottom_up"]
        base_proj = ctx.lab_base_global[
            [c for c in _base_cols if c in ctx.lab_base_global.columns]
        ].copy()

        fig_wi = chart_what_if_diff(
            base_df=base_proj,
            whatif_df=wi_proj_for_chart,
            base_label=f"{ctx.sc_id.title()} (starting point)",
            whatif_label="Custom scenario",
        )

        _col_chart, _col_bullets = st.columns([3, 2], gap="large")
        with _col_chart:
            st.plotly_chart(fig_wi, use_container_width=True)

            delta_rows = []
            for yr in [2030, 2035, 2040, 2050]:
                b = base_proj.loc[base_proj["year"] == yr, "capacity_operating_gw"]
                w = wi_proj_for_chart.loc[wi_proj_for_chart["year"] == yr, "capacity_operating_gw"]
                if not b.empty and not w.empty:
                    delta = round(w.values[0] - b.values[0], 1)
                    delta_rows.append({
                        "Year": yr,
                        f"{ctx.active_key.title()} base (GW)": round(b.values[0], 1),
                        "Custom (GW)": round(w.values[0], 1),
                        "Delta (GW)":  f"{'+' if delta >= 0 else ''}{delta}",
                    })
            if delta_rows:
                st.dataframe(delta_rows, hide_index=True, use_container_width=True)

        with _col_bullets:
            st.markdown("**What's been customised**")
            _bullets = build_scenario_bullets(
                ctx.state,
                ctx.active_key,
                wi_overrides=st.session_state.get("wi_overrides", []),
                wi_synthetic=st.session_state.get("wi_synthetic", []),
            )
            if _bullets:
                for _b in _bullets:
                    st.markdown(f"- {_b}")
            else:
                st.caption("No changes from the base preset yet.")
