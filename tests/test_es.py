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
