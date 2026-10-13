"""Copy fits its box and the layout has no gaps or dead strips: names in a narrow placeholder
wrap by whole words, a scrolling text shows whole lines at rest, a centred panel line stays
centred, a row's grey line runs under a bare status, the game page's note is short and its
Your RomM strip follows a short summary, the Space legend goes single-column when narrow, a
failure's reason reads short in Lately, and the hint bar never says Options (PlayStation's
Start) or Menu (Xbox's) for A or X."""
import glob
import os
import re

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
def app(isolated, rb, fonts, monkeypatch):
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    a = rb.App(None, headless=True)
    a.platforms = [{"id": 7, "name": "PlayStation", "_system": "psx"}]
    a.platform_by_id = {7: a.platforms[0]}
    a.bg = lambda fn, done=None, err=None: done(fn()) if done else fn()
    a.api.me = lambda max_age=300: {}
    return a


def _canvas(rb):
    import pygame
    c = pygame.Surface((rb.S(rb.W), rb.S(rb.H)))
    c.fill(rb.BG)
    return c


# ---- the shared helpers

def test_wrap_keeps_whole_words_and_cuts_one_too_wide(rb, fonts):
    f = fonts.tiny
    name = "Lorem Exercitation Consequat Kart"
    split = rb.wrap(f, name, 78)
    whole = rb.wrap(f, name, 78, whole_words=True)
    assert any(ln in ("Exercitati", "Consequa") for ln in split)       # the old way: broken mid-word
    assert all(rb.measure(f, ln) <= 78 for ln in whole)
    assert whole[0] == "Lorem" and whole[1].endswith("…") and whole[1].startswith("Exercit")
    assert whole[-1] == "Kart"
    assert rb.wrap(f, name, 78, 2, whole_words=True)[-1].endswith("…")   # max_lines still ends in "…"
    assert rb.wrap(f, "short words only", 200, whole_words=True) == ["short words only"]


def test_placeholder_names_wrap_by_word(rb, fonts, monkeypatch):
    import pygame
    calls = []
    rb.patch(monkeypatch, "text", lambda c, f, s, *a, **k: calls.append(s) or pygame.Rect(0, 0, 1, 1))
    rb.placeholder(_canvas(rb), fonts, "Lorem Exercitation Consequat Kart", pygame.Rect(0, 0, 90, 120))
    assert calls and all(" " in s or s.endswith("…") or s in ("Lorem", "Kart") for s in calls)
    assert "Exercitati" not in calls


def test_scroll_text_shows_whole_lines_at_rest(rb, fonts):
    import pygame
    rect = pygame.Rect(100, 100, 300, 125)                               # not a multiple of the line height
    long = ("Lorem ipsum dolor sit amet consectetur. " * 12).strip()
    used = rb.scroll_text(_canvas(rb), fonts.small, long, rect, rb.DIM, line_h=20, bg=rb.BG, t=0.0)
    assert used.height == 120 and used.topleft == rect.topleft           # six lines, never a cut seventh
    used = rb.scroll_text(_canvas(rb), fonts.small, "Fits.", rect, rb.DIM, line_h=20, bg=rb.BG, t=0.0)
    assert used.height == 20                                             # one that fits is as tall as it is


def test_a_centred_panel_line_stays_centred(rb, fonts, monkeypatch):
    import pygame
    calls = []

    def fake_text(c, f, s, pos, color=None, anchor="topleft", maxw=None):
        calls.append((f, s, anchor, maxw))
        return pygame.Rect(pos[0], pos[1], 10, 10)
    monkeypatch.setattr(rb.layout, "text", fake_text)
    monkeypatch.setattr(rb.layout, "marquee", lambda *a, **k: calls.append(("marquee",)) or pygame.Rect(0, 0, 1, 1))
    meta = "Nintendo Entertainment System · 1999 · Role-Playing"
    w = int(rb.measure(fonts.small, meta)) + 2                           # the small face just fits, body doesn't
    p = rb.Panel(_canvas(rb), fonts, pygame.Rect(0, 0, w + 48, 400))
    p.line("Short", center=True, font=fonts.body)
    p.line(meta, center=True, font=fonts.body)
    p.line(meta + " and much more besides that", center=True, font=fonts.body)
    p.line(meta, center=False)
    assert calls[0][2] == "midtop" and calls[0][0] is fonts.body
    assert calls[1][2] == "midtop" and calls[1][3] == p.w and calls[1][0] is fonts.small       # a size down
    assert calls[2][2] == "midtop" and calls[2][3] == p.w and calls[2][0] is fonts.body        # still too long: "…"
    assert calls[3] == ("marquee",)                                      # a left line slides as before


def test_a_rows_grey_line_runs_under_a_bare_status(rb, fonts, monkeypatch):
    import pygame
    widths = {}

    def fake_marquee(c, f, s, pos, color=None, maxw=None, focused=True, anchor="topleft", key=None, bg=None,
                     slot=None, t=None):
        widths[slot] = maxw
        return pygame.Rect(pos[0], pos[1], 10, 10)
    monkeypatch.setattr(rb.layout, "marquee", fake_marquee)
    r = pygame.Rect(32, 100, 740, 58)
    rb.list_row(_canvas(rb), fonts, r, False, "Flaky Cart", "Checksum doesn't match RomM after 2 tries", "Failed",
                rb.RED, aside="", slot="dl")
    assert widths["dl-sub"] > widths["dl-label"]                         # nothing under the status: full width
    rb.list_row(_canvas(rb), fonts, r, False, "Ipsum", "roms/psx", "38 MB/s", rb.BLUE, aside=(0.4, rb.BLUE), slot="bar")
    assert widths["bar-sub"] == widths["bar-label"]                      # a progress bar under it: both stop short


