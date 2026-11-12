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
    assert isinstance(app.stack[-1], rb.JukeboxScreen) and app.stack[-1].crumbs() == ["Music"]
    assert dict(home.rail_rows("music"))["Menu music"] == "Off"


def test_the_screen_lists_the_switch_the_cap_and_every_source(app, rb):
    scr = music(app, rb)
    keys = [r[0] for r in scr.rows()]
    assert keys == ["on", "favourites", "on_box", "playlist:1", "playlist:2", "add_game", "new_mix", "cap"]
    assert row(scr, "favourites")[2].endswith("you starred in RomM") and row(scr, "playlist:1")[2] == "Playlist · 12 tracks · shared by sam"
    assert row(scr, "playlist:2")[2].endswith("private")
    assert scr.summary()[0][0].startswith("Off") and scr.crumbs()[-1] == "Menu music"
    import pygame
    canvas = pygame.Surface((1280, 720))
    scr.draw(canvas)                                                # the switch's card, the rows and the panel draw
    # the rows sit under two headings, and the list scrolls to keep the selection in view
    placed, heads = scr.placed(scr.rows(), 1)
    assert [t for t, _ in heads] == ["What plays in the menus", "Space"] and [i for i, _ in placed] == list(range(1, len(keys)))
    many = [("on", "", "", False, True)] + [("game:%d" % i, "Game %d" % i, "", True, True) for i in range(20)] + [("cap", "", "", None, True)]
    placed, heads = scr.placed(many, 1)
    assert len(placed) < 20 and placed[-1][1] + scr.ROW_H - 6 <= scr.LIST.bottom and [t for t, _ in heads] == []
    rommlib.save_music_sources({"favourites": False, "on_box": False, "playlists": [], "games": list(range(1, 15)), "mixes": []})
    scr.enter()
    scr.sel = len(scr.rows()) - 1                                   # the space row, past the fold
    scr.draw(canvas)
    assert scr.scroll > 1 and scr.rows()[scr.sel][0] == "cap"
    scr.sel = 0
    scr.draw(canvas)
    scr.sel = 1
    scr.draw(canvas)
    assert scr.scroll == 1


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
    assert [r[0] for r in scr.rows()] == ["on", "favourites", "on_box", "add_game", "new_mix", "cap"]      # no playlists to list
    scr.action("ok")
    assert rommlib.load_music_sources()["favourites"] is False
    assert "playlists.read" in app.toasts[-1][0]
    # switched on before the token changed: it can't be read, but it can still be switched off
    rommlib.save_music_sources(dict(rommlib.load_music_sources(), favourites=True, playlists=[1]))
    scr.enter()
    assert row(scr, "favourites")[3] is True and row(scr, "favourites")[4] is False
    scr.action("ok")
    assert rommlib.load_music_sources()["favourites"] is False
    gone = row(scr, "playlist:1")
    assert gone[3] is True and gone[1] == "A playlist RomM no longer lists"
    scr.action("ok")
    assert rommlib.load_music_sources()["playlists"] == []
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
    assert not app.music["stop"].is_set()
    app.sync_music()
    assert app.music["again"] is True and app.music["stop"].is_set()      # the one under way is told to stop; it runs once more after
    app.music["busy"] = False
    rommlib.save_music_sources(dict(rommlib.load_music_sources(), playlists=[2]))
    app.sync_music()
    assert app.music["busy"] is False and app.music["again"] is False and len(files()) == 10
    assert rb.minutes(4080) == "68 min" and rb.minutes(12000) == "3 h 20 min" and rb.cap_label(0) == "No limit"


# ---------------------------------------------------------------- adding a game, and mixes

def test_a_games_soundtrack_is_added_from_the_list_of_games_that_have_one(app, rb):
    scr = music(app, rb)
    row(scr, "add_game")
    scr.action("ok")
    pick = app.stack[-1]
    pick.enter()
    assert isinstance(pick, rb.MusicPickScreen) and pick.single and len(pick.items) == len(app.api.music_all("games"))
    assert pick.hints()[0] == ("A", "Add")
    import pygame
    pick.draw(pygame.Surface((1280, 720)))
    pick.sel = 2
    chosen = pick.items[2]
    pick.action("ok")
    assert app.stack[-1] is scr and rommlib.load_music_sources()["games"] == [chosen["value"]]
    assert row(scr, "game:%d" % chosen["value"])[1] == chosen["label"]
    scr.action("ok")                                                # switched off again: it leaves the list
    assert rommlib.load_music_sources()["games"] == []
    row(scr, "add_game")
    scr.action("ok")
    app.stack[-1].enter()
    app.stack[-1].action("back")                                    # left without choosing: nothing added
    assert rommlib.load_music_sources()["games"] == [] and app.stack[-1] is scr


