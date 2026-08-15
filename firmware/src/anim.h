#pragma once

#include <cstdint>

// Shared so the screen and the LED pulse in step. They are saying the same
// thing — a session wants you — and two independent periods would read as two
// unrelated events.

namespace anim {

// Triangle wave in [0,1]. Cheaper than sin() and indistinguishable at the
// sizes anything here animates at.
inline float triangle(uint32_t nowMs, uint32_t periodMs) {
  const float phase = static_cast<float>(nowMs % periodMs) / periodMs;
  return phase < 0.5f ? phase * 2.0f : (1.0f - phase) * 2.0f;
}

}  // namespace anim
