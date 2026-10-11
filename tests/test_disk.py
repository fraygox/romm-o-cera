"""Disk management: keep-free reserve and waiting for space, queue forecast,
incomplete disc images, deleting any game, leftovers, PSP CHD."""
import json
import os
import time

import pytest

import rommlib
from test_multidisc import _rom, _setup, _wait

GiB = 1 << 30
SYNC = b"\x00" + b"\xff" * 10 + b"\x00"


def _dirrec(name, lba, size, is_dir=False):
    n = len(name)
    rec = bytearray(33 + n + (1 - n % 2))
    rec[0] = len(rec)
    rec[2:6] = lba.to_bytes(4, "little")
    rec[10:14] = size.to_bytes(4, "little")
    rec[25] = 2 if is_dir else 0
    rec[32] = n
    rec[33:33 + n] = name
    return bytes(rec)


def _iso_sectors(files_end, volume=None):
    """{lba: 2048 bytes}: PVD (volume size = `volume`, default files_end), a
    root directory at 18 with a subdirectory at 19 holding one file that
    ends exactly at `files_end`."""
    pvd = bytearray(2048)
    pvd[0], pvd[1:6] = 1, b"CD001"
    pvd[80:84] = (volume or files_end).to_bytes(4, "little")
    pvd[156:190] = _dirrec(b"\x00", 18, 2048, True)[:34]
    root = _dirrec(b"\x00", 18, 2048, True) + _dirrec(b"\x01", 18, 2048, True) + _dirrec(b"DATA", 19, 2048, True)
    sub = _dirrec(b"\x00", 19, 2048, True) + _dirrec(b"\x01", 18, 2048, True) + \
        _dirrec(b"GAME.BIN;1", 20, (files_end - 20) * 2048)
    return {16: bytes(pvd), 18: root.ljust(2048, b"\0"), 19: sub.ljust(2048, b"\0")}


def _raw_disc(path, sectors, volume, header=None):
    """A MODE2/2352 data track whose files need `volume` sectors."""
    secs = _iso_sectors(volume, header)
    with open(path, "wb") as f:
        for lba in range(sectors):
            sec = bytearray(2352)
            sec[:12] = SYNC
            sec[15] = 2
            if lba in secs:
                sec[24:24 + 2048] = secs[lba]
            f.write(sec)


def _iso(path, sectors, volume, header=None):
    secs = _iso_sectors(volume, header)
    with open(path, "wb") as f:
        for lba in sorted(secs):
            f.seek(lba * 2048)
            f.write(secs[lba])
        f.truncate(sectors * 2048)


def _cue(folder, stem, sectors, volume, header=None):
    os.makedirs(folder, exist_ok=True)
    _raw_disc(os.path.join(folder, stem + ".bin"), sectors, volume, header)
    cue = os.path.join(folder, stem + ".cue")
    open(cue, "w").write('FILE "%s.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n' % stem)
    return cue


# ------------------------------------------------------------ keep free / forecast

def test_keep_free_setting_and_forecast(monkeypatch):
    assert rommlib.keep_free_bytes({}) == 20 * GiB                      # default 20 GB
    assert rommlib.keep_free_bytes({"keep_free_gb": 0}) == 0
    assert rommlib.keep_free_bytes({"keep_free_gb": "junk"}) == 20 * GiB
    J = rommlib.Job
    a = J({"id": 1, "fs_size_bytes": 4 * GiB}, "gba")
    b = J({"id": 2, "fs_size_bytes": 1 * GiB}, "psx")                    # expands 2.2x, CHD working space
    b.done_bytes = int(0.2 * GiB)
    done = J({"id": 3, "fs_size_bytes": 50 * GiB}, "gba")
    done.state = J.DONE
    fc = rommlib.queue_forecast([a, b, done], free=40 * GiB, keep=20 * GiB)
    need = 4 * GiB + int(2.2 * GiB) - int(0.2 * GiB) + int(2.2 * GiB)
    assert fc["jobs"] == 2 and fc["need"] == need and fc["fits"]
    fc = rommlib.queue_forecast([a, b], free=25 * GiB, keep=20 * GiB)
    assert not fc["fits"] and fc["short"] == need + 20 * GiB - 25 * GiB
    assert rommlib.queue_forecast([a, b], 40 * GiB, 0, chd=False)["need"] == need - int(2.2 * GiB)
    fit, used = rommlib.fit_in_space([({"fs_size_bytes": GiB}, "gba")] * 5, 23 * GiB, 20 * GiB)
    assert len(fit) == 3 and used == 3 * GiB


