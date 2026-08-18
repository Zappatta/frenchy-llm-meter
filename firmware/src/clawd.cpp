#include "clawd.h"

#include <cstring>

#include "config.h"

namespace clawd {
namespace {

// The shell, drawn flat. Claws and legs are not in this table: they are the
// parts that move, and a pose that differs by two rows is not worth a second
// copy of the whole crab.
//
//                                  0123456789012345678
constexpr uint8_t SHELL_ROWS = 10;
constexpr uint8_t SHELL_COL0 = 3;
constexpr uint8_t SHELL_W = 14;

// Four legs, two cells wide, with two-cell gaps — which is exactly the 14
// cells the shell is wide, so the outer legs sit flush with its corners.
constexpr uint8_t LEG_COUNT = 4;
constexpr uint8_t LEG_COL[LEG_COUNT] = {3, 7, 11, 15};
constexpr uint8_t LEG_W = 2;
constexpr uint8_t LEG_ROW0 = SHELL_ROWS;
constexpr uint8_t LEG_LEN = 4;

// Claws stick out past the shell either side, a little above the waist.
constexpr uint8_t CLAW_ROW0 = 3;
constexpr uint8_t CLAW_LEN = 4;
constexpr uint8_t CLAW_W = 3;

// Eyes sit two cells in from each shell edge, which leaves four between them.
constexpr uint8_t EYE_ROW0 = 1;
constexpr uint8_t EYE_SIZE = 3;
constexpr uint8_t EYE_L_COL = 5;
constexpr uint8_t EYE_R_COL = 12;

// Open, and the blink: chevrons pointing inward, ">" and "<".
constexpr char EYE_OPEN[EYE_SIZE][EYE_SIZE + 1] = {"###", "###", "###"};
constexpr char EYE_SHUT_L[EYE_SIZE][EYE_SIZE + 1] = {"#..", ".#.", "#.."};
constexpr char EYE_SHUT_R[EYE_SIZE][EYE_SIZE + 1] = {"..#", ".#.", "..#"};

// One step of the dance. Claw offsets are row deltas, so negative is raised.
// legLift alternates which pair of legs comes off the floor.
struct Step {
  int8_t dx;
  int8_t dy;
  int8_t clawL;
  int8_t clawR;
  uint8_t legLift;  // 0 none, 1 outer pair, 2 inner pair
};

// Hop, land, shuffle left, hop, land, shuffle right. Eight steps so the two
// halves mirror rather than repeat — a four-step loop reads as a twitch.
constexpr Step STEPS[] = {
    {0, -1, -1, -1, 0},   // up, both claws up
    {0, 0, 0, 0, 1},      // down
    {-2, 0, -1, 1, 2},    // lean left, left claw up
    {-2, 0, 0, 0, 1},     // settle
    {0, -1, -1, -1, 0},   // up
    {0, 0, 0, 0, 1},      // down
    {2, 0, 1, -1, 2},     // lean right, right claw up
    {2, 0, 0, 0, 1},      // settle
};
constexpr uint8_t STEP_COUNT = sizeof(STEPS) / sizeof(STEPS[0]);

// Centring offsets for a crab at rest inside the envelope.
constexpr int8_t HOME_COL = (ENV_COLS - COLS) / 2;
constexpr int8_t HOME_ROW = ENV_ROWS - ROWS;

void put(uint8_t grid[ENV_ROWS][ENV_COLS], int16_t row, int16_t col, uint8_t v) {
  if (row < 0 || row >= ENV_ROWS || col < 0 || col >= ENV_COLS) return;
  grid[row][col] = v;
}

}  // namespace

void pose(uint32_t nowMs, uint8_t grid[ENV_ROWS][ENV_COLS]) {
  std::memset(grid, CELL_EMPTY, ENV_ROWS * ENV_COLS);

  const Step& step = STEPS[(nowMs / CLAWD_POSE_MS) % STEP_COUNT];
  const int16_t col0 = HOME_COL + step.dx;
  const int16_t row0 = HOME_ROW + step.dy;

  for (uint8_t r = 0; r < SHELL_ROWS; ++r) {
    for (uint8_t c = 0; c < SHELL_W; ++c) {
      put(grid, row0 + r, col0 + SHELL_COL0 + c, CELL_SHELL);
    }
  }

  for (uint8_t i = 0; i < LEG_COUNT; ++i) {
    const bool lifted = (step.legLift == 1 && (i % 2) == 0) ||
                        (step.legLift == 2 && (i % 2) == 1);
    const uint8_t len = lifted ? LEG_LEN - 1 : LEG_LEN;
    for (uint8_t r = 0; r < len; ++r) {
      for (uint8_t c = 0; c < LEG_W; ++c) {
        put(grid, row0 + LEG_ROW0 + r, col0 + LEG_COL[i] + c, CELL_SHELL);
      }
    }
  }

  for (uint8_t r = 0; r < CLAW_LEN; ++r) {
    for (uint8_t c = 0; c < CLAW_W; ++c) {
      put(grid, row0 + CLAW_ROW0 + step.clawL + r, col0 + c, CELL_SHELL);
      put(grid, row0 + CLAW_ROW0 + step.clawR + r, col0 + COLS - CLAW_W + c,
          CELL_SHELL);
    }
  }

  // One pose step of blink, so it lands on the beat rather than cutting across
  // it. The two periods are deliberately not multiples of each other, so the
  // blink drifts around the dance instead of always hitting the same step.
  const bool blink = (nowMs % CLAWD_BLINK_EVERY_MS) < CLAWD_POSE_MS;
  for (uint8_t r = 0; r < EYE_SIZE; ++r) {
    for (uint8_t c = 0; c < EYE_SIZE; ++c) {
      if ((blink ? EYE_SHUT_L : EYE_OPEN)[r][c] == '#') {
        put(grid, row0 + EYE_ROW0 + r, col0 + EYE_L_COL + c, CELL_EYE);
      }
      if ((blink ? EYE_SHUT_R : EYE_OPEN)[r][c] == '#') {
        put(grid, row0 + EYE_ROW0 + r, col0 + EYE_R_COL + c, CELL_EYE);
      }
    }
  }
}

}  // namespace clawd
