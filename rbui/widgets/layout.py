"""List + panel: the list takes the width its rows need (up to LIST_MAX of the content), the
detail panel beside it takes the rest, out to the right edge. No dead strip, no row stretched
across the whole screen.

    lst, pan = split(need_width(f, rows))           # or split(676): a fixed need
    for i, r, on in self.row_rects(124, x=lst.left, w=lst.width):
        list_row(c, f, r, on, label, sub, status)
    p = Panel(c, f, pan, app=self.app, key=self.sel)
    p.actions([("A", "Games", fn)])                  # footer first: the body knows its room
    p.title(name, "3 games, 1.2 GB"); p.fact("Where", path); p.prose(why)
"""
import pygame
from rbui import theme
from rbui.theme import H, W
from rbui.draw import focus_box, hline, marquee, measure, meter, rich_text, rrect, scroll_text, text, wrap
from rbui.widgets.chips import button_chip, button_chip_width, tag as draw_tag
from rbui.widgets.factsheet import facts
from rbui.widgets.hero import VERB_H, verb_button

CONTENT = pygame.Rect(32, 80, W - 64, 576)     # under the top bar, above the hint bar
GAP = 20                                       # between the list and the panel
LIST_MAX = 0.62                                # the list's width, at most, of the content's
LIST_MIN = 0.45                                # and at least (short rows still read as a list)
PANEL_MIN = 340                                # the panel never gets narrower (4:3)
ROWS_TOP = CONTENT.top + 44                    # the first row's y, under list_head
ROW_PAD = 22                                   # a row's text inset; 48 with a tick box or mark


def split(need=None, content=CONTENT, gap=GAP):
    """(list rect, panel rect) in content: the list `need` wide, kept between LIST_MIN and
    LIST_MAX of the content (and leaving the panel PANEL_MIN); None: as wide as allowed. The
    panel takes the rest, so the two always span the content exactly."""
    content = pygame.Rect(content)
    hi = max(0, min(int(content.width * LIST_MAX), content.width - gap - PANEL_MIN))
    lo = min(hi, int(content.width * LIST_MIN))
    lw = hi if need is None else max(lo, min(hi, int(need)))
    lst = pygame.Rect(content.left, content.top, lw, content.height)
    return lst, pygame.Rect(lst.right + gap, content.top, content.right - lst.right - gap, content.height)


def need_width(f, rows, mark=False, limit=200):
    """The list width rows need: [(label, sub, status)], each part may be empty; measured over
    the first `limit` rows (a long list is capped by split anyway)."""
    lab = sub = st = 0
    for row in rows[:limit]:
        label, s, status = (tuple(row) + ("", "", ""))[:3]
        lab = max(lab, measure(f.row, label or ""))
        sub = max(sub, measure(f.small, s or ""))
        st = max(st, measure(f.small, status or ""))
    return (48 if mark else ROW_PAD) + max(lab, sub) + (32 + st if st else 0) + 20


