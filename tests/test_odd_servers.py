"""Servers that aren't what the address promised: some other JSON where RomM should be, plain
http behind an https:// address, redirects that drop the token, a library that grows while
it's listed, GitHub out of reach when Updates is pressed."""
import json
import socket
import threading
import urllib.parse

import pytest
import requests

import rommlib

TOKEN = "rmm_" + "x" * 30


class Raw:
    """A socket that answers every request with handler(path, request bytes) -> bytes."""

    def __init__(self, handler):
        self.s = socket.socket()
        self.s.bind(("127.0.0.1", 0))
        self.s.listen(16)
        self.port, self.handler, self.reqs = self.s.getsockname()[1], handler, []
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        while True:
            try:
                c, _ = self.s.accept()
            except OSError:
                return
            threading.Thread(target=self.one, args=(c,), daemon=True).start()

    def one(self, c):
        data = b""
        c.settimeout(5)
        try:
            while b"\r\n\r\n" not in data:
                d = c.recv(65536)
                if not d:
                    break
                data += d
                if not data[:1].isalpha():           # a TLS hello: answer it in plain http, as a web server does
                    break
            self.reqs.append(data)
            c.sendall(self.handler(data.split(b" ")[1].decode() if data[:1].isalpha() else "", data))
        except OSError:
            pass
        finally:
            c.close()


@pytest.fixture
def raw():
    made = []

    def make(handler):
        made.append(Raw(handler))
        return made[-1]
    yield make
    for r in made:
        r.s.close()


def answer(body=b"", status="200 OK", headers=()):
    head = "HTTP/1.1 %s\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: %d\r\n" % (status, len(body))
    return (head + "".join("%s: %s\r\n" % h for h in headers) + "\r\n").encode() + body


@pytest.mark.parametrize("body", [
    b'{"detail": "ok"}', b"null", b'["nes"]',
    b'[{"id": 1, "slug": "nes", "rom_count": 3, "name": "NES"}]',                          # no fs_slug
    b'[{"id": 1, "fs_slug": "nes", "rom_count": 3, "name": null, "display_name": null}]',  # no name
])
def test_json_that_isnt_romms_platform_list_is_an_error_not_a_crash(raw, body):
    srv = raw(lambda path, data: answer(body))
    with pytest.raises(rommlib.RommError, match="Not a RomM API response"):
        rommlib.Api("http://127.0.0.1:%d" % srv.port, TOKEN).platforms()


def test_a_platform_named_only_one_way_is_fine(raw):
    body = b'[{"id": 1, "fs_slug": "nes", "rom_count": 3, "name": "NES", "display_name": null}]'
    srv = raw(lambda path, data: answer(body))
    assert rommlib.Api("http://127.0.0.1:%d" % srv.port, TOKEN).platforms()[0]["id"] == 1


def test_https_typed_for_a_plain_http_server_says_so(raw):
    srv = raw(lambda path, data: answer(status="400 Bad Request"))
    url = rommlib.clean_server_url("127.0.0.1:%d" % srv.port)
    assert url.startswith("https://")
    for verify in (True, False):                   # turning verification off can't help: say what will
        with pytest.raises(rommlib.TlsRejected) as e:
            rommlib.Api(url, TOKEN, verify).platforms()
        assert e.value.why == "plain-http"


def test_the_welcome_screen_has_words_for_every_certificate_reason(rb):
    assert "http://" in rb.screens.welcome.CERT_SAYS["plain-http"]


def test_a_redirect_to_another_port_names_the_new_address(raw):
    there = raw(lambda path, data: answer(b"{}", "401 Unauthorized"))
    here = raw(lambda path, data: answer(status="302 Found",
                                         headers=[("Location", "http://127.0.0.1:%d%s" % (there.port, path))]))
    with pytest.raises(rommlib.RommError) as e:
        rommlib.Api("http://127.0.0.1:%d" % here.port, TOKEN).platforms()
    assert not isinstance(e.value, rommlib.AuthRejected)          # the token is fine: it was never sent there
    assert "moved to http://127.0.0.1:%d" % there.port in str(e.value)
    assert b"Authorization" not in there.reqs[0]


def test_a_rejected_token_is_still_a_rejected_token(raw):
    srv = raw(lambda path, data: answer(b"{}", "401 Unauthorized"))
    with pytest.raises(rommlib.AuthRejected):
        rommlib.Api("http://127.0.0.1:%d" % srv.port, TOKEN).platforms()


