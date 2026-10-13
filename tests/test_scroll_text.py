"""Text longer than its box scrolls instead of being cut: scroll_text() (prose,
vertical) and marquee() (a focused one-line label, sideways) wait, move at a
calm pace, wait at the end and start over; they don't move when the text fits,
start over when the text changes, and never draw outside their box."""
import pytest

LONG = ("This is a long help text that cannot possibly fit in a small box. " * 6).strip()


@pytest.fixture
def fonts(rb, monkeypatch, request):
    import pygame
    pygame.init()
    k = getattr(request, "param", 1.0)
    monkeypatch.setattr(rb.theme, "K", k)
    f = rb.Fonts()
    monkeypatch.setitem(rb.draw.FONTS, "f", f)
    monkeypatch.setattr(rb.draw, "_SCROLLS", {})
    return f


def _canvas(rb):
    import pygame
    c = pygame.Surface((rb.S(rb.W), rb.S(rb.H)))
    c.fill(rb.BG)
    return c


def _outside_untouched(rb, c, rect):
    """Every pixel outside rect (logical) is still the background."""
    import pygame
    probe = c.copy()
    probe.fill(rb.BG, rb.SR(rect))
    return pygame.image.tobytes(probe, "RGB") == pygame.image.tobytes(_canvas(rb), "RGB")


def _frames(draw, times):
    """Draw every 0.1 s like the app's frames (a slot not drawn for half a second starts over)
    and return the shot at each of times."""
    out, t, end = {}, 0.0, max(times)
    while t <= end + 1e-9:
        shot = draw(round(t, 2))
        if any(abs(t - x) < 1e-6 for x in times):
            out[min(times, key=lambda x: abs(t - x))] = shot
        t += 0.1
    return [out[x] for x in times]


def test_scroll_offset_waits_scrolls_holds_and_starts_over(rb):
    off = rb.scroll_offset
    sp, wait = rb.draw.SCROLL_SPEED, rb.draw.SCROLL_WAIT
    assert off(0, 100) == off(wait - 0.01, 100) == 0                       # still at the top
    assert off(wait + 1, 100) == pytest.approx(sp)                          # a calm glide
    run = 100 / sp
    assert off(wait + run + 0.1, 100) == 100                                # the end, held
    assert off(wait + run + wait - 0.01, 100) == 100
    assert off(wait + run + wait + 0.01, 100) == 0                          # back to the top
    assert off(2 * (wait + run + wait) + wait + 1, 100) == pytest.approx(sp)   # and round again
    assert all(off(t, 0) == 0 for t in (0, 1, 5, 50))                       # fits: never moves
    assert all(off(t, -20) == 0 for t in (0, 5, 50))


def test_a_new_text_or_key_starts_over_and_a_slot_off_screen_does_too(rb, monkeypatch):
    monkeypatch.setattr(rb.draw, "_SCROLLS", {})
    e = rb.scroll_elapsed
    assert e("help", ("a", 1), 10.0) == 0
    for t in (10.2, 10.4, 10.6, 10.8, 11.0):
        e("help", ("a", 1), t)
    assert e("help", ("a", 1), 11.2) == pytest.approx(1.2)
    assert e("help", ("b", 1), 11.4) == 0                                   # the text changed
    assert e("help", ("b", 1), 11.6) == pytest.approx(0.2)
    assert e("help", ("b", 2), 11.8) == 0                                   # the key (selection) changed
    assert e("help", ("b", 2), 15.0) == 0                                   # not drawn for a while: from the top


@pytest.mark.parametrize("fonts", [1.0, 1.5, 3.0], indirect=True)
def test_long_text_scrolls_inside_its_box_and_never_outside(rb, fonts):
    import pygame
    rect = pygame.Rect(200, 150, 300, 120)

    def draw(t):
        c = _canvas(rb)
        used = rb.scroll_text(c, fonts.body, LONG, rect, rb.TEXT, bg=rb.BG, t=t)
        assert used.height == rect.height                                  # full: it uses the whole box
        if t in (0.0, 0.5, 1.9, 3.5, 6.0):
            assert _outside_untouched(rb, c, rect), t
        return pygame.image.tobytes(c, "RGB")
    shots = _frames(draw, (0.0, 0.5, 1.9, 3.5, 6.0))
    assert shots[0] == shots[1] == shots[2]                                 # waiting at the top
    assert shots[3] != shots[2] and shots[4] != shots[3]                    # then moving
    assert not rb.scroll_fits(fonts.body, LONG, rect.width, rect.height)


