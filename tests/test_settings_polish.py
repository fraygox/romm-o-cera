"""Settings: Low-memory mode back to Auto."""
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
