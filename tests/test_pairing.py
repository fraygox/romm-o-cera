"""Settings, Pair with RomM: swap RomM's short pairing code for a client API
token (POST /api/client-tokens/exchange, unauthenticated), against
tools/mock_romm.py."""
import json
import os
import time

import pytest
import requests

import rommlib

OLD = "rmm_" + "a" * 64


def _mint(url):
    return requests.post(url + "/mock/pair-code", timeout=5).json()["code"]


def _seen(url):
    return requests.post(url + "/mock/pair-seen", timeout=5).json()


@pytest.mark.parametrize("raw,want", [
    ("ABCD2345", "ABCD2345"), ("abcd2345", "ABCD2345"), (" abcd-2345 ", "ABCD2345"),
    ("AB CD 23 45", "ABCD2345"), ("1234-5678", "12345678"),
])
def test_code_normalised(raw, want):
    assert rommlib.normalize_pair_code(raw) == want


@pytest.mark.parametrize("raw,msg", [
    ("", "Type the pairing code"), ("   ", "Type the pairing code"),
    ("ABCD234", "8 characters"), ("ABCD23456", "8 characters"),
    ("ABCD#345", "letters and digits"), ("ABCD2345é", "letters and digits"),
])
def test_bad_code_refused_before_asking_romm(raw, msg):
    with pytest.raises(rommlib.RommError, match=msg):
        rommlib.normalize_pair_code(raw)


def test_valid_token():
    assert rommlib.valid_token("rmm_" + "0" * 64)
    for bad in (None, "", "rmm_", "abc_" + "0" * 64, "rmm_" + "0" * 600, "rmm_" + "0" * 30 + " x",
                "rmm_" + "0" * 30 + "\n", 42):
        assert not rommlib.valid_token(bad)


def test_exchange_against_mock(isolated, mock_url):
    code = _mint(mock_url)
    before = len(_seen(mock_url))
    res = rommlib.Api(mock_url, OLD).exchange_pair_code(code.lower()[:4] + "-" + code[4:])
    assert rommlib.valid_token(res["token"]) and res["token"] != OLD
    assert res["name"] == "batocera" and "roms.read" in res["scopes"]
    seen = _seen(mock_url)[before:]
    assert seen == [None]                       # no Authorization header, not even the old token
    # single use, like RomM
    with pytest.raises(rommlib.RommError, match="isn't valid or has expired"):
        rommlib.exchange_pair_code(mock_url, code)
    log = open(rommlib.LOG_PATH).read()
    assert "paired with RomM" in log and res["token"] not in log


@pytest.mark.parametrize("status,body,msg", [
    (404, {"detail": "Invalid or expired pairing code"}, "isn't valid or has expired"),
    (410, {}, "isn't valid or has expired"),
    (422, {}, "isn't valid or has expired"),
    (429, {"detail": "Too many exchange attempts"}, "Too many tries"),
    (403, {"detail": "Token owner is disabled"}, "owner is disabled"),
    (302, None, "Not a RomM API response"),
    (500, {}, "HTTP 500"),
    (200, "<html>login</html>", "Not a RomM API response"),
    (200, {"name": "x"}, "no usable token"),
    (200, {"raw_token": "secret-not-rmm"}, "no usable token"),
])
def test_exchange_errors(monkeypatch, isolated, status, body, msg):
    class R:
        status_code = status

        def json(self):
            if not isinstance(body, dict):
                raise ValueError("not json")
            return body
    monkeypatch.setattr(rommlib.requests, "post", lambda *a, **k: R())
    with pytest.raises(rommlib.RommError, match=msg) as ei:
        rommlib.exchange_pair_code("https://romm.example.com", "ABCD2345")
    assert "secret-not-rmm" not in str(ei.value)


