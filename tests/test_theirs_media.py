"""Found on a Batocera VM (2026-11-05): media the owner had for a game before the app
downloaded it (their copy scraped: images/<stem>-image.png, -marquee.png, a video) was
deleted with the app's download, and overwritten by RomM's when a damaged copy was
downloaded again; a download into a folder holding another game of the owner's (a
translation's sheet) gave RomM's info and art to that game, not the download's own sheet."""
import os

import pytest

import rommlib
import rb_finalize
from test_es_media import FakeEsApi, PNG, all_files, rom_54, served  # noqa: F401 (fixture)

ES = {"psx": {".cue", ".chd", ".m3u"}, "nes": {".zip", ".nes"}}


def _file(system, rel, data=b"x"):
    p = os.path.join(rommlib.ROMS_ROOT, system, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    return p


@pytest.fixture
def dl(isolated, monkeypatch):
    queued = []
    monkeypatch.setattr(rommlib, "append_gamelist", lambda *es: queued.extend(es))
    lib = rommlib.Library()
    d = rommlib.Downloader(None, lib, dict(rommlib.DEFAULT_CONFIG), persist=False)
    d.es = ES
    d.queued = queued
    return d


def _rom(**kw):
    return rom_54(fs_name="Lorem Mages.zip", fs_name_no_ext="Lorem Mages", **kw)


# ------------------------------------------------------------ A: deleting keeps the owner's media

def test_media_there_before_the_download_is_the_owners_and_stays_when_it_is_deleted(dl):
    lib = dl.library
    game = _file("nes", "Lorem Mages.zip", b"g" * 100)
    theirs = [_file("nes", "images/Lorem Mages-image.png", b"mine"),
              _file("nes", "images/Lorem Mages-marquee.png", b"mine"),
              _file("nes", "videos/Lorem Mages-video.mp4", b"mine")]
    rom = _rom()
    e = dl._queue_gamelist(rom, "nes", [game], files=[game])
    assert dl.queued == [e] and "game" not in e
    assert sorted(f for fs in e["theirs_media"].values() for f in fs) == sorted(theirs)
    lib.mark(rom, "nes", [game], meta=e, files=[game])
    made = _file("nes", "images/Lorem Mages-thumb.png", b"romm")      # what ES was sent for it
    assert lib.made_media(rom, "nes") == [made]
    lib.uninstall(rom, "nes")
    assert not os.path.exists(made) and not os.path.exists(game)
    assert all(os.path.exists(p) for p in theirs)


def test_downloading_the_apps_game_again_keeps_the_app_s_media_the_apps(dl):
    lib = dl.library
    game = _file("nes", "Lorem Mages.zip")
    mine = _file("nes", "images/Lorem Mages-image.png", b"mine")
    rom = _rom()
    lib.mark(rom, "nes", [game], meta=dl._queue_gamelist(rom, "nes", [game], files=[game]), files=[game])
    made = _file("nes", "videos/Lorem Mages-video.mp4", b"romm")
    e = dl._queue_gamelist(rom, "nes", [game], files=[game])            # Download again
    assert e["theirs_media"] == {"image": [mine]}
    lib.mark(rom, "nes", [game], meta=e, files=[game])
    assert lib.made_media(rom, "nes") == [made]


def test_an_ordinary_download_carries_nothing_new(dl):
    game = _file("nes", "Lorem Mages.zip")
    e = dl._queue_gamelist(_rom(), "nes", [game], files=[game])
    assert "theirs_media" not in e and "game" not in e and e["paths"] == [game]


# ------------------------------------------------------------ B: finalize never replaces them

def test_finalize_never_sends_romms_over_the_owners_media(served, dl):
    files, asked = served
    rom = _rom()
    files.update(all_files(rom))
    game = _file("nes", "Lorem Mages.zip")
    # the owner's scrape: the box art as both image and thumbnail (what "box art again" replaces)
    img = _file("nes", "images/Lorem Mages-image.png", PNG)
    thumb = _file("nes", "images/Lorem Mages-thumb.png", PNG)
    marquee = _file("nes", "images/Lorem Mages-marquee.png", b"my logo")    # ES doesn't list it
    e = dl._queue_gamelist(rom, "nes", [game], files=[game])
    g = {"id": "a1", "path": game, "name": "Lorem Mages",
         "image": "/systems/nes/games/a1/media/image", "thumbnail": "/systems/nes/games/a1/media/thumbnail"}
    esapi = FakeEsApi({game: g})
    esapi.files = {("a1", "image"): PNG, ("a1", "thumbnail"): PNG}
    esapi.media_present = lambda *a: True
    assert rb_finalize.apply([e], None, ES, esapi, {}) == []
    tags = {t for t, _, _ in esapi.media}
    assert not tags & {"image", "thumbnail", "marquee"} and "video" in tags
    with open(img, "rb") as a, open(thumb, "rb") as b, open(marquee, "rb") as c:
        assert a.read() == PNG and b.read() == PNG and c.read() == b"my logo"
    assert not any("screenshots" in p or "marquee" in p or "logo" in p for p in asked)   # not even fetched


def test_an_older_entry_still_gets_its_image_again(served):
    files, _ = served
    rom = _rom()
    files.update(all_files(rom))
    game = _file("nes", "Lorem Mages.zip")
    e = rommlib.gamelist_entry(rom, "nes", [game])                     # no theirs_media
    g = {"id": "a1", "path": game, "name": "Lorem Mages",
         "image": "/systems/nes/games/a1/media/image", "thumbnail": "/systems/nes/games/a1/media/thumbnail"}
    esapi = FakeEsApi({game: g})
    esapi.files = {("a1", "image"): PNG, ("a1", "thumbnail"): PNG}
    esapi.media_present = lambda *a: True
    rb_finalize.apply([e], None, ES, esapi, {})
    assert "image" in {t for t, _, _ in esapi.media}


# ------------------------------------------------------------ C: the download's own sheet

def _folder_with_their_translation():
    theirs = _file("psx", "Lorem Disc/Mix.cue", b'FILE "Mix.bin" BINARY\n')
    _file("psx", "Lorem Disc/Mix.bin", b"t" * 10)
    cue = _file("psx", "Lorem Disc/Lorem Disc.cue", b'FILE "Lorem Disc.bin" BINARY\n')
    bin_ = _file("psx", "Lorem Disc/Lorem Disc.bin", b"d" * 10)
    return os.path.dirname(cue), cue, bin_, theirs


def test_a_download_into_a_folder_holding_their_game_names_its_own_sheet(served, dl):
    files, _ = served
    rom = rom_54(fs_name="Lorem Disc.zip", fs_name_no_ext="Lorem Disc")
    files.update(all_files(rom))
    folder, cue, bin_, theirs = _folder_with_their_translation()
    assert rommlib.primary_path("psx", [folder], ES) == theirs          # what ES's first sheet is
    e = dl._queue_gamelist(rom, "psx", [folder], files=[cue, bin_])
    assert e["game"] == cue and e["paths"] == [folder]
    mine = {"id": "a1", "path": cue, "name": "Lorem Disc"}
    other = {"id": "b2", "path": theirs, "name": "Mix"}
    esapi = FakeEsApi({cue: mine, theirs: other})
    assert rb_finalize.wanted([e], ES) == {"psx": {cue}}
    assert rb_finalize.apply([e], None, ES, esapi, {}) == []
    assert other == {"id": "b2", "path": theirs, "name": "Mix"}           # the owner's game untouched
    assert mine["name"] == "Lorem Mages" and mine.get("image")


def test_its_own_sheet_is_followed_to_its_chd(served, dl):
    files, _ = served
    rom = rom_54(fs_name="Lorem Disc.zip", fs_name_no_ext="Lorem Disc")
    files.update(all_files(rom))
    folder, cue, bin_, theirs = _folder_with_their_translation()
    e = dl._queue_gamelist(rom, "psx", [folder], files=[cue, bin_])
    chd = _file("psx", "Lorem Disc.chd")
    os.remove(cue)
    os.remove(bin_)
    batch = rb_finalize.route_renames([e, {"system": "psx", "rename": {cue: chd, bin_: chd}}])
    assert batch[0]["game"] == chd and batch[0]["paths"] == [folder]
    mine = {"id": "a1", "path": chd, "name": "Lorem Disc"}
    other = {"id": "b2", "path": theirs, "name": "Mix"}
    esapi = FakeEsApi({chd: mine, theirs: other})
    rb_finalize.apply([batch[0]], None, ES, esapi, {})
    assert other == {"id": "b2", "path": theirs, "name": "Mix"} and mine["name"] == "Lorem Mages"


def test_an_entry_from_before_names_the_folder_as_it_did():
    e = {"system": "psx", "paths": ["/nowhere/Lorem Disc"], "name": "Lorem"}
    assert rb_finalize._own_game(e) is None
    assert rb_finalize.wanted([e], ES) == {}
