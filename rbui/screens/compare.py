"""Compare with RomM: the games of one platform or collection that are on this box, each against
RomM's copy (opened from that platform's or collection's game list, never library-wide)."""
import os
import threading
import time

import pygame
import rommlib
from rommlib import human_size
from rbui import pads, theme
from rbui.draw import circle, installed_mark, text
from rbui.modals import DownloadConfirm
from rbui.screens.base import letter_of, ListScreen
from rbui.widgets.chips import pill_row
from rbui.widgets.hero import time_ago
from rbui.widgets.layout import CONTENT, Panel, list_row, need_width, split

TOP = 134                                   # the rows' y, under the filter pills
AREA = pygame.Rect(CONTENT.left, TOP, CONTENT.width, CONTENT.bottom - TOP)
# (key, pill label, statuses it shows)
FILTERS = [("all", "All", None),
           ("matches", "Matches RomM", (rommlib.MATCHES, rommlib.COMPRESSED)),
           ("newer", "Newer in RomM", (rommlib.NEWER,)),
           ("not_in_romm", "Not in RomM", (rommlib.NOT_IN_ROMM,))]


ROW_LABEL = {rommlib.COMPRESSED: "Matches (compressed)"}      # a row's status, short enough for narrow lists


def row_label(it):
    return ROW_LABEL.get(it["status"], it["label"])


def state_color(status):
    """A compare status's colour (theme tokens, read when drawn)."""
    return {rommlib.MATCHES: theme.GREEN, rommlib.COMPRESSED: theme.GREEN,
            rommlib.NEWER: theme.BLUE}.get(status, theme.DIM if status else theme.TEXT)


def ext_of(path):
    """A file's format as people say it (CHD, ZIP); a folder's "Folder"."""
    e = os.path.splitext(path or "")[1].lstrip(".")
    return e.upper() if e and len(e) <= 5 else "Folder"


def rom_ext(rom):
    """RomM's file format (ZIP), or "Folder" for a game of several files."""
    e = rom.get("fs_extension") or os.path.splitext(rom.get("fs_name") or "")[1].lstrip(".")
    return e.upper() if e and len(e) <= 5 else "Folder"


def display_name(it):
    """RomM's name for a game it has, else the file's name without its extension."""
    rom = it.get("rom")
    if rom and rom.get("name"):
        return rom["name"]
    n = it["name"]
    return os.path.splitext(n)[0] if it.get("size") is not None else n


def system_of(it):
    """The roms/<system> folder an item is in."""
    rel = os.path.relpath(it["path"], rommlib.ROMS_ROOT)
    return rel.split(os.sep)[0]


def say(it, recorded=False):
    """The one plain sentence about an item."""
    st, rom = it["status"], it.get("rom") or {}
    if st == rommlib.MATCHES:
        return "The same file RomM has. Nothing to do."
    if st == rommlib.COMPRESSED:
        fmt = rom_ext(rom)
        return "RomM's %s, compressed here to %s to save space. The same game: nothing to do." % (
            "files" if fmt == "Folder" else fmt, ext_of(it["path"]))
    if st == rommlib.NEWER:
        return ("RomM's copy changed since this one was downloaded." if recorded else
                "RomM has a different copy under the same name.") + " Getting it replaces the file here; saves are kept."
    return "RomM has no game by this name: it was put here another way. It still plays."


