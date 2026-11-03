"""rb_finalize: media RomM can't send now is tried again (never noted as gone), a PS3
folder gets its info, play history survives "not-a-date-time", a slow reload doesn't
push new games' info to the next run, a deferred rename doesn't reload ES every run,
a batch that always fails no longer blocks the rest, and an entry queued as finalize
exits isn't left waiting."""
import json
import os
import socket

import pytest

import rommlib
import rb_finalize
from test_es_media import FakeEsApi, PNG, all_files, game_here, rom_54, served  # noqa: F401 (fixture)
from test_net import quick, serve  # noqa: F401 (fixtures)


def cfg(**kw):
    c = dict(rommlib.DEFAULT_CONFIG)
    c.update(kw)
    return c


# ------------------------------------------------------------ 1. RomM down is not "never comes"

@pytest.mark.parametrize("status", [401, 403, 500, 502])
def test_a_romm_that_cant_serve_now_is_retried_and_not_asked_again_this_batch(serve, quick, isolated, status):
    s = serve(lambda p, n: (status, {}, b"no"))
    api = rommlib.Api(s.url, "t")
    info = {}
    assert rb_finalize.fetch_bytes(api, "/a.png", info=info) is None
    assert info.get("retry")
    hits = len(s.hits)
    info = {}
    assert rb_finalize.fetch_bytes(api, "/b.png", info=info) is None and info.get("retry")
    assert len(s.hits) == hits                                 # not asked again
    rb_finalize._ROMM_DOWN.clear()


def test_a_missing_file_is_romms_answer(serve, isolated):
    s = serve(lambda p, n: (404, {}, b"no"))
    info = {}
    assert rb_finalize.fetch_bytes(rommlib.Api(s.url, "t"), "/a.png", info=info) is None
    assert not info.get("retry")


def test_an_offline_romm_is_retried():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()                                               # nothing listens there
    api = rommlib.Api("http://127.0.0.1:%d" % port, "t")
    info = {}
    assert rb_finalize.fetch_bytes(api, "/a.png", info=info) is None and info.get("retry")
    assert rb_finalize.romm_down(api)
    rb_finalize._ROMM_DOWN.clear()


def test_media_romm_couldnt_send_is_kept_for_later_not_noted(served, monkeypatch):
    files, asked = served
    rom = rom_54()
    files.update(all_files(rom))
    real = rb_finalize.fetch_bytes

    def down(api, path, limit=200 << 20, info=None):           # RomM offline
        if info is not None:
            info["retry"] = "ConnectionError"
        return None
    monkeypatch.setattr(rb_finalize, "fetch_bytes", down)
    path, g = game_here()
    esapi = FakeEsApi({path: g})
    e = rommlib.gamelist_entry(rom, "nes", [path])
    assert rb_finalize.apply([e], None, {"nes": {".zip"}}, esapi, {}, cfg()) == [e]   # kept
    assert esapi.meta and not esapi.media                     # its info is in
    assert rommlib.media_skips() == {}                         # nothing noted as never coming
    monkeypatch.setattr(rb_finalize, "fetch_bytes", real)      # RomM back (the served files)
    assert rb_finalize.apply([e], None, {"nes": {".zip"}}, esapi, {}, cfg()) == []
    assert {t for t, _, _ in esapi.media} >= {"image", "thumbnail"}


def test_a_down_romm_costs_one_request_per_batch(serve, quick, isolated):
    s = serve(lambda p, n: (502, {}, b"bad gateway"))
    api = rommlib.Api(s.url, "t")
    sysdir = os.path.join(rommlib.ROMS_ROOT, "nes")
    os.makedirs(sysdir)
    games, entries = {}, []
    for i in range(6):
        rom = json.loads(json.dumps(rom_54()).replace("/2/3/", "/2/%d/" % (100 + i)))
        rom.update(id=100 + i, name="Lorem %d" % i, fs_name="Lorem %d.zip" % i)
        p = os.path.join(sysdir, rom["fs_name"])
        open(p, "wb").close()
        games[p] = {"id": "g%d" % i, "path": p, "name": "Lorem %d" % i}
        entries.append(rommlib.gamelist_entry(rom, "nes", [p]))
    one = None
    for parallel in (False, True):
        s.hits.clear()
        left = rb_finalize.apply(entries, api, {"nes": {".zip"}}, FakeEsApi(dict(games)), {}, cfg(), parallel=parallel)
        assert len(left) == len(entries)
        one = one or len(s.hits)
        assert len(s.hits) <= 3 * one                          # the first fetch or two (and their tries)
    assert rommlib.media_skips() == {}


# ------------------------------------------------------------ 2. game folders (PS3)

def test_a_ps3_folder_gets_its_info(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "ps3")
    game = os.path.join(sysdir, "Lorem Quest.ps3")
    os.makedirs(os.path.join(game, "PS3_GAME", "USRDIR"))
    open(os.path.join(game, "PS3_GAME", "USRDIR", "EBOOT.BIN"), "wb").close()
    es = {"ps3": {".ps3", ".psn", ".squashfs"}}
    e = {"system": "ps3", "paths": [game], "name": "Lorem Quest", "desc": "Ipsum"}
    assert rb_finalize.wanted([e], es) == {"ps3": {game}}
    esapi = FakeEsApi({game: {"id": "p1", "path": game, "name": "Lorem Quest"}})
    assert rb_finalize.process([e], None, es, esapi) == []
    assert esapi.meta == [{"desc": "Ipsum"}]


