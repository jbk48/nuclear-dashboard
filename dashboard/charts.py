"""
charts.py — Plotly chart definitions for the nuclear capacity dashboard.

All data comes through model.api — no direct DB access.
"""
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
from typing import Optional
from config import TRANSITION_YEAR as _TRANSITION_YEAR

# ── Consistent color palette ───────────────────────────────────────────────
REGION_COLORS = {
    "United States":          "#1f77b4",
    "Canada & Mexico":        "#aec7e8",
    "France":                 "#2ca02c",
    "United Kingdom":         "#98df8a",
    "Rest of Western Europe": "#d62728",
    "Eastern Europe":         "#ff9896",
    "Russia":                 "#9467bd",
    "China":                  "#e6550d",
    "South Asia":             "#fdae6b",
    "East Asia":              "#8c564b",
    "Southeast Asia":         "#c49c94",
    "Emerging & Rest":        "#7f7f7f",
}

SCENARIO_COLORS = {
    "decline":      "#d62728",
    "conservative": "#ff7f0e",
    "base":         "#1f77b4",
    "optimistic":   "#2ca02c",
    "custom":       "#8c564b",   # brown — visually distinct
}
SCENARIO_NAMES = {
    "decline":      "Decline",
    "conservative": "Conservative",
    "base":         "Base",
    "optimistic":   "Optimistic",
    "custom":       "Custom",
}

TRANSITION_YEAR = _TRANSITION_YEAR   # sourced from config — do not hardcode
HISTORICAL_COLOR = "#555555"
BENCHMARK_COLORS = {
    "IAEA Low":  "#ff7f0e",
    "IAEA High": "#2ca02c",
    "IEA STEPS": "#1f77b4",
    "IEA APS":   "#9467bd",
    "IEA Low Nuclear": "#d62728",
}

# Pipeline stage colors — distinct: green / steel blue / light purple
PIPELINE_STAGE_COLORS = {
    "UnderConstruction": "#2ca02c",   # green
    "Planned":           "#1f77b4",   # steel blue (was orange — too close to grey)
    "Proposed":          "#c5b0d5",   # light purple (was light blue-grey)
}

CHART_TEMPLATE = "plotly_white"
FONT = dict(family="Inter, sans-serif", size=13)


# ── Chart 1 — Global Capacity: History & Projection ───────────────────────

def chart1_global_projection(
    projections: dict[str, pd.DataFrame],   # {scenario_id: df}
    historical: pd.DataFrame,
    benchmarks: pd.DataFrame,
    active_scenario: str = "base",
    compare_scenarios: list[str] = None,
    show_historical: bool = True,
    show_benchmarks: dict[str, bool] = None,
    show_transition_marker: bool = True,
    base_df: pd.DataFrame | None = None,    # dotted base line shown when what-if active
) -> go.Figure:
    """
    Chart 1 — Global Capacity: History & Projection (hero chart).
    Combines historical actuals with scenario projection lines.
    """
    if compare_scenarios is None:
        compare_scenarios = []
    if show_benchmarks is None:
        show_benchmarks = {k: True for k in BENCHMARK_COLORS}

    fig = go.Figure()

    # Historical shaded background
    if show_historical and not historical.empty:
        fig.add_vrect(
            x0=historical["year"].min(), x1=2024.5,
            fillcolor="rgba(200,200,200,0.15)", line_width=0,
            annotation_text="Historical", annotation_position="top left",
            annotation_font_size=11, annotation_font_color="#888",
        )
        fig.add_trace(go.Scatter(
            x=historical["year"], y=historical["capacity_gw"],
            name="Historical (observed)",
            mode="lines+markers",
            line=dict(color=HISTORICAL_COLOR, width=2.5),
            marker=dict(size=5),
            hovertemplate="<b>%{x}</b><br>Observed: %{y:.1f} GW<extra></extra>",
        ))

    # Scenario lines — active first (solid), comparisons secondary (dashed)
    scenarios_to_plot = [active_scenario] + [s for s in compare_scenarios if s != active_scenario]
    for sc_id in scenarios_to_plot:
        df = projections.get(sc_id)
        if df is None or df.empty:
            continue
        color = SCENARIO_COLORS.get(sc_id, "#333333")
        sc_name = SCENARIO_NAMES.get(sc_id, sc_id.title())
        is_active = (sc_id == active_scenario)

        # Split at transition year: bottom-up solid, top-down dashed
        df_bu = df[df["year"] <= TRANSITION_YEAR].copy()
        df_td = df[df["year"] >= TRANSITION_YEAR].copy()

        line_width = 3 if is_active else 1.8
        opacity = 1.0 if is_active else 0.65

        fig.add_trace(go.Scatter(
            x=df_bu["year"], y=df_bu["capacity_operating_gw"],
            name=sc_name,
            mode="lines",
            line=dict(color=color, width=line_width, dash="solid"),
            opacity=opacity,
            legendgroup=sc_id,
            showlegend=True,
            hovertemplate=f"<b>{sc_name}</b><br>Year: %{{x}}<br>Capacity: %{{y:.1f}} GW<extra></extra>",
        ))
        if not df_td.empty:
            fig.add_trace(go.Scatter(
                x=df_td["year"], y=df_td["capacity_operating_gw"],
                name=sc_name + " (post-transition)",
                mode="lines",
                line=dict(color=color, width=line_width, dash="dot"),
                opacity=opacity,
                legendgroup=sc_id,
                showlegend=False,
                hovertemplate=(
                    f"<b>{sc_name}</b><br>Year: %{{x}}<br>"
                    "Capacity: %{y:.1f} GW<br>"
                    "<i>Top-down projection</i><extra></extra>"
                ),
            ))

    # Dotted base line — shown when what-if is active to highlight the delta
    if base_df is not None and not base_df.empty:
        fig.add_trace(go.Scatter(
            x=base_df["year"], y=base_df["capacity_operating_gw"],
            name="Base (pre-scenario)",
            mode="lines",
            line=dict(color="#aaaaaa", width=1.8, dash="dot"),
            opacity=0.7,
            hovertemplate="<b>Base</b><br>Year: %{x}<br>Capacity: %{y:.1f} GW<extra></extra>",
        ))

    # Benchmark reference lines
    _add_benchmark_lines(fig, benchmarks, show_benchmarks)

    if show_transition_marker:
        fig.add_vline(
            x=TRANSITION_YEAR, line_dash="dash", line_color="#aaaaaa", line_width=1.5,
            annotation_text="2040 transition", annotation_position="top right",
            annotation_font_size=10, annotation_font_color="#aaa",
        )

    fig.update_layout(
        title=dict(text="Global Nuclear Capacity — History & Projection", font=dict(size=17)),
        xaxis=dict(title="Year", tickmode="linear", dtick=5, range=[2004, 2051]),
        yaxis=dict(title="Capacity (GW)", rangemode="tozero"),
        legend=dict(orientation="v", x=1.01, y=1, borderwidth=1),
        hovermode="x unified",
        template=CHART_TEMPLATE,
        font=FONT,
        margin=dict(l=60, r=160, t=60, b=50),
    )
    return fig


