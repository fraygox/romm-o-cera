"""The background downloader handing the queue back to the app: a transfer or
an unpack stops at once (an unpack rolls back: it's staged in a private folder),
and only a game whose files are being moved into place is waited for, briefly.
An app of either build works with a worker of the other."""
import json
import os
import threading
import time
from types import SimpleNamespace

import rommlib
import rb_dl


class FakeDl:
    def __init__(self, *jobs):
        self.jobs = list(jobs)
        self.paused = False

    def pause_all(self):
        self.paused = True

    def active_count(self):
        return sum(1 for j in self.jobs if j.state in (rommlib.Job.DOWNLOADING, rommlib.Job.EXTRACTING))

    def pending_count(self):
        return self.active_count()


def _job(state, moving=False):
    return SimpleNamespace(state=state, name="Lorem Odyssey", done_bytes=0, total_bytes=1, moving=moving)


def _later(secs, fn):
    t = threading.Timer(secs, fn)
    t.start()
    return t


def test_hand_back_doesnt_wait_for_a_whole_unpack(isolated, monkeypatch):
    """An unpack still extracting (one member can take minutes) is rolled back, not waited for."""
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    monkeypatch.setattr(rb_dl, "HANDBACK_WAIT", 0.3)
    monkeypatch.setattr(rb_dl, "MOVE_WAIT", 10)
    t0 = time.time()
    assert rb_dl.hand_back(FakeDl(_job(rommlib.Job.EXTRACTING))) is False
    assert time.time() - t0 < 3


def test_hand_back_waits_for_a_game_being_moved_into_place(isolated, monkeypatch):
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    monkeypatch.setattr(rb_dl, "HANDBACK_WAIT", 0.3)
    monkeypatch.setattr(rb_dl, "MOVE_WAIT", 10)
    j = _job(rommlib.Job.EXTRACTING, moving=True)
    d = FakeDl(j)
    _later(1.5, lambda: setattr(j, "state", rommlib.Job.DONE))
    t0 = time.time()
    assert rb_dl.hand_back(d) is True
    assert d.paused and time.time() - t0 >= 1.4          # not cut off at HANDBACK_WAIT
    st = json.load(open(rommlib.DL_STATUS))
    assert st["moving"] == "Lorem Odyssey"              # the app saw why it's waiting
    assert "unpacking" not in st                        # an app of the last build would wait 15 min on it


def test_hand_back_stops_waiting_for_a_move_in_the_end(isolated, monkeypatch):
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    monkeypatch.setattr(rb_dl, "HANDBACK_WAIT", 0.2)
    monkeypatch.setattr(rb_dl, "MOVE_WAIT", 1)
    t0 = time.time()
    assert rb_dl.hand_back(FakeDl(_job(rommlib.Job.EXTRACTING, moving=True))) is False
    assert 0.9 <= time.time() - t0 < 4


def test_hand_back_with_a_rommlib_whose_jobs_never_move(isolated, monkeypatch):
    """An older rommlib's Job has no "moving": a plain transfer wait."""
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    monkeypatch.setattr(rb_dl, "HANDBACK_WAIT", 0.3)
    j = SimpleNamespace(state=rommlib.Job.DOWNLOADING, name="Lorem", done_bytes=0, total_bytes=1)
    t0 = time.time()
    assert rb_dl.hand_back(FakeDl(j)) is False
    assert time.time() - t0 < 3
    rb_dl.write_status(FakeDl(j))
    assert json.load(open(rommlib.DL_STATUS))["moving"] is None


def _hold_lock(release):
    held = threading.Event()

    def run():
        with rommlib.try_lock(rommlib.DL_LOCK) as fd:
            assert fd is not None
            held.set()
            release.wait(20)
    threading.Thread(target=run, daemon=True).start()
    assert held.wait(5)


def _status(**kw):
    age = kw.pop("age", 0.0)
    rommlib.atomic_write_json(rommlib.DL_STATUS, dict({"pending": 1, "updated": time.time() - age}, **kw))


