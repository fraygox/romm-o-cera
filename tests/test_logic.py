"""Pure-logic tests: no network, no RomM."""
import json
import os
import stat
import xml.etree.ElementTree as ET
import time
import zipfile
import zlib

import pytest

import rommlib
import rb_chd
import rb_finalize


# ------------------------------------------------------------ config & TLS

@pytest.mark.parametrize("value,expected", [
    (True, True), (False, False), (None, True), ("", True), (0, True), (1, True),
    ("true", True), ("yes", True), ("on", True),
    ("false", False), ("FALSE", False), ("0", False), ("no", False), ("off", False),
    ("/etc/ssl/my-ca.pem", "/etc/ssl/my-ca.pem"),
])
def test_norm_verify_fails_closed(value, expected):
    assert rommlib.norm_verify(value) == expected


def test_load_config_env_and_cleanup(isolated, monkeypatch):
    os.makedirs(rommlib.DATA_DIR)
    with open(rommlib.CONFIG_PATH, "w") as f:
        json.dump({"url": "https://a.example", "token": "rmm_x\n", "verify_tls": None}, f)
    cfg = rommlib.load_config()
    assert cfg["token"] == "rmm_x"                 # trailing newline stripped
    assert cfg["verify_tls"] is True               # null -> verification ON
    monkeypatch.setenv("ROMM_URL", "http://b.example")
    monkeypatch.setenv("ROMM_VERIFY_TLS", "false")
    cfg = rommlib.load_config()
    assert cfg["url"] == "http://b.example" and cfg["verify_tls"] is False


def test_save_config_is_private(isolated):
    rommlib.save_config({"url": "https://x", "token": "rmm_secret", "junk": 1})
    mode = stat.S_IMODE(os.stat(rommlib.CONFIG_PATH).st_mode)
    assert mode == 0o600
    saved = json.load(open(rommlib.CONFIG_PATH))
    assert "junk" not in saved and saved["token"] == "rmm_secret"
    assert saved["config_version"] == rommlib.CONFIG_VERSION and list(saved)[-1] == "config_version"


def test_display_url_masks_credentials():
    assert rommlib.display_url("https://bob:pw@romm.lan:8080/x") == "https://***@romm.lan:8080/x"
    assert rommlib.display_url("http://romm.lan") == "http://romm.lan"
    assert rommlib.url_has_userinfo("https://bob:pw@h") and not rommlib.url_has_userinfo("https://h")


def test_api_refuses_bad_setups():
    with pytest.raises(rommlib.RommError, match="No RomM server"):
        rommlib.Api("", "t").get("/api/platforms")
    with pytest.raises(rommlib.RommError, match="user:password"):
        rommlib.Api("https://u:p@127.0.0.1:9", "t").get("/api/platforms")
    a = rommlib.Api("https://h", " rmm_tok\n", None)
    assert a.s.headers["Authorization"] == "Bearer rmm_tok"
    assert a.s.verify is True


def test_cover_paths_must_be_server_relative(isolated):
    a = rommlib.Api("http://127.0.0.1:9", "t")
    for bad in ("@evil.example/x.png", "//evil.example/x.png", "https://evil.example/x.png"):
        assert a.fetch_cover({"id": 1, "path_cover_small": bad}) is None


# ------------------------------------------------------------ UI helpers

def test_letter_of(rb):
    assert rb.letter_of("Alpha") == "A"
    assert rb.letter_of("'Allo 'Allo") == "A"
    assert rb.letter_of("~Hack~ Labore") == "H"
    assert rb.letter_of("[BIOS] x") == "B"
    assert rb.letter_of("007 Racing") == "#"
    assert rb.letter_of("ゼルダ") == "ゼ"
    assert rb.letter_of("") == "#"


def test_letter_jump(rb):
    starts = [(0, "A"), (10, "B"), (20, "C")]
    assert rb.letter_jump(starts, 5, True) == (10, "B")
    assert rb.letter_jump(starts, 15, False) == (10, "B")     # back to this letter's start
    assert rb.letter_jump(starts, 10, False) == (0, "A")
    assert rb.letter_jump(starts, 25, True) is None
    assert rb.letter_jump([], 0, True) is None


def test_collection_kinds(rb):
    kinds = rb.collection_kinds(["Lorem: Best Of", "Theme: X", "Top 25: Y", "Series: S",
                                 "Favs: a", "Favs: b", "Favs: c", "Plain"])
    assert kinds == {"theme", "top 25", "series", "favs"}
    assert rb.collection_kinds(["Lorem: Best Of", "Road trip: cars"]) == set()


def test_rank_search_prefers_real_titles(rb):
    items = [{"name": "~Hack~ Lorem Thing", "regions": ["World"]},
             {"name": "BS ゼルダの伝説", "regions": ["Japan"]},
             {"name": "The Legend of Lorem", "regions": ["USA"]}]
    assert rb.rank_search("lorem", items)[0]["name"] == "The Legend of Lorem"


# ------------------------------------------------------------ CHD worker helpers

def test_sources_of_cue_and_gdi(tmp_path):
    cue = tmp_path / "G.cue"
    cue.write_text('FILE "G (Track 1).bin" BINARY\n  TRACK 01 MODE2/2352\nFILE "G (Track 2).bin" BINARY\n')
    assert [os.path.basename(p) for p in rb_chd.sources_of(str(cue))] == \
        ["G.cue", "G (Track 1).bin", "G (Track 2).bin"]
    gdi = tmp_path / "g.gdi"
    gdi.write_text('3\n1 0 4 2352 track01.bin 0\n2 450 0 2352 "Track Two.raw" 0\n3 45000 4 2352 track03.bin 0\n')
    assert [os.path.basename(p) for p in rb_chd.sources_of(str(gdi))] == \
        ["g.gdi", "track01.bin", "Track Two.raw", "track03.bin"]


def test_migrate_saves_copies_never_overwrites(isolated, tmp_path, monkeypatch):
    saves = tmp_path / "saves" / "psx"
    saves.mkdir(parents=True)
    (saves / "Game (Disc 1).srm").write_text("old")
    (saves / "Game (Disc 1).state.auto").write_text("st")
    (saves / "Game.srm").write_text("keep-me")
    monkeypatch.setattr(rommlib, "SAVES_ROOT", str(tmp_path / "saves"))
    n = rommlib.migrate_saves("psx", "/r/psx/Game (Disc 1).cue", "/r/psx/Game.m3u")
    assert n == 1                                  # .state.auto copied, Game.srm untouched
    assert (saves / "Game.srm").read_text() == "keep-me"
    assert (saves / "Game.state.auto").read_text() == "st"
    assert (saves / "Game (Disc 1).srm").exists()  # originals stay


def test_fix_m3us(tmp_path):
    (tmp_path / "G.m3u").write_text("G (Disc 1).cue\nG (Disc 2).cue\n")
    old = {os.path.normpath(str(tmp_path / "G (Disc 1).cue")): str(tmp_path / "G (Disc 1).chd"),
           os.path.normpath(str(tmp_path / "G (Disc 2).cue")): str(tmp_path / "G (Disc 2).chd")}
    assert rommlib.fix_m3us(str(tmp_path), old) == [str(tmp_path / "G.m3u")]
    assert (tmp_path / "G.m3u").read_text() == "G (Disc 1).chd\nG (Disc 2).chd\n"


# ------------------------------------------------------------ download pipeline (offline parts)

def _downloader(isolated, es=None):
    d = rommlib.Downloader.__new__(rommlib.Downloader)   # no worker thread
    d.cfg, d.es, d._chd_queued = {"chd": True}, es or {}, False
    return d


def test_zip_slip_rejected(isolated, tmp_path):
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("../../escape.txt", "x")
    d = _downloader(isolated)
    j = rommlib.Job({"id": 1, "name": "x"}, "psx")
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(rommlib.RommError, match="Unsafe path"):
        d._extract(str(z), str(out), j)
    assert not (tmp_path / "escape.txt").exists()


def test_postprocess_keeps_cart_zip_extracts_disc(isolated):
    es = {"snes": {".zip", ".sfc"}, "psx": {".cue", ".chd"}}
    d = _downloader(isolated, es)
    roms = rommlib.ROMS_ROOT
    os.makedirs(os.path.join(roms, "snes"))
    cart = os.path.join(roms, "snes", "Cart (USA).zip")
    with zipfile.ZipFile(cart, "w") as f:
        f.writestr("Cart (USA).sfc", b"\0" * 64)
    assert d._postprocess_file(rommlib.Job({"id": 1}, "snes"), cart) == [cart]
    os.makedirs(os.path.join(roms, "psx"))
    disc = os.path.join(roms, "psx", "Disc (USA).zip")
    with zipfile.ZipFile(disc, "w") as f:
        f.writestr("Disc (USA).cue", 'FILE "Disc (USA).bin" BINARY\n')
        f.writestr("Disc (USA).bin", b"\0" * 64)
    j = rommlib.Job({"id": 2}, "psx")
    out = d._postprocess_file(j, disc)
    assert out == [os.path.join(roms, "psx", "Disc (USA)")]
    assert not os.path.exists(disc) and j.chd_task
    task = json.load(open(os.path.join(rommlib.CHD_QUEUE_DIR, os.listdir(rommlib.CHD_QUEUE_DIR)[0])))
    assert task["mode"] == "createcd" and task["sources"][0].endswith("Disc (USA).cue")


