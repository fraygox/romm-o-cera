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
    monkeypatch.setattr(rommlib, "es_busy", lambda app_busy=False, get=None: "")   # nothing busy in ES
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
    monkeypatch.setattr(rommlib, "es_busy", lambda app_busy=False, get=None: "")   # nothing busy in ES
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


def _detail(files, size=1000):
    return {"fs_size_bytes": size, "files": files}


def _zip(members, name="Lorem Odyssey (USA).zip"):
    return {"file_name": name, "file_size_bytes": 1000,
            "archive_members": [{"name": n, "size": s} for n, s in members]}


def test_disc_layout(isolated):
    cfg = {"chd": True, "rvz": False}
    cuebin = _detail([_zip([("Lorem.cue", 200), ("Lorem (Track 1).bin", 600_000), ("Lorem (Track 2).bin", 90_000),
                            ("Lorem (Track 3).bin", 80_000)])])
    assert rommlib.disc_layout(cuebin, "psx", cfg) == {
        "label": "CUE/BIN, 3 tracks", "discs": 1, "unpacked": 770_200, "estimated": False, "convert": "CHD"}
    two = rommlib.disc_layout(_detail([{"file_name": "Ipsum (Disc 1).cue", "file_size_bytes": 100},
                                       {"file_name": "Ipsum (Disc 1).bin", "file_size_bytes": 9},
                                       {"file_name": "Ipsum (Disc 2).cue", "file_size_bytes": 100},
                                       {"file_name": "Ipsum (Disc 2).bin", "file_size_bytes": 9}]), "psx", cfg)
    assert two["discs"] == 2 and two["label"] == "CUE/BIN" and two["unpacked"] is None   # loose: nothing to unpack
    gdi = rommlib.disc_layout(_detail([_zip([("Dolor.gdi", 1), ("track01.bin", 5), ("track02.raw", 5),
                                            ("track03.bin", 5)])]), "dreamcast", cfg)
    assert gdi["label"] == "GDI, 3 tracks" and gdi["convert"] == "CHD"
    # a zip RomM listed no members for: size is a guess, said so
    est = rommlib.disc_layout(_detail([{"file_name": "Sit.zip", "file_size_bytes": 1000}]), "psx", cfg)
    assert est["estimated"] and est["unpacked"] == rommlib.space_needed({"fs_size_bytes": 1000}, "psx")
    assert est["label"] == "" and est["convert"] is None
    # cartridges aren't disc games
    assert rommlib.disc_layout(_detail([_zip([("Cart.gba", 10)])]), "gba", cfg) is None
    assert rommlib.disc_layout({"files": []}, "psx", cfg) is None
    iso = _detail([_zip([("Amet.iso", 4 * GiB)])])
    assert rommlib.disc_layout(iso, "ps2", cfg)["convert"] == "CHD"
    assert rommlib.disc_layout(iso, "ps2", {"chd": False})["convert"] is None
    assert rommlib.disc_layout(iso, "ps2", cfg, accepts=lambda s, e: e != ".chd")["convert"] is None
    assert rommlib.disc_layout(iso, "gamecube", cfg)["convert"] is None          # RVZ off
    assert rommlib.disc_layout(iso, "xbox", cfg) == {"label": "ISO", "discs": 1, "unpacked": 4 * GiB,
                                                     "estimated": False, "convert": None}


def test_es_counts(isolated, fake_es, monkeypatch):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    os.makedirs(sysdir)
    for n in ("A (USA).chd", "B (USA).chd"):
        open(os.path.join(sysdir, n), "w").write("x")
    monkeypatch.setattr(rommlib, "ES_API", fake_es({"psx": {".chd"}}).url)
    assert rommlib.es_counts()["psx"] == 2
    monkeypatch.setattr(rommlib, "ES_API", "http://127.0.0.1:9")      # ES not running: no counts, no error
    assert rommlib.es_counts() == {}


def _xbox_image(path, offset, tail=b"game data"):
    """A sparse stand-in for an Xbox image: the XDVDFS magic where an XISO has
    it, `offset` bytes in (a redump image's game partition)."""
    with open(path, "wb") as f:
        f.seek(offset + 0x10000)
        f.write(rommlib.XDVDFS_MAGIC)
        f.seek(offset + 0x20000)
        f.write(tail)


