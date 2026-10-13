"""The read-only data the RomM-side screens use: your statuses and lists,
notes, RetroAchievements progress, screenshots, What's new, Compare with
RomM, backlog fit and plays here. Against tools/mock_romm.py and the fake ES."""
import calendar
import json
import os
import threading
import time

import pytest

import rommlib


def _touch(path, size=1):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * size)
    return path


# ------------------------------------------------------------ your status

def test_user_status_reads_rom_user_and_the_stand_in():
    blank = {"rom_user": {"id": -1, "user_id": -1, "status": None, "now_playing": False, "backlogged": False,
                          "hidden": False, "rating": 0, "difficulty": 0, "completion": 0, "last_played": None}}
    st = rommlib.user_status(blank)
    assert st == {"status": None, "label": "", "now_playing": False, "backlogged": False, "hidden": False,
                  "rating": 0, "difficulty": 0, "completion": 0, "last_played": None, "has_notes": False}
    assert rommlib.user_status({}) == st and rommlib.user_status({"rom_user": "junk"}) == st
    rom = {"has_notes": True, "rom_user": {"id": 5, "user_id": 1, "status": "completed_100", "now_playing": True,
                                           "rating": 9, "completion": 100,
                                           "last_played": "2026-10-01T12:30:00.123456+00:00"}}
    st = rommlib.user_status(rom)
    assert st["status"] == "completed_100" and st["label"] == "Completed 100%" and st["now_playing"]
    assert st["rating"] == 9 and st["completion"] == 100 and st["has_notes"]
    assert st["last_played"] == calendar.timegm((2026, 10, 1, 12, 30, 0, 0, 0, 0))
    assert rommlib.user_status({"rom_user": {"status": "made_up", "rating": "9"}})["status"] is None
    assert rommlib.utc_seconds("never") is None


def test_on_user_list_matches_romm_statuses_filter():
    def rom(**ru):
        return {"rom_user": ru}
    assert rommlib.on_user_list(rom(now_playing=True), "playing")
    assert rommlib.on_user_list(rom(backlogged=True), "backlog")
    assert rommlib.on_user_list(rom(status="completed_100"), "finished")
    assert rommlib.on_user_list(rom(status="finished"), "finished")
    assert not rommlib.on_user_list(rom(status="retired"), "finished")
    assert not rommlib.on_user_list(rom(backlogged=True, hidden=True), "backlog")
    assert set(rommlib.USER_LISTS) >= {"playing", "backlog", "finished"}


def test_notes_ra_progress_and_screenshot_paths():
    detail = {"rom_user": {"user_id": 1}, "all_user_notes": [
        {"user_id": 1, "title": "Old", "content": "a", "updated_at": "2026-01-01T00:00:00Z", "is_public": False},
        {"user_id": 2, "title": "Theirs", "content": "b", "updated_at": "2026-05-01T00:00:00Z"},
        {"user_id": 1, "title": "New", "content": "**c**", "updated_at": "2026-06-01T00:00:00Z", "is_public": True}]}
    notes = rommlib.my_notes(detail)
    assert [n["title"] for n in notes] == ["New", "Old"] and notes[0]["content"] == "**c**" and notes[0]["public"]
    assert [n["title"] for n in rommlib.my_notes(detail, user_id=2)] == ["Theirs"]
    assert rommlib.my_notes({"rom_user": {"user_id": -1}, "all_user_notes": detail["all_user_notes"]}) == []

    me = {"ra_progression": {"total": 1, "results": [
        {"rom_ra_id": 77, "max_possible": 40, "num_awarded": 12, "num_awarded_hardcore": 3,
         "most_recent_awarded_date": "2026-10-11T22:00:00Z", "highest_award_kind": "beaten-softcore",
         "earned_achievements": []}]}}
    got = rommlib.ra_progress(me, {"ra_id": 77})
    assert got == {"earned": 12, "earned_hardcore": 3, "total": 40, "last": rommlib.utc_seconds("2026-10-11T22:00:00Z"),
                   "award": "beaten-softcore"}
    assert rommlib.ra_progress(me, {"ra_id": 78}) is None and rommlib.ra_progress({}, {"ra_id": 77}) is None

    rom = {"merged_screenshots": ["/assets/romm/resources/roms/1/2/screenshots/0.png", "//evil/x.png",
                                  "https://elsewhere/x.png", "/assets/../../etc/passwd", None,
                                  "/assets/romm/resources/roms/1/2/screenshots/0.png"]}
    assert rommlib.screenshot_paths(rom) == ["/assets/romm/resources/roms/1/2/screenshots/0.png"]