def test_a_source_waiting_to_be_converted_is_kept_without_spending_a_try(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "ps3")
    os.makedirs(sysdir)
    z = os.path.join(sysdir, "Lorem Quest.zip")
    open(z, "wb").close()
    os.makedirs(rommlib.CHD_QUEUE_DIR)
    with open(os.path.join(rommlib.CHD_QUEUE_DIR, "0001.json"), "w") as f:
        json.dump({"mode": "ps3", "system": "ps3", "sources": [z]}, f)
    es = {"ps3": {".ps3"}}
    e = {"system": "ps3", "paths": [z], "name": "Lorem Quest"}
    left = rb_finalize.process([e], None, es, FakeEsApi({}))
    assert left == [e] and e.get("_wait")
    os.remove(os.path.join(rommlib.CHD_QUEUE_DIR, "0001.json"))   # not converting: an ordinary wait
    e.pop("_wait")
    left = rb_finalize.process([e], None, es, FakeEsApi({}))
    assert left == [e] and not e.get("_wait")
    os.remove(z)                                                   # files gone: nothing to keep
    assert rb_finalize.process([e], None, es, FakeEsApi({})) == []


# ------------------------------------------------------------ 3. play history

def test_a_carried_last_played_beats_never_played():
    for never in ("not-a-date-time", "0", "", None):
        assert rb_finalize.merge_usage({"lastplayed": never}, {"lastplayed": "20260101T120000"}) == \
            {"lastplayed": "20260101T120000"}
    assert rb_finalize.merge_usage({"lastplayed": "20260601T000000"}, {"lastplayed": "20260101T120000"}) == {}


# ------------------------------------------------------------ 4. a slow full reload

class _SlowEs(FakeEsApi):
    """An ES whose full reload is still going when finalize stops waiting for it."""

    def __init__(self, games):
        super().__init__({})
        self.later, self.reloads = games, 0

    def add_games(self, system, games):
        return 204                       # Batocera 43.1: a full reload instead

    def reload(self):
        self.reloads += 1


def test_games_a_slow_reload_lists_late_still_get_their_info(isolated, monkeypatch):
    path, g = game_here(name="Lorem.zip")
    esapi = _SlowEs({path: g})
    waits = []

    def wait(es_, entries, es, timeout=180, settle=6, lists=None):
        waits.append(len(entries))
        if len(waits) == 2:              # by the second wait the reload is done
            es_.g.update(es_.later)
            return True
        return rb_finalize.NotYet(loading=True)    # still listing more as the wait ended
    monkeypatch.setattr(rb_finalize, "wait_listed", wait)
    e = {"system": "nes", "paths": [path], "name": "Lorem", "desc": "Ipsum"}
    assert rb_finalize.process([e], None, {"nes": {".zip"}}, esapi) == []
    assert waits == [1, 1] and esapi.meta and esapi.meta[0]["desc"] == "Ipsum"


# ------------------------------------------------------------ 5. a deferred rename

def test_a_deferred_rename_es_already_shows_needs_no_reload(isolated, fake_es):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    os.makedirs(sysdir)
    chd, cue = os.path.join(sysdir, "Lorem.chd"), os.path.join(sysdir, "Lorem.cue")
    open(chd, "wb").close()
    es = fake_es({"psx": {".chd", ".cue"}})
    e = {"system": "psx", "rename": {cue: chd}, "hide": []}
    rb_finalize.process([e], None, {"psx": {".chd", ".cue"}})
    assert ("GET", "/reloadgames") not in es.calls
    open(cue, "w").close()                                    # ES still lists the old file
    es.reload()
    os.remove(cue)
    rb_finalize.process([dict(e)], None, {"psx": {".chd", ".cue"}})
    assert ("GET", "/reloadgames") in es.calls


# ------------------------------------------------------------ 6. a batch that always fails

@pytest.fixture
def draining(isolated, monkeypatch):
    os.makedirs(rommlib.DATA_DIR)
    monkeypatch.setattr(rb_finalize, "wait_idle", lambda *a: True)
    monkeypatch.setattr(rb_finalize, "game_running", lambda: False)
    monkeypatch.setattr(rb_finalize.time, "sleep", lambda s: None)
    done = []

    def process(entries, api=None, es=None, esapi=None, cfg=None, bad=None):
        def one(e):
            if e.get("poison"):
                raise ValueError("always")
            done.append(e["paths"][0])
        if bad is None and any(e.get("poison") for e in entries):
            raise ValueError("always")
        rb_finalize._each(entries, one, bad)
        return []
    monkeypatch.setattr(rb_finalize, "process", process)
    return done


def test_an_entry_that_always_fails_is_set_aside_and_the_rest_go_in(draining):
    rommlib.append_gamelist({"system": "nes", "paths": ["/r/nes/Good.zip"]},
                            {"system": "nes", "paths": ["/r/nes/Bad.zip"], "poison": True})
    assert rb_finalize.drain() is True
    assert draining == ["/r/nes/Good.zip"]
    work = rommlib.GAMELIST_QUEUE + ".work"
    assert not os.path.exists(work) and not os.path.exists(rb_finalize.fails_path())
    assert "Bad.zip" in open(work + ".bad").read()


def test_failed_passes_count_across_runs(draining, monkeypatch):
    rommlib.append_gamelist({"system": "nes", "paths": ["/r/nes/Good.zip"]},
                            {"system": "nes", "paths": ["/r/nes/Bad.zip"], "poison": True})
    monkeypatch.setattr(rb_finalize, "MAX_TRIES", 2)
    assert rb_finalize.drain() is False                        # two failed passes, then this run stops
    assert rb_finalize._fails() == 2 and draining == []
    assert rb_finalize.drain() is True                         # the third, then one at a time
    assert draining == ["/r/nes/Good.zip"]


