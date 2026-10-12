"""The on-screen keyboard for text settings."""
import pygame
from rbui import pads, theme
from rbui.theme import W
from rbui.draw import hline, measure, rrect, text
from rbui.screens.base import Screen


class TextInputScreen(Screen):
    """Full-charset on-screen keyboard for URLs / tokens. A types, X deletes,
    Y = shift, Start saves, B cancels."""
    LOWER = ["1234567890", "qwertyuiop", "asdfghjkl:", "zxcvbnm./-", "_@?=&%~+#!"]
    SPECIAL = [("Shift", 2), ("Space", 2), ("Delete", 2), ("Clear", 2), ("Save", 2)]
    own_start = True

    def __init__(self, app, title, value, on_save, secret=False):
        super().__init__(app)
        self.title, self.value, self.on_save, self.secret = title, value or "", on_save, secret
        self.shift = False
        self.r, self.c = 1, 0

    def rows(self):
        if not self.shift:
            return self.LOWER
        return [r.upper() if i in (1, 2, 3) else
                ("!@#$%^&*()" if i == 0 else "<>[]{}|;,\'")
                for i, r in enumerate(self.LOWER)]

    def crumbs(self):
        return ["Settings", self.title]

    def hints(self):
        if pads.INPUT_MODE["kb"]:
            return [("TYPE", "Letters"), ("BKSP", "Delete"), ("A", "Save"), ("B", "Cancel")]
        return [("A", "Type"), ("X", "Delete"), ("Y", "Shift"), ("START", "Save"), ("B", "Cancel")]

    def row_len(self, r):
        return len(self.rows()[r]) if r < len(self.LOWER) else len(self.SPECIAL)

    def action(self, a):
        n = len(self.LOWER) + 1
        if a == "up":
            self.r = (self.r - 1) % n
        elif a == "down":
            self.r = (self.r + 1) % n
        elif a == "left":
            self.c = (self.c - 1) % self.row_len(self.r)
        elif a == "right":
            self.c = (self.c + 1) % self.row_len(self.r)
        elif a == "x":
            self.value = self.value[:-1]
        elif a == "y":
            self.shift = not self.shift
        elif a == "ok":
            if self.r < len(self.LOWER):
                self.value += self.rows()[self.r][self.c]
            else:
                key = self.SPECIAL[self.c][0]
                if key == "Shift":
                    self.shift = not self.shift
                elif key == "Space":
                    self.value += " "
                elif key == "Delete":
                    self.value = self.value[:-1]
                elif key == "Clear":
                    self.value = ""
                else:
                    self.save()
        elif a == "start":
            self.save()
        elif a == "back":
            self.app.pop()
        self.c = min(self.c, self.row_len(self.r) - 1)

    def save(self):
        self.app.pop()
        self.on_save(self.value.strip())

    text_entry = True

    def type_text(self, s):
        self.value = (self.value + s)[:2048]

    def tap_key(self, r, c):
        self.r, self.c = r, c
        self.action("ok")

    def draw(self, c):
        qf = pygame.Rect(48, 88, W - 96, 60)
        rrect(c, theme.PANEL, qf, 14)
        rrect(c, theme.ACCENT, qf, 14, 2)
        shown = self.value
        if self.secret:
            shown = (shown[:4] + "•" * min(24, len(shown) - 8) + shown[-4:]) if len(shown) > 16 \
                else "•" * len(shown)
        # keep the tail (where you type) visible
        while shown and measure(self.f.h3, shown) > qf.width - 50:
            shown = shown[1:]
        r = text(c, self.f.h3, shown, (qf.left + 22, qf.centery), theme.TEXT, "midleft")
        if not self.value:
            text(c, self.f.h3, self.title, (qf.left + 22, qf.centery), theme.FAINT, "midleft")
            r = pygame.Rect(qf.left + 20, qf.top, 0, qf.height)
        if int(self.app.t * 2) % 2 == 0:
            hline(c, theme.ACCENT_HI, (r.right + 3, qf.top + 16), (r.right + 3, qf.bottom - 16), 2)
        kw, kh, gap = 96, 62, 8
        kx = (W - (10 * kw + 9 * gap)) // 2
        ky = 172
        for ri, row in enumerate(self.rows()):
            for ci, ch in enumerate(row):
                kr = pygame.Rect(kx + ci * (kw + gap), ky + ri * (kh + gap), kw, kh)
                self.app.hit(kr, lambda ri=ri, ci=ci: self.tap_key(ri, ci))
                on = (ri, ci) == (self.r, self.c)
                rrect(c, theme.ACCENT if on else theme.PANEL, kr, 10)
                text(c, self.f.key, ch, kr.center, theme.TEXT if on else theme.DIM, "center")
        sy = ky + len(self.LOWER) * (kh + gap)
        sx = kx
        for ci, (label, span) in enumerate(self.SPECIAL):
            w = span * kw + (span - 1) * gap
            kr = pygame.Rect(sx, sy, w, kh)
            self.app.hit(kr, lambda ci=ci: self.tap_key(len(self.LOWER), ci))
            on = (self.r, self.c) == (len(self.LOWER), ci)
            hot = label == "Save" or (label == "Shift" and self.shift)
            rrect(c, theme.ACCENT if on else (theme.PANEL_HI if hot else theme.PANEL), kr, 10)
            text(c, self.f.h3, label, kr.center, theme.TEXT if on or hot else theme.DIM, "center")
            sx += w + gap
