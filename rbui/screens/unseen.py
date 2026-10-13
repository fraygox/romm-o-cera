"""Files EmulationStation can't see."""
import os

import pygame
import rommlib
from rommlib import human_size
from rbui import pads, theme
from rbui.theme import H, W
from rbui.draw import circle, focus_box, marquee, measure, rrect, text
from rbui.screens.base import ListScreen
from rbui.screens.box_data import plural
from rbui.modals import ActionMenu, Confirm


class UnseenScreen(ListScreen):
    """Check games › Games ES can't see: files in roms/<system>/ that
    EmulationStation ignores because the system doesn't list their extension
    (rommlib.find_unseen). Archives can be unpacked (as after a download),
    games in the wrong system folder moved, and anything deleted after a
    confirmation that names what goes. The X Actions menu picks files for doing
    several at once: unpack every picked archive, or delete them all."""
    ROWS, ROW_H = 9, 56
    MOVE_CHOICES = 5                   # the menu has no scrolling: the likeliest systems first
    PICKS, PICK_WHAT, JUMP = True, "files", "System"

    def __init__(self, app):
        super().__init__(app)
        self.items = None
        self.sel = self.scroll = 0
        self.busy = False
        self.progress = ""             # "Unpacking 3 of 13…" while a batch runs
        self.load()

    def load(self):
        es = self.app.downloader.es
        self.load_error = None
        self.app.bg(lambda: rommlib.find_unseen(es), self._loaded, self.load_failed("items"))

    def _loaded(self, items):
        self.items = items
        keep = {i["path"]: i for i in items if i["path"] in self.picks}
        self.picks.clear()
        self.picks.update(keep)
        self.clamp()

    def crumbs(self):
        return self.trail("Games ES can't see")

    def hints(self):
        if not self.items:
            return [("B", "Back")]
        return self.list_hints(("A", "Options"), ("X", pads.ACTIONS))

    def jump_label(self, it):
        return it["system"]

    def action(self, a):
        if not self.items or self.busy:
            if self.busy and a in ("ok", "x", "y"):
                self.app.toast("Still working on the last one…", theme.AMBER)
                return
            return super().action(a)
        if self.nav(a) or self.pick_action(a):
            return
        if a == "ok":
            self.menu(self.items[self.sel])
        else:
            super().action(a)

    def pick_entries(self, todo):
        arcs = [i for i in todo if i["kind"] == "archive"]
        size = sum(i["size"] for i in todo)
        return [("Unpack %s" % plural(len(arcs), "archive"),
                 "%s, as after a download" % human_size(sum(i["size"] for i in arcs)) if arcs
                 else "None of the picked files is an archive", (lambda: self.ask_unpack_many(arcs)) if arcs else None),
                ("Delete %s" % plural(len(todo), "file"), "%s, after you confirm" % human_size(size),
                 lambda: self.ask_delete_many(todo))]

    def ask_unpack_many(self, arcs):
        app = self.app
        systems = sorted({i["system"] for i in arcs})
        app.modal = Confirm(app, "Unpack %s?" % plural(len(arcs), "archive"),
                            "Each is unpacked into its own system folder (%s) as after a download, compressed if "
                            "that's on, and deleted once it's out. One that can't be unpacked is left as it is."
                            % ", ".join(systems), "Unpack %d" % len(arcs), lambda: self.run_many("unpack", arcs))

    def ask_delete_many(self, todo):
        app = self.app
        size, extra = 0, 0
        for it in todo:
            size += it["size"]
            extra += len([p for p in rommlib.game_files(it["path"])
                          if os.path.normpath(p) != os.path.normpath(it["path"])])
        also = ", with %s they name" % plural(extra, "file") if extra else ""
        app.modal = Confirm(app, "Delete %s?" % plural(len(todo), "file"),
                            "Deletes %s (%s)%s. Saves and states stay. This can't be undone."
                            % (plural(len(todo), "file"), human_size(size), also),
                            "Delete %d" % len(todo), lambda: self.run_many("delete", todo), danger=True)

    def run_many(self, what, todo):
        """Unpack or delete the picked files one after another; a failure is
        noted and the rest go on."""
        app = self.app
        verb = {"unpack": "Unpacking", "delete": "Deleting"}[what]
        self.busy = True

        def run():
            done, failed, freed, tasks = 0, [], 0, False
            for n, it in enumerate(todo, 1):
                self.progress = "%s %d of %d…" % (verb, n, len(todo))
                if not os.path.lexists(it["path"]):
                    done += 1                       # went with a sheet picked before it
                    continue
                try:
                    if what == "unpack":
                        tasks = bool(rommlib.unpack_unseen(app.downloader, it)[1]) or tasks
                    else:
                        freed += rommlib.delete_unseen(it, app.library)[1]
                    done += 1
                except Exception as e:              # a damaged archive is noted, the rest go on
                    why = str(e) if isinstance(e, rommlib.RommError) else "can't be read (%s)" % (
                        e.strerror if isinstance(e, OSError) and e.strerror else e.__class__.__name__)
                    failed.append((os.path.basename(it["path"]), why))
            return done, failed, freed, tasks

        def ok(res):
            done, failed, freed, tasks = res
            self.progress = ""
            self.picks.clear()
            self.picking = False
            if tasks and not app.headless:
                app.spawn_helper("rb_chd.py")          # flock'd: no-op if already running
            if what == "unpack":
                msg = "Unpacked %d%s" % (done, ", compressing in the background" if tasks else "")
            else:
                msg = "Deleted %d, freed %s" % (done, human_size(freed))
            if failed:
                msg += " — %d couldn't be: %s (%s)" % (len(failed), failed[0][0], failed[0][1])
                rommlib.log("UNSEEN %s failed: %s" % (what, failed))
            self._done(msg, theme.AMBER if failed else theme.GREEN)

        def err(e):
            self.progress = ""
            self._failed(e)
        app.bg(run, ok, err)

    def menu(self, it):
        app = self.app
        name = os.path.basename(it["path"])
        leave = ("Leave it", "Stays where it is", lambda: None)
        delete = ("Delete", "Removes it from the box after you confirm", lambda: self.ask_delete(it))
        if self.busy:
            app.toast("Still working on the last one…", theme.AMBER)
            return
        if it["kind"] == "archive":
            entries = [("Unpack", "Shows what's inside first; unpacked here as after a download",
                        lambda: self.ask_unpack(it)), delete, leave]
        elif it["kind"] == "wrong":
            entries = [("Move to %s/" % s, "%s · roms/%s/%s" % (rommlib.system_label(s), s,
                        "" if os.path.isdir(os.path.join(rommlib.ROMS_ROOT, s)) else " (new folder)"),
                        lambda s=s: self.move_to(it, s)) for s in it["to"][:self.MOVE_CHOICES]] + [delete, leave]
        else:
            entries = [("Leave it", "ES can't list it here; it does no harm", lambda: None), delete]
        app.modal = ActionMenu(app, name, entries)

    def ask_unpack(self, it):
        app = self.app
        name = os.path.basename(it["path"])

        def shown(members):
            if not members:
                app.toast("%s is empty or can't be read" % name, theme.RED)
                return
            parts = ["%s (%s)" % (os.path.basename(m) or m, human_size(sz)) if sz is not None else m
                     for m, sz in members[:3]]
            more = " and %d more" % (len(members) - 3) if len(members) > 3 else ""
            app.modal = Confirm(app, "Unpack %s?" % name,
                                "Inside: %s%s. Unpacked into roms/%s/ as after a download (compressed "
                                "if that's on); the archive is deleted once it's out."
                                % (", ".join(parts), more, it["system"]), "Unpack", lambda: self.unpack(it))
        app.bg(lambda: rommlib.archive_contents(app.downloader, it["path"]), shown,
               lambda e: app.toast("Can't read %s (%s)" % (name, e), theme.RED))

    def ask_delete(self, it):
        app = self.app
        name = os.path.basename(it["path"])
        extra = [p for p in rommlib.game_files(it["path"]) if os.path.normpath(p) != os.path.normpath(it["path"])]
        also = " with %s it names" % plural(len(extra), "file") if extra else ""
        app.modal = Confirm(app, "Delete %s?" % name,
                            "Deletes %s (%s)%s from roms/%s/. Saves and states stay. This can't be undone."
                            % (name, human_size(it["size"]), also, it["system"]),
                            "Delete", lambda: self.delete(it), danger=True)

    def delete(self, it):
        app = self.app
        name = os.path.basename(it["path"])
        self.busy = True
        app.bg(lambda: rommlib.delete_unseen(it, app.library),
               lambda res: self._done("Deleted %s, freed %s" % (name, human_size(res[1]))), self._failed)

    def _done(self, msg, color=None):
        color = theme.GREEN if color is None else color
        self.busy = False
        self.app.toast(msg, color)
        self.load()

    def _failed(self, e):
        self._done(str(e), theme.RED)

    def unpack(self, it):
        app = self.app
        name = os.path.basename(it["path"])
        self.busy = True
        app.toast("Unpacking %s…" % name)

        def ok(res):
            out, task = res
            if task and not app.headless:
                app.spawn_helper("rb_chd.py")      # flock'd: no-op if already running
            self._done("Unpacked %s%s — ES lists it when you leave RomM-o-Cera" % (
                name, ", compressing in the background" if task else ""))
        app.bg(lambda: rommlib.unpack_unseen(app.downloader, it), ok, self._failed)

    def move_to(self, it, system):
        app = self.app
        self.busy = True
        app.bg(lambda: rommlib.move_unseen(it, system, app.library, app.downloader.es),
               lambda new: self._done("Moved to roms/%s/ — ES lists it when you leave RomM-o-Cera" % system),
               self._failed)

    def draw(self, c):
        if self.items is None:
            text(c, self.f.h2, "Looking through roms/…", (W // 2, H // 2), theme.DIM, "center")
            return
        if self.draw_load_error(c):
            return
        if not self.items:
            text(c, self.f.h3, "ES sees every game file here.", (56, 86), theme.GREEN)
            text(c, self.f.body, "No archives a system doesn't take and no games in the wrong system folder.",
                 (56, 124), theme.DIM, maxw=W - 112)
            return
        n_sys = len({i["system"] for i in self.items})
        head = "%s ES can't see, in %s" % (plural(len(self.items), "file"), plural(n_sys, "system"))
        picked = self.picked()
        if picked:
            head += " · %d picked (%s)" % (len(picked), human_size(sum(i["size"] for i in picked)))
        text(c, self.f.h3, head, (56, 86), theme.TEXT)
        if self.progress:
            text(c, self.f.small, self.progress, (W - 56, 90), theme.AMBER, "topright")
        for i, r, on in self.row_rects(124):
            it = self.items[i]
            if on:
                focus_box(c, r)
            col = theme.FAINT if it["kind"] == "unknown" else theme.AMBER
            if not self.draw_pick(c, it, (r.left + 18, r.top + 18)):
                circle(c, col, (r.left + 18, r.top + 18), 5)
            rel = os.path.relpath(it["path"], os.path.join(rommlib.ROMS_ROOT, it["system"]))
            marquee(c, self.f.row, rel, (r.left + 34, r.top + 6), theme.TEXT if on else theme.DIM,
                    maxw=r.width - 260, focused=on, slot="unseen-path")
            why = it["why"][:1].upper() + it["why"][1:]
            marquee(c, self.f.small, why, (r.left + 34, r.top + 32), col, maxw=r.width - 260, focused=on,
                    slot="unseen-why")
            tag = it["system"]
            tw = int(measure(self.f.tiny, tag)) + 14
            tr = pygame.Rect(r.right - 20 - tw, r.top + 8, tw, 20)
            rrect(c, theme.BG2, tr, 6)
            text(c, self.f.tiny, tag, tr.center, theme.DIM, "center")
            text(c, self.f.small, human_size(it["size"]), (r.right - 20, r.top + 40), theme.DIM, "midright")
