"""config.json: the Settings table, defaults, load/save, and the helpers built on it."""
import json
import os
import time
import urllib.parse
from dataclasses import dataclass
from typing import Callable, Optional

from . import core as _core


def _parses(fn):
    """valid= for a value fn() turns into what the app uses: False when it can't."""
    def valid(v):
        try:
            fn(v)
        except (TypeError, ValueError):
            return False
        return True
    return valid


def _str_or_null(v):
    return v is None or isinstance(v, str)


# what Settings cycles through
DOWNLOAD_HOURS = ["any", "1-7", "0-6", "2-8", "22-6", "23-7"]
KEEP_FREE_CHOICES = (0, 5, 10, 20, 50, 100)     # GB
SPEEDS = [0, 50, 20, 10, 5, 2]                  # MB/s while a game runs, 0 = no limit
CACHE_LIMITS_MB = [256, 512, 1024, 2048, 4096, 0]     # 0 = no limit


@dataclass(frozen=True, eq=False)
class Setting:
    """One config.json key. SETTINGS' order is the order config.json is written in."""
    key: str                   # the config.json key (stored on disk: never rename it without RENAMED)
    default: object            # its value until the user changes it
    row: str = ""              # its Settings row id ("" = config.json only)
    label: str = ""            # the row's name, also in messages ("turn on Compress disc games to CHD")
    sub: str = ""              # the row's grey line
    help: str = ""             # what the right panel says about it
    kind: str = ""             # Settings' generic rows: "onoff" (a switch), "choice" (cycles choices); "": by hand
    choices: tuple = ()        # a choice row's values, in the order left/right cycles them
    effects: tuple = ()        # what Settings re-wires after a change (names in SettingsScreen.EFFECTS)
    valid: Optional[Callable] = None   # valid(value) -> False: config.json's value is dropped, the default applies