# ------------------------------------------------------------ gamelist merge

# ------------------------------------------------------------ uninstall

def test_uninstall_only_touches_the_game(isolated):
    lib = rommlib.Library()
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    os.makedirs(sysdir)
    saves = os.path.join(rommlib.USERDATA, "saves", "psx")
    os.makedirs(saves)
    for n in ("Game (Disc 1).chd", "Game (Disc 2).chd", "Other.chd"):
        open(os.path.join(sysdir, n), "wb").write(b"x" * 10)
    open(os.path.join(sysdir, "Game.m3u"), "w").write("Game (Disc 1).chd\nGame (Disc 2).chd\n")
    open(os.path.join(saves, "Game.srm"), "w").write("save")
    outside = os.path.join(rommlib.USERDATA, "precious.bin")
    open(outside, "w").write("keep")
    os.symlink(outside, os.path.join(sysdir, "Evil.chd"))     # must not follow out of roms/
    rom = {"id": 7, "name": "Game", "fs_name": "Game.m3u", "fs_name_no_ext": "Game"}
    paths, freed = lib.uninstall(rom, "psx")
    left = sorted(os.listdir(sysdir))
    assert left == ["Evil.chd", "Other.chd"] and freed > 0
    assert os.path.exists(os.path.join(saves, "Game.srm")) and open(outside).read() == "keep"
    q = [json.loads(l) for l in open(rommlib.GAMELIST_QUEUE)]
    assert q[-1]["system"] == "psx" and len(q[-1]["remove"]) == 3
    evil = {"id": 8, "name": "Evil", "fs_name": "Evil.chd", "fs_name_no_ext": "Evil"}
    assert lib.paths_for(evil, "psx") == []                  # symlink out of roms/ is refused


# ------------------------------------------------------------ BIOS detection

def _bios_db(isolated, entries):
    with open(rommlib.BIOS_DB, "w") as f:
        f.write("systems = %r\n" % {"psx": {"name": "PSX", "biosFiles": entries},
                                    "segacd": {"name": "CD", "biosFiles": [
                                        {"md5": "0" * 32, "file": "bios/bios_CD_U.bin"}]}})


def test_missing_bios_and_aliases(isolated):
    import hashlib
    good = b"real-ish bios"
    os.makedirs(os.path.join(rommlib.USERDATA, "bios"))
    open(os.path.join(rommlib.USERDATA, "bios", "have.bin"), "wb").write(good)
    open(os.path.join(rommlib.USERDATA, "bios", "wrong.bin"), "wb").write(b"nope")
    _bios_db(isolated, [{"md5": hashlib.md5(good).hexdigest(), "file": "bios/have.bin"},
                        {"md5": "1" * 32, "file": "bios/wrong.bin"},
                        {"md5": "2" * 32, "file": "bios/absent.bin"},
                        {"md5": "3" * 32, "file": "bios/x.zip", "zippedFile": "in.rom"}])
    miss = {m["file"] for m in rommlib.missing_bios("psx")}
    assert miss == {"bios/wrong.bin", "bios/absent.bin"}       # zipped entries skipped
    assert [m["file"] for m in rommlib.missing_bios("megacd")] == ["bios/bios_CD_U.bin"]  # alias


def test_controller_styles(rb):
    rb.PAD_STYLE.update(style="playstation", ok="south", back="east")
    assert rb.pad_face("A")[:2] == ("shape", "cross") and rb.pad_face("B")[1] == "circle"
    assert rb.pad_face("START")[1] == "Options"
    rb.PAD_STYLE.update(style="nintendo")
    assert rb.pad_face("A")[1] == "B"          # confirm is the south button = "B" on Nintendo
    rb.PAD_STYLE.update(ok="east", back="south")
    assert rb.pad_face("A")[1] == "A"          # swapped confirm follows the physical button
    rb.PAD_STYLE.update(style="xbox", ok="south", back="east")
    assert rb.style_from_names(["Sony Interactive Entertainment DualSense Wireless Controller"]) == "playstation"
    assert rb.style_from_names(["Nintendo Switch Pro Controller"]) == "nintendo"
    assert rb.style_from_names(["8BitDo Ultimate 2C", "Xbox Wireless Controller"]) == "xbox"
    assert rb.GLYPH_RE.split("Press {START} now") == ["Press ", "START", " now"]


def test_junk_tags_keep_homebrew():
    tags = ["Proto 2", "Beta", "E3 2011 demo", "Hack", "Homebrew", "Homebrew Demo", "Aftermarket",
            "USA", "Kiosk", "Rev 1", "Unlicensed"]
    assert sorted(rommlib.junk_tags(tags)) == ["Beta", "E3 2011 demo", "Hack", "Kiosk", "Proto 2"]


def test_space_estimate_and_fit():
    gb = 1 << 30
    cart = ({"fs_size_bytes": 1 * gb}, "snes")
    disc = ({"fs_size_bytes": 1 * gb}, "psx")
    assert rommlib.space_needed(*cart) == gb and rommlib.space_needed(*disc) == int(2.2 * gb)
    fit, used = rommlib.fit_in_space([disc, cart, disc], free=5 * gb)
    assert fit == [disc] and used == int(2.2 * gb)          # 2 GB margin kept free; order kept
    fit, _ = rommlib.fit_in_space([cart, cart, cart], free=10 * gb)
    assert len(fit) == 3


def test_update_check_uses_cache(isolated, monkeypatch):
    import time as _t
    monkeypatch.setattr(rommlib, "UPDATE_CACHE", os.path.join(rommlib.DATA_DIR, "update-check.json"))
    os.makedirs(rommlib.DATA_DIR)
    with open(rommlib.UPDATE_CACHE, "w") as f:
        json.dump({"checked": _t.time(), "latest": "v9.9.9"}, f)
    assert rommlib.newer_release({"check_updates": True}) == "v9.9.9"       # no network: cache is fresh
    with open(rommlib.UPDATE_CACHE, "w") as f:
        json.dump({"checked": _t.time(), "latest": "v" + rommlib.VERSION}, f)
    assert rommlib.newer_release({"check_updates": True}) is None
    assert rommlib.newer_release({"check_updates": False}) is None
    assert rommlib._vtuple("v0.10.0") > rommlib._vtuple("0.9.9")


def test_palette_cover_does_not_crash(rb):
    """Regression: an 8-bit (palette) cover crashed the detail panel —
    smoothscale only takes 24/32-bit surfaces."""
    import pygame
    pal = pygame.Surface((40, 60), depth=8)
    pal.set_palette([(i, i, i) for i in range(256)])
    img = rb.as_rgba(pal)
    assert img.get_bitsize() == 32
    assert rb.smooth_scale(img, (20, 30)).get_size() == (20, 30)
    assert rb.smooth_scale(pal, (20, 30)).get_size() == (20, 30)      # fallback path


def test_keyboard_alongside_pad_and_typing(rb, monkeypatch):
    """A real keyboard works next to a pad; Batocera's evmapy echoes of pad
    presses (arriving before OR after the pad event, any key) are dropped and
    never flip the hints to keyboard labels; text fields take typed text."""
    import pygame
    clock = [1000.0]
    monkeypatch.setattr(rb.time, "time", lambda: clock[0])
    inp = rb.Input(False)
    rb.INPUT_MODE["kb"] = False
    K = lambda key: pygame.event.Event(pygame.KEYDOWN, key=key, mod=0)
    pad = pygame.event.Event(pygame.CONTROLLERBUTTONDOWN, button=pygame.CONTROLLER_BUTTON_DPAD_DOWN,
                             instance_id=0)
    out = []
    inp.handle(pad, out)                          # pad first, echo 20 ms later
    clock[0] += 0.02
    inp.handle(K(pygame.K_DOWN), out)
    clock[0] += 0.2
    inp.flush(out)
    assert out == ["down"] and not rb.INPUT_MODE["kb"]
    clock[0] += 1
    out = []
    inp.handle(K(pygame.K_SPACE), out)            # echo first (Select -> Space), pad 30 ms later
    clock[0] += 0.03
    inp.handle(pygame.event.Event(pygame.CONTROLLERBUTTONDOWN, button=pygame.CONTROLLER_BUTTON_BACK,
                                  instance_id=0), out)
    clock[0] += 0.2
    inp.flush(out)
    assert out == ["select"] and not rb.INPUT_MODE["kb"] and rb.pad_face("A")[1] == "A"
    clock[0] += 1                                 # a real key press, no pad around
    out = []
    inp.handle(K(pygame.K_DOWN), out)
    clock[0] += 0.1
    inp.flush(out)
    assert out == ["down"] and rb.INPUT_MODE["kb"] and rb.pad_face("A")[1] == "Enter"
    inp.text_mode = True
    out = []
    clock[0] += 1
    for ev in (pygame.event.Event(pygame.TEXTINPUT, text="z"), K(pygame.K_x), K(pygame.K_BACKSPACE),
               K(pygame.K_RETURN)):
        inp.handle(ev, out)
    clock[0] += 0.1
    inp.flush(out)
    assert out == ["text:z", "x", "start"]
    rb.INPUT_MODE["kb"] = False


