"""Downloads: the queue, the workers, space planning."""
import json
import os
import re
import shutil
import stat
import subprocess
import threading
import time
import urllib.parse
import zipfile

import requests

from . import (core as _core, config as _config, net as _net, discs as _discs, es as _es, ps3 as _ps3, convert as _convert,
               library as _library, leftovers as _leftovers)


# Systems whose downloads are usually archives of disc images: extracting
# roughly doubles them on disk (before any CHD compression).
EXPANDS = {"psx", "ps2", "ps3", "psp", "saturn", "dreamcast", "megacd", "pcenginecd", "neogeocd",
           "3do", "amigacd32", "amigacdtv", "cdi", "pcfx", "gamecube", "wii", "wiiu", "xbox",
           "xbox360", "3ds", "jaguarcd", "fmtowns"}


# A Wii U disc image (.wud) is always the whole disc, whatever the game.
WUD_SIZE = 25_025_314_816


def _archived(rom):
    """The rom's archive files as RomM lists them, [] for loose files. Without
    a file list (a list record) its own file name says: None for an archive
    (or when nothing says), [] for anything else."""
    files = [f for f in rom.get("files") or [] if f.get("category") in (None, "game")]
    if not files:
        name, ext = rom.get("fs_name") or "", (rom.get("fs_extension") or "").lower()
        if not name and not ext:
            return None                       # nothing says
        ext = "." + ext if ext else os.path.splitext(name)[1].lower()
        return None if ext in _core.ARCHIVE_EXTS else []
    return [f for f in files if os.path.splitext(f.get("file_name") or "")[1].lower() in _core.ARCHIVE_EXTS]


def _romm_members(f):
    """An archive file's members as RomM listed them, when it gave every size."""
    ms = f.get("archive_members") or []
    return ms if ms and all(isinstance(m.get("size"), int) for m in ms) else None


# Archive listings read from the server (zip_listing), by (rom id, size): a
# record fetched again (the download prompt's) finds what the downloader read.
_LISTINGS = {}
LISTINGS_KEPT = 2000


def _listing(rom):
    """{archive name: [{"name", "size"}]} read from the server for this rom,
    False when it couldn't be read, None when nobody tried."""
    got = rom.get("_members")
    if got is None:
        got = _LISTINGS.get((rom.get("id"), rom.get("fs_size_bytes")))
    return got


def _listed_members(listed, name, arcs):
    """One archive's members from a listing; a rom of a single archive takes
    the listing's single entry even when it was read under the rom's name."""
    ms = listed.get(name)
    if ms is None and len(arcs) == 1 and len(listed) == 1:
        ms = next(iter(listed.values()))
    return ms


def _unpacked(rom, system):
    """(bytes, exact) for unpacked_size."""
    if system in _core.ZIP_NATIVE:
        return None, False
    arcs = _archived(rom)
    if arcs == []:
        return None, False
    listed = _listing(rom) or {}
    if arcs:
        total = 0
        for f in arcs:
            ms = _romm_members(f)
            if ms is None:
                ms = _listed_members(listed, f.get("file_name"), arcs)
            if ms is None:
                break
            total += sum(m["size"] for m in ms)
        else:
            return total, True
    elif rom.get("fs_name") in listed:
        return sum(m["size"] for m in listed[rom["fs_name"]]), True
    return (WUD_SIZE, False) if system == "wiiu" else (None, False)


def unpacked_size(rom, system):
    """What a downloaded archive unpacks to, in bytes, when that's known:
    the sum of its members' sizes when RomM listed them (it does for the
    platforms it hashes) or the zip's own listing was read from the server
    (zip_listing), else for Wii U at most a full disc image (.wud; RomM lists
    no members for Wii U). None otherwise: not an archive, or nothing says."""
    return _unpacked(rom, system)[0]


def unpacked_upper_bound(rom, system):
    """unpacked_size is only the most it can be: a Wii U zip whose listing
    hasn't been (or couldn't be) read counts as a whole disc image."""
    size, exact = _unpacked(rom, system)
    return size is not None and not exact


def space_needed(rom, system):
    """Bytes on disk once downloaded and extracted, at the peak (the archive
    and what it unpacks to side by side): exact when the archive's unpacked
    size is known (unpacked_size), else about 2.2 times the download for the
    systems whose downloads are usually zipped disc images."""
    size = rom.get("fs_size_bytes") or 0
    if system in EXPANDS:
        inner = unpacked_size(rom, system)
        if inner is not None:
            return size + inner
        if _archived(rom) == []:
            return size                  # loose files: nothing to unpack
    return int(size * (2.2 if system in EXPANDS else 1.0))


# -- reading an archive's member list from the server ----------------------
# A zip lists its members at its end (the central directory, found from the
# end-of-central-directory record), so two or three small ranged GETs tell
# what a multi-GB download unpacks to.
ZIP_TAIL = 22 + 0xFFFF + 20 + 56     # EOCD + the longest comment + Zip64 locator and record
ZIP_DIR_MAX = 16 << 20               # a central directory bigger than this isn't read


class Unlistable(Exception):
    """The server's copy of an archive can't be listed (not a zip, no Range)."""


def needs_listing(rom, system):
    """The archives of this rom whose members only a ranged read would tell
    (RomM didn't list them, and nobody has read them yet): file names, [] when
    there is nothing to read."""
    if system not in EXPANDS or system in _core.ZIP_NATIVE or _listing(rom) is not None:
        return []
    arcs = _archived(rom)
    if arcs is None:
        name = rom.get("fs_name") or ""
        return [name] if name else []
    return [f.get("file_name") for f in arcs if _romm_members(f) is None]


class _RangedFile:
    """The read-only, seekable file zipfile needs, each read a ranged GET;
    the tail (where a zip's directory ends) is fetched once."""

    def __init__(self, get, size):
        self.get, self.size, self.pos = get, size, 0
        self.tail_at, self.tail = max(0, size - ZIP_TAIL), None

    def seekable(self):
        return True

    def seek(self, off, whence=0):
        self.pos = max(0, off if whence == 0 else self.pos + off if whence == 1 else self.size + off)
        return self.pos

    def tell(self):
        return self.pos

    def read(self, n=-1):
        n = self.size - self.pos if n is None or n < 0 else min(n, self.size - self.pos)
        if n <= 0:
            return b""
        if self.pos >= self.tail_at:
            if self.tail is None:
                self.tail = self.get(self.tail_at, self.size - 1)
            data = self.tail[self.pos - self.tail_at:self.pos - self.tail_at + n]
        else:
            if n > ZIP_DIR_MAX:
                raise Unlistable("central directory too big")
            data = self.get(self.pos, self.pos + n - 1)
        self.pos += len(data)
        return data


def _ranged_get(api, path, size):
    """get(start, end) -> those bytes of path on the RomM server, or Unlistable
    when the server doesn't answer with exactly that range of a file that size."""
    def get(start, end):
        try:
            r = api.s.get(api.base + path, headers={"Range": "bytes=%d-%d" % (start, end)},
                          stream=True, timeout=(10, 30))
        except requests.RequestException as e:      # an OSError: zipfile would call it "not a zip"
            raise NetError("Archive listing interrupted (%s)." % e.__class__.__name__) from None
        try:
            want = "bytes %d-%d/%d" % (start, end, size)
            if r.status_code != 206 or (r.headers.get("Content-Range") or "").strip() != want:
                raise Unlistable("no Range (HTTP %d)" % r.status_code)
            data, n = [], end - start + 1
            try:
                for chunk in r.iter_content(1 << 16):
                    data.append(chunk)
                    n -= len(chunk)
                    if n < 0:
                        raise Unlistable("more than the range asked for")
            except requests.RequestException as e:
                raise NetError("Archive listing interrupted (%s)." % e.__class__.__name__) from None
            if n:
                raise Unlistable("short range")
            return b"".join(data)
        finally:
            r.close()
    return get


