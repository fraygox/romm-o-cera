"""Scaling (logical layout units -> screen pixels) and the colour palette with its themes.

apply_theme() rebinds the palette names in this module: read them as theme.BG (never
`from rbui.theme import BG`, which would keep the colour of the theme at import)."""
import colorsys

import pygame


W, H = 1280, 720      # logical layout units; drawn natively at W*K x H*K
FPS = 60
K = 1.0               # physical px per logical unit (3.0 on a 4K TV), set at startup


def S(v):
    return int(round(v * K))


def SR(r):
    r = pygame.Rect(r)
    return pygame.Rect(S(r.x), S(r.y), max(1, S(r.width)), max(1, S(r.height)))


def LR(r):
    """physical rect -> logical rect (what layout code reasons in)"""
    return pygame.Rect(int(r.x / K), int(r.y / K), int(round(r.width / K)), int(round(r.height / K)))

# palette (RomM-ish violet on near-black)
# Design tokens (2026-10-10 sweep): deep indigo "ink" instead of neutral black
# (calmer on a TV, ties to RomM's violet), one focus language (violet outline
# on a lifted panel), status colours used for status only.
BG = (21, 19, 42)          # ink        #15132A
BG2 = (28, 25, 54)         # recess     #1C1936
PANEL = (33, 30, 64)       # panel      #211E40
PANEL_HI = (47, 42, 92)    # focus fill #2F2A5C
LINE = (61, 55, 108)       # rule       #3D376C
TEXT = (242, 239, 255)     # text       #F2EFFF
DIM = (171, 165, 216)      # secondary  #ABA5D8
FAINT = (145, 138, 196)    # tertiary   #918AC4 (5.7:1 on BG: readable from the couch)
ACCENT = (139, 92, 246)    # RomM violet #8B5CF6
ACCENT_HI = (183, 160, 255)
GREEN = (74, 222, 128)
AMBER = (251, 191, 36)
RED = (248, 113, 113)
BLUE = (96, 165, 250)
# controller face buttons keep their own colours in every theme; L/R/START/SELECT use DIM
BTN_COL = {"A": (52, 211, 153), "B": (248, 113, 113), "X": (96, 165, 250), "Y": (251, 191, 36)}

