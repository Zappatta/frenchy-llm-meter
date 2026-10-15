"""List prices per model, for turning transcript token counts into dollars.

An estimate, and deliberately labelled as one. It is the same calculation
Claude Code does for its own cost display: tokens times the published per-token
rate. What it cannot know is anything negotiated — an enterprise discount, a
committed-use rate — so the figure is what the usage would cost at list price,
which on a credits account is usually what is being drawn down.

Rates are dollars per million tokens. Cache writes are priced off the input
rate (1.25x for the 5-minute TTL, 2x for the 1-hour TTL) on every model. Cache
reads are not a fixed fraction any more — 0.1x on most models, 0.05x on Opus
5.5 and 0.025x on Fable 5.1 — so each entry carries its own.

Last checked against the published table on 2026-10-15. When a new model
ships, add a row; until then it is priced as the newest model of its family
and logged once, which is a better answer than silently pricing it at zero.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

CACHE_WRITE_5M = 1.25
CACHE_WRITE_1H = 2.0

# Claude Code's fast mode runs the same model at premium rates: 2x standard on
# every model that offers it.
FAST_MULTIPLIER = 2.0

# Server-side web search is billed per request, on top of tokens.
WEB_SEARCH_PER_REQUEST = 10.0 / 1000


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float
    # Haiku 5.5 charges more once the prompt passes a size threshold. None for
    # every model with one flat rate.
    long_prompt_over: int | None = None
    long_input: float = 0.0
    long_output: float = 0.0
    long_cache_read: float = 0.0


# Matched by longest prefix, so a dated snapshot id such as
# ``claude-sonnet-4-5-20250929`` finds its row, and ``claude-opus-4-5`` is not
# mistaken for ``claude-opus-4``.
PRICES: dict[str, Price] = {
    "claude-fable-5-1": Price(10.00, 50.00, 0.25),
    "claude-mythos-5-1": Price(10.00, 50.00, 0.25),
    "claude-fable-5": Price(10.00, 50.00, 1.00),
    "claude-mythos-5": Price(10.00, 50.00, 1.00),
    "claude-opus-5-5": Price(4.00, 20.00, 0.20),
    "claude-opus-5": Price(5.00, 25.00, 0.50),
    "claude-opus-4-8": Price(5.00, 25.00, 0.50),
    "claude-opus-4-7": Price(5.00, 25.00, 0.50),
    "claude-opus-4-6": Price(5.00, 25.00, 0.50),
    "claude-opus-4-5": Price(5.00, 25.00, 0.50),
    "claude-opus-4-1": Price(15.00, 75.00, 1.50),
    "claude-opus-4": Price(15.00, 75.00, 1.50),
    "claude-sonnet-5-5": Price(2.00, 10.00, 0.20),
    "claude-sonnet-5": Price(2.00, 10.00, 0.20),
    "claude-sonnet-4-6": Price(3.00, 15.00, 0.30),
    "claude-sonnet-4-5": Price(3.00, 15.00, 0.30),
    "claude-sonnet-4": Price(3.00, 15.00, 0.30),
    "claude-3-7-sonnet": Price(3.00, 15.00, 0.30),
    "claude-haiku-5-5": Price(
        0.10, 0.50, 0.01,
        long_prompt_over=100_000, long_input=0.50, long_output=2.50,
        long_cache_read=0.05,
    ),
    "claude-haiku-4-5": Price(1.00, 5.00, 0.10),
    "claude-3-5-haiku": Price(0.80, 4.00, 0.08),
}

# For a model id nobody has added a row for yet: the newest of its family.
_FAMILY_FALLBACK = {
    "fable": "claude-fable-5-1",
    "mythos": "claude-mythos-5-1",
    "opus": "claude-opus-5-5",
    "sonnet": "claude-sonnet-5-5",
    "haiku": "claude-haiku-5-5",
}

_warned: set[str] = set()


def price_for(model: str) -> Price | None:
    best = ""
    for prefix in PRICES:
        if model.startswith(prefix) and len(prefix) > len(best):
            best = prefix
    if best:
        return PRICES[best]

    # "<synthetic>" is what Claude Code writes for a locally generated message
    # (an API error, an interrupt). It carries zero usage and costs nothing.
    if not model or model.startswith("<"):
        return None

    for family, fallback in _FAMILY_FALLBACK.items():
        if family in model:
            if model not in _warned:
                _warned.add(model)
                log.warning("no price for %s; pricing it as %s", model, fallback)
            return PRICES[fallback]

    if model not in _warned:
        _warned.add(model)
        log.warning("no price for %s and no family to guess from; counting it as $0",
                    model)
    return None


def cost_usd(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_write_5m: int,
    cache_write_1h: int,
    cache_read: int,
    fast: bool = False,
    web_searches: int = 0,
) -> float:
    """What one API response cost at list price."""
    price = price_for(model)
    if price is None:
        return web_searches * WEB_SEARCH_PER_REQUEST

    rate_in, rate_out, rate_read = price.input, price.output, price.cache_read
    prompt = input_tokens + cache_write_5m + cache_write_1h + cache_read
    if price.long_prompt_over is not None and prompt > price.long_prompt_over:
        rate_in, rate_out, rate_read = (
            price.long_input, price.long_output, price.long_cache_read
        )

    dollars = (
        input_tokens * rate_in
        + output_tokens * rate_out
        + cache_write_5m * rate_in * CACHE_WRITE_5M
        + cache_write_1h * rate_in * CACHE_WRITE_1H
        + cache_read * rate_read
    ) / 1_000_000
    if fast:
        dollars *= FAST_MULTIPLIER
    return dollars + web_searches * WEB_SEARCH_PER_REQUEST
