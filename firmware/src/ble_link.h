#pragma once

#include <cstdint>

#include "protocol.h"

namespace ble_link {

void begin();

// Call every loop. Drops a connection whose central has gone silent and starts
// advertising again — without it the device can sit "connected" forever to a
// host that no longer exists.
void tick(uint32_t nowMs);

// True once a payload has arrived recently enough to still be believable.
bool linkUp(uint32_t nowMs);

// True once any valid payload has ever been decoded. Distinguishes "the host
// went quiet, here are its last figures" from "this device has never been fed".
bool hasFrame();

// The most recent decoded frame. Contents are meaningful once hasFrame(); if
// linkUp() is false they are the last thing the host sent, not current.
const proto::StateFrame& frame();

// True exactly once per newly received payload, so the caller can react to a
// change without polling every field.
bool consumeUpdate();

}  // namespace ble_link
