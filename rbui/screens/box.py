"""On this box: the overview screen."""
import os
import time

import pygame
import rommlib
from rommlib import human_size
from rbui import theme
from rbui.draw import (circle, focus_box, hline, meter, progress_bar, rich_text, rrect, scroll_text,
                       state_stripe, text)
from rbui.widgets.chips import status_chip, tag
from rbui.screens.base import Screen
from rbui.screens.box_data import (bios_pill, bios_rows, conversion_rows, conversion_status, disk_space,
                                   installed_games, pending_roms, plural, space_bar, space_parts, storage_by_system)
from rbui.screens.compress import compress_by_system, compress_scan, CompressScreen
from rbui.screens.installed import ConversionsScreen, InstalledScreen
from rbui.screens.space import SpaceScreen
from rbui.screens.bios import BiosScreen
from rbui.screens.check import CheckScreen
from rbui.modals import InfoDialog


class OnThisBoxScreen(Screen):
    """What's on the box, one section each: games from RomM, space, BIOS,
    checks, conversions, game info. Each line under a section is its live
    state, coloured only when something wants attention; the panel on the
    right previews the selected section, A opens it."""
    ITEMS = [("games", "Games from RomM"), ("space", "Space"), ("compress", "Compress games already here"),
             ("bios", "BIOS files"), ("check", "Check games"), ("conv", "Conversions"),
             ("info", "Fill in game info")]
    TILE_H, STEP = 70, 80

    def __init__(self, app):
        super().__init__(app)
        self.sel = 0
        self.games = self.storage = self.leftovers = self.bios = self.counts = None
        self.audit, self.convs, self.conv_at = None, [], 0
        self.compress = None

    def enter(self):
        app = self.app
        app.bg(compress_scan, lambda v: setattr(self, "compress", v), lambda e: setattr(self, "compress", []))
        app.bg(lambda: installed_games(app.library), lambda v: setattr(self, "games", v),
               lambda e: setattr(self, "games", []))
        app.bg(lambda: storage_by_system(app.library), lambda v: setattr(self, "storage", v),
               lambda e: setattr(self, "storage", []))
        app.bg(lambda: rommlib.find_leftovers(pending_roms(app)), lambda v: setattr(self, "leftovers", v),
               lambda e: setattr(self, "leftovers", []))
        app.bg(lambda: bios_rows(app), lambda v: setattr(self, "bios", v), lambda e: setattr(self, "bios", []))
        app.bg(rommlib.es_counts, lambda v: setattr(self, "counts", v), lambda e: setattr(self, "counts", {}))
        self.audit = rommlib.load_audit()
        self.convs, self.conv_at = conversion_rows(), time.time()

    def crumbs(self):
        return ["On this box"]

    def hints(self):
        return [("A", "Open"), ("B", "Back")]

    def action(self, a):
        app = self.app
        if a == "up":
            self.sel = (self.sel - 1) % len(self.ITEMS)
        elif a == "down":
            self.sel = (self.sel + 1) % len(self.ITEMS)
        elif a == "ok":
            key = self.ITEMS[self.sel][0]
            if key == "games":
                app.push(InstalledScreen(app))
            elif key == "space":
                app.push(SpaceScreen(app))
            elif key == "compress":
                app.push(CompressScreen(app))
            elif key == "bios":
                app.push(BiosScreen(app))
            elif key == "check":
                app.push(CheckScreen(app))
            elif key == "conv":
                app.push(ConversionsScreen(app))
            elif key == "info":
                if not app.platforms:
                    app.load_platforms()
                    app.toast(app.error or "Still loading platforms…", theme.AMBER)
                else:
                    InfoDialog.start(app, InfoDialog.all_systems(app))
        else:
            super().action(a)

    # -- the line under each section: (text, colour or None)
    def verdict(self, key):
        app = self.app
        if key == "games":
            if self.games is None:
                return "Measuring…", None
            if not self.games:
                return "None yet", None
            return "%s, %s" % (plural(len(self.games), "game"), human_size(sum(g["size"] for g in self.games))), None
        if key == "space":
            total, free, keep = disk_space(app.cfg)
            if not total:
                return "Can't read the drive", theme.AMBER
            if free < keep:
                return "%s free: under the %s kept free" % (human_size(free), human_size(keep)), theme.AMBER
            return "%s free, %s for downloads" % (human_size(free), human_size(free - keep)), None
        if key == "bios":
            if self.bios is None:
                return "Checking…", None
            here = [r for r in self.bios if r.get("here")]
            if not here:
                return "No BIOS needed", None
            offer = [r for r in here if r["offers"]]
            gone = [r for r in here if r["missing"] and not r["offers"]]
            if offer:
                if len(offer) == 1:
                    n = len(offer[0]["offers"])
                    return "%s is missing %s RomM has" % (offer[0]["name"], plural(n, "file")), theme.ACCENT_HI
                return "%d systems are missing files RomM has" % len(offer), theme.ACCENT_HI
            if gone:
                return "%s missing files, not in RomM" % plural(len(gone), "system"), theme.AMBER
            return "All %s complete" % plural(len(here), "system"), None
        if key == "check":
            a = self.audit
            if not a:
                return "Not checked yet", None
            when = time.strftime("%Y-%m-%d", time.localtime(a.get("time", 0)))
            dmg = sum(1 for f in a["findings"] if f["severity"] == "damaged")
            dif = len(a["findings"]) - dmg
            if not a["findings"]:
                return "All good on %s" % when, None
            bits = (["%d damaged" % dmg] if dmg else []) + (["%d differ from RomM" % dif] if dif else [])
            return "%s, checked %s" % (", ".join(bits), when), theme.RED if dmg else theme.AMBER
        if key == "compress":
            if self.compress is None:
                return "Looking…", None
            ok = [c for c in self.compress if c["enabled"]]
            if not ok:
                return "Nothing left to compress", None
            return "%s could be smaller (%s)" % (plural(len(ok), "game"), human_size(sum(c["size"] for c in ok))), None
        if key == "conv":
            run = next((r for r in self.convs if r["kind"] == "running"), None)
            nq = sum(1 for r in self.convs if r["kind"] == "queued")
            if run:
                return "Converting" + (", %d waiting" % nq if nq else ""), theme.BLUE
            if nq:
                return "%d waiting for a quiet moment" % nq, None
            failed = [r for r in self.convs if r["kind"] == "failed"]
            if failed:
                return "%s failed: retry from here" % plural(len(failed), "conversion"), theme.RED
            return "Nothing waiting", None
        return "Art, videos, descriptions from RomM", None

    def draw(self, c):
        if time.time() - self.conv_at > 1.0:
            self.convs, self.conv_at = conversion_rows(), time.time()
        y = 84
        for i, (key, label) in enumerate(self.ITEMS):
            r = pygame.Rect(48, y, 520, self.TILE_H)
            self.app.hit(r, lambda i=i: self.tap_now(i))
            on = i == self.sel
            if on:
                focus_box(c, r, 14)
            else:
                rrect(c, theme.PANEL, r, 14)
            text(c, self.f.h2, label, (r.left + 28, r.top + 9), theme.TEXT if on else theme.DIM)
            line, col = self.verdict(key)
            if col:
                circle(c, col, (r.left + 33, r.top + 50), 5)
            text(c, self.f.small, line, (r.left + (44 if col else 28), r.top + 40),
                 col or (theme.DIM if on else theme.FAINT), maxw=r.width - 60)
            y += self.STEP
        p = pygame.Rect(616, 84, 616, self.STEP * (len(self.ITEMS) - 1) + self.TILE_H)
        rrect(c, theme.PANEL, p, 16)
        getattr(self, "panel_" + self.ITEMS[self.sel][0])(c, p)

    def foot(self, c, p, s, col=None):
        col = theme.DIM if col is None else col
        hline(c, theme.LINE, (p.left + 28, p.bottom - 74), (p.right - 28, p.bottom - 74))
        rich_text(c, self.f, self.f.body, s, (p.left + 28, p.bottom - 38), col, "midleft", maxw=p.width - 56)

    def head(self, c, p, title, right=""):
        text(c, self.f.h2, title, (p.left + 28, p.top + 22), theme.TEXT, maxw=p.width - 260)
        if right:
            text(c, self.f.small, right, (p.right - 28, p.top + 28), theme.FAINT, "topright", maxw=280)

    def waiting(self, c, p, s):
        self.prose(c, p, s, p.top + 72)

    def prose(self, c, p, s, top):
        """A panel's text, from top down to its foot; a long one scrolls."""
        if p.bottom - 84 - top >= 26:
            scroll_text(c, self.f.body, s, (p.left + 28, top, p.width - 56, p.bottom - 84 - top), theme.DIM,
                        key=self.sel, line_h=26, slot="box-prose")

    def panel_games(self, c, p):
        x = p.left + 28
        self.head(c, p, "Biggest first", self.verdict("games")[0] if self.games else "")
        if self.games is None:
            return self.waiting(c, p, "Measuring…")
        if not self.games:
            self.prose(c, p, "Nothing downloaded with RomM-o-Cera yet. Games copied over "
                                        "another way are in Space, under Free up space.", p.top + 72)
            return
        yy = p.top + 72
        for g in self.games[:7]:
            text(c, self.f.row, g["name"], (x, yy), theme.DIM, maxw=p.width - 240)
            tag(c, self.f.tiny, g["system"] or "?", (p.right - 128, yy + 3), "topright", color=theme.DIM)
            text(c, self.f.small, human_size(g["size"]), (p.right - 28, yy + 3), theme.DIM, "topright")
            yy += 44
        if len(self.games) > 7:
            text(c, self.f.small, "and %d more" % (len(self.games) - 7), (x, yy), theme.FAINT)
        self.foot(c, p, "Delete any of them; saves and states are kept.")

    def panel_space(self, c, p):
        x, w = p.left + 28, p.width - 56
        total, free, keep = disk_space(self.app.cfg)
        if not total:
            return self.head(c, p, "Can't read the drive")
        text(c, self.f.h1, human_size(free) + " free", (x, p.top + 20), theme.AMBER if free < keep else theme.TEXT)
        text(c, self.f.small, "of " + human_size(total), (p.right - 28, p.top + 36), theme.FAINT, "topright")
        if self.storage is None:
            return self.waiting(c, p, "Measuring every system…")
        yy = space_bar(c, self.f, (x, p.top + 82, w, 26), space_parts(self.storage, total, free),
                       total, free, keep) + 16
        if self.storage:
            text(c, self.f.h3, "Biggest systems", (x, yy), theme.TEXT)
            yy += 34
            big = self.storage[0]["total"] or 1
            for r in self.storage[:3]:
                text(c, self.f.body, r["system"], (x, yy), theme.DIM, maxw=110)
                meter(c, (x + 120, yy + 8, w - 230, 8), [(r[k] / big, col) for k, col, _ in theme.SPACE_PARTS],
                      track=theme.PANEL, least=4)
                text(c, self.f.small, human_size(r["total"]), (p.right - 28, yy + 1), theme.DIM, "topright")
                yy += 30
        if self.leftovers:
            n = sum(i["size"] for i in self.leftovers)
            self.foot(c, p, "%s of leftovers from interrupted downloads" % human_size(n), theme.AMBER)
        elif self.leftovers is not None:
            self.foot(c, p, "No leftovers to clean up.")

    def panel_bios(self, c, p):
        x = p.left + 28
        self.head(c, p, "Systems with games here", "" if self.app.platforms else "RomM offline")
        if self.bios is None:
            return self.waiting(c, p, "Checking BIOS files…")
        here = [r for r in self.bios if r.get("here")]
        if not here:
            return self.waiting(c, p, "None of the systems with games on this box needs BIOS files.")
        yy = p.top + 70
        for it in here[:6]:
            pill, col, sub = bios_pill(it)
            pr = status_chip(c, self.f.small, pill, (p.right - 28, yy + 6), col, "topright", h=30)
            text(c, self.f.row, it["name"], (x, yy), theme.DIM, maxw=pr.left - 34 - x)
            text(c, self.f.small, sub, (x, yy + 26), theme.FAINT, maxw=pr.left - 34 - x)
            yy += 64
        others = len(self.bios) - len(here)
        more = (["%s more" % plural(len(here) - 6, "system")] if len(here) > 6 else []) + \
            (["%d without games here" % others] if others else [])
        if more:
            text(c, self.f.small, "Also: " + ", ".join(more), (x, yy), theme.FAINT)
        if any(r["offers"] for r in here):
            self.foot(c, p, "Files RomM has: get one, or all at once.")
        else:
            self.foot(c, p, "Files go to /userdata/bios")

    def panel_check(self, c, p):
        x, a = p.left + 28, self.audit
        if not a:
            self.head(c, p, "Not checked yet")
            yy = p.top + 72
        else:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(a.get("time", 0)))
            self.head(c, p, "Last check", "%s, %s" % ("Full check" if a.get("deep") else "Quick check", when))
            yy = p.top + 66
            for f in a["findings"][:3]:
                col = theme.RED if f["severity"] == "damaged" else theme.AMBER
                r = pygame.Rect(x, yy, p.width - 56, 70)
                rrect(c, theme.BG2, r, 10)
                state_stripe(c, r, col)
                name = (f.get("rom") or {}).get("name") or os.path.basename(f["path"])
                text(c, self.f.row, name, (r.left + 20, r.top + 10), theme.TEXT, maxw=r.width - 120)
                text(c, self.f.small, f["problem"], (r.left + 20, r.top + 40), col, maxw=r.width - 40)
                text(c, self.f.tiny, f["system"], (r.right - 16, r.top + 14), theme.DIM, "topright")
                yy += 82
            st = a.get("stats") or {}
            more = len(a["findings"]) - 3
            line = ("and %d more. " % more if more > 0 else "") + \
                "%d checked, %d matched to RomM." % (st.get("checked", 0), st.get("matched", 0))
            text(c, self.f.body, line, (x, yy + 4), theme.GREEN if not a["findings"] else theme.DIM)
            yy += 50
        text(c, self.f.h3, "Quick check", (x, yy), theme.TEXT)
        text(c, self.f.small, "Missing tracks, damaged zips, cut-short discs, sizes against RomM.", (x, yy + 26),
             theme.FAINT, maxw=p.width - 56)
        text(c, self.f.h3, "Full check", (x, yy + 62), theme.TEXT)
        text(c, self.f.small, "Also checksums every file and verifies CHDs. Hours for a big library.",
             (x, yy + 88), theme.FAINT, maxw=p.width - 56)
        self.foot(c, p, "Damaged games can be downloaded again; saves follow.")

    def panel_compress(self, c, p):
        x = p.left + 28
        self.head(c, p, "Not compressed yet", "by system")
        if self.compress is None:
            return self.waiting(c, p, "Looking through every system…")
        rows = compress_by_system(self.compress)
        if not rows:
            self.prose(c, p, "Every disc game here that a converter supports is "
                                        "compressed already (or queued).", p.top + 72)
            return
        yy = p.top + 70
        for r in rows[:7]:
            col = theme.DIM if r["enabled"] else theme.FAINT
            text(c, self.f.row, r["system"], (x, yy), col, maxw=170)
            text(c, self.f.small, "%s, %s" % (plural(r["n"], "game"), human_size(r["size"])), (x + 180, yy + 3),
                 theme.FAINT, maxw=220)
            text(c, self.f.small, ("to " + r["label"]) if r["enabled"] else "needs a setting",
                 (p.right - 28, yy + 3), theme.ACCENT_HI if r["enabled"] else theme.FAINT, "topright")
            yy += 40
        if len(rows) > 7:
            text(c, self.f.small, "and %s more" % plural(len(rows) - 7, "system"), (x, yy), theme.FAINT)
        self.foot(c, p, "One game, or a whole system (that can take hours).")

    def panel_conv(self, c, p):
        x = p.left + 28
        run = next((r for r in self.convs if r["kind"] == "running"), None)
        queued = [r for r in self.convs if r["kind"] == "queued"]
        done = [r for r in self.convs if r["kind"] not in ("running", "queued")]
        mode = lambda r: ConversionsScreen.MODES.get((r.get("task") or {}).get("mode") or r.get("mode"), "")  # noqa: E731
        if not self.convs:
            self.head(c, p, "Nothing to convert")
            self.prose(c, p, "Disc games are compressed in the background after they "
                                        "download, while nothing is playing.", p.top + 72)
            return
        yy = p.top + 66
        if run:
            self.head(c, p, "Converting now", "runs while nothing is playing")
            r = pygame.Rect(x, yy, p.width - 56, 92)
            rrect(c, theme.BG2, r, 12)
            st, frac = conversion_status(run)
            text(c, self.f.row, run["title"], (r.left + 20, r.top + 12), theme.TEXT, maxw=r.width - 40)
            text(c, self.f.small, "%s · %s" % (mode(run), st), (r.left + 20, r.top + 40), theme.BLUE, maxw=r.width - 40)
            progress_bar(c, (r.left + 20, r.bottom - 22, r.width - 40, 8), frac, theme.BLUE)
            yy = r.bottom + 26
        else:
            self.head(c, p, "Conversions", "runs while nothing is playing")
        for title, rows in (("Waiting", queued[:3]), ("Recent", done[:3])):
            if not rows:
                continue
            text(c, self.f.h3, title, (x, yy), theme.TEXT)
            yy += 32
            for r in rows:
                text(c, self.f.body, r["title"], (x, yy), theme.DIM, maxw=p.width - 300)
                if r["kind"] == "queued" and r.get("tool"):
                    s, col = "Waiting for %s" % r["tool"], theme.AMBER
                elif r["kind"] == "queued":
                    s, col = "%s · %s" % (mode(r), human_size(r["size"])), theme.FAINT
                else:
                    s = {"ok": "Done", "failed": "Failed", "skipped": "Skipped", "waiting": "Waiting"}.get(
                        r["kind"], r["kind"] or "")
                    col = {"ok": theme.GREEN, "failed": theme.RED}.get(r["kind"], theme.AMBER)
                text(c, self.f.small, s, (p.right - 28, yy + 2), col, "topright", maxw=220)
                yy += 32
            yy += 18
        self.foot(c, p, "Also in Activity: {L2 R2}")

    def panel_info(self, c, p):
        x = p.left + 28
        self.head(c, p, "Systems RomM can fill in")
        yy = p.top + 70
        counts = self.counts or {}
        romm = {pl["_system"] for pl in self.app.platforms}
        systems = sorted(((k, v) for k, v in counts.items() if not romm or k in romm), key=lambda kv: -kv[1])
        for sysn, n in systems[:6]:
            text(c, self.f.row, sysn, (x, yy), theme.DIM)
            text(c, self.f.small, plural(n, "game"), (p.right - 28, yy + 3), theme.DIM, "topright")
            yy += 38
        if len(systems) > 6:
            text(c, self.f.small, "and %s more" % plural(len(systems) - 6, "system"), (x, yy), theme.FAINT)
            yy += 26
        self.prose(c, p, "Descriptions, screenshots, box art, logos, videos and manuals from RomM, matched by "
                   "file name. Only what's empty is filled: your own scrapes and edits are never replaced.", yy + 18)
        self.foot(c, p, "To do one system: its game list, then {X}")
