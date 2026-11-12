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
    monkeypatch.setattr(rommlib, "es_busy", lambda app_busy=False, get=None: "")   # nothing busy in ES
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
    monkeypatch.setattr(rommlib, "es_busy", lambda app_busy=False, get=None: "")   # nothing busy in ES
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


# ---------------------------------------------------------------- package discs, with the real tools

TITLE = "content/0000000000000000/4D2A0001/000D0000/AB12CD34"
ADDON = "content/0000000000000000/4D2A0001/00000002/EF56AB78"
UPDATE = "$TitleUpdate/4D2A0001/tu00000001_00000000"


def _package_disc(xiso, path):
    """A disc that only starts a package: a stub, the title's package, an add-on, an update."""
    from stfs_build import build_package
    src = path + ".src"
    for rel, data in (("default.xex", b"XEX2stub"), ("nxeart", b"art"), ("$SystemUpdate/su", b"s" * 64),
                      (TITLE, build_package({"default.xex": b"XEX2" + os.urandom(9000)}, 0x000D0000, 0x4D2A0001)),
                      (ADDON, build_package({"spa.bin": b"spa", "08.png": os.urandom(6000)}, 0x00000002, 0x4D2A0001)),
                      (UPDATE, build_package({"default.xexp": os.urandom(3000)}, 0x000B0000, 0x4D2A0001, magic=b"LIVE"))):
        p = os.path.join(src, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "wb").write(data)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run([xiso, "-q", "-c", src, path], check=True, capture_output=True)
    shutil.rmtree(src)
    return path


def _tree(root):
    return sorted(os.path.relpath(os.path.join(dp, f), root) for dp, _, fs in os.walk(root) for f in fs)


def test_a_zars_file_list_is_read_without_unpacking_it(tools, tmp_path):
    iso = _disc(tools[0], str(tmp_path / "d.iso"))
    work = str(tmp_path / "files")
    subprocess.run([tools[0], "-q", "-d", work, iso], check=True, capture_output=True)
    deep = os.path.join(work, "a" * 150, "b")                       # a name long enough for a two-byte length
    os.makedirs(deep)
    open(os.path.join(deep, "f.bin"), "wb").write(b"z" * 123)
    z = str(tmp_path / "x.zar")
    subprocess.run([tools[1], work, z], check=True, capture_output=True)
    want = {r: os.path.getsize(os.path.join(work, r)) for r in _tree(work)}
    assert rommlib.zar_listing(z) == want and len(want) == 5
    assert rommlib.zar_listing(iso) is None and rommlib.zar_listing(str(tmp_path / "none")) is None
    raw = open(z, "rb").read()
    # a file tree that can't be one is refused at once, never walked: a folder naming billions of
    # entries, and one that lists itself
    tree_at = int.from_bytes(raw[-144 + 48:-144 + 56], "big")
    for first, n in ((1, 0xFFFFFFF0), (0, 1)):
        bad = bytearray(raw)
        bad[tree_at + 4:tree_at + 12] = first.to_bytes(4, "big") + n.to_bytes(4, "big")
        open(z, "wb").write(bad)
        assert rommlib.zar_listing(z) is None
    open(z, "wb").write(raw[:-200])                                 # cut short: no footer where one must be
    assert rommlib.zar_listing(z) is None


def test_a_package_disc_is_set_up_whatever_the_zar_switch_says(tools, monkeypatch):
    """Xbox 360 to .zar off (the default): an ordinary disc stays as it is, a package disc
    is queued for its launcher; on: the ordinary one becomes a .zar, the package disc still a launcher."""
    import rb_chd
    monkeypatch.setattr(rb_chd, "running_game", lambda: "")
    monkeypatch.setattr(rommlib, "es_busy", lambda app_busy=False, get=None: "")
    sysdir = os.path.join(rommlib.ROMS_ROOT, "xbox360")
    plain = _disc(tools[0], os.path.join(sysdir, "Ipsum (USA).iso"))
    pkg = _package_disc(tools[0], os.path.join(sysdir, "Lorem Tales (World).iso"))
    for zar, modes in ((False, (None, "xpkg")), (True, ("zar", "xpkg"))):
        cfg = dict(rommlib.DEFAULT_CONFIG, zar=zar, keep_free_gb=0)
        got = tuple(getattr(rommlib.conversion_for("xbox360", p, cfg), "mode", None) for p in (plain, pkg))
        assert got == modes
        # a game still to download: nothing says which kind it is, so only the switch counts
        assert getattr(rommlib.conversion_for("xbox360", ".iso", cfg), "mode", None) == ("zar" if zar else None)
    assert rommlib.conversion_for("xbox360", pkg, dict(rommlib.DEFAULT_CONFIG, x360_packages=False)) is None
    # the download's hand-off queues it, and the worker makes the launcher
    cfg = dict(rommlib.DEFAULT_CONFIG, keep_free_gb=0)
    monkeypatch.setattr(rommlib, "load_config", lambda: dict(cfg))
    d = rommlib.Downloader(None, rommlib.Library(), cfg, persist=False)
    d.es = {"xbox360": {".iso", ".xex", ".xbox360", ".zar"}}
    j, j2 = rommlib.Job({"id": 3}, "xbox360"), rommlib.Job({"id": 4}, "xbox360")
    d._postprocess_file(j2, plain)
    assert j2.chd_task is None
    d._postprocess_file(j, pkg)
    task = json.load(open(os.path.join(rommlib.CHD_QUEUE_DIR, j.chd_task)))
    assert task["mode"] == j.chd_mode == "xpkg" and rommlib.needed_to_play(j.chd_mode)
    assert rommlib.conversion_of([pkg]) == ("Waiting to be set up for xenia", False)
    assert rb_chd.convert(task) is True
    folder = os.path.join(sysdir, "Lorem Tales (World).packages")
    assert sorted(os.listdir(sysdir)) == ["Ipsum (USA).iso", "Lorem Tales (World).packages",
                                          "Lorem Tales (World).xbox360"]
    assert open(os.path.join(sysdir, "Lorem Tales (World).xbox360")).read() == \
        "Lorem Tales (World).packages/%s\n" % TITLE
    assert _tree(folder) == sorted([TITLE, ADDON + "/08.png", ADDON + "/spa.bin", UPDATE + "/default.xexp"])
    content = os.path.join(rommlib.SAVES_ROOT, "xbox360", "0000000000000000", "4D2A0001")
    assert os.path.isfile(os.path.join(content, "00000002", "EF56AB78", "spa.bin"))
    assert os.path.isfile(os.path.join(content, "000B0000", "tu00000001_00000000", "default.xexp"))
    assert not os.listdir(os.path.join(rommlib.DATA_DIR, "tmp"))


