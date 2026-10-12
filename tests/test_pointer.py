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
    rb.K = 1.0                                             # what the other tests' headless Apps draw at


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
    tile = next(r for r, fn in app.targets if r.left == 48 and r.top == 84 + 71 * 6)    # Settings
    click(rb, app, tile.center)
    assert isinstance(app.stack[-1], rb.SettingsScreen)
    s = app.stack[-1]
    seen = []
    s.tap_now = lambda i, a="ok": seen.append(i)
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
    rows = sorted((r, fn) for r, fn in app.targets if r.left == 48 and fn.__defaults__)
    rows.sort(key=lambda t: t[0].top)
    r, fn = rows[1]
    want = fn.__defaults__[0]
    click(rb, app, r.center, touch=True)
    assert cr.sel == want
    app.draw()
    rows2 = sorted(((r2, f2) for r2, f2 in app.targets if r2.left == 48 and f2.__defaults__),
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