# Themes (Settings → Theme): each one hue carried through the neutrals (ink, panels,
# rules, secondary text are tinted toward it) with the system's colour as the accent.
# OLED versions put everything on pure black; panels stay faintly lifted so rows keep
# their shape. A status colour that would read as the accent moves (Kyoto: danger
# is coral, never the red focus; PlayStation: "working" is cyan; Xbox: "ok" is mint).
# Contrast floors (test_themes), Default's own levels: TEXT 12:1, DIM 6.5:1, FAINT 4.5:1 on BG/BG2/PANEL.
_OLED = dict(BG=(0, 0, 0))
THEMES = {
    "default": ("Default", {}),
    "default_oled": ("Default OLED", dict(_OLED, BG2=(12, 10, 24), PANEL=(20, 17, 40), PANEL_HI=(42, 35, 88),
                                          LINE=(48, 42, 88))),
    "playstation": ("PlayStation", dict(
        BG=(8, 18, 40), BG2=(12, 26, 56), PANEL=(17, 35, 74), PANEL_HI=(24, 62, 128), LINE=(40, 70, 126),
        TEXT=(238, 244, 255), DIM=(164, 186, 224), FAINT=(134, 160, 206), ACCENT=(0, 112, 209),
        ACCENT_HI=(118, 182, 255), BLUE=(80, 206, 232))),
    "playstation_oled": ("PlayStation OLED", dict(
        _OLED, BG2=(6, 12, 26), PANEL=(10, 22, 48), PANEL_HI=(18, 52, 112), LINE=(30, 52, 96),
        TEXT=(238, 244, 255), DIM=(164, 186, 224), FAINT=(134, 160, 206), ACCENT=(0, 112, 209),
        ACCENT_HI=(118, 182, 255), BLUE=(80, 206, 232))),
    "xbox": ("Xbox", dict(
        BG=(14, 17, 14), BG2=(21, 25, 21), PANEL=(29, 34, 29), PANEL_HI=(24, 66, 24), LINE=(52, 62, 52),
        TEXT=(238, 245, 238), DIM=(172, 188, 172), FAINT=(142, 158, 142), ACCENT=(16, 124, 16),
        ACCENT_HI=(136, 224, 96), GREEN=(80, 220, 190))),
    "xbox_oled": ("Xbox OLED", dict(
        _OLED, BG2=(8, 11, 8), PANEL=(16, 21, 16), PANEL_HI=(18, 56, 18), LINE=(38, 48, 38),
        TEXT=(238, 245, 238), DIM=(172, 188, 172), FAINT=(142, 158, 142), ACCENT=(16, 124, 16),
        ACCENT_HI=(136, 224, 96), GREEN=(80, 220, 190))),
    "nintendo": ("Kyoto", dict(
        BG=(28, 24, 26), BG2=(38, 32, 35), PANEL=(48, 41, 44), PANEL_HI=(110, 22, 30), LINE=(76, 64, 68),
        TEXT=(250, 245, 245), DIM=(210, 192, 194), FAINT=(182, 164, 168), ACCENT=(230, 0, 18),
        ACCENT_HI=(255, 128, 134), RED=(255, 150, 84))),
    "nintendo_oled": ("Kyoto OLED", dict(
        _OLED, BG2=(12, 9, 10), PANEL=(22, 17, 19), PANEL_HI=(92, 14, 22), LINE=(54, 42, 46),
        TEXT=(250, 245, 245), DIM=(210, 192, 194), FAINT=(178, 160, 164), ACCENT=(230, 0, 18),
        ACCENT_HI=(255, 128, 134), RED=(255, 150, 84))),
    # High contrast: pure black and white, a yellow focus; amber turns orange so it
    # never reads as the focus
    "high_contrast": ("High contrast", dict(
        BG=(0, 0, 0), BG2=(0, 0, 0), PANEL=(16, 16, 16), PANEL_HI=(70, 58, 0), LINE=(170, 170, 170),
        TEXT=(255, 255, 255), DIM=(235, 235, 235), FAINT=(205, 205, 205), ACCENT=(255, 214, 0),
        ACCENT_HI=(255, 236, 120), GREEN=(90, 255, 150), AMBER=(255, 120, 0), RED=(255, 96, 96),
        BLUE=(110, 205, 255))),
    "high_contrast_light": ("High contrast light", dict(
        BG=(255, 255, 255), BG2=(238, 238, 238), PANEL=(246, 246, 246), PANEL_HI=(210, 224, 255),
        LINE=(80, 80, 80), TEXT=(0, 0, 0), DIM=(34, 34, 34), FAINT=(70, 70, 70), ACCENT=(0, 56, 200),
        ACCENT_HI=(0, 44, 150), GREEN=(0, 112, 44), AMBER=(140, 78, 0), RED=(184, 0, 28),
        BLUE=(0, 112, 128))),
    # consoles, screens and moods, classic palettes (2026-10-12): each nudged to the same floors
    "dmg": ("Brick", dict(BG=(8, 29, 9), BG2=(11, 36, 11), PANEL=(16, 44, 16), PANEL_HI=(42, 84, 41),
        LINE=(48, 98, 48), TEXT=(224, 240, 184), DIM=(180, 210, 122), FAINT=(139, 172, 15),
        ACCENT=(139, 172, 15), ACCENT_HI=(200, 224, 112), GREEN=(111, 224, 208), AMBER=(240, 192, 64),
        RED=(255, 140, 120), BLUE=(140, 200, 255))),
    "virtualboy_oled": ("Red Visor OLED", dict(_OLED, BG2=(12, 0, 0), PANEL=(24, 3, 3), PANEL_HI=(90, 10, 10),
        LINE=(74, 16, 16), TEXT=(255, 216, 216), DIM=(255, 154, 154), FAINT=(224, 106, 106),
        ACCENT=(232, 16, 28), ACCENT_HI=(255, 112, 112), GREEN=(112, 224, 160), AMBER=(255, 192, 64),
        RED=(255, 154, 64), BLUE=(128, 192, 255))),
    "famicom": ("Red & White", dict(BG=(42, 20, 22), BG2=(52, 26, 28), PANEL=(66, 34, 36),
        PANEL_HI=(122, 42, 46), LINE=(92, 52, 54), TEXT=(251, 241, 223), DIM=(230, 207, 176),
        FAINT=(201, 171, 136), ACCENT=(192, 40, 58), ACCENT_HI=(255, 154, 162), GREEN=(126, 224, 160),
        AMBER=(242, 193, 78), RED=(255, 160, 96), BLUE=(140, 196, 255))),
    "snes": ("Lavender", dict(BG=(26, 26, 33), BG2=(32, 32, 40), PANEL=(39, 39, 48), PANEL_HI=(74, 60, 122),
        LINE=(71, 71, 87), TEXT=(242, 242, 248), DIM=(198, 198, 212), FAINT=(162, 162, 182),
        ACCENT=(110, 84, 200), ACCENT_HI=(184, 168, 255))),
    "megadrive": ("Mega Drive", dict(BG=(12, 12, 14), BG2=(20, 20, 23), PANEL=(28, 28, 33),
        PANEL_HI=(92, 16, 24), LINE=(52, 52, 60), TEXT=(244, 244, 246), DIM=(196, 196, 204),
        FAINT=(154, 154, 166), ACCENT=(216, 32, 44), ACCENT_HI=(255, 128, 136), RED=(255, 154, 80),
        BLUE=(74, 168, 255))),
    "dreamcast": ("Dreamcast", dict(BG=(246, 245, 242), BG2=(236, 235, 231), PANEL=(255, 255, 255),
        PANEL_HI=(255, 226, 204), LINE=(212, 210, 204), TEXT=(17, 17, 20), DIM=(58, 58, 64), FAINT=(92, 92, 100),
        ACCENT=(232, 87, 30), ACCENT_HI=(176, 58, 12), GREEN=(10, 122, 60), AMBER=(138, 90, 0), RED=(180, 0, 30),
        BLUE=(0, 88, 180))),
    "gamecube": ("Indigo Cube", dict(BG=(27, 26, 64), BG2=(34, 32, 76), PANEL=(42, 40, 88),
        PANEL_HI=(75, 68, 166), LINE=(64, 60, 124), TEXT=(242, 241, 255), DIM=(202, 198, 244),
        FAINT=(169, 163, 224), ACCENT=(132, 120, 255), ACCENT_HI=(196, 188, 255), BLUE=(90, 200, 255))),
    "n64": ("Sixty-Four", dict(BG=(23, 24, 26), BG2=(30, 31, 34), PANEL=(38, 40, 43), PANEL_HI=(31, 90, 44),
        LINE=(58, 60, 64), TEXT=(242, 243, 244), DIM=(196, 199, 204), FAINT=(158, 162, 168),
        ACCENT=(16, 154, 60), ACCENT_HI=(124, 228, 154), GREEN=(96, 216, 232), AMBER=(255, 200, 40),
        RED=(255, 106, 90), BLUE=(90, 160, 255))),
    "turbografx": ("TurboGrafx", dict(BG=(18, 17, 16), BG2=(26, 24, 22), PANEL=(35, 32, 29),
        PANEL_HI=(106, 52, 16), LINE=(58, 53, 48), TEXT=(248, 244, 239), DIM=(210, 200, 188),
        FAINT=(170, 158, 144), ACCENT=(240, 120, 0), ACCENT_HI=(255, 184, 112), AMBER=(255, 224, 96),
        RED=(255, 106, 106))),
    "neogeo": ("Neo Geo", dict(BG=(13, 12, 9), BG2=(21, 19, 14), PANEL=(30, 27, 20), PANEL_HI=(90, 68, 16),
        LINE=(58, 52, 36), TEXT=(255, 248, 230), DIM=(224, 210, 168), FAINT=(184, 168, 120),
        ACCENT=(212, 160, 23), ACCENT_HI=(255, 216, 112), AMBER=(255, 92, 44), RED=(255, 106, 168))),
    "atari": ("Atari 2600", dict(BG=(28, 19, 12), BG2=(37, 26, 16), PANEL=(48, 34, 22), PANEL_HI=(106, 58, 20),
        LINE=(74, 54, 36), TEXT=(251, 240, 226), DIM=(224, 200, 168), FAINT=(188, 162, 130),
        ACCENT=(226, 102, 28), ACCENT_HI=(255, 170, 106), AMBER=(255, 216, 74), RED=(255, 112, 112))),
    "c64": ("Commodore 64", dict(BG=(26, 22, 96), BG2=(30, 26, 106), PANEL=(34, 30, 116), PANEL_HI=(60, 54, 164),
        LINE=(70, 64, 164), TEXT=(238, 236, 255), DIM=(184, 178, 255), FAINT=(154, 148, 240),
        ACCENT=(122, 116, 255), ACCENT_HI=(200, 196, 255), GREEN=(126, 240, 160), AMBER=(255, 208, 96),
        RED=(255, 144, 144), BLUE=(112, 216, 255))),
    "phosphor": ("Green phosphor", dict(BG=(4, 10, 5), BG2=(7, 17, 10), PANEL=(11, 26, 15),
        PANEL_HI=(20, 80, 42), LINE=(26, 58, 34), TEXT=(184, 255, 204), DIM=(110, 232, 148),
        FAINT=(76, 194, 114), ACCENT=(51, 255, 102), ACCENT_HI=(160, 255, 184), GREEN=(92, 232, 224),
        AMBER=(255, 208, 64), RED=(255, 138, 112), BLUE=(138, 180, 255))),
    "amber": ("Amber monitor", dict(BG=(12, 7, 0), BG2=(20, 12, 2), PANEL=(28, 18, 4), PANEL_HI=(90, 54, 0),
        LINE=(58, 40, 8), TEXT=(255, 224, 168), DIM=(255, 192, 96), FAINT=(224, 154, 48), ACCENT=(255, 176, 0),
        ACCENT_HI=(255, 216, 144), GREEN=(128, 224, 112), AMBER=(255, 106, 60), RED=(255, 106, 168),
        BLUE=(112, 192, 255))),
    "synthwave": ("Synthwave", dict(BG=(20, 11, 38), BG2=(27, 16, 49), PANEL=(36, 21, 63),
        PANEL_HI=(90, 28, 110), LINE=(61, 37, 99), TEXT=(251, 239, 255), DIM=(224, 191, 240),
        FAINT=(196, 154, 224), ACCENT=(255, 46, 166), ACCENT_HI=(255, 154, 214), GREEN=(70, 240, 192),
        AMBER=(255, 208, 74), RED=(255, 122, 90), BLUE=(58, 216, 255))),
    "sakura": ("Sakura", dict(BG=(31, 22, 25), BG2=(40, 29, 33), PANEL=(50, 37, 42), PANEL_HI=(106, 52, 72),
        LINE=(74, 56, 64), TEXT=(255, 242, 245), DIM=(239, 200, 211), FAINT=(207, 162, 176),
        ACCENT=(244, 114, 160), ACCENT_HI=(255, 184, 208), RED=(255, 154, 90))),
    "nord": ("Nord", dict(BG=(32, 36, 45), BG2=(36, 41, 51), PANEL=(41, 46, 57), PANEL_HI=(60, 81, 114),
        LINE=(67, 76, 94), TEXT=(239, 242, 244), DIM=(208, 214, 226), FAINT=(170, 179, 196),
        ACCENT=(94, 129, 172), ACCENT_HI=(168, 196, 232), GREEN=(163, 190, 140), AMBER=(235, 203, 139),
        RED=(228, 138, 143), BLUE=(136, 192, 208))),
    "solarized": ("Solarized", dict(BG=(0, 43, 54), BG2=(6, 51, 63), PANEL=(7, 54, 66), PANEL_HI=(15, 84, 102),
        LINE=(28, 74, 86), TEXT=(253, 246, 227), DIM=(196, 207, 204), FAINT=(160, 174, 174),
        ACCENT=(108, 113, 196), ACCENT_HI=(180, 184, 240), GREEN=(154, 193, 26), AMBER=(217, 162, 10),
        RED=(243, 114, 111), BLUE=(59, 169, 160))),
    "dracula": ("Dracula", dict(BG=(29, 30, 39), BG2=(34, 35, 46), PANEL=(40, 42, 54), PANEL_HI=(78, 68, 112),
        LINE=(68, 71, 90), TEXT=(248, 248, 242), DIM=(214, 214, 230), FAINT=(176, 178, 200),
        ACCENT=(189, 147, 249), ACCENT_HI=(224, 204, 255), GREEN=(80, 250, 123), AMBER=(241, 250, 140),
        RED=(255, 110, 110), BLUE=(139, 233, 253))),
    "paper": ("Paper", dict(BG=(244, 239, 230), BG2=(235, 229, 217), PANEL=(251, 248, 242),
        PANEL_HI=(228, 220, 246), LINE=(207, 198, 182), TEXT=(23, 19, 14), DIM=(60, 53, 44), FAINT=(94, 85, 71),
        ACCENT=(91, 63, 196), ACCENT_HI=(60, 38, 144), GREEN=(10, 110, 54), AMBER=(124, 82, 0),
        RED=(174, 26, 26), BLUE=(10, 90, 168))),
}
PALETTE = ("BG", "BG2", "PANEL", "PANEL_HI", "LINE", "TEXT", "DIM", "FAINT", "ACCENT", "ACCENT_HI",
           "GREEN", "AMBER", "RED", "BLUE")