def _add_benchmark_lines(fig, benchmarks: pd.DataFrame, show_benchmarks: dict[str, bool]):
    """Helper: add IAEA/IEA benchmark reference points to a figure."""
    if benchmarks.empty:
        return

    benchmark_map = {
        ("IAEA_RDS1_2025", "Low"):           ("IAEA Low",        True),
        ("IAEA_RDS1_2025", "High"):          ("IAEA High",       True),
        ("IEA_WEO2024",    "STEPS"):         ("IEA STEPS",       True),
        ("IEA_WEO2024",    "APS"):           ("IEA APS",         False),
        ("IEA_WEO2024",    "LowNuclearCase"):("IEA Low Nuclear",  True),
    }

    added = set()
    for (src, sc_name), (label, default_show) in benchmark_map.items():
        if not show_benchmarks.get(label, default_show):
            continue
        bdf = benchmarks[(benchmarks["source"] == src) & (benchmarks["scenario_name"] == sc_name)]
        if bdf.empty:
            continue
        color = BENCHMARK_COLORS.get(label, "#999999")
        show_leg = label not in added

        mode = "markers"
        line_opts = dict(color=color, width=0)

        fig.add_trace(go.Scatter(
            x=bdf["year"], y=bdf["capacity_gw"],
            name=label,
            mode=mode,
            line=line_opts,
            marker=dict(symbol="diamond", size=10, color=color,
                        line=dict(color="white", width=1)),
            opacity=0.85,
            legendgroup="benchmarks",
            showlegend=show_leg,
            hovertemplate=f"<b>{label}</b><br>Year: %{{x}}<br>%{{y:.0f}} GW<extra></extra>",
        ))
        added.add(label)


# ── Chart 2 — Retirement & Addition Waterfall (unused — kept for future use) ──

def chart2_waterfall(
    projection: pd.DataFrame,
    scenario_id: str = "base",
) -> go.Figure:
    """
    Chart 2 — Annual retirements (negative) and additions (positive) as bars,
    with net change overlay line.
    """
    df = projection.copy()
    df = df[df["year"] > 2024]

    sc_name = SCENARIO_NAMES.get(scenario_id, scenario_id.title())
    color = SCENARIO_COLORS.get(scenario_id, "#1f77b4")

    is_post_2040 = df["year"] > TRANSITION_YEAR
    fig = go.Figure()

    fig.add_trace(go.Bar(
        x=df["year"],
        y=-df["retirements_this_year_gw"],
        name="Retirements",
        marker_color=["rgba(214,39,40,0.8)" if not p else "rgba(214,39,40,0.45)"
                      for p in is_post_2040],
        hovertemplate="<b>%{x}</b><br>Retirements: %{customdata:.2f} GW<extra></extra>",
        customdata=df["retirements_this_year_gw"],
    ))

    fig.add_trace(go.Bar(
        x=df["year"],
        y=df["additions_this_year_gw"],
        name="New Additions",
        marker_color=["rgba(44,160,44,0.8)" if not p else "rgba(44,160,44,0.45)"
                      for p in is_post_2040],
        hovertemplate="<b>%{x}</b><br>Additions: %{y:.2f} GW<extra></extra>",
    ))

    if "adders_this_year_gw" in df.columns and df["adders_this_year_gw"].abs().sum() > 0:
        fig.add_trace(go.Bar(
            x=df["year"],
            y=df["adders_this_year_gw"],
            name="Manual Adders",
            marker_color="rgba(148,103,189,0.8)",
            hovertemplate="<b>%{x}</b><br>Manual adder: %{y:.2f} GW<extra></extra>",
        ))

    net = df["additions_this_year_gw"] - df["retirements_this_year_gw"]
    if "adders_this_year_gw" in df.columns:
        net = net + df["adders_this_year_gw"].fillna(0)
    fig.add_trace(go.Scatter(
        x=df["year"], y=net,
        name="Net Change",
        mode="lines+markers",
        line=dict(color=color, width=2),
        marker=dict(size=4),
        hovertemplate="<b>%{x}</b><br>Net: %{y:.2f} GW<extra></extra>",
    ))

    fig.add_hline(y=0, line_color="#888", line_width=1, line_dash="dot")
    if TRANSITION_YEAR in df["year"].values:
        fig.add_vline(
            x=TRANSITION_YEAR, line_dash="dash", line_color="#aaa", line_width=1.5,
            annotation_text="2040 transition", annotation_position="top right",
            annotation_font_size=10, annotation_font_color="#aaa",
        )

    fig.update_layout(
        title=dict(text=f"Annual Retirements & Additions — {sc_name} Scenario", font=dict(size=17)),
        xaxis=dict(title="Year", tickmode="linear", dtick=5),
        yaxis=dict(title="GW per Year"),
        barmode="relative",
        legend=dict(orientation="h", y=-0.2),
        hovermode="x unified",
        template=CHART_TEMPLATE,
        font=FONT,
        margin=dict(l=60, r=40, t=60, b=80),
    )
    return fig


