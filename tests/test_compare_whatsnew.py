"""Compare with RomM (inside a platform or a collection) and What's new: filters and their
counts, the comparison off the UI thread and stopped by B, "Get the newer one" through the
download-again dialog, What's new shown once after an update (never in the way, never when
offline) and the shared pieces they added (scroll_doc, scroll_nudge, day_label, Panel.art,
Panel.verb, Panel.doc)."""
import os
import time

import pytest

import rommlib


@pytest.fixture
def app(isolated, rb, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    monkeypatch.setattr(rb.theme, "K", 1.0)
    pygame.init()
    a = rb.App(None, headless=True)
    a.platforms = [{"id": 7, "name": "PlayStation", "display_name": "PlayStation", "_system": "psx"},
                   {"id": 8, "name": "PlayStation (JP)", "_system": "psx"}]
    a.platform_by_id = {p["id"]: p for p in a.platforms}
    a.ran = []

    def bg(fn, done=None, err=None):
        a.ran.append(fn)
        try:
            res = fn()
        except Exception as e:
            if err:
                err(e)
            return
        if done:
            done(res)
    monkeypatch.setattr(a, "bg", bg)
    a.cover = lambda rom: None
    a.covers = {}
    yield a
    rb.apply_theme("default")


def _item(status, name, rom_id=None, size=100, ext=".chd"):
    path = os.path.join(rommlib.ROMS_ROOT, "psx", name + ext)
    rom = {"id": rom_id, "name": name, "fs_name": name + ".zip", "fs_name_no_ext": name, "fs_extension": "zip",
           "fs_size_bytes": size * 2, "updated_at": "2026-10-09T10:00:00Z"} if rom_id else None
    return {"status": status, "label": rommlib.COMPARE_LABELS[status], "name": name + ext, "path": path,
            "paths": [path], "size": size, "rom": rom, "rom_ids": [rom_id] if rom_id else []}


def _result():
    items = [_item(rommlib.MATCHES, "Alpha", 1, ext=".zip"), _item(rommlib.COMPRESSED, "Beta", 2),
             _item(rommlib.NEWER, "Gamma", 3), _item(rommlib.NOT_IN_ROMM, "Delta"),
             _item(rommlib.NEWER, "Epsilon Renamed", 5)]
    counts = dict.fromkeys(rommlib.COMPARE_LABELS, 0)
    for it in items:
        counts[it["status"]] += 1
    return {"items": items, "counts": counts}


def _platform_screen(rb, app, monkeypatch, result=None):
    seen = {}

    def fake(api, library, system, pids, cancel=None):
        seen.update(system=system, pids=pids, cancel=cancel)
        return _result() if result is None else result
    monkeypatch.setattr(rommlib, "compare_platform", fake)
    scr = rb.CompareScreen(app, "PlayStation", platform=app.platforms[0])
    app.push(scr)
    return scr, seen


def test_compare_reads_every_romm_platform_of_the_system_off_the_ui_thread(rb, app, monkeypatch):
    scr, seen = _platform_screen(rb, app, monkeypatch)
    assert seen["system"] == "psx" and seen["pids"] == [7, 8]
    assert seen["cancel"] is scr.cancel and not scr.comparing
    assert app.ran                                       # through app.bg


def test_the_pills_count_matches_with_the_compressed_ones_and_filter_the_list(rb, app, monkeypatch):
    scr, _ = _platform_screen(rb, app, monkeypatch)
    assert [scr.count(st) for _, _, st in scr.filters()] == [5, 2, 2, 1]
    assert len(scr.listed()) == 5
    scr.action("y")
    assert scr.filt == "matches" and [it["name"] for it in scr.listed()] == ["Alpha.zip", "Beta.chd"]
    scr.action("y")
    assert {it["status"] for it in scr.listed()} == {rommlib.NEWER}
    scr.set_filter(3)
    assert [it["name"] for it in scr.listed()] == ["Delta.chd"]
    scr.action("y")
    assert scr.filt == "all"


def test_a_collection_has_no_not_in_romm_pill(rb, app, monkeypatch):
    got = {}

    def fake(api, library, col, system_of, cancel=None):
        got.update(col=col, system_of=system_of)
        return _result()
    monkeypatch.setattr(rommlib, "compare_collection", fake)
    scr = rb.CompareScreen(app, "Top 25", collection={"param": "collection_id", "id": 4, "full": "Top 25"})
    assert got["col"] == ("collection_id", 4) and got["system_of"] == app.system_for
    assert [k for k, _, _ in scr.filters()] == ["all", "matches", "newer"]
    assert scr.crumbs() == ["Collections", "Top 25", "Compare with RomM"]


def test_back_while_comparing_stops_it(rb, app, monkeypatch):
    monkeypatch.setattr(app, "bg", lambda fn, done=None, err=None: None)     # still running
    app.push(rb.Screen(app))
    scr = rb.CompareScreen(app, "PlayStation", platform=app.platforms[0])
    app.push(scr)
    assert scr.comparing and scr.hints() == [("B", "Stop")]
    scr.action("down")
    scr.action("back")
    assert scr.cancel.is_set() and app.stack[-1] is not scr


def test_a_late_result_of_a_stopped_comparison_is_dropped(rb, app, monkeypatch):
    later = []
    monkeypatch.setattr(app, "bg", lambda fn, done=None, err=None: later.append(done))
    scr = rb.CompareScreen(app, "PlayStation", platform=app.platforms[0])
    scr.start()                                          # Compare again: the first run is stale
    later[0](_result())
    assert scr.comparing
    later[1](_result())
    assert not scr.comparing


def test_get_the_newer_one_only_on_newer_rows_through_the_download_again_dialog(rb, app, monkeypatch):
    scr, _ = _platform_screen(rb, app, monkeypatch)
    assert ("A", "Get the newer one") not in scr.hints()            # Alpha: matches, no verb
    fetched = []
    monkeypatch.setattr(app.api, "rom", lambda rid: fetched.append(rid) or dict(
        next(it["rom"] for it in scr.listed() if it["rom_ids"] == [rid]), platform_id=7))
    scr.action("ok")
    assert fetched == [] and app.modal is None
    scr.set_filter(2)
    assert ("A", "Get the newer one") in scr.hints()
    scr.action("ok")                                     # same name: replaced in place by the download
    assert fetched[:1] == [3] and isinstance(app.modal, rb.DownloadConfirm)
    queued = []
    monkeypatch.setattr(app.downloader, "enqueue", lambda rom, system, replace=None, **k: queued.append(
        (rom["id"], system, replace)))
    app.modal.action("ok")
    assert queued == [(3, "psx", None)]


def test_get_the_newer_one_under_another_name_replaces_the_file_here(rb, app, monkeypatch):
    res = _result()
    res["items"][4]["rom"]["fs_name_no_ext"] = "Epsilon (Rev 1)"
    scr, _ = _platform_screen(rb, app, monkeypatch, res)
    monkeypatch.setattr(app.api, "rom", lambda rid: dict(res["items"][4]["rom"], platform_id=7))
    scr.set_filter(2)
    scr.sel = 1
    scr.action("ok")
    queued = []
    monkeypatch.setattr(app.downloader, "enqueue", lambda rom, system, replace=None, **k: queued.append(replace))
    app.modal.action("ok")
    assert queued == [res["items"][4]["paths"]]


def test_compare_draws_every_state_inside_its_boxes(rb, app, monkeypatch):
    scr, _ = _platform_screen(rb, app, monkeypatch)
    for filt in range(4):
        scr.set_filter(filt)
        for i in range(len(scr.listed()) or 1):
            scr.sel = i
            app.draw()
    scr.result = {"items": [], "counts": {}}
    app.draw()
    scr.result = None
    app.draw()


def test_compare_pages_ten_thousand_rows_quickly(rb, app, monkeypatch):
    items = [_item(rommlib.NEWER if i % 3 else rommlib.MATCHES, "Game %05d" % i, i + 1) for i in range(10000)]
    res = {"items": items, "counts": {rommlib.MATCHES: 3334, rommlib.NEWER: 6666}}
    scr, _ = _platform_screen(rb, app, monkeypatch, res)
    t = time.monotonic()
    for a in ("r1",) * 30 + ("y", "r1", "l1", "r2", "down"):
        scr.action(a)
        app.draw()
    assert time.monotonic() - t < 10
    assert scr.listed() is scr.listed()                  # the filtered rows are kept, not rebuilt each frame


def test_platform_and_collection_lists_offer_compare_in_their_actions(rb, app, monkeypatch):
    monkeypatch.setattr(app.api, "roms", lambda **k: {"items": [], "total": 0})
    gl = rb.GameListScreen(app, "PlayStation", platform=app.platforms[0])
    assert "Compare with RomM" in [e[0] for e in gl.list_actions()]
    gl = rb.GameListScreen(app, "Top 25", collection={"param": "collection_id", "id": 4, "name": "Top 25"})
    entry = next(e for e in gl.list_actions() if e[0] == "Compare with RomM")
    monkeypatch.setattr(rommlib, "compare_collection", lambda *a, **k: _result())
    entry[2]()
    assert isinstance(app.stack[-1], rb.CompareScreen) and app.stack[-1].collection["id"] == 4
    gl = rb.GameListScreen(app, "Search", search="lorem")
    assert "Compare with RomM" not in [e[0] for e in gl.list_actions()]


# ------------------------------------------------------------------ What's new

REL = {"tag": "v0.9.0", "name": "v0.9.0", "url": "https://example.com/releases/v0.9.0", "label": "0.9.0",
       "body": "## Features\n\n- One\n- Two, a longer line that wraps " + "word " * 40 + "\n\n## Fixes\n\nA fix."}


def test_whats_new_continue_remembers_the_version_and_goes_back(rb, app):
    app.push(rb.Screen(app))
    scr = rb.WhatsNewScreen(app, REL, "0.8.2")
    app.push(scr)
    app.draw()
    assert scr.lines[0] == ("h", "Features") and scr.hints()[:2] == [("A", "Continue"), ("X", "Full notes")]
    scr.action("ok")
    assert app.stack[-1] is not scr and rommlib.load_config()["notes_seen"] == rommlib.VERSION_LABEL


def test_whats_new_full_notes_show_the_release_link_as_a_qr_code(rb, app):
    scr = rb.WhatsNewScreen(app, REL, None)
    app.push(scr)
    scr.action("x")
    top = app.stack[-1]
    assert isinstance(top, rb.DocsScreen) and top.url == REL["url"]
    app.draw()


def test_whats_new_reads_the_notes_itself_from_settings_and_says_so_offline(rb, app, monkeypatch):
    monkeypatch.setattr(rommlib, "release_notes", lambda label=None: None)
    scr = rb.WhatsNewScreen(app)
    app.push(scr)
    assert scr.failed and scr.lines == [] and scr.url.startswith(rommlib.PROJECT_URL)
    app.draw()
    st = rb.SettingsScreen(app)
    app.push(st)
    st.sel = next(i for i, it in enumerate(st.items()) if it[0] == "whatsnew")
    st.action("ok")
    assert isinstance(app.stack[-1], rb.WhatsNewScreen)


def test_whats_new_waits_for_home_and_never_covers_a_dialog(rb, app):
    home = rb.Screen(app)
    app.push(home)
    other = rb.Screen(app)
    app.push(other)
    app._notes_found((REL, "0.8.2"))
    assert app.stack[-1] is other                        # not over what the user opened
    app.modal = object()
    app.pop()
    assert app.stack == [home] and app.notes_pending      # a dialog is open: still waiting
    app.modal = None
    app.show_notes()
    assert isinstance(app.stack[-1], rb.WhatsNewScreen) and app.notes_pending is None


def test_notes_at_start_is_none_offline_or_when_already_seen(rb, app, monkeypatch):
    monkeypatch.setattr(rommlib, "whats_new", lambda cfg: None)
    assert rb.notes_at_start(app.cfg) is None
    monkeypatch.setattr(rommlib, "whats_new", lambda cfg: REL)
    monkeypatch.setattr(rommlib, "backup_version", lambda backup=None: "0.8.2")
    assert rb.notes_at_start(app.cfg) == (REL, "0.8.2")


def test_previous_version_falls_back_to_the_last_notes_seen(rb, monkeypatch):
    monkeypatch.setattr(rommlib, "backup_version", lambda backup=None: None)
    assert rb.previous_version({"notes_seen": "0.8.1"}) == "0.8.1"
    assert rb.previous_version({"notes_seen": rommlib.VERSION_LABEL}) is None


# ------------------------------------------------------------------ shared pieces

@pytest.fixture
def fonts(rb, monkeypatch):
    import pygame
    pygame.init()
    monkeypatch.setattr(rb.theme, "K", 1.0)
    f = rb.Fonts()
    monkeypatch.setitem(rb.draw.FONTS, "f", f)
    monkeypatch.setattr(rb.draw, "_SCROLLS", {})
    return f


def _canvas(rb):
    import pygame
    return pygame.Surface((rb.S(rb.W), rb.S(rb.H)))


def test_doc_layout_indents_bullets_and_spaces_headings(rb, fonts):
    rows, total = rb.doc_layout(fonts, [("h", "Head"), ("li", "item " * 30), ("p", "para"), ("h", "Two")], 300)
    heads = [r for r in rows if r[3] in ("Head", "Two")]
    assert heads[0][0] is fonts.h3 and heads[0][4] == 0
    bullet = [r for r in rows if r[3] == "•"]
    assert len(bullet) == 1 and all(r[2] == 22 for r in rows if r[3].startswith("item"))
    assert len([r for r in rows if r[3].startswith("item")]) > 1          # wrapped, indented under the bullet
    assert heads[1][4] > [r for r in rows if r[3] == "para"][0][4] + 14 and total > heads[1][4]


def test_scroll_doc_scrolls_when_long_and_scroll_nudge_moves_it(rb, fonts, monkeypatch):
    c = _canvas(rb)
    lines = [("p", "line %d" % i) for i in range(40)]
    monkeypatch.setattr(rb.draw, "NOW", 100.0)
    r = rb.scroll_doc(c, fonts, lines, (0, 0, 300, 100), slot="doc")
    assert r.height == 100
    st = rb.draw._SCROLLS["doc"]
    rb.scroll_nudge("doc", 1.0)                          # past the pause at the top, then a second on
    assert st[2] - st[1] == pytest.approx(rb.draw.SCROLL_WAIT + 1.0)
    rb.scroll_nudge("doc", -10.0)
    assert st[2] - st[1] == 0
    rb.scroll_nudge("nothing", 1.0)


def test_day_label(rb):
    now = time.mktime((2026, 10, 13, 15, 0, 0, 0, 0, -1))
    assert rb.day_label(now - 3600, now) == "today"
    assert rb.day_label(now - 86400, now) == "yesterday"
    assert rb.day_label(time.mktime((2026, 8, 12, 9, 0, 0, 0, 0, -1)), now) == "12 Aug"
    assert rb.day_label(time.mktime((2025, 8, 12, 9, 0, 0, 0, 0, -1)), now) == "12 Aug 2025"
    assert rb.day_label(None) == ""


def test_panel_art_verb_status_and_doc(rb, app, fonts):
    c = _canvas(rb)
    p = rb.Panel(c, fonts, (800, 100, 400, 500), app)
    vr = p.verb("Get the newer one", detail="1 GB", fn=lambda: None)
    assert vr.bottom == 600 - p.PAD and p.bottom == vr.top - 16
    x0, w0 = p.x, p.w
    p.art(None, name="No cover")
    assert p.x > x0 and p.w < w0
    p.title("A name", small=True)
    p.status("Newer in RomM", rb.theme.BLUE)
    p.under_art()
    assert (p.x, p.w) == (x0, w0) and p.y >= 100 + p.PAD + 138
    assert p.doc([("p", "word " * 400)]) is True
    assert p.doc([]) is False
