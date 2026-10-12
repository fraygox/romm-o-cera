"""Settings → Theme: every theme as a card with a live thumbnail (ThemePickerScreen)."""
import time

import pygame
from rbui import theme
from rbui.theme import apply_theme, palette_of, SR, THEME_DESC, THEME_GROUPS, theme_base, theme_oled, THEMES
from rbui.draw import circle, polyline, rich_lines, rich_text, rrect, text, wrap
from rbui.pads import theme_for
from rbui.screens.base import Screen


def contrast(a, b):
    la, lb = sorted((theme.luminance(a), theme.luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def text_contrast(p):
    """How readable a palette's text is: TEXT on its darkest-to-read ground."""
    return min(contrast(p["TEXT"], p[g]) for g in ("BG", "BG2", "PANEL"))


def draw_mini(c, r, p):
    """A thumbnail of the app in palette p (logical rect r): the top bar, a
    focused row with a progress bar, rows with status colours."""
    r = pygame.Rect(r)
    u = r.width / 176                                       # drawn for 176 wide; scales with the card
    def R(x, y, w, h):
        return pygame.Rect(r.left + round(x * u), r.top + round(y * u), max(1, round(w * u)), max(1, round(h * u)))
    rrect(c, p["BG"], r, 8)
    rrect(c, p["BG2"], R(0, 0, 176, 22), 8)
    rrect(c, p["LINE"], R(0, 21, 176, 1), 0)
    rrect(c, p["ACCENT"], R(8, 5, 12, 12), 3)
    rrect(c, p["TEXT"], R(26, 8, 46, 6), 3)
    rrect(c, p["DIM"], R(78, 8, 30, 6), 3)
    y = 30
    for i, st in enumerate((None, "AMBER", "RED", "GREEN")):
        row = R(8, y, 160, 17)
        if st is None:
            rrect(c, p["PANEL_HI"], row, 5)
            rrect(c, p["ACCENT"], row, 5, 2)
        else:
            rrect(c, p["PANEL"], row, 5)
        rrect(c, p["TEXT"] if st is None else p["DIM"], R(15, y + 6, 50 - i * 6, 5), 3)
        if st is None:
            rrect(c, p["LINE"], R(78, y + 7, 82, 4), 2)
            rrect(c, p["BLUE"], R(78, y + 7, 49, 4), 2)
        else:
            rrect(c, p[st], R(128, y + 6, 32, 5), 3)
        y += 21


class ThemePickerScreen(Screen):
    """Every theme as a card in groups. Moving previews the theme across the
    whole app; A keeps it, B puts back the one there was. Y switches a card
    to its OLED version."""
    COLS, CW, CH, GAP, HEAD = 4, 196, 168, 16, 36
    X0, TOP, BOTTOM = 48, 84, 638
    PANEL = pygame.Rect(904, 84, 328, 554)

    def __init__(self, app):
        super().__init__(app)
        self.groups = [(g, list(keys)) for g, keys in self.card_groups()]
        self.items = [k for _, keys in self.groups for k in keys]
        self.group_of = [gi for gi, (_, keys) in enumerate(self.groups) for _ in keys]
        self.was = app.cfg["theme"] if app.cfg["theme"] in THEMES else "default"
        # an OLED Theme: every card starts in its OLED version
        self.oled = {k for k in self.items if theme_oled(k)} if self.was.endswith("_oled") else set()
        here = theme_base(self.was)
        self.sel = self.items.index(here) if here in self.items else 0
        self.scroll, self.scroll_f, self.drawn_at = 0, 0.0, None
        self.preview()

    @staticmethod
    def card_groups():
        return THEME_GROUPS

    def crumbs(self):
        return self.trail("Theme")

    def enter(self):
        self.preview()                      # back from Downloads: the card under the cursor again

    # ---- the theme under the cursor
    def key(self, i=None):
        """The THEMES key card i (default: the focused one) stands for now."""
        k = self.items[self.sel if i is None else i]
        o = theme_oled(k)
        return o if o and (o == k or k in self.oled) else k

    def label(self, i=None):
        return THEMES[self.key(i)][0]

    def preview(self):
        apply_theme(self.key())

    def restore(self):
        apply_theme(self.app.theme_key())

    def choose(self):
        app, k = self.app, self.key()
        app.cfg["theme"] = k
        app.apply_config()
        app.sfx.play("toggle")
        shown = app.theme_key()
        if shown != k:                      # Match theme to controller wins with this pad
            app.toast("Saved. Match theme to controller is on: this controller shows %s" % THEMES[shown][0],
                      theme.AMBER)
        app.pop()

    def toggle_oled(self):
        k = self.items[self.sel]
        o = theme_oled(k)
        if o is None:
            self.app.toast("%s has no OLED version" % self.label())
        elif o == k:
            self.app.toast("%s is only OLED" % self.label())
        else:
            self.oled ^= {k}
            self.preview()
            self.app.sfx.play("toggle")

    # ---- moving round the grid
    def cell(self, i):
        """(group, row, column) of card i."""
        g = self.group_of[i]
        n = i - self.items.index(self.groups[g][1][0])
        return g, n // self.COLS, n % self.COLS

    def at(self, g, row, col):
        """The card at a group's row, the column clamped to that row's length."""
        keys = self.groups[g][1]
        return self.items.index(keys[0]) + min(len(keys) - 1, row * self.COLS + col)

    def rows_in(self, g):
        return (len(self.groups[g][1]) + self.COLS - 1) // self.COLS

    def step(self, a):
        g, row, col = self.cell(self.sel)
        if a == "left":
            return max(0, self.sel - 1)
        if a == "right":
            return min(len(self.items) - 1, self.sel + 1)
        if a == "up":
            if row:
                return self.at(g, row - 1, col)
            return self.at(g - 1, self.rows_in(g - 1) - 1, col) if g else self.sel
        if a == "down":
            if row + 1 < self.rows_in(g):
                return self.at(g, row + 1, col)
            return self.at(g + 1, 0, col) if g + 1 < len(self.groups) else self.sel
        first = self.items.index(self.groups[g][1][0])
        if a == "l1":
            return first if self.sel != first or not g else self.items.index(self.groups[g - 1][1][0])
        if a == "r1":
            return self.items.index(self.groups[g + 1][1][0]) if g + 1 < len(self.groups) else len(self.items) - 1
        return None

    def go(self, i):
        if i != self.sel:
            self.sel = i
            self.preview()

    def tap_card(self, i):
        """Touch: the first tap previews, a second on the same card keeps it."""
        if i == self.sel:
            self.action("ok")
        else:
            self.go(i)

    def tap_oled(self, i):
        self.go(i)
        self.toggle_oled()

    def hints(self):
        o = theme_oled(self.items[self.sel])
        return [("A", "Use")] + ([("Y", "OLED off" if self.key() == o else "OLED on")] if o and o != self.items[self.sel]
                                 else []) + [("L", "Previous group"), ("R", "Next group"), ("B", "Back")]

    def action(self, a):
        if a == "back":
            self.restore()
            self.app.pop()
        elif a == "ok":
            self.choose()
        elif a == "y":
            self.toggle_oled()
        else:
            i = self.step(a)
            if i is not None:
                self.go(i)

    # ---- drawing
    def layout(self):
        """[(group title or None, card index or None, rect in list space)], and the height of it all."""
        out, y = [], 0
        for g, (title, keys) in enumerate(self.groups):
            out.append((title, None, pygame.Rect(0, y, self.COLS * (self.CW + self.GAP) - self.GAP, self.HEAD)))
            y += self.HEAD
            first = self.items.index(keys[0])
            for n in range(len(keys)):
                r, col = divmod(n, self.COLS)
                out.append((None, first + n, pygame.Rect(col * (self.CW + self.GAP), y + r * (self.CH + self.GAP),
                                                         self.CW, self.CH)))
            y += self.rows_in(g) * (self.CH + self.GAP) + 6
        return out, y

    def glide(self, target):
        """Scroll smoothly toward target (logical px); the first frame jumps there."""
        now = time.monotonic()
        dt = None if self.drawn_at is None else now - self.drawn_at
        self.drawn_at = now
        if dt is None or dt > 0.5:
            self.scroll_f = float(target)
        else:
            self.scroll_f += (target - self.scroll_f) * min(1.0, dt * 14)
            if abs(target - self.scroll_f) < 0.5:
                self.scroll_f = float(target)
        self.scroll = target

    def draw(self, c):
        lay, total = self.layout()
        view = self.BOTTOM - self.TOP
        cur = next(r for _, i, r in lay if i == self.sel)
        head = self.HEAD if self.cell(self.sel)[1] == 0 else 0      # a group's first row shows its title too
        target = self.scroll
        if cur.top - head < target:
            target = cur.top - head
        elif cur.bottom + 8 > target + view:
            target = cur.bottom + 8 - view
        self.glide(max(0, min(target, max(0, total - view))))
        off = self.TOP - round(self.scroll_f)
        clip = c.get_clip()
        area = pygame.Rect(self.X0 - 8, self.TOP - 4, self.COLS * (self.CW + self.GAP) + 8, view + 8)
        c.set_clip(SR(area).clip(clip) if clip else SR(area))
        for title, i, r in lay:
            rr = r.move(self.X0, off)
            if rr.bottom < area.top or rr.top > area.bottom:
                continue
            if title:
                text(c, self.f.small, title.upper(), (rr.left + 4, rr.top + 12), theme.ACCENT_HI)
                continue
            self.draw_card(c, i, rr, area)
        c.set_clip(clip)
        if total > view:                                            # where in the list
            track = pygame.Rect(self.X0 + self.COLS * (self.CW + self.GAP) + 2, self.TOP, 4, view - 5)
            rrect(c, theme.LINE, track, 2)
            h = max(30, track.height * view // total)
            yy = track.top + int((track.height - h) * self.scroll_f / max(1, total - view))
            rrect(c, theme.ACCENT, (track.left, yy, 4, h), 2)
        self.draw_panel(c)

    def draw_card(self, c, i, rr, area):
        on = i == self.sel
        k = self.key(i)
        if on:
            rrect(c, theme.PANEL_HI, rr.inflate(8, 8), 15)
            rrect(c, theme.ACCENT, rr.inflate(8, 8), 15, 3)
        else:
            rrect(c, theme.PANEL, rr, 12)
        self.tap_target(rr.clip(area), lambda i=i: self.tap_card(i))
        mini = pygame.Rect(rr.left + 10, rr.top + 10, rr.width - 20, 118)
        self.draw_thumb(c, i, mini)
        o = theme_oled(self.items[i])
        label = self.label(i)
        if o:
            label = label[:-5] if label.endswith(" OLED") else label
        text(c, self.f.body, label, (rr.left + 12, rr.top + 139), theme.TEXT if on else theme.DIM,
             maxw=rr.width - (78 if o else 24))
        if o:
            pill = pygame.Rect(rr.right - 60, rr.top + 136, 50, 24)
            lit = k == o
            rrect(c, theme.ACCENT if lit else theme.BG2, pill, 12)
            if not lit:
                rrect(c, theme.LINE, pill, 12, 1)
            text(c, self.f.btn, "OLED", pill.center, theme.ink_on(theme.ACCENT) if lit else theme.FAINT, "center")
            if o != self.items[i]:
                self.tap_target(pill.inflate(8, 8).clip(area), lambda i=i: self.tap_oled(i))
        if theme_base(self.was) == self.items[i]:                   # the Theme in use: a tick on the corner
            ctr = (rr.right - 8, rr.top + 8)
            circle(c, theme.BG, ctr, 13)
            circle(c, theme.GREEN, ctr, 11)
            ink = theme.ink_on(theme.GREEN)
            polyline(c, ink, [(ctr[0] - 5, ctr[1]), (ctr[0] - 1, ctr[1] + 4), (ctr[0] + 5, ctr[1] - 4)], 2)

    def tap_target(self, r, fn):
        if r.width and r.height:                # the part of a card inside the list's window
            self.app.hit(r, fn)

    def draw_thumb(self, c, i, r):
        draw_mini(c, r, palette_of(self.key(i)))

    def describe(self):
        """(group title, name, description) of the focused card."""
        k = self.items[self.sel]
        desc = THEME_DESC.get(k, "")
        if self.key() != k:
            desc += ". On pure black, for OLED TVs" if self.key().endswith("_oled") else ""
        return self.groups[self.group_of[self.sel]][0], self.label(), desc

    def draw_panel(self, c):
        p = self.PANEL
        rrect(c, theme.PANEL, p, 16)
        group, name, desc = self.describe()
        x, w = p.left + 24, p.width - 48
        text(c, self.f.small, group.upper(), (x, p.top + 22), theme.ACCENT_HI)
        text(c, self.f.h2, name, (x, p.top + 44), theme.TEXT, maxw=w)
        y = p.top + 84
        for ln in wrap(self.f.body, desc, w, 3):
            text(c, self.f.body, ln, (x, y), theme.DIM)
            y += 25
        y += 14
        for n, (lab, col) in enumerate((("Focus", theme.ACCENT), ("Ready", theme.GREEN), ("Working", theme.AMBER),
                                        ("Problem", theme.RED), ("Info", theme.BLUE))):
            cx, cy = x + (w // 2) * (n // 3), y + 31 * (n % 3)        # what each colour means, in two columns
            circle(c, col, (cx + 10, cy + 12), 9)
            text(c, self.f.body, lab, (cx + 30, cy + 1), theme.TEXT)
        y += 3 * 31 + 10
        k, app = self.key(), self.app
        shown = theme_for(k, app.cfg["theme_auto"], app.cfg["button_style"], app.input.names())
        if shown != k:
            note, col = "Match theme to controller is on: this controller shows %s instead." % THEMES[shown][0], \
                theme.AMBER
        elif k == self.was:
            note, col = "Your theme now.", theme.FAINT
        else:
            note, col = "The whole app shows it now. {A} keeps it, {B} goes back to %s." % THEMES[self.was][0], \
                theme.FAINT
        for ln in rich_lines(self.f, self.f.small, note, w, 3):
            rich_text(c, self.f, self.f.small, ln, (x, y), col)
            y += 23
        # a row as the lists draw it, focused and not: the text at its real size
        row = pygame.Rect(x, p.bottom - 176, w, 52)
        rrect(c, theme.PANEL_HI, row, 12)
        rrect(c, theme.ACCENT, row, 12, 2)
        text(c, self.f.h3, "A focused row", (row.left + 16, row.top + 4), theme.TEXT, maxw=w - 32)
        text(c, self.f.small, "Its grey line", (row.left + 16, row.top + 28), theme.FAINT, maxw=w - 32)
        row = row.move(0, 58)
        rrect(c, theme.BG2, row, 12)
        text(c, self.f.h3, "Another row", (row.left + 16, row.top + 4), theme.DIM, maxw=w - 32)
        text(c, self.f.small, "Its grey line", (row.left + 16, row.top + 28), theme.FAINT, maxw=w - 32)
        badge = pygame.Rect(x, p.bottom - 52, w, 32)
        rrect(c, theme.BG2, badge, 16)
        ratio = text_contrast({n: getattr(theme, n) for n in theme.PALETTE})
        text(c, self.f.small, "Easy to read · text %.0f:1" % ratio, badge.center, theme.GREEN, "center", maxw=w - 20)