# ------------------------------------------------------------ lists from RomM

def test_user_lists_are_filtered_and_ordered_by_romm(isolated, fast_mock_url):
    api = rommlib.Api(fast_mock_url, "demo")
    playing = api.user_list("playing")
    names = [r["name"] for r in playing["items"]]
    assert names == ["Anguna", "Lorem Odyssey", "Tobu Tobu Girl"]          # last played first
    assert playing["total"] == 3 and not playing["filtered"]
    back = api.user_list("backlog", offset=0, limit=8)
    assert back["total"] == 20 and len(back["items"]) == 8
    rest = api.user_list("backlog", offset=8, limit=50)
    assert len(rest["items"]) == 12
    assert all(rommlib.user_status(r)["backlogged"] for r in back["items"] + rest["items"])
    fin = api.user_list("finished", limit=100)
    assert {rommlib.user_status(r)["status"] for r in fin["items"]} == {"finished", "completed_100"}
    gba = api.user_list("playing", platform_ids=[3])
    assert [r["name"] for r in gba["items"]] == ["Anguna"]


def test_a_romm_that_ignores_the_status_filter_gives_no_false_list(isolated):
    api = rommlib.Api("http://romm.invalid", "demo")
    everything = [{"id": 1, "rom_user": {"now_playing": True}}, {"id": 2, "rom_user": {}}, {"id": 3}]
    seen = {}

    def roms(**kw):
        seen.update(kw)
        return {"items": everything, "total": 5000}
    api.roms = roms
    got = api.user_list("playing")
    assert [r["id"] for r in got["items"]] == [1] and got["filtered"]
    assert seen["statuses"] == ("now_playing",) and seen["order_by"] == "last_played" and seen["order_dir"] == "desc"


def test_me_is_kept_a_while_and_gives_ra_progress(isolated, fast_mock_url, monkeypatch):
    api = rommlib.Api(fast_mock_url, "demo")
    calls = []
    real = api.get
    monkeypatch.setattr(api, "get", lambda path, *a, **k: (calls.append(path), real(path, *a, **k))[1])
    me = api.me()
    assert me["username"] == "demo" and api.me() is me and calls == ["/api/users/me"]
    rom = api.user_list("playing")["items"][1]                    # Lorem Odyssey: a RetroAchievements game
    prog = rommlib.ra_progress(me, rom)
    assert prog["earned"] == 2 and prog["total"] == 3 and prog["earned_hardcore"] == 1
    detail = api.rom(rom["id"])
    assert [n["title"] for n in rommlib.my_notes(detail, me["id"])] == ["Where I am"]


def test_me_from_a_romm_without_the_route_is_empty(isolated):
    api = rommlib.Api("http://romm.invalid", "demo")

    def get(path, *a, **k):
        raise rommlib.RommError("RomM error HTTP 404 on " + path)
    api.get = get
    assert api.me() == {}


def test_screenshots_go_through_the_cover_cache(isolated, fast_mock_url, monkeypatch):
    api = rommlib.Api(fast_mock_url, "demo")
    rom = api.user_list("playing")["items"][0]
    assert len(rommlib.screenshot_paths(rom)) == 3
    path = api.fetch_screenshot(rom, 1)
    assert path and os.path.dirname(path) == rommlib.COVER_CACHE
    with open(path, "rb") as f:
        assert f.read(8) == b"\x89PNG\r\n\x1a\n"
    monkeypatch.setattr(api.s, "get", lambda *a, **k: pytest.fail("fetched again"))
    assert api.fetch_screenshot(rom, 1) == path
    assert api.fetch_screenshot(rom, 3) is None and api.fetch_screenshot({"id": 1}, 0) is None
    assert api.fetch_cover(dict(rom, path_cover_small=None, path_cover_large=None)) is None


