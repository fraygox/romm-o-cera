"""EmulationStation: its HTTP API, playlists and a game's files."""
import json
import os
import shutil
import urllib.parse
import xml.etree.ElementTree as ET

from . import core as _core, discs as _discs


ES_API = os.environ.get("ROMM_BROWSER_ES", "http://127.0.0.1:1234")     # Batocera ES's HTTP API


# ---------------------------------------------------------------- the ES client
# Every call to ES's HTTP API goes through EsClient: one timeout rule, replies
# as EsReply (status + body, read as text or JSON), and one error, EsError,
# whatever went wrong. The names below it (es_get, es_notify...) are thin
# wrappers that older code and the workers rely on.

class EsError(_core.RommError):
    """ES couldn't be asked (not running, timed out: .status None), answered
    with an HTTP error (.status 4xx/5xx) or with JSON that isn't JSON."""

    def __init__(self, path, status=None, reason=""):
        self.path, self.status, self.reason = path, status, reason
        super().__init__("EmulationStation didn't answer (%s)" % reason if status is None or reason
                         else "EmulationStation answered HTTP %d to %s" % (status, path.split("?")[0]))


class EsReply:
    """What ES answered: .status, .body (bytes), .ok (2xx), .text, .json()."""

    __slots__ = ("path", "status", "body")

    def __init__(self, path, status, body):
        self.path, self.status, self.body = path, status, body or b""

    @property
    def ok(self):
        return 200 <= self.status < 300

    @property
    def text(self):
        return self.body.decode("utf-8", "replace")

    def json(self):
        """The body as JSON (empty: None). EsError if it isn't JSON."""
        try:
            return json.loads(self.body or b"null")
        except ValueError:
            raise EsError(self.path, self.status, "not JSON") from None


class EsClient:
    """Batocera EmulationStation's HTTP API (port 1234, localhost only).
    base: ES's address (ES_API, read at each call, by default)."""

    def __init__(self, base=None, timeout=5):
        self.base, self.timeout = base, timeout

    def request(self, method, path, data=None, ctype=None, timeout=None, errors=True):
        """One call: an EsReply. str data goes as UTF-8 text/plain. Raises
        EsError when ES can't be asked, and (errors=True) on an HTTP status
        >= 400; errors=False hands those back as replies (ES uses 404 for
        "nothing changed", for instance)."""
        import urllib.error
        import urllib.request
        if isinstance(data, str):
            data, ctype = data.encode("utf-8"), ctype or "text/plain; charset=utf-8"
        req = urllib.request.Request((self.base or ES_API) + path, data=data, method=method,
                                     headers={"Content-Type": ctype} if ctype else {})
        try:
            try:
                with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                    reply = EsReply(path, r.status, r.read())
            except urllib.error.HTTPError as e:
                try:
                    body = e.read()
                except Exception:
                    body = b""
                finally:
                    e.close()
                reply = EsReply(path, e.code, body)
        except Exception as e:                  # refused, timed out, reset, a bad address
            raise EsError(path, None, e.__class__.__name__) from None
        if errors and reply.status >= 400:
            raise EsError(path, reply.status)
        return reply

    def get(self, path, timeout=None, errors=True):
        return self.request("GET", path, timeout=timeout, errors=errors)

    def post(self, path, data, ctype=None, timeout=None, errors=True):
        return self.request("POST", path, data, ctype, timeout, errors)

    def json(self, path, timeout=None):
        """GET a JSON reply, parsed (empty: None). Raises EsError."""
        return self.get(path, timeout).json()

    def text(self, path, timeout=None):
        """GET a reply as text. Raises EsError."""
        return self.get(path, timeout).text


def es_get(path, timeout=5, base=None):
    """GET from ES's API: (status, body bytes). Raises EsError when ES can't be
    asked or answers with an HTTP error."""
    r = EsClient(base).get(path, timeout)
    return r.status, r.body


