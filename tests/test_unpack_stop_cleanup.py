"""Stopping an unpack and cleaning up after one: a 7z never outlives a pause or
the app, a stopped unpack's archive isn't downloaded again, a half-moved game a
cancel leaves goes with it (or is offered in Free up space), move markers are
one per job, Free up space's delete counts what went when installed.json can't
be updated, and installed.json.bak is copied at most once a minute where it
can't be a hard link."""
import json
import os
import shutil
import stat
import threading
import time
import zipfile

import pytest

import rommlib
from tests.test_downloader_fixes import _Api, _Resp, _downloader, _w


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _fake_7z(where):
    """A 7z that writes its pid, then runs until it's killed."""
    p = os.path.join(where, "fake7z")
    pidfile = os.path.join(where, "7z.pid")
    with open(p, "w") as f:
        f.write("#!/bin/sh\necho $$ > %s\nexec sleep 30\n" % pidfile)
    os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
    return p, pidfile


# ------------------------------------------------------------ 1. a 7z never outlives a pause

@pytest.mark.parametrize("how", ["pause_all", "hold_all", "cancel_job", "pause_job"])
def test_stopping_a_7z_unpack_kills_7z_before_it_returns(isolated, tmp_path, how):
    """The app closing calls pause_all and then exits: the unpack's thread dies
    with it, so a 7z still running then would go on writing into a stage the
    next attempt (rb_dl) sweeps as abandoned, beside a second 7z."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    arc = _w(os.path.join(sysdir, "Game.7z"), b"7z" * 10)
    fake, pidfile = _fake_7z(str(tmp_path))
    d = _downloader()
    d._7z = lambda: fake
    j = rommlib.Job({"id": 4, "name": "Game", "fs_name": "Game.7z"}, "psx")
    j.state = rommlib.Job.EXTRACTING
    d.jobs.append(j)
    stage = rommlib.unpack_stage(sysdir)
    os.makedirs(stage)
    got = []

    def run():
        try:
            d._run_7z(arc, stage, j)
        except Exception as e:
            got.append(e)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    for _ in range(200):
        if os.path.exists(pidfile) and open(pidfile).read().strip():
            break
        time.sleep(0.02)
    pid = int(open(pidfile).read())
    assert _alive(pid)
    getattr(d, how)(j) if how in ("cancel_job", "pause_job") else getattr(d, how)()
    assert not _alive(pid), "7z still running after %s returned" % how
    t.join(5)
    assert got and isinstance(got[0], rommlib.Cancelled), got      # a pause, not "7z extract failed"
    assert j.proc is None


def test_a_7z_never_starts_once_its_job_is_stopped(isolated, tmp_path):
    """Stopped between the unpack's last look at the cancel and starting 7z:
    _run_7z checks it under the same lock _stop_unpacks looks for the 7z under."""
    fake, pidfile = _fake_7z(str(tmp_path))
    d = _downloader()
    d._7z = lambda: fake
    j = rommlib.Job({"id": 4, "name": "Game"}, "psx")
    j.cancel.set()
    with pytest.raises(rommlib.Cancelled):
        d._run_7z(str(tmp_path / "Game.7z"), str(tmp_path / "stage"), j)
    assert not os.path.exists(pidfile)


# ------------------------------------------------------------ 2. a stopped unpack's archive is kept

def _seven_zip_detail(size=1000):
    return {"id": 7, "name": "Game", "fs_name": "Game.7z", "fs_name_no_ext": "Game", "fs_size_bytes": size,
            "files": [{"id": 1, "file_name": "Game.7z", "file_size_bytes": size, "category": None,
                       "full_path": "psx/Game.7z"}]}


@pytest.mark.parametrize("made", [True, False])
def test_a_stopped_7z_unpack_is_not_downloaded_again(isolated, monkeypatch, made):
    """Pause all, closing, the hand-over to rb_dl and an update all stop an unpack
    and leave the whole archive: RomM can't vouch for a 7z's bytes (it hashes what's
    inside), so it used to be downloaded again from 0. The job's own download (in
    j.made, saved with the queue) is reused; one it didn't download still isn't."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    body = b"7z\xbc\xaf\x27\x1c" + b"x" * 994
    dest = _w(os.path.join(sysdir, "Game.7z"), body)
    detail = _seven_zip_detail()
    api = _Api(detail, answer=lambda url, h: _Resp(body, 200))
    d = _downloader(api)
    monkeypatch.setattr(d, "_free_bytes", lambda p: 1 << 40)
    monkeypatch.setattr(d, "_will_extract", lambda j, p: True)
    j = rommlib.Job(dict(detail), "psx")
    if not made:                       # not the app's: never reused, written over or deleted
        with pytest.raises(rommlib.RommError, match="wasn't downloaded by RomM-o-Cera"):
            d._process(j)
        assert open(dest, "rb").read() == body and api.asked == []
        return
    j.made = [dest]
    post = d._process(j)
    assert post["path"] == dest
    assert api.asked == [], api.asked