def test_download_waits_for_space_then_runs(isolated, mock_url, monkeypatch):
    """Not enough room (with the keep-free reserve): the download waits —
    re-checked — instead of failing, and runs once space is freed."""
    api, d = _setup(mock_url)
    d.cfg["keep_free_gb"] = 1
    free = {"b": 512 << 20}
    monkeypatch.setattr(d, "_free_bytes", lambda p: free["b"])
    monkeypatch.setattr(rommlib, "SPACE_RECHECK", 0.2)
    j = d.enqueue(_rom(api, "Ipsum Saga (Japan) (Disc 1)"), "psx")
    t0 = time.time()
    while not j.short_space and time.time() - t0 < 10:
        time.sleep(0.05)
    assert j.state == rommlib.Job.QUEUED and j.short_space > 0
    assert j.message.startswith("Waiting for space: free ")
    assert j.attempts == 0                              # not a failure, not a retry
    free["b"] = 100 * GiB
    _wait([j])
    assert j.state == rommlib.Job.DONE and not j.short_space, j.message
    log = open(rommlib.LOG_PATH).read()
    assert "SPACE " in log and "enough space now" in log


# ------------------------------------------------------------ incomplete disc images

def test_disc_incomplete_cue_and_iso(isolated):
    d = os.path.join(rommlib.ROMS_ROOT, "psx")
    good = _cue(os.path.join(d, "Good"), "Good", 400, 400)
    cut = _cue(os.path.join(d, "Cut"), "Cut", 100, 1000)
    assert rommlib.disc_sectors(good) == (400, 400, False) and rommlib.disc_incomplete(good) is None
    assert rommlib.disc_incomplete(cut) == (100, 1000, False)
    # audio first track (no ISO9660): can't tell, never flagged
    os.makedirs(os.path.join(d, "Audio"))
    open(os.path.join(d, "Audio", "a.bin"), "wb").write(b"\x01" * 2352 * 50)
    open(os.path.join(d, "Audio", "a.cue"), "w").write('FILE "a.bin" BINARY\n  TRACK 01 AUDIO\n')
    assert rommlib.disc_incomplete(os.path.join(d, "Audio", "a.cue")) is None
    iso_ok, iso_cut = os.path.join(d, "ok.iso"), os.path.join(d, "cut.iso")
    _iso(iso_ok, 1000, 1000)
    _iso(iso_cut, 500, 1000)
    assert rommlib.disc_incomplete(iso_ok) is None and rommlib.disc_incomplete(iso_cut) == (500, 1000, False)
    # the header's volume size can claim more than the disc has (Fugiat Fantasy
    # VI, Tempor Raider II): only where the files end counts
    ff6 = _cue(os.path.join(d, "FF6"), "FF6", 1000, 990, header=1200)
    assert rommlib.disc_sectors(ff6) == (1000, 990, False) and rommlib.disc_incomplete(ff6) is None
    # cut before a subdirectory: a hint, not proof (dummy entries exist)
    deep = _cue(os.path.join(d, "Deep"), "Deep", 19, 1000)
    r = rommlib.disc_incomplete(deep)
    assert r is None or r[2] is False
    # cut before the root directory: the game can't start — certain
    root = _cue(os.path.join(d, "Root"), "Root", 18, 1000)
    assert rommlib.disc_incomplete(root) == (18, 1000, True)            # the header gives the size


def test_audit_flags_incomplete_disc_image(isolated, mock_url):
    api, d = _setup(mock_url)
    plats = {p["fs_slug"]: p["id"] for p in api.platforms()}
    _cue(os.path.join(rommlib.ROMS_ROOT, "psx", "Cut Game (USA)"), "Cut Game (USA)", 100, 4000)
    _cue(os.path.join(rommlib.ROMS_ROOT, "psx", "Gap Game (USA)"), "Gap Game (USA)", 18, 4000)
    findings, _ = rommlib.audit(api, {"psx": [plats["ps1"]]})
    got = {os.path.basename(os.path.dirname(x["path"])): (x["severity"], x["problem"]) for x in findings
           if "cut short" in x["problem"] or x["problem"].startswith("Incomplete")}
    # a file past the end: maybe (dummy files exist) -> amber; a directory past the end: certain -> red
    assert got["Cut Game (USA)"][0] == "differs" and got["Cut Game (USA)"][1].startswith("May be cut short")
    assert got["Gap Game (USA)"] == ("damaged", "Incomplete disc image: 100% missing (18 of 4,000 sectors)")


