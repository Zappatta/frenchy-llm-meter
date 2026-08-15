#pragma once

#include "protocol.h"

namespace display {

void begin();

// Draws the concentric rings and hub. Only redraws what changed, so calling
// this every loop is cheap.
//
// haveFrame says whether `frame` holds anything the host has ever sent. With a
// frame in hand the figures stay on the glass whether or not the link is up,
// and the link dot carries the warning; without one there is nothing to show
// but NO LINK.
void render(const proto::StateFrame& frame, bool linkUp, bool haveFrame);

void showWaiting();  // pre-connection splash

}  // namespace display