_DEFAULT_PALETTE = {k: globals()[k] for k in PALETTE}


# Settings → Theme's picker: the cards, in groups. A theme's "_oled" twin is its card's OLED switch,
# not a card of its own (Red Visor exists only as OLED).
THEME_GROUPS = (
    ("Built in", ("default", "playstation", "xbox", "nintendo", "high_contrast", "high_contrast_light")),
    ("Consoles", ("dmg", "virtualboy_oled", "famicom", "snes", "megadrive", "dreamcast", "gamecube", "n64",
                  "turbografx", "neogeo", "atari", "c64")),
    ("Screens and moods", ("phosphor", "amber", "synthwave", "sakura")),
    ("Classic palettes", ("nord", "solarized", "dracula", "paper")),
    ("Yours", ("custom",)),
)
THEME_DESC = {
    "default": "RomM's violet on a deep indigo ink",
    "playstation": "Deep blue, with cyan for work in progress",
    "xbox": "Green on graphite, with mint for what's done",
    "nintendo": "Bold red on warm charcoal; warnings in coral, never the red of the focus",
    "high_contrast": "Pure black and white with a yellow focus",
    "high_contrast_light": "Black on white with a blue focus, for bright rooms",
    "dmg": "The original handheld's pea-green screen: four greens, darkest as the ground",
    "virtualboy_oled": "Red on true black, like the 3D visor",
    "famicom": "The red-and-white machine: maroon and cream with the gold of the cartridge labels",
    "snes": "The 16-bit console's greys with the US pad's lavender and purple",
    "megadrive": "Black plastic, chrome text and the 16-BIT red",
    "dreamcast": "A light theme: console white with the orange swirl",
    "gamecube": "The launch colour of the little cube, a touch lighter",
    "n64": "Charcoal with the 64-bit logo's green",
    "turbografx": "Black with the PC Engine's orange",
    "neogeo": "Arcade black and gold, the 100-mega-shock look",
    "atari": "Woodgrain brown and the rainbow-stripe orange",
    "c64": "READY. Light blue on the boot screen's blue",
    "phosphor": "A monochrome terminal, glowing green",
    "amber": "The warm amber CRT",
    "synthwave": "Night drive: magenta and cyan on deep purple",
    "sakura": "Soft blossom pink on a warm dusk",
    "nord": "Arctic blue-greys, calm and low-glare",
    "solarized": "The classic low-contrast-noise palette, dark",
    "dracula": "Purple-grey night with pink and green",
    "paper": "A warm light theme for bright rooms",
    "custom": "Your own: an accent colour, a background tint, and dark, OLED or light",
}


