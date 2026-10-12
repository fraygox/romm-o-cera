"""Every HTTP request the app makes (RomM, GitHub) goes through one of these
sessions: connections are kept open and shared by all threads (one TLS
handshake per connection, not per request), a busy server gets fewer
requests at once instead of every one failing, and a request it turned away
is tried again after the wait it asked for."""
import email.utils
import random
import threading
import time
import urllib.parse

import requests


BUSY_STATUS = (429, 502, 503, 504)   # the server (or the proxy in front) is overloaded or restarting
NET_TRIES = 4                        # a GET it turned away is sent up to this many times
GATE_FLOOR = 4                       # a server busy enough to cut still gets this many at once
NET_WAIT_MAX = 10.0                  # seconds: never wait longer than this before trying again
IDEMPOTENT = ("GET", "HEAD", "OPTIONS")


def retry_after(r):
    """Seconds a 429/503 asks us to wait (Retry-After: seconds or a date), or None."""
    v = (r.headers.get("Retry-After") or "").strip() if r is not None else ""
    if not v:
        return None
    try:
        s = float(v)
    except ValueError:
        try:
            s = email.utils.parsedate_to_datetime(v).timestamp() - time.time()
        except (TypeError, ValueError, IndexError, OverflowError):
            return None
    return s if s > 0 else None          # 0, a past date, nan: our own backoff applies


class HostGate:
    """How many requests one server gets at once. Starts at `ceiling`. A sign
    of overload (a 429/502/503/504 or a timeout) halves what was in flight,
    once per round (answers to requests sent before the last cut don't cut
    again), never below GATE_FLOOR: one lone 502 mustn't leave a trickle. A pause the server
    asked for holds every request. Each answer in time adds a little back
    (AIMD, like TCP), up to the ceiling."""

    def __init__(self, ceiling):
        self.ceiling = max(1, ceiling)
        self.limit = float(self.ceiling)
        self.active = 0
        self.sent = 0                       # requests let through so far (their tickets)
        self.cut_after = 0                  # only requests sent from here on can cut again
        self.until = 0.0                    # nothing is sent before this (monotonic)
        self.cv = threading.Condition()

    def enter(self):
        """Wait for a place; returns the request's ticket for leave()."""
        with self.cv:
            while True:
                wait = self.until - time.monotonic()
                if wait <= 0 and self.active < int(self.limit):
                    self.active += 1
                    self.sent += 1
                    return self.sent
                self.cv.wait(wait if wait > 0 else None)

    def leave(self, ticket, busy=False, pause=0.0):
        with self.cv:
            if busy:
                if ticket > self.cut_after:
                    self.limit = max(float(min(GATE_FLOOR, self.ceiling)), min(self.limit, self.active) / 2)
                    self.cut_after = self.sent
                if pause:
                    self.until = max(self.until, time.monotonic() + min(pause, NET_WAIT_MAX))
            else:
                self.limit = min(float(self.ceiling), self.limit + 1.0 / self.limit)
            self.active -= 1
            self.cv.notify_all()


def _backoff(attempt, asked):
    """Seconds before try number attempt+1: what the server asked for, else
    0.5 s, 1 s, 2 s, 4 s with jitter so the threads don't all come back at once."""
    if asked is not None:
        return min(asked, NET_WAIT_MAX)
    return min(NET_WAIT_MAX, 0.5 * (2 ** (attempt - 1)) * random.uniform(0.75, 1.25))


class GatedAdapter(requests.adapters.HTTPAdapter):
    """requests' connection pool, plus a HostGate per server and retries for
    requests the server turned away. Only sending and the response head are
    gated: a long download's body doesn't hold a place. A GET is tried again
    after a busy status (up to NET_TRIES times), and once after a refused or
    dropped connection (a restarting server, a kept connection the server had
    closed: not load). A timeout isn't repeated: to connect, the address is
    likely wrong (the user waits once, not four times); to answer, the server
    is working on it. Nothing else (POST) is ever sent twice."""

    def __init__(self, pool=10, ceiling=None):
        super().__init__(pool_maxsize=pool)
        self.ceiling = ceiling or pool
        self.gates = {}
        self.gates_lock = threading.Lock()

    def gate(self, url):
        host = urllib.parse.urlsplit(url).netloc.lower()
        with self.gates_lock:
            g = self.gates.get(host)
            if g is None:
                g = self.gates[host] = HostGate(self.ceiling)
            return g

    def send(self, request, **kw):
        g = self.gate(request.url)
        again = request.method in IDEMPOTENT
        attempt = 0
        while True:
            attempt += 1
            last = attempt >= NET_TRIES or not again
            ticket = g.enter()
            try:
                r = super().send(request, **kw)
            except requests.exceptions.SSLError:
                g.leave(ticket)                       # a certificate problem isn't load
                raise
            except requests.exceptions.Timeout:
                g.leave(ticket, busy=True)
                raise
            except requests.exceptions.ConnectionError:
                g.leave(ticket)
                if last or attempt >= 2:
                    raise
                time.sleep(_backoff(attempt, None))
                continue
            except BaseException:
                g.leave(ticket)
                raise
            if r.status_code not in BUSY_STATUS:
                g.leave(ticket)
                return r
            asked = retry_after(r)
            g.leave(ticket, busy=True, pause=asked or 0.0)
            if last or (asked is not None and asked > NET_WAIT_MAX):
                return r                             # the caller reports it
            discard(r)                               # its connection back to the pool
            time.sleep(_backoff(attempt, asked))


def discard(r, most=1 << 16):
    """Done with a response without wanting its body (an error page, a busy
    answer): read a small body off first, so the connection goes back to the
    pool. Closing it unread would close the connection, and the next request
    would pay for a new one (and a TLS handshake). A big body is cheaper to
    drop with its connection."""
    try:
        n = 0
        for chunk in r.iter_content(1 << 14):
            n += len(chunk)
            if n > most:
                break
    except Exception:
        pass
    r.close()


def http_session(pool=10, ceiling=None):
    """A requests.Session whose every request goes through a GatedAdapter."""
    s = requests.Session()
    a = GatedAdapter(pool, ceiling)
    for scheme in ("https://", "http://"):
        s.mount(scheme, a)
    return s


_PLAIN = []
_PLAIN_LOCK = threading.Lock()


def plain_get(url, **kw):
    """requests.get for servers other than RomM (GitHub releases and tools):
    no RomM token, but the same shared connections, limits and retries."""
    with _PLAIN_LOCK:
        if not _PLAIN:
            _PLAIN.append(http_session(pool=4))
    return _PLAIN[0].get(url, **kw)
