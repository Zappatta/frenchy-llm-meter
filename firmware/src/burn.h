#pragma once

#include <cstdint>

// Rolling history of how fast the plan window is being spent.
//
// The rings and the plan arc both say how much is left. Neither says anything
// about the shape of the last hour, which is the one thing a level cannot
// carry: whether it was quiet, steady, or one enormous burst twenty minutes
// ago. This keeps enough history to draw that and nothing more.
//
// Built entirely from frames the device already receives, so nothing is added
// to the wire. Each bucket holds the rise in pct_5h_x10 across its span, which
// makes a bucket a burn rate rather than a level.

namespace burn {

// Fold a freshly received frame's plan figure into the history. Call once per
// payload; sampling faster than the bucket span costs nothing.
void sample(uint32_t nowMs, uint16_t pct5hX10);

// Oldest first. Buckets never filled read as zero, so a device that booted ten
// minutes ago draws a short trace rather than a wrong one.
const uint16_t* buckets();

// Largest bucket in the history, for autoscaling. Never below
// BURN_SCALE_MIN, so a near-idle hour stays flat instead of amplifying
// rounding noise into a mountain range.
uint16_t peak();

// Bumped whenever a bucket closes. The hub only needs repainting that often,
// so this is what the dirty check keys on rather than a timer of its own.
uint32_t revision();

}  // namespace burn