@pytest.mark.parametrize("in_place", [True, False])
def test_make_xiso(isolated, tmp_path, monkeypatch, in_place):
    if not in_place:
        monkeypatch.setattr(rommlib, "_collapse_front", lambda p, n: False)     # exFAT/btrfs: copy instead
    off = rommlib.XBOX_PARTITIONS[2]
    p = str(tmp_path / "Lorem Odyssey (USA).iso")
    _xbox_image(p, off)
    full = os.path.getsize(p)
    assert rommlib.xbox_partition(p) == off
    try:
        dropped = rommlib.make_xiso(p, free=100 * GiB)
    except Exception:
        if in_place:
            pytest.skip("this filesystem can't collapse ranges")
        raise
    if in_place and not dropped:
        pytest.skip("this filesystem can't collapse ranges")
    assert dropped == off and os.path.getsize(p) == full - off
    assert rommlib.xbox_partition(p) == 0                      # an XISO now
    with open(p, "rb") as f:
        f.seek(0x20000)
        assert f.read(9) == b"game data"
    assert rommlib.make_xiso(p) == 0                            # nothing more to do
    assert rommlib.xbox_cut("xbox", p, {"file_size_bytes": full})
    assert not rommlib.xbox_cut("xbox", p, {"file_size_bytes": full + 1})
    assert not rommlib.xbox_cut("ps2", p, {"file_size_bytes": full})


def test_make_xiso_leaves_other_files(isolated, tmp_path, monkeypatch):
    p = tmp_path / "Ipsum.iso"
    p.write_bytes(b"\0" * 200_000)
    assert rommlib.xbox_partition(str(p)) is None and rommlib.make_xiso(str(p)) == 0
    monkeypatch.setattr(rommlib, "_collapse_front", lambda p, n: False)
    big = str(tmp_path / "Dolor.iso")
    _xbox_image(big, rommlib.XBOX_PARTITIONS[1])
    with pytest.raises(rommlib.NoSpace):                        # a copy that doesn't fit: never half-done
        rommlib.make_xiso(big, free=0)
    assert rommlib.xbox_partition(big) == rommlib.XBOX_PARTITIONS[1]


def test_stem_of():
    assert rommlib.stem_of("Lorem (USA).iso.squashfs") == "Lorem (USA)"
    assert rommlib.stem_of("Lorem (USA).squashfs") == "Lorem (USA)"
    assert rommlib.stem_of("Lorem (v1.02).squashfs") == "Lorem (v1.02)"       # not a disc extension
    assert rommlib.stem_of("Lorem (USA).chd") == "Lorem (USA)"


def test_squashfs_layout_and_setting(isolated, monkeypatch):
    monkeypatch.setattr(rommlib.shutil, "which", lambda n: "/usr/bin/" + n)
    iso = _detail([_zip([("Amet.iso", 7 * GiB)])])
    assert rommlib.disc_layout(iso, "xbox", {"squashfs": False})["convert"] is None
    assert rommlib.disc_layout(iso, "xbox", {"squashfs": True})["convert"] == "squashfs"
    assert not rommlib.squashfs_enabled({"squashfs": True}, "ps2")
    monkeypatch.setattr(rommlib.shutil, "which", lambda n: None)
    assert not rommlib.squashfs_enabled({"squashfs": True}, "xbox")             # mksquashfs missing


