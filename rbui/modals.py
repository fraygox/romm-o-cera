"""Dialogs drawn over a screen: action menus, confirmations, info, the download prompt."""
import os
import threading

import pygame
import rommlib
from rommlib import human_size
from rbui import theme
from rbui.theme import H, W
from rbui.draw import (chevron, focus_box, glyph_width, hline, marquee, measure, meter, progress_bar, rom_title, rrect,
                       scroll_text, text)
from rbui.widgets.chips import switch
from rbui.widgets.hero import already_queued, verb_button


# ------------------------------------------------------------------ modals

class ActionMenu:
    """Modal menu. entries: list of rows, None = divider. A row is
    (label, sub, fn or None=disabled[, value[, kind]]):
      kind "action" (default): A runs fn and closes the menu;
      kind "switch": value True/False drawn as a switch, A/◀▶ flip it in place;
      kind "choice": value drawn as ‹ value ›, A/▶ next, ◀ previous, in place.
    In-place rows need `refresh` (returns fresh entries) so values update."""
    ROW, ROW_SUB, CTRL, GAP = 46, 62, 44, 14

    def __init__(self, app, title, entries, refresh=None):
        self.app, self.title, self.entries, self.refresh = app, title, entries, refresh
        self.sel = next((i for i, e in enumerate(entries) if e and e[2]), 0)

    @staticmethod
    def kind(e):
        return e[4] if len(e) > 4 else "action"

    def action(self, a):
        n = len(self.entries)
        e = self.entries[self.sel] if n else None
        if a in ("up", "down") and n:
            for _ in range(n):
                self.sel = (self.sel + (1 if a == "down" else -1)) % n
                if self.entries[self.sel] and self.entries[self.sel][2]:
                    break
        elif e and e[2] and self.kind(e) != "action" and a in ("ok", "left", "right"):
            fn = e[2]
            if self.kind(e) == "choice":
                fn(-1 if a == "left" else 1)
            else:
                fn()
            self.reload()
        elif a == "ok" and e and e[2]:
            self.app.modal = None
            e[2]()
        elif a in ("back", "quit", "x"):
            self.app.modal = None

    def tap(self, i, a="ok"):
        self.sel = i
        self.action(a)

    def reload(self):
        """Re-read entries, keeping focus on the same row (by label)."""
        if not self.refresh:
            return
        cur = self.entries[self.sel][0] if self.entries and self.entries[self.sel] else None
        self.entries = self.refresh()
        idx = next((i for i, e in enumerate(self.entries) if e and e[0] == cur), None)
        self.sel = idx if idx is not None else next((i for i, e in enumerate(self.entries) if e and e[2]), 0)

    def _height(self, e):
        if e is None:
            return self.GAP
        if self.kind(e) != "action":
            return self.CTRL
        return self.ROW_SUB if e[1] else self.ROW

    def draw(self, c):
        f = self.app.fonts
        veil = pygame.Surface(c.get_size(), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 170))
        c.blit(veil, (0, 0))
        tops, body = [], 0
        for e in self.entries:
            tops.append(body)
            body += self._height(e)
        r = pygame.Rect(0, 0, 680, min(78 + body + 18, H - 40))    # a long menu scrolls
        r.center = (W // 2, H // 2)
        self.app.hit(r, None)                    # taps on the dialog itself don't close it
        rrect(c, theme.PANEL, r, 18)
        rrect(c, theme.LINE, r, 18, 1)
        tf = f.h2 if measure(f.h2, self.title) <= r.width - 56 else f.h3     # as Confirm's title
        marquee(c, tf, self.title, (r.left + 28, r.top + 24 + (f.h2.get_height() - tf.get_height()) / theme.K / 2),
                theme.TEXT, maxw=r.width - 56, bg=theme.PANEL, slot=("menu-title", id(self)))
        view = r.height - 96                     # what the rows get
        off = getattr(self, "off", 0)
        if self.entries:                         # keep the selected row in view
            st, sh = tops[self.sel], self._height(self.entries[self.sel])
            off = min(max(off, st + sh - view), st)
        self.off = off = max(0, min(off, body - view))
        for up, show in ((True, off > 0), (False, off + view < body)):
            if show:                             # more rows above / below
                chevron(c, theme.FAINT, (r.centerx, r.top + 62 if up else r.bottom - 10), "up" if up else "down",
                        16, 10)
        for i, e in enumerate(self.entries):
            h = self._height(e)
            y = r.top + 72 + tops[i] - off
            if y < r.top + 72 or y + h > r.top + 72 + view:
                continue                                # scrolled out of the dialog
            if e is None:                               # divider between actions and view controls
                hline(c, theme.LINE, (r.left + 28, y + h // 2), (r.right - 28, y + h // 2))
                continue
            label, sub, fn = e[:3]
            value = e[3] if len(e) > 3 else None
            kind = self.kind(e)
            row = pygame.Rect(r.left + 16, y, r.width - 32, h - 6)
            if fn:
                self.app.hit(row, lambda i=i: self.tap(i))
            on = i == self.sel
            if on:
                focus_box(c, row)
            col = (theme.TEXT if on else theme.DIM) if fn else theme.FAINT
            vw = 0
            if kind == "switch":
                switch(c, (row.right - 18 - 44, row.centery - 12, 44, 24), value)
                vw = 44 + 24
            elif kind == "choice":
                vr = text(c, f.body, str(value), (row.right - 40, row.centery),
                          theme.ACCENT_HI if on else theme.DIM, "midright")
                for side, x0 in (("left", vr.left - 15), ("right", row.right - 25)):
                    chevron(c, theme.ACCENT_HI if on else theme.FAINT, (x0, row.centery), side)
                if fn:                                  # ‹ and the value's left half step back
                    bx = min(vr.left - 28, vr.centerx - 60)
                    self.app.hit((bx, row.top, vr.centerx - bx, row.height), lambda i=i: self.tap(i, "left"))
                vw = (row.right - vr.left) + 24
            elif value:
                vr = text(c, f.body, value, (row.right - 18, row.centery),
                          theme.ACCENT_HI if on else theme.DIM, "midright")
                vw = vr.width + 24
            # the focused row's label and grey line slide to show what doesn't fit; the others keep "…"
            if sub:
                marquee(c, f.body, label, (row.left + 18, row.top + 6), col, maxw=row.width - 36 - vw,
                        focused=on, bg=theme.PANEL_HI, slot="menu-label")
                marquee(c, f.small, sub, (row.left + 18, row.top + 30), theme.FAINT, maxw=row.width - 36 - vw,
                        focused=on, bg=theme.PANEL_HI, slot="menu-sub")
            else:
                marquee(c, f.body, label, (row.left + 18, row.centery), col, anchor="midleft", maxw=row.width - 36 - vw,
                        focused=on, bg=theme.PANEL_HI, slot="menu-label")


def media_note(media):
    """For a delete's confirm: the art, video and manual of the game that go with it."""
    if not media:
        return ""
    return " and its %d art, video and manual file%s" % (len(media), "" if len(media) == 1 else "s")


def plural_files(n):
    return "%d file%s" % (n, "" if n == 1 else "s")


def files_list(files, system):
    """The files a confirm names, relative to roms/<system>: all of them, up to 8, then how many more."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, system)
    names = [os.path.relpath(f, sysdir) for f in files]
    return ", ".join(names[:8]) + (" and %d more" % (len(names) - 8) if len(names) > 8 else "")


def playlists_note(system, paths):
    """For a delete's confirm: a user's own playlist naming the game stays (it isn't ours),
    and ES goes on listing it."""
    names = rommlib.playlists_naming(system, paths) if paths else []
    if not names:
        return ""
    listed = rommlib.lists_playlists(system)             # (a system ES lists no playlists for: it doesn't)
    if len(names) == 1:
        return " %s still points at it: it is kept%s." % (
            names[0], ", and ES lists it with nothing to start" if listed else "")
    return " %s and %d more playlists still point at it: they are kept%s." % (
        names[0], len(names) - 1, ", and ES lists them with nothing to start" if listed else "")


class ManageMenu(ActionMenu):
    """For a game already on the box. then: runs after a delete (a list to reload)."""

    def __init__(self, app, rom, then=None):
        system = app.system_for(rom)
        lib = app.library
        paths = lib.paths_for(rom, system) if system else []
        size = sum(rommlib.path_size(p) for p in paths)
        name = rom_title(rom)
        group = lib.group_for(rom["id"]) if system else None
        if group:
            n = len(group.get("rom_ids") or [])
            allp = []
            for rid in group["rom_ids"]:
                for p in lib.paths_for({"id": rid, "fs_name": None, "fs_name_no_ext": None}, system):
                    if p not in allp:
                        allp.append(p)
            gmedia = []                           # art and video stay: never deleted with a game
            gsize = sum(rommlib.path_size(p) for p in allp + gmedia)
            title = group.get("title") or name

            def delete_set():
                app.modal = Confirm(app, "Delete all %d discs of %s?" % (n, title),
                                    "Removes the discs and their playlist from roms/%s%s (%s). Saves and "
                                    "states are kept.%s" % (system, media_note(gmedia), human_size(gsize),
                                                            playlists_note(system, allp)), "Delete",
                                    lambda: app.uninstall_group(group, system, title, then), danger=True)
            super().__init__(app, title, [
                app.play_entry(rom, system, title),
                ("Delete all %d discs" % n, "Frees %s" % human_size(gsize), delete_set if allp else None),
                ("Download this disc again", "Replaces this disc only", lambda: app.start_download(rom, group=group)),
            ])
            return

        if paths and not lib.downloaded(rom):
            # put here another way (or changed since the app downloaded it): it acts on it only when
            # asked, and its confirm names every file that goes or gets replaced
            theirs = lib.their_files(rom, system)
            tsize = sum(rommlib.path_size(p) for p in theirs)

            def delete_theirs():
                pins = rommlib.pin_files(theirs)       # what the confirm names, as it opens
                app.modal = Confirm(app, "Delete %s?" % name,
                                    "Not downloaded by RomM-o-Cera, or changed since it was. These go from roms/%s (%s): %s. Anything else "
                                    "beside or in its folder stays (notes, saves, patches, other games); saves "
                                    "and states are kept.%s" % (system, human_size(tsize), files_list(theirs, system),
                                                                playlists_note(system, theirs)),
                                    "Delete", lambda: app.delete_theirs(rom, system, theirs, then, pins), danger=True)
            super().__init__(app, name, [
                app.play_entry(rom, system, name),
                ("Delete from this box", "Not downloaded by RomM-o-Cera, or changed since: frees %s (%s)" % (
                    human_size(tsize), plural_files(len(theirs))), delete_theirs if theirs else None),
                ("Download again", "Replaces these files with RomM's copy", (lambda: setattr(
                    app, "modal", DownloadConfirm(app, rom, replace=list(theirs)))) if theirs else None),
            ])
            return
        media = []                                # art and video stay: never deleted with a game
        size += sum(rommlib.path_size(p) for p in media)

        def delete():
            files = ", ".join(os.path.basename(p) for p in paths[:3]) + ("…" if len(paths) > 3 else "")
            app.modal = Confirm(app, "Delete %s?" % name,
                                "Removes %s from roms/%s%s (%s). Saves and states are kept.%s"
                                % (files, system, media_note(media), human_size(size), playlists_note(system, paths)),
                                "Delete", lambda: app.uninstall(rom, system, then), danger=True)
        nf = len(paths) + len(media)
        super().__init__(app, name, [
            app.play_entry(rom, system, name),
            ("Delete from this box", "Frees %s (%d file%s)" % (human_size(size), nf, "" if nf == 1 else "s"),
             delete if paths else None),
            # (the app's own copy: the download replaces what of it is still as it wrote it, checked
            # when it runs, never a file put in its place meanwhile)
            ("Download again", "Replaces what's on the box", lambda: setattr(
                app, "modal", DownloadConfirm(app, rom))),
        ])


def dialog_frame(app, c, size):
    """A dialog's ground: the screen dimmed, a panel of size (w, h) centred on it, taps
    outside it caught. Returns the panel's rect."""
    veil = pygame.Surface(c.get_size(), pygame.SRCALPHA)
    veil.fill((0, 0, 0, 170))
    c.blit(veil, (0, 0))
    r = pygame.Rect(0, 0, *size)
    r.center = (W // 2, H // 2)
    app.hit(r, None)
    rrect(c, theme.PANEL, r, 18)
    rrect(c, theme.LINE, r, 18, 1)
    return r


def dialog_buttons(app, c, r, label, on_ok, kind="go", back="Back", on_back=None, glyph="A"):
    """A dialog's buttons along its bottom: the action (as wide as its glyph and label need, a
    keyboard's Enter being wider than a pad's A) and, unless back is None, the way out (B)."""
    f = app.fonts
    bx, by = r.left + 32, r.bottom - 58
    need = 18 + glyph_width(f, glyph) + 12 + int(measure(f.h3, label)) + 22
    okr = verb_button(c, f, (bx, by, max(260, min(need, r.width - 64 - 16 - 180)), 40), label, kind, glyph=glyph)
    app.hit(okr, on_ok)
    if back is not None:
        nr = verb_button(c, f, (okr.right + 16, by, 180, 40), back, "quiet", glyph="B")
        app.hit(nr, on_back)


class Confirm:
    def __init__(self, app, title, body, ok_label, on_ok, danger=False):
        self.app, self.title, self.body = app, title, body
        self.ok_label, self.on_ok, self.danger = ok_label, on_ok, danger

    def action(self, a):
        if a == "ok":
            self.app.modal = None
            self.on_ok()
        elif a in ("back", "quit"):
            self.app.modal = None

    def draw(self, c):
        f = self.app.fonts
        r = dialog_frame(self.app, c, (640, 260))
        # a long title (a game's full name) takes the smaller heading before it slides: a sliding
        # title reads as cut off at its edge
        tf = f.h2 if measure(f.h2, self.title) <= r.width - 64 else f.h3
        marquee(c, tf, self.title, (r.left + 32, r.top + 28 + (f.h2.get_height() - tf.get_height()) / theme.K / 2),
                theme.TEXT, maxw=r.width - 64, bg=theme.PANEL, slot=("confirm-title", id(self)))
        # a body longer than its four lines scrolls by itself (never cut)
        scroll_text(c, f.body, self.body, (r.left + 32, r.top + 74, r.width - 64, 4 * 26 + 2), theme.DIM,
                    key=self.title, line_h=26, slot=("confirm", id(self)))
        dialog_buttons(self.app, c, r, self.ok_label, lambda: self.action("ok"), "danger" if self.danger else "go",
                       "Keep" if self.danger else "Back", lambda: self.action("back"))     # deleting: B keeps it


class InfoDialog(Confirm):
    """Fill in game info (text, screenshot, box art, logo, video, manual...)
    from RomM for games already on the box. Only what's missing is added — scrapes and
    edits stay — and it's written by the usual idle-time gamelist merge."""

    @staticmethod
    def all_systems(app):
        by = {}
        for p in app.platforms:
            by.setdefault(p["_system"], []).append(p["id"])
        return sorted(by.items())

    @classmethod
    def start(cls, app, systems):
        app.modal = cls(app, systems)

    @classmethod
    def start_all(cls, app):
        """Every system, once the platform list is here (asked for otherwise)."""
        if not app.platforms:
            app.load_platforms()
            app.toast(app.error or "Still loading platforms…", theme.AMBER)
        else:
            cls.start(app, cls.all_systems(app))

    def __init__(self, app, systems):
        super().__init__(app, "Fill in game info", "Matching the games on this box with RomM…",
                         "Fill in", lambda: None)
        self.pending, self.plan = True, []
        self.cancel = threading.Event()
        es = app.downloader.es
        cfg = dict(app.cfg)

        def run():
            tot = {"on_box": 0, "matched": 0, "complete": 0, "queued": 0, "waiting": 0}
            out = []
            for system, pids in systems:
                if self.cancel.is_set():
                    break
                self.body = "Matching roms/%s with RomM…" % system
                ents, st = rommlib.plan_info(app.api, system, pids, es, cfg, self.cancel)
                out += ents
                for k in tot:
                    tot[k] += st.get(k, 0)
            return out, tot
        app.bg(run, self._planned, self._failed)

    def _failed(self, e):
        self.pending = False
        self.body = "Couldn't ask RomM: %s" % e
        self.ok_label, self.on_ok = "OK", lambda: None

    def _planned(self, res):
        self.plan, st = res
        self.pending = False
        if not self.plan:
            self.title = "Nothing to fill in"
            if st.get("waiting"):
                self.body = ("%d games here, %d found in RomM: %d have everything RomM has for them, and %d "
                             "already filled in are saved when you leave RomM-o-Cera."
                             % (st["on_box"], st["matched"], st["complete"], st["waiting"]))
            else:
                self.body = ("%d games here, %d found in RomM, all already have everything RomM has for them."
                             % (st["on_box"], st["matched"]))
            self.ok_label, self.on_ok = "OK", lambda: None
            return
        self.title = "Fill in info for %d game%s?" % (len(self.plan), "" if len(self.plan) == 1 else "s")
        self.body = ("%d games here, %d found in RomM, %d already complete. Only what's missing is added: "
                     "info, and art where there's no file yet; art already on the box is never replaced. "
                     "Saved when you leave RomM-o-Cera."
                     % (st["on_box"], st["matched"], st["complete"])) + self._waiting(st)
        self.ok_label = "Fill in %d" % len(self.plan)
        self.on_ok = self._queue

    @staticmethod
    def _waiting(st):
        n = st.get("waiting", 0)
        return (" %d more %s already waiting to be saved when you leave." % (n, "is" if n == 1 else "are")) if n else ""

    def _queue(self):
        rommlib.queue_info(self.plan)
        self.app.toast("Game info queued for %d games — saved when you leave RomM-o-Cera" % len(self.plan),
                       theme.GREEN)

    def action(self, a):
        if self.pending:
            if a in ("back", "quit"):
                self.cancel.set()
                self.app.modal = None
            return
        super().action(a)


class BulkDownload:
    """Many games at once, one dialog. First, when they aren't in hand, every game of the
    scopes is listed from RomM ([(label, roms_all arguments)], one after another, with progress;
    B stops it). Then what they take, counted as Activity's forecast counts space (bulk_space),
    on a bar: the queue already waiting, these, the keep-free reserve. Whole platforms download
    only when they all fit (else: Free up space, back here after); any other list can take the
    ones that fit, or all of them waiting for room."""
    WIDTH = 820

    def __init__(self, app, title, scopes=None, roms=None, whole=False, after=None, what="", labels=None):
        """roms: the games already in hand (labels: each one's platform, for the table), else
        scopes [(label, roms_all arguments[, games expected])] to list from RomM first."""
        self.app, self.title, self.whole, self.after, self.what = app, title, whole, after, what
        self.cancel = threading.Event()
        self.state, self.error, self.sel, self.unknown, self.never, self.drive = "listing", None, 0, False, False, 0
        self.listed, self.total, self.wanted = 0, 0, 0
        self.labels, self.roms = [], []
        if roms is not None:
            self.labels = list(labels) if labels else [None] * len(roms)
            self.roms = list(roms)
            self.plan()
            return
        self.scopes = scopes
        self.total = sum(sc[2] for sc in scopes if len(sc) > 2 and sc[2])
        app.bg(self._list, self._listed, self._failed)

    # -- listing (a worker thread)
    def keep(self, r):
        """A game a download takes: one this box has a system for and doesn't hold yet; for
        whole platforms the disc games here too (their sets are put together from the listing)."""
        app = self.app
        sy = app.system_for(r)
        return bool(sy) and (not app.library.is_installed(r, sy)
                             or self.whole and bool(rommlib.disc_info(r.get("fs_name_no_ext") or "")))

    def _list(self):
        out, labels, before = [], [], 0
        for sc in self.scopes:
            label, kw = sc[0], sc[1]
            base = before

            def progress(n, total, base=base):
                self.listed = base + n
                if len(self.scopes) == 1 or not self.total:
                    self.total = max(self.total, base + total)
            got = self.app.api.roms_all(limit=250, cancel=self.cancel, keep=self.keep,
                                        fields=rommlib.BULK_FIELDS, progress=progress, **kw)
            if got is None:
                return None                           # stopped
            out += got
            labels += [label] * len(got)
            self.wanted = len(out)
            before = self.listed
        return out, labels

    def _listed(self, res):
        if res is None or self.cancel.is_set():
            return
        self.roms, self.labels = res
        self.plan()
        self.app.show_modal(self)           # (another dialog opened meanwhile: once it closes)

    def _failed(self, e):
        self.state, self.error = "failed", str(e)

    # -- what it takes
    def plan(self):
        """What's to download of the listing, what it takes and whether it fits, worked out
        (again, after Free up space) now."""
        app, d = self.app, self.app.downloader
        busy = {j.rom["id"] for j in list(d.jobs) if j.active}
        pick, labels = [], []
        for r, label in zip(self.roms, self.labels):
            sy = app.system_for(r)
            if sy and r["id"] not in busy and not app.library.is_installed(r, sy):
                pick.append((r, sy))
                labels.append(label)
        self.items = pick
        self.pool = [(r, app.system_for(r)) for r in self.roms if app.system_for(r)
                     and rommlib.disc_info(r.get("fs_name_no_ext") or "")] if self.whole else None
        rows = {}
        for (r, _), label in zip(pick, labels):
            n, b = rows.get(label, (0, 0))
            rows[label] = (n + 1, b + (r.get("fs_size_bytes") or 0))
        self.rows = [(k, n, b) for k, (n, b) in rows.items()] if len(rows) > 1 else []
        self.size = sum(r.get("fs_size_bytes") or 0 for r, _ in pick)
        free = rommlib.free_bytes(default=None)
        self.unknown = free is None             # can't tell (a share that's away): no fit to judge, as before
        if self.unknown:
            free = 1 << 62
        self.space = rommlib.bulk_space(pick, list(d.jobs), free, rommlib.keep_free_bytes(app.cfg),
                                        app.cfg, rommlib.chd_enabled(app.cfg))
        # bigger than the whole drive (with what's queued and the keep-free space): no deleting makes room
        drive = rommlib.disk_bytes()
        self.never = bool(drive) and not self.space["fits"] and \
            self.space["queue"] + self.space["need"] + self.space["keep"] > drive
        self.drive = drive
        self.fit = []
        if pick and not self.space["fits"] and not self.whole:
            room = free - self.space["queue"]
            self.fit = rommlib.fit_in_space(pick, room, self.space["keep"])[0]
        speed = max([j.speed for j in list(d.jobs) if j.state == rommlib.Job.DOWNLOADING and j.speed] or [0])
        self.hours = self.size / speed / 3600 if speed else None
        self.speed = speed
        self.state = "empty" if not pick else "confirm"
        self.sel = 0

    def options(self):
        """The choices under the bar: (label, sub, fn)."""
        n = len(self.items)
        if self.state == "empty" or self.state == "failed":
            return [("OK", "", lambda: None)]
        if self.space["fits"]:
            return [("Download {:,}".format(n), "", lambda: self.go(self.items))]
        if self.whole:
            return [] if self.never else \
                [("Free up space", "Pick games on this box to delete; back here after", self.free_up)]
        out = []
        if self.fit:
            out.append(("Download the {:,} that fit".format(len(self.fit)),
                        "The other {:,} aren't queued".format(n - len(self.fit)), lambda: self.go(self.fit)))
        out.append(("Download all {:,} anyway".format(n),
                    "The rest wait in the queue until there's room", lambda: self.go(self.items)))
        if not self.never:
            out.append(("Free up space", "Pick games on this box to delete; back here after", self.free_up))
        return out

    def go(self, chosen):
        self.app.bulk_go(chosen, self.pool, self.after)

    def free_up(self):
        from rbui.screens.space import FreeUpScreen         # (screens import this module)
        gb = max(1, -(-self.space["short"] // (1 << 30)))

        def back():
            self.plan()
            self.app.modal = self
        self.app.push(FreeUpScreen(self.app, free_gb=gb, then=back,
                                   spare={sy for _, sy in self.items}))   # never the games it would fetch again

    def action(self, a):
        app = self.app
        if self.state == "listing":
            if a in ("back", "quit"):
                self.cancel.set()
                app.modal = None
                app.toast("Stopped listing; nothing was queued")
            return
        opts = self.options()
        if self.state == "confirm" and not self.space["fits"] and not self.whole and a in ("up", "down"):
            self.sel = (self.sel + (1 if a == "down" else -1)) % len(opts)
        elif a == "ok":
            app.modal = None
            if opts:                                # (bigger than the drive: only Back)
                opts[min(self.sel, len(opts) - 1)][2]()
        elif a in ("back", "quit"):
            app.modal = None

    # -- drawing
    def draw(self, c):
        app, f = self.app, self.app.fonts
        listing = self.state in ("listing", "failed")
        list_opts = self.state == "confirm" and not self.space["fits"] and not self.whole
        r = dialog_frame(app, c, (self.WIDTH, min(self.height(list_opts), H - 120)))
        x, y, w = r.left + 32, r.top + 26, r.width - 64
        title = "Listing every game" if listing else ("Nothing to download" if self.state == "empty" else self.title)
        marquee(c, f.h2, title, (x, y), theme.TEXT, maxw=w, bg=theme.PANEL, slot=("bulk-title", id(self)))
        y += 42
        if self.state == "failed":
            scroll_text(c, f.body, "RomM couldn't list them: %s" % self.error, (x, y, w, 3 * 26), theme.RED,
                        line_h=26, slot=("bulk-err", id(self)))
            return self._buttons(c, r, [("OK", "", None)])
        if self.state == "listing":
            scroll_text(c, f.body, self.what + " Games already on this box are left out.", (x, y, w, 2 * 26),
                        theme.DIM, line_h=26, slot=("bulk-what", id(self)))
            y += 66
            tot = max(self.total, self.listed)
            text(c, f.body, "{:,} of {:,} listed".format(self.listed, tot) if tot else "Asking RomM…", (x, y),
                 theme.TEXT)
            progress_bar(c, (x, y + 34, w, 10), self.listed / tot if tot else 0)
            return self._buttons(c, r, [("Stop", "", None)], glyph="B", kind="quiet")
        if self.state == "empty":
            scroll_text(c, f.body, "Every game here is on this box or already queued.", (x, y, w, 2 * 26),
                        theme.DIM, line_h=26, slot=("bulk-empty", id(self)))
            return self._buttons(c, r, [("OK", "", None)])
        sp, n = self.space, len(self.items)
        scroll_text(c, f.body, self.what, (x, y, w, 26), theme.DIM, line_h=26, slot=("bulk-what", id(self)))
        y += 40
        if self.rows:
            y = self._table(c, f, x, y, w)
        else:
            y = self._facts(c, f, x, y, w)
        y = self._bar(c, f, x, y + 8, w)
        if list_opts:
            return self._list_opts(c, f, r, y + 10)
        if not sp["fits"]:
            why = ("This drive holds %s in all: %s can't come to this box whole%s."
                   % (human_size(self.drive), "these platforms" if self.rows else "this platform",
                      " (pick fewer)" if self.rows else "")) if self.never else \
                ("Whole platforms download only when every game fits: free up %s first%s."
                 % (human_size(sp["short"]), ", or pick fewer" if self.rows else ""))
            scroll_text(c, f.small, why, (x, y + 4, w, 44), theme.AMBER, line_h=22, slot=("bulk-short", id(self)))
        opts = self.options()
        self._buttons(c, r, [(o[0], o[1], o[2]) for o in opts] or [("Back", "", None)],
                      glyph="B" if not opts else "A", kind="quiet" if not opts else "go")

    def facts(self):
        """The dialog's numbers: (big, small, colour) each."""
        sp, n = self.space, len(self.items)
        out = [("{:,}".format(n), "games" if n != 1 else "game", theme.TEXT),
               (human_size(self.size), "to download", theme.TEXT)]
        if sp["need"] > self.size * 1.02:                 # disc games: more while they unpack
            out.append((human_size(sp["need"]), "room, estimated" if sp["estimated"] else "room needed", theme.TEXT))
        left = sp["free"] - sp["queue"] - sp["need"]
        if not self.unknown:
            out.append((human_size(left), "free afterwards", theme.TEXT) if sp["fits"] else
                       (human_size(sp["short"]), "short of room", theme.AMBER))
        return out

    def _facts(self, c, f, x, y, w):
        cells = self.facts()
        cw = (w - 16 * (len(cells) - 1)) // len(cells)
        for k, (big, small, col) in enumerate(cells):
            box = pygame.Rect(x + k * (cw + 16), y, cw, 74)
            rrect(c, theme.BG2, box, 12)
            marquee(c, f.h2, big, (box.left + 16, box.top + 10), col, maxw=box.width - 32, bg=theme.BG2,
                    slot=("bulk-big", k, id(self)))
            marquee(c, f.small, small, (box.left + 16, box.top + 46), theme.DIM, maxw=box.width - 32,
                    bg=theme.BG2, slot=("bulk-fact", k, id(self)))
        return y + 86

    def height(self, list_opts):
        """The dialog's height for what it shows (the layout draw() lays down)."""
        if self.state != "confirm":
            return 26 + 42 + 66 + 48 + 20 + 62
        h = 26 + 42 + 40 + (28 + (len(self.rows) + 1) * 30 + 6 if self.rows else 86) + 8 + (30 if self.unknown else 84)
        h += 26 if self.hours is not None else 0
        if list_opts:
            return h + 10 + len(self.options()) * 66 + 16
        return h + (52 if not self.space["fits"] else 0) + 82

    def _table(self, c, f, x, y, w):
        nw, sw = 120, 130
        text(c, f.small, "Platform", (x, y), theme.FAINT)
        text(c, f.small, "Games", (x + w - sw - 16, y), theme.FAINT, "topright")
        text(c, f.small, "Download", (x + w, y), theme.FAINT, "topright")
        y += 28
        total = ("All {:,}".format(len(self.rows)), len(self.items), self.size)
        for k, (label, n, b) in enumerate(self.rows + [total]):
            col = theme.TEXT if k == len(self.rows) else theme.DIM
            text(c, f.body, label, (x, y), theme.TEXT, maxw=w - nw - sw - 32)
            text(c, f.body, "{:,}".format(n), (x + w - sw - 16, y), col, "topright")
            text(c, f.body, human_size(b), (x + w, y), col, "topright")
            y += 30
        return y + 6

    def _bar(self, c, f, x, y, w):
        sp = self.space
        if self.unknown:
            text(c, f.small, "The free space on this box can't be read right now", (x, y), theme.AMBER)
            return y + 30
        free, queue, need, keep = sp["free"], sp["queue"], sp["need"], sp["keep"]
        whole = max(free, queue + need + keep, 1)
        text(c, f.small, "Space on this box", (x, y), theme.DIM)
        text(c, f.small, "%s free" % human_size(free), (x + w, y), theme.FAINT, "topright")
        y += 28
        over = max(0, queue + need + keep - free)
        parts = [(queue / whole, theme.FAINT), (min(need, max(0, free - queue - keep)) / whole, theme.ACCENT)]
        if over:
            parts.append((over / whole, theme.AMBER))
        meter(c, (x, y, w, 16), parts, reserve=keep / whole if not over else 0)
        y += 26
        key = ([("waiting in the queue", theme.FAINT)] if queue else []) + [("these games", theme.ACCENT),
                                                                           ("kept free: %s" % human_size(keep), theme.LINE)]
        if over:
            key.append(("%s more than the box has" % human_size(over), theme.AMBER))
        kx = x
        for label, col in key:
            rrect(c, col, (kx, y + 4, 12, 12), 3)
            kx = text(c, f.small, label, (kx + 18, y), theme.DIM).right + 22
            if kx > x + w:
                break
        if self.hours is not None:            # at the speed of the download under way
            t = ("about %d min" % max(1, round(self.hours * 60))) if self.hours < 1 else \
                ("about %.1f hours" % self.hours if self.hours < 48 else "about %d days" % round(self.hours / 24))
            text(c, f.small, "%s at %s/s" % (t, human_size(self.speed)), (x, y + 26), theme.FAINT)
            return y + 56
        return y + 30

    def _list_opts(self, c, f, r, y):
        for i, (label, sub, _) in enumerate(self.options()):
            row = pygame.Rect(r.left + 20, y, r.width - 40, 60)
            if i == self.sel:
                focus_box(c, row, 12)
            marquee(c, f.h3, label, (row.left + 14, row.top + 8), theme.TEXT, maxw=row.width - 28,
                    focused=i == self.sel, slot=("bulk-opt", i, id(self)))
            marquee(c, f.small, sub, (row.left + 14, row.top + 34), theme.DIM, maxw=row.width - 28,
                    focused=i == self.sel, slot=("bulk-sub", i, id(self)))
            self.app.hit(row, lambda i=i: (setattr(self, "sel", i), self.action("ok")))
            y += 66

    def _buttons(self, c, r, opts, glyph="A", kind="go"):
        label = opts[0][0]
        stop = glyph == "B"                          # listing: B is the one button
        dialog_buttons(self.app, c, r, label, lambda: self.action("back" if stop else "ok"), kind,
                       None if stop or label == "OK" else "Back", lambda: self.action("back"), glyph=glyph)


class DownloadConfirm(Confirm):
    """Download one game. A disc of a multi-disc set first looks the set up in
    RomM, then offers all discs (one ES entry via an .m3u) or just this one."""

    def __init__(self, app, rom, replace=None, group=None):
        """replace: files on the box this download takes the place of (another name: Compare's
        "Get the newer one"), removed once it's in. group: the disc set the game is part of, kept by the download."""
        system = app.system_for(rom)
        st = app.rom_status(rom)
        name = rom.get("name") or rom.get("fs_name_no_ext")
        self.pending = False
        if not system:
            super().__init__(app, name, "This platform has no Batocera system to put it in.",
                             "OK", lambda: None)
            return
        if st[0] == "active":
            super().__init__(app, name, already_queued(st[1]) + ".", "OK", lambda: None)
            return
        self.app = app
        self._rom, self._replace, self._group = rom, replace, group
        self._pins = rommlib.pin_files(replace) if replace else None      # the files it names, now
        body = self._body(rom, system, st)
        super().__init__(app, name, body, "Download", self._start)
        if rommlib.needs_listing(rom, system):       # an estimate until RomM tells what the zip holds
            def listed(_):
                j = app.downloader.job_for(rom["id"])
                if j is not None and j.rom is rom:
                    j.touch()                        # queued meanwhile: the listing went into its record
                if app.modal is self and not self.pending:
                    about = self.body[:len(self.body) - len(body)] if self.body.endswith(body) else ""
                    self.body = about + self._body(rom, system, st)
            app.bg(lambda: rommlib.zip_listing(app.api, rom, system), listed, lambda e: None)
        if rommlib.disc_info(rom.get("fs_name_no_ext") or "") and st[0] != "installed" and replace is None:
            # (a replacement is this one disc on purpose: no set menu, which would drop replace)
            self.pending = True
            self.body = "Looking for the other discs in RomM…"
            app.bg(lambda: rommlib.find_disc_set(app.api, rom),
                   lambda res: self._found(rom, system, body, *res),
                   lambda e: self._lookup_failed(body, e))

    def _start(self):
        kw = {} if self._replace is None else {"replace": self._replace, "pins": self._pins}
        if self._group:
            kw["group"] = self._group
        self.app.start_download(self._rom, **kw)

    def _body(self, rom, system, st):
        app = self.app
        body = app.plan_text(rom, system)
        if st[0] == "installed":
            body = "Already on this box. Download again anyway?  " + body
            if self._replace and not app.library.downloaded(rom):
                body += "  Not downloaded by RomM-o-Cera: once RomM's copy is in, these go: %s." % files_list(
                    self._replace, system)
        var = app.library.variants(rom, system)
        if var:
            body += "  You already have %s%s." % (var[0], " and %d more" % (len(var) - 1) if len(var) > 1 else "")
        return body

    def _lookup_failed(self, body, e):
        self.pending = False
        self.body = body + "  (Couldn't look up the other discs: %s)" % e

    def _found(self, rom, system, body, discs, missing):
        app = self.app
        self.pending = False
        if app.modal is not self:
            return                            # closed meanwhile
        if not discs or len(discs) < 2:
            self.body = body + "  No other discs of it found in RomM."
            return
        me = rommlib.disc_info(rom.get("fs_name_no_ext") or "")[1]
        here = [r for _, r in discs if r["id"] != rom["id"] and app.library.is_installed(r, system)]
        size = sum(r.get("fs_size_bytes") or 0 for _, r in discs
                   if r["id"] == rom["id"] or not app.library.is_installed(r, system))
        m3u = app.downloader._accepts(system, ".m3u")
        sub = "%s · %s" % (human_size(size), "one entry in EmulationStation" if m3u
                           else "each disc listed separately")
        if here:
            sub += " · %d already here" % len(here)
        entries = [("Download all %d discs" % len(discs), sub, lambda: app.download_set(rom, discs))]
        if missing:
            entries.append(("Disc %s not in RomM" % ", ".join(map(str, missing)),
                            "The set looks incomplete", None))
        entries.append(("Only disc %d" % me, human_size(rom.get("fs_size_bytes")),
                        lambda: app.start_download(rom)))
        var = app.library.variants(rom, system)
        if var:
            entries += [None, ("Already here: %s" % var[0],
                               ("and %d more" % (len(var) - 1)) if len(var) > 1 else "Another version", None)]
        app.modal = ActionMenu(app, app.set_title(rom), entries)

    def action(self, a):
        if self.pending and a == "ok":
            return
        super().action(a)
