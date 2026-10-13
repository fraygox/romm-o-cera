"""Documentation (QR code) and Credits."""
import pygame
import rommlib
from rbui import theme
from rbui.theme import H, SR, W
from rbui.draw import rrect, text, wrap
from rbui.screens.base import ListScreen, Screen


CREDITS = [
    ("RomM-o-Cera", "by fraygox", "GPL-3.0-or-later", "github.com/fraygox/romm-o-cera",
     "This app. Its license is LICENSE in the app folder."),
    ("Rubik", "the font, © The Rubik Project Authors (design: Hubert & Fischer)", "SIL Open Font License 1.1",
     "github.com/googlefonts/rubik", "Its license travels with it: fonts/OFL-Rubik.txt in the app folder."),
    ("dolphin-tool", "from the Dolphin emulator, version %s" % rommlib.TOOL_DOLPHIN.pin["version"],
     "GPL-2.0-or-later", "dolphin-emu.org · source: github.com/dolphin-emu/dolphin (commit %s)"
     % rommlib.TOOL_DOLPHIN.pin["commit"][:10],
     "Optional download for GameCube and Wii to RVZ. Its source, COPYING and LICENSES/ are on the same release."),
    ("extract-xiso", "by in <in@fishtank.com>, XboxDev (commit %s)" % rommlib.TOOL_X360.pin["extract_xiso"][:7],
     "BSD-style license", "github.com/XboxDev/extract-xiso",
     "This product includes software developed by in <in@fishtank.com>. Optional download for Xbox 360 to .zar."),
    ("ZArchive", "zarchive, by Exzap (commit %s)" % rommlib.TOOL_X360.pin["zarchive"][:7], "MIT-0",
     "github.com/Exzap/ZArchive",
     "Optional download for Xbox 360 to .zar, and inside wuatool for Wii U's .wua (commit %s). Licenses and "
     "source are on the xbox360-tools and wiiu-tools releases." % rommlib.TOOL_WIIU.pin["zarchive"][:7]),
    ("wuatool", "by fraygox, built on Cemu's disc code (Cemu commit %s)" % rommlib.TOOL_WIIU.pin["cemu"][:10],
     "MPL-2.0", "cemu.info · source: github.com/cemu-project/Cemu",
     "Optional download for Wii U to .wux and Decrypt Wii U discs, built with OpenSSL (Apache-2.0). Its source, licenses and the "
     "Cemu source it compiles are on the wiiu-tools release."),
    ("zstd", "Zstandard, by Meta Platforms, linked into zarchive and wuatool", "BSD-3-Clause",
     "github.com/facebook/zstd", "Also on Batocera, where 3DS to .zcci uses it."),
    ("chdman", "from MAME", "BSD-3-Clause / GPL-2.0-or-later", "mamedev.org",
     "Part of Batocera; RomM-o-Cera runs it to make CHDs."),
    ("RomM", "the game library server this app talks to", "AGPL-3.0", "romm.app", ""),
    ("pygame and SDL", "graphics, input and sound", "LGPL-2.1 / zlib", "pygame.org · libsdl.org",
     "Part of Batocera."),
    ("Requests", "HTTP for Python", "Apache-2.0", "requests.readthedocs.io", "Part of Batocera."),
    ("7-Zip", "for .7z and .rar downloads", "LGPL-2.1 (+ unRAR restriction)", "7-zip.org", "Part of Batocera."),
    ("Batocera.linux", "the system, and EmulationStation's API", "GPL and others", "batocera.org", ""),
]


def docs_qr():
    """The documentation site's QR code (made once)."""
    if "docs" not in _QR:
        _QR["docs"] = rommlib.qr_matrix(rommlib.DOCS_URL)
    return _QR["docs"]


_QR = {}


def draw_qr(c, matrix, rect):
    """A QR code, as large as fits in rect (logical), centred: whole physical
    pixels per module, dark on white with the 4-module quiet zone, whatever
    the theme (phones read dark on light best). Returns the physical rect."""
    n = len(matrix) + 8
    r = SR(rect)
    px = max(1, min(r.width, r.height) // n)
    box = pygame.Rect(0, 0, px * n, px * n)
    box.center = r.center
    pygame.draw.rect(c, (255, 255, 255), box, border_radius=px)
    for y, row in enumerate(matrix):
        x = 0
        while x < len(row):                     # one rect per run of dark modules
            if not row[x]:
                x += 1
                continue
            x0 = x
            while x < len(row) and row[x]:
                x += 1
            c.fill((0, 0, 0), (box.left + (x0 + 4) * px, box.top + (y + 4) * px, (x - x0) * px, px))
    return box


class DocsScreen(Screen):
    """Settings, Documentation: the docs site's QR code, big enough to scan from the couch."""

    def crumbs(self):
        return ["Settings", "Documentation"]

    def hints(self):
        return [("B", "Back")]

    def draw(self, c):
        p = pygame.Rect(48, 84, W - 96, 554)
        rrect(c, theme.PANEL, p, 16)
        draw_qr(c, docs_qr(), pygame.Rect(p.left + 32, p.top + 32, 490, p.height - 64))
        x, w = p.left + 32 + 490 + 40, p.right - 32 - (p.left + 32 + 490 + 40)
        text(c, self.f.h2, "Documentation", (x, p.top + 60), theme.TEXT, maxw=w)
        text(c, self.f.body, rommlib.DOCS_URL.replace("https://", "").rstrip("/"), (x, p.top + 104), theme.ACCENT_HI,
             maxw=w)
        yy = p.top + 160
        for para in ("Point your phone's camera at the code to open it.",
                     "Installing, connecting to RomM, browsing and downloading, disc compression, PS3 games, "
                     "every setting, the controls, and what to do when something goes wrong."):
            for ln in wrap(self.f.body, para, w):
                text(c, self.f.body, ln, (x, yy), theme.DIM)
                yy += 28
            yy += 14


class CreditsScreen(ListScreen):
    """Who made what RomM-o-Cera uses or downloads, and under which license."""
    ROW_H = 96
    ROWS, STEP = (H - 60 - 84) // ROW_H, 5     # 6 show, L/R pages by 5

    def __init__(self, app):
        super().__init__(app)
        self.sel = self.scroll = 0

    def crumbs(self):
        return ["Settings", "Credits and licenses"]

    def hints(self):
        return [("L", "Page up"), ("R", "Page down"), ("B", "Back")]

    def listed(self):
        return CREDITS

    def tap_row(self, i):
        self.sel = i                 # nothing to open: a tap only selects

    def action(self, a):
        if not self.nav(a):
            super().action(a)

    def draw(self, c):
        for i, r, on in self.row_rects(84, gap=8):
            name, what, lic, url, note = CREDITS[i]
            rrect(c, theme.PANEL_HI if on else theme.PANEL, r, 12)
            if on:
                rrect(c, theme.ACCENT, r, 12, 2)
            t = text(c, self.f.h3, name, (r.left + 22, r.top + 8), theme.TEXT)
            text(c, self.f.small, what, (t.right + 12, r.top + 13), theme.DIM, maxw=r.right - 380 - t.right)
            text(c, self.f.small, lic, (r.right - 22, r.top + 13), theme.ACCENT_HI, "topright", maxw=330)
            text(c, self.f.small, url, (r.left + 22, r.top + 38), theme.FAINT, maxw=r.width - 44)
            if note:
                text(c, self.f.small, note, (r.left + 22, r.top + 60), theme.FAINT, maxw=r.width - 44)
