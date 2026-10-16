"""dolphin-tool download/install, GameCube/Wii -> RVZ conversion (with a fake
dolphin-tool), download-while-extracting, and the control-glyph helper."""
import hashlib
import io
import json
import os
import stat
import sys
import tarfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import rommlib
from test_multidisc import _rom, _setup, _wait

FAKE_TOOL = r'''#!%s
"""Fake dolphin-tool: 'verify' prints a CRC32 from the file's first bytes
(junk -> rc 1), 'convert' copies (or corrupts if the source holds BADCOPY)."""
import sys, zlib
a = sys.argv[1:]
if a[0] in ("--help", "-h"):
    print("commands supported: [convert, verify, header, extract]"); sys.exit(0)
def opt(k):
    return a[a.index(k) + 1]
data = open(opt("-i"), "rb").read()
if a[0] == "verify":
    if not (data.startswith(b"GCDISC") or data.startswith(b"RVZ:GCDISC")):
        print("Error: Unable to open input file"); sys.exit(1)
    body = data[4:] if data.startswith(b"RVZ:") else data
    crc = "%%08x" %% (zlib.crc32(body) & 0xffffffff)
    if "-a" in a:
        print(crc)
    else:
        print("CRC32: " + crc); print("Problems Found: No")
    sys.exit(0)
if a[0] == "convert":
    assert "-u" in a, "always pass a private user folder"
    out = b"RVZ:" + (data.replace(b"BADCOPY", b"broken!") if b"BADCOPY" in data else data)
    open(opt("-o"), "wb").write(out); sys.exit(0)
sys.exit(2)
'''


def _install_fake_tool(monkeypatch, tmp_path):
    d = tmp_path / "tools" / ("dolphin-tool-" + rommlib.DOLPHIN_TOOL["version"])
    d.mkdir(parents=True)
    exe = d / "dolphin-tool"
    exe.write_text(FAKE_TOOL % sys.executable)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("ROMM_BROWSER_TOOLS", str(tmp_path / "tools"))
    return str(exe)


# ------------------------------------------------------------ install

def _serve(payload):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, args=(0.05,), daemon=True).start()   # quick shutdown()
    return srv, "http://127.0.0.1:%d/dt.tar.gz" % srv.server_address[1]


def _tarball(members):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, data, mode in members:
            ti = tarfile.TarInfo(name)
            ti.size, ti.mode = len(data), mode
            t.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def test_install_dolphin_tool_checks_and_unpacks(isolated, tmp_path, monkeypatch):
    monkeypatch.setenv("ROMM_BROWSER_TOOLS", str(tmp_path / "tools"))
    tool = (FAKE_TOOL % sys.executable).encode()
    good = _tarball([("./dolphin-tool", tool, 0o755), ("./COPYING", b"GPL", 0o644),
                     ("./SOURCE.txt", b"source", 0o644)])
    srv, url = _serve(good)
    try:
        with pytest.raises(rommlib.RommError, match="didn't match its checksum"):
            rommlib.install_tool(rommlib.TOOL_DOLPHIN, url=url, sha256="0" * 64)
        assert rommlib.dolphin_tool_path() is None
        seen = []
        p = rommlib.install_tool(rommlib.TOOL_DOLPHIN, lambda g, t: seen.append(g), url=url,
                                 sha256=hashlib.sha256(good).hexdigest())
        assert p == rommlib.dolphin_tool_path() and os.access(p, os.X_OK) and seen[-1] == len(good)
        assert sorted(os.listdir(os.path.dirname(p))) == ["COPYING", "SOURCE.txt", "dolphin-tool"]
        assert rommlib.rvz_enabled({"rvz": True}) and not rommlib.rvz_enabled({"rvz": False})
    finally:
        srv.shutdown()
    evil = _tarball([("../escape", b"x", 0o644), ("./dolphin-tool", tool, 0o755)])
    srv, url = _serve(evil)
    try:
        with pytest.raises(rommlib.RommError, match="Unexpected file"):
            rommlib.install_tool(rommlib.TOOL_DOLPHIN, url=url, sha256=hashlib.sha256(evil).hexdigest())
        assert not (tmp_path / "escape").exists()
    finally:
        srv.shutdown()


