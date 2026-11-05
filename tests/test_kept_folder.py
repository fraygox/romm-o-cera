"""A game's folder kept for the owner's files once the game was compressed to
one file beside it (or a folder made by hand with only notes in it) isn't the
game: Check games doesn't call it damaged, the library doesn't count it as on
the box, a download of the game again unpacks beside the owner's files (never
over them) and replaces the copy its record names, a refused unpack leaves no
archive behind, and Check games' row says when its download failed."""
import os
import threading
import types
import zipfile

import pytest

import rommlib
from test_audit_fixes import Api, _put


def _w(p, data=b""):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    return p


def _zip(path, members):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        for n, data in members.items():
            z.writestr(n, data)
    return path


def _downloader():
    d = rommlib.Downloader.__new__(rommlib.Downloader)     # no worker thread
    d.api, d.cfg, d.es, d.unopenable = None, dict(rommlib.DEFAULT_CONFIG, chd=False), {}, {}
    d.library, d.persist, d.qlock, d.cv = rommlib.Library(), False, threading.Lock(), threading.Condition()
    d.hold, d.rate_limit, d.unpacking_bytes, d.jobs, d._closed = False, 0, 0, [], False
    return d


GAME = "Lamp Run (USA)"
CUE = 'FILE "%s.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n' % GAME
ROM = {"id": 5, "name": "Lamp Run", "fs_name": GAME + ".zip", "fs_name_no_ext": GAME}


def _sysdir():
    return os.path.join(rommlib.ROMS_ROOT, "psx")


def _compressed_with_manual():
    """What the CHD worker leaves: the CHD beside the game's folder, kept for the
    owner's manual, and the record pointing at the CHD."""
    chd = _w(os.path.join(_sysdir(), GAME + ".chd"), b"chd")
    manual = _w(os.path.join(_sysdir(), GAME, "Lamp Run manual.pdf"), b"manual")
    rommlib.Library().mark(ROM, "psx", [chd])
    return chd, manual


# ------------------------------------------------------------ (a) Check games

def test_check_games_doesnt_call_a_kept_folder_a_damaged_game(isolated, monkeypatch):
    monkeypatch.setattr(rommlib, "es_hashes", lambda system: {})
    _compressed_with_manual()
    api = Api([(1, GAME + ".zip", [{"file_name": GAME + ".zip", "file_size_bytes": 100, "category": None,
                                    "archive_members": [{"name": GAME + ".cue", "size": len(CUE)},
                                                        {"name": GAME + ".bin", "size": 2352}]}])])
    findings, stats = rommlib.audit(api, {"psx": [1]})
    folder = os.path.join(_sysdir(), GAME)
    assert not [f for f in findings if f["path"].startswith(folder + os.sep) or "Missing" in f["problem"]]
    assert stats["checked"] == 1                       # the CHD; the folder isn't a game of its own


def test_a_folder_with_a_game_file_left_in_it_is_still_checked(isolated, monkeypatch):
    monkeypatch.setattr(rommlib, "es_hashes", lambda system: {})
    _w(os.path.join(_sysdir(), GAME, GAME + ".cue"), CUE.encode())    # its track gone
    api = Api([(1, GAME + ".zip", [{"file_name": GAME + ".zip", "file_size_bytes": 100, "category": None,
                                    "archive_members": [{"name": GAME + ".cue", "size": len(CUE)},
                                                        {"name": GAME + ".bin", "size": 2352}]}])])
    findings, _ = rommlib.audit(api, {"psx": [1]})
    assert any("Missing" in f["problem"] for f in findings)


# ------------------------------------------------------------ (d) on the box?

def test_a_folder_holding_only_notes_isnt_the_game_on_the_box(isolated):
    _w(os.path.join(_sysdir(), GAME, "notes.txt"), b"my notes")
    lib = rommlib.Library()
    assert not lib.is_installed(ROM, "psx")
    assert lib.paths_for(ROM, "psx") == []
    assert not rommlib.holds_game(os.path.join(_sysdir(), GAME), "psx")
    _w(os.path.join(_sysdir(), GAME, "images", GAME + ".bin"), b"not a game: a media folder")
    lib.rescan("psx")
    assert not lib.is_installed(ROM, "psx")
    cue = _w(os.path.join(_sysdir(), GAME, GAME + ".cue"), CUE.encode())
    lib.rescan("psx")
    assert lib.is_installed(ROM, "psx") and rommlib.holds_game(cue, "psx")
    assert lib.paths_for(ROM, "psx") == [os.path.join(_sysdir(), GAME)]


