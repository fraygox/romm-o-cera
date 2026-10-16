"""Settings → Metadata: what EmulationStation gets for a game follows the
settings (game image, videos and their size, extra art, region/language/
series, manuals), on downloads and with Fill in game info; media RomM offers
that never arrives (a video over the size limit, a file that 404s) is noted,
so Fill in game info stops listing the game as incomplete for it."""
import json
import os

import rommlib
import rb_finalize
from romm import audit
from test_es_media import all_files, FakeEsApi, game_here, JPG, ListApi, MP4, PNG, RES, rom_54, served  # noqa: F401


def cfg(**kw):
    return dict(rommlib.DEFAULT_CONFIG, **kw)


# ------------------------------------------------------------- the entry

def test_the_defaults_are_todays_behaviour():
    assert rommlib.gamelist_entry(rom_54(), "nes", ["/x.zip"]) == rommlib.gamelist_entry(rom_54(), "nes", ["/x.zip"],
                                                                                         cfg=cfg())
    assert rommlib.es_video_max(cfg()) == 50 << 20 and rommlib.es_video_max(None) == 50 << 20


def test_box_art_as_the_image_keeps_the_box_art_as_the_thumbnail():
    e = rommlib.gamelist_entry(rom_54(), "nes", ["/x.zip"], cfg=cfg(es_image="boxart"))
    assert e["media"]["image"] == e["media"]["thumbnail"]
    assert e["media"]["image"][0].startswith(RES + "roms/2/3/cover/")
    plain = rommlib.gamelist_entry(rom_54(), "nes", ["/x.zip"], cfg=cfg())
    assert plain["media"]["image"][0].endswith("screenshots/0.jpg")


def test_videos_off_leaves_the_video_out_and_the_size_is_the_cap():
    assert "video" not in rommlib.gamelist_entry(rom_54(), "nes", ["/x.zip"], cfg=cfg(es_video_mb=0))["media"]
    assert "video" in rommlib.gamelist_entry(rom_54(), "nes", ["/x.zip"], cfg=cfg(es_video_mb=25))["media"]
    assert rommlib.es_video_max(cfg(es_video_mb=25)) == 25 << 20
    assert rommlib.es_video_max(cfg(es_video_mb="100")) == 100 << 20
    assert rommlib.es_video_max(cfg(es_video_mb="lots")) == 50 << 20 and rommlib.es_video_max(cfg(es_video_mb=-1)) == 0


def test_extra_art_off_keeps_only_the_image_box_art_and_video():
    m = rommlib.gamelist_entry(rom_54(), "nes", ["/x.zip"], cfg=cfg(es_extra_art=False))["media"]
    assert set(m) == {"image", "thumbnail", "video"}
    assert set(rommlib.EXTRA_ART) == {"marquee", "titleshot", "boxart", "boxback", "fanart", "cartridge"}


def test_region_language_and_series_off_leave_them_out():
    e = rommlib.gamelist_entry(rom_54(languages=["English"]), "nes", ["/x.zip"], cfg=cfg(es_tags=False))
    assert e["region"] is None and e["lang"] is None and e["family"] is None
    assert not {"region", "lang", "family"} & set(rb_finalize.entry_fields(e))
    on = rommlib.gamelist_entry(rom_54(languages=["English"]), "nes", ["/x.zip"], cfg=cfg())
    assert (on["region"], on["lang"], on["family"]) == ("wr", "en", "Lorem Saga")


def test_manuals_follow_their_setting_unless_told_otherwise():
    assert rommlib.gamelist_entry(rom_54(), "nes", ["/x.zip"], cfg=cfg(manuals=False))["manual"] is None
    assert rommlib.gamelist_entry(rom_54(), "nes", ["/x.zip"], cfg=cfg(manuals=False), manual=True)["manual"]
    assert rommlib.gamelist_entry(rom_54(), "nes", ["/x.zip"], cfg=cfg(), manual=False)["manual"] is None


def test_the_download_queue_uses_the_settings(isolated, monkeypatch):
    """Downloader._queue_gamelist: the entry it queues is made from its config."""
    queued = []
    monkeypatch.setattr(rommlib, "append_gamelist", lambda *es: queued.extend(es))
    dl = rommlib.Downloader(None, None, cfg(es_video_mb=0, es_tags=False), persist=False)
    e = dl._queue_gamelist(rom_54(), "nes", ["/x.zip"])
    assert queued == [e] and "video" not in e["media"] and e["region"] is None


