"""No text is lost to its box: every Settings help, row line, dialog body and
menu line either fits where it's drawn or goes through the scroller
(scroll_text / marquee), which shows the rest by moving. Fits are checked at
scale 1 with a margin, since other fonts (CI's) wrap a few pixels differently."""
import pytest

MARGIN = 24             # px of width a text must leave spare to count as fitting


@pytest.fixture
def ui(isolated, rb, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    monkeypatch.setattr(rb.theme, "K", 1.0)
    pygame.init()
    app = rb.App(None, headless=True)
    yield app
    rb.apply_theme("default")


def _spy(rb, monkeypatch, name):
    calls = []
    real = getattr(rb, name)

    def spy(c, font, s, *a, **k):
        calls.append((s, a, k))
        return real(c, font, s, *a, **k)
    rb.patch(monkeypatch, name, spy)
    return calls


def _rows(st):
    return [(i, it) for i, it in enumerate(st.items()) if it and it[0] != "§"]


def test_every_settings_help_is_drawn_through_the_scroller_in_the_panel(ui, rb, monkeypatch):
    app = ui
    st = rb.SettingsScreen(app)
    app.push(st)
    calls = _spy(rb, monkeypatch, "scroll_text")
    for i, (key, label, value, sub) in _rows(st):
        st.sel = i
        del calls[:]
        app.draw()
        help_ = rb.SETTING_HELP.get(key, sub)
        mine = [c for c in calls if c[0] == help_]
        assert mine, key                                           # never a hard line cap
        x, y, w, h = mine[0][1][0]
        assert 776 < x and x + w <= 776 + 472 and y > 84 and y + h <= 84 + 554 - 150, key


def test_every_settings_help_is_short_enough_to_read_without_scrolling(ui, rb):
    """A sentence or two: the help fits its panel at scale 1, a line and a margin to spare
    (the scroller is the safety net for other fonts and translations, not the plan)."""
    app = ui
    st = rb.SettingsScreen(app)
    app.push(st)
    f = app.fonts
    width = 472 - 56 - MARGIN
    for _, (key, label, value, sub) in _rows(st):
        help_ = rb.SETTING_HELP.get(key, sub)
        room = 4 * 26 if key == "docs" else (554 - 150 - 40 - 92)
        lines = rb.wrapped(f.body, help_, width)
        assert (len(lines) + 1) * 26 <= room, (key, len(lines), help_)
        assert len(lines) <= 7, (key, len(lines))                   # a sentence or two, not a page


def test_every_settings_row_line_fits_or_slides_when_focused(ui, rb, monkeypatch):
    app = ui
    st = rb.SettingsScreen(app)
    app.push(st)
    calls = _spy(rb, monkeypatch, "marquee")
    rows = _rows(st)
    for i, (key, label, value, sub) in rows:
        assert rb.rich_width(app.fonts, app.fonts.small, sub) <= 420 - MARGIN, (key, sub)
    i, (key, *_rest, sub) = rows[3]
    st.sel = i
    app.draw()
    assert any(c[0] == sub and c[2].get("focused") and c[2].get("maxw") == 420 for c in calls)


@pytest.mark.parametrize("key", ["rvz", "zar", "wux", "wua"])
def test_a_tool_download_prompt_fits_its_dialog(ui, rb, key):
    app = ui
    say = rb.SettingsScreen.TOOL_TEXT[key]
    body = say["body"].format(version="v1.234-5678", size=" (12.3 MB)")
    assert len(rb.wrapped(app.fonts.body, body, 640 - 64 - MARGIN)) <= 4, (key, body)
    assert rb.measure(app.fonts.h2, say["title"]) <= 640 - 64 - MARGIN


def test_a_dialog_body_scrolls_instead_of_being_cut(ui, rb, monkeypatch):
    app = ui
    app.push(rb.SettingsScreen(app))
    calls = _spy(rb, monkeypatch, "scroll_text")
    body = "A very long explanation that goes on. " * 12
    app.modal = rb.Confirm(app, "Long", body, "OK", lambda: None)
    app.draw()
    assert [c for c in calls if c[0] == body]


def test_a_menu_row_slides_its_long_lines_only_when_focused(ui, rb, monkeypatch):
    app = ui
    app.push(rb.SettingsScreen(app))
    calls = _spy(rb, monkeypatch, "marquee")
    long_sub = "A grey line much longer than the menu row it sits in, so it can't fit at all"
    app.modal = rb.ActionMenu(app, "Menu", [("First", long_sub, lambda: None), ("Second", long_sub, lambda: None)])
    app.draw()
    subs = [c[2] for c in calls if c[0] == long_sub]
    assert [k["focused"] for k in subs] == [True, False]
    assert all(k["maxw"] < 680 for k in subs)