def test_handover_waits_while_the_worker_moves_a_game_into_place(isolated, monkeypatch):
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    monkeypatch.setattr(rb_dl, "MOVE_WAIT", 10)
    release = threading.Event()
    _hold_lock(release)
    _status(moving="Lorem Odyssey")
    stop = threading.Event()

    def heartbeat():                                     # the worker keeps its status fresh
        while not stop.wait(0.5):
            _status(moving="Lorem Odyssey")
    threading.Thread(target=heartbeat, daemon=True).start()
    _later(1.5, release.set)
    t0 = time.time()
    try:
        assert rb_dl.handover(timeout=0.3) is True
    finally:
        stop.set()
    took = time.time() - t0
    assert 1.4 <= took < 8                               # waited for the lock, not just timeout
    assert os.path.exists(rommlib.DL_STOP)


def test_handover_stops_waiting_for_a_move_in_the_end(isolated, monkeypatch):
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    monkeypatch.setattr(rb_dl, "MOVE_WAIT", 1)
    monkeypatch.setattr(rb_dl, "_status_moving", lambda: "Lorem")      # says so forever
    release = threading.Event()
    _hold_lock(release)
    try:
        t0 = time.time()
        assert rb_dl.handover(timeout=0.3) is True
        assert 1.2 <= time.time() - t0 < 4
    finally:
        release.set()


def test_handover_doesnt_wait_on_an_unpack_a_plain_or_a_stale_status(isolated):
    """"unpacking" (what the last build's worker says) is no reason to wait; nor
    is a status without "moving" (an older worker) or a stale one."""
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    release = threading.Event()
    _hold_lock(release)
    try:
        for st in ({"unpacking": "Lorem"}, {}, {"moving": "Lorem", "age": rb_dl.STATUS_FRESH + 5}):
            _status(**st)
            t0 = time.time()
            assert rb_dl.handover(timeout=0.3) is True
            assert time.time() - t0 < 3
    finally:
        release.set()


def test_handover_takes_what_an_app_of_the_last_build_passes(isolated):
    """That app calls handover(waiting=...) and reads None as "closed while
    waiting": it gets True or False, never None."""
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    release = threading.Event()
    _hold_lock(release)
    calls = []
    try:
        assert rb_dl.handover(timeout=0.3, unpack_timeout=930, waiting=lambda name: calls.append(name) or True) is True
    finally:
        release.set()
    assert calls == []


def test_handover_without_a_worker_returns_at_once(isolated):
    assert rb_dl.handover(timeout=5) is False
    assert not os.path.exists(rommlib.DL_STOP)


def _app(rb, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    return rb.App(None, headless=True)


def test_the_app_calls_handover_with_no_arguments(isolated, rb, monkeypatch):
    """So it works with an rb_dl.py of the last release too (handover(timeout=30))."""
    import rb_dl as mod
    app = _app(rb, monkeypatch)
    calls = []
    monkeypatch.setattr(mod, "handover", lambda *a, **k: calls.append((a, k)) or False)
    app.take_over_downloads()
    assert calls == [((), {})]


def test_closing_the_app_waits_only_for_a_move(isolated, rb, monkeypatch):
    app = _app(rb, monkeypatch)
    app.headless = False                              # the real app's path, with no window
    unpacking = rommlib.Job({"id": 1, "name": "Lorem"}, "psx")
    unpacking.state = rommlib.Job.EXTRACTING
    moving = rommlib.Job({"id": 2, "name": "Ipsum"}, "psx")
    moving.state, moving.moving = rommlib.Job.EXTRACTING, True
    app.downloader.jobs = [unpacking]
    shown = []
    monkeypatch.setattr(app, "_notice", lambda *a: shown.append(a))
    t0 = time.monotonic()
    app._finish_moves()
    assert time.monotonic() - t0 < 0.5                # an unpack under way: not waited for
    app.downloader.jobs = [unpacking, moving]
    _later(1.3, lambda: setattr(moving, "moving", False))
    try:
        t0 = time.monotonic()
        app._finish_moves()
        assert 1.2 <= time.monotonic() - t0 < 5
    finally:
        app.downloader.jobs = []
    assert shown and "1 game" in shown[0][0]           # said why, once it took a moment
