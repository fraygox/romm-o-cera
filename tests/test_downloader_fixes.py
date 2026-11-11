"""Downloader regressions: a resume answered from the wrong offset, an unpack
cut short over an installed copy, what a cancel leaves in roms/<system>, and
"Pause all" racing the worker."""
import errno
import os
import threading
import zipfile

import pytest

import rommlib


class _Resp:
    def __init__(self, body, status=200, headers=None, on_chunk=None):
        self.body, self.status_code, self.headers = body, status, headers or {}
        self.on_chunk = on_chunk

    def iter_content(self, n):
        for i in range(0, len(self.body), n or 1):
            if self.on_chunk:
                self.on_chunk()
            yield self.body[i:i + n]

    def close(self):
        pass


class _Api:
    base = "http://romm"

    def __init__(self, detail=None, answer=None):
        self.detail, self.answer, self.asked = detail, answer, []
        self.s = self

    def rom(self, rid):
        return self.detail

    def get(self, url, headers=None, **k):
        self.asked.append((url, dict(headers or {})))
        return self.answer(url, headers or {})


def _downloader(api=None):
    d = rommlib.Downloader.__new__(rommlib.Downloader)     # no worker thread
    d.api, d.cfg, d.es, d.unopenable = api, dict(rommlib.DEFAULT_CONFIG), {}, {}
    d.library, d.persist, d.qlock, d.cv = rommlib.Library(), False, threading.Lock(), threading.Condition()
    d.hold, d.rate_limit, d.unpacking_bytes, d.jobs, d._closed = False, 0, 0, [], False
    return d


def _w(p, data):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    return p


# ------------------------------------------------------------ 1. resume at the wrong offset

@pytest.mark.parametrize("cr, want", [
    ("bytes 6-19/20", b"A" * 6 + b"B" * 14),        # the rest, as asked: appended
    ("bytes 0-19/20", b"B" * 20),                   # a proxy answering from 0: written over, not appended
])
def test_a_resumed_206_is_appended_only_at_the_resume_offset(isolated, cr, want):
    dest = os.path.join(rommlib.ROMS_ROOT, "gba", "G.gba")
    _w(dest + ".part", b"A" * 6)
    body = b"B" * (20 - int(cr.split()[1].split("-")[0]))
    api = _Api(answer=lambda url, h: _Resp(body, 206, {"Content-Range": cr}))
    j = rommlib.Job({"id": 1, "name": "G"}, "gba")
    _downloader(api)._fetch(j, "/g", dest, 20, {"file_name": "G.gba", "file_size_bytes": 20})
    assert api.asked[0][1] == {"Range": "bytes=6-"}
    assert open(dest, "rb").read() == want


@pytest.mark.parametrize("headers", [{"Content-Range": "bytes 3-16/20"}, {}])
def test_a_206_from_elsewhere_starts_the_download_over(isolated, headers):
    dest = os.path.join(rommlib.ROMS_ROOT, "gba", "G.gba")
    _w(dest + ".part", b"A" * 6)
    # exactly the 14 bytes left: appended, they'd make a 20-byte file of the wrong bytes
    api = _Api(answer=lambda url, h: _Resp(b"B" * 14, 206, headers))
    j = rommlib.Job({"id": 1, "name": "G"}, "gba")
    with pytest.raises(rommlib.NetError):
        _downloader(api)._fetch(j, "/g", dest, 20, {"file_name": "G.gba", "file_size_bytes": 20})
    assert not os.path.exists(dest + ".part") and not os.path.exists(dest)   # next try: from 0


# ------------------------------------------------------------ 2. unpacking over an installed copy

def _zip(path, members):
    with zipfile.ZipFile(path, "w") as z:
        for n, data in members.items():
            z.writestr(n, data)
    return path