# ── Chart 3 — Pipeline Funnel (unused — kept for future use) ──────────────

def chart3_pipeline(
    reactors: pd.DataFrame,
    highlight_smr: bool = True,
) -> go.Figure:
    """
    Chart 3 — Pipeline capacity by expected online year, colored by stage.
    Updated colors: UC=green, Planned=steel blue, Proposed=light purple.
    """
    pipeline_statuses = ["UnderConstruction", "Planned", "Proposed"]
    df = reactors[reactors["status"].isin(pipeline_statuses)].copy()
    df = df[df["expected_online_year"].notna()].copy()
    df["expected_online_year"] = df["expected_online_year"].astype(int)
    df = df[(df["expected_online_year"] >= 2024) & (df["expected_online_year"] <= 2050)]

    stage_labels = {
        "UnderConstruction": "Under Construction",
        "Planned":           "Planned",
        "Proposed":          "Proposed",
    }

    fig = go.Figure()

    for status in pipeline_statuses:
        sdf = df[df["status"] == status].copy()
        if sdf.empty:
            continue
        color = PIPELINE_STAGE_COLORS[status]

        if highlight_smr and "is_smr" in sdf.columns:
            for is_smr_val, smr_label, opacity in [(0, "", 0.85), (1, " (SMR)", 1.0)]:
                sub = sdf[sdf["is_smr"] == is_smr_val]
                if sub.empty:
                    continue
                agg = (sub.groupby("expected_online_year")
                          .agg(capacity_gw=("net_capacity_mw", lambda x: x.sum() / 1000),
                               count=("reactor_id", "count"))
                          .reset_index())
                # SMR bars slightly lighter
                bar_color = color if is_smr_val == 0 else _lighten(color)
                fig.add_trace(go.Bar(
                    x=agg["expected_online_year"],
                    y=agg["capacity_gw"],
                    name=stage_labels[status] + smr_label,
                    marker_color=bar_color,
                    opacity=opacity,
                    legendgroup=status,
                    showlegend=(is_smr_val == 0),
                    hovertemplate=(
                        f"<b>{stage_labels[status]}{smr_label}</b><br>"
                        "Year: %{x}<br>Capacity: %{y:.1f} GW<br>"
                        "Units: %{customdata}<extra></extra>"
                    ),
                    customdata=agg["count"],
                ))
        else:
            agg = (sdf.groupby("expected_online_year")
                      .agg(capacity_gw=("net_capacity_mw", lambda x: x.sum() / 1000),
                           count=("reactor_id", "count"))
                      .reset_index())
            fig.add_trace(go.Bar(
                x=agg["expected_online_year"],
                y=agg["capacity_gw"],
                name=stage_labels[status],
                marker_color=color,
                hovertemplate=(
                    f"<b>{stage_labels[status]}</b><br>"
                    "Year: %{x}<br>Capacity: %{y:.1f} GW<br>"
                    "Units: %{customdata}<extra></extra>"
                ),
                customdata=agg["count"],
            ))

    fig.add_vline(
        x=TRANSITION_YEAR, line_dash="dash", line_color="#aaa", line_width=1.5,
        annotation_text="Model transition", annotation_position="top right",
        annotation_font_size=10, annotation_font_color="#aaa",
    )

    fig.update_layout(
        title=dict(text="Pipeline Funnel — Capacity by Expected Online Year", font=dict(size=17)),
        xaxis=dict(title="Expected Online Year", tickmode="linear", dtick=5, range=[2024, 2051]),
        yaxis=dict(title="Capacity (GW)", rangemode="tozero"),
        barmode="stack",
        legend=dict(orientation="h", y=-0.2),
        hovermode="x unified",
        template=CHART_TEMPLATE,
        font=FONT,
        margin=dict(l=60, r=40, t=60, b=80),
    )
    return fig


