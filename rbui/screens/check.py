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
from rbui.widgets.hero import day


WHY_UNVERIFIED = {rommlib.UNVERIFIED_ROMM: "RomM didn't send its record",
                  rommlib.UNVERIFIED_ZIP: "a zip the box can't unpack",
                  rommlib.UNVERIFIED_SLOW: "chdman verify didn't finish (see the log)",
                  rommlib.UNVERIFIED_ERROR: "the check itself went wrong (in the log)"}


def check_counts(findings):
    """(damaged, can't be read, differ from RomM), in games: a game with two
    findings counts once, as the worse of them."""
    worst = {}
    for f in findings:
        rel = os.path.relpath(f.get("path") or "?", os.path.join(rommlib.ROMS_ROOT, f.get("system") or ""))
        key = (f.get("system"), rel.split(os.sep)[0]) if f.get("path") else id(f)
        rank = 1 if f.get("io_error") else 0 if f["severity"] == "damaged" else 2
        worst[key] = min(rank, worst.get(key, 2))
    n = list(worst.values())
    return n.count(0), n.count(1), n.count(2)


def unverified_line(st):
    """What a check couldn't verify, as one line ("12 games not verified
    (missing chdman)"), or ""."""
    n = st.get("unverified")
    if not n:
        return ""
    tools = st.get("unverified_tools") or ["chdman"]        # a check saved by v0.9.0: CHDs only
    by = st.get("unverified_by") or {}
    missing = [t for t in tools if t not in WHY_UNVERIFIED]
    bits = (["missing %s" % ", ".join(missing)] if missing else []) + \
        [("%s for %d" % (WHY_UNVERIFIED[t], by[t]) if by.get(t) and len(tools) > 1 else WHY_UNVERIFIED[t])
         for t in tools if t in WHY_UNVERIFIED]
    return "%s not verified (%s)" % (plural(n, "game"), "; ".join(bits))


def unmatched_line(st):
    n = st.get("unmatched")
    return "%s not in RomM under the same file name" % plural(n, "game") if n else ""


