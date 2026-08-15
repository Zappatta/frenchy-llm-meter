#pragma once

#include <cstdint>

#include "protocol.h"

namespace status_led {

void begin();

// Call every loop — the breathe and pulse effects are driven off nowMs rather
// than blocking delays.
void update(const proto::StateFrame& frame, bool linkUp, uint32_t nowMs);

}  // namespace status_led