SETTINGS = (
    Setting("url", "",                  # your RomM, e.g. https://romm.example.com
            row="url", label="Server address", sub="{A} to edit (http:// or https://)",
            valid=_str_or_null,
            help="Your RomM's address, e.g. https://romm.example.com. {A} edits it. Plain http:// sends the token "
                 "unencrypted."),
    Setting("token", "",                # RomM client API token (read scopes)
            row="token", label="API token", sub="Made in RomM: Profile, Client API tokens",
            valid=_str_or_null,
            help="A RomM client API token with read access: in RomM, Profile, Client API tokens. {A} edits it."),
    Setting("verify_tls", True,         # False for a self-signed cert, or a path to a CA bundle
            row="tls", label="Verify TLS certificate", sub="Off only for a self-signed cert",
            help="Checks your RomM's HTTPS certificate. Turn it off only for a self-signed certificate, or point "
                 "it at your own CA bundle in config.json."),
    Setting("verify_tls_ca", ""),       # the CA bundle path last used (Settings cycles back to it)
    Setting("swap_ab", False,           # False: south button = confirm (Batocera default)
            row="swap", label="Confirm button", sub="Which face button means OK",
            help="Which face button confirms. Batocera's default is the bottom one."),
    Setting("button_style", "auto",     # button labels: auto | xbox | playstation | nintendo
            row="style", label="Button labels", sub="Match the symbols on your controller",
            valid=_str_or_null,
            help="Which symbols the hints show. Auto picks them from the controller's name."),
    Setting("check_updates", True,      # once a day, ask GitHub if a newer release exists
            row="checkupdates", label="Check for updates daily", sub="Updates still checks when you ask",
            kind="onoff", effects=("check_updates",),
            help="Once a day, asks GitHub whether a newer version is out; the request says nothing about you. Off,"
                 " Updates still checks when you select it."),
    Setting("chd", True,                # background CHD conversion of extracted disc games
            row="chd", label="Compress disc games to CHD", sub="CD systems, PS2 and PSP, in the background",
            kind="onoff", effects=("downloader",),
            help="Turns CD and DVD games (PlayStation, PS2, PSP, Saturn, Dreamcast…) into CHD in the background. "
                 "Lossless, verified before the original is deleted."),
    Setting("chd_threads", "auto",      # chdman threads: "auto" or a number (see chd_threads())
            row="chdthreads", label="Compression threads", sub="More is faster; fewer keeps the menus snappier",
            help="CPU threads conversions may use while nothing else runs. Auto leaves some for the menus."),
    Setting("chd_low_memory", "auto",   # CDs without chdman's leaky FLAC codec: "auto" (on under 4 GB RAM), true, false
            row="chdlowmem", label="Low-memory mode", sub="Skips chdman's leaky FLAC codec for CDs",
            valid=lambda v: isinstance(v, bool) or v == "auto",
            help="Skips chdman's FLAC codec, which leaks memory on some builds: CD audio comes out a bit bigger, "
                 "still lossless. Auto turns it on under 4 GB of RAM."),
    Setting("keep_free_gb", 20,         # downloads and CHD conversions never leave less than this free (GB)
            row="keepfree", label="Keep free on disk", sub="Downloads wait instead of filling the drive",
            kind="choice", choices=KEEP_FREE_CHOICES, effects=("downloader",),
            valid=_parses(lambda v: float(v or 0)),     # 0: no reserve; null/"" show as Nothing but keep 20 GB (keep_free_bytes)
            help="Downloads and conversions never leave less than this free. One that doesn't fit waits for room; "
                 "smaller ones keep going."),
    Setting("extract_parallel", True,   # the next download starts while the last one is being extracted
            row="overlap", label="Download while extracting", sub="Next download starts during unpacking",
            kind="onoff", effects=("downloader",),
            help="The next game downloads while one unpacks. Turn it off on slow storage (SD cards, USB sticks)."),
    Setting("rvz", False,               # convert GameCube/Wii images to RVZ (downloads dolphin-tool when turned on)
            row="rvz", label="GameCube and Wii to RVZ",
            help="Turns GameCube and Wii images into RVZ, usually 30-60% smaller and lossless; the original is "
                 "deleted once the copy is verified. Downloads dolphin-tool (about 8 MB) once."),
    Setting("xbox_bootable", True,      # Xbox: keep only the game partition xemu boots; "X.xiso" -> "X.iso" (ES lists .iso)
            row="xboxboot", label="Make Xbox images bootable", sub="Game partition only; .xiso renamed to .iso",
            kind="onoff", effects=("downloader",),
            help="xemu boots only a disc's game partition (a full image says \"Please insert an Xbox disc\"). This "
                 "keeps just that part and renames .xiso to .iso so EmulationStation lists them."),
    Setting("squashfs", False,          # pack Xbox images into zstd squashfs (Batocera mounts them at launch)
            row="squashfs", label="Xbox to squashfs", sub="Packed smaller; Batocera opens it at launch",
            kind="onoff", effects=("downloader",),
            help="Packs Xbox games into squashfs, usually 15-45% smaller, which Batocera opens at launch. "
                 "Lossless, verified before the original is deleted. Full disc images need Make Xbox images "
                 "bootable."),
    Setting("z3ds", True,               # compress decrypted 3DS cartridge images to Azahar's .zcci (lossless)
            row="z3ds", label="3DS to .zcci", sub="Compressed copies Azahar opens directly",
            kind="onoff", effects=("downloader",),
            help="Compresses decrypted 3DS games to Azahar's .zcci, often 40-60% smaller. Lossless, verified "
                 "before the original is deleted; encrypted dumps are left alone."),
    Setting("zar", False,               # Xbox 360 images -> .zar for xenia-canary (downloads two small tools)
            row="zar", label="Xbox 360 to .zar",
            help="Turns Xbox 360 images into .zar for xenia-canary, often 40-60% smaller. It keeps the game's "
                 "files, not the whole disc, each checked before the image is deleted. Downloads two small tools "
                 "once."),
    Setting("wux", True,                # Wii U .wud -> .wux, lossless, no keys (downloads wuatool when turned on)
            row="wux", label="Wii U to .wux", sub="Lossless; Cemu opens .wux directly",
            help="Compresses Wii U disc images (.wud) into .wux for Cemu, often much smaller. Lossless and needs "
                 "no keys: checked byte for byte before the .wud is deleted. Needs wuatool (about 2 MB), "
                 "downloaded by itself when needed."),
    Setting("wua", False,               # decrypt Wii U .wud/.wux -> .wua with the user's own disc keys (Cemu)
            row="wua", label="Decrypt Wii U discs", sub="Uses disc keys for games you own",
            help="Turns Wii U discs into .wua for Cemu with the disc keys of games you own: in Cemu's keys.txt, "
                 "bios/wiiukeys or RomM's Wii U firmware (none come with the app). Checked first; a .wua can't "
                 "become the disc image again. A disc without its key becomes a .wux."),
    Setting("ps3_folders", True,        # PS3 disc images (.iso, zipped .iso) -> "Name.ps3" game folders
            row="ps3folders", label="PS3 games as folders",
            sub="Disc images become folders RPCS3 can update",
            kind="onoff", effects=("downloader",),
            help="Turns PS3 disc images (.iso, zipped or not) into .ps3 folders, the layout RPCS3 installs updates"
                 " and DLC for. Checked before the image is deleted. Encrypted discs need Decrypt PS3 discs."),
    Setting("ps3_decrypt", False,       # decrypt encrypted PS3 discs with the user's own disc keys (bios/ps3keys)
            row="ps3decrypt", label="Decrypt PS3 discs", sub="Uses disc keys for games you own",
            help="Decrypts PS3 discs with the disc keys of games you own: put them in bios/ps3keys or RomM's PS3 "
                 "firmware (none come with the app). A wrong key is never used."),
    Setting("cache_limit_mb", 1024,     # cover cache size cap; least recently used covers go first (0 = no cap)
            row="cachelimit", label="Cover cache limit", sub="Least recently seen covers are removed first",
            kind="choice", choices=CACHE_LIMITS_MB, effects=("cache_limit",),
            valid=_parses(int),
            help="How much space game covers may take. The least recently seen go first."),
    Setting("download_hours", "any",    # "any", or "S-E" local hours (e.g. "1-7"): downloads only run then
            row="hours", label="Download hours", sub="Only download in this window (e.g. overnight)",
            kind="choice", choices=DOWNLOAD_HOURS, effects=("downloader", "wake"),
            valid=lambda v: v == "any" or parse_hours(v) is not None,
            help="Downloads only start inside this window, e.g. overnight; one still running pauses when it "
                 "closes. Download now in Downloads skips the wait."),
    Setting("background_downloads", True,  # keep downloading after the app exits
            row="bgdl", label="Keep downloading after exit", sub="Finishes the queue while you play",
            kind="onoff",
            help="When you leave RomM-o-Cera, the queue keeps going in the background, also while you play."),
    Setting("speed_while_playing", 0,   # MB/s cap for background downloads while a game runs (0 = no cap)
            row="speed", label="Download speed while playing", sub="Keeps online play and streaming smooth",
            kind="choice", choices=SPEEDS,
            valid=_parses(lambda v: float(v or 0)),
            help="Caps background downloads while a game runs, so online play and streaming stay smooth."),
    Setting("notify", True,             # EmulationStation popup when background downloads finish/fail
            row="notify", label="Popup when downloads finish", sub="In EmulationStation, after you exit",
            kind="onoff",
            help="A popup in EmulationStation when background downloads finish."),
    Setting("manuals", True,            # fetch the game's manual (PDF) from RomM for ES's manual viewer
            row="manuals", label="Download manuals", sub="PDFs from RomM, in the game's menu in ES",
            kind="onoff", effects=("downloader",),
            help="Brings each game's PDF manual from RomM; it opens from the game's menu in EmulationStation."),
    Setting("sounds", "normal",         # UI sounds: normal | quiet | off
            row="sounds", label="Sounds", sub="Clicks and chimes in this app",
            kind="choice", choices=("normal", "quiet", "off"), effects=("sounds",),
            valid=lambda v: v in ("normal", "quiet", "off"),
            help="Clicks and chimes while you browse."),
    Setting("theme", "default",         # colours: a THEMES key (rbui/theme.py), e.g. default, xbox_oled, synthwave
            row="theme", label="Theme", sub="Colours for the whole app, previewed live",
            help="Colours for the whole app. {A} shows every theme, previewed live: {A} keeps one, {B} goes back, "
                 "{Y} picks its OLED version. Left and right step through them here."),
    Setting("update_channel", "stable",  # stable: releases only; nightly: also the nightly build
            row="channel", label="Update channel", sub="Stable releases, or nightly builds",
            kind="choice", choices=("stable", "nightly"), effects=("update_channel",),
            valid=lambda v: v in ("stable", "nightly"),
            help="Stable gets tested releases. Nightly also gets builds of the newest changes: fixes sooner, less "
                 "tested."),
    Setting("theme_auto", False,        # True: a PlayStation / Xbox / Nintendo pad picks the theme made for it
            row="themeauto", label="Match theme to controller", sub="Follows a PlayStation, Xbox or Nintendo pad",
            kind="onoff", valid=lambda v: isinstance(v, bool),
            help="A PlayStation, Xbox or Nintendo controller shows the theme made for it instead of yours. High "
                 "contrast and Custom are always kept."),
    # Settings → Theme → Custom: the player's own theme, every colour made from these four (rbui/theme.py's
    # custom_palette); "theme": "custom" uses it
    Setting("theme_custom_accent", 195,     # the accent's hue in degrees (0 red, 120 green, 240 blue)
            valid=lambda v: type(v) is int and 0 <= v < 360),
    Setting("theme_custom_tint", 225,       # the hue the backgrounds and greys lean toward, degrees
            valid=lambda v: type(v) is int and 0 <= v < 360),
    Setting("theme_custom_strength", 30,    # how far they lean, 0 (neutral grey) to 100
            valid=lambda v: type(v) is int and 0 <= v <= 100),
    Setting("theme_custom_mode", "dark",    # dark | oled (pure black) | light
            valid=lambda v: v in ("dark", "oled", "light")),
    Setting("platform_map", {},         # per-slug overrides of PLATFORM_MAP (null hides)
            valid=_parses(lambda v: dict(v or {}))),
    Setting("notes_seen", "",           # the version whose release notes What's new last showed
            valid=lambda v: isinstance(v, str)),
    Setting("welcome_seen", False,      # the first-run Welcome was finished or put off (Later)
            valid=lambda v: isinstance(v, bool)),
)
SETTING = {s.key: s for s in SETTINGS}
DEFAULT_CONFIG = {s.key: s.default for s in SETTINGS}     # a plain dict: old workers and tests read it


