#pragma once

#include <cstdint>

// Clawd, as pixel art, on a cell grid rather than as a bitmap.
//
// The grid is the whole point: movement is quantised to whole cells, so the
// screensaver can diff this frame's cells against the last one and repaint
// only what changed. Pixel-level drift would force a clear-and-redraw of the
// bounding box every frame, which tears at this size. It also happens to be
// how pixel art is supposed to move.
//
// This header knows what Clawd looks like at an instant. It does not know
// about the panel: display.cpp owns the glass and turns cells into rectangles.

namespace clawd {

constexpr uint8_t COLS = 20;  // claw tip to claw tip
constexpr uint8_t ROWS = 14;  // shell top to leg tips

// The dance envelope — the grid Clawd moves *within*. Four spare columns for
// the sideways shuffle, one spare row for the hop. Everything is addressed in
// envelope space so a cell that emptied because he moved off it still gets
// diffed and cleared.
constexpr uint8_t ENV_COLS = COLS + 4;
constexpr uint8_t ENV_ROWS = ROWS + 1;

constexpr uint8_t CELL_EMPTY = 0;
constexpr uint8_t CELL_SHELL = 1;
constexpr uint8_t CELL_EYE = 2;

// Fills `grid` with the pose for this instant. Pure — same nowMs, same grid.
void pose(uint32_t nowMs, uint8_t grid[ENV_ROWS][ENV_COLS]);

}  // namespace clawd
