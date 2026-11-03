"""On this box: the overview screen."""
import os
import time

import pygame
import rommlib
from rommlib import human_size
from rbui import theme
from rbui.draw import (card, circle, focus_box, hline, meter, progress_bar, rich_text, rrect, scroll_text,
                       state_stripe, text)
from rbui.theme import W
from rbui.widgets.layout import list_row
from rbui.widgets.chips import status_chip, tag
from rbui.screens.base import Screen
from rbui.screens.box_data import (RESULT_LABELS, bios_pill, bios_rows, conversion_rows, conversion_status,
                                   disk_space, installed_games, pending_roms, plural, space_bar, space_parts,
                                   storage_by_system)
from rbui.screens.compress import compress_by_system, compress_scan, CompressScreen
from rbui.screens.installed import ConversionsScreen, InstalledScreen
from rbui.screens.space import SpaceScreen
from rbui.screens.bios import BiosScreen
from rbui.screens.check import CheckScreen, check_counts, not_fully_checked
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
                InfoDialog.start_all(app)
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
            return "%s in all" % human_size(sum(g["size"] for g in self.games)), None
        if key == "space":
            total, free, keep = disk_space(app.cfg)
            if not total:
                return "Can't read the drive", theme.AMBER
            if free < keep:
                return "%s free: under the %s kept free" % (human_size(free), human_size(keep)), theme.AMBER
            return "%s for downloads, %s kept free" % (human_size(free - keep), human_size(keep)), None
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
            when = time.strftime("%Y-%m-%d", time.localtime(a.get("time") or 0))
            dmg, unread, dif = check_counts(a["findings"])
            if not a["findings"]:       # a quick check can't vouch for checksums: it says only what it saw
                k = not_fully_checked(a.get("stats"))
                if not a.get("deep"):
                    return "Nothing wrong found on %s (quick check)" % when, None
                return ("Nothing wrong found on %s · %d not fully checked" % (when, k) if k else
                        "All good on %s" % when), None
            bits = (["%d damaged" % dmg] if dmg else []) + (["%d can't be read" % unread] if unread else []) + \
                (["%d differ from RomM" % dif] if dif else [])
            return ", ".join(bits), theme.RED if dmg or unread else theme.AMBER
        if key == "compress":
            if self.compress is None:
                return "Looking…", None
            ok = [c for c in self.compress if c["enabled"]]
            if not ok:
                return "Nothing left to compress", None
            return "%s could be smaller" % human_size(sum(c["size"] for c in ok)), None
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

    # -- the short value at the right of each row: (text, colour when it needs you, or None)
    def glance(self, key):
        if key == "games":
            return ("" if self.games is None else plural(len(self.games), "game")), None
        if key == "space":
            total, free, keep = disk_space(self.app.cfg)
            return (human_size(free) + " free" if total else ""), (theme.AMBER if total and free < keep else None)
        if key == "compress":
            ok = [c for c in (self.compress or []) if c["enabled"]]
            return (plural(len(ok), "game") if ok else ""), None
        if key == "bios":
            offer = sum(len(r["offers"]) for r in (self.bios or []) if r.get("here"))
            return ("Fetch %d" % offer, theme.ACCENT_HI) if offer else ("", None)
        if key == "check":
            dmg, unread, dif = check_counts((self.audit or {}).get("findings", []))
            if dmg:
                return "%d damaged" % dmg, theme.RED
            if unread:
                return "%d can't be read" % unread, theme.RED
            if dif:
                return "%d differ" % dif, theme.AMBER
            return "", None
        if key == "conv":
            run = next((r for r in self.convs if r["kind"] == "running"), None)
            if run:
                return "%d%%" % (conversion_status(run)[1] * 100), theme.BLUE
            failed = sum(1 for r in self.convs if r["kind"] == "failed")
            return ("%d failed" % failed, theme.RED) if failed else ("", None)
        return "", None

    def summary(self):
        """The card's headline: what needs you, else what's running, else all's well."""
        need = [(k, self.glance(k)) for k, _ in self.ITEMS]
        dmg = next((g for k, g in need if k == "check" and g[1] == theme.RED), None)
        if dmg:
            n = int(dmg[0].split()[0])
            return "%s you" % plural(n, "game needs", "games need"), theme.RED
        if any(g[1] in (theme.AMBER, theme.ACCENT_HI) or (g[1] == theme.RED) for k, g in need):
            return "A few things to look at", theme.AMBER
        if any(r["kind"] == "running" for r in self.convs):
            return "Compressing in the background", theme.TEXT
        return "Everything here looks fine", theme.TEXT

    HEAD = pygame.Rect(32, 80, W - 64, 76)
    LIST = pygame.Rect(32, 168, 584, 0)
    ROW_H = 70

    def draw(self, c):
        if time.time() - self.conv_at > 1.0:
            self.convs, self.conv_at = conversion_rows(), time.time()
        hr = self.HEAD
        card(c, hr)
        line, col = self.summary()
        total, free, keep = disk_space(self.app.cfg)
        run = sum(1 for r in self.convs if r["kind"] == "running")
        meta = ("%s free of %s" % (human_size(free), human_size(total)) if total else "") + \
            (" · %d converting" % run if run else "")
        mr = text(c, self.f.small, meta, (hr.right - 20, hr.top + 20), theme.DIM, "topright", maxw=hr.width // 2)
        text(c, self.f.h2, line, (hr.left + 20, hr.top + 12), col, maxw=mr.left - 40 - hr.left)
        if total and self.storage is not None:
            space_bar(c, self.f, (hr.left + 20, hr.bottom - 22, hr.width - 40, 8),
                      space_parts(self.storage, total, free), total, free, keep, legend=False)
        for i, (key, label) in enumerate(self.ITEMS):
            r = pygame.Rect(self.LIST.left, self.LIST.top + i * self.ROW_H, self.LIST.width, self.ROW_H - 6)
            self.app.hit(r, lambda i=i: self.tap_now(i))
            on = i == self.sel
            sub, scol = self.verdict(key)
            val, vcol = self.glance(key)
            stripe = scol or vcol
            list_row(c, self.f, r, on, label, sub, val, vcol or theme.DIM, sub_color=scol, slot="box-row",
                     box=theme.PANEL, chevron=True)
            if val and vcol:                          # what needs you: the value as a chip, over the plain one
                status_chip(c, self.f.small, val, (r.right - 38, r.centery), vcol, "midright", h=26)
            state_stripe(c, r, stripe or theme.LINE)
        p = pygame.Rect(640, self.LIST.top, W - 32 - 640, self.ROW_H * len(self.ITEMS) - 6)
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
        self.head(c, p, "Games from RomM", "biggest first")
        if self.games is None:
            return self.waiting(c, p, "Measuring…")
        if not self.games:
            self.prose(c, p, "Nothing downloaded with RomM-o-Cera yet. Games copied over "
                                        "another way are in Space, under Free up space.", p.top + 72)
            return
        yy = p.top + 72
        for g in self.games[:7]:
            text(c, self.f.row, g["name"], (x, yy), theme.DIM, maxw=p.width - 240)
            tag(c, self.f.tiny, self.app.system_name(g["system"]) if g["system"] else "?", (p.right - 128, yy + 3), "topright", color=theme.DIM)
            text(c, self.f.small, human_size(g["size"]), (p.right - 28, yy + 3), theme.DIM, "topright")
            yy += 44
        if len(self.games) > 7:
            text(c, self.f.small, "and %d more" % (len(self.games) - 7), (x, yy), theme.FAINT)
        self.foot(c, p, "Delete any of them; saves and states are kept.")

    def panel_space(self, c, p):
        x, w = p.left + 28, p.width - 56
        total, free, keep = disk_space(self.app.cfg)
        if not total:
            return self.head(c, p, "Space", "can't read the drive")
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
                if yy + 30 > p.bottom - (84 if self.leftovers else 20):   # stops above the foot's rule
                    break
                text(c, self.f.body, self.app.system_name(r["system"]), (x, yy), theme.DIM, maxw=170)
                meter(c, (x + 180, yy + 8, w - 290, 8), [(r[k] / big, col) for k, col, _ in theme.SPACE_PARTS],
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
        self.head(c, p, "BIOS files", "systems with games here" if self.app.platforms else "RomM offline")
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
            self.head(c, p, "Check games", "not checked yet")
            yy = p.top + 72
        else:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(a.get("time", 0)))
            self.head(c, p, "Check games", "%s, %s" % ("full check" if a.get("deep") else "quick check", when))
            yy = p.top + 66
            for f in a["findings"][:3]:
                col = theme.RED if f["severity"] == "damaged" else theme.AMBER
                r = pygame.Rect(x, yy, p.width - 56, 70)
                rrect(c, theme.BG2, r, 10)
                state_stripe(c, r, col)
                name = (f.get("rom") or {}).get("name") or os.path.basename(f["path"])
                text(c, self.f.row, name, (r.left + 20, r.top + 10), theme.TEXT, maxw=r.width - 120)
                text(c, self.f.small, f["problem"], (r.left + 20, r.top + 40), col, maxw=r.width - 40)
                text(c, self.f.tiny, self.app.system_name(f["system"]), (r.right - 16, r.top + 14), theme.DIM, "topright")
                yy += 82
            st = a.get("stats") or {}
            more = len(a["findings"]) - 3
            line = ("and %d more. " % more if more > 0 else "") + \
                "%d checked, %d matched to RomM." % (st.get("checked", 0), st.get("matched", 0))
            text(c, self.f.body, line, (x, yy + 4), theme.GREEN if not a["findings"] else theme.DIM)
            yy += 40
        if not a:
            self.prose(c, p, "A quick check looks for missing tracks, damaged zips and cut-short discs, and "
                             "compares sizes with RomM. It takes a few minutes.", yy)
        self.foot(c, p, "Damaged games can be downloaded again; saves follow.")

    def panel_compress(self, c, p):
        x = p.left + 28
        self.head(c, p, "Compress", "not compressed yet, by system")
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
            text(c, self.f.row, self.app.system_name(r["system"]), (x, yy), col, maxw=170)
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
            self.head(c, p, "Conversions", "nothing to convert")
            self.prose(c, p, "Disc games are compressed in the background after they "
                                        "download, while nothing is playing.", p.top + 72)
            return
        yy = p.top + 66
        if run:
            self.head(c, p, "Conversions", "runs while nothing is playing")
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
                    s = RESULT_LABELS.get(r["kind"], r["kind"] or "")
                    col = {"ok": theme.GREEN, "failed": theme.RED}.get(r["kind"], theme.AMBER)
                text(c, self.f.small, s, (p.right - 28, yy + 2), col, "topright", maxw=220)
                yy += 32
            yy += 18
        self.foot(c, p, "Activity shows them too, beside the downloads.")

    def panel_info(self, c, p):
        x = p.left + 28
        self.head(c, p, "Fill in game info", "systems RomM can fill in")
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
