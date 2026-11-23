"""The bulk download dialog (BulkDownload), whole platforms picked on Platforms, Activity during
a big run, and a Backlog of any size."""
import pytest

import rommlib

J = rommlib.Job
GB = 1 << 30


@pytest.fixture
def app(isolated, rb, monkeypatch, fast_mock_url):
    import pygame
    monkeypatch.setenv("ROMM_URL", fast_mock_url)
    monkeypatch.setenv("ROMM_TOKEN", "demo")
    pygame.init()
    a = rb.App(None, headless=True)

    def bg(fn, done=None, err=None):              # at once, in this thread
        try:
            res = fn()
        except Exception as e:
            return err(e) if err else None
        if done:
            done(res)
    monkeypatch.setattr(a, "bg", bg)
    a.load_platforms()
    a.downloader.hold = True
    return a


def _free(monkeypatch, gb, drive_gb=4000):
    """gb free on a drive of drive_gb (the test machine's own disk decides nothing)."""
    monkeypatch.setattr(rommlib, "free_bytes", lambda path=None, default=0: gb * GB)
    monkeypatch.setattr(rommlib, "disk_bytes", lambda path=None, default=0: drive_gb * GB)


def _roms(app, pid):
    return app.api.roms_all(limit=250, platform_ids=[pid], fields=rommlib.BULK_FIELDS)


def test_a_whole_platform_that_fits_downloads_every_game(app, rb, monkeypatch):
    _free(monkeypatch, 600)
    went = []
    monkeypatch.setattr(app, "bulk_go", lambda chosen, pool=None, after=None: went.append((len(chosen), pool)))
    m = rb.BulkDownload(app, "Download this whole list?", roms=_roms(app, 1), whole=True)
    app.modal = m
    assert m.space["fits"] and [o[0] for o in m.options()] == ["Download 1,240"]
    m.action("ok")
    assert went == [(1240, [])] and app.modal is None


