#!/usr/bin/env python3
"""Hand RomM-o-Cera's pending game info to EmulationStation, through ES's own
API, once ES is idle.

Spawned detached by the app on exit (and by the CHD / download workers).
EmulationStation owns its gamelists: it saves them by itself at any time (its
RetroAchievements hasher writes entries for new files in the background), so
editing gamelist.xml behind its back can always be overwritten — that lost
game info after CHD conversions up to v0.6.3. Since v0.6.4 nothing here
writes gamelist.xml. Instead:

  1. wait until ES is idle: no game (and not this app) running, nothing
     scraping, hashing, installing or updating (/isIdle, Batocera 36+);
  2. read, from gamelist.xml (read only), the info of games about to be
     renamed (CHD conversions), so it can be carried to the new file;
  3. let ES list new files: downloads go in with POST /addgames/<system>
     (only those games, in a moment); anything else, or an ES that doesn't
     take them (Batocera 43.1's rejects every body), gets the full
     /reloadgames, which can take minutes on a big library;
  4. per game, POST /systems/<sys>/games/<id> with only the fields that
     should change (ES keeps the rest), and upload covers/manuals/media with
     POST .../media/<type>. ES updates its in-memory list and its own
     crash-safe journal, and writes gamelist.xml itself.

Entries ES can't take yet (a system or file it doesn't list) are kept and
retried; nothing is dropped silently.
"""
import json
import os
import sys
import tempfile
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rommlib  # noqa: E402

MAX_TRIES = 5
BAD_MAX = 1024 * 1024    # <queue>.bad keeps its newest lines up to this size

TEXT_FIELDS = ("name", "desc", "releasedate", "developer", "publisher", "genre", "players", "rating",
               "region", "lang", "family")
USAGE_FIELDS = ("playcount", "lastplayed", "gametime", "favorite")
# gamelist media tags ES accepts uploads for, and the content types it wants
MEDIA_TAGS = ("image", "thumbnail", "marquee", "video", "fanart", "boxback", "bezel", "manual", "map",
              "titleshot", "wheel", "boxart", "cartridge", "magazine")
IMAGE_MAX = 20 << 20     # an image from RomM bigger than this isn't one
VIDEO_MAX = 50 << 20     # a game's video clip: a bigger one is skipped (and logged)
# Settings → Metadata (v0.11): a rommlib still in memory from before an update lacks these
# (tests/test_package.py): then the cap stays VIDEO_MAX and skips aren't noted
_video_max = getattr(rommlib, "es_video_max", None)
_note_skip = getattr(rommlib, "record_media_skip", lambda *a: None)
_forget_skip = getattr(rommlib, "clear_media_skip", lambda *a: None)
_wants_own_image = getattr(rommlib, "wants_own_image", lambda m: False)
_art_files = getattr(rommlib, "es_art_files", lambda *a: [])
_same_file = getattr(rommlib, "same_file", lambda a, b: False)
_es_media_file = getattr(rommlib, "es_media_file", lambda *a: None)
# next release: rommlib.FOLDER_GAME_EXTS (library.py) and rommlib.EsError (es.py, in v0.11.2 but
# not listed in PREVIOUS_RELEASE_NAMES yet)
_FOLDER_GAME_EXTS = getattr(rommlib, "FOLDER_GAME_EXTS", None) or frozenset(
    {".ps3", ".xbox360", ".wiiu", ".pc", ".psvita", ".daphne", ".scummvm"})


class _NoEsError(Exception):
    """Stands in for rommlib.EsError where a rommlib lacks it (nothing raises it)."""


EsError = getattr(rommlib, "EsError", None) or _NoEsError
CTYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
          ".mp4": "video/mp4", ".avi": "video/avi", ".mkv": "video/mkv", ".webm": "video/webm",
          ".pdf": "application/pdf", ".cbz": "application/cbz"}


def lock_path():
    return os.path.join(rommlib.DATA_DIR, "finalize.lock")


def deferred_path():
    # entries ES couldn't take this time (game/system not listed yet): retried next run
    return os.path.join(rommlib.DATA_DIR, "gamelist-deferred.jsonl")


# ------------------------------------------------------------------ ES API

def game_running():
    """True when ES is busy: a game runs (this app included), ES is busy
    itself (/isIdle), or ES is unreachable."""
    return rommlib.es_busy(app_busy=True) != ""


def wait_idle(max_wait=6 * 3600):
    t0 = time.time()
    idle_since = None
    while time.time() - t0 < max_wait:
        if game_running():
            idle_since = None
        elif idle_since is None:
            idle_since = time.time()
        elif time.time() - idle_since >= 5:   # ES settled back in the menu
            return True
        time.sleep(2)
    return False


class EsApi:
    """The slice of Batocera ES's HTTP API this uses (Batocera 43)."""

    def __init__(self, base=None):
        self.base = base or rommlib.ES_API
        self._games = {}
        self.failed = set()      # systems whose last list ES answered wrongly (not "no such system": 404)

    def _req(self, method, path, data=None, ctype=None, timeout=30):
        """(status, body); HTTP errors come back as a status, EsError when ES
        can't be asked."""
        r = rommlib.EsClient(self.base).request(method, path, data, ctype, timeout, errors=False)
        return r.status, r.body

    def games(self, system, refresh=False):
        """{absolute path: game dict} for a system ES lists, or None."""
        if refresh or system not in self._games:
            st, body = self._req("GET", "/systems/%s/games" % urllib.parse.quote(system))
            if st != 200:
                (self.failed.add if st != 404 else self.failed.discard)(system)
                return None
            try:
                self._games[system] = {g["path"]: g for g in json.loads(body)}
            except (ValueError, KeyError, TypeError):
                self.failed.add(system)
                return None
            self.failed.discard(system)
        return self._games[system]

    def game(self, system, gid):
        st, body = self._req("GET", "/systems/%s/games/%s" % (urllib.parse.quote(system), gid))
        return json.loads(body) if st == 200 else None

    @staticmethod
    def same(a, b):
        """ES reformats numbers ("0.80" reads back "0.800000")."""
        a, b = str(a if a is not None else ""), str(b if b is not None else "")
        if a == b:
            return True
        try:
            return abs(float(a) - float(b)) < 1e-6
        except ValueError:
            return a.strip() == b.strip()

    def set_meta(self, system, gid, fields):
        """Only the given fields change (ES keeps the rest). 404 also means
        "nothing changed" in ES's API, so the result is read back."""
        if not fields:
            return True
        body = json.dumps({k: str(v) for k, v in fields.items()}).encode("utf-8")
        self._req("POST", "/systems/%s/games/%s" % (urllib.parse.quote(system), gid), body, "application/json")
        g = self.game(system, gid)
        return g is not None and all(self.same(g.get(k, ""), v) for k, v in fields.items())

    def set_media(self, system, gid, mtype, data, ctype):
        st, _ = self._req("POST", "/systems/%s/games/%s/media/%s" % (urllib.parse.quote(system), gid, mtype),
                          data, ctype, timeout=120)
        return st == 200

    def media_file(self, system, gid, tag):
        """A game's media file as ES serves it (GET .../media/<tag>): bytes, or None."""
        try:
            st, body = self._req("GET", "/systems/%s/games/%s/media/%s" % (urllib.parse.quote(system), gid, tag))
        except Exception:                      # ES can't be asked
            return None
        return body if st == 200 else None

    def media_present(self, system, gid, tag):
        """ES has the file of this media tag (only the headers of GET .../media/<tag> are
        read): False for a path to a file that is gone, which ES answers with nothing;
        None when ES can't be asked."""
        import urllib.error
        import urllib.request
        url = "%s/systems/%s/games/%s/media/%s" % (self.base, urllib.parse.quote(system), gid, tag)
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return int(r.headers.get("Content-Length") or 0) > 0
        except urllib.error.HTTPError as e:
            e.close()
            return False if e.code == 404 else None
        except Exception:
            return None

    def add_games(self, system, games):
        """List games ({absolute path: {tag: text}}) with POST /addgames/<system>:
        the status (see rommlib.es_add_games), or None when ES can't be asked.
        ValueError/UnicodeError: a name XML can't carry."""
        return rommlib.es_add_games(system, games)

    def reload(self):
        """A full /reloadgames. An HTTP error is logged, not raised: what ES doesn't list
        afterwards is kept and tried again, as for any game ES can't take yet (raising
        failed the whole batch, every run). EsError when ES can't be asked."""
        r = rommlib.EsClient(self.base).get("/reloadgames", timeout=120, errors=False)
        if r.status >= 400:
            rommlib.log("finalize: ES answered HTTP %d to /reloadgames" % r.status)


