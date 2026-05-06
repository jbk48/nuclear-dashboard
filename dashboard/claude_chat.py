"""
claude_chat.py — Claude NL chat integration for the Scenario Lab.

Translates natural-language what-if questions into structured lever/override actions.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import REGIONS

# ── Lever schema ────────────────────────────────────────────────────────────

LEVER_SCHEMA = {
    "extension_policy": {
        "ss_key": "lv_ext_policy",
        "type": "str",
        "options": ["AcceleratedRetirement", "CurrentPolicy", "ExtendedOperations"],
        "description": (
            "Global reactor retirement/extension policy. "
            "AcceleratedRetirement = no new extensions beyond currently approved licenses. "
            "CurrentPolicy = follow each country's stated national policy. "
            "ExtendedOperations = maximum regulatory life (US/Japan 80yr, France 60yr)."
        ),
    },
    "large_reactor_uc_pct": {
        "ss_key": "lv_large_uc_pct",
        "type": "int",
        "range": [0, 100],
        "description": "Probability (%) that Under Construction large reactors complete. Default 100.",
    },
    "large_reactor_planned_pct": {
        "ss_key": "lv_large_plan_pct",
        "type": "int",
        "range": [0, 100],
        "description": "Probability (%) that Planned large reactors get built. Default 100.",
    },
    "large_reactor_proposed_pct": {
        "ss_key": "lv_large_prop_pct",
        "type": "int",
        "range": [0, 100],
        "description": "Probability (%) that Proposed large reactors get built. Default 0.",
    },
    "smr_uc_pct": {
        "ss_key": "lv_smr_uc_pct",
        "type": "int",
        "range": [0, 100],
        "description": "Probability (%) that SMR Under Construction units complete. Default 100.",
    },
    "smr_planned_pct": {
        "ss_key": "lv_smr_plan_pct",
        "type": "int",
        "range": [0, 100],
        "description": "Probability (%) that SMR Planned units get built. Default 100.",
    },
    "smr_proposed_pct": {
        "ss_key": "lv_smr_prop_pct",
        "type": "int",
        "range": [0, 100],
        "description": "Probability (%) that SMR Proposed units get built. Default 0.",
    },
    "construction_delay_years": {
        "ss_key": "lv_delay",
        "type": "int",
        "options": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
        "description": "Additional years of delay added to all pipeline reactor expected online dates.",
    },
    "smr_acceleration_start_year": {
        "ss_key": "lv_smr_accel_start",
        "type": "int",
        "range": [2028, 2040],
        "description": "Year when pre-2040 SMR acceleration (beyond announced pipeline) begins.",
    },
    "smr_acceleration_gw_per_year": {
        "ss_key": "lv_smr_accel_rate",
        "type": "float",
        "range": [0.0, 10.0],
        "description": "Rate of pre-2040 SMR capacity acceleration (GW/yr beyond announced pipeline).",
    },
    "smr_post2040_share_pct": {
        "ss_key": "lv_smr_share_pct",
        "type": "int",
        "range": [0, 60],
        "description": "Percentage of post-2040 new build capacity that comes from SMRs.",
    },
    "post2040_global_new_build_gw_per_year": {
        "ss_key": "lv_post2040_gw",
        "type": "float",
        "range": [0.0, 80.0],
        "description": (
            "Global gross nuclear capacity added per year after 2040. "
            "Reference: Decline≈10 GW/yr, Conservative≈29, Base≈28, Optimistic≈54."
        ),
    },
    "china_post2040_gw_per_year": {
        "ss_key": "lv_china_gw",
        "type": "float",
        "range": [0.0, 25.0],
        "description": "China's share of post-2040 new build (GW/yr). Must not exceed global rate.",
    },
}

_SYSTEM_PROMPT = """\
You are an expert assistant for a nuclear capacity projection dashboard (2024–2050).

## Your Job
The user wants to explore a what-if nuclear scenario. Convert their natural language request \
into precise structured JSON actions that will update the projection model. Then explain clearly \
what you're proposing.

## Current Lever State
{current_state}

## Reactor Database Context
All pipeline reactors are listed below grouped by region. Use your own geographic
and political knowledge to determine which ones belong to the area the user is
describing — do not ask the user for reactor IDs; figure it out from the list.
{reactor_context}

## Available Levers (name → description | valid values)
{lever_schema}

## Available Regions (for synthetic builds and geographic context)
{regions}

Common geographic groupings used in the reactor context:
- "West / Western world" → United States, Canada & Mexico, France, United Kingdom, Rest of Western Europe
- "Europe" → France, United Kingdom, Rest of Western Europe, Eastern Europe
- "Asia" → China, East Asia, South Asia, Southeast Asia

## Response Format — return ONLY valid JSON, nothing else:
```json
{{
  "message": "Clear friendly explanation of what you're proposing (2-4 sentences). Be specific about numbers and what will change.",
  "actions": [
    // SET A MACRO LEVER:
    {{"type": "set_lever", "lever": "<lever_name_from_schema>", "value": <value>}},

    // ADD SYNTHETIC NEW BUILDS (hypothetical reactors not in the database):
    {{"type": "synthetic_build", "region": "<one of the available regions>", "capacity_mw": <mw_per_unit>, "per_year": <count_per_year_int>, "start_year": <2025_to_2049>, "n_years": <int>}},

    // OVERRIDE A SPECIFIC REACTOR (only use IDs from reactor context above):
    {{"type": "reactor_override", "reactor_id": "<id>", "field": "<field>", "value": <value>}}
    // reactor_override valid fields: retirement_year (int), capacity_mw (float),
    //   restart_date ("YYYY-MM"), status (str),
    //   expected_online_year (int, pipeline only), pipeline_probability (0.0–1.0, pipeline only)
  ]
}}
```