def test_a_mix_is_made_by_ticking_values_counted_as_you_go_and_saved(app, rb):
    scr = music(app, rb)
    row(scr, "new_mix")
    scr.action("ok")
    mix = app.stack[-1]
    mix.enter()
    total = len(app.api.music_all("tracks"))
    assert isinstance(mix, rb.MixScreen) and mix.count[0] == total and mix.title() == "Everything"
    mix.sel = [r[0] for r in mix.rows()].index("game_genre")
    mix.action("ok")
    pick = app.stack[-1]
    pick.enter()
    assert not pick.single and pick.hints()[0] == ("A", "Pick") and pick.hints()[-1] == ("B", "Done")
    genres = [r["value"] for r in pick.items[:2]]
    for i in (0, 1):
        pick.sel = i
        pick.action("ok")
    pick.action("ok")                                               # the second one unticked again
    pick.action("back")
    assert app.stack[-1] is mix and mix.filters == {"game_genre": genres[:1]}
    want = [t for t in app.api.music_all("tracks") if genres[0] in t["game_genres"]]
    assert mix.count == (len(want), sum(t["duration_seconds"] or 0 for t in want)) and 0 < len(want) < total
    mix.sel = [r[0] for r in mix.rows()].index("length")
    mix.action("ok")
    assert mix.filters["max_minutes"] == 2 and mix.count[0] < len(want)
    for _ in range(len(rb.MIX_LENGTHS) - 1):
        mix.action("ok")
    assert "max_minutes" not in mix.filters                         # round to "Any length"
    mix.sel = [r[0] for r in mix.rows()].index("decade")
    mix.action("ok")
    pick = app.stack[-1]
    pick.enter()
    assert all(r["label"].endswith("0s") for r in pick.items) and sum(r["count"] for r in pick.items) <= total
    pick.action("back")                                             # nothing ticked: the row stays "Any"
    assert "decade" not in mix.filters
    import pygame
    mix.draw(pygame.Surface((1280, 720)))
    assert "save" in [r[0] for r in mix.rows()] and "delete" not in [r[0] for r in mix.rows()]
    mix.sel = [r[0] for r in mix.rows()].index("save")
    mix.action("ok")
    saved = rommlib.load_music_sources()["mixes"]
    assert saved == [{"name": genres[0], "on": True, "filters": {"game_genre": genres[:1]}}] and app.stack[-1] is scr
    assert row(scr, "mix:" + genres[0])[3] is True and ("Y", "Edit mix") in scr.hints()


def test_a_mix_can_be_renamed_changed_and_deleted_and_two_cant_share_a_name(app, rb):
    rommlib.save_music_sources({"favourites": False, "on_box": False, "playlists": [], "games": [], "mixes": [
        {"name": "Rainy day", "on": True, "filters": {"genre": ["Ambient"]}},
        {"name": "Loud", "on": False, "filters": {"genre": ["Chiptune"]}}]})
    app.cfg["menu_music"] = True
    scr = music(app, rb)
    app.sync_music()
    before = len(files())
    assert before == len(app.api.music_all("tracks", genre="Ambient")) > 0
    row(scr, "mix:Rainy day")
    scr.action("y")
    mix = app.stack[-1]
    mix.enter()
    assert mix.was == "Rainy day" and "delete" in [r[0] for r in mix.rows()] and mix.crumbs()[-1] == "Change mix"
    mix.rename(" loud ")                                            # (the keyboard's Save; it pops the keyboard screen)
    app.push(mix)
    mix.sel = [r[0] for r in mix.rows()].index("save")
    mix.action("ok")
    assert "of its own" in app.toasts[-1][0] and app.stack[-1] is mix      # "Loud" is taken, whatever the case
    mix.name = "Calm"
    mix.action("ok")
    assert [m["name"] for m in rommlib.load_music_sources()["mixes"]] == ["Calm", "Loud"] and len(files()) == before
    assert rommlib.load_music_state()["tracks"] and all(r["sources"] == ["mix:Calm"] for r in rommlib.load_music_state()["tracks"].values())
    row(scr, "mix:Calm")
    scr.action("y")
    mix = app.stack[-1]
    mix.sel = [r[0] for r in mix.rows()].index("delete")
    mix.action("ok")
    assert isinstance(app.modal, rb.Confirm) and "Calm" in app.modal.body
    app.modal.action("left")
    app.modal.action("ok")
    assert [m["name"] for m in rommlib.load_music_sources()["mixes"]] == ["Loud"] and files() == []
    row(scr, "on")
    scr.action("y")                                                 # Y on a row that isn't a mix does nothing
    assert app.stack[-1] is scr


# ---------------------------------------------------------------- the game page

def test_a_games_page_shows_its_soundtrack_and_offers_it_for_the_menu_music(app, rb):
    g = app.api.music_all("games")[0]
    rom = app.api.rom(g["rom_id"])
    tracks = [t for t in app.api.music_all("tracks", rom_id=g["rom_id"])]
    page = rb.GameScreen(app, rom)
    app.push(page)
    facts = dict(page.facts(page.record()))
    assert facts["Soundtrack"] == "%d tracks · %s" % (len(tracks), rb.minutes(sum(t["duration_seconds"] or 0 for t in tracks)))
    page.open_menu()
    entry = next(e for e in app.modal.entries if e and e[0] == "Add soundtrack to menu music")
    app.modal = None
    entry[2]()
    assert rommlib.load_music_sources()["games"] == [g["rom_id"]] and files() == []        # saved; menu music is off
    assert "Menu music is off" in app.toasts[-1][0]
    assert dict(page.facts(page.record()))["Soundtrack"].endswith("in the menu music")
    app.cfg["menu_music"] = True
    app.sync_music()
    playable = [t for t in tracks if rommlib.music_playable(t)]
    assert len(files()) == len(playable) > 0
    page.open_menu()
    entry = next(e for e in app.modal.entries if e and e[0] == "Remove soundtrack from menu music")
    app.modal = None
    entry[2]()
    assert rommlib.load_music_sources()["games"] == [] and files() == []
    # a game without one: no line, nothing offered
    other = next(r for r in app.api.roms(platform_ids=[rom["platform_id"]], limit=50)["items"] if not r.get("has_soundtrack"))
    page.show(other)
    assert dict(page.facts(page.record()))["Soundtrack"] == ""
    page.open_menu()
    assert not [e for e in app.modal.entries if e and "soundtrack" in e[0].lower()]
