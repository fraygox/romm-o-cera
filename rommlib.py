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

VERSION = "0.3.1"

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
    "swap_ab": False,          # False: south button = confirm (Batocera default)
    "button_style": "auto",    # button labels: auto | xbox | playstation | nintendo
    "chd": True,               # background CHD conversion of extracted disc games
    "background_downloads": True,  # keep downloading after the app exits
    "platform_map": {},        # per-slug overrides of PLATFORM_MAP (null hides)
}
# Environment overrides (handy for testing / other launchers); config.json wins
# for anything the Settings screen saves, env wins over both when set.
ENV_OVERRIDES = {"ROMM_URL": "url", "ROMM_TOKEN": "token", "ROMM_VERIFY_TLS": "verify_tls"}


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
             offset=0, limit=60, order_by="name", char_index=False, regions=None):
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
        return self.get("/api/roms", params)

    def rom(self, rom_id):
        return self.get("/api/roms/%d" % rom_id)

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
            return dest
        try:
            r = self.s.get(self.base + path, timeout=15)
            if r.status_code == 200 and r.content:
                tmp = dest + ".tmp"
                write_png(r.content, tmp)
                os.replace(tmp, dest)
                return dest
        except requests.RequestException:
            pass
        return None


# ------------------------------------------------------- installed tracking

class Library:
    """What's already on the box: our own installs (installed.json) plus a
    filename-stem scan of roms/<sys>, so games pushed earlier (same RomM
    filenames) also show as installed."""

    def __init__(self):
        self.lock = threading.Lock()
        self.installed = {}
        self._stems = {}
        try:
            with open(STATE_PATH) as f:
                self.installed = json.load(f)
        except (OSError, ValueError):
            pass

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
            rec = self.installed.get(str(rom["id"]))
            if rec and any(os.path.exists(p) for p in rec.get("paths", [])):
                return True
            stems = self._stems.get(system)
            if stems is None:
                stems = self._scan(system)
        fs = (rom.get("fs_name") or "").lower()
        return fs in stems or (rom.get("fs_name_no_ext") or "").lower() in stems

    def mark(self, rom, system, paths):
        with self.lock:
            self.installed[str(rom["id"])] = {
                "system": system, "paths": paths, "name": rom.get("name"),
                "when": time.strftime("%Y-%m-%d %H:%M:%S")}
            self._stems.pop(system, None)
            os.makedirs(DATA_DIR, exist_ok=True)
            tmp = STATE_PATH + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.installed, f, indent=1)
            os.replace(tmp, STATE_PATH)

    def rescan(self, system):
        with self.lock:
            self._stems.pop(system, None)

    def paths_for(self, rom, system):
        """Files/folders that make up this rom on the box: our own install
        record, else a filename match in roms/<system>; an .m3u brings its
        discs. Only ever paths inside roms/<system>."""
        sysdir = os.path.join(ROMS_ROOT, system)
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
        with self.lock:
            self.installed.pop(str(rom["id"]), None)
            self._stems.pop(system, None)
            os.makedirs(DATA_DIR, exist_ok=True)
            tmp = STATE_PATH + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.installed, f, indent=1)
            os.replace(tmp, STATE_PATH)
        if paths:
            with open(GAMELIST_QUEUE, "a") as f:
                f.write(json.dumps({"system": system, "remove": paths}) + "\n")
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


def fetch_bios(api, offers):
    """Download offered BIOS files to Batocera's paths; MD5-verified, an
    existing wrong file is kept as .bak-romm. Returns the files written."""
    written = []
    for m, fw in offers:
        if not m["path"].startswith(os.path.join(USERDATA, "bios") + os.sep):
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
        log("BIOS %s <- RomM firmware %s" % (m["file"], fw["file_name"]))
    return written


# ----------------------------------------------------------- downloads