# ------------------------------------------------------------- finalize

def test_finalize_applies_todays_video_cap_and_off(served):
    files, asked = served
    rom = rom_54()
    files.update(all_files(rom))
    files[RES + "roms/2/3/video/video.mp4"] = MP4 + b"\0" * (30 << 20)        # 30 MB
    path, g = game_here()
    esapi = FakeEsApi({path: g})
    e = rommlib.gamelist_entry(rom, "nes", [path])
    rb_finalize.apply([e], None, {"nes": {".zip"}}, esapi, {}, cfg(es_video_mb=25))
    assert "video" not in {t for t, _, _ in esapi.media}
    assert rommlib.media_skips()["nes/3"]["video"]["size"] == len(files[RES + "roms/2/3/video/video.mp4"])
    esapi = FakeEsApi({path: dict(g)})
    rb_finalize.apply([e], None, {"nes": {".zip"}}, esapi, {}, cfg(es_video_mb=50))
    assert "video" in {t for t, _, _ in esapi.media} and "video" not in rommlib.media_skips().get("nes/3", {})
    esapi, before = FakeEsApi({path: dict(g)}), len(asked)
    rb_finalize.apply([e], None, {"nes": {".zip"}}, esapi, {}, cfg(es_video_mb=0))
    assert "video" not in {t for t, _, _ in esapi.media}
    assert not any("video" in p for p in asked[before:])                  # off: not even fetched


def test_a_file_romm_cant_serve_is_noted_and_one_that_comes_clears_the_note(served):
    files, _ = served
    rom = rom_54()
    files.update(all_files(rom))
    del files[RES + "roms/2/3/logo/logo.png"]                            # both logo sources 404
    del files[rommlib.manual_path(rom)]
    path, g = game_here()
    esapi = FakeEsApi({path: g})
    rb_finalize.apply([rommlib.gamelist_entry(rom, "nes", [path])], None, {"nes": {".zip"}}, esapi, {}, cfg())
    noted = rommlib.media_skips()["nes/3"]
    assert set(noted) == {"marquee", "manual"}
    assert noted["marquee"] == {"path": RES + "roms/2/3/logo/logo.png", "size": None}
    files[RES + "roms/2/3/logo/logo.png"] = PNG
    esapi = FakeEsApi({path: dict(g, image="x", thumbnail="x", video="x")})
    rb_finalize.apply([rommlib.gamelist_entry(rom, "nes", [path])], None, {"nes": {".zip"}}, esapi, {}, cfg())
    assert set(rommlib.media_skips()["nes/3"]) == {"manual"}


def test_the_notes_are_bounded(isolated, monkeypatch):
    monkeypatch.setattr(rommlib, "SKIPS_MAX", 40)      # the bound, not 2,000 rewrites of the file
    for i in range(rommlib.SKIPS_MAX + 5):
        rommlib.record_media_skip("nes", i, "video", "/v/%d.mp4" % i, 1)
    d = rommlib.media_skips()
    assert len(d) == rommlib.SKIPS_MAX and "nes/0" not in d and "nes/%d" % (rommlib.SKIPS_MAX + 4) in d
    rommlib.record_media_skip("nes", None, "video", "/v/x.mp4")          # an entry without a rom id: nothing
    assert len(rommlib.media_skips()) == rommlib.SKIPS_MAX
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    with open(rommlib.media_skips_path(), "w") as f:
        f.write("not json")
    assert rommlib.media_skips() == {}


# ------------------------------------------------------------- Fill in game info

def _gamelist(sysdir, tags):
    body = "".join("<%s>%s</%s>" % (t, "./images/x.png" if t in audit.OFFER_MEDIA else "x", t) for t in tags)
    with open(os.path.join(sysdir, "gamelist.xml"), "w") as f:
        f.write("<gameList><game><path>./Lorem Mages.zip</path>%s</game></gameList>" % body)
    os.makedirs(os.path.join(sysdir, "images"), exist_ok=True)       # media count when their file is there
    open(os.path.join(sysdir, "images", "x.png"), "wb").close()


def _on_box(system="nes"):
    sysdir = os.path.join(rommlib.ROMS_ROOT, system)
    os.makedirs(sysdir, exist_ok=True)
    open(os.path.join(sysdir, "Lorem Mages.zip"), "wb").close()
    return sysdir


