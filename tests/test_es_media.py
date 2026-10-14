"""What the app tells EmulationStation about a game beyond its name: RomM's
regions, languages and franchise as ES's fields, and the media RomM stored
(screenshot, box art, logo, title screen, 3D box, fan art, video, manual) as
ES's media tags, fetched from RomM's own assets only."""
import json
import os

import pytest

import rommlib
import rb_finalize
from romm import audit

RES = "/assets/romm/resources/"


def rom_54(**kw):
    """A record shaped like RomM 5.4's (RomSchema) for a game scanned with
    ScreenScraper and scan.media: box2d, screenshot, manual, logo, title_screen,
    box3d, fanart, video."""
    r = {
        "id": 3, "name": "Lorem Mages", "summary": "Lorem ipsum dolor sit amet.",
        "regions": ["World", "Unlicensed"], "languages": [],
        "metadatum": {"genres": ["Platform"], "franchises": ["Lorem Saga"], "first_release_date": 1556582400000,
                      "player_count": "1-4", "developers": ["Ipsum Soft"], "publishers": [],
                      "average_rating": 90},
        "path_cover_small": RES + "roms/2/3/cover/small.png?ts=2026-10-13 19:31:26",
        "path_cover_large": RES + "roms/2/3/cover/big.png?ts=2026-10-13 19:31:26",
        "merged_screenshots": [RES + "roms/2/3/screenshots/0.jpg", RES + "roms/2/3/screenshots/1.jpg"],
        "path_video": "roms/2/3/video/video.mp4",
        "has_manual": True, "path_manual": "roms/2/3/manual/3.pdf",
        "url_manual": "https://scraper.invalid/manual.pdf?devpassword=x",
        "ss_metadata": {
            "box2d_path": "roms/2/3/box2d/box2d.png", "box3d_path": "roms/2/3/box3d/box3d.png",
            "logo_path": "roms/2/3/logo/logo.png", "marquee_path": "roms/2/3/marquee/marquee.png",
            "title_screen_path": "roms/2/3/title_screen/title_screen.png",
            "fanart_path": "roms/2/3/fanart/fanart.png", "video_path": "roms/2/3/video/video.mp4",
            "box2d_back_path": None, "physical_path": None,
            # upstream links (they carry the server's scraper credentials): never used
            "logo_url": "https://scraper.invalid/logo.png?devid=x&devpassword=y",
            "video_url": "https://scraper.invalid/video.mp4?devid=x",
        },
        "gamelist_metadata": None, "launchbox_metadata": None,
    }
    r.update(kw)
    return r


# ------------------------------------------------------------- the mapping

def test_es_media_maps_what_romm_stored_to_es_tags():
    m = rommlib.es_media(rom_54())
    assert m == {
        "image": [RES + "roms/2/3/screenshots/0.jpg", RES + "roms/2/3/cover/big.png?ts=2026-10-13 19:31:26"],
        "thumbnail": [RES + "roms/2/3/cover/big.png?ts=2026-10-13 19:31:26", RES + "roms/2/3/box2d/box2d.png"],
        "video": [RES + "roms/2/3/video/video.mp4"],
        "marquee": [RES + "roms/2/3/logo/logo.png", RES + "roms/2/3/marquee/marquee.png"],
        "titleshot": [RES + "roms/2/3/title_screen/title_screen.png"],
        "boxart": [RES + "roms/2/3/box3d/box3d.png"],
        "fanart": [RES + "roms/2/3/fanart/fanart.png"],
    }
    assert not any("scraper.invalid" in p for ps in m.values() for p in ps)


def test_es_media_without_screenshots_or_extras_is_the_cover():
    r = rom_54(merged_screenshots=[], path_video=None, ss_metadata=None)
    cover = r["path_cover_large"]
    assert rommlib.es_media(r) == {"image": [cover], "thumbnail": [cover]}
    assert rommlib.es_media({"id": 1}) == {}


