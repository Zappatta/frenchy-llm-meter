// LGFX_USE_V1 comes from build_flags in platformio.ini.
#include <LovyanGFX.hpp>

#include <cstdio>
#include <cstring>

#include "anim.h"
#include "config.h"
#include "display.h"

namespace {

// LovyanGFX has no built-in board profile for a bare GC9A01 on an S3 Super
// Mini, so the bus and panel are configured by hand.
class LGFX : public lgfx::LGFX_Device {
  lgfx::Panel_GC9A01 _panel;
  lgfx::Bus_SPI _bus;

 public:
  LGFX() {
    {
      auto cfg = _bus.config();
      cfg.spi_host = SPI2_HOST;
      cfg.spi_mode = 0;
      cfg.freq_write = 40000000;
      cfg.freq_read = 16000000;
      cfg.spi_3wire = true;
      cfg.use_lock = true;
      cfg.dma_channel = SPI_DMA_CH_AUTO;
      cfg.pin_sclk = PIN_SCLK;
      cfg.pin_mosi = PIN_MOSI;
      cfg.pin_miso = -1;  // the module does not break MISO out
      cfg.pin_dc = PIN_DC;
      _bus.config(cfg);
      _panel.setBus(&_bus);
    }
    {
      auto cfg = _panel.config();
      cfg.pin_cs = PIN_CS;
      cfg.pin_rst = PIN_RST;
      cfg.pin_busy = -1;
      cfg.panel_width = SCREEN_W;
      cfg.panel_height = SCREEN_H;
      cfg.offset_x = 0;
      cfg.offset_y = 0;
      cfg.offset_rotation = 0;
      cfg.dummy_read_pixel = 8;
      cfg.dummy_read_bits = 1;
      cfg.readable = false;  // write-only bus
      cfg.invert = true;     // GC9A01 panels are inverted
      cfg.rgb_order = PANEL_RGB_ORDER;
      cfg.dlen_16bit = false;
      cfg.bus_shared = false;
      _panel.config(cfg);
    }
    setPanel(&_panel);
  }
};

LGFX tft;

// The hub used to be a 124x124 sprite, on the theory that it redrew far more
// often than the rings. It does not: it shows an integer percentage that moves
// every few minutes. What the sprite did do was paint its square corners —
// which reach radius 88 — straight over the two innermost rings, so a third
// live session appeared with a black box cut through it. Drawing a circle
// directly onto the panel costs an occasional redraw and gives back 23KB.

bool initialised = false;

// Cache of what is currently on the glass, so an unchanged frame costs nothing.
struct Painted {
  uint8_t count = 0xFF;
  uint8_t ctx[proto::MAX_SESSIONS] = {0xFF, 0xFF, 0xFF, 0xFF};
  uint8_t state[proto::MAX_SESSIONS] = {0xFF, 0xFF, 0xFF, 0xFF};
  uint16_t pct5h = 0xFFFF;
  uint16_t pct7d = 0xFFFF;
  uint8_t lowestCtx = 0xFF;
  uint8_t flags = 0xFF;
  uint8_t pulse = 0xFF;
  bool linkUp = false;
  bool haveFrame = false;
  bool valid = false;
} painted;

// Scale an RGB888 colour toward black.
uint32_t dim(uint32_t colour, float k) {
  const uint32_t r = static_cast<uint32_t>(((colour >> 16) & 0xFF) * k);
  const uint32_t g = static_cast<uint32_t>(((colour >> 8) & 0xFF) * k);
  const uint32_t b = static_cast<uint32_t>((colour & 0xFF) * k);
  return (r << 16) | (g << 8) | b;
}

// Frozen while the link is down: the figures on screen are the last ones the
// host sent, and animating them would claim a liveness they do not have.
float pulseLevel(uint32_t nowMs, bool linkUp) {
  if (!linkUp) return 1.0f;
  return PULSE_FLOOR + (1.0f - PULSE_FLOOR) * anim::triangle(nowMs, PULSE_PERIOD_MS);
}

uint32_t colourForRing(uint8_t state, uint8_t ctxPct) {
  // Nearly out of context outranks whatever the session is doing: that is the
  // state the user has to act on.
  if (ctxPct <= CTX_CRITICAL_PCT) return COL_ALERT;
  switch (state) {
    case proto::STATE_WORKING:
      return COL_WORKING;
    case proto::STATE_WAITING:
      return COL_WAITING;
    case proto::STATE_ERROR:
      return COL_ALERT;
    default:
      return COL_IDLE;
  }
}

// A waiting session is the one asking something of you, so its ring breathes.
// Everything else holds steady — if every ring moved, none of them would mean
// anything.
uint32_t ringColour(uint8_t state, uint8_t ctxPct, float pulse) {
  const uint32_t base = colourForRing(state, ctxPct);
  return state == proto::STATE_WAITING ? dim(base, pulse) : base;
}

// Only the filled portion. The track underneath does not move, so a pulse
// repaints this and nothing else.
void drawRingFill(uint8_t index, uint8_t ctxPct, uint32_t colour) {
  if (ctxPct == 0) return;
  const int16_t outer = RING_OUTER - index * RING_PITCH;
  const int16_t inner = outer - RING_THICK;
  // 12 o'clock is -90 degrees; arcs sweep clockwise from there.
  const float sweep = 360.0f * ctxPct / 100.0f;
  tft.fillArc(CENTRE_X, CENTRE_Y, inner, outer, -90.0f, -90.0f + sweep, colour);
}

void drawRing(uint8_t index, uint8_t ctxPct, uint8_t state, float pulse) {
  const int16_t outer = RING_OUTER - index * RING_PITCH;
  const int16_t inner = outer - RING_THICK;

  // Track first, then the filled portion on top of it. The fill is context
  // REMAINING, so a ring drains clockwise as the session fills up.
  tft.fillArc(CENTRE_X, CENTRE_Y, inner, outer, 0.0f, 360.0f, COL_TRACK);
  drawRingFill(index, ctxPct, ringColour(state, ctxPct, pulse));
}

void clearRing(uint8_t index) {
  const int16_t outer = RING_OUTER - index * RING_PITCH;
  const int16_t inner = outer - RING_THICK;
  tft.fillArc(CENTRE_X, CENTRE_Y, inner, outer, 0.0f, 360.0f, COL_BG);
}

// Lowest context remaining across the live sessions — the one that runs out
// first, and so the number worth putting in the middle.
uint8_t lowestContext(const proto::StateFrame& frame, bool& found) {
  uint8_t lowest = 100;
  found = false;
  for (uint8_t i = 0; i < frame.count; ++i) {
    if (!found || frame.sessions[i].ctx_pct < lowest) {
      lowest = frame.sessions[i].ctx_pct;
      found = true;
    }
  }
  return lowest;
}

// The 5-hour plan window, as the outermost band. Unlike a session ring this
// one fills as it is consumed, so a full circle means the window is spent.
void drawPlanArc(const proto::StateFrame& frame, bool haveFrame) {
  tft.fillArc(CENTRE_X, CENTRE_Y, PLAN_INNER, PLAN_OUTER, 0.0f, 360.0f, COL_TRACK);

  // An empty track still says "there is a plan gauge here and it has no
  // reading", which is the honest answer without the statusline hook.
  if (!haveFrame || frame.noUsage() || frame.pct_5h_x10 == 0) return;

  const float sweep = 360.0f * frame.pct_5h_x10 / 1000.0f;
  tft.fillArc(CENTRE_X, CENTRE_Y, PLAN_INNER, PLAN_OUTER, -90.0f, -90.0f + sweep,
              frame.warning() ? COL_ALERT : COL_PLAN);
}

// The whole reason the figures can stay on screen after the host goes quiet:
// without this dot there would be nothing to tell current numbers from the
// last ones the host managed to send.
void drawLinkDot(bool linkUp) {
  tft.fillCircle(CENTRE_X, CENTRE_Y + HUB_DOT_Y, HUB_DOT_RADIUS,
                 linkUp ? COL_LINK_OK : COL_ALERT);
}

void drawHubBody(const proto::StateFrame& frame, bool haveFrame) {
  // Never fed. Nothing stale to fall back on, so say so outright.
  if (!haveFrame) {
    tft.setFont(&fonts::FreeSansBold18pt7b);
    tft.setTextColor(COL_ALERT);
    tft.drawString("NO", CENTRE_X, CENTRE_Y - 16);
    tft.drawString("LINK", CENTRE_X, CENTRE_Y + 16);
    return;
  }

  bool haveContext = false;
  const uint8_t ctx = lowestContext(frame, haveContext);

  if (!haveContext) {
    tft.setFont(&fonts::FreeSansBold12pt7b);
    tft.setTextColor(COL_MUTED);
    tft.drawString("NO", CENTRE_X, CENTRE_Y - 12);
    tft.drawString("SESSIONS", CENTRE_X, CENTRE_Y + 12);
    return;
  }

  // The headline: context left, in a 48px seven-segment face so it reads from
  // across the room. Three digits span ~84px and the hub is only 89px wide at
  // the top of the glyphs, so a full 100% steps down a size rather than
  // clipping against the circle.
  // Two colours, not one: the seven-segment face draws its unlit segments in
  // the background colour, and left transparent they show up as ghost strokes
  // beside the digits.
  tft.setTextColor(ctx <= CTX_CRITICAL_PCT ? COL_ALERT : COL_TEXT, COL_BG);
  tft.setFont(ctx >= 100 ? &fonts::FreeSansBold18pt7b
                         : static_cast<const lgfx::IFont*>(&fonts::Font7));
  tft.drawString(String(ctx), CENTRE_X, CENTRE_Y - 10);

  tft.setFont(&fonts::FreeSansBold9pt7b);
  tft.setTextColor(COL_MUTED);
  tft.drawString("% CTX", CENTRE_X, CENTRE_Y + 24);

  // The 7-day window is the slow-moving one, so it keeps a compact line rather
  // than a band of its own. The 5-hour figure is the outer arc.
  char footer[16];
  if (frame.noUsage()) {
    std::snprintf(footer, sizeof(footer), "%u session%s", frame.count,
                  frame.count == 1 ? "" : "s");
  } else if (frame.stale()) {
    std::snprintf(footer, sizeof(footer), "7d %u%% old", frame.pct_7d_x10 / 10);
  } else {
    std::snprintf(footer, sizeof(footer), "7d %u%%", frame.pct_7d_x10 / 10);
  }
  tft.setFont(&fonts::FreeSans9pt7b);
  tft.drawString(footer, CENTRE_X, CENTRE_Y + 42);
}

void drawHub(const proto::StateFrame& frame, bool linkUp, bool haveFrame) {
  // Clear right up to the innermost ring, not just to HUB_RADIUS, so the gap
  // between the two cannot accumulate anything.
  tft.fillCircle(CENTRE_X, CENTRE_Y, HUB_CLEAR_RADIUS, COL_BG);
  tft.setTextDatum(middle_center);

  drawHubBody(frame, haveFrame);
  drawLinkDot(linkUp);

  tft.setFont(&fonts::Font0);
}

bool ringsChanged(const proto::StateFrame& frame) {
  if (!painted.valid || painted.count != frame.count) return true;
  for (uint8_t i = 0; i < frame.count; ++i) {
    if (painted.ctx[i] != frame.sessions[i].ctx_pct) return true;
    if (painted.state[i] != frame.sessions[i].state) return true;
  }
  return false;
}

bool planChanged(const proto::StateFrame& frame, bool haveFrame) {
  return !painted.valid || painted.pct5h != frame.pct_5h_x10 ||
         painted.flags != frame.flags || painted.haveFrame != haveFrame;
}

bool hubChanged(const proto::StateFrame& frame, bool linkUp, bool haveFrame) {
  // Deliberately not the 5h percentage or its countdown: those live on the
  // outer arc now, and keying the hub to a value that ticks every minute would
  // repaint the centre number for no reason.
  bool found = false;
  return !painted.valid || painted.lowestCtx != lowestContext(frame, found) ||
         painted.pct7d != frame.pct_7d_x10 || painted.flags != frame.flags ||
         painted.linkUp != linkUp || painted.haveFrame != haveFrame ||
         painted.count != frame.count;
}

}  // namespace