def test_touch_taps_rows_hints_and_dialogs(isolated, rb, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    app = rb.App(None, headless=True)
    app.push(rb.HomeScreen(app))
    app.draw()
    home = app.stack[-1]
    # tap the Settings tile (index 6) -> opens Settings in one tap
    rect = next(r for r, fn in app.targets if r.left == 48 and r.top == 84 + 71 * 6)
    app.tap(rect.center)
    assert isinstance(app.stack[-1], rb.SettingsScreen)
    app.draw()
    # footer hints are tappable: "B Back" leaves Settings
    back = [r for r, fn in app.targets if r.top == rb.H - 46]
    assert back
    app.tap((60, 33))                                    # the logo goes Home
    assert isinstance(app.stack[-1], rb.HomeScreen)
    # dialogs: tap outside closes, tap the button runs it
    hits = []
    app.modal = rb.Confirm(app, "T", "body", "OK", lambda: hits.append(1))
    app.draw()
    app.tap((5, 300))
    assert app.modal is None and not hits
    app.modal = rb.Confirm(app, "T", "body", "OK", lambda: hits.append(1))
    app.draw()
    okr = next(r for r, fn in app.targets[app.modal_from:] if fn and r.width == 260)
    app.tap(okr.center)
    assert hits == [1] and app.modal is None
    # a finger's drag up = scroll down (SDL marks every event a touch makes)
    acts = []
    app.pointer(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(400, 400), touch=True), acts)
    app.pointer(pygame.event.Event(pygame.MOUSEMOTION, buttons=(1, 0, 0), pos=(400, 300), touch=True), acts)
    app.pointer(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=(400, 300), touch=True), acts)
    assert acts == ["down", "down"]
    # a mouse doesn't drag: a click must never turn into a scroll (the wheel scrolls)
    acts = []
    app.pointer(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(400, 400)), acts)
    app.pointer(pygame.event.Event(pygame.MOUSEMOTION, buttons=(1, 0, 0), pos=(400, 300)), acts)
    app.pointer(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=(400, 300)), acts)
    assert acts == []


def test_no_move_sound_at_list_edges(isolated, rb, monkeypatch):
    """Holding a direction at the top/bottom of a list repeats the action but
    nothing moves: no sound. Moving, or cycling a setting, still clicks."""
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    app = rb.App(None, headless=True)
    played = []
    app.sfx.play = played.append
    app.push(rb.SettingsScreen(app))
    app.dispatch("up")                    # Settings wraps: moves to the last row
    assert played == ["move"]
    scr = app.stack[-1]
    scr.sel = 0
    played.clear()
    app.push(rb.InstalledScreen(app))
    inst = app.stack[-1]
    inst.items = [{"id": 1, "name": "A", "system": "gba", "when": "", "size": 1}]
    for _ in range(5):
        app.dispatch("up")                # top of a one-row list: nothing moves
    app.dispatch("down")
    assert played == []
    app.pop()
    i = next(k for k, it in enumerate(scr.items()) if it[0] == "speed")
    scr.sel = i
    app.dispatch("right")                 # cycles a setting in place
    assert played == ["move"]


def test_single_member_zip_without_member_list(tmp_path):
    """RomM records some zips with no archive_members; their file hashes are
    the single member's content (e.g. a DS game)."""
    import hashlib
    rom = bytes(range(256)) * 300
    zp = tmp_path / "Labore (USA).zip"
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("Labore (USA).nds", rom)
    rec = {"file_name": "Labore (USA).zip", "sha1_hash": hashlib.sha1(rom).hexdigest(), "archive_members": []}
    assert rommlib.file_hash_ok(str(zp), rec) == (True, "zip")
    assert rommlib.file_hash_ok(str(zp), dict(rec, sha1_hash="0" * 40)) == (False, "zip")
    assert rommlib.file_hash_ok(str(zp), {"file_name": "x.zip", "crc_hash": "%08x" % zlib.crc32(rom)}) == (True, "zip")
    with zipfile.ZipFile(zp, "a") as z:
        z.writestr("readme.txt", b"hi")
    assert rommlib.file_hash_ok(str(zp), rec) == (None, None)          # several members: can't redo RomM's hash


def test_chdman_with_huge_progress_output_does_not_hang(isolated, tmp_path, monkeypatch):
    """Regression: chdman's stderr was a pipe read only at exit; long
    conversions printed >64 KB of progress and blocked forever."""
    import stat
    import sys
    import rb_chd
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "chdman"
    exe.write_text("#!%s\nimport sys\nfor i in range(20000):\n"
                   "    sys.stderr.write('Compressing, %%d.%%d%%%% complete... (ratio=50.0%%%%)\\r' %% (i // 200, i %% 10))\n"
                   "sys.stderr.write('done\\n')\n" % sys.executable)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", "%s:%s" % (bindir, os.environ["PATH"]))
    monkeypatch.setattr(rb_chd, "running_game", lambda: "")
    monkeypatch.setattr(rb_chd.time, "sleep", lambda s: None)
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    import threading
    res = []
    t = threading.Thread(target=lambda: res.append(rb_chd.chdman("createdvd", "-i", "x", "-o", "y")), daemon=True)
    t.start()
    t.join(30)
    assert res and res[0][0] == 0 and res[0][1].endswith("done")


def test_chd_low_memory_setting(monkeypatch):
    """CDs skip chdman's leaky FLAC codec on boxes under 4 GB (a "4 GB" box
    reports ~3.7-3.9 GiB); the setting overrides either way; DVDs always skip it."""
    import rb_chd
    GiB = 2 ** 30
    assert [rommlib.low_ram(int(g * GiB)) for g in (1, 1.9, 3.4, 3.7, 7.6, 31)] == \
        [True, True, True, False, False, False]
    assert rommlib.low_ram(0) is False                       # unknown: don't guess
    monkeypatch.setattr(rommlib, "total_ram", lambda: 2 * GiB)
    assert rommlib.chd_low_memory({}) is True and rommlib.chd_low_memory({"chd_low_memory": "auto"}) is True
    assert rommlib.chd_low_memory({"chd_low_memory": False}) is False
    assert rb_chd.codecs_for("createcd", {}) == ["-c", "cdlz,cdzl"]
    assert rb_chd.codecs_for("createcd", {"chd_low_memory": False}) == []
    monkeypatch.setattr(rommlib, "total_ram", lambda: int(3.8 * GiB))
    assert rommlib.chd_low_memory({}) is False and rommlib.chd_low_memory({"chd_low_memory": True}) is True
    assert rb_chd.codecs_for("createcd", {}) == []
    assert rb_chd.codecs_for("createcd", {"chd_low_memory": True}) == ["-c", "cdlz,cdzl"]
    assert rb_chd.codecs_for("createdvd", {}) == rb_chd.codecs_for("createdvd", {"chd_low_memory": False}) == \
        ["-c", "lzma,zlib,huff"]
    assert rommlib.total_ram() > 0 or not os.path.exists("/proc/meminfo")


def test_chd_threads_auto_and_setting(monkeypatch):
    assert [rommlib.auto_chd_threads(n) for n in (1, 2, 3, 4, 5, 6, 8, 12, 32)] == [1, 1, 2, 3, 4, 4, 6, 10, 30]
    monkeypatch.setattr(rommlib, "cpu_threads", lambda: 8)
    assert rommlib.chd_threads({"chd_threads": "auto"}) == 6
    assert rommlib.chd_threads({}) == 6
    assert rommlib.chd_threads({"chd_threads": 3}) == 3
    assert rommlib.chd_threads({"chd_threads": 64}) == 8          # never more than the box has
    assert rommlib.chd_threads({"chd_threads": "junk"}) == 6