def test_one_entry_whose_media_rule_breaks_doesnt_sink_the_batch(served, monkeypatch):
    files, _ = served
    rom = rom_54()
    files.update(all_files(rom))
    good, gg = game_here(name="Good.zip")
    bad, bg = game_here(name="Bad.zip")
    bg["id"] = "b1"
    real = rb_finalize.media_wanted

    def wanted(e, cfg=None):
        if e["paths"][0] == bad:
            raise TypeError("a malformed entry")
        return real(e, cfg)
    monkeypatch.setattr(rb_finalize, "media_wanted", wanted)
    esapi = FakeEsApi({good: gg, bad: bg})
    eb, eg = rommlib.gamelist_entry(rom, "nes", [bad]), rommlib.gamelist_entry(rom, "nes", [good])
    assert rb_finalize.apply([eb, eg], object(), {"nes": {".zip"}}, esapi, {}, cfg()) == [eb]
    assert esapi.files.get(("a1", "thumbnail")) == PNG


def test_a_reload_es_answers_with_an_error_doesnt_fail_the_batch(serve):
    s = serve(lambda p, n: (500, {}, b"oops"))
    rb_finalize.EsApi(s.url).reload()                          # logged, not raised


def test_unreadable_lines_are_set_aside_once(isolated):
    os.makedirs(rommlib.DATA_DIR)
    work = rommlib.GAMELIST_QUEUE + ".work"
    with open(work, "w") as f:
        f.write('{"system": "nes", "paths": ["/r/a.zip"]}\n{"syst\n')
    for _ in range(3):
        assert len(rb_finalize._read_jsonl(work, rewrite=True)) == 1
    assert open(work + ".bad").read() == '{"syst\n'
    assert open(work).read() == '{"system": "nes", "paths": ["/r/a.zip"]}\n'


# ------------------------------------------------------------ 7. connections

def test_media_files_share_one_connection(serve, isolated):
    body = PNG + b"\0" * (300 << 10)
    s = serve(lambda p, n: (200, {}, body))
    api = rommlib.Api(s.url, "t")
    for i in range(5):
        assert rb_finalize.fetch_bytes(api, "/m/%d.png" % i) == body
    assert len(s.ports) == 1


# ------------------------------------------------------------ 8. queued as finalize exits

def test_an_entry_queued_as_finalize_finishes_is_handed_over_too(isolated, monkeypatch):
    os.makedirs(rommlib.DATA_DIR)
    runs = []

    def drain(retry_deferred=True):
        runs.append(retry_deferred)
        if len(runs) == 1:              # queued just as the lock was let go
            rommlib.append_gamelist({"system": "nes", "paths": ["/r/nes/Late.zip"]})
        else:
            os.remove(rommlib.GAMELIST_QUEUE)
        return True
    monkeypatch.setattr(rb_finalize, "drain", drain)
    rb_finalize.main()
    assert runs == [True, False]                               # deferred entries once per run


def test_finalize_that_stopped_early_doesnt_loop(isolated, monkeypatch):
    os.makedirs(rommlib.DATA_DIR)
    rommlib.append_gamelist({"system": "nes", "paths": ["/r/nes/A.zip"]})
    runs = []
    monkeypatch.setattr(rb_finalize, "drain", lambda retry_deferred=True: runs.append(1) or False)
    rb_finalize.main()
    assert runs == [1]


# ------------------------------------------------------------ round 2

def _es(esapi):
    """rb_finalize.EsApi() hands this fake out (EsApi.same still there)."""
    def make(base=None):
        return esapi
    make.same = rb_finalize.EsApi.same
    return make


# --- a download waiting for its conversion, and the conversion's rename

def _ps3_waiting(monkeypatch):
    """A PS3 .zip download queued while its conversion to Name.ps3 waits."""
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    sysdir = os.path.join(rommlib.ROMS_ROOT, "ps3")
    os.makedirs(sysdir)
    z, folder = os.path.join(sysdir, "Lorem.zip"), os.path.join(sysdir, "Lorem.ps3")
    open(z, "wb").close()
    os.makedirs(rommlib.CHD_QUEUE_DIR)
    task = os.path.join(rommlib.CHD_QUEUE_DIR, "0001.json")
    with open(task, "w") as f:
        json.dump({"mode": "ps3", "system": "ps3", "sources": [z]}, f)
    es = FakeEsApi({})
    es.reload = lambda: None
    es.add_games = lambda s, g: 204
    monkeypatch.setattr(rb_finalize, "EsApi", _es(es))
    monkeypatch.setattr(rb_finalize, "game_running", lambda: False)
    monkeypatch.setattr(rb_finalize, "wait_idle", lambda *a: True)
    monkeypatch.setattr(rb_finalize, "wait_listed", lambda *a, **k: True)
    monkeypatch.setattr(rommlib, "load_es_systems", lambda: {"ps3": {".ps3"}})

    def converted():
        os.remove(z)
        os.makedirs(folder)
        os.remove(task)
        es.g[folder] = {"id": "p1", "path": folder, "name": "Lorem"}
    return es, z, folder, converted


