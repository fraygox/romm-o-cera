"""Updating closes the app: downloads pause (kept in the queue), the
background helpers of any version stop, the folder is swapped, and nothing
starts again until the new version is opened."""
import os
import subprocess
import sys
import time

import pytest

import rommlib

# a stand-in helper: rb_dl hands over when downloads.stop appears; "stubborn"
# ignores SIGTERM (only the kill after the timeout ends it, with its child)
HELPER = r"""
import os, signal, subprocess, sys, time
kind, stop = sys.argv[1], sys.argv[2]
if kind == "dl_busy":                     # past its download loop: never looks for downloads.stop
    kind = "plain"
if kind == "stubborn":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
open(stop + ".ready-" + os.path.basename(sys.argv[0]), "w").close()
for _ in range(600):
    if kind == "dl" and os.path.exists(stop):
        sys.exit(0)
    time.sleep(0.1)
"""


def _start(app_dir, script, kind, stop):
    path = os.path.join(app_dir, script)
    with open(path, "w") as f:
        f.write(HELPER)
    return subprocess.Popen([sys.executable, path, kind, stop], start_new_session=True)


def test_stop_helpers_stops_every_helper_of_this_app(isolated, tmp_path):
    app_dir = str(tmp_path / "app")
    os.makedirs(app_dir)
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    other = str(tmp_path / "elsewhere")
    os.makedirs(other)
    procs = {"rb_dl.py": _start(app_dir, "rb_dl.py", "dl", rommlib.DL_STOP),
             "rb_chd.py": _start(app_dir, "rb_chd.py", "stubborn", rommlib.DL_STOP),
             "rb_finalize.py": _start(app_dir, "rb_finalize.py", "plain", rommlib.DL_STOP)}
    stranger = _start(other, "rb_chd.py", "plain", rommlib.DL_STOP)        # another folder: not ours
    for _ in range(100):
        if all(os.path.exists(rommlib.DL_STOP + ".ready-" + s) for s in procs):
            break
        time.sleep(0.05)
    try:
        assert set(rommlib.helper_pids(app_dir).values()) == set(procs)
        t0 = time.monotonic()
        stopped = rommlib.stop_helpers(timeout=2, app_dir=app_dir)
        assert stopped == sorted(procs)
        assert time.monotonic() - t0 < 10
        for p in procs.values():
            p.wait(timeout=5)                        # reaped here; all gone
        assert procs["rb_dl.py"].returncode == 0     # handed over by itself
        assert procs["rb_chd.py"].returncode == -9   # ignored SIGTERM: killed after the wait
        assert rommlib.helper_pids(app_dir) == {}
        assert stranger.poll() is None
    finally:
        for p in list(procs.values()) + [stranger]:
            if p.poll() is None:
                p.kill()
                p.wait()


def test_stop_helpers_with_nothing_running(isolated, tmp_path):
    assert rommlib.stop_helpers(timeout=1, app_dir=str(tmp_path)) == []
    assert not os.path.exists(rommlib.DL_STOP)


