#!/usr/bin/env python3
"""A tiny fake RomM API for developing and screenshotting RomM Browser
without a real server — or a real library.

  python tools/mock_romm.py [--port 8765] [--rate-mb 12]

Then point the app at it: ROMM_URL=http://127.0.0.1:8765 ROMM_TOKEN=demo.
Implements only what the app uses (platforms, collections, roms list/detail,
content downloads with Range, covers). Any bearer token is accepted.

The library is made of freely distributed homebrew / open-source games (with
descriptions written for this mock, not copied) plus invented "lorem ipsum"
titles. Covers are generated, so nothing here is anyone's box art.
"""
import argparse
import hashlib
import io
import json
import os
import random
import re
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MS_PER_YEAR = 365.25 * 24 * 3600 * 1000

PLATFORMS = [  # id, fs_slug, display name
    (1, "nes", "Nintendo Entertainment System"),
    (2, "gb", "Game Boy"),
    (3, "gba", "Game Boy Advance"),
    (4, "snes", "Super Nintendo Entertainment System"),
    (5, "genesis", "Sega Mega Drive/Genesis"),
    (6, "ps1", "PlayStation"),
    (7, "dreamcast", "Dreamcast"),
    (8, "ps2", "PlayStation 2"),
    (9, "n64", "Nintendo 64"),
    (10, "saturn", "Sega Saturn"),
    (11, "pc-fx", "PC-FX"),
    (12, "zxs", "ZX Spectrum"),
]

# Freely distributed homebrew / open-source games. Descriptions are our own.
REAL = [
    (1, "Alter Ego", 2011, "Shiru", "Puzzle, Platform", "1",
     "Swap places with your phantom twin to collect every pixel on each screen. A small, "
     "clever freeware puzzle-platformer for the NES."),
    (1, "Lan Master", 2011, "Shiru", "Puzzle", "1",
     "Rotate cable pieces until every computer on the network lights up. Freeware."),
    (1, "Sir Ababol", 2013, "The Mojon Twins", "Platform", "1",
     "A knight hunts poppies through a castle of flick-screen rooms. Free homebrew."),
    (1, "Super Tilt Bro.", 2018, "sgadrat", "Fighting", "1-2",
     "A fast platform fighter built for the NES and released as open source."),
    (1, "Nova the Squirrel", 2018, "NovaSquirrel", "Platform", "1",
     "A squirrel with copy abilities explores a colourful world. Open-source homebrew."),
    (1, "Concentration Room", 2010, "Damian Yerrick", "Puzzle", "1-2",
     "The classic memory card-matching game, with a lab-accident twist. Open source."),
    (1, "Thwaite", 2011, "Damian Yerrick", "Action", "1-2",
     "Defend your villages from falling missiles with two launchers. Open source."),
    (2, "Tobu Tobu Girl", 2017, "Tangram Games", "Arcade, Platform", "1",
     "Bounce ever higher on the heads of flying creatures to rescue a lost cat. "
     "Open-source Game Boy homebrew."),
    (2, "Libbet and the Magic Floor", 2018, "Damian Yerrick", "Puzzle", "1",
     "Walk over every tile of each basement floor without getting stuck. Open source."),
    (2, "uCity", 2017, "AntonioND", "Simulation", "1",
     "A full city-building sim squeezed onto the Game Boy Color. Open source."),
    (3, "Anguna", 2008, "Nathan Tolbert", "Adventure", "1",
     "A top-down dungeon adventure released free for the GBA."),
    (3, "Butano Fighter", 2020, "GValiente", "Shooter", "1",
     "A vertical shooter that doubles as the showcase for an open-source GBA engine."),
    (3, "Varooom 3D", 2022, "GValiente", "Racing", "1",
     "Pseudo-3D racing on the GBA, released as open source."),
]

LOREM = ("lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor "
         "incididunt ut labore et dolore magna aliqua enim minim veniam quis nostrud "
         "exercitation ullamco laboris nisi aliquip commodo consequat").split()
SUFFIX = ["", "", "", " II", " 3", " Racing", " Quest", " Arena", " Legends", " Deluxe",
          " Kart", " Tactics", " Party", " Odyssey", " Chronicles", " Turbo", " Zero"]
