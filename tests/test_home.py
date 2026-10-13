"""Home: the rail of destinations, the shelves (Playing, Recommended for you, New in RomM,
Backlog, Collections) and the panel showing whatever has the focus."""
import pytest


def _rom(i, pid=1, **kw):
    return dict({"id": i, "name": "Lorem %d" % i, "fs_name": "Lorem %d.gba" % i, "platform_id": pid,
                 "fs_size_bytes": 1 << 20, "summary": "Ipsum dolor sit amet."}, **kw)


class FakeApi:
    def __init__(self):
        self.lists = {"playing": [_rom(i, rom_user={"now_playing": True}) for i in range(3)],
                      "backlog": [_rom(100 + i, rom_user={"backlogged": True}) for i in range(12)]}
        self.filtered = False
        self.recent = [_rom(200 + i) for i in range(10)]
        self.recs = [{"rom": _rom(300 + i), "seed_rom_name": "Alter Ego"} for i in range(4)]
        self.colls = [{"id": 7, "name": "Theme: Hidden Gems", "rom_count": 5},
                      {"id": 8, "name": "Top 25: Homebrew", "rom_count": 25},
                      {"id": 9, "name": "Empty", "rom_count": 0}]

    def user_list(self, name, offset=0, limit=60, platform_ids=None):
        items = self.lists[name]
        return {"items": items, "total": len(items), "filtered": self.filtered}

    def recommendations(self, limit=50):
        return self.recs

    def get(self, path, params=None):
        assert path == "/api/roms"
        return {"items": self.recent}

    def collections(self):
        return self.colls

    def roms(self, platform_ids=None, collection=None, limit=60, **kw):
        return {"items": [_rom(400 + collection[1] * 10 + i) for i in range(4)]}


@pytest.fixture
def home(isolated, rb, monkeypatch):
    """Home on an App whose RomM answers at once (FakeApi), covers never loading."""
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    app = rb.App(None, headless=True)
    app.api = FakeApi()

    def bg(fn, done=None, err=None):
        try:
            r = fn()
        except Exception as e:                      # noqa: BLE001
            if err:
                err(e)
            return
        if done:
            done(r)
    app.bg = bg
    app.platforms = [{"id": 1, "name": "Game Boy Advance", "display_name": "Game Boy Advance",
                      "rom_count": 40, "_system": "gba"}]
    app.platform_by_id = {1: app.platforms[0]}
    app.loaded = True
    app.cover = lambda rom: None
    app.on_box = set()
    monkeypatch.setattr(app, "rom_status", lambda rom: ("installed", None) if rom["id"] in app.on_box
                        else ("remote", None))
    h = rb.HomeScreen(app)
    app.push(h)
    app.draw()
    return h


def keys(home):
    return [s[0] for s in home.shelves()]


def test_every_shelf_in_order_and_the_first_one_focused(home):
    assert keys(home) == ["playing", "recommended", "new", "backlog", "collections"]
    key, shelf, rom = home.focused()
    assert key == "playing" and rom["id"] == 0
    notes = {s[0]: s[2] for s in home.shelves()}
    assert notes["playing"] == "3 marked in RomM"
    assert notes["recommended"] == "Because you played Alter Ego"
    assert notes["backlog"] == "12 games · 12.0 MB to download"
    assert notes["collections"] == "2 in RomM"                     # an empty collection isn't one


def test_shelf_navigation(home, rb):
    app = home.app
    app.dispatch("right")
    app.dispatch("right")
    assert home.focused()[2]["id"] == 2
    app.dispatch("right")                                         # the end of the shelf: stays
    assert home.focused()[2]["id"] == 2
    app.dispatch("down")
    assert home.focused()[0] == "recommended"
    app.dispatch("up")
    assert home.focused()[2]["id"] == 2                           # each shelf keeps its place
    app.dispatch("up")
    assert home.focused()[0] == "rail"
    app.dispatch("up")
    assert home.focused()[0] == "rail"
    for _ in range(9):
        app.dispatch("down")
        app.draw()
    assert home.focused()[0] == "collections"
    assert home.top > 0                                           # scrolled: the focused shelf is whole
    app.dispatch("r1")                                            # L/R: a shelf's page
    assert home.focused()[2]["id"] == 8
    home.focus_shelf("backlog", 0)
    app.dispatch("r1")
    assert home.cols["backlog"] == home.per_shelf()


def test_moving_on_home_sounds_and_the_edge_stays_quiet(home):
    app, played = home.app, []
    app.sfx.play = played.append
    app.dispatch("right")                                         # along a shelf
    app.dispatch("down")                                          # to the next shelf
    app.dispatch("up")
    app.dispatch("up")                                            # to the rail
    app.dispatch("right")                                         # along the rail
    assert played == ["move"] * 5
    played.clear()
    app.dispatch("up")                                            # above the rail: nothing moves
    assert played == []