def test_screenshots_count_toward_the_cache_prune(isolated, fast_mock_url, monkeypatch):
    api = rommlib.Api(fast_mock_url, "demo")
    api.cache_limit = 1
    monkeypatch.setattr(rommlib, "PRUNE_EVERY", 1)
    calls = []
    monkeypatch.setattr(rommlib, "prune_cache", lambda lim: calls.append(lim))
    assert api.fetch_screenshot(api.user_list("playing")["items"][0], 0) and calls == [1]


# ------------------------------------------------------------ What's new

def test_notes_show_once_per_version_after_an_update(isolated, monkeypatch):
    monkeypatch.setattr(rommlib, "VERSION_LABEL", "0.9.0")
    cfg = dict(rommlib.DEFAULT_CONFIG)
    assert rommlib.notes_due(cfg) is None                          # a fresh install: nothing to catch up on
    cfg["url"] = "http://romm.lan"
    assert rommlib.notes_due(cfg) == "0.9.0"                       # set up before the notes existed
    cfg["notes_seen"] = "0.8.2"
    assert rommlib.notes_due(cfg) == "0.9.0"
    assert rommlib.mark_notes_seen(cfg)
    assert json.load(open(rommlib.CONFIG_PATH))["notes_seen"] == "0.9.0"
    assert rommlib.notes_due(rommlib.load_config()) is None
    monkeypatch.setattr(rommlib, "VERSION_LABEL", "0.9.0+n20261020.0300.abc1234")
    assert rommlib.notes_due(rommlib.load_config()) == "0.9.0+n20261020.0300.abc1234"


def test_release_notes_from_github(isolated, fast_mock_url, monkeypatch):
    monkeypatch.setattr(rommlib, "GITHUB_API", fast_mock_url)
    monkeypatch.setattr(rommlib, "VERSION_LABEL", "0.9.0+abc1234")
    got = rommlib.release_notes()
    assert got["tag"] == "v0.9.0" and "your RomM lists" in got["body"] and got["label"] == "0.9.0+abc1234"
    assert rommlib.release_notes("0.9.0+n20261020.0300.abc1234")["tag"] == "nightly"
    cfg = dict(rommlib.DEFAULT_CONFIG, url="http://romm.lan", notes_seen="0.8.0")
    assert rommlib.whats_new(cfg)["tag"] == "v0.9.0"
    cfg["notes_seen"] = "0.9.0+abc1234"
    assert rommlib.whats_new(cfg) is None
    monkeypatch.setattr(rommlib, "GITHUB_API", "http://127.0.0.1:9")       # offline: nothing, never raises
    assert rommlib.release_notes() is None


def test_release_note_lines():
    body = ("## Features\r\n\r\n- Lorem: **bold** and `code` [link](https://x) *em*\n<!-- hidden -->\n"
            "1. Numbered\n---\nPlain snake_case_name text.\n")
    assert rommlib.release_note_lines(body) == [
        ("h", "Features"), ("li", "Lorem: bold and code link em"), ("li", "Numbered"),
        ("p", "Plain snake_case_name text.")]


# ------------------------------------------------------------ Compare with RomM

def _rom(rid, fs_name, size=100, **kw):
    stem, ext = os.path.splitext(fs_name)
    return dict({"id": rid, "name": stem, "fs_name": fs_name, "fs_name_no_ext": stem,
                 "fs_extension": ext.lstrip("."), "fs_size_bytes": size}, **kw)


