"""The saved download queue as a whole copy plus a log of changes (core.read_queue,
Downloader._write_queue): what it reads back is always what the whole-file writer would
have written, an older release finds a whole file once the app is done with it, and a
big queue costs a few lines a change instead of a rewrite."""
import json
import os
import random

import rommlib

J = rommlib.Job


def _dl():
    d = rommlib.Downloader(None, rommlib.Library(), dict(rommlib.DEFAULT_CONFIG))
    with d.cv:
        d.hold = True                         # nothing starts: only the queue's bookkeeping
    return d


def _rom(i):
    return {"id": i, "name": "Game %d" % i, "fs_name": "G%d.zip" % i, "fs_name_no_ext": "G%d" % i,
            "fs_size_bytes": 1000 + i, "platform_id": 3, "summary": "Lorem %d" % i}


def _spy(d, monkeypatch):
    """The (held, jobs, history) each save meant to write."""
    meant = []
    real = d._write_queue

    def spy(held, pend, done):
        meant.append(json.loads(json.dumps({"held": held, "jobs": pend, "history": done})))
        real(held, pend, done)
    monkeypatch.setattr(d, "_write_queue", spy)
    return meant


def _reference(d):
    """What the whole-file writer (before the log and its caches) wrote for this queue: each job's
    live fields, nothing remembered from an earlier save."""
    pend = [{"rom": dict({k: j.rom.get(k) for k in d.SLIM_KEYS}, _es_media=rommlib.es_media(j.rom)),
             "system": j.system, "group": j.group, "paused": bool(j.user_paused), "replace": j.replace or None,
             "replace_pins": getattr(j, "replace_pins", None) or None,
             "made": list(j.made) or None, "checked": list(getattr(j, "checked", None) or []) or None}
            for j in list(d.jobs) if j.active or j.state == J.PAUSED]
    done = [{"rom": {k: j.rom.get(k) for k in d.HISTORY_KEYS if j.rom.get(k) is not None},
             "system": j.system, "group": j.group, "state": j.state, "message": j.message,
             "verified": j.verified, "chd_task": j.chd_task, "chd_mode": getattr(j, "chd_mode", None)}
            for j in list(d.jobs) if j.state in J.FINISHED][-rommlib.HISTORY_KEEP:]
    return json.loads(json.dumps({"held": bool(d.hold), "jobs": pend, "history": done}))


def _as_read():
    got = rommlib.read_queue()
    return {k: got[k] for k in ("held", "jobs", "history")}


def test_what_it_reads_back_is_what_the_whole_writer_wrote(isolated, monkeypatch):
    """Random runs of every kind of change; after each save the queue read back (copy +
    log) equals what that save meant, made/checked/paused included."""
    rng = random.Random(5)
    monkeypatch.setattr(rommlib, "in_download_hours", lambda cfg: False)   # Resume all starts nothing either
    d = _dl()
    meant = _spy(d, monkeypatch)
    try:
        nxt = 1
        for step in range(400):
            jobs = list(d.jobs)
            op = rng.choice(["add", "add", "finish", "finish", "fail", "made", "listing", "pause", "resume",
                             "remove", "move", "clear", "hold", "retry"])
            if op == "add":
                n = rng.randint(1, 6)
                d.enqueue_many([(_rom(i), "gba", None) for i in range(nxt, nxt + n)])
                nxt += n
                continue
            live = [j for j in jobs if j.active]
            if op in ("finish", "fail") and live:
                j = rng.choice(live)
                with d.cv:
                    j.state = J.DONE if op == "finish" else J.FAILED
                    j.message = "Ready" if op == "finish" else "Checksum doesn't match"
                d._settled(j)
            elif op == "made" and live:
                j = rng.choice(live)
                j.made = j.made + ["/r/gba/%s.part%d" % (j.rom["fs_name"], step)]
                j.checked = j.made[:1]
                d.save_queue()
            elif op == "listing" and live:             # zip_listing writes into the record in place
                j = rng.choice(live)
                j.rom["_members"] = {j.rom["fs_name"]: [{"name": "g%d.bin" % step, "size": step}]}
                j.touch()
                d.save_queue()
            elif op == "pause" and live:
                d.pause_job(rng.choice(live))
            elif op == "resume":
                held = [j for j in jobs if j.state in (J.PAUSED, J.FAILED, J.CANCELLED)]
                if held:
                    d.resume_job(rng.choice(held))
            elif op == "remove" and jobs:
                d.remove(rng.choice(jobs))
            elif op == "move" and live:
                d.move(rng.choice(live), rng.choice(["top", "up", "down"]))
            elif op == "clear":
                d.clear_finished()
            elif op == "hold":
                d.hold = not d.hold
                d.save_queue()
            elif op == "retry":
                gone = [j for j in jobs if j.state in J.FINISHED]
                if gone:
                    j = rng.choice(gone)
                    d.enqueue_many([(j.rom, j.system, None)])
            if meant:
                assert _as_read() == meant[-1] == _reference(d), "step %d (%s)" % (step, op)
        assert os.path.getsize(rommlib.queue_changes_path()) > 0, "the run never used the log"
    finally:
        d.close()
    # done with it: one whole file, as releases before the log read it
    whole = json.load(open(rommlib.QUEUE_PATH))
    assert {k: whole[k] for k in ("held", "jobs", "history")} == _as_read() == meant[-1] == _reference(d)
    assert os.path.getsize(rommlib.queue_changes_path()) == 0


