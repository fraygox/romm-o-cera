"""rb_finalize's AdaptiveFetcher: RomM's media for the games coming up is fetched a few at a
time, the width found as it goes (wider while files wait on the network, narrow when the
CPU or the box is the limit), videos one at a time, memory bounded; ES still gets the
games one after another, the same files as before."""
import json
import os
import threading
import time

import pytest

import rommlib
import rb_finalize
from test_es_media import FakeEsApi, JPG, MP4, PNG, all_files, rom_54, served  # noqa: F401 (fixture)

F = rb_finalize.AdaptiveFetcher


def _box(idle_share):
    """_cpu_times for a box whose CPU is this idle, whatever runs on the test machine."""
    t = [0, 0]

    def times():
        t[0] += int(1000 * idle_share)
        t[1] += 1000
        return tuple(t)
    return times


@pytest.fixture(autouse=True)
def _idle_box(monkeypatch):
    """An idle box (parallel test workers load the real one): the busy-box guard has its own test."""
    monkeypatch.setattr(rb_finalize, "_cpu_times", _box(0.9))


def _drain(f, keys, take_pause=0.0):
    """Take every key in order, as apply() does; returns the widest the fetcher got."""
    widest = 0
    for k in keys:
        widest = max(widest, f.limit)
        f.take(k)
        if take_pause:
            time.sleep(take_pause)
    return max(widest, f.limit)


def test_it_widens_while_files_wait_on_the_network():
    f = F()
    keys = list(range(240))
    for k in keys:
        f.submit(k, lambda: (time.sleep(0.03), (b"x", 1))[1])
    t = time.monotonic()
    widest = _drain(f, keys)
    took = time.monotonic() - t
    f.close()
    assert widest >= 8
    assert took < len(keys) * 0.03 / 4                       # at least 4x one at a time


def test_it_stays_narrow_when_the_cpu_is_the_limit(monkeypatch):
    """Work that holds Python's lock (decoding, converting): more threads would only wait
    for each other, so the width doesn't grow past a couple."""
    clock = time.monotonic                     # this process uses all of a core, whatever else the machine runs
    monkeypatch.setattr(rb_finalize.time, "process_time", clock)

    def busy():
        end = clock() + 0.01
        n = 0
        while clock() < end:
            n += 1
        return b"x", 1
    f = F()
    keys = list(range(160))
    for k in keys:
        f.submit(k, busy)
    widest = _drain(f, keys)
    f.close()
    assert widest <= 3


def test_it_stays_narrow_on_a_box_whose_cpu_is_busy(monkeypatch):
    """Other programs keep the box's CPU nearly full: no more threads (it doesn't grow), and
    with none to spare at all it gives some back."""
    monkeypatch.setattr(rb_finalize, "_cpu_times", _box(0.02))
    f = F()
    keys = list(range(120))
    for k in keys:
        f.submit(k, lambda: (time.sleep(0.02), (b"x", 1))[1])
    widest = _drain(f, keys)
    f.close()
    assert widest <= F.START and f.limit == F.LO


def test_it_backs_off_when_files_slow_down():
    f = F()
    slow = threading.Event()
    keys = list(range(190))
    for k in keys:
        f.submit(k, lambda: (time.sleep(0.1 if slow.is_set() else 0.01), (b"x", 1))[1])
    _drain(f, keys[:150])
    before = f.limit
    slow.set()
    _drain(f, keys[150:])
    f.close()
    assert before >= 4 and f.limit < before


def test_a_busy_box_from_its_load_average_alone_doesnt_hold_it_back(monkeypatch):
    """The load average lags and counts this process's own recent work (a 1-CPU box read
    1.6 while 84% idle): only the CPU it really has spare counts."""
    monkeypatch.setattr(os, "getloadavg", lambda: (8.0 * (os.cpu_count() or 1), 0, 0))
    f = F()
    keys = list(range(240))
    for k in keys:
        f.submit(k, lambda: (time.sleep(0.03), (b"x", 1))[1])
    widest = _drain(f, keys)
    f.close()
    assert widest >= 8


