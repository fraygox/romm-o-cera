"""Activity: the downloads, and the conversions on this box, on one screen."""
import math
import time

import pygame
import rommlib
from rommlib import human_size
from rbui import pads, theme
from rbui.draw import card, circle, installed_mark, marquee, measure, progress_bar, state_stripe, text
from rbui.screens.base import ListScreen
from rbui.screens.box_data import (RESULT_LABELS, conversion_left, conversion_rows, conversion_status, plural,
                                   work_label,
                                   result_line, time_left)
from rbui.screens.compress import CompressScreen
from rbui.screens.installed import ConversionsScreen
from rbui.screens.check import AuditScreen
from rbui.modals import ActionMenu, Confirm
from rbui.widgets.chips import button_chip
from rbui.widgets.layout import CONTENT, Panel, list_head, list_row, split
from rbui.widgets.tiles import cover

ACT_HEAD = pygame.Rect(CONTENT.left, CONTENT.top, CONTENT.width, 96)       # what's going on, the time, the counts
ACT_BODY = pygame.Rect(CONTENT.left, ACT_HEAD.bottom + 16, CONTENT.width, CONTENT.bottom - ACT_HEAD.bottom - 16)
SPEED_TAU = 5.0             # seconds: the headline's speed is smoothed over about this long
RATE_WINDOW = 60.0          # seconds: once it has RATE_MIN of them, the bytes that came in over this long
LABEL_HOLD = 2.0            # seconds a new headline must last before it replaces the one shown
RATE_MIN = 3.0              # set the pace (many small files: the time between them counts too)
RESULT_COLOURS = {"ok": "GREEN", "failed": "RED", "stopped": "AMBER", "skipped": "AMBER", "waiting": "AMBER"}
PAUSED_WHY = {"hold": "paused by you", "game": "paused while you play", "es": "paused while ES is busy"}


def sentence(msg):
    """msg starting with a capital, the rest as it is (str.capitalize would turn
    "ES" into "es"); never a file name it starts with ("installed.json")."""
    first = msg.split(" ", 1)[0]
    return msg if "." in first or "/" in first else msg[:1].upper() + msg[1:]


class RateMeter:
    """How fast the queue really goes: the bytes downloads add, over the time they took,
    the last RATE_WINDOW seconds of it. Unlike the transfer speed it doesn't drop to nothing
    between two small files, nor jump with each one's burst. Kept on the app, which measures
    every frame (App.tick_downloads): Activity has the pace as soon as it opens."""

    GAP = 5.0                 # seconds between two looks past which the clock jumped (or the app stood still)

    def __init__(self):
        self.seen, self.total, self.marks = {}, 0, []
        self.last = None          # (when, going, what it said): the app and Activity both ask, once a frame

    def rate(self, jobs, going, now):
        """Bytes a second over the window, or None until it has RATE_MIN seconds of downloading.
        going: something downloads (not paused, nor all of it on hold). Counts only what came
        over the network (Job.got): a resumed .part or a file already here isn't speed."""
        if self.last and self.last[1] == going and 0 <= now - self.last[0] < 0.05:
            return self.last[2]                    # asked again this frame: no second look at every job
        if not going or (self.marks and not 0 <= now - self.marks[-1][0] < self.GAP):
            self.seen, self.total, self.marks = {}, 0, []    # stopped, or the clock jumped: afresh
            if not going:
                self.last = (now, going, None)
                return None
        seen, fresh = {}, not self.marks
        for j in jobs:
            b = getattr(j, "got", 0) or 0
            # one queued since counts from nothing: a small file can come and finish between two looks
            was = self.seen.get(j, b if fresh else 0)
            if b > was:
                self.total += b - was
            seen[j] = b
        self.seen = seen
        if not self.marks or now - self.marks[-1][0] >= 0.5:
            self.marks.append((now, self.total))
        while len(self.marks) > 2 and now - self.marks[1][0] >= RATE_WINDOW:
            self.marks.pop(0)
        t0, b0 = self.marks[0]
        pace = None if now - t0 < RATE_MIN or self.total <= b0 else (self.total - b0) / (now - t0)
        self.last = (now, going, pace)
        return pace


