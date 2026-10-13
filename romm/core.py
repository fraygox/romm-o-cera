"""Paths, platform constants, errors and the small helpers every part uses."""
import contextlib
import io
import json
import os
import subprocess
import sys
import threading
import time

import rommlib


DATA_DIR = os.environ.get("ROMM_BROWSER_DATA", "/userdata/system/romm-browser")
ROMS_ROOT = os.environ.get("ROMM_BROWSER_ROMS", "/userdata/roms")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
STATE_PATH = os.path.join(DATA_DIR, "installed.json")
GAMELIST_QUEUE = os.path.join(DATA_DIR, "gamelist-pending.jsonl")
CHD_QUEUE_DIR = os.path.join(DATA_DIR, "chd-queue")
COVER_CACHE = os.path.join(DATA_DIR, "cache", "covers")
LOG_PATH = os.path.join(DATA_DIR, "romm-browser.log")
QUEUE_PATH = os.path.join(DATA_DIR, "download-queue.json")   # survives exit / reboot
DL_LOCK = os.path.join(DATA_DIR, "downloads.lock")          # held by rb_dl.py
DL_STOP = os.path.join(DATA_DIR, "downloads.stop")          # app asks rb_dl.py to hand over
DL_STATUS = os.path.join(DATA_DIR, "downloads-status.json")


# data-dir files the worker and the app share, resolved at call time (DATA_DIR can be re-pointed)
def tmp_dir():
    return os.path.join(DATA_DIR, "tmp")              # scratch: find_leftovers offers what's left


def chd_lock_path():
    return os.path.join(DATA_DIR, "chd.lock")         # held by rb_chd.py while it works


def chd_current_path():
    return os.path.join(DATA_DIR, "chd-current.json")  # what the worker is converting now


def chd_results_path():
    return os.path.join(DATA_DIR, "chd-results.jsonl")  # finished tasks, for the Conversions screen
USERDATA = os.environ.get("ROMM_BROWSER_USERDATA") or os.path.dirname(ROMS_ROOT.rstrip("/"))
BIOS_DB = os.environ.get("ROMM_BROWSER_BIOS_DB", "/usr/bin/batocera-systems")
SAVES_ROOT = os.environ.get("ROMM_BROWSER_SAVES", "/userdata/saves")
UA = "romm-o-cera/" + rommlib.VERSION

# RomM platform fs_slug -> Batocera roms/<dir>. Anything not listed (or mapped
# to None) is hidden: there is no Batocera system that plays it.
PLATFORM_MAP = {
    "3do": "3do", "3ds": "3ds", "new-nintendo-3ds": "3ds", "64dd": "n64dd",
    "amiga": "amiga500", "amiga-cd32": "amigacd32", "commodore-cdtv": "amigacdtv",
    "arcade": "fbneo",
    "atari2600": "atari2600", "atari5200": "atari5200", "atari7800": "atari7800",
    "atari-jaguar-cd": "jaguarcd", "jaguar": "jaguar", "atari-st": "atarist",
    "lynx": "lynx", "c64": "c64", "colecovision": "colecovision",
    "dreamcast": "dreamcast", "fairchild-channel-f": "channelf", "fds": "fds",
    "fm-towns": "fmtowns", "gamecube": "gamecube", "game-dot-com": "gamecom",
    "gamegear": "gamegear", "g-and-w": "gameandwatch", "gb": "gb", "gba": "gba",
    "gbc": "gbc", "genesis": "megadrive", "intellivision": "intellivision",
    "mastersystem": "mastersystem", "mega-duck-slash-cougar-boy": "megaduck",
    "msx": "msx1", "msx2": "msx2", "n64": "n64", "nds": "nds",
    "neo-geo-cd": "neogeocd", "neo-geo-pocket": "ngp", "neo-geo-pocket-color": "ngpc",
    "nes": "nes", "odyssey-2": "odyssey2", "pc-9800-series": "pc98", "pc-fx": "pcfx",
    "philips-cd-i": "cdi", "pokemon-mini": "pokemini", "ps1": "psx", "ps2": "ps2",
    "ps3": "ps3", "psp": "psp", "psvita": "psvita", "satellaview": "satellaview",
    "saturn": "saturn", "sega32": "sega32x", "segacd": "megacd", "sega-pico": "pico",
    "sg1000": "sg1000", "sharp-x68000": "x68000", "snes": "snes",
    "sufami-turbo": "sufami", "supergrafx": "supergrafx", "supervision": "supervision",
    "turbografx16": "pcengine", "turbografx-cd": "pcenginecd", "vectrex": "vectrex",
    "virtualboy": "virtualboy", "vsmile": "vsmile", "wii": "wii", "wiiu": "wiiu",
    "wonderswan": "wswan", "wonderswan-color": "wswanc", "xbox": "xbox",
    "xbox360": "xbox360", "zxs": "zxspectrum",
}

