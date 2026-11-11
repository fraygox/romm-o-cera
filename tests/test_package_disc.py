"""An Xbox 360 disc that holds no game of its own: default.xex is a stub that
starts a package on the disc. Batocera can't start such a disc (xenia-canary
asks to be restarted, for ever), as an .iso or a .zar. Xbox 360 to .zar keeps
its packages in a folder and writes the one-line .xbox360 launcher Batocera
does start."""
import json
import os
import stat
import sys

import pytest

import rommlib

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


def stub_disc(**more):
    files = {"default.xex": "XEX2stub", "$SystemUpdate/su": "s" * 64, "nxeart": "art",
             TITLE: package(), ADDON: package(0x00000002), UPDATE: "tu" * 40}
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
    # the packages and the game's update stay; the stub, the console update and the art don't
    assert _tree(folder) == sorted([TITLE, ADDON, UPDATE])
    assert open(os.path.join(folder, TITLE), "rb").read() == package().encode("latin-1")
    # the app's record of the game: the launcher and the folder, so deleting it takes both
    assert sorted(rommlib.Library().paths_for(rom, "xbox360")) == [folder, launcher]
    assert "only starts a package" in _log() and not os.listdir(os.path.join(rommlib.DATA_DIR, chd.MADE_DIR))
    # Check games: fine as it is; without its package it can't start
    assert rommlib.output_converter(launcher).mode == "zar"
    assert rommlib.zar_audit(launcher, None, False) is None
    os.remove(os.path.join(folder, TITLE))
    assert rommlib.zar_audit(launcher, None, False) == ("The package its launcher starts is missing", "damaged")


def test_deleting_the_game_takes_the_launcher_and_the_packages(chd):
    sysdir, src = _image("Lorem Ipsum (World).iso", stub_disc())
    rom = {"id": 7, "fs_name": "Lorem Ipsum (World).iso", "fs_name_no_ext": "Lorem Ipsum (World)"}
    rommlib.Library().mark(rom, "xbox360", [src])
    assert _convert(chd, src) is True
    for p in rommlib.Library().paths_for(rom, "xbox360"):
        rommlib.remove_game_path(p, "xbox360")
    assert os.listdir(sysdir) == []


def test_a_file_that_isnt_a_title_package_is_packed_as_before(chd):
    sysdir, src = _image("Dolor (World).iso", stub_disc(**{TITLE: "not a package" * 100}), pad=4096)
    assert _convert(chd, src) is True
    assert os.listdir(sysdir) == ["Dolor (World).zar"]


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
    assert _tree(folder) == sorted([TITLE, ADDON, UPDATE])