## Rules
- Return ONLY the JSON block. No preamble, no text after.
- If the request is unclear, set actions:[] and ask for clarification in message.
- Never invent reactor_ids — only use IDs from the reactor context above.
- Lever values must be within specified ranges/options.
- The model covers 2024–2050. Actions outside this range have no effect.

- **ONLY MAKE CHANGES THE USER ASKED FOR — NO EXCEPTIONS.** If the user asks about new-build \
  scenarios (starting, stopping, or changing construction), ONLY use reactor_override with \
  pipeline_probability and/or synthetic_build. Do NOT touch extension_policy or increase any \
  global pipeline rate as "compensation", context, or for any other reason not explicitly asked.

- **`extension_policy` is FORBIDDEN unless the user explicitly mentions: retirement, shutdown, \
  life extension, license renewal, or continued operations.** NEVER set extension_policy when \
  the user asks about new construction. Here is why: changing extension_policy to \
  ExtendedOperations causes an IMMEDIATE upward jump in capacity from 2024 because it expands \
  ALL operating reactor lifetimes starting NOW — this is completely unrelated to new-build \
  decisions and will produce a confusing, incorrect chart. Only touch extension_policy if the \
  user's question is specifically about how long existing reactors will run.

- **NEVER increase global pipeline rates (large_reactor_planned_pct, large_reactor_proposed_pct, \
  smr_planned_pct, smr_proposed_pct, post2040_global_new_build_gw_per_year, etc.) when the user \
  is asking to REDUCE or STOP builds.** These levers are GLOBAL and would incorrectly add \
  capacity in every region, including the ones the user wants to restrict. For regional \
  restrictions, use ONLY reactor_override with pipeline_probability=0.

- GLOBAL LEVERS CANNOT TARGET REGIONS: Pipeline realization rates and post-2040 growth apply \
  to ALL regions worldwide. If the user asks for a regional restriction (e.g. "the west stops \
  building"), do NOT use those levers as-is — they would incorrectly affect every region. \
  Instead: (a) use reactor_override with pipeline_probability=0 on the specific pipeline \
  reactors in that region (use the reactor context above), and (b) for the post-2040 period \
  follow the POST-2040 REGIONAL APPROXIMATION rule below to reduce the global rate \
  proportionally. If no reactor context is available for that region, set actions:[] and \
  explain the limitation.

- ECONOMIC/INDIRECT SCENARIOS: If the user asks about something that doesn't directly map \
  to a lever (e.g. "what if SMR costs drop to $5,000/kW?" or "what if there's a nuclear \
  accident?"), reason about the likely downstream effects and translate into concrete model \
  actions. Explain your reasoning. But still follow the ONLY MAKE CHANGES ASKED FOR rule — \
  only implement effects that logically follow from the stated scenario.

- POST-2040 REGIONAL APPROXIMATION: The post-2040 growth lever is global-only. When the \
  user asks a region to stop or reduce large reactor builds after 2040, use the \
  "Post-2040 Regional Build Shares" table in the reactor context — it shows each region's \
  estimated GW/yr contribution and the pre-calculated new global rate if they stop building. \
  Implement this as a set_lever action on post2040_global_new_build_gw_per_year using the \
  "new rate if stops" value. Also set reactor_override pipeline_probability=0 for any \
  pipeline reactors in that region with expected_online_year ≥ 2035. Always note in your \
  message that the post-2040 adjustment is an approximation based on current pipeline \
  proportions, and the user can refine it manually.

- Users may ask follow-up questions to refine the scenario — treat the conversation as \
  iterative. Each new message can add to or modify previous changes.

- Macro levers are GLOBAL — they cannot target individual countries. \
  To target a specific country, use reactor_override (for existing reactors) or \
  synthetic_build for their region.