namespace display {

void begin() {
  tft.init();
  tft.setRotation(SCREEN_ROTATION);
  tft.fillScreen(COL_BG);

  initialised = true;
}

void showWaiting() {
  if (!initialised) return;
  tft.fillScreen(COL_BG);
  tft.setTextDatum(middle_center);
  tft.setTextColor(COL_MUTED);
  tft.setFont(&fonts::FreeSansBold12pt7b);
  tft.drawString("clawd-meter", CENTRE_X, CENTRE_Y - 14);
  tft.setFont(&fonts::FreeSans9pt7b);
  tft.drawString("waiting for host", CENTRE_X, CENTRE_Y + 16);
  tft.setFont(&fonts::Font0);
  painted.valid = false;
}

void render(const proto::StateFrame& frame, bool linkUp, bool haveFrame,
            uint32_t nowMs) {
  if (!initialised) return;

  // First real frame after the splash: wipe the whole panel once. Everything
  // below paints only its own band, so splash text wider than the hub would
  // otherwise survive in whatever gaps the current geometry leaves.
  if (!painted.valid) {
    tft.fillScreen(COL_BG);
  }

  const float pulse = pulseLevel(nowMs, linkUp);
  // Quantised so an unchanged breath does not cost an SPI write.
  const uint8_t pulseStep = static_cast<uint8_t>(pulse * 255.0f);

  if (ringsChanged(frame)) {
    for (uint8_t i = 0; i < MAX_RINGS; ++i) {
      if (i < frame.count) {
        drawRing(i, frame.sessions[i].ctx_pct, frame.sessions[i].state, pulse);
      } else if (painted.valid && i < painted.count) {
        clearRing(i);  // a session went away — wipe its ring
      } else if (!painted.valid) {
        clearRing(i);
      }
    }
  } else if (pulseStep != painted.pulse) {
    // Same geometry, new brightness: repaint the fill of waiting rings only.
    for (uint8_t i = 0; i < frame.count && i < MAX_RINGS; ++i) {
      if (frame.sessions[i].state != proto::STATE_WAITING) continue;
      drawRingFill(i, frame.sessions[i].ctx_pct,
                   ringColour(frame.sessions[i].state, frame.sessions[i].ctx_pct,
                              pulse));
    }
  }

  if (planChanged(frame, haveFrame)) {
    drawPlanArc(frame, haveFrame);
  }

  if (hubChanged(frame, linkUp, haveFrame)) {
    drawHub(frame, linkUp, haveFrame);
  }

  bool found = false;
  painted.lowestCtx = lowestContext(frame, found);
  painted.count = frame.count;
  for (uint8_t i = 0; i < proto::MAX_SESSIONS; ++i) {
    painted.ctx[i] = i < frame.count ? frame.sessions[i].ctx_pct : 0;
    painted.state[i] = i < frame.count ? frame.sessions[i].state : 0xFF;
  }
  painted.pct5h = frame.pct_5h_x10;
  painted.pct7d = frame.pct_7d_x10;
  painted.pulse = pulseStep;
  painted.flags = frame.flags;
  painted.linkUp = linkUp;
  painted.haveFrame = haveFrame;
  painted.valid = true;
}

}  // namespace display