def _count(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0


def not_fully_checked(st):
    """Games a check couldn't compare with RomM in full: not verified, or not
    in RomM under their file name (only their tracks and disc images looked at)."""
    st = st if isinstance(st, dict) else {}
    return _count(st.get("unverified")) + _count(st.get("unmatched"))


def games_in(findings):
    """How many games the findings are about (a game with two counts once)."""
    return sum(check_counts(findings))


def check_result(st, findings, deep):
    """What a finished check found, as one line and its colour, for the toast."""
    st = st or {}
    if st.get("no_games_folder"):
        return "The games folder %s wasn't found: nothing was checked" % rommlib.ROMS_ROOT, theme.AMBER
    if not st.get("checked"):
        return "No games found to check", theme.AMBER
    head = "Checked %s: " % plural(st["checked"], "game")
    if findings:
        return head + plural(games_in(findings), "game needs", "games need") + " a look", theme.AMBER
    k = not_fully_checked(st)
    if k:
        return head + "nothing wrong found · %d not fully checked" % k, theme.GREEN
    return head + ("every game looks right" if deep else "nothing wrong found"), theme.GREEN


class QueuedAgain:
    """The findings queued to download again (path -> its job). One counts while its
    download waits, runs or is done; once that failed or was cancelled it can be
    queued again (its row says so instead of "queued")."""

    def __init__(self):
        self.jobs = {}

    def add(self, path, job=None):
        self.jobs[path] = job

    def __contains__(self, path):
        if path not in self.jobs:
            return False
        j = self.jobs[path]
        return j is None or getattr(j, "state", None) not in (rommlib.Job.FAILED, rommlib.Job.CANCELLED)

    def done(self, path):
        """Its download finished (RomM's copy is on the box now)."""
        j = self.jobs.get(path)
        return j is not None and getattr(j, "state", None) == rommlib.Job.DONE

    def tag(self, path):
        """The row's tag while it counts: "downloaded" once done, else "queued"."""
        return "downloaded" if self.done(path) else "queued"

    def failed(self, path):
        """Why the download queued for this finding failed (its job's message), else None."""
        j = self.jobs.get(path)
        if j is not None and getattr(j, "state", None) == rommlib.Job.FAILED:
            return getattr(j, "message", "") or "It didn't finish"
        return None


class AuditScreen(ListScreen):
    """Check what's on the box against RomM; damaged games can be downloaded
    again. Red: damaged or incomplete. Amber: not RomM's copy (another dump,
    a trimmed image…), which may well play. Picked findings (amber ones
    too) download again together from the X Actions menu."""
    LOAD_ERROR_HEAD = "The check didn't finish"
    ROWS, ROW_H = 8, 62
    PICKS, PICK_WHAT, JUMP = True, "findings", "System"
    running = None        # the last check's "finished" event: one check runs at a time, even after B

    def __init__(self, app, deep=False):
        super().__init__(app)
        self.deep = deep
        self.cancel = threading.Event()
        self.finished = AuditScreen.running = threading.Event()
        self.where = ""
        self.findings = None
        self.stats = None
        self.sel = self.scroll = 0
        self.queued = QueuedAgain()
        self.t0 = time.time()
        by_sys = {}
        for p in app.platforms:
            by_sys.setdefault(p["_system"], []).append(p["id"])

        def progress(system, name):
            self.where = "roms/%s/%s" % (system, name)

        def run():
            try:
                return rommlib.audit(app.api, by_sys, deep=deep, progress=progress, cancel=self.cancel)
            finally:
                self.finished.set()                      # stopped for real: a new check may start

        def done(res):
            f, self.stats = res
            self.findings = self.order(f)
            if self.cancel.is_set():
                app.toast("Check stopped; the last result is kept" if os.path.exists(rommlib.audit_path())
                          else "Check stopped; nothing was saved")
                return
            if (self.stats or {}).get("checked"):        # nothing checked: keep the last
                try:
                    rommlib.save_audit(self.findings, self.stats, deep)
                except OSError as e:
                    rommlib.log("audit save: %s" % e)
            st = app.stack
            if len(st) > 1 and st[-1] is self and isinstance(st[-2], CheckScreen):
                app.toast(*check_result(self.stats, self.findings, deep))
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

    def ours(self, f):
        """The finding is in a game the app downloaded (its records): only those are downloaded
        again over; a copy put here another way is never written over or deleted by the app."""
        return self.app.library.owns(f["path"])    # as the app wrote it (a file put in its place isn't)

    def redownloadable(self):
        """What "Download all damaged again" takes: not a file the box
        couldn't read (the drive may be failing or gone: downloading every game again onto it
        wouldn't help)."""
        return [f for f in (self.findings or []) if f["severity"] == "damaged" and f.get("rom")
                and not f.get("io_error") and f["path"] not in self.queued]

    def jump_label(self, f):
        return f["system"]

    def pickable(self, f):
        return bool(f.get("rom")) and f["path"] not in self.queued

    def unpickable_why(self, f):
        return "Already queued" if f["path"] in self.queued else \
            "Not in RomM under this name — can't download it again" if not f.get("rom") else "Can't download it again"

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
            "from RomM too." + self.by_name_note(todo) + self.theirs_note(todo))) if todo else None)]

    @staticmethod
    def by_name_note(todo):
        """The confirm's word on games RomM has only under another name: matched by title,
        so another release of the game than the one here."""
        named = sorted({f["rom"].get("fs_name") or f["rom"].get("name") or "" for f in todo if f.get("via") == "name"})
        if not named:
            return ""
        return " %s matched by title only, so RomM's copy is another release: %s." % (
            plural(len(named), "game is", "games are"), ", ".join(named))

    def inside_folder(self, f):
        """The finding is a file inside a game's folder (a track, a disc of a set)."""
        return os.path.dirname(os.path.normpath(f["path"])) != os.path.join(rommlib.ROMS_ROOT, f["system"])

    @staticmethod
    def renamed(f):
        """RomM's copy has another file name than the broken one on the box."""
        new = (f["rom"].get("fs_name_no_ext") or os.path.splitext(f["rom"].get("fs_name") or "")[0]).lower()
        return bool(f.get("via")) or (new and new != os.path.splitext(os.path.basename(f["path"]))[0].lower())

    def replaced(self, f):
        """The files downloading this finding's game again replaces when the app didn't download
        it: exactly the game's own files (their_files) and the broken file. [] for the app's own."""
        if self.ours(f):
            return []
        mine = self.app.library.their_files(f["rom"], f["system"])
        return sorted({os.path.normpath(p) for p in mine} | {os.path.normpath(f["path"])})

    def replaced_text(self, f):
        sysdir = os.path.join(rommlib.ROMS_ROOT, f["system"])
        rel = [os.path.relpath(p, sysdir) for p in self.replaced(f)]
        shown = ", ".join(rel[:8])
        return shown + (" and %d more" % (len(rel) - 8) if len(rel) > 8 else "")

    def theirs_note(self, todo):
        """The confirm's word on games the app didn't download: the files RomM's copies replace."""
        theirs = [f for f in {f["rom"]["id"]: f for f in todo}.values() if not self.ours(f)]
        if not theirs:
            return ""
        files = [os.path.relpath(p, os.path.join(rommlib.ROMS_ROOT, f["system"]))
                 for f in theirs for p in self.replaced(f)]
        shown = ", ".join(files[:8]) + (" and %d more" % (len(files) - 8) if len(files) > 8 else "")
        return " Not as RomM-o-Cera downloaded them (%s: put here another way, or changed since), so these files are replaced: %s. Notes, saves and other " \
               "files beside them stay." % (plural(len(theirs), "game"), shown)

    def redownload(self, f, pins=None):
        app = self.app
        # always the broken file shown, which the user asked to replace: RomM's copy can land under
        # another name, or unpack beside it (a zipped cue/bin for a junk .chd of that name); the
        # downloader never deletes a path the new copy holds
        job = app.downloader.enqueue(dict(f["rom"]), f["system"], replace=self.replaced(f) or [f["path"]],
                                     pins=pins)
        self.queued.add(f["path"], job)

    def action(self, a):
        app = self.app
        if self.findings is None:
            if a in ("back", "quit"):
                self.cancel.set()
                app.toast("Stopping the check…")
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
            if self.inside_folder(f):
                how = "RomM's copy of the game is downloaded into its folder again (saves are kept)."
            elif self.renamed(f):
                how = "RomM's copy (%s) is downloaded; then saves move to its name and the broken file is " \
                      "deleted." % (f["rom"].get("fs_name") or name)
            else:
                how = "RomM's copy replaces the one on the box (saves are kept)."
            if f.get("via") == "name":
                how += " RomM has it only under another name, matched by title: it's another release."
            if not self.ours(f):
                how += " It isn't as RomM-o-Cera downloaded it (put here another way, or changed since), so these files are replaced: %s." % self.replaced_text(f)
            if f.get("io_error"):
                how = "The box couldn't read this file: check the drive or card first. " + how
            pins = rommlib.pin_files(self.replaced(f) or [f["path"]])     # what the confirm names, now
            app.modal = Confirm(app, "Download %s again?" % name, "%s. %s" % (f["problem"].split(" · ")[0], how),
                                "Download again",
                                lambda: (self.redownload(f, pins), app.toast("Queued: %s" % name, theme.GREEN)))

    def redownload_all(self):
        todo = self.redownloadable()
        n = len({f["rom"]["id"] for f in todo})
        self.redownload_many(todo, "Download %s again?" % plural(n, "damaged game"),
                             "RomM's copies replace the damaged ones. Games that only differ from RomM "
                             "(amber) are left alone." + self.by_name_note(todo) + self.theirs_note(todo))

    def redownload_many(self, todo, title, body):
        """Each game once (two findings can be one game), after a confirm."""
        app = self.app
        uniq = {}
        for f in todo:
            uniq.setdefault(f["rom"]["id"], f)

        pins = {rid: rommlib.pin_files(self.replaced(f) or [f["path"]]) for rid, f in uniq.items()}

        def go():
            for rid, f in uniq.items():
                self.redownload(f, pins[rid])
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
        if not self.findings and (st.get("no_games_folder") or not st.get("checked")):
            text(c, self.f.h3, check_result(st, self.findings, self.deep)[0] + ".", (56, 86), theme.AMBER)
            return
        dmg = sum(1 for f in self.findings if f["severity"] == "damaged")
        dif = len(self.findings) - dmg
        head = "%d checked, %d matched to RomM. " % (st.get("checked", 0), st.get("matched", 0))
        if st.get("unverified"):
            head += unverified_line(st) + ". "
        if not self.findings:
            text(c, self.f.h3, head + ("Everything looks right." if self.deep and not not_fully_checked(st)
                                       else "Nothing wrong found."), (56, 86), theme.GREEN)
            if st.get("unmatched"):
                text(c, self.f.body, "%s: only their tracks and disc images were looked at." % unmatched_line(st),
                     (56, 124), theme.DIM)
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
                     f["problem"], sub_color=col, slot="check", tag=(self.queued.tag(f["path"]) if isinstance(self.queued, QueuedAgain) else "queued") if queued else f["system"],
                     tag_color=theme.GREEN if queued else theme.DIM,
                     mark=lambda ctr, f=f, col=col: self.draw_pick(c, f, ctr) or circle(c, col, ctr, 5) or True)


