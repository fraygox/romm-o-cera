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
    assert list(rb.THEMES)[0] == "default" and len(rb.THEMES) == 31
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


# ------------------------------------------------------------ the picker

def test_every_theme_has_a_card_or_is_a_cards_oled_version(rb):
    cards = [k for _, keys in rb.THEME_GROUPS for k in keys]
    assert len(cards) == len(set(cards)) and cards[0] == "default"
    reach = set(cards) | {rb.theme_oled(k) for k in cards if rb.theme_oled(k)}
    assert reach == set(rb.THEMES)
    assert all(rb.THEME_DESC.get(k) for k in cards)
    assert rb.theme_oled("virtualboy_oled") == "virtualboy_oled" and rb.theme_oled("dmg") is None
    assert rb.theme_base("xbox_oled") == "xbox" and rb.theme_base("virtualboy_oled") == "virtualboy_oled"


def _picker(app, rb):
    st = rb.SettingsScreen(app)
    app.push(st)
    st.sel = [r[0] for r in st.items()].index("theme")
    st.action("ok")
    pk = app.stack[-1]
    assert isinstance(pk, rb.ThemePickerScreen) and pk.crumbs() == ["Settings", "Theme"]
    return st, pk


def test_moving_previews_b_puts_the_old_theme_back_a_keeps_it(ui, rb):
    app = ui
    st, pk = _picker(app, rb)
    assert pk.key() == "default"
    pk.action("right")
    assert pk.key() == "playstation" and rb.ACCENT == rb.THEMES["playstation"][1]["ACCENT"]
    assert app.cfg["theme"] == "default" and rommlib.load_config()["theme"] == "default"   # only a preview
    pk.action("back")
    assert app.stack[-1] is st and rb.ACCENT == rb._DEFAULT_PALETTE["ACCENT"]
    st.action("ok")
    pk = app.stack[-1]
    pk.action("y")                                                    # Default's OLED version
    assert pk.key() == "default_oled" and rb.BG == (0, 0, 0)
    pk.action("ok")
    assert app.stack[-1] is st and app.cfg["theme"] == "default_oled"
    assert rommlib.load_config()["theme"] == "default_oled" and rb.BG == (0, 0, 0)
    st.action("ok")                                                   # an OLED theme: every card starts OLED
    pk = app.stack[-1]
    assert pk.key() == "default_oled"
    pk.action("right")
    assert pk.key() == "playstation_oled"
    app.dispatch("start")                                             # Home: the saved theme, not the preview
    assert len(app.stack) == 1 and rb.THEMES["default_oled"][1]["PANEL"] == rb.PANEL


def test_the_grid_moves_by_row_column_and_group(ui, rb):
    app = ui
    _, pk = _picker(app, rb)
    pk.action("down")
    assert pk.key() == "high_contrast"                                # Built in's second row
    pk.action("right")
    pk.action("up")
    assert pk.key() == "playstation"
    pk.action("down")
    pk.action("down")
    assert pk.key() == "virtualboy_oled"                              # into Consoles, same column
    pk.action("y")                                                    # only OLED: stays
    assert pk.key() == "virtualboy_oled"
    pk.action("r1")
    assert pk.key() == "phosphor"
    pk.action("r1")
    pk.action("l1")
    assert pk.key() == "phosphor"                                     # back to the start of the group before
    pk.action("l1")
    assert pk.key() == "dmg"
    for _ in range(40):
        pk.action("down")
    assert pk.sel == len(pk.items) - 4 or pk.cell(pk.sel)[0] == len(pk.groups) - 1


