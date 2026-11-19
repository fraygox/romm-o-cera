"""installed.json as a whole copy plus a log of changes (library: _read_state, Library.update,
fold_changes): every reader sees every record, a release from before the log finds a whole file
once the app is done with it, and a big library costs a line per change, not a rewrite."""
import json
import os
import random
import subprocess
import sys

import rommlib
from conftest import APP


def _rec(name, paths=()):
    return {"name": name, "system": "psx", "paths": list(paths), "files": {}}


def _whole():
    """installed.json as a release from before the log reads it: the file alone."""
    return json.load(open(rommlib.STATE_PATH))


def _log_lines():
    try:
        return len(open(rommlib.STATE_PATH + ".changes").read().splitlines())
    except OSError:
        return 0


def test_today_s_installed_json_is_read_and_carried_on(isolated):
    """The migration: an installed.json written by an earlier release (no log) is read as it
    is; the first change writes it whole and starts the log, the next ones are log lines."""
    os.makedirs(rommlib.DATA_DIR)
    json.dump({"1": _rec("Old")}, open(rommlib.STATE_PATH, "w"), indent=1)
    lib = rommlib.Library()
    assert list(lib.snapshot()) == ["1"]
    lib.update(lambda inst: inst.update({"2": _rec("Two")}))
    assert set(_whole()) == {"1", "2"} and _log_lines() == 1          # whole, and the log's first line
    lib.update(lambda inst: inst.update({"3": _rec("Three")}))
    assert set(_whole()) == {"1", "2"} and _log_lines() == 2          # a line, not a rewrite
    assert set(rommlib.read_installed()) == {"1", "2", "3"}


def test_two_processes_and_a_reference_agree_through_random_changes(isolated):
    """Two Library objects (the app and rb_chd) take turns making every kind of change: after
    each, both, a fresh reader and a plain dict that made the same changes agree."""
    rng = random.Random(3)
    libs, ref = [rommlib.Library(), rommlib.Library()], {}
    for step in range(300):
        lib = rng.choice(libs)
        k = str(rng.randint(1, 40))
        op = rng.choice(["put", "put", "edit", "scan", "drop", "files"])
        if op == "put":
            r = _rec("G%s-%d" % (k, step), ["/r/%s" % k])

            def fn(inst, k=k, r=r):
                inst[k] = json.loads(json.dumps(r))
        elif op == "edit":
            def fn(inst, k=k):
                rec = inst.get(k)
                if rec:
                    rec["paths"] = rec["paths"] + ["/r/%s.m3u" % k]
        elif op == "scan":                                         # rename_paths' way: every record
            def fn(inst, step=step):
                for rec in inst.values():
                    if rec["paths"] and rng_hit(rec, step):
                        rec["name"] = rec["name"] + "+"
        elif op == "files":                                        # a nested dict changed in place
            def fn(inst, k=k, step=step):
                rec = inst.get(k)
                if rec:
                    rec["files"]["/r/%s" % k] = [step, 1, 2]
        else:
            def fn(inst, k=k):
                inst.pop(k, None)
        lib.update(fn)
        fn(ref)
        assert rommlib.read_installed() == ref, "step %d (%s)" % (step, op)
        for other in libs:
            assert other.snapshot() == ref, "step %d (%s)" % (step, op)
    assert rommlib.Library().snapshot() == ref


def rng_hit(rec, step):
    return (len(rec["name"]) + step) % 3 == 0


def test_folded_it_is_whole_for_an_older_release(isolated):
    lib = rommlib.Library()
    for i in range(20):
        lib.update(lambda inst, i=i: inst.update({str(i): _rec("G%d" % i)}))
    lib.update(lambda inst: inst.pop("3"))
    assert _whole() != rommlib.read_installed()                        # the log holds the newer ones
    assert rommlib.fold_changes() is True
    assert _whole() == rommlib.read_installed() and "3" not in _whole()
    assert _log_lines() == 1 and rommlib.fold_changes() is False      # nothing more to fold


def test_a_process_that_logged_changes_leaves_installed_json_whole_when_it_exits(isolated):
    """rb_chd, rb_dl or the app: whatever it logged is in installed.json once it has exited."""
    code = ("import os, sys; sys.path.insert(0, %r); import rommlib\n"
            "for k, v in {'DATA_DIR': %r, 'STATE_PATH': %r, 'ROMS_ROOT': %r, 'LOG_PATH': %r}.items():\n"
            "    setattr(rommlib, k, v)\n"
            "lib = rommlib.Library()\n"
            "for i in range(5):\n"
            "    lib.update(lambda inst, i=i: inst.update({str(i): {'name': 'G', 'system': 'psx', 'paths': []}}))\n"
            % (APP, rommlib.DATA_DIR, rommlib.STATE_PATH, rommlib.ROMS_ROOT, rommlib.LOG_PATH))
    subprocess.run([sys.executable, "-c", code], check=True, timeout=60)
    assert set(_whole()) == {"0", "1", "2", "3", "4"} and _log_lines() == 1