def list_head(c, f, lst, title, note="", note_color=None, color=None):
    """The line above the rows: what the list is on the left, a count or total on the right."""
    room = max(lst.width // 2, lst.width - 36 - measure(f.h3, title))     # a short title leaves the note more
    nw = min(measure(f.small, note), room) if note else 0
    text(c, f.h3, title, (lst.left + 8, lst.top + 6), color or theme.TEXT, maxw=lst.width - 16 - (nw + 20 if nw else 0))
    if note:
        text(c, f.small, note, (lst.right - 8, lst.top + 10), note_color or theme.FAINT, "topright", maxw=room)


def list_row(c, f, r, on, label, sub="", status="", status_color=None, label_color=None, sub_color=None,
             mark=None, slot="row", tag=None, tag_color=None):
    """One row: the focus box, a mark at the left (mark(center) draws a tick box or dot and
    returns True when it did), the label with a grey line under it (the focused row's slide
    when too long), a short status right-aligned in the row and, left of it, a tag (chips.tag:
    a system, "queued"). Returns the text's x."""
    if on:
        focus_box(c, r)
    tx = r.left + (48 if mark and mark((r.left + 22, r.centery)) else ROW_PAD)
    right = r.right - 20
    if status:
        sr = text(c, f.small, status, (right, r.centery), status_color or theme.DIM, "midright",
                  maxw=r.width * 2 // 5)
        right = sr.left - 24
    if tag:
        right = draw_tag(c, f.tiny, tag, (right, r.centery), "midright", color=tag_color).left - 16
    lh, sh = f.row.get_height() / theme.K, f.small.get_height() / theme.K
    top = r.centery - ((lh + sh) / 2 if sub else lh / 2)
    marquee(c, f.row, label, (tx, top), label_color or (theme.TEXT if on else theme.DIM), maxw=right - tx,
            focused=on, slot=slot + "-label")
    if sub:
        marquee(c, f.small, sub, (tx, top + lh), sub_color or theme.FAINT, maxw=right - tx, focused=on,
                slot=slot + "-sub")
    return tx


class Panel:
    """A detail panel, drawn top down: each call puts its piece under the last. Footers
    (actions, foot) go at the bottom and shrink the room the body has, so draw them first."""
    PAD = 24

    def __init__(self, c, f, rect, app=None, key=None, slot="panel"):
        self.c, self.f, self.app, self.key, self.slot = c, f, app, key, slot
        self.rect = rect = pygame.Rect(rect)
        rrect(c, theme.PANEL, rect, 16)
        self.x, self.w = rect.left + self.PAD, rect.width - 2 * self.PAD
        self.y, self.bottom = rect.top + self.PAD - 2, rect.bottom - self.PAD
        self.n = 0

    def room(self):
        return self.bottom - self.y

    def space(self, h):
        self.y += h

    def _slot(self):
        self.n += 1
        return "%s-%d" % (self.slot, self.n)

    def title(self, s, sub=None, sub_color=None, lines=2, center=False):
        """The heading (wraps, up to `lines`, then scrolls) and a line under it; center: both
        centred in the panel (under a cover) while the heading fits its lines."""
        lh = 32
        rows = wrap(self.f.h2, s, self.w) if center else ()
        if center and len(rows) <= lines:
            for ln in rows:
                text(self.c, self.f.h2, ln, (self.x + self.w // 2, self.y), theme.TEXT, "midtop")
                self.y += lh
            self.y += 4
        else:
            self.y = scroll_text(self.c, self.f.h2, s, (self.x, self.y, self.w, lines * lh), theme.TEXT,
                                 key=self.key, line_h=lh, slot=self._slot()).bottom + 4
        if sub:
            self.line(sub, sub_color or theme.DIM, self.f.body, center, 30)
        self.y += 12

    def line(self, s, color=None, font=None, center=False, h=24):
        """One line (default f.small, DIM), centred or not; one too long slides."""
        font = font or self.f.small
        if center and measure(font, s) <= self.w:
            text(self.c, font, s, (self.x + self.w // 2, self.y), color or theme.DIM, "midtop")
        else:
            marquee(self.c, font, s, (self.x, self.y), color or theme.DIM, maxw=self.w, bg=theme.PANEL,
                    slot=self._slot())
        self.y += h

    def fact(self, label, value, color=None):
        """A grey label with its value under it (skipped when there's no value)."""
        if value in (None, "") or self.room() < 40:
            return
        self.facts([(label, value)], cols=1, colors={label: color} if color else None)

    def facts(self, pairs, cols=2, lines=None, strong=(), colors=None):
        """factsheet.facts in the panel's width: label-over-value cells in cols columns, scrolling
        when they're taller than `lines` fact rows (default: all the room left)."""
        h = self.room() if lines is None else min(self.room(), lines * 42)
        if h < 40:
            return
        used = facts(self.c, self.f, pairs, (self.x, self.y, self.w, h), cols, key=self.key, slot=self._slot(),
                     strong=strong, colors=colors)
        self.y = used.bottom + 6 if used.height else self.y

    def pair(self, label, value, color=None, label_color=None):
        """Label on the left, value on the right of one line (a long label ends in "…")."""
        vr = text(self.c, self.f.small, value, (self.x + self.w, self.y), color or theme.DIM, "topright",
                  maxw=self.w * 2 // 3)
        text(self.c, self.f.small, label, (self.x, self.y), label_color or theme.FAINT, maxw=vr.left - 16 - self.x)
        self.y += 24

    def bar(self, frac, color=None):
        """A progress bar across the panel: frac (0..1) of it in color, or parts as for meter."""
        meter(self.c, (self.x, self.y, self.w, 10), frac if isinstance(frac, list) else
              [(frac, theme.ACCENT if color is None else color)])
        self.y += 22

    def prose(self, s, label=None, color=None, lines=None, line_h=22):
        """A sentence or a few (label above it, if given), scrolling when it's longer than its
        room: `lines` lines at most, else all the room left."""
        if label:
            if self.room() < 44:
                return
            text(self.c, self.f.small, label, (self.x, self.y), theme.FAINT)
            self.y += 22
        h = self.room() if lines is None else min(self.room(), lines * line_h)
        if not s or h < line_h:
            return
        used = scroll_text(self.c, self.f.small, s, (self.x, self.y, self.w, h), color or theme.DIM,
                           key=self.key, line_h=line_h, slot=self._slot())
        self.y = used.bottom + 14

    def rule(self):
        hline(self.c, theme.LINE, (self.x, self.y), (self.x + self.w, self.y))
        self.y += 14

    def actions(self, items):
        """Footer chips: [(token, label, fn or None)] left to right, wrapping upwards; a chip
        with fn is tapped like its button."""
        lines, cur, cw = [], [], 0
        for tok, lab, fn in items:
            w = button_chip_width(self.f, tok, lab, self.w)
            if cur and cw + 8 + w > self.w:
                lines.append(cur)
                cur, cw = [], 0
            cur.append((tok, lab, fn))
            cw += (8 if cw else 0) + w
        if cur:
            lines.append(cur)
        y = self.bottom - 36
        for line in reversed(lines):
            x = self.x
            for tok, lab, fn in line:
                r = button_chip(self.c, self.f, (x, y), tok, lab, maxw=self.w)
                if fn and self.app:
                    self.app.hit(r, fn)
                x = r.right + 8
            y -= 44
        self.bottom = y + 44 - 16

    def foot(self, s, color=None, right="", right_color=None):
        """A rule and one line ({TOKEN} glyphs work) at the bottom; right: a second, shorter one
        at its right end (a server address)."""
        y = self.bottom - 22
        hline(self.c, theme.LINE, (self.x, y - 20), (self.x + self.w, y - 20))
        left = self.x + self.w
        if right:
            room = max(self.w * 2 // 5, self.w - 16 - measure(self.f.small, s))
            left = text(self.c, self.f.small, right, (self.x + self.w, y), right_color or theme.FAINT, "midright",
                        maxw=room).left - 16
        rich_text(self.c, self.f, self.f.small, s, (self.x, y), color or theme.DIM, "midleft", maxw=left - self.x)
        self.bottom = y - 36

    def verb(self, label, kind="go", detail="", glyph="A", frac=0.0, fn=None):
        """hero.verb_button across the panel's foot (above any foot line), tapped like A when fn
        is given. Returns its rect."""
        r = pygame.Rect(self.x, self.bottom - VERB_H, self.w, VERB_H)
        if fn and self.app:
            self.app.hit(r, fn)
        verb_button(self.c, self.f, r, label, kind, detail, glyph, frac)
        self.bottom = r.top - 16
        return r


def bubble_at(lst):
    """Where a list's letter bubble goes: over the list, not the panel."""
    return (lst.centerx, H // 2)


ACTION_OF = {"A": "ok", "X": "x", "Y": "y"}


def hint_actions(scr, tokens=("A", "X", "Y")):
    """The screen's own A/X/Y hints as Panel.actions chips, each tapped like its button: the
    panel's buttons always say what the hint bar says."""
    return [(tok, lab, lambda a=ACTION_OF[tok]: scr.action(a)) for tok, lab in scr.hints() if tok in tokens]
