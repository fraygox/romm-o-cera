"""Check games › Games ES can't see: files EmulationStation ignores because
their system doesn't list the extension (rommlib.find_unseen), and the two
fixes offered — unpack an archive (the downloader's own post-processing) and
move a game to the system that lists it."""
import io
import json
import os
import struct
import zipfile

import pytest

import rommlib

# the extension lists of Batocera 43.1's es_systems.cfg for these systems
ES = {
    "psp": {".iso", ".cso", ".pbp", ".chd"},
    "ps2": {".iso", ".mdf", ".nrg", ".bin", ".img", ".dump", ".gz", ".cso", ".chd", ".m3u"},
    "psx": {".cue", ".img", ".mdf", ".pbp", ".toc", ".cbn", ".m3u", ".ccd", ".chd", ".iso"},
    "gamecube": {".gcm", ".iso", ".gcz", ".ciso", ".wbfs", ".rvz", ".elf", ".dol", ".m3u", ".json"},
    "wii": {".gcm", ".iso", ".gcz", ".ciso", ".wbfs", ".rvz", ".elf", ".dol", ".m3u", ".json"},
    "n64": {".z64", ".n64", ".v64", ".zip", ".7z"},
}


def _w(rel, data=b"x"):
    p = os.path.join(rommlib.ROMS_ROOT, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode())
    return p


