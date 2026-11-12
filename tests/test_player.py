"""Listening in the app: the player (rbui/player.py) and its screens (rbui/screens/jukebox.py),
against the mock RomM's jukebox. The music stream is a stand-in, except in the last test."""
import os
import time

import pytest

import rommlib


class Out:
    """pygame.mixer.music as far as the player uses it."""
    def __init__(self):
        self.loaded, self.busy, self.pos, self.refuse = None, False, 0, False

    def load(self, path):
        import pygame
        if self.refuse:
            raise pygame.error("not a sound file")
        assert os.path.getsize(path) > 0
        self.loaded = path

    def play(self):
        self.busy, self.pos = True, 0

    def pause(self):
        self.busy = False

    def unpause(self):
        self.busy = True

    def stop(self):
        self.busy = False

    def unload(self):
        self.loaded = None

    def get_busy(self):
        return self.busy

    def get_pos(self):
        return self.pos


@pytest.fixture
def app(isolated, rb, monkeypatch, fast_mock_url):
    """An App on the mock RomM whose background work runs at once, with a stand-in music stream."""
    import pygame
    monkeypatch.setenv("ROMM_URL", fast_mock_url)
    monkeypatch.setenv("ROMM_TOKEN", "demo")
    pygame.init()
    a = rb.App(None, headless=True)

    def bg(fn, done=None, err=None):
        try:
            r = fn()
        except Exception as e:                      # noqa: BLE001
            (err or (lambda e: a.toast(str(e))))(e)
            return
        if done:
            done(r)
    monkeypatch.setattr(a, "bg", bg)
    a.load_platforms()
    a.player.out = Out()
    monkeypatch.setattr(a.player, "SETTLE", 0)
    a.push(rb.HomeScreen(a))
    return a


def album(app, rb, autoplay=False):
    """The track list of the first game with a soundtrack whose tracks all play."""
    g = next(g for g in sorted(app.api.music_all("games"), key=lambda g: g["name"])
             if all(rommlib.music_playable(t) for t in app.api.music_all("tracks", rom_id=g["rom_id"])))
    scr = rb.TracksScreen(app, g["name"], lambda: rb.album_order(app.api.music_all("tracks", rom_id=g["rom_id"])),
                          ("game", g["rom_id"]), autoplay=autoplay)
    app.push(scr)
    return scr


def ended(app):
    """The track plays out, and a frame passes."""
    app.player.out.busy = False
    app.player.tick()


def cached():
    d = rommlib.music_cache_dir()
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


def test_a_plays_from_the_selected_track_and_the_list_plays_on_to_its_end(app, rb):
    scr = album(app, rb)
    pl, rows = app.player, scr.listed()
    assert len(rows) >= 4 and not pl.active() and scr.hints()[:3] == [("A", "Play"), ("X", "Play all"), ("Y", "Shuffle")]
    scr.sel = len(rows) - 3
    scr.action("ok")
    assert pl.state == "playing" and pl.current() is rows[-3] and pl.what == scr.title
    assert pl.out.loaded == rommlib.music_local(rows[-3]) and pl.out.busy
    assert len(cached()) == 2                                       # the one playing, and the next already here
    assert scr.hints()[0] == ("A", "Pause") and ("L2 R2", "Previous, next") in scr.hints()
    scr.draw(app.canvas)
    ended(app)
    assert pl.current() is rows[-2] and pl.state == "playing" and pl.out.busy
    scr.draw(app.canvas)
    assert scr.sel == len(rows) - 2                                  # the selection was on the playing track: it follows
    ended(app)
    assert pl.current() is rows[-1]
    ended(app)
    assert pl.state == "stopped" and not pl.out.busy and pl.out.loaded is None      # the list is through
    assert scr.hints()[0] == ("A", "Play")


def test_a_and_x_pause_and_resume_and_a_paused_track_never_counts_as_ended(app, rb):
    scr = album(app, rb)
    pl = app.player
    scr.action("x")                                                  # Play all: from the first
    assert pl.state == "playing" and pl.current() is scr.listed()[0] and scr.sel == 0 and not pl.shuffle
    scr.action("ok")                                                 # A on the playing track
    assert pl.state == "paused" and not pl.out.busy and scr.hints()[0] == ("A", "Resume")
    pl.tick()
    assert pl.state == "paused" and pl.current() is scr.listed()[0]
    scr.sel = 2
    assert scr.hints()[:2] == [("A", "Play"), ("X", "Resume")]
    scr.action("x")                                                  # X resumes wherever the selection is
    assert pl.state == "playing" and pl.out.busy and pl.current() is scr.listed()[0]
    scr.action("ok")                                                 # A on another track plays that one
    assert pl.current() is scr.listed()[2] and pl.state == "playing"


