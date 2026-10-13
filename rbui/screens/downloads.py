"""The download queue."""
import time

import rommlib
from rommlib import human_size
from rbui import theme
from rbui.theme import H, W
from rbui.draw import measure, progress_bar, rich_text, rrect, text
from rbui.screens.base import ListScreen
from rbui.screens.box_data import plural
from rbui.screens.installed import ConversionsScreen
from rbui.modals import ActionMenu, Confirm


class DownloadsScreen(ListScreen):
    """The queue in the order it runs, then finished downloads (newest
    first). A: per-download menu (reorder, pause, cancel, retry); X: pause
    or resume everything, and pick several to pause, resume, cancel, retry
    or remove at once; Y clears the finished ones."""
    ROWS, ROW_H = 7, 76
    PICKS, PICK_WHAT = True, "downloads"

    def __init__(self, app):
        super().__init__(app)
        self.sel = self.scroll = 0

    def crumbs(self):
        return ["Downloads"]

    def listed(self):
        return self.view()

    def view(self):
        d = self.app.downloader
        jobs = list(d.jobs)
        pend = [j for j in jobs if j.active or j.state == rommlib.Job.PAUSED]
        return pend + [j for j in reversed(jobs) if j not in pend]

    def hints(self):
        d = self.app.downloader
        h = [("A", "Options"), ("X", "Menu")] if d.jobs else []
        if any(not (j.active or j.state == rommlib.Job.PAUSED) for j in d.jobs):
            h.append(("Y", "Clear finished"))
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
        if any(not (j.active or j.state == rommlib.Job.PAUSED) for j in d.jobs):
            e.append(("Clear finished", "Done, failed and cancelled ones leave the list", self.clear_finished))
        return e

    def clear_finished(self):
        n = self.app.downloader.clear_finished()
        if n:
            self.app.toast("Cleared %d finished" % n)

    def pick_entries(self, jobs):
        """Pause, resume, cancel, retry or remove the picked downloads (each
        only those it applies to); cancelling asks first."""
        app, d, J = self.app, self.app.downloader, rommlib.Job
        live = [j for j in jobs if j.active and j.state != J.PAUSED]
        paused = [j for j in jobs if j.state == J.PAUSED]
        again = [j for j in jobs if j.state in (J.FAILED, J.CANCELLED)]
        over = [j for j in jobs if not (j.active or j.state == J.PAUSED)]

        def each(fn, todo, msg):
            def run():
                for j in todo:
                    fn(j)
                self.picks.clear()
                self.picking = False
                app.toast(msg % plural(len(todo), "download"))
            return run
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
        """The first tap also remembers the download, for follow()."""
        if self.sel != i:
            jobs = self.view()
            self.sel_job = jobs[i] if i < len(jobs) else None
        super().tap_row(i)

    def action(self, a):
        app, d = self.app, self.app.downloader
        jobs = self.view()
        self.follow(jobs)
        if self.nav(a):
            self.sel_job = jobs[min(self.sel, len(jobs) - 1)]
        elif jobs and self.pick_action(a):
            jobs = self.view()
            if jobs:
                self.sel_job = jobs[min(self.sel, len(jobs) - 1)]
        elif a == "ok" and jobs:
            self.menu(jobs[min(self.sel, len(jobs) - 1)])
        elif a == "y":
            self.clear_finished()
        elif a in ("l2", "r2"):
            app.push(ConversionsScreen(app))
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

    def status(self, j):
        """(text, colour) for a row."""
        J = rommlib.Job
        frac = (j.done_bytes / j.total_bytes) if j.total_bytes else 0
        if j.state == J.DOWNLOADING:
            lim = " (slowed)" if self.app.downloader.rate_limit else ""
            return "%d%%   %s of %s   %s/s%s" % (frac * 100, human_size(j.done_bytes), human_size(j.total_bytes),
                                                 human_size(j.speed), lim), theme.BLUE
        if j.state == J.VERIFYING:
            return "Checking checksum…", theme.BLUE
        if j.state == J.EXTRACTING:
            return ("Waiting to unpack" if j.message.startswith("Waiting") else "Extracting…"), theme.AMBER
        if j.state == J.QUEUED:
            if self.app.downloader.hold:
                return "Paused (all)", theme.AMBER
            if not j.force and not rommlib.in_download_hours(self.app.cfg):
                return "Waiting for download hours (%s)" % rommlib.hours_label(
                    self.app.cfg["download_hours"]), theme.AMBER
            if j.short_space:
                return "Waiting for space: free %s more" % human_size(j.short_space), theme.AMBER
            wait = j.retry_at - time.time()
            if wait > 0 and j.attempts:                  # a real retry, not just "later"
                return "%s Retry %d of %d in %d s" % (j.message.split(" Retrying")[0], j.attempts,
                                                      len(rommlib.RETRY_DELAYS), wait + 1), theme.AMBER
            return "Queued", theme.DIM
        if j.state == J.DONE:
            msg = j.message or "Done"
            cache = self.__dict__.setdefault("_chd_st", {})
            if id(j) not in cache:
                cache[id(j)] = rommlib.chd_status(getattr(j, "chd_task", None), self.app.library,
                                                  j.rom.get("id"), getattr(j, "chd_mode", None)) \
                    if getattr(j, "chd_task", None) else None
            st = cache[id(j)]
            fmt = rommlib.CONVERT_LABELS.get(getattr(j, "chd_mode", None), "CHD")
            msg += {"queued": " · %s queued" % fmt, "compressing": " · compressing to %s…" % fmt,
                    "done": " · compressed to %s" % fmt}.get(st, "")
            return msg, theme.GREEN
        if j.state == J.FAILED:
            return "Failed — " + j.message, theme.RED
        if j.state == J.PAUSED:
            return "Paused", theme.AMBER
        return j.state, theme.FAINT

    def draw(self, c):
        d = self.app.downloader
        jobs = self.view()
        self.follow(jobs)
        y = 84
        if d.hold and d.pending_count():
            rich_text(c, self.f, self.f.small, "Downloads are paused. Resume them from the {X} menu.", (48, y),
                      theme.AMBER)
            y += 28
        now = time.time()
        if now - getattr(self, "_chd_at", 0) > 1.0:   # disk reads once a second, not every frame
            self._chd_at = now
            self._chd_n = rommlib.conversions_waiting()
            self._chd_st = {}
            self._fc = rommlib.queue_forecast(d.jobs, rommlib.free_bytes(),
                                              rommlib.keep_free_bytes(self.app.cfg), self.app.cfg["chd"],
                                              self.app.cfg)
        fc = self._fc
        if fc["jobs"]:
            line = "Queue needs about %s · %s free · keeping %s free" % (
                human_size(fc["need"]), human_size(fc["free"]), human_size(fc["keep"]))
            if not fc["fits"]:
                line += " · short by %s: downloads wait for space" % human_size(fc["short"])
            text(c, self.f.small, line, (48, y), theme.GREEN if fc["fits"] else theme.AMBER, maxw=W - 96)
            y += 28
        chd = self._chd_n
        if chd:
            text(c, self.f.small, "%d disc game%s waiting for background compression "
                 "(runs when no game is playing) · {L2 R2} Conversions" % (chd, "s" if chd > 1 else ""),
                 (48, y), theme.AMBER)
            y += 28
        if not jobs:
            text(c, self.f.h2, "No downloads yet", (W // 2, 300), theme.DIM, "center")
            text(c, self.f.body, "Pick a game and press {A} to download it to this box.",
                 (W // 2, 340), theme.FAINT, "center")
            return
        rows = max(1, (H - 60 - y) // self.ROW_H)       # fewer below the banners; L/R still page by ROWS
        self.sel = min(self.sel, len(jobs) - 1)
        J = rommlib.Job
        npend = sum(1 for j in jobs if j.active or j.state == J.PAUSED)
        for i, r, on in self.row_rects(y, gap=6, rows=rows):
            j = jobs[i]
            rrect(c, theme.PANEL_HI if on else theme.PANEL, r, 12)
            if on:
                rrect(c, theme.ACCENT, r, 12, 2)
            x = r.left + 20
            if self.draw_pick(c, j, (x + 9, r.top + 24)):
                x += 30
            if i < npend:                          # position in the queue
                text(c, self.f.small, str(i + 1), (x + 10, r.top + 24), theme.FAINT, "center")
                x += 30
            st, col = self.status(j)
            sw = min(520, int(measure(self.f.small, st)) + 4)
            text(c, self.f.h3, j.label, (x, r.top + 12), theme.TEXT, maxw=r.right - 40 - sw - x)
            text(c, self.f.small, st, (r.right - 20, r.top + 14), col, "topright", maxw=520)
            sub = "roms/" + j.system
            if j.group:
                sub += "  ·  %s, %d discs" % (j.group.get("title") or j.group.get("base"),
                                             len(j.group.get("rom_ids") or []))
            if j.state == J.DONE and j.verified:
                sub += "  ·  checksum OK"
            bar = j.active and j.state != J.QUEUED
            text(c, self.f.small, sub, (x, r.top + 42), theme.FAINT, maxw=(r.left + 440 - x) if bar else r.width - 60)
            if bar:
                frac = (j.done_bytes / j.total_bytes) if j.total_bytes else 0
                busy = j.state in (J.EXTRACTING, J.VERIFYING)
                progress_bar(c, (r.left + 460, r.top + 48, r.width - 480, 8),
                             1.0 if busy else frac, theme.AMBER if j.state == J.EXTRACTING else theme.BLUE)
        if len(jobs) > rows:
            text(c, self.f.small, "%d–%d of %d" % (self.scroll + 1, min(len(jobs), self.scroll + rows), len(jobs)),
                 (W - 48, H - 64), theme.FAINT, "bottomright")