def theme_base(key):
    """The picker card a theme is on: its key without "_oled" when that's a theme of its own."""
    return key[:-5] if key.endswith("_oled") and key[:-5] in THEMES else key


def theme_oled(base):
    """The OLED version of a card's theme (the theme itself when it's only OLED; None: there's none)."""
    return base + "_oled" if base + "_oled" in THEMES else base if base.endswith("_oled") else None


def palette_of(key):
    """A theme's whole palette: Default's colours wherever it keeps them."""
    return dict(_DEFAULT_PALETTE, **THEMES.get(key, THEMES["default"])[1])


def mix(a, b, t):
    """Colour t of the way from a to b."""
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def luminance(c):
    """Relative luminance (WCAG), 0 black .. 1 white."""
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2])


def light_theme():
    return luminance(BG) > 0.5


def ink_on(fill):
    """TEXT or BG, whichever reads better on fill (a letter on an accent tile)."""
    return TEXT if abs(luminance(TEXT) - luminance(fill)) >= abs(luminance(BG) - luminance(fill)) else BG


def contrast(a, b):
    """WCAG contrast ratio, 1 .. 21."""
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# The Custom theme (Settings → Theme → Custom): every colour made from four choices, then nudged
# to the floors every theme meets (test_themes), so no choice can make it hard to read.
CUSTOM_MODES = ("dark", "oled", "light")
_LIGHT_STATUS = dict(GREEN=(10, 110, 54), AMBER=(124, 82, 0), RED=(174, 26, 26), BLUE=(10, 90, 168))


