"""Wii U -> .wua with the real wuatool from tools/wiiu-tools/out (skipped where
it isn't built or can't run): install from the pinned archive, the worker's
checked conversion of synthetic .wud/.wux discs (mkdisc.py, made-up keys), its
refusals, and Check games on the result. Synthetic discs use a made-up common
key too, which only wuatool's test option (-T) accepts: the tool the app runs
here is a small wrapper that adds it."""
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile

import pytest

import rommlib
from test_rvz_overlap import _serve

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(APP, "tools", "wiiu-tools"))
pytest.importorskip("cryptography")
import mkdisc  # noqa: E402

TARBALL = os.path.join(APP, "tools", "wiiu-tools", "out", "wiiu-tools-%s-%s.tar.gz" % (
    rommlib.WIIU_TOOLS["version"], rommlib._machine()))
KEYS = {"disc": bytes(range(16, 32)), "title": bytes(range(32, 48)), "common": bytes(range(16))}   # made up
TOP = "%016x_v%d" % (mkdisc.TITLE_ID, mkdisc.TITLE_VERSION)


def _built():
    if not os.path.exists(TARBALL) or not rommlib.wua_supported():
        pytest.skip("wiiu-tools not built here")


@pytest.fixture
def tool(isolated, tmp_path, monkeypatch):
    """The real wuatool, unpacked where the app looks for it, behind a wrapper
    that hands it the made-up common key; Cemu's keys.txt holds the disc key."""
    _built()
    monkeypatch.setenv("ROMM_BROWSER_TOOLS", str(tmp_path / "tools"))
    with tarfile.open(TARBALL) as t:
        t.extractall(str(tmp_path / "unpack"), filter="data")
    d = rommlib.tool_dir(rommlib.TOOL_WIIU)
    os.makedirs(os.path.dirname(d), exist_ok=True)
    shutil.move(str(tmp_path / "unpack" / ("wiiu-tools-" + rommlib.WIIU_TOOLS["version"])), d)
    real = os.path.join(d, "wuatool.real")
    os.rename(os.path.join(d, "wuatool"), real)
    if subprocess.run([real, "--version"], capture_output=True).returncode != 0:
        pytest.skip("wuatool doesn't run on this host (glibc too old?)")
    common = str(tmp_path / "common.txt")
    open(common, "w").write(KEYS["common"].hex() + "\n")
    with open(os.path.join(d, "wuatool"), "w") as f:
        f.write('#!/bin/sh\n[ "$1" = --version ] && exec "%s" --version\ncmd=$1; shift\n'
                'exec "%s" "$cmd" -T "%s" "$@"\n' % (real, real, common))
    os.chmod(os.path.join(d, "wuatool"), 0o755)
    keys = rommlib.cemu_keys_path()
    os.makedirs(os.path.dirname(keys), exist_ok=True)
    open(keys, "w").write("# made up\n%s\n" % KEYS["disc"].hex())
    assert rommlib.wuatool_path() == os.path.join(d, "wuatool")
    return real


def _cfg(monkeypatch, **kw):
    monkeypatch.setattr(rommlib, "load_config", lambda: dict(rommlib.DEFAULT_CONFIG, keep_free_gb=0, **kw))


def _idle(monkeypatch):
    import rb_chd
    monkeypatch.setattr(rb_chd, "running_game", lambda: "")
    monkeypatch.setattr(rommlib, "es_busy", lambda app_busy=False, get=None: "")   # nothing busy in ES
    return rb_chd


def test_install_from_pinned_archive(isolated, tmp_path, monkeypatch):
    _built()
    monkeypatch.setenv("ROMM_BROWSER_TOOLS", str(tmp_path / "tools"))
    data = open(TARBALL, "rb").read()
    assert hashlib.sha256(data).hexdigest() == rommlib.WIIU_TOOLS["sha256"]      # the pin matches the build
    assert len(data) == rommlib.WIIU_TOOLS["size"]
    srv, url = _serve(data)
    try:
        with pytest.raises(rommlib.RommError, match="didn't match its checksum"):
            rommlib.install_tool(rommlib.TOOL_WIIU, url=url, sha256="0" * 64)
        assert rommlib.wuatool_path() is None
        assert rommlib.tool_state(rommlib.TOOL_WIIU, {"wua": True})["label"] == "Tool missing"
        assert rommlib.install_tool(rommlib.TOOL_WIIU, url=url, sha256=rommlib.WIIU_TOOLS["sha256"]) == \
            rommlib.wuatool_path()
        d = rommlib.tool_dir(rommlib.TOOL_WIIU)
        assert sorted(os.listdir(d)) == ["LICENSES", "README.txt", "SOURCE.txt", "wuatool"]
        lic = sorted(os.listdir(os.path.join(d, "LICENSES")))
        assert "LICENSE-Cemu.txt" in lic and "LICENSE-ZArchive.txt" in lic and "COPYRIGHT-OpenSSL.txt" in lic
        assert "Mozilla Public License" in open(os.path.join(d, "LICENSES", "LICENSE-Cemu.txt")).read()
        assert rommlib.WIIU_TOOLS["cemu"] in open(os.path.join(d, "SOURCE.txt")).read()
        assert rommlib.wua_enabled({"wua": True}, "wiiu") and not rommlib.wua_enabled({"wua": True}, "wii")
        assert rommlib.tool_state(rommlib.TOOL_WIIU, {"wua": True})["label"] == "On"
    finally:
        srv.shutdown()


