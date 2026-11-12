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
    return sorted(n for _, _, names in os.walk(rommlib.music_cache_dir()) for n in names)


def test_a_plays_from_the_selected_track_and_the_list_plays_on_to_its_end(app, rb):
    scr = album(app, rb)
    pl, rows = app.player, scr.listed()
    assert len(rows) >= 4 and not pl.active() and scr.hints()[:3] == [("A", "Play"), ("X", "Play all"), ("Y", "Shuffle")]
    scr.sel = len(rows) - 3
    scr.action("ok")
    assert pl.state == "playing" and pl.current() is rows[-3] and pl.what == scr.title
    assert pl.out.loaded == rommlib.music_local(rows[-3], app.api.base) and pl.out.busy
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


def groups(juke):
    return [r for r in juke.rows() if isinstance(r["key"], tuple)]


MIX = {"favourites": False, "on_box": False, "playlists": [], "games": [],
       "mixes": [{"name": "Short ones", "on": False, "filters": {"max_minutes": 2}}]}


def test_music_is_shelves_under_pills_and_browse_by(app, rb, monkeypatch):
    rommlib.save_music_sources(MIX)
    juke = wall(app, rb, monkeypatch)
    games = app.api.music_all("games")
    assert [p["key"] for p in juke.pills()] == [("all",), "search", "menu"] and juke.pills()[2]["label"] == "Menu music: Off"
    rows = juke.rows()
    assert [r["key"] for r in rows[:3]] == ["pills", "views", "lists"]
    assert [li["key"] for li in rows[2]["items"]] == [("favourites",), ("playlist", 1), ("playlist", 2), ("mix", "Short ones")]
    assert juke.index and len(juke.index["albums"]) == len(games)         # the jukebox was read in the background
    by_system = groups(juke)
    assert {g["key"][1] for g in by_system} == {"system"} and sum(len(g["items"]) for g in by_system) == len(games)
    assert [len(g["items"]) for g in by_system] == sorted((len(g["items"]) for g in by_system), reverse=True)
    row, it = juke.current()
    assert row is by_system[0] and it is row["items"][0]                 # the focus starts on the first system's shelf
    assert juke.hints() == [("A", "Open"), ("X", "Play"), ("Y", "See all"), ("L R", "Browse by"), ("B", "Back")]
    juke.draw(app.canvas)
    juke.draw(app.canvas)                                             # the focused soundtrack's tracks are in: the panel lists them
    assert len(juke.tracks[it["key"]]) == it["count"]
    # along a shelf, and no further than its ends
    juke.action("right")
    assert juke.current()[1] is row["items"][1]
    juke.action("left")
    juke.action("left")
    assert juke.current()[1] is row["items"][0]
    # up: your lists, Browse by, the pills
    juke.action("up")
    assert juke.current()[0]["key"] == "lists" and juke.hints()[:3] == [("A", "Open"), ("X", "Play"), ("L R", "Browse by")]
    juke.draw(app.canvas)
    juke.action("up")
    assert juke.at == "views" and juke.hints() == [("LEFT RIGHT", "Browse by"), ("B", "Back")]
    juke.draw(app.canvas)
    juke.action("right")                                              # on Browse by, left and right regroup at once
    assert juke.view == 1 and juke.at == "views" and {g["key"][1] for g in groups(juke)} == {"genre"}
    every = [it["rom_id"] for g in groups(juke) for it in g["items"]]
    assert set(every) == {g["rom_id"] for g in games} and len(every) > len(games)      # a game sits under each of its genres
    juke.draw(app.canvas)
    juke.action("left")
    juke.action("left")
    assert juke.view == 0                                             # the first grouping is the end
    juke.action("up")
    assert juke.at == "pills" and juke.hints()[0] == ("A", "Play")
    juke.action("up")
    assert juke.at == "pills"                                         # the top stays the top
    juke.action("right")
    assert juke.current()[1]["key"] == "search" and juke.hints()[0] == ("A", "Open")
    juke.draw(app.canvas)
    juke.action("right")
    juke.action("right")
    assert juke.current()[1]["key"] == "menu"
    juke.draw(app.canvas)
    juke.action("ok")
    assert isinstance(app.stack[-1], rb.MusicScreen) and app.stack[-1].crumbs() == ["Music", "Menu music"]
    app.pop()
    # L and R regroup from anywhere; from a group's shelf the focus goes to the new grouping's first shelf
    for _ in range(3):
        juke.action("down")
    assert juke.current()[0] is groups(juke)[0]
    juke.action("r1")
    juke.action("r1")
    assert juke.view == 2 and juke.current()[0]["key"][:2] == ("group", "composer")
    assert all(a["artist"] == g["label"] for g in groups(juke) for a in g["albums"])
    juke.action("r1")
    labels = [g["label"] for g in groups(juke)]
    assert juke.view == 3 and labels == sorted(labels) and all(lab.endswith("0s") for lab in labels)
    juke.action("r1")
    labels = [g["label"] for g in groups(juke)]
    assert juke.view == 4 and labels == sorted(labels) and all(len(lab) == 1 for lab in labels)
    juke.action("r1")
    assert juke.view == 0 and app.music_view == 0                     # round again; the app remembers the grouping
    juke.action("l1")
    assert juke.view == 4 and rb.JukeboxScreen(app).view == 4
    # the last shelf is the end, scrolled into view
    for _ in range(60):
        juke.action("down")
    assert juke.current()[0] is groups(juke)[-1]
    juke.draw(app.canvas)
    assert juke.off > 0
    for _ in range(60):
        juke.action("up")
    juke.draw(app.canvas)
    assert juke.at == "pills" and juke.off == 0


