"""Play now: the app quits and a detached waiter has EmulationStation start
the game, once ES lists it (rb_finalize adds new downloads after the app has
quit) and rb_finalize has finished writing game info, and nothing else has
started meanwhile."""
import os
import time

import pytest

import rommlib


@pytest.fixture
def es(isolated, monkeypatch):
    """A fake ES (not the fake_es fixture: that one makes time.sleep a no-op)."""
    from fake_es import FakeES
    e = FakeES(rommlib.ROMS_ROOT, {"gba": {".gba", ".zip"}})
    e.launch_any = False                     # like Batocera's ES: only paths it lists
    monkeypatch.setattr(rommlib, "ES_API", e.url)
    yield e
    e.close()


APP = "/userdata/roms/pygame/rommbrowser.pygame"


def _game(name="Lorem Quest (USA).gba"):
    p = os.path.join(rommlib.ROMS_ROOT, "gba", name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "wb").write(b"x")
    return p


def _until(cond, secs=8):
    end = time.time() + secs
    while time.time() < end and not cond():
        time.sleep(0.1)
    return cond()


def test_the_game_starts_once_es_lists_it(es):
    game = _game()
    es.running = APP                                   # the app still open
    assert rommlib.launch_when_closed(game, "gba", "Lorem Quest", wait=30)
    time.sleep(0.6)
    es.running = None                                  # it quits
    time.sleep(2.6)
    assert es.launched == []                           # ES doesn't list the new file yet: no blind /launch
    es.reload()                                        # rb_finalize hands it to ES
    assert _until(lambda: es.running == game)
    assert es.launched == [game] and es.notes == []


def test_a_game_es_lists_already_starts_right_after_the_app_quits(es):
    game = _game()
    es.reload()
    t0 = time.time()
    assert rommlib.launch_when_closed(game, "gba", "Lorem Quest", wait=30)
    assert _until(lambda: es.running == game)
    assert time.time() - t0 < 4                        # the 1.5 s pause, then at once


def test_nothing_starts_over_a_game_the_player_started(es):
    game = _game()
    assert rommlib.launch_when_closed(game, "gba", "Lorem Quest", wait=30)
    time.sleep(2.2)                                    # waiting for ES to list it
    es.running = "/userdata/roms/gba/Ipsum.gba"        # the player starts another game meanwhile
    es.reload()
    time.sleep(2.5)
    assert es.launched == [] and es.running == "/userdata/roms/gba/Ipsum.gba"


def test_it_gives_up_with_a_popup_when_es_never_lists_it(es):
    game = _game()
    assert rommlib.launch_when_closed(game, "gba", "Lorem Quest", wait=1)
    assert _until(lambda: es.notes, 8)
    assert es.launched == []
    assert es.notes == ["Lorem Quest isn't in EmulationStation's list yet: start it from gba once it shows."]


def test_once_listed_it_is_launched_once_and_left_to_es(es):
    """ES may ask first (its missing-BIOS warning waits for the player), so a
    game that isn't running right after /launch isn't an error."""
    game = _game()
    es.reload()
    es.launch_works = False
    assert rommlib.launch_when_closed(game, "gba", "Lorem Quest", wait=2)
    assert _until(lambda: es.launched)
    time.sleep(3)
    assert es.launched == [game] and es.notes == []


def _finalizing():
    """rb_finalize in the middle of a batch: its queue claimed into .work,
    its lock held (by this test, as rb_finalize holds it)."""
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    open(rommlib.GAMELIST_QUEUE + ".work", "w").write('{"system": "gba"}\n')
    return rommlib.try_lock(rommlib.finalize_lock_path())


def test_the_waiter_reads_rb_finalizes_own_lock():
    import rb_finalize
    assert rommlib.finalize_lock_path() == rb_finalize.lock_path()


def test_the_game_starts_only_once_rb_finalize_is_done(es):
    """ES lists the game as soon as rb_finalize reloads its games, while it is
    still writing game info: /launch waits for it to finish."""
    game = _game()
    es.reload()
    with _finalizing() as fd:
        assert fd is not None
        assert rommlib.launch_when_closed(game, "gba", "Lorem Quest", wait=30)
        time.sleep(3.5)
        assert es.launched == []                       # listed, but rb_finalize is still writing
        os.remove(rommlib.GAMELIST_QUEUE + ".work")    # its batch is done
        assert _until(lambda: es.running == game)
    assert es.launched == [game] and es.notes == []


