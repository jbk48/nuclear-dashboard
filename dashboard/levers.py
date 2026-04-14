"""
levers.py — Scenario lever UI components for the nuclear capacity dashboard.

Simplified panel with:
  - Scenario preset selector
  - Life Extensions (global policy only)
  - Pipeline Realization: separate Large Reactor + SMR toggles (High/Medium/Low)
  - Construction delay adder: discrete steps 0/1/2/3/5 years
  - Post-2040 New Build: global rate + China carve-out
  - Display Options
  - Scenario Comparison
  - Geography Filter (checkbox list)

An "Update" button (via st.form) gates all recalculations.
"""
import streamlit as st
from dataclasses import dataclass, field
from typing import Optional
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import REGIONS


# ── Scenario state dataclass ───────────────────────────────────────────────

@dataclass
class ScenarioState:
    """Captures all lever positions. Matches scenarios table fields."""
    scenario_id: str = "base"
    name: str = "Base"

    # Life extension
    extension_policy_global: str = "CurrentPolicy"

    # Pipeline realization — large reactors
    large_pipeline_preset: str = "medium"
    pipeline_uc_rate: float = 1.00
    pipeline_planned_rate: float = 1.00
    pipeline_proposed_rate: float = 0.00

    # Pipeline realization — SMRs
    smr_pipeline_preset: str = "medium"
    smr_uc_rate: float = 1.00
    smr_planned_rate: float = 1.00
    smr_proposed_rate: float = 0.00

    # Construction delay (discrete years, separate from realization preset)
    construction_delay_adder: float = 0.0

    # SMR deployment
    smr_accel_start_year: int = 2040
    smr_accel_gw_per_year: float = 0.0
    smr_post2040_share: float = 0.20

    # Post-2040 growth
    post2040_global_growth_gw: float = 30.3   # base scenario default (IEA STEPS)
    china_post2040_gw: float = 8.6

    # Benchmark display toggles
    show_iaea_low: bool = True
    show_iaea_high: bool = True
    show_iea_steps: bool = True
    show_iea_aps: bool = False
    show_iea_low_nuclear: bool = True

    # Display toggles
    show_historical: bool = True
    show_transition_marker: bool = True
    compare_scenarios: list = field(default_factory=list)

    # Geography filter
    selected_regions: list = field(default_factory=lambda: list(REGIONS))


# ── Pipeline presets ────────────────────────────────────────────────────────
# H/M/L definitions apply equally to large reactors and SMRs:
#   High   = 100% UC / 100% Planned / 100% Proposed
#   Medium = 100% UC / 100% Planned /   0% Proposed
#   Low    = 100% UC /   0% Planned /   0% Proposed

LARGE_PIPELINE_PRESETS = {
    "high": {
        "label": "High",
        "uc_rate": 1.00, "planned_rate": 1.00, "proposed_rate": 1.00,
        "desc": "All UC, Planned & Proposed complete.",
    },
    "medium": {
        "label": "Medium",
        "uc_rate": 1.00, "planned_rate": 1.00, "proposed_rate": 0.00,
        "desc": "UC & Planned complete; Proposed excluded.",
    },
    "low": {
        "label": "Low",
        "uc_rate": 1.00, "planned_rate": 0.00, "proposed_rate": 0.00,
        "desc": "Only Under Construction units complete.",
    },
}

SMR_PIPELINE_PRESETS = {
    "high": {
        "label": "High",
        "uc_rate": 1.00, "planned_rate": 1.00, "proposed_rate": 1.00,
        "desc": "All SMR UC, Planned & Proposed complete.",
    },
    "medium": {
        "label": "Medium",
        "uc_rate": 1.00, "planned_rate": 1.00, "proposed_rate": 0.00,
        "desc": "SMR UC & Planned complete; Proposed excluded.",
    },
    "low": {
        "label": "Low",
        "uc_rate": 1.00, "planned_rate": 0.00, "proposed_rate": 0.00,
        "desc": "Only SMR Under Construction units complete.",
    },
}

DELAY_OPTIONS = [0, 1, 2, 3, 4, 5]

SCENARIO_TO_PIPELINE = {
    "decline":      "low",
    "conservative": "low",
    "base":         "medium",
    "optimistic":   "high",
    "custom":       "medium",
}

