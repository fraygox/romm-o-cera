"""Touch and keyboard alone: a double tap never acts on what the first tap opened, a shelf's
covers past the edge can be reached by taps, the logo never saves a text field, a held Delete
goes on deleting, and the keys the keyboard screens name work while typing."""
import time

import pytest

from test_home import FakeApi, home  # noqa: F401  (the Home fixture)
from test_pointer import make_app, click, phys  # noqa: F401


def test_home_shelf_covers_past_the_edge_reachable_by_touch(home, rb):
    """Backlog has 12 games; only `per` fit. Tapping the rightmost visible tile (twice
    or more) never brings the next one into view, and a sideways drag does nothing."""
    app = home.app
    home.focus_shelf("backlog", 0)
    app.draw()
    sh = home.shelf_of["backlog"]
    per = len(sh.rects)
    assert per < 12
    seen = set()
    for _ in range(30):                       # keep tapping the rightmost tile shown
        i, rect = sh.rects[-1]
        seen.update(j for j, _ in sh.rects)
        app.tap(rect.center)
        app.draw()
        if len(app.stack) > 1:                # it opened that game's page: back
            app.pop()
            app.draw()
    # a horizontal drag with a finger, right to left across the shelf
    import pygame
    acts = []
    y = sh.rects[0][1].centery
    app.pointer(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(900, y), button=1, touch=True), acts)
    for x in range(900, 100, -20):
        app.pointer(pygame.event.Event(pygame.MOUSEMOTION, pos=(x, y), buttons=(1, 0, 0), touch=True), acts)
    app.pointer(pygame.event.Event(pygame.MOUSEBUTTONUP, pos=(100, y), button=1, touch=True), acts)
    print("per", per, "seen", sorted(seen), "drag actions", acts)
    assert max(seen) == 11, "covers %d..11 of Backlog never shown by touch" % (max(seen) + 1)


def _app(rb, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    app = rb.App(None, headless=True)
    app.bg = lambda fn, done=None, err=None: None
    app.push(rb.HomeScreen(app))
    return app


def test_tapping_the_logo_on_a_text_field_goes_home_without_saving(isolated, rb, monkeypatch):
    """The logo is the header's Home button; on a Settings text field Start means Save."""
    app = _app(rb, monkeypatch)
    app.push(rb.SettingsScreen(app))
    saved = []
    s = rb.TextInputScreen(app, "Server address", "http://typed-but-not-meant", saved.append, context="url")
    app.push(s)
    app.draw()
    app.tap((30, 30))                              # the "R" logo
    print("saved", saved, "stack", [type(x).__name__ for x in app.stack])
    assert not saved, "a tap on the logo saved %r" % saved


def test_held_backspace_in_a_text_field_keeps_deleting(rb, monkeypatch):
    import pygame
    pygame.init()
    clock = [1000.0]
    monkeypatch.setattr(rb.time, "time", lambda: clock[0])
    inp = rb.Input(False)
    inp.text_mode = True
    out = []
    inp.handle(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_BACKSPACE, mod=0, unicode=""), out)
    for _ in range(150):                           # held 1.5 s
        clock[0] += 0.01
        inp.repeats(out)
    print("held Backspace 1.5 s ->", out)
    assert out.count("x") > 1


@pytest.mark.parametrize("key", ["K_HOME", "K_PAGEUP", "K_PAGEDOWN"])
def test_keys_the_keyboard_screens_name_do_something_in_text_mode(rb, monkeypatch, key):
    """Search says 'PgUp symbols · PgDn or Home searches', Settings fields 'Home saves',
    Welcome 'Home connects' and 'Y Advanced'."""
    import pygame
    pygame.init()
    clock = [1000.0]
    monkeypatch.setattr(rb.time, "time", lambda: clock[0])
    inp = rb.Input(False)
    inp.text_mode = True
    out = []
    k = getattr(pygame, key)
    inp.handle(pygame.event.Event(pygame.KEYDOWN, key=k, mod=0, unicode=""), out)
    inp.handle(pygame.event.Event(pygame.KEYUP, key=k, mod=0), out)
    clock[0] += 0.2
    inp.repeats(out)
    print(key, "->", out)
    assert out