@pytest.mark.parametrize("ext", [".wud", ".wux"])
def test_worker_packs_and_checks_every_file(tool, monkeypatch, ext):
    rb_chd = _idle(monkeypatch)
    _cfg(monkeypatch, wua=True)
    sysdir = os.path.join(rommlib.ROMS_ROOT, "wiiu")
    os.makedirs(sysdir)
    disc = os.path.join(sysdir, "Lorem Kart (USA)" + ext)
    game = mkdisc.make_disc(disc, KEYS, wux=ext == ".wux")
    assert rb_chd.convert({"system": "wiiu", "mode": "wua", "sources": [disc], "roots": [disc]}) is True
    out = os.path.join(sysdir, "Lorem Kart (USA).wua")
    assert os.path.exists(out) and not os.path.exists(disc) and not os.path.exists(out + ".part")
    assert rommlib.zar_check(out, full=True) is None
    for path, data in game.items():                  # Cemu's layout: <title id>_v<version>/<path>
        assert subprocess.run([tool, "cat", out, TOP + "/" + path], capture_output=True).stdout == data, path
    assert rommlib.wua_audit(out, {"name": "x" + ext, "size": 1, "crc": "0", "sha1": ""}, True) is None
    assert not [n for n in os.listdir(os.path.join(rommlib.DATA_DIR, "tmp")) if "key" in n]   # key list gone
    assert "chd: OK Lorem Kart (USA)%s -> Lorem Kart (USA).wua" % ext in open(rommlib.LOG_PATH).read()


def test_refusals_keep_the_image(tool, monkeypatch):
    rb_chd = _idle(monkeypatch)
    _cfg(monkeypatch, wua=True, wux=False)           # with .wux on, a disc without its key becomes one
    sysdir = os.path.join(rommlib.ROMS_ROOT, "wiiu")
    os.makedirs(sysdir)

    def run(p):
        return rb_chd.convert({"system": "wiiu", "mode": "wua", "sources": [p], "roots": [p]})
    junk = os.path.join(sysdir, "Junk.wud")
    open(junk, "wb").write(b"\0" * 0x40000)
    assert run(junk) is False and os.path.exists(junk)
    other = os.path.join(sysdir, "Other Key.wud")                       # no key for it
    mkdisc.make_disc(other, dict(KEYS, disc=bytes(16)))
    assert run(other) is False and os.path.exists(other)
    bad = os.path.join(sysdir, "Damaged.wud")                           # a block that doesn't match its hash
    mkdisc.make_disc(bad, KEYS)
    gm, _ = mkdisc.hashed_cluster_offset(bad)
    mkdisc.flip(bad, gm + 8 * mkdisc.SECTOR + 5 * mkdisc.BLOCK + 0x1234)
    assert run(bad) is False and os.path.exists(bad)
    assert not any(n.endswith((".wua", ".part")) for n in os.listdir(sysdir))
    log = open(rommlib.LOG_PATH).read()
    assert "not a Wii U disc image" in log and "no disc key" in log and "damaged or cut short" in log
    _cfg(monkeypatch, wua=False, wux=False)          # both off: nothing happens
    ok = os.path.join(sysdir, "Off.wud")
    mkdisc.make_disc(ok, KEYS)
    assert run(ok) is False and os.path.exists(ok)


def test_check_games_finds_a_damaged_wua(tool, tmp_path):
    disc = str(tmp_path / "d.wud")
    mkdisc.make_disc(disc, KEYS)
    out = str(tmp_path / "d.wua")
    keys = rommlib.cemu_keys_path()
    subprocess.run([rommlib.wuatool_path(), "pack", "-k", keys, disc, out], check=True, capture_output=True)
    assert rommlib.wua_audit(out, None, False) is None and rommlib.wua_audit(out, None, True) is None
    data = open(out, "rb").read()
    open(out, "wb").write(data[:100] + bytes([data[100] ^ 1]) + data[101:])
    assert rommlib.wua_audit(out, None, False) is None                  # quick: the footer only
    assert rommlib.wua_audit(out, None, True) == ("Compressed file damaged (its SHA-256 doesn't match)", "damaged")
    open(out, "wb").write(data[:-500])
    problem, severity = rommlib.wua_audit(out, None, False)
    assert severity == "damaged" and ".wua" in problem
