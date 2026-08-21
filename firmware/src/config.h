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
// Panel colour order. GC9A01 modules ship wired both ways, and the panel does
// not report which it is: the driver has to be told. Get it wrong and the red
// and blue channels swap. Green sits in the middle and is unaffected, so it
// looks plausible at a glance — the tell is that the amber "working" ring
// renders blue, and COL_ALERT (0xE03131) renders as 0x3131E0, which reads as
// calm rather than as a warning.
//
// The boards this was built against are BGR, which is the default. Build with
// `pio run -e frenchy-llm-meter-rgb` for the other variant.
// ---------------------------------------------------------------------------
#ifdef FRENCHY_PANEL_RGB
constexpr bool PANEL_RGB_ORDER = true;
#else
constexpr bool PANEL_RGB_ORDER = false;
#endif

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

// The onboard WS2812, off by default.
//
// It breathed amber while working and pulsed green while a session waited on
// input. Both went the way of the ring pulse: something moving on the desk all
// day is nagging rather than signalling, whether it is a 240px disc or a 5mm
// dot. Flip this to true to get it back — the code is intact and every state
// below still means what it says.
//
// With it off, session state is carried by ring colour alone.
constexpr bool STATUS_LED_ENABLED = false;

// Period of the LED's attention-seeking pulse, when it is enabled at all.
constexpr uint32_t PULSE_PERIOD_MS = 1100;

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

// Clawd's own colours, sampled from the official pixel crab. Deliberately not
// COL_WORKING: the amber means "a session is working" everywhere else on this
// display, and the screensaver must not look like a reading.
constexpr uint32_t COL_CLAWD = 0xF0644B;
constexpr uint32_t COL_CLAWD_EYE = 0x000000;

// ---------------------------------------------------------------------------
// BLE
// ---------------------------------------------------------------------------
constexpr char DEVICE_NAME[] = "frenchy-llm-meter";
constexpr char SERVICE_UUID[] = "6b1d0001-9a3f-4c6e-b0d2-7f2a5c8e41aa";
constexpr char STATE_CHAR_UUID[] = "6b1d0002-9a3f-4c6e-b0d2-7f2a5c8e41aa";

// If nothing arrives for this long the host is presumed gone: the LED goes red
// and the link dot turns red. The last figures stay on the glass — stale
// numbers with a warning beat a blank screen when the daemon drops out
// mid-session. Only a device that has never had a frame says NO LINK outright.
constexpr uint32_t LINK_TIMEOUT_MS = 60000;

// ---------------------------------------------------------------------------
// Screensaver
//
// A SECOND, LONGER SILENCE THRESHOLD. This is a deliberate exception to the
// one-timeout rule that governs LINK_TIMEOUT_MS, and it is not a fork of it:
// LINK_TIMEOUT_MS still does both of its jobs at 60s, unchanged, and still
// means "stop trusting the link, drop the central, re-advertise". This
// constant does nothing but decide when the glass stops being an instrument
// and starts being an ornament. Fifteen minutes, so the red dot and the held
// figures get a full fourteen of them to be seen first.
//
// Gated on having ever had a frame. A device that has never been fed keeps
// saying NO LINK: that is a setup problem, and a dancing crab would bury it.
// ---------------------------------------------------------------------------
constexpr uint32_t SCREENSAVER_AFTER_MS = 900000;  // 15 minutes

// How long a dance step is held. Movement is quantised to whole grid cells,
// so there is nothing to draw between steps and this is the render tick too.
constexpr uint32_t CLAWD_POSE_MS = 140;

// Not a multiple of CLAWD_POSE_MS's eight-step cycle (1120ms), so the blink
// drifts around the dance rather than landing on the same step every time.
constexpr uint32_t CLAWD_BLINK_EVERY_MS = 3100;

// Cell size in pixels. At 8 the crab is 160x112 and the envelope it dances in
// is 192x120 — the far corner of that sits at radius 113 on a 120px panel, so
// this is close to as large as it goes. Check the geometry before raising it.
constexpr int16_t CLAWD_SCALE = 8;

// ---------------------------------------------------------------------------
// Burn-rate sparkline — the hub's headline since 2026-08-20
//
// Replaced the lowest-context number, which repeated what the innermost ring
// already showed. The rings and the plan arc are both levels; this is the only
// thing on the glass carrying a trend, so it plots rate rather than total:
// a flat floor while idle, a spike where a burst landed.
//
// Thirty two-minute buckets is the last hour. Three pixels a bucket puts the
// chart at 90px wide, and its far corner at radius 50 against a
// HUB_CLEAR_RADIUS of 59 — inside, but check the corner before widening it.
// ---------------------------------------------------------------------------
constexpr uint8_t BURN_BUCKETS = 30;
constexpr uint32_t BURN_BUCKET_MS = 120000;  // 2 minutes

constexpr int16_t SPARK_COL_W = 3;
constexpr int16_t SPARK_W = BURN_BUCKETS * SPARK_COL_W;  // 90
constexpr int16_t SPARK_H = 30;
constexpr int16_t SPARK_TOP = -22;  // relative to the hub centre; baseline +8

// Floor for the autoscale, in pct_5h_x10 per bucket. Without it a completely
// quiet hour scales its own rounding noise to full height and reads as chaos.
constexpr uint16_t BURN_SCALE_MIN = 10;  // 1.0% in one bucket
