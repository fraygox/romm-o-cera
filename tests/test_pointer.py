"""Touch and mouse: a click or tap acts on exactly what's under it, at any
scale (K) and on scrolled lists; choice rows and the hint bar step both ways;
Search's Top matches take controller and keyboard focus; a raw joystick's
triggers are L2/R2."""
import pytest


@pytest.fixture
def make_app(isolated, rb, monkeypatch):
    """An App drawing to a real (dummy-driver) screen of the given size, so
    pointer positions are physical pixels that to_logical() scales by K."""
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()

    def make(size=None):
        screen = pygame.display.set_mode(size) if size else None
        app = rb.App(screen, headless=True)
        app.bg = lambda fn, done=None, err=None: None       # no network
        return app
    yield make
    rb.theme.K = 1.0                                       # what the other tests' headless Apps draw at


def ev(kind, pos=None, **kw):
    import pygame
    t = {"down": pygame.MOUSEBUTTONDOWN, "up": pygame.MOUSEBUTTONUP, "move": pygame.MOUSEMOTION}[kind]
    if kind == "move":
        kw.setdefault("buttons", (1, 0, 0))
    else:
        kw.setdefault("button", 1)
    return pygame.event.Event(t, pos=pos, **kw)


def phys(rb, app, pt):
    """A logical point -> where it is on the physical screen (K, letterbox)."""
    sw, sh = app.screen.get_size()
    cw, ch = app.canvas.get_size()
    return (int(pt[0] * rb.K) + (sw - cw) // 2, int(pt[1] * rb.K) + (sh - ch) // 2)


def click(rb, app, pt, between=(), touch=False):
    """Press and let go at logical pt (sent as physical pixels); `between`:
    pointer motions (logical points) while held. Returns the actions."""
    acts = []
    app.pointer(ev("down", phys(rb, app, pt), touch=touch), acts)
    for q in between:
        app.pointer(ev("move", phys(rb, app, q), touch=touch), acts)
    app.pointer(ev("up", phys(rb, app, pt), touch=touch), acts)
    return acts


def settings_at_bottom(rb, app):
    app.push(rb.HomeScreen(app))
    s = rb.SettingsScreen(app)
    app.push(s)
    s.sel = s.selectable()[-1]
    app.draw()
    return s


def row_rects(rb, app, s):
    """{row key: its rect} for the Settings rows on screen."""
    items = s.items()
    out = {}
    for r, fn in app.targets:
        if r.left == 48 and r.width == 700 and fn.__defaults__:
            out[items[fn.__defaults__[0]][0]] = r
    return out


@pytest.mark.parametrize("size", [(3840, 2160), (3840, 2400), (1280, 720)])
def test_a_click_on_a_scrolled_settings_list_acts_on_the_row_under_it(make_app, rb, size):
    """Seen on a 4K VM: with Settings scrolled to its end, a click on Update
    channel selected a row three above it instead (and a second click
    scrolled further up). A stray pointer motion while the button is held
    (here: to the bottom edge) turned the click into a drag."""
    app = make_app(size)
    s = settings_at_bottom(rb, app)
    assert s.scroll > 0
    rects = row_rects(rb, app, s)
    ch = rects["channel"]
    before, scroll = app.cfg["update_channel"], s.scroll
    acts = click(rb, app, ch.center, between=[(ch.centerx, rb.H - 1)])
    assert acts == []                                     # no scrolling
    items = s.items()
    assert items[s.sel][0] == "channel" and app.cfg["update_channel"] != before
    app.draw()
    assert s.scroll == scroll and row_rects(rb, app, s)["channel"] == ch
    click(rb, app, ch.center, between=[(ch.centerx, rb.H - 1)])     # the same spot again: the same row
    assert items[s.sel][0] == "channel" and app.cfg["update_channel"] == before
    # every row on screen, by its physical position
    seen = []
    s.tap_now = lambda i, a="ok": seen.append((i, a))
    app.draw()
    for key, r in row_rects(rb, app, s).items():
        seen.clear()
        app.draw()                                        # what a click shows comes first
        click(rb, app, (r.left + 30, r.centery))
        assert [s.items()[i][0] for i, a in seen] == [key]


def test_a_tapped_settings_row_stays_under_the_finger(make_app, rb):
    """Picking a row by touch never scrolls the list (a row half past the
    edge, or under a header scrolled away, used to jump a row)."""
    app = make_app((3840, 2160))
    app.push(rb.HomeScreen(app))
    s = rb.SettingsScreen(app)
    app.push(s)
    s.action = lambda a: None
    for _ in range(40):
        app.draw()
        scroll = s.scroll
        for key, r in row_rects(rb, app, s).items():
            click(rb, app, (r.left + 30, r.centery))
            app.draw()
            assert s.scroll == scroll, key
        s.tapped = None
        s.sel = s.selectable()[min(len(s.selectable()) - 1, s.selectable().index(s.sel) + 1)]


def test_settings_choice_rows_step_back_from_the_left_of_their_value(make_app, rb):
    app = make_app()
    app.push(rb.HomeScreen(app))
    s = rb.SettingsScreen(app)
    app.push(s)
    app.sfx.set_level = lambda lv: None
    i = next(k for k, it in enumerate(s.items()) if it[0] == "sounds")
    s.sel = i
    app.draw()
    r = row_rects(rb, app, s)["sounds"]
    assert app.cfg["sounds"] == "normal"
    app.tap((r.right - 30, r.centery))                    # the value's right: on
    assert app.cfg["sounds"] == "quiet"
    app.draw()
    back = [t for t, fn in app.targets if fn and fn.__defaults__ == (i,) and t.width < 700]
    assert back
    app.tap(back[0].center)                               # its left half (and ‹): back
    assert app.cfg["sounds"] == "normal"
    app.draw()
    app.tap((r.left + 40, r.centery))                     # the label: on, as A
    assert app.cfg["sounds"] == "quiet"


def test_action_menu_choice_rows_step_both_ways_by_tap(make_app, rb):
    app = make_app()
    app.push(rb.HomeScreen(app))
    got = []
    app.modal = rb.ActionMenu(app, "Menu", [("Pick", "", lambda d: got.append(d), "Two", "choice"),
                                            ("Act", "", lambda: got.append("act"))],
                              refresh=lambda: app.modal.entries)
    app.draw()
    row = [r for r, fn in app.targets[app.modal_from:] if fn and fn.__defaults__ == (0,)]
    full = max(row, key=lambda r: r.width)
    back = min(row, key=lambda r: r.width)
    app.tap((full.right - 10, full.centery))              # ›
    app.tap(back.center)                                  # ‹
    app.tap((full.left + 20, full.centery))               # the label
    assert got == [1, -1, 1] and app.modal is not None


def test_the_left_right_hint_goes_both_ways(make_app, rb):
    app = make_app()
    app.push(rb.HomeScreen(app))
    s = rb.SettingsScreen(app)
    app.push(s)
    s.sel = next(k for k, it in enumerate(s.items()) if it[0] == "sounds")
    app.draw()
    sent = []
    app.dispatch = sent.append
    foot = [(r, fn) for r, fn in app.targets if r.top == rb.H - 46 and fn and fn.__defaults__ in (("left",),
                                                                                               ("right",))]
    left = next(r for r, fn in foot if fn.__defaults__ == ("left",))
    right = next(r for r, fn in foot if fn.__defaults__ == ("right",))
    app.tap((left.left + 4, left.centery))
    app.tap((right.right - 4, right.centery))
    assert sent == ["left", "right"]


def test_a_press_let_go_on_another_row_does_nothing(make_app, rb):
    app = make_app((3840, 2160))
    s = settings_at_bottom(rb, app)
    seen = []
    s.tap_now = lambda i, a="ok": seen.append(i)
    app.draw()
    rs = sorted(row_rects(rb, app, s).values(), key=lambda r: r.top)
    acts = []
    app.pointer(ev("down", phys(rb, app, rs[0].center)), acts)
    app.pointer(ev("up", phys(rb, app, rs[2].center)), acts)
    assert seen == [] and acts == []


def test_a_second_click_before_the_next_frame_waits_for_it(make_app, rb):
    """Two clicks in one batch of events: the first opened Settings, so the
    second must hit Settings' rows (drawn next), not Home's old ones."""
    app = make_app((3840, 2160))
    app.push(rb.HomeScreen(app))
    app.draw()
    pill = app.stack[-1].rail_rects[-1]                     # Settings, on Home's rail
    click(rb, app, pill.center)
    assert isinstance(app.stack[-1], rb.SettingsScreen)
    s = app.stack[-1]
    seen = []
    s.tap_now = lambda i, a="ok": seen.append(i)
    tile = rb.pygame.Rect(48, 290, 700, 20)                 # where Settings draws a row (Home: a shelf)
    click(rb, app, tile.center)
    assert seen == [] and len(app.ptr_later) == 2           # held back
    app.draw()
    acts = []
    for e in app.pointer_later():
        app.pointer(e, acts)
    hit = app.target_at(tile.center)
    assert seen == [hit[1].__defaults__[0]] and isinstance(app.stack[-1], rb.SettingsScreen)


def test_a_click_on_a_scrolled_list_screen_selects_then_opens_that_row(make_app, rb):
    app = make_app((3840, 2160))
    app.push(rb.HomeScreen(app))
    cr = rb.CreditsScreen(app)
    app.push(cr)
    cr.action("r1")
    cr.action("r1")
    app.draw()
    assert cr.scroll > 0
    rows = sorted((r, fn) for r, fn in app.targets if r.left == rb.CONTENT.left and fn.__defaults__)
    rows.sort(key=lambda t: t[0].top)
    r, fn = rows[1]
    want = fn.__defaults__[0]
    click(rb, app, r.center, touch=True)
    assert cr.sel == want
    app.draw()
    rows2 = sorted(((r2, f2) for r2, f2 in app.targets if r2.left == rb.CONTENT.left and f2.__defaults__),
                   key=lambda t: t[0].top)
    assert rows2[1][0] == r and rows2[1][1].__defaults__ == (want,)      # nothing moved


def test_finger_events_alone_do_nothing(make_app, rb):
    """SDL turns a touch into mouse events with touch=True (handled above);
    the FINGER events beside them must not act a second time."""
    import pygame
    app = make_app()
    app.push(rb.HomeScreen(app))
    app.draw()
    out = []
    for t in (pygame.FINGERDOWN, pygame.FINGERUP):
        e = pygame.event.Event(t, x=0.5, y=0.5, dx=0, dy=0, touch_id=1, finger_id=0)
        assert not app.pointer(e, out)
        app.input.handle(e, out)
    assert out == [] and len(app.stack) == 1


# ---- Search: the Top matches

def _search(rb, app, monkeypatch, installed=()):
    app.push(rb.HomeScreen(app))
    sc = rb.SearchScreen(app)
    app.push(sc)
    sc.query = "lorem"
    sc.preview = [{"id": i, "name": "Lorem %d" % i, "platform_id": 1, "fs_size_bytes": 1} for i in range(5)]
    sc.preview_total = 5
    monkeypatch.setattr(app, "rom_status", lambda rom: ("installed" if rom["id"] in installed else "remote", None))
    opened = []                                           # the game pages opened, by rom id

    def push(scr, _push=app.push):
        if isinstance(scr, rb.GameScreen):
            opened.append(scr.rom["id"])
        else:
            _push(scr)
    monkeypatch.setattr(app, "push", push)
    return sc, opened


def test_search_matches_take_controller_focus(make_app, rb, monkeypatch):
    app = make_app()
    sc, opened = _search(rb, app, monkeypatch, installed={2})
    sc.kb.r, sc.kb.c = 1, sc.kb.row_len(1) - 1            # the end of the Q row
    sc.action("right")
    assert sc.pick is not None and not sc.text_entry
    first = sc.pick
    sc.action("down")
    assert sc.pick == first + 1
    for _ in range(10):
        sc.action("down")
    assert sc.pick == 4                                   # stays on the last
    sc.action("up")
    sc.action("up")
    sc.action("ok")                                       # A: the game's page
    assert opened == [2]
    sc.action("down")
    sc.action("ok")
    assert opened[-1] == 3
    sc.action("left")                                     # back to the keyboard, beside it
    assert sc.pick is None and sc.text_entry and sc.kb.at_edge("right")
    sc.kb.c = 0
    sc.action("left")                                     # left from the first key: the matches too
    assert sc.pick is not None
    sc.action("back")                                     # B: the keyboard again, not out of Search
    assert sc.pick is None and app.stack[-1] is sc
    app.draw()


def test_search_without_matches_keeps_the_keyboard_wrapping(make_app, rb, monkeypatch):
    app = make_app()
    sc, opened = _search(rb, app, monkeypatch)
    sc.preview = []
    sc.kb.r, sc.kb.c = 1, sc.kb.row_len(1) - 1
    sc.action("right")
    assert sc.pick is None and sc.kb.c == 0
    sc.action("left")
    assert sc.kb.at_edge("right")


def test_search_enter_on_a_match_opens_it_from_the_keyboard(make_app, rb, monkeypatch):
    """With focus on the matches the screen leaves text entry, so Enter is A."""
    import pygame
    app = make_app()
    sc, opened = _search(rb, app, monkeypatch)
    sc.pick = 1
    app.input.text_mode = sc.text_entry                   # what the main loop sets from it
    assert app.input.text_mode is False
    out = []
    app.input.handle(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0, unicode="\r"), out)
    app.input.pending = [(rb.time.time() - 1, a) for _, a in app.input.pending]
    app.input.flush(out)
    assert out == ["ok"]
    for a in out:
        app.dispatch(a)
    assert opened == [1]