def test_a_save_that_only_finishes_jobs_appends_a_line(isolated):
    d = _dl()
    try:
        d.enqueue_many([(_rom(i), "gba", None) for i in range(1, 201)])
        size = os.path.getsize(rommlib.QUEUE_PATH)
        stamp = os.stat(rommlib.QUEUE_PATH).st_mtime_ns
        for j in list(d.jobs)[:20]:
            with d.cv:
                j.state = J.DONE
            d._settled(j)
        assert os.stat(rommlib.QUEUE_PATH).st_mtime_ns == stamp          # the copy wasn't written again
        log = os.path.getsize(rommlib.queue_changes_path())
        assert 0 < log < size / 4
        assert [e["rom"]["id"] for e in rommlib.read_queue()["jobs"]] == list(range(21, 201))
    finally:
        d.close()


def test_whole_run_writes_stay_small(isolated, monkeypatch):
    """Finishing 600 queued games one by one: the queue's bytes written stay a few KB a game
    (a whole rewrite a change was ~300 KB a game here, and grows with the queue)."""
    written = []
    real = rommlib.atomic_write_json
    monkeypatch.setattr(rommlib, "atomic_write_json",
                        lambda p, o, *a, **k: (real(p, o, *a, **k), written.append(os.path.getsize(p))))
    d = _dl()
    try:
        d.enqueue_many([(_rom(i), "gba", None) for i in range(1, 601)])
        first = sum(written)
        for j in list(d.jobs):
            with d.cv:
                j.state = J.DONE
            d._settled(j)
        log_and_copies = sum(written) - first + os.path.getsize(rommlib.queue_changes_path())
        assert log_and_copies < 600 * 8 * 1024, log_and_copies
    finally:
        d.close()


def test_a_torn_last_line_ends_the_replay(isolated):
    d = _dl()
    try:
        d.enqueue_many([(_rom(i), "gba", None) for i in range(1, 6)])
        j = d.jobs[0]
        with d.cv:
            j.state = J.DONE
        d._settled(j)
        before = rommlib.read_queue()
        with open(rommlib.queue_changes_path(), "a") as f:
            f.write('{"g":"x","x":[2')                  # the box died mid-write
        assert rommlib.read_queue() == before
    finally:
        d.close()


def test_an_older_release_writing_the_whole_file_makes_the_log_stale(isolated):
    """A copy without a gen (any release before the log) is read as it is; the lines left
    beside it belong to another copy."""
    d = _dl()
    try:
        d.enqueue_many([(_rom(i), "gba", None) for i in range(1, 6)])
        j = d.jobs[0]
        with d.cv:
            j.state = J.DONE
        d._settled(j)
        assert os.path.getsize(rommlib.queue_changes_path()) > 0
        old = {"held": False, "jobs": [{"rom": _rom(9), "system": "gba"}], "history": []}
        json.dump(old, open(rommlib.QUEUE_PATH, "w"))
        assert rommlib.read_queue() == old
        # this process notices the file isn't the one it wrote: its next save is whole again
        d.save_queue()
        assert os.path.getsize(rommlib.queue_changes_path()) == 0
        assert [e["rom"]["id"] for e in json.load(open(rommlib.QUEUE_PATH))["jobs"]] == [2, 3, 4, 5]
    finally:
        d.close()


