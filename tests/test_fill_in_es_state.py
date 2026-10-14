"""Fill in game info against what EmulationStation holds, as found on a lab RomM with
full ScreenScraper media and Batocera 43.1 VMs (2026-10-13):

- ES keeps new game info in its journal and writes gamelist.xml only now and then
  (quitting, a reload): a game just given everything must not be listed again;
- versions before 0.11 gave ES the cover as the image, and Fill in game info then added
  the same cover as the thumbnail: that image (the box art twice) gives way to the
  screenshot, and ES's old file of it goes; an image of the player's own never does;
- a game deleted and downloaded again gets its old ES entry back, media paths and all,
  to files that are gone (ES serves them as nothing): those count as missing;
- the Settings panel's version line fits with a nightly's long label."""
import os

import rommlib
import rb_finalize
from romm import audit
from test_es_media import all_files, FakeEsApi, game_here, JPG, PNG, rom_54, served  # noqa: F401
from test_metadata_settings import ListApi
from test_text_fits import MARGIN, _spy, ui  # noqa: F401


def cfg(**kw):
    return dict(rommlib.DEFAULT_CONFIG, **kw)


def lorem():
    return rom_54(fs_name="Lorem Mages.zip", fs_name_no_ext="Lorem Mages", languages=["English"])


def give_es(es, path, tags, **files):
    """ES holds these tags for the game (in memory only: no gamelist.xml written)."""
    os.makedirs(os.path.join(rommlib.ROMS_ROOT, "nes", "images"), exist_ok=True)
    open(os.path.join(rommlib.ROMS_ROOT, "nes", "images", "x.png"), "wb").write(PNG)
    with es.lock:
        md = es.mem["nes"][path]
        for t in tags:
            md[t] = "./images/x.png" if t in audit.OFFER_MEDIA else "x"
        md.update(files)


def art(sysdir, name, data):
    os.makedirs(os.path.join(sysdir, "images"), exist_ok=True)
    with open(os.path.join(sysdir, "images", name), "wb") as f:
        f.write(data)
    return "./images/" + name


def test_fill_in_counts_what_es_holds_before_it_writes_gamelist_xml(isolated, fake_es, monkeypatch):
    rom = lorem()
    path, _ = game_here()
    es = fake_es({"nes": {".zip"}})
    give_es(es, path, audit.offered(rommlib.gamelist_entry(rom, "nes", [path])))
    assert not os.path.exists(os.path.join(os.path.dirname(path), "gamelist.xml"))
    entries, st = rommlib.plan_info(ListApi([rom]), "nes", [2], {"nes": {".zip"}}, cfg())
    assert entries == [] and st["complete"] == 1
    monkeypatch.setattr(rommlib, "ES_API", "http://127.0.0.1:9")          # ES can't be asked: the file is all
    entries, st = rommlib.plan_info(ListApi([rom]), "nes", [2], {"nes": {".zip"}}, cfg())
    assert len(entries) == 1


def test_fill_in_lists_a_game_whose_image_is_its_box_art_again(isolated, fake_es):
    rom = lorem()
    path, _ = game_here()
    sysdir = os.path.dirname(path)
    es = fake_es({"nes": {".zip"}})
    tags = audit.offered(rommlib.gamelist_entry(rom, "nes", [path]))
    give_es(es, path, tags, image=art(sysdir, "Lorem Mages-image.png", PNG),
            thumbnail=art(sysdir, "Lorem Mages-thumb.png", PNG))
    entries, st = rommlib.plan_info(ListApi([rom]), "nes", [2], {"nes": {".zip"}}, cfg())
    assert len(entries) == 1 and st["complete"] == 0
    entries, st = rommlib.plan_info(ListApi([rom]), "nes", [2], {"nes": {".zip"}}, cfg(es_image="boxart"))
    assert entries == [] and st["complete"] == 1                           # the box art is what's asked for
    # the screenshot came, as a JPEG: ES left the old PNG behind, but its answer counts
    give_es(es, path, (), image=art(sysdir, "Lorem Mages-image.jpg", JPG))
    entries, st = rommlib.plan_info(ListApi([rom]), "nes", [2], {"nes": {".zip"}}, cfg())
    assert entries == [] and st["complete"] == 1


def test_finalize_gives_an_old_download_its_screenshot_in_one_go(served, fake_es):
    files, _ = served
    rom = lorem()
    files.update(all_files(rom))                    # the cover (thumbnail) is PNG, the screenshot JPG
    path, _ = game_here()
    sysdir = os.path.dirname(path)
    es = fake_es({"nes": {".zip"}})
    give_es(es, path, ("name", "desc"), image=art(sysdir, "Lorem Mages-image.png", PNG))   # 0.10: cover as image
    e = rommlib.gamelist_entry(rom, "nes", [path], cfg=cfg())
    e["fill"] = True
    assert rb_finalize.apply([e], None, {"nes": {".zip"}}, rb_finalize.EsApi(es.url), {}, cfg()) == []
    md = es.mem["nes"][path]
    assert md["thumbnail"] == "./images/Lorem Mages-thumb.png" and md["image"] == "./images/Lorem Mages-image.jpg"
    assert open(os.path.join(sysdir, "images", "Lorem Mages-image.jpg"), "rb").read() == JPG
    assert not os.path.exists(os.path.join(sysdir, "images", "Lorem Mages-image.png"))   # the cover's copy went
    assert os.path.exists(os.path.join(sysdir, "images", "Lorem Mages-thumb.png"))