# ------------------------------------------------------------ (b) downloading it again

def _download_again(d, members=None):
    arc = _zip(os.path.join(_sysdir(), GAME + ".zip"),
               members or {GAME + ".cue": CUE, GAME + ".bin": "B" * 2352})
    j = rommlib.Job(dict(ROM), "psx")
    j.made = [arc]                                      # the job's own download
    d.jobs.append(j)
    return j, arc


def test_downloading_it_again_unpacks_beside_the_owners_files_and_replaces_the_chd(isolated):
    chd, manual = _compressed_with_manual()
    d = _downloader()
    j, arc = _download_again(d)
    d._post(j, {"kind": "file", "path": arc, "heavy": True})
    assert j.state == rommlib.Job.DONE, j.message
    folder = os.path.join(_sysdir(), GAME)
    assert sorted(os.listdir(folder)) == sorted([GAME + ".cue", GAME + ".bin", "Lamp Run manual.pdf"])
    assert open(manual, "rb").read() == b"manual"          # the owner's file stays as it was
    assert not os.path.exists(arc)
    assert not os.path.exists(chd)                         # our own old copy: replaced, as Download again does
    assert d.library.snapshot()["5"]["paths"] == [folder]


def test_an_owners_file_is_never_unpacked_over(isolated):
    _w(os.path.join(_sysdir(), GAME, "readme.txt"), b"the owner's")
    d = _downloader()
    j, arc = _download_again(d, {GAME + ".cue": CUE, GAME + ".bin": "B" * 2352, "readme.txt": "the archive's"})
    with pytest.raises(rommlib.RommError, match="readme.txt is already in"):
        d._post(j, {"kind": "file", "path": arc, "heavy": True})
    folder = os.path.join(_sysdir(), GAME)
    assert os.listdir(folder) == ["readme.txt"] and open(os.path.join(folder, "readme.txt")).read() == "the owner's"
    assert not os.path.exists(arc)                        # a refused unpack leaves no archive in roms/


def test_a_refused_unpack_leaves_no_archive_behind(isolated):
    """Another game's folder (a game file in it, not ours): refused, and the job's
    archive goes, where ES (no zips on this system) would never show it."""
    other = _w(os.path.join(_sysdir(), GAME, GAME + ".cue"), b"someone else's copy")
    d = _downloader()
    j, arc = _download_again(d)
    with pytest.raises(rommlib.RommError, match="already on the box") as e:
        d._post(j, {"kind": "file", "path": arc, "heavy": True})
    d._failed(j, e.value)
    assert j.state == rommlib.Job.FAILED
    assert not os.path.exists(arc) and arc not in j.made
    assert open(other, "rb").read() == b"someone else's copy"


def test_a_file_there_before_the_job_isnt_deleted_when_its_unpack_is_refused(isolated):
    _w(os.path.join(_sysdir(), GAME, GAME + ".cue"), b"someone else's copy")
    d = _downloader()
    j, arc = _download_again(d)
    j.made = []                                           # the archive was there before the job
    with pytest.raises(rommlib.RommError):
        d._post(j, {"kind": "file", "path": arc, "heavy": True})
    assert os.path.exists(arc)


def test_an_unpack_beside_the_owners_files_cut_short_never_deletes_them(isolated):
    _compressed_with_manual()
    d = _downloader()
    j, arc = _download_again(d)
    real = d._move_into_place

    def killed(j_, moves, archive, lands, keep=False):
        real(j_, moves[:1], archive, lands, keep=keep)    # one file in, then the box lost power
        raise rommlib.Cancelled()
    d._move_into_place = killed
    with pytest.raises(rommlib.Cancelled):
        d._post(j, {"kind": "file", "path": arc, "heavy": True})
    d._failed(j, rommlib.Cancelled())                     # the cancel's clean-up
    assert os.path.exists(os.path.join(_sysdir(), GAME, "Lamp Run manual.pdf"))


# ------------------------------------------------------------ (c) Check games' row

