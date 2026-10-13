"""A game's own page: cover and backdrop, facts, summary, your RomM (status, rating, note,
RetroAchievements), screenshots, a QR to open it on your phone, and its one verb."""

import pygame
import rommlib
from rommlib import human_size
from rbui import pads, theme
from rbui.theme import W
from rbui.draw import card, marquee, measure, progress_bar, rom_title, scroll_fits, scroll_text, text
from rbui.modals import ActionMenu, ManageMenu
from rbui.screens.about import draw_qr
from rbui.screens.base import Screen
from rbui.widgets.chips import stars, status_chip
from rbui.widgets.factsheet import facts
from rbui.widgets.hero import (backdrop, game_note, game_verb, meta_line, press_verb, time_ago, verb_button, verb_note,
                               VERB_H)
from rbui.widgets.tiles import cover, placeholder, shot

X0, X1 = 48, W - 48                 # the page's edges
TOP = 92
COVER = pygame.Rect(X0, TOP, 240, 320)
TX = COVER.right + 32               # the column right of the cover
TW = X1 - TX
SHOTS_Y, SHOT_H, SHOT_GAP = 452, 176, 12
SIDE = pygame.Rect(X0 + 3 * (270 + SHOT_GAP), SHOTS_Y, X1 - (X0 + 3 * (270 + SHOT_GAP)), SHOT_H)   # verb, QR
ROW_PITCH = 28                      # a line of the Your RomM strip


def status_word(st):
    """The chip for your RomM status: Playing and the backlog first, then RomM's own word."""
    return "Playing" if st["now_playing"] else "On your backlog" if st["backlogged"] else st["label"]


def plain_note(content):
    """A note's Markdown as one line of plain text."""
    return " ".join(s for _, s in rommlib.release_note_lines(content))