def _lighten(hex_color: str, factor: float = 0.5) -> str:
    """Lighten a hex color by blending toward white."""
    hex_color = hex_color.lstrip("#")
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    r = int(r + (255 - r) * factor)
    g = int(g + (255 - g) * factor)
    b = int(b + (255 - b) * factor)
    return f"#{r:02x}{g:02x}{b:02x}"


# ── Chart 4 — Regional Stacked Area ───────────────────────────────────────

def chart4_regional(
    projections_by_region: dict[str, pd.DataFrame],
    historical_by_region: dict[str, pd.DataFrame],
    regions: list[str],
    scenario_id: str = "base",
    show_historical: bool = True,
) -> go.Figure:
    """
    Chart 4 — Stacked area chart showing capacity by region (2005–2050).
    """
    sc_name = SCENARIO_NAMES.get(scenario_id, scenario_id.title())
    fig = go.Figure()

    if show_historical:
        fig.add_vrect(
            x0=2004.5, x1=2024.5,
            fillcolor="rgba(200,200,200,0.15)", line_width=0,
        )

    ordered_regions = [r for r in regions if r in projections_by_region]

    for region in ordered_regions:
        color = REGION_COLORS.get(region, "#999999")
        hist_df = historical_by_region.get(region, pd.DataFrame())
        proj_df = projections_by_region.get(region, pd.DataFrame())

        if show_historical and not hist_df.empty:
            x_hist = list(hist_df["year"])
            y_hist = list(hist_df["capacity_gw"])
        else:
            x_hist, y_hist = [], []

        if not proj_df.empty:
            proj_future = proj_df[proj_df["year"] >= 2025]
            # Split at transition: bottom-up (≤2040) vs top-down (>2040)
            proj_bu = proj_future[proj_future["year"] <= TRANSITION_YEAR]
            proj_td = proj_future[proj_future["year"] > TRANSITION_YEAR]
            x_bu = list(proj_bu["year"])
            y_bu = list(proj_bu["capacity_operating_gw"])
            x_td = list(proj_td["year"])
            y_td = list(proj_td["capacity_operating_gw"])
        else:
            x_bu, y_bu, x_td, y_td = [], [], [], []

        # Bottom-up + historical: solid, full opacity
        x_all = x_hist + x_bu
        y_all = y_hist + y_bu
        if not x_all:
            continue

        fig.add_trace(go.Scatter(
            x=x_all, y=y_all,
            name=region,
            fill="tonexty",
            mode="lines",
            line=dict(color=color, width=0.8),
            fillcolor=color,
            opacity=0.75,
            stackgroup="regions",
            legendgroup=region,
            showlegend=True,
            hovertemplate=f"<b>{region}</b><br>%{{x}}: %{{y:.1f}} GW<extra></extra>",
        ))

        # Top-down (post-2040): dashed line, lighter fill — same stackgroup so stacking continues
        if x_td:
            fig.add_trace(go.Scatter(
                x=x_td, y=y_td,
                name=region + " (top-down)",
                fill="tonexty",
                mode="lines",
                line=dict(color=color, width=0.8, dash="dot"),
                fillcolor=color,
                opacity=0.35,
                stackgroup="regions",
                legendgroup=region,
                showlegend=False,
                hovertemplate=(
                    f"<b>{region}</b><br>%{{x}}: %{{y:.1f}} GW"
                    "<br><i>Top-down estimate</i><extra></extra>"
                ),
            ))

    fig.add_vline(
        x=2024.5, line_dash="dash", line_color="#888", line_width=1,
        annotation_text="Projection →", annotation_position="top right",
        annotation_font_size=10, annotation_font_color="#888",
    )
    fig.add_vline(
        x=TRANSITION_YEAR, line_dash="dash", line_color="#aaa", line_width=1,
        annotation_text="2040 transition", annotation_position="top left",
        annotation_font_size=10, annotation_font_color="#aaa",
    )

    fig.update_layout(
        title=dict(text=f"Regional Breakdown — {sc_name} Scenario", font=dict(size=17)),
        xaxis=dict(title="Year", tickmode="linear", dtick=5, range=[2004, 2051]),
        yaxis=dict(title="Capacity (GW)", rangemode="tozero"),
        legend=dict(orientation="v", x=1.01, y=1, borderwidth=1),
        hovermode="x unified",
        template=CHART_TEMPLATE,
        font=FONT,
        margin=dict(l=60, r=180, t=60, b=50),
    )
    return fig


# ── Chart 4b — Technology Stacked Area ────────────────────────────────────

TECH_COLORS = {
    "PWR":   "#1f77b4",
    "BWR":   "#2ca02c",
    "PHWR":  "#ff7f0e",
    "SMR":   "#9467bd",
    "Other": "#7f7f7f",
}
TECH_LABELS = {
    "PWR":   "PWR (Pressurized Water)",
    "BWR":   "BWR (Boiling Water)",
    "PHWR":  "PHWR / CANDU (Heavy Water)",
    "SMR":   "SMR (Small Modular)",
    "Other": "Other / Unknown",
}
TECH_ORDER = ["PWR", "BWR", "PHWR", "SMR", "Other"]