def test_install_x360_tools_runs_each_program_from_the_archives_top_folder(isolated, tmp_path, monkeypatch):
    """The same installer as dolphin-tool's, from the Xbox 360 tools' ToolSpec:
    two programs, inside a top folder named after the release."""
    monkeypatch.setenv("ROMM_BROWSER_TOOLS", str(tmp_path / "tools"))
    monkeypatch.setattr(rommlib, "zar_supported", lambda: True)
    top = rommlib.TOOL_X360.folder + "/"
    script = "#!/bin/sh\necho '%s usage'\n"
    one = _tarball([(top + "extract-xiso", (script % "extract-xiso").encode(), 0o755)])
    both = _tarball([(top + "extract-xiso", (script % "extract-xiso").encode(), 0o755),
                     (top + "zarchive", (script % "ZArchive").encode(), 0o755), (top + "LICENSE", b"MIT-0", 0o644)])
    srv, url = _serve(one)
    try:
        with pytest.raises(rommlib.RommError, match="has no zarchive in it"):
            rommlib.install_tool(rommlib.TOOL_X360, url=url, sha256=hashlib.sha256(one).hexdigest())
    finally:
        srv.shutdown()
    srv, url = _serve(both)
    try:
        got = rommlib.install_tool(rommlib.TOOL_X360, url=url, sha256=hashlib.sha256(both).hexdigest())
    finally:
        srv.shutdown()
    d = rommlib.tool_dir(rommlib.TOOL_X360)
    assert got == rommlib.x360_tools() == (os.path.join(d, "extract-xiso"), os.path.join(d, "zarchive"))
    assert sorted(os.listdir(d)) == ["LICENSE", "extract-xiso", "zarchive"]
    assert rommlib.tool_state(rommlib.TOOL_X360, {"zar": True})["label"] == "On"
    assert "xbox360-tools %s installed" % rommlib.X360_TOOLS["version"] in open(rommlib.LOG_PATH).read()


def test_converters_name_the_tools_they_need():
    assert {c.mode: c.tool for c in rommlib.CONVERTERS if c.tool} == \
        {"rvz": rommlib.TOOL_DOLPHIN, "zar": rommlib.TOOL_X360, "wua": rommlib.TOOL_WIIU, "wux": rommlib.TOOL_WIIU}
    for c in rommlib.CONVERTERS:      # a tool is named after the first switch that needs it; Wii U's two share it
        assert c.tool is None or c.tool.setting == c.setting or (c.mode, c.tool.setting) == ("wux", "wua")


def test_rvz_state_labels(isolated, tmp_path, monkeypatch):
    monkeypatch.setenv("ROMM_BROWSER_TOOLS", str(tmp_path / "tools"))
    assert rommlib.tool_state(rommlib.TOOL_DOLPHIN, {})["label"] == "Off"
    assert rommlib.tool_state(rommlib.TOOL_DOLPHIN, {"rvz": True})["label"] == "Tool missing"
    _install_fake_tool(monkeypatch, tmp_path)
    assert rommlib.tool_state(rommlib.TOOL_DOLPHIN, {"rvz": True})["label"] == "On"
    monkeypatch.setattr(rommlib, "rvz_supported", lambda: False)
    assert rommlib.tool_state(rommlib.TOOL_DOLPHIN, {"rvz": True})["label"] == "Not on this CPU"


# ------------------------------------------------------------ conversion

def _gc(path, body=b"GCDISC" + b"\x00" * 4000):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").write(body)
    return path


