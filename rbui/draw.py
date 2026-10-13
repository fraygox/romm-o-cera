"""Fonts, drawing primitives, text (glyph fallback, the rendered-text cache, wrapping) and
prose with inline controller glyphs.

TEXT_BUDGET / COVER_BUDGET are set at start-up (startup.fit_memory_budgets): read them
as draw.TEXT_BUDGET from other modules."""
import os
import re
import threading
from collections import OrderedDict

import pygame
import rommlib
from rbui import APP_DIR, theme
from rbui.theme import LR, S, SR
from rbui.pads import pad_face, TOKENS


FONT_DIRS = ["/usr/share/fonts/dejavu", "/usr/share/fonts/dejavu-sans-fonts",
             "/usr/share/fonts/truetype/dejavu", os.path.join(APP_DIR, "fonts")]


CJK_FONTS = ["/usr/share/emulationstation/resources/DroidSansFallbackFull.ttf",
             "/usr/share/fonts/truetype/droid/DroidSansFallback.ttf",
             "/usr/share/fonts/google-droid-sans-fonts/DroidSansFallbackFull.ttf"]
# Hangul: Droid has none. EmulationStation's own Korean font first (its weight
# matches Rubik's), Nanum otherwise. Batocera's Noto CJK fonts are variable
# fonts that SDL_ttf draws at their thinnest weight, so they aren't used.
HANGUL_FONTS = ["/usr/share/emulationstation/resources/PyeojinGothic-Medium.ttf",
                "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
                "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"]
HANGUL = ((0x1100, 0x11FF), (0x3130, 0x318F), (0xA960, 0xA97F), (0xAC00, 0xD7FF))
FALLBACK = {}   # Font -> [same-size Fonts tried in turn for characters it doesn't have]
FONT_PATH = {}  # Font -> the file it was made from (coverage is per file, at any size)
FONT_CACHE = {}  # (file, size) -> Font: one open file each, however often Fonts() is made


def find_font(name):
    for d in FONT_DIRS:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    return None


class Fonts:
    def __init__(self):
        fdir = os.path.join(APP_DIR, "fonts")
        bundled = lambda n: os.path.join(fdir, n) if os.path.exists(os.path.join(fdir, n)) else None  # noqa: E731
        dv = find_font("DejaVuSans.ttf")
        reg = bundled("Rubik-Regular.ttf") or dv
        med = bundled("Rubik-Medium.ttf") or find_font("DejaVuSans-Bold.ttf") or reg
        bold = bundled("Rubik-SemiBold.ttf") or med
        # for characters Rubik lacks: Greek, arrows, music and other symbols; Chinese
        # and Japanese; Korean (game titles and descriptions come in every script)
        chain = [p for p in (dv, next((p for p in CJK_FONTS if os.path.exists(p)), None),
                             next((p for p in HANGUL_FONTS if os.path.exists(p)), None)) if p and p != reg]
        def font(path, size):
            if (path, size) not in FONT_CACHE:
                f = FONT_CACHE[(path, size)] = pygame.font.Font(path, size)
                FONT_PATH[f] = path
            return FONT_CACHE[(path, size)]

        def mk(path, size):
            size = max(6, int(round(size * theme.K)))
            f = font(path, size)
            FALLBACK[f] = [font(p, size) for p in chain]
            return f
        self.h1 = mk(bold, 36)
        self.h2 = mk(bold, 25)
        self.h3 = mk(med, 20)
        self.body = mk(reg, 19)
        self.row = mk(reg, 21)
        self.small = mk(reg, 18)
        self.cond = mk(reg, 18)
        self.tiny = mk(med, 16)
        self.key = mk(med, 24)
        self.btn = mk(bold, 14)


# ------------------------------------------------------------------ drawing

def rrect(surf, color, rect, radius=10, width=0):
    pygame.draw.rect(surf, color, SR(rect), max(1, S(width)) if width else 0,
                     border_radius=S(radius))


def focus_box(surf, rect, radius=10):
    """The one focus treatment: lifted fill + violet outline."""
    rrect(surf, theme.PANEL_HI, rect, radius)
    rrect(surf, theme.ACCENT, rect, radius, 2)


