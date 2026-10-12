"""Controller styles: which glyph and label each hint token shows for the pad in hand
(or the keyboard). PAD_STYLE and INPUT_MODE change at run time: read them through this
module (pads.INPUT_MODE)."""
from rbui import theme
from rbui.theme import BTN_COL, light_theme, mix


# --- controller styles -------------------------------------------------------
# Hints name *actions* with Xbox-ish tokens ("A" confirm, "B" back, "X"/"Y" the
# west/north buttons, "L"/"R", "L2 R2", "START", "SELECT"). PAD_STYLE turns a
# token into what is printed on the user's controller, by physical position, so
# the glyphs stay right with any style and with the confirm-button swap.
PAD_STYLE = {"style": "xbox", "ok": "south", "back": "east"}
FACE = {
    "xbox": {"south": ("A", (52, 211, 153)), "east": ("B", (248, 113, 113)),
             "west": ("X", (96, 165, 250)), "north": ("Y", (251, 191, 36))},
    "nintendo": {"south": ("B", (251, 191, 36)), "east": ("A", (248, 113, 113)),
                 "west": ("Y", (74, 222, 128)), "north": ("X", (96, 165, 250))},
    "playstation": {"south": ("cross", (125, 167, 230)), "east": ("circle", (236, 112, 112)),
                    "west": ("square", (226, 140, 205)), "north": ("triangle", (95, 205, 160))},
}
OTHER = {
    "xbox": {"L": "LB", "R": "RB", "L2 R2": "LT RT", "START": "Menu", "SELECT": "View"},
    "playstation": {"L": "L1", "R": "R1", "L2 R2": "L2 R2", "START": "Options", "SELECT": "Create"},
    "nintendo": {"L": "L", "R": "R", "L2 R2": "ZL ZR", "START": "+", "SELECT": "−"},
}
STYLE_NAMES = {"xbox": "Xbox", "playstation": "PlayStation", "nintendo": "Nintendo"}
# While the keyboard is the last thing used, hints show key names instead.
INPUT_MODE = {"kb": False}
KEY_LABELS = {"A": "Enter", "B": "Esc", "X": "X", "Y": "Y", "L": "PgUp", "R": "PgDn",
              "L2 R2": "[ ]", "START": "Home", "SELECT": "Tab"}
TOKEN_ACTION = {"A": "ok", "B": "back", "X": "x", "Y": "y", "L": "l1", "R": "r1", "L2 R2": "r2",
                "START": "start", "SELECT": "select", "LEFT RIGHT": "right", "BKSP": "x"}
TOKEN_BACK = {"LEFT RIGHT": "left", "L2 R2": "l2"}      # a tap on the left half of these glyphs
# Every control shown on screen is one of these tokens, drawn by button_glyph():
# in hints as (token, label), in prose as "{token}" through rich_text/rich_lines.
TOKENS = ("A", "B", "X", "Y", "L", "R", "L2 R2", "START", "SELECT", "LEFT RIGHT", "TYPE", "BKSP")
# keyboard-only: typing letters and Backspace in text fields
KEY_ONLY = {"TYPE": "a–z", "BKSP": "Bksp"}


def style_from_names(names):
    """Guess the label style from controller names SDL reports."""
    for n in names:
        n = (n or "").lower()
        if any(k in n for k in ("dualsense", "dualshock", "playstation", "ps3", "ps4", "ps5", "sony")):
            return "playstation"
        if any(k in n for k in ("nintendo", "pro controller", "joy-con", "joycon", "switch")):
            return "nintendo"
    return "xbox"


# "Match theme to controller": the theme of the pad's maker (the button labels'
# style when they're set by hand); the chosen Theme for a keyboard or a pad it
# doesn't know, and always for a high-contrast Theme.
XBOX_NAMES = ("xbox", "x-box", "microsoft", "xinput", "x360")


def pad_family(names):
    """playstation, nintendo or xbox from controller names; None when there's
    no controller or its name says nothing (style_from_names would say xbox)."""
    st = style_from_names(names)
    if st != "xbox" or any(k in (n or "").lower() for n in names for k in XBOX_NAMES):
        return st
    return None


def theme_for(name, auto, button_style, names):
    """The Theme setting (and Match theme to controller) -> the THEMES key to paint with."""
    if not auto or name.startswith("high_contrast"):
        return name
    fam = button_style if button_style in FACE else pad_family(names)
    return fam + ("_oled" if name.endswith("_oled") else "") if fam else name


def pad_face(token):
    """(kind, text_or_shape, colour) for a hint token in the active style; on a light
    theme the controller's colours are deepened so they hold up on white."""
    kind, label, col = _pad_face(token)
    return kind, label, (mix(col, (0, 0, 0), 0.45) if light_theme() and col != theme.DIM else col)


def _pad_face(token):
    st = PAD_STYLE["style"]
    if token == "LEFT RIGHT":                 # D-pad left/right (arrow keys on a keyboard)
        return ("arrows" if INPUT_MODE["kb"] else "dpad"), "", theme.DIM
    if token in KEY_ONLY:
        return "pill", KEY_ONLY[token], theme.DIM
    if INPUT_MODE["kb"]:
        col = {"A": FACE["xbox"]["south"][1], "B": FACE["xbox"]["east"][1], "X": FACE["xbox"]["west"][1],
               "Y": FACE["xbox"]["north"][1]}.get(token, BTN_COL.get(token, theme.DIM))
        return "pill", KEY_LABELS.get(token, token), col
    pos = {"A": PAD_STYLE["ok"], "B": PAD_STYLE["back"], "X": "west", "Y": "north"}.get(token)
    if pos:
        lab, col = FACE[st][pos]
        return ("shape" if st == "playstation" else "letter"), lab, col
    return "pill", OTHER[st].get(token, token), BTN_COL.get(token, theme.DIM)


def pad_text(token):
    """Plain-text name of a button for prose ("Press Options to …")."""
    kind, lab, _ = pad_face(token)
    if kind in ("dpad", "arrows"):
        return "Left/Right"
    return {"cross": "Cross", "circle": "Circle", "square": "Square", "triangle": "Triangle"}.get(lab, lab)