@pytest.fixture
def app(isolated, rb, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    a = rb.App(None, headless=True)

    def bg(fn, done=None, err=None):
        try:
            r = fn()
        except Exception as e:
            if err:
                err(e)
            return
        if done:
            done(r)
    a.bg = bg
    return a


def _settings(app, rb):
    app.push(rb.HomeScreen(app))
    st = rb.SettingsScreen(app)
    app.push(st)
    return st


def test_an_update_closes_the_app_and_starts_nothing(app, rb, monkeypatch):
    calls = []
    monkeypatch.setattr(rommlib, "stop_helpers", lambda: calls.append("stop") or [])
    monkeypatch.setattr(rommlib, "es_notify", lambda msg: calls.append(msg))
    monkeypatch.setattr(rommlib, "relaunch_when_closed", lambda **kw: calls.append("relaunch") or True)
    monkeypatch.setattr(app.downloader, "pause_all", lambda: calls.append("pause"))
    spawned = []
    monkeypatch.setattr(app, "spawn_helper", spawned.append)
    st = _settings(app, rb)
    st._swap(lambda: "/staged", lambda staged: calls.append(("install", staged)) or "9.9.9")
    assert calls[:4] == ["pause", "stop", ("install", "/staged"), "relaunch"]
    assert "v9.9.9" in calls[4] and "Opening it again" in calls[4]
    assert app.running is False and app.updated == "9.9.9"
    app.headless = False                              # on_exit as on the box
    monkeypatch.setattr(rommlib, "conversions_pending", lambda: True)
    app.on_exit()
    assert spawned == []                              # it all carries on when the new version starts


def test_a_failed_download_changes_nothing(app, rb, monkeypatch):
    calls = []
    monkeypatch.setattr(rommlib, "stop_helpers", lambda: calls.append("stop") or [])
    monkeypatch.setattr(app.downloader, "pause_all", lambda: calls.append("pause"))

    def prepare():
        raise rommlib.UpdateError("Checksum mismatch — download corrupted, nothing changed")
    st = _settings(app, rb)
    st._swap(prepare, lambda staged: calls.append("install"))
    assert calls == [] and app.running is True and not app.updating


def test_going_back_closes_too(app, rb, monkeypatch):
    calls = []
    monkeypatch.setattr(rommlib, "stop_helpers", lambda: calls.append("stop") or [])
    monkeypatch.setattr(rommlib, "es_notify", lambda msg: None)
    monkeypatch.setattr(rommlib, "relaunch_when_closed", lambda **kw: True)
    st = _settings(app, rb)
    st._swap(None, lambda: calls.append("rollback") or "0.9.0")
    assert calls == ["stop", "rollback"] and app.running is False


@pytest.fixture
def es(isolated, monkeypatch):
    """A fake ES (not the fake_es fixture: that one makes time.sleep a no-op)."""
    from fake_es import FakeES
    e = FakeES(rommlib.ROMS_ROOT, {"gba": {".zip"}})
    monkeypatch.setattr(rommlib, "ES_API", e.url)
    yield e
    e.close()


def test_the_app_is_reopened_once_it_has_quit(es, tmp_path):
    app = str(tmp_path / "app")
    es.running = os.path.join(app, "rommbrowser.pygame")          # still open while the update installs
    assert rommlib.relaunch_when_closed(app)
    time.sleep(1.0)
    assert es.launched == []                                      # not while it runs
    es.running = None                                             # it quits
    for _ in range(60):
        if es.launched:
            break
        time.sleep(0.1)
    assert es.launched == [os.path.join(app, "rommbrowser.pygame")]


def test_nothing_is_reopened_over_a_game_started_meanwhile(es, tmp_path):
    assert rommlib.relaunch_when_closed(str(tmp_path))
    time.sleep(0.5)
    es.running = "/userdata/roms/gba/Lorem.zip"                   # a game starts in the pause
    time.sleep(2.5)
    assert es.launched == []


def test_a_download_worker_past_its_loop_is_stopped_after_a_short_grace(isolated, tmp_path):
    app_dir = str(tmp_path / "app")
    os.makedirs(app_dir)
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    p = _start(app_dir, "rb_dl.py", "dl_busy", rommlib.DL_STOP)
    for _ in range(100):
        if os.path.exists(rommlib.DL_STOP + ".ready-rb_dl.py"):
            break
        time.sleep(0.05)
    try:
        t0 = time.monotonic()
        assert rommlib.stop_helpers(timeout=8, app_dir=app_dir) == ["rb_dl.py"]
        assert p.wait(timeout=5) == -15                   # SIGTERM after the grace, not the kill at the end
        assert time.monotonic() - t0 < 7.5
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()


def test_quitting_while_the_update_installs_waits_for_it(app):
    app.updating = True
    said = []
    app.toast = lambda msg, color=None: said.append(msg)
    app.request_quit()
    assert app.running is True and "Finishing the update" in said[-1]
