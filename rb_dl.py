#!/usr/bin/env python3
"""Background downloader: finishes RomM Browser's queue after the app exits.

Spawned detached by the app on exit (when background_downloads is on). One
instance (flock on downloads.lock). Works through download-queue.json with the
same pipeline as the app (resume via Range, extract, CHD hand-off, gamelist
hand-off), keeps going while you play, and writes downloads-status.json. When
the app starts again it touches downloads.stop: this worker pauses its current
transfer (the .part stays) or unpack (its private folder is swept), lets a game
whose files are being moved into place finish that, saves the queue and exits,
and the app takes over.
When the queue is empty it starts the CHD worker if needed and merges gamelist
entries once ES is idle.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rommlib  # noqa: E402


# Handing the queue back to the app: a transfer stops at its next chunk (its
# .part resumes) and an unpack at its next chunk too (it is unpacked into a
# private folder that the next attempt sweeps; the archive stays), so
# HANDBACK_WAIT only covers a stuck socket or disk. The one step that mustn't be
# cut short is moving an unpacked game's files into place (a rename per file):
# the status file says "moving" then, and both this worker and the app's
# handover() wait for it, up to MOVE_WAIT. (A move killed half-way anyway is
# finished by the next attempt: the downloader leaves a marker for it.)
HANDBACK_WAIT = 15
MOVE_WAIT = 30
STATUS_FRESH = 10          # seconds: an older status file is a worker that's gone


def _moving(d):
    # next release: d.moving_jobs() (an old rommlib's Job has no "moving": never moving)
    return next((j for j in d.jobs if getattr(j, "moving", False)), None)


def write_status(d):
    j = next((j for j in d.jobs if j.state == rommlib.Job.DOWNLOADING), None)
    m = _moving(d)
    st = {"pending": d.pending_count(), "current": j.name if j else None,
          "done_bytes": j.done_bytes if j else 0, "total_bytes": j.total_bytes if j else 0,
          "moving": m.name if m else None, "updated": time.time()}
    rommlib.atomic_write_json(rommlib.DL_STATUS, st, sync=False)     # rewritten every second


# problems the queue can't get past without the player: a message box (once
# per run), not just a popup that goes by
ALERTS = {
    "space": "RomM-o-Cera: background downloads are waiting for disk space. "
             "Free %s on the games drive and they carry on by themselves.",
    "token": "RomM-o-Cera: RomM no longer accepts this box's token, so background downloads "
             "stopped. Open RomM-o-Cera and pair it again (Settings > Pair with RomM).",
}


def _rejected(message):
    m = message or ""
    return m.startswith(("RomM rejected the token", "RomM refused:")) or m in ("Download failed: HTTP 401", "Download failed: HTTP 403")


class Notes:
    """Finished/failed downloads, shown as one EmulationStation popup once
    ES is back in its menus (a popup during a game would go unseen); a
    problem only the player can fix (no space, token refused) as a message
    box, once."""

    def __init__(self, cfg):
        self.on = cfg.get("notify", rommlib.DEFAULT_CONFIG["notify"])
        self.ready, self.failed = [], []
        self.seen = set()
        self.alerts = {}               # ALERTS key -> text, waiting to be shown
        self.alerted = set()

    def collect(self, d):
        short = max((j.short_space or 0 for j in d.jobs if j.state == rommlib.Job.QUEUED), default=0)
        if short and "space" not in self.alerted:
            self.alerts["space"] = ALERTS["space"] % rommlib.human_size(short)
        elif not short:
            self.alerts.pop("space", None)         # room again before anyone saw it
        for j in d.jobs:
            if id(j) in self.seen or getattr(j, "restored", False):    # restored: an earlier run's
                continue
            if j.state == rommlib.Job.DONE:
                self.ready.append(j.name)
            elif j.state == rommlib.Job.FAILED:
                self.failed.append(j.name)
                if _rejected(j.message) and "token" not in self.alerted:
                    self.alerts["token"] = ALERTS["token"]
            else:
                continue
            self.seen.add(id(j))

    def text(self):
        def names(xs):
            return ", ".join(xs[:3]) + (" +%d more" % (len(xs) - 3) if len(xs) > 3 else "")
        parts = []
        if self.ready:
            parts.append("%d game%s ready: %s" % (len(self.ready), "" if len(self.ready) == 1 else "s",
                                                  names(self.ready)))
        if self.failed:
            parts.append("%d failed: %s" % (len(self.failed), names(self.failed)))
        return "RomM-o-Cera: " + "; ".join(parts)

    def flush(self, force=False):
        if not (self.alerts or self.on and (self.ready or self.failed)):
            return
        if not force and rommlib.es_running_game() != "":
            return                      # a game is up (or ES unreachable): later
        for k, text in list(self.alerts.items()):     # shown whatever "notify" says: they need the player
            if rommlib.es_messagebox(text):
                self.alerted.add(k)
                del self.alerts[k]
        if self.on and (self.ready or self.failed) and rommlib.es_notify(self.text()):
            self.ready, self.failed = [], []


def main():
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    with rommlib.try_lock(rommlib.DL_LOCK) as fd:
        if fd is None:
            return                               # another worker is already on it
        if os.path.exists(rommlib.DL_STOP):
            os.remove(rommlib.DL_STOP)
        cfg = rommlib.load_config()
        held = rommlib.Downloader.saved_held()
        if held:
            rommlib.log("dl-worker: queue is paused (Pause all); not starting")
        else:
            notes = download(cfg)
    if held:
        after_downloads(cfg, None)           # earlier games' CHD + game info still go ahead
    elif notes is not None:                      # None: handed back to the app
        after_downloads(cfg, notes)


def download(cfg):
    """Work through the saved queue (under DL_LOCK). Returns this run's Notes,
    or None when the app came back and took the queue over."""
    d = rommlib.Downloader(rommlib.Api.from_config(cfg), rommlib.Library(), cfg)
    d.allow_overlap = False      # mostly runs while a game plays: one disk job at a time
    n = d.resume()
    rommlib.log("dl-worker: %d queued" % n)
    cap = float(cfg.get("speed_while_playing", rommlib.DEFAULT_CONFIG["speed_while_playing"]) or 0) * 1e6
    notes = Notes(cfg)
    stopped = False
    tick = 0
    while d.active_count():
        if os.path.exists(rommlib.DL_STOP):      # the app is back: hand over
            hand_back(d)
            stopped = True
            break
        if tick % 5 == 0:                        # every ~5 s: game running? cap / popups
            playing = rommlib.es_running_game()
            d.rate_limit = cap if (cap and playing) else 0
            notes.collect(d)
            if playing == "":
                notes.flush()
        tick += 1
        write_status(d)
        time.sleep(1)
    d.save_queue()
    try:
        os.remove(rommlib.DL_STATUS)
    except OSError:
        pass
    done = sum(1 for j in d.jobs if j.state == rommlib.Job.DONE and not getattr(j, "restored", False))
    rommlib.log("dl-worker: %s (%d done)" % ("handed back to the app" if stopped else "queue finished", done))
    if os.path.exists(rommlib.DL_STOP):
        os.remove(rommlib.DL_STOP)
    if stopped:
        return None
    notes.collect(d)
    return notes


def hand_back(d):
    """Pause everything and wait until the jobs have stopped: HANDBACK_WAIT,
    or MOVE_WAIT while a game's files are being moved into place. Keeps the
    status file fresh meanwhile, so the app's handover() knows to keep waiting
    for a move. Returns True if all stopped."""
    d.pause_all()
    t0 = shown = time.time()
    said = False
    while d.active_count():
        now = time.time()
        m = _moving(d)
        if m is not None and not said:
            rommlib.log("dl-worker: letting %s finish moving into place before handing back" % m.name)
            said = True
        if now - t0 >= (MOVE_WAIT if m is not None else HANDBACK_WAIT):
            rommlib.log("dl-worker: %d job(s) still going after %d s; handing back anyway"
                        % (d.active_count(), now - t0))
            return False
        if now - shown >= 1:
            write_status(d)
            shown = now
        time.sleep(0.2)
    return True


def after_downloads(cfg, notes):
    """CHD-compress and hand game info to ES for whatever is queued — this
    run's downloads or earlier ones (the app leaves them to us on exit)."""
    rommlib.start_conversions()
    import rb_finalize
    rb_finalize.main()                           # waits until ES is idle; no-op with nothing queued
    if notes is not None:
        if notes.alerts or notes.on and (notes.ready or notes.failed):
            rb_finalize.wait_idle()              # (finalize may have had nothing to wait for)
        notes.flush()                            # games are listed now: tell the player


