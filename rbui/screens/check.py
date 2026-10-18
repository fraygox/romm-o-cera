"""Check games: the quick and full check of what's on the box."""
import os
import threading
import time

import pygame
import rommlib
from rbui import pads, theme
from rbui.theme import W
from rbui.draw import circle, measure, state_stripe, text
from rommlib import human_size
from rbui.widgets.layout import Panel, ROWS_TOP, bubble_at, hint_actions, list_head, list_row, need_width, split
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
            st = app.stack
            if not self.cancel.is_set() and len(st) > 1 and st[-1] is self and isinstance(st[-2], CheckScreen):
                n = len(self.findings)
                app.toast("Checked %s: %s" % (plural((self.stats or {}).get("checked", 0), "game"),
                                               plural(n, "game needs", "games need") + " a look" if n
                                               else "every game looks right"), theme.AMBER if n else theme.GREEN)
                app.pop()                                # Check games shows what was found
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
            app.toast("Queued %s" % plural(len(uniq), "game"), theme.GREEN)
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
            col = theme.RED if f["severity"] == "damaged" else theme.AMBER
            queued = f["path"] in self.queued
            list_row(c, self.f, r, on, os.path.relpath(f["path"], os.path.join(rommlib.ROMS_ROOT, f["system"])),
                     f["problem"], sub_color=col, slot="check", tag="queued" if queued else f["system"],
                     tag_color=theme.GREEN if queued else theme.DIM,
                     mark=lambda ctr, f=f, col=col: self.draw_pick(c, f, ctr) or circle(c, col, ctr, 5) or True)


DOOR = {"door": "unseen"}            # the row under the findings: Games ES can't see
CHECK = {"door": "check"}            # never checked: the first row starts one

KIND = {"damaged": ("Damaged", "RED"), "differs": ("Differs from RomM", "AMBER")}
WHY = {"damaged": "It's broken or cut short, so it may not start or may stop partway. Downloading it again "
                  "replaces it; saves and states follow.",
       "differs": "It isn't RomM's copy: another dump or a trimmed image. It may well play. Downloading it "
                  "again replaces it with RomM's; saves and states follow."}