def test_chd_refuses_incomplete_source_and_replaces_incomplete_chd(isolated, monkeypatch):
    import rb_chd
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    cut = _cue(os.path.join(sysdir, "Cut (USA)"), "Cut (USA)", 18, 4000)        # cut before the root: certain
    monkeypatch.setattr(rb_chd, "running_game", lambda: "")
    monkeypatch.setattr(rb_chd, "chdman", lambda *a: pytest.fail("must not convert an incomplete image"))
    assert rb_chd.convert({"system": "psx", "mode": "createcd", "sources": [cut],
                           "roots": [os.path.dirname(cut)]}) is False
    assert os.path.exists(cut) and "SKIP incomplete disc image" in open(rommlib.LOG_PATH).read()
    # a complete fresh copy next to an old, incomplete CHD of the same name: replace it
    good = _cue(os.path.join(sysdir, "Fresh (USA)"), "Fresh (USA)", 400, 400)
    old = os.path.join(sysdir, "Fresh (USA).chd")
    open(old, "w").write("old broken chd")
    monkeypatch.setattr(rommlib, "disc_incomplete",
                        lambda p, chdman="chdman": (10, 400, True) if p == old and open(p).read() == "old broken chd" else None)
    monkeypatch.setattr(rb_chd, "chdman", lambda mode, *a: (
        mode == "verify" or open(a[a.index("-o") + 1], "w").write("new chd")) and (0, ""))
    monkeypatch.setattr(rommlib, "load_config", lambda: dict(rommlib.DEFAULT_CONFIG, keep_free_gb=0))
    assert rb_chd.convert({"system": "psx", "mode": "createcd", "sources": [good],
                           "roots": [os.path.dirname(good)]}) is True
    assert open(old).read() == "new chd" and not os.path.exists(good)


def test_chd_waits_for_space(isolated, monkeypatch):
    import rb_chd
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    src = _cue(os.path.join(sysdir, "Big (USA)"), "Big (USA)", 400, 400)
    monkeypatch.setattr(rb_chd, "running_game", lambda: "")
    monkeypatch.setattr(rb_chd, "chdman", lambda *a: pytest.fail("no room: must wait"))
    monkeypatch.setattr(rommlib, "free_bytes", lambda p=None: 5 * GiB)
    monkeypatch.setattr(rommlib, "load_config", lambda: dict(rommlib.DEFAULT_CONFIG))   # keeps 20 GB free
    assert rb_chd.convert({"system": "psx", "mode": "createcd", "sources": [src],
                           "roots": [os.path.dirname(src)]}) is None
    assert os.path.exists(src) and "WAIT for space" in open(rommlib.LOG_PATH).read()


def test_psp_iso_to_chd_with_ppsspp_hunks():
    import rb_chd
    import rb_chd_plan
    assert "psp" in rommlib.CHD_DVD_SYSTEMS and "psp" in rb_chd_plan.ORDER
    assert rb_chd.codecs_for("createdvd", {}, "psp") == ["-c", "lzma,zlib,huff", "-hs", "2048"]
    assert rb_chd.codecs_for("createdvd", {}, "ps2") == ["-c", "lzma,zlib,huff"]


# ------------------------------------------------------------ deleting any game

