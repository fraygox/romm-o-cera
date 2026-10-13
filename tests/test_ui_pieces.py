"""Behaviour of UI pieces that were only ever drawn by the screenshot
harness: the search keyboard, the menu of a game already on the box, the
download prompt, queue_info; and the polish around them: a paused download's
cancel prompt, toasts that repeat, games Compress can't convert."""
import json
import os

import pytest

import rommlib


@pytest.fixture
def app(isolated, rb, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    app = rb.App(None, headless=True)

    def bg(fn, done=None, err=None):               # background work runs right away
        try:
            res = fn()
        except Exception as e:                     # noqa: BLE001 - handed to err, like App.bg
            if err:
                err(e)
            return
        if done:
            done(res)
    monkeypatch.setattr(app, "bg", bg)
    app.platforms = [{"id": 7, "name": "PlayStation", "_system": "psx"}]
    app.platform_by_id = {7: app.platforms[0]}
    return app


def _rom(rid=1, name="Lorem Quest (USA)", size=300 << 20, **kw):
    r = {"id": rid, "name": name, "fs_name": name + ".chd", "fs_name_no_ext": name, "fs_extension": "chd",
         "fs_size_bytes": size, "platform_id": 7}
    r.update(kw)
    return r


def _put(rel, data=b"x" * 1000):
    p = os.path.join(rommlib.ROMS_ROOT, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    return p


def _entries(menu):
    return [e for e in menu.entries if e]


# ------------------------------------------------------------ search keyboard

def test_search_keyboard_types_deletes_clears_and_wraps(app, rb):
    sc = rb.SearchScreen(app)
    app.push(sc)
    kb = sc.kb
    assert (kb.r, kb.c) == (1, 0) and kb.focus.label == "q"     # starts on Q
    sc.action("ok")
    assert sc.query == "q" and sc.pending_at       # letters type lower case; the live search waits a moment
    sc.action("up")
    sc.action("ok")
    assert sc.query == "q1"                        # the digit row
    sc.action("y")
    assert sc.query == "q1 "
    sc.action("x")
    assert sc.query == "q1"
    sc.action("left")
    assert kb.focus.label == "'"                   # left from the first key wraps, to the context column
    sc.action("up")
    assert kb.r == 4 and kb.focus.label == "!"     # up from the top wraps to the action row, the key below
    kb.c = [k.kind for k in kb.keys()[4]].index("clear")
    sc.preview, sc.preview_total = [_rom()], 1
    sc.action("ok")
    assert sc.query == "" and sc.preview == [] and sc.preview_total is None
    sc.type_text("a" * 70)
    assert len(sc.query) == 60                     # capped


def test_search_needs_two_characters_then_opens_the_results(app, rb):
    sc = rb.SearchScreen(app, platform=app.platform_by_id[7])
    app.push(sc)
    assert sc.crumbs() == ["Search", "PlayStation"]
    sc.type_text(" a ")
    sc.action("start")
    assert app.stack[-1] is sc and any("at least 2" in t[0] for t in app.toasts)
    sc.type_text("b")
    sc.kb.r, sc.kb.c = 4, [k.kind for k in sc.kb.keys()[4]].index("primary")
    sc.action("ok")
    gl = app.stack[-1]
    assert isinstance(gl, rb.GameListScreen) and gl.search == "a b" and gl.platform is sc.platform


def test_search_live_preview_ranks_and_drops_a_stale_answer(app, rb, monkeypatch):
    sc = rb.SearchScreen(app)
    app.push(sc)
    asked = []

    def roms(**kw):
        asked.append(kw)
        if kw["search"] == "lo":
            sc.query = "lor"                       # typed on while RomM answered: that answer is old
        return {"items": [_rom(1, "Lorem Quest"), _rom(2, "Lo")], "total": 2}
    monkeypatch.setattr(app.api, "roms", roms)
    sc.query, sc.pending_at = "lo", 1
    sc.live_preview()
    assert asked[0]["platform_ids"] == [7] and sc.preview == [] and sc.preview_total is None
    sc.pending_at = 1
    sc.live_preview()
    assert sc.preview_total == 2 and [r["name"] for r in sc.preview][0] in ("Lo", "Lorem Quest")
    sc.query, sc.pending_at = "l", 1               # one character: no request, no results
    sc.live_preview()
    assert len(asked) == 2 and sc.preview == []


def test_search_hints_follow_the_input(app, rb, monkeypatch):
    sc = rb.SearchScreen(app)
    monkeypatch.setitem(rb.INPUT_MODE, "kb", False)
    assert ("START", "Search") in sc.hints()
    monkeypatch.setitem(rb.INPUT_MODE, "kb", True)
    assert ("TYPE", "Letters") in sc.hints()


# ------------------------------------------------------------ the menu of a game on the box

def test_manage_menu_deletes_after_a_confirm_or_downloads_again(app, rb, monkeypatch):
    rom = _rom()
    p = _put("psx/Lorem Quest (USA).chd", b"x" * 4096)
    app.library.mark(rom, "psx", [p])
    m = rb.ManageMenu(app, rom)
    labels = [e[0] for e in _entries(m)]
    assert m.title == "Lorem Quest (USA)" and labels == ["Play now", "Delete from this box", "Download again"]
    assert "1 file" in _entries(m)[1][1]
    gone = []
    monkeypatch.setattr(app, "uninstall", lambda r, s, then=None: gone.append((r["id"], s)))
    app.modal = m
    m.action("down")
    m.action("ok")                                 # delete, but ask first
    assert isinstance(app.modal, rb.Confirm) and app.modal.danger and "Lorem Quest (USA).chd" in app.modal.body
    assert gone == []
    app.modal.action("ok")
    assert gone == [(1, "psx")] and app.modal is None
    app.modal = m = rb.ManageMenu(app, rom)
    m.action("down")
    m.action("down")
    m.action("ok")
    assert isinstance(app.modal, rb.DownloadConfirm) and "Already on this box" in app.modal.body


def test_manage_menu_with_no_files_cant_delete(app, rb):
    m = rb.ManageMenu(app, _rom(name="Nowhere (USA)"))
    play, first = _entries(m)[:2]
    assert play[0] == "Play now" and play[2] is None and play[1] == "It isn't on this box"
    assert first[0] == "Delete from this box" and first[2] is None
    assert m.sel == 2                              # the first row it can run


def test_manage_menu_of_a_disc_set_deletes_every_disc(app, rb, monkeypatch):
    discs = [_rom(10 + i, "Ipsum Saga (USA) (Disc %d)" % (i + 1)) for i in range(2)]
    group = {"id": "g1", "base": "Ipsum Saga (USA)", "title": "Ipsum Saga", "rom_ids": [10, 11]}
    for r in discs:
        app.library.mark(r, "psx", [_put("psx/%s.chd" % r["name"])], group=group)
    m = rb.ManageMenu(app, discs[0])
    assert m.title == "Ipsum Saga"
    assert [e[0] for e in _entries(m)] == ["Play now", "Delete all 2 discs", "Download this disc again"]
    assert _entries(m)[1][1] == "Frees %s" % rb.human_size(2000)
    calls = []
    monkeypatch.setattr(app, "uninstall_group", lambda g, s, t, then=None: calls.append((g["id"], s, t)))
    app.modal = m
    m.action("down")
    m.action("ok")
    assert app.modal.title == "Delete all 2 discs of Ipsum Saga?"
    app.modal.action("ok")
    assert calls == [("g1", "psx", "Ipsum Saga")]


# ------------------------------------------------------------ the download prompt

def test_download_confirm_queues_on_a(app, rb, monkeypatch):
    rom = _rom()
    app.details[rom["id"]] = {}
    got = []
    monkeypatch.setattr(app, "start_download", lambda r: got.append(r["id"]))
    app.modal = dc = rb.DownloadConfirm(app, rom)
    assert dc.title == "Lorem Quest (USA)" and dc.ok_label == "Download" and not dc.pending
    assert "300.0 MB to download" in dc.body and "Already" not in dc.body
    dc.action("back")
    assert app.modal is None and got == []
    app.modal = dc = rb.DownloadConfirm(app, rom)
    dc.action("ok")
    assert got == [1] and app.modal is None


def test_download_confirm_says_why_it_cant(app, rb, monkeypatch):
    dc = rb.DownloadConfirm(app, _rom(platform_id=99))
    assert "no Batocera system" in dc.body and dc.ok_label == "OK"
    rom = _rom()
    monkeypatch.setattr(app, "rom_status", lambda r: ("active", None))
    dc = rb.DownloadConfirm(app, rom)
    assert dc.body == "Already downloading." and dc.ok_label == "OK"


def test_download_confirm_mentions_whats_here(app, rb):
    rom = _rom()
    app.details[rom["id"]] = {}
    app.library.mark(rom, "psx", [_put("psx/Lorem Quest (USA).chd")])
    assert rb.DownloadConfirm(app, rom).body.startswith("Already on this box. Download again anyway?")
    other = _rom(2, "Lorem Quest (Europe)")
    app.details[2] = {}
    assert "You already have" in rb.DownloadConfirm(app, other).body


def test_download_confirm_of_a_disc_offers_the_set(app, rb, monkeypatch):
    rom = _rom(20, "Dolor Odyssey (USA) (Disc 1)")
    disc2 = _rom(21, "Dolor Odyssey (USA) (Disc 2)")
    app.details[20] = {}
    monkeypatch.setattr(rommlib, "find_disc_set", lambda api, r: ([(1, rom), (2, disc2)], [3]))
    sets = []
    monkeypatch.setattr(app, "download_set", lambda r, discs: sets.append([d["id"] for _, d in discs]))
    dc = rb.DownloadConfirm.__new__(rb.DownloadConfirm)
    app.modal = dc                                 # open while RomM is asked (the lookup answers at once here)
    dc.__init__(app, rom)
    m = app.modal
    assert isinstance(m, rb.ActionMenu)
    labels = [e[0] for e in _entries(m)]
    assert labels == ["Download all 2 discs", "Disc 3 not in RomM", "Only disc 1"]
    assert _entries(m)[1][2] is None               # a missing disc is a note, not an action
    m.action("ok")
    assert sets == [[20, 21]]


def test_download_confirm_waits_for_the_disc_lookup(app, rb, monkeypatch):
    rom = _rom(20, "Dolor Odyssey (USA) (Disc 1)")
    app.details[20] = {}
    monkeypatch.setattr(app, "bg", lambda fn, done=None, err=None: None)   # RomM hasn't answered yet
    got = []
    monkeypatch.setattr(app, "start_download", lambda r: got.append(r))
    app.modal = dc = rb.DownloadConfirm(app, rom)
    assert dc.pending and "Looking for the other discs" in dc.body
    dc.action("ok")
    assert app.modal is dc and got == []           # A does nothing until the answer is in
    dc._lookup_failed("300.0 MB to download.", RuntimeError("timed out"))
    assert not dc.pending and "Couldn't look up the other discs: timed out" in dc.body
    dc.action("ok")
    assert got == [rom]


# ------------------------------------------------------------ queue_info

def test_queue_info_appends_whole_lines_and_nothing_for_nothing(isolated):
    assert not os.path.exists(rommlib.DATA_DIR)
    rommlib.queue_info([])
    assert not os.path.exists(rommlib.GAMELIST_QUEUE)  # an empty plan doesn't even make the data folder
    a = {"system": "psx", "path": "./A.chd", "name": "A", "fill": True}
    b = {"system": "psx", "path": "./B.chd", "name": "B", "fill": True}
    rommlib.queue_info([a])
    rommlib.queue_info([b])
    with open(rommlib.GAMELIST_QUEUE) as f:
        lines = f.read().splitlines()
    assert [json.loads(ln) for ln in lines] == [a, b]


# ------------------------------------------------------------ cancelling a paused download

def _paused_job(rb, app, part=b""):
    J = rommlib.Job
    j = J(_rom(5, "Sed Sprite (Europe)", size=700 << 20, fs_name="Sed Sprite (Europe).zip"), "psx")
    j.state = J.PAUSED
    app.downloader.jobs.append(j)
    if part:
        _put("psx/Sed Sprite (Europe).zip.part", part)
    return j


def test_cancelling_a_paused_download_asks_and_says_how_much_goes(app, rb):
    J = rommlib.Job
    j = _paused_job(rb, app, part=b"p" * (3 << 20))
    sc = rb.DownloadsScreen(app)
    app.push(sc)
    sc.menu(j)
    cancel = next(e for e in _entries(app.modal) if e[0] == "Cancel download")
    cancel[2]()
    dlg = app.modal
    assert isinstance(dlg, rb.Confirm) and dlg.danger and dlg.title == "Cancel download?"
    assert "the 3.0 MB downloaded so far (of 700.0 MB) is deleted" in dlg.body and "Back keeps it" in dlg.body
    part = os.path.join(rommlib.ROMS_ROOT, "psx", "Sed Sprite (Europe).zip.part")
    dlg.action("back")
    assert j.state == J.PAUSED and os.path.exists(part)        # Back: nothing lost
    sc.menu(j)
    next(e for e in _entries(app.modal) if e[0] == "Cancel download")[2]()
    app.modal.action("ok")
    assert j.state == J.CANCELLED and not os.path.exists(part)


def test_cancel_prompt_with_nothing_downloaded_yet(app, rb):
    j = _paused_job(rb, app)
    sc = rb.DownloadsScreen(app)
    sc.confirm_cancel(j)
    assert "nothing is downloaded yet" in app.modal.body


def test_part_bytes_counts_a_folder_downloads_parts(app):
    j = _paused_job(None, app)
    j.rom["fs_name"] = "Sed Sprite (Europe)"
    _put("psx/Sed Sprite (Europe)/a.bin.part", b"a" * 100)
    _put("psx/Sed Sprite (Europe)/b.bin.part", b"b" * 50)
    _put("psx/Sed Sprite (Europe)/c.bin", b"c" * 999)          # finished files aren't partial
    assert app.downloader.part_bytes(j) == 150


# ------------------------------------------------------------ toasts that repeat

def test_the_same_toast_again_is_one_toast_with_a_count(app, rb, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(rb.time, "time", lambda: now[0])
    played = []
    monkeypatch.setattr(app.sfx, "play", lambda name, *a, **k: played.append(name))
    app.toast("Turn on RVZ in Settings first", rb.AMBER)
    now[0] += 2
    app.toast("Turn on RVZ in Settings first", rb.AMBER)
    assert len(app.toasts) == 1 and app.toasts[0][3] == 2 and app.toasts[0][2] == 1005.5   # timer renewed
    app.toast("Something else")
    app.toast("Turn on RVZ in Settings first", rb.AMBER)    # not the newest one: stacks as before
    assert [t[0] for t in app.toasts] == ["Turn on RVZ in Settings first", "Something else",
                                          "Turn on RVZ in Settings first"]
    app.toast("Something else", rb.RED)                     # same words, other colour: its own toast
    assert len(app.toasts) == 4
    now[0] += 10                                            # the last one has gone: a fresh one
    app.toast("Something else", rb.RED)
    assert app.toasts[-1][3] == 1


def test_a_burst_of_the_same_error_sounds_once(app, rb, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(rb.time, "time", lambda: now[0])
    played = []
    monkeypatch.setattr(app.sfx, "play", lambda name, *a, **k: played.append(name))
    for _ in range(5):                                      # every load of a screen failing at once
        app.toast("Can't reach RomM", rb.RED)
    assert played == ["deny"] and app.toasts[-1][3] == 5
    now[0] += 1
    app.toast("Can't reach RomM", rb.RED)                   # pressed again a moment later: heard again
    assert played == ["deny", "deny"]


def test_a_repeated_toast_draws_its_count(app, rb, monkeypatch):
    seen = []
    real = rb.marquee
    rb.patch(monkeypatch, "marquee", lambda c, font, s, *a, **k: (seen.append(s), real(c, font, s, *a, **k))[1])
    app.toast("Queued: Lorem")
    app.toast("Queued: Lorem")
    app.toast("Queued: Lorem")
    app.draw_toasts(app.canvas)
    assert seen == ["Queued: Lorem  ×3"]


# ------------------------------------------------------------ what Compress can't convert

def _encrypted_3ds(name):
    h = bytearray(0x400)
    h[0x100:0x104] = b"NCCH"                       # NCCH flags[7] without "no crypto": encrypted
    return _put("3ds/%s.3ds" % name, bytes(h))


def test_compress_lists_what_it_cant_convert_and_never_queues_it(app, rb, monkeypatch):
    monkeypatch.setattr(rommlib, "load_config", lambda: dict(rommlib.DEFAULT_CONFIG, z3ds=True))
    enc = _encrypted_3ds("Lorem Cart (Encrypted)")
    _put("psx/Ipsum (USA)/Ipsum (USA).cue", b'FILE "Ipsum (USA).bin" BINARY\n  TRACK 01 MODE2/2352\n')
    _put("psx/Ipsum (USA)/Ipsum (USA).bin", b"x" * 5000)
    sc = rb.CompressScreen(app)
    app.push(sc)
    sc.enter()
    assert [r["system"] for r in sc.rows] == ["psx", "3ds"] and sc.rows[1]["cant"]
    assert [c["system"] for c in sc.cands] == ["psx"]          # nothing to queue from 3ds
    sc.draw(app.canvas)
    sc.sel = 1
    assert sc.hints()[0] == ("A", "Why")
    sc.action("x")
    assert app.modal is None and "can't be converted" in app.toasts[-1][0]
    sc.action("ok")
    why = app.stack[-1]
    assert isinstance(why, rb.CantConvertScreen) and why.items == [(enc, "an encrypted dump (Azahar runs "
                                                                         "decrypted ones only)")]
    why.draw(app.canvas)
    why.action("ok")
    assert app.stack[-1] is why and app.modal is None


def test_compress_with_only_games_it_cant_convert(app, rb, monkeypatch):
    monkeypatch.setattr(rommlib, "load_config", lambda: dict(rommlib.DEFAULT_CONFIG, z3ds=True))
    _encrypted_3ds("Lorem Cart (Encrypted)")
    sc = rb.CompressScreen(app)
    app.push(sc)
    sc.enter()
    assert sc.cands == [] and [r["system"] for r in sc.rows] == ["3ds"]
    sc.draw(app.canvas)                             # "Nothing left to compress", then the row
    sc.action("ok")
    assert isinstance(app.stack[-1], rb.CantConvertScreen)


def test_a_failed_compress_scan_lists_nothing(app, rb, monkeypatch):
    sc = rb.CompressScreen(app)
    app.push(sc)
    sc._loaded([], [("3ds", "/r/3ds/x.3ds", "an encrypted dump")])
    assert sc.rows
    rb.patch(monkeypatch, "compress_scan", lambda skipped=None: 1 / 0)
    sc.enter()
    assert sc.rows == [] and sc.load_error
    sc.action("ok")
    assert app.stack[-1] is sc


def test_a_menu_taller_than_the_screen_scrolls_to_the_selected_row(isolated, rb, monkeypatch):
    """A game list's X menu with picks and BIOS on offer could run past the
    screen's bottom, its last rows unreachable by touch and unseen."""
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    app = rb.App(None, headless=True)
    picked = []
    entries = [("Entry %d" % i, "a line under it", lambda i=i: picked.append(i)) for i in range(25)]
    m = rb.ActionMenu(app, "A long menu", entries)

    def frame():
        app.targets = []
        m.draw(app.canvas)
        return [(r, fn) for r, fn in app.targets if fn]
    rows = frame()
    dialog = max((r for r, fn in app.targets if fn is None), key=lambda r: r.height)
    assert dialog.height <= rb.H - 40 and dialog.top >= 0
    assert len(rows) < 25                               # not everything fits
    for _ in range(24):
        m.action("down")
    rows = frame()
    assert all(dialog.top <= r.top and r.bottom <= dialog.bottom for r, _ in rows)
    rows[-1][1]()                                       # the last one drawn is the last entry, tappable
    assert picked == [24]
    m.sel = 0
    frame()
    assert m.off == 0                                    # back at the top


def test_a_key_tapped_after_a_tapped_match_types_instead_of_reopening_it(isolated, rb, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    app = rb.App(None, headless=True)
    app.bg = lambda fn, done=None, err=None: None
    app.push(rb.HomeScreen(app))
    sc = rb.SearchScreen(app)
    app.push(sc)
    sc.query = "lorem"
    sc.preview = [{"id": i, "name": "Lorem %d" % i, "platform_id": 1, "fs_size_bytes": 1} for i in range(5)]
    sc.preview_total = 5
    monkeypatch.setattr(app, "rom_status", lambda rom: ("remote", None))
    opened = []
    rb.patch(monkeypatch, "DownloadConfirm", lambda a, rom: opened.append(rom["id"]) or "dl")
    sc.tap_pick(1)                                  # a Top match tapped: its prompt opens
    assert opened == [1] and app.modal == "dl"
    app.modal = None                                # and is closed
    sc.kb.tap(1, 0)                                 # then the Q key
    assert opened == [1] and sc.query == "loremq"