@pytest.mark.parametrize("fonts", [1.0, 3.0], indirect=True)
def test_text_that_fits_never_moves(rb, fonts):
    import pygame
    rect = pygame.Rect(200, 150, 400, 120)
    assert rb.scroll_fits(fonts.body, "Short and sweet.", rect.width, rect.height)
    shots = set()
    for t in (0.0, 3.0, 9.0, 30.0):
        c = _canvas(rb)
        used = rb.scroll_text(c, fonts.body, "Short and sweet.", rect, rb.TEXT, bg=rb.BG, t=t)
        assert used.height < rect.height
        shots.add(pygame.image.tobytes(c, "RGB"))
    assert len(shots) == 1


def test_scroll_text_draws_glyphs_and_list_paragraphs(rb, fonts):
    import pygame
    rect = pygame.Rect(100, 100, 360, 60)
    c = _canvas(rb)
    rb.scroll_text(c, fonts.body, ["Press {A} to pick.", LONG], rect, rb.TEXT, bg=rb.BG, t=0)
    assert _outside_untouched(rb, c, rect)
    assert rb.wrapped(fonts.body, "Press {A} to pick.", 360) == ["Press {A} to pick."]


def test_a_changed_text_starts_at_the_top_again(rb, fonts):
    import pygame
    rect = pygame.Rect(200, 150, 300, 80)
    frames = {}
    t = 0.0
    while t < 5.0:                                       # drawn every frame, like the app does
        c = _canvas(rb)
        rb.scroll_text(c, fonts.body, LONG, rect, rb.TEXT, bg=rb.BG, t=t, key="row 1")
        frames[round(t, 1)] = pygame.image.tobytes(c, "RGB")
        t += 0.1
    c = _canvas(rb)
    rb.scroll_text(c, fonts.body, LONG, rect, rb.TEXT, bg=rb.BG, t=5.0, key="row 1")
    moved = pygame.image.tobytes(c, "RGB")
    assert moved != frames[0.0]
    c = _canvas(rb)
    rb.scroll_text(c, fonts.body, LONG, rect, rb.TEXT, bg=rb.BG, t=5.1, key="row 2")   # the selection moved
    assert pygame.image.tobytes(c, "RGB") == frames[0.0]


@pytest.mark.parametrize("fonts", [1.0, 1.5, 3.0], indirect=True)
def test_marquee_slides_only_the_focused_label_and_stays_in_its_box(rb, fonts):
    import pygame
    label = "A very long menu sub-line that is cut short on a narrow row"
    box = pygame.Rect(100, 200, 200, 30)

    def shot(focused, t):
        c = _canvas(rb)
        r = rb.marquee(c, fonts.small, label, (box.left, box.top), rb.TEXT, maxw=box.width,
                       focused=focused, bg=rb.BG, t=t)
        assert r.width <= box.width + 1
        assert _outside_untouched(rb, c, pygame.Rect(box.left, box.top, box.width + 1, 30)), (focused, t)
        return pygame.image.tobytes(c, "RGB")

    unfocused = {shot(False, t) for t in (0.0, 3.0, 8.0)}
    assert len(unfocused) == 1                                        # keeps its "…", never moves
    a, b, d = _frames(lambda t: shot(True, t), (0.0, 1.0, 3.0))
    assert a == b != d                                                # waits, then slides
    c = _canvas(rb)
    rb.marquee(c, fonts.small, "Short", (box.left, box.top), rb.TEXT, maxw=box.width, focused=True, t=9.0)
    c2 = _canvas(rb)
    rb.text(c2, fonts.small, "Short", (box.left, box.top), rb.TEXT)
    assert pygame.image.tobytes(c, "RGB") == pygame.image.tobytes(c2, "RGB")   # fits: plain text
