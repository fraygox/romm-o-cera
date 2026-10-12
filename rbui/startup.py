"""Start-up chores: markers, registering with ES, memory budgets, malloc tuning."""
import os
import threading
import time

import rommlib
from rbui import APP_DIR, APP_NAME, draw


def read_marker(name):
    """A one-line marker file in the data folder (None if there's none)."""
    try:
        with open(os.path.join(rommlib.DATA_DIR, name)) as f:
            return f.read().strip()
    except OSError:
        return None


def write_marker(name, value):
    """Write it whole or not at all; a full disk only means it's written next time."""
    try:
        rommlib.atomic_write_text(os.path.join(rommlib.DATA_DIR, name), value)
        return True
    except OSError as e:
        rommlib.log("marker %s: %s" % (name, e.__class__.__name__))
        return False


def register_self():
    """Queue our own ES gamelist entry (name, icon, developer, release date)
    for the post-exit merge, so the pygame system lists the app by name, not a
    folder. Re-queued whenever the version changes, so ES stays current.
    Never stops the app from starting (a full disk: tried again next start)."""
    if read_marker(".registered") == rommlib.VERSION_LABEL:
        return
    import calendar
    rel = calendar.timegm(time.strptime(rommlib.RELEASE_DATE, "%Y-%m-%d")) * 1000
    entry = {"system": "pygame", "paths": [os.path.join(APP_DIR, "rommbrowser.pygame")],
             "name": APP_NAME,
             "desc": "Browse and search your RomM library with a controller and download games "
                     "straight into this box's roms folders. v%s · %s" % (rommlib.VERSION, rommlib.PROJECT_URL),
             "genres": ["Utility"], "developer": rommlib.AUTHOR, "publisher": rommlib.AUTHOR,
             "players": "1", "releasedate": rel,
             "image_local": os.path.join(APP_DIR, "icon.png")}
    try:
        rommlib.append_gamelist(entry)
    except OSError as e:
        rommlib.log("register: %s" % e.__class__.__name__)
        return
    write_marker(".registered", rommlib.VERSION_LABEL)


def fit_memory_budgets():
    """A box with under 3 GB of RAM keeps half as much text and covers in memory."""
    ram = rommlib.total_ram()
    if ram and ram < 3 << 30:
        draw.TEXT_BUDGET, draw.COVER_BUDGET = 32 << 20, 32 << 20


def tame_malloc():
    """glibc gives each thread its own heap (up to 8 per core) and keeps what's
    freed: with a dozen cover threads decoding images, the app's memory only
    ever grew (~200 MB more at 4K on a 2 GB box). Two heaps, and freed memory
    handed back to the system every 30 s. Best effort: not glibc, no change."""
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6")
        libc.mallopt(-8, 2)                        # M_ARENA_MAX
        malloc_trim = libc.malloc_trim
    except (ImportError, OSError, AttributeError):
        return

    def trim():
        while True:
            time.sleep(30)
            malloc_trim(0)
    threading.Thread(target=trim, daemon=True, name="malloc-trim").start()
