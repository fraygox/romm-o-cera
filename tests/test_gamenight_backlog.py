"""Game night (Surprise me as a screen) and Backlog, against tools/mock_romm.py."""
import os

import rommlib
from test_party_surprise_recs import _menu_row, _pump, live  # noqa: F401 (live: the fixture)


def _install(app, roms):
    for r in roms:
        sy = app.system_for(r)
        p = os.path.join(rommlib.ROMS_ROOT, sy, r["fs_name_no_ext"] + ".chd")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "wb").write(b"x")
        app.library.mark(r, sy, [p])


def _platform(app, system):
    return next(p for p in app.platforms if p["_system"] == system)


# ------------------------------------------------------------------ the rolls

def test_roll_from_the_library_and_for_a_party(live, rb):
    app = live
    scope = rb.Scope.library(app)
    rom, why, pool = rb.gamenight.roll(app, scope)
    assert why is None and app.system_for(rom) and pool > 100
    rom, why, four = rb.gamenight.roll(app, scope, players=4)
    assert rommlib.max_players(rom["metadatum"]["player_count"]) >= 4 and 0 < four < pool
    app.player_counts = ["1", "1-2"]                     # nobody plays 3+ in this library
    assert rb.gamenight.roll(app, scope, players=3) == (None, "empty", 0)


def test_roll_only_whats_on_the_box(live, rb):
    app = live
    psx = _platform(app, "psx")
    scope = rb.Scope.platform(app, psx)
    assert rb.gamenight.roll(app, scope, here=True) == (None, "empty", 0)
    roms = app.api.roms(platform_ids=[psx["id"]], limit=5)["items"]
    _install(app, roms[:3])
    seen = set()
    for _ in range(12):
        rom, why, pool = rb.gamenight.roll(app, scope, here=True, avoid=list(seen))
        assert pool == 3 and rom["id"] in {r["id"] for r in roms[:3]}
        seen.add(rom["id"])
    assert len(seen) == 3                                # each one before any comes again
    lib = rb.Scope.library(app)
    assert rb.gamenight.roll(app, lib, here=True)[2] == 3
    gba = rb.Scope.platform(app, _platform(app, "gba"))
    assert rb.gamenight.roll(app, gba, here=True) == (None, "empty", 0)


def test_roll_from_a_loaded_list_avoids_repeats(live, rb):
    app = live
    pid = _platform(app, "gba")["id"]
    pool = [{"id": i, "name": "Lorem %d" % i, "platform_id": pid, "fs_name": "L%d.gba" % i,
             "fs_name_no_ext": "L%d" % i, "metadatum": {"player_count": "1-2" if i % 2 else "1"}}
            for i in range(1, 5)]
    scope = rb.Scope("Games like Lorem", pool=pool)
    rom, why, n = rb.gamenight.roll(app, scope, players=2, avoid={1})
    assert rom["id"] == 3 and n == 2
    rom, _, _ = rb.gamenight.roll(app, scope, players=2, avoid=[1, 3])
    assert rom["id"] == 1                                # nothing fresh: an older one, not the latest again
    rom, _, _ = rb.gamenight.roll(app, scope, players=2, avoid=[1, 3, 3])
    assert rom["id"] == 1
    rom, _, _ = rb.gamenight.roll(app, rb.gamenight.Scope("Lorem", pool=pool[:1]), avoid=[1])
    assert rom["id"] == 1                                # the only one: a repeat beats nothing
    assert rb.gamenight.roll(app, scope, players=4) == (None, "empty", 0)


# ------------------------------------------------------------------ the screen

def test_game_night_screen(live, rb, monkeypatch):
    app = live
    app.night_opts = {"players": 0, "here": False}
    gn = rb.GameNightScreen(app, rb.Scope.platform(app, _platform(app, "gba")))
    app.push(gn)
    _pump(app, lambda: gn.picks)
    for k in range(6):                                   # Y: another, the one before is passed on
        app.dispatch("y")
        _pump(app, lambda: len(gn.picks) == min(k + 2, rb.gamenight.KEEP + 1) and not gn.rolling)
    assert gn.rolls == 7 and len(gn.picks) == rb.gamenight.KEEP + 1 and gn.sel == len(gn.picks) - 1
    app.draw()
    newest = gn.picks[-1]
    app.dispatch("up")
    assert gn.current() is gn.picks[-2]
    app.dispatch("down")
    assert gn.current() is newest
    app.dispatch("ok")                                   # A: the pick's page (its verb is there)
    assert isinstance(app.stack[-1], rb.GameScreen) and app.stack[-1].rom is newest
    app.pop()
    app.dispatch("right")                                # who's playing: 2+, and a roll for them
    assert app.night_opts["players"] == 2
    _pump(app, lambda: not gn.rolling)
    assert rommlib.max_players(gn.current()["metadatum"]["player_count"]) >= 2
    assert "for 2+ players" in gn.rolling_from()
    app.dispatch("x")
    m = app.modal
    assert m.entries[_menu_row(app, "Players")][3] == "2 players"
    m.sel = _menu_row(app, rb.gamenight.HERE)
    app.dispatch("ok")                                   # on, in place: nothing of gba is here
    assert app.night_opts["here"] is True and m.entries[m.sel][3] is True
    _pump(app, lambda: not gn.rolling)
    assert gn.why == "empty" and gn.nothing()[0] == "No game on this box for 2+ players"
    app.modal = None
    app.draw()
    played = []
    _install(app, [newest])
    monkeypatch.setattr(app, "play_now", lambda path, system, title: played.append(path))
    gn.sel = len(gn.picks) - 1
    gn.picks[gn.sel] = newest
    assert gn.hints()[0] == ("A", "Open")
    app.dispatch("ok")                                   # its page: Play now is the big button there
    page = app.stack[-1]
    assert isinstance(page, rb.GameScreen) and page.hints()[0] == ("A", "Play now")
    page.action("ok")
    assert played