def test_chd_status_follows_the_worker(isolated):
    """Finished downloads show where their CHD conversion is, not a frozen
    "(CHD later)"."""
    os.makedirs(rommlib.CHD_QUEUE_DIR)
    lib = rommlib.Library()
    task = "1-psx.json"
    open(os.path.join(rommlib.CHD_QUEUE_DIR, task), "w").write("{}")
    assert rommlib.chd_status(task, lib, 7) == "queued"
    json.dump({"task": task}, open(os.path.join(rommlib.DATA_DIR, "chd-current.json"), "w"))
    assert rommlib.chd_status(task, lib, 7) == "compressing"
    os.remove(os.path.join(rommlib.DATA_DIR, "chd-current.json"))
    os.remove(os.path.join(rommlib.CHD_QUEUE_DIR, task))
    chd = os.path.join(rommlib.ROMS_ROOT, "psx", "Game.chd")
    os.makedirs(os.path.dirname(chd))
    open(chd, "w").close()
    lib.mark({"id": 7, "name": "Game"}, "psx", [chd])
    assert rommlib.chd_status(task, lib, 7) == "done"
    assert rommlib.chd_status(None) is None


def test_conversions_state_reads_what_the_worker_publishes(isolated):
    assert rommlib.conversions_state() == {"current": {}, "queue": [], "results": []}    # nothing yet
    q = rommlib.CHD_QUEUE_DIR
    os.makedirs(q)
    json.dump({"sources": ["/r/psx/Lorem.cue"]}, open(os.path.join(q, "2-psx.json"), "w"))
    json.dump({"sources": ["/r/psx/Ipsum.cue"]}, open(os.path.join(q, "1-psx.json"), "w"))
    open(os.path.join(q, "3-psx.json"), "w").write('{"sour')          # half-written
    open(os.path.join(q, ".3-psx.json.1-2.tmp"), "w").write("{}")     # being published: not a task yet
    json.dump({"task": "1-psx.json", "pct": 42}, open(rommlib.chd_current_path(), "w"))
    with open(rommlib.chd_results_path(), "w") as f:
        for i in range(70):
            f.write(json.dumps({"task": "%d-old.json" % i, "result": "done"}) + "\n")
        f.write("\n")
    st = rommlib.conversions_state()
    assert st["current"] == {"task": "1-psx.json", "pct": 42}
    assert st["queue"] == [("1-psx.json", {"sources": ["/r/psx/Ipsum.cue"]}),
                           ("2-psx.json", {"sources": ["/r/psx/Lorem.cue"]}), ("3-psx.json", None)]
    assert [r["task"] for r in st["results"]] == ["%d-old.json" % i for i in range(11, 70)]   # last 60 lines
    open(rommlib.chd_current_path(), "w").write('{"task": "1-p')       # mid-rewrite
    open(rommlib.chd_results_path(), "a").write('{"task": "70-o')
    st = rommlib.conversions_state()
    assert st["current"] == {} and st["results"] == [] and len(st["queue"]) == 3


def test_chd_worker_never_waits_on_finalize_holding_the_lock(isolated, monkeypatch):
    """Regression: after its last disc the worker ran the gamelist merge
    inline, which waits for ES to go idle (forever while the app is open),
    holding chd.lock — so conversions queued meanwhile never started."""
    import fcntl
    import rb_chd
    os.makedirs(rommlib.CHD_QUEUE_DIR)
    monkeypatch.setattr(rb_chd, "running_game", lambda: "")
    done, spawned = [], []

    def fake_convert(task):
        done.append(task["n"])
        if task["n"] == 1:      # a download queues another disc while the worker runs
            json.dump({"n": 2}, open(os.path.join(rommlib.CHD_QUEUE_DIR, "2-psx.json"), "w"))
        return True
    monkeypatch.setattr(rb_chd, "convert", fake_convert)
    monkeypatch.setattr(rb_chd, "finalize_later", lambda: spawned.append(1))
    monkeypatch.setattr(rb_chd.rb_finalize, "main", lambda: pytest.fail("finalize ran inline"))
    json.dump({"n": 1}, open(os.path.join(rommlib.CHD_QUEUE_DIR, "1-psx.json"), "w"))
    rb_chd.main()
    assert done == [1, 2] and spawned and not os.listdir(rommlib.CHD_QUEUE_DIR)
    with open(rommlib.chd_lock_path(), "w") as f:           # and the lock is free again
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_try_lock_gives_up_at_once_when_the_lock_is_held(isolated, tmp_path):
    path = str(tmp_path / "x.lock")
    os.makedirs(rommlib.DATA_DIR)
    with rommlib.try_lock(path) as fd:
        assert fd is not None
        with rommlib.try_lock(path) as again:      # another open file: flock says busy
            assert again is None
        assert rommlib.chd_worker_busy() is False
        with rommlib.try_lock(rommlib.chd_lock_path()):
            assert rommlib.chd_worker_busy() is True
    with rommlib.try_lock(path) as fd:             # released on leaving the block
        assert fd is not None
    with pytest.raises(OSError):
        with rommlib.try_lock(str(tmp_path / "missing" / "x.lock")):
            pass


def test_rom_dest_stays_inside_the_system_folder(isolated, tmp_path):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    assert rommlib.rom_dest({"fs_name": "Game (USA).chd"}, "psx") == os.path.join(sysdir, "Game (USA).chd")
    assert rommlib.rom_dest({"fs_name": "../../etc/passwd"}, "psx") == os.path.join(sysdir, "_.._etc_passwd")
    assert rommlib.rom_dest({"fs_name": ""}, "psx") is None
    assert rommlib.rom_dest({}, "psx") is None
    os.makedirs(sysdir)
    os.symlink(str(tmp_path), os.path.join(sysdir, "Out"))      # a link that leads out of roms/psx
    assert rommlib.rom_dest({"fs_name": "Out"}, "psx") is None


def test_library_snapshot_is_fresh_and_a_copy(isolated):
    lib = rommlib.Library()
    lib.mark({"id": 1, "name": "A"}, "psx", ["/r/psx/A.chd"])
    other = rommlib.Library()                       # another process writes installed.json
    other.mark({"id": 2, "name": "B"}, "psx", ["/r/psx/B.chd"])
    st = os.stat(rommlib.STATE_PATH)
    os.utime(rommlib.STATE_PATH, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))     # within a clock tick of lib's write
    snap = lib.snapshot()
    assert sorted(snap) == ["1", "2"]
    snap.pop("1")
    assert sorted(lib.snapshot()) == ["1", "2"]


# ------------------------------------------------------------ gamelist write safety



# ------------------------------------------------------------ game info via ES's API

def _gl(sysdir, body):
    os.makedirs(sysdir, exist_ok=True)
    with open(os.path.join(sysdir, "gamelist.xml"), "w") as f:
        f.write(body)


def test_metadata_entry_goes_through_es_api(isolated, fake_es):
    es = fake_es({"gba": {".zip", ".gba"}})
    sysdir = os.path.join(rommlib.ROMS_ROOT, "gba")
    os.makedirs(sysdir, exist_ok=True)
    rom = os.path.join(sysdir, "A (World).zip")
    open(rom, "w").close()
    _gl(sysdir, "<gameList><game><path>./Other.zip</path><name>Other</name></game></gameList>")
    gl_before = open(os.path.join(sysdir, "gamelist.xml")).read()
    left = rb_finalize.process([{"system": "gba", "paths": [rom], "name": "A", "desc": "Hello",
                                 "releasedate": 946684800000, "genres": ["Puzzle"], "players": "1",
                                 "rating": 80, "developer": "Dev", "publisher": "Pub", "cover": None}],
                               None, {"gba": {".zip", ".gba"}})
    assert left == []
    g = es.game("gba", "A (World).zip")
    assert g["name"] == "A" and g["releasedate"] == "20000101T000000" and g["rating"] == "0.80"
    assert g["genre"] == "Puzzle" and g["publisher"] == "Pub"
    # we never wrote gamelist.xml ourselves (only ES's reload-save did, from its memory)
    es.save()
    names = {x.findtext("path"): x.findtext("name") for x in
             ET.parse(os.path.join(sysdir, "gamelist.xml")).getroot()}
    assert names["./A (World).zip"] == "A"


