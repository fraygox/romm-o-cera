"""Pills and chips: a row of pills that ends flush at its column's edge (Home's rail, filter
rows), a button chip with its pad glyph, a small status chip, a quiet tag, and a star rating."""
import math

import pygame
from rbui import theme
from rbui.theme import mix, S
from rbui.draw import button_glyph, circle, glyph_width, focus_box, marquee, measure, rrect, text

PILL_H, PILL_GAP, PILL_PAD = 38, 10, 16
MIN_PAD = 6                 # a tight row still keeps this much padding each side of a label


def fill_widths(naturals, width, gap=PILL_GAP, least=None):
    """Integer widths for items naturally `naturals` wide, gap apart, that together span exactly
    width: the slack (or the shortfall) is shared out evenly, none narrower than its floor in
    least (default: its natural width less 2 x (PILL_PAD - MIN_PAD), the padding a tight row
    gives up). When even the floors don't fit, every item shrinks in proportion (its label then
    marquees). The last takes the rounding, so the row always ends exactly at width."""
    n = len(naturals)
    if not n:
        return []
    room = width - gap * (n - 1)
    least = least if least is not None else [w - 2 * (PILL_PAD - MIN_PAD) for w in naturals]
    if sum(least) > room:
        want = [w * room / sum(naturals) for w in naturals]
    else:
        # share the slack evenly, then take any item pushed under its floor out and share again
        want, fixed = list(naturals), set()
        while True:
            free = [i for i in range(n) if i not in fixed]
            if not free:
                break
            extra = (room - sum(want[i] if i in fixed else naturals[i] for i in range(n))) / len(free)
            low = [i for i in free if naturals[i] + extra < least[i]]
            if not low:
                for i in free:
                    want[i] = naturals[i] + extra
                break
            for i in low:
                want[i], fixed = least[i], fixed | {i}
    out, x = [], 0.0
    for i, w in enumerate(want):
        out.append(int(round(width - round(x))) if i == n - 1 else int(round(x + w)) - int(round(x)))
        x += w + gap
    return out


def pill_rects(font, labels, x0, x1, y, h=PILL_H, gap=PILL_GAP, lead=None):
    """Rects for pills holding labels, from x0 ending flush at x1. lead: extra room before each
    label (a dot), per pill or one for all."""
    leads = lead if isinstance(lead, (list, tuple)) else [lead or 0] * len(labels)
    nat = [measure(font, lab) + 2 * PILL_PAD + leads[i] for i, lab in enumerate(labels)]
    out, x = [], x0
    for w in fill_widths(nat, x1 - x0, gap):
        out.append(pygame.Rect(x, y, w, h))
        x += w + gap
    return out


