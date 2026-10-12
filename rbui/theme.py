"""Scaling (logical layout units -> screen pixels) and the colour palette with its themes.

apply_theme() rebinds the palette names in this module: read them as theme.BG (never
`from rbui.theme import BG`, which would keep the colour of the theme at import)."""
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
# their shape. A status colour that would read as the accent moves (Nintendo: danger
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
    "nintendo": ("Nintendo", dict(
        BG=(28, 24, 26), BG2=(38, 32, 35), PANEL=(48, 41, 44), PANEL_HI=(110, 22, 30), LINE=(76, 64, 68),
        TEXT=(250, 245, 245), DIM=(210, 192, 194), FAINT=(182, 164, 168), ACCENT=(230, 0, 18),
        ACCENT_HI=(255, 128, 134), RED=(255, 150, 84))),
    "nintendo_oled": ("Nintendo OLED", dict(
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
}
PALETTE = ("BG", "BG2", "PANEL", "PANEL_HI", "LINE", "TEXT", "DIM", "FAINT", "ACCENT", "ACCENT_HI",
           "GREEN", "AMBER", "RED", "BLUE")
_DEFAULT_PALETTE = {k: globals()[k] for k in PALETTE}


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
