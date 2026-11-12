"""An Xbox 360 disc that holds no game of its own: default.xex is a stub that
starts a package on the disc. Batocera can't start such a disc (xenia-canary
asks to be restarted, for ever), as an .iso or a .zar. Xbox 360 to .zar keeps
its packages in a folder and writes the one-line .xbox360 launcher Batocera
does start, with the game's add-ons and update unpacked and linked where
xenia looks for them; and a .zar that would be no smaller than the image is never made."""
import json
import os
import stat
import sys

import pytest

import rommlib
from stfs_build import build_package

# extract-xiso and zarchive stand-ins. A test "image" is a JSON {path: text}.
FAKE_TOOL = r'''#!%s
import json, os, sys
name, a = os.path.basename(sys.argv[0]), sys.argv[1:]

def put(root, files):
    for rel, text in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(text.encode("latin-1"))

if name == "extract-xiso":
    put(a[a.index("-d") + 1], json.load(open(a[-1])))
elif os.path.isdir(a[0]):                       # zarchive folder -> archive
    files = {}
    for dp, _, fns in os.walk(a[0]):
        for fn in fns:
            p = os.path.join(dp, fn)
            files[os.path.relpath(p, a[0])] = open(p, "rb").read().decode("latin-1")
    with open(a[1], "w") as f:
        json.dump(files, f)
        f.write(" " * int(os.environ.get("FAKE_ZAR_PAD", "0")))
else:                                           # zarchive archive -> folder
    put(a[1], json.load(open(a[0])))
'''

TITLE = "content/0000000000000000/4D2A0001/000D0000/AB12CD34"
ADDON = "content/0000000000000000/4D2A0001/00000002/EF56AB78"
UPDATE = "$TitleUpdate/4D2A0001/tu00000001_00000000"


def package(kind=0x000D0000, magic="PIRS", size=0x400):
    head = bytearray(size)
    head[:4] = magic.encode()
    head[0x344:0x348] = kind.to_bytes(4, "big")
    return bytes(head).decode("latin-1")


TITLE_FILES = {"default.xex": b"XEX2" + b"game" * 2000, "data.ttarch2": b"d" * 5000}
ADDON_FILES = {"spa.bin": b"spa", "08.png": b"\x89PNG picture", "data/ep2.ttarch2": b"e" * 9000}
UPDATE_FILES = {"default.xexp": b"patch" * 300}


def _text(b):
    return b.decode("latin-1")


def stub_disc(**more):
    files = {"default.xex": "XEX2stub", "$SystemUpdate/su": "s" * 64, "nxeart": "art",
             TITLE: _text(build_package(TITLE_FILES, 0x000D0000, 0x4D2A0001)),
             ADDON: _text(build_package(ADDON_FILES, 0x00000002, 0x4D2A0001, folders=("data",))),
             UPDATE: _text(build_package(UPDATE_FILES, 0x000B0000, 0x4D2A0001, magic=b"LIVE"))}
    files.update(more)
    return files


@pytest.fixture
def chd(isolated, tmp_path, monkeypatch):
    b = tmp_path / "bin"
    b.mkdir()
    for name in ("extract-xiso", "zarchive"):
        exe = b / name
        exe.write_text(FAKE_TOOL % sys.executable)
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    import rb_chd
    monkeypatch.setattr(rb_chd, "running_game", lambda: "")
    monkeypatch.setattr(rb_chd, "busy_reason", lambda: "")
    monkeypatch.setattr(rb_chd, "TOOL_POLL", 0.05)
    monkeypatch.setattr(rommlib, "keep_free_bytes", lambda cfg: 0)
    monkeypatch.setattr(rommlib, "zar_enabled", lambda cfg, system: True)
    monkeypatch.setattr(rommlib, "x360_tools", lambda: (str(b / "extract-xiso"), str(b / "zarchive")))
    monkeypatch.setattr(rommlib, "xiso_listing", lambda xiso, src: {
        r: len(t.encode("latin-1")) for r, t in json.load(open(src)).items()})
    monkeypatch.setattr(rommlib, "zar_check", lambda path, full=False, cancel=None: None)
    return rb_chd


