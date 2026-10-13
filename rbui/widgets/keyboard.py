"""The one on-screen keyboard (Search, Settings text fields, Welcome) and its text field.

Keyboard owns the value, the layout, focus, touch, hardware typing, Shift and the #+=
symbols layer; a screen gives it a rect to draw into, a primary verb and its context keys."""
import pygame
from rbui import pads, theme
from rbui.draw import hline, measure, polyline, rrect, text

UNIT_GAP = 0.2          # the space between the grid and the context column, in key pitches
CONTEXT_SPAN = 1.6      # a context key's width, in key pitches

CONTEXT = {"url": ["https://", ":", "/", ".", "-"],
           "search": ["'", "&", "-", ":", "!"],
           "token": ["rmm_", "-", "_", ".", "/"]}


class Key:
    __slots__ = ("label", "kind", "x0", "x1")

    def __init__(self, label, kind, x0, span):
        self.label, self.kind, self.x0, self.x1 = label, kind, x0, x0 + span

    @property
    def mid(self):
        return (self.x0 + self.x1) / 2


class Keyboard:
    """Four character rows (digits always on top), the action row, and an optional
    column of context keys beside them, one per row. mode "code": upper-case letters
    and digits only, for a pairing code."""
    LETTERS = ["1234567890", "qwertyuiop", "asdfghjkl-", "zxcvbnm.,'"]
    SYMBOLS = ["1234567890", "!@#$%^&*()", "~`<>[]{}|\\", "_=+;:\"/?,."]
    CODE = ["1234567890", "QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM"]
    ACTIONS = [("shift", 1.25), ("sym", 1.25), ("space", 2.5), ("delete", 1.5), ("clear", 1.5), ("primary", 2)]
    CODE_ACTIONS = [("delete", 3), ("clear", 3), ("primary", 4)]
    LABEL = {"space": "Space", "delete": "Delete", "clear": "Clear"}

    def __init__(self, primary, on_submit=None, value="", context=None, mode="text", maxlen=2048,
                 y_key="shift", on_change=None, on_touch=None):
        self.primary = primary
        self.on_submit, self.on_change, self.on_touch = on_submit, on_change, on_touch
        self.mode, self.maxlen, self.y_key = mode, maxlen, y_key
        context = CONTEXT.get(context, context) if isinstance(context, str) else context
        self.context = [] if mode == "code" else list(context or [])[:5]
        self.value = ""
        self.shift = self.sym = False
        self.r, self.c = 1, 0
        self.insert(value or "", notify=False)

    def nav_key(self):
        """The cursor and layer: a change is a move (the app's move sound)."""
        return self.r, self.c, self.shift, self.sym

    # ---- layout, in key pitches (one letter key + its gap = 1)
    def char_rows(self):
        if self.mode == "code":
            return self.CODE
        if self.sym:
            return self.SYMBOLS
        if self.shift:
            return [self.LETTERS[0]] + [r.upper() for r in self.LETTERS[1:]]
        return self.LETTERS

    @property
    def units(self):
        return 10 + (UNIT_GAP + CONTEXT_SPAN if self.context else 0)

    def keys(self):
        out = []
        for row in self.char_rows():
            x = (10 - len(row)) / 2
            out.append([Key(ch, "char", x + i, 1) for i, ch in enumerate(row)])
        x, acts = 0, []
        for kind, span in (self.CODE_ACTIONS if self.mode == "code" else self.ACTIONS):
            acts.append(Key(self.label(kind), kind, x, span))
            x += span
        out.append(acts)
        for i, label in enumerate(self.context):
            out[i].append(Key(label, "context", 10 + UNIT_GAP, CONTEXT_SPAN))
        return out

    def label(self, kind):
        if kind == "primary":
            return self.primary
        if kind == "sym":
            return "abc" if self.sym else "#+="
        return self.LABEL.get(kind, "")

    def row_len(self, r):
        return len(self.keys()[r])

    @property
    def focus(self):
        return self.keys()[self.r][self.c]

    def at_edge(self, side):
        """Focus is on the first (side "left") or last ("right") key of its row."""
        return self.c == (0 if side == "left" else self.row_len(self.r) - 1)

    def enter(self, side, row):
        """Focus arrives from outside: the key at that end of a row."""
        self.r = max(0, min(4, row))
        self.c = 0 if side == "left" else self.row_len(self.r) - 1

    def _move_v(self, step):
        rows = self.keys()
        mid = rows[self.r][self.c].mid
        self.r = (self.r + step) % len(rows)
        self.c = min(range(len(rows[self.r])), key=lambda i: abs(rows[self.r][i].mid - mid))

    # ---- editing
    def allowed(self, ch):
        if self.mode == "code":
            return ch.isascii() and ch.isalnum()
        return ch.isprintable()

    def insert(self, s, notify=True):
        s = "".join(ch for ch in s if self.allowed(ch))
        if self.mode == "code":
            s = s.upper()
        if s:
            self.value = (self.value + s)[:self.maxlen]
            if notify:
                self.changed()

    def changed(self):
        if self.on_change:
            self.on_change()

    def delete(self):
        self.value = self.value[:-1]
        self.changed()

    def clear(self):
        self.value = ""
        self.changed()

    def submit(self):
        if self.on_submit:
            self.on_submit()

    def toggle_shift(self):
        if self.mode != "code":
            self.shift = self.sym or not self.shift
            self.sym = False

    def toggle_sym(self):
        if self.mode != "code":
            self.sym = not self.sym

    def press(self):
        k = self.focus
        if k.kind in ("char", "context"):
            self.insert(k.label)
        else:
            {"shift": self.toggle_shift, "sym": self.toggle_sym, "space": lambda: self.insert(" "),
             "delete": self.delete, "clear": self.clear, "primary": self.submit}[k.kind]()
        self.c = min(self.c, self.row_len(self.r) - 1)

    def type_text(self, s):
        """A hardware keyboard's text: what this keyboard could type, nothing else."""
        self.insert(s)

    def tap(self, r, c):
        if self.on_touch:
            self.on_touch()
        self.r, self.c = r, c
        self.press()

    def action(self, a):
        """D-pad, A, X (delete), Y (space or Shift), Start and R (the primary verb), L (#+=).
        True if the keyboard used it."""
        if a in ("up", "down"):
            self._move_v(-1 if a == "up" else 1)
        elif a in ("left", "right"):
            self.c = (self.c + (1 if a == "right" else -1)) % self.row_len(self.r)
        elif a == "ok":
            self.press()
        elif a == "x":
            self.delete()
        elif a == "y" and self.y_key == "space":
            self.insert(" ")
        elif a == "y" and self.y_key == "shift":
            self.toggle_shift()
        elif a == "l1":
            self.toggle_sym()
        elif a in ("start", "r1"):
            self.submit()
        else:
            return False
        self.c = min(self.c, self.row_len(self.r) - 1)
        return True

    def hints(self):
        """The hint bar's keyboard part; the screen adds its own B."""
        if pads.INPUT_MODE["kb"]:
            return [("TYPE", "Letters"), ("BKSP", "Delete"), ("A", self.primary)]
        y = {"space": [("Y", "Space")], "shift": [("Y", "Shift")]}.get(self.y_key if self.mode != "code" else None, [])
        return [("A", "Type"), ("X", "Delete")] + y + [("START", self.primary)]

    # ---- drawing
    @staticmethod
    def height_for(kh, gap=8):
        return 5 * kh + 4 * gap

    def width_for(self, kw, gap=8):
        return self.units * (kw + gap) - gap

    def rects(self, rect, gap=8):
        """[(row, col, Key, pygame.Rect)] for the keyboard drawn into rect."""
        rect = pygame.Rect(rect)
        p = (rect.width + gap) / self.units
        kh = (rect.height - 4 * gap) / 5
        out = []
        for ri, row in enumerate(self.keys()):
            y = rect.top + ri * (kh + gap)
            for ci, k in enumerate(row):
                x0, x1 = rect.left + k.x0 * p, rect.left + k.x1 * p - gap
                out.append((ri, ci, k, pygame.Rect(round(x0), round(y), round(x1) - round(x0), round(kh))))
        return out

    @staticmethod
    def row_at(y, rect, gap=8):
        """The row at height y, for focus coming in from beside the keyboard."""
        rect = pygame.Rect(rect)
        return max(0, min(4, int((y - rect.top) // ((rect.height + gap) / 5))))

    def row_mid(self, rect, gap=8):
        """The focused row's centre height, for focus leaving to beside the keyboard."""
        rect = pygame.Rect(rect)
        return rect.top + self.r * (rect.height + gap) / 5 + (rect.height - 4 * gap) / 10

    def draw(self, c, f, rect, focused=True, hit=None, gap=8):
        """Draw into rect; hit(rect, fn) registers each key's tap target."""
        for ri, ci, k, kr in self.rects(rect, gap):
            if hit:
                hit(kr, lambda ri=ri, ci=ci: self.tap(ri, ci))
            on = focused and (ri, ci) == (self.r, self.c)
            hot = k.kind == "primary" or (k.kind == "shift" and self.shift) or (k.kind == "sym" and self.sym)
            if on:
                fill = theme.ACCENT
            elif k.kind == "context":
                fill = theme.BG2
            else:
                fill = theme.PANEL_HI if hot else theme.PANEL
            rrect(c, fill, kr, 10)
            if k.kind == "context" and not on:
                rrect(c, theme.LINE, kr, 10, 1)
            col = theme.TEXT if on or hot else theme.DIM
            if k.kind == "shift":
                cx, cy = kr.centerx, kr.centery
                polyline(c, col, [(cx, cy - 9), (cx + 9, cy), (cx + 4, cy), (cx + 4, cy + 8), (cx - 4, cy + 8),
                                  (cx - 4, cy), (cx - 9, cy), (cx, cy - 9)], 2)
            else:
                font = f.key if len(k.label) <= 3 and k.kind != "primary" else f.h3
                text(c, font, k.label, kr.center, col, "center", maxw=kr.width - 12)
        return pygame.Rect(rect)


def mask(value):
    """A secret shown as its ends only."""
    if len(value) > 16:
        return value[:4] + "•" * min(24, len(value) - 8) + value[-4:]
    return "•" * len(value)


def text_field(c, f, rect, value, placeholder="", font=None, focused=True, cursor=True, secret=False):
    """A one-line text field: the value's tail stays in view, a cursor at its end."""
    font = font or f.h3
    rect = pygame.Rect(rect)
    rrect(c, theme.BG2, rect, 14)
    rrect(c, theme.ACCENT if focused else theme.LINE, rect, 14, 2)
    shown = mask(value) if secret else value
    while shown and measure(font, shown) > rect.width - 50:
        shown = shown[1:]
    if shown:
        r = text(c, font, shown, (rect.left + 22, rect.centery), theme.TEXT, "midleft")
    else:
        text(c, font, placeholder, (rect.left + 22, rect.centery), theme.FAINT, "midleft", maxw=rect.width - 44)
        r = pygame.Rect(rect.left + 20, rect.top, 0, rect.height)
    if cursor:
        hline(c, theme.ACCENT_HI, (r.right + 3, rect.top + 16), (r.right + 3, rect.bottom - 16), 2)
    return r


def code_slots(c, f, center_x, y, value, n=8, split=4, w=62, h=68, gap=10):
    """A short code as n boxes (a dash after the first `split`), the next one to fill outlined
    and underlined: a pairing code being typed. Returns the row's rect."""
    total = n * w + (n - 1) * gap + 2 * gap
    x0 = center_x - total // 2
    for i in range(n):
        r = pygame.Rect(x0 + i * (w + gap) + (2 * gap if i >= split else 0), y, w, h)
        cur = i == len(value)
        rrect(c, theme.PANEL_HI if cur else theme.BG2, r, 12)
        if cur:
            rrect(c, theme.ACCENT, r, 12, 2)
            hline(c, theme.ACCENT_HI, (r.centerx - 10, r.bottom - 14), (r.centerx + 10, r.bottom - 14), 2)
        if i < len(value):
            text(c, f.h1, value[i], r.center, theme.TEXT, "center")
        if i == split - 1:
            text(c, f.h2, "-", (r.right + gap * 1.5, r.centery), theme.FAINT, "center")
    return pygame.Rect(x0, y, total, h)
