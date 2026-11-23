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
    j.state, j.done_bytes, j.total_bytes, j.speed, j.got = state, done, total, speed, done
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
    assert scr.time_line(o) == "Done in 4 min"
    app.downloader.jobs[0].speed = 20 * MB                     # a burst: the estimate eases toward it
    o = scr.outlook(now + 5)
    assert scr.speed == pytest.approx(10 * MB + 10 * MB * (1 - 2.718281828 ** -1), rel=1e-3)
    assert 600 / 20 < o["dl_secs"] < 60


def test_the_time_left_keeps_the_queues_pace_with_many_small_files(rb, app):
    """40 files of 1 MB, each a 10 MB/s burst then a pause (its check, the next request): the
    queue really moves 1 MB a second. Once it has 5 s of that, the time left is what's left at
    that pace, shown between two files too, and steady instead of following each burst (#7)."""
    J = rommlib.Job
    files = [_job(app, "Cart %02d" % i, J.QUEUED, 0, MB) for i in range(40)]
    scr = _screen(rb, app)
    t, said = time.time(), []
    for k, j in enumerate(files[:30]):
        j.state, j.done_bytes, j.got, j.speed = J.DOWNLOADING, MB // 2, MB // 2, 10 * MB    # its burst
        o_burst = scr.outlook(t)
        t += 0.5
        j.state, j.done_bytes, j.got, j.speed = J.DONE, MB, MB, 0.0          # between two files
        o_gap = scr.outlook(t)
        t += 0.5
        if k >= 7:
            left = 40 - k - 1                                                  # MB, at 1 MB/s
            for o in (o_burst, o_gap):
                assert o["dl_secs"] is not None and o["dl_secs"] == pytest.approx(left, rel=0.2)
            said.append(scr.time_line(o_gap))
    assert all(x and x.startswith("Done in ") for x in said)
    # one queued and finished between two looks (a small file, no speed of its own): its bytes count
    late = _job(app, "Cart late", J.DONE, MB, MB)
    before = app.dl_rate.total
    scr.outlook(t)
    assert app.dl_rate.total == before + MB and late in app.dl_rate.seen
    app.downloader.hold = True                                                 # a pause starts the pace afresh
    scr.outlook(t)
    assert app.dl_rate.marks == []