def es_running_game(app_busy=False, get=None):
    """Path of the game ES is running ('' when idle); None when ES can't be
    asked. This app itself counts as idle unless app_busy (rb_finalize must
    not reload ES under it). get: the (status, body) fetcher, es_get by default."""
    try:
        status, body = (get or es_get)("/runningGame")
    except Exception:
        return None
    if status != 200 or not body.strip():
        return ""
    try:
        path = json.loads(body).get("path") or ""
    except (ValueError, AttributeError):
        return ""                     # not a JSON object: as if nothing runs
    if not isinstance(path, str):
        return ""
    return "" if path.endswith("rommbrowser.pygame") and not app_busy else path


def es_is_idle(get=None):
    """ES's own word on background work (GET /isIdle, Batocera 36+): False
    while a game runs (this app included) or ES scrapes, installs content,
    computes RetroAchievements hashes or updates Batocera; None when ES can't
    say (an older ES answers 404; not running). get: as in es_running_game."""
    try:
        status, body = (get or es_get)("/isIdle")
        v = json.loads(body)
    except Exception:
        return None
    return v[0] if isinstance(v, list) and len(v) == 1 and isinstance(v[0], bool) else None


def es_busy(app_busy=False, get=None):
    """Why background work (conversions, handing games to ES) should wait:
    "" it may run, "game" a game runs (this app only with app_busy) or ES
    can't be asked, "es" ES is busy itself (scraping, hashing...: /isIdle).
    The running game first; /isIdle after it, so a game that just quit can't
    leave a stale False, and only when nothing runs: while this app runs
    /isIdle says False whatever ES does, so then the app alone counts (busy
    with app_busy, idle without). Without /isIdle (older ES) the running
    game alone decides."""
    path = es_running_game(app_busy=True, get=get)
    if path is None:
        return "game"
    if path:
        return "game" if app_busy or not path.endswith("rommbrowser.pygame") else ""
    return "es" if es_is_idle(get) is False else ""


def _es_json(path, timeout=10):
    return EsClient().json(path, timeout)


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
                if g.get("hidden") == "true" or not g.get("path", "").startswith(_core.ROMS_ROOT + "/"):
                    continue
                out.append(dict(_played(g), system=sysname, path=g["path"], name=g.get("name") or
                                os.path.splitext(os.path.basename(g["path"]))[0]))
        return out
    except _core.RommError:
        raise
    except Exception as e:
        raise _core.RommError("EmulationStation didn't answer (%s)" % e.__class__.__name__)