def _status_moving():
    """The game the running worker says it's moving into place (its status
    file is fresh), else None. An older worker never says so."""
    try:
        with open(rommlib.DL_STATUS) as f:
            st = json.load(f)
        if time.time() - float(st.get("updated") or 0) < STATUS_FRESH:
            return st.get("moving") or None
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return None


def handover(timeout=30, unpack_timeout=None, waiting=None):
    """Called by the app at start: if a worker is running, ask it to stop and
    wait until it has released the lock, up to timeout; a little longer (up
    to MOVE_WAIT more) while its status says a game's files are being moved
    into place. Returns True if one was running.
    unpack_timeout and waiting are what an app of the previous build passes
    (it waited for whole unpacks): taken, and no longer needed."""
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    with rommlib.try_lock(rommlib.DL_LOCK) as fd:
        if fd is not None:
            return False                         # nobody holds it
    open(rommlib.DL_STOP, "w").close()
    t0 = time.time()
    said = False
    while True:
        with rommlib.try_lock(rommlib.DL_LOCK) as fd:
            if fd is not None:
                return True
        waited = time.time() - t0
        if waited >= timeout + MOVE_WAIT:
            break
        if waited >= timeout:
            name = _status_moving()
            if not name:
                break
            if not said:
                rommlib.log("handover: waiting for the background worker to finish moving %s into place" % name)
                said = True
        time.sleep(0.2)
    rommlib.log("handover: the background worker didn't stop in %d s" % (time.time() - t0))
    return True


if __name__ == "__main__":
    main()
