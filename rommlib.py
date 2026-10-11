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

VERSION = "0.6.5"            # changes only for a real GitHub release
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
RELEASE_DATE = "2026-10-10"   # bump with VERSION (tools/build-release.sh refuses a stale one)
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
# worker turns into CHD. GC/Wii (rvz), PSP, PS3, Xbox stay as-is.
CHD_CD_SYSTEMS = {"psx", "saturn", "dreamcast", "megacd", "pcenginecd", "neogeocd",
                  "3do", "amigacd32", "amigacdtv", "cdi", "pcfx"}
CHD_DVD_SYSTEMS = {"ps2"}

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


def space_needed(rom, system):
    """Rough bytes on disk once downloaded and extracted."""
    size = rom.get("fs_size_bytes") or 0
    return int(size * (2.2 if system in EXPANDS else 1.0))


def fit_in_space(items, free):
    """Greedy, keeping the given order: (that_fit, total_needed_for_them)."""
    fit, used = [], 0
    for rom, system in items:
        need = space_needed(rom, system)
        if used + need + SPACE_MARGIN <= free:
            fit.append((rom, system))
            used += need
    return fit, used


# ----------------------------------------------------------- updates

def _vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3]) or (0,)


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
                stems.add(os.path.splitext(n)[0].lower())
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
            stem = os.path.splitext(n)[0] if not os.path.isdir(os.path.join(ROMS_ROOT, system, n)) else n
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

    def members_of(files):
        return {m["name"].lower(): m for f in files for m in (f.get("archive_members") or []) if m.get("name")}

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
            # 2) against RomM's record of the game
            stem = n if os.path.isdir(path) else os.path.splitext(n)[0]
            rom = index.get(low) or index.get(stem.lower())
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
                if detail.get("has_multiple_files") or detail.get("has_nested_single_file"):
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
    return findings, stats


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
        self.verified = None          # True once every file matched RomM's checksum
        self.attempts = 0
        self.retry_at = 0.0
        self.pause = False            # cancel, but keep it in the saved queue
        self.force = False            # "Download now": ignore the download hours
        self.user_paused = False      # paused by the user (not by "Pause all" / exit)
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
    def enqueue(self, rom, system, group=None):
        with self.cv:
            for j in self.jobs:
                if j.rom["id"] == rom["id"] and j.active:
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
                     "group": j.group, "paused": bool(j.user_paused)}
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
                j = self.enqueue(it["rom"], it["system"], it.get("group"))
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
                self._process(j)
            except Cancelled:
                if not j.pause:
                    self._drop_parts(j)        # before the state flips: "Cancelled" means cleaned up
                j.state = Job.PAUSED if j.pause else Job.CANCELLED
                j.message = "Paused" if j.pause else "Cancelled"
            except OutsideHours:
                j.state, j.message, j.speed = Job.QUEUED, "Waiting for download hours", 0.0
                log("HOURS %s: paused until %s" % (j.name, hours_label(self.cfg.get("download_hours"))))
            except (NetError, ChecksumError) as e:
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
            except Exception as e:  # keep the worker alive whatever happens
                j.state = Job.FAILED
                # our own errors are written to be shown; anything else could carry
                # request details, so only its type goes on screen / in the log
                j.message = (str(e) if isinstance(e, (RommError, OSError, zipfile.BadZipFile))
                             else "Error (%s)" % e.__class__.__name__)[:160]
                log("FAIL %s: %s" % (j.name, j.message))
            if j.state == Job.DONE and j.group:
                try:
                    self._finish_group(j.group, j.system)
                except Exception as e:
                    log("group %s: %r" % (j.group.get("base"), e))
            self.save_queue()

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
        # extracted disc games are bigger than the zip; leave a margin
        need = space_needed(detail, system) + SPACE_MARGIN
        if self._free_bytes(sysdir) < need:
            raise RommError("Not enough space (%s free, need ~%s)" % (
                human_size(self._free_bytes(sysdir)), human_size(need)))

        j.state = Job.DOWNLOADING
        fs_name = safe_name(detail["fs_name"])
        # A RomM rom that is a *folder* (multi-file GDI/cue+bin set, or a folder
        # holding a single file) is pulled file by file, keeping its layout; the
        # whole-rom /content endpoint would hand back a zip or an extensionless
        # file named after the folder.
        is_folder = files and (detail.get("has_multiple_files")
                               or detail.get("has_nested_single_file"))
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
            results = self._postprocess_folder(j, folder)
        else:
            dest = os.path.join(sysdir, fs_name)
            rec = files[0] if len(files) == 1 else None
            if os.path.isfile(dest) and not os.path.exists(dest + ".part") and \
                    os.path.getsize(dest) == j.total_bytes and file_hash_ok(dest, rec, name=dest)[0] is True:
                j.done_bytes, j.verified = j.total_bytes, True   # already here, verified (e.g. paused while extracting)
            else:
                self._fetch(j, "/api/roms/%d/content/%s" % (
                    rom["id"], urllib.parse.quote(detail["fs_name"])), dest, j.total_bytes, rec)
            results = self._postprocess_file(j, dest)

        entry = self._queue_gamelist(rom, system, results)
        self.library.mark(rom, system, results, group=j.group, meta=entry)
        j.chd_queued = self._chd_queued
        j.chd_task = self._chd_task if self._chd_queued else None
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

    # -- post-processing -------------------------------------------------
    _chd_queued = False

    _chd_task = None

    def _accepts(self, system, ext):
        exts = self.es.get(system)
        return True if exts is None else ext.lower() in exts

    def _postprocess_file(self, j, path):
        self._chd_queued = False
        ext = os.path.splitext(path)[1].lower()
        system = j.system
        if ext not in ARCHIVE_EXTS or system in ZIP_NATIVE:
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
        playable = self._playable_paths(system, out)
        self._maybe_chd(system, playable, out)
        return out

    def _postprocess_folder(self, j, folder):
        self._chd_queued = False
        self._maybe_chd(j.system, self._playable_paths(j.system, [folder]), [folder])
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

    def _maybe_chd(self, system, playable, roots):
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
        os.makedirs(CHD_QUEUE_DIR, exist_ok=True)
        task = {"system": system, "mode": mode, "sources": srcs, "roots": roots,
                "queued": time.time()}
        name = "%d-%s.json" % (int(time.time() * 1000), re.sub(r"\W+", "_", system))
        self._chd_task = name
        with open(os.path.join(CHD_QUEUE_DIR, name), "w") as f:
            json.dump(task, f)
        self._chd_queued = True

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