def test_the_space_check_counts_a_kept_archive(isolated, monkeypatch):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    body = b"x" * 1000
    dest = _w(os.path.join(sysdir, "Game.7z"), body)
    detail = _seven_zip_detail()
    d = _downloader(_Api(detail, answer=lambda url, h: _Resp(body, 200)))
    monkeypatch.setattr(d, "_will_extract", lambda j, p: True)
    reserve = rommlib.keep_free_bytes(d.cfg) or rommlib.SPACE_MARGIN
    rest = rommlib.space_needed(dict(detail), "psx") - len(body)        # the unpacked copy only
    monkeypatch.setattr(d, "_free_bytes", lambda p: rest + reserve)
    j = rommlib.Job(dict(detail), "psx")
    j.made = [dest]
    d._process(j)                                                        # no NoSpace: the archive is here
    j2 = rommlib.Job(dict(detail), "psx")                               # not the job's own: fetched again
    with pytest.raises(rommlib.NoSpace):
        d._process(j2)


def test_a_zip_the_job_downloaded_is_not_tested_again(isolated, monkeypatch, tmp_path):
    """A zip's full re-test (every member's CRC) reads it all: not for the job's own
    download, which was checked when it came in."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    dest = os.path.join(sysdir, "Game.zip")
    os.makedirs(sysdir)
    with zipfile.ZipFile(dest, "w") as z:
        z.writestr("Game.bin", b"bin")
    size = os.path.getsize(dest)
    detail = {"id": 8, "name": "Game", "fs_name": "Game.zip", "fs_name_no_ext": "Game", "fs_size_bytes": size,
              "files": [{"id": 1, "file_name": "Game.zip", "file_size_bytes": size, "category": None,
                         "archive_members": [{"name": "Game.bin", "size": 3}]}]}
    api = _Api(detail, answer=lambda url, h: _Resp(b"", 500))
    d = _downloader(api)
    monkeypatch.setattr(d, "_free_bytes", lambda p: 1 << 40)
    monkeypatch.setattr(d, "_will_extract", lambda j, p: True)

    def boom(*a, **k):
        raise AssertionError("tested again")
    monkeypatch.setattr(rommlib, "zip_matches", boom)
    j = rommlib.Job(dict(detail), "psx")
    j.made = [dest]
    d._process(j)
    assert api.asked == []


# ------------------------------------------------------------ 3. a half-moved game after a cancel

def _half_moved(sysdir, rom_id=3):
    """An unpack of Game.zip killed while moving its files into roms/psx/Game/."""
    arc = _w(os.path.join(sysdir, "Game.zip"), b"zip")
    lands = os.path.join(sysdir, "Game")
    _w(os.path.join(lands, "Game.cue"), b"cue")                          # the .bin never got there
    marker = rommlib.write_move_marker(sysdir, lands, arc, rom_id)
    return arc, lands, marker


@pytest.mark.parametrize("case", ["cancel", "remove", "recorded", "other game"])
def test_cancelling_a_job_killed_mid_move_removes_the_half_moved_game(isolated, case):
    """Left behind, the half-moved folder was listed by ES and "installed" (Play
    started a broken game), and with its archive gone nothing finished or offered it."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    arc, lands, marker = _half_moved(sysdir, rom_id=99 if case == "other game" else 3)
    d = _downloader()
    if case == "recorded":                                               # a repair over the installed copy
        d.library.update(lambda inst: inst.update({"3": {"system": "psx", "paths": [lands]}}))
    j = rommlib.Job({"id": 3, "name": "Game", "fs_name": "Game.zip"}, "psx")
    j.made = [arc]
    j.state = rommlib.Job.PAUSED
    d.jobs.append(j)
    if case == "remove":
        d.remove(j)
    else:
        d.cancel_job(j)
    assert not os.path.exists(arc)
    gone = case in ("cancel", "remove")
    assert os.path.exists(lands) != gone and os.path.exists(marker) != gone


