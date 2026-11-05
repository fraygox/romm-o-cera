"""Activity and Conversions say what is really going on: the line on top never contradicts the
rows, one waiting job has one number, a conversion worker that died isn't shown converting
(and is started again), a conversion waiting for room says so, and the download list (finished
ones too) survives a restart."""
import json
import os
import subprocess
import sys
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
    return scr


def _task(name, title="Lorem (USA)"):
    os.makedirs(rommlib.CHD_QUEUE_DIR, exist_ok=True)
    src = os.path.join(rommlib.ROMS_ROOT, "psx", title + ".cue")
    os.makedirs(os.path.dirname(src), exist_ok=True)
    with open(src, "wb") as f:
        f.truncate(MB)
    with open(os.path.join(rommlib.CHD_QUEUE_DIR, name), "w") as f:
        json.dump({"system": "psx", "mode": "createcd", "sources": [src]}, f)
    return src


def _results(*entries):
    with open(rommlib.chd_results_path(), "w") as f:
        for e in entries:
            f.write(json.dumps(dict({"time": time.time(), "mode": "createcd", "sources": ["Lorem (USA).cue"]}, **e))
                    + "\n")


def _current(task, pid):
    with open(rommlib.chd_current_path(), "w") as f:
        json.dump({"task": task, "since": time.time() - 30, "file": "Lorem (USA).cue", "step": "Converting",
                   "pct": 38, "paused": "", "pid": pid}, f)


WAIT = "WAIT for space Lorem (USA).cue (needs ~66.1 MB, 5.0 GB free, keeping 5.0 GB free)"


# ------------------------------------------------------------------ the line on top

@pytest.mark.parametrize("state, said", [("Downloading", "Downloading"), ("Extracting", "Unpacking"),
                                         ("Verifying", "Checking downloads")])
def test_the_line_on_top_says_a_download_is_under_way(rb, app, state, said):
    """No speed yet (a download at 2%, or one unpacking): not "Waiting to download"."""
    _job(app, "Lorem (USA)", state, 14 * MB, 700 * MB)
    _job(app, "Ipsum (USA)", rommlib.Job.QUEUED)
    scr = _screen(rb, app)
    assert scr.headline(scr.outlook()) == (said, rb.TEXT)


def test_the_line_on_top_says_downloads_wait_for_space(rb, app):
    j = _job(app, "Lorem (USA)", rommlib.Job.QUEUED)
    j.short_space = 85 * MB
    scr = _screen(rb, app)
    assert scr.headline(scr.outlook()) == ("Downloads wait for space", rb.AMBER)


def test_the_line_on_top_counts_failed_downloads(rb, app):
    _job(app, "Lorem (USA)", rommlib.Job.DONE)
    _job(app, "Ipsum (USA)", rommlib.Job.FAILED)
    scr = _screen(rb, app)
    assert scr.headline(scr.outlook()) == ("1 download failed", rb.RED)
    app.downloader.jobs.pop()
    assert scr.headline(scr.outlook()) == ("All done", rb.DIM)


def test_the_running_conversions_time_left_reads_like_the_line_on_top(rb, app):
    """30 s for 60%: 20 s left, said as "20 s" in both places, never "about 1 min" beside it."""
    _task("1-run.json")
    _current("1-run.json", os.getpid())
    row = {"kind": "running", "cur": {"pct": 60, "since": time.time() - 30, "step": "Converting"}}
    st, _ = rb.conversion_status(row)
    assert st == "Converting 60% · 20 s left"


def test_queued_conversions_arent_called_compressing(rb, app):
    _task("1-a.json")
    scr = _screen(rb, app)
    assert scr.headline(scr.outlook()) == ("Waiting to compress", rb.DIM)
    _results({"task": "1-a.json", "result": "waiting", "message": WAIT})
    scr.refresh()
    assert scr.headline(scr.outlook()) == ("Compressing waits for space", rb.AMBER)


# ------------------------------------------------------------------ one number for one wait

def test_the_downloads_note_says_the_waiting_rows_number(rb, app, monkeypatch):
    j = _job(app, "Lorem (USA)", rommlib.Job.QUEUED, total=80 * MB)
    j.short_space = 85 * MB
    scr = _screen(rb, app)
    assert scr.status(j)[0] == "Waiting for 85.0 MB of space"
    seen = []
    rb.patch(monkeypatch, "list_head", lambda c, f, r, title, note, *a, **k: seen.append(note))
    scr.draw_list(app.canvas, rb.split(None, rb.ACT_BODY)[0], scr.view())
    assert seen == ["waiting for 85.0 MB of space"]


def test_already_queued_says_where_it_stands(rb, app):
    j = _job(app, "Lorem (USA)", rommlib.Job.QUEUED)
    assert rb.already_queued(j) == "Already in the download queue"
    j.short_space = 6 * MB
    assert rb.already_queued(j) == "Already queued: it waits for 6.0 MB of space"
    j.state = rommlib.Job.EXTRACTING
    assert rb.already_queued(j) == "Already downloaded: it's being unpacked"
    j.state = rommlib.Job.DOWNLOADING
    assert rb.already_queued(j) == "Already downloading"
    assert rb.already_queued(None) == "Already downloading"


# ------------------------------------------------------------------ waiting for space

