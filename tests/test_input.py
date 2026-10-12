"""Keyboard input: a key press waits KEY_HOLD before it counts (a pad's
keyboard echo may follow it). A quick tap, let go within that wait, moved
once and then kept repeating by itself: its release arrived before the press
counted, so nothing stopped the repeat."""
import pytest


@pytest.fixture
def kb(rb, monkeypatch):
    import pygame
    pygame.init()
    clock = [1000.0]
    monkeypatch.setattr(rb.time, "time", lambda: clock[0])
    monkeypatch.setitem(rb.INPUT_MODE, "kb", rb.INPUT_MODE.get("kb", False))   # a key press sets it
    inp = rb.Input(False)

    def key(kind, k):
        out = []
        inp.handle(pygame.event.Event(kind, key=k, mod=0, unicode=""), out)
        return out

    def run(seconds, step=0.01):
        out = []
        end = clock[0] + seconds
        while clock[0] < end:
            clock[0] += step
            inp.repeats(out)
        return out
    return pygame, key, run


def test_a_quick_tap_moves_once(kb):
    pygame, key, run = kb
    key(pygame.KEYDOWN, pygame.K_DOWN)
    key(pygame.KEYUP, pygame.K_DOWN)                 # let go before KEY_HOLD
    assert run(2.0) == ["down"]


def test_a_held_key_repeats_until_let_go(kb):
    pygame, key, run = kb
    key(pygame.KEYDOWN, pygame.K_DOWN)
    held = run(1.0)
    assert held[0] == "down" and len(held) > 5
    key(pygame.KEYUP, pygame.K_DOWN)
    assert run(1.0) == []


def test_taps_in_a_row_move_once_each(kb):
    pygame, key, run = kb
    moves = []
    for _ in range(5):
        key(pygame.KEYDOWN, pygame.K_UP)
        key(pygame.KEYUP, pygame.K_UP)
        moves += run(0.2)
    moves += run(2.0)
    assert moves == ["up"] * 5
