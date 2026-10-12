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
    assert o.most == 4 and sorted(out) == list(range(12))
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
    assert api.o.most == 3 and len(api.calls) == 4         # first page, then the other three at once
    api = PagedApi([{"id": i} for i in range(3000)])
    assert len(api.roms_all(limit=250)) == 3000 and api.o.most == rommlib.PAGE_WORKERS


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
    pid = max(api.platforms(), key=lambda p: p.get("rom_count") or 0)["id"]
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
    t = time.time()
    after = rommlib.audit(par, {"gba": [1]})
    assert after == before and one.records.most == 1 and par.records.most > 1
    assert time.time() - t < 20 * 0.02                 # not one at a time
    findings, stats = after
    assert stats == {"checked": 21, "matched": 20, "unmatched": 1}
    probs = {os.path.basename(f["path"]): f["problem"] for f in findings}
    assert probs["G7.gba"].startswith("Couldn't fetch RomM record")
    assert probs["G3.gba"].startswith("Smaller") and probs["G11.gba"].startswith("Smaller")
    assert len(findings) == 3
    assert sorted(par.records.asked) == list(range(20))     # each record asked once


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
    t = time.time()
    rows = {r["system"]: r for r in rommlib.bios_status(Api(), {"psx": [10, 12], "saturn": [11], "gba": [13]},
                                                         {"psx", "saturn", "gba"})}
    assert sorted(o.asked) == [10, 11, 12] and o.most == 3 and time.time() - t < 0.15
    assert [m["file"] for m, _ in rows["psx"]["offers"]] == ["scph.bin"]      # 12 failed: skipped
    assert [m["file"] for m, _ in rows["saturn"]["offers"]] == ["sat.bin"]
    assert "gba" not in rows
    assert rommlib.bios_status(None, {"psx": [10]}, {"psx"})[0]["offers"] == []
