"""A conversion stopped half-way (the worker's SIGTERM: updating, Batocera
shutting down) or failing leaves no half-written output behind, and never
touches the sources. Every converter path: chdman, dolphin-tool, mksquashfs,
extract-xiso + zarchive, and the in-process ones (.cso decoding, Z3DS, PS3
folders), plus make_xiso's copy."""
import glob
import os
import shutil
import signal
import stat
import sys
import threading

import pytest

import rommlib
from test_cso import make_cso, make_iso
from test_disk import _xbox_image
from test_ps3 import KEY, _keys, _zip, make_disc

# one stand-in for every external converter, picked by the name it's run as:
# it writes the start of its output, then works "forever" until it's stopped
FAKE_TOOL = r'''#!%s
import os, sys, time
name, a = os.path.basename(sys.argv[0]), sys.argv[1:]

def hang(out):
    with open(out, "wb") as f:
        f.write(b"half written")
        f.flush()
    time.sleep(60)
    sys.exit(1)

if name == "chdman":
    if a[0] in ("verify", "info"):
        sys.exit(0)
    hang(a[a.index("-o") + 1])
elif name == "dolphin-tool":
    if a[0] == "verify":
        print("CRC32: 0badc0de")
        sys.exit(0)
    hang(a[a.index("-o") + 1])
elif name == "mksquashfs":
    hang(a[1])
elif name == "extract-xiso":
    d = a[a.index("-d") + 1]
    os.makedirs(d)
    open(os.path.join(d, "default.xex"), "wb").write(b"XEX2")
elif name == "zarchive":
    hang(a[1])
'''
TOOLS = ("chdman", "dolphin-tool", "mksquashfs", "extract-xiso", "zarchive")


@pytest.fixture
def chd(isolated, tmp_path, monkeypatch):
    """rb_chd with the fake tools on PATH, nothing playing, no keep-free reserve."""
    b = tmp_path / "bin"
    b.mkdir()
    for name in TOOLS:
        exe = b / name
        exe.write_text(FAKE_TOOL % sys.executable)
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", "%s:%s" % (b, os.environ["PATH"]))
    import rb_chd
    monkeypatch.setattr(rb_chd, "running_game", lambda: "")
    monkeypatch.setattr(rb_chd, "TOOL_POLL", 0.05)
    monkeypatch.setattr(rommlib, "keep_free_bytes", lambda cfg: 0)
    monkeypatch.setattr(rb_chd, "_test_bin", b, raising=False)
    return rb_chd


def _stop_once_written(chd, monkeypatch, sysdir, pattern="*.part"):
    """SIGTERM the worker as soon as a converter has started writing its
    output (asked between steps: rb_chd checks busy_reason all the time)."""
    def busy():
        if glob.glob(os.path.join(glob.escape(sysdir), pattern)):
            if threading.current_thread() is threading.main_thread():
                os.kill(os.getpid(), signal.SIGTERM)
            else:
                chd._sigterm(signal.SIGTERM, None)
        return ""
    monkeypatch.setattr(chd, "busy_reason", busy)
    if threading.current_thread() is threading.main_thread():
        old = signal.signal(signal.SIGTERM, chd._sigterm)
        monkeypatch.setattr(chd, "_restore", lambda: signal.signal(signal.SIGTERM, old), raising=False)
    else:
        monkeypatch.setattr(chd, "_restore", lambda: None, raising=False)


def _stopped(chd, task):
    try:
        with pytest.raises(SystemExit):
            chd.convert(task)
    finally:
        chd._restore()


def _w(p, data=b"x" * 4096):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    return p


def _left(sysdir):
    return sorted(os.listdir(sysdir))


def test_stopped_chdman_leaves_no_part(chd, monkeypatch):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    track = _w(os.path.join(sysdir, "Lorem (World).bin"))
    cue = _w(os.path.join(sysdir, "Lorem (World).cue"), b'FILE "Lorem (World).bin" BINARY\n  TRACK 01 MODE2/2352\n')
    _stop_once_written(chd, monkeypatch, sysdir)
    _stopped(chd, {"system": "psx", "mode": "createcd", "sources": [cue], "roots": [cue]})
    assert _left(sysdir) == ["Lorem (World).bin", "Lorem (World).cue"]
    assert open(track, "rb").read() == b"x" * 4096


@pytest.mark.parametrize("during", ["*" + rommlib.CSO_TEMP_SUFFIX, "*.chd.part"])   # decoding, chdman
def test_stopped_cso_leaves_no_part_and_no_decoded_iso(chd, monkeypatch, during):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psp")
    data = make_cso(make_iso())
    src = _w(os.path.join(sysdir, "Ipsum (World).cso"), data)
    _stop_once_written(chd, monkeypatch, sysdir, during)
    _stopped(chd, {"system": "psp", "mode": "cso", "sources": [src], "roots": [src]})
    assert _left(sysdir) == ["Ipsum (World).cso"] and open(src, "rb").read() == data


def test_stopped_rvz_leaves_no_part(chd, monkeypatch):
    monkeypatch.setattr(rommlib, "rvz_enabled", lambda cfg: True)
    monkeypatch.setattr(rommlib, "dolphin_tool_path", lambda: str(chd._test_bin / "dolphin-tool"))
    sysdir = os.path.join(rommlib.ROMS_ROOT, "gamecube")
    src = _w(os.path.join(sysdir, "Dolor (World).iso"))
    _stop_once_written(chd, monkeypatch, sysdir)
    _stopped(chd, {"system": "gamecube", "mode": "rvz", "sources": [src], "roots": [src]})
    assert _left(sysdir) == ["Dolor (World).iso"]


