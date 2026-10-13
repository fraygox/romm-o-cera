"""The first-run Welcome: when it shows (and never on an env-configured box), Later, the
server step's connection check with its certificate-error path, Advanced's Verify TLS
choices (the same cfg logic Settings cycles), and the pairing code that checks itself."""
import json
import os
import time

import pytest
import requests
import rommlib


def _app(rb, monkeypatch, cfg=None):
    import pygame
    monkeypatch.delenv("ROMM_URL", raising=False)
    monkeypatch.delenv("ROMM_TOKEN", raising=False)
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    with open(rommlib.CONFIG_PATH, "w") as f:
        json.dump(cfg or {}, f)
    pygame.init()
    app = rb.App(None, headless=True)
    app.push(rb.HomeScreen(app))
    return app


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


def _platforms(monkeypatch, fn):
    monkeypatch.setattr(rommlib.Api, "platforms", lambda self: fn(self))


def _raise(e):
    def fn(_api):
        raise e
    return fn


# ------------------------------------------------------------------ when it shows

@pytest.mark.parametrize("cfg, env, due", [
    ({}, False, True),
    ({"url": "https://romm.example.com"}, False, True),
    ({"token": "rmm_" + "a" * 30}, False, True),
    ({"url": "https://romm.example.com", "token": "rmm_" + "a" * 30}, False, False),
    ({"welcome_seen": True}, False, False),
    ({}, True, False),
])
def test_welcome_due(isolated, rb, monkeypatch, cfg, env, due):
    monkeypatch.delenv("ROMM_URL", raising=False)
    monkeypatch.delenv("ROMM_TOKEN", raising=False)
    if env:
        monkeypatch.setenv("ROMM_URL", "https://romm.example.com")
        monkeypatch.setenv("ROMM_TOKEN", "rmm_" + "b" * 30)
    assert rb.welcome_due(dict(rommlib.DEFAULT_CONFIG, **cfg)) is due


def test_start_pushes_welcome_only_when_due(isolated, rb, monkeypatch):
    app = _app(rb, monkeypatch)
    app.welcome()
    assert isinstance(app.stack[-1], rb.WelcomeScreen) and app.stack[-1].step == 0
    app.draw()
    app = _app(rb, monkeypatch, {"url": "https://romm.example.com", "token": "rmm_" + "a" * 30})
    app.welcome()
    assert len(app.stack) == 1


def test_a_rejected_token_on_the_first_fetch_opens_the_pair_step(isolated, rb, monkeypatch):
    _platforms(monkeypatch, _raise(rommlib.AuthRejected("RomM rejected the token (HTTP 401)")))
    app = _app(rb, monkeypatch, {"url": "https://romm.example.com", "token": "rmm_" + "a" * 30,
                                 "welcome_seen": True})
    app.load_platforms()
    _pump(app, lambda: app.error)
    assert isinstance(app.stack[-1], rb.WelcomeScreen) and app.stack[-1].step == 1
    app.stack[-1].later()
    app.load_platforms()                            # not the first fetch any more: no Welcome again
    app.error = None
    _pump(app, lambda: app.error)
    assert len(app.stack) == 1


def test_env_configured_box_never_sees_it_even_on_401(isolated, rb, monkeypatch):
    _platforms(monkeypatch, _raise(rommlib.AuthRejected("RomM rejected the token (HTTP 401)")))
    app = _app(rb, monkeypatch)
    monkeypatch.setenv("ROMM_URL", "https://romm.example.com")
    monkeypatch.setenv("ROMM_TOKEN", "rmm_" + "b" * 30)
    app.cfg.update(rommlib.load_config())
    app.welcome()
    app.load_platforms()
    _pump(app, lambda: app.error)
    assert len(app.stack) == 1


def test_later_goes_home_and_is_remembered(isolated, rb, monkeypatch):
    app = _app(rb, monkeypatch)
    app.welcome()
    app.dispatch("back")
    assert len(app.stack) == 1
    assert json.load(open(rommlib.CONFIG_PATH))["welcome_seen"] is True
    assert not rb.welcome_due(rommlib.load_config())


# ------------------------------------------------------------------ the server step

