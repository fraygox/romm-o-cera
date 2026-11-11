"""Check games: what it calls damaged is damaged, one bad file never ends the
check, a quick check doesn't wipe out a full one, and B stops the work."""
import errno
import os
import subprocess
import sys
import threading
import time
import zipfile
import zlib

import pytest

import rommlib
from conftest import whole_chd
from test_review_batch import _drawn, _sync


class Api:
    """RomM: games = [(platform id, fs_name, files, extra fields)]; ids are the list positions."""

    def __init__(self, games, broken=()):
        self.games = []
        for i, g in enumerate(games):
            pid, name, files = g[:3]
            r = {"id": i, "name": rommlib.stem_of(name), "fs_name": name, "fs_name_no_ext": rommlib.stem_of(name),
                 "platform_id": pid, "files": files}
            r.update(g[3] if len(g) > 3 else {})
            self.games.append(r)
        self.broken = set(broken)
        self.searched = []

    def roms_all(self, platform_ids=None, keep=None, **kw):
        return [r for r in self.games if r["platform_id"] in platform_ids and (keep is None or keep(r))]

    def rom(self, rid):
        if rid in self.broken:
            raise rommlib.RommError("RomM error HTTP 502")
        return dict(self.games[rid])

    def roms(self, search=None, **kw):
        self.searched.append(search)
        return {"items": [r for r in self.games if rommlib.strip_tags(r["fs_name_no_ext"]) == search.lower()]}

    def by_hash(self, **kw):
        return None


