"""Settings: Low-memory mode back to Auto, the daily update check, and the Auto
theme that follows the controller."""
import pytest

import rommlib

# ------------------------------------------------------------------ Settings

@pytest.fixture
def ui(isolated, rb, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    app = rb.App(None, headless=True)
    names = ["Xbox Wireless Controller"]
    app.input.names = lambda: list(names)
    st = rb.SettingsScreen(app)
    app.push(st)
    yield app, st, names
    rb.apply_theme("default")


def _at(st, key):
    st.sel = [it[0] for it in st.items()].index(key)


def _value(st, key):
    return next(it[2] for it in st.items() if it[0] == key)


def test_low_memory_mode_cycles_auto_on_off(ui, monkeypatch):
    app, st, _ = ui
    monkeypatch.setattr(rommlib, "total_ram", lambda: 2 << 30)
    _at(st, "chdlowmem")
    assert app.cfg["chd_low_memory"] == "auto" and _value(st, "chdlowmem") == "Auto (On, 2.0 GB RAM)"
    seen = []
    for _ in range(3):
        st.action("right")
        seen.append((app.cfg["chd_low_memory"], _value(st, "chdlowmem"), rommlib.load_config()["chd_low_memory"]))
    assert seen == [(True, "On", True), (False, "Off", False), ("auto", "Auto (On, 2.0 GB RAM)", "auto")]
    st.action("left")
    assert app.cfg["chd_low_memory"] is False
    monkeypatch.setattr(rommlib, "total_ram", lambda: 0)
    app.cfg["chd_low_memory"] = "auto"
    assert _value(st, "chdlowmem").startswith("Auto (")


def test_the_daily_update_check_has_a_row_and_updates_still_checks_on_demand(ui, monkeypatch):
    app, st, _ = ui
    asked = []
    monkeypatch.setattr(rommlib, "newer_release", lambda cfg, force=False: asked.append(
        (cfg["check_updates"], force)) or None)
    app.bg = lambda fn, done=None, err=None: done and done(fn())
    keys = [it[0] for it in st.items()]
    assert keys.index("channel") < keys.index("checkupdates") < keys.index("update")
    _at(st, "checkupdates")
    assert _value(st, "checkupdates") == "On"
    st.action("ok")
    assert app.cfg["check_updates"] is False and rommlib.load_config()["check_updates"] is False
    assert _value(st, "checkupdates") == "Off"
    _at(st, "update")
    st.action("ok")
    assert asked == [(True, True)]                    # the button asks GitHub even with the daily check off


def test_theme_for_follows_the_controller(rb):
    t = rb.theme_for
    assert t("auto", "auto", ["Sony Interactive Entertainment Wireless Controller"]) == "playstation"
    assert t("auto_oled", "auto", ["DualSense Wireless Controller"]) == "playstation_oled"
    assert t("auto", "auto", ["Nintendo Switch Pro Controller"]) == "nintendo"
    assert t("auto", "auto", ["Xbox Series X Controller"]) == "xbox"
    assert t("auto", "auto", ["Microsoft X-Box 360 pad"]) == "xbox"
    assert t("auto", "auto", []) == "default"                                   # keyboard
    assert t("auto_oled", "auto", ["Generic USB Gamepad"]) == "default_oled"    # unknown
    assert t("auto", "nintendo", ["Generic USB Gamepad"]) == "nintendo"         # labels set by hand
    assert t("auto", "", []) == "default"
    assert t("xbox_oled", "playstation", ["DualSense"]) == "xbox_oled"          # a chosen theme stays


def test_auto_theme_cycles_repaints_and_follows_a_new_controller(ui, rb):
    app, st, names = ui
    _at(st, "theme")
    st.action("left")
    assert app.cfg["theme"] == "auto_oled" and rb.BG == (0, 0, 0)
    assert rb.ACCENT == rb.THEMES["xbox_oled"][1]["ACCENT"] and _value(st, "theme") == "Auto OLED (Xbox)"
    st.action("left")
    assert app.cfg["theme"] == "auto" and rommlib.load_config()["theme"] == "auto"
    assert rb.BG == rb.THEMES["xbox"][1]["BG"] and _value(st, "theme") == "Auto (Xbox)"
    names[:] = ["DualSense Wireless Controller"]                    # a pad connects
    app.dispatch("padchange")
    assert rb.ACCENT == rb.THEMES["playstation"][1]["ACCENT"] and _value(st, "theme") == "Auto (PlayStation)"
    names[:] = []                                                   # and goes: keyboard
    app.dispatch("padchange")
    assert rb.ACCENT == rb._DEFAULT_PALETTE["ACCENT"] and _value(st, "theme") == "Auto (Default)"
    st.action("left")                                               # wraps to the last theme
    assert app.cfg["theme"] == list(rb.THEMES)[-1]
    st.action("right")
    st.action("right")
    assert app.cfg["theme"] == "auto_oled"
    app.cfg["theme"] = "xbox"
    names[:] = ["DualSense Wireless Controller"]
    app.apply_config()
    app.dispatch("padchange")                                         # a chosen theme doesn't follow
    assert rb.ACCENT == rb.THEMES["xbox"][1]["ACCENT"]


def test_auto_theme_applies_at_start(isolated, rb, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    pygame.init()
    rommlib.save_config(dict(rommlib.DEFAULT_CONFIG, theme="auto_oled", button_style="nintendo"))
    try:
        rb.App(None, headless=True)
        assert rb.BG == (0, 0, 0) and rb.ACCENT == rb.THEMES["nintendo_oled"][1]["ACCENT"]
    finally:
        rb.apply_theme("default")

