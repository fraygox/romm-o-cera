#!/usr/bin/env python3
"""Screenshot harness: every screen and dialog, in every control style.

  python tools/shots.py OUT_DIR [--styles xbox,playstation,nintendo,kb] [--scenes home,settings,...]

Runs the app headless against tools/mock_romm.py (fake library) and
tests/fake_es.py (fake EmulationStation API) on a throwaway data/roms tree,
then writes OUT_DIR/<style>/<scene>.png and OUT_DIR/index.html (a contact
sheet). No box, no RomM, no display needed. Also checks every hint token is
one the glyph helper knows (exit code 1 otherwise) — controls are never
hand-drawn.
"""
import argparse
import html
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

ap = argparse.ArgumentParser()
ap.add_argument("out")
ap.add_argument("--styles", default="xbox,playstation,nintendo,kb")
ap.add_argument("--scenes", default="")
ap.add_argument("--theme", default="default", help="colour theme (rommbrowser THEMES key), e.g. xbox_oled")
ap.add_argument("--readme", action="store_true",
                help="also copy the README set (Xbox style) into screenshots/ next to the app")
ap.add_argument("--fixed-wait", action="store_true",
                help="old pacing: always wait the full 50 frames per scene instead of drawing as soon as "
                     "the app's background work has settled (50 frames stays the upper bound either way)")
args = ap.parse_args()

work = tempfile.mkdtemp(prefix="rb-shots-")
os.environ["ROMM_BROWSER_DATA"] = os.path.join(work, "data")
os.environ["ROMM_BROWSER_ROMS"] = os.path.join(work, "roms")
os.environ["ROMM_BROWSER_TOOLS"] = os.path.join(work, "tools")
sys.path.insert(0, APP)
sys.path.insert(0, os.path.join(APP, "tests"))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