def test_a_download_waiting_for_its_conversion_gets_its_info_when_the_rename_comes_later_in_the_run(
        isolated, monkeypatch):
    es, z, folder, converted = _ps3_waiting(monkeypatch)
    rommlib.append_gamelist({"system": "ps3", "paths": [z], "name": "Lorem", "desc": "Ipsum"})
    real, n = rb_finalize.process, [0]

    def process(entries, *a, **k):
        n[0] += 1
        out = real(entries, *a, **k)
        if n[0] == 1:                         # the conversion finishes during the first pass
            converted()
            rommlib.append_gamelist({"system": "ps3", "rename": {z: folder}, "hide": []})
        return out
    monkeypatch.setattr(rb_finalize, "process", process)
    rb_finalize.main()
    assert {"desc": "Ipsum"} in es.meta
    assert not os.path.exists(rb_finalize.deferred_path())


def test_a_download_whose_files_were_converted_follows_its_install_record(isolated, monkeypatch):
    es, z, folder, converted = _ps3_waiting(monkeypatch)
    converted()                               # and its rename went to ES in an earlier run
    rommlib.Library().update(lambda inst: inst.update({"7": {"system": "ps3", "paths": [folder], "name": "Lorem",
                                                              "fs_name": "Lorem.zip"}}))
    e = {"system": "ps3", "paths": [z], "rom_id": 7, "name": "Lorem", "desc": "Ipsum"}
    assert rb_finalize.process([e], None, {"ps3": {".ps3"}}, es) == []
    assert es.meta and es.meta[0]["desc"] == "Ipsum" and e["fill"]


def test_an_uninstalled_downloads_entry_is_still_dropped(isolated, monkeypatch):
    es, z, folder, converted = _ps3_waiting(monkeypatch)
    os.remove(z)
    e = {"system": "ps3", "paths": [z], "rom_id": 8, "name": "Lorem"}
    assert rb_finalize.process([e], None, {"ps3": {".ps3"}}, es) == []
    assert es.meta == []


# --- isolate: one reload, RomM down stays down, ES trouble isn't the entry's fault

class _CountEs(FakeEsApi):
    def __init__(self, later):
        super().__init__({})
        self.later, self.reloads = later, 0

    def add_games(self, system, games):
        return 204

    def reload(self):
        self.reloads += 1
        self.g.update(self.later)


def test_isolating_a_batch_reloads_es_once(isolated, monkeypatch):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "nes")
    os.makedirs(sysdir)
    games, entries = {}, []
    for i in range(4):
        p = os.path.join(sysdir, "Lorem %d.zip" % i)
        open(p, "wb").close()
        games[p] = {"id": "g%d" % i, "path": p, "name": "Lorem %d" % i}
        entries.append({"system": "nes", "paths": [p], "name": "Ipsum %d" % i})
    entries += [{"system": "nes", "remove": [os.path.join(sysdir, "Old %d.zip" % i)]} for i in range(3)]
    entries.append({"system": "nes", "paths": 5})              # breaks every step: set aside
    es = _CountEs(games)
    monkeypatch.setattr(rb_finalize, "EsApi", _es(es))
    monkeypatch.setattr(rb_finalize, "wait_listed", lambda *a, **k: True)
    monkeypatch.setattr(rommlib, "load_es_systems", lambda: {"nes": {".zip"}})
    left, bad = rb_finalize.isolate(entries, None, {})
    assert es.reloads == 1
    assert left == [] and bad == [entries[-1]]
    assert sorted(m["name"] for m in es.meta) == ["Ipsum %d" % i for i in range(4)]


def test_isolated_entries_dont_ask_a_down_romm_again(serve, quick, isolated, monkeypatch):
    s = serve(lambda p, n: (502, {}, b"bad gateway"))
    api = rommlib.Api(s.url, "t")
    sysdir = os.path.join(rommlib.ROMS_ROOT, "nes")
    os.makedirs(sysdir)
    games, entries = {}, []
    for i in range(5):
        rom = json.loads(json.dumps(rom_54()).replace("/2/3/", "/2/%d/" % (100 + i)))
        rom.update(id=100 + i, name="Lorem %d" % i, fs_name="Lorem %d.zip" % i)
        p = os.path.join(sysdir, rom["fs_name"])
        open(p, "wb").close()
        games[p] = {"id": "g%d" % i, "path": p, "name": "Lorem %d" % i}
        entries.append(rommlib.gamelist_entry(rom, "nes", [p]))
    monkeypatch.setattr(rb_finalize, "EsApi", _es(FakeEsApi(dict(games))))
    monkeypatch.setattr(rommlib, "load_es_systems", lambda: {"nes": {".zip"}})
    rb_finalize.apply(entries[:1], api, {"nes": {".zip"}}, FakeEsApi(dict(games)), {}, cfg(), parallel=False)
    one = len(s.hits)                                          # what the first entry costs
    s.hits.clear()
    left, bad = rb_finalize.isolate(entries, api, cfg())
    assert len(left) == 5 and bad == []
    assert len(s.hits) <= one
    assert not rb_finalize.romm_down(api)                      # the next batch asks afresh


def test_es_not_answering_while_isolating_keeps_the_batch(isolated, monkeypatch):
    path, g = game_here()

    class Silent(FakeEsApi):
        def games(self, system, refresh=False):
            raise rb_finalize.EsError("/systems/nes/games", None, "timeout")
    monkeypatch.setattr(rb_finalize, "EsApi", _es(Silent({path: g})))
    monkeypatch.setattr(rommlib, "load_es_systems", lambda: {"nes": {".zip"}})
    e = {"system": "nes", "paths": [path], "name": "Lorem"}
    with pytest.raises(rb_finalize.EsError):                   # a batch of one too
        rb_finalize.isolate([e], None, {})
    with pytest.raises(rb_finalize.EsError):
        rb_finalize.isolate([e, dict(e)], None, {})