def test_es_media_falls_back_to_gamelist_and_launchbox_media():
    r = rom_54(ss_metadata={"logo_path": None}, gamelist_metadata={
        "marquee_path": "roms/2/3/marquee/gl.png", "physical_path": "roms/2/3/physical/gl.png"},
        launchbox_metadata={"box3d_path": "roms/2/3/box3d/lb.png", "box2d_back_path": "roms/2/3/back/lb.jpg"})
    m = rommlib.es_media(r)
    assert m["marquee"] == [RES + "roms/2/3/marquee/gl.png"]
    assert m["cartridge"] == [RES + "roms/2/3/physical/gl.png"]
    assert m["boxart"] == [RES + "roms/2/3/box3d/lb.png"]
    assert m["boxback"] == [RES + "roms/2/3/back/lb.jpg"]


def test_es_media_carried_by_a_saved_queue_record_is_used_as_is():
    carried = {"image": [RES + "roms/1/1/screenshots/0.png"]}
    assert rommlib.es_media({"id": 1, "_es_media": carried}) is carried


@pytest.mark.parametrize("p, out", [
    ("roms/1/2/logo/logo.png", RES + "roms/1/2/logo/logo.png"),
    (RES + "roms/1/2/logo/logo.png?ts=1", RES + "roms/1/2/logo/logo.png"),
    ("/roms/1/2/logo/logo.png", RES + "roms/1/2/logo/logo.png"),
    ("https://scraper.invalid/logo.png", None),
    ("//scraper.invalid/logo.png", None),
    ("roms/1/../../../etc/passwd", None),
    ("roms\\1\\logo.png", None),
    ("", None), (None, None), (42, None),
])
def test_resource_path_only_ever_names_romms_own_files(p, out):
    assert rommlib.resource_path(p) == out


def test_resource_path_checks_the_kind_of_file():
    assert rommlib.resource_path("roms/1/2/video/video.mp4", (".mp4",))
    assert rommlib.resource_path("roms/1/2/video/video.mkv", (".mp4",)) is None
    assert rommlib.manual_path({"has_manual": True, "path_manual": "roms/1/2/manual/2.pdf"}) == \
        RES + "roms/1/2/manual/2.pdf"
    assert rommlib.manual_path({"has_manual": True, "path_manual": "roms/1/2/manual/2.txt"}) is None
    assert rommlib.manual_path({"has_manual": False, "path_manual": "roms/1/2/manual/2.pdf"}) is None


@pytest.mark.parametrize("regions, code", [
    (["World", "Unlicensed"], "wr"), (["Japan", "Unlicensed"], "jp"), (["USA", "Europe"], "us"),
    (["Europe"], "eu"), (["Spain"], "es"), (["Sweden"], "sw"), (["England"], "uk"),
    (["Unlicensed", "Public Domain"], None), ([], None), (None, None), (["us"], "us"),
])
def test_es_region_uses_the_codes_es_has_flags_for(regions, code):
    assert rommlib.es_region({"regions": regions}) == code


@pytest.mark.parametrize("langs, code", [
    (["English", "Italian"], "en,it"), (["Japanese"], "jp"), (["English", "English"], "en"),
    (["No Language"], None), ([], None), (["Klingon", "French"], "fr"),
])
def test_es_lang_uses_screenscrapers_codes(langs, code):
    assert rommlib.es_lang({"languages": langs}) == code


def test_gamelist_entry_and_es_fields_carry_region_lang_family():
    e = rommlib.gamelist_entry(rom_54(languages=["English"]), "nes", ["/x/Lorem Mages.zip"])
    assert (e["region"], e["lang"], e["family"]) == ("wr", "en", "Lorem Saga")
    assert e["media"]["image"][0].endswith("/screenshots/0.jpg")
    assert e["manual"] == RES + "roms/2/3/manual/3.pdf"
    f = rb_finalize.entry_fields(e)
    assert f["region"] == "wr" and f["lang"] == "en" and f["family"] == "Lorem Saga"
    assert f["releasedate"] == "20190430T000000" and f["rating"] == "0.90" and f["players"] == "1-4"
    bare = rommlib.gamelist_entry(rom_54(regions=[], metadatum={}), "nes", ["/x.zip"])
    assert not {"region", "lang", "family"} & set(rb_finalize.entry_fields(bare))
    assert json.loads(json.dumps(e)) == e            # the queue is JSON lines


