"""The background downloader handing the queue back to the app: a transfer
stops at once, but an unpack under way is let finish (or roll back) instead of
being killed half-way, and the app keeps waiting while the worker says so."""
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


def _job(state):
    return SimpleNamespace(state=state, name="Lorem Odyssey", done_bytes=0, total_bytes=1)


def _later(secs, fn):
    t = threading.Timer(secs, fn)
    t.start()
    return t


def test_hand_back_waits_for_an_unpack_past_the_transfer_wait(isolated, monkeypatch):
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    monkeypatch.setattr(rb_dl, "HANDBACK_WAIT", 0.3)
    monkeypatch.setattr(rb_dl, "UNPACK_WAIT", 10)
    j = _job(rommlib.Job.EXTRACTING)
    d = FakeDl(j)
    _later(1.5, lambda: setattr(j, "state", rommlib.Job.DONE))
    t0 = time.time()
    assert rb_dl.hand_back(d) is True
    assert d.paused and time.time() - t0 >= 1.4          # not cut off at HANDBACK_WAIT
    st = json.load(open(rommlib.DL_STATUS))
    assert st["unpacking"] == "Lorem Odyssey"           # the app saw why it's waiting


def test_hand_back_gives_up_on_a_stuck_transfer(isolated, monkeypatch):
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    monkeypatch.setattr(rb_dl, "HANDBACK_WAIT", 0.3)
    monkeypatch.setattr(rb_dl, "UNPACK_WAIT", 10)
    t0 = time.time()
    assert rb_dl.hand_back(FakeDl(_job(rommlib.Job.DOWNLOADING))) is False
    assert time.time() - t0 < 3


def _hold_lock(release):
    held = threading.Event()

    def run():
        with rommlib.try_lock(rommlib.DL_LOCK) as fd:
            assert fd is not None
            held.set()
            release.wait(20)
    threading.Thread(target=run, daemon=True).start()
    assert held.wait(5)


def _status(unpacking, age=0.0):
    rommlib.atomic_write_json(rommlib.DL_STATUS, {"pending": 1, "unpacking": unpacking,
                                                  "updated": time.time() - age})


def test_handover_waits_while_the_worker_is_unpacking(isolated):
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    release = threading.Event()
    _hold_lock(release)
    _status("Lorem Odyssey")
    stop = threading.Event()

    def heartbeat():                                     # the worker keeps its status fresh
        while not stop.wait(0.5):
            _status("Lorem Odyssey")
    threading.Thread(target=heartbeat, daemon=True).start()
    _later(1.5, release.set)
    t0 = time.time()
    try:
        assert rb_dl.handover(timeout=0.3, unpack_timeout=10) is True
    finally:
        stop.set()
    took = time.time() - t0
    assert 1.4 <= took < 8                               # waited for the lock, not just timeout
    assert os.path.exists(rommlib.DL_STOP)


def test_handover_doesnt_wait_on_a_plain_or_stale_status(isolated):
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    release = threading.Event()
    _hold_lock(release)
    try:
        for st in (None, "Lorem Odyssey"):
            _status(st, age=0 if st is None else rb_dl.STATUS_FRESH + 5)   # stale: worker gone
            t0 = time.time()
            assert rb_dl.handover(timeout=0.3, unpack_timeout=10) is True
            assert time.time() - t0 < 3
    finally:
        release.set()


def test_handover_without_a_worker_returns_at_once(isolated):
    assert rb_dl.handover(timeout=5) is False
    assert not os.path.exists(rommlib.DL_STOP)


def test_handover_tells_the_app_while_it_waits_for_an_unpack(tmp_path, monkeypatch):
    """waiting(name) is called while past timeout the worker reports an unpack."""
    import rb_dl
    import rommlib
    calls = []
    seq = iter([None, None, 1])                   # lock held twice, then free

    class L:
        def __init__(self, fd): self.fd = fd
        def __enter__(self): return self.fd
        def __exit__(self, *a): return False
    first = [True]

    def try_lock(path):
        if first[0]:
            first[0] = False
            return L(None)
        return L(next(seq))
    monkeypatch.setattr(rommlib, "try_lock", try_lock)
    monkeypatch.setattr(rommlib, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(rommlib, "DL_STOP", str(tmp_path / "stop"))
    monkeypatch.setattr(rb_dl, "_status_unpacking", lambda: "Nova")
    monkeypatch.setattr(rb_dl.time, "sleep", lambda s: None)
    assert rb_dl.handover(timeout=0, waiting=calls.append) is True
    assert calls and set(calls) == {"Nova"}
