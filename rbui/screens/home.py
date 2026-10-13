"""Home and the platform list."""
import os

import pygame
import rommlib
from rommlib import human_size
from rbui import pads, theme
from rbui.theme import SR, W
from rbui.draw import _fade, card, COVER_BOX, installed_mark, measure, rom_title, scrollbar, text, wrap
from rbui.screens.base import Screen
from rbui.screens.collection import CollectionScreen
from rbui.screens.games import GameListScreen, SearchScreen, more_like
from rbui.screens.gamenight import GameNightScreen, Scope
from rbui.screens.backlog import BacklogScreen
from rbui.modals import ActionMenu, DownloadConfirm, ManageMenu
from rbui.screens.downloads import DownloadsScreen
from rbui.screens.game import GameScreen
from rbui.screens.settings import SettingsScreen
from rbui.screens.box import OnThisBoxScreen
from rbui.widgets.chips import pill_row, tag
from rbui.widgets.hero import game_verb, meta_line, press_verb, time_ago
from rbui.widgets.layout import list_head, Panel, split
from rbui.widgets.tiles import collection_card, cover, cover_capacity, mosaic, Shelf, tile_size

# the rail and shelves, the hero panel beside them
HOME_COLUMN, HOME_PANEL = split(702, pygame.Rect(48, 84, W - 96, 558), 24)