def test_moving_on_the_keyboard_sounds(home, rb):
    app, played = home.app, []
    app.sfx.play = played.append
    app.push(rb.TextInputScreen(app, "Search", "", lambda v: None))
    app.dispatch("right")
    app.dispatch("down")
    assert played == ["move", "move"]


def test_empty_and_unsupported_shelves_are_hidden(isolated, rb, home):
    app = home.app
    app.api.lists["playing"] = []
    app.api.filtered = True                                       # an older RomM: no lists at all
    home2 = rb.HomeScreen(app)
    app.stack[0] = home2
    app.recs = None
    app.api.recs = []
    app.draw()
    assert keys(home2) == ["new", "collections"]
    assert home2.focused()[0] == "new"


def test_focus_stays_put_when_a_shelf_turns_up(home):
    home.focus_shelf("rail", 5)
    lists = home.lists
    home.lists = {}
    assert home.focused()[2][0] == "settings"
    home.lists = lists
    assert home.focused()[2][0] == "settings"
    home.focus_shelf("new", 3)
    playing = home.lists.pop("playing")
    assert home.focused()[0] == "new" and home.focused()[2]["id"] == 203
    home.lists["playing"] = playing
    assert home.focused()[2]["id"] == 203


def test_the_rail_panel_shows_whats_behind_the_pill(home, rb, monkeypatch):
    app = home.app
    home.focus_shelf("rail", 2)
    assert home.hints()[0] == ("A", "Open")
    drawn = []
    real = rb.layout.text

    def spy(surf, font, s, *a, **k):
        drawn.append(s)
        return real(surf, font, s, *a, **k)
    rb.patch(monkeypatch, "text", spy)
    app.draw()
    assert "Theme: Hidden Gems" in drawn and "Top 25: Homebrew" in drawn
    assert home.rail_rows("platforms") == [("Game Boy Advance", "40")]
    assert home.rail_rows("downloads") == [("Nothing downloading right now", "")]
    app.dispatch("ok")
    assert isinstance(app.stack[-1], rb.CollectionScreen)


def test_the_rail_pills_end_at_the_column_edge_and_open_on_tap(home, rb):
    app = home.app
    assert home.rail_rects[0].left == rb.HOME_COLUMN.left and home.rail_rects[-1].right == rb.HOME_COLUMN.right
    app.tap(home.rail_rects[5].center)
    assert isinstance(app.stack[-1], rb.SettingsScreen)


def test_the_verb_and_the_actions_menu(home, rb, monkeypatch):
    app = home.app
    assert home.hints() == [("A", "Download"), ("X", rb.pads.ACTIONS), ("Y", "Game night"), ("B", "Exit")]
    app.dispatch("ok")
    assert isinstance(app.modal, rb.DownloadConfirm)
    app.modal = None
    app.on_box.add(0)
    app.draw()
    assert home.hints()[0] == ("A", "Play now")
    assert rb.game_verb(app, home.focused()[2])["label"] == "Play now"
    played = []
    monkeypatch.setattr(app, "play_entry", lambda rom, *a: ("Play now", "", lambda: played.append(rom["id"])))
    app.dispatch("ok")                                            # A plays it
    assert played == [0] and app.modal is None
    app.dispatch("x")
    labels = [e[0] for e in app.modal.entries if e]
    assert labels[0] == "Play now" and "More like this" in labels and "See the whole shelf" in labels
    m = app.modal
    m.sel = next(i for i, e in enumerate(m.entries) if e and e[0] == "Open the game page")
    app.dispatch("ok")
    assert isinstance(app.stack[-1], rb.GameScreen) and app.stack[-1].rom is home.focused()[2]
    app.pop()
    app.modal = None
    opened = []
    home.open_game = opened.append                                # the one place A on a game goes
    app.dispatch("ok")
    assert opened == [home.focused()[2]]


def test_the_backlog_shelf_opens_the_backlog(home, rb):
    app = home.app
    home.focus_shelf("backlog", 0)
    app.dispatch("x")
    m = app.modal
    m.sel = next(i for i, e in enumerate(m.entries) if e and e[0] == "See the whole shelf")
    app.dispatch("ok")
    assert isinstance(app.stack[-1], rb.BacklogScreen)


def test_new_in_romm_opens_as_recently_added_once(home, rb):
    app = home.app
    assert "Recently added" not in [label for _, label, _ in home.RAIL]
    home.focus_shelf("new", 0)
    app.dispatch("x")
    m = app.modal
    m.sel = next(i for i, e in enumerate(m.entries) if e and e[0] == "See the whole shelf")
    app.dispatch("ok")
    gl = app.stack[-1]
    assert isinstance(gl, rb.GameListScreen) and gl.title == "Recently added" and gl.order_by == "id_desc"


