"""The EmulationStation client (romm/es.py) and what the workers do with
ES's API: /addgames instead of a full reload, /isIdle as the gate for
background work, /messagebox for problems that need the player."""
import os
import socket
import threading

import pytest

import rommlib


@pytest.fixture
def es(isolated, fake_es, monkeypatch):
    """A fake ES the app side (rommlib.ES_API) and the workers both talk to."""
    e = fake_es({"gba": {".zip"}})
    monkeypatch.setattr(rommlib, "ES_API", e.url)
    return e


def _silent_server():
    """A port that accepts and never answers (ES hung)."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(4)
    held = []
    threading.Thread(target=lambda: held.append(s.accept()), daemon=True).start()
    return s, "http://127.0.0.1:%d" % s.getsockname()[1]


# ------------------------------------------------------------ the client

def test_client_reads_json_and_text(es):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "gba")
    os.makedirs(sysdir)
    open(os.path.join(sysdir, "Lorem.zip"), "w").close()
    es.reload()
    c = rommlib.EsClient()
    assert [g["name"] for g in c.json("/systems/gba/games")] == ["Lorem"]
    r = c.get("/runningGame")
    assert r.status == 201 and r.ok and r.json() == {"msg": "NO GAME RUNNING"}
    assert "NO GAME RUNNING" in c.text("/runningGame")


def test_client_errors_have_one_shape(es):
    c = rommlib.EsClient()
    with pytest.raises(rommlib.EsError) as e:
        c.get("/no/such/thing")
    assert e.value.status == 404 and isinstance(e.value, rommlib.RommError)
    r = c.get("/no/such/thing", errors=False)        # handed back instead
    assert r.status == 404 and not r.ok and r.text == "404 not found"
    with pytest.raises(rommlib.EsError) as e:
        rommlib.EsClient("http://127.0.0.1:9").get("/runningGame")    # nothing listens
    assert e.value.status is None and "didn't answer" in str(e.value)
    with pytest.raises(rommlib.EsError) as e:
        rommlib.EsReply("/x", 200, b"<html>").json()
    assert e.value.reason == "not JSON"


def test_client_times_out(isolated):
    s, url = _silent_server()
    try:
        with pytest.raises(rommlib.EsError) as e:
            rommlib.EsClient(url, timeout=0.3).get("/runningGame")
        assert e.value.status is None
    finally:
        s.close()


def test_old_names_still_work(es, monkeypatch):
    """es_get / es_notify / es_running_game keep their shapes (workers and an
    older app call them)."""
    assert rommlib.es_get("/runningGame") == (201, b'{"msg": "NO GAME RUNNING"}')
    assert rommlib.es_get("/runningGame", base=es.url)[0] == 201
    with pytest.raises(Exception):
        rommlib.es_get("/nothing")
    assert rommlib.es_running_game() == ""
    es.running = "/userdata/roms/gba/Lorem.zip"
    assert rommlib.es_running_game() == "/userdata/roms/gba/Lorem.zip"
    assert rommlib.es_notify("Lorem ipsum ✓") is True
    assert es.notes == ["Lorem ipsum ✓"]
    assert rommlib.es_notify("") is False                 # ES: 400, body is missing
    monkeypatch.setattr(rommlib, "ES_API", "http://127.0.0.1:9")
    assert rommlib.es_notify("x") is False
    assert rommlib.es_running_game() is None
    assert rommlib.es_counts() == {}
    with pytest.raises(rommlib.RommError, match="didn't answer"):
        rommlib.es_game_list()


# ------------------------------------------------------------ /addgames (idea 28)

def _rom(system, name):
    p = os.path.join(rommlib.ROMS_ROOT, system, name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").close()
    return p


def _reloads(es):
    return [c for c in es.calls if c == ("GET", "/reloadgames")]


def test_download_is_added_without_a_full_reload(es):
    import rb_finalize
    _rom("gba", "Old.zip")
    es.reload()
    rom = _rom("gba", "Lorem & Ipsum (World).zip")
    left = rb_finalize.process([{"system": "gba", "paths": [rom], "name": "Lorem & Ipsum", "desc": "Dolor",
                                 "players": "2"}], None, {"gba": {".zip"}})
    assert left == []
    assert ("POST", "/addgames/gba") in es.calls and not _reloads(es)
    g = es.game("gba", "Lorem & Ipsum (World).zip")
    assert g["name"] == "Lorem & Ipsum" and g["desc"] == "Dolor" and g["players"] == "2"


def test_batocera_43_1_addgames_falls_back_to_a_reload(es):
    """Batocera 43.1's ES answers 204 to every /addgames body."""
    import rb_finalize
    _rom("gba", "Old.zip")
    es.reload()
    es.addgames_bug = True
    rom = _rom("gba", "Sit Amet (World).zip")
    assert rb_finalize.process([{"system": "gba", "paths": [rom], "name": "Sit Amet"}], None, {"gba": {".zip"}}) == []
    assert ("POST", "/addgames/gba") in es.calls and len(_reloads(es)) == 1
    assert es.game("gba", "Sit Amet (World).zip")["name"] == "Sit Amet"


