#include "status_led.h"

#include <Adafruit_NeoPixel.h>

#include "anim.h"
#include "config.h"

namespace {

Adafruit_NeoPixel pixel(1, PIN_LED, NEO_GRB + NEO_KHZ800);

// Working breathes slowly and unobtrusively. Waiting uses PULSE_PERIOD_MS,
// shared with the waiting rings on screen.
constexpr uint32_t BREATHE_MS = 3200;

using anim::triangle;

void write(uint8_t r, uint8_t g, uint8_t b, float scale) {
  pixel.setPixelColor(0, pixel.Color(static_cast<uint8_t>(r * scale),
                                     static_cast<uint8_t>(g * scale),
                                     static_cast<uint8_t>(b * scale)));
  pixel.show();
}

}  // namespace

namespace status_led {

void begin() {
  pixel.begin();
  pixel.setBrightness(255);
  pixel.clear();
  pixel.show();
}

void update(const proto::StateFrame& frame, bool linkUp, uint32_t nowMs) {
  // Red is reserved for things that are actually wrong: no host, a host-side
  // read failure, or genuinely near the plan ceiling. Spending it on "no
  // sessions running" would leave nothing to signal a real problem with.
  if (!linkUp || frame.hostError()) {
    write(255, 40, 40, 0.20f + 0.60f * triangle(nowMs, PULSE_PERIOD_MS));
    return;
  }
  if (frame.warning()) {
    write(255, 40, 40, 0.85f);
    return;
  }

  // Waiting outranks working. If one session is churning and another wants
  // input, the actionable state is the one worth showing.
  bool anyWaiting = false;
  bool anyWorking = false;
  for (uint8_t i = 0; i < frame.count; ++i) {
    if (frame.sessions[i].state == proto::STATE_WAITING) anyWaiting = true;
    if (frame.sessions[i].state == proto::STATE_WORKING) anyWorking = true;
  }

  if (anyWaiting) {
    write(46, 204, 113, 0.25f + 0.75f * triangle(nowMs, PULSE_PERIOD_MS));
  } else if (anyWorking) {
    write(255, 159, 28, 0.12f + 0.45f * triangle(nowMs, BREATHE_MS));
  } else {
    write(40, 90, 160, 0.06f);  // idle: a dim blue ember, not off and not alarming
  }
}

}  // namespace status_led
