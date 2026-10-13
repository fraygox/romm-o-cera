"""List screens: selection, paging, the scroll that follows the selection,
the visible window's row hit rects and the touch rule (first tap selects,
second opens). The expected values are today's behaviour, hard-coded, so a
shared list base can replace each screen's own copy without changing it."""
import pytest


@pytest.fixture
def app(isolated, rb, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    app = rb.App(None, headless=True)
    app.feed = None

    def bg(fn, done=None, err=None):               # background loads hand back app.feed
        if done and app.feed is not None:
            done(app.feed)
    monkeypatch.setattr(app, "bg", bg)
    return app


def _leftovers(rb, app, n):
    return [{"path": "/nowhere/f%02d.part" % i, "kind": "Partial file", "size": i + 1} for i in range(n)]


def _installed(rb, app, n):
    return [{"id": i, "name": "Game %02d" % i, "system": "gba", "when": "2026-01-01", "size": i + 1}
            for i in range(n)]


def _cands(n, systems=False):
    return [{"system": "s%02d" % i if systems else "psx", "root": "/r/psx/G%02d.cue" % i,
             "files": ["/r/psx/G%02d.cue" % i], "name": "G%02d" % i, "mode": "createcd", "label": "CHD",
             "size": (n - i) << 20, "enabled": True, "setting": "x", "xiso": False, "cut": 0} for i in range(n)]


def _bios(rb, app, n):
    return [{"name": "System %02d" % i, "system": "s%02d" % i, "missing": [], "offers": [], "required": ["a.bin"],
             "fetched": {}, "here": True} for i in range(n)]


def _games(rb, app, n):
    return [{"path": "/nowhere/gba/G%02d.gba" % i, "name": "Game %02d" % i, "system": "gba", "size": (n - i) << 20,
             "lastplayed": None, "playcount": 0} for i in range(n)]


def _unseen(rb, app, n):
    return [{"path": "%s/gba/F%02d.7z" % (rb.rommlib.ROMS_ROOT, i), "system": "gba", "kind": "archive",
             "why": "an archive gba doesn't take", "size": i + 1} for i in range(n)]


def _conversions(n):
    return [{"kind": "queued", "name": "%02d.json" % i, "title": "Disc %02d" % i, "discs": 1, "size": 1 << 20,
             "mode": "createcd"} for i in range(n)]


def _findings(n):
    return [{"severity": "damaged", "system": "gba", "path": "/nowhere/gba/G%02d.gba" % i, "problem": "Bad zip",
             "rom": None} for i in range(n)]


def _make(rb, app, name, n, monkeypatch=None):
    """(screen with n rows, reload(m): the same screen re-read with m rows)."""
    if name == "LeftoversScreen":
        app.feed = _leftovers(rb, app, n)
        scr = rb.LeftoversScreen(app)
        return scr, lambda m: scr._loaded(_leftovers(rb, app, m))
    if name == "InstalledScreen":
        app.feed = _installed(rb, app, n)
        app.detail = lambda rom: {}                   # RomM hasn't got them: the panel from what's on the box
        scr = rb.InstalledScreen(app)
        return scr, lambda m: scr._loaded(_installed(rb, app, m))
    if name == "CompressSystemScreen":
        scr = rb.CompressSystemScreen(app, "psx")
        scr._loaded(_cands(n))
        return scr, lambda m: scr._loaded(_cands(m))
    if name == "CompressScreen":
        scr = rb.CompressScreen(app)
        scr._loaded(_cands(n, systems=True))
        return scr, lambda m: scr._loaded(_cands(m, systems=True))
    if name == "BiosScreen":
        app.feed = _bios(rb, app, n)
        scr = rb.BiosScreen(app)

        def reload(m):
            app.feed = _bios(rb, app, m)
            scr.load()
        return scr, reload
    if name == "FreeUpScreen":
        app.feed = _games(rb, app, n)
        scr = rb.FreeUpScreen(app)
        return scr, lambda m: scr._loaded(_games(rb, app, m))
    if name == "UnseenScreen":
        app.feed = _unseen(rb, app, n)
        scr = rb.UnseenScreen(app)
        return scr, lambda m: scr._loaded(_unseen(rb, app, m))
    if name == "ConversionsScreen":
        have = [_conversions(n)]
        rb.patch(monkeypatch, "conversion_rows", lambda: have[0])
        scr = rb.ConversionsScreen(app)

        def reload(m):
            have[0] = _conversions(m)
            scr.refresh()
        return scr, reload
    if name == "AuditScreen":                         # one check, no reload
        app.feed = (_findings(n), {"checked": 0})
        return rb.AuditScreen(app), None
    raise AssertionError(name)


# name: (ROWS, first row's y, ROW_H, gap below each row box)
GEOMETRY = {
    "LeftoversScreen": (9, 124, 56, 4),
    "InstalledScreen": (10, 124, 54, 4),
    "CompressSystemScreen": (9, 124, 58, 4),
    "CompressScreen": (9, 124, 58, 4),
    "BiosScreen": (8, 124, 62, 6),
    "FreeUpScreen": (9, 124, 56, 4),
    "UnseenScreen": (9, 124, 56, 4),
    "ConversionsScreen": (8, 124, 60, 6),             # no ROWS today: pages by 8, (H - 60 - 124) // 60 shown
    "AuditScreen": (8, 124, 62, 6),
}
# the list's x and width where it isn't the whole screen (a game panel beside it)
XW = {"InstalledScreen": (32, 676)}


def _script(rows):
    return (["down"] * (rows + 1) + ["r1"] * 3 + ["l1"] + ["up"] * 3 + ["r1"] * 4 + ["down"] +
            ["l1"] * 5 + ["up"])


# (sel, scroll) after each step of _script, on 3 * ROWS + 2 rows
TRACE = {
    9: [(1, 0), (2, 0), (3, 0), (4, 0), (5, 0), (6, 0), (7, 0), (8, 0), (9, 1), (10, 2), (19, 11), (28, 20),
        (28, 20), (19, 19), (18, 18), (17, 17), (16, 16), (25, 17), (28, 20), (28, 20), (28, 20), (28, 20),
        (19, 19), (10, 10), (1, 1), (0, 0), (0, 0), (0, 0)],
    10: [(1, 0), (2, 0), (3, 0), (4, 0), (5, 0), (6, 0), (7, 0), (8, 0), (9, 0), (10, 1), (11, 2), (21, 12),
         (31, 22), (31, 22), (21, 21), (20, 20), (19, 19), (18, 18), (28, 19), (31, 22), (31, 22), (31, 22),
         (31, 22), (21, 21), (11, 11), (1, 1), (0, 0), (0, 0), (0, 0)],
    8: [(1, 0), (2, 0), (3, 0), (4, 0), (5, 0), (6, 0), (7, 0), (8, 1), (9, 2), (17, 10), (25, 18), (25, 18),
        (17, 17), (16, 16), (15, 15), (14, 14), (22, 15), (25, 18), (25, 18), (25, 18), (25, 18), (17, 17),
        (9, 9), (1, 1), (0, 0), (0, 0), (0, 0)],
}


# CreditsScreen on 20 rows: 6 shown, L/R by 5
TRACE_CREDITS = [(1, 0), (2, 0), (3, 0), (4, 0), (5, 0), (6, 1), (7, 2), (12, 7), (17, 12), (19, 14), (14, 14),
                 (13, 13), (12, 12), (11, 11), (6, 6), (1, 1), (0, 0), (0, 0)]


def _frame(app, scr):
    app.targets = []
    scr.draw(app.canvas)
    return [tuple(r) for r, _ in app.targets]


@pytest.mark.parametrize("name", sorted(GEOMETRY))
def test_moves_pages_and_scroll_follow(app, rb, name, monkeypatch):
    rows, y0, rh, gap = GEOMETRY[name]
    x, w = XW.get(name, (48, rb.W - 96))
    n = 3 * rows + 2
    scr, _ = _make(rb, app, name, n, monkeypatch)
    app.push(scr)
    assert getattr(scr, "ROWS", rows) == rows and scr.ROW_H == rh
    assert _frame(app, scr) == [(x, y0 + k * rh, w, rh - gap) for k in range(rows)]
    seen = []
    for a in _script(rows):
        scr.action(a)
        rects = _frame(app, scr)
        seen.append((scr.sel, scr.scroll))
        assert 0 <= scr.sel <= n - 1
        assert scr.scroll <= scr.sel < scr.scroll + rows
        vis = min(rows, n - scr.scroll)
        assert rects == [(x, y0 + k * rh, w, rh - gap) for k in range(vis)]
    assert seen == TRACE[rows]


@pytest.mark.parametrize("name", sorted(set(GEOMETRY) - {"FreeUpScreen"}))     # Free up space: a tap picks
def test_first_tap_selects_second_opens(app, rb, name, monkeypatch):
    rows, y0, rh, gap = GEOMETRY[name]
    scr, _ = _make(rb, app, name, 2 * rows, monkeypatch)
    app.push(scr)
    for _ in range(rows + 3):
        scr.action("down")
    _frame(app, scr)
    assert (scr.sel, scr.scroll) == (rows + 3, 4)
    opened = []
    monkeypatch.setattr(scr, "action", opened.append)
    pt = (rb.W // 2, y0 + 2 * rh + rh // 2)           # the third visible row: row 6
    app.tap(pt)
    assert scr.sel == 6 and opened == []
    _frame(app, scr)
    assert scr.scroll == 4
    app.tap(pt)
    assert scr.sel == 6 and opened == ["ok"]


@pytest.mark.parametrize("name", sorted(set(GEOMETRY) - {"AuditScreen"}))
def test_reload_clamps_the_selection(app, rb, name, monkeypatch):
    """A shorter list after a reload: sel moves to the new last row; Installed
    and BIOS clamp scroll at once, the rest leave it to the next draw."""
    rows = GEOMETRY[name][0]
    scr, reload = _make(rb, app, name, 3 * rows, monkeypatch)
    app.push(scr)
    for _ in range(3):
        scr.action("r1")
    _frame(app, scr)
    assert (scr.sel, scr.scroll) == (3 * rows - 1, 2 * rows)
    reload(5)
    keeps = name not in ("InstalledScreen", "BiosScreen")
    assert (scr.sel, scr.scroll) == (4, 2 * rows if keeps else 4)
    _frame(app, scr)
    assert (scr.sel, scr.scroll) == (4, 4)
    reload(0)
    assert scr.sel == 0
    for a in ("down", "up", "r1", "l1"):
        scr.action(a)
        assert scr.sel == 0
    _frame(app, scr)
    assert app.targets == [] and app.stack[-1] is scr


def test_compress_lists_rows_not_cands(app, rb):
    """Compress moves through its per-system rows, not the games under them."""
    scr = rb.CompressScreen(app)
    cands = _cands(4, systems=True) + [dict(c, root=c["root"] + "x") for c in _cands(4, systems=True)]
    scr._loaded(cands)
    assert len(scr.rows) == 4 and len(scr.cands) == 8
    for _ in range(6):
        scr.action("down")
    assert scr.sel == 3
    scr.action("r1")
    assert scr.sel == 3
    scr.action("l1")
    assert scr.sel == 0


def test_list_base_moves_pages_and_offsets_taps_past_tiles(app, rb):
    """The shared base on its own: STEP pages instead of ROWS, `base` tiles
    come before the rows (sel counts them; taps land at i + base), and a
    screen without `items` lists nothing rather than failing."""
    class Tiles(rb.ListScreen):
        ROWS, ROW_H, STEP, base = 4, 50, 3, 2

    scr = Tiles(app)
    assert (scr.sel, scr.scroll) == (0, 0) and scr.listed() == []
    assert not scr.nav("down") and scr.sel == 0 and not scr.nav("ok")
    scr.items = list("abcdefghij")
    scr.sel = 2
    assert scr.nav("r1") and scr.sel == 5
    scr.nav("r1"), scr.nav("r1"), scr.nav("r1")
    assert scr.sel == 11                                  # base + n - 1
    scr.nav("up")
    app.targets = []
    rows = list(scr.row_rects(100, x=10, w=300, gap=6))
    assert [i for i, _, _ in rows] == [5, 6, 7, 8] and scr.scroll == 5
    assert [tuple(r) for _, r, _ in rows] == [(10, 100 + k * 50, 300, 44) for k in range(4)]
    assert [on for _, _, on in rows] == [False, False, False, True]
    tapped = []
    scr.tap_row = tapped.append
    app.targets[0][1]()
    assert tapped == [7]                                  # row 5, after the 2 tiles
    scr.sel = 0                                            # on a tile: the window stays at the top row
    assert list(scr.visible()) == [0, 1, 2, 3] and scr.scroll == 0
    for _ in range(12):
        scr.nav("l1")
    assert scr.sel == 2
    scr.items, scr.sel, scr.scroll = list("abc"), 11, 6
    scr.clamp(scroll=True)
    assert (scr.sel, scr.scroll) == (4, 4)
    scr.items = []
    scr.clamp()
    assert scr.sel == 1                                   # max(0, base - 1): nothing below the tiles


def test_credits_pages_by_five_shows_six_and_a_tap_only_selects(app, rb, monkeypatch):
    """Credits: L/R page by 5 while 6 rows show; a tap selects and never opens."""
    monkeypatch.setattr(rb.about, "CREDITS", [("Name %02d" % i, "what", "MIT", "https://x/%d" % i,
                                               "" if i % 2 else "a note") for i in range(20)])
    scr = rb.CreditsScreen(app)
    app.push(scr)
    n, rh = len(rb.CREDITS), 96
    assert _frame(app, scr) == [(48, 84 + k * rh, rb.W - 96, rh - 8) for k in range(min(6, n))]
    seen = []
    for a in ["down"] * 7 + ["r1"] * 3 + ["l1"] + ["up"] * 3 + ["l1"] * 3 + ["up"]:
        scr.action(a)
        rects = _frame(app, scr)
        seen.append((scr.sel, scr.scroll))
        assert rects == [(48, 84 + k * rh, rb.W - 96, rh - 8) for k in range(min(6, n - scr.scroll))]
    assert seen == TRACE_CREDITS
    opened = []
    monkeypatch.setattr(scr, "action", opened.append)
    pt = (rb.W // 2, 84 + 2 * rh + rh // 2)
    app.tap(pt)
    app.tap(pt)
    assert scr.sel == scr.scroll + 2 and opened == []


def test_audit_moves_only_once_the_check_is_done(app, rb):
    """While the check runs only B does anything (it stops the check)."""
    app.feed = None
    app.push(rb.Screen(app))
    scr = rb.AuditScreen(app)
    app.push(scr)
    for a in ("down", "r1", "ok", "y"):
        scr.action(a)
    assert (scr.sel, scr.scroll) == (0, 0) and app.stack[-1] is scr and not scr.cancel.is_set()
    scr.action("back")
    assert scr.cancel.is_set() and app.stack[-1] is not scr


# -- tiles above a list (Space, Check), Downloads, Collections, game lists ----------------

def _tile_script(rows):
    return (["left", "left", "right", "l1", "r1", "up", "down"] + ["down"] * (rows + 1) + ["r1"] * 3 +
            ["left", "l1"] + ["up"] * 3 + ["l1"] * 4 + ["up", "up", "right", "down", "up"])


def _systems(n):
    return [{"system": "s%02d" % i, "ours": n - i, "other": 1, "media": 0, "total": n - i + 1} for i in range(n)]


def _space(rb, app, n):
    scr = rb.SpaceScreen(app)
    app.push(scr)
    scr.leftovers = []
    scr._loaded(_systems(n))
    return scr


def _check(rb, app, n, monkeypatch):
    found = _findings(n)
    monkeypatch.setattr(rb.rommlib, "load_audit", lambda: {"findings": found, "stats": {"checked": n},
                                                           "time": 0, "deep": False})
    scr = rb.CheckScreen(app)
    app.push(scr)
    return scr


# name: (tiles, ROWS, first row's y, ROW_H, gap)
TILED = {"SpaceScreen": (2, 6, 326, 54, 4), "CheckScreen": (3, 7, 212, 62, 6)}

# (sel, scroll) after each step of _tile_script, on 3 * ROWS + 2 rows
TRACE_TILED = {
    "SpaceScreen": [(1, 0), (0, 0), (1, 0), (1, 0), (1, 0), (1, 0), (2, 0), (3, 0), (4, 0), (5, 0), (6, 0),
                    (7, 0), (8, 1), (9, 2), (15, 8), (21, 14), (21, 14), (15, 13), (9, 7), (8, 6),
                    (7, 5), (6, 4), (2, 0), (2, 0), (2, 0), (2, 0), (0, 0), (0, 0), (1, 0), (2, 0), (0, 0)],
    "CheckScreen": [(0, 0), (0, 0), (1, 0), (1, 0), (1, 0), (1, 0), (3, 0), (4, 0), (5, 0), (6, 0), (7, 0),
                    (8, 0), (9, 0), (10, 1), (11, 2), (18, 9), (25, 16), (25, 16), (18, 15), (11, 8),
                    (10, 7), (9, 6), (8, 5), (3, 0), (3, 0), (3, 0), (3, 0), (0, 0), (0, 0), (1, 0),
                    (3, 0), (0, 0)],
}


@pytest.mark.parametrize("name", sorted(TILED))
def test_tiles_then_rows_move_page_and_scroll(app, rb, name, monkeypatch):
    """Left/right among the tiles (Space's two swap, Check's three stop at the
    ends), down into the rows, up from the first row to tile 0, L/R (and
    left/right in the rows) page only in the rows; rows tap at i + tiles,
    the first tap selecting."""
    tiles, rows, y0, rh, gap = TILED[name]
    n = 3 * rows + 2
    scr = _space(rb, app, n) if name == "SpaceScreen" else _check(rb, app, n, monkeypatch)
    rects = _frame(app, scr)
    assert rects[tiles:] == [(48, y0 + k * rh, rb.W - 96, rh - gap) for k in range(rows)]
    seen = []
    for a in _tile_script(rows):
        scr.action(a)
        rects = _frame(app, scr)
        seen.append((scr.sel, scr.scroll))
        assert 0 <= scr.sel <= tiles + n - 1
        if scr.sel >= tiles:
            assert scr.scroll <= scr.sel - tiles < scr.scroll + rows
        vis = min(rows, n - scr.scroll)
        assert rects[tiles:] == [(48, y0 + k * rh, rb.W - 96, rh - gap) for k in range(vis)]
    assert seen == TRACE_TILED[name]
    opened = []
    monkeypatch.setattr(scr, "action", opened.append)
    pt = (rb.W // 2, y0 + 2 * rh + rh // 2)
    app.tap(pt)
    assert scr.sel == tiles + scr.scroll + 2 and opened == []
    app.tap(pt)
    assert opened == ["ok"]
    app.tap((rects[1][0] + 5, rects[1][1] + 5))
    assert scr.sel == 1 and opened == ["ok", "ok"]          # a tile opens at once


@pytest.mark.parametrize("name", sorted(TILED))
def test_tiles_reload_and_empty_rows(app, rb, name, monkeypatch):
    """A shorter list after a reload: sel onto its last row, or onto the last
    tile when nothing's left; then only the tiles move."""
    tiles, rows = TILED[name][:2]
    if name == "SpaceScreen":
        scr = _space(rb, app, 3 * rows)
        reload = lambda m: scr._loaded(_systems(m))
    else:
        scr = _check(rb, app, 3 * rows, monkeypatch)

        def reload(m):
            found = _findings(m)
            monkeypatch.setattr(rb.rommlib, "load_audit", lambda: {"findings": found, "stats": {}, "time": 0})
            scr.enter()
    for a in ["down"] + ["r1"] * 4:
        scr.action(a)
    _frame(app, scr)
    assert scr.sel == tiles + 3 * rows - 1
    reload(5)
    assert scr.sel == tiles + 4
    reload(0)
    assert scr.sel == tiles - 1
    for a in ("down", "r1", "l1"):
        scr.action(a)
        assert scr.sel == tiles - 1
    scr.action("up")
    assert scr.sel == tiles - 1                              # on a tile: up stays


def _jobs(rb, app, n, paused=0):
    J = rb.rommlib.Job
    out = []
    for i in range(n):
        j = J({"id": i, "name": "Game %02d" % i, "fs_name": "G%02d.gba" % i, "fs_size_bytes": 1 << 20}, "gba")
        j.state = J.PAUSED if i < paused else (J.DONE if i % 3 else J.FAILED)
        out.append(j)
    app.downloader.jobs[:] = out
    return out


# (sel, scroll) after each step of _script(7) on 23 jobs, no banners (7 rows) / Pause all on (fewer rows)
TRACE_DOWNLOADS = {
    False: [(1, 0), (2, 0), (3, 0), (4, 0), (5, 0), (6, 0), (7, 1), (8, 2), (15, 9), (22, 16), (22, 16),
            (15, 15), (14, 14), (13, 13), (12, 12), (19, 13), (22, 16), (22, 16), (22, 16), (22, 16), (15, 15),
            (8, 8), (1, 1), (0, 0), (0, 0), (0, 0)],
    True: [(1, 0), (2, 0), (3, 0), (4, 0), (5, 0), (6, 1), (7, 2), (8, 3), (15, 10), (22, 17), (22, 17),
           (15, 15), (14, 14), (13, 13), (12, 12), (19, 14), (22, 17), (22, 17), (22, 17), (22, 17), (15, 15),
           (8, 8), (1, 1), (0, 0), (0, 0), (0, 0)],
}


@pytest.mark.parametrize("hold", [False, True])
def test_downloads_move_page_and_scroll(app, rb, hold, monkeypatch):
    """Downloads page by 7 whatever the banners leave room for; the rows start
    below the banners."""
    n = 23
    jobs = _jobs(rb, app, n, paused=4 if hold else 0)
    app.downloader.hold = hold
    scr = rb.DownloadsScreen(app)
    app.push(scr)
    rects = _frame(app, scr)
    y0 = rects[0][1]
    rows = len(rects)
    assert (y0, rows) == ((140, 6) if hold else (84, 7))
    assert rects == [(48, y0 + k * 76, rb.W - 96, 70) for k in range(rows)]
    seen = []
    for a in _script(7):
        scr.action(a)
        rects = _frame(app, scr)
        seen.append((scr.sel, scr.scroll))
        assert scr.sel_job is scr.view()[scr.sel]
        assert scr.scroll <= scr.sel < scr.scroll + rows
        assert rects == [(48, y0 + k * 76, rb.W - 96, 70) for k in range(min(rows, n - scr.scroll))]
    assert seen == TRACE_DOWNLOADS[hold] and len(jobs) == n


def test_downloads_tap_selects_the_job_and_the_highlight_follows_it(app, rb, monkeypatch):
    """A tap selects a download (and remembers it: the highlight stays on it
    when the queue reorders); a tap on the selected one opens it."""
    jobs = _jobs(rb, app, 10)
    scr = rb.DownloadsScreen(app)
    app.push(scr)
    _frame(app, scr)
    pt = (rb.W // 2, 84 + 3 * 76 + 30)
    opened = []
    monkeypatch.setattr(scr, "action", opened.append)
    app.tap(pt)
    v = scr.view()
    assert scr.sel == 3 and scr.sel_job is v[3] and opened == []
    v[3].state = rb.rommlib.Job.PAUSED                     # now first in the queue
    _frame(app, scr)
    assert scr.sel == 0 and scr.view()[0] is v[3]
    app.tap((rb.W // 2, 84 + 30))
    assert opened == ["ok"] and len(jobs) == 10


def _collections(groups):
    return [{"name": "%s %02d" % (g, i), "tag": "", "group": g, "count": i + 1, "full": "%s %02d" % (g, i),
             "param": "collection_id", "id": 100 * k + i} for k, (g, m) in enumerate(groups) for i in range(m)]


# (sel, scroll) on 3 groups (Top lists 5, Themes 14, Series 13): _script(10) + left/right group jumps
TRACE_COLLECTIONS = [(1, 0), (2, 0), (3, 0), (4, 0), (5, 0), (6, 0), (7, 0), (8, 0), (9, 0), (10, 1), (11, 2),
                     (21, 12), (31, 22), (31, 22), (21, 21), (20, 20), (19, 19), (18, 18), (28, 19), (31, 22),
                     (31, 22), (31, 22), (31, 22), (21, 21), (11, 11), (1, 1), (0, 0), (0, 0), (0, 0), (5, 0),
                     (19, 10), (19, 10), (5, 5), (6, 5), (0, 0), (0, 0), (0, 0), (0, 0)]


def test_collections_move_page_jump_groups_and_open_on_tap(app, rb, monkeypatch):
    """Collections: rows as everywhere, left/right to the previous or next
    group, A-Z only in a single list (L2/R2 do nothing here), one tap opens."""
    scr = rb.CollectionScreen(app)
    scr.items = _collections([("Top lists", 5), ("Themes", 14), ("Series", 13)])
    app.push(scr)
    n, rows = len(scr.items), 10
    rects = _frame(app, scr)
    assert rects == [(48, 80 + k * 54, rb.W - 96, 50) for k in range(rows)]
    seen = []
    for a in _script(rows) + ["right", "right", "right", "left", "down", "left", "left", "r2", "l2"]:
        scr.action(a)
        rects = _frame(app, scr)
        seen.append((scr.sel, scr.scroll))
        assert scr.scroll <= scr.sel < scr.scroll + rows
        assert rects == [(48, 80 + k * 54, rb.W - 96, 50) for k in range(min(rows, n - scr.scroll))]
    assert seen == TRACE_COLLECTIONS
    opened = []
    monkeypatch.setattr(scr, "action", opened.append)
    app.tap((rb.W // 2, 80 + 3 * 54 + 25))
    assert scr.sel == scr.scroll + 3 and opened == ["ok"]   # one tap opens


def test_collections_letter_jump_in_one_list(app, rb):
    """A Browse-by list: L2/R2 jump to the next or previous letter, put at the
    top of the list (as in a game list)."""
    scr = rb.CollectionScreen(app, "genre", "Genre")
    scr.items = [dict(it, name=chr(65 + i // 4) + it["name"]) for i, it in enumerate(_collections([("Genre", 30)]))]
    app.push(scr)
    seen = []
    for a in ["r2", "r2", "down", "l2", "l2", "r1", "r1", "r2", "l2"]:
        scr.action(a)
        _frame(app, scr)
        seen.append((scr.sel, scr.scroll))
    assert seen == [(4, 4), (8, 8), (9, 8), (8, 8), (4, 4), (14, 5), (24, 15), (28, 20), (24, 20)]


# (sel, scroll, rows asked to be fetched) on a 40-row list: _script(12), left/right paging, then A–Z jumps
TRACE_GAMES = [(1, 0, (13,)), (2, 0, (14,)), (3, 0, (15,)), (4, 0, (16,)), (5, 0, (17,)), (6, 0, (18,)),
               (7, 0, (19,)), (8, 0, (20,)), (9, 0, (21,)), (10, 0, (22,)), (11, 0, (23,)), (12, 1, (24,)),
               (13, 2, (25,)), (25, 14, (37,)), (37, 26, (49,)), (39, 28, (51,)), (27, 27, (39,)),
               (26, 26, (38,)), (25, 25, (37,)), (24, 24, (36,)), (36, 25, (48,)), (39, 28, (51,)),
               (39, 28, (51,)), (39, 28, (51,)), (39, 28, (51,)), (27, 27, (39,)), (15, 15, (27,)),
               (3, 3, (15,)), (0, 0, (12,)), (0, 0, (12,)), (0, 0, (12,)), (12, 1, (24,)), (24, 13, (36,)),
               (36, 25, (48,)), (24, 24, (36,)), (12, 12, (24,)), (14, 14, (14, 26)), (31, 28, (31, 43)),
               (30, 28, (42,)), (14, 14, (14, 26)), (0, 0, (0, 12)), (14, 14, (14, 26))]


def test_game_list_moves_pages_jumps_and_fetches_ahead(app, rb, monkeypatch):
    """Game lists page with L/R and left/right alike, ask for the page a screen
    ahead after every move, and an A-Z jump puts the letter at the top."""
    roms = [{"id": i, "name": "Game %02d" % i, "fs_name": "G%02d.gba" % i, "platform_id": 1, "fs_size_bytes": 1 << 20}
            for i in range(40)]
    scr = rb.GameListScreen(app, "Games", static=roms)
    asked = []
    monkeypatch.setattr(scr, "ensure", asked.append)
    scr.starts = [(0, "G"), (14, "H"), (31, "K")]
    app.push(scr)
    n, rows = 40, 12

    def frame():
        return [r for r in _frame(app, scr) if r[0] == 32 and r[2] == 676]
    assert frame() == [(32, 120 + k * 44, 676, 40) for k in range(rows)]
    seen = []
    for a in _script(rows) + ["right", "right", "right", "left", "left", "r2", "r2", "up", "l2", "l2", "r2"]:
        asked.clear()
        scr.action(a)
        rects = frame()
        seen.append((scr.sel, scr.scroll, tuple(asked)))
        assert scr.scroll <= scr.sel < scr.scroll + rows
        assert rects == [(32, 120 + k * 44, 676, 40) for k in range(min(rows, n - scr.scroll))]
    assert seen == TRACE_GAMES
    opened = []
    monkeypatch.setattr(scr, "action", opened.append)
    pt = (300, 120 + 2 * 44 + 20)
    app.tap(pt)
    assert scr.sel == scr.scroll + 2 and opened == []
    app.tap(pt)
    assert opened == ["ok"]


# -- one way to pick several rows, A-Z / system jumps and paging on every list ----------------

def _picking(rb, app, name, n, monkeypatch):
    """A list that can pick rows, with n of them."""
    if name == "DownloadsScreen":
        _jobs(rb, app, n, paused=n)
        return rb.DownloadsScreen(app)
    if name == "GameListScreen":
        app.platform_by_id = {1: {"_system": "gba", "id": 1, "name": "GBA"}}
        roms = [{"id": i, "name": "Game %02d" % i, "fs_name": "G%02d.gba" % i, "platform_id": 1,
                 "fs_size_bytes": 1 << 20} for i in range(n)]
        return rb.GameListScreen(app, "Games", static=roms)
    if name == "AuditScreen":
        app.feed = ([dict(f, rom={"id": k, "name": "G%02d" % k}, severity="damaged" if k % 2 else "differs")
                     for k, f in enumerate(_findings(n))], {"checked": n})
        return rb.AuditScreen(app)
    return _make(rb, app, name, n, monkeypatch)[0]


PICKING = ["AuditScreen", "CompressSystemScreen", "DownloadsScreen", "GameListScreen", "InstalledScreen",
           "LeftoversScreen", "UnseenScreen"]
# what the first entry of the X menu says with two rows picked
PICKED_FIRST = {"AuditScreen": "Download 2 again", "CompressSystemScreen": "Compress 2 picked",
                "DownloadsScreen": "Resume 2", "GameListScreen": "Download 2 picked",
                "InstalledScreen": "Delete 2 picked", "LeftoversScreen": "Delete 2 picked",
                "UnseenScreen": "Unpack 2 archives"}


@pytest.mark.parametrize("name", PICKING)
def test_every_list_picks_several_the_same_way(app, rb, name, monkeypatch):
    """X opens the list's menu, whose "Pick several" starts picking; then A
    picks and moves on, X's menu starts with what to do with the picks, B
    stops picking (the picks stay ticked) and a second B goes back."""
    app.push(rb.Screen(app))
    scr = _picking(rb, app, name, 12, monkeypatch)
    app.push(scr)
    _frame(app, scr)
    assert ("X", "Actions") in scr.hints() and not scr.picking
    scr.action("x")
    labels = [e[0] for e in app.modal.entries if e]
    k = next(i for i, e in enumerate(app.modal.entries) if e and e[0].startswith("Pick several"))
    app.modal.sel = k
    app.modal.action("ok")
    assert app.modal is None and scr.picking
    h = scr.hints()
    assert h[:2] == [("A", "Pick"), ("X", "Actions")] and h[-1] == ("B", "Stop picking")
    scr.action("ok")
    scr.action("ok")
    assert scr.pick_count() == 2 and scr.sel == 2 and scr.hints()[1] == ("X", "2 picked")
    _frame(app, scr)                                      # tick boxes drawn
    scr.action("x")
    assert app.modal.title.startswith("2 picked") or name == "GameListScreen"
    assert app.modal.entries[0][0] == PICKED_FIRST[name] and app.modal.entries[0][2]
    assert "Stop picking" in [e[0] for e in app.modal.entries if e]
    app.modal = None
    scr.action("back")
    assert not scr.picking and scr.pick_count() == 2 and app.stack[-1] is scr
    scr.action("back")
    assert app.stack[-1] is not scr
    assert "Pick several" in " ".join(labels)


@pytest.mark.parametrize("name", PICKING)
def test_a_tap_picks_while_picking(app, rb, name, monkeypatch):
    scr = _picking(rb, app, name, 12, monkeypatch)
    app.push(scr)
    scr.picking = True
    _frame(app, scr)
    rect = app.targets[0][0]                              # the first row
    app.tap(rect.center)
    assert scr.pick_count() == 1 and scr.sel == scr.base
    app.tap(rect.center)
    assert scr.pick_count() == 0


@pytest.mark.parametrize("name, entry, title", [
    ("InstalledScreen", 0, "Delete 2 games?"), ("LeftoversScreen", 0, "Delete 2 leftovers?"),
    ("UnseenScreen", 1, "Delete 2 files?"), ("DownloadsScreen", 1, "Cancel 2 downloads?")])
def test_deleting_or_cancelling_picks_asks_first(app, rb, name, entry, title, monkeypatch):
    scr = _picking(rb, app, name, 6, monkeypatch)
    app.push(scr)
    scr.picking = True
    scr.action("ok")
    scr.action("ok")
    scr.action("x")
    app.modal.sel = entry
    app.modal.action("ok")
    assert isinstance(app.modal, rb.Confirm) and app.modal.danger and app.modal.title == title


def test_free_up_space_is_always_picking(app, rb, monkeypatch):
    """Free up space only picks: A picks from the start, B goes back, a tap
    picks; X deletes the picks after a confirm, or picks enough to free N GB."""
    app.push(rb.Screen(app))
    scr = _make(rb, app, "FreeUpScreen", 12, monkeypatch)[0]
    app.push(scr)
    assert scr.hints()[:3] == [("A", "Pick"), ("X", "Actions"), ("Y", "Least played first")]
    scr.action("ok")
    assert scr.pick_count() == 1 and scr.sel == 1
    scr.action("x")
    assert app.modal.entries[0][0] == "Delete 1 picked"
    assert "Pick several games" not in [e[0] for e in app.modal.entries if e]
    app.modal.action("ok")
    assert isinstance(app.modal, rb.Confirm) and app.modal.danger
    app.modal = None
    scr.action("x")
    app.modal.sel = 1
    app.modal.action("ok")                                # pick enough to free 10 GB: all 12 (78 MB)
    assert scr.pick_count() == 12
    scr.action("back")
    assert app.stack[-1] is not scr


def test_check_findings_amber_ones_download_again_together(app, rb, monkeypatch):
    """Picked findings download again together, the amber "differs" ones too;
    a finding RomM hasn't got can't be picked."""
    found = [dict(f, rom={"id": k, "name": "G%02d" % k}, severity="damaged" if k == 0 else "differs")
             for k, f in enumerate(_findings(3))]
    found.append(dict(_findings(4)[3], severity="differs"))           # rom None
    monkeypatch.setattr(rb.rommlib, "load_audit", lambda: {"findings": found, "stats": {}, "time": 0})
    scr = rb.CheckScreen(app)
    app.push(scr)
    queued = []
    monkeypatch.setattr(app.downloader, "enqueue", lambda rom, system, **kw: queued.append(rom["id"]))
    scr.picking = True
    scr.sel = scr.base
    for _ in range(4):
        scr.action("ok")
    assert scr.pick_count() == 3 and "can't download it again" in app.toasts[-1][0]
    scr.action("x")
    assert app.modal.entries[0][:2] == ("Download 3 again", "1 damaged, 2 differ from RomM")
    app.modal.action("ok")
    app.modal.action("ok")
    assert sorted(queued) == [0, 1, 2] and scr.picks == {} and not scr.picking


def test_downloads_menu_pauses_all_and_picks_cancel_several(app, rb, monkeypatch):
    jobs = _jobs(rb, app, 5, paused=3)
    scr = rb.DownloadsScreen(app)
    app.push(scr)
    scr.action("x")
    assert app.modal.entries[0][0] == "Pause all"
    app.modal.action("ok")
    assert app.downloader.hold
    scr.picking = True
    scr.action("ok")
    scr.action("ok")
    scr.action("x")
    labels = [e[0] for e in app.modal.entries if e]
    assert labels[:2] == ["Resume 2", "Cancel 2"]
    app.modal.sel = 1
    app.modal.action("ok")
    app.modal.action("ok")
    J = rb.rommlib.Job
    assert [j.state for j in jobs[:3]] == [J.CANCELLED, J.CANCELLED, J.PAUSED] and not scr.picking


@pytest.mark.parametrize("name, label", [("FreeUpScreen", "A–Z"), ("InstalledScreen", "A–Z"),
                                         ("LeftoversScreen", "System"), ("UnseenScreen", "System"),
                                         ("BiosScreen", "A–Z"), ("CompressSystemScreen", "A–Z")])
def test_letter_or_system_jumps(app, rb, name, label, monkeypatch):
    """L2/R2 on every list in name (or system) order; the jump puts the run
    at the top of the list. Free up space and Games from RomM jump only
    when sorted by name, and say so."""
    scr = _make(rb, app, name, 30, monkeypatch)[0]
    app.push(scr)
    rows = scr.listed()
    for k, r in enumerate(rows):                          # three runs of ten: A…, B…, C…
        ch = "ABC"[k // 10]
        if name in ("LeftoversScreen", "UnseenScreen"):
            sy = "s" + ch
            r["path"] = "%s/%s/f%02d" % (rb.rommlib.ROMS_ROOT, sy, k)
            r["system"] = sy
        elif name == "CompressSystemScreen":
            r["name"] = ch + r["name"]
        else:
            r["name"] = ch + "%02d" % k
    if name in ("FreeUpScreen", "InstalledScreen"):
        scr.action("r2")
        assert scr.sel == 0 and "sorted by name" in app.toasts[-1][0]
        assert ("L2 R2", label) not in scr.hints()
        while scr.sort != "name":
            scr.action("y")
    scr.__dict__.pop("_starts", None)                     # names changed in place
    rows = scr.listed()
    first = {k: rows[k] for k in (0, 10, 20)}
    assert ("L2 R2", label) in scr.hints()
    scr.action("r2")
    assert scr.listed()[scr.sel] is first[10] and scr.scroll == 10 and scr.bubble
    scr.action("r2")
    assert scr.listed()[scr.sel] is first[20] and scr.scroll == min(20, 30 - scr.ROWS)
    scr.action("down")
    scr.action("l2")                                      # back to the start of its own run first
    assert scr.listed()[scr.sel] is first[20]
    scr.action("l2")
    assert scr.listed()[scr.sel] is first[10]


def test_page_hints_and_left_right_on_every_list(app, rb, monkeypatch):
    """L/R are shown only when there's more than a page; left/right page too."""
    for name in sorted(GEOMETRY):
        rows = GEOMETRY[name][0]
        scr, _ = _make(rb, app, name, rows, monkeypatch)
        assert ("L", "Page up") not in scr.hints(), name
        scr, _ = _make(rb, app, name, 2 * rows + 1, monkeypatch)
        assert ("L", "Page up") in scr.hints() and ("R", "Page down") in scr.hints(), name
        scr.action("right")
        assert scr.sel == rows, name
        scr.action("left")
        assert scr.sel == 0, name


def test_games_from_romm_sorts_and_offers_the_game_list_menu(app, rb, monkeypatch):
    """Games from RomM: Y sorts (biggest, name, newest); A opens the menu a
    game opened from a game list has, plus More like this, once RomM's
    record is here (else Delete only); the panel shows RomM's record."""
    app.platform_by_id = {1: {"_system": "gba", "id": 1, "name": "GBA"}}
    recs = {}
    monkeypatch.setattr(app, "detail", lambda rom: recs.get(rom["id"]))
    items = [{"id": i, "name": "Game %s" % c, "system": "gba", "when": "2026-01-%02d" % (10 - i), "size": s}
             for i, (c, s) in enumerate([("B", 5), ("C", 9), ("A", 1)])]
    app.feed = items
    scr = rb.InstalledScreen(app)
    app.push(scr)
    assert [i["name"] for i in scr.items] == ["Game C", "Game B", "Game A"]
    scr.action("y")
    assert [i["name"] for i in scr.items] == ["Game A", "Game B", "Game C"]
    scr.action("y")
    assert [i["id"] for i in scr.items] == [0, 1, 2] and ("Y", "Newest first") in scr.hints()
    scr.action("ok")                                      # RomM's record not here yet: delete only
    m = app.modal
    assert [(e[0], bool(e[2])) for e in m.entries] == [("Delete from this box", True), ("Download again", False),
                                                       ("More like this", False)]
    assert m.entries[1][1] == "Looking it up in RomM…"
    app.modal = None
    _frame(app, scr)
    recs[0] = {"id": 0, "name": "Game B", "fs_name": "Game B.gba", "platform_id": 1, "fs_size_bytes": 5}
    scr.action("ok")
    assert isinstance(app.modal, rb.ManageMenu)
    assert [e[0] for e in app.modal.entries] == ["Play now", "Delete from this box", "Download again",
                                                 "More like this"]
    app.modal = None
    rects = _frame(app, scr)
    assert (752, 580, 472, 52) in rects                   # the game panel's action area (as in a game list)