class GameLists:
    """ES's game list of each system for one batch, fetched once and kept until the batch
    changes what ES lists (/addgames, a reload: drop()), so a batch costs a fetch or two
    per system however many entries it has. xml: each system's gamelist.xml <game>s, read
    once (snapshot_renames, removed_media)."""

    def __init__(self, esapi):
        self.esapi, self.got, self.xml = esapi, {}, {}

    def get(self, system):
        """{path: game} as ES lists the system, or None (EsError when ES can't be asked)."""
        if system not in self.got:
            self.got[system] = self.esapi.games(system, refresh=True)
        return self.got[system]

    def unknown(self, system):
        """ES's list of the system couldn't be read (it answered wrongly): what it lists is
        not known, which is not the same as it listing nothing."""
        return self.got.get(system) is None and system in getattr(self.esapi, "failed", ())

    def put(self, system, games):
        self.got[system] = games

    def drop(self, system=None):
        if system is None:
            self.got.clear()
        else:
            self.got.pop(system, None)

    def nodes(self, sysdir):
        if sysdir not in self.xml:
            try:
                self.xml[sysdir] = ET.parse(os.path.join(sysdir, "gamelist.xml")).getroot().findall("game")
            except (OSError, ET.ParseError):
                self.xml[sysdir] = None
        return self.xml[sysdir]


# ------------------------------------------------------------------ helpers

def rel(sysdir, path):
    return "./" + os.path.relpath(path, sysdir)


def blank_name(game):
    """ES names a game it has no info for after its file."""
    stem = os.path.splitext(os.path.basename(game.get("path", "")))[0]
    return not game.get("name") or game.get("name") == stem


def has(game, k):
    if k == "name":
        return not blank_name(game)
    v = str(game.get(k) or "").strip()
    return bool(v) and v not in ("0", "0.000000", "not-a-date-time", "false")


# RomM didn't answer in this batch (offline, an error, a token it refused): {id(api): api}.
# The rest of the batch doesn't ask it again (each try could wait a minute, holding
# finalize.lock), and what it lacks is retried later instead of being noted as never coming.
_ROMM_DOWN = {}
GONE = (404, 410)                 # RomM's answer for a file it hasn't: noted, not asked again
DOWN = (401, 403, 408, 429)       # and 5xx: RomM (or the way to it) can't serve now


# RomM (or the way to it) can't serve now, whatever the file: offline, refused, timed out, a
# proxy or TLS in the way (ProxyError, SSLError: ConnectionErrors), a login redirecting in a
# loop, errors on every retry. Not an answer about the file: retried, never noted.
_ROMM_CANT = (requests.ConnectionError, requests.Timeout, requests.TooManyRedirects,
              requests.exceptions.RetryError, requests.exceptions.ChunkedEncodingError)


def romm_down(api):
    return _ROMM_DOWN.get(id(api)) is api


def _retry(info, why, api=None):
    """A fetch that may work later: nothing is noted (info["retry"]); with api, RomM
    itself is down and isn't asked again in this batch."""
    if info is not None:
        info["retry"] = why
    if api is not None:
        _ROMM_DOWN[id(api)] = api


def fetch_bytes(api, url_path, limit=200 << 20, info=None):
    """A RomM asset by server-relative path (token session), or None. A file
    over limit is None too, and sets info["too_big"] (its size when RomM says).
    info["retry"] says the file may come another time (RomM offline, an error,
    a token refused): only a None without it is RomM's answer that it isn't there.
    No RomM, or a path that isn't RomM's, sets info["no_url"]."""
    if api is None or not getattr(api, "base", None) or not url_path or not url_path.startswith("/") \
            or url_path.startswith("//"):
        if info is not None:
            info["no_url"] = True
        return None
    if romm_down(api):
        _retry(info, "down")
        return None
    try:
        r = api.s.get(api.base + url_path, timeout=60, stream=True)
    except _ROMM_CANT as ex:                   # offline, refused, timed out, a proxy or TLS in the way
        _retry(info, ex.__class__.__name__, api)
        return None
    except requests.RequestException:          # this path's own answer (a URL requests can't use)
        return None
    except Exception as ex:                    # something else: maybe another time, RomM isn't down
        _retry(info, ex.__class__.__name__)
        return None
    try:
        size = int(r.headers.get("Content-Length") or -1) if r.status_code == 200 else -1
        if r.status_code != 200 or size > limit:
            n = 0
            for c in r.iter_content(1 << 14):     # a small error page: read it so the
                n += len(c)                       # connection is kept, not closed
                if n > 1 << 16:
                    break
            if size > limit and info is not None:
                info["too_big"] = size
            elif r.status_code >= 500 or r.status_code in DOWN:
                _retry(info, "HTTP %d" % r.status_code, api)
            elif not 400 <= r.status_code < 500 and r.status_code != 200:
                _retry(info, "HTTP %d" % r.status_code)     # an odd answer (a 3xx left over): maybe later
            # any other 4xx (404, 400, 422...) is RomM's answer for this file: noted
            return None
        # read to its end, urllib3 hands the connection back to the pool by itself
        # (close() below then leaves it be); a file over limit closes it instead
        data = r.raw.read(limit + 1, decode_content=True)
        if data and len(data) > limit and info is not None:
            info["too_big"] = len(data)
        return data if data and len(data) <= limit else None
    except Exception as ex:                    # the connection dropped mid-file
        _retry(info, ex.__class__.__name__, api)
        return None
    finally:
        r.close()


def image_kind(data):
    """The content type ES takes for these image bytes, or None (WebP, not an image...)."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


def fetch_media(api, tag, path, video_max=None, info=None):
    """(bytes, content type) of one of RomM's media files as ES takes it, or None:
    images as PNG/JPEG/GIF (anything else converted to PNG), video as MP4 up to
    video_max (VIDEO_MAX by default; a bigger one is logged and skipped, its size
    in info["too_big"]), the manual a PDF. info["retry"]: see fetch_bytes (a full disk
    while converting an image too)."""
    if tag == "video":
        info = {} if info is None else info
        cap = VIDEO_MAX if video_max is None else video_max
        data = fetch_bytes(api, path, cap, info)
        if info.get("too_big"):
            rommlib.log("finalize: video skipped, %d MB is over %d MB: %s" % (
                info["too_big"] >> 20, cap >> 20, path))
        return (data, "video/mp4") if data and data[4:8] == b"ftyp" else None
    why = {}                    # why it didn't come; an image over IMAGE_MAX isn't noted as a size
    data = fetch_bytes(api, path, *((200 << 20, why) if tag == "manual" else (IMAGE_MAX, why)))
    if info is not None:
        info.update((k, v) for k, v in why.items() if k in ("retry", "no_url"))
    if tag == "manual":
        return (data, "application/pdf") if data and data.startswith(b"%PDF") else None
    if not data:
        return None
    kind = image_kind(data)
    if kind:
        return data, kind
    png = as_png(data, info)
    return (png, "image/png") if png else None


TRACE = bool(os.environ.get("ROMM_BROWSER_FETCH_TRACE"))    # log each width decision


def _cpu_times():
    """(idle, total) CPU time of the whole box so far, from /proc/stat (iowait counts as
    idle: the CPU could run something), or None where there's no such file."""
    try:
        with open("/proc/stat") as f:
            v = [int(x) for x in f.readline().split()[1:]]
    except (OSError, ValueError):
        return None
    return v[3] + (v[4] if len(v) > 4 else 0), sum(v[:8])