- For the extension_policy lever: this affects ALL reactors globally.
"""


def _lever_schema_text() -> str:
    lines = []
    for name, spec in LEVER_SCHEMA.items():
        if "options" in spec:
            constraint = f"Options: {spec['options']}"
        else:
            r = spec["range"]
            constraint = f"Range: {r[0]}–{r[1]}"
        lines.append(f"- {name}: {spec['description']} | {constraint}")
    return "\n".join(lines)


def _current_state_text() -> str:
    import streamlit as st
    ss = st.session_state
    lines = []
    for name, spec in LEVER_SCHEMA.items():
        val = ss.get(spec["ss_key"], "(default)")
        lines.append(f"- {name}: {val}")
    wi_ov = ss.get("wi_overrides", [])
    wi_sy = ss.get("wi_synthetic", [])
    if wi_ov:
        lines.append(f"\nActive reactor overrides ({len(wi_ov)}):")
        for o in wi_ov:
            lines.append(f"  {o['reactor_name']} ({o['country']}): {o['field']} → {o['value']}")
    if wi_sy:
        lines.append(f"\nActive synthetic builds ({len(wi_sy)}):")
        for s in wi_sy:
            lines.append(f"  {s['label']}")
    return "\n".join(lines)


# Region aliases → list of DB region names they map to
# DB regions: United States | Canada & Mexico | France | United Kingdom |
#             Rest of Western Europe | Eastern Europe | Russia | China |
#             East Asia | South Asia | Southeast Asia | Emerging & Rest
_REGION_ALIASES: dict[str, list[str]] = {
    "west":             ["United States", "Canada & Mexico", "France",
                         "United Kingdom", "Rest of Western Europe"],
    "western":          ["United States", "Canada & Mexico", "France",
                         "United Kingdom", "Rest of Western Europe"],
    "western world":    ["United States", "Canada & Mexico", "France",
                         "United Kingdom", "Rest of Western Europe"],
    "western countries":["United States", "Canada & Mexico", "France",
                         "United Kingdom", "Rest of Western Europe"],
    "europe":           ["France", "United Kingdom", "Rest of Western Europe", "Eastern Europe"],
    "european":         ["France", "United Kingdom", "Rest of Western Europe", "Eastern Europe"],
    "western europe":   ["France", "United Kingdom", "Rest of Western Europe"],
    "eastern europe":   ["Eastern Europe"],
    "north america":    ["United States", "Canada & Mexico"],
    "asia":             ["China", "East Asia", "South Asia", "Southeast Asia"],
    "east asia":        ["China", "East Asia"],
    "south asia":       ["South Asia"],
    "southeast asia":   ["Southeast Asia"],
    "middle east":      ["Emerging & Rest"],
    "africa":           ["Emerging & Rest"],
    "global":           [],   # empty = don't filter; handled separately
}


def _resolve_regions(msg_lower: str, all_regions: list[str]) -> list[str]:
    """Return DB region names mentioned directly or via alias in msg_lower."""
    matched: set[str] = set()
    regions_lower = {r.lower(): r for r in all_regions}

    # 1. Direct match: region name appears as substring in message
    for r_low, r_orig in regions_lower.items():
        if r_low in msg_lower:
            matched.add(r_orig)

    # 2. Alias match
    for alias, target_regions in _REGION_ALIASES.items():
        if alias in msg_lower:
            for tr in target_regions:
                if tr in all_regions:
                    matched.add(tr)

    return list(matched)


# Common country aliases → canonical DB name keywords
_COUNTRY_ALIASES: dict[str, list[str]] = {
    "us":            ["united states"],
    "usa":           ["united states"],
    "america":       ["united states"],
    "united states": ["united states"],
    "uk":            ["united kingdom"],
    "britain":       ["united kingdom"],
    "england":       ["united kingdom"],
    "korea":         ["korea"],
    "south korea":   ["korea"],
    "uae":           ["arab emirates"],
    "emirates":      ["arab emirates"],
    "russia":        ["russia", "russian"],
    "iran":          ["iran", "islamic republic"],
    "czech":         ["czech"],
    "slovakia":      ["slovak"],
}


def _resolve_countries(msg_lower: str, all_countries: list[str]) -> list[str]:
    """
    Return the list of DB country names mentioned (directly or via alias) in msg_lower.
    Countries in the DB are stored in ALL CAPS (e.g. 'UNITED STATES OF AMERICA').
    """
    matched: set[str] = set()
    countries_lower = {c.lower(): c for c in all_countries}  # lower → original

    # 1. Direct substring match: country name appears in message
    for c_low, c_orig in countries_lower.items():
        if c_low in msg_lower:
            matched.add(c_orig)

    # 2. Alias match: a known alias keyword appears in the message as a word/phrase
    msg_words = msg_lower  # we check substrings, not word boundaries, intentionally
    for alias, keywords in _COUNTRY_ALIASES.items():
        # Check if the alias phrase appears in the message
        if alias in msg_words:
            for kw in keywords:
                for c_low, c_orig in countries_lower.items():
                    if kw in c_low:
                        matched.add(c_orig)

    # 3. Bidirectional: any word from the message (len≥4) appears in a country name
    for word in msg_lower.split():
        if len(word) >= 4:
            for c_low, c_orig in countries_lower.items():
                if word in c_low:
                    matched.add(c_orig)

    return list(matched)


def _reactor_context_text(reactor_df, user_message: str) -> str:
    """
    Build reactor context for Claude.

    Strategy:
    - PIPELINE reactors (UC / Planned / Proposed): always include ALL of them,
      grouped by region. Claude uses its own geographic reasoning to identify
      which ones apply — no aliases needed.
    - OPERATING fleet: only include when a specific country/region is detected
      in the query (retirement/extension scenarios). Otherwise show summary only.
    """
    if reactor_df is None or reactor_df.empty:
        return "Reactor database not available."

    _PIPELINE_STATUSES = {"UnderConstruction", "Planned", "Proposed"}
    pipeline_df  = reactor_df[reactor_df["status"].isin(_PIPELINE_STATUSES)].copy()
    operating_df = reactor_df[reactor_df["status"] == "Operating"].copy()

    lines: list[str] = []

    # ── 1. Full pipeline context (always included) ────────────────────────
    lines.append(
        f"## ALL Pipeline Reactors ({len(pipeline_df)} total — UC / Planned / Proposed)\n"
        "Use your geographic and political knowledge to determine which reactors\n"
        "belong to the region/bloc the user is asking about (e.g. 'the west',\n"
        "'NATO members', 'OECD', specific countries). IDs are required for overrides.\n"
        "expected_online_year shown where available."
    )

    # Group by region for readability
    if "region" in pipeline_df.columns:
        for region, grp in pipeline_df.groupby("region", sort=True):
            lines.append(f"\n--- {region} ({len(grp)} reactors) ---")
            for _, row in grp.iterrows():
                rid    = row.get("reactor_id", "")
                name   = row.get("name", "")
                ctry   = row.get("country", "")
                status = row.get("status", "")
                cap    = row.get("net_capacity_mw", 0)
                yr     = row.get("expected_online_year", "")
                is_smr = " [SMR]" if row.get("is_smr") else ""
                lines.append(
                    f"  id={rid} | {name} | {ctry} | {status}{is_smr}"
                    f" | {cap:.0f} MW" + (f" | online ~{yr}" if yr else "")
                )
    else:
        for _, row in pipeline_df.iterrows():
            rid    = row.get("reactor_id", "")
            name   = row.get("name", "")
            ctry   = row.get("country", "")
            status = row.get("status", "")
            cap    = row.get("net_capacity_mw", 0)
            yr     = row.get("expected_online_year", "")
            lines.append(
                f"  id={rid} | {name} | {ctry} | {status} | {cap:.0f} MW"
                + (f" | online ~{yr}" if yr else "")
            )

    # ── 2. Operating fleet — only when geography detected ─────────────────
    msg_lower    = user_message.lower()
    all_countries = operating_df["country"].dropna().unique().tolist() if "country" in operating_df.columns else []
    all_regions   = operating_df["region"].dropna().unique().tolist()  if "region"  in operating_df.columns else []

    mentioned_countries = _resolve_countries(msg_lower, all_countries)
    mentioned_regions   = _resolve_regions(msg_lower, all_regions)

    if mentioned_countries or mentioned_regions:
        mask = operating_df["country"].isin(mentioned_countries)
        if "region" in operating_df.columns:
            mask = mask | operating_df["region"].isin(mentioned_regions)
        op_filtered = operating_df[mask]

        lines.append(
            f"\n## Operating Fleet — {', '.join(mentioned_countries + mentioned_regions)}"
            f" ({len(op_filtered)} reactors, for retirement/extension overrides)"
        )
        for _, row in op_filtered.iterrows():
            rid    = row.get("reactor_id", "")
            name   = row.get("name", "")
            ctry   = row.get("country", "")
            cap    = row.get("net_capacity_mw", 0)
            yr     = row.get("retirement_year", "") or row.get("shutdown_date", "")
            lines.append(
                f"  id={rid} | {name} | {ctry} | Operating | {cap:.0f} MW"
                + (f" | retires ~{yr}" if yr else "")
            )
    else:
        lines.append(
            f"\n## Operating Fleet ({len(operating_df)} reactors)\n"
            "For retirement/extension overrides: mention a specific country or region\n"
            "and the operating reactor IDs for that area will be shown."
        )

    # ── 3. Post-2040 regional build share estimates ───────────────────────
    # Use large (non-SMR) pipeline MW by region as a proxy for each region's
    # share of the global post-2040 build rate.  This lets Claude propose a
    # proportional reduction to post2040_global_new_build_gw_per_year when the
    # user asks a specific region to stop building after 2040.
    try:
        import streamlit as _st_shares
        _p40_gw    = float(_st_shares.session_state.get("lv_post2040_gw", 28.1))
        _china_gw  = float(_st_shares.session_state.get("lv_china_gw",    8.0))
        _smr_pct   = float(_st_shares.session_state.get("lv_smr_share_pct", 20))
    except Exception:
        _p40_gw, _china_gw, _smr_pct = 28.1, 8.0, 20.0

    _smr_share    = _smr_pct / 100.0
    _row_gw       = max(0.0, _p40_gw - _china_gw)        # rest-of-world total
    _row_large_gw = _row_gw * (1.0 - _smr_share)         # large-reactor portion of RoW

    # Non-China, non-SMR pipeline MW → regional shares
    if "region" in pipeline_df.columns:
        _region_mw: dict[str, float] = {}
        for _, _pr in pipeline_df.iterrows():
            if str(_pr.get("region", "")) == "China":
                continue
            if _pr.get("is_smr", False):
                continue
            _r = str(_pr.get("region", "Other"))
            _region_mw[_r] = _region_mw.get(_r, 0.0) + float(_pr.get("net_capacity_mw", 0) or 0)

        _total_pipe_mw = sum(_region_mw.values())

        if _total_pipe_mw > 0:
            lines.append(
                f"\n## Post-2040 Regional Build Shares (proxy from large-reactor pipeline)\n"
                f"Current levers: post2040={_p40_gw:.1f} GW/yr global | "
                f"China={_china_gw:.1f} GW/yr | "
                f"Rest-of-world={_row_gw:.1f} GW/yr "
                f"({_row_large_gw:.1f} GW/yr large + {_row_gw*_smr_share:.1f} GW/yr SMR "
                f"at {_smr_pct:.0f}% SMR share)"
            )
            lines.append(
                "Non-China regional share of large-reactor post-2040 builds "
                "(set post2040_global_new_build_gw_per_year to the 'new rate' value below "
                "if that region stops building large reactors):"
            )

            _sorted = sorted(_region_mw.items(), key=lambda x: -x[1])
            for _reg, _mw in _sorted:
                _share = _mw / _total_pipe_mw
                _gw_eq = _share * _row_large_gw
                _new_rate = round(_p40_gw - _gw_eq, 1)
                lines.append(
                    f"  {_reg}: {_share*100:.0f}% of RoW large → "
                    f"~{_gw_eq:.1f} GW/yr | new rate if stops = {_new_rate}"
                )

            # Pre-compute named blocs
            _blocs: list[tuple[str, list[str]]] = [
                ("West (US+Canada+France+UK+W.Europe)",
                 ["United States", "Canada & Mexico", "France",
                  "United Kingdom", "Rest of Western Europe"]),
                ("Europe total (incl. Eastern Europe)",
                 ["France", "United Kingdom", "Rest of Western Europe", "Eastern Europe"]),
                ("Western Europe only (France+UK+W.Europe)",
                 ["France", "United Kingdom", "Rest of Western Europe"]),
                ("Asia ex-China (East+South+SE Asia)",
                 ["East Asia", "South Asia", "Southeast Asia"]),
            ]
            lines.append("")
            for _bloc_name, _bloc_regs in _blocs:
                _bloc_mw    = sum(_region_mw.get(r, 0.0) for r in _bloc_regs)
                _bloc_share = _bloc_mw / _total_pipe_mw
                _bloc_gw    = _bloc_share * _row_large_gw
                _bloc_new   = round(_p40_gw - _bloc_gw, 1)
                lines.append(
                    f"  {_bloc_name}: {_bloc_share*100:.0f}% → ~{_bloc_gw:.1f} GW/yr large | "
                    f"new post2040 rate if stops = {_bloc_new}"
                )

    return "\n".join(lines)


def call_claude(
    user_message: str,
    reactor_df,
    api_key: str,
    chat_history: list,
) -> dict:
    """Call Claude API; return dict with 'message' and 'actions' keys."""
    import anthropic as _ant

    client = _ant.Anthropic(api_key=api_key)

    system = _SYSTEM_PROMPT.format(
        current_state   = _current_state_text(),
        reactor_context = _reactor_context_text(reactor_df, user_message),
        lever_schema    = _lever_schema_text(),
        regions         = ", ".join(REGIONS),
    )

    messages: list = []
    for h in chat_history[-8:]:  # last 4 turns
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": user_message})

    # Try models in preference order — first available wins
    _models_to_try = [
        "claude-sonnet-4-5-20250929",   # primary: best reasoning for complex geo queries
        "claude-sonnet-4-20250514",     # fallback sonnet
        "claude-haiku-4-5-20251001",    # fast fallback
        "claude-opus-4-20250514",
        "claude-3-5-sonnet-20241022",   # older fallbacks
        "claude-3-haiku-20240307",
    ]
    try:
        resp = None
        _last_err = None
        for _model in _models_to_try:
            try:
                resp = client.messages.create(
                    model      = _model,
                    max_tokens = 8192,
                    system     = system,
                    messages   = messages,
                )
                break  # success
            except Exception as _e:
                _last_err = _e
                if "not_found" not in str(_e).lower():
                    raise  # non-model error — re-raise immediately

        if resp is None:
            raise Exception(f"No accessible model found. Last error: {_last_err}")

        raw = resp.content[0].text.strip() if resp.content else ""

        if not raw:
            return {
                "message": "Claude returned an empty response. Please try rephrasing your question.",
                "actions": [],
            }

        # Strategy 1: strip ```json … ``` fences
        if "```" in raw:
            for part in raw.split("```"):
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                if part.startswith("{"):
                    try:
                        return json.loads(part)
                    except json.JSONDecodeError:
                        continue

        # Strategy 2: direct parse (response IS json)
        if raw.startswith("{"):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                pass

        # Strategy 3: find first { ... last } in case of preamble/postamble text
        brace_start = raw.find("{")
        brace_end   = raw.rfind("}")
        if brace_start != -1 and brace_end > brace_start:
            candidate = raw[brace_start:brace_end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

        # Fallback: Claude replied conversationally — show message, no actions
        return {"message": raw, "actions": [], "_raw": raw}

    except json.JSONDecodeError as e:
        return {
            "message": f"Sorry, I had trouble formatting my response. Please try again. (Parse error: {e})",
            "actions": [],
        }
    except Exception as e:
        return {
            "message": f"Error calling Claude: {str(e)}",
            "actions": [],
        }


# Keywords that must appear in the user message for extension_policy changes to be allowed.
# Bare "extend" intentionally excluded — too broad (matches "extend the build program",
# "extend to new regions", etc.).  Only compound retirement/lifetime phrases are allowed.
_EXTENSION_KEYWORDS = {
    "retire", "retirement", "retir",
    "shutdown", "shut down", "decommission",
    "life extension", "extended life", "extended operation",
    "license", "licence",
    "operating life", "long-term operation", "lto",
    "reactor life",
}

# Global pipeline rate levers that should NOT be increased in "reduction" scenarios
_PIPELINE_RATE_LEVERS = {
    "large_reactor_uc_pct", "large_reactor_planned_pct", "large_reactor_proposed_pct",
    "smr_uc_pct", "smr_planned_pct", "smr_proposed_pct",
}

# Base-scenario defaults for pipeline rate levers — used by guard 2 when the session-state
# key is absent (e.g. fresh app load before the form has been submitted once).
# Matches PRESET_DEFAULTS["base"] / "medium" pipeline preset.
_PIPELINE_LEVER_DEFAULTS: dict[str, int] = {
    "large_reactor_uc_pct":      100,
    "large_reactor_planned_pct": 100,
    "large_reactor_proposed_pct":  0,
    "smr_uc_pct":                100,
    "smr_planned_pct":           100,
    "smr_proposed_pct":            0,
}


def _user_asked_about_extensions(user_message: str) -> bool:
    """Return True if the user's message is about retirement or life extension."""
    msg = user_message.lower()
    return any(kw in msg for kw in _EXTENSION_KEYWORDS)


