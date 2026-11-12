"""Music: RomM's soundtracks on shelves you can regroup (JukeboxScreen), one shelf as a wall
(AlbumWallScreen), search (MusicSearchScreen), and a list of tracks with what's playing beside it
(TracksScreen). The playing itself is rbui/player.py."""
import pygame
import rommlib
from rommlib import human_size
from rbui import draw, theme
from rbui.theme import SR, W
from rbui.draw import (_fade, button_glyph, card, cover_fit, marquee, measure, play_mark, progress_bar, rrect,
                       scroll_text, scrollbar, text, wrap)
from rbui.player import track_title
from rbui.screens.base import letter_jump, ListScreen, Screen
from rbui.screens.music import load_jukebox, minutes, mix_summary, MusicScreen, plural_tracks
from rbui.screens.text_input import TextInputScreen
from rbui.widgets.layout import hint_actions, list_head, list_row, Panel, split, ROWS_TOP
from rbui.widgets.tiles import art_variant, crop_to, fit_in, focus_ring, mosaic, placeholder, Shelf

STATE_WORDS = {"loading": "Loading…", "playing": "Now playing", "paused": "Paused", "stopped": "Stopped"}
COLUMN, SIDE = split(702, pygame.Rect(48, 84, W - 96, 572), 24)     # the shelves (or a wall), and the focused thing
PILL_H, VIEW_H, HEAD_H = 38, 32, 30
SHELF_TILE, TILE_GAP, SHELF_GAP = (90, 120), 12, 14   # a shelf: covers, seven across
LIST_FOOT = 40                                 # your lists: a mosaic with a name and a count under it
WALL_PER, WALL_GAP = 6, 12                     # a wall: covers, six a row
FOCUS_WAIT = 0.35                              # seconds a tile keeps the focus before its tracks are asked for
RECENT = 14                                    # soundtracks on the Recently added shelf
VIEWS = (("system", "Systems"), ("genre", "Game genres"), ("composer", "Composers"), ("decade", "Decades"), ("letter", "A–Z"))
VIEW_ABOUT = {"system": "One shelf for each system, the biggest first.",
              "genre": "One shelf for each kind of game. A game sits on every shelf that fits it.",
              "composer": "One shelf for each composer or group that most of a soundtrack is by.",
              "decade": "One shelf for each decade the games came out in.",
              "letter": "Every soundtrack by name, a shelf a letter."}
SORTS = (("name", "A–Z"), ("added", "Newest in RomM"), ("length", "Longest"), ("year", "Oldest game"))
SEARCH_TRACKS = 200                            # the most tracks a search lists


