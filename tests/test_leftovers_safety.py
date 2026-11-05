"""Clean up leftovers offers the original of a converted game only when the
converted file is proven whole and the same game, and never a file a playlist,
another sheet or the converted game itself still needs."""
import hashlib
import json
import os
import shutil
import struct
import subprocess
import time
import zipfile

import pytest

import rommlib
from romm import leftovers

COPY = "Copy of a converted game"
CUE = 'FILE "%s" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n'


REAL_VERIFIED = leftovers._chd_verified


@pytest.fixture(autouse=True)
def _no_chdman(monkeypatch):
    """chdman verify is tested on its own (and with the real chdman, when it's here)."""
    monkeypatch.setattr(leftovers, "_chd_verified", lambda path: True)


def _meta(entries, at):
    """CHD v5 metadata entries chained from offset at: bytes."""
    out = b""
    for i, (tag, data) in enumerate(entries):
        nxt = at + len(out) + 16 + len(data) if i + 1 < len(entries) else 0
        out += tag + bytes([1]) + len(data).to_bytes(3, "big") + nxt.to_bytes(8, "big") + data
    return out


def fake_chd(path, tracks=(("MODE2_RAW", 300, 0),), dvd_bytes=None, tag=b"CHT2", version=5, parent=b"\0" * 20):
    """A compressed CHD v5 laid out as chdman writes one (header, metadata, hunks,
    then the compressed map at the very end). tracks: (type, frames, pad)."""
    if dvd_bytes is not None:
        meta, unit, hunk, logical = [(b"DVD ", b"\0")], 2048, 4096, dvd_bytes
    else:
        meta = [(tag, (b"TRACK:%d TYPE:%s SUBTYPE:NONE FRAMES:%d " % (i + 1, t.encode(), n)
                       + (b"PAD:%d " % pad if tag == b"CHGD" else b"")
                       + b"PREGAP:0 PGTYPE:MODE1 PGSUB:NONE POSTGAP:0\0")) for i, (t, n, pad) in enumerate(tracks)]
        unit, hunk = 2448, 19584
        logical = sum(-(-n // 4) * 4 for _, n, _ in tracks) * unit
    md = _meta(meta, 124)
    hunks = b"\x5a" * 1000
    mapoff = 124 + len(md) + len(hunks)
    cmap = b"\x11" * 64
    head = (b"MComprHD" + struct.pack(">II", 124, version) + b"cdlzcdzlcdfl\0\0\0\0"
            + struct.pack(">QQQII", logical, mapoff, 124, hunk, unit) + b"\0" * 40 + parent)
    maphead = len(cmap).to_bytes(4, "big") + (124 + len(md)).to_bytes(6, "big") + b"\0\0" + bytes([15, 0, 0, 0])
    with open(path, "wb") as f:
        f.write(head + md + hunks + maphead + cmap)
    return path


def cue_bin(d, stem, frames=300, bin_name=None):
    os.makedirs(d, exist_ok=True)
    b = bin_name or stem + ".bin"
    with open(os.path.join(d, b), "wb") as f:
        f.write(b"\1" * 2352 * frames)
    with open(os.path.join(d, stem + ".cue"), "w") as f:
        f.write(CUE % b)


@pytest.fixture(autouse=True)
def _all_the_apps(monkeypatch, request):
    """These tests are about which copies are proven safe to delete: every file here counts as
    one the app downloaded (only those .part files are ever offered; see the test that says so)."""
    if "theirs" not in request.keywords:
        monkeypatch.setattr(leftovers, "app_download_paths", lambda: {os.path.normpath(rommlib.ROMS_ROOT)})


@pytest.mark.theirs
def test_copies_of_the_users_own_games_are_offered_but_not_their_part_files(isolated):
    """A cue/bin the user copied in beside a proven CHD is offered; another program's .part never is."""
    d = os.path.join(rommlib.ROMS_ROOT, "psx")
    cue_bin(d, "Elit (USA)")
    fake_chd(os.path.join(d, "Elit (USA).chd"))
    open(os.path.join(d, "Mine.iso.part"), "wb").write(b"x")
    assert offered() == {"psx/Elit (USA).cue": "Already here as Elit (USA).chd",
                         "psx/Elit (USA).bin": "Already here as Elit (USA).chd"}
    assert [i for i in rommlib.find_leftovers(chd_busy=False) if i["path"].endswith(".part")] == []


def offered(root=None):
    root = root or rommlib.ROMS_ROOT
    return {os.path.relpath(i["path"], root): i.get("note")
            for i in rommlib.find_leftovers(chd_busy=False) if i["kind"] == COPY}


def test_a_whole_chd_of_the_same_disc_offers_its_cue_and_bin(isolated):
    d = os.path.join(rommlib.ROMS_ROOT, "psx")
    cue_bin(d, "Lorem (USA)")
    fake_chd(os.path.join(d, "Lorem (USA).chd"))
    assert offered() == {"psx/Lorem (USA).cue": "Already here as Lorem (USA).chd",
                         "psx/Lorem (USA).bin": "Already here as Lorem (USA).chd"}


@pytest.mark.parametrize("damage", ["half", "header only", "magic and zeros", "text", "empty", "v4",
                                    "needs a parent", "map past the end", "metadata past the end"])
def test_a_chd_not_proven_whole_never_offers_its_original(isolated, damage):
    d = os.path.join(rommlib.ROMS_ROOT, "psx")
    cue_bin(d, "Ipsum (USA)")
    chd = os.path.join(d, "Ipsum (USA).chd")
    if damage == "v4":
        fake_chd(chd, version=4)
    elif damage == "needs a parent":
        fake_chd(chd, parent=b"\7" * 20)
    else:
        fake_chd(chd)
        size = os.path.getsize(chd)
        cut = {"half": size // 2, "header only": 124, "map past the end": size - 1,
               "metadata past the end": 150}.get(damage)
        if cut is not None:
            os.truncate(chd, cut)
        elif damage == "magic and zeros":
            open(chd, "wb").write(b"MComprHD" + b"\0" * 2000)
        elif damage == "text":
            open(chd, "w").write("not a chd at all\n")
        elif damage == "empty":
            open(chd, "wb").close()
    assert rommlib.chd_layout(chd) is None
    assert offered() == {}
    assert rommlib.delete_leftovers(rommlib.find_leftovers(chd_busy=False)) == 0
    assert os.path.exists(os.path.join(d, "Ipsum (USA).bin"))


def test_a_chd_of_another_image_never_offers_the_original(isolated):
    """Another revision: its track holds 500 frames, the bin here 300."""
    d = os.path.join(rommlib.ROMS_ROOT, "psx")
    cue_bin(d, "Dolor (USA)")
    fake_chd(os.path.join(d, "Dolor (USA).chd"), tracks=(("MODE2_RAW", 500, 0),))
    assert offered() == {}
    # the same frames in another sector size (MODE1/2048 holds 2048 bytes a frame): not the same either
    fake_chd(os.path.join(d, "Dolor (USA).chd"), tracks=(("MODE1", 300, 0),))
    assert offered() == {}


def test_a_gdi_chd_counts_its_tracks_without_the_padding(isolated):
    d = os.path.join(rommlib.ROMS_ROOT, "dreamcast")
    os.makedirs(d)
    for n, frames in ((1, 300), (2, 160), (3, 600)):
        open(os.path.join(d, "Sit (USA) (Track %d).bin" % n), "wb").write(b"\2" * 2352 * frames)
    open(os.path.join(d, "Sit (USA).gdi"), "w").write(
        '3\n1 0 4 2352 "Sit (USA) (Track 1).bin" 0\n2 450 0 2352 "Sit (USA) (Track 2).bin" 0\n'
        '3 45000 4 2352 "Sit (USA) (Track 3).bin" 0\n')
    fake_chd(os.path.join(d, "Sit (USA).chd"), tag=b"CHGD",
             tracks=(("MODE1_RAW", 450, 150), ("AUDIO", 44550, 44390), ("MODE1_RAW", 600, 0)))
    assert set(offered()) == {"dreamcast/Sit (USA).gdi"} | {"dreamcast/Sit (USA) (Track %d).bin" % n
                                                            for n in (1, 2, 3)}


def test_a_dvd_chd_offers_an_iso_of_the_same_size_only(isolated):
    d = os.path.join(rommlib.ROMS_ROOT, "ps2")
    os.makedirs(d)
    open(os.path.join(d, "Amet (USA).iso"), "wb").write(b"\3" * 2048 * 100)
    fake_chd(os.path.join(d, "amet (usa).chd"), dvd_bytes=2048 * 100)
    assert offered() == {"ps2/Amet (USA).iso": "Already here as amet (usa).chd"}
    fake_chd(os.path.join(d, "amet (usa).chd"), dvd_bytes=2048 * 101)
    assert offered() == {}


def test_a_zip_beside_a_chd_is_compared_by_its_tracks_and_a_7z_never(isolated):
    d = os.path.join(rommlib.ROMS_ROOT, "psx")
    os.makedirs(d)
    with zipfile.ZipFile(os.path.join(d, "Elit (USA).zip"), "w") as z:
        z.writestr("Elit (USA).cue", CUE % "Elit (USA).bin")
        z.writestr("Elit (USA).bin", b"\4" * 2352 * 300)
    fake_chd(os.path.join(d, "Elit (USA).chd"))
    open(os.path.join(d, "Sed (USA).7z"), "wb").write(b"7z\xbc\xaf\x27\x1c" + b"\0" * 100)
    fake_chd(os.path.join(d, "Sed (USA).chd"))
    assert offered() == {"psx/Elit (USA).zip": "Already here as Elit (USA).chd"}


def test_a_zip_holding_the_owners_files_besides_the_disc_is_never_offered(isolated):
    """RomM's cue/bin zipped with the owner's memory card and walkthrough beside the CHD: it
    was offered as a copy, and deleting it lost them."""
    d = os.path.join(rommlib.ROMS_ROOT, "psx")
    os.makedirs(d)
    with zipfile.ZipFile(os.path.join(d, "Elit (USA).zip"), "w") as z:
        z.writestr("Elit (USA).cue", CUE % "Elit (USA).bin")
        z.writestr("Elit (USA).bin", b"\4" * 2352 * 300)
        z.writestr("MY SAVE card1.mcr", b"save")
    fake_chd(os.path.join(d, "Elit (USA).chd"))
    assert offered() == {}


def test_a_partial_file_changed_within_the_hour_is_never_offered(isolated, monkeypatch):
    """Another program's .part (a browser download, the owner's own chdman run) was offered as
    the app's partial download a minute after it was written."""
    monkeypatch.setattr(rommlib, "PART_FRESH", 3600)
    d = os.path.join(rommlib.ROMS_ROOT, "gba")
    os.makedirs(d)
    p = os.path.join(d, "MyHack.gba.part")
    open(p, "wb").write(b"half")
    def parts():
        return [i["kind"] for i in rommlib.find_leftovers(chd_busy=False) if i["path"] == p]
    assert parts() == []
    t = time.time() - 7200
    os.utime(p, (t, t))
    assert parts() == ["Partial download"]


def test_discs_a_playlist_lists_are_never_offered(isolated):
    """Deleting the cues an .m3u lists leaves EmulationStation a playlist of missing discs."""
    d = os.path.join(rommlib.ROMS_ROOT, "psx")
    for n in (1, 2):
        stem = "Lorem (USA) (Disc %d)" % n
        cue_bin(d, stem)
        fake_chd(os.path.join(d, stem + ".chd"))
    open(os.path.join(d, "Lorem (USA).m3u"), "w").write("Lorem (USA) (Disc 1).cue\nLorem (USA) (Disc 2).cue\n")
    # a playlist one folder up, naming its disc in a different letter case (exFAT finds it)
    sub = os.path.join(d, "Ipsum (USA)")
    cue_bin(sub, "Ipsum (USA)")
    fake_chd(os.path.join(sub, "Ipsum (USA).chd"))
    open(os.path.join(d, "Ipsum (USA).m3u"), "w").write("ipsum (usa)/IPSUM (USA).CUE\n")
    assert offered() == {}
    items = rommlib.find_leftovers(chd_busy=False)
    rommlib.delete_leftovers(items)
    for n in (1, 2):
        assert os.path.exists(os.path.join(d, "Lorem (USA) (Disc %d).cue" % n))
        assert os.path.exists(os.path.join(d, "Lorem (USA) (Disc %d).bin" % n))


def test_a_track_another_games_sheet_shares_is_never_offered(isolated):
    d = os.path.join(rommlib.ROMS_ROOT, "psx")
    cue_bin(d, "Dolor (USA)", bin_name="Dolor (USA) (Track 1).bin")
    fake_chd(os.path.join(d, "Dolor (USA).chd"))
    open(os.path.join(d, "Dolor (Japan).cue"), "w").write(CUE % "Dolor (USA) (Track 1).bin")   # no CHD of its own
    assert offered() == {"psx/Dolor (USA).cue": "Already here as Dolor (USA).chd"}


def test_an_unreadable_playlist_means_no_copies_are_offered(isolated, monkeypatch):
    d = os.path.join(rommlib.ROMS_ROOT, "psx")
    cue_bin(d, "Sit (USA)")
    fake_chd(os.path.join(d, "Sit (USA).chd"))
    open(os.path.join(d, "Other.m3u"), "w").write("x.cue\n")
    real = rommlib.m3u_paths

    def boom(p):
        if p.endswith("Other.m3u"):
            raise PermissionError(13, "denied")
        return real(p)
    monkeypatch.setattr(rommlib, "m3u_paths", boom)
    assert offered() == {}


def test_a_libcrypt_patch_and_a_subchannel_stay_beside_the_chd(isolated):
    d = os.path.join(rommlib.ROMS_ROOT, "psx")
    cue_bin(d, "Amet (Europe)")
    fake_chd(os.path.join(d, "Amet (Europe).chd"))
    open(os.path.join(d, "Amet (Europe).sbi"), "wb").write(b"SBI\0" + b"\1" * 100)
    open(os.path.join(d, "Amet (Europe).sub"), "wb").write(b"\0" * 96)
    assert set(offered()) == {"psx/Amet (Europe).cue", "psx/Amet (Europe).bin"}
    rommlib.delete_leftovers(rommlib.find_leftovers(chd_busy=False))
    assert sorted(os.listdir(d)) == ["Amet (Europe).chd", "Amet (Europe).sbi", "Amet (Europe).sub"]


def test_a_clonecd_image_is_held_by_its_ccd(isolated):
    d = os.path.join(rommlib.ROMS_ROOT, "psx")
    os.makedirs(d)
    open(os.path.join(d, "Elit (USA).img"), "wb").write(b"\1" * 2352 * 300)
    open(os.path.join(d, "Elit (USA).ccd"), "w").write("[CloneCD]\n")
    fake_chd(os.path.join(d, "Elit (USA).chd"))
    assert offered() == {}


def test_delete_checks_the_converted_game_again(isolated):
    d = os.path.join(rommlib.ROMS_ROOT, "psx")
    cue_bin(d, "Sed (USA)")
    chd = fake_chd(os.path.join(d, "Sed (USA).chd"))
    items = rommlib.find_leftovers(chd_busy=False)
    assert len(items) == 2
    os.truncate(chd, os.path.getsize(chd) // 2)          # cut short after it was listed
    assert rommlib.delete_leftovers(items) == 0
    assert sorted(os.listdir(d)) == ["Sed (USA).bin", "Sed (USA).chd", "Sed (USA).cue"]
    fake_chd(chd)                                         # whole again: the cue and bin go together
    assert rommlib.delete_leftovers(rommlib.find_leftovers(chd_busy=False)) == 2352 * 300 + len(CUE % "Sed (USA).bin")
    assert os.listdir(d) == ["Sed (USA).chd"]


def _rvz(path, disc_size, extra=b""):
    h2 = b"\0" * 0x70
    body = b"RVZ\x01" + b"\0" * 8 + len(h2).to_bytes(4, "big") + hashlib.sha1(h2).digest() \
        + disc_size.to_bytes(8, "big")
    size = 0x48 + len(h2) + len(extra)
    h1 = body + size.to_bytes(8, "big")
    open(path, "wb").write(h1 + hashlib.sha1(h1).digest() + h2 + extra)


def test_an_rvz_offers_a_plain_image_of_its_disc_size_only(isolated):
    d = os.path.join(rommlib.ROMS_ROOT, "gamecube")
    os.makedirs(d)
    open(os.path.join(d, "Lorem (USA).iso"), "wb").write(b"\0" * 4096)
    _rvz(os.path.join(d, "Lorem (USA).rvz"), 4096)
    open(os.path.join(d, "Ipsum (USA).iso"), "wb").write(b"\0" * 4096)
    _rvz(os.path.join(d, "Ipsum (USA).rvz"), 8192)                       # another disc
    open(os.path.join(d, "Dolor (USA).7z"), "wb").write(b"\0" * 10)      # can't be compared
    _rvz(os.path.join(d, "Dolor (USA).rvz"), 4096)
    open(os.path.join(d, "Sit (USA).iso"), "wb").write(b"\0" * 4096)
    _rvz(os.path.join(d, "Sit (USA).rvz"), 4096)
    with open(os.path.join(d, "Sit (USA).rvz"), "ab") as f:              # longer than its header says
        f.write(b"\0")
    assert offered() == {"gamecube/Lorem (USA).iso": "Already here as Lorem (USA).rvz"}


def test_chdman_verify_is_asked_once_per_file_version(isolated, monkeypatch):
    monkeypatch.setattr(leftovers, "_chd_verified", REAL_VERIFIED)
    monkeypatch.setattr(leftovers.shutil, "which", lambda n: "/usr/bin/chdman" if n == "chdman" else None)
    calls = []
    rc = {"v": 1}

    def run(argv, cancel=None, timeout=None, stall=None):
        calls.append(argv[-1])
        return rc["v"], ""
    monkeypatch.setattr(rommlib, "run_check_tool", run)
    monkeypatch.setattr(leftovers, "_CHD_VERIFIED", {})
    d = os.path.join(rommlib.ROMS_ROOT, "psx")
    cue_bin(d, "Amet (USA)")
    chd = fake_chd(os.path.join(d, "Amet (USA).chd"))
    assert offered() == {} and offered() == {}            # fails verify: never offered
    assert len(calls) == 1
    rc["v"] = None                                        # stalled: proves nothing (and would stall again)
    os.utime(chd, ns=(1, 1))                              # another version of the file: asked again
    assert offered() == {} and offered() == {} and len(calls) == 2
    rc["v"] = 0
    os.utime(chd, ns=(2, 2))
    assert len(offered()) == 2 and len(calls) == 3
    assert len(offered()) == 2 and len(calls) == 3
    monkeypatch.setattr(leftovers.shutil, "which", lambda n: None)     # no chdman here: the header decides
    os.utime(chd, ns=(3, 3))
    assert len(offered()) == 2 and len(calls) == 3


@pytest.mark.skipif(not shutil.which("chdman"), reason="chdman isn't installed here")
def test_real_chdman_cut_or_damaged_chd_never_offers_its_original(isolated, monkeypatch):
    monkeypatch.setattr(leftovers, "_chd_verified", REAL_VERIFIED)
    monkeypatch.setattr(leftovers, "_CHD_VERIFIED", {})
    monkeypatch.setattr(rommlib, "CHD_VERIFY_STALL", 3)   # chdman spins at 0% on the flipped one
    d = os.path.join(rommlib.ROMS_ROOT, "psx")
    os.makedirs(d)
    for stem in ("Good (USA)", "Cut (USA)", "Flipped (USA)", "Other (USA)"):
        with open(os.path.join(d, stem + ".bin"), "wb") as f:
            f.write(os.urandom(2352 * 300))
        with open(os.path.join(d, stem + ".cue"), "w") as f:
            f.write(CUE % (stem + ".bin"))
        subprocess.run(["chdman", "createcd", "-i", os.path.join(d, stem + ".cue"),
                        "-o", os.path.join(d, stem + ".chd")], check=True, capture_output=True)
    cut = os.path.join(d, "Cut (USA).chd")
    os.truncate(cut, os.path.getsize(cut) // 2)
    flipped = os.path.join(d, "Flipped (USA).chd")
    with open(flipped, "r+b") as f:                       # one byte of hunk data: only chdman verify sees it
        f.seek(os.path.getsize(flipped) // 2)
        b = f.read(1)
        f.seek(-1, 1)
        f.write(bytes([b[0] ^ 0xff]))
    with open(os.path.join(d, "Other (USA).bin"), "ab") as f:  # another revision of the disc
        f.write(os.urandom(2352 * 50))
    assert offered() == {"psx/Good (USA).cue": "Already here as Good (USA).chd",
                         "psx/Good (USA).bin": "Already here as Good (USA).chd"}
    assert json.dumps(rommlib.chd_layout(os.path.join(d, "Good (USA).chd"))) == \
        json.dumps({"kind": "cd", "bytes": 2352 * 300, "tracks": 1})


def test_a_folder_compression_kept_for_the_users_files_is_never_offered(isolated):
    """Compressing a game in its own folder keeps the folder when it holds the
    user's files and puts the CHD beside it: neither the folder nor its files are junk."""
    d = os.path.join(rommlib.ROMS_ROOT, "psx")
    os.makedirs(d)
    fake_chd(os.path.join(d, "Lorem (USA).chd"))
    kept = os.path.join(d, "Lorem (USA)")
    os.makedirs(os.path.join(kept, "scans"))
    for n in ("manual.pdf", "cover.jpg", "readme.txt", "Lorem (USA).srm", "Thumbs.db", "scans/back.png"):
        open(os.path.join(kept, n), "wb").write(b"\1" * 10)
    junk = os.path.join(d, "Ipsum (USA)")                 # only what an OS leaves: empty
    os.makedirs(junk)
    for n in ("Thumbs.db", ".DS_Store", "desktop.ini", ".directory", "._Ipsum (USA).cue"):
        open(os.path.join(junk, n), "wb").write(b"\0" * 4)
    os.makedirs(os.path.join(d, "Dolor (USA)", "sub"))    # holds a folder: not empty (nor is a game's subfolder)
    items = rommlib.find_leftovers(chd_busy=False)
    assert {os.path.relpath(i["path"], d): (i["kind"], i["size"]) for i in items} == {
        "Ipsum (USA)": ("Empty folder", 20)}
    open(os.path.join(junk, "save.mcd"), "wb").write(b"\1")      # a file of the user's since: kept
    assert rommlib.delete_leftovers(items) == 0
    assert os.path.exists(os.path.join(junk, "save.mcd"))
    os.remove(os.path.join(junk, "save.mcd"))
    assert rommlib.delete_leftovers(rommlib.find_leftovers(chd_busy=False)) == 20
    assert not os.path.exists(junk)
    assert sorted(os.listdir(kept)) == ["Lorem (USA).srm", "Thumbs.db", "cover.jpg", "manual.pdf",
                                        "readme.txt", "scans"]


def test_a_killed_compressions_cue_copy_is_not_listed_nor_holds_tracks(isolated):
    d = os.path.join(rommlib.ROMS_ROOT, "psx")
    cue_bin(d, "Sit (USA)")
    fake_chd(os.path.join(d, "Sit (USA).chd"))
    open(os.path.join(d, rommlib.CHDMAN_CUE + "123-Sit (USA).cue"), "w").write(CUE % "Sit (USA).bin")
    found = {os.path.basename(i["path"]) for i in rommlib.find_leftovers(chd_busy=False)}
    assert found == {"Sit (USA).cue", "Sit (USA).bin"}