class AdaptiveFetcher:
    """Runs fetches on a few threads ahead of the one that uses them, finding its own width:
    it starts at START, adds one while files come about as fast as the quickest seen and the
    process has CPU to spare, and cuts back by a third when they slow down (the server, the
    link or this box queueing them), when this process nearly fills a core (Python runs one
    thread at a time: more would only wait for each other) or when the box is loaded. Heavy
    jobs (videos) run one at a time. Memory: what's fetched but not taken yet, plus what's
    on its way (reserved at the typical size, VIDEO_MAX for a video), stays under BUDGET.
    take() moves the job it wants to the front and waits for it; a key never submitted, or
    one that can't start until later results are taken, is None (the caller fetches it)."""
    START, LO, HI = 2, 1, 16
    BUDGET = 64 << 20
    CPU_HOT = 0.85                          # this process's share of one core: Python's lock is full
    IDLE_GROW, IDLE_CUT = 0.15, 0.05        # the box's idle CPU share: room to grow / none left
    SLOW, QUICK = 2.0, 1.3                  # a window's typical time vs the quickest seen

    def __init__(self, hi=None, budget=None):
        self.hi = hi or self.HI
        self.budget = budget or self.BUDGET
        self.limit = min(self.START, self.hi)
        self.cv = threading.Condition()
        self.jobs = []                      # [key, fn, heavy], in the order they'll be used
        self.done, self.on = {}, {}         # key -> (result, size); key -> bytes reserved
        self.held, self.typical_size = 0, 1 << 20
        self.window, self.best, self.closed = [], None, False
        self.t0, self.cpu0, self.box0 = time.monotonic(), time.process_time(), _cpu_times()
        self.threads = []
        self._grow()

    def _grow(self):
        """Threads for the width allowed now (never more than hi; idle ones wait)."""
        while len(self.threads) < self.limit:
            t = threading.Thread(target=self._work, daemon=True)
            self.threads.append(t)
            t.start()

    def submit(self, key, fn, heavy=False):
        with self.cv:
            self.jobs.append([key, fn, heavy])
            self.cv.notify_all()

    def backlog(self):
        with self.cv:
            return len(self.jobs) + len(self.on) + len(self.done)

    def _fits(self, heavy):
        """The bytes a job would reserve if memory allows it now (else None)."""
        want = VIDEO_MAX if heavy else self.typical_size
        used = self.held + sum(self.on.values())
        return want if used + want <= self.budget or not used else None

    def _room(self, heavy):
        """The bytes a job would reserve, if it may start now (else None): memory, and one
        video at a time."""
        if heavy and any(v == VIDEO_MAX for v in self.on.values()):
            return None
        return self._fits(heavy)

    def take(self, key):
        with self.cv:
            while not self.closed:
                if key in self.done or key not in self.on and not any(j[0] == key for j in self.jobs):
                    break
                i = next((i for i, j in enumerate(self.jobs) if j[0] == key), None)
                if i is not None:
                    if self._fits(self.jobs[i][2]) is None:
                        del self.jobs[i]        # only later results hold the memory it needs
                        break
                    self.jobs.insert(0, self.jobs.pop(i))      # wanted now: next to start
                    self.cv.notify_all()
                self.cv.wait(0.5)
            out = self.done.pop(key, None)
            if out is not None:
                self.held -= out[1]
                self.cv.notify_all()
            return out[0] if out is not None else None

    def close(self):
        with self.cv:
            self.closed = True
            self.jobs.clear()
            self.done.clear()
            self.cv.notify_all()

    def _next(self):
        """The first job that may start now, with what it reserves; or None."""
        if len(self.on) >= self.limit:
            return None
        for i, (key, fn, heavy) in enumerate(self.jobs):
            room = self._room(heavy)
            if room is not None:
                return self.jobs.pop(i), room
            if not heavy:
                return None                 # in order: a light job waits for room like the rest
        return None

    def _work(self):
        while True:
            with self.cv:
                nxt = None
                while not self.closed and nxt is None:
                    nxt = self._next()
                    if nxt is None:
                        self.cv.wait(0.5)
                if self.closed:
                    return
                (key, fn, heavy), room = nxt
                self.on[key] = room
            t = time.monotonic()
            try:
                out, size = fn()
            except Exception:
                out, size = None, 0
            took = time.monotonic() - t
            with self.cv:
                self.on.pop(key, None)
                if not self.closed:
                    self.done[key] = (out, size)
                    self.held += size
                if not heavy and out is not None:
                    self.typical_size = max(64 << 10, int(0.8 * self.typical_size + 0.2 * size))
                    self._learn(took)
                self.cv.notify_all()

    def _learn(self, took):
        """Called with the lock held, for each light job that came."""
        self.window.append(took)
        if len(self.window) < max(4, int(self.limit)):
            return
        typical = sorted(self.window)[len(self.window) // 2]
        self.window = []
        now, cpu, box = time.monotonic(), time.process_time(), _cpu_times()
        busy = (cpu - self.cpu0) / max(1e-3, now - self.t0)
        idle = 1.0
        if box and self.box0 and box[1] > self.box0[1]:
            idle = (box[0] - self.box0[0]) / (box[1] - self.box0[1])
        self.t0, self.cpu0, self.box0 = now, cpu, box
        self.best = typical if self.best is None else min(self.best * 1.02, typical)   # forgets slowly
        was = self.limit
        if busy >= self.CPU_HOT or idle < self.IDLE_CUT or typical > self.SLOW * self.best:
            self.limit = max(self.LO, int(self.limit * 2 / 3))
        elif typical < self.QUICK * self.best and idle >= self.IDLE_GROW:
            self.limit = min(self.hi, self.limit + 1)
            self._grow()
        if TRACE:
            rommlib.log("finalize: fetch width %d -> %d (typical %.0f ms, quickest %.0f ms, cpu %.2f, box idle %.2f)"
                        % (was, self.limit, 1000 * typical, 1000 * self.best, busy, idle))


def media_wanted(e, cfg=None):
    """([(tag, RomM paths)] an entry offers, the thumbnail first, as upload_media goes
    through them; the video cap): one rule for upload_media and the prefetch ahead of it."""
    media = entry_media(e)
    if e.get("image_local") and os.path.isfile(e["image_local"]):
        media.pop("image", None)
    if e.get("manual"):
        media["manual"] = [e["manual"]]
    video_max = _video_max(cfg) if cfg is not None and _video_max else None
    if video_max == 0:
        media.pop("video", None)
    order = sorted(media.items(), key=lambda kv: kv[0] != "thumbnail")
    return [(t, ps) for t, ps in order if t in MEDIA_TAGS], video_max


def fetch_first(api, tag, paths, video_max=None):
    """([(path, fetch_media's answer)] tried in order up to the first that came, info)."""
    info, tried = {}, []
    for p in paths:
        got = fetch_media(api, tag, p, video_max, info)
        tried.append((p, got))
        if got:
            break
    return tried, info


def prefetch(pre, api, e, g, cfg=None):
    """Ask the fetcher for the media upload_media will want for this game (what ES's game
    list says it lacks); the rest (an image that may only be the box art again) is fetched
    when it's needed."""
    wanted, video_max = media_wanted(e, cfg)
    for tag, paths in wanted:
        if not g.get(tag) and not theirs(e, g, tag):
            def job(tag=tag, paths=paths):
                tried, info = fetch_first(api, tag, paths, video_max)
                return (tried, info), sum(len(got[0]) for _, got in tried if got)
            pre.submit((id(e), tag), job, heavy=tag == "video")


def image_is_box_art_again(esapi, system, g, media):
    """The game's image in ES is byte for byte its thumbnail while the entry offers an image
    of its own (a screenshot): what versions before 0.11 left after a Fill in game info,
    the cover twice. That image is replaced; any other never is."""
    if not g.get("image") or not _wants_own_image(media) or not hasattr(esapi, "media_file"):
        return False
    img = esapi.media_file(system, g["id"], "image")
    return img is not None and img == esapi.media_file(system, g["id"], "thumbnail")


def drop_stale_images(system, g, keep):
    """After the image was replaced under another extension, ES's old file is left in
    images/: removed when it is the thumbnail's copy (and not the new image)."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, system)
    thumbs = _art_files(sysdir, g["path"], "thumb")
    for f in _art_files(sysdir, g["path"], "image"):
        try:
            with open(f, "rb") as fh:
                data = fh.read()
            if data != keep and any(_same_file(f, t) for t in thumbs):
                os.remove(f)
        except OSError:
            pass


def theirs(e, g, tag):
    """The game has a file of this tag that was there before the app handed it to ES (the
    entry's "theirs_media": the owner's, a copy of theirs scraped): RomM's never replaces it."""
    stem = os.path.splitext(os.path.basename(g.get("path") or ""))[0] + "-"
    fs = (e.get("theirs_media") or {}).get(tag) if isinstance(e.get("theirs_media"), dict) else None
    return any(isinstance(f, str) and os.path.basename(f).startswith(stem) and os.path.isfile(f)
               for f in (fs if isinstance(fs, list) else ()))


def upload_media(api, esapi, system, g, e, cfg=None, pre=None):
    """Every media file the entry offers that the game has none of in ES yet (a
    path to a file that is gone counts as none; what's there is never replaced,
    but for an image that is only the box art again: image_is_box_art_again):
    the first of its RomM paths that comes.
    A tag none of them comes for is noted (library.record_media_skip), so Fill
    in game info stops asking for it, but only when RomM said so (a missing file,
    one over the cap, one that isn't media); videos follow today's cap (cfg).
    pre: an AdaptiveFetcher that may have fetched them already (prefetch()).
    False when some media may come another time (RomM offline, an error, a full
    disk, ES refusing to store a file that came): the entry is kept and tried again.
    A tag the owner had a file of already (theirs) is left as it is."""
    later = refused = False
    sysdir = os.path.join(rommlib.ROMS_ROOT, system)
    named = getattr(rommlib, "es_named_media", None)      # next release: rommlib.es_named_media only

    def on_disk(tag):
        """A file is there already under the name ES would give this tag's file: whoever put it
        there, it's never written over (an unlinked scrape, the owner's own, the app's earlier one)."""
        if named is None or not g.get("path"):
            return False
        try:
            return bool(named(sysdir, [g["path"]], (tag,)).get(tag))
        except Exception:
            return True                                    # can't tell: leave it
    if e.get("image_local") and os.path.isfile(e["image_local"]) and not g.get("image") \
            and not theirs(e, g, "image") and not on_disk("image"):
        with open(e["image_local"], "rb") as f:
            if not esapi.set_media(system, g["id"], "image", f.read(), "image/png"):
                later = refused = True
    wanted, video_max = media_wanted(e, cfg)
    media = dict(wanted)
    # the thumbnail first, so an image that is only the box art again shows as such
    for tag, paths in wanted:
        again = False
        if theirs(e, g, tag) or on_disk(tag):
            continue
        if g.get(tag) and getattr(esapi, "media_present", lambda *a: None)(system, g["id"], tag) is not False:
            again = tag == "image" and image_is_box_art_again(esapi, system, g, media)
            if not again:
                continue
        ahead = pre.take((id(e), tag)) if pre is not None else None
        tried, info = ahead if ahead is not None else ([], {})
        came, done = False, False
        for p, got in tried:                   # what the fetcher got, then the paths it didn't try
            came = came or bool(got)
            if got and esapi.set_media(system, g["id"], tag, got[0], got[1]):
                done = True
                break
        for p in paths[len(tried):] if not done else ():
            got = fetch_media(api, tag, p, video_max, info)
            came = came or bool(got)
            if got and esapi.set_media(system, g["id"], tag, got[0], got[1]):
                done = True
                break
        if done:
            _forget_skip(system, e.get("rom_id"), tag)
            if again:
                drop_stale_images(system, g, got[0])
        elif came:                             # it came, ES didn't store it (a full disk...)
            later = refused = True
        elif info.get("retry"):
            later = True
        elif not info.get("no_url"):
            _note_skip(system, e.get("rom_id"), tag, paths[0], info.get("too_big"))
    if later:
        rommlib.log("finalize: media for %s kept for later (%s)" % (
            os.path.basename(g.get("path") or "?"), "ES didn't store it" if refused
            else "RomM can't be asked" if romm_down(api) else "couldn't fetch it"))
    return not later


def as_png(data, info=None):
    """Covers can be WebP: ES takes png/jpg/gif. No room to convert it (a full
    disk...) sets info["retry"]: the file is fine, it is tried another time."""
    try:
        os.makedirs(rommlib.DATA_DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(suffix=".png", dir=rommlib.DATA_DIR)
        os.close(fd)
    except OSError as ex:
        _retry(info, "%s converting an image" % ex.__class__.__name__)
        return None
    try:
        rommlib.write_png(data, tmp)
        with open(tmp, "rb") as f:
            out = f.read()
        # write_png copies what it can't convert as it is: only an image goes on to ES (a
        # WebP left as WebP where there's no PIL is still one), never an error page RomM sent
        return out if image_kind(out) or (out[:4] == b"RIFF" and out[8:12] == b"WEBP") else None
    except OSError as ex:
        _retry(info, "%s converting an image" % ex.__class__.__name__)
        return None
    except Exception:
        return None
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def entry_fields(e):
    """A gamelist entry's text as ES's fields, in ES's formats; empty ones left out."""
    out = {"name": e.get("name"), "desc": e.get("desc"),
           "genre": ", ".join(e.get("genres") or []) or None,
           "developer": e.get("developer"), "publisher": e.get("publisher"),
           "players": e.get("players"), "region": e.get("region"), "lang": e.get("lang"),
           "family": e.get("family")}
    ms = e.get("releasedate")
    if ms:
        out["releasedate"] = time.strftime("%Y%m%dT000000", time.gmtime(ms / 1000))
    if e.get("rating") is not None:
        out["rating"] = "%.2f" % (float(e["rating"]) / 100)
    return {k: v for k, v in out.items() if v not in (None, "")}


def entry_media(e):
    """{ES media tag: RomM server paths, best first} a gamelist entry offers
    (library.es_media). Entries queued by older versions carry only "cover":
    that is the image."""
    m = e.get("media")
    if isinstance(m, dict):
        return {k: [p for p in v if isinstance(p, str)] for k, v in m.items() if isinstance(v, list) and v}
    return {"image": [e["cover"]]} if e.get("cover") else {}


def node_info(sysdir, g):
    """A gamelist.xml <game> as {text, usage, hidden, media{tag: abs path}}."""
    info = {"text": {}, "usage": {}, "hidden": g.findtext("hidden") == "true", "media": {}}
    for k in TEXT_FIELDS:
        v = (g.findtext(k) or "").strip()
        if v:
            info["text"][k] = v
    for k in USAGE_FIELDS:
        v = (g.findtext(k) or "").strip()
        if v:
            info["usage"][k] = v
    for k in MEDIA_TAGS:
        v = (g.findtext(k) or "").strip()
        if v:
            p = os.path.normpath(os.path.join(sysdir, v))
            if os.path.isfile(p):
                info["media"][k] = p
    return info


def richness(info):
    return len(info["text"]) + len(info["media"]) + len(info["usage"])


def api_info(g, sysdir=None):
    """An ES API game dict as node_info() shape. ES gives its media as URLs: with sysdir,
    the files ES names for them (images/<stem>-image.png...) that are there."""
    info = {"text": {}, "usage": {}, "hidden": str(g.get("hidden")) == "true", "media": {}}
    for k in MEDIA_TAGS if sysdir else ():
        f = _es_media_file(sysdir, g.get("path") or "", k) if g.get(k) else None
        if f:
            info["media"][k] = f
    for k in TEXT_FIELDS:
        if has(g, k):
            info["text"][k] = str(g[k])
    for k in USAGE_FIELDS:
        if has(g, k):
            info["usage"][k] = str(g[k])
    return info


def snapshot_renames(entries, esapi=None, lists=None):
    """Before ES reloads: the info of every path a CHD conversion is renaming,
    keyed by the new path — from gamelist.xml (with media) and from ES's
    memory (fresher: may not be saved yet; its media by the files ES names),
    richest wins; a deferred entry brings the snapshot it took the first time.
    lists: the batch's GameLists (ES's lists and gamelist.xml read once for it)."""
    carry, live = {}, {}
    if lists is None:
        lists = GameLists(esapi)
    for e in entries:
        if "remove" in e:
            gone = [r for r in e["remove"] if not os.path.exists(r)]   # reinstalled since: keep its art
            if e.get("keep_media"):      # a game put here another way: only ES's entry goes, never the owner's media
                gone = []
            sysdir = os.path.join(rommlib.ROMS_ROOT, e["system"])
            carry[("media-of-removed", id(e))] = removed_media(sysdir, gone, lists.nodes(sysdir)) if gone else []
            continue
        if "rename" not in e:
            continue
        for new, info in (e.get("_carry") or {}).items():
            carry[new] = info
        sysdir = os.path.join(rommlib.ROMS_ROOT, e["system"])
        if esapi is not None and e["system"] not in live:
            live[e["system"]] = lists.get(e["system"]) or {}
        nodes = lists.nodes(sysdir) or []
        for old, new in e["rename"].items():
            old_rel = rel(sysdir, old)
            for g in nodes:
                p = g.findtext("path") or ""
                if p == old_rel or p.startswith(old_rel.rstrip("/") + "/"):
                    info = node_info(sysdir, g)
                    if richness(info) and (new not in carry or richness(info) > richness(carry[new])):
                        carry[new] = info
            for path, g in (live.get(e["system"]) or {}).items():
                if path == old or path.startswith(old.rstrip("/") + "/"):
                    info = api_info(g, sysdir)       # media ES has only in its journal too
                    base = carry.get(new)
                    if base is None:
                        if richness(info):
                            carry[new] = info
                    else:                    # ES's memory wins for text/usage, the file keeps media
                        base["text"].update(info["text"])
                        base["usage"].update(info["usage"])
                        base["hidden"] = base["hidden"] or info["hidden"]
                        for k, f in info["media"].items():
                            base["media"].setdefault(k, f)
    return carry


def merge_usage(game, usage):
    """Never lose play history: the higher count/time, the later date."""
    out = {}
    for k in ("playcount", "gametime"):
        try:
            old, cur = int(float(usage.get(k) or 0)), int(float(game.get(k) or 0))
        except ValueError:
            continue
        if old > cur:
            out[k] = str(old)
    lp = usage.get("lastplayed") or ""
    cur = str(game.get("lastplayed") or "")
    if not cur[:1].isdigit() or cur.strip("0") == "":     # never played: "", "0", "not-a-date-time"
        cur = ""
    if lp and lp > cur and lp[:1].isdigit():
        out["lastplayed"] = lp
    if usage.get("favorite") == "true" and str(game.get("favorite")) != "true":
        out["favorite"] = "true"
    return out


def removed_media(sysdir, removed, nodes=False):
    """Media files of uninstalled games, from gamelist.xml (read only, before
    ES reloads): those inside the system's media folders that no other entry
    uses. The entries themselves are left to ES (it ignores missing files).
    nodes: its <game>s read already (None: it couldn't be read)."""
    if nodes is False:
        nodes = GameLists(None).nodes(sysdir)
    if nodes is None:
        return []
    rels = [rel(sysdir, r) for r in removed]
    gone, keep = set(), set()
    for g in nodes:
        p = g.findtext("path") or ""
        dead = any(p == r or p.startswith(r.rstrip("/") + "/") for r in rels)
        for k in MEDIA_TAGS:
            v = (g.findtext(k) or "").strip()
            if v:
                (gone if dead else keep).add(os.path.normpath(os.path.join(sysdir, v)))
    return sorted(f for f in gone - keep
                  if any(f.startswith(os.path.join(sysdir, d) + os.sep) for d in rommlib.GAMELIST_MEDIA_DIRS))


def upload_file(esapi, system, gid, tag, path):
    ctype = CTYPES.get(os.path.splitext(path)[1].lower())
    if not ctype:
        return False
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return False
    return esapi.set_media(system, gid, tag, data, ctype)


# ------------------------------------------------------------------ apply

def primary_of(system, paths, es):
    """The path ES lists for a game made of `paths`: a playlist given alone, a game
    folder ES lists as one game (a PS3 "Name.ps3": its extension is the system's, as
    library.play_target has it), else library.primary_path's file; None for nothing
    ES would list."""
    paths = paths or []
    if len(paths) == 1 and paths[0].lower().endswith(".m3u"):
        return paths[0]
    exts = es.get(system)
    for p in paths:
        x = os.path.splitext(p.rstrip("/"))[1].lower()
        if x and os.path.isdir(p) and (x in exts if exts is not None else x in _FOLDER_GAME_EXTS):
            return os.path.normpath(p)
    return rommlib.primary_path(system, paths, es)


def _own_game(e):
    """The game file a download into a folder holding other files names ("game": its own
    sheet, not the folder's first one, which may be the owner's), or None (older entries,
    and downloads whose folder holds only the game, name the folder)."""
    g = e.get("game")
    return os.path.normpath(g) if isinstance(g, str) and g else None


def _primary(e, es):
    own = _own_game(e)
    if own is not None:
        return primary_of(e["system"], [own], es) if os.path.exists(own) else None
    return primary_of(e["system"], e.get("paths") or [], es)


def _converting(paths):
    """A conversion waiting or running (the conversion worker's queue) reads one of these
    files: the game ES will list is the one it makes."""
    try:
        names = [n for n in os.listdir(rommlib.CHD_QUEUE_DIR) if n.endswith(".json")]
    except OSError:
        return False
    want = [os.path.normpath(p) for p in paths]
    for n in names:
        try:
            with open(os.path.join(rommlib.CHD_QUEUE_DIR, n)) as f:
                srcs = json.load(f).get("sources") or []
        except (OSError, ValueError, AttributeError):
            continue
        for s in srcs:
            s = os.path.normpath(str(s))
            if any(s == p or s.startswith(p.rstrip(os.sep) + os.sep) or p.startswith(s.rstrip(os.sep) + os.sep)
                   for p in want):
                return True
    return False


def _has_media(e, cfg):
    try:
        return bool(media_wanted(e, cfg)[0])
    except Exception:                  # a malformed entry: _apply's guard reports it
        return False


def foreign(system, es):
    """ES has no such system on this box (its es_systems.cfg doesn't list it): nothing of
    it is ever listed, so it isn't waited or reloaded for. Unknown without a list."""
    return bool(es) and system not in es


def _files(e):
    """The files on disk an entry is about."""
    out = list(e.get("paths") or []) + list((e.get("rename") or {}).values()) + list(e.get("hide") or [])
    return [p for p in out if isinstance(p, str) and os.path.exists(p)]


def _resting(e, now):
    """An entry kept only for media whose last try is within MEDIA_TRY_GAP: _spend counts
    one try per gap, so it is not applied for media (nor fetched) before the gap is over."""
    return bool(e.get("fill")) and 0 <= now - _num(e.get("_tried"), -MEDIA_TRY_GAP) < MEDIA_TRY_GAP


def apply(entries, api, es, esapi, carry, cfg=None, parallel=True, fresh=True, lists=None):
    """Hand entries to ES. Returns the entries ES couldn't take yet. RomM's media for the
    games coming up is fetched ahead, a few at a time (AdaptiveFetcher), while ES gets
    them one game after another, in order. fresh: RomM is asked afresh (not when the
    caller hands over one batch in several calls: isolate). lists: the batch's
    GameLists (else ES's lists are fetched afresh, once per system)."""
    left = []
    lists = lists if lists is not None else GameLists(esapi)

    def find(system, path):
        return (lists.get(system) or {}).get(path)

    if fresh:
        _ROMM_DOWN.pop(id(api), None)      # each batch asks RomM afresh
    now = time.time()
    media = [e for e in entries if api is not None and "paths" in e and "remove" not in e
             and "rename" not in e and "hide" not in e and not _resting(e, now) and _has_media(e, cfg)]
    pre = AdaptiveFetcher() if parallel and len(media) > 1 else None
    fed = [0]

    def feed():
        """Keep the fetcher a few games ahead of ES (3x its width, a dozen at least)."""
        while pre is not None and fed[0] < len(media) and pre.backlog() < max(12, 3 * pre.limit):
            e = media[fed[0]]
            fed[0] += 1
            try:
                prim = _primary(e, es)
                g = find(e["system"], prim) if prim else None
                if g is not None and not romm_down(api):
                    prefetch(pre, api, e, g, cfg)
            except Exception:                   # _apply's guard reports the entry
                pass
    try:
        return _apply(entries, api, es, esapi, carry, cfg, find, pre, feed, left)
    finally:
        if pre is not None:
            pre.close()
        if fresh:
            _ROMM_DOWN.pop(id(api), None)


def _apply(entries, api, es, esapi, carry, cfg, find, pre, feed, left):
    for e in entries:
        system = e["system"]
        try:
            feed()
            if "remove" in e:
                for f in carry.get(("media-of-removed", id(e)), []):   # ES itself drops the games
                    try:
                        os.remove(f)
                    except OSError:
                        pass
                continue
            if foreign(system, es):                 # never listed here: kept (tries spent), no fetch
                if _files(e):
                    e["_left"] = "no-system"
                    left.append(e)
                continue
            if "rename" in e or "hide" in e:
                targets = list(dict.fromkeys(e.get("rename", {}).values())) if "rename" in e else []   # once each: every track maps to the same file
                ok = True
                for new in targets:
                    g = find(system, primary_of(system, [new], es) or new)
                    if g is None:
                        ok = ok and not os.path.exists(new)    # gone again: nothing to carry
                        continue
                    info = carry.get(new)
                    if info:
                        fields = {k: v for k, v in info["text"].items() if not has(g, k)}
                        fields.update(merge_usage(g, info["usage"]))
                        if info["hidden"] and not new.lower().endswith(".m3u"):
                            fields["hidden"] = "true"      # a disc under a playlist stays hidden; the playlist never
                        ok = esapi.set_meta(system, g["id"], fields) and ok
                        for tag, p in info["media"].items():
                            if not g.get(tag):
                                upload_file(esapi, system, g["id"], tag, p)
                for h in e.get("hide", []):
                    g = find(system, h)
                    if g is None:
                        ok = ok and not os.path.exists(h)
                        continue
                    if str(g.get("hidden")) != "true":
                        ok = esapi.set_meta(system, g["id"], {"hidden": "true"}) and ok
                if not ok:
                    e["_left"] = "unlisted"
                    left.append(e)
                continue
            if _own_game(e) is not None and not os.path.exists(_own_game(e)):
                moved = moved_to(dict(e, paths=[_own_game(e)]))     # converted since it was queued
                if moved:
                    e["game"] = primary_of(system, moved, es) or moved[0]
                    e["fill"] = True
            prim = _primary(e, es)
            if not prim and e.get("paths") and not any(os.path.exists(p) for p in e["paths"]):
                moved = moved_to(e)                 # converted since it was queued: its new files
                if moved:
                    e["paths"] = moved
                    e["fill"] = True                # another file: its info only fills gaps
                    prim = _primary(e, es)
            if not prim:
                here = [p for p in e.get("paths") or [] if os.path.exists(p)]
                if here:                            # not a game ES lists yet (a source being converted)
                    e["_left"] = "converting" if _converting(here) else "unlisted"
                    left.append(e)
                continue                            # nothing ES would list (files gone)
            g = find(system, prim)
            if g is None:
                e["_left"] = "unlisted"
                left.append(e)
                continue
            want = entry_fields(e)
            if e.get("fill"):
                want = {k: v for k, v in want.items() if not has(g, k)}
            else:
                want = {k: v for k, v in want.items() if not EsApi.same(g.get(k, ""), v)}
            if not esapi.set_meta(system, g["id"], want):
                left.append(e)
                continue
            g.update((k, str(v)) for k, v in want.items())   # the batch's list stays true to ES
            if _resting(e, time.time()):            # media tried lately: no RomM request until the gap is over
                e["_left"] = "media"
                left.append(e)
                continue
            if upload_media(api, esapi, system, g, e, cfg, pre) is False:
                e["_left"] = "media"                # its info is in; media RomM couldn't send now
                e["fill"] = True                    # so the retry only fills gaps: it never undoes
                left.append(e)                      # what was edited in ES in the meantime
        except Exception as ex:                    # one bad entry never sinks the batch
            rommlib.log("finalize: entry for %s failed (%r)" % (system, ex))
            left.append(e)
    return left


# every rename handed to ES in this run ({old path: new path}): a download whose files a
# conversion replaced after it was queued follows them (moved_to)
_RENAMED = {}


def _route(p, renames):
    if p in renames:
        return renames[p]
    for old, new in renames.items():
        if p.startswith(old.rstrip("/") + "/"):
            return new
    return p


def moved_to(e):
    """The files an entry's game is now in, when all of its own are gone: a rename seen in
    this run, else the install record (rb_chd points it at the converted files). None
    when there's none (the game was removed)."""
    paths = e.get("paths") or []
    routed = list(dict.fromkeys(_route(p, _RENAMED) for p in paths))
    if routed != paths and any(os.path.exists(p) for p in routed):
        return routed
    if e.get("rom_id") is None:
        return None
    try:
        rec = rommlib.Library().snapshot().get(str(e["rom_id"])) or {}
    except Exception:                          # installed.json can't be read: nothing to follow
        return None
    if rec.get("system") not in (None, e["system"]) or not _same_game(rec, e):
        return None
    here = [p for p in rec.get("paths") or [] if isinstance(p, str) and os.path.exists(p)]
    return here if here and here != paths else None


def _stem(p):
    return os.path.splitext(os.path.basename(str(p).rstrip("/")))[0].lower()


def _same_game(rec, e):
    """The install record is this entry's game: installed.json is keyed by RomM's game id
    alone, and after a switch to another RomM server the same id is another game. Its name,
    or its file name against the entry's files, must match."""
    if rec.get("name") and e.get("name") and rec["name"] == e["name"]:
        return True
    fs = rec.get("fs_name")
    return bool(fs) and _stem(fs) in {_stem(p) for p in e.get("paths") or [] if isinstance(p, str)}


def wanted(entries, es):
    """{system: primary paths ES must list} for the entries' files on disk (none for a
    system ES doesn't have: foreign)."""
    want = {}
    for e in entries:
        if foreign(e["system"], es):
            continue
        own = _own_game(e) if "rename" not in e else None
        ps = list((e.get("rename") or {}).values()) or ([own] if own else e.get("paths") or [])
        for p in ps:
            if os.path.exists(p):
                prim = primary_of(e["system"], [p], es)
                if prim:
                    want.setdefault(e["system"], set()).add(prim)
    return want


def _plan(entries, es):
    """What add_games asks ES for: (wanted(), {path: name}, {system: discs to list hidden})."""
    want, names, hides = wanted(entries, es), {}, {}
    for e in entries:
        for p in (e.get("paths") or []) + ([_own_game(e)] if _own_game(e) else []):
            if e.get("name"):
                names.setdefault(p, e["name"])
        if foreign(e["system"], es):
            continue
        for h in e.get("hide") or []:               # a set's discs: listed (hidden) with its playlist
            if os.path.exists(h):
                hides.setdefault(e["system"], set()).add(h)
                want.setdefault(e["system"], set())
    return want, names, hides


def add_games(esapi, entries, es, lists=None):
    """List a batch's new files in ES without a full reload: per system, POST
    /addgames/<system> with just the games ES doesn't list yet (an entry for
    a listed game would reset what ES keeps beyond the tags we send). True
    when ES lists them all now (or nothing was missing), False when the
    batch needs /reloadgames: it renames or removes games (ES must also
    forget the old files), or ES didn't take them — Batocera 43.1's ES
    answers 204 to every /addgames (fixed upstream 2026-07), an older one
    may not know the file, the address may be down. When ES reloads by itself
    (a new system), wait_listed's answer. lists: the batch's GameLists."""
    lists = lists if lists is not None else GameLists(esapi)
    if any("remove" in e for e in entries) or any(
            not renamed(esapi, e, es, lists) for e in entries if "rename" in e and not foreign(e["system"], es)):
        return False
    want, names, hides = _plan(entries, es)
    for system, paths in want.items():
        listed = lists.get(system)
        if listed is None and lists.unknown(system):    # a game ES lists would be reset by /addgames
            rommlib.log("finalize: couldn't read ES's %s list; its new games wait for the next run" % system)
            continue
        listed = listed or {}
        new = {p: ({"name": names[p]} if names.get(p) else {}) for p in sorted(paths) if p not in listed}
        new.update({h: {"hidden": "true"} for h in sorted(hides.get(system, ())) if h not in listed and h not in new})
        if not new:
            continue
        lists.drop(system)
        try:
            st = esapi.add_games(system, new)
        except (ValueError, UnicodeError):          # a name XML can't carry: reload instead
            return False
        if st == 201:                               # a system ES hadn't loaded: it reloads by itself
            rommlib.log("finalize: %d game(s) added to ES (%s, new system; ES reloads)" % (len(new), system))
            lists.drop()
            return wait_listed(esapi, entries, es, lists=lists)    # the reload lists the rest too
        listed = (lists.get(system) or {}) if st == 200 else {}
        if st != 200 or any(p not in listed for p in paths):
            rommlib.log("finalize: ES didn't take the new games (%s: %s); reloading all" % (
                system, "HTTP %d" % st if st else "no answer"))
            return False
        rommlib.log("finalize: %d game(s) added to ES (%s)" % (len(new), system))
    return True


def renamed(esapi, e, es, lists=None):
    """ES lists a rename's new files and none of its old ones that are gone (an entry
    deferred after a reload that did that): it needs no other reload."""
    system = e["system"]
    listed = (lists if lists is not None else GameLists(esapi)).get(system)
    if listed is None:
        return False
    for old, new in (e.get("rename") or {}).items():
        if os.path.exists(new):
            prim = primary_of(system, [new], es) or new
            if prim not in listed:
                return False
        old = os.path.normpath(old)
        if any((p == old or p.startswith(old.rstrip(os.sep) + os.sep)) and not os.path.exists(p) for p in listed):
            return False
    return True


class NotYet:
    """wait_listed's answer when ES still doesn't list every game: false, and loading
    says whether ES looked busy loading still (it listed more as the wait went on, a
    system wasn't listed at all, or ES wasn't answering), so waiting more may help."""

    def __init__(self, loading):
        self.loading = loading

    def __bool__(self):
        return False

    def __repr__(self):
        return "NotYet(loading=%r)" % self.loading


def wait_listed(esapi, entries, es, timeout=180, settle=6, lists=None):
    """After /reloadgames: wait until ES lists the systems (and new files) we
    need — the reload runs on ES's UI thread after the request returns. True,
    or NotYet. lists: the batch's GameLists, given what ES lists now."""
    want = wanted(entries, es)
    if not want:
        return True
    time.sleep(settle)
    t0 = time.time()
    first = least = None
    loading = False
    while True:
        missing, loading = 0, False
        for system, paths in want.items():
            try:
                gs = esapi.games(system, refresh=True)
            except EsError:                    # ES busy reloading (not answering yet): wait on
                gs = None
            if lists is not None:
                lists.put(system, gs)
            loading = loading or gs is None    # not answering, or not listing the system (yet)
            missing += len(paths) if gs is None else sum(1 for p in paths if p not in gs)
        if not missing:
            return True
        first = missing if first is None else first
        least = missing if least is None else min(least, missing)
        if time.time() - t0 >= timeout:
            return NotYet(loading or least < first)
        time.sleep(3)


# ------------------------------------------------------------------ queue

def valid(e):
    return isinstance(e, dict) and isinstance(e.get("system"), str) and e["system"] and \
        any(k in e for k in ("paths", "hide", "rename", "remove"))


def _read_jsonl(path, rewrite=False):
    """Entries, one per line. A torn or malformed line (power cut mid-append,
    an entry that isn't one) is set aside in <file>.bad, never the batch.
    rewrite: the file is written again without them (set aside once, not on
    every read)."""
    out, bad, good = [], [], []
    try:
        with open(path, errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    bad.append(line)
                    continue
                if valid(e):
                    out.append(e)
                    good.append(line if line.endswith("\n") else line + "\n")
                else:
                    bad.append(line)
    except OSError:
        return []
    if bad:
        _set_aside(path, bad)
        rommlib.log("finalize: set aside %d unreadable queue line(s) in %s.bad" % (len(bad), os.path.basename(path)))
        if rewrite:
            rommlib.atomic_write_text(path, "".join(good))
    return out


def _set_aside(path, lines, once=False):
    """Add lines to <path>.bad (its newest BAD_MAX bytes kept). once: not those it has
    already (what a pass killed half way sets aside again)."""
    lines = [line if line.endswith("\n") else line + "\n" for line in lines]
    try:
        with open(path + ".bad", errors="replace") as f:
            old = f.read()
    except OSError:
        old = ""
    if once:
        have = set(old.splitlines(True))
        lines = [line for line in lines if line not in have]
        if not lines:
            return
    text = old + ("\n" if old and not old.endswith("\n") else "") + "".join(lines)
    if len(text) > BAD_MAX:                  # keep the newest whole lines
        text = text[text.find("\n", len(text) - BAD_MAX - 1) + 1:]
    rommlib.atomic_write_text(path + ".bad", text)


def _write_jsonl(path, entries):
    if not entries:
        if os.path.exists(path):
            os.remove(path)
        return
    rommlib.atomic_write_text(path, "".join(json.dumps(e) + "\n" for e in entries))


def route_renames(entries):
    """A CHD conversion may finish before a download's or a set's entries are
    handed over: point their paths (and a set's discs to hide) at the new
    files, using the batch's renames (a file, or anything inside a folder).
    A rename queued after an entry always applies to it (the entry was written before the
    conversion). One queued before it applies only to a path that is gone: an entry written
    after the conversion names what is there then, and the old name may be a new download
    by now (a disc fetched again under its cue name) that the rename must not send back to
    the CHD it replaced. A deferred entry queued again after the rename still follows it:
    its old files are gone."""
    renames = {}
    for e in entries:
        if isinstance(e.get("rename"), dict):
            renames.update(e["rename"])
    if not renames:
        return entries
    _RENAMED.update(renames)
    # the renames queued after each entry (the later one wins, as in renames)
    after, later = [None] * len(entries), {}
    for i in range(len(entries) - 1, -1, -1):
        after[i] = later
        r = entries[i].get("rename")
        if isinstance(r, dict) and r:
            later = {**r, **later}

    def route(p, later):
        if not isinstance(p, str):
            return p
        to = _route(p, later)
        if to != p or os.path.lexists(p):
            return to
        return _route(p, renames)
    for i, e in enumerate(entries):
        if isinstance(e.get("paths"), list):
            e["paths"] = list(dict.fromkeys(route(p, after[i]) for p in e["paths"]))
        if _own_game(e) is not None:
            e["game"] = route(e["game"], after[i])
        if isinstance(e.get("hide"), list) and "rename" not in e:
            e["hide"] = list(dict.fromkeys(route(p, after[i]) for p in e["hide"]))
    return entries


class GameStarted(RuntimeError):
    """A game started just before ES was to be reloaded: the batch waits (not its fault)."""


def _each(entries, fn, bad):
    """fn(e) for each entry; the entries it went through. bad (isolate): an entry whose fn
    raises is logged and put in bad instead, but for ES not answering or a game starting
    (EsError, GameStarted: not the entry's fault), which stop the batch."""
    if bad is None:
        for e in entries:
            fn(e)
        return list(entries)
    ok = []
    for e in entries:
        try:
            fn(e)
        except (EsError, GameStarted):
            raise
        except Exception as ex:
            rommlib.log("finalize: an entry for %s fails every time (%r); set aside" % (e.get("system"), ex))
            bad.append(e)
            continue
        ok.append(e)
    return ok


WAIT_RUNS = 5            # a source being converted: no try spent for this many runs and...
WAIT_DAYS = 2            # ...days at least; after both, it goes like any game ES doesn't list


def _num(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _hold(e, now):
    """A source being converted waits for its conversion's rename, which brings the game in:
    no try is spent, for WAIT_RUNS runs and WAIT_DAYS days. A conversion that never ends
    then no longer keeps the game's info (and Fill in game info, which leaves pending
    games be) waiting for good."""
    since = _num(e.get("_waiting_since"), now)
    e["_waiting_since"] = since
    e["_waits"] = int(_num(e.get("_waits"), 0)) + 1
    if e["_waits"] <= WAIT_RUNS or now - since < WAIT_DAYS * 86400:
        e["_wait"] = True
    elif not e.get("_waited_out"):
        e["_waited_out"] = True
        rommlib.log("finalize: stopped waiting for a conversion after %d runs: %s" % (
            e["_waits"] - 1, (e.get("paths") or ["?"])[0]))


def _check(esapi, e, es, lists):
    """Read an entry the way add_games and apply will, asking ES nothing the batch's lists
    don't hold: raises for an entry that would break them (isolate sets it aside)."""
    if "rename" in e and not foreign(e["system"], es):
        renamed(esapi, e, es, lists)
    _plan([e], es)
    _files(e)


def process(entries, api=None, es=None, esapi=None, cfg=None, bad=None):
    """One batch: route renames, snapshot old info, let ES list the new
    files (/addgames, else a full reload), apply. Returns the entries ES
    couldn't take yet (those waiting for a conversion marked "_wait", those
    kept only for media "_media"). ES's game lists are fetched once per system
    (and once more after it changes them), shared by every step (GameLists).
    bad (isolate): each step goes entry by entry, an entry that breaks one is put in bad
    and the rest go on; still one reload for them all, and RomM found down stays down."""
    es = es if es is not None else rommlib.load_es_systems()
    esapi = esapi or EsApi()
    lists = GameLists(esapi)
    for e in entries:
        e.pop("_wait", None)
        e.pop("_media", None)
    route_renames(entries)
    if bad is None:
        carry = snapshot_renames(entries, esapi, lists)
        listed = add_games(esapi, entries, es, lists)
    else:
        carry = {}
        entries = _each(entries, lambda e: carry.update(snapshot_renames([e], esapi, lists)), bad)
        entries = _each(entries, lambda e: _check(esapi, e, es, lists), bad)
        listed = add_games(esapi, entries, es, lists)       # the rest together, as a batch
    if listed is False:
        esapi.reload()
        lists.drop()
        listed = wait_listed(esapi, entries, es, lists=lists)

    def hand(batch):
        if bad is None:
            return apply(batch, api, es, esapi, carry, cfg, lists=lists)
        out = []
        _each(batch, lambda e: out.extend(apply([e], api, es, esapi, carry, cfg, fresh=False, lists=lists)), bad)
        return out
    left = hand(entries)
    late = [e for e in left if e.get("_left") == "unlisted"]
    if not listed and late and getattr(listed, "loading", False):
        # a slow full reload (a big library) still going when wait_listed gave up: once more
        # for the games it hadn't listed, instead of their info waiting for the next run. Not
        # for a game ES had stopped listing more of: it never will (a file it filters out)
        rommlib.log("finalize: ES is still loading; waiting once more for %d game(s)" % len(late))
        lists.drop()
        wait_listed(esapi, late, es, settle=0, lists=lists)
        came = {id(e) for e in late} - {id(e) for e in hand(late)}
        left = [e for e in left if id(e) not in came]
    kept = {id(e) for e in left}
    now, nosys = time.time(), {}
    for e in entries:
        why = e.pop("_left", None)
        if id(e) not in kept:
            continue
        if why == "converting":
            _hold(e, now)
        else:
            for k in ("_waits", "_waiting_since", "_waited_out"):
                e.pop(k, None)
        if why == "media":
            e["_media"] = True               # its info is in: a try for media spent by time, not by run
        elif why == "no-system":
            nosys[e["system"]] = nosys.get(e["system"], 0) + 1
    for system, n in sorted(nosys.items()):
        rommlib.log("finalize: ES has no %s system on this box; %d game(s) kept for later" % (system, n))
    for e in left:                           # keep what we knew about renamed games
        if "rename" in e:
            e["_carry"] = {new: carry[new] for new in e["rename"].values() if new in carry}
    return left


ISOLATE_AFTER = 3        # failed passes in a row (across runs) before entries go one at a time


def fails_path():
    # failed passes of the batch in .work, across runs (beside it)
    return rommlib.GAMELIST_QUEUE + ".work.fails"


def _fails():
    try:
        with open(fails_path()) as f:
            return int(f.read().strip() or 0)
    except (OSError, ValueError):
        return 0


def _set_fails(n):
    try:
        if n:
            rommlib.atomic_write_text(fails_path(), "%d\n" % n)
        elif os.path.exists(fails_path()):
            os.remove(fails_path())
    except OSError:
        pass


def _covers(old, p):
    return isinstance(p, str) and (p == old or p.startswith(old.rstrip("/") + "/"))


def related_deferred(lines):
    """The deferred entries a batch's (queue lines) renames bring in, or that bring in its
    downloads: a download that waited for its conversion and the conversion's rename go
    to ES together (route_renames), even when the rename comes in a later pass of this
    run. (their lines, the deferred lines left)."""
    def parse(text):
        out = []
        for line in text.splitlines(True):
            try:
                e = json.loads(line)
            except ValueError:
                e = None
            out.append((line if line.endswith("\n") else line + "\n", e if valid(e) else None))
        return out
    batch = [e for _, e in parse(lines) if e]
    olds = [o for e in batch if isinstance(e.get("rename"), dict) for o in e["rename"]]
    paths = [p for e in batch if "rename" not in e and isinstance(e.get("paths"), list)
             for p in e["paths"] + ([e["game"]] if _own_game(e) else [])]
    try:
        with open(deferred_path(), errors="replace") as f:
            deferred = parse(f.read())
    except OSError:
        return "", None
    take, rest = [], []
    for line, e in deferred:
        hit = e is not None and (
            "rename" not in e and isinstance(e.get("paths"), list)
            and any(_covers(o, p) for o in olds for p in e["paths"] + ([e["game"]] if _own_game(e) else []))
            or isinstance(e.get("rename"), dict) and any(_covers(o, p) for o in e["rename"] for p in paths))
        (take if hit else rest).append(line)
    return ("".join(take), "".join(rest)) if take else ("", None)


def _queued():
    try:
        return os.path.getsize(rommlib.GAMELIST_QUEUE) > 0
    except OSError:
        return False


def main():
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    first = True
    while True:
        with rommlib.try_lock(lock_path()) as fd:
            if fd is None:
                return  # another finalize is already waiting; it will pick ours up
            done = drain(retry_deferred=first)
        first = False
        # an entry queued while this one was finishing: its own finalize found the lock
        # still held and left, so it is this one's (unless another took the lock since)
        if not done or not _queued():
            return


def isolate(entries, api, cfg):
    """A batch that failed ISOLATE_AFTER passes in a row: its entries one at a time, so
    one that always fails (set aside in .work.bad, logged) no longer holds back the rest;
    ES still reloads once for them all (process(bad=...)). (left, set aside). ES not
    answering or a game starting is nobody's fault: raised, the batch stays in .work.
    When every entry fails, it isn't one entry's fault either: raised."""
    bad = []
    _ROMM_DOWN.pop(id(api), None)              # RomM asked afresh for this batch, once
    try:
        left = process(entries, api, cfg=cfg, bad=bad)
    finally:
        _ROMM_DOWN.pop(id(api), None)
    if bad and len(bad) == len(entries) > 1:
        raise RuntimeError("every entry of the batch failed")
    return left, bad


MEDIA_TRY_GAP = 6 * 3600     # an entry kept only for media: one try spent per this much time at most


def done_path():
    # a pass's outcome ({"left": entries kept for later, "bad": entries set aside}), written
    # before .work goes and moved on after it (_settle): a kill at any point loses nothing
    # and adds nothing
    return rommlib.GAMELIST_QUEUE + ".work.done"


def _key(e):
    """An entry as it asks ES for something: its run-to-run bookkeeping left out."""
    return json.dumps({k: v for k, v in e.items() if not k.startswith("_") and k != "fill"}, sort_keys=True)


def _unique(entries):
    """Entries, each one once (the first; the most tries, and fill if any copy has it)."""
    out, at = [], {}
    for e in entries:
        k = _key(e)
        if k not in at:
            at[k] = e
            out.append(e)
            continue
        first = at[k]
        tries = max(int(_num(first.get("_tries"), 0)), int(_num(e.get("_tries"), 0)))
        if tries:
            first["_tries"] = tries
        if e.get("fill"):
            first["fill"] = True
    return out


def _unique_lines(text):
    """Queue text with each line once: a claim killed half way (.work written, the queue or
    the deferred file not yet gone) takes the same lines again."""
    seen, out = set(), []
    for line in text.splitlines(True):
        line = line if line.endswith("\n") else line + "\n"
        if line.strip() and line not in seen:
            seen.add(line)
            out.append(line)
    return "".join(out)


def _spend(left, now):
    """The entries kept for later, a try spent for each but those waiting for their
    conversion, and those kept only for media (one try per MEDIA_TRY_GAP: an evening
    with RomM offline doesn't use them all up). Those out of tries are logged."""
    keep = []
    for e in left:
        wait, media = e.pop("_wait", None), e.pop("_media", None)
        if wait:
            pass
        elif media and now - _num(e.get("_tried"), 0) < MEDIA_TRY_GAP:
            pass
        else:
            e["_tries"] = int(_num(e.get("_tries"), 0)) + 1
            if media:
                e["_tried"] = now
        if int(_num(e.get("_tries"), 0)) < MAX_TRIES:
            keep.append(e)
        else:
            rommlib.log("finalize: giving up on an entry for %s after %d tries: %s" % (
                e.get("system"), MAX_TRIES, (e.get("paths") or list((e.get("rename") or {}).values()) or ["?"])[0]))
    return keep


def _settle():
    """Finish a pass whose .work is gone: what it left into the deferred file and what it
    set aside into .work.bad (each once: this may run again after a kill half way), then
    forget .done. A .done beside its .work is from a pass killed before it was through:
    the batch goes again. OSError: the disk can't take it now (.done stays)."""
    p, work = done_path(), rommlib.GAMELIST_QUEUE + ".work"
    if not os.path.exists(p):
        return
    if os.path.exists(work):
        os.remove(p)
        return
    try:
        with open(p, errors="replace") as f:
            out = json.load(f)
    except ValueError:
        out = None
    if not isinstance(out, dict):             # torn or emptied (a power cut on exFAT): keep it aside
        rommlib.log("finalize: %s doesn't parse; what the last pass kept for later is lost (file kept as .done.bad)" % p)
        os.replace(p, p + ".bad")
        return
    left = [e for e in out.get("left") or [] if valid(e)] if isinstance(out.get("left"), list) else []
    bad = [e for e in out.get("bad") or [] if isinstance(e, dict)] if isinstance(out.get("bad"), list) else []
    if left:
        _write_jsonl(deferred_path(), _unique(_read_jsonl(deferred_path()) + left))
    if bad:
        _set_aside(work, [json.dumps(e) for e in bad], once=True)
    _set_fails(0)
    os.remove(p)


def drain(retry_deferred=True):
    """Hand ES every queued entry (under the lock), waiting for it to go idle first.
    True once the queue is empty; False when it stopped with entries still waiting.
    A kill (or a full disk) at any point loses no entry and leaves none twice: the
    queue is claimed into .work, each pass's outcome goes to .done before .work goes,
    and every step can run again (_settle, _unique_lines)."""
    work = rommlib.GAMELIST_QUEUE + ".work"
    try:
        _settle()                               # the last run's outcome, when a kill cut it short
    except OSError as ex:
        rommlib.log("finalize: can't settle the last pass (%r); next time" % ex)
        return False
    # entries ES couldn't take last time: try them again, once per run
    deferred = _read_jsonl(deferred_path()) if retry_deferred else []
    if deferred:
        rommlib.append_gamelist(*deferred)      # queue again first, then forget (a kill between: _unique_lines)
        _write_jsonl(deferred_path(), [])
    failures = 0
    while True:
        if not _queued() and not os.path.exists(work):
            return True
        if not wait_idle():
            rommlib.log("finalize: gave up waiting for ES to go idle")
            return False
        # claim the queue (under the writers' lock); a .work left by an
        # interrupted run goes in front of anything newer
        with rommlib.queue_lock():
            try:
                _settle()                       # a pass whose outcome the disk couldn't take
            except OSError as ex:
                rommlib.log("finalize: can't settle the last pass (%r); next time" % ex)
                return False
            old = ""
            if os.path.exists(work):
                with open(work, errors="replace") as f:
                    old = f.read()
            new = ""
            if os.path.exists(rommlib.GAMELIST_QUEUE):
                with open(rommlib.GAMELIST_QUEUE, errors="replace") as f:
                    new = f.read()
            if old and not old.endswith("\n"):
                old += "\n"
            if not old.strip():
                _set_fails(0)          # failed passes count for a .work left over, not a new batch
            # deferred entries that go with this batch's renames (or its downloads): with it
            pulled, rest = related_deferred(old + new)
            # .work must be durable before the queue goes: a failed write or a kill in
            # between then leaves the same lines twice, taken once next time
            rommlib.atomic_write_text(work, _unique_lines(old + new + pulled))
            if new:
                os.remove(rommlib.GAMELIST_QUEUE)
            if rest is not None:
                if rest:
                    rommlib.atomic_write_text(deferred_path(), rest)
                else:
                    os.remove(deferred_path())
        entries = _read_jsonl(work, rewrite=True)
        cfg = rommlib.load_config()
        api = rommlib.Api.from_config(cfg)
        try:
            if game_running():      # re-check right before reloading ES
                raise GameStarted("game started")
            bad = []
            if _fails() >= ISOLATE_AFTER:
                rommlib.log("finalize: the batch failed %d times; its entries one at a time" % _fails())
                left, bad = isolate(entries, api, cfg)
            else:
                left = process(entries, api, cfg=cfg)
            keep = _spend(left, time.time())
            outcome = json.dumps({"left": keep, "bad": bad})
        except Exception as e:
            # keep the batch (.work stays, in front of anything new) and retry later
            failures += 1
            # a game starting or ES not answering isn't the batch's fault: no step to one at a time
            if not isinstance(e, (GameStarted, EsError)):
                _set_fails(_fails() + 1)
            rommlib.log("finalize: deferred (%r)" % e)
            if failures >= MAX_TRIES:
                rommlib.log("finalize: stopping after %d failed passes; the batch waits for the next run" % failures)
                return False
            time.sleep(30)
            continue
        try:
            rommlib.atomic_write_text(done_path(), outcome)
            os.remove(work)                     # the batch is through: what it left is in .done
            _settle()
        except OSError as ex:                   # a full disk: not the batch's fault, nothing counted
            failures += 1
            if not os.path.exists(work):        # through, its outcome in .done: saved next run
                rommlib.log("finalize: can't save what the batch left (%r); saved next time" % ex)
                return False
            rommlib.log("finalize: can't save what the batch left (%r); it goes again" % ex)
            if failures >= MAX_TRIES:
                return False
            time.sleep(30)
            continue
        rommlib.log("finalize: %d entries to ES, %d kept for later%s" % (
            len(entries) - len(left) - len(bad), len(keep), ", %d set aside" % len(bad) if bad else ""))


if __name__ == "__main__":
    main()
