#pragma once

#include <cstdint>

// ---------------------------------------------------------------------------
// Pin map — ESP32-S3 Super Mini -> GC9A01 1.28" round TFT
//
// All five display lines sit on the right-hand header (GP8..GP12) so the
// ribbon runs in one straight bundle with no crossing. GP48 is skipped
// deliberately: the onboard WS2812 is wired to it.
//
// The GC9A01 module has no MISO — it is write-only SPI, so pin_miso stays -1.
// ---------------------------------------------------------------------------
constexpr int8_t PIN_SCLK = 12;  // module pin 3, SCL
constexpr int8_t PIN_MOSI = 11;  // module pin 4, SDA
constexpr int8_t PIN_DC = 10;    // module pin 5, DC
constexpr int8_t PIN_CS = 9;     // module pin 6, CS
constexpr int8_t PIN_RST = 8;    // module pin 7, RST
constexpr int8_t PIN_LED = 48;   // onboard WS2812

// The module's VCC pin feeds an XC6206P332MR regulator, so it is happy on
// either 3V3 or 5V. Backlight is hardwired through R6 to +3.3V and is not
// exposed on the 7-pin header — there is no brightness or blanking control
// without modifying the board.

// ---------------------------------------------------------------------------
// Display geometry. Active area is 32.40mm across a 240x240 raster.
// ---------------------------------------------------------------------------
constexpr int16_t SCREEN_W = 240;
constexpr int16_t SCREEN_H = 240;
constexpr int16_t CENTRE_X = SCREEN_W / 2;
constexpr int16_t CENTRE_Y = SCREEN_H / 2;

// The panel is mounted upside down in the enclosure, so everything is rotated
// 180 degrees in software rather than asking for the model to be re-cut.
// LovyanGFX rotation units are quarter turns: 0, 1, 2, 3 = 0, 90, 180, 270.
constexpr uint8_t SCREEN_ROTATION = 2;

// Plan usage owns the outermost band. It is account-wide, there is always
// exactly one of it, and it used to be a line of 9pt text wedged under the
// centre number where it was both cramped and clipped.
constexpr int16_t PLAN_OUTER = 118;
constexpr int16_t PLAN_THICK = 10;
constexpr int16_t PLAN_INNER = PLAN_OUTER - PLAN_THICK;

// Session rings start inside the plan band. Slimmer than they used to be:
// four of them plus the plan arc plus a hub big enough for a 48px number is
// all the radius a 240px panel has.
constexpr int16_t RING_OUTER = 101;
constexpr int16_t RING_THICK = 8;
constexpr int16_t RING_PITCH = 11;  // thickness + gap
constexpr uint8_t MAX_RINGS = 4;    // beyond 4 the arcs get too thin to read

// Innermost ring's inner edge is RING_OUTER - 3*RING_PITCH - RING_THICK = 60,
// so the hub clears it by 4px. The hub is drawn as a circle straight onto the
// panel: it used to be a square sprite whose corners reached radius 88 and
// silently ate the two innermost rings whenever three or more sessions were
// live.
constexpr int16_t HUB_RADIUS = 56;

// Where the innermost ring starts, and how far the hub clears. Without the
// second constant the band between the hub and the rings is painted once at
// boot and never again, so whatever lands there — the splash text, a previous
// layout — is stuck on the glass for good.
constexpr int16_t RING_INNER_EDGE =
    RING_OUTER - (MAX_RINGS - 1) * RING_PITCH - RING_THICK;  // 60
constexpr int16_t HUB_CLEAR_RADIUS = RING_INNER_EDGE - 1;

// Link indicator, above the centre number and inside the hub circle.
constexpr int16_t HUB_DOT_RADIUS = 4;
constexpr int16_t HUB_DOT_Y = -44;  // relative to the hub centre

// Below this much context left, a ring turns red whatever the session is
// doing — running out of context outranks working or waiting.
constexpr uint8_t CTX_CRITICAL_PCT = 15;

// ---------------------------------------------------------------------------
// Palette. Deliberately dark: the display has no dimming, so a bright
// background is a desk lamp that never switches off.
// ---------------------------------------------------------------------------
constexpr uint32_t COL_BG = 0x0A0C10;
constexpr uint32_t COL_TRACK = 0x1E2430;
constexpr uint32_t COL_WORKING = 0xFF9F1C;
constexpr uint32_t COL_WAITING = 0x2ECC71;
constexpr uint32_t COL_IDLE = 0x4A5464;
constexpr uint32_t COL_ALERT = 0xE03131;
constexpr uint32_t COL_TEXT = 0xE8ECF2;
constexpr uint32_t COL_MUTED = 0x7A8598;
// Plan usage is a different kind of quantity from the session rings — it
// fills toward a limit rather than draining — so it gets a colour none of the
// session states use.
constexpr uint32_t COL_PLAN = 0x9B7DFF;
// Deliberately darker than COL_WAITING: a healthy link is the normal case and
// should not compete with the rings for attention.
constexpr uint32_t COL_LINK_OK = 0x2E7D4F;

// ---------------------------------------------------------------------------
// BLE
// ---------------------------------------------------------------------------
constexpr char DEVICE_NAME[] = "clawd-meter";
constexpr char SERVICE_UUID[] = "6b1d0001-9a3f-4c6e-b0d2-7f2a5c8e41aa";
constexpr char STATE_CHAR_UUID[] = "6b1d0002-9a3f-4c6e-b0d2-7f2a5c8e41aa";

// If nothing arrives for this long the host is presumed gone: the LED goes red
// and the link dot turns red. The last figures stay on the glass — stale
// numbers with a warning beat a blank screen when the daemon drops out
// mid-session. Only a device that has never had a frame says NO LINK outright.
constexpr uint32_t LINK_TIMEOUT_MS = 60000;