def test_fill_entries_never_replace_existing_info(isolated, fake_es):
    es = fake_es({"snes": {".zip"}})
    sysdir = os.path.join(rommlib.ROMS_ROOT, "snes")
    os.makedirs(sysdir, exist_ok=True)
    rom = os.path.join(sysdir, "G.zip")
    open(rom, "w").close()
    _gl(sysdir, "<gameList><game><path>./G.zip</path><name>My Name</name><desc>mine</desc>"
                "<playcount>4</playcount></game></gameList>")
    es.reload()
    rb_finalize.process([{"system": "snes", "paths": [rom], "name": "RomM", "desc": "romm",
                          "developer": "Dev", "fill": True}], None, {"snes": {".zip"}})
    g = es.game("snes", "G.zip")
    assert g["name"] == "My Name" and g["desc"] == "mine" and g["playcount"] == "4" and g["developer"] == "Dev"


def test_chd_rename_carries_info_media_and_play_history(isolated, fake_es):
    """The v0.6.3 loss: a CHD conversion renames the game; ES's own save of
    the new file's bare entry overwrote the info we had moved in the file.
    Now the old entry's info goes into ES itself, so ES's saves keep it."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    os.makedirs(os.path.join(sysdir, "images"))
    open(os.path.join(sysdir, "images", "Game-image.png"), "wb").write(b"\x89PNG fake")
    open(os.path.join(sysdir, "images", "Game-video.mp4"), "wb").write(b"mp4 fake")
    _gl(sysdir, '<gameList>'
                '<game><path>./Game/Game.cue</path><name>Game</name><desc>d</desc><playcount>3</playcount>'
                '<lastplayed>20251110T002042</lastplayed><favorite>true</favorite>'
                '<image>./images/Game-image.png</image><video>./images/Game-video.mp4</video></game>'
                '<game><path>./Game/Game.bin</path><name>Game (bin)</name></game>'
                '<game><path>./Other.chd</path><name>Other</name></game></gameList>')
    open(os.path.join(sysdir, "Other.chd"), "w").close()
    es = fake_es({"psx": {".chd", ".cue", ".m3u"}})
    # the conversion: folder gone, Game.chd appears; ES hasn't seen it yet
    open(os.path.join(sysdir, "Game.chd"), "w").close()
    left = rb_finalize.process([{"system": "psx", "rename": {os.path.join(sysdir, "Game"): os.path.join(sysdir, "Game.chd")},
                                 "hide": []}], None, {"psx": {".chd", ".cue", ".m3u"}})
    assert left == []
    es.save()                     # ES saves on its own — must keep what we gave it
    es.reload()
    g = es.game("psx", "Game.chd")
    assert g["name"] == "Game" and g["desc"] == "d" and g["playcount"] == "3" and g["favorite"] == "true"
    assert g["lastplayed"] == "20251110T002042"
    assert g.get("image", "").endswith("Game-image.png") and g.get("video", "").endswith("Game-video.mp4")
    assert es.game("psx", "Other.chd")["name"] == "Other"


def test_hide_and_remove(isolated, fake_es):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    os.makedirs(os.path.join(sysdir, "images"))
    for n in ("D1.chd", "D2.chd", "Set.m3u"):
        open(os.path.join(sysdir, n), "w").close()
    open(os.path.join(sysdir, "images", "Gone-image.png"), "w").close()
    open(os.path.join(sysdir, "images", "Shared-image.png"), "w").close()
    _gl(sysdir, '<gameList><game><path>./Gone.chd</path><image>./images/Gone-image.png</image></game>'
                '<game><path>./D1.chd</path><image>./images/Shared-image.png</image></game></gameList>')
    es = fake_es({"psx": {".chd", ".m3u"}})
    rb_finalize.process([{"system": "psx", "hide": [os.path.join(sysdir, "D1.chd"), os.path.join(sysdir, "D2.chd")]},
                         {"system": "psx", "remove": [os.path.join(sysdir, "Gone.chd")]}], None,
                        {"psx": {".chd", ".m3u"}})
    assert es.game("psx", "D1.chd")["hidden"] == "true" and es.game("psx", "D2.chd")["hidden"] == "true"
    assert not es.game("psx", "Set.m3u").get("hidden")
    assert not os.path.exists(os.path.join(sysdir, "images", "Gone-image.png"))     # the uninstalled game's art
    assert os.path.exists(os.path.join(sysdir, "images", "Shared-image.png"))


def test_entries_es_cannot_take_yet_are_kept(isolated, fake_es, monkeypatch):
    """A system ES doesn't list (no games yet, or a reload that didn't pick
    the file up): the entry is kept for the next run, not dropped."""
    es = fake_es({"gba": {".zip"}})
    monkeypatch.setattr(rb_finalize, "wait_listed", lambda *a, **k: False)
    sysdir = os.path.join(rommlib.ROMS_ROOT, "nes")
    os.makedirs(sysdir)
    rom = os.path.join(sysdir, "N.zip")
    open(rom, "w").close()
    e = {"system": "nes", "paths": [rom], "name": "N"}
    assert rb_finalize.process([dict(e)], None, {"nes": {".zip"}}) == [e]


def test_corrupt_gamelist_is_never_touched(isolated, fake_es):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "snes")
    _gl(sysdir, "<gameList><game><path>./A.zip</path>")          # truncated
    rom = os.path.join(sysdir, "B.zip")
    open(rom, "w").close()
    es = fake_es({"snes": {".zip"}})
    es.save = lambda: None                                       # (only ES would ever write it)
    rb_finalize.process([{"system": "snes", "paths": [rom], "name": "Bee"}], None, {"snes": {".zip"}})
    assert open(os.path.join(sysdir, "gamelist.xml")).read() == "<gameList><game><path>./A.zip</path>"
    assert es.game("snes", "B.zip")["name"] == "Bee"


def test_repair_restores_lost_info_strictly(isolated, fake_es, capsys):
    """rb_repair: the v0.6.3 loss (bare CHD entry, rich entry only in the
    backup) is restored through ES; other copies and other discs are never
    used; a dry run changes nothing."""
    import rb_repair
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    os.makedirs(os.path.join(sysdir, "images"))
    os.makedirs(os.path.join(sysdir, "51. Aliqua (USA)"))
    open(os.path.join(sysdir, "images", "Aliqua (USA)-image.png"), "wb").write(b"\x89PNG x")
    for n in ("Aliqua (USA).chd", "51. Aliqua (USA)/Aliqua (USA).cue", "Game (Disc 2).chd"):
        open(os.path.join(sysdir, n), "w").close()
    bak = ('<gameList>'
           '<game><path>./Aliqua (USA)/Aliqua (USA).cue</path><name>Aliqua</name><desc>dream</desc>'
           '<playcount>1</playcount><image>./images/Aliqua (USA)-image.png</image></game>'
           '<game><path>./Game (Disc 1).cue</path><name>Game</name><desc>disc one only</desc></game>'
           '</gameList>')
    open(os.path.join(sysdir, "gamelist.xml.bak-rommbrowser"), "w").write(bak)
    _gl(sysdir, '<gameList><game><path>./Aliqua (USA).chd</path><name>Aliqua (USA)</name>'
                '<cheevosHash>38</cheevosHash></game>'
                '<game><path>./51. Aliqua (USA)/Aliqua (USA).cue</path><name>Aliqua</name>'
                '<desc>the TOP copy</desc></game></gameList>')
    es = fake_es({"psx": {".chd", ".cue"}})
    assert rb_repair.main(["--dry-run"]) == 0
    assert es.game("psx", "Aliqua (USA).chd").get("desc") is None          # dry run: untouched
    out = capsys.readouterr().out
    assert "TOTAL: 1 games" in out
    assert rb_repair.main([]) == 0
    g = es.game("psx", "Aliqua (USA).chd")
    assert g["name"] == "Aliqua" and g["desc"] == "dream" and g["playcount"] == "1" and g.get("image")
    assert g["cheevosHash"] == "38"                                        # ES's own data kept
    assert es.game("psx", "Game (Disc 2).chd").get("desc") is None         # disc 1's info is not disc 2's
    assert [d for d in os.listdir(rommlib.DATA_DIR) if d.startswith("repair-backup-")]


def test_chd_plan_never_merges_different_games(isolated):
    """Sweep regression: a folder holding several games' "(Disc N)" folders
    became one multi-disc game (the disc check looked at full paths)."""
    import rb_chd_plan
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    for rel in ("TOP 100/Alpha (Disc 1)/x.cue", "TOP 100/FF7 (Disc 1)/x.cue",
                "Game (USA)/Game (USA) (Disc 1)/Game (USA) (Disc 1).cue",
                "Game (USA)/Game (USA) (Disc 2)/Game (USA) (Disc 2).cue", "Loose (USA).cue"):
        os.makedirs(os.path.dirname(os.path.join(sysdir, rel)), exist_ok=True)
        open(os.path.join(sysdir, rel), "w").close()
    got = {os.path.relpath(r, sysdir): [os.path.relpath(f, sysdir) for f in fs]
           for r, fs, _ in rb_chd_plan.games_for("psx")}
    assert got["TOP 100/Alpha (Disc 1)"] == ["TOP 100/Alpha (Disc 1)/x.cue"]
    assert got["TOP 100/FF7 (Disc 1)"] == ["TOP 100/FF7 (Disc 1)/x.cue"]
    assert got["Game (USA)"] == ["Game (USA)/Game (USA) (Disc 1)/Game (USA) (Disc 1).cue",
                                 "Game (USA)/Game (USA) (Disc 2)/Game (USA) (Disc 2).cue"]
    assert got["Loose (USA).cue"] == ["Loose (USA).cue"] and "TOP 100" not in got


def test_chd_one_folder_two_releases_two_playlists(isolated, monkeypatch, fake_es):
    """Box regression (Dolor Evil 2, 2026-10-10 02:30): RomM's game folder
    held the original and the Dual Shock release; the old planner made one
    4-disc set with one playlist. Now: one set and one playlist per release."""
    import rb_chd_plan, rb_chd
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    rels = ["RE2 (USA)/RE2 (USA) (Disc %d)/RE2 (USA) (Disc %d).cue" % (n, n) for n in (1, 2)] + \
           ["RE2 (USA)/RE2 - Dual Shock Ver. (USA) (Disc %d)/RE2 - Dual Shock Ver. (USA) (Disc %d).cue" % (n, n)
            for n in (1, 2)]
    for rel in rels:
        os.makedirs(os.path.dirname(os.path.join(sysdir, rel)), exist_ok=True)
        open(os.path.join(sysdir, rel), "w").write("x")
    plan = rb_chd_plan.games_for("psx")
    assert sorted((os.path.relpath(r, sysdir), len(fs), n) for r, fs, n in plan) == [
        ("RE2 (USA)", 2, "RE2 (USA)"), ("RE2 (USA)", 2, "RE2 - Dual Shock Ver. (USA)")]
    monkeypatch.setattr(rb_chd, "running_game", lambda: "")
    monkeypatch.setattr(rb_chd, "chdman", lambda mode, *a: (
        mode == "verify" or open(a[a.index("-o") + 1], "w").write("chd")) and (0, ""))
    for r, fs, n in plan:
        assert rb_chd.convert({"system": "psx", "mode": "createcd", "sources": fs,
                               "roots": [r], "name": n}) is True
    assert open(os.path.join(sysdir, "RE2 (USA).m3u")).read().split("\n")[:2] == [
        "RE2 (USA) (Disc 1).chd", "RE2 (USA) (Disc 2).chd"]
    assert open(os.path.join(sysdir, "RE2 - Dual Shock Ver. (USA).m3u")).read().split("\n")[:2] == [
        "RE2 - Dual Shock Ver. (USA) (Disc 1).chd", "RE2 - Dual Shock Ver. (USA) (Disc 2).chd"]
    assert not os.path.exists(os.path.join(sysdir, "RE2 (USA)"))       # folder gone once both are done


def test_chd_refuses_two_sources_with_one_output_name(isolated, monkeypatch):
    import rb_chd
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    for d in ("G/CD1", "G/CD2"):
        os.makedirs(os.path.join(sysdir, d))
        open(os.path.join(sysdir, d, "game.cue"), "w").write("x")
    monkeypatch.setattr(rb_chd, "running_game", lambda: "")
    monkeypatch.setattr(rb_chd, "chdman", lambda *a: pytest.fail("must not convert"))
    srcs = [os.path.join(sysdir, "G/CD1/game.cue"), os.path.join(sysdir, "G/CD2/game.cue")]
    assert rb_chd.convert({"system": "psx", "mode": "createcd", "sources": srcs,
                           "roots": [os.path.join(sysdir, "G")]}) is False
    assert all(os.path.exists(s) for s in srcs)


# ------------------------------------------------------------ bug-sweep regressions (2026-10-10)

def test_sweep_hidden_never_carried_onto_a_playlist(isolated, fake_es):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    os.makedirs(os.path.join(sysdir, "G (Disc 1)"))
    open(os.path.join(sysdir, "G.m3u"), "w").write("G (Disc 1).chd\n")
    open(os.path.join(sysdir, "G (Disc 1).chd"), "w").close()
    _gl(sysdir, "<gameList><game><path>./G (Disc 1)/G (Disc 1).cue</path><name>G</name><desc>d</desc>"
                "<hidden>true</hidden></game></gameList>")
    es = fake_es({"psx": {".chd", ".m3u", ".cue"}})
    rb_finalize.process([{"system": "psx", "rename": {os.path.join(sysdir, "G (Disc 1)"): os.path.join(sysdir, "G.m3u")},
                          "hide": []}], None, {"psx": {".chd", ".m3u", ".cue"}})
    assert es.game("psx", "G.m3u").get("hidden") != "true" and es.game("psx", "G.m3u")["desc"] == "d"


def test_sweep_torn_or_bad_queue_lines_are_set_aside(isolated):
    os.makedirs(rommlib.DATA_DIR)
    p = os.path.join(rommlib.DATA_DIR, "q.jsonl")
    with open(p, "w") as f:
        f.write(json.dumps({"system": "gba", "paths": ["/x"]}) + "\n")
        f.write('{"system": "gba", "pat')                       # torn by a power cut
        f.write(json.dumps({"no": "system"}) + "\n")            # glued + not an entry
        f.write(json.dumps([1, 2]) + "\n")
        f.write(json.dumps({"system": "snes", "remove": ["/y"]}) + "\n")
    got = rb_finalize._read_jsonl(p)
    assert [e["system"] for e in got] == ["gba", "snes"]
    assert len(open(p + ".bad").read().splitlines()) == 2


def test_sweep_reinstalled_game_keeps_its_cover(isolated, fake_es):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "gba")
    os.makedirs(os.path.join(sysdir, "images"))
    rom = os.path.join(sysdir, "A.zip")
    open(rom, "w").close()                                       # back again after the uninstall
    open(os.path.join(sysdir, "images", "A-image.png"), "w").close()
    _gl(sysdir, "<gameList><game><path>./A.zip</path><image>./images/A-image.png</image></game></gameList>")
    fake_es({"gba": {".zip"}})
    rb_finalize.process([{"system": "gba", "remove": [rom]}], None, {"gba": {".zip"}})
    assert os.path.exists(os.path.join(sysdir, "images", "A-image.png"))


def test_sweep_numbers_compare_by_value():
    assert rb_finalize.EsApi.same("0.80", "0.800000") and rb_finalize.EsApi.same("7", "7.0")
    assert not rb_finalize.EsApi.same("0.8", "0.7") and rb_finalize.EsApi.same(" a ", "a")


def test_sweep_unsupported_zip_method_is_not_called_damaged(tmp_path):
    zp = tmp_path / "g.zip"
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("g.gba", b"x" * 1000)
    raw = bytearray(zp.read_bytes())
    for sig in (b"PK\x03\x04", b"PK\x01\x02"):                  # say Deflate64 (method 9) in both headers
        i = raw.find(sig)
        off = i + (8 if sig == b"PK\x03\x04" else 10)
        raw[off:off + 2] = (9).to_bytes(2, "little")
    zp.write_bytes(bytes(raw))
    rec = {"file_name": "g.zip", "sha1_hash": "0" * 40,
           "archive_members": [{"name": "g.gba", "size": 1000, "crc_hash": "%08x" % zlib.crc32(b"x" * 1000)}]}
    assert rommlib.file_hash_ok(str(zp), rec) == (None, None)
    assert rommlib.file_hash_ok(str(zp), dict(rec, archive_members=[])) == (None, None)


def test_sweep_resume_all_keeps_jobs_paused_by_hand(isolated):
    cfg = dict(rommlib.DEFAULT_CONFIG, url="http://127.0.0.1:9", token="x")
    d = rommlib.Downloader(rommlib.Api.from_config(cfg), rommlib.Library(), cfg)
    d.hold_all()
    roms = [{"id": 9100 + i, "name": "C%d" % i, "fs_name": "C%d.zip" % i, "fs_name_no_ext": "C%d" % i,
             "fs_size_bytes": 1} for i in range(2)]
    a, b = d.enqueue(roms[0], "gba"), d.enqueue(roms[1], "gba")
    d.pause_job(a)
    d.release_all()
    assert a.state == "Paused" and b.state != "Paused"


def test_sweep_ui_sort_infodialog_clamp_and_status(isolated, rb, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    app = rb.App(None, headless=True)
    # Sort row exists now (was an AttributeError)
    gl = rb.GameListScreen(app, "Recently added", order_by="id_desc")
    app.push(gl)
    gl._toggle_sort()
    assert gl.order_by == "name"
    # arrows under the Fill-in-info dialog (was KeyError in nav_state)
    dlg = rb.InfoDialog.__new__(rb.InfoDialog)
    rb.Confirm.__init__(dlg, app, "t", "b", "OK", lambda: None)
    dlg.pending, dlg.plan = False, [{"system": "gba", "paths": ["/x"], "name": "n", "desc": "d", "fill": True}]
    app.modal = dlg
    app.dispatch("down")
    app.modal = None
    # On this box: deleting the last row keeps a valid selection
    inst = rb.InstalledScreen(app)
    inst.items, inst.sel = [{"id": 1}, {"id": 2}, {"id": 3}], 2
    inst._loaded([{"id": 1}, {"id": 2}])
    assert inst.sel == 1
    # a finished download whose files are gone isn't "installed"
    rom = {"id": 77, "name": "Gone", "fs_name": "Gone.zip", "fs_name_no_ext": "Gone", "platform_id": 1}
    j = rommlib.Job(rom, "gba")
    j.state = rommlib.Job.DONE
    app.downloader.jobs.append(j)
    app.platform_by_id = {1: {"_system": "gba"}}
    assert app.rom_status(rom)[0] == "remote"


def test_cover_cache_lru_prune(isolated, monkeypatch):
    """Over the limit: least recently used covers go first, down to 90%;
    using a cached cover makes it recent (mtime, since /userdata is noatime)."""
    import time as _t
    root = rommlib.COVER_CACHE
    os.makedirs(root)
    now = _t.time()
    for i in range(10):                                   # 10 x 100 KB, oldest = 0
        p = os.path.join(root, "%d.img" % i)
        open(p, "wb").write(b"x" * 100_000)
        os.utime(p, (now - 10000 + i, now - 10000 + i))
    open(os.path.join(root, "9.img.tmp"), "w").close()
    os.utime(os.path.join(root, "9.img.tmp"), (now - 7200, now - 7200))
    rommlib.touch_cached(os.path.join(root, "0.img"))     # cover 0 just used
    assert rommlib.prune_cache(2_000_000) == (0, 0)       # under the limit: nothing
    n, freed = rommlib.prune_cache(600_000)               # -> at most 540 KB
    left = sorted(os.listdir(root))
    assert n == 5 and freed == 500_000
    assert "0.img" in left and "1.img" not in left and "9.img" in left
    assert "9.img.tmp" not in left                         # stale partial gone too
    assert rommlib.cache_usage()[0] <= 540_000
    assert rommlib.cache_limit_bytes({"cache_limit_mb": 0}) == 0
    assert rommlib.cache_limit_bytes({}) == 1024 << 20


def test_cover_fetch_prunes_after_enough_new_covers(isolated, mock_url, monkeypatch):
    api = rommlib.Api(mock_url, "demo")
    api.cache_limit = 1
    monkeypatch.setattr(rommlib, "PRUNE_EVERY", 1)
    calls = []
    monkeypatch.setattr(rommlib, "prune_cache", lambda lim: calls.append(lim))
    rom = api.roms(limit=1)["items"][0]
    assert api.fetch_cover(rom) and calls == [1]


def test_cover_load_retries_when_cache_file_vanishes(isolated, rb, monkeypatch):
    """A cover trimmed from the cache between being found and read is fetched
    again instead of showing "no cover" for the rest of the session."""
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    app = rb.App(None, headless=True)
    good = os.path.join(rommlib.DATA_DIR, "good.png")
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    pygame.image.save(pygame.Surface((4, 6)), good)
    calls = []

    def fetch_cover(rom, large=False):
        calls.append(1)
        return os.path.join(rommlib.DATA_DIR, "gone.png") if len(calls) == 1 else good
    app.api.fetch_cover = fetch_cover
    app.cover_loader.WORKERS = 0                    # run it here, not on a worker thread
    app.cover({"id": 5})
    assert app.cover_loader.run_one(wait=False)
    for fn in app.events_from_threads:
        fn()
    assert len(calls) == 2 and app.covers[5].get_size() == rb.cover_fit(pygame.Surface((4, 6))).get_size()


def _fake_chdman_bin(tmp_path, monkeypatch, body):
    import stat
    import sys as _s
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "chdman"
    exe.write_text("#!%s\nimport sys, time, os\n%s\n" % (_s.executable, body))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", "%s:%s" % (bindir, os.environ["PATH"]))


def test_dvd_conversions_leave_out_flac(isolated, tmp_path, monkeypatch):
    """chdman 0.285's FLAC codec leaks ~4 MB per MB on createdvd (an 8 GB DVD
    took the box down): DVDs are compressed with lzma,zlib,huff."""
    import rb_chd
    log = tmp_path / "args.txt"
    _fake_chdman_bin(tmp_path, monkeypatch,
                     "open(%r, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
                     "a = sys.argv[1:]\nif a[0] != 'verify': open(a[a.index('-o') + 1], 'w').write('chd')" % str(log))
    monkeypatch.setattr(rb_chd, "running_game", lambda: "")
    monkeypatch.setattr(rb_chd.time, "sleep", lambda s: None)
    sysdir = os.path.join(rommlib.ROMS_ROOT, "ps2")
    os.makedirs(sysdir)
    iso = os.path.join(sysdir, "G (USA).iso")
    open(iso, "w").write("x")
    assert rb_chd.convert({"system": "ps2", "mode": "createdvd", "sources": [iso], "roots": [iso]})
    first = open(log).read().splitlines()[0]
    assert first.startswith("createdvd") and "-c lzma,zlib,huff" in first and "flac" not in first


def test_memory_guard_stops_chdman_and_keeps_originals(isolated, tmp_path, monkeypatch):
    import rb_chd
    _fake_chdman_bin(tmp_path, monkeypatch,
                     "a = sys.argv[1:]\nblob = bytearray(64 << 20)\n"     # holds 64 MB
                     "for i in range(0, len(blob), 4096): blob[i] = 1\n"
                     "time.sleep(30)")
    monkeypatch.setattr(rb_chd, "running_game", lambda: "")
    monkeypatch.setattr(rb_chd, "MEM_SHARE_MAX", (32 << 20) / max(1, rb_chd._meminfo().get("MemTotal", 1)))
    real_sleep = time.sleep
    monkeypatch.setattr(rb_chd.time, "sleep", lambda s: real_sleep(0.3))
    sysdir = os.path.join(rommlib.ROMS_ROOT, "ps2")
    os.makedirs(sysdir)
    iso = os.path.join(sysdir, "G (USA).iso")
    open(iso, "w").write("x")
    t0 = time.time()
    res = rb_chd.convert({"system": "ps2", "mode": "createdvd", "sources": [iso], "roots": [iso]})
    assert res is False and time.time() - t0 < 20               # stopped, not waited out
    assert sorted(os.listdir(sysdir)) == ["G (USA).iso"]          # original kept, no partial
    assert "STOPPED" in open(rommlib.LOG_PATH).read()
    # memory ran low while a game was up: kept for later (None), not given up
    monkeypatch.setattr(rb_chd, "memory_check", lambda pid, busy: rb_chd.MemoryGuard("low", True))
    assert rb_chd.convert({"system": "ps2", "mode": "createdvd", "sources": [iso], "roots": [iso]}) is None



def test_rom_is_folder_also_with_files_only_in_subfolders():
    """RomM counts only files directly in a game's folder, so a set kept in
    per-disc subfolders reports has_multiple_files False (5.3 and 5.4 alike):
    it's still a folder to download file by file."""
    single = {"full_path": "roms/gba/Game (USA).zip", "has_multiple_files": False, "has_nested_single_file": False,
              "files": [{"full_path": "roms/gba/Game (USA).zip"}]}
    flat = {"full_path": "roms/psx/Set", "has_multiple_files": True, "files": []}
    nested = {"full_path": "roms/psx/Set (USA)", "has_multiple_files": False, "has_nested_single_file": False,
              "files": [{"full_path": "roms/psx/Set (USA)/Set (USA) (Disc 1)/Set (USA) (Disc 1).cue"},
                        {"full_path": "roms/psx/Set (USA)/Set (USA) (Disc 1)/Set (USA) (Disc 1).bin"}]}
    assert not rommlib.rom_is_folder(single)
    assert rommlib.rom_is_folder(flat) and rommlib.rom_is_folder(nested)
    assert not rommlib.rom_is_folder({"full_path": "roms/x/y.zip", "files": []})