def test_an_older_release_writing_installed_json_makes_the_log_stale(isolated):
    """A whole installed.json from something that doesn't know the log (a release before it,
    a hand): read as it is. The lines logged against the earlier copy never come back."""
    lib = rommlib.Library()
    lib.update(lambda inst: inst.update({"1": _rec("One")}))
    lib.update(lambda inst: inst.update({"2": _rec("Two")}))           # a log line
    json.dump({"9": _rec("Nine")}, open(rommlib.STATE_PATH, "w"), indent=1)
    assert rommlib.read_installed() == {"9": _rec("Nine")}
    assert lib.snapshot() == {"9": _rec("Nine")}
    lib.update(lambda inst: inst.update({"10": _rec("Ten")}))          # carries on from that copy
    assert set(rommlib.read_installed()) == {"9", "10"} and set(_whole()) == {"9", "10"}


def test_a_touched_date_keeps_the_log(isolated):
    """The log goes by installed.json's contents: a copy tool or a clock fix that only changes
    its date loses nothing."""
    lib = rommlib.Library()
    lib.update(lambda inst: inst.update({"1": _rec("One")}))
    lib.update(lambda inst: inst.update({"2": _rec("Two")}))
    st = os.stat(rommlib.STATE_PATH)
    os.utime(rommlib.STATE_PATH, ns=(st.st_atime_ns, st.st_mtime_ns + 5 * 10 ** 9))
    assert set(rommlib.Library().snapshot()) == {"1", "2"}
    assert set(lib.snapshot()) == {"1", "2"}


def test_a_torn_log_line_is_skipped(isolated):
    lib = rommlib.Library()
    lib.update(lambda inst: inst.update({"1": _rec("One")}))
    lib.update(lambda inst: inst.update({"2": _rec("Two")}))
    with open(rommlib.STATE_PATH + ".changes", "a") as f:
        f.write('{"set": {"3": {"na')                                  # the box died mid-write
    assert set(rommlib.read_installed()) == {"1", "2"}
    lib.update(lambda inst: inst.update({"4": _rec("Four")}))           # after a torn line: whole again
    assert set(rommlib.read_installed()) == {"1", "2", "4"} == set(_whole())


def test_a_lost_installed_json_carries_on_from_bak_and_the_log(isolated):
    lib = rommlib.Library()
    lib.update(lambda inst: inst.update({"1": _rec("One")}))
    lib.update(lambda inst: inst.update({"2": _rec("Two")}))           # logged after .bak was copied
    os.remove(rommlib.STATE_PATH)
    assert set(rommlib.Library().snapshot()) == {"1", "2"}


def test_a_snapshot_s_records_are_never_changed_under_it(isolated):
    lib = rommlib.Library()
    lib.update(lambda inst: inst.update({"1": _rec("One", ["/a"])}))
    snap = lib.snapshot()
    held = snap["1"]
    lib.update(lambda inst: inst["1"]["paths"].append("/b"))
    lib.update(lambda inst: [r.update(name="Renamed") for r in inst.values()])
    assert held == _rec("One", ["/a"])
    assert lib.snapshot()["1"]["paths"] == ["/a", "/b"] and lib.snapshot()["1"]["name"] == "Renamed"


def test_a_big_library_costs_a_line_a_change(isolated, monkeypatch):
    """600 games installed one by one: installed.json's bytes written stay a few KB a game
    (a rewrite a game wrote ~200 MB here, and grows with the square of the library)."""
    written = []
    real = rommlib.atomic_write_json
    monkeypatch.setattr(rommlib, "atomic_write_json",
                        lambda p, o, *a, **k: (real(p, o, *a, **k), written.append(os.path.getsize(p))))
    lib = rommlib.Library()
    meta = {"desc": "x" * 1200}
    for i in range(600):
        lib.update(lambda inst, i=i: inst.update({str(i): dict(_rec("G%d" % i, ["/r/%d" % i]), meta=meta)}))
    total = sum(written) + os.path.getsize(rommlib.STATE_PATH + ".changes")
    assert total < 600 * 6 * 1024, total
    assert len(rommlib.read_installed()) == 600


def test_a_save_that_fails_leaves_memory_to_be_read_again(isolated, monkeypatch):
    lib = rommlib.Library()
    lib.update(lambda inst: inst.update({"1": _rec("One"), "2": _rec("Two")}))

    def full(*a, **k):
        raise OSError(28, "No space left on device")
    import pytest
    with monkeypatch.context() as m:
        m.setattr(lib, "_save", full)
        with pytest.raises(OSError):
            lib.update(lambda inst: inst.pop("1"))
    assert set(lib.snapshot()) == {"1", "2"}                # what's on disk, not the change that never got there


def test_the_conversion_worker_sees_games_only_the_log_holds(isolated):
    import rb_chd
    lib = rommlib.Library()
    lib.update(lambda inst: inst.update({"1": _rec("One", ["/r/psx/One"])}))
    assert "/r/psx/One" in rb_chd.downloaded_paths()
    lib.update(lambda inst: inst.update({"2": _rec("Two", ["/r/psx/Two"])}))     # a log line only
    assert "/r/psx/Two" in rb_chd.downloaded_paths()
