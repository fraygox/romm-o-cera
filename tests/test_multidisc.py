"""Multi-disc sets, checksums/retries, queue control and the box audit —
against tools/mock_romm.py's fixtures (Lorem Odyssey, Ipsum Saga, Corrupt /
Flaky Cart) and on a temp roms tree."""
import json
import os
import stat
import sys
import time
import xml.etree.ElementTree as ET
import zipfile

import pytest

import rommlib
import rb_finalize

PSX_ES = {"psx": {".cue", ".chd", ".m3u", ".pbp"}, "gba": {".gba", ".zip"}}


def _wait(jobs, timeout=120):
    t0 = time.time()
    while any(j.active for j in jobs):
        if time.time() - t0 > timeout:
            pytest.fail("downloads did not finish: %s" % [(j.label, j.state, j.message) for j in jobs])
        time.sleep(0.05)


def _setup(mock_url, es=PSX_ES):
    cfg = dict(rommlib.DEFAULT_CONFIG, url=mock_url, token="demo")
    api = rommlib.Api.from_config(cfg)
    d = rommlib.Downloader(api, rommlib.Library(), cfg)
    d.es = es
    return api, d


def _rom(api, stem):
    title = rommlib.strip_tags(stem)
    for r in api.roms(search=title, limit=250)["items"]:
        if r["fs_name_no_ext"] == stem:
            return r
    raise KeyError(stem)


# ------------------------------------------------------------ naming

@pytest.mark.parametrize("name,expected", [
    ("Final Fantasy VII (USA) (Disc 1)", ("Final Fantasy VII (USA)", 1)),
    ("Game (Europe) (Disc 2 of 3) (Rev 1)", ("Game (Europe) (Rev 1)", 2)),
    ("Riven (USA) (Disc 05)", ("Riven (USA)", 5)),
    ("Enemy Zero (Japan) (Disc 0)", ("Enemy Zero (Japan)", 0)),
    ("Policenauts (Japan) (Disk B)", ("Policenauts (Japan)", 2)),
    ("Metal Gear Solid (USA) (CD 1)", ("Metal Gear Solid (USA)", 1)),
    ("Discworld (Europe)", None),
    ("Disc Jockey (USA)", None),
])
def test_disc_info(name, expected):
    assert rommlib.disc_info(name) == expected


def test_strip_tags():
    assert rommlib.strip_tags("Lorem Odyssey (USA) (Disc 1) [!]") == "lorem odyssey"


# ------------------------------------------------------------ finding a set

def test_find_disc_set_full_and_style(isolated, mock_url):
    api = rommlib.Api(mock_url, "demo")
    d1 = _rom(api, "Lorem Odyssey (USA) (Disc 1)")
    discs, missing = rommlib.find_disc_set(api, d1)
    assert [r["fs_name_no_ext"] for _, r in discs] == [
        "Lorem Odyssey (USA) (Disc 1)", "Lorem Odyssey (USA) (Disc 2)", "Lorem Odyssey (USA) (Disc 3)"]
    assert missing == []
    # the "Disc 01" copy prefers its own style, borrows disc 3 from the other
    d01 = _rom(api, "Lorem Odyssey (USA) (Disc 01)")
    discs, missing = rommlib.find_disc_set(api, d01)
    assert [r["fs_name_no_ext"] for _, r in discs] == [
        "Lorem Odyssey (USA) (Disc 01)", "Lorem Odyssey (USA) (Disc 02)", "Lorem Odyssey (USA) (Disc 3)"]
    # another region is never mixed in, though RomM calls it a sibling
    eu = _rom(api, "Lorem Odyssey (Europe) (Disc 2)")
    discs, _ = rommlib.find_disc_set(api, eu)
    assert [n for n, _ in discs] == [1, 2] and all("(Europe)" in r["fs_name_no_ext"] for _, r in discs)


def test_find_disc_set_incomplete_and_not_a_disc(isolated, mock_url):
    api = rommlib.Api(mock_url, "demo")
    discs, missing = rommlib.find_disc_set(api, _rom(api, "Ipsum Saga (Japan) (Disc 3)"))
    assert [n for n, _ in discs] == [1, 3] and missing == [2]
    assert rommlib.find_disc_set(api, _rom(api, "Flaky Cart (USA)")) == (None, None)