def test_an_image_of_the_players_own_is_never_replaced(served):
    files, asked = served
    rom = lorem()
    files.update(all_files(rom))
    path, g = game_here(image="/systems/nes/games/a1/media/image", thumbnail="/systems/nes/games/a1/media/thumbnail")
    esapi = FakeEsApi({path: g})
    esapi.files.update({("a1", "image"): b"my own picture", ("a1", "thumbnail"): PNG})
    e = rommlib.gamelist_entry(rom, "nes", [path], cfg=cfg())
    e["fill"] = True
    assert rb_finalize.apply([e], None, {"nes": {".zip"}}, esapi, {}, cfg()) == []
    assert "image" not in {t for t, _, _ in esapi.media}
    assert not any("screenshots" in p for p in asked)
    # the box art twice: the screenshot replaces it; with Game image: Box art, nothing does
    esapi = FakeEsApi({path: dict(g)})
    esapi.files.update({("a1", "image"): PNG, ("a1", "thumbnail"): PNG})
    assert rb_finalize.apply([dict(e)], None, {"nes": {".zip"}}, esapi, {}, cfg()) == []
    assert ("image", "image/jpeg", len(JPG)) in esapi.media
    esapi = FakeEsApi({path: dict(g)})
    esapi.files.update({("a1", "image"): PNG, ("a1", "thumbnail"): PNG})
    e2 = rommlib.gamelist_entry(rom, "nes", [path], cfg=cfg(es_image="boxart"))
    e2["fill"] = True
    assert rb_finalize.apply([e2], None, {"nes": {".zip"}}, esapi, {}, cfg(es_image="boxart")) == []
    assert "image" not in {t for t, _, _ in esapi.media}


def test_a_game_downloaded_again_gets_media_though_es_kept_paths_to_files_that_are_gone(served, fake_es, monkeypatch):
    files, _ = served
    rom = lorem()
    files.update(all_files(rom))
    path, _ = game_here()
    sysdir = os.path.dirname(path)
    es = fake_es({"nes": {".zip"}})
    gone = {"image": "./images/Lorem Mages-image.png", "thumbnail": "./images/Lorem Mages-thumb.png",
            "marquee": "./images/Lorem Mages-marquee.png", "video": "./videos/Lorem Mages-video.mp4"}
    with es.lock:
        es.mem["nes"][path].update(name="Lorem Mages", desc="Lorem ipsum.", **gone)
    entries, st = rommlib.plan_info(ListApi([rom]), "nes", [2], {"nes": {".zip"}}, cfg())
    assert len(entries) == 1 and st["complete"] == 0
    e = rommlib.gamelist_entry(rom, "nes", [path], cfg=cfg())
    assert rb_finalize.apply([e], None, {"nes": {".zip"}}, rb_finalize.EsApi(es.url), {}, cfg()) == []
    md = es.mem["nes"][path]
    for t in gone:
        assert os.path.isfile(os.path.normpath(os.path.join(sysdir, md[t]))), t
    entries, st = rommlib.plan_info(ListApi([rom]), "nes", [2], {"nes": {".zip"}}, cfg())
    assert entries == [] and st["complete"] == 1
    # gamelist.xml alone (ES can't be asked): a media path to a file that's gone doesn't count
    es.save()
    os.remove(os.path.normpath(os.path.join(sysdir, md["marquee"])))
    monkeypatch.setattr(rommlib, "ES_API", "http://127.0.0.1:9")
    assert len(rommlib.plan_info(ListApi([rom]), "nes", [2], {"nes": {".zip"}}, cfg())[0]) == 1


def test_the_version_line_fits_the_settings_panel_with_a_nightly_label(ui, rb, monkeypatch):  # noqa: F811
    from rbui.draw import measure
    monkeypatch.setattr(rommlib, "VERSION_LABEL", "0.10.0+n20261013.2109.95f4f7a")
    st = rb.SettingsScreen(ui)
    ui.push(st)
    calls = _spy(rb, monkeypatch, "text")
    ui.draw()
    line = [s for s, _, _ in calls if s.startswith("v0.10.0+n20261013") and " · " in s]     # About, not the hint bar
    assert line and measure(ui.fonts.small, line[0]) <= rb.SettingsScreen.PANEL.width - 56 - MARGIN