@pytest.mark.parametrize("k", [1.0, 1.5, 3.0])
def test_the_picker_draws_at_every_scale_and_taps_preview_then_keep(ui, rb, monkeypatch, k):
    import pygame
    app = ui
    monkeypatch.setattr(rb.theme, "K", k)
    app.canvas = pygame.Surface((rb.S(rb.W), rb.S(rb.H)))
    _, pk = _picker(app, rb)
    for _ in range(len(pk.items)):                                    # every card, scrolled into view
        app.targets = []
        pk.draw(app.canvas)
        view = pygame.Rect(0, pk.TOP - 4, rb.W, pk.BOTTOM - pk.TOP + 8)
        assert app.targets and all(view.contains(r) for r, _ in app.targets)
        pk.action("right")
    pk.drawn_at = None
    app.targets = []
    pk.draw(app.canvas)
    taps = [fn for r, fn in app.targets if fn.__defaults__ and fn.__name__ == "<lambda>"]
    taps[0]()                                                         # first tap: a preview
    assert app.stack[-1] is pk and app.cfg["theme"] == "default"
    want = pk.key()
    taps[0]()                                                         # the same card again: keep it
    assert app.cfg["theme"] == want and app.stack[-1] is not pk


def test_match_theme_to_controller_still_wins_and_says_so(ui, rb):
    app = ui
    app.cfg["theme_auto"] = True
    app.input.names = lambda: ["Xbox Wireless Controller"]
    app.apply_config()
    assert rb.ACCENT == rb.THEMES["xbox"][1]["ACCENT"]
    _, pk = _picker(app, rb)
    pk.go(pk.items.index("synthwave"))
    pk.draw(app.canvas)
    said = []
    app.toast = lambda msg, color=None: said.append(msg)
    pk.action("ok")
    assert app.cfg["theme"] == "synthwave" and rb.ACCENT == rb.THEMES["xbox"][1]["ACCENT"]
    assert said and "Match theme to controller" in said[0] and "Xbox" in said[0]


# ------------------------------------------------------------ Custom

def _floors(p):
    for ground in ("BG", "PANEL", "BG2"):
        assert contrast(p["TEXT"], p[ground]) >= 12 and contrast(p["DIM"], p[ground]) >= 6.5
        assert contrast(p["FAINT"], p[ground]) >= 4.5
    assert contrast(p["TEXT"], p["PANEL_HI"]) >= 7
    for status in ("RED", "GREEN", "AMBER", "BLUE"):
        assert contrast(p[status], p["PANEL"]) >= 4.5 and _dist(p[status], p["ACCENT"]) > 80


@pytest.mark.parametrize("mode", ["dark", "oled", "light"])
def test_no_custom_choice_is_hard_to_read(rb, mode):
    """Every hue step of accent and tint, every strength step: the floors every theme meets.
    (Every one of the 24 x 24 x 11 x 3 combinations was checked once; this walks a third of them.)"""
    for accent in range(0, 360, 15):
        for tint in range(0, 360, 45):
            for strength in (0, 30, 60, 100):
                p = rb.custom_palette(accent, tint, strength, mode)
                assert set(p) == set(rb.PALETTE)
                _floors(p)
                if mode == "oled":
                    assert p["BG"] == (0, 0, 0)
                if mode == "light":
                    assert rb.luminance(p["BG"]) > 0.5
    _floors(rb.custom_palette("junk", None, 999, "neon"))          # hand-edited nonsense: still readable


def test_the_custom_settings_defaults_match_the_palette_and_odd_values_fall_back(isolated, rb):
    st = rommlib.SETTING
    assert (st["theme_custom_accent"].default, st["theme_custom_tint"].default, st["theme_custom_strength"].default,
            st["theme_custom_mode"].default) == (195, 225, 30, "dark")
    assert rb.custom_palette() == rb.custom_palette(195, 225, 30, "dark")
    import json
    import os
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    with open(rommlib.CONFIG_PATH, "w") as f:
        json.dump({"theme_custom_accent": 400, "theme_custom_tint": True, "theme_custom_strength": "50",
                   "theme_custom_mode": "neon"}, f)
    cfg = rommlib.load_config()
    assert [cfg[k] for k in rb.CUSTOM_KEYS] == [195, 225, 30, "dark"]


def _custom(app, rb):
    _, pk = _picker(app, rb)
    pk.go(pk.items.index("custom"))
    assert pk.hints()[0] == ("A", "Edit")
    pk.action("ok")
    cs = app.stack[-1]
    assert isinstance(cs, rb.CustomThemeScreen) and cs.crumbs() == ["Settings", "Theme", "Custom"]
    return pk, cs