# config.json carries "config_version" (written last). A file without it is
# version 1: every file saved before the key existed.
CONFIG_VERSION = 2
RENAMED = {}               # old config.json key -> its new name; load_config moves the value over
_logged_once = set()


def log_once(key, msg):
    """log(msg) the first time this process sees key (workers load the config per disc)."""
    if key not in _logged_once:
        _logged_once.add(key)
        _core.log(msg)


def config_value(cfg, key):
    """cfg[key], or its default when cfg (a partial dict, or None) doesn't have it."""
    return (cfg or {}).get(key, DEFAULT_CONFIG[key])


# Environment overrides (handy for testing / other launchers); config.json wins
# for anything the Settings screen saves, env wins over both when set.
ENV_OVERRIDES = {"ROMM_URL": "url", "ROMM_TOKEN": "token", "ROMM_VERIFY_TLS": "verify_tls"}


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
    hb = parse_hours(config_value(cfg, "download_hours"))
    if not hb:
        return True
    hr = time.localtime(now).tm_hour
    a, b = hb
    return a <= hr < b if a < b else (hr >= a or hr < b)


def auto_chd_threads(n=None):
    """chdman runs only while no game is up, at nice 19. Plenty of threads
    (6+): leave 2 for EmulationStation and the system. Few (3-5, e.g. a Pi
    or a small handheld): leave 1 — 2 would halve the work for little gain.
    1-2 threads: 1."""
    n = n or _core.cpu_threads()
    if n >= 6:
        return n - 2
    if n >= 3:
        return n - 1
    return 1


