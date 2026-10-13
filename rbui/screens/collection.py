"""Collections: shelves of cover mosaics, one shelf a kind (Top lists, Themes, Series, any other
"Prefix:" your collections share, Your collections, Smart, Browse by), the focused one in the
panel with how much of it is on this box. Y turns the focused kind into a wall and back;
opening a collection lists its games against the box (GameListScreen)."""
import re

import pygame
import rommlib
from rommlib import human_size
from rbui import draw, pads, theme
from rbui.theme import SR, W
from rbui.draw import _fade, card, measure, scrollbar, text, wrap
from rbui.modals import ActionMenu
from rbui.screens.base import Screen
from rbui.screens.compare import compare_entry
from rbui.screens.games import GameListScreen
from rbui.screens.backlog import BacklogScreen
from rbui.screens.gamenight import GameNightScreen, Scope
from rbui.widgets.layout import hint_actions, list_head, Panel, split
from rbui.widgets.tiles import collection_tile, collection_tile_h, cover_capacity, mosaic, Shelf


KNOWN_KINDS = re.compile(r"^(top\s*\d+|theme|series)$", re.I)
TOP_N = re.compile(r"^top\s*(\d+)$", re.I)
FIXED = {"top": "Top lists", "theme": "Themes", "series": "Series"}        # the shelves the known words get
VIRTUAL_TYPES = [("genre", "Genre", "genre"), ("franchise", "Franchise", "franchise"),
                 ("collection", "Series", "series"), ("company", "Company", "company"),
                 ("mode", "Game mode", "game mode")]

COLUMN, SIDE = split(702, pygame.Rect(48, 84, W - 96, 572), 24)    # the shelves (or the wall), the focused one
TILE_GAP = 14
SHELF_HEAD = 30                     # a shelf's heading over its tiles
SHELF_GAP = 12                      # under a shelf's tiles
WALL_HEAD = 32                      # the wall's heading
WALL_GAP = 4                        # the least between wall rows; the rows share the height's slack evenly
TILE_SIZES = {"small": (6, 7), "medium": (5, 6), "large": (4, 4)}   # Settings: tiles a shelf and a wall row show
FOCUS_WAIT = 0.4                    # seconds a tile keeps the focus before its games are fetched
ROMS_KEPT = 12                      # collections whose games stay in memory (the focused ones, latest last)


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


def kind_of(name, kinds):
    """(the kind as written, the rest of the name) for "Kind: Name" with a kind in kinds, else ("", name)."""
    if ":" in name:
        k, rest = name.split(":", 1)
        if k.strip().lower() in kinds:
            return k.strip(), rest.strip() or name
    return "", name


def shelf_key(kind):
    """The shelf a kind word goes on: "top" for any Top N, "theme", "series", else the word itself."""
    k = kind.lower()
    return "top" if TOP_N.match(k) else k


def item(c, param, kind="", name=None, group=""):
    """A collection record as the screens hold it: its name without the kind, the kind as written,
    the shelf's label, RomM's count and game ids, its own covers."""
    return {"full": c["name"], "name": name or c["name"], "kind": kind, "group": group,
            "count": c.get("rom_count") or 0, "param": param, "id": c["id"],
            "rom_ids": c.get("rom_ids") or [], "path_covers_small": c.get("path_covers_small") or []}


def collection_items(regular):
    """Your collections with games as items, each on its shelf (group)."""
    kinds = collection_kinds([c["name"] for c in regular])
    labels, out = {}, []
    for c in regular:
        if not c.get("rom_count"):
            continue
        kind, name = kind_of(c["name"], kinds)
        key = shelf_key(kind) if kind else "yours"
        if kind and key not in FIXED:
            labels.setdefault(key, kind[:1].upper() + kind[1:])     # the first spelling seen names the shelf
        it = item(c, "collection_id", kind, c["name"] if key == "top" else name)   # a Top list keeps its N
        it["shelf"] = key
        out.append(it)
    for it in out:
        it["group"] = FIXED.get(it["shelf"]) or labels.get(it["shelf"]) or "Your collections"
    return out


