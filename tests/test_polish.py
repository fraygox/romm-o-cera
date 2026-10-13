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