def _user_asked_to_reduce_builds(user_message: str) -> bool:
    """Return True if the user's message implies reducing or stopping new builds."""
    msg = user_message.lower()
    _reduce_kws = {
        "stop", "no new", "not build", "don't build", "halt", "ban", "moratorium",
        "reduce", "cut", "fewer", "less", "cancel", "abandon", "pause",
    }
    return any(kw in msg for kw in _reduce_kws)


def apply_claude_actions(actions: list, reactor_df, user_message: str = "") -> tuple:
    """
    Parse Claude's action list.
    Returns (new_overrides, new_synthetic, lever_updates, warnings).
      new_overrides — list of reactor override dicts
      new_synthetic — list of synthetic build dicts
      lever_updates — {ss_key: value} to write to session_state
      warnings      — list of human-readable strings for filtered/blocked actions
    """
    new_overrides: list = []
    new_synthetic: list = []
    lever_updates: dict = {}
    warnings: list = []

    _ext_ok       = _user_asked_about_extensions(user_message)
    _reducing     = _user_asked_to_reduce_builds(user_message)

    id_to_row: dict = {}
    if reactor_df is not None and not reactor_df.empty:
        id_to_row = reactor_df.set_index("reactor_id").to_dict("index")

    for action in actions:
        t = action.get("type", "")

        if t == "set_lever":
            lever = action.get("lever")
            value = action.get("value")

            # ── Safety guard 1: extension_policy ────────────────────────────
            # Only allow if the user explicitly asked about retirement/extension
            if lever == "extension_policy" and not _ext_ok:
                warnings.append(
                    f"Blocked: Claude tried to set `extension_policy → {value}` but your "
                    "question was about new builds, not retirement or life extension. "
                    "This change would cause an unrelated shift in all reactor lifetimes from 2024."
                )
                continue

            # ── Safety guard 2: don't increase global pipeline rates when reducing ──
            if lever in _PIPELINE_RATE_LEVERS and _reducing:
                import streamlit as _st2
                spec2 = LEVER_SCHEMA.get(lever, {})
                current_val = _st2.session_state.get(spec2.get("ss_key", ""), None)
                # Fall back to base-scenario default when key is absent from session state
                # (e.g. fresh app load) so the guard still catches spurious increases.
                if current_val is None:
                    current_val = _PIPELINE_LEVER_DEFAULTS.get(lever)
                # Block only if the value is being INCREASED from current/default
                try:
                    proposed_int = int(value)
                    current_int  = int(current_val) if current_val is not None else None
                    if current_int is not None and proposed_int > current_int:
                        warnings.append(
                            f"Blocked: Claude tried to INCREASE `{lever} → {value}` while you asked "
                            "to reduce builds. This is a global lever that would add capacity in all "
                            "regions. Only reactor_override (pipeline_probability=0) is allowed for "
                            "regional restrictions."
                        )
                        continue
                except (TypeError, ValueError):
                    pass

            if lever in LEVER_SCHEMA:
                spec   = LEVER_SCHEMA[lever]
                ss_key = spec["ss_key"]

                # ── Type cast ──────────────────────────────────────────────
                try:
                    if spec["type"] == "int":
                        value = int(value)
                    elif spec["type"] == "float":
                        value = float(value)
                    else:
                        value = str(value)
                except (TypeError, ValueError) as _cast_err:
                    warnings.append(
                        f"Blocked: Claude proposed `{lever} = {value!r}` which could not "
                        f"be converted to {spec['type']} ({_cast_err}). Skipped."
                    )
                    continue

                # ── Range / options validation & clamping ──────────────────
                if "options" in spec:
                    if value not in spec["options"]:
                        # Snap to nearest valid option
                        try:
                            clamped = min(spec["options"], key=lambda x: abs(x - value))
                        except TypeError:
                            # Non-numeric options (e.g. extension_policy strings)
                            clamped = spec["options"][0]
                        warnings.append(
                            f"Clamped: Claude proposed `{lever} = {value}` which is not a "
                            f"valid option {spec['options']}. Using nearest valid value: {clamped}."
                        )
                        value = clamped
                elif "range" in spec:
                    lo, hi = spec["range"]
                    if value < lo or value > hi:
                        clamped = max(lo, min(hi, value))
                        warnings.append(
                            f"Clamped: Claude proposed `{lever} = {value}` which is outside "
                            f"the allowed range [{lo}, {hi}]. Clamped to {clamped}."
                        )
                        value = clamped

                lever_updates[ss_key] = value

        elif t == "synthetic_build":
            region   = action.get("region")
            if region not in REGIONS:
                continue
            cap_mw   = float(action.get("capacity_mw", 0))
            per_year = int(action.get("per_year", 1))
            start    = int(action.get("start_year", 2030))
            n_yrs    = int(action.get("n_years", 1))
            label = (
                f"{per_year} × {int(cap_mw)} MW/yr in {region} "
                f"from {start} ({n_yrs} yr) [Claude]"
            )
            new_synthetic.append({
                "label":       label,
                "region":      region,
                "capacity_mw": cap_mw,
                "per_year":    per_year,
                "start_year":  start,
                "n_years":     n_yrs,
            })

        elif t == "reactor_override":
            rid   = action.get("reactor_id")
            field = action.get("field")
            value = action.get("value")
            if not rid or not field or value is None:
                continue

            # ── Type-coerce value by field name ───────────────────────────
            _INT_FIELDS   = {"retirement_year", "expected_online_year"}
            _FLOAT_FIELDS = {"capacity_mw", "pipeline_probability"}
            try:
                if field in _INT_FIELDS:
                    value = int(value)
                elif field in _FLOAT_FIELDS:
                    value = float(value)
                else:
                    value = str(value)
            except (TypeError, ValueError) as _ov_err:
                warnings.append(
                    f"Blocked: reactor override `{field}` on `{rid}` — "
                    f"could not convert {value!r} to the expected type: {_ov_err}"
                )
                continue

            # Clamp pipeline_probability to [0.0, 1.0]
            if field == "pipeline_probability":
                value = max(0.0, min(1.0, float(value)))

            info = id_to_row.get(rid, {})
            new_overrides.append({
                "reactor_id":   rid,
                "reactor_name": info.get("name", rid),
                "country":      info.get("country", "Unknown"),
                "field":        field,
                "value":        value,
            })

    # Deduplicate new_overrides by (reactor_id, field) — keep the last occurrence
    # in case Claude emits the same reactor+field twice in one response.
    _seen: set = set()
    _deduped: list = []
    for _ov in reversed(new_overrides):
        _k = (_ov["reactor_id"], _ov["field"])
        if _k not in _seen:
            _seen.add(_k)
            _deduped.append(_ov)
    new_overrides = list(reversed(_deduped))

    return new_overrides, new_synthetic, lever_updates, warnings


