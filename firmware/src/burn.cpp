#include "burn.h"

#include <cstring>

#include "config.h"

namespace burn {
namespace {

uint16_t g_buckets[BURN_BUCKETS] = {0};
uint32_t g_accum = 0;       // rise accumulated into the bucket still open
uint32_t g_bucketStart = 0;
uint16_t g_lastPct = 0;
bool g_primed = false;      // seen at least one frame, so a delta is meaningful
uint32_t g_revision = 0;

void closeBucket() {
  // Oldest sample falls off the front. Thirty entries is small enough that
  // moving them beats the index arithmetic of a ring buffer being read in
  // order by the renderer every time the hub repaints.
  std::memmove(&g_buckets[0], &g_buckets[1], sizeof(uint16_t) * (BURN_BUCKETS - 1));
  g_buckets[BURN_BUCKETS - 1] = g_accum > 0xFFFF ? 0xFFFF : static_cast<uint16_t>(g_accum);
  g_accum = 0;
  ++g_revision;
}

}  // namespace

void sample(uint32_t nowMs, uint16_t pct5hX10) {
  if (!g_primed) {
    g_lastPct = pct5hX10;
    g_bucketStart = nowMs;
    g_primed = true;
    return;
  }

  // A window rollover drops the figure to near zero. That is the opposite of
  // spending, so it contributes nothing rather than a large negative.
  if (pct5hX10 > g_lastPct) {
    g_accum += pct5hX10 - g_lastPct;
  }
  g_lastPct = pct5hX10;

  // A gap wider than the whole history means every bucket on screen would be
  // older than the trace claims, so the honest answer is an empty one. It also
  // discards the rise accumulated across the gap: the figure did climb, but
  // there is no telling when during those hours it happened, and attributing
  // it to the bucket we happen to be in would draw a spike at the wrong time.
  if (nowMs - g_bucketStart >= BURN_BUCKET_MS * BURN_BUCKETS) {
    std::memset(g_buckets, 0, sizeof(g_buckets));
    g_accum = 0;
    g_bucketStart = nowMs;
    ++g_revision;
    return;
  }

  // Shorter gaps close the buckets they cover, so the trace keeps its time
  // axis honest rather than compressing an idle stretch out of existence.
  while (nowMs - g_bucketStart >= BURN_BUCKET_MS) {
    closeBucket();
    g_bucketStart += BURN_BUCKET_MS;
  }
}

const uint16_t* buckets() { return g_buckets; }

uint16_t peak() {
  uint16_t hi = BURN_SCALE_MIN;
  for (uint8_t i = 0; i < BURN_BUCKETS; ++i) {
    if (g_buckets[i] > hi) hi = g_buckets[i];
  }
  return hi;
}

uint32_t revision() { return g_revision; }

}  // namespace burn