# ------------------------------------------------------------- finalize

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
JPG = b"\xff\xd8\xff\xe0" + b"\0" * 32
MP4 = b"\0\0\0\x20ftypisom" + b"\0" * 32
PDF = b"%PDF-1.4\n%%EOF\n"


class FakeEsApi:
    """rb_finalize's EsApi, in memory: one system, its games by path."""

    def __init__(self, games):
        self.g = games
        self.meta, self.media = [], []
        self.files = {}                    # (game id, tag) -> bytes ES serves

    def games(self, system, refresh=False):
        return self.g

    def game(self, system, gid):
        return next((g for g in self.g.values() if g["id"] == gid), None)

    def set_meta(self, system, gid, fields):
        if fields:
            self.meta.append(dict(fields))
            self.game(system, gid).update(fields)
        return True

    def set_media(self, system, gid, mtype, data, ctype):
        self.media.append((mtype, ctype, len(data)))
        self.game(system, gid)[mtype] = "/systems/%s/games/%s/media/%s" % (system, gid, mtype)
        self.files[(gid, mtype)] = data
        return True

    def media_file(self, system, gid, tag):
        return self.files.get((gid, tag))


@pytest.fixture
def served(monkeypatch, isolated):
    """RomM's assets as rb_finalize fetches them: {server path: bytes}; a path
    not in it answers 404. Big files are reported like RomM's Content-Length."""
    files, asked = {}, []

    def fetch(api, path, limit=200 << 20, info=None):
        asked.append(path)
        data = files.get(path)
        if data is not None and len(data) > limit:
            if info is not None:
                info["too_big"] = len(data)
            return None
        return data
    monkeypatch.setattr(rb_finalize, "fetch_bytes", fetch)
    return files, asked