def test_a_conversion_waiting_for_room_says_so_and_lately_drops_old_waits(rb, app):
    _task("1-a.json")
    _results({"task": "0-old.json", "result": "waiting", "message": WAIT},
             {"task": "0-old.json", "result": "ok", "in_size": 66 * MB, "out_size": 41 * MB},
             {"task": "1-a.json", "result": "waiting", "message": WAIT})
    rows = rb.conversion_rows()
    assert [(r["kind"], r["name"]) for r in rows] == [("queued", "1-a.json"), ("ok", "0-old.json")]
    assert rows[0]["wait"] == "space"
    scr = rb.ConversionsScreen(app)
    assert scr.state(rows[0])[:2] == ("Waiting for space", rb.AMBER)
    assert rb.room_note(rows[0]).startswith("Waiting for space. It needs about 66.1 MB beside the original")
    _results({"task": "1-a.json", "result": "waiting", "message": WAIT},
             {"task": "1-a.json", "result": "ok", "in_size": 66 * MB, "out_size": 41 * MB})
    os.remove(os.path.join(rommlib.CHD_QUEUE_DIR, "1-a.json"))
    assert [r["kind"] for r in rb.conversion_rows()] == ["ok"]          # done: its wait isn't "Lately"


# ------------------------------------------------------------------ a worker that died

def test_a_dead_workers_task_shows_queued_and_the_app_starts_one_again(rb, app, monkeypatch):
    _task("1-run.json")
    p = subprocess.Popen([sys.executable, "-c", "pass", "rb_chd.py"])
    p.wait()
    _current("1-run.json", p.pid)                       # killed mid-task: its file stays behind
    assert rommlib.conversion_current() == {} and rommlib.conversion_worker_gone()
    assert [r["kind"] for r in rb.conversion_rows()] == ["queued"]
    assert rommlib.chd_status("1-run.json") == "queued"
    monkeypatch.setattr(app, "headless", False)
    assert app.watch_conversions(now=1000.0) and app.spawned == ["rb_chd.py"]
    assert not app.watch_conversions(now=1005.0)       # looked a moment ago
    assert app.watch_conversions(now=1011.0)


def test_a_live_worker_is_trusted(rb, app, monkeypatch):
    _task("1-run.json")
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "rb_chd.py"])
    try:
        _current("1-run.json", p.pid)
        assert rommlib.conversion_current()["task"] == "1-run.json" and not rommlib.conversion_worker_gone()
        assert [r["kind"] for r in rb.conversion_rows()] == ["running"]
        monkeypatch.setattr(app, "headless", False)
        assert not app.watch_conversions(now=1000.0) and app.spawned == []
    finally:
        p.kill()
        p.wait()


def test_the_worker_publishes_its_pid(isolated):
    import rb_chd
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    rb_chd.status(task="1-a.json", since=time.time(), file="x.cue", step="Starting", pct=None, paused="")
    with open(rommlib.chd_current_path()) as f:
        assert json.load(f)["pid"] == os.getpid()


# ------------------------------------------------------------------ the list survives a restart

def test_finished_downloads_are_still_listed_after_a_restart(isolated, monkeypatch):
    lib = rommlib.Library()
    d = rommlib.Downloader(None, lib, dict(rommlib.DEFAULT_CONFIG))
    try:
        J = rommlib.Job
        for i, st in enumerate((J.DONE, J.FAILED)):
            j = J({"id": 10 + i, "name": "Game %d" % i, "fs_name": "g%d.zip" % i, "fs_size_bytes": MB,
                   "summary": "long text " * 50}, "nes")
            j.state, j.message = st, "Ready to play" if st == J.DONE else "Checksum doesn't match"
            d.jobs.append(j)
        d.save_queue()
    finally:
        d.close()
    with open(rommlib.QUEUE_PATH) as f:
        hist = json.load(f)["history"]
    assert [h["state"] for h in hist] == ["Done", "Failed"] and "summary" not in hist[0]["rom"]
    d2 = rommlib.Downloader(None, lib, dict(rommlib.DEFAULT_CONFIG))
    try:
        assert d2.resume() == 0
        assert [(j.label, j.state, j.restored) for j in d2.jobs] == [("Game 0", "Done", True),
                                                                      ("Game 1", "Failed", True)]
        d2.resume()
        assert len(d2.jobs) == 2                            # read once
        assert d2.clear_finished() == 2
        with open(rommlib.QUEUE_PATH) as f:
            assert json.load(f)["history"] == []
    finally:
        d2.close()


def test_history_keeps_the_last_ones(isolated):
    d = rommlib.Downloader(None, rommlib.Library(), dict(rommlib.DEFAULT_CONFIG))
    try:
        for i in range(rommlib.HISTORY_KEEP + 5):
            j = rommlib.Job({"id": i + 1, "name": "G%d" % i, "fs_size_bytes": 1}, "nes")
            j.state = rommlib.Job.DONE
            d.jobs.append(j)
        d.save_queue()
        with open(rommlib.QUEUE_PATH) as f:
            hist = json.load(f)["history"]
        assert len(hist) == rommlib.HISTORY_KEEP and hist[-1]["rom"]["name"] == "G%d" % (rommlib.HISTORY_KEEP + 4)
    finally:
        d.close()


def test_restored_downloads_arent_announced_again(rb, app):
    j = _job(app, "Lorem (USA)", rommlib.Job.DONE)
    j.restored = True
    toasts = []
    app.toast = lambda *a, **k: toasts.append(a)
    app.tick_downloads()
    assert toasts == []
    j.requeue()
    assert not j.restored


def test_retried_download_starts_the_converter_when_it_finishes(rb, app):
    j = _job(app, "Ipsum (USA)", rommlib.Job.FAILED)
    app.toast = lambda *a, **k: None
    spawned = []
    app.spawn_helper = lambda *a, **k: spawned.append(a)
    app.headless = False
    app.tick_downloads()                      # the failure is announced
    j.requeue()
    j.state, j.chd_queued = rommlib.Job.DONE, True
    app.tick_downloads()                      # the retry's success must start rb_chd again
    assert ("rb_chd.py",) in spawned