def test_compare_classifies_every_game_on_the_box(isolated):
    lib = rommlib.Library()
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    roms = [_rom(1, "Same (USA).zip", sha1_hash="aa"), _rom(2, "Packed (USA).zip"), _rom(3, "Changed (USA).zip"),
            _rom(4, "Renamed (USA) (Rev 1).zip"), _rom(5, "Loose (USA).cue"), _rom(6, "LoosePacked (USA).zip"),
            _rom(7, "Resized (USA).iso", size=500), _rom(8, "Set (USA) (Disc 1).zip"),
            _rom(9, "Set (USA) (Disc 2).zip"), _rom(10, "Tracks (USA).zip")]
    lib.mark(roms[0], "psx", [_touch(os.path.join(sysdir, "Same (USA).cue")),
                              _touch(os.path.join(sysdir, "Same (USA).bin"))])
    lib.mark(roms[1], "psx", [_touch(os.path.join(sysdir, "Packed (USA).chd"))])
    lib.mark(roms[2], "psx", [_touch(os.path.join(sysdir, "Changed (USA).chd"))])
    lib.mark(dict(roms[3], fs_name="Renamed (USA).zip", fs_size_bytes=None), "psx",
             [_touch(os.path.join(sysdir, "Renamed (USA).chd"))])
    lib.update(lambda inst: inst["4"].pop("romm", None))        # a record from before the stamp
    _touch(os.path.join(sysdir, "Loose (USA).cue"))
    _touch(os.path.join(sysdir, "Loose (USA).bin"))
    _touch(os.path.join(sysdir, "LoosePacked (USA).chd"))
    _touch(os.path.join(sysdir, "Resized (USA).iso"), 400)
    _touch(os.path.join(sysdir, "Homebrew Thing.cue"))
    _touch(os.path.join(sysdir, "Tracks (USA).cue"))
    _touch(os.path.join(sysdir, "Tracks (USA) (Track 1).bin"))
    _touch(os.path.join(sysdir, "Tracks (USA) (Track 2).bin"))
    d1, d2 = _touch(os.path.join(sysdir, "Set (USA) (Disc 1).chd")), _touch(os.path.join(sysdir, "Set (USA) (Disc 2).chd"))
    m3u = os.path.join(sysdir, "Set (USA).m3u")
    open(m3u, "w").write("Set (USA) (Disc 1).chd\nSet (USA) (Disc 2).chd\n")
    lib.mark(roms[7], "psx", [d1, m3u])
    lib.mark(roms[8], "psx", [d2, m3u])
    _touch(os.path.join(sysdir, "images", "Same (USA)-image.png"))
    _touch(os.path.join(sysdir, "gamelist.xml"))
    _touch(os.path.join(sysdir, "notes.txt"))
    roms[2]["fs_size_bytes"] = 101                                 # RomM's file changed since
    roms[8]["fs_size_bytes"] = 99

    got = rommlib.compare_games(roms, lib.snapshot(), rommlib.local_games("psx"), "psx")
    status = {it["name"]: it["status"] for it in got["items"]}
    assert status == {"Same (USA).cue": "matches", "Packed (USA).chd": "compressed",
                      "Changed (USA).chd": "newer", "Renamed (USA).chd": "newer", "Loose (USA).cue": "matches",
                      "LoosePacked (USA).chd": "compressed", "Resized (USA).iso": "newer",
                      "Homebrew Thing.cue": "not_in_romm", "Set (USA).m3u": "newer", "Tracks (USA).cue": "matches"}
    sets = next(it for it in got["items"] if it["name"] == "Set (USA).m3u")
    assert sets["rom_ids"] == [8, 9] and sorted(sets["paths"]) == sorted([d1, d2, m3u])
    assert got["counts"] == {"matches": 3, "compressed": 2, "newer": 4, "not_in_romm": 1}
    assert next(it for it in got["items"] if it["name"] == "Same (USA).cue")["rom"]["id"] == 1
    assert next(it for it in got["items"] if it["name"] == "Same (USA).cue")["label"] == "Matches RomM"

    col = rommlib.compare_games([roms[1], roms[4]], lib.snapshot(), rommlib.local_games("psx"), "psx",
                                scope="collection")
    assert {it["name"]: it["status"] for it in col["items"]} == {"Packed (USA).chd": "compressed",
                                                                  "Loose (USA).cue": "matches"}