def _image(name, files, pad=0):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "xbox360")
    os.makedirs(sysdir, exist_ok=True)
    src = os.path.join(sysdir, name)
    with open(src, "w") as f:
        json.dump(files, f)
        f.write(" " * pad)
    return sysdir, src


def _convert(chd, src):
    return chd.convert({"system": "xbox360", "mode": "zar", "sources": [src], "roots": [src]})


def _tree(root):
    return sorted(os.path.relpath(os.path.join(dp, f), root) for dp, _, fs in os.walk(root) for f in fs)


def _log():
    return open(rommlib.LOG_PATH).read()


# ------------------------------------------------------------------ which discs

def test_only_a_stub_with_one_title_package_is_a_package_disc(chd):
    sizes = {r: len(t) for r, t in stub_disc().items()}
    assert chd.package_disc(sizes) == TITLE
    assert chd.package_disc(dict(sizes, **{"default.xex": 6 << 20})) is None          # a game of its own
    assert chd.package_disc(dict(sizes, **{"game/main.xex": 9})) is None               # the stub may start that
    assert chd.package_disc(dict(sizes, **{TITLE[:-8] + "FFFF0000": 9})) is None       # two games: which one?
    assert chd.package_disc({r: n for r, n in sizes.items() if r != TITLE}) is None    # add-ons only
    assert chd.package_disc({r: n for r, n in sizes.items() if r != "default.xex"}) is None
    upper = {r.upper() if r == TITLE else r: n for r, n in sizes.items()}             # a disc names them anyhow
    assert chd.package_disc(upper) == TITLE.upper()


# ------------------------------------------------------------------ what it becomes

def test_a_package_disc_becomes_a_launcher_and_its_packages(chd):
    sysdir, src = _image("Lorem Ipsum (World).iso", stub_disc())
    lib = rommlib.Library()
    rom = {"id": 7, "fs_name": "Lorem Ipsum (World).iso", "fs_name_no_ext": "Lorem Ipsum (World)"}
    lib.mark(rom, "xbox360", [src])
    assert _convert(chd, src) is True
    launcher = os.path.join(sysdir, "Lorem Ipsum (World).xbox360")
    folder = os.path.join(sysdir, "Lorem Ipsum (World).packages")
    assert sorted(os.listdir(sysdir)) == ["Lorem Ipsum (World).packages", "Lorem Ipsum (World).xbox360"]
    # the launcher names the title's package the way Batocera reads it: one line, from its own folder
    assert open(launcher).read() == "Lorem Ipsum (World).packages/%s\n" % TITLE
    assert rommlib.launcher_target(launcher) == os.path.join(folder, TITLE)
    # the title's package stays as it is; the add-on and the update are unpacked in their
    # places; the stub, the console update and the art don't stay
    assert _tree(folder) == sorted([TITLE] + [ADDON + "/" + f for f in ADDON_FILES]
                                   + [UPDATE + "/" + f for f in UPDATE_FILES])
    assert open(os.path.join(folder, ADDON, "data/ep2.ttarch2"), "rb").read() == ADDON_FILES["data/ep2.ttarch2"]
    # ... and linked where xenia looks for a title's add-ons and update
    content = os.path.join(rommlib.SAVES_ROOT, "xbox360", "0000000000000000", "4D2A0001")
    assert os.path.realpath(os.path.join(content, "00000002", "EF56AB78")) == os.path.join(folder, ADDON)
    assert os.path.realpath(os.path.join(content, "000B0000", "tu00000001_00000000")) == os.path.join(folder, UPDATE)
    assert "2 of 2 add-ons and updates" in _log()
    assert rommlib.stfs_listing(os.path.join(folder, TITLE)) == {k: len(v) for k, v in TITLE_FILES.items()}
    # the app's record of the game: the launcher and the folder, so deleting it takes both
    assert sorted(rommlib.Library().paths_for(rom, "xbox360")) == [folder, launcher]
    assert "only starts a package" in _log() and not os.listdir(os.path.join(rommlib.DATA_DIR, chd.MADE_DIR))
    # Check games: fine as it is; without its package it can't start
    check = rommlib.output_converter(launcher)
    assert check.mode == "xpkg" and check.audit(launcher, None, False) is None
    os.remove(os.path.join(folder, TITLE))
    assert check.audit(launcher, None, False) == ("The package its launcher starts is missing", "damaged")