def chart4_technology(
    tech_df: pd.DataFrame,
    historical: pd.DataFrame,
    scenario_id: str = "base",
    show_historical: bool = True,
) -> go.Figure:
    """
    Chart 4b — Stacked area chart of capacity by technology group (2005–2050).
    tech_df must have columns: year, tech_group, capacity_gw, is_bottom_up.
    Historical is the global historical series (shading only, not stacked by tech).
    Post-2040 is top-down — caveat annotated on chart.
    """
    sc_name = SCENARIO_NAMES.get(scenario_id, scenario_id.title())
    fig = go.Figure()

    if show_historical:
        fig.add_vrect(
            x0=2004.5, x1=2024.5,
            fillcolor="rgba(200,200,200,0.15)", line_width=0,
        )
        # Overlay historical global line for reference
        if not historical.empty:
            fig.add_trace(go.Scatter(
                x=historical["year"], y=historical["capacity_gw"],
                name="Historical (observed)",
                mode="lines",
                line=dict(color=HISTORICAL_COLOR, width=2, dash="dot"),
                opacity=0.6,
                hovertemplate="<b>Historical</b><br>%{x}: %{y:.1f} GW<extra></extra>",
            ))

    for group in TECH_ORDER:
        df_g = tech_df[tech_df["tech_group"] == group].copy()
        if df_g.empty or df_g["capacity_gw"].sum() == 0:
            continue

        # Split at transition year — exclude 2040 from top-down to avoid double-stacking
        df_bu = df_g[df_g["year"] <= TRANSITION_YEAR]
        df_td = df_g[df_g["year"] > TRANSITION_YEAR]

        color = TECH_COLORS.get(group, "#999999")
        label = TECH_LABELS.get(group, group)

        # Bottom-up portion
        if not df_bu.empty:
            fig.add_trace(go.Scatter(
                x=df_bu["year"], y=df_bu["capacity_gw"],
                name=label,
                fill="tonexty",
                mode="lines",
                line=dict(color=color, width=0.8),
                fillcolor=color,
                opacity=0.80,
                stackgroup="tech",
                legendgroup=group,
                showlegend=True,
                hovertemplate=f"<b>{label}</b><br>%{{x}}: %{{y:.1f}} GW<extra></extra>",
            ))
        # Top-down continuation (slightly lighter, same stack)
        if not df_td.empty:
            fig.add_trace(go.Scatter(
                x=df_td["year"], y=df_td["capacity_gw"],
                name=label + " (top-down)",
                fill="tonexty",
                mode="lines",
                line=dict(color=color, width=0.8, dash="dot"),
                fillcolor=color,
                opacity=0.45,
                stackgroup="tech",
                legendgroup=group,
                showlegend=False,
                hovertemplate=(
                    f"<b>{label}</b><br>%{{x}}: %{{y:.1f}} GW"
                    "<br><i>Top-down estimate</i><extra></extra>"
                ),
            ))

    fig.add_vline(
        x=2024.5, line_dash="dash", line_color="#888", line_width=1,
        annotation_text="Projection →", annotation_position="top right",
        annotation_font_size=10, annotation_font_color="#888",
    )
    fig.add_vline(
        x=TRANSITION_YEAR, line_dash="dash", line_color="#aaa", line_width=1,
        annotation_text="2040 transition", annotation_position="top left",
        annotation_font_size=10, annotation_font_color="#aaa",
    )

    fig.update_layout(
        title=dict(
            text=f"Capacity by Technology — {sc_name} Scenario",
            font=dict(size=17),
        ),
        xaxis=dict(title="Year", tickmode="linear", dtick=5, range=[2004, 2051]),
        yaxis=dict(title="Capacity (GW)", rangemode="tozero"),
        legend=dict(orientation="v", x=1.01, y=1, borderwidth=1),
        hovermode="x unified",
        template=CHART_TEMPLATE,
        font=FONT,
        margin=dict(l=60, r=180, t=60, b=50),
    )
    return fig


# ── Chart 5 — Retirements (Negative Shaded Area by Region) ────────────────

def chart5_retirements(
    projections_by_region: dict[str, pd.DataFrame],
    regions: list[str],
    scenario_id: str = "base",
) -> go.Figure:
    """
    Chart 5 — Cumulative retirements as a negative shaded area chart by region.
    Each region's annual retirements are shown as negative values (stacked downward).
    """
    sc_name = SCENARIO_NAMES.get(scenario_id, scenario_id.title())
    fig = go.Figure()

    ordered_regions = [r for r in regions if r in projections_by_region]

    for region in ordered_regions:
        proj_df = projections_by_region.get(region, pd.DataFrame())
        if proj_df.empty:
            continue
        df = proj_df[proj_df["year"] > 2024].copy()
        if df.empty:
            continue

        color = REGION_COLORS.get(region, "#999999")
        # Negate retirements so bars go downward; keep positive values for hover display
        y_vals = (-df["retirements_this_year_gw"]).tolist()
        pos_vals = df["retirements_this_year_gw"].tolist()

        fig.add_trace(go.Bar(
            x=df["year"].tolist(),
            y=y_vals,
            name=region,
            marker_color=color,
            opacity=0.85,
            hovertemplate=f"<b>{region}</b><br>%{{x}}: %{{customdata:.2f}} GW retired<extra></extra>",
            customdata=pos_vals,
        ))

    fig.add_hline(y=0, line_color="#888", line_width=1, line_dash="dot")
    fig.add_vline(
        x=TRANSITION_YEAR, line_dash="dash", line_color="#aaa", line_width=1.5,
        annotation_text="2040 transition", annotation_position="top right",
        annotation_font_size=10, annotation_font_color="#aaa",
    )

    fig.update_layout(
        title=dict(text=f"Annual Retirements by Region — {sc_name} Scenario", font=dict(size=17)),
        xaxis=dict(title="Year", tickmode="linear", dtick=5),
        yaxis=dict(title="Retirements (GW/yr, negative)"),
        barmode="relative",
        legend=dict(orientation="v", x=1.01, y=1, borderwidth=1),
        hovermode="x unified",
        template=CHART_TEMPLATE,
        font=FONT,
        margin=dict(l=60, r=180, t=60, b=50),
    )
    return fig


