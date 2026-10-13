"""The game page: a list's A opens it (picking still picks), A there is the game's verb (A, A
downloads; Play now when it's here), X its Actions, left/right the list's previous/next game;
the box's copy against RomM's, plays here, your RomM strip, screenshots through the shared
budgeted image store; every piece hidden when RomM has nothing for it."""
import os
import time

import pytest

import rommlib


@pytest.fixture
def app(isolated, rb, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    monkeypatch.setattr(rb.theme, "K", 1.0)
    monkeypatch.setattr(rb.draw, "_SCROLLS", {})
    a = rb.App(None, headless=True)
    a.platforms = [{"id": 7, "name": "PlayStation", "_system": "psx"}]
    a.platform_by_id = {7: a.platforms[0]}

    def bg(fn, done=None, err=None):            # background work, at once
        try:
            res = fn()
        except Exception as e:
            if err:
                err(e)
            return
        if done:
            done(res)
    a.bg = bg
    a.api.me = lambda max_age=300: {"id": 1, "ra_username": "me", "ra_progression": {"results": [
        {"rom_ra_id": 77, "max_possible": 40, "num_awarded": 12, "num_awarded_hardcore": 3}]}}
    return a


def _rom(i=1, **kw):
    r = {"id": i, "name": "Lorem %d" % i, "platform_id": 7, "fs_name": "Lorem %d.zip" % i,
         "fs_name_no_ext": "Lorem %d" % i, "fs_extension": "zip", "fs_size_bytes": 300 << 20}
    r.update(kw)
    return r


def _rich(i=1):
    """A game RomM knows a lot about: your status, rating, a note, RA, screenshots, a summary."""
    return _rom(i, ra_id=77, regions=["Japan"], summary="Lorem ipsum dolor sit amet. " * 20,
                metadatum={"developers": ["Lorem Ipsum Soft"], "genres": ["Puzzle"], "first_release_date": 915148800000},
                merged_ra_metadata={"achievements": [{"points": 5}] * 40},
                rom_user={"id": 3, "user_id": 1, "backlogged": True, "rating": 8},
                all_user_notes=[{"user_id": 1, "title": "t", "content": "Start on **hard**",
                                 "updated_at": "2026-10-01T00:00:00Z"}],
                merged_screenshots=["/assets/romm/resources/roms/7/%d/screenshots/%d.png" % (i, k) for k in range(4)])


def _draw(app, scr):
    app.targets = []
    scr.draw(app.canvas)
    return app.targets


def _install(app, rom, size=1 << 20):
    p = os.path.join(rommlib.ROMS_ROOT, "psx", rom["fs_name_no_ext"] + ".chd")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.truncate(size)
    app.library.mark(rom, "psx", [p])
    return p


def test_a_on_a_row_opens_the_page_and_picking_still_picks(rb, app):
    roms = [_rom(i) for i in range(1, 4)]
    gl = rb.GameListScreen(app, "Games", static=roms, why={2: "Because you played Lorem 1"})
    app.push(gl)
    gl.sel = 1
    gl.action("ok")
    page = app.stack[-1]
    assert isinstance(page, rb.GameScreen) and page.rom is roms[1] and page.source is gl
    assert page.why == "Because you played Lorem 1"
    assert page.crumbs() == ["More like this", "Lorem 2"]
    app.pop()
    gl.picking = True
    gl.action("ok")
    assert app.stack[-1] is gl and gl.pick_count() == 1
    assert ("A", "Open") in rb.GameListScreen(app, "Games", static=roms).hints()


def test_a_then_a_downloads(rb, app, monkeypatch):
    rom = _rom()
    got = []
    monkeypatch.setattr(app, "start_download", lambda r: got.append(r["id"]))
    page = rb.GameScreen(app, rom)
    app.push(page)
    assert page.hints()[0] == ("A", "Download")
    page.action("ok")
    assert isinstance(app.modal, rb.DownloadConfirm)
    app.modal.action("ok")
    assert got == [1] and app.modal is None


def test_on_the_box_a_plays_and_the_facts_compare_the_copies(rb, app, monkeypatch):
    rom = _rich()
    _install(app, rom, 257 << 20)
    played = []
    monkeypatch.setattr(app, "play_entry", lambda r, *a: ("Play now", "", lambda: played.append(r["id"])))
    monkeypatch.setattr(rommlib, "played_here", lambda lib, r, system, stats=None: {
        "playcount": 4, "last_played": time.time() - 3 * 86400, "gametime": 0, "es": True, "lastplayed": "x"})
    app.details[rom["id"]] = rom
    page = rb.GameScreen(app, rom)
    app.push(page)
    facts = dict(page.facts(page.record()))
    assert facts["On this box"] == "CHD · 257.0 MB · roms/psx"
    assert facts["In RomM"] == "ZIP · 300.0 MB" and "Size" not in facts
    (word, stars, note, play), ra = page.strip_rows(page.record())
    assert play == "Played here 3 days ago · 4 plays"
    assert page.hints()[0] == ("A", "Play now")
    page.action("ok")
    assert played == [1]
    _draw(app, page)


def test_play_now_says_why_it_cannot_start(rb, app, monkeypatch):
    rom = _rom()
    _install(app, rom)
    monkeypatch.setattr(app, "play_entry", lambda r, *a: ("Play now", "It's still downloading", None))
    page = rb.GameScreen(app, rom)
    page.action("ok")
    assert app.toasts[-1][0] == "It's still downloading" and app.modal is None


def test_your_romm_strip_reads_status_rating_note_and_ra(rb, app):
    rom = _rich()
    app.details[rom["id"]] = rom
    page = rb.GameScreen(app, rom)
    (word, stars, note, play), ra = page.strip_rows(page.record())
    assert (word, stars, note, play) == ("On your backlog", 4, "Start on hard", None)
    assert ra == (12, 40, 3)
    assert page.strip_height(page.record()) == 10 + 2 * 28
    assert page.shot_count(page.record()) == [0, 1, 2]
    _draw(app, page)


def test_missing_data_hides_each_piece(rb, app):
    rom = _rom()
    page = rb.GameScreen(app, rom)
    assert page.shot_count(rom) is None                     # RomM's record on its way: placeholders
    _draw(app, page)
    app.details[rom["id"]] = {}                              # couldn't be fetched
    rec = page.record()
    assert page.shot_count(rec) == [] and page.strip_height(rec) == 0
    app.api.me = lambda max_age=300: {}
    assert page.ra(rec) is None
    facts = [k for k, v in page.facts(rec) if v]
    assert facts == ["Size", "Format"]
    targets = _draw(app, page)
    assert any(tuple(r)[3] == rb.VERB_H for r, _ in targets)  # the verb is still there, tappable
    rom2 = _rom(2, platform_id=99)                           # no Batocera system
    page2 = rb.GameScreen(app, rom2)
    assert page2.hints()[0] == ("X", rb.pads.ACTIONS)
    _draw(app, page2)


def test_long_title_and_no_url_still_draw(rb, app):
    rom = _rom(name="Lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor " * 3)
    app.cfg["url"] = ""
    _draw(app, rb.GameScreen(app, rom))


def test_the_qr_code_never_carries_user_and_password(rb, app, monkeypatch):
    seen = []
    monkeypatch.setattr(rommlib, "qr_matrix", lambda s, *a: seen.append(s) or [[True]])
    app.cfg["url"] = "https://bob:pw@romm.example.com:8443/"
    _draw(app, rb.GameScreen(app, _rom(5)))
    assert seen == ["https://romm.example.com:8443/rom/5"]


def test_left_right_walk_the_list_it_came_from(rb, app):
    roms = [_rom(i) for i in range(1, 4)]
    gl = rb.GameListScreen(app, "Games", static=roms)
    app.push(gl)
    gl.sel = 2
    gl.action("ok")
    page = app.stack[-1]
    page.action("right")                                    # the last one: stays
    assert page.rom is roms[2]
    page.action("left")
    assert page.rom is roms[1] and gl.sel == 1
    assert ("LEFT RIGHT", "Next game") in page.hints()
    assert ("LEFT RIGHT", "Next game") not in rb.GameScreen(app, roms[0]).hints()


def test_actions_menu_is_the_games_menu(rb, app):
    rom = _rom()
    gl = rb.GameListScreen(app, "Games", static=[rom])
    app.push(gl)
    gl.action("ok")
    page = app.stack[-1]
    page.action("x")
    assert [e and e[0] for e in app.modal.entries] == ["More like this", "Game night"]
    app.modal = None
    _install(app, rom)
    page.action("x")
    assert [e and e[0] for e in app.modal.entries] == ["More like this", "Game night", None,
                                                       "Delete from this box", "Download again"]


def test_games_from_romm_rows_open_the_page(rb, app, monkeypatch):
    rec = _rom(5)
    monkeypatch.setattr(app, "detail", lambda r: rec if r["id"] == 5 else None)
    monkeypatch.setattr(rb.InstalledScreen, "load", lambda self: None)
    scr = rb.InstalledScreen(app)
    scr.items = [{"id": 5, "name": "Lorem 5", "system": "psx", "size": 1}, {"id": 6, "name": "X", "system": "psx",
                                                                              "size": 1}]
    app.push(scr)
    scr.action("ok")
    page = app.stack[-1]
    assert isinstance(page, rb.GameScreen) and page.rom is rec
    page.action("right")                                    # its record isn't here yet: stays
    assert page.rom is rec and scr.sel == 0


def test_screenshots_use_the_shared_image_store_per_shot(rb, app, monkeypatch):
    import pygame
    rom = _rich()
    asked = []
    loads = []

    class Loader:
        def drawn(self, key):
            pass

        def want(self, key, load, done, failed, dropped, wanted):
            loads.append((key, load, done))
    app.cover_loader = Loader()
    monkeypatch.setattr(app.api, "fetch_screenshot", lambda r, i: asked.append(i) or None)
    c = app.canvas
    assert rb.shot(c, app, rom, 0, pygame.Rect(0, 0, 270, 176)) is False      # loading: a placeholder
    assert rb.shot(c, app, rom, 1, pygame.Rect(0, 0, 270, 176)) is False
    assert [k for k, _, _ in loads] == [("shot", 1, 0), ("shot", 1, 1)]
    loads[0][1]()
    assert asked == [0]
    loads[0][2](None)                                       # RomM has no file for it
    assert app.art_missing(("shot", 1, 0)) and not app.art_missing(("shot", 1, 1))
    img = pygame.Surface((1920, 1080))
    loads[1][2](rb.shot_fit(img))
    kept = app.covers[("shot", 1, 1)]
    assert kept.get_size() == (400, 225)                     # scaled down once, in the covers' budget
    assert rb.shot(c, app, rom, 1, pygame.Rect(0, 0, 270, 176)) is True
    assert ("crop", (270, 176)) in app.scaled_covers[("shot", 1, 1)][1]
    app.details[rom["id"]] = rom
    assert rb.GameScreen(app, rom).shot_count(rom) == [1, 2]   # the missing one is left out


def test_time_ago_and_box_copy(rb, app):
    now = time.mktime((2026, 10, 13, 12, 0, 0, 0, 0, -1))
    day = 86400
    assert [rb.time_ago(now - d * day, now) for d in (0, 1, 3, 20, 90, 800)] == [
        "today", "yesterday", "3 days ago", "2 weeks ago", "3 months ago", "2 years ago"]
    assert rb.time_ago(None) == ""
    rom = _rom()
    assert rommlib.box_copy(app.library, rom, "psx") is None
    _install(app, rom, 3 << 20)
    got = rommlib.box_copy(app.library, rom, "psx")
    assert got["format"] == "CHD" and got["size"] == 3 << 20