def test_shelves_for_whats_on_this_box_and_whats_new(app, rb, monkeypatch):
    import sys
    juke = wall(app, rb, monkeypatch)
    assert not [r for r in juke.rows() if r["key"] in ("here", "recent")]        # nothing installed, and few enough to see at once
    two = [a["rom_id"] for a in juke.index["albums"][:2]]
    juke.here = set(two)
    monkeypatch.setattr(sys.modules[type(juke).__module__], "RECENT", 3)
    juke.cached = (None, None)
    rows = {r["key"]: r for r in juke.rows()}
    assert sorted(it["rom_id"] for it in rows["here"]["items"]) == sorted(two) and rows["here"]["label"] == "On this box"
    newest = sorted(juke.index["albums"], key=lambda a: a["added"], reverse=True)[:3]
    assert len(rows["recent"]["items"]) == 3 and {it["rom_id"] for it in rows["recent"]["items"]} <= \
        {a["rom_id"] for a in juke.index["albums"] if a["added"] >= newest[-1]["added"]}
    assert [r["key"] for r in juke.rows()][2:5] == ["lists", "recent", "here"]
    juke.draw(app.canvas)
    juke.at = "here"
    juke.action("y")                                                  # Y: the shelf as a wall
    assert isinstance(app.stack[-1], rb.AlbumWallScreen) and app.stack[-1].title == "On this box"
    assert len(app.stack[-1].items()) == 2


def test_other_groupings_wait_for_the_index_and_a_kept_one_is_used_until_romm_changes(app, rb, monkeypatch):
    real, calls = rommlib.build_music_index, []

    def build(api, stats=None, cancel=None):
        calls.append(stats)
        if len(calls) == 1:
            raise rommlib.RommError("RomM didn't answer")
        return real(api, stats, cancel)
    monkeypatch.setattr(rommlib, "build_music_index", build)
    juke = wall(app, rb, monkeypatch)
    games = app.api.music_all("games")
    assert juke.index is None and "didn't answer" in juke.index_error
    assert sum(len(g["items"]) for g in groups(juke)) == len(games)   # by system works from RomM's list of games alone
    juke.draw(app.canvas)
    juke.set_view(1)
    rows = juke.rows()
    assert not groups(juke) and rows[-1]["kind"] == "note" and "didn't answer" in rows[-1]["label"]
    juke.at = "views"
    juke.action("down")
    assert juke.current()[0]["kind"] != "note"                        # a line to read, never the focus
    juke.draw(app.canvas)
    juke.at = "views"
    juke.draw(app.canvas)                                             # Browse by's panel says the jukebox couldn't be read
    app.pop()
    again = wall(app, rb, monkeypatch)                                # the next time it's read and kept
    assert again.index and len(calls) == 2 and rommlib.load_music_index()["stats"] == app.api.music_stats()
    app.pop()
    third = wall(app, rb, monkeypatch)
    assert third.index and len(calls) == 2                            # RomM's totals are as they were: not read again
    idx = rommlib.load_music_index()
    idx["stats"] = {"total_tracks": 1}
    rommlib.atomic_write_json(rommlib.music_index_path(), idx)
    app.pop()
    fourth = wall(app, rb, monkeypatch)
    assert len(calls) == 3 and fourth.index["stats"] == app.api.music_stats()      # they changed: read again