class CheckScreen(AuditScreen):
    """Check games, from On this box: the last finished check's findings as a list beside a detail
    panel (damaged first; files changed since drop out), each one downloadable again, and under
    them the way into Games ES can't see. Y runs a quick check; X Actions has the full check and
    downloads every damaged game again."""
    ROWS, ROW_H = 8, 58
    base = 0

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

    def listed(self):
        return ([] if self.last else [CHECK]) + list(self.findings) + [DOOR]

    def current(self):
        items = self.listed()
        return items[min(self.sel, len(items) - 1)]

    def crumbs(self):
        return self.trail("Check games")

    def pickable(self, f):
        return f not in (DOOR, CHECK) and AuditScreen.pickable(self, f)

    def pick_key(self, f):
        return f.get("path") or ("door", f["door"])      # the two door rows have no file

    def jump_label(self, f):
        return "ES" if f is DOOR else "Check" if f is CHECK else self.app.system_name(f["system"])

    def unpickable_why(self, f):
        return "Games ES can't see opens on its own" if f is DOOR else AuditScreen.unpickable_why(self, f)

    def hints(self):
        it = self.current()
        if it is DOOR:
            first = [("A", "Open")]
        elif it is CHECK:
            first = [("A", "Quick check")]
        elif not it.get("rom") or it["path"] in self.queued:
            first = []
        else:
            first = [("A", "Download again")]
        y = [] if it is CHECK else [("Y", "Check again" if self.last else "Quick check")]
        return self.list_hints(*(first + [("X", pads.ACTIONS)] + y))

    def list_actions(self):
        out = [("Quick check", "Missing tracks, damaged zips, cut-short discs, sizes against RomM",
                lambda: self.start(False)),
               ("Full check", "Also checksums every file and verifies CHDs: hours for a big library",
                lambda: self.start(True))]
        return out + AuditScreen.list_actions(self)

    def start(self, deep):
        app = self.app
        if not app.platforms:                      # offline: a check would find nothing and say all is well
            app.load_platforms()
            app.toast(app.error or "Still loading platforms…", theme.AMBER)
            return
        app.push(AuditScreen(app, deep=deep))

    def action(self, a):
        app = self.app
        if self.nav(a) or self.pick_action(a):
            return
        it = self.current()
        if a == "ok":
            if it is DOOR:
                app.push(UnseenScreen(app))
            elif it is CHECK:
                self.start(False)
            else:
                self.open_finding(it)
        elif a == "y":
            self.start(False)
        else:
            Screen.action(self, a)

    def name(self, f):
        return (f.get("rom") or {}).get("name") or os.path.splitext(os.path.basename(f["path"]))[0]

    def draw(self, c):
        items = self.listed()
        a = self.last
        st = self.stats
        rows = ([] if a else [("Check now", "A quick check takes a few minutes", "")]) + \
            [(self.name(f), f["problem"].split(" · ")[0], self.app.system_name(f["system"]))
             for f in self.findings] + [("Games ES can't see", "Archives a system doesn't take, games in "
                                         "the wrong folder", "")]
        lst, pan = split(need_width(self.f, rows, mark=self.showing_picks()))
        self.BUBBLE_AT = bubble_at(lst)
        if not a:
            list_head(c, self.f, lst, "Not checked yet")
        elif self.findings:
            list_head(c, self.f, lst, "%s a look" % plural(len(self.findings), "game needs", "games need"),
                      "%s, %s · %s checked" % ("Full check" if a.get("deep") else "Quick check",
                                               time.strftime("%-d %b %H:%M", time.localtime(a.get("time", 0))),
                                               "{:,}".format(st.get("checked", 0))))
        else:
            list_head(c, self.f, lst, "Every game here looks right", color=theme.GREEN)
        for i, r, on in self.row_rects(ROWS_TOP, x=lst.left, w=lst.width):
            f = items[i]
            if f is CHECK:
                list_row(c, self.f, r, on, rows[i][0], rows[i][1], slot="check-now", chevron=True, box=theme.BG2)
                continue
            if f is DOOR:
                n = self.unseen
                st_, col = ("%s" % plural(n, "file"), theme.AMBER) if n else (("None", theme.GREEN) if n == 0
                                                                              else ("", None))
                list_row(c, self.f, r, on, rows[i][0], rows[i][1], st_, col, slot="check-door", chevron=True,
                         box=theme.BG2)
                continue
            col = getattr(theme, KIND.get(f["severity"], ("", "AMBER"))[1])
            queued = f["path"] in self.queued
            list_row(c, self.f, r, on, rows[i][0], rows[i][1], sub_color=col, slot="check",
                     tag="queued" if queued else rows[i][2], tag_color=theme.GREEN if queued else theme.DIM,
                     mark=(lambda ctr, f=f: self.draw_pick(c, f, ctr)) if self.showing_picks() else None)
            state_stripe(c, r, col)                  # red or amber, as Activity marks its rows
        self.draw_panel(c, pan, self.current())

    def draw_panel(self, c, pan, it):
        p = Panel(c, self.f, pan, app=self.app, key=self.sel, slot="check-panel")
        p.actions(hint_actions(self))
        if it is DOOR:
            n = self.unseen
            p.title("Games ES can't see", "Looking…" if n is None else plural(n, "file") if n else
                    "ES sees every game file here", theme.AMBER if n else theme.GREEN if n == 0 else None)
            p.prose("Archives in a system that doesn't take them, and games in another system's folder. "
                    "Unpack them, move them where they belong, or delete them.", lines=4)
            return
        if it is CHECK:
            p.title("Check games", "Not checked yet")
            p.prose("Quick check looks for missing tracks, damaged zips and cut-short discs, and compares "
                    "sizes with RomM: a few minutes. Full check (in Actions) also checksums every file: hours "
                    "for a big library.", lines=5)
            p.prose("What it finds stays here until the next check. Damaged games can be downloaded again; "
                    "saves follow.", lines=3)
            return
        if it is None or not self.findings:
            return
        kind, col = KIND.get(it["severity"], ("Needs a look", "AMBER"))
        p.title(self.name(it), "Queued to download again" if it["path"] in self.queued else kind,
                theme.GREEN if it["path"] in self.queued else getattr(theme, col))
        try:
            here = human_size(os.path.getsize(it["path"]))
        except OSError:
            here = ""
        p.facts([("System", self.app.system_name(it["system"])), ("On this box", here)], lines=1)
        p.facts([("File", "roms/%s/%s" % (it["system"], os.path.relpath(
            it["path"], os.path.join(rommlib.ROMS_ROOT, it["system"]))))], cols=1, lines=2)
        p.prose(it["problem"].replace(" · ", ". ") + ".", label="What's wrong", lines=3)
        p.prose(WHY.get(it["severity"], "") if it.get("rom") else
                "RomM has no game under this file name, so it can't be downloaded again from here.", lines=4)
