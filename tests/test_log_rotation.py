"""romm-browser.log rotation: the app and its workers append at once."""
import glob
import multiprocessing
import os

import rommlib
import rb_finalize


def _logs():
    return sorted(glob.glob(rommlib.LOG_PATH + "*"))


def _lines(path):
    with open(path) as f:
        return [line.split(" ", 2)[2].rstrip("\n") for line in f]


def test_log_rotates_when_a_line_takes_it_past_the_limit(isolated, monkeypatch):
    monkeypatch.setattr(rommlib, "LOG_MAX", 200)
    line = len(b"2026-01-01 00:00:00 ") + 10 + 1                   # 31 bytes per line
    for i in range(200 // line):                                    # 6 lines: 186 bytes
        rommlib.log("line %05d" % i)
    assert not os.path.exists(rommlib.LOG_PATH + ".1")
    rommlib.log("line %05d" % 6)                                    # 217 >= 200: rotated
    assert _lines(rommlib.LOG_PATH + ".1") == ["line %05d" % i for i in range(7)]
    assert not os.path.exists(rommlib.LOG_PATH)
    rommlib.log("next")
    assert _lines(rommlib.LOG_PATH) == ["next"]


def test_log_keeps_the_live_file_and_nine_old_ones(isolated, monkeypatch):
    monkeypatch.setattr(rommlib, "LOG_MAX", 100)
    for i in range(301):                       # the last line starts a new live file
        rommlib.log("line %05d" % i)
    names = [os.path.basename(p) for p in _logs() if not p.endswith(".lock")]
    assert sorted(names) == sorted(["romm-browser.log"] + ["romm-browser.log.%d" % i for i in range(1, 10)])
    # .9 is the oldest kept, .1 the newest old one, then the live file; nothing in between is lost
    kept = sum((_lines(rommlib.LOG_PATH + ".%d" % i) for i in range(9, 0, -1)), []) + _lines(rommlib.LOG_PATH)
    assert kept == ["line %05d" % i for i in range(301 - len(kept), 301)]


def test_log_a_writer_that_saw_the_old_file_full_does_not_rotate_again(isolated, monkeypatch):
    monkeypatch.setattr(rommlib, "LOG_MAX", 100)
    for i in range(4):
        rommlib.log("line %05d" % i)                                 # 124 bytes: rotated
    rommlib.log("fresh")
    rommlib._rotate_log()            # another process found the old file full and only now got the lock
    assert _lines(rommlib.LOG_PATH) == ["fresh"] and not os.path.exists(rommlib.LOG_PATH + ".2")


def _writer(n, count):
    for i in range(count):
        rommlib.log("w%d %05d %s" % (n, i, "x" * (i % 7 * 1000)))   # some lines past a pipe/buffer size


def test_log_concurrent_writers_lose_nothing_and_rotate_once_each_time(isolated, monkeypatch):
    monkeypatch.setattr(rommlib, "LOG_MAX", 64 * 1024)
    monkeypatch.setattr(rommlib, "LOG_KEEP", 1000)                  # keep everything, to count it
    ctx = multiprocessing.get_context("fork")
    procs = [ctx.Process(target=_writer, args=(n, 400)) for n in range(6)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
        assert p.exitcode == 0
    files = [p for p in _logs() if not p.endswith(".lock")]
    assert len(files) > 10
    got = sum((_lines(p) for p in files), [])
    assert sorted(got) == sorted("w%d %05d %s" % (n, i, "x" * (i % 7 * 1000)) for n in range(6) for i in range(400))
    for p in files:
        if p != rommlib.LOG_PATH:                                    # a second rotation would leave a tiny one
            assert os.path.getsize(p) >= rommlib.LOG_MAX, p


def test_set_aside_queue_lines_keep_only_the_newest(isolated, monkeypatch):
    monkeypatch.setattr(rb_finalize, "BAD_MAX", 1000)
    os.makedirs(rommlib.DATA_DIR)
    p = os.path.join(rommlib.DATA_DIR, "q.jsonl")
    for i in range(50):
        with open(p, "w") as f:
            f.write('{"torn %03d %s\n' % (i, "y" * 40))
        assert rb_finalize._read_jsonl(p) == []
    bad = open(p + ".bad").read()
    assert len(bad) <= 1000 and bad.endswith("\n")
    lines = bad.splitlines()
    assert lines[-1].startswith('{"torn 049') and all(line.startswith('{"torn ') for line in lines)
    assert len(lines) == 1000 // len(lines[0] + "\n")