def test_check_games_row_says_its_download_failed(isolated, rb):
    job = rommlib.Job(dict(ROM), "psx")
    app = types.SimpleNamespace(downloader=types.SimpleNamespace(enqueue=lambda *a, **k: job),
                                library=rommlib.Library())
    rommlib.Library().mark(ROM, "psx", [_w(os.path.join(_sysdir(), GAME + ".chd"), b"chd")])   # the app's own
    scr = rb.AuditScreen.__new__(rb.AuditScreen)
    scr.app, scr.queued = app, rb.QueuedAgain()
    f = {"path": os.path.join(_sysdir(), GAME + ".chd"), "system": "psx", "severity": "damaged",
         "problem": "Incomplete disc image", "rom": dict(ROM)}
    scr.findings = [f]
    scr.redownload(f)
    assert f["path"] in scr.queued and not scr.pickable(f) and scr.queued.failed(f["path"]) is None
    job.state, job.message = rommlib.Job.FAILED, "Lamp Run (USA) is already on the box under that name"
    assert f["path"] not in scr.queued and scr.pickable(f)
    assert scr.queued.failed(f["path"]) == job.message
    assert scr.redownloadable() == [f]                     # downloadable again
    job.state = rommlib.Job.CANCELLED
    assert f["path"] not in scr.queued and scr.queued.failed(f["path"]) is None


def test_check_games_download_again_replaces_the_broken_file_shown(isolated, rb, monkeypatch):
    """A junk .chd named like RomM's zipped cue/bin: Download again names it to replace (the
    downloader never deletes what the new copy holds), and the row says downloaded once done."""
    import types
    got = []
    app = types.SimpleNamespace(library=rommlib.Library(), downloader=types.SimpleNamespace(
        enqueue=lambda rom, system, replace=None: got.append(replace) or types.SimpleNamespace(state=rommlib.Job.DONE)))
    monkeypatch.setattr(rb.AuditScreen, "ours", lambda self, f: True)
    scr = rb.AuditScreen.__new__(rb.AuditScreen)
    from rbui.screens.check import QueuedAgain
    scr.app, scr.queued = app, QueuedAgain()
    path = os.path.join(rommlib.ROMS_ROOT, "psx", "Breaker (USA).chd")
    scr.redownload({"rom": {"id": 1, "fs_name": "Breaker (USA).zip"}, "system": "psx", "path": path})
    assert got == [[path]]
    assert scr.queued.tag(path) == "downloaded" and path in scr.queued


def test_download_again_never_unpacks_in_among_another_game_named_like_this_one(isolated):
    """Lamp Run (USA)/ holding the owner's MyOther.cue/.bin: Check games calls Lamp Run's cue
    missing there. Downloading it again unpacked into that folder, mixing the two games (or,
    before, failed every time with no hint). Now it's refused, saying what to do."""
    other = _w(os.path.join(_sysdir(), GAME, "MyOther.cue"), b"FILE \"MyOther.bin\" BINARY\n")
    _w(os.path.join(_sysdir(), GAME, "MyOther.bin"), b"o" * 2352)
    d = _downloader()
    j, arc = _download_again(d)
    j.replace = [os.path.join(_sysdir(), GAME, GAME + ".cue")]      # the file Check games found missing
    with pytest.raises(rommlib.RommError, match="Move or rename it, then try again"):
        d._post(j, {"kind": "file", "path": arc, "heavy": True})
    # the game page's Download again names the whole folder (taken for the game by its name):
    # still never unpacked in among the other game
    j, arc = _download_again(d)
    j.replace = [os.path.join(_sysdir(), GAME)]
    with pytest.raises(rommlib.RommError, match=r"not unpacking.*Move or rename"):
        d._post(j, {"kind": "file", "path": arc, "heavy": True})
    assert sorted(os.listdir(os.path.join(_sysdir(), GAME))) == ["MyOther.bin", "MyOther.cue"]
    assert open(other, "rb").read().startswith(b"FILE")


def test_download_again_replaces_the_owners_damaged_file_it_was_asked_to(isolated):
    """The owner's cut-short Lamp Run (USA).bin beside their notes (not downloaded by the app):
    Check games' Download again names it and its cue (the confirm lists them), and RomM's copy
    takes their place; the notes stay."""
    b = _w(os.path.join(_sysdir(), GAME, GAME + ".bin"), b"short")
    cue = _w(os.path.join(_sysdir(), GAME, GAME + ".cue"), CUE.encode())
    _w(os.path.join(_sysdir(), GAME, "notes.txt"), b"notes")
    d = _downloader()
    j, arc = _download_again(d)
    j.replace = [b, cue]
    d._post(j, {"kind": "file", "path": arc, "heavy": True})
    assert j.state == rommlib.Job.DONE, j.message
    assert open(b).read() == "B" * 2352
    assert open(os.path.join(_sysdir(), GAME, "notes.txt")).read() == "notes"


