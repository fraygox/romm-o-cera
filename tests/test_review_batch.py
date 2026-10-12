"""Fixes from the parity and helper review: failed loads say so, Xbox images
made bootable without squashfs, guarded Conversions queue edits, set deletes
wake waiting conversions, atomic CHD playlists, a missing chdman isn't damage,
startup survives a full disk, and firmware downloads reach the disk whole."""
import errno
import json
import os
import threading

import pytest

import rommlib
from test_disk import GiB, _xbox_image


@pytest.fixture
def app(isolated, rb, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    a = rb.App(None, headless=True)
    a.bg = lambda fn, done=None, err=None: _sync(fn, done, err, a)
    return a


def _sync(fn, done, err, app):
    try:
        res = fn()
    except Exception as e:
        (err or (lambda e: None))(e)
        return
    if done:
        done(res)


def _drawn(rb, monkeypatch, scr):
    """Every string scr.draw() puts on screen."""
    seen = []
    real = rb.text

    def text(surf, font, s, *a, **k):
        seen.append(s)
        return real(surf, font, s, *a, **k)
    monkeypatch.setattr(rb, "text", text)
    scr.draw(scr.app.canvas)
    return " | ".join(str(s) for s in seen)


def _boom(*a, **k):
    raise rommlib.RommError("Can't reach RomM (ConnectionError)")


# ------------------------------------------------------------ 1. a failed load isn't "all good"

CASES = [
    ("AuditScreen", (), [("rommlib", "audit")], "Everything looks right"),
    ("CompressScreen", (), [("rb", "compress_scan")], "Nothing left to compress"),
    ("InstalledScreen", (), [("rb", "installed_games")], "Measuring"),
    ("LeftoversScreen", (), [("rommlib", "find_leftovers")], "Nothing to clean up"),
    ("BiosScreen", (), [("rb", "bios_rows")], "No BIOS needed"),
    ("UnseenScreen", (), [("rommlib", "find_unseen")], "ES sees every game file"),
    ("FreeUpScreen", (), [("rommlib", "es_game_list")], "No games found"),
    ("CollectionScreen", (), [("api", "collections")], "No collections"),
]


@pytest.mark.parametrize("name,args,breaks,all_good", CASES, ids=[c[0] for c in CASES])
def test_a_failed_load_says_so_instead_of_nothing_here(app, rb, monkeypatch, name, args, breaks, all_good):
    for where, attr in breaks:
        monkeypatch.setattr({"rommlib": rommlib, "rb": rb, "api": app.api}[where], attr, _boom)
    monkeypatch.setattr(rb.time, "sleep", lambda s: None)
    scr = getattr(rb, name)(app, *args)
    app.push(scr)
    scr.enter()
    shown = _drawn(rb, monkeypatch, scr)
    assert all_good not in shown and "Can't reach RomM" in shown, shown
    assert scr.load_error


def test_a_list_that_loads_again_clears_the_error(app, rb, monkeypatch):
    monkeypatch.setattr(rommlib, "find_leftovers", _boom)
    scr = rb.LeftoversScreen(app)
    app.push(scr)
    scr.enter()
    assert scr.load_error
    monkeypatch.setattr(rommlib, "find_leftovers", lambda pend: [])
    scr.load()
    assert scr.load_error is None and "Nothing to clean up" in _drawn(rb, monkeypatch, scr)


def test_a_game_list_that_cant_ask_romm_isnt_empty(app, rb, monkeypatch):
    monkeypatch.setattr(app.api, "roms", _boom)
    monkeypatch.setattr(app.api, "get", _boom)
    scr = rb.GameListScreen(app, "Lorem", search="x")
    app.push(scr)
    scr.enter()
    shown = _drawn(rb, monkeypatch, scr)
    assert "No games found" not in shown and "Couldn't load" in shown


# ------------------------------------------------------------ 2. Xbox bootable without squashfs

def test_xbox_images_here_are_made_bootable_with_squashfs_off(isolated, monkeypatch):
    import rb_chd_plan
    monkeypatch.setattr(rommlib, "_collapse_front", lambda p, n: False)
    monkeypatch.setattr(rommlib, "free_bytes", lambda p=None: 100 * GiB)
    sysdir = os.path.join(rommlib.ROMS_ROOT, "xbox")
    os.makedirs(sysdir)
    _xbox_image(os.path.join(sysdir, "Lorem (USA).xiso"), 0)                       # ES can't list it
    _xbox_image(os.path.join(sysdir, "Ipsum (USA).iso"), rommlib.XBOX_PARTITIONS[0])   # full redump
    _xbox_image(os.path.join(sysdir, "Dolor (USA).iso"), 0)                        # bootable already
    cfg = dict(rommlib.DEFAULT_CONFIG)                     # Make bootable on, Xbox to squashfs off
    by = {os.path.basename(c["files"][0]): c for c in rb_chd_plan.candidates(["xbox"], cfg=cfg)}
    for n in ("Lorem (USA).xiso", "Ipsum (USA).iso"):
        assert by[n]["enabled"] and by[n]["prep"] and by[n]["label"] == "bootable .iso", n
    assert not by["Dolor (USA).iso"]["enabled"] and not by["Dolor (USA).iso"]["prep"]   # needs squashfs
    assert rb_chd_plan.queue(list(by.values())) == 2
    assert os.path.exists(os.path.join(sysdir, "Lorem (USA).iso"))
    assert rommlib.xbox_partition(os.path.join(sysdir, "Ipsum (USA).iso")) == 0
    assert not os.path.isdir(rommlib.CHD_QUEUE_DIR) or not os.listdir(rommlib.CHD_QUEUE_DIR)  # nothing queued
    cfg["xbox_bootable"] = False
    assert not [c for c in rb_chd_plan.candidates(["xbox"], cfg=cfg) if c["enabled"]]


# ------------------------------------------------------------ 3. Conversions queue edits

def _queued(name, task=None):
    os.makedirs(rommlib.CHD_QUEUE_DIR, exist_ok=True)
    with open(os.path.join(rommlib.CHD_QUEUE_DIR, name), "w") as f:
        json.dump(task or {"system": "psx", "mode": "createcd", "sources": ["/x"]}, f)


def test_queue_edits_never_touch_a_running_or_finished_task(isolated):
    _queued("1-00-00001.json")
    _queued("1-00-00002.json")
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    rommlib.atomic_write_json(rommlib.chd_current_path(), {"task": "1-00-00001.json"})
    assert not rommlib.queue_task_first("1-00-00001.json")          # converting now: left alone
    assert not rommlib.drop_queue_task("1-00-00001.json")
    assert rommlib.queue_task_first("1-00-00002.json")
    assert sorted(os.listdir(rommlib.CHD_QUEUE_DIR)) == ["0000000000000-1-00-00002.json", "1-00-00001.json"]
    assert not rommlib.drop_queue_task("1-00-00002.json")           # gone (renamed): no crash
    assert rommlib.drop_queue_task("0000000000000-1-00-00002.json")
    assert not rommlib.queue_task_first("nope.json")


def test_the_conversions_menu_survives_a_task_that_moved_on(app, rb):
    _queued("1-00-00001.json")
    scr = rb.ConversionsScreen(app)
    app.push(scr)
    row = next(r for r in rb.conversion_rows() if r["kind"] == "queued")
    os.remove(os.path.join(rommlib.CHD_QUEUE_DIR, row["name"]))       # the worker finished it meanwhile
    toasts = []
    app.toast = lambda msg, color=None: toasts.append(msg)
    scr.menu(row)
    for label, _, fn in [e for e in app.modal.entries if e]:
        if label in ("Convert next", "Remove from the queue"):
            fn()
    assert len(toasts) == 2 and all("converting now, or done" in t for t in toasts)


# ------------------------------------------------------------ 4. set deletes wake conversions

def test_deleting_a_disc_set_frees_space_for_waiting_conversions(app, monkeypatch):
    woke = []
    monkeypatch.setattr(app, "space_freed", lambda: woke.append(1))
    monkeypatch.setattr(app.library, "uninstall_group", lambda g, s: (["/x"], 5 << 30))
    after = []
    app.uninstall_group("g1", "psx", "Saga", lambda: after.append(1))
    assert woke == [1] and after == [1]


# ------------------------------------------------------------ 5. CHD playlists whole or not at all

def test_a_chd_playlist_is_written_whole_or_not_at_all():
    import inspect
    import rb_chd
    src = inspect.getsource(rb_chd)
    block = src[src.index("m3u exists, not overwriting"):][:600]
    assert "atomic_write_text" in block and 'open(primary, "w")' not in block


# ------------------------------------------------------------ 6. no chdman isn't damage

def test_without_chdman_a_full_check_flags_no_chd(isolated, monkeypatch, tmp_path):
    import subprocess
    calls = []

    class P:
        def __init__(self, code):
            self.returncode = code
    monkeypatch.setattr(subprocess, "run", lambda argv, **k: calls.append(argv) or P(127))   # nice: not found
    assert rommlib._chd_verify("chdman", "/x.chd") is None
    monkeypatch.setattr(subprocess, "run", lambda argv, **k: P(1))
    assert rommlib._chd_verify("chdman", "/x.chd") is False
    monkeypatch.setattr(subprocess, "run", lambda argv, **k: P(0))
    assert rommlib._chd_verify("chdman", "/x.chd") is True

    def gone(argv, **k):
        raise FileNotFoundError(argv[0])
    monkeypatch.setattr(subprocess, "run", gone)
    assert rommlib._chd_verify("chdman", "/x.chd") is None


def test_the_check_screen_says_chds_went_unverified(app, rb, monkeypatch):
    monkeypatch.setattr(rommlib, "audit", lambda *a, **k: ([], {"checked": 3, "matched": 3, "unmatched": 0,
                                                               "unverified": 2}))
    monkeypatch.setattr(rommlib, "save_audit", lambda *a: None)
    scr = rb.AuditScreen(app, deep=True)
    app.push(scr)
    assert "2 CHDs not verified (chdman isn't installed)" in _drawn(rb, monkeypatch, scr)


# ------------------------------------------------------------ 7. startup on a full disk

def test_startup_survives_a_full_disk(isolated, rb, monkeypatch):
    def full(*a, **k):
        raise OSError(errno.ENOSPC, "No space left on device")
    monkeypatch.setattr(rommlib, "append_gamelist", full)
    rb.register_self()                                          # no exception
    assert rb.read_marker(".registered") is None                # tried again next start
    monkeypatch.setattr(rommlib, "append_gamelist", lambda *e: None)
    rb.register_self()
    assert rb.read_marker(".registered") == rommlib.VERSION_LABEL
    monkeypatch.setattr(rommlib, "atomic_write_text", full)
    assert rb.write_marker(".update-notified", "v9") is False


# ------------------------------------------------------------ 8. firmware downloads

class _Resp:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    def iter_content(self, n):
        for i in range(0, len(self.body), n):
            yield self.body[i:i + n]

    def close(self):
        pass


def _fw_api(body, status=200):
    api = rommlib.Api("http://romm", "t")
    api.s.get = lambda url, **k: _Resp(body, status)
    return api


def test_firmware_downloads_reach_the_disk_before_they_are_renamed(tmp_path, monkeypatch):
    synced = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(fd), real(fd)))
    part = str(tmp_path / "bios.bin.part")
    _fw_api(b"x" * 3000).download_firmware(1, "bios.bin", part, 4096, "BIOS", "too big")
    assert open(part, "rb").read() == b"x" * 3000 and synced
    with pytest.raises(rommlib.RommError, match="too big"):
        _fw_api(b"x" * 5000).download_firmware(1, "bios.bin", part, 4096, "BIOS", "too big")
    assert not os.path.exists(part)
    with pytest.raises(rommlib.RommError, match="Key download failed: HTTP 404"):
        _fw_api(b"", 404).download_firmware(1, "k.zip", part, 4096, "Key", "too big")
    assert not os.path.exists(part)
