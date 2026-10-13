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
