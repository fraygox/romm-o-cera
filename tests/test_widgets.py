"""rbui/widgets: shelves end flush with their column at any width and scroll with the focus,
pill rows share their slack so they end exactly at the column edge, facts too tall for their
box scroll in it (the shared scroller), scaled covers leave memory with their cover, and the
verb button says the right thing for each state of a game."""
import types

import pytest


@pytest.fixture
def fonts(rb, monkeypatch):
    import pygame
    pygame.init()
    monkeypatch.setattr(rb.theme, "K", 1.0)
    f = rb.Fonts()
    monkeypatch.setitem(rb.draw.FONTS, "f", f)
    monkeypatch.setattr(rb.draw, "_SCROLLS", {})
    return f


@pytest.fixture
def app(isolated, rb, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    a = rb.App(None, headless=True)
    a.platforms = [{"id": 7, "name": "PlayStation", "_system": "psx"}]
    a.platform_by_id = {7: a.platforms[0]}
    return a


def _canvas(rb):
    import pygame
    c = pygame.Surface((rb.S(rb.W), rb.S(rb.H)))
    c.fill(rb.BG)
    return c


# ------------------------------------------------------------------ the shelf

@pytest.mark.parametrize("width", [300, 452, 640, 652, 702, 1000, 1184])
def test_a_shelf_fits_what_it_can_and_ends_flush_with_its_column(rb, width):
    x0 = 48
    sh = rb.Shelf((90, 120), 12)
    rects = [r for _, r in sh.layout(x0, x0 + width, 100, 50)]
    per = (width + 12) // (90 + 12)
    assert len(rects) == per
    assert rects[0].left == x0 and rects[-1].right == x0 + width
    gaps = [b.left - a.right for a, b in zip(rects, rects[1:])]
    assert all(g >= 12 for g in gaps) and max(gaps) - min(gaps) <= 1      # the slack shared evenly
    assert all(r.size == (90, 120) for r in rects)


def test_a_shelf_too_narrow_for_one_tile_still_shows_one(rb):
    assert rb.shelf_fit(60, 90, 12) == (1, 0.0)


def test_a_short_shelf_keeps_the_same_spacing(rb):
    full = [r for _, r in rb.Shelf((90, 120), 12).layout(48, 700, 0, 20)]
    short = [r for _, r in rb.Shelf((90, 120), 12).layout(48, 700, 0, 3)]
    assert short == full[:3]


def test_the_shelf_scrolls_with_the_focus_and_counts_whats_beyond(rb):
    sh = rb.Shelf((90, 120), 12)
    sh.layout(48, 700, 0, 14, sel=0)
    per = len(sh.rects)
    assert sh.first == 0 and sh.more(14) == 14 - per
    sh.layout(48, 700, 0, 14, sel=per)                  # one step past the right edge: one step along
    assert sh.first == 1 and sh.rects[-1][0] == per
    sh.layout(48, 700, 0, 14, sel=13)
    assert sh.rects[-1][0] == 13 and sh.more(14) == 0
    sh.layout(48, 700, 0, 14, sel=2)                    # back left: the least scroll that shows it
    assert sh.first == 2
    sh.layout(48, 700, 0, 3, sel=2)                     # the list got shorter: never scrolled past its end
    assert sh.first == 0


def test_a_tap_finds_the_tile_under_it(rb):
    sh = rb.Shelf((90, 120), 12)
    sh.layout(48, 700, 100, 14, sel=9)
    for i, r in sh.rects:
        assert sh.index_at(r.center) == i
    a, b = sh.rects[0][1], sh.rects[1][1]
    assert sh.index_at(((a.right + b.left) // 2, a.centery)) is None
    assert sh.index_at((a.centerx, a.bottom + 5)) is None


def test_shelf_draw_registers_a_tap_for_each_tile(rb, app, fonts):
    roms = [{"id": i, "name": "Lorem %d" % i, "platform_id": 7} for i in range(12)]
    sh = rb.Shelf((90, 120), 12)
    tapped = []
    app.targets = []
    sh.draw(_canvas(rb), app, roms, 48, 700, 100, sel=0, on_tap=tapped.append)
    assert [r for r, _ in app.targets] == [r for _, r in sh.rects]
    app.targets[2][1]()
    assert tapped == [sh.rects[2][0]]


# ------------------------------------------------------------------ pills

@pytest.mark.parametrize("width", [420, 560, 652, 702, 900])
def test_pills_share_the_slack_and_end_exactly_at_the_edge(rb, fonts, width):
    labels = ["Search", "Platforms", "Collections", "Downloads", "On this box", "Settings"]
    rects = rb.pill_rects(fonts.body, labels, 48, 48 + width, 84)
    assert rects[0].left == 48 and rects[-1].right == 48 + width
    assert all(b.left - a.right == rb.PILL_GAP for a, b in zip(rects, rects[1:]))
    nat = [rb.measure(fonts.body, s) + 2 * rb.PILL_PAD for s in labels]
    grow = [r.width - n for r, n in zip(rects, nat)]
    if all(r.width >= n - 2 * (rb.PILL_PAD - rb.MIN_PAD) for r, n in zip(rects, nat)):
        assert max(grow) - min(grow) <= 2           # roomy or a little tight: everyone grows/shrinks alike


def test_fill_widths_keeps_floors_then_shrinks_in_proportion(rb):
    w = rb.fill_widths([100, 40, 100], 250, gap=10, least=[90, 40, 90])
    assert sum(w) + 20 == 250 and w == [95, 40, 95]          # the short one sits on its floor, the others share
    w = rb.fill_widths([100, 100], 110, gap=10)          # not even the floors fit: in proportion
    assert sum(w) + 10 == 110 and w[0] == w[1]
    assert rb.fill_widths([], 100) == []


def test_a_pill_row_draws_inside_its_column(rb, fonts):
    import pygame
    c = _canvas(rb)
    rects = rb.pill_row(c, fonts.body, ["All · 12", "Matches RomM · 8", "Newer in RomM · 2"], 48, 500, 84,
                        focus=1, dots=[None, rb.GREEN, rb.BLUE])
    probe = c.copy()
    probe.fill(rb.BG, pygame.Rect(48, 84, 452, 38))
    assert pygame.image.tobytes(probe, "RGB") == pygame.image.tobytes(_canvas(rb), "RGB")
    assert rects[-1].right == 500


# ------------------------------------------------------------------ facts

PAIRS = [("Size", "690 MB"), ("Format", "CHD (from CUE/BIN)"), ("Region", "USA"), ("Developer", "Lorem Soft"),
         ("Players", "1-2"), ("Released", "1998"), ("Genre", "Racing"), ("Manual", "Included")]


def test_facts_lay_out_in_columns_and_drop_empty_ones(rb, fonts):
    cells, total = rb.facts_layout(fonts, PAIRS[:4] + [("Achievements", "")], 400, cols=2)
    assert [c[0] for c in cells] == ["Size", "Format", "Region", "Developer"]
    assert [c[2] for c in cells] == [0, 208, 0, 208]          # (400 - 16) / 2 + 16
    assert total == 2 * 42
    cells, total = rb.facts_layout(fonts, PAIRS[:2], 150, cols=2)
    assert len(cells[1][1]) > 1 and total > 42                 # a long value wraps in its column


def test_facts_that_fit_stay_still_and_overflow_scrolls_in_the_box(rb, fonts, monkeypatch):
    import pygame
    seen = []
    real = rb.scroll_box

    def spy(surf, rect, total, paint, sig, *a, **kw):
        seen.append((pygame.Rect(rect), total))
        return real(surf, rect, total, paint, sig, *a, **kw)
    rb.patch(monkeypatch, "scroll_box", spy)
    box = pygame.Rect(600, 200, 400, 84)

    def shot(pairs, t):
        monkeypatch.setattr(rb.draw, "NOW", t)
        c = _canvas(rb)
        rb.facts(c, fonts, pairs, box, cols=2, bg=rb.BG, slot="t")
        return pygame.image.tobytes(c, "RGB")
    fit = [shot(PAIRS[:4], t) for t in (0.0, 0.3, 6.0)]
    assert fit[0] == fit[-1] and seen[-1][1] <= box.height
    t, over = 0.0, []
    while t <= 6.0:                                    # every frame, as the app draws it
        over.append(shot(PAIRS, round(t, 2)))
        t += 0.1
    assert seen[-1][1] > box.height                    # handed to the scroller, taller than the box
    assert over[0] != over[-1]                         # and it moved
    c = pygame.Surface((rb.W, rb.H))
    c.fill(rb.BG)
    c.blit(pygame.image.frombytes(over[-1], (rb.W, rb.H), "RGB"), (0, 0))
    c.fill(rb.BG, box)
    assert pygame.image.tobytes(c, "RGB") == pygame.image.tobytes(_canvas(rb), "RGB")   # never outside


def test_overflowing_facts_leave_room_for_the_position_bar(rb, fonts, monkeypatch):
    import pygame
    widths = []
    real = rb.facts_layout

    def spy(f, pairs, width, cols=2):
        widths.append(width)
        return real(f, pairs, width, cols)
    monkeypatch.setattr(rb.factsheet, "facts_layout", spy)
    rb.facts(_canvas(rb), fonts, PAIRS, pygame.Rect(0, 0, 300, 60), cols=2)
    assert widths == [300, 300 - rb.SCROLL_BAR]


# ------------------------------------------------------------------ covers, backdrop

def _with_cover(app, rid, size=(192, 256)):
    import pygame
    img = pygame.Surface(size, pygame.SRCALPHA, 32)
    img.fill((200, 40, 40))
    app.covers[rid] = img
    app.cover_order.append(rid)
    app.cover_bytes += 1
    return img


def test_scaled_covers_are_cached_and_leave_with_their_cover(rb, app, fonts):
    import pygame
    _with_cover(app, 1)
    c = _canvas(rb)
    rb.cover(c, app, {"id": 1}, pygame.Rect(0, 0, 90, 120))
    rb.cover(c, app, {"id": 1}, pygame.Rect(0, 0, 192, 256), fit=True)
    ent = app.scaled_covers[1]
    assert ent[0] is app.covers[1] and len(ent[1]) == 2
    assert ent[1][("fit", (192, 256))] is app.covers[1]        # already that size: no copy
    first = ent[1][("crop", (90, 120))]
    rb.cover(c, app, {"id": 1}, pygame.Rect(10, 10, 90, 120))
    assert app.scaled_covers[1][1][("crop", (90, 120))] is first
    app.cover_order.insert(0, app.cover_order.pop())
    app.cover_bytes = 1 << 40                                  # over budget: the cover goes, its copies too
    app.trim_covers()
    assert 1 not in app.scaled_covers


def test_scaled_covers_stay_in_their_share_of_the_budget(rb, app, fonts, monkeypatch):
    import pygame
    monkeypatch.setattr(rb.draw, "COVER_BUDGET", 400 * 520 * 4)
    for rid in range(6):
        _with_cover(app, rid, (400, 520))
        rb.cover(_canvas(rb), app, {"id": rid}, pygame.Rect(0, 0, 200, 260))
    total = sum(rb.surface_bytes(s) for e in app.scaled_covers.values() for s in e[1].values())
    assert total <= rb.draw.COVER_BUDGET * rb.SCALED_SHARE
    assert 5 in app.scaled_covers                              # the one just drawn stays


def test_a_missing_cover_draws_a_placeholder(rb, app, fonts, monkeypatch):
    import pygame
    monkeypatch.setattr(app, "cover", lambda rom: None)
    c = _canvas(rb)
    r = rb.cover_tile(c, app, {"id": 3, "name": "Lorem Ipsum Quest", "platform_id": 7}, pygame.Rect(48, 100, 90, 120),
                      focused=True, on_box=False)
    assert r == pygame.Rect(44, 96, 98, 128)                   # lifted inside the ring
    assert c.get_at((60, 210))[:3] == rb.BG2


def test_the_backdrop_respects_the_memory_budget(rb, app, fonts, monkeypatch):
    import pygame
    _with_cover(app, 1)
    rect = pygame.Rect(0, 64, rb.W, 300)
    assert rb.backdrop(_canvas(rb), app, {"id": 1}, rect)
    monkeypatch.setattr(rb.draw, "COVER_BUDGET", 4 << 20)
    assert not rb.backdrop_fits(rect)
    assert not rb.backdrop(_canvas(rb), app, {"id": 1}, rect)


# ------------------------------------------------------------------ the verb, bars

def test_the_game_verb_follows_the_games_state(rb, app, monkeypatch):
    rom = {"id": 1, "name": "Lorem", "platform_id": 7, "fs_size_bytes": 300 << 20}
    monkeypatch.setattr(app, "rom_status", lambda r: ("remote", None))
    v = rb.game_verb(app, rom)
    assert (v["label"], v["kind"], v["detail"]) == ("Download", "go", "300.0 MB")
    assert rb.game_note(app, rom) == "Saved to roms/psx."
    monkeypatch.setattr(app, "rom_status", lambda r: ("installed", None))
    assert rb.game_verb(app, rom)["kind"] == "play" and rb.game_verb(app, rom)["label"] == "Play now"
    job = types.SimpleNamespace(state=rb.rommlib.Job.DOWNLOADING, done_bytes=1, total_bytes=4, speed=1 << 20)
    v = rb.game_verb(app, rom, ("active", job))
    assert v["kind"] == "busy" and v["frac"] == 0.25 and v["label"].startswith("Downloading 25%")
    assert rb.game_verb(app, rom, ("failed", job))["label"] == "Retry download"
    assert rb.game_verb(app, dict(rom, platform_id=99))["kind"] == "off"


def test_meter_segments_follow_each_other(rb, fonts):
    c = _canvas(rb)
    rb.meter(c, (100, 100, 200, 10), [(0.5, rb.ACCENT), (0.25, rb.AMBER)])
    assert c.get_at((150, 105))[:3] == rb.ACCENT
    assert c.get_at((225, 105))[:3] == rb.AMBER
    assert c.get_at((290, 105))[:3] == rb.LINE
    rb.state_stripe(c, (100, 200, 400, 56), rb.BLUE)
    assert c.get_at((101, 228))[:3] == rb.BLUE


def test_meter_keeps_small_parts_visible_and_stripes_the_reserve(rb, fonts):
    c = _canvas(rb)
    rb.meter(c, (100, 100, 400, 20), [(0.001, rb.ACCENT), (0.5, rb.BLUE)], track=rb.BG2, least=3, reserve=0.25)
    assert c.get_at((101, 110))[:3] == rb.ACCENT          # at least 3 px wide
    assert c.get_at((200, 110))[:3] == rb.BLUE
    stripes = {tuple(c.get_at((x, 110))[:3]) for x in range(410, 490)}
    assert tuple(rb.LINE) in stripes and tuple(rb.BG2) in stripes
    assert {tuple(c.get_at((x, 110))[:3]) for x in range(320, 390)} == {tuple(rb.BG2)}
    assert c.get_at((100, 100))[:3] == rb.BG               # the corner stays round
    rb.progress_bar(c, (100, 200, 200, 10), 0.0)
    assert c.get_at((200, 205))[:3] == rb.LINE


def test_scrollbar_thumb_follows_the_position(rb, fonts):
    c = _canvas(rb)
    rb.scrollbar(c, (100, 100, 4, 200), 0.25, 0.0)
    assert c.get_at((101, 110))[:3] == rb.ACCENT and c.get_at((101, 290))[:3] == rb.LINE
    c = _canvas(rb)
    rb.scrollbar(c, (100, 100, 4, 200), 0.01, 1.0)        # never thinner than min_h
    assert c.get_at((101, 290))[:3] == rb.ACCENT and c.get_at((101, 278))[:3] == rb.ACCENT
    assert c.get_at((101, 120))[:3] == rb.LINE


def test_card_is_the_focus_box_when_focused(rb, fonts):
    c = _canvas(rb)
    rb.card(c, (100, 100, 200, 60), False)
    rb.card(c, (100, 200, 200, 60), True)
    assert c.get_at((200, 130))[:3] == rb.PANEL
    assert c.get_at((200, 230))[:3] == rb.PANEL_HI and c.get_at((200, 200))[:3] == rb.ACCENT


def test_button_chip_is_as_wide_as_it_says(rb, fonts):
    c = _canvas(rb)
    for tok in ("A", "SELECT"):
        r = rb.button_chip(c, fonts, (10, 10), tok, "Compress all")
        assert r.width == rb.button_chip_width(fonts, tok, "Compress all")
    assert rb.button_chip_width(fonts, "SELECT", "x") > rb.button_chip_width(fonts, "A", "x")
    assert rb.button_chip(c, fonts, (10, 10), "A", "A long label " * 9, maxw=200).width == 200


def test_facts_take_a_colour_per_label(rb, fonts, monkeypatch):
    seen = {}
    real = rb.draw.text

    def spy(surf, font, s, pos, color=None, anchor="topleft", maxw=None):
        seen[s] = color
        return real(surf, font, s, pos, color, anchor, maxw)
    rb.patch(monkeypatch, "text", spy)
    rb.facts(_canvas(rb), fonts, [("License", "GPL"), ("Size", "1 GB"), ("Region", "USA")], (0, 0, 400, 200),
             strong=("Size",), colors={"License": rb.ACCENT_HI})
    assert seen["GPL"] == rb.ACCENT_HI and seen["1 GB"] == rb.TEXT and seen["USA"] == rb.DIM


def test_a_dialogs_buttons_are_verb_buttons(rb, app, monkeypatch):
    kinds = []
    real = rb.verb_button

    def spy(c, f, rect, label, kind="go", *a, **k):
        kinds.append((label, kind, k.get("glyph", "A")))
        return real(c, f, rect, label, kind, *a, **k)
    rb.patch(monkeypatch, "verb_button", spy)
    d = rb.Confirm(app, "Delete?", "Gone for good.", "Delete", lambda: None, danger=True)
    d.draw(app.canvas)
    assert kinds == [("Delete", "danger", "A"), ("Back", "quiet", "B")]


# ------------------------------------------------------------------ wave 2: Game night, Backlog

def test_switch_and_switch_pill(rb, fonts):
    c = _canvas(rb)
    on = rb.switch(c, (10, 10, 44, 24), True)
    assert c.get_at((on.right - 12, on.centery))[:3] == rb.theme.TEXT        # the knob at the right
    off = rb.switch(c, (10, 50, 44, 24), False)
    assert c.get_at((off.left + 12, off.centery))[:3] == rb.theme.DIM
    w = rb.switch_pill_width(fonts.body, "Only what's on this box")
    sw = rb.switch_pill(c, fonts.body, "Only what's on this box", (100, 100, w, 38), True)
    assert sw.right <= 100 + w and sw.left > 100 + rb.measure(fonts.body, "Only what's on this box")


def test_player_dots_light_tonights_party(rb, fonts):
    c = _canvas(rb)
    x = rb.player_dots(c, fonts, 4, 2, (20, 30), "4 can play")
    assert c.get_at((28, 30))[:3] == rb.theme.GREEN and c.get_at((28 + 2 * 24, 30))[:3] == rb.theme.LINE
    assert x > 20 + 3 * 24 + 16 + rb.measure(fonts.small, "4 can play") - 1
    assert rb.player_dots(c, fonts, 8, 8, (20, 80)) == 20 + 16 + 3 * 24        # four dots at most


def test_meta_line_and_year(rb, app):
    rom = {"id": 1, "platform_id": 7, "metadatum": {"genres": ["Action", "Shooter", "Puzzle"],
                                                    "player_count": "1-4", "first_release_date": 946684800000}}
    assert rb.meta_line(app, rom) == "PlayStation · 2000 · Action, Shooter · up to 4 players"
    assert rb.meta_line(app, rom, platform=False, players=False) == "2000 · Action, Shooter"
    assert rb.rom_year({"metadatum": {"first_release_date": "junk"}}) == ""
    assert rb.meta_line(app, {"id": 2, "platform_id": 99}) == ""


def test_press_verb_follows_the_games_state(rb, app, monkeypatch):
    rom = {"id": 1, "name": "Lorem", "platform_id": 7, "fs_name": "Lorem.chd", "fs_size_bytes": 10}
    monkeypatch.setattr(app, "rom_status", lambda r: ("remote", None))
    rb.press_verb(app, rom)
    assert isinstance(app.modal, rb.DownloadConfirm)
    app.modal = None
    ran = []
    monkeypatch.setattr(app, "rom_status", lambda r: ("installed", None))
    monkeypatch.setattr(app, "play_entry", lambda r: ("Play now", "", lambda: ran.append(1)))
    rb.press_verb(app, rom)
    assert ran == [1]
    monkeypatch.setattr(app, "play_entry", lambda r: ("Play now", "It's still downloading", None))
    rb.press_verb(app, rom)
    assert app.toasts[-1][0] == "It's still downloading"
    rb.press_verb(app, dict(rom, platform_id=99))
    assert app.toasts[-1][0] == "No Batocera system for this platform"


def test_list_row_thumb_and_status_tick(rb, fonts, monkeypatch):
    import pygame
    c = _canvas(rb)
    got = []
    r = pygame.Rect(40, 100, 600, 52)
    tx = rb.list_row(c, fonts, r, False, "Lorem", "Ipsum", "On this box", rb.theme.GREEN,
                     thumb=lambda tr: got.append(pygame.Rect(tr)), status_tick=True)
    assert got[0].height == 44 and got[0].width == 33 and tx == got[0].right + 14
    sr_left = r.right - 20 - rb.measure(fonts.small, "On this box")
    assert c.get_at((int(sr_left - 14), r.centery))[:3] != rb.theme.BG       # the tick before the status


def test_panel_figure_legend_and_verb(rb, app, fonts):
    c = _canvas(rb)
    p = rb.Panel(c, fonts, (800, 80, 400, 560), app=app)
    y0 = p.y
    p.figure("13.5 GB", "14 games not on this box yet")
    assert p.y == y0 + 46 + 30 + 6
    y1 = p.y
    p.legend([(rb.theme.ACCENT, "12 fit now"), (None, "10 GB free")])
    assert p.y == y1 + 2 * 24 + 6
    tapped = []
    vr = p.verb("Download 14", "go", "13.5 GB", glyph="Y", fn=lambda: tapped.append(1), foot=False)
    assert vr.width == p.w and vr.height == rb.VERB_H and p.y == vr.bottom + 14
    assert any(t[0] == vr for t in app.targets)
    fr = p.verb("Continue", fn=lambda: None)
    assert fr.top > vr.bottom and fr.bottom <= 80 + 560          # foot: pinned to the bottom


@pytest.mark.parametrize("name", ["default", "dreamcast", "high_contrast_light", "paper"])
def test_verb_button_labels_read_on_their_fill(rb, fonts, monkeypatch, name):
    """A light theme's TEXT is dark: on the red or the accent the label takes ink_on(fill)."""
    seen = {}
    real = rb.draw.text

    def spy(surf, font, s, pos, color=None, anchor="topleft", maxw=None):
        seen[s] = color
        return real(surf, font, s, pos, color, anchor, maxw)
    rb.patch(monkeypatch, "text", spy)
    rb.apply_theme(name)
    try:
        c = _canvas(rb)
        rb.verb_button(c, fonts, (0, 0, 400, 52), "Delete", "danger")
        rb.verb_button(c, fonts, (0, 60, 400, 52), "Download", "go", detail="1 GB")
        assert seen["Delete"] == rb.ink_on(rb.RED) and seen["Download"] == rb.ink_on(rb.ACCENT)
        assert seen["1 GB"] == rb.ink_on(rb.ACCENT)
        if name == "dreamcast":
            assert rb.ink_on(rb.RED) != rb.TEXT            # the case this fixes
    finally:
        rb.apply_theme("default")


# ------------------------------------------------------------------ Welcome's pieces

def test_step_dot_notice_code_slots_and_row_chevron(rb, fonts):
    import pygame
    c = _canvas(rb)
    for st in ("done", "now", "next"):
        rb.step_dot(c, fonts, (40, 40), 2, st)
    assert c.get_at((40, 40))[:3] != tuple(rb.BG)[:3]
    r = rb.notice(c, fonts, (100, 100, 400, 120), "Couldn't verify", "why " * 80, slot="t-notice")
    assert r == pygame.Rect(100, 100, 400, 120) and c.get_at((101 + 6, 160))[:3] != tuple(rb.BG)[:3]
    row = rb.code_slots(c, fonts, 640, 300, "ABCD2")
    assert row.centerx in (639, 640) and row.height == 68
    n = rb.Keyboard("Check", mode="code", maxlen=8)
    n.type_text("abcd-23 45zz")
    assert n.value == "ABCD2345"                       # code mode: letters and digits, eight at most
    lr = pygame.Rect(100, 500, 400, 64)
    tx = rb.list_row(c, fonts, lr, True, "Type an API token instead", "x" * 200, chevron=True, slot="t-chev")
    assert tx == lr.left + 22