DOOR = {"door": "unseen"}            # the row under the findings: Games ES can't see
CHECK = {"door": "check"}            # never checked: the first row starts one
NOTES = {"door": "notes"}            # what the last check couldn't verify or match, under DOOR

KIND = {"damaged": ("Damaged", "RED"), "differs": ("Differs from RomM", "AMBER")}
WHY = {"damaged": "It's broken or cut short, so it may not start or may stop partway. Downloading it again "
                  "replaces it; saves and states follow.",
       "differs": "It isn't RomM's copy: another dump or a trimmed image. It may well play. Downloading it "
                  "again replaces it with RomM's; saves and states follow."}
WHY_IO = ("The box got an error reading this file. The drive or card may be failing, loose or unplugged, "
          "so it isn't in Download all damaged again. Check the drive first; downloading this one again "
          "writes a fresh copy.")


def when(t):
    return day(t, clock=True)


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
        self.queued = QueuedAgain()
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
        return ([] if self.last else [CHECK]) + list(self.findings) + [DOOR] + ([NOTES] if self.notes() else [])

    def notes(self):
        """The last check's lines on what it couldn't verify or match ([] for none)."""
        st = self.stats or {}
        return [x for x in (unverified_line(st), unmatched_line(st)) if x] if self.last else []

    def carried(self):
        """Findings of the last full check a later quick check kept (it can't find them itself)."""
        return [f for f in self.findings if f.get("full_time")] if self.last and not self.last.get("deep") else []

    def current(self):
        items = self.listed()
        return items[min(self.sel, len(items) - 1)]

    def crumbs(self):
        return self.trail("Check games")

    def pickable(self, f):
        return f not in (DOOR, CHECK, NOTES) and AuditScreen.pickable(self, f)

    def pick_key(self, f):
        return f.get("path") or ("door", f["door"])      # the door rows have no file

    def jump_label(self, f):
        return "ES" if f is DOOR else "Check" if f in (CHECK, NOTES) else self.app.system_name(f["system"])

    def unpickable_why(self, f):
        return "Games ES can't see opens on its own" if f is DOOR else \
            "Nothing to download here" if f is NOTES else AuditScreen.unpickable_why(self, f)

    def hints(self):
        it = self.current()
        if it is DOOR:
            first = [("A", "Open")]
        elif it is CHECK:
            first = [("A", "Quick check")]
        elif it is NOTES:
            first = []
        elif not it.get("rom") or it["path"] in self.queued:
            first = []
        else:
            first = [("A", "Download again")]
        y = [] if it is CHECK else [("Y", "Quick check")]
        return self.list_hints(*(first + [("X", pads.ACTIONS)] + y))

    def list_actions(self):
        out = [("Quick check", "Missing tracks, damaged zips, cut-short discs, sizes against RomM",
                lambda: self.start(False)),
               ("Full check", "Also checksums every file and verifies CHDs: hours for a big library",
                lambda: self.start(True))]
        return out + AuditScreen.list_actions(self)

    def start(self, deep):
        app = self.app
        if AuditScreen.running is not None and not AuditScreen.running.is_set():
            app.toast("The last check is still stopping; try again in a moment", theme.AMBER)
            return
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
            elif it is not NOTES:
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
             for f in self.findings] + \
            [("Games ES can't see", "Archives a system doesn't take, games in the wrong folder", "")] + \
            ([("Not fully checked", " · ".join(self.notes()), "")] if self.notes() else [])
        lst, pan = split(need_width(self.f, rows, mark=self.showing_picks()))
        self.BUBBLE_AT = bubble_at(lst)
        if not a:
            list_head(c, self.f, lst, "Not checked yet")
        else:
            note = "%s, %s · %s checked" % ("Full check" if a.get("deep") else "Quick check", when(a.get("time")),
                                            "{:,}".format(st.get("checked", 0)))
            kept = self.carried()
            if kept:
                note += " · %d from the full check of %s" % (len(kept), when(max(f["full_time"] for f in kept)))
            if self.findings:
                list_head(c, self.f, lst, "%s a look" % plural(games_in(self.findings), "game needs", "games need"),
                          note)
            else:       # a quick check can't vouch for checksums, nor any check for games it couldn't compare
                list_head(c, self.f, lst, "Every game here looks right" if a.get("deep") and not not_fully_checked(st)
                          else "Nothing wrong found", note, color=theme.GREEN)
        for i, r, on in self.row_rects(ROWS_TOP, x=lst.left, w=lst.width):
            f = items[i]
            if f is CHECK:
                list_row(c, self.f, r, on, rows[i][0], rows[i][1], slot="check-now", chevron=True, box=theme.BG2)
                continue
            if f is NOTES:
                list_row(c, self.f, r, on, rows[i][0], rows[i][1], box=theme.BG2, slot="check-notes")
                continue
            if f is DOOR:
                n = self.unseen
                st_, col = ("%s" % plural(n, "file"), theme.AMBER) if n else (("None", theme.GREEN) if n == 0
                                                                              else ("", None))
                list_row(c, self.f, r, on, rows[i][0], rows[i][1], st_, col, slot="check-door", chevron=True,
                         box=theme.BG2)
                continue
            col = getattr(theme, self.kind(f)[1])
            queued = f["path"] in self.queued
            failed = not queued and isinstance(self.queued, QueuedAgain) and self.queued.failed(f["path"])
            list_row(c, self.f, r, on, rows[i][0], rows[i][1], sub_color=col, slot="check",
                     tag=(self.queued.tag(f["path"]) if isinstance(self.queued, QueuedAgain) else "queued") if queued else "failed" if failed else rows[i][2],
                     tag_color=theme.GREEN if queued else theme.RED if failed else theme.DIM,
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
        if it is NOTES:
            st = self.stats or {}
            p.title("Not fully checked", " · ".join(self.notes()))
            if st.get("unverified"):
                p.prose("%s. A missing tool can be installed. Games that couldn't be verified are "
                        "tried again on the next check; some (a zip this box can't unpack) never can be." % unverified_line(st), label="Not verified", lines=4)
            if st.get("unmatched"):
                p.prose("%s: only their tracks and disc images were looked at, not their sizes or checksums "
                        "against RomM." % unmatched_line(st), label="Not in RomM", lines=4)
                names = [n for n in st.get("unmatched_names") or [] if isinstance(n, str)]
                if names:              # which they are (an older version's check didn't keep their names)
                    more = _count(st.get("unmatched")) - len(names)
                    p.prose(" · ".join(names) + (" · and %d more" % more if more > 0 else ""),
                            label="Which games")
            return
        if it is None or not self.findings:
            return
        kind, col = self.kind(it)
        failed = self.queued.failed(it["path"]) if isinstance(self.queued, QueuedAgain) else None
        done = isinstance(self.queued, QueuedAgain) and self.queued.done(it["path"])
        p.title(self.name(it), "Downloaded again: Quick check to see it" if done else
                "Queued to download again" if it["path"] in self.queued else
                "Download failed" if failed else kind,
                theme.GREEN if it["path"] in self.queued else theme.RED if failed else getattr(theme, col))
        try:
            here = human_size(os.path.getsize(it["path"]))
        except OSError:
            here = ""
        last = self.last or {}
        # (the day alone: the fact has one line, and the list's header gives the time)
        found = ("Full check, %s" % day(it["full_time"])) if it.get("full_time") else \
            "%s, %s" % ("Full check" if last.get("deep") else "Quick check", day(last.get("time")))
        p.facts([("System", self.app.system_name(it["system"])), ("On this box", here), ("Found by", found)],
                lines=2)
        p.facts([("File", "roms/%s/%s" % (it["system"], os.path.relpath(
            it["path"], os.path.join(rommlib.ROMS_ROOT, it["system"]))))], cols=1, lines=2)
        p.prose(it["problem"].replace(" · ", ". ") + ".", label="What's wrong", lines=3)
        if failed:
            p.prose(failed.rstrip(".") + ".", label="Downloading it again failed", lines=3)
        p.prose(WHY_IO if it.get("io_error") else
                "RomM has no game under this file name, so it can't be downloaded again from here."
                if not it.get("rom") else WHY.get(it["severity"], "") + ("" if self.ours(it) else
                " It isn't as RomM-o-Cera downloaded it (put here another way, or changed since): downloading it again replaces its files (listed in the "
                "confirm); notes, saves and other files beside it stay."), lines=5)

    @staticmethod
    def kind(f):
        """(what the finding is, theme colour name)."""
        if f.get("io_error"):
            return "Can't be read", "RED"
        return KIND.get(f["severity"], ("Needs a look", "AMBER"))
