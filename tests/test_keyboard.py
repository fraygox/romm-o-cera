"""The one on-screen keyboard (rbui/widgets/keyboard.py) and the screens typed on it."""
import re

import pytest

import rommlib


@pytest.fixture
def app(isolated, rb, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    app = rb.App(None, headless=True)
    app.bg = lambda fn, done=None, err=None: None
    app.push(rb.HomeScreen(app))
    return app


def _press(kb, label):
    """Focus the key with that label and press it."""
    for r, row in enumerate(kb.keys()):
        for c, k in enumerate(row):
            if k.label == label:
                kb.r, kb.c = r, c
                return kb.action("ok")
    raise AssertionError("no %r key" % label)


def _kinds(kb, r=4):
    return [k.kind for k in kb.keys()[r]]


def test_shift_and_the_symbols_layer_keep_the_digit_row(rb):
    kb = rb.Keyboard("Save")
    assert kb.char_rows()[1] == "qwertyuiop"
    _press(kb, "q")
    kb.action("y")                                    # Y is Shift here
    assert kb.shift and kb.char_rows()[0] == "1234567890" and kb.char_rows()[1] == "QWERTYUIOP"
    _press(kb, "Q")
    assert kb.shift                                   # Shift stays on until pressed again
    kb.action("l1")                                   # L: the symbols layer
    assert kb.sym and kb.char_rows()[0] == "1234567890" and "@" in kb.char_rows()[1]
    assert [k.label for k in kb.keys()[4] if k.kind == "sym"] == ["abc"]
    _press(kb, "@")
    _press(kb, "5")
    kb.toggle_shift()                                 # Shift on the symbols: back to letters
    assert not kb.sym and kb.shift
    assert kb.value == "qQ@5"


def test_every_printable_ascii_character_is_on_a_layer(rb):
    """A URL or token needs nothing the keyboard can't type."""
    kb = rb.Keyboard("Save")
    have = set()
    for sym, shift in ((False, False), (False, True), (True, False)):
        kb.sym, kb.shift = sym, shift
        have |= set("".join(kb.char_rows()))
    assert set(map(chr, range(33, 127))) - have == set()


def test_focus_wraps_and_moves_to_the_key_below(rb):
    kb = rb.Keyboard("Search", context="search")
    assert kb.focus.label == "q"
    kb.action("left")
    assert kb.focus.label == "&"                      # left from the first key: the row's context key
    kb.action("right")
    assert kb.focus.label == "q"
    kb.action("up")
    kb.action("up")
    assert kb.r == 4 and kb.focus.kind == "shift"     # past the digits: the action row, wrapping
    kb.action("down")
    assert kb.r == 0 and kb.focus.label == "1"
    kb.r, kb.c = 3, 4                                 # b
    kb.action("down")
    assert kb.focus.kind == "space"                   # the key under it, not the same index
    kb.r, kb.c = 4, _kinds(kb).index("primary")
    kb.action("right")
    assert kb.focus.kind == "context"


def test_context_keys_type_whole_strings(rb):
    kb = rb.Keyboard("Save", context="url")
    assert [row[-1].label for row in kb.keys()] == ["https://", ":", "/", ".", "-"]
    _press(kb, "https://")
    _press(kb, "r")
    _press(kb, ".")
    assert kb.value == "https://r."
    custom = rb.Keyboard("Go", context=["a", "b", "c", "d", "e", "f"])
    assert sum(k.kind == "context" for row in custom.keys() for k in row) == 5
    assert rb.Keyboard("Save").units == 10            # no context: the grid alone


def test_pair_code_mode_types_only_what_a_code_accepts(rb):
    kb = rb.Keyboard("Pair", mode="code", context="url")
    assert not any(k.kind == "context" for row in kb.keys() for k in row)
    assert _kinds(kb) == ["delete", "clear", "primary"]
    for r, row in enumerate(kb.keys()[:4]):
        for c in range(len(row)):
            kb.r, kb.c = r, c
            kb.action("ok")
    assert re.fullmatch(r"[A-Z0-9]+", kb.value) and len(kb.value) == 36
    kb.value = ""
    kb.type_text("ab-cd 2!x")                         # a hardware keyboard: upper case, the rest dropped
    assert kb.value == "ABCD2X"
    kb.action("y")
    kb.action("l1")
    assert not kb.shift and not kb.sym
    kb.type_text("345678")
    assert rommlib.normalize_pair_code(kb.value[:8]) == "ABCD2X34"


def test_delete_clear_submit_and_the_cap(rb):
    done = []
    changes = []
    kb = rb.Keyboard("Go", on_submit=lambda: done.append(kb.value), value="abc", maxlen=5,
                     on_change=lambda: changes.append(kb.value))
    kb.type_text("defgh\x07")
    assert kb.value == "abcde"
    kb.action("x")
    assert kb.value == "abcd"
    kb.r, kb.c = 4, _kinds(kb).index("clear")
    kb.action("ok")
    assert kb.value == "" and changes == ["abcde", "abcd", ""]
    kb.type_text("hi")
    kb.action("r1")
    kb.action("start")
    kb.r, kb.c = 4, _kinds(kb).index("primary")
    kb.action("ok")
    assert done == ["hi"] * 3
    assert kb.action("back") is False                 # the screen's to handle


def test_y_and_the_hints_follow_the_caller(rb, monkeypatch):
    monkeypatch.setitem(rb.INPUT_MODE, "kb", False)
    kb = rb.Keyboard("Search", y_key="space")
    kb.action("y")
    assert kb.value == " " and ("Y", "Space") in kb.hints() and ("START", "Search") in kb.hints()
    assert ("Y", "Shift") in rb.Keyboard("Save").hints()
    assert not any(t == "Y" for t, _ in rb.Keyboard("Pair", mode="code").hints())
    monkeypatch.setitem(rb.INPUT_MODE, "kb", True)
    assert kb.hints() == [("TYPE", "Letters"), ("BKSP", "Delete"), ("A", "Search")]


def test_keys_fill_the_rect_and_taps_press_them(app, rb):
    import pygame
    touched = []
    kb = rb.Keyboard("Save", context="url", on_touch=lambda: touched.append(1))
    rect = pygame.Rect(40, 100, 760, 372)
    hits = []
    kb.draw(app.canvas, app.fonts, rect, hit=lambda r, fn: hits.append((r, fn)))
    rects = [r for r, _ in hits]
    assert len(rects) == sum(len(row) for row in kb.keys())
    assert all(rect.contains(r) for r in rects)
    assert not any(a.colliderect(b) for i, a in enumerate(rects) for b in rects[i + 1:])
    assert max(r.right for r in rects) == rect.right and max(r.bottom for r in rects) == rect.bottom
    fn = next(fn for r, fn in hits if r.collidepoint(rect.right - 5, rect.top + 5))
    fn()
    assert kb.value == "https://" and touched == [1] and kb.focus.label == "https://"
    assert kb.row_at(kb.row_mid(rect), rect) == kb.r


# ---- the screens on it

def test_url_field_types_saves_and_cancels(app, rb, monkeypatch):
    saved = []
    sc = rb.TextInputScreen(app, "RomM server URL", "https://", saved.append, context="url", help="Where it is.")
    app.push(sc)
    sc.type_text("romm.lan ")
    _press(sc.kb, ":")
    sc.type_text("8080")
    app.draw()
    assert sc.text_entry and sc.crumbs() == ["Settings", "RomM server URL"]
    app.dispatch("start")
    assert saved == ["https://romm.lan :8080".strip()] and app.stack[-1] is not sc
    sc = rb.TextInputScreen(app, "RomM server URL", "x", saved.append)
    app.push(sc)
    sc.action("x")
    sc.action("back")
    assert app.stack[-1] is not sc and len(saved) == 1


def test_token_field_never_draws_the_token(app, rb, monkeypatch):
    tok = "rmm_" + "a1b2c3d4" * 4
    drawn = []
    real = rb.text
    rb.patch(monkeypatch, "text", lambda surf, font, s, *a, **k: drawn.append(s) or real(surf, font, s, *a, **k))
    sc = rb.TextInputScreen(app, "RomM client API token", tok, lambda v: None, secret=True, context="token")
    app.push(sc)
    app.draw()
    assert not any(tok in (s or "") for s in drawn)
    assert any(s == rb.mask(tok) for s in drawn) and "rmm_" in [k.label for row in sc.kb.keys() for k in row]


def test_settings_fields_use_the_keyboard_modes(app, rb):
    app.cfg["url"] = "https://romm.example.com"
    scr = rb.SettingsScreen(app)
    app.push(scr)
    scr.pair()
    kb = app.stack[-1]
    assert isinstance(kb, rb.TextInputScreen) and kb.kb.mode == "code" and kb.kb.primary == "Pair"
    assert "{A}" not in (kb.help or "x")
    kb.type_text("abcd2345")
    assert kb.shown() == "ABCD-2345"
    app.draw()