def test_last_check_kept_until_its_files_change(isolated):
    """A finished check is kept for On this box; a finding whose file was
    replaced (size/mtime differ) or deleted since drops out."""
    a, b = isolated / "roms" / "a.chd", isolated / "roms" / "b.chd"
    a.write_bytes(b"x" * 10)
    b.write_bytes(b"y" * 10)
    finds = [{"system": "psx", "path": str(a), "severity": "damaged", "problem": "cut short", "rom": None},
             {"system": "psx", "path": str(b), "severity": "differs", "problem": "smaller", "rom": None}]
    assert rommlib.load_audit() is None
    rommlib.save_audit(finds, {"checked": 2, "matched": 2}, deep=True)
    d = rommlib.load_audit()
    assert d["deep"] and d["stats"]["checked"] == 2 and [f["path"] for f in d["findings"]] == [str(a), str(b)]
    a.write_bytes(b"fixed copy, longer")
    b.unlink()
    assert rommlib.load_audit()["findings"] == []


def test_on_this_box_sections(isolated, rb, monkeypatch):
    """The hub: each section's line reflects its state (coloured only when it
    wants attention), A opens the right screen, and the two-tile screens
    move between tiles and rows."""
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    app = rb.App(None, headless=True)
    hub = rb.OnThisBoxScreen(app)
    app.push(hub)
    hub.games = [{"id": 1, "name": "A", "system": "psx", "when": "", "size": 2 << 30}]
    assert hub.verdict("games") == ("1 game, 2.0 GB", None)
    hub.bios = []
    assert hub.verdict("bios") == ("No BIOS needed", None)
    hub.bios = [{"name": "Saturn", "offers": [({}, {})], "missing": [{}], "required": [{}], "fetched": {}, "here": True},
                {"name": "3DO", "offers": [({}, {})], "missing": [{}], "required": [{}], "fetched": {}, "here": False}]
    assert hub.verdict("bios") == ("Saturn is missing 1 file RomM has", rb.ACCENT_HI)
    hub.audit = None
    assert hub.verdict("check")[0] == "Not checked yet"
    hub.audit = {"time": 0, "findings": [{"severity": "damaged"}, {"severity": "differs"}]}
    line, col = hub.verdict("check")
    assert line.startswith("1 damaged, 1 differ from RomM") and col == rb.RED
    hub.convs = [{"kind": "running"}, {"kind": "queued"}]
    assert hub.verdict("conv") == ("Converting, 1 waiting", rb.BLUE)
    hub.convs = [{"kind": "failed"}]
    assert hub.verdict("conv")[1] == rb.RED
    for i, cls in enumerate([rb.InstalledScreen, rb.SpaceScreen, rb.CompressScreen, rb.BiosScreen, rb.CheckScreen,
                             rb.ConversionsScreen]):
        hub.sel = i
        hub.action("ok")
        assert isinstance(app.stack[-1], cls)
        assert app.stack[-1].crumbs()[0] == "On this box"
        app.stack.pop()
    hub.sel = 0
    hub.action("up")
    assert hub.sel == len(hub.ITEMS) - 1           # wraps, like Home
    # Space: tiles side by side, then the systems
    sp = rb.SpaceScreen(app)
    sp.rows = [{"system": s, "ours": 0, "other": 1, "media": 0, "total": 1} for s in ("psx", "gba")]
    sp.action("right")
    assert sp.sel == 1
    sp.action("down")
    assert sp.sel == 2
    sp.action("down")
    sp.action("down")
    assert sp.sel == 3                             # last system: stays
    sp.action("up")
    sp.action("up")
    assert sp.sel == 0
    # Check games: kept findings below the two tiles
    ck = rb.CheckScreen(app)
    app.push(ck)
    assert ck.findings == [] and ck.hints()[0] == ("A", "Start")
    ck.action("ok")
    assert isinstance(app.stack[-1], rb.AuditScreen) and not app.stack[-1].deep
    app.stack[-1].cancel.set()