@pytest.mark.parametrize("how", ["cancel", "disk full"])
def test_an_unpack_cut_short_leaves_the_installed_copy_whole(isolated, monkeypatch, how):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    old = {"Game/Game.cue": b"old cue", "Game/Game.bin": b"old bin"}
    for n, data in old.items():
        _w(os.path.join(sysdir, n), data)
    arc = _zip(os.path.join(sysdir, "Game.zip"), {"Game/Game.cue": "new cue", "Game/Game.bin": "new bin",
                                                   "Game/Extra.txt": "x"})
    j = rommlib.Job({"id": 1, "name": "Game"}, "psx")
    real, n = rommlib.Downloader._unzip_member, [0]

    def unzip_member(z, info, stage, j_):
        n[0] += 1
        if n[0] == 2:
            if how == "cancel":
                j.cancel.set()
            else:
                raise OSError(errno.ENOSPC, "No space left on device")
        return real(z, info, stage, j_)
    monkeypatch.setattr(rommlib.Downloader, "_unzip_member", staticmethod(unzip_member))
    with pytest.raises((rommlib.Cancelled, OSError)):
        _downloader()._extract(arc, sysdir, j)
    for n_, data in old.items():
        assert open(os.path.join(sysdir, n_), "rb").read() == data
    assert sorted(os.listdir(os.path.join(sysdir, "Game"))) == ["Game.bin", "Game.cue"]
    assert not any(x.startswith(".rb-extract") for x in os.listdir(sysdir))


