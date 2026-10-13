"""Covers: the shared scaled-cover cache, a cover tile (focus ring, on-box tick, "+N" badge,
placeholder), a horizontal shelf of tiles, and 2x2 mosaics for collections.

Scaled copies live in app.scaled_covers ({rom id: [the cover, {variant: Surface}]}), so a
cover leaving memory (App.trim_covers / forget_covers) takes its copies with it; together
they stay under SCALED_SHARE of draw.COVER_BUDGET."""
import pygame
from rbui import draw, theme
from rbui.theme import mix, SR
from rbui.draw import card, installed_mark, measure, rrect, scroll_text, smooth_scale, surface_bytes, text, wrap

SCALED_SHARE = 0.5          # scaled copies may take this much of draw.COVER_BUDGET


def cover_variant(app, rom, key, make):
    """app.cover(rom) made into a variant by make(cover) -> Surface, cached under key until the
    cover changes or leaves memory. None while the cover loads (or there is none)."""
    img = app.cover(rom)
    if not img:
        return None
    rid = rom["id"]
    ent = app.scaled_covers.pop(rid, None)        # re-inserted last: the most recently drawn
    if not ent or ent[0] is not img:
        ent = [img, {}]
    app.scaled_covers[rid] = ent
    out = ent[1].get(key)
    if out is None:
        out = ent[1][key] = make(img)
        _trim(app, rid)
    return out


def _trim(app, keep):
    def size(ent):
        return sum(surface_bytes(s) for s in ent[1].values() if s is not ent[0])
    total = sum(size(e) for e in app.scaled_covers.values())
    for rid in list(app.scaled_covers):
        if total <= draw.COVER_BUDGET * SCALED_SHARE:
            break
        if rid != keep:
            total -= size(app.scaled_covers.pop(rid))


def crop_to(img, size):
    """img scaled to cover size (physical px) whole, the overflow cropped evenly."""
    iw, ih = img.get_size()
    f = max(size[0] / iw, size[1] / ih)
    scaled = smooth_scale(img, (max(1, int(iw * f + 0.5)), max(1, int(ih * f + 0.5))))
    crop = pygame.Rect((0, 0), size)
    crop.center = scaled.get_rect().center
    return scaled.subsurface(crop.clip(scaled.get_rect())).copy()


def fit_in(img, size):
    """img scaled to fit inside size (physical px), never cropped; itself when it already does."""
    iw, ih = img.get_size()
    f = min(size[0] / iw, size[1] / ih)
    out = (max(1, int(iw * f)), max(1, int(ih * f)))
    return img if abs(out[0] - iw) <= 1 and abs(out[1] - ih) <= 1 else smooth_scale(img, out)


def cover(c, app, rom, rect, fit=False, radius=8, label=True):
    """rom's cover in rect (logical): filling it, cropped (default), or with fit the whole cover,
    top-centred. While it loads (or when there's none) a quiet box with the game's name.
    Returns the rect the art took up (logical)."""
    rect = pygame.Rect(rect)
    pr = SR(rect)
    img = cover_variant(app, rom, ("fit" if fit else "crop", pr.size),
                        lambda im: (fit_in if fit else crop_to)(im, pr.size))
    if img:
        r = img.get_rect(midtop=pr.midtop)
        c.blit(img, r)
        return pygame.Rect(rect.left + (rect.width - round(r.width / theme.K)) // 2, rect.top,
                           round(r.width / theme.K), round(r.height / theme.K))
    placeholder(c, app.fonts, (rom.get("name") or "") if label else "", rect, radius)
    return rect


