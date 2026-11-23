"""Acting on thousands of downloads at once (a whole platform queued, #2) stays quick."""
import threading

import rommlib


def _held_queue(n):
    d = rommlib.Downloader(None, rommlib.Library(), dict(rommlib.DEFAULT_CONFIG), persist=False)
    for i in range(n):
        j = rommlib.Job({"id": i, "name": "G%d" % i, "fs_name": "G%d.zip" % i}, "nes")
        d.jobs.append(j)
    d.hold = True
    return d


def test_cancelling_thousands_under_pause_all_checks_for_an_empty_queue_once(isolated, monkeypatch):
    """Each cancel read every job to see whether Pause all could end: 4,000 cancels froze the
    screen for seconds (the #8 flow: Pause all, pick all, Cancel)."""
    d = _held_queue(2000)
    calls = []
    real = d._unhold_if_empty
    me = threading.current_thread()                    # (the download worker looks too, on its own)
    monkeypatch.setattr(d, "_unhold_if_empty",
                        lambda: (calls.append(1) if threading.current_thread() is me else None) or real())
    with d.one_save():
        for j in list(d.jobs):
            d.cancel_job(j)
    assert len(calls) == 1 and d.hold is False          # still ends once nothing is left
    d.close()


def test_a_single_cancel_still_ends_pause_all_at_once(isolated):
    d = _held_queue(1)
    d.cancel_job(d.jobs[0])
    assert d.hold is False
    d.close()