@pytest.mark.parametrize("size", [(3840, 2160), (2560, 1600), (1280, 800)])
def test_every_search_key_takes_its_own_tap_at_any_scale(make_app, rb, size):
    app = make_app(size)
    app.push(rb.HomeScreen(app))
    s = rb.SearchScreen(app)
    app.push(s)
    app.draw()
    bad = []
    for ri, ci, k, kr in s.kb.rects(s.kb_rect()):
        app.draw()
        s.kb.r, s.kb.c = 0, 0
        for pt in (kr.center, (kr.left + 2, kr.top + 2), (kr.right - 3, kr.bottom - 3)):
            app.draw()
            click(rb, app, pt)
            app.tapped = False
            if (s.kb.r, s.kb.c) != (ri, ci):
                bad.append((k.label, pt, (s.kb.r, s.kb.c)))
            if len(app.stack) > 2:
                del app.stack[2:]
    print(size, "K", rb.theme.K, "misses", bad[:8])
    assert not bad


def test_a_double_tap_on_a_one_tap_row_does_not_act_on_the_next_screen(make_app, rb):
    """Settings hub rows open on one tap. A player used to lists (two taps) double-taps: the
    second tap, a few frames later, lands on the section that just opened and changes a setting."""
    app = make_app((1280, 720))
    app.push(rb.HomeScreen(app))
    s = rb.SettingsScreen(app)
    app.push(s)
    app.draw()
    hub = [(r, fn) for r, fn in app.targets if r.left == 48 and fn and fn.__defaults__]
    changed = []
    for r, fn in hub:
        app.del_ = None
        s2 = rb.SettingsScreen(app)
        app.stack[1:] = [s2]
        app.draw()
        before = dict(app.cfg)
        stack_before = len(app.stack)
        click(rb, app, r.center)                  # first tap: the section opens
        app.tapped = False
        for _ in range(8):                        # ~130 ms of frames
            app.draw()
        title = s2.crumbs()
        click(rb, app, r.center)                  # the second tap of the double tap
        app.tapped = False
        diff = {k: (before[k], app.cfg[k]) for k in app.cfg if before.get(k) != app.cfg[k]}
        if diff or len(app.stack) != stack_before or app.modal is not None:
            changed.append((title, diff, [type(x).__name__ for x in app.stack], type(app.modal).__name__))
        app.modal = None
    for c in changed:
        print("double tap ->", c)
    assert not changed


def test_a_double_tap_on_delete_in_a_games_actions_does_not_confirm_it(make_app, rb):
    """A game page opened from a list (so its Actions has Game night), installed: Actions is
    More like this, Game night, a line, Delete from this box, Download again (game.py open_menu).
    Delete opens its Confirm; the Confirm's red Delete button sits under the lower part of the row."""
    app = make_app((1280, 720))
    app.push(rb.HomeScreen(app))
    deleted = []

    def delete():
        app.modal = rb.Confirm(app, "Delete Lorem?", "Removes Lorem.gba from roms/gba (1 MB).", "Delete",
                               lambda: deleted.append(1), danger=True)
    entries = [("More like this", "Games RomM says are like it", lambda: None),
               ("Game night", "Roll a game from the list this one is in", lambda: None), None,
               ("Delete from this box", "Frees 1.0 MB (1 file)", delete),
               ("Download again", "Replaces what's on the box", lambda: None)]
    hits = []
    for y in range(380, 460, 4):
        for x in range(330, 960, 30):
            deleted.clear()
            app.modal = rb.ActionMenu(app, "Lorem", list(entries))
            app.draw()
            click(rb, app, (x, y))
            app.tapped = False
            if not isinstance(app.modal, rb.Confirm):
                continue
            for _ in range(8):
                app.draw()
            click(rb, app, (x, y))
            app.tapped = False
            if deleted:
                hits.append((x, y))
    print("double tap spots on 'Delete from this box' that delete at once:", len(hits), hits[:6], hits[-3:])
    assert not hits


def test_keyboard_alone_reaches_welcomes_advanced(isolated, rb, monkeypatch):
    """While a text field has the keys, y types a y: Y is F2 there, and the hints say so."""
    import pygame
    app = _app(rb, monkeypatch)
    w = rb.WelcomeScreen(app)
    app.push(w)
    rb.pads.INPUT_MODE["kb"] = rb.pads.INPUT_MODE["text"] = True
    assert rb.pads.pad_face("Y")[1] == "F2"
    inp = app.input
    inp.text_mode = True
    out = []
    inp.handle(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F2, mod=0, unicode=""), out)
    inp.handle(pygame.event.Event(pygame.KEYUP, key=pygame.K_F2, mod=0), out)
    time.sleep(0.1)
    inp.repeats(out)
    for a in out:
        app.dispatch(a)
    assert w.view == "advanced" and "y" not in w.url_kb.value.replace("https", "")
    rb.pads.INPUT_MODE["text"] = False
    assert rb.pads.pad_face("Y")[1] == "Y"          # in lists the y key is Y
