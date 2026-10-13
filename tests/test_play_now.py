"""Play now: the app quits and a detached waiter has EmulationStation start
the game, once ES lists it (rb_finalize adds new downloads after the app has
quit) and nothing else has started meanwhile."""
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


def test_a_launch_es_ignored_says_where_to_start_it(es):
    game = _game()
    es.reload()
    es.launch_works = False
    assert rommlib.launch_when_closed(game, "gba", "Lorem Quest", wait=2)
    assert _until(lambda: es.notes, 10)
    assert es.launched == [game]
    assert es.notes == ["Lorem Quest didn't start: open it from gba in EmulationStation."]


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
