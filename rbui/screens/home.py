"""Home and the platform list."""
import os

import pygame
import rommlib
from rommlib import human_size
from rbui import theme
from rbui.theme import W
from rbui.draw import card, focus_box, hline, installed_mark, measure, rrect, scroll_text, scrollbar, text, wrap
from rbui.screens.base import Screen
from rbui.screens.collection import CollectionScreen
from rbui.screens.games import GameListScreen, SearchScreen, surprise
from rbui.modals import ActionMenu
from rbui.screens.downloads import DownloadsScreen
from rbui.screens.settings import SettingsScreen
from rbui.screens.box import OnThisBoxScreen
from rbui.widgets.chips import tag
from rbui.widgets.tiles import Shelf


class HomeScreen(Screen):
    ITEMS = [("search", "Search", "Find any game across every platform"),
             ("platforms", "Platforms", "Browse by console"),
             ("collections", "Collections", "Series, Top-N lists and themes"),
             ("recent", "Recently added", "Newest games in the library"),
             ("downloads", "Downloads", "Queue and progress"),
             ("installed", "On this box", "Your games, space, BIOS files and checks"),
             ("settings", "Settings", "Server, controller, downloads, compression"),
             ("exit", "Exit", "Back to EmulationStation")]

    REC = ("recommended", "Recommended for you", "")

    def __init__(self, app):
        super().__init__(app)
        self.sel = 0
        self.free = None
        self.shelves = (Shelf((128, 172), 16), Shelf((128, 172), 16))

    def items(self):
        """ITEMS, with Recommended for you after Recently added while RomM has some."""
        recs = self.app.recs
        if not recs:
            return self.ITEMS
        k = next(i for i, it in enumerate(self.ITEMS) if it[0] == "recent") + 1
        seeds = [seed for _, _, seed in recs if seed]
        sub = "Picked by RomM from what you play" if seeds else "Highly rated games in your library"
        return self.ITEMS[:k] + [(self.REC[0], self.REC[1], sub)] + self.ITEMS[k:]

    def load_recs(self):
        """Recommended for you, asked once a session. Nothing to show (nothing to
        recommend yet, a RomM before 5.4, or an error) hides the row:
        never a message at start."""
        app = self.app
        if app.recs is not None or not app.platforms:
            return
        app.recs = []

        def done(items):
            had = [it[0] for it in self.items()]
            app.recs = [(x["rom"], rommlib.recommendation_why(x),
                         x["seed_rom_name"].strip() if isinstance(x.get("seed_rom_name"), str) else "")
                        for x in items or [] if app.system_for(x["rom"])]
            if not app.recs:
                rommlib.log("recommendations: %s" % ("none from RomM yet" if items is not None else
                                                     "this RomM has none (no /api/recommendations)"))
            keys = [it[0] for it in self.items()]
            if had != keys and app.stack and app.stack[0] is self:     # the row appeared: focus stays put
                self.sel = keys.index(had[self.sel])
        app.bg(lambda: app.api.recommendations(50), done, lambda e: None)

    def open_recs(self):
        app = self.app
        app.push(GameListScreen(app, "Recommended for you", static=[rec[0] for rec in app.recs],
                                why={rom["id"]: why for rom, why, _ in app.recs if why},
                                crumb="Recommended for you"))

    def enter(self):
        try:
            st = os.statvfs(rommlib.ROMS_ROOT)
            self.free = st.f_bavail * st.f_frsize
        except OSError:
            self.free = None

    def hints(self):
        return [("A", "Select"), ("Y", "Surprise me"), ("B", "Exit")]

    PARTY = [(0, "Any"), (2, "2+"), (3, "3+"), (4, "4+")]

    def surprise_menu(self):
        """Y: a random game from the whole library, for this many players and
        maybe only ones not on the box yet (kept for the session)."""
        app = self.app
        if not app.platforms:
            app.toast(app.error or "Still loading platforms…", theme.AMBER)
            return
        o = app.__dict__.setdefault("surprise_opts", {"players": 0, "new": False})

        def players(step=1):
            ns = [n for n, _ in self.PARTY]
            o["players"] = ns[(ns.index(o["players"]) + step) % len(ns)]

        def entries():
            n = len(app.platforms)
            return [("Pick a game", "From all %d platforms%s%s" % (
                        n, ", for %d+ players" % o["players"] if o["players"] else "",
                        ", not on this box yet" if o["new"] else ""), self.surprise_now),
                    None,
                    ("Players", "", players, dict(self.PARTY)[o["players"]], "choice"),
                    ("Only games not on this box", "", lambda: o.update(new=not o["new"]), o["new"], "switch")]
        app.modal = ActionMenu(app, "Surprise me", entries(), refresh=entries)

    def surprise_now(self):
        app, o = self.app, self.app.surprise_opts
        pids = [p["id"] for p in app.platforms]
        keep = (lambda r: app.rom_status(r)[0] != "installed") if o["new"] else None
        if not o["players"]:
            return surprise(app, page=lambda off, lim: app.api.roms(platform_ids=pids, offset=off, limit=lim),
                            scope={"platform_ids": pids}, keep=keep, where="your library")

        def page(off, lim):
            if app.player_counts is None:                  # RomM's player count values, once a session
                app.player_counts = [c for c in app.api.rom_filters().get("player_counts") or []
                                     if isinstance(c, str)]
            counts = rommlib.party_values(app.player_counts, o["players"])
            if not counts:
                return {"items": [], "total": 0}
            return app.api.roms(platform_ids=pids, offset=off, limit=lim, player_counts=counts)
        surprise(app, page=page, keep=keep, where="your library for %d+ players" % o["players"])

    def action(self, a):
        if a == "y":
            return self.surprise_menu()
        items = self.items()
        self.sel = min(self.sel, len(items) - 1)
        if a == "up":
            self.sel = (self.sel - 1) % len(items)
        elif a == "down":
            self.sel = (self.sel + 1) % len(items)
        elif a == "back":
            self.app.request_quit()
        elif a == "ok":
            key = items[self.sel][0]
            app = self.app
            if key == "exit":
                app.request_quit()
            elif key == "settings":
                app.push(SettingsScreen(app))
            elif key == "installed":
                app.push(OnThisBoxScreen(app))
            elif not app.platforms and key not in ("downloads",):
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
            elif key == "recent":
                app.push(GameListScreen(app, "Recently added", order_by="id_desc"))
            elif key == "recommended":
                self.open_recs()
            elif key == "downloads":
                app.push(DownloadsScreen(app))

    def draw(self, c):
        self.load_recs()
        items = self.items()
        self.sel = min(self.sel, len(items) - 1)
        pitch = min(71, 568 // len(items))       # one more row (Recommended for you) still ends with the panel
        rh = pitch - 7
        pad = max(3, (rh - 50) // 2)
        y = 84
        for i, (key, label, sub) in enumerate(items):
            r = pygame.Rect(48, y, 520, rh)
            self.app.hit(r, lambda i=i: self.tap_now(i))
            on = i == self.sel
            if on:
                focus_box(c, r, 14)
            else:
                rrect(c, theme.PANEL, r, 14)
            text(c, self.f.h2, label, (r.left + 28, r.top + pad + 1), theme.TEXT if on else theme.DIM)
            extra = ""
            if key == "downloads":
                n = self.app.downloader.active_count()
                extra = (", %d running" % n) if n else ""
            text(c, self.f.small, sub + extra, (r.left + 28, r.top + pad + 31), theme.DIM if on else theme.FAINT)
            y += pitch
        # right panel: a shelf of the newest box art — the library's own face
        p = pygame.Rect(616, 84, 616, 558)
        rrect(c, theme.PANEL, p, 16)
        app = self.app
        recs = app.recs and items[self.sel][0] == "recommended"     # the shelf follows the focus
        if app.platforms:
            if recs:
                text(c, self.f.h2, "Recommended for you", (p.left + 28, p.top + 22), theme.TEXT)
                n = len(app.recs)
                text(c, self.f.small, "%d game%s" % (n, "" if n == 1 else "s"), (p.right - 28, p.top + 28),
                     theme.FAINT, "topright")
                seeds = list(dict.fromkeys(seed for _, _, seed in app.recs if seed))
                note = ("Because you played " + " and ".join(seeds[:2]) + (" and more" if len(seeds) > 2 else "")) \
                    if seeds else "Highly rated games in your library, until you've played a few"
                text(c, self.f.body, note, (p.left + 28, p.top + 446), theme.DIM, maxw=p.width - 56)
                shelf = [rec[0] for rec in app.recs]
            else:
                total = sum(pl["rom_count"] for pl in app.platforms)
                text(c, self.f.h2, "Recently added", (p.left + 28, p.top + 22), theme.TEXT)
                text(c, self.f.small, "{:,} games on {} platforms".format(total, len(app.platforms)),
                     (p.right - 28, p.top + 28), theme.FAINT, "topright")
                self.load_recent()
                shelf = self.recent or []
            for k in range(2):                              # two rows of the newest box art
                self.shelves[k].draw(c, app, shelf[4 * k:4 * k + 4], p.left + 28, p.right - 28,
                                     p.top + 70 + k * 188)
            if self.recent == [] and not recs:
                text(c, self.f.body, "Nothing new yet.", (p.left + 28, p.top + 80), theme.DIM)
        elif app.error:
            text(c, self.f.h2, "Can't reach RomM", (p.left + 28, p.top + 22), theme.RED)
            scroll_text(c, self.f.body, app.error, (p.left + 28, p.top + 66, p.width - 56, 4 * 26), theme.DIM,
                        line_h=26, slot="home-error")
            text(c, self.f.small, rommlib.display_url(app.cfg["url"]) or "No server set",
                 (p.left + 28, p.top + 180), theme.FAINT)
            text(c, self.f.small, "Check Settings, then select any item to retry.",
                 (p.left + 28, p.top + 206), theme.FAINT)
        else:
            text(c, self.f.h2, "Loading…", (p.left + 28, p.top + 22), theme.DIM)
        # panel foot: this box and the server, as plain sentences
        hline(c, theme.LINE, (p.left + 28, p.bottom - 74), (p.right - 28, p.bottom - 74))
        if self.free is not None:
            text(c, self.f.h3, "%s free on this box" % human_size(self.free),
                 (p.left + 28, p.bottom - 38), theme.TEXT, "midleft")
        u = rommlib.display_url(app.cfg["url"]) or "No server set"
        insecure = u.lower().startswith("http://")
        text(c, self.f.small, u if insecure else u.split("://")[-1], (p.right - 28, p.bottom - 38),
             theme.AMBER if insecure else theme.DIM, "midright", maxw=270)

    recent = None

    def load_recent(self):
        if self.recent is not None or getattr(self, "_recent_loading", False):
            return
        self._recent_loading = True
        app = self.app
        p = {"limit": 8, "offset": 0, "order_by": "id", "order_dir": "desc",
             "with_char_index": "false", "with_filter_values": "false", "with_rom_id_index": "false",
             "platform_ids": [pl["id"] for pl in app.platforms]}
        app.bg(lambda: app.api.get("/api/roms", p).get("items", []),
               lambda items: setattr(self, "recent", items),
               lambda e: setattr(self, "recent", []))


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
            p = self.app.platforms[self.sel]
            surprise(self.app, page=lambda off, lim: self.app.api.roms(platform_ids=[p["id"]], offset=off, limit=lim),
                     scope={"platform_ids": [p["id"]]}, where=p.get("display_name") or p["name"])
        else:
            super().action(a)

    def hints(self):
        return [("A", "Open"), ("X", "Surprise me"), ("Y", "Search platform"), ("L", "Page up"),
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
