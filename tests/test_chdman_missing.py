"""A box whose chdman is missing or won't run: Compress disc games to CHD says Not available,
turning it on says why, and nothing is queued for a converter that can't run (the game stays
as it downloaded, and plays)."""
import os
import stat

import pytest

import rommlib
from romm import convert

REAL = convert.chdman_supported         # taken at import, before conftest's stand-in


@pytest.fixture
def real_check(monkeypatch):
    """The real chdman_supported (conftest stands every other test on a box that has chdman)."""
    monkeypatch.setattr(rommlib, "chdman_supported", REAL)
    convert._CHDMAN.clear()
    yield
    convert._CHDMAN.clear()


def _chdman(tmp_path, monkeypatch, body):
    exe = tmp_path / "bin" / "chdman"
    exe.parent.mkdir(exist_ok=True)
    exe.write_text(body)
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(exe.parent))
    return exe


def test_no_chdman_on_the_box(real_check, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert not rommlib.chdman_supported()
    assert not rommlib.chd_enabled(dict(rommlib.DEFAULT_CONFIG, chd=True))


def test_a_chdman_that_runs_is_fine_whatever_it_answers(real_check, tmp_path, monkeypatch):
    _chdman(tmp_path, monkeypatch, "#!/bin/sh\necho usage; exit 1\n")
    assert rommlib.chdman_supported()
    assert rommlib.chd_enabled(dict(rommlib.DEFAULT_CONFIG, chd=True))
    assert not rommlib.chd_enabled(dict(rommlib.DEFAULT_CONFIG, chd=False))


@pytest.mark.parametrize("body", ["#!/bin/sh\nexit 127\n", "#!/bin/sh\nexit 126\n", "\x7fELF not a program"])
def test_a_chdman_that_cant_run(real_check, tmp_path, monkeypatch, body):
    _chdman(tmp_path, monkeypatch, body)
    assert not rommlib.chdman_supported()


def test_it_is_asked_once_and_again_when_chdman_changes(real_check, tmp_path, monkeypatch):
    exe = _chdman(tmp_path, monkeypatch, "#!/bin/sh\necho ran >> %s\nexit 127\n" % (tmp_path / "runs"))
    assert not rommlib.chdman_supported() and not rommlib.chdman_supported()
    assert (tmp_path / "runs").read_text().count("ran") == 1
    exe.write_text("#!/bin/sh\nexit 0\n# a new one\n")
    os.utime(exe, (1, 1))
    assert rommlib.chdman_supported()


def test_settings_says_not_available_and_why(rb, isolated, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    monkeypatch.setattr(rommlib, "chdman_supported", lambda: False)
    pygame.init()
    app = rb.App(None, headless=True)
    app.bg = lambda fn, done=None, err=None: None
    toasts = []
    monkeypatch.setattr(app, "toast", lambda msg, *a, **k: toasts.append(msg))
    scr = rb.SettingsScreen(app)
    rows = scr.items()
    i = next(i for i, it in enumerate(rows) if it[0] == "chd")
    assert rows[i][2] == "Not available"
    was = app.cfg["chd"]
    scr.sel = i
    scr.action("ok")
    assert app.cfg["chd"] == was and any("chdman" in t for t in toasts)


def test_no_conversion_is_planned_for_a_converter_that_cant_run(isolated, monkeypatch):
    cfg = dict(rommlib.DEFAULT_CONFIG, chd=True)
    chd = [c for c in rommlib.CONVERTER.values() if getattr(c, "chd", False)]
    assert chd and all(c.enabled(cfg, "psx") for c in chd)
    monkeypatch.setattr(rommlib, "chdman_supported", lambda: False)
    assert not any(c.enabled(cfg, "psx") for c in chd)
