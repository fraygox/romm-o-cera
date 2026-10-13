"""The party filter (Players: 2+, 3+, 4+), Surprise me and Recommended for you,
against tools/mock_romm.py."""
import json
import os
import time

import pytest

import rommlib


def test_max_players_reads_romms_player_counts():
    assert rommlib.max_players("1-4") == 4
    assert rommlib.max_players("2") == 2
    assert rommlib.max_players("Up to 8") == 8
    assert rommlib.max_players("1") == 1
    assert rommlib.max_players("") == rommlib.max_players(None) == rommlib.max_players("Many") == 0
    assert rommlib.max_players("1-2, 2022") == 2              # a four-digit number isn't a player count
    vals = ["1", "1-2", "1-4", "2", "4", "", None, "1-8"]
    assert rommlib.party_values(vals, 2) == ["1-2", "1-4", "2", "4", "1-8"]
    assert rommlib.party_values(vals, 3) == ["1-4", "4", "1-8"]
    assert rommlib.party_values(None, 2) == []


def test_api_filters_and_player_counts(mock_url):
    api = rommlib.Api(mock_url, "demo")
    counts = api.rom_filters()["player_counts"]
    assert "1-4" in counts and "1" in counts
    four = api.roms(platform_ids=[1], limit=500, player_counts=rommlib.party_values(counts, 4))
    allp = api.roms(platform_ids=[1], limit=1)
    assert 0 < four["total"] < allp["total"]
    assert all(rommlib.max_players(r["metadatum"]["player_count"]) >= 4 for r in four["items"])


def test_rom_filters_falls_back_on_an_older_romm(monkeypatch):
    api = rommlib.Api("http://127.0.0.1:9", "x")
    asked = []

    def get(path, params=None, timeout=20):
        asked.append(path)
        if path == "/api/roms/filters":
            raise rommlib.RommError("RomM error HTTP 422 on /api/roms/filters")
        return {"items": [], "filter_values": {"player_counts": ["1", "1-2"]}}
    monkeypatch.setattr(api, "get", get)
    assert api.rom_filters()["player_counts"] == ["1", "1-2"]
    assert asked == ["/api/roms/filters", "/api/roms"]


