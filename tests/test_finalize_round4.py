"""rb_finalize round 4: media ES refused isn't fetched again within MEDIA_TRY_GAP, a full
disk at the start of a drain is a quiet "next time", a torn .done is logged and kept aside,
and an ES list that couldn't be read isn't taken for an empty one."""
import os

import pytest

import rommlib
import rb_finalize
from test_es_media import FakeEsApi, all_files, game_here, rom_54, served  # noqa: F401 (fixture)
from test_finalize_fixes import cfg, draining  # noqa: F401 (fixture)


def test_media_es_refused_is_fetched_once_per_try_gap_not_every_run(served, monkeypatch):
    files, asked = served
    rom = rom_54()
    files.update(all_files(rom))
    path, g = game_here()

    class Full(FakeEsApi):
        def set_media(self, *a):
            return False
    es = Full({path: g})
    e = rommlib.gamelist_entry(rom, "nes", [path])
    clock = [1.7e9]
    monkeypatch.setattr(rb_finalize.time, "time", lambda: clock[0])
    per_run = []
    for run in range(8):                      # 8 finalize runs in an hour
        clock[0] = 1.7e9 + run * 400
        n = len(asked)
        left = rb_finalize.process([e], object(), {"nes": {".zip"}}, es, cfg())
        assert left == [e]
        rb_finalize._spend(left, clock[0])
        per_run.append(len(asked) - n)
    assert per_run[0] > 0 and per_run[1:] == [0] * 7
    assert e["_tries"] == 1
    clock[0] += rb_finalize.MEDIA_TRY_GAP      # the gap is over: one more try
    n = len(asked)
    left = rb_finalize.process([e], object(), {"nes": {".zip"}}, es, cfg())
    assert len(asked) > n and left == [e]


def test_a_full_disk_at_the_start_of_a_drain_is_not_a_crash(draining, monkeypatch):
    rommlib.append_gamelist({"system": "nes", "paths": ["/r/nes/Good.zip"]})

    def full():
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(rb_finalize, "_settle", full)
    assert rb_finalize.drain() is False
    assert draining == []


def test_a_full_disk_inside_the_claim_is_not_a_crash(draining, monkeypatch):
    rommlib.append_gamelist({"system": "nes", "paths": ["/r/nes/Good.zip"]})
    calls = []

    def settle():
        calls.append(1)
        if len(calls) > 1:
            raise OSError(28, "No space left on device")
    monkeypatch.setattr(rb_finalize, "_settle", settle)
    assert rb_finalize.drain() is False and len(calls) == 2


@pytest.mark.parametrize("text", ["", '{"left": [{"sys', "[]"])
def test_a_torn_done_is_logged_and_kept_aside(isolated, text):
    os.makedirs(rommlib.DATA_DIR)
    p = rb_finalize.done_path()
    with open(p, "w") as f:
        f.write(text)
    rb_finalize._settle()
    assert not os.path.exists(p)
    assert open(p + ".bad").read() == text
    assert "doesn't parse" in open(rommlib.LOG_PATH).read()


def test_an_es_list_that_couldnt_be_read_gets_no_addgames(isolated, tmp_path):
    f = tmp_path / "Game.zip"
    f.write_text("x")

    class Flaky:
        failed = {"nes"}
        posted = []

        def games(self, system, refresh=False):
            return None

        def add_games(self, system, new):
            self.posted.append(new)
            return 200
    esapi = Flaky()
    e = {"system": "nes", "paths": [str(f)]}
    assert rb_finalize.add_games(esapi, [e], {"nes": {".zip"}}) is True
    assert esapi.posted == []
    esapi.failed = set()                       # ES said "no such system" (404): that one is added
    rb_finalize.add_games(esapi, [e], {"nes": {".zip"}})
    assert len(esapi.posted) == 1


def test_a_rename_queued_earlier_leaves_a_file_back_under_its_old_name_be(isolated):
    """A conversion's rename (cue folder -> CHD) applies to the entries queued before it, and
    to later ones only for a path that is gone: a disc fetched again under its old name
    afterwards keeps that name (it isn't sent back to the CHD it replaced)."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    folder = os.path.join(sysdir, "Game (Disc 1)")
    cue = os.path.join(folder, "Game (Disc 1).cue")
    chd = os.path.join(sysdir, "Game (Disc 1).chd")
    gone = os.path.join(sysdir, "Gone (Disc 2)")
    os.makedirs(folder)
    open(cue, "w").close()                    # fetched again since the conversion
    before = {"system": "psx", "paths": [folder]}
    rename = {"system": "psx", "rename": {cue: chd, folder: chd, gone: chd + "2"}, "hide": []}
    after = {"system": "psx", "paths": [folder]}
    hide = {"system": "psx", "hide": [cue, os.path.join(gone, "x.cue")]}
    rb_finalize.route_renames([before, rename, after, hide])
    assert before["paths"] == [chd]
    assert after["paths"] == [folder]
    assert hide["hide"] == [cue, chd + "2"]   # what's gone still follows the rename
    assert rename["rename"] == {cue: chd, folder: chd, gone: chd + "2"}
