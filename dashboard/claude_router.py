"""
claude_router.py — Intent classification and top-level routing.

Owns:
- _is_analytical_question() : keyword heuristic for analytical vs. action routing
- _classify_intent()        : Haiku-based fast classifier (primary)
- call_claude_router()      : top-level entry point — routes to analyst or action agent

Imports from:
- claude_action  : call_claude (action agent)
- claude_analyst : call_claude_analyst (analytical agent)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.claude_action  import call_claude
from dashboard.claude_analyst import call_claude_analyst


# ── Routing heuristic ─────────────────────────────────────────────────────────

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


# ── Intent classifier (Haiku-powered) ────────────────────────────────────────

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


# ── Top-level router ──────────────────────────────────────────────────────────

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