def test_pairing_with_a_server_that_wants_https_says_so(raw):
    srv = raw(lambda path, data: answer(status="301 Moved Permanently",
                                        headers=[("Location", "https://romm.example" + path)]))
    with pytest.raises(rommlib.RommError, match="wants https"):
        rommlib.exchange_pair_code("http://127.0.0.1:%d" % srv.port, "ABCD1234")


def test_a_ca_bundle_that_isnt_there_is_named(raw, tmp_path):
    srv = raw(lambda path, data: answer(b"[]"))
    gone = str(tmp_path / "ca.pem")
    with pytest.raises(rommlib.RommError, match="CA bundle isn't there"):
        rommlib.Api("https://127.0.0.1:%d" % srv.port, TOKEN, gone).platforms()


def test_a_library_that_grows_while_its_listed_lists_no_game_twice(raw):
    lib = [{"id": i, "name": n} for i, n in enumerate("ABCDEFGHIJ", 1)]
    lock, hits = threading.Lock(), []

    def page(path, data):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
        off, lim = int(q["offset"][0]), int(q["limit"][0])
        with lock:
            hits.append(off)
            if len(hits) == 2:                         # a scan adds a game that sorts first
                lib.insert(0, {"id": 99, "name": "0-new"})
            return answer(json.dumps({"items": lib[off:off + lim], "total": len(lib)}).encode())
    srv = raw(page)
    api = rommlib.Api("http://127.0.0.1:%d" % srv.port, TOKEN)
    for fields in (None, ("name",)):                   # the ids are told apart before fields are dropped
        del hits[:], lib[:]
        lib.extend({"id": i, "name": n} for i, n in enumerate("ABCDEFGHIJ", 1))
        names = [r["name"] for r in api.roms_all(limit=3, platform_ids=[1], fields=fields)]
        assert len(names) == len(set(names)) == 10


def test_updates_pressed_while_github_cant_be_reached_never_says_latest(isolated, monkeypatch):
    def down(*a, **k):
        raise requests.exceptions.ConnectionError("offline")
    monkeypatch.setattr(rommlib, "plain_get", down)
    cfg = dict(rommlib.DEFAULT_CONFIG, check_updates=True)
    assert rommlib.newer_release(cfg, force=True) is None            # the daily check stays quiet
    with pytest.raises(rommlib.UpdateError, match="GitHub"):
        rommlib.newer_release(cfg, force=True, strict=True)


def test_updates_pressed_while_github_answers_an_error_never_says_latest(isolated, monkeypatch):
    class R:
        status_code = 403

        def close(self):
            pass
    monkeypatch.setattr(rommlib, "plain_get", lambda *a, **k: R())
    monkeypatch.setattr(rommlib, "discard", lambda r: None)
    with pytest.raises(rommlib.UpdateError):
        rommlib.newer_release(dict(rommlib.DEFAULT_CONFIG), force=True, strict=True)


def test_a_web_page_where_githubs_release_should_be_is_an_update_error(isolated, monkeypatch):
    class R:
        status_code = 200

        def json(self):
            raise ValueError("<html>")
    monkeypatch.setattr(rommlib, "_gh_get", lambda *a, **k: R())
    with pytest.raises(rommlib.UpdateError, match="wasn't readable"):
        rommlib.release_info()


def test_the_header_follows_romms_last_answer(raw, rb, isolated, monkeypatch):
    """Connected at the start, then RomM goes away or rejects the token: the header says so,
    and says Online again after the next good answer."""
    mood = ["200 OK"]
    srv = raw(lambda path, data: answer(b"[]", mood[0]))
    api = rommlib.Api("http://127.0.0.1:%d" % srv.port, TOKEN)
    monkeypatch.setattr(rommlib, "request_with_retry", lambda fn, *a, **k: fn(), raising=False)

    class Header:
        status, romm_version = "Online", "5.4.0"
    Header.api = api
    label = lambda: rb.App.status_label(Header)
    api.platforms()
    assert label() == "Online · RomM 5.4.0"
    mood[0] = "401 Unauthorized"
    with pytest.raises(rommlib.AuthRejected):
        api.platforms()
    assert label() == "Token rejected"
    mood[0] = "200 OK"
    api.platforms()
    assert label().startswith("Online")
    srv.s.close()
    api2 = rommlib.Api("http://127.0.0.1:9", TOKEN)
    Header.api = api2
    with pytest.raises(rommlib.RommError):
        api2.get("/api/platforms", timeout=2)
    assert label() == "Offline"
