"""Music: which of RomM's soundtracks EmulationStation plays as its menu music."""
import pygame
import rommlib
from rommlib import human_size
from rbui import theme
from rbui.draw import card, hline, measure, meter, rrect, scroll_text, scrollbar, state_stripe, text
from rbui.theme import W
from rbui.widgets.chips import switch
from rbui.widgets.layout import list_head, list_row, split, ROWS_TOP
from rbui.screens.base import ListScreen, Screen
from rbui.screens.text_input import TextInputScreen
from rbui.modals import Confirm


def plural_tracks(n):
    return "{:,} track{}".format(n, "" if n == 1 else "s")


def cap_label(gb):
    return "%g GB" % gb if gb else "No limit"


def minutes(secs):
    """ "68 min", "3 h 20 min"."""
    m = int(round((secs or 0) / 60))
    return "%d min" % m if m < 100 else "%d h %02d min" % divmod(m, 60)


def jukebox_failed(scr, attr):
    """The err callback of load_jukebox's totals: {} for them, and the reason kept for the screen to draw."""
    def failed(e):
        scr.load_error = str(e) or e.__class__.__name__
        setattr(scr, attr, {})
    return failed


def load_jukebox(scr):
    """Ask RomM, in the background, what its jukebox holds, onto scr: stats (its totals; {}
    when they couldn't be read), games ({rom id: RomM's music-games row}), playlists and
    favourites (how many are starred); lists_why says why the last two can't be read, when
    this token may not."""
    app, api = scr.app, scr.app.api
    app.bg(api.music_stats, lambda v: setattr(scr, "stats", v), jukebox_failed(scr, "stats"))
    app.bg(lambda: {g["rom_id"]: g for g in api.music_all("games") or []},
           lambda v: setattr(scr, "games", v), lambda e: setattr(scr, "games", {}))

    def no_lists(e):
        scr.playlists, scr.favourites = [], 0
        scr.lists_why = str(e) if isinstance(e, rommlib.ScopeMissing) else "RomM didn't answer (%s)" % e
    app.bg(api.music_playlists, lambda v: setattr(scr, "playlists", v), no_lists)
    app.bg(lambda: len(api.music_all("favorites") or []), lambda n: setattr(scr, "favourites", n), lambda e: None)