def test_a_finished_finalize_still_holding_its_lock_doesnt_hold_the_game(es):
    game = _game()
    es.reload()
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    with rommlib.try_lock(rommlib.finalize_lock_path()):   # no batch left: it's on its way out
        assert rommlib.launch_when_closed(game, "gba", "Lorem Quest", wait=30)
        assert _until(lambda: es.running == game)


def test_a_batch_no_finalize_is_working_on_doesnt_hold_the_game(es):
    """A .work left by a killed rb_finalize, or a queue no finalize has started
    on: nothing writes to ES now, and the next finalize waits for the game."""
    game = _game()
    es.reload()
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    open(rommlib.GAMELIST_QUEUE + ".work", "w").write('{"system": "gba"}\n')
    open(rommlib.GAMELIST_QUEUE, "w").write('{"system": "gba"}\n')
    assert rommlib.launch_when_closed(game, "gba", "Lorem Quest", wait=30)
    assert _until(lambda: es.running == game)


def test_while_finalize_writes_a_game_the_player_starts_wins(es):
    game = _game()
    es.reload()
    with _finalizing():
        assert rommlib.launch_when_closed(game, "gba", "Lorem Quest", wait=30)
        time.sleep(2.2)
        es.running = "/userdata/roms/gba/Ipsum.gba"    # the player starts another game meanwhile
        time.sleep(2)
        os.remove(rommlib.GAMELIST_QUEUE + ".work")
        time.sleep(1.5)
    assert es.launched == [] and es.running == "/userdata/roms/gba/Ipsum.gba"


def test_a_finalize_that_outlasts_the_wait_gets_a_popup_not_a_launch(es):
    game = _game()
    es.reload()
    with _finalizing():
        assert rommlib.launch_when_closed(game, "gba", "Lorem Quest", wait=1)
        assert _until(lambda: es.notes, 8)
    assert es.launched == [] and es.notes == ["Lorem Quest is ready: start it from gba."]