# Disc images: an archive holding any of these is always extracted, even when
# the system nominally accepts .zip (saturn/pcfx/fmtowns do, but their cores
# want the raw cue/iso).
DISC_EXTS = {".cue", ".gdi", ".iso", ".chd", ".ccd", ".cdi", ".rvz", ".wbfs",
             ".gcm", ".gcz", ".ciso", ".cso", ".pbp", ".m3u", ".toc", ".img",
             ".mdf", ".nrg", ".wua", ".wud", ".wux", ".3ds", ".cia", ".squashfs"}
ARCHIVE_EXTS = {".zip", ".7z", ".rar"}
# Folders in a system folder that hold ES media, never games: scans (audit,
# leftovers, the planner, Space) skip them, and an archive may not unpack onto them.
MEDIA_DIRS = frozenset({"images", "videos", "manuals", "media", "downloaded_images", "downloaded_videos"})
# The subset gamelist media goes into (RomM-o-Cera's own and ES's): the only
# folders rb_finalize deletes an uninstalled game's media from, and all a
# system folder may hold besides gamelist.xml/_info.txt to count as "no games here".
GAMELIST_MEDIA_DIRS = ("images", "videos", "manuals")
# Systems where the zip IS the rom (romset name = zip name) — never extract.
ZIP_NATIVE = {"fbneo", "mame", "neogeo", "gameandwatch"}
# Systems whose extracted cue/gdi (createcd) or iso (createdvd) the background
# worker turns into CHD. PSP: PPSSPP (1.15+) reads CHD; made with 2048-byte
# hunks as PPSSPP recommends. GC/Wii (rvz needs dolphin-tool, not on Batocera),
# PS3, Xbox stay as-is.
CHD_CD_SYSTEMS = {"psx", "saturn", "dreamcast", "megacd", "pcenginecd", "neogeocd",
                  "3do", "amigacd32", "amigacdtv", "cdi", "pcfx"}
CHD_DVD_SYSTEMS = {"ps2", "psp"}
# GameCube / Wii images the worker turns into RVZ with dolphin-tool (downloaded
# on demand: Settings, "GameCube and Wii to RVZ").
RVZ_SYSTEMS = {"gamecube", "wii"}
RVZ_SOURCES = (".iso", ".gcm", ".wbfs", ".ciso", ".gcz")
# Xbox images packed into zstd squashfs (Settings, off by default). Batocera
# mounts a .squashfs at launch and hands xemu the file inside; xz packs ~5%
# smaller but reads ~20x slower (whole 1 MB blocks per read), so zstd.
SQUASH_SYSTEMS = {"xbox"}
SQUASH_ARGS = ("-comp", "zstd", "-b", "1M", "-noappend", "-quiet")
# Redump Xbox images: a DVD-video partition, then the game's own XDVDFS
# partition at one of these offsets (XGD1/2/3). xemu boots only the game
# partition (an "XISO"); a full redump image shows "Please insert an Xbox disc".
XBOX_PARTITIONS = (0x18300000, 0xFD90000, 0x2080000)
XDVDFS_MAGIC = b"MICROSOFT*XBOX*MEDIA"


def stem_of(name):
    """A game file's name without extension. "X.iso.squashfs" -> "X": a disc
    image packed into squashfs keeps its own extension inside the name."""
    base, ext = os.path.splitext(name)
    if ext.lower() == ".squashfs" and os.path.splitext(base)[1].lower() in DISC_EXTS:
        base = os.path.splitext(base)[0]
    return base


def cpu_threads():
    try:
        return len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        return os.cpu_count() or 4