def test_a_wall_sorts_and_jumps_by_letter(app, rb, monkeypatch):
    juke = wall(app, rb, monkeypatch)
    juke.action("y")
    w = app.stack[-1]
    assert isinstance(w, rb.AlbumWallScreen) and w.title == juke.current()[0]["label"] and w.crumbs() == ["Music", w.title]
    app.pop()
    w = rb.AlbumWallScreen(app, "Everything", juke.index["albums"])
    app.push(w)
    names = [it["name"] for it in w.items()]
    n = len(names)
    assert names == sorted(names, key=str.lower) and 6 < n <= 12
    assert ("Y", "Sort: A–Z") in w.hints() and ("L2 R2", "A–Z") in w.hints()
    w.draw(app.canvas)
    w.action("r2")
    first = rommlib.music_letter(names[0])
    assert w.i == next(i for i, x in enumerate(names) if rommlib.music_letter(x) != first)
    w.action("l2")
    assert w.i == 0
    w.action("right")
    w.action("down")
    assert w.i == 7
    w.action("down")
    assert w.i == 7                                                   # the last row stays the last
    w.action("up")
    for _ in range(9):
        w.action("right")
    assert w.i == 5                                                   # a row's end stays its end
    w.action("down")
    assert w.i == n - 1                                               # under a shorter last row: its end
    w.action("y")
    added = [a["added"] for a in rommlib.music_sorted(juke.index["albums"], "added")]
    assert w.i == 0 and [it["rom_id"] for it in w.items()] == [a["rom_id"] for a in rommlib.music_sorted(juke.index["albums"], "added")]
    assert added == sorted(added, reverse=True) and ("Y", "Sort: Newest in RomM") in w.hints() and ("L2 R2", "A–Z") not in w.hints()
    w.action("r2")
    assert w.i == 0                                                   # letters only mean something in name order
    w.action("y")
    secs = [a["seconds"] for a in rommlib.music_sorted(juke.index["albums"], "length")]
    assert secs == sorted(secs, reverse=True) and w.items()[0]["rom_id"] == rommlib.music_sorted(juke.index["albums"], "length")[0]["rom_id"]
    w.action("y")
    years = [it["year"] for it in w.items()]
    assert years == sorted(years)
    w.action("y")
    assert [it["name"] for it in w.items()] == names
    w.draw(app.canvas)
    w.tap(2)
    assert w.i == 2 and app.stack[-1] is w                            # a tap selects, a second opens
    w.tap(2)
    assert isinstance(app.stack[-1], rb.TracksScreen) and app.stack[-1].key == w.items()[2]["key"]
    app.pop()
    w.action("x")
    assert app.player.state == "playing" and app.player.key == w.items()[2]["key"]
    app.pop()
    w.draw(app.canvas)                                                # the playing one carries its mark
    empty = rb.AlbumWallScreen(app, "Nobody", [])
    app.push(empty)
    empty.action("ok")
    empty.draw(app.canvas)
    assert app.stack[-1] is empty


