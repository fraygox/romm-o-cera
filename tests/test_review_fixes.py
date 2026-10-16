"""Regression tests for bugs found by the 2026-10-11 full review (each failed
before its fix). Conversions are driven with a fake chdman."""
import errno
import json
import os
import stat
import sys

import pytest

import rommlib

FAKE_CHDMAN = r'''#!%s
import os, sys
a = sys.argv[1:]
if a[0] == "verify":
    sys.exit(0)
src, out = a[a.index("-i") + 1], a[a.index("-o") + 1]
data = open(src, "rb").read()
act = os.environ.get("FAKE_DURING")
if act == "replace":          # a fresh copy lands mid-conversion (Check games' download again)
    open(src + ".part", "wb").write(b"FRESH GOOD COPY")
    os.replace(src + ".part", src)
elif act == "delete":         # Free up space deletes the game mid-conversion
    os.remove(src)
open(out, "wb").write(b"CHD:" + data)
'''


@pytest.fixture
def chd(isolated, tmp_path, monkeypatch):
    """rb_chd with a fake chdman on PATH, nothing playing, no keep-free reserve."""
    b = tmp_path / "bin"
    b.mkdir()
    exe = b / "chdman"
    exe.write_text(FAKE_CHDMAN % sys.executable)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", "%s:%s" % (b, os.environ["PATH"]))
    import rb_chd
    monkeypatch.setattr(rb_chd, "running_game", lambda: "")
    monkeypatch.setattr(rommlib, "es_busy", lambda app_busy=False, get=None: "")   # nothing busy in ES
    monkeypatch.setattr(rb_chd.time, "sleep", lambda s: None)
    monkeypatch.setattr(rommlib, "keep_free_bytes", lambda cfg: 0)
    return rb_chd


def _w(p, data=b"x" * 4096):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb" if isinstance(data, bytes) else "w") as f:
        f.write(data)
    return p


def test_cue_track_outside_the_game_folder_is_never_touched(chd, tmp_path):
    """A cue naming a file outside its game (another game's track, or anything
    on the box): the task is refused, nothing is converted or deleted."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    other = _w(os.path.join(sysdir, "B", "B (Track 1).bin"))
    victim = _w(str(tmp_path / "system" / "batocera.conf"), b"keep me")
    a = _w(os.path.join(sysdir, "A", "A.cue"), 'FILE "../B/B (Track 1).bin" BINARY\n  TRACK 01 MODE2/2352\n')
    assert chd.convert({"system": "psx", "mode": "createcd", "sources": [a], "roots": [os.path.dirname(a)]}) is False
    loose = _w(os.path.join(sysdir, "Loose.cue"), 'FILE "%s" BINARY\n  TRACK 01 MODE2/2352\n' % victim)
    assert chd.convert({"system": "psx", "mode": "createcd", "sources": [loose], "roots": [loose]}) is False
    assert os.path.exists(other) and open(victim, "rb").read() == b"keep me"
    assert not any(n.endswith(".chd") for n in os.listdir(sysdir))
    assert "outside the game" in open(rommlib.LOG_PATH).read()


FAKE_7Z = r'''#!%s
import os, sys
a = sys.argv[1:]
plan = os.environ["FAKE_7Z_PLAN"]          # "link" | "through"
members = ["Game/d", "Game/game.bin"] if plan == "link" else ["Game/d/custom.sh", "Game/game.bin"]
if a[0] == "l":
    for m in members:
        print("Path = " + m)
    sys.exit(0)
out = next(x[2:] for x in a if x.startswith("-o"))
os.makedirs(os.path.join(out, "Game"), exist_ok=True)
open(os.path.join(out, "Game", "game.bin"), "wb").write(b"rom")
if plan == "link":                         # p7zip recreates a symlink entry
    os.symlink(os.environ["FAKE_7Z_OUTSIDE"], os.path.join(out, "Game", "d"))
else:                                      # a plain file whose path runs through an existing link
    os.makedirs(os.path.join(out, "Game", "d"), exist_ok=True)
    open(os.path.join(out, "Game", "d", "custom.sh"), "w").write("echo pwned\n")
'''


@pytest.mark.parametrize("plan", ["link", "through"])
def test_7z_can_neither_plant_a_link_nor_write_through_one(isolated, tmp_path, monkeypatch, plan):
    """A .7z/.rar from the server may hold symlink entries (p7zip restores
    them). None may be left in roms/, and no later archive may write through
    one an earlier download planted."""
    outside = tmp_path / "userdata-system"
    outside.mkdir()
    fake = tmp_path / "7z"
    fake.write_text(FAKE_7Z % sys.executable)
    fake.chmod(0o755)
    monkeypatch.setenv("FAKE_7Z_PLAN", plan)
    monkeypatch.setenv("FAKE_7Z_OUTSIDE", str(outside))
    d = rommlib.Downloader(None, rommlib.Library(), dict(rommlib.DEFAULT_CONFIG), persist=False)
    monkeypatch.setattr(d, "_7z", lambda: str(fake))
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    os.makedirs(os.path.join(sysdir, "Game"))
    if plan == "through":                  # planted by an earlier download (or an older version)
        os.symlink(str(outside), os.path.join(sysdir, "Game", "d"))
    arc = os.path.join(sysdir, "Game.7z")
    open(arc, "wb").write(b"7z")
    j = rommlib.Job({"id": 1, "name": "Game"}, "psx")
    with pytest.raises(rommlib.RommError):
        d._extract(arc, sysdir, j)
    assert os.listdir(str(outside)) == [], "an archive wrote outside roms/"
    if plan == "link":
        assert not os.path.islink(os.path.join(sysdir, "Game", "d")), "a symlink was left in roms/"
    assert not any(n.startswith(".rb-extract") for n in os.listdir(sysdir))       # staging cleaned up


def test_output_is_on_disk_before_the_originals_go(chd, monkeypatch):
    """Power loss after the originals were unlinked but before the CHD's data
    reached the disk left a 0-byte CHD and no originals (reproduced on a VM
    with sysrq-b). The output and its directory entry must be fsync'd first."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, "ps2")
    src = _w(os.path.join(sysdir, "Game (USA).iso"))
    out = os.path.join(sysdir, "Game (USA).chd")
    synced = set()
    real_fsync, real_remove = os.fsync, os.remove

    def fsync(fd):
        synced.add(os.path.realpath(os.readlink("/proc/self/fd/%d" % fd)))
        return real_fsync(fd)

    def remove(p, *a, **k):
        if os.path.realpath(p) == os.path.realpath(src):
            assert os.path.realpath(out) in synced, "original deleted before the CHD was fsync'd"
            assert os.path.realpath(sysdir) in synced, "original deleted before the rename was fsync'd"
        return real_remove(p, *a, **k)
    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "remove", remove)
    assert chd.convert({"system": "ps2", "mode": "createdvd", "sources": [src], "roots": [src]})
    assert not os.path.exists(src)