def test_resume_reads_the_log_and_close_leaves_it_whole(isolated):
    d = _dl()
    d.enqueue_many([(_rom(i), "gba", None) for i in range(1, 11)])
    for j in list(d.jobs)[:3]:
        with d.cv:
            j.state = J.DONE
        d._settled(j)
    d.pause_job(d.jobs[5])
    d.jobs[6].made = ["/r/gba/G7.zip"]
    d.save_queue()
    rommlib.Downloader.instances.remove(d)           # as if the process died: no close, no fold
    d2 = _dl()
    try:
        assert d2.resume() == 7
        assert [j.rom["id"] for j in d2.jobs if j.active or j.state == J.PAUSED] == list(range(4, 11))
        assert {j.rom["id"]: j.state for j in d2.jobs}[6] == J.PAUSED
        assert {j.rom["id"]: j.made for j in d2.jobs}[7] == ["/r/gba/G7.zip"]
        assert [h["rom"]["id"] for h in rommlib.read_queue()["history"]] == [1, 2, 3]
    finally:
        d2.close()
    assert os.path.getsize(rommlib.queue_changes_path()) == 0


def test_enqueue_many_does_what_enqueue_does(isolated):
    """The same starting queue (one of each state) and the same games queued: one at a time
    or all at once, the queue comes out the same."""
    def build():
        d = _dl()
        for i, st in enumerate([J.QUEUED, J.PAUSED, J.FAILED, J.CANCELLED, J.DONE, J.DOWNLOADING], 1):
            j = J(_rom(i), "gba")
            j.state = st
            d.jobs.append(j)
        return d
    want = [(_rom(i), "gba", None) for i in (1, 2, 3, 4, 5, 6, 7, 7)]
    a, b = build(), build()
    try:
        one = [a.enqueue(r, s, g) for r, s, g in want]
        many = b.enqueue_many(want)

        def shape(d, got):
            return ([(j.rom["id"], j.state) for j in d.jobs], [(j.rom["id"], j.state) for j in got])
        assert shape(a, one) == shape(b, many)
        assert len({id(j) for j in many}) == 7                   # the game asked twice: one job
    finally:
        a.close()
        b.close()


def test_job_for_follows_every_change_to_the_list(isolated):
    d = _dl()
    try:
        jobs = [J(_rom(i), "gba") for i in range(1, 6)]
        d.jobs.extend(jobs[:3])
        assert d.job_for(2) is jobs[1] and d.job_for(4) is None
        d.jobs.append(jobs[3])
        assert d.job_for(4) is jobs[3]
        d.jobs.remove(jobs[0])
        assert d.job_for(1) is None
        d.jobs.insert(0, jobs[4])
        assert d.job_for(5) is jobs[4]
        d.jobs[:] = jobs[:2]
        assert d.job_for(5) is None and d.job_for(1) is jobs[0]
        del d.jobs[0]
        assert d.job_for(1) is None
        d.jobs.pop()
        assert d.job_for(2) is None
        d.jobs = [jobs[2]]
        assert d.job_for(3) is jobs[2] and isinstance(d.jobs, type(d.jobs))
        again = J(_rom(3), "gba")
        d.jobs += [again]
        assert d.job_for(3) is again                              # the later one, as a scan from the end
        d.jobs.clear()
        assert d.job_for(3) is None
    finally:
        d.close()


def test_a_finished_job_keeps_only_what_a_restart_would(isolated):
    d = _dl()
    try:
        [j] = d.enqueue_many([(dict(_rom(1), ss_metadata={"synopsis": "x" * 5000}), "gba", None)])
        with d.cv:
            j.state = J.DONE
        d._settled(j)
        assert set(j.rom) <= set(d.HISTORY_KEYS) and j.rom["fs_name"] == "G1.zip"
        assert getattr(j, "_saved", None) is None
    finally:
        d.close()


def test_a_listing_put_in_a_queued_record_reaches_the_saved_queue(isolated):
    """The archive listing lands in the job's record in place: touch() makes the next save carry it."""
    d = _dl()
    try:
        [j] = d.enqueue_many([(_rom(1), "gba", None)])
        assert rommlib.read_queue()["jobs"][0]["rom"]["_members"] is None
        j.rom["_members"] = {"G1.zip": [{"name": "g.bin", "size": 5}]}
        j.touch()
        d.save_queue()
        assert rommlib.read_queue()["jobs"][0]["rom"]["_members"] == {"G1.zip": [{"name": "g.bin", "size": 5}]}
    finally:
        d.close()