def test_unreachable_addgames_falls_back_to_a_reload(es, monkeypatch):
    import rb_finalize
    _rom("gba", "Old.zip")
    es.reload()
    monkeypatch.setattr(rb_finalize.EsApi, "add_games", lambda self, system, body: None)
    rom = _rom("gba", "Consectetur (World).zip")
    assert rb_finalize.process([{"system": "gba", "paths": [rom], "name": "Consectetur"}], None,
                               {"gba": {".zip"}}) == []
    assert len(_reloads(es)) == 1 and es.game("gba", "Consectetur (World).zip")["name"] == "Consectetur"


def test_first_game_of_a_system_es_reloads_by_itself(isolated, fake_es, monkeypatch):
    """A system with no games yet: ES loads it, answers 201 and reloads every
    list itself — we don't ask for another reload."""
    import rb_finalize
    es = fake_es({"gba": {".zip"}, "nes": {".zip"}})
    _rom("gba", "Old.zip")
    es.reload()
    rom = _rom("nes", "Adipiscing.zip")
    assert rb_finalize.process([{"system": "nes", "paths": [rom], "name": "Adipiscing", "desc": "Elit"}], None,
                               {"nes": {".zip"}}) == []
    assert ("POST", "/addgames/nes") in es.calls and not _reloads(es)
    assert es.game("nes", "Adipiscing.zip")["desc"] == "Elit"
    assert es.game("gba", "Old.zip") is not None


def test_multi_disc_set_is_added_with_its_discs_hidden(isolated, fake_es):
    import rb_finalize
    exts = {"psx": {".chd", ".m3u"}}
    es = fake_es(exts)
    _rom("psx", "Old.chd")
    es.reload()
    d1, d2 = _rom("psx", "Sed/Sed (Disc 1).chd"), _rom("psx", "Sed/Sed (Disc 2).chd")
    m3u = _rom("psx", "Sed.m3u")
    assert rb_finalize.process([{"system": "psx", "paths": [m3u], "name": "Sed"},
                                {"system": "psx", "hide": [d1, d2]}], None, exts) == []
    assert not _reloads(es)
    assert es.game("psx", "Sed.m3u")["name"] == "Sed" and not es.game("psx", "Sed.m3u").get("hidden")
    assert es.game("psx", "Sed (Disc 1).chd")["hidden"] == "true"
    assert es.game("psx", "Sed (Disc 2).chd")["hidden"] == "true"


def test_listed_games_are_not_sent_again(es):
    """A game ES already lists isn't re-added (that would reset what ES keeps
    beyond our tags), and nothing new means no reload either."""
    import rb_finalize
    rom = _rom("gba", "Do.zip")
    es.reload()
    assert rb_finalize.process([{"system": "gba", "paths": [rom], "name": "Do", "fill": True}], None,
                               {"gba": {".zip"}}) == []
    assert not [c for c in es.calls if c[1].startswith("/addgames")] and not _reloads(es)
    assert es.game("gba", "Do.zip")["name"] == "Do"


def test_renames_and_removals_still_reload(es):
    """ES must also forget the old files: /addgames can't do that."""
    import rb_finalize
    _rom("gba", "Old.zip")
    es.reload()
    rom = _rom("gba", "Eiusmod (World).zip")
    rb_finalize.process([{"system": "gba", "paths": [rom], "name": "Eiusmod"},
                         {"system": "gba", "remove": [os.path.join(rommlib.ROMS_ROOT, "gba", "Gone.zip")]}],
                        None, {"gba": {".zip"}})
    assert not [c for c in es.calls if c[1].startswith("/addgames")] and len(_reloads(es)) == 1
    assert es.game("gba", "Eiusmod (World).zip")["name"] == "Eiusmod"