# ══════════════════════════════════════════════════════════════════════════════
# ── Analytical agent — read-only tool-calling ─────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

import sqlite3 as _sqlite3

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
- active: whichever scenario the user currently has selected

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
]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _analyst_resolve_scenario(scenario: str) -> str:
    """Map natural language scenario name to DB scenario_id."""
    try:
        import streamlit as _st
        active = _st.session_state.get("preset_selector", "base")
        if active == "custom":
            active = "base"
    except Exception:
        active = "base"
    s = scenario.lower().strip()
    _map = {
        "active": active, "current": active, "selected": active,
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


# ── Tool implementations ───────────────────────────────────────────────────────

def _tool_get_capacity(scenario: str, region: str, year: int) -> dict:
    sc  = _analyst_resolve_scenario(scenario)
    reg = _analyst_resolve_region(region)
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
    conn = _analyst_db()
    c = conn.cursor()
    data: dict = {}
    if start_year < 2024:
        c.execute(
            "SELECT year, capacity_gw FROM historical_capacity "
            "WHERE region=? AND year BETWEEN ? AND ? ORDER BY year",
            (reg, start_year, min(end_year, 2023)),
        )
        for yr, gw in c.fetchall():
            data[yr] = round(gw, 2)
    if end_year >= 2024:
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
    return {"scenario": sc, "region": reg,
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
    conn  = _analyst_db()
    c     = conn.cursor()
    c.execute(
        "SELECT year, capacity_operating_gw FROM projections "
        "WHERE scenario_id=? AND region=? AND year BETWEEN 2024 AND 2050 ORDER BY year",
        (sc, reg_a),
    )
    series_a = {yr: gw for yr, gw in c.fetchall()}

    _thresh = _re.match(r"(\d+(?:\.\d+)?)\s*(?:GW|gw)?$", region_b.strip())
    if _thresh:
        threshold = float(_thresh.group(1))
        conn.close()
        if not series_a:
            return {"error": f"No data for region='{reg_a}'"}
        for yr in sorted(series_a):
            if series_a[yr] >= threshold:
                return {"scenario": sc, "region": reg_a, "threshold_gw": threshold,
                        "crossover_year": yr,
                        "capacity_at_crossover_gw": round(series_a[yr], 2)}
        return {"scenario": sc, "region": reg_a, "threshold_gw": threshold,
                "crossover_year": None,
                "note": f"{reg_a} does not reach {threshold} GW by 2050",
                "max_by_2050_gw": round(max(series_a.values()), 2)}

    reg_b = _analyst_resolve_region(region_b)
    c.execute(
        "SELECT year, capacity_operating_gw FROM projections "
        "WHERE scenario_id=? AND region=? AND year BETWEEN 2024 AND 2050 ORDER BY year",
        (sc, reg_b),
    )
    series_b = {yr: gw for yr, gw in c.fetchall()}
    conn.close()
    if not series_a or not series_b:
        return {"error": "Missing data for one of the regions"}
    for yr in sorted(set(series_a) & set(series_b)):
        if series_a[yr] >= series_b[yr]:
            return {"scenario": sc, "region_a": reg_a, "region_b": reg_b,
                    "crossover_year": yr,
                    "a_gw": round(series_a[yr], 2), "b_gw": round(series_b[yr], 2)}
    last = max(set(series_a) & set(series_b))
    return {"scenario": sc, "region_a": reg_a, "region_b": reg_b,
            "crossover_year": None,
            "note": f"{reg_a} does not exceed {reg_b} by 2050 under {sc}",
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


# ── Tool dispatcher ────────────────────────────────────────────────────────────

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
        else:
            result = {"error": f"Unknown tool: {tool_name}"}
    except Exception as _e:
        result = {"error": f"Tool error: {_e}"}
    return json.dumps(result)


# ── Routing heuristic ──────────────────────────────────────────────────────────

def _is_analytical_question(msg: str) -> bool:
    """Return True if the message looks like a data/analysis query rather than a model action."""
    m = msg.lower().strip()

    # "What if / what happens if / what would happen if" → scenario change requests.
    # These belong to the action agent even though they start with "what".
    _scenario_prefixes = (
        "what if ",
        "what happens if",
        "what would happen if",
        "what would it look like if",
        "what would change if",
    )
    for phrase in _scenario_prefixes:
        if m.startswith(phrase):
            return False

    # Strong imperative action verbs at the start → action agent
    _action_prefixes = (
        "set ", "apply ", "retire ", "extend the life", "make ",
        "switch to ", "change to ", "turn on", "turn off",
        "add a synthetic", "stop building", "cancel ", "increase ",
        "decrease ", "reduce ", "model a scenario",
    )
    for prefix in _action_prefixes:
        if m.startswith(prefix):
            return False

    # Pure data / analytical question starters
    _analytical_starts = (
        "what is", "what are", "what was", "what were", "what does",
        "what's", "what ",   # catch-all "what" only after filtering "what if" above
        "how much", "how many", "how does", "how do", "how has", "how far",
        "when does", "when did", "when will", "when is",
        "which", "where", "why", "who",
        "is ", "are ", "does ", "can ", "could ", "would ", "will ",
        "compare", "show me", "tell me", "explain", "describe",
        "give me", "list ", "find ",
    )
    for start in _analytical_starts:
        if m.startswith(start):
            return True

    # Explicit question mark → analytical
    if "?" in msg:
        return True

    # Default → preserve existing action-agent behaviour
    return False


# ── Analytical agent call ──────────────────────────────────────────────────────

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


# ── Intent classifier ─────────────────────────────────────────────────────────

def _classify_intent(msg: str, api_key: str) -> str:
    """
    Use Claude Haiku to classify the message as 'analytical' or 'action'.
    Returns 'analytical' or 'action'. Fast (~300 ms) and cheap.
    Falls back to 'unknown' on any error so the caller can use the heuristic.
    """
    import anthropic as _ant
    _classifier_models = [
        "claude-haiku-4-5-20251001",
        "claude-3-haiku-20240307",
        "claude-haiku-4-20250514",
    ]
    try:
        client = _ant.Anthropic(api_key=api_key)
        for _model in _classifier_models:
            try:
                resp = client.messages.create(
                    model      = _model,
                    max_tokens = 5,
                    system     = (
                        "Classify the user message for a nuclear capacity projection dashboard.\n"
                        "Reply with exactly one word — either: analytical or action\n\n"
                        "analytical = asking for data, facts, comparisons, or explanations "
                        "(e.g. 'What is US capacity in 2040?', 'Compare scenarios', "
                        "'Tell me about Hinkley Point C', 'How far from the IEA target?')\n\n"
                        "action = requesting a scenario change, lever adjustment, or model "
                        "modification (e.g. 'What if there is an accident?', "
                        "'Can you retire all French reactors?', "
                        "'Set pipeline realization to high', 'Add 5 GW/yr SMRs in Asia')"
                    ),
                    messages   = [{"role": "user", "content": msg}],
                )
                result = resp.content[0].text.strip().lower()
                if result in ("analytical", "action"):
                    return result
            except Exception as _e:
                if "not_found" not in str(_e).lower():
                    return "unknown"
                continue
    except Exception:
        pass
    return "unknown"


# ── Top-level router ───────────────────────────────────────────────────────────

def call_claude_router(
    user_message: str,
    reactor_df,
    api_key: str,
    chat_history: list,
) -> dict:
    """
    Route to the analytical agent (read-only) or the action agent (lever changes).
    Uses a Haiku classifier for accuracy; falls back to the keyword heuristic
    if the classifier call fails or the API key is unavailable.
    Both paths return {message: str, actions: list}.
    """
    if api_key:
        intent = _classify_intent(user_message, api_key)
    else:
        intent = "unknown"

    if intent == "unknown":
        # Classifier unavailable — fall back to keyword heuristic
        intent = "analytical" if _is_analytical_question(user_message) else "action"

    if intent == "analytical":
        return call_claude_analyst(user_message, api_key, chat_history)
    return call_claude(user_message, reactor_df, api_key, chat_history)