class HomeScreen(Screen):
    """A rail of destinations, then shelves of covers: Playing and Backlog from your RomM
    status, Recommended for you, New in RomM, Collections. The panel beside them shows
    whatever has the focus: a game (its verb), a collection, or what's behind a rail pill.
    Focus is a shelf's key ("rail" for the rail), so a shelf turning up above keeps it put."""
    RAIL = [("search", "Search", "Find any game across every platform"),
            ("platforms", "Platforms", "Browse by console"),
            ("collections", "Collections", "Series, Top-N lists and themes"),
            ("downloads", "Activity", "Downloads and compressing, as they run"),
            ("installed", "On this box", "Your games, space, BIOS files and checks"),
            ("settings", "Settings", "Server, controller, downloads, compression")]
    RAIL_Y = 84
    Y0, Y1 = 138, 674               # the shelf area, under the rail and above the hint bar
    GAP = 12
    HEAD = 30                       # a shelf's heading, above its covers
    LIST_MAX = 60                   # a RomM list's first page: what a shelf shows
    MOSAICS = 24                    # collections whose four covers are fetched for their cards

    def __init__(self, app):
        super().__init__(app)
        self.focus = None           # None: the first shelf (the rail until one has games)
        self.rail_i = 0
        self.cols = {}              # shelf key -> the focused tile in it
        self.top = 0                # the first shelf shown
        self.shelf_of = {}          # shelf key -> its tiles.Shelf (keeps its scroll)
        self.free = None
        self.on_box = 0
        self.lists = {}             # "playing"/"backlog" -> {items, total}; [] items when unsupported
        self.colls = None           # [{name, full, count, param, id}]
        self.coll_roms = {}         # collection id -> its first four roms
        self.played = {}            # rom id -> rommlib.played_here(...) (None: not on the box)
        self.rail_rects = []
        self._busy = set()
        self._retry = set()         # keys whose fetch failed: asked again on the next enter
        self.gen = app.conn_gen     # the connection the cached shelves came from

    # ---------------------------------------------------------------- data

    def load_recs(self):
        """Recommended for you, asked once a session. Nothing to show (nothing to
        recommend yet, a RomM before 5.4, or an error) hides the shelf:
        never a message at start."""
        app = self.app
        if app.recs is not None or not app.platforms:
            return
        app.recs = []
        gen = app.conn_gen

        def done(items):
            if gen != app.conn_gen:
                return
            app.recs = [(x["rom"], rommlib.recommendation_why(x),
                         x["seed_rom_name"].strip() if isinstance(x.get("seed_rom_name"), str) else "")
                        for x in items or [] if app.system_for(x["rom"])]
            if not app.recs:
                rommlib.log("recommendations: %s" % ("none from RomM yet" if items is not None else
                                                     "this RomM has none (no /api/recommendations)"))
        app.bg(lambda: app.api.recommendations(50), done, lambda e: None)

    def _once(self, key, fn, done, failed=None):
        """fn in the background, once (until done/failed clears the key)."""
        if key in self._busy:
            return
        self._busy.add(key)
        busy, gen = self._busy, self.gen

        def ok(r):
            busy.discard(key)
            if gen == self.gen == self.app.conn_gen:
                done(r)

        def err(e):
            busy.discard(key)
            if gen == self.gen == self.app.conn_gen:
                self._retry.add(key)
                (failed or (lambda e: None))(e)
        self.app.bg(fn, ok, err)

    def follow_connection(self):
        """After a reconnect (another server or user), forget the old one's shelves."""
        if self.gen == self.app.conn_gen:
            return
        self.gen = self.app.conn_gen
        self.lists, self.colls, self.coll_roms, self.recent = {}, None, {}, None
        self.played, self.cols, self.shelf_of = {}, {}, {}
        self._busy, self._retry = set(), set()
        self.focus, self.top = None, 0

    def retry_failed(self):
        """Fetches that failed are asked again (their empty stand-ins dropped)."""
        for key in self._retry:
            if key in ("playing", "backlog"):
                self.lists.pop(key, None)
            elif key == "colls":
                self.colls = None
            elif key == "recent":
                self.recent = None
            elif isinstance(key, tuple) and key[0] == "mosaic":
                self.coll_roms.pop(key[1], None)
        self._retry = set()

    def load(self):
        """Everything the shelves show, each asked once when the platforms are in."""
        app = self.app
        self.follow_connection()
        self.load_recs()
        if not app.platforms:
            return
        pids = [p["id"] for p in app.platforms]
        for name in ("playing", "backlog"):
            if name not in self.lists:
                def got(page, name=name):
                    # a RomM that ignores the status filter can't list it: no shelf
                    self.lists[name] = {"items": [] if page["filtered"] else page["items"], "total": page["total"]}
                self._once(name, lambda name=name: app.api.user_list(name, limit=self.LIST_MAX, platform_ids=pids),
                           got, lambda e, name=name: self.lists.__setitem__(name, {"items": [], "total": 0}))
        self.load_recent()
        if self.colls is None:
            self._once("colls", app.api.collections, self._got_colls, lambda e: setattr(self, "colls", []))

    def _got_colls(self, cols):
        self.colls = sorted(({"name": c["name"], "full": c["name"], "count": c["rom_count"],
                              "param": "collection_id", "id": c["id"]}
                             for c in cols or [] if c.get("rom_count")), key=lambda c: c["name"].lower())

    def want_mosaic(self, coll):
        """A collection card's four covers, fetched when it's first drawn (one at a time)."""
        cid = coll["id"]
        if cid in self.coll_roms or any(k[0] == "mosaic" for k in self._busy if isinstance(k, tuple)):
            return
        app = self.app
        pids = [p["id"] for p in app.platforms]
        self._once(("mosaic", cid),
                   lambda: app.api.roms(platform_ids=pids, collection=(coll["param"], cid), limit=4).get("items", []),
                   lambda roms: self.coll_roms.__setitem__(cid, roms[:4]),
                   lambda e: self.coll_roms.__setitem__(cid, []))

    recent = None

    def load_recent(self):
        if self.recent is not None:
            return
        app = self.app
        p = {"limit": 24, "offset": 0, "order_by": "id", "order_dir": "desc",
             "with_char_index": "false", "with_filter_values": "false", "with_rom_id_index": "false",
             "platform_ids": [pl["id"] for pl in app.platforms]}
        self._once("recent", lambda: app.api.get("/api/roms", p).get("items", []),
                   lambda items: setattr(self, "recent", items), lambda e: setattr(self, "recent", []))

    def want_played(self, rom):
        """How much the focused game was played here (EmulationStation), asked once."""
        rid = rom["id"]
        system = self.app.system_for(rom)
        if rid in self.played or not system or self.app.rom_status(rom)[0] != "installed":
            return
        lib = self.app.library
        self._once(("played", rid), lambda: rommlib.played_here(lib, rom, system),
                   lambda r: self.played.__setitem__(rid, r), lambda e: self.played.__setitem__(rid, None))

    def enter(self):
        try:
            st = os.statvfs(rommlib.ROMS_ROOT)
            self.free = st.f_bavail * st.f_frsize
        except OSError:
            self.free = None
        try:
            self.on_box = len(self.app.library.snapshot())
        except Exception:                          # noqa: BLE001 — a glance, never an error
            self.on_box = 0
        self.played = {}                           # back from a game list: installs may have changed
        self.follow_connection()
        self.retry_failed()

    # ---------------------------------------------------------------- shelves

    def per_shelf(self):
        """Covers a shelf shows at once: seven, or five bigger ones when this box can't keep
        about four shelves of seven covers in memory (a 4K screen on a small box)."""
        return 7 if cover_capacity() >= 36 else 5

    def tile(self):
        return tile_size(HOME_COLUMN.width, self.per_shelf(), self.GAP)

    def pitch(self):
        return self.HEAD + self.tile()[1] + 14

    def shelves(self):
        """The shelves that have something: [(key, heading, note, items)]; Collections' items
        are collection dicts, the rest roms."""
        app = self.app
        out = []
        playing = self.lists.get("playing") or {}
        if playing.get("items"):
            n = max(playing["total"], len(playing["items"]))
            out.append(("playing", "Playing", "%d marked in RomM" % n, playing["items"]))
        recs = app.recs or []
        if recs:
            seeds = list(dict.fromkeys(s for _, _, s in recs if s))
            note = ("Because you played " + " and ".join(seeds[:2]) + (" and more" if len(seeds) > 2 else "")) \
                if seeds else "Highly rated in your library"
            out.append(("recommended", "Recommended for you", note, [r for r, _, _ in recs]))
        if self.recent and app.platforms:
            out.append(("new", "New in RomM", "{:,} games on {} platforms".format(
                sum(p["rom_count"] for p in app.platforms), len(app.platforms)), self.recent))
        backlog = self.lists.get("backlog") or {}
        if backlog.get("items"):
            items = backlog["items"]
            here = [r for r in items if app.rom_status(r)[0] == "installed"]
            fetch = sum(r.get("fs_size_bytes") or 0 for r in items if r not in here)
            n = max(backlog["total"], len(items))
            note = ["%d game%s" % (n, "" if n == 1 else "s")] + (["%d on this box" % len(here)] if here else []) + \
                (["%s to download" % human_size(fetch)] if fetch else [])
            out.append(("backlog", "Backlog", " · ".join(note), items))
        if self.colls:
            out.append(("collections", "Collections", "%d in RomM" % len(self.colls), self.colls))
        return out

    def focused(self):
        """(key, shelf or None, item or None): what has the focus now. key is "rail" or a
        shelf's key; the focus falls back to the first shelf (or the rail) when its shelf
        is gone."""
        shelves = self.shelves()
        keys = [s[0] for s in shelves]
        key = self.focus
        if key != "rail" and key not in keys:
            key = keys[0] if keys else "rail"
        if key == "rail":
            self.rail_i = max(0, min(self.rail_i, len(self.RAIL) - 1))
            return "rail", None, self.RAIL[self.rail_i]
        shelf = shelves[keys.index(key)]
        i = self.cols[key] = max(0, min(self.cols.get(key, 0), len(shelf[3]) - 1))
        return key, shelf, shelf[3][i]

    def focus_shelf(self, key, i=None):
        self.focus = key
        if i is not None:
            if key == "rail":
                self.rail_i = i
            else:
                self.cols[key] = i

    # ---------------------------------------------------------------- input

    def hints(self):
        key, shelf, it = self.focused()
        if shelf and key != "collections":
            a = "Play now" if self.app.rom_status(it)[0] == "installed" else "Download"
            return [("A", a), ("X", pads.ACTIONS), ("Y", "Game night"), ("B", "Exit")]
        return [("A", "Open"), ("Y", "Game night"), ("B", "Exit")]

    def action(self, a):
        if a == "y":
            return self.game_night()
        if a == "back":
            return self.app.request_quit()
        key, shelf, it = self.focused()
        rows = ["rail"] + [s[0] for s in self.shelves()]
        row = rows.index(key)
        if a in ("up", "down"):
            self.focus = rows[max(0, min(len(rows) - 1, row + (1 if a == "down" else -1)))]
        elif a in ("left", "right", "l1", "r1"):
            step = (1 if a in ("right", "r1") else -1) * (1 if a in ("left", "right") else self.per_shelf())
            self.focus = key
            if key == "rail":
                self.rail_i = max(0, min(len(self.RAIL) - 1, self.rail_i + step))
            else:
                self.cols[key] = max(0, min(len(shelf[3]) - 1, self.cols[key] + step))
        elif a == "ok":
            if key == "rail":
                self.open_rail(it[0])
            elif key == "collections":
                self.open_collection(it)
            else:
                self.open_game(it)
        elif a == "x" and shelf and key != "collections":
            self.game_actions(it, shelf)

    def tap_tile(self, key, i):
        """Touch: a first tap focuses the tile, a second acts on it."""
        if self.focused()[0] == key and self.cols.get(key) == i:
            self.action("ok")
        else:
            self.focus_shelf(key, i)

    def tap_rail(self, i):
        self.focus_shelf("rail", i)
        self.action("ok")

    def open_game(self, rom):
        """A on a game: its verb (Play now, or the download prompt)."""
        press_verb(self.app, rom)

    def open_page(self, rom):
        why = next((w for r, w, _ in self.app.recs or [] if r["id"] == rom["id"] and w), None)
        self.app.push(GameScreen(self.app, rom, why=why))

    def open_collection(self, coll):
        self.app.push(GameListScreen(self.app, coll["full"], collection=coll))

    def open_shelf(self, key):
        """The shelf as a full list: New in RomM is the Recently added list."""
        app = self.app
        if key == "recommended":
            return self.open_recs()
        if key == "new":
            return app.push(GameListScreen(app, "Recently added", order_by="id_desc"))
        if key == "backlog":
            return app.push(BacklogScreen(app))
        app.push(GameListScreen(app, "Playing", static=list(self.lists[key]["items"]), crumb="Playing"))

    def game_actions(self, rom, shelf):
        """X on a game: what the list's menus offer for it, its page, and its shelf as a list."""
        app = self.app
        key, heading = shelf[0], shelf[1]
        name = rom_title(rom)
        more = [("Open the game page", "Facts, screenshots and your RomM notes", lambda: self.open_page(rom)),
                ("More like this", name, lambda: more_like(app, rom)),
                ("See the whole shelf", heading, lambda: self.open_shelf(key))]
        if app.rom_status(rom)[0] == "installed":
            m = ManageMenu(app, rom)
            m.entries += [None] + more
        else:
            dl = game_verb(app, rom)
            m = ActionMenu(app, name, [(dl["label"], dl.get("detail", ""),
                                        (lambda: setattr(app, "modal", DownloadConfirm(app, rom)))
                                        if dl["kind"] == "go" else None)] + more)
        app.modal = m

    def open_rail(self, key):
        app = self.app
        if key == "settings":
            app.push(SettingsScreen(app))
        elif key == "installed":
            app.push(OnThisBoxScreen(app))
        elif key == "downloads":
            app.push(DownloadsScreen(app))
        elif not app.platforms:
            app.load_platforms()
            msg = app.error or ("No RomM platform maps to a Batocera system"
                                if app.loaded else "Still loading platforms…")
            app.toast(msg, theme.AMBER)
        elif key == "search":
            app.push(SearchScreen(app))
        elif key == "platforms":
            app.push(PlatformScreen(app))
        elif key == "collections":
            app.push(CollectionScreen(app))

    def open_recs(self):
        app = self.app
        app.push(GameListScreen(app, "Recommended for you", static=[rec[0] for rec in app.recs],
                                why={rom["id"]: why for rom, why, _ in app.recs if why},
                                crumb="Recommended for you"))

    def game_night(self):
        """Y: Game night, rolling from the whole library."""
        app = self.app
        if not app.platforms:
            app.toast(app.error or "Still loading platforms…", theme.AMBER)
            return
        app.push(GameNightScreen(app, Scope.library(app)))

    # ---------------------------------------------------------------- drawing

    def draw(self, c):
        self.load()
        key, shelf, it = self.focused()
        self.draw_rail(c, key)
        self.draw_shelves(c, key)
        self.draw_panel(c, key, shelf, it)

    def draw_rail(self, c, key):
        n = self.app.downloader.active_count()
        labels = [label + (" · %d" % n if k == "downloads" and n else "") for k, label, _ in self.RAIL]
        self.rail_rects = pill_row(c, self.f.body, labels, HOME_COLUMN.left, HOME_COLUMN.right, self.RAIL_Y,
                                   focus=self.rail_i if key == "rail" else None, app=self.app, on_tap=self.tap_rail)

    def draw_shelves(self, c, key):
        app, col = self.app, HOME_COLUMN
        shelves = self.shelves()
        if not shelves:
            msg = ("" if app.error else "Loading…") if not app.platforms else \
                "Loading…" if self.recent is None else "Nothing new in RomM yet."
            text(c, self.f.body, msg, (col.left, self.Y0 + 4), theme.DIM)
            return
        pitch = self.pitch()
        vis = max(1, (self.Y1 - self.Y0) // pitch)            # whole shelves on screen; the next peeks
        keys = [s[0] for s in shelves]
        if key in keys:
            row = keys.index(key)
            self.top = max(row - vis + 1, min(self.top, row))
        self.top = max(0, min(self.top, len(shelves) - 1))
        area = pygame.Rect(col.left - 8, self.Y0, col.width + 16, self.Y1 - self.Y0)
        old = c.get_clip()
        c.set_clip(SR(area).clip(old))
        tw, th = self.tile()
        y = self.Y0
        for k in range(self.top, len(shelves)):
            if y >= self.Y1:
                break
            skey, heading, note, items = shelves[k]
            on = skey == key
            cards = skey == "collections"
            list_head(c, self.f, pygame.Rect(col.left - 8, y - 6, col.width + 16, self.HEAD), heading, note,
                      color=theme.TEXT if on else theme.DIM)
            sh = self.shelf_of.setdefault(skey, Shelf((tw, th), self.GAP, "bottom" if cards else "top"))
            sh.tile = ((col.width - self.GAP) // 2, th) if cards else (tw, th)
            sh.draw(c, app, items, col.left, col.right, y + self.HEAD, self.cols.get(skey, 0) if on else None,
                    tile=self.draw_card if cards else None,
                    on_tap=lambda i, k=skey: self.tap_tile(k, i), clip=area)
            y += pitch
        c.set_clip(old)
        if y > self.Y1:                                        # the next shelf peeks: fade it out
            _fade(c, theme.BG, SR(pygame.Rect(area.left, self.Y1 - 44, area.width, 44)), "bottom")

    def draw_card(self, c, coll, rect, on):
        self.want_mosaic(coll)
        return collection_card(c, self.app, coll["full"], self.coll_roms.get(coll["id"], []), rect, on,
                               sub="{:,} games".format(coll["count"]))

    def draw_panel(self, c, key, shelf, it):
        app = self.app
        p = Panel(c, self.f, HOME_PANEL, app=app, key=(key, it.get("id") if isinstance(it, dict) else it),
                  slot="home-panel")
        u = rommlib.display_url(app.cfg["url"]) or "No server set"
        insecure = u.lower().startswith("http://")
        p.foot("%s free on this box" % human_size(self.free) if self.free is not None else "",
               right=u if insecure else u.split("://")[-1], right_color=theme.AMBER if insecure else theme.FAINT)
        if not app.platforms:
            if app.error:
                p.title("Can't reach RomM", sub=u, sub_color=theme.FAINT)
                p.prose(app.error, color=theme.RED, lines=4, line_h=24)
                p.line("Check Settings, then select any item to retry.", theme.FAINT)
            else:
                p.title("Loading…")
        elif key == "rail":
            self.panel_rail(p, it)
        elif key == "collections":
            self.panel_collection(p, it)
        else:
            self.panel_game(p, key, it)

    def rail_rows(self, key):
        """A glance at what's behind a rail pill: [(name, value)]."""
        app = self.app
        if key == "search":
            return [("Across every platform", "{:,} games".format(sum(p["rom_count"] for p in app.platforms))),
                    ("Platforms", "%d" % len(app.platforms))]
        if key == "platforms":
            return [(p.get("display_name") or p["name"], "{:,}".format(p["rom_count"]))
                    for p in sorted(app.platforms, key=lambda p: -p["rom_count"])]
        if key == "collections":
            if self.colls is None:
                return [("Loading…", "")]
            return [(cl["full"], "{:,}".format(cl["count"])) for cl in self.colls] or \
                [("No collections in RomM yet", "")]
        if key == "downloads":
            jobs = [j for j in app.downloader.jobs if j.active]
            return [(j.name, j.state) for j in jobs] or [("Nothing downloading right now", "")]
        if key == "installed":
            return [("Games from RomM", "{:,}".format(self.on_box)),
                    ("Free space", human_size(self.free) if self.free is not None else "?")]
        if key == "settings":
            u = rommlib.display_url(app.cfg["url"]) or "No server set"
            return [("Server", u.split("://")[-1]),
                    ("Theme", theme.THEMES.get(app.cfg.get("theme"), (app.cfg.get("theme") or "Default",))[0])]
        return []

    def panel_rail(self, p, it):
        key, label, sub = it
        p.title(label, sub=sub)
        for name, value in self.rail_rows(key):
            if p.room() < 38:
                break
            p.pair(name, value, theme.FAINT, theme.DIM)
            p.rule()

    def panel_collection(self, p, coll):
        p.verb("Open the collection", "go", fn=lambda: self.action("ok"))
        mh = max(100, min(300, p.room() - 110))
        mw = mh * 232 // 300
        mosaic(p.c, self.app, self.coll_roms.get(coll["id"], []), (p.x + (p.w - mw) // 2, p.y, mw, mh))
        p.space(mh + 16)
        p.title(coll["full"], sub="{:,} games".format(coll["count"]), center=True)

    def status_line(self, key, rom):
        """Your RomM status of the game, how recently it was played here, else RomM's reason
        for recommending it."""
        st = rommlib.user_status(rom)
        label = {"playing": "Playing", "backlog": "On your backlog"}.get(key) or st["label"] or \
            ("Playing" if st["now_playing"] else "On your backlog" if st["backlogged"] else "")
        parts = [label] if label else []
        here = self.played.get(rom["id"])
        if here and here.get("last_played"):
            parts.append("played here " + time_ago(here["last_played"]))
        if not parts and key == "recommended":
            return next((w for r, w, _ in self.app.recs or [] if r["id"] == rom["id"] and w), "")
        return " · ".join(parts)

    def panel_game(self, p, key, rom):
        app = self.app
        self.want_played(rom)
        p.verb(fn=lambda: self.action("ok"), **game_verb(app, rom))
        ch = max(120, min(COVER_BOX[1], p.room() - 162))        # leaves a line of title, meta, status, 2 of summary
        cw = ch * 3 // 4
        cover(p.c, app, rom, (p.x + (p.w - cw) // 2, p.y, cw, ch), fit=True)
        p.space(ch + 14)
        p.title(rom_title(rom), sub=meta_line(app, rom, players=False), center=True)
        p.y -= 8
        status = self.status_line(key, rom)
        if status:
            p.line(status, theme.ACCENT_HI, self.f.cond, center=True)
        summary = rom.get("summary") or ""
        if summary and p.room() >= 22:
            p.space(4)
            p.prose(summary, color=theme.FAINT, line_h=20)


class PlatformScreen(Screen):
    COLS, CW, CH, GAP = 4, 284, 98, 14

    def __init__(self, app):
        super().__init__(app)
        self.sel = 0
        self.scroll = 0
        app.bg(rommlib.es_counts, lambda n: setattr(app, "es_count", n), lambda e: None)

    def crumbs(self):
        return ["Platforms"]

    def action(self, a):
        n = len(self.app.platforms)
        if not n:
            return super().action(a)
        moves = {"left": -1, "right": 1, "up": -self.COLS, "down": self.COLS,
                 "l1": -self.COLS * 4, "r1": self.COLS * 4}
        if a in moves:
            self.sel = max(0, min(n - 1, self.sel + moves[a]))
        elif a == "ok":
            p = self.app.platforms[self.sel]
            self.app.push(GameListScreen(self.app, p.get("display_name") or p["name"],
                                         platform=p))
        elif a == "y":
            self.app.push(SearchScreen(self.app, platform=self.app.platforms[self.sel]))
        elif a == "x":
            self.app.push(GameNightScreen(self.app, Scope.platform(self.app, self.app.platforms[self.sel])))
        else:
            super().action(a)

    def hints(self):
        return [("A", "Open"), ("X", "Game night"), ("Y", "Search platform"), ("L", "Page up"),
                ("R", "Page down"), ("B", "Back")]

    def draw(self, c):
        plats = self.app.platforms
        x0, y0 = 48, 88
        rows_vis = 5
        row = self.sel // self.COLS
        if row < self.scroll:
            self.scroll = row
        elif row >= self.scroll + rows_vis:
            self.scroll = row - rows_vis + 1
        for i, p in enumerate(plats):
            r_i, c_i = divmod(i, self.COLS)
            if not (self.scroll <= r_i < self.scroll + rows_vis):
                continue
            r = pygame.Rect(x0 + c_i * (self.CW + self.GAP),
                            y0 + (r_i - self.scroll) * (self.CH + self.GAP), self.CW, self.CH)
            self.app.hit(r, lambda i=i: self.tap_now(i))
            on = i == self.sel
            card(c, r, on, 14)
            name = p.get("display_name") or p["name"]
            lines = wrap(self.f.h3, name, r.width - 36, 2)
            for k, ln in enumerate(lines):
                text(c, self.f.h3, ln, (r.left + 18, r.top + 14 + k * 24), theme.TEXT if on else theme.DIM)
            tr = tag(c, self.f.tiny, "roms/" + p["_system"], (r.right - 14, r.bottom - 30), "topright")
            room = tr.left - 10                   # the left side never runs into the tag
            games = "{:,} games".format(p["rom_count"])
            if r.left + 18 + measure(self.f.small, games) > room:
                games = "{:,}".format(p["rom_count"])
            text(c, self.f.small, games, (r.left + 18, r.bottom - 28), theme.DIM if on else theme.FAINT)
            here = self.app.es_count.get(p["_system"])
            hx = r.left + 18 + measure(self.f.small, games) + 16
            if here and hx + 11 + measure(self.f.small, "{:,}".format(here)) <= room:
                installed_mark(c, (hx, r.bottom - 19), 7)     # games EmulationStation lists there
                text(c, self.f.small, "{:,}".format(here), (hx + 11, r.bottom - 28), theme.GREEN)
        # scrollbar
        total_rows = (len(plats) + self.COLS - 1) // self.COLS
        if total_rows > rows_vis:
            scrollbar(c, (W - 20, y0, 4, rows_vis * (self.CH + self.GAP) - self.GAP), rows_vis / total_rows,
                      self.scroll / (total_rows - rows_vis))