def circle(surf, color, center, radius, width=0):
    pygame.draw.circle(surf, color, (S(center[0]), S(center[1])), max(1, S(radius)),
                       max(1, S(width)) if width else 0)


def hline(surf, color, a, b, width=1):
    pygame.draw.line(surf, color, (S(a[0]), S(a[1])), (S(b[0]), S(b[1])), max(1, S(width)))


def polyline(surf, color, pts, width=1):
    pygame.draw.lines(surf, color, False, [(S(x), S(y)) for x, y in pts], max(1, S(width)))


def arc(surf, color, rect, a0, a1, width=1):
    pygame.draw.arc(surf, color, SR(rect), a0, a1, max(1, S(width)))


def installed_mark(surf, center, radius=9):
    """The green tick for "on this box"."""
    x, y = center
    k = radius / 9
    circle(surf, theme.GREEN, center, radius)
    polyline(surf, theme.BG, [(x - 4 * k, y), (x - k, y + 3 * k), (x + 4 * k, y - 3 * k)], 2)


def _wide(ch):
    return ord(ch) >= 0x2E80        # CJK, kana, hangul, fullwidth forms


_HAS = {}       # (font file, char) -> it has a glyph for it
_PROBE = {}     # font file -> (a small Font of it, its "missing glyph" pixels)


def _has_glyph(path, ch):
    """SDL_ttf draws a character a font lacks as that font's missing-glyph box
    (and its metrics don't tell): compare with an unassigned code point."""
    k = (path, ch)
    if k not in _HAS:
        if path in HANGUL_FONTS and not any(a <= ord(ch) <= b for a, b in HANGUL):
            _HAS[k] = False                 # only Hangul from these (their other glyphs aren't Rubik-like)
            return False
        if path not in _PROBE:
            f = pygame.font.Font(path, 16)
            _PROBE[path] = (f, pygame.image.tobytes(f.render("\u0378", True, (255,) * 3, (0,) * 3), "RGB"))
        f, missing = _PROBE[path]
        try:
            _HAS[k] = pygame.image.tobytes(f.render(ch, True, (255,) * 3, (0,) * 3), "RGB") != missing
        except pygame.error:                # zero width (a combining mark): nothing to draw, no box either
            _HAS[k] = True
    return _HAS[k]


def _font_for(font, chain, ch):
    if ch.isascii() or ch.isspace() or _has_glyph(FONT_PATH[font], ch):
        return font
    # the first fallback that has it; none does (an emoji): the font's own box
    return next((f for f in chain if _has_glyph(FONT_PATH[f], ch)), font)


def _runs(font, s):
    """Split s into (font, chunk) runs: each character in the first font of
    font + FALLBACK[font] that has it."""
    chain = FALLBACK.get(font)
    if not chain or s.isascii():
        return [(font, s)]
    out = []
    for ch in s:
        f = _font_for(font, chain, ch)
        if out and out[-1][0] is f:
            out[-1] = (f, out[-1][1] + ch)
        else:
            out.append((f, ch))
    return out


def measure(font, s):
    """width in logical units"""
    return sum(f.size(chunk)[0] for f, chunk in _runs(font, s)) / theme.K


# Memory: rendered text and decoded covers are kept in RAM by size, not by
# count: at 4K one line of text is ~180 KB and one cover several MB, so a count
# limit let a long scroll reach gigabytes. What's dropped comes back from the
# fonts / the disk cache, in milliseconds.
TEXT_BUDGET = 64 << 20          # rendered strings (a full screen of 4K text is ~25 MB)
COVER_BUDGET = 64 << 20         # decoded covers, already shrunk to what's drawn (~1.8 MB each at 4K)
COVER_ENTRIES = 400            # covers tracked at once, loading and "no cover" ones included
COVER_DECODES = threading.BoundedSemaphore(2)   # a 4000 px cover is ~100 MB while it decodes


def surface_bytes(img):
    return img.get_width() * img.get_height() * img.get_bytesize() if isinstance(img, pygame.Surface) else 0


