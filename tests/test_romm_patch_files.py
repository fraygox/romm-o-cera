"""A patch file (.ips, .bps, .ppf...) lying beside a game comes down with the game on
every RomM: 5.5 lists it under the category "patch" wherever it lies, 5.4 and older
listed one beside the game as a game file. The records below are shaped like the ones
those two servers send for the same folders."""
import os

import pytest

import rommlib

GAME = "Lamp Patched (USA)"
FOLDER = "roms/snes/" + GAME


def _file(name, category, sub="", size=64):
    path = "/".join(p for p in (FOLDER, sub) if p)
    return {"id": sum(name.encode()) % 1000, "file_name": name, "file_size_bytes": size, "category": category,
            "file_path": path, "full_path": path + "/" + name}


def _folder_game(patch_category):
    """A cart in a folder with a patch beside it, one more in patch/, and a manual."""
    return {"id": 7, "fs_name": GAME, "full_path": FOLDER, "has_multiple_files": True,
            "has_nested_single_file": False,
            "files": [_file(GAME + ".ips", patch_category), _file(GAME + ".sfc", "game", size=32768),
                      _file("fix.bps", "patch", "patch"), _file("Manual.pdf", "manual", "manual")]}


@pytest.mark.parametrize("patch_category", ["game", "patch"], ids=["romm-5.4", "romm-5.5"])
def test_a_patch_beside_the_game_is_one_of_its_files(patch_category):
    names = [f["file_name"] for f in rommlib.romm_game_files(_folder_game(patch_category))]
    assert names == [GAME + ".ips", GAME + ".sfc"]             # not patch/fix.bps, not the manual


@pytest.mark.parametrize("category", ["game", "patch"], ids=["romm-5.4", "romm-5.5"])
def test_a_game_that_is_one_patch_file_keeps_its_record(category):
    """A patch stored as a game of its own: its one file is still the file to check the download against."""
    name = "Lamp Hack (USA).ips"
    rom = {"id": 8, "fs_name": name, "fs_extension": "ips", "full_path": "roms/snes/" + name,
           "files": [{"id": 80, "file_name": name, "file_size_bytes": 72, "category": category,
                      "file_path": "roms/snes", "full_path": "roms/snes/" + name}]}
    assert [f["id"] for f in rommlib.romm_game_files(rom)] == [80]


def test_other_categories_and_records_without_paths_stay_as_they_were():
    rom = {"id": 9, "fs_name": GAME, "full_path": FOLDER,
           "files": [{"file_name": "a.sfc", "category": None}, {"file_name": "b.sfc", "category": "game"},
                     {"file_name": "c.ips", "category": "patch"},              # no path: can't tell where it lies
                     _file("d.nsp", "dlc"), _file("e.nsp", "update"), _file("f.pdf", "manual")]}
    assert [f["file_name"] for f in rommlib.romm_game_files(rom)] == ["a.sfc", "b.sfc"]
    assert rommlib.romm_game_files({"id": 1}) == [] and rommlib.romm_game_files({"files": None}) == []
    # a list record (no full_path): a patch is left out, as any file RomM doesn't call the game
    assert rommlib.romm_game_files({"files": [_file("x.ips", "patch")]}) == []


@pytest.mark.parametrize("patch_category", ["game", "patch"], ids=["romm-5.4", "romm-5.5"])
def test_the_download_takes_the_patch_and_leaves_the_patch_folder(isolated, patch_category):
    """The folder game is fetched file by file: the cart and the patch beside it, nothing else."""
    detail = _folder_game(patch_category)

    class Api:
        base = "http://romm.invalid"

        def rom(self, rid):
            return dict(detail)

    d = rommlib.Downloader(Api(), rommlib.Library(), dict(rommlib.DEFAULT_CONFIG), persist=False)
    d.es = {"snes": {".sfc", ".smc", ".zip"}}
    fetched = []

    def fetch(j, url_path, dest, size_hint, record=None):
        fetched.append((url_path, os.path.relpath(dest, rommlib.ROMS_ROOT)))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(b"x" * size_hint)
    d._fetch_new = fetch
    j = rommlib.Job(dict(detail), "snes")
    post = d._process(j)
    assert post["kind"] == "folder"
    assert sorted(dest for _, dest in fetched) == [os.path.join("snes", GAME, GAME + ".ips"),
                                                    os.path.join("snes", GAME, GAME + ".sfc")]
    assert j.total_bytes == 64 + 32768