# ── Chart 6 — New Additions (Shaded Area, Geography or Tech split) ────────

def chart6_additions(
    projections_by_region: dict[str, pd.DataFrame],
    reactors: pd.DataFrame,
    regions: list[str],
    scenario_id: str = "base",
    split_by: str = "geography",    # "geography" | "technology"
    smr_post2040_share: float = 0.20,
    tech_df: "pd.DataFrame | None" = None,
) -> go.Figure:
    """
    Chart 6 — Annual new additions as a shaded area chart.
    split_by="geography": stacked by region.
    split_by="technology": stacked by SMR vs Large reactor type.

    tech_df: optional output from get_tech_projection(). When provided, the
    pre-2040 SMR split is derived from year-over-year SMR capacity changes in
    tech_df, which correctly account for construction delay adders, realization
    rates, and SMR acceleration. Without it, falls back to raw pipeline data
    (acknowledged approximate: ignores delay adder and realization weights).
    """
    sc_name = SCENARIO_NAMES.get(scenario_id, scenario_id.title())
    fig = go.Figure()

    if split_by == "geography":
        ordered_regions = [r for r in regions if r in projections_by_region]
        for region in ordered_regions:
            proj_df = projections_by_region.get(region, pd.DataFrame())
            if proj_df.empty:
                continue
            df = proj_df[proj_df["year"] > 2024].copy()
            if df.empty:
                continue
            color = REGION_COLORS.get(region, "#999999")
            fig.add_trace(go.Bar(
                x=df["year"].tolist(),
                y=df["additions_this_year_gw"].tolist(),
                name=region,
                marker_color=color,
                opacity=0.85,
                hovertemplate=f"<b>{region}</b><br>%{{x}}: %{{y:.2f}} GW<extra></extra>",
            ))
        title_suffix = "by Region"

    else:
        # Aggregate correctly-adjusted total global additions from projection
        frames = [
            proj_df[proj_df["year"] > 2024][["year", "additions_this_year_gw"]].copy()
            for region, proj_df in projections_by_region.items()
            if region in regions and not proj_df.empty
        ]
        if not frames:
            fig.update_layout(title="No data")
            return fig

        all_proj = (
            pd.concat(frames)
            .groupby("year", as_index=False)["additions_this_year_gw"]
            .sum()
            .sort_values("year")
            .reset_index(drop=True)
        )

        if all_proj.empty:
            fig.update_layout(title="No data")
            return fig

        # ── SMR additions by year ─────────────────────────────────────────────
        # Pre-2040: derive from tech_df year-over-year SMR delta (correctly
        # accounts for delay adder, realization rates, SMR acceleration).
        # SMR retirements before 2040 are negligible so delta ≈ gross additions.
        # Post-2040: use smr_post2040_share of the global addition.
        # Fallback (no tech_df): raw pipeline fraction (approximate).

        if tech_df is not None and not tech_df.empty:
            smr_cap = (
                tech_df[tech_df["tech_group"] == "SMR"]
                .set_index("year")["capacity_gw"]
                .to_dict()
            )
            smr_add_by_year: dict[int, float] = {}
            for yr in range(2025, 2041):
                delta = smr_cap.get(yr, 0.0) - smr_cap.get(yr - 1, 0.0)
                smr_add_by_year[yr] = max(0.0, delta)

            def smr_gw_for_year(row):
                yr = int(row["year"])
                total = float(row["additions_this_year_gw"])
                if yr <= 2040:
                    return min(smr_add_by_year.get(yr, 0.0), total)
                return total * smr_post2040_share

        else:
            # Fallback: raw pipeline fractions (approximate — ignores delay adder)
            pipeline_statuses = ["UnderConstruction", "Planned", "Proposed"]
            pipe_df = reactors[reactors["status"].isin(pipeline_statuses)].copy()
            pipe_df = pipe_df[pipe_df["expected_online_year"].notna()].copy()
            pipe_df["expected_online_year"] = pipe_df["expected_online_year"].astype(int)
            pipe_df = pipe_df[(pipe_df["expected_online_year"] >= 2025) &
                              (pipe_df["expected_online_year"] <= 2040)]

            smr_frac_by_year: dict[int, float] = {}
            for yr in range(2025, 2041):
                yr_df = pipe_df[pipe_df["expected_online_year"] == yr]
                if yr_df.empty or yr_df["net_capacity_mw"].sum() == 0:
                    smr_frac_by_year[yr] = 0.10
                else:
                    smr_mw = yr_df[yr_df["is_smr"] == 1]["net_capacity_mw"].sum()
                    smr_frac_by_year[yr] = smr_mw / yr_df["net_capacity_mw"].sum()

            def smr_gw_for_year(row):
                yr = int(row["year"])
                total = float(row["additions_this_year_gw"])
                if yr <= 2040:
                    return total * smr_frac_by_year.get(yr, 0.10)
                return total * smr_post2040_share

        smr_gw   = all_proj.apply(smr_gw_for_year, axis=1)
        large_gw = all_proj["additions_this_year_gw"] - smr_gw

        for tech, vals, color in [
            ("Large Reactors", large_gw, "#1f77b4"),
            ("SMRs", smr_gw, "#2ca02c"),
        ]:
            fig.add_trace(go.Bar(
                x=all_proj["year"].tolist(),
                y=vals.tolist(),
                name=tech,
                marker_color=color,
                opacity=0.80,
                hovertemplate=f"<b>{tech}</b><br>%{{x}}: %{{y:.2f}} GW<extra></extra>",
            ))
        title_suffix = "by Technology"

    fig.add_hline(y=0, line_color="#888", line_width=1, line_dash="dot")
    vline_note = (
        f"2040 — SMR share switches to lever ({smr_post2040_share*100:.0f}%)"
        if split_by == "technology" else "2040 transition"
    )
    fig.add_vline(
        x=TRANSITION_YEAR, line_dash="dash", line_color="#aaa", line_width=1.5,
        annotation_text=vline_note, annotation_position="top right",
        annotation_font_size=10, annotation_font_color="#aaa",
    )

    fig.update_layout(
        title=dict(text=f"Annual New Additions {title_suffix} — {sc_name} Scenario", font=dict(size=17)),
        xaxis=dict(title="Year", tickmode="linear", dtick=5),
        yaxis=dict(title="Additions (GW/yr)", rangemode="tozero"),
        barmode="stack",
        legend=dict(orientation="v", x=1.01, y=1, borderwidth=1),
        hovermode="x unified",
        template=CHART_TEMPLATE,
        font=FONT,
        margin=dict(l=60, r=180, t=60, b=50),
    )
    return fig


