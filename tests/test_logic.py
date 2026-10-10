"""Pure-logic tests: no network, no RomM."""
import json
import os
import stat
import xml.etree.ElementTree as ET
import zipfile

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
    assert rb.letter_of("~Hack~ Kirby") == "H"
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
    kinds = rb.collection_kinds(["Zelda: Best Of", "Theme: X", "Top 25: Y", "Series: S",
                                 "Favs: a", "Favs: b", "Favs: c", "Plain"])
    assert kinds == {"theme", "top 25", "series", "favs"}
    assert rb.collection_kinds(["Zelda: Best Of", "Road trip: cars"]) == set()


def test_rank_search_prefers_real_titles(rb):
    items = [{"name": "~Hack~ Zelda Thing", "regions": ["World"]},
             {"name": "BS ゼルダの伝説", "regions": ["Japan"]},
             {"name": "The Legend of Zelda", "regions": ["USA"]}]
    assert rb.rank_search("zelda", items)[0]["name"] == "The Legend of Zelda"


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


def test_migrate_saves_copies_never_overwrites(tmp_path, monkeypatch):
    saves = tmp_path / "saves" / "psx"
    saves.mkdir(parents=True)
    (saves / "Game (Disc 1).srm").write_text("old")
    (saves / "Game (Disc 1).state.auto").write_text("st")
    (saves / "Game.srm").write_text("keep-me")
    monkeypatch.setattr(rb_chd, "SAVES_ROOT", str(tmp_path / "saves"))
    n = rb_chd.migrate_saves("psx", "/r/psx/Game (Disc 1).cue", "/r/psx/Game.m3u")
    assert n == 1                                  # .state.auto copied, Game.srm untouched
    assert (saves / "Game.srm").read_text() == "keep-me"
    assert (saves / "Game.state.auto").read_text() == "st"
    assert (saves / "Game (Disc 1).srm").exists()  # originals stay


def test_fix_m3us(tmp_path):
    (tmp_path / "G.m3u").write_text("G (Disc 1).cue\nG (Disc 2).cue\n")
    old = {os.path.normpath(str(tmp_path / "G (Disc 1).cue")): str(tmp_path / "G (Disc 1).chd"),
           os.path.normpath(str(tmp_path / "G (Disc 2).cue")): str(tmp_path / "G (Disc 2).chd")}
    assert rb_chd.fix_m3us(str(tmp_path), old) == [str(tmp_path / "G.m3u")]
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
    out = d._postprocess_file(rommlib.Job({"id": 2}, "psx"), disc)
    assert out == [os.path.join(roms, "psx", "Disc (USA)")]
    assert not os.path.exists(disc) and d._chd_queued
    task = json.load(open(os.path.join(rommlib.CHD_QUEUE_DIR, os.listdir(rommlib.CHD_QUEUE_DIR)[0])))
    assert task["mode"] == "createcd" and task["sources"][0].endswith("Disc (USA).cue")


# ------------------------------------------------------------ gamelist merge

def test_merge_renames_and_collapses_duplicates(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    os.makedirs(sysdir)
    open(os.path.join(sysdir, "Game.chd"), "w").close()
    with open(os.path.join(sysdir, "gamelist.xml"), "w") as f:
        f.write('<?xml version="1.0"?><gameList>'
                '<game><path>./Game/Game.cue</path><name>Game</name><desc>d</desc><playcount>3</playcount></game>'
                '<game><path>./Game/Game.bin</path><name>Game (bin)</name></game>'
                '<game><path>./Other.cue</path><name>Other</name></game></gameList>')
    rb_finalize.merge([{"system": "psx", "rename": {os.path.join(sysdir, "Game"): os.path.join(sysdir, "Game.chd")},
                        "hide": []}], None, {})
    games = ET.parse(os.path.join(sysdir, "gamelist.xml")).getroot().findall("game")
    paths = [g.findtext("path") for g in games]
    assert paths.count("./Game.chd") == 1 and "./Other.cue" in paths and len(games) == 2
    kept = next(g for g in games if g.findtext("path") == "./Game.chd")
    assert kept.findtext("desc") == "d" and kept.findtext("playcount") == "3"


def test_merge_adds_metadata_entry(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "gba")
    os.makedirs(sysdir)
    rom = os.path.join(sysdir, "A (World).zip")
    open(rom, "w").close()
    rb_finalize.merge([{"system": "gba", "paths": [rom], "name": "A", "desc": "Hello",
                        "releasedate": 946684800000, "genres": ["Puzzle"], "players": "1",
                        "rating": 80, "developer": "Dev", "publisher": "Pub", "cover": None}],
                      None, {"gba": {".zip", ".gba"}})
    g = ET.parse(os.path.join(sysdir, "gamelist.xml")).getroot().find("game")
    assert g.findtext("path") == "./A (World).zip" and g.findtext("name") == "A"
    assert g.findtext("releasedate") == "20000101T000000" and g.findtext("rating") == "0.80"


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


def test_merge_removes_entries(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "snes")
    os.makedirs(sysdir)
    with open(os.path.join(sysdir, "gamelist.xml"), "w") as f:
        f.write('<gameList><game><path>./A.zip</path></game><game><path>./B.zip</path></game></gameList>')
    rb_finalize.merge([{"system": "snes", "remove": [os.path.join(sysdir, "A.zip")]}], None, {})
    paths = [g.findtext("path") for g in ET.parse(os.path.join(sysdir, "gamelist.xml")).getroot()]
    assert paths == ["./B.zip"]


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