def test_a_whole_platform_too_big_only_offers_free_up_space_and_comes_back(app, rb, monkeypatch):
    _free(monkeypatch, 100)
    app.push(rb.PlatformScreen(app))
    m = rb.BulkDownload(app, "Download this whole list?", roms=_roms(app, 6), whole=True)
    app.modal = m
    assert not m.space["fits"] and [o[0] for o in m.options()] == ["Free up space"]
    short_gb = -(-m.space["short"] // GB)
    m.action("ok")
    fu = app.stack[-1]
    assert isinstance(fu, rb.FreeUpScreen) and fu.free_gb == short_gb and app.modal is None
    _free(monkeypatch, 2000)                      # room made
    app.pop()
    assert app.modal is m and m.space["fits"]      # back, counted again
    assert [o[0] for o in m.options()] == ["Download {:,}".format(len(m.items))]


def test_a_list_too_big_offers_the_ones_that_fit_all_of_them_or_free_up_space(app, rb, monkeypatch):
    _free(monkeypatch, 100)
    m = rb.BulkDownload(app, "Download this whole list?", roms=_roms(app, 6), whole=False)
    labels = [o[0] for o in m.options()]
    assert labels[0].startswith("Download the ") and labels[0].endswith(" that fit")
    assert labels[1:] == ["Download all {:,} anyway".format(len(m.items)), "Free up space"]


def test_its_space_is_activitys_forecast(app, rb, monkeypatch):
    """The dialog says it fits exactly when Activity's forecast, with these queued, would."""
    _free(monkeypatch, 300)
    queued = [J(r, "psx") for r in _roms(app, 6)[:50]]
    app.downloader.jobs.extend(queued)
    m = rb.BulkDownload(app, "x", roms=_roms(app, 7), whole=True)
    jobs = list(app.downloader.jobs) + [J(r, sy) for r, sy in m.items]
    fc = rommlib.queue_forecast(jobs, 300 * GB, rommlib.keep_free_bytes(app.cfg), rommlib.chd_enabled(app.cfg),
                                app.cfg)
    assert m.space["fits"] == fc["fits"] and m.space["short"] == fc["short"]


def test_listing_shows_progress_and_stop_queues_nothing(app, rb, monkeypatch):
    _free(monkeypatch, 600)
    seen, later = [], []
    real, run_now = app.api.roms_all, app.bg
    monkeypatch.setattr(app, "bg", lambda *a: later.append(a))      # in the background, as the app runs it

    def roms_all(**kw):
        kw["progress"](100, 1240)
        seen.append((app.modal.listed, app.modal.total))
        app.modal.action("back")                   # B while it lists
        return real(**kw)
    monkeypatch.setattr(app.api, "roms_all", roms_all)
    app.bulk_download("Download this whole list?", [("NES", {"platform_ids": [1]}, 1240)], whole=True)
    assert app.modal.state == "listing"
    run_now(*later.pop())
    assert seen == [(100, 1240)] and app.modal is None and not app.downloader.jobs


def test_platforms_picked_download_whole_one_after_another(app, rb, monkeypatch):
    _free(monkeypatch, 600)
    scr = rb.PlatformScreen(app)
    app.push(scr)
    app.dispatch("x")
    assert [e[0] for e in app.modal.entries] == ["Game night", "Pick several platforms"]
    app.modal.sel = 1
    app.dispatch("ok")
    assert scr.picking and ("X", "Download selected platforms") in scr.hints()
    for pid in (1, 2):
        scr.sel = next(i for i, p in enumerate(app.platforms) if p["id"] == pid)
        app.dispatch("ok")
    assert scr.picks == {1, 2}
    app.dispatch("x")
    m = app.modal
    assert isinstance(m, rb.BulkDownload) and m.whole and m.state == "confirm"
    order = [p["display_name"] for p in app.platforms if p["id"] in (1, 2)]   # the grid's order
    assert [label for label, _, _ in m.rows] == order
    assert len(m.items) == sum(p["rom_count"] for p in app.platforms if p["id"] in (1, 2))
    app.dispatch("back")
    app.dispatch("back")                           # B stops picking, it doesn't leave
    assert not scr.picking and app.stack[-1] is scr


def test_activity_says_how_far_a_big_run_is_and_what_is_left_by_platform(app, rb):
    jobs = []
    for k, sy in enumerate(["nes"] * 30 + ["snes"] * 20):
        j = J({"id": 9000 + k, "name": "G%d" % k, "fs_name": "G%d.zip" % k, "fs_size_bytes": 1}, sy)
        j.state = J.DONE if k < 5 else J.QUEUED
        jobs.append(j)
    app.downloader.jobs.extend(jobs)
    scr = rb.DownloadsScreen(app)
    line = scr.run_counts(app.downloader.jobs)
    assert line.startswith("5 of 50 done · 25 ") and line.endswith(" 20 " + app.system_name("snes"))
    assert scr.run_counts(jobs[:10]) == ""          # a short queue: nothing extra


def test_activity_retries_the_failed_and_checks_the_run_s_platforms(app, rb, monkeypatch):
    done = J({"id": 1, "name": "A", "fs_name": "A.zip", "fs_size_bytes": 1}, "nes")
    done.state = J.DONE
    bad = J({"id": 2, "name": "B", "fs_name": "B.zip", "fs_size_bytes": 1}, "nes")
    bad.state = J.FAILED
    app.downloader.jobs.extend([done, bad])
    scr = rb.DownloadsScreen(app)
    app.push(scr)
    acts = {e[0]: e for e in scr.list_actions()}
    acts["Retry the one that failed"][2]()
    assert bad.state == J.QUEUED
    pushed = []
    monkeypatch.setattr(rb.check, "AuditScreen", lambda *a, **k: pushed.append(k) or rb.Screen(app))
    monkeypatch.setattr(rb.downloads, "AuditScreen", lambda *a, **k: pushed.append(k) or rb.Screen(app))
    name = app.system_name("nes")
    acts["Check the games on %s" % name][2]()
    assert pushed == [{"deep": True, "systems": ["nes"]}]


def test_backlog_holds_every_game_trimmed(app, rb, monkeypatch):
    big = [dict(r, ss_metadata={"x": "y" * 3000}) for r in _roms(app, 1)] + _roms(app, 4)[:300]
    monkeypatch.setattr(app.api, "user_list", lambda name, offset=0, limit=60, platform_ids=None: {
        "items": big[offset:offset + limit], "total": len(big), "filtered": False})
    monkeypatch.setattr(rommlib, "backlog_fit_listed", lambda *a, **k: None)
    scr = rb.BacklogScreen(app)
    assert len(scr.items) == len(big) > 1000
    assert all("ss_metadata" not in r for r in scr.items) and scr.items[0]["name"] == big[0]["name"]


def test_a_big_run_says_ready_together_not_once_a_game(app, rb):
    def job(k, st):
        j = J({"id": 7000 + k, "name": "G%d" % k, "fs_name": "G%d.zip" % k, "fs_size_bytes": 1}, "nes")
        j.state = st
        return j
    toasts = []
    app.toast = lambda msg, *a, **k: toasts.append(msg)
    app.downloader.jobs.extend([job(k, J.QUEUED) for k in range(30)] + [job(100 + k, J.DONE) for k in range(5)])
    app.tick_downloads()
    assert toasts == ["Ready: 5 more games · {SELECT} Activity lists them"]
    app.downloader.jobs[:] = [job(200, J.DONE)]                 # a short queue: the game by name, as before
    app.tick_downloads()
    assert toasts[-1].startswith("Ready: G200")


def test_retrying_many_failed_downloads_saves_the_queue_once(app, rb, monkeypatch):
    app.downloader.persist = True
    jobs = []
    for k in range(40):
        j = J({"id": 8000 + k, "name": "F%d" % k, "fs_name": "F%d.zip" % k, "fs_size_bytes": 1}, "nes")
        j.state = J.FAILED
        jobs.append(j)
    app.downloader.jobs.extend(jobs)
    app.downloader.save_queue()
    writes = []
    real = app.downloader._write_queue
    monkeypatch.setattr(app.downloader, "_write_queue", lambda *a: (writes.append(1), real(*a)))
    scr = rb.DownloadsScreen(app)
    app.push(scr)
    next(e for e in scr.list_actions() if e[0].startswith("Retry the"))[2]()
    assert all(j.state == J.QUEUED for j in jobs) and len(writes) == 1


def test_free_up_space_never_offers_the_platforms_being_downloaded(app, rb, monkeypatch):
    _free(monkeypatch, 100)
    app.push(rb.PlatformScreen(app))
    m = rb.BulkDownload(app, "x", roms=_roms(app, 6), whole=True)
    monkeypatch.setattr(rb.space, "freeable_games", lambda lib, systems=None: [
        {"path": "/r/psx/A.chd", "system": "psx", "name": "A", "size": 9 << 30, "lastplayed": None, "playcount": 0},
        {"path": "/r/gba/B.gba", "system": "gba", "name": "B", "size": 1 << 20, "lastplayed": None, "playcount": 0}])
    m.free_up()
    fu = app.stack[-1]
    assert [g["system"] for g in fu.items] == ["gba"]


def test_a_finished_listing_waits_for_another_dialog(app, rb):
    m = rb.BulkDownload(app, "x", roms=[])
    other = rb.Confirm(app, "Pair again?", "", "OK", lambda: None)
    app.modal = other
    app.show_modal(m)
    assert app.modal is other
    app.modal = None
    app.show_waiting()
    assert app.modal is m


def test_free_space_that_cant_be_read_isnt_taken_for_none(app, rb, monkeypatch):
    monkeypatch.setattr(rommlib, "free_bytes", lambda path=None, default=0: default)
    m = rb.BulkDownload(app, "x", roms=_roms(app, 6), whole=True)
    assert m.unknown and m.space["fits"] and [o[0] for o in m.options()] == ["Download {:,}".format(len(m.items))]


def test_a_platform_bigger_than_the_drive_offers_only_back(app, rb, monkeypatch):
    _free(monkeypatch, 60, drive_gb=89)
    m = rb.BulkDownload(app, "x", roms=_roms(app, 6), whole=True)
    assert m.never and m.options() == []
    app.modal = m
    m.action("ok")                                   # A does nothing harmful: it just closes
    assert app.modal is None and not app.downloader.jobs


def test_free_up_space_picks_nothing_when_every_game_here_is_not_enough(app, rb, monkeypatch):
    toasts = []
    monkeypatch.setattr(app, "toast", lambda m, *a, **k: toasts.append(m))
    monkeypatch.setattr(rb.space, "freeable_games", lambda lib, systems=None: [
        {"path": "/r/gba/B%d.gba" % i, "system": "gba", "name": "B", "size": 1 << 30, "lastplayed": None,
         "playcount": 0} for i in range(5)])
    app.push(rb.PlatformScreen(app))
    fu = rb.FreeUpScreen(app, free_gb=700)
    app.push(fu)
    assert not fu.picks and "not the 700 GB" in toasts[-1]


def test_paused_games_queued_again_are_counted_once(app, rb, monkeypatch):
    """A platform paused partway, then downloaded whole again: its paused games were counted as the
    queue and again as what the download adds, and it asked to free space it didn't need."""
    roms = _roms(app, 1)
    _free(monkeypatch, 4000)
    sp = rb.BulkDownload(app, "x", roms=roms, whole=True).space
    _free(monkeypatch, (sp["need"] * 1.5 + sp["keep"]) / GB)      # room for it once, not twice
    fresh = rb.BulkDownload(app, "x", roms=roms, whole=True)
    assert fresh.space["fits"]
    for r, sy in fresh.items:                     # every one queued, then paused
        j = J(r, sy)
        j.state = J.PAUSED
        app.downloader.jobs.append(j)
    again = rb.BulkDownload(app, "x", roms=roms, whole=True)
    assert again.space["fits"] and again.space["queue"] == 0
    assert [o[0] for o in again.options()] == ["Download {:,}".format(len(again.items))]


def test_queueing_under_pause_all_says_they_wait_paused(app, rb, monkeypatch):
    """Pause all on with games still waiting: "Queued 30 downloads" in green, and none started."""
    said = []
    monkeypatch.setattr(app, "toast", lambda msg, col=None: said.append((msg, col)))
    app.downloader.jobs.append(J({"id": 999999, "name": "W", "fs_name": "W.zip"}, "nes"))
    app.downloader.hold = True
    app.bulk_go([(r, "nes") for r in _roms(app, 1)[:30]])
    msg, col = said[-1]
    assert msg.startswith("Queued 30 downloads") and "paused" in msg and col == rb.theme.AMBER