def test_deleting_the_game_takes_the_launcher_and_the_packages(chd):
    sysdir, src = _image("Lorem Ipsum (World).iso", stub_disc())
    rom = {"id": 7, "fs_name": "Lorem Ipsum (World).iso", "fs_name_no_ext": "Lorem Ipsum (World)"}
    rommlib.Library().mark(rom, "xbox360", [src], files=[src])
    assert _convert(chd, src) is True
    gone, freed = rommlib.Library().uninstall(rom, "xbox360")
    assert len(gone) == 2 and freed > 0 and os.listdir(sysdir) == []     # its pictures and data too
    # the links it leaves in xenia's content lead nowhere: xenia lists only folders
    assert os.path.islink(_content("00000002", "EF56AB78")) and not os.path.exists(_content("00000002", "EF56AB78"))
    assert rommlib.Library().paths_for(rom, "xbox360") == []


def test_a_file_in_the_titles_place_that_isnt_a_package_keeps_the_image(chd):
    sysdir, src = _image("Dolor (World).iso", stub_disc(**{TITLE: "not a package" * 100}), pad=4096)
    assert _convert(chd, src) is False
    assert os.listdir(sysdir) == ["Dolor (World).iso"] and "not a disc that only starts a package" in _log()


def test_someone_elses_folder_of_that_name_is_never_replaced(chd):
    sysdir, src = _image("Lorem Ipsum (World).iso", stub_disc())
    theirs = os.path.join(sysdir, "Lorem Ipsum (World).packages", "notes.txt")
    os.makedirs(os.path.dirname(theirs))
    open(theirs, "w").write("mine")
    assert _convert(chd, src) is False
    assert sorted(os.listdir(sysdir)) == ["Lorem Ipsum (World).iso", "Lorem Ipsum (World).packages"]
    assert _tree(os.path.dirname(theirs)) == ["notes.txt"] and "is already here" in _log()


def test_someone_elses_launcher_of_that_name_is_never_replaced(chd):
    sysdir, src = _image("Lorem Ipsum (World).iso", stub_disc())
    theirs = os.path.join(sysdir, "Lorem Ipsum (World).xbox360")
    open(theirs, "w").write("elsewhere/package\n")
    assert _convert(chd, src) is False
    assert sorted(os.listdir(sysdir)) == ["Lorem Ipsum (World).iso", "Lorem Ipsum (World).xbox360"]
    assert open(theirs).read() == "elsewhere/package\n"


def test_an_interrupted_runs_folder_is_made_again(chd):
    """Killed after the packages folder landed, before the image went: the next
    run finishes the game instead of skipping it for ever."""
    sysdir, src = _image("Lorem Ipsum (World).iso", stub_disc())
    folder = os.path.join(sysdir, "Lorem Ipsum (World).packages")
    os.makedirs(os.path.join(folder, "content"))
    chd.mark_made(folder, [src], folder)
    assert _convert(chd, src) is True
    assert sorted(os.listdir(sysdir)) == ["Lorem Ipsum (World).packages", "Lorem Ipsum (World).xbox360"]
    assert os.path.isfile(os.path.join(folder, TITLE)) and os.path.isdir(os.path.join(folder, ADDON))


# ------------------------------------------------------------------ add-ons for xenia

def _content(*more):
    return os.path.join(rommlib.SAVES_ROOT, "xbox360", "0000000000000000", "4D2A0001", *more)


def test_an_add_on_xenia_already_has_is_left_alone(chd):
    """Installed from xenia's own menu: a real folder in its content. Never replaced or linked over."""
    sysdir, src = _image("Lorem Ipsum (World).iso", stub_disc())
    theirs = _content("00000002", "EF56AB78", "spa.bin")
    os.makedirs(os.path.dirname(theirs))
    open(theirs, "w").write("installed by hand")
    assert _convert(chd, src) is True
    assert not os.path.islink(_content("00000002", "EF56AB78")) and open(theirs).read() == "installed by hand"
    assert os.path.islink(_content("000B0000", "tu00000001_00000000"))
    assert "xenia already has that add-on" in _log() and "1 of 2 add-ons and updates" in _log()


