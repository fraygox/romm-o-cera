"""Backlog: the games you marked Backlog in RomM, in RomM's order, and what Download my backlog
would fetch: what fits in the space left now and what waits for room."""
import rommlib
from rommlib import human_size
from rbui import pads, theme
from rbui.theme import H, W
from rbui.draw import scroll_text, text
from rbui.screens.base import ListScreen
from rbui.screens.space import FreeUpScreen
from rbui.modals import ActionMenu
from rbui.widgets.hero import meta_line, press_verb
from rbui.widgets.layout import Panel, ROWS_TOP, bubble_at, list_head, list_row, need_width, split
from rbui.widgets.tiles import cover

THUMB = 40                  # the cover thumbnail's width in a row, for need_width


def backlog_plan(app, roms, fit):
    """backlog_fit's answer made into what the screen shows: {"fit": ids, "wait": ids, "todo":
    [roms] in RomM's order, "fit_bytes", "wait_bytes", "free", "here": games on the box,
    "platforms": [(name, games, bytes)] biggest first}."""
    fit_ids = {r["id"] for r, _ in fit["fit"]}
    wait_ids = {r["id"] for r, _ in fit["wait"]}
    todo = [r for r in roms if r["id"] in fit_ids or r["id"] in wait_ids]
    size = lambda r: r.get("fs_size_bytes") or 0          # noqa: E731
    per = {}
    for r in todo:
        pl = app.platform_by_id.get(r.get("platform_id")) or {}
        k = pl.get("display_name") or pl.get("name") or "?"
        n, b = per.get(k, (0, 0))
        per[k] = (n + 1, b + size(r))
    return {"fit": fit_ids, "wait": wait_ids, "todo": todo, "free": fit["free"],
            "fit_bytes": sum(size(r) for r in todo if r["id"] in fit_ids),
            "wait_bytes": sum(size(r) for r in todo if r["id"] in wait_ids),
            "here": sum(1 for r in roms if app.rom_status(r)[0] == "installed"),
            "platforms": sorted(((k, n, b) for k, (n, b) in per.items()), key=lambda x: (-x[2], x[0]))}