EXTENSION_POLICIES = ["AcceleratedRetirement", "CurrentPolicy", "ExtendedOperations"]
EXTENSION_POLICY_LABELS = {
    "AcceleratedRetirement": "No New Extensions — existing approved licenses honored; no further extension rounds granted",
    "CurrentPolicy":         "Current Policy — approved licenses & stated national policy (country-specific rates)",
    "ExtendedOperations":    "Extended Operations — maximum regulatory life (US: 80yr · Japan: 80yr · France: 60yr)",
}

PRESET_DEFAULTS = {
    "decline": {
        "extension_policy_global": "AcceleratedRetirement",
        "large_pipeline_preset": "low",
        "smr_pipeline_preset": "low",
        "construction_delay_adder": 3,
        "smr_accel_start_year": 2040,
        "smr_accel_gw_per_year": 0.0,
        "smr_post2040_share": 10,   # stored as integer percent
        "post2040_global_growth_gw": 10.0,  # calibrated to IEA Low Nuclear ~250 GW by 2050
        "china_post2040_gw": 2.8,
    },
    "conservative": {
        "extension_policy_global": "CurrentPolicy",
        "large_pipeline_preset": "low",
        "smr_pipeline_preset": "medium",
        "construction_delay_adder": 2,
        "smr_accel_start_year": 2040,
        "smr_accel_gw_per_year": 0.0,
        "smr_post2040_share": 15,
        "post2040_global_growth_gw": 28.7,  # calibrated to IAEA Low ~561 GW by 2050
        "china_post2040_gw": 8.2,
    },
    "base": {
        "extension_policy_global": "CurrentPolicy",
        "large_pipeline_preset": "medium",
        "smr_pipeline_preset": "medium",
        "construction_delay_adder": 0,
        "smr_accel_start_year": 2040,
        "smr_accel_gw_per_year": 0.0,
        "smr_post2040_share": 20,
        "post2040_global_growth_gw": 28.1,  # calibrated to IEA STEPS ~647 GW by 2050
        "china_post2040_gw": 8.0,
    },
    "optimistic": {
        "extension_policy_global": "ExtendedOperations",
        "large_pipeline_preset": "high",
        "smr_pipeline_preset": "high",
        "construction_delay_adder": 0,
        "smr_accel_start_year": 2040,
        "smr_accel_gw_per_year": 0.0,
        "smr_post2040_share": 40,
        "post2040_global_growth_gw": 53.6,  # calibrated to IAEA High ~992 GW by 2050
        "china_post2040_gw": 15.3,
    },
}


def _ext_label(policy: str) -> str:
    return EXTENSION_POLICY_LABELS.get(policy, policy)


# ── Scenario save / load helpers ──────────────────────────────────────────

# Maps human-readable JSON keys → (session_state_key, type)
_LEVER_MAP = [
    ("scenario_preset",          "scenario_preset",       str),
    ("extension_policy_global",  "lv_ext_policy",         str),
    ("large_uc_pct",             "lv_large_uc_pct",       int),
    ("large_planned_pct",        "lv_large_plan_pct",     int),
    ("large_proposed_pct",       "lv_large_prop_pct",     int),
    ("smr_uc_pct",               "lv_smr_uc_pct",         int),
    ("smr_planned_pct",          "lv_smr_plan_pct",       int),
    ("smr_proposed_pct",         "lv_smr_prop_pct",       int),
    ("construction_delay_years", "lv_delay",              float),
    ("smr_accel_start_year",     "lv_smr_accel_start",    int),
    ("smr_accel_gw_per_year",    "lv_smr_accel_rate",     float),
    ("smr_post2040_share_pct",   "lv_smr_share_pct",      int),
    ("post2040_global_gw",       "lv_post2040_gw",        float),
    ("china_post2040_gw",        "lv_china_gw",           float),
    ("show_iaea_low",            "lv_iaea_low",           bool),
    ("show_iaea_high",           "lv_iaea_high",          bool),
    ("show_iea_steps",           "lv_iea_steps",          bool),
    ("show_iea_aps",             "lv_iea_aps",            bool),
    ("show_iea_low_nuclear",     "lv_iea_low_nuc",        bool),
    ("show_historical",          "lv_show_hist",          bool),
    ("show_transition_marker",   "lv_show_trans",         bool),
    ("compare_scenarios",        "lv_compare",            list),
]


