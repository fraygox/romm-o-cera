"""Downloads + headless UI smoke against tools/mock_romm.py (no real RomM)."""
import json
import os
import subprocess
import sys
import time

import pytest

import rommlib
from conftest import APP


def _wait(jobs, timeout=120):
    t0 = time.time()
    while any(j.active for j in jobs):
        if time.time() - t0 > timeout:
            pytest.fail("downloads did not finish")
        time.sleep(0.1)


def _setup(isolated, mock_url):
    cfg = dict(rommlib.DEFAULT_CONFIG, url=mock_url, token="demo")
    api = rommlib.Api.from_config(cfg)
    d = rommlib.Downloader(api, rommlib.Library(), cfg)
    d.es = {"gba": {".gba", ".zip"}, "dreamcast": {".gdi", ".chd", ".cue"},
            "ps2": {".iso", ".chd"}}
    plats = {p["fs_slug"]: p["id"] for p in api.platforms()}
    return api, d, plats


def _first(api, pid, pred, limit=500):
    return next(r for r in api.roms(platform_ids=[pid], limit=limit)["items"] if pred(r))


def test_downloads_on_a_fresh_box(isolated, mock_url):
    """Fresh data dir (regression: first download used to fail), cart zip kept,
    folder rom fetched file by file, nested single-file rom keeps its extension."""
    api, d, plats = _setup(isolated, mock_url)
    assert not os.path.exists(rommlib.DATA_DIR)
    cart = api.roms(platform_ids=[plats["gba"]], search="anguna")["items"][0]
    folder = _first(api, plats["dreamcast"], lambda r: r["has_multiple_files"])
    nested = _first(api, plats["ps2"], lambda r: r["has_nested_single_file"], limit=2000)
    jobs = [d.enqueue(cart, "gba"), d.enqueue(folder, "dreamcast"), d.enqueue(nested, "ps2")]
    _wait(jobs, 300)
    assert [j.state for j in jobs] == ["Done"] * 3, [j.message for j in jobs]
    roms = rommlib.ROMS_ROOT
    assert os.path.getsize(os.path.join(roms, "gba", cart["fs_name"])) == cart["fs_size_bytes"]
    fdir = os.path.join(roms, "dreamcast", folder["fs_name"])
    assert sorted(os.listdir(fdir)) == sorted(f["file_name"] for f in api.rom(folder["id"])["files"])
    iso = os.path.join(roms, "ps2", nested["fs_name"], nested["fs_name_no_ext"] + ".iso")
    assert os.path.isfile(iso)
    installed = json.load(open(rommlib.STATE_PATH))
    assert {str(cart["id"]), str(folder["id"]), str(nested["id"])} <= set(installed)
    tasks = [json.load(open(os.path.join(rommlib.CHD_QUEUE_DIR, t))) for t in os.listdir(rommlib.CHD_QUEUE_DIR)]
    assert {t["system"] for t in tasks} == {"dreamcast", "ps2"}
    pending = [json.loads(l) for l in open(rommlib.GAMELIST_QUEUE)]
    assert len(pending) == 3 and all(p.get("name") for p in pending)


def test_cancel_then_resume(isolated, mock_url):
    api, d, plats = _setup(isolated, mock_url)
    big = _first(api, plats["dreamcast"], lambda r: not r["has_multiple_files"])
    j = d.enqueue(big, "dreamcast")
    t0 = time.time()
    while j.done_bytes < 20 << 20:
        assert time.time() - t0 < 60 and j.active, j.message
        time.sleep(0.02)
    j.cancel.set()
    _wait([j])
    assert j.state == "Cancelled"
    part = os.path.join(rommlib.ROMS_ROOT, "dreamcast", big["fs_name"] + ".part")
    had = os.path.getsize(part)
    assert 0 < had < big["fs_size_bytes"]
    j2 = d.enqueue(big, "dreamcast")
    _wait([j2], 300)
    # zeros from the mock aren't a zip, so post-processing fails — but the
    # transfer itself resumed and completed at the exact size first
    assert not os.path.exists(part)


STEPS = ("wait:3,act:down,act:ok,wait:2,act:right,act:down,act:ok,wait:2,act:r2,act:r2,act:l2,"
         "act:down,act:ok,wait:0.3,act:back,act:back,act:back,"
         "act:down,act:ok,wait:2,act:right,act:right,act:right,act:right,act:right,act:ok,wait:2,"
         "act:r2,act:ok,wait:2,act:back,act:back,act:back,"
         "act:up,act:up,act:ok,type:magna,wait:2,act:start,wait:2,act:back,act:back,"
         "act:select,wait:0.5,act:back,"
         "act:down,act:down,act:down,act:down,act:down,act:ok,wait:0.5,act:down,act:down,act:down,act:ok,wait:2,"
         "act:back,act:start,act:down,act:down,act:down,act:ok,wait:2,act:x,act:down,act:ok,wait:2,"
         # list menu: picks + download picked, filters, then On this box
         "act:start,act:down,act:ok,wait:2,act:ok,wait:2,act:x,wait:0.3,act:down,act:ok,wait:2,"
         "act:x,act:down,act:down,act:down,act:ok,act:ok,act:down,act:ok,act:x,wait:0.3,"
         "act:down,act:down,act:down,act:down,act:ok,wait:0.3,act:ok,wait:2,"
         "act:start,act:down,act:down,act:down,act:down,act:down,act:ok,wait:2,act:ok,wait:0.3,act:back,"
         # list menu: more like this, junk + verified filters
         "act:start,act:down,act:ok,wait:2,act:ok,wait:2,act:x,wait:0.3,act:ok,wait:2,act:back,"
         "act:x,act:down,act:down,act:down,act:down,act:ok,wait:2,act:x,act:down,act:down,act:down,act:down,act:down,act:ok,wait:2,"
         "shot:end")