def clock(secs):
    """ "3:42", "1:02:03"."""
    s = int(round(secs or 0))
    return "%d:%02d:%02d" % (s // 3600, s % 3600 // 60, s % 60) if s >= 3600 else "%d:%02d" % (s // 60, s % 60)


def album_order(tracks):
    """One game's tracks as its album runs: by disc, then track number, then file name."""
    return sorted(tracks or [], key=lambda t: (t.get("disc") or 0, t.get("track") or 0, (t.get("file_name") or "").lower()))


def ost_art(app, rom_id, url):
    """(key, image) of the cover RomM gives for a game's soundtrack, in app.art's store; the image
    is None while it loads or when there's none."""
    key = ("ost", rom_id)
    return key, app.art(key, lambda: app.api.fetch_asset(url, "ost-%s" % rom_id), cover_fit)


def album_art(c, app, rom_id, url, rect, name="", fit=True):
    """A soundtrack's cover in rect (logical): whole and centred (fit), or filling it, cropped.
    A quiet box with the name while it loads or when there's none. Returns the rect it took."""
    rect = pygame.Rect(rect)
    pr = SR(rect)
    key, src = ost_art(app, rom_id, url)
    img = art_variant(app, key, src, ("fit" if fit else "crop", pr.size),
                      lambda im: (fit_in if fit else crop_to)(im, pr.size))
    if img:
        r = img.get_rect(center=pr.center)
        c.blit(img, r)
        return pygame.Rect(round(r.left / theme.K), round(r.top / theme.K), round(r.width / theme.K), round(r.height / theme.K))
    placeholder(c, app.fonts, name, rect)
    return rect


def covers_of(app, tracks, n=4):
    """Up to n (key, image) cells for a mosaic: the covers of the first games a list's tracks are from."""
    seen, out = set(), []
    for t in tracks or []:
        if t.get("rom_id") not in seen and len(out) < n:
            seen.add(t.get("rom_id"))
            out.append(ost_art(app, t.get("rom_id"), t.get("game_cover_url") or t.get("cover_url")))
    return out


def album_item(a):
    """A soundtrack of the index (rommlib.build_music_index), or a row of RomM's /music/games, as the
    thing the walls show: its key, name, system, cover and what's known of its length, year and composer."""
    return {"key": ("game", a["rom_id"]), "rom_id": a["rom_id"], "name": a.get("name") or "",
            "kind": a.get("platform") or a.get("platform_name") or "", "cover": a.get("cover") or a.get("cover_url"),
            "count": a.get("count") or 0, "seconds": a.get("seconds"), "year": a.get("year"), "artist": a.get("artist") or "",
            "album": True}


class AlbumFocus:
    """What the screens showing soundtracks as covers share: the focused thing's tracks (asked for
    once it has kept the focus a moment), opening and playing it, its tile and its panel."""

    def init_focus(self):
        self.tracks = {}                           # key -> its tracks ("loading" while asked for)
        self.focus_since = (None, 0.0)

    def loader(self, it):
        if it.get("load"):
            return it["load"]
        if it.get("album"):
            api, rid = self.app.api, it["rom_id"]
            return lambda: album_order(api.music_all("tracks", rom_id=rid))
        return None

    def ask_tracks(self, it):
        key, load = it["key"], self.loader(it)
        if load and self.tracks.get(key) is None:
            self.tracks[key] = "loading"
            self.app.bg(load, lambda v, key=key: self.tracks.__setitem__(key, list(v or [])),
                        lambda e, key=key: self.tracks.__setitem__(key, []))

    def tracks_of(self, it):
        """The focused thing's tracks, asked for once it has kept the focus a moment; None until they're in."""
        key = it["key"]
        got = self.tracks.get(key)
        if got is None and self.loader(it):
            if self.focus_since[0] != key:
                self.focus_since = (key, draw.NOW)
            elif draw.NOW - self.focus_since[1] >= FOCUS_WAIT:
                self.ask_tracks(it)
        return got if isinstance(got, list) else None

    def open_item(self, it, play=False):
        """Its list of tracks in front; play: playing from the top (shuffled, for Shuffle everything)."""
        app, load = self.app, self.loader(it)
        if load:
            app.push(TracksScreen(app, it.get("title") or it["name"], load, it["key"], shuffle=it.get("shuffle", False),
                                  autoplay=play or it.get("shuffle", False)))

    def album_tile(self, c, it, r, on):
        app = self.app
        if on:
            focus_ring(c, r)
        album_art(c, app, it["rom_id"], it.get("cover"), r, it["name"], fit=False)
        if app.player.active() and app.player.key == it["key"]:       # the one that's playing: a small mark on its cover
            m = pygame.Rect(r.left + 5, r.bottom - 27, 22, 22)
            rrect(c, theme.ACCENT, m, 11)
            play_mark(c, theme.ink_on(theme.ACCENT), m.center, paused=app.player.state == "paused", size=8)
        return r

    def list_tile(self, c, li, r, on):
        """A list as a tile: a mosaic of the covers its tracks are from, its name and how much it holds."""
        f = self.f
        art = pygame.Rect(r.left, r.top, r.width, r.width)
        if on:
            focus_ring(c, art)
        self.ask_tracks(li)                        # few and small: asked for as soon as they're drawn
        got = self.tracks.get(li["key"])
        mosaic(c, self.app, covers_of(self.app, got if isinstance(got, list) else None), art)
        marquee(c, f.small, li["name"], (r.left, art.bottom + 4), theme.TEXT if on else theme.DIM, maxw=r.width, focused=on,
                slot=("list-tile", str(li["key"])), bg=theme.BG)
        n = li["count"] if li.get("count") is not None else len(got) if isinstance(got, list) else None
        text(c, f.tiny, plural_tracks(n) if n is not None else li["kind"], (r.left, art.bottom + 24), theme.FAINT, maxw=r.width)
        return art

    def item_panel(self, c, it, actions=("X",)):
        """The panel for a soundtrack or a list: its cover (or a mosaic), what it holds and its first tracks."""
        app = self.app
        p = Panel(c, self.f, SIDE, app=app, key=str(it["key"]), slot="music-panel")
        p.actions(hint_actions(self, actions))
        tracks = self.tracks_of(it)
        secs = sum(t.get("duration_seconds") or 0 for t in tracks) if tracks is not None else it.get("seconds")
        count = len(tracks) if tracks is not None else it.get("count")
        p.verb("Play" if it.get("shuffle") else "Open", "go", plural_tracks(count) if count else "", fn=lambda: self.action("ok"))
        h = max(120, min(224, p.room() - 250))
        if it.get("album"):
            album_art(c, app, it["rom_id"], it.get("cover"), (p.x, p.y, p.w, h), it["name"])
        else:
            mosaic(c, app, covers_of(app, tracks), (p.x + (p.w - h) // 2, p.y, h, h))
        p.space(h + 14)
        artists = sorted({t.get("artist") for t in tracks or [] if t.get("artist")})
        years = sorted({t["year"] for t in tracks or [] if isinstance(t.get("year"), int)})
        year = years[0] if years else it.get("year")
        if it.get("album"):
            sub = " · ".join(x for x in (it["kind"], str(year) if year else "") if x)
        else:
            sub = it.get("about") or it.get("kind") or "Every soundtrack in RomM, in a new order each time"
        p.title(it.get("title") or it["name"], sub=sub, sub_color=theme.FAINT, center=True)
        p.y -= 6
        by = artists[0] if len(artists) == 1 else "%d artists" % len(artists) if artists else it.get("artist")
        if by:
            p.pair("By", by)
        if count:
            p.pair("Length", "%s · %s" % (plural_tracks(count), minutes(secs)) if secs else plural_tracks(count))
        if tracks is None:
            if self.loader(it) and not count:
                p.line("Loading…", theme.FAINT)
            return
        if it.get("shuffle"):
            return
        p.space(6)
        p.rule()
        for t in tracks[:max(0, int(p.room() // 24))]:       # the first tracks, as far as there's room
            p.pair(track_title(t), clock(t["duration_seconds"]) if t.get("duration_seconds") else "", theme.FAINT, theme.DIM)


class MusicSearchInput(TextInputScreen):
    def crumbs(self):
        return self.trail("Search")


class JukeboxScreen(AlbumFocus, Screen):
    """Music, from Home. Pills on top (what's playing, shuffle everything, search, menu music), then
    Browse by: how the shelves below are grouped (system, game genre, composer, decade, name). The
    shelves: your lists (RomM favourites, playlists, saved mixes), what's new, what's on this box,
    then one shelf for each group, covers side by side. A opens what's focused, X plays it, Y shows
    the focused shelf as a wall; L and R change the grouping from anywhere. The panel shows the
    focused thing. Groupings other than by system wait for the jukebox index (read once in the
    background and kept), since RomM's list of soundtracks only says which system each is on."""

    def __init__(self, app):
        super().__init__(app)
        self.init_focus()
        self.at = None                             # the focused row's key; None: the first shelf, once there is one
        self.cols = {}                             # row key -> the focused thing along it
        self.off = 0                               # pixels scrolled
        self.view = min(getattr(app, "music_view", 0), len(VIEWS) - 1)
        self.sources = rommlib.load_music_sources()
        self.stats = self.playlists = self.favourites = self.games = None
        self.lists_why = ""
        self.index, self.index_asked, self.index_error = None, False, ""
        self.here = set()                          # rom ids on this box
        self.shelf_of, self.cached = {}, (None, None)

    def enter(self):
        app = self.app
        self.sources = rommlib.load_music_sources()
        try:
            self.here = {int(k) for k in app.library.snapshot()}
        except Exception:                                # noqa: BLE001 — a shelf, never an error
            self.here = set()
        if self.games is None:
            load_jukebox(self)
        if not self.index_asked:
            self.index_asked = True
            self.index = rommlib.load_music_index()      # last time's, at once; replaced if RomM has changed
            api = app.api

            def read():
                stats = api.music_stats()
                kept = rommlib.load_music_index()
                return kept if kept and kept.get("stats") == stats else rommlib.build_music_index(api, stats)

            def failed(e):
                self.index_error = str(e)
            app.bg(read, lambda v: setattr(self, "index", v or self.index), failed)

    # ---------------------------------------------------------------- what's on it

    def pills(self):
        app, pl = self.app, self.app.player
        st = self.stats or {}
        out = []
        if pl.queue and pl.current():
            out.append({"key": "now", "label": "Now playing", "dot": theme.ACCENT_HI if pl.state == "playing" else theme.FAINT})
        out.append({"key": ("all",), "label": "Shuffle everything", "name": "Shuffle everything", "title": "Everything",
                    "shuffle": True, "load": lambda: app.api.music_all("tracks"), "count": st.get("total_tracks") or 0,
                    "seconds": st.get("total_duration_seconds") or 0})
        out.append({"key": "search", "label": "Search"})
        out.append({"key": "menu", "label": "Menu music: %s" % ("On" if app.cfg["menu_music"] else "Off"),
                    "dot": theme.GREEN if app.cfg["menu_music"] else None})
        return out

    def lists(self):
        api, out = self.app.api, []
        if not self.lists_why:
            if self.favourites:
                out.append({"key": ("favourites",), "name": "Favourites", "kind": "Starred in RomM",
                            "count": self.favourites, "load": lambda: api.music_all("favorites")})
            for p in self.playlists or []:
                out.append({"key": ("playlist", p["id"]), "name": p["name"], "count": p.get("track_count") or 0,
                            "kind": "Playlist by %s" % p.get("owner_username", "someone") if p.get("is_public") else "Your playlist",
                            "load": lambda pid=p["id"]: api.music_all("playlists/%d/tracks" % pid)})
        for m in self.sources["mixes"]:
            out.append({"key": ("mix", m["name"]), "name": m["name"], "kind": "Mix", "count": None,
                        "about": mix_summary(m["filters"]) or "every track",
                        "load": lambda f=m["filters"]: rommlib.music_mix_tracks(api, f)})
        return out

    def albums(self):
        """Every soundtrack as the index has it; before the index is in, as RomM's list of games gives it."""
        if self.index:
            return self.index["albums"]
        return [{"rom_id": g["rom_id"], "name": g.get("name") or "", "platform": g.get("platform_name") or "",
                 "cover": g.get("cover_url"), "count": g.get("count") or 0} for g in (self.games or {}).values()]

    def shelves(self):
        """The shelves under Browse by, as rows: {"key", "kind" ("shelf", or "note": a line that isn't
        focused), "label", "items", "tile"}. Worked out again only when what they're made of changes."""
        by = VIEWS[self.view][0]
        sig = (id(self.index), id(self.games), by, self.favourites, id(self.playlists), self.lists_why,
               tuple(m["name"] for m in self.sources["mixes"]), len(self.here), self.index_error)
        if self.cached[0] == sig:
            return self.cached[1]
        out, albums = [], self.albums()
        lists = self.lists()
        if lists:
            out.append({"key": "lists", "kind": "shelf", "label": "Your lists", "items": lists, "tile": "list"})
        if self.index and len(albums) > RECENT:
            recent = [a for a in rommlib.music_sorted(albums, "added") if a.get("added")][:RECENT]
            if recent:
                out.append({"key": "recent", "kind": "shelf", "label": "Recently added", "albums": recent})
        here = [a for a in albums if a["rom_id"] in self.here]
        if here:
            out.append({"key": "here", "kind": "shelf", "label": "On this box", "albums": rommlib.music_sorted(here)})
        if by != "system" and not self.index:
            out.append({"key": "wait", "kind": "note", "label": self.index_error or "Reading RomM's jukebox…"})
        else:
            for label, group in rommlib.music_groups(albums, by):
                out.append({"key": ("group", by, label), "kind": "shelf", "label": label, "albums": group})
            if not albums:
                out.append({"key": "wait", "kind": "note", "label": "Loading…" if self.games is None else "No soundtracks in RomM yet"})
        for row in out:
            if "albums" in row:
                row.update(items=[album_item(a) for a in row["albums"]], tile="album")
        self.cached = (sig, out)
        return out

    def rows(self):
        return [{"key": "pills", "kind": "pills", "items": self.pills()},
                {"key": "views", "kind": "views", "items": [{"key": ("view", k), "label": lab} for k, lab in VIEWS]}] + self.shelves()

    def where(self, rows=None):
        """(rows, the focused row's index, the focused thing along it), the focus mended when its row has gone."""
        rows = rows or self.rows()
        i = next((k for k, r in enumerate(rows) if r["key"] == self.at), None)
        if i is None:
            i = next((k for k, r in enumerate(rows) if isinstance(r["key"], tuple)), None)        # a group's shelf
            if i is None:
                i = next((k for k, r in enumerate(rows) if r["kind"] == "shelf"), 0)
            if self.at is not None or rows[i]["kind"] == "shelf":
                self.at = rows[i]["key"]
        row = rows[i]
        col = self.view if row["kind"] == "views" else max(0, min(self.cols.get(row["key"], 0), len(row["items"]) - 1))
        return rows, i, col

    def current(self):
        rows, i, col = self.where()
        return rows[i], (rows[i]["items"][col] if rows[i]["items"] else None)

    def set_view(self, v):
        self.view = self.app.music_view = v % len(VIEWS)
        if isinstance(self.at, tuple):
            self.at = "views" if self.at[0] == "view" else None      # a group's shelf: onto the new grouping's first

    # ---------------------------------------------------------------- acting

    def crumbs(self):
        return ["Music"]

    def nav_key(self):
        return self.at, tuple(sorted((str(k), v) for k, v in self.cols.items())), self.view

    def hints(self):
        row, it = self.current()
        if row["kind"] == "views":
            return [("LEFT RIGHT", "Browse by"), ("B", "Back")]
        if row["kind"] == "pills":
            return [("A", "Play" if it and it["key"] == ("all",) else "Open"), ("L R", "Browse by"), ("B", "Back")]
        own = [("A", "Open"), ("X", "Play")] + ([("Y", "See all")] if row.get("tile") == "album" else [])
        return own + [("L R", "Browse by"), ("B", "Back")]

    def action(self, a):
        app = self.app
        row, it = self.current()
        if a in ("ok", "x") and it:
            key = it["key"]
            if row["kind"] == "views":
                pass                                # left and right have chosen already
            elif key == "now":
                app.open_player()
            elif key == "menu":
                app.push(MusicScreen(app))
            elif key == "search":
                app.push(MusicSearchInput(app, "Search music", "", self.search, primary="Search",
                                          help="Finds soundtracks by the game's name, composers, and tracks by their "
                                               "title, artist or album."))
            elif a == "ok" or row["kind"] == "shelf":
                self.open_item(it, play=a == "x")
        elif a == "y" and row.get("tile") == "album":
            app.push(AlbumWallScreen(app, row["label"], row["albums"]))
        elif a in ("l1", "r1"):
            self.set_view(self.view + (1 if a == "r1" else -1))
        elif a in ("left", "right", "up", "down"):
            self.move(a)
        else:
            super().action(a)

    def search(self, value):
        app = self.app
        app.pop()
        if (value or "").strip():
            app.push(MusicSearchScreen(app, value.strip(), {"albums": self.albums()}))

    def move(self, a):
        rows, i, col = self.where()
        row = rows[i]
        if a in ("left", "right"):
            col += 1 if a == "right" else -1
            if row["kind"] == "views":
                if 0 <= col < len(VIEWS):
                    self.set_view(col)
            else:
                self.cols[row["key"]] = max(0, min(col, len(row["items"]) - 1))
            return
        d = 1 if a == "down" else -1
        j = i + d
        while 0 <= j < len(rows) and rows[j]["kind"] == "note":
            j += d
        if 0 <= j < len(rows):
            self.at = rows[j]["key"]

    def tap(self, key, i):
        rows, at, col = self.where()
        row = next((r for r in rows if r["key"] == key), None)
        if row is None:
            return
        if row["kind"] == "views":
            self.at = key
            self.set_view(i)
            self.at = "views"
            return
        same = rows[at]["key"] == key and col == i
        self.at = key
        self.cols[key] = i
        if same or row["kind"] == "pills":
            self.action("ok")

    # ---------------------------------------------------------------- drawing

    def row_h(self, row):
        if row["kind"] == "pills":
            return PILL_H + 10
        if row["kind"] == "views":
            return VIEW_H + 16
        if row["kind"] == "note":
            return 56
        return HEAD_H + (SHELF_TILE[0] + LIST_FOOT if row["tile"] == "list" else SHELF_TILE[1]) + SHELF_GAP

    def draw(self, c):
        app, f = self.app, self.f
        if self.load_error and self.stats == {}:
            self.draw_load_error(c)
            return
        rows, at, col = self.where()
        ys, y = [], 0
        for row in rows:
            ys.append(y)
            y += self.row_h(row)
        total = y - SHELF_GAP
        top, bottom = ys[at], ys[at] + self.row_h(rows[at]) - (SHELF_GAP if rows[at]["kind"] == "shelf" else 0)
        if at <= 2 and bottom <= COLUMN.height:
            top = 0                                    # the first shelf fits under the pills: show them with it
        self.off = max(bottom - COLUMN.height, min(self.off, top))
        self.off = max(0, min(self.off, max(0, total - COLUMN.height)))
        area = pygame.Rect(COLUMN.left - 8, COLUMN.top - 6, COLUMN.width + 16, COLUMN.height + 6)
        old = c.get_clip()
        c.set_clip(SR(area).clip(old) if old else SR(area))
        for k, row in enumerate(rows):
            y = COLUMN.top + ys[k] - self.off
            if y + self.row_h(row) < COLUMN.top - 6 or y > COLUMN.bottom:
                continue
            on = k == at
            if row["kind"] == "pills":
                self.draw_pills(c, row, y, col if on else None)
            elif row["kind"] == "views":
                self.draw_views(c, row, y, on)
            elif row["kind"] == "note":
                text(c, f.body, row["label"], (COLUMN.left, y + 14), theme.RED if self.index_error else theme.DIM, maxw=COLUMN.width)
            else:
                n = len(row["items"])
                tile = (SHELF_TILE[0], SHELF_TILE[0] + LIST_FOOT) if row["tile"] == "list" else SHELF_TILE
                sh = self.shelf_of.setdefault(row["key"], Shelf(tile, TILE_GAP))
                sh.tile = tile
                per = max(1, (COLUMN.width + TILE_GAP) // (tile[0] + TILE_GAP))
                note = "{:,} / {:,}".format(col + 1, n) if on and n > per else "{:,}".format(n)
                if row["key"] == "lists" and self.lists_why:
                    note = "Favourites and playlists need another token"
                list_head(c, f, pygame.Rect(COLUMN.left - 8, y - 6, COLUMN.width + 16, HEAD_H), row["label"], note,
                          note_color=theme.AMBER if row["key"] == "lists" and self.lists_why else None,
                          color=theme.TEXT if on else theme.DIM)
                sh.draw(c, app, row["items"], COLUMN.left, COLUMN.right, y + HEAD_H, col if on else None,
                        tile=self.list_tile if row["tile"] == "list" else self.album_tile,
                        on_tap=lambda i, key=row["key"]: self.tap(key, i), clip=area)
        c.set_clip(old)
        if total - self.off > COLUMN.height:
            _fade(c, theme.BG, SR(pygame.Rect(area.left, COLUMN.bottom - 40, area.width, 40)), "bottom")
        if self.off > 0:
            _fade(c, theme.BG, SR(pygame.Rect(area.left, COLUMN.top - 6, area.width, 30)), "top")
        self.draw_panel(c, rows[at], rows[at]["items"][col] if rows[at]["items"] else None)

    def draw_pills(self, c, row, y, focus):
        f, x = self.f, COLUMN.left
        for i, p in enumerate(row["items"]):
            r = pygame.Rect(x, y, int(measure(f.body, p["label"])) + 36 + (16 if p.get("dot") else 0), PILL_H)
            self.app.hit(r, lambda i=i: self.tap("pills", i))
            card(c, r, i == focus, PILL_H // 2)
            tx = r.left + 18
            if p.get("dot"):
                draw.circle(c, p["dot"], (tx + 4, r.centery), 5)
                tx += 16
            text(c, f.body, p["label"], (tx, r.centery), theme.TEXT if i == focus else theme.DIM, "midleft")
            x = r.right + 10

    def draw_views(self, c, row, y, on):
        """Browse by: the grouping in use is lifted; with the focus here it carries the focus box."""
        f = self.f
        lr = text(c, f.small, "Browse by", (COLUMN.left, y + VIEW_H // 2), theme.FAINT, "midleft")
        x = lr.right + 12
        for i, v in enumerate(row["items"]):
            r = pygame.Rect(x, y, int(measure(f.small, v["label"])) + 28, VIEW_H)
            self.app.hit(r, lambda i=i: self.tap("views", i))
            cur = i == self.view
            if cur and on:
                card(c, r, True, VIEW_H // 2)
            elif cur:
                rrect(c, theme.PANEL_HI, r, VIEW_H // 2)
            text(c, f.small, v["label"], r.center, theme.TEXT if cur else theme.DIM, "center")
            x = r.right + 6

    def draw_panel(self, c, row, it):
        app = self.app
        if it is None:
            rrect(c, theme.PANEL, SIDE, 16)
            return
        key = it["key"]
        if row["kind"] == "shelf" or key == ("all",):
            self.item_panel(c, it, ("X", "Y") if row.get("tile") == "album" else ("X",) if row["kind"] == "shelf" else ())
            return
        p = Panel(c, self.f, SIDE, app=app, key=str(key), slot="music-panel")
        if row["kind"] == "views":
            by, label = VIEWS[self.view]
            groups = [r for r in self.shelves() if isinstance(r["key"], tuple)]
            p.title("Browse by: %s" % label.lower())
            p.y -= 8
            p.prose(VIEW_ABOUT[by], lines=3)
            if groups:
                p.pair("Shelves", "{:,}".format(len(groups)))
                p.pair("Soundtracks", "{:,}".format(len(self.albums())))
                p.space(6)
                p.rule()
                for g in groups[:max(0, int(p.room() // 24))]:
                    p.pair(g["label"], "{:,}".format(len(g["items"])), theme.FAINT, theme.DIM)
            elif not self.index:
                p.prose(self.index_error or "Reading RomM's jukebox for the first time. It's kept after that, and read again only when RomM's soundtracks change.")
        elif key == "menu":
            st = rommlib.music_status()
            p.verb("Open", "go", "On" if app.cfg["menu_music"] else "Off", fn=lambda: self.action("ok"))
            p.title("Menu music", sub="In EmulationStation's menus")
            if st["tracks"]:
                p.pair("In the menus", "%s · %s" % (plural_tracks(st["tracks"]), human_size(st["bytes"])))
                p.space(8)
            p.prose("Have EmulationStation play these soundtracks in its menus: your favourites, a playlist, the games "
                    "on this box, or a mix. It's separate from what you play here.")
        elif key == "search":
            p.verb("Open", "go", fn=lambda: self.action("ok"))
            p.title("Search", sub="Soundtracks, composers and tracks")
            p.prose("Type part of a game's name, a composer or a track title. Soundtracks and composers are found on "
                    "the box at once; tracks are asked of RomM.")
        elif key == "now":
            pl = app.player
            t = pl.current()
            p.verb("Open", "go", "Track %d of %d" % (pl.at + 1, len(pl.order)), fn=lambda: self.action("ok"))
            h = max(120, min(224, p.room() - 150))
            album_art(c, app, t.get("rom_id"), t.get("game_cover_url") or t.get("cover_url"), (p.x, p.y, p.w, h),
                      t.get("game_name") or "")
            p.space(h + 14)
            p.title(track_title(t), sub=t.get("artist") or "", center=True)
            p.line("%s · %s" % (STATE_WORDS[pl.state].rstrip("…"), pl.what), theme.ACCENT_HI, center=True)


class AlbumWallScreen(AlbumFocus, Screen):
    """One shelf, or one composer's soundtracks, as a wall of covers. A opens, X plays, Y changes
    the order (by name, newest in RomM, longest, oldest game); L and R go a screenful, L2 and R2
    to the next letter while it's in name order."""

    def __init__(self, app, title, albums):
        super().__init__(app)
        self.init_focus()
        self.title, self.albums = title, list(albums)
        self.sort, self.i, self.top = 0, 0, 0
        self.listed = None

    def items(self):
        if self.listed is None:
            self.listed = [album_item(a) for a in rommlib.music_sorted(self.albums, SORTS[self.sort][0])]
        return self.listed

    def tile(self):
        w = (COLUMN.width - WALL_GAP * (WALL_PER - 1)) // WALL_PER
        return w, w * 4 // 3

    def rows_shown(self):
        return max(1, (COLUMN.height - HEAD_H + WALL_GAP) // (self.tile()[1] + WALL_GAP))

    def crumbs(self):
        return self.trail(self.title)

    def nav_key(self):
        return self.i, self.sort

    def hints(self):
        own = [("A", "Open"), ("X", "Play"), ("Y", "Sort: %s" % SORTS[self.sort][1])]
        if SORTS[self.sort][0] == "name" and len(self.starts()) > 1:
            own.append(("L2 R2", "A–Z"))
        if len(self.items()) > WALL_PER * self.rows_shown():
            own += [("L", "Page up"), ("R", "Page down")]
        return own + [("B", "Back")]

    def starts(self):
        out, last = [], None
        for i, it in enumerate(self.items()):
            letter = rommlib.music_letter(it["name"])
            if letter != last:
                out.append((i, letter))
                last = letter
        return out

    def action(self, a):
        items = self.items()
        n = len(items)
        self.i = max(0, min(self.i, n - 1))
        if a in ("ok", "x") and items:
            self.open_item(items[self.i], play=a == "x")
        elif a == "y":
            self.sort = (self.sort + 1) % len(SORTS)
            self.listed, self.i, self.top = None, 0, 0
        elif a in ("left", "right"):
            d = 1 if a == "right" else -1
            if 0 <= self.i % WALL_PER + d < WALL_PER and 0 <= self.i + d < n:
                self.i += d
        elif a in ("up", "down", "l1", "r1"):
            step = WALL_PER * (self.rows_shown() if a in ("l1", "r1") else 1)
            j = self.i + (step if a in ("down", "r1") else -step)
            if 0 <= j < n:
                self.i = j
            elif a in ("down", "r1") and self.i // WALL_PER < (n - 1) // WALL_PER:
                self.i = n - 1                         # under a shorter last row: its end
            elif a == "l1":
                self.i %= WALL_PER
        elif a in ("l2", "r2") and SORTS[self.sort][0] == "name":
            to = letter_jump(self.starts(), self.i, a == "r2")
            if to:
                self.i = to[0]
        else:
            super().action(a)

    def tap(self, i):
        if self.i == i:
            self.action("ok")
        else:
            self.i = i

    def draw(self, c):
        app, f = self.app, self.f
        items = self.items()
        self.i = max(0, min(self.i, len(items) - 1))
        tw, th = self.tile()
        vis = self.rows_shown()
        row = self.i // WALL_PER
        self.top = max(row - vis + 1, min(self.top, row))
        rows = (len(items) + WALL_PER - 1) // WALL_PER
        list_head(c, f, pygame.Rect(COLUMN.left - 8, COLUMN.top - 6, COLUMN.width + 16, HEAD_H), self.title,
                  "%s · %s" % ("{:,} soundtrack{}".format(len(items), "" if len(items) == 1 else "s"), SORTS[self.sort][1]))
        y0 = COLUMN.top + HEAD_H
        for i, it in enumerate(items):
            r_i, c_i = divmod(i, WALL_PER)
            if not (self.top <= r_i < self.top + vis):
                continue
            r = pygame.Rect(COLUMN.left + c_i * (tw + WALL_GAP), y0 + (r_i - self.top) * (th + WALL_GAP), tw, th)
            app.hit(r, lambda i=i: self.tap(i))
            self.album_tile(c, it, r, i == self.i)
        if rows > vis:
            scrollbar(c, (COLUMN.right + 8, y0, 4, vis * (th + WALL_GAP) - WALL_GAP), vis / rows, self.top / (rows - vis))
        if items:
            self.item_panel(c, items[self.i], ("X", "Y"))
        else:
            rrect(c, theme.PANEL, SIDE, 16)
            text(c, f.body, "No soundtracks here", (COLUMN.left, y0 + 12), theme.DIM)


class MusicSearchScreen(ListScreen):
    """What a search found: soundtracks whose game's name holds every word typed, composers the same
    way (both from what the box knows of the jukebox), then tracks RomM finds by title, artist or
    album. A opens a soundtrack or a composer's soundtracks, and plays a track (the tracks found
    play on from it)."""
    ROWS, ROW_H = 9, 56

    def __init__(self, app, query, index):
        super().__init__(app)
        self.query = query
        albums, composers = rommlib.music_find(index, query)
        self.found = [{"kind": "album", "item": album_item(a)} for a in albums] + \
                     [{"kind": "composer", "name": n, "albums": v} for n, v in composers]
        self.tracks = None                         # the tracks RomM found; [] when it found none or couldn't be asked
        self.more = 0                              # tracks found beyond the ones listed
        self.sel = self.scroll = 0

    def enter(self):
        if self.tracks is not None:
            return
        api, q = self.app.api, self.query

        def got(tracks):
            tracks = list(tracks or [])
            self.tracks, self.more = tracks[:SEARCH_TRACKS], max(0, len(tracks) - SEARCH_TRACKS)
        self.app.bg(lambda: api.music_all("tracks", search=q), got, self.load_failed("tracks"))

    def listed(self):
        return self.found + [{"kind": "track", "track": t} for t in self.tracks or []]

    def key(self):
        return ("search", self.query)

    def crumbs(self):
        return self.trail("\"%s\"" % self.query)

    def hints(self):
        rows = self.listed()
        kind = rows[min(self.sel, len(rows) - 1)]["kind"] if rows else None
        return self.list_hints(("A", "Play" if kind == "track" else "Open")) if rows else [("B", "Back")]

    def action(self, a):
        app, rows = self.app, self.listed()
        if a == "ok" and rows:
            row = rows[min(self.sel, len(rows) - 1)]
            if row["kind"] == "album":
                it = row["item"]
                api, rid = app.api, it["rom_id"]
                app.push(TracksScreen(app, it["name"], lambda: album_order(api.music_all("tracks", rom_id=rid)), it["key"]))
            elif row["kind"] == "composer":
                app.push(AlbumWallScreen(app, row["name"], row["albums"]))
            else:
                pl, t = app.player, row["track"]
                if pl.active() and pl.key == self.key() and pl.playing(t):
                    pl.toggle()
                elif not rommlib.music_playable(t):
                    app.toast("\"%s\" is in a format the app can't play" % track_title(t), theme.AMBER)
                else:
                    pl.play(self.tracks, t, "Search: %s" % self.query, self.key())
        elif not self.nav(a):
            super().action(a)

    def draw(self, c):
        app, f, pl = self.app, self.f, self.app.player
        lst, pan = split(700)
        rows = self.listed()
        n_alb = sum(1 for r in self.found if r["kind"] == "album")
        n_comp = len(self.found) - n_alb
        parts = ["{:,} soundtrack{}".format(n_alb, "" if n_alb == 1 else "s")] if n_alb else []
        parts += ["{:,} composer{}".format(n_comp, "" if n_comp == 1 else "s")] if n_comp else []
        if self.tracks:
            parts.append(plural_tracks(len(self.tracks)) + ("+" if self.more else ""))
        list_head(c, f, lst, "Search: %s" % self.query, " · ".join(parts) if parts else "Asking RomM…" if self.tracks is None else "")
        rrect(c, theme.PANEL, pan, 16)
        if not rows:
            if self.load_error:
                self.draw_load_error(c, ROWS_TOP + 8)
            else:
                text(c, f.body, "Asking RomM…" if self.tracks is None else "Nothing in RomM's jukebox matches \"%s\"" % self.query,
                     (lst.left + 8, ROWS_TOP + 12), theme.DIM, maxw=lst.width - 16)
            return
        self.clamp()
        live = pl.active() and pl.key == self.key()
        for i in self.visible():
            row = rows[i]
            r = pygame.Rect(lst.left, ROWS_TOP + (i - self.scroll) * self.ROW_H, lst.width, self.ROW_H - 4)
            app.hit(r, lambda i=i: self.tap_row(i))
            if row["kind"] == "album":
                it = row["item"]
                list_row(c, f, r, i == self.sel, it["name"], "Soundtrack · %s" % it["kind"] if it["kind"] else "Soundtrack",
                         plural_tracks(it["count"]), theme.FAINT, slot="search-row",
                         thumb=lambda tr, it=it: album_art(c, app, it["rom_id"], it.get("cover"), tr, fit=False), chevron=True)
            elif row["kind"] == "composer":
                n = len(row["albums"])
                list_row(c, f, r, i == self.sel, row["name"], "Composer", "{:,} soundtrack{}".format(n, "" if n == 1 else "s"),
                         theme.FAINT, slot="search-row", chevron=True)
            else:
                t = row["track"]
                cur = live and pl.playing(t)
                sub = " · ".join(x for x in (t.get("artist"), t.get("album") or t.get("game_name")) if x)
                status = STATE_WORDS[pl.state] if cur else clock(t["duration_seconds"]) if t.get("duration_seconds") else ""
                list_row(c, f, r, i == self.sel, track_title(t), sub, status, theme.ACCENT_HI if cur else theme.FAINT, slot="search-row",
                         label_color=theme.ACCENT_HI if cur else None,
                         thumb=lambda tr, t=t: album_art(c, app, t.get("rom_id"), t.get("game_cover_url") or t.get("cover_url"), tr, fit=False))
        self.draw_scrollbar(c, lst.right + 6, ROWS_TOP)
        row = rows[min(self.sel, len(rows) - 1)]
        p = Panel(c, f, pan, app=app, key=self.sel, slot="search-panel")
        if row["kind"] == "album":
            it = row["item"]
            p.verb("Open", "go", plural_tracks(it["count"]), fn=lambda: self.action("ok"))
            h = max(120, min(224, p.room() - 150))
            album_art(c, app, it["rom_id"], it.get("cover"), (p.x, p.y, p.w, h), it["name"])
            p.space(h + 14)
            p.title(it["name"], sub=" · ".join(x for x in (it["kind"], str(it["year"]) if it.get("year") else "") if x),
                    sub_color=theme.FAINT, center=True)
            p.y -= 6
            if it.get("artist"):
                p.pair("By", it["artist"])
            if it.get("seconds"):
                p.pair("Length", "%s · %s" % (plural_tracks(it["count"]), minutes(it["seconds"])))
        elif row["kind"] == "composer":
            p.verb("Open", "go", "{:,} soundtrack{}".format(len(row["albums"]), "" if len(row["albums"]) == 1 else "s"),
                   fn=lambda: self.action("ok"))
            p.title(row["name"], sub="Composer", sub_color=theme.FAINT)
            for a in row["albums"][:max(0, int(p.room() // 24))]:
                p.pair(a.get("name") or "", a.get("platform") or "", theme.FAINT, theme.DIM)
        else:
            t = row["track"]
            p.verb("Play", "go", clock(t["duration_seconds"]) if t.get("duration_seconds") else "", fn=lambda: self.action("ok"))
            h = max(120, min(224, p.room() - 170))
            album_art(c, app, t.get("rom_id"), t.get("game_cover_url") or t.get("cover_url"), (p.x, p.y, p.w, h), t.get("game_name") or "")
            p.space(h + 14)
            p.title(track_title(t), sub=t.get("artist") or "", center=True)
            p.y -= 6
            p.pair("From", t.get("album") or t.get("game_name") or "")
            p.pair("System", t.get("platform_name") or "")


class TracksScreen(ListScreen):
    """A list of tracks (a soundtrack, a playlist, a mix, everything) and, beside it, what's
    playing from it, or the list itself when nothing is. A plays from the selected track, and
    the list plays on from there; on the track that's playing it pauses. X pauses whatever
    plays here, Y shuffles, L2/R2 go to the previous and next track. Leaving the screen doesn't
    stop the music. A soundtrack's rows are one line (number, title, length); a list across
    several soundtracks names each track's artist and game under it."""

    def __init__(self, app, title, load, key, shuffle=False, autoplay=False, game=None):
        super().__init__(app)
        self.title, self.load, self.key, self.shuffle, self.autoplay, self.game = title, load, key, shuffle, autoplay, game
        self.items = None
        self.sel = self.scroll = 0
        self.many = False            # tracks of more than one album: a row says which
        self.was = None              # the track that was playing when last drawn

    @property
    def ROW_H(self):
        return 56 if self.many else 46

    @property
    def ROWS(self):
        return 9 if self.many else 11

    def enter(self):
        if self.items is not None:
            return

        def got(tracks):
            self.items = list(tracks or [])
            self.many = len({(t.get("album"), t.get("rom_id")) for t in self.items}) > 1
            pl = self.app.player
            if self.autoplay and self.items and not (self.mine() and pl.active()):
                pl.play(self.items, None, self.title, self.key, shuffle=self.shuffle)
            self.to_current()
        self.app.bg(self.load, got, self.load_failed("items"))

    def mine(self):
        """The player's queue is this list."""
        pl = self.app.player
        return bool(pl.queue) and pl.key == self.key

    def live(self):
        return self.mine() and self.app.player.active()

    def to_current(self):
        cur = self.app.player.current() if self.mine() else None
        for i, t in enumerate(self.items or []):
            if cur and t.get("rom_file_id") == cur.get("rom_file_id"):
                self.sel = i

    def crumbs(self):
        return self.trail(self.title)

    def hints(self):
        pl, rows = self.app.player, self.listed()
        t = rows[min(self.sel, len(rows) - 1)] if rows else None
        if not self.live():
            return self.list_hints(("A", "Play"), ("X", "Play all"), ("Y", "Shuffle"))
        word = "Resume" if pl.state == "paused" else "Pause"
        own = [("A", word)] if pl.playing(t) else [("A", "Play"), ("X", word)]     # on the playing track A and X do the same
        return self.list_hints(*own, ("Y", "In order" if pl.shuffle else "Shuffle"), ("L2 R2", "Previous, next"))

    def action(self, a):
        app, pl, rows = self.app, self.app.player, self.listed()
        live = self.live()
        if a == "ok" and rows:
            t = rows[min(self.sel, len(rows) - 1)]
            if live and pl.playing(t):
                pl.toggle()
            elif not rommlib.music_playable(t):
                app.toast("\"%s\" is in a format the app can't play" % track_title(t), theme.AMBER)
            else:
                pl.play(rows, t, self.title, self.key)
        elif a == "x" and rows:
            if live:
                pl.toggle()
            else:
                pl.play(rows, None, self.title, self.key, shuffle=False)
                self.to_current()
        elif a == "y" and rows:
            if live:
                pl.set_shuffle(not pl.shuffle)
                app.toast("Shuffled from here on" if pl.shuffle else "In the list's order from here on")
            else:
                pl.play(rows, None, self.title, self.key, shuffle=True)
                self.to_current()
        elif a in ("l2", "r2"):
            if live:
                (pl.next if a == "r2" else pl.prev)()
        elif not self.nav(a):
            super().action(a)

    def follow(self):
        """The selection stays on the playing track when it was on it and the track changed."""
        cur = self.app.player.current() if self.live() else None
        fid = cur.get("rom_file_id") if cur else None
        rows = self.listed()
        if fid != self.was and self.was is not None and rows \
                and rows[min(self.sel, len(rows) - 1)].get("rom_file_id") == self.was:
            self.to_current()
        self.was = fid

    def draw(self, c):
        app, f, pl = self.app, self.f, self.app.player
        lst, pan = split(700)
        rows = self.listed()
        total = sum(t.get("duration_seconds") or 0 for t in rows)
        list_head(c, f, lst, self.title, "%s · %s" % (plural_tracks(len(rows)), minutes(total)) if rows else "")
        if self.draw_load_error(c, ROWS_TOP + 8):
            return
        if self.items is None:
            text(c, f.body, "Loading…", (lst.left + 8, ROWS_TOP + 12), theme.DIM)
        elif not rows:
            text(c, f.body, "No tracks here", (lst.left + 8, ROWS_TOP + 12), theme.DIM)
        self.clamp()
        self.follow()
        live = self.live()
        for i in self.visible():
            t = rows[i]
            r = pygame.Rect(lst.left, ROWS_TOP + (i - self.scroll) * self.ROW_H, lst.width, self.ROW_H - 4)
            app.hit(r, lambda i=i: self.tap_row(i))
            cur, ok = live and pl.playing(t), rommlib.music_playable(t)
            dur = clock(t["duration_seconds"]) if t.get("duration_seconds") else ""
            if cur:
                status = STATE_WORDS[pl.state] if pl.state != "playing" else "%s / %s" % (clock(pl.position()), dur or "?")
            else:
                status = dur if ok else "Can't be played here"
            sub = " · ".join(x for x in (t.get("artist"), t.get("album") or t.get("game_name")) if x) if self.many else ""

            def mark(center, i=i, cur=cur):
                if cur:
                    play_mark(c, theme.ACCENT_HI, center, paused=pl.state == "paused", size=11)
                else:
                    text(c, f.small, str(i + 1), center, theme.FAINT, "center")
                return True
            list_row(c, f, r, i == self.sel, track_title(t), sub, status, theme.ACCENT_HI if cur else theme.FAINT,
                     slot="track-row", label_color=theme.ACCENT_HI if cur else None if ok else theme.FAINT, mark=mark, mark_w=52)
        self.draw_scrollbar(c, lst.right + 6, ROWS_TOP)
        if rows:
            (self.panel_playing if live else self.panel_list)(c, pan, rows)
        else:
            rrect(c, theme.PANEL, pan, 16)

    def cover(self, c, t, rect, tracks=None):
        """The art for the panel: a soundtrack's cover, or a mosaic of the covers a mixed list draws on."""
        if t is not None:
            return album_art(c, self.app, t.get("rom_id"), t.get("game_cover_url") or t.get("cover_url"), rect,
                             t.get("game_name") or "")
        rect = pygame.Rect(rect)
        side = min(rect.width, rect.height)
        r = pygame.Rect(rect.centerx - side // 2, rect.top, side, side)
        mosaic(c, self.app, covers_of(self.app, tracks), r)
        return r

    def panel_list(self, c, pan, rows):
        """Nothing from here is playing: the list itself, and the one big action."""
        app, pl = self.app, self.app.player
        p = Panel(c, self.f, pan, app=app, key=str(self.key), slot="tracks-panel")
        p.actions(hint_actions(self, ("X", "Y")))
        t = rows[min(self.sel, len(rows) - 1)]
        p.verb("Play", "go", "from %d. %s" % (self.sel + 1, track_title(t)) if self.sel else "from the top",
               fn=lambda: self.action("ok"))
        if self.mine() and pl.error:
            p.status(pl.error, theme.RED)
        h = max(120, min(232, p.room() - 210))
        self.cover(c, None if self.many else rows[0], (p.x, p.y, p.w, h), rows)
        p.space(h + 14)
        artists = sorted({x.get("artist") for x in rows if x.get("artist")})
        years = sorted({x["year"] for x in rows if isinstance(x.get("year"), int)})
        if self.many:
            sub = "%d soundtracks" % len({x.get("rom_id") for x in rows})
        else:
            sub = artists[0] if len(artists) == 1 else "%d artists" % len(artists) if artists else ""
        p.title(self.title, sub=sub, sub_color=theme.DIM, center=True)
        p.y -= 6
        total = sum(x.get("duration_seconds") or 0 for x in rows)
        p.pair("Length", "%s · %s" % (plural_tracks(len(rows)), minutes(total)))
        if not self.many:
            p.pair("Platform", rows[0].get("platform_name") or "")
            if years:
                p.pair("Released", str(years[0]))
            genres = sorted({x.get("genre") for x in rows if x.get("genre")})
            if genres:
                p.pair("Genre", ", ".join(genres[:2]))
        elif years:
            p.pair("Years", "%d to %d" % (years[0], years[-1]) if years[0] != years[-1] else str(years[0]))

    def panel_playing(self, c, pan, rows):
        """Something from here is playing: the track, how far in, the controls, what's next."""
        app, f, pl = self.app, self.f, self.app.player
        t = pl.current()
        p = Panel(c, f, pan, app=app, key=t.get("rom_file_id"), slot="now-panel")
        p.actions([("L2 R2", "Previous, next", None), ("Y", "In order" if pl.shuffle else "Shuffle", lambda: self.action("y"))])
        # the one big control: pause or resume, with the track's progress along its foot
        r = p.reserve(lambda: self.action("x"))
        dur = t.get("duration_seconds") or 0
        pos = min(pl.position(), dur or 1e9)
        rrect(c, theme.BG2, r, 12)
        gx = r.left + 18
        gx += button_glyph(c, f, "X", gx, r.top + 12) + 12
        text(c, f.h3, {"playing": "Pause", "paused": "Resume", "loading": "Loading…"}.get(pl.state, "Play"), (gx, r.top + 23),
             theme.TEXT, "midleft")
        text(c, f.small, "%s / %s" % (clock(pos), clock(dur) if dur else "?"), (r.right - 20, r.top + 23), theme.DIM, "midright")
        progress_bar(c, (r.left + 18, r.bottom - 14, r.width - 36, 6), pos / dur if dur else 0,
                     theme.ACCENT if pl.state == "playing" else theme.FAINT)
        # the track
        text(c, f.small, STATE_WORDS[pl.state], (p.x, p.y), theme.ACCENT_HI if pl.state == "playing" else theme.FAINT)
        note = "%d of %d%s" % (pl.at + 1, len(pl.order), ", shuffled" if pl.shuffle else "")
        text(c, f.small, note, (p.x + p.w, p.y), theme.FAINT, "topright")
        p.space(30)
        ar = self.cover(c, t, (p.x, p.y, 132, 176))
        tx = p.x + 132 + 18
        tw = p.x + p.w - tx
        lines = min(2, len(wrap(f.h2, track_title(t), tw)))
        ty = p.y + 6 + scroll_text(c, f.h2, track_title(t), (tx, p.y + 2, tw, lines * 32), theme.TEXT, key=t.get("rom_file_id"),
                                   line_h=32, slot="now-title").height
        marquee(c, f.body, t.get("artist") or "Unknown artist", (tx, ty), theme.DIM, maxw=tw, bg=theme.PANEL,
                slot="now-artist", key=t.get("rom_file_id"))
        marquee(c, f.small, t.get("album") or t.get("game_name") or "", (tx, ty + 36), theme.FAINT, maxw=tw, bg=theme.PANEL,
                slot="now-album", key=t.get("rom_file_id"))
        about = (t.get("platform_name"), str(t["year"]) if isinstance(t.get("year"), int) else None)
        marquee(c, f.small, " · ".join(x for x in about if x), (tx, ty + 60), theme.FAINT, maxw=tw, bg=theme.PANEL,
                slot="now-about", key=t.get("rom_file_id"))
        p.space(176 + 18)
        # what's next
        nxt = [pl.queue[i] for i in pl.order[pl.at + 1:pl.at + 1 + max(0, int((p.room() - 30) // 24))]]
        if nxt:
            p.rule()
            p.label("Up next")
            for x in nxt:
                p.pair(track_title(x), clock(x["duration_seconds"]) if x.get("duration_seconds") else "", theme.FAINT, theme.DIM)
        elif p.room() > 40:
            p.rule()
            p.label("The last track of this list")