def build_save_payload(state: ScenarioState) -> str:
    """Serialize current ScenarioState to a JSON string for download."""
    # Build geo dict from session state
    geo = {r: st.session_state.get(f"geo_{r}", True) for r in REGIONS}
    payload = {
        "version": 1,
        "saved_at": str(date.today()),
        "scenario_name": state.name,
        "levers": {
            "scenario_preset":          state.scenario_id,
            "extension_policy_global":  state.extension_policy_global,
            "large_uc_pct":             int(round(state.pipeline_uc_rate * 100)),
            "large_planned_pct":        int(round(state.pipeline_planned_rate * 100)),
            "large_proposed_pct":       int(round(state.pipeline_proposed_rate * 100)),
            "smr_uc_pct":               int(round(state.smr_uc_rate * 100)),
            "smr_planned_pct":          int(round(state.smr_planned_rate * 100)),
            "smr_proposed_pct":         int(round(state.smr_proposed_rate * 100)),
            "construction_delay_years": state.construction_delay_adder,
            "smr_accel_start_year":     state.smr_accel_start_year,
            "smr_accel_gw_per_year":    state.smr_accel_gw_per_year,
            "smr_post2040_share_pct":   int(round(state.smr_post2040_share * 100)),
            "post2040_global_gw":       state.post2040_global_growth_gw,
            "china_post2040_gw":        state.china_post2040_gw,
            "show_iaea_low":            state.show_iaea_low,
            "show_iaea_high":           state.show_iaea_high,
            "show_iea_steps":           state.show_iea_steps,
            "show_iea_aps":             state.show_iea_aps,
            "show_iea_low_nuclear":     state.show_iea_low_nuclear,
            "show_historical":          state.show_historical,
            "show_transition_marker":   state.show_transition_marker,
            "compare_scenarios":        state.compare_scenarios,
        },
        "geography": geo,
    }
    return json.dumps(payload, indent=2)


def apply_loaded_scenario(payload: dict) -> None:
    """Write a loaded JSON payload back into Streamlit session state."""
    levers = payload.get("levers", {})
    geo    = payload.get("geography", {})

    # Map JSON keys → session state
    key_to_ss = {jk: (ss_key, typ) for jk, ss_key, typ in _LEVER_MAP}
    for jk, value in levers.items():
        if jk not in key_to_ss:
            continue
        ss_key, typ = key_to_ss[jk]
        if ss_key == "scenario_preset":
            st.session_state["scenario_preset"] = str(value)
        else:
            try:
                st.session_state[ss_key] = typ(value) if not isinstance(value, list) else value
            except (ValueError, TypeError):
                pass  # skip malformed values silently

    # Restore geography
    for region, checked in geo.items():
        st.session_state[f"geo_{region}"] = bool(checked)


# ── Main lever panel renderer ──────────────────────────────────────────────

