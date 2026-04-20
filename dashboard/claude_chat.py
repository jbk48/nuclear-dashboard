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
