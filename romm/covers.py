"""The cover cache (LRU)."""
import os
import threading
import time

from . import core as _core


# ----------------------------------------------------------- cover cache (LRU)
# /userdata is mounted noatime, so "last used" is kept in the file's mtime:
# bumped when a cached cover is used (at most every few minutes per file).

PRUNE_EVERY = 64 << 20                                # check after this much new cache
_prune_lock = threading.Lock()


def touch_cached(path, every=600):
    try:
        if time.time() - os.path.getmtime(path) > every:
            os.utime(path, None)
    except OSError:
        pass


def cache_usage(root=None):
    """(bytes, files) in the cover cache."""
    root = root or _core.COVER_CACHE
    tot = n = 0
    for dp, _, fns in os.walk(root):
        for f in fns:
            try:
                tot += os.path.getsize(os.path.join(dp, f))
                n += 1
            except OSError:
                pass
    return tot, n


def prune_cache(limit, root=None, target=0.9):
    """Keep the cover cache under `limit` bytes: once over it, delete the
    least recently used covers until it's at `target` of the limit (so it
    doesn't trim on every new cover). Returns (files deleted, bytes freed)."""
    root = root or _core.COVER_CACHE
    if not limit or not _prune_lock.acquire(blocking=False):
        return 0, 0
    try:
        files, total = [], 0
        now = time.time()
        for dp, _, fns in os.walk(root):
            for f in fns:
                p = os.path.join(dp, f)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                if f.endswith(".tmp") and now - st.st_mtime > 3600:
                    try:                                  # a download that never finished
                        os.remove(p)
                    except OSError:
                        pass
                    continue
                files.append((st.st_mtime, st.st_size, p))
                total += st.st_size
        if total <= limit:
            return 0, 0
        goal = int(limit * target)
        n = freed = 0
        for _, size, p in sorted(files):                  # oldest use first
            if total - freed <= goal:
                break
            try:
                os.remove(p)
                n += 1
                freed += size
            except OSError:
                pass
        if n:
            _core.log("cache: removed %d least recently used covers (%s) to stay under %s" % (
                n, _core.human_size(freed), _core.human_size(limit)))
        return n, freed
    finally:
        _prune_lock.release()