def test_a_source_replaced_mid_conversion_survives(chd, monkeypatch):
    """A fresh copy lands (os.replace) while the worker converts the old one:
    the fresh copy must stay, and no output of the old one may be kept."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, "ps2")
    src = _w(os.path.join(sysdir, "Game (USA).iso"), b"OLD DAMAGED COPY")
    monkeypatch.setenv("FAKE_DURING", "replace")
    assert chd.convert({"system": "ps2", "mode": "createdvd", "sources": [src], "roots": [src]}) is False
    assert open(src, "rb").read() == b"FRESH GOOD COPY"
    assert not os.path.exists(os.path.join(sysdir, "Game (USA).chd"))


def test_a_source_deleted_mid_conversion_isnt_brought_back(chd, monkeypatch):
    """Free up space deletes the game while it's converting: no output may
    reappear (and be renamed into installed.json and the gamelist)."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, "ps2")
    src = _w(os.path.join(sysdir, "Game (USA).iso"))
    monkeypatch.setenv("FAKE_DURING", "delete")
    assert chd.convert({"system": "ps2", "mode": "createdvd", "sources": [src], "roots": [src]}) is False
    assert os.listdir(sysdir) == []
    assert not os.path.exists(rommlib.GAMELIST_QUEUE) or "Game (USA).chd" not in open(rommlib.GAMELIST_QUEUE).read()


def test_check_games_redownload_keeps_the_game_it_repaired(isolated, rb):
    """A folder game whose cue is named differently from the folder: Check
    games flags the inner cue, the re-download refills the same folder, and
    the "replace the old copy" step must not then delete the fresh files."""
    import types
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    folder = os.path.join(sysdir, "Game (USA)")
    cue = _w(os.path.join(folder, "Game (USA) (Rev 1).cue"),
             'FILE "Game (USA) (Rev 1) (Track 1).bin" BINARY\nFILE "Game (USA) (Rev 1) (Track 2).bin" BINARY\n')
    for t in (1, 2):
        _w(os.path.join(folder, "Game (USA) (Rev 1) (Track %d).bin" % t), b"good" * 100)
    f = {"system": "psx", "path": cue, "severity": "damaged",
         "rom": {"id": 1, "fs_name": "Game (USA)", "fs_name_no_ext": "Game (USA)"}}
    replace = [cue] if rb.AuditScreen.renamed(f) else None
    assert replace == [cue]                         # what Check games really passes
    d = object.__new__(rommlib.Downloader)
    d.es, d.library = {"psx": {".cue", ".chd", ".m3u"}}, rommlib.Library()
    d._replace_old(types.SimpleNamespace(system="psx", replace=replace), [folder])
    assert os.path.isfile(cue) and len(os.listdir(folder)) == 3


def test_make_xiso_never_empties_an_image(isolated, monkeypatch):
    """Two overlapping make_xiso calls (rb_chd_plan over SSH while the worker
    packs the same image): the second saw the redump layout before the first
    cut it, its collapse failed past EOF and the copy fallback copied nothing
    over the game -> 0 bytes (200/200 on a VM). Simulated with the stale view."""
    p = os.path.join(rommlib.ROMS_ROOT, "xbox", "Game (USA).iso")
    os.makedirs(os.path.dirname(p))
    with open(p, "wb") as f:                      # already an XISO (magic at 0x10000)
        f.truncate(64 << 20)
        f.seek(0x10000)
        f.write(rommlib.XDVDFS_MAGIC)
    monkeypatch.setattr(rommlib, "xbox_partition", lambda path: 0x18300000)   # the stale view
    monkeypatch.setattr(rommlib, "_collapse_front", lambda path, n: False)
    try:
        rommlib.make_xiso(p, free=1 << 40)
    except (OSError, rommlib.RommError):
        pass
    assert os.path.getsize(p) == 64 << 20, "image emptied: %d bytes" % os.path.getsize(p)


def test_collapse_errors_other_than_unsupported_dont_fall_back_to_a_copy(isolated, monkeypatch):
    """Only "this filesystem can't" may fall back to copying; any other
    collapse failure (EINVAL: range past EOF) must not."""
    import ctypes
    p = os.path.join(rommlib.ROMS_ROOT, "xbox", "G.iso")
    os.makedirs(os.path.dirname(p))
    open(p, "wb").write(b"\0" * 8192)

    class Libc:
        def fallocate(self, *a):
            ctypes.set_errno(errno.EINVAL)
            return -1
    monkeypatch.setattr(ctypes, "CDLL", lambda *a, **k: Libc())
    with pytest.raises(OSError):
        rommlib._collapse_front(p, 4096)


