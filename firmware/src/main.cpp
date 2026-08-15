// clawd-meter — Claude Code plan usage on a 1.28" round TFT, inside a crab.
//
// The Mac reads its own transcripts, works out plan utilisation, and writes a
// 108-byte frame over BLE every few seconds. This end draws it and lights the
// status LED. All the judgement lives on the host; the firmware is a display.

#include <Arduino.h>

#include "ble_link.h"
#include "config.h"
#include "display.h"
#include "protocol.h"
#include "status_led.h"

namespace {

// The LED animates continuously, so it updates far more often than the screen.
constexpr uint32_t LED_INTERVAL_MS = 33;  // ~30fps

// The screen animates too now — a waiting session's ring breathes — so this is
// a render tick rather than a heartbeat. render() diffs against what is on the
// glass, so a tick with nothing moving costs a handful of comparisons.
constexpr uint32_t SCREEN_INTERVAL_MS = 60;

uint32_t lastLed = 0;
uint32_t lastScreen = 0;
bool wasLinkUp = false;

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("\n[clawd-meter] booting");

  status_led::begin();
  display::begin();
  display::showWaiting();
  ble_link::begin();

  Serial.println("[clawd-meter] ready");
}

void loop() {
  const uint32_t now = millis();
  const bool linkUp = ble_link::linkUp(now);
  const bool haveFrame = ble_link::hasFrame();
  const bool freshPayload = ble_link::consumeUpdate();

  if (now - lastLed >= LED_INTERVAL_MS) {
    lastLed = now;
    status_led::update(ble_link::frame(), linkUp, now);
  }

  // Redraw on a new payload, on a link state change, or on the render tick.
  const bool linkChanged = linkUp != wasLinkUp;
  if (freshPayload || linkChanged || (now - lastScreen >= SCREEN_INTERVAL_MS)) {
    lastScreen = now;

    if (linkChanged && !linkUp) {
      Serial.println("[clawd-meter] host went quiet; holding last figures");
    }
    wasLinkUp = linkUp;

    display::render(ble_link::frame(), linkUp, haveFrame, now);
  }

  delay(5);
}