def test_app_side_add_games(es, monkeypatch):
    _rom("gba", "Old.zip")
    es.reload()
    rom = _rom("gba", "Tempor <Beta>.zip")
    assert b"<path>./Tempor &lt;Beta&gt;.zip</path>" in rommlib.gamelist_xml("gba", {rom: {"name": "T & U"}})
    assert rommlib.es_add_games("gba", {rom: {"name": "T & U"}}) == 200
    assert es.game("gba", "Tempor <Beta>.zip")["name"] == "T & U"
    assert rommlib.es_add_games("gba", {os.path.join(rommlib.ROMS_ROOT, "gba", "Absent.zip"): {}}) == 204
    assert rommlib.es_add_games("nope", {rom: {}}) == 404
    es.addgames_bug = True
    assert rommlib.es_add_games("gba", {rom: {}}) == 204
    monkeypatch.setattr(rommlib, "ES_API", "http://127.0.0.1:9")
    assert rommlib.es_add_games("gba", {rom: {}}) is None


# ------------------------------------------------------------ /isIdle (idea 29)

APP = "/userdata/roms/ports/romm-o-cera/rommbrowser.pygame"


def test_is_idle_as_the_real_es_reports_it(es, monkeypatch):
    assert rommlib.es_is_idle() is True and rommlib.es_busy() == ""
    es.busy = True                                  # scraping, hashing, installing, updating
    assert rommlib.es_is_idle() is False and rommlib.es_busy() == "es"
    es.busy = False
    es.running = "/userdata/roms/gba/Lorem.zip"
    assert rommlib.es_is_idle() is False and rommlib.es_busy() == "game"
    es.running = APP                                # only this app: idle unless app_busy
    assert rommlib.es_busy() == "" and rommlib.es_busy(app_busy=True) == "game"
    es.running = None
    es.has_isidle = False                           # before Batocera 36: 404
    assert rommlib.es_is_idle() is None
    es.busy = True                                  # ...so nothing tells us; the running game decides
    assert rommlib.es_busy() == ""
    es.running = "/userdata/roms/gba/Lorem.zip"
    assert rommlib.es_busy() == "game"
    monkeypatch.setattr(rommlib, "ES_API", "http://127.0.0.1:9")
    assert rommlib.es_is_idle() is None and rommlib.es_busy() == "game"


@pytest.mark.parametrize("body", [b"[]", b'"x"', b"[1]", b"null", b"<html>"])
def test_odd_is_idle_replies_count_as_unknown(isolated, body):
    assert rommlib.es_is_idle(get=lambda path: (200, body)) is None


def test_workers_wait_while_es_is_busy(es):
    """rb_finalize (the app counts as busy) and rb_chd (the browser alone
    counts as idle) wait while ES itself is busy; without /isIdle they fall
    back to the running game."""
    import rb_chd
    import rb_finalize
    assert rommlib.es_busy(app_busy=True) == "" and rb_finalize.game_running() is False and rb_chd.busy_reason() == ""
    es.busy = True
    assert rommlib.es_busy(app_busy=True) == "es" and rb_finalize.game_running() is True and rb_chd.busy_reason() == "es"
    es.busy = False
    es.running = APP
    assert rb_finalize.game_running() is True and rb_chd.busy_reason() == ""
    es.running = "/userdata/roms/gba/Lorem.zip"
    assert rommlib.es_busy(app_busy=True) == "game" and rb_chd.busy_reason() == "game"
    es.running = None
    es.has_isidle = False
    es.busy = True                                  # unseen without /isIdle
    assert rb_finalize.game_running() is False and rb_chd.busy_reason() == ""
    assert [c for c in es.calls if c[1] == "/isIdle"]


def test_workers_treat_unreachable_es_as_busy(isolated, monkeypatch):
    import rb_chd
    import rb_finalize
    monkeypatch.setattr(rb_finalize.rommlib, "ES_API", "http://127.0.0.1:9")
    assert rommlib.es_is_idle() is None and rb_finalize.game_running() is True
    assert rb_chd.busy_reason() == "game"