def chd_threads(cfg):
    v = config_value(cfg, "chd_threads")
    try:
        v = int(v)
    except (TypeError, ValueError):
        return auto_chd_threads()
    return max(1, min(v, _core.cpu_threads()))


def chd_low_memory(cfg):
    """True: compress CDs without FLAC (cdlz,cdzl). Default: on for low-RAM boxes."""
    v = config_value(cfg, "chd_low_memory")
    if isinstance(v, bool):
        return v
    return _core.low_ram()


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


def tls_choices(cfg):
    """What Verify TLS certificate steps through: on, off, and the CA bundle last used, if any."""
    cur = norm_verify(cfg.get("verify_tls", True))
    ca = cur if isinstance(cur, str) else (cfg.get("verify_tls_ca") or "")
    return [True, False] + ([ca] if ca else [])


def set_verify(cfg, value):
    """Sets verify_tls, remembering the CA bundle in verify_tls_ca when leaving it (so it can
    be picked again)."""
    cur = norm_verify(cfg.get("verify_tls", True))
    if isinstance(cur, str):
        cfg["verify_tls_ca"] = cur
    cfg["verify_tls"] = value


def step_verify(cfg, step):
    """Verify TLS certificate one choice left (-1) or right (+1), wrapping."""
    cycle = tls_choices(cfg)
    cur = norm_verify(cfg.get("verify_tls", True))
    i = next((k for k, v in enumerate(cycle) if v == cur), 0)
    set_verify(cfg, cycle[(i + step) % len(cycle)])


def clean_server_url(v):
    """A typed server address as the app keeps it: https:// added when there's no scheme,
    no trailing slash. Raises RommError for one carrying user:password@."""
    v = (v or "").strip()
    if v and "://" not in v:
        v = "https://" + v
    if url_has_userinfo(v):
        raise _core.RommError("Leave user:password out of the URL — the token is the login")
    return v.rstrip("/")


def env_configured():
    """The server and token come from the environment (another launcher): no first-run Welcome."""
    return bool(os.environ.get("ROMM_URL") and os.environ.get("ROMM_TOKEN"))