def test_a_hand_edit_typo_in_config_never_loses_the_original(isolated):
    """docs/settings.md documents hand-editing config.json; a trailing comma
    made load_config fall back to defaults and the next save wiped the URL and
    token for good. The unreadable file must be kept beside it first."""
    os.makedirs(rommlib.DATA_DIR)
    text = '{"url": "https://romm.example.com", "token": "rmm_secret",}'
    open(rommlib.CONFIG_PATH, "w").write(text)
    cfg = rommlib.load_config()
    assert cfg["url"] == ""                          # unreadable: defaults, as before
    with pytest.raises(rommlib.ConfigUnreadable):    # e.g. any Settings change: refused now
        rommlib.save_config(cfg)
    assert open(rommlib.CONFIG_PATH).read() == text
    kept = [n for n in os.listdir(rommlib.DATA_DIR) if n.startswith("config.json.bad-")]
    assert len(kept) == 1 and open(os.path.join(rommlib.DATA_DIR, kept[0])).read() == text
    rommlib.load_config()                            # loading again doesn't pile up copies
    assert len([n for n in os.listdir(rommlib.DATA_DIR) if n.startswith("config.json.bad-")]) == 1


def test_a_truncated_installed_json_is_kept_before_the_next_write(isolated):
    """A half-written installed.json made a fresh process (rb_dl, rb_chd) start
    from {} and its next write replaced every record with one (39 -> 1)."""
    os.makedirs(rommlib.DATA_DIR)
    full = json.dumps({str(i): {"name": "Game %d" % i, "system": "psx", "paths": []} for i in range(39)})
    open(rommlib.STATE_PATH, "w").write(full[: len(full) // 2])       # cut short
    rommlib.Library().update(lambda inst: inst.update({"99": {"name": "New", "system": "psx", "paths": []}}))
    kept = [n for n in os.listdir(rommlib.DATA_DIR) if n.startswith("installed.json.bad-")]
    assert len(kept) == 1 and open(os.path.join(rommlib.DATA_DIR, kept[0])).read() == full[: len(full) // 2]


def _enospc_on(match):
    """open() that fails like a full disk when writing a path containing match
    (after truncating it, as a real open(..., "w") does)."""
    import builtins
    real_open = builtins.open

    def fake(path, mode="r", *a, **k):
        if isinstance(path, str) and match in path and ("w" in mode or "a" in mode):
            real_open(path, mode, *a, **k).close()
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_open(path, mode, *a, **k)
    return fake


def test_finalize_never_drops_the_queue_while_claiming_it(isolated, monkeypatch):
    """rb_finalize deleted gamelist-pending.jsonl before writing .work: a
    failed write (or a kill in between) lost every queued entry, CHD renames
    included (a converted game then loses its info)."""
    import builtins
    import rb_finalize
    os.makedirs(rommlib.DATA_DIR)
    monkeypatch.setattr(rb_finalize, "wait_idle", lambda *a: True)
    rommlib.append_gamelist({"system": "psx", "rename": {"/r/psx/A.cue": "/r/psx/A.chd"}, "hide": []})
    with monkeypatch.context() as m:                 # only open() breaks; the isolated paths stay
        m.setattr(builtins, "open", _enospc_on(".work.tmp"))
        with pytest.raises(OSError):
            rb_finalize.main()
    left = "".join(open(p).read() for p in (rommlib.GAMELIST_QUEUE, rommlib.GAMELIST_QUEUE + ".work")
                   if os.path.exists(p))
    assert "A.chd" in left, "queued game info (a CHD rename) is gone"


def test_a_failed_playlist_rewrite_leaves_the_old_playlist(isolated, monkeypatch):
    """fix_m3us (CHD renames, Check games' replace) rewrote .m3u files in
    place: a full disk or a kill mid-write left an empty playlist."""
    import builtins
    import rb_chd
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    os.makedirs(sysdir)
    m3u = os.path.join(sysdir, "Saga.m3u")
    open(m3u, "w").write("Saga (Disc 1).cue\nSaga (Disc 2).cue\n")
    with monkeypatch.context() as m:
        m.setattr(builtins, "open", _enospc_on("Saga.m3u"))
        with pytest.raises(OSError):
            rommlib.fix_m3us(sysdir, {os.path.join(sysdir, "Saga (Disc 1).cue"): os.path.join(sysdir, "Saga (Disc 1).chd")})
    assert open(m3u).read() == "Saga (Disc 1).cue\nSaga (Disc 2).cue\n", "playlist emptied"
    rommlib.fix_m3us(sysdir, {os.path.join(sysdir, "Saga (Disc 1).cue"): os.path.join(sysdir, "Saga (Disc 1).chd")})
    assert open(m3u).read() == "Saga (Disc 1).chd\nSaga (Disc 2).cue\n"


def test_an_interrupted_save_copy_never_leaves_a_truncated_save(chd, monkeypatch, tmp_path):
    """migrate_saves copied straight to the new name: a full disk or a kill
    mid-copy left a truncated save there that was never copied again (the
    exists check skipped it) and the emulator loads from then on."""
    saves = tmp_path / "saves" / "psx"
    _w(str(saves / "Game (Disc 1).srm"), b"S" * 131072)

    def full(src, dst, *a, **k):
        with open(src, "rb") as f, open(dst, "wb") as g:
            g.write(f.read(1000))
        raise OSError(errno.ENOSPC, "No space left on device")
    with monkeypatch.context() as m:
        m.setattr(chd.shutil, "copy2", full)
        with pytest.raises(OSError):
            rommlib.migrate_saves("psx", "/r/psx/G/Game (Disc 1).cue", "/r/psx/Game.m3u")
    rommlib.migrate_saves("psx", "/r/psx/G/Game (Disc 1).cue", "/r/psx/Game.m3u")
    assert (saves / "Game.srm").stat().st_size == 131072, "truncated save left under the new name"
    assert sorted(os.listdir(str(saves))) == ["Game (Disc 1).srm", "Game.srm"]          # no temp left


def test_queue_tasks_are_never_visible_half_written(isolated, monkeypatch):
    """The worker lists chd-queue/*.json while tasks are written; a task still
    being written under its final name was json-errored and deleted (the
    conversion silently lost). Every writer must publish it whole."""
    seen = []
    real = json.dump

    def spy(obj, f, *a, **k):
        if os.path.isdir(rommlib.CHD_QUEUE_DIR):
            seen.append([n for n in os.listdir(rommlib.CHD_QUEUE_DIR) if n.endswith(".json")])
        return real(obj, f, *a, **k)
    monkeypatch.setattr(json, "dump", spy)
    j = type("J", (), {"rom": {"id": 7}, "chd_task": None})()
    rommlib.Downloader._queue_conversion(None, j, "psx", "createcd", ["/x.cue"], ["/x.cue"])
    assert seen and all(s == [] for s in seen), "a task was visible under its final name before it was whole"
    t = json.load(open(os.path.join(rommlib.CHD_QUEUE_DIR, j.chd_task)))
    assert t["mode"] == "createcd" and t["sources"] == ["/x.cue"]
    # exclusive publishing (the planner): never overwrites an existing task
    first = rommlib.publish_queue_task({"a": 1}, "1-00-00000.json", exclusive=True)
    assert first and rommlib.publish_queue_task({"a": 2}, "1-00-00000.json", exclusive=True) is None
    assert json.load(open(os.path.join(rommlib.CHD_QUEUE_DIR, "1-00-00000.json"))) == {"a": 1}
    assert not [n for n in os.listdir(rommlib.CHD_QUEUE_DIR) if not n.endswith(".json")]   # no temp left
    # userdata without hard links (exFAT, FAT32): the planner still queues, still never overwrites

    def no_links(src, dst):
        raise PermissionError(1, "Operation not permitted")
    monkeypatch.setattr(os, "link", no_links)
    assert rommlib.publish_queue_task({"a": 3}, "1-00-00001.json", exclusive=True)
    assert rommlib.publish_queue_task({"a": 4}, "1-00-00001.json", exclusive=True) is None
    assert json.load(open(os.path.join(rommlib.CHD_QUEUE_DIR, "1-00-00001.json"))) == {"a": 3}
    assert not [n for n in os.listdir(rommlib.CHD_QUEUE_DIR) if not n.endswith(".json")]


def test_worker_records_each_task_as_itself(chd, monkeypatch):
    """A broken queue file was recorded with the PREVIOUS task's details as
    its retry payload ("Try again" re-queued the wrong game), and a task whose
    files were gone was a reasonless "failed" with a pointless retry."""
    q = rommlib.CHD_QUEUE_DIR
    os.makedirs(q)
    json.dump({"system": "ps2", "mode": "createdvd", "sources": ["/gone/A.iso"], "roots": ["/gone/A.iso"]},
              open(os.path.join(q, "1-a.json"), "w"))
    open(os.path.join(q, "2-b.json"), "w").write('{"system": "ps')          # cut short
    monkeypatch.setattr(chd, "finalize_later", lambda: None)
    import fcntl
    with open(os.path.join(rommlib.DATA_DIR, "chd.lock"), "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        chd.work(lk)
    res = {r["task"]: r for r in map(json.loads, open(os.path.join(rommlib.DATA_DIR, "chd-results.jsonl")))}
    assert res["2-b.json"]["retry"] is None and not res["2-b.json"]["sources"], res["2-b.json"]
    assert res["1-a.json"]["result"] == "skipped" and res["1-a.json"]["message"], res["1-a.json"]
    assert res["1-a.json"]["retry"] is None


def test_unquoted_cue_tracks_are_found(chd):
    """FILE without quotes is valid cue syntax: both parsers ignored it, so
    converted tracks stayed as orphans (and the folder was kept) and an
    uninstall left them behind."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    g = os.path.join(sysdir, "Game (USA)")
    _w(os.path.join(g, "track01.bin"))
    cue = _w(os.path.join(g, "Game (USA).cue"), "FILE track01.bin BINARY\n  TRACK 01 MODE2/2352\n")
    assert [os.path.basename(p) for p in rommlib.sheet_sources(cue)] == ["track01.bin"]
    assert chd.convert({"system": "psx", "mode": "createcd", "sources": [cue], "roots": [g]})
    assert sorted(os.listdir(sysdir)) == ["Game (USA).chd"], os.listdir(sysdir)


@pytest.mark.parametrize("body", [b"[]", b'"x"', b'{"path": 5}', b"null"])
def test_odd_es_running_game_replies_dont_crash_the_worker(isolated, monkeypatch, body):
    """rb_chd.running_game raised on a JSON reply that isn't an object (the
    worker crashed mid-queue); rommlib's twin already treats it as idle."""
    import rb_chd
    monkeypatch.setattr(rommlib, "es_get", lambda path: (200, body))
    assert rb_chd.running_game() == ""

    class R:
        status = 200

        def read(self):
            return body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: R())
    assert rommlib.es_running_game() == ""


@pytest.mark.parametrize("body", [b"[]", b'"x"', b'{"path": 5}', b"null"])
def test_odd_es_running_game_replies_dont_crash_finalize(isolated, monkeypatch, body):
    """rb_finalize.game_running raised AttributeError on a JSON reply that
    isn't an object (wait_idle and main() died with it) and called a
    non-string path a running game; its twins in rb_chd/rommlib treat both
    as idle."""
    import rb_finalize
    monkeypatch.setattr(rommlib, "es_get", lambda path: (200, body))
    assert rb_finalize.game_running() is False


def test_queued_conversions_start_even_with_chd_turned_off(isolated, monkeypatch):
    """rb_dl only spawned rb_chd when the CHD setting was on, but the queue
    also holds 3DS (on by default), RVZ, squashfs and .zar tasks: with CHD off
    they never ran in the background."""
    import subprocess
    import rb_dl
    import rb_finalize
    rommlib.publish_queue_task({"system": "3ds", "mode": "z3ds", "sources": ["/x.3ds"], "roots": ["/x.3ds"]},
                               "1-z.json")
    spawned = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **k: spawned.append(argv))
    monkeypatch.setattr(rb_finalize, "main", lambda: None)
    rb_dl.after_downloads(dict(rommlib.DEFAULT_CONFIG, chd=False), None)
    assert any("rb_chd.py" in " ".join(a) for a in spawned)
    assert rommlib.conversions_pending()


def test_downloads_screen_names_every_conversion_format(isolated, rb, monkeypatch):
    """Activity (then Downloads) said "CHD queued" / "compressed to CHD" for RVZ,
    .zcci, squashfs and .zar jobs, and never showed them as done."""
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    app = rb.App(None, headless=True)
    out = _w(os.path.join(rommlib.ROMS_ROOT, "3ds", "Game.zcci"))
    lib = app.library
    lib.update(lambda inst: inst.update({"5": {"name": "Game", "system": "3ds", "paths": [out]}}))
    j = rommlib.Job({"id": 5, "name": "Game"}, "3ds")
    rommlib.Downloader._queue_conversion(None, j, "3ds", "z3ds", ["/x.3ds"], ["/x.3ds"])
    assert j.chd_mode == "z3ds"
    j.state = rommlib.Job.DONE
    msg, _ = rb.DownloadsScreen(app).detail(j)
    assert "ZCCI queued" in msg and "CHD" not in msg
    os.remove(os.path.join(rommlib.CHD_QUEUE_DIR, j.chd_task))          # the worker finished it
    msg, _ = rb.DownloadsScreen(app).detail(j)
    assert "compressed to ZCCI" in msg


def test_a_retry_after_a_kill_finishes_the_job(chd, monkeypatch):
    """Killed (SIGTERM at shutdown) after the CHD landed but before the rename
    was recorded: the retry said "SKIP exists" forever and ES listed both."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, "ps2")
    src = _w(os.path.join(sysdir, "Game (USA).iso"))
    task = {"system": "ps2", "mode": "createdvd", "sources": [src], "roots": [src]}

    def die(*a, **k):
        raise SystemExit(0)
    with monkeypatch.context() as m:
        m.setattr(rommlib, "append_gamelist", die)
        with pytest.raises(SystemExit):
            chd.convert(task)
    assert os.path.exists(src) and os.path.exists(os.path.join(sysdir, "Game (USA).chd"))
    assert chd.convert(task) is True               # the queue file is still there: the worker retries
    assert os.listdir(sysdir) == ["Game (USA).chd"]


def test_an_old_output_beside_a_fresh_copy_is_still_left_alone(chd):
    """The resume above must not swallow the existing rule: an output older
    than its sources (a fresh re-download next to an old CHD) is skipped."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, "ps2")
    out = _w(os.path.join(sysdir, "Game (USA).chd"), b"OLD CHD")
    os.utime(out, (1, 1))
    src = _w(os.path.join(sysdir, "Game (USA).iso"))
    assert chd.convert({"system": "ps2", "mode": "createdvd", "sources": [src], "roots": [src]}) is False
    assert open(out, "rb").read() == b"OLD CHD" and os.path.exists(src)


def test_a_killed_worker_takes_its_converter_with_it(isolated, tmp_path):
    """SIGKILL of rb_chd left chdman (or any tool) running: no longer paused
    for games or memory-guarded, and a respawned worker started a second one
    on the same .part."""
    import signal
    import subprocess
    import time
    pidf = tmp_path / "child.pid"
    code = ("import sys; sys.path.insert(0, %r); import rb_chd; rb_chd.running_game = lambda: ''; "
            "rb_chd.memory_check = lambda *a: None; "
            "rb_chd.run_tool(['sh', '-c', 'echo $$ > %s; exec sleep 60'])"
            % (os.path.dirname(os.path.dirname(os.path.abspath(__file__))), pidf))
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    parent = subprocess.Popen([sys.executable, "-c", code], env=dict(os.environ, ROMM_BROWSER_DATA=rommlib.DATA_DIR))
    for _ in range(100):
        if pidf.exists() and pidf.read_text().strip():
            break
        time.sleep(0.05)
    child = int(pidf.read_text())
    parent.send_signal(signal.SIGKILL)
    parent.wait()
    for _ in range(40):                          # the kernel delivers the death signal right away
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        if open("/proc/%d/stat" % child).read().split()[2] == "Z":
            break                                # killed, waiting to be reaped
        time.sleep(0.05)
    else:
        os.kill(child, signal.SIGKILL)
        pytest.fail("the converter outlived the killed worker")


def test_a_download_stops_once_it_exceeds_the_files_size(isolated, tmp_path):
    """_fetch wrote for as long as the server kept sending and compared sizes
    only at the end: a server could fill /userdata (where saves live)."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    sent = {"n": 0}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            try:
                while sent["n"] < (64 << 20):
                    self.wfile.write(b"A" * (1 << 20))
                    sent["n"] += 1 << 20
            except OSError:
                pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        api = rommlib.Api("http://127.0.0.1:%d" % srv.server_address[1], "t")
        d = rommlib.Downloader(api, rommlib.Library(), dict(rommlib.DEFAULT_CONFIG), persist=False)
        j = rommlib.Job({"id": 1, "name": "Big"}, "gba")
        dest = os.path.join(rommlib.ROMS_ROOT, "gba", "Big.gba")
        os.makedirs(os.path.dirname(dest))
        with pytest.raises(rommlib.RommError):
            d._fetch(j, "/big", dest, 10, {"file_name": "Big.gba", "file_size_bytes": 10})
        assert not os.path.exists(dest + ".part") and not os.path.exists(dest)
        assert sent["n"] < (64 << 20), "the whole oversized body was swallowed"
    finally:
        srv.shutdown()


@pytest.mark.parametrize("members", [{"gamelist.xml": "<gameList/>"}, {"./Game.cue": "x", "./Game.bin": "y"},
                                     {"images/evil.png": "x"}])
def test_an_archive_cant_unpack_over_es_files_or_the_system_folder(isolated, members):
    """An archive whose single top entry is gamelist.xml, a media folder or "."
    unpacked straight into roms/<sys>: ES's gamelist overwritten, or the whole
    system folder recorded as this game (uninstall / conversion targets)."""
    import io
    import zipfile
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    gl = _w(os.path.join(sysdir, "gamelist.xml"), "<gameList><game/></gameList>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, data in members.items():
            z.writestr(n, data)
    arc = os.path.join(sysdir, "Evil.zip")
    open(arc, "wb").write(buf.getvalue())
    d = rommlib.Downloader(None, rommlib.Library(), dict(rommlib.DEFAULT_CONFIG), persist=False)
    d.es = {"psx": {".cue", ".chd", ".m3u"}}
    with pytest.raises(rommlib.RommError):
        d._postprocess_file(rommlib.Job({"id": 3, "name": "Evil"}, "psx"), arc)
    assert open(gl).read() == "<gameList><game/></gameList>"
    assert not os.path.exists(os.path.join(sysdir, "images"))


def test_an_unreadable_download_queue_is_kept(isolated):
    """A truncated download-queue.json was read as empty and the next save
    replaced it: the queued games were forgotten (and their .part files
    offered for deletion in Clean up leftovers)."""
    os.makedirs(rommlib.DATA_DIR)
    text = '{"held": false, "jobs": [{"rom": {"id": 1, "name": "A"}, "system": "psx"}'   # cut short
    open(rommlib.QUEUE_PATH, "w").write(text)
    assert rommlib.Downloader.saved_queue() == []
    kept = [n for n in os.listdir(rommlib.DATA_DIR) if n.startswith("download-queue.json.bad-")]
    assert len(kept) == 1 and open(os.path.join(rommlib.DATA_DIR, kept[0])).read() == text


def test_resume_writes_the_queue_once(isolated, monkeypatch):
    """resume() saved after each job it re-added, so the file on disk held 1,
    then 2 of 3 jobs while it ran: a kill then shortened the queue."""
    os.makedirs(rommlib.DATA_DIR)
    jobs = [{"rom": {"id": i, "name": "G%d" % i, "fs_name": "G%d.zip" % i}, "system": "gba"} for i in (1, 2, 3)]
    json.dump({"held": True, "jobs": jobs}, open(rommlib.QUEUE_PATH, "w"))
    d = rommlib.Downloader(None, rommlib.Library(), dict(rommlib.DEFAULT_CONFIG), persist=True)
    d.hold = True
    sizes = []
    real = d._save_queue

    def spy():
        real()
        sizes.append(len(json.load(open(rommlib.QUEUE_PATH))["jobs"]))
    monkeypatch.setattr(d, "_save_queue", spy)
    assert d.resume() == 3
    assert sizes and min(sizes) == 3, "the queue on disk shrank while resuming: %s" % sizes
    d.close()


def test_a_huge_cover_is_refused_without_reading_it_all(isolated):
    """fetch_cover read the whole response into memory (r.content) with no
    cap: a broken or hostile server could get the app OOM-killed."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    sent = {"n": 0}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            try:
                while sent["n"] < (256 << 20):
                    self.wfile.write(b"\x89PNG" + b"A" * ((1 << 20) - 4))
                    sent["n"] += 1 << 20
            except OSError:
                pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        api = rommlib.Api("http://127.0.0.1:%d" % srv.server_address[1], "t")
        assert api.fetch_cover({"id": 9, "path_cover_small": "/cover.png"}) is None
        assert sent["n"] < (256 << 20), "the whole oversized cover was read"
        assert not os.listdir(rommlib.COVER_CACHE)
    finally:
        srv.shutdown()


def test_ps2_cd_games_in_cue_bin_become_chd_on_download(isolated):
    """PS2 CD-based games come as cue/bin: rb_chd_plan converted them, but the
    download path only queued .iso for PS2, so downloads stayed cue/bin."""
    cfg = dict(rommlib.DEFAULT_CONFIG)
    d = rommlib.Downloader(None, rommlib.Library(), cfg, persist=False)
    d.es = {"ps2": {".iso", ".cue", ".chd", ".m3u"}}
    g = os.path.join(rommlib.ROMS_ROOT, "ps2", "Lorem (USA)")
    cue = _w(os.path.join(g, "Lorem (USA).cue"), 'FILE "Lorem (USA).bin" BINARY\n  TRACK 01 MODE2/2352\n')
    _w(os.path.join(g, "Lorem (USA).bin"))
    j = rommlib.Job({"id": 4}, "ps2")
    d._maybe_chd(j, "ps2", [cue, os.path.join(g, "Lorem (USA).bin")], [g])
    assert j.chd_task, "no conversion queued for a PS2 cue/bin game"
    t = json.load(open(os.path.join(rommlib.CHD_QUEUE_DIR, j.chd_task)))
    assert t["mode"] == "createcd" and t["sources"] == [cue]
    det = {"files": [{"file_name": "Lorem (USA).zip", "file_size_bytes": 1,
                      "archive_members": [{"name": "Lorem (USA).cue", "size": 100},
                                          {"name": "Lorem (USA).bin", "size": 600 << 20}]}]}
    assert rommlib.disc_layout(det, "ps2", cfg)["convert"] == "CHD"
    iso = _w(os.path.join(rommlib.ROMS_ROOT, "ps2", "Ipsum (USA).iso"))      # DVD games: unchanged
    j2 = rommlib.Job({"id": 5}, "ps2")
    d._maybe_chd(j2, "ps2", [iso], [iso])
    assert json.load(open(os.path.join(rommlib.CHD_QUEUE_DIR, j2.chd_task)))["mode"] == "createdvd"


def _app(path, version):
    os.makedirs(path)
    open(os.path.join(path, "rommlib.py"), "w").write('VERSION = "%s"\n' % version)
    open(os.path.join(path, "rommbrowser.pygame"), "w").write("# app\n")
    return path


def _second_move_dies(real):
    """A power cut right after the first rename of a swap (the dangerous moment)."""
    calls = []

    def move(src, dst):
        calls.append(src)
        if len(calls) == 2:
            raise SystemExit("power cut")
        return real(src, dst)
    return move


def test_an_update_cut_off_midway_never_leaves_no_app(isolated, tmp_path, monkeypatch):
    """install_staged renamed the app away, then the new one in: a power cut
    between the two left nothing at roms/pygame/rommbrowser (ES lists nothing
    to start, no in-app way back). The swap is now one atomic exchange."""
    app = _app(str(tmp_path / "roms" / "pygame" / "rommbrowser"), "0.0.1")
    staged = _app(str(tmp_path / "data" / "app-staging" / "rommbrowser"), "0.0.2")
    backup = str(tmp_path / "data" / "app-backup")
    monkeypatch.setattr(rommlib, "_move", _second_move_dies(rommlib._move))
    try:
        rommlib.install_staged(staged, app_dir=app, backup=backup)
    except SystemExit:
        pass
    assert rommlib._version_in(app) in ("0.0.1", "0.0.2"), "no app at all after an interrupted update"


def test_a_rollback_cut_off_midway_never_leaves_no_app(isolated, tmp_path, monkeypatch):
    app = _app(str(tmp_path / "roms" / "pygame" / "rommbrowser"), "0.0.2")
    backup = _app(str(tmp_path / "data" / "app-backup"), "0.0.1")
    monkeypatch.setattr(rommlib, "_move", _second_move_dies(rommlib._move))
    try:
        rommlib.rollback(app_dir=app, backup=backup)
    except SystemExit:
        pass
    assert rommlib._version_in(app) in ("0.0.1", "0.0.2") and os.path.isdir(backup)


def test_swaps_still_work_where_the_filesystem_cant_exchange(isolated, tmp_path, monkeypatch):
    """exFAT and cross-filesystem layouts can't exchange atomically: the old
    two-step swap is the fallback, and install/rollback behave as before."""
    app = _app(str(tmp_path / "roms" / "pygame" / "rommbrowser"), "0.0.1")
    staged = _app(str(tmp_path / "data" / "app-staging" / "rommbrowser"), "0.0.2")
    backup = str(tmp_path / "data" / "app-backup")

    def cant(a, b):
        raise OSError(errno.EINVAL, "not supported")
    monkeypatch.setattr(rommlib, "_exchange", cant)
    assert rommlib.install_staged(staged, app_dir=app, backup=backup) == "0.0.2"
    assert rommlib._version_in(backup) == "0.0.1"
    assert rommlib.rollback(app_dir=app, backup=backup) == "0.0.1"
    assert rommlib._version_in(backup) == "0.0.2"


def test_an_unreadable_config_is_never_saved_over_until_reset(isolated):
    os.makedirs(rommlib.DATA_DIR)
    text = '{"url": "https://romm.example.com", "token": "rmm_secret",}'
    open(rommlib.CONFIG_PATH, "w").write(text)
    assert rommlib.config_unreadable()
    with pytest.raises(rommlib.ConfigUnreadable):
        rommlib.save_config(rommlib.load_config())
    assert open(rommlib.CONFIG_PATH).read() == text            # untouched
    rommlib.reset_config()
    assert not rommlib.config_unreadable() and rommlib.load_config()["url"] == ""
    kept = [n for n in os.listdir(rommlib.DATA_DIR) if n.startswith("config.json.bad-")]
    assert len(kept) == 1 and open(os.path.join(rommlib.DATA_DIR, kept[0])).read() == text


def test_the_app_says_so_and_offers_a_clean_config(isolated, rb, monkeypatch):
    """With an unreadable config the app shows why at start, doesn't save
    Settings changes over the file, and Reset brings back a clean config."""
    import pygame
    os.makedirs(rommlib.DATA_DIR)
    open(rommlib.CONFIG_PATH, "w").write('{"token": "rmm_secret",}')
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    pygame.init()
    app = rb.App(None, headless=True)
    app.startup_checks()
    assert app.modal is not None and "can't be read" in app.modal.title
    app.modal = None
    app.cfg["sounds"] = "quiet"
    app.apply_config()
    assert open(rommlib.CONFIG_PATH).read() == '{"token": "rmm_secret",}'
    assert any("not saved" in t[0] for t in app.toasts)
    app.reset_config()
    assert not rommlib.config_unreadable()
    s = rb.SettingsScreen(app)
    assert any(it[0] == "resetcfg" for it in s.items() if it)


def _zip_bytes(members):
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, data in members.items():
            z.writestr(n, data)
    return buf.getvalue()


@pytest.mark.parametrize("case", ["other_game", "foreign", "same_game", "empty_leftover", "replacing"])
def test_an_archive_never_unpacks_into_another_games_folder(isolated, case):
    """An archive with one top folder unpacks straight into roms/<system>/ and
    that folder becomes this game's. Same name as a game already there: it
    wrote into it, and deleting the new entry later removed the other game.
    Only this game's own folder (re-download, repair), the copy Check games
    replaces, or an empty leftover may be unpacked into."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    folder = os.path.join(sysdir, "Game (USA)")
    lib = rommlib.Library()
    if case != "empty_leftover":
        _w(os.path.join(folder, "Game (USA).cue"), "ORIGINAL")
    else:
        os.makedirs(folder)
    owner = {"other_game": 99, "same_game": 7}.get(case)
    if owner:
        lib.update(lambda inst: inst.update({str(owner): {"name": "Game", "system": "psx", "paths": [folder]}}))
    arc = os.path.join(sysdir, "Game (USA) (Rev 1).zip")
    open(arc, "wb").write(_zip_bytes({"Game (USA)/Game (USA).cue": "NEW", "Game (USA)/Game (USA).bin": "B"}))
    d = rommlib.Downloader(None, lib, dict(rommlib.DEFAULT_CONFIG, chd=False), persist=False)
    d.es = {"psx": {".cue", ".chd", ".m3u"}}
    j = rommlib.Job({"id": 7, "name": "Game (Rev 1)"}, "psx")
    if case == "replacing":
        j.replace = [os.path.join(folder, "Game (USA).cue")]
    if case in ("other_game", "foreign"):
        with pytest.raises(rommlib.RommError, match="already on the box"):
            d._postprocess_file(j, arc)
        assert open(os.path.join(folder, "Game (USA).cue")).read() == "ORIGINAL"
        assert sorted(os.listdir(folder)) == ["Game (USA).cue"]
    else:
        assert d._postprocess_file(j, arc) == [folder]
        assert open(os.path.join(folder, "Game (USA).cue")).read() == "NEW"


def test_collections_show_how_many_of_their_games_are_on_the_box(isolated, rb, monkeypatch):
    """Inside a collection the green ticks showed which games were on the box
    (including ones copied over by hand, matched by file name), but the
    Collections list never said how many: platform cards do (from ES)."""
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    app = rb.App(None, headless=True)
    app.platforms = [{"id": 3, "_system": "xbox360", "name": "Xbox 360", "rom_count": 3}]
    app.platform_by_id = {3: app.platforms[0]}
    roms = [{"id": i, "name": "G%d" % i, "platform_id": 3, "fs_name": "G%d.zip" % i, "fs_name_no_ext": "G%d" % i}
            for i in (1, 2, 3)]
    _w(os.path.join(rommlib.ROMS_ROOT, "xbox360", "G2.iso"))           # copied over by hand
    app.library.mark(roms[0], "xbox360", [_w(os.path.join(rommlib.ROMS_ROOT, "xbox360", "G1.iso"))])
    monkeypatch.setattr(app, "bg", lambda fn, done=None, err=None: None)
    sc = rb.CollectionScreen(app)
    it = {"name": "Xbox 360", "kind": "", "group": "Your collections", "count": 3, "full": "Xbox 360",
          "param": "collection_id", "id": 11, "rom_ids": [1, 2, 3], "path_covers_small": []}
    st = sc.state(it)
    assert st["here"] == 1                           # at once: this app's own install, from RomM's game ids
    st["roms"] = roms
    sc.measure(st)
    assert st["here"] == 2 and st["rest"] == 0       # exact once its games are in: the hand-copied one too
    sc.kinds = [{"key": "yours", "label": "Your collections", "items": [it]}]
    sc.draw(app.canvas)                              # the tile and the panel show it (no crash)


def test_compress_games_already_here_scans_without_changing_anything_and_queues(isolated, monkeypatch):
    """On this box > Compress games already here: the scan lists what each
    enabled converter still applies to (by system) without touching files;
    queueing one game or a whole system publishes worker tasks."""
    import rb_chd_plan
    monkeypatch.setattr(rommlib, "load_config", lambda: dict(rommlib.DEFAULT_CONFIG, rvz=False))
    g = os.path.join(rommlib.ROMS_ROOT, "psx", "Lorem (USA)")
    cue = _w(os.path.join(g, "Lorem (USA).cue"), 'FILE "Lorem (USA).bin" BINARY\n  TRACK 01 MODE2/2352\n')
    _w(os.path.join(g, "Lorem (USA).bin"), b"x" * 5000)
    iso = _w(os.path.join(rommlib.ROMS_ROOT, "ps2", "Ipsum (USA).iso"), b"y" * 7000)
    gc = _w(os.path.join(rommlib.ROMS_ROOT, "gamecube", "Dolor (USA).iso"), b"z" * 3000)
    before = sorted(os.path.join(dp, f) for dp, _, fs in os.walk(rommlib.ROMS_ROOT) for f in fs)
    cands = rb_chd_plan.candidates()
    assert sorted(os.path.join(dp, f) for dp, _, fs in os.walk(rommlib.ROMS_ROOT) for f in fs) == before
    by = {c["system"]: c for c in cands}
    assert by["psx"]["mode"] == "createcd" and by["psx"]["size"] == len('FILE "Lorem (USA).bin" BINARY\n'
                                                                          '  TRACK 01 MODE2/2352\n') + 5000
    assert by["ps2"]["mode"] == "createdvd" and by["ps2"]["enabled"]
    assert by["gamecube"]["mode"] == "rvz" and not by["gamecube"]["enabled"]     # needs its Settings switch
    assert rb_chd_plan.queue([by["psx"]]) == 1
    assert rb_chd_plan.queue(cands) == 1                 # psx already queued; GameCube needs RVZ turned on
    tasks = [json.load(open(os.path.join(rommlib.CHD_QUEUE_DIR, n))) for n in sorted(os.listdir(rommlib.CHD_QUEUE_DIR))]
    assert sorted(t["mode"] for t in tasks) == ["createcd", "createdvd"]
    assert {c["system"] for c in rb_chd_plan.candidates()} == {"gamecube"}       # queued ones drop out
    assert cue and iso and gc


def test_compress_screens_list_systems_and_ask_before_a_whole_system(isolated, rb, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    app = rb.App(None, headless=True)
    cands = [{"system": "ps2", "root": "/r/ps2/A.iso", "files": ["/r/ps2/A.iso"], "name": None, "mode": "createdvd",
              "label": "CHD", "size": 7 << 30, "enabled": True, "setting": "x", "xiso": False, "cut": 0},
             {"system": "gamecube", "root": "/r/gc/B.iso", "files": ["/r/gc/B.iso"], "name": None, "mode": "rvz",
              "label": "RVZ", "size": 1 << 30, "enabled": False, "setting": "GameCube and Wii to RVZ",
              "xiso": False, "cut": 0}]
    sc = rb.CompressScreen(app)
    app.push(sc)
    sc._loaded(cands)
    assert [r["system"] for r in sc.rows] == ["ps2", "gamecube"]
    sc.draw(app.canvas)
    sc.action("x")
    assert app.modal is not None and "hours" in app.modal.body       # warns before a whole system
    app.modal = None
    sc.sel = 1
    sc.action("x")
    assert app.modal is None and any("Turn on" in t[0] for t in app.toasts)
    sc.action("ok")
    assert isinstance(app.stack[-1], rb.CompressSystemScreen) and app.stack[-1].system == "gamecube"
