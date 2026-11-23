"""Deleting one game reaches its own records, not every one: a batch of thousands took minutes (#13)."""
import copy
import os

import rommlib
from romm import library as _library


def _library_of(n):
    lib = rommlib.Library()
    sysdir = os.path.join(rommlib.ROMS_ROOT, "nes")
    os.makedirs(sysdir)
    for i in range(n):
        p = os.path.join(sysdir, "g%03d.zip" % i)
        with open(p, "wb") as f:
            f.write(b"x")
        lib.mark({"id": i, "name": "g%d" % i, "fs_name": "g%03d.zip" % i}, "nes", [p])
    return lib, sysdir


def _counting(monkeypatch):
    n = {"checked": 0, "copied": 0}
    real_files, real_copy = _library.owned_files, copy.deepcopy

    def owned_files(rec):
        n["checked"] += 1
        return real_files(rec)

    def deepcopy(x, *a):
        n["copied"] += 1
        return real_copy(x, *a)
    monkeypatch.setattr(_library, "owned_files", owned_files)
    monkeypatch.setattr(_library.copy, "deepcopy", deepcopy)
    return n


def test_deleting_a_downloaded_game_checks_only_its_own_records(isolated, monkeypatch):
    lib, _ = _library_of(200)
    n = _counting(monkeypatch)
    assert lib.uninstall({"id": 7, "name": "g7"}, "nes")[0]
    assert n["checked"] <= 2 and n["copied"] <= 2, n
    assert "7" not in lib.snapshot() and len(lib.snapshot()) == 199


def test_free_up_space_deleting_a_game_checks_only_its_own_records(isolated, monkeypatch):
    lib, sysdir = _library_of(200)
    n = _counting(monkeypatch)
    gone, _ = rommlib.delete_game("nes", os.path.join(sysdir, "g007.zip"), lib)
    assert gone and n["checked"] <= 2 and n["copied"] <= 2, n
    assert "7" not in lib.snapshot() and len(lib.snapshot()) == 199


def test_a_delete_still_keeps_a_file_put_in_the_place_of_the_apps(isolated):
    """Scoping the check to the game's records keeps the rule: a file swapped in since is the owner's."""
    lib, sysdir = _library_of(20)
    p = os.path.join(sysdir, "g003.zip")
    os.remove(p)
    with open(p, "wb") as f:
        f.write(b"theirs, a different size")
    try:
        lib.uninstall({"id": 3, "name": "g3"}, "nes")
    except rommlib.RommError:
        pass
    assert os.path.exists(p)


def test_free_up_space_deleting_a_copied_game_checks_only_records_near_it(isolated, monkeypatch):
    """A game put here another way, at the top of a system folder full of the app's downloads."""
    lib, sysdir = _library_of(200)
    theirs = os.path.join(sysdir, "copied.zip")
    with open(theirs, "wb") as f:
        f.write(b"theirs")
    n = _counting(monkeypatch)
    gone, _ = rommlib.delete_game("nes", theirs, lib)
    assert gone == [theirs] and n["checked"] <= 2, n
    assert len(lib.snapshot()) == 200


def test_free_up_space_deleting_a_folder_download_drops_its_record(isolated):
    """A multi-file download is recorded as its folder; ES lists the playlist inside it. Deleting
    it took the files and the folder but left the record behind (found in review)."""
    lib = rommlib.Library()
    folder = os.path.join(rommlib.ROMS_ROOT, "psx", "Game")
    os.makedirs(folder)
    files = []
    for n in ("Game.m3u", "Game (Disc 1).chd", "Game (Disc 2).chd"):
        files.append(os.path.join(folder, n))
        with open(files[-1], "w") as f:
            f.write(n + "\n" if n.endswith(".m3u") else "x")
    with open(files[0], "w") as f:
        f.write("Game (Disc 1).chd\nGame (Disc 2).chd\n")
    lib.mark({"id": 7, "name": "Game", "fs_name": "Game.zip"}, "psx", [folder], files=files)
    rommlib.delete_game("psx", files[0], lib)
    assert not os.path.exists(folder) and "7" not in lib.snapshot()


def test_a_copied_games_mac_file_the_app_wrote_stays(isolated):
    """A "._" file one of the app's records holds is the app's: never taken with a copied game."""
    lib, sysdir = _library_of(1)
    theirs = os.path.join(sysdir, "copied.zip")
    with open(theirs, "wb") as f:
        f.write(b"theirs")
    mac = os.path.join(sysdir, "._copied.zip")
    with open(mac, "wb") as f:
        f.write(b"\x00\x05\x16\x07" + b"\x00" * 60)
    lib.mark({"id": 50, "name": "odd", "fs_name": "._copied.zip"}, "nes", [mac])
    rommlib.delete_game("nes", theirs, lib)
    assert not os.path.exists(theirs) and os.path.exists(mac)
