"""Listening in the app: what RomM's jukebox holds (JukeboxScreen) and a list of tracks with
what's playing beside it (TracksScreen). The playing itself is rbui/player.py."""
import pygame
import rommlib
from rbui import theme
from rbui.theme import SR
from rbui.draw import cover_fit, marquee, meter, rrect, scroll_text, state_stripe, text
from rbui.player import track_title
from rbui.screens.base import ListScreen
from rbui.screens.music import load_jukebox, minutes, mix_summary, plural_tracks
from rbui.widgets.layout import list_head, list_row, split, ROWS_TOP
from rbui.widgets.tiles import art_variant, fit_in, placeholder

STATE_WORDS = {"loading": "Loading…", "playing": "Now playing", "paused": "Paused", "stopped": "Stopped"}


def clock(secs):
    """ "3:42", "1:02:03"."""
    s = int(round(secs or 0))
    return "%d:%02d:%02d" % (s // 3600, s % 3600 // 60, s % 60) if s >= 3600 else "%d:%02d" % (s // 60, s % 60)


def album_order(tracks):
    """One game's tracks as its album runs: by disc, then track number, then file name."""
    return sorted(tracks or [], key=lambda t: (t.get("disc") or 0, t.get("track") or 0, (t.get("file_name") or "").lower()))


def album_art(c, app, rom_id, url, rect, name=""):
    """The cover RomM gives for a game's soundtrack, whole, centred in rect (logical); a quiet
    box with the name while it loads or when there's none."""
    rect = pygame.Rect(rect)
    pr = SR(rect)
    key = ("ost", rom_id)
    img = art_variant(app, key, app.art(key, lambda: app.api.fetch_asset(url, "ost-%s" % rom_id), cover_fit),
                      ("fit", pr.size), lambda im: fit_in(im, pr.size))
    if img:
        c.blit(img, img.get_rect(center=pr.center))
    else:
        placeholder(c, app.fonts, name, rect)


class JukeboxScreen(ListScreen):
    """What there is to listen to: everything shuffled, your RomM favourites, playlists, your
    saved mixes, then every game RomM has a soundtrack for. A opens a row's tracks; X plays it
    at once. What's playing, when something is, is the first row."""
    ROWS, ROW_H = 9, 56

    def __init__(self, app):
        super().__init__(app)
        self.sel = self.scroll = 0
        self.sources = rommlib.load_music_sources()
        self.stats = self.playlists = self.favourites = self.games = None
        self.lists_why = ""

    def enter(self):
        self.sources = rommlib.load_music_sources()
        if self.games is None:
            load_jukebox(self)

    def listed(self):
        """The rows: {"key", "label", "sub", "status", "ok", "title", "load", "shuffle", "game"}."""
        api, pl = self.app.api, self.app.player
        out = []
        if pl.queue and pl.current():
            out.append({"key": "now", "label": track_title(pl.current()),
                        "sub": "%s · %s" % (STATE_WORDS[pl.state].rstrip("…"), pl.what), "status": ""})
        total = (self.stats or {}).get("total_tracks")
        out.append({"key": ("all",), "label": "Everything, shuffled", "sub": "Every soundtrack in RomM, in any order",
                    "status": plural_tracks(total) if total else "", "title": "Everything",
                    "load": lambda: api.music_all("tracks"), "shuffle": True})
        lists = not self.lists_why
        out.append({"key": ("favourites",), "label": "My RomM favourites", "ok": lists, "title": "My RomM favourites",
                    "sub": "The tracks you starred in RomM" if lists else "Not available with this token",
                    "status": plural_tracks(self.favourites) if lists and self.favourites is not None else "",
                    "load": lambda: api.music_all("favorites")})
        for p in self.playlists or []:
            out.append({"key": ("playlist", p["id"]), "label": p["name"], "title": p["name"],
                        "sub": "Playlist · %s" % ("shared by %s" % p.get("owner_username", "someone") if p.get("is_public") else "private"),
                        "status": plural_tracks(p.get("track_count") or 0),
                        "load": lambda pid=p["id"]: api.music_all("playlists/%d/tracks" % pid)})
        for m in self.sources["mixes"]:
            out.append({"key": ("mix", m["name"]), "label": m["name"], "title": m["name"],
                        "sub": "Mix · " + (mix_summary(m["filters"]) or "every track"), "status": "",
                        "load": lambda f=m["filters"]: rommlib.music_mix_tracks(api, f)})
        for g in sorted((self.games or {}).values(), key=lambda g: (g.get("name") or "").lower()):
            out.append({"key": ("game", g["rom_id"]), "label": g["name"], "title": g["name"], "sub": g.get("platform_name") or "",
                        "status": plural_tracks(g.get("count") or 0), "game": g,
                        "load": lambda rid=g["rom_id"]: album_order(api.music_all("tracks", rom_id=rid))})
        return out

    def crumbs(self):
        return self.trail("Play music")

    def hints(self):
        rows = self.listed()
        now = bool(rows) and rows[min(self.sel, len(rows) - 1)]["key"] == "now"
        return self.list_hints(("A", "Open"), *([] if now else [("X", "Play")]))

    def action(self, a):
        app, rows = self.app, self.listed()
        if a in ("ok", "x") and rows:
            row = rows[min(self.sel, len(rows) - 1)]
            if row["key"] == "now":
                app.open_player()
            elif not row.get("ok", True):
                app.toast(self.lists_why or "Not available", theme.AMBER)
            else:
                app.push(TracksScreen(app, row["title"], row["load"], row["key"], shuffle=row.get("shuffle", False),
                                      autoplay=a == "x" or row.get("shuffle", False)))
        elif not self.nav(a):
            super().action(a)

    def draw(self, c):
        app, f = self.app, self.f
        lst, pan = split(700)
        rows = self.listed()
        st = self.stats or {}
        list_head(c, f, lst, "Play music",
                  "%s · %s" % (plural_tracks(st["total_tracks"]), minutes(st.get("total_duration_seconds")))
                  if st.get("total_tracks") else "")
        if self.load_error and self.stats == {}:
            self.draw_load_error(c, ROWS_TOP + 8)
            return
        self.clamp()
        for i in self.visible():
            row = rows[i]
            r = pygame.Rect(lst.left, ROWS_TOP + (i - self.scroll) * self.ROW_H, lst.width, self.ROW_H - 6)
            app.hit(r, lambda i=i: self.tap_row(i))
            ok = row.get("ok", True)
            list_row(c, f, r, i == self.sel, row["label"], row["sub"], row["status"], theme.DIM, slot="jukebox-row",
                     label_color=None if ok else theme.FAINT, sub_color=None if ok else theme.AMBER, chevron=True)
            if row["key"] == "now":
                state_stripe(c, r, theme.ACCENT)
        self.draw_scrollbar(c, lst.right + 6, ROWS_TOP)
        rrect(c, theme.PANEL, pan, 16)
        row = rows[min(self.sel, len(rows) - 1)]
        x, w = pan.left + 28, pan.width - 56
        y = pan.top + 24
        g = row.get("game")
        if g:
            album_art(c, app, g["rom_id"], g.get("cover_url"), (x, y, w, 250), g["name"])
            y += 250 + 22
        text(c, f.h2, row["label"], (x, y), theme.TEXT, maxw=w)
        scroll_text(c, f.body, self.about(row), (x, y + 46, w, pan.bottom - 24 - (y + 46)), theme.DIM, bg=theme.PANEL,
                    line_h=26, slot=("jukebox-about", str(row["key"])), key=str(row["key"]))

    def about(self, row):
        kind = row["key"] if isinstance(row["key"], str) else row["key"][0]
        if not row.get("ok", True):
            return ("%s. Favourites and playlists belong to an account, and the token decides what the box may read of "
                    "them. Everything else here plays as it is." % self.lists_why.rstrip("."))
        return {
            "now": "What's playing. It goes on while you look around the app, and stops when the app closes or a "
                   "game starts.",
            "all": "Every track of every soundtrack in RomM, in a new order each time. {X} starts it.",
            "favourites": "The tracks you've starred in RomM's jukebox.",
            "playlist": "A playlist from RomM's jukebox, in its own order.",
            "mix": "A mix you saved on the Music screen: every track in RomM that fits it.",
            "game": "%s · %s. {A} lists its tracks, {X} plays it from the first." % (row["sub"], row["status"]),
        }[kind] + (" Tracks are played from RomM, a few kept on the box so they start at once the next time."
                   if kind in ("all", "game") else "")


class TracksScreen(ListScreen):
    """A list of tracks (a soundtrack, a playlist, a mix, everything) and, beside it, what's
    playing or the track selected. A plays from the selected track, and the list plays on from
    there; on the track that's playing it pauses. X pauses whatever plays here, Y shuffles,
    L2/R2 go to the previous and next track. Leaving the screen doesn't stop the music."""
    ROWS, ROW_H = 9, 56

    def __init__(self, app, title, load, key, shuffle=False, autoplay=False):
        super().__init__(app)
        self.title, self.load, self.key, self.shuffle, self.autoplay = title, load, key, shuffle, autoplay
        self.items = None
        self.sel = self.scroll = 0
        self.many = False            # tracks of more than one album: a row says which
        self.was = None              # the track that was playing when last drawn

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
        if not (self.mine() and pl.active()):
            return self.list_hints(("A", "Play"), ("X", "Play all"), ("Y", "Shuffle"))
        word = "Resume" if pl.state == "paused" else "Pause"
        return self.list_hints(("A", word if pl.playing(t) else "Play"), ("X", word),
                               ("Y", "In order" if pl.shuffle else "Shuffle"), ("L2 R2", "Previous, next"))

    def action(self, a):
        app, pl, rows = self.app, self.app.player, self.listed()
        live = self.mine() and pl.active()
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
        cur = self.app.player.current() if self.mine() and self.app.player.active() else None
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
        live = self.mine() and pl.active()
        for i in self.visible():
            t = rows[i]
            r = pygame.Rect(lst.left, ROWS_TOP + (i - self.scroll) * self.ROW_H, lst.width, self.ROW_H - 6)
            app.hit(r, lambda i=i: self.tap_row(i))
            cur, ok = live and pl.playing(t), rommlib.music_playable(t)
            dur = clock(t["duration_seconds"]) if t.get("duration_seconds") else ""
            if cur:
                status = STATE_WORDS[pl.state] if pl.state != "playing" else "%s / %s" % (clock(pl.position()), dur or "?")
            else:
                status = dur if ok else "Can't be played here"
            sub = " · ".join(x for x in (t.get("artist"), (t.get("album") or t.get("game_name")) if self.many else None) if x)
            list_row(c, f, r, i == self.sel, track_title(t), sub, status, theme.ACCENT_HI if cur else theme.DIM,
                     slot="track-row", label_color=None if ok else theme.FAINT)
            if cur:
                state_stripe(c, r, theme.ACCENT)
        self.draw_scrollbar(c, lst.right + 6, ROWS_TOP)
        rrect(c, theme.PANEL, pan, 16)
        if rows:
            self.panel(c, pan, pl.current() if live else rows[min(self.sel, len(rows) - 1)], live)

    def panel(self, c, pan, t, live):
        app, f, pl = self.app, self.f, self.app.player
        x, w, y = pan.left + 28, pan.width - 56, pan.top + 24
        album_art(c, app, t.get("rom_id"), t.get("game_cover_url") or t.get("cover_url"), (x, y, 150, 200),
                  t.get("game_name") or "")
        tx = x + 170
        tw = pan.right - 28 - tx
        text(c, f.small, STATE_WORDS[pl.state] if live else "Selected", (tx, y + 4),
             theme.ACCENT_HI if live and pl.state == "playing" else theme.FAINT, maxw=tw)
        marquee(c, f.h2, track_title(t), (tx, y + 30), theme.TEXT, maxw=tw, bg=theme.PANEL, slot="track-title",
                key=t.get("rom_file_id"))
        fid = t.get("rom_file_id")
        marquee(c, f.body, t.get("artist") or "Unknown artist", (tx, y + 72), theme.DIM, maxw=tw, bg=theme.PANEL,
                slot="track-artist", key=fid)
        marquee(c, f.small, t.get("album") or t.get("game_name") or "", (tx, y + 106), theme.FAINT, maxw=tw, bg=theme.PANEL,
                slot="track-album", key=fid)
        about = (t.get("platform_name"), str(t["year"]) if isinstance(t.get("year"), int) else None, t.get("genre"))
        marquee(c, f.small, " · ".join(x for x in about if x), (tx, y + 132), theme.FAINT, maxw=tw, bg=theme.PANEL,
                slot="track-about-line", key=fid)
        y += 200 + 26
        dur = t.get("duration_seconds") or 0
        pos = min(pl.position(), dur or 1e9) if live else 0
        meter(c, (x, y, w, 8), [(pos / dur, theme.ACCENT)] if dur and pos else [])
        text(c, f.small, clock(pos), (x, y + 18), theme.DIM)
        text(c, f.small, clock(dur) if dur else "", (pan.right - 28, y + 18), theme.DIM, "topright")
        y += 56
        if live:
            line = "Track %d of %d%s" % (pl.at + 1, len(pl.order), " · shuffled" if pl.shuffle else "")
            nxt = pl.peek()
            more = "Next: %s" % track_title(nxt) if nxt else "The last one of this list"
        else:
            err = pl.error if self.mine() else ""
            line = err or "{A} plays from here, and the list plays on"
            more = "{X} plays the list from the top, {Y} in any order"
        marquee(c, f.body, line, (x, y), theme.RED if not live and err else theme.TEXT, maxw=w, bg=theme.PANEL,
                slot="track-line", key=line)
        marquee(c, f.small, more, (x, y + 34), theme.DIM, maxw=w, bg=theme.PANEL, slot="track-more", key=more)
        scroll_text(c, f.small, "The music goes on while you look around the app. It stops when the app closes or a "
                                "game starts.", (x, y + 76, w, pan.bottom - 20 - (y + 76)), theme.FAINT, bg=theme.PANEL,
                    line_h=22, slot="track-about")