def test_stopped_squashfs_leaves_no_part(chd, monkeypatch):
    monkeypatch.setattr(rommlib, "squashfs_enabled", lambda cfg, system: True)
    sysdir = os.path.join(rommlib.ROMS_ROOT, "xbox")
    os.makedirs(sysdir)
    src = os.path.join(sysdir, "Sit Amet (World).iso")
    _xbox_image(src, 0)                                       # an XISO already: nothing to cut
    _stop_once_written(chd, monkeypatch, sysdir)
    _stopped(chd, {"system": "xbox", "mode": "squashfs", "sources": [src], "roots": [src]})
    assert _left(sysdir) == ["Sit Amet (World).iso"] and rommlib.xbox_partition(src) == 0


def test_stopped_zar_leaves_no_part_and_no_extracted_files(chd, monkeypatch):
    monkeypatch.setattr(rommlib, "zar_enabled", lambda cfg, system: True)
    monkeypatch.setattr(rommlib, "x360_tools", lambda: (str(chd._test_bin / "extract-xiso"), str(chd._test_bin / "zarchive")))
    monkeypatch.setattr(rommlib, "xiso_listing", lambda xiso, src: {"default.xex": 4})
    sysdir = os.path.join(rommlib.ROMS_ROOT, "xbox360")
    src = _w(os.path.join(sysdir, "Consectetur (World).iso"))
    _stop_once_written(chd, monkeypatch, sysdir)
    _stopped(chd, {"system": "xbox360", "mode": "zar", "sources": [src], "roots": [src]})
    assert _left(sysdir) == ["Consectetur (World).iso"]
    assert not glob.glob(os.path.join(rommlib.tmp_dir(), "zar-*"))


def test_stopped_z3ds_leaves_no_part(chd, monkeypatch):
    if not rommlib.z3ds_supported():
        pytest.skip("no libzstd here")
    from test_z3ds import _cart
    sysdir = os.path.join(rommlib.ROMS_ROOT, "3ds")
    src = _cart(os.path.join(sysdir, "Adipiscing (USA).3ds"))
    data = open(src, "rb").read()
    _stop_once_written(chd, monkeypatch, sysdir)
    _stopped(chd, {"system": "3ds", "mode": "z3ds", "sources": [src], "roots": [src]})
    assert _left(sysdir) == ["Adipiscing (USA).3ds"] and open(src, "rb").read() == data


def test_stopped_ps3_leaves_no_half_folder(chd, monkeypatch):
    cfg = dict(rommlib.DEFAULT_CONFIG, ps3_decrypt=True)
    monkeypatch.setattr(rommlib, "load_config", lambda: dict(cfg))
    sysdir = os.path.join(rommlib.ROMS_ROOT, "ps3")
    _keys(**{"Ipsum Quest (USA)": KEY})
    src = _zip(os.path.join(sysdir, "Ipsum Quest (USA).zip"), "Ipsum Quest (USA).iso", make_disc())
    data = open(src, "rb").read()
    _stop_once_written(chd, monkeypatch, sysdir)
    _stopped(chd, {"system": "ps3", "mode": "ps3", "sources": [src], "roots": [src]})
    assert _left(sysdir) == ["Ipsum Quest (USA).zip"] and open(src, "rb").read() == data


@pytest.mark.parametrize("error", [SystemExit(0), RuntimeError("boom"), rommlib.RommError("odd")])
def test_any_converter_error_drops_its_output(chd, monkeypatch, error):
    """Whatever a converter raises (not only a failed exit code), its output
    goes and the sources stay; the error still reaches the worker."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, "ps2")
    src = _w(os.path.join(sysdir, "Lorem (World).iso"))

    def runner(src, tmp, threads):
        _w(tmp, b"half written")
        raise error
    monkeypatch.setitem(chd.RUNNERS, "createdvd", runner)
    with pytest.raises(type(error)):
        chd.convert({"system": "ps2", "mode": "createdvd", "sources": [src], "roots": [src]})
    assert _left(sysdir) == ["Lorem (World).iso"]


@pytest.mark.parametrize("error", [SystemExit(0), OSError(28, "No space left on device")])
def test_make_xiso_copy_stopped_leaves_no_half_copy(isolated, tmp_path, monkeypatch, error):
    monkeypatch.setattr(rommlib, "_collapse_front", lambda p, n: False)     # exFAT/btrfs: through a copy
    off = rommlib.XBOX_PARTITIONS[1]
    d = tmp_path / "xbox"
    d.mkdir()
    p = str(d / "Lorem Odyssey (USA).iso")
    _xbox_image(p, off)
    size = os.path.getsize(p)

    def copy(src, dst, n=0):
        dst.write(src.read(1 << 16))
        raise error
    monkeypatch.setattr(shutil, "copyfileobj", copy)
    with pytest.raises(type(error)):
        rommlib.make_xiso(p, free=1 << 40)
    assert os.listdir(str(d)) == ["Lorem Odyssey (USA).iso"]
    assert os.path.getsize(p) == size and rommlib.xbox_partition(p) == off
