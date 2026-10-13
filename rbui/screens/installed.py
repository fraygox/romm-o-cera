"""Games from RomM on this box, and the conversion queue."""
import os
import time

import pygame
import rommlib
from rommlib import human_size
from rbui import pads, theme
from rbui.theme import H, W
from rbui.draw import focus_box, marquee, progress_bar, rrect, scroll_text, text
from rbui.screens.base import letter_of, ListScreen
from rbui.screens.games import GameListScreen
from rbui.screens.box_data import conversion_rows, conversion_status, installed_games, plural
from rbui.modals import ActionMenu, Confirm, ManageMenu


class InstalledScreen(ListScreen):
    """Games RomM-o-Cera installed, biggest first (Y: by name or newest),
    with RomM's cover and details beside them. A: the game's menu, as in a
    game list (delete, download again, more like this); X: delete several."""
    ROWS, ROW_H = 10, 54
    LX, LW = 32, 676                     # the list; the game panel is to its right, as in a game list
    SORTS = (("size", "Biggest first"), ("name", "Name"), ("new", "Newest first"))
    PICKS, PICK_WHAT = True, "games"
    JUMP, JUMP_OFF = "A–Z", "A–Z jump works when sorted by name ({Y})"
    BUBBLE_AT = (LX + LW // 2, H // 2)
    sort = "size"
    draw_detail = GameListScreen.draw_detail        # RomM's record: cover, facts, summary
    variants = GameListScreen.variants
    _more_like = GameListScreen._more_like

    def __init__(self, app):
        super().__init__(app)
        self.items = None
        self.sel = self.scroll = 0
        self.load()

    def load(self):
        self.load_error = None
        self.app.bg(lambda: installed_games(self.app.library), self._loaded, self.load_failed("items"))

    def _loaded(self, items):
        self.items = items
        self.resort()
        self.clamp(scroll=True)

    def resort(self):
        if self.items is None:
            return
        if self.sort == "name":
            self.items.sort(key=lambda i: (i.get("name") or "").lower())
        elif self.sort == "new":
            self.items.sort(key=lambda i: i.get("when") or "", reverse=True)
        else:
            self.items.sort(key=lambda i: -(i.get("size") or 0))

    def cycle_sort(self, step=1):
        keys = [k for k, _ in self.SORTS]
        self.sort = keys[(keys.index(self.sort) + step) % len(keys)]
        self.resort()
        self.clamp()

    def enter(self):
        self.load()

    def crumbs(self):
        return self.trail("Games from RomM")

    def hints(self):
        if not self.items:
            return [("B", "Back")]
        return self.list_hints(("A", "Options"), ("X", pads.ACTIONS), ("Y", dict(self.SORTS)[self.sort]))

    def pick_key(self, it):
        return it["id"]

    def jump_label(self, it):
        return letter_of(it.get("name")) if self.sort == "name" else None

    def rom_status(self, rom):
        return self.app.rom_status(rom)

    def record(self, it):
        """RomM's record of a row's game: a dict, {} when RomM hasn't got it
        (or can't be reached), None while it's being asked."""
        d = self.app.detail({"id": it["id"]})
        return d if isinstance(d, dict) else None

    def list_controls(self):
        return [("Sort", "", self.cycle_sort, dict(self.SORTS)[self.sort], "choice")]

    def pick_entries(self, items):
        size = sum(i["size"] for i in items)
        return [("Delete %d picked" % len(items), "Frees %s, after you confirm" % human_size(size),
                 lambda: self.ask_delete_many(items))]

    def ask_delete_many(self, items):
        app, lib = self.app, self.app.library
        size = sum(i["size"] for i in items)

        def run():
            freed = 0
            for it in items:
                if it.get("group"):
                    freed += lib.uninstall_group(it["group"], it["system"])[1]
                else:
                    freed += lib.uninstall({"id": it["id"], "name": it["name"]}, it["system"])[1]
            return freed

        def done(freed):
            self.picks.clear()
            self.picking = False
            app.toast("Deleted %s — %s freed. Saves are kept." % (plural(len(items), "game"), human_size(freed)),
                      theme.GREEN)
            app.space_freed()
            self.load()
        app.modal = Confirm(app, "Delete %s?" % plural(len(items), "game"),
                            "Frees about %s. Every file and disc of them goes from roms/; saves and states are "
                            "kept." % human_size(size), "Delete %d" % len(items),
                            lambda: app.bg(run, done, lambda e: (app.toast(str(e), theme.RED), self.load())), danger=True)

    def ask_delete(self, it):
        app = self.app
        rom = {"id": it["id"], "name": it["name"]}
        if it.get("group"):
            app.modal = Confirm(app, "Delete %s?" % it["name"],
                                "Removes every disc and the playlist from roms/%s (%s). Saves and "
                                "states are kept." % (it["system"], human_size(it["size"])), "Delete",
                                lambda: app.uninstall_group(it["group"], it["system"], it["name"], self.load),
                                danger=True)
            return
        app.modal = Confirm(app, "Delete %s?" % it["name"],
                            "Removes it from roms/%s (%s). Saves and states are kept."
                            % (it["system"], human_size(it["size"])), "Delete",
                            lambda: app.uninstall(rom, it["system"], self.load), danger=True)

    def menu(self, it):
        """The game's menu from a game list, plus More like this; delete only
        while RomM's record isn't here."""
        app = self.app
        d = self.record(it)
        if d and d.get("id") is not None and app.system_for(d):
            m = ManageMenu(app, d, then=self.load)
            m.entries.append(("More like this", "Games RomM says are like it", lambda: self._more_like(d)))
            app.modal = m
            return
        why = "Looking it up in RomM…" if d is None else "RomM doesn't have it, or can't be reached"
        app.modal = ActionMenu(app, it["name"], [
            ("Delete from this box", "Frees %s" % human_size(it["size"]), lambda: self.ask_delete(it)),
            ("Download again", why, None), ("More like this", why, None)])

    def action(self, a):
        if not self.items:
            return super().action(a)
        if self.nav(a) or self.pick_action(a):
            return
        if a == "ok":
            self.menu(self.items[self.sel])
        elif a == "y":
            self.cycle_sort()
        else:
            super().action(a)

    def draw(self, c):
        if self.items is None:
            text(c, self.f.h2, "Measuring…", (W // 2, H // 2), theme.DIM, "center")
            return
        if self.draw_load_error(c):
            return
        try:
            st = os.statvfs(rommlib.ROMS_ROOT)
            free = human_size(st.f_bavail * st.f_frsize)
        except OSError:
            free = "?"
        if not self.items:
            text(c, self.f.h3, "No games from RomM here. %s free." % free, (56, 86), theme.TEXT)
            text(c, self.f.body, "Nothing installed by RomM-o-Cera yet. Games copied over another way "
                 "can be deleted from their game list.", (56, 130), theme.DIM)
            return
        lx, lw = self.LX, self.LW
        head = "%s · %s · %s free" % (plural(len(self.items), "game"),
                                      human_size(sum(i["size"] for i in self.items)), free)
        n = self.pick_count()
        if n:
            head += " · %d picked" % n
        text(c, self.f.h3, head, (lx + 8, 86), theme.TEXT, maxw=lw - 170)
        text(c, self.f.small, dict(self.SORTS)[self.sort], (lx + lw - 8, 92), theme.FAINT, "topright")
        for i, r, on in self.row_rects(124, x=lx, w=lw):
            it = self.items[i]
            if on:
                focus_box(c, r)
            nx = r.left + (44 if self.draw_pick(c, it, (r.left + 22, r.centery)) else 18)
            sr = text(c, self.f.small, human_size(it["size"]), (r.right - 14, r.centery), theme.DIM, "midright")
            marquee(c, self.f.row, it["name"], (nx, r.top + 4), theme.TEXT if on else theme.DIM,
                    maxw=sr.left - 16 - nx, focused=on, slot="installed-name")
            sub = (it["system"] or "?") + ("  ·  added %s" % it["when"][:10] if it.get("when") else "")
            text(c, self.f.small, sub, (nx, r.top + 29), theme.FAINT, maxw=sr.left - 16 - nx)
        it = self.items[self.sel]
        d = self.record(it)
        if d and d.get("id") is not None:
            self.draw_detail(c, d)
        else:
            self.draw_local(c, it, "Looking it up in RomM…" if d is None else
                            "RomM doesn't have this game any more, or can't be reached: only Delete works.")

    def draw_local(self, c, it, note):
        """The panel from what's on the box, while RomM's record isn't here."""
        p = pygame.Rect(728, 80, 520, 576)
        rrect(c, theme.PANEL, p, 16)
        x, y, w = p.left + 24, p.top + 24, p.width - 48
        y = scroll_text(c, self.f.h3, it["name"], (x, y, w, 3 * 25), theme.TEXT, line_h=25,
                        slot="local-title").bottom + 12
        for k, v in (("System", it["system"]), ("Size", human_size(it["size"])), ("Added", it.get("when", "")[:10])):
            if v:
                text(c, self.f.small, k, (x, y), theme.FAINT)
                text(c, self.f.cond, str(v), (x, y + 17), theme.DIM, maxw=w)
                y += 42
        scroll_text(c, self.f.small, note, (x, y + 12, w, min(3 * 20, p.bottom - 24 - y - 12)), theme.FAINT,
                    line_h=20, slot="local-note")


def _lastplayed_label(lp):
    return "never played" if not lp else "played %s-%s-%s" % (lp[:4], lp[4:6], lp[6:8])


class ConversionsScreen(ListScreen):
    """Background CHD / RVZ conversions: the one running (progress, time
    left), the queue, and recent results (retry a failed one). The worker
    (rb_chd.py) publishes chd-current.json and chd-results.jsonl."""
    ROW_H = 60
    ROWS = (H - 60 - 124) // ROW_H      # 8: as many as fit, and L/R pages by as many
    MODES = rommlib.CONVERT_LABELS

    def __init__(self, app):
        super().__init__(app)
        self.sel = self.scroll = 0
        self.rows, self.at = [], 0
        self.refresh()

    def refresh(self):
        rows = conversion_rows()
        self.rows, self.at = rows, time.time()
        self.clamp()

    def listed(self):
        return self.rows

    def crumbs(self):
        return self.trail("Conversions")

    def hints(self):
        h = [("A", "Options")] if self.rows else []
        if any(r["kind"] in ("queued", "running") for r in self.rows):
            h.append(("X", "Start now"))
        if any(r["kind"] not in ("queued", "running") for r in self.rows):
            h.append(("Y", "Clear finished"))
        return self.list_hints(*h)

    def action(self, a):
        app = self.app
        n = len(self.rows)
        if self.nav(a):
            return
        if a == "ok" and n:
            self.menu(self.rows[self.sel])
        elif a == "x":
            app.spawn_helper("rb_chd.py")
            app.toast("Conversions start now if nothing is playing and there's room")
        elif a == "y":
            try:
                os.remove(rommlib.chd_results_path())
            except OSError:
                pass
            self.refresh()
        else:
            super().action(a)

    def menu(self, r):
        app = self.app
        e = []
        if r["kind"] == "queued":
            def gone():
                app.toast("It's converting now, or done already", theme.AMBER)
                self.refresh()

            def first():
                if not rommlib.queue_task_first(r["name"]):
                    return gone()
                self.refresh()

            def drop():
                if not rommlib.drop_queue_task(r["name"]):
                    return gone()
                app.toast("Removed from the queue — the original stays as it is")
                self.refresh()
            e += [("Convert next", "", first), ("Remove from the queue", "The original is kept as it is", drop)]
        elif r["kind"] == "running":
            e.append(("Converting now", "It pauses by itself while a game runs", None))
        elif r["kind"] in ("failed", "waiting") and r.get("task"):
            def retry():
                rommlib.publish_queue_task(r["task"], "%d-retry.json" % int(time.time() * 1000))
                app.spawn_helper("rb_chd.py")
                app.toast("Queued again: %s" % r["title"], theme.GREEN)
                self.refresh()
            e.append(("Try again", r.get("message") or "", retry))
        else:
            e.append((r["title"], r.get("message") or "", None))
        app.modal = ActionMenu(app, r["title"], e)

    def draw(self, c):
        if time.time() - self.at > 1.0:
            self.refresh()
        if not self.rows:
            text(c, self.f.h2, "Nothing to convert", (W // 2, 300), theme.DIM, "center")
            text(c, self.f.body, "Disc games are compressed here in the background after they download.",
                 (W // 2, 340), theme.FAINT, "center")
            return
        nq = sum(1 for r in self.rows if r["kind"] in ("queued", "running"))
        text(c, self.f.h3, "%d waiting · runs while nothing is playing" % nq if nq else "Queue empty",
             (56, 86), theme.TEXT)
        col = {"running": theme.BLUE, "queued": theme.DIM, "ok": theme.GREEN, "failed": theme.RED,
               "skipped": theme.AMBER, "waiting": theme.AMBER}
        for i, box, on in self.row_rects(124, gap=6):
            r = self.rows[i]
            rrect(c, theme.PANEL_HI if on else theme.PANEL, box, 12)
            if on:
                rrect(c, theme.ACCENT, box, 12, 2)
            mode = self.MODES.get((r.get("task") or {}).get("mode") or r.get("mode"), "")
            title = r["title"] + (" (%d discs)" % r["discs"] if r["discs"] > 1 else "")
            marquee(c, self.f.h3, title, (box.left + 20, box.top + 8), theme.TEXT if on else theme.DIM,
                    maxw=box.width - 500, focused=on, slot="conv-title")
            k = r["kind"]
            if k == "running":
                st, frac = conversion_status(r)
                progress_bar(c, (box.left + 460, box.top + 40, box.width - 480, 8), frac, theme.BLUE)
                sub = "%s · %s" % (mode, human_size(r["size"]))
            elif k == "queued":
                st = "Waiting for %s" % r["tool"] if r.get("tool") else "Queued"
                sub = "%s · %s" % (mode, human_size(r["size"]))
            else:
                st = {"ok": "Done", "failed": "Failed", "skipped": "Skipped", "waiting": "Waiting"}.get(k, k)
                sub = r.get("message") or ""
            text(c, self.f.small, st, (box.right - 20, box.top + 12), col.get(k, theme.DIM), "topright", maxw=440)
            marquee(c, self.f.small, sub, (box.left + 20, box.top + 34), theme.FAINT,
                    maxw=(box.width - 40) if k not in ("running",) else 420, focused=on, slot="conv-sub")
