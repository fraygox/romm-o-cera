"""Collections: the shelves a collection's name puts it on, the wall, the box state of each
collection, the list a collection opens into (with its box pills), and A opening a game's page
from the other lists."""
import pytest


@pytest.fixture
def app(isolated, rb, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    app = rb.App(None, headless=True)
    app.bg = lambda fn, done=None, err=None: None
    app.platforms = [{"id": 1, "name": "PlayStation", "display_name": "PlayStation", "rom_count": 40, "_system": "psx"}]
    app.platform_by_id = {1: app.platforms[0]}
    app.cover = lambda rom: None
    return app


def _col(i, name, ids=(1, 2, 3)):
    return {"id": i, "name": name, "rom_count": len(ids), "rom_ids": list(ids),
            "path_covers_small": ["/assets/c%d-%d.png" % (i, k) for k in range(2)]}


def _rom(i, size=100):
    return {"id": i, "name": "Lorem %d" % i, "fs_name": "Lorem %d.chd" % i, "fs_name_no_ext": "Lorem %d" % i,
            "platform_id": 1, "fs_size_bytes": size}


REGULAR = [_col(1, "Theme: Hidden Gems"), _col(2, "Top 100: PlayStation"), _col(3, "Top 25: Lorem"),
           _col(4, "Series: Dolor"), _col(5, "theme : Cosy Nights"), _col(6, "Weekend Favourites"),
           _col(7, "Picks: Rainy Day"), _col(8, "picks: Sunday"), _col(9, "Picks: Late"),
           _col(10, "Lorem: Best Of"), _col(11, "Empty", ()), _col(12, "Top10: Amet")]
SMART = [{"id": 1, "name": "Tiny games", "rom_count": 2, "rom_ids": [1, 2]}]


def test_names_put_collections_on_their_shelves_in_the_fixed_order(rb):
    kinds = rb.group_collections(REGULAR, SMART)
    assert [k["label"] for k in kinds] == ["Top lists", "Themes", "Series", "Picks", "Your collections", "Smart",
                                           "Browse by"]
    names = {k["label"]: [it["name"] for it in k["items"]] for k in kinds}
    assert names["Top lists"] == ["Top10: Amet", "Top 25: Lorem", "Top 100: PlayStation"]     # by N; the N stays
    assert names["Themes"] == ["Cosy Nights", "Hidden Gems"]            # the kind dropped, any case and spacing
    assert names["Series"] == ["Dolor"]
    assert names["Picks"] == ["Late", "Rainy Day", "Sunday"]            # three share "Picks:": a shelf of its own
    assert names["Your collections"] == ["Lorem: Best Of", "Weekend Favourites"]   # a one-off prefix is just a name
    assert names["Smart"] == ["Tiny games"]
    assert [it["name"] for it in names and kinds[-1]["items"]] == ["Genre", "Franchise", "Series", "Company",
                                                                   "Game mode", "Backlog"]
    assert all(rb.door(it) for it in kinds[-1]["items"])
    theme = kinds[1]["items"][0]
    assert (theme["full"], theme["kind"], theme["group"], theme["param"]) == ("theme : Cosy Nights", "theme", "Themes",
                                                                             "collection_id")
    assert "Empty" not in str(kinds)                                     # nothing in it: no tile


def test_no_collections_means_only_the_doors(rb):
    assert [k["label"] for k in rb.group_collections([], [])] == ["Browse by"]


def _screen(app, rb):
    scr = rb.CollectionScreen(app)
    scr.kinds = rb.group_collections(REGULAR, SMART)
    app.push(scr)
    return scr


def test_moving_on_the_shelves_and_the_wall(app, rb):
    scr = _screen(app, rb)
    k, it = scr.current()
    assert k["label"] == "Top lists" and it["name"] == "Top10: Amet"
    assert scr.crumbs() == ["Collections", "Top lists"]
    keys = [scr.nav_key()]
    for a in ("right", "right", "right", "down", "left", "up", "up", "r1", "l1"):
        scr.action(a)
        keys.append(scr.nav_key())
    assert keys == [(0, 0, False), (0, 1, False), (0, 2, False), (0, 2, False), (1, 1, False), (1, 0, False),
                    (0, 0, False), (0, 0, False), (0, 2, False), (0, 0, False)]   # a move past the edge stays put
    scr.action("y")                                                      # the kind as a wall
    assert scr.wall and scr.crumbs() == ["Collections", "Top lists", "All 3"]
    assert ("Y", "Shelves") in scr.hints()
    scr.action("down")                                                   # a wall row down: past the end stays
    assert scr.col == 0
    scr.action("y")
    assert not scr.wall and ("Y", "All top lists") in scr.hints()


def test_a_tap_focuses_then_opens_and_doors_open_other_screens(app, rb, monkeypatch):
    scr = _screen(app, rb)
    app.targets = []
    scr.draw(app.canvas)
    rects = [r for r, _ in app.targets]
    tile = next(r for r in rects if r.width > 100 and r.top > 120 and r.left < 400)   # a tile on a shelf
    pushed = []
    monkeypatch.setattr(app, "push", pushed.append)
    app.tap(tile.center)
    assert pushed == [] and (scr.row, scr.col) != (0, 0) or tile.left == 48
    scr.row, scr.col = len(scr.kinds) - 1, 0                             # the Genre door
    assert ("X", rb.pads.ACTIONS) not in scr.hints()
    scr.action("ok")
    assert isinstance(pushed[-1], rb.CollectionScreen) and pushed[-1].vtype == "genre" and pushed[-1].wall
    scr.col = 5
    scr.action("ok")
    assert isinstance(pushed[-1], rb.BacklogScreen)


def test_the_box_state_comes_from_romms_game_ids_then_the_games_themselves(app, rb, monkeypatch):
    scr = _screen(app, rb)
    k, it = scr.current()
    monkeypatch.setattr(app.library, "snapshot", lambda: {"1": {}, "9": {}})
    scr.enter()
    assert scr.state(it)["here"] == 1 and scr.box_line(it, 300) == "1 of 3 here"
    roms = [_rom(1), _rom(2, 500), _rom(3, 700)]
    monkeypatch.setattr(app.library, "is_installed", lambda rom, system: rom["id"] in (1, 2))
    scr.box[("collection_id", it["id"])]["roms"] = roms
    scr.enter()                                                          # back from a list: measured again
    st = scr.state(it)
    assert (st["here"], st["rest"]) == (2, 700)
    assert scr.box_line(it, 300) == "2 of 3 here"
    scr.draw(app.canvas)


def test_the_focused_collections_games_are_fetched_after_a_moment_once(app, rb, monkeypatch):
    scr = _screen(app, rb)
    asked = []
    app.bg = lambda fn, done=None, err=None: asked.append(fn) or done(fn())
    monkeypatch.setattr(app.api, "roms_all", lambda **kw: [_rom(1), _rom(2)])
    k, it = scr.current()
    rb.draw.NOW = 10.0
    scr.draw(app.canvas)
    assert asked == []                                                   # just focused: not yet
    rb.draw.NOW = 11.0
    scr.draw(app.canvas)
    scr.draw(app.canvas)
    assert len(asked) == 1 and scr.box[("collection_id", 12)]["roms"][0]["id"] == 1
    assert scr.state(it)["rest"] == 200


def test_opening_a_collection_lists_all_of_it_with_the_box_pills(app, rb, monkeypatch):
    scr = _screen(app, rb)
    k, it = scr.current()
    roms = [_rom(1), _rom(2, 500), _rom(3, 700)]
    monkeypatch.setattr(app.library, "is_installed", lambda rom, system: rom["id"] == 1)
    scr.box.setdefault(("collection_id", it["id"]), {})["roms"] = roms
    gl = scr.list_of(it)
    assert isinstance(gl, rb.GameListScreen) and gl.whole and gl.pills and gl.static is roms
    assert gl.counts == {"all": 3, "here": 1, "away": 2} and gl.crumbs() == ["Collections", "Top lists"]
    assert gl.ROWS == 10 and gl.lst.top == 134                            # under the pills: fewer rows
    assert ("Y", "Filter") in gl.hints()
    gl.action("y")
    assert gl.box_filter == "here" and [r["id"] for r in gl.items] == [1]
    gl.action("y")
    assert gl.box_filter == "away" and [r["id"] for r in gl.items] == [2, 3]
    labels = [e[0] for e in gl.list_actions()]
    assert "Download the rest" in labels and "Compare with RomM" in labels
    assert "Hide games on this box" not in [e[0] for e in gl.list_controls()]
    assert "Download the rest" in [e[0] for e in gl.list_actions()]
    sub = next(e[1] for e in gl.list_actions() if e[0] == "Download the rest")
    assert sub == "2 games · 1 KB"
    app.push(gl)
    app.targets = []
    gl.draw(app.canvas)
    rows = [r for r, _ in app.targets if r.left == gl.lst.left]
    assert rows and max(r.bottom for r in rows) <= rb.CONTENT.bottom    # every row above the hint bar
    gl.action("ok")                                                      # A: the game's page from the list
    assert isinstance(app.stack[-1], rb.GameScreen) and app.stack[-1].source is gl


def test_a_whole_collection_is_fetched_when_it_isnt_in_yet(app, rb, monkeypatch):
    it = rb.item(_col(1, "Theme: Hidden Gems"), "collection_id", "Theme", "Hidden Gems", "Themes")
    fetched = []

    def bg(fn, done=None, err=None):
        fetched.append(fn)
        try:
            done(fn())
        except Exception as e:                      # noqa: BLE001 — a paged list asks RomM: no RomM here
            err(e)
    app.bg = bg
    monkeypatch.setattr(app.api, "roms_all", lambda **kw: [_rom(1), _rom(2)])
    gl = rb.GameListScreen(app, it["full"], collection=it, group="Themes", whole=True)
    assert len(fetched) == 1 and gl.pills and gl.counts["all"] == 2 and gl.total == 2
    big = rb.GameListScreen(app, it["full"], collection=dict(it, count=5000), group="Themes", whole=False)
    assert not big.whole and not big.pills and ("Y", "Search") in big.hints()


def test_the_menu_has_the_rest_to_download(app, rb, monkeypatch):
    scr = _screen(app, rb)
    k, it = scr.current()
    scr.menu(it)
    labels = [e[0] for e in app.modal.entries]
    assert labels == ["Open", "Compare with RomM", "Game night", "Download the rest"]
    assert app.modal.entries[-1][1] == "3 games" and app.modal.entries[-1][2]
    monkeypatch.setattr(app.library, "snapshot", lambda: {"1": {}, "2": {}, "3": {}})
    scr.enter()
    scr.menu(it)
    assert app.modal.entries[-1][1:] == ("Every game is on this box already", None)


def test_a_on_a_game_opens_its_page_from_backlog_and_game_night(app, rb, monkeypatch):
    rom = _rom(1)
    bl = rb.BacklogScreen(app)
    bl.items, bl.loading = [rom, _rom(2)], False
    assert ("A", "Open") in bl.hints()
    bl.action("ok")
    page = app.stack[-1]
    assert isinstance(page, rb.GameScreen) and page.rom is rom and page.source is bl
    page.action("right")
    assert page.rom["id"] == 2 and bl.sel == 1
    gn = rb.GameNightScreen(app, rb.Scope.library(app))
    gn.picks = [rom]
    assert ("A", "Open") in gn.hints()
    gn.action("ok")
    assert isinstance(app.stack[-1], rb.GameScreen) and app.stack[-1].rom is rom