def test_a_folder_named_like_a_game_holding_another_game_isnt_that_game(isolated):
    """Lamp Run (USA)/ holding the owner's MyGame.cue/.bin: it was taken for RomM's Lamp Run
    by its name, so Delete from this box deleted MyGame. A folder found by name is the game
    only when one of its sheets carries its name."""
    d = os.path.join(_sysdir(), GAME)
    _w(os.path.join(d, "MyGame.cue"), b'FILE "MyGame.bin" BINARY\n')
    _w(os.path.join(d, "MyGame.bin"), b"m" * 2352)
    lib = rommlib.Library()
    assert not lib.is_installed(ROM, "psx") and lib.paths_for(ROM, "psx") == []
    assert rommlib.holds_game(d, "psx") and not rommlib.named_game(d, "psx")
    _w(os.path.join(d, GAME + " (Disc 1).cue"), CUE.encode())          # now it's Lamp Run's too
    lib.rescan("psx")
    assert lib.is_installed(ROM, "psx") and lib.paths_for(ROM, "psx") == [d]


def test_a_playlist_left_pointing_out_of_the_folder_isnt_a_game_in_it(isolated):
    """Compressing kept the folder for a playlist whose name was taken beside it, pointing at
    ../Lamp Run (USA).chd: the folder was the game again, Check games called its cue missing,
    and Download again was refused. The playlist lists the CHD; the folder holds no game."""
    d = os.path.join(_sysdir(), GAME)
    _w(os.path.join(d, "Mine.m3u"), b"../Lamp Run (USA).chd\n")
    _w(os.path.join(d, "notes.txt"), b"notes")
    assert not rommlib.holds_game(d, "psx") and not rommlib.named_game(d, "psx")
    _w(os.path.join(d, "Mine.m3u"), b"Lamp Run (USA).cue\n")          # one listing a disc in it is
    assert rommlib.holds_game(d, "psx")


@pytest.mark.parametrize("theirs", ["Other Game.chd", "Other.iso", "cheats.cht", "patch.ips", "soundtrack.mp3",
                                    "mysave.bin"])
def test_a_folder_named_like_a_game_holding_any_file_not_named_so_isnt_that_game(isolated, theirs):
    """Round 4: any game-type file not named like the folder made it RomM's game again, and
    Delete from this box deleted it (another game's CHD, a cheat, a save)."""
    d = os.path.join(_sysdir(), GAME)
    _w(os.path.join(d, theirs), b"theirs")
    lib = rommlib.Library()
    assert not lib.is_installed(ROM, "psx") and lib.paths_for(ROM, "psx") == []


def test_a_game_found_by_its_folder_is_never_deleted(isolated):
    """Not downloaded by the app (no record): Delete refuses and nothing goes, whatever is in it."""
    d = os.path.join(_sysdir(), GAME)
    cue = _w(os.path.join(d, GAME + ".cue"), CUE.encode())
    other = _w(os.path.join(d, GAME + " (Hack).bin"), b"the owner's hack")
    lib = rommlib.Library()
    assert lib.paths_for(ROM, "psx") == [d] and not lib.downloaded(ROM)
    with pytest.raises(rommlib.NotDownloadedHere):
        lib.uninstall(ROM, "psx")
    assert os.path.exists(cue) and open(other, "rb").read() == b"the owner's hack"


def test_free_up_space_takes_only_a_playlists_own_file_for_one_put_here_another_way(isolated, monkeypatch):
    """Free up space listed the owner's own playlist (My Mix.m3u) as a game, and deleting it
    took another game's CHD it listed. Games put here another way are listed (the user wants to
    free space with them), and deleting one takes exactly its own files: a playlist alone."""
    from rbui.screens.space import freeable_games
    mine = _w(os.path.join(_sysdir(), "My Mix.m3u"), b"Other (USA).chd\n")
    other = _w(os.path.join(_sysdir(), "Other (USA).chd"), b"o" * 100)
    ours = _w(os.path.join(_sysdir(), GAME + ".chd"), b"c" * 100)
    lib = rommlib.Library()
    lib.mark(ROM, "psx", [ours])
    monkeypatch.setattr(rommlib, "es_game_list", lambda systems=None: [
        {"system": "psx", "path": p, "name": os.path.basename(p), "lastplayed": None, "playcount": 0}
        for p in (mine, ours)])
    got = {g["path"]: g for g in freeable_games(lib)}
    assert got[ours]["ours"] and not got[mine]["ours"] and got[mine]["size"] == os.path.getsize(mine)
    rommlib.delete_game("psx", mine, lib)
    assert not os.path.exists(mine) and open(other, "rb").read() == b"o" * 100