def test_custom_previews_as_you_change_it_and_b_leaves_it_as_it_was(ui, rb):
    app = ui
    pk, cs = _custom(app, rb)
    assert rb.ACCENT == rb.custom_palette()["ACCENT"]
    cs.action("right")
    assert cs.v["accent"] == 210 and rb.ACCENT == rb.custom_palette(210, 225, 30, "dark")["ACCENT"]
    cs.action("r1")
    assert cs.v["accent"] == 270
    cs.action("l1")
    cs.action("l1")
    cs.action("l1")
    cs.action("l1")
    assert cs.v["accent"] == 30                                       # round the wheel
    for _ in range(3):
        cs.action("down")
    cs.action("left")                                                 # mode wraps round to Light
    assert cs.v["mode"] == "light" and rb.luminance(rb.BG) > 0.5
    cs.action("up")
    for _ in range(12):
        cs.action("right")
    assert cs.v["strength"] == 100
    cs.action("back")
    assert app.stack[-1] is pk and app.cfg["theme"] == "default"
    assert rb.THEMES["custom"][1] == rb.custom_palette() and rb.ACCENT == rb.custom_palette()["ACCENT"]
    assert rommlib.load_config()["theme_custom_accent"] == 195          # nothing saved


def test_custom_a_saves_four_settings_and_uses_it_from_start(ui, rb):
    app = ui
    pk, cs = _custom(app, rb)
    app.targets = []
    cs.draw(app.canvas)
    assert len(app.targets) >= 4 + 24 + 24 + 11 + 3                   # rows, swatches, strength steps, modes
    cs.set("accent", 330)
    cs.set("mode", "oled")
    assert rb.BG == (0, 0, 0)
    cs.action("ok")
    assert isinstance(app.stack[-1], rb.SettingsScreen)                # the picker closed too
    saved = rommlib.load_config()
    assert saved["theme"] == "custom" and [saved[k] for k in rb.CUSTOM_KEYS] == [330, 225, 30, "oled"]
    want = rb.custom_palette(330, 225, 30, "oled")
    assert rb.ACCENT == want["ACCENT"] and rb.BG == (0, 0, 0)
    rb.set_custom(195, 225, 30, "dark")                               # a fresh start reads config.json
    rb.apply_theme("default")
    app2 = rb.App(None, headless=True)
    assert app2.cfg["theme"] == "custom" and rb.ACCENT == want["ACCENT"]
    st = rb.SettingsScreen(app2)
    assert {it[0]: it[2] for it in st.items()}["theme"] == "Custom"
    for scr in (st, rb.ThemePickerScreen(app2), rb.CustomThemeScreen(app2)):
        app2.canvas.fill(rb.BG)
        scr.draw(app2.canvas)
    rb.set_custom(195, 225, 30, "dark")


def test_home_from_custom_leaves_no_unsaved_colours_behind(ui, rb):
    """Start went home past the Custom screen's own restore: its unsaved
    colours stayed on screen (and on the Custom card)."""
    app = ui
    pk, cs = _custom(app, rb)
    cs.set("accent", 330)
    app.dispatch("start")
    assert len(app.stack) == 1 and app.cfg["theme"] == "default"
    assert rb.ACCENT == rb._DEFAULT_PALETTE["ACCENT"]
    assert rb.THEMES["custom"][1] == rb.custom_palette()              # the saved custom colours again


def test_custom_kept_while_the_controller_picks_the_theme_shows_the_controller_theme(ui, rb):
    app = ui
    app.cfg["theme_auto"] = True
    app.input.names = lambda: ["Xbox Wireless Controller"]
    app.apply_config()
    said = []
    app.toast = lambda msg, color=None: said.append(msg)
    pk, cs = _custom(app, rb)
    cs.action("ok")
    assert app.cfg["theme"] == "custom" and isinstance(app.stack[-1], rb.SettingsScreen)
    assert rb.ACCENT == rb.THEMES["xbox"][1]["ACCENT"] and "Xbox" in said[-1]
