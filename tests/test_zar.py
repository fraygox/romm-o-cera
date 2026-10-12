"""Xbox 360 -> .zar with the real extract-xiso + zarchive from
tools/xbox360-tools/out (skipped where they aren't built or can't run, e.g. a
host older than glibc 2.36): install from a pinned archive, the worker's
checked conversion and its refusals, the footer check, queueing and planning."""
import hashlib
import json
import os
import random
import shutil
import subprocess
import tarfile

import pytest

import rommlib
from test_rvz_overlap import _serve

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARBALL = os.path.join(APP, "tools", "xbox360-tools", "out",
                       "xbox360-tools-%s-x86_64.tar.gz" % rommlib.X360_TOOLS["version"])


def _runs(exe):
    try:
        return subprocess.run([exe, "-h"], capture_output=True, timeout=10).returncode in (0, 1)
    except OSError:
        return False


@pytest.fixture
def tools(isolated, tmp_path, monkeypatch):
    """The real tools, unpacked where the app looks for them."""
    if not os.path.exists(TARBALL) or not rommlib.zar_supported():
        pytest.skip("xbox360-tools not built here")
    monkeypatch.setenv("ROMM_BROWSER_TOOLS", str(tmp_path / "tools"))
    with tarfile.open(TARBALL) as t:
        t.extractall(str(tmp_path / "unpack"))
    os.makedirs(tmp_path / "tools", exist_ok=True)
    shutil.move(str(tmp_path / "unpack" / ("xbox360-tools-" + rommlib.X360_TOOLS["version"])),
                rommlib.tool_dir(rommlib.TOOL_X360))
    exe = rommlib.x360_tools()
    if not exe or not _runs(exe[0]):
        pytest.skip("xbox360-tools don't run on this host (glibc too old?)")
    return exe


def _disc(xiso, path, with_xex=True, seed=7):
    """A tiny XDVDFS game disc made with extract-xiso -c."""
    src = path + ".src"
    rnd = random.Random(seed)
    os.makedirs(os.path.join(src, "media", "maps"))
    if with_xex:
        open(os.path.join(src, "default.xex"), "wb").write(b"XEX2" + bytes(rnd.getrandbits(8) for _ in range(5000)))
    open(os.path.join(src, "media", "big.bin"), "wb").write(b"lorem ipsum " * 40000)
    open(os.path.join(src, "media", "maps", "m1.dat"), "wb").write(bytes(rnd.getrandbits(8) for _ in range(70000)))
    open(os.path.join(src, "media", "empty.txt"), "wb").close()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run([xiso, "-q", "-c", src, path], check=True, capture_output=True)
    shutil.rmtree(src)
    return path


def _cfg(monkeypatch, **kw):
    monkeypatch.setattr(rommlib, "load_config", lambda: dict(rommlib.DEFAULT_CONFIG, keep_free_gb=0, **kw))


def test_install_from_pinned_archive(isolated, tmp_path, monkeypatch):
    if not os.path.exists(TARBALL) or not rommlib.zar_supported():
        pytest.skip("xbox360-tools not built here")
    monkeypatch.setenv("ROMM_BROWSER_TOOLS", str(tmp_path / "tools"))
    data = open(TARBALL, "rb").read()
    assert hashlib.sha256(data).hexdigest() == rommlib.X360_TOOLS["sha256"]      # the pin matches the build
    assert len(data) == rommlib.X360_TOOLS["size"]
    srv, url = _serve(data)
    try:
        with pytest.raises(rommlib.RommError, match="didn't match its checksum"):
            rommlib.install_tool(rommlib.TOOL_X360, url=url, sha256="0" * 64)
        assert rommlib.x360_tools() is None
        assert rommlib.tool_state(rommlib.TOOL_X360, {"zar": True})["label"] == "Tools missing"
        try:
            got = rommlib.install_tool(rommlib.TOOL_X360, url=url, sha256=rommlib.X360_TOOLS["sha256"])
        except rommlib.RommError as e:
            if "don't run" in str(e):
                pytest.skip("xbox360-tools don't run on this host")
            raise
        assert got == rommlib.x360_tools()
        d = rommlib.tool_dir(rommlib.TOOL_X360)
        assert sorted(os.listdir(os.path.join(d, "LICENSES"))) == [
            "COPYING-zstd.txt", "LICENSE-ZArchive.txt", "LICENSE-extract-xiso.txt", "LICENSE-zstd.txt"]
        assert "fishtank" in open(os.path.join(d, "LICENSES", "LICENSE-extract-xiso.txt")).read()
        assert rommlib.zar_enabled({"zar": True}, "xbox360") and not rommlib.zar_enabled({"zar": True}, "xbox")
        assert rommlib.tool_state(rommlib.TOOL_X360, {"zar": True})["label"] == "On"
    finally:
        srv.shutdown()