def test_xbox_downloads_made_bootable(isolated, monkeypatch):
    """Make Xbox images bootable: a bare .xiso download becomes .iso (ES lists
    .iso only), a full redump image keeps its game partition, an XISO is left
    as it is, and squashfs is queued only when that's on too. Off: untouched."""
    monkeypatch.setattr(rommlib, "_collapse_front", lambda p, n: False)    # copy path: works on any fs
    monkeypatch.setattr(rommlib.shutil, "which", lambda n: "/usr/bin/" + n)
    sysdir = os.path.join(rommlib.ROMS_ROOT, "xbox")
    os.makedirs(sysdir)
    cfg = dict(rommlib.DEFAULT_CONFIG)
    d = rommlib.Downloader(None, rommlib.Library(), cfg, persist=False)
    d.es = {"xbox": {".iso", ".squashfs"}}
    monkeypatch.setattr(rommlib, "free_bytes", lambda p=None: 100 * GiB)

    xiso = os.path.join(sysdir, "Lorem (USA).xiso")
    _xbox_image(xiso, 0)
    j = rommlib.Job({"id": 1}, "xbox")
    out = d._postprocess_file(j, xiso)
    assert out == [os.path.join(sysdir, "Lorem (USA).iso")] and not os.path.exists(xiso)
    assert rommlib.xbox_partition(out[0]) == 0 and j.chd_task is None          # squashfs is off

    full = os.path.join(sysdir, "Ipsum (USA).iso")
    _xbox_image(full, rommlib.XBOX_PARTITIONS[0])
    cfg["squashfs"] = True
    j = rommlib.Job({"id": 2}, "xbox")
    assert d._postprocess_file(j, full) == [full]
    assert rommlib.xbox_partition(full) == 0                                    # cut to the game partition
    assert json.load(open(os.path.join(rommlib.CHD_QUEUE_DIR, j.chd_task)))["mode"] == "squashfs"

    cfg.update(xbox_bootable=False, squashfs=False)
    other = os.path.join(sysdir, "Dolor (USA).xiso")
    _xbox_image(other, 0)
    redump = os.path.join(sysdir, "Sit (USA).iso")
    _xbox_image(redump, rommlib.XBOX_PARTITIONS[0])
    assert d._postprocess_file(rommlib.Job({"id": 3}, "xbox"), other) == [other]   # left as .xiso
    d._postprocess_file(rommlib.Job({"id": 4}, "xbox"), redump)
    assert rommlib.xbox_partition(redump) == rommlib.XBOX_PARTITIONS[0]           # left full


def test_xiso_rename_never_overwrites(isolated, tmp_path):
    a, b = tmp_path / "Amet.xiso", tmp_path / "Amet.iso"
    a.write_bytes(b"x")
    b.write_bytes(b"y")
    assert rommlib.xiso_to_iso(str(a)) == str(a) and b.read_bytes() == b"y"


def test_forecast_reserves_every_converters_working_space():
    J = rommlib.Job
    cfg = dict(rommlib.DEFAULT_CONFIG, zar=True)
    x360 = J({"id": 1, "fs_size_bytes": 8 * GiB}, "xbox360")            # .zar: files + archive, 2x
    gba = J({"id": 2, "fs_size_bytes": 1 * GiB}, "gba")
    size = rommlib.space_needed(x360.rom, "xbox360")
    base = size + GiB
    with_tools = dict(cfg)
    import unittest.mock as m
    with m.patch.object(rommlib, "zar_enabled", lambda c, s=None: bool(c.get("zar"))):
        assert rommlib.queue_forecast([x360, gba], 0, 0, cfg=with_tools)["need"] == base + 2 * size
        assert rommlib.queue_forecast([x360, gba], 0, 0, cfg=dict(cfg, zar=False))["need"] == base
    ps3 = J({"id": 3, "fs_size_bytes": 10 * GiB}, "ps3")                 # the folder beside the zip
    assert rommlib.queue_forecast([ps3], 0, 0, cfg=cfg)["need"] == 2 * rommlib.space_needed(ps3.rom, "ps3")
    assert rommlib.queue_forecast([ps3], 0, 0, cfg=dict(cfg, ps3_folders=False))["need"] == \
        rommlib.space_needed(ps3.rom, "ps3")
    assert rommlib.queue_forecast([ps3], 0, 0)["need"] == rommlib.space_needed(ps3.rom, "ps3")   # no cfg: CHD only


def test_forecast_asks_every_converter_for_its_working_space(monkeypatch):
    """Every system a converter works on reserves that converter's working space
    while it's on (the biggest space_factor among them), and none while it's off."""
    for name, key in (("squashfs_enabled", "squashfs"), ("z3ds_enabled", "z3ds"), ("zar_enabled", "zar")):
        monkeypatch.setattr(rommlib, name, lambda cfg, system=None, key=key: bool(cfg.get(key)))
    monkeypatch.setattr(rommlib, "rvz_enabled", lambda cfg: bool(cfg.get("rvz")))
    on = dict(rommlib.DEFAULT_CONFIG, **{c.setting: True for c in rommlib.CONVERTERS})
    off = dict(rommlib.DEFAULT_CONFIG, **{c.setting: False for c in rommlib.CONVERTERS})
    for system in sorted(set().union(*(c.systems for c in rommlib.CONVERTERS))):
        j = rommlib.Job({"id": 1, "fs_size_bytes": 4 * GiB}, system)
        size = rommlib.space_needed(j.rom, system)
        factor = max(c.space_factor for c in rommlib.converters_for(system))
        assert rommlib.queue_forecast([j], 0, 0, on["chd"], on)["need"] == size + factor * size, system
        assert rommlib.queue_forecast([j], 0, 0, off["chd"], off)["need"] == size, system