def test_deleting_the_apps_playlist_never_takes_a_disc_it_didnt_download(isolated):
    """The app's own playlist, edited by the owner to also list another game's CHD: deleting it
    took that CHD. Only the files the app downloaded go."""
    disc = _w(os.path.join(_sysdir(), GAME + ".chd"), b"c" * 100)
    other = _w(os.path.join(_sysdir(), "Other (USA).chd"), b"o" * 100)
    m3u = _w(os.path.join(_sysdir(), GAME + ".m3u"), ("%s.chd\nOther (USA).chd\n" % GAME).encode())
    lib = rommlib.Library()
    lib.mark(ROM, "psx", [m3u, disc])
    rommlib.delete_game("psx", m3u, lib)
    assert not os.path.exists(disc) and not os.path.exists(m3u)
    assert open(other, "rb").read() == b"o" * 100


def test_check_games_downloads_again_over_a_copy_the_app_didnt_download_and_says_so(isolated, rb):
    """A game copied over by hand can be repaired: its game files (not the notes beside it) are
    replaced, and the confirm says it wasn't downloaded by RomM-o-Cera and lists them."""
    jobs = []
    dl = types.SimpleNamespace(enqueue=lambda rom, system, replace=None: jobs.append((rom, system, replace)) or "job")
    app = types.SimpleNamespace(library=rommlib.Library(), downloader=dl, modal=None, toast=lambda *a, **k: None)
    scr = rb.AuditScreen.__new__(rb.AuditScreen)
    scr.app, scr.queued = app, rb.QueuedAgain()
    cue = _w(os.path.join(_sysdir(), GAME + ".cue"), b'FILE "%s.bin" BINARY\n' % GAME.encode())
    track = _w(os.path.join(_sysdir(), GAME + ".bin"), b"bin")
    notes = _w(os.path.join(_sysdir(), "notes.txt"), b"mine")
    f = {"path": track, "system": "psx", "severity": "damaged", "problem": "Incomplete disc image",
         "rom": dict(ROM)}
    scr.findings = [f]
    assert scr.pickable(f) and scr.redownloadable() == [f]
    scr.open_finding(f)
    body = app.modal.body if hasattr(app.modal, "body") else repr(vars(app.modal))
    assert "wasn't downloaded by RomM-o-Cera" in body and GAME + ".bin" in body and "notes.txt" not in body
    scr.redownload(f)
    assert jobs[0][2] == sorted([os.path.normpath(cue), os.path.normpath(track)])
    assert os.path.exists(notes)
    scr.queued = rb.QueuedAgain()
    note = scr.theirs_note([f])
    assert "Not downloaded by RomM-o-Cera (1 game)" in note and GAME + ".bin" in note and "notes.txt" not in note
    rommlib.Library().mark(ROM, "psx", [track])
    jobs.clear()
    scr.redownload(f)
    assert jobs[0][2] == [track] and scr.theirs_note([f]) == ""


def test_deleting_a_downloaded_folder_game_leaves_what_the_owner_added_to_it(isolated):
    """Round 6: the app's own BattleTech folder, with the owner's .ppf, (Fan Translation).ips,
    .cht and a hack .bin added: Delete took all but the .txt. Only the files the download wrote
    go now."""
    d = _downloader()
    j, arc = _download_again(d)
    d._post(j, {"kind": "file", "path": arc, "heavy": True})
    folder = os.path.join(_sysdir(), GAME)
    theirs = [_w(os.path.join(folder, n), b"theirs") for n in
              (GAME + ".ppf", GAME + " (Fan Translation).ips", GAME + ".cht", GAME + " (Hack).bin")]
    rommlib.Library().uninstall(ROM, "psx")
    assert sorted(os.listdir(folder)) == sorted(os.path.basename(p) for p in theirs)


def test_a_copy_put_where_a_stale_record_points_is_never_deleted(isolated):
    """Round 6: a record left from a game deleted outside the app; the owner copies their own
    game to that same path. Delete removed it: the record only had the path. It holds the
    file's size and inode now, so a different file there isn't the app's."""
    p = _w(os.path.join(_sysdir(), GAME + ".chd"), b"the app's")
    lib = rommlib.Library()
    lib.mark(ROM, "psx", [p])
    os.remove(p)                                          # deleted outside the app
    _w(p, b"the owner's own copy")
    with pytest.raises(rommlib.NotDownloadedHere):
        lib.uninstall(ROM, "psx")
    assert open(p, "rb").read() == b"the owner's own copy"