class CompareScreen(ListScreen):
    """A platform's or collection's games on this box against RomM, filtered by state with the
    pills (Y), the selected one's two copies side by side. The comparison runs in the
    background; B while it runs stops it."""
    ROWS, ROW_H = 9, 54
    JUMP = "A–Z"
    BUBBLE_AT = (AREA.left + AREA.width // 4, AREA.centery)

    def __init__(self, app, title, platform=None, collection=None):
        super().__init__(app)
        self.title, self.platform, self.collection = title, platform, collection
        self.filt = "all"
        self.result = None
        self.when = None
        self.cancel = threading.Event()
        self._view = None                   # (result id, filter) -> rows, cached: 10,000 rows filter once
        self._need = None
        self._added = {}                    # path -> mtime, for the selected rows only
        self.sel = self.scroll = 0
        self.start()

    # -- the comparison
    def start(self):
        app = self.app
        self.cancel.set()
        self.cancel = cancel = threading.Event()
        self.result, self.load_error, self._view, self._need = None, None, None, None
        if self.platform:
            system = self.platform["_system"]
            pids = [p["id"] for p in app.platforms if p.get("_system") == system] or [self.platform["id"]]

            def run():
                return rommlib.compare_platform(app.api, app.library, system, pids, cancel)
        else:
            col = (self.collection["param"], self.collection["id"])

            def run():
                return rommlib.compare_collection(app.api, app.library, col, app.system_for, cancel)

        def done(res):
            if cancel is self.cancel and res is not None:
                self.result, self.when = res, time.time()
                self.clamp(scroll=True)

        def failed(e):
            if cancel is self.cancel:
                self.load_error = str(e) or e.__class__.__name__
                self.result = {"items": [], "counts": {}}
                app.toast(self.load_error, theme.RED)
        app.bg(run, done, failed)

    @property
    def comparing(self):
        return self.result is None

    def filters(self):
        """The pills: a collection has no "Not in RomM" (games outside it aren't listed)."""
        return [f for f in FILTERS if f[0] != "not_in_romm" or not self.collection]

    def count(self, statuses):
        counts = (self.result or {}).get("counts") or {}
        if statuses is None:
            return len((self.result or {}).get("items") or [])
        return sum(counts.get(s, 0) for s in statuses)

    def listed(self):
        if not self.result:
            return []
        key = (id(self.result), self.filt)
        if not self._view or self._view[0] != key:
            want = dict((k, s) for k, _, s in FILTERS)[self.filt]
            items = self.result["items"]
            self._view = (key, items if want is None else [it for it in items if it["status"] in want])
        return self._view[1]

    def current(self):
        rows = self.listed()
        return rows[self.sel] if 0 <= self.sel < len(rows) else None

    def set_filter(self, i):
        self.filt = self.filters()[i][0]
        self.sel = self.scroll = 0

    def cycle_filter(self, step=1):
        keys = [k for k, _, _ in self.filters()]
        self.set_filter((keys.index(self.filt) + step) % len(keys))

    def jump_label(self, it):
        return letter_of(display_name(it))

    # -- input
    def crumbs(self):
        return ["Platforms" if self.platform else "Collections", self.title, "Compare with RomM"]

    def hints(self):
        if self.comparing:
            return [("B", "Stop")]
        it = self.current()
        own = [("A", "Get the newer one")] if it and it["status"] == rommlib.NEWER else []
        return self.list_hints(*own, ("X", pads.ACTIONS), ("Y", "Filter"))

    def action(self, a):
        if a in ("back", "quit"):
            self.cancel.set()
            return super().action(a)
        if self.comparing:
            return
        if self.nav(a):
            return
        if a == "y":
            self.cycle_filter()
        elif a == "x":
            self.open_menu()
        elif a == "ok":
            it = self.current()
            if it and it["status"] == rommlib.NEWER:
                self.get_newer(it)
        else:
            super().action(a)

    def menu_title(self):
        return "Compare with RomM"

    def list_actions(self):
        return [("Compare again", "Read RomM's list for %s again" % self.title, self.start)]

    def list_controls(self):
        return [("Show", "", self.cycle_filter, dict((k, lab) for k, lab, _ in FILTERS)[self.filt], "choice")]

    def get_newer(self, it):
        """RomM's whole record, then the usual download-again dialog; the files here are replaced
        once the new one is in (a .chd made from RomM's old zip too; the new files are kept)."""
        app, rom = self.app, it["rom"]

        def got(full):
            if app.stack[-1] is not self or app.modal is not None:
                return
            app.modal = DownloadConfirm(app, full, replace=list(it["paths"]))
        app.toast("Asking RomM for %s…" % display_name(it))
        app.bg(lambda: app.api.rom(rom["id"]), got, lambda e: app.toast(str(e), theme.RED))

    # -- drawing
    def added(self, it):
        p = it["path"]
        if p not in self._added:
            if len(self._added) > 200:
                self._added.clear()
            try:
                self._added[p] = os.path.getmtime(p)
            except OSError:
                self._added[p] = None
        return self._added[p]

    def recorded(self, it):
        """The app downloaded this one (its install record), for what the panel says."""
        rom = it.get("rom")
        if not rom or it["status"] != rommlib.NEWER:
            return False
        if "_rec" not in it:
            it["_rec"] = str(rom.get("id")) in self.app.library.snapshot()
        return it["_rec"]

    def sub(self, it):
        size = human_size(it["size"]) if it.get("size") is not None else ""
        return " · ".join(x for x in ("roms/" + system_of(it), ext_of(it["path"]).lower()
                                      if it.get("size") is not None else "folder", size) if x)

    def queued(self, it):
        rom = it.get("rom")
        j = rom and self.app.downloader.job_for(rom["id"])
        return "downloading" if j and j.active else None

    def draw_pills(self, c):
        fl = self.filters()
        labels = [lab if self.comparing else "%s · %s" % (lab, "{:,}".format(self.count(st)))
                  for _, lab, st in fl]
        dots = [state_color(st[0] if st else None) for _, _, st in fl]
        idx = [k for k, _, _ in fl].index(self.filt)
        pill_row(c, self.f.body, labels, CONTENT.left, CONTENT.right, 84, focus=idx, dots=dots,
                 app=self.app, on_tap=None if self.comparing else self.set_filter)

    def draw(self, c):
        f = self.f
        self.draw_pills(c)
        if self.comparing:
            p = Panel(c, f, AREA, self.app, key="comparing", slot="cmp-wait")
            p.title("Comparing with RomM…", "Reading RomM's list for %s" % self.title)
            p.prose("Each game here is checked against RomM's copy: the same file, the same game compressed "
                    "here, a newer copy in RomM, or a game RomM doesn't have. {B} stops.", lines=3)
            return
        if self.draw_load_error(c, TOP):
            return
        rows = self.listed()
        if not self.result["items"]:
            p = Panel(c, f, AREA, self.app, key="none", slot="cmp-none")
            p.title("None of these games are on this box" if self.collection else
                    "No %s games on this box" % self.title)
            p.prose("Download a few and compare again from the {X} menu.", lines=2)
            return
        if self._need is None or self._need[0] != id(self.result):     # one width for every filter
            self._need = (id(self.result), need_width(f, [(display_name(it), self.sub(it), row_label(it))
                                                          for it in self.result["items"][:200]], mark=True))
        lst, pan = split(self._need[1], content=AREA)
        if not rows:
            lab = dict((k, lab) for k, lab, _ in FILTERS)[self.filt]
            text(c, f.body, "None here: %s." % lab.lower().replace("romm", "RomM"), (lst.left + 8, TOP + 12),
                 theme.FAINT, maxw=lst.width - 16)
        for i, r, on in self.row_rects(TOP, x=lst.left, w=lst.width):
            it = rows[i]
            col = state_color(it["status"])
            ok = it["status"] in (rommlib.MATCHES, rommlib.COMPRESSED)

            def mark(ctr, col=col, ok=ok):
                if ok:
                    installed_mark(c, ctr, 8)
                else:
                    circle(c, col, ctr, 6)
                return True
            busy = self.queued(it)
            list_row(c, f, r, on, display_name(it), self.sub(it), row_label(it), col, mark=mark, slot="cmp",
                     tag=busy)
        self.draw_scrollbar(c, lst.right + 6, TOP)
        n = len(self.result["items"])
        ago = int((time.time() - self.when) // 60) if self.when else 0
        text(c, f.small, "{:,} game{} here · compared with RomM {}".format(
            n, "" if n == 1 else "s", "just now" if ago < 1 else "%d min ago" % ago if ago < 60 else "a while ago"),
            (lst.left + 8, TOP + self.ROWS * self.ROW_H + 6), theme.FAINT, maxw=lst.width - 16)
        self.draw_bubble(c)
        it = self.current()
        if it is None:
            Panel(c, f, pan, self.app, slot="cmp-panel")
            return
        self.draw_panel(c, pan, it)

    def draw_panel(self, c, pan, it):
        app, rom = self.app, it.get("rom")
        p = Panel(c, self.f, pan, app, key=it["path"], slot="cmp-panel")
        if it["status"] == rommlib.NEWER:
            p.verb("Get the newer one", "go", human_size(rom.get("fs_size_bytes")) if rom else "",
                   fn=lambda: self.action("ok"))
        p.art(rom, name=display_name(it))
        p.title(display_name(it), lines=3, small=True)
        p.status(it["label"], state_color(it["status"]))
        p.under_art()
        here = ext_of(it["path"]) + (" · " + human_size(it["size"]) if it.get("size") is not None else "")
        added = time_ago(self.added(it)) or "not known"
        if rom:
            pairs = [("Here", here), ("In RomM", "%s · %s" % (rom_ext(rom), human_size(rom.get("fs_size_bytes")))),
                     ("Folder", "roms/" + system_of(it)), ("RomM's file", rom.get("fs_name") or "not known"),
                     ("Added here", added),
                     ("Updated in RomM", time_ago(rommlib.utc_seconds(rom.get("updated_at"))) or "not known")]
        else:
            pairs = [("Here", here), ("Folder", "roms/" + system_of(it)), ("Added here", added)]
        p.facts(pairs, cols=2, lines=4, strong=("Here", "In RomM"))
        p.prose(say(it, self.recorded(it)))


def compare_entry(app, title, platform=None, collection=None):
    """The "Compare with RomM" row of a platform's or collection's Actions menu."""
    return ("Compare with RomM", "Which games here match RomM's, which RomM has newer",
            lambda: app.push(CompareScreen(app, title, platform=platform, collection=collection)))
