"""Collections (RomM's own and the virtual ones: genre, franchise, ...)."""
import re

import pygame
import rommlib
from rbui import pads, theme
from rbui.theme import H, W
from rbui.draw import focus_box, hline, installed_mark, measure, rrect, text, wrap
from rbui.screens.base import letter_of, ListScreen
from rbui.screens.games import GameListScreen, surprise
from rbui.modals import ActionMenu


KNOWN_KINDS = re.compile(r"^(top\s*\d+|theme|series)$", re.I)
VIRTUAL_TYPES = [("genre", "Genre"), ("franchise", "Franchise"), ("collection", "Series"),
                 ("company", "Company"), ("mode", "Game mode")]


def collection_kinds(names):
    """Prefixes ("Theme" in "Theme: Hidden Gems") that are real categories:
    a known word, or shared by 3+ collections. Anything else stays part of
    the name, so "Lorem: Best Of" isn't split into a tag."""
    counts = {}
    for n in names:
        if ":" in n:
            k = n.split(":", 1)[0].strip()
            counts[k.lower()] = counts.get(k.lower(), 0) + 1
    return {k for k, c in counts.items() if c >= 3 or KNOWN_KINDS.match(k)}


class CollectionScreen(ListScreen):
    """Top level: the user's collections (grouped by kind when the names use
    a "Kind: Name" convention), smart collections, and "Browse by" entries
    for RomM's virtual collections. With `vtype` set: one virtual type's list."""
    ROWS, ROW_H = 10, 54

    def __init__(self, app, vtype=None, vlabel=None):
        super().__init__(app)
        self.vtype, self.vlabel = vtype, vlabel
        self.items = None
        self.sel = 0
        self.scroll = 0
        self.here = {}                      # (param, id) -> games of that collection on this box
        if vtype:
            app.bg(lambda: app.api.virtual_collections(vtype), self._loaded_virtual,
                   lambda e: self._failed(e))
        else:
            app.bg(self._load_top, self._loaded_top, lambda e: self._failed(e))

    def _failed(self, e):
        self.load_failed("items")(e)

    def enter(self):
        self.here = {}                      # back from a list: downloads/deletes may have changed it

    def count_here(self, it):
        """How many of a collection's games are on this box: the same test as
        the green tick in its list (our installs, and games copied over by
        hand, matched by file name). Fetches the collection's games."""
        app, n = self.app, 0
        for r in app.api.roms_all(collection=(it["param"], it["id"])):
            pl = app.platform_by_id.get(r.get("platform_id"))
            if pl and app.library.is_installed(r, pl["_system"]):
                n += 1
        return n

    def _want_here(self, it):
        """Count a visible collection's games on the box, one at a time, in the background."""
        key = (it.get("param"), it.get("id"))
        if not it.get("id") or key in self.here or getattr(self, "_here_busy", False) \
                or not self.app.platform_by_id:
            return
        self._here_busy = True

        def done(n):
            self.here[key] = n
            self._here_busy = False

        def failed(e):
            self.here[key] = None
            self._here_busy = False
        self.app.bg(lambda: self.count_here(it), done, failed)

    def _load_top(self):
        api = self.app.api
        regular = api.collections()
        try:
            smart = api.smart_collections()
        except rommlib.RommError:
            smart = []            # older RomM without smart collections
        return regular, smart

    def _loaded_top(self, res):
        regular, smart = res
        kinds = collection_kinds([c["name"] for c in regular])
        items = []
        for c in regular:
            if not c.get("rom_count"):
                continue
            name, kind = c["name"], ""
            if ":" in name and name.split(":", 1)[0].strip().lower() in kinds:
                kind, name = [x.strip() for x in name.split(":", 1)]
            group = "Top lists" if re.match(r"top\s*\d+", kind, re.I) else \
                {"theme": "Themes", "series": "Series"}.get(kind.lower(), kind or "Your collections")
            items.append({"name": name, "tag": kind, "group": group, "count": c["rom_count"],
                          "full": c["name"], "param": "collection_id", "id": c["id"]})
        order = {"Top lists": 0, "Themes": 1, "Series": 2}
        items.sort(key=lambda i: (order.get(i["group"], 3 if i["group"] != "Your collections" else 4),
                                  i["group"].lower(), i["name"].lower()))
        for c in sorted(smart, key=lambda c: c["name"].lower()):
            if c.get("rom_count"):
                items.append({"name": c["name"], "tag": "smart", "group": "Smart", "count": c["rom_count"],
                              "full": c["name"], "param": "smart_collection_id", "id": c["id"]})
        for vt, label in VIRTUAL_TYPES:
            items.append({"name": label, "tag": "", "group": "Browse by", "count": None,
                          "full": label, "vtype": vt})
        self.items = items

    def _loaded_virtual(self, cols):
        self.items = [{"name": c["name"], "tag": "", "group": self.vlabel, "count": c.get("rom_count"),
                       "full": c["name"], "param": "virtual_collection_id", "id": c["id"]}
                      for c in sorted(cols, key=lambda c: c["name"].lower()) if c.get("rom_count")]

    def groups(self):
        return [] if not self.items else list(dict.fromkeys(i["group"] for i in self.items))

    JUMP = "A–Z"

    def jump_label(self, it):
        """Letters only in a plain alphabetical list (a Browse-by list, or one group)."""
        return letter_of(it["name"]) if self.vtype or len(self.groups()) == 1 else None

    def action(self, a):
        if not self.items:
            return super().action(a)
        if a in ("left", "right") and len(self.groups()) > 1:       # left/right: groups, not pages
            gs = self.groups()
            gi = gs.index(self.items[self.sel]["group"])
            gi = max(0, min(len(gs) - 1, gi + (1 if a == "right" else -1)))
            self.sel = next(i for i, it in enumerate(self.items) if it["group"] == gs[gi])
        elif self.nav(a):
            return
        elif a == "ok":
            it = self.items[self.sel]
            if it.get("vtype"):
                self.app.push(CollectionScreen(self.app, it["vtype"], it["name"]))
            else:
                self.app.push(GameListScreen(self.app, it["full"], collection=it))
        elif a == "x" and not self.items[self.sel].get("vtype"):
            self.menu(self.items[self.sel])
        else:
            super().action(a)

    def menu(self, it):
        app = self.app
        n = it.get("count") or 0
        e = [("Open", "", lambda: app.push(GameListScreen(app, it["full"], collection=it)))]
        if n:
            pids = [p["id"] for p in app.platforms]
            col = (it["param"], it["id"])
            e.append(("Surprise me", "One of its {:,} games, picked at random".format(n), lambda: surprise(
                app, page=lambda off, lim: app.api.roms(platform_ids=pids, collection=col, offset=off, limit=lim),
                scope={"platform_ids": pids, "collection": col}, where=it["full"])))
        if n <= GameListScreen.BULK_MAX:
            e.append(("Download every game", "{:,} games, skipping any already on this box".format(n),
                      lambda: self.download_all(it)))
        else:
            e.append(("Download every game", "{:,} is more than 1,000: open it and filter first".format(n), None))
        app.modal = ActionMenu(app, it["full"], e)

    def download_all(self, it):
        app = self.app
        app.toast("Listing %s…" % it["full"])

        def collect():
            return app.api.roms_all(limit=250, cap=GameListScreen.BULK_MAX, collection=(it["param"], it["id"]))

        def listed(roms):
            if app.modal is not None or app.stack[-1] is not self:
                app.toast("List ready — press {X} on the collection again to download it", theme.AMBER)
                return
            app.queue_many(roms, "Download %s?" % it["full"])
        app.bg(collect, listed, lambda e: app.toast(str(e), theme.RED))

    def hints(self):
        h = [("A", "Open")]
        if self.items and not self.items[self.sel].get("vtype"):
            h.append(("X", pads.ACTIONS))
        return self.list_hints(*h)

    def crumbs(self):
        if self.vtype:
            return ["Collections", self.vlabel]
        if self.items and len(self.groups()) > 1:
            return ["Collections", self.items[self.sel]["group"]]
        return ["Collections"]

    def draw(self, c):
        if self.items is None:
            text(c, self.f.h2, "Loading…", (W // 2, H // 2), theme.DIM, "center")
            return
        if self.draw_load_error(c):
            return
        if not self.items:
            text(c, self.f.h2, "No collections", (W // 2, 300), theme.DIM, "center")
            return
        rows = self.ROWS
        for i, r, on in self.row_rects(80, tap=self.tap_now):
            it = self.items[i]
            if i > self.scroll and it["group"] != self.items[i - 1]["group"]:
                hline(c, theme.LINE, (64, r.top - 2), (W - 112, r.top - 2))
            if on:
                focus_box(c, r)
            here = self.here.get((it.get("param"), it.get("id")))
            right = 150 + (24 + int(measure(self.f.small, "{:,}".format(here))) if here else 0)
            tag = it["tag"]
            if tag:
                tw = int(measure(self.f.tiny, tag)) + 14
                tr = pygame.Rect(r.right - right - tw, r.centery - 10, tw, 20)
                rrect(c, theme.BG2, tr, 6)
                text(c, self.f.tiny, tag, tr.center, theme.FAINT, "center")
                right += tw + 16
            # full name: one line if it fits, else two smaller lines — never "…"
            name = it["name"]
            avail = r.width - 22 - right
            col_txt = theme.TEXT if on else theme.DIM
            if measure(self.f.row, name) <= avail:
                text(c, self.f.row, name, (r.left + 22, r.centery), col_txt, "midleft")
            else:
                lines = wrap(self.f.small, name, avail)
                fnt, lh = (self.f.small, 19) if len(lines) <= 2 else (self.f.tiny, 15)
                if len(lines) > 2:
                    lines = wrap(fnt, name, avail)
                y0 = r.centery - lh * len(lines) // 2
                for k, ln in enumerate(lines):
                    text(c, fnt, ln, (r.left + 22, y0 + k * lh), col_txt)
            cnt = ("{:,} game{}".format(it["count"], "" if it["count"] == 1 else "s")
                   if it["count"] is not None else "›")
            cr = text(c, self.f.small, cnt, (r.right - 20, r.centery), theme.FAINT, "midright")
            self._want_here(it)
            if here:                                   # like the platform cards: green tick + count
                hr = text(c, self.f.small, "{:,}".format(here), (cr.left - 14, r.centery), theme.GREEN, "midright")
                installed_mark(c, (hr.left - 11, r.centery), 7)
        n = len(self.items)
        if n > rows:
            track = pygame.Rect(W - 30, 80, 4, rows * self.ROW_H - 4)
            rrect(c, theme.LINE, track, 2)
            h = max(24, track.height * rows // n)
            yy = track.top + int((track.height - h) * (self.sel / max(1, n - 1)))
            rrect(c, theme.ACCENT, (track.left, yy, 4, h), 2)
