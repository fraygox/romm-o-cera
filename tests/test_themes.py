"""Settings → Theme: every theme readable from the couch (contrast floors),
its status colours distinct from its accent, and a change repainting the
whole app at once (every colour is drawn from the palette names)."""
import pytest

import rommlib


def _lum(c):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(x) for x in c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _dist(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


@pytest.fixture
def themed(rb):
    yield rb
    rb.apply_theme("default")


def test_every_theme_is_readable_and_keeps_status_apart_from_the_accent(themed):
    """Floors are the Default palette's own levels: no theme is harder to read."""
    rb = themed
    assert list(rb.THEMES)[0] == "default" and len(rb.THEMES) == 30
    for name, (label, _) in rb.THEMES.items():
        assert "Nintendo" not in label, name                          # the Nintendo-inspired ones have own names
        assert rb.apply_theme(name) == name
        p = {k: getattr(rb, k) for k in rb.PALETTE}
        for k, v in p.items():
            assert len(v) == 3 and all(0 <= x <= 255 for x in v), (name, k)
        for ground in ("BG", "PANEL", "BG2"):
            assert contrast(p["TEXT"], p[ground]) >= 12, (name, ground)
            assert contrast(p["DIM"], p[ground]) >= 6.5, (name, ground)
            assert contrast(p["FAINT"], p[ground]) >= 4.5, (name, ground)
        assert contrast(p["TEXT"], p["PANEL_HI"]) >= 7, name          # the focused row
        for status in ("RED", "GREEN", "AMBER", "BLUE"):
            assert contrast(p[status], p["PANEL"]) >= 4.5, (name, status)
            assert _dist(p[status], p["ACCENT"]) > 80, (name, status)   # never mistaken for the focus
        if name.endswith("_oled"):
            assert p["BG"] == (0, 0, 0) and label.endswith("OLED")


def test_a_theme_switch_reaches_derived_colours_and_unknown_is_default(themed):
    rb = themed
    rb.apply_theme("xbox")
    assert rb.ACCENT == rb.THEMES["xbox"][1]["ACCENT"]
    assert rb.SPACE_PARTS[0][1] == rb.ACCENT and rb.SYSTEM_COL == rb.mix(rb.BG, rb.FAINT, 0.57)
    rb.apply_theme("nintendo")
    assert rb.GREEN == rb._DEFAULT_PALETTE["GREEN"]                      # Xbox's mint didn't stick
    assert rb.apply_theme("no-such-theme") == "default"
    assert {k: getattr(rb, k) for k in rb.PALETTE} == rb._DEFAULT_PALETTE


@pytest.fixture
def ui(isolated, rb, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    app = rb.App(None, headless=True)
    yield app
    rb.apply_theme("default")


def test_settings_theme_row_cycles_and_repaints_at_once(ui, rb):
    app = ui
    st = rb.SettingsScreen(app)
    app.push(st)
    keys = [r[0] for r in st.items() if r and len(r) > 1]
    st.sel = keys.index("theme")
    st.action("right")
    assert app.cfg["theme"] == "default_oled" and rb.BG == (0, 0, 0)
    assert rommlib.load_config()["theme"] == "default_oled"          # saved
    st.action("left")
    st.action("left")                                                 # wraps round to the last theme
    last = list(rb.THEMES)[-1]
    assert app.cfg["theme"] == last and rb.ACCENT == rb.THEMES[last][1]["ACCENT"]
    for name in rb.THEMES:                                            # every screen draws in every theme
        rb.apply_theme(name)
        for scr in (st, rb.OnThisBoxScreen(app)):
            app.canvas.fill(rb.BG)
            scr.draw(app.canvas)
    rb.apply_theme("default")
    assert rb.App(None, headless=True).cfg["theme"] == last
    assert rb.ACCENT == rb.THEMES[last][1]["ACCENT"]                 # applied again at start