def test_search_finds_soundtracks_composers_and_tracks(app, rb, monkeypatch):
    juke = wall(app, rb, monkeypatch)
    juke.at = "pills"
    juke.cols["pills"] = next(i for i, p in enumerate(juke.pills()) if p["key"] == "search")
    juke.draw(app.canvas)
    juke.action("ok")
    assert isinstance(app.stack[-1], rb.MusicSearchInput) and app.stack[-1].crumbs() == ["Music", "Search"]
    juke.search("   ")
    assert app.stack[-1] is juke                                      # nothing typed: back on Music
    one = juke.index["albums"][3]
    word = max(one["name"].split(), key=len)
    juke.action("ok")
    juke.search("  %s " % word.upper())
    res = app.stack[-1]
    assert isinstance(res, rb.MusicSearchScreen) and res.query == word.upper() and res.crumbs()[-1] == "\"%s\"" % word.upper()
    rows = res.listed()
    assert one["rom_id"] in [r["item"]["rom_id"] for r in rows if r["kind"] == "album"]
    assert all(word.lower() in r["item"]["name"].lower() for r in rows if r["kind"] == "album")
    tracks = [r["track"] for r in rows if r["kind"] == "track"]
    assert tracks and all(any(word.lower() in (t[k] or "").lower() for k in ("title", "artist", "album")) for t in tracks)
    res.draw(app.canvas)
    res.sel = next(i for i, r in enumerate(rows) if r["kind"] == "album" and r["item"]["rom_id"] == one["rom_id"])
    assert res.hints()[0] == ("A", "Open")
    res.action("ok")
    assert isinstance(app.stack[-1], rb.TracksScreen) and app.stack[-1].key == ("game", one["rom_id"])
    app.pop()
    # a track plays, and the tracks found play on from it
    res.sel = next(i for i, r in enumerate(rows) if r["kind"] == "track" and rommlib.music_playable(r["track"]))
    assert res.hints()[0] == ("A", "Play")
    res.draw(app.canvas)
    res.action("ok")
    pl = app.player
    assert pl.state == "playing" and pl.key == ("search", word.upper()) and pl.current()["rom_file_id"] == rows[res.sel]["track"]["rom_file_id"]
    res.draw(app.canvas)
    res.action("ok")
    assert pl.state == "paused"
    app.pop()
    # a composer: found by name, opens as a wall of what's theirs
    who = one["artist"]
    comp = rb.MusicSearchScreen(app, who.split()[0], juke.index)
    app.push(comp)
    rows = comp.listed()
    i = next(i for i, r in enumerate(rows) if r["kind"] == "composer" and r["name"] == who)
    comp.sel = i
    comp.draw(app.canvas)
    comp.action("ok")
    w = app.stack[-1]
    assert isinstance(w, rb.AlbumWallScreen) and w.title == who and one["rom_id"] in [it["rom_id"] for it in w.items()]
    assert all(who in a["artists"] for a in w.albums)
    app.pop()
    app.pop()
    # nothing found says so
    none = rb.MusicSearchScreen(app, "zzzzqq", juke.index)
    app.push(none)
    assert none.listed() == [] and none.hints() == [("B", "Back")]
    none.draw(app.canvas)
    none.action("ok")
    assert app.stack[-1] is none


def test_a_opens_what_is_focused_x_plays_it_and_what_plays_gets_a_pill(app, rb, monkeypatch):
    rommlib.save_music_sources(MIX)
    juke = wall(app, rb, monkeypatch)
    pl = app.player
    # a mix: its tracks, opened without playing
    juke.at = "lists"
    juke.cols["lists"] = 3
    juke.action("ok")
    mix = app.stack[-1]
    assert mix.title == "Short ones" and mix.many and mix.listed() and not pl.active()
    assert all(t["duration_seconds"] <= 120 for t in mix.listed())
    mix.draw(app.canvas)                                              # the list, and the panel about it
    app.pop()
    # X on a soundtrack: its list in front, playing from the first track
    juke.at = groups(juke)[0]["key"]
    key = juke.current()[1]["key"]
    juke.action("x")
    scr = app.stack[-1]
    assert isinstance(scr, rb.TracksScreen) and pl.state == "playing" and pl.key == key
    assert pl.current() is pl.queue[0] and not pl.shuffle and not scr.many
    scr.draw(app.canvas)                                              # what's playing, in the panel
    pl.toggle()
    scr.draw(app.canvas)
    pl.toggle()
    app.pop()
    # back on the shelves: Now playing is the first pill, and opens the list that's playing
    assert [p["key"] for p in juke.pills()][0] == "now"
    juke.draw(app.canvas)                                             # the playing cover carries its mark
    juke.at = "pills"
    juke.cols["pills"] = 0
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
    juke.cols["pills"] = 1
    assert juke.current()[1]["key"] == ("all",)
    juke.draw(app.canvas)
    juke.action("ok")
    assert pl.shuffle and pl.key == ("all",) and len(pl.queue) == len([t for t in app.api.music_all("tracks")
                                                                    if rommlib.music_playable(t)])
    assert app.stack[-1].many
    app.stack[-1].draw(app.canvas)
    # a tap: the first selects a cover, the second opens it; a pill opens at once; a grouping is chosen at once
    app.pop()
    shelf = groups(juke)[0]
    juke.tap(shelf["key"], 1)
    assert juke.current() == (shelf, shelf["items"][1]) and app.stack[-1] is juke
    juke.tap(shelf["key"], 1)
    assert isinstance(app.stack[-1], rb.TracksScreen) and app.stack[-1].key == shelf["items"][1]["key"]
    app.pop()
    juke.tap("views", 3)
    assert juke.view == 3 and juke.at == "views"
    juke.tap("pills", len(juke.pills()) - 1)
    assert isinstance(app.stack[-1], rb.MusicScreen)