def test_kept_check_survives_odd_files_and_missing_tracks(isolated):
    """A "Missing file" finding (its path never existed) stays until the file
    turns up; a hand-edited or foreign audit-last.json never crashes."""
    gone = str(isolated / "roms" / "Game (Track 2).bin")
    rommlib.save_audit([{"system": "psx", "path": gone, "severity": "damaged",
                         "problem": "Missing file: Game (Track 2).bin", "rom": None}], {"checked": 1}, False)
    assert [f["path"] for f in rommlib.load_audit()["findings"]] == [gone]
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    for junk in ("[]", '{"findings": [1, {"path": "/x"}], "stats": 5}', "nope"):
        open(rommlib.audit_path(), "w").write(junk)
        d = rommlib.load_audit()
        assert d is None or (d["findings"] == [] and d["stats"] == {})


def test_check_needs_romm_and_empty_lists_stay_put(isolated, rb, monkeypatch):
    """Offline, Quick check refuses instead of overwriting the last result with
    "all good"; Down on the tiles with nothing below doesn't jump sideways."""
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    app = rb.App(None, headless=True)
    app.platforms = []
    monkeypatch.setattr(app, "load_platforms", lambda: None)
    ck = rb.CheckScreen(app)
    app.push(ck)
    ck.action("down")
    assert ck.sel == 0
    ck.action("ok")
    assert app.stack[-1] is ck                      # no check started
    sp = rb.SpaceScreen(app)
    sp.rows = []
    sp.action("down")
    assert sp.sel == 0