def test_worker_converts_and_checks_every_file(tools, monkeypatch):
    import rb_chd
    monkeypatch.setattr(rb_chd, "running_game", lambda: "")
    _cfg(monkeypatch, zar=True)
    sysdir = os.path.join(rommlib.ROMS_ROOT, "xbox360")
    iso = _disc(tools[0], os.path.join(sysdir, "Lorem Racer (USA).iso"))
    want = rommlib.xiso_listing(tools[0], iso)
    assert want["default.xex"] == 5004 and want["media/maps/m1.dat"] == 70000
    assert rb_chd.convert({"system": "xbox360", "mode": "zar", "sources": [iso], "roots": [iso]}) is True
    out = os.path.join(sysdir, "Lorem Racer (USA).zar")
    assert os.path.exists(out) and not os.path.exists(iso) and not os.path.exists(out + ".part")
    assert rommlib.zar_check(out, full=True) is None
    back = os.path.join(rommlib.DATA_DIR, "back")
    subprocess.run([tools[1], out, back], check=True, capture_output=True)
    assert sorted(os.path.relpath(os.path.join(dp, f), back) for dp, _, fs in os.walk(back) for f in fs) == \
        sorted(want)
    assert not os.listdir(os.path.join(rommlib.DATA_DIR, "tmp"))          # scratch cleaned up


def test_refusals_keep_the_image(tools, monkeypatch):
    import rb_chd
    monkeypatch.setattr(rb_chd, "running_game", lambda: "")
    _cfg(monkeypatch, zar=True)
    sysdir = os.path.join(rommlib.ROMS_ROOT, "xbox360")

    def run(p):
        return rb_chd.convert({"system": "xbox360", "mode": "zar", "sources": [p], "roots": [p]})
    junk = os.path.join(sysdir, "Junk.iso")
    os.makedirs(sysdir, exist_ok=True)
    open(junk, "wb").write(b"\0" * 200000)
    assert run(junk) is False and os.path.exists(junk)
    noxex = _disc(tools[0], os.path.join(sysdir, "Not A Game.iso"), with_xex=False)
    assert run(noxex) is False and os.path.exists(noxex)
    cut = _disc(tools[0], os.path.join(sysdir, "Cut Short.iso"))
    with open(cut, "r+b") as f:                      # an interrupted copy: extract-xiso still exits 0
        f.truncate(os.path.getsize(cut) - 60000)
    assert run(cut) is False and os.path.exists(cut)
    assert not any(n.endswith(".zar") for n in os.listdir(sysdir))
    log = open(rommlib.LOG_PATH).read()
    assert "not an Xbox 360 disc image" in log and "default.xex" in log and "cut short or damaged" in log
    _cfg(monkeypatch, zar=False)                     # turned off: nothing happens
    ok = _disc(tools[0], os.path.join(sysdir, "Off.iso"))
    assert run(ok) is False and os.path.exists(ok)


def test_footer_check_finds_damage(tools, tmp_path):
    src = tmp_path / "d"
    os.makedirs(src)
    (src / "default.xex").write_bytes(os.urandom(300000))
    z = str(tmp_path / "x.zar")
    subprocess.run([tools[1], str(src), z], check=True, capture_output=True)
    assert rommlib.zar_check(z) is None and rommlib.zar_check(z, full=True) is None
    raw = bytearray(open(z, "rb").read())
    raw[1000:1008] = b"BROKEN!!"                     # zarchive itself would unpack this with exit 0
    bad = str(tmp_path / "bad.zar")
    open(bad, "wb").write(raw)
    assert rommlib.zar_check(bad) is None and "SHA-256" in rommlib.zar_check(bad, full=True)
    open(bad, "wb").write(raw[:-500])
    assert rommlib.zar_check(bad) is not None
    assert "not a .zar" in rommlib.zar_check(str(src / "default.xex"))


def test_queue_prompt_planner_and_leftovers(tools, monkeypatch):
    import rb_chd_plan
    cfg = dict(rommlib.DEFAULT_CONFIG, zar=True)
    d = rommlib.Downloader(None, rommlib.Library(), cfg, persist=False)
    d.es = {"xbox360": {".iso", ".xex", ".xbox360", ".zar"}}
    iso = _disc(tools[0], os.path.join(rommlib.ROMS_ROOT, "xbox360", "Ipsum (USA).iso"))
    j = rommlib.Job({"id": 3}, "xbox360")
    assert d._postprocess_file(j, iso) == [iso] and j.chd_task
    assert json.load(open(os.path.join(rommlib.CHD_QUEUE_DIR, j.chd_task)))["mode"] == "zar"
    cfg["zar"] = False
    j2 = rommlib.Job({"id": 4}, "xbox360")
    d._postprocess_file(j2, iso)
    assert j2.chd_task is None
    monkeypatch.setattr(rommlib, "load_config", lambda: dict(rommlib.DEFAULT_CONFIG, zar=True))
    assert [os.path.basename(r) for r, _, _ in rb_chd_plan.games_for("xbox360")] == ["Ipsum (USA).iso"]
    det = {"files": [{"file_name": "Ipsum (USA).zip", "file_size_bytes": 1,
                      "archive_members": [{"name": "Ipsum (USA).iso", "size": 7 << 30}]}]}
    assert rommlib.disc_layout(det, "xbox360", {"zar": True})["convert"] == "ZAR"
    assert rommlib.disc_layout(det, "xbox360", {"zar": False})["convert"] is None
    part = os.path.join(rommlib.ROMS_ROOT, "xbox360", "Half.zar.part")
    open(part, "wb").write(b"x")
    assert "Unfinished conversion" in [i["kind"] for i in rommlib.find_leftovers(chd_busy=False)]
    assert not any(i["path"] == part for i in rommlib.find_leftovers(chd_busy=True))
