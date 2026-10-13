"""Activity: the downloads, and the conversions on this box, on one screen."""
import math
import re
import time

import pygame
import rommlib
from rommlib import human_size
from rbui import pads, theme
from rbui.draw import card, circle, installed_mark, progress_bar, state_stripe, text
from rbui.screens.base import ListScreen
from rbui.screens.box_data import conversion_left, conversion_rows, conversion_status, plural, time_left
from rbui.screens.compress import CompressScreen
from rbui.screens.installed import ConversionsScreen
from rbui.modals import ActionMenu, Confirm
from rbui.widgets.chips import button_chip
from rbui.widgets.layout import CONTENT, Panel, list_head, list_row, split
from rbui.widgets.tiles import cover

ACT_HEAD = pygame.Rect(CONTENT.left, CONTENT.top, CONTENT.width, 76)       # the one line for everything
ACT_BODY = pygame.Rect(CONTENT.left, ACT_HEAD.bottom + 16, CONTENT.width, CONTENT.bottom - ACT_HEAD.bottom - 16)
SPEED_TAU = 5.0             # seconds: the headline's speed is smoothed over about this long
RESULT_WORDS = {"ok": ("done", "GREEN"), "failed": ("failed", "RED"), "stopped": ("stopped", "AMBER"),
                "skipped": ("skipped", "AMBER"), "waiting": ("waiting", "AMBER")}
PAUSED_WHY = {"hold": "paused by you", "game": "paused while you play", "es": "paused while ES is busy"}


