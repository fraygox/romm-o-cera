"""Storage: space by system, Free up space, Leftovers."""
import os

import pygame
import rommlib
from rommlib import human_size
from rbui import theme
from rbui.theme import H, W
from rbui.draw import circle, focus_box, hline, measure, rrect, scroll_text, text
from rbui.screens.base import letter_of, ListScreen
from rbui.screens.box_data import disk_space, pending_roms, plural, space_bar, space_parts, storage_by_system
from rbui.screens.installed import _lastplayed_label
from rbui.modals import Confirm
from rbui.widgets.layout import Panel, ROWS_TOP, bubble_at, hint_actions, list_head, list_row, need_width, split


class SpaceScreen(ListScreen):
    """The whole drive as one bar (what's used, what downloads may use, the
    keep-free reserve), the two ways to get space back, then space per
    system. A on a system: free up space there."""
    ROWS, ROW_H, TOP = 6, 54, 326
    TILES = (("free", "Free up space"), ("leftovers", "Clean up leftovers"))
    base = 2

    def __init__(self, app):
        super().__init__(app)
        self.rows = self.leftovers = None
        self.sel = self.scroll = 0          # 0, 1: the tiles; 2…: systems

    def listed(self):
        return self.rows or []

    def enter(self):
        app = self.app
        app.bg(lambda: storage_by_system(app.library), self._loaded,
               lambda e: (setattr(self, "rows", []), app.toast(str(e), theme.RED)))
        app.bg(lambda: rommlib.find_leftovers(pending_roms(app)), lambda v: setattr(self, "leftovers", v),
               lambda e: setattr(self, "leftovers", []))

    def _loaded(self, rows):
        self.rows = rows
        self.clamp()

    def crumbs(self):
        return self.trail("Space")

    def hints(self):
        if self.sel < 2:
            return [("A", "Open"), ("B", "Back")]
        return self.list_hints(("A", "Free up space here"))

    def action(self, a):
        app = self.app
        n = len(self.rows or [])
        if self.tile_nav(a, wrap=True):
            return
        if a == "ok":
            if self.sel == 0:
                app.push(FreeUpScreen(app))
            elif self.sel == 1:
                app.push(LeftoversScreen(app))
            elif n:
                app.push(FreeUpScreen(app, [self.rows[self.sel - 2]["system"]]))
        else:
            super().action(a)

    def tap_tile(self, i):
        self.sel = i
        self.action("ok")

    def draw(self, c):
        total, free, keep = disk_space(self.app.cfg)
        low = free < keep
        text(c, self.f.h1, human_size(free) + " free", (56, 78), theme.AMBER if low else theme.TEXT)
        text(c, self.f.small, "of %s   Keep free on disk: %s (Settings)" % (human_size(total), human_size(keep)),
             (W - 56, 94), theme.FAINT, "topright")
        if self.rows is None:
            text(c, self.f.h2, "Measuring every system…", (W // 2, H // 2), theme.DIM, "center")
            return
        space_bar(c, self.f, (56, 128, W - 112, 26), space_parts(self.rows, total, free), total, free, keep, cols=3)
        lo = self.leftovers
        subs = ["Every game on the box, least played first",
                "Looking…" if lo is None else "Nothing to clean up" if not lo else
                "%s from interrupted downloads" % human_size(sum(i["size"] for i in lo))]
        tw = (W - 96 - 16) // 2
        for i, (key, label) in enumerate(self.TILES):
            r = pygame.Rect(48 + i * (tw + 16), 240, tw, 64)
            self.app.hit(r, lambda i=i: self.tap_tile(i))
            on = self.sel == i
            if on:
                focus_box(c, r, 12)
            else:
                rrect(c, theme.PANEL, r, 12)
            text(c, self.f.h3, label, (r.left + 22, r.top + 10), theme.TEXT if on else theme.DIM)
            text(c, self.f.small, subs[i], (r.left + 22, r.top + 37),
                 theme.AMBER if key == "leftovers" and lo else theme.FAINT, maxw=r.width - 44)
        hline(c, theme.LINE, (56, 316), (W - 56, 316))
        if not self.rows:
            text(c, self.f.body, "No games on this box yet.", (56, self.TOP + 10), theme.DIM)
            return
        big = self.rows[0]["total"] or 1
        for i, r, on in self.row_rects(self.TOP):
            r_ = self.rows[i]
            if on:
                focus_box(c, r)
            text(c, self.f.row, r_["system"], (r.left + 22, r.top + 4), theme.TEXT if on else theme.DIM)
            x, ty = r.left + 22, r.top + 28      # each part's dot is its colour in the bar: the legend
            for key, col, lab in theme.SPACE_PARTS:
                if r_[key] and x < r.left + 560:
                    circle(c, col, (x + 5, ty + 9), 5)
                    lab = "%s %s" % (lab, human_size(r_[key]))
                    text(c, self.f.small, lab, (x + 13, ty), theme.FAINT)
                    x += 13 + measure(self.f.small, lab) + 18
            bx, bw = r.left + 600, r.width - 760
            x = bx
            for key, col, _ in theme.SPACE_PARTS:
                w = int(bw * r_[key] / big)
                if w:
                    rrect(c, col, (x, r.centery - 5, w, 10), 4)
                    x += w
            text(c, self.f.small, human_size(r_["total"]), (r.right - 20, r.centery), theme.DIM, "midright")


class FreeUpScreen(ListScreen):
    """Every game EmulationStation lists (ours or copied over), least played
    and biggest first. Always picking: A picks, X: delete the picked / pick
    enough to free N GB, Y: sort. Saves and states are never touched."""
    ROWS, ROW_H = 9, 56
    SORTS = (("unplayed", "Least played first"), ("size", "Biggest first"), ("name", "Name"))
    PICKS, PICK_ALWAYS, PICK_ALL, PICK_WHAT = True, True, False, "games"
    JUMP, JUMP_OFF = "A–Z", "A–Z jump works when sorted by name ({Y})"

    def __init__(self, app, systems=None):
        super().__init__(app)
        self.systems = systems
        self.items = None
        self.sort = "unplayed"
        self.sel = self.scroll = 0
        self.busy = False
        self.load()

    def load(self):
        def scan():
            games = rommlib.es_game_list(self.systems)
            for g in games:
                g["size"] = sum(rommlib.path_size(p) for p in rommlib.game_files(g["path"]))
            return [g for g in games if g["size"]]
        self.load_error = None
        self.app.bg(scan, self._loaded, self.load_failed("items"))

    def _loaded(self, items):
        self.items = items
        keep = {g["path"]: g for g in items if g["path"] in self.picks}
        self.picks.clear()
        self.picks.update(keep)
        self.resort()

    def resort(self):
        if self.items is None:
            return
        if self.sort == "size":
            self.items.sort(key=lambda g: -g["size"])
        elif self.sort == "name":
            self.items.sort(key=lambda g: g["name"].lower())
        else:                       # never played first, then longest ago; bigger first within
            self.items.sort(key=lambda g: (g["lastplayed"] or "0", g["playcount"], -g["size"]))
        self.clamp()

    def crumbs(self):
        return self.trail("Free up space" + (" · " + ", ".join(self.systems) if self.systems else ""))

    def hints(self):
        if not self.items:
            return [("B", "Back")]
        return self.list_hints(("Y", dict(self.SORTS)[self.sort]))

    def jump_label(self, g):
        return letter_of(g["name"]) if self.sort == "name" else None

    def suggest(self, gb):
        """Pick the least-played, biggest games until about gb GB would be freed."""
        order = sorted(self.items, key=lambda g: (g["lastplayed"] or "0", g["playcount"], -g["size"]))
        self.picks.clear()
        tot = 0
        for g in order:
            if tot >= gb << 30:
                break
            self.picks[g["path"]] = g
            tot += g["size"]
        self.app.toast("Picked %d games (%s) — check the list, then {X} to delete them"
                       % (len(self.picks), human_size(tot)))

    def delete_marked(self):
        app, todo = self.app, self.picked()
        size = sum(g["size"] for g in todo)

        def run():
            freed = 0
            for g in todo:
                freed += rommlib.delete_game(g["system"], g["path"], app.library)[1]
            return freed

        def done(freed):
            self.busy = False
            self.picks.clear()
            app.toast("Deleted %d games — %s freed. Saves are kept." % (len(todo), human_size(freed)), theme.GREEN)
            app.space_freed()
            self.load()
        app.modal = Confirm(app, "Delete %d games?" % len(todo),
                            "Frees about %s. Their files go (playlist discs and tracks too); saves, states and "
                            "screenshots are kept. EmulationStation drops them from its lists when you leave "
                            "RomM-o-Cera." % human_size(size), "Delete %d" % len(todo),
                            lambda: (setattr(self, "busy", True), app.bg(run, done)), danger=True)

    def pick_entries(self, games):
        return [("Delete %d picked" % len(games), "Frees about %s" % human_size(sum(g["size"] for g in games)),
                 self.delete_marked)]

    def list_actions(self):
        return [("Pick enough to free %d GB" % gb, "Least played, biggest first", lambda gb=gb: self.suggest(gb))
                for gb in (10, 20, 50, 100)]

    def list_controls(self):
        return [("Sort", "", self.cycle_sort, dict(self.SORTS)[self.sort], "choice")]

    def menu_title(self):
        n = self.pick_count()
        return "Free up space" + (" · %d picked" % n if n else "")

    def cycle_sort(self, step=1):
        keys = [k for k, _ in self.SORTS]
        self.sort = keys[(keys.index(self.sort) + step) % len(keys)]
        self.resort()

    def action(self, a):
        if not self.items or self.busy:
            return super().action(a)
        if self.nav(a) or self.pick_action(a):
            return
        if a == "y":
            self.cycle_sort()
        else:
            super().action(a)

    def draw(self, c):
        if self.items is None or self.busy:
            text(c, self.f.h2, "Deleting…" if self.busy else "Measuring games…", (W // 2, H // 2), theme.DIM, "center")
            return
        if self.draw_load_error(c):
            return
        free = rommlib.free_bytes()
        picked = self.picked()
        n, size = len(picked), sum(g["size"] for g in picked)
        head = "%d games · %s free" % (len(self.items), human_size(free))
        if not self.items:
            text(c, self.f.h3, head, (56, 86), theme.TEXT)
            text(c, self.f.body, "No games found.", (56, 130), theme.DIM)
            return
        row = lambda g: (g["name"], "%s · %s" % (g["system"], _lastplayed_label(g["lastplayed"])),  # noqa: E731
                         human_size(g["size"]))
        lst, pan = split(need_width(self.f, [row(g) for g in self.items[:200]], mark=True))
        self.BUBBLE_AT = bubble_at(lst)
        list_head(c, self.f, lst, head, dict(self.SORTS)[self.sort])
        for i, r, on in self.row_rects(ROWS_TOP, x=lst.left, w=lst.width):
            g = self.items[i]
            list_row(c, self.f, r, on, *row(g), mark=lambda ctr, g=g: self.draw_pick(c, g, ctr), slot="freeup")
        g = self.items[self.sel]
        p = Panel(c, self.f, pan, app=self.app, key=self.sel, slot="freeup-panel")
        p.foot("%d picked: frees %s" % (n, human_size(size)) if n else "Nothing picked yet",
               theme.TEXT if n else theme.FAINT)
        p.actions(hint_actions(self))
        p.title(g["name"], "roms/" + g["system"])
        p.fact("Size", human_size(g["size"]))
        p.fact("Last played", _lastplayed_label(g["lastplayed"]).replace("played ", "") if g["lastplayed"] else
               "Never")
        p.fact("Times played", g["playcount"] or "")
        p.prose("Deleting it takes its files (every disc and track); saves, states and screenshots stay.",
                color=theme.FAINT, lines=3)


class LeftoversScreen(ListScreen):
    """Partial files and empty folders that interrupted downloads or
    conversions left behind, and copies of games already here converted (a
    .7z beside its .rvz). Partial downloads still queued are kept. A deletes
    one; the X menu deletes the picked ones or all of them (Y)."""
    ROWS, ROW_H = 9, 56
    PICKS, PICK_WHAT, JUMP = True, "leftovers", "System"

    def __init__(self, app):
        super().__init__(app)
        self.items = None
        self.sel = self.scroll = 0
        self.load()

    def load(self):
        pend = pending_roms(self.app)
        self.load_error = None
        self.app.bg(lambda: rommlib.find_leftovers(pend), self._loaded, self.load_failed("items"))

    def _loaded(self, items):
        self.items = items
        self.clamp()

    def crumbs(self):
        return self.trail("Leftovers")

    def hints(self):
        if not self.items:
            return [("B", "Back")]
        return self.list_hints(("A", "Delete"), ("X", "Menu"), ("Y", "Delete all"))

    def jump_label(self, it):
        """Grouped by system: roms/<system>/…, then the app's own folder."""
        p = it["path"]
        if p.startswith(rommlib.ROMS_ROOT + "/"):
            return os.path.relpath(p, rommlib.ROMS_ROOT).split(os.sep)[0]
        return "other"

    def list_actions(self):
        return [("Delete all %d" % len(self.items), "%s, after you confirm" % human_size(
            sum(i["size"] for i in self.items)), self.ask_delete_all)]

    def pick_entries(self, items):
        return [("Delete %d picked" % len(items), "%s, after you confirm" % human_size(sum(i["size"] for i in items)),
                 lambda: setattr(self.app, "modal", Confirm(
                     self.app, "Delete %s?" % plural(len(items), "leftover"),
                     "Frees %s. Partial downloads that are still queued or paused are not listed and are kept."
                     % human_size(sum(i["size"] for i in items)), "Delete %d" % len(items),
                     lambda: self.remove(items), danger=True)))]

    def ask_delete_all(self):
        tot = sum(i["size"] for i in self.items)
        copies = sum(1 for i in self.items if i.get("note"))
        self.app.modal = Confirm(self.app, "Delete all %d leftovers?" % len(self.items),
                                 "Frees %s.%s Partial downloads that are still queued or paused are not "
                                 "listed and are kept." % (human_size(tot), (
                                     " Includes %s of games already here converted; the converted ones "
                                     "stay." % plural(copies, "copy", "copies")) if copies else ""),
                                 "Delete all",
                                 lambda: self.remove(list(self.items)), danger=True)

    def remove(self, items):
        app = self.app

        def done(freed):
            self.picks.clear()
            self.picking = False
            app.toast("Cleaned up %d — %s freed" % (len(items), human_size(freed)), theme.GREEN)
            app.space_freed()
            self.load()
        app.bg(lambda: rommlib.delete_leftovers(items), done)

    def action(self, a):
        if not self.items:
            return super().action(a)
        if self.nav(a) or self.pick_action(a):
            return
        if a == "ok":
            it = self.items[self.sel]
            self.app.modal = Confirm(self.app, "Delete this %s?" % it["kind"].lower(),
                                     "%s (%s)" % (it["path"], human_size(it["size"])), "Delete",
                                     lambda: self.remove([it]), danger=True)
        elif a == "y":
            self.ask_delete_all()
        else:
            super().action(a)

    def draw(self, c):
        if self.items is None:
            text(c, self.f.h2, "Looking for leftovers…", (W // 2, H // 2), theme.DIM, "center")
            return
        if self.draw_load_error(c):
            return
        if not self.items:
            text(c, self.f.h3, "Nothing to clean up.", (56, 86), theme.GREEN)
            scroll_text(c, self.f.body, "No partial files, empty game folders or copies of converted games. "
                        "Partial downloads still in the queue are kept so they can resume.",
                        (56, 124, W - 112, 3 * 26), theme.DIM, bg=theme.BG, line_h=26, slot="leftovers-none")
            return
        tot = sum(i["size"] for i in self.items)
        row = lambda it: (os.path.basename(self.where(it)), it["kind"] + " · " + (  # noqa: E731
            os.path.dirname(self.where(it)) or "/"), human_size(it["size"]) if it["size"] else "")
        lst, pan = split(need_width(self.f, [row(it) for it in self.items[:200]], mark=self.showing_picks()))
        self.BUBBLE_AT = bubble_at(lst)
        list_head(c, self.f, lst, plural(len(self.items), "leftover"), "%s in all" % human_size(tot))
        for i, r, on in self.row_rects(ROWS_TOP, x=lst.left, w=lst.width):
            it = self.items[i]
            list_row(c, self.f, r, on, *row(it), mark=lambda ctr, it=it: self.draw_pick(c, it, ctr), slot="leftover")
        it = self.items[self.sel]
        p = Panel(c, self.f, pan, app=self.app, key=self.sel, slot="leftover-panel")
        p.foot("{Y} deletes all %d: frees %s" % (len(self.items), human_size(tot)))
        p.title(it["kind"], human_size(it["size"]) if it["size"] else "Takes no space")
        p.prose(self.where(it), label="Where", lines=2)
        why, gone = self.WHY.get(it["kind"], ("", "Deleting it frees the space."))
        p.prose((it["note"] + ". " if it.get("note") else "") + why, label="Why it's here", lines=4)
        p.prose(gone, label="If you delete it", lines=3)

    WHY = {"Partial download": ("A download that stopped before it finished: the connection dropped, or it was "
                                "cancelled. Downloads still queued or paused aren't listed here.",
                                "It frees the space. Downloading the game again starts over."),
           "Empty folder": ("A game folder with nothing left in it.", "Nothing you can play changes."),
           "Copy of a converted game": ("The original of a game that's here converted too. The converted one is "
                                        "what EmulationStation runs.",
                                        "It frees the space; the converted game stays and plays as before."),
           "Unfinished conversion": ("A conversion that was stopped before it finished. The original it came from "
                                     "is still here.", "It frees the space; the original stays."),
           "Unfinished CHD": ("A CHD that was stopped before it finished. The original it came from is still here.",
                              "It frees the space; the original stays."),
           "Temporary file": ("A temporary file a download or an unpack left behind.", "It frees the space."),
           "RomM-o-Cera scratch": ("Scratch space an interrupted download or unpack left behind.",
                                   "It frees the space.")}

    @staticmethod
    def where(it):
        """Its path from roms/ (or the app's folder)."""
        p = it["path"]
        for root in (rommlib.ROMS_ROOT, rommlib.DATA_DIR):
            if p.startswith(root + "/"):
                return os.path.relpath(p, os.path.dirname(root))
        return p