def test_leftovers_offer_copies_of_converted_games_and_unfinished_folders(isolated):
    R = rommlib.ROMS_ROOT

    def w(rel, size=10):
        p = os.path.join(R, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "wb").write(b"x" * size)
        return p
    w("gamecube/Lorem Crossing (USA).7z", 100)                    # beside its RVZ: a copy
    w("gamecube/Lorem Crossing (USA).rvz")
    w("psx/Ipsum (USA) (Track 1).bin", 30)                        # loose tracks beside the CHD
    w("psx/Ipsum (USA) (Track 2).bin", 20)
    w("psx/Ipsum (USA).chd")
    w("psx/Dolor (USA).cue")                                      # no converted copy: a game, not a leftover
    w("psx/Dolor (USA).bin")
    w("xbox/Sit (USA).iso", 40)                                   # beside X.iso.squashfs
    w("xbox/Sit (USA).iso.squashfs")
    w("ps3/Amet (USA).zip", 50)                                   # beside its game folder
    w("ps3/Amet (USA).ps3/PS3_GAME/USRDIR/EBOOT.BIN")
    w("ps3/Amet (USA).ps3/PS3_GAME/USRDIR/cache.tmp.part")        # inside a game folder: never looked at
    w("ps3/Elit (USA).ps3.part/PS3_GAME/PARAM.SFO", 60)           # an interrupted PS3 folder
    w("ps3/Queued (USA).zip", 5)                                  # a queued conversion's source stays
    w("ps3/Queued (USA).ps3/PS3_GAME/USRDIR/EBOOT.BIN")
    w("mame/sitgame.zip")                                         # arcade sets keep zip + chd together
    w("mame/sitgame.chd")
    os.makedirs(rommlib.CHD_QUEUE_DIR, exist_ok=True)
    json.dump({"system": "ps3", "mode": "ps3", "sources": [os.path.join(R, "ps3/Queued (USA).zip")]},
              open(os.path.join(rommlib.CHD_QUEUE_DIR, "1-1-ps3.json"), "w"))
    items = rommlib.find_leftovers(chd_busy=False)
    found = {os.path.relpath(i["path"], R): (i["kind"], i.get("note")) for i in items}
    copy = "Copy of a converted game"
    assert found == {
        "gamecube/Lorem Crossing (USA).7z": (copy, "Already here as Lorem Crossing (USA).rvz"),
        "psx/Ipsum (USA) (Track 1).bin": (copy, "Already here as Ipsum (USA).chd"),
        "psx/Ipsum (USA) (Track 2).bin": (copy, "Already here as Ipsum (USA).chd"),
        "xbox/Sit (USA).iso": (copy, "Already here as Sit (USA).iso.squashfs"),
        "ps3/Amet (USA).zip": (copy, "Already here as Amet (USA).ps3"),
        "ps3/Elit (USA).ps3.part": ("Unfinished conversion", None)}
    assert rommlib.find_leftovers(chd_busy=True) == []            # never while a conversion may be writing
    assert rommlib.delete_leftovers(items) == 100 + 30 + 20 + 40 + 50 + 60
    assert not os.path.exists(os.path.join(R, "ps3/Elit (USA).ps3.part"))
    for kept in ("gamecube/Lorem Crossing (USA).rvz", "psx/Ipsum (USA).chd", "psx/Dolor (USA).bin",
                 "ps3/Amet (USA).ps3/PS3_GAME/USRDIR/EBOOT.BIN", "ps3/Queued (USA).zip", "mame/sitgame.zip"):
        assert os.path.exists(os.path.join(R, kept)), kept


def test_a_copy_stays_when_its_converted_game_looks_broken(isolated, monkeypatch):
    R = rommlib.ROMS_ROOT
    for rel in ("psx/Lorem (USA).zip", "psx/Lorem (USA).chd", "ps3/Ipsum (USA).zip"):
        os.makedirs(os.path.dirname(os.path.join(R, rel)), exist_ok=True)
        open(os.path.join(R, rel), "wb").write(b"x")
    os.makedirs(os.path.join(R, "ps3", "Ipsum (USA).ps3", "PS3_GAME"))          # no EBOOT.BIN in it
    monkeypatch.setattr(rommlib, "disc_incomplete", lambda p, *a: (10, 100, True))   # the CHD is cut short
    assert rommlib.find_leftovers(chd_busy=False) == []
