"""Facts about a game (Size, Format, Region…) as label-over-value cells in columns, filling a
box or scrolling in it."""
import pygame
from rbui import theme
from rbui.draw import scroll_box, SCROLL_BAR, text, wrapped

LABEL_H, LINE_H, CELL_GAP, COL_GAP = 17, 20, 5, 16


def facts_layout(f, pairs, width, cols=2):
    """Cells for (label, value) pairs, row by row in cols columns across width; an empty value
    drops its fact; a long value wraps in its column. Returns ([(label, value lines, x, y)]
    relative to the box, total height)."""
    pairs = [(k, str(v)) for k, v in pairs if v not in (None, "")]
    colw = (width - COL_GAP * (cols - 1)) / cols
    cells, y = [], 0
    for r in range(0, len(pairs), cols):
        row_h = 0
        for k, (label, value) in enumerate(pairs[r:r + cols]):
            lines = wrapped(f.cond, value, int(colw)) or [""]
            cells.append((label, lines, int(round(k * (colw + COL_GAP))), y))
            row_h = max(row_h, LABEL_H + len(lines) * LINE_H + CELL_GAP)
        y += row_h
    return cells, y


def facts(c, f, pairs, rect, cols=2, key=None, slot=None, bg=None, strong=()):
    """The facts in rect (logical): label (faint) over value (labels in strong: the value in TEXT,
    the rest DIM). When they're taller than rect they scroll in it like prose does (scroll_box).
    bg: the colour behind (default PANEL). Returns the rect they took up."""
    rect = pygame.Rect(rect)
    cells, total = facts_layout(f, pairs, rect.width, cols)
    if total > rect.height:
        cells, total = facts_layout(f, pairs, rect.width - SCROLL_BAR, cols)

    def paint(top):
        for label, lines, x, y in cells:
            text(c, f.small, label, (rect.left + x, top + y), theme.FAINT)
            for j, ln in enumerate(lines):
                text(c, f.cond, ln, (rect.left + x, top + y + LABEL_H + j * LINE_H),
                     theme.TEXT if label in strong else theme.DIM)
    return scroll_box(c, rect, total, paint, (tuple(cells), key), slot or ("facts",) + tuple(rect), bg)
