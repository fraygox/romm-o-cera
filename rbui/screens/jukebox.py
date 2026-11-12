"""Music: RomM's soundtracks as a wall of covers to listen to (JukeboxScreen), and a list of
tracks with what's playing beside it (TracksScreen). The playing itself is rbui/player.py."""
import pygame
import rommlib
from rommlib import human_size
from rbui import draw, theme
from rbui.theme import SR, W
from rbui.draw import (_fade, button_glyph, card, cover_fit, marquee, measure, play_mark, progress_bar, rrect,
                       scroll_text, scrollbar, text, wrap)
from rbui.player import track_title
from rbui.screens.base import ListScreen, Screen
from rbui.screens.music import load_jukebox, minutes, mix_summary, MusicScreen, plural_tracks
from rbui.widgets.layout import hint_actions, list_head, list_row, Panel, split, ROWS_TOP
from rbui.widgets.tiles import art_variant, crop_to, fit_in, focus_ring, mosaic, placeholder

STATE_WORDS = {"loading": "Loading…", "playing": "Now playing", "paused": "Paused", "stopped": "Stopped"}
COLUMN, SIDE = split(702, pygame.Rect(48, 84, W - 96, 572), 24)     # the wall, and the focused thing
PILL_H, HEAD_H = 38, 30
COVERS_PER, COVER_GAP = 6, 12                  # soundtracks: covers, six a row
CARDS_PER, CARD_GAP = COVERS_PER, COVER_GAP    # your lists: mosaic tiles on the same grid, a name and a count under each
CARD_FOOT = 46                                 # ... the room those two lines take
FOCUS_WAIT = 0.35                              # seconds a tile keeps the focus before its tracks are asked for


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