def test_fill_in_doesnt_ask_for_what_the_settings_turned_off(isolated):
    rom = rom_54(fs_name="Lorem Mages.zip", fs_name_no_ext="Lorem Mages", languages=["English"])
    sysdir = _on_box()
    _gamelist(sysdir, {"name", "desc", "releasedate", "developer", "genre", "players", "image", "thumbnail"})
    off = cfg(es_video_mb=0, es_extra_art=False, es_tags=False, manuals=False)
    entries, st = rommlib.plan_info(ListApi([rom]), "nes", [2], {"nes": {".zip"}}, off)
    assert entries == [] and st["complete"] == 1
    entries, st = rommlib.plan_info(ListApi([rom]), "nes", [2], {"nes": {".zip"}}, cfg())
    assert len(entries) == 1 and st["complete"] == 0
    assert audit.offered(entries[0]) >= {"video", "marquee", "region", "lang", "family", "manual"}


def test_fill_in_skips_a_video_over_the_limit_until_the_limit_allows_it(isolated):
    rom = rom_54(fs_name="Lorem Mages.zip", fs_name_no_ext="Lorem Mages", languages=["English"])
    sysdir = _on_box()
    e = rommlib.gamelist_entry(rom, "nes", [os.path.join(sysdir, "Lorem Mages.zip")])
    _gamelist(sysdir, audit.offered(e) - {"video"})
    entries, st = rommlib.plan_info(ListApi([rom]), "nes", [2], {"nes": {".zip"}}, cfg())
    assert len(entries) == 1
    rommlib.record_media_skip("nes", 3, "video", e["media"]["video"][0], 70 << 20)     # 70 MB, over 50
    entries, st = rommlib.plan_info(ListApi([rom]), "nes", [2], {"nes": {".zip"}}, cfg())
    assert entries == [] and st["complete"] == 1
    entries, st = rommlib.plan_info(ListApi([rom]), "nes", [2], {"nes": {".zip"}}, cfg(es_video_mb=100))
    assert len(entries) == 1                                               # fits under 100 MB now
    rom["path_video"] = "roms/2/3/video/video2.mp4"                        # a new file at RomM
    entries, st = rommlib.plan_info(ListApi([rom]), "nes", [2], {"nes": {".zip"}}, cfg())
    assert len(entries) == 1


def test_fill_in_skips_a_file_romm_couldnt_serve_and_a_missing_manual(isolated):
    rom = rom_54(fs_name="Lorem Mages.zip", fs_name_no_ext="Lorem Mages", languages=["English"])
    sysdir = _on_box()
    e = rommlib.gamelist_entry(rom, "nes", [os.path.join(sysdir, "Lorem Mages.zip")])
    _gamelist(sysdir, audit.offered(e) - {"marquee", "manual"})
    assert len(rommlib.plan_info(ListApi([rom]), "nes", [2], {"nes": {".zip"}}, cfg())[0]) == 1
    rommlib.record_media_skip("nes", 3, "marquee", e["media"]["marquee"][0])
    rommlib.record_media_skip("nes", 3, "manual", e["manual"])
    entries, st = rommlib.plan_info(ListApi([rom]), "nes", [2], {"nes": {".zip"}}, cfg())
    assert entries == [] and st["complete"] == 1
    assert rommlib.skipped_tags(e, cfg()) == {"marquee", "manual"}
    rommlib.clear_media_skip("nes", 3, "marquee")
    assert len(rommlib.plan_info(ListApi([rom]), "nes", [2], {"nes": {".zip"}}, cfg())[0]) == 1


def test_the_fill_in_dialog_plans_with_the_apps_config(isolated, rb, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    app = rb.App(None, headless=True)
    app.bg = lambda fn, done=None, err=None: done(fn()) if done else fn()
    seen = []

    def plan(api, system, pids, es, c, cancel):
        seen.append((system, c["es_video_mb"], c["manuals"]))
        return [], {"on_box": 0, "matched": 0, "complete": 0, "queued": 0}
    monkeypatch.setattr(rommlib, "plan_info", plan)
    app.cfg.update(es_video_mb=25, manuals=False)
    app.platforms = [{"id": 2, "_system": "nes", "name": "NES", "slug": "nes", "fs_slug": "nes"}]
    rb.InfoDialog.start_all(app)
    assert seen == [("nes", 25, False)] and app.modal.title == "Nothing to fill in"
    app.modal, app.platforms = None, []
    app.load_platforms = lambda: None
    rb.InfoDialog.start_all(app)
    assert app.modal is None and seen == [("nes", 25, False)]            # no platforms yet: asked for, a toast