def test_exchange_network_errors(monkeypatch, isolated):
    def boom(exc):
        def post(*a, **k):
            raise exc
        return post
    monkeypatch.setattr(rommlib.requests, "post", boom(requests.exceptions.SSLError("x")))
    with pytest.raises(rommlib.RommError, match="TLS certificate rejected"):
        rommlib.exchange_pair_code("https://romm.example.com", "ABCD2345")
    monkeypatch.setattr(rommlib.requests, "post", boom(requests.exceptions.ConnectionError("x")))
    with pytest.raises(rommlib.RommError, match="Can't reach RomM"):
        rommlib.exchange_pair_code("https://romm.example.com", "ABCD2345")
    with pytest.raises(rommlib.RommError, match="server address first"):
        rommlib.exchange_pair_code("", "ABCD2345")
    with pytest.raises(rommlib.RommError, match="user:password"):
        rommlib.exchange_pair_code("https://u:p@romm.example.com", "ABCD2345")


# ------------------------------------------------------------- Settings UI

def _app(rb, url):
    import pygame
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    with open(rommlib.CONFIG_PATH, "w") as f:
        json.dump({"url": url, "token": OLD}, f)
    pygame.init()
    app = rb.App(None, headless=True)
    app.push(rb.SettingsScreen(app))
    scr = app.stack[-1]
    scr.sel = next(i for i, it in enumerate(scr.items()) if it[0] == "pair")
    return app, scr


def _pump(app, until, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        with app.lock:
            fns, app.events_from_threads = app.events_from_threads, []
        for fn in fns:
            fn()
        if until():
            return
        time.sleep(0.02)
    raise AssertionError("timed out")


def _type(app, s):
    kb = app.stack[-1]
    kb.type_text(s)
    app.dispatch("start")


def test_settings_row_sits_after_the_token(isolated, rb, mock_url):
    app, scr = _app(rb, mock_url)
    keys = [it[0] for it in scr.items()]
    assert keys[keys.index("token") + 1] == "pair"
    assert "pair" in rb.SETTING_HELP and "pair" in scr.ACTIONS
    app.draw()


def test_pair_without_url_toasts_instead_of_keyboard(isolated, rb):
    app, scr = _app(rb, "")
    app.dispatch("ok")
    assert app.stack[-1] is scr
    assert any("server address first" in t[0] for t in app.toasts)


def test_pair_stores_token_and_reconnects(isolated, rb, mock_url):
    app, scr = _app(rb, mock_url)
    code = _mint(mock_url)
    app.dispatch("ok")
    assert isinstance(app.stack[-1], rb.TextInputScreen)
    app.draw()
    _type(app, code[:4].lower() + " " + code[4:])
    assert app.stack[-1] is scr
    _pump(app, lambda: any("Paired with RomM" in t[0] for t in app.toasts))
    tok = app.cfg["token"]
    assert tok != OLD and rommlib.valid_token(tok)
    assert json.load(open(rommlib.CONFIG_PATH))["token"] == tok
    assert any("(batocera)" in t[0] for t in app.toasts)
    assert app.api.s.headers["Authorization"] == "Bearer " + tok     # reconnected with it
    assert tok not in open(rommlib.LOG_PATH).read()
    assert not any(tok in t[0] for t in app.toasts)

    # the same code again: refused, the new token stays
    app.toasts.clear()
    app.dispatch("ok")
    _type(app, code)
    _pump(app, lambda: any("expired" in t[0] for t in app.toasts))
    assert app.toasts[-1][1] == rb.RED
    assert app.cfg["token"] == tok and json.load(open(rommlib.CONFIG_PATH))["token"] == tok


def test_malformed_code_is_caught_on_the_box(isolated, rb, mock_url):
    app, scr = _app(rb, mock_url)
    before = len(_seen(mock_url))
    app.dispatch("ok")
    _type(app, "12345")
    assert any("8 characters" in t[0] for t in app.toasts)
    assert app.cfg["token"] == OLD and len(_seen(mock_url)) == before