port = free_port()
mock = subprocess.Popen([sys.executable, os.path.join(APP, "tools", "mock_romm.py"), "--port", str(port),
                         "--rate-mb", "400"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
os.environ["ROMM_URL"] = "http://127.0.0.1:%d" % port
os.environ["ROMM_TOKEN"] = "demo"
os.environ["ROMM_BROWSER_GITHUB_API"] = os.environ["ROMM_URL"]     # What's new reads the mock's release notes

import pygame  # noqa: E402
import requests  # noqa: E402
import rommlib  # noqa: E402
from fake_es import FakeES  # noqa: E402
import uifacade  # noqa: E402  (tests/uifacade.py)

for _ in range(100):
    try:
        if requests.get(os.environ["ROMM_URL"] + "/api/platforms", timeout=1).ok:
            break
    except requests.RequestException:
        time.sleep(0.1)

# ---- a small box: games, media, leftovers, a cut-short disc
R = rommlib.ROMS_ROOT


def mk(rel, size):
    p = os.path.join(R, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.truncate(size)


MB = 1 << 20
mk("psx/Tempor Legends (USA)/Tempor Legends (USA) (Disc 1).chd", 520 * MB)
mk("psx/Dolor Dash (USA).chd", 380 * MB)
mk("psx/images/crash.png", 40 * MB)
mk("ps2/Amet Racing Pro (USA).iso", 4000 * MB)
mk("gba/Anguna (USA).gba", 8 * MB)
for _t in ("Veniam Quest (USA)", "Fugiat Racer (Europe)"):        # uncompressed cue/bin: Compress screens
    mk("psx/%s/%s.bin" % (_t, _t), 460 * MB if "Quest" in _t else 610 * MB)
    with open(os.path.join(R, "psx", _t, _t + ".cue"), "w") as _f:
        _f.write('FILE "%s.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n' % _t)
mk("psx/Gone (USA).zip.part", 120 * MB)
mk("psx/Dolor Dash (USA).7z", 300 * MB)                          # a copy of a game already here as CHD
os.makedirs(os.path.join(R, "psx", "Empty Game"), exist_ok=True)
es = FakeES(R, {"psx": {".chd", ".cue", ".m3u"}, "ps2": {".iso", ".chd"}, "gba": {".gba", ".zip"}})
es.mem["psx"][os.path.join(R, "psx/Dolor Dash (USA).chd")].update(lastplayed="20261003T200000", playcount="4")
rommlib.ES_API = es.url

rb = uifacade.load(APP, "rb")             # rommbrowser.pygame, every UI name readable as rb.X
_real_statvfs = os.statvfs


def _fixed_statvfs(path):
    """The same disk in every run (1.8 TB, 644 GB free): screenshots don't drift with the host's disk."""
    st = _real_statvfs(path)
    frsize = 4096
    return os.statvfs_result((frsize, frsize, 1800 * 10 ** 9 // frsize, 644 * 10 ** 9 // frsize,
                              644 * 10 ** 9 // frsize, st.f_files, st.f_ffree, st.f_favail, st.f_flag,
                              st.f_namemax))


os.statvfs = _fixed_statvfs
rommlib.display_url = lambda url: "romm.example.com"     # not the mock's 127.0.0.1:<port>
pygame.init()
app = rb.App(None, headless=True)
app.cfg["theme"] = rb.apply_theme(args.theme)
app.downloader.hold = True               # scenes fake their own jobs; nothing really downloads
_rom = app.api.rom

import settle  # noqa: E402  (tools/settle.py)
settled = settle.install(app)            # scenes are drawn once background loads have landed


def _rom_with_members(rid):
    """The mock's generated disc zips list no members; real RomM usually does.
    Give PlayStation ones a cue + bin so the detail panel shows its Format row."""
    d = _rom(rid)
    for f in d.get("files") or []:
        if d.get("platform_id") == 6 and f["file_name"].endswith(".zip") and not f.get("archive_members"):
            stem = f["file_name"][:-4]
            f["archive_members"] = [{"name": stem + ".cue", "size": 120},
                                    {"name": stem + ".bin", "size": int(f["file_size_bytes"] * 1.6)}]
    return d


app.api.rom = _rom_with_members
LATER = time.time() + 10 ** 9            # fake queued jobs "retry" far in the future: the worker skips them
J = rommlib.Job


def fake_jobs():
    def job(name, system, state, **kw):
        j = J({"id": abs(hash(name)) % 100000, "name": name, "fs_name": name + ".zip",
               "fs_size_bytes": kw.pop("size", 700 * MB)}, system)
        j.state = state
        for k, v in kw.items():
            setattr(j, k, v)
        return j
    return [
        job("Ipsum Infiltrator (USA) (Disc 1)", "psx", J.DOWNLOADING, done_bytes=420 * MB, speed=38 * MB,
            total_bytes=700 * MB),
        job("Amet Racing Pro (USA)", "ps2", J.EXTRACTING, message=""),
        job("Fugiat Fighters (USA)", "psx", J.EXTRACTING, message="Waiting to unpack"),
        job("Veniam Chronicles (USA) (Disc 1)", "psx", J.QUEUED, short_space=3 << 30, retry_at=LATER,
            message="Waiting for space"),
        job("Sed Sprite (Europe)", "psx", J.QUEUED, retry_at=LATER),
        job("Dolor Dash (USA)", "psx", J.DONE, verified=True, message="Ready to play, checksum OK"),
        job("Flaky Cart (USA)", "gba", J.FAILED, message="Checksum doesn't match RomM after 2 tries"),
    ]


def fake_conversions():
    import json as _j
    rommlib.hold_conversions(False)
    q = rommlib.CHD_QUEUE_DIR
    os.makedirs(q, exist_ok=True)
    mk("psx/Veniam Chronicles (USA) (Disc 1).cue", 2 * MB)
    mk("gamecube/Commodo Cab (USA).iso", 1400 * MB)
    for n, t in (("1-run.json", {"system": "psx", "mode": "createcd",
                                 "sources": [os.path.join(R, "psx/Veniam Chronicles (USA) (Disc 1).cue")]}),
                 ("2-next.json", {"system": "gamecube", "mode": "rvz",
                                  "sources": [os.path.join(R, "gamecube/Commodo Cab (USA).iso")]})):
        with open(os.path.join(q, n), "w") as f:
            _j.dump(t, f)
    with open(os.path.join(rommlib.DATA_DIR, "chd-current.json"), "w") as f:
        _j.dump({"task": "1-run.json", "since": time.time() - 90, "file": "Veniam Chronicles (USA) (Disc 1).cue",
                 "step": "Converting", "pct": 42.0}, f)
    with open(os.path.join(rommlib.DATA_DIR, "chd-results.jsonl"), "w") as f:
        f.write(_j.dumps({"time": time.time() - 600, "task": "0-a.json", "result": "ok",
                          "message": "OK Elit Adventure 2 (USA).cue -> Elit Adventure 2 (USA).chd (0.43 GB -> 0.21 GB)",
                          "sources": ["Elit Adventure 2 (USA).cue"], "mode": "createcd"}) + "\n")
        f.write(_j.dumps({"time": time.time() - 300, "task": "0-b.json", "result": "failed",
                          "message": "Misplaced.iso: not a GameCube/Wii disc image (dolphin-tool verify failed)",
                          "sources": ["Misplaced.iso"], "mode": "rvz", "retry": {"mode": "rvz"}}) + "\n")


def no_conversions():
    """Nothing queued, running or finished: the empty Activity screen."""
    import shutil as _sh
    _sh.rmtree(rommlib.CHD_QUEUE_DIR, ignore_errors=True)
    rommlib.hold_conversions(False)
    for n in ("chd-current.json", "chd-results.jsonl"):
        try:
            os.remove(os.path.join(rommlib.DATA_DIR, n))
        except OSError:
            pass


def fake_audit():
    """A finished quick check with one damaged and one differing game."""
    stem = os.path.join(R, "psx/Tempor Legends (USA)/Tempor Legends (USA) (Disc 1).chd")
    rommlib.save_audit([
        {"system": "psx", "path": stem, "severity": "damaged", "rom": None,
         "problem": "Disc image cut short: boot files missing"},
        {"system": "psx", "path": os.path.join(R, "psx/Dolor Dash (USA).chd"), "severity": "differs", "rom": None,
         "problem": "Smaller than RomM's copy (a trimmed image?)"}],
        {"checked": 412, "matched": 398, "unmatched": 14}, False)
    with open(rommlib.audit_path()) as f:              # a fixed time: screenshots don't change by the minute
        d = json.load(f)
    d["time"] = 1791057600                             # 2026-10-03 20:00 UTC
    rommlib.atomic_write_json(rommlib.audit_path(), d)


def fake_unseen():
    """Files ES can't see: zips in a system that doesn't take .zip, a GameCube/Wii
    image in n64/, a file no system lists (the Downloader's es map plays es_systems.cfg)."""
    disc = {".gcm", ".iso", ".gcz", ".ciso", ".wbfs", ".rvz", ".m3u"}
    app.downloader.es = {"psx": {".chd", ".cue", ".m3u"}, "ps2": {".iso", ".chd"}, "gba": {".gba", ".zip"},
                         "n64": {".z64", ".n64", ".v64", ".zip", ".7z"}, "gamecube": disc, "wii": set(disc)}
    mk("ps2/Lorem Racer (Homebrew).zip", 1900 * MB)
    mk("ps2/Ipsum Arena (Homebrew).7z", 2600 * MB)
    mk("n64/Dolor Cube (Homebrew).rvz", 1100 * MB)
    mk("n64/Sit Amet Demo (Homebrew).z64", 8 * MB)
    mk("psx/Elit Notes.xyz", 1 * MB)


ENC3DS = os.path.join(R, "3ds", "Lorem Cart (Encrypted).3ds")


def encrypted_3ds():
    """A 3DS dump Z3DS can't pack (its NCCH says encrypted): Compress lists it as can't convert.
    reset() removes it, so it shows up in the Compress scenes only."""
    os.makedirs(os.path.dirname(ENC3DS), exist_ok=True)
    h = bytearray(0x200)
    h[0x100:0x104] = b"NCCH"
    with open(ENC3DS, "wb") as f:
        f.write(bytes(h))
        f.truncate(1800 * MB)


def paused_cancel():
    """The cancel prompt of a paused download (deletes the partial file)."""
    jobs = fake_jobs()
    j = jobs[4]
    j.state, j.done_bytes = J.PAUSED, 230 * MB
    app.downloader.jobs.__init__(jobs)
    app.downloader.hold = False
    app.push(rb.DownloadsScreen(app))
    app.stack[-1].confirm_cancel(j)


def install_some():
    """A few games from the mock library 'on the box' (files + install records)."""
    lib = app.library
    for r in app.api.roms(platform_ids=[first_platform()["id"]], limit=6)["items"][:4]:
        p = os.path.join(R, "psx", r["fs_name_no_ext"] + ".chd")
        mk(os.path.relpath(p, R), int(r.get("fs_size_bytes") or 300 * MB) // 2)
        lib.mark(r, "psx", [p])
    return r


def compare_box():
    """Games for Compare with RomM: the app's compressed installs, one RomM changed since, one
    copied over by hand that matches, one at another size, and the homebrew already here."""
    lib = app.library
    roms = app.api.roms(platform_ids=[first_platform()["id"]], limit=10)["items"]
    install_some()
    changed = roms[4]
    p = os.path.join(R, "psx", changed["fs_name_no_ext"] + ".chd")
    mk(os.path.relpath(p, R), 300 * MB)
    lib.mark(dict(changed, fs_size_bytes=(changed.get("fs_size_bytes") or 0) + 1, sha1_hash="0" * 40), "psx", [p])
    mk("psx/" + roms[5]["fs_name"], int(roms[5].get("fs_size_bytes") or 1))
    mk("psx/" + roms[6]["fs_name"], int(roms[6].get("fs_size_bytes") or 1) + 4096)


def compare_collection():
    """A collection with games compare_box put on the box."""
    here = {str(k) for k in app.library.snapshot()}
    for c in app.api.collections():
        ids = {str(r["id"]) for r in app.api.roms_all(collection=("collection_id", c["id"]))}
        if ids & here:
            return {"name": c["name"], "tag": "", "group": "Your collections", "count": c["rom_count"],
                    "full": c["name"], "param": "collection_id", "id": c["id"]}
    return collection_item()


def compare_at(filt="all", sel=0, collection=False, wait=False, theme_key=None):
    """Compare with RomM on PlayStation (or a collection), a filter and row chosen."""
    compare_box()
    if theme_key:
        rb.apply_theme(theme_key)
    if collection:
        it = compare_collection()
        sc = rb.CompareScreen(app, it["full"], collection=it)
    else:
        sc = rb.CompareScreen(app, "PlayStation", platform=first_platform())
    app.push(sc)

    def after():
        if wait:
            sc.result = None
            return
        sc.filt = filt
        sc.sel = min(sel, max(0, len(sc.listed()) - 1))
    app._after = after


def whats_new(theme_key=None, prev="0.8.2"):
    if theme_key:
        rb.apply_theme(theme_key)
    app.push(rb.WhatsNewScreen(app, rommlib.release_notes(), prev))


def install_backlog():
    """Three of the mock's backlog games on the box."""
    lib = app.library
    got = [r for r in app.api.user_list("backlog", limit=100)["items"] if app.system_for(r)]
    for r in got[:6:2]:
        sy = app.system_for(r)
        p = os.path.join(R, sy, r["fs_name_no_ext"] + ".chd")
        mk(os.path.relpath(p, R), int(r.get("fs_size_bytes") or 300 * MB) // 2)
        lib.mark(r, sy, [p])


def game_night(players=0, here=False, rolls=4, scope=None, then=None):
    """Game night from Home after a few rolls (the same ones every run)."""
    import random
    random.seed(7)
    if here:
        install_some()
    app.night_opts = {"players": players, "here": here}
    scr = rb.GameNightScreen(app, scope or rb.Scope.library(app))
    app.push(scr)

    def more():
        for _ in range(rolls - 1):
            rom, why, pool = rb.gamenight.roll(app, scr.scope, players, here, [r["id"] for r in scr.picks])
            if rom:
                scr.picks.append(rom)
                scr.rolls += 1
        scr.sel = max(0, len(scr.picks) - 1)
        if then:
            then()
    app._after = more


def search_ready():
    sc = rb.SearchScreen(app)
    app.push(sc)
    sc.query = "lorem"
    sc.pending_at = time.time() - 1          # run the live search now


def first_platform():
    return next(p for p in app.platforms if p["_system"] == "psx")


def collection_item():
    regular = app.api.collections()
    c = regular[0]
    return {"name": c["name"], "tag": "", "group": "Your collections", "count": c["rom_count"],
            "full": c["name"], "param": "collection_id", "id": c["id"]}


def a_rom():
    return app.api.roms(platform_ids=[first_platform()["id"]], limit=5)["items"][0]


def party_list(players=2, menu=False):
    """A game list with the party filter on (the menu open on it, with menu)."""
    app.player_counts = app.api.rom_filters().get("player_counts")
    gl = rb.GameListScreen(app, "PlayStation", platform=first_platform())
    gl.players = players
    gl.reset()
    app.push(gl)
    if menu:
        def opened():
            gl.action("x")
            app.modal.sel = next(i for i, e in enumerate(app.modal.entries) if e and e[0] == "Players")
        app._after = opened


def game_page(here=False, plain=False):
    """A game's page from the PlayStation list: Lorem Odyssey (your status, note, RA, screenshots);
    here: its first disc on the box, played 3 days ago; plain: a game RomM knows little about."""
    gl = rb.GameListScreen(app, "PlayStation", platform=first_platform())
    app.push(gl)
    if plain:
        rom = a_rom()
    else:
        rom = next(r for r in app.api.roms(platform_ids=[first_platform()["id"]], search="Lorem Odyssey",
                                           limit=50)["items"] if r["fs_name_no_ext"] == "Lorem Odyssey (USA) (Disc 1)")
    if here:
        p = os.path.join(R, "psx", rom["fs_name_no_ext"] + ".chd")
        mk(os.path.relpath(p, R), 257 * MB)
        app.library.mark(rom, "psx", [p])
        es.mem["psx"][p] = {"playcount": "4", "lastplayed": time.strftime("%Y%m%dT%H%M%S",
                                                                          time.localtime(time.time() - 3 * 86400))}
        rommlib._PLAY.clear()
    app.push(rb.GameScreen(app, rom, source=gl))


def settings_at(key):
    s = rb.SettingsScreen(app)
    s.sel = next(i for i, it in enumerate(s.items()) if it[0] == key)
    return s


def picker_at(key, oled=False):
    """Settings, Theme's picker with the cursor on a theme (previewed, as moving there would)."""
    app.push(settings_at("theme"))
    p = rb.ThemePickerScreen(app)
    app.push(p)
    p.go(p.items.index(key))
    if oled:
        p.oled.add(key)
    p.preview()


def welcome_at(step=0, url="https://romm.", view="keys", code="", cert=None, field="url"):
    w = rb.WelcomeScreen(app, step=step)
    w.url_kb.clear()
    w.url_kb.insert(url)
    w.code_kb.insert(code, notify=False)
    w.view, w.cert, w.field = view, cert, field
    if field == "token":
        w.token_kb.insert("rmm_" + "7f3a9c2e" * 3)
    app.push(w)


def custom_at(sel=0, **v):
    """Settings, Theme, Custom with these choices made (previewed, as making them would)."""
    s = rb.CustomThemeScreen(app)
    s.v.update(v)
    s.sel = sel
    s.preview()
    return s


class WidgetsGallery(rb.Screen):
    """Every rbui/widgets building block on one frame (the "widgets-gallery" scene)."""

    def __init__(self, app_):
        super().__init__(app_)
        install_some()
        self.roms = app_.api.roms(platform_ids=[first_platform()["id"]], limit=14)["items"]
        self.colls = [(c["name"], c.get("rom_count", 0)) for c in app_.api.collections()[:3]]
        self.shelf = rb.Shelf((90, 120), 12)
        self.cards = rb.Shelf((212, 104), 12)
        self.installed = next(r for r in self.roms if app_.rom_status(r)[0] == "installed")
        self.remote = next(r for r in self.roms if app_.rom_status(r)[0] != "installed")

    def crumbs(self):
        return ["Widgets"]

    def draw(self, c):
        from types import SimpleNamespace
        f, a, roms = self.f, self.app, self.roms
        x0, x1 = 48, 744
        rb.pill_row(c, f.body, ["Search", "Platforms", "Collections", "Downloads", "On this box", "Settings"],
                    x0, x1, 84, focus=1)
        rb.text(c, f.h3, "Recently added", (x0, 136), rb.TEXT)
        rb.text(c, f.small, "%d games" % len(roms), (x1, 139), rb.FAINT, "topright")
        self.shelf.draw(c, a, roms, x0, x1, 166, sel=0)
        cards = [(name, roms[3 * i:3 * i + 4], n) for i, (name, n) in enumerate(self.colls)]
        self.cards.draw(c, a, cards, x0, x1, 306, sel=1, tile=lambda c_, it, r, on: rb.collection_card(
            c_, a, it[0], it[1], r, on, "%d games" % it[2]))
        rb.pill_row(c, f.small, ["All · 12", "Matches RomM · 8", "Newer in RomM · 2", "Not in RomM · 2"],
                    x0, x1, 426, active=1, dots=[None, rb.GREEN, rb.BLUE, rb.FAINT], h=32)
        for k, (col, title, parts) in enumerate((
                (rb.BLUE, "Downloading 42%, 12.0 MB/s", [(0.42, rb.BLUE)]),
                (rb.AMBER, "Backlog: 6 fit now, 2 wait for space", [(0.6, rb.ACCENT), (0.3, rb.AMBER)]))):
            r = pygame.Rect(x0, 472 + k * 64, x1 - x0, 58)
            rb.rrect(c, rb.PANEL, r, 12)
            rb.state_stripe(c, r, col)
            rb.cover_tile(c, a, roms[5 + k], (r.left + 16, r.top + 6, 34, 46), radius=4)
            rb.text(c, f.body, rb.rom_title(roms[5 + k]), (r.left + 62, r.top + 8), rb.TEXT, maxw=300)
            rb.text(c, f.small, title, (r.left + 62, r.top + 32), rb.FAINT, maxw=300)
            rb.meter(c, (r.right - 260, r.centery - 3, 240, 6), parts)
        y = 616
        x = rb.status_chip(c, f.small, "Playing", (x0, y + 12), anchor="midleft").right + 10
        x = rb.status_chip(c, f.small, "On your backlog", (x, y + 12), rb.AMBER, "midleft").right + 14
        x = rb.stars(c, 3, (x, y + 12)) + 14
        rb.tag(c, f.tiny, "roms/psx", (x, y + 12), "midleft")
        # the hero side: backdrop, cover, facts, the verbs
        rom = self.installed
        rb.backdrop(c, a, rom, (768, 64, 1280 - 768, 300))
        art = rb.cover(c, a, rom, (788, 84, 120, 160), fit=True)
        rb.focus_ring(c, art)
        tx, tw = 928, 1232 - 928
        rb.text(c, f.h2, rb.rom_title(rom), (tx, 84), rb.TEXT, maxw=tw)
        rb.text(c, f.small, "PlayStation · 1998 · Racing", (tx, 118), rb.DIM, maxw=tw)
        rb.facts(c, f, [("Size", rb.human_size(rom.get("fs_size_bytes"))), ("Format", "CHD (from CUE/BIN)"),
                        ("Region", "USA"), ("Developer", "Lorem Ipsum Soft"), ("Players", "1-2"),
                        ("Released", "1998"), ("Genre", "Racing, Arcade"), ("Manual", "Included")],
                 (tx, 148, tw, 112), cols=2, bg=rb.BG, slot="gallery-facts", strong=("Size",))
        vr = pygame.Rect(788, 290, 1232 - 788, rb.VERB_H)
        rb.verb_button(c, f, vr, **rb.game_verb(a, rom))
        rb.verb_note(c, f, vr, rb.game_note(a, rom))
        vr2 = vr.move(0, 100)
        rb.verb_button(c, f, vr2, **rb.game_verb(a, self.remote))
        rb.verb_note(c, f, vr2, rb.game_note(a, self.remote))
        job = SimpleNamespace(state=J.DOWNLOADING, done_bytes=42, total_bytes=100, speed=12 << 20)
        rb.verb_button(c, f, (788, 490, vr.width, rb.VERB_H), **rb.game_verb(a, self.remote, ("active", job)))
        w1 = vr.width * 3 // 5
        rb.verb_button(c, f, (788, 556, w1, rb.VERB_H), "Unpick", "quiet", "3 picked")
        rb.verb_button(c, f, (788 + w1 + 12, 556, vr.width - w1 - 12, rb.VERB_H), "Not in RomM", "off")
        rb.progress_bar(c, (788, 630, vr.width, 8), 0.7, rb.GREEN)


# (name, setup) — setup pushes a screen / opens a modal; returns an optional
# "ready" predicate (default: no background work pending)
SCENES = [
    ("home", lambda: None),
    ("platforms", lambda: app.push(rb.PlatformScreen(app))),
    ("collections", lambda: app.push(rb.CollectionScreen(app))),
    ("collection-menu", lambda: (app.push(rb.CollectionScreen(app)),
                                 setattr(app, "_after", lambda: app.stack[-1].menu(collection_item())))),
    ("game-list", lambda: app.push(rb.GameListScreen(app, "PlayStation", platform=first_platform()))),
    ("list-menu", lambda: (app.push(rb.GameListScreen(app, "PlayStation", platform=first_platform())),
                           setattr(app, "_after", lambda: app.stack[-1].action("x")))),
    ("list-picking", lambda: (app.push(rb.GameListScreen(app, "PlayStation", platform=first_platform())),
                              setattr(app, "_after", lambda: (setattr(app.stack[-1], "picking", True),
                                                              [app.dispatch(k) for k in ("r1", "ok", "down", "ok")])))),
    ("game-list-party", lambda: party_list(2)),
    ("list-menu-party", lambda: party_list(4, menu=True)),
    ("game-night", lambda: game_night()),
    ("game-night-here", lambda: game_night(players=2, here=True)),
    ("game-night-menu", lambda: game_night(rolls=1, then=lambda: app.dispatch("x"))),
    ("game-night-empty", lambda: game_night(players=4, here=True, rolls=1, scope=rb.Scope("Lorem", pool=[]))),
    ("backlog", lambda: (install_backlog(), app.push(rb.BacklogScreen(app)),
                         setattr(app, "_after", lambda: [app.dispatch("down") for _ in range(3)]))),
    ("backlog-tight", lambda: (install_backlog(), app.cfg.update(keep_free_gb=636),
                               app.push(rb.BacklogScreen(app)))),
    ("surprise-pick", lambda: (app.push(rb.GameListScreen(app, "PlayStation", platform=first_platform())),
                               setattr(app, "_after", lambda: app.stack[-1].surprise_me()))),
    ("home-recommended", lambda: setattr(app, "_after", lambda: setattr(
        app.stack[0], "sel", [it[0] for it in app.stack[0].items()].index("recommended")))),
    ("recommended", lambda: setattr(app, "_after", lambda: app.stack[0].open_recs())),
    ("search", search_ready),
    ("download-confirm", lambda: setattr(app, "modal", rb.DownloadConfirm(app, a_rom()))),
    ("downloads", lambda: (app.downloader.jobs.__init__(fake_jobs()), setattr(app.downloader, "hold", False),
                           fake_conversions(), app.push(rb.DownloadsScreen(app)))),
    ("downloads-compress", lambda: (app.downloader.jobs.__init__(fake_jobs()), setattr(app.downloader, "hold", False),
                                    fake_conversions(), app.push(rb.DownloadsScreen(app)),
                                    setattr(app, "_after", lambda: app.dispatch("right")))),
    ("downloads-paused", lambda: (app.downloader.jobs.__init__(fake_jobs()), setattr(app.downloader, "hold", True),
                                  fake_conversions(), rommlib.hold_conversions(True),
                                  app.push(rb.DownloadsScreen(app)),
                                  setattr(app, "_after", lambda: app.dispatch("right")))),
    ("downloads-picked", lambda: (app.downloader.jobs.__init__(fake_jobs()), setattr(app.downloader, "hold", False),
                                  rommlib.hold_conversions(False),
                                  app.push(rb.DownloadsScreen(app)),
                                  setattr(app, "_after", lambda: (setattr(app.stack[-1], "picking", True),
                                                                  [app.dispatch(k) for k in ("ok", "ok", "x")])))),
    ("conversions", lambda: (fake_conversions(), app.push(rb.ConversionsScreen(app)))),
    ("downloads-cancel", paused_cancel),
    ("downloads-empty", lambda: (app.downloader.jobs.clear(), no_conversions(), app.push(rb.DownloadsScreen(app)))),
    ("settings", lambda: app.push(rb.SettingsScreen(app))),
    ("settings-pair", lambda: app.push(settings_at("pair"))),
    ("pair-code", lambda: (app.push(settings_at("pair")), app.stack[-1].pair(),
                           app.stack[-1].type_text("abcd-23"))),
    ("settings-downloads", lambda: app.push(settings_at("overlap"))),
    ("settings-compression", lambda: app.push(settings_at("rvz"))),
    ("settings-app", lambda: app.push(settings_at("credits"))),
    ("rvz-confirm", lambda: (app.push(settings_at("rvz")), app.stack[-1]._toggle_tool("rvz"))),
    ("credits", lambda: app.push(rb.CreditsScreen(app))),
    ("settings-docs", lambda: app.push(settings_at("docs"))),
    ("docs", lambda: (app.push(rb.SettingsScreen(app)), app.push(rb.DocsScreen(app)))),
    ("settings-updates", lambda: app.push(settings_at("checkupdates"))),
    ("settings-theme", lambda: app.push(settings_at("theme"))),
    ("theme-picker", lambda: picker_at(rb.theme_base(app.cfg["theme"]))),
    ("theme-picker-consoles", lambda: picker_at("famicom")),
    ("theme-picker-light", lambda: picker_at("dreamcast")),
    ("theme-picker-moods", lambda: picker_at("synthwave")),
    ("theme-picker-oled", lambda: picker_at("playstation", oled=True)),
    ("theme-picker-custom", lambda: picker_at("custom")),
    ("theme-custom", lambda: (picker_at("custom"), app.push(rb.CustomThemeScreen(app)))),
    ("theme-custom-copy", lambda: (picker_at("synthwave"), app.stack[-1].edit_copy())),
    ("theme-custom-light", lambda: (picker_at("custom"), app.push(custom_at(accent=150, tint=30, strength=60,
                                                                            mode="light")))),
    ("theme-custom-oled", lambda: (picker_at("custom"), app.push(custom_at(accent=330, tint=280, strength=100,
                                                                           mode="oled", sel=2)))),
    ("welcome-server", lambda: welcome_at()),
    ("welcome-advanced", lambda: welcome_at(url="https://romm.example.com", view="advanced")),
    ("welcome-tls-error", lambda: welcome_at(url="https://romm.example.com", view="cert", cert="unknown")),
    ("welcome-token", lambda: welcome_at(url="https://romm.example.com", field="token")),
    ("welcome-pair", lambda: welcome_at(step=1, url="https://romm.example.com", code="ABCD2")),
    ("url-edit", lambda: app.push(rb.TextInputScreen(app, "RomM server URL", "https://romm.example.com",
                                                     lambda v: None, context="url", help=rb.field_help("url")))),
    ("token-edit", lambda: app.push(rb.TextInputScreen(app, "RomM client API token", "rmm_" + "7f3a9c2e" * 5,
                                                       lambda v: None, secret=True, context="token",
                                                       help=rb.field_help("token")))),
    ("on-this-box", lambda: (install_some(), fake_audit(), fake_conversions(), app.push(rb.OnThisBoxScreen(app)))),
    ("on-this-box-space", lambda: (app.push(rb.OnThisBoxScreen(app)), setattr(app.stack[-1], "sel", 1))),
    ("on-this-box-compress", lambda: (app.push(rb.OnThisBoxScreen(app)), setattr(app.stack[-1], "sel", 2))),
    ("compress", lambda: (encrypted_3ds(), app.push(rb.OnThisBoxScreen(app)), app.push(rb.CompressScreen(app)))),
    ("compress-cant", lambda: (encrypted_3ds(), app.push(rb.OnThisBoxScreen(app)), app.push(rb.CompressScreen(app)),
                               app.push(rb.CantConvertScreen(app, "3ds", [
                                   (ENC3DS, "an encrypted dump (Azahar runs decrypted ones only)")])))),
    ("compress-system", lambda: (app.push(rb.OnThisBoxScreen(app)), app.push(rb.CompressScreen(app)),
                                 app.push(rb.CompressSystemScreen(app, "psx")))),
    ("on-this-box-bios", lambda: (app.push(rb.OnThisBoxScreen(app)), setattr(app.stack[-1], "sel", 3))),
    ("on-this-box-check", lambda: (fake_audit(), app.push(rb.OnThisBoxScreen(app)), setattr(app.stack[-1], "sel", 4))),
    ("on-this-box-conversions", lambda: (fake_conversions(), app.push(rb.OnThisBoxScreen(app)),
                                         setattr(app.stack[-1], "sel", 5))),
    ("games-from-romm", lambda: (install_some(), app.push(rb.OnThisBoxScreen(app)), app.push(rb.InstalledScreen(app)))),
    ("games-from-romm-menu", lambda: (install_some(), app.push(rb.OnThisBoxScreen(app)),
                                      app.push(rb.InstalledScreen(app)),
                                      setattr(app, "_after", lambda: app.dispatch("ok")))),
    ("check-games", lambda: (fake_audit(), fake_unseen(), app.push(rb.OnThisBoxScreen(app)),
                             app.push(rb.CheckScreen(app)))),
    ("check-unseen", lambda: (fake_unseen(), app.push(rb.OnThisBoxScreen(app)), app.push(rb.CheckScreen(app)),
                              app.push(rb.UnseenScreen(app)))),
    ("check-unseen-menu", lambda: (fake_unseen(), app.push(rb.OnThisBoxScreen(app)), app.push(rb.CheckScreen(app)),
                                   app.push(rb.UnseenScreen(app)),
                                   setattr(app, "_after", lambda: app.stack[-1].action("ok")))),
    ("check-unseen-picked", lambda: (fake_unseen(), app.push(rb.OnThisBoxScreen(app)),
                                     app.push(rb.CheckScreen(app)), app.push(rb.UnseenScreen(app)),
                                     setattr(app, "_after", lambda: [app.dispatch(k) for k in
                                                                     ("x", "ok", "down", "ok", "ok")]))),
    ("manage", lambda: (app.push(rb.GameListScreen(app, "PlayStation", platform=first_platform())),
                        setattr(app, "_after", lambda: setattr(app, "modal", rb.ManageMenu(app, install_some()))))),
    ("storage", lambda: (app.push(rb.OnThisBoxScreen(app)), app.push(rb.SpaceScreen(app)))),
    ("free-up", lambda: app.push(rb.FreeUpScreen(app))),
    ("free-up-menu", lambda: (app.push(rb.FreeUpScreen(app)),
                              setattr(app, "_after", lambda: app.stack[-1].open_menu()))),
    ("leftovers", lambda: app.push(rb.LeftoversScreen(app))),
    ("bios", lambda: (app.push(rb.OnThisBoxScreen(app)), app.push(rb.BiosScreen(app)))),
    ("compare", lambda: compare_at()),
    ("compare-newer", lambda: compare_at("newer")),
    ("compare-not-in-romm", lambda: compare_at("not_in_romm")),
    ("compare-collection", lambda: compare_at(collection=True)),
    ("compare-waiting", lambda: compare_at(wait=True)),
    ("compare-oled", lambda: compare_at("newer", theme_key="playstation_oled")),
    ("compare-light", lambda: compare_at(sel=3, theme_key="dreamcast")),
    ("whats-new", lambda: whats_new()),
    ("whats-new-oled", lambda: whats_new("playstation_oled")),
    ("whats-new-light", lambda: whats_new("dreamcast", prev=None)),
    ("whats-new-offline", lambda: (app.push(rb.WhatsNewScreen(app, None, None)), setattr(
        app, "_after", lambda: app.stack[-1].__dict__.update(rel=None, lines=[], failed=True)))),
    ("whats-new-full", lambda: (whats_new(), app.stack[-1].full_notes())),
    ("settings-whatsnew", lambda: app.push(settings_at("whatsnew"))),
    ("widgets-gallery", lambda: app.push(WidgetsGallery(app))),
    ("game-page", lambda: game_page()),
    ("game-page-here", lambda: game_page(here=True)),
    ("game-page-plain", lambda: game_page(plain=True)),
    ("game-page-actions", lambda: (game_page(here=True), setattr(app, "_after", lambda: app.dispatch("x")))),
    ("toast", lambda: app.toast("Picked 3 games (4.2 GB) — check the list, then {X} to delete them")),
    ("confirm-danger", lambda: setattr(app, "modal", rb.Confirm(
        app, "Delete 3 games?", "Frees about 4.2 GB. Saves are kept. Press {A} to delete or {B} to keep them.",
        "Delete 3", lambda: None, danger=True))),
]
wanted = set(filter(None, args.scenes.split(",")))
SCENES = [s for s in SCENES if not wanted or s[0] in wanted]


def reset():
    if os.path.exists(ENC3DS):
        os.remove(ENC3DS)
    del app.stack[1:]
    app.stack[0].sel = 0
    rb.apply_theme(app.theme_key())          # a theme a picker scene previewed
    app.modal = None
    app.toasts = []
    app._after = None
    app.downloader.hold = True
    app.cfg["keep_free_gb"] = KEEP_FREE


KEEP_FREE = app.cfg.get("keep_free_gb", 20)
bad_tokens = set()
failures = []
results = {}
plan = [(st, name, setup) for st in args.styles.split(",") for name, setup in SCENES]
state = {"i": -1, "phase": "boot", "frames": 0}
SETTLE_FRAMES = 3                                  # consecutive settled frames before a scene is drawn


def set_style(st):
    rb.INPUT_MODE["kb"] = st == "kb"
    app.cfg["button_style"] = "xbox" if st == "kb" else st
    app.apply_config()


def step(app_):
    if state["phase"] == "boot":                   # until the platform list is in
        state["frames"] += 1
        if app.loaded or state["frames"] > 600:
            state["phase"] = "setup"
        return []
    if state["i"] >= len(plan):
        app.running = False
        return []
    if state["phase"] == "setup":
        state["i"] += 1
        if state["i"] >= len(plan):
            app.running = False
            return []
        st, name, setup = plan[state["i"]]
        reset()
        set_style(st)
        try:
            setup()
        except Exception as e:                     # record and move on
            failures.append((st, name, "setup: %r" % e))
            state["phase"] = "setup"
            return []
        state["phase"], state["frames"] = "wait", 0
        return []
    st, name, _ = plan[state["i"]]
    state["frames"] += 1
    # let background loads land: fixed 25 (+25 after _after) frames with --fixed-wait; otherwise as
    # soon as the app has settled for SETTLE_FRAMES frames in a row, those same frame counts the upper bound
    calm = state["calm"] = state.get("calm", 0) + 1 if not args.fixed_wait and settled() else 0
    if getattr(app, "_after", None) and (state["frames"] == 25 or calm >= SETTLE_FRAMES):
        after, app._after = app._after, None
        state["calm"] = 0
        try:
            after()
        except Exception as e:
            failures.append((st, name, "after: %r" % e))
        return []
    if state["frames"] < 50 and (args.fixed_wait or calm < SETTLE_FRAMES or app._after):
        return []
    state["calm"] = 0
    try:
        top = app.modal if app.modal is not None and hasattr(app.modal, "hints") else app.stack[-1]
        for tok, _ in app.stack[-1].hints():
            if tok not in rb.TOKENS:
                bad_tokens.add((name, tok))
        if name != "toast":
            app.toasts = []                        # messages from earlier steps would cover the screen
        app.draw()
        d = os.path.join(args.out, st)
        os.makedirs(d, exist_ok=True)
        pygame.image.save(app.canvas, os.path.join(d, name + ".png"))
        results.setdefault(name, []).append(st)
    except Exception as e:
        failures.append((st, name, "draw: %r" % e))
    state["phase"] = "setup"
    return []


try:
    app.run(step)
finally:
    es.close()
    mock.terminate()

# contact sheet
styles = args.styles.split(",")
os.makedirs(args.out, exist_ok=True)
with open(os.path.join(args.out, "index.html"), "w") as f:
    f.write("<!doctype html><meta charset=utf-8><title>RomM-o-Cera screens</title>"
            "<style>body{background:#111;color:#ddd;font:14px sans-serif}img{width:100%%;border:1px solid #333}"
            "table{border-collapse:collapse;width:100%%}td{vertical-align:top;padding:4px;width:%d%%}</style>"
            % (100 // max(1, len(styles))))
    f.write("<table><tr>" + "".join("<th>%s</th>" % html.escape(s) for s in styles) + "</tr>")
    for name, _ in SCENES:
        f.write("<tr><td colspan=%d><h3>%s</h3></td></tr><tr>" % (len(styles), html.escape(name)))
        for st in styles:
            f.write("<td><img src='%s/%s.png' loading=lazy></td>" % (st, name))
        f.write("</tr>")
    f.write("</table>")
README_SET = {"home": "home", "platforms": "platforms", "game-list": "game-list", "list-menu": "menu",
              "search": "search", "manage": "manage", "on-this-box": "on-this-box", "downloads": "downloads",
              "settings-compression": "settings", "collection-menu": "collections",
              "conversions": "conversions", "storage": "storage", "free-up": "free-up", "credits": "credits",
              "compress-system": "compress", "theme-picker": "theme-picker", "theme-custom": "custom-theme",
              "surprise-pick": "surprise", "home-recommended": "recommended"}
if args.readme:
    import shutil
    dst = os.path.join(APP, "screenshots")
    for scene, name in README_SET.items():
        src = os.path.join(args.out, "xbox", scene + ".png")
        if os.path.exists(src):
            shutil.copy(src, os.path.join(dst, name + ".png"))
    print("README screenshots updated in", dst)
print("%d screenshots in %s (%d scenes x %d styles)" % (sum(len(v) for v in results.values()), args.out,
                                                        len(SCENES), len(styles)))
for f_ in failures:
    print("FAIL", *f_)
for b in sorted(bad_tokens):
    print("UNKNOWN HINT TOKEN", *b)
sys.exit(1 if failures or bad_tokens else 0)
