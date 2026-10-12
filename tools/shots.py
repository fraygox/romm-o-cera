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

import importlib.machinery  # noqa: E402
import pygame  # noqa: E402
import requests  # noqa: E402
import rommlib  # noqa: E402
from fake_es import FakeES  # noqa: E402

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
os.makedirs(os.path.join(R, "psx", "Empty Game"), exist_ok=True)
es = FakeES(R, {"psx": {".chd", ".cue", ".m3u"}, "ps2": {".iso", ".chd"}, "gba": {".gba", ".zip"}})
es.mem["psx"][os.path.join(R, "psx/Dolor Dash (USA).chd")].update(lastplayed="20261003T200000", playcount="4")
rommlib.ES_API = es.url

rb = importlib.machinery.SourceFileLoader("rb", os.path.join(APP, "rommbrowser.pygame")).load_module()
rommlib.display_url = lambda url: "romm.example.com"     # not the mock's 127.0.0.1:<port>
pygame.init()
app = rb.App(None, headless=True)
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


def fake_audit():
    """A finished quick check with one damaged and one differing game."""
    stem = os.path.join(R, "psx/Tempor Legends (USA)/Tempor Legends (USA) (Disc 1).chd")
    rommlib.save_audit([
        {"system": "psx", "path": stem, "severity": "damaged", "rom": None,
         "problem": "Disc image cut short: boot files missing"},
        {"system": "psx", "path": os.path.join(R, "psx/Dolor Dash (USA).chd"), "severity": "differs", "rom": None,
         "problem": "Smaller than RomM's copy (a trimmed image?)"}],
        {"checked": 412, "matched": 398, "unmatched": 14}, False)


def install_some():
    """A few games from the mock library 'on the box' (files + install records)."""
    lib = app.library
    for r in app.api.roms(platform_ids=[first_platform()["id"]], limit=6)["items"][:4]:
        p = os.path.join(R, "psx", r["fs_name_no_ext"] + ".chd")
        mk(os.path.relpath(p, R), int(r.get("fs_size_bytes") or 300 * MB) // 2)
        lib.mark(r, "psx", [p])
    return r


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


def settings_at(key):
    s = rb.SettingsScreen(app)
    s.sel = next(i for i, it in enumerate(s.items()) if it[0] == key)
    return s


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
    ("search", search_ready),
    ("download-confirm", lambda: setattr(app, "modal", rb.DownloadConfirm(app, a_rom()))),
    ("downloads", lambda: (app.downloader.jobs.__init__(fake_jobs()), setattr(app.downloader, "hold", False),
                           app.push(rb.DownloadsScreen(app)))),
    ("conversions", lambda: (fake_conversions(), app.push(rb.ConversionsScreen(app)))),
    ("downloads-empty", lambda: (app.downloader.jobs.clear(), app.push(rb.DownloadsScreen(app)))),
    ("settings", lambda: app.push(rb.SettingsScreen(app))),
    ("settings-downloads", lambda: app.push(settings_at("overlap"))),
    ("settings-compression", lambda: app.push(settings_at("rvz"))),
    ("settings-app", lambda: app.push(settings_at("credits"))),
    ("rvz-confirm", lambda: (app.push(settings_at("rvz")), app.stack[-1]._toggle_rvz())),
    ("credits", lambda: app.push(rb.CreditsScreen(app))),
    ("url-edit", lambda: app.push(rb.TextInputScreen(app, "RomM server URL", "https://romm.example.com",
                                                     lambda v: None))),
    ("on-this-box", lambda: (install_some(), fake_audit(), fake_conversions(), app.push(rb.OnThisBoxScreen(app)))),
    ("on-this-box-space", lambda: (app.push(rb.OnThisBoxScreen(app)), setattr(app.stack[-1], "sel", 1))),
    ("on-this-box-compress", lambda: (app.push(rb.OnThisBoxScreen(app)), setattr(app.stack[-1], "sel", 2))),
    ("compress", lambda: (app.push(rb.OnThisBoxScreen(app)), app.push(rb.CompressScreen(app)))),
    ("compress-system", lambda: (app.push(rb.OnThisBoxScreen(app)), app.push(rb.CompressScreen(app)),
                                 app.push(rb.CompressSystemScreen(app, "psx")))),
    ("on-this-box-bios", lambda: (app.push(rb.OnThisBoxScreen(app)), setattr(app.stack[-1], "sel", 3))),
    ("on-this-box-check", lambda: (fake_audit(), app.push(rb.OnThisBoxScreen(app)), setattr(app.stack[-1], "sel", 4))),
    ("on-this-box-conversions", lambda: (fake_conversions(), app.push(rb.OnThisBoxScreen(app)),
                                         setattr(app.stack[-1], "sel", 5))),
    ("games-from-romm", lambda: (install_some(), app.push(rb.OnThisBoxScreen(app)), app.push(rb.InstalledScreen(app)))),
    ("check-games", lambda: (fake_audit(), app.push(rb.OnThisBoxScreen(app)), app.push(rb.CheckScreen(app)))),
    ("manage", lambda: (app.push(rb.GameListScreen(app, "PlayStation", platform=first_platform())),
                        setattr(app, "_after", lambda: setattr(app, "modal", rb.ManageMenu(app, install_some()))))),
    ("storage", lambda: (app.push(rb.OnThisBoxScreen(app)), app.push(rb.SpaceScreen(app)))),
    ("free-up", lambda: app.push(rb.FreeUpScreen(app))),
    ("free-up-menu", lambda: (app.push(rb.FreeUpScreen(app)),
                              setattr(app, "_after", lambda: app.stack[-1].menu()))),
    ("leftovers", lambda: app.push(rb.LeftoversScreen(app))),
    ("bios", lambda: (app.push(rb.OnThisBoxScreen(app)), app.push(rb.BiosScreen(app)))),
    ("toast", lambda: app.toast("Marked 3 games (4.2 GB) — check the list, then {X} to delete")),
    ("confirm-danger", lambda: setattr(app, "modal", rb.Confirm(
        app, "Delete 3 games?", "Frees about 4.2 GB. Saves are kept. Press {A} to delete or {B} to keep them.",
        "Delete 3", lambda: None, danger=True))),
]
wanted = set(filter(None, args.scenes.split(",")))
SCENES = [s for s in SCENES if not wanted or s[0] in wanted]


def reset():
    del app.stack[1:]
    app.modal = None
    app.toasts = []
    app._after = None
    app.downloader.hold = True


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
              "compress-system": "compress"}
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