def test_a_token_that_cant_read_lists_still_plays_everything_else(app, rb, fast_mock_url, monkeypatch):
    app.api = rommlib.Api(fast_mock_url, "demo-no-lists")
    juke = wall(app, rb, monkeypatch)
    assert juke.lists() == [] and "playlists.read" in juke.lists_why        # no favourites, no playlists; the screen says why
    assert [r["key"] for r in juke.rows()][:2] == ["pills", "views"] and "lists" not in [r["key"] for r in juke.rows()]
    juke.draw(app.canvas)
    juke.action("up")
    assert juke.at == "views"                                         # nothing between the shelves and Browse by to stop on
    juke.action("down")
    assert juke.current()[0] is groups(juke)[0]
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


# ---------------------------------------------------------------- what the review found

def later(app, monkeypatch):
    """Background work held back: a list of (fn, done, err) the test runs when it chooses."""
    pending = []
    monkeypatch.setattr(app, "bg", lambda fn, done=None, err=None: pending.append((fn, done, err)))
    return pending


def run(pending, n=None):
    for _ in range(len(pending) if n is None else n):
        fn, done, err = pending.pop(0)
        try:
            r = fn()
        except Exception as e:                      # noqa: BLE001
            if err:
                err(e)
            continue
        if done:
            done(r)


def test_a_list_left_before_its_tracks_arrive_stays_quiet(app, rb, monkeypatch):
    g = app.api.music_all("games")[0]
    pending = later(app, monkeypatch)
    scr = rb.TracksScreen(app, "An album", lambda: rb.album_order(app.api.music_all("tracks", rom_id=g["rom_id"])),
                          ("game", g["rom_id"]), autoplay=True)
    app.push(scr)
    assert len(pending) == 1
    app.stack.pop()                                                   # B before the list arrived
    run(pending, 1)
    assert scr.items and not app.player.active() and not app.player.queue
    again = rb.TracksScreen(app, "An album", scr.load, scr.key, autoplay=True)
    app.push(again)
    run(pending)
    assert app.player.state in ("loading", "playing") and app.player.key == scr.key      # still in front: it plays


def test_skipping_fast_starts_two_downloads_at_most(app, rb, monkeypatch):
    scr = album(app, rb)
    scr.action("x")
    pl, rows = app.player, scr.listed()
    assert pl.state == "playing" and len(rows) >= 5 and not pl.fetching
    pending = later(app, monkeypatch)
    for _ in range(4):
        scr.action("r2")
    assert pl.at == 4 and pl.state == "loading" and len(pending) == 2 and len(pl.fetching) == 2
    run(pending)                                                      # the two that were started land: neither is wanted now
    assert pl.state == "loading" and not pl.fetching and not pending
    pl.tick()                                                         # room again: the one that's waited for is asked for
    assert len(pending) == 1
    run(pending, 1)
    assert pl.state == "playing" and pl.current() is rows[4]
    scr.sel = 4
    assert scr.hints()[0] == ("A", "Pause")
    pl.state = "loading"
    assert all(h[1] not in ("Pause", "Resume") for h in scr.hints())      # nothing to pause while it's fetched
    pl.state = "playing"