def test_es_not_answering_isnt_set_aside_by_drain(isolated, monkeypatch):
    os.makedirs(rommlib.DATA_DIR)
    monkeypatch.setattr(rb_finalize, "wait_idle", lambda *a: True)
    monkeypatch.setattr(rb_finalize, "game_running", lambda: False)
    monkeypatch.setattr(rb_finalize.time, "sleep", lambda s: None)
    monkeypatch.setattr(rb_finalize, "MAX_TRIES", 1)

    def process(entries, api=None, es=None, esapi=None, cfg=None, bad=None):
        raise rb_finalize.EsError("/reloadgames", None, "timeout")
    monkeypatch.setattr(rb_finalize, "process", process)
    rommlib.append_gamelist({"system": "nes", "paths": ["/r/nes/A.zip"]})
    rb_finalize._set_fails(rb_finalize.ISOLATE_AFTER)
    work = rommlib.GAMELIST_QUEUE + ".work"
    open(work, "w").write('{"system": "nes", "paths": ["/r/nes/B.zip"]}\n')
    assert rb_finalize.drain() is False
    assert os.path.exists(work) and not os.path.exists(work + ".bad")


def test_wait_listed_rides_out_es_not_answering(isolated, monkeypatch):
    path, g = game_here()
    monkeypatch.setattr(rb_finalize.time, "sleep", lambda s: None)
    calls = []

    class Busy(FakeEsApi):
        def games(self, system, refresh=False):
            calls.append(1)
            if len(calls) == 1:
                raise rb_finalize.EsError("/systems/nes/games", None, "timeout")
            return self.g
    e = {"system": "nes", "paths": [path]}
    assert rb_finalize.wait_listed(Busy({path: g}), [e], {"nes": {".zip"}}, settle=0) is True
    assert len(calls) == 2


# --- RomM's other answers

@pytest.mark.parametrize("status", [400, 405, 422])
def test_another_4xx_is_romms_answer(serve, quick, isolated, status):
    s = serve(lambda p, n: (status, {}, b"no"))
    api = rommlib.Api(s.url, "t")
    info = {}
    assert rb_finalize.fetch_bytes(api, "/a.png", info=info) is None
    assert not info.get("retry") and not rb_finalize.romm_down(api)


def test_a_bad_path_doesnt_mark_romm_down(isolated):
    import requests
    api = rommlib.Api("http://127.0.0.1:1", "t")

    def get(*a, **k):
        raise requests.exceptions.InvalidURL("bad")
    api.s.get = get
    info = {}
    assert rb_finalize.fetch_bytes(api, "/a.png", info=info) is None
    assert not info.get("retry") and not rb_finalize.romm_down(api)

    def timeout(*a, **k):
        raise requests.ConnectTimeout("slow")
    api.s.get = timeout
    assert rb_finalize.fetch_bytes(api, "/b.png", info=info) is None
    assert info.get("retry") and rb_finalize.romm_down(api)
    rb_finalize._ROMM_DOWN.clear()


def test_an_error_page_isnt_uploaded_as_an_image(served):
    files, _ = served
    files["/a.png"] = b"<html><body>Sign in</body></html>"
    info = {}
    assert rb_finalize.fetch_media(object(), "image", "/a.png", info=info) is None
    assert not info.get("retry")                               # noted: it isn't media
    files["/b.png"] = PNG
    assert rb_finalize.fetch_media(object(), "image", "/b.png") == (PNG, "image/png")


# --- a retry for media only

def test_a_retry_for_media_doesnt_undo_edits_made_in_es(served, monkeypatch):
    files, _ = served
    rom = rom_54()
    files.update(all_files(rom))
    real = rb_finalize.fetch_bytes

    def down(api, path, limit=200 << 20, info=None):
        if info is not None:
            info["retry"] = "ConnectionError"
        return None
    monkeypatch.setattr(rb_finalize, "fetch_bytes", down)
    path, g = game_here()
    esapi = FakeEsApi({path: g})
    e = rommlib.gamelist_entry(rom, "nes", [path])
    assert rb_finalize.apply([e], None, {"nes": {".zip"}}, esapi, {}, cfg()) == [e]
    assert e.get("fill")
    g["name"], g["desc"] = "My own name", "My own words"      # edited in ES meanwhile
    e = json.loads(json.dumps(e))                              # as deferred keeps it
    monkeypatch.setattr(rb_finalize, "fetch_bytes", real)
    assert rb_finalize.apply([e], None, {"nes": {".zip"}}, esapi, {}, cfg()) == []
    assert g["name"] == "My own name" and g["desc"] == "My own words"
    assert {t for t, _, _ in esapi.media} >= {"image", "thumbnail"}


# --- a dotted folder isn't a game folder

def test_a_dotted_folder_isnt_a_game_folder_when_es_doesnt_say(isolated, monkeypatch):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    dotted, game = os.path.join(sysdir, "Dr. Lorem (USA)"), os.path.join(sysdir, "Lorem Quest.ps3")
    os.makedirs(dotted)
    os.makedirs(game)
    monkeypatch.setattr(rommlib, "primary_path", lambda *a: "its file")
    assert rb_finalize.primary_of("psx", [dotted], {}) == "its file"
    assert rb_finalize.primary_of("ps3", [game], {}) == game


# --- the failed-passes count belongs to a .work