def test_ui_smoke(isolated, mock_url, tmp_path):
    """Drive every screen headless (home, platforms + letter jump, download
    confirm, collections + browse-by, search, downloads, settings, recently
    added + sort toggle). Any exception fails the run."""
    pytest.importorskip("pygame")
    env = dict(os.environ, ROMM_URL=mock_url, ROMM_TOKEN="demo",
               ROMM_BROWSER_DATA=str(isolated / "uidata"), ROMM_BROWSER_ROMS=str(isolated / "uiroms"),
               SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
    out = tmp_path / "shots"
    out.mkdir()
    p = subprocess.run([sys.executable, os.path.join(APP, "tools", "shoot.py"), str(out), STEPS],
                       env=env, capture_output=True, text=True, timeout=300)
    assert p.returncode == 0 and "Traceback" not in p.stderr, p.stderr[-2000:]
    assert (out / "end.png").exists()


def test_bios_fetch_from_romm(isolated, mock_url):
    import hashlib
    data = b"MOCK-BIOS-scph5501.bin" * 64
    with open(rommlib.BIOS_DB, "w") as f:
        f.write("systems = %r\n" % {"psx": {"name": "PSX", "biosFiles": [
            {"md5": hashlib.md5(data).hexdigest(), "file": "bios/scph5501.bin"},
            {"md5": "f" * 32, "file": "bios/notinromm.bin"}]}})
    api = rommlib.Api(mock_url, "demo")
    pid = {p["fs_slug"]: p["id"] for p in api.platforms()}["ps1"]
    offers = rommlib.bios_offer(api, "psx", pid)
    assert [m["file"] for m, _ in offers] == ["bios/scph5501.bin"]
    assert rommlib.fetch_bios(api, offers) == ["bios/scph5501.bin"]
    assert open(os.path.join(rommlib.USERDATA, "bios", "scph5501.bin"), "rb").read() == data
    assert [m["file"] for m in rommlib.missing_bios("psx")] == ["bios/notinromm.bin"]
    bad = [({"path": os.path.join(rommlib.USERDATA, "bios", "x.bin"), "md5": "0" * 32,
             "file": "bios/x.bin"}, offers[0][1])]
    with pytest.raises(rommlib.RommError, match="checksum"):
        rommlib.fetch_bios(api, bad)
    assert not os.path.exists(os.path.join(rommlib.USERDATA, "bios", "x.bin"))


def test_queue_survives_restart_and_background_handover(isolated, mock_url):
    """Pause -> saved queue -> a fresh Downloader resumes it; rb_dl.py finishes
    a queue in the background and hands it back when asked."""
    api, d, plats = _setup(isolated, mock_url)
    big = _first(api, plats["ps2"], lambda r: not r["has_nested_single_file"] and r["fs_size_bytes"] > 1 << 30)
    small = api.roms(platform_ids=[plats["gba"]], search="butano")["items"][0]
    jb, js = d.enqueue(big, "ps2"), d.enqueue(small, "gba")
    while jb.done_bytes < 8 << 20:
        time.sleep(0.02)
    d.pause_all()
    _wait([jb, js])
    assert {it["rom"]["id"] for it in d.saved_queue()} == {big["id"], small["id"]}
    # "restart": a new Downloader picks the queue up
    d2 = rommlib.Downloader(api, rommlib.Library(), d.cfg)
    assert d2.resume() == 2
    d2.pause_all()
    _wait(d2.jobs)
    # background worker: run rb_dl.py against the saved queue, then take it back
    env = dict(os.environ, ROMM_URL=mock_url, ROMM_TOKEN="demo",
               ROMM_BROWSER_DATA=rommlib.DATA_DIR, ROMM_BROWSER_ROMS=rommlib.ROMS_ROOT,
               ROMM_BROWSER_ES=("http://127.0.0.1:9"))
    p = subprocess.Popen([sys.executable, os.path.join(APP, "rb_dl.py")], env=env)
    t0 = time.time()
    while not os.path.exists(rommlib.DL_STATUS):
        assert time.time() - t0 < 30 and p.poll() is None
        time.sleep(0.1)
    import rb_dl
    assert rb_dl.handover(timeout=30) is True
    p.wait(30)
    left = {it["rom"]["id"] for it in rommlib.Downloader.saved_queue()}
    assert big["id"] in left                    # still queued for the app to resume
    part = os.path.join(rommlib.ROMS_ROOT, "ps2", big["fs_name"] + ".part")
    assert os.path.getsize(part) > 8 << 20      # and its partial kept growing in the background


def test_similar_and_filters_against_mock(isolated, mock_url):
    api = rommlib.Api(mock_url, "demo")
    pid = {p["fs_slug"]: p["id"] for p in api.platforms()}["snes"]
    first = api.roms(platform_ids=[pid], limit=1)["items"][0]
    sim = api.similar(first["id"])
    assert sim and all(r["platform_id"] == pid and r["id"] != first["id"] for r in sim)
    total = api.roms(platform_ids=[pid], limit=1)["total"]
    junk = rommlib.junk_tags(api.all_tags())
    assert "Homebrew" not in junk and junk
    clean = api.roms(platform_ids=[pid], limit=500, exclude_tags=junk)
    assert clean["total"] < total and not any(set(r["tags"]) & set(junk) for r in clean["items"])
    ver = api.roms(platform_ids=[pid], limit=1, verified=True)["total"]
    assert 0 < ver < total