def test_tapping_a_match_opens_its_game_page(make_app, rb, monkeypatch):
    app = make_app()
    sc, opened = _search(rb, app, monkeypatch, installed={0})
    app.draw()
    hits = [(r, fn) for r, fn in app.targets if r.left == sc.PANEL.left + 12]
    assert len(hits) == 5
    app.tap(hits[0][0].center)
    assert opened == [0]
    app.tap(hits[1][0].center)
    assert opened[-1] == 1


# ---- raw joysticks (no SDL mapping)

class FakeJoy:
    def __init__(self, buttons, axes):
        self.buttons, self.axes = buttons, axes

    def init(self):
        pass

    def get_instance_id(self):
        return 7

    def get_numbuttons(self):
        return self.buttons

    def get_numaxes(self):
        return len(self.axes)

    def get_axis(self, a):
        return self.axes[a]

    def get_name(self):
        return "Generic USB Joystick"


def _raw(rb, monkeypatch, joy):
    import pygame
    pygame.init()
    monkeypatch.setattr(rb.input, "sdlcontroller", None)
    monkeypatch.setattr(pygame.joystick, "get_count", lambda: 0)
    inp = rb.Input(False)
    monkeypatch.setattr(pygame.joystick, "Joystick", lambda i: joy)
    inp._open(0)
    assert inp.joysticks == {7: joy}
    return inp


