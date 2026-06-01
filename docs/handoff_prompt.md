# Handoff Prompt — Nuclear Capacity Dashboard (Company Version)

*Paste this as the first message in a new Claude Code session when starting the company build.*

---

You are helping rebuild the Nuclear Capacity Dashboard as an internal company tool. A fully working reference implementation already exists — read it carefully before writing any new code.

**Reference repo:** `jbk48/nuclear-dashboard` on GitHub. The key documents to read first, in order:
1. `CLAUDE.md` — architecture, commands, session state conventions, known gotchas
2. `docs/architecture.html` — open in browser for a visual layer diagram
3. `docs/Nuclear_Capacity_Dashboard_Methodology.docx` — full methodology and user guide
4. `docs/scenario_calibration.md` — scenario definitions, lever values, IEA/IAEA anchors
5. `docs/data_contract.md` — exact field requirements the model needs from any data source

**What the reference implementation is:** An interactive nuclear capacity projection dashboard (2024–2050) built on unit-level reactor data. It models four scenarios (decline / conservative / base / optimistic) calibrated to IEA WEO 2024 and IAEA RDS-1 2025 benchmarks, plus user-defined custom scenarios. A Claude-powered chat assistant handles both analytical questions (read-only, tool-calling) and scenario modifications (NL → JSON lever/override actions).

**What's the same in the company version:** The architecture, the projection model logic, the two-agent Claude integration, the dashboard structure and tabs. Do not redesign any of these — they work and are well-tested.

**What's different:** The data source. The reference uses IAEA PRIS + WNA as its reactor registry. The company version will use [DESCRIBE COMPANY DATA SOURCE HERE]. Before writing any model code, map the company data fields to the requirements in `docs/data_contract.md` and confirm every required field has a source. Build the ingestion layer (`ingest.py`) last, after the model and dashboard are working against a synthetic or sample dataset.

**Architecture rules to preserve:**
- The dashboard must never query SQLite directly. All data access goes through `model/api.py` only.
- What-if overrides must be applied exactly once, in `model/state.py:_apply_overrides()`. Nowhere else.
- The `projections` table holds pre-computed scenario outputs. Custom scenarios write to a session-scoped `custom_<uuid>` scenario ID. What-if runs are entirely in-memory — never written to DB.
- `retirement_date_used` is the field the model uses for all retirement calculations. It must be populated for every operating reactor before projection runs.

**Two things most likely to break on a new data source:**
1. Reactor IDs — the model uses `reactor_id` as a stable key throughout (overrides, retirement schedule, pipeline). Whatever the company's ID scheme is, it must be consistent across all tables and never change between ingestion runs.
2. Retirement date resolution — the reference has a three-tier system (declared → licence expiry → design life). If the company data has a single retirement date field, map it to `retirement_date_used` directly and set `retirement_tier = 1` for all rows to disable the extension policy machinery until it can be properly calibrated.

**Claude integration note:** The two-agent system (action agent + analytical agent) in `dashboard/claude_*` files is ready to use as-is. The main thing to recalibrate is the system prompt in `claude_context.py` — update the scenario descriptions, region names, and benchmark references to match the company version. Do not change the routing logic or the `_ACTIVE_SENTINEL` session state pattern.

**Suggested build order:**
1. Set up the SQLite schema (`model/schema.py`) — keep it identical unless the company data requires new fields
2. Build a synthetic dataset matching the data contract (enough to run projections end-to-end)
3. Confirm the projection engine runs and produces sensible numbers on synthetic data
4. Wire up the dashboard against synthetic data — all tabs should render
5. Build the real ingestion layer (`ingest.py`) against the company data source
6. Calibrate the four scenarios against the company's chosen benchmarks
7. Integrate and test the Claude agents

**Run the tests** (`pytest`) before and after every significant model change. The test suite in `tests/` uses an in-memory SQLite DB and covers projection correctness, retirement logic, and override application. Add new tests when you modify any model function.