def _welcome(rb, monkeypatch, cfg=None, url="https://romm.example.com"):
    app = _app(rb, monkeypatch, cfg)
    app.welcome() if rb.welcome_due(app.cfg) else app.push(rb.WelcomeScreen(app))
    w = app.stack[-1]
    w.url_kb.clear()
    w.type_text(url)
    return app, w


def test_server_without_token_goes_on_to_pairing(isolated, rb, monkeypatch):
    _platforms(monkeypatch, _raise(rommlib.AuthRejected("RomM rejected the token (HTTP 401)")))
    app, w = _welcome(rb, monkeypatch, url="romm.example.com/")
    app.dispatch("start")
    _pump(app, lambda: w.step == 1)
    assert app.cfg["url"] == "https://romm.example.com"       # cleaned as Settings saves it
    app.draw()
    app.dispatch("back")                                       # B on the code: back to the address
    assert w.step == 0 and app.stack[-1] is w


def test_server_that_already_answers_skips_to_platforms(isolated, rb, monkeypatch):
    _platforms(monkeypatch, lambda api: [{"id": 1, "name": "PlayStation", "fs_slug": "psx", "rom_count": 3}])
    app, w = _welcome(rb, monkeypatch, {"token": "rmm_" + "a" * 30})
    app.dispatch("start")
    _pump(app, lambda: w.step == 2)
    _pump(app, lambda: app.loaded)
    app.draw()
    _pump(app, lambda: isinstance(app.stack[-1], rb.PlatformScreen))
    assert len(app.stack) == 2 and app.cfg["welcome_seen"] is True


def test_url_with_password_is_refused(isolated, rb, monkeypatch):
    app, w = _welcome(rb, monkeypatch, url="https://u:p@romm.example.com")
    app.dispatch("start")
    assert w.busy is None and any("user:password" in t[0] for t in app.toasts)


def test_certificate_error_shows_host_why_and_what_to_do(isolated, rb, monkeypatch):
    calls = []

    def fn(api):
        calls.append(api.s.verify)
        raise rommlib.TlsRejected("TLS certificate rejected — set verify_tls in Settings", "self-signed")
    _platforms(monkeypatch, fn)
    app, w = _welcome(rb, monkeypatch)
    app.dispatch("start")
    _pump(app, lambda: w.view == "cert")
    assert w.cert == "self-signed" and w.host() == "romm.example.com"
    assert [o[1] for o in w.cert_options()] == ["Advanced", "Try again", "Edit the address"]
    assert not w.text_entry
    app.draw()
    app.dispatch("y")                                  # Advanced first
    assert w.view == "advanced"
    app.dispatch("right")                              # verification off...
    assert app.cfg["verify_tls"] is False
    app.dispatch("back")                               # ...back to the error, then Try again
    assert w.view == "cert"
    w.err_sel = 1
    app.dispatch("ok")
    assert w.busy and w.view == "keys"
    _pump(app, lambda: w.view == "cert")
    assert calls[0] is True and calls[-1] is False     # tried again with checks off
    app.dispatch("x")                                  # Edit the address: the keyboard again
    assert w.view == "keys" and w.cert is None and w.text_entry


@pytest.mark.parametrize("msg, why", [
    ("certificate verify failed: self-signed certificate (_ssl.c:1006)", "self-signed"),
    ("certificate verify failed: unable to get local issuer certificate", "unknown"),
    ("certificate verify failed: certificate has expired", "expired"),
    ("certificate verify failed: Hostname mismatch, certificate is not valid for 'x'", "hostname"),
    ("EOF occurred in violation of protocol", "other"),
])
def test_tls_why(msg, why):
    assert rommlib.tls_why(Exception(msg)) == why


def test_ssl_error_raises_tls_rejected_with_its_kind(monkeypatch):
    def boom(*a, **k):
        raise requests.exceptions.SSLError("certificate verify failed: self-signed certificate")
    api = rommlib.Api("https://romm.example.com", "rmm_x")
    monkeypatch.setattr(api.s, "get", boom)
    with pytest.raises(rommlib.TlsRejected) as e:
        api.platforms()
    assert e.value.why == "self-signed" and "verify_tls" in str(e.value)