def top_n(it):
    m = TOP_N.match(it["kind"])
    return int(m.group(1)) if m else 0


def group_collections(regular, smart=()):
    """The shelves, in the fixed order: Top lists, Themes, Series, then any other "Prefix:" kind
    A–Z, Your collections, Smart, Browse by (RomM's virtual collections and your backlog). A Top
    list shelf goes by N then name, the others by name; a kind with no collections has no shelf."""
    by = {}
    for it in collection_items(regular):
        by.setdefault(it["shelf"], []).append(it)
    for c in sorted(smart, key=lambda c: c["name"].lower()):
        if c.get("rom_count"):
            by.setdefault("smart", []).append(item(c, "smart_collection_id", "Smart", group="Smart"))
    custom = sorted(k for k in by if k not in FIXED and k not in ("yours", "smart"))
    out = []
    for key in ["top", "theme", "series"] + custom + ["yours", "smart"]:
        items = by.get(key)
        if items:
            items.sort(key=lambda it: ((top_n(it), it["name"].lower()) if key == "top" else (it["name"].lower(),)))
            out.append({"key": key, "label": items[0]["group"], "items": items})
    doors = [{"full": label, "name": label, "kind": "", "group": "Browse by", "count": None, "browse": vt,
              "unit": unit, "id": vt} for vt, label, unit in VIRTUAL_TYPES]
    doors.append({"full": "Backlog", "name": "Backlog", "kind": "", "group": "Browse by", "count": None,
                  "screen": "backlog", "unit": "", "id": "backlog"})
    out.append({"key": "browse", "label": "Browse by", "items": doors})
    return out


def door(it):
    """A tile that opens another screen (Browse by, Backlog), not a collection of games."""
    return bool(it.get("browse") or it.get("screen"))


class CollectionArt:
    """What a collection's mosaic shows: the record's own covers (RomM's path_covers_small),
    or, for a record without them, four of its games fetched once when first drawn (one at a
    time). cells(coll) -> what tiles.mosaic takes. Fewer cells when covers are short of memory."""

    def __init__(self, app):
        self.app = app
        self.roms = {}              # (param, id) -> its first four games
        self.busy = False
        self.gen = app.conn_gen

    def per(self):
        return 4 if cover_capacity() >= 80 else 1

    def cells(self, coll):
        app = self.app
        if self.gen != app.conn_gen:
            self.roms, self.gen = {}, app.conn_gen
        key = (coll.get("param"), coll["id"])
        paths = rommlib.collection_cover_paths(coll)
        if paths:
            return [(("coll",) + key + (i,), app.collection_cover(coll, i)) for i in range(min(self.per(), len(paths)))]
        if key in self.roms:
            return self.roms[key][:self.per()]
        if not self.busy and app.platforms:
            self.busy = True
            pids = [p["id"] for p in app.platforms]

            def done(page):
                self.busy = False
                self.roms[key] = (page.get("items") or [])[:4]

            def failed(e):
                self.busy = False
                self.roms[key] = []
            app.bg(lambda: app.api.roms(platform_ids=pids, collection=key, limit=4), done, failed)
        return []