def test_l2_and_r2_go_to_the_previous_and_next_track(app, rb):
    scr = album(app, rb)
    pl, rows = app.player, scr.listed()
    scr.action("r2")
    assert not pl.active()                                           # nothing plays here: nothing to skip
    scr.action("x")
    scr.action("r2")
    scr.action("r2")
    assert pl.current() is rows[2]
    scr.action("l2")
    assert pl.current() is rows[1]
    pl.out.pos = 5000                                                # five seconds in: back to its start
    scr.action("l2")
    assert pl.current() is rows[1] and pl.out.pos == 0
    scr.action("l2")
    scr.action("l2")
    assert pl.current() is rows[0]                                   # the first stays the first
    pl.at = len(pl.order) - 1
    pl.start()
    scr.action("r2")
    assert pl.current() is rows[-1] and "last track" in app.toasts[-1][0]


def test_shuffle_plays_every_track_once_and_can_be_switched_while_playing(app, rb):
    scr = album(app, rb)
    pl, rows = app.player, scr.listed()
    scr.action("y")                                                  # nothing playing: play in any order
    assert pl.shuffle and pl.state == "playing" and sorted(pl.order) == list(range(len(rows)))
    assert scr.listed()[scr.sel] is pl.current()
    seen = [pl.current()["rom_file_id"]]
    for _ in rows[1:]:
        ended(app)
        seen.append(pl.current()["rom_file_id"])
    assert sorted(seen) == sorted(t["rom_file_id"] for t in rows)
    scr.action("y")                                                  # playing: back to the list's order from here
    assert not pl.shuffle and pl.order == list(range(len(rows))) and pl.current()["rom_file_id"] == seen[-1]
    assert ("Y", "Shuffle") in scr.hints()
    scr.action("y")
    assert pl.shuffle and pl.order[0] == pl.queue.index(pl.current()) and ("Y", "In order") in scr.hints()


def test_a_track_in_a_format_the_app_cant_play_is_said_and_left_out_of_the_queue(app, rb):
    odd = next(t for t in app.api.music_all("tracks") if not rommlib.music_playable(t))
    tracks = rb.album_order(app.api.music_all("tracks", rom_id=odd["rom_id"]))
    scr = rb.TracksScreen(app, "An album", lambda: tracks, ("game", odd["rom_id"]))
    app.push(scr)
    scr.sel = next(i for i, t in enumerate(scr.listed()) if t["rom_file_id"] == odd["rom_file_id"])
    scr.action("ok")
    assert not app.player.active() and "can't play" in app.toasts[-1][0]
    scr.action("x")
    assert app.player.active() and odd["rom_file_id"] not in [t["rom_file_id"] for t in app.player.queue]
    assert len(app.player.queue) == len(tracks) - 1
    scr.draw(app.canvas)


def test_a_track_that_cant_be_fetched_is_skipped_and_three_in_a_row_stop_the_queue(app, rb, monkeypatch):
    scr = album(app, rb)
    pl, rows = app.player, scr.listed()
    real = rommlib.fetch_music
    bad = {rows[1]["rom_file_id"]}

    def fetch(api, t, *a, **k):
        if t["rom_file_id"] in bad:
            raise rommlib.RommError("Track download failed: HTTP 500")
        return real(api, t, *a, **k)
    monkeypatch.setattr(rommlib, "fetch_music", fetch)
    scr.action("x")
    ended(app)
    assert pl.current() is rows[2] and pl.state == "playing" and "HTTP 500" in app.toasts[-1][0]      # the second was skipped
    bad.update(t["rom_file_id"] for t in rows)
    ended(app)
    assert pl.state == "stopped" and "HTTP 500" in pl.error
    scr.draw(app.canvas)
    # a file the mixer won't take counts the same way
    bad.clear()
    pl.out.refuse = True
    scr.action("x")
    assert pl.state == "stopped" and "couldn't be played" in pl.error
    pl.out.refuse = False
    scr.action("x")
    assert pl.state == "playing" and pl.error == ""


def test_without_sound_the_app_says_so_instead_of_playing(app, rb, monkeypatch):
    import pygame
    scr = album(app, rb)
    app.player.out = None
    monkeypatch.setattr(pygame.mixer, "get_init", lambda: None)
    scr.action("x")
    assert not app.player.active() and "no sound" in app.toasts[-1][0]


def wall(app, rb, monkeypatch):
    """Music as Home opens it, with the focused thing's tracks asked for at once."""
    import sys
    juke = rb.JukeboxScreen(app)
    monkeypatch.setattr(sys.modules[type(juke).__module__], "FOCUS_WAIT", 0)
    app.push(juke)
    return juke