def test_a_count_left_without_its_batch_doesnt_isolate_the_next(draining, monkeypatch):
    rb_finalize._set_fails(5)                    # a kill after .work went, before the count did
    went = []
    monkeypatch.setattr(rb_finalize, "isolate", lambda *a: went.append(1) or ([], []))
    rommlib.append_gamelist({"system": "nes", "paths": ["/r/nes/New.zip"]})
    assert rb_finalize.drain() is True
    assert went == [] and draining == ["/r/nes/New.zip"]
    assert not os.path.exists(rb_finalize.fails_path())


# ------------------------------------------------------------ round 3: what a batch costs

class _Es(FakeEsApi):
    """ES with its requests counted: games() is a game-list fetch, add_games lists what it
    is given (at status 200), reload lists `later`."""

    def __init__(self, games=None, later=None, status=200, systems=("nes", "psx")):
        super().__init__(dict(games or {}))
        self.later, self.status, self.systems = later, status, systems
        self.fetches = self.reloads = self.posts = 0

    def games(self, system, refresh=False):
        self.fetches += 1
        return dict(self.g) if system in self.systems else None

    def add_games(self, system, games):
        self.posts += 1
        if self.status == 200:
            for p, tags in games.items():
                self.g[p] = {"id": "n%d" % len(self.g), "path": p, "name": _stem(p)}
        return self.status

    def reload(self):
        self.reloads += 1
        if self.later is not None:
            self.g = dict(self.later)


def _stem(p):
    return os.path.splitext(os.path.basename(p))[0]


@pytest.fixture
def clock(monkeypatch):
    """Simulated time: sleeping moves it on at once. [now]."""
    t = [1e9]
    monkeypatch.setattr(rb_finalize.time, "sleep", lambda s: t.__setitem__(0, t[0] + s))
    monkeypatch.setattr(rb_finalize.time, "time", lambda: t[0])
    return t


def _downloads(n, system="nes", ext=".zip"):
    sysdir = os.path.join(rommlib.ROMS_ROOT, system)
    os.makedirs(sysdir, exist_ok=True)
    games, entries = {}, []
    for i in range(n):
        p = os.path.join(sysdir, "Lorem %d%s" % (i, ext))
        open(p, "wb").close()
        games[p] = {"id": "g%d" % i, "path": p, "name": "Lorem %d" % i}
        entries.append({"system": system, "paths": [p], "name": "Ipsum %d" % i, "desc": "Dolor"})
    return games, entries


@pytest.mark.parametrize("status", [200, 204])          # 204: Batocera 43.1, a full reload instead
def test_a_batch_of_50_downloads_costs_two_list_fetches_and_one_reload_at_most(isolated, clock, status):
    games, entries = _downloads(50)
    es = _Es(later=games, status=status)
    t0 = clock[0]
    assert rb_finalize.process(entries, None, {"nes": {".zip"}}, es) == []
    assert es.fetches <= 2 and es.reloads <= 1 and es.posts == 1
    assert clock[0] - t0 <= 10                                # no wait beyond the reload's settle
    assert len(es.meta) == 50


def _renames(n, listed_new):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    os.makedirs(sysdir)
    old, new, entries = {}, {}, []
    for i in range(n):
        cue, chd = os.path.join(sysdir, "Lorem %d.cue" % i), os.path.join(sysdir, "Lorem %d.chd" % i)
        open(chd, "wb").close()
        old[cue] = {"id": "c%d" % i, "path": cue, "name": "Ipsum %d" % i, "desc": "Dolor"}
        new[chd] = {"id": "d%d" % i, "path": chd, "name": "Lorem %d" % i}
        entries.append({"system": "psx", "rename": {cue: chd}, "hide": []})
    return _Es(new if listed_new else old, later=new), entries


@pytest.mark.parametrize("listed_new", [False, True])    # True: deferred, ES lists the new files already
def test_a_batch_of_20_renames_costs_two_list_fetches(isolated, clock, listed_new):
    es, entries = _renames(20, listed_new)
    assert rb_finalize.process(entries, None, {"psx": {".chd", ".cue"}}, es) == []
    assert es.fetches <= 2
    assert es.reloads == (0 if listed_new else 1)
    if not listed_new:                                       # the old files' info, carried over
        assert sum(1 for m in es.meta if m.get("desc") == "Dolor") == 20


def test_isolating_50_entries_costs_what_a_batch_does(isolated, clock, monkeypatch):
    games, entries = _downloads(50)
    es = _Es(later=games)
    monkeypatch.setattr(rb_finalize, "EsApi", _es(es))
    monkeypatch.setattr(rommlib, "load_es_systems", lambda: {"nes": {".zip"}})
    left, bad = rb_finalize.isolate(entries + [{"system": "nes", "paths": 5}], None, {})
    assert left == [] and len(bad) == 1
    assert es.fetches <= 2 and es.reloads == 0 and es.posts == 1


# --- a game ES never lists

def test_a_game_es_never_lists_costs_one_wait(isolated, clock):
    games, entries = _downloads(1)
    es = _Es(later={}, status=204)                           # ES filters the file out: never listed
    t0 = clock[0]
    left = rb_finalize.process(entries, None, {"nes": {".zip"}}, es)
    assert left == entries
    assert es.reloads == 1
    assert clock[0] - t0 < 200                               # one wait (6 + 180 s), not a second one