def test_compress_games_already_here_sets_up_package_discs_and_old_zars_of_them(tools, monkeypatch):
    """A package disc already on the box, as an image or as the .zar an earlier version
    made of it (neither starts): offered as a launcher, switch or no switch; an ordinary
    .zar is left alone and nothing is said of it."""
    import rb_chd
    import rb_chd_plan
    monkeypatch.setattr(rb_chd, "running_game", lambda: "")
    monkeypatch.setattr(rommlib, "es_busy", lambda app_busy=False, get=None: "")
    cfg = dict(rommlib.DEFAULT_CONFIG, keep_free_gb=0)
    monkeypatch.setattr(rommlib, "load_config", lambda: dict(cfg))
    sysdir = os.path.join(rommlib.ROMS_ROOT, "xbox360")

    def zar_of(iso):
        work = iso + ".files"
        subprocess.run([tools[0], "-q", "-d", work, iso], check=True, capture_output=True)
        subprocess.run([tools[1], work, iso[:-4] + ".zar"], check=True, capture_output=True)
        shutil.rmtree(work)
        os.remove(iso)
        return iso[:-4] + ".zar"
    plain_zar = zar_of(_disc(tools[0], os.path.join(sysdir, "Ipsum (USA).iso")))
    old = zar_of(_package_disc(tools[0], os.path.join(sysdir, "Lorem Tales (World).iso")))
    img = _package_disc(tools[0], os.path.join(sysdir, "Dolor Tales (World).iso"))
    plain = _disc(tools[0], os.path.join(sysdir, "Sit (USA).iso"), seed=9)
    skipped = []
    cands = rb_chd_plan.candidates(["xbox360"], cfg, skipped)
    got = {os.path.basename(c["files"][0]): (c["mode"], c["label"], c["enabled"]) for c in cands}
    assert got == {"Dolor Tales (World).iso": ("xpkg", "xenia launcher", True),
                   "Lorem Tales (World).zar": ("xpkg", "xenia launcher", True),
                   "Sit (USA).iso": ("zar", "ZAR", False)}                  # needs its switch, as before
    assert skipped == []
    assert rb_chd_plan.queue(cands) == 2
    for name in sorted(os.listdir(rommlib.CHD_QUEUE_DIR)):
        if name.endswith(".json"):
            assert rb_chd.convert(json.load(open(os.path.join(rommlib.CHD_QUEUE_DIR, name)))) is True
            os.remove(os.path.join(rommlib.CHD_QUEUE_DIR, name))
    assert sorted(os.listdir(sysdir)) == [
        "Dolor Tales (World).packages", "Dolor Tales (World).xbox360", "Ipsum (USA).zar",
        "Lorem Tales (World).packages", "Lorem Tales (World).xbox360", "Sit (USA).iso"]
    assert _tree(os.path.join(sysdir, "Lorem Tales (World).packages")) == \
        sorted([TITLE, ADDON + "/08.png", ADDON + "/spa.bin", UPDATE + "/default.xexp"])
    assert os.path.exists(plain_zar) and os.path.exists(plain) and not os.path.exists(old) and not os.path.exists(img)
    assert rb_chd_plan.candidates(["xbox360"], cfg) and all(
        c["mode"] == "zar" for c in rb_chd_plan.candidates(["xbox360"], cfg))    # done ones aren't offered again
