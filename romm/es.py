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
    can't be asked, "es" ES is busy itself (scraping, hashing...). /isIdle
    first; without it (older ES), the running game alone decides. While
    this app runs /isIdle says False whatever ES does, so then only the
    running game counts."""
    idle = es_is_idle(get)
    if idle:
        return ""
    path = es_running_game(app_busy=True, get=get)
    if path is None:
        return "game"
    if path:
        return "game" if app_busy or not path.endswith("rommbrowser.pygame") else ""
    return "es" if idle is False else ""


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
                lp = g.get("lastplayed") or ""
                out.append({"system": sysname, "path": g["path"], "name": g.get("name") or
                            os.path.splitext(os.path.basename(g["path"]))[0],
                            "playcount": int(float(g.get("playcount") or 0)),
                            "lastplayed": lp if lp[:1].isdigit() and lp != "0" else ""})
        return out
    except _core.RommError:
        raise
    except Exception as e:
        raise _core.RommError("EmulationStation didn't answer (%s)" % e.__class__.__name__)


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
