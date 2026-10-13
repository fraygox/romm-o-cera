"""A game's one action (the verb button and the line saying what it does) and the soft art
backdrop behind a game's page."""
import datetime
import time

import pygame
import rommlib
from rommlib import human_size
from rbui import draw, theme
from rbui.theme import mix, SR
from rbui.draw import _fade, button_glyph, marquee, measure, progress_bar, rrect, smooth_scale, text
from rbui.widgets.tiles import cover_variant

VERB_H = 52                 # the verb button's height


def verb_button(c, f, rect, label, kind="go", detail="", glyph="A", frac=0.0):
    """The one big action, and any button bigger than a chip (a dialog's). kind: "play" (green:
    the game is here), "go" (the accent: download, pick, open), "danger" (red: delete), "quiet"
    (recessed: an undo such as Unpick, a dialog's Back), "busy" (the label over a progress bar
    at frac), "off" (nothing to do: the label faint and centred). detail sits at the right (a
    size, a folder) when it fits whole beside the label. Returns rect."""
    rect = pygame.Rect(rect)
    if kind == "off":
        rrect(c, theme.BG2, rect, 12)
        text(c, f.body, label, rect.center, theme.FAINT, "center", maxw=rect.width - 36)
        return rect
    if kind == "busy":
        rrect(c, theme.BG2, rect, 12)
        text(c, f.body, label, (rect.left + 18, rect.top + 10), theme.TEXT, maxw=rect.width - 36)
        progress_bar(c, (rect.left + 18, rect.bottom - 16, rect.width - 36, 6), frac, theme.BLUE)
        return rect
    play = kind == "play"
    fill = mix(theme.BG, theme.GREEN, 0.18) if play else theme.BG2 if kind == "quiet" else \
        theme.RED if kind == "danger" else theme.ACCENT
    rrect(c, fill, rect, 12)
    ink = theme.ink_on(fill)                  # a light theme's TEXT is dark: unreadable on its red or accent
    gx = rect.left + 18
    if glyph:
        gx += button_glyph(c, f, glyph, gx, rect.centery - 11) + 12
    lw = min(measure(f.h3, label), rect.right - 20 - gx)
    room = rect.right - 20 - (gx + lw + 16)             # the label first; the detail gets what's left
    if detail and measure(f.small, detail) <= room:      # whole or not at all
        text(c, f.small, detail, (rect.right - 20, rect.centery), ink if kind == "go" else theme.DIM,
             "midright")
    text(c, f.h3, label, (gx, rect.centery), theme.GREEN if play else theme.DIM if kind == "quiet" else ink,
         "midleft", maxw=lw + 1)
    return rect


def verb_note(c, f, rect, note, color=None, bg=None):
    """The one line under a verb button saying what pressing it does; slides when it's longer
    than the button (bg: the colour behind it, default BG)."""
    if note:
        marquee(c, f.small, note, (rect[0], rect[1] + rect[3] + 12), theme.FAINT if color is None else color,
                maxw=rect[2], bg=theme.BG if bg is None else bg, slot=("verb-note",) + tuple(rect))


def game_verb(app, rom, status=None, play="Play now", play_detail="on this box", go_detail=None):
    """verb_button's arguments for a game: Play now when it's on the box, Download (and its size,
    or go_detail) when not, its progress while it downloads, nothing to do without a system.
    status: (state, job) when the screen knows better than app.rom_status (a disc set)."""
    st, j = status or app.rom_status(rom)
    if not app.system_for(rom):
        return dict(label="No Batocera system for this platform", kind="off")
    if st == "installed":
        return dict(label=play, kind="play", detail=play_detail)
    if st == "active":
        frac = (j.done_bytes / j.total_bytes) if j.total_bytes else 0
        label = j.state
        if j.state == rommlib.Job.DOWNLOADING:
            label = "Downloading %d%%, %s/s" % (frac * 100, human_size(j.speed))
        return dict(label=label, kind="busy", frac=frac)
    return dict(label="Retry download" if st == "failed" else "Download", kind="go",
                detail=go_detail if go_detail is not None else human_size(rom.get("fs_size_bytes")))