_TEXT_CACHE = OrderedDict()     # (font, text, colour) -> Surface, least recently drawn first
_TEXT_BYTES = [0]


def render_cached(font, s, color):
    key = (id(font), s, color)
    img = _TEXT_CACHE.get(key)
    if img is not None:
        _TEXT_CACHE.move_to_end(key)
        return img
    img = _TEXT_CACHE[key] = render(font, s, color)
    _TEXT_BYTES[0] += surface_bytes(img)
    while _TEXT_BYTES[0] > TEXT_BUDGET and len(_TEXT_CACHE) > 1:
        _TEXT_BYTES[0] -= surface_bytes(_TEXT_CACHE.popitem(last=False)[1])
    return img


def render(font, s, color):
    runs = _runs(font, s)
    if len(runs) == 1:
        return runs[0][0].render(runs[0][1], True, color)
    imgs = [(f, f.render(chunk, True, color)) for f, chunk in runs]
    h = font.get_height()
    out = pygame.Surface((sum(i.get_width() for _, i in imgs), h), pygame.SRCALPHA)
    x = 0
    for f, i in imgs:
        # align baselines
        out.blit(i, (x, font.get_ascent() - f.get_ascent()))
        x += i.get_width()
    return out


FONTS = {}            # the app's Fonts, so text() can draw {TOKEN} glyphs anywhere


def text(surf, font, s, pos, color=None, anchor="topleft", maxw=None):
    color = theme.TEXT if color is None else color
    s = s or ""
    if "{" in s and FONTS.get("f") and GLYPH_RE.search(s):
        # prose naming a control: same glyphs as the hint bar, whoever draws it
        f = FONTS["f"]
        w = rich_width(f, font, s) if not maxw else min(maxw, rich_width(f, font, s))
        lh = font.get_height() / theme.K
        x = {"topright": pos[0] - w, "midright": pos[0] - w, "center": pos[0] - w / 2,
             "midtop": pos[0] - w / 2, "midbottom": pos[0] - w / 2}.get(anchor, pos[0])
        top = {"center": pos[1] - lh / 2, "midleft": pos[1] - lh / 2, "midright": pos[1] - lh / 2,
               "bottomleft": pos[1] - lh, "bottomright": pos[1] - lh, "midbottom": pos[1] - lh}.get(anchor, pos[1])
        return rich_text(surf, f, font, s, (x, top), color, maxw=maxw)
    if maxw is not None and measure(font, s) > maxw:
        while s and measure(font, s + "…") > maxw:
            s = s[:-1]
        s = s.rstrip() + "…"
    img = render_cached(font, s, color)
    r = img.get_rect(**{anchor: (S(pos[0]), S(pos[1]))})
    surf.blit(img, r)
    return LR(r)


def wrap(font, s, width, max_lines=None):
    lines = []
    for para in (s or "").replace("\r", "").split("\n"):
        cur = ""
        for w in para.split():
            t = (cur + " " + w).strip()
            if measure(font, t) <= width:
                cur = t
                continue
            if cur:
                lines.append(cur)
            cur = w
            while measure(font, cur) > width and len(cur) > 1:   # unbreakable (CJK) run
                k = len(cur)
                while k > 1 and measure(font, cur[:k]) > width:
                    k -= 1
                lines.append(cur[:k])
                cur = cur[k:]
        lines.append(cur)
    # drop blank paragraph gaps beyond one
    lines = [l for i, l in enumerate(lines) if l or (i and lines[i - 1])]
    while lines and not lines[-1]:
        lines.pop()
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and measure(font, last + "…") > width:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines


def glyph_width(fonts, token):
    kind, lab, _ = pad_face(token)
    if kind == "dpad":
        return 22
    if kind == "arrows":
        return 40
    return 22 if kind in ("letter", "shape") else int(measure(fonts.btn, lab)) + 14