def _put(system, rel, data=b""):
    p = os.path.join(rommlib.ROMS_ROOT, system, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    return p


def _rec(name, size, **kw):
    return dict({"file_name": name, "file_size_bytes": size, "category": None}, **kw)


def _run(api, by_sys, **kw):
    findings, stats = rommlib.audit(api, by_sys, **kw)
    return {os.path.basename(f["path"]): f for f in findings}, stats


@pytest.fixture
def no_es(monkeypatch):
    monkeypatch.setattr(rommlib, "es_hashes", lambda system: {})


# ------------------------------------------------------------ 1. RomM not answering isn't damage

def test_a_record_romm_didnt_send_is_not_verified_never_damaged(isolated, no_es):
    _put("gba", "Fine Cart (USA).gba", b"x" * 64)
    _put("gba", "Silent Cart (USA).gba", b"x" * 64)
    api = Api([(1, "Fine Cart (USA).gba", [_rec("Fine Cart (USA).gba", 64)]),
               (1, "Silent Cart (USA).gba", [_rec("Silent Cart (USA).gba", 64)])], broken={1})
    got, stats = _run(api, {"gba": [1]})
    assert got == {}
    assert stats["unverified"] == 1 and stats["unverified_tools"] == ["RomM"] and stats["unverified_by"] == {"RomM": 1}
    assert api.searched == []                    # never matched to another title either


# ------------------------------------------------------------ 2. one unreadable file never ends the check

def _corrupt_deflate(path, member="Game.gba"):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(member, b"abcdefgh" * 50000)
    data = bytearray(open(path, "rb").read())
    data[30 + len(member) + 2] ^= 0xFF           # the first block's code lengths, after the local header
    open(path, "wb").write(bytes(data))


def test_a_corrupt_deflate_stream_is_damaged_and_the_check_goes_on(isolated, no_es):
    z = _put("gba", "Broken Zip (USA).zip")
    _corrupt_deflate(z)
    with zipfile.ZipFile(z) as zz:
        with pytest.raises(zlib.error):           # not an OSError: it used to end the whole check
            zz.testzip()
    _put("gba", "Zz Cart (USA).gba", b"y" * 10)          # checked after the zip
    info = zipfile.ZipFile(z).infolist()[0]
    api = Api([(1, "Broken Zip (USA).zip", [_rec("Broken Zip (USA).zip", os.path.getsize(z), archive_members=[
        {"name": "Game.gba", "size": info.file_size, "crc_hash": "%08x" % info.CRC}])]),
        (1, "Zz Cart (USA).gba", [_rec("Zz Cart (USA).gba", 20)])])
    got, stats = _run(api, {"gba": [1]}, deep=True)
    assert got["Broken Zip (USA).zip"]["problem"].startswith("Damaged zip")
    assert got["Broken Zip (USA).zip"]["severity"] == "damaged" and got["Broken Zip (USA).zip"]["deep"]
    assert got["Zz Cart (USA).gba"]["problem"].startswith("Smaller")
    assert stats["checked"] == 2
    # the downloader's own check says so too, instead of raising
    assert rommlib.zip_matches(z, [{"name": "Game.gba"}]) == (False, "zip")


def test_an_io_error_is_its_own_finding_and_not_in_the_bulk_download(isolated, no_es, monkeypatch, rb):
    monkeypatch.setattr(rb.AuditScreen, "ours", lambda self, f: True)       # findings in the app's downloads
    bad = _put("gba", "Sector Cart (USA).gba", b"x" * 64)
    _put("gba", "Zz Cart (USA).gba", b"y" * 10)
    real = os.path.getsize

    def getsize(p):
        if p == bad:
            raise OSError(errno.EIO, "Input/output error")
        return real(p)
    monkeypatch.setattr(os.path, "getsize", getsize)
    api = Api([(1, "Sector Cart (USA).gba", [_rec("Sector Cart (USA).gba", 64)]),
               (1, "Zz Cart (USA).gba", [_rec("Zz Cart (USA).gba", 20)])])
    got, _ = _run(api, {"gba": [1]})
    f = got["Sector Cart (USA).gba"]
    assert f["problem"] == "Can't be read (I/O error)" and f["io_error"] and f["rom"]["id"] == 0
    assert "Zz Cart (USA).gba" in got
    scr = rb.AuditScreen.__new__(rb.AuditScreen)
    scr.findings, scr.queued = list(got.values()), set()
    assert [x["path"] for x in scr.redownloadable()] == [got["Zz Cart (USA).gba"]["path"]]


def test_a_zip_python_cant_unpack_is_not_verified(isolated, no_es, monkeypatch):
    z = _put("gba", "Odd Method (USA).zip")
    with zipfile.ZipFile(z, "w") as zz:
        zz.writestr("Game.gba", b"x" * 100)
    def deflate64(self, *a, **k):
        raise NotImplementedError("That compression method is not supported")
    monkeypatch.setattr(zipfile.ZipFile, "open", deflate64)
    api = Api([(1, "Odd Method (USA).zip", [_rec("Odd Method (USA).zip", 100, archive_members=[
        {"name": "Game.gba", "size": 100}])])])
    got, stats = _run(api, {"gba": [1]}, deep=True)
    assert got == {} and stats["unverified_tools"] == ["zip"]


# ------------------------------------------------------------ 3. a quick check keeps the full check's findings

def test_a_quick_check_keeps_what_only_the_full_check_finds(isolated):
    a = _put("psx", "Alpha (USA).chd", b"a" * 10)
    b = _put("psx", "Beta Run (USA).chd", b"b" * 10)
    rommlib.save_audit([{"system": "psx", "path": a, "severity": "damaged", "problem": "CHD failed chdman verify",
                         "rom": None, "deep": True},
                        {"system": "psx", "path": b, "severity": "damaged", "problem": "Checksum doesn't match RomM",
                         "rom": None, "deep": True}], {"checked": 2}, True)
    full_time = rommlib.load_audit()["time"]
    open(b, "wb").write(b"fixed and longer")            # replaced since: its finding goes
    rommlib.save_audit([], {"checked": 2}, False)
    d = rommlib.load_audit()
    assert not d["deep"] and [(os.path.basename(f["path"]), f["full_time"]) for f in d["findings"]] == \
        [("Alpha (USA).chd", full_time)]
    rommlib.save_audit([], {"checked": 2}, False)       # and the next quick check still keeps it
    assert [f["full_time"] for f in rommlib.load_audit()["findings"]] == [full_time]
    # one the quick check found itself is the quick check's
    rommlib.save_audit([{"system": "psx", "path": a, "severity": "damaged", "problem": "Incomplete", "rom": None}],
                       {"checked": 2}, False)
    assert [f["problem"] for f in rommlib.load_audit()["findings"]] == ["Incomplete"]


def _app(rb, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    app = rb.App(None, headless=True)
    app.bg = lambda fn, done=None, err=None: _sync(fn, done, err, app)
    return app


def test_check_games_says_which_check_found_what(isolated, rb, monkeypatch):
    app = _app(rb, monkeypatch)
    a = _put("psx", "Alpha (USA).chd", b"a" * 10)
    rommlib.save_audit([{"system": "psx", "path": a, "severity": "damaged", "problem": "CHD failed chdman verify",
                         "rom": None, "deep": True}], {"checked": 2}, True)
    rommlib.save_audit([], {"checked": 2, "unmatched": 0}, False)
    ck = rb.CheckScreen(app)
    app.push(ck)
    seen = _drawn(rb, monkeypatch, ck)
    assert "1 from the full check of" in seen and "Quick check" in seen
    os.remove(rommlib.audit_path())
    rommlib.save_audit([], {"checked": 2, "unmatched": 0}, False)
    ck.enter()
    seen = _drawn(rb, monkeypatch, ck)
    assert "Nothing wrong found" in seen and "Every game here looks right" not in seen


# ------------------------------------------------------------ 4. what wasn't verified is said

def test_check_games_shows_what_it_couldnt_verify_or_match(isolated, rb, monkeypatch):
    app = _app(rb, monkeypatch)
    rommlib.save_audit([], {"checked": 30, "matched": 16, "unmatched": 14, "unverified": 12,
                            "unverified_tools": ["chdman", "RomM"], "unverified_by": {"chdman": 11, "RomM": 1}}, True)
    ck = rb.CheckScreen(app)
    app.push(ck)
    seen = _drawn(rb, monkeypatch, ck)
    assert "12 games not verified (missing chdman; RomM didn't send its record for 1)" in seen
    assert "14 games not in RomM under the same file name" in seen
    ck.sel = [i for i, it in enumerate(ck.listed()) if it.get("door") == "notes"][0]
    ck.action("ok")                                     # nothing to open
    assert app.stack[-1] is ck and app.modal is None


# ------------------------------------------------------------ 5. one disc of a set converted to CHD

def _set_detail():
    return {"files": [{"category": "game", "file_name": "Set (USA).zip", "archive_members": [
        {"name": "Set (USA) (Disc 1).cue", "size": 100},
        {"name": "Set (USA) (Disc 1).bin", "size": 2352 * 200000},
        {"name": "Set (USA) (Disc 2).cue", "size": 100},
        {"name": "Set (USA) (Disc 2) (Track 1).bin", "size": 2352 * 150000},
        {"name": "Set (USA) (Disc 2) (Track 2).bin", "size": 2352 * 50000}]}]}


def test_a_chd_of_one_disc_is_compared_with_that_disc_only(isolated, monkeypatch):
    chd = _put("psx", "Set (USA)/Set (USA) (Disc 1).chd", whole_chd())

    class A:
        def rom(self, rid):
            return _set_detail()
    monkeypatch.setattr(rommlib, "_chd_frames", lambda p, c: ("cd", 200000))
    assert rommlib._chd_vs_romm(A(), chd, {"id": 1}, "chdman") is False
    two = _put("psx", "Set (USA)/Set (USA) (Disc 2).chd", whole_chd())
    assert rommlib._chd_vs_romm(A(), two, {"id": 1}, "chdman") is False
    monkeypatch.setattr(rommlib, "_chd_frames", lambda p, c: ("cd", 100000))
    assert rommlib._chd_vs_romm(A(), two, {"id": 1}, "chdman") == (100000, 200000)
    # a CHD that can't be told apart from the set's discs: not compared, never "incomplete"
    odd = _put("psx", "Set (USA)/Whatever.chd", whole_chd())
    assert rommlib._chd_vs_romm(A(), odd, {"id": 1}, "chdman") is None


# ------------------------------------------------------------ 7. a zip with one top folder

def test_a_zip_with_one_top_folder_is_found_where_it_unpacked(isolated, no_es):
    _put("psx", "Lamp (USA)/Lamp (USA).cue", b"c" * 10)
    _put("psx", "Lamp (USA)/Lamp (USA).bin", b"b" * 20)
    api = Api([(1, "Lamp (USA).zip", [_rec("Lamp (USA).zip", 30, archive_members=[
        {"name": "Lamp (USA)/Lamp (USA).cue", "size": 10}, {"name": "Lamp (USA)/Lamp (USA).bin", "size": 20}])])])
    got, stats = _run(api, {"psx": [1]})
    assert not [k for k, f in got.items() if f["problem"].startswith("Missing file")]
    assert stats["matched"] == 1


# ------------------------------------------------------------ 8. converted discs named their own way

def test_converted_discs_in_a_folder_have_no_missing_tracks(isolated, no_es, monkeypatch):
    monkeypatch.setattr(rommlib, "_chd_frames", lambda p, c: None)
    monkeypatch.setattr(rommlib, "disc_incomplete", lambda p, c="chdman": None)
    _put("psx", "Rope (USA)/Rope.chd", whole_chd())            # named by hand, not like the tracks
    api = Api([(1, "Rope (USA).zip", [_rec("Rope (USA).zip", 30, archive_members=[
        {"name": "Rope (USA).cue", "size": 10}, {"name": "Rope (USA) (Track 1).bin", "size": 20},
        {"name": "Rope (USA) (Track 2).bin", "size": 20}])])])
    got, _ = _run(api, {"psx": [1]})
    assert got == {}
    monkeypatch.setattr(rommlib, "rvz_audit", lambda path, orig, deep: None)
    _put("gamecube", "Kite (USA)/Kite (USA).rvz", b"x")
    api = Api([(1, "Kite (USA).zip", [_rec("Kite (USA).zip", 30, archive_members=[
        {"name": "Kite (USA).iso", "size": 1 << 20}])])])
    got, _ = _run(api, {"gamecube": [1]})
    assert not [f for f in got.values() if f["problem"].startswith("Missing file")]


# ------------------------------------------------------------ 9. a beta isn't the retail game

def test_a_beta_on_the_box_is_never_matched_to_the_retail_game_by_name(isolated):
    api = Api([(1, "Moth Garden (USA).iso", [])])
    assert rommlib.find_in_romm(api, "ps2", [1], "/roms/ps2/Moth Garden (USA) (Beta).iso") == (None, None)
    assert rommlib.find_in_romm(api, "ps2", [1], "/roms/ps2/Moth Garden (USA) (Proto 2).iso") == (None, None)
    assert rommlib.find_in_romm(api, "ps2", [1], "/roms/ps2/Moth Garden (USA) (Rev 1).iso")[1] == "name"


def test_the_confirm_names_releases_matched_by_title(isolated, rb, monkeypatch):
    app = _app(rb, monkeypatch)
    scr = rb.AuditScreen.__new__(rb.AuditScreen)
    scr.app = app
    todo = [{"path": "/roms/ps2/A.iso", "system": "ps2", "severity": "damaged", "via": "name",
             "rom": {"id": 1, "fs_name": "Moth Garden (USA) (Rev 1).iso"}},
            {"path": "/roms/ps2/B.iso", "system": "ps2", "severity": "damaged", "rom": {"id": 2, "fs_name": "B.iso"}}]
    note = scr.by_name_note(todo)
    assert "1 game is matched by title only" in note and "Moth Garden (USA) (Rev 1).iso" in note
    assert "B.iso" not in note


def test_a_file_inside_a_game_folder_isnt_said_to_be_deleted(isolated, rb, monkeypatch):
    monkeypatch.setattr(rb.AuditScreen, "ours", lambda self, f: True)       # findings in the app's downloads
    app = _app(rb, monkeypatch)
    scr = rb.AuditScreen.__new__(rb.AuditScreen)
    scr.app, scr.queued = app, set()
    track = os.path.join(rommlib.ROMS_ROOT, "psx", "Lamp (USA)", "Lamp (USA) (Track 2).bin")
    scr.open_finding({"path": track, "system": "psx", "severity": "damaged", "problem": "Missing file: x",
                      "rom": {"id": 1, "name": "Lamp", "fs_name": "Lamp (USA).zip", "fs_name_no_ext": "Lamp (USA)"}})
    body = app.modal.body
    assert "deleted" not in body and "into its folder again" in body


# ------------------------------------------------------------ 10. a missing file that's back

def test_a_missing_file_finding_goes_once_the_file_is_there(isolated):
    gone = os.path.join(rommlib.ROMS_ROOT, "psx", "Game (Track 2).bin")
    rommlib.save_audit([{"system": "psx", "path": gone, "severity": "damaged",
                         "problem": "Missing file: Game (Track 2).bin", "rom": None}], {"checked": 1}, False)
    assert len(rommlib.load_audit()["findings"]) == 1
    _put("psx", "Game (Track 2).bin", b"x")
    assert rommlib.load_audit()["findings"] == []


# ------------------------------------------------------------ 11. B stops the work, not just the screen

def test_chdman_verify_is_killed_when_the_check_stops(tmp_path):
    slow = tmp_path / "chdman"
    slow.write_text("#!/bin/sh\nsleep 30\n")
    slow.chmod(0o755)
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    t0 = time.time()
    with pytest.raises(rommlib._Stopped):
        rommlib._chd_verify(str(slow), "/x.chd", cancel)
    assert time.time() - t0 < 5


def test_hashing_stops_when_the_check_stops(isolated, no_es):
    p = _put("gba", "Big Cart (USA).gba", b"x" * (12 << 20))
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(rommlib._Stopped):
        rommlib._hash_ok(p, {"sha1_hash": "0" * 40}, cancel)
    api = Api([(1, "Big Cart (USA).gba", [_rec("Big Cart (USA).gba", 12 << 20, sha1_hash="0" * 40)])])
    got, stats = _run(api, {"gba": [1]}, deep=True, cancel=cancel)
    assert got == {}
    assert rommlib._hash_ok(p, {"sha1_hash": "0" * 40}) == (False, "sha1")


def test_a_new_check_waits_for_the_stopped_one(isolated, rb, monkeypatch):
    app = _app(rb, monkeypatch)
    app.platforms = [{"id": 1, "_system": "gba"}]
    ck = rb.CheckScreen(app)
    app.push(ck)
    still = threading.Event()
    monkeypatch.setattr(rb.AuditScreen, "running", still)
    toasts = []
    monkeypatch.setattr(app, "toast", lambda msg, *a, **k: toasts.append(msg))
    ck.start(False)
    assert app.stack[-1] is ck and "still stopping" in toasts[-1]
    still.set()
    monkeypatch.setattr(rommlib, "audit", lambda *a, **k: ([], {"checked": 1, "matched": 1, "unmatched": 0}))
    ck.start(False)
    assert rb.AuditScreen.running.is_set()          # that one ran (synchronously) and finished


def test_a_stopped_check_says_so_and_keeps_the_last_result(isolated, rb, monkeypatch):
    app = _app(rb, monkeypatch)
    app.platforms = [{"id": 1, "_system": "gba"}]
    toasts = []
    monkeypatch.setattr(app, "toast", lambda msg, *a, **k: toasts.append(msg))
    saved = []
    monkeypatch.setattr(rommlib, "save_audit", lambda *a: saved.append(a))

    def audit(api, by_sys, cancel=None, **k):
        cancel.set()                                 # B pressed while it ran
        return [], {"checked": 1, "matched": 1, "unmatched": 0}
    monkeypatch.setattr(rommlib, "audit", audit)
    app.push(rb.AuditScreen(app))
    assert saved == [] and toasts[-1] == "Check stopped; nothing was saved"      # the first check: no last result
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    with open(rommlib.audit_path(), "w") as fh:
        fh.write('{"time": 1, "deep": false, "stats": {}, "findings": []}')
    app.push(rb.AuditScreen(app))
    assert saved == [] and toasts[-1] == "Check stopped; the last result is kept"


# ------------------------------------------------------------ 12. a headered cart

def test_a_cart_a_header_off_differs_but_isnt_damaged(isolated, no_es):
    _put("nes", "Pixel Fox (USA).nes", b"x" * (40960 - 16))
    _put("nes", "Short Fox (USA).nes", b"x" * 20000)
    api = Api([(1, "Pixel Fox (USA).nes", [_rec("Pixel Fox (USA).nes", 40960)]),
               (1, "Short Fox (USA).nes", [_rec("Short Fox (USA).nes", 40960)])])
    got, _ = _run(api, {"nes": [1]})
    assert got["Pixel Fox (USA).nes"]["severity"] == "differs" and "header" in got["Pixel Fox (USA).nes"]["problem"]
    assert got["Short Fox (USA).nes"]["severity"] == "damaged"


# ------------------------------------------------------------ 13. smaller ones

def test_one_name_on_two_platforms_picks_the_one_that_matches(isolated, no_es):
    _put("megadrive", "Twin Name (World).bin", b"x" * 2048)
    api = Api([(1, "Twin Name (World).bin", [_rec("Twin Name (World).bin", 4096)], {"fs_size_bytes": 4096}),
               (2, "Twin Name (World).bin", [_rec("Twin Name (World).bin", 2048)], {"fs_size_bytes": 2048})])
    got, stats = _run(api, {"megadrive": [1, 2]})
    assert got == {} and stats["matched"] == 1


def test_checked_counts_games_not_their_tracks(isolated, no_es):
    _put("psx", "Disc Here (USA).cue", b'FILE "Disc Here (USA) (Track 1).bin" BINARY\n'
                                       b'FILE "Disc Here (USA) (Track 2).bin" BINARY\n')
    _put("psx", "Disc Here (USA) (Track 1).bin", b"x")
    _put("psx", "Disc Here (USA) (Track 2).bin", b"x")
    _put("psx", "Disc Here (USA).sbi", b"x")
    _, stats = _run(Api([]), {"psx": [1]})
    assert stats["checked"] == 1 and stats["unmatched"] == 1


def test_records_with_empty_fields_dont_crash_the_check(isolated, no_es):
    _put("psx", "Holes (USA)/Holes (USA).cue", b"")
    _put("gba", "Nulls (USA).gba", b"x")
    api = Api([(1, "Holes (USA)", [{"file_name": None, "full_path": None, "category": None},
                                   {"file_name": "x", "archive_members": [{"name": None}]}],
                {"full_path": "roms/psx/Holes (USA)", "has_multiple_files": True}),
               (2, "Nulls (USA).gba", [{"file_name": None, "file_size_bytes": 1, "category": None}])])
    rommlib.audit(api, {"psx": [1], "gba": [2]})


# ------------------------------------------------------------ round 2: the drive going away ends the check

def _zipgame(system, stem, pid):
    _put(system, "%s/%s.cue" % (stem, stem), b"c" * 10)
    _put(system, "%s/%s.bin" % (stem, stem), b"b" * 20)
    return (pid, stem + ".zip", [_rec(stem + ".zip", 30, archive_members=[{"name": stem + ".cue", "size": 10},
                                                                         {"name": stem + ".bin", "size": 20}])])


def _three_folder_games():
    return [_zipgame("psx", "Aa Quest (USA)", 1), _zipgame("psx", "Bb Quest (USA)", 1),
            _zipgame("psx", "Cc Quest (USA)", 1)]


def test_a_drive_failing_mid_check_ends_it_instead_of_calling_games_missing(isolated, no_es, monkeypatch):
    games = _three_folder_games()
    _put("snes", "Later Cart (USA).sfc", b"s")
    games.append((3, "Later Cart (USA).sfc", [_rec("Later Cart (USA).sfc", 999)]))
    real_stat, real_scandir, real_listdir = os.stat, os.scandir, os.listdir
    gone = [False]

    def failing(real):
        def f(p=".", *a, **k):
            if gone[0] and str(p).startswith(rommlib.ROMS_ROOT):
                raise OSError(errno.EIO, "Input/output error")
            return real(p, *a, **k)
        return f
    monkeypatch.setattr(os, "stat", failing(real_stat))
    monkeypatch.setattr(os, "scandir", failing(real_scandir))
    monkeypatch.setattr(os, "listdir", failing(real_listdir))

    def progress(system, name):
        if name.startswith("Bb"):
            gone[0] = True
    with pytest.raises(rommlib.CheckAborted) as e:
        rommlib.audit(Api(games), {"psx": [1], "snes": [3]}, progress=progress)
    assert "stopped answering" in str(e.value) and "I/O error" in str(e.value)


def test_a_drive_swapped_mid_check_ends_it(isolated, no_es):
    games = _three_folder_games()

    def progress(system, name):
        if name.startswith("Bb") and not os.path.exists(rommlib.ROMS_ROOT + ".old"):
            os.rename(rommlib.ROMS_ROOT, rommlib.ROMS_ROOT + ".old")      # another (empty) drive at the same place
            os.makedirs(os.path.join(rommlib.ROMS_ROOT, "psx"))
    with pytest.raises(rommlib.CheckAborted) as e:
        rommlib.audit(Api(games), {"psx": [1]}, progress=progress)
    assert "changed during the check" in str(e.value) or "stopped answering" in str(e.value)


def test_a_system_folder_that_cant_be_read_ends_the_check(isolated, no_es, monkeypatch):
    _put("gba", "Fine Cart (USA).gba", b"x")
    sysdir = os.path.join(rommlib.ROMS_ROOT, "snes")
    os.makedirs(sysdir)
    real = os.listdir

    def listdir(p="."):
        if p == sysdir:
            raise PermissionError(errno.EACCES, "Permission denied")
        return real(p)
    monkeypatch.setattr(os, "listdir", listdir)
    with pytest.raises(rommlib.CheckAborted) as e:
        rommlib.audit(Api([(1, "Fine Cart (USA).gba", [_rec("Fine Cart (USA).gba", 1)])]), {"gba": [1], "snes": [2]})
    assert "roms/snes" in str(e.value) and "permission denied" in str(e.value)
    os.rmdir(sysdir)                                 # no folder for a system at all: just nothing to check there
    monkeypatch.setattr(os, "listdir", real)
    got, stats = _run(Api([(1, "Fine Cart (USA).gba", [_rec("Fine Cart (USA).gba", 1)])]), {"gba": [1], "snes": [2]})
    assert got == {} and stats["checked"] == 1


def test_a_check_ended_by_the_drive_says_so_and_saves_nothing(isolated, rb, monkeypatch):
    app = _app(rb, monkeypatch)
    app.platforms = [{"id": 1, "_system": "gba"}]
    saved = []
    monkeypatch.setattr(rommlib, "save_audit", lambda *a: saved.append(a))

    def audit(*a, **k):
        raise rommlib.CheckAborted("The drive with the games stopped answering (roms/psx: I/O error)")
    monkeypatch.setattr(rommlib, "audit", audit)
    scr = rb.AuditScreen(app)
    app.push(scr)
    assert saved == [] and "stopped answering" in scr.load_error
    assert "The check didn't finish" in _drawn(rb, monkeypatch, scr)


def test_a_game_deleted_since_the_listing_isnt_damage(isolated, no_es):
    import shutil

    def progress(system, name):
        if name.startswith("Bb"):
            shutil.rmtree(os.path.join(rommlib.ROMS_ROOT, "psx", name))
    got, stats = _run(Api(_three_folder_games()), {"psx": [1]}, progress=progress)
    assert got == {}


# ------------------------------------------------------------ round 2: one disc converted hides no other one

@pytest.fixture
def no_chdman(monkeypatch):
    monkeypatch.setattr(rommlib, "_chd_frames", lambda p, c: None)
    monkeypatch.setattr(rommlib, "disc_incomplete", lambda p, c="chdman": None)


def _two_disc_set():
    mem = [{"name": "Set Run (USA) (Disc %d).%s" % (d, e), "size": 10 if e == "cue" else 20}
           for d in (1, 2) for e in ("cue", "bin")]
    return Api([(1, "Set Run (USA).zip", [_rec("Set Run (USA).zip", 60, archive_members=mem)])])


def test_a_disc_still_missing_beside_a_converted_one_is_missing(isolated, no_es, no_chdman):
    _put("psx", "Set Run (USA)/Set Run (USA) (Disc 1).chd", whole_chd())
    got, _ = _run(_two_disc_set(), {"psx": [1]})
    assert sorted(got) == ["Set Run (USA) (Disc 2).bin", "Set Run (USA) (Disc 2).cue"]
    assert all(f["problem"].startswith("Missing file") for f in got.values())
    _put("psx", "Set Run (USA)/Set Run (USA) (Disc 2).chd", whole_chd())       # both converted: nothing missing
    got, _ = _run(_two_disc_set(), {"psx": [1]})
    assert got == {}


def test_one_disc_converted_under_another_name_is_converted(isolated, no_es, no_chdman):
    _put("psx", "Lone Disc (USA)/Lone Disc (USA) [fixed].chd", whole_chd())
    api = Api([(1, "Lone Disc (USA).zip", [_rec("Lone Disc (USA).zip", 30, archive_members=[
        {"name": "Lone Disc (USA).cue", "size": 10}, {"name": "Lone Disc (USA) (Track 1).bin", "size": 20}])])])
    got, _ = _run(api, {"psx": [1]})
    assert got == {}


def test_a_folder_game_asks_romm_for_its_record_once(isolated, no_es, no_chdman):
    _put("psx", "Solo Disc (USA)/Solo Disc (USA).chd", whole_chd())
    calls = []

    class Counting(Api):
        def rom(self, rid):
            calls.append(rid)
            return Api.rom(self, rid)
    api = Counting([(1, "Solo Disc (USA).zip", [_rec("Solo Disc (USA).zip", 30, archive_members=[
        {"name": "Solo Disc (USA).cue", "size": 10}, {"name": "Solo Disc (USA).bin", "size": 2352 * 400}])])])
    got, _ = _run(api, {"psx": [1]})
    assert got == {} and calls == [0]


# ------------------------------------------------------------ round 2: what can't be read, or went wrong

def test_a_sheet_that_cant_be_read_is_cant_be_read(isolated, no_es):
    cue = _put("psx", "Shut Disc (USA).cue", b'FILE "Gone Track.bin" BINARY\n')
    os.chmod(cue, 0)
    try:
        if os.access(cue, os.R_OK):
            pytest.skip("running as root: every file can be read")
        got, _ = _run(Api([]), {"psx": [1]})
    finally:
        os.chmod(cue, 0o644)
    f = got["Shut Disc (USA).cue"]
    assert f["problem"] == "Can't be read (permission denied)" and f["io_error"]


def test_a_bug_in_the_check_is_not_verified_not_damage_or_a_pass(isolated, no_es, monkeypatch):
    _put("gba", "Fine Cart (USA).gba", b"x")
    _put("gba", "Odd Cart (USA).gba", b"x")

    def boom(system, fp, f):
        if "Odd" in fp:
            raise RecursionError("maximum recursion depth exceeded")
        return False
    monkeypatch.setattr(rommlib, "xbox_cut", boom)
    api = Api([(1, "Fine Cart (USA).gba", [_rec("Fine Cart (USA).gba", 1)]),
               (1, "Odd Cart (USA).gba", [_rec("Odd Cart (USA).gba", 1)])])
    got, stats = _run(api, {"gba": [1]})
    assert got == {} and stats["unverified"] == 1 and stats["unverified_tools"] == [rommlib.UNVERIFIED_ERROR]
    from rbui.screens.check import unverified_line
    assert unverified_line(stats) == "1 game not verified (the check itself went wrong (in the log))"


def test_not_verified_counts_games_not_discs(isolated, no_es, no_chdman):
    mem = []
    for d in (1, 2, 3):
        _put("psx", "Trio Run (USA)/Trio Run (USA) (Disc %d).chd" % d, whole_chd())
        mem += [{"name": "Trio Run (USA) (Disc %d).cue" % d, "size": 10},
                {"name": "Trio Run (USA) (Disc %d).bin" % d, "size": 20}]
    api = Api([(1, "Trio Run (USA).zip", [_rec("Trio Run (USA).zip", 90, archive_members=mem)])])
    got, stats = _run(api, {"psx": [1]}, deep=True, chdman="/nonexistent/chdman")
    assert got == {} and stats["unverified"] == 1 and stats["unverified_by"] == {"chdman": 1}


# ------------------------------------------------------------ round 2: B (and quitting) stops every tool

def _endless(tmp_path, name):
    exe = tmp_path / name
    exe.write_text("#!/bin/sh\nwhile :; do echo xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx; done\n")
    exe.chmod(0o755)
    return str(exe)


def _stops_soon(fn):
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    t0 = time.time()
    with pytest.raises(rommlib.CheckStopped):
        fn(cancel)
    assert time.time() - t0 < 5


def test_b_stops_dolphin_tool_and_wuatool(isolated, monkeypatch, tmp_path):
    p = _put("wii", "Big Disc (USA).rvz", b"x")
    monkeypatch.setattr(rommlib, "rvz_header", lambda path: {"size": 1, "complete": True})
    monkeypatch.setattr(rommlib, "dolphin_tool_path", lambda: _endless(tmp_path, "dolphin-tool"))
    _stops_soon(lambda cancel: rommlib.rvz_audit(p, None, True, cancel=cancel))
    w = _put("wiiu", "Big Disc (USA).wux", b"x")
    monkeypatch.setattr(rommlib, "wux_layout", lambda path: 1)
    monkeypatch.setattr(rommlib, "wuatool_path", lambda: _endless(tmp_path, "wuatool"))
    _stops_soon(lambda cancel: rommlib.wux_audit(w, None, True, cancel=cancel))


def test_b_stops_unsquashfs_and_the_zar_hash(isolated, monkeypatch, tmp_path):
    import shutil
    exe = _endless(tmp_path, "unsquashfs")
    monkeypatch.setattr(shutil, "which", lambda name: exe if name == "unsquashfs" else None)
    _stops_soon(lambda cancel: rommlib.squashfs_crc("/x.squashfs", "x.iso", cancel))
    size = 64 << 20
    z = _put("xbox360", "Big Disc (USA).zar", b"")
    with open(z, "wb") as f:
        f.truncate(size - 16)
        f.seek(size - 16)
        f.write(size.to_bytes(8, "big") + rommlib.ZAR_VERSION.to_bytes(4, "big") + rommlib.ZAR_MAGIC.to_bytes(4, "big"))
    assert rommlib.zar_check(z) is None
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(rommlib.CheckStopped):
        rommlib.zar_check(z, full=True, cancel=cancel)


def test_quitting_the_app_kills_a_running_check_tool(tmp_path):
    exe = _endless(tmp_path, "chdman")
    got = []

    def run():
        try:
            got.append(rommlib.run_check_tool([exe], threading.Event()))
        except rommlib.CheckStopped:
            got.append("stopped")
    t = threading.Thread(target=run)
    t.start()
    time.sleep(0.3)
    rommlib.stop_running_checks()
    t.join(5)
    assert got == ["stopped"]                        # not "the CHD failed chdman verify"


# ------------------------------------------------------------ round 2: saved results from other versions

def test_an_older_full_checks_findings_survive_the_first_quick_check(isolated):
    import json
    a = _put("psx", "Alpha (USA).iso", b"a" * 10)
    b = _put("psx", "Beta Run (USA).iso", b"b" * 10)
    sig = lambda p: [os.stat(p).st_size, int(os.stat(p).st_mtime)]
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    with open(rommlib.audit_path(), "w") as fh:          # as v0.11 saved a full check: no "deep" on findings
        json.dump({"time": 1000, "deep": True, "stats": {"checked": 2}, "findings": [
            {"system": "psx", "path": a, "severity": "damaged", "problem": "Checksum doesn't match RomM",
             "rom": None, "sig": sig(a)},
            {"system": "psx", "path": b, "severity": "damaged", "problem": "Smaller than RomM's copy: 1 vs 2",
             "rom": None, "sig": sig(b)}]}, fh)
    rommlib.save_audit([], {"checked": 2}, False)
    d = rommlib.load_audit()
    assert [(os.path.basename(f["path"]), f["full_time"]) for f in d["findings"]] == [("Alpha (USA).iso", 1000)]


def test_a_saved_check_with_odd_times_doesnt_crash(isolated, rb, monkeypatch):
    import json
    a = _put("psx", "Alpha (USA).iso", b"a" * 10)
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    with open(rommlib.audit_path(), "w") as fh:
        json.dump({"time": "yesterday", "deep": False, "stats": {}, "findings": [
            {"system": "psx", "path": a, "severity": "damaged", "problem": "Checksum doesn't match RomM",
             "rom": None, "sig": [10, int(os.stat(a).st_mtime)], "deep": True, "full_time": "soon"}]}, fh)
    d = rommlib.load_audit()
    assert d["time"] == 0 and "full_time" not in d["findings"][0]
    app = _app(rb, monkeypatch)
    ck = rb.CheckScreen(app)
    app.push(ck)
    assert "Checksum" in _drawn(rb, monkeypatch, ck)


# ------------------------------------------------------------ round 2: On this box's line on the last check

def test_on_this_box_counts_games_and_says_what_a_quick_check_vouches_for(isolated, rb, monkeypatch):
    app = _app(rb, monkeypatch)
    hub = rb.OnThisBoxScreen(app)
    day = rb.day(1e9)
    hub.audit = {"time": 1e9, "deep": False, "findings": []}
    assert hub.verdict("check") == ("Nothing wrong found on %s (quick check)" % day, None)
    hub.audit = {"time": 1e9, "deep": True, "findings": []}
    assert hub.verdict("check") == ("All good on %s" % day, None)
    root = os.path.join(rommlib.ROMS_ROOT, "psx")
    hub.audit = {"time": 1e9, "deep": False, "findings": [
        {"system": "psx", "severity": "damaged", "path": os.path.join(root, "Trio (USA)", "Trio (Disc %d).cue" % d)}
        for d in (1, 2, 3)] + [
        {"system": "psx", "severity": "damaged", "io_error": True, "path": os.path.join(root, "Shut (USA).chd")},
        {"system": "psx", "severity": "differs", "path": os.path.join(root, "Trim (USA).iso")}]}
    assert hub.verdict("check") == ("1 damaged, 1 can't be read, 1 differ from RomM", rb.RED)
    assert hub.glance("check") == ("1 damaged", rb.RED)
    hub.audit["findings"] = hub.audit["findings"][3:]
    assert hub.glance("check") == ("1 can't be read", rb.RED)


# ------------------------------------------------------------ round 3: the drive "changing" on exFAT

def _fat_numbering(monkeypatch):
    """os.stat as on exFAT/FAT (and shares, FUSE): a folder no one holds open
    gets a new inode number each time it's read back into memory (here: each
    game, as a big check pushes it out). One held open keeps its number.
    Returns the list of folders held open now."""
    real_stat, real_open, real_close = os.stat, os.open, os.close
    held, gen = {}, [0]

    def fake_open(p, flags, *a, **k):
        fd = real_open(p, flags, *a, **k)
        st = os.fstat(fd)
        held[fd] = (st.st_dev, st.st_ino)
        return fd

    def fake_close(fd):
        held.pop(fd, None)
        return real_close(fd)

    def fake_stat(p, *a, **k):
        st = real_stat(p, *a, **k)
        if isinstance(p, int) or not __import__("stat").S_ISDIR(st.st_mode) or \
                (st.st_dev, st.st_ino) in held.values():
            return st
        v = list(st)
        v[1] += gen[0] * 1000003
        return os.stat_result(v)
    monkeypatch.setattr(os, "open", fake_open)
    monkeypatch.setattr(os, "close", fake_close)
    monkeypatch.setattr(os, "stat", fake_stat)
    return held, gen


def test_a_folder_renumbered_by_exfat_isnt_a_drive_swapped(isolated, no_es, monkeypatch):
    held, gen = _fat_numbering(monkeypatch)

    def progress(system, name):
        gen[0] += 1                         # pushed out of memory and read back: a new number
    got, stats = _run(Api(_three_folder_games()), {"psx": [1]}, progress=progress)
    assert got == {} and stats["checked"] == 3
    assert held == {}                       # every folder let go again


def test_the_folders_are_let_go_when_the_check_ends_any_way(isolated, no_es, monkeypatch):
    held, gen = _fat_numbering(monkeypatch)
    cancel = threading.Event()
    _run(Api(_three_folder_games()), {"psx": [1]}, cancel=cancel, progress=lambda s, n: cancel.set())
    assert held == {}

    def progress(system, name):
        if name.startswith("Bb"):
            os.rename(os.path.join(rommlib.ROMS_ROOT, "psx"), os.path.join(rommlib.ROMS_ROOT, "psx-moved"))
    with pytest.raises(rommlib.CheckAborted):
        rommlib.audit(Api(_three_folder_games()), {"psx": [1]}, progress=progress)
    assert held == {}


def test_a_system_folder_moved_mid_check_says_so_not_the_drive(isolated, no_es):
    psx = os.path.join(rommlib.ROMS_ROOT, "psx")

    def moved(system, name):
        if name.startswith("Bb") and os.path.isdir(psx):
            os.rename(psx, psx + "-old")
    with pytest.raises(rommlib.CheckAborted) as e:
        rommlib.audit(Api(_three_folder_games()), {"psx": [1]}, progress=moved)
    assert str(e.value) == "The folder roms/psx was moved or deleted during the check"
    import shutil
    shutil.rmtree(psx + "-old")
    _three_folder_games()

    def replaced(system, name):
        if name.startswith("Bb") and not os.path.exists(psx + "-old"):
            os.rename(psx, psx + "-old")
            os.makedirs(psx)
    with pytest.raises(rommlib.CheckAborted) as e:
        rommlib.audit(Api(_three_folder_games()), {"psx": [1]}, progress=replaced)
    assert str(e.value) == "The folder roms/psx changed during the check"


def test_no_games_folder_says_so(isolated, no_es, rb):
    import shutil
    shutil.rmtree(rommlib.ROMS_ROOT)
    findings, stats = rommlib.audit(Api([]), {"psx": [1]})
    assert findings == [] and stats["no_games_folder"]
    line, col = rb.check_result(stats, findings, True)
    assert "wasn't found" in line and col == rb.AMBER
    assert rb.check_result({"checked": 0}, [], False) == ("No games found to check", rb.AMBER)


# ------------------------------------------------------------ round 3: a share timing out isn't a damaged zip

def test_a_zip_on_a_share_that_times_out_cant_be_read_not_damaged(isolated, no_es, monkeypatch):
    z = _put("gba", "Far Away (USA).zip")
    with zipfile.ZipFile(z, "w") as zz:
        zz.writestr("Game.gba", b"x" * 100)
    real = zipfile.ZipFile
    err = [errno.ETIMEDOUT]

    def zf(p, *a, **k):
        if str(p) == z:
            raise OSError(err[0], os.strerror(err[0]))
        return real(p, *a, **k)
    monkeypatch.setattr(zipfile, "ZipFile", zf)
    api = Api([(1, "Far Away (USA).zip", [_rec("Far Away (USA).zip", 100, archive_members=[
        {"name": "Game.gba", "size": 100}])])])
    for code in (errno.ETIMEDOUT, errno.EHOSTDOWN, errno.ECONNRESET, errno.EAGAIN):
        err[0] = code
        f = _run(api, {"gba": [1]})[0]["Far Away (USA).zip"]
        assert f["problem"].startswith("Can't be read (") and f["io_error"], code
    err[0] = errno.EINVAL                    # a seek before the start of the file: the zip itself
    f = _run(api, {"gba": [1]})[0]["Far Away (USA).zip"]
    assert f["problem"] == "Damaged zip (can't be opened)" and not f.get("io_error")
    monkeypatch.setattr(zipfile, "ZipFile", real)
    open(z, "wb").write(b"PK\x03")           # too short to be a zip at all
    assert _run(api, {"gba": [1]})[0]["Far Away (USA).zip"]["problem"] == "Damaged zip (can't be opened)"


# ------------------------------------------------------------ round 3: B stops a PS3 folder's full check

def _ps3_folder(big=0):
    from test_audit_outputs import make_folder, SFO
    files = {"PS3_DISC.SFB": b".SFB" + b"\0" * 60, "PS3_GAME/PARAM.SFO": SFO,
             "PS3_GAME/USRDIR/EBOOT.BIN": b"SCE\0" + b"\0" * 400, "PS3_GAME/USRDIR/DATA.PSARC": b"lorem" * 100}
    root = make_folder("Lorem (USA).ps3", files)
    if big:
        with open(os.path.join(root, "PS3_GAME", "USRDIR", "BIG.DAT"), "wb") as f:
            f.truncate(big)
    return root


def test_b_stops_a_ps3_folders_full_check(isolated, monkeypatch):
    root = _ps3_folder(big=8 * rommlib.PS3_CHUNK)
    conv = rommlib.output_converter(root)
    cancel = threading.Event()
    reads = [0]
    real = open

    class Counting:
        def __init__(self, fh):
            self.fh = fh

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.fh.close()

        def fileno(self):
            return self.fh.fileno()

        def read(self, n=-1):
            reads[0] += 1
            if reads[0] == 3:
                cancel.set()
            return self.fh.read(n)

    monkeypatch.setattr("builtins.open", lambda p, *a, **k: Counting(real(p, *a, **k)))
    with pytest.raises(rommlib.CheckStopped):
        conv.audit(root, None, True, cancel=cancel)
    assert reads[0] < 10                    # not the whole folder first
    monkeypatch.setattr("builtins.open", real)
    assert conv.audit(root, None, True, cancel=threading.Event()) is None


def test_a_ps3_file_the_box_cant_read_is_an_io_error(isolated, no_es, monkeypatch):
    from test_audit_outputs import Api as OneApi, _zip_rec
    _ps3_folder()
    real = open

    def flaky(p, *a, **k):
        if str(p).endswith("DATA.PSARC"):
            raise OSError(errno.EIO, "Input/output error")
        return real(p, *a, **k)
    monkeypatch.setattr("builtins.open", flaky)
    api = OneApi([("Lorem (USA).zip", [_zip_rec("Lorem (USA).zip", "Lorem (USA).iso", 1 << 30)])])
    assert rommlib.audit(api, {"ps3": [1]})[0] == []
    (f,), _ = rommlib.audit(api, {"ps3": [1]}, deep=True)
    assert f["problem"] == "Can't be read (I/O error)" and f["io_error"] and f["path"].endswith(".ps3")


# ------------------------------------------------------------ round 3: GoodTools [codes]

def test_a_goodtools_hack_or_bad_dump_isnt_matched_by_name(isolated):
    api = Api([(1, "Moth Garden (USA).sfc", [])])
    for code in ("[h1]", "[h1C]", "[b]", "[b2]", "[t1]", "[p1]", "[o1]", "[T+Eng]", "[hM04]"):
        assert rommlib.find_in_romm(api, "snes", [1], "/roms/snes/Moth Garden (USA) %s.sfc" % code) == (None, None), code
    for code in ("[!]", "[a1]", "[f1]"):           # the game itself: verified, an alternate, a fixed copy
        assert rommlib.find_in_romm(api, "snes", [1], "/roms/snes/Moth Garden (USA) %s.sfc" % code)[1] == "name"
    api = Api([(1, "Moth Garden (USA) [b1].sfc", [])])          # RomM's bad dump isn't the box's game either
    assert rommlib.find_in_romm(api, "snes", [1], "/roms/snes/Moth Garden (USA).sfc") == (None, None)


# ------------------------------------------------------------ round 3: what only a full check finds, by its text

def test_every_full_check_only_text_is_known_as_one(isolated, no_es, monkeypatch, tmp_path):
    # made by a real full check (and not by the quick one): a zip failing its CRC, a checksum,
    # a PS3 folder's file cut short
    z = _put("gba", "Crc Zip (USA).zip")
    with zipfile.ZipFile(z, "w") as zz:
        zz.writestr("Game.gba", b"x" * 100)
    data = bytearray(open(z, "rb").read())
    data[data.index(b"x" * 100)] = ord("y")
    open(z, "wb").write(bytes(data))
    _put("gba", "Sum Cart (USA).gba", b"s" * 10)
    root = _ps3_folder()
    from test_audit_outputs import Api as OneApi, _zip_rec
    gba = Api([(1, "Crc Zip (USA).zip", [_rec("Crc Zip (USA).zip", len(data), archive_members=[
        {"name": "Game.gba", "size": 100, "crc_hash": "%08x" % zlib.crc32(b"x" * 100)}])]),
        (1, "Sum Cart (USA).gba", [_rec("Sum Cart (USA).gba", 10, sha1_hash="0" * 40)])])
    ps3 = OneApi([("Lorem (USA).zip", [_zip_rec("Lorem (USA).zip", "Lorem (USA).iso", 1 << 30)])])
    real_fstat = os.fstat

    def short(fd):                           # DATA.PSARC says it's longer than what can be read
        st = real_fstat(fd)
        if os.readlink("/proc/self/fd/%d" % fd).endswith("DATA.PSARC"):
            v = list(st)
            v[6] += 1
            return os.stat_result(v)
        return st
    monkeypatch.setattr(os, "fstat", short)
    made = []
    for api, by in ((gba, {"gba": [1]}), (ps3, {"ps3": [1]})):
        quick = {f["problem"] for f in rommlib.audit(api, by)[0]}
        made += [f["problem"] for f in rommlib.audit(api, by, deep=True)[0] if f["problem"] not in quick]
    assert sorted(made) == ["Can't read PS3_GAME/USRDIR/DATA.PSARC to its end", "Checksum doesn't match RomM",
                            "Damaged zip (a file inside fails its CRC)"]
    # what the converters say only in a full check (convert.py, under `if deep`)
    made += ["CHD failed chdman verify", "Damaged zip (a file inside can't be unpacked)",
             "Can't be read: the data in it is damaged", "Compressed file damaged: bad block",
             "Compressed file damaged (dolphin-tool couldn't read all of it)",
             "Compressed file damaged (wuatool couldn't read all of it)",
             "Compressed file damaged (unsquashfs couldn't read all of it)",
             "Compressed file damaged (its SHA-256 doesn't match)", "Checksum doesn't match RomM's copy",
             "dolphin-tool found errors in 3 blocks (damaged, or a bad dump)"]
    for p in made:
        assert rommlib.deep_problem(p), p
    for p in ("Missing file: X", "Smaller than RomM's copy: 1 vs 2", "Can't be read (I/O error)",
              "Damaged zip (can't be opened)", "Compressed file cut short"):
        assert not rommlib.deep_problem(p), p


# ------------------------------------------------------------ round 3: zip members sharing a file name

def test_two_members_with_one_file_name_are_each_compared_with_their_own(isolated, no_es):
    z = _put("gba", "Two Discs (USA).zip")
    with zipfile.ZipFile(z, "w") as zz:
        zz.writestr("Disc1/Game.bin", b"a" * 100)
        zz.writestr("Disc2/Game.bin", b"b" * 100)
    mem = [{"name": "Disc1\\Game.bin", "size": 100, "crc_hash": "%08x" % zlib.crc32(b"a" * 100)},
           {"name": "Disc2\\Game.bin", "size": 100, "crc_hash": "%08x" % zlib.crc32(b"b" * 100)}]
    rec = _rec("Two Discs (USA).zip", os.path.getsize(z), archive_members=mem)
    assert rommlib._hash_ok(z, rec) == (True, "zip")
    api = Api([(1, "Two Discs (USA).zip", [rec])])
    got, _ = _run(api, {"gba": [1]}, deep=True)            # a zip kept as is (a system that takes zips)
    assert got == {}
    # one of RomM's names no longer in the zip: never stood in for by its namesake in another folder
    mem2 = [dict(mem[0], name="Disc3\\Game.bin"), mem[1]]
    assert rommlib._hash_ok(z, _rec("x.zip", 1, archive_members=mem2)) == (False, "zip")
    one = [{"name": "Game.bin", "size": 100, "crc_hash": mem[0]["crc_hash"]}]  # by file name: only when it's one
    assert rommlib._hash_ok(z, _rec("x.zip", 1, archive_members=one)) == (False, "zip")


# ------------------------------------------------------------ round 3: what a check with nothing wrong says

def test_a_check_with_nothing_wrong_doesnt_vouch_for_games_it_couldnt_compare(isolated, rb, monkeypatch):
    st = {"checked": 30, "matched": 16, "unmatched": 14}
    assert rb.check_result(st, [], True) == ("Checked 30 games: nothing wrong found · 14 not fully checked", rb.GREEN)
    assert rb.check_result({"checked": 3}, [], True) == ("Checked 3 games: every game looks right", rb.GREEN)
    app = _app(rb, monkeypatch)
    rommlib.save_audit([], st, True)
    ck = rb.CheckScreen(app)
    app.push(ck)
    seen = _drawn(rb, monkeypatch, ck)
    assert "Nothing wrong found" in seen and "looks right" not in seen
    hub = rb.OnThisBoxScreen(app)
    day = rb.day(1e9)
    hub.audit = {"time": 1e9, "deep": True, "findings": [], "stats": st}
    assert hub.verdict("check") == ("Nothing wrong found on %s · 14 not fully checked" % day, None)
    scr = rb.AuditScreen.__new__(rb.AuditScreen)       # the result as the check screen shows it
    scr.app, scr.f, scr.deep, scr.findings, scr.stats, scr.load_error = app, ck.f, True, [], st, None
    seen = _drawn(rb, monkeypatch, scr)
    assert "aren't checked" not in seen and "only their tracks and disc images were looked at" in seen
    assert "Everything looks right" not in seen


def test_check_games_counts_games_not_findings(isolated, rb, monkeypatch):
    app = _app(rb, monkeypatch)
    root = os.path.join(rommlib.ROMS_ROOT, "psx", "Trio (USA)")
    disc = [_put("psx", "Trio (USA)/Trio (Disc %d).cue" % d, b"c") for d in (1, 2, 3)]
    rommlib.save_audit([{"system": "psx", "path": p, "severity": "damaged", "problem": "Missing track file: x",
                         "rom": None} for p in disc], {"checked": 1}, False)
    ck = rb.CheckScreen(app)
    app.push(ck)
    seen = _drawn(rb, monkeypatch, ck)
    assert "1 game needs a look" in seen and "3 games need" not in seen
    assert os.path.isdir(root)


def test_a_check_tool_that_never_finishes_is_given_up_on(isolated):
    """chdman verify can hang on a damaged raw hunk: run_check_tool's timeout kills it (rc None)."""
    import sys
    import threading
    t0 = time.monotonic()
    rc, _ = rommlib.run_check_tool([sys.executable, "-c", "import time; time.sleep(60)"], threading.Event(),
                                   timeout=1)
    assert rc is None and time.monotonic() - t0 < 10


# ------------------------------------------------------------ review round 4: slow verifies, quitting, cue lookups

@pytest.fixture(autouse=True)
def _checks_running_again():
    yield
    rommlib._CHECKS_STOPPED.clear()                  # a test that quit the app must not stop the next ones


def test_a_chdman_verify_that_stalls_is_slow_not_missing(monkeypatch):
    monkeypatch.setattr(rommlib._convert, "run_check_tool", lambda *a, **k: (None, ""))
    assert rommlib._chd_verify("chdman", "/x.chd", threading.Event()) == rommlib.UNVERIFIED_SLOW


def test_a_stalled_chdman_verify_is_counted_apart_from_a_missing_chdman(monkeypatch):
    from rbui.screens import check as ck
    st = {"unverified": 2, "unverified_tools": [rommlib.UNVERIFIED_SLOW], "unverified_by": {rommlib.UNVERIFIED_SLOW: 2}}
    line = ck.unverified_line(st)
    assert "missing" not in line and "didn't finish" in line
    assert "missing chdman" in ck.unverified_line({"unverified": 1, "unverified_tools": ["chdman"]})


def test_a_tool_that_keeps_reporting_is_not_cut_off_but_one_that_goes_quiet_is(tmp_path):
    chatty = tmp_path / "chatty"
    chatty.write_text("#!/bin/sh\nfor i in 1 2 3 4 5 6; do echo 1% >&2; sleep 0.5; done\nexit 0\n")
    chatty.chmod(0o755)
    assert rommlib.run_check_tool([str(chatty)], threading.Event(), stall=1.2)[0] == 0
    quiet = tmp_path / "quiet"
    quiet.write_text("#!/bin/sh\necho 1% >&2\nsleep 60\n")
    quiet.chmod(0o755)
    t0 = time.monotonic()
    assert rommlib.run_check_tool([str(quiet)], threading.Event(), stall=1.2)[0] is None
    assert time.monotonic() - t0 < 10


def test_no_check_tool_starts_once_the_app_has_quit(tmp_path):
    rommlib.stop_running_checks()
    marker = tmp_path / "ran"
    with pytest.raises(rommlib.CheckStopped):
        rommlib.run_check_tool(["touch", str(marker)])
    assert not marker.exists()
    with pytest.raises(rommlib.CheckStopped):
        rommlib.check_stop_point(None)


def test_a_cue_sheet_lists_each_folder_once(tmp_path, monkeypatch):
    (tmp_path / "Disc.BIN").write_bytes(b"x")
    cue = tmp_path / "disc.cue"
    cue.write_bytes(b"\xef\xbb\xbfFILE \"disc.bin\" BINARY\r  FILE disc.Bin BINARY\r\nFILE \"gone.bin\" BINARY\n")
    real, calls = os.listdir, []
    monkeypatch.setattr(os, "listdir", lambda p: calls.append(p) or real(p))
    got = rommlib.sheet_sources(str(cue))
    assert got[:2] == [str(tmp_path / "Disc.BIN")] * 2 and got[2] == str(tmp_path / "gone.bin")
    assert len(calls) == 1