def test_a_finished_unpack_replaces_the_installed_copy(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    old = _w(os.path.join(sysdir, "Game", "Game.bin"), b"old bin")
    rommlib.Library().mark({"id": 1, "name": "Game"}, "psx", [os.path.dirname(old)], files=[old])   # the app's copy
    arc = _zip(os.path.join(sysdir, "Game.zip"), {"Game/Game.cue": "new cue", "Game/Game.bin": "new bin",
                                                   "Game/sub/": ""})
    _downloader()._extract(arc, sysdir, rommlib.Job({"id": 1}, "psx"))
    assert open(os.path.join(sysdir, "Game", "Game.bin")).read() == "new bin"
    assert open(os.path.join(sysdir, "Game", "Game.cue")).read() == "new cue"
    assert os.path.isdir(os.path.join(sysdir, "Game", "sub"))
    assert sorted(os.listdir(sysdir)) == ["Game", "Game.zip"]


# ------------------------------------------------------------ 3. what a cancel leaves behind

def _folder_rom():
    return {"id": 7, "name": "Game", "fs_name": "Game", "full_path": "psx/Game", "has_multiple_files": True,
            "fs_size_bytes": 12,
            "files": [{"id": 71, "file_name": "Game.cue", "full_path": "psx/Game/Game.cue", "file_size_bytes": 4},
                      {"id": 72, "file_name": "Game.bin", "full_path": "psx/Game/Game.bin", "file_size_bytes": 8}]}


@pytest.mark.parametrize("installed", [False, True])
def test_cancelling_a_folder_game_removes_only_the_files_it_made(isolated, installed):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    cue = os.path.join(sysdir, "Game", "Game.cue")
    if installed:                              # a repair over the app's copy already there: the cue stays
        _w(cue, b"OLD")                        # not RomM's size: downloaded again over it
        rommlib.Library().mark(_folder_rom(), "psx", [os.path.dirname(cue)])
    holder = {}

    def answer(url, h):
        if "Game.cue" in url:
            return _Resp(b"CUE!")
        return _Resp(b"B" * 8, on_chunk=holder["j"].cancel.set)    # cancelled during the second file
    d = _downloader(_Api(_folder_rom(), answer))
    j = holder["j"] = rommlib.Job(_folder_rom(), "psx")
    d.jobs.append(j)
    with pytest.raises(rommlib.Cancelled) as e:
        d._process(j)
    d._failed(j, e.value)
    assert j.state == rommlib.Job.CANCELLED
    if installed:
        assert open(cue, "rb").read() == b"CUE!"           # replaced (not created) by the job: kept
        assert os.listdir(os.path.join(sysdir, "Game")) == ["Game.cue"]
    else:
        assert not os.path.exists(os.path.join(sysdir, "Game")), "a half game left for ES to list"


@pytest.mark.parametrize("installed", [False, True])
def test_cancelling_an_archive_waiting_to_unpack_removes_it(isolated, tmp_path, installed):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    dest = os.path.join(sysdir, "Game.zip")
    os.makedirs(sysdir)
    if installed:
        _w(dest, b"old")
    body = open(_zip(str(tmp_path / "src.zip"), {"Game.cue": "c", "Game.bin": "b"}), "rb").read()
    d = _downloader(_Api(answer=lambda url, h: _Resp(body)))
    j = rommlib.Job({"id": 3, "name": "Game", "fs_name": "Game.zip"}, "psx")
    d.jobs.append(j)
    d._fetch_new(j, "/g", dest, len(body), None)
    j.state = rommlib.Job.EXTRACTING                    # handed to the post thread: "Waiting to unpack"
    d.cancel_job(j)
    with pytest.raises(rommlib.Cancelled) as e:
        d._post(j, {"kind": "file", "path": dest, "heavy": True})
    d._failed(j, e.value)
    assert j.state == rommlib.Job.CANCELLED
    assert os.path.exists(dest) == installed


def test_what_a_job_made_survives_a_restart_for_its_cancel(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    d = _downloader()
    d.persist = True
    j = rommlib.Job(_folder_rom(), "psx")
    j.made = [_w(os.path.join(sysdir, "Game", "Game.cue"), b"CUE!")]
    d.jobs.append(j)
    d.save_queue()
    d2 = _downloader()
    d2.enqueue = lambda rom, system, *a, **k: d2.jobs.append(rommlib.Job(rom, system)) or d2.jobs[-1]
    assert d2.resume() == 1
    d2.cancel_job(d2.jobs[0])
    assert not os.path.exists(os.path.join(sysdir, "Game"))


# ------------------------------------------------------------ 4. Pause all under the lock

def test_pause_all_sets_job_flags_under_the_lock(isolated):
    d = _downloader()
    seen = []

    class Ev:
        def set(self):
            seen.append(d.cv._is_owned())
    for _ in range(3):
        j = rommlib.Job({"id": 1}, "psx")
        j.state, j.cancel = rommlib.Job.DOWNLOADING, Ev()
        d.jobs.append(j)
    d.pause_all()
    assert seen == [True, True, True]
    assert all(j.pause and not j.resume for j in d.jobs)


# ------------------------------------------------------------ downloads that explain themselves

def test_a_rejected_token_puts_the_whole_queue_on_hold_once(isolated):
    """RomM turns the token away mid-queue: the game goes back in the queue (not Failed), every
    other download waits too, and one line says to pair again; Resume all clears it."""
    api = _Api(answer=lambda url, h: _Resp(b"", 401))
    d = _downloader(api)
    j, k = rommlib.Job({"id": 1, "name": "G"}, "gba"), rommlib.Job({"id": 2, "name": "H"}, "gba")
    d.jobs = [j, k]
    dest = os.path.join(rommlib.ROMS_ROOT, "gba", "G.gba")
    os.makedirs(os.path.dirname(dest))
    with pytest.raises(rommlib.AuthRejected) as e:
        d._fetch(j, "/g", dest, 20, {"file_name": "G.gba", "file_size_bytes": 20})
    d._failed(j, e.value)
    assert j.state == rommlib.Job.QUEUED and d.hold and "pair again" in d.hold_why
    assert [x.state for x in d.jobs] == [rommlib.Job.QUEUED, rommlib.Job.QUEUED]      # nothing failed
    d.save_queue = lambda: None
    d.release_all()
    assert not d.hold and d.hold_why == ""


def test_a_transfer_with_nothing_arriving_is_stalled_not_fast():
    j = rommlib.Job({"id": 1, "name": "G"}, "gba")
    j.state, j.speed, j.last_data = rommlib.Job.DOWNLOADING, 5e6, 1000.0
    assert j.stalled(now=1005.0) == 0                       # a pause in the stream: still its speed
    assert j.stalled(now=1042.0) == 42.0
    j.state = rommlib.Job.EXTRACTING
    assert j.stalled(now=1042.0) == 0                       # only a transfer stalls


def test_the_speed_limit_is_the_lower_of_the_two(isolated):
    d = _downloader()
    assert d.speed_limit() == 0
    d.cfg["download_speed"] = 10
    assert d.speed_limit() == 10e6
    d.rate_limit = 2e6                                      # a game is running, capped lower
    assert d.speed_limit() == 2e6
    d.cfg["download_speed"], d.rate_limit = 1, 5e6
    assert d.speed_limit() == 1e6
    d.cfg["download_speed"] = "junk"
    assert d.speed_limit() == 5e6
    assert rommlib.DEFAULT_CONFIG["download_speed"] == 0