def button_glyph(surf, fonts, label, x, y):
    """Pad-button glyph in the active controller style; returns its width.
    The only place controls are drawn: hints, prose and menus all come here."""
    kind, lab, col = pad_face(label)
    cx, cy = x + 11, y + 11
    if kind == "dpad":                  # a D-pad, its left/right arms lit
        rrect(surf, theme.LINE, (cx - 3.5, cy - 10, 7, 20), 2)
        rrect(surf, theme.LINE, (cx - 11, cy - 3.5, 22, 7), 2)
        rrect(surf, theme.TEXT, (cx - 11, cy - 3.5, 7, 7), 2)
        rrect(surf, theme.TEXT, (cx + 4, cy - 3.5, 7, 7), 2)
        return 22
    if kind == "arrows":                # ← → keys
        rrect(surf, col, (x, y + 1, 40, 20), 10)
        polyline(surf, theme.BG, [(x + 15, y + 6), (x + 9, y + 11), (x + 15, y + 16)], 2)
        polyline(surf, theme.BG, [(x + 25, y + 6), (x + 31, y + 11), (x + 25, y + 16)], 2)
        return 40
    if kind == "letter":
        circle(surf, col, (cx, cy), 11)
        text(surf, fonts.btn, lab, (cx, cy), theme.BG, "center")
        return 22
    if kind == "shape":                 # PlayStation symbols, drawn (no font has them all)
        circle(surf, theme.BG2, (cx, cy), 11)
        circle(surf, col, (cx, cy), 11, 2)
        if lab == "cross":
            hline(surf, col, (cx - 4, cy - 4), (cx + 4, cy + 4), 2)
            hline(surf, col, (cx - 4, cy + 4), (cx + 4, cy - 4), 2)
        elif lab == "circle":
            circle(surf, col, (cx, cy), 5, 2)
        elif lab == "square":
            rrect(surf, col, (cx - 4.5, cy - 4.5, 9, 9), 1, 2)
        else:
            polyline(surf, col, [(cx, cy - 5), (cx + 5, cy + 4), (cx - 5, cy + 4), (cx, cy - 5)], 2)
        return 22
    w = int(measure(fonts.btn, lab)) + 14
    rrect(surf, col, (x, y + 1, w, 20), 10)
    text(surf, fonts.btn, lab, (x + w // 2, y + 11), theme.BG, "center")
    return w


def as_rgba(img):
    """Covers can be palette / 8-bit images (GIF-ish PNGs); smoothscale only
    takes 24/32-bit surfaces, so normalise once at load."""
    if img.get_bitsize() in (24, 32):
        return img
    out = pygame.Surface(img.get_size(), pygame.SRCALPHA, 32)
    out.blit(img, (0, 0))
    return out


COVER_BOX = (192, 256)          # the game panel's cover (logical): the largest any screen draws


def cover_fit(img):
    """A decoded cover scaled once to the game panel's cover box, the largest
    any screen draws it (Home's smaller shelf crops from it). RomM's large
    covers are often 1000-4000 px: kept whole they cost ~10x the RAM, and a
    smaller one needed a second, enlarged copy for the panel."""
    iw, ih = img.get_size()
    pw, ph = SR(pygame.Rect((0, 0), COVER_BOX)).size
    f = min(pw / iw, ph / ih)
    size = (max(1, int(iw * f)), max(1, int(ih * f)))
    return img if size == (iw, ih) else smooth_scale(img, size)


def smooth_scale(img, size):
    try:
        return pygame.transform.smoothscale(img, size)
    except ValueError:                    # any other odd format: plain scale, never crash
        return pygame.transform.scale(img, size)


def rom_title(rom):
    """The rom's name, plus its disc number when RomM gives every disc of a
    game the same name ("Lorem Odyssey" ×3)."""
    name = rom.get("name") or rom.get("fs_name_no_ext") or ""
    d = rommlib.disc_info(rom.get("fs_name_no_ext") or "")
    if d and not rommlib.disc_info(name):
        return "%s (Disc %d)" % (name, d[1])
    return name


GLYPH_RE = re.compile(r"\{(%s)\}" % "|".join(re.escape(t) for t in TOKENS))


def rich_width(fonts, font, s):
    """Width of a line with {TOKEN} glyphs, as rich_text draws it."""
    parts = GLYPH_RE.split(s)
    w = 0
    for i, part in enumerate(parts):
        if i % 2:
            w += glyph_width(fonts, part) + 7
        else:
            if i > 0:
                part = part.lstrip()
            if i + 1 < len(parts):
                part = part.rstrip()
            if part:
                w += measure(font, part) + (7 if i + 1 < len(parts) else 0)
    return w


def rich_lines(fonts, font, s, width, max_lines=0):
    """Word-wrap prose that may hold {TOKEN} glyphs (a glyph never splits)."""
    out = []
    for para in s.split("\n"):
        cur = ""
        for word in re.split(r"(\{[^}]+\} ?|\s+)", para):
            if not word or word.isspace():
                if cur and not cur.endswith(" "):
                    cur += " "
                continue
            trial = cur + word
            if cur and rich_width(fonts, font, trial.rstrip()) > width:
                out.append(cur.rstrip())
                cur = word
            else:
                cur = trial
        out.append(cur.rstrip())
    if max_lines and len(out) > max_lines:
        out = out[:max_lines]
        while out[-1] and rich_width(fonts, font, out[-1] + "…") > width:
            out[-1] = out[-1][:-1]
        out[-1] = out[-1].rstrip() + "…"
    return out


def rich_text(surf, fonts, font, s, pos, color=None, anchor="topleft", maxw=None):
    """Text with inline pad glyphs: "Press {START} to see all results" draws
    the same glyph the hint bar uses, in the active controller style, so
    prose and hints always match. Supports topleft/midleft anchors; maxw
    shortens the text (never a glyph) with "…"."""
    color = theme.TEXT if color is None else color
    if maxw and rich_width(fonts, font, s) > maxw:
        s = rich_lines(fonts, font, s, maxw, 1)[0]
    parts = GLYPH_RE.split(s)            # text, token, text, token, ...
    lh = font.get_height() / theme.K
    x, y = pos
    top = y - lh / 2 if anchor == "midleft" else y
    cy = top + lh / 2
    for i, part in enumerate(parts):
        if i % 2:
            x += button_glyph(surf, fonts, part, x, cy - 11) + 7
        else:
            if i > 0:
                part = part.lstrip()      # the glyph's own gap replaces the space
            if i + 1 < len(parts):
                part = part.rstrip()
            if part:
                r = text(surf, font, part, (x, cy), color, "midleft")
                x = r.right + (7 if i + 1 < len(parts) else 0)
    return pygame.Rect(pos[0], top, x - pos[0], lh)


def card(surf, rect, on=False, radius=12, bg=None):
    """A row or tile's box: focused, the focus box; else a plain fill (default PANEL)."""
    if on:
        focus_box(surf, rect, radius)
    else:
        rrect(surf, theme.PANEL if bg is None else bg, rect, radius)


def chevron(surf, color, center, side, span=12, depth=6, width=2):
    """A chevron pointing to side ("left", "right", "up", "down"): a choice's ‹ ›, "more above"."""
    cx, cy = center
    d = 1 if side in ("right", "down") else -1
    s, k = span / 2, depth / 2
    if side in ("left", "right"):
        pts = [(cx - k * d, cy - s), (cx + k * d, cy), (cx - k * d, cy + s)]
    else:
        pts = [(cx - s, cy - k * d), (cx, cy + k * d), (cx + s, cy - k * d)]
    polyline(surf, color, pts, width)


def scrollbar(surf, rect, shown, pos, color=None, min_h=24, radius=2):
    """Where a list is: a LINE track (rect, logical) with a thumb `shown` (0..1) of it tall at
    `pos` (0 top .. 1 bottom), in color (default ACCENT). Draw it only when the list overflows."""
    track = pygame.Rect(rect)
    rrect(surf, theme.LINE, track, radius)
    th = min(track.height, max(min_h, int(track.height * min(1.0, shown))))
    y = track.top + int((track.height - th) * max(0.0, min(1.0, pos)))
    rrect(surf, theme.ACCENT if color is None else color, (track.left, y, track.width, th), radius)


def progress_bar(surf, rect, frac, color=None):
    """A rounded bar on a LINE track, frac (0..1) of it filled in color (default ACCENT)."""
    meter(surf, rect, [(frac, theme.ACCENT if color is None else color)])


def _stripes(surf, prect, color, step=9):
    old = surf.get_clip()
    surf.set_clip(prect.clip(old) if old else prect)
    for x in range(prect.left - prect.height, prect.right, max(2, S(step))):
        pygame.draw.line(surf, color, (x, prect.bottom), (x + prect.height, prect.top), max(1, S(2)))
    surf.set_clip(old)


def hatch(surf, rect, color, step=9):
    """Diagonal stripes over rect (logical): space that's set aside, not used."""
    _stripes(surf, SR(rect), color, step)


_MASKS = OrderedDict()      # physical size -> the rounded-bar alpha mask meter() clips to


def meter(surf, rect, parts, track=None, least=None, reserve=0.0):
    """A bar of segments, left to right: parts = [(fraction of the whole, colour)], on a track
    (default LINE), the whole clipped to one rounded bar; the last segment's end is rounded.
    least: the narrowest a segment shows (logical, default the bar's height). reserve: the last
    fraction of the track striped in LINE (space downloads never use). Progress, Backlog's
    "fits now / waits", the drive's space by kind."""
    pr = SR(rect)
    w, h = pr.size
    if w <= 0 or h <= 0:
        return
    bar = pygame.Surface(pr.size, pygame.SRCALPHA)
    bar.fill(theme.LINE if track is None else track)
    if reserve > 0:
        kw = int(w * min(1.0, reserve))
        _stripes(bar, pygame.Rect(w - kw, 0, kw, h), theme.LINE)
    least = h if least is None else S(least)
    shown = [(frac, col) for frac, col in parts if frac > 0]
    cx = 0
    for k, (frac, col) in enumerate(shown):
        if cx >= w:
            break
        sw = min(w - cx, max(least, int(round(w * min(1.0, frac)))))
        cap = h // 2 if k == len(shown) - 1 else -1
        pygame.draw.rect(bar, col, (cx, 0, sw, h), border_top_right_radius=cap, border_bottom_right_radius=cap)
        cx += sw
    mask = _MASKS.get(pr.size)
    if mask is None:
        mask = _MASKS[pr.size] = pygame.Surface(pr.size, pygame.SRCALPHA)
        pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(), border_radius=h // 2)
        if len(_MASKS) > 32:
            _MASKS.popitem(last=False)
    bar.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    surf.blit(bar, pr.topleft)


def state_stripe(surf, rect, color):
    """A row's state as a 4 px colour bar down its left edge, inset from its rounded corners."""
    x, y, w, h = rect
    inset = min(12, h // 4)
    rrect(surf, color, (x, y + inset, 4, h - 2 * inset), 2)


# ------------------------------------------------------------------ text that scrolls in its box
#
# Text longer than the box it's drawn in is never cut: scroll_text() moves a block of prose up
# by itself (a pause at the top, a calm glide, a pause at the end, back to the top) and marquee()
# slides the focused row's one-line label sideways. Both run on the app's frame clock (NOW,
# which App.run sets to app.t every frame), start over when their text or key changes, and
# don't move at all when the text fits.

NOW = 0.0                   # the app's frame clock, seconds (App.run: draw.NOW = app.t)
SCROLL_WAIT = 2.0           # seconds still at the top, and again at the end, before moving on
SCROLL_SPEED = 18           # logical px a second up: a calm reading pace (~ a line every 1.4 s)
MARQUEE_WAIT = 1.5
MARQUEE_SPEED = 40          # logical px a second sideways

_SCROLLS = {}               # slot -> [signature, start time, last drawn]
_WRAPS = OrderedDict()      # (font, text, width) -> wrapped lines
_FADES = OrderedDict()      # (colour, w, h, side) -> a small alpha gradient Surface


def scroll_offset(elapsed, overflow, speed=SCROLL_SPEED, wait=SCROLL_WAIT):
    """How far (logical px) text `overflow` px longer than its box has moved after `elapsed`
    seconds: still for `wait`, then `speed` px/s to the end, still for `wait`, then back to
    0 and round again. 0 when it fits."""
    if overflow <= 0 or elapsed <= 0:
        return 0.0
    run = overflow / float(speed)
    t = elapsed % (wait + run + wait)
    if t < wait:
        return 0.0
    return min(float(overflow), (t - wait) * speed)


def scroll_elapsed(slot, sig, t=None):
    """Seconds since what `slot` shows last changed (its signature) or came back on screen.
    A slot not drawn for half a second starts over: a dialog opened again starts at the top."""
    now = NOW if t is None else t
    st = _SCROLLS.get(slot)
    if st is None or st[0] != sig or now - st[2] > 0.5 or now < st[1]:
        st = _SCROLLS[slot] = [sig, now, now]
        if len(_SCROLLS) > 64:                  # forget slots that went off screen
            for k in [k for k, v in _SCROLLS.items() if now - v[2] > 1.0 and k != slot]:
                del _SCROLLS[k]
    st[2] = now
    return now - st[1]


def wrapped(font, s, width):
    """Lines of s wrapped to width, {TOKEN} glyphs kept whole; cached per (font, text, width)."""
    key = (id(font), s, width)
    lines = _WRAPS.get(key)
    if lines is None:
        if FONTS.get("f") and "{" in s and GLYPH_RE.search(s):
            lines = rich_lines(FONTS["f"], font, s, width)
        else:
            lines = wrap(font, s, width)
        _WRAPS[key] = lines
        if len(_WRAPS) > 256:
            _WRAPS.popitem(last=False)
    else:
        _WRAPS.move_to_end(key)
    return lines


def _fade(surf, color, prect, side):
    """Fade the text at one edge of prect (physical) into color: "top", "bottom", "left", "right"."""
    if prect.width <= 0 or prect.height <= 0:
        return
    key = (tuple(color), prect.width, prect.height, side)
    img = _FADES.get(key)
    if img is None:
        img = pygame.Surface(prect.size, pygame.SRCALPHA)
        n = prect.height if side in ("top", "bottom") else prect.width
        for i in range(n):
            a = int(255 * (1 - (i + 0.5) / n) ** 1.5)       # solid at the edge, clear inwards
            j = i if side in ("top", "left") else n - 1 - i
            if side in ("top", "bottom"):
                img.fill(tuple(color) + (a,), (0, j, prect.width, 1))
            else:
                img.fill(tuple(color) + (a,), (j, 0, 1, prect.height))
        _FADES[key] = img
        if len(_FADES) > 24:
            _FADES.popitem(last=False)
    surf.blit(img, prect)


def _clip_to(surf, prect):
    old = surf.get_clip()
    surf.set_clip(prect.clip(old))
    return old


SCROLL_BAR = 10             # logical px a scrolling box keeps free on its right for its position bar


def scroll_box(surf, rect, total, paint, sig, slot=None, bg=None, t=None, edge=16):
    """The scroller for any content: paint(top) draws `total` logical px of it from y=top, clipped
    to rect (logical). Taller than rect, it scrolls up by itself (scroll_offset) with its edges
    fading into bg (default PANEL) and a thin position bar in the rightmost SCROLL_BAR px (leave
    them free); otherwise it stays still. sig: what, when it changes, starts the scroll over.
    slot: which scroller this is (default: the rect); edge: how deep the fades are (logical px).
    Returns the rect the content took up."""
    bg = theme.PANEL if bg is None else bg
    rect = pygame.Rect(rect)
    over = max(0.0, total - rect.height)
    off = scroll_offset(scroll_elapsed(slot or ("v",) + tuple(rect), sig, t), over) if over > 0.5 else 0.0
    prect = SR(rect)
    old = _clip_to(surf, prect)
    paint(rect.top - off)
    if over > 0.5:
        fh = max(2, S(min(edge, rect.height / 4)))
        if off > 0.5:
            _fade(surf, bg, pygame.Rect(prect.left, prect.top, prect.width, fh), "top")
        if off < over - 0.5:
            _fade(surf, bg, pygame.Rect(prect.left, prect.bottom - fh, prect.width, fh), "bottom")
        scrollbar(surf, (rect.right - 3, rect.top + 2, 3, rect.height - 4), rect.height / total, off / over,
                  theme.FAINT, 12, 1)
    surf.set_clip(old)
    return pygame.Rect(rect.left, rect.top, rect.width, int(min(total, rect.height) + 0.999))


def scroll_text(surf, font, s, rect, color=None, key=None, line_h=None, bg=None, slot=None, t=None):
    """Prose wrapped into rect (logical), never drawn outside it: scroll_box's scroller, so when
    it's taller than rect it scrolls up by itself; when it fits it stays still.
    s: a string (\\n splits paragraphs; {TOKEN} glyphs work) or a list of paragraphs.
    key: anything else that should start the scroll over when it changes (the selection).
    bg: the colour behind it (default PANEL). slot: which scroller this is (default: the rect).
    Returns the rect the text took up (its height is the text's, at most rect's); scroll_fits()
    tells beforehand whether it will move."""
    color = theme.TEXT if color is None else color
    rect = pygame.Rect(rect)
    s = "\n".join(s) if isinstance(s, (list, tuple)) else (s or "")
    lh = line_h or (font.get_height() / theme.K + 4)
    lines = wrapped(font, s, rect.width)
    if len(lines) * lh - rect.height > 0.5:            # room for the position bar
        lines = wrapped(font, s, rect.width - SCROLL_BAR)
    top_clip = SR(rect)

    def paint(top):
        py0 = S(top)
        for i, ln in enumerate(lines):
            py = py0 + S(i * lh)
            if py + S(lh) < top_clip.top or py > top_clip.bottom:
                continue
            y = py / theme.K
            if FONTS.get("f") and "{" in ln and GLYPH_RE.search(ln):
                rich_text(surf, FONTS["f"], font, ln, (rect.left, y), color)
            else:
                text(surf, font, ln, (rect.left, y), color)
    return scroll_box(surf, rect, len(lines) * lh, paint, (s, key, rect.width), slot, bg, t, lh * 0.7)


def scroll_fits(font, s, width, height, line_h=None):
    """Whether scroll_text would draw s still (it fits width x height)."""
    s = "\n".join(s) if isinstance(s, (list, tuple)) else (s or "")
    lh = line_h or (font.get_height() / theme.K + 4)
    return len(wrapped(font, s, width)) * lh <= height + 0.5


def marquee(surf, font, s, pos, color=None, maxw=None, focused=True, anchor="topleft", key=None,
            bg=None, slot=None, t=None):
    """A one-line label in maxw: unfocused (or fitting) it's text() with its "…"; focused and too
    long it slides left after MARQUEE_WAIT to show its end, waits, and comes back, its cut edges
    fading into bg (default PANEL_HI, the focused row). topleft or midleft anchors."""
    s = s or ""
    rich = bool(FONTS.get("f") and "{" in s and GLYPH_RE.search(s))
    w = rich_width(FONTS["f"], font, s) if rich else measure(font, s) if maxw else 0
    if not focused or not maxw or w <= maxw:
        return text(surf, font, s, pos, color, anchor, maxw=maxw)
    bg = theme.PANEL_HI if bg is None else bg
    lh = font.get_height() / theme.K
    top = pos[1] - lh / 2 if anchor == "midleft" else pos[1]
    box = pygame.Rect(int(pos[0]), int(top), int(maxw), int(lh + 1.999))
    over = w - maxw
    off = scroll_offset(scroll_elapsed(slot or ("h", box.left, box.top, box.width), (s, key), t),
                        over, MARQUEE_SPEED, MARQUEE_WAIT)
    prect = SR(box)
    old = _clip_to(surf, prect)
    text(surf, font, s, (pos[0] - off, pos[1]), color, anchor)
    fw = max(2, S(14))
    if off > 0.5:
        _fade(surf, bg, pygame.Rect(prect.left, prect.top, fw, prect.height), "left")
    if off < over - 0.5:
        _fade(surf, bg, pygame.Rect(prect.right - fw, prect.top, fw, prect.height), "right")
    surf.set_clip(old)
    return box
