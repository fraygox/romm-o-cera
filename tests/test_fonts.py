"""Game titles and descriptions come in every script: each character is drawn
in the first font that has it (Rubik, then the box's fallbacks), never as a
missing-glyph box when a font on the box has it. Batocera's own fonts aren't
in the test image; pygame's bundled FreeSans stands in for a fallback."""
import os

import pytest


@pytest.fixture
def fonts(rb, monkeypatch):
    import pygame
    pygame.font.init()
    rubik = os.path.join(rb.APP_DIR, "fonts", "Rubik-Regular.ttf")
    free = os.path.join(os.path.dirname(pygame.__file__), pygame.font.get_default_font())
    monkeypatch.setattr(rb, "_HAS", {})
    main, fb = pygame.font.Font(rubik, 20), pygame.font.Font(free, 20)
    rb.FONT_PATH.update({main: rubik, fb: free})
    rb.FALLBACK[main] = [fb]
    yield rb, main, fb, free
    rb.FALLBACK.pop(main, None)


def test_a_missing_glyph_is_told_from_a_real_one(fonts):
    rb, main, fb, free = fonts
    rubik = rb.FONT_PATH[main]
    assert rb._has_glyph(rubik, "A") and rb._has_glyph(rubik, "é") and rb._has_glyph(rubik, "ﬁ")   # ﬁ
    assert not rb._has_glyph(rubik, "Λ")                                   # Λ: Rubik has no Greek
    assert rb._has_glyph(free, "Λ") and not rb._has_glyph(free, "\U0001f938")
    assert rb._has_glyph(rubik, "́")                                      # a combining mark: no box


def test_each_character_goes_to_the_first_font_that_has_it(fonts):
    rb, main, fb, _ = fonts
    assert rb._runs(main, "Darius Plus") == [(main, "Darius Plus")]
    assert rb._runs(main, "Garfield ﬁ") == [(main, "Garfield ﬁ")]   # Rubik has the ligature
    assert rb._runs(main, "Λ Core") == [(fb, "Λ"), (main, " Core")]
    assert rb._runs(main, "Hyper \U0001f938") == [(main, "Hyper \U0001f938")]   # no font has it: Rubik's box
    img = rb.render(main, "Λ Core α", (255, 255, 255))
    assert img.get_height() == main.get_height()
    assert abs(rb.measure(main, "Λ Core α") * rb.K - img.get_width()) <= 1


def test_the_korean_font_is_used_for_hangul_only(fonts, monkeypatch):
    """Its other glyphs don't match Rubik (an emoji drew as a wrong symbol)."""
    rb, main, fb, free = fonts
    monkeypatch.setattr(rb, "HANGUL_FONTS", [free])
    assert not rb._has_glyph(free, "Λ") and rb._runs(main, "Λ") == [(main, "Λ")]
    assert rb._runs(main, "한") == [(main, "한")]      # FreeSans has no Hangul either


def test_without_fallback_fonts_text_still_draws(rb):
    import pygame
    pygame.font.init()
    f = pygame.font.Font(os.path.join(rb.APP_DIR, "fonts", "Rubik-Regular.ttf"), 20)
    assert rb._runs(f, "배틀 Λ") == [(f, "배틀 Λ")]   # never made by Fonts: as is
    assert rb.render(f, "배틀", (255, 255, 255)).get_width() > 0
