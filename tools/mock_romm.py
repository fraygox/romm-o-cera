#!/usr/bin/env python3
"""A tiny fake RomM API for developing and screenshotting RomM Browser
without a real server — or a real library.

  python tools/mock_romm.py [--port 8765] [--rate-mb 12]

Then point the app at it: ROMM_URL=http://127.0.0.1:8765 ROMM_TOKEN=demo.
Implements only what the app uses (platforms, collections, roms list/detail,
filters, random picks and recommendations, content downloads with Range,
covers, screenshots, your statuses/notes/RA progress (users/me), pairing),
plus GitHub's release-notes route for What's new
(ROMM_BROWSER_GITHUB_API=<this url>).
Any bearer token is accepted; the pairing code ABCD2345 works once.

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
            "tags": rng.choice([[], [], [], [], ["Proto"], ["Beta 1"], ["Demo"], ["Hack"]]),
            "verified": rng.random() < 0.85,
            "updated_at": "2026-%02d-%02dT10:00:00Z" % (8 + rid % 3, 1 + rid % 28),
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


def _disc_zip(stem, seed, size=600 * 1024):
    """A real zip holding <stem>.cue + <stem>.bin (deterministic bytes)."""
    import zipfile
    import zlib
    rng = random.Random(seed)
    binary = bytes(rng.getrandbits(8) for _ in range(256)) * (size // 256)
    cue = ('FILE "%s.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n' % stem).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in ((stem + ".cue", cue), (stem + ".bin", binary)):
            z.writestr(zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0)), data)
    members = [{"name": n, "size": len(d), "crc_hash": "%08x" % zlib.crc32(d),
                "md5_hash": hashlib.md5(d).hexdigest(), "sha1_hash": hashlib.sha1(d).hexdigest()}
               for n, d in ((stem + ".cue", cue), (stem + ".bin", binary))]
    return buf.getvalue(), members


# Fixed test fixtures appended after the generated library (ids continue).
# (platform id, rom name, fs stem, tags) — the multi-disc cases the app must
# handle: a full set, the same set again tagged "Disc 01" (only discs 1-2),
# another region of it, an incomplete set (no disc 2), plus carts whose
# download is corrupt (wrong checksum) or flaky (503 twice, then fine).
SPECIAL = [
    (6, "Lorem Odyssey", "Lorem Odyssey (USA) (Disc 1)"),
    (6, "Lorem Odyssey", "Lorem Odyssey (USA) (Disc 2)"),
    (6, "Lorem Odyssey", "Lorem Odyssey (USA) (Disc 3)"),
    (6, "Lorem Odyssey", "Lorem Odyssey (USA) (Disc 01)"),
    (6, "Lorem Odyssey", "Lorem Odyssey (USA) (Disc 02)"),
    (6, "Lorem Odyssey", "Lorem Odyssey (Europe) (Disc 1)"),
    (6, "Lorem Odyssey", "Lorem Odyssey (Europe) (Disc 2)"),
    (6, "Ipsum Saga", "Ipsum Saga (Japan) (Disc 1)"),
    (6, "Ipsum Saga", "Ipsum Saga (Japan) (Disc 3)"),
    (3, "Corrupt Cart", "Corrupt Cart (USA)"),
    (3, "Flaky Cart", "Flaky Cart (USA)"),
]


def add_special(roms):
    plat = {p[0]: p for p in PLATFORMS}
    rid = max(r["id"] for r in roms) + 1
    made = []
    for pid, name, stem in SPECIAL:
        slug = plat[pid][1]
        if pid == 6:
            data, members = _disc_zip(stem, rid)
        else:
            import zipfile
            import zlib
            rom_bytes = bytes(range(256)) * 512
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
                z.writestr(zipfile.ZipInfo(stem + ".gba", date_time=(2020, 1, 1, 0, 0, 0)), rom_bytes)
            data = buf.getvalue()
            members = [{"name": stem + ".gba", "size": len(rom_bytes),
                        "crc_hash": "%08x" % zlib.crc32(rom_bytes),
                        "md5_hash": hashlib.md5(rom_bytes).hexdigest(),
                        "sha1_hash": hashlib.sha1(rom_bytes).hexdigest()}]
            if name == "Corrupt Cart":        # RomM's record disagrees with what's served
                members[0] = dict(members[0], crc_hash="deadbeef", sha1_hash="0" * 40)
        fs_name = stem + ".zip"
        # like RomM: an archive's file-level hashes describe its contents, not the zip
        sha1 = members[0]["sha1_hash"] if len(members) == 1 else hashlib.sha1(
            "".join(m["sha1_hash"] for m in members).encode()).hexdigest()
        regions = [re.search(r"\((USA|Europe|Japan)\)", stem).group(1)]
        r = {
            "id": rid, "platform_id": pid, "platform_slug": slug, "platform_fs_slug": slug,
            "platform_display_name": plat[pid][2], "name": name, "fs_name": fs_name,
            "fs_name_no_ext": stem, "fs_extension": "zip", "fs_size_bytes": len(data),
            "regions": regions, "summary": "A test fixture for the mock RomM.",
            "metadatum": {"genres": ["Role-Playing"], "first_release_date": int(29 * MS_PER_YEAR),
                          "player_count": "1", "developers": ["Lorem Ipsum Soft"],
                          "publishers": ["Lorem Ipsum Soft"], "average_rating": 80},
            "path_cover_small": "/covers/%d.png?s=1" % rid, "path_cover_large": "/covers/%d.png?s=2" % rid,
            "has_multiple_files": False, "has_nested_single_file": False, "tags": [], "verified": True,
            "updated_at": "2026-%02d-%02dT10:00:00Z" % (9 + rid % 2, 1 + rid % 28),
            "full_path": "roms/%s/%s" % (slug, fs_name),
            "files": [{"id": rid * 10, "rom_id": rid, "file_name": fs_name, "file_size_bytes": len(data),
                       "category": "game", "full_path": "roms/%s/%s" % (slug, fs_name),
                       "sha1_hash": sha1, "md5_hash": None, "crc_hash": None,
                       "archive_members": members}],
            "_data": data,
        }
        if name in ("Lorem Odyssey", "Flaky Cart"):      # a manual + RetroAchievements data
            r.update(has_manual=True, path_manual="roms/%d/%d/manual/%d.pdf" % (pid, rid, rid),
                     ra_id=1000 + rid, merged_ra_metadata={"achievements": [
                         {"title": "Lorem %d" % i, "points": p} for i, p in enumerate((5, 10, 25))]})
        made.append(r)
        roms.append(r)
        rid += 1
    # IGDB-style siblings: every Lorem Odyssey copy is a "sibling" of the others
    lo = [r for r in made if r["name"] == "Lorem Odyssey"]
    for r in lo:
        r["sibling_roms"] = [{"id": o["id"], "name": o["name"], "fs_name_no_ext": o["fs_name_no_ext"]}
                             for o in lo if o is not r]
    return made


# Your RomM side, like 5.4's rom_user: a few homebrew games and lorem picks
# playing, backlogged, finished...; everything else RomM's stand-in (id -1).
USER_ID = 1
BLANK_USER = {"id": -1, "user_id": -1, "rom_id": -1, "created_at": "2026-01-01T00:00:00Z",
              "updated_at": "2026-01-01T00:00:00Z", "last_played": None, "is_main_sibling": False,
              "backlogged": False, "now_playing": False, "hidden": False, "rating": 0, "difficulty": 0,
              "completion": 0, "status": None, "pinned_media": None}
USER_REAL = {   # homebrew name -> rom_user fields
    "Alter Ego": dict(status="finished", rating=9, completion=100, last_played="2026-09-28T20:15:00Z"),
    "Anguna": dict(now_playing=True, status="incomplete", rating=8, completion=40,
                   last_played="2026-10-12T19:02:00Z"),
    "Tobu Tobu Girl": dict(now_playing=True, completion=15, last_played="2026-10-10T18:30:00Z"),
    "Thwaite": dict(backlogged=True),
    "Libbet and the Magic Floor": dict(backlogged=True),
    "Butano Fighter": dict(status="completed_100", rating=7, completion=100, last_played="2026-08-03T21:40:00Z"),
    "uCity": dict(status="retired", rating=6, last_played="2026-07-14T10:00:00Z"),
    "Lan Master": dict(status="never_playing"),
}
SHOT_NAMES = set(USER_REAL) | {"Sir Ababol", "Varooom 3D"}


def add_user_data(roms, seed=13):
    """rom_user, notes and screenshots (its own random stream: the library stays the same)."""
    rng = random.Random(seed)
    lorem = [r for r in roms if r["metadatum"]["developers"][0] == "Lorem Ipsum Soft"
             and r["platform_id"] in (1, 3, 4, 6, 8)]
    picks = {}
    for k, r in enumerate(rng.sample(lorem, 30)):
        picks[r["id"]] = dict(backlogged=True) if k < 18 else \
            dict(status=("finished", "completed_100", "incomplete", "retired")[k % 4], rating=rng.randint(4, 10),
                 last_played="2026-%02d-%02dT20:00:00Z" % (rng.randint(3, 9), rng.randint(1, 28)))
    odyssey = next(r for r in roms if r["fs_name_no_ext"] == "Lorem Odyssey (USA) (Disc 1)")
    picks[odyssey["id"]] = dict(now_playing=True, status="incomplete", completion=55,
                                last_played="2026-10-11T22:10:00Z")
    for r in roms:
        fields = USER_REAL.get(r["name"]) if r["metadatum"]["developers"][0] != "Lorem Ipsum Soft" \
            else picks.get(r["id"])
        r["rom_user"] = dict(BLANK_USER, id=r["id"], user_id=USER_ID, rom_id=r["id"], **fields) \
            if fields else dict(BLANK_USER)
        shots = 3 if r["name"] in SHOT_NAMES or r["id"] == odyssey["id"] else 0
        r["merged_screenshots"] = ["/assets/romm/resources/roms/%d/%d/screenshots/%d.png" % (
            r["platform_id"], r["id"], i) for i in range(shots)]
        notes = []
        if r["name"] in ("Anguna", "Alter Ego") or r["id"] == odyssey["id"]:
            notes = [{"id": r["id"], "title": "Where I am", "is_public": False, "tags": None,
                      "content": "Lorem ipsum: **third** dungeon, key behind the waterfall.",
                      "created_at": "2026-10-01T10:00:00Z", "updated_at": "2026-10-11T22:12:00Z",
                      "user_id": USER_ID, "username": "demo"},
                     {"id": 10000 + r["id"], "title": "Tip", "is_public": True, "tags": None,
                      "content": "Dolor sit amet: save before the boss.", "created_at": "2026-09-01T10:00:00Z",
                      "updated_at": "2026-09-01T10:00:00Z", "user_id": 2, "username": "friend"}]
        r["has_notes"] = bool(notes)
        r["_notes"] = notes


def users_me(roms):
    """GET /api/users/me: you, with RetroAchievements progress for the RA games you played."""
    results = []
    for r in roms:
        if r.get("ra_id") and (r.get("rom_user") or BLANK_USER)["id"] != -1:
            got = [{"id": str(i), "date": "2026-10-0%d 20:00:00" % (i + 1)} for i in range(2)]
            results.append({"rom_ra_id": r["ra_id"], "max_possible": 3, "num_awarded": 2,
                            "num_awarded_hardcore": 1, "most_recent_awarded_date": "2026-10-11T22:00:00Z",
                            "highest_award_kind": None, "earned_achievements": got})
    return {"id": USER_ID, "username": "demo", "email": None, "enabled": True, "role": "viewer",
            "oauth_scopes": ["roms.read", "platforms.read"], "avatar_path": "", "last_login": None,
            "last_active": None, "ra_username": "demo",
            "ra_progression": {"total": len(results), "results": results},
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z"}


def on_status(r, statuses):
    """RomM's statuses filter (any): now_playing / backlogged / hidden flags, else
    the status itself; hidden games only when asked for."""
    ru = r.get("rom_user") or BLANK_USER
    if ru["hidden"] and "hidden" not in statuses:
        return False
    return any(ru.get(s) is True if s in ("now_playing", "backlogged", "hidden") else ru["status"] == s
               for s in statuses)


RELEASE_NOTES = ("## Features\n\n- Lorem ipsum: **your RomM lists** on the box.\n"
                 "- Dolor sit amet: compare a platform with RomM.\n"
                 "- Sed do eiusmod: a game's page shows its cover, facts and notes side by side, with the "
                 "one thing to do about it in the big button.\n"
                 "- Tempor incididunt: one keyboard for search, the server address and every text setting.\n\n"
                 "## Smoother on 4K\n\nUt labore et dolore magna aliqua: covers and text are kept within a "
                 "memory budget, so a long scroll through a big library stays smooth.\n\n"
                 "## Fixes\n\n"
                 "- Consectetur adipiscing: long names [scroll](https://example.com) instead of being cut off.\n"
                 "- Ut enim ad minim veniam: a download paused overnight carries on where it stopped.\n"
                 "- Quis nostrud: the hint bar says Actions on every controller.\n")


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


def make_shot(rom, n):
    """A generated 16:9 "screenshot": stripes, blocks and the game's name. PNG bytes."""
    from PIL import Image, ImageDraw
    w, h = 640, 360
    rng = random.Random("%s#%d" % (rom["name"], n))
    base = [rng.randint(30, 200) for _ in range(3)]
    img = Image.new("RGB", (w, h), tuple(base))
    d = ImageDraw.Draw(img)
    for y in range(0, h, 24):
        d.rectangle([0, y, w, y + 11], fill=tuple(min(255, c + 25) for c in base))
    for _ in range(12):
        x, y = rng.randint(0, w - 40), rng.randint(0, h - 40)
        d.rectangle([x, y, x + rng.randint(16, 80), y + rng.randint(16, 60)],
                    fill=tuple(rng.randint(0, 255) for _ in range(3)))
    d.rectangle([0, h - 40, w, h], fill=(10, 10, 16))
    d.text((12, h - 32), "%s  %d" % (rom["name"], n + 1), fill=(240, 240, 250), font=_font(20))
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