def test_a_download_whose_unpack_folder_holds_another_game_is_refused_before_downloading(isolated, monkeypatch):
    """Round 6: Download again fetched all 50 MB, then refused to unpack into the owner's folder."""
    from romm import downloader
    monkeypatch.setattr(downloader, "needs_listing", lambda rom, system: False)
    _w(os.path.join(_sysdir(), GAME, GAME + ".cue"), b"someone else's copy")
    d = _downloader()
    d.es = {"psx": {".cue", ".chd", ".m3u"}}
    d.api = types.SimpleNamespace(rom=lambda rid: dict(ROM, files=[{"file_name": GAME + ".zip", "category": None,
                                                                    "file_size_bytes": 100}]))
    d._free_bytes = lambda p: 1 << 40
    j = rommlib.Job(dict(ROM), "psx")
    with pytest.raises(rommlib.RommError, match="Move or rename it"):
        d._process(j)
    assert not os.path.exists(os.path.join(_sysdir(), GAME + ".zip.part"))


def test_download_again_never_writes_over_a_file_put_where_the_apps_was(isolated):
    """Round 7: the app's zip deleted and the owner's own put at the same path; Download again
    (Check games, the game page, Compare) wrote RomM's over it. Its record knows the file it
    wrote; a new file there is the owner's."""
    d = _downloader()
    arc = os.path.join(_sysdir(), GAME + ".zip")
    _w(arc, b"the app's")
    d.library.mark(ROM, "psx", [arc])
    os.remove(arc)
    _w(arc, b"the owner's own, a new file")
    j = rommlib.Job(dict(ROM), "psx")
    assert not d._ours(j, arc)
    with pytest.raises(rommlib.RommError, match="wasn't downloaded by RomM-o-Cera"):
        d._never_over_theirs(j, arc)


def test_the_apps_own_download_changed_since_is_left_alone_and_said_so(isolated):
    """A file at the app's path that was cut short or rewritten by something else (its size and
    time changed) can't be told from one put there in its place (ext4 hands the freed inode to
    the next file): never deleted, and the refusal says it changed since."""
    p = _w(os.path.join(_sysdir(), GAME + ".chd"), b"x" * 1000)
    lib = rommlib.Library()
    lib.mark(ROM, "psx", [p])
    os.truncate(p, 10)
    assert not lib.downloaded(ROM)
    with pytest.raises(rommlib.NotDownloadedHere, match="or was changed since"):
        lib.uninstall(ROM, "psx")
    assert os.path.getsize(p) == 10

def test_download_again_never_unpacks_over_the_owners_file_at_the_apps_unpacked_path(isolated):
    """Round 8: the app's unpacked .z64 replaced by the owner's own (same path); Check games'
    Download again unpacked RomM's over it. The unpack writes only over the job's own files."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, "n64")
    z64 = _w(os.path.join(sysdir, "Fission (World).z64"), b"the app's" * 100)
    rom = {"id": 8, "name": "Fission", "fs_name": "Fission (World).zip", "fs_name_no_ext": "Fission (World)"}
    lib = rommlib.Library()
    lib.mark(rom, "n64", [z64])
    os.remove(z64)
    _w(z64, b"the owner's own hack")
    d = _downloader()
    d.es = {"n64": {".z64", ".n64", ".v64"}}           # ES doesn't list the zip: it's unpacked
    arc = _zip(os.path.join(sysdir, "Fission (World).zip"), {"Fission (World).z64": "R" * 900})
    j = rommlib.Job(dict(rom), "n64")
    j.made = [arc]                                      # nothing named to replace: an automatic redownload
    d.jobs.append(j)
    with pytest.raises(rommlib.RommError, match="not unpacking over it"):
        d._post(j, {"kind": "file", "path": arc, "heavy": True})
    assert open(z64, "rb").read() == b"the owner's own hack"
    # asked for by name (Check games' confirm listed it): RomM's copy replaces it
    arc = _zip(os.path.join(sysdir, "Fission (World).zip"), {"Fission (World).z64": "R" * 900})
    j = rommlib.Job(dict(rom), "n64")
    j.made, j.replace = [arc], [z64]
    d.jobs.append(j)
    d._post(j, {"kind": "file", "path": arc, "heavy": True})
    assert j.state == rommlib.Job.DONE, j.message
    assert open(z64).read() == "R" * 900