# ── Chart 7 — Country Capacity Snapshot ───────────────────────────────────

def chart7_country_bar(
    country_df: pd.DataFrame,
    year: int = 2026,
    scenario_id: str = "base",
    regions: Optional[list] = None,
) -> go.Figure:
    """
    Chart 7 — Horizontal stacked bar chart showing capacity by country
    for a given snapshot year. Bars colored by region; stacked as
    Operating Fleet + Pipeline Additions coming online by that year.
    """
    sc_name = SCENARIO_NAMES.get(scenario_id, scenario_id.title())

    df = country_df.copy()
    if regions:
        df = df[df["region"].isin(regions)]

    df = df[df["total_gw"] > 0.01].sort_values("total_gw", ascending=True)

    if df.empty:
        fig = go.Figure()
        fig.update_layout(
            title=f"Country Capacity Snapshot ({year}) — no data for selected regions",
            template=CHART_TEMPLATE,
        )
        return fig

    region_colors = [REGION_COLORS.get(r, "#999999") for r in df["region"]]
    pipeline_colors = [_lighten(REGION_COLORS.get(r, "#999999"), 0.45) for r in df["region"]]

    fig = go.Figure()

    fig.add_trace(go.Bar(
        x=df["operating_gw"],
        y=df["country"],
        name="Operating Fleet",
        orientation="h",
        marker_color=region_colors,
        opacity=0.9,
        hovertemplate="<b>%{y}</b><br>Operating: %{x:.1f} GW<extra></extra>",
    ))

    if df["pipeline_gw"].sum() > 0.01:
        fig.add_trace(go.Bar(
            x=df["pipeline_gw"],
            y=df["country"],
            name=f"Pipeline additions (online ≤ {year})",
            orientation="h",
            marker_color=pipeline_colors,
            opacity=0.75,
            hovertemplate="<b>%{y}</b><br>Pipeline additions: %{x:.1f} GW<extra></extra>",
        ))

    fig.update_layout(
        title=dict(
            text=f"Country-Level Capacity Snapshot ({year}) — {sc_name} Scenario",
            font=dict(size=17),
        ),
        xaxis=dict(title="Capacity (GW)"),
        yaxis=dict(title="", automargin=True),
        barmode="stack",
        legend=dict(orientation="h", y=-0.08),
        hovermode="y unified",
        template=CHART_TEMPLATE,
        font=FONT,
        height=max(500, len(df) * 22 + 130),
        margin=dict(l=170, r=40, t=60, b=100),
    )
    return fig


# ── Chart 8 — World Capacity Map (Choropleth) ─────────────────────────────

# Mapping from our DB country names → Plotly-recognised country names
_COUNTRY_PLOTLY = {
    "UNITED STATES OF AMERICA": "United States",
    "UNITED KINGDOM":           "United Kingdom",
    "SOUTH KOREA":              "South Korea",
    "CZECH REPUBLIC":           "Czech Republic",
    "SOUTH AFRICA":             "South Africa",
    "SAUDI ARABIA":             "Saudi Arabia",
    "VIET NAM":                 "Vietnam",
    "UAE":                      "United Arab Emirates",
    "TAIWAN":                   "Taiwan",          # may not render on all projections
}