# chdman's FLAC codecs leak memory on some builds (Batocera 43's chdman 0.285):
# DVDs always skip FLAC (rb_chd.DVD_CODECS); a CD's cdfl leak is bounded by the
# disc (~1-1.3 GB), harmless with 4 GB+, too much for a 1-2 GB box. A "4 GB"
# machine reports ~3.7-3.9 GiB (firmware/GPU reservations), hence 3.5 GiB.
LOW_RAM_BYTES = int(3.5 * 2 ** 30)


def total_ram():
    """Installed RAM in bytes (0 if unknown)."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def low_ram(total=None):
    t = total_ram() if total is None else total
    return 0 < t < LOW_RAM_BYTES


class queue_lock:
    """Exclusive lock on gamelist-pending.jsonl: every writer appends under
    it and rb_finalize claims the file under it, so no append is lost."""

    def __enter__(self):
        import fcntl
        os.makedirs(DATA_DIR, exist_ok=True)
        self.f = open(GAMELIST_QUEUE + ".lock", "w")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        self.f.close()


@contextlib.contextmanager
def try_lock(path):
    """Hold path's exclusive flock for the with block, or yield None at once
    when another process holds it: `with try_lock(p) as fd: if fd is None: ...`.
    The file is made if missing and never truncated; not being able to open it
    raises OSError. Leaving the block releases the lock."""
    import fcntl
    fd = os.open(path, os.O_RDONLY | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield None
        else:
            yield fd
    finally:
        os.close(fd)


def append_gamelist(*entries):
    """Queue game-info entries for rb_finalize (whole lines, under the lock)."""
    if not entries:
        return
    data = "".join(json.dumps(e) + "\n" for e in entries)
    with queue_lock():
        with open(GAMELIST_QUEUE, "a") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())


LOG_MAX = 10 * 1024 * 1024     # romm-browser.log moves to .1 at this size
LOG_KEEP = 9                   # old logs kept: .1 (newest) ... .9


def log(msg):
    """Append a line to romm-browser.log. The app and every worker write it at
    once: each line is one O_APPEND write, so lines never mix, and the writer
    that takes the file past LOG_MAX rotates it (see _rotate_log)."""
    data = (time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n").encode("utf-8", "backslashreplace")
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        fd = os.open(LOG_PATH, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, data)
            full = os.fstat(fd).st_size >= LOG_MAX
        finally:
            os.close(fd)
        if full:
            _rotate_log()
    except OSError:
        pass


def _rotate_log():
    """romm-browser.log -> .1 -> .2 ... -> .9 (dropped), under a lock. Whoever
    gets the lock second finds a fresh log and leaves it. A writer still
    holding the old file appends to .1: nothing is lost."""
    import fcntl
    with open(LOG_PATH + ".lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            if os.path.getsize(LOG_PATH) < LOG_MAX:
                return
        except OSError:
            return
        for i in range(LOG_KEEP - 1, 0, -1):
            try:
                os.replace("%s.%d" % (LOG_PATH, i), "%s.%d" % (LOG_PATH, i + 1))
            except FileNotFoundError:
                pass
        os.replace(LOG_PATH, LOG_PATH + ".1")


def sync_file(path):
    """Make a file's data durable (a rename to a new name doesn't flush it on ext4)."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write(path, write, errors="strict", mode=None, sync=True):
    tmp = "%s.tmp-%d-%d" % (path, os.getpid(), threading.get_ident())
    opener = None if mode is None else (lambda p, flags: os.open(p, flags, mode))
    try:
        with open(tmp, "w", errors=errors, opener=opener) as f:
            write(f)
            if sync:
                f.flush()
                os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def atomic_write_text(path, text, errors="strict", mode=None, sync=True):
    """Replace a text file whole or not at all: write a temp file beside it
    (a unique name), fsync, rename over. A full disk or a kill leaves the old
    file intact. mode: permissions of the new file (e.g. 0o600); sync=False
    for a status file rewritten every few seconds (no fsync)."""
    _atomic_write(path, lambda f: f.write(text), errors, mode, sync)


def atomic_write_json(path, obj, mode=None, sync=True, **dump_kw):
    """atomic_write_text for json.dump(obj, **dump_kw)."""
    _atomic_write(path, lambda f: json.dump(obj, f, **dump_kw), mode=mode, sync=sync)


