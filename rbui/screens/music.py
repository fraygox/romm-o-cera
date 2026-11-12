"""Music: which of RomM's soundtracks EmulationStation plays as its menu music."""
import pygame
import rommlib
from rommlib import human_size
from rbui import theme
from rbui.draw import card, hline, meter, rrect, scroll_text, scrollbar, state_stripe, text
from rbui.theme import W
from rbui.widgets.chips import switch
from rbui.widgets.layout import list_row
from rbui.screens.base import ListScreen
from rbui.modals import Confirm


def plural_tracks(n):
    return "{:,} track{}".format(n, "" if n == 1 else "s")


def cap_label(gb):
    return "%g GB" % gb if gb else "No limit"


def minutes(secs):
    """ "68 min", "3 h 20 min"."""
    m = int(round((secs or 0) / 60))
    return "%d min" % m if m < 100 else "%d h %02d min" % divmod(m, 60)


class MusicScreen(ListScreen):
    """Menu music: a switch, the space it may take, and the sources that feed it (your RomM
    favourites, the games on this box, RomM playlists, single games, saved mixes). Each source is
    a switch; what's switched on adds up, and the app keeps its folder in EmulationStation's music
    folder in step. The card on top says what's there and what the last sync did; the panel on
    the right, what the selected row means."""
    HEAD = pygame.Rect(32, 80, W - 64, 76)
    LIST = pygame.Rect(32, 168, 584, 0)
    ROW_H, ROWS = 70, 7

    def __init__(self, app):
        super().__init__(app)
        self.sel = self.scroll = 0
        self.sources = rommlib.load_music_sources()
        self.stats = None            # RomM's totals; {} when they couldn't be read
        self.playlists = None        # RomM's playlists; [] with lists_why set when this token may not read them
        self.lists_why = ""
        self.favourites = None       # how many tracks are starred
        self.games = None            # {rom id: RomM's music-games row}: names and counts for single games and the box
        self.here = set()            # rom ids on this box

    # ---------------------------------------------------------------- data

    def enter(self):
        app, api = self.app, self.app.api
        self.sources = rommlib.load_music_sources()
        try:
            self.here = {int(k) for k in app.library.snapshot()}
        except Exception:                                # noqa: BLE001 — a count, never an error
            self.here = set()
        app.bg(api.music_stats, lambda v: setattr(self, "stats", v), self.load_failed("stats"))
        app.bg(lambda: {g["rom_id"]: g for g in api.music_all("games") or []},
               lambda v: setattr(self, "games", v), lambda e: setattr(self, "games", {}))

        def no_lists(e):
            self.playlists, self.favourites = [], 0
            self.lists_why = str(e) if isinstance(e, rommlib.ScopeMissing) else "RomM didn't answer (%s)" % e
        app.bg(api.music_playlists, lambda v: setattr(self, "playlists", v), no_lists)
        app.bg(lambda: len(api.music_all("favorites") or []), lambda n: setattr(self, "favourites", n), lambda e: None)

    def load_failed(self, attr):
        def failed(e):
            self.load_error = str(e) or e.__class__.__name__
            setattr(self, attr, {})
        return failed

    def listed(self):
        return self.rows()

    def rows(self):
        """[(key, label, grey line, on or None, available)]: the switch and the cap, then the sources."""
        cfg, s = self.app.cfg, self.sources
        out = [("on", "Menu music", "EmulationStation plays these tracks in its menus", bool(cfg["menu_music"]), True),
               ("cap", "Space for menu music", "The most these tracks may take on the box", None, True)]
        lists = not self.lists_why
        fav = "Loading…" if self.favourites is None else "%s you starred in RomM" % plural_tracks(self.favourites)
        out.append(("favourites", "My RomM favourites", fav if lists else "Not available with this token", s["favourites"], lists))
        on_box = None if self.games is None else [g for rid, g in self.games.items() if rid in self.here]
        out.append(("on_box", "Games on this box",
                    "Loading…" if on_box is None else
                    "%d of your games here %s a soundtrack" % (len(on_box), "has" if len(on_box) == 1 else "have"),
                    s["on_box"], True))
        seen = set()
        for p in self.playlists or []:
            seen.add(p["id"])
            out.append(("playlist:%d" % p["id"], p["name"],
                        "Playlist · %s · %s" % (plural_tracks(p.get("track_count") or 0),
                                                "shared by %s" % p.get("owner_username", "someone") if p.get("is_public") else "private"),
                        p["id"] in s["playlists"], True))
        for pid in s["playlists"]:
            if pid not in seen:                       # switched on earlier; not listed now (deleted, or not readable)
                out.append(("playlist:%d" % pid, "A playlist RomM no longer lists",
                            "Not available with this token" if not lists else "Switch it off to drop its tracks", True, True))
        for rid in s["games"]:
            g = (self.games or {}).get(rid)
            out.append(("game:%d" % rid, g["name"] if g else "A game's soundtrack",
                        "Soundtrack · %s · %s" % (plural_tracks(g["count"]), g["platform_name"]) if g else
                        ("Loading…" if self.games is None else "RomM has no soundtrack for it now"), True, True))
        for m in s["mixes"]:
            out.append(("mix:" + m["name"], m["name"], "Mix · " + (mix_summary(m["filters"]) or "every track"), m["on"], True))
        return out

    # ---------------------------------------------------------------- acting

    def crumbs(self):
        return ["Music"]

    def hints(self):
        rows = self.rows()
        key = rows[min(self.sel, len(rows) - 1)][0]
        return [("A", "Change" if key == "cap" else "Switch"), ("X", "Sync now"), ("B", "Back")]

    def action(self, a):
        app = self.app
        rows = self.rows()
        if a == "ok":
            key, _, _, on, ok = rows[min(self.sel, len(rows) - 1)]
            if not ok:
                app.toast(self.lists_why or "Not available", theme.AMBER)
            elif key == "on":
                self.switch_all(not on)
            elif key == "cap":
                ch = rommlib.MUSIC_CAP_CHOICES
                cur = app.cfg["menu_music_cap_gb"]
                app.cfg["menu_music_cap_gb"] = ch[(ch.index(cur) + 1) % len(ch)] if cur in ch else ch[0]
                app.apply_config()
                app.sync_music()
            else:
                self.switch_source(key, not on)
        elif a == "x":
            if not app.cfg["menu_music"]:
                app.toast("Menu music is off: switch it on first")
            else:
                app.sync_music()
                app.toast("Checking RomM for changes…")
        elif not self.nav(a):
            super().action(a)

    def switch_all(self, on):
        app = self.app
        st = rommlib.music_status()

        def apply():
            app.cfg["menu_music"] = on
            app.apply_config()
            app.sync_music()
        if on or not st["tracks"]:
            apply()
            if on and not rommlib.music_source_keys(self.sources):
                app.toast("Now switch on what should play: favourites, a playlist, your games")
            return
        app.modal = Confirm(
            app, "Turn menu music off?",
            "%s (%s) the app placed in EmulationStation's music folder will be removed. Batocera's own songs "
            "play again once they're gone. Music of your own is never touched."
            % (plural_tracks(st["tracks"]), human_size(st["bytes"])), "Turn off", apply)

    def switch_source(self, key, on):
        s = self.sources
        kind, _, arg = key.partition(":")
        if kind in ("favourites", "on_box"):
            s[kind] = on
        elif kind in ("playlist", "game"):
            ids = s["playlists" if kind == "playlist" else "games"]
            if on and int(arg) not in ids:
                ids.append(int(arg))
            elif not on and int(arg) in ids:
                ids.remove(int(arg))
        elif kind == "mix":
            for m in s["mixes"]:
                if m["name"] == arg:
                    m["on"] = on
        try:
            rommlib.save_music_sources(s)
        except OSError as e:
            self.app.toast("That couldn't be saved (%s)" % (e.strerror or e), theme.RED)
            return
        self.clamp()
        if self.app.cfg["menu_music"]:
            self.app.sync_music()
        elif on:
            self.app.toast("Saved. Menu music is off: switch it on to hear it")

    # ---------------------------------------------------------------- drawing

    def summary(self):
        """The card's two lines: (headline, colour), (what the sync is doing or did, colour)."""
        app, m = self.app, self.app.music
        st = rommlib.music_status()
        res = m["result"] or {}
        cap = app.cfg["menu_music_cap_gb"]
        if not app.cfg["menu_music"] and not st["tracks"]:
            head = ("Menu music is off", theme.TEXT)
        else:
            head = ("%s in the menus · %s%s" % (plural_tracks(st["tracks"]), human_size(st["bytes"]),
                                               " of %s" % cap_label(cap) if cap else ""), theme.TEXT)
        if m["busy"]:
            note = ("Fetching %d of %d…" % (min(m["done"] + 1, m["total"]), m["total"]) if m["total"] else "Checking RomM…", theme.ACCENT_HI)
        elif res.get("error"):
            note = (res["error"], theme.RED)
        elif res.get("no_room"):
            note = ("Stopped: the space to keep free on the drive would be touched", theme.AMBER)
        elif res.get("left_out"):
            note = ("%s left out: over the space allowed" % plural_tracks(res["left_out"]), theme.AMBER)
        elif res.get("skipped"):
            note = ("%s in a format EmulationStation can't play" % plural_tracks(res["skipped"]), theme.DIM)
        elif app.cfg["menu_music"] and res.get("when"):
            note = ("Up to date", theme.GREEN)
        elif not app.cfg["menu_music"] and not st["tracks"]:
            note = ("Batocera's own songs play in the menus", theme.DIM)
        else:
            note = ("", theme.DIM)
        return head, note

    def draw(self, c):
        app, f = self.app, self.f
        hr = self.HEAD
        card(c, hr)
        (line, col), (note, ncol) = self.summary()
        nr = text(c, f.small, note, (hr.right - 20, hr.top + 20), ncol, "topright", maxw=hr.width // 2)
        text(c, f.h2, line, (hr.left + 20, hr.top + 12), col, maxw=(nr.left if note else hr.right) - 40 - hr.left)
        st, cap = rommlib.music_status(), rommlib.music_cap_bytes(app.cfg)
        if st["tracks"] and cap < 1 << 60:
            meter(c, (hr.left + 20, hr.bottom - 22, hr.width - 40, 8), [(min(1.0, st["bytes"] / cap), theme.ACCENT)])
        if self.load_error and self.stats == {}:
            self.draw_load_error(c, self.LIST.top + 8)
            return
        rows = self.rows()
        self.clamp()
        if self.sel < self.scroll:
            self.scroll = self.sel
        if self.sel >= self.scroll + self.ROWS:
            self.scroll = self.sel - self.ROWS + 1
        for n, (key, label, sub, on, ok) in enumerate(rows[self.scroll:self.scroll + self.ROWS]):
            i = self.scroll + n
            r = pygame.Rect(self.LIST.left, self.LIST.top + n * self.ROW_H, self.LIST.width, self.ROW_H - 6)
            app.hit(r, lambda i=i: self.tap_now(i))
            focus = i == self.sel
            status = cap_label(app.cfg["menu_music_cap_gb"]) if key == "cap" else ""
            list_row(c, f, r, focus, label, sub, status, theme.DIM, slot="music-row", box=theme.PANEL,
                     label_color=None if ok else theme.FAINT, sub_color=None if ok else theme.AMBER)
            if on is not None and ok:
                switch(c, (r.right - 76, r.centery - 13, 52, 26), on)
            state_stripe(c, r, theme.ACCENT if on and ok and (key == "on" or app.cfg["menu_music"]) else theme.LINE)
        if len(rows) > self.ROWS:
            scrollbar(c, (self.LIST.right + 6, self.LIST.top, 4, self.ROW_H * self.ROWS - 6), self.ROWS / len(rows),
                      self.scroll / max(1, len(rows) - self.ROWS))
        p = pygame.Rect(640, self.LIST.top, W - 32 - 640, self.ROW_H * self.ROWS - 6)
        rrect(c, theme.PANEL, p, 16)
        self.panel(c, p, rows[min(self.sel, len(rows) - 1)])

    def panel(self, c, p, row):
        f = self.f
        key, label, _, on, ok = row
        kind = key.partition(":")[0]
        text(c, f.h2, label, (p.left + 28, p.top + 22), theme.TEXT, maxw=p.width - 56)
        body = self.about(kind, key, ok)
        scroll_text(c, f.body, body, (p.left + 28, p.top + 72, p.width - 56, p.bottom - 84 - (p.top + 72)), theme.DIM,
                    bg=theme.PANEL, line_h=26, slot=("music-panel", key), key=key)
        hline(c, theme.LINE, (p.left + 28, p.bottom - 74), (p.right - 28, p.bottom - 74))
        st = self.stats or {}
        foot = ("RomM has %s, %s of music" % (plural_tracks(st.get("total_tracks") or 0), minutes(st.get("total_duration_seconds")))
                if st.get("total_tracks") else "Loading what RomM has…" if self.stats is None else "RomM has no soundtracks yet")
        text(c, f.body, foot, (p.left + 28, p.bottom - 38), theme.DIM, "midleft", maxw=p.width - 56)

    def about(self, kind, key, ok):
        if not ok:
            return ("%s. Favourites and playlists belong to an account, and the token decides what the box may read "
                    "of them. Mixes, single games and the games on this box work as they are." % self.lists_why.rstrip("."))
        return {
            "on": "RomM-o-Cera keeps the tracks you choose below in a folder of its own inside EmulationStation's "
                  "music folder (music/romm-o-cera), and EmulationStation plays from there at random. While any "
                  "track is there, Batocera's own songs don't play; turn this off and they return. Music of your "
                  "own in that folder is never touched.",
            "cap": "The most room these tracks take. When what you've chosen is bigger, every album keeps some of "
                   "its tracks and the card above says how many were left out. Downloads also stop before the space "
                   "to keep free on the drive (Settings, Downloads) is touched.",
            "favourites": "Every track you've starred in RomM's jukebox. Star or unstar there, and the box follows "
                          "the next time the app opens or you press Sync now.",
            "on_box": "The soundtracks of the games you've downloaded to this box. A game you delete takes its "
                      "tracks along, unless something else here still asks for them.",
            "playlist": "A playlist from RomM's jukebox: yours, or one someone shared with everyone. Change it in "
                        "RomM and the box follows.",
            "game": "This game's whole soundtrack, added from its page. It stays whether or not the game is on the box.",
            "mix": "A mix you saved: every track in RomM that fits it, so new soundtracks that fit arrive by themselves.",
        }[kind]


def mix_summary(filters):
    """A mix's filters in a line: "RPG, Racing · 1990s · up to 5 min"."""
    parts = []
    for k in ("game_genre", "genre", "artist"):
        if filters.get(k):
            parts.append(", ".join(filters[k][:3]) + ("…" if len(filters[k]) > 3 else ""))
    if filters.get("platform"):
        n = len(filters["platform"])
        parts.append("%d platform%s" % (n, "" if n == 1 else "s"))
    if filters.get("decade"):
        parts.append(", ".join("%ds" % d for d in sorted(filters["decade"])))
    if filters.get("max_minutes"):
        parts.append("up to %g min" % filters["max_minutes"])
    return " · ".join(parts)