def test_music_is_a_wall_of_soundtracks_under_your_lists_and_two_pills(app, rb, monkeypatch):
    rommlib.save_music_sources({"favourites": False, "on_box": False, "playlists": [], "games": [],
                                "mixes": [{"name": "Short ones", "on": False, "filters": {"max_minutes": 2}}]})
    juke = wall(app, rb, monkeypatch)
    games = sorted(app.api.music_all("games"), key=lambda g: g["name"].lower())
    assert [p["key"] for p in juke.pills()] == [("all",), "menu"] and juke.pills()[1]["label"] == "Menu music: Off"
    assert [li["key"] for li in juke.lists()] == [("favourites",), ("playlist", 1), ("playlist", 2), ("mix", "Short ones")]
    assert [al["key"][1] for al in juke.albums()] == [g["rom_id"] for g in games]
    assert (juke.region, juke.i) == ("albums", 0) and juke.hints() == [("A", "Open"), ("X", "Play"), ("B", "Back")]
    juke.draw(app.canvas)
    juke.draw(app.canvas)                                             # the focused soundtrack's tracks are in: the panel lists them
    assert len(juke.tracks[("game", games[0]["rom_id"])]) == games[0]["count"]
    # the focus moves along a row, down a row, and between the three parts
    per = len(games) if len(games) < 6 else 6
    juke.action("right")
    assert juke.i == 1
    juke.action("left")
    juke.action("left")
    assert juke.i == 0                                                # the row's end stays the end
    juke.action("down")
    assert juke.i == min(per, len(games) - 1) and juke.region == "albums"
    juke.action("up")
    juke.action("up")
    assert (juke.region, juke.i) == ("lists", 0)
    juke.action("right")
    juke.action("right")
    juke.action("right")
    juke.action("right")
    assert juke.i == 3 and juke.current()["key"] == ("mix", "Short ones")
    juke.draw(app.canvas)
    juke.action("up")
    assert (juke.region, juke.i) == ("pills", 0) and juke.hints() == [("A", "Play"), ("B", "Back")]
    juke.action("up")
    assert juke.region == "pills"                                     # the top stays the top
    juke.draw(app.canvas)
    juke.action("right")
    assert juke.current()["key"] == "menu" and juke.hints() == [("A", "Open"), ("B", "Back")]
    juke.draw(app.canvas)
    juke.action("ok")
    assert isinstance(app.stack[-1], rb.MusicScreen) and app.stack[-1].crumbs() == ["Music", "Menu music"]
    app.pop()
    juke.action("down")
    juke.action("down")
    juke.action("down")
    juke.action("down")
    assert juke.region == "albums" and juke.i == min(per, len(games) - 1)
    juke.action("down")
    assert juke.i == per                                              # the last row stays the last
    juke.action("up")
    for _ in range(per - 1):
        juke.action("right")
    juke.action("down")
    assert per < len(games) < 2 * per and juke.i == len(games) - 1    # under a shorter last row: its end
    juke.action("r1")
    juke.action("l1")
    assert juke.i == 0                                                # L and R go three rows at a time
    juke.draw(app.canvas)
    assert juke.off >= 0