def test_disc_group():
    discs = [(1, {"id": 5, "fs_name_no_ext": "Lorem Odyssey (USA) (Disc 1)"}),
             (2, {"id": 6, "fs_name_no_ext": "Lorem Odyssey (USA) (Disc 2)"})]
    g = rommlib.disc_group("ps1", discs)
    assert g == {"id": "ps1:lorem odyssey (usa)", "base": "Lorem Odyssey (USA)",
                 "title": "Lorem Odyssey", "rom_ids": [5, 6]}


# ------------------------------------------------------------ end to end

def _fake_chdman(tmp_path, monkeypatch):
    """A chdman stand-in on PATH: 'converts' by writing a small marker file."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "chdman"
    exe.write_text("#!%s\nimport sys\na = sys.argv[1:]\n"
                   "if a[0] != 'verify':\n"
                   "    open(a[a.index('-o') + 1], 'w').write('CHD of ' + a[a.index('-i') + 1])\n" % sys.executable)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", "%s:%s" % (bindir, os.environ["PATH"]))


def test_multi_disc_download_m3u_gamelist_and_chd(isolated, mock_url, tmp_path, monkeypatch):
    """Download a 3-disc set as one: every disc verified, one .m3u, discs
    hidden in ES and the playlist carrying the metadata; then the CHD worker
    converts the discs and everything (m3u, installed.json, gamelist) follows."""
    api, d = _setup(mock_url)
    d1 = _rom(api, "Lorem Odyssey (USA) (Disc 1)")
    discs, missing = rommlib.find_disc_set(api, d1)
    group = rommlib.disc_group("ps1", discs, "Lorem Odyssey")
    jobs = [d.enqueue(r, "psx", group) for _, r in discs]
    _wait(jobs)
    assert [j.state for j in jobs] == ["Done"] * 3, [j.message for j in jobs]
    assert all(j.verified for j in jobs) and "checksum OK" in jobs[0].message
    assert [j.label for j in jobs] == ["Lorem Odyssey (Disc %d)" % n for n in (1, 2, 3)]
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    m3u = os.path.join(sysdir, "Lorem Odyssey (USA).m3u")
    lines = open(m3u).read().split()
    assert open(m3u).read().splitlines() == [
        "Lorem Odyssey (USA) (Disc %d)/Lorem Odyssey (USA) (Disc %d).cue" % (n, n) for n in (1, 2, 3)]
    assert lines
    inst = json.load(open(rommlib.STATE_PATH))
    recs = [inst[str(r["id"])] for _, r in discs]
    assert all(rec["group"]["id"] == group["id"] and m3u in rec["paths"] for rec in recs)
    # gamelist hand-off: 3 disc entries, then hide + the playlist's own entry
    q = [json.loads(l) for l in open(rommlib.GAMELIST_QUEUE)]
    hide = [e for e in q if "hide" in e and "rename" not in e]
    assert len(hide) == 1 and len(hide[0]["hide"]) == 3
    meta = [e for e in q if e.get("paths") == [m3u]]
    assert len(meta) == 1 and meta[0]["name"] == "Lorem Odyssey" and meta[0]["desc"]
    # idempotent: finishing again changes nothing
    assert d._finish_group(group, "psx") == m3u
    assert len([json.loads(l) for l in open(rommlib.GAMELIST_QUEUE)]) == len(q)

    # --- the CHD worker converts each disc (fake chdman) ---
    _fake_chdman(tmp_path, monkeypatch)
    import rb_chd
    monkeypatch.setattr(rb_chd, "running_game", lambda: "")
    tasks = sorted(os.listdir(rommlib.CHD_QUEUE_DIR))
    assert len(tasks) == 3
    for t in tasks:
        assert rb_chd.convert(json.load(open(os.path.join(rommlib.CHD_QUEUE_DIR, t))))
    assert open(m3u).read().splitlines() == ["Lorem Odyssey (USA) (Disc %d).chd" % n for n in (1, 2, 3)]
    assert sorted(os.listdir(sysdir)) == ["Lorem Odyssey (USA) (Disc %d).chd" % n for n in (1, 2, 3)] + \
        ["Lorem Odyssey (USA).m3u"]
    inst = json.load(open(rommlib.STATE_PATH))
    for n, (_, r) in enumerate(discs, 1):
        assert inst[str(r["id"])]["paths"] == [
            os.path.join(sysdir, "Lorem Odyssey (USA) (Disc %d).chd" % n), m3u]
    assert d._finish_group(group, "psx") == m3u            # still consistent

    # --- gamelist merge of the whole batch: one visible entry, discs hidden ---
    entries = [json.loads(l) for l in open(rommlib.GAMELIST_QUEUE)]
    rb_finalize.merge(entries, None, PSX_ES)
    games = ET.parse(os.path.join(sysdir, "gamelist.xml")).getroot().findall("game")
    shown = [g.findtext("path") for g in games if g.findtext("hidden") != "true"]
    hidden = sorted(g.findtext("path") for g in games if g.findtext("hidden") == "true")
    assert shown == ["./Lorem Odyssey (USA).m3u"]
    assert hidden == ["./Lorem Odyssey (USA) (Disc %d).chd" % n for n in (1, 2, 3)]
    pl = next(g for g in games if g.findtext("path") == "./Lorem Odyssey (USA).m3u")
    assert pl.findtext("name") == "Lorem Odyssey" and pl.findtext("desc")

    # --- deleting the set removes discs + playlist, keeps nothing behind ---
    lib = rommlib.Library()
    assert lib.group_for(discs[0][1]["id"])["id"] == group["id"]
    paths, freed = lib.uninstall_group(group, "psx")
    assert freed > 0 and sorted(os.listdir(sysdir)) == ["gamelist.xml", "gamelist.xml.bak-rommbrowser"] or \
        sorted(os.listdir(sysdir)) == ["gamelist.xml"]
    assert not any(str(r["id"]) in json.load(open(rommlib.STATE_PATH)) for _, r in discs)


def test_chd_before_set_finishes(isolated, mock_url, tmp_path, monkeypatch):
    """Disc 1 is converted to CHD while disc 2 is still downloading: the
    playlist must point at the CHD, not the deleted cue."""
    api, d = _setup(mock_url)
    discs, _ = rommlib.find_disc_set(api, _rom(api, "Lorem Odyssey (Europe) (Disc 1)"))
    group = rommlib.disc_group("ps1", discs, "Lorem Odyssey")
    d.hold_all()
    j1, j2 = d.enqueue(discs[0][1], "psx", group), d.enqueue(discs[1][1], "psx", group)
    d.pause_job(j2)
    d.release_all()                    # (resumes paused jobs too) ...
    d.pause_job(j2)                    # ... so pause disc 2 again
    _wait([j1])
    assert j1.state == "Done" and j2.state == "Paused"
    _fake_chdman(tmp_path, monkeypatch)
    import rb_chd
    monkeypatch.setattr(rb_chd, "running_game", lambda: "")
    t = os.listdir(rommlib.CHD_QUEUE_DIR)[0]
    assert rb_chd.convert(json.load(open(os.path.join(rommlib.CHD_QUEUE_DIR, t))))
    os.remove(os.path.join(rommlib.CHD_QUEUE_DIR, t))
    d.resume_job(j2)
    _wait([j2])
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    assert open(os.path.join(sysdir, "Lorem Odyssey (Europe).m3u")).read().splitlines() == [
        "Lorem Odyssey (Europe) (Disc 1).chd", "Lorem Odyssey (Europe) (Disc 2)/Lorem Odyssey (Europe) (Disc 2).cue"]


def test_set_adopts_discs_already_on_the_box(isolated, mock_url, rb):
    """Disc 1 was copied over earlier (not by the app): "download all" only
    fetches the rest, and the playlist includes the copy already there."""
    api, d = _setup(mock_url)
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    os.makedirs(sysdir)
    pre = os.path.join(sysdir, "Lorem Odyssey (USA) (Disc 1).chd")
    open(pre, "wb").write(b"old copy")
    discs, _ = rommlib.find_disc_set(api, _rom(api, "Lorem Odyssey (USA) (Disc 1)"))
    group = rommlib.disc_group("ps1", discs, "Lorem Odyssey")

    class FakeApp:                       # just what App.queue_set needs
        library, downloader = d.library, d
        bg = staticmethod(lambda fn, done=None, err=None: done(fn()) if done else fn())
        toast = staticmethod(lambda *a, **k: None)
    n = rb.App.queue_set(FakeApp, group, [(r, "psx") for _, r in discs], "psx")
    assert n == 2
    _wait(list(d.jobs))
    lines = open(os.path.join(sysdir, "Lorem Odyssey (USA).m3u")).read().splitlines()
    assert lines[0] == "Lorem Odyssey (USA) (Disc 1).chd" and len(lines) == 3
    assert open(pre, "rb").read() == b"old copy"           # not downloaded again


def test_system_without_m3u_keeps_discs_separate(isolated, mock_url):
    api, d = _setup(mock_url, es={"psx": {".cue", ".chd"}})
    discs, _ = rommlib.find_disc_set(api, _rom(api, "Ipsum Saga (Japan) (Disc 1)"))
    group = rommlib.disc_group("ps1", discs)
    _wait([d.enqueue(r, "psx", group) for _, r in discs])
    assert not [n for n in os.listdir(os.path.join(rommlib.ROMS_ROOT, "psx")) if n.endswith(".m3u")]


def test_foreign_m3u_is_never_overwritten(isolated, mock_url):
    api, d = _setup(mock_url)
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    os.makedirs(sysdir)
    mine = os.path.join(sysdir, "Ipsum Saga (Japan).m3u")
    open(mine, "w").write("somewhere/else.cue\n")
    discs, _ = rommlib.find_disc_set(api, _rom(api, "Ipsum Saga (Japan) (Disc 1)"))
    _wait([d.enqueue(r, "psx", rommlib.disc_group("ps1", discs)) for _, r in discs])
    assert open(mine).read() == "somewhere/else.cue\n"


# ------------------------------------------------------------ variants

def test_variants(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    os.makedirs(sysdir)
    for n in ("Lorem Odyssey (Europe) (Disc 1).chd", "Lorem Odyssey (USA) (Disc 2).chd",
              "Lorem Odyssey (USA).m3u", "Lorem Odyssey II (USA).chd", "gamelist.xml"):
        open(os.path.join(sysdir, n), "w").close()
    lib = rommlib.Library()
    rom = {"id": 1, "fs_name": "Lorem Odyssey (USA) (Disc 1).zip", "fs_name_no_ext": "Lorem Odyssey (USA) (Disc 1)"}
    assert lib.variants(rom, "psx") == ["Lorem Odyssey (Europe) (Disc 1).chd"]
    cart = {"id": 2, "fs_name": "Game (USA).zip", "fs_name_no_ext": "Game (USA)"}
    os.makedirs(os.path.join(rommlib.ROMS_ROOT, "snes"))
    for n in ("Game (Europe).zip", "Game (USA).zip", "Game (Japan) (Rev 1).sfc"):
        open(os.path.join(rommlib.ROMS_ROOT, "snes", n), "w").close()
    assert lib.variants(cart, "snes") == ["Game (Europe).zip", "Game (Japan) (Rev 1).sfc"]


# ------------------------------------------------------------ checksums + retries

def test_corrupt_download_fails_after_one_retry(isolated, mock_url, monkeypatch):
    monkeypatch.setattr(rommlib, "RETRY_DELAYS", (0.1, 0.1, 0.1))
    api, d = _setup(mock_url)
    j = d.enqueue(_rom(api, "Corrupt Cart (USA)"), "gba")
    _wait([j])
    assert j.state == "Failed" and "match RomM" in j.message and j.attempts == 2
    assert os.listdir(os.path.join(rommlib.ROMS_ROOT, "gba")) == []     # nothing damaged left behind


def test_flaky_server_is_retried(isolated, mock_url, monkeypatch):
    monkeypatch.setattr(rommlib, "RETRY_DELAYS", (0.1, 0.1, 0.1))
    api, d = _setup(mock_url)
    j = d.enqueue(_rom(api, "Flaky Cart (USA)"), "gba")
    _wait([j])
    assert j.state == "Done" and j.verified and 1 <= j.attempts <= 2, (j.state, j.message, j.attempts)


def test_file_hash_ok(tmp_path):
    import hashlib
    import zlib
    p = tmp_path / "f"
    p.write_bytes(b"hello")
    assert rommlib.file_hash_ok(str(p), {"sha1_hash": hashlib.sha1(b"hello").hexdigest()}) == (True, "sha1")
    assert rommlib.file_hash_ok(str(p), {"md5_hash": "0" * 32}) == (False, "md5")
    assert rommlib.file_hash_ok(str(p), {"crc_hash": "%x" % zlib.crc32(b"hello")}) == (True, "crc")
    assert rommlib.file_hash_ok(str(p), {}) == (None, None)


def test_zip_checked_by_contents_like_romm(tmp_path):
    """RomM's file-level hashes of a zip describe its contents, so a zip is
    judged by its members (CRC + size) and testzip, never the zip's own hash."""
    import hashlib
    import zlib
    a, b = b"A" * 5000, bytes(range(256)) * 40
    zp = tmp_path / "Game (USA).zip"
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("Game (USA).gba", a)
        z.writestr("sub/Extra.bin", b)

    def mem(name, data, **kw):
        return dict({"name": name, "size": len(data), "crc_hash": "%08x" % zlib.crc32(data)}, **kw)
    rec = {"file_name": "Game (USA).zip", "sha1_hash": hashlib.sha1(a).hexdigest(),
           "archive_members": [mem("Game (USA).gba", a), mem("sub/Extra.bin", b)]}
    assert rommlib.file_hash_ok(str(zp), rec) == (True, "zip")
    # names matched case-insensitively, or by base name
    rec2 = dict(rec, archive_members=[mem("GAME (USA).GBA", a), mem("Extra.bin", b)])
    assert rommlib.file_hash_ok(str(zp), rec2) == (True, "zip")
    for bad in ([mem("Game (USA).gba", a, crc_hash="deadbeef")],
                [mem("Game (USA).gba", a, size=1)],
                [mem("Missing.gba", a)]):
        assert rommlib.file_hash_ok(str(zp), dict(rec, archive_members=bad)) == (False, "zip")
    # no member list: RomM can't vouch for it (its sha1 is not the zip's)
    assert rommlib.file_hash_ok(str(zp), {"file_name": "x.zip", "sha1_hash": "0" * 40}) == (None, None)
    # damaged data inside an intact-looking zip fails testzip
    zs = tmp_path / "stored.zip"
    with zipfile.ZipFile(zs, "w", zipfile.ZIP_STORED) as z:
        z.writestr("Game (USA).gba", a)
    raw = bytearray(zs.read_bytes())
    raw[raw.find(b"AAAA") + 10] ^= 0xFF
    zs.write_bytes(bytes(raw))
    assert rommlib.file_hash_ok(str(zs), dict(rec, archive_members=[mem("Game (USA).gba", a)])) == (False, "zip")
    # truncated
    zt = tmp_path / "cut.zip"
    zt.write_bytes(zp.read_bytes()[:len(zp.read_bytes()) // 2])
    assert rommlib.file_hash_ok(str(zt), rec) == (False, "zip")
    # other archives RomM hashes by content: size only
    sz = tmp_path / "Game.7z"
    sz.write_bytes(b"7z..")
    assert rommlib.file_hash_ok(str(sz), {"sha1_hash": "0" * 40}) == (None, None)


# ------------------------------------------------------------ queue control

def _carts(n):
    return [{"id": 9000 + i, "name": "Cart %d" % i, "fs_name": "Cart %d.zip" % i,
             "fs_name_no_ext": "Cart %d" % i, "fs_size_bytes": 10} for i in range(n)]


def test_queue_reorder_pause_and_persist(isolated):
    cfg = dict(rommlib.DEFAULT_CONFIG, url="http://127.0.0.1:9", token="x")
    d = rommlib.Downloader(rommlib.Api.from_config(cfg), rommlib.Library(), cfg)
    d.hold_all()                                   # the worker starts nothing
    a, b, c = (d.enqueue(r, "gba") for r in _carts(3))
    d.move(c, "top")
    assert [j.name for j in d.jobs] == ["Cart 2", "Cart 0", "Cart 1"]
    d.move(a, "up")
    d.move(c, "down")
    assert [j.name for j in d.jobs] == ["Cart 0", "Cart 1", "Cart 2"]
    d.pause_job(b)
    assert b.state == "Paused"
    d.cancel_job(c)
    assert c.state == "Cancelled"
    assert d.clear_finished() == 1 and c not in d.jobs
    saved = json.load(open(rommlib.QUEUE_PATH))
    assert saved["held"] is True and [it["rom"]["name"] for it in saved["jobs"]] == ["Cart 0", "Cart 1"]
    # a new session keeps the queue paused (and so does the background worker)
    d2 = rommlib.Downloader(d.api, rommlib.Library(), cfg)
    assert d2.resume() == 2 and d2.hold
    assert all(j.state == "Queued" for j in d2.jobs)
    d2.release_all()
    assert not d2.hold and not json.load(open(rommlib.QUEUE_PATH))["held"]


def test_old_queue_format_still_loads(isolated):
    os.makedirs(rommlib.DATA_DIR)
    json.dump([{"rom": _carts(1)[0], "system": "gba"}], open(rommlib.QUEUE_PATH, "w"))
    assert rommlib.Downloader.saved_queue()[0]["system"] == "gba"
    assert rommlib.Downloader.saved_held() is False


# ------------------------------------------------------------ installed.json sharing

def test_installed_json_survives_concurrent_writers(isolated):
    """The app and rb_chd.py each hold a Library; neither may drop the
    other's change (regression: last writer won)."""
    app_lib, chd_lib = rommlib.Library(), rommlib.Library()
    app_lib.mark({"id": 1, "name": "A", "fs_name": "A.zip"}, "psx", ["/x/A"])
    chd_lib.rename_paths({"/x/A": "/x/A.chd"})
    app_lib.mark({"id": 2, "name": "B", "fs_name": "B.zip"}, "psx", ["/x/B"])
    inst = json.load(open(rommlib.STATE_PATH))
    assert inst["1"]["paths"] == ["/x/A.chd"] and inst["2"]["paths"] == ["/x/B"]


# ------------------------------------------------------------ audit

def test_audit_finds_damage(isolated, mock_url):
    api, d = _setup(mock_url)
    disc = _rom(api, "Ipsum Saga (Japan) (Disc 1)")
    cart = _rom(api, "Flaky Cart (USA)")
    import rommlib as rl
    rl.RETRY_DELAYS = (0.1, 0.1, 0.1)
    try:
        _wait([d.enqueue(disc, "psx"), d.enqueue(cart, "gba")])
    finally:
        rl.RETRY_DELAYS = (15, 60, 180)
    plats = {p["fs_slug"]: p["id"] for p in api.platforms()}
    by_sys = {"psx": [plats["ps1"]], "gba": [plats["gba"]]}
    findings, stats = rommlib.audit(api, by_sys)
    assert findings == [] and stats["matched"] == 2
    # break them: truncate the zip, cut the bin short, then remove it
    z = os.path.join(rommlib.ROMS_ROOT, "gba", cart["fs_name"])
    data = open(z, "rb").read()
    open(z, "wb").write(data[:len(data) // 3])
    folder = os.path.join(rommlib.ROMS_ROOT, "psx", disc["fs_name_no_ext"])
    binf = os.path.join(folder, disc["fs_name_no_ext"] + ".bin")
    open(binf, "r+b").truncate(1000)
    findings, _ = rommlib.audit(api, by_sys)
    probs = {(os.path.basename(f["path"]), f["severity"]): f["problem"] for f in findings}
    assert probs[(cart["fs_name"], "damaged")].startswith("Damaged zip")
    assert "incomplete" in probs[(os.path.basename(binf), "damaged")]
    assert all(f["rom"] and f["rom"]["id"] in (disc["id"], cart["id"]) for f in findings)
    os.remove(binf)
    findings, _ = rommlib.audit(api, by_sys)
    assert any(f["problem"].startswith("Missing track file") for f in findings)
    # deep: a same-size corrupted copy is caught by checksum
    _wait([d.enqueue(disc, "psx")])                     # re-download repairs it
    assert not [f for f in rommlib.audit(api, by_sys)[0] if f["system"] == "psx"]
    with open(binf, "r+b") as f:
        f.seek(100)
        f.write(b"\xff" * 16)
    assert not [f for f in rommlib.audit(api, {"psx": by_sys["psx"]})[0]]
    deep = rommlib.audit(api, {"psx": by_sys["psx"]}, deep=True)[0]
    assert [f["problem"] for f in deep] == ["Checksum doesn't match RomM"]


def test_audit_skips_converted_and_trimmed(isolated, mock_url):
    api, d = _setup(mock_url)
    disc = _rom(api, "Ipsum Saga (Japan) (Disc 3)")
    _wait([d.enqueue(disc, "psx")])
    folder = os.path.join(rommlib.ROMS_ROOT, "psx", disc["fs_name_no_ext"])
    # converted to CHD inside its folder: tracks gone on purpose
    for n in os.listdir(folder):
        os.remove(os.path.join(folder, n))
    open(os.path.join(folder, disc["fs_name_no_ext"] + ".chd"), "w").close()
    plats = {p["fs_slug"]: p["id"] for p in api.platforms()}
    assert rommlib.audit(api, {"psx": [plats["ps1"]]})[0] == []


# ------------------------------------------------------------ notifications

def test_background_notes(monkeypatch):
    import rb_dl
    sent = []
    monkeypatch.setattr(rommlib, "es_notify", lambda m: sent.append(m) or True)
    monkeypatch.setattr(rommlib, "es_running_game", lambda: "/userdata/roms/psx/Game.chd")

    class D:
        jobs = []
    n = rb_dl.Notes({"notify": True})
    for i, st in enumerate(["Done", "Done", "Failed", "Queued"]):
        j = rommlib.Job({"id": i, "name": "G%d" % i}, "gba")
        j.state = st
        D.jobs.append(j)
    n.collect(D)
    n.flush()
    assert sent == []                                   # a game is running: wait
    monkeypatch.setattr(rommlib, "es_running_game", lambda: "")
    n.collect(D)
    n.flush()
    assert sent == ["RomM Browser: 2 games ready: G0, G1; 1 failed: G2"]
    n.flush()
    assert len(sent) == 1
    off = rb_dl.Notes({"notify": False})
    off.collect(D)
    off.flush()
    assert len(sent) == 1


# ------------------------------------------------------------ manuals, achievements, sounds

def test_manual_lands_in_gamelist_and_leaves_with_the_game(isolated, mock_url):
    api, d = _setup(mock_url)
    cart = _rom(api, "Flaky Cart (USA)")
    monkey = rommlib.RETRY_DELAYS
    rommlib.RETRY_DELAYS = (0.1, 0.1, 0.1)
    try:
        _wait([d.enqueue(cart, "gba")])
    finally:
        rommlib.RETRY_DELAYS = monkey
    assert rommlib.ra_summary(cart) == (3, 40)
    q = [json.loads(l) for l in open(rommlib.GAMELIST_QUEUE)]
    assert q[0]["manual"].startswith("/assets/romm/resources/roms/")
    rb_finalize.merge(q, api, PSX_ES)
    sysdir = os.path.join(rommlib.ROMS_ROOT, "gba")
    g = ET.parse(os.path.join(sysdir, "gamelist.xml")).getroot().find("game")
    man = os.path.join(sysdir, g.findtext("manual"))
    assert g.findtext("manual") == "./manuals/Flaky Cart (USA)-manual.pdf"
    assert open(man, "rb").read().startswith(b"%PDF")
    # deleting the game takes its manual + cover with it
    paths, _ = d.library.uninstall(cart, "gba")
    rb_finalize.merge([json.loads(l) for l in open(rommlib.GAMELIST_QUEUE)][-1:], api, PSX_ES)
    assert not os.path.exists(man)
    assert ET.parse(os.path.join(sysdir, "gamelist.xml")).getroot().find("game") is None


def test_manual_path_is_never_the_scraper_url():
    assert rommlib.manual_path({"has_manual": True, "path_manual": "roms/7/1/manual/1.pdf",
                                "url_manual": "https://scraper.example/x?devpassword=y"}) == \
        "/assets/romm/resources/roms/7/1/manual/1.pdf"
    assert rommlib.manual_path({"has_manual": True, "path_manual": "roms/../../etc/x.pdf"}) is None
    assert rommlib.manual_path({"has_manual": False, "path_manual": "roms/7/1/manual/1.pdf"}) is None
    assert rommlib.manual_path({"has_manual": True, "url_manual": "https://x/y.pdf"}) is None
    assert rommlib.ra_summary({"ra_id": None}) is None


def test_sounds_synthesise(rb):
    import wave
    w = wave.open(rb.Sfx._wav((784, 1175), 42, 0.14, "sine"))
    assert w.getframerate() == 44100 and w.getnframes() > 3000
    sfx = rb.Sfx("normal")                 # dummy audio driver: loads or stays silent, never raises
    sfx.play("ok")
    sfx.set_level("off")
    sfx.play("move")
