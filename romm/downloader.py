"""Downloads: the queue, the workers, space planning."""
import itertools
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import threading
import time
import urllib.parse
import zipfile
import zlib

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


def _range_start(r):
    """Where a 206's body starts in the file ("bytes S-E/T"), or None."""
    m = re.match(r"\s*bytes\s+(\d+)-\d+/(\d+|\*)\s*$", r.headers.get("Content-Range") or "")
    return int(m.group(1)) if m else None


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


MARGIN_MIN = 32 << 20               # keep_free_gb 0: what a download still leaves free, at least
MARGIN_SHARE = 20                   # ... or 1/20 (5%) of what it writes, up to core.SPACE_MARGIN (2 GB)


def space_margin(need):
    """The small margin a download leaves free when the keep-free reserve is 0
    (Settings: "no reserve beyond a small margin"): 5% of the bytes it writes,
    at least 32 MB and at most 2 GB. A fixed 2 GB would make a 600 KB cart
    wait on a nearly full card."""
    return min(_core.SPACE_MARGIN, max(MARGIN_MIN, int(need or 0) // MARGIN_SHARE))


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

HISTORY_KEEP = 50                   # finished downloads Activity still lists after a restart


class Job:
    QUEUED, DOWNLOADING, VERIFYING, EXTRACTING, DONE, FAILED, CANCELLED, PAUSED = (
        "Queued", "Downloading", "Verifying", "Extracting", "Done", "Failed", "Cancelled", "Paused")
    FINISHED = (DONE, FAILED, CANCELLED)
    restored = False              # listed by an earlier session (Activity's history): never announced

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
        self.made = []                # finished files this job put where none was (cancel removes them)
        self.checked = []             # those of them that matched RomM's checksum when they came in
        self.moving = False           # its unpacked files are being moved into place (see MOVE_WAIT)
        self.proc = None              # its running 7z (killed by a pause or cancel: see _stop_unpacks)
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
        self.restored = False         # this session's now: its outcome is announced
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


class DamagedArchive(_core.RommError):
    """The downloaded archive can't be listed or unpacked (a bad CRC, a cut-off
    stream, 7z failing): _post deletes the job's own copy so Retry downloads it again."""


def _kill_group(p):
    """Kill a 7z started in its own process group (_run_7z) and whatever it started."""
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except OSError:                      # its group is gone already (or isn't its own)
        p.kill()


RETRY_DELAYS = (15, 60, 180)        # seconds before attempts 2, 3, 4
SPACE_RECHECK = 30                  # seconds between free-space checks for a waiting download
WORKER_POLL = 1.0                   # seconds a download/unpack worker sleeps before re-checking its queue

# Stopping an unpack (Pause all, closing the app, the background worker handing
# the queue back, an update) is safe at any moment but one: the archive is
# unpacked into a private folder (leftovers.unpack_stage) and a stopped or killed
# unpack only leaves that folder, which the next attempt (or Free up space)
# deletes; the archive stays and is unpacked again. The one step that can't be
# rolled back is moving the unpacked files into place (Job.moving, short: one
# rename per file), until the game is recorded. Nothing stops a job then, and
# whatever closes waits for it, up to MOVE_WAIT seconds; past that a kill is
# caught by the move marker (leftovers.write_move_marker) on the next attempt.
MOVE_WAIT = 30
UNZIP_CHUNK = 1 << 20               # bytes copied between two looks at a pause or cancel
# A 7z/rar unpack is a child process. Its own thread only looks at a pause every
# half second, and a process that exits first (the app closing, rb_dl handing
# back) takes that thread with it: 7z would carry on writing into a stage the
# next attempt sweeps as stale, beside a second 7z. So whatever stops a job kills
# its 7z itself and waits this long for it to go.
UNPACK_KILL_WAIT = 3.0
_PROC_LOCK = threading.Lock()       # a 7z is started, or looked up to be killed, under it


# RomM hashes an archive by what's inside it (a zip's crc/md5/sha1 are its
# member's), so these never match the file itself; zips are checked member by
# member instead, the rest by size only.
CONTENT_HASHED = (".zip", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz")

# A sign-in or proxy page (Authentik, Cloudflare, a reverse proxy's error page)
# answering instead of RomM: never written into a game file, never resumed onto.
WEB_PAGE = "RomM answered with a web page (a login or proxy page?)."
WEB_PAGE_HEADS = (b"<!doctype html", b"<html")
WEB_PAGE_SNIFF = 64                 # bytes looked at before the first write


def looks_like_web_page(head):
    """True when the first bytes of a body are an HTML page."""
    return head.lstrip(b"\xef\xbb\xbf \t\r\n").lower().startswith(WEB_PAGE_HEADS)


def _is_web_page_type(r):
    return (r.headers.get("Content-Type") or "").split(";")[0].strip().lower() in ("text/html", "application/xhtml+xml")


def archive_member_path(name):
    """A member name with either slash as a folder separator ("Sub\\Game.iso"
    from a Windows zipper is Sub/Game.iso), so no file is ever named with a
    backslash."""
    return (name or "").replace("\\", "/")


def archive_member_outside(name):
    """True when a member would land outside the folder it's unpacked into:
    an absolute path, a drive letter, or a ".." step."""
    n = archive_member_path(name)
    return n.startswith("/") or bool(re.match(r"[A-Za-z]:", n)) or ".." in n.split("/")


def outside_message(name):
    return "The archive has a file outside its folder (%s); left as it is" % archive_member_path(name)


def _member_key(n):
    return (n or "").replace("\\", "/").strip("/").lower()


def zip_member_finder(infos, wanted):
    """find(RomM's member name): the zip's entry for it, by its path (either
    slash, any case), else by its file name alone when only one entry in the
    zip and one of RomM's names (wanted) have it; None otherwise. (The same
    rule as audit's _member_finder.)"""
    by_path = {_member_key(i.filename): i for i in infos}
    bases, romm = {}, {}
    for i in infos:
        bases.setdefault(_member_key(i.filename).rsplit("/", 1)[-1], []).append(i)
    for n in wanted:
        b = _member_key(n).rsplit("/", 1)[-1]
        romm[b] = romm.get(b, 0) + 1

    def find(name):
        n = _member_key(name)
        if n in by_path:
            return by_path[n]
        b = n.rsplit("/", 1)[-1]
        hits = bases.get(b) or []
        return hits[0] if len(hits) == 1 and romm.get(b) == 1 else None
    return find


def zip_matches(path, members):
    """(True|False, "zip"): every RomM archive member is in the zip with the
    same size and CRC, and the data inside passes its CRCs (testzip)."""
    try:
        with zipfile.ZipFile(path) as z:
            infos = [i for i in z.infolist() if not i.is_dir()]
            find = zip_member_finder(infos, [m.get("name") or "" for m in members])
            for m in members:
                i = find(m.get("name") or "")
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
    except (zipfile.BadZipFile, OSError, EOFError, zlib.error):     # zlib.error: a damaged deflate stream
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
    except (zipfile.BadZipFile, OSError, EOFError, zlib.error):
        return False, "zip"
    got = h.hexdigest() if h else "%08x" % c
    return got == f[key].lower().zfill(len(got)), "zip"


def zip_crc_bad(path, cancel=None):
    """The first member of a zip whose data fails its own CRC (or that can't
    be read at all: ""), else None. One streaming read, a chunk
    at a time so cancel (an Event) stops it: Cancelled. Members Python can't
    decompress (Deflate64, encrypted) aren't held against it."""
    try:
        with zipfile.ZipFile(path) as z:
            for i in z.infolist():
                if i.is_dir():
                    continue
                try:
                    with z.open(i) as fh:
                        while fh.read(UNZIP_CHUNK):
                            if cancel is not None and cancel.is_set():
                                raise Cancelled()
                except (NotImplementedError, RuntimeError):
                    continue
                except (zipfile.BadZipFile, zlib.error, EOFError):
                    return archive_member_path(i.filename)
    except (zipfile.BadZipFile, EOFError, zlib.error):
        return ""
    return None


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
    HISTORY_KEYS = ("id", "name", "fs_name", "fs_name_no_ext", "fs_size_bytes", "platform_id", "platform_slug",
                    "path_cover_small", "path_cover_large", "has_multiple_files")    # enough to list it again

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
            stopped = []
            for j in self.jobs:
                if j.state in (Job.DOWNLOADING, Job.VERIFYING, Job.EXTRACTING):
                    j.pause, j.resume = True, False
                    j.cancel.set()
                    stopped.append(j)
        self._stop_unpacks(stopped)
        self.save_queue()

    @staticmethod
    def _stop_unpacks(jobs):
        """Kill the 7z of these jobs (their cancel is set already) and wait for it,
        UNPACK_KILL_WAIT at most: see _PROC_LOCK. A 7z started after this sees
        the cancel and never runs (_run_7z checks it under the same lock)."""
        with _PROC_LOCK:
            procs = [(j, j.proc) for j in jobs if getattr(j, "proc", None) is not None]
        for j, p in procs:
            try:
                if p.poll() is None:
                    _kill_group(p)
                p.wait(timeout=UNPACK_KILL_WAIT)
            except (OSError, subprocess.TimeoutExpired) as e:
                _core.log("UNPACK %s: 7z not stopped: %r" % (j.name, e))

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
        self._stop_unpacks([job])
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
        """Delete a cancelled/removed download's partial files, and the finished
        files it created (a folder game's members, an archive waiting to be
        unpacked): left behind, ES would list a broken game nothing can delete.
        Never a file that was there before the job (repair over an installed copy)."""
        sysdir = os.path.join(_core.ROMS_ROOT, job.system)
        made = [p for p in job.made if os.path.isfile(p) and not os.path.islink(p) and _core._inside(p, sysdir)]
        # an unpack of one of them killed while moving its files in left part of the
        # game where it lands: with its archive gone nothing would finish or list
        # it, and ES would list (and Play start) a broken game. Gone with it, unless
        # installed.json records it there (drop_unfinished_move says why that's safe),
        # or something there is newer than the unpack (put there by hand since: Free
        # up space offers it, and deletes it only when asked).
        for p in made:
            for marker in _leftovers.move_markers(os.path.dirname(p), p, job.rom.get("id")):
                _leftovers.drop_unfinished_move(marker, sysdir, keep=job.replace or (), if_untouched=True)
        for p in self._part_files(job) + made:
            try:
                os.remove(p)
            except OSError:
                pass
        base = _core.rom_dest(job.rom, job.system)
        if made and base and _core._inside(base, sysdir):
            base = os.path.normpath(base)
            for p in made:                     # the game's own folders it emptied (only empty ones go)
                d = os.path.dirname(os.path.normpath(p))
                while d == base or d.startswith(base + os.sep):
                    try:
                        os.rmdir(d)
                    except OSError:
                        break
                    d = os.path.dirname(d)
        job.made, job.checked = [], []

    def cancel_job(self, job):
        drop = False
        with self.cv:
            if job.state in (Job.QUEUED, Job.PAUSED):
                job.state, job.message = Job.CANCELLED, "Cancelled"
                drop = True
            elif job.active:
                job.pause = job.resume = False
                job.cancel.set()               # the worker drops the partial when it stops
        self._stop_unpacks([job])
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
            n -= len(self.jobs)
        if n:
            self.save_queue()              # the history on disk goes too
        return n

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
                     "group": j.group, "paused": bool(j.user_paused), "replace": j.replace or None,
                     "made": list(j.made) or None, "checked": list(getattr(j, "checked", None) or []) or None}
                    for j in list(self.jobs) if j.active or j.state == Job.PAUSED]
            # finished ones (Activity's list) survive a restart too: the last HISTORY_KEEP of them
            done = [{"rom": {k: j.rom.get(k) for k in self.HISTORY_KEYS if j.rom.get(k) is not None},
                     "system": j.system, "group": j.group, "state": j.state, "message": j.message,
                     "verified": j.verified, "chd_task": j.chd_task, "chd_mode": getattr(j, "chd_mode", None)}
                    for j in list(self.jobs) if j.state in Job.FINISHED][-HISTORY_KEEP:]
            os.makedirs(_core.DATA_DIR, exist_ok=True)
            _core.atomic_write_json(_core.QUEUE_PATH, {"held": self.hold, "jobs": pend, "history": done})

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

    @staticmethod
    def saved_history():
        try:
            with open(_core.QUEUE_PATH) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return []
        got = data.get("history") if isinstance(data, dict) else None
        return got if isinstance(got, list) else []

    def _restore_history(self):
        """The finished downloads a previous session listed, back in Activity
        (oldest first, before anything this session adds). Never announced
        again: restored is True."""
        if getattr(self, "_history_loaded", False):
            return
        self._history_loaded = True
        old = []
        for it in self.saved_history():
            rom = it.get("rom") if isinstance(it, dict) else None
            if not (isinstance(rom, dict) and rom.get("id") and it.get("system")
                    and it.get("state") in Job.FINISHED):
                continue
            j = Job(rom, it["system"], it.get("group"))
            j.state, j.message, j.verified = it["state"], it.get("message") or "", it.get("verified")
            j.chd_task, j.chd_mode, j.restored = it.get("chd_task"), it.get("chd_mode"), True
            j.done_bytes = j.total_bytes
            old.append(j)
        if old:
            with self.cv:
                self.jobs[:0] = old

    def resume(self):
        """Re-queue what a previous session (or rb_dl.py) left pending; a queue
        that was paused with "Pause all" stays paused. The finished ones it
        listed come back too, as history."""
        if self.saved_held():
            with self.cv:
                self.hold = True
        self._restore_history()
        n = 0
        for it in self.saved_queue():
            if it.get("rom", {}).get("id") and it.get("system"):
                j = self.enqueue(it["rom"], it["system"], it.get("group"), it.get("replace"), save=False)
                if it.get("made") and not j.made:         # what it had created before the restart
                    j.made = [p for p in it["made"] if isinstance(p, str)]
                    j.checked = [p for p in it.get("checked") or [] if isinstance(p, str)]
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
        with self.cv:                      # the lock _failed / resume_job decide pause and resume under
            stopped = [j for j in self.jobs if j.active]
            for j in stopped:
                j.pause, j.resume = True, False
                j.cancel.set()
            self.cv.notify_all()
        self._stop_unpacks(stopped)        # before it returns: closing the app must not leave a 7z running
        self.save_queue()

    def pending_count(self):
        return sum(1 for j in self.jobs if j.active or j.state == Job.PAUSED)

    def moving_jobs(self):
        """Jobs whose unpacked files are being moved into place right now."""
        return [j for j in list(self.jobs) if getattr(j, "moving", False)]

    def wait_moves(self, timeout=MOVE_WAIT, tick=None, sleep=None):
        """Wait while a game's files are being moved into place (the one part of
        an unpack that mustn't be cut short), at most timeout seconds; tick(jobs,
        waited) on each poll. Returns True when none is moving any more."""
        clock, sleep = time.monotonic, sleep or time.sleep
        t0 = clock()
        while True:
            busy = self.moving_jobs()
            waited = clock() - t0
            if not busy or waited >= timeout:
                if busy:
                    _core.log("MOVE %s still going after %d s" % (busy[0].name, waited))
                return not busy
            if tick:
                tick(busy, waited)
            sleep(0.1)

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
        page_ok = dest.lower().endswith((".htm", ".html"))   # the one game file that may be a web page
        if have and not page_ok:
            try:
                with open(part, "rb") as f:
                    head = f.read(WEB_PAGE_SNIFF)
            except OSError:
                head = b""
            if looks_like_web_page(head):
                os.remove(part)            # a sign-in page an older version kept: never resumed onto
                _core.log("DL %s: dropped a .part that is a web page" % j.name)
                have = 0
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
        elif _is_web_page_type(r) and not page_ok:
            _net.discard(r)                # a sign-in or proxy page: worth trying again, never kept
            raise NetError(WEB_PAGE)
        else:
            if r.status_code == 200:
                have = 0
            elif have and _range_start(r) != have:
                # a 206 that isn't the rest of the .part (a proxy answering from 0, or
                # another range): appending it would corrupt the file
                if _range_start(r) == 0:
                    have = 0                       # the whole file after all: write it over the .part
                else:
                    _net.discard(r)
                    os.remove(part)                # start over from 0 next time
                    raise NetError("The server resumed at the wrong place; starting over.")
            chunks, head = r.iter_content(1 << 18), b""
            if not have and not page_ok:
                # the start of the file: a page without the text/html type is
                # caught by its first bytes, before anything is written over the .part
                try:
                    for chunk in chunks:
                        head += chunk
                        if len(head) >= WEB_PAGE_SNIFF:
                            break
                except Exception as e:
                    r.close()
                    raise NetError("Download interrupted (%s)." % e.__class__.__name__) from None
                if looks_like_web_page(head):
                    r.close()
                    raise NetError(WEB_PAGE)
            j.done_bytes += have
            t0, b0 = time.time(), j.done_bytes
            lim_t0, lim_b = time.time(), 0
            try:
                with open(part, "ab" if have else "wb") as f:
                    for chunk in itertools.chain([head] if head else [], chunks):
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
        checked = [p for p in getattr(j, "checked", None) or [] if os.path.normpath(p) != os.path.normpath(dest)]
        j.checked = checked + [dest] if ok is True else checked       # a reuse of it says "checksum OK" too
        if j.cancel.is_set():
            if not j.pause:
                os.remove(part)            # cancelled while verifying: don't keep it
            raise Cancelled()
        os.replace(part, dest)

    def _fetch_new(self, j, url_path, dest, size_hint, record=None):
        """_fetch, noting dest on the job when nothing was there before (a file
        this job created goes again on cancel; one it replaced never does)."""
        existed = os.path.lexists(dest)
        self._fetch(j, url_path, dest, size_hint, record)
        if not existed and dest not in j.made:
            j.made.append(dest)

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

    @staticmethod
    def _kept_download(j, dest, rec, hash_it=False):
        """(reuse, verified) for a whole file already at dest (no .part beside it)
        of the size RomM gives, so it isn't downloaded again: when this job
        downloaded it itself (dest in j.made, which survives a restart in the saved
        queue), or (hash_it) when it passes RomM's checksum. The job's own passed
        _fetch's check before it took that name (verified when that was RomM's
        checksum: j.checked); a 7z/rar RomM can't vouch for again (CONTENT_HASHED),
        and a zip whose full re-test would read it all, are reused as they are. A
        stopped unpack (Pause all, closing, the background worker handing back, an
        update) leaves just such an archive. hash_it reads the whole file: never
        before the space check, which a job waiting for space repeats every
        SPACE_RECHECK."""
        try:
            whole = os.path.isfile(dest) and not os.path.exists(dest + ".part") and \
                os.path.getsize(dest) == j.total_bytes
        except OSError:
            return False, None
        if not whole:
            return False, None
        made = {os.path.normpath(p) for p in j.made if isinstance(p, str)}
        if j.total_bytes and os.path.normpath(dest) in made and not os.path.islink(dest):
            checked = {os.path.normpath(p) for p in getattr(j, "checked", None) or [] if isinstance(p, str)}
            return True, (True if os.path.normpath(dest) in checked else None)
        if hash_it and file_hash_ok(dest, rec, name=dest)[0] is True:
            return True, True
        return False, None

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
        is_folder = files and _library.rom_is_folder(detail, files)
        rec = files[0] if len(files) == 1 else None
        dest = _core.rom_dest(detail, system)
        # the cheap part only (no file read): this check repeats every SPACE_RECHECK while it waits
        kept, verified = self._kept_download(j, dest, rec) if dest and not is_folder else (False, None)
        # extracted disc games are bigger than the zip; what's already
        # downloaded (.part, or the whole file kept) is on disk; and the keep-free reserve stays free
        on_disk = self._part_bytes(system, detail) + (j.total_bytes if kept else 0)
        rest = max(0, space_needed(rom, system) - on_disk)
        need = rest + (_config.keep_free_bytes(self.cfg) or space_margin(rest))   # keep-free 0: a small margin
        free = self._free_bytes(sysdir) - self.unpacking_bytes
        if free < need:
            raise _core.NoSpace(need - free)
        if j.short_space:
            _core.log("SPACE %s: enough space now" % j.name)
        j.short_space = 0
        j.message = ""                       # "Waiting for space…", a retry's reason: over now

        j.state = Job.DOWNLOADING
        target = _core.rom_dest(detail, system)
        if not target:
            raise _core.RommError("RomM gave no usable file name for this game")
        self._never_over_theirs(j, target)
        ext = os.path.splitext(target)[1].lower()
        if not is_folder and ext in _core.ARCHIVE_EXTS and system not in _core.ZIP_NATIVE \
                and not self._accepts(system, ext):
            # where a disc archive's files would unpack (a folder named after it): taken by a game
            # the app didn't download, the unpack would be refused, so don't download it first
            lands = os.path.splitext(target)[0]
            if os.path.isdir(lands) and not os.path.islink(lands) and not self._ours(j, lands) \
                    and _library.holds_game(lands, system) and not self._may_unpack_into(j, lands):
                raise _core.RommError("%s is already on the box under that name and isn't this "
                                      "download's; not unpacking over it. Move or rename it, then "
                                      "try again" % os.path.basename(lands))
        # A RomM rom that is a *folder* (multi-file GDI/cue+bin set, or a folder
        # holding a single file) is pulled file by file, keeping its layout; the
        # whole-rom /content endpoint would hand back a zip or an extensionless
        # file named after the folder.
        if is_folder:
            folder = target
            prefix = (detail.get("full_path") or "").rstrip("/") + "/"
            j.wrote = []
            for f in files:
                rel = f.get("full_path", "")
                rel = rel[len(prefix):] if rel.startswith(prefix) else f["file_name"]
                parts = [_core.safe_name(p) for p in rel.split("/") if p not in ("", ".", "..")]
                dest = os.path.join(folder, *parts)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                if os.path.exists(dest) and os.path.getsize(dest) == f.get("file_size_bytes") \
                        and file_hash_ok(dest, f)[0] is not False:
                    j.done_bytes += f.get("file_size_bytes") or 0
                    j.wrote = list(getattr(j, "wrote", None) or []) + [dest]     # (in its own folder)
                    continue
                self._fetch_new(j, "/api/roms/%d/files/content/%s" % (
                    f["id"], urllib.parse.quote(f["file_name"])), dest, f.get("file_size_bytes"), f)
                j.wrote = list(getattr(j, "wrote", None) or []) + [dest]
            return {"kind": "folder", "path": folder, "heavy": False}
        else:
            dest = target
            if not kept:                     # one RomM's checksum vouches for (reads it all)
                kept, verified = self._kept_download(j, dest, rec, hash_it=True)
            if kept:
                j.done_bytes, j.verified = j.total_bytes, verified   # already here (e.g. paused while unpacking)
            else:
                self._fetch_new(j, "/api/roms/%d/content/%s" % (
                    rom["id"], urllib.parse.quote(detail["fs_name"])), dest, j.total_bytes, rec)
            heavy = self._will_extract(j, dest)
            return {"kind": "file", "path": dest, "heavy": heavy,
                    "extra": max(0, space_needed(rom, system) - j.total_bytes) if heavy else 0}

    def _post(self, j, post):
        """After the download: extract, hand the game to ES, record it, queue its
        conversion, done."""
        rom, system = j.rom, j.system
        j.after_warn, j.pending_chd, j.move_markers = None, None, []
        if post.get("kind") != "folder":
            j.wrote = []                         # (a folder game's were listed as it was fetched)
        j.defer_chd = True                       # its conversion is queued below, once it's recorded
        recorded = False
        # only the app's own copy is ever replaced (deleted once RomM's is in), whoever asked
        j.replace = [p for p in j.replace or [] if self._ours(j, p)] or None
        if j.replace is None:
            j.replace = self._recorded_copy(j) or None
        try:
            if post["kind"] == "folder":
                results = self._postprocess_folder(j, post["path"])
            else:
                if post.get("heavy"):
                    j.state, j.message = Job.EXTRACTING, ""
                    if j.cancel.is_set():
                        raise Cancelled()
                try:
                    results = self._postprocess_file(j, post["path"])
                except _core.RommError:
                    # damaged, or refused before any file moved in (in the way, an unsafe
                    # layout): the job's archive goes, never left in roms/ where ES can't use it
                    self._drop_damaged(j, post["path"])
                    raise
            # every file of the game is whole and in place: from here on no failure
            # (and no removing the job from Activity) may delete them
            j.made, j.checked = [], []
            self.save_queue()
            # the rest is bookkeeping: the game plays whether or not it all goes
            # through, so a failure is a warning on a finished job, not a failed one
            # (which would offer to download it all again)
            warn = j.after_warn
            if j.replace:
                warn = self._replace_old(j, results) or warn
            entry = None
            try:
                entry = self._queue_gamelist(rom, system, results)
            except OSError as e:
                _core.log("gamelist %s: %r" % (j.name, e))
                warn = warn or "its game info couldn't be handed to the game list"
            try:
                # what this download wrote: its unpacked or fetched files, and a file kept as it is
                wrote = [r for r in results if os.path.isfile(r)] + list(getattr(j, "wrote", None) or [])
                self.library.mark(rom, system, results, group=j.group, meta=entry,
                                  files=[p for p in dict.fromkeys(wrote) if os.path.lexists(p)])
                recorded = True
            except (_core.RommError, OSError) as e:
                _core.log("mark %s: %s" % (j.name, e))
                warn = warn or "installed.json can't be updated now"
        finally:
            j.moving = j.defer_chd = False
        if recorded:
            # its record now says the files are this game's: a later download may
            # unpack over them without the marker (and one kept while it isn't
            # recorded lets a download of it again do so)
            _leftovers.drop_move_markers(j.move_markers)
        # queued only once the game is recorded: the converter updates that record
        # when it's done (queued first, a quick conversion could finish before it)
        if j.pending_chd:
            try:
                self._maybe_chd(j, *j.pending_chd)
            except Exception as e:
                _core.log("convert %s: %r" % (j.name, e))
                warn = warn or "it couldn't be queued for compression"
        j.chd_queued = bool(j.chd_task)
        j.state = Job.DONE
        j.message = "Ready to play" + (", checksum OK" if j.verified else "") + (", but " + warn if warn else "")
        _core.log("OK %s -> %s%s%s" % (j.name, results, " [verified]" if j.verified else "",
                                       " WARNING: " + warn if warn else ""))

    def _drop_damaged(self, j, path):
        """The job's own download of an archive that won't unpack (damaged, or its
        unpack refused) goes (and out of j.made), so Retry downloads it again instead
        of reusing it on its size (_kept_download), and a failed job never leaves an
        archive in roms/. A file that was there before the job is never deleted:
        not being the job's, it's reused only when RomM's checksum vouches for it.
        Nor one the unpack already removed (its files are in)."""
        p = os.path.normpath(path)
        if p not in {os.path.normpath(x) for x in j.made if isinstance(x, str)}:
            return
        sysdir = os.path.join(_core.ROMS_ROOT, j.system)
        try:
            if os.path.isfile(p) and not os.path.islink(p) and _core._inside(p, sysdir):
                os.remove(p)
        except OSError as e:
            _core.log("UNPACK %s: damaged archive not removed: %r" % (j.name, e))
            return                           # still there: it's still the job's to delete on cancel
        j.made = [x for x in j.made if not (isinstance(x, str) and os.path.normpath(x) == p)]
        j.checked = [x for x in getattr(j, "checked", None) or [] if os.path.normpath(x) != p]
        _core.log("UNPACK %s: its archive removed (Retry downloads it again)" % j.name)
        self.save_queue()

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
            # done only when the playlist is right, every disc's record holds it (Compare
            # tells a disc's own files from the set's by that) and every disc is hidden
            # under it: Library.mark records a disc fetched again (under a new name too)
            # as not hidden yet; a record from before that was kept has no say, and was
            # hidden when its playlist was written
            if old == lines and all(m3u in r["paths"] and r.get("hidden_disc") is not False for r in recs):
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
        first = recs[0]
        meta = dict(first.get("meta") or {}, system=system, paths=[m3u],
                    name=group.get("title") or group["base"])
        # the hide is queued before the records say so: one that can't be queued
        # leaves them not hidden, and the next check (repair_groups) queues it again
        _core.append_gamelist({"system": system, "hide": prims}, meta)

        def add_m3u(inst):
            for rid in ids:
                r = inst.get(rid)
                if r and m3u not in r["paths"]:
                    r["paths"].append(m3u)
                if r and isinstance(r.get("files"), dict):
                    try:
                        r["files"][os.path.normpath(m3u)] = _library.file_sig(m3u)   # the app wrote it
                    except OSError:
                        pass
                if r:
                    r["hidden_disc"] = True             # its disc's hide is queued above
        lib.update(add_m3u)
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
        old files are deleted (never the new ones, nor a set's playlist or a
        file another disc of the set holds or the playlist names; a playlist
        goes alone, never the discs it names). Each old copy on its own:
        one that can't go (or whose removal installed.json can't record) doesn't
        stop the rest. Returns a warning for the job's message, else None."""
        sysdir = os.path.join(_core.ROMS_ROOT, j.system)
        new = _library.primary_path(j.system, results, self.es) or results[0]
        new_set = {os.path.normpath(p) for r in results for p in ([r] + _es.game_files(r))}
        # a game folder the download (re)filled: whatever "old" file sits in it is
        # part of the new copy now (game_files doesn't look inside folders)
        new_dirs = [os.path.realpath(r) for r in results if os.path.isdir(r)]
        keep = new_set | self._others_files(j)
        warn = None
        for old in j.replace or []:
            if not os.path.exists(old) or os.path.normpath(old) in new_set or not _core._inside(old, sysdir):
                continue
            if any(os.path.realpath(old).startswith(d + os.sep) for d in new_dirs):
                continue
            # a playlist alone, never what it names (the new disc and the set's other discs);
            # the set's own playlist stays: fix_m3us points it at the new disc
            playlist = old.lower().endswith(".m3u")
            files = [os.path.normpath(old)] if playlist else _es.game_files(old)
            own = [p for p in files if p not in keep]
            if not own:
                _core.log("replace %s: part of the set, kept" % old)
                continue
            try:
                _library.migrate_saves(j.system, old, new)
                _library.fix_m3us(sysdir, {os.path.normpath(old): new})
            except Exception as e:
                _core.log("replace %s: %r" % (old, e))
            try:
                if own == files and not playlist:
                    _es.delete_game(j.system, old, self.library)      # queues ES's removal before the library's
                else:                                     # a playlist, or a sheet naming another disc's tracks
                    self._delete_own(j.system, own)
            except (_core.RommError, OSError) as e:
                _core.log("replace %s: %s" % (old, e))
                warn = warn or ("the old copy is still there" if os.path.lexists(old)
                                else "installed.json can't be updated now")
                continue
            _core.log("REPLACED %s -> %s" % (old, new))
        j.replace = None
        return warn

    def _recorded_copy(self, j):
        """A game downloaded again (Check games, a disc of a set) replaces the copy
        its own record names, as the game page's Download again does: e.g. the CHD
        made here from its cue/bin, which the new download would only sit beside.
        Only paths no other game's record holds, inside roms/<system>."""
        try:
            recs = self.library.snapshot()
        except (_core.RommError, OSError):
            return []
        me = str((j.rom or {}).get("id"))
        sysdir = os.path.join(_core.ROMS_ROOT, j.system)
        rec = recs.get(me)
        mine = (rec.get("paths") or []) if isinstance(rec, dict) else []
        others = {os.path.normpath(p) for rid, rec in recs.items() if rid != me and isinstance(rec, dict)
                  for p in rec.get("paths") or []}
        return [p for p in mine if isinstance(p, str) and os.path.normpath(p) not in others
                and not p.lower().endswith(".m3u")                 # a set's playlist stays (fix_m3us)
                and os.path.lexists(p) and _core._inside(p, sysdir)]

    def _others_files(self, j):
        """Files replacing a disc of a set must never delete: those another disc's record holds
        and the discs the set's playlist names (but the old copy). A playlist itself isn't
        followed (it names this disc too). Normalised paths; empty for a lone game."""
        out = set()
        me, group = str((getattr(j, "rom", None) or {}).get("id")), getattr(j, "group", None)
        if not group:
            return out
        for rid, rec in self.library.snapshot().items():
            if rid != me and isinstance(rec, dict) and rec.get("system") == j.system and \
                    (rec.get("group") or {}).get("id") == group.get("id"):
                out.update(os.path.normpath(p) for p in rec.get("paths") or [])
        if group.get("base"):
            m3u = os.path.join(_core.ROMS_ROOT, j.system, _core.safe_name(group["base"]) + ".m3u")
            if os.path.isfile(m3u):
                out.add(os.path.normpath(m3u))
                mine = {os.path.normpath(p) for p in j.replace or []}
                out.update(p for p in (os.path.normpath(q) for q in _es.m3u_paths(m3u)) if p not in mine)
        return out

    def _delete_own(self, system, files):
        """delete_game for a list of files as it is (no playlist or sheet followed): only the
        old copy's own files go when some of what it names is another game's or the new one's."""
        sysdir = os.path.join(_core.ROMS_ROOT, system)
        files = [p for p in files if _core._inside(p, sysdir)]
        gone = []
        try:
            for p in files:                  # one that won't go stops the rest; what went is still told
                if os.path.isdir(p) and not os.path.islink(p):
                    shutil.rmtree(p)
                elif os.path.lexists(p):
                    os.remove(p)
                gone.append(p)
        finally:
            _core.log("DELETE %s: %s%s" % (system, gone, "" if len(gone) == len(files) else
                                           " (not all: %s left)" % files[len(gone):]))
            if gone:
                done = {os.path.normpath(p) for p in gone}

                def drop(inst):
                    for rid, rec in list(inst.items()):
                        paths = [os.path.normpath(x) for x in rec.get("paths", [])]
                        if paths and all(x in done or not os.path.exists(x) for x in paths):
                            inst.pop(rid, None)
                try:
                    _core.append_gamelist({"system": system, "remove": gone})     # queued first, as delete_game does
                finally:
                    try:
                        self.library.update(drop)
                    finally:
                        self.library.rescan(system)

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

    def _zip_kept_ok(self, j, path):
        """A zip the emulator reads as it is (arcade, a cart zip) is never
        unpacked, which would check its CRCs: when no RomM checksum vouched for
        it (j.verified), it's read through once, so a damaged copy isn't "Ready to
        play". DamagedArchive: _post drops the job's copy, Retry downloads it again."""
        if j.verified:
            return
        prev = j.state
        j.state = Job.VERIFYING
        try:
            bad = zip_crc_bad(path, j.cancel)
        finally:
            j.state = prev
        if bad is not None:
            _core.log("VERIFY %s: %s fails its CRC" % (j.name, bad))
            raise DamagedArchive("the download is damaged (%s)" % (
                "%s fails its check" % bad if bad else "it isn't a zip that can be read"))

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

    def _convert(self, j, system, playable, roots):
        """Queue the game's conversion now, or (j.defer_chd: in _post) once it's recorded."""
        if getattr(j, "defer_chd", False):
            j.pending_chd = (system, playable, roots)
        else:
            self._maybe_chd(j, system, playable, roots)

    def _postprocess_file(self, j, path):
        """The game's files from a downloaded file (unpacked when the system needs
        it), and its conversion queued (from _post: left in j.pending_chd).
        Once the files are in place nothing fails the job: what goes wrong after
        is a warning in j.after_warn."""
        j.chd_task = None
        j.after_warn = None
        path = self._xbox_names(j.system, [path])[0]
        ext = os.path.splitext(path)[1].lower()
        system = j.system
        if system == "ps3" and ext == ".zip" and _convert.conversion_for(system, path, self.cfg, self._accepts) \
                and _ps3.ps3_zip_is_disc(path):
            self._convert(j, system, [path], [path])   # read in place: no unzipped copy beside it
            return [path]
        if ext not in _core.ARCHIVE_EXTS or system in _core.ZIP_NATIVE:
            if ext in _core.DISC_EXTS:                   # a bare .iso/.cue/...: convert it like an extracted one
                self._convert(j, system, [path], [path])
            elif ext == ".zip":
                self._zip_kept_ok(j, path)               # arcade: the zip is the game
            return [path]
        try:
            members = self._list_archive(path)
        except (zipfile.BadZipFile, zlib.error, EOFError) as e:
            raise DamagedArchive("the archive is damaged: %s" % str(e).rstrip(".").lower()) from None
        if not members:                       # 7z lists nothing in one it can't read either
            raise DamagedArchive("The archive is empty.")
        has_disc = any(os.path.splitext(m)[1].lower() in _core.DISC_EXTS for m in members)
        if self._accepts(system, ext) and not has_disc:
            if ext == ".zip":
                self._zip_kept_ok(j, path)
            return [path]          # cart rom in a zip the core reads directly
        bad = next((m for m in members if archive_member_outside(m)), None)
        if bad is not None:
            raise _core.RommError(outside_message(bad))
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
        _leftovers.sweep_stages(target)        # a killed unpack's folder isn't "already on the box"
        lands = target if len(top) > 1 else os.path.join(sysdir, next(iter(top)))
        # an earlier attempt at this same archive for this same game that was cut
        # off while moving its files in (or before it was recorded): what's there
        # is its own half-made copy, unpacked over again now
        before = _leftovers.move_markers(sysdir, path, j.rom.get("id"))
        resumed = os.path.normpath(lands) in before.values()
        if resumed:
            _core.log("UNPACK %s: finishing an earlier unpack into %s that was cut off" % (j.name, lands))
        # a folder of that name with no game in it (the owner's manual or notes, kept when
        # the game was compressed to one file beside it) takes the game's files beside
        # them: none of them is ever written over, nor deleted with an unpack cut short
        owner = resumed and any((_leftovers.read_move_marker(m) or {}).get("keep") for m in before
                                if os.path.normpath(before[m]) == os.path.normpath(lands))
        new_owner = False
        if os.path.lexists(lands) and not resumed and not self._may_unpack_into(j, lands):
            if not (os.path.isdir(lands) and not os.path.islink(lands) and not _library.holds_game(lands, system)):
                raise _core.RommError("%s is already on the box under that name and isn't this "
                                      "download's; not unpacking over it. Move or rename it, then "
                                      "try again" % os.path.basename(lands))
            owner = new_owner = True
            _core.log("UNPACK %s: %s holds no game, only other files: unpacking beside them" % (j.name, lands))
        if not resumed and not new_owner and os.path.isdir(lands) and not os.path.islink(lands):
            other = self._other_game_in(j, lands, target, members)
            if other:
                raise _core.RommError("%s holds another game (%s), not this download's; not unpacking "
                                      "in among it. Move or rename the folder, then try again"
                                      % (os.path.basename(lands), other))
        j.move_markers = list(before)
        os.makedirs(target, exist_ok=True)
        self._extract(path, target, j, lands=lands, keep_existing=new_owner, keep_lands=bool(owner))
        # the files are in place: whatever goes wrong now leaves a game that plays
        out = [target] if len(top) > 1 else [os.path.join(sysdir, list(top)[0])]
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as e:
            _core.log("UNPACK %s: archive not removed: %r" % (j.name, e))
            j.after_warn = j.after_warn or "its archive couldn't be deleted"
        try:
            out = self._xbox_names(system, out)
        except OSError as e:
            _core.log("xbox %s: %r" % (j.name, e))
            j.after_warn = j.after_warn or "it couldn't be renamed for xemu"
        playable = self._playable_paths(system, out)
        try:
            self._convert(j, system, playable, out)
        except Exception as e:                   # not deferred (Unpack from Games ES can't see)
            _core.log("convert %s: %r" % (j.name, e))
            j.after_warn = j.after_warn or "it couldn't be queued for compression"
        if not getattr(j, "defer_chd", False):   # no _post to record it: the caller does
            j.moving = False
            _leftovers.drop_move_markers(j.move_markers)
        return out

    def _ours(self, j, path):
        """path is this job's own (made by it) or the app's download of this game (its record,
        or inside one of its recorded folders)."""
        p = os.path.normpath(path)
        mine = {os.path.normpath(x) for x in j.made if isinstance(x, str)}
        rec = self.library.snapshot().get(str((j.rom or {}).get("id"))) or {}
        mine |= {os.path.normpath(x) for x in rec.get("paths") or []}
        return any(p == m or p.startswith(m + os.sep) for m in mine)

    def _never_over_theirs(self, j, target):
        """The download's file or folder name is taken by something the app didn't download
        for this game (the owner's own zip of it, another game, a copy put there by hand):
        refused, never written over, reused or deleted after unpacking."""
        if not os.path.lexists(target) or self._ours(j, target):
            return
        if os.path.isdir(target) and not os.path.islink(target) and not _library.holds_game(target, j.system):
            return            # empty, or only the owner's notes (kept when the game was compressed): files go beside
        raise _core.RommError("%s is already on the box and wasn't downloaded by RomM-o-Cera; not writing over "
                              "it. Move or rename it, then try again" % os.path.basename(os.path.normpath(target)))

    def _other_game_in(self, j, folder, target, members):
        """A game file in folder (one the app didn't download) that this download
        neither brings nor replaces: another game, which unpacking would mix this
        one in with (and Download again "replace" by unpacking beside). Its name
        relative to folder, else None."""
        d = os.path.normpath(folder)
        for rec in self.library.snapshot().values():
            if any(os.path.normpath(p) == d or os.path.normpath(p).startswith(d + os.sep)
                   for p in rec.get("paths") or []):
                return None                      # the app's own download: its files
        brings = {os.path.normpath(os.path.join(target, archive_member_path(m))).lower() for m in members}
        replaced = {os.path.normpath(p).lower() for p in j.replace or []}
        for dp, dns, fns in os.walk(d):
            dns[:] = [n for n in dns if n.lower() not in _core.MEDIA_DIRS]
            for n in sorted(fns):
                q = os.path.normpath(os.path.join(dp, n))
                if q.lower() not in brings and q.lower() not in replaced and _library.is_game_name(q, j.system):
                    return os.path.relpath(q, d)
        return None

    def _may_unpack_into(self, j, dest):
        """An existing folder/file may receive this download only when it's this
        same RomM game's (re-download, repair), the copy Check games replaces,
        or an empty folder left by an interrupted attempt."""
        d = os.path.normpath(dest)
        if os.path.isdir(d) and not os.path.islink(d) and not os.listdir(d):
            return True
        for old in j.replace or []:
            o = os.path.normpath(old)
            # (only a copy that's there: a missing file Check games named in a folder holding
            # another game never lets this one's files in among that one's)
            if os.path.lexists(o) and (o == d or o.startswith(d + os.sep) or d.startswith(o + os.sep)):
                return True
        for rid, rec in self.library.snapshot().items():
            for p in rec.get("paths", []):
                q = os.path.normpath(p)
                if q == d or q.startswith(d + os.sep) or d.startswith(q + os.sep):
                    return str(rid) == str(j.rom.get("id"))
        return False                             # there, but not ours: put there another way

    def _postprocess_folder(self, j, folder):
        j.chd_task = None
        j.after_warn = None
        self._xbox_names(j.system, [folder])
        self._convert(j, j.system, self._playable_paths(j.system, [folder]), [folder])
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
        """The archive's file members, either slash a folder separator
        (archive_member_path)."""
        if path.lower().endswith(".zip"):
            with zipfile.ZipFile(path) as z:
                names = [archive_member_path(i.filename) for i in z.infolist() if not i.is_dir()]
        else:
            out = subprocess.run([self._7z(), "l", "-slt", "-ba", path],
                                 capture_output=True, text=True).stdout
            names = [archive_member_path(l[7:]) for l in out.splitlines() if l.startswith("Path = ")]
        return [n for n in names if not n.endswith("/")]     # a Windows zipper's "Sub\" folder entry

    def _7z(self):
        for name in ("7zz", "7z", "7zr"):
            p = shutil.which(name)
            if p:
                return p
        for p in ("/userdata/system/7zz", "/usr/bin/7zz", "/usr/bin/7zr"):
            if os.path.exists(p):
                return p
        return "7zr"

    def _extract(self, path, target, j, lands=None, keep_existing=False, keep_lands=False):
        """Unpack the archive into target: into a private folder there first (a
        pause, cancel or kill until then leaves only that folder, swept later),
        then each file moved into place, which nothing interrupts. lands: where
        the game lands, for the move marker (see leftovers.write_move_marker).
        keep_existing: lands holds the owner's files, so no file there is written
        over (refused before any is moved); keep_lands: nor is lands ever deleted
        as an unfinished unpack."""
        _leftovers.sweep_stages(target)        # what an unpack killed half-way here left
        if path.lower().endswith(".zip"):
            try:
                with zipfile.ZipFile(path) as z:
                    real = os.path.realpath(target)
                    infos = z.infolist()
                    for info in infos:                     # check every member before writing any
                        dest = os.path.realpath(os.path.join(target, archive_member_path(info.filename)))
                        if archive_member_outside(info.filename) or not dest.startswith(real + os.sep):
                            raise _core.RommError("Unsafe path in archive: %s" % info.filename)
                    # unpack into a private folder beside the target and move into place only
                    # once all of it is out: a cancel or a full disk halfway leaves no
                    # half-extracted game, and a copy it replaces (repair) stays whole
                    stage = _leftovers.unpack_stage(target)
                    shutil.rmtree(stage, ignore_errors=True)
                    os.makedirs(stage)
                    try:
                        got = [self._unzip_member(z, info, stage, j) for info in infos]
                        moves = []
                        for p in got:                     # check every destination before replacing any
                            dest = os.path.join(target, os.path.relpath(p, stage))
                            if os.path.isdir(dest) != os.path.isdir(p) and os.path.lexists(dest):
                                raise _core.RommError("%s is in the way of the archive's %s"
                                                      % (os.path.relpath(dest, target), "folder" if os.path.isdir(p) else "file"))
                            if keep_existing and not os.path.isdir(p) and os.path.lexists(dest):
                                raise _core.RommError(self._not_over(dest, target))
                            moves.append((p, dest))
                        self._move_into_place(j, moves, path, lands, keep=keep_lands)
                    finally:
                        shutil.rmtree(stage, ignore_errors=True)
            except (zipfile.BadZipFile, zlib.error, EOFError) as e:     # a bad CRC, a cut-off stream
                raise DamagedArchive("the archive is damaged: %s" % str(e).rstrip(".").lower()) from None
        else:
            for m in self._list_archive(path):        # same zip-slip rule for 7z/rar
                if archive_member_outside(m):     # _postprocess_file names it first (outside_message)
                    raise _core.RommError("Unsafe path in archive: %s" % m)
            # 7z restores symlink entries, and would also write a member through a
            # link already on disk. So: unpack into a private folder beside the
            # target, refuse anything that isn't a plain file or folder, and move
            # each file into place only where its real destination stays inside
            # the target (no link along the way, planted earlier or now).
            stage = _leftovers.unpack_stage(target)
            shutil.rmtree(stage, ignore_errors=True)
            os.makedirs(stage)
            try:
                self._run_7z(path, stage, j)
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
                # a member 7z wrote with a backslash in its name goes where the zip
                # path says (Sub\Game.iso: Sub/Game.iso), as from a zip unpacked here
                moves = [(os.path.join(stage, rel), os.path.join(target, archive_member_path(rel).replace("/", os.sep)))
                         for rel in files]
                for src, dest in moves:          # check every destination before writing any
                    rel = os.path.relpath(src, stage)
                    parent = os.path.realpath(os.path.dirname(dest))    # resolves links that exist
                    if (parent != real and not parent.startswith(real + os.sep)) or os.path.islink(dest):
                        raise _core.RommError("Unsafe path in archive (through a link): %s" % rel)
                    if keep_existing and os.path.lexists(dest):
                        raise _core.RommError(self._not_over(dest, target))
                self._move_into_place(j, moves, path, lands, keep=keep_lands)
            finally:
                shutil.rmtree(stage, ignore_errors=True)

    @staticmethod
    def _not_over(dest, target):
        return "%s is already in %s and isn't this game's; not unpacking over it" % (
            os.path.relpath(dest, target), os.path.basename(os.path.normpath(target)))

    @staticmethod
    def _unzip_member(z, info, stage, j):
        """One member into the stage, as ZipFile.extract names it, a chunk at a
        time so a pause or cancel stops it within a second (a disc image is one
        member: minutes). Returns the path written."""
        name = archive_member_path(info.filename)        # a Windows zipper's Sub\Game.iso: Sub/Game.iso
        arc = os.path.splitdrive(name.replace("/", os.sep))[1]
        arc = os.sep.join(x for x in arc.split(os.sep) if x not in ("", os.curdir, os.pardir))
        dest = os.path.normpath(os.path.join(stage, arc))
        if j.cancel.is_set():
            raise Cancelled()
        if info.is_dir() or name.endswith("/"):
            os.makedirs(dest, exist_ok=True)
            return dest
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with z.open(info) as src, open(dest, "wb") as out:
            while True:
                buf = src.read(UNZIP_CHUNK)
                if not buf:
                    break
                out.write(buf)
                if j.cancel.is_set():
                    raise Cancelled()
        return dest

    def _run_7z(self, path, stage, j):
        """7z x into the stage; a pause or cancel stops it (the stage is swept).
        The process is kept on the job (j.proc) so whatever stops the job kills it
        too (_stop_unpacks): this thread may not outlive the process."""
        with _PROC_LOCK:
            if j.cancel.is_set():
                raise Cancelled()
            # its own process group: a 7z that is a wrapper script (or starts
            # helpers) goes whole when it's stopped (_kill_group)
            p = subprocess.Popen([self._7z(), "x", "-y", "-o" + stage, path], start_new_session=True,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            j.proc = p
        try:
            while True:
                try:
                    rc = p.wait(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    if j.cancel.is_set():
                        raise Cancelled() from None
        finally:
            if p.poll() is None:
                _kill_group(p)
                p.wait()
            with _PROC_LOCK:
                j.proc = None
        if j.cancel.is_set():
            raise Cancelled()              # killed by _stop_unpacks: a pause, not a failed unpack
        if rc != 0:
            raise DamagedArchive("7z extract failed (rc %d)" % rc)

    def _move_into_place(self, j, moves, archive, lands, keep=False):
        """Move the staged files (src, dest) into place. The last point a pause or
        cancel stops the unpack is before the first one: from there the job is
        "moving" (whatever closes waits for it, see MOVE_WAIT) until _post has
        recorded the game, and a marker beside where it lands lets an attempt
        killed half-way be finished by the next one."""
        if j.cancel.is_set():
            raise Cancelled()
        j.moving = True
        try:
            marker = None
            if lands is not None:
                keep = keep or _leftovers.path_overlaps(lands, [os.path.normpath(p) for p in getattr(j, "replace", None) or []])
                marker = _leftovers.write_move_marker(os.path.dirname(os.path.normpath(lands)), lands, archive,
                                                      j.rom.get("id"), keep=keep)
                j.move_markers = list(getattr(j, "move_markers", None) or []) + [marker]
            for src, dest in moves:
                if os.path.isdir(src):
                    os.makedirs(dest, exist_ok=True)
                else:
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    os.replace(src, dest)
                    j.wrote = list(getattr(j, "wrote", None) or []) + [dest]
            if marker is not None:
                _leftovers.mark_moved(marker)     # whole now: never offered as an unfinished unpack
        finally:
            if lands is None:
                j.moving = False                 # nothing records it after: the caller's own unpack
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