# ------------------------------------------------------------------ Advanced: Verify TLS certificate

def test_tls_choices_map_to_cfg_like_settings(isolated, rb, monkeypatch):
    app, w = _welcome(rb, monkeypatch, {"verify_tls_ca": "/userdata/ca.pem"})
    app.dispatch("y")
    assert w.view == "advanced" and not w.text_entry
    app.draw()
    seen = []
    for _ in range(3):
        app.dispatch("right")
        seen.append(app.cfg["verify_tls"])
    assert seen == [False, "/userdata/ca.pem", True]
    assert json.load(open(rommlib.CONFIG_PATH))["verify_tls"] is True
    app.dispatch("right")
    assert any(t[0] == rb.TLS_OFF_WARNING for t in app.toasts)      # the warning Settings gives
    w.tls_choice(2)
    assert app.cfg["verify_tls"] == "/userdata/ca.pem"
    app.draw()
    w.tls_choice(0)
    assert app.cfg["verify_tls"] is True and app.cfg["verify_tls_ca"] == "/userdata/ca.pem"


def test_ca_bundle_needs_one_in_config(isolated, rb, monkeypatch):
    app, w = _welcome(rb, monkeypatch)
    w.open_advanced()
    w.tls_choice(2)
    assert app.cfg["verify_tls"] is True
    assert any("CA bundle" in t[0] for t in app.toasts)
    assert rommlib.tls_choices(app.cfg) == [True, False]


def test_settings_row_cycles_through_the_same_function(isolated, rb, monkeypatch):
    app = _app(rb, monkeypatch, {"verify_tls": "/userdata/ca.pem"})
    app.push(rb.SettingsScreen(app))
    scr = app.stack[-1]
    scr.sel = next(i for i, it in enumerate(scr.items()) if it[0] == "tls")
    app.dispatch("right")
    assert app.cfg["verify_tls"] is True and app.cfg["verify_tls_ca"] == "/userdata/ca.pem"
    app.dispatch("left")
    assert app.cfg["verify_tls"] == "/userdata/ca.pem"


def test_token_instead(isolated, rb, monkeypatch):
    seen = []

    def fn(api):
        seen.append(api.s.headers["Authorization"])
        return []
    _platforms(monkeypatch, fn)
    app, w = _welcome(rb, monkeypatch)
    app.dispatch("y")
    app.dispatch("down")
    app.dispatch("ok")
    assert w.field == "token" and w.view == "keys" and w.kb.context[0] == "rmm_"
    app.draw()
    tok = "rmm_" + "c" * 30
    w.type_text(tok)
    app.dispatch("start")
    _pump(app, lambda: w.step == 2)
    assert seen[0] == "Bearer " + tok and app.cfg["token"] == tok
    assert app.cfg["url"] == "https://romm.example.com"


# ------------------------------------------------------------------ the pairing code

def test_pair_checks_itself_at_eight_characters(isolated, rb, monkeypatch, mock_url):
    app, w = _welcome(rb, monkeypatch, {"url": mock_url}, url=mock_url)
    w.step = 1
    code = requests.post(mock_url + "/mock/pair-code", timeout=5).json()["code"]
    w.type_text(code[:4].lower())
    app.draw()
    assert w.busy is None
    w.type_text(code[4:])
    assert w.busy                                     # no Start needed
    _pump(app, lambda: w.step == 2)
    assert rommlib.valid_token(app.cfg["token"])
    assert any("Paired with RomM" in t[0] for t in app.toasts)
    _pump(app, lambda: app.loaded)
    app.draw()
    _pump(app, lambda: isinstance(app.stack[-1], rb.PlatformScreen))


def test_a_refused_code_clears_for_the_next(isolated, rb, monkeypatch, mock_url):
    app, w = _welcome(rb, monkeypatch, {"url": mock_url}, url=mock_url)
    w.step = 1
    w.type_text("ZZZZ9999")
    _pump(app, lambda: w.busy is None)
    assert w.step == 1 and w.code_kb.value == ""
    assert app.toasts[-1][1] == rb.RED