def pill_row(c, font, labels, x0, x1, y, focus=None, active=None, dots=None, h=PILL_H, gap=PILL_GAP,
             app=None, on_tap=None, bg=None):
    """A row of pills from x0 ending flush at x1. focus: the focused one (the focus box); active:
    the chosen one when focus is elsewhere (a lifted fill: the current filter). dots: a colour per
    pill (None for none) drawn before its label. on_tap(i) with app: tap targets. Returns the rects."""
    dots = dots or [None] * len(labels)
    rects = pill_rects(font, labels, x0, x1, y, h, gap, [16 if d else 0 for d in dots])
    for i, (lab, r) in enumerate(zip(labels, rects)):
        if app is not None and on_tap:
            app.hit(r, lambda i=i: on_tap(i))
        on, cur = i == focus, i == active
        if on:
            focus_box(c, r, h // 2)
        else:
            rrect(c, theme.PANEL_HI if cur else theme.PANEL if bg is None else bg, r, h // 2)
        col = theme.TEXT if on or cur else theme.DIM
        lead = 16 if dots[i] else 0
        room = r.width - lead - 2 * MIN_PAD - (4 if dots[i] else 0)
        tx = r.left + (MIN_PAD + 4 if dots[i] else MIN_PAD)
        if dots[i]:
            circle(c, dots[i], (tx + 4, r.centery), 5)
            tx += lead
        if not dots[i] and measure(font, lab) <= room:
            text(c, font, lab, r.center, col, "center")
        else:
            marquee(c, font, lab, (tx, r.centery), col, maxw=room, focused=on, anchor="midleft",
                    bg=theme.PANEL_HI if on or cur else theme.PANEL if bg is None else bg, slot=("pill", x0, y, i))
    return rects


def button_chip_width(f, token, label, maxw=None):
    """How wide button_chip draws token and label (at most maxw)."""
    w = 12 + glyph_width(f, token) + 8 + measure(f.small, label) + 16
    return int(min(w, maxw) if maxw else w)


def button_chip(c, f, pos, token, label, hot=False, maxw=None):
    """A small button with its pad glyph ([X] Compress all), 36 tall from pos (topleft); hot:
    lifted with the accent outline. Returns its rect."""
    r = pygame.Rect(pos[0], pos[1], button_chip_width(f, token, label, maxw), 36)
    rrect(c, theme.PANEL_HI if hot else theme.BG2, r, 18)
    if hot:
        rrect(c, theme.ACCENT, r, 18, 2)
    g = button_glyph(c, f, token, r.left + 12, r.centery - 11)
    text(c, f.small, label, (r.left + 12 + g + 8, r.centery), theme.TEXT if hot else theme.DIM, "midleft",
         maxw=r.right - 16 - (r.left + 12 + g + 8) + 1)
    return r


def status_chip(c, font, label, pos, color=None, anchor="topleft", h=24):
    """A small rounded chip: label in color on a tint of it (default the accent): "Playing",
    "On your backlog". anchor: topleft, topright, midleft or midright of pos. Returns its rect."""
    color = theme.ACCENT_HI if color is None else color
    w = int(measure(font, label)) + 20
    r = pygame.Rect(0, 0, w, h)
    setattr(r, anchor, pos)
    rrect(c, mix(theme.BG, color, 0.22), r, h // 2)
    text(c, font, label, r.center, color, "center")
    return r


def tag(c, font, label, pos, anchor="topleft", warn=False, color=None):
    """A quiet tag on a row or tile (roms/psx, a system name): recessed, square-ish, its label in
    color (default FAINT); warn tints it red (a platform with no Batocera system). Returns its rect."""
    w = int(measure(font, label)) + 12
    r = pygame.Rect(0, 0, w, 20)
    setattr(r, anchor, pos)
    rrect(c, mix(theme.BG, theme.RED, 0.22) if warn else theme.BG2, r, 6)
    text(c, font, label, r.center, theme.FAINT if color is None else color, "center")
    return r


def switch(c, rect, on):
    """An on/off switch: the accent track with the knob right when on, a LINE track with it left
    when off. Returns rect."""
    rect = pygame.Rect(rect)
    rrect(c, theme.ACCENT if on else theme.LINE, rect, rect.height // 2)
    k = rect.height // 2
    circle(c, theme.TEXT if on else theme.DIM, (rect.right - k if on else rect.left + k, rect.centery), k - 4)
    return rect


def switch_pill_width(font, label):
    return int(measure(font, label)) + 2 * PILL_PAD + 56


def switch_pill(c, font, label, rect, on, focused=False):
    """A pill holding a label and its switch at the right ("Only what's on this box  [o ]"): a
    setting's state shown on the screen it changes. Returns the switch's rect."""
    rect = pygame.Rect(rect)
    if focused:
        focus_box(c, rect, rect.height // 2)
    else:
        rrect(c, theme.PANEL, rect, rect.height // 2)
    sw = pygame.Rect(rect.right - PILL_PAD - 40, rect.centery - 11, 40, 22)
    text(c, font, label, (rect.left + PILL_PAD, rect.centery), theme.TEXT if on or focused else theme.DIM,
         "midleft", maxw=sw.left - 12 - rect.left - PILL_PAD)
    return switch(c, sw, on)


def player_dots(c, f, total, lit, pos, label=None, r=8, pitch=24):
    """Up to four dots for the players a game takes, `lit` of them green (tonight's party), then
    label beside them (e.g. "4 can play"). pos: the first dot's midleft. Returns the x after."""
    x, y = pos
    n = max(1, min(4, total))
    for i in range(n):
        circle(c, theme.GREEN if i < lit else theme.LINE, (x + r + i * pitch, y), r)
    x += r * 2 + (n - 1) * pitch
    if label:
        x = text(c, f.small, label, (x + 12, y), theme.DIM, "midleft").right
    return x


def stars(c, n, pos, out_of=5, r=8, pitch=19, color=None):
    """n of out_of five-point stars (drawn: no app font has ★), starting at pos (midleft), the
    rest as outlines. Returns the x just past the last."""
    x, y = pos
    for i in range(out_of):
        cx = x + r
        pts = []
        for k in range(10):
            rad = r if k % 2 == 0 else r * 0.42
            a = -math.pi / 2 + k * math.pi / 5
            pts.append((S(cx + rad * math.cos(a)), S(y + rad * math.sin(a))))
        if i < round(n):
            pygame.draw.polygon(c, theme.AMBER if color is None else color, pts)
        else:
            pygame.draw.polygon(c, theme.LINE, pts, max(1, S(1.5)))
        x += pitch
    return x - (pitch - 2 * r)