def test_everything_and_a_mix_arent_fetched_for_a_look(app, rb, monkeypatch):
    rommlib.save_music_sources(MIX)
    calls, real, page = [], app.api.music_all, app.api.music_page
    monkeypatch.setattr(app.api, "music_all", lambda what, cancel=None, **f: calls.append((what, f)) or real(what, cancel=cancel, **f))
    monkeypatch.setattr(app.api, "music_page", lambda what, limit, **f: calls.append((what, limit, f)) or page(what, limit, **f))
    juke = wall(app, rb, monkeypatch)
    assert [c for c in calls if c == ("tracks", {})] == [("tracks", {})]         # the index, once
    assert ("favorites", 1, {}) in calls                                         # how many are starred: a count
    del calls[:]
    for _ in range(3):
        juke.draw(app.canvas)
    assert {c[0] for c in calls} == {"favorites", "playlists/1/tracks", "playlists/2/tracks", "tracks"}     # small lists, for their covers
    assert [c for c in calls if c[0] == "tracks"] == [("tracks", {"rom_id": juke.current()[1]["rom_id"]})]   # and the focused soundtrack
    del calls[:]
    juke.at = "pills"
    juke.cols["pills"] = 0
    assert juke.current()[1]["key"] == ("all",)
    for _ in range(3):
        juke.draw(app.canvas)
    assert not calls                                                  # resting on Shuffle everything asks RomM nothing
    juke.at = "lists"
    juke.cols["lists"] = 0
    for _ in range(3):
        juke.draw(app.canvas)
    assert not calls                                                  # (a small list is in already)
    juke.cols["lists"] = 3
    for _ in range(3):
        juke.draw(app.canvas)
    assert calls == [("tracks", 60, {"max_duration": 120})]           # a mix only once it has the focus, and only some of it
    juke.draw(app.canvas)
    # edited on the Menu music screen: the tile is the new mix, not what was read for the old one
    sel = rommlib.load_music_sources()
    sel["mixes"][0]["filters"] = {"decade": [1990]}
    rommlib.save_music_sources(sel)
    juke.enter()
    tile = juke.current()[1]
    assert tile["about"] == "1990s" and ("mix", "Short ones") not in juke.tracks
    del calls[:]
    for _ in range(3):
        juke.draw(app.canvas)
    assert calls == [("tracks", 60, {"min_year": 1990, "max_year": 1999})]
    # a soundtrack's mark follows the game the playing track is from, whatever list it plays from
    juke.at = "pills"
    juke.action("ok")
    cur = app.player.current()
    app.pop()
    marked = [it for g in groups(juke) for it in g["items"] if it["rom_id"] == cur["rom_id"]]
    assert marked and app.player.key == ("all",)
    juke.draw(app.canvas)


def test_counting_a_mix_asks_one_thing_at_a_time(app, rb, monkeypatch):
    music = rb.MusicScreen(app)
    app.push(music)
    calls, real = [], app.api.music_all
    monkeypatch.setattr(app.api, "music_all", lambda what, cancel=None, **f: calls.append((what, f)) or real(what, cancel=cancel, **f))
    pending = later(app, monkeypatch)
    mix = rb.MixScreen(app, music)
    app.push(mix)
    assert len(pending) == 1 and mix.count is None
    mix.sel = [r[0] for r in mix.rows()].index("length")
    for _ in range(3):
        mix.action("ok")                                              # three changes while the first count is out
    assert len(pending) == 1
    run(pending, 1)
    assert not calls and len(pending) == 1 and mix.count is None      # nothing picked was RomM's totals; it's stale: asked once more
    run(pending, 1)
    assert mix.count and mix.count[0] > 0 and not mix.counting and not pending
    assert calls == [("tracks", {"max_duration": mix.filters["max_minutes"] * 60})]
    mix.draw(app.canvas)


def test_another_server_stops_the_player_and_a_new_sync_stops_the_one_running(app, rb, monkeypatch):
    scr = album(app, rb)
    scr.action("x")
    assert app.player.state == "playing"
    first = app.music["stop"]
    app.apply_config(reconnect=True)
    assert app.player.state == "stopped" and app.player.queue == [] and not app.player.out.busy
    scr.draw(app.canvas)
    # a track still on its way from the old server when the new one is set is never played
    rows = scr.listed()
    at_once = app.bg
    held = later(app, monkeypatch)
    scr.action("x")
    assert app.player.state == "loading" and len(held) == 1
    asked = held.pop(0)
    app.apply_config(reconnect=True)
    del held[:]
    run([asked])
    assert app.player.state == "stopped" and not app.player.fetching and not app.player.out.busy
    monkeypatch.setattr(app, "bg", at_once)
    app.music_in_tests = True
    app.cfg["menu_music"] = True
    rommlib.save_music_sources(dict(rommlib.load_music_sources(), playlists=[1]))
    app.sync_music()
    assert app.music["stop"] is not first and not app.music["stop"].is_set() and app.music["result"]["downloaded"] == 12
    either = rb.app._Either(app.music_stop, app.music["stop"])
    assert not either.is_set()
    app.music["stop"].set()
    assert either.is_set()
