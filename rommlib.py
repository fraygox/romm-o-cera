"""RomM Browser backend: RomM API client, platform map, download queue and
post-processing (extract / CHD hand-off / gamelist hand-off) for Batocera.

No pygame imports here, so it can be exercised from a shell on the box.
Python 3.9+ (dev venvs) and 3.12 (Batocera 43).
"""
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile

import requests

VERSION = "0.6.9"            # changes only for a real GitHub release
BUILD = ""                   # internal builds: deploy.sh stamps the git commit here (empty in releases)
VERSION_LABEL = VERSION + ("+" + BUILD if BUILD else "")

APP_DIR = os.path.dirname(os.path.abspath(__file__))
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
USERDATA = os.environ.get("ROMM_BROWSER_USERDATA") or os.path.dirname(ROMS_ROOT.rstrip("/"))
BIOS_DB = os.environ.get("ROMM_BROWSER_BIOS_DB", "/usr/bin/batocera-systems")
RELEASE_DATE = "2026-10-11"   # bump with VERSION (tools/build-release.sh refuses a stale one)
AUTHOR = "fraygox"
PROJECT_URL = "https://github.com/fraygox/romm-o-cera"
UPDATE_REPO = os.environ.get("ROMM_BROWSER_UPDATE_REPO", "fraygox/romm-o-cera")
UPDATE_CACHE = os.path.join(DATA_DIR, "update-check.json")
UA = "romm-o-cera/" + VERSION

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

DEFAULT_CONFIG = {
    "url": "",                 # your RomM, e.g. https://romm.example.com
    "token": "",               # RomM client API token (read scopes)
    "verify_tls": True,        # False for a self-signed cert, or a path to a CA bundle
    "verify_tls_ca": "",       # the CA bundle path last used (Settings cycles back to it)
    "swap_ab": False,          # False: south button = confirm (Batocera default)
    "button_style": "auto",    # button labels: auto | xbox | playstation | nintendo
    "check_updates": True,     # once a day, ask GitHub if a newer release exists
    "chd": True,               # background CHD conversion of extracted disc games
    "chd_threads": "auto",     # chdman threads: "auto" or a number (see chd_threads())
    "chd_low_memory": "auto",  # CDs without chdman's leaky FLAC codec: "auto" (on under 4 GB RAM), true, false
    "keep_free_gb": 20,        # downloads and CHD conversions never leave less than this free (GB)
    "extract_parallel": True,  # the next download starts while the last one is being extracted
    "rvz": False,              # convert GameCube/Wii images to RVZ (downloads dolphin-tool when turned on)
    "xbox_bootable": True,     # Xbox: keep only the game partition xemu boots; "X.xiso" -> "X.iso" (ES lists .iso)
    "squashfs": False,         # pack Xbox images into zstd squashfs (Batocera mounts them at launch)
    "z3ds": True,              # compress decrypted 3DS cartridge images to Azahar's .zcci (lossless)
    "zar": False,              # Xbox 360 images -> .zar for xenia-canary (downloads two small tools)
    "cache_limit_mb": 1024,    # cover cache size cap; least recently used covers go first (0 = no cap)
    "download_hours": "any",   # "any", or "S-E" local hours (e.g. "1-7"): downloads only run then
    "background_downloads": True,  # keep downloading after the app exits
    "speed_while_playing": 0,  # MB/s cap for background downloads while a game runs (0 = no cap)
    "notify": True,            # EmulationStation popup when background downloads finish/fail
    "manuals": True,           # fetch the game's manual (PDF) from RomM for ES's manual viewer
    "sounds": "normal",        # UI sounds: normal | quiet | off
    "platform_map": {},        # per-slug overrides of PLATFORM_MAP (null hides)
}
# Environment overrides (handy for testing / other launchers); config.json wins
# for anything the Settings screen saves, env wins over both when set.
ENV_OVERRIDES = {"ROMM_URL": "url", "ROMM_TOKEN": "token", "ROMM_VERIFY_TLS": "verify_tls"}


ES_API = os.environ.get("ROMM_BROWSER_ES", "http://127.0.0.1:1234")


def es_running_game():
    """Path of the game ES is running ('' when idle or only this app runs);
    None when ES can't be asked."""
    import urllib.request
    try:
        with urllib.request.urlopen(ES_API + "/runningGame", timeout=5) as r:
            status, body = r.status, r.read()
    except Exception:
        return None
    if status != 200 or not body.strip():
        return ""
    try:
        path = json.loads(body).get("path") or ""
    except (ValueError, AttributeError):
        return ""
    return "" if path.endswith("rommbrowser.pygame") else path