def test_a_collection_tile_opens_its_list(home, rb):
    app = home.app
    home.focus_shelf("collections", 0)
    app.draw()
    assert home.coll_roms                                         # its card fetched four covers
    assert home.hints()[0] == ("A", "Open")
    app.dispatch("ok")
    gl = app.stack[-1]
    assert isinstance(gl, rb.GameListScreen) and gl.collection["id"] == 7


def test_tap_a_tile_focuses_then_acts(home, rb):
    app = home.app
    home.focus_shelf("new", 0)
    app.draw()
    rect = next(r for i, r in home.shelf_of["new"].rects if i == 2)
    app.tap(rect.center)
    assert home.focused()[0] == "new" and home.cols["new"] == 2 and app.modal is None
    app.draw()
    app.tap(rect.center)
    assert isinstance(app.modal, rb.DownloadConfirm) and home.focused()[2]["id"] == 202


def test_status_line_says_how_recently_it_was_played_here(home, rb, monkeypatch):
    import time
    app = home.app
    app.on_box.add(0)
    monkeypatch.setattr(rb.rommlib, "played_here", lambda lib, rom, system, stats=None: {"last_played": time.time()})
    app.draw()
    assert home.status_line("playing", home.focused()[2]) == "Playing · played here today"
    rec = app.recs[0][0]
    assert home.status_line("recommended", rec) == app.recs[0][1]


def test_y_is_game_night_and_b_exits(home, rb, monkeypatch):
    app = home.app
    app.dispatch("y")
    assert isinstance(app.stack[-1], rb.GameNightScreen)
    app.pop()
    quit = []
    monkeypatch.setattr(app, "request_quit", lambda: quit.append(1))
    app.dispatch("back")
    assert quit == [1]


@pytest.mark.parametrize("k,budget,per", [(1.0, 64 << 20, 7), (3.0, 64 << 20, 7), (3.0, 32 << 20, 5)])
def test_a_small_box_at_4k_shows_fewer_bigger_covers(rb, monkeypatch, k, budget, per):
    monkeypatch.setattr(rb.theme, "K", k)
    monkeypatch.setattr(rb.draw, "COVER_BUDGET", budget)
    h = rb.HomeScreen.__new__(rb.HomeScreen)
    assert h.per_shelf() == per
    tw, th = h.tile()
    assert per * tw + (per - 1) * h.GAP <= rb.HOME_COLUMN.width < per * tw + (per - 1) * h.GAP + per


def test_a_reconnect_drops_the_previous_servers_shelves(home, rb, monkeypatch):
    app = home.app
    assert home.lists["playing"]["items"][0]["id"] == 0 and home.colls
    monkeypatch.setattr(app, "load_platforms", lambda: None)
    monkeypatch.setattr(rb.rommlib.Api, "from_config", classmethod(lambda cls, cfg: FakeApi()))
    late = []
    app.bg = lambda fn, done=None, err=None: late.append((fn, done))
    home.load()                                       # nothing missing: nothing asked
    app.apply_config(reconnect=True)
    other = FakeApi()
    other.lists["playing"] = [_rom(900, rom_user={"now_playing": True})]
    other.colls = [{"id": 70, "name": "Another user's", "rom_count": 3}]
    app.api = other
    app.platforms = [{"id": 1, "name": "Game Boy Advance", "rom_count": 40, "_system": "gba"}]
    app.platform_by_id = {1: app.platforms[0]}
    home.load()
    assert home.lists == {} and home.colls is None and home.recent is None    # the old ones are gone
    for fn, done in late:
        done(fn())
    assert home.lists["playing"]["items"][0]["id"] == 900 and [c["id"] for c in home.colls] == [70]


def test_an_answer_from_before_a_reconnect_is_dropped(home):
    app = home.app
    home.lists.pop("playing")
    late = []
    app.bg = lambda fn, done=None, err=None: late.append((fn, done))
    home.load()
    app.conn_gen += 1
    for fn, done in late:
        done(fn())
    home.load()
    assert "playing" not in home.lists


def test_a_failed_shelf_is_asked_again_on_the_next_enter(home):
    app = home.app
    home.colls = None
    real, calls = app.api.collections, []

    def broken():
        calls.append(1)
        raise OSError("timed out")
    app.api.collections = broken
    home.load()
    assert home.colls == [] and calls == [1]
    home.load()
    assert calls == [1]                               # not hammered every frame
    app.api.collections = real
    home.enter()
    home.load()
    assert [c["id"] for c in home.colls] == [7, 8]