def chart_map(
    country_df: pd.DataFrame,
    year: int = 2026,
    metric: str = "total_gw",      # "total_gw" | "operating_gw" | "pipeline_gw"
    scenario_id: str = "base",
) -> go.Figure:
    """
    Chart 8 — World choropleth: nuclear capacity by country.
    country_df must have columns: country, region, operating_gw, pipeline_gw, total_gw.
    metric controls which value is used for shading.
    """
    metric_labels = {
        "total_gw":     "Total Capacity (GW)",
        "operating_gw": "Operating Capacity (GW)",
        "pipeline_gw":  "Pipeline Capacity (GW)",
    }
    sc_name = SCENARIO_NAMES.get(scenario_id, scenario_id.title())

    df = country_df[country_df[metric] > 0].copy()
    df["country_plotly"] = df["country"].str.upper().map(
        lambda c: _COUNTRY_PLOTLY.get(c, c.title())
    )
    df["hover"] = (
        "<b>" + df["country_plotly"] + "</b><br>"
        + "Operating: " + df["operating_gw"].round(1).astype(str) + " GW<br>"
        + "Pipeline:  " + df["pipeline_gw"].round(1).astype(str) + " GW<br>"
        + "Total:     " + df["total_gw"].round(1).astype(str) + " GW"
    )

    fig = go.Figure(go.Choropleth(
        locations=df["country_plotly"],
        locationmode="country names",
        z=df[metric],
        text=df["hover"],
        hovertemplate="%{text}<extra></extra>",
        colorscale=[
            [0.0,  "#edf8e9"],
            [0.15, "#bae4b3"],
            [0.40, "#74c476"],
            [0.70, "#238b45"],
            [1.0,  "#005a32"],
        ],
        colorbar=dict(
            title=dict(text=metric_labels.get(metric, metric), side="right"),
            thickness=14,
            len=0.6,
        ),
        marker_line_color="#2d8a4e",
        marker_line_width=1.2,
    ))

    fig.update_layout(
        title=dict(
            text=f"Nuclear Capacity by Country — {year}  ({sc_name} scenario)",
            font=dict(size=17),
        ),
        geo=dict(
            showframe=False,
            showcoastlines=True,
            coastlinecolor="#cccccc",
            showland=True,
            landcolor="#f5f5f5",
            showocean=True,
            oceancolor="#e8f4f8",
            showlakes=False,
            projection_type="natural earth",
        ),
        template=CHART_TEMPLATE,
        font=FONT,
        margin=dict(l=0, r=0, t=60, b=0),
        height=520,
    )
    return fig


# ── What-If diff chart ────────────────────────────────────────────────────

def chart_what_if_diff(
    base_df: pd.DataFrame,
    whatif_df: pd.DataFrame,
    base_label: str = "Base scenario",
    whatif_label: str = "What-If",
) -> go.Figure:
    """
    Overlay the base scenario projection against a what-if variant.
    Shades the gap between them to make the delta immediately visible.
    """
    fig = go.Figure()

    base_color   = "#4a90d9"
    whatif_color = "#e07b39"
    fill_color   = "rgba(224,123,57,0.15)"

    # Shaded fill between the two lines
    fig.add_trace(go.Scatter(
        x=list(base_df["year"]) + list(whatif_df["year"])[::-1],
        y=list(base_df["capacity_operating_gw"]) +
          list(whatif_df["capacity_operating_gw"])[::-1],
        fill="toself",
        fillcolor=fill_color,
        line=dict(width=0),
        showlegend=False,
        hoverinfo="skip",
    ))

    # Base scenario line
    fig.add_trace(go.Scatter(
        x=base_df["year"],
        y=base_df["capacity_operating_gw"],
        name=base_label,
        mode="lines",
        line=dict(color=base_color, width=2.5),
        hovertemplate=f"<b>{base_label}</b><br>%{{x}}: %{{y:.1f}} GW<extra></extra>",
    ))

    # What-if line
    fig.add_trace(go.Scatter(
        x=whatif_df["year"],
        y=whatif_df["capacity_operating_gw"],
        name=whatif_label,
        mode="lines",
        line=dict(color=whatif_color, width=2.5, dash="dot"),
        hovertemplate=f"<b>{whatif_label}</b><br>%{{x}}: %{{y:.1f}} GW<extra></extra>",
    ))

    # Annotate net delta at 2030, 2040, 2050
    for yr in [2030, 2040, 2050]:
        b = base_df.loc[base_df["year"] == yr, "capacity_operating_gw"]
        w = whatif_df.loc[whatif_df["year"] == yr, "capacity_operating_gw"]
        if b.empty or w.empty:
            continue
        delta = round(w.values[0] - b.values[0], 1)
        sign  = "+" if delta >= 0 else ""
        fig.add_annotation(
            x=yr,
            y=max(b.values[0], w.values[0]),
            text=f"{sign}{delta} GW",
            showarrow=False,
            yshift=12,
            font=dict(size=11, color=whatif_color if delta != 0 else "#888"),
        )

    # Transition year marker
    fig.add_vline(
        x=_TRANSITION_YEAR, line_dash="dot",
        line_color="#aaaaaa", line_width=1,
        annotation_text="2040", annotation_position="top right",
        annotation_font_size=10,
    )

    fig.update_layout(
        title=dict(text="What-If Impact: Global Nuclear Capacity", font=dict(size=16)),
        xaxis=dict(title="Year", range=[2024, 2050]),
        yaxis=dict(title="Capacity (GW)"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        template=CHART_TEMPLATE,
        font=FONT,
        margin=dict(l=60, r=20, t=60, b=40),
        height=420,
    )
    return fig