def test_install_records_carry_what_romm_said(isolated):
    lib = rommlib.Library()
    lib.mark(_rom(3, "A.zip", size=7, sha1_hash="ab", md5_hash=None), "gba", ["/x"])
    assert lib.snapshot()["3"]["romm"] == {"size": 7, "sha1": "ab"}


def test_compare_is_quick_on_a_10k_platform(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "snes")
    os.makedirs(sysdir)
    roms = [_rom(i, "Game %05d (USA).zip" % i) for i in range(10000)]
    for i in range(0, 10000, 2):
        open(os.path.join(sysdir, "Game %05d (USA).zip" % i), "wb").close()
    for i in range(500):
        open(os.path.join(sysdir, "Other %d.sfc" % i), "wb").close()
    records = {str(i): {"system": "snes", "paths": [os.path.join(sysdir, "Game %05d (USA).zip" % i)],
                        "fs_name": "Game %05d (USA).zip" % i, "romm": {"size": 100}} for i in range(0, 4000, 2)}
    t = time.monotonic()
    got = rommlib.compare_games(roms, records, rommlib.local_games("snes"), "snes")
    assert time.monotonic() - t < 3
    assert got["counts"] == {"matches": 5000, "compressed": 0, "newer": 0, "not_in_romm": 500}


def test_compare_platform_and_collection_against_romm(isolated, fast_mock_url):
    api = rommlib.Api(fast_mock_url, "demo")
    lib = rommlib.Library()
    gba = api.roms(platform_ids=[3], limit=3)["items"]
    sysdir = os.path.join(rommlib.ROMS_ROOT, "gba")
    lib.mark(api.rom(gba[0]["id"]), "gba", [_touch(os.path.join(sysdir, gba[0]["fs_name_no_ext"] + ".gba"))])
    _touch(os.path.join(sysdir, gba[1]["fs_name"]), gba[1]["fs_size_bytes"])
    _touch(os.path.join(sysdir, "Not From RomM.gba"))
    got = rommlib.compare_platform(api, lib, "gba", [3])
    assert {it["name"]: it["status"] for it in got["items"]} == {
        gba[0]["fs_name_no_ext"] + ".gba": "matches", gba[1]["fs_name"]: "matches", "Not From RomM.gba": "not_in_romm"}
    assert set(got["items"][0]["rom"] or {}) <= set(rommlib.COMPARE_FIELDS)

    col = next(c for c in api.collections() if c["name"] == "Theme: Open Source Gems")
    ids = {r["id"] for r in api.roms_all(collection=("collection_id", col["id"]))}
    systems = {3: "gba", 1: "nes"}
    got = rommlib.compare_collection(api, lib, ("collection_id", col["id"]), lambda r: systems.get(r["platform_id"]))
    assert all(set(it["rom_ids"]) <= ids for it in got["items"])
    assert all(it["status"] != "not_in_romm" for it in got["items"])
    cancel = threading.Event()
    cancel.set()
    assert rommlib.compare_platform(api, lib, "gba", [3], cancel=cancel) is None


# ------------------------------------------------------------ backlog fit

def test_backlog_fit_keeps_romm_order(isolated):
    gb = 1 << 30
    items = [(_rom(1, "A.gba", 3 * gb), "gba"), (_rom(2, "B.gba", 9 * gb), "gba"), (_rom(3, "C.gba", 2 * gb), "gba"),
             (_rom(4, "D.gba", 1), None), (_rom(5, "E.gba", 1 * gb), "gba"), (_rom(6, "F.gba", 1 * gb), "gba")]
    got = rommlib.backlog_fit(items, free=10 * gb, reserve=2 * gb, on_box=lambda r, s: r["id"] == 6)
    assert [r["id"] for r, _ in got["fit"]] == [1, 3, 5]
    assert [r["id"] for r, _ in got["wait"]] == [2]
    assert [r["id"] for r, _ in got["skipped"]] == [4, 6]
    assert got["need"] == 6 * gb and got["free"] == 10 * gb
    assert rommlib.backlog_fit([], 0)["fit"] == []


