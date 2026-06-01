# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

An interactive nuclear capacity projection dashboard (2024–2050) built on unit-level reactor data from IAEA PRIS. It models four scenarios (decline/conservative/base/optimistic) plus user-defined custom scenarios, and includes a Claude-powered chat assistant that can answer analytical questions and propose scenario changes in plain English.

## Running the app

```bash
# Run locally (from repo root)
streamlit run dashboard/app.py

# Install dependencies
pip install -e ".[dev]"        # installs model + dashboard packages + pytest
pip install -r requirements.txt  # alternative: direct install

# Run all tests
pytest

# Run a single test file
pytest tests/test_projection.py -v

# Re-run projections after DB or scenario changes
python ingest.py --project

# Full ingest + project + validate (after data source update)
python ingest.py --validate

# Reset DB from scratch (destructive)
python ingest.py --reset --project
```

**Local secrets:** Streamlit looks for secrets in `dashboard/.streamlit/secrets.toml` (the script's directory), NOT the repo root `.streamlit/`. Both files should exist with the same content:
```toml
APP_PASSWORD = "..."
ANTHROPIC_API_KEY = "sk-ant-..."
```

## Architecture overview

### Layer separation

```
config.py                          # DB path, region map, model constants
    ↓
model/                             # Pure Python — no Streamlit, no UI
  schema.py                        # SQLite DDL (single source of truth)
  pipeline.py                      # Pipeline probability weighting
  retirement.py                    # Retirement date logic, extension policies
  projection.py                    # Core projection engine
  state.py                         # ProjectionState: overrides applied once here
  scenarios.py                     # Scenario presets and extension fractions
  api.py                           # ONLY entry point for dashboard → model calls
    ↓
dashboard/                         # Streamlit — imports from model.api only
  app.py                           # Entry point: password gate, tab wiring
  cache.py                         # All @st.cache_data wrappers (5-min TTL)
  levers.py                        # Sidebar + Scenario Lab UI, ScenarioState
  tab_renderers.py                 # One render_tab*() function per tab
  charts.py                        # All Plotly chart builders
  helpers.py                       # Pure helpers: override dict, levers_match_preset
  overrides.py                     # WhatIfOverrides dataclass
  claude_context.py                # LEVER_SCHEMA, system prompt, reactor context builder
  claude_action.py                 # Action agent: NL → lever/reactor JSON actions
  claude_analyst.py                # Analytical agent: 14 read-only tools + tool loop
  claude_router.py                 # Haiku classifier + keyword heuristic → route
  claude_chat.py                   # Thin shim re-exporting public API for levers.py
```

The **dashboard never queries SQLite directly** — all data access goes through `model/api.py`.

### Database (`data/nuclear.db`)

Single SQLite file. Key tables:

| Table | Purpose |
|---|---|
| `reactors` | Master registry; every unit with ~35 fields including `retirement_date_used`, `retirement_tier`, `pipeline_probability`, `reactor_type`, `nsss_supplier` |
| `retirement_rules` | Per-country licence life rules (baseline, extension increments, max life) |
| `scenarios` | The 4 named scenarios plus user-defined ones with macro parameters |
| `scenario_regional_params` | Per-region overrides within a scenario |
| `projections` | Pre-computed output: one row per (scenario, region, year) |
| `historical_capacity` | Observed GW by region, 2005–2024 |
| `benchmarks` | IEA WEO 2024 and IAEA RDS-1 2025 reference lines |

### Projection model

**Two-phase hybrid model** (`model/projection.py`):

- **Bottom-up (2024–2040):** Unit-level simulation. Each year = Σ(operating units not yet retired) + Σ(pipeline units × probability with online_year ≤ year). Retirement dates are computed by `model/retirement.py` based on scenario extension policy.
- **Top-down (2041–2050):** Parametric. Starts from the 2040 bottom-up anchor and applies `post2040_global_new_build_gw_per_year` as a scenario lever, minus continuing unit-level retirements.
- **Continuity:** The 2040 transition year is guaranteed identical between phases by design.

**Retirement tier system** (critical for correctness):
- **Tier 1** — declared shutdown date; never modified by scenario logic
- **Tier 2** — derived from country licence expiry rules; adjusted by extension policy
- **Tier 3** — design life assumption; most adjustable

**De facto extension baseline:** Many reactors have a Tier 2 date in the past (still operating proves they already received extensions). `retirement.py:advance_past_baseline()` advances the nominal date forward until it clears `BASELINE_YEAR = 2024` before applying scenario policy. This prevents scenarios from incorrectly retiring already-extended reactors.

**Extension policies:** `AcceleratedRetirement` / `CurrentPolicy` / `ExtendedOperations`. `CurrentPolicy` uses a deterministic hash of `reactor_id` for reproducible probabilistic outcomes (same reactor always gets the same answer for a given country fraction).

**What-if overrides** apply exactly once in `model/state.py:_apply_overrides()`. The `ProjectionState` dataclass pre-bakes all inputs; projection and tech functions receive a ready-to-use state and never re-apply overrides.

**Pre-computed vs. live:** The four built-in scenarios are pre-computed into the `projections` table at ingest time. Custom scenarios (lever changes) run `write_and_run_custom_scenario()` on demand and write to a session-scoped `custom_<uuid>` scenario ID. What-if projections (reactor overrides + synthetic builds) run fully in-memory via `run_what_if_all_regions()` and are never written to the DB.

### Claude integration

**Two-agent system** routing on every message:

1. **Router** (`claude_router.py`): Haiku classifier (primary, ~300ms) → keyword heuristic fallback → dispatches to action or analyst agent.

2. **Action agent** (`claude_action.py`): Converts NL to structured JSON (`set_lever` / `reactor_override` / `synthetic_build` actions). Returns proposed changes; user must click **✅ Apply changes** to commit them. Has two safety guards: `extension_policy` blocked unless message mentions retirement/life extension keywords; global pipeline rate increases blocked when user is asking to reduce builds.

3. **Analytical agent** (`claude_analyst.py`): Read-only tool-calling loop with 14 tools backed by SQLite queries. Never modifies state. Uses `_ACTIVE_SENTINEL = "__active__"` to route tools to session state (`wi_proj_all` → `_custom_projection`) instead of DB when a custom scenario exists.

**Active scenario access:** `_active_proj_dict()` returns the live projection dict from `st.session_state`. This is only populated after the user clicks Apply (which triggers `_chat_auto_submit = True` → `render_lab_panel()` auto-submits the form → `run_what_if_all_regions()` runs → `wi_proj_all` is set). Before Apply is clicked, no active projection exists and the analyst falls back to base.

**Reactor context for action agent:** `_reactor_context_text()` in `claude_context.py` always includes all pipeline reactors (for override IDs) and conditionally includes operating fleet only when a specific geography is detected in the message. To resolve pronouns across turns (e.g. "what if they all shut down?" after asking about US reactors), `call_claude()` builds `_geo_context` by combining the current message with the last 6 user messages from chat history — used for geography detection only, not passed to the API.

**Analyst scope rule:** The analyst system prompt explicitly instructs the model to answer ONLY the final user message; prior history is for pronoun/reference resolution only. Without this, the analyst re-answers stale action-agent exchanges that appear as "unanswered" user messages in the history.

### Session state conventions

Key session state keys used across the dashboard:

| Key | Set by | Read by |
|---|---|---|
| `wi_overrides` | levers.py (apply btn) | helpers.py, api.py |
| `wi_synthetic` | levers.py (apply btn) | helpers.py, api.py |
| `wi_proj_all` | tab_renderers.py (lab submit) | app.py, claude_analyst.py |
| `_custom_projection` | tab_renderers.py (lab submit) | app.py, claude_analyst.py |
| `_chat_auto_submit` | levers.py (chat apply btn) | levers.py (render_lab_panel) |
| `preset_selector` | levers.py sidebar | throughout |
| `lv_*` | levers.py sliders/selects | model layer via ScenarioState |

### Tests

Tests use a fully in-memory SQLite DB built from `model/schema.DDL` with deterministic fixtures (see `tests/conftest.py`). The `db` fixture provides a connection; `db_path` provides a temp file path for functions that open the DB internally (like `build_projection_state()`). Tests are isolated — each function gets a fresh DB.

## Known gotchas

- **`st.secrets` crash:** `st.secrets.get()` raises `StreamlitSecretNotFoundError` at the *file* level when no `secrets.toml` exists — even with a default argument. The call in `app.py` is wrapped in try/except.
- **Streamlit secrets path:** Streamlit resolves secrets relative to the *script's* directory (`dashboard/`), not the repo root. The secrets file must exist at `dashboard/.streamlit/secrets.toml` (gitignored). The repo root `.streamlit/secrets.toml` is ignored when running `streamlit run dashboard/app.py`.
- **`wi_proj_all` vs `_custom_projection`:** `wi_proj_all` is only set when reactor overrides or synthetic builds exist (`bool(wi_dict)` check in `tab_renderers.py`). Lever-only changes populate `_custom_projection` only. `_active_proj_dict()` checks both in priority order.
- **Reactor context column names:** The `get_reactor_options()` query in `model/api.py` must include `reactor_type`, `reactor_model`, `is_smr`, `nsss_supplier`, and `pipeline_probability` — these are used by the action agent's context builder. If columns are missing, Claude will hallucinate technology tags from training data.
- **Retirement date column:** The model uses `retirement_date_used` (the resolved base-case date). Do not use `shutdown_date`, `actual_shutdown_date`, or `planned_shutdown_date_declared` for scenario calculations — they have different semantics.
- **`pipeline_probability=0` cancellation:** Setting `pipeline_probability=0` on a pipeline reactor must reduce `expected_capacity_mw` to 0. It must NOT promote the reactor into the operating fleet via the `extra_ids` path in `projection.py` (which only triggers for `_FLEET_OVERRIDE_FIELDS = {"status", "capacity_mw", "restart_date"}`).
