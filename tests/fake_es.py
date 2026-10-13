"""A small stand-in for Batocera EmulationStation's HTTP API (port 1234),
modelled on what Batocera 43.1 was observed to do (2026-10-10 trial):

- GET  /systems/<sys>/games         games ES lists (files on disk with a valid
                                    extension), metadata from gamelist.xml or
                                    bare (name = file stem) for new files
- POST /systems/<sys>/games/<id>    JSON of string fields; only those change;
                                    200 if something changed, else 404 (sic)
- POST /systems/<sys>/games/<id>/media/<tag>   bytes -> images/<stem>-<tag>.ext
- GET  /reloadgames                 save in-memory lists, then reload from disk
- GET  /runningGame                 {"msg": "NO GAME RUNNING"} unless .running set
- GET  /systems                     systems with games (name, gamesystem, totalGames)
- POST /notify                      text/plain popup: 200, empty body; 400 if no body
- POST /addgames/<sys>              partial gamelist.xml: games ES lists get the given
                                    tags; files on disk with a valid extension are added.
                                    200 "OK"; 204 nothing taken; 404 unknown system; 400 no
                                    body; a system with no games yet is loaded, saved and
                                    every list reloaded: 201. `addgames_bug` = Batocera
                                    43.1, whose ES answers 204 to every body.
- GET  /isIdle                      200 "[ true ]", or 201 "[ false ]" while a game runs or
                                    `busy` (scraping, hashing, installing, updating);
                                    `has_isidle = False` = before Batocera 36: 404
- POST /messagebox                  text/plain message box: 200, empty body; 400 if no body
- POST /launch                      text/plain path: 200, empty body, always; the game
                                    starts (`running`) only if ES lists that exact path
                                    (`launch_any = True`, the default here: any path, e.g.
                                    the app itself), and `launch_works = False` starts
                                    nothing (as when ES first asks the player, e.g. its
                                    missing-BIOS warning) at all

All of it as in batocera-emulationstation's es-app/src/services/HttpServerThread.cpp.

Unknown GET paths fall through to ES's static-file route: 404 "404 not found"
(text/html); unknown POST paths get httplib's bare 404.

`save()` writes the in-memory lists over gamelist.xml whenever a test wants —
the way ES's background saves overwrote edits made behind its back.
"""
import hashlib
import json
import os
import re
import threading
import urllib.parse
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TEXT = ("name", "desc", "releasedate", "developer", "publisher", "genre", "players", "rating", "region",
        "lang", "family", "hidden", "favorite", "playcount", "lastplayed", "gametime")
MEDIA = ("image", "thumbnail", "marquee", "video", "fanart", "boxback", "bezel", "manual", "map",
         "titleshot", "wheel", "boxart", "cartridge", "magazine")
EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "video/mp4": ".mp4",
       "application/pdf": ".pdf"}