def zip_listing(api, rom, system):
    """Read the member lists of this rom's unlisted archives (needs_listing)
    from the server with ranged GETs: {archive name: [{"name", "size"}]}, or
    False when one can't be read (not a zip, Zip or Range trouble). Kept on
    the rom (rom["_members"]) and in _LISTINGS. A network error raises
    NetError and keeps nothing, so a later try reads it again."""
    names = needs_listing(rom, system)
    if not names:
        return _listing(rom)
    files = [f for f in rom.get("files") or [] if f.get("category") in (None, "game")]
    by_name = {f.get("file_name"): f for f in files}
    out = {}
    try:
        for name in names:
            if not name.lower().endswith(".zip"):
                raise Unlistable("not a zip")
            f = by_name.get(name)
            if len(files) > 1:            # one archive of a folder rom: its own file
                if not (f and f.get("id")):
                    raise Unlistable("no file id")
                path, size = "/api/roms/%d/files/content/%s" % (f["id"], urllib.parse.quote(name)), \
                    f.get("file_size_bytes") or 0
            else:                         # the whole rom is this zip
                path = "/api/roms/%d/content/%s" % (rom["id"], urllib.parse.quote(rom.get("fs_name") or name))
                size = (f or {}).get("file_size_bytes") or rom.get("fs_size_bytes") or 0
            if size < 22:
                raise Unlistable("size unknown")
            with zipfile.ZipFile(_RangedFile(_ranged_get(api, path, size), size)) as z:
                out[name] = [{"name": i.filename, "size": i.file_size} for i in z.infolist() if not i.is_dir()]
    except (Unlistable, zipfile.BadZipFile, zipfile.LargeZipFile, ValueError, KeyError, EOFError) as e:
        _core.log("LIST %s: can't read the archive's member list (%s)" % (rom.get("fs_name"), e))
        out = False
    if len(_LISTINGS) >= LISTINGS_KEPT:
        _LISTINGS.clear()
    _LISTINGS[(rom.get("id"), rom.get("fs_size_bytes"))] = rom["_members"] = out
    return out


def _lands(rom, system):
    """(bytes the game takes once unpacked, bytes more while its archive is
    still beside them): what space_needed adds up, split. The archive goes as
    soon as it's unpacked; never count less than the download itself."""
    size = rom.get("fs_size_bytes") or 0
    need = space_needed(rom, system)
    if system in EXPANDS and system not in _core.ZIP_NATIVE and _archived(rom) != []:
        return max(need - size, size), size
    return need, 0


def space_for(items):
    """Bytes a batch of downloads [(rom, system)] needs at its peak: one
    archive at a time is beside what it unpacks to (the next downloads while
    it unpacks, but a download is never bigger than what it lands as), so the
    biggest archive plus every game's unpacked size."""
    lands = [_lands(rom, system) for rom, system in items]
    return sum(land for land, _ in lands) + max([extra for _, extra in lands] or [0])


def queue_forecast(jobs, free, keep, chd=True, cfg=None):
    """Space the unfinished downloads still need, and whether it fits.
    Each job: what's left of it once unpacked (done bytes are already on
    disk); plus the biggest archive (beside what it unpacks to until it's
    deleted, one at a time: space_for); plus the biggest conversion's working
    space (original and output side by side until the output is verified,
    the archive long gone): every converter that's on for the job's system
    (cfg; without it, CHD only, as chd says), times its space_factor (.zar:
    the extracted files and the archive)."""
    need, extra, conv, n = 0, 0, 0, 0
    for j in jobs:
        if not (j.active or j.state == Job.PAUSED):
            continue
        n += 1
        land, beside = _lands(j.rom, j.system)
        need += max(0, land - (j.done_bytes or 0))
        extra = max(extra, beside)
        if cfg is None:
            factor = 1 if chd and j.system in _core.CHD_CD_SYSTEMS | _core.CHD_DVD_SYSTEMS else 0
        else:
            factor = max([c.space_factor for c in _convert.converters_for(j.system)
                          if (c.chd and chd) or (not c.chd and c.enabled(cfg, j.system))] or [0])
        conv = max(conv, land * factor)
    total = need + extra + conv
    return {"jobs": n, "need": total, "free": free, "keep": keep,
            "fits": free - total >= keep, "short": max(0, total + keep - free)}


def fit_in_space(items, free, reserve=_core.SPACE_MARGIN):
    """Greedy, keeping the given order: (that_fit, total_needed_for_them),
    the total counted like space_for."""
    fit, landed, extra = [], 0, 0
    for rom, system in items:
        land, beside = _lands(rom, system)
        if landed + land + max(extra, beside) + reserve <= free:
            fit.append((rom, system))
            landed, extra = landed + land, max(extra, beside)
    return fit, landed + extra


# ----------------------------------------------------------- downloads

class Job:
    QUEUED, DOWNLOADING, VERIFYING, EXTRACTING, DONE, FAILED, CANCELLED, PAUSED = (
        "Queued", "Downloading", "Verifying", "Extracting", "Done", "Failed", "Cancelled", "Paused")

    def __init__(self, rom, system, group=None):
        self.rom = rom
        self.system = system
        self.group = group            # multi-disc set: {"id", "base", "rom_ids"}
        self.state = Job.QUEUED
        self.done_bytes = 0
        self.total_bytes = rom.get("fs_size_bytes") or 0
        self.speed = 0.0
        self.message = ""
        self.chd_queued = False
        self.chd_task = None
        self.verified = None          # True once every file matched RomM's checksum
        self.attempts = 0
        self.retry_at = 0.0
        self.pause = False            # cancel, but keep it in the saved queue
        self.resume = False           # resumed while still stopping: queue it again once it has
        self.force = False            # "Download now": ignore the download hours
        self.user_paused = False      # paused by the user (not by "Pause all" / exit)
        self.short_space = 0          # waiting for space: bytes still to free
        self.replace = None           # broken copy under another name, removed once this one is in
        self.listing_tried = False    # the forecast's archive listing (zip_listing) was tried
        self.cancel = threading.Event()

    @property
    def name(self):
        return self.rom.get("name") or self.rom.get("fs_name_no_ext") or "?"

    @property
    def label(self):
        """Name plus "Disc n" for a disc of a set (RomM often names every
        disc of a game the same)."""
        d = _library.disc_info(self.rom.get("fs_name_no_ext") or "") if self.group else None
        if d and not _library.disc_info(self.name):
            return "%s (Disc %d)" % (self.name, d[1])
        return self.name

    @property
    def active(self):
        return self.state in (Job.QUEUED, Job.DOWNLOADING, Job.VERIFYING, Job.EXTRACTING)

    def requeue(self):
        self.state, self.message, self.pause, self.resume = Job.QUEUED, "", False, False
        self.short_space = 0
        self.user_paused, self.force = False, False
        self.cancel = threading.Event()
        self.done_bytes, self.speed, self.attempts, self.retry_at = 0, 0.0, 0, 0.0


