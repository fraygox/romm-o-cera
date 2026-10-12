"""Home and the platform list."""
import os

import pygame
import rommlib
from rommlib import human_size
from rbui import theme
from rbui.theme import SR, W
from rbui.draw import focus_box, hline, installed_mark, measure, rrect, smooth_scale, text, wrap
from rbui.screens.base import Screen
from rbui.screens.collection import CollectionScreen
from rbui.screens.games import GameListScreen, SearchScreen
from rbui.screens.downloads import DownloadsScreen
from rbui.screens.settings import SettingsScreen
from rbui.screens.box import OnThisBoxScreen


class HomeScreen(Screen):
    ITEMS = [("search", "Search", "Find any game across every platform"),
             ("platforms", "Platforms", "Browse by console"),
             ("collections", "Collections", "Series, Top-N lists and themes"),
             ("recent", "Recently added", "Newest games in the library"),
             ("downloads", "Downloads", "Queue and progress"),
             ("installed", "On this box", "Your games, space, BIOS files and checks"),
             ("settings", "Settings", "Server, controller, downloads, compression"),
             ("exit", "Exit", "Back to EmulationStation")]

    def __init__(self, app):
        super().__init__(app)
        self.sel = 0
        self.free = None

    def enter(self):
        try:
            st = os.statvfs(rommlib.ROMS_ROOT)
            self.free = st.f_bavail * st.f_frsize
        except OSError:
            self.free = None

    def hints(self):
        return [("A", "Select"), ("B", "Exit")]

    def action(self, a):
        if a == "up":
            self.sel = (self.sel - 1) % len(self.ITEMS)
        elif a == "down":
            self.sel = (self.sel + 1) % len(self.ITEMS)
        elif a == "back":
            self.app.request_quit()
        elif a == "ok":
            key = self.ITEMS[self.sel][0]
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
            elif key == "downloads":
                app.push(DownloadsScreen(app))

    def draw(self, c):
        y = 84
        for i, (key, label, sub) in enumerate(self.ITEMS):
            r = pygame.Rect(48, y, 520, 64)
            self.app.hit(r, lambda i=i: self.tap_now(i))
            on = i == self.sel
            if on:
                focus_box(c, r, 14)
            else:
                rrect(c, theme.PANEL, r, 14)
            text(c, self.f.h2, label, (r.left + 28, r.top + 8), theme.TEXT if on else theme.DIM)
            extra = ""
            if key == "downloads":
                n = self.app.downloader.active_count()
                extra = (", %d running" % n) if n else ""
            text(c, self.f.small, sub + extra, (r.left + 28, r.top + 38), theme.DIM if on else theme.FAINT)
            y += 71
        # right panel: a shelf of the newest box art — the library's own face
        p = pygame.Rect(616, 84, 616, 558)
        rrect(c, theme.PANEL, p, 16)
        app = self.app
        if app.platforms:
            total = sum(pl["rom_count"] for pl in app.platforms)
            text(c, self.f.h2, "Recently added", (p.left + 28, p.top + 22), theme.TEXT)
            text(c, self.f.small, "{:,} games on {} platforms".format(total, len(app.platforms)),
                 (p.right - 28, p.top + 28), theme.FAINT, "topright")
            self.load_recent()
            cw, ch, gap = 128, 172, 16
            x0 = p.left + (p.width - (4 * cw + 3 * gap)) // 2
            for k, rom in enumerate((self.recent or [])[:8]):
                cr = pygame.Rect(x0 + (k % 4) * (cw + gap), p.top + 70 + (k // 4) * (ch + gap), cw, ch)
                img = app.cover(rom)
                if img:
                    sc = self.shelf.get(rom["id"])
                    if sc is None or sc[0] != id(img):
                        pc = SR(cr)
                        iw, ih = img.get_size()
                        f = max(pc.width / iw, pc.height / ih)      # fill the slot, crop overflow
                        scaled = smooth_scale(img, (max(1, int(iw * f)), max(1, int(ih * f))))
                        crop = pygame.Rect(0, 0, pc.width, pc.height)
                        crop.center = scaled.get_rect().center
                        # the crop and the cover's id, not the cover: an evicted cover is freed
                        sc = (id(img), scaled.subsurface(crop.clip(scaled.get_rect())).copy())
                        if len(self.shelf) > 24:
                            self.shelf.clear()
                        self.shelf[rom["id"]] = sc
                    c.blit(sc[1], SR(cr).topleft)
                else:
                    rrect(c, theme.BG2, cr, 8)
                    for j, ln in enumerate(wrap(self.f.small, rom.get("name") or "", cw - 16, 4)):
                        text(c, self.f.small, ln, (cr.left + 8, cr.top + 10 + j * 20), theme.FAINT)
            if self.recent == []:
                text(c, self.f.body, "Nothing new yet.", (p.left + 28, p.top + 80), theme.DIM)
        elif app.error:
            text(c, self.f.h2, "Can't reach RomM", (p.left + 28, p.top + 22), theme.RED)
            for i, line in enumerate(wrap(self.f.body, app.error, p.width - 56, 4)):
                text(c, self.f.body, line, (p.left + 28, p.top + 66 + i * 26), theme.DIM)
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
    shelf = None

    def load_recent(self):
        if self.recent is not None or getattr(self, "_recent_loading", False):
            return
        self._recent_loading = True
        app = self.app
        self.shelf = {}
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
        else:
            super().action(a)

    def hints(self):
        return [("A", "Open"), ("Y", "Search platform"), ("L", "Page up"),
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
            rrect(c, theme.PANEL_HI if on else theme.PANEL, r, 14)
            if on:
                rrect(c, theme.ACCENT, r, 14, 2)
            name = p.get("display_name") or p["name"]
            lines = wrap(self.f.h3, name, r.width - 36, 2)
            for k, ln in enumerate(lines):
                text(c, self.f.h3, ln, (r.left + 18, r.top + 14 + k * 24), theme.TEXT if on else theme.DIM)
            tag = "roms/" + p["_system"]
            tw = int(measure(self.f.tiny, tag)) + 14
            tr = pygame.Rect(r.right - tw - 14, r.bottom - 30, tw, 20)
            rrect(c, theme.BG2, tr, 6)
            text(c, self.f.tiny, tag, tr.center, theme.FAINT, "center")
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
            track = pygame.Rect(W - 20, y0, 4, rows_vis * (self.CH + self.GAP) - self.GAP)
            rrect(c, theme.LINE, track, 2)
            h = max(30, track.height * rows_vis // total_rows)
            yy = track.top + (track.height - h) * self.scroll // (total_rows - rows_vis)
            rrect(c, theme.ACCENT, (track.left, yy, 4, h), 2)