def _es_json(path, timeout=10):
    import urllib.request
    with urllib.request.urlopen(ES_API + path, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


def es_game_list(systems=None):
    """Every game EmulationStation lists (not hidden ones: discs behind a
    playlist), as dicts with system, path, name, playcount, lastplayed
    ("YYYYmmddTHHMMSS" or "" if never). Raises RommError if ES can't be asked."""
    try:
        if systems is None:
            systems = [x["name"] for x in _es_json("/systems") or []
                       if x.get("gamesystem") == "true" and x.get("collection") != "true"
                       and int(x.get("totalGames") or 0) > 0]
        out = []
        for sysname in systems:
            try:
                games = _es_json("/systems/%s/games" % urllib.parse.quote(sysname)) or []
            except Exception:
                continue
            for g in games:
                if g.get("hidden") == "true" or not g.get("path", "").startswith(ROMS_ROOT + "/"):
                    continue
                lp = g.get("lastplayed") or ""
                out.append({"system": sysname, "path": g["path"], "name": g.get("name") or
                            os.path.splitext(os.path.basename(g["path"]))[0],
                            "playcount": int(float(g.get("playcount") or 0)),
                            "lastplayed": lp if lp[:1].isdigit() and lp != "0" else ""})
        return out
    except RommError:
        raise
    except Exception as e:
        raise RommError("EmulationStation didn't answer (%s)" % e.__class__.__name__)


def game_files(path):
    """Everything that makes up the game ES lists at `path`: a playlist's
    discs, a cue/gdi's tracks — never anything outside its system folder."""
    out, seen = [], set()

    def add(p):
        p = os.path.normpath(p)
        if p in seen or not os.path.lexists(p):
            return
        seen.add(p)
        out.append(p)
        low = p.lower()
        if low.endswith(".m3u") and os.path.isfile(p):
            with open(p, errors="replace") as f:
                for ln in f:
                    if ln.strip() and not ln.startswith("#"):
                        add(os.path.join(os.path.dirname(p), ln.strip()))
        elif low.endswith((".cue", ".gdi")) and os.path.isfile(p):
            for q in sheet_sources(p):
                add(q)
    add(path)
    rel = os.path.relpath(path, ROMS_ROOT).split(os.sep)
    sysdir = os.path.join(ROMS_ROOT, rel[0]) if len(rel) > 1 else None
    return [p for p in out if sysdir and _inside(p, sysdir)]


def delete_game(system, path, library=None):
    """Delete a game ES lists (any game, not only ours): its files, then any
    game folder left empty. Saves and states live elsewhere and are kept.
    Returns (deleted paths, bytes freed)."""
    sysdir = os.path.join(ROMS_ROOT, system)
    files = game_files(path)
    freed = 0
    for p in files:
        if not _inside(p, sysdir):
            continue
        freed += path_size(p)
        if os.path.isdir(p) and not os.path.islink(p):
            shutil.rmtree(p)
        elif os.path.lexists(p):
            os.remove(p)
    for p in sorted({os.path.dirname(p) for p in files}, key=len, reverse=True):
        d = p
        while _inside(d, sysdir) and os.path.isdir(d) and not os.listdir(d):
            os.rmdir(d)
            d = os.path.dirname(d)
    if library is not None and files:
        gone = {os.path.normpath(p) for p in files}

        def drop(inst):
            for rid, rec in list(inst.items()):
                paths = [os.path.normpath(x) for x in rec.get("paths", [])]
                if paths and all(x in gone or not os.path.exists(x) for x in paths):
                    inst.pop(rid, None)
        library.update(drop)
    if files:
        append_gamelist({"system": system, "remove": files})
    log("DELETE %s: %s (%s)" % (system, files, human_size(freed)))
    return files, freed


# ---------------------------------------------------------- incomplete disc images

def _pvd_volume(sector16):
    """ISO9660 volume size (sectors) from the 2048-byte sector 16, or None."""
    if len(sector16) >= 88 and sector16[1:6] == b"CD001":
        return int.from_bytes(sector16[80:84], "little")
    return None


class _Beyond(Exception):
    """A sector past what was read (and, for a CHD head, maybe past the end)."""
    def __init__(self, lba):
        self.lba = lba


def _files_end(read, limit=4_300_000, have=None):
    """Walk an ISO9660 directory tree: (end, boot_end) — the highest sector
    any entry reaches (exclusive), and the highest the game needs to start
    (root directory, SYSTEM.CNF and the boot program it names). None if
    there's no ISO9660 filesystem. read(lba) returns 2048 bytes or raises
    _Beyond; sectors at/after `have` (the image's end) count as unreadable
    and are recorded rather than raised. Entries past `limit` (no disc is
    that big) are garbage and ignored."""
    pvd = read(16)
    if pvd[1:6] != b"CD001":
        return None
    root = pvd[156:190]
    rlba, rsize = int.from_bytes(root[2:6], "little"), int.from_bytes(root[10:14], "little")
    todo = [("", rlba, rsize)]
    seen, end, files = set(), 0, {}
    boot_end = rlba + (rsize + 2047) // 2048
    while todo and len(seen) < 5000:
        path, lba, size = todo.pop()
        if lba in seen or size <= 0 or lba + (size + 2047) // 2048 > limit:
            continue
        seen.add(lba)
        nsec = (size + 2047) // 2048
        end = max(end, lba + nsec)
        for k in range(nsec):
            if have is not None and lba + k >= have:
                break                                   # past the image: can't be read
            sec = read(lba + k)
            i = 0
            while i < 2048 and sec[i]:
                rec = sec[i:i + sec[i]]
                i += sec[i]
                if len(rec) < 34:
                    break
                name = rec[33:33 + rec[32]]
                if name in (b"\x00", b"\x01"):
                    continue
                flba, fsize = int.from_bytes(rec[2:6], "little"), int.from_bytes(rec[10:14], "little")
                fend = flba + (fsize + 2047) // 2048
                if fend > limit:
                    continue
                full = path + "/" + name.decode("ascii", "replace").split(";")[0].upper()
                if rec[25] & 2:
                    todo.append((full, flba, fsize))
                elif fsize:
                    end = max(end, fend)
                    files[full] = (flba, fsize, fend)
    cnf = files.get("/SYSTEM.CNF")
    if cnf:
        boot_end = max(boot_end, cnf[2])
        if have is None or cnf[0] < have:
            m = re.search(rb"BOOT2?\s*=\s*cdrom0?:\\?([^\s;]+)", read(cnf[0])[:cnf[1]], re.I)
            if m:
                exe = "/" + m.group(1).decode("ascii", "replace").replace("\\", "/").lstrip("/").upper()
                if exe in files:
                    boot_end = max(boot_end, files[exe][2])
    return end, boot_end


def _verdict(have, r, vol=None):
    """(have, need, certain) from _files_end's (end, boot_end): certain only
    when what the game needs to start lies past the end of the image. Then
    the directory tree couldn't be read, so the header's size says how big
    the disc should be."""
    if not r:
        return None
    end, boot_end = r
    sure = boot_end > have
    need = max(end, boot_end, vol or 0) if sure else max(end, boot_end)
    return (have, need, sure)


def _sector_reader(f, raw, base=0):
    """read(lba) over an open image (raw 2352-byte or plain 2048-byte sectors)."""
    def read(lba):
        if raw:
            f.seek(base + lba * 2352)
            sec = f.read(2352)
            if len(sec) < 2352:
                raise _Beyond(lba)
            off = 16 if sec[15] == 1 else 24
            return sec[off:off + 2048]
        f.seek(base + lba * 2048)
        sec = f.read(2048)
        if len(sec) < 2048:
            raise _Beyond(lba)
        return sec
    return read


def _chd_head(path, nbytes, chdman):
    """First nbytes of a CHD's track data (chdman extract*, stopped early),
    plus chdman info's text and whether it's a DVD CHD."""
    import tempfile
    tmpdir = os.path.join(DATA_DIR, "tmp")
    os.makedirs(tmpdir, exist_ok=True)
    t = tempfile.mkdtemp(prefix="head-", dir=tmpdir)
    try:
        info = subprocess.run([chdman, "info", "-i", path], capture_output=True, text=True, timeout=30).stdout
        dvd = "Tag='DVD" in info
        b = os.path.join(t, "x.bin")
        cmd = [chdman, "extractdvd", "-i", path, "-o", b, "-f"] if dvd else \
            [chdman, "extractcd", "-i", path, "-o", os.path.join(t, "x.cue"), "-ob", b, "-f"]
        pr = subprocess.Popen(["nice", "-n", "19"] + cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        end = time.time() + 120
        while pr.poll() is None and time.time() < end:
            if os.path.exists(b) and os.path.getsize(b) >= nbytes:
                break
            time.sleep(0.05)
        pr.kill()
        pr.wait()
        with open(b, "rb") as f:
            head = f.read(nbytes)
        return head, info, dvd
    except (OSError, subprocess.SubprocessError):
        return None, "", False
    finally:
        shutil.rmtree(t, ignore_errors=True)


def _quick_ok(read, have):
    """The header says the filesystem fits in the image: complete, no walk needed."""
    vol = _pvd_volume(read(16))
    return vol is not None and vol <= have + 16


def disc_sectors(path, chdman="chdman"):
    """(sectors in the image, sectors its files need, certain) for a
    .cue/.iso/.chd whose first track is ISO9660 data; None when that can't be
    told (audio first track, GDI, unreadable). Header within the image:
    complete. Else the whole directory tree is walked: the root directory,
    SYSTEM.CNF or its boot program past the end is certain proof it's cut
    short (the game can't start); anything else past the end is only a hint
    (some discs list dummy files and directories past their end)."""
    low = path.lower()
    try:
        if low.endswith(".iso"):
            with open(path, "rb") as f:
                have = os.path.getsize(path) // 2048
                read = _sector_reader(f, False)
                try:
                    vol = _pvd_volume(read(16))
                    if _quick_ok(read, have):
                        return (have, vol, False)
                    r = _files_end(read, 4_300_000, have)
                except _Beyond:
                    return None                         # no header at all: not a disc image we can judge
            return _verdict(have, r, vol)
        if low.endswith(".cue"):
            tracks = sheet_sources(path)
            if not tracks or not all(os.path.isfile(t) for t in tracks):
                return None
            with open(tracks[0], "rb") as f:
                raw = f.read(12) == b"\x00" + b"\xff" * 10 + b"\x00"
                size = 2352 if raw else 2048
                have = sum(os.path.getsize(t) for t in tracks) // size
                read = _sector_reader(f, raw)
                try:
                    vol = _pvd_volume(read(16))
                    if _quick_ok(read, have):
                        return (have, vol, False)
                    # a multi-file cue: only the first (data) track's file is read
                    r = _files_end(read, 450_000 if raw else 4_300_000,
                                   os.path.getsize(tracks[0]) // size)
                except _Beyond:
                    return None
            return _verdict(have, r, vol)
        if low.endswith(".chd"):
            want = 64 << 10                             # header first; the tree only if it's in doubt
            while True:
                head, info, dvd = _chd_head(path, want, chdman)
                if not head:
                    return None
                if dvd:
                    m = re.search(r"Logical size:\s*([\d,]+)", info)
                    have = int(m.group(1).replace(",", "")) // 2048 if m else 0
                else:
                    have = sum(int(x) for x in re.findall(r"FRAMES:(\d+)", info))
                raw = not dvd and head[:12] == b"\x00" + b"\xff" * 10 + b"\x00"
                import io
                read = _sector_reader(io.BytesIO(head), raw)
                try:
                    if _quick_ok(read, have):
                        return (have, _pvd_volume(read(16)), False)
                    if want < 8 << 20:
                        want = 8 << 20
                        continue
                    return _verdict(have, _files_end(read, 4_300_000 if dvd else 450_000, have),
                                    _pvd_volume(read(16))) if have else None
                except _Beyond as b:
                    if b.lba <= 16 or len(head) < want or want >= 256 << 20:
                        return None                     # no header, or can't reach it cheaply: don't guess
                    want *= 4                           # a directory further in: read more
    except (OSError, ValueError, IndexError):
        return None
    return None


def disc_incomplete(path, chdman="chdman"):
    """None if complete (or can't tell); else (sectors present, sectors
    needed, certain) — see disc_sectors."""
    r = disc_sectors(path, chdman)
    if not r:
        return None
    have, need, sure = r
    return r if sure or need > have + 16 else None


# ---------------------------------------------------------- dolphin-tool (GameCube / Wii -> RVZ)

# Built from the same Dolphin commit Batocera 43.1 ships (its dolphin-emu.mk
# turns the CLI tool off), by tools/dolphin-tool/make.sh; published as its own
# release on GitHub with Dolphin's license and the exact source it came from.
_DT_BASE = "https://github.com/fraygox/romm-o-cera/releases/download/dolphin-tool-2603a/"
DOLPHIN_TOOLS = {           # per CPU: the archive RomM-o-Cera downloads, and its pinned SHA-256
    "x86_64": {"url": _DT_BASE + "dolphin-tool-2603a-x86_64.tar.gz",
               "sha256": "19eab07e5ac5658fed51d56af5ed475831519fc28961ac95d1b43c4fb7d04ea7", "size": 7990471},
    "aarch64": {"url": _DT_BASE + "dolphin-tool-2603a-aarch64.tar.gz",
                "sha256": "655cb5520ea3742505ae30ec6100165a8f05c67ceacf6c52e68ba27b75162b06", "size": 7298926},
}


def _machine():
    """The CPU dolphin-tool must be built for. uname says what the kernel is;
    a 64-bit kernel can run 32-bit programs only (some ARM boards), and the
    tool must match the programs, so a 32-bit Python means "not supported"."""
    import platform
    import struct
    m = platform.machine()
    m = {"AMD64": "x86_64", "arm64": "aarch64"}.get(m, m)
    if struct.calcsize("P") != 8:                  # 32-bit userland
        return {"x86_64": "i686", "aarch64": "armv7l"}.get(m, m)
    return m


DOLPHIN_TOOL = dict({"version": "2603a", "commit": "5e7cc91d8c9a43ca189b288937f65c9763af9c22"},
                    **DOLPHIN_TOOLS.get(_machine(), {"url": "", "sha256": "", "size": 0}))
TOOLS_DIR = os.path.join(DATA_DIR, "tools")
RVZ_ARGS = ["-f", "rvz", "-c", "zstd", "-l", "5", "-b", "131072"]   # Dolphin's recommended RVZ settings


def dolphin_tool_dir():
    return os.path.join(os.environ.get("ROMM_BROWSER_TOOLS", TOOLS_DIR), "dolphin-tool-" + DOLPHIN_TOOL["version"])


def dolphin_tool_path():
    """The installed dolphin-tool, or None."""
    p = os.path.join(dolphin_tool_dir(), "dolphin-tool")
    return p if os.path.isfile(p) and os.access(p, os.X_OK) else None


def rvz_supported():
    return _machine() in DOLPHIN_TOOLS


def xbox_partition(path):
    """Where an Xbox image's game partition starts: 0 for an XISO (what xemu
    boots), the offset for a full redump image, None if it's neither."""
    try:
        with open(path, "rb") as f:
            for off in (0,) + XBOX_PARTITIONS:
                f.seek(off + 0x10000)
                if f.read(len(XDVDFS_MAGIC)) == XDVDFS_MAGIC:
                    return off
    except OSError:
        pass
    return None


def _collapse_front(path, nbytes):
    """Drop the first nbytes of a file in place (Linux fallocate collapse-range:
    no copy, no extra space). False where the filesystem can't (exFAT, btrfs)."""
    import ctypes
    import ctypes.util
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
        fd = os.open(path, os.O_RDWR)
    except OSError:
        return False
    try:
        return libc.fallocate(fd, 0x08, ctypes.c_long(0), ctypes.c_long(nbytes)) == 0   # FALLOC_FL_COLLAPSE_RANGE
    finally:
        os.close(fd)


def make_xiso(path, free=None):
    """Make a full redump Xbox image bootable: keep only its game partition.
    In place when the filesystem allows, else through a copy (needs the
    room). Returns the bytes dropped (0 if it was already an XISO, or isn't
    an Xbox image); raises NoSpace when a copy doesn't fit."""
    off = xbox_partition(path)
    if not off:
        return 0
    if _collapse_front(path, off):
        return off
    size = os.path.getsize(path)
    free = free_bytes(os.path.dirname(path)) if free is None else free
    if free < size - off + SPACE_MARGIN:
        raise NoSpace(size - off + SPACE_MARGIN - free)
    tmp = path + ".xiso.part"
    with open(path, "rb") as src, open(tmp, "wb") as out:
        src.seek(off)
        shutil.copyfileobj(src, out, 4 << 20)
    os.replace(tmp, path)
    return off


def xbox_cut(system, path, romm_file):
    """An Xbox image this box cut to its game partition (make_xiso): exactly
    one partition offset smaller than RomM's full disc, and an XISO now."""
    want = (romm_file or {}).get("file_size_bytes")
    try:
        short = want - os.path.getsize(path) if want else None
    except OSError:
        return False
    return system == "xbox" and short in XBOX_PARTITIONS and xbox_partition(path) == 0


def xiso_to_iso(path):
    """ "X.xiso" -> "X.iso" (Batocera lists Xbox games as .iso / .squashfs only).
    Returns the path the file now has; left alone if an "X.iso" exists."""
    if not path.lower().endswith(".xiso"):
        return path
    new = path[:-5] + ".iso"
    if os.path.exists(new):
        log("xbox: %s kept as is: %s exists" % (os.path.basename(path), os.path.basename(new)))
        return path
    os.rename(path, new)
    return new


def stem_of(name):
    """A game file's name without extension. "X.iso.squashfs" -> "X": a disc
    image packed into squashfs keeps its own extension inside the name."""
    base, ext = os.path.splitext(name)
    if ext.lower() == ".squashfs" and os.path.splitext(base)[1].lower() in DISC_EXTS:
        base = os.path.splitext(base)[0]
    return base


def squashfs_enabled(cfg, system):
    return system in SQUASH_SYSTEMS and bool((cfg or {}).get("squashfs")) and shutil.which("mksquashfs") is not None


def rvz_enabled(cfg):
    return bool((cfg or {}).get("rvz")) and dolphin_tool_path() is not None


# ---------------------------------------------------------- 3DS -> Z3DS (.zcci / .zcxi)

# Azahar (Batocera 43.1 ships 2125.0.1) opens Z3DS files directly: a 0x20-byte
# header, optional metadata, then a standard zstd *seekable* stream (independent
# frames + a seek table), as Azahar's own "Compress ROM File" writes them
# (src/common/zstd_compression.cpp). Written here with the box's libzstd through
# ctypes: no tool to download. Decrypted dumps only (Azahar can't run encrypted
# ones either). CIA stays as it is for now (32 MiB frames, title info from the CIA).
Z3DS_SYSTEMS = {"3ds"}
Z3DS_SOURCES = {".3ds": ".zcci", ".cci": ".zcci", ".cxi": ".zcxi"}
Z3DS_EXTS = (".zcci", ".zcxi", ".zcia")
Z3DS_FRAME = 256 << 10           # Azahar's frame size for CCI/CXI
Z3DS_LEVEL = 3                   # zstd's default, what Azahar uses
_ZSTD = []


class Z3dsError(Exception):
    pass


def _zstd():
    """The box's libzstd (ctypes), or None."""
    if not _ZSTD:
        import ctypes
        lib = None
        for name in ("libzstd.so.1", "libzstd.so"):
            try:
                lib = ctypes.CDLL(name)
                break
            except OSError:
                continue
        if lib is not None:
            c = ctypes
            lib.ZSTD_compressBound.restype = c.c_size_t
            lib.ZSTD_compressBound.argtypes = [c.c_size_t]
            lib.ZSTD_compress.restype = c.c_size_t
            lib.ZSTD_compress.argtypes = [c.c_void_p, c.c_size_t, c.c_char_p, c.c_size_t, c.c_int]
            lib.ZSTD_decompress.restype = c.c_size_t
            lib.ZSTD_decompress.argtypes = [c.c_void_p, c.c_size_t, c.c_char_p, c.c_size_t]
            lib.ZSTD_isError.restype = c.c_uint
            lib.ZSTD_isError.argtypes = [c.c_size_t]
            try:                                       # per-frame content checksums (zstd 1.4+)
                lib.ZSTD_createCCtx.restype = c.c_void_p
                lib.ZSTD_freeCCtx.argtypes = [c.c_void_p]
                lib.ZSTD_CCtx_setParameter.restype = c.c_size_t
                lib.ZSTD_CCtx_setParameter.argtypes = [c.c_void_p, c.c_int, c.c_int]
                lib.ZSTD_compress2.restype = c.c_size_t
                lib.ZSTD_compress2.argtypes = [c.c_void_p, c.c_void_p, c.c_size_t, c.c_char_p, c.c_size_t]
                lib.has_cctx = True
            except AttributeError:
                lib.has_cctx = False
        _ZSTD.append(lib)
    return _ZSTD[0]


def z3ds_supported():
    return _zstd() is not None


def z3ds_enabled(cfg, system):
    return system in Z3DS_SYSTEMS and bool((cfg or {}).get("z3ds")) and z3ds_supported()


def z3ds_target(path):
    """'X.3ds' -> 'X.zcci' (None if it isn't a file Z3DS packs)."""
    base, ext = os.path.splitext(path)
    out = Z3DS_SOURCES.get(ext.lower())
    return base + out if out else None


def z3ds_info(path):
    """(magic, title-info bytes) of a decrypted CCI/CXI; Z3dsError otherwise."""
    with open(path, "rb") as f:
        h = f.read(0x200)
        if h[0x100:0x104] == b"NCSD":
            f.seek(int.from_bytes(h[0x120:0x124], "little") * 0x200)    # partition 0: the game
            f_ncch, magic = f.read(0x200), b"NCSD"
        elif h[0x100:0x104] == b"NCCH":
            f_ncch, magic = h, b"NCCH"
        else:
            raise Z3dsError("not a 3DS cartridge image")
    if len(f_ncch) < 0x200 or f_ncch[0x100:0x104] != b"NCCH":
        raise Z3dsError("not a 3DS cartridge image")
    if not f_ncch[0x18F] & 0x04:
        raise Z3dsError("an encrypted dump (Azahar runs decrypted ones only)")
    unit = 0x200 << f_ncch[0x18E]
    pid, ver = f_ncch[0x118:0x120], f_ncch[0x112:0x114]
    size = int.from_bytes(f_ncch[0x104:0x108], "little") * unit
    return magic, pid + size.to_bytes(8, "little") + ver + b"\0" * 6


def _z3ds_metadata(items):
    out = bytearray([1])                       # metadata version
    for k, v in items:
        out += bytes([1, len(k)]) + len(v).to_bytes(2, "little") + k + v
    out += b"\0" * 4                           # end marker
    out += b"\0" * ((-len(out)) % 16)
    return bytes(out)


def z3ds_compress(src, dst, threads=2, pause=None, progress=None):
    """Write src (decrypted .3ds/.cci/.cxi) as Z3DS to dst. pause(): called
    between frames, may block (a game is running). progress(fraction).
    Returns the source's CRC32 (hex)."""
    import ctypes
    import zlib
    from concurrent.futures import ThreadPoolExecutor
    lib = _zstd()
    if lib is None:
        raise Z3dsError("libzstd isn't on this system")
    magic, title = z3ds_info(src)
    total = os.path.getsize(src)
    md = _z3ds_metadata([(b"compressor", b"RomM-o-Cera " + VERSION.encode()),
                         (b"date", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()).encode()),
                         (b"maxframesize", str(Z3DS_FRAME).encode()), (b"titleinfo", title)])

    def pack(chunk):                       # ctypes drops the GIL: frames compress in parallel
        cap = lib.ZSTD_compressBound(len(chunk))
        buf = ctypes.create_string_buffer(cap)
        if lib.has_cctx:
            # each frame carries zstd's content checksum: a damaged .zcci then fails to
            # decode on its own (Check games, replacing a broken copy), not only against
            # the original's CRC. Azahar reads such frames as usual.
            cc = lib.ZSTD_createCCtx()
            try:
                lib.ZSTD_CCtx_setParameter(cc, 100, Z3DS_LEVEL)      # ZSTD_c_compressionLevel
                lib.ZSTD_CCtx_setParameter(cc, 201, 1)               # ZSTD_c_checksumFlag
                n = lib.ZSTD_compress2(cc, buf, cap, chunk, len(chunk))
            finally:
                lib.ZSTD_freeCCtx(cc)
        else:
            n = lib.ZSTD_compress(buf, cap, chunk, len(chunk), Z3DS_LEVEL)
        if lib.ZSTD_isError(n):
            raise Z3dsError("zstd couldn't compress a frame")
        return buf.raw[:n]
    crc, done, packed, table = 0, 0, 0, []
    with open(src, "rb") as fi, open(dst, "wb") as fo, ThreadPoolExecutor(max(1, int(threads))) as ex:
        fo.write(b"\0" * 0x20 + md)
        pend = []

        def drain(n):
            nonlocal packed
            while len(pend) > n:
                size, fut = pend.pop(0)
                b = fut.result()
                fo.write(b)
                table.append((len(b), size))
                packed += len(b)
        while True:
            if pause:
                pause()
            chunk = fi.read(Z3DS_FRAME)
            if not chunk:
                break
            crc = zlib.crc32(chunk, crc)
            done += len(chunk)
            pend.append((len(chunk), ex.submit(pack, chunk)))
            drain(max(1, int(threads)) * 4)
            if progress and total:
                progress(done / total)
        drain(0)
        seek = b"".join(c.to_bytes(4, "little") + d.to_bytes(4, "little") for c, d in table)
        seek += len(table).to_bytes(4, "little") + b"\0" + (0x8F92EAB1).to_bytes(4, "little")
        seek = (0x184D2A5E).to_bytes(4, "little") + len(seek).to_bytes(4, "little") + seek
        fo.write(seek)
        packed += len(seek)
        fo.seek(0)
        fo.write(b"Z3DS" + magic + bytes([1, 0]) + (0x20).to_bytes(2, "little") + len(md).to_bytes(4, "little")
                 + packed.to_bytes(8, "little") + total.to_bytes(8, "little"))
    if done != total:
        raise Z3dsError("the file changed while it was read")
    return "%08x" % (crc & 0xffffffff)


def z3ds_header(path):
    """{"size": original size, "complete": the file is as long as its header
    says} from a Z3DS file, or None if it isn't one."""
    try:
        with open(path, "rb") as f:
            h = f.read(0x20)
            end = os.fstat(f.fileno()).st_size
    except OSError:
        return None
    if len(h) < 0x20 or h[:4] != b"Z3DS" or h[8] != 1:
        return None
    base = int.from_bytes(h[0x0A:0x0C], "little") + int.from_bytes(h[0x0C:0x10], "little")
    return {"size": int.from_bytes(h[0x18:0x20], "little"),
            "complete": end == base + int.from_bytes(h[0x10:0x18], "little")}


def z3ds_verify(path, pause=None, progress=None):
    """Read a Z3DS file the way Azahar does (header, seek table) and decode
    every frame: (CRC32 hex, size) of the original; Z3dsError if it's broken."""
    import ctypes
    import zlib
    lib = _zstd()
    if lib is None:
        raise Z3dsError("libzstd isn't on this system")
    with open(path, "rb") as f:
        h = f.read(0x20)
        if len(h) < 0x20 or h[:4] != b"Z3DS" or h[8] != 1:
            raise Z3dsError("not a Z3DS file")
        hsize = int.from_bytes(h[0x0A:0x0C], "little")
        base = hsize + int.from_bytes(h[0x0C:0x10], "little")
        packed, total = int.from_bytes(h[0x10:0x18], "little"), int.from_bytes(h[0x18:0x20], "little")
        f.seek(0, 2)
        end = f.tell()
        if end != base + packed:
            raise Z3dsError("size doesn't match its header (cut short?)")
        f.seek(end - 9)
        n, desc, mg = int.from_bytes(f.read(4), "little"), f.read(1)[0], int.from_bytes(f.read(4), "little")
        if mg != 0x8F92EAB1:
            raise Z3dsError("no seek table")
        esz = 12 if desc & 0x80 else 8
        if n * esz + 17 > packed:
            raise Z3dsError("damaged seek table")
        f.seek(end - 9 - n * esz - 8)
        if int.from_bytes(f.read(4), "little") != 0x184D2A5E:
            raise Z3dsError("damaged seek table")
        f.read(4)
        tab = f.read(n * esz)
        sizes = [(int.from_bytes(tab[i * esz:i * esz + 4], "little"),
                  int.from_bytes(tab[i * esz + 4:i * esz + 8], "little")) for i in range(n)]
        # the table must add up before anything is decoded (and before a buffer is sized from it)
        if sum(c for c, _ in sizes) + n * esz + 17 != packed or sum(d for _, d in sizes) != total \
                or any(d > (1 << 30) for _, d in sizes):
            raise Z3dsError("damaged seek table")
        f.seek(base)
        crc, done = 0, 0
        for i, (c, d) in enumerate(sizes):
            if pause:
                pause()
            out = ctypes.create_string_buffer(d)
            r = lib.ZSTD_decompress(out, d, f.read(c), c)
            if lib.ZSTD_isError(r) or r != d:
                raise Z3dsError("frame %d doesn't decode" % i)
            crc = zlib.crc32(out.raw, crc)
            done += d
            if progress and total:
                progress(done / total)
    if done != total:
        raise Z3dsError("decodes to %d bytes, header says %d" % (done, total))
    return "%08x" % (crc & 0xffffffff), done


def rvz_state(cfg):
    """{"label", "help"} for the Settings row."""
    if not rvz_supported():
        return {"label": "Not on this CPU", "help": "dolphin-tool is built for x86_64 and 64-bit ARM only"}
    if (cfg or {}).get("rvz") and dolphin_tool_path():
        return {"label": "On", "help": "Lossless, usually much smaller (dolphin-tool %s)" % DOLPHIN_TOOL["version"]}
    if (cfg or {}).get("rvz"):
        return {"label": "Tool missing", "help": "{A} downloads dolphin-tool again"}
    return {"label": "Off", "help": "Turning it on downloads dolphin-tool once"}


def _fetch_tool_archive(label, url, want, dest, progress=None):
    """Download a tool archive, check its pinned SHA-256 and unpack it into
    dest + ".new" (returned). Refuses links, absolute paths and "..";"""
    import hashlib
    import tarfile
    if not want:
        raise RommError("This version doesn't know %s's checksum yet." % label)
    tmpdir = os.path.join(DATA_DIR, "tmp")
    os.makedirs(tmpdir, exist_ok=True)
    tgz = os.path.join(tmpdir, os.path.basename(dest) + ".tar.gz")
    h = hashlib.sha256()
    try:
        with requests.get(url, stream=True, timeout=30) as r:
            if r.status_code != 200:
                raise RommError("Couldn't download %s (HTTP %d)." % (label, r.status_code))
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            with open(tgz, "wb") as f:
                for chunk in r.iter_content(1 << 16):
                    f.write(chunk)
                    h.update(chunk)
                    got += len(chunk)
                    if progress:
                        progress(got, total)
    except requests.RequestException as e:
        raise RommError("Couldn't download %s (%s)." % (label, e.__class__.__name__)) from None
    if h.hexdigest() != want:
        os.remove(tgz)
        raise RommError("The %s download didn't match its checksum; not installed." % label)
    staging = dest + ".new"
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging)
    with tarfile.open(tgz) as t:
        for m in t.getmembers():
            if not (m.isfile() or m.isdir()) or m.name.startswith(("/", "..")) or "/../" in m.name:
                raise RommError("Unexpected file in the %s archive: %s" % (label, m.name))
        try:
            t.extractall(staging, filter="data")       # Python 3.12+: tarfile's own safety net too
        except TypeError:
            t.extractall(staging)
    os.remove(tgz)
    return staging


def install_dolphin_tool(progress=None, url=None, sha256=None):
    """Download, check and unpack dolphin-tool into DATA_DIR/tools. Returns
    its path. The archive holds the binary, Dolphin's license and SOURCE.txt."""
    if not rvz_supported():
        raise RommError("dolphin-tool isn't built for this CPU (%s)." % _machine())
    url = url or os.environ.get("ROMM_BROWSER_DOLPHIN_URL") or DOLPHIN_TOOL["url"]
    want = (sha256 or os.environ.get("ROMM_BROWSER_DOLPHIN_SHA256") or DOLPHIN_TOOL["sha256"]).lower()
    if not want:
        raise RommError("This version doesn't know dolphin-tool's checksum yet.")
    dest = dolphin_tool_dir()
    staging = _fetch_tool_archive("dolphin-tool", url, want, dest, progress)
    exe = os.path.join(staging, "dolphin-tool")
    if not os.path.isfile(exe):
        raise RommError("The dolphin-tool archive has no dolphin-tool in it.")
    os.chmod(exe, 0o755)
    try:
        out = subprocess.run([exe, "--help"], capture_output=True, text=True, timeout=30)
    except OSError as e:
        shutil.rmtree(staging, ignore_errors=True)
        raise RommError("dolphin-tool doesn't run on this box (%s)." % e.strerror) from None
    if "convert" not in (out.stdout + out.stderr):
        shutil.rmtree(staging, ignore_errors=True)
        raise RommError("dolphin-tool doesn't run on this box.")
    shutil.rmtree(dest, ignore_errors=True)
    os.replace(staging, dest)
    log("dolphin-tool %s installed (%s)" % (DOLPHIN_TOOL["version"], DOLPHIN_TOOL["commit"][:10]))
    return os.path.join(dest, "dolphin-tool")


# ---------------------------------------------------------- Xbox 360 -> .zar (ZArchive)

# xenia-canary (Batocera 43.1, x86_64 only) opens a ".zar": a ZArchive of the
# disc's files. Made with two small tools RomM-o-Cera downloads when the setting
# is turned on (like dolphin-tool): extract-xiso (game partition -> folder) and
# zarchive (folder -> .zar), built by tools/xbox360-tools/ and published as their
# own GitHub release with their licenses and exact source. Neither tool's exit
# code is enough on its own: extract-xiso exits 0 on a cut-short image and
# zarchive never checks its archive's SHA-256, so the worker checks both itself.
_X360_BASE = "https://github.com/fraygox/romm-o-cera/releases/download/xbox360-tools-2610a/"
X360_TOOLS_ALL = {          # per CPU (Batocera has no Xbox 360 off x86_64)
    "x86_64": {"url": _X360_BASE + "xbox360-tools-2610a-x86_64.tar.gz",
               "sha256": "b5b63fd0fd7b13a69b2c75c51059810df4bf89cda70cd711f0a4a267967bdd8f", "size": 757610},
}
X360_TOOLS = dict({"version": "2610a", "extract_xiso": "3f5b62cfe68f000b0e3c8a30104973f3a297948e",
                   "zarchive": "b467f7aa342895e66a1a003377ed63b3c1be3de3"},
                  **X360_TOOLS_ALL.get(_machine(), {"url": "", "sha256": "", "size": 0}))
ZAR_SYSTEMS = {"xbox360"}
ZAR_MAGIC, ZAR_VERSION = 0x169f52d6, 0x61bf3a01


def x360_tools_dir():
    return os.path.join(os.environ.get("ROMM_BROWSER_TOOLS", TOOLS_DIR), "xbox360-tools-" + X360_TOOLS["version"])


def x360_tools():
    """(extract-xiso, zarchive) paths when both are installed, else None."""
    d = x360_tools_dir()
    exe = [os.path.join(d, n) for n in ("extract-xiso", "zarchive")]
    return tuple(exe) if all(os.path.isfile(p) and os.access(p, os.X_OK) for p in exe) else None


def zar_supported():
    return _machine() in X360_TOOLS_ALL


def zar_enabled(cfg, system):
    return system in ZAR_SYSTEMS and bool((cfg or {}).get("zar")) and x360_tools() is not None


def zar_state(cfg):
    """{"label", "help"} for the Settings row."""
    if not zar_supported():
        return {"label": "Not on this CPU", "help": "Xbox 360 emulation on Batocera is x86_64 only"}
    if (cfg or {}).get("zar") and x360_tools():
        return {"label": "On", "help": "Smaller files xenia-canary opens directly"}
    if (cfg or {}).get("zar"):
        return {"label": "Tools missing", "help": "{A} downloads them again"}
    return {"label": "Off", "help": "Turning it on downloads two small tools once"}


def zar_check(path, full=False):
    """None if a .zar looks whole; else what's wrong. Always: the size and
    magic in its footer. full: also the SHA-256 it carries over the whole file
    (zarchive itself never checks it)."""
    import hashlib
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size < 144:
                return "too short to be a .zar"
            f.seek(size - 144)
            foot = f.read(144)
            if int.from_bytes(foot[-4:], "big") != ZAR_MAGIC or int.from_bytes(foot[-8:-4], "big") != ZAR_VERSION:
                return "not a .zar (no ZArchive footer)"
            if int.from_bytes(foot[-16:-8], "big") != size:
                return "cut short (%s of %s)" % (human_size(size), human_size(int.from_bytes(foot[-16:-8], "big")))
            if not full:
                return None
            h = hashlib.sha256()
            f.seek(0)
            left = size - 144
            while left:
                chunk = f.read(min(8 << 20, left))
                if not chunk:
                    return "cut short"
                h.update(chunk)
                left -= len(chunk)
            h.update(foot[:96] + b"\0" * 32 + foot[128:])
            if h.digest() != foot[96:128]:
                return "damaged (its SHA-256 doesn't match)"
    except OSError as e:
        return "can't read it (%s)" % e.strerror
    return None


def xiso_listing(exe, iso):
    """{relative path: size} of the files on an Xbox / Xbox 360 disc image
    (extract-xiso -l), or None if it isn't one."""
    try:
        # surrogateescape: names decode the way os.walk sees them on disk, even when not UTF-8
        p = subprocess.run([exe, "-l", iso], capture_output=True, text=True, errors="surrogateescape", timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    out = {}
    for line in p.stdout.splitlines():
        m = re.match(r"^(/.*[^/]) \((\d+) bytes\)\s*$", line)
        if m:
            out[m.group(1).lstrip("/")] = int(m.group(2))
    return out or None


def install_x360_tools(progress=None, url=None, sha256=None):
    """Download, check and unpack extract-xiso + zarchive into DATA_DIR/tools.
    Returns the (extract-xiso, zarchive) paths."""
    if not zar_supported():
        raise RommError("The Xbox 360 tools aren't built for this CPU (%s)." % _machine())
    url = url or os.environ.get("ROMM_BROWSER_X360_URL") or X360_TOOLS["url"]
    want = (sha256 or os.environ.get("ROMM_BROWSER_X360_SHA256") or X360_TOOLS["sha256"]).lower()
    dest = x360_tools_dir()
    staging = _fetch_tool_archive("Xbox 360 tools", url, want, dest, progress)
    top = os.path.join(staging, "xbox360-tools-" + X360_TOOLS["version"])
    root = top if os.path.isdir(top) else staging
    exe = [os.path.join(root, n) for n in ("extract-xiso", "zarchive")]
    if not all(os.path.isfile(p) for p in exe):
        shutil.rmtree(staging, ignore_errors=True)
        raise RommError("The Xbox 360 tools archive is missing a tool.")
    for p in exe:
        os.chmod(p, 0o755)
    try:
        a = subprocess.run([exe[0], "-h"], capture_output=True, text=True, timeout=30)
        b = subprocess.run([exe[1]], capture_output=True, text=True, timeout=30)
    except OSError as e:
        shutil.rmtree(staging, ignore_errors=True)
        raise RommError("The Xbox 360 tools don't run on this box (%s)." % e.strerror) from None
    if "extract-xiso" not in (a.stdout + a.stderr) or "zarchive" not in (b.stdout + b.stderr).lower():
        shutil.rmtree(staging, ignore_errors=True)
        raise RommError("The Xbox 360 tools don't run on this box.")
    shutil.rmtree(dest, ignore_errors=True)
    os.replace(root, dest)
    shutil.rmtree(staging, ignore_errors=True)
    log("xbox360-tools %s installed (extract-xiso %s, zarchive %s)" % (
        X360_TOOLS["version"], X360_TOOLS["extract_xiso"][:7], X360_TOOLS["zarchive"][:7]))
    return x360_tools()


# ---------------------------------------------------------- leftovers

def chd_worker_busy():
    """True while rb_chd.py holds its lock (a conversion may be writing)."""
    import fcntl
    try:
        fd = os.open(os.path.join(DATA_DIR, "chd.lock"), os.O_RDONLY | os.O_CREAT, 0o644)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        os.close(fd)


# Systems whose games are folders full of data (or that aren't games at all):
# their empty folders can be part of a game — never offered for cleanup.
FOLDER_GAME_SYSTEMS = {"ps3", "psvita", "xbox360", "wiiu", "switch", "windows", "windows_installers",
                       "steam", "flatpak", "ports", "pygame", "lutris", "epic", "heroic", "vita3k"}


def _leftover_folder(sysdir, d):
    """An empty folder RomM-o-Cera could have left: a game folder directly in
    the system folder, or a "(Disc N)" folder a CHD conversion emptied —
    never a folder inside a game's own data (".ps3" etc.)."""
    if os.path.splitext(d)[1].lower() in (".ps3", ".xbox360", ".wiiu", ".pc", ".psvita", ".nsp"):
        return False
    rel = os.path.relpath(d, sysdir).split(os.sep)
    if any(os.path.splitext(x)[1].lower() in (".ps3", ".xbox360", ".wiiu", ".pc", ".psvita") for x in rel):
        return False
    return len(rel) == 1 or bool(DISC_RE.search(os.path.basename(d)))


CONVERT_PARTS = (".rvz.part", ".squashfs.part", ".zar.part") + tuple(e + ".part" for e in Z3DS_EXTS)


def find_leftovers(pending_roms=(), chd_busy=None):
    """Files and folders interrupted downloads/conversions left behind:
    [{"path", "kind", "size"}]. Never a partial download that's still
    queued or paused (it resumes from it), never a CHD being written."""
    busy = chd_worker_busy() if chd_busy is None else chd_busy
    keep = set()
    for rom, system in pending_roms:
        name = safe_name(rom.get("fs_name") or "")
        if name:
            keep.add(os.path.normpath(os.path.join(ROMS_ROOT, system, name)))
    out = []

    def kept(p):
        q = os.path.normpath(p)
        return any(q == k or q == k + ".part" or q.startswith(k + os.sep) for k in keep)
    try:
        systems = sorted(os.listdir(ROMS_ROOT))
    except OSError:
        systems = []
    for system in systems:
        sysdir = os.path.join(ROMS_ROOT, system)
        if not os.path.isdir(sysdir) or os.path.islink(sysdir):
            continue
        for dp, dns, fns in os.walk(sysdir, topdown=False):
            if any(part in AUDIT_SKIP for part in os.path.relpath(dp, sysdir).split(os.sep)):
                continue
            for fn in fns:
                p = os.path.join(dp, fn)
                low = fn.lower()
                if low.endswith((".chd.part", ".tmp.chd")):
                    kind = "Unfinished CHD"
                    if busy:
                        continue
                elif low.endswith(CONVERT_PARTS):
                    kind = "Unfinished conversion"
                    if busy:
                        continue
                elif low.endswith(".part"):
                    kind = "Partial download"
                elif low.endswith(".tmp") and fn.startswith("."):
                    kind = "Temporary file"
                else:
                    continue
                if kept(p):
                    continue
                try:
                    out.append({"path": p, "kind": kind, "size": os.path.getsize(p)})
                except OSError:
                    pass
            if dp != sysdir and not kept(dp) and system not in FOLDER_GAME_SYSTEMS \
                    and _leftover_folder(sysdir, dp):
                try:
                    if not os.listdir(dp):
                        out.append({"path": dp, "kind": "Empty folder", "size": 0})
                except OSError:
                    pass
    tmp = os.path.join(DATA_DIR, "tmp")
    if os.path.isdir(tmp) and not busy:
        for n in sorted(os.listdir(tmp)):
            p = os.path.join(tmp, n)
            out.append({"path": p, "kind": "RomM-o-Cera scratch", "size": path_size(p)})
    return out


def delete_leftovers(items):
    """Delete find_leftovers() entries; returns bytes freed."""
    freed = 0
    for it in items:
        p = it["path"]
        if not (_inside(p, ROMS_ROOT) or _inside(p, DATA_DIR)):
            continue
        try:
            if os.path.isdir(p) and not os.path.islink(p):
                if it["kind"] == "Empty folder":
                    os.rmdir(p)                      # only if still empty
                else:
                    shutil.rmtree(p)
            elif os.path.lexists(p):
                os.remove(p)
            freed += it.get("size") or 0
        except OSError:
            continue
        d = os.path.dirname(p)                          # a game folder only the leftover kept
        while _inside(d, ROMS_ROOT) and os.path.dirname(d) != os.path.normpath(ROMS_ROOT) \
                and os.path.isdir(d) and not os.listdir(d):
            os.rmdir(d)
            d = os.path.dirname(d)
    log("LEFTOVERS: removed %d (%s)" % (len(items), human_size(freed)))
    return freed


def es_notify(msg):
    """Show a popup in EmulationStation (Batocera's ES API). Best effort."""
    import urllib.request
    try:
        req = urllib.request.Request(ES_API + "/notify", data=msg.encode("utf-8"), method="POST",
                                     headers={"Content-Type": "text/plain; charset=utf-8"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status < 300
    except Exception:
        return False


CHD_CURRENT = os.path.join(DATA_DIR, "chd-current.json")


def chd_status(task_name, library=None, rom_id=None):
    """Where a download's CHD conversion stands: "queued", "compressing",
    "done", or None (no task / gave up — the original files stay)."""
    if not task_name:
        return None
    try:
        with open(os.path.join(os.path.dirname(CHD_QUEUE_DIR), os.path.basename(CHD_CURRENT))) as f:
            if json.load(f).get("task") == task_name:
                return "compressing"
    except (OSError, ValueError):
        pass
    if os.path.exists(os.path.join(CHD_QUEUE_DIR, task_name)):
        return "queued"
    if library is not None and rom_id is not None:
        with library.lock:
            library._reload()
            rec = library.installed.get(str(rom_id)) or {}
        if any(p.lower().endswith(".chd") and os.path.exists(p) for p in rec.get("paths", [])):
            return "done"
    return None


DOWNLOAD_HOURS = ["any", "1-7", "0-6", "2-8", "22-6", "23-7"]


def parse_hours(v):
    """(start, end) hours from "S-E", or None for any time."""
    try:
        a, b = (int(x) for x in str(v).split("-"))
        if 0 <= a < 24 and 0 <= b <= 24 and a != b:
            return a, b % 24
    except ValueError:
        pass
    return None


def hours_label(v):
    hb = parse_hours(v)
    if not hb:
        return "Any time"

    def h(x):
        return "midnight" if x == 0 else "noon" if x == 12 else "%d %s" % (x % 12 or 12, "am" if x < 12 else "pm")
    return "%s – %s" % (h(hb[0]), h(hb[1]))


def in_download_hours(cfg, now=None):
    hb = parse_hours((cfg or {}).get("download_hours", "any"))
    if not hb:
        return True
    hr = time.localtime(now).tm_hour
    a, b = hb
    return a <= hr < b if a < b else (hr >= a or hr < b)


def cpu_threads():
    try:
        return len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        return os.cpu_count() or 4


def auto_chd_threads(n=None):
    """chdman runs only while no game is up, at nice 19. Plenty of threads
    (6+): leave 2 for EmulationStation and the system. Few (3-5, e.g. a Pi
    or a small handheld): leave 1 — 2 would halve the work for little gain.
    1-2 threads: 1."""
    n = n or cpu_threads()
    if n >= 6:
        return n - 2
    if n >= 3:
        return n - 1
    return 1


def chd_threads(cfg):
    v = (cfg or {}).get("chd_threads", "auto")
    try:
        v = int(v)
    except (TypeError, ValueError):
        return auto_chd_threads()
    return max(1, min(v, cpu_threads()))


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


def chd_low_memory(cfg):
    """True: compress CDs without FLAC (cdlz,cdzl). Default: on for low-RAM boxes."""
    v = (cfg or {}).get("chd_low_memory", "auto")
    if isinstance(v, bool):
        return v
    return low_ram()


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


def log(msg):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def norm_verify(v):
    """verify_tls -> True | False | CA-bundle path. Only an explicit off value
    disables verification; anything odd (null, "", 0, typos of booleans) is
    treated as ON so a malformed config can't silently drop TLS checks."""
    if v is False:
        return False
    if isinstance(v, str):
        t = v.strip()
        if t.lower() in ("0", "false", "no", "off"):
            return False
        if t.lower() in ("", "1", "true", "yes", "on"):
            return True
        return t                      # a CA-bundle path
    return True


def url_has_userinfo(url):
    return bool(urllib.parse.urlsplit(url or "").username)


def display_url(url):
    """URL for the screen: never shows user:password@ (the token is the login)."""
    u = urllib.parse.urlsplit(url or "")
    if u.username or u.password:
        host = u.hostname or ""
        if u.port:
            host += ":%d" % u.port
        url = urllib.parse.urlunsplit((u.scheme, "***@" + host, u.path, u.query, u.fragment))
    return url


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH) as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    for env, key in ENV_OVERRIDES.items():
        v = os.environ.get(env)
        if v:
            cfg[key] = v
    cfg["verify_tls"] = norm_verify(cfg.get("verify_tls", True))
    cfg["token"] = (cfg.get("token") or "").strip()
    return cfg


def save_config(cfg):
    """Persist the user-editable keys (atomic, 0600: it holds the token)."""
    os.makedirs(DATA_DIR, exist_ok=True)
    keep = {k: cfg[k] for k in DEFAULT_CONFIG if k in cfg}
    tmp = CONFIG_PATH + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(keep, f, indent=1)
    os.replace(tmp, CONFIG_PATH)


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


# ---------------------------------------------------------------- ES systems

def load_es_systems():
    """{system_dir_name: set(extensions)} from Batocera's es_systems.cfg."""
    out = {}
    paths = [os.environ.get("ROMM_BROWSER_ES_SYSTEMS",
                            "/usr/share/emulationstation/es_systems.cfg")]
    cfgdir = "/userdata/system/configs/emulationstation"
    if os.path.isdir(cfgdir):
        paths += sorted(os.path.join(cfgdir, p) for p in os.listdir(cfgdir)
                        if p.startswith("es_systems_") and p.endswith(".cfg"))
    for p in paths:
        try:
            root = ET.parse(p).getroot()
        except (OSError, ET.ParseError):
            continue
        for s in root.findall("system"):
            path = (s.findtext("path") or "").rstrip("/")
            exts = (s.findtext("extension") or "").lower().split()
            if path:
                out[os.path.basename(path)] = set(exts)
    return out


# ----------------------------------------------------------------- API

class RommError(Exception):
    pass


class Api:
    def __init__(self, url, token, verify_tls=True):
        self.base = (url or "").strip().rstrip("/")
        self.s = requests.Session()
        self.s.verify = norm_verify(verify_tls)
        self.s.headers.update({"Authorization": "Bearer " + (token or "").strip(),
                               "User-Agent": UA})

    @classmethod
    def from_config(cls, cfg):
        return cls(cfg.get("url"), cfg.get("token"), cfg.get("verify_tls", True))

    def get(self, path, params=None, timeout=20):
        if not self.base:
            raise RommError("No RomM server set — open Settings")
        if url_has_userinfo(self.base):
            raise RommError("Remove user:password@ from the server URL — the token is the login")
        try:
            r = self.s.get(self.base + path, params=params, timeout=timeout)
        except requests.exceptions.SSLError:
            raise RommError("TLS certificate rejected — set verify_tls in Settings") from None
        except requests.RequestException as e:
            raise RommError("Can't reach RomM (%s)" % e.__class__.__name__) from None
        except Exception as e:      # e.g. ValueError from a bad header: its text can hold the token
            raise RommError("Request failed (%s)" % e.__class__.__name__) from None
        if r.status_code in (401, 403):
            raise RommError("RomM rejected the token (HTTP %d)" % r.status_code)
        if r.status_code != 200:
            raise RommError("RomM error HTTP %d on %s" % (r.status_code, path))
        try:
            return r.json()
        except ValueError:
            raise RommError("Not a RomM API response — check the URL (auth proxy in front?)")

    def platforms(self):
        return self.get("/api/platforms")

    def collections(self):
        return self.get("/api/collections")

    def smart_collections(self):
        return self.get("/api/collections/smart")

    def virtual_collections(self, vtype):
        return self.get("/api/collections/virtual", {"type": vtype})

    def roms(self, platform_ids=None, search=None, collection=None,
             offset=0, limit=60, order_by="name", char_index=False, regions=None,
             verified=None, exclude_tags=None):
        """collection: (param, id) with param one of collection_id,
        smart_collection_id, virtual_collection_id."""
        params = {"offset": offset, "limit": limit,
                  "with_char_index": "true" if char_index else "false",
                  "with_filter_values": "false", "with_rom_id_index": "false",
                  "order_by": order_by, "order_dir": "asc"}
        if platform_ids:
            params["platform_ids"] = list(platform_ids)
        if search:
            params["search_term"] = search
            params["order_by"] = ""   # relevance order
        if collection:
            params[collection[0]] = collection[1]
        if regions:
            params["regions"] = list(regions)
            params["regions_logic"] = "any"
        if verified:
            params["verified"] = "true"
        if exclude_tags:
            params["tags"] = list(exclude_tags)
            params["tags_logic"] = "none"
        return self.get("/api/roms", params)

    def by_hash(self, **hashes):
        """RomM's game with this hash (crc_hash / md5_hash / sha1_hash / ra_hash), or None."""
        params = {k: v for k, v in hashes.items() if v}
        if not params:
            return None
        try:
            r = self.get("/api/roms/by-hash", params)
        except RommError as e:
            if "HTTP 404" in str(e) or "HTTP 422" in str(e):
                return None
            raise
        if isinstance(r, list):
            r = r[0] if r else None
        return r if isinstance(r, dict) and r.get("id") else None

    def rom(self, rom_id):
        return self.get("/api/roms/%d" % rom_id)

    def similar(self, rom_id, limit=30):
        """RomM's similar games, as plain rom dicts (best match first)."""
        out = self.get("/api/roms/%d/similar" % rom_id, {"limit": limit})
        return [x["rom"] for x in out if isinstance(x, dict) and x.get("rom")]

    def all_tags(self):
        """Every filename tag in the library (for the "hide prototypes…" filter)."""
        page = self.get("/api/roms", {"limit": 1, "with_filter_values": "true",
                                      "with_char_index": "false", "with_rom_id_index": "false"})
        return (page.get("filter_values") or {}).get("tags") or []

    def firmware(self, platform_id):
        return self.get("/api/firmware", {"platform_id": platform_id})

    def fetch_cover(self, rom, large=False):
        """Cached cover for a rom (path or None). Blocking. large=True for
        high-DPI screens (the small cover is ~300 px tall)."""
        keys = ("path_cover_large", "path_cover_small") if large else \
            ("path_cover_small", "path_cover_large")
        path = rom.get(keys[0]) or rom.get(keys[1])
        if not path or not path.startswith("/") or path.startswith("//"):
            return None             # only server-relative paths: the session carries the token
        os.makedirs(COVER_CACHE, exist_ok=True)
        dest = os.path.join(COVER_CACHE, "%d%s.img" % (rom["id"], "-L" if large else ""))
        if os.path.exists(dest):
            touch_cached(dest)
            return dest
        try:
            r = self.s.get(self.base + path, timeout=15)
            if r.status_code == 200 and r.content:
                tmp = dest + ".tmp"
                write_png(r.content, tmp)
                os.replace(tmp, dest)
                self._cache_written = getattr(self, "_cache_written", 0) + os.path.getsize(dest)
                if self._cache_written > PRUNE_EVERY and getattr(self, "cache_limit", 0):
                    self._cache_written = 0
                    prune_cache(self.cache_limit)
                return dest
        except (requests.RequestException, OSError):
            pass
        return None


# ----------------------------------------------------------- cover cache (LRU)
# /userdata is mounted noatime, so "last used" is kept in the file's mtime:
# bumped when a cached cover is used (at most every few minutes per file).

CACHE_LIMITS_MB = [256, 512, 1024, 2048, 4096, 0]     # 0 = no limit
PRUNE_EVERY = 64 << 20                                # check after this much new cache
_prune_lock = threading.Lock()


def touch_cached(path, every=600):
    try:
        if time.time() - os.path.getmtime(path) > every:
            os.utime(path, None)
    except OSError:
        pass


def cache_limit_bytes(cfg):
    try:
        mb = int((cfg or {}).get("cache_limit_mb", 1024))
    except (TypeError, ValueError):
        mb = 1024
    return max(0, mb) << 20


def cache_usage(root=None):
    """(bytes, files) in the cover cache."""
    root = root or COVER_CACHE
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
    root = root or COVER_CACHE
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
            log("cache: removed %d least recently used covers (%s) to stay under %s" % (
                n, human_size(freed), human_size(limit)))
        return n, freed
    finally:
        _prune_lock.release()


# Filename tags that mark something other than a finished release. Matched as
# substrings against RomM's tag list, so "Proto 2", "Beta 1", "E3 demo" count.
# Homebrew, Aftermarket, Unlicensed stay visible on purpose.
JUNK_TAG_WORDS = ("proto", "beta", "alpha", "demo", "hack", "pirate", "sample", "kiosk",
                  "debug", "preview", "sampler", "bootleg")


def junk_tags(tags):
    return [t for t in tags if any(w in t.lower() for w in JUNK_TAG_WORDS)
            and "homebrew" not in t.lower()]


# Systems whose downloads are usually archives of disc images: extracting
# roughly doubles them on disk (before any CHD compression).
EXPANDS = {"psx", "ps2", "ps3", "psp", "saturn", "dreamcast", "megacd", "pcenginecd", "neogeocd",
           "3do", "amigacd32", "amigacdtv", "cdi", "pcfx", "gamecube", "wii", "wiiu", "xbox",
           "xbox360", "3ds", "jaguarcd", "fmtowns"}
SPACE_MARGIN = 2 << 30
KEEP_FREE_CHOICES = (0, 5, 10, 20, 50, 100)     # GB, Settings cycles these


def keep_free_bytes(cfg):
    """Space downloads and conversions must leave free (Settings, default 20 GB)."""
    v = (cfg or {}).get("keep_free_gb", DEFAULT_CONFIG["keep_free_gb"])
    try:
        return max(0, int(float(v) * (1 << 30)))
    except (TypeError, ValueError):
        return DEFAULT_CONFIG["keep_free_gb"] << 30


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


def space_needed(rom, system):
    """Rough bytes on disk once downloaded and extracted."""
    size = rom.get("fs_size_bytes") or 0
    return int(size * (2.2 if system in EXPANDS else 1.0))


def queue_forecast(jobs, free, keep, chd=True):
    """Space the unfinished downloads still need, and whether it fits.
    Each job: what's left of its download (done bytes are already on disk);
    plus, for disc games the CHD worker converts, the biggest one's working
    space (original and CHD side by side until the CHD is verified)."""
    need, chd_peak, n = 0, 0, 0
    for j in jobs:
        if not (j.active or j.state == Job.PAUSED):
            continue
        n += 1
        size = space_needed(j.rom, j.system)
        need += max(0, size - (j.done_bytes or 0))
        if chd and j.system in CHD_CD_SYSTEMS | CHD_DVD_SYSTEMS:
            chd_peak = max(chd_peak, size)
    total = need + chd_peak
    return {"jobs": n, "need": total, "free": free, "keep": keep,
            "fits": free - total >= keep, "short": max(0, total + keep - free)}


def fit_in_space(items, free, reserve=SPACE_MARGIN):
    """Greedy, keeping the given order: (that_fit, total_needed_for_them)."""
    fit, used = [], 0
    for rom, system in items:
        need = space_needed(rom, system)
        if used + need + reserve <= free:
            fit.append((rom, system))
            used += need
    return fit, used


# ----------------------------------------------------------- updates

def _vtuple(v):
    """(major, minor, patch) for a RomM-o-Cera version/tag ("v0.6.5", "0.6.5+abc");
    anything else (e.g. the "dolphin-tool-2603a" release) is (0,): never an update."""
    m = re.match(r"^v?(\d+)\.(\d+)(?:\.(\d+))?(?:[+-].*)?$", (v or "").strip())
    return tuple(int(x or 0) for x in m.groups()) if m else (0,)


def newer_release(cfg, force=False):
    """Latest release tag on GitHub if it is newer than VERSION, else None.
    Checked at most once a day (cached); never raises."""
    if not cfg.get("check_updates", True):
        return None
    now = time.time()
    cache = {}
    try:
        with open(UPDATE_CACHE) as f:
            cache = json.load(f)
    except (OSError, ValueError):
        pass
    checked = cache.get("checked", 0)
    if force or now - checked > 86400 or checked > now + 3600:
        try:
            r = requests.get("https://api.github.com/repos/%s/releases/latest" % UPDATE_REPO,
                             timeout=6, headers={"User-Agent": UA,
                                                 "Accept": "application/vnd.github+json"})
            if r.status_code == 200:
                cache = {"checked": now, "latest": r.json().get("tag_name", "")}
            else:
                cache = {"checked": now, "latest": cache.get("latest", "")}
        except Exception:
            cache = {"checked": now, "latest": cache.get("latest", "")}    # offline: try again tomorrow
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(UPDATE_CACHE, "w") as f:
                json.dump(cache, f)
        except OSError:
            pass
    latest = cache.get("latest") or ""
    return latest if _vtuple(latest) > _vtuple(VERSION) else None


# ----------------------------------------------------------- self-update
# Manual, from GitHub releases only: the release zip must match the release's
# SHA256SUMS, contain only rommbrowser/…, compile, and carry the version its tag
# names. It is staged next to the app and swapped in with two renames; the
# previous version is kept for one-step rollback. The RomM token is never sent
# (plain requests, not the RomM session). SHA256SUMS comes from the same
# release, so it guards against corruption, not against a compromised repo.
GITHUB_API = os.environ.get("ROMM_BROWSER_GITHUB_API", "https://api.github.com")
APP_STAGING = os.path.join(DATA_DIR, "app-staging")
APP_BACKUP = os.path.join(DATA_DIR, "app-backup")     # outside roms/: ES must not list it
MAX_RELEASE_BYTES = 40 << 20


class UpdateError(Exception):
    pass


def _gh_get(url, **kw):
    kw.setdefault("timeout", 20)
    headers = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    try:
        r = requests.get(url, headers=headers, **kw)
    except requests.RequestException as e:
        raise UpdateError("Can't reach GitHub (%s)" % e.__class__.__name__) from None
    if r.status_code != 200:
        raise UpdateError("GitHub answered HTTP %d" % r.status_code)
    return r


def release_info(tag=None):
    """{"tag", "zip", "sums"} for the latest (or a given) release."""
    path = "releases/tags/%s" % tag if tag else "releases/latest"
    data = _gh_get("%s/repos/%s/%s" % (GITHUB_API, UPDATE_REPO, path)).json()
    assets = {a.get("name"): a.get("browser_download_url") for a in data.get("assets") or []}
    if not assets.get("romm-o-cera.zip") or not assets.get("SHA256SUMS"):
        raise UpdateError("Release %s has no romm-o-cera.zip / SHA256SUMS" % data.get("tag_name"))
    return {"tag": data.get("tag_name") or "", "zip": assets["romm-o-cera.zip"],
            "sums": assets["SHA256SUMS"]}


def _version_in(app_dir):
    try:
        with open(os.path.join(app_dir, "rommlib.py")) as f:
            m = re.search(r'^VERSION = "([^"]+)"', f.read(), re.M)
        return m.group(1) if m else None
    except OSError:
        return None


def stage_release(info, staging=APP_STAGING):
    """Download, verify and unpack a release into staging; returns the staged
    app folder. Raises UpdateError and leaves nothing behind on any problem."""
    import hashlib
    import io
    sums = _gh_get(info["sums"]).text
    want = None
    for ln in sums.splitlines():
        parts = ln.split()
        if len(parts) == 2 and parts[1].lstrip("*") == "romm-o-cera.zip":
            want = parts[0].lower()
    if not want:
        raise UpdateError("SHA256SUMS doesn't list romm-o-cera.zip")
    r = _gh_get(info["zip"], stream=True)
    buf = io.BytesIO()
    for chunk in r.iter_content(1 << 16):
        buf.write(chunk)
        if buf.tell() > MAX_RELEASE_BYTES:
            raise UpdateError("Release zip is unexpectedly large")
    data = buf.getvalue()
    if hashlib.sha256(data).hexdigest() != want:
        raise UpdateError("Checksum mismatch — download corrupted, nothing changed")
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for n in z.namelist():
                parts = n.replace("\\", "/").split("/")
                if n.startswith("/") or ".." in parts or parts[0] != "rommbrowser":
                    raise UpdateError("Unexpected path in release: %s" % n)
            z.extractall(staging)
        app = os.path.join(staging, "rommbrowser")
        for need in ("rommbrowser.pygame", "rommlib.py"):
            if not os.path.isfile(os.path.join(app, need)):
                raise UpdateError("Release is missing %s" % need)
        for fn in os.listdir(app):
            if fn.endswith((".py", ".pygame")):
                with open(os.path.join(app, fn), encoding="utf-8") as f:
                    compile(f.read(), fn, "exec")
        ver = _version_in(app)
        if not ver or _vtuple(ver) != _vtuple(info["tag"]):
            raise UpdateError("Release %s contains version %s" % (info["tag"], ver))
        return app
    except UpdateError:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    except (zipfile.BadZipFile, SyntaxError, OSError, UnicodeDecodeError) as e:
        shutil.rmtree(staging, ignore_errors=True)
        raise UpdateError("Release failed checks (%s)" % e.__class__.__name__) from None


def _move(src, dst):
    try:
        os.rename(src, dst)                 # atomic on the same filesystem
    except OSError:
        shutil.move(src, dst)


def install_staged(staged_app, app_dir=APP_DIR, backup=APP_BACKUP):
    """Swap the staged app in; the current one becomes the backup."""
    shutil.rmtree(backup, ignore_errors=True)
    _move(app_dir, backup)
    try:
        _move(staged_app, app_dir)
    except Exception:
        _move(backup, app_dir)              # put the old version back
        raise
    shutil.rmtree(os.path.dirname(staged_app), ignore_errors=True)
    log("UPDATE installed %s (previous %s kept for rollback)" % (_version_in(app_dir), _version_in(backup)))
    return _version_in(app_dir)


def backup_version(backup=APP_BACKUP):
    return _version_in(backup) if os.path.isdir(backup) else None


def rollback(app_dir=APP_DIR, backup=APP_BACKUP):
    """Swap app and backup (so a rollback can itself be undone)."""
    if not os.path.isdir(backup):
        raise UpdateError("No previous version to go back to")
    tmp = backup + ".swap"
    shutil.rmtree(tmp, ignore_errors=True)
    _move(app_dir, tmp)
    try:
        _move(backup, app_dir)
    except Exception:
        _move(tmp, app_dir)                  # put the current version back
        raise UpdateError("Couldn't swap versions; nothing changed")
    _move(tmp, backup)
    log("ROLLBACK now %s (backup %s)" % (_version_in(app_dir), _version_in(backup)))
    return _version_in(app_dir)


# ------------------------------------------------------- installed tracking

class Library:
    """What's already on the box: our own installs (installed.json) plus a
    filename-stem scan of roms/<sys>, so games pushed earlier (same RomM
    filenames) also show as installed."""

    def __init__(self):
        self.lock = threading.RLock()
        self.installed = {}
        self._stems = {}
        self._mtime = None
        self._reload()

    # installed.json is shared with rb_chd.py / rb_dl.py (other processes):
    # every change is a read-modify-write under an flock, and reads pick up
    # changes made elsewhere.
    def _reload(self):
        try:
            mt = os.stat(STATE_PATH).st_mtime_ns
        except FileNotFoundError:
            if self._mtime is not None:          # it was deleted (a reset): don't write old records back
                self.installed, self._mtime = {}, None
            return
        except OSError:
            return
        if mt == self._mtime:
            return
        try:
            with open(STATE_PATH) as f:
                data = json.load(f)
            if isinstance(data, dict):
                self.installed, self._mtime = data, mt
        except (OSError, ValueError):
            pass

    def update(self, fn):
        """Apply fn(installed) to the freshest installed.json and write it back."""
        import fcntl
        with self.lock:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(STATE_PATH + ".lock", "w") as lk:
                fcntl.flock(lk, fcntl.LOCK_EX)
                self._mtime = None
                self._reload()
                out = fn(self.installed)
                tmp = STATE_PATH + ".tmp.%d" % os.getpid()
                with open(tmp, "w") as f:
                    json.dump(self.installed, f, indent=1)
                os.replace(tmp, STATE_PATH)
                self._mtime = os.stat(STATE_PATH).st_mtime_ns
            return out

    def _scan(self, system):
        stems = set()
        d = os.path.join(ROMS_ROOT, system)
        try:
            for n in os.listdir(d):
                stems.add(stem_of(n).lower())
                stems.add(n.lower())
        except OSError:
            pass
        self._stems[system] = stems
        return stems

    def is_installed(self, rom, system):
        with self.lock:
            self._reload()
            rec = self.installed.get(str(rom["id"]))
            if rec and any(os.path.exists(p) for p in rec.get("paths", [])):
                return True
            stems = self._stems.get(system)
            if stems is None:
                stems = self._scan(system)
        fs = (rom.get("fs_name") or "").lower()
        return fs in stems or (rom.get("fs_name_no_ext") or "").lower() in stems

    def mark(self, rom, system, paths, group=None, meta=None):
        rec = {"system": system, "paths": list(paths), "name": rom.get("name"),
               "fs_name": rom.get("fs_name"), "when": time.strftime("%Y-%m-%d %H:%M:%S")}
        if group:
            rec["group"] = group
        if meta:
            rec["meta"] = meta

        def put(inst):
            inst[str(rom["id"])] = rec
        self.update(put)
        self._stems.pop(system, None)

    def group_for(self, rom_id):
        with self.lock:
            self._reload()
        rec = self.installed.get(str(rom_id)) or {}
        return rec.get("group")

    def variants(self, rom, system):
        """Other versions of this game on the box (different region/revision),
        by filename: same title once every (...)/[...] tag is removed, but not
        the rom itself nor another disc of the same set."""
        mine = (rom.get("fs_name_no_ext") or "").lower()
        title = strip_tags(mine)
        dset = disc_info(rom.get("fs_name_no_ext") or "")
        out = []
        try:
            names = sorted(os.listdir(os.path.join(ROMS_ROOT, system)))
        except OSError:
            return out
        for n in names:
            stem = stem_of(n) if not os.path.isdir(os.path.join(ROMS_ROOT, system, n)) else n
            low = stem.lower()
            if low == mine or n.lower().endswith((".m3u", ".xml", ".txt")) or strip_tags(low) != title:
                continue
            other = disc_info(stem)
            if dset and other and other[0].lower() == dset[0].lower():
                continue                               # another disc of the same set
            out.append(n)
        return out

    def adopt(self, rom, system, group):
        """A disc that is already on the box joins a set being downloaded:
        record it (keeping an existing record's details) with the group."""
        paths = [p for p in self.paths_for(rom, system) if not p.lower().endswith(".m3u")]
        if not paths:
            return False

        def put(inst):
            rec = inst.get(str(rom["id"])) or {
                "system": system, "name": rom.get("name"), "fs_name": rom.get("fs_name"),
                "when": time.strftime("%Y-%m-%d %H:%M:%S")}
            rec["paths"] = list(dict.fromkeys([p for p in rec.get("paths", []) if os.path.exists(p)] + paths))
            rec["group"] = group
            rec.setdefault("meta", gamelist_entry(rom, system, paths))
            inst[str(rom["id"])] = rec
        self.update(put)
        self._stems.pop(system, None)
        return True

    def rename_paths(self, rename, extra=()):
        """A CHD conversion replaced files: point records at the new ones
        (keeping their other paths, e.g. a set's .m3u)."""
        def fix(inst):
            for rec in inst.values():
                ps = rec.get("paths", [])
                if not any(p in rename for p in ps):
                    continue
                out = []
                for p in ps:
                    for q in ([rename[p]] + list(extra)) if p in rename else [p]:
                        if q not in out:
                            out.append(q)
                rec["paths"] = out
        self.update(fix)

    def rescan(self, system):
        with self.lock:
            self._stems.pop(system, None)

    def paths_for(self, rom, system):
        """Files/folders that make up this rom on the box: our own install
        record, else a filename match in roms/<system>; an .m3u brings its
        discs. Only ever paths inside roms/<system>."""
        sysdir = os.path.join(ROMS_ROOT, system)
        with self.lock:
            self._reload()
        rec = self.installed.get(str(rom["id"])) or {}
        out = [p for p in rec.get("paths", []) if os.path.exists(p)]
        if not out:
            names = {(rom.get("fs_name") or "").lower(), (rom.get("fs_name_no_ext") or "").lower()}
            names.discard("")
            try:
                for n in sorted(os.listdir(sysdir)):
                    if n.lower() in names or os.path.splitext(n)[0].lower() in names:
                        out.append(os.path.join(sysdir, n))
            except OSError:
                pass
        for p in list(out):
            if p.lower().endswith(".m3u") and os.path.isfile(p):
                with open(p, errors="replace") as f:
                    for ln in f:
                        q = os.path.normpath(os.path.join(os.path.dirname(p), ln.strip()))
                        if ln.strip() and os.path.exists(q) and q not in out:
                            out.append(q)
        return [p for p in out if _inside(p, sysdir)]

    def uninstall_group(self, group, system):
        """Delete every disc of a set (and its .m3u)."""
        freed, paths = 0, []
        with self.lock:
            self._reload()
        for rid in group.get("rom_ids", []):
            rec = self.installed.get(str(rid)) or {}
            p, f = self.uninstall({"id": rid, "name": rec.get("name"), "fs_name": rec.get("fs_name"),
                                   "fs_name_no_ext": os.path.splitext(rec.get("fs_name") or "")[0]}, system)
            paths += p
            freed += f
        return paths, freed

    def uninstall(self, rom, system):
        """Delete the rom's files (never saves/states, never outside
        roms/<system>) and queue its gamelist entries for removal."""
        sysdir = os.path.join(ROMS_ROOT, system)
        paths = self.paths_for(rom, system)
        freed = 0
        for p in paths:
            if not _inside(p, sysdir):
                continue
            freed += path_size(p)
            if os.path.isdir(p) and not os.path.islink(p):
                shutil.rmtree(p)
            elif os.path.lexists(p):
                os.remove(p)
        self.update(lambda inst: inst.pop(str(rom["id"]), None))
        self._stems.pop(system, None)
        if paths:
            append_gamelist({"system": system, "remove": paths})
        log("UNINSTALL %s: %s (%s)" % (rom.get("name"), paths, human_size(freed)))
        return paths, freed


def _inside(path, root):
    """True if path is strictly inside root (resolving symlinks)."""
    r = os.path.realpath(root)
    p = os.path.realpath(path)
    return p != r and p.startswith(r + os.sep)


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


# ----------------------------------------------------------- multi-disc

DISC_RE = re.compile(r"\s*\((?:Disc|Disk|CD|DVD)\s*([0-9]{1,2}|[A-H])(?:\s*of\s*[0-9]{1,2})?\)", re.I)


def rom_is_folder(detail, files=None):
    """True when a RomM game is a folder (cue/bin set, GDI, a folder holding
    one file, per-disc subfolders...): some file of it lives at another path
    than the game itself. RomM's has_multiple_files / has_nested_single_file
    only count files directly in the folder, so a game whose files all sit in
    subfolders (Game/Game (Disc 1)/...) reports neither."""
    if detail.get("has_multiple_files") or detail.get("has_nested_single_file"):
        return True
    files = files if files is not None else (detail.get("files") or [])
    gp = (detail.get("full_path") or "").rstrip("/")
    return bool(files) and bool(gp) and any((f.get("full_path") or "").rstrip("/") != gp for f in files)


def disc_info(name):
    """("Base Name (USA)", n) for "Base Name (USA) (Disc 2)"; None when not a disc."""
    m = DISC_RE.search(name or "")
    if not m:
        return None
    v = m.group(1)
    n = int(v) if v.isdigit() else ord(v.upper()) - ord("A") + 1
    base = (name[:m.start()] + name[m.end():]).strip()
    return re.sub(r"\s{2,}", " ", base), n


def strip_tags(name):
    return re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", name or "").strip().lower()


def find_disc_set(api, rom):
    """Every disc of the set `rom` belongs to, in disc order: [(n, rom)].
    Candidates come from RomM's siblings and a title search on the same
    platform; only names that match exactly apart from the disc tag count
    (so other regions, revisions and unrelated "siblings" are excluded).
    Returns (discs, missing_numbers) or (None, None) if `rom` isn't a disc."""
    me = disc_info(rom.get("fs_name_no_ext") or "")
    if not me:
        return None, None
    base = me[0].lower()
    pid = rom.get("platform_id")
    cands = {rom["id"]: rom}
    title = strip_tags(me[0]) or me[0]
    try:
        for r in api.roms(platform_ids=[pid], search=title, limit=250).get("items", []):
            cands.setdefault(r["id"], r)
    except RommError:
        pass
    try:
        for sib in (api.rom(rom["id"]).get("sibling_roms") or []):
            if sib.get("id") not in cands:
                d = disc_info(sib.get("fs_name_no_ext") or "")
                if d and d[0].lower() == base:
                    cands[sib["id"]] = api.rom(sib["id"])
    except RommError:
        pass
    # The same set is sometimes in RomM twice ("Disc 01" and "Disc 1"): prefer
    # the copies tagged in the same style as the disc picked, never mix them.
    def tag_style(name):
        m = DISC_RE.search(name)
        g = m.group(0)                     # "(Disc 01)" vs "(Disc 1)": the digit count is the style
        return (g[:m.start(1) - m.start(0)] + "#" * len(m.group(1)) + g[m.end(1) - m.start(0):]).strip().lower()
    style = tag_style(rom.get("fs_name_no_ext") or "")
    discs = {}
    for r in sorted(cands.values(), key=lambda r: r["id"]):
        name = r.get("fs_name_no_ext") or ""
        d = disc_info(name)
        if not d or d[0].lower() != base or r.get("platform_id") != pid:
            continue
        same_style = tag_style(name) == style
        if d[1] not in discs or (same_style and not discs[d[1]][1]):
            discs[d[1]] = (r, same_style)
    discs = {n: v[0] for n, v in discs.items()}
    discs[me[1]] = rom                                # always the one actually picked
    ordered = sorted(discs.items())
    first = 0 if 0 in discs else 1                    # some sets have a "Disc 0" (e.g. Enemy Zero)
    missing = [n for n in range(first, max(discs) + 1) if n not in discs]
    return ordered, missing


def disc_group(platform_slug, discs, title=None):
    """The group record a disc set's jobs share."""
    base = disc_info(discs[0][1].get("fs_name_no_ext") or "")[0]
    return {"id": "%s:%s" % (platform_slug, base.lower()), "base": base,
            "title": title or re.sub(r"\s*[\(\[].*$", "", base) or base,
            "rom_ids": [r["id"] for _, r in discs]}


PRIMARY_ORDER = [".m3u", ".chd", ".gdi", ".cue", ".cdi", ".iso", ".rvz"]


def primary_path(system, paths, es):
    """The one file ES lists for a rom made of `paths` (files or folders)."""
    exts = es.get(system)
    cands = []
    for p in paths:
        if os.path.isdir(p):
            for dp, _, fns in os.walk(p):
                cands += [os.path.join(dp, f) for f in fns]
        elif os.path.exists(p):
            cands.append(p)
    if exts is not None:
        cands = [c for c in cands if os.path.splitext(c)[1].lower() in exts]
    cands = [c for c in cands if not c.lower().endswith(".m3u")] or cands
    if not cands:
        return None

    def key(c):
        e = os.path.splitext(c)[1].lower()
        return (PRIMARY_ORDER.index(e) if e in PRIMARY_ORDER else 99, len(c))
    return sorted(cands, key=key)[0]


# ----------------------------------------------------------- audit

AUDIT_SKIP = {"images", "videos", "manuals", "media", "downloaded_images", "downloaded_videos"}


def sheet_sources(sheet):
    """Track files a .cue/.gdi references (paths)."""
    d = os.path.dirname(sheet)
    out = []
    try:
        with open(sheet, errors="replace") as f:
            text = f.read()
    except OSError:
        return out
    if sheet.lower().endswith(".cue"):
        out = [os.path.join(d, m.group(1)) for m in re.finditer(r'FILE\s+"([^"]+)"', text, re.I)]
    else:
        for line in text.splitlines()[1:]:
            m = re.match(r'\s*\d+\s+\d+\s+\d+\s+\d+\s+("([^"]+)"|(\S+))', line)
            if m:
                out.append(os.path.join(d, m.group(2) or m.group(3)))
    return out


def _sheets_in(path):
    if os.path.isfile(path):
        return [path] if path.lower().endswith((".cue", ".gdi")) else []
    out = []
    for dp, dns, fns in os.walk(path):
        dns[:] = [x for x in dns if x not in AUDIT_SKIP]
        out += [os.path.join(dp, f) for f in fns if f.lower().endswith((".cue", ".gdi"))]
    return out


def es_hashes(system):
    """{path: RetroAchievements hash} EmulationStation computed for a system's
    games (hidden ones too). Empty if ES can't be asked."""
    try:
        games = _es_json("/systems/%s/games" % urllib.parse.quote(system)) or []
    except Exception:
        return {}
    return {g["path"]: g["cheevosHash"] for g in games if g.get("path") and g.get("cheevosHash")}


def es_counts():
    """{system: games EmulationStation lists}; empty if ES can't be asked."""
    try:
        return {x["name"]: int(x.get("totalGames") or 0) for x in _es_json("/systems", timeout=3) or []
                if x.get("name") and x.get("collection") != "true"}
    except Exception:
        return {}


def disc_layout(detail, system, cfg, accepts=None):
    """What a download of this game is and becomes, from RomM's full record
    (files and archive members): {"label": "CUE/BIN, 3 tracks", "discs": n,
    "unpacked": bytes or None, "estimated": unpacked is a guess,
    "convert": "CHD" | "RVZ" | "squashfs" | "ZCCI" | "ZAR" | None}. None for a
    game that isn't a disc image (carts), or when RomM listed no files."""
    files = (detail or {}).get("files") or []
    if not files:
        return None
    members, sizes_known, archived = [], True, False
    for f in files:
        name = f.get("file_name") or ""
        if os.path.splitext(name)[1].lower() in ARCHIVE_EXTS and system not in ZIP_NATIVE:
            archived = True
            inner = f.get("archive_members") or []
            if not inner:
                sizes_known = False
            members += [(m.get("name") or "", m.get("size")) for m in inner]
        else:
            members.append((name, f.get("file_size_bytes")))
    exts = [os.path.splitext(n)[1].lower() for n, _ in members]
    if not any(e in DISC_EXTS for e in exts):
        if archived and not sizes_known and system in EXPANDS:     # an archive RomM didn't list
            return {"label": "", "discs": 0, "unpacked": space_needed(detail, system), "estimated": True,
                    "convert": None}
        return None
    count = exts.count
    kinds = []
    if count(".cue"):
        tracks = count(".bin")
        kinds.append("CUE/BIN" + (", %d tracks" % tracks if tracks > count(".cue") else ""))
    if count(".gdi"):
        kinds.append("GDI, %d tracks" % (len(exts) - count(".gdi")) if len(exts) > count(".gdi") + 1 else "GDI")
    for e in (".iso", ".chd", ".rvz", ".wbfs", ".gcz", ".ciso", ".cso", ".pbp", ".ccd", ".cdi", ".3ds", ".cci",
              ".cxi", ".cia"):
        if count(e):
            kinds.append(e[1:].upper())
    discs = count(".cue") + count(".gdi") + count(".iso") + count(".chd") + count(".rvz") + count(".wbfs") \
        + count(".cso") + count(".cdi")
    unpacked, estimated = None, False
    if archived and sizes_known and all(s for _, s in members):
        unpacked = sum(s for _, s in members)        # loose files are in members too
    elif archived:
        unpacked, estimated = space_needed(detail, system), True     # RomM didn't list sizes
    ok = accepts or (lambda s, e: True)
    convert = None
    if system in RVZ_SYSTEMS:
        if rvz_enabled(cfg) and ok(system, ".rvz") and any(e in RVZ_SOURCES for e in exts):
            convert = "RVZ"
    elif system in SQUASH_SYSTEMS:
        if squashfs_enabled(cfg, system) and ok(system, ".squashfs") and ".iso" in exts:
            convert = "squashfs"
    elif system in ZAR_SYSTEMS:
        if zar_enabled(cfg, system) and ok(system, ".zar") and ".iso" in exts:
            convert = "ZAR"
    elif system in Z3DS_SYSTEMS:
        if z3ds_enabled(cfg, system) and any(e in Z3DS_SOURCES and ok(system, Z3DS_SOURCES[e]) for e in exts):
            convert = "ZCCI"
    elif (cfg or {}).get("chd", True) and ok(system, ".chd"):
        if system in CHD_CD_SYSTEMS and any(e in (".cue", ".gdi") for e in exts):
            convert = "CHD"
        elif system in CHD_DVD_SYSTEMS and ".iso" in exts:
            convert = "CHD"
    return {"label": ", ".join(kinds), "discs": discs, "unpacked": unpacked, "estimated": estimated,
            "convert": convert}


TAG_RE = re.compile(r"\(([^)]*)\)")
NOT_THE_GAME = ("demo", "beta", "proto", "sample", "kiosk", "unl", "pirate", "hack")


def _tags(name):
    return {t.strip().lower() for g in TAG_RE.findall(name) for t in g.split(",")}


def find_in_romm(api, system, platform_ids, path, ra_hash=None):
    """RomM's copy of a game on the box whose file name RomM doesn't use:
    (rom, how) with how "ra" (same RetroAchievements hash: the same game data)
    or "name" (the one RomM title with the same name, disc and region — another
    revision or region spelling), else (None, None)."""
    if ra_hash and ra_hash.strip("0") and re.fullmatch(r"[0-9a-fA-F]{32}", ra_hash):
        # games without RetroAchievements: no hash in ES, or RomM has none -> the title match below
        try:
            r = api.by_hash(ra_hash=ra_hash.lower())
        except RommError:
            r = None
        if r and (not platform_ids or r.get("platform_id") in platform_ids):
            return r, "ra"
    stem = os.path.splitext(os.path.basename(path))[0]
    title = strip_tags(stem).lower()
    disc = disc_info(stem)
    want_regions = _tags(stem) & {"usa", "europe", "japan", "world", "canada", "australia", "korea",
                                  "asia", "germany", "france", "spain", "italy", "brazil"}
    try:
        items = api.roms(platform_ids=platform_ids, search=strip_tags(stem), limit=100).get("items", [])
    except RommError:
        return None, None
    cands = []
    for r in items:
        n = r.get("fs_name_no_ext") or ""
        tags = _tags(n)
        if strip_tags(n).lower() != title or any(any(b in t for b in NOT_THE_GAME) for t in tags):
            continue
        d = disc_info(n)
        if (d[1] if d else None) != (disc[1] if disc else None):
            continue
        if want_regions and not (want_regions & tags):
            continue
        cands.append(r)
    if len(cands) == 1:
        return cands[0], "name"
    if cands:                                   # several revisions: the latest one
        revs = sorted(cands, key=lambda r: [int(x) for x in re.findall(r"\(Rev (\d+)\)",
                                                                       r.get("fs_name_no_ext") or "")] or [0])
        top = revs[-1]
        if sum(1 for r in cands if _tags(r.get("fs_name_no_ext") or "") == _tags(top.get("fs_name_no_ext") or "")) == 1:
            return top, "name"
    return None, None


def _pct(cut):
    have, need = cut[0], cut[1]
    return (round(100 * (1 - have / need)), "{:,}".format(have), "{:,}".format(need))


def _chd_frames(path, chdman):
    try:
        info = subprocess.run([chdman, "info", "-i", path], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    if "Tag='DVD" in info:
        m = re.search(r"Logical size:\s*([\d,]+)", info)
        return ("dvd", int(m.group(1).replace(",", "")) // 2048) if m else None
    f = re.findall(r"FRAMES:(\d+)", info)
    return ("cd", sum(int(x) for x in f)) if f else None


def _chd_vs_romm(api, chd, rom, chdman):
    """A CHD converted on the box against RomM's original (zip of cue/bin, or
    iso): None if it can't be compared; False if complete; else (have, need)."""
    try:
        detail = api.rom(rom["id"])
    except RommError:
        return None
    sizes = {}
    for f in detail.get("files") or []:
        if f.get("category") not in (None, "game"):
            continue
        for m in f.get("archive_members") or [{"name": f.get("file_name"), "size": f.get("file_size_bytes")}]:
            ext = os.path.splitext((m.get("name") or "").lower())[1]
            if m.get("size"):
                sizes.setdefault(ext, 0)
                sizes[ext] += m["size"]
    got = _chd_frames(chd, chdman)
    if not got:
        return None
    kind, have = got
    if kind == "cd" and sizes.get(".bin"):
        need = sizes[".bin"] // 2352
    elif kind == "dvd" and sizes.get(".iso"):
        need = sizes[".iso"] // 2048
    else:
        return None
    return (have, need) if have + 300 < need else False


def _images_in(path):
    """Disc images (.cue, .iso, .chd) at or under path, skipping media folders."""
    if os.path.isfile(path):
        return [path] if path.lower().endswith((".cue", ".iso", ".chd")) else []
    out = []
    for dp, dns, fns in os.walk(path):
        dns[:] = [x for x in dns if x not in AUDIT_SKIP]
        out += [os.path.join(dp, f) for f in fns if f.lower().endswith((".cue", ".iso", ".chd"))]
    return out


def _slim(rom):
    return {k: rom.get(k) for k in ("id", "name", "fs_name", "fs_name_no_ext", "platform_id")}


def audit(api, platforms_by_system, deep=False, progress=None, cancel=None, chdman="chdman"):
    """Check games on the box against RomM. Quick: missing files and sizes.
    deep: also checksums and `chdman verify`. Returns (findings, stats).
    A finding: {"system", "path", "problem", "severity", "rom" (or None)};
    severity "damaged" (unplayable/corrupt) or "differs" (not RomM's copy:
    another dump, a trimmed image… — may well play)."""
    findings, stats = [], {"checked": 0, "matched": 0, "unmatched": 0}

    def add(system, path, problem, rom=None, severity="damaged"):
        findings.append({"system": system, "path": path, "problem": problem,
                         "severity": severity, "rom": _slim(rom) if rom else None})

    def identify(system):
        """Damaged games RomM doesn't list under their file name: find RomM's copy
        by RetroAchievements hash, or the one matching title/disc/region."""
        hashes = None
        for f in findings:
            if f["system"] != system or f["rom"] or f["severity"] != "damaged":
                continue
            if hashes is None:
                hashes = es_hashes(system)
            rom, how = find_in_romm(api, system, platforms_by_system.get(system) or [], f["path"],
                                    hashes.get(f["path"]))
            if rom:
                f["rom"], f["via"] = dict(_slim(rom), fs_name_no_ext=rom.get("fs_name_no_ext")), how
                f["problem"] += " · RomM has it as %s%s" % (rom.get("fs_name") or rom.get("name"),
                                                           " (same RetroAchievements hash)" if how == "ra" else "")

    def members_of(files):
        return {m["name"].lower(): m for f in files for m in (f.get("archive_members") or []) if m.get("name")}

    def check_z3ds(system, path, files, rom):
        """A .zcci packed here: RomM has the .3ds it came from (loose or in a zip)."""
        stem = os.path.splitext(os.path.basename(path))[0].lower()
        orig = None
        for f in files:
            for name, size, crc in [(f.get("file_name"), f.get("file_size_bytes"), f.get("crc_hash"))] + \
                    [(m.get("name"), m.get("size"), m.get("crc_hash")) for m in (f.get("archive_members") or [])]:
                base, ext = os.path.splitext(os.path.basename(name or "").lower())
                if base == stem and ext in Z3DS_SOURCES:
                    orig = (size, (crc or "").lower())
        hdr = z3ds_header(path)
        if hdr is None:
            add(system, path, "Not a readable .zcci (damaged header)", rom)
        elif not hdr["complete"]:
            add(system, path, "Compressed file cut short", rom)
        elif orig and orig[0] and hdr["size"] != orig[0]:
            add(system, path, "Packed from a different file than RomM's (%s vs %s)" % (
                human_size(hdr["size"]), human_size(orig[0])), rom, "differs")
        elif deep:
            try:
                got, _ = z3ds_verify(path)
            except (Z3dsError, OSError) as e:
                add(system, path, "Compressed file damaged: %s" % e, rom)
                return
            if orig and orig[1] and got != orig[1]:
                add(system, path, "Checksum doesn't match RomM's copy", rom, "differs")

    def check_zip(system, path, files, rom):
        """A zip kept as-is: judge the ROM inside, not the container (the same
        ROM zips to different sizes)."""
        try:
            with zipfile.ZipFile(path) as z:
                local = {i.filename.lower(): i for i in z.infolist() if not i.is_dir()}
                if deep and z.testzip() is not None:
                    add(system, path, "Damaged zip (a file inside fails its CRC)", rom)
                    return
        except (zipfile.BadZipFile, OSError):
            add(system, path, "Damaged zip (can't be opened)", rom)
            return
        want = members_of(files)
        if not want:                                # RomM didn't record the contents
            if files and files[0].get("file_size_bytes") and \
                    os.path.getsize(path) != files[0]["file_size_bytes"]:
                add(system, path, "Differs from RomM's copy (size)", rom, "differs")
            return
        by_base = {os.path.basename(k): v for k, v in local.items()}
        for name, m in want.items():
            i = local.get(name) or by_base.get(os.path.basename(name))
            if i is None:
                add(system, path, "Missing inside the zip: %s" % m["name"], rom)
            elif m.get("crc_hash") and ("%08x" % i.CRC) != m["crc_hash"].lower().zfill(8):
                add(system, path, "Different ROM than RomM's copy (%s)" % m["name"], rom, "differs")

    for system in sorted(platforms_by_system):
        sysdir = os.path.join(ROMS_ROOT, system)
        if not os.path.isdir(sysdir):
            continue
        index = {}
        for pid in platforms_by_system[system]:
            off = 0
            while True:
                page = api.roms(platform_ids=[pid], offset=off, limit=500)
                for r in page.get("items", []):
                    index.setdefault((r.get("fs_name_no_ext") or "").lower(), r)
                    index.setdefault((r.get("fs_name") or "").lower(), r)
                off += len(page.get("items", []))
                if not page.get("items") or off >= page.get("total", 0):
                    break
        for n in sorted(os.listdir(sysdir)):
            if cancel is not None and cancel.is_set():
                return findings, stats
            path = os.path.join(sysdir, n)
            low = n.lower()
            if n.startswith(".") or n in AUDIT_SKIP or low.endswith((".xml", ".txt", ".m3u", ".part", ".tmp.chd", ".chd.part")):
                continue
            if progress:
                progress(system, n)
            stats["checked"] += 1
            # 1) disc sheets must find every track (needs no RomM at all)
            for sheet in _sheets_in(path):
                for ref in sheet_sources(sheet):
                    if not os.path.exists(ref):
                        add(system, sheet, "Missing track file: %s" % os.path.basename(ref),
                            index.get(os.path.splitext(n)[0].lower()) or index.get(low))
            # 1b) disc images cut short (an interrupted copy): the game can't
            # load what's past the end. Converted CHDs are compared with RomM's
            # original exactly (below); here, the disc's own filesystem says so
            for img in _images_in(path):
                st = os.path.splitext(os.path.basename(img))[0].lower()
                irom = index.get(st) or index.get(os.path.splitext(n)[0].lower()) or index.get(low)
                if img.lower().endswith(".chd") and irom and \
                        not (irom.get("fs_name") or "").lower().endswith(".chd"):
                    exact = _chd_vs_romm(api, img, irom, chdman)
                    if exact is not None:
                        if exact:
                            add(system, img, "Incomplete disc image: %d%% missing (%s of %s sectors in RomM's "
                                "copy)" % _pct(exact), irom)
                        continue
                cut = disc_incomplete(img, chdman)
                if cut and cut[2]:
                    add(system, img, "Incomplete disc image: %d%% missing (%s of %s sectors)" % _pct(cut), irom)
                elif cut:
                    add(system, img, "May be cut short: a file on the disc reaches past its end (%s of %s "
                        "sectors; some discs list dummy files)" % ("{:,}".format(cut[0]), "{:,}".format(cut[1])),
                        irom, "differs")
            # 2) against RomM's record of the game
            stem = n if os.path.isdir(path) else stem_of(n)
            rom = index.get(low) or index.get(stem.lower())
            if system in Z3DS_SYSTEMS and os.path.isfile(path) and z3ds_target(path):
                try:
                    z3ds_info(path)
                except Z3dsError as e:          # whether RomM lists it or not: it won't run
                    if "encrypted" in str(e):
                        add(system, path, "Encrypted dump: Azahar can't run it (needs a decrypted copy)",
                            rom, "differs")
                except OSError:
                    pass
            if not rom:
                stats["unmatched"] += 1
                continue
            stats["matched"] += 1
            if os.path.isfile(path) and low.endswith(".chd") and \
                    not (rom.get("fs_name") or "").lower().endswith(".chd"):
                if deep:                                   # converted here: chdman checks itself
                    p = subprocess.run(["nice", "-n", "19", chdman, "verify", "-i", path],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
                    if p.returncode != 0:
                        add(system, path, "CHD failed chdman verify", rom)
                continue
            try:
                detail = api.rom(rom["id"])
            except RommError as e:
                add(system, path, "Couldn't fetch RomM record (%s)" % e, None)
                continue
            files = [f for f in (detail.get("files") or []) if f.get("category") in (None, "game")]
            if os.path.isfile(path) and low.endswith(Z3DS_EXTS):
                check_z3ds(system, path, files, rom)
                continue
            if os.path.isfile(path) and low.endswith(".zar"):
                # packed here from the disc's files: RomM's image hashes can't match by design
                bad = zar_check(path, full=deep)
                if bad:
                    add(system, path, "Compressed file %s" % bad, rom)
                continue
            if os.path.isfile(path):
                if len(files) == 1 and files[0]["file_name"].lower() == low and low.endswith(".zip"):
                    check_zip(system, path, files, rom)
                    continue
                if len(files) == 1 and files[0]["file_name"].lower() == low:
                    want = [(path, files[0])]               # same file as RomM (iso, chd…)
                else:
                    members = {m["name"].lower(): m for f in files for m in (f.get("archive_members") or [])
                               if m.get("name")}
                    m = members.get(low)
                    want = [(path, {"file_size_bytes": m.get("size"), "sha1_hash": m.get("sha1_hash"),
                                    "md5_hash": m.get("md5_hash"), "crc_hash": m.get("crc_hash")})] if m else []
            else:
                if rom_is_folder(detail, files):
                    prefix = (detail.get("full_path") or "").rstrip("/") + "/"
                    want = []
                    for f in files:
                        rel = f.get("full_path", "")
                        rel = rel[len(prefix):] if rel.startswith(prefix) else f["file_name"]
                        want.append((os.path.join(path, *rel.split("/")), f))
                else:
                    want = [(os.path.join(path, *m["name"].split("/")),
                             {"file_size_bytes": m.get("size"), "sha1_hash": m.get("sha1_hash"),
                              "md5_hash": m.get("md5_hash"), "crc_hash": m.get("crc_hash")})
                            for f in files for m in (f.get("archive_members") or [])]
                # a disc folder later turned into <folder>.chd keeps nothing to compare
            # discs converted to CHD on the box: the tracks are gone on purpose
            chds = {os.path.splitext(x)[0].lower(): os.path.join(path, x)
                    for x in (os.listdir(path) if os.path.isdir(path) else []) if x.lower().endswith(".chd")}
            converted = set()
            for fp, f in want:
                st = os.path.splitext(os.path.basename(fp))[0].lower()
                st = re.sub(r"\s*\(track \d+\)$", "", st)
                if not os.path.exists(fp) and st in chds:
                    converted.add(chds[st])
            for c in converted:
                if deep and subprocess.run(["nice", "-n", "19", chdman, "verify", "-i", c],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
                    add(system, c, "CHD failed chdman verify", rom)
            for fp, f in want:
                if cancel is not None and cancel.is_set():
                    return findings, stats
                st = re.sub(r"\s*\(track \d+\)$", "", os.path.splitext(os.path.basename(fp))[0].lower())
                if not os.path.exists(fp):
                    if st not in chds:
                        add(system, fp, "Missing file: %s" % os.path.basename(fp), rom)
                elif xbox_cut(system, fp, f):
                    continue          # cut to its game partition here: RomM's size and hashes are the full disc's
                elif f.get("file_size_bytes") and os.path.getsize(fp) != f["file_size_bytes"]:
                    small = os.path.getsize(fp) < f["file_size_bytes"]
                    # a smaller .iso is usually a trimmed image (plays); a short
                    # track or cart file is a broken copy
                    trimmed = small and fp.lower().endswith((".iso", ".xiso"))
                    add(system, fp, "%s than RomM's copy: %s vs %s%s" % (
                        "Smaller" if small else "Bigger", human_size(os.path.getsize(fp)),
                        human_size(f["file_size_bytes"]),
                        " (trimmed or incomplete)" if trimmed else " (incomplete)" if small else ""),
                        rom, "differs" if (trimmed or not small) else "damaged")
                elif deep and file_hash_ok(fp, f)[0] is False:
                    add(system, fp, "Checksum doesn't match RomM", rom)
    for system in {f["system"] for f in findings}:
        if cancel is not None and cancel.is_set():
            break
        try:
            identify(system)
        except RommError:
            pass
    return findings, stats


def audit_path():
    return os.path.join(DATA_DIR, "audit-last.json")     # follows DATA_DIR (tests re-point it)


def save_audit(findings, stats, deep):
    """Keep the last finished check, so On this box can show it later. Each
    finding remembers its file's size and mtime: a file changed since (fixed,
    replaced) drops out on load."""
    out = []
    for f in findings:
        try:
            st = os.stat(f["path"])
            sig = [st.st_size, int(st.st_mtime)]
        except OSError:
            sig = None
        out.append(dict(f, sig=sig))
    os.makedirs(DATA_DIR, exist_ok=True)
    path = audit_path()
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"time": time.time(), "deep": bool(deep), "stats": stats, "findings": out}, fh)
    os.replace(tmp, path)


def load_audit():
    """The last finished check, or None: {"time", "deep", "stats", "findings"},
    without findings whose file is gone or changed since."""
    try:
        with open(audit_path()) as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    keep = []
    for f in d.get("findings") or []:
        if not isinstance(f, dict) or not all(isinstance(f.get(k), str) for k in ("path", "system", "severity",
                                                                                  "problem")):
            continue
        try:
            st = os.stat(f["path"])
        except OSError:
            if f.get("sig") is None:            # missing when checked, still missing ("Missing file: …")
                keep.append(f)
            continue
        if f.get("sig") in (None, [st.st_size, int(st.st_mtime)]):
            keep.append(f)
    d["findings"] = keep
    if not isinstance(d.get("stats"), dict):
        d["stats"] = {}
    return d


# ----------------------------------------------------------- game info for games already here

INFO_TAGS = ("name", "desc", "image")


def _gamelist_info(sysdir):
    """{relative path: set of filled tags} from roms/<sys>/gamelist.xml (read only)."""
    out = {}
    try:
        root = ET.parse(os.path.join(sysdir, "gamelist.xml")).getroot()
    except (OSError, ET.ParseError):
        return out
    for g in root.findall("game"):
        p = g.findtext("path")
        if p:
            out[p] = {t for t in INFO_TAGS + ("manual",) if (g.findtext(t) or "").strip()}
    return out


def plan_info(api, system, platform_ids, es, manuals=True, cancel=None):
    """Gamelist entries for games already in roms/<system> that RomM knows
    (matched by file name, like "already on this box") and whose ES entry
    lacks a name, description or cover. Entries carry "fill": the merge only
    sets what's missing. Returns (entries, stats)."""
    sysdir = os.path.join(ROMS_ROOT, system)
    stats = {"on_box": 0, "matched": 0, "complete": 0, "queued": 0}
    if not os.path.isdir(sysdir):
        return [], stats
    index, sets = {}, {}
    for pid in platform_ids:
        off = 0
        while True:
            if cancel is not None and cancel.is_set():
                return [], stats
            page = api.roms(platform_ids=[pid], offset=off, limit=500)
            for r in page.get("items", []):
                index.setdefault((r.get("fs_name_no_ext") or "").lower(), r)
                index.setdefault((r.get("fs_name") or "").lower(), r)
                d = disc_info(r.get("fs_name_no_ext") or "")
                if d and (d[0].lower() not in sets or d[1] < sets[d[0].lower()][0]):
                    sets[d[0].lower()] = (d[1], r)          # a set's .m3u gets disc 1's info
            off += len(page.get("items", []))
            if not page.get("items") or off >= page.get("total", 0):
                break
    have = _gamelist_info(sysdir)
    names = sorted(os.listdir(sysdir))
    in_m3u = set()
    for n in names:
        if n.lower().endswith(".m3u"):
            try:
                with open(os.path.join(sysdir, n), errors="replace") as f:
                    in_m3u |= {os.path.normpath(os.path.join(sysdir, ln.strip())) for ln in f if ln.strip()}
            except OSError:
                pass
    exts = es.get(system)
    entries = []
    for n in names:
        path = os.path.join(sysdir, n)
        low = n.lower()
        if n.startswith(".") or n in AUDIT_SKIP or low.endswith((".xml", ".txt", ".part", ".tmp.chd")) \
                or ".bak" in low or path in in_m3u \
                or any(q.startswith(path + os.sep) for q in in_m3u):       # a disc folder of a playlist
            continue
        isdir = os.path.isdir(path)
        if not isdir and exts is not None and os.path.splitext(low)[1] not in exts:
            continue
        stats["on_box"] += 1
        stem = n if isdir else os.path.splitext(n)[0]
        rom = index.get(low) or index.get(stem.lower())
        title = None
        if rom is None and low.endswith(".m3u"):
            hit = sets.get(stem.lower())
            if hit:
                rom = hit[1]
                d = disc_info(rom.get("name") or "")
                title = d[0] if d else rom.get("name")
        if rom is None:
            continue
        stats["matched"] += 1
        prim = path if (not isdir or low.endswith(".m3u")) else primary_path(system, [path], es)
        if not prim:
            continue
        got = have.get("./" + os.path.relpath(prim, sysdir), set())
        if set(INFO_TAGS) <= got and (not manuals or not manual_path(rom) or "manual" in got):
            stats["complete"] += 1
            continue
        e = gamelist_entry(rom, system, [prim], manual=manuals)
        e["fill"] = True
        if title:
            e["name"] = title
        entries.append(e)
    stats["queued"] = len(entries)
    return entries, stats


def queue_info(entries):
    if entries:
        os.makedirs(DATA_DIR, exist_ok=True)
        append_gamelist(*entries)


# ----------------------------------------------------------- BIOS

# Batocera's BIOS list (batocera-systems) is keyed by system name; a few of
# our roms folders use a different key there.
BIOS_ALIASES = {"megacd": ["segacd"], "pcenginecd": ["pcengine"], "fbneo": ["neogeo"],
                "msx1": ["msx"]}
_BIOS_DB = None
_MD5 = {}


def bios_requirements():
    """{system: {"biosFiles": [{"md5", "file"}...]}} from Batocera's own list
    (empty off-box)."""
    global _BIOS_DB
    if _BIOS_DB is None:
        try:
            import runpy
            _BIOS_DB = runpy.run_path(BIOS_DB, run_name="romm_browser_bios").get("systems", {})
        except Exception:
            _BIOS_DB = {}
    return _BIOS_DB


def file_md5(path):
    st = os.stat(path)
    key = (path, st.st_size, st.st_mtime)
    if key not in _MD5:
        import hashlib
        h = hashlib.md5()
        with open(path, "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""):
                h.update(b)
        _MD5[key] = h.hexdigest()
    return _MD5[key]


def missing_bios(system):
    """BIOS files Batocera lists for this system that are absent or have the
    wrong checksum. (Some systems list alternatives — any one works.)"""
    out, seen = [], set()
    db = bios_requirements()
    for key in [system] + BIOS_ALIASES.get(system, []):
        for b in (db.get(key) or {}).get("biosFiles", []):
            if b.get("zippedFile") or not b.get("file") or not b.get("md5"):
                continue
            path = os.path.join(USERDATA, b["file"])
            if path in seen:
                continue
            seen.add(path)
            try:
                if os.path.isfile(path) and file_md5(path) == b["md5"].lower():
                    continue
            except OSError:
                pass
            out.append({"path": path, "md5": b["md5"].lower(), "file": b["file"]})
    return out


def bios_offer(api, system, platform_id):
    """[(missing entry, RomM firmware)] that RomM can supply, matched by MD5."""
    missing = missing_bios(system)
    if not missing or not platform_id:
        return []
    by_md5 = {}
    for fw in api.firmware(platform_id):
        if fw.get("md5_hash"):
            by_md5.setdefault(fw["md5_hash"].lower(), fw)
    return [(m, by_md5[m["md5"]]) for m in missing if m["md5"] in by_md5]


def bios_fetched():
    """{"bios/x.bin": {"when", "from", "system"}} — what RomM Browser fetched."""
    try:
        with open(os.path.join(DATA_DIR, "bios-fetched.json")) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _record_bios(entries):
    data = bios_fetched()
    data.update(entries)
    os.makedirs(DATA_DIR, exist_ok=True)
    p = os.path.join(DATA_DIR, "bios-fetched.json")
    with open(p + ".tmp", "w") as f:
        json.dump(data, f, indent=1)
    os.replace(p + ".tmp", p)


def bios_required(system):
    """Distinct BIOS files Batocera lists for this system (with checksums)."""
    out, seen = [], set()
    db = bios_requirements()
    for key in [system] + BIOS_ALIASES.get(system, []):
        for b in (db.get(key) or {}).get("biosFiles", []):
            if b.get("zippedFile") or not b.get("file") or not b.get("md5") or b["file"] in seen:
                continue
            seen.add(b["file"])
            out.append(b["file"])
    return out


def bios_status(api, platforms_by_system, systems_with_games=()):
    """Per system that needs BIOS: [{"system", "name", "required", "missing",
    "offers", "fetched"}], for systems with games on the box or BIOS that
    RomM can supply. missing = entries absent/wrong; offers = the subset RomM
    has (matched by MD5)."""
    db = bios_requirements()
    rec = bios_fetched()
    out = []
    for system in sorted(set(platforms_by_system) | set(systems_with_games)):
        required = bios_required(system)
        if not required:
            continue
        missing = missing_bios(system)
        offers, seen = [], set()
        if missing and api is not None:
            want = {m["md5"]: m for m in missing}
            for pid in platforms_by_system.get(system, []):
                try:
                    fws = api.firmware(pid)
                except RommError:
                    continue
                for fw in fws:
                    md5 = (fw.get("md5_hash") or "").lower()
                    if md5 in want and want[md5]["file"] not in seen:
                        seen.add(want[md5]["file"])
                        offers.append((want[md5], fw))
        if system not in systems_with_games and not offers:
            continue
        name = next(((db.get(k) or {}).get("name") for k in [system] + BIOS_ALIASES.get(system, [])
                     if (db.get(k) or {}).get("name")), system)
        out.append({"system": system, "name": name, "required": required, "missing": missing,
                    "offers": offers,
                    "fetched": {f: r for f, r in rec.items() if f in required}})
    return out


def fetch_bios(api, offers, system=None):
    """Download offered BIOS files to Batocera's paths; MD5-verified, an
    existing wrong file is kept as .bak-romm. Returns the files written."""
    written = []
    record = {}
    try:
        _fetch_bios_into(api, offers, system, written, record)
    finally:
        if record:
            _record_bios(record)              # what got written, even if a later file failed
    return written


def _fetch_bios_into(api, offers, system, written, record):
    biosdir = os.path.realpath(os.path.join(USERDATA, "bios"))
    for m, fw in offers:
        if not os.path.realpath(m["path"]).startswith(biosdir + os.sep):
            continue
        os.makedirs(os.path.dirname(m["path"]), exist_ok=True)
        part = m["path"] + ".part"
        try:
            r = api.s.get("%s/api/firmware/%d/content/%s" % (
                api.base, fw["id"], urllib.parse.quote(fw["file_name"])), timeout=60)
        except Exception as e:
            raise RommError("BIOS download failed (%s)" % e.__class__.__name__) from None
        if r.status_code != 200:
            raise RommError("BIOS download failed: HTTP %d" % r.status_code)
        with open(part, "wb") as f:
            f.write(r.content)
        if file_md5(part) != m["md5"]:
            os.remove(part)
            raise RommError("BIOS checksum mismatch for %s" % m["file"])
        if os.path.exists(m["path"]):
            os.replace(m["path"], m["path"] + ".bak-romm")
        os.replace(part, m["path"])
        written.append(m["file"])
        record[m["file"]] = {"when": time.strftime("%Y-%m-%d %H:%M"), "from": fw.get("file_name"),
                             "system": system}
        log("BIOS %s <- RomM firmware %s" % (m["file"], fw["file_name"]))


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
        self.force = False            # "Download now": ignore the download hours
        self.user_paused = False      # paused by the user (not by "Pause all" / exit)
        self.short_space = 0          # waiting for space: bytes still to free
        self.replace = None           # broken copy under another name, removed once this one is in
        self.cancel = threading.Event()

    @property
    def name(self):
        return self.rom.get("name") or self.rom.get("fs_name_no_ext") or "?"

    @property
    def label(self):
        """Name plus "Disc n" for a disc of a set (RomM often names every
        disc of a game the same)."""
        d = disc_info(self.rom.get("fs_name_no_ext") or "") if self.group else None
        if d and not disc_info(self.name):
            return "%s (Disc %d)" % (self.name, d[1])
        return self.name

    @property
    def active(self):
        return self.state in (Job.QUEUED, Job.DOWNLOADING, Job.VERIFYING, Job.EXTRACTING)

    def requeue(self):
        self.state, self.message, self.pause = Job.QUEUED, "", False
        self.short_space = 0
        self.user_paused, self.force = False, False
        self.cancel = threading.Event()
        self.done_bytes, self.speed, self.attempts, self.retry_at = 0, 0.0, 0, 0.0


class Cancelled(Exception):
    pass


class OutsideHours(Exception):
    """The download hours ended mid-transfer: back to the queue, .part kept."""


class NetError(RommError):
    """Transient: worth retrying (connection drop, timeout, 5xx/429)."""


class ChecksumError(RommError):
    """The file arrived but doesn't match RomM's checksum."""


RETRY_DELAYS = (15, 60, 180)        # seconds before attempts 2, 3, 4
SPACE_RECHECK = 30                  # seconds between free-space checks for a waiting download


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
                 "path_cover_large", "has_multiple_files", "has_nested_single_file")

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
        self.es = load_es_systems()
        self.jobs = []
        self._closed = False
        Downloader.instances.append(self)
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()

    instances = []

    def close(self):
        """Stop the worker (after its current job; used by tests)."""
        self.pause_all()
        with self.cv:
            self._closed = True
            self.cv.notify_all()
        self.t.join(30)

    # -- queue ------------------------------------------------------------
    def enqueue(self, rom, system, group=None, replace=None):
        """replace: paths of a broken copy under another name (Check games): once
        RomM's copy is in, saves move over, playlists follow, and they're deleted."""
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
                    j.pause = True
                    j.cancel.set()
        self.save_queue()

    def release_all(self):
        with self.cv:
            self.hold = False
            for j in self.jobs:
                if j.state == Job.PAUSED and not j.user_paused:    # one you paused yourself stays paused
                    j.requeue()
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
                job.pause = True
                job.cancel.set()
        self.save_queue()

    def resume_job(self, job):
        with self.cv:
            if job.state in (Job.PAUSED, Job.FAILED, Job.CANCELLED):
                job.requeue()
                self.cv.notify_all()
        self.save_queue()

    def _drop_parts(self, job):
        """Delete a cancelled/removed download's partial files (only .part
        files of that rom, only inside roms/<system>)."""
        sysdir = os.path.join(ROMS_ROOT, job.system)
        base = os.path.join(sysdir, safe_name(job.rom.get("fs_name") or ""))
        if not job.rom.get("fs_name") or not _inside(base, sysdir):
            return
        cands = [base + ".part"]
        if os.path.isdir(base):
            cands += [os.path.join(dp, f) for dp, _, fs in os.walk(base) for f in fs if f.endswith(".part")]
        for p in cands:
            try:
                if os.path.isfile(p) and _inside(p, sysdir):
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
                job.pause = False
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
            log("queue save failed: %r" % e)

    def _save_queue(self):
        with self.qlock:
            pend = [{"rom": {k: j.rom.get(k) for k in self.SLIM_KEYS}, "system": j.system,
                     "group": j.group, "paused": bool(j.user_paused), "replace": j.replace or None}
                    for j in list(self.jobs) if j.active or j.state == Job.PAUSED]
            os.makedirs(DATA_DIR, exist_ok=True)
            tmp = QUEUE_PATH + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"held": self.hold, "jobs": pend}, f)
            os.replace(tmp, QUEUE_PATH)

    @staticmethod
    def saved_queue():
        try:
            with open(QUEUE_PATH) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return []
        return data.get("jobs", []) if isinstance(data, dict) else data   # old format: a list

    @staticmethod
    def saved_held():
        try:
            with open(QUEUE_PATH) as f:
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
                j = self.enqueue(it["rom"], it["system"], it.get("group"), it.get("replace"))
                if it.get("paused"):
                    with self.cv:
                        j.user_paused = True
                        if j.state == Job.QUEUED:
                            j.state, j.message = Job.PAUSED, "Paused"
                n += 1
        return n

    def pause_all(self):
        """Stop transfers but keep them queued (partials resume via Range)."""
        for j in list(self.jobs):
            if j.active:
                j.pause = True
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
                    if not j.force and not in_download_hours(self.cfg):
                        continue                 # waits for the download hours
                    j.state = Job.DOWNLOADING
                    return j
                self.cv.wait(1.0)

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
            if not j.pause:
                self._drop_parts(j)        # before the state flips: "Cancelled" means cleaned up
            j.state = Job.PAUSED if j.pause else Job.CANCELLED
            j.message = "Paused" if j.pause else "Cancelled"
        elif isinstance(e, NoSpace):
            if not j.short_space:
                log("SPACE %s: %s (keeping %s free)" % (j.name, e, human_size(keep_free_bytes(self.cfg))))
            j.state, j.message, j.speed = Job.QUEUED, str(e), 0.0
            j.short_space, j.retry_at = e.short, time.time() + SPACE_RECHECK
        elif isinstance(e, OutsideHours):
            j.state, j.message, j.speed = Job.QUEUED, "Waiting for download hours", 0.0
            log("HOURS %s: paused until %s" % (j.name, hours_label(self.cfg.get("download_hours"))))
        elif isinstance(e, (NetError, ChecksumError)):
            j.attempts += 1
            limit = 1 if isinstance(e, ChecksumError) else len(RETRY_DELAYS)
            if j.attempts <= limit:
                delay = RETRY_DELAYS[j.attempts - 1]
                j.state, j.retry_at = Job.QUEUED, time.time() + delay
                j.message = "%s Retrying in %d s." % (e, delay)
                log("RETRY %s (%d): %s" % (j.name, j.attempts, e))
            else:
                j.state, j.message = Job.FAILED, str(e)[:160]
                log("FAIL %s: %s" % (j.name, j.message))
        else:  # keep the worker alive whatever happens
            j.state = Job.FAILED
            # our own errors are written to be shown; anything else could carry
            # request details, so only its type goes on screen / in the log
            j.message = (str(e) if isinstance(e, (RommError, OSError, zipfile.BadZipFile))
                         else "Error (%s)" % e.__class__.__name__)[:160]
            log("FAIL %s: %s" % (j.name, j.message))

    def _settled(self, j):
        if j.state == Job.DONE and j.group:
            try:
                self._finish_group(j.group, j.system)
            except Exception as e:
                log("group %s: %r" % (j.group.get("base"), e))
        self.save_queue()

    # -- unpacking while the next download runs ------------------------------
    allow_overlap = True          # rb_dl.py (background, mostly while a game runs) turns it off
    POST_BACKLOG = 2              # one unpacking + one waiting; the next download waits beyond that

    def _overlap(self, post):
        return self.allow_overlap and self.cfg.get("extract_parallel", True) and post.get("heavy")

    def _hand_off(self, j, post):
        with self.cv:
            if not hasattr(self, "_post_q"):
                self._post_q, self._post_busy = [], 0
                threading.Thread(target=self._post_run, daemon=True).start()
            while len(self._post_q) + self._post_busy >= self.POST_BACKLOG and not self._closed:
                self.cv.wait(1.0)                  # disk is busy enough: wait before downloading more
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
                    self.cv.wait(1.0)
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
            raise RommError("TLS certificate rejected") from None
        except Exception as e:
            raise NetError("Download interrupted (%s)." % e.__class__.__name__) from None
        if r.status_code == 416:      # .part already complete (verified below)
            r.close()
        elif r.status_code in (429, 500, 502, 503, 504):
            r.close()
            raise NetError("RomM busy (HTTP %d)." % r.status_code)
        elif r.status_code not in (200, 206):
            r.close()
            raise RommError("Download failed: HTTP %d" % r.status_code)
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
                        if not j.force and not in_download_hours(self.cfg):
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
                    raise RommError("The disk is full.") from None      # not worth retrying
                raise NetError("Download interrupted (%s)." % e.__class__.__name__) from None
            except Exception as e:
                r.close()
                raise NetError("Download interrupted (%s)." % e.__class__.__name__) from None
        if size_hint and os.path.getsize(part) != size_hint:
            got = os.path.getsize(part)
            if got < size_hint and r.status_code != 416:
                raise NetError("Download stopped early (%s of %s)." % (human_size(got), human_size(size_hint)))
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
    def _part_bytes(sysdir, detail):
        """Bytes of this rom's partial download already on disk."""
        name = safe_name(detail.get("fs_name") or "")
        if not name:
            return 0
        p = os.path.join(sysdir, name)
        if os.path.isfile(p + ".part"):
            return os.path.getsize(p + ".part")
        if os.path.isdir(p):
            return sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(p) for f in fs
                       if f.endswith(".part") or not f.startswith("."))
        return 0

    def _process(self, j):
        rom, system = j.rom, j.system
        sysdir = os.path.join(ROMS_ROOT, system)
        os.makedirs(sysdir, exist_ok=True)
        try:
            detail = self.api.rom(rom["id"])
        except RommError as e:
            if "Can't reach" in str(e):
                raise NetError(str(e) + ".") from None
            raise
        j.rom = rom = dict(rom, **detail)
        files = [f for f in (detail.get("files") or [])
                 if f.get("category") in (None, "game")]
        j.total_bytes = sum(f.get("file_size_bytes") or 0 for f in files) or \
            detail.get("fs_size_bytes") or 0
        j.done_bytes, j.verified = 0, None
        # extracted disc games are bigger than the zip; what's already
        # downloaded (.part) is on disk; and the keep-free reserve stays free
        need = max(0, space_needed(detail, system) - self._part_bytes(sysdir, detail)) + \
            (keep_free_bytes(self.cfg) or SPACE_MARGIN)       # keep-free 0: still a small margin
        free = self._free_bytes(sysdir) - self.unpacking_bytes
        if free < need:
            raise NoSpace(need - free)
        if j.short_space:
            log("SPACE %s: enough space now" % j.name)
        j.short_space = 0

        j.state = Job.DOWNLOADING
        fs_name = safe_name(detail["fs_name"])
        # A RomM rom that is a *folder* (multi-file GDI/cue+bin set, or a folder
        # holding a single file) is pulled file by file, keeping its layout; the
        # whole-rom /content endpoint would hand back a zip or an extensionless
        # file named after the folder.
        is_folder = files and rom_is_folder(detail, files)
        if is_folder:
            folder = os.path.join(sysdir, fs_name)
            prefix = (detail.get("full_path") or "").rstrip("/") + "/"
            for f in files:
                rel = f.get("full_path", "")
                rel = rel[len(prefix):] if rel.startswith(prefix) else f["file_name"]
                parts = [safe_name(p) for p in rel.split("/") if p not in ("", ".", "..")]
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
            dest = os.path.join(sysdir, fs_name)
            rec = files[0] if len(files) == 1 else None
            if os.path.isfile(dest) and not os.path.exists(dest + ".part") and \
                    os.path.getsize(dest) == j.total_bytes and file_hash_ok(dest, rec, name=dest)[0] is True:
                j.done_bytes, j.verified = j.total_bytes, True   # already here, verified (e.g. paused while extracting)
            else:
                self._fetch(j, "/api/roms/%d/content/%s" % (
                    rom["id"], urllib.parse.quote(detail["fs_name"])), dest, j.total_bytes, rec)
            heavy = self._will_extract(j, dest)
            return {"kind": "file", "path": dest, "heavy": heavy,
                    "extra": max(0, space_needed(detail, system) - j.total_bytes) if heavy else 0}

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
        log("OK %s -> %s%s" % (j.name, results, " [verified]" if j.verified else ""))

    # -- multi-disc sets -----------------------------------------------
    def _finish_group(self, group, system):
        """When every disc of a set is on the box: write <base>.m3u (if the
        system plays playlists), hide the single discs in ES and give the
        playlist the game's metadata. Idempotent."""
        lib = self.library
        with lib.lock:
            lib._reload()
            recs = [lib.installed.get(str(rid)) for rid in group["rom_ids"]]
        if not all(r and any(os.path.exists(p) for p in r.get("paths", [])) for r in recs):
            return None
        if not self._accepts(system, ".m3u"):
            log("group %s: %s has no .m3u support; discs stay separate" % (group["base"], system))
            return None
        sysdir = os.path.join(ROMS_ROOT, system)
        prims = [primary_path(system, r["paths"], self.es) for r in recs]
        if not all(prims):
            return None
        m3u = os.path.join(sysdir, safe_name(group["base"]) + ".m3u")
        lines = [os.path.relpath(p, sysdir) for p in prims]
        if os.path.exists(m3u):
            with open(m3u, errors="replace") as f:
                old = [ln.strip() for ln in f if ln.strip()]
            if old == lines:
                return m3u
            ours = any(m3u in r["paths"] for r in recs)     # we wrote it earlier
            if not ours and not all(os.path.normpath(os.path.join(sysdir, ln)) in
                       {os.path.normpath(p) for r in recs for p in r["paths"]} or
                       any(os.path.normpath(os.path.join(sysdir, ln)).startswith(os.path.normpath(p) + os.sep)
                           for r in recs for p in r["paths"]) for ln in old):
                log("group %s: %s exists and isn't ours; not overwriting" % (group["base"], m3u))
                return None
        with open(m3u, "w") as f:
            f.write("\n".join(lines) + "\n")
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
        append_gamelist({"system": system, "hide": prims}, meta)
        log("group %s: wrote %s (%d discs)" % (group["base"], m3u, len(prims)))
        return m3u

    def repair_groups(self):
        """Re-check every disc set on the box (e.g. a CHD conversion finished
        while the playlist was being written). Cheap; file checks only."""
        lib = self.library
        with lib.lock:
            lib._reload()
            seen = {}
            for rec in lib.installed.values():
                g = rec.get("group")
                if g and g.get("id") not in seen:
                    seen[g["id"]] = (g, rec["system"])
        for g, system in seen.values():
            try:
                self._finish_group(g, system)
            except Exception as e:
                log("group %s: %r" % (g.get("base"), e))
        return len(seen)

    def _replace_old(self, j, results):
        """The broken copy (another file name) goes now that RomM's is here:
        saves are copied to the new name, playlists pointed at it, then the
        old files are deleted (never the new ones)."""
        import rb_chd                                  # save/playlist helpers live with the converter
        sysdir = os.path.join(ROMS_ROOT, j.system)
        new = primary_path(j.system, results, self.es) or results[0]
        new_set = {os.path.normpath(p) for r in results for p in ([r] + game_files(r))}
        for old in j.replace or []:
            if not os.path.exists(old) or os.path.normpath(old) in new_set or not _inside(old, sysdir):
                continue
            try:
                rb_chd.migrate_saves(j.system, old, new)
                rb_chd.fix_m3us(sysdir, {os.path.normpath(old): new})
            except Exception as e:
                log("replace %s: %r" % (old, e))
            delete_game(j.system, old, self.library)
            log("REPLACED %s -> %s" % (old, new))
        j.replace = None

    # -- post-processing -------------------------------------------------
    def _will_extract(self, j, path):
        """True when post-processing will unpack this file (the slow part)."""
        ext = os.path.splitext(path)[1].lower()
        if ext not in ARCHIVE_EXTS or j.system in ZIP_NATIVE:
            return False
        try:
            members = self._list_archive(path)
        except Exception:
            return False
        has_disc = any(os.path.splitext(m)[1].lower() in DISC_EXTS for m in members)
        return bool(members) and not (self._accepts(j.system, ext) and not has_disc)

    def _accepts(self, system, ext):
        exts = self.es.get(system)
        return True if exts is None else ext.lower() in exts

    def _xbox_names(self, system, roots):
        """Make Xbox images bootable, part 1: "X.xiso" -> "X.iso" before anything
        filters by extension (ES lists .iso, not .xiso). Returns the roots."""
        if system != "xbox" or not self.cfg.get("xbox_bootable", True):
            return roots
        out = []
        for r in roots:
            if os.path.isdir(r):
                for dp, _, fns in os.walk(r):
                    for fn in fns:
                        xiso_to_iso(os.path.join(dp, fn))
                out.append(r)
            else:
                out.append(xiso_to_iso(r))
        return out

    def _postprocess_file(self, j, path):
        j.chd_task = None
        path = self._xbox_names(j.system, [path])[0]
        ext = os.path.splitext(path)[1].lower()
        system = j.system
        if ext not in ARCHIVE_EXTS or system in ZIP_NATIVE:
            if ext in DISC_EXTS:                   # a bare .iso/.cue/...: convert it like an extracted one
                self._maybe_chd(j, system, [path], [path])
            return [path]
        members = self._list_archive(path)
        if not members:
            raise RommError("The archive is empty.")
        has_disc = any(os.path.splitext(m)[1].lower() in DISC_EXTS for m in members)
        if self._accepts(system, ext) and not has_disc:
            return [path]          # cart rom in a zip the core reads directly
        j.state = Job.EXTRACTING
        stem = os.path.splitext(os.path.basename(path))[0]
        top = {m.split("/")[0] for m in members}
        # several loose files (cue + bins) -> own folder named after the game;
        # a single file or an archive that already has one top folder -> sysdir
        sysdir = os.path.dirname(path)
        if len(top) > 1:
            target = os.path.join(sysdir, stem)
        else:
            target = sysdir
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
                        raise RommError("Unsafe path in archive: %s" % info.filename)
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
                    raise RommError("Unsafe path in archive: %s" % m)
            rc = subprocess.run([self._7z(), "x", "-y", "-o" + target, path],
                                capture_output=True).returncode
            if rc != 0:
                raise RommError("7z extract failed (rc %d)" % rc)

    def _maybe_chd(self, j, system, playable, roots):
        if system == "xbox":
            for p in playable if self.cfg.get("xbox_bootable", True) else []:
                if p.lower().endswith(".iso"):
                    try:
                        cut = make_xiso(p)
                    except (NoSpace, OSError) as e:
                        log("xbox: %s left as a full disc image (%s); rb_chd_plan.py can fix it later"
                            % (os.path.basename(p), e))
                        continue
                    if cut:
                        log("xbox: %s: kept the game partition (xemu can't boot a full disc image), "
                            "-%s" % (os.path.basename(p), human_size(cut)))
            if squashfs_enabled(self.cfg, system) and self._accepts(system, ".squashfs"):
                srcs = [p for p in playable if p.lower().endswith(".iso")]
                if srcs:
                    self._queue_conversion(j, system, "squashfs", srcs, roots)
            return
        if system in ZAR_SYSTEMS:
            srcs = [p for p in playable if p.lower().endswith(".iso")]
            if zar_enabled(self.cfg, system) and srcs and self._accepts(system, ".zar"):
                self._queue_conversion(j, system, "zar", srcs, roots)
            return
        if system in Z3DS_SYSTEMS:
            srcs = [p for p in playable if z3ds_target(p) and self._accepts(system, os.path.splitext(z3ds_target(p))[1])]
            if z3ds_enabled(self.cfg, system) and srcs:
                self._queue_conversion(j, system, "z3ds", srcs, roots)
            return
        if system in RVZ_SYSTEMS:
            if not rvz_enabled(self.cfg) or not self._accepts(system, ".rvz"):
                return
            srcs = [p for p in playable if p.lower().endswith(RVZ_SOURCES)]
            if srcs:
                self._queue_conversion(j, system, "rvz", srcs, roots)
            return
        if not self.cfg.get("chd"):
            return
        if system in CHD_CD_SYSTEMS:
            srcs = [p for p in playable if p.lower().endswith((".cue", ".gdi"))]
            mode = "createcd"
        elif system in CHD_DVD_SYSTEMS:
            srcs = [p for p in playable if p.lower().endswith(".iso")]
            mode = "createdvd"
        else:
            return
        if not srcs or not self._accepts(system, ".chd"):
            return
        self._queue_conversion(j, system, mode, srcs, roots)

    def _queue_conversion(self, j, system, mode, srcs, roots):
        os.makedirs(CHD_QUEUE_DIR, exist_ok=True)
        task = {"system": system, "mode": mode, "sources": srcs, "roots": roots,
                "queued": time.time()}
        name = "%d-%d-%s.json" % (int(time.time() * 1000), j.rom.get("id") or 0, re.sub(r"\W+", "_", system))
        j.chd_task = name
        with open(os.path.join(CHD_QUEUE_DIR, name), "w") as f:
            json.dump(task, f)

    def _queue_gamelist(self, rom, system, paths):
        """Hand gamelist metadata to the launcher's post-exit merge step
        (ES must not be written to while it runs — see rb_finalize.py)."""
        entry = gamelist_entry(rom, system, paths, manual=self.cfg.get("manuals", True))
        os.makedirs(DATA_DIR, exist_ok=True)
        append_gamelist(entry)
        return entry


def manual_path(rom):
    """Server path of the rom's manual PDF in RomM's assets, or None. (Never
    url_manual: that's the upstream scraper URL, not RomM.)"""
    p = rom.get("path_manual") if rom.get("has_manual") else None
    if not p or not isinstance(p, str):
        return None
    p = p.split("?")[0].lstrip("/")
    if p.startswith("assets/romm/resources/"):
        p = p[len("assets/romm/resources/"):]
    if ".." in p.split("/") or not p.lower().endswith(".pdf"):
        return None
    return "/assets/romm/resources/" + p


def ra_summary(rom):
    """(achievements, points) from RomM's RetroAchievements data, or None."""
    if not rom.get("ra_id"):
        return None
    ach = (rom.get("merged_ra_metadata") or {}).get("achievements") or []
    return len(ach), sum(int(a.get("points") or 0) for a in ach)


def gamelist_entry(rom, system, paths, manual=True):
    md = rom.get("metadatum") or {}
    return {
        "manual": manual_path(rom) if manual else None,
        "system": system, "paths": paths, "rom_id": rom["id"],
        "name": rom.get("name"), "desc": rom.get("summary"),
        "releasedate": md.get("first_release_date"),
        "genres": md.get("genres") or [],
        "developer": ", ".join(md.get("developers") or md.get("companies") or []),
        "publisher": ", ".join(md.get("publishers") or []),
        "players": md.get("player_count"),
        "rating": md.get("average_rating"),
        "cover": rom.get("path_cover_large") or rom.get("path_cover_small"),
    }