def placeholder(c, f, name, rect, radius=8):
    """A recessed box with the name in it, as far as it fits (none on a thumbnail)."""
    rrect(c, theme.BG2, rect, radius)
    if not name or rect.width < 60:
        return
    lines = wrap(f.tiny, name, rect.width - 12, max(1, (rect.height - 12) // 17))
    for j, ln in enumerate(lines):
        text(c, f.tiny, ln, (rect.left + 6, rect.top + 8 + j * 17), theme.FAINT)


def focus_ring(c, rect, radius=8):
    """The focus outline around art (covers, mosaics): the focus colour, just outside rect."""
    rrect(c, theme.ACCENT, pygame.Rect(rect).inflate(6, 6), radius + 2, 3)


def plus_badge(c, f, n, rect, corner="top"):
    """"+N" (more beyond the edge) on rect's top-right corner, inside it (corner "bottom":
    the bottom-right one, clear of a card's name)."""
    s = "+%d" % n
    w = int(measure(f.tiny, s)) + 14
    rr = pygame.Rect(rect.right - w - 4, rect.top + 4 if corner == "top" else rect.bottom - 26, w, 22)
    rrect(c, mix(theme.BG, theme.PANEL, 0.5), rr, 11)
    text(c, f.tiny, s, rr.center, theme.TEXT, "center")
    return rr


def cover_tile(c, app, rom, rect, focused=False, on_box=None, badge=0, radius=8):
    """A shelf tile: the cover filling rect; focused, lifted 4 px each way inside the focus ring;
    the green tick when the game is on the box (on_box None: ask the app); "+badge" on its
    corner. Returns the rect drawn (the lifted one when focused)."""
    r = pygame.Rect(rect).inflate(8, 8) if focused else pygame.Rect(rect)
    cover(c, app, rom, r, radius=radius)
    if focused:
        focus_ring(c, r, radius)
    if on_box is None:
        on_box = app.rom_status(rom)[0] == "installed"
    if on_box:
        k = 8 if r.width >= 70 else 6
        installed_mark(c, (r.right - k - 4, r.bottom - k - 4), k)
    if badge:
        plus_badge(c, app.fonts, badge, r)
    return r


def mosaic(c, app, roms, rect, gap=2, radius=8):
    """Up to four covers as a 2x2 mosaic filling rect; missing ones leave a quiet cell."""
    rect = pygame.Rect(rect)
    rrect(c, theme.BG2, rect, radius)
    cw, ch = (rect.width - gap) // 2, (rect.height - gap) // 2
    for i, rom in enumerate(roms[:4]):
        cell = pygame.Rect(rect.left + (i % 2) * (cw + gap), rect.top + (i // 2) * (ch + gap), cw, ch)
        cover(c, app, rom, cell, radius=4, label=False)


def mosaic_tile(c, app, roms, rect, focused=False, radius=8):
    """A collection's tile: its first four covers as a mosaic, lifted inside the ring when focused."""
    r = pygame.Rect(rect).inflate(8, 8) if focused else pygame.Rect(rect)
    mosaic(c, app, roms, r, radius=radius)
    if focused:
        focus_ring(c, r, radius)
    return r


def collection_card(c, app, name, roms, rect, focused=False, sub=""):
    """A wide collection tile: the mosaic at its left, the name (two lines) and sub beside it."""
    rect = pygame.Rect(rect)
    f = app.fonts
    card(c, rect, focused, 10)
    side = min(rect.height - 12, rect.width // 2)
    mosaic(c, app, roms, (rect.left + 6, rect.top + 6, side, rect.height - 12), radius=6)
    tx = rect.left + side + 18
    tw = rect.right - tx - 12
    if focused:                         # all of it, scrolling when it's longer than two lines
        scroll_text(c, f.body, name, (tx, rect.top + 12, tw, 48), theme.TEXT, line_h=24, bg=theme.PANEL_HI,
                    slot=("card",) + tuple(rect))
    else:
        for i, ln in enumerate(wrap(f.body, name, tw, 2)):
            text(c, f.body, ln, (tx, rect.top + 12 + i * 24), theme.DIM)
    if sub:
        text(c, f.small, sub, (tx, rect.bottom - 30), theme.FAINT, maxw=tw)
    return rect


# ------------------------------------------------------------------ the shelf

def shelf_fit(width, tile_w, gap):
    """(tiles that fit in width at least gap apart, the gap that sets the last one flush with
    the right edge). One tile when not even one fits."""
    per = max(1, int((width + gap) // (tile_w + gap)))
    return per, ((width - per * tile_w) / (per - 1) if per > 1 else 0.0)


class Shelf:
    """A row of tiles from x0 to x1: as many as fit, the last flush with x1 (the slack goes into
    the gaps), scrolled so the focused one is in view, a "+N" on the last when more lie beyond.
    Keep one per shelf on the screen: it remembers its scroll and the rects it drew (index_at)."""

    def __init__(self, tile=(90, 120), gap=12, badge="top"):
        self.tile, self.gap, self.badge = tile, gap, badge
        self.first = 0
        self.rects = []                 # [(item index, Rect)] as last laid out

    def follow(self, sel, n, per):
        """Scroll the least that brings sel into view; never past the end."""
        if sel is not None:
            if sel < self.first:
                self.first = sel
            elif sel >= self.first + per:
                self.first = sel - per + 1
        self.first = max(0, min(self.first, max(0, n - per)))

    def layout(self, x0, x1, y, n, sel=None):
        """[(item index, Rect)] of the tiles in view (sel brought into view first)."""
        tw, th = self.tile
        per, gap = shelf_fit(x1 - x0, tw, self.gap)
        self.follow(sel, n, per)
        self.per = per
        self.rects = [(i, pygame.Rect(x0 + round((i - self.first) * (tw + gap)), y, tw, th))
                      for i in range(self.first, min(n, self.first + per))]
        return self.rects

    def more(self, n):
        """How many lie beyond the right edge."""
        return max(0, n - self.first - len(self.rects))

    def index_at(self, pos):
        """The item under a point (logical), as last laid out; None between tiles."""
        return next((i for i, r in self.rects if r.collidepoint(pos)), None)

    def draw(self, c, app, items, x0, x1, y, sel=None, tile=None, on_tap=None, clip=None):
        """Lay out and draw items: tile(c, item, rect, focused) draws one (default: a cover_tile
        of a rom). on_tap(i) runs when a tile is tapped (clip: only where a tile shows inside
        that rect, for a shelf scrolled half out of its area). Returns the rects drawn."""
        rects = self.layout(x0, x1, y, len(items), sel)
        more = self.more(len(items))
        for k, (i, r) in enumerate(rects):
            tap = r if clip is None else r.clip(clip)
            if on_tap and tap.height > 0:
                app.hit(tap, lambda i=i: on_tap(i))
            on = i == sel
            if tile:
                drawn = tile(c, items[i], r, on)
            else:
                drawn = cover_tile(c, app, items[i], r, on)
            if more and k == len(rects) - 1:
                plus_badge(c, app.fonts, more, drawn or r, self.badge)
        return rects


def cover_capacity():
    """How many covers fit in memory at once (draw.COVER_BUDGET over one decoded cover at this
    scale): a screen showing more than this many at a time makes them load over and over."""
    pw, ph = SR(pygame.Rect((0, 0), draw.COVER_BOX)).size
    return int(draw.COVER_BUDGET // max(1, pw * ph * 4))


def tile_size(width, per, gap=12, aspect=4 / 3):
    """The tile (w, h) that fits per tiles gap apart in width, at the cover's aspect (h / w)."""
    w = int((width - gap * (per - 1)) // per)
    return w, int(round(w * aspect))

