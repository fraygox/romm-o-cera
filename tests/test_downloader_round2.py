"""Downloader, second round: an unpack killed half-way leaves a folder Free up
space offers (and the next unpack clears); a game whose files are all in place
is never failed or deleted over bookkeeping; the update waits only for a game
being moved into place; Free up space carries on past a game it can't delete."""
import os
import subprocess
import sys
import textwrap
import threading
import time
import zipfile
import zlib

import pytest

import rommlib


def _w(p, data):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    return p


def _zip(path, members):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        for n, data in members.items():
            z.writestr(n, data)
    return path


def _downloader():
    d = rommlib.Downloader.__new__(rommlib.Downloader)     # no worker thread
    d.api, d.cfg, d.es, d.unopenable = None, dict(rommlib.DEFAULT_CONFIG), {}, {}
    d.library, d.persist, d.qlock, d.cv = rommlib.Library(), False, threading.Lock(), threading.Condition()
    d.hold, d.rate_limit, d.unpacking_bytes, d.jobs, d._closed = False, 0, 0, [], False
    return d


def _dead_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def _stage(where, pid, tid=1):
    st = os.path.join(where, ".rb-extract-%d-%d" % (pid, tid))
    _w(os.path.join(st, "Game", "Game.bin"), b"half" * 100)
    return st


# ------------------------------------------------------------ 1. an unpack killed half-way

