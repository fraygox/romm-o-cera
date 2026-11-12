"""The Music screen (rbui/screens/music.py) and Music on Home's rail, against the mock RomM's jukebox."""
import os

import pytest

import rommlib


@pytest.fixture
def app(isolated, rb, monkeypatch, fast_mock_url):
    """An App on the mock RomM whose background work runs at once, menu-music syncs included."""
    import pygame
    monkeypatch.setenv("ROMM_URL", fast_mock_url)
    monkeypatch.setenv("ROMM_TOKEN", "demo")
    pygame.init()
    a = rb.App(None, headless=True)
    a.music_in_tests = True

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
    return a


def music(app, rb):
    scr = rb.MusicScreen(app)
    app.push(scr)
    scr.enter()
    return scr


def row(scr, key):
    rows = scr.rows()
    i = next(i for i, r in enumerate(rows) if r[0] == key)
    scr.sel = i
    return rows[i]


def files():
    d = rommlib.music_dir()
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


def test_music_is_on_homes_rail_only_when_romm_has_soundtracks(app, rb):
    home = rb.HomeScreen(app)
    app.push(home)
    home.enter()
    assert app.music_total > 0 and [k for k, _, _ in home.RAIL][-1] == "music"
    assert len(rb.HomeScreen.RAIL) == len(home.RAIL) - 1          # the class's list is the rail without it
    assert [k for k, _, _ in home.RAIL][:-1] == [k for k, _, _ in rb.HomeScreen.RAIL]      # the others keep their places
    home.rail_i = len(home.RAIL) - 1                               # on Music when RomM's soundtracks go away
    app.music_total = 0
    home.set_rail()
    assert "music" not in [k for k, _, _ in home.RAIL] and home.rail_i == 0
    home.rail_i = [k for k, _, _ in home.RAIL].index("settings")
    app.music_total = 5
    home.set_rail()
    assert home.RAIL[home.rail_i][0] == "settings"                  # the focus stays on its pill
    home.open_rail("music")
    assert isinstance(app.stack[-1], rb.MusicScreen)
    assert dict(home.rail_rows("music"))["Menu music"] == "Off"


def test_the_screen_lists_the_switch_the_cap_and_every_source(app, rb):
    scr = music(app, rb)
    keys = [r[0] for r in scr.rows()]
    assert keys == ["on", "cap", "favourites", "on_box", "playlist:1", "playlist:2"]
    assert row(scr, "favourites")[2].endswith("you starred in RomM") and row(scr, "playlist:1")[2] == "Playlist · 12 tracks · shared by sam"
    assert row(scr, "playlist:2")[2].endswith("private")
    assert scr.summary()[0][0] == "Menu music is off"
    import pygame
    scr.draw(pygame.Surface((1280, 720)))                           # every row and the panel draw


def test_switching_it_on_and_a_source_brings_the_tracks_and_off_asks_first(app, rb):
    scr = music(app, rb)
    row(scr, "playlist:1")
    scr.action("ok")
    assert rommlib.load_music_sources()["playlists"] == [1] and files() == []        # saved; nothing fetched while it's off
    row(scr, "on")
    scr.action("ok")
    assert app.cfg["menu_music"] is True and len(files()) == 12
    assert scr.summary()[0][0].startswith("12 tracks in the menus") and scr.summary()[1][0] == "Up to date"
    row(scr, "favourites")
    scr.action("ok")
    n = len(files())
    assert n > 12 and rommlib.music_status()["tracks"] == n
    row(scr, "on")
    scr.action("ok")
    assert isinstance(app.modal, rb.Confirm) and files() and app.cfg["menu_music"] is True      # asked, nothing gone yet
    assert "%d tracks" % n in app.modal.body
    app.modal.action("left")
    app.modal.action("ok")
    assert app.cfg["menu_music"] is False and files() == [] and not os.path.exists(rommlib.music_dir())
    assert rommlib.load_music_sources()["favourites"] is True      # the choice is kept for next time


def test_the_cap_cycles_and_syncs(app, rb):
    scr = music(app, rb)
    row(scr, "cap")
    seen = []
    for _ in rommlib.MUSIC_CAP_CHOICES:
        scr.action("ok")
        seen.append(app.cfg["menu_music_cap_gb"])
    assert seen == [20, 50, 0, 1, 2, 5, 10]
    assert rommlib.load_config()["menu_music_cap_gb"] == 10
    app.cfg["menu_music_cap_gb"] = 3                               # set by hand to something not offered
    scr.action("ok")
    assert app.cfg["menu_music_cap_gb"] == rommlib.MUSIC_CAP_CHOICES[0]


def test_a_token_that_cant_read_lists_shows_them_unavailable_with_the_reason(app, rb, fast_mock_url):
    app.api = rommlib.Api(fast_mock_url, "demo-no-lists")
    scr = music(app, rb)
    fav = row(scr, "favourites")
    assert fav[4] is False and fav[2] == "Not available with this token"
    assert [r[0] for r in scr.rows()] == ["on", "cap", "favourites", "on_box"]      # no playlists to list
    scr.action("ok")
    assert rommlib.load_music_sources()["favourites"] is False
    assert "playlists.read" in app.toasts[-1][0]
    assert "playlists.read" in scr.about("favourites", "favourites", False)
    row(scr, "on_box")
    scr.action("ok")
    assert rommlib.load_music_sources()["on_box"] is True          # what needs no list still works


def test_a_game_and_a_mix_in_the_saved_choice_show_as_rows(app, rb):
    g = app.api.music_all("games")[0]
    rommlib.save_music_sources({"favourites": False, "on_box": False, "playlists": [9], "games": [g["rom_id"], 424242],
                                "mixes": [{"name": "Rainy day", "on": False, "filters": {"genre": ["Ambient"], "decade": [1990]}}]})
    scr = music(app, rb)
    assert row(scr, "game:%d" % g["rom_id"])[1] == g["name"] and row(scr, "game:424242")[2] == "RomM has no soundtrack for it now"
    assert row(scr, "mix:Rainy day")[2] == "Mix · Ambient · 1990s" and row(scr, "mix:Rainy day")[3] is False
    assert row(scr, "playlist:9")[1] == "A playlist RomM no longer lists"
    scr.action("ok")                                                # switched off: it leaves the list
    assert "playlist:9" not in [r[0] for r in scr.rows()]
    row(scr, "mix:Rainy day")
    scr.action("ok")
    assert rommlib.load_music_sources()["mixes"][0]["on"] is True
    assert rb.mix_summary({"game_genre": ["A", "B", "C", "D"], "platform": [1, 2], "max_minutes": 5}) == "A, B, C… · 2 platforms · up to 5 min"


def test_sync_now_says_why_when_its_off_and_the_app_asks_once_more_after_a_change_mid_sync(app, rb):
    scr = music(app, rb)
    scr.action("x")
    assert "off" in app.toasts[-1][0]
    app.cfg["menu_music"] = True
    app.music["busy"] = True
    app.sync_music()
    assert app.music["again"] is True                               # runs once more when the one under way ends
    app.music["busy"] = False
    rommlib.save_music_sources(dict(rommlib.load_music_sources(), playlists=[2]))
    app.sync_music()
    assert app.music["busy"] is False and app.music["again"] is False and len(files()) == 10
    assert rb.minutes(4080) == "68 min" and rb.minutes(12000) == "3 h 20 min" and rb.cap_label(0) == "No limit"