def test_game_night_says_what_it_rolls_from(live, rb):
    app = live
    gn = rb.GameNightScreen(app, rb.Scope.library(app))
    gn.pool, gn.opts = 1234, {"players": 3, "here": False}
    assert gn.rolling_from() == "Rolling from 1,234 games in your library, for 3+ players"
    gn.pool, gn.opts = 4, {"players": 0, "here": True}
    assert gn.rolling_from() == "Rolling from 4 games on this box"
    gn.scope = rb.Scope.platform(app, _platform(app, "psx"))
    gn.opts = {"players": 2, "here": True}
    assert gn.rolling_from() == "Rolling from 4 games on this box in PlayStation, keeping the ones for 2+ players"
    gn.pool = 0
    assert gn.rolling_from() == ""


# ------------------------------------------------------------------ backlog

def test_backlog_lists_romms_backlog_and_what_fits(live, rb, monkeypatch):
    app = live
    bl = rb.BacklogScreen(app)
    app.push(bl)
    _pump(app, lambda: not bl.loading)
    assert len(bl.items) == bl.total == 20
    assert all(rommlib.on_user_list(r, "backlog") for r in bl.items)
    _install(app, [r for r in bl.items if app.system_for(r)][:2])
    app.draw()                                           # the plan is worked out once the list is in
    _pump(app, lambda: bl.plan)
    p = bl.plan
    assert p["here"] == 2 and not p["wait"]
    todo_ids = [r["id"] for r in p["todo"]]
    assert todo_ids == [r["id"] for r in bl.items if r["id"] in set(todo_ids)]      # RomM's order
    assert all(app.rom_status(r)[0] != "installed" and app.system_for(r) for r in p["todo"])
    assert sum(n for _, n, _ in p["platforms"]) == len(p["todo"])
    assert ("Y", "Download my backlog") in bl.hints()
    queued = []
    monkeypatch.setattr(app, "queue_many", lambda roms, title, after=None: queued.append(roms))
    app.dispatch("y")
    assert queued == [p["todo"]]
    app.dispatch("x")
    assert [e[0] for e in app.modal.entries if e][1:3] == ["Download my backlog", "Free up space"]
    app.modal = None


def test_backlog_waits_for_space(live, rb):
    app = live
    app.cfg["keep_free_gb"] = 10 ** 6                    # nothing fits
    bl = rb.BacklogScreen(app)
    app.push(bl)
    _pump(app, lambda: not bl.loading)
    app.draw()
    _pump(app, lambda: bl.plan)
    assert len(bl.plan["wait"]) == len(bl.plan["todo"]) > 0
    rom = next(r for r in bl.items if r["id"] in bl.plan["wait"])
    assert bl.row(rom)[2].startswith("Waits for space") and bl.row(rom)[3] == rb.theme.AMBER
    app.draw()


def test_backlog_redoes_the_plan_when_the_queue_changes(live, rb):
    app = live
    bl = rb.BacklogScreen(app)
    app.push(bl)
    _pump(app, lambda: not bl.loading)
    app.draw()
    _pump(app, lambda: bl.plan)
    first = bl.plan
    rom = first["todo"][0]
    app.downloader.enqueue(rom, app.system_for(rom))
    app.draw()
    _pump(app, lambda: bl.plan is not first)
    assert rom["id"] not in {r["id"] for r in bl.plan["todo"]}
    assert bl.row(rom)[2] == "In the queue"


def test_backlog_empty_and_error(live, rb, monkeypatch):
    app = live
    monkeypatch.setattr(app.api, "user_list", lambda *a, **k: {"items": [], "total": 0, "filtered": False})
    bl = rb.BacklogScreen(app)
    app.push(bl)
    _pump(app, lambda: not bl.loading)
    app.draw()
    assert bl.items == [] and bl.plan is None and ("X", rb.pads.ACTIONS) in bl.hints()

    def boom(*a, **k):
        raise rommlib.RommError("RomM rejected the token (HTTP 403)")
    monkeypatch.setattr(app.api, "user_list", boom)
    bl.load()
    _pump(app, lambda: not bl.loading)
    assert bl.load_error and "403" in bl.load_error
    app.draw()


def test_backlog_from_collections(live, rb):
    app = live
    cs = rb.CollectionScreen(app)
    app.push(cs)
    _pump(app, lambda: cs.kinds)
    cs.row = len(cs.kinds) - 1                                    # Browse by: the Backlog door
    cs.col = next(i for i, it in enumerate(cs.kinds[-1]["items"]) if it.get("screen") == "backlog")
    assert ("X", rb.pads.ACTIONS) not in cs.hints()
    app.dispatch("ok")
    assert isinstance(app.stack[-1], rb.BacklogScreen)
    assert app.stack[-1].crumbs() == ["Collections", "Browse by", "Backlog"]
