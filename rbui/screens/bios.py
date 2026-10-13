"""BIOS files."""
import os

import rommlib
from rbui import theme
from rbui.theme import H, W
from rbui.draw import text
from rbui.screens.base import ListScreen
from rbui.screens.box_data import bios_pill, bios_rows
from rbui.modals import Confirm
from rbui.widgets.layout import Panel, ROWS_TOP, hint_actions, list_row, need_width, split


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
        row = lambda it: (it["name"], ("No games here yet · " if not it.get("here", True) else "") +  # noqa: E731
                          bios_pill(it)[2], bios_pill(it)[0])
        lst, pan = split(need_width(self.f, [row(it) for it in self.items[:200]]))
        x = lst.left + 8
        for label, col in (("%d complete" % ok, theme.GREEN), ("%d RomM can fill" % can, theme.ACCENT_HI),
                           ("%d missing everywhere" % gone, theme.AMBER)):
            r = text(c, self.f.h3, label, (x, lst.top + 6), col, maxw=max(40, lst.right - 8 - x))
            x = r.right + 24
        for i, r, on in self.row_rects(ROWS_TOP, x=lst.left, w=lst.width, gap=6):
            it = self.items[i]
            name, sub, pill = row(it)
            list_row(c, self.f, r, on, name, sub, pill, bios_pill(it)[1], slot="bios")
        it = self.items[self.sel]
        pill, col, _ = bios_pill(it)
        p = Panel(c, self.f, pan, app=self.app, key=self.sel, slot="bios-panel")
        p.foot("Files go to /userdata/bios")
        p.actions(hint_actions(self))
        p.title(it["name"], pill, col)
        p.fact("Files it needs", len(it["required"]))
        offered = {os.path.basename(m["file"]) for m, _ in it["offers"]}
        missing = [os.path.basename(m["file"]) for m in it["missing"]]
        if offered:
            p.prose(", ".join(sorted(offered)), label="RomM has", lines=3)
        gone_here = [m for m in missing if m not in offered]
        if gone_here:
            p.prose(", ".join(gone_here), label="Not in RomM either", color=theme.AMBER, lines=3)
        if it["fetched"]:
            p.fact("Fetched by RomM-o-Cera", "%d, last %s" % (len(it["fetched"]), max(
                rec.get("when", "") for rec in it["fetched"].values())[:10]))
        if not it.get("here", True):
            p.prose("No games for it on this box yet.", color=theme.FAINT, lines=2)
        if self.busy:
            p.prose("Fetching from RomM…", color=theme.ACCENT_HI, lines=1)
