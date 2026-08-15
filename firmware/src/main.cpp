// frenchy-llm-meter — Claude Code plan usage on a 1.28" round TFT, inside a crab.
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

// Nothing on the glass animates: the LED carries motion, the screen carries
// colour. render() diffs against what is already drawn, so this is a slow
// heartbeat rather than a frame rate.
constexpr uint32_t SCREEN_INTERVAL_MS = 250;

uint32_t lastLed = 0;
uint32_t lastScreen = 0;
bool wasLinkUp = false;

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("\n[frenchy-llm-meter] booting");

  status_led::begin();
  display::begin();
  display::showWaiting();
  ble_link::begin();

  Serial.println("[frenchy-llm-meter] ready");
}

void loop() {
  const uint32_t now = millis();
  ble_link::tick(now);
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
      Serial.println("[frenchy-llm-meter] host went quiet; holding last figures");
    }
    wasLinkUp = linkUp;

    display::render(ble_link::frame(), linkUp, haveFrame);
  }

  delay(5);
}
