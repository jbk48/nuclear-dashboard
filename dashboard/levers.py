"""
levers.py — Scenario lever UI components for the nuclear capacity dashboard.

Provides two panel renderers:
  - render_sidebar_panel(): minimal sidebar (preset, geo, display opts, save/load)
  - render_lab_panel(): full macro levers + what-if builder inside Scenario Lab tab

An older render_lever_panel() alias is kept for backward compatibility.
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

DELAY_OPTIONS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]

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


# ── Helper: build ScenarioState from session state ─────────────────────────

def _build_state_from_session(preset: str, defaults: dict, scenario_id: str,
                               selected_regions: list) -> ScenarioState:
    """Read current lever values from session state and build a ScenarioState."""
    lp_data = LARGE_PIPELINE_PRESETS[defaults["large_pipeline_preset"]]
    sp_data = SMR_PIPELINE_PRESETS[defaults["smr_pipeline_preset"]]

    delay_val = st.session_state.get("lv_delay", defaults["construction_delay_adder"])
    if delay_val not in DELAY_OPTIONS:
        delay_val = min(DELAY_OPTIONS, key=lambda x: abs(x - delay_val))

    smr_share_final = int(st.session_state.get("lv_smr_share_pct", defaults["smr_post2040_share"]))

    final_large_uc   = st.session_state.get("lv_large_uc_pct",   int(lp_data["uc_rate"]       * 100)) / 100.0
    final_large_plan = st.session_state.get("lv_large_plan_pct", int(lp_data["planned_rate"]  * 100)) / 100.0
    final_large_prop = st.session_state.get("lv_large_prop_pct", int(lp_data["proposed_rate"] * 100)) / 100.0
    final_smr_uc     = st.session_state.get("lv_smr_uc_pct",     int(sp_data["uc_rate"]       * 100)) / 100.0
    final_smr_plan   = st.session_state.get("lv_smr_plan_pct",   int(sp_data["planned_rate"]  * 100)) / 100.0
    final_smr_prop   = st.session_state.get("lv_smr_prop_pct",   int(sp_data["proposed_rate"] * 100)) / 100.0

    return ScenarioState(
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


# ── Sidebar panel ──────────────────────────────────────────────────────────

def render_sidebar_panel() -> tuple:
    """
    Render the minimal sidebar panel.
    Contains: title, preset selector, Scenario Lab status line,
    geography filter, display options, scenario comparison, save/load.
    Returns (ScenarioState, False) — no submitted flag from the sidebar.
    """
    # ── Apply any pending scenario load BEFORE any widget is instantiated ────
    # The file uploader stores the raw payload here on the first rerun so we
    # can write to session-state keys without hitting the
    # "cannot be modified after widget is instantiated" error.
    if "_pending_scenario_load" in st.session_state:
        _pending = st.session_state.pop("_pending_scenario_load")
        try:
            apply_loaded_scenario(_pending)
            st.session_state["_scenario_load_success"] = (
                _pending.get("scenario_name", "saved scenario"),
                _pending.get("saved_at", "unknown date"),
            )
        except Exception as _load_err:
            st.session_state["_scenario_load_error"] = str(_load_err)

    st.sidebar.title("⚛️ Nuclear Capacity Dashboard")
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
        help="Loading a preset fills the levers in the Scenario Lab tab. Click '▶ Apply Scenario' to apply.",
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
        # Clear all what-if state so overrides/projections from the old preset
        # don't silently carry over to the new one.
        st.session_state["wi_overrides"]  = []
        st.session_state["wi_synthetic"]  = []
        st.session_state["wi_result"]     = None
        st.session_state["wi_proj_all"]   = None
        st.session_state.pop("_custom_projection", None)
        st.session_state.pop("_custom_for_scenario", None)
        st.session_state.last_preset = preset

    defaults = PRESET_DEFAULTS.get(preset if preset != "custom" else "base", PRESET_DEFAULTS["base"])
    scenario_id = preset if preset != "custom" else "base"

    # ── Scenario Lab status line ───────────────────────────────────────────
    _wi_proj_all = st.session_state.get("wi_proj_all")
    _wi_overrides = st.session_state.get("wi_overrides", [])
    _wi_synthetic = st.session_state.get("wi_synthetic", [])
    if _wi_proj_all is not None and (_wi_overrides or _wi_synthetic):
        n = len(_wi_overrides)
        m = len(_wi_synthetic)
        st.sidebar.info(f"🔬 Scenario Lab active — {n} override(s) + {m} batch(es)")

    # ── Geography Filter ───────────────────────────────────────────────────
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

    # ── Display Options ────────────────────────────────────────────────────
    st.sidebar.markdown("---")
    with st.sidebar.expander("🎨 Display Options", expanded=False):
        st.toggle("Show historical data (2005–2024)", value=True, key="lv_show_hist")
        st.toggle("Show transition year marker", value=True, key="lv_show_trans")
        st.markdown("**Benchmark overlays**")
        st.toggle("IAEA Low Case (561 GW)",      value=True,  key="lv_iaea_low")
        st.toggle("IAEA High Case (992 GW)",     value=True,  key="lv_iaea_high")
        st.toggle("IEA STEPS (647 GW)",          value=True,  key="lv_iea_steps")
        st.toggle("IEA APS (874 GW)",            value=False, key="lv_iea_aps")
        st.toggle("IEA Low Nuclear (~250 GW)",   value=True,  key="lv_iea_low_nuc")

    # ── Scenario Comparison ────────────────────────────────────────────────
    st.sidebar.markdown("---")
    all_scenarios = ["decline", "conservative", "base", "optimistic"]
    compare_opts = all_scenarios
    compare_labels = {
        "decline": "Decline", "conservative": "Conservative",
        "base": "Base", "optimistic": "Optimistic",
    }
    st.sidebar.multiselect(
        "Compare with scenarios",
        options=compare_opts,
        format_func=lambda x: compare_labels[x],
        default=[],
        key="lv_compare",
        help="Overlay additional scenario lines on the global projection chart.",
    )

    # ── Save / Load scenario ───────────────────────────────────────────────
    # Build a temporary state to serialize for save
    state = _build_state_from_session(preset, defaults, scenario_id, selected_regions)

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
            # Use name+size as a fingerprint so we only trigger the apply-rerun
            # cycle ONCE per distinct file.  Without this guard, the file uploader
            # keeps the file in session state across reruns, causing an infinite
            # rerun loop (apply → file still present → store pending → rerun …).
            _fp = f"{uploaded.name}|{uploaded.size}"
            if st.session_state.get("_last_processed_upload") != _fp:
                try:
                    payload = json.loads(uploaded.read())
                    if payload.get("version") != 1:
                        st.error("Unrecognised file format (version mismatch).")
                    else:
                        # Mark this file as processed BEFORE the rerun so the
                        # guard above holds on the next pass.
                        st.session_state["_last_processed_upload"] = _fp
                        st.session_state["_pending_scenario_load"] = payload
                        st.rerun()
                except (json.JSONDecodeError, KeyError) as e:
                    st.error(f"Could not parse scenario file: {e}")

        # Show deferred success / error messages written at the top of this function
        if "_scenario_load_success" in st.session_state:
            _sname, _sat = st.session_state.pop("_scenario_load_success")
            st.success(
                f"Loaded **{_sname}** (saved {_sat}). "
                "Go to **Scenario Lab** and click **▶ Apply Scenario** to apply."
            )
        if "_scenario_load_error" in st.session_state:
            st.error(f"Could not apply scenario: {st.session_state.pop('_scenario_load_error')}")

    return state, False


# ── Scenario Lab panel (renders inside the Scenario Lab tab) ───────────────

def render_lab_panel(scenario_id: str, defaults: dict) -> bool:
    """
    Render the full macro lever panel + what-if builder inside the Scenario Lab tab.
    Override builder / synthetic builds / remove buttons are OUTSIDE the form
    (they call st.rerun). The macro levers are inside st.form("lab_form").
    Returns submitted (bool) — True when the form submit button was clicked.
    """
    # Check for auto-submit triggered by Claude chat apply
    _auto_submit = st.session_state.pop("_chat_auto_submit", False)

    # ── Initialise session state ──────────────────────────────────────────
    if "wi_overrides" not in st.session_state:
        st.session_state["wi_overrides"] = []
    if "wi_synthetic" not in st.session_state:
        st.session_state["wi_synthetic"] = []
    if "wi_result" not in st.session_state:
        st.session_state["wi_result"] = None

    # ── Load reactor options (cached via app.py — reuse if already loaded) ──
    # We import lazily to avoid circular imports; the cache is app-level anyway.
    try:
        from model.api import get_reactor_options
        import streamlit as _st

        @_st.cache_data(ttl=300)
        def _load_reactor_options_lab():
            return get_reactor_options()

        reactor_opts = _load_reactor_options_lab()
        reactor_opts["label"] = (
            reactor_opts["name"] + " — " +
            reactor_opts["country"] + " · " +
            reactor_opts["status"] + " · " +
            (reactor_opts["net_capacity_mw"] / 1000).round(2).astype(str) + " GW"
        )
        label_to_id = dict(zip(reactor_opts["label"], reactor_opts["reactor_id"]))
        id_to_row   = reactor_opts.set_index("reactor_id").to_dict("index")
        _reactor_opts_ok = True
    except Exception:
        _reactor_opts_ok = False
        label_to_id = {}
        id_to_row = {}

    # ── Claude chat section (top of Lab — primary interaction point) ───────
    st.markdown("### 💬 Ask Claude")
    st.caption(
        "Describe a scenario in plain English and Claude will translate it into model changes. "
        "You can then fine-tune further using the manual controls below."
    )
    with st.expander("💡 What can I ask?", expanded=False):
        st.markdown("""