class GameScreen(Screen):
    """One game, the whole screen. A: its verb (Play now / Download, as a list's A), X: its
    Actions, left/right: the previous or next game of the list it came from (source: a screen
    with sel, listed() and page_rom(i) -> rom or None while it loads), B: back. why: RomM's
    reason it was recommended."""

    def __init__(self, app, rom, why=None, source=None, then=None):
        super().__init__(app)
        self.source, self.then = source, then
        self.me = None
        app.bg(lambda: app.api.me(), lambda me: setattr(self, "me", me or {}), lambda e: setattr(self, "me", {}))
        self.show(rom, why)

    def show(self, rom, why=None):
        self.rom, self.why = rom, why
        self.qr = None
        self.box = None                 # {"copy": box_copy, "play": played_here} once asked
        self.box_for = None

    # ---- data
    def record(self):
        """The list's record merged with RomM's detail record once it's here (off the UI thread)."""
        d = self.app.detail(self.rom)
        return dict(self.rom, **d) if d else self.rom

    def detail_loaded(self):
        return bool(self.app.details.get(self.rom["id"]))

    def status(self):
        return self.app.rom_status(self.rom)

    def box_facts(self):
        """The box's copy and its plays here, asked off the UI thread whenever the game is found on
        the box (again after a download or a delete)."""
        app, rom = self.app, self.rom
        system = app.system_for(rom)
        here = self.status()[0] == "installed"
        want = (rom["id"], here)
        if want != self.box_for:
            self.box_for, self.box = want, None
            if here:
                def ask():
                    return {"copy": rommlib.box_copy(app.library, rom, system),
                            "play": rommlib.played_here(app.library, rom, system)}

                def got(res, want=want):
                    if self.box_for == want:
                        self.box = res
                app.bg(ask, got, lambda e: None)
        return self.box or {}

    def notes(self, rec):
        if not self.detail_loaded():
            return []
        uid = (self.me or {}).get("id")
        return rommlib.my_notes(rec, uid if type(uid) is int else None)

    def ra(self, rec):
        """(earned, total, hardcore) for the RetroAchievements bar, or None to hide it."""
        if not self.me:
            return None
        prog = rommlib.ra_progress(self.me, rec)
        if prog and prog["total"]:
            return prog["earned"], prog["total"], prog["earned_hardcore"]
        summ = rommlib.ra_summary(rec)
        if self.me.get("ra_username") and summ and summ[0]:
            return 0, summ[0], 0
        return None

    def shot_count(self, rec):
        """Screenshots to show (at most 3); None while the detail record that lists them loads."""
        n = len(rommlib.screenshot_paths(rec))
        if not n and self.rom["id"] not in self.app.details:
            return None
        return [i for i in range(min(3, n)) if not self.app.art_missing(("shot", self.rom["id"], i))]

    def platform_in_crumbs(self):
        plat = self.app.platform_by_id.get(self.rom.get("platform_id"))
        name = plat and (plat.get("display_name") or plat.get("name"))
        return bool(name) and name in self.trail()

    def meta_line(self, rec):
        return meta_line(self.app, rec, platform=not self.platform_in_crumbs())

    def facts(self, rec):
        app = self.app
        md = rec.get("metadatum") or {}
        lay = app.layout(self.rom)
        size = human_size(rec.get("fs_size_bytes"))
        name = rec.get("fs_name") or ""
        ext = (rec.get("fs_extension") or (name.rpartition(".")[2] if "." in name else "")).upper()
        copy = self.box_facts().get("copy") if self.status()[0] == "installed" else None
        if copy:
            romm_fmt = lay["label"] if lay else ext
            if lay and ext in ("ZIP", "7Z", "RAR"):
                romm_fmt += " in a %s" % ext.lower()
            out = [("On this box", " · ".join(b for b in (copy["format"], human_size(copy["size"]),
                                                       "roms/%s" % app.system_for(self.rom)) if b)),
                   ("In RomM", " · ".join(b for b in (romm_fmt, size) if b))]
        else:
            fmt = ext
            if lay:
                if lay["unpacked"]:
                    size += " · %s%s unpacked" % ("~" if lay["estimated"] else "", human_size(lay["unpacked"]))
                fmt = ("%d discs, " % lay["discs"] if lay["discs"] > 1 else "") + lay["label"]
                if fmt and lay["convert"]:
                    fmt += " (becomes %s)" % lay["convert"]
            out = [("Size", size), ("Format", fmt)]
        ra = rommlib.ra_summary(rec)
        return out + [
            ("Region", "/".join(rec.get("regions") or [])),
            ("Developer", ", ".join((md.get("developers") or [])[:2])),
            ("Achievements", ("%d · %d points" % ra) if ra and ra[0] else ""),
            ("Manual", "Included" if rommlib.manual_path(rec) else ""),
        ]

    # ---- controls
    def verb(self):
        return game_verb(self.app, self.rom, play_detail="roms/%s" % self.app.system_for(self.rom),
                         go_detail=None)

    def hints(self):
        v = self.verb()
        out = [] if v["kind"] in ("busy", "off") else [("A", v["label"])]
        out.append(("X", pads.ACTIONS))
        if self.source is not None:
            out.append(("LEFT RIGHT", "Next game"))
        return out + [("B", "Back")]

    def crumbs(self):
        return self.trail(rom_title(self.rom))

    def nav_key(self):
        return self.rom.get("id")

    def action(self, a):
        if a == "ok":
            self.go()
        elif a == "x":
            self.open_menu()
        elif a in ("left", "right"):
            self.step(1 if a == "right" else -1)
        else:
            super().action(a)

    def go(self):
        """The verb: Play now when it's here (the game menu's own Play now), else the download prompt."""
        press_verb(self.app, self.rom, self.status())

    def open_menu(self):
        """The game's Actions: what its menu in a list offers (delete, download again when it's
        here), More like this, and Game night from the list it came from."""
        app, rom = self.app, self.rom
        entries = [("More like this", "Games RomM says are like it", self.more_like)]
        night = getattr(self.source, "game_night", None)
        if night:
            entries.append(("Game night", "Roll a game from the list this one is in", night))
        if self.status()[0] == "installed":
            entries += [None] + [e for e in ManageMenu(app, rom, then=self.then).entries if e and e[0] != "Play now"]
        app.modal = ActionMenu(app, rom_title(rom), entries)

    def more_like(self):
        from rbui.screens.games import more_like
        more_like(self.app, self.rom)

    def step(self, d):
        """The previous or next game of the source list (one still loading: stay)."""
        src = self.source
        if src is None:
            return
        i = src.sel + d
        if not 0 <= i < len(src.listed()):
            return
        rom = src.page_rom(i)
        if rom is None:
            return
        src.sel = i
        why = getattr(src, "why", None)
        self.show(rom, why.get(rom["id"]) if isinstance(why, dict) else None)

    # ---- drawing
    def draw(self, c):
        """With screenshots: cover and text above (the Your RomM strip right under the summary,
        at the lowest level with the cover's foot), the shots and the verb/QR column below (the
        mockup). Without: the text runs down the right column, the QR goes under the cover and the
        Your RomM strip and the verb share the column's foot."""
        app, f, rom = self.app, self.f, self.rom
        rec = self.record()
        backdrop(c, app, rom, (0, 64, W, 340))
        cover(c, app, rom, COVER, fit=True)
        y = self.draw_head(c, rec)
        shots = self.shot_count(rec)
        strip_h = self.strip_height(rec)
        if shots == []:
            verb = pygame.Rect(SIDE.left, SIDE.bottom - 32 - VERB_H, SIDE.width, VERB_H)
            strip = pygame.Rect(TX, verb.top, verb.left - 16 - TX, strip_h)
            bottom = verb.top - 16
            qr = pygame.Rect(X0, SIDE.bottom - 120, 120, 120)
            cap = pygame.Rect(X0, COVER.bottom + 18, COVER.width, qr.top - 12 - COVER.bottom - 18)
        else:
            verb = pygame.Rect(SIDE.left, SIDE.top, SIDE.width, VERB_H)
            strip = pygame.Rect(TX, COVER.bottom - strip_h, TW, strip_h)
            bottom = (strip.top if strip_h else COVER.bottom) - 10
            qr = pygame.Rect(SIDE.left, SIDE.bottom - 80, 80, 80)
            cap = pygame.Rect(qr.right + 14, qr.top, SIDE.right - qr.right - 14, qr.height)
        pairs = [p for p in self.facts(rec) if p[1]]
        y = facts(c, f, pairs, (TX, y, TW, max(42, min(2 * 42, bottom - y - 40))), cols=3 if len(pairs) > 4 else 2,
                  key=rom["id"], slot="game-facts", bg=theme.BG,
                  strong=("On this box", "In RomM")).bottom + 6
        summ = rec.get("summary") or ""
        fits = True
        if summ and bottom - y >= 40:
            fits = scroll_fits(f.small, summ, TW, bottom - y, 20)
            y = scroll_text(c, f.small, summ, (TX, y, TW, bottom - y), theme.DIM, key=rom["id"], line_h=20,
                            bg=theme.BG, slot="game-summary").bottom
        if strip_h and shots != [] and fits:          # the strip follows a short summary, not a gap
            strip.top = min(strip.top, y + 14)
        if strip_h:
            self.draw_strip(c, rec, strip)
        if shots:
            self.draw_shots(c, rec, shots)
        elif shots is None:             # the record that lists them is on its way
            self.draw_shots(c, rec, [0, 1, 2], loading=True)
        app.hit(verb, lambda: self.action("ok"))
        verb_button(c, f, verb, **self.verb())
        verb_note(c, f, verb, game_note(app, rom))
        self.draw_qr(c, qr, cap)

    def draw_head(self, c, rec):
        """Title (one big line, or two smaller), the meta line, RomM's reason. Returns the y below."""
        f = self.f
        title = rom_title(rec)
        y = TOP
        if measure(f.h1, title) <= TW:
            text(c, f.h1, title, (TX, y - 4), theme.TEXT)
            y += 44
        else:
            y = scroll_text(c, f.h2, title, (TX, y, TW, 2 * 30), theme.TEXT, key=rec["id"], line_h=30,
                            bg=theme.BG, slot="game-title").bottom + 6
        meta = self.meta_line(rec)
        if meta:
            marquee(c, f.body, meta, (TX, y), theme.DIM, maxw=TW, bg=theme.BG, slot="game-meta")
            y += 28
        if self.why:
            marquee(c, f.small, self.why, (TX, y), theme.ACCENT_HI, maxw=TW, bg=theme.BG, slot="game-why")
            y += 24
        return y + 6

    def strip_rows(self, rec):
        """What the Your RomM strip holds: (status word, stars 0-5, note, played here) and the RA
        bar's (earned, total, hardcore); each None when there's nothing to say."""
        st = rommlib.user_status(rec)
        word = status_word(st) or None
        rating = round(st["rating"] / 2) if st["rating"] else None       # RomM rates out of 10
        notes = self.notes(rec)
        note = plain_note(notes[0]["content"]) if notes else None
        play = None
        p = self.box_facts().get("play") if self.status()[0] == "installed" else None
        if p and p.get("playcount"):
            n = p["playcount"]
            play = "Played here %s · %d play%s" % (time_ago(p["last_played"]), n, "" if n == 1 else "s") \
                if p.get("last_played") else "%d play%s here" % (n, "" if n == 1 else "s")
        return (word, rating, note, play), self.ra(rec)

    def strip_height(self, rec):
        first, ra = self.strip_rows(rec)
        n = any(first) + bool(ra)
        return 10 + ROW_PITCH * n if n else 0

    def draw_strip(self, c, rec, r):
        f = self.f
        (word, rating, note, play), ra = self.strip_rows(rec)
        card(c, r)
        cy = r.top + 19
        if any((word, rating, note, play)):
            x, right = r.left + 16, r.right - 16
            if play:
                right = text(c, f.small, play, (right, cy), theme.DIM, "midright", maxw=r.width // 2).left - 16
            if word:
                x = status_chip(c, f.small, word, (x, cy), anchor="midleft").right + 12
            if rating:
                x = stars(c, rating, (x, cy)) + 12
            if note and right - x > 40:
                marquee(c, f.small, "“%s”" % note, (x, cy), theme.DIM, maxw=right - x, anchor="midleft",
                        bg=theme.PANEL, slot="game-note", key=rec["id"])
            cy += ROW_PITCH
        if ra:
            earned, total, hard = ra
            progress_bar(c, (r.left + 16, cy - 3, 180, 6), earned / total, theme.GREEN)
            lab = text(c, f.small, "RetroAchievements", (r.right - 16, cy), theme.FAINT, "midright")
            s = "%d of %d achievements" % (earned, total) + (" · %d hardcore" % hard if hard else "")
            text(c, f.small, s, (r.left + 210, cy), theme.DIM, "midleft", maxw=lab.left - 16 - (r.left + 210))

    def draw_shots(self, c, rec, shots, loading=False):
        f = self.f
        text(c, f.small, "Screenshots", (X0, SHOTS_Y - 24), theme.FAINT)
        n = len(shots)
        span = SIDE.left - SHOT_GAP - X0
        w = min(int(SHOT_H * 16 / 9), (span - SHOT_GAP * (n - 1)) // n)
        for k, i in enumerate(shots):
            r = pygame.Rect(X0 + k * (w + SHOT_GAP), SHOTS_Y, w, SHOT_H)
            if loading:
                placeholder(c, f, "", r)
            else:
                shot(c, self.app, rec, i, r)

    def draw_qr(self, c, qr, cap):
        """The QR to the game's page in RomM, its caption and address in cap."""
        app, f, rom = self.app, self.f, self.rom
        url = rommlib.display_url(app.cfg.get("url") or "", mask="").rstrip("/")
        if not url:
            return
        if self.qr is None:
            self.qr = rommlib.qr_matrix("%s/rom/%d" % (url, rom["id"]))
        draw_qr(c, self.qr, qr)
        scroll_text(c, f.small, "Open it in RomM on your phone.", (cap.left, cap.top + 2, cap.width, cap.height - 24),
                    theme.DIM, line_h=20, bg=theme.BG, slot="game-qr")
        host = url.split("://")[-1]
        text(c, f.tiny, "%s/rom/%d" % (host, rom["id"]), (cap.left, cap.bottom - 2), theme.FAINT, "bottomleft",
             maxw=cap.width)