class Job:
    QUEUED, DOWNLOADING, EXTRACTING, DONE, FAILED, CANCELLED, PAUSED = (
        "Queued", "Downloading", "Extracting", "Done", "Failed", "Cancelled", "Paused")

    def __init__(self, rom, system):
        self.rom = rom
        self.system = system
        self.state = Job.QUEUED
        self.done_bytes = 0
        self.total_bytes = rom.get("fs_size_bytes") or 0
        self.speed = 0.0
        self.message = ""
        self.chd_queued = False
        self.pause = False            # cancel, but keep it in the saved queue
        self.cancel = threading.Event()

    @property
    def name(self):
        return self.rom.get("name") or self.rom.get("fs_name_no_ext") or "?"

    @property
    def active(self):
        return self.state in (Job.QUEUED, Job.DOWNLOADING, Job.EXTRACTING)


class Cancelled(Exception):
    pass


class Downloader:
    """One worker thread; downloads are sequential (one at a time saturates a typical link)."""

    SLIM_KEYS = ("id", "name", "fs_name", "fs_name_no_ext", "fs_size_bytes", "platform_id",
                 "platform_slug", "regions", "summary", "metadatum", "path_cover_small",
                 "path_cover_large", "has_multiple_files", "has_nested_single_file")

    def __init__(self, api, library, cfg, persist=True):
        self.api = api
        self.library = library
        self.cfg = cfg
        self.persist = persist
        self.qlock = threading.Lock()
        self.es = load_es_systems()
        self.jobs = []
        self.q = queue.Queue()
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()

    def enqueue(self, rom, system):
        for j in self.jobs:
            if j.rom["id"] == rom["id"] and j.active:
                return j
        j = Job(rom, system)
        self.jobs.append(j)
        self.q.put(j)
        self.save_queue()
        return j

    # -- persistence: pending jobs survive exit, reboot, or a hand-over to rb_dl.py
    def save_queue(self):
        if not self.persist:
            return
        with self.qlock:
            pend = [{"rom": {k: j.rom.get(k) for k in self.SLIM_KEYS}, "system": j.system}
                    for j in self.jobs if j.state in (Job.QUEUED, Job.DOWNLOADING,
                                                      Job.EXTRACTING, Job.PAUSED)]
            os.makedirs(DATA_DIR, exist_ok=True)
            tmp = QUEUE_PATH + ".tmp"
            with open(tmp, "w") as f:
                json.dump(pend, f)
            os.replace(tmp, QUEUE_PATH)

    @staticmethod
    def saved_queue():
        try:
            with open(QUEUE_PATH) as f:
                return json.load(f)
        except (OSError, ValueError):
            return []

    def resume(self):
        """Re-queue what a previous session (or rb_dl.py) left pending."""
        n = 0
        for it in self.saved_queue():
            if it.get("rom", {}).get("id") and it.get("system"):
                self.enqueue(it["rom"], it["system"])
                n += 1
        return n

    def pause_all(self):
        """Stop transfers but keep them queued (partials resume via Range)."""
        for j in self.jobs:
            if j.active:
                j.pause = True
                j.cancel.set()
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

    def _run(self):
        while True:
            j = self.q.get()
            if j.cancel.is_set():
                j.state = Job.PAUSED if j.pause else Job.CANCELLED
                self.save_queue()
                continue
            try:
                self._process(j)
            except Cancelled:
                j.state = Job.PAUSED if j.pause else Job.CANCELLED
                j.message = "Paused" if j.pause else "Cancelled"
            except Exception as e:  # keep the worker alive whatever happens
                j.state = Job.FAILED
                # our own errors are written to be shown; anything else could carry
                # request details, so only its type goes on screen / in the log
                j.message = (str(e) if isinstance(e, (RommError, OSError, zipfile.BadZipFile))
                             else "Error (%s)" % e.__class__.__name__)[:160]
                log("FAIL %s: %s" % (j.name, j.message))
            self.save_queue()

    # -- transfer ------------------------------------------------------
    def _fetch(self, j, url_path, dest, size_hint):
        """Resumable GET into dest (via dest.part). Updates j progress."""
        part = dest + ".part"
        have = os.path.getsize(part) if os.path.exists(part) else 0
        headers = {"Range": "bytes=%d-" % have} if have else {}
        try:
            r = self.api.s.get(self.api.base + url_path, headers=headers,
                               stream=True, timeout=(10, 60))
        except requests.exceptions.SSLError:
            raise RommError("TLS certificate rejected") from None
        except Exception as e:
            raise RommError("Download failed (%s)" % e.__class__.__name__) from None
        if r.status_code == 416:      # .part already complete (verified below)
            r.close()
            if size_hint and os.path.getsize(part) != size_hint:
                os.remove(part)
                raise RommError("Partial file doesn't match the server's — retry")
            os.replace(part, dest)
            return
        if r.status_code not in (200, 206):
            raise RommError("Download failed: HTTP %d" % r.status_code)
        if r.status_code == 200:
            have = 0
        base = j.done_bytes
        j.done_bytes = base + have
        t0, b0 = time.time(), j.done_bytes
        with open(part, "ab" if have else "wb") as f:
            for chunk in r.iter_content(1 << 20):
                if j.cancel.is_set():
                    r.close()
                    raise Cancelled()
                f.write(chunk)
                j.done_bytes += len(chunk)
                dt = time.time() - t0
                if dt >= 1.0:
                    j.speed = (j.done_bytes - b0) / dt
                    t0, b0 = time.time(), j.done_bytes
        if size_hint and os.path.getsize(part) != size_hint:
            got = os.path.getsize(part)
            os.remove(part)      # don't resume from a corrupt partial
            raise RommError("Size mismatch (%d != %d bytes)" % (got, size_hint))
        os.replace(part, dest)

    def _free_bytes(self, path):
        st = os.statvfs(path)
        return st.f_bavail * st.f_frsize

    def _process(self, j):
        rom, system = j.rom, j.system
        sysdir = os.path.join(ROMS_ROOT, system)
        os.makedirs(sysdir, exist_ok=True)
        detail = self.api.rom(rom["id"])
        j.rom = rom = dict(rom, **detail)
        files = [f for f in (detail.get("files") or [])
                 if f.get("category") in (None, "game")]
        j.total_bytes = sum(f.get("file_size_bytes") or 0 for f in files) or \
            detail.get("fs_size_bytes") or 0
        # extracted disc games are bigger than the zip; leave a margin
        need = j.total_bytes * 3 + (2 << 30)
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
                if os.path.exists(dest) and os.path.getsize(dest) == f.get("file_size_bytes"):
                    j.done_bytes += f.get("file_size_bytes") or 0
                    continue
                self._fetch(j, "/api/roms/%d/files/content/%s" % (
                    f["id"], urllib.parse.quote(f["file_name"])), dest, f.get("file_size_bytes"))
            results = self._postprocess_folder(j, folder)
        else:
            dest = os.path.join(sysdir, fs_name)
            self._fetch(j, "/api/roms/%d/content/%s" % (
                rom["id"], urllib.parse.quote(detail["fs_name"])), dest, j.total_bytes)
            results = self._postprocess_file(j, dest)

        self.library.mark(rom, system, results)
        self._queue_gamelist(rom, system, results)
        j.chd_queued = self._chd_queued
        j.state = Job.DONE
        j.message = "Ready to play" + (" (CHD later)" if self._chd_queued else "")
        log("OK %s -> %s" % (j.name, results))

    # -- post-processing -------------------------------------------------
    _chd_queued = False

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
                for info in z.infolist():
                    dest = os.path.realpath(os.path.join(target, info.filename))
                    if not dest.startswith(real + os.sep):
                        raise RommError("Unsafe path in archive: %s" % info.filename)
                    if j.cancel.is_set():
                        raise Cancelled()
                    z.extract(info, target)
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
        with open(os.path.join(CHD_QUEUE_DIR, name), "w") as f:
            json.dump(task, f)
        self._chd_queued = True

    def _queue_gamelist(self, rom, system, paths):
        """Hand gamelist metadata to the launcher's post-exit merge step
        (ES must not be written to while it runs — see rb_finalize.py)."""
        md = rom.get("metadatum") or {}
        entry = {
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
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(GAMELIST_QUEUE, "a") as f:
            f.write(json.dumps(entry) + "\n")