def hls(hue, light, sat):
    """A colour from a hue in degrees, lightness and saturation 0..1."""
    return tuple(round(v * 255) for v in colorsys.hls_to_rgb((hue % 360) / 360, light, sat))


def _hls_of(c):
    return colorsys.rgb_to_hls(*(v / 255 for v in c))


def custom_palette(accent=195, tint=225, strength=30, mode="dark"):
    """The 14 colours of a Custom theme: accent hue and tint hue in degrees, tint
    strength 0..100 (0: neutral greys), mode dark, oled (pure black) or light.
    Text and status colours then move toward white (black on light) until they
    reach the floors, with a margin; the grounds deepen when a bright tint at
    full strength would keep white text from them; a status colour too close to
    the accent, or to a status colour before it, turns round the colour wheel."""
    def num(v, default):
        try:
            return int(v)
        except (TypeError, ValueError):
            return default
    accent, tint = num(accent, 195) % 360, num(tint, 225) % 360
    s = max(0, min(100, num(strength, 30))) / 100
    light = mode == "light"

    def t(level, k):                       # the tint at a lightness (k: how much of the strength)
        return hls(tint, level, s * k)
    if light:
        p = dict(BG=t(.95, .45), BG2=t(.91, .45), PANEL=t(.985, .6), LINE=t(.78, .3), TEXT=t(.07, .5),
                 DIM=t(.23, .35), FAINT=t(.37, .3), ACCENT=hls(accent, .45, .85), ACCENT_HI=hls(accent, .32, .8))
        p.update(_LIGHT_STATUS)
    else:
        lv = (0, .045, .075, .2) if mode == "oled" else (.085, .11, .14, .27)
        p = dict(BG=t(lv[0], .55), BG2=t(lv[1], .55), PANEL=t(lv[2], .5), LINE=t(lv[3], .4), TEXT=t(.95, .6),
                 DIM=t(.79, .45), FAINT=t(.67, .4), ACCENT=hls(accent, .6, .85), ACCENT_HI=hls(accent, .8, .9))
        p.update({k: _DEFAULT_PALETTE[k] for k in ("GREEN", "AMBER", "RED", "BLUE")})
    ink, ground = ((0, 0, 0), (255, 255, 255)) if light else ((255, 255, 255), (0, 0, 0))

    def push(k, floor, against, to=ink):
        for _ in range(200):
            if min(contrast(p[k], p[g]) for g in against) >= floor:
                break
            p[k] = mix(p[k], to, 0.04)
    for g in ("BG", "BG2", "PANEL", "LINE"):            # a bright tint at full strength: deeper grounds
        for _ in range(100):
            if contrast(ink, p[g]) >= (8 if g == "LINE" else 14):
                break
            p[g] = mix(p[g], ground, 0.04)
    p["PANEL_HI"] = mix(p["PANEL"], p["ACCENT"], 0.2 if light else 0.35)
    grounds = ("BG", "BG2", "PANEL")
    push("TEXT", 12.5, grounds)
    push("DIM", 6.8, grounds)
    push("FAINT", 4.7, grounds)
    push("ACCENT", 3, grounds)
    push("ACCENT_HI", 4.7, ("PANEL", "PANEL_HI"))
    push("PANEL_HI", 7.3, ("TEXT",), ground)
    ah, done = _hls_of(p["ACCENT"])[0], []
    for k in ("GREEN", "AMBER", "RED", "BLUE"):
        h, lt, sat = _hls_of(p[k])
        away = 1 if (h - ah) % 1 < 0.5 else -1          # turn away from the accent's hue first
        for n in range(24):
            p[k] = hls(h * 360 + away * (1 if n % 2 else -1) * ((n + 1) // 2) * 15, lt, sat)
            push(k, 4.7, ("PANEL",))
            if _dist(p[k], p["ACCENT"]) > 90 and all(_dist(p[k], p[o]) > 60 for o in done):
                break
        done.append(k)
    return {k: p[k] for k in PALETTE}


def _dist(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def set_custom(accent, tint, strength, mode):
    """Make the Custom theme from these (the app does it from config.json at start and on every save)."""
    THEMES["custom"] = ("Custom", custom_palette(accent, tint, strength, mode))


HUE_NAMES = ((0, "Red"), (20, "Orange"), (40, "Amber"), (55, "Yellow"), (75, "Lime"), (105, "Green"),
             (150, "Teal"), (175, "Cyan"), (195, "Sky"), (215, "Blue"), (240, "Indigo"), (255, "Violet"),
             (275, "Purple"), (295, "Magenta"), (320, "Pink"), (340, "Rose"), (355, "Red"))


def hue_name(hue):
    return [n for h, n in HUE_NAMES if h <= hue % 360][-1]


set_custom(195, 225, 30, "dark")


# On this box's space bar (derived colours: apply_theme keeps them in step)
MEDIA_COL = (232, 121, 249)     # covers, videos, manuals
SYSTEM_COL = mix(BG, FAINT, 0.57)   # Batocera itself, saves, BIOS: everything outside the game folders
SPACE_PARTS = (("ours", ACCENT, "From RomM-o-Cera"), ("other", BLUE, "Added another way"),
               ("media", MEDIA_COL, "Media"))


def apply_theme(name):
    """Switch every colour to a theme (unknown: Default). Takes effect on the next
    frame: everything is drawn from these names. Returns the theme's key."""
    if name not in THEMES:
        name = "default"
    globals().update(_DEFAULT_PALETTE)
    globals().update(THEMES[name][1])
    g = globals()                               # colours derived from the palette
    g["SYSTEM_COL"] = mix(g["BG"], g["FAINT"], 0.57)
    g["SPACE_PARTS"] = (("ours", g["ACCENT"], "From RomM-o-Cera"), ("other", g["BLUE"], "Added another way"),
                        ("media", MEDIA_COL, "Media"))
    return name