class DownloadsScreen(ListScreen):
    """Activity. Left: the download queue in the order it runs, then finished downloads (newest
    first). A: per-download menu (reorder, pause, cancel, retry, Play now); X: pause or resume
    everything, and pick several to pause, resume, cancel, retry or remove at once; Y clears
    the finished ones. Right: compressing on this box, the running conversion (A pause or
    resume, X stop it), the queue, what finished lately, and the way into Compress (Y).
    Left/right moves between the two; the line on top says when all of it is done."""
    ROWS, ROW_H = 6, 64
    PICKS, PICK_WHAT = True, "downloads"
    focus = "list"              # or "panel"
    psel = 0

    def __init__(self, app):
        super().__init__(app)
        self.sel = self.scroll = 0
        self.convs, self.held, self.conv_at = [], False, 0.0
        self.speed, self.speed_at = None, 0.0
        self.shown = None                    # the headline on screen: (kind, line, colour, when last said)
        self.work_said = None                # the batch's line, as state_line last said it
        self.time_shown = None               # (line, when) last drawn on the right
        self.time_est = None                 # (secs, when): the last time for all of it
        self.time_w = 0                      # the time's room, kept while it's said
        self.refresh()
        if not self.view():
            self.focus = "panel"

    def crumbs(self):
        return ["Activity"]

    def nav_key(self):
        return self.focus, id(getattr(self, "sel_job", None))

    def listed(self):
        drawing = getattr(self, "_drawing", None)       # the list this frame is drawing: one look at the queue a frame
        return drawing if drawing is not None else self.view()

    def view(self):
        d = self.app.downloader
        jobs = list(d.jobs)
        paused = rommlib.Job.PAUSED
        pend = [j for j in jobs if j.active or j.state == paused]
        if len(pend) == len(jobs):
            return pend
        waiting = set(map(id, pend))           # a set: a list's "in" made this quadratic (4,000 jobs: ¼ s a frame)
        return pend + [j for j in reversed(jobs) if id(j) not in waiting]

    def refresh(self):
        """What the conversion worker publishes and the space forecast: disk reads, once a second."""
        d = self.app.downloader
        self.convs, self.held, self.conv_at = conversion_rows(), rommlib.conversions_held(), time.time()
        self._chd_st = {}
        self._fc = rommlib.queue_forecast(d.jobs, rommlib.free_bytes(), rommlib.keep_free_bytes(self.app.cfg),
                                          rommlib.chd_enabled(self.app.cfg), self.app.cfg)
        self._run = self.run_counts(d.jobs)

    RUN_SHOWN = 20                # a queue this long says how far along it is, and what's left by platform

    def run_counts(self, jobs):
        """How far along a big run is, for the header: "12 of 4,411 done", then what's still
        to come by platform, the most first ("" for a short queue). Once a second, not a frame."""
        J, app = rommlib.Job, self.app
        left, done = {}, 0
        for j in list(jobs):
            if j.active or j.state == J.PAUSED:
                left[j.system] = left.get(j.system, 0) + 1
            elif j.state == J.DONE and not j.restored:
                done += 1
        n = sum(left.values())
        if n < self.RUN_SHOWN:
            return ""
        out = "{:,} of {:,} done".format(done, done + n)
        if len(left) > 1:
            out += " · " + ", ".join("{:,} {}".format(k, app.system_name(sy))
                                     for sy, k in sorted(left.items(), key=lambda x: -x[1]))
        return out

    # -- the compressing panel
    def running(self):
        return next((r for r in self.convs if r["kind"] == "running"), None)

    def queued(self):
        return [r for r in self.convs if r["kind"] == "queued"]

    def queue_room(self, top):
        """How many queued conversions the panel lists under its top card (the rest: "+N more")."""
        _, pan = split(None, ACT_BODY)
        room = pan.height - 2 * Panel.PAD - 40 - (58 + 12) - ((114 if top == "running" else 58) + 8 if top else 0)
        n = len(self.queued())
        fit = max(0, room // 66)
        return n if n <= fit else max(0, (room - 26) // 66)

    def panel_items(self):
        """The panel's focusable cards, top down: the running conversion (or "paused" while the
        worker waits), the queue that fits, then Compress games."""
        run = self.running()
        top = [run] if run else [{"kind": "held"}] if self.held else []
        q = self.queued()
        return top + q[:self.queue_room(top[0]["kind"] if top else None)] + [{"kind": "more"}]

    def panel_item(self):
        items = self.panel_items()
        self.psel = max(0, min(self.psel, len(items) - 1))
        return items[self.psel]

    def toggle_hold(self):
        app = self.app
        if self.held:
            rommlib.hold_conversions(False)
            app.spawn_helper("rb_chd.py")             # flock'd: no-op while one runs
            app.toast("Compressing resumed", theme.GREEN)
        else:
            rommlib.hold_conversions(True)
            app.toast("Compressing paused — it carries on where it stopped", theme.AMBER)
        self.refresh()

    def ask_stop(self, r):
        app = self.app

        def stop():
            rommlib.stop_conversion(r["name"])
            app.toast("Stopping %s — the original stays as it is" % self.conv_title(r))
            self.refresh()
        app.modal = Confirm(app, "Stop compressing?", "%s: what's compressed so far is dropped; the original stays "
                            "as it is. Conversions can queue it again." % self.conv_title(r), "Stop", stop,
                            danger=True)

    def queue_first(self, r):
        if not rommlib.queue_task_first(r["name"]):
            self.app.toast("It's converting now, or done already", theme.AMBER)
        self.refresh()

    def take_out(self, r):
        if rommlib.drop_queue_task(r["name"]):
            self.app.toast("Taken out of the queue — the original stays as it is")
        else:
            self.app.toast("It's converting now, or done already", theme.AMBER)
        self.refresh()

    def compress(self):
        self.app.push(CompressScreen(self.app))

    def panel_action(self, a):
        """A button while the panel has the focus; False when it isn't one it takes."""
        items = self.panel_items()
        it = self.panel_item()
        k = it["kind"]
        if a in ("up", "down"):
            self.psel = max(0, min(len(items) - 1, self.psel + (1 if a == "down" else -1)))
        elif a == "left":
            if self.view():
                self.focus = "list"
        elif a == "ok":
            if k in ("running", "held"):
                self.toggle_hold()
            elif k == "queued":
                self.queue_first(it)
            else:
                self.compress()
        elif a == "x" and k == "running":
            self.ask_stop(it)
        elif a == "x" and k == "queued":
            self.take_out(it)
        elif a == "y":
            self.compress()
        elif a in ("l1", "r1", "right", "x"):
            pass
        else:
            return False
        return True

    def tap_panel(self, i):
        if self.focus == "panel" and self.psel == i:
            self.action("ok")
        else:
            self.focus, self.psel = "panel", i

    def hints(self):
        d = self.app.downloader
        if self.focus == "panel":
            k = self.panel_item()["kind"]
            h = {"running": [("A", "Resume" if self.held else "Pause"), ("X", "Stop this one")],
                 "held": [("A", "Resume")],
                 "queued": [("A", "Convert next"), ("X", "Take out")]}.get(k, [("A", "Compress games")])
            if k != "more":
                h.append(("Y", "Compress games"))
            if d.jobs:                                 # (what view() lists: every job)
                h.append(("LEFT RIGHT", "Downloads"))
            return h + [("L2 R2", "Conversions"), ("B", "Back")]
        h = [("A", "Manage"), ("X", pads.ACTIONS)] if d.jobs else []
        P = rommlib.Job.PAUSED
        if any(not (j.active or j.state == P) for j in d.jobs):
            h.append(("Y", "Clear finished"))
        h += [] if self.picking else [("LEFT RIGHT", "Compressing")]
        return self.list_hints(*h + [("L2 R2", "Conversions")])

    def pick_key(self, j):
        return (j.rom.get("id"), j.system)

    def list_actions(self):
        d, app = self.app.downloader, self.app
        e = []
        if d.pending_count():
            if d.hold:
                e.append(("Resume all", "", lambda: (d.release_all(), app.toast("Downloads resumed", theme.GREEN))))
            else:
                e.append(("Pause all", "The partial files are kept", lambda: (
                    d.hold_all(), app.toast("Downloads paused — partial files are kept", theme.AMBER))))
        J = rommlib.Job
        failed = [j for j in list(d.jobs) if j.state == J.FAILED]
        if failed:
            e.append(("Retry the %s that failed" % ("{:,}".format(len(failed)) if len(failed) > 1 else "one"),
                      "A file that stopped partway picks up where it was",
                      self.each(d.resume_job, failed, "Queued %s again")))
        systems = sorted({j.system for j in list(d.jobs) if j.state == J.DONE and not j.restored})
        if systems:
            names = [app.system_name(sy) for sy in systems]
            e.append(("Check the games on %s" % (names[0] if len(names) == 1 else "these %d platforms" % len(names)),
                      "Every file there read against RomM's checksums again" +
                      (": " + ", ".join(names) if len(names) > 1 else ""),
                      lambda: app.push(AuditScreen(app, deep=True, systems=systems))))
        if any(not (j.active or j.state == J.PAUSED) for j in d.jobs):
            e.append(("Clear finished", "Done, failed and cancelled ones leave the list", self.clear_finished))
        return e


    def clear_finished(self):
        n = self.app.downloader.clear_finished()
        if n:
            self.app.toast("Cleared %d finished" % n)

    def each(self, fn, todo, msg):
        """An action on several downloads: fn for each, picking ends, a toast says how many (msg)."""
        def run():
            with self.app.downloader.one_save():     # one save for all of them, not one each
                for j in todo:
                    fn(j)
            self.picks.clear()
            self.picking = False
            self.app.toast(msg % plural(len(todo), "download"))
        return run

    def pick_entries(self, jobs):
        """Pause, resume, cancel, retry or remove the picked downloads (each
        only those it applies to); cancelling asks first."""
        app, d, J = self.app, self.app.downloader, rommlib.Job
        live = [j for j in jobs if j.active and j.state != J.PAUSED]
        paused = [j for j in jobs if j.state == J.PAUSED]
        again = [j for j in jobs if j.state in (J.FAILED, J.CANCELLED)]
        over = [j for j in jobs if not (j.active or j.state == J.PAUSED)]

        each = self.each
        stop = live + paused
        cancel = each(d.cancel_job, stop, "Cancelled %s")
        e = []
        if live:
            e.append(("Pause %d" % len(live), "Keeps their partial files", each(d.pause_job, live, "Paused %s")))
        if paused:
            e.append(("Resume %d" % len(paused), "", each(d.resume_job, paused, "Resumed %s")))
        if stop:
            e.append(("Cancel %d" % len(stop), "Their partial files are deleted", lambda: setattr(app, "modal", Confirm(
                app, "Cancel %s?" % plural(len(stop), "download"),
                "Their partial files are deleted; finished downloads in the pick stay as they are.",
                "Cancel %d" % len(stop), cancel, danger=True))))
        if again:
            e.append(("Retry %d" % len(again), "Failed and cancelled ones download again",
                      each(d.resume_job, again, "Queued %s again")))
        if over:
            e.append(("Remove %d from the list" % len(over), "Finished games stay on the box",
                      each(d.remove, over, "Removed %s from the list")))
        return e

    def follow(self, jobs):
        """Keep the highlight on the same download when the list reorders."""
        sj = getattr(self, "sel_job", None)
        if sj in jobs:
            self.sel = jobs.index(sj)

    def tap_row(self, i):
        """The first tap also remembers the download, for follow(); from the panel it only
        brings the focus back to the list."""
        jobs = self.view()
        if self.focus != "list":
            self.focus, self.sel = "list", i
            self.sel_job = jobs[i] if i < len(jobs) else None
            return
        if self.sel != i:
            self.sel_job = jobs[i] if i < len(jobs) else None
        super().tap_row(i)

    def action(self, a):
        app = self.app
        jobs = self.view()
        self.follow(jobs)
        if a in ("l2", "r2"):
            app.push(ConversionsScreen(app))
        elif self.focus == "panel" and (self.panel_action(a) or a != "back"):
            pass
        elif a == "right" and not self.picking:
            self.focus = "panel"
        elif a == "left":
            pass
        elif self.nav(a):
            self.sel_job = jobs[min(self.sel, len(jobs) - 1)]
        elif jobs and self.pick_action(a):
            jobs = self.view()
            if jobs:
                self.sel_job = jobs[min(self.sel, len(jobs) - 1)]
        elif a == "ok" and jobs:
            self.menu(jobs[min(self.sel, len(jobs) - 1)])
        elif a == "y":
            self.clear_finished()
        else:
            super().action(a)

    def menu(self, j):
        app, d = self.app, self.app.downloader
        J = rommlib.Job
        queued = [x for x in d.jobs if x.active or x.state == J.PAUSED]
        e = []
        if j in queued and len(queued) > 1:
            i = queued.index(j)
            e += [("Move to top", "Downloads next", (lambda: d.move(j, "top")) if i else None),
                  ("Move up", "", (lambda: d.move(j, "up")) if i else None),
                  ("Move down", "", (lambda: d.move(j, "down")) if i < len(queued) - 1 else None),
                  None]
        if j.state in (J.QUEUED, J.PAUSED) and not j.force and (d.hold or not rommlib.in_download_hours(app.cfg)):
            e.append(("Download now", "Start this one anyway (download hours / Pause all)", lambda: d.force_job(j)))
        if j.active:
            e.append(("Pause", "Keeps the partial file; resume any time", lambda: d.pause_job(j)))
            e.append(("Cancel download", "", lambda: self.confirm_cancel(j)))
        elif j.state == J.PAUSED:
            e.append(("Resume", "", lambda: d.resume_job(j)))
            e.append(("Cancel download", "Deletes the part downloaded so far", lambda: self.confirm_cancel(j)))
        elif j.state in (J.FAILED, J.CANCELLED):
            e.append(("Retry" if j.state == J.FAILED else "Download again", j.message, lambda: d.resume_job(j)))
            e.append(("Remove from the list", "", lambda: d.remove(j)))
        else:                                      # done
            title = (j.group.get("title") or j.label) if j.group else j.label
            e.append(app.play_entry(j.rom, j.system, title))
            e.append(("Remove from the list", "The game stays on the box", lambda: d.remove(j)))
        app.modal = ActionMenu(app, j.label, e)

    def confirm_cancel(self, j):
        """Cancelling deletes the partial file, paused or not: ask first, and say how much goes."""
        app, d = self.app, self.app.downloader
        got = max(d.part_bytes(j), j.done_bytes)
        if got:
            body = "%s: the %s downloaded so far%s is deleted." % (
                j.label, human_size(got), " (of %s)" % human_size(j.total_bytes) if j.total_bytes else "")
            if j.state == rommlib.Job.PAUSED:
                body += " Back keeps it, to resume later."
        else:
            body = "%s: nothing is downloaded yet." % j.label
        app.modal = Confirm(app, "Cancel download?", body, "Cancel download", lambda: d.cancel_job(j), danger=True)

    # -- what a download row says

    def still_here(self, j, now=None):
        """A finished download's game is still on the box (it may have been deleted since):
        asked of the library at most every few seconds per job, as rows draw every frame."""
        now = time.time() if now is None else now
        cache = self.__dict__.setdefault("_here", {})
        t, here = cache.get(id(j), (0.0, True))
        if now - t > 5:
            try:
                here = self.app.library.is_installed(j.rom, j.system)
            except Exception:
                here = True
            cache[id(j)] = (now, here)
        return here
    def status(self, j):
        """(the short status at the right of a row, its colour: the row's state colour)."""
        J, d = rommlib.Job, self.app.downloader
        if j.state == J.DOWNLOADING:
            frac = (j.done_bytes / j.total_bytes) if j.total_bytes else 0
            gone = getattr(j, "stalled", lambda: 0)()
            if gone:                              # the old speed and time left would be a guess
                return "Stalled: nothing from RomM for %d s" % gone, theme.AMBER
            if not j.speed:
                return "%d%%" % (frac * 100), theme.BLUE
            st = "%s/s" % human_size(j.speed)
            if j.total_bytes:
                st += " · %s left" % time_left(max(0, j.total_bytes - j.done_bytes) / j.speed)
            return st + (" (slowed)" if d.speed_limit() else ""), theme.BLUE
        if j.state == J.VERIFYING:
            return "Checking the file…", theme.BLUE
        if j.state == J.EXTRACTING:
            return ("Waiting to unpack" if j.message.startswith("Waiting") else "Unpacking"), theme.AMBER
        if j.state == J.QUEUED:
            if d.hold:
                return "On hold", theme.AMBER
            if not j.force and not rommlib.in_download_hours(self.app.cfg):
                return "Waits for download hours", theme.AMBER
            if j.short_space:
                return "Waiting for %s of space" % human_size(j.short_space), theme.AMBER
            wait = j.retry_at - time.time()
            if wait > 0 and j.attempts:                  # a real retry, not just "later"
                return "Retry %d of %d in %d s" % (j.attempts, len(rommlib.RETRY_DELAYS), wait + 1), theme.AMBER
            return "Queued", theme.DIM
        if j.state == J.DONE:
            if not self.still_here(j):
                return "Deleted", theme.FAINT        # (#13: "Deleted since" read as cut short)
            if ", but " in (j.message or ""):            # in and playable; a step after it didn't go through
                return "Ready to play", theme.AMBER
            if self.chd_state(j) in ("queued", "compressing") and rommlib.needed_to_play(getattr(j, "chd_mode", None)):
                return "Getting it ready", theme.AMBER   # it can't start until it's set up (a package disc)
            return "Ready to play", theme.GREEN
        if j.state == J.FAILED:
            return "Failed", theme.RED
        if j.state == J.PAUSED:
            return "Paused", theme.AMBER
        return j.state.capitalize(), theme.FAINT

    def detail(self, j):
        """(the grey line under a row's name, its colour): where it goes, then what happens or
        happened to it; a failure's reason in red."""
        J = rommlib.Job
        if j.state == J.FAILED:
            return sentence(j.message or "Failed"), theme.RED
        parts = ["roms/" + j.system]
        if j.group:
            parts.append("%s, %d discs" % (j.group.get("title") or j.group.get("base"),
                                           len(j.group.get("rom_ids") or [])))
        if j.state == J.QUEUED and not j.force and not rommlib.in_download_hours(self.app.cfg) \
                and not self.app.downloader.hold:
            parts.append(rommlib.hours_label(self.app.cfg["download_hours"]))
        elif j.state == J.QUEUED and j.attempts and j.retry_at > time.time():
            parts.append(j.message.split(" Retrying")[0])
        if j.state == J.DONE and ", but " in (j.message or ""):
            return sentence(j.message.split(", but ", 1)[1]), theme.AMBER     # e.g. installed.json can't be updated now
        if j.state == J.DONE:
            if j.verified:
                parts.append("checksum OK")
            st = self.chd_state(j)
            words = rommlib.convert_words(getattr(j, "chd_mode", None))
            word = {"queued": words[0], "compressing": words[1], "done": words[2]}.get(st)
            if word:
                parts.append(word)
        return " · ".join(parts), theme.FAINT

    def chd_state(self, j):
        """A finished download's conversion: queued, compressing, done or None (cached per refresh)."""
        cache = self.__dict__.setdefault("_chd_st", {})
        if id(j) not in cache:
            task = getattr(j, "chd_task", None)
            cache[id(j)] = rommlib.chd_status(task, self.app.library, j.rom.get("id"),
                                              getattr(j, "chd_mode", None)) if task else None
        return cache[id(j)]

    def aside(self, c, j, col):
        """Under a row's status: its progress bar, the on-this-box tick, or nothing."""
        J = rommlib.Job
        if j.state == J.DONE:
            return lambda r: installed_mark(c, (r.right - 10, r.centery), 8)
        if j.state in (J.EXTRACTING, J.VERIFYING):
            return (1.0, col)
        if (j.active and j.state != J.QUEUED) or j.state == J.PAUSED:
            return ((j.done_bytes / j.total_bytes) if j.total_bytes else 0, col)
        return ""

    # -- the line on top
    def smoothed(self, raw, now):
        """The downloads' total speed, smoothed over about SPEED_TAU seconds (None: not moving)."""
        if raw <= 0:
            self.speed = None
        elif self.speed is None:
            self.speed = float(raw)
        else:
            self.speed += (raw - self.speed) * (1 - math.exp(-max(0.0, now - self.speed_at) / SPEED_TAU))
        self.speed_at = now
        return self.speed

    def outlook(self, now=None):
        """The whole picture: {"live", "convs" (counts), "secs" (until everything's done, None
        when a part can't be told), "dl_secs", "frac" (done of all of it)}. Downloads: what's
        left over the pace the queue keeps (RateMeter; the smoothed transfer speed for its first
        RATE_MIN seconds); the running conversion from the worker's progress, the
        ones queued behind it about as long each."""
        J = rommlib.Job
        now = time.time() if now is None else now
        live = [j for j in self.app.downloader.jobs if j.active or j.state == J.PAUSED]
        fin = getattr(self.app, "run_done", 0) if live else 0      # what this run already finished
        total = fin + sum(max(j.total_bytes or j.rom.get("fs_size_bytes") or 0, j.done_bytes) for j in live)
        done = fin + sum(j.done_bytes for j in live)
        speed = self.smoothed(sum(j.speed for j in live if j.state == J.DOWNLOADING and not j.stalled(now)), now)
        d = self.app.downloader
        meter = getattr(self.app, "dl_rate", None) or RateMeter()
        self.app.dl_rate = meter
        pace = meter.rate(d.jobs, not d.hold and any(j.active for j in live), now)
        speed = pace or speed                      # the pace once it's known; the transfer speed till then
        dl = 0.0 if total <= done else (total - done) / speed if speed else None
        if any(j.state == J.PAUSED or (j.state == J.QUEUED and getattr(j, "short_space", 0)) for j in live):
            dl = None            # one is on hold (paused, no room for it): when it all ends can't be told
        # unpacking and checking a file take time nothing here can tell: while more is downloading
        # it hides in that, but once all of it is here there's nothing left to count (#12)
        after = dl == 0 and any(j.state in (J.EXTRACTING, J.VERIFYING) for j in live)
        run, queue = self.running(), self.queued()
        convs = ([run] if run else []) + queue
        ctotal = sum(r["size"] for r in convs)
        cdone, cv = 0, 0.0 if not convs or self.held else None    # paused: it waits for you, not the clock
        if run and not self.held:
            pct = ((run.get("cur") or {}).get("pct") or 0) / 100.0
            cdone = run["size"] * pct
            left = conversion_left(run)
            if left is not None:                  # the ones behind it: about as long each (a cue's size says little)
                el = now - ((run.get("cur") or {}).get("since") or now)
                cv = left + len(queue) * (el + left)
        secs = dl + cv if dl is not None and cv is not None and not after else None
        allb = total + ctotal
        return {"live": len(live), "convs": len(convs), "secs": secs, "dl_secs": dl,
                "frac": (done + cdone) / allb if allb else 0.0, "running": bool(run),
                "conv_space": bool(queue) and queue[0].get("wait") == "space"}

    def headline(self, o):
        """(the line on top, its colour): never at odds with the rows under it."""
        d, J = self.app.downloader, rommlib.Job
        if d.hold and d.pending_count():
            if getattr(d, "hold_why", ""):
                return d.hold_why, theme.RED
            return "Downloads are paused: {X} Actions resumes them", theme.AMBER
        if not o["live"] and not o["convs"]:
            failed = sum(1 for j in d.jobs if j.state == J.FAILED)
            if failed:
                return "%s failed" % plural(failed, "download"), theme.RED
            return ("All done" if d.jobs else "Nothing running"), theme.DIM
        if not o["live"] and self.held:
            return "Compressing is paused", theme.AMBER
        if o["live"]:
            states = {j.state for j in d.jobs}
            if J.DOWNLOADING in states:
                return "Downloading", theme.TEXT
            if J.EXTRACTING in states:
                return "Unpacking", theme.TEXT
            if J.VERIFYING in states:
                return "Checking downloads", theme.TEXT
            if any(getattr(j, "short_space", 0) for j in d.jobs if j.state == J.QUEUED):
                return "Downloads wait for space", theme.AMBER
            return "Waiting to download", theme.DIM
        if o["running"]:
            return "Compressing", theme.TEXT
        if o["conv_space"]:
            return "Compressing waits for space", theme.AMBER
        return "Waiting to compress", theme.DIM

    def steady(self, said, now):
        """The headline to draw: what's shown stays while it keeps being said, and gives way to the
        newest line once it hasn't been for LABEL_HOLD seconds, so fast downloads (Downloading,
        Checking downloads, Downloading…) and the moment between two conversions ("All done")
        don't flicker. A batch's count going up is the same line; one
        that needs you (amber, red: paused, waiting for space, failed) comes and goes at once."""
        line, col = said
        kind = line
        if self.work_said is not None and line == self.work_said:
            kind = "work"                        # (state_line said it: the batch, as read once)
        urgent = (theme.AMBER, theme.RED)
        over = self.shown is not None and self.shown[0] == "work" and kind != "work"    # the batch ended: at once
        if self.shown is None or kind == self.shown[0] or col in urgent or self.shown[2] in urgent or over \
                or now - self.shown[3] >= LABEL_HOLD or now < self.shown[3]:
            self.shown = (kind, line, col, now)
            return line, col
        return self.shown[1], self.shown[2]

    def state_line(self, o):
        """(what's going on, its colour): the line on top without the time, which stands apart. A batch
        in the background (deleting many games, #10) comes first, unless something needs you: it's
        what's going on right now."""
        w = getattr(self.app, "work", None)      # (read once: the batch's thread changes it any time)
        said = self.headline(o)
        self.work_said = None
        if w and said[1] not in (theme.AMBER, theme.RED):
            self.work_said = work_label(w)
            return self.work_said, theme.TEXT
        return said

    def time_line(self, o, now=None):
        """"Done in 6 min": when what's running ends, one line in one colour (#12), or None when it
        can't be told. Paused compressing isn't counted (it waits for you). While downloads run
        and a conversion can't be told yet (just started, or waiting its turn), the last time for
        all of it keeps counting down, never below the downloads' own; with none yet, the
        downloads' time: the line stays put instead of blinking out or changing its words."""
        now = time.time() if now is None else now
        if self.app.downloader.hold and self.app.downloader.pending_count():
            return None
        if not o["live"] and (self.held or not o["convs"]):
            return None
        secs = o["secs"]
        if secs is not None:
            self.time_est = (secs, now)
        elif o["live"] and o["dl_secs"]:
            last = self.time_est
            secs = max(o["dl_secs"], last[0] - (now - last[1])) if last else o["dl_secs"]
        else:
            self.time_est = None
        if not secs:                                 # None: can't be told; 0: a step after it runs
            return None
        return "Done in %s" % time_left(secs, about=False)

    def steady_time(self, said, now):
        """The time to draw: a moment without one (a file being checked) keeps the last for
        LABEL_HOLD seconds instead of blinking out."""
        if said is not None:
            self.time_shown = (said, now)
            return said
        if self.time_shown and now - self.time_shown[1] < LABEL_HOLD:
            return self.time_shown[0]
        self.time_shown = None
        return None

    def draw_head(self, c, o, now=None):
        """The card on top (#6): what's going on, left; when it's all done, on its own on the right;
        the counts under it; one bar for all of it."""
        now = time.time() if now is None else now
        f = self.f
        rr = pygame.Rect(ACT_HEAD)
        card(c, rr)
        jobs = self.app.downloader.jobs
        queued = self.app.downloader.active_count()          # as the header counts them
        P = rommlib.Job.PAUSED
        paused = sum(1 for j in jobs if j.state == P)
        parts = (["%d in the queue" % queued] if queued else []) + (["%d paused" % paused] if paused else []) + \
            (["%d compressing" % o["convs"]] if o["convs"] and o.get("running", True) else
             ["%d to compress" % o["convs"]] if o["convs"] else [])       # none running: they only wait
        n = self.pick_count()
        if n:
            parts.append("%d picked" % n)
        run = getattr(self, "_run", "")
        if run:                                    # a big run: how far along, and what's left by platform
            parts = [run] + [p for p in parts if not p.endswith("in the queue")]
        right = rr.right - 20
        when = self.steady_time(self.time_line(o, now), now)
        if when:
            # it starts where the widest time so far did, so a time ticking down (19 s, 18 s:
            # digits differ in width) only changes the end of it (#12)
            w = max(min(int(measure(f.h2, when)) + 1, rr.width // 2), self.time_w)
            self.time_w = w
            text(c, f.h2, when, (right - w, rr.top + 10), theme.TEXT, maxw=w)
            right -= w + 24
        else:
            self.time_w = 0
        line, col = self.steady(self.state_line(o), now)
        text(c, f.h2, line, (rr.left + 20, rr.top + 10), col, maxw=right - rr.left - 20)
        counts = " · ".join(parts)
        if counts:
            marquee(c, f.small, counts, (rr.left + 20, rr.top + 46), theme.DIM, maxw=rr.width - 40, bg=theme.PANEL,
                    slot="activity-head")
        w = getattr(self.app, "work", None)
        frac = o["frac"] if o["live"] or o["convs"] else w["n"] / w["total"] if w and w["total"] else None
        if frac is not None:                     # the queue's bar (only a batch: its own), how far, in %
            pct = "%.1f%%" % (frac * 100) if frac < 0.1 else "%d%%" % min(100, frac * 100)   # a big run's start moves too
            pw = int(measure(f.small, "100%")) + 1
            text(c, f.small, pct, (rr.right - 20 - int(measure(f.small, pct)) - 1, rr.bottom - 26), theme.DIM)
            progress_bar(c, (rr.left + 20, rr.bottom - 18, rr.width - 52 - pw, 8), frac,
                         theme.AMBER if col == theme.AMBER and (o["live"] or o["convs"]) else theme.ACCENT)

    def draw(self, c):
        now = time.time()
        if now - self.conv_at > 1.0:
            self.refresh()
        jobs = self._drawing = self.view()
        try:
            self.follow(jobs)
            if not jobs and self.focus == "list":
                self.focus = "panel"
            self.draw_head(c, self.outlook(now), now)
            lst, pan = split(None, ACT_BODY)
            self.draw_list(c, lst, jobs)
            self.draw_panel(c, pan)
        finally:
            self._drawing = None

    def draw_list(self, c, lst, jobs):
        f, fc = self.f, self._fc
        # a download waiting for space says the same number as its row; otherwise the
        # whole queue's shortfall, said as the queue's
        Q = rommlib.Job.QUEUED
        waits = max((j.short_space for j in jobs if j.state == Q and j.short_space), default=0)
        note, ncol = ("waiting for %s of space" % human_size(waits), theme.AMBER) if waits else \
            ("%s free" % human_size(fc["free"]), theme.FAINT) if fc["fits"] else \
            ("the queue needs %s more space" % human_size(fc["short"]), theme.AMBER)
        on_list = self.focus == "list"
        list_head(c, f, lst, "Downloads", note, ncol, color=theme.TEXT if on_list else theme.DIM)
        y0 = lst.top + 44
        if not jobs:
            w = getattr(self.app, "work", None)     # (read once: the batch's thread ends it any time)
            if w:                                   # a batch has no rows here: the card follows it (#13)
                head = "Nothing downloading"
                body = "%s %ss shows in the card above. Downloads show here." % (w["verb"], w["what"])
            else:
                head, body = "No downloads yet", "Pick a game and press {A} to download it here."
            text(c, f.h3, head, (lst.centerx, y0 + 120), theme.DIM, "center")
            text(c, f.body, body, (lst.centerx, y0 + 160), theme.FAINT, "center", maxw=lst.width - 40)
            return
        self.sel = min(self.sel, len(jobs) - 1)
        over = len(jobs) > self.ROWS
        for i, r, on in self.row_rects(y0, x=lst.left, w=lst.width - (12 if over else 0), gap=6):
            j = jobs[i]
            st, col = self.status(j)
            sub, scol = self.detail(j)

            def thumb(ctr, j=j, r=r):
                return self.draw_pick(c, j, ctr) or bool(cover(c, self.app, j.rom, (ctr[0] - 17, r.top + 6, 34,
                                                                                   r.height - 12), radius=4,
                                                               label=False))
            list_row(c, f, r, on and on_list, j.label, sub, st, col, sub_color=scol, mark=thumb, mark_w=62,
                     box=theme.PANEL, aside=self.aside(c, j, col), slot="act-dl",
                     label_color=None if on_list else (theme.TEXT if on else theme.DIM))
            state_stripe(c, r, col)
        if over:
            self.draw_scrollbar(c, lst.right - 4, y0)

    def draw_panel(self, c, pan):
        f, app = self.f, self.app
        on_panel = self.focus == "panel"
        items = self.panel_items()
        self.psel = max(0, min(self.psel, len(items) - 1))
        p = Panel(c, f, pan, app=app, key=self.psel, slot="act-panel")
        last = len(items) - 1
        p.card("Compress games…", "Pick games already here to make smaller", on=on_panel and self.psel == last,
               bottom=True, chevron=True, tap=lambda: self.tap_panel(last))
        run = self.running()
        cu = (run or {}).get("cur") or {}
        why = "hold" if self.held else cu.get("paused") or ""
        note = PAUSED_WHY.get(why) or ("pauses while you play" if run or self.queued() else "")
        p.head("Compressing", note, theme.AMBER if why else theme.FAINT, color=theme.TEXT if on_panel else theme.DIM)
        for i, it in enumerate(items[:-1]):
            on = on_panel and i == self.psel
            tap = lambda i=i: self.tap_panel(i)
            k = it["kind"]
            if k == "running":
                st, frac = conversion_status(it)
                if why:
                    st = "Paused at %d%%" % (frac * 100)
                _, room = p.card(self.conv_title(it), "to %s · %s" % (self.mode(it), st), on=on, h=62, more=52,
                                 tap=tap, sub_color=theme.AMBER if why else theme.ACCENT_HI)
                progress_bar(c, (room.left, room.top - 2, room.width, 6), frac, theme.AMBER if why else theme.ACCENT)
                x = room.left
                for tok, lab, fn in (("A", "Resume" if self.held else "Pause", self.toggle_hold),
                                     ("X", "Stop", lambda it=it: self.ask_stop(it))):
                    cr = button_chip(c, f, (x, room.top + 10), tok, lab, hot=on)
                    app.hit(cr, fn)
                    x = cr.right + 8
            elif k == "held":
                p.card("Compressing is paused", "%s waiting · {A} resumes" % plural(len(self.queued()), "game"),
                       on=on, tap=tap, sub_color=theme.AMBER)
            else:
                wait = "waits for %s" % it["tool"] if it.get("tool") else \
                    "waits for space" if it.get("wait") == "space" else "waits its turn"
                p.card(self.conv_title(it), "to %s · %s · %s" % (self.mode(it), human_size(it["size"]), wait),
                       on=on, tap=tap)
        more = len(self.queued()) - sum(1 for it in items if it["kind"] == "queued")
        if more > 0:
            p.label("+%d more waiting · {L2 R2} Conversions" % more)
        if len(items) == 1:
            p.space(4)
            p.prose("Nothing compressing. Disc games are compressed here after they download, while nothing "
                    "is playing.", color=theme.FAINT, lines=3)
        self.draw_lately(c, p)

    def draw_lately(self, c, p):
        done = [r for r in self.convs if r["kind"] in RESULT_COLOURS]
        if not done or p.room() < 24 + 48:
            return
        p.space(4)
        p.label("Lately")
        for r in done:
            if p.room() < 48:
                break
            word, col = RESULT_LABELS[r["kind"]], getattr(theme, RESULT_COLOURS[r["kind"]])
            sub = result_line(r, self.mode(r))
            box = pygame.Rect(p.x - 12, p.y, p.w + 24, 46)
            list_row(c, self.f, box, False, self.conv_title(r), sub, word, col, mark_w=40,
                     mark=lambda ctr, col=col: circle(c, col, (ctr[0] + 2, ctr[1] - 10), 5) or True,
                     slot="act-lately")
            p.space(48)

    @staticmethod
    def conv_title(r):
        return ConversionsScreen.title(r)

    @staticmethod
    def mode(r):
        return rommlib.CONVERT_LABELS.get((r.get("task") or {}).get("mode") or r.get("mode"), "")