def test_reopening_after_an_update_uses_the_same_waiter(es, tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(rommlib, "launch_when_closed", lambda path, **kw: seen.append((path, kw)) or True)
    assert rommlib.relaunch_when_closed(str(tmp_path), python="/usr/bin/python3")
    assert seen == [(os.path.join(str(tmp_path), "rommbrowser.pygame"), {"python": "/usr/bin/python3"})]


# ------------------------------------------------------------ what to launch

ES_EXTS = {"psx": {".cue", ".chd", ".m3u", ".zip"}, "ps3": {".ps3", ".psn", ".squashfs"},
           "wiiu": {".wua", ".wud", ".wux", ".rpx"}, "gba": {".gba", ".zip"}}


def _file(system, name, data=b"x"):
    p = os.path.join(rommlib.ROMS_ROOT, system, name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "wb").write(data)
    return p


def _rom(rid, name):
    return {"id": rid, "name": name, "fs_name": name, "fs_name_no_ext": os.path.splitext(name)[0]}


def test_the_primary_file_is_launched(isolated):
    lib = rommlib.Library()
    cue = _file("psx", "Lorem (USA).cue")
    bins = _file("psx", "Lorem (USA) (Track 1).bin")
    lib.mark(_rom(1, "Lorem (USA).zip"), "psx", [cue, bins])
    assert rommlib.play_target(lib, _rom(1, "Lorem (USA).zip"), "psx", ES_EXTS) == (cue, None)
    wua = _file("wiiu", "Ipsum (USA).wua")
    lib.mark(_rom(2, "Ipsum (USA).zip"), "wiiu", [wua])
    assert rommlib.play_target(lib, _rom(2, "Ipsum (USA).zip"), "wiiu", ES_EXTS) == (wua, None)


def test_a_ps3_folder_is_launched_as_the_folder(isolated):
    lib = rommlib.Library()
    folder = os.path.join(rommlib.ROMS_ROOT, "ps3", "Dolor (USA).ps3")
    os.makedirs(os.path.join(folder, "PS3_GAME"))
    open(os.path.join(folder, "PS3_GAME", "PARAM.SFO"), "wb").write(b"x")
    lib.mark(_rom(3, "Dolor (USA).zip"), "ps3", [folder])
    assert rommlib.play_target(lib, _rom(3, "Dolor (USA).zip"), "ps3", ES_EXTS) == (folder, None)


def test_a_disc_set_launches_its_playlist_once_every_disc_is_here(isolated):
    lib = rommlib.Library()
    group = {"id": "psx:sit", "base": "Sit (USA)", "title": "Sit", "rom_ids": [4, 5]}
    d1 = _file("psx", "Sit (USA) (Disc 1).chd")
    lib.mark(_rom(4, "Sit (USA) (Disc 1).zip"), "psx", [d1], group=group)
    path, why = rommlib.play_target(lib, _rom(4, "Sit (USA) (Disc 1).zip"), "psx", ES_EXTS)
    assert path is None and why == "Its other discs aren't on the box yet"
    d2 = _file("psx", "Sit (USA) (Disc 2).chd")
    m3u = _file("psx", "Sit (USA).m3u", b"Sit (USA) (Disc 1).chd\nSit (USA) (Disc 2).chd\n")
    lib.mark(_rom(4, "Sit (USA) (Disc 1).zip"), "psx", [d1, m3u], group=group)
    lib.mark(_rom(5, "Sit (USA) (Disc 2).zip"), "psx", [d2, m3u], group=group)
    assert rommlib.play_target(lib, _rom(5, "Sit (USA) (Disc 2).zip"), "psx", ES_EXTS) == (m3u, None)


def test_never_a_game_whose_conversion_is_waiting_or_running(isolated):
    lib = rommlib.Library()
    cue = _file("psx", "Amet (USA).cue")
    lib.mark(_rom(6, "Amet (USA).zip"), "psx", [cue])
    task = {"system": "psx", "mode": "chd", "sources": [cue], "roots": [], "queued": 0}
    rommlib.publish_queue_task(task, "1-6-psx.json")
    path, why = rommlib.play_target(lib, _rom(6, "Amet (USA).zip"), "psx", ES_EXTS)
    assert path is None and why == "Waiting to be compressed to CHD: it plays once that's done"
    rommlib.atomic_write_json(rommlib.chd_current_path(), {"task": "1-6-psx.json", "pct": 42})
    path, why = rommlib.play_target(lib, _rom(6, "Amet (USA).zip"), "psx", ES_EXTS)
    assert path is None and why == "Being compressed to CHD (42%): it plays once that's done"
    os.remove(os.path.join(rommlib.CHD_QUEUE_DIR, "1-6-psx.json"))      # done: the CHD replaced the cue
    os.remove(cue)
    chd = _file("psx", "Amet (USA).chd")
    lib.mark(_rom(6, "Amet (USA).zip"), "psx", [chd])
    assert rommlib.play_target(lib, _rom(6, "Amet (USA).zip"), "psx", ES_EXTS) == (chd, None)


def test_other_games_conversions_dont_block(isolated):
    lib = rommlib.Library()
    gba = _file("gba", "Consectetur (USA).gba")
    lib.mark(_rom(7, "Consectetur (USA).gba"), "gba", [gba])
    rommlib.publish_queue_task({"system": "psx", "mode": "chd", "sources": ["/elsewhere/x.cue"]}, "1-1-psx.json")
    assert rommlib.play_target(lib, _rom(7, "Consectetur (USA).gba"), "gba", ES_EXTS) == (gba, None)
    path, why = rommlib.play_target(lib, _rom(8, "Gone (USA).gba"), "gba", ES_EXTS)
    assert path is None and why == "It isn't on this box"


# ------------------------------------------------------------ in the app

@pytest.fixture
def app(isolated, rb, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    app = rb.App(None, headless=True)
    app.platforms = [{"id": 7, "name": "Game Boy Advance", "_system": "gba"}]
    app.platform_by_id = {7: app.platforms[0]}
    app.downloader.es = ES_EXTS
    app.launches, app.notes = [], []
    monkeypatch.setattr(rommlib, "launch_when_closed",
                        lambda path, **kw: app.launches.append((path, kw["system"], kw["name"])) or True)
    monkeypatch.setattr(rommlib, "es_notify", app.notes.append)
    return app


def _here(app, rid=1, name="Lorem Quest (USA)"):
    rom = dict(_rom(rid, name + ".gba"), name=name, platform_id=7)
    p = _file("gba", name + ".gba")
    app.library.mark(rom, "gba", [p])
    return rom, p


def _job(app, rom, state):
    j = rommlib.Job(rom, "gba")
    j.state = state
    app.downloader.jobs.append(j)
    return j


def test_the_game_menu_plays_it_at_once_when_nothing_else_runs(app, rb):
    rom, p = _here(app)
    m = rb.ManageMenu(app, rom)
    assert m.entries[0] == ("Play now", "RomM-o-Cera closes and the game starts", m.entries[0][2])
    assert m.sel == 0
    app.modal = m
    m.action("ok")
    assert app.launches == [(p, "gba", "Lorem Quest (USA)")]
    assert app.notes == ["Starting Lorem Quest (USA)…"] and app.running is False and app.play_after == p


def test_downloads_still_running_ask_first(app, rb):
    rom, p = _here(app)
    _job(app, _rom(2, "Ipsum.gba"), rommlib.Job.DOWNLOADING)
    app.cfg["background_downloads"] = True
    app.play_entry(rom)[2]()
    c = app.modal
    assert isinstance(c, rb.Confirm) and c.title == "Play Lorem Quest (USA) now?" and c.ok_label == "Play now"
    assert c.body == ("RomM-o-Cera closes and Lorem Quest (USA) starts. 1 download still running keeps going "
                      "in the background while you play.")
    c.action("back")
    assert app.launches == [] and app.running
    app.cfg["background_downloads"] = False
    _job(app, _rom(3, "Dolor.gba"), rommlib.Job.QUEUED)
    app.play_entry(rom)[2]()
    assert app.modal.body == ("RomM-o-Cera closes and Lorem Quest (USA) starts. 2 downloads still running are "
                              "paused and resume next time you open RomM-o-Cera.")
    app.modal.action("ok")
    assert app.launches == [(p, "gba", "Lorem Quest (USA)")] and app.running is False


def test_play_now_waits_while_it_downloads_converts_or_updates(app, rb):
    rom, p = _here(app)
    j = _job(app, rom, rommlib.Job.EXTRACTING)
    assert app.play_entry(rom) == ("Play now", "It's still downloading", None)
    j.state = rommlib.Job.DONE
    rommlib.publish_queue_task({"system": "gba", "mode": "chd", "sources": [p]}, "1-1-gba.json")
    assert app.play_entry(rom)[2] is None and "it plays once that's done" in app.play_entry(rom)[1]
    os.remove(os.path.join(rommlib.CHD_QUEUE_DIR, "1-1-gba.json"))
    app.updating = True
    assert app.play_entry(rom) == ("Play now", "Wait for the update to finish", None)


def test_a_finished_download_offers_play_now_in_downloads_and_its_toast(app, rb):
    rom, p = _here(app)
    j = _job(app, rom, rommlib.Job.DONE)
    scr = rb.DownloadsScreen(app)
    app.push(scr)
    scr.menu(j)
    assert app.modal.entries[0][0] == "Play now" and app.modal.entries[0][2]
    app.modal = None
    app.tick_downloads()
    msg, _, _, _, tap = app.toasts[-1]
    assert msg == "Ready: Lorem Quest (USA) · Play now from {SELECT} Activity" and tap
    tap()
    assert app.launches == [(p, "gba", "Lorem Quest (USA)")]


def test_a_finished_download_that_cant_play_yet_just_says_ready(app, rb):
    rom, p = _here(app)
    j = _job(app, rom, rommlib.Job.DONE)
    rommlib.publish_queue_task({"system": "gba", "mode": "chd", "sources": [p]}, "1-1-gba.json")
    app.tick_downloads()
    assert app.toasts[-1][0] == "Ready: Lorem Quest (USA)" and not app.toasts[-1][4]
    app.downloader.jobs.remove(j)


def test_after_play_now_es_gets_the_game_even_while_downloads_carry_on(app, monkeypatch):
    rom, p = _here(app)
    _job(app, _rom(2, "Ipsum.gba"), rommlib.Job.QUEUED)
    spawned = []
    monkeypatch.setattr(app, "spawn_helper", spawned.append)
    monkeypatch.setattr(app.downloader, "pause_all", lambda: None)
    app.cfg["background_downloads"] = True
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    open(rommlib.GAMELIST_QUEUE, "w").write("{}\n")
    app.headless = False                              # on_exit as on the box
    app.on_exit()
    assert spawned == ["rb_dl.py"]                    # a plain quit: the game list waits for the downloads
    spawned.clear()
    app.play_after = p
    app.on_exit()
    assert spawned == ["rb_dl.py", "rb_finalize.py"]  # Play now: ES lists the game now
