"""Every HTTP request goes through one gated session: connections are kept
and reused (even after an error answer), a busy server is retried after the
wait it asks for and gets fewer requests at once, nothing but a GET is sent
twice, and a cover RomM couldn't send is asked for again later instead of
showing "no cover" for the rest of the session."""
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

import rommlib


class _Server:
    """A local HTTP/1.1 server (keep-alive). plan(path) -> (status, headers, body)
    decides each answer; it counts requests, connections and requests in flight."""

    def __init__(self, plan, delay=0.0):
        me = self
        self.plan, self.delay = plan, delay
        self.hits, self.ports, self.lock = [], set(), threading.Lock()
        self.inflight = self.peak = 0

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _answer(self):
                with me.lock:
                    me.hits.append((self.command, self.path))
                    me.ports.add(self.client_address[1])
                    me.inflight += 1
                    me.peak = max(me.peak, me.inflight)
                    n = me.inflight
                try:
                    if me.delay:
                        time.sleep(me.delay)
                    status, headers, body = me.plan(self.path, n)
                    self.send_response(status)
                    for k, v in headers.items():
                        self.send_header(k, v)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                finally:
                    with me.lock:
                        me.inflight -= 1

            do_GET = do_POST = _answer

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.url = "http://127.0.0.1:%d" % self.srv.server_address[1]

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def serve():
    made = []

    def start(plan, delay=0.0):
        made.append(_Server(plan, delay))
        return made[-1]
    yield start
    for s in made:
        s.close()


@pytest.fixture
def quick(monkeypatch):
    """Waits between tries shrunk so the tests stay fast."""
    monkeypatch.setattr(rommlib, "NET_WAIT_MAX", 0.05)


def _then_ok(first, n_bad=1, headers=None):
    seen = []

    def plan(path, inflight):
        seen.append(path)
        if len(seen) <= n_bad:
            return first, headers or {}, b"busy"
        return 200, {"Content-Type": "application/json"}, b"[]"
    return plan


# ------------------------------------------------------------ retries

@pytest.mark.parametrize("status", [429, 502, 503, 504])
def test_a_busy_answer_is_tried_again(serve, quick, status):
    s = serve(_then_ok(status, headers={"Retry-After": "0"}))
    assert rommlib.Api(s.url, "t").get("/api/platforms") == []
    assert len(s.hits) == 2


def test_retry_after_is_waited_for(serve, monkeypatch):
    s = serve(_then_ok(503, headers={"Retry-After": "1"}))
    t0 = time.monotonic()
    assert rommlib.Api(s.url, "t").get("/api/platforms") == []
    assert time.monotonic() - t0 >= 0.9


def test_a_server_still_busy_after_three_tries_is_reported(serve, quick):
    s = serve(_then_ok(503, n_bad=99))
    with pytest.raises(rommlib.RommError, match="HTTP 503"):
        rommlib.Api(s.url, "t").get("/api/platforms")
    assert len(s.hits) == rommlib.NET_TRIES


def test_a_long_retry_after_isnt_sat_out(serve):
    s = serve(_then_ok(429, n_bad=99, headers={"Retry-After": "3600"}))
    t0 = time.monotonic()
    with pytest.raises(rommlib.RommError, match="HTTP 429"):
        rommlib.Api(s.url, "t").get("/api/platforms")
    assert len(s.hits) == 1 and time.monotonic() - t0 < 2


def test_a_post_is_never_sent_twice(serve, quick):
    s = serve(_then_ok(503))
    r = rommlib.http_session().post(s.url + "/x", data=b"code")
    assert r.status_code == 503 and len(s.hits) == 1


def test_an_unreachable_server_is_tried_again_then_reported(quick, monkeypatch):
    sends = []
    real = requests.adapters.HTTPAdapter.send

    def send(self, request, **kw):
        sends.append(1)
        return real(self, request, **kw)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    api = rommlib.Api("http://127.0.0.1:9", "t")
    with pytest.raises(rommlib.RommError, match="Can't reach RomM"):
        api.get("/api/platforms")
    assert len(sends) == 2                                   # once more, not four times
    g = api.s.get_adapter(api.base).gate(api.base)
    assert g.limit == g.ceiling                               # a refused connection isn't load


def test_a_timeout_to_connect_isnt_waited_out_again(quick, monkeypatch):
    """A wrong address in Settings: the user waits one timeout, not four."""
    sends = []

    def send(self, request, **kw):
        sends.append(1)
        raise requests.exceptions.ConnectTimeout("blackhole")
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    with pytest.raises(rommlib.RommError, match="ConnectTimeout"):
        rommlib.Api("http://10.255.255.1", "t").get("/api/platforms")
    assert len(sends) == 1


def test_a_slow_answer_isnt_asked_again(serve, quick):
    s = serve(lambda p, n: (200, {}, b"[]"), delay=1.0)
    with pytest.raises(rommlib.RommError, match="ReadTimeout"):
        rommlib.Api(s.url, "t").get("/api/platforms", timeout=0.3)
    time.sleep(1.0)
    assert len(s.hits) == 1                 # the server was working on it: not piled on