class MusicScreen(ListScreen):
    """Menu music: a switch, the space it may take, and the sources that feed it (your RomM
    favourites, the games on this box, RomM playlists, single games, saved mixes). Each source is
    a switch; what's switched on adds up, and the app keeps its folder in EmulationStation's music
    folder in step. The card on top says what's there and what the last sync did; the panel on
    the right, what the selected row means."""
    HEAD = pygame.Rect(32, 80, W - 64, 82)
    LIST = pygame.Rect(32, 176, 584, 480)
    ROW_H, ROWS = 58, 7

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
        app = self.app
        self.sources = rommlib.load_music_sources()
        try:
            self.here = {int(k) for k in app.library.snapshot()}
        except Exception:                                # noqa: BLE001 — a count, never an error
            self.here = set()
        load_jukebox(self)

    def listed(self):
        return self.rows()

    def rows(self):
        """[(key, label, grey line, on or None, available)]: the switch, the sources, then the space."""
        cfg, s = self.app.cfg, self.sources
        out = [("on", "Menu music", "", bool(cfg["menu_music"]), True)]
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
        n = len(self.games or {})
        out.append(("add_game", "Add a game's soundtrack",
                    "Loading…" if self.games is None else "%d game%s in RomM %s one" % (n, "" if n == 1 else "s", "has" if n == 1 else "have"),
                    None, True))
        out.append(("new_mix", "New mix", "By game genre, music genre, artist, platform or decade", None, True))
        out.append(("cap", "Space for menu music", "The most these tracks may take on the box", None, True))
        return out

    # ---------------------------------------------------------------- acting

    def crumbs(self):
        return self.trail("Menu music")

    def hints(self):
        rows = self.rows()
        key = rows[min(self.sel, len(rows) - 1)][0]
        verb = "Change" if key == "cap" else "Open" if key in ("add_game", "new_mix") else "Switch"
        return [("A", verb)] + ([("Y", "Edit mix")] if key.startswith("mix:") else []) + [("X", "Sync now"), ("B", "Back")]

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
            elif key == "add_game":
                self.add_game()
            elif key == "new_mix":
                app.push(MixScreen(app, self))
            else:
                self.switch_source(key, not on)
        elif a == "y":
            key = rows[min(self.sel, len(rows) - 1)][0]
            mix = next((m for m in self.sources["mixes"] if "mix:" + m["name"] == key), None)
            if mix:
                app.push(MixScreen(app, self, mix))
        elif a == "x":
            if not app.cfg["menu_music"]:
                app.toast("Menu music is off: switch it on first")
            else:
                app.sync_music()
                app.toast("Checking RomM for changes…")
        elif not self.nav(a):
            super().action(a)

    def add_game(self):
        """A list of the games RomM has a soundtrack for: A adds one to the menu music."""
        app = self.app

        def load():
            return [{"value": g["rom_id"], "label": g["name"], "count": g["count"], "note": g["platform_name"]}
                    for g in sorted(app.api.music_all("games") or [], key=lambda g: g["name"].lower())
                    if g["rom_id"] not in self.sources["games"]]

        def picked(values):
            for v in values:
                self.switch_source("game:%d" % v, True)
        app.push(MusicPickScreen(app, "Add a game's soundtrack", load, [], picked, single=True,
                                 about="The game's whole soundtrack joins the menu music, whether or not the game is "
                                       "on this box. Switch it off on the Music screen to drop it again."))

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
        """The master card's two lines: (what's in the menus, colour), (what the sync is doing or did, colour)."""
        app, m = self.app, self.app.music
        st = rommlib.music_status()
        res = m["result"] or {}
        cap = app.cfg["menu_music_cap_gb"]
        if not app.cfg["menu_music"] and not st["tracks"]:
            head = ("Off. Batocera's own songs play in the menus", theme.DIM)
        else:
            head = ("%s in the menus · %s%s" % (plural_tracks(st["tracks"]), human_size(st["bytes"]),
                                               " of %s" % cap_label(cap) if cap else ""), theme.DIM)
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
        else:
            note = ("", theme.DIM)
        return head, note

    SECTIONS = {"favourites": "What plays in the menus", "cap": "Space"}     # a heading above the row with this key
    SECTION_H = 34

    def placed(self, rows, first):
        """[(row index, y)] of the rows under the master card from `first` on, as far as they fit,
        and [(heading, y)] above the ones that start a section."""
        out, heads, y = [], [], self.LIST.top
        for i in range(first, len(rows)):
            title = self.SECTIONS.get(rows[i][0])
            h = self.SECTION_H if title else 0
            if y + h + self.ROW_H - 6 > self.LIST.bottom:
                break
            if title:
                heads.append((title, y))
            out.append((i, y + h))
            y += h + self.ROW_H
        return out, heads

    def draw(self, c):
        app, f = self.app, self.f
        rows = self.rows()
        self.clamp()
        # the master switch: one card across the top
        hr = self.HEAD
        on = bool(app.cfg["menu_music"])
        card(c, hr, self.sel == 0)
        app.hit(hr, lambda: self.tap_now(0))
        switch(c, (hr.left + 22, hr.top + 18, 52, 26), on)
        (line, col), (note, ncol) = self.summary()
        nr = text(c, f.small, note, (hr.right - 22, hr.top + 20), ncol, "topright", maxw=hr.width // 2)
        text(c, f.h2, "Menu music", (hr.left + 92, hr.top + 10), theme.TEXT, maxw=(nr.left if note else hr.right) - 112 - hr.left)
        st, cap = rommlib.music_status(), rommlib.music_cap_bytes(app.cfg)
        bar = st["tracks"] and cap < 1 << 60
        text(c, f.small, line, (hr.left + 92, hr.top + 46), col, maxw=hr.width - 114 - (260 if bar else 0))
        if bar:
            meter(c, (hr.right - 22 - 240, hr.top + 56, 240, 8), [(min(1.0, st["bytes"] / cap), theme.ACCENT)])
        if self.load_error and self.stats == {}:
            self.draw_load_error(c, self.LIST.top + 8)
            return
        first = max(1, self.scroll)
        if 1 <= self.sel < first:
            first = self.sel
        placed, heads = self.placed(rows, first)
        while self.sel >= 1 and placed and self.sel > placed[-1][0]:
            first += 1
            placed, heads = self.placed(rows, first)
        self.scroll = first
        for title, y in heads:
            text(c, f.small, title, (self.LIST.left + 4, y + 8), theme.ACCENT_HI)
            hline(c, theme.LINE, (self.LIST.left + 12 + measure(f.small, title), y + 18), (self.LIST.right, y + 18))
        for i, y in placed:
            key, label, sub, on, ok = rows[i]
            r = pygame.Rect(self.LIST.left, y, self.LIST.width, self.ROW_H - 6)
            app.hit(r, lambda i=i: self.tap_now(i))
            status = cap_label(app.cfg["menu_music_cap_gb"]) if key == "cap" else ""
            list_row(c, f, r, i == self.sel, label, sub, status, theme.TEXT if i == self.sel else theme.DIM, slot="music-row",
                     box=theme.PANEL, label_color=None if ok else theme.FAINT, sub_color=None if ok else theme.AMBER,
                     chevron=key in ("add_game", "new_mix"))
            if on is not None and ok:
                switch(c, (r.right - 74, r.centery - 13, 52, 26), on)
        n = len(rows) - 1
        if len(placed) < n:
            scrollbar(c, (self.LIST.right + 6, self.LIST.top, 4, self.LIST.height), len(placed) / n, (first - 1) / max(1, n - len(placed)))
        p = pygame.Rect(640, self.LIST.top, W - 32 - 640, self.LIST.height)
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
            "mix": "A mix you saved: every track in RomM that fits it, so new soundtracks that fit arrive by "
                   "themselves. {Y} changes or deletes it.",
            "add_game": "Pick a game from the ones RomM has a soundtrack for. Its page in the game lists offers the "
                        "same.",
            "new_mix": "A mix is every track that fits what you pick: game genres, music genres, artists, platforms, "
                       "decades and a longest length. It's saved on this box and switched like any other source.",
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



# ---------------------------------------------------------------- picking values, and mixes

MIX_LENGTHS = (0, 2, 3, 5, 8, 12)        # a mix's longest track, minutes; 0: any length


class MusicPickScreen(ListScreen):
    """Rows with a count each, ticked with A: the values of one facet of RomM's jukebox (a
    mix's game genres, artists...), or, single, a list one row is chosen from. load() gives
    [{"value", "label", "count", "note"}] in the background; done(values) gets what's ticked
    when the screen is left (single: the row chosen, at once)."""
    PICKS = PICK_ALWAYS = True
    PICK_ALL = False
    ROWS, ROW_H = 9, 56

    def __init__(self, app, title, load, chosen, done, about="", single=False):
        super().__init__(app)
        self.title, self.load, self.done, self.about, self.single = title, load, done, about, single
        self.chosen = list(chosen)
        self.items = None
        self.sel = self.scroll = 0

    def enter(self):
        if self.items is not None:
            return

        def got(rows):
            self.items = rows
            self.picks.update((r["value"], r) for r in rows if r["value"] in self.chosen)
        self.app.bg(self.load, got, self.load_failed("items"))

    def pick_key(self, row):
        return row["value"]

    def showing_picks(self):
        return not self.single

    def crumbs(self):
        return self.trail(self.title)

    def hints(self):
        if self.single:
            return self.list_hints(("A", "Add"))
        return self.list_hints(("A", "Pick"), back="Done")

    def list_hints(self, *own, back="Back"):
        own = list(own)
        if len(self.listed()) > self.ROWS:
            own += [("L", "Page up"), ("R", "Page down")]
        return own + [("B", back)]

    def action(self, a):
        rows = self.listed()
        if a == "ok" and rows:
            if self.single:
                self.app.pop()
                self.done([rows[self.sel]["value"]])
            else:
                self.toggle_pick(move=False)
        elif a == "back":
            if not self.single and self.items is not None:
                self.done([r["value"] for r in self.picked()])
            self.app.pop()
        elif not self.nav(a):
            super().action(a)

    def tap_row(self, i):
        self.sel = i
        self.action("ok")

    def draw(self, c):
        f = self.f
        lst, pan = split()
        rows = self.listed()
        n = self.pick_count()
        list_head(c, f, lst, self.title, "" if self.single else "%d picked" % n if n else "None picked: any will do")
        if self.draw_load_error(c, ROWS_TOP + 8):
            return
        if self.items is None:
            text(c, f.body, "Loading…", (lst.left + 8, ROWS_TOP + 12), theme.DIM)
        elif not rows:
            text(c, f.body, "Nothing to pick here", (lst.left + 8, ROWS_TOP + 12), theme.DIM)
        self.clamp()
        for i in self.visible():
            row = rows[i]
            r = pygame.Rect(lst.left, ROWS_TOP + (i - self.scroll) * self.ROW_H, lst.width, self.ROW_H - 6)
            self.app.hit(r, lambda i=i: self.tap_row(i))
            list_row(c, f, r, i == self.sel, row["label"], row.get("note") or "", plural_tracks(row["count"]), theme.DIM,
                     slot="music-pick", mark=(lambda ctr, row=row: self.draw_pick(c, row, ctr)) if not self.single else None)
        if len(rows) > self.ROWS:
            scrollbar(c, (lst.right + 6, ROWS_TOP, 4, self.ROW_H * self.ROWS - 6), self.ROWS / len(rows),
                      self.scroll / max(1, len(rows) - self.ROWS))
        rrect(c, theme.PANEL, pan, 16)
        text(c, f.h2, self.title, (pan.left + 28, pan.top + 22), theme.TEXT, maxw=pan.width - 56)
        scroll_text(c, f.body, self.about, (pan.left + 28, pan.top + 72, pan.width - 56, pan.height - 100), theme.DIM,
                    bg=theme.PANEL, line_h=26, slot="music-pick-about")


class MixScreen(Screen):
    """A mix being made or changed: its name, a row per facet (each opens its values to tick), the
    longest track, Save and, for one that exists, Delete. The card on top counts the tracks that
    fit as the picks change."""
    HEAD = MusicScreen.HEAD
    LIST = MusicScreen.LIST
    ROW_H = 54
    FACETS = (("game_genre", "Game genre", "game-genres", "Tracks from games of these genres"),
              ("genre", "Music genre", "genres", "Tracks tagged with these music genres"),
              ("artist", "Artist", "artists", "Tracks by these artists"),
              ("platform", "Platform", "platforms", "Tracks from games on these platforms"),
              ("decade", "Decade", "years", "Tracks from these decades"))

    def __init__(self, app, music, mix=None):
        super().__init__(app)
        self.music = music                       # the Music screen: its sources are saved from here
        self.was = mix["name"] if mix else None  # the mix being changed
        self.name = mix["name"] if mix else ""   # "": named after what's picked
        self.on = mix["on"] if mix else True
        self.filters = {k: list(v) if isinstance(v, list) else v for k, v in (mix["filters"] if mix else {}).items()}
        self.sel = 0
        self.count = None                        # (tracks, seconds) that fit; None while counting
        self.gen = 0
        self.names = {}                          # platform id -> name, for the rows' values

    def enter(self):
        self.recount()

    def title(self):
        return self.name or mix_summary(self.filters) or "Everything"

    def rows(self):
        f = self.filters
        out = [("name", "Name", self.title() if self.name else "%s (named after what you pick)" % self.title())]
        for key, label, _, _ in self.FACETS:
            v = f.get(key) or []
            shown = [self.names.get(x, "Platform %s" % x) for x in v] if key == "platform" else \
                ["%ds" % x for x in sorted(v)] if key == "decade" else v
            out.append((key, label, ", ".join(shown) if v else "Any"))
        out.append(("length", "Longest track", "%g min" % f["max_minutes"] if f.get("max_minutes") else "Any length"))
        out.append(("save", "Save mix", "It joins the sources on the Music screen, switched %s" % ("on" if self.on else "off")))
        if self.was:
            out.append(("delete", "Delete mix", "Its tracks leave the menus unless another source asks for them"))
        return out

    def crumbs(self):
        return self.trail("Change mix" if self.was else "New mix")

    def hints(self):
        key = self.rows()[self.sel][0]
        verb = {"save": "Save", "delete": "Delete", "length": "Change", "name": "Edit"}.get(key, "Pick")
        return [("A", verb), ("B", "Back")]

    def recount(self):
        self.gen += 1
        gen, f, api = self.gen, dict(self.filters), self.app.api
        self.count = None

        def got(tracks):
            if gen == self.gen:
                self.count = (len(tracks or []), sum(t.get("duration_seconds") or 0 for t in tracks or []))
        self.app.bg(lambda: rommlib.music_mix_tracks(api, f), got, lambda e: got([]) or self.app.toast(str(e), theme.RED))

    def facet_rows(self, key, what):
        """A facet's values with their track counts, as MusicPickScreen lists them."""
        api = self.app.api
        got = api.music_all(what) or []
        if key == "platform":
            self.names.update({p["id"]: p["name"] for p in got})
            return [{"value": p["id"], "label": p["name"], "count": p["count"]} for p in got]
        if key == "decade":
            tens = {}
            for y in got:
                if isinstance(y.get("value"), int):
                    tens[y["value"] // 10 * 10] = tens.get(y["value"] // 10 * 10, 0) + y["count"]
            return [{"value": d, "label": "%ds" % d, "count": n} for d, n in sorted(tens.items())]
        return [{"value": x["value"], "label": str(x["value"]), "count": x["count"]}
                for x in sorted(got, key=lambda x: str(x["value"]).lower())]

    def action(self, a):
        app = self.app
        rows = self.rows()
        if a in ("up", "down"):
            self.sel = (self.sel + (1 if a == "down" else -1)) % len(rows)
        elif a == "ok":
            key = rows[self.sel][0]
            facet = next((x for x in self.FACETS if x[0] == key), None)
            if facet:
                def picked(values, key=key):
                    self.filters[key] = values
                    if not values:
                        del self.filters[key]
                    self.recount()
                app.push(MusicPickScreen(app, facet[1], lambda: self.facet_rows(key, facet[2]),
                                         self.filters.get(key) or [], picked,
                                         about=facet[3] + ". Tick as many as you like: a track fits when it matches any "
                                                          "one of them. None ticked means any."))
            elif key == "name":
                app.push(TextInputScreen(app, "Name this mix", self.name, self.rename,
                                         help="What the mix is called on the Music screen. Leave it empty to name it "
                                              "after what you pick."))
            elif key == "length":
                cur = self.filters.get("max_minutes") or 0
                nxt = MIX_LENGTHS[(MIX_LENGTHS.index(cur) + 1) % len(MIX_LENGTHS)] if cur in MIX_LENGTHS else 0
                if nxt:
                    self.filters["max_minutes"] = nxt
                else:
                    self.filters.pop("max_minutes", None)
                self.recount()
            elif key == "save":
                self.save()
            elif key == "delete":
                app.modal = Confirm(app, "Delete this mix?",
                                    "\"%s\" goes from the Music screen. Its tracks leave the menus at the next sync, "
                                    "unless another source still asks for them." % self.was, "Delete", self.delete, danger=True)
        else:
            super().action(a)

    def rename(self, value):
        self.name = (value or "").strip()
        self.app.pop()

    def others(self):
        return [m for m in self.music.sources["mixes"] if m["name"] != self.was]

    def save(self):
        app, name = self.app, self.title()
        if name.lower() in {m["name"].lower() for m in self.others()}:
            app.toast("Another mix is called \"%s\": give this one a name of its own" % name, theme.AMBER)
            return
        mix = rommlib.music_mix({"name": name, "on": self.on, "filters": self.filters})
        mixes = self.music.sources["mixes"]
        at = next((i for i, m in enumerate(mixes) if m["name"] == self.was), None)
        if at is None:
            mixes.append(mix)
        else:
            mixes[at] = mix
        self.store("Saved \"%s\"" % name)

    def delete(self):
        self.music.sources["mixes"] = self.others()
        self.store("Deleted \"%s\"" % self.was)

    def store(self, said):
        app = self.app
        try:
            rommlib.save_music_sources(self.music.sources)
        except OSError as e:
            app.toast("That couldn't be saved (%s)" % (e.strerror or e), theme.RED)
            return
        app.pop()
        app.toast(said, theme.GREEN)
        if app.cfg["menu_music"]:
            app.sync_music()

    def draw(self, c):
        app, f = self.app, self.f
        hr = self.HEAD
        card(c, hr)
        if self.count is None:
            line = "Counting what fits…"
        else:
            n, secs = self.count
            size = secs * rommlib.music_rate(rommlib.load_music_state()["tracks"])
            line = "%s fit%s · %s · about %s" % (plural_tracks(n), "s" if n == 1 else "", minutes(secs), human_size(size)) \
                if n else "Nothing in RomM fits this yet"
        text(c, f.h2, line, (hr.left + 20, hr.top + 12), theme.TEXT, maxw=hr.width - 40)
        text(c, f.small, "A track fits when it matches one pick in every row that has any", (hr.left + 20, hr.bottom - 28),
             theme.FAINT, maxw=hr.width - 40)
        rows = self.rows()
        self.sel = min(self.sel, len(rows) - 1)
        for i, (key, label, value) in enumerate(rows):
            r = pygame.Rect(self.LIST.left, self.LIST.top + i * self.ROW_H, self.LIST.width, self.ROW_H - 6)
            app.hit(r, lambda i=i: self.tap_now(i))
            list_row(c, f, r, i == self.sel, label, value, slot="mix-row", box=theme.PANEL,
                     label_color=theme.RED if key == "delete" else None, chevron=key not in ("save", "delete", "length"))
            state_stripe(c, r, theme.ACCENT if key == "save" else theme.LINE)
        p = pygame.Rect(640, self.LIST.top, W - 32 - 640, self.LIST.height)
        rrect(c, theme.PANEL, p, 16)
        text(c, f.h2, self.title(), (p.left + 28, p.top + 22), theme.TEXT, maxw=p.width - 56)
        scroll_text(c, f.body,
                    "A mix is every track in RomM that fits what you pick here, so soundtracks added later that fit "
                    "arrive by themselves. Pick nothing in a row and it doesn't narrow the mix. Mixes are saved on "
                    "this box.", (p.left + 28, p.top + 72, p.width - 56, p.height - 100), theme.DIM, bg=theme.PANEL,
                    line_h=26, slot="mix-about")