def game_note(app, rom, status=None):
    """What the game's verb does next, in a sentence short enough for the line under the button."""
    st = (status or app.rom_status(rom))[0]
    system = app.system_for(rom)
    if not system:
        return ""
    if st == "installed":
        return "Starts it in EmulationStation."
    lay = app.layout(rom)
    if lay and lay["convert"]:
        return "Saved to roms/%s, then %s." % (system, lay["convert"])
    return "Saved to roms/%s." % system


def time_ago(ts, now=None):
    """When epoch seconds ts was, in calendar days: "today", "yesterday", "3 days ago",
    "2 weeks ago", "5 months ago", "2 years ago"."""
    if not ts:
        return ""
    now = time.time() if now is None else now
    days = (datetime.date.fromtimestamp(now) - datetime.date.fromtimestamp(ts)).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    for limit, unit, size in ((14, "day", 1), (60, "week", 7), (730, "month", 30), (None, "year", 365)):
        if limit is None or days < limit:
            n = days // size
            return "%d %s%s ago" % (n, unit, "" if n == 1 else "s")


def press_verb(app, rom, status=None):
    """What pressing a game's verb does: Play now when it's on the box (or says why it can't
    start), the download prompt when it isn't, a word while it downloads."""
    st = (status or app.rom_status(rom))[0]
    if not app.system_for(rom):
        app.toast("No Batocera system for this platform", theme.AMBER)
    elif st == "installed":
        label, why, fn = app.play_entry(rom)
        if fn:
            fn()
        else:
            app.toast(why, theme.AMBER)
    elif st == "active":
        app.toast("Already downloading", theme.AMBER)
    else:
        from rbui.modals import DownloadConfirm
        app.modal = DownloadConfirm(app, rom)


def rom_year(rom):
    """The year RomM says a game came out ("" when it doesn't)."""
    ts = (rom.get("metadatum") or {}).get("first_release_date")
    try:
        ts = int(ts)
        return time.strftime("%Y", time.gmtime(ts / (1000 if ts > 10 ** 11 else 1))) if ts else ""
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def meta_line(app, rom, platform=True, players=True):
    """A game in one grey line: platform · year · two genres · up to N players (what's known)."""
    md = rom.get("metadatum") or {}
    bits = []
    if platform:
        pl = app.platform_by_id.get(rom.get("platform_id")) or {}
        bits.append(pl.get("display_name") or pl.get("name") or rom.get("platform_display_name") or "")
    bits.append(rom_year(rom))
    bits.append(", ".join((md.get("genres") or rom.get("genres") or [])[:2]))
    n = rommlib.max_players(md.get("player_count"))
    if players and n > 1:
        bits.append("up to %d players" % n)
    return " · ".join(b for b in bits if b)


# ------------------------------------------------------------------ the backdrop

BACKDROP_SHARE = 0.25       # a backdrop is drawn only when it takes at most this much of COVER_BUDGET
VEIL = 205                  # how much of BG lies over the art (0-255)


def backdrop_fits(rect):
    """Whether a backdrop for rect (logical) is cheap enough for this box's memory budget
    (a 4K screen on a small box goes without)."""
    pr = SR(rect)
    return pr.width * pr.height * 4 <= draw.COVER_BUDGET * BACKDROP_SHARE


def backdrop(c, app, rom, rect, veil=VEIL, fade=145):
    """The cover blown up soft behind rect: scaled down to a few pixels and back up (a cheap blur,
    one surface) under a BG veil, its bottom `fade` px fading into BG. False (nothing drawn) while
    the cover loads or when backdrop_fits says no."""
    rect = pygame.Rect(rect)
    if not backdrop_fits(rect):
        return False
    pr = SR(rect)

    def make(img):
        big = smooth_scale(smooth_scale(img, (24, 14)), pr.size)
        v = pygame.Surface(pr.size, pygame.SRCALPHA)
        v.fill(tuple(theme.BG) + (veil,))
        big.blit(v, (0, 0))
        return big
    img = cover_variant(app, rom, ("backdrop", pr.size, tuple(theme.BG), veil), make)
    if img is None:
        return False
    c.blit(img, pr.topleft)
    if fade:
        fh = min(pr.height, int(fade * theme.K))
        _fade(c, theme.BG, pygame.Rect(pr.left, pr.bottom - fh, pr.width, fh), "bottom")
    return True

