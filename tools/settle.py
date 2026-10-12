"""Settle detection for the headless drivers (tools/shots.py, tools/shoot.py).

Every background load in the app goes through App.bg: a worker thread runs
the call, then its done/err callback is posted to the main loop. install()
wraps App.bg to count those calls (the callbacks themselves run unchanged),
so a driver can move on as soon as nothing is in flight or queued instead of
after a fixed wait. The fixed wait stays the upper bound in both drivers.
"""


def install(app, downloads=False):
    """Wrap app.bg; returns settled(): True when no background load is in
    flight, no callback is waiting for the main loop and no debounced search
    is pending. downloads=True also waits for the download queue to go quiet
    (nothing transferring or unpacking, nothing queued that may start now)."""
    import time
    import rommlib
    real_bg, busy = app.bg, {"n": 0}

    def left():
        with app.lock:
            busy["n"] -= 1

    def counted_bg(fn, done=None, err=None):
        with app.lock:
            busy["n"] += 1

        def fn_():
            try:
                return fn()
            except BaseException:
                app.post(left)               # posted before App.bg posts err / its toast
                raise

        def done_(res):
            try:
                if done:
                    done(res)
            finally:
                left()
        real_bg(fn_, done_, err)

    app.bg = counted_bg
    J = rommlib.Job

    def downloads_quiet():
        d = app.downloader
        now = time.time()
        for j in list(d.jobs):
            if j.state in (J.DOWNLOADING, J.VERIFYING, J.EXTRACTING):
                return False
            if j.state == J.QUEUED and (j.force or not d.hold) and j.retry_at <= now \
                    and (j.force or rommlib.in_download_hours(d.cfg)):
                return False
        return True

    def settled():
        with app.lock:
            if busy["n"] or app.events_from_threads:
                return False
        if any(getattr(o, "pending_at", None) for o in (app.stack[-1] if app.stack else None, app.modal)):
            return False
        return not downloads or downloads_quiet()
    return settled