def test_backlog_fit_reads_archive_listings_first(isolated, monkeypatch):
    gb = 1 << 30
    zipped = _rom(1, "Disc (USA).zip", 1 * gb)
    read = []

    def listing(api, rom, system):
        read.append(rom["id"])
        rom["_members"] = {rom["fs_name"]: [{"name": "Disc (USA).iso", "size": 2 * gb}]}
        return rom["_members"]
    monkeypatch.setattr(rommlib, "zip_listing", listing)
    got = rommlib.backlog_fit_listed(None, [(zipped, "psx"), (_rom(2, "Cart.gba", 1), "gba")],
                                     cfg={"keep_free_gb": 0}, free=6 * gb)
    assert read == [1]
    assert [r["id"] for r, _ in got["fit"]] == [1, 2]
    assert got["need"] == 3 * gb + 1                               # the iso plus the zip beside it


# ------------------------------------------------------------ played here

def test_played_here_joins_es_by_path(isolated, fake_es):
    rommlib._PLAY.clear()
    lib = rommlib.Library()
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    a = _touch(os.path.join(sysdir, "A (USA).chd"))
    folder = os.path.join(sysdir, "F (USA)")
    _touch(os.path.join(folder, "F (USA).cue"))
    d1, d2 = _touch(os.path.join(sysdir, "S (Disc 1).chd")), _touch(os.path.join(sysdir, "S (Disc 2).chd"))
    m3u = os.path.join(sysdir, "S.m3u")
    open(m3u, "w").write("S (Disc 1).chd\nS (Disc 2).chd\n")
    lib.mark({"id": 1, "fs_name": "A (USA).zip"}, "psx", [a])
    lib.mark({"id": 2, "fs_name": "F (USA)"}, "psx", [folder])
    lib.mark({"id": 3, "fs_name": "S (Disc 1).zip"}, "psx", [d1, m3u])
    es = fake_es({"psx": {".chd", ".cue", ".m3u"}})
    es.mem["psx"][a].update(playcount="3", lastplayed="20261010T224528", gametime="3600")
    es.mem["psx"][os.path.join(folder, "F (USA).cue")].update(playcount="1", lastplayed="20260101T100000")
    es.mem["psx"][m3u].update(playcount="2", lastplayed="20261001T080000")
    got = rommlib.played_here(lib, {"id": 1}, "psx")
    assert got["playcount"] == 3 and got["lastplayed"] == "20261010T224528" and got["gametime"] == 3600 and got["es"]
    assert got["last_played"] == int(time.mktime(time.strptime("20261010T224528", "%Y%m%dT%H%M%S")))
    assert rommlib.played_here(lib, {"id": 2}, "psx")["playcount"] == 1
    assert rommlib.played_here(lib, {"id": 3}, "psx")["playcount"] == 2
    assert rommlib.played_here(lib, {"id": 9, "fs_name": "Nope.zip"}, "psx") is None
    calls = len(es.calls)
    rommlib.played_here(lib, {"id": 1}, "psx")
    assert len(es.calls) == calls                                  # ES asked once a while, not per game


def test_played_here_without_es(isolated, monkeypatch):
    rommlib._PLAY.clear()
    monkeypatch.setattr(rommlib, "ES_API", "http://127.0.0.1:9")
    lib = rommlib.Library()
    a = _touch(os.path.join(rommlib.ROMS_ROOT, "nes", "A.nes"))
    lib.mark({"id": 1, "fs_name": "A.nes"}, "nes", [a])
    assert rommlib.played_here(lib, {"id": 1}, "nes") == {
        "playcount": 0, "lastplayed": "", "last_played": None, "gametime": 0, "es": False}
    assert rommlib.es_play_stats("nes") is None
