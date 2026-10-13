"""The conversion worker and a converter's tool that isn't here (yet): waiting
tasks run as soon as it is, a tool gone mid-task never drops the task, a
Settings download starts the worker, no key found by wuatool still makes a
.wux, and a .wux of a cut-short image is replaced by a fresh download. Fake
tools (installed or not, at will) and fake runners: no real disc needed."""
import os
import struct

import pytest

import rommlib


@pytest.fixture
def box(isolated, monkeypatch):
    """A Wii U .wud (just its header) queued for .wux; wuatool "installed" when
    have[0] is True; the runner writes a small .wux-looking file."""
    from romm import tools as T
    import rb_chd
    have = [False]
    monkeypatch.setattr(T, "wuatool_path", lambda: "/fake/wuatool" if have[0] else None)
    monkeypatch.setattr(T, "wua_supported", lambda: True)
    monkeypatch.setattr(rb_chd, "running_game", lambda: "")
    monkeypatch.setattr(rommlib, "es_busy", lambda app_busy=False, get=None: "")
    monkeypatch.setattr(rommlib, "load_config", lambda: dict(rommlib.DEFAULT_CONFIG, keep_free_gb=0))
    ran = []

    def fake_wux(src, tmp):
        ran.append(("wux", src))
        with open(tmp, "wb") as f:
            f.write(b"WUX0" + b"\0" * 12 + os.path.getsize(src).to_bytes(8, "little"))
        return 0, "ok"
    monkeypatch.setitem(rb_chd.RUNNERS, "wux", lambda src, tmp, threads: fake_wux(src, tmp))
    disc = os.path.join(rommlib.ROMS_ROOT, "wiiu", "Lorem.wud")
    os.makedirs(os.path.dirname(disc))
    data = bytearray(0x20000)
    struct.pack_into(">I", data, 0x10000, 0xCC549EB9)
    open(disc, "wb").write(bytes(data))
    task = {"system": "wiiu", "mode": "wux", "sources": [disc], "roots": [disc]}
    rommlib.publish_queue_task(task, "1-wiiu.json")
    return {"rb_chd": rb_chd, "have": have, "ran": ran, "disc": disc, "task": task}


def test_waiting_tasks_run_once_the_tool_arrives_in_the_same_worker(box):
    rb_chd, waiting = box["rb_chd"], {}
    rb_chd.work(None, waiting)
    assert list(waiting) == ["1-wiiu.json"] and box["ran"] == [] and os.path.exists(box["disc"])
    box["have"][0] = True                          # fetched meanwhile: the running worker takes it
    rb_chd.work(None, waiting)
    assert waiting == {} and box["ran"] == [("wux", box["disc"])]
    assert os.path.exists(box["disc"][:-4] + ".wux") and not os.listdir(rommlib.CHD_QUEUE_DIR)


def test_a_tool_gone_mid_task_keeps_the_task(box, monkeypatch):
    rb_chd = box["rb_chd"]
    box["have"][0] = True

    def gone(src, tmp, threads):
        box["have"][0] = False                     # an upgrade's swap, say
        raise rb_chd.ToolMissing("wuatool")
    monkeypatch.setitem(rb_chd.RUNNERS, "wux", gone)
    rb_chd.main()
    assert os.listdir(rommlib.CHD_QUEUE_DIR) == ["1-wiiu.json"] and os.path.exists(box["disc"])
    log = open(rommlib.LOG_PATH).read()
    assert "dropping" not in log and "wiiu-tools isn't installed yet" in log


def test_a_settings_download_starts_the_worker(box, rb, monkeypatch):
    import pygame
    pygame.init()
    app = rb.App(None, headless=True)
    app.bg = lambda fn, done=None, err=None: done(fn()) if done else fn()
    started = []
    monkeypatch.setattr(app, "spawn_helper", started.append)

    def install(spec, progress=None):
        assert app.wua_busy                        # set before the thread starts
        box["have"][0] = True
        return "/fake/wuatool"
    monkeypatch.setattr(rommlib, "install_tool", install)
    app.cfg["wux"] = False
    scr = rb.SettingsScreen(app)
    scr._toggle_tool("wux")                        # asks first: wuatool isn't here
    app.modal.on_ok()
    assert app.cfg["wux"] is True and started == ["rb_chd.py"] and not app.wua_busy


def test_no_key_found_by_wuatool_still_makes_a_wux(box, monkeypatch):
    """Python's cryptography missing: the key can't be checked before, so a
    decrypt task runs wuatool, which finds none (rc 3): the .wud becomes a .wux."""
    rb_chd = box["rb_chd"]
    box["have"][0] = True
    monkeypatch.setattr(rommlib, "load_config", lambda: dict(rommlib.DEFAULT_CONFIG, keep_free_gb=0, wua=True))
    monkeypatch.setattr(rommlib, "wiiu_check", lambda src: None)          # what the ImportError branch does
    monkeypatch.setitem(rb_chd.RUNNERS, "wua", lambda src, tmp, threads: (3, rb_chd.WUA_FAIL[3]))
    os.remove(os.path.join(rommlib.CHD_QUEUE_DIR, "1-wiiu.json"))
    assert rb_chd.convert(dict(box["task"], mode="wua")) is True
    assert box["ran"] == [("wux", box["disc"])] and os.path.exists(box["disc"][:-4] + ".wux")
    assert not os.path.exists(box["disc"][:-4] + ".wua")
    assert "chd: WUX Lorem.wud instead of WUA: no disc key" in open(rommlib.LOG_PATH).read()


def test_a_wux_of_a_cut_short_image_is_replaced_by_a_fresh_download(box):
    rb_chd, disc = box["rb_chd"], box["disc"]
    box["have"][0] = True
    out = disc[:-4] + ".wux"
    open(out, "wb").write(b"WUX0" + b"\0" * 12 + (12345).to_bytes(8, "little"))   # made from a shorter .wud
    assert rb_chd.convert(box["task"]) is True
    assert int.from_bytes(open(out, "rb").read()[0x10:0x18], "little") == 0x20000
    log = open(rommlib.LOG_PATH).read()
    assert "replacing %s: made from a different (cut-short?) image" % out in log
