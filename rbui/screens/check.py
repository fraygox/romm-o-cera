"""Check games: the quick and full check of what's on the box."""
import os
import threading
import time

import pygame
import rommlib
from rbui import pads, theme
from rbui.theme import W
from rbui.draw import circle, focus_box, hline, marquee, measure, rrect, scroll_text, text
from rbui.screens.base import ListScreen, Screen
from rbui.screens.box_data import plural
from rbui.screens.unseen import UnseenScreen
from rbui.modals import Confirm


class AuditScreen(ListScreen):
    """Check what's on the box against RomM; damaged games can be downloaded
    again. Red: damaged or incomplete. Amber: not RomM's copy (another dump,
    a trimmed image…), which may well play. Picked findings (amber ones
    too) download again together from the X Actions menu."""
    LOAD_ERROR_HEAD = "The check didn't finish"
    ROWS, ROW_H = 8, 62
    PICKS, PICK_WHAT, JUMP = True, "findings", "System"

    def __init__(self, app, deep=False):
        super().__init__(app)
        self.deep = deep
        self.cancel = threading.Event()
        self.where = ""
        self.findings = None
        self.stats = None
        self.sel = self.scroll = 0
        self.queued = set()
        self.t0 = time.time()
        by_sys = {}
        for p in app.platforms:
            by_sys.setdefault(p["_system"], []).append(p["id"])

        def progress(system, name):
            self.where = "roms/%s/%s" % (system, name)

        def run():
            return rommlib.audit(app.api, by_sys, deep=deep, progress=progress, cancel=self.cancel)

        def done(res):
            f, self.stats = res
            self.findings = self.order(f)
            if not self.cancel.is_set() and (self.stats or {}).get("checked"):   # nothing checked: keep the last
                try:
                    rommlib.save_audit(self.findings, self.stats, deep)
                except OSError as e:
                    rommlib.log("audit save: %s" % e)
        app.bg(run, done, self.load_failed("findings"))

    base = 0            # sel of the first finding (CheckScreen puts three tiles above them)

    def listed(self):
        return self.findings or []

    @staticmethod
    def order(findings):
        rank = {"damaged": 0, "differs": 1}
        return sorted(findings, key=lambda x: (rank.get(x["severity"], 2), x["system"], x["path"]))

    def crumbs(self):
        return self.trail("Full check" if self.deep else "Quick check")

    def hints(self):
        if self.findings is None:
            return [("B", "Stop")]
        h = [("A", "Download again"), ("X", pads.ACTIONS)] if self.findings else []
        if self.redownloadable():
            h.append(("Y", "Download all damaged again"))
        return self.list_hints(*h)

    def redownloadable(self):
        return [f for f in (self.findings or []) if f["severity"] == "damaged" and f.get("rom")
                and f["path"] not in self.queued]

    def jump_label(self, f):
        return f["system"]

    def pickable(self, f):
        return bool(f.get("rom")) and f["path"] not in self.queued

    def unpickable_why(self, f):
        return "Already queued" if f["path"] in self.queued else \
            "Not in RomM under this name — can't download it again"

    def list_actions(self):
        n = len({f["rom"]["id"] for f in self.redownloadable()})
        return [("Download all damaged again", "%s; amber ones are left alone" % plural(n, "game"),
                 self.redownload_all)] if n else []

    def pick_entries(self, picked):
        todo = [f for f in picked if self.pickable(f)]
        dmg = sum(1 for f in todo if f["severity"] == "damaged")
        sub = ", ".join(x for x in ("%d damaged" % dmg if dmg else "",
                                    "%d differ from RomM" % (len(todo) - dmg) if len(todo) > dmg else "") if x)
        return [("Download %d again" % len(todo), sub, (lambda: self.redownload_many(
            todo, "Download %s again?" % plural(len({f["rom"]["id"] for f in todo}), "picked game"),
            "RomM's copies replace the ones on the box (saves are kept), the ones that only differ "
            "from RomM too.")) if todo else None)]

    @staticmethod
    def renamed(f):
        """RomM's copy has another file name than the broken one on the box."""
        new = (f["rom"].get("fs_name_no_ext") or os.path.splitext(f["rom"].get("fs_name") or "")[0]).lower()
        return bool(f.get("via")) or (new and new != os.path.splitext(os.path.basename(f["path"]))[0].lower())

    def redownload(self, f):
        app = self.app
        app.downloader.enqueue(dict(f["rom"]), f["system"], replace=[f["path"]] if self.renamed(f) else None)
        self.queued.add(f["path"])

    def action(self, a):
        app = self.app
        if self.findings is None:
            if a in ("back", "quit"):
                self.cancel.set()
                app.toast("Check stopped")
                super().action("back")
            return
        n = len(self.findings)
        if self.nav(a) or self.pick_action(a):
            return
        if a == "ok" and n:
            self.open_finding(self.findings[self.sel])
        elif a == "y" and self.redownloadable():
            self.redownload_all()
        else:
            if a == "back":
                self.cancel.set()
            super().action(a)

    def open_finding(self, f):
        app = self.app
        if not f.get("rom"):
            app.toast("Not in RomM under this name — can't download it again", theme.AMBER)
        elif f["path"] in self.queued:
            app.toast("Already queued")
        else:
            name = f["rom"].get("name") or os.path.basename(f["path"])
            how = ("RomM's copy (%s) is downloaded; then saves move to its name and the broken file is "
                   "deleted." % (f["rom"].get("fs_name") or name)) if self.renamed(f) else \
                "RomM's copy replaces the one on the box (saves are kept)."
            app.modal = Confirm(app, "Download %s again?" % name, "%s. %s" % (f["problem"].split(" · ")[0], how),
                                "Download again",
                                lambda: (self.redownload(f), app.toast("Queued: %s" % name, theme.GREEN)))

    def redownload_all(self):
        todo = self.redownloadable()
        n = len({f["rom"]["id"] for f in todo})
        self.redownload_many(todo, "Download %d damaged games again?" % n,
                             "RomM's copies replace the damaged ones. Games that only differ from RomM "
                             "(amber) are left alone.")

    def redownload_many(self, todo, title, body):
        """Each game once (two findings can be one game), after a confirm."""
        app = self.app
        uniq = {}
        for f in todo:
            uniq.setdefault(f["rom"]["id"], f)

        def go():
            for f in uniq.values():
                self.redownload(f)
            self.picks.clear()
            self.picking = False
            app.toast("Queued %d games" % len(uniq), theme.GREEN)
        app.modal = Confirm(app, title, body, "Download %d" % len(uniq), go)

    def draw(self, c):
        if self.findings is None:
            secs = int(time.time() - self.t0)
            text(c, self.f.h2, "Checking games…", (W // 2, 280), theme.TEXT, "center")
            text(c, self.f.body, self.where or "Asking RomM what it has…", (W // 2, 326), theme.DIM, "center",
                 maxw=W - 160)
            text(c, self.f.small, "%d:%02d elapsed%s" % (secs // 60, secs % 60,
                 " · full check: checksums every file" if self.deep else ""), (W // 2, 362), theme.FAINT, "center")
            return
        if self.draw_load_error(c):
            return
        st = self.stats or {}
        dmg = sum(1 for f in self.findings if f["severity"] == "damaged")
        dif = len(self.findings) - dmg
        head = "%d checked, %d matched to RomM. " % (st.get("checked", 0), st.get("matched", 0))
        if st.get("unverified"):
            tools = st.get("unverified_tools") or ["chdman"]        # a check saved by v0.9.0: CHDs only
            head += "%s not verified (missing %s). " % (plural(st["unverified"], "game"), ", ".join(tools))
        if not self.findings:
            text(c, self.f.h3, head + "Everything looks right.", (56, 86), theme.GREEN)
            text(c, self.f.body, "Games not in RomM under the same file name aren't checked (%d)."
                 % st.get("unmatched", 0), (56, 124), theme.DIM)
            return
        text(c, self.f.h3, head, (56, 86), theme.TEXT)
        x = 56 + int(measure(self.f.h3, head)) + 6
        if dmg:
            r = text(c, self.f.h3, "%d damaged" % dmg, (x, 86), theme.RED)
            x = r.right + 16
        if dif:
            text(c, self.f.h3, "%d differ from RomM" % dif, (x, 86), theme.AMBER)
        self.draw_rows(c, 124, self.ROWS)

    def draw_rows(self, c, y, rows):
        for i, r, on in self.row_rects(y, gap=6, rows=rows):
            f = self.findings[i]
            if on:
                focus_box(c, r)
            col = theme.RED if f["severity"] == "damaged" else theme.AMBER
            if not self.draw_pick(c, f, (r.left + 18, r.top + 20)):
                circle(c, col, (r.left + 18, r.top + 20), 5)
            name = os.path.relpath(f["path"], os.path.join(rommlib.ROMS_ROOT, f["system"]))
            marquee(c, self.f.row, name, (r.left + 34, r.top + 8), theme.TEXT if on else theme.DIM,
                    maxw=r.width - 230, focused=on, slot="check-name")
            marquee(c, self.f.small, f["problem"], (r.left + 34, r.top + 34), col, maxw=r.width - 230, focused=on,
                    slot="check-problem")
            tag = "queued" if f["path"] in self.queued else f["system"]
            tw = int(measure(self.f.tiny, tag)) + 14
            tr = pygame.Rect(r.right - 20 - tw, r.top + 10, tw, 20)
            rrect(c, theme.BG2, tr, 6)
            text(c, self.f.tiny, tag, tr.center, theme.GREEN if f["path"] in self.queued else theme.FAINT, "center")


class CheckScreen(AuditScreen):
    """Check games, from On this box: start a quick or full check, look for
    games ES can't see, and the last finished check's findings (files changed
    since drop out), each one downloadable again from here."""
    ROWS = 7
    TILES = (("Quick check", "Missing tracks, bad zips, short discs"),
             ("Full check", "Also checksums every file (slow)"),
             ("Games ES can't see", "Archives and games in the wrong folder"))
    base = 3

    def __init__(self, app):
        Screen.__init__(self, app)
        self.deep = False
        self.cancel = threading.Event()
        self.queued = set()
        self.sel = self.scroll = 0
        self.last = None
        self.findings, self.stats = [], {}
        self.unseen = None                 # files ES can't see (counted in the background)

    def enter(self):
        self.last = rommlib.load_audit()
        self.findings = self.order(self.last["findings"]) if self.last else []
        self.stats = (self.last or {}).get("stats") or {}
        self.clamp()
        es = self.app.downloader.es
        self.app.bg(lambda: len(rommlib.find_unseen(es)), lambda n: setattr(self, "unseen", n),
                    lambda e: setattr(self, "unseen", None))

    def crumbs(self):
        return self.trail("Check games")

    def hints(self):
        y = [("Y", "Download all damaged again")] if self.redownloadable() else []
        if self.sel >= self.base:
            return AuditScreen.hints(self)
        return [("A", "Look" if self.sel == 2 else "Start")] + ([("X", pads.ACTIONS)] if self.findings else []) + y + \
            [("B", "Stop picking" if self.picking else "Back")]

    def action(self, a):
        app = self.app
        n = len(self.findings)
        if self.tile_nav(a):
            return
        if a in ("l2", "r2"):
            if self.sel >= self.base and n:
                self.jump(a == "r2")
        elif (a != "ok" or self.sel >= self.base) and (a != "x" or n) and self.pick_action(a):
            return
        elif a == "ok":
            if self.sel == 2:
                app.push(UnseenScreen(app))
            elif self.sel < 2:
                if not app.platforms:              # offline: a check would find nothing and say all is well
                    app.load_platforms()
                    app.toast(app.error or "Still loading platforms…", theme.AMBER)
                    return
                app.push(AuditScreen(app, deep=self.sel == 1))
            elif n:
                self.open_finding(self.findings[self.sel - self.base])
        elif a == "y" and self.redownloadable():
            self.redownload_all()
        else:
            Screen.action(self, a)

    def tap_tile(self, i):
        self.sel = i
        self.action("ok")

    def draw(self, c):
        k = len(self.TILES)
        tw = (W - 96 - 16 * (k - 1)) // k
        for i, (label, sub) in enumerate(self.TILES):
            r = pygame.Rect(48 + i * (tw + 16), 80, tw, 64)
            self.app.hit(r, lambda i=i: self.tap_tile(i))
            on = self.sel == i
            if on:
                focus_box(c, r, 12)
            else:
                rrect(c, theme.PANEL, r, 12)
            col = theme.FAINT
            if i == 2 and self.unseen is not None:
                sub, col = ("%s ES can't see" % plural(self.unseen, "file"), theme.AMBER) if self.unseen else \
                    ("ES sees every game file", theme.GREEN)
            text(c, self.f.h3, label, (r.left + 20, r.top + 10), theme.TEXT if on else theme.DIM, maxw=r.width - 40)
            text(c, self.f.small, sub, (r.left + 20, r.top + 37), col, maxw=r.width - 40)
        hline(c, theme.LINE, (56, 160), (W - 56, 160))
        a = self.last
        if not a:
            text(c, self.f.h3, "Not checked yet.", (56, 176), theme.TEXT)
            text(c, self.f.body, "A quick check takes minutes; findings stay here until you check again.",
                 (56, 212), theme.DIM, maxw=W - 112)
            return
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(a.get("time", 0)))
        head = "Last %s, %s: " % ("full check" if a.get("deep") else "quick check", when)
        st = self.stats
        dmg = sum(1 for f in self.findings if f["severity"] == "damaged")
        dif = len(self.findings) - dmg
        r = text(c, self.f.h3, head, (56, 176), theme.TEXT)
        if not self.findings:
            text(c, self.f.h3, "everything looks right.", (r.right + 4, 176), theme.GREEN)
            scroll_text(c, self.f.body, "%d checked, %d matched to RomM. Games not in RomM under the same file "
                        "name aren't checked (%d)." % (st.get("checked", 0), st.get("matched", 0),
                                                       st.get("unmatched", 0)),
                        (56, 212, W - 112, 3 * 26), theme.DIM, bg=theme.BG, line_h=26, slot="check-none")
            return
        x = r.right + 4
        if dmg:
            x = text(c, self.f.h3, "%d damaged" % dmg, (x, 176), theme.RED).right + 16
        if dif:
            text(c, self.f.h3, "%d differ from RomM" % dif, (x, 176), theme.AMBER)
        self.draw_rows(c, 212, self.ROWS)