def test_a_opens_what_is_focused_x_plays_it_and_what_plays_gets_a_pill(app, rb, monkeypatch):
    rommlib.save_music_sources({"favourites": False, "on_box": False, "playlists": [], "games": [],
                                "mixes": [{"name": "Short ones", "on": False, "filters": {"max_minutes": 2}}]})
    juke = wall(app, rb, monkeypatch)
    pl = app.player
    # a mix: its tracks, opened without playing
    juke.region, juke.i = "lists", 3
    juke.action("ok")
    mix = app.stack[-1]
    assert mix.title == "Short ones" and mix.many and mix.listed() and not pl.active()
    assert all(t["duration_seconds"] <= 120 for t in mix.listed())
    mix.draw(app.canvas)                                              # the list, and the panel about it
    app.pop()
    # X on a soundtrack: its list in front, playing from the first track
    juke.region, juke.i = "albums", 0
    key = juke.current()["key"]
    juke.action("x")
    scr = app.stack[-1]
    assert isinstance(scr, rb.TracksScreen) and pl.state == "playing" and pl.key == key
    assert pl.current() is pl.queue[0] and not pl.shuffle and not scr.many
    scr.draw(app.canvas)                                              # what's playing, in the panel
    pl.toggle()
    scr.draw(app.canvas)
    pl.toggle()
    app.pop()
    # back on the wall: Now playing is the first pill, and opens the list that's playing
    assert [p["key"] for p in juke.pills()][0] == "now"
    juke.draw(app.canvas)
    juke.region, juke.i = "pills", 0
    juke.draw(app.canvas)
    juke.action("ok")
    assert app.stack[-1].key == pl.key
    app.open_player()
    assert len([s for s in app.stack if isinstance(s, rb.TracksScreen)]) == 1       # it's in front already
    app.pop()
    # the top bar names the track and where it's from: a tap opens its list
    app.draw()
    hit = next(t for t in app.targets if t[1] == app.open_player)
    app.tap(hit[0].center)
    assert isinstance(app.stack[-1], rb.TracksScreen) and app.stack[-1].key == pl.key
    app.pop()
    # A on Shuffle everything plays at once
    juke.region, juke.i = "pills", 1
    assert juke.current()["key"] == ("all",)
    juke.action("ok")
    assert pl.shuffle and pl.key == ("all",) and len(pl.queue) == len([t for t in app.api.music_all("tracks")
                                                                    if rommlib.music_playable(t)])
    assert app.stack[-1].many
    app.stack[-1].draw(app.canvas)
    # a tap: the first selects a cover, the second opens it; a pill opens at once
    app.pop()
    juke.tap("albums", 2)
    assert (juke.region, juke.i) == ("albums", 2) and app.stack[-1] is juke
    juke.tap("albums", 2)
    assert isinstance(app.stack[-1], rb.TracksScreen) and app.stack[-1].key == juke.albums()[2]["key"]
    app.pop()
    juke.tap("pills", 2)
    assert isinstance(app.stack[-1], rb.MusicScreen)


def test_a_token_that_cant_read_lists_still_plays_everything_else(app, rb, fast_mock_url, monkeypatch):
    app.api = rommlib.Api(fast_mock_url, "demo-no-lists")
    juke = wall(app, rb, monkeypatch)
    assert juke.lists() == [] and "playlists.read" in juke.lists_why        # no favourites, no playlists; the screen says why
    juke.draw(app.canvas)
    juke.action("up")
    assert juke.region == "pills"                                     # nothing between the wall and the pills to stop on
    juke.action("down")
    assert juke.region == "albums"
    juke.action("x")
    assert app.player.state == "playing"


def test_a_games_page_plays_its_soundtrack_and_closing_the_app_stops_it(app, rb):
    g = app.api.music_all("games")[0]
    page = rb.GameScreen(app, app.api.rom(g["rom_id"]))
    app.push(page)
    page.soundtrack(page.record())
    page.open_menu()
    entry = next(e for e in app.modal.entries if e and e[0] == "Play soundtrack")
    assert entry[1].endswith("here in the app")
    app.modal = None
    entry[2]()
    scr, pl = app.stack[-1], app.player
    first = rb.album_order(app.api.music_all("tracks", rom_id=g["rom_id"]))
    first = next(t for t in first if rommlib.music_playable(t))
    assert isinstance(scr, rb.TracksScreen) and pl.state == "playing" and pl.current()["rom_file_id"] == first["rom_file_id"]
    app.pop()                                                         # back on the page: it plays on
    assert pl.state == "playing"
    page.open_menu()
    entry = next(e for e in app.modal.entries if e and e[0] == "Play soundtrack")
    app.modal = None
    was = pl.current()
    entry[2]()                                                        # asked again while it plays: its list, not a restart
    assert pl.current() is was and isinstance(app.stack[-1], rb.TracksScreen)
    app.on_exit()
    assert pl.state == "stopped" and not pl.out.busy


def test_the_real_mixer_plays_a_fetched_track_through_and_moves_on(app, rb, monkeypatch):
    import pygame
    monkeypatch.setenv("SDL_AUDIODRIVER", "dummy")
    pygame.mixer.quit()
    try:
        pygame.mixer.init(44100, -16, 2, 512)
    except pygame.error:
        pytest.skip("no mixer here")
    try:
        app.player.out = None
        monkeypatch.setattr(app.player, "SETTLE", 0.05)
        scr = album(app, rb)
        scr.action("x")
        pl = app.player
        assert pl.state == "playing" and pygame.mixer.music.get_busy()
        first = pl.current()
        until = time.time() + 10                                      # the mock's tracks are a fifth of a second
        while pl.current() is first and pl.active() and time.time() < until:
            time.sleep(0.02)
            pl.tick()
        assert pl.active() and pl.current() is scr.listed()[1]
        pl.toggle()
        assert pl.state == "paused" and not pygame.mixer.music.get_busy()
        time.sleep(0.5)
        pl.tick()
        assert pl.current() is scr.listed()[1]                        # paused well past its length: still the same track
        pl.stop()
    finally:
        pygame.mixer.quit()