def test_a_system_es_doesnt_have_costs_no_request_and_no_wait(isolated, clock):
    games, entries = _downloads(2, system="ps3x", ext=".iso")
    es = _Es(systems=("nes",))
    t0 = clock[0]
    left = rb_finalize.process(entries, None, {"nes": {".zip"}}, es)
    assert left == entries                                   # kept, its tries spent as before
    assert (es.fetches, es.reloads, es.posts) == (0, 0, 0) and clock[0] == t0
    assert open(rommlib.LOG_PATH).read().count("no ps3x system") == 1
    os.remove(entries[0]["paths"][0])                        # files gone: nothing to keep
    assert rb_finalize.process(entries, None, {"nes": {".zip"}}, es) == entries[1:]


def test_wait_listed_says_whether_es_was_still_loading(isolated, clock):
    games, entries = _downloads(2)

    class Es(_Es):
        def games(self, system, refresh=False):
            if self.fetches == 3:                            # one more game listed part way
                self.g.update(list(games.items())[:1])
            return super().games(system, refresh)
    out = rb_finalize.wait_listed(Es(), entries, {"nes": {".zip"}})
    assert not out and out.loading
    out = rb_finalize.wait_listed(_Es(), entries, {"nes": {".zip"}})      # listed, never more
    assert not out and not out.loading
    out = rb_finalize.wait_listed(_Es(systems=()), entries, {"nes": {".zip"}})   # the system not yet
    assert not out and out.loading


# --- failed passes: only the batch's own failures count

def test_es_not_answering_doesnt_count_as_a_failed_pass(isolated, monkeypatch):
    os.makedirs(rommlib.DATA_DIR)
    monkeypatch.setattr(rb_finalize, "wait_idle", lambda *a: True)
    monkeypatch.setattr(rb_finalize, "game_running", lambda: False)
    monkeypatch.setattr(rb_finalize.time, "sleep", lambda s: None)

    def process(entries, api=None, es=None, esapi=None, cfg=None, bad=None):
        raise rb_finalize.EsError("/reloadgames", None, "timeout")
    monkeypatch.setattr(rb_finalize, "process", process)
    rommlib.append_gamelist({"system": "nes", "paths": ["/r/nes/A.zip"]})
    assert rb_finalize.drain() is False
    assert rb_finalize._fails() == 0 and os.path.exists(rommlib.GAMELIST_QUEUE + ".work")


def test_a_full_disk_after_the_batch_doesnt_count_as_a_failed_pass(isolated, monkeypatch):
    os.makedirs(rommlib.DATA_DIR)
    monkeypatch.setattr(rb_finalize, "wait_idle", lambda *a: True)
    monkeypatch.setattr(rb_finalize, "game_running", lambda: False)
    monkeypatch.setattr(rb_finalize.time, "sleep", lambda s: None)
    monkeypatch.setattr(rb_finalize, "process", lambda entries, *a, **k: list(entries))
    real = rommlib.atomic_write_text

    def full(path, text):
        if path.endswith((".done", "deferred.jsonl")):
            raise OSError(28, "No space left on device")
        return real(path, text)
    rommlib.append_gamelist({"system": "nes", "paths": ["/r/nes/A.zip"]})
    with monkeypatch.context() as m:
        m.setattr(rommlib, "atomic_write_text", full)
        assert rb_finalize.drain() is False
    assert rb_finalize._fails() == 0 and os.path.exists(rommlib.GAMELIST_QUEUE + ".work")
    assert rb_finalize.drain() is True                       # room again: kept for later, once
    assert [e["paths"] for e in rb_finalize._read_jsonl(rb_finalize.deferred_path())] == [["/r/nes/A.zip"]]


# --- a kill at any step

class _Kill(BaseException):
    """A kill: no handler catches it."""


def _queue_state():
    q = rommlib.GAMELIST_QUEUE
    deferred = sorted(e["paths"][0] for e in rb_finalize._read_jsonl(rb_finalize.deferred_path()))
    bad = open(q + ".work.bad").read().count("Poison") if os.path.exists(q + ".work.bad") else 0
    return {"deferred": deferred, "bad": bad,
            "left over": [p for p in (q, q + ".work", rb_finalize.done_path()) if os.path.exists(p)]}