def test_a_raw_joystick_has_l2_r2(rb, monkeypatch):
    import pygame
    inp = _raw(rb, monkeypatch, FakeJoy(12, [0.0, 0.0, -1.0, 0.0, 0.0, -1.0]))
    out = []
    for b in (6, 7, 8, 9, 4, 5):
        inp.handle(pygame.event.Event(pygame.JOYBUTTONDOWN, button=b, instance_id=7, joy=0), out)
        inp.handle(pygame.event.Event(pygame.JOYBUTTONUP, button=b, instance_id=7, joy=0), out)
    assert out == ["l2", "r2", "select", "start", "l1", "r1"]
    out = []
    for axis, v in ((2, 1.0), (2, -1.0), (5, 0.8), (5, -1.0), (3, 1.0), (3, 0.0), (4, -1.0), (2, 0.9)):
        inp.handle(pygame.event.Event(pygame.JOYAXISMOTION, axis=axis, value=v, instance_id=7, joy=0), out)
    assert out == ["l2", "r2", "l2"]                        # the right stick (3, 4) does nothing


def test_a_raw_joystick_with_few_buttons_keeps_select_start(rb, monkeypatch):
    import pygame
    inp = _raw(rb, monkeypatch, FakeJoy(8, [0.0, 0.0]))
    out = []
    for b in (6, 7):
        inp.handle(pygame.event.Event(pygame.JOYBUTTONDOWN, button=b, instance_id=7, joy=0), out)
    assert out == ["select", "start"]