HIDDEN = ("files", "_data", "sibling_roms", "_notes")      # never in list records


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

    # pairing: like RomM 5.x, a code works once (then 404). ABCD2345 is
    # there from the start; POST /mock/pair-code mints more (tests).
    pair_codes = {"ABCD2345"}
    pair_seen = []                 # Authorization header (or None) per exchange call

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        if path == "/mock/pair-code":
            code = "".join(random.choice("ABCDEFGHJKMNPQRSTUVWXYZ23456789") for _ in range(8))
            Handler.pair_codes.add(code)
            return self._json({"code": code, "expires_in": 60})
        if path == "/mock/recs":                  # tests: {"mode": "ok" | "cold" | "empty" | "404"}
            Handler.recs_mode = json.loads(raw or b"{}").get("mode", "ok")
            return self._json({"mode": Handler.recs_mode})
        if path == "/mock/pair-seen":
            return self._json(Handler.pair_seen)
        if path == "/api/client-tokens/exchange":
            Handler.pair_seen.append(self.headers.get("Authorization"))
            try:
                code = str(json.loads(raw or b"{}")["code"])
            except (ValueError, KeyError, TypeError):
                return self._json({"detail": [{"msg": "Field required"}]}, 422)
            code = code.replace("-", "").upper()
            if code not in Handler.pair_codes:
                return self._json({"detail": "Invalid or expired pairing code"}, 404)
            Handler.pair_codes.discard(code)
            return self._json({"id": 1, "name": "batocera", "scopes": ["roms.read", "platforms.read"],
                               "expires_at": None, "last_used_at": None,
                               "created_at": "2026-01-01T00:00:00Z", "user_id": 1, "device_id": None,
                               "raw_token": "rmm_" + hashlib.sha256(code.encode()).hexdigest()})
        return self._json({"detail": "Not Found"}, 404)

    @classmethod
    def filter_values(cls):
        """Like RomM's RomFiltersDict: sorted distinct values (player counts are strings)."""
        md = [r["metadatum"] for r in cls.roms]
        return {"genres": sorted({g for m in md for g in m["genres"]}), "franchises": [], "collections": [],
                "companies": sorted({d for m in md for d in m["developers"]}), "publishers": [],
                "developers": sorted({d for m in md for d in m["developers"]}), "game_modes": [],
                "age_ratings": [], "player_counts": sorted({m["player_count"] for m in md if m["player_count"]}),
                "regions": sorted({x for r in cls.roms for x in r["regions"]}),
                "languages": [], "tags": sorted({t for r in cls.roms for t in r["tags"]}),
                "platforms": sorted({r["platform_id"] for r in cls.roms})}

    @classmethod
    def recommendations(cls, cold=False):
        """Like RomM 5.4's feed: SimpleRomSchema roms with a score and why. Seeded
        by games "played" (two of the homebrew ones); cold: its best-rated games."""
        rng = random.Random(23)
        pool = [r for r in cls.roms if r["metadatum"]["developers"][0] == "Lorem Ipsum Soft"
                and r["platform_id"] in (1, 3, 4, 5, 6, 9)]
        out = []
        for k, r in enumerate(rng.sample(pool, 18)):
            md = r["metadatum"]
            if cold:
                why, seed = [{"facet": "top_rated", "value": ""}], None
            else:
                seed = ("Alter Ego", "Anguna", None)[k % 3]
                year = time.gmtime(md["first_release_date"] / 1000).tm_year // 10 * 10
                why = [{"facet": "genre", "value": md["genres"][0]}, {"facet": "decade", "value": str(year)}]
                if seed is None:
                    why = [{"facet": "igdb", "value": ""}] + why[:1]
                elif k % 4 == 1:
                    why.insert(0, {"facet": "franchise", "value": "Dolor Sit"})
            rom = {x: v for x, v in r.items() if x not in HIDDEN}
            out.append({"rom": rom, "score": round(0.95 - k * 0.03, 3), "reasons": why,
                        "seed_rom_id": None if seed is None else next(
                            x["id"] for x in cls.roms if x["name"] == seed), "seed_rom_name": seed})
        return out

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
        if path == "/api/users/me":
            return self._json(users_me(self.roms))
        m = re.match(r"^/repos/[^/]+/[^/]+/releases/(?:tags/([^/]+)|latest)$", path)
        if m:                                      # GitHub, for What's new (ROMM_BROWSER_GITHUB_API)
            tag = m.group(1) or "v0.9.0"
            return self._json({"tag_name": tag, "name": tag, "body": RELEASE_NOTES,
                               "html_url": "https://example.com/releases/" + tag, "assets": []})
        if path == "/api/roms/filters":            # every filter's values across the library
            return self._json(self.filter_values())
        if path == "/api/recommendations":
            mode = getattr(Handler, "recs_mode", "ok")
            if mode == "404":                     # a RomM before 5.4
                return self._json({"detail": "Not Found"}, 404)
            lim = int((q.get("limit") or [20])[0])
            return self._json([] if mode == "empty" else self.recommendations(mode == "cold")[:lim])
        if path == "/api/roms/random":             # RomM 5.4: one game from the scope, or null
            items = self.roms
            if "platform_ids" in q:
                pids = {int(x) for x in q["platform_ids"]}
                items = [r for r in items if r["platform_id"] in pids]
            for key, cols in (("collection_id", self.collections), ("smart_collection_id", self.smart)):
                if q.get(key):
                    ids = set(next((c["rom_ids"] for c in cols if c["id"] == int(q[key][0])), []))
                    items = [r for r in items if r["id"] in ids]
            # the same picks in the same order every run (tests, screenshots)
            Handler.random_n = getattr(Handler, "random_n", 0) + 1
            if not items:
                return self._json(None)
            r = random.Random("%s#%d" % (u.query, Handler.random_n)).choice(items)
            return self._json({k: v for k, v in r.items() if k not in HIDDEN})
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
            if q.get("regions"):
                want = set(q["regions"])
                items = [r for r in items if want & set(r["regions"])]
            if (q.get("verified") or [""])[0] == "true":
                items = [r for r in items if r["verified"]]
            if q.get("tags") and (q.get("tags_logic") or ["any"])[0] == "none":
                bad = set(q["tags"])
                items = [r for r in items if not bad & set(r["tags"])]
            if q.get("player_counts"):              # RomM: the rom's player count is one of these
                want = set(q["player_counts"])
                keep = (q.get("player_counts_logic") or ["any"])[0] != "none"
                items = [r for r in items if (r["metadatum"]["player_count"] in want) == keep]
            if q.get("statuses"):
                want = set(q["statuses"])
                keep = (q.get("statuses_logic") or ["any"])[0] != "none"
                items = [r for r in items if on_status(r, want) == keep]
            else:
                items = [r for r in items if not (r.get("rom_user") or BLANK_USER)["hidden"]]
            term = (q.get("search_term") or [""])[0].lower()
            if term:
                items = [r for r in items if all(t in r["name"].lower() for t in term.split())]
            order = (q.get("order_by") or ["name"])[0]
            rev = (q.get("order_dir") or ["asc"])[0] == "desc"
            if order == "id":
                items = sorted(items, key=lambda r: r["id"], reverse=rev)
            elif order == "last_played":             # RomUser column: never played last either way
                items = sorted(items, key=lambda r: r["name"].lower())
                lp = {r["id"]: (r.get("rom_user") or BLANK_USER)["last_played"] for r in items}
                items = sorted([r for r in items if lp[r["id"]]], key=lambda r: lp[r["id"]], reverse=rev) + \
                    [r for r in items if not lp[r["id"]]]
            else:
                items = sorted(items, key=lambda r: r["name"].lower(), reverse=rev)
            off = int((q.get("offset") or [0])[0])
            lim = int((q.get("limit") or [50])[0])
            page = [{k: v for k, v in r.items() if k not in HIDDEN}
                    for r in items[off:off + lim]]
            out = {"items": page, "total": len(items), "offset": off, "limit": lim}
            if (q.get("with_filter_values") or ["false"])[0] == "true":
                out["filter_values"] = {"tags": sorted({t for r in self.roms for t in r["tags"]} | {"Homebrew"})}
            if (q.get("with_char_index") or ["false"])[0] == "true" and order != "id":
                ci = {}
                for i, r in enumerate(items):          # like RomM: first char -> first offset
                    ci.setdefault(r["name"][:1].lower(), i)
                out["char_index"] = ci
            return self._json(out)
        m = re.match(r"^/api/roms/(\d+)/similar$", path)
        if m and int(m.group(1)) in self.by_id:
            me = self.by_id[int(m.group(1))]
            same = [r for r in self.roms if r["platform_id"] == me["platform_id"] and r["id"] != me["id"]]
            pick = random.Random(me["id"]).sample(same, min(int((q.get("limit") or [12])[0]), len(same), 12))
            return self._json([{"rom": {k: v for k, v in r.items() if k not in HIDDEN},
                                "score": 0.9,
                                "reasons": ["genre"]} for r in pick])
        m = re.match(r"^/api/roms/(\d+)$", path)
        if m and int(m.group(1)) in self.by_id:
            r = self.by_id[int(m.group(1))]
            out = dict({k: v for k, v in r.items() if k not in ("_data", "_notes")},
                       sibling_roms=r.get("sibling_roms", []), all_user_notes=r.get("_notes", []))
            if "files" in r:
                out["files"] = [{k: v for k, v in f.items() if k != "_data"} for f in r["files"]]
            return self._json(out)
        m = re.match(r"^/api/roms/(\d+)/content/", path)
        if m and int(m.group(1)) in self.by_id:
            r = self.by_id[int(m.group(1))]
            if r["name"] == "Flaky Cart":
                Handler.flaky = getattr(Handler, "flaky", 0) + 1
                if Handler.flaky % 3:             # 503 twice, then serve
                    return self._json({"detail": "busy"}, 503)
            if "_data" in r:
                return self._stream(len(r["_data"]), r["_data"])
            if r["fs_name"].endswith(".zip") and r["fs_size_bytes"] <= 64 << 20:
                return self._stream(r["fs_size_bytes"], self._zip_for(r))
            return self._stream(r["fs_size_bytes"])
        m = re.match(r"^/api/roms/(\d+)/files/content/", path)
        if m and int(m.group(1)) in self.files:
            f = self.files[int(m.group(1))]
            return self._stream(f["file_size_bytes"], f.get("_data"))
        m = re.match(r"^/assets/romm/resources/roms/\d+/(\d+)/manual/\d+\.pdf$", path)
        if m and self.by_id.get(int(m.group(1)), {}).get("has_manual"):
            data = b"%PDF-1.4\n% mock manual for rom " + m.group(1).encode() + b"\n%%EOF\n"
            return self._stream(len(data), data)
        m = re.match(r"^/assets/romm/resources/roms/\d+/(\d+)/screenshots/(\d+)\.png$", path)
        if m and int(m.group(1)) in self.by_id:
            r = self.by_id[int(m.group(1))]
            if path in r.get("merged_screenshots", []):
                key = (r["id"], "shot", int(m.group(2)))
                if key not in self.covers:
                    self.covers[key] = make_shot(r, int(m.group(2)))
                data = self.covers[key]
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
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
        """Range-aware (bytes=a-, a-b and -n, like RomM's nginx), throttled so
        progress is visible. Zeros unless data (bytes, or anything sliceable)."""
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].split(",")[0].partition("-")
            if a:
                start, end = int(a), min(int(b), size - 1) if b else size - 1
            else:
                start = max(0, size - int(b or 0))
            if start >= size or start > end:
                self.send_response(416)
                self.send_header("Content-Range", "bytes */%d" % size)
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        else:
            self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(end + 1 - start))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        chunk = b"\0" * (256 * 1024)
        left = end + 1 - start
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
    add_special(Handler.roms)
    add_user_data(Handler.roms)
    Handler.by_id = {r["id"]: r for r in Handler.roms}
    Handler.files = {f["id"]: f for r in Handler.roms for f in r["files"]}
    Handler.collections = build_collections(Handler.roms)
    Handler.smart = build_smart(Handler.roms)
    Handler.rate = a.rate_mb * 1024 * 1024
    print("mock RomM: %d games on http://127.0.0.1:%d" % (len(Handler.roms), a.port), flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