def url_has_userinfo(url):
    return bool(urllib.parse.urlsplit(url or "").username)


def display_url(url, mask="***@"):
    """URL for the screen: never shows user:password@ (the token is the login); mask="" drops
    it altogether (a QR code's address)."""
    u = urllib.parse.urlsplit(url or "")
    if u.username or u.password:
        host = u.hostname or ""
        if u.port:
            host += ":%d" % u.port
        url = urllib.parse.urlunsplit((u.scheme, mask + host, u.path, u.query, u.fragment))
    return url


def _read_config_file():
    """config.json's object, or None when there's none (or it can't be opened).
    Raises ValueError when it isn't a readable JSON object."""
    try:
        with open(_core.CONFIG_PATH) as f:
            data = json.load(f)
    except OSError:
        return None
    if not isinstance(data, dict):
        raise ValueError("not an object")
    return data


def _migrate_config(data):
    """A config.json of any version, with its keys under today's names. Runs on
    every load: a rollback to an older release saves without config_version, so
    the next start migrates again (a rename only fills a key that isn't there).
    A newer release's file loads as it is."""
    data.pop("config_version", None)
    for old, new in RENAMED.items():
        if old in data:
            v = data.pop(old)
            if new not in data:
                data[new] = v
                log_once("renamed " + old, "config.json: %s is now %s" % (old, new))
    return data


def _check_config(data):
    """Drops values the app can't use (the wrong type), so their default
    applies; each key is logged once per process. Only values the app already
    read as the default, or that broke it, are dropped: an on/off key keeps
    whatever it holds ("false" is on, as it always was)."""
    for k in list(data):
        st = SETTING.get(k)
        if st and st.valid and not st.valid(data[k]):
            log_once("invalid " + k, "config.json: %s isn't usable (%s); using the default"
                     % (k, type(data[k]).__name__))
            del data[k]
    return data


def _from_env():
    """{key: value} the environment sets (ENV_OVERRIDES), as load_config reads them."""
    out = {}
    for env, key in ENV_OVERRIDES.items():
        v = os.environ.get(env)
        if v:
            out[key] = norm_verify(v) if key == "verify_tls" else v.strip() if key == "token" else v
    return out


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        data = _read_config_file()
    except ValueError:
        _core._keep_unreadable(_core.CONFIG_PATH)
        data = None
    if data is not None:
        cfg.update(_check_config(_migrate_config(data)))
    cfg.update(_from_env())
    cfg["verify_tls"] = norm_verify(cfg.get("verify_tls", True))
    cfg["token"] = (cfg.get("token") or "").strip()
    return cfg


def config_unreadable():
    """True when config.json exists but isn't a readable JSON object."""
    try:
        _read_config_file()
    except ValueError:
        return True
    return False


def reset_config():
    """Start over from the defaults (the unreadable file is kept beside it)."""
    _core._keep_unreadable(_core.CONFIG_PATH)
    save_config(dict(DEFAULT_CONFIG), force=True)
    _core.log("config.json reset to defaults")


def save_config(cfg, force=False):
    """Persist the user-editable keys (atomic, 0600: it holds the token).
    Refuses (ConfigUnreadable) to write over a config.json that can't be read,
    unless force (reset_config)."""
    if not force and config_unreadable():
        _core._keep_unreadable(_core.CONFIG_PATH)
        raise _core.ConfigUnreadable("config.json can't be read; not saving over it")
    os.makedirs(_core.DATA_DIR, exist_ok=True)
    keep = {k: cfg[k] for k in DEFAULT_CONFIG if k in cfg}
    env = _from_env()
    if env:                    # ROMM_URL / ROMM_TOKEN values never land in the file: it keeps its own
        try:
            before = _read_config_file() or {}
        except ValueError:
            before = {}
        for key, v in env.items():
            if keep.get(key) == v:
                keep.pop(key)
                if key in before:
                    keep[key] = before[key]
    keep["config_version"] = CONFIG_VERSION
    _core.atomic_write_json(_core.CONFIG_PATH, keep, mode=0o600, indent=1)


def cache_limit_bytes(cfg):
    try:
        mb = int(config_value(cfg, "cache_limit_mb"))
    except (TypeError, ValueError):
        mb = DEFAULT_CONFIG["cache_limit_mb"]
    return max(0, mb) << 20


def keep_free_bytes(cfg):
    """Space downloads and conversions must leave free (Settings, default 20 GB)."""
    v = config_value(cfg, "keep_free_gb")
    try:
        return max(0, int(float(v) * (1 << 30)))
    except (TypeError, ValueError):
        return DEFAULT_CONFIG["keep_free_gb"] << 30