def test_rvz_conversion_verified_junk_and_mismatch_refused(isolated, tmp_path, monkeypatch):
    import rb_chd
    _install_fake_tool(monkeypatch, tmp_path)
    monkeypatch.setattr(rb_chd, "running_game", lambda: "")
    monkeypatch.setattr(rommlib, "es_busy", lambda app_busy=False, get=None: "")   # nothing busy in ES
    monkeypatch.setattr(rommlib, "load_config", lambda: dict(rommlib.DEFAULT_CONFIG, rvz=True, keep_free_gb=0))
    sysdir = os.path.join(rommlib.ROMS_ROOT, "gamecube")
    good = _gc(os.path.join(sysdir, "Commodo Cab (USA).iso"))
    assert rb_chd.convert({"system": "gamecube", "mode": "rvz", "sources": [good], "roots": [good]}) is True
    out = os.path.join(sysdir, "Commodo Cab (USA).rvz")
    assert os.path.exists(out) and not os.path.exists(good) and not os.path.exists(out + ".part")
    assert os.path.isdir(os.path.join(rommlib.DATA_DIR, "tmp", "dolphin-user"))     # never Batocera's own
    junk = _gc(os.path.join(sysdir, "Not A Disc.iso"), b"PS2 stuff" * 100)
    assert rb_chd.convert({"system": "gamecube", "mode": "rvz", "sources": [junk], "roots": [junk]}) is False
    assert os.path.exists(junk) and not os.path.exists(os.path.join(sysdir, "Not A Disc.rvz"))
    bad = _gc(os.path.join(sysdir, "Bad Copy.iso"), b"GCDISC BADCOPY" + b"\x00" * 100)
    assert rb_chd.convert({"system": "gamecube", "mode": "rvz", "sources": [bad], "roots": [bad]}) is False
    assert os.path.exists(bad) and not os.path.exists(os.path.join(sysdir, "Bad Copy.rvz"))
    log = open(rommlib.LOG_PATH).read()
    assert "not a GameCube/Wii disc image" in log and "doesn't match the original" in log


def test_rvz_skipped_when_off_and_planner(isolated, tmp_path, monkeypatch):
    import rb_chd
    import rb_chd_plan
    _install_fake_tool(monkeypatch, tmp_path)
    monkeypatch.setattr(rb_chd, "running_game", lambda: "")
    monkeypatch.setattr(rommlib, "es_busy", lambda app_busy=False, get=None: "")   # nothing busy in ES
    monkeypatch.setattr(rommlib, "load_config", lambda: dict(rommlib.DEFAULT_CONFIG, rvz=False))
    src = _gc(os.path.join(rommlib.ROMS_ROOT, "wii", "Game (USA).wbfs"))
    assert rb_chd.convert({"system": "wii", "mode": "rvz", "sources": [src], "roots": [src]}) is False
    assert os.path.exists(src)
    _gc(os.path.join(rommlib.ROMS_ROOT, "wii", "Already.rvz"))
    plan = rb_chd_plan.games_for("wii")
    assert [os.path.basename(r) for r, _, _ in plan] == ["Game (USA).wbfs"]


def test_downloader_queues_rvz_and_bare_iso_chd(isolated, tmp_path, monkeypatch):
    _install_fake_tool(monkeypatch, tmp_path)
    cfg = dict(rommlib.DEFAULT_CONFIG, rvz=True)
    d = rommlib.Downloader(None, rommlib.Library(), cfg, persist=False)
    d.es = {"gamecube": {".iso", ".rvz"}, "ps2": {".iso", ".chd"}}
    gc = _gc(os.path.join(rommlib.ROMS_ROOT, "gamecube", "G.iso"))
    j = rommlib.Job({"id": 7}, "gamecube")
    assert d._postprocess_file(j, gc) == [gc] and j.chd_task
    t = json.load(open(os.path.join(rommlib.CHD_QUEUE_DIR, j.chd_task)))
    assert t["mode"] == "rvz" and t["sources"] == [gc]
    iso = _gc(os.path.join(rommlib.ROMS_ROOT, "ps2", "P.iso"))        # a bare .iso download (no zip)
    j2 = rommlib.Job({"id": 8}, "ps2")
    d._postprocess_file(j2, iso)
    assert json.load(open(os.path.join(rommlib.CHD_QUEUE_DIR, j2.chd_task)))["mode"] == "createdvd"
    cfg["rvz"] = False
    j3 = rommlib.Job({"id": 9}, "gamecube")
    d._postprocess_file(j3, gc)
    assert j3.chd_task is None


# ------------------------------------------------------------ download while extracting