def test_delete_game_playlist_tracks_folders_library(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    for n in (1, 2):
        _cue(os.path.join(sysdir, "Game (USA)", "Game (USA) (Disc %d)" % n), "Game (USA) (Disc %d)" % n, 20, 20)
    m3u = os.path.join(sysdir, "Game (USA).m3u")
    open(m3u, "w").write("Game (USA)/Game (USA) (Disc 1)/Game (USA) (Disc 1).cue\n"
                         "Game (USA)/Game (USA) (Disc 2)/Game (USA) (Disc 2).cue\n../../escape.txt\n")
    outside = os.path.join(os.path.dirname(rommlib.ROMS_ROOT), "escape.txt")
    open(outside, "w").write("must survive")
    other = os.path.join(sysdir, "Other (USA).chd")
    open(other, "w").write("x")
    lib = rommlib.Library()
    lib.update(lambda inst: inst.update({"5": {"system": "psx", "paths": [m3u]},
                                          "6": {"system": "psx", "paths": [other]}}))
    files, freed = rommlib.delete_game("psx", m3u, lib)
    assert not os.path.exists(m3u) and not os.path.exists(os.path.join(sysdir, "Game (USA)"))
    assert os.path.exists(outside) and os.path.exists(other) and freed > 0
    assert len(files) == 5                                  # m3u + 2 × (cue + bin); never escape.txt
    inst = json.load(open(rommlib.STATE_PATH))
    assert "5" not in inst and "6" in inst
    q = [json.loads(l) for l in open(rommlib.GAMELIST_QUEUE)]
    assert q[-1]["system"] == "psx" and m3u in q[-1]["remove"]


def test_es_game_list(isolated, fake_es):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    os.makedirs(sysdir)
    for n in ("A (USA).chd", "B (USA).chd"):
        open(os.path.join(sysdir, n), "w").write("x")
    es = fake_es({"psx": {".chd"}})
    es.mem["psx"][os.path.join(sysdir, "A (USA).chd")].update(lastplayed="20261010T224528", playcount="3")
    es.mem["psx"][os.path.join(sysdir, "B (USA).chd")].update(lastplayed="0")
    rommlib.ES_API = es.url
    try:
        games = {os.path.basename(g["path"]): g for g in rommlib.es_game_list()}
    finally:
        rommlib.ES_API = "http://127.0.0.1:1234"
    assert games["A (USA).chd"]["lastplayed"] == "20261010T224528" and games["A (USA).chd"]["playcount"] == 3
    assert games["B (USA).chd"]["lastplayed"] == "" and games["B (USA).chd"]["system"] == "psx"


# ------------------------------------------------------------ leftovers

def test_find_and_delete_leftovers(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    os.makedirs(os.path.join(sysdir, "Empty Game"))
    os.makedirs(os.path.join(sysdir, "images"))
    os.makedirs(os.path.join(sysdir, "Half (USA)"))
    open(os.path.join(sysdir, "Half (USA)", "Half (USA).bin.part"), "wb").write(b"x" * 10)
    open(os.path.join(sysdir, "Gone (USA).zip.part"), "wb").write(b"x" * 100)
    open(os.path.join(sysdir, "Queued (USA).zip.part"), "wb").write(b"x" * 100)    # still queued: resumes
    open(os.path.join(sysdir, "Conv (USA).chd.part"), "wb").write(b"x" * 1000)
    open(os.path.join(sysdir, "images", "x.part"), "wb").write(b"x")                # media: not ours
    open(os.path.join(sysdir, "Real (USA).chd"), "wb").write(b"x")
    os.makedirs(os.path.join(rommlib.DATA_DIR, "tmp", "head-abc"))
    # a game's own (empty) data folders are never leftovers
    os.makedirs(os.path.join(rommlib.ROMS_ROOT, "ps3", "Game.ps3", "PS3_GAME", "USRDIR", "empty"))
    os.makedirs(os.path.join(rommlib.ROMS_ROOT, "flatpak", "data"))
    os.makedirs(os.path.join(sysdir, "Some Game", "data", "empty"))
    open(os.path.join(sysdir, "Some Game", "game.cue"), "w").write("x")
    os.makedirs(os.path.join(sysdir, "Set (USA)", "Set (USA) (Disc 2)"))                # emptied by CHD
    open(os.path.join(sysdir, "Set (USA)", "Set (USA) (Disc 1).chd"), "w").write("x")
    pend = [({"fs_name": "Queued (USA).zip"}, "psx")]
    found = {os.path.relpath(i["path"], os.path.dirname(rommlib.ROMS_ROOT)): i["kind"]
             for i in rommlib.find_leftovers(pend, chd_busy=False)}
    assert found == {"roms/psx/Empty Game": "Empty folder",
                     "roms/psx/Set (USA)/Set (USA) (Disc 2)": "Empty folder",
                     "roms/psx/Half (USA)/Half (USA).bin.part": "Partial download",
                     "roms/psx/Gone (USA).zip.part": "Partial download",
                     "roms/psx/Conv (USA).chd.part": "Unfinished CHD",
                     "data/tmp/head-abc": "RomM-o-Cera scratch"}
    busy = rommlib.find_leftovers(pend, chd_busy=True)
    assert not any(i["kind"] in ("Unfinished CHD", "RomM-o-Cera scratch") for i in busy)
    items = rommlib.find_leftovers(pend, chd_busy=False)
    assert rommlib.delete_leftovers(items) == 1110
    left = sorted(os.listdir(sysdir))
    assert left == ["Queued (USA).zip.part", "Real (USA).chd", "Set (USA)", "Some Game", "images"]  # Half's went
    assert os.path.isdir(os.path.join(sysdir, "Some Game", "data", "empty"))


def test_chd_compared_exactly_with_romm(isolated, monkeypatch):
    """A CHD converted on the box from RomM's zip: its frames against RomM's bins."""
    chd = os.path.join(rommlib.ROMS_ROOT, "psx", "X (USA).chd")
    os.makedirs(os.path.dirname(chd))
    open(chd, "w").write("x")

    class Api:
        def rom(self, rid):
            return {"files": [{"category": "game", "file_name": "X (USA).zip", "archive_members": [
                {"name": "X (USA) (Track 1).bin", "size": 2352 * 300000},
                {"name": "X (USA) (Track 2).bin", "size": 2352 * 10000},
                {"name": "X (USA).cue", "size": 200}]}]}
    monkeypatch.setattr(rommlib, "_chd_frames", lambda p, c: ("cd", 150000))
    assert rommlib._chd_vs_romm(Api(), chd, {"id": 1}, "chdman") == (150000, 310000)
    monkeypatch.setattr(rommlib, "_chd_frames", lambda p, c: ("cd", 310000))
    assert rommlib._chd_vs_romm(Api(), chd, {"id": 1}, "chdman") is False