def test_conversion_pauses_while_es_is_busy(es, monkeypatch):
    """The in-process converters' pause (rb_chd._idle) holds while ES scrapes."""
    import rb_chd
    es.busy = True
    steps = []
    monkeypatch.setattr(rb_chd, "status", lambda **kw: steps.append(kw.get("step")))

    def sleep(s):
        es.busy = False                              # the scrape ends
    monkeypatch.setattr(rb_chd.time, "sleep", sleep)
    rb_chd._idle()
    assert [s for s in steps if s] == ["Paused while EmulationStation is busy"]


# ------------------------------------------------------------ /messagebox (idea 37)

def test_app_side_messagebox(es, monkeypatch):
    assert rommlib.es_messagebox("Lorem ipsum") is True and es.messages == ["Lorem ipsum"]
    assert rommlib.es_messagebox("") is False                  # ES: 400, body is missing
    monkeypatch.setattr(rommlib, "ES_API", "http://127.0.0.1:9")
    assert rommlib.es_messagebox("x") is False


def _jobs(*specs):
    class D:
        jobs = []
    for i, (state, message, short) in enumerate(specs):
        j = rommlib.Job({"id": i, "name": "Lorem %d" % i}, "gba")
        j.state, j.message, j.short_space = state, message, short
        D.jobs.append(j)
    return D


def test_background_downloads_waiting_for_space_get_one_message_box(es):
    import rb_dl
    n = rb_dl.Notes({"notify": False})                         # shown even with popups off
    d = _jobs(("Queued", "Waiting for space: free 3.0 GB more", 3 << 30), ("Queued", "", 0))
    es.running = "/userdata/roms/gba/Other.zip"
    n.collect(d)
    n.flush()
    assert es.messages == []                                    # not over a game
    es.running = None
    n.collect(d)
    n.flush()
    assert len(es.messages) == 1 and "waiting for disk space" in es.messages[0] and "3.0 GB" in es.messages[0]
    n.collect(d)
    n.flush()
    assert len(es.messages) == 1 and es.notes == []             # once per run; no popup with notify off


def test_space_freed_before_anyone_saw_it_shows_nothing(es):
    import rb_dl
    n = rb_dl.Notes({"notify": True})
    d = _jobs(("Queued", "Waiting for space", 1 << 30))
    es.running = "/userdata/roms/gba/Other.zip"
    n.collect(d)
    n.flush()
    d.jobs[0].short_space = 0
    es.running = None
    n.collect(d)
    n.flush()
    assert es.messages == []


def test_refused_token_gets_a_message_box_and_the_popup(es):
    import rb_dl
    n = rb_dl.Notes({"notify": True})
    d = _jobs(("Failed", "RomM rejected the token (HTTP 401)", 0), ("Failed", "Download failed: HTTP 403", 0),
              ("Failed", "Download failed: HTTP 500", 0), ("Done", "", 0))
    n.collect(d)
    n.flush()
    assert len(es.messages) == 1 and "pair it again" in es.messages[0]
    assert es.notes == ["RomM-o-Cera: 1 game ready: Lorem 3; 3 failed: Lorem 0, Lorem 1, Lorem 2"]


def test_ordinary_failures_stay_a_popup(es):
    import rb_dl
    n = rb_dl.Notes({"notify": True})
    n.collect(_jobs(("Failed", "Download failed: HTTP 500", 0), ("Failed", "Can't reach RomM.", 0)))
    n.flush()
    assert es.messages == [] and len(es.notes) == 1


def test_message_box_retried_when_es_did_not_take_it(es, monkeypatch):
    import rb_dl
    n = rb_dl.Notes({"notify": False})
    d = _jobs(("Failed", "RomM rejected the token (HTTP 401)", 0))
    monkeypatch.setattr(rommlib, "ES_API", "http://127.0.0.1:9")
    monkeypatch.setattr(rommlib, "es_running_game", lambda: "")
    n.collect(d)
    n.flush()
    monkeypatch.setattr(rommlib, "ES_API", es.url)
    n.flush()
    assert len(es.messages) == 1


def test_every_refusal_of_the_token_gets_the_message_box():
    import rb_dl
    for m in ("RomM rejected the token (HTTP 401)", "RomM refused: the token's owner is disabled (HTTP 403)",
              "Download failed: HTTP 401", "Download failed: HTTP 403"):
        assert rb_dl._rejected(m), m
    assert not rb_dl._rejected("Download failed: HTTP 500")