def _zip(rel, members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, data in members.items():
            z.writestr(n, data)
    return _w(rel, buf.getvalue())


def _cue(rel_dir, stem, system):
    _w("%s/%s%s.bin" % (system, rel_dir, stem), b"\0" * 2352)
    return _w("%s/%s%s.cue" % (system, rel_dir, stem),
              'FILE "%s.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n' % stem)


def _by_name(found):
    return {os.path.relpath(f["path"], rommlib.ROMS_ROOT): f for f in found}


def _gc_iso(rel):
    head = bytearray(0x40)
    head[0x1C:0x20] = struct.pack(">I", 0xC2339F3D)
    return _w(rel, bytes(head))


def test_scan_classifies_what_es_cant_see(isolated):
    _zip("ps2/Lorem Racer.zip", {"Lorem Racer.iso": b"i" * 64})
    _w("psp/Ipsum Quest.7z", b"7z")
    _w("n64/Dolor Cube.rvz", b"RVZ\x01")                      # a GameCube/Wii image in n64/
    _gc_iso("n64/Sit Amet.iso")                               # a plain GC disc: the header says which
    _w("n64/Mystery.xyz", b"?")                               # no system lists it
    _cue("", "Amet Saga", "psx")                              # cue + bin: .bin isn't psx's, but the cue names it
    _cue("", "Elit Story", "ps2")                             # PS2 lists .bin, not .cue: ES lists the game
    _w("psx/Consectetur.chd")                                 # listed
    _w("n64/Homebrew Demo.z64")                               # listed
    _w("psx/images/Amet Saga-image.zip")                      # media folder
    _w("psx/gamelist.xml", "<gameList/>")
    _w("psx/.hidden.zip")
    _w("psx/Gone.zip.part")
    _w("psx/readme.txt", "notes")
    _w("unlisted-system/Thing.zip")                           # ES doesn't know this folder at all
    _w("psx/Tempor (Disc 1)/Tempor (Disc 1).chd")             # a game folder: its other files belong to it
    _w("psx/Tempor (Disc 1)/Tempor.sbi")
    found = rommlib.find_unseen(ES)
    by = _by_name(found)
    assert set(by) == {"ps2/Lorem Racer.zip", "psp/Ipsum Quest.7z", "n64/Dolor Cube.rvz", "n64/Sit Amet.iso",
                       "n64/Mystery.xyz"}
    assert [f["system"] for f in found] == sorted(f["system"] for f in found)
    zipf = by["ps2/Lorem Racer.zip"]
    assert zipf["kind"] == "archive" and zipf["why"] == "PS2 doesn't list .zip — ES can't see it"
    assert by["psp/Ipsum Quest.7z"]["kind"] == "archive"
    rvz = by["n64/Dolor Cube.rvz"]
    assert rvz["kind"] == "wrong" and rvz["to"] == ["gamecube", "wii"]
    assert rvz["why"] == "a GameCube/Wii image in n64/"
    assert by["n64/Sit Amet.iso"]["to"] == ["gamecube"]
    unk = by["n64/Mystery.xyz"]
    assert unk["kind"] == "unknown" and unk["to"] == [] and "ES can't list" in unk["why"]



def test_scan_ignores_housekeeping_and_generic_extensions(isolated):
    """Real-box noise: backups and parked files aren't findings, and .bin/.sh/.iso say nothing
    about which system a file belongs to, so nothing offers to move them."""
    es = dict(ES, c64={".d64", ".t64", ".prg"}, megadrive={".bin", ".md"}, atari2600={".bin", ".a26"}, nds={".nds", ".bin"}, ports={".sh", ".squashfs"}, ps3={".ps3", ".squashfs"}, triforce={".iso", ".rvz"})
    es["gamecube"] = es["gamecube"] | {".ub3"}
    _w("psx/gamelist.xml.bak-romm-20260815-1200", "<gameList/>")
    _w("psx/Amet.cue.bak-rommbrowser")
    _w("psx/Amet.gdi.bak-gdi")
    _w("ps2/Lorem.iso.disabled")
    _w("ps2/library.sqlite")
    _w("ps2/Lorem.torrent")
    _w("ps2/core.1")
    _w("ps2/core.102")
    _w("c64/Cart.bin")                           # ps2 lists .bin, but so might anything
    _w("ps2/Launch.sh")                          # only a launcher (ports) lists .sh
    _w("n64/Mystery Disc.iso")                   # no GameCube/Wii header: 5 systems list .iso
    _w("n64/Dolor Cube.rvz")                     # still a clear GameCube/Wii image
    _w("c64/[BIOS] Drive/901229-01.ub3")         # a chip dump in a set, not a stray GameCube disc
    by = _by_name(rommlib.find_unseen(es))
    assert set(by) == {"c64/Cart.bin", "ps2/Launch.sh", "n64/Mystery Disc.iso", "n64/Dolor Cube.rvz",
                       "c64/[BIOS] Drive/901229-01.ub3"}
    assert by["c64/[BIOS] Drive/901229-01.ub3"]["kind"] == "unknown"
    for name in ("c64/Cart.bin", "n64/Mystery Disc.iso"):
        assert by[name]["kind"] == "unknown" and by[name]["to"] == []
        assert "can't list it here" in by[name]["why"]
    sh = by["ps2/Launch.sh"]
    assert sh["kind"] == "unknown" and sh["to"] == [] and "no system takes .sh" in sh["why"]
    assert by["n64/Dolor Cube.rvz"]["kind"] == "wrong"

def test_scan_skips_folders_es_lists_and_folder_game_systems(isolated):
    es = dict(ES, ps3={".ps3", ".psn", ".squashfs"})
    _w("ps3/Lorem.ps3/PS3_GAME/USRDIR/EBOOT.BIN")
    _w("ps3/Lorem.ps3/PS3_GAME/data.zip")
    _w("ps3/Other Folder/stuff.zip")                          # inside game folders: never looked at
    _w("ps3/Loose.zip")
    assert set(_by_name(rommlib.find_unseen(es))) == {"ps3/Loose.zip"}


def _dl(es=None):
    d = rommlib.Downloader(None, rommlib.Library(), dict(rommlib.DEFAULT_CONFIG), persist=False)
    d.es = es or ES
    return d


def test_unpack_goes_through_the_download_path(isolated):
    arc = _zip("ps2/Lorem Racer.zip", {"Lorem Racer.iso": b"i" * 4096})
    d = _dl()
    f = _by_name(rommlib.find_unseen(ES))["ps2/Lorem Racer.zip"]
    assert rommlib.archive_contents(d, arc) == [("Lorem Racer.iso", 4096)]
    out, task = rommlib.unpack_unseen(d, f)
    iso = os.path.join(rommlib.ROMS_ROOT, "ps2", "Lorem Racer.iso")
    assert out == [iso] and os.path.getsize(iso) == 4096
    assert not os.path.exists(arc)                            # deleted, as after a download
    t = json.load(open(os.path.join(rommlib.CHD_QUEUE_DIR, task)))
    assert t["system"] == "ps2" and t["mode"] == "createdvd" and t["sources"] == [iso]
    q = [json.loads(ln) for ln in open(rommlib.GAMELIST_QUEUE)]
    assert q == [{"system": "ps2", "rename": {arc: iso}}]
    assert rommlib.find_unseen(ES) == []
    assert not os.path.exists(rommlib.STATE_PATH)             # not ours: no install record made


def test_unpacking_our_own_archive_updates_its_record(isolated):
    arc = _zip("psp/Ipsum Quest.zip", {"Ipsum Quest.iso": b"i" * 64})
    d = _dl()
    d.library.mark({"id": 5, "name": "Ipsum Quest", "fs_name": "Ipsum Quest.zip"}, "psp", [arc])
    out, _ = rommlib.unpack_unseen(d, _by_name(rommlib.find_unseen(ES))["psp/Ipsum Quest.zip"])
    assert json.load(open(rommlib.STATE_PATH))["5"]["paths"] == out


def test_unpack_a_cue_bin_zip_into_its_own_folder(isolated):
    _zip("psp/Ipsum.zip", {"Ipsum.cue": 'FILE "Ipsum.bin" BINARY\n', "Ipsum.bin": b"\0" * 64})
    d = _dl()
    f = _by_name(rommlib.find_unseen(ES))["psp/Ipsum.zip"]
    out, _ = rommlib.unpack_unseen(d, f)
    assert out == [os.path.join(rommlib.ROMS_ROOT, "psp", "Ipsum")]
    assert sorted(os.listdir(out[0])) == ["Ipsum.bin", "Ipsum.cue"]


def test_unpack_refuses_to_mix_into_another_game(isolated):
    arc = _zip("ps2/Lorem Racer.zip", {"Lorem Racer.iso": b"new"})
    other = _w("ps2/Lorem Racer.iso", b"someone else's copy")
    f = _by_name(rommlib.find_unseen(ES))["ps2/Lorem Racer.zip"]
    with pytest.raises(rommlib.RommError, match="already on the box"):
        rommlib.unpack_unseen(_dl(), f)
    assert os.path.exists(arc) and open(other, "rb").read() == b"someone else's copy"
    assert not os.path.exists(rommlib.CHD_QUEUE_DIR) or not os.listdir(rommlib.CHD_QUEUE_DIR)


def test_unpack_refuses_unsafe_archives(isolated):
    _zip("ps2/Evil.zip", {"../escape.iso": b"x", "Evil.iso": b"y"})
    f = _by_name(rommlib.find_unseen(ES))["ps2/Evil.zip"]
    with pytest.raises(rommlib.RommError, match="Unsafe"):
        rommlib.unpack_unseen(_dl(), f)
    assert not os.path.exists(os.path.join(rommlib.ROMS_ROOT, "escape.iso"))


def test_move_carries_cue_tracks_and_updates_our_record(isolated):
    cue = _cue("", "Amet Saga", "n64")                       # a PlayStation game in n64/
    lib = rommlib.Library()
    lib.mark({"id": 7, "name": "Amet Saga", "fs_name": "Amet Saga.cue"}, "n64", [cue],
             meta={"system": "n64", "paths": [cue], "rom_id": 7, "name": "Amet Saga"})
    f = _by_name(rommlib.find_unseen(ES))["n64/Amet Saga.cue"]
    assert f["kind"] == "wrong" and f["to"] == ["psx"]
    new = rommlib.move_unseen(f, "psx", lib, ES)
    psx = os.path.join(rommlib.ROMS_ROOT, "psx")
    assert new == os.path.join(psx, "Amet Saga.cue")
    assert sorted(os.listdir(psx)) == ["Amet Saga.bin", "Amet Saga.cue"]
    assert sorted(os.listdir(os.path.join(rommlib.ROMS_ROOT, "n64"))) == []
    rec = json.load(open(rommlib.STATE_PATH))["7"]
    assert rec["system"] == "psx" and rec["paths"] == [new] and rec["meta"]["system"] == "psx"
    q = [json.loads(ln) for ln in open(rommlib.GAMELIST_QUEUE)]
    assert q[0] == {"system": "psx", "rename": {cue: new}}
    assert q[1]["system"] == "psx" and q[1]["paths"] == [new]
    assert rommlib.find_unseen(ES) == []


def test_move_never_overwrites(isolated):
    rvz = _w("n64/Dolor Cube.rvz", b"mine")
    there = _w("gamecube/Dolor Cube.rvz", b"already here")
    f = _by_name(rommlib.find_unseen(ES))["n64/Dolor Cube.rvz"]
    with pytest.raises(rommlib.RommError, match="already in gamecube"):
        rommlib.move_unseen(f, "gamecube", rommlib.Library(), ES)
    assert open(rvz, "rb").read() == b"mine" and open(there, "rb").read() == b"already here"
    with pytest.raises(rommlib.RommError):
        rommlib.move_unseen(f, "../etc", None, ES)
    new = rommlib.move_unseen(f, "wii", None, ES)              # a folder that isn't there yet
    assert open(new, "rb").read() == b"mine" and not os.path.exists(rvz)
    assert not os.path.exists(rommlib.STATE_PATH)             # not ours: no install record made


def test_move_with_a_track_already_at_the_destination_moves_nothing(isolated):
    cue = _cue("", "Amet Saga", "n64")
    bin_there = _w("psx/Amet Saga.bin", b"another")
    f = _by_name(rommlib.find_unseen(ES))["n64/Amet Saga.cue"]
    with pytest.raises(rommlib.RommError):
        rommlib.move_unseen(f, "psx", None, ES)
    assert os.path.exists(cue) and os.path.exists(cue[:-4] + ".bin") and open(bin_there, "rb").read() == b"another"
    assert not os.path.exists(os.path.join(rommlib.ROMS_ROOT, "psx", "Amet Saga.cue"))


@pytest.fixture
def ui(isolated, rb, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    app = rb.App(None, headless=True)
    app.downloader.es = ES

    def bg(fn, done=None, err=None):                         # run background work inline
        try:
            res = fn()
        except rommlib.RommError as e:
            return (err or (lambda e: app.toast(str(e), rb.RED)))(e)
        if done:
            done(res)
    monkeypatch.setattr(app, "bg", bg)
    return app


def test_delete_takes_the_tracks_it_names_and_updates_our_record(isolated):
    cue = _cue("Amet Saga/", "Amet Saga", "n64")             # a PlayStation game in a folder in n64/
    keep = _w("n64/Homebrew Demo.z64")
    disc2 = _w("n64/Other Disc.z64")
    lib = rommlib.Library()
    lib.mark({"id": 7, "name": "Amet Saga", "fs_name": "Amet Saga.cue"}, "n64", [cue])
    lib.mark({"id": 8, "name": "Set", "fs_name": "Set.m3u"}, "n64", [cue, disc2])   # shares the cue
    save = _w("../saves/psx/Amet Saga.srm")
    size = os.path.getsize(cue) + os.path.getsize(cue[:-4] + ".bin")
    f = _by_name(rommlib.find_unseen(ES))["n64/Amet Saga/Amet Saga.cue"]
    paths, freed = rommlib.delete_unseen(f, lib)
    assert paths == [cue, cue[:-4] + ".bin"] and freed == size
    assert sorted(os.listdir(os.path.join(rommlib.ROMS_ROOT, "n64"))) == ["Homebrew Demo.z64", "Other Disc.z64"]
    assert os.path.exists(keep) and os.path.exists(save)
    inst = json.load(open(rommlib.STATE_PATH))
    assert "7" not in inst and inst["8"]["paths"] == [disc2]
    q = [json.loads(ln) for ln in open(rommlib.GAMELIST_QUEUE)]
    assert q[-1] == {"system": "n64", "remove": paths}
    assert rommlib.find_unseen(ES) == []


def test_delete_refuses_what_isnt_a_plain_file_in_the_system(isolated, tmp_path):
    outside = tmp_path / "elsewhere.zip"
    outside.write_bytes(b"x")
    link = os.path.join(rommlib.ROMS_ROOT, "ps2", "Link.zip")
    os.makedirs(os.path.dirname(link), exist_ok=True)
    os.symlink(str(outside), link)
    for path in (link, str(outside), os.path.join(rommlib.ROMS_ROOT, "ps2", "Gone.zip")):
        with pytest.raises(rommlib.RommError):
            rommlib.delete_unseen({"system": "ps2", "path": path})
    assert outside.exists() and os.path.islink(link)


def test_delete_from_the_screen_confirms_first(ui, rb):
    app = ui
    z = _w("gamecube/Lorem Crossing.7z", b"7" * 4096)
    app.push(rb.UnseenScreen(app))
    sc = app.stack[-1]
    sc.action("ok")
    assert [e[0] for e in app.modal.entries] == ["Unpack", "Delete", "Leave it"]
    app.modal.action("down")
    app.modal.action("ok")                                    # Delete: asks first, in red
    assert isinstance(app.modal, rb.Confirm) and app.modal.danger and "(4 KB)" in app.modal.body
    assert os.path.exists(z)
    app.modal.action("back")                                  # backing out deletes nothing
    assert app.modal is None and os.path.exists(z)
    sc.action("ok")
    app.modal.action("down")
    app.modal.action("ok")
    app.modal.action("ok")
    assert not os.path.exists(z) and sc.items == []
    assert any(t[0].startswith("Deleted Lorem Crossing.7z, freed") for t in app.toasts)


def test_check_games_tile_and_screen(ui, rb):
    app = ui
    _zip("ps2/Lorem Racer.zip", {"Lorem Racer.iso": b"i" * 64})
    _w("n64/Dolor Cube.rvz", b"RVZ\x01")
    _w("n64/Mystery.xyz")
    ck = rb.CheckScreen(app)
    app.push(ck)
    assert ck.unseen == 3
    ck.action("right")
    ck.action("right")
    assert ck.sel == 2 and ck.hints()[0] == ("A", "Look")
    ck.draw(app.canvas)
    ck.action("ok")
    sc = app.stack[-1]
    assert isinstance(sc, rb.UnseenScreen) and sc.crumbs()[-1] == "Games ES can't see"
    assert [os.path.basename(i["path"]) for i in sc.items] == ["Dolor Cube.rvz", "Mystery.xyz", "Lorem Racer.zip"]
    sc.draw(app.canvas)
    sc.action("ok")                                           # the .rvz: one choice per system, then Leave it
    assert [e[0] for e in app.modal.entries] == ["Move to gamecube/", "Move to wii/", "Delete", "Leave it"]
    app.modal.action("ok")
    assert app.modal is None and os.path.exists(os.path.join(rommlib.ROMS_ROOT, "gamecube", "Dolor Cube.rvz"))
    assert [os.path.basename(i["path"]) for i in sc.items] == ["Mystery.xyz", "Lorem Racer.zip"]
    sc.action("ok")                                           # unknown: leave it (first) or delete it
    assert [e[0] for e in app.modal.entries] == ["Leave it", "Delete"]
    app.modal.action("ok")
    sc.action("down")
    sc.action("ok")                                           # the zip: Unpack shows what's inside first
    assert [e[0] for e in app.modal.entries] == ["Unpack", "Delete", "Leave it"]
    app.modal.action("ok")
    assert isinstance(app.modal, rb.Confirm) and "Lorem Racer.iso" in app.modal.body
    app.modal.action("ok")
    assert os.path.exists(os.path.join(rommlib.ROMS_ROOT, "ps2", "Lorem Racer.iso"))
    assert [os.path.basename(i["path"]) for i in sc.items] == ["Mystery.xyz"]
    assert any(t[0].startswith("Unpacked Lorem Racer.zip") for t in app.toasts)


def test_three_tiles_keep_the_findings_below(ui, rb):
    app = ui
    rommlib.save_audit([{"system": "psx", "path": _w("psx/A.chd"), "severity": "damaged", "rom": None,
                         "problem": "Disc image cut short"}], {"checked": 1}, False)
    ck = rb.CheckScreen(app)
    app.push(ck)
    assert ck.unseen == 0 and len(ck.findings) == 1
    ck.action("right")
    ck.action("down")
    assert ck.sel == 3 and ck.hints()[0] == ("A", "Download again")
    ck.action("down")
    assert ck.sel == 3                                        # the last finding: stays
    ck.action("up")
    assert ck.sel == 0
    ck.action("left")
    assert ck.sel == 0
    ck.draw(app.canvas)


def test_pick_several_then_unpack_them_all(ui, rb):
    app = ui
    _zip("ps2/Lorem Racer.zip", {"Lorem Racer.iso": b"i" * 64})
    _zip("psp/Ipsum Quest.zip", {"Ipsum Quest.iso": b"p" * 64})
    _w("psp/Broken.zip", b"not a zip")                       # can't be unpacked: noted, the rest go on
    _w("n64/Mystery.xyz")
    app.push(rb.UnseenScreen(app))
    sc = app.stack[-1]
    names = [os.path.basename(i["path"]) for i in sc.items]
    assert names == ["Mystery.xyz", "Lorem Racer.zip", "Broken.zip", "Ipsum Quest.zip"]
    assert sc.hints()[:3] == [("A", "Options"), ("X", "Pick"), ("Y", "Pick all")]
    sc.action("y")                                            # pick all, then drop the .xyz
    assert len(sc.marks) == 4
    sc.action("x")
    assert len(sc.marks) == 3 and sc.sel == 1 and sc.hints()[0] == ("A", "Do 3 picked")
    sc.draw(app.canvas)
    sc.action("ok")
    assert [e[0] for e in app.modal.entries if e] == ["Unpack 3 archives", "Delete 3 files", "Clear picks"]
    app.modal.action("ok")
    assert isinstance(app.modal, rb.Confirm) and "ps2, psp" in app.modal.body
    app.modal.action("ok")
    roms = rommlib.ROMS_ROOT
    assert os.path.exists(os.path.join(roms, "ps2", "Lorem Racer.iso"))
    assert os.path.exists(os.path.join(roms, "psp", "Ipsum Quest.iso"))
    assert os.path.exists(os.path.join(roms, "psp", "Broken.zip"))
    assert [os.path.basename(i["path"]) for i in sc.items] == ["Mystery.xyz", "Broken.zip"]
    assert sc.marks == set() and not sc.busy and sc.progress == ""
    msg, col = app.toasts[-1][:2]
    assert msg.startswith("Unpacked 2") and "1 couldn't be: Broken.zip (can't be read" in msg and col == rb.AMBER


def test_pick_several_then_delete_them_all(ui, rb):
    app = ui
    _cue("", "Amet Saga", "n64")                              # the cue and its own track, both picked
    _w("ps2/Lorem.7z", b"7" * 1024)
    keep = _w("ps2/Keep.zip")
    app.push(rb.UnseenScreen(app))
    sc = app.stack[-1]
    for i in sc.items:
        if not i["path"].endswith("Keep.zip"):
            sc.marks.add(i["path"])
    sc.action("back")                                         # B clears picks first
    assert sc.marks == set() and app.stack[-1] is sc
    for i in sc.items:
        if not i["path"].endswith("Keep.zip"):
            sc.marks.add(i["path"])
    sc.action("ok")
    app.modal.action("down")
    app.modal.action("ok")
    assert isinstance(app.modal, rb.Confirm) and app.modal.danger
    app.modal.action("ok")
    assert sorted(os.listdir(os.path.join(rommlib.ROMS_ROOT, "n64"))) == []
    assert os.listdir(os.path.join(rommlib.ROMS_ROOT, "ps2")) == ["Keep.zip"] and os.path.exists(keep)
    assert app.toasts[-1][0].startswith("Deleted 2, freed")
