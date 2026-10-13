"""Activity: downloads on the left, compressing on this box on the right. The panel takes the
focus with left/right, pauses, resumes and stops the running conversion, reorders or takes
out a queued one and opens Compress; the line on top says when everything's done. The
worker (rb_chd) honours the pause and the stop."""
import json
import os
import time

import pytest

import rommlib

MB = 1 << 20


@pytest.fixture
def app(isolated, rb, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    a = rb.App(None, headless=True)
    a.spawned = []
    monkeypatch.setattr(a, "spawn_helper", lambda name: a.spawned.append(name))
    return a


def _convs(since=60.0, pct=50.0, queued=1, results=True):
    """The worker's published state: one conversion running, `queued` behind it."""
    os.makedirs(rommlib.CHD_QUEUE_DIR, exist_ok=True)
    names = ["1-run.json"] + ["%d-next.json" % (k + 2) for k in range(queued)]
    for k, n in enumerate(names):
        src = os.path.join(rommlib.ROMS_ROOT, "psx", "Game %d (USA).cue" % k)
        os.makedirs(os.path.dirname(src), exist_ok=True)
        with open(src, "wb") as f:
            f.truncate(MB)
        with open(os.path.join(rommlib.CHD_QUEUE_DIR, n), "w") as f:
            json.dump({"system": "psx", "mode": "createcd", "sources": [src]}, f)
    with open(rommlib.chd_current_path(), "w") as f:
        json.dump({"task": "1-run.json", "since": time.time() - since, "file": "Game 0 (USA).cue",
                   "step": "Converting", "pct": pct}, f)
    if results:
        with open(rommlib.chd_results_path(), "w") as f:
            f.write(json.dumps({"time": time.time() - 60, "task": "0-a.json", "result": "ok", "mode": "createcd",
                                "sources": ["Old (USA).cue"], "in_size": 400 * MB, "out_size": 200 * MB}) + "\n")
    return names


def _job(app, name, state, done=0, total=700 * MB, speed=0.0):
    J = rommlib.Job
    j = J({"id": abs(hash(name)) % 10000, "name": name, "fs_name": name + ".zip", "fs_size_bytes": total}, "psx")
    j.state, j.done_bytes, j.total_bytes, j.speed = state, done, total, speed
    app.downloader.jobs.append(j)
    return j


def _screen(rb, app):
    app.push(rb.Screen(app))
    scr = rb.DownloadsScreen(app)
    app.push(scr)
    scr.draw(app.canvas)
    return scr


def test_left_and_right_move_between_the_downloads_and_the_panel(rb, app):
    _convs()
    _job(app, "Lorem (USA)", rommlib.Job.DOWNLOADING, 100 * MB, speed=10 * MB)
    scr = _screen(rb, app)
    assert scr.crumbs() == ["Activity"] and scr.focus == "list"
    assert ("X", "Actions") in scr.hints() and ("LEFT RIGHT", "Compressing") in scr.hints()
    scr.action("right")
    assert scr.focus == "panel" and scr.panel_item()["kind"] == "running"
    assert scr.hints()[:3] == [("A", "Pause"), ("X", "Stop this one"), ("Y", "Compress games")]
    scr.action("down")
    assert scr.panel_item()["kind"] == "queued" and scr.hints()[:2] == [("A", "Convert next"), ("X", "Take out")]
    scr.action("down")
    assert scr.panel_item()["kind"] == "more" and scr.hints()[0] == ("A", "Compress games")
    scr.action("down")
    assert scr.panel_item()["kind"] == "more"                  # stays on the last
    scr.action("left")
    assert scr.focus == "list"
    scr.action("ok")
    assert isinstance(app.modal, rb.ActionMenu)                # the download's own menu, as before


def test_with_no_downloads_the_panel_has_the_focus(rb, app):
    scr = _screen(rb, app)
    assert scr.focus == "panel" and [it["kind"] for it in scr.panel_items()] == ["more"]
    scr.action("left")
    assert scr.focus == "panel"
    scr.action("ok")
    assert isinstance(app.stack[-1], rb.CompressScreen)


def test_pause_and_resume_the_running_conversion(rb, app):
    _convs()
    scr = _screen(rb, app)
    assert scr.focus == "panel" and not rommlib.conversions_held()
    scr.action("ok")
    assert rommlib.conversions_held() and scr.held and scr.hints()[0] == ("A", "Resume")
    scr.action("ok")
    assert not rommlib.conversions_held() and app.spawned == ["rb_chd.py"]


def test_stop_this_one_asks_then_tells_the_worker(rb, app):
    _convs()
    scr = _screen(rb, app)
    scr.action("x")
    dlg = app.modal
    assert isinstance(dlg, rb.Confirm) and dlg.danger and "the original stays" in dlg.body
    dlg.action("back")
    assert not rommlib.stop_requested("1-run.json")
    scr.action("x")
    app.modal.action("ok")
    assert rommlib.stop_requested("1-run.json") and not rommlib.stop_requested("2-next.json")


def test_a_queued_conversion_goes_first_or_leaves_the_queue(rb, app):
    names = _convs(queued=2)
    scr = _screen(rb, app)
    scr.action("down")
    scr.action("down")                                         # the second queued one
    assert scr.panel_item()["name"] == names[2]
    scr.action("ok")
    assert os.path.exists(os.path.join(rommlib.CHD_QUEUE_DIR, "0000000000000-" + names[2]))
    scr.action("x")                                            # now the first queued one: out
    left = sorted(os.listdir(rommlib.CHD_QUEUE_DIR))
    assert len(left) == 2 and names[0] in left


def test_y_in_the_panel_opens_compress(rb, app):
    _convs()
    _job(app, "Lorem (USA)", rommlib.Job.QUEUED)
    scr = _screen(rb, app)
    scr.action("y")                                            # in the list: clear finished
    assert app.stack[-1] is scr
    scr.action("right")
    scr.action("y")
    assert isinstance(app.stack[-1], rb.CompressScreen)


def test_tapping_a_panel_card_focuses_it_then_presses_it(rb, app):
    _convs()
    _job(app, "Lorem (USA)", rommlib.Job.QUEUED)
    scr = _screen(rb, app)
    app.targets = []
    scr.draw(app.canvas)
    pan = rb.split(None, rb.ACT_BODY)[1]
    more = [r for r, _ in app.targets if r.left < pan.left + 20 and r.left > pan.left and r.bottom > pan.bottom - 40]
    app.tap(more[0].center)
    assert scr.focus == "panel" and scr.panel_item()["kind"] == "more" and app.stack[-1] is scr
    app.tap(more[0].center)
    assert isinstance(app.stack[-1], rb.CompressScreen)


def test_the_line_on_top_adds_downloads_and_conversions(rb, app):
    """Downloads: what's left over the speed, smoothed over about 5 s; the running conversion
    from its progress (50% in 60 s: 60 s left), each queued one about as long again (120 s)."""
    _convs(since=60.0, pct=50.0, queued=1)
    _job(app, "Lorem (USA)", rommlib.Job.DOWNLOADING, 100 * MB, 700 * MB, speed=10 * MB)
    scr = _screen(rb, app)
    now = time.time()
    o = scr.outlook(now)
    assert o["live"] == 1 and o["convs"] == 2
    assert o["dl_secs"] == pytest.approx(60.0) and o["secs"] == pytest.approx(60 + 60 + 120, abs=2)
    assert scr.headline(o) == ("Everything's done in about 4 min", rb.TEXT)
    app.downloader.jobs[0].speed = 20 * MB                     # a burst: the estimate eases toward it
    o = scr.outlook(now + 5)
    assert scr.speed == pytest.approx(10 * MB + 10 * MB * (1 - 2.718281828 ** -1), rel=1e-3)
    assert 600 / 20 < o["dl_secs"] < 60


def test_the_line_on_top_when_it_cant_tell(rb, app):
    _job(app, "Lorem (USA)", rommlib.Job.QUEUED)
    scr = _screen(rb, app)
    assert scr.headline(scr.outlook()) == ("Waiting to download", rb.DIM)
    app.downloader.hold = True
    assert scr.headline(scr.outlook())[1] == rb.AMBER
    app.downloader.jobs.clear()
    assert scr.headline(scr.outlook()) == ("Nothing running", rb.DIM)


def test_failed_rows_say_why_under_the_name(rb, app):
    j = _job(app, "Lorem (USA)", rommlib.Job.FAILED)
    j.message = "Checksum doesn't match RomM after 2 tries"
    scr = _screen(rb, app)
    assert scr.status(j) == ("Failed", rb.RED) and scr.detail(j) == (j.message, rb.RED)


@pytest.mark.parametrize("secs, said", [(7, "7 s"), (59.4, "59 s"), (61, "about 1 min"), (420, "about 7 min"),
                                        (4800, "about 1 h 20 min")])
def test_time_left(rb, secs, said):
    assert rb.time_left(secs) == said


# ------------------------------------------------------------------ the worker's side

def test_the_worker_pauses_while_held_and_stops_when_asked(isolated, monkeypatch):
    import rb_chd
    monkeypatch.setattr(rommlib, "es_busy", lambda *a, **k: "")
    assert rb_chd.busy_reason() == ""
    rommlib.hold_conversions(True)
    assert rb_chd.busy_reason() == "hold" and rommlib.conversions_held()
    rommlib.hold_conversions(False)
    assert rb_chd.busy_reason() == ""
    monkeypatch.setattr(rb_chd, "TOOL_POLL", 0.05)
    monkeypatch.setattr(rb_chd, "memory_check", lambda *a: None)
    rb_chd._state["task"] = "1-run.json"
    rommlib.stop_conversion("1-run.json")
    t0 = time.time()
    with pytest.raises(rb_chd.Stopped):
        rb_chd.run_tool(["sleep", "30"])
    assert time.time() - t0 < 10
    rommlib.clear_stop()
    assert not rommlib.stop_requested("1-run.json")


def test_a_stopped_task_leaves_the_queue_and_can_be_queued_again(isolated, monkeypatch):
    import rb_chd
    monkeypatch.setattr(rommlib, "es_busy", lambda *a, **k: "")
    names = _convs(queued=0, results=False)
    rommlib.stop_conversion(names[0])
    os.remove(rommlib.chd_current_path())
    rb_chd.work(None)
    assert os.listdir(rommlib.CHD_QUEUE_DIR) == []
    with open(rommlib.chd_results_path()) as f:
        res = [json.loads(line) for line in f]
    assert [r["result"] for r in res] == ["stopped"] and res[0]["retry"]["mode"] == "createcd"
    assert not os.path.exists(os.path.join(rommlib.DATA_DIR, "chd-stop"))
    assert os.path.exists(os.path.join(rommlib.ROMS_ROOT, "psx", "Game 0 (USA).cue"))    # the original stays
