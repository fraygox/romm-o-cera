"""Compare with RomM: sets of discs, direction of a changed file, same names on two platforms, folders."""
import os

import pytest
import time

import rommlib
from conftest import whole_chd


def _touch(path, size=1):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:      # a .chd: a whole one (Compare reads its header)
        f.write(whole_chd() if path.endswith(".chd") else b"x" * size)
    return path


def _rom(rid, fs_name, size=100, **kw):
    stem, ext = os.path.splitext(fs_name)
    return dict({"id": rid, "name": stem, "fs_name": fs_name, "fs_name_no_ext": stem,
                 "fs_extension": ext.lstrip("."), "fs_size_bytes": size}, **kw)


def _utc(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _set(sysdir):
    lib = rommlib.Library()
    roms = [_rom(1, "Set (USA) (Disc 1).zip"), _rom(2, "Set (USA) (Disc 2).zip")]
    d1, d2 = _touch(os.path.join(sysdir, "Set (USA) (Disc 1).chd")), _touch(os.path.join(sysdir, "Set (USA) (Disc 2).chd"))
    m3u = os.path.join(sysdir, "Set (USA).m3u")
    open(m3u, "w").write("Set (USA) (Disc 1).chd\nSet (USA) (Disc 2).chd\n")
    lib.mark(roms[0], "psx", [d1, m3u])
    lib.mark(roms[1], "psx", [d2, m3u])
    return lib, roms, d1, d2, m3u


def test_a_set_names_the_disc_that_changed_and_its_own_files(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    lib, roms, d1, d2, m3u = _set(sysdir)
    roms[1]["fs_size_bytes"] = 99                                 # only disc 2 changed
    got = rommlib.compare_games(roms, lib.snapshot(), rommlib.local_games("psx"), "psx")
    it = got["items"][0]
    assert it["status"] == "newer" and it["rom"]["id"] == 2
    st = {x["rom"]["id"]: x for x in it["parts"]}
    assert st[1]["status"] == "compressed" and st[2]["status"] == "newer"
    assert st[2]["paths"] == [d2]                                  # not disc 1, not the shared playlist


def test_recheck_after_the_new_disc_is_in(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    lib, roms, d1, d2, m3u = _set(sysdir)
    roms[1]["fs_size_bytes"] = 99
    it = rommlib.compare_games(roms, lib.snapshot(), rommlib.local_games("psx"), "psx")["items"][0]
    lib.mark(roms[1], "psx", [d2, m3u])                           # the download finished: stamped with 99
    fresh = rommlib.recheck(it, lib, "psx")
    assert fresh["status"] == "compressed" and fresh["rom_ids"] == [1, 2]


def test_a_copy_the_app_did_not_install_never_claims_to_be_older(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    p = _touch(os.path.join(sysdir, "Plain (USA).iso"), 400)
    os.utime(p, (1000, 1000))
    plain = _rom(5, "Plain (USA).iso", size=500)
    run = lambda r: rommlib.compare_games([r], {}, rommlib.local_games("psx"), "psx")["items"][0]
    for upd in (None, _utc(500), _utc(time.time())):               # RomM's date says nothing: any edit moves it
        assert run(dict(plain, updated_at=upd))["status"] == "differs"


def test_a_recorded_download_is_newer_when_romms_file_changed(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    p = _touch(os.path.join(sysdir, "Plain (USA).iso"), 400)
    lib = rommlib.Library()
    lib.mark(_rom(5, "Plain (USA).iso", size=400), "psx", [p])
    now = _rom(5, "Plain (USA).iso", size=500)
    got = rommlib.compare_games([now], lib.snapshot(), rommlib.local_games("psx"), "psx")["items"][0]
    assert got["status"] == "newer"


def test_utc_seconds_honours_offsets():
    base = rommlib.utc_seconds("2026-10-01T12:00:00Z")
    assert rommlib.utc_seconds("2026-10-01T12:00:00+00:00") == base
    assert rommlib.utc_seconds("2026-10-01T14:00:00+02:00") == base
    assert rommlib.utc_seconds("2026-10-01T07:30:00.123-04:30") == base
    assert rommlib.utc_seconds("2026-10-01T12:00:00") == base      # RomM's zoneless times are UTC
    assert rommlib.utc_seconds("nope") is None and rommlib.utc_seconds(None) is None


def test_a_lone_disc_never_takes_the_files_of_a_disc_romm_lacks(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    lib, roms, d1, d2, m3u = _set(sysdir)
    roms[0]["fs_size_bytes"] = 99                                 # disc 1 changed; RomM has no disc 2 here
    for scope in ("collection", "platform"):
        got = rommlib.compare_games(roms[:1], lib.snapshot(), rommlib.local_games("psx"), "psx", scope=scope)
        it = got["items"][0]
        assert it["status"] == "newer" and [x["paths"] for x in it["parts"]] == [[d1]]


def test_a_game_matched_by_name_replaces_all_its_files(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    a, b = _touch(os.path.join(sysdir, "Solo (USA).bin"), 400), _touch(os.path.join(sysdir, "Solo (USA).cue"), 5)
    got = rommlib.compare_games([_rom(9, "Solo (USA).cue", size=7)], {}, rommlib.local_games("psx"), "psx")
    assert sorted(got["items"][0]["parts"][0]["paths"]) == sorted([a, b])


def test_same_name_on_two_platforms_uses_the_one_that_fits(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    _touch(os.path.join(sysdir, "Twin (USA).iso"), 300)
    a, b = _rom(1, "Twin (USA).iso", size=500, platform_id=1), _rom(2, "Twin (USA).iso", size=300, platform_id=2)
    got = rommlib.compare_games([a, b], {}, rommlib.local_games("psx"), "psx")["items"][0]
    assert got["status"] == "matches" and got["rom"]["id"] == 2


def test_folders_with_a_version_in_the_name_stay_apart(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "pc")
    _touch(os.path.join(sysdir, "Game v1.1", "a.bin"))
    _touch(os.path.join(sysdir, "Game v1.2", "a.bin"))
    _touch(os.path.join(sysdir, "Other.ps3", "a.bin"))
    _touch(os.path.join(sysdir, "Other.iso"))
    names = sorted(g["name"] for g in rommlib.local_games("pc"))
    assert len(names) == 3 and "Game v1.1" in names and "Game v1.2" in names


# -- the screen's download watch
from rbui.screens import compare as screen   # noqa: E402


class _Dl:
    def __init__(self):
        self.jobs = {}

    def job_for(self, rid):
        return self.jobs.get(rid)


class _App:
    def __init__(self):
        self.downloader, self.modal, self.library, self.rechecked = _Dl(), None, None, 0

    def bg(self, fn, ok, bad):
        self.rechecked += 1
        ok(None)


def _job(rid, state):
    j = rommlib.Job(_rom(rid, "X (USA).iso"), "psx")
    j.state = state
    return j


def _screen():
    s = screen.CompareScreen.__new__(screen.CompareScreen)
    s.app, s._watch, s._polls, s.result = _App(), [], 0, None
    return s


def _poll(s, n=1):
    for _ in range(n * s.WATCH_EVERY):
        s.poll_downloads()


def _watch(s, rids, modal="dlg"):
    s.app.modal = modal
    s._watch.append(s.watch_for({"path": "p"}, [{"rom": {"id": r}} for r in rids], modal))


def test_the_watch_follows_a_requeued_job_by_state_not_identity():
    s = _screen()
    j = _job(1, rommlib.Job.FAILED)
    s.app.downloader.jobs[1] = j
    _watch(s, [1])
    s.app.modal = None
    j.requeue()                                                    # the same object, active again
    _poll(s)
    assert s._watch and not s.app.rechecked
    j.state = rommlib.Job.DONE
    _poll(s)
    assert not s._watch and s.app.rechecked == 1


def test_the_watch_survives_the_dialog_being_replaced_and_a_cleared_job():
    s = _screen()
    _watch(s, [1, 2], modal="confirm")
    s.app.modal = "menu"                                           # a disc game's own menu took over
    _poll(s)
    assert s._watch
    s.app.modal = None
    s.app.downloader.jobs.update({1: _job(1, rommlib.Job.DOWNLOADING), 2: _job(2, rommlib.Job.DOWNLOADING)})
    _poll(s)
    s.app.downloader.jobs[1].state = rommlib.Job.DONE
    del s.app.downloader.jobs[2]                                   # "Clear finished" ran between looks
    _poll(s)
    assert not s._watch and s.app.rechecked == 1


def test_a_failed_download_ends_the_watch_and_a_start_that_never_comes_expires(monkeypatch):
    s = _screen()
    _watch(s, [1])
    s.app.modal = None
    s.app.downloader.jobs[1] = _job(1, rommlib.Job.DOWNLOADING)
    _poll(s)
    s.app.downloader.jobs[1].state = rommlib.Job.FAILED
    _poll(s)
    assert not s._watch and not s.app.rechecked
    s2 = _screen()
    _watch(s2, [7])
    s2.app.modal = None
    _poll(s2)
    assert s2._watch
    s2._watch[0]["closed"] -= screen.CompareScreen.START_WITHIN + 1
    _poll(s2)
    assert not s2._watch


def test_the_watch_looks_only_every_few_frames():
    s = _screen()
    _watch(s, [1])
    s.app.modal = None
    calls = []
    s.app.downloader.job_for = lambda rid: calls.append(rid)
    for _ in range(s.WATCH_EVERY - 1):
        s.poll_downloads()
    assert not calls
    s.poll_downloads()
    assert calls


def test_local_games_carry_no_unused_mtime(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    _touch(os.path.join(sysdir, "Plain (USA).iso"), 5)
    assert all("mtime" not in g for g in rommlib.local_games("psx"))


class _Api:
    def rom(self, rid):
        return {"id": rid, "name": "Disc %d" % rid}


class _Lib:
    def __init__(self, groups):
        self.groups = groups

    def group_for(self, rid):
        return self.groups.get(rid)


class _GetApp(_App):
    def __init__(self, groups):
        super().__init__()
        self.library, self.api, self.stack, self.started, self.toasts = _Lib(groups), _Api(), [], [], []

    def toast(self, *a, **k):
        self.toasts.append(a)

    def bg(self, fn, ok, bad):
        ok(fn())

    def start_download(self, rom, replace=None, group=None):
        self.started.append((rom["id"], replace, group))


def _item_of(parts):
    return {"status": "newer", "name": "Set", "path": "/r/Set.m3u", "paths": [], "size": 1, "rom": None,
            "parts": [{"rom": {"id": i}, "status": "newer", "paths": list(p)} for i, p in parts]}


def test_get_newer_keeps_a_disc_in_its_set_and_skips_files_nobody_owns(monkeypatch):
    group = {"id": "g", "base": "Set", "rom_ids": [1, 2]}
    s = _screen()
    s.app = _GetApp({1: group, 2: group})
    s.app.stack.append(s)
    shown = []
    monkeypatch.setattr(screen, "DownloadConfirm", lambda app, rom, replace=None, group=None: shown.append(
        (rom["id"], replace, group)) or "dlg")
    it = _item_of([(1, ["/r/a.chd"]), (2, [])])                    # disc 2's files are shared: nothing of its own
    assert [x["rom"]["id"] for x in screen.replaceable(it)] == [1]
    s.get_newer(it)
    assert shown == [(1, ["/r/a.chd"], group)]
    s.app.modal = None
    s.get_newer(_item_of([(1, ["/r/a.chd"]), (2, ["/r/b.chd"])]))  # two discs: one confirm, then each replaced in its set
    s.app.modal.action("ok")
    assert s.app.started == [(1, ["/r/a.chd"], group), (2, ["/r/b.chd"], group)]


def test_get_newer_with_nothing_of_its_own_does_not_become_a_plain_download():
    s = _screen()
    s.app = _GetApp({})
    s.app.stack.append(s)
    it = _item_of([(1, [])])
    assert screen.replaceable(it) == []
    s.get_newer(it)
    assert s.app.started == [] and s.app.modal is None


def test_a_set_disc_never_owns_the_playlist(isolated):
    """Only one disc's record still holds the playlist (another was fetched again by an earlier
    version): it's still not that disc's own file, or Get the newer one would delete the set."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    lib = rommlib.Library()
    group = {"id": "g", "base": "Set (USA)", "rom_ids": [1, 2], "title": "Set"}
    roms = [_rom(1, "Set (USA) (Disc 1).chd"), _rom(2, "Set (USA) (Disc 2).chd")]
    d1, d2 = _touch(os.path.join(sysdir, "Set (USA) (Disc 1).chd")), _touch(os.path.join(sysdir, "Set (USA) (Disc 2).chd"))
    m3u = os.path.join(sysdir, "Set (USA).m3u")
    open(m3u, "w").write("Set (USA) (Disc 1).chd\nSet (USA) (Disc 2).chd\n")
    lib.mark(roms[0], "psx", [d1, m3u], group=group)

    def lost(inst):                                   # disc 2's record without the playlist
        inst["2"] = dict(inst["1"], paths=[d2], fs_name=roms[1]["fs_name"])
    lib.update(lost)
    roms[0]["fs_size_bytes"] = 77
    it = rommlib.compare_games(roms, lib.snapshot(), rommlib.local_games("psx"), "psx")["items"]
    assert len(it) == 1
    own = {x["rom"]["id"]: x["paths"] for x in it[0]["parts"]}
    assert own == {1: [d1], 2: [d2]}


def _zip(path, member, data):
    import zipfile
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(member, data)
    return path


def test_a_download_cut_short_behind_the_apps_back_differs(isolated):
    """Its record still holds RomM's size and hashes from the day it came: the file as it is
    now decides, never the stamp."""
    import zlib
    sysdir = os.path.join(rommlib.ROMS_ROOT, "gba")
    data = os.urandom(20000)
    p = _zip(os.path.join(sysdir, "Cut (World).zip"), "Cut (World).gba", data)
    size = os.path.getsize(p)
    rom = _rom(7, "Cut (World).zip", size=size, crc_hash="%08x" % zlib.crc32(data))
    lib = rommlib.Library()
    lib.mark(rom, "gba", [p])
    run = lambda: rommlib.compare_games([rom], lib.snapshot(), rommlib.local_games("gba"), "gba")["items"][0]
    assert run()["status"] == "matches"
    with open(p, "r+b") as f:
        f.truncate(size // 2)
    it = run()
    assert it["status"] == "differs" and it["parts"][0]["paths"] == [p]      # Get RomM's copy replaces it
    # RomM's file changed too: getting the newer one is still the thing to do
    assert rommlib.compare_games([dict(rom, fs_size_bytes=size + 9)], lib.snapshot(),
                                 rommlib.local_games("gba"), "gba")["items"][0]["status"] == "newer"


def test_an_unpacked_or_compressed_download_isnt_called_cut(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "gba")
    p = _touch(os.path.join(sysdir, "Unpacked (World).gba"), 50)
    lib = rommlib.Library()
    rom = _rom(8, "Unpacked (World).zip", size=30)                 # RomM's zip, unpacked here
    lib.mark(rom, "gba", [p])
    assert rommlib.compare_games([rom], lib.snapshot(), rommlib.local_games("gba"), "gba")["items"][0][
        "status"] == "matches"


def test_a_copy_renamed_by_hand_is_romms_game_by_its_hash(isolated, rb):
    import hashlib
    import zlib
    sysdir = os.path.join(rommlib.ROMS_ROOT, "gba")
    data = os.urandom(30000)
    p = _zip(os.path.join(sysdir, "Mine.zip"), "Homebrew (World).gba", data)
    size = os.path.getsize(p)
    rom = _rom(3, "Homebrew (World) (v1.0).zip", size=size, crc_hash="%08x" % zlib.crc32(data))
    other = _rom(4, "Other (World).zip", size=size, crc_hash="deadbeef")      # the same size, not the same game
    got = rommlib.compare_games([other, rom], {}, rommlib.local_games("gba"), "gba")
    it = got["items"][0]
    assert it["status"] == "matches" and it["renamed"] and it["rom"]["id"] == 3 and it["paths"] == [p]
    assert rb.compare.say(it) == "The same file RomM has, here under another name. Nothing to do."
    assert os.path.exists(p)                                       # never renamed
    # by sha1 on a plain file; a different size or hash stays "not in RomM"
    q = _touch(os.path.join(sysdir, "Plain.gba"), 64)
    sha = hashlib.sha1(b"x" * 64).hexdigest()
    plain = _rom(5, "Plain (World).gba", size=64, sha1_hash=sha.upper())
    st = {i["name"]: i for i in rommlib.compare_games([plain, rom], {}, rommlib.local_games("gba"), "gba")["items"]}
    assert st["Plain.gba"]["status"] == "matches" and st["Plain.gba"]["renamed"]
    for wrong in (dict(plain, sha1_hash="0" * 40), dict(plain, fs_size_bytes=65)):
        st = {i["name"]: i for i in rommlib.compare_games([wrong], {}, rommlib.local_games("gba"), "gba")["items"]}
        assert st["Plain.gba"]["status"] == "not_in_romm" and not st["Plain.gba"]["renamed"]
    assert os.path.exists(q)


def test_a_renamed_copy_never_takes_a_game_already_here(isolated):
    import zlib
    sysdir = os.path.join(rommlib.ROMS_ROOT, "gba")
    data = os.urandom(5000)
    a = _zip(os.path.join(sysdir, "Twice (World).zip"), "t.gba", data)
    _zip(os.path.join(sysdir, "Copy.zip"), "t.gba", data)
    rom = _rom(6, "Twice (World).zip", size=os.path.getsize(a), crc_hash="%08x" % zlib.crc32(data))
    st = {i["name"]: i["status"] for i in rommlib.compare_games([rom], {}, rommlib.local_games("gba"), "gba")["items"]}
    assert st == {"Twice (World).zip": "matches", "Copy.zip": "not_in_romm"}


@pytest.mark.parametrize("ours", [False, True])
def test_a_chd_that_isnt_a_whole_one_never_matches(isolated, ours):
    """Random bytes named like RomM's game (.chd), or the app's own CHD cut short: Compare said
    "Matches RomM (compressed)" while Check games called it damaged."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    rom = _rom(1, "Wave (World).zip")
    chd = os.path.join(sysdir, "Wave (World).chd")
    os.makedirs(sysdir, exist_ok=True)
    open(chd, "wb").write(whole_chd()[:60] if ours else os.urandom(3000))
    lib = rommlib.Library()
    if ours:
        lib.mark(rom, "psx", [chd])
    it = rommlib.compare_games([rom], lib.snapshot(), rommlib.local_games("psx"), "psx")["items"][0]
    assert it["status"] == "differs"


def test_a_folder_of_notes_or_of_another_game_named_like_romms_never_matches(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    rom = _rom(1, "Pyra (USA).zip")
    d = os.path.join(sysdir, "Pyra (USA)")
    _touch(os.path.join(d, "Pyra manual.pdf"))
    got = rommlib.compare_games([rom], {}, rommlib.local_games("psx"), "psx")
    assert got["items"] == [] or all(i["status"] != "matches" for i in got["items"])
    _touch(os.path.join(d, "MyGame.cue"))
    got = rommlib.compare_games([rom], {}, rommlib.local_games("psx"), "psx")
    assert [i["status"] for i in got["items"]] == ["not_in_romm"]


def test_a_folders_dots_arent_its_format(isolated):
    """"Breaker 1.1 (USA)/" showed as "On this box: 1 (USA) (UNL)"."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    rom = _rom(1, "Breaker 1.1 (USA).zip")
    _touch(os.path.join(sysdir, "Breaker 1.1 (USA)", "Breaker 1.1 (USA).cue"))
    assert rommlib.box_copy(rommlib.Library(), rom, "psx")["format"] == "folder"