**Macro levers** (affect all reactors globally):
- *"Switch to extended operations policy — maximum licence life for all reactors"*
- *"Set pipeline realization to high — include all proposed reactors"*
- *"Add a 3-year construction delay to all pipeline projects"*
- *"Increase post-2040 global new build to 50 GW/yr"*

**Synthetic new builds** (hypothetical capacity not in the database):
- *"Add 5 GW/yr of SMRs in North America starting 2030 for 10 years"*
- *"Model 3 × 1 GW large reactors per year in Southeast Asia from 2035"*

**Reactor-level overrides** (specific units — search by country or name):
- *"Retire all French reactors by 2035"*
- *"Extend the operating life of all US reactors to 2060"*
- *"Restart the Japanese long-term shutdown reactors in 2027"*

**Economic / indirect scenarios** (Claude will interpret and propose adjustments):
- *"What if SMR overnight costs drop to $5,000/kW by 2032?"*
- *"What if there's a major nuclear accident in 2028?"*
- *"What if carbon prices reach $200/tonne by 2035?"*

After Claude proposes changes, click **✅ Apply changes** — all 6 chart tabs will update. You can then make further manual adjustments below.
""")

    # Init chat state
    if "chat_history" not in st.session_state:
        st.session_state["chat_history"] = []
    if "chat_pending_response" not in st.session_state:
        st.session_state["chat_pending_response"] = None

    # Display past history
    for _msg in st.session_state["chat_history"]:
        with st.chat_message(_msg["role"]):
            st.markdown(_msg["content"])

    # Display pending Claude response (awaiting user confirmation)
    _pending = st.session_state.get("chat_pending_response")
    if _pending:
        with st.chat_message("assistant"):
            st.markdown(_pending["message"])
            _actions = _pending.get("actions", [])
            if _actions:
                st.markdown("**Proposed changes:**")
                for _a in _actions:
                    if _a["type"] == "set_lever":
                        st.markdown(f"- Set **{_a['lever']}** → `{_a['value']}`")
                    elif _a["type"] == "synthetic_build":
                        st.markdown(
                            f"- Add synthetic build: {_a.get('per_year', 1)} × "
                            f"{_a.get('capacity_mw', 0):.0f} MW/yr in **{_a.get('region')}** "
                            f"from {_a.get('start_year')} for {_a.get('n_years')} yr"
                        )
                    elif _a["type"] == "reactor_override":
                        st.markdown(
                            f"- Override reactor `{_a.get('reactor_id')}`: "
                            f"**{_a.get('field')}** → `{_a.get('value')}`"
                        )
                _cap1, _cap2, _cap3 = st.columns([2, 2, 4])
                with _cap1:
                    if st.button("✅ Apply changes", type="primary", key="chat_apply_btn"):
                        from dashboard.claude_chat import apply_claude_actions
                        _rdf = reactor_opts if _reactor_opts_ok else None
                        # Retrieve the last user message for safety-filter context
                        _last_user_msg = ""
                        for _hm in reversed(st.session_state.get("chat_history", [])):
                            if _hm.get("role") == "user":
                                _last_user_msg = _hm.get("content", "")
                                break
                        _new_ov, _new_sy, _lv_upd, _warns = apply_claude_actions(
                            _actions, _rdf, user_message=_last_user_msg
                        )
                        for _k, _v in _lv_upd.items():
                            st.session_state[_k] = _v
                        # Merge: if a new override targets the same (reactor_id, field)
                        # as an existing one, the new value wins (drop the old entry).
                        _existing_ov = st.session_state.get("wi_overrides", [])
                        if _new_ov:
                            _new_keys = {(o["reactor_id"], o["field"]) for o in _new_ov}
                            _existing_ov = [
                                o for o in _existing_ov
                                if (o["reactor_id"], o["field"]) not in _new_keys
                            ]
                        st.session_state["wi_overrides"] = _existing_ov + _new_ov
                        st.session_state["wi_synthetic"] = (
                            st.session_state.get("wi_synthetic", []) + _new_sy
                        )
                        if _warns:
                            st.session_state["_chat_apply_warnings"] = _warns
                        st.session_state["chat_history"].append(
                            {"role": "assistant", "content": _pending["message"]}
                        )
                        st.session_state["chat_pending_response"] = None
                        st.session_state["wi_result"] = None
                        st.session_state["_chat_auto_submit"] = True
                        st.rerun()
                with _cap2:
                    if st.button("✕ Dismiss", key="chat_dismiss_btn"):
                        st.session_state["chat_history"].append(
                            {"role": "assistant", "content": _pending["message"]}
                        )
                        st.session_state["chat_pending_response"] = None
                        st.rerun()
            else:
                if st.button("OK", key="chat_ok_btn"):
                    st.session_state["chat_history"].append(
                        {"role": "assistant", "content": _pending["message"]}
                    )
                    st.session_state["chat_pending_response"] = None
                    st.rerun()

    # Debug expander — shows raw Claude JSON (helps diagnose parsing failures)
    if st.session_state.get("_chat_last_raw"):
        with st.expander("🔍 Debug: raw Claude response", expanded=False):
            st.code(st.session_state["_chat_last_raw"], language="json")

    # Safety warnings from apply_claude_actions (blocked actions)
    _apply_warnings = st.session_state.pop("_chat_apply_warnings", None)
    if _apply_warnings:
        with st.expander("⚠️ Actions blocked by safety filter", expanded=True):
            st.caption(
                "The following changes Claude proposed were automatically blocked to prevent "
                "unintended side-effects on the projection:"
            )
            for _w in _apply_warnings:
                st.warning(_w)

    # Chat input box
    _chat_input = st.chat_input(
        "e.g. 'Retire all US reactors by 2035' or 'Add 5 GW/yr SMRs in Asia from 2030'"
    )
    if _chat_input:
        _api_key = ""
        try:
            _api_key = st.secrets.get("ANTHROPIC_API_KEY", "")
        except Exception:
            pass

        if not _api_key:
            st.error(
                "No Anthropic API key configured. "
                "Add `ANTHROPIC_API_KEY` to `.streamlit/secrets.toml` to use Claude."
            )
        else:
            st.session_state["chat_history"].append({"role": "user", "content": _chat_input})
            with st.spinner("Claude is thinking…"):
                from dashboard.claude_chat import call_claude
                _history_for_api = st.session_state["chat_history"][:-1]
                _response = call_claude(
                    user_message  = _chat_input,
                    reactor_df    = reactor_opts if _reactor_opts_ok else None,
                    api_key       = _api_key,
                    chat_history  = _history_for_api,
                )
            import json as _json
            st.session_state["_chat_last_raw"] = _json.dumps(_response, indent=2)
            st.session_state["chat_pending_response"] = _response
            st.rerun()

    # Clear chat + all customisations button
    _has_any_custom = (
        st.session_state.get("chat_history")
        or st.session_state.get("chat_pending_response")
        or st.session_state.get("wi_overrides")
        or st.session_state.get("wi_synthetic")
        or st.session_state.get("_custom_projection")
    )
    if _has_any_custom:
        _clr1, _clr2 = st.columns([3, 2])
        with _clr1:
            if st.button("🗑 Clear all customisations & return to base scenario", key="lab_clear_all_btn"):
                # Clear what-if overrides and projections
                st.session_state["chat_history"] = []
                st.session_state["chat_pending_response"] = None
                st.session_state["wi_overrides"] = []
                st.session_state["wi_synthetic"] = []
                st.session_state["wi_result"] = None
                st.session_state["wi_proj_all"] = None
                st.session_state.pop("_custom_projection", None)
                st.session_state.pop("_custom_for_scenario", None)
                st.session_state.pop("_chat_last_raw", None)
                # Also reset all lever session-state keys so form widgets
                # revert to the current preset's defaults on next render
                for _lk in [
                    "lv_ext_policy",
                    "lv_large_uc_pct", "lv_large_plan_pct", "lv_large_prop_pct",
                    "lv_smr_uc_pct",   "lv_smr_plan_pct",   "lv_smr_prop_pct",
                    "lv_delay",
                    "lv_smr_accel_start", "lv_smr_accel_rate",
                    "lv_smr_share_pct",
                    "lv_post2040_gw", "lv_china_gw",
                ]:
                    st.session_state.pop(_lk, None)
                st.rerun()

    st.markdown("---")

    _PIPELINE_STATUSES = {"UnderConstruction", "Planned", "Proposed"}
    _FLEET_FIELDS    = ["retirement_year", "restart_date", "status", "capacity_mw"]
    _PIPELINE_FIELDS = ["expected_online_year", "pipeline_probability", "capacity_mw"]

    # ── 1. Override builder expander (OUTSIDE form) ───────────────────────
    with st.expander("➕ Add a reactor override", expanded=True):
        if not _reactor_opts_ok:
            st.warning("Reactor options could not be loaded.")
        else:
            wi_col1, wi_col2, wi_col3 = st.columns([3, 2, 2])
            with wi_col1:
                selected_label = st.selectbox(
                    "Reactor", options=[""] + list(label_to_id.keys()),
                    key="wi_reactor_sel", label_visibility="collapsed",
                    placeholder="Search by name, country, or status…",
                    help="All reactors in the database are searchable — type a country name, reactor name, or status to filter.",
                )
            _sel_status = (id_to_row[label_to_id[selected_label]]["status"]
                           if selected_label else None)
            _field_opts = (_PIPELINE_FIELDS if _sel_status in _PIPELINE_STATUSES
                           else _FLEET_FIELDS)
            with wi_col2:
                override_field = st.selectbox(
                    "Field", options=_field_opts,
                    key="wi_field_sel", label_visibility="collapsed",
                )
            _placeholders = {
                "retirement_year":      "e.g. 2045",
                "restart_date":         "e.g. 2028-06",
                "status":               "e.g. Restarted",
                "capacity_mw":          "e.g. 900",
                "expected_online_year": "e.g. 2032",
                "pipeline_probability": "0.0 – 1.0",
            }
            with wi_col3:
                override_value_raw = st.text_input(
                    "Value", key="wi_value_inp", label_visibility="collapsed",
                    placeholder=_placeholders.get(override_field, "value"),
                )

            if st.button("➕ Add override", type="primary", key="wi_add_btn"):
                if not selected_label:
                    st.warning("Select a reactor first.")
                elif not override_value_raw.strip():
                    st.warning("Enter a value.")
                else:
                    rid = label_to_id[selected_label]
                    try:
                        if override_field in ("retirement_year", "expected_online_year"):
                            val = int(override_value_raw.strip())
                        elif override_field in ("capacity_mw", "pipeline_probability"):
                            val = float(override_value_raw.strip())
                        else:
                            val = override_value_raw.strip()
                        existing = st.session_state["wi_overrides"]
                        existing = [o for o in existing
                                    if not (o["reactor_id"] == rid and o["field"] == override_field)]
                        existing.append({
                            "reactor_id":   rid,
                            "reactor_name": id_to_row[rid]["name"],
                            "country":      id_to_row[rid]["country"],
                            "field":        override_field,
                            "value":        val,
                        })
                        st.session_state["wi_overrides"] = existing
                        st.session_state["wi_result"] = None
                        st.rerun()
                    except ValueError:
                        st.error(f"Invalid value for {override_field}: '{override_value_raw}'")

    # ── 2. Synthetic builds expander (OUTSIDE form) ───────────────────────
    with st.expander("🏗 Add a synthetic new-build batch", expanded=False):
        st.caption("Model hypothetical reactors not in the database — e.g. 'what if the US built 3 × 300 MW reactors per year starting 2033?'")
        sb_c1, sb_c2, sb_c3, sb_c4, sb_c5 = st.columns([2, 1, 1, 1, 1])
        with sb_c1:
            sb_region = st.selectbox("Region", options=REGIONS, key="wi_sb_region",
                                     label_visibility="collapsed")
        with sb_c2:
            sb_cap = st.number_input("MW each", min_value=10, max_value=10000,
                                     value=300, step=50, key="wi_sb_cap",
                                     label_visibility="collapsed")
        with sb_c3:
            sb_per_yr = st.number_input("Per year", min_value=1, max_value=50,
                                        value=3, step=1, key="wi_sb_per_yr",
                                        label_visibility="collapsed")
        with sb_c4:
            sb_start = st.number_input("Start year", min_value=2025, max_value=2049,
                                       value=2030, step=1, key="wi_sb_start",
                                       label_visibility="collapsed")
        with sb_c5:
            sb_nyrs = st.number_input("For N years", min_value=1, max_value=20,
                                      value=5, step=1, key="wi_sb_nyrs",
                                      label_visibility="collapsed")
        st.caption(
            f"Region · MW each · Per year · Start year · For N years  →  adds "
            f"**{int(sb_per_yr) * int(sb_cap) / 1000:.1f} GW/yr** in **{sb_region}** "
            f"for {int(sb_nyrs)} year(s) from {int(sb_start)}"
        )
        if st.button("➕ Add batch", key="wi_sb_add", type="primary"):
            label = f"{int(sb_per_yr)} × {int(sb_cap)} MW/yr in {sb_region} from {int(sb_start)} ({int(sb_nyrs)} yr)"
            st.session_state["wi_synthetic"].append({
                "label":      label,
                "region":     sb_region,
                "capacity_mw": float(sb_cap),
                "per_year":   int(sb_per_yr),
                "start_year": int(sb_start),
                "n_years":    int(sb_nyrs),
            })
            st.session_state["wi_result"] = None
            st.rerun()

    # ── 3. Current overrides/batches display with remove buttons (OUTSIDE form) ──
    overrides = st.session_state["wi_overrides"]
    synthetic = st.session_state["wi_synthetic"]
    has_any   = overrides or synthetic

    if has_any:
        if overrides:
            st.markdown("**Reactor overrides**")
            for i, ov in enumerate(overrides):
                c1, c2, c3, c4 = st.columns([3, 2, 2, 1])
                c1.write(f"**{ov['reactor_name']}** ({ov['country']})")
                c2.write(ov["field"].replace("_", " ").title())
                c3.write(str(ov["value"]))
                if c4.button("✕", key=f"wi_rm_{i}"):
                    st.session_state["wi_overrides"].pop(i)
                    st.session_state["wi_result"] = None
                    st.rerun()

        if synthetic:
            st.markdown("**Synthetic new builds**")
            for i, sb in enumerate(synthetic):
                sc1, sc2 = st.columns([6, 1])
                sc1.write(sb["label"])
                if sc2.button("✕", key=f"wi_sb_rm_{i}"):
                    st.session_state["wi_synthetic"].pop(i)
                    st.session_state["wi_result"] = None
                    st.rerun()

        st.markdown("")
        if st.button("🗑 Clear all overrides & batches", key="wi_clear_btn",
                     use_container_width=False):
            st.session_state["wi_overrides"] = []
            st.session_state["wi_synthetic"] = []
            st.session_state["wi_result"]    = None
            st.session_state["wi_proj_all"]  = None
            st.rerun()

    st.markdown("---")

    # ── 4. Macro lever form ────────────────────────────────────────────────
    with st.form("lab_form", clear_on_submit=False):

        # Read-only override summary at top of form
        n_ov = len(st.session_state.get("wi_overrides", []))
        n_sb = len(st.session_state.get("wi_synthetic", []))
        if n_ov or n_sb:
            st.info(f"🔬 {n_ov} reactor override(s) + {n_sb} synthetic batch(es) queued — will be applied on submit.")

        # ── Group 1: Life Extensions ───────────────────────────────────────
        st.markdown("### ⏳ Life Extensions")
        st.selectbox(
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

        lpp = LARGE_PIPELINE_PRESETS[defaults["large_pipeline_preset"]]

        st.caption("**Large Reactors** — realization rate per pipeline stage:")

        _luc_def  = int(st.session_state.get("lv_large_uc_pct",   int(lpp["uc_rate"]      * 100)))
        _lpl_def  = int(st.session_state.get("lv_large_plan_pct", int(lpp["planned_rate"] * 100)))
        _lpr_def  = int(st.session_state.get("lv_large_prop_pct", int(lpp["proposed_rate"]* 100)))
        _lc1, _lc2, _lc3 = st.columns(3)
        with _lc1:
            st.number_input("UC %",       min_value=0, max_value=100, step=5,
                            value=_luc_def, key="lv_large_uc_pct",
                            help="Under Construction realization rate")
        with _lc2:
            st.number_input("Planned %", min_value=0, max_value=100, step=5,
                            value=_lpl_def, key="lv_large_plan_pct",
                            help="Planned realization rate")
        with _lc3:
            st.number_input("Proposed %", min_value=0, max_value=100, step=5,
                            value=_lpr_def, key="lv_large_prop_pct",
                            help="Proposed realization rate")

        st.markdown(" ")

        spp = SMR_PIPELINE_PRESETS[defaults["smr_pipeline_preset"]]

        st.caption("**SMR Pipeline** — realization rate per pipeline stage:")

        _suc_def  = int(st.session_state.get("lv_smr_uc_pct",   int(spp["uc_rate"]      * 100)))
        _spl_def  = int(st.session_state.get("lv_smr_plan_pct", int(spp["planned_rate"] * 100)))
        _spr_def  = int(st.session_state.get("lv_smr_prop_pct", int(spp["proposed_rate"]* 100)))
        _sc1, _sc2, _sc3 = st.columns(3)
        with _sc1:
            st.number_input("UC %",       min_value=0, max_value=100, step=5,
                            value=_suc_def, key="lv_smr_uc_pct",
                            help="SMR Under Construction realization rate")
        with _sc2:
            st.number_input("Planned %",  min_value=0, max_value=100, step=5,
                            value=_spl_def, key="lv_smr_plan_pct",
                            help="SMR Planned realization rate")
        with _sc3:
            st.number_input("Proposed %", min_value=0, max_value=100, step=5,
                            value=_spr_def, key="lv_smr_prop_pct",
                            help="SMR Proposed realization rate")

        st.markdown(" ")

        delay_default = st.session_state.get("lv_delay", defaults["construction_delay_adder"])
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

        # ── Apply Scenario button ──────────────────────────────────────────
        submitted = st.form_submit_button(
            "▶ Apply Scenario",
            use_container_width=True,
            type="primary",
        )

    # Auto-submit triggered by Claude chat "Apply these changes" button
    if _auto_submit:
        submitted = True

    return submitted


# ── Backward-compatible alias ──────────────────────────────────────────────

def render_lever_panel() -> tuple:
    """Backward-compatible alias for render_sidebar_panel()."""
    return render_sidebar_panel()