def _pump(app, until, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        with app.lock:
            fns, app.events_from_threads = app.events_from_threads, []
        for fn in fns:
            fn()
        if until():
            return
        time.sleep(0.02)
    raise AssertionError("timed out")


@pytest.fixture
def live(isolated, rb, mock_url):
    """The app against the mock RomM, platforms loaded."""
    import pygame
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    with open(rommlib.CONFIG_PATH, "w") as f:
        json.dump({"url": mock_url, "token": "demo"}, f)
    pygame.init()
    app = rb.App(None, headless=True)
    app.downloader.hold = True
    app.push(rb.HomeScreen(app))
    app.load_platforms()
    _pump(app, lambda: app.loaded)
    return app


@pytest.fixture
def opened(rb, monkeypatch):
    """The download prompt keeps the game it was opened for (as .rom), for the tests."""
    class Prompt(rb.DownloadConfirm):
        def __init__(self, app, rom):
            self.rom = rom
            super().__init__(app, rom)
    rb.patch(monkeypatch, "DownloadConfirm", Prompt)
    return Prompt


def _menu_row(app, label):
    return next(i for i, e in enumerate(app.modal.entries) if e and e[0] == label)


def test_party_filter_in_the_list_menu(live, rb):
    app = live
    psx = next(p for p in app.platforms if p["_system"] == "psx")
    gl = rb.GameListScreen(app, "PlayStation", platform=psx)
    app.push(gl)
    _pump(app, lambda: gl.total)
    everything = gl.total
    app.dispatch("x")
    m = app.modal
    i = _menu_row(app, "Players")
    assert m.entries[i][3] == "Any" and m.entries[i][4] == "choice"
    m.sel = i
    app.dispatch("right")                       # first use asks RomM for its player counts
    _pump(app, lambda: gl.players == 2 and gl.total is not None)
    assert app.player_counts and gl.total < everything
    assert app.modal.entries[_menu_row(app, "Players")][3] == "2+"
    loaded = [r for r in gl.items if r]
    assert loaded and all(rommlib.max_players(r["metadatum"]["player_count"]) >= 2 for r in loaded)
    app.dispatch("right")
    _pump(app, lambda: gl.players == 3 and gl.total is not None)
    three = gl.total
    app.dispatch("left")
    app.dispatch("left")
    _pump(app, lambda: gl.players == 0 and gl.total is not None)
    assert gl.total == everything and three < everything
    app.dispatch("right")                       # asked once a session
    assert gl.players == 2
    app.modal = None
    app.draw()


def test_party_filter_shows_in_the_header(live, rb, monkeypatch):
    app = live
    app.player_counts = ["1", "1-2", "1-4"]
    gl = rb.GameListScreen(app, "Theme: Lorem Ipsum Classics That Were Actually Good, a long collection name",
                           platform=app.platforms[0])
    app.push(gl)
    gl.region_i, gl.players = 1, 4
    gl.reset()
    _pump(app, lambda: gl.total is not None)
    drawn = []
    real = rb.games.text

    def spy(surf, font, s, *a, **k):
        drawn.append(s)
        return real(surf, font, s, *a, **k)
    rb.patch(monkeypatch, "text", spy)
    app.draw()
    assert any("USA + World, 4+ players" in s for s in drawn)


def test_party_filter_with_no_game_for_that_many(live, rb):
    app = live
    app.player_counts = ["1", "1-2"]              # nobody plays 3+ here: an empty list, not the whole one
    gl = rb.GameListScreen(app, "NES", platform=app.platforms[0])
    app.push(gl)
    gl.players = 3
    gl.reset()
    _pump(app, lambda: gl.total is not None)
    assert gl.total == 0 and gl.server_total == 0


def test_party_filter_on_a_fixed_list(live, rb):
    app = live
    roms = [{"id": i, "name": "Lorem %d" % i, "platform_id": 1, "metadatum": {"player_count": pc}}
            for i, pc in enumerate(["1", "1-2", "1-4", None, "4"])]
    gl = rb.GameListScreen(app, "Games like Lorem", static=roms)
    app.push(gl)
    gl._cycle_players(1)                        # a fixed list filters itself: RomM isn't asked
    assert app.player_counts is None
    assert [r["id"] for r in gl.items] == [1, 2, 4]
    gl._cycle_players(1)
    gl._cycle_players(1)
    assert [r["id"] for r in gl.items] == [2, 4]
    gl._cycle_players(1)
    assert len(gl.items) == 5


# ------------------------------------------------------------- Surprise me

def test_random_rom_and_surprise_against_the_mock(mock_url):
    api = rommlib.Api(mock_url, "demo")
    r = api.random_rom(platform_ids=[3])
    assert r["platform_id"] == 3
    assert api.random_rom(platform_ids=[999]) is None
    col = api.random_rom(collection=("collection_id", 6))           # "Series: Dolor Sit", 9 games
    assert col["id"] in [x["id"] for x in api.roms(collection=("collection_id", 6), limit=50)["items"]]

    def page(off, lim):
        return api.roms(platform_ids=[3], offset=off, limit=lim, player_counts=["1-4"])
    rom, why = api.surprise(page)
    assert why is None and rom["platform_id"] == 3 and rom["metadatum"]["player_count"] == "1-4"
    assert api.surprise(page, keep=lambda r: False) == (None, "skipped")
    assert api.surprise(lambda off, lim: {"items": [], "total": 0}) == (None, "empty")
    rom, why = api.surprise(page, scope={"platform_ids": [2]})        # RomM picks when nothing narrows it
    assert rom["platform_id"] == 2
    assert api.surprise(page, scope={"platform_ids": [999]}) == (None, "empty")


def test_surprise_without_romms_random_route(monkeypatch):
    """A RomM before 5.4 reads /api/roms/random as a rom id (422): the pages pick instead."""
    api = rommlib.Api("http://127.0.0.1:9", "x")

    def get(path, params=None, timeout=20):
        raise rommlib.RommError("RomM error HTTP 422 on %s" % path)
    monkeypatch.setattr(api, "get", get)
    items = [{"id": i} for i in range(400)]
    rom, why = api.surprise(lambda off, lim: {"items": items[off:off + lim], "total": 400},
                            scope={"platform_ids": [1]}, keep=lambda r: r["id"] % 2)
    assert why is None and rom["id"] % 2


def test_game_night_from_a_game_list_menu(live, rb):
    app = live
    gba = next(p for p in app.platforms if p["_system"] == "gba")
    gl = rb.GameListScreen(app, "Game Boy Advance", platform=gba)
    app.push(gl)
    _pump(app, lambda: gl.server_total)
    app.dispatch("x")
    i = _menu_row(app, "Game night")
    assert app.modal.entries[i][1] == "Roll one of these {:,} games for tonight".format(gl.server_total)
    app.modal.sel = i
    app.dispatch("ok")
    gn = app.stack[-1]
    assert isinstance(gn, rb.GameNightScreen) and gn.crumbs()[-1] == "Game night"
    _pump(app, lambda: gn.picks)
    assert gn.picks[0]["platform_id"] == gba["id"] and gn.pool == gl.server_total
    app.draw()


def test_surprise_respects_the_lists_filters(live, rb, opened):
    app = live
    app.player_counts = ["1", "1-2", "1-4"]
    gba = next(p for p in app.platforms if p["_system"] == "gba")
    gl = rb.GameListScreen(app, "Game Boy Advance", platform=gba)
    gl.players, gl.region_i = 4, 3                             # 4+ players, Japan
    gl.reset()
    app.push(gl)
    _pump(app, lambda: gl.total is not None)
    for _ in range(3):
        app.modal = None
        gl.surprise_me()
        _pump(app, lambda: app.modal is not None)
        rom = app.modal.rom
        assert rom["metadatum"]["player_count"] == "1-4" and "Japan" in rom["regions"]


def test_surprise_opens_the_menu_of_a_game_already_here(live, rb):
    app = live
    roms = [{"id": 5, "name": "Lorem", "platform_id": 3, "fs_name": "Lorem.zip", "fs_name_no_ext": "Lorem"}]
    p = os.path.join(rommlib.ROMS_ROOT, "gba", "Lorem.zip")
    os.makedirs(os.path.dirname(p))
    open(p, "wb").write(b"x")
    app.library.mark(roms[0], "gba", [p])
    gl = rb.GameListScreen(app, "Games like Ipsum", static=roms)
    app.push(gl)
    gl.surprise_me()
    assert isinstance(app.modal, rb.ManageMenu)
    app.modal = None
    gl._toggle_hide()                                          # nothing left once the box's games are hidden
    gl.surprise_me()
    assert app.modal is None and app.toasts[-1][0] == "No game in this list to pick from"


def test_game_night_from_home_platforms_and_collections(live, rb):
    """Home Y, a platform's X and a collection's menu open Game night on their own scope."""
    app = live
    home = app.stack[0]
    assert ("Y", "Game night") in home.hints()
    app.dispatch("y")
    gn = app.stack[-1]
    assert isinstance(gn, rb.GameNightScreen) and gn.scope.whole
    _pump(app, lambda: gn.picks)
    assert app.system_for(gn.picks[0])
    app.pop()

    plats = rb.PlatformScreen(app)
    app.push(plats)
    plats.sel = next(i for i, p in enumerate(app.platforms) if p["_system"] == "snes")
    assert ("X", "Game night") in plats.hints()
    app.dispatch("x")
    gn = app.stack[-1]
    _pump(app, lambda: gn.picks)
    assert app.platform_by_id[gn.picks[0]["platform_id"]]["_system"] == "snes"
    assert gn.scope.systems == {"snes"}
    app.pop()

    cs = rb.CollectionScreen(app)
    app.push(cs)
    _pump(app, lambda: cs.items)
    it = next(x for x in cs.items if x.get("param") == "collection_id")
    cs.menu(it)
    app.modal.sel = _menu_row(app, "Game night")
    app.dispatch("ok")
    gn = app.stack[-1]
    _pump(app, lambda: gn.picks)
    ids = [r["id"] for r in app.api.roms_all(collection=("collection_id", it["id"]))]
    assert gn.picks[0]["id"] in ids


# ------------------------------------------------------ Recommended for you

def test_recommendation_why_words_it_like_romm():
    why = rommlib.recommendation_why
    assert why({"seed_rom_name": "Lorem", "reasons": [{"facet": "franchise", "value": "Dolor Sit"},
                                                      {"facet": "decade", "value": "1990"}]}) == \
        "Because you played Lorem · Dolor Sit · 1990s"
    assert why({"reasons": [{"facet": "top_rated", "value": ""}]}) == "Highly rated"
    assert why({"reasons": [{"facet": "igdb", "value": ""}, {"facet": "genre", "value": "Puzzle"},
                            {"facet": "genre", "value": "Puzzle"}, "junk", {"facet": "theme"}]}) == \
        "Similar on IGDB · Puzzle"
    assert why({}) == why({"reasons": None, "seed_rom_name": " "}) == ""


@pytest.fixture
def recs_mode(mock_url):
    import requests

    def mode(m):
        requests.post(mock_url + "/mock/recs", json={"mode": m}, timeout=5)
    yield mode
    mode("ok")


def test_api_recommendations(mock_url, recs_mode):
    api = rommlib.Api(mock_url, "demo")
    got = api.recommendations(10)
    assert len(got) == 10 and got[0]["score"] >= got[-1]["score"]
    assert got[0]["rom"]["id"] and isinstance(got[0]["reasons"], list)
    recs_mode("empty")
    assert api.recommendations() == []
    recs_mode("404")
    assert api.recommendations() is None


def test_recommended_for_you_on_home(live, rb, monkeypatch):
    app = live
    home = app.stack[0]
    home.sel = 6                                 # Settings: focus stays on it when the row appears
    app.draw()
    _pump(app, lambda: app.recs)
    keys = [it[0] for it in home.items()]
    assert keys[keys.index("recent") + 1] == "recommended"
    assert keys[home.sel] == "settings"
    assert all(app.system_for(rom) for rom, _, _ in app.recs)
    home.sel = keys.index("recommended")
    app.draw()                                   # the shelf shows them
    app.dispatch("ok")
    gl = app.stack[-1]
    assert gl.title == "Recommended for you" and gl.crumbs() == ["Recommended for you"]
    assert len(gl.items) == len(app.recs)
    rom = gl.items[0]
    assert gl.why[rom["id"]].startswith("Because you played ")
    drawn = []
    real = rb.games.text

    def spy(surf, font, s, *a, **k):
        drawn.append(s)
        return real(surf, font, s, *a, **k)
    rb.patch(monkeypatch, "text", spy)
    app.draw()
    assert "Why" in drawn and any(s.startswith("Because you played") for s in drawn)


@pytest.mark.parametrize("mode", ["empty", "404", "error"])
def test_no_recommendations_hides_the_row_quietly(isolated, rb, mock_url, recs_mode, mode, monkeypatch):
    import pygame
    recs_mode("empty" if mode == "error" else mode)
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    with open(rommlib.CONFIG_PATH, "w") as f:
        json.dump({"url": mock_url, "token": "demo"}, f)
    pygame.init()
    app = rb.App(None, headless=True)
    if mode == "error":
        def boom(limit=50):
            raise rommlib.RommError("RomM rejected the token (HTTP 403)")
        monkeypatch.setattr(app.api, "recommendations", boom)
    app.push(rb.HomeScreen(app))
    app.load_platforms()
    _pump(app, lambda: app.loaded)
    app.draw()
    asking = app.recs                            # [] while RomM is asked; its answer is a new list
    if mode == "error":
        time.sleep(0.3)                          # an error leaves the asking list: nothing to wait for
        _pump(app, lambda: True)
    else:
        _pump(app, lambda: app.recs is not asking)
    app.draw()
    assert app.recs == []
    assert "recommended" not in [it[0] for it in app.stack[0].items()]
    assert not [t for t in app.toasts if t[1] == rb.theme.RED]


def test_a_surprise_pick_offers_another_on_y(live, rb, opened):
    """Game night: Y on the surprise prompt rolls again; the prompt says it's a surprise."""
    app = live
    gba = next(p for p in app.platforms if p["_system"] == "gba")
    gl = rb.GameListScreen(app, "Game Boy Advance", platform=gba)
    app.push(gl)
    _pump(app, lambda: gl.server_total)
    gl.surprise_me()
    _pump(app, lambda: app.modal is not None)
    first = app.modal
    assert first.title.startswith("Surprise: ") and first.alt[0] == "Another"
    app.targets = []
    app.draw()                                            # three buttons fit the dialog
    app.dispatch("y")
    _pump(app, lambda: app.modal is not None and app.modal is not first)
    assert isinstance(app.modal, rb.DownloadConfirm) and app.modal.alt and app.modal.rom["platform_id"] == gba["id"]


def test_a_plain_download_prompt_has_no_another(live, rb, opened):
    app = live
    rom = {"id": 1, "name": "Lorem", "platform_id": next(p for p in app.platforms if p["_system"] == "gba")["id"],
           "fs_name": "Lorem.gba", "fs_size_bytes": 10}
    p = rb.DownloadConfirm(app, rom)
    assert p.alt is None
    app.modal = p
    app.dispatch("y")
    assert app.modal is p                                 # Y does nothing there


def test_a_surprise_from_a_loaded_list_moves_the_cursor_to_it(live, rb, opened):
    """The detail panel behind the prompt shows the pick, not the row you were on."""
    app = live
    gba = next(p for p in app.platforms if p["_system"] == "gba")
    gl = rb.GameListScreen(app, "Game Boy Advance", platform=gba)
    app.push(gl)
    _pump(app, lambda: gl.server_total)
    rb.surprise(app, pool=[r for r in gl.items if r][:30])
    pick = app.modal.rom
    assert gl.items[gl.sel] is pick


def test_the_ready_toast_cant_be_tapped_through_a_dialog(live, rb):
    app = live
    fired = []
    app.toasts.append(("Ready: Lorem", rb.theme.GREEN, time.time() + 6, 1, lambda: fired.append(1)))
    app.modal = rb.Confirm(app, "Lorem", "Ipsum", "OK", lambda: None)
    app.targets = []
    app.draw()
    assert not any(fn and fn.__code__ is app.toasts[-1][4].__code__ for _, fn in app.targets)
    app.modal = None
    app.targets = []
    app.draw()
    assert any(fn and fn.__code__ is app.toasts[-1][4].__code__ for _, fn in app.targets)
