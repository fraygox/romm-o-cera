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

`save()` writes the in-memory lists over gamelist.xml whenever a test wants —
the way ES's background saves overwrote edits made behind its back.
"""
import hashlib
import json
import os
import re
import threading
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TEXT = ("name", "desc", "releasedate", "developer", "publisher", "genre", "players", "rating", "region",
        "lang", "family", "hidden", "favorite", "playcount", "lastplayed", "gametime")
MEDIA = ("image", "thumbnail", "marquee", "video", "fanart", "boxback", "bezel", "manual", "map",
         "titleshot", "wheel", "box", "cartridge", "magazine")
EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "video/mp4": ".mp4",
       "application/pdf": ".pdf"}


class FakeES:
    def __init__(self, roms_root, systems):
        self.roms, self.systems = roms_root, systems    # {system: {".ext", ...}}
        self.mem = {}                                    # system -> {abs path: {field: value}}
        self.running = None
        self.posts = []
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
                if p == "/runningGame":
                    body = json.dumps({"path": fake.running} if fake.running else {"msg": "NO GAME RUNNING"})
                    return self._send(200 if fake.running else 201, body.encode())
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
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

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

    def game(self, system, name):
        """The in-memory metadata of the game whose file name is `name`."""
        for path, md in self.mem.get(system, {}).items():
            if os.path.basename(path) == name:
                return md
        return None

    def close(self):
        self.server.shutdown()
