"""RomM requests side by side: fetch_many, the read-ahead Prefetch, listings
fetched a few pages at a time (roms_all), Check games and the BIOS check
asking for records in parallel, and the cover loader's worker threads. Each
must give exactly what the one-at-a-time code gave."""
import os
import threading
import time

import pytest

import rommlib


class Overlap:
    """Counts calls in flight: the most at once, and every key asked (in order)."""

    def __init__(self, delay=0.02):
        self.delay, self.now, self.most, self.asked = delay, 0, 0, []
        self.lock = threading.Lock()

    def __call__(self, key, fn=lambda k: k):
        with self.lock:
            self.now += 1
            self.most = max(self.most, self.now)
            self.asked.append(key)
        try:
            time.sleep(self.delay)
            return fn(key)
        finally:
            with self.lock:
                self.now -= 1


# ------------------------------------------------------------ fetch_many / Prefetch

def test_fetch_many_runs_side_by_side_and_hands_back_errors():
    o = Overlap()

    def fn(k):
        if k == 3:
            raise rommlib.RommError("no 3")
        return k * 10
    out = rommlib.fetch_many(lambda k: o(k, fn), range(12), workers=4)
    assert 2 <= o.most <= 4 and sorted(out) == list(range(12))
    assert out[5] == 50 and isinstance(out[3], rommlib.RommError)
    with pytest.raises(rommlib.RommError, match="no 3"):
        rommlib.fetched(out[3])
    assert rommlib.fetched(out[5]) == 50
    assert rommlib.fetch_many(fn, []) == {}


def test_fetch_many_stops_asking_once_cancelled():
    cancel, o = threading.Event(), Overlap(0.01)

    def fn(k):
        if k == 2:
            cancel.set()
        return k
    out = rommlib.fetch_many(lambda k: o(k, fn), range(40), workers=2, cancel=cancel)
    assert len(out) < 40 and len(o.asked) < 40


def test_prefetch_reads_ahead_in_order_but_never_too_far():
    o = Overlap(0.01)

    def fn(k):
        if k == "bad":
            raise rommlib.RommError("gone")
        return k.upper()
    keys = ["k%d" % i for i in range(30)]
    keys.insert(7, "bad")
    with rommlib.Prefetch(lambda k: o(k, fn), keys + ["k3"], workers=3, ahead=5) as p:
        for i, k in enumerate(keys):
            if k == "bad":
                with pytest.raises(rommlib.RommError, match="gone"):
                    p.get(k)
                continue
            assert p.get(k) == k.upper()
            assert len(o.asked) <= i + 1 + 5            # at most `ahead` past the one in use
        assert o.most <= 3
        assert o.asked[:len(keys)] == keys              # in the loop's order, duplicates once
        n = len(o.asked)
        assert p.get("k3") == "K3" and p.get("other") == "OTHER"     # again / never listed: fetched there
        assert len(o.asked) == n + 2


def test_prefetch_keeps_reading_ahead_past_keys_the_loop_skips():
    """Check games skips the record of a CHD converted here: a run of skipped
    keys longer than the window left the rest fetched one at a time."""
    o = Overlap(0.02)
    with rommlib.Prefetch(o, range(60), workers=4, ahead=6) as p:
        p.get(0)
        o.most = 0
        for k in range(30, 60):                          # 1..29 never asked for
            assert p.get(k) == k
        assert o.most >= 2 and len(p.futures) <= 6
        assert not set(o.asked) & set(range(7, 30))      # keys long passed weren't fetched for nothing


def test_quitting_never_waits_for_lookups_in_flight(tmp_path):
    """The workers are daemon threads: Python waits for ThreadPoolExecutor's."""
    import subprocess
    import sys
    code = """
import sys, threading, time
sys.path.insert(0, %r)
import rommlib
def slow(k):
    time.sleep(5)
    return k
def check():
    with rommlib.Prefetch(slow, range(10)) as p:
        p.get(0)
threading.Thread(target=check, daemon=True).start()
rommlib.fetch_many
threading.Thread(target=lambda: rommlib.fetch_many(slow, range(4)), daemon=True).start()
time.sleep(0.3)
""" % os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    t = time.time()
    subprocess.run([sys.executable, "-c", code], check=True, timeout=30, cwd=str(tmp_path),
                   env=dict(os.environ, ROMM_BROWSER_DATA=str(tmp_path)))
    assert time.time() - t < 3


def test_prefetch_with_nothing_to_fetch_and_close_drops_the_rest():
    with rommlib.Prefetch(lambda k: k, []) as p:
        assert p.get("x") == "x"
    o = Overlap(0.05)
    p = rommlib.Prefetch(o, range(100), workers=2, ahead=4)
    p.get(0)
    p.close()
    time.sleep(0.2)
    assert len(o.asked) <= 7                            # queued ones never ran