def test_the_space_legend_goes_single_column_when_its_labels_dont_fit(rb, fonts):
    parts = [(key, col, lab, n) for (key, col, lab), n in zip(rb.theme.SPACE_PARTS, (5 << 30, 7 << 30, 40 << 20))] + \
        [("system", rb.SYSTEM_COL, "Batocera and saves", 1 << 40)]
    kw = dict(total=1600 << 30, free=600 << 30, keep=20 << 30)
    wide = rb.space_bar(_canvas(rb), fonts, (56, 128, 1168, 26), parts, cols=3, **kw)
    narrow = rb.space_bar(_canvas(rb), fonts, (640, 166, 560, 26), parts, cols=2, **kw)
    assert wide == 128 + 26 + 16 + 2 * 28                                # six items, three a row
    assert narrow == 166 + 26 + 16 + 6 * 28                              # one under the other


# ---- the game page

def test_the_games_note_fits_under_its_verb(rb, app, monkeypatch):
    rom = {"id": 1, "name": "Lorem", "platform_id": 7, "fs_name": "Lorem.zip", "fs_extension": "zip",
           "fs_size_bytes": 1 << 20}
    monkeypatch.setattr(app, "layout", lambda rom: {"convert": "CHD", "label": "CUE/BIN", "unpacked": 0,
                                                    "estimated": False, "discs": 1})
    notes = [rb.game_note(app, rom, ("remote", None)), rb.game_note(app, rom, ("installed", None))]
    assert notes == ["Saved to roms/psx, then CHD.", "Starts it in EmulationStation."]
    for n in notes:
        assert rb.measure(app.fonts.small, n) <= rb.game.SIDE.width


def test_the_your_romm_strip_follows_a_short_summary(rb, app, monkeypatch):
    rom = {"id": 1, "name": "Lorem", "platform_id": 7, "fs_name": "Lorem.zip", "fs_name_no_ext": "Lorem",
           "fs_extension": "zip", "fs_size_bytes": 1 << 20, "summary": "One short line.",
           "rom_user": {"id": 3, "user_id": 1, "backlogged": True},
           "merged_screenshots": ["/assets/romm/resources/roms/7/1/screenshots/%d.png" % k for k in range(3)]}
    monkeypatch.setattr(app, "detail", lambda rom: rom)
    scr = rb.GameScreen(app, rom)
    strips = []
    monkeypatch.setattr(scr, "draw_strip", lambda c, rec, r: strips.append(r.copy()))
    scr.draw(app.canvas)
    assert strips and strips[0].top < rb.game.COVER.bottom - scr.strip_height(rom)   # right under the summary
    rom["summary"] = "Lorem ipsum dolor sit amet consectetur adipiscing elit. " * 30
    scr.draw(app.canvas)
    assert strips[-1].top == rb.game.COVER.bottom - scr.strip_height(rom)            # a long one: at the foot


# ---- Activity

def test_a_failures_reason_reads_short_in_lately(rb):
    assert rb.short_reason("Misplaced.iso: not a GameCube/Wii disc image (dolphin-tool verify failed)") == \
        "not a GameCube/Wii disc image"
    assert rb.short_reason("Checksum doesn't match RomM after 2 tries") == "Checksum doesn't match RomM after 2 tries"
    assert rb.short_reason("Note: this one (keeps) its brackets") == "Note: this one (keeps) its brackets"
    assert rb.short_reason("Lorem (USA).cue: no such track (track 2)") == "no such track"
    assert rb.short_reason("") == ""


def test_activity_is_called_activity_everywhere(rb):
    assert [r[1] for r in rb.HomeScreen.RAIL if r[0] == "downloads"] == ["Activity"]
    src = open(os.path.join(os.path.dirname(rb.app.__file__), "app.py"), encoding="utf-8").read()
    assert '("SELECT", "Activity")' in src and '("SELECT", "Downloads")' not in src


def test_no_a_or_x_hint_is_called_options_or_menu(rb):
    """Options is PlayStation's Start and Menu is Xbox's: a hint saying "A Options" reads as two
    buttons. Every list's own A is named for what it does (Manage, Fix it, Open…)."""
    pat = re.compile(r'\("[AX]",\s*"(Options|Menu)"\)')
    root = os.path.dirname(rb.app.__file__)
    bad = [p for p in glob.glob(os.path.join(root, "**", "*.py"), recursive=True)
           if pat.search(open(p, encoding="utf-8").read())]
    assert bad == []


def test_game_night_actions_offer_the_page_and_say_where_the_switch_is(rb, app, monkeypatch):
    app.night_opts = {"players": 0, "here": True}
    gn = rb.GameNightScreen(app, rb.Scope.library(app))
    gn.picks, gn.sel, gn.why = [{"id": 1, "name": "Lorem", "platform_id": 7, "fs_size_bytes": 1}], 0, None
    labels = [e[0] for e in gn.menu_entries() if e]
    assert labels[:2] == ["Roll again", "Open the game page"]
    gn.open_page()
    assert isinstance(app.stack[-1], rb.GameScreen) and app.stack[-1].rom is gn.picks[0]
    gn.picks, gn.why = [], "empty"
    assert "Open the game page" not in [e[0] for e in gn.menu_entries() if e]
    assert gn.nothing()[1] == "Turn off %s in {X} Actions, or download a few first." % rb.gamenight.HERE