class JukeboxScreen(Screen):
    """Music, from Home: two pills (shuffle everything; menu music), your lists (RomM favourites,
    playlists, saved mixes) as cards, and every game RomM has a soundtrack for as a wall of
    covers. A opens what's focused; X plays it at once. The panel shows the focused thing: its
    cover, what it holds and its first tracks."""

    def __init__(self, app):
        super().__init__(app)
        self.region, self.i = "albums", 0          # where the focus is: "pills", "lists", "albums", and which
        self.off = 0                               # pixels scrolled
        self.sources = rommlib.load_music_sources()
        self.stats = self.playlists = self.favourites = self.games = None
        self.lists_why = ""
        self.tracks = {}                           # key -> its tracks ("loading" while asked for)
        self.focus_since = (None, 0.0)

    def enter(self):
        self.sources = rommlib.load_music_sources()
        if self.games is None:
            load_jukebox(self)

    # ---------------------------------------------------------------- what's on it

    def pills(self):
        app, pl = self.app, self.app.player
        st = self.stats or {}
        out = []
        if pl.queue and pl.current():
            out.append({"key": "now", "label": "Now playing", "dot": theme.ACCENT_HI if pl.state == "playing" else theme.FAINT})
        out.append({"key": ("all",), "label": "Shuffle everything", "title": "Everything", "shuffle": True,
                    "load": lambda: app.api.music_all("tracks"), "count": st.get("total_tracks") or 0,
                    "seconds": st.get("total_duration_seconds") or 0})
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
        api = self.app.api
        return [{"key": ("game", g["rom_id"]), "name": g["name"], "kind": g.get("platform_name") or "", "count": g.get("count") or 0,
                 "game": g, "load": lambda rid=g["rom_id"]: album_order(api.music_all("tracks", rom_id=rid))}
                for g in sorted((self.games or {}).values(), key=lambda g: (g.get("name") or "").lower())]

    def items(self, region):
        return {"pills": self.pills, "lists": self.lists, "albums": self.albums}[region]()

    def current(self):
        rows = self.items(self.region)
        if not rows:
            for region in ("albums", "lists", "pills"):
                if self.items(region):
                    self.region, self.i, rows = region, 0, self.items(region)
                    break
        self.i = max(0, min(self.i, len(rows) - 1))
        return rows[self.i] if rows else None

    def tracks_of(self, it):
        """The focused thing's tracks, asked for once it has kept the focus a moment; None until they're in."""
        key = it["key"]
        got = self.tracks.get(key)
        if got is None and it.get("load"):
            if self.focus_since[0] != key:
                self.focus_since = (key, draw.NOW)
            elif draw.NOW - self.focus_since[1] >= FOCUS_WAIT:
                self.tracks[key] = "loading"
                self.app.bg(it["load"], lambda v, key=key: self.tracks.__setitem__(key, list(v or [])),
                            lambda e, key=key: self.tracks.__setitem__(key, []))
        return got if isinstance(got, list) else None

    # ---------------------------------------------------------------- acting

    def crumbs(self):
        return ["Music"]

    def nav_key(self):
        return self.region, self.i

    def hints(self):
        it = self.current()
        if not it or it["key"] in ("now", "menu"):
            return [("A", "Open"), ("B", "Back")]
        if it["key"] == ("all",):
            return [("A", "Play"), ("B", "Back")]
        return [("A", "Open"), ("X", "Play"), ("B", "Back")]

    def action(self, a):
        app, it = self.app, self.current()
        if a in ("ok", "x") and it:
            if it["key"] == "now":
                app.open_player()
            elif it["key"] == "menu":
                app.push(MusicScreen(app))
            elif a == "ok" or it.get("load"):
                play = a == "x" or it.get("shuffle", False)
                app.push(TracksScreen(app, it.get("title") or it["name"], it["load"], it["key"],
                                      shuffle=it.get("shuffle", False), autoplay=play, game=it.get("game")))
        elif a in ("left", "right", "up", "down", "l1", "r1"):
            self.move(a)
        else:
            super().action(a)

    def move(self, a):
        order = [r for r in ("pills", "lists", "albums") if self.items(r)]
        n = len(self.items(self.region))
        per = {"pills": n, "lists": CARDS_PER, "albums": COVERS_PER}[self.region]
        at = order.index(self.region)
        if a in ("left", "right"):
            d = 1 if a == "right" else -1
            if 0 <= self.i % per + d < per and 0 <= self.i + d < n:
                self.i += d
        elif a in ("l1", "r1") and self.region == "albums":
            self.i = max(0, min(n - 1, self.i + (3 * per if a == "r1" else -3 * per)))
        elif a == "down":
            if self.i + per < n or (self.i // per) < (n - 1) // per:
                self.i = min(n - 1, self.i + per)
            elif at + 1 < len(order):
                self.region, self.i = order[at + 1], 0
        elif a == "up":
            if self.i - per >= 0:
                self.i -= per
            elif at > 0:
                self.region = order[at - 1]
                m = len(self.items(self.region))
                self.i = 0 if self.region == "pills" else (m - 1) // CARDS_PER * CARDS_PER

    def tap(self, region, i):
        if (self.region, self.i) == (region, i) or region == "pills":
            self.region, self.i = region, i
            self.action("ok")
        else:
            self.region, self.i = region, i

    # ---------------------------------------------------------------- drawing

    def draw(self, c):
        app, f = self.app, self.f
        if self.load_error and self.stats == {}:
            self.draw_load_error(c)
            return
        it = self.current()
        pills, lists, albums = self.pills(), self.lists(), self.albums()
        cw = (COLUMN.width - COVER_GAP * (COVERS_PER - 1)) // COVERS_PER
        ch = cw * 4 // 3
        card_w, CARD_H = cw, cw + CARD_FOOT
        # where everything sits, down the column from 0
        y = PILL_H + 18
        lists_y = y
        if lists or self.lists_why:
            y += HEAD_H + ((len(lists) + CARDS_PER - 1) // CARDS_PER) * (CARD_H + CARD_GAP) + 4
        albums_y = y
        total = y + HEAD_H + ((len(albums) + COVERS_PER - 1) // COVERS_PER) * (ch + COVER_GAP) - COVER_GAP
        # the least scroll that shows the focused thing whole (a first row: with its heading)
        if self.region == "pills":
            top, bottom = 0, PILL_H
        elif self.region == "lists":
            row = self.i // CARDS_PER
            top = lists_y + (HEAD_H + row * (CARD_H + CARD_GAP) if row else 0)
            bottom = lists_y + HEAD_H + row * (CARD_H + CARD_GAP) + CARD_H
            if not row:
                top = 0
        else:
            row = self.i // COVERS_PER
            top = albums_y + (HEAD_H + row * (ch + COVER_GAP) if row else 0)
            bottom = albums_y + HEAD_H + row * (ch + COVER_GAP) + ch + 4
            if not row and albums_y + HEAD_H + ch <= COLUMN.height:
                top = 0                                # the first row fits under what's above it: show it all
        self.off = max(bottom - COLUMN.height, min(self.off, top))
        self.off = max(0, min(self.off, max(0, total - COLUMN.height)))
        area = pygame.Rect(COLUMN.left - 8, COLUMN.top - 6, COLUMN.width + 16, COLUMN.height + 6)
        old = c.get_clip()
        c.set_clip(SR(area).clip(old) if old else SR(area))
        y0 = COLUMN.top - self.off
        # pills
        labels = [p["label"] for p in pills]
        nat = [int(measure(f.body, lab)) + 36 + (16 if p.get("dot") else 0) for lab, p in zip(labels, pills)]
        x = COLUMN.left
        for i, (p, w) in enumerate(zip(pills, nat)):
            r = pygame.Rect(x, y0, w, PILL_H)
            on = self.region == "pills" and i == self.i
            app.hit(r, lambda i=i: self.tap("pills", i))
            card(c, r, on, PILL_H // 2)
            tx = r.left + 18
            if p.get("dot"):
                draw.circle(c, p["dot"], (tx + 4, r.centery), 5)
                tx += 16
            text(c, f.body, p["label"], (tx, r.centery), theme.TEXT if on else theme.DIM, "midleft")
            x = r.right + 10
        # your lists
        if lists or self.lists_why:
            on = self.region == "lists"
            list_head(c, f, pygame.Rect(COLUMN.left - 8, y0 + lists_y - 6, COLUMN.width + 16, HEAD_H), "Your lists",
                      "Favourites and playlists need another token" if self.lists_why else "",
                      note_color=theme.AMBER, color=theme.TEXT if on else theme.DIM)
            for i, li in enumerate(lists):
                r = pygame.Rect(COLUMN.left + (i % CARDS_PER) * (card_w + CARD_GAP),
                                y0 + lists_y + HEAD_H + (i // CARDS_PER) * (CARD_H + CARD_GAP), card_w, CARD_H)
                app.hit(r, lambda i=i: self.tap("lists", i))
                self.list_card(c, li, r, on and i == self.i)
        # soundtracks
        on = self.region == "albums"
        st = self.stats or {}
        list_head(c, f, pygame.Rect(COLUMN.left - 8, y0 + albums_y - 6, COLUMN.width + 16, HEAD_H), "Soundtracks",
                  "%d games · %s" % (len(albums), minutes(st.get("total_duration_seconds"))) if albums else
                  "Loading…" if self.games is None else "", color=theme.TEXT if on else theme.DIM)
        playing = app.player.key if app.player.active() else None
        for i, al in enumerate(albums):
            r = pygame.Rect(COLUMN.left + (i % COVERS_PER) * (cw + COVER_GAP),
                            y0 + albums_y + HEAD_H + (i // COVERS_PER) * (ch + COVER_GAP), cw, ch)
            if r.bottom < COLUMN.top or r.top > COLUMN.bottom:
                continue
            app.hit(r, lambda i=i: self.tap("albums", i))
            focused = on and i == self.i
            if focused:
                focus_ring(c, r)
            album_art(c, app, al["game"]["rom_id"], al["game"].get("cover_url"), r, al["name"], fit=False)
            if al["key"] == playing:                   # the one that's playing: a small mark on its cover
                m = pygame.Rect(r.left + 6, r.bottom - 30, 24, 24)
                rrect(c, theme.ACCENT, m, 12)
                play_mark(c, theme.ink_on(theme.ACCENT), m.center, paused=app.player.state == "paused", size=9)
        c.set_clip(old)
        if total - self.off > COLUMN.height:
            _fade(c, theme.BG, SR(pygame.Rect(area.left, COLUMN.bottom - 40, area.width, 40)), "bottom")
        if self.off > 0:
            _fade(c, theme.BG, SR(pygame.Rect(area.left, COLUMN.top - 6, area.width, 30)), "top")
        if it:
            self.draw_panel(c, it)

    def list_card(self, c, li, r, on):
        """A list as a tile: a mosaic of the covers its tracks are from, its name and how much it holds."""
        f = self.f
        art = pygame.Rect(r.left, r.top, r.width, r.width)
        if on:
            focus_ring(c, art)
        got = self.tracks.get(li["key"])
        if got is None:                                # few and small: asked for as soon as they're drawn
            self.tracks[li["key"]] = "loading"
            self.app.bg(li["load"], lambda v, key=li["key"]: self.tracks.__setitem__(key, list(v or [])),
                        lambda e, key=li["key"]: self.tracks.__setitem__(key, []))
        mosaic(c, self.app, covers_of(self.app, got if isinstance(got, list) else None), art)
        marquee(c, f.small, li["name"], (r.left, art.bottom + 6), theme.TEXT if on else theme.DIM, maxw=r.width, focused=on,
                slot=("list-card", str(li["key"])), bg=theme.BG)
        n = li["count"] if li["count"] is not None else len(got) if isinstance(got, list) else None
        text(c, f.tiny, plural_tracks(n) if n is not None else li["kind"], (r.left, art.bottom + 28), theme.FAINT, maxw=r.width)

    def draw_panel(self, c, it):
        app, f = self.app, self.f
        p = Panel(c, f, SIDE, app=app, key=str(it["key"]), slot="music-panel")
        p.actions(hint_actions(self, ("X",)))
        key = it["key"]
        if key == "menu":
            st = rommlib.music_status()
            p.verb("Open", "go", "On" if app.cfg["menu_music"] else "Off", fn=lambda: self.action("ok"))
            p.title("Menu music", sub="In EmulationStation's menus")
            if st["tracks"]:
                p.pair("In the menus", "%s · %s" % (plural_tracks(st["tracks"]), human_size(st["bytes"])))
                p.space(8)
            p.prose("Have EmulationStation play these soundtracks in its menus: your favourites, a playlist, the games "
                    "on this box, or a mix. It's separate from what you play here.")
            return
        if key == "now":
            pl = app.player
            t = pl.current()
            p.verb("Open", "go", "Track %d of %d" % (pl.at + 1, len(pl.order)), fn=lambda: self.action("ok"))
            ar = album_art(c, app, t.get("rom_id"), t.get("game_cover_url") or t.get("cover_url"),
                           (p.x, p.y, p.w, min(224, p.room() - 150)), t.get("game_name") or "")
            p.space(ar.height + 16 if ar.height < 224 else 240)
            p.title(track_title(t), sub=t.get("artist") or "", center=True)
            p.line("%s · %s" % (STATE_WORDS[pl.state].rstrip("…"), pl.what), theme.ACCENT_HI, center=True)
            return
        tracks = self.tracks_of(it)
        secs = sum(t.get("duration_seconds") or 0 for t in tracks or [])
        count = len(tracks) if tracks is not None else it.get("count")
        p.verb("Play" if key == ("all",) else "Open", "go", plural_tracks(count) if count else "", fn=lambda: self.action("ok"))
        h = max(120, min(224, p.room() - 250))
        if it.get("game"):
            album_art(c, app, it["game"]["rom_id"], it["game"].get("cover_url"), (p.x, p.y, p.w, h), it["name"])
        else:
            mosaic(c, app, covers_of(app, tracks), (p.x + (p.w - h) // 2, p.y, h, h))
        p.space(h + 14)
        artists = sorted({t.get("artist") for t in tracks or [] if t.get("artist")})
        years = sorted({t["year"] for t in tracks or [] if isinstance(t.get("year"), int)})
        if it.get("game"):
            sub = " · ".join(x for x in (it["kind"], str(years[0]) if years else "") if x)
        else:
            sub = it.get("about") or it.get("kind") or "Every soundtrack in RomM, in a new order each time"
        p.title(it.get("title") or it["name"], sub=sub, sub_color=theme.FAINT, center=True)
        p.y -= 6
        if tracks is None:
            p.line("Loading…" if it.get("load") else "", theme.FAINT)
            return
        if len(artists) == 1:
            p.pair("By", artists[0])
        elif artists:
            p.pair("By", "%d artists" % len(artists))
        p.pair("Length", "%s · %s" % (plural_tracks(len(tracks)), minutes(secs)))
        if key == ("all",):
            return
        p.space(6)
        p.rule()
        for n, t in enumerate(tracks[:max(0, int(p.room() // 24))]):       # the first tracks, as far as there's room
            p.pair(track_title(t), clock(t["duration_seconds"]) if t.get("duration_seconds") else "", theme.FAINT, theme.DIM)


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