def test_next_download_starts_while_previous_unpacks(isolated, mock_url, monkeypatch):
    api, d = _setup(mock_url)
    gate, started = threading.Event(), []
    real = d._postprocess_file

    def slow(j, path):
        started.append((j.rom["id"], time.time()))
        if len(started) == 1:
            gate.wait(20)                  # the first game's unpacking hangs until released
        return real(j, path)
    monkeypatch.setattr(d, "_postprocess_file", slow)
    monkeypatch.setattr(d, "_will_extract", lambda j, p: True)
    a = d.enqueue(_rom(api, "Ipsum Saga (Japan) (Disc 1)"), "psx")
    b = d.enqueue(_rom(api, "Flaky Cart (USA)"), "gba")
    rommlib.RETRY_DELAYS = (0.1, 0.1, 0.1)
    try:
        t0 = time.time()
        while b.state not in (rommlib.Job.EXTRACTING, rommlib.Job.DONE) and time.time() - t0 < 30:
            time.sleep(0.05)
        assert a.state == rommlib.Job.EXTRACTING     # still unpacking...
        assert b.state in (rommlib.Job.EXTRACTING, rommlib.Job.DONE) and b.done_bytes  # ...b downloaded meanwhile
        gate.set()
        _wait([a, b])
    finally:
        rommlib.RETRY_DELAYS = (15, 60, 180)
        gate.set()
    assert a.state == b.state == rommlib.Job.DONE, (a.message, b.message)


def test_overlap_off_is_sequential(isolated, mock_url, monkeypatch):
    api, d = _setup(mock_url)
    d.cfg["extract_parallel"] = False
    order = []
    real_fetch, real_post = d._fetch, d._post

    def fetch(j, *a, **k):
        order.append(("fetch", j.rom["id"]))
        return real_fetch(j, *a, **k)

    def post(j, p):
        order.append(("post", j.rom["id"]))
        return real_post(j, p)
    monkeypatch.setattr(d, "_fetch", fetch)
    monkeypatch.setattr(d, "_post", post)
    a = d.enqueue(_rom(api, "Ipsum Saga (Japan) (Disc 1)"), "psx")
    b = d.enqueue(_rom(api, "Lorem Odyssey (USA) (Disc 1)"), "psx")
    _wait([a, b])
    firsts = [k for k, rid in order if rid == a.rom["id"]]
    assert order.index(("post", a.rom["id"])) < next(i for i, (k, rid) in enumerate(order) if rid == b.rom["id"])
    assert firsts[-1] == "post"


# ------------------------------------------------------------ control glyphs

def test_glyph_helper_tokens_and_wrapping(rb, monkeypatch):
    import pygame
    pygame.init()
    fonts = rb.Fonts()
    assert set(rb.TOKEN_ACTION) <= set(rb.TOKENS)
    for st in ("xbox", "playstation", "nintendo"):     # setitem: the style is put back afterwards
        monkeypatch.setitem(rb.PAD_STYLE, "style", st)
        for kb in (False, True):
            monkeypatch.setitem(rb.INPUT_MODE, "kb", kb)
            for t in rb.TOKENS:
                assert rb.glyph_width(fonts, t) > 0 and rb.pad_text(t)
    monkeypatch.setitem(rb.INPUT_MODE, "kb", False)
    lines = rb.rich_lines(fonts, fonts.body, "Press {A} to delete or {B} to keep them, then {LEFT RIGHT} to choose.", 200)
    assert len(lines) > 1 and all("{" not in l or rb.GLYPH_RE.search(l) for l in lines)
    assert all(rb.rich_width(fonts, fonts.body, l) <= 200 for l in lines)
    assert "".join(lines).count("{") == 3                      # a glyph never splits
    surf = pygame.Surface((1280 * rb.K, 720 * rb.K))
    rb.FONTS["f"] = fonts
    r = rb.text(surf, fonts.body, "then {X} to delete", (10, 10))   # any text() call draws glyphs
    assert r.width < rb.measure(fonts.body, "then {X} to delete") + 30