def _num(v):
    try:
        return max(0, int(float(v or 0)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _played(g):
    """ES's play record of one game: {"playcount", "lastplayed" ("YYYYmmddTHHMMSS"
    or ""), "gametime" (seconds)}."""
    lp = g.get("lastplayed") or ""
    lp = lp if isinstance(lp, str) else ""
    return {"playcount": _num(g.get("playcount")), "lastplayed": lp if lp[:1].isdigit() and lp != "0" else "",
            "gametime": _num(g.get("gametime"))}


def es_play_stats(system, timeout=5):
    """{path: _played record} for every game ES has for a system (hidden
    discs too), {} when ES lists none for it, None when ES can't be asked."""
    try:
        games = EsClient(timeout=timeout).json("/systems/%s/games" % urllib.parse.quote(system))
    except EsError as e:
        return {} if e.status == 404 else None
    return {os.path.normpath(g["path"]): _played(g) for g in games or []
            if isinstance(g, dict) and isinstance(g.get("path"), str)}


def lastplayed_seconds(lp):
    """Epoch seconds of ES's "YYYYmmddTHHMMSS" (local time), None for ""."""
    import time
    try:
        return int(time.mktime(time.strptime(lp, "%Y%m%dT%H%M%S")))
    except (TypeError, ValueError, OverflowError):
        return None


def m3u_entry(line):
    """A playlist line's disc path as written; '' for a blank or # comment line."""
    s = line.strip()
    return "" if s.startswith("#") else s


def m3u_lines(path):
    """The disc paths an .m3u lists, as written (blank and # lines skipped;
    odd bytes in names survive as-is). Raises OSError."""
    with open(path, errors="surrogateescape") as f:
        return [e for e in map(m3u_entry, f) if e]


def m3u_paths(path):
    """m3u_lines() as normalised paths, relative to the playlist's folder."""
    d = os.path.dirname(path)
    return [os.path.normpath(os.path.join(d, e)) for e in m3u_lines(path)]


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
            for q in m3u_paths(p):
                add(q)
        elif low.endswith((".cue", ".gdi")) and os.path.isfile(p):
            for q in _discs.sheet_sources(p):
                add(q)
    add(path)
    rel = os.path.relpath(path, _core.ROMS_ROOT).split(os.sep)
    sysdir = os.path.join(_core.ROMS_ROOT, rel[0]) if len(rel) > 1 else None
    return [p for p in out if sysdir and _core._inside(p, sysdir)]


def delete_game(system, path, library=None):
    """Delete a game ES lists (any game, not only ours): its files, then any
    game folder left empty. Saves and states live elsewhere and are kept.
    Returns (deleted paths, bytes freed)."""
    sysdir = os.path.join(_core.ROMS_ROOT, system)
    files = game_files(path)
    freed = 0
    for p in files:
        if not _core._inside(p, sysdir):
            continue
        freed += _core.path_size(p)
        if os.path.isdir(p) and not os.path.islink(p):
            shutil.rmtree(p)
        elif os.path.lexists(p):
            os.remove(p)
    for p in sorted({os.path.dirname(p) for p in files}, key=len, reverse=True):
        d = p
        while _core._inside(d, sysdir) and os.path.isdir(d) and not os.listdir(d):
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
        _core.append_gamelist({"system": system, "remove": files})
    _core.log("DELETE %s: %s (%s)" % (system, files, _core.human_size(freed)))
    return files, freed


def es_media_present(system, gid, tag, base=None, timeout=15):
    """Does ES have the file of one of a game's media tags? (GET .../media/<tag>, only its
    headers read.) True: ES serves it; False: no such tag, or a path to a file that is gone
    (ES answers 200 with nothing: a game deleted and downloaded again gets its old entry
    back, media paths and all); None: ES can't be asked."""
    import urllib.error
    import urllib.request
    url = "%s/systems/%s/games/%s/media/%s" % (base or ES_API, urllib.parse.quote(system), gid, tag)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return int(r.headers.get("Content-Length") or 0) > 0
    except urllib.error.HTTPError as e:
        e.close()
        return False if e.code == 404 else None
    except Exception:
        return None


def es_notify(msg):
    """Show a popup in EmulationStation (Batocera's ES API). Best effort."""
    try:
        return EsClient().post("/notify", msg).ok
    except EsError:
        return False


def es_messagebox(msg):
    """A message box in EmulationStation that stays until the player closes
    it (POST /messagebox): for problems that need them, not for news (that's
    es_notify). Best effort."""
    try:
        return EsClient().post("/messagebox", msg).ok
    except EsError:
        return False


# Starting a game once this app has quit: ES runs one thing at a time and
# its POST /launch only starts a game when nothing else runs, so a detached
# waiter (stdlib only, its own session) asks for it once ES no longer reports
# the app running and has had a moment to get back to its menus. It gives up
# when anything else starts meanwhile, or when the app hasn't quit within a
# minute. Used to reopen the app after an update, and for Play now.
#
# /launch looks the path up in ES's in-memory game lists (every game system,
# the exact absolute path) and silently does nothing for a path it doesn't
# list: its reply is 200 either way. A game downloaded this session isn't
# listed until rb_finalize hands it to ES, which it does once the app has quit
# and ES is idle, so with `system` the waiter first waits (up to `wait` s)
# for /systems/<system>/games to list the path. ES lists it as soon as
# rb_finalize has reloaded its games, while rb_finalize is still writing
# game info and media (for it and others), so the waiter then also waits,
# within the same `wait`, until rb_finalize is done: its batch (the queue
# and its .work file) is gone, or no rb_finalize holds its lock (read from
# /proc/locks, never taken: a finalize starting then would think another
# runs and skip). A listed game then starts, or ES asks the player first
# (its "missing BIOS" warning waits for an answer), so nothing is checked
# after the launch.
_LAUNCHER = r"""
import json, os, sys, time, urllib.parse, urllib.request
es, path, system, name, wait = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], float(sys.argv[5])
lock, queue = (sys.argv[6], sys.argv[7]) if len(sys.argv) > 7 else ("", "")
def get(p, timeout=10):
    with urllib.request.urlopen(es + p, timeout=timeout) as r:
        return r.read()
def post(p, body):
    urllib.request.urlopen(urllib.request.Request(es + p, data=body.encode("utf-8"), method="POST"), timeout=5)
def running():
    try:
        return json.loads(get("/runningGame", 3) or b"{}").get("path") or ""
    except ValueError:
        return ""
    except Exception:
        return None
def listed():
    try:
        return any(g.get("path") == path for g in json.loads(get("/systems/" + urllib.parse.quote(system) + "/games", 30)))
    except Exception:
        return False
def say(msg):
    try:
        post("/notify", msg)
    except Exception:
        pass
def pending():
    try:
        if os.path.getsize(queue) > 0:
            return True
    except OSError:
        pass
    return os.path.exists(queue + ".work")
def locked():
    try:
        st = os.stat(lock)
    except OSError:
        return False
    key = "%02x:%02x:%d" % (os.major(st.st_dev), os.minor(st.st_dev), st.st_ino)
    try:
        with open("/proc/locks") as f:
            return any(key in line.split() for line in f if "FLOCK" in line)
    except OSError:
        return True                   # can't tell: the batch files say
def finalizing():
    return bool(queue) and pending() and locked()
end = time.time() + 60
while time.time() < end and running() != "":
    time.sleep(0.3)
time.sleep(1.5)                       # ES back in its menus
if time.time() >= end or running() != "":
    sys.exit()
if system:
    end = time.time() + wait
    while not listed():
        if running():                 # the player started something else
            sys.exit()
        if time.time() >= end:
            say("%s isn't in EmulationStation's list yet: start it from %s once it shows." % (name, system))
            sys.exit()
        time.sleep(2)
    while finalizing():               # rb_finalize still writing game info: never under a running game
        if running():
            sys.exit()
        if time.time() >= end:
            say("%s is ready: start it from %s." % (name, system))
            sys.exit()
        time.sleep(1)
    if running():
        sys.exit()
post("/launch", path)
"""


def finalize_lock_path():
    """rb_finalize's lock (rb_finalize.lock_path(); the same file)."""
    return os.path.join(_core.DATA_DIR, "finalize.lock")


def launch_when_closed(path, system=None, name=None, wait=600, python=None):
    """Start the waiter that has ES launch `path` once this app has quit.
    system: the game's system, when ES may not list it yet (Play now: the
    waiter waits for the list, then for rb_finalize to finish); name: the
    game's name for the popup if ES never lists it. True when the waiter is
    running."""
    import subprocess
    import sys
    try:
        subprocess.Popen([python or sys.executable, "-c", _LAUNCHER, ES_API, path, system or "", name or "",
                          str(wait), finalize_lock_path(), _core.GAMELIST_QUEUE], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def gamelist_xml(system, games):
    """A partial gamelist.xml (bytes) for games of a system: {absolute path:
    {tag: text}}; paths are written relative to the system folder."""
    sysdir = os.path.join(_core.ROMS_ROOT, system)
    root = ET.Element("gameList")
    for path, fields in games.items():
        g = ET.SubElement(root, "game")
        ET.SubElement(g, "path").text = "./" + os.path.relpath(path, sysdir)
        for k, v in (fields or {}).items():
            ET.SubElement(g, k).text = str(v)
    return ET.tostring(root, encoding="utf-8")


def es_add_games(system, games, timeout=30):
    """List new games in ES without reloading every system: POST
    /addgames/<system> with a partial gamelist.xml (gamelist_xml). ES adds
    files on disk with an extension the system takes, and merges the given
    tags. The status: 200 added; 201 added to a system ES hadn't loaded (ES
    then reloads everything by itself); 204 nothing taken (unknown files, or
    Batocera 43.1, whose ES rejects every body); 404 no such system; None
    when ES can't be asked."""
    try:
        return EsClient().post("/addgames/" + urllib.parse.quote(system), gamelist_xml(system, games),
                               "application/xml", timeout, errors=False).status
    except EsError:
        return None


# ---------------------------------------------------------------- ES systems

def _es_system_elements():
    """(system dir name, <system> element) for every system, the user's es_systems_*.cfg last."""
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
            if path:
                yield os.path.basename(path), s


def load_es_systems():
    """{system_dir_name: set(extensions)} from Batocera's es_systems.cfg: what ES lists."""
    return {name: set((s.findtext("extension") or "").lower().split()) for name, s in _es_system_elements()}


# what a system is called when ES's own es_systems.cfg can't say (no Batocera, or not listed)
SYSTEM_NAMES = {"psx": "PlayStation", "ps2": "PlayStation 2", "ps3": "PlayStation 3", "psp": "PSP",
                "psvita": "PS Vita", "gamecube": "GameCube", "wii": "Wii", "wiiu": "Wii U", "n64": "Nintendo 64",
                "nds": "Nintendo DS", "3ds": "Nintendo 3DS", "switch": "Switch", "gb": "Game Boy",
                "gbc": "Game Boy Color", "gba": "Game Boy Advance", "nes": "NES", "snes": "Super Nintendo",
                "megadrive": "Mega Drive", "mastersystem": "Master System", "megacd": "Mega-CD",
                "saturn": "Saturn", "dreamcast": "Dreamcast", "xbox": "Xbox", "xbox360": "Xbox 360",
                "pcengine": "PC Engine", "pcenginecd": "PC Engine CD", "neogeocd": "Neo Geo CD",
                "triforce": "Triforce", "zxspectrum": "ZX Spectrum", "pcfx": "PC-FX", "fbneo": "FinalBurn Neo",
                "mame": "MAME"}
_FULLNAMES = {}        # es_systems.cfg in use -> {system: its full name}, read once


def system_name(system):
    """A system's name for people ("PlayStation 2", not "ps2"): ES's own full name for it,
    else SYSTEM_NAMES, else the folder name."""
    src = os.environ.get("ROMM_BROWSER_ES_SYSTEMS", "")
    if src not in _FULLNAMES:
        _FULLNAMES[src] = {name: (s.findtext("fullname") or "").strip() for name, s in _es_system_elements()}
    return _FULLNAMES[src].get(system) or SYSTEM_NAMES.get(system) or system


def batocera_conf():
    """batocera.conf as {key: value} (comments and blank lines skipped); {} when unreadable."""
    out = {}
    try:
        with open(os.path.join(_core.USERDATA, "system", "batocera.conf"), encoding="utf-8",
                  errors="replace") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def load_es_unopenable(conf=None):
    """{system_dir_name: set(extensions)} ES lists but the system's emulator and core can't open:
    the chosen core's incompatible_extensions (N64's default mupen64plus can't open .zip). The
    choice is batocera.conf's <system>.emulator / <system>.core, else the core es_systems marks
    default. Systems with nothing left out aren't in it."""
    conf = batocera_conf() if conf is None else conf
    out = {}
    for name, s in _es_system_elements():
        cores = [(e.get("name"), c) for e in s.iter("emulator") for c in e.iter("core")]
        want_emu, want_core = conf.get(name + ".emulator"), conf.get(name + ".core")
        pick = None
        if want_emu or want_core:
            pick = next((ec for ec in cores if (not want_emu or ec[0] == want_emu) and
                         (not want_core or (ec[1].text or "").strip() == want_core)), None)
            if pick is None and want_emu:
                pick = next((ec for ec in cores if ec[0] == want_emu and ec[1].get("default") == "true"),
                            next((ec for ec in cores if ec[0] == want_emu), None))
        if pick is None:
            pick = next((ec for ec in cores if ec[1].get("default") == "true"), cores[0] if cores else None)
        bad = set((pick[1].get("incompatible_extensions") or "").lower().split()) if pick is not None else set()
        if bad:
            out[name] = bad
        else:
            out.pop(name, None)
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