def test_retry_after_dates_and_junk():
    class R:
        def __init__(self, v):
            self.headers = {"Retry-After": v} if v is not None else {}
    import email.utils
    assert rommlib.retry_after(R("7")) == 7.0
    assert 50 < rommlib.retry_after(R(email.utils.formatdate(time.time() + 60, usegmt=True))) <= 60
    assert rommlib.retry_after(R("soon")) is None and rommlib.retry_after(R(None)) is None
    for none in ("-5", "0", "nan", email.utils.formatdate(time.time() - 60, usegmt=True)):
        assert rommlib.retry_after(R(none)) is None, none     # our own backoff, not none at all


# ------------------------------------------------------------ an overloaded server

def test_an_overloaded_server_gets_fewer_requests_instead_of_all_failing(serve, quick):
    """RomM (or its proxy) answers 503 past 6 requests at once. 12 threads ask
    for 60 things: the session backs off to what the server takes, and every
    request gets its answer. (Past GATE_FLOOR: a server that takes fewer than
    the floor stays overloaded by design.)"""
    def plan(path, inflight):
        return (503, {}, b"busy") if inflight > 6 else (200, {}, b"[]")
    s = serve(plan, delay=0.05)
    api = rommlib.Api(s.url, "t")
    out = rommlib.fetch_many(lambda k: api.get("/api/roms/%d" % k), range(60), workers=12)
    failed = [k for k, v in out.items() if isinstance(v, Exception)]
    assert not failed, "%d of 60 failed: %s" % (len(failed), out[failed[0]])
    gate = api.s.get_adapter(s.url).gate(s.url)
    assert gate.limit < gate.ceiling


def test_the_gate_halves_what_was_in_flight_once_per_round_and_grows_back():
    g = rommlib.HostGate(32)
    burst = [g.enter() for _ in range(12)]    # 12 sent together, all turned away: one cut
    for t in burst:
        g.leave(t, busy=True)
    assert g.limit == 6                       # half of the 12 in flight, not half the ceiling
    t = g.enter()                             # sent after the cut: its failure cuts again
    g.leave(t, busy=True)
    assert g.limit == rommlib.GATE_FLOOR      # but a lone failure never leaves a trickle
    for _ in range(2000):
        g.leave(g.enter())
    assert g.limit == 32                      # back up to the ceiling, never past it


def test_the_gate_holds_requests_while_the_server_asked_for_a_pause():
    g = rommlib.HostGate(4)
    g.leave(g.enter(), busy=True, pause=0.3)
    t0 = time.monotonic()
    g.leave(g.enter())
    assert time.monotonic() - t0 >= 0.25


def test_a_gate_at_its_limit_waits_for_a_free_place():
    g = rommlib.HostGate(1)
    first = g.enter()
    got = []
    t = threading.Thread(target=lambda: got.append(g.enter()))
    t.start()
    time.sleep(0.2)
    assert not got
    g.leave(first)
    t.join(2)
    assert got


def test_a_certificate_error_isnt_load(monkeypatch):
    def bad(self, request, **kw):
        raise requests.exceptions.SSLError("cert")
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", bad)
    s = rommlib.http_session()
    with pytest.raises(requests.exceptions.SSLError):
        s.get("https://romm.invalid/x")
    g = s.get_adapter("https://romm.invalid/").gate("https://romm.invalid/x")
    assert g.limit == g.ceiling and g.active == 0


def test_each_server_has_its_own_gate():
    a = rommlib.GatedAdapter()
    assert a.gate("https://romm.example/x") is a.gate("https://ROMM.example/y")
    assert a.gate("https://romm.example/x") is not a.gate("https://api.github.com/x")


# ------------------------------------------------------------ connections are reused

def test_error_answers_give_their_connection_back(serve, isolated, quick):
    """A 404 or 503 cover used to keep its connection out of the pool (a new
    TLS handshake for the next request, just when the server is busiest)."""
    def plan(path, inflight):
        return {"/none.png": (404, {}, b"no"), "/busy.png": (503, {}, b"busy")}.get(path, (200, {}, b"\x89PNG"))
    s = serve(plan)
    api = rommlib.Api(s.url, "t")
    for i in range(10):
        assert api.fetch_cover({"id": 100 + i, "path_cover_small": "/none.png"}) is None
        with pytest.raises(rommlib.CoverError):
            api.fetch_cover({"id": 200 + i, "path_cover_small": "/busy.png"})
        assert api.fetch_cover({"id": 300 + i, "path_cover_small": "/ok.png"})
    assert len(s.hits) == 10 * (1 + rommlib.NET_TRIES + 1)
    assert len(s.ports) == 1, "%d connections for %d requests" % (len(s.ports), len(s.hits))


def test_threads_share_a_few_connections(serve):
    s = serve(lambda p, n: (200, {}, b"[]"), delay=0.01)
    api = rommlib.Api(s.url, "t")
    rommlib.fetch_many(lambda k: api.get("/api/%d" % k), range(200), workers=6)
    assert len(s.ports) <= 6