def test_the_pace_is_measured_with_activity_closed_so_it_opens_with_a_time(rb, app, monkeypatch):
    """Small files that each finish in under a second have no speed of their own; the app
    measures the queue's pace every frame wherever you are, so Activity opened in the middle
    of the batch says when it's done at once (#4)."""
    J = rommlib.Job
    clock = [time.time()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    files = [_job(app, "Cart %02d" % i, J.QUEUED, 0, MB) for i in range(20)]
    for j in files[:8]:                                       # 1 MB a second, Activity not open
        app.tick_downloads()
        j.state, j.done_bytes, j.got = J.DONE, MB, MB
        clock[0] += 0.5
        app.tick_downloads()
        clock[0] += 0.5
    scr = _screen(rb, app)
    o = scr.outlook(clock[0])
    assert o["dl_secs"] == pytest.approx(12, rel=0.25)
    assert scr.time_line(o).startswith("Done in ")


def test_a_resumed_download_or_a_jumping_clock_doesnt_make_up_a_pace(rb, app):
    """The sweep: a download resumed from a 3 GB .part (done_bytes back to 0, then 3 GB at once)
    isn't 3 GB downloaded in a frame; nor is a clock set forward (NTP on a box with no clock
    battery) hours of downloading. Either way the time left stays what the pace says."""
    J = rommlib.Job
    big = _job(app, "Big", J.DOWNLOADING, 0, 4096 * MB, MB)
    other = _job(app, "Other", J.DOWNLOADING, 0, 4096 * MB, MB)
    meter, t = rb.RateMeter(), 1000.0
    for k in range(20):                                       # 1 MB/s, from the other one
        other.done_bytes += MB // 2
        other.got += MB // 2
        meter.rate(app.downloader.jobs, True, t)
        t += 0.5
    assert meter.rate(app.downloader.jobs, True, t) == pytest.approx(MB, rel=0.1)
    big.done_bytes = 0                                        # it resumes: the .part counts as done...
    meter.rate(app.downloader.jobs, True, t + 0.1)
    big.done_bytes = 3072 * MB                                # ...but none of it came just now
    assert meter.rate(app.downloader.jobs, True, t + 0.2) == pytest.approx(MB, rel=0.15)
    other.got += MB // 2
    assert meter.rate(app.downloader.jobs, True, t + 0.2 + 3600 * 24) is None   # the clock jumped: afresh
    assert meter.marks and meter.total == 0


def test_the_line_on_top_holds_steady_while_states_flicker(rb, app):
    """A new headline replaces the one shown once it has lasted 2 s (#5): fast files going
    Downloading, Checking downloads, Downloading… stay "Downloading"; a moment of "All done"
    between two conversions doesn't show; anything that needs you (paused), both ways."""
    scr = _screen(rb, app)
    T, D = rb.theme.TEXT, rb.theme.DIM
    t = 1000.0
    scr.shown = None
    assert scr.steady(("Downloading", T), t) == ("Downloading", T)
    for k in range(6):                                        # flips every half second
        said = ("Checking downloads", T) if k % 2 == 0 else ("Downloading", T)
        assert scr.steady(said, t + 0.5 * k) == ("Downloading", T)
    assert scr.steady(("Unpacking", T), t + 3.5) == ("Downloading", T)
    assert scr.steady(("Unpacking", T), t + 4.6) == ("Unpacking", T)          # Downloading ended 2 s ago
    assert scr.steady(("Unpacking", T), t + 7) == ("Unpacking", T)
    assert scr.steady(("Compressing", T), t + 8) == ("Unpacking", T)
    assert scr.steady(("Compressing", T), t + 10.5) == ("Compressing", T)
    assert scr.steady(("All done", D), t + 11) == ("Compressing", T)
    assert scr.steady(("Compressing", T), t + 11.5) == ("Compressing", T)
    paused = ("Downloads are paused: {X} Actions resumes them", rb.theme.AMBER)
    assert scr.steady(paused, t + 12) == paused                               # needs you: at once
    assert scr.steady(("Downloading", T), t + 12.1) == ("Downloading", T)     # and gone at once


def test_a_line_no_longer_said_gives_way_even_while_two_others_flicker(rb, app):
    """The sweep: "Waiting to download" on screen, then small files flick between Downloading
    (1.5 s) and Checking downloads (0.5 s): neither lasts 2 s in a row, but the old line went."""
    scr = _screen(rb, app)
    T, D = rb.theme.TEXT, rb.theme.DIM
    t = 1000.0
    scr.shown = None
    scr.steady(("Waiting to download", D), t)
    got = None
    for i in range(1, 200):                                   # 20 s, a frame every 0.1 s
        line = "Downloading" if (i % 20) < 15 else "Checking downloads"
        got = scr.steady((line, T), t + 0.1 * i)
    assert got[0] != "Waiting to download"
    assert got[0] == "Downloading"                            # and it holds the one that keeps coming back


def test_the_card_on_top_keeps_the_time_apart_from_whats_going_on(rb, app):
    """#6: what's going on on the left (never a time), when it's all done on its own on the right,
    which doesn't blink out for a moment without one; paused: no time."""
    _convs(since=60.0, pct=50.0, queued=1)
    j = _job(app, "Lorem (USA)", rommlib.Job.DOWNLOADING, 100 * MB, 700 * MB, speed=10 * MB)
    scr = _screen(rb, app)
    o = scr.outlook()
    assert scr.state_line(o) == ("Downloading", rb.TEXT)
    assert scr.time_line(o) == "Done in 4 min"
    j.state = rommlib.Job.EXTRACTING                          # no time can be told for a moment
    o = scr.outlook()
    assert scr.state_line(o) == ("Unpacking", rb.TEXT) and scr.time_line(o) is None
    assert scr.steady_time(None, scr.time_shown[1] + 1) == "Done in 4 min"   # kept a moment
    assert scr.steady_time(None, scr.time_shown[1] + 3) is None              # then gone
    j.state = rommlib.Job.DOWNLOADING
    app.downloader.hold = True
    o = scr.outlook()
    assert scr.time_line(o) is None and scr.state_line(o)[1] == rb.AMBER


def test_the_bar_on_top_counts_what_this_run_already_finished(rb, app):
    """#12: a finished download leaves the queue but stays in the bar, so on a big run it fills
    up instead of following the one file downloading; Clear finished doesn't set it back, and
    a run that's over starts the next one from nothing."""
    J = rommlib.Job
    jobs = [_job(app, "Cart %02d" % i, J.QUEUED, 0, MB) for i in range(4)]
    app.tick_downloads()
    for j in jobs[:2]:
        j.state, j.done_bytes, j.got = J.DONE, MB, MB
    app.tick_downloads()
    scr = _screen(rb, app)
    assert scr.outlook()["frac"] == pytest.approx(0.5)
    scr.clear_finished()
    app.tick_downloads()
    assert scr.outlook()["frac"] == pytest.approx(0.5)
    for j in jobs[2:]:
        j.state, j.done_bytes, j.got = J.DONE, MB, MB
    app.tick_downloads()
    assert app.run_done == 0                                # nothing left: the next run starts afresh
    _job(app, "Cart new", J.QUEUED, 0, MB)
    app.tick_downloads()
    assert scr.outlook()["frac"] == 0


def test_the_time_on_top_keeps_its_place_as_it_ticks_down(rb, app, monkeypatch):
    """#12: the time is one line in one colour, and starts where the widest one so far did: 19 s to 18 s
    (digits differ in width) doesn't move the words before it."""
    scr = _screen(rb, app)
    o = scr.outlook()
    said = {"v": "Done in 1 h 20 min"}
    monkeypatch.setattr(scr, "time_line", lambda o, now=None: said["v"])
    scr.draw_head(app.canvas, o)
    wide = scr.time_w
    said["v"] = "Done in 18 s"
    scr.draw_head(app.canvas, o)
    assert scr.time_w == wide
    said["v"] = None
    scr.time_shown = None
    scr.draw_head(app.canvas, o)
    assert scr.time_w == 0                                  # gone: the next time starts from its own width


def test_the_time_on_top_is_one_line_that_doesnt_flip_while_small_files_are_checked(rb, app, monkeypatch):
    """#12: a file being checked while more downloads keeps the time for all of it, instead of
    flipping to the downloads' time and back; compressing paused leaves the line as it is; and
    it's said one way however long ("Done in 40 s", "Done in 4 min"), never "about"."""
    _convs(since=60.0, pct=50.0, queued=1)
    _job(app, "Lorem (USA)", rommlib.Job.DOWNLOADING, 100 * MB, 700 * MB, speed=10 * MB)
    small = _job(app, "Cart", rommlib.Job.VERIFYING, MB, MB)
    scr = _screen(rb, app)
    assert scr.time_line(scr.outlook()) == "Done in 4 min"
    small.state = rommlib.Job.DONE
    assert scr.time_line(scr.outlook()) == "Done in 4 min"
    scr.held = True                                           # compressing paused: the downloads alone
    assert scr.time_line(scr.outlook()) == "Done in 1 min"
    app.downloader.jobs.clear()
    assert scr.time_line(scr.outlook()) is None               # only paused compressing left: no time
    scr.held = False
    _job(app, "Ipsum (USA)", rommlib.Job.DOWNLOADING, 600 * MB, 700 * MB, speed=10 * MB)
    o = scr.outlook()
    assert scr.time_line(o).startswith("Done in ") and "about" not in scr.time_line(o)


def test_the_time_on_top_doesnt_blink_out_while_a_conversion_starts(rb, app):
    """A conversion just started (too early to tell) or one waiting its turn: the last time for
    all of it keeps counting down while downloads run, never below theirs; none yet: theirs."""
    _convs(since=60.0, pct=50.0, queued=1)
    _job(app, "Lorem (USA)", rommlib.Job.DOWNLOADING, 100 * MB, 700 * MB, speed=10 * MB)
    scr = _screen(rb, app)
    t = time.time()
    assert scr.time_line(scr.outlook(t), t) == "Done in 4 min"
    _convs(since=1.0, pct=1.0, queued=1)                      # the next one starts: can't be told yet
    scr.refresh()
    o = scr.outlook(t + 60)
    assert o["secs"] is None and scr.time_line(o, t + 60) == "Done in 3 min"
    scr2 = _screen(rb, app)                                   # opened just now: nothing to go on
    assert scr2.time_line(o, t + 60) == "Done in %s" % rb.time_left(o["dl_secs"], about=False)


def test_the_line_on_top_gives_no_time_while_a_download_unpacks(rb, app):
    """All of it is downloaded, so nothing is left over the speed: that isn't "done in 1 s"
    while the file is still being unpacked or checked."""
    j = _job(app, "Lorem (USA)", rommlib.Job.EXTRACTING, 700 * MB, 700 * MB)
    scr = _screen(rb, app)
    o = scr.outlook()
    assert o["secs"] is None and scr.time_line(o) is None and scr.headline(o) == ("Unpacking", rb.TEXT)
    j.state = rommlib.Job.VERIFYING
    assert scr.headline(scr.outlook()) == ("Checking downloads", rb.TEXT)
    _job(app, "Ipsum (USA)", rommlib.Job.DOWNLOADING, 100 * MB, 700 * MB, speed=10 * MB)
    assert scr.time_line(scr.outlook()) == "Done in 1 min"   # (more is downloading: the check hides in it)


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
    assert rb.time_left(secs, about=False) == said.replace("about ", "")


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


@pytest.mark.parametrize("on", [True, False])
def test_popups_in_the_app_can_be_turned_off_but_a_failure_still_shows(rb, app, on):
    """Settings, Popups in the app (#9): off, a finished download says nothing (Activity lists it);
    one that failed still says so."""
    app.cfg["notify_app"] = on
    app.toasts = []
    ok = _job(app, "Lorem (USA)", rommlib.Job.DONE, 700 * MB, 700 * MB)
    ok.message = "Ready to play"
    bad = _job(app, "Ipsum (USA)", rommlib.Job.FAILED)
    bad.message = "Checksum doesn't match RomM after 2 tries"
    app.tick_downloads()
    said = [t[0] for t in app.toasts]
    assert any(s.startswith("Failed: Ipsum (USA)") for s in said)
    assert any(s.startswith(("Ready: Lorem", "Downloaded: Lorem")) for s in said) == on