def test_an_unpack_killed_half_way_is_offered_and_deleted(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    os.makedirs(sysdir)
    z = _zip(os.path.join(sysdir, "Game.zip"), {"Game.cue": 'FILE "Game.bin" BINARY\n',
                                                "Game.bin": os.urandom(1 << 20) * 64})
    code = textwrap.dedent("""
        import sys, threading, zipfile; sys.path.insert(0, %r)
        import rommlib
        class J: cancel = threading.Event()
        real = rommlib.Downloader._unzip_member
        def unzip(z, info, stage, j):
            out = real(z, info, stage, j)
            open(%r, "w").close()
            threading.Event().wait()                 # killed here, half-way through the archive
        rommlib.Downloader._unzip_member = staticmethod(unzip)
        rommlib.Downloader.__new__(rommlib.Downloader)._extract(%r, %r, J())
    """ % (os.getcwd(), os.path.join(sysdir, "..", "ready"), z, sysdir))
    p = subprocess.Popen([sys.executable, "-c", code])
    try:
        for _ in range(200):
            if os.path.exists(os.path.join(rommlib.ROMS_ROOT, "ready")):
                break
            time.sleep(0.05)
    finally:
        p.kill()
        p.wait()                                     # power cut / app killed mid-unpack
    left = [n for n in os.listdir(sysdir) if n.startswith(".rb-extract")]
    assert len(left) == 1
    found = [x for x in rommlib.find_leftovers(chd_busy=False) if ".rb-extract" in x["path"]]
    assert [x["kind"] for x in found] == ["Unfinished unpack"]
    assert found[0]["path"] == os.path.join(sysdir, left[0]) and found[0]["size"] > 0
    # nothing inside it is listed on its own (no "Empty folder" for its subfolders)
    assert not [x for x in rommlib.find_leftovers(chd_busy=False) if left[0] + os.sep in x["path"]]
    assert rommlib.delete_leftovers(found) > 0
    assert not any(n.startswith(".rb-extract") for n in os.listdir(sysdir))


def test_a_running_unpack_is_never_offered(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    mine = rommlib.unpack_stage(sysdir)                        # this thread's: it's running
    _w(os.path.join(mine, "Game.bin"), b"x")
    other = _stage(sysdir, os.getppid())                       # another live process's
    assert not rommlib.stage_stale(mine) and not rommlib.stage_stale(other)
    assert not [x for x in rommlib.find_leftovers(chd_busy=False) if ".rb-extract" in x["path"]]
    gone_thread = _stage(sysdir, os.getpid(), tid=1)          # this process, but a thread that's gone
    assert rommlib.stage_stale(gone_thread)
    t = time.time() - rommlib.STALE_STAGE_AGE - 60            # a live pid (reused after a restart?)
    for dp, dns, fns in os.walk(other):                        # whose folder hasn't changed in an hour
        for n in [dp] + [os.path.join(dp, x) for x in fns + dns]:
            os.utime(n, (t, t))
    assert rommlib.stage_stale(other)


def test_a_stage_name_without_its_owner_is_stale_only_once_old(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    odd = os.path.join(sysdir, ".rb-extract-old")
    _w(os.path.join(odd, "x"), b"x")
    assert not rommlib.stage_stale(odd)
    t = time.time() - rommlib.STALE_STAGE_AGE - 60
    os.utime(odd, (t, t))
    assert not rommlib.stage_stale(odd)                        # a file in it changed lately
    os.utime(os.path.join(odd, "x"), (t, t))
    assert rommlib.stage_stale(odd)


def test_the_next_unpack_clears_a_killed_one_and_isnt_refused(isolated):
    """A loose cue+bin set unpacks into roms/psx/Game/: a killed attempt's
    folder in there isn't "another game already on the box"."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    stale = _stage(os.path.join(sysdir, "Game"), _dead_pid())
    live = _stage(sysdir, os.getppid())                        # someone else's running unpack: left alone
    arc = _zip(os.path.join(sysdir, "Game.zip"), {"Game.cue": 'FILE "Game.bin" BINARY\n', "Game.bin": "bin"})
    d = _downloader()
    j = rommlib.Job({"id": 5, "name": "Game", "fs_name": "Game.zip"}, "psx")
    out = d._postprocess_file(j, arc)
    assert out == [os.path.join(sysdir, "Game")]
    assert sorted(os.listdir(os.path.join(sysdir, "Game"))) == ["Game.bin", "Game.cue"]
    assert not os.path.exists(stale) and os.path.isdir(live)


# ------------------------------------------------------------ 2/4. files in place: done, never deleted

def _folder_job(sysdir):
    files = [_w(os.path.join(sysdir, "Game", "Game.cue"), b"CUE!"),
             _w(os.path.join(sysdir, "Game", "Game.bin"), b"B" * 8)]
    j = rommlib.Job({"id": 7, "name": "Game", "fs_name": "Game", "full_path": "psx/Game"}, "psx")
    j.made = list(files)                                # what the job downloaded where nothing was
    return j, files


def test_a_library_that_cant_be_written_doesnt_fail_a_finished_game(isolated, monkeypatch):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    d = _downloader()
    j, files = _folder_job(sysdir)
    d.jobs.append(j)

    def unreadable(*a, **k):
        raise rommlib.LibraryUnreadable("installed.json can't be read right now, so nothing was recorded; try again")
    monkeypatch.setattr(d.library, "update", unreadable)
    d._post(j, {"kind": "folder", "path": os.path.join(sysdir, "Game")})
    assert j.state == rommlib.Job.DONE
    assert j.message.startswith("Ready to play") and "installed.json" in j.message
    assert j.made == []
    assert os.path.exists(rommlib.GAMELIST_QUEUE)               # ES still gets the game's info
    d.remove(j)                                                 # cleared from Activity
    assert all(os.path.exists(p) for p in files)


def test_a_failed_job_with_its_files_in_place_never_deletes_them(isolated, monkeypatch):
    """Whatever goes wrong after the files are in (here the game list queue),
    removing the job from Activity leaves the finished game alone."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    d = _downloader()
    j, files = _folder_job(sysdir)
    d.jobs.append(j)

    def boom(*a, **k):
        raise ValueError("bug")
    monkeypatch.setattr(d.library, "mark", boom)
    with pytest.raises(ValueError) as e:
        d._post(j, {"kind": "folder", "path": os.path.join(sysdir, "Game")})
    d._failed(j, e.value)
    assert j.state == rommlib.Job.FAILED
    d.remove(j)
    assert all(os.path.exists(p) for p in files)


def test_replacing_old_copies_goes_on_past_one_that_cant_be_recorded(isolated, monkeypatch):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "snes")
    new = _w(os.path.join(sysdir, "Game (Rev 1).sfc"), b"new")
    olds = [_w(os.path.join(sysdir, "Game.sfc"), b"old"), _w(os.path.join(sysdir, "Game (Alt).sfc"), b"old")]
    d = _downloader()
    j = rommlib.Job({"id": 9, "name": "Game", "fs_name": "Game (Rev 1).sfc"}, "snes")
    j.replace = list(olds)
    d.library.mark(j.rom, "snes", olds)                          # the app's own old copies
    calls = []
    real = d.library.update

    def update(fn):
        calls.append(fn)
        if len(calls) == 1:
            raise rommlib.LibraryUnreadable("installed.json can't be read right now")
        return real(fn)
    monkeypatch.setattr(d.library, "update", update)
    d._post(j, {"kind": "file", "path": new, "heavy": False})
    assert j.state == rommlib.Job.DONE and "installed.json" in j.message
    assert not any(os.path.exists(p) for p in olds)            # both old copies went
    with open(rommlib.GAMELIST_QUEUE) as f:
        q = f.read()
    assert q.count('"remove"') == 2                             # ES drops both
    assert "9" in d.library.snapshot()                          # the new one is recorded


# ------------------------------------------------------------ 1. the update waits only for a move

HELPER = r"""
import os, sys, time
open(sys.argv[1], "w").close()
time.sleep(3)                     # "moving a game into place": exits by itself, never looks for downloads.stop
"""


def test_stop_helpers_leaves_rb_dl_to_finish_moving_a_game_into_place(isolated, tmp_path):
    app_dir = str(tmp_path / "app")
    os.makedirs(app_dir)
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    path = os.path.join(app_dir, "rb_dl.py")
    with open(path, "w") as f:
        f.write(HELPER)
    ready = str(tmp_path / "ready")
    p = subprocess.Popen([sys.executable, path, ready], start_new_session=True)
    stop = threading.Event()

    def status():                                  # what rb_dl's hand_back writes every second
        while not stop.is_set() and p.poll() is None:
            rommlib.atomic_write_json(rommlib.DL_STATUS, {"moving": "Game", "updated": time.time()})
            time.sleep(0.3)
    try:
        for _ in range(100):
            if os.path.exists(ready):
                break
            time.sleep(0.05)
        t = threading.Thread(target=status, daemon=True)
        t.start()
        assert rommlib.stop_helpers(timeout=1, app_dir=app_dir, move_timeout=30) == ["rb_dl.py"]
        assert p.wait(timeout=5) == 0                  # not SIGTERMed nor killed half-way through
    finally:
        stop.set()
        if p.poll() is None:
            p.kill()
            p.wait()


@pytest.mark.parametrize("says", ["moving", "unpacking"])
def test_stop_helpers_doesnt_wait_long_for_rb_dl(isolated, tmp_path, monkeypatch, says):
    """Moving forever: stopped after move_timeout. "Unpacking" (the last build's
    worker): no reason to wait at all, an unpack rolls back."""
    app_dir = str(tmp_path / "app")
    os.makedirs(app_dir)
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    path = os.path.join(app_dir, "rb_dl.py")
    with open(path, "w") as f:
        f.write("import time\ntime.sleep(60)\n")
    p = subprocess.Popen([sys.executable, path], start_new_session=True)
    stop = threading.Event()

    def status():
        while not stop.is_set():
            rommlib.atomic_write_json(rommlib.DL_STATUS, {says: "Game", "updated": time.time()})
            time.sleep(0.3)
    threading.Thread(target=status, daemon=True).start()
    try:
        for _ in range(100):
            if rommlib.helper_pids(app_dir):
                break
            time.sleep(0.05)
        t0 = time.monotonic()
        rommlib.stop_helpers(timeout=1, app_dir=app_dir, move_timeout=2.5)
        assert p.wait(timeout=10) in (-9, -15)
        took = time.monotonic() - t0
        assert (2.4 <= took < 9) if says == "moving" else took < 2.4
    finally:
        stop.set()
        if p.poll() is None:
            p.kill()
            p.wait()


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


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += s


@pytest.mark.parametrize("case", ["unpacking", "moving", "stuck"])
def test_the_update_waits_for_a_move_but_not_for_an_unpack(app, rb, monkeypatch, case):
    from rbui.screens import settings as settings_mod
    clock = _Clock()
    monkeypatch.setattr(settings_mod, "time", clock)
    j = rommlib.Job({"id": 1, "name": "Game"}, "psx")
    j.state = rommlib.Job.EXTRACTING                 # pause_all hasn't stopped it yet
    j.moving = case != "unpacking"
    app.downloader.jobs.append(j)
    monkeypatch.setattr(app.downloader, "pause_all", lambda: None)
    finish_at = clock.t + 20
    real_sleep = clock.sleep

    def sleep(s):
        real_sleep(s)
        if case == "moving" and clock.t >= finish_at:
            j.state, j.moving = rommlib.Job.DONE, False
    clock.sleep = sleep
    app.post = lambda fn: fn()
    app.toast = lambda msg, color=None, tap=None: None
    stopped = []
    monkeypatch.setattr(rommlib, "stop_helpers", lambda: stopped.append(clock.t) or [])
    monkeypatch.setattr(rommlib, "es_notify", lambda msg: None)
    monkeypatch.setattr(rommlib, "relaunch_when_closed", lambda **kw: True)
    app.push(rb.HomeScreen(app))
    st = rb.SettingsScreen(app)
    app.push(st)
    try:
        st._swap(None, lambda: "1.0")
    finally:
        app.downloader.jobs.remove(j)
    assert len(stopped) == 1
    waited = stopped[0] - 1000.0
    if case == "unpacking":
        assert 15 <= waited < 16                       # what a stuck transfer gets, no more
    elif case == "moving":
        assert 20 <= waited < 21
    else:
        assert rommlib.MOVE_WAIT <= waited < rommlib.MOVE_WAIT + 1


# ------------------------------------------------------------ 3. Free up space carries on

def test_free_up_space_carries_on_past_a_game_it_cant_delete(app, rb, monkeypatch):
    monkeypatch.setattr(rb.FreeUpScreen, "load", lambda self: None)
    sp = rb.FreeUpScreen(app)
    games = [{"system": "snes", "path": "/x/%s.sfc" % n, "name": n, "size": 10} for n in ("Alpha", "Beta", "Gamma")]
    monkeypatch.setattr(sp, "picked", lambda: games)
    monkeypatch.setattr(app, "space_freed", lambda: None)

    def delete_game(system, path, lib, **k):
        if "Alpha" in path:
            raise rommlib.GameBusy("It's being compressed")
        if "Beta" in path:
            raise OSError(13, "Permission denied")
        return [path], 10
    monkeypatch.setattr(rommlib, "delete_game", delete_game)
    said = []
    app.toast = lambda msg, color=None, tap=None: said.append(msg)
    sp.delete_marked()
    app.modal.action("ok")
    assert said, "the delete ran and said how it went"
    assert sp.busy is False
    assert said[-1].startswith("Deleted 1 of 3 games") and "Alpha" in said[-1] and "1 more" in said[-1]


def test_free_up_space_is_never_stuck_on_deleting(app, rb, monkeypatch):
    monkeypatch.setattr(rb.FreeUpScreen, "load", lambda self: None)
    sp = rb.FreeUpScreen(app)
    monkeypatch.setattr(sp, "picked", lambda: [{"system": "snes", "path": "/x/A.sfc", "name": "A", "size": 1}])
    monkeypatch.setattr(app, "space_freed", lambda: None)

    def delete_game(system, path, lib, **k):
        raise ValueError("bug")
    monkeypatch.setattr(rommlib, "delete_game", delete_game)
    app.toast = lambda msg, color=None, tap=None: None
    sp.delete_marked()
    app.modal.action("ok")
    assert sp.busy is False


def test_activity_says_what_didnt_go_through_for_a_finished_game(isolated, rb, monkeypatch):
    import pygame
    from types import SimpleNamespace
    from rbui.screens.downloads import DownloadsScreen
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    app = rb.App(None, headless=True)
    scr = DownloadsScreen(app)
    j = SimpleNamespace(state=rommlib.Job.DONE, message="Ready to play, but installed.json can't be updated now",
                        system="psx", group=None, verified=False, rom={"id": 1}, force=False)
    monkeypatch.setattr(app.library, "is_installed", lambda rom, system: True)
    assert scr.status(j) == ("Ready to play", rb.theme.AMBER)
    assert scr.detail(j) == ("installed.json can't be updated now", rb.theme.AMBER)     # a file name stays as it is
    j.message = "Ready to play, but the old copy is still there"
    assert scr.detail(j) == ("The old copy is still there", rb.theme.AMBER)
    from rbui.screens.downloads import sentence
    assert sentence("its ES entry is late") == "Its ES entry is late"            # never "es"
    # deleted since: the row says so instead of "Ready to play" (asked again after a few seconds)
    monkeypatch.setattr(app.library, "is_installed", lambda rom, system: False)
    j.message = ""
    assert scr.status(j) == ("Ready to play", rb.theme.GREEN)                    # cached for now
    scr._here.clear()
    assert scr.status(j) == ("Deleted since", rb.theme.FAINT)


# ------------------------------------------------------------ round 3: stopping an unpack, the move into place

from romm import downloader as _dlmod          # noqa: E402  (module constants the tests patch)

CUE_SET = {"Game.cue": 'FILE "Game (Track 1).bin" BINARY\n', "Game (Track 1).bin": "one" * 1000,
           "Game (Track 2).bin": "two" * 1000}

KILL_MID_MOVE = """
import os, sys, threading
sys.path.insert(0, %(cwd)r)
import rommlib
d = rommlib.Downloader.__new__(rommlib.Downloader)
d.api, d.cfg, d.es, d.unopenable = None, dict(rommlib.DEFAULT_CONFIG), {}, {}
d.library = rommlib.Library()
real, n = os.replace, [0]
def replace(src, dst):
    if ".rb-extract-" in str(src):
        n[0] += 1
        if n[0] == 2:                              # one file moved in, the next not yet
            open(%(ready)r, "w").close()
            threading.Event().wait()               # killed here
    return real(src, dst)
os.replace = replace
j = rommlib.Job({"id": 5, "name": "Game", "fs_name": "Game.zip"}, "psx")
j.defer_chd = True                                 # as from _post: recorded (and converted) after
d._postprocess_file(j, %(arc)r)
"""


def _kill_mid_move(arc):
    ready = os.path.join(rommlib.ROMS_ROOT, "ready")
    p = subprocess.Popen([sys.executable, "-c", KILL_MID_MOVE % {"cwd": os.getcwd(), "ready": ready, "arc": arc}])
    try:
        for _ in range(400):
            if os.path.exists(ready) or p.poll() is not None:
                break
            time.sleep(0.05)
        assert os.path.exists(ready), "the move never started"
    finally:
        p.kill()
        p.wait()
    os.remove(ready)


def test_a_move_killed_between_two_files_is_finished_by_the_next_attempt(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    arc = _zip(os.path.join(sysdir, "Game.zip"), CUE_SET)
    _kill_mid_move(arc)
    folder = os.path.join(sysdir, "Game")
    half = [n for n in os.listdir(folder) if not n.startswith(".")]
    assert len(half) == 1                                   # half a game on disk, nothing recorded
    assert [n for n in os.listdir(sysdir) if n.startswith(".rb-moving-")]
    d = _downloader()
    # another game (or a copy put there another way) is still never unpacked over
    with pytest.raises(rommlib.RommError, match="already on the box|not unpacking over it"):
        d._postprocess_file(rommlib.Job({"id": 6, "name": "Game", "fs_name": "Game.zip"}, "psx"), arc)
    j = rommlib.Job({"id": 5, "name": "Game", "fs_name": "Game.zip"}, "psx")
    d.jobs.append(j)
    d._post(j, {"kind": "file", "path": arc, "heavy": True})
    assert j.state == rommlib.Job.DONE, j.message
    assert sorted(os.listdir(folder)) == sorted(CUE_SET)    # whole, nothing of the stage left
    for n, data in CUE_SET.items():
        assert open(os.path.join(folder, n)).read() == data
    assert not os.path.exists(arc)
    assert d.library.snapshot()["5"]["paths"] == [folder]
    assert not [n for n in os.listdir(sysdir) if n.startswith((".rb-moving-", ".rb-extract-"))]
    assert not j.moving


def test_the_marker_stays_while_the_game_isnt_recorded(isolated, monkeypatch):
    """installed.json can't be written: the game is done (a warning), and its
    marker stays so downloading it again may unpack over it."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    arc = _zip(os.path.join(sysdir, "Game.zip"), CUE_SET)
    d = _downloader()
    real = d.library.update
    broken = [True]

    def update(fn):
        if broken[0]:
            raise rommlib.LibraryUnreadable("installed.json can't be read right now")
        return real(fn)
    monkeypatch.setattr(d.library, "update", update)
    j = rommlib.Job({"id": 5, "name": "Game", "fs_name": "Game.zip"}, "psx")
    d._post(j, {"kind": "file", "path": arc, "heavy": True})
    assert j.state == rommlib.Job.DONE and "installed.json" in j.message
    assert [n for n in os.listdir(sysdir) if n.startswith(".rb-moving-")]
    broken[0] = False
    arc = _zip(os.path.join(sysdir, "Game.zip"), CUE_SET)          # downloaded again
    j2 = rommlib.Job({"id": 5, "name": "Game", "fs_name": "Game.zip"}, "psx")
    d._post(j2, {"kind": "file", "path": arc, "heavy": True})
    assert j2.state == rommlib.Job.DONE and j2.message.startswith("Ready to play"), j2.message
    assert "5" in d.library.snapshot()


def test_trouble_after_the_files_are_in_place_finishes_the_game(isolated, monkeypatch):
    """The archive can't be deleted: Ready to play with a warning, recorded, and
    its conversion queued only after it's recorded."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    arc = _zip(os.path.join(sysdir, "Game.zip"), CUE_SET)
    d = _downloader()
    real = os.remove

    def remove(p, *a, **k):
        if p == arc:
            raise PermissionError(13, "Permission denied")
        return real(p, *a, **k)
    monkeypatch.setattr(os, "remove", remove)
    seen = []
    monkeypatch.setattr(d, "_maybe_chd", lambda j, system, playable, roots: seen.append(
        "5" in d.library.snapshot()) or setattr(j, "chd_task", "t.json"))
    j = rommlib.Job({"id": 5, "name": "Game", "fs_name": "Game.zip"}, "psx")
    d._post(j, {"kind": "file", "path": arc, "heavy": True})
    assert j.state == rommlib.Job.DONE and "archive" in j.message
    assert seen == [True] and j.chd_queued
    assert d.library.snapshot()["5"]["paths"] == [os.path.join(sysdir, "Game")]


def test_a_conversion_that_cant_be_queued_doesnt_fail_the_game(isolated, monkeypatch):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    arc = _zip(os.path.join(sysdir, "Game.zip"), CUE_SET)
    d = _downloader()

    def boom(*a):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(d, "_maybe_chd", boom)
    j = rommlib.Job({"id": 5, "name": "Game", "fs_name": "Game.zip"}, "psx")
    d._post(j, {"kind": "file", "path": arc, "heavy": True})
    assert j.state == rommlib.Job.DONE and "compression" in j.message
    assert "5" in d.library.snapshot()


class _CancelAfter:
    def __init__(self, n):
        self.n = n

    def is_set(self):
        self.n -= 1
        return self.n < 0


def test_a_pause_stops_an_unzip_inside_a_big_member(isolated, monkeypatch):
    monkeypatch.setattr(_dlmod, "UNZIP_CHUNK", 4096)
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    arc = _zip(os.path.join(sysdir, "Game.zip"), {"Game.iso": os.urandom(1 << 20)})
    j = rommlib.Job({"id": 1, "name": "Game"}, "psx")
    j.cancel = _CancelAfter(10)                       # a few chunks into the one member
    with pytest.raises(_dlmod.Cancelled):
        _downloader()._extract(arc, sysdir, j)
    assert sorted(os.listdir(sysdir)) == ["Game.zip"]  # no stage, nothing half-written
    assert not j.moving


def test_a_pause_stops_7z(isolated, tmp_path):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    arc = _w(os.path.join(sysdir, "Game.7z"), b"7z")
    fake = tmp_path / "7zz"
    fake.write_text("#!/bin/sh\nsleep 30\n")
    fake.chmod(0o755)
    d = _downloader()
    d._7z = lambda: str(fake)
    d._list_archive = lambda p: ["Game.iso"]
    j = rommlib.Job({"id": 1, "name": "Game"}, "psx")
    threading.Timer(0.5, j.cancel.set).start()
    t0 = time.monotonic()
    with pytest.raises(_dlmod.Cancelled):
        d._extract(arc, sysdir, j)
    assert time.monotonic() - t0 < 5
    assert sorted(os.listdir(sysdir)) == ["Game.7z"]


def test_a_pause_during_the_move_doesnt_cut_it_short(isolated, monkeypatch):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    arc = _zip(os.path.join(sysdir, "Game.zip"), CUE_SET)
    j = rommlib.Job({"id": 5, "name": "Game", "fs_name": "Game.zip"}, "psx")
    real = os.replace
    flags = []

    def replace(src, dst):
        if ".rb-extract-" in str(src):
            flags.append(j.moving)
            j.cancel.set()                           # Pause all, while the files go in
        return real(src, dst)
    monkeypatch.setattr(os, "replace", replace)
    d = _downloader()
    d.jobs.append(j)
    assert [x.name for x in d.moving_jobs()] == []
    d._post(j, {"kind": "file", "path": arc, "heavy": True})
    assert flags == [True] * 3
    assert j.state == rommlib.Job.DONE
    assert sorted(os.listdir(os.path.join(sysdir, "Game"))) == sorted(CUE_SET)


def test_wait_moves_is_bounded(isolated):
    d = _downloader()
    j = rommlib.Job({"id": 5, "name": "Game"}, "psx")
    d.jobs.append(j)
    assert d.wait_moves(timeout=5) is True             # nothing moving: at once
    j.moving = True
    ticks = []
    t0 = time.monotonic()
    assert d.wait_moves(timeout=0.3, tick=lambda busy, w: ticks.append(busy)) is False
    assert time.monotonic() - t0 < 2 and ticks and ticks[0] == [j]


def test_zip_members_with_the_same_file_name_are_told_apart(tmp_path):
    p = str(tmp_path / "set.zip")
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("Disc A/Track.bin", b"aaaa")
        z.writestr("Disc B/Track.bin", b"bbbbbb")
        z.writestr("Data/Game.iso", b"iso")
    crc = lambda b: "%08x" % (zlib.crc32(b) & 0xFFFFFFFF)
    a = {"name": "Disc A\\Track.bin", "size": 4, "crc_hash": crc(b"aaaa")}     # RomM's backslashes
    b = {"name": "disc b/track.bin", "size": 6, "crc_hash": crc(b"bbbbbb")}     # another case
    assert rommlib.zip_matches(p, [a, b])[0] is True
    # a bare name two members share: neither is guessed (the last one used to be)
    assert rommlib.zip_matches(p, [{"name": "Track.bin", "size": 6, "crc_hash": crc(b"bbbbbb")}])[0] is False
    # a bare name only one member has: found by it
    assert rommlib.zip_matches(p, [{"name": "Game.iso", "size": 3, "crc_hash": crc(b"iso")}])[0] is True


def test_free_up_space_never_deletes_an_unpack_that_started_since(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    mine = rommlib.unpack_stage(sysdir)                  # this thread's: running now
    _w(os.path.join(mine, "Game.bin"), b"x")
    rommlib.delete_leftovers([{"path": mine, "kind": "Unfinished unpack", "size": 1}])
    assert os.path.isdir(mine)


def test_free_up_space_explains_an_unfinished_unpack(rb):
    from rbui.screens.space import LeftoversScreen
    assert "Unfinished unpack" in LeftoversScreen.WHY


@pytest.mark.parametrize("err", ["unrecorded", "oserror"])
def test_deleting_installed_games_counts_one_whose_record_lags(app, rb, monkeypatch, err):
    from rbui.screens.installed import InstalledScreen
    monkeypatch.setattr(InstalledScreen, "load", lambda self: None)
    scr = InstalledScreen(app)
    said = []
    app.toast = lambda msg, color=None, tap=None: said.append(msg)
    monkeypatch.setattr(app, "space_freed", lambda: None)

    def uninstall(rom, system):
        if err == "oserror":
            raise OSError(5, "Input/output error")
        e = rommlib.LibraryUnreadable("Deleted Lorem — 2.0 GB freed, but installed.json can't be updated now")
        e.paths, e.freed, e.failed = ["/x/Lorem.chd"], 2 << 30, []
        raise e
    monkeypatch.setattr(app.library, "uninstall", uninstall)
    scr.ask_delete_many([{"id": 1, "name": "Lorem", "system": "psx", "size": 2 << 30}])
    app.modal.action("ok")
    assert said
    if err == "oserror":
        assert said[-1].startswith("Deleted 0 of 1 game") and "Lorem: Input/output error" in said[-1]
    else:
        assert said[-1].startswith("Deleted 1 of 1 game — 2.0 GB freed")
        assert "Lorem: deleted, but installed.json can't be updated now" in said[-1]
        assert said[-1].count("Lorem") == 1
