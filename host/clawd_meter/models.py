"""Per-model facts the meter needs.

This was once a pricing module: it carried the full per-model rate table and
cache multipliers so usage could be summed in "Opus-equivalent tokens" and
turned into a percentage of the plan. That job disappeared twice over — first
when the statusline turned out to report the real plan percentages, and then
when the rings changed from usage share to context remaining. What survived is
the one thing still consulted: how big each model's context window is.
"""

from __future__ import annotations

# Every current model carries a 1M context window except Haiku, so the table
# only needs the exceptions.
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
