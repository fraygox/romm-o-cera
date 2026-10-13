"""Dialogs drawn over a screen: action menus, confirmations, info, the download prompt."""
import os
import threading

import pygame
import rommlib
from rommlib import human_size
from rbui import theme
from rbui.theme import H, W
from rbui.draw import chevron, focus_box, hline, marquee, rom_title, rrect, scroll_text, text
from rbui.widgets.chips import switch
from rbui.widgets.hero import verb_button


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
        text(c, f.h2, self.title, (r.left + 28, r.top + 24), theme.TEXT, maxw=r.width - 56)
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
            gsize = sum(rommlib.path_size(p) for p in allp)
            title = group.get("title") or name

            def delete_set():
                app.modal = Confirm(app, "Delete all %d discs of %s?" % (n, title),
                                    "Removes the discs and their playlist from roms/%s (%s). Saves and "
                                    "states are kept." % (system, human_size(gsize)), "Delete",
                                    lambda: app.uninstall_group(group, system, title, then), danger=True)
            super().__init__(app, title, [
                app.play_entry(rom, system, title),
                ("Delete all %d discs" % n, "Frees %s" % human_size(gsize), delete_set if allp else None),
                ("Download this disc again", "Replaces this disc only", lambda: app.start_download(rom)),
            ])
            return

        def delete():
            files = ", ".join(os.path.basename(p) for p in paths[:3]) + ("…" if len(paths) > 3 else "")
            app.modal = Confirm(app, "Delete %s?" % name,
                                "Removes %s from roms/%s (%s). Saves and states are kept."
                                % (files, system, human_size(size)),
                                "Delete", lambda: app.uninstall(rom, system, then), danger=True)
        super().__init__(app, name, [
            app.play_entry(rom, system, name),
            ("Delete from this box", "Frees %s (%d file%s)" % (human_size(size), len(paths),
                                                         "" if len(paths) == 1 else "s"),
             delete if paths else None),
            ("Download again", "Replaces what's on the box", lambda: setattr(
                app, "modal", DownloadConfirm(app, rom))),
        ])


class Confirm:
    alt = None                      # (label, fn): a third button on Y (Surprise me's "Another")

    def __init__(self, app, title, body, ok_label, on_ok, danger=False):
        self.app, self.title, self.body = app, title, body
        self.ok_label, self.on_ok, self.danger = ok_label, on_ok, danger

    def action(self, a):
        if a == "ok":
            self.app.modal = None
            self.on_ok()
        elif a == "y" and self.alt:
            self.app.modal = None
            self.alt[1]()
        elif a in ("back", "quit"):
            self.app.modal = None

    def draw(self, c):
        f = self.app.fonts
        veil = pygame.Surface(c.get_size(), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 170))
        c.blit(veil, (0, 0))
        r = pygame.Rect(0, 0, 640, 260)
        r.center = (W // 2, H // 2)
        self.app.hit(r, None)
        rrect(c, theme.PANEL, r, 18)
        rrect(c, theme.LINE, r, 18, 1)
        text(c, f.h2, self.title, (r.left + 32, r.top + 28), theme.TEXT, maxw=r.width - 64)
        # a body longer than its four lines scrolls by itself (never cut)
        scroll_text(c, f.body, self.body, (r.left + 32, r.top + 74, r.width - 64, 4 * 26 + 2), theme.DIM,
                    key=self.title, line_h=26, slot=("confirm", id(self)))
        bx = r.left + 32
        by = r.bottom - 58
        okw, nw = (220, 150) if self.alt else (260, 180)
        okr = verb_button(c, f, (bx, by, okw, 40), self.ok_label, "danger" if self.danger else "go")
        self.app.hit(okr, lambda: self.action("ok"))
        nx = okr.right + 16
        if self.alt:
            ar = verb_button(c, f, (nx, by, 170, 40), self.alt[0], "quiet", glyph="Y")
            self.app.hit(ar, lambda: self.action("y"))
            nx = ar.right + 16
        nr = verb_button(c, f, (nx, by, nw, 40), "Back", "quiet", glyph="B")
        self.app.hit(nr, lambda: self.action("back"))


class InfoDialog(Confirm):
    """Fill in game info (name, description, cover, manual) from RomM for
    games already on the box. Only what's missing is added — scrapes and
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

    def __init__(self, app, systems):
        super().__init__(app, "Fill in game info", "Matching the games on this box with RomM…",
                         "Fill in", lambda: None)
        self.pending, self.plan = True, []
        self.cancel = threading.Event()
        es = app.downloader.es
        manuals = app.cfg["manuals"]

        def run():
            tot = {"on_box": 0, "matched": 0, "complete": 0, "queued": 0}
            out = []
            for system, pids in systems:
                if self.cancel.is_set():
                    break
                self.body = "Matching roms/%s with RomM…" % system
                ents, st = rommlib.plan_info(app.api, system, pids, es, manuals, self.cancel)
                out += ents
                for k in tot:
                    tot[k] += st[k]
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
            self.body = ("%d games here, %d found in RomM, all already have a name, description and cover."
                         % (st["on_box"], st["matched"]))
            self.ok_label, self.on_ok = "OK", lambda: None
            return
        self.title = "Fill in info for %d game%s?" % (len(self.plan), "" if len(self.plan) == 1 else "s")
        self.body = ("%d games here, %d found in RomM, %d already complete. Only what's missing is added — "
                     "nothing you have is replaced. Saved when you leave RomM-o-Cera."
                     % (st["on_box"], st["matched"], st["complete"]))
        self.ok_label = "Fill in %d" % len(self.plan)
        self.on_ok = self._queue

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


class DownloadConfirm(Confirm):
    """Download one game. A disc of a multi-disc set first looks the set up in
    RomM, then offers all discs (one ES entry via an .m3u) or just this one."""

    def __init__(self, app, rom):
        system = app.system_for(rom)
        st = app.rom_status(rom)
        name = rom.get("name") or rom.get("fs_name_no_ext")
        self.pending = False
        if not system:
            super().__init__(app, name, "This platform has no Batocera system to put it in.",
                             "OK", lambda: None)
            return
        if st[0] == "active":
            super().__init__(app, name, "Already downloading.", "OK", lambda: None)
            return
        self.app = app
        body = self._body(rom, system, st)
        super().__init__(app, name, body, "Download", lambda: app.start_download(rom))
        if rommlib.needs_listing(rom, system):       # an estimate until RomM tells what the zip holds
            def listed(_):
                if app.modal is self and not self.pending:
                    about = self.body[:len(self.body) - len(body)] if self.body.endswith(body) else ""
                    self.body = about + self._body(rom, system, st)
            app.bg(lambda: rommlib.zip_listing(app.api, rom, system), listed, lambda e: None)
        if rommlib.disc_info(rom.get("fs_name_no_ext") or "") and st[0] != "installed":
            self.pending = True
            self.body = "Looking for the other discs in RomM…"
            app.bg(lambda: rommlib.find_disc_set(app.api, rom),
                   lambda res: self._found(rom, system, body, *res),
                   lambda e: self._lookup_failed(body, e))

    def _body(self, rom, system, st):
        app = self.app
        body = app.plan_text(rom, system)
        if st[0] == "installed":
            body = "Already on this box. Download again anyway?  " + body
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
        if self.alt:                          # a Surprise me pick: roll again from the discs menu too
            entries += [None, ("Another surprise", "Pick again", self.alt[1])]
        app.modal = ActionMenu(app, (self.title.split(":", 1)[0] + ": " if self.alt else "") + app.set_title(rom),
                               entries)

    def action(self, a):
        if self.pending and a == "ok":
            return
        super().action(a)