class BacklogScreen(ListScreen):
    """A plays or downloads the game, Y downloads the whole backlog (what fits first, the rest
    waits for room), X: the actions (Free up space among them)."""
    ROWS, ROW_H = 9, 56
    PAGE = 100
    CAP = 1000                  # as many as one download-every-game takes
    BULK_MAX = 1000

    def __init__(self, app):
        super().__init__(app)
        self.gen = 0
        self.load()

    def crumbs(self):
        return self.trail("Backlog")

    def enter(self):
        self.plan_sig = None                        # back from Free up space: the room changed

    # -- loading: RomM's pages, then the fit
    def load(self):
        self.gen += 1
        gen, app = self.gen, self.app
        self.items, self.total, self.filtered, self.loading = [], None, False, True
        self.load_error, self.plan, self.plan_sig = None, None, None
        self.sel = self.scroll = 0
        seen = set()

        def fetch(off):
            app.bg(lambda: app.api.user_list("backlog", offset=off, limit=self.PAGE),
                   lambda got: landed(off, got), failed)

        def landed(off, got):
            if gen != self.gen:
                return
            for r in got.get("items") or []:
                if r and r.get("id") not in seen and len(self.items) < self.CAP:
                    seen.add(r["id"])
                    self.items.append(r)
            self.total = got.get("total") or 0
            self.filtered = self.filtered or got.get("filtered")
            if off + self.PAGE < self.total and len(self.items) < self.CAP:
                fetch(off + self.PAGE)
            else:
                self.loading = False

        def failed(e):
            if gen == self.gen:
                self.loading = False
                self.load_failed("items")(e)
        fetch(0)

    def queue_sig(self):
        d = self.app.downloader
        return (len(d.jobs), d.active_count())

    def replan(self):
        """What fits, worked out again (the queue or the space changed): exact sizes from
        backlog_fit_listed, against the space left once the queue has landed."""
        app, gen = self.app, self.gen
        self.plan_sig = self.queue_sig()
        roms = list(self.items)
        items = [(r, app.system_for(r)) for r in roms]

        def on_box(r, system):
            j = app.downloader.job_for(r["id"])
            return bool(j and j.active) or app.library.is_installed(r, system)
        free = app.free_after_queue()

        def done(fit):
            if gen == self.gen and fit is not None:
                self.plan = backlog_plan(app, roms, fit)

        def err(e):
            if gen == self.gen:
                app.toast(str(e), theme.RED)
        app.bg(lambda: rommlib.backlog_fit_listed(app.api, items, app.cfg, free=free, on_box=on_box), done, err)

    # -- input
    def current(self):
        return self.items[self.sel] if self.items and self.sel < len(self.items) else None

    def verb_label(self, rom):
        st = self.app.rom_status(rom)[0]
        if not self.app.system_for(rom) or st == "active":
            return None
        return "Play now" if st == "installed" else "Download"

    def download_all(self):
        p = self.plan
        if not p or not p["todo"]:
            self.app.toast("Every game on your backlog is on this box or queued", theme.AMBER)
            return
        self.app.queue_many(p["todo"][:self.BULK_MAX], "Download my backlog?")

    def menu_entries(self):
        app, rom, p = self.app, self.current(), self.plan
        e = []
        if rom is not None and self.verb_label(rom):
            e.append((self.verb_label(rom), rom.get("name") or "", lambda: press_verb(app, rom)))
        if p is None:
            e.append(("Download my backlog", "Working out what fits…", None))
        elif p["todo"]:
            e.append(("Download my backlog", "%d games, %s" % (len(p["todo"]), human_size(
                p["fit_bytes"] + p["wait_bytes"])), self.download_all))
        e.append(("Free up space", "Pick games on this box to delete", lambda: app.push(FreeUpScreen(app))))
        e.append(("Reload from RomM", "", self.load))
        return e

    def hints(self):
        rom = self.current()
        h = [("A", self.verb_label(rom))] if rom is not None and self.verb_label(rom) else []
        h.append(("X", pads.ACTIONS))
        if self.plan and self.plan["todo"]:
            h.append(("Y", "Download my backlog"))
        return self.list_hints(*h)

    def action(self, a):
        rom = self.current()
        if self.nav(a):
            return
        if a == "ok" and rom is not None:
            press_verb(self.app, rom)
        elif a == "x":
            self.app.modal = ActionMenu(self.app, "Backlog", self.menu_entries())
        elif a == "y":
            self.download_all()
        else:
            super().action(a)

    # -- drawing
    def row(self, rom):
        """(label, sub, status, colour, tick) for a row."""
        app, p = self.app, self.plan
        size = human_size(rom.get("fs_size_bytes"))
        if not app.system_for(rom):
            return rom.get("name") or "", meta_line(app, rom), "No Batocera system", theme.FAINT, False
        st = app.rom_status(rom)[0]
        if st == "installed":
            status, col, tick = "On this box", theme.GREEN, True
        elif st == "active":
            status, col, tick = "In the queue", theme.ACCENT_HI, False
        elif p and rom["id"] in p["wait"]:
            status, col, tick = "Waits for space · " + size, theme.AMBER, False
        else:
            status, col, tick = size, theme.DIM, False
        return rom.get("name") or rom.get("fs_name_no_ext") or "", meta_line(app, rom), status, col, tick

    def draw(self, c):
        app, f = self.app, self.f
        if self.draw_load_error(c):
            return
        if not self.items:
            text(c, f.h2, "Loading your backlog…" if self.loading else "Nothing on your backlog", (W // 2, H // 2 - 20),
                 theme.DIM, "center")
            if not self.loading:
                text(c, f.body, "Mark games Backlog in RomM and they show up here.", (W // 2, H // 2 + 20),
                     theme.FAINT, "center")
            return
        if not self.loading and self.plan_sig != self.queue_sig():
            self.replan()
        rows = [self.row(r) for r in self.items[:200]]
        lst, pan = split(need_width(f, [r[:3] for r in rows]) + THUMB)
        self.BUBBLE_AT = bubble_at(lst)
        p = self.plan
        n = len(self.items)
        note = "{:,} in RomM".format(self.total if not self.filtered else n)
        if p:
            note += " · {:,} on this box".format(p["here"])
        list_head(c, f, lst, "Backlog", note)
        for i, r, on in self.row_rects(ROWS_TOP, x=lst.left, w=lst.width):
            rom = self.items[i]
            label, sub, status, col, tick = rows[i] if i < len(rows) else self.row(rom)
            list_row(c, f, r, on, label, sub, status, col, mark=None, slot="backlog", status_tick=tick,
                     thumb=lambda tr, rom=rom: cover(c, app, rom, tr, radius=4, label=False))
        self.draw_scrollbar(c, lst.right + 6, ROWS_TOP)
        self.draw_panel(c, pan)

    def draw_panel(self, c, pan):
        f, p = self.f, self.plan
        rom = self.current()
        pn = Panel(c, f, pan, app=self.app, key=self.sel, slot="backlog-panel")
        pn.title("Download my backlog", lines=1)
        if p is None:
            pn.prose("Still listing your backlog…" if self.loading else "Working out what fits…", color=theme.DIM,
                     lines=2)
            return
        todo = p["todo"]
        if not todo:
            pn.prose("Every game on your backlog is on this box already, or on its way." if self.items else "",
                     lines=3)
            return self.draw_game(pn, rom)
        total = p["fit_bytes"] + p["wait_bytes"]
        nf, nw = len(todo) - len(p["wait"]), len(p["wait"])
        pn.figure(human_size(total), "%d game%s not on this box yet" % (len(todo), "" if len(todo) == 1 else "s"))
        share = p["fit_bytes"] / total if total else (1.0 if not nw else 0.0)
        pn.bar([(share, theme.ACCENT), (1 - share, theme.AMBER)] if nw else [(1.0, theme.ACCENT)])
        keep = rommlib.keep_free_bytes(self.app.cfg) or rommlib.SPACE_MARGIN
        legend = [(theme.ACCENT, "%d %s now · %s" % (nf, "fits" if nf == 1 else "fit", human_size(p["fit_bytes"])))
                  ] if nf else []
        if nw:
            legend.append((theme.AMBER, "%d %s for space · %s" % (nw, "waits" if nw == 1 else "wait",
                                                                 human_size(p["wait_bytes"]))))
        legend.append((None, "%s free after the queue" % human_size(max(0, p["free"]))))
        legend.append((None, "Keeping %s free (Settings)" % human_size(keep)))
        pn.legend(legend)
        pn.verb("Download %d" % len(todo), "go", human_size(total), glyph="Y", fn=self.download_all, foot=False)
        if nw:
            note = ("%d start now; the other %d wait for room." % (nf, nw)) if nf else "All of them wait for room."
            note += " {X} %s has Free up space." % pads.ACTIONS
        else:
            note = "Queued in your backlog's order."
        pn.prose(note, color=theme.FAINT, lines=2, line_h=21)
        room = int((pn.room() - 96) // 24)
        for name, n, b in p["platforms"][:max(0, room)]:
            pn.pair(name, "%d · %s" % (n, human_size(b)))
        self.draw_game(pn, rom)

    def draw_game(self, pn, rom):
        if rom is None or pn.room() < 70:
            return
        pn.space(4)
        pn.rule()
        app = self.app
        pn.line(rom.get("name") or "", theme.TEXT, self.f.h3, h=28)
        st = app.rom_status(rom)[0]
        p = self.plan
        if not app.system_for(rom):
            s = "Its platform has no Batocera system, so it can't come to this box."
        elif st == "installed":
            s = "Already on this box. Playing it is a press away."
        elif st == "active":
            s = "In the download queue."
        elif p and rom["id"] in p["wait"]:
            s = "Waits for space: it starts once there's room."
        else:
            s = "Fits now: %s to download." % human_size(rom.get("fs_size_bytes"))
        scroll_text(pn.c, self.f.small, s, (pn.x, pn.y, pn.w, min(pn.room(), 42)), theme.DIM, key=rom["id"],
                    line_h=21, slot="backlog-game")
