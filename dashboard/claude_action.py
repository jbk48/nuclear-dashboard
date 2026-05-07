"""
claude_action.py — Action agent: convert NL what-if requests into lever/reactor actions.

Owns:
- call_claude()            : call the action agent (returns {message, actions})
- apply_claude_actions()   : validate/apply the actions Claude returns
- Guard helpers            : _user_asked_about_extensions(), _user_asked_to_reduce_builds()
- Guard constants          : _EXTENSION_KEYWORDS, _PIPELINE_RATE_LEVERS, _PIPELINE_LEVER_DEFAULTS

Imports from:
- config             : REGIONS
- claude_context     : LEVER_SCHEMA, _SYSTEM_PROMPT, context-builder functions
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import REGIONS
from dashboard.claude_context import (
    LEVER_SCHEMA,
    _SYSTEM_PROMPT,
    _current_state_text,
    _lever_schema_text,
    _reactor_context_text,
)

# ── Safety guard constants ────────────────────────────────────────────────────

# Keywords that must appear in the user message for extension_policy changes to be allowed.
# Bare "extend" intentionally excluded — too broad (matches "extend the build program", etc.).
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

# Base-scenario defaults for pipeline rate levers — used by guard 2 when session-state
# key is absent (e.g. fresh app load before the form has been submitted once).
_PIPELINE_LEVER_DEFAULTS: dict[str, int] = {
    "large_reactor_uc_pct":      100,
    "large_reactor_planned_pct": 100,
    "large_reactor_proposed_pct":  0,
    "smr_uc_pct":                100,
    "smr_planned_pct":           100,
    "smr_proposed_pct":            0,
}


# ── Guard helpers ─────────────────────────────────────────────────────────────

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


# ── Action agent ──────────────────────────────────────────────────────────────

def call_claude(
    user_message: str,
    reactor_df,
    api_key: str,
    chat_history: list,
) -> dict:
    """Call Claude action agent; return dict with 'message' and 'actions' keys."""
    import anthropic as _ant

    client = _ant.Anthropic(api_key=api_key)

    # Build combined text for geography detection: include recent user messages
    # so pronouns like "they all" or "what if they all shut down?" resolve
    # correctly from prior conversation context (e.g. after analyst answers
    # "how many US reactors?", the action agent needs the US fleet IDs even
    # though the follow-up message contains no explicit geographic reference).
    _geo_context = user_message
    for _h in chat_history[-6:]:
        if _h["role"] == "user":
            _geo_context += " " + _h["content"]

    system = _SYSTEM_PROMPT.format(
        current_state   = _current_state_text(),
        reactor_context = _reactor_context_text(reactor_df, _geo_context),
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


# ── Action applicator ─────────────────────────────────────────────────────────

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

    _ext_ok   = _user_asked_about_extensions(user_message)
    _reducing = _user_asked_to_reduce_builds(user_message)

    id_to_row: dict = {}
    if reactor_df is not None and not reactor_df.empty:
        id_to_row = reactor_df.set_index("reactor_id").to_dict("index")

    for action in actions:
        t = action.get("type", "")

        if t == "set_lever":
            lever = action.get("lever")
            value = action.get("value")

            # ── Safety guard 1: extension_policy ────────────────────────────
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
                if current_val is None:
                    current_val = _PIPELINE_LEVER_DEFAULTS.get(lever)
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
                        try:
                            clamped = min(spec["options"], key=lambda x: abs(x - value))
                        except TypeError:
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