def spawn_detached(script, python=None):
    """Start one of the app's scripts (rb_chd.py, rb_dl.py, rb_finalize.py)
    in its own session with no stdio, so it outlives its parent. They're
    flock'd: a second one exits at once. Returns False (logged) if it can't."""
    try:
        subprocess.Popen([python or sys.executable, os.path.join(rommlib.APP_DIR, script)], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         close_fds=True)
        return True
    except OSError as e:
        log("spawn %s failed: %r" % (script, e))
        return False


def _keep_unreadable(path):
    """A state file that won't parse is about to be replaced with defaults:
    keep a copy beside it first (once per content), so nothing is lost."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return
    d, base = os.path.split(path)
    for n in os.listdir(d or "."):
        if n.startswith(base + ".bad-"):
            try:
                with open(os.path.join(d, n), "rb") as f:
                    if f.read() == data:
                        return
            except OSError:
                pass
    dest = "%s.bad-%s" % (path, time.strftime("%Y%m%d-%H%M%S"))
    try:
        with open(dest, "xb") as f:
            f.write(data)
        os.chmod(dest, 0o600)                      # it may hold the token
        log("%s couldn't be read; kept as %s" % (base, os.path.basename(dest)))
    except OSError:
        pass


def human_size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return ("%.0f %s" if unit in ("B", "KB") else "%.1f %s") % (n, unit)
        n /= 1024
    return ""


def safe_name(name):
    """Strip path separators / control chars from a server-supplied filename."""
    name = name.replace("/", "_").replace("\\", "_").replace("\0", "")
    name = name.strip().lstrip(".")
    return name or "unnamed"


def write_png(data, dest):
    """Covers come as png or webp; ES and SDL_image both want png/jpg."""
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        try:
            import io
            from PIL import Image
            img = Image.open(io.BytesIO(data))
            img.save(dest, "PNG")
            return
        except Exception:
            pass
    with open(dest, "wb") as f:
        f.write(data)


# ----------------------------------------------------------------- API

class RommError(Exception):
    pass


class AuthRejected(RommError):
    """RomM answered 401/403: the server is there, the token isn't good (or missing)."""


class TlsRejected(RommError):
    """The server's HTTPS certificate failed verification. why: "self-signed",
    "unknown" (an authority this box doesn't trust), "expired", "hostname" or "other"."""

    def __init__(self, msg, why="other"):
        super().__init__(msg)
        self.why = why


def tls_why(exc):
    """What an SSL error says about the certificate, as a TlsRejected.why word
    (only the kind is kept: the error text itself never reaches the screen)."""
    s = str(exc).lower()
    for why, words in (("self-signed", ("self-signed", "self signed")),
                       ("expired", ("expired",)),
                       ("hostname", ("hostname", "doesn't match", "not valid for")),
                       ("unknown", ("unable to get local issuer", "unknown ca", "unable to verify"))):
        if any(w in s for w in words):
            return why
    return "other"


class ConfigUnreadable(RommError):
    """config.json exists but can't be read: it's never saved over (that would
    lose what's in it) until the user fixes it or resets to a clean config."""
SPACE_MARGIN = 2 << 30


def free_bytes(path=None):
    try:
        st = os.statvfs(path or ROMS_ROOT)
    except OSError:
        return 0
    return st.f_bavail * st.f_frsize


class NoSpace(Exception):
    """A download doesn't fit (with the keep-free reserve): it waits, it doesn't fail."""
    def __init__(self, short):
        super().__init__("Waiting for space: free %s more" % human_size(short))
        self.short = short


def _inside(path, root):
    """True if path is strictly inside root (resolving symlinks)."""
    r = os.path.realpath(root)
    p = os.path.realpath(path)
    return p != r and p.startswith(r + os.sep)


def rom_dest(rom, system):
    """Where RomM's file (or folder) for rom goes: roms/<system>/<its fs_name,
    made safe>. None when RomM gave no name, or when that path would leave
    roms/<system> (resolving symlinks)."""
    if not rom.get("fs_name"):
        return None
    sysdir = os.path.join(ROMS_ROOT, system)
    path = os.path.join(sysdir, safe_name(rom["fs_name"]))
    return path if _inside(path, sysdir) else None


def path_size(p):
    if os.path.isfile(p):
        return os.path.getsize(p)
    tot = 0
    for dp, _, fns in os.walk(p):
        for fn in fns:
            try:
                tot += os.path.getsize(os.path.join(dp, fn))
            except OSError:
                pass
    return tot
