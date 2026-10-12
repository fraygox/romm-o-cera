"""BIOS files."""
import os

import pygame
import rommlib
from rbui import theme
from rbui.theme import H, W
from rbui.draw import focus_box, measure, rrect, text
from rbui.screens.base import ListScreen
from rbui.screens.box_data import bios_pill, bios_rows
from rbui.modals import Confirm


class BiosScreen(ListScreen):
    """BIOS status per system: complete, missing but in RomM (A gets it,
    X gets everything RomM has), or missing everywhere — plus what RomM
    Browser has fetched before."""
    ROWS, ROW_H = 8, 62
    JUMP = "A–Z"

    def __init__(self, app):
        super().__init__(app)
        self.items = None
        self.sel = self.scroll = 0
        self.busy = False
        self.load()

    def load(self):
        app = self.app

        def loaded(rows):
            self.items = rows
            self.clamp(scroll=True)
        self.load_error = None
        app.bg(lambda: bios_rows(app), loaded, self.load_failed("items"))

    def crumbs(self):
        return self.trail("BIOS files")

    def hints(self):
        h = [("A", "Get from RomM")] if self.items and self.items[self.sel]["offers"] else []
        if self.items and any(r["offers"] for r in self.items):
            h.append(("X", "Get all from RomM"))
        return self.list_hints(*h)

    def fetch(self, rows):
        app = self.app
        if self.busy:
            return
        self.busy = True
        offers = [(r["system"], o) for r in rows for o in r["offers"]]

        def run():
            done = []
            for system in dict.fromkeys(sy for sy, _ in offers):
                done += rommlib.fetch_bios(app.api, [o for sy, o in offers if sy == system], system)
            return done

        def ok(written):
            self.busy = False
            app.toast("BIOS installed: %d file%s" % (len(written), "" if len(written) == 1 else "s"), theme.GREEN)
            self.load()

        def fail(e):
            self.busy = False
            app.toast(str(e), theme.RED)
            self.load()
        app.bg(run, ok, fail)

    def action(self, a):
        if not self.items:
            return super().action(a)
        if self.nav(a):
            return
        if a == "ok":
            r = self.items[self.sel]
            if r["offers"]:
                self.fetch([r])
            elif r["missing"]:
                self.app.toast("Not in RomM either: " + ", ".join(os.path.basename(m["file"])
                                                                    for m in r["missing"][:4]), theme.AMBER)
        elif a == "x" and any(r["offers"] for r in self.items):
            todo = [r for r in self.items if r["offers"]]
            k = sum(len(r["offers"]) for r in todo)
            self.app.modal = Confirm(self.app, "Get %d BIOS file%s from RomM?" % (k, "" if k == 1 else "s"),
                                     "For %s. Each is checked against Batocera's checksum; a wrong file "
                                     "already there is kept as .bak-romm." % ", ".join(r["name"] for r in todo[:4]) +
                                     (" and %d more" % (len(todo) - 4) if len(todo) > 4 else ""),
                                     "Get %d" % k, lambda: self.fetch(todo))
        else:
            super().action(a)

    def draw(self, c):
        if self.items is None:
            text(c, self.f.h2, "Checking BIOS files…", (W // 2, H // 2), theme.DIM, "center")
            return
        if self.draw_load_error(c):
            return
        if not self.items:
            text(c, self.f.h2, "No BIOS needed", (W // 2, 300), theme.DIM, "center")
            text(c, self.f.body, "None of the systems with games on this box needs BIOS files.",
                 (W // 2, 340), theme.FAINT, "center")
            return
        ok = sum(1 for r in self.items if not r["missing"])
        can = sum(1 for r in self.items if r["offers"])
        gone = sum(1 for r in self.items if r["missing"] and not r["offers"])
        x = 56
        for label, col in (("%d complete" % ok, theme.GREEN), ("%d RomM can fill" % can, theme.ACCENT_HI),
                           ("%d missing everywhere" % gone, theme.AMBER)):
            r = text(c, self.f.h3, label, (x, 86), col)
            x = r.right + 24
        text(c, self.f.small, "Files go to /userdata/bios", (W - 56, 90), theme.FAINT, "topright")
        for i, r, on in self.row_rects(124, gap=6):
            it = self.items[i]
            if on:
                focus_box(c, r)
            pill, col, sub = bios_pill(it)
            if not it.get("here", True):
                sub = "No games here yet · " + sub
            text(c, self.f.row, it["name"], (r.left + 22, r.top + 8), theme.TEXT if on else theme.DIM, maxw=r.width - 420)
            text(c, self.f.small, sub, (r.left + 22, r.top + 34), theme.FAINT, maxw=r.width - 60)
            pw = int(measure(self.f.small, pill)) + 20
            pr = pygame.Rect(r.right - 20 - pw, r.top + 8, pw, 24)
            rrect(c, theme.BG2, pr, 8)
            text(c, self.f.small, pill, pr.center, col, "center")
        if self.busy:
            text(c, self.f.body, "Fetching from RomM…", (W // 2, H - 90), theme.ACCENT_HI, "center")