@pytest.mark.parametrize("style", ["xbox", "playstation", "nintendo", "kb"])
def test_screenshot_harness_every_screen_every_style(tmp_path, rb, style):
    """tools/shots.py renders every screen and dialog in every control style and
    fails on a draw error or a hint that bypasses the glyph helper. One style per
    test, so parallel workers share the work (it's the suite's longest)."""
    import re
    import subprocess
    pytest.importorskip("PIL")
    from conftest import APP
    r = subprocess.run([sys.executable, os.path.join(APP, "tools", "shots.py"), str(tmp_path), "--styles", style],
                       capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    scenes = int(re.search(r"\((\d+) scenes x 1 styles\)", r.stdout).group(1))
    assert scenes >= 26 and len(os.listdir(tmp_path / style)) == scenes
    assert (tmp_path / "index.html").exists()


def test_only_version_tags_count_as_updates(isolated, monkeypatch):
    assert rommlib._vtuple("v0.6.5") == (0, 6, 5) and rommlib._vtuple("0.6.5+e88d7e7") == (0, 6, 5)
    assert rommlib._vtuple("v1.2") == (1, 2, 0)
    for tag in ("dolphin-tool-2603a", "2603a", "", None, "latest"):
        assert rommlib._vtuple(tag) == (0,)

    class R:
        status_code = 200

        @staticmethod
        def json():
            return {"tag_name": "dolphin-tool-2603a"}
    monkeypatch.setattr(rommlib, "plain_get", lambda *a, **k: R())
    assert rommlib.newer_release({"check_updates": True}, force=True) is None


# ------------------------------------------------------------ repair by hash / closest title

class _FakeApi:
    def __init__(self, roms, by_ra=None):
        self._roms, self._by_ra = roms, by_ra or {}

    def by_hash(self, **kw):
        return self._by_ra.get(kw.get("ra_hash"))

    def roms(self, platform_ids=None, search=None, limit=100, **kw):
        return {"items": [r for r in self._roms if (search or "").lower() in r["fs_name_no_ext"].lower()]}


def test_find_in_romm_by_ra_hash_then_name(isolated):
    def r(i, n):
        return {"id": i, "platform_id": 5, "fs_name_no_ext": n, "fs_name": n + ".zip", "name": n}
    roms = [r(1, "Aliqua (USA) (Rev 1)"), r(2, "Aliqua (Japan)"), r(3, "Aliqua 2 - A New Legend Begins (USA)"),
            r(4, "Fugiat Fantasy IX (USA, Canada) (Disc 1)"), r(5, "Fugiat Fantasy IX (USA, Canada) (Disc 3)"),
            r(6, "Sit Filter (USA) (Rev 1)"), r(7, "Sit Filter (USA) (Demo)"),
            r(8, "Tempor Raider (USA) (Rev 1)"), r(9, "Tempor Raider (USA) (Rev 6)")]
    H = "abc123" + "0" * 26
    api = _FakeApi(roms, {H: r(42, "Something Else Entirely (USA)")})
    p = "/userdata/roms/psx/%s.chd"
    assert rommlib.find_in_romm(api, "psx", [5], p % "Whatever", H.upper())[0]["id"] == 42     # RA hash wins
    assert rommlib.find_in_romm(api, "psx", [5], p % "Aliqua (USA)")[0]["id"] == 1
    assert rommlib.find_in_romm(api, "psx", [5], p % "Fugiat Fantasy IX (USA) (Disc 3)")[0]["id"] == 5
    assert rommlib.find_in_romm(api, "psx", [5], p % "Sit Filter (USA)")[0]["id"] == 6      # never the demo
    assert rommlib.find_in_romm(api, "psx", [5], p % "Tempor Raider (USA)")[0]["id"] == 9        # latest revision
    assert rommlib.find_in_romm(api, "psx", [5], p % "Aliqua (Europe)") == (None, None)       # region must match
    assert rommlib.find_in_romm(api, "psx", [9], p % "Whatever", H) == (None, None)            # other platform
    # games without RetroAchievements: every way the hash can be missing falls back to the title
    for h in (None, "", "0" * 32, "not-a-hash"):
        assert rommlib.find_in_romm(api, "psx", [5], p % "Aliqua (USA)", h)[0]["id"] == 1

    class NoRA(_FakeApi):
        def by_hash(self, **kw):          # RomM without RA data for it: 404 -> None
            return None
    assert rommlib.find_in_romm(NoRA(roms), "psx", [5], p % "Aliqua (USA)", H)[0]["id"] == 1

    class Broken(_FakeApi):
        def by_hash(self, **kw):          # older RomM / server error: never blocks the title match
            raise rommlib.RommError("RomM error HTTP 500 on /api/roms/by-hash")
    assert rommlib.find_in_romm(Broken(roms), "psx", [5], p % "Aliqua (USA)", H)[0]["id"] == 1


def test_replace_old_copy_after_download(isolated, mock_url, monkeypatch, tmp_path):
    """A broken copy under another name: RomM's copy comes in, saves are copied
    to its name, the playlist follows, the broken file is deleted."""
    api, d = _setup(mock_url)
    rom = _rom(api, "Ipsum Saga (Japan) (Disc 1)")
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    os.makedirs(sysdir, exist_ok=True)
    old = os.path.join(sysdir, "Ipsum Saga (J) (Disc 1).chd")
    open(old, "w").write("truncated")
    m3u = os.path.join(sysdir, "Ipsum Saga.m3u")
    open(m3u, "w").write("Ipsum Saga (J) (Disc 1).chd\n")
    saves = tmp_path / "saves"
    (saves / "psx").mkdir(parents=True)
    (saves / "psx" / "Ipsum Saga (J) (Disc 1).srm").write_text("card")
    monkeypatch.setattr(rommlib, "SAVES_ROOT", str(saves))
    j = d.enqueue(rom, "psx", replace=[old])
    _wait([j])
    assert j.state == rommlib.Job.DONE, j.message
    assert not os.path.exists(old)
    new_stem = rom["fs_name_no_ext"]
    assert (saves / "psx" / (new_stem + ".srm")).read_text() == "card"
    line = open(m3u).read().strip()
    assert new_stem in line and "(J)" not in line
    assert "REPLACED" in open(rommlib.LOG_PATH).read()


def test_by_hash_404_means_none(isolated, monkeypatch):
    api = rommlib.Api("http://romm.invalid", "t")
    monkeypatch.setattr(api, "get", lambda *a, **k: (_ for _ in ()).throw(
        rommlib.RommError("RomM error HTTP 404 on /api/roms/by-hash")))
    assert api.by_hash(ra_hash="ab" * 16) is None
    assert api.by_hash() is None                                     # nothing to ask with
    monkeypatch.setattr(api, "get", lambda *a, **k: [])
    assert api.by_hash(ra_hash="ab" * 16) is None


def test_dolphin_tool_per_cpu(monkeypatch):
    """x86_64 and 64-bit ARM have their own pinned archive; anything else isn't offered."""
    assert set(rommlib.DOLPHIN_TOOLS) == {"x86_64", "aarch64"}
    for arch, t in rommlib.DOLPHIN_TOOLS.items():
        assert t["url"].endswith("dolphin-tool-2603a-%s.tar.gz" % arch) and len(t["sha256"]) == 64 and t["size"] > 0
    assert rommlib.DOLPHIN_TOOLS["x86_64"]["sha256"] != rommlib.DOLPHIN_TOOLS["aarch64"]["sha256"]
    import platform
    for m, ok in (("x86_64", True), ("AMD64", True), ("aarch64", True), ("arm64", True), ("armv7l", False),
                  ("riscv64", False)):
        monkeypatch.setattr(platform, "machine", lambda m=m: m)
        assert rommlib.rvz_supported() is ok, m
    assert rommlib.tool_state(rommlib.TOOL_DOLPHIN, {})["label"] == "Not on this CPU"     # riscv64 left patched


def test_32bit_userland_on_64bit_kernel_not_offered(monkeypatch):
    """A 64-bit ARM kernel running 32-bit programs reports aarch64 in uname:
    the 64-bit tool wouldn't run, so it's not offered (no pointless download)."""
    import platform
    import struct
    monkeypatch.setattr(platform, "machine", lambda: "aarch64")
    real = struct.calcsize
    monkeypatch.setattr(struct, "calcsize", lambda f: 4 if f == "P" else real(f))
    assert rommlib._machine() == "armv7l" and not rommlib.rvz_supported()
    monkeypatch.setattr(struct, "calcsize", real)
    assert rommlib._machine() == "aarch64" and rommlib.rvz_supported()
