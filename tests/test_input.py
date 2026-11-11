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


# --- Batocera's pad mappings (es_input.cfg) -> SDL -----------------------------
# The Steam Deck's entry as Batocera 43 ships it (the hotkey is Select again).
DECK = """<?xml version="1.0"?>
<inputList>
  <inputConfig type="keyboard" deviceName="Keyboard" deviceGUID="-1">
    <input name="a" type="key" id="27" value="1" />
  </inputConfig>
  <inputConfig type="joystick" deviceName="Steam Deck" deviceGUID="03000000de2800000512000011010000">
    <input name="a" type="button" id="4" value="1" code="305" />
    <input name="b" type="button" id="3" value="1" code="304" />
    <input name="down" type="button" id="17" value="1" code="545" />
    <input name="hotkey" type="button" id="11" value="1" code="314" />
    <input name="joystick1left" type="axis" id="0" value="-1" code="0" />
    <input name="joystick1up" type="axis" id="1" value="-1" code="1" />
    <input name="l2" type="axis" id="2" value="1" code="2" />
    <input name="left" type="button" id="18" value="1" code="546" />
    <input name="pagedown" type="button" id="8" value="1" code="311" />
    <input name="pageup" type="button" id="7" value="1" code="310" />
    <input name="r2" type="axis" id="5" value="1" code="5" />
    <input name="right" type="button" id="19" value="1" code="547" />
    <input name="select" type="button" id="11" value="1" code="314" />
    <input name="start" type="button" id="12" value="1" code="315" />
    <input name="up" type="button" id="16" value="1" code="544" />
    <input name="x" type="button" id="6" value="1" code="308" />
    <input name="y" type="button" id="5" value="1" code="307" />
  </inputConfig>
</inputList>
"""


def _binds(line):
    guid, name, *rest = line.rstrip(",").split(",")
    return guid, name, dict(b.split(":", 1) for b in rest)


def test_a_pad_batocera_knows_is_mapped_by_position(rb, tmp_path):
    """ES's b is the bottom button (SDL's a), its x the top one (SDL's y)."""
    f = tmp_path / "es_input.cfg"
    f.write_text(DECK)
    (line,) = rb.es_pad_mappings((str(f),))
    guid, name, b = _binds(line)
    assert (guid, name, b["platform"]) == ("03000000de2800000512000011010000", "Steam Deck", "Linux")
    assert (b["a"], b["b"], b["x"], b["y"]) == ("b3", "b4", "b5", "b6")
    assert (b["dpup"], b["dpdown"], b["dpleft"], b["dpright"]) == ("b16", "b17", "b18", "b19")
    assert (b["leftshoulder"], b["rightshoulder"], b["back"], b["start"]) == ("b7", "b8", "b11", "b12")
    assert (b["leftx"], b["lefty"], b["lefttrigger"], b["righttrigger"]) == ("a0", "a1", "a2", "a5")
    assert "guide" not in b                      # the hotkey is Select: one SDL button per pad button


def test_the_players_own_mapping_wins_and_odd_ones_translate(rb, tmp_path):
    mine, system = tmp_path / "mine.cfg", tmp_path / "system.cfg"
    mine.write_text("""<inputList><inputConfig type="joystick" deviceName="Odd, Pad"
        deviceGUID="03000000DE2800000512000011010000">
      <input name="b" type="button" id="0" value="1" />
      <input name="hotkey" type="button" id="9" value="1" />
      <input name="up" type="hat" id="0" value="1" />
      <input name="left" type="axis" id="6" value="-1" />
      <input name="joystick1up" type="axis" id="1" value="1" />
      <input name="l2" type="axis" id="2" value="-1" />
      <input name="r2" type="button" id="7" value="1" />
      <input name="a" type="key" id="13" value="1" />
      <input name="y" type="button" id="" value="1" />
    </inputConfig></inputList>""")
    system.write_text(DECK)
    (line,) = rb.es_pad_mappings((str(tmp_path / "missing.cfg"), str(mine), str(system)))
    guid, name, b = _binds(line)
    assert (guid, name) == ("03000000de2800000512000011010000", "Odd. Pad")
    assert b == {"platform": "Linux", "a": "b0", "guide": "b9", "dpup": "h0.1", "dpleft": "-a6",
                 "lefty": "a1~", "lefttrigger": "a2~", "righttrigger": "b7"}


def test_a_broken_or_missing_mapping_file_changes_nothing(rb, tmp_path, monkeypatch):
    bad = tmp_path / "bad.cfg"
    bad.write_text("<inputList><inputConfig")
    monkeypatch.setenv("SDL_GAMECONTROLLERCONFIG", "theirs")
    assert rb.use_es_pad_mappings((str(bad), str(tmp_path / "none.cfg"))) == 0
    assert rb.os.environ["SDL_GAMECONTROLLERCONFIG"] == "theirs"


def test_a_mapping_already_given_to_sdl_stays_on_top(rb, tmp_path, monkeypatch):
    f = tmp_path / "es_input.cfg"
    f.write_text(DECK)
    monkeypatch.setenv("SDL_GAMECONTROLLERCONFIG", "theirs")
    assert rb.use_es_pad_mappings((str(f),)) == 1
    first, last = rb.os.environ["SDL_GAMECONTROLLERCONFIG"].split("\n")
    assert first.startswith("03000000de2800000512000011010000,Steam Deck,") and last == "theirs"


def test_pads_sharing_a_guid_take_the_entry_named_like_the_one_plugged_in(rb, tmp_path):
    f = tmp_path / "es_input.cfg"
    f.write_text("""<inputList>
      <inputConfig type="joystick" deviceName="Pad one" deviceGUID="0000000010ba00007ac3000000000000">
        <input name="b" type="button" id="0" value="1" /></inputConfig>
      <inputConfig type="joystick" deviceName="Pad two" deviceGUID="0000000010ba00007ac3000000000000">
        <input name="b" type="button" id="5" value="1" /></inputConfig>
    </inputList>""")
    assert [_binds(l)[1:] for l in rb.es_pad_mappings((str(f),))] == [("Pad one", {"platform": "Linux", "a": "b0"})]
    assert [_binds(l)[1:] for l in rb.es_pad_mappings((str(f),), names=["Pad two"])] == \
        [("Pad two", {"platform": "Linux", "a": "b5"})]
    assert [_binds(l)[1] for l in rb.es_pad_mappings((str(f),), names=["Pad one", "Pad two"])] == ["Pad one"]


def test_the_log_says_how_each_pad_is_read(rb):
    import pygame
    pygame.init()
    inp = rb.Input(False)
    assert inp.describe() == ["pad: none connected"]         # the test box has no pad