@pytest.mark.parametrize("isolating", [False, True])
def test_a_kill_at_any_step_loses_nothing_and_adds_nothing(isolated, monkeypatch, isolating):
    os.makedirs(rommlib.DATA_DIR)
    monkeypatch.setattr(rb_finalize, "wait_idle", lambda *a: True)
    monkeypatch.setattr(rb_finalize, "game_running", lambda: False)
    monkeypatch.setattr(rb_finalize.time, "sleep", lambda s: None)
    handed = []

    def process(entries, api=None, es=None, esapi=None, cfg=None, bad=None):
        left = []
        for e in entries:
            if e.get("poison") and bad is not None:
                bad.append(e)
            elif e.get("keep") or e.get("poison"):
                left.append(e)
            else:
                handed.append(e["paths"][0])
        return left
    monkeypatch.setattr(rb_finalize, "process", process)
    q = rommlib.GAMELIST_QUEUE

    def start():
        for p in (q, q + ".work", q + ".work.bad", q + ".work.fails", rb_finalize.done_path(),
                  rb_finalize.deferred_path()):
            if os.path.exists(p):
                os.remove(p)
        rommlib.append_gamelist({"system": "nes", "paths": ["/r/nes/A.zip"]},
                                {"system": "nes", "paths": ["/r/nes/B.zip"], "keep": True})
        rb_finalize._write_jsonl(rb_finalize.deferred_path(),
                                 [{"system": "nes", "paths": ["/r/nes/C.zip"], "keep": True}])
        if isolating:                                        # a .work that failed: one at a time
            with open(q + ".work", "w") as f:
                f.write(json.dumps({"system": "nes", "paths": ["/r/nes/Poison.zip"], "poison": True}) + "\n")
            rb_finalize._set_fails(rb_finalize.ISOLATE_AFTER)
        handed.clear()
    real_write, real_remove, real_append = rommlib.atomic_write_text, os.remove, rommlib.append_gamelist

    def run(kill_at):
        """drain() killed at its kill_at'th file change (before it, or just after); the steps."""
        n = [0]

        def step(fn):
            def wrapped(*a, **k):
                n[0] += 1
                if kill_at == (n[0], "before"):
                    raise _Kill()
                out = fn(*a, **k)
                if kill_at == (n[0], "after"):
                    raise _Kill()
                return out
            return wrapped
        with monkeypatch.context() as m:
            m.setattr(rommlib, "atomic_write_text", step(real_write))
            m.setattr(rommlib, "append_gamelist", step(real_append))
            m.setattr(os, "remove", step(real_remove))
            try:
                rb_finalize.drain()
            except _Kill:
                return None
        return n[0]
    start()
    steps = run(None)
    clean = _queue_state()
    assert clean == {"deferred": ["/r/nes/B.zip", "/r/nes/C.zip"], "bad": int(isolating), "left over": []}
    assert steps and steps > 5
    for i in range(1, steps + 1):
        for when in ("before", "after"):
            start()
            assert run((i, when)) is None
            assert rb_finalize.drain() is True                       # the next run
            got = _queue_state()
            assert got == clean, "killed %s step %d" % (when, i)
            assert "/r/nes/A.zip" in handed, "killed %s step %d" % (when, i)
            assert rb_finalize.drain() is True and _queue_state() == clean   # and no growth after


# --- the install record after a switch to another RomM server

def test_a_download_doesnt_follow_another_servers_game_with_the_same_id(isolated, monkeypatch):
    es, z, folder, converted = _ps3_waiting(monkeypatch)
    converted()
    rommlib.Library().update(lambda inst: inst.update({"7": {"system": "ps3", "paths": [folder],
                                                              "name": "Another Game", "fs_name": "Another.zip"}}))
    e = {"system": "ps3", "paths": [z], "rom_id": 7, "name": "Lorem", "desc": "Ipsum"}
    assert rb_finalize.process([e], None, {"ps3": {".ps3"}}, es) == []
    assert es.meta == []


# --- RomM behind a login that redirects in a loop

def test_a_redirect_loop_is_romm_down_not_an_answer(isolated):
    import requests
    api = rommlib.Api("http://127.0.0.1:1", "t")
    for ex in (requests.TooManyRedirects("loop"), requests.exceptions.ProxyError("proxy"),
               requests.exceptions.SSLError("tls")):
        def get(*a, ex=ex, **k):
            raise ex
        api.s.get = get
        info = {}
        assert rb_finalize.fetch_bytes(api, "/a.png", info=info) is None
        assert info.get("retry") and rb_finalize.romm_down(api)
        rb_finalize._ROMM_DOWN.clear()


# --- media ES didn't store

def test_media_es_refused_to_store_is_kept_for_later(served):
    files, _ = served
    rom = rom_54()
    files.update(all_files(rom))
    path, g = game_here()

    class Full(FakeEsApi):
        def set_media(self, *a):
            return False                                     # a full roms disk
    e = rommlib.gamelist_entry(rom, "nes", [path])
    assert rb_finalize.apply([e], object(), {"nes": {".zip"}}, Full({path: g}), {}, cfg()) == [e]
    assert e["_left"] == "media" and e["fill"]
    assert rommlib.media_skips() == {}


# --- waits and media tries are bounded by time

def test_a_conversion_that_never_ends_stops_holding_the_game(isolated, monkeypatch, clock):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "ps3")
    os.makedirs(sysdir)
    z = os.path.join(sysdir, "Lorem Quest.zip")
    open(z, "wb").close()
    os.makedirs(rommlib.CHD_QUEUE_DIR)
    with open(os.path.join(rommlib.CHD_QUEUE_DIR, "0001.json"), "w") as f:
        json.dump({"mode": "ps3", "system": "ps3", "sources": [z]}, f)
    e = {"system": "ps3", "paths": [z], "name": "Lorem Quest"}
    for _ in range(rb_finalize.WAIT_RUNS + 3):              # many runs, the same day: still waits
        assert rb_finalize.process([e], None, {"ps3": {".ps3"}}, FakeEsApi({})) == [e] and e.get("_wait")
    clock[0] += rb_finalize.WAIT_DAYS * 86400 + 1            # days on, more runs than WAIT_RUNS
    assert rb_finalize.process([e], None, {"ps3": {".ps3"}}, FakeEsApi({})) == [e]
    assert not e.get("_wait")                                # a try spent from now on
    assert rb_finalize._spend([e], clock[0])[0]["_tries"] == 1


def test_media_tries_are_spent_by_the_hour_not_by_the_run():
    e = {"system": "nes", "paths": ["/r/nes/A.zip"]}
    now = 1e9
    for i in range(10):                                      # an evening of runs, RomM offline
        e["_media"] = True
        assert rb_finalize._spend([e], now + 60 * i) == [e]
    assert e["_tries"] == 1
    e["_media"] = True
    rb_finalize._spend([e], now + rb_finalize.MEDIA_TRY_GAP)
    assert e["_tries"] == 2
    rb_finalize._spend([e], now + rb_finalize.MEDIA_TRY_GAP + 1)    # not for media: a try a run
    assert e["_tries"] == 3
