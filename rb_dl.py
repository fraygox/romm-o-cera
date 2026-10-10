#!/usr/bin/env python3
"""Background downloader: finishes RomM Browser's queue after the app exits.

Spawned detached by the app on exit (when background_downloads is on). One
instance (flock on downloads.lock). Works through download-queue.json with the
same pipeline as the app (resume via Range, extract, CHD hand-off, gamelist
hand-off), keeps going while you play, and writes downloads-status.json. When
the app starts again it touches downloads.stop: this worker pauses its current
transfer (the .part stays), saves the queue and exits, and the app takes over.
When the queue is empty it starts the CHD worker if needed and merges gamelist
entries once ES is idle.
"""
import fcntl
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rommlib  # noqa: E402


def write_status(d):
    j = next((j for j in d.jobs if j.state == rommlib.Job.DOWNLOADING), None)
    st = {"pending": d.pending_count(), "current": j.name if j else None,
          "done_bytes": j.done_bytes if j else 0, "total_bytes": j.total_bytes if j else 0,
          "updated": time.time()}
    tmp = rommlib.DL_STATUS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, rommlib.DL_STATUS)


def main():
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    lockf = open(rommlib.DL_LOCK, "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return                                   # another worker is already on it
    if os.path.exists(rommlib.DL_STOP):
        os.remove(rommlib.DL_STOP)
    cfg = rommlib.load_config()
    d = rommlib.Downloader(rommlib.Api.from_config(cfg), rommlib.Library(), cfg)
    n = d.resume()
    rommlib.log("dl-worker: %d queued" % n)
    stopped = False
    while d.active_count():
        if os.path.exists(rommlib.DL_STOP):      # the app is back: hand over
            d.pause_all()
            t0 = time.time()
            while d.active_count() and time.time() - t0 < 15:
                time.sleep(0.2)
            stopped = True
            break
        write_status(d)
        time.sleep(1)
    d.save_queue()
    try:
        os.remove(rommlib.DL_STATUS)
    except OSError:
        pass
    done = sum(1 for j in d.jobs if j.state == rommlib.Job.DONE)
    rommlib.log("dl-worker: %s (%d done)" % ("handed back to the app" if stopped else "queue finished", done))
    if os.path.exists(rommlib.DL_STOP):
        os.remove(rommlib.DL_STOP)
    lockf.close()
    if stopped or not done:
        return
    here = os.path.dirname(os.path.abspath(__file__))
    if cfg.get("chd", True) and os.path.isdir(rommlib.CHD_QUEUE_DIR) and os.listdir(rommlib.CHD_QUEUE_DIR):
        subprocess.Popen([sys.executable, os.path.join(here, "rb_chd.py")], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    import rb_finalize
    rb_finalize.main()


def handover(timeout=15):
    """Called by the app at start: if a worker is running, ask it to stop and
    wait until it has released the lock. Returns True if one was running."""
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    f = open(rommlib.DL_LOCK, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(f, fcntl.LOCK_UN)
        return False                             # nobody holds it
    except OSError:
        pass
    open(rommlib.DL_STOP, "w").close()
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(f, fcntl.LOCK_UN)
            return True
        except OSError:
            time.sleep(0.2)
    return True


if __name__ == "__main__":
    main()