def short_reason(msg):
    """A failure's reason for one narrow line: without its "file.ext: " prefix and a trailing
    "(…)" aside, which the Conversions screen still shows whole."""
    head, sep, rest = msg.partition(": ")
    if sep and re.search(r"\.\w{1,5}$", head):          # "Lorem (USA).cue: …"
        msg = rest
    if msg.endswith(")") and " (" in msg:
        msg = msg[:msg.rfind(" (")]
    return msg


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
        self.refresh()
        if not self.view():
            self.focus = "panel"

    def crumbs(self):
        return ["Activity"]

    def listed(self):
        return self.view()

    def view(self):
        d = self.app.downloader
        jobs = list(d.jobs)
        pend = [j for j in jobs if j.active or j.state == rommlib.Job.PAUSED]
        return pend + [j for j in reversed(jobs) if j not in pend]

    def refresh(self):
        """What the conversion worker publishes and the space forecast: disk reads, once a second."""
        d = self.app.downloader
        self.convs, self.held, self.conv_at = conversion_rows(), rommlib.conversions_held(), time.time()
        self._chd_st = {}
        self._fc = rommlib.queue_forecast(d.jobs, rommlib.free_bytes(), rommlib.keep_free_bytes(self.app.cfg),
                                          self.app.cfg["chd"], self.app.cfg)

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
            if self.view():
                h.append(("LEFT RIGHT", "Downloads"))
            return h + [("L2 R2", "Conversions"), ("B", "Back")]
        h = [("A", "Manage"), ("X", pads.ACTIONS)] if d.jobs else []
        if any(not (j.active or j.state == rommlib.Job.PAUSED) for j in d.jobs):
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
    def status(self, j):
        """(the short status at the right of a row, its colour: the row's state colour)."""
        J, d = rommlib.Job, self.app.downloader
        if j.state == J.DOWNLOADING:
            frac = (j.done_bytes / j.total_bytes) if j.total_bytes else 0
            if not j.speed:
                return "%d%%" % (frac * 100), theme.BLUE
            st = "%s/s" % human_size(j.speed)
            if j.total_bytes:
                st += " · %s left" % time_left(max(0, j.total_bytes - j.done_bytes) / j.speed)
            return st + (" (slowed)" if d.rate_limit else ""), theme.BLUE
        if j.state == J.VERIFYING:
            return "Checking checksum…", theme.BLUE
        if j.state == J.EXTRACTING:
            return ("Waiting to unpack" if j.message.startswith("Waiting") else "Unpacking"), theme.AMBER
        if j.state == J.QUEUED:
            if d.hold:
                return "Paused (all)", theme.AMBER
            if not j.force and not rommlib.in_download_hours(self.app.cfg):
                return "Waits for download hours", theme.AMBER
            if j.short_space:
                return "Waiting for %s of space" % human_size(j.short_space), theme.AMBER
            wait = j.retry_at - time.time()
            if wait > 0 and j.attempts:                  # a real retry, not just "later"
                return "Retry %d of %d in %d s" % (j.attempts, len(rommlib.RETRY_DELAYS), wait + 1), theme.AMBER
            return "Queued", theme.DIM
        if j.state == J.DONE:
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
            return j.message or "Failed", theme.RED
        parts = ["roms/" + j.system]
        if j.group:
            parts.append("%s, %d discs" % (j.group.get("title") or j.group.get("base"),
                                           len(j.group.get("rom_ids") or [])))
        if j.state == J.QUEUED and not j.force and not rommlib.in_download_hours(self.app.cfg) \
                and not self.app.downloader.hold:
            parts.append(rommlib.hours_label(self.app.cfg["download_hours"]))
        elif j.state == J.QUEUED and j.attempts and j.retry_at > time.time():
            parts.append(j.message.split(" Retrying")[0])
        if j.state == J.DONE:
            if j.verified:
                parts.append("checksum OK")
            st = self.chd_state(j)
            fmt = rommlib.CONVERT_LABELS.get(getattr(j, "chd_mode", None), "CHD")
            word = {"queued": "%s queued", "compressing": "compressing to %s…", "done": "compressed to %s"}.get(st)
            if word:
                parts.append(word % fmt)
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
        left over the smoothed speed; the running conversion from the worker's progress, the
        ones queued behind it about as long each."""
        J = rommlib.Job
        now = time.time() if now is None else now
        live = [j for j in self.app.downloader.jobs if j.active or j.state == J.PAUSED]
        total = sum(max(j.total_bytes or j.rom.get("fs_size_bytes") or 0, j.done_bytes) for j in live)
        done = sum(j.done_bytes for j in live)
        speed = self.smoothed(sum(j.speed for j in live if j.state == J.DOWNLOADING), now)
        dl = 0.0 if total <= done else (total - done) / speed if speed else None
        run, queue = self.running(), self.queued()
        convs = ([run] if run else []) + queue
        ctotal = sum(r["size"] for r in convs)
        cdone, cv = 0, 0.0 if not convs else None
        if run and not self.held:
            pct = ((run.get("cur") or {}).get("pct") or 0) / 100.0
            cdone = run["size"] * pct
            left = conversion_left(run)
            if left is not None:                  # the ones behind it: about as long each (a cue's size says little)
                el = now - ((run.get("cur") or {}).get("since") or now)
                cv = left + len(queue) * (el + left)
        secs = dl + cv if dl is not None and cv is not None else None
        allb = total + ctotal
        return {"live": len(live), "convs": len(convs), "secs": secs, "dl_secs": dl,
                "frac": (done + cdone) / allb if allb else 0.0}

    def headline(self, o):
        """(the line on top, its colour)."""
        d = self.app.downloader
        if d.hold and d.pending_count():
            return "Downloads are paused: {X} Actions resumes them", theme.AMBER
        if not o["live"] and not o["convs"]:
            return ("All done" if d.jobs else "Nothing running"), theme.DIM
        if not o["live"] and self.held:
            return "Compressing is paused", theme.AMBER
        if o["secs"] is not None:
            return "Everything's done in %s" % time_left(o["secs"]), theme.TEXT
        if o["live"] and o["dl_secs"] is not None:
            return "Downloads done in %s" % time_left(o["dl_secs"]), theme.TEXT
        return ("Waiting to download" if o["live"] else "Compressing"), theme.DIM

    def draw_head(self, c, o):
        f = self.f
        rr = pygame.Rect(ACT_HEAD)
        card(c, rr)
        parts = (["%d downloading" % o["live"]] if o["live"] else []) + \
            (["%d compressing" % o["convs"]] if o["convs"] else [])
        n = self.pick_count()
        if n:
            parts.append("%d picked" % n)
        cr = text(c, f.small, " · ".join(parts), (rr.right - 20, rr.top + 20), theme.DIM, "topright",
                  maxw=rr.width // 3) if parts else pygame.Rect(rr.right - 20, 0, 0, 0)
        line, col = self.headline(o)
        text(c, f.h2, line, (rr.left + 20, rr.top + 12), col, maxw=cr.left - 40 - rr.left)
        if o["live"] or o["convs"]:
            progress_bar(c, (rr.left + 20, rr.bottom - 20, rr.width - 40, 8), o["frac"],
                         theme.AMBER if col == theme.AMBER else theme.ACCENT)

    def draw(self, c):
        now = time.time()
        if now - self.conv_at > 1.0:
            self.refresh()
        jobs = self.view()
        self.follow(jobs)
        if not jobs and self.focus == "list":
            self.focus = "panel"
        self.draw_head(c, self.outlook(now))
        lst, pan = split(None, ACT_BODY)
        self.draw_list(c, lst, jobs)
        self.draw_panel(c, pan)

    def draw_list(self, c, lst, jobs):
        f, fc = self.f, self._fc
        note, ncol = ("%s free" % human_size(fc["free"]), theme.FAINT) if fc["fits"] else \
            ("short by %s: downloads wait for space" % human_size(fc["short"]), theme.AMBER)
        on_list = self.focus == "list"
        list_head(c, f, lst, "Downloads", note, ncol, color=theme.TEXT if on_list else theme.DIM)
        y0 = lst.top + 44
        if not jobs:
            text(c, f.h3, "No downloads yet", (lst.centerx, y0 + 120), theme.DIM, "center")
            text(c, f.body, "Pick a game and press {A} to download it here.", (lst.centerx, y0 + 160),
                 theme.FAINT, "center", maxw=lst.width - 40)
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
                wait = "waits for %s" % it["tool"] if it.get("tool") else "waits its turn"
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
        done = [r for r in self.convs if r["kind"] in RESULT_WORDS]
        if not done or p.room() < 24 + 48:
            return
        p.space(4)
        p.label("Lately")
        for r in done:
            if p.room() < 48:
                break
            word, col = RESULT_WORDS[r["kind"]]
            col = getattr(theme, col)
            if r["kind"] == "ok":
                sub = "to %s" % self.mode(r) + (" · %s -> %s" % tuple(human_size(n) for n in r["sizes"])
                                                 if r.get("sizes") else "")
            else:
                sub = short_reason(r.get("message") or "")
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