def test_the_box_cpu_reading():
    idle, total = rb_finalize._cpu_times()
    assert 0 <= idle <= total and total > 0


def test_videos_come_one_at_a_time_and_memory_stays_bounded(monkeypatch):
    monkeypatch.setattr(rb_finalize, "VIDEO_MAX", 1 << 20)
    on, most, held_most = [0], [0], [0]
    lock = threading.Lock()
    f = F(budget=4 << 20)

    def video():
        with lock:
            on[0] += 1
            most[0] = max(most[0], on[0])
        time.sleep(0.02)
        with lock:
            on[0] -= 1
        return b"v", 1 << 20

    def image():
        time.sleep(0.005)
        held_most[0] = max(held_most[0], f.held)
        return b"i", 512 << 10
    keys = []
    for i in range(40):
        f.submit(("v", i), video, heavy=True)
        f.submit(("i", i), image)
        keys += [("v", i), ("i", i)]
    _drain(f, keys, take_pause=0.002)
    f.close()
    assert most[0] == 1
    assert held_most[0] <= (4 << 20) + (1 << 20)


def test_it_never_waits_for_memory_only_the_caller_can_free():
    """Later results fill the budget while the one the caller wants hasn't started: take()
    hands it back (None, the caller fetches it) instead of waiting for room only taking
    those later results would make."""
    f = F(budget=1 << 20)
    f.submit("later", lambda: (b"x", 2 << 20))              # fills the budget at once
    time.sleep(0.2)
    f.submit("wanted", lambda: (b"w", 1))
    t = time.monotonic()
    assert f.take("wanted") is None and time.monotonic() - t < 1
    assert f.take("later") == b"x"
    f.close()


def test_the_job_the_caller_wants_starts_next():
    f = F()
    for k in range(30):
        f.submit(k, lambda k=k: (time.sleep(0.05), (k, 1))[1])
    t = time.monotonic()
    assert f.take(29) == 29 and time.monotonic() - t < 0.5   # not after the 29 before it
    f.close()


# ------------------------------------------------------------ apply() with the fetcher

def _games(n):
    """n NES games on the box, ES lists them, RomM has every kind of media for each."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, "nes")
    os.makedirs(sysdir, exist_ok=True)
    games, roms = {}, []
    for i in range(n):
        rom = json.loads(json.dumps(rom_54()).replace("/2/3/", "/2/%d/" % (100 + i)))
        rom.update(id=100 + i, name="Lorem %d" % i, fs_name="Lorem %d.zip" % i, fs_name_no_ext="Lorem %d" % i)
        path = os.path.join(sysdir, rom["fs_name"])
        open(path, "wb").close()
        games[path] = {"id": "g%d" % i, "path": path, "name": "Lorem %d" % i}
        roms.append((rom, path))
    return games, roms


def test_apply_fetches_ahead_but_es_gets_the_same_files_in_the_same_order(served, monkeypatch):
    files, asked = served
    real = rb_finalize.fetch_bytes

    def slow(api, path, limit=200 << 20, info=None):          # a RomM 20 ms away
        time.sleep(0.02)
        return real(api, path, limit, info)
    monkeypatch.setattr(rb_finalize, "fetch_bytes", slow)
    results = {}
    for parallel in (False, True):
        games, roms = _games(12)
        for rom, _ in roms:
            files.update(all_files(rom))
        esapi = FakeEsApi(games)
        order = []
        real_set = esapi.set_media
        esapi.set_media = lambda s, gid, tag, data, ctype: (order.append((gid, tag)),
                                                            real_set(s, gid, tag, data, ctype))[1]
        entries = [rommlib.gamelist_entry(rom, "nes", [p]) for rom, p in roms]
        t = time.monotonic()
        assert rb_finalize.apply(entries, object(), {"nes": {".zip"}}, esapi, {}, parallel=parallel) == []
        results[parallel] = (order, sorted(esapi.media), time.monotonic() - t)
    assert results[True][0] == results[False][0]               # every upload, in the same order
    assert results[True][1] == results[False][1]
    assert results[True][2] < results[False][2] / 2            # and it took well under half the time
