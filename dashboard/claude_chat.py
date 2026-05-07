"""
claude_chat.py — Backward-compatible shim.

This module previously contained all chat/AI logic (~2 400 lines).
It has been refactored into four focused modules:

  claude_context.py  — LEVER_SCHEMA, system prompt, context builders
  claude_action.py   — action agent (call_claude, apply_claude_actions)
  claude_analyst.py  — analytical agent (tool-calling, 14 read-only tools)
  claude_router.py   — intent classifier + top-level router

Public API re-exported here so existing callers (levers.py) require no changes:
  - call_claude_router    → dashboard.claude_router.call_claude_router
  - apply_claude_actions  → dashboard.claude_action.apply_claude_actions
  - LEVER_SCHEMA          → dashboard.claude_context.LEVER_SCHEMA  (if needed)
"""

# Re-export the two symbols levers.py imports directly from this module
from dashboard.claude_router  import call_claude_router          # noqa: F401
from dashboard.claude_action  import apply_claude_actions        # noqa: F401
from dashboard.claude_context import LEVER_SCHEMA                # noqa: F401

# Also expose the individual agents in case any other code imports them here
from dashboard.claude_action  import call_claude                 # noqa: F401
from dashboard.claude_analyst import call_claude_analyst         # noqa: F401

__all__ = [
    "call_claude_router",
    "apply_claude_actions",
    "call_claude",
    "call_claude_analyst",
    "LEVER_SCHEMA",
]
