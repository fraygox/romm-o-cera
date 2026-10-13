"""Screens typed on the shared keyboard: the base layout, and a Settings text field."""
import pygame
from rbui import theme
from rbui.theme import W
from rbui.draw import hline, marquee, rich_text, rrect, scroll_text, text
from rbui.screens.base import Screen
from rbui.widgets.keyboard import Keyboard, mask, text_field, CONTEXT_SPAN, UNIT_GAP


class KeyboardScreen(Screen):
    """The field and the keyboard on the left, a panel the screen fills on the right.
    self.kb is the Keyboard; A types, X deletes, Start / R does the primary verb."""
    KB_W = 764
    FIELD = pygame.Rect(48, 88, KB_W, 60)
    KB = pygame.Rect(48, 172, KB_W, Keyboard.height_for(68))
    PANEL = pygame.Rect(48 + KB_W + 24, 88, W - 48 - (48 + KB_W + 24), 568)
    own_start = True
    text_entry = True

    def type_text(self, s):
        self.kb.type_text(s)

    def kb_rect(self):
        """KB, narrowed (centred) for a keyboard without context keys: the same key size."""
        pitch = (self.KB_W + 8) / (10 + UNIT_GAP + CONTEXT_SPAN)
        r = pygame.Rect(self.KB)
        r.width = round(self.kb.units * pitch - 8)
        r.centerx = self.KB.centerx
        return r

    def draw_keyboard(self, c, placeholder, secret=False, focused=True, font=None, shown=None, note=None):
        """The field, the keys, a note under them and the empty panel; returns the panel's rect."""
        text_field(c, self.f, self.FIELD, self.kb.value if shown is None else shown, placeholder,
                   font=font, secret=secret, cursor=focused and int(self.app.t * 2) % 2 == 0)
        self.kb.draw(c, self.f, self.kb_rect(), focused, self.app.hit)
        if note:
            kr = self.kb_rect()
            rich_text(c, self.f, self.f.small, note, (kr.left, kr.bottom + 32), theme.FAINT, "midleft", maxw=kr.width)
        rrect(c, theme.PANEL, self.PANEL, 16)
        return self.PANEL


class TextInputScreen(KeyboardScreen):
    """A text setting: Start saves, B cancels. context: the keys beside the grid ("url",
    "token" or a list); mode "code" for a pairing code (letters and digits, upper case)."""

    def __init__(self, app, title, value, on_save, secret=False, context=None, mode="text", help=None,
                 primary="Save"):
        super().__init__(app)
        self.title, self.on_save, self.secret, self.help = title, on_save, secret, help
        self.saved = value or ""
        self.kb = Keyboard(primary, self.save, value, context=context, mode=mode)

    @property
    def value(self):
        return self.kb.value

    def crumbs(self):
        return ["Settings", self.title]

    def hints(self):
        return self.kb.hints() + [("B", "Cancel")]

    def action(self, a):
        if a == "back":
            self.app.pop()
        else:
            self.kb.action(a)

    def save(self):
        self.app.pop()
        self.on_save(self.kb.value.strip())

    def shown(self):
        v = self.kb.value
        if self.kb.mode == "code" and len(v) > 4:
            return v[:4] + "-" + v[4:]           # as RomM shows it
        return v

    def draw(self, c):
        f = self.f
        note = "Letters and digits; the dash goes in by itself." if self.kb.mode == "code" else \
            "{START} saves · {B} leaves it as it was"
        p = self.draw_keyboard(c, self.title, secret=self.secret, shown=self.shown(), note=note)
        x, w = p.left + 24, p.width - 48
        text(c, f.h3, self.title, (x, p.top + 20), theme.TEXT, maxw=w)
        bottom = p.bottom - 24
        if self.saved and self.kb.mode != "code":
            marquee(c, f.small, mask(self.saved) if self.secret else self.saved, (x, bottom - 22), theme.DIM, maxw=w,
                    bg=theme.PANEL, slot="text-input-saved")
            text(c, f.small, "Saved now", (x, bottom - 44), theme.FAINT)
            bottom -= 62
            hline(c, theme.LINE, (x, bottom), (p.right - 24, bottom))
            bottom -= 16
        if self.help:
            scroll_text(c, f.small, self.help, (x, p.top + 56, w, bottom - p.top - 56), theme.DIM, line_h=22,
                        bg=theme.PANEL, slot="text-input-help")