class FakeES:
    def __init__(self, roms_root, systems):
        self.roms, self.systems = roms_root, systems    # {system: {".ext", ...}}
        self.mem = {}                                    # system -> {abs path: {field: value}}
        self.running = None
        self.posts = []
        self.notes = []                                  # /notify texts
        self.messages = []                               # /messagebox texts
        self.launched = []                               # /launch paths
        self.calls = []                                  # (method, path) of every request
        self.busy = False                                # scraping, hashing, installing...
        self.has_isidle = True                           # False: an ES before /isIdle
        self.addgames_bug = False                        # True: Batocera 43.1's /addgames
        self.launch_any = True                           # False: /launch only starts listed paths
        self.launch_works = True                         # False: /launch starts nothing
        self.lock = threading.Lock()
        self.reload()
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body=b"", ctype="application/json"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                p = self.path.split("?")[0]
                fake.calls.append(("GET", p))
                if p == "/isIdle":
                    if not fake.has_isidle:
                        return self._send(404, b"404 not found", "text/html")
                    idle = not fake.running and not fake.busy
                    return self._send(200 if idle else 201, b"[ true ]" if idle else b"[ false ]")
                if p == "/runningGame":
                    body = json.dumps({"path": fake.running} if fake.running else {"msg": "NO GAME RUNNING"})
                    return self._send(200 if fake.running else 201, body.encode())
                if p == "/systems":
                    with fake.lock:
                        body = [{"name": k, "gamesystem": "true", "collection": "false",
                                 "totalGames": len(v)} for k, v in fake.mem.items()]
                    return self._send(200, json.dumps(body).encode())
                if p == "/reloadgames":
                    fake.save()
                    fake.reload()
                    return self._send(200, b"")
                m = re.match(r"^/systems/([^/]+)/games(?:/([0-9a-f]+))?$", p)
                if m:
                    with fake.lock:
                        games = fake.mem.get(m.group(1))
                        if games is None:
                            return self._send(404, b"404 not found", "text/html")
                        out = [fake.json(m.group(1), path, md) for path, md in games.items()]
                    if m.group(2):
                        g = next((x for x in out if x["id"] == m.group(2)), None)
                        return self._send(200, json.dumps(g).encode()) if g else self._send(404, b"")
                    return self._send(200, json.dumps(out).encode())
                self._send(404, b"404 not found", "text/html")

            def do_POST(self):
                p = self.path.split("?")[0]
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                fake.calls.append(("POST", p))
                if p in ("/notify", "/messagebox") or p.startswith("/addgames/"):
                    if not body:
                        return self._send(400, b"400 bad request - body is missing", "text/html")
                if p == "/launch":                       # ES starts the game (here: just says it runs)
                    path = body.decode("utf-8")
                    fake.launched.append(path)
                    with fake.lock:
                        known = any(path in games for games in fake.mem.values())
                    if fake.launch_works and (fake.launch_any or known):
                        fake.running = path
                    return self._send(200, b"")
                if p in ("/notify", "/messagebox"):
                    (fake.notes if p == "/notify" else fake.messages).append(body.decode("utf-8"))
                    return self._send(200, b"")
                if p.startswith("/addgames/"):
                    code, text = fake.add_games(urllib.parse.unquote(p[len("/addgames/"):]), body)
                    return self._send(code, text.encode(), "text/html")
                m = re.match(r"^/systems/([^/]+)/games/([0-9a-f]+)/media/([a-z]+)$", p)
                if m:
                    path = fake.find(m.group(1), m.group(2))
                    ext = EXT.get(self.headers.get("Content-Type"))
                    if not path or not ext or m.group(3) not in MEDIA:
                        return self._send(404, b"404 media not found", "text/html")
                    d = os.path.join(fake.roms, m.group(1), "videos" if m.group(3) == "video" else
                                     "manuals" if m.group(3) == "manual" else "images")
                    os.makedirs(d, exist_ok=True)
                    stem = os.path.splitext(os.path.basename(path))[0]
                    f = os.path.join(d, "%s-%s%s" % (stem, m.group(3), ext))
                    open(f, "wb").write(body)
                    with fake.lock:
                        fake.mem[m.group(1)][path][m.group(3)] = "./" + os.path.relpath(f, os.path.join(fake.roms, m.group(1)))
                    fake.posts.append(("media", m.group(1), stem, m.group(3)))
                    return self._send(200, b"")
                m = re.match(r"^/systems/([^/]+)/games/([0-9a-f]+)$", p)
                if m:
                    path = fake.find(m.group(1), m.group(2))
                    try:
                        data = json.loads(body)
                    except ValueError:
                        data = None
                    if not path or not isinstance(data, dict):
                        return self._send(404, b"404 game not found", "text/html")
                    changed = False
                    with fake.lock:
                        md = fake.mem[m.group(1)][path]
                        for k, v in data.items():
                            if k in TEXT and isinstance(v, str) and md.get(k) != v:
                                md[k] = v
                                changed = True
                    fake.posts.append(("meta", m.group(1), os.path.basename(path), data))
                    return self._send(200 if changed else 404, b"OK" if changed else b"404 game not found")
                self._send(404, b"404 not found", "text/html")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True).start()   # quick shutdown()

    # -- ES-ish behaviour
    @staticmethod
    def gid(path):
        return hashlib.md5(path.encode()).hexdigest()

    def find(self, system, gid):
        for path in self.mem.get(system, {}):
            if self.gid(path) == gid:
                return path
        return None

    def json(self, system, path, md):
        g = {"id": self.gid(path), "path": path, "systemName": system,
             "name": md.get("name") or os.path.splitext(os.path.basename(path))[0]}
        for k, v in md.items():
            if k in MEDIA:
                g[k] = "/systems/%s/games/%s/media/%s" % (system, self.gid(path), k)
            elif k != "name":
                g[k] = v
        return g

    def reload(self):
        """Like ES: list files with a valid extension; info from gamelist.xml
        for those (entries for missing files are ignored), bare otherwise."""
        with self.lock:
            self.mem = {}
            for system, exts in self.systems.items():
                sysdir = os.path.join(self.roms, system)
                if not os.path.isdir(sysdir):
                    continue
                files = {}
                for dp, dns, fns in os.walk(sysdir):
                    dns[:] = [d for d in dns if d not in ("images", "videos", "manuals")]
                    for fn in fns:
                        if os.path.splitext(fn)[1].lower() in exts:
                            files[os.path.join(dp, fn)] = {}
                if not files:
                    continue
                try:
                    for g in ET.parse(os.path.join(sysdir, "gamelist.xml")).getroot().findall("game"):
                        p = os.path.normpath(os.path.join(sysdir, g.findtext("path") or ""))
                        if p in files:
                            files[p] = {c.tag: c.text or "" for c in g if c.tag != "path"}
                except (OSError, ET.ParseError):
                    pass
                self.mem[system] = files

    def save(self):
        """Write the in-memory lists over gamelist.xml (as ES does on its own):
        entries for listed games are rewritten from memory; entries ES doesn't
        list (missing files) are left as they were — like ES's updateGamelist."""
        with self.lock:
            for system, games in self.mem.items():
                gl = os.path.join(self.roms, system, "gamelist.xml")
                try:
                    root = ET.parse(gl).getroot()
                except (OSError, ET.ParseError):
                    root = ET.Element("gameList")
                sysdir = os.path.join(self.roms, system)
                for g in list(root.findall("game")):
                    if os.path.normpath(os.path.join(sysdir, g.findtext("path") or "")) in games:
                        root.remove(g)
                for path, md in games.items():
                    g = ET.SubElement(root, "game")
                    ET.SubElement(g, "path").text = "./" + os.path.relpath(path, sysdir)
                    for k, v in md.items():
                        ET.SubElement(g, k).text = v
                ET.ElementTree(root).write(gl, encoding="utf-8", xml_declaration=True)

    def add_games(self, system, body):
        """ES's /addgames: (status, text)."""
        if self.addgames_bug:
            return 204, "204 No game added / updated"
        exts = self.systems.get(system)
        if exts is None:
            return 404, "404 System not found"
        sysdir = os.path.join(self.roms, system)
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            root = None
        added = {}
        with self.lock:
            games = self.mem.get(system, {})
            for g in root.findall("game") if root is not None and root.tag == "gameList" else []:
                path = os.path.normpath(os.path.join(sysdir, g.findtext("path") or ""))
                if path not in games and not (os.path.splitext(path)[1].lower() in exts and os.path.isfile(path)
                                              and path.startswith(sysdir + os.sep)):
                    continue                      # "does not exist or is arcade asset ! Ignoring."
                md = dict(games.get(path, {}))
                md.update({c.tag: c.text or "" for c in g if c.tag != "path"})
                added[path] = md
            if not added:
                return 204, "204 No game added / updated"
            new_system = system not in self.mem
            self.mem.setdefault(system, {}).update(added)
        if new_system:              # loaded just for this: its gamelist saved, then every list reloaded
            self.save()
            self.reload()
            return 201, "201 Game added. System not updated"
        return 200, "OK"

    def game(self, system, name):
        """The in-memory metadata of the game whose file name is `name`."""
        for path, md in self.mem.get(system, {}).items():
            if os.path.basename(path) == name:
                return md
        return None

    def close(self):
        self.server.shutdown()