class Cancelled(Exception):
    pass


class OutsideHours(Exception):
    """The download hours ended mid-transfer: back to the queue, .part kept."""


class NetError(_core.RommError):
    """Transient: worth retrying (connection drop, timeout, 5xx/429)."""


class ChecksumError(_core.RommError):
    """The file arrived but doesn't match RomM's checksum."""


RETRY_DELAYS = (15, 60, 180)        # seconds before attempts 2, 3, 4
SPACE_RECHECK = 30                  # seconds between free-space checks for a waiting download
WORKER_POLL = 1.0                   # seconds a download/unpack worker sleeps before re-checking its queue


# RomM hashes an archive by what's inside it (a zip's crc/md5/sha1 are its
# member's), so these never match the file itself; zips are checked member by
# member instead, the rest by size only.
CONTENT_HASHED = (".zip", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz")


def zip_matches(path, members):
    """(True|False, "zip"): every RomM archive member is in the zip with the
    same size and CRC, and the data inside passes its CRCs (testzip)."""
    try:
        with zipfile.ZipFile(path) as z:
            infos = [i for i in z.infolist() if not i.is_dir()]
            by_name = {i.filename.lower(): i for i in infos}
            by_base = {os.path.basename(i.filename).lower(): i for i in infos}
            for m in members:
                n = (m.get("name") or "").lower()
                i = by_name.get(n) or by_base.get(os.path.basename(n))
                if i is None:
                    return False, "zip"
                if m.get("size") is not None and i.file_size != m["size"]:
                    return False, "zip"
                if m.get("crc_hash") and ("%08x" % i.CRC) != m["crc_hash"].lower().zfill(8):
                    return False, "zip"
            if z.testzip() is not None:
                return False, "zip"
    except (NotImplementedError, RuntimeError):
        return None, None             # a method Python can't decompress (Deflate64, encrypted): size only
    except (zipfile.BadZipFile, OSError, EOFError):
        return False, "zip"
    return True, "zip"


def zip_content_ok(path, f):
    """A zip RomM recorded without its member list: when it holds one file,
    RomM's file-level hashes are that file's (content hashing), so hash the
    decompressed member (reading it also checks the zip's own CRC).
    (None, None) for several members (RomM's combined hash can't be redone)."""
    import hashlib
    import zlib
    want = [(k, a) for k, a in (("sha1_hash", "sha1"), ("md5_hash", "md5"), ("crc_hash", "crc")) if f.get(k)]
    if not want:
        return None, None
    key, algo = want[0]
    try:
        with zipfile.ZipFile(path) as z:
            infos = [i for i in z.infolist() if not i.is_dir()]
            if len(infos) != 1:
                return None, None
            h = hashlib.new(algo) if algo != "crc" else None
            c = 0
            with z.open(infos[0]) as fh:
                for b in iter(lambda: fh.read(1 << 22), b""):
                    if h:
                        h.update(b)
                    else:
                        c = zlib.crc32(b, c)
    except (NotImplementedError, RuntimeError):
        return None, None             # can't decompress it here: size only
    except (zipfile.BadZipFile, OSError, EOFError):
        return False, "zip"
    got = h.hexdigest() if h else "%08x" % c
    return got == f[key].lower().zfill(len(got)), "zip"


def file_hash_ok(path, f, name=None):
    """(ok, algo) — compare a file with a RomM file record's checksums.
    ok is None when RomM's record can't vouch for it (no hash, or an archive
    type RomM hashes by content)."""
    import hashlib
    import zlib
    f = f or {}
    name = (name or f.get("file_name") or path).lower()
    if name.endswith(".zip"):
        members = f.get("archive_members") or []
        return zip_matches(path, members) if members else zip_content_ok(path, f)
    if name.endswith(CONTENT_HASHED):
        return None, None
    for key, algo in (("sha1_hash", "sha1"), ("md5_hash", "md5"), ("crc_hash", "crc")):
        want = f.get(key)
        if not want:
            continue
        if algo == "crc":
            c = 0
            with open(path, "rb") as fh:
                for b in iter(lambda: fh.read(1 << 22), b""):
                    c = zlib.crc32(b, c)
            return ("%08x" % c) == want.lower().zfill(8), algo
        h = hashlib.new(algo)
        with open(path, "rb") as fh:
            for b in iter(lambda: fh.read(1 << 22), b""):
                h.update(b)
        return h.hexdigest() == want.lower(), algo
    return None, None


class Downloader:
    """One worker thread; downloads are sequential (one at a time saturates a
    typical link). The queue is the ordered list self.jobs: the worker takes
    the first Queued job, so reordering the list reorders the queue."""

    SLIM_KEYS = ("id", "name", "fs_name", "fs_name_no_ext", "fs_size_bytes", "platform_id",
                 "platform_slug", "regions", "summary", "metadatum", "path_cover_small",
                 "path_cover_large", "has_multiple_files", "has_nested_single_file", "_members",
                 "languages", "has_manual", "path_manual")

    def __init__(self, api, library, cfg, persist=True):
        self.api = api
        self.library = library
        self.cfg = cfg
        self.persist = persist
        self.qlock = threading.Lock()
        self.cv = threading.Condition()
        self.hold = False             # "Pause all": the worker starts nothing new
        self.rate_limit = 0           # bytes/s, 0 = unlimited (rb_dl.py sets it while a game runs)
        self.unpacking_bytes = 0      # space games handed to the post thread will still take
        self.es = _es.load_es_systems()
        self.unopenable = _es.load_es_unopenable()  # listed by ES, but its emulator can't open them
        self.jobs = []
        self._closed = False
        Downloader.instances.append(self)
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()
        threading.Thread(target=self._list_run, daemon=True).start()

    instances = []

    def close(self):
        """Stop the worker (after its current job; used by tests)."""
        self.pause_all()
        with self.cv:
            self._closed = True
            self.cv.notify_all()
        self.t.join(30)

    # -- queue ------------------------------------------------------------
    def enqueue(self, rom, system, group=None, replace=None, save=True):
        """replace: paths of a broken copy under another name (Check games): once
        RomM's copy is in, saves move over, playlists follow, and they're deleted.
        save=False: the caller saves the queue once when it's done adding."""
        with self.cv:
            for j in self.jobs:
                if j.rom["id"] == rom["id"] and j.active:
                    if replace:
                        j.replace = sorted(set(j.replace or []) | set(replace))
                    return j
            for j in self.jobs:                      # re-use a paused/failed entry in place
                if j.rom["id"] == rom["id"] and j.state in (Job.PAUSED, Job.FAILED, Job.CANCELLED):
                    j.requeue()
                    j.group = group or j.group
                    self.cv.notify_all()
                    break
            else:
                j = Job(rom, system, group)
                self.jobs.append(j)
                self.cv.notify_all()
            if replace:
                j.replace = sorted(set(j.replace or []) | set(replace))
        if save:
            self.save_queue()
        return j

    def move(self, job, where):
        """where: "top", "up" or "down" (among the jobs not yet finished)."""
        with self.cv:
            if job not in self.jobs:
                return
            i = self.jobs.index(job)
            self.jobs.pop(i)
            if where == "top":
                first = next((k for k, j in enumerate(self.jobs) if j.state == Job.QUEUED), len(self.jobs))
                self.jobs.insert(first, job)
            elif where == "up":
                self.jobs.insert(max(0, i - 1), job)
            else:
                self.jobs.insert(min(len(self.jobs), i + 1), job)
        self.save_queue()

    def hold_all(self):
        """Pause the whole queue (the running transfer stops; its .part stays)."""
        with self.cv:
            self.hold = True
            for j in self.jobs:
                if j.state in (Job.DOWNLOADING, Job.VERIFYING, Job.EXTRACTING):
                    j.pause, j.resume = True, False
                    j.cancel.set()
        self.save_queue()

    def release_all(self):
        with self.cv:
            self.hold = False
            for j in self.jobs:
                if j.user_paused:                                  # one you paused yourself stays paused
                    continue
                if j.state == Job.PAUSED:
                    j.requeue()
                elif j.pause and j.active:                         # Pause all hasn't stopped it yet
                    j.resume = True
            self.cv.notify_all()
        self.save_queue()

    def force_job(self, job):
        """Download this one now, outside the download hours."""
        with self.cv:
            job.force = True
            if job.state in (Job.PAUSED, Job.FAILED, Job.CANCELLED):
                job.requeue()
                job.force = True
            self.cv.notify_all()

    def pause_job(self, job):
        """Pause one download (keeps its .part and its place in the queue)."""
        with self.cv:
            job.user_paused = True
            if job.state == Job.QUEUED:
                job.state, job.message = Job.PAUSED, "Paused"
            elif job.active:
                job.pause, job.resume = True, False
                job.cancel.set()
        self.save_queue()

    def resume_job(self, job):
        with self.cv:
            if job.state in (Job.PAUSED, Job.FAILED, Job.CANCELLED):
                job.requeue()
                self.cv.notify_all()
            elif job.pause and job.active:                         # its pause hasn't stopped it yet
                job.resume = True
        self.save_queue()

    def _part_files(self, job):
        """A download's partial files on disk (only .part files of that rom,
        only inside roms/<system>)."""
        sysdir = os.path.join(_core.ROMS_ROOT, job.system)
        base = _core.rom_dest(job.rom, job.system)
        if not base:
            return []
        cands = [base + ".part"]
        if os.path.isdir(base):
            cands += [os.path.join(dp, f) for dp, _, fs in os.walk(base) for f in fs if f.endswith(".part")]
        return [p for p in cands if os.path.isfile(p) and _core._inside(p, sysdir)]

    def part_bytes(self, job):
        """Bytes of a download's partial files on disk (what cancelling deletes)."""
        n = 0
        for p in self._part_files(job):
            try:
                n += os.path.getsize(p)
            except OSError:
                pass
        return n

    def _drop_parts(self, job):
        """Delete a cancelled/removed download's partial files."""
        for p in self._part_files(job):
            try:
                os.remove(p)
            except OSError:
                pass

    def cancel_job(self, job):
        drop = False
        with self.cv:
            if job.state in (Job.QUEUED, Job.PAUSED):
                job.state, job.message = Job.CANCELLED, "Cancelled"
                drop = True
            elif job.active:
                job.pause = job.resume = False
                job.cancel.set()               # the worker drops the partial when it stops
        if drop:
            self._drop_parts(job)
        self.save_queue()

    def remove(self, job):
        """Drop a finished/failed/cancelled/paused job from the list."""
        drop = False
        with self.cv:
            if job in self.jobs and not (job.active and job.state != Job.QUEUED):
                if job.state == Job.QUEUED:
                    job.state = Job.CANCELLED
                drop = job.state != Job.DONE
                self.jobs.remove(job)
        if drop:
            self._drop_parts(job)
        self.save_queue()

    def clear_finished(self):
        with self.cv:
            n = len(self.jobs)
            self.jobs = [j for j in self.jobs if j.active or j.state == Job.PAUSED]
            return n - len(self.jobs)

    # -- persistence: pending jobs survive exit, reboot, or a hand-over to rb_dl.py
    def save_queue(self):
        if not self.persist:
            return
        try:
            self._save_queue()
        except OSError as e:          # e.g. disk full: never take the worker down
            _core.log("queue save failed: %r" % e)

    def _save_queue(self):
        with self.qlock:
            # the game's media for ES ride along as paths (_es_media), not RomM's whole metadata
            pend = [{"rom": dict({k: j.rom.get(k) for k in self.SLIM_KEYS}, _es_media=_library.es_media(j.rom)),
                     "system": j.system,
                     "group": j.group, "paused": bool(j.user_paused), "replace": j.replace or None}
                    for j in list(self.jobs) if j.active or j.state == Job.PAUSED]
            os.makedirs(_core.DATA_DIR, exist_ok=True)
            _core.atomic_write_json(_core.QUEUE_PATH, {"held": self.hold, "jobs": pend})

    @staticmethod
    def saved_queue():
        try:
            with open(_core.QUEUE_PATH) as f:
                data = json.load(f)
        except OSError:
            return []
        except ValueError:
            _core._keep_unreadable(_core.QUEUE_PATH)         # the next save replaces it: keep what's there
            return []
        return data.get("jobs", []) if isinstance(data, dict) else data   # old format: a list

    @staticmethod
    def saved_held():
        try:
            with open(_core.QUEUE_PATH) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return False
        return bool(isinstance(data, dict) and data.get("held"))

    def resume(self):
        """Re-queue what a previous session (or rb_dl.py) left pending; a queue
        that was paused with "Pause all" stays paused."""
        if self.saved_held():
            with self.cv:
                self.hold = True
        n = 0
        for it in self.saved_queue():
            if it.get("rom", {}).get("id") and it.get("system"):
                j = self.enqueue(it["rom"], it["system"], it.get("group"), it.get("replace"), save=False)
                if it.get("paused"):
                    with self.cv:
                        j.user_paused = True
                        if j.state == Job.QUEUED:
                            j.state, j.message = Job.PAUSED, "Paused"
                n += 1
        if n:
            self.save_queue()            # once, whole: never a partly re-added queue on disk
        return n

    def pause_all(self):
        """Stop transfers but keep them queued (partials resume via Range)."""
        for j in list(self.jobs):
            if j.active:
                j.pause, j.resume = True, False
                j.cancel.set()
        with self.cv:
            self.cv.notify_all()
        self.save_queue()

    def pending_count(self):
        return sum(1 for j in self.jobs if j.active or j.state == Job.PAUSED)

    def job_for(self, rom_id):
        for j in reversed(self.jobs):
            if j.rom["id"] == rom_id:
                return j
        return None

    def active_count(self):
        return sum(1 for j in self.jobs if j.active)

    # -- worker -------------------------------------------------------------
    def _next(self):
        with self.cv:
            while True:
                if self._closed:
                    return None
                now = time.time()
                for j in self.jobs:
                    if self.hold and not j.force:
                        continue                 # "Pause all" ("Download now" still goes)
                    if j.state != Job.QUEUED:
                        continue
                    if j.cancel.is_set():
                        j.state = Job.PAUSED if j.pause else Job.CANCELLED
                        continue
                    if j.retry_at > now:
                        continue
                    if not j.force and not _config.in_download_hours(self.cfg):
                        continue                 # waits for the download hours
                    j.state = Job.DOWNLOADING
                    return j
                self.cv.wait(WORKER_POLL)

    def _run(self):
        while True:
            j = self._next()
            if j is None:
                return
            self.save_queue()
            try:
                post = self._process(j)
                if post is not None:
                    if self._overlap(post):
                        self._hand_off(j, post)       # unpacks in the post thread; next download starts
                        continue
                    self._post(j, post)
            except Exception as e:                # every outcome is recorded on the job
                self._failed(j, e)
            self._settled(j)

    def _failed(self, j, e):
        """Record how a job ended when it didn't finish (shared by both threads)."""
        if isinstance(e, Cancelled):
            with self.cv:                  # pause/resume/cancel decide under the same lock
                if j.pause and j.resume:   # resumed before it stopped: the .part resumes it
                    j.requeue()
                    self.cv.notify_all()
                    return
                if not j.pause:
                    self._drop_parts(j)    # before the state flips: "Cancelled" means cleaned up
                j.state = Job.PAUSED if j.pause else Job.CANCELLED
                j.message = "Paused" if j.pause else "Cancelled"
        elif isinstance(e, _core.NoSpace):
            if not j.short_space:
                _core.log("SPACE %s: %s (keeping %s free)" % (j.name, e, _core.human_size(_config.keep_free_bytes(self.cfg))))
            j.state, j.message, j.speed = Job.QUEUED, str(e), 0.0
            j.short_space, j.retry_at = e.short, time.time() + SPACE_RECHECK
        elif isinstance(e, OutsideHours):
            j.state, j.message, j.speed = Job.QUEUED, "Waiting for download hours", 0.0
            _core.log("HOURS %s: paused until %s" % (j.name, _config.hours_label(_config.config_value(self.cfg, "download_hours"))))
        elif isinstance(e, (NetError, ChecksumError)):
            j.attempts += 1
            limit = 1 if isinstance(e, ChecksumError) else len(RETRY_DELAYS)
            if j.attempts <= limit:
                delay = RETRY_DELAYS[j.attempts - 1]
                j.state, j.retry_at = Job.QUEUED, time.time() + delay
                j.message = "%s Retrying in %d s." % (e, delay)
                _core.log("RETRY %s (%d): %s" % (j.name, j.attempts, e))
            else:
                j.state, j.message = Job.FAILED, str(e)[:160]
                _core.log("FAIL %s: %s" % (j.name, j.message))
        else:  # keep the worker alive whatever happens
            j.state = Job.FAILED
            # our own errors are written to be shown; anything else could carry
            # request details, so only its type goes on screen / in the log
            j.message = (str(e) if isinstance(e, (_core.RommError, OSError, zipfile.BadZipFile))
                         else "Error (%s)" % e.__class__.__name__)[:160]
            _core.log("FAIL %s: %s" % (j.name, j.message))

    def _settled(self, j):
        if j.state == Job.DONE and j.group:
            try:
                self._finish_group(j.group, j.system)
            except Exception as e:
                _core.log("group %s: %r" % (j.group.get("base"), e))
        self.save_queue()

    # -- archive listings for the space forecast -----------------------------
    def _list_run(self):
        """Read the member list of each waiting job's archive RomM didn't list
        (zip_listing), once per job, so queue_forecast counts what it really
        unpacks to. Its own thread: a few small ranged GETs, never the UI's."""
        while True:
            with self.cv:
                while True:
                    if self._closed:
                        return
                    j = next((j for j in self.jobs if j.state in (Job.QUEUED, Job.PAUSED) and not j.listing_tried
                              and needs_listing(j.rom, j.system)), None)
                    if j is not None:
                        break
                    self.cv.wait(WORKER_POLL)
                j.listing_tried = True
                rom = j.rom
            try:
                got = zip_listing(self.api, rom, j.system)
            except Exception as e:         # NetError, or anything else: the estimate stands
                _core.log("LIST %s: %s" % (j.name, e.__class__.__name__))
                continue
            if j.rom is not rom and j.rom.get("id") == rom.get("id") and "_members" not in j.rom:
                j.rom["_members"] = got    # the job's record was replaced meanwhile
            self.save_queue()

    # -- unpacking while the next download runs ------------------------------
    allow_overlap = True          # rb_dl.py (background, mostly while a game runs) turns it off
    POST_BACKLOG = 2              # one unpacking + one waiting; the next download waits beyond that

    def _overlap(self, post):
        return self.allow_overlap and _config.config_value(self.cfg, "extract_parallel") and post.get("heavy")

    def _hand_off(self, j, post):
        with self.cv:
            if not hasattr(self, "_post_q"):
                self._post_q, self._post_busy = [], 0
                threading.Thread(target=self._post_run, daemon=True).start()
            while len(self._post_q) + self._post_busy >= self.POST_BACKLOG and not self._closed:
                self.cv.wait(WORKER_POLL)              # disk is busy enough: wait before downloading more
            j.state, j.message, j.speed = Job.EXTRACTING, "Waiting to unpack", 0.0
            self._post_q.append((j, post))
            self.unpacking_bytes += post.get("extra", 0)
            self.cv.notify_all()

    def _post_run(self):
        while True:
            with self.cv:
                while not self._post_q:
                    if self._closed:
                        return
                    self.cv.wait(WORKER_POLL)
                j, post = self._post_q.pop(0)
                self._post_busy += 1
            try:
                self._post(j, post)
            except Exception as e:
                self._failed(j, e)
            finally:
                with self.cv:
                    self._post_busy -= 1
                    self.unpacking_bytes = max(0, self.unpacking_bytes - post.get("extra", 0))
                    self.cv.notify_all()
            self._settled(j)

    def unpacking(self):
        """Jobs waiting for or in the post thread."""
        with self.cv:
            return [j for j, _ in getattr(self, "_post_q", [])]

    # -- transfer ------------------------------------------------------
    def _fetch(self, j, url_path, dest, size_hint, record=None):
        """Resumable GET into dest (via dest.part), then checksum against the
        RomM file record. Updates j progress; honours self.rate_limit."""
        part = dest + ".part"
        have = os.path.getsize(part) if os.path.exists(part) else 0
        headers = {"Range": "bytes=%d-" % have} if have else {}
        try:
            r = self.api.s.get(self.api.base + url_path, headers=headers,
                               stream=True, timeout=(10, 60))
        except requests.exceptions.SSLError:
            raise _core.RommError("TLS certificate rejected") from None
        except Exception as e:
            raise NetError("Download interrupted (%s)." % e.__class__.__name__) from None
        if r.status_code == 416:      # .part already complete (verified below)
            _net.discard(r)
        elif r.status_code in (429, 500, 502, 503, 504):
            _net.discard(r)
            raise NetError("RomM busy (HTTP %d)." % r.status_code)
        elif r.status_code not in (200, 206):
            _net.discard(r)
            raise _core.RommError("Download failed: HTTP %d" % r.status_code)
        else:
            if r.status_code == 200:
                have = 0
            j.done_bytes += have
            t0, b0 = time.time(), j.done_bytes
            lim_t0, lim_b = time.time(), 0
            try:
                with open(part, "ab" if have else "wb") as f:
                    for chunk in r.iter_content(1 << 18):
                        if j.cancel.is_set():
                            r.close()
                            raise Cancelled()
                        f.write(chunk)
                        if record is not None and size_hint and f.tell() > size_hint:
                            # more than the exact file RomM described (a multi-file rom's zip,
                            # made on the fly, has no exact size): never let a server fill the disk
                            r.close()
                            f.close()
                            os.remove(part)
                            raise _core.RommError("The server sent more than the file's size; download stopped.")
                        if not j.force and not _config.in_download_hours(self.cfg):
                            r.close()
                            raise OutsideHours()
                        j.done_bytes += len(chunk)
                        dt = time.time() - t0
                        if dt >= 1.0:
                            j.speed = (j.done_bytes - b0) / dt
                            t0, b0 = time.time(), j.done_bytes
                        if self.rate_limit:
                            lim_b += len(chunk)
                            ahead = lim_b / self.rate_limit - (time.time() - lim_t0)
                            if ahead > 0:
                                time.sleep(min(ahead, 2.0))
                        else:
                            lim_t0, lim_b = time.time(), 0
            except (Cancelled, OutsideHours):
                raise
            except OSError as e:
                r.close()
                import errno
                if e.errno in (errno.ENOSPC, errno.EDQUOT):
                    raise _core.RommError("The disk is full.") from None      # not worth retrying
                raise NetError("Download interrupted (%s)." % e.__class__.__name__) from None
            except Exception as e:
                r.close()
                raise NetError("Download interrupted (%s)." % e.__class__.__name__) from None
        if size_hint and os.path.getsize(part) != size_hint:
            got = os.path.getsize(part)
            if got < size_hint and r.status_code != 416:
                raise NetError("Download stopped early (%s of %s)." % (_core.human_size(got), _core.human_size(size_hint)))
            os.remove(part)      # wrong size: don't resume from it
            raise ChecksumError("Size mismatch (%d != %d bytes)." % (got, size_hint))
        prev = j.state
        j.state = Job.VERIFYING
        ok, algo = file_hash_ok(part, record, name=dest)
        j.state = prev
        if ok is False:
            os.remove(part)
            raise ChecksumError(("Zip contents don't match RomM's" if algo == "zip" else
                                 "Checksum (%s) doesn't match RomM" % algo) + " — got a damaged copy.")
        j.verified = (j.verified is not False) and bool(ok)
        if j.cancel.is_set():
            if not j.pause:
                os.remove(part)            # cancelled while verifying: don't keep it
            raise Cancelled()
        os.replace(part, dest)

    def _free_bytes(self, path):
        st = os.statvfs(path)
        return st.f_bavail * st.f_frsize

    @staticmethod
    def _part_bytes(system, detail):
        """Bytes of this rom's partial download already on disk."""
        p = _core.rom_dest(detail, system)
        if not p:
            return 0
        if os.path.isfile(p + ".part"):
            return os.path.getsize(p + ".part")
        if os.path.isdir(p):
            return sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(p) for f in fs
                       if f.endswith(".part") or not f.startswith("."))
        return 0

    def _process(self, j):
        rom, system = j.rom, j.system
        sysdir = os.path.join(_core.ROMS_ROOT, system)
        os.makedirs(sysdir, exist_ok=True)
        try:
            detail = self.api.rom(rom["id"])
        except _core.RommError as e:
            if "Can't reach" in str(e):
                raise NetError(str(e) + ".") from None
            raise
        j.rom = rom = dict(rom, **detail)
        files = [f for f in (detail.get("files") or [])
                 if f.get("category") in (None, "game")]
        j.total_bytes = sum(f.get("file_size_bytes") or 0 for f in files) or \
            detail.get("fs_size_bytes") or 0
        j.done_bytes, j.verified = 0, None
        if needs_listing(rom, system):       # what the archive really unpacks to, when RomM didn't say
            try:
                zip_listing(self.api, rom, system)
            except NetError as e:
                _core.log("LIST %s: %s" % (j.name, e))      # the estimate stands; read again next time
        # extracted disc games are bigger than the zip; what's already
        # downloaded (.part) is on disk; and the keep-free reserve stays free
        need = max(0, space_needed(rom, system) - self._part_bytes(system, detail)) + \
            (_config.keep_free_bytes(self.cfg) or _core.SPACE_MARGIN)       # keep-free 0: still a small margin
        free = self._free_bytes(sysdir) - self.unpacking_bytes
        if free < need:
            raise _core.NoSpace(need - free)
        if j.short_space:
            _core.log("SPACE %s: enough space now" % j.name)
        j.short_space = 0

        j.state = Job.DOWNLOADING
        target = _core.rom_dest(detail, system)
        if not target:
            raise _core.RommError("RomM gave no usable file name for this game")
        # A RomM rom that is a *folder* (multi-file GDI/cue+bin set, or a folder
        # holding a single file) is pulled file by file, keeping its layout; the
        # whole-rom /content endpoint would hand back a zip or an extensionless
        # file named after the folder.
        is_folder = files and _library.rom_is_folder(detail, files)
        if is_folder:
            folder = target
            prefix = (detail.get("full_path") or "").rstrip("/") + "/"
            for f in files:
                rel = f.get("full_path", "")
                rel = rel[len(prefix):] if rel.startswith(prefix) else f["file_name"]
                parts = [_core.safe_name(p) for p in rel.split("/") if p not in ("", ".", "..")]
                dest = os.path.join(folder, *parts)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                if os.path.exists(dest) and os.path.getsize(dest) == f.get("file_size_bytes") \
                        and file_hash_ok(dest, f)[0] is not False:
                    j.done_bytes += f.get("file_size_bytes") or 0
                    continue
                self._fetch(j, "/api/roms/%d/files/content/%s" % (
                    f["id"], urllib.parse.quote(f["file_name"])), dest, f.get("file_size_bytes"), f)
            return {"kind": "folder", "path": folder, "heavy": False}
        else:
            dest = target
            rec = files[0] if len(files) == 1 else None
            if os.path.isfile(dest) and not os.path.exists(dest + ".part") and \
                    os.path.getsize(dest) == j.total_bytes and file_hash_ok(dest, rec, name=dest)[0] is True:
                j.done_bytes, j.verified = j.total_bytes, True   # already here, verified (e.g. paused while extracting)
            else:
                self._fetch(j, "/api/roms/%d/content/%s" % (
                    rom["id"], urllib.parse.quote(detail["fs_name"])), dest, j.total_bytes, rec)
            heavy = self._will_extract(j, dest)
            return {"kind": "file", "path": dest, "heavy": heavy,
                    "extra": max(0, space_needed(rom, system) - j.total_bytes) if heavy else 0}

    def _post(self, j, post):
        """After the download: extract / queue conversion, hand the game to ES, done."""
        rom, system = j.rom, j.system
        if post["kind"] == "folder":
            results = self._postprocess_folder(j, post["path"])
        else:
            if post.get("heavy"):
                j.state, j.message = Job.EXTRACTING, ""
                if j.cancel.is_set():
                    raise Cancelled()
            results = self._postprocess_file(j, post["path"])
        if j.replace:
            self._replace_old(j, results)
        entry = self._queue_gamelist(rom, system, results)
        self.library.mark(rom, system, results, group=j.group, meta=entry)
        j.chd_queued = bool(j.chd_task)
        j.state = Job.DONE
        j.message = "Ready to play" + (", checksum OK" if j.verified else "")
        _core.log("OK %s -> %s%s" % (j.name, results, " [verified]" if j.verified else ""))

    # -- multi-disc sets -----------------------------------------------
    def _finish_group(self, group, system):
        """When every disc of a set is on the box: write <base>.m3u (if the
        system plays playlists), hide the single discs in ES and give the
        playlist the game's metadata. Idempotent."""
        lib = self.library
        inst = lib.snapshot()
        recs = [inst.get(str(rid)) for rid in group["rom_ids"]]
        if not all(r and any(os.path.exists(p) for p in r.get("paths", [])) for r in recs):
            return None
        if not self._accepts(system, ".m3u"):
            _core.log("group %s: %s has no .m3u support; discs stay separate" % (group["base"], system))
            return None
        sysdir = os.path.join(_core.ROMS_ROOT, system)
        prims = [_library.primary_path(system, r["paths"], self.es) for r in recs]
        if not all(prims):
            return None
        m3u = os.path.join(sysdir, _core.safe_name(group["base"]) + ".m3u")
        lines = [os.path.relpath(p, sysdir) for p in prims]
        if os.path.exists(m3u):
            old = _es.m3u_lines(m3u)
            if old == lines:
                return m3u
            ours = any(m3u in r["paths"] for r in recs)     # we wrote it earlier
            if not ours and not all(os.path.normpath(os.path.join(sysdir, ln)) in
                       {os.path.normpath(p) for r in recs for p in r["paths"]} or
                       any(os.path.normpath(os.path.join(sysdir, ln)).startswith(os.path.normpath(p) + os.sep)
                           for r in recs for p in r["paths"]) for ln in old):
                _core.log("group %s: %s exists and isn't ours; not overwriting" % (group["base"], m3u))
                return None
        _core.atomic_write_text(m3u, "\n".join(lines) + "\n")
        ids = [str(rid) for rid in group["rom_ids"]]

        def add_m3u(inst):
            for rid in ids:
                r = inst.get(rid)
                if r and m3u not in r["paths"]:
                    r["paths"].append(m3u)
        lib.update(add_m3u)
        first = recs[0]
        meta = dict(first.get("meta") or {}, system=system, paths=[m3u],
                    name=group.get("title") or group["base"])
        _core.append_gamelist({"system": system, "hide": prims}, meta)
        _core.log("group %s: wrote %s (%d discs)" % (group["base"], m3u, len(prims)))
        return m3u

    def repair_groups(self):
        """Re-check every disc set on the box (e.g. a CHD conversion finished
        while the playlist was being written). Cheap; file checks only."""
        seen = {}
        for rec in self.library.snapshot().values():
            g = rec.get("group")
            if g and g.get("id") not in seen:
                seen[g["id"]] = (g, rec["system"])
        for g, system in seen.values():
            try:
                self._finish_group(g, system)
            except Exception as e:
                _core.log("group %s: %r" % (g.get("base"), e))
        return len(seen)

    def _replace_old(self, j, results):
        """The broken copy (another file name) goes now that RomM's is here:
        saves are copied to the new name, playlists pointed at it, then the
        old files are deleted (never the new ones)."""
        sysdir = os.path.join(_core.ROMS_ROOT, j.system)
        new = _library.primary_path(j.system, results, self.es) or results[0]
        new_set = {os.path.normpath(p) for r in results for p in ([r] + _es.game_files(r))}
        # a game folder the download (re)filled: whatever "old" file sits in it is
        # part of the new copy now (game_files doesn't look inside folders)
        new_dirs = [os.path.realpath(r) for r in results if os.path.isdir(r)]
        for old in j.replace or []:
            if not os.path.exists(old) or os.path.normpath(old) in new_set or not _core._inside(old, sysdir):
                continue
            if any(os.path.realpath(old).startswith(d + os.sep) for d in new_dirs):
                continue
            try:
                _library.migrate_saves(j.system, old, new)
                _library.fix_m3us(sysdir, {os.path.normpath(old): new})
            except Exception as e:
                _core.log("replace %s: %r" % (old, e))
            _es.delete_game(j.system, old, self.library)
            _core.log("REPLACED %s -> %s" % (old, new))
        j.replace = None

    # -- post-processing -------------------------------------------------
    def _will_extract(self, j, path):
        """True when post-processing will unpack this file (the slow part)."""
        ext = os.path.splitext(path)[1].lower()
        if ext not in _core.ARCHIVE_EXTS or j.system in _core.ZIP_NATIVE:
            return False
        try:
            members = self._list_archive(path)
        except Exception:
            return False
        has_disc = any(os.path.splitext(m)[1].lower() in _core.DISC_EXTS for m in members)
        return bool(members) and not (self._accepts(j.system, ext) and not has_disc)

    def _accepts(self, system, ext):
        """The system's emulator opens ext as it is (else a zip is unpacked)."""
        if ext.lower() in self.unopenable.get(system, ()):
            return False
        exts = self.es.get(system)
        return True if exts is None else ext.lower() in exts

    def _xbox_names(self, system, roots):
        """Make Xbox images bootable, part 1: "X.xiso" -> "X.iso" before anything
        filters by extension (ES lists .iso, not .xiso). Returns the roots."""
        if system != "xbox" or not _config.config_value(self.cfg, "xbox_bootable"):
            return roots
        out = []
        for r in roots:
            if os.path.isdir(r):
                for dp, _, fns in os.walk(r):
                    for fn in fns:
                        _discs.xiso_to_iso(os.path.join(dp, fn))
                out.append(r)
            else:
                out.append(_discs.xiso_to_iso(r))
        return out

    def _postprocess_file(self, j, path):
        j.chd_task = None
        path = self._xbox_names(j.system, [path])[0]
        ext = os.path.splitext(path)[1].lower()
        system = j.system
        if system == "ps3" and ext == ".zip" and _convert.conversion_for(system, path, self.cfg, self._accepts) \
                and _ps3.ps3_zip_is_disc(path):
            self._maybe_chd(j, system, [path], [path])   # read in place: no unzipped copy beside it
            return [path]
        if ext not in _core.ARCHIVE_EXTS or system in _core.ZIP_NATIVE:
            if ext in _core.DISC_EXTS:                   # a bare .iso/.cue/...: convert it like an extracted one
                self._maybe_chd(j, system, [path], [path])
            return [path]
        members = self._list_archive(path)
        if not members:
            raise _core.RommError("The archive is empty.")
        has_disc = any(os.path.splitext(m)[1].lower() in _core.DISC_EXTS for m in members)
        if self._accepts(system, ext) and not has_disc:
            return [path]          # cart rom in a zip the core reads directly
        j.state = Job.EXTRACTING
        stem = os.path.splitext(os.path.basename(path))[0]
        top = {m.split("/")[0] for m in members}
        # one top entry unpacks straight into the system folder: never ES's own
        # files or folders, nor "." (that would make the system folder the game)
        if len(top) == 1 and next(iter(top)).lower() in _leftovers.ARCHIVE_RESERVED_TOPS:
            raise _core.RommError("Unsafe archive layout: %s at the top" % next(iter(top)))
        # several loose files (cue + bins) -> own folder named after the game;
        # a single file or an archive that already has one top folder -> sysdir
        sysdir = os.path.dirname(path)
        if len(top) > 1:
            target = os.path.join(sysdir, stem)
        else:
            target = sysdir
        # where this game's files land must not already be another game's (or a
        # copy put there another way): unpacking would mix into it, and deleting
        # this game later would take that one with it
        lands = target if len(top) > 1 else os.path.join(sysdir, next(iter(top)))
        if os.path.lexists(lands) and not self._may_unpack_into(j, lands):
            raise _core.RommError("%s is already on the box under that name; not unpacking over it"
                            % os.path.basename(lands))
        os.makedirs(target, exist_ok=True)
        self._extract(path, target, j)
        os.remove(path)
        if len(top) > 1:
            out = [target]
        else:
            out = [os.path.join(sysdir, list(top)[0])]
        out = self._xbox_names(system, out)
        playable = self._playable_paths(system, out)
        self._maybe_chd(j, system, playable, out)
        return out

    def _may_unpack_into(self, j, dest):
        """An existing folder/file may receive this download only when it's this
        same RomM game's (re-download, repair), the copy Check games replaces,
        or an empty folder left by an interrupted attempt."""
        d = os.path.normpath(dest)
        if os.path.isdir(d) and not os.path.islink(d) and not os.listdir(d):
            return True
        for old in j.replace or []:
            o = os.path.normpath(old)
            if o == d or o.startswith(d + os.sep) or d.startswith(o + os.sep):
                return True
        for rid, rec in self.library.snapshot().items():
            for p in rec.get("paths", []):
                q = os.path.normpath(p)
                if q == d or q.startswith(d + os.sep) or d.startswith(q + os.sep):
                    return str(rid) == str(j.rom.get("id"))
        return False                             # there, but not ours: put there another way

    def _postprocess_folder(self, j, folder):
        j.chd_task = None
        self._xbox_names(j.system, [folder])
        self._maybe_chd(j, j.system, self._playable_paths(j.system, [folder]), [folder])
        return [folder]

    def _playable_paths(self, system, roots):
        found = []
        for root in roots:
            if os.path.isfile(root):
                found.append(root)
                continue
            for dp, _, fns in os.walk(root):
                for fn in fns:
                    if self._accepts(system, os.path.splitext(fn)[1]):
                        found.append(os.path.join(dp, fn))
        return found

    def _list_archive(self, path):
        if path.lower().endswith(".zip"):
            with zipfile.ZipFile(path) as z:
                return [i.filename for i in z.infolist() if not i.is_dir()]
        out = subprocess.run([self._7z(), "l", "-slt", "-ba", path],
                             capture_output=True, text=True).stdout
        return [l[7:] for l in out.splitlines() if l.startswith("Path = ")]

    def _7z(self):
        for name in ("7zz", "7z", "7zr"):
            p = shutil.which(name)
            if p:
                return p
        for p in ("/userdata/system/7zz", "/usr/bin/7zz", "/usr/bin/7zr"):
            if os.path.exists(p):
                return p
        return "7zr"

    def _extract(self, path, target, j):
        if path.lower().endswith(".zip"):
            with zipfile.ZipFile(path) as z:
                real = os.path.realpath(target)
                infos = z.infolist()
                for info in infos:                     # check every member before writing any
                    dest = os.path.realpath(os.path.join(target, info.filename))
                    if not dest.startswith(real + os.sep):
                        raise _core.RommError("Unsafe path in archive: %s" % info.filename)
                made = []
                try:
                    for info in infos:
                        if j.cancel.is_set():
                            raise Cancelled()
                        made.append(z.extract(info, target))
                except BaseException:
                    for m in reversed(made):          # no half-extracted game left behind
                        try:
                            os.rmdir(m) if os.path.isdir(m) else os.remove(m)
                        except OSError:
                            pass
                    raise
        else:
            for m in self._list_archive(path):        # same zip-slip rule for 7z/rar
                if m.startswith(("/", "\\")) or ".." in m.replace("\\", "/").split("/"):
                    raise _core.RommError("Unsafe path in archive: %s" % m)
            # 7z restores symlink entries, and would also write a member through a
            # link already on disk. So: unpack into a private folder beside the
            # target, refuse anything that isn't a plain file or folder, and move
            # each file into place only where its real destination stays inside
            # the target (no link along the way, planted earlier or now).
            stage = os.path.join(target, ".rb-extract-%d-%d" % (os.getpid(), threading.get_ident()))
            shutil.rmtree(stage, ignore_errors=True)
            os.makedirs(stage)
            try:
                rc = subprocess.run([self._7z(), "x", "-y", "-o" + stage, path],
                                    capture_output=True).returncode
                if rc != 0:
                    raise _core.RommError("7z extract failed (rc %d)" % rc)
                files = []
                for dp, dns, fns in os.walk(stage):
                    for n in dns + fns:
                        p = os.path.join(dp, n)
                        st = os.lstat(p)
                        if not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode)):
                            raise _core.RommError("Unsafe entry in archive (link or special file): %s"
                                            % os.path.relpath(p, stage))
                        if stat.S_ISREG(st.st_mode):
                            files.append(os.path.relpath(p, stage))
                real = os.path.realpath(target)
                for rel in files:                # check every destination before writing any
                    dest = os.path.join(target, rel)
                    parent = os.path.realpath(os.path.dirname(dest))    # resolves links that exist
                    if (parent != real and not parent.startswith(real + os.sep)) or os.path.islink(dest):
                        raise _core.RommError("Unsafe path in archive (through a link): %s" % rel)
                for rel in files:
                    dest = os.path.join(target, rel)
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    os.replace(os.path.join(stage, rel), dest)
            finally:
                shutil.rmtree(stage, ignore_errors=True)

    def _maybe_chd(self, j, system, playable, roots):
        if system == "xbox":
            for p in playable if _config.config_value(self.cfg, "xbox_bootable") else []:
                if p.lower().endswith(".iso"):
                    try:
                        cut = _discs.make_xiso(p)
                    except (_core.NoSpace, OSError) as e:
                        _core.log("xbox: %s left as a full disc image (%s); rb_chd_plan.py can fix it later"
                            % (os.path.basename(p), e))
                        continue
                    if cut:
                        _core.log("xbox: %s: kept the game partition (xemu can't boot a full disc image), "
                            "-%s" % (os.path.basename(p), _core.human_size(cut)))
        # one conversion per download: CD images before DVD images (PS2), else
        # the system's own converter (rommlib.CONVERTERS decides what applies)
        tasks = _convert.conversion_tasks(system, playable, self.cfg, self._accepts)
        if tasks:
            conv, srcs = tasks[0]
            self._queue_conversion(j, system, conv.mode, srcs, roots)

    def _queue_conversion(self, j, system, mode, srcs, roots):
        os.makedirs(_core.CHD_QUEUE_DIR, exist_ok=True)
        task = {"system": system, "mode": mode, "sources": srcs, "roots": roots,
                "queued": time.time()}
        name = "%d-%d-%s.json" % (int(time.time() * 1000), j.rom.get("id") or 0, re.sub(r"\W+", "_", system))
        _convert.publish_queue_task(task, name)
        j.chd_task = name
        j.chd_mode = mode                    # in memory only: names the format on the Downloads screen

    def _queue_gamelist(self, rom, system, paths):
        """Hand gamelist metadata to the launcher's post-exit merge step
        (ES must not be written to while it runs — see rb_finalize.py)."""
        entry = _library.gamelist_entry(rom, system, paths, cfg=self.cfg)
        os.makedirs(_core.DATA_DIR, exist_ok=True)
        _core.append_gamelist(entry)
        return entry
