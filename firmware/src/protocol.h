#pragma once

#include <cstdint>
#include <cstring>

// Mirror of host/clawd_meter/protocol.py. Little-endian, fixed width; the
// ESP32-S3 is little-endian so the structs map straight onto the wire bytes.
// Keep the two files in step — docs/protocol.md is the shared reference.

namespace proto {

constexpr uint8_t MAGIC = 0xC1;
constexpr uint8_t VERSION = 1;
constexpr uint8_t MAX_SESSIONS = 4;
constexpr uint8_t LABEL_LEN = 16;

constexpr uint8_t FLAG_LIMIT_WARN = 1 << 0;
constexpr uint8_t FLAG_HOST_ERROR = 1 << 1;
constexpr uint8_t FLAG_STALE = 1 << 2;     // figures are old, not current
constexpr uint8_t FLAG_NO_USAGE = 1 << 3;  // statusline shim not installed

enum State : uint8_t {
  STATE_IDLE = 0,
  STATE_WORKING = 1,
  STATE_WAITING = 2,
  STATE_ERROR = 3,
};

#pragma pack(push, 1)
struct Header {
  uint8_t magic;
  uint8_t version;
  uint8_t flags;
  uint8_t count;
  uint16_t pct_5h_x10;
  uint16_t pct_7d_x10;
  uint16_t resets_5h_min;  // whole minutes until the window resets
  uint16_t resets_7d_min;
};

struct SessionRecord {
  uint8_t state;
  uint8_t ctx_pct;   // context window remaining, 0..100
  uint16_t reserved;
  uint32_t tokens;
  char label[LABEL_LEN];
};
#pragma pack(pop)

static_assert(sizeof(Header) == 12, "header must be 12 bytes");
static_assert(sizeof(SessionRecord) == 24, "session record must be 24 bytes");

constexpr size_t MAX_PAYLOAD = sizeof(Header) + sizeof(SessionRecord) * MAX_SESSIONS;

struct SessionView {
  uint8_t state;
  uint8_t ctx_pct;   // context window remaining, 0..100
  uint32_t tokens;
  char label[LABEL_LEN + 1];  // NUL-terminated for printing
};

struct StateFrame {
  uint8_t flags;
  uint8_t count;
  uint16_t pct_5h_x10;
  uint16_t pct_7d_x10;
  uint16_t resets_5h_min;
  uint16_t resets_7d_min;
  SessionView sessions[MAX_SESSIONS];

  bool noUsage() const { return flags & FLAG_NO_USAGE; }
  bool stale() const { return flags & FLAG_STALE; }
  bool warning() const { return flags & FLAG_LIMIT_WARN; }
  bool hostError() const { return flags & FLAG_HOST_ERROR; }
};

// Returns false on a malformed payload; the caller keeps the previous frame
// rather than blanking the screen over one bad write.
inline bool decode(const uint8_t* data, size_t len, StateFrame& out) {
  if (data == nullptr || len < sizeof(Header)) return false;

  Header header;
  std::memcpy(&header, data, sizeof(Header));
  if (header.magic != MAGIC || header.version != VERSION) return false;
  if (header.count > MAX_SESSIONS) return false;
  if (len < sizeof(Header) + sizeof(SessionRecord) * header.count) return false;

  out.flags = header.flags;
  out.count = header.count;
  out.pct_5h_x10 = header.pct_5h_x10;
  out.pct_7d_x10 = header.pct_7d_x10;
  out.resets_5h_min = header.resets_5h_min;
  out.resets_7d_min = header.resets_7d_min;

  const uint8_t* cursor = data + sizeof(Header);
  for (uint8_t i = 0; i < header.count; ++i) {
    SessionRecord record;
    std::memcpy(&record, cursor, sizeof(SessionRecord));
    cursor += sizeof(SessionRecord);

    out.sessions[i].state = record.state;
    out.sessions[i].ctx_pct = record.ctx_pct > 100 ? 100 : record.ctx_pct;
    out.sessions[i].tokens = record.tokens;
    std::memcpy(out.sessions[i].label, record.label, LABEL_LEN);
    out.sessions[i].label[LABEL_LEN] = '\0';
  }
  return true;
}

}  // namespace proto