def game_here(system="nes", name="Lorem Mages.zip", **have):
    path = os.path.join(rommlib.ROMS_ROOT, system, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").close()
    g = {"id": "a1", "path": path, "name": os.path.splitext(name)[0]}
    g.update(have)
    return path, g


def all_files(rom):
    m = rommlib.es_media(rom)
    out = {m["image"][0]: JPG, m["thumbnail"][0]: PNG, m["video"][0]: MP4, rommlib.manual_path(rom): PDF}
    for t in ("marquee", "titleshot", "boxart", "fanart"):
        out[m[t][0]] = PNG
    return out


def test_a_download_gets_every_media_romm_has(served):
    files, asked = served
    rom = rom_54()
    files.update(all_files(rom))
    path, g = game_here()
    esapi = FakeEsApi({path: g})
    e = rommlib.gamelist_entry(rom, "nes", [path])
    assert rb_finalize.apply([e], None, {"nes": {".zip"}}, esapi, {}) == []
    assert sorted(esapi.media) == sorted([
        ("image", "image/jpeg", len(JPG)), ("thumbnail", "image/png", len(PNG)), ("video", "video/mp4", len(MP4)),
        ("marquee", "image/png", len(PNG)), ("titleshot", "image/png", len(PNG)),
        ("boxart", "image/png", len(PNG)), ("fanart", "image/png", len(PNG)),
        ("manual", "application/pdf", len(PDF))])
    assert esapi.meta[0]["region"] == "wr" and esapi.meta[0]["family"] == "Lorem Saga"
    assert all(p.startswith(RES) for p in asked)                          # RomM's own files only
    assert RES + "roms/2/3/marquee/marquee.png" not in asked              # the logo came first


def test_media_es_has_is_never_replaced_and_fill_adds_the_rest(served):
    files, asked = served
    rom = rom_54()
    files.update(all_files(rom))
    path, g = game_here(image="/systems/nes/games/a1/media/image", desc="my own words",
                        video="/systems/nes/games/a1/media/video")
    esapi = FakeEsApi({path: g})
    e = rommlib.gamelist_entry(rom, "nes", [path])
    e["fill"] = True
    assert rb_finalize.apply([e], None, {"nes": {".zip"}}, esapi, {}) == []
    tags = {t for t, _, _ in esapi.media}
    assert tags == {"thumbnail", "marquee", "titleshot", "boxart", "fanart", "manual"}
    assert "desc" not in esapi.meta[0] and esapi.meta[0]["region"] == "wr"
    assert not any("screenshots" in p or "video" in p for p in asked)   # not even fetched


def test_the_next_source_is_tried_when_one_doesnt_come(served):
    files, _ = served
    rom = rom_54(path_video=None, ss_metadata={"box2d_path": "roms/2/3/box2d/box2d.png"})
    files[rom["path_cover_large"]] = PNG                 # the screenshot 404s; the cover doesn't
    files[RES + "roms/2/3/box2d/box2d.png"] = JPG
    path, g = game_here()
    esapi = FakeEsApi({path: g})
    e = rommlib.gamelist_entry(rom, "nes", [path], manual=False)
    rb_finalize.apply([e], None, {"nes": {".zip"}}, esapi, {})
    assert ("image", "image/png", len(PNG)) in esapi.media                 # the cover stands in
    assert ("thumbnail", "image/png", len(PNG)) in esapi.media


def test_a_video_over_the_cap_is_skipped_and_logged(served, monkeypatch):
    files, _ = served
    monkeypatch.setattr(rb_finalize, "VIDEO_MAX", 1000)
    rom = rom_54()
    files.update(all_files(rom))
    files[RES + "roms/2/3/video/video.mp4"] = MP4 + b"\0" * 2000
    path, g = game_here()
    esapi = FakeEsApi({path: g})
    rb_finalize.apply([rommlib.gamelist_entry(rom, "nes", [path])], None, {"nes": {".zip"}}, esapi, {})
    assert "video" not in {t for t, _, _ in esapi.media} and "image" in {t for t, _, _ in esapi.media}
    with open(rommlib.LOG_PATH) as f:
        assert "video skipped" in f.read()


def test_files_that_arent_what_they_claim_are_left_out(served):
    files, _ = served
    rom = rom_54()
    files.update(all_files(rom))
    files[RES + "roms/2/3/video/video.mp4"] = b"<html>not a video</html>"
    files[rommlib.manual_path(rom)] = b"<html>login</html>"
    path, g = game_here()
    esapi = FakeEsApi({path: g})
    rb_finalize.apply([rommlib.gamelist_entry(rom, "nes", [path])], None, {"nes": {".zip"}}, esapi, {})
    tags = {t for t, _, _ in esapi.media}
    assert "video" not in tags and "manual" not in tags and "marquee" in tags


def test_an_entry_queued_by_an_older_version_still_gets_its_cover(served):
    files, _ = served
    files["/covers/1.png"] = PNG
    path, g = game_here()
    esapi = FakeEsApi({path: g})
    old = {"system": "nes", "paths": [path], "rom_id": 1, "name": "Lorem", "cover": "/covers/1.png",
           "manual": None}
    rb_finalize.apply([old], None, {"nes": {".zip"}}, esapi, {})
    assert esapi.media == [("image", "image/png", len(PNG))]


def test_image_kind_sniffs_what_es_takes():
    assert rb_finalize.image_kind(PNG) == "image/png"
    assert rb_finalize.image_kind(JPG) == "image/jpeg"
    assert rb_finalize.image_kind(b"GIF89a....") == "image/gif"
    assert rb_finalize.image_kind(b"RIFF\0\0\0\0WEBPVP8 ") is None       # converted to PNG first


# ------------------------------------------------------------- what "missing info" means

def test_offered_is_only_what_romm_has():
    full = rommlib.gamelist_entry(rom_54(languages=["English"]), "nes", ["/x.zip"])
    assert audit.offered(full) == {"name", "desc", "releasedate", "developer", "genre", "players", "region",
                                   "lang", "family", "image", "thumbnail", "video", "marquee", "titleshot",
                                   "boxart", "fanart", "manual"}
    bare = rommlib.gamelist_entry(rom_54(merged_screenshots=[], path_video=None, ss_metadata=None,
                                         has_manual=False, summary=None, regions=[], metadatum={}),
                                  "nes", ["/x.zip"])
    assert audit.offered(bare) == {"name", "image", "thumbnail"}
    assert "manual" not in audit.offered(rommlib.gamelist_entry(rom_54(), "nes", ["/x.zip"], manual=False))


class ListApi:
    def __init__(self, roms):
        self.roms = roms

    def roms_all(self, platform_ids, cancel=None, limit=None, keep=None):
        return [r for r in self.roms if keep is None or keep(r)]


def _gamelist(sysdir, tags):
    body = "".join("<%s>%s</%s>" % (t, "./images/x.png" if t in audit.OFFER_MEDIA else "x", t) for t in tags)
    with open(os.path.join(sysdir, "gamelist.xml"), "w") as f:
        f.write("<gameList><game><path>./Lorem Mages.zip</path>%s</game></gameList>" % body)
    os.makedirs(os.path.join(sysdir, "images"), exist_ok=True)       # media count when their file is there
    open(os.path.join(sysdir, "images", "x.png"), "wb").close()


def test_fill_in_doesnt_flag_a_game_for_media_romm_doesnt_have(isolated):
    rom = rom_54(fs_name="Lorem Mages.zip", fs_name_no_ext="Lorem Mages", merged_screenshots=[],
                 path_video=None, ss_metadata=None, has_manual=False, languages=[])
    sysdir = os.path.join(rommlib.ROMS_ROOT, "nes")
    os.makedirs(sysdir)
    open(os.path.join(sysdir, "Lorem Mages.zip"), "wb").close()
    have = audit.offered(rommlib.gamelist_entry(rom, "nes", [os.path.join(sysdir, "Lorem Mages.zip")]))
    assert "video" not in have and "marquee" not in have
    _gamelist(sysdir, have)                                  # all RomM has: no video, logo...
    entries, st = rommlib.plan_info(ListApi([rom]), "nes", [2], {"nes": {".zip"}})
    assert entries == [] and st["complete"] == 1
    # the same game once RomM has a logo and a video for it: those are missing now
    rom.update(path_video="roms/2/3/video/video.mp4", ss_metadata={"logo_path": "roms/2/3/logo/logo.png"})
    entries, st = rommlib.plan_info(ListApi([rom]), "nes", [2], {"nes": {".zip"}})
    assert len(entries) == 1 and entries[0]["fill"] and st["complete"] == 0
    assert set(entries[0]["media"]) >= {"video", "marquee"}


class _Resp:
    def __init__(self, status, body, length=None):
        import io
        self.status_code, self.raw = status, io.BytesIO(body)
        self.headers = {"Content-Length": str(len(body) if length is None else length)}
        self.raw.read = (lambda f: lambda n, decode_content=True: f(n))(self.raw.read)

    def iter_content(self, n):
        yield b"x" * 10

    def close(self):
        pass


class _Session:
    def __init__(self, resp):
        self.resp, self.got = resp, []

    def get(self, url, **kw):
        self.got.append(url)
        return self.resp


def test_fetch_bytes_reports_a_file_over_the_limit_without_reading_it():
    api = type("A", (), {})()
    api.base, api.s = "http://romm.invalid", _Session(_Resp(200, b"", length=60 << 20))
    info = {}
    assert rb_finalize.fetch_bytes(api, RES + "roms/1/1/video/video.mp4", 50 << 20, info) is None
    assert info == {"too_big": 60 << 20}
    api.s = _Session(_Resp(200, MP4))
    assert rb_finalize.fetch_bytes(api, RES + "roms/1/1/video/video.mp4", 50 << 20) == MP4
    assert rb_finalize.fetch_bytes(api, "https://scraper.invalid/video.mp4") is None   # never off RomM
    assert api.s.got == ["http://romm.invalid" + RES + "roms/1/1/video/video.mp4"]