# ------------------------------------------------------------ roms_all

class PagedApi(rommlib.Api):
    """roms() over a list, like RomM: a page and the current total."""

    def __init__(self, items, delay=0.01):
        super().__init__("http://romm.invalid", "t")
        self.items, self.o, self.calls = items, Overlap(delay), []

    def roms(self, offset=0, limit=60, **kw):
        self.calls.append((offset, limit, kw))
        return self.o(offset, lambda off: {"items": self.items[off:off + limit], "total": len(self.items)})


def _one_at_a_time(api, limit, cap=None, **kw):
    """The loop every caller had before roms_all."""
    out, off = [], 0
    while True:
        page = api.roms(offset=off, limit=limit, **kw)
        got = page.get("items", [])
        out.extend(got)
        off += len(got)
        if not got or off >= page.get("total", 0) or (cap and off >= cap):
            return out


@pytest.mark.parametrize("n", [0, 1, 5, 23, 25, 26])
def test_roms_all_gives_every_page_in_order(n):
    items = [{"id": i} for i in range(n)]
    api = PagedApi(items)
    assert api.roms_all(limit=5, platform_ids=[7]) == _one_at_a_time(PagedApi(items), 5) == items
    assert all(kw == {"platform_ids": [7]} for _, _, kw in api.calls)
    assert len(api.calls) == max(1, -(-n // 5))         # one request per page, none extra


def test_roms_all_asks_for_pages_side_by_side_and_stops_at_cap():
    api = PagedApi([{"id": i} for i in range(1200)])
    assert [r["id"] for r in api.roms_all(limit=250, cap=1000)] == list(range(1000))
    assert 2 <= api.o.most <= 3 and len(api.calls) == 4    # first page, then the other three at once
    api = PagedApi([{"id": i} for i in range(3000)])
    assert len(api.roms_all(limit=250)) == 3000 and 2 <= api.o.most <= rommlib.PAGE_WORKERS


def test_roms_all_when_the_library_changes_while_listing():
    items = [{"id": i} for i in range(20)]
    api = PagedApi(items)
    real = api.roms

    def growing(offset=0, limit=60, **kw):
        page = real(offset=offset, limit=limit, **kw)
        if offset == 0:
            items.extend({"id": i} for i in range(20, 27))   # added after the first page
        return page
    api.roms = growing
    assert [r["id"] for r in api.roms_all(limit=5)] == list(range(27))
    shrink = [{"id": i} for i in range(20)]
    api = PagedApi(shrink)
    real2 = api.roms

    def shrinking(offset=0, limit=60, **kw):
        page = real2(offset=offset, limit=limit, **kw)
        if offset == 0:
            del shrink[12:]
        return page
    api.roms = shrinking
    assert [r["id"] for r in api.roms_all(limit=5)] == list(range(12))


def test_roms_all_cancelled_and_failing():
    api = PagedApi([{"id": i} for i in range(50)])
    cancel = threading.Event()
    cancel.set()
    assert api.roms_all(limit=5, cancel=cancel) is None
    real = api.roms

    def fails(offset=0, limit=60, **kw):
        if offset == 20:
            raise rommlib.RommError("RomM error HTTP 500")
        return real(offset=offset, limit=limit, **kw)
    api.roms = fails
    with pytest.raises(rommlib.RommError, match="500"):
        api.roms_all(limit=5)


def test_roms_all_against_the_mock_romm(mock_url):
    api = rommlib.Api(mock_url, "t")
    # the smallest platform that still takes several pages: the same check, a few dozen requests
    pid = min((p for p in api.platforms() if (p.get("rom_count") or 0) > 9), key=lambda p: p["rom_count"])["id"]
    assert api.roms_all(limit=3, platform_ids=[pid]) == _one_at_a_time(api, 3, platform_ids=[pid])
    assert len(api.roms_all(limit=3, platform_ids=[pid])) > 6


# ------------------------------------------------------------ Check games

class AuditApi(PagedApi):
    """A RomM listing gba games G0..G19; G7's record fails; record sizes say G3
    and G11 on the box are cut short."""

    def __init__(self, delay=0.02):
        super().__init__([{"id": i, "name": "G%d" % i, "fs_name": "G%d.gba" % i, "fs_name_no_ext": "G%d" % i,
                           "platform_id": 1} for i in range(20)], delay=0.0)
        self.records = Overlap(delay)

    def rom(self, rom_id):
        def rec(i):
            if i == 7:
                raise rommlib.RommError("RomM error HTTP 502")
            return {"id": i, "files": [{"file_name": "G%d.gba" % i, "file_size_bytes": 8192, "category": None}]}
        return self.records(rom_id, rec)


def _gba_box():
    d = os.path.join(rommlib.ROMS_ROOT, "gba")
    os.makedirs(d)
    for i in range(20):
        with open(os.path.join(d, "G%d.gba" % i), "wb") as f:
            f.write(b"x" * (100 if i in (3, 11) else 8192))
    open(os.path.join(d, "Homebrew.gba"), "wb").close()        # not in RomM


def test_check_games_asks_for_records_side_by_side_with_the_same_findings(isolated, monkeypatch):
    _gba_box()
    monkeypatch.setattr(rommlib, "LOOKUP_WORKERS", 1)
    one = AuditApi()
    before = rommlib.audit(one, {"gba": [1]})
    monkeypatch.setattr(rommlib, "LOOKUP_WORKERS", 6)
    par = AuditApi()
    after = rommlib.audit(par, {"gba": [1]})
    assert after == before and one.records.most == 1 and par.records.most > 1
    findings, stats = after
    assert stats == {"checked": 21, "matched": 20, "unmatched": 1}
    probs = {os.path.basename(f["path"]): f["problem"] for f in findings}
    assert probs["G7.gba"].startswith("Couldn't fetch RomM record")
    assert probs["G3.gba"].startswith("Smaller") and probs["G11.gba"].startswith("Smaller")
    assert len(findings) == 3
    assert sorted(par.records.asked) == list(range(20))     # each record asked once


def test_check_games_never_lists_romm_for_an_empty_system_folder(isolated):
    os.makedirs(os.path.join(rommlib.ROMS_ROOT, "gba", "media"))
    open(os.path.join(rommlib.ROMS_ROOT, "gba", "_info.txt"), "w").close()    # what Batocera leaves there
    api = AuditApi()
    assert rommlib.audit(api, {"gba": [1]}) == ([], {"checked": 0, "matched": 0, "unmatched": 0})
    assert api.calls == [] and api.records.asked == []


def test_check_games_cancelled_part_way_stops_asking(isolated):
    _gba_box()
    api, cancel, seen = AuditApi(delay=0.05), threading.Event(), []

    def progress(system, name):
        seen.append(name)
        if len(seen) == 3:
            cancel.set()
    rommlib.audit(api, {"gba": [1]}, progress=progress, cancel=cancel)
    time.sleep(0.2)
    assert len(seen) == 3 and len(api.records.asked) < 20


# ------------------------------------------------------------ BIOS check

def test_bios_check_asks_every_platform_at_once(isolated, monkeypatch):
    need = {"psx": ["scph.bin"], "saturn": ["sat.bin"], "gba": []}
    monkeypatch.setattr(rommlib, "bios_requirements", lambda: {})
    monkeypatch.setattr(rommlib, "bios_fetched", lambda: {})
    monkeypatch.setattr(rommlib, "bios_required", lambda s: need.get(s, []))
    monkeypatch.setattr(rommlib, "missing_bios", lambda s: [{"file": f, "md5": "m-" + f} for f in need.get(s, [])])
    o = Overlap(0.05)

    class Api:
        def firmware(self, pid):
            def fw(p):
                if p == 12:
                    raise rommlib.RommError("RomM error HTTP 500")
                return [{"md5_hash": "M-SCPH.BIN" if p == 10 else "m-sat.bin", "file_name": "x"}]
            return o(pid, fw)
    rows = {r["system"]: r for r in rommlib.bios_status(Api(), {"psx": [10, 12], "saturn": [11], "gba": [13]},
                                                         {"psx", "saturn", "gba"})}
    assert sorted(o.asked) == [10, 11, 12] and o.most >= 2
    assert [m["file"] for m, _ in rows["psx"]["offers"]] == ["scph.bin"]      # 12 failed: skipped
    assert [m["file"] for m, _ in rows["saturn"]["offers"]] == ["sat.bin"]
    assert "gba" not in rows
    assert rommlib.bios_status(None, {"psx": [10]}, {"psx"})[0]["offers"] == []


# ------------------------------------------------------------ cover loader

@pytest.fixture
def app(isolated, rb, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    a = rb.App(None, headless=True)
    good = os.path.join(rommlib.DATA_DIR, "c.png")
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    pygame.image.save(pygame.Surface((4, 6)), good)
    a.good = good
    return a


def _run_posted(app):
    fns, app.events_from_threads = app.events_from_threads, []
    for fn in fns:
        fn()


def test_covers_load_newest_first_and_skip_what_scrolled_away(app, rb):
    asked = []

    def fetch_cover(rom, large=False):
        asked.append(rom["id"])
        return app.good
    app.api.fetch_cover = fetch_cover
    loader = app.cover_loader
    loader.WORKERS = 0
    for rid in (1, 2, 3):
        assert app.cover({"id": rid}) is None
    assert loader.pending() == 3
    loader.seen[1] -= loader.STALE + 1                   # not drawn for a while: scrolled away
    while loader.run_one(wait=False):
        pass
    _run_posted(app)
    assert asked == [3, 2]                               # newest first; 1 never fetched
    assert app.covers[3].get_size() == rb.cover_fit(__import__("pygame").Surface((4, 6))).get_size() and app.covers[2] is not None
    assert 1 not in app.covers and 1 not in app.cover_order and loader.pending() == 0
    app.cover({"id": 1})                                 # drawn again: asked again
    loader.run_one(wait=False)
    _run_posted(app)
    assert asked == [3, 2, 1] and app.covers[1] is not None


def test_a_cover_pushed_out_while_queued_is_never_loaded_twice_or_kept(app, rb):
    """Past COVER_ENTRIES covers, the oldest leaves memory while its load still waits:
    drawing it again must not end in two loads (the loser came back blank) or
    a Surface nothing tracks; nor may a stale duplicate cancel the new request."""
    asked = []

    def fetch_cover(rom, large=False):
        asked.append(rom["id"])
        return app.good
    app.api.fetch_cover = fetch_cover
    loader = app.cover_loader
    loader.WORKERS = 0
    n = rb.COVER_ENTRIES + 10
    for rid in range(n):
        app.cover({"id": rid})
    assert 0 not in app.covers                           # pushed out, its load still queued
    app.cover({"id": 0})                                 # drawn again
    for rid in range(n):
        loader.seen[rid] = time.monotonic()
    while loader.run_one(wait=False):
        pass
    _run_posted(app)
    assert asked.count(0) == 1 and len(asked) == len(set(asked))
    assert set(app.covers) == set(app.cover_order) and len(app.cover_order) <= rb.COVER_ENTRIES
    assert app.covers[0] is not None and app.cover_req == {}


def test_covers_forgotten_on_reconnect_ignore_loads_on_their_way(app):
    app.api.fetch_cover = lambda rom, large=False: app.good
    app.cover_loader.WORKERS = 0
    for rid in range(5):
        app.cover({"id": rid})
    app.forget_covers()                                  # new server / cache cleared
    while app.cover_loader.run_one(wait=False):
        pass
    _run_posted(app)
    assert app.covers == {} and app.cover_order == [] and app.cover_req == {}


def test_a_stale_request_never_cancels_a_newer_one(app):
    app.api.fetch_cover = lambda rom, large=False: app.good
    loader = app.cover_loader
    loader.WORKERS = 0
    app.cover({"id": 1})
    old = loader.todo[-1]
    app.forget_covers()
    app.cover({"id": 1})                                 # a new request for the same game
    loader.seen[1] -= loader.STALE + 1
    loader.todo.insert(0, loader.todo.pop())             # the new one waits behind the old
    loader.todo.append(old)
    loader.run_one(wait=False)                           # the old one comes up first: ignored
    _run_posted(app)
    assert app.covers.get(1) == "loading" and 1 in app.cover_req


def test_a_cover_that_fails_isnt_asked_again_straight_away(app):
    calls = []

    def fetch_cover(rom, large=False):
        calls.append(1)
        raise OSError("disk")
    app.api.fetch_cover = fetch_cover
    app.cover_loader.WORKERS = 0
    app.cover({"id": 9})
    app.cover_loader.run_one(wait=False)
    _run_posted(app)
    assert 9 not in app.covers and app.cover({"id": 9}) is None        # "…" for now
    assert app.cover_loader.pending() == 0 and calls == [1]            # after a wait (test_net)


def test_covers_use_a_bounded_set_of_threads(app):
    o = Overlap(0.03)
    app.api.fetch_cover = lambda rom, large=False: o(rom["id"], lambda k: app.good)
    for rid in range(60):
        app.cover({"id": rid})
    deadline = time.time() + 10
    while app.cover_loader.pending() and time.time() < deadline:
        for rid in range(60):
            app.cover_loader.drawn(rid)                  # all still on screen
        time.sleep(0.01)
    _run_posted(app)
    assert app.cover_loader.pending() == 0
    assert o.most <= app.cover_loader.WORKERS and app.cover_loader.threads <= app.cover_loader.WORKERS
    assert sorted(o.asked) == list(range(60)) and all(app.covers[r] is not None for r in range(60))


def test_the_screenshot_harness_waits_for_covers(app):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "settle", os.path.join(os.path.dirname(__file__), "..", "tools", "settle.py"))
    settle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(settle)
    settled = settle.install(app)
    app.api.fetch_cover = lambda rom, large=False: app.good
    app.cover_loader.WORKERS = 0
    app.cover({"id": 4})
    assert not settled()
    app.cover_loader.run_one(wait=False)
    assert not settled()                                 # its result still waits for the main loop
    _run_posted(app)
    assert settled()