def test_github_requests_share_a_session_too(serve, monkeypatch):
    s = serve(lambda p, n: (200, {}, b"{}"))
    for _ in range(5):
        assert rommlib.plain_get(s.url + "/repos/x").status_code == 200
    assert len(s.ports) == 1
    assert "Authorization" not in rommlib._PLAIN[0].headers    # never the RomM token


def test_rb_finalize_gives_back_the_connection_of_a_missing_file(serve):
    import rb_finalize
    s = serve(lambda p, n: (404, {}, b"no") if p == "/gone" else (200, {}, b"data"))
    api = rommlib.Api(s.url, "t")
    for _ in range(5):
        assert rb_finalize.fetch_bytes(api, "/gone") is None
        assert rb_finalize.fetch_bytes(api, "/ok") == b"data"
    assert len(s.ports) == 1


# ------------------------------------------------------------ covers

def test_a_cover_knows_missing_from_couldnt_fetch(serve, isolated, quick):
    def plan(path, inflight):
        return {"/none.png": (404, {}, b""), "/err.png": (500, {}, b""),
                "/denied.png": (401, {}, b"")}.get(path, (200, {}, b""))
    s = serve(plan)
    api = rommlib.Api(s.url, "t")
    assert api.fetch_cover({"id": 1, "path_cover_small": "/none.png"}) is None
    assert api.fetch_cover({"id": 5, "path_cover_small": "/denied.png"}) is None   # asking again won't help
    assert api.fetch_cover({"id": 2, "path_cover_small": "/empty.png"}) is None
    with pytest.raises(rommlib.CoverError, match="HTTP 500"):
        api.fetch_cover({"id": 3, "path_cover_small": "/err.png"})
    with pytest.raises(rommlib.CoverError):
        rommlib.Api("http://127.0.0.1:9", "t").fetch_cover({"id": 4, "path_cover_small": "/x.png"})
    assert not [f for f in os.listdir(rommlib.COVER_CACHE) if f.endswith(".tmp")]


@pytest.fixture
def app(isolated, rb, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    a = rb.App(None, headless=True)
    a.cover_loader.WORKERS = 0                     # run loads here, not on worker threads
    good = os.path.join(rommlib.DATA_DIR, "good.png")
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    pygame.image.save(pygame.Surface((4, 6)), good)
    a.good = good
    return a


def _run(app):
    while app.cover_loader.run_one(wait=False):
        pass
    for fn in app.events_from_threads:
        fn()
    app.events_from_threads.clear()


def test_a_cover_romm_couldnt_send_is_asked_again_later(app, rb, monkeypatch):
    calls = []

    def fetch_cover(rom, large=False):
        calls.append(1)
        if len(calls) <= 2:
            raise rommlib.CoverError("cover: HTTP 503")
        return app.good
    app.api.fetch_cover = fetch_cover
    rom = {"id": 7}
    assert app.cover(rom) is None
    _run(app)
    assert 7 not in app.covers and app.cover_retry[7][1] == 1           # "…", not "no cover"
    assert app.cover(rom) is None and not app.cover_loader.todo         # too soon: not asked
    app.cover_retry[7] = (0, 1)                                         # the wait is over
    app.cover(rom)
    _run(app)
    assert app.cover_retry[7][1] == 2 and app.cover_retry[7][0] - time.monotonic() > 10
    app.cover_retry[7] = (0, 2)
    app.cover(rom)
    _run(app)
    assert app.covers[7].get_size() == rb.cover_fit(__import__("pygame").Surface((4, 6))).get_size() and 7 not in app.cover_retry
    assert app.cover_order.count(7) == 1


def test_retry_waits_grow_and_stop_growing(app, rb, monkeypatch):
    app.api.fetch_cover = lambda rom, large=False: (_ for _ in ()).throw(rommlib.CoverError("x"))
    logged = []
    monkeypatch.setattr(rommlib, "log", lambda m: logged.append(m))
    waits = []
    for _ in range(5):
        app.cover({"id": 8})
        _run(app)
        waits.append(round(app.cover_retry[8][0] - time.monotonic()))
        app.cover_retry[8] = (0, app.cover_retry[8][1])
    assert waits == [5, 15, 60, 60, 60]
    assert len(logged) == 1                   # logged once, not every minute it stays down
    app.forget_covers()
    assert not app.cover_retry


def test_a_cover_with_no_art_stays_no_cover(app, rb):
    calls = []
    app.api.fetch_cover = lambda rom, large=False: calls.append(1)
    app.cover({"id": 9})
    _run(app)
    assert app.covers[9] is None and 9 not in app.cover_retry
    app.cover({"id": 9})
    assert len(calls) == 1


def test_an_image_that_wont_decode_is_no_cover_not_a_retry_loop(app, rb):
    bad = os.path.join(rommlib.DATA_DIR, "bad.png")
    calls = []

    def fetch_cover(rom, large=False):
        calls.append(1)
        with open(bad, "wb") as f:
            f.write(b"not an image")
        return bad
    app.api.fetch_cover = fetch_cover
    app.cover({"id": 10})
    _run(app)
    assert app.covers[10] is None and 10 not in app.cover_retry and len(calls) == 2