GENRES = ["Action", "Adventure", "Platform", "Puzzle", "Racing", "Role-Playing", "Shooter",
          "Sports", "Fighting", "Strategy", "Simulation", "Arcade"]
REGIONS = [["USA"], ["USA"], ["Europe"], ["Japan"], ["World"], ["USA", "Europe"]]
COUNTS = {1: 1240, 2: 610, 3: 820, 4: 980, 5: 760, 6: 1460, 7: 420, 8: 1310, 9: 330,
          10: 290, 11: 40, 12: 900}
DISC = {6: (300, 700), 7: (900, 1200), 8: (1200, 4200), 10: (300, 650), 11: (300, 600)}  # MB
CART = {1: (0.03, 0.5), 2: (0.03, 1), 3: (2, 16), 4: (0.5, 4), 5: (0.5, 4), 9: (4, 64),
        12: (0.02, 0.1)}


def lorem_sentence(rng, n=24):
    w = [rng.choice(LOREM) for _ in range(n)]
    return (" ".join(w)).capitalize() + "."


def build_library(seed=7):
    rng = random.Random(seed)
    roms = []
    rid = 1
    plat = {p[0]: p for p in PLATFORMS}

    def add(pid, name, year, dev, genre, players, summary, regions, size):
        nonlocal rid
        slug = plat[pid][1]
        disc = pid in DISC
        tag = "(%s)" % ", ".join(regions)
        fs_no_ext = "%s %s" % (name, tag)
        multi = disc and pid in (6, 7) and rng.random() < 0.5    # some folder roms
        nested = disc and pid == 8 and rng.random() < 0.1        # folder holding one .iso
        fs_name = fs_no_ext if (multi or nested) else fs_no_ext + ".zip"
        r = {
            "id": rid, "platform_id": pid, "platform_slug": slug, "platform_fs_slug": slug,
            "platform_display_name": plat[pid][2],
            "name": name, "fs_name": fs_name, "fs_name_no_ext": fs_no_ext,
            "fs_extension": "" if multi else "zip", "fs_size_bytes": int(size),
            "regions": regions, "summary": summary,
            "metadatum": {"genres": [g.strip() for g in genre.split(",")],
                          "first_release_date": int((year - 1970) * MS_PER_YEAR),
                          "player_count": players, "developers": [dev], "publishers": [dev],
                          "average_rating": rng.randint(55, 95)},
            "path_cover_small": "/covers/%d.png?s=1" % rid,
            "path_cover_large": "/covers/%d.png?s=2" % rid,
            "has_multiple_files": multi, "has_nested_single_file": nested,
            "full_path": "roms/%s/%s" % (slug, fs_name),
        }
        if multi:
            ext = ".gdi" if pid == 7 else ".cue"
            sheet = "%s%s" % (fs_no_ext, ext)
            tracks = ["track01.bin", "track02.raw", "track03.bin"] if pid == 7 else [fs_no_ext + ".bin"]
            files = [(sheet, 120)] + [(t, size // len(tracks)) for t in tracks]
            r["files"] = [{"id": rid * 10 + i, "rom_id": rid, "file_name": fn, "file_size_bytes": int(sz),
                           "category": "game", "full_path": "%s/%s" % (r["full_path"], fn)}
                          for i, (fn, sz) in enumerate(files)]
            r["fs_size_bytes"] = sum(f["file_size_bytes"] for f in r["files"])
        elif nested:
            fn = fs_no_ext + ".iso"
            r["files"] = [{"id": rid * 10, "rom_id": rid, "file_name": fn, "file_size_bytes": int(size),
                           "category": "game", "full_path": "%s/%s" % (r["full_path"], fn)}]
            r["fs_extension"] = ""
        else:
            r["files"] = [{"id": rid * 10, "rom_id": rid, "file_name": fs_name,
                           "file_size_bytes": int(size), "category": "game", "full_path": r["full_path"]}]
        roms.append(r)
        rid += 1

    for pid, name, year, dev, genre, players, summary in REAL:
        lo, hi = CART.get(pid, (0.1, 1))
        add(pid, name, year, dev, genre, players, summary, ["World"],
            rng.uniform(lo, hi) * 1024 * 1024)
    seen = set()
    for pid, n in COUNTS.items():
        made = sum(1 for r in roms if r["platform_id"] == pid)
        while made < n:
            k = rng.choice([2, 2, 3, 3, 4])
            name = " ".join(w.capitalize() for w in rng.sample(LOREM, k)) + rng.choice(SUFFIX)
            if (pid, name) in seen:
                continue
            seen.add((pid, name))
            lo, hi = DISC.get(pid) or CART.get(pid, (0.1, 1))
            add(pid, name, rng.randint(1985, 2008), "Lorem Ipsum Soft",
                ", ".join(rng.sample(GENRES, rng.choice([1, 2]))), rng.choice(["1", "1-2", "1-4"]),
                lorem_sentence(rng, rng.randint(18, 60)) + " " + lorem_sentence(rng),
                rng.choice(REGIONS), rng.uniform(lo, hi) * 1024 * 1024)
            made += 1
    return roms


def build_collections(roms, seed=11):
    rng = random.Random(seed)
    real = [r["id"] for r in roms if r["metadatum"]["developers"][0] != "Lorem Ipsum Soft"]
    lorem = [r["id"] for r in roms if r["id"] not in real]
    cols = [("Top 25: Homebrew Heroes", real + rng.sample(lorem, 12)),
            ("Theme: Open Source Gems", real),
            ("Theme: Lorem Ipsum Classics That Were Actually Good", rng.sample(lorem, 64)),
            ("Theme: Couch Co-op & Party", rng.sample(lorem, 48)),
            ("Theme: Hidden Gems", rng.sample(lorem, 70)),
            ("Series: Dolor Sit", rng.sample(lorem, 9)),
            ("Series: Magna Aliqua", rng.sample(lorem, 14)),
            ("Top 100: PlayStation", [r["id"] for r in roms if r["platform_id"] == 6][:100]),
            ("Lorem: Weekend Picks", rng.sample(lorem, 7)),     # one-off colon: not a tag
            ("Weekend Favourites", rng.sample(lorem, 11))]
    return [{"id": i + 1, "name": n, "rom_count": len(ids), "rom_ids": ids}
            for i, (n, ids) in enumerate(cols)]


def build_smart(roms):
    short = [r["id"] for r in roms if r["fs_size_bytes"] < 64 * 1024]
    multi = [r["id"] for r in roms if r["metadatum"]["player_count"] == "1-4"]
    return [{"id": 1, "name": "Tiny games (< 64 KB)", "rom_count": len(short), "rom_ids": short},
            {"id": 2, "name": "Four-player games", "rom_count": len(multi), "rom_ids": multi}]


def build_virtual(roms, vtype):
    import base64
    key = {"genre": "genres", "company": "developers"}.get(vtype)
    groups = {}
    if key:
        for r in roms:
            for v in r["metadatum"][key]:
                groups.setdefault(v, []).append(r["id"])
    out = []
    for name, ids in sorted(groups.items()):
        vid = base64.b64encode(json.dumps({"name": name, "type": vtype}).encode()).decode()
        out.append({"id": vid, "name": name, "type": vtype, "rom_count": len(ids), "rom_ids": ids})
    return out


_FONT = None


def _font(size):
    global _FONT
    from PIL import ImageFont
    if _FONT is None:
        for p in ("/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
                  "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
            if os.path.exists(p):
                _FONT = p
                break
    return ImageFont.truetype(_FONT, size) if _FONT else ImageFont.load_default()


def make_cover(rom, scale):
    """Generated box art: gradient, shapes, title. PNG bytes."""
    from PIL import Image, ImageDraw
    w, h = 300 * scale, 420 * scale
    seed = int(hashlib.md5(rom["name"].encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    hue = [rng.randint(40, 230) for _ in range(3)]
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        d.line([(0, y), (w, y)], fill=tuple(int(c * (1 - t) + c * 0.25 * t) for c in hue))
    for _ in range(7):
        x, y, rr = rng.randint(0, w), rng.randint(0, h), rng.randint(20, 140) * scale // 2
        col = tuple(min(255, c + rng.randint(30, 90)) for c in hue)
        if rng.random() < 0.5:
            d.ellipse([x - rr, y - rr, x + rr, y + rr], outline=col, width=4 * scale)
        else:
            d.rectangle([x - rr, y - rr // 2, x + rr, y + rr // 2], outline=col, width=4 * scale)
    d.rectangle([0, h - 46 * scale, w, h], fill=(14, 13, 22))
    d.text((14 * scale, h - 38 * scale), rom["platform_slug"].upper(), fill=(220, 220, 230),
           font=_font(20 * scale))
    words, lines, cur = rom["name"].split(), [], ""
    f = _font(34 * scale)
    for wd in words:
        t = (cur + " " + wd).strip()
        if d.textlength(t, font=f) <= w - 36 * scale:
            cur = t
        else:
            lines.append(cur)
            cur = wd
    lines.append(cur)
    y = 40 * scale
    for ln in lines[:4]:
        d.text((18 * scale + 3, y + 3), ln, fill=(0, 0, 0), font=f)
        d.text((18 * scale, y), ln, fill=(250, 248, 255), font=f)
        y += 42 * scale
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# Stand-in BIOS files (not real firmware): PlayStation gets one, matched by MD5.
FIRMWARE = []
for _i, (_pid, _name) in enumerate([(6, "scph5501.bin"), (6, "scph1001.bin")]):
    _data = ("MOCK-BIOS-%s" % _name).encode() * 64
    FIRMWARE.append({"id": _i + 1, "platform_id": _pid, "file_name": _name,
                     "file_size_bytes": len(_data), "md5_hash": hashlib.md5(_data).hexdigest(),
                     "data": _data})


class Handler(BaseHTTPRequestHandler):
    roms, by_id, files, collections, smart, rate = [], {}, {}, [], [], 12 * 1024 * 1024
    covers = {}

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        path = u.path
        if path == "/api/platforms":
            out = []
            for pid, slug, name in PLATFORMS:
                n = sum(1 for r in self.roms if r["platform_id"] == pid)
                out.append({"id": pid, "slug": slug, "fs_slug": slug, "name": name,
                            "display_name": name, "rom_count": n})
            return self._json(out)
        if path == "/api/collections":
            return self._json([{k: v for k, v in c.items() if k != "rom_ids"} for c in self.collections])
        if path == "/api/firmware":
            pid = int((q.get("platform_id") or ["0"])[0])
            return self._json([{k: v for k, v in f.items() if k != "data"}
                               for f in FIRMWARE if f["platform_id"] == pid])
        m = re.match(r"^/api/firmware/(\d+)/content/", path)
        if m:
            f = next((f for f in FIRMWARE if f["id"] == int(m.group(1))), None)
            if f:
                return self._stream(len(f["data"]), f["data"])
        if path == "/api/collections/smart":
            return self._json([{k: v for k, v in c.items() if k != "rom_ids"} for c in self.smart])
        if path == "/api/collections/virtual":
            vt = (q.get("type") or [""])[0]
            return self._json([{k: v for k, v in c.items() if k != "rom_ids"}
                               for c in build_virtual(self.roms, vt)])
        if path == "/api/roms":
            items = self.roms
            if q.get("smart_collection_id"):
                c = next(c for c in self.smart if c["id"] == int(q["smart_collection_id"][0]))
                ids = set(c["rom_ids"])
                items = [r for r in items if r["id"] in ids]
            if q.get("virtual_collection_id"):
                vid = q["virtual_collection_id"][0]
                vt = json.loads(__import__("base64").b64decode(vid))["type"]
                c = next(c for c in build_virtual(self.roms, vt) if c["id"] == vid)
                ids = set(c["rom_ids"])
                items = [r for r in items if r["id"] in ids]
            if "platform_ids" in q:
                pids = {int(x) for x in q["platform_ids"]}
                items = [r for r in items if r["platform_id"] in pids]
            if q.get("collection_id"):
                col = next(c for c in self.collections if c["id"] == int(q["collection_id"][0]))
                ids = set(col["rom_ids"])
                items = [r for r in items if r["id"] in ids]
            term = (q.get("search_term") or [""])[0].lower()
            if term:
                items = [r for r in items if all(t in r["name"].lower() for t in term.split())]
            order = (q.get("order_by") or ["name"])[0]
            rev = (q.get("order_dir") or ["asc"])[0] == "desc"
            if order == "id":
                items = sorted(items, key=lambda r: r["id"], reverse=rev)
            else:
                items = sorted(items, key=lambda r: r["name"].lower(), reverse=rev)
            off = int((q.get("offset") or [0])[0])
            lim = int((q.get("limit") or [50])[0])
            page = [{k: v for k, v in r.items() if k != "files"} for r in items[off:off + lim]]
            out = {"items": page, "total": len(items), "offset": off, "limit": lim}
            if (q.get("with_char_index") or ["false"])[0] == "true" and order != "id":
                ci = {}
                for i, r in enumerate(items):          # like RomM: first char -> first offset
                    ci.setdefault(r["name"][:1].lower(), i)
                out["char_index"] = ci
            return self._json(out)
        m = re.match(r"^/api/roms/(\d+)$", path)
        if m and int(m.group(1)) in self.by_id:
            return self._json(self.by_id[int(m.group(1))])
        m = re.match(r"^/api/roms/(\d+)/content/", path)
        if m and int(m.group(1)) in self.by_id:
            r = self.by_id[int(m.group(1))]
            if r["fs_name"].endswith(".zip") and r["fs_size_bytes"] <= 64 << 20:
                return self._stream(r["fs_size_bytes"], self._zip_for(r))
            return self._stream(r["fs_size_bytes"])
        m = re.match(r"^/api/roms/(\d+)/files/content/", path)
        if m and int(m.group(1)) in self.files:
            return self._stream(self.files[int(m.group(1))]["file_size_bytes"])
        m = re.match(r"^/covers/(\d+)\.png$", path)
        if m and int(m.group(1)) in self.by_id:
            s = int((q.get("s") or ["1"])[0])
            key = (int(m.group(1)), s)
            if key not in self.covers:
                self.covers[key] = make_cover(self.by_id[key[0]], s)
            data = self.covers[key]
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self._json({"detail": "Not Found"}, 404)

    zips = {}
    EXT = {"nes": ".nes", "gb": ".gb", "gba": ".gba", "snes": ".sfc", "genesis": ".md",
           "n64": ".z64", "zxs": ".tzx"}

    def _zip_for(self, r):
        """A real stored zip of exactly fs_size_bytes, so the app's zip/size
        checks pass (contents are zeros — it's a stand-in, not a game)."""
        if r["id"] not in self.zips:
            name = r["fs_name_no_ext"] + self.EXT.get(r["platform_slug"], ".bin")
            n = len(name.encode())
            body = max(0, r["fs_size_bytes"] - (98 + 2 * n))
            buf = io.BytesIO()
            import zipfile
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
                zi = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
                z.writestr(zi, b"\0" * body)
            data = buf.getvalue()
            assert len(data) == r["fs_size_bytes"], (len(data), r["fs_size_bytes"])
            self.zips[r["id"]] = data
        return self.zips[r["id"]]

    def _stream(self, size, data=None):
        """Range-aware, throttled so progress is visible. Zeros unless data."""
        start = 0
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            start = int(rng[6:].split("-")[0] or 0)
            if start >= size:
                self.send_response(416)
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, size - 1, size))
        else:
            self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(size - start))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        chunk = b"\0" * (256 * 1024)
        left = size - start
        pos = start
        t0, sent = time.time(), 0
        try:
            while left > 0:
                n = min(left, len(chunk))
                self.wfile.write(data[pos:pos + n] if data is not None else chunk[:n])
                pos += n
                left -= n
                sent += n
                ahead = sent / self.rate - (time.time() - t0)
                if ahead > 0:
                    time.sleep(ahead)
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--rate-mb", type=float, default=12, help="download speed, MB/s")
    a = ap.parse_args()
    Handler.roms = build_library()
    Handler.by_id = {r["id"]: r for r in Handler.roms}
    Handler.files = {f["id"]: f for r in Handler.roms for f in r["files"]}
    Handler.collections = build_collections(Handler.roms)
    Handler.smart = build_smart(Handler.roms)
    Handler.rate = a.rate_mb * 1024 * 1024
    print("mock RomM: %d games on http://127.0.0.1:%d" % (len(Handler.roms), a.port), flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