def render_lever_panel() -> tuple[ScenarioState, bool]:
    """
    Render the full lever panel in the Streamlit sidebar.
    All lever widgets are inside a st.form; changes only apply when
    the user clicks "Update Projections".
    Returns (ScenarioState, lever_updated) where lever_updated=True
    means the form was just submitted.
    """
    st.sidebar.title("Scenario Levers")
    st.sidebar.markdown("---")

    # ── Preset selector (outside form — changes defaults but doesn't re-project) ──
    preset_options = ["decline", "conservative", "base", "optimistic", "custom"]
    preset_labels = {
        "decline":      "📉 Decline",
        "conservative": "📊 Conservative",
        "base":         "📈 Base",
        "optimistic":   "🚀 Optimistic",
        "custom":       "⚙️ Custom",
    }
    preset = st.sidebar.selectbox(
        "Load scenario preset",
        options=preset_options,
        format_func=lambda x: preset_labels[x],
        index=2,  # default: base
        key="preset_selector",
        help="Loading a preset fills the levers below. Click 'Update' to apply.",
    )

    # When preset changes, push defaults into session state so form widgets pick them up
    if "last_preset" not in st.session_state:
        st.session_state.last_preset = preset
    if st.session_state.last_preset != preset and preset != "custom":
        defaults = PRESET_DEFAULTS.get(preset, PRESET_DEFAULTS["base"])
        st.session_state["lv_ext_policy"]         = defaults["extension_policy_global"]
        st.session_state["lv_delay"]              = defaults["construction_delay_adder"]
        st.session_state["lv_smr_accel_start"]    = defaults["smr_accel_start_year"]
        st.session_state["lv_smr_accel_rate"]     = defaults["smr_accel_gw_per_year"]
        st.session_state["lv_smr_share_pct"]      = defaults["smr_post2040_share"]
        st.session_state["lv_post2040_gw"]        = defaults["post2040_global_growth_gw"]
        st.session_state["lv_china_gw"]           = defaults["china_post2040_gw"]
        # Push per-stage rates to match the preset
        lpp_ = LARGE_PIPELINE_PRESETS[defaults["large_pipeline_preset"]]
        spp_ = SMR_PIPELINE_PRESETS[defaults["smr_pipeline_preset"]]
        st.session_state["lv_large_uc_pct"]   = int(lpp_["uc_rate"]       * 100)
        st.session_state["lv_large_plan_pct"] = int(lpp_["planned_rate"]  * 100)
        st.session_state["lv_large_prop_pct"] = int(lpp_["proposed_rate"] * 100)
        st.session_state["lv_smr_uc_pct"]     = int(spp_["uc_rate"]       * 100)
        st.session_state["lv_smr_plan_pct"]   = int(spp_["planned_rate"]  * 100)
        st.session_state["lv_smr_prop_pct"]   = int(spp_["proposed_rate"] * 100)
        st.session_state.last_preset = preset

    defaults = PRESET_DEFAULTS.get(preset if preset != "custom" else "base", PRESET_DEFAULTS["base"])
    scenario_id = preset if preset != "custom" else "base"

    # ── Lever form ─────────────────────────────────────────────────────────
    with st.sidebar.form("lever_form", clear_on_submit=False):

        # ── Group 1: Life Extensions ───────────────────────────────────────
        st.markdown("### ⏳ Life Extensions")
        ext_policy = st.selectbox(
            "Extension policy (global)",
            options=EXTENSION_POLICIES,
            format_func=_ext_label,
            index=EXTENSION_POLICIES.index(
                st.session_state.get("lv_ext_policy", defaults["extension_policy_global"])
            ),
            key="lv_ext_policy",
            help=(
                "Historical Rate: ~50% of eligible units receive one extension increment. "
                "Maximum Allowed: all units extended to regulatory maximum life."
            ),
        )

        st.markdown("---")

        # ── Group 2: Pipeline Realization ─────────────────────────────────
        st.markdown("### 🔩 Pipeline Realization")

        # Derive preset defaults from current scenario
        lpp = LARGE_PIPELINE_PRESETS[defaults["large_pipeline_preset"]]

        st.caption("**Large Reactors** — realization rate per pipeline stage:")

        # Per-stage rate inputs (editable — preset fills defaults; can be overridden)
        _luc_def  = int(st.session_state.get("lv_large_uc_pct",   int(lpp["uc_rate"]      * 100)))
        _lpl_def  = int(st.session_state.get("lv_large_plan_pct", int(lpp["planned_rate"] * 100)))
        _lpr_def  = int(st.session_state.get("lv_large_prop_pct", int(lpp["proposed_rate"]* 100)))
        _lc1, _lc2, _lc3 = st.columns(3)
        with _lc1:
            large_uc_pct  = st.number_input("UC %",       min_value=0, max_value=100, step=5,
                                             value=_luc_def, key="lv_large_uc_pct",
                                             help="Under Construction realization rate")
        with _lc2:
            large_plan_pct = st.number_input("Planned %", min_value=0, max_value=100, step=5,
                                             value=_lpl_def, key="lv_large_plan_pct",
                                             help="Planned realization rate")
        with _lc3:
            large_prop_pct = st.number_input("Proposed %",min_value=0, max_value=100, step=5,
                                             value=_lpr_def, key="lv_large_prop_pct",
                                             help="Proposed realization rate")

        st.markdown(" ")

        # Derive SMR preset defaults from current scenario
        spp = SMR_PIPELINE_PRESETS[defaults["smr_pipeline_preset"]]

        st.caption("**SMR Pipeline** — realization rate per pipeline stage:")

        # Per-stage rate inputs — SMR
        _suc_def  = int(st.session_state.get("lv_smr_uc_pct",   int(spp["uc_rate"]      * 100)))
        _spl_def  = int(st.session_state.get("lv_smr_plan_pct", int(spp["planned_rate"] * 100)))
        _spr_def  = int(st.session_state.get("lv_smr_prop_pct", int(spp["proposed_rate"]* 100)))
        _sc1, _sc2, _sc3 = st.columns(3)
        with _sc1:
            smr_uc_pct   = st.number_input("UC %",       min_value=0, max_value=100, step=5,
                                            value=_suc_def, key="lv_smr_uc_pct",
                                            help="SMR Under Construction realization rate")
        with _sc2:
            smr_plan_pct = st.number_input("Planned %",  min_value=0, max_value=100, step=5,
                                            value=_spl_def, key="lv_smr_plan_pct",
                                            help="SMR Planned realization rate")
        with _sc3:
            smr_prop_pct = st.number_input("Proposed %", min_value=0, max_value=100, step=5,
                                            value=_spr_def, key="lv_smr_prop_pct",
                                            help="SMR Proposed realization rate")

        st.markdown(" ")

        # Construction delay (discrete steps)
        delay_default = st.session_state.get("lv_delay", defaults["construction_delay_adder"])
        # Snap to nearest valid option
        if delay_default not in DELAY_OPTIONS:
            delay_default = min(DELAY_OPTIONS, key=lambda x: abs(x - delay_default))

        st.caption("**Construction delay adder**")
        delay_sel = st.select_slider(
            "Delay adder (years)",
            options=DELAY_OPTIONS,
            value=delay_default,
            key="lv_delay",
            label_visibility="collapsed",
            help="Additional years added to all pipeline reactor expected online dates.",
        )
        st.caption(f"+{delay_sel} yr applied to all pipeline stages")

        st.markdown("---")

        # ── Group 3: SMR Deployment ────────────────────────────────────────
        st.markdown("### 🔬 SMR Deployment")

        # Pre-2040 acceleration
        st.caption("**Pre-2040 acceleration** (beyond announced pipeline)")
        accel_start_default = st.session_state.get("lv_smr_accel_start",
                                                    defaults["smr_accel_start_year"])
        accel_rate_default  = float(st.session_state.get("lv_smr_accel_rate",
                                                          defaults["smr_accel_gw_per_year"]))

        smr_accel_start = st.select_slider(
            "Additional SMR deployment start year",
            options=list(range(2028, 2041)),
            value=int(accel_start_default),
            key="lv_smr_accel_start",
            label_visibility="collapsed",
        )
        smr_accel_rate = st.slider(
            "Additional SMR rate (GW/yr)",
            min_value=0.0, max_value=10.0, step=0.5,
            value=accel_rate_default,
            key="lv_smr_accel_rate",
            label_visibility="collapsed",
            help="Global GW/yr of additional SMR capacity beyond the announced pipeline.",
        )
        if smr_accel_rate > 0:
            years_of_accel = max(1, 2040 - smr_accel_start + 1)
            total_extra = smr_accel_rate * years_of_accel
            st.caption(
                f"+{smr_accel_rate:.1f} GW/yr from {smr_accel_start} "
                f"→ ~{total_extra:.0f} GW additional by 2040"
            )
        else:
            st.caption("No pre-2040 acceleration — announced pipeline only")

        st.markdown(" ")

        # Post-2040 SMR share
        st.caption("**Post-2040 SMR share of new build**")
        smr_share_default = int(st.session_state.get("lv_smr_share_pct",
                                                      defaults["smr_post2040_share"]))
        smr_share_pct = st.slider(
            "SMR share of post-2040 new build (%)",
            min_value=0, max_value=60, step=5,
            value=smr_share_default,
            key="lv_smr_share_pct",
            label_visibility="collapsed",
        )
        smr_post2040_share_val = smr_share_pct / 100.0
        # Read current post2040 GW from session for the live breakdown label
        _p40_gw = float(st.session_state.get("lv_post2040_gw",
                                              defaults["post2040_global_growth_gw"]))
        _smr_gw   = _p40_gw * smr_post2040_share_val
        _large_gw = _p40_gw - _smr_gw
        st.caption(
            f"→ **{_smr_gw:.1f} GW/yr SMR** · {_large_gw:.1f} GW/yr large reactor "
            f"(at {_p40_gw:.0f} GW/yr global)"
        )

        st.markdown("---")

        # ── Group 4: Post-2040 New Build ───────────────────────────────────
        st.markdown("### 🏗️ Post-Transition New Build")
        post2040_growth = st.slider(
            "Global new build rate (GW/yr)",
            min_value=0.0, max_value=80.0, step=1.0,
            value=float(st.session_state.get("lv_post2040_gw", defaults["post2040_global_growth_gw"])),
            key="lv_post2040_gw",
            help=(
                "Gross new capacity added globally per year from 2040 onward. "
                "Decline≈10 · Conservative≈29 · Base≈28 · Optimistic≈54 GW/yr"
            ),
        )
        st.caption(
            "Decline≈10 · Conservative≈29 · Base≈28 · Optimistic≈54 GW/yr  ·  calibrated to IEA/IAEA 2050 benchmarks  ·  "
            "⚠️ Conservative > Base reflects cross-compensation: Conservative's tighter pre-2040 pipeline "
            "requires a higher post-2040 rate to reach its IAEA Low 2050 anchor."
        )

        china_gw_default = float(st.session_state.get("lv_china_gw", defaults["china_post2040_gw"]))
        china_gw_default = min(china_gw_default, post2040_growth)
        china_gw = st.slider(
            "China share (GW/yr)",
            min_value=0.0, max_value=min(25.0, post2040_growth),
            step=1.0,
            value=china_gw_default,
            key="lv_china_gw",
            help="China's portion of the global new build rate. Subtractive from the global total.",
        )
        row_gw = max(0.0, post2040_growth - china_gw)
        st.caption(f"Rest-of-world: **{row_gw:.0f} GW/yr** (Global {post2040_growth:.0f} − China {china_gw:.0f})")

        st.markdown("---")

        # ── Group 4: Display Options ───────────────────────────────────────
        with st.expander("🎨 Display Options", expanded=False):
            show_historical = st.toggle("Show historical data (2005–2024)", value=True, key="lv_show_hist")
            show_transition = st.toggle("Show transition year marker", value=True, key="lv_show_trans")
            st.markdown("**Benchmark overlays**")
            show_iaea_low  = st.toggle("IAEA Low Case (561 GW)",      value=True,  key="lv_iaea_low")
            show_iaea_high = st.toggle("IAEA High Case (992 GW)",     value=True,  key="lv_iaea_high")
            show_iea_steps = st.toggle("IEA STEPS (647 GW)",          value=True,  key="lv_iea_steps")
            show_iea_aps   = st.toggle("IEA APS (874 GW)",            value=False, key="lv_iea_aps")
            show_iea_low_nuc = st.toggle("IEA Low Nuclear (~250 GW)", value=True,  key="lv_iea_low_nuc")

        st.markdown("---")

        # ── Group 5: Scenario Comparison ──────────────────────────────────
        all_scenarios = ["decline", "conservative", "base", "optimistic"]
        compare_opts = [s for s in all_scenarios if s != scenario_id]
        compare_labels = {
            "decline": "Decline", "conservative": "Conservative",
            "base": "Base", "optimistic": "Optimistic",
        }
        compare = st.multiselect(
            "Compare with scenarios",
            options=compare_opts,
            format_func=lambda x: compare_labels[x],
            default=[],
            key="lv_compare",
            help="Overlay additional scenario lines on the global projection chart.",
        )

        # ── Update button ──────────────────────────────────────────────────
        submitted = st.form_submit_button(
            "▶ Update Projections",
            use_container_width=True,
            type="primary",
        )

    # ── Geography Filter (outside form — immediate filter, no re-project needed) ──
    st.sidebar.markdown("---")
    st.sidebar.markdown("### 🌍 Geography Filter")
    with st.sidebar.expander("Select regions to display", expanded=False):
        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("All", key="geo_all"):
                for r in REGIONS:
                    st.session_state[f"geo_{r}"] = True
        with col_b:
            if st.button("None", key="geo_none"):
                for r in REGIONS:
                    st.session_state[f"geo_{r}"] = False

        selected_regions = []
        for region in REGIONS:
            checked = st.checkbox(
                region,
                value=st.session_state.get(f"geo_{region}", True),
                key=f"geo_{region}",
            )
            if checked:
                selected_regions.append(region)

    if not selected_regions:
        selected_regions = list(REGIONS)  # safety fallback

    # ── Read final lever values from session state ─────────────────────────
    lp_data = LARGE_PIPELINE_PRESETS[defaults["large_pipeline_preset"]]
    sp_data = SMR_PIPELINE_PRESETS[defaults["smr_pipeline_preset"]]

    delay_val = st.session_state.get("lv_delay", defaults["construction_delay_adder"])
    if delay_val not in DELAY_OPTIONS:
        delay_val = min(DELAY_OPTIONS, key=lambda x: abs(x - delay_val))

    smr_share_final = int(st.session_state.get("lv_smr_share_pct", defaults["smr_post2040_share"]))

    # Per-stage rates: read from editable inputs (fall back to preset if not yet set)
    final_large_uc   = st.session_state.get("lv_large_uc_pct",   int(lp_data["uc_rate"]       * 100)) / 100.0
    final_large_plan = st.session_state.get("lv_large_plan_pct", int(lp_data["planned_rate"]  * 100)) / 100.0
    final_large_prop = st.session_state.get("lv_large_prop_pct", int(lp_data["proposed_rate"] * 100)) / 100.0
    final_smr_uc     = st.session_state.get("lv_smr_uc_pct",     int(sp_data["uc_rate"]       * 100)) / 100.0
    final_smr_plan   = st.session_state.get("lv_smr_plan_pct",   int(sp_data["planned_rate"]  * 100)) / 100.0
    final_smr_prop   = st.session_state.get("lv_smr_prop_pct",   int(sp_data["proposed_rate"] * 100)) / 100.0

    state = ScenarioState(
        scenario_id=scenario_id,
        name=preset.title(),
        extension_policy_global=st.session_state.get("lv_ext_policy", defaults["extension_policy_global"]),
        large_pipeline_preset=defaults["large_pipeline_preset"],
        pipeline_uc_rate=final_large_uc,
        pipeline_planned_rate=final_large_plan,
        pipeline_proposed_rate=final_large_prop,
        smr_pipeline_preset=defaults["smr_pipeline_preset"],
        smr_uc_rate=final_smr_uc,
        smr_planned_rate=final_smr_plan,
        smr_proposed_rate=final_smr_prop,
        construction_delay_adder=float(delay_val),
        smr_accel_start_year=int(st.session_state.get("lv_smr_accel_start", defaults["smr_accel_start_year"])),
        smr_accel_gw_per_year=float(st.session_state.get("lv_smr_accel_rate", defaults["smr_accel_gw_per_year"])),
        smr_post2040_share=smr_share_final / 100.0,
        post2040_global_growth_gw=st.session_state.get("lv_post2040_gw", defaults["post2040_global_growth_gw"]),
        china_post2040_gw=st.session_state.get("lv_china_gw", defaults["china_post2040_gw"]),
        show_iaea_low=st.session_state.get("lv_iaea_low", True),
        show_iaea_high=st.session_state.get("lv_iaea_high", True),
        show_iea_steps=st.session_state.get("lv_iea_steps", True),
        show_iea_aps=st.session_state.get("lv_iea_aps", False),
        show_iea_low_nuclear=st.session_state.get("lv_iea_low_nuc", True),
        show_historical=st.session_state.get("lv_show_hist", True),
        show_transition_marker=st.session_state.get("lv_show_trans", True),
        compare_scenarios=st.session_state.get("lv_compare", []),
        selected_regions=selected_regions,
    )

    # ── Save / Load scenario ───────────────────────────────────────────────
    st.sidebar.markdown("---")
    with st.sidebar.expander("💾 Save / Load Scenario", expanded=False):
        # Save
        safe_name = state.name.lower().replace(" ", "_")
        filename = f"nuclear_scenario_{safe_name}_{date.today()}.json"
        st.download_button(
            label="⬇ Download current scenario (.json)",
            data=build_save_payload(state),
            file_name=filename,
            mime="application/json",
            use_container_width=True,
            help="Downloads all lever positions as a JSON file you can reload later.",
        )

        st.caption(
            "The downloaded file captures every lever position — extension policy, "
            "pipeline rates, delay adder, post-2040 growth, SMR settings, display "
            "toggles, and geography filter."
        )

        st.markdown("**Load a saved scenario**")
        uploaded = st.file_uploader(
            "Upload a scenario JSON file",
            type=["json"],
            key="scenario_upload",
            label_visibility="collapsed",
        )
        if uploaded is not None:
            try:
                payload = json.loads(uploaded.read())
                if payload.get("version") != 1:
                    st.error("Unrecognised file format (version mismatch).")
                else:
                    apply_loaded_scenario(payload)
                    loaded_name = payload.get("scenario_name", "saved scenario")
                    saved_at = payload.get("saved_at", "unknown date")
                    st.success(
                        f"Loaded **{loaded_name}** (saved {saved_at}). "
                        "Click **▶ Update Projections** to apply."
                    )
                    st.rerun()
            except (json.JSONDecodeError, KeyError) as e:
                st.error(f"Could not parse scenario file: {e}")

    return state, submitted