def test_a_half_moved_game_with_nothing_to_finish_it_is_offered(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    arc, lands, marker = _half_moved(sysdir)
    os.remove(arc)                                                       # e.g. cancelled by an older version
    rom = {"id": 3, "fs_name": "Game.zip"}
    assert not [x for x in rommlib.find_leftovers([(rom, "psx")], chd_busy=False) if x["path"] == lands], \
        "a pending download of it may still unpack it"
    found = [x for x in rommlib.find_leftovers(chd_busy=False) if x["path"] == lands]
    assert [x["kind"] for x in found] == ["Unfinished unpack"] and found[0]["size"] == 3
    assert rommlib.delete_leftovers(found) == 3
    assert not os.path.exists(lands) and not os.path.exists(marker)


@pytest.mark.parametrize("why", ["recorded", "moved", "archive here", "unreadable"])
def test_a_marker_never_offers_a_game_that_may_be_whole(isolated, why):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    arc, lands, marker = _half_moved(sysdir)
    if why != "archive here":
        os.remove(arc)
    if why == "recorded":
        rommlib.Library().update(lambda inst: inst.update({"3": {"system": "psx", "paths": [lands]}}))
    elif why == "moved":                                                 # whole, only not recorded yet
        rommlib.mark_moved(marker)
    elif why == "unreadable":
        os.makedirs(rommlib.DATA_DIR, exist_ok=True)
        os.makedirs(rommlib.STATE_PATH)                                  # can't be opened as a file
    assert not [x for x in rommlib.find_leftovers(chd_busy=False) if x["path"] == lands]


def test_a_finished_move_marks_its_marker_moved(isolated, tmp_path):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    src = _w(str(tmp_path / "stage" / "Game.bin"), b"b")
    lands = os.path.join(sysdir, "Game.bin")
    os.makedirs(sysdir)
    d = _downloader()
    j = rommlib.Job({"id": 5, "name": "Game"}, "psx")
    d._move_into_place(j, [(src, lands)], os.path.join(sysdir, "Game.zip"), lands)
    m = json.load(open(j.move_markers[-1]))
    assert m["moved"] is True and m["lands"] == lands and os.path.exists(lands)


# ------------------------------------------------------------ 5. one marker per job

def test_two_jobs_in_one_thread_get_their_own_markers(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    os.makedirs(sysdir)
    a = rommlib.write_move_marker(sysdir, os.path.join(sysdir, "A"), os.path.join(sysdir, "A.zip"), 1)
    b = rommlib.write_move_marker(sysdir, os.path.join(sysdir, "B"), os.path.join(sysdir, "B.zip"), 2)
    assert a != b, "B's marker overwrote A's"
    assert list(rommlib.move_markers(sysdir, os.path.join(sysdir, "A.zip"), 1)) == [a]
    assert list(rommlib.move_markers(sysdir, os.path.join(sysdir, "B.zip"), 2)) == [b]


def test_an_old_marker_is_still_read(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    old = os.path.join(sysdir, ".rb-moving-123-456")                    # as the previous release named it
    _w(old, json.dumps({"lands": os.path.join(sysdir, "A"), "archive": os.path.join(sysdir, "A.zip"),
                        "rom": "1", "when": 1.0}).encode())
    assert rommlib.move_markers(sysdir, os.path.join(sysdir, "A.zip"), 1) == {old: os.path.join(sysdir, "A")}


# ------------------------------------------------------------ 4. Free up space with installed.json unreadable

def test_free_up_space_counts_a_delete_installed_json_cant_record(isolated, monkeypatch):
    from rbui.screens.installed import delete_outcome
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    g = _w(os.path.join(sysdir, "Game.iso"), b"x" * 5000)
    lib = rommlib.Library()
    lib.mark({"id": 1, "name": "Game", "fs_name": "Game.iso"}, "psx", [g])      # the app's download

    def unreadable(self, fn):
        raise rommlib.LibraryUnreadable("installed.json can't be read right now, so nothing was recorded; try again")
    monkeypatch.setattr(rommlib.Library, "update", unreadable)
    with pytest.raises(rommlib.LibraryUnreadable) as ei:
        rommlib.delete_game("psx", g, lib)
    e = ei.value
    assert not os.path.exists(g)
    assert e.paths == [g] and e.freed == 5000 and not e.failed
    assert delete_outcome("Game", e)[0], "counted as not deleted"
    q = [json.loads(line) for line in open(rommlib.GAMELIST_QUEUE)]
    assert q[-1] == {"system": "psx", "remove": [g], "keep_media": True}


def test_free_up_space_deletes_the_rest_when_one_file_wont_go(isolated, monkeypatch):
    from rbui.screens.installed import delete_outcome
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    cue = _w(os.path.join(sysdir, "Game.cue"), b'FILE "Game (Track 1).bin" BINARY\nFILE "Game (Track 2).bin" BINARY\n')
    t1 = _w(os.path.join(sysdir, "Game (Track 1).bin"), b"1" * 100)
    t2 = _w(os.path.join(sysdir, "Game (Track 2).bin"), b"2" * 100)
    cue_size = os.path.getsize(cue)
    rommlib.Library().mark({"id": 1, "name": "Game", "fs_name": "Game.cue"}, "psx", [cue, t1, t2])
    real = os.remove

    def remove(p, *a, **k):
        if p == t1:
            raise PermissionError(13, "Permission denied", p)
        return real(p, *a, **k)
    monkeypatch.setattr(os, "remove", remove)
    with pytest.raises(rommlib.DeleteIncomplete) as ei:
        rommlib.delete_game("psx", cue, rommlib.Library())
    e = ei.value
    assert os.path.exists(t1) and not os.path.exists(t2) and not os.path.exists(cue)
    assert sorted(e.paths) == sorted([cue, t2]) and [f for f, _ in e.failed] == [t1]
    assert e.freed == cue_size + 100
    went, note = delete_outcome("Game", e)
    assert not went and "Game (Track 1).bin" in note
    q = [json.loads(line) for line in open(rommlib.GAMELIST_QUEUE)]
    assert sorted(q[-1]["remove"]) == sorted([cue, t2])


# ------------------------------------------------------------ 6. installed.json.bak without hard links

def test_bak_is_copied_at_most_once_a_minute(isolated, monkeypatch):
    """.bak is a real copy (never a hard link: damage in place would take both), at
    most once a minute: a batch of 200 deletes doesn't copy the file 200 times."""
    copies = []
    real = shutil.copyfile

    def copy(a, b, *x, **k):
        if a == rommlib.STATE_PATH:
            copies.append(b)
        return real(a, b, *x, **k)
    monkeypatch.setattr(shutil, "copyfile", copy)
    lib = rommlib.Library()
    lib.update(lambda inst: inst.update({"1": {"system": "psx", "paths": ["/a"]}}))     # the first write: copied
    for n in range(2, 12):                                               # a batch of deletes, say
        lib.update(lambda inst: inst.update({str(n): {"system": "psx", "paths": ["/b"]}}))
    assert len(copies) == 1
    bak = rommlib.STATE_PATH + ".bak"
    assert set(json.load(open(bak))) == {"1"}
    assert os.stat(bak).st_ino != os.stat(rommlib.STATE_PATH).st_ino     # its own copy, not a link
    t = time.time() - rommlib.BAK_COPY_EVERY - 1
    os.utime(bak, (t, t))
    lib.update(lambda inst: inst.pop("1"))
    assert len(copies) == 2 and len(json.load(open(bak))) == 10           # the state after that write


# ------------------------------------------------------------ 7. round 5

def test_a_job_waiting_for_space_doesnt_hash_the_file_there_each_time(isolated, monkeypatch):
    """A repair in place (same name, same size, damaged) waiting for space: every
    SPACE_RECHECK the whole file there was hashed before the space check said no."""
    import hashlib
    body = b"X" * 1000
    dest = _w(os.path.join(rommlib.ROMS_ROOT, "psx", "G.iso"), body)
    good = {"id": 1, "name": "G", "fs_name": "G.iso", "fs_size_bytes": 1000,
            "files": [{"file_name": "G.iso", "file_size_bytes": 1000, "sha1_hash": hashlib.sha1(body).hexdigest()}]}
    rommlib.Library().mark(good, "psx", [dest])                         # the app's own copy
    api = _Api(good, answer=lambda url, h: _Resp(body, 200))
    d = _downloader(api)
    calls = []
    real = rommlib.file_hash_ok
    monkeypatch.setattr(rommlib, "file_hash_ok", lambda *a, **k: (calls.append(a[0]), real(*a, **k))[1])
    monkeypatch.setattr(d, "_free_bytes", lambda p: 10)
    j = rommlib.Job(dict(good), "psx")
    for _ in range(3):
        with pytest.raises(rommlib.NoSpace):
            d._process(j)
    assert not calls, "hashed while waiting for space"
    monkeypatch.setattr(d, "_free_bytes", lambda p: 1 << 40)
    d._process(j)                                                        # space now: RomM's checksum still vouches
    assert calls == [dest] and api.asked == [] and j.verified is True


def _running(pid):
    """Alive and not a zombie (an orphan nobody reaps: in Docker that's pytest, pid 1)."""
    try:
        with open("/proc/%d/stat" % pid) as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def _bad_crc_zip(path):
    """A zip whose listing reads fine but whose disc image fails its CRC."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
        z.writestr("Game.cue", b'FILE "Game.bin" BINARY\n')
        z.writestr("Game.bin", b"B" * 4000)
    data = bytearray(open(path, "rb").read())
    i = data.index(b"B" * 4000)
    data[i + 100] ^= 0xFF
    open(path, "wb").write(bytes(data))
    return path


@pytest.mark.parametrize("made", [True, False])
def test_an_archive_that_wont_unpack_is_downloaded_again_on_retry(isolated, made):
    """It used to stay in j.made: Retry reused it on its size and failed the same
    way, forever. The job's own copy goes; one that was there before stays."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    arc = _bad_crc_zip(os.path.join(sysdir, "Game.zip"))
    d = _downloader()
    j = rommlib.Job({"id": 9, "name": "Game", "fs_name": "Game.zip", "fs_name_no_ext": "Game"}, "psx")
    if made:
        j.made, j.checked = [arc], [arc]
    with pytest.raises(rommlib.DamagedArchive):
        d._post(j, {"kind": "file", "path": arc, "heavy": True})
    assert os.path.exists(arc) != made
    assert j.made == [] if made else True
    assert not [n for n in os.listdir(sysdir) if n.startswith((rommlib.UNPACK_STAGE, rommlib.MOVE_MARKER))]
    d._failed(j, rommlib.DamagedArchive("x"))
    assert j.state == rommlib.Job.FAILED


def test_a_7z_that_fails_is_a_damaged_archive(isolated, tmp_path):
    fake = tmp_path / "fail7z"
    fake.write_text("#!/bin/sh\nexit 2\n")
    os.chmod(fake, 0o755)
    d = _downloader()
    d._7z = lambda: str(fake)
    j = rommlib.Job({"id": 4, "name": "Game"}, "psx")
    os.makedirs(tmp_path / "stage")
    with pytest.raises(rommlib.DamagedArchive):
        d._run_7z(str(tmp_path / "Game.7z"), str(tmp_path / "stage"), j)


def test_stopping_a_7z_wrapper_kills_what_it_started(isolated, tmp_path):
    """A 7z that is a shell script (it doesn't exec): killing only the script left
    the real unpacker running and writing into the stage."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    arc = _w(os.path.join(sysdir, "Game.7z"), b"7z" * 10)
    p = tmp_path / "wrap7z"
    pidfile = tmp_path / "child.pid"
    p.write_text("#!/bin/sh\nsleep 30 &\necho $! > %s\nwait\n" % pidfile)
    os.chmod(p, 0o755)
    d = _downloader()
    d._7z = lambda: str(p)
    j = rommlib.Job({"id": 4, "name": "Game", "fs_name": "Game.7z"}, "psx")
    j.state = rommlib.Job.EXTRACTING
    d.jobs.append(j)
    stage = rommlib.unpack_stage(sysdir)
    os.makedirs(stage)
    t = threading.Thread(target=lambda: pytest.raises(rommlib.Cancelled, d._run_7z, arc, stage, j), daemon=True)
    t.start()
    for _ in range(200):
        if pidfile.exists() and pidfile.read_text().strip():
            break
        time.sleep(0.02)
    child = int(pidfile.read_text())
    assert _running(child)
    d.pause_all()
    for _ in range(100):
        if not _running(child):
            break
        time.sleep(0.02)
    assert not _running(child), "the wrapper's child outlived the pause"
    t.join(5)


def test_a_cancel_keeps_a_half_moved_folder_filled_by_hand_since(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    arc, lands, marker = _half_moved(sysdir)
    mine = _w(os.path.join(lands, "Game.bin"), b"bin")                  # copied in over the network since
    later = json.load(open(marker))["when"] + 60
    os.utime(mine, (later, later))
    d = _downloader()
    j = rommlib.Job({"id": 3, "name": "Game", "fs_name": "Game.zip"}, "psx")
    j.made = [arc]
    j.state = rommlib.Job.PAUSED
    d.jobs.append(j)
    d.cancel_job(j)
    assert not os.path.exists(arc)
    assert os.path.exists(mine) and os.path.exists(marker)
    found = [x for x in rommlib.find_leftovers(chd_busy=False) if x["path"] == lands]
    assert [x["kind"] for x in found] == ["Unfinished unpack"]          # offered, deleted when asked
    assert rommlib.delete_leftovers(found) == 6
    assert not os.path.exists(lands) and not os.path.exists(marker)


def test_an_archive_reused_after_a_pause_still_says_checksum_ok(isolated, monkeypatch):
    """Its own download, checked against RomM's checksum when it came in, reused after
    a stopped unpack (and a restart: saved with the queue) said only "Ready to play"."""
    import hashlib
    body = b"x" * 1000
    detail = _seven_zip_detail()
    detail.update(fs_name="Game.iso")
    detail["files"][0].update(file_name="Game.iso", sha1_hash=hashlib.sha1(body).hexdigest())
    api = _Api(detail, answer=lambda url, h: _Resp(body, 200))
    d = _downloader(api)
    d.persist = True
    monkeypatch.setattr(d, "_free_bytes", lambda p: 1 << 40)
    monkeypatch.setattr(rommlib, "file_hash_ok", lambda *a, **k: (True, "sha1") if a[0].endswith(".part")
                        else pytest.fail("hashed again"))
    j = rommlib.Job(dict(detail), "psx")
    j.state = rommlib.Job.QUEUED
    d.jobs.append(j)
    dest = d._process(j)["path"]
    assert j.verified is True and j.made == [dest] and j.checked == [dest] and len(api.asked) == 1
    d.save_queue()
    assert json.load(open(rommlib.QUEUE_PATH))["jobs"][0]["checked"] == [dest]
    j2 = rommlib.Job(dict(detail), "psx")                               # as resume() rebuilds it
    j2.made, j2.checked = [dest], [dest]
    d._process(j2)
    assert len(api.asked) == 1 and j2.verified is True