def test_one_converter_per_file_and_the_planner_knows_every_system():
    """rommlib.CONVERTERS is the one answer to "what does this file become":
    never two conversions for the same file on the same system, and the
    planner's default order covers every system a converter applies to."""
    import rb_chd_plan
    seen = {}
    for c in rommlib.CONVERTERS:
        for system in c.systems:
            for ext in c.sources:
                assert (system, ext) not in seen, (system, ext, seen.get((system, ext)), c.mode)
                seen[(system, ext)] = c.mode
    assert sorted(rb_chd_plan.ORDER) == sorted(set().union(*(c.systems for c in rommlib.CONVERTERS)))   # each once
    for c in rommlib.CONVERTERS:
        assert set(c.priority) <= c.systems, (c.mode, c.priority)      # ranks only its own systems
    # the table's priorities give the planner's order (the planner reads them once
    # the previous release has planner_order; until then they must agree)
    assert rommlib.planner_order() == rb_chd_plan.ORDER
    assert rommlib.conversion_for("ps2", "/r/G.cue", {"chd": True}).mode == "createcd"
    assert rommlib.conversion_for("ps2", ".iso", {"chd": True}).mode == "createdvd"
    assert rommlib.conversion_for("psx", ".iso", {"chd": True}) is None
    assert rommlib.conversion_for("psx", ".cue", {"chd": False}) is None
    assert rommlib.conversion_for("psx", ".cue", {}, accepts=lambda s, e: e != ".chd") is None
    assert rommlib.conversion_output("squashfs", "X", "/r/X.ISO") == "X.iso.squashfs"
    assert rommlib.conversion_output("z3ds", "X", "/r/X.cxi") == "X.zcxi"


def test_one_m3u_reading_and_one_sheet_parser(tmp_path):
    """Every playlist reader skips blank and # lines and keeps odd bytes in
    names (surrogateescape); a sheet parses the same for both contracts."""
    m3u = tmp_path / "Saga.m3u"
    m3u.write_bytes(b"#EXTM3U\n\nSaga (Disc 1).chd\n  Saga \xe9 (Disc 2).chd  \n")
    odd = os.fsdecode(b"Saga \xe9 (Disc 2).chd")
    assert rommlib.m3u_lines(str(m3u)) == ["Saga (Disc 1).chd", odd]
    assert rommlib.m3u_paths(str(m3u)) == [str(tmp_path / "Saga (Disc 1).chd"), str(tmp_path / odd)]
    cue = tmp_path / "G.cue"
    cue.write_text('FILE "G (Track 1).bin" BINARY\nFILE g2.bin BINARY\n')
    assert rb_chd.sources_of(str(cue)) == [str(cue)] + rommlib.sheet_sources(str(cue)) == \
        [str(cue), str(tmp_path / "G (Track 1).bin"), str(tmp_path / "g2.bin")]
    assert rommlib.sheet_sources(str(tmp_path / "gone.cue")) == []
    with pytest.raises(OSError):
        rb_chd.sources_of(str(tmp_path / "gone.cue"))


def test_conversions_waiting_counts_the_queue_by_name(isolated):
    assert rommlib.conversions_waiting() == 0
    os.makedirs(rommlib.CHD_QUEUE_DIR, exist_ok=True)
    for n in ("1-a.json", "2-b.json", ".3-c.json.1-2.tmp"):
        open(os.path.join(rommlib.CHD_QUEUE_DIR, n), "w").write("{")      # half-written still counts
    assert rommlib.conversions_waiting() == 2
