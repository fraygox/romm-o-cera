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