class CollectionScreen(Screen):
    """Top level: the shelves. With `vtype` set: one virtual type's collections as a wall."""

    def __init__(self, app, vtype=None, vlabel=None):
        super().__init__(app)
        self.vtype, self.vlabel = vtype, vlabel
        self.kinds = None               # [{key, label, items}]
        self.row = self.col = 0         # the focused shelf and the tile in it
        self.off = 0                    # the shelves' scroll, in px
        self.wall = bool(vtype)         # the focused kind alone, many a row
        self.wall_top = 0               # the wall's first row shown
        self.shelf_of = {}              # shelf key -> its Shelf (keeps its scroll)
        self.art = CollectionArt(app)
        self.box = {}                   # (param, id) -> {"here": n, "roms": its games or None, "rest": bytes}
        self.here_ids = set()           # ids of the games this app put on the box
        self._busy = None               # the collection whose games are being fetched
        self.focus_since = (None, 0.0)  # (what has the focus, since when)
        self.gen = app.conn_gen
        self.load()

    # ---------------------------------------------------------------- data

    def load(self):
        app = self.app
        self.kinds, self.load_error = None, None
        self.snapshot()
        if self.vtype:
            app.bg(lambda: app.api.virtual_collections(self.vtype), self._loaded_virtual, self.load_failed("kinds"))
        else:
            app.bg(self._load_top, self._loaded_top, self.load_failed("kinds"))

    def _load_top(self):
        api = self.app.api
        regular = api.collections()
        try:
            smart = api.smart_collections()
        except rommlib.RommError:
            smart = []            # older RomM without smart collections
        return regular, smart

    def _loaded_top(self, res):
        self.kinds = group_collections(*res)
        self.clamp()

    def _loaded_virtual(self, cols):
        items = [item(c, "virtual_collection_id", group=self.vlabel)
                 for c in sorted(cols, key=lambda c: c["name"].lower()) if c.get("rom_count")]
        self.kinds = [{"key": self.vtype, "label": self.vlabel, "items": items}]
        self.clamp()

    def snapshot(self):
        try:
            self.here_ids = set(self.app.library.snapshot())
        except Exception:                           # noqa: BLE001 — a count, never an error
            self.here_ids = set()

    def enter(self):
        """Back from a list: downloads and deletes may have changed what's here."""
        if self.gen != self.app.conn_gen:
            self.gen = self.app.conn_gen
            self.box, self.shelf_of = {}, {}
            self.row = self.col = self.off = self.wall_top = 0
            self.load()
            return
        self.snapshot()
        for st in self.box.values():
            st.pop("here", None)
            if st.get("roms") is not None:
                self.measure(st)

    def clamp(self):
        if not self.kinds:
            return
        self.row = max(0, min(self.row, len(self.kinds) - 1))
        self.col = max(0, min(self.col, len(self.kinds[self.row]["items"]) - 1))

    def current(self):
        """(the focused shelf, the focused tile), or (None, None) before the list is in."""
        if not self.kinds:
            return None, None
        self.clamp()
        k = self.kinds[self.row]
        return k, (k["items"][self.col] if k["items"] else None)     # an empty Browse by type: none

    def state(self, it):
        """The box's side of a collection: {"here", "roms", "rest"}; here from RomM's game ids
        and this app's installs at once, exact (games copied over by hand too) once its games
        are in."""
        key = (it.get("param"), it["id"])
        st = self.box.setdefault(key, {})
        if "here" not in st:
            if st.get("roms") is not None:
                self.measure(st)
            else:
                st["here"] = len(self.here_ids.intersection(str(i) for i in it["rom_ids"]))
        return st

    def measure(self, st):
        """here and the size of the rest from a collection's games, each checked on the box."""
        app = self.app
        here = rest = 0
        for r in st["roms"]:
            system = app.system_for(r)
            if system and app.library.is_installed(r, system):
                here += 1
            else:
                rest += r.get("fs_size_bytes") or 0
        st["here"], st["rest"] = here, rest

    def want_roms(self, it):
        """The focused collection's games (sizes, exact counts, the list it opens into), asked
        once it has kept the focus a moment; one collection at a time."""
        key = (it.get("param"), it["id"])
        if door(it) or self._busy or self.box.get(key, {}).get("roms") is not None:
            return
        if self.focus_since[0] != key:
            self.focus_since = (key, draw.NOW)
            return
        if draw.NOW - self.focus_since[1] < FOCUS_WAIT:
            return
        self.fetch_roms(it)

    def fetch_roms(self, it, then=None):
        """A collection's games in the background; then(roms) once they're in."""
        app, key = self.app, (it.get("param"), it["id"])
        st = self.box.setdefault(key, {})
        if st.get("roms") is not None:
            if then:
                then(st["roms"])
            return
        self._busy = key
        gen = self.gen

        def done(roms):
            self._busy = None
            if gen != self.gen or roms is None:
                return
            st["roms"] = roms
            self.measure(st)
            kept = [k for k, s in self.box.items() if s.get("roms") is not None and k != key]
            for old in kept[:max(0, len(kept) + 1 - ROMS_KEPT)]:
                self.box[old].pop("roms", None)
                self.box[old].pop("rest", None)
            if then:
                then(roms)

        def failed(e):
            self._busy = None
            app.toast(str(e), theme.RED)
        app.bg(lambda: app.api.roms_all(limit=250, cap=GameListScreen.BULK_MAX, collection=key), done, failed)

    # ---------------------------------------------------------------- input

    def nav_key(self):
        return self.row, self.col, self.wall

    def crumbs(self):
        k, it = self.current()
        if self.vtype:
            return ["Collections", self.vlabel]
        if not k:
            return ["Collections"]
        return ["Collections", k["label"]] + (["All {:,}".format(len(k["items"]))] if self.wall else [])

    def hints(self):
        k, it = self.current()
        if it is None:
            return [("B", "Back")]
        h = [("A", "Open")]
        if not door(it):
            h.append(("X", pads.ACTIONS))
        if not self.vtype and k["key"] != "browse":
            h.append(("Y", "Shelves" if self.wall else "All " + k["label"].lower()))
        if self.wall and len(k["items"]) > self.per_wall() * self.wall_rows():
            h += [("L", "Page up"), ("R", "Page down")]
        return h + [("B", "Shelves" if self.wall and not self.vtype else "Back")]

    def action(self, a):
        k, it = self.current()
        if it is None:
            return super().action(a)
        n, per = len(k["items"]), self.per_wall() if self.wall else self.per_shelf()
        if a in ("left", "right"):
            self.col = max(0, min(n - 1, self.col + (1 if a == "right" else -1)))
        elif a in ("l1", "r1"):
            step = per * (self.wall_rows() if self.wall else 1)
            self.col = max(0, min(n - 1, self.col + (step if a == "r1" else -step)))
        elif a in ("up", "down"):
            if self.wall:                                   # a row up or down; a shorter last row: its end
                t = self.col + (per if a == "down" else -per)
                if 0 <= t < n:
                    self.col = t
                elif a == "down" and self.col // per < (n - 1) // per:
                    self.col = n - 1
            else:
                self.row = max(0, min(len(self.kinds) - 1, self.row + (1 if a == "down" else -1)))
                self.col = min(self.col, len(self.kinds[self.row]["items"]) - 1)
        elif a == "ok":
            self.open(it)
        elif a == "x" and not door(it):
            self.menu(it)
        elif a == "y" and not self.vtype and k["key"] != "browse":
            self.wall = not self.wall
        elif a == "back" and self.wall and not self.vtype:     # the wall came from the shelves: back to them
            self.wall = False
        else:
            super().action(a)

    def tap(self, r_i, i):
        """Touch: a first tap focuses the tile, a second opens it."""
        if (self.row, self.col) == (r_i, i):
            self.action("ok")
        else:
            self.row, self.col = r_i, i

    def open(self, it):
        app = self.app
        if it.get("screen") == "backlog":
            app.push(BacklogScreen(app))
        elif it.get("browse"):
            app.push(CollectionScreen(app, it["browse"], it["name"]))
        else:
            app.push(self.list_of(it))

    def list_of(self, it):
        """The collection's games as a list (its games handed over when they're in already)."""
        st = self.box.get((it.get("param"), it["id"]), {})
        return GameListScreen(self.app, it["full"], collection=it, group=it["group"], static=st.get("roms"),
                              whole=it["count"] <= GameListScreen.BULK_MAX)

    def menu(self, it):
        app, st = self.app, self.state(it)
        rest = it["count"] - st["here"]
        if not rest:
            dl = ("Download the rest", "Every game is on this box already", None)
        elif it["count"] > GameListScreen.BULK_MAX:
            dl = ("Download the rest", "{:,} is more than 1,000: open it and filter first".format(it["count"]), None)
        else:
            sub = "{:,} game{}".format(rest, "" if rest == 1 else "s")
            if st.get("rest") is not None:
                sub += " · " + human_size(st["rest"])
            if st["here"]:
                sub += ", skipping the {:,} already here".format(st["here"])
            dl = ("Download the rest", sub, lambda: self.download_rest(it))
        e = [("Open", "", lambda: app.push(self.list_of(it))),
             compare_entry(app, it["full"], collection=it),
             ("Game night", "Roll one of its {:,} games for tonight".format(it["count"]),
              lambda: app.push(GameNightScreen(app, Scope.collection(app, it)))),
             dl]
        app.modal = ActionMenu(app, it["full"], e)

    def download_rest(self, it):
        app = self.app

        def listed(roms):
            if app.modal is not None or app.stack[-1] is not self:
                app.toast("List ready — press {X} on the collection again to download it", theme.AMBER)
                return
            app.queue_many(roms, "Download %s?" % it["full"])
        if self.box.get((it.get("param"), it["id"]), {}).get("roms") is None:
            app.toast("Listing %s…" % it["full"])
        self.fetch_roms(it, listed)

    # ---------------------------------------------------------------- drawing

    def sizes(self):
        """(tiles a shelf shows, tiles a wall row shows): Settings' Collections tile size, one fewer
        each in a narrower column (4:3)."""
        per, wall = TILE_SIZES.get(self.app.cfg.get("collection_tiles"), TILE_SIZES["medium"])
        narrow = COLUMN.width < 640
        return max(3, per - narrow), max(4, wall - narrow)

    def per_shelf(self):
        return self.sizes()[0]

    def per_wall(self):
        return self.sizes()[1]

    def shelf_metrics(self):
        """(tiles a shelf, a tile's side, its height, a shelf's pitch)."""
        per = self.per_shelf()
        side = (COLUMN.width - TILE_GAP * (per - 1)) // per
        th = collection_tile_h(side)
        return per, side, th, SHELF_HEAD + th + SHELF_GAP

    def wall_metrics(self):
        """(tiles a row, a tile's side, its height, rows in view, the rows' pitch): as many rows as
        fit under the heading, the height's slack shared evenly between them so the last row ends
        at the foot of the column."""
        per = self.per_wall()
        side = (COLUMN.width - TILE_GAP * (per - 1)) // per
        th = collection_tile_h(side)
        avail = COLUMN.height - WALL_HEAD
        rows = max(1, (avail + WALL_GAP) // (th + WALL_GAP))
        pitch = th + ((avail - rows * th) // (rows - 1) if rows > 1 else 0)
        return per, side, th, rows, pitch

    def wall_rows(self):
        return self.wall_metrics()[3]

    def draw(self, c):
        if self.kinds is None:
            text(c, self.f.h2, "Loading…", (W // 2, 360), theme.DIM, "center")
            return
        if self.draw_load_error(c):
            return
        k, it = self.current()
        if it is None:
            if self.vtype:
                text(c, self.f.h2, "Nothing under %s" % self.vlabel, (W // 2, 300), theme.DIM, "center")
                text(c, self.f.body, "RomM has no %s for its games yet: it fills them in from game metadata."
                     % self.vlabel.lower(), (W // 2, 340), theme.FAINT, "center", maxw=W - 96)
                return
            text(c, self.f.h2, "No collections", (W // 2, 300), theme.DIM, "center")
            text(c, self.f.body, "Make some in RomM and they show up here, grouped by their names.",
                 (W // 2, 340), theme.FAINT, "center")
            return
        if self.wall:
            self.draw_wall(c, k)
        else:
            self.draw_shelves(c)
        self.want_roms(it)
        self.draw_panel(c, k, it)

    def box_line(self, it, width):
        """A tile's quiet line: how much of the collection is on this box, short enough for the tile."""
        n, here = it["count"], self.state(it)["here"]
        if not here:
            return "{:,} game{}".format(n, "" if n == 1 else "s")
        if here >= n:
            return "All {:,} here".format(n)
        s = "{:,} of {:,} here".format(here, n)
        return s if measure(self.f.tiny, s) <= width else s[:-5]        # the bar says "here" already

    def tile(self, c, it, r, on, slot):
        if door(it):                    # Browse by: a quiet door, not a collection
            dr = r.inflate(8, 8) if on else r
            card(c, dr, on, 8, bg=theme.BG2)
            lines = wrap(self.f.h3, it["name"], dr.width - 16, 2)
            y = dr.centery - 12 * len(lines)
            for i, ln in enumerate(lines):
                text(c, self.f.h3, ln, (dr.centerx, y + i * 24), theme.TEXT if on else theme.DIM, "center")
            if it.get("screen"):
                text(c, self.f.tiny, "your RomM list", (dr.centerx, y + len(lines) * 24 + 4), theme.FAINT, "center",
                     maxw=dr.width - 16)
            return dr
        st = self.state(it)
        frac = st["here"] / it["count"] if it["count"] else 0
        return collection_tile(c, self.app, it["name"], self.art.cells(it), r, on, self.box_line(it, r.width), frac,
                               slot=slot)

    def draw_shelves(self, c):
        """The shelves, scrolled by the pixel: the least that brings the focused one whole into
        view, so the last shelf ends at the foot of the column with the one above it cut (and
        faded) at the top; the next one peeks under the fade at the bottom."""
        per, side, th, pitch = self.shelf_metrics()
        total = len(self.kinds) * pitch - SHELF_GAP
        y0, y1 = self.row * pitch, self.row * pitch + pitch - SHELF_GAP
        self.off = max(y1 - COLUMN.height, min(self.off, y0))
        self.off = max(0, min(self.off, total - COLUMN.height))
        area = pygame.Rect(COLUMN.left - 8, COLUMN.top, COLUMN.width + 16, COLUMN.height)
        old = c.get_clip()
        c.set_clip(SR(area).clip(old))
        for r_i, k in enumerate(self.kinds):
            y = COLUMN.top + r_i * pitch - self.off
            if y + pitch <= COLUMN.top or y >= COLUMN.bottom:
                continue
            on = r_i == self.row
            n = len(k["items"])
            note = "{:,} of {:,}".format(self.col + 1, n) if on and n > per else "{:,}".format(n)
            list_head(c, self.f, pygame.Rect(COLUMN.left - 8, y - 6, COLUMN.width + 16, SHELF_HEAD), k["label"], note,
                      color=theme.TEXT if on else theme.DIM)
            sh = self.shelf_of.setdefault(k["key"], Shelf((side, th), TILE_GAP))
            sh.tile = (side, th)
            sh.draw(c, self.app, k["items"], COLUMN.left, COLUMN.right, y + SHELF_HEAD, self.col if on else None,
                    tile=lambda c_, it, r, o, key=k["key"]: self.tile(c_, it, r, o, ("coll-tile", key)),
                    on_tap=lambda i, r_i=r_i: self.tap(r_i, i), clip=area)
        c.set_clip(old)
        if total - self.off > COLUMN.height:          # the next shelf peeks: fade it out
            _fade(c, theme.BG, SR(pygame.Rect(area.left, COLUMN.bottom - 44, area.width, 44)), "bottom")
        if self.off > 0:                              # the one above is cut: fade it in
            _fade(c, theme.BG, SR(pygame.Rect(area.left, COLUMN.top, area.width, 32)), "top")

    def draw_wall(self, c, k):
        per, side, th, vis, pitch = self.wall_metrics()
        items = k["items"]
        rows = (len(items) + per - 1) // per
        row = self.col // per
        self.wall_top = max(row - vis + 1, min(self.wall_top, row))
        here = sum(1 for it in items if not door(it) and self.state(it)["here"])
        note = "{:,} collections".format(len(items)) + (" · {:,} with games here".format(here) if here else "")
        list_head(c, self.f, pygame.Rect(COLUMN.left - 8, COLUMN.top - 6, COLUMN.width + 16, SHELF_HEAD),
                  k["label"], note)
        y0 = COLUMN.top + WALL_HEAD
        for i, it in enumerate(items):
            r_i, c_i = divmod(i, per)
            if not (self.wall_top <= r_i < self.wall_top + vis):
                continue
            r = pygame.Rect(COLUMN.left + c_i * (side + TILE_GAP), y0 + (r_i - self.wall_top) * pitch, side, th)
            self.app.hit(r, lambda i=i: self.tap(self.row, i))
            self.tile(c, it, r, i == self.col, ("wall-tile", i))
        if rows > vis:
            scrollbar(c, (COLUMN.right + 8, y0, 4, (vis - 1) * pitch + th), vis / rows, self.wall_top / (rows - vis))

    def draw_panel(self, c, k, it):
        app = self.app
        p = Panel(c, self.f, SIDE, app=app, key=(k["key"], it["id"]), slot="coll-panel")
        p.actions(hint_actions(self, ("X", "Y")))
        if door(it):
            p.verb("Open", "go", fn=lambda: self.action("ok"))
            if it.get("browse"):
                p.title(it["name"], sub="RomM's own grouping")
                p.prose("Every game in RomM, grouped by %s. A group opens like any collection: what's here, "
                        "what isn't, and the size to get the rest." % it["unit"], lines=4)
            else:
                p.title("Backlog", sub="Your RomM list")
                p.prose("The games you marked Backlog in RomM, with what fits on this box now.", lines=3)
            return
        st = self.state(it)
        here, rest = st["here"], it["count"] - st["here"]
        p.verb("Open", "go", "{:,} game{}".format(it["count"], "" if it["count"] == 1 else "s"),
               fn=lambda: self.action("ok"))
        mh = max(120, min(224, p.room() - 230))
        mosaic(c, app, self.art.cells(it), (p.x + (p.w - mh) // 2, p.y, mh, mh))
        p.space(mh + 14)
        p.title(it["name"], sub=it["kind"] or it["group"], sub_color=theme.FAINT, center=True)
        p.y -= 6
        p.pair("On this box", "{:,} of {:,}".format(here, it["count"]), theme.GREEN if here else theme.DIM)
        p.bar([(here / it["count"], theme.GREEN)] if it["count"] else [])
        if not rest:
            p.pair("To get", "Nothing: all of it is here", theme.GREEN)
        elif st.get("rest") is None:
            p.pair("To get the rest", "{:,} game{}".format(rest, "" if rest == 1 else "s"))
        else:
            p.pair("To get the rest", "{:,} game{} · {}".format(rest, "" if rest == 1 else "s", human_size(st["rest"])))
            free = app.free_after_queue()
            if free is not None:
                fits = st["rest"] <= free
                p.pair("Room for it", ("Yes, %s free" if fits else "No: %s free") % human_size(free),
                       theme.DIM if fits else theme.AMBER)
        if st.get("roms"):
            plats = {app.platform_by_id.get(r.get("platform_id"), {}).get("display_name") or
                     r.get("platform_display_name") or "" for r in st["roms"]}
            p.pair("Platforms", next(iter(plats)) if len(plats) == 1 else "%d platforms" % len(plats))
