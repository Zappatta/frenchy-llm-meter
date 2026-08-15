"""Per-model facts the meter needs.

This was once a pricing module: it carried the full per-model rate table and
cache multipliers so usage could be summed in "Opus-equivalent tokens" and
turned into a percentage of the plan. That job disappeared twice over — first
when the statusline turned out to report the real plan percentages, and then
when the rings changed from usage share to context remaining. What survived is
the one thing still consulted: how big each model's context window is.
"""

from __future__ import annotations

# KNOWN GAP, deliberately left rather than guessed at.
#
# The table assumes every model but Haiku has a 1M window. For a model that
# actually has 200k, a 180k prompt reports 82% of the window free when the
# truth is nearer 10%, so the ring never turns red before a compaction. The
# failure is one-directional and it is the dangerous direction.
#
# Inverting the default is not obviously better: it would peg every genuinely
# 1M session at zero and leave the meter permanently, wrongly red. Fixing this
# properly needs the actual window per model id, which is not something to
# invent from memory — it wants a real list.
#
# Blast radius is limited to the estimate path: any session with a statusline
# capture uses the real figure and never reaches this.
_CONTEXT_WINDOWS: dict[str, int] = {
    "claude-haiku-4-5": 200_000,
}
_DEFAULT_CONTEXT_WINDOW = 1_000_000


def context_window_for(model: str) -> int:
    """Context window size in tokens, for estimating how full a session is.

    Only used when a session has no statusline capture yet — the statusline
    reports the real figure and is preferred wherever it exists.
    """
    key = (model or "").removeprefix("anthropic.")
    for known, size in _CONTEXT_WINDOWS.items():
        if key.startswith(known):
            return size
    return _DEFAULT_CONTEXT_WINDOW