def test_a_link_a_deleted_game_left_is_replaced_and_the_owners_own_link_never(chd):
    sysdir, src = _image("Lorem Ipsum (World).iso", stub_disc())
    os.makedirs(_content("00000002"))
    os.makedirs(_content("000B0000"))
    # the app's own, from a copy of the game deleted since: leads into a ".packages" folder that's gone
    os.symlink(os.path.join(sysdir, "Old Name (World).packages", ADDON), _content("00000002", "EF56AB78"))
    # the owner's: the update kept on a drive that isn't plugged in now
    os.symlink("/elsewhere/xbox360/update", _content("000B0000", "tu00000001_00000000"))
    assert _convert(chd, src) is True
    assert os.path.isfile(_content("00000002", "EF56AB78", "spa.bin"))
    assert os.readlink(_content("000B0000", "tu00000001_00000000")) == "/elsewhere/xbox360/update"
    assert "1 of 2 add-ons and updates" in _log()


def test_a_launcher_that_cant_be_written_leaves_no_folder_behind(chd, monkeypatch):
    sysdir, src = _image("Lorem Ipsum (World).iso", stub_disc())
    real = rommlib.atomic_write_text

    def full(path, text, **kw):
        if path.endswith(".xbox360.part"):
            raise OSError(28, "No space left on device")
        return real(path, text, **kw)
    monkeypatch.setattr(rommlib, "atomic_write_text", full)
    assert _convert(chd, src) is False
    assert os.listdir(sysdir) == ["Lorem Ipsum (World).iso"] and "launcher couldn't be written" in _log()


def test_a_damaged_add_on_keeps_the_image(chd):
    """A block that doesn't match the package's own SHA-1: the disc image is damaged."""
    bad = bytearray(build_package(ADDON_FILES, 0x00000002, 0x4D2A0001, folders=("data",)))
    bad[-100] ^= 0xFF
    sysdir, src = _image("Lorem Ipsum (World).iso", stub_disc(**{ADDON: _text(bytes(bad))}))
    assert _convert(chd, src) is False
    assert os.listdir(sysdir) == ["Lorem Ipsum (World).iso"] and "doesn't match its SHA-1" in _log()
    assert not os.path.lexists(_content("00000002", "EF56AB78"))


def test_a_package_in_an_add_ons_place_that_isnt_one_stays_packed(chd):
    """Another title's package, or another kind, under the add-ons' folder: kept, not unpacked or linked."""
    other = _text(build_package(ADDON_FILES, 0x00000002, 0x4D2A0099, folders=("data",)))
    sysdir, src = _image("Lorem Ipsum (World).iso", stub_disc(**{ADDON: other}))
    assert _convert(chd, src) is True
    folder = os.path.join(sysdir, "Lorem Ipsum (World).packages")
    assert os.path.isfile(os.path.join(folder, ADDON)) and not os.path.lexists(_content("00000002", "EF56AB78"))


# ------------------------------------------------------------------ a .zar only when it's a gain

def test_a_disc_with_its_own_game_still_becomes_a_zar(chd):
    files = {"default.xex": "XEX2" + "g" * (2 << 20), "data/level.pak": "p" * 512}
    sysdir, src = _image("Dolor (World).iso", files, pad=4096)
    assert _convert(chd, src) is True
    assert os.listdir(sysdir) == ["Dolor (World).zar"]


def test_a_zar_no_smaller_than_the_image_is_not_kept(chd, monkeypatch):
    files = {"default.xex": "XEX2" + "g" * (2 << 20), "data/level.pak": "p" * 512}
    sysdir, src = _image("Dolor (World).iso", files)
    monkeypatch.setenv("FAKE_ZAR_PAD", "64")
    before = open(src).read()
    assert _convert(chd, src) is False
    assert os.listdir(sysdir) == ["Dolor (World).iso"] and open(src).read() == before
    assert "no smaller than the image" in _log()
