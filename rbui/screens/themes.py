"""Settings → Theme: every theme as a card with a live thumbnail (ThemePickerScreen)."""
import time

import pygame
from rbui import theme
from rbui.theme import (apply_theme, CUSTOM_MODES, H, custom_from, custom_palette, hue_name, palette_of, set_custom, SR,
                        THEME_DESC, THEME_GROUPS, theme_base, theme_oled, THEMES)
from rbui.draw import circle, polyline, rich_text, rich_width, rrect, scroll_text, text
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


CUSTOM_KEYS = ("theme_custom_accent", "theme_custom_tint", "theme_custom_strength", "theme_custom_mode")
MODE_NAMES = {"dark": "Dark", "oled": "OLED", "light": "Light"}


def custom_of(cfg):
    """(accent, tint, strength, mode) of the Custom theme in a config."""
    return tuple(cfg[k] for k in CUSTOM_KEYS)


def custom_summary(accent, tint, strength, mode):
    lean = "neutral grey" if not strength else "%s tint at %d%%" % (hue_name(tint).lower(), strength)
    return "%s accent, %s; %s" % (hue_name(accent), lean, {"oled": "OLED", "light": "light"}.get(mode, "dark"))


def draw_meanings(c, f, x, y, w):
    """What each colour means, in two columns; returns the y below."""
    for n, (lab, col) in enumerate((("Focus", theme.ACCENT), ("Ready", theme.GREEN), ("Working", theme.AMBER),
                                    ("Problem", theme.RED), ("Info", theme.BLUE))):
        cx, cy = x + (w // 2) * (n // 3), y + 31 * (n % 3)
        circle(c, col, (cx + 10, cy + 12), 9)
        text(c, f.body, lab, (cx + 30, cy + 1), theme.TEXT)
    return y + 3 * 31


class ThemePickerScreen(Screen):
    """Every theme as a card in groups. Moving previews the theme across the
    whole app; A keeps it, B puts back the one there was. Y switches a card
    to its OLED version; X starts the Custom theme from the card's colours."""
    COLS, CW, CH, GAP, HEAD = 4, 196, 168, 16, 36
    X0, TOP, BOTTOM = 48, 84, 638
    PANEL = pygame.Rect(904, 84, 328, 554)

    def __init__(self, app):
        super().__init__(app)
        self.groups = [(g, list(keys)) for g, keys in THEME_GROUPS]
        self.items = [k for _, keys in self.groups for k in keys]
        self.group_of = [gi for gi, (_, keys) in enumerate(self.groups) for _ in keys]
        self.was = app.cfg["theme"] if app.cfg["theme"] in THEMES else "default"
        # an OLED Theme: every card starts in its OLED version
        self.oled = {k for k in self.items if theme_oled(k)} if self.was.endswith("_oled") else set()
        here = theme_base(self.was)
        self.sel = self.items.index(here) if here in self.items else 0
        self.scroll, self.scroll_f, self.drawn_at = 0, 0.0, None
        self.preview()

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

    def edit_copy(self):
        """The Custom screen, started from the focused card's colours (Custom itself: as it is).
        Nothing is saved until A there."""
        if self.items[self.sel] == "custom":
            self.app.push(CustomThemeScreen(self.app))
            return
        start = custom_from(palette_of(self.key()))
        self.app.push(CustomThemeScreen(self.app, start=start, source=self.label()))

    def toggle_oled(self):
        k = self.items[self.sel]
        o = theme_oled(k)
        if k == "custom":
            self.app.toast("Custom's Mode can be OLED: {A} opens it")
        elif o is None:
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
        k = self.items[self.sel]
        o = theme_oled(k)
        oled = [("Y", "OLED off" if self.key() == o else "OLED on")] if o and o != k else []
        copy = [] if k == "custom" else [("X", "Edit a copy")]
        return [("A", "Edit" if k == "custom" else "Use")] + copy + oled + \
            [("L", "Previous group"), ("R", "Next group"), ("B", "Back")]

    def action(self, a):
        if a == "back":
            self.restore()
            self.app.pop()
        elif a == "ok" and self.items[self.sel] == "custom":
            self.app.push(CustomThemeScreen(self.app))
        elif a == "ok":
            self.choose()
        elif a == "y":
            self.toggle_oled()
        elif a == "x":
            self.edit_copy()
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

    def scroll_to(self, lay, total, view):
        """Where the list scrolls to keep the focused card in view: always a group's title or a row's top,
        so the top card is never cut through (the title stays above its first row where it fits)."""
        stops = sorted({r.top for title, i, r in lay if title or self.cell(i)[1]})     # titles, and rows 2..
        cur = next(r for _, i, r in lay if i == self.sel)
        head = self.HEAD if self.cell(self.sel)[1] == 0 else 0      # a group's first row shows its title too
        last = max([0] + [s for s in stops if s <= max(0, total - view)])
        if total > view and last < total - view:                    # the end: the last rows whole as well
            last = min((s for s in stops if s >= total - view), default=last)
        target = self.scroll
        if cur.top - head < target:
            target = cur.top - head
        elif cur.bottom + 8 > target + view:
            target = min((s for s in stops if s >= cur.bottom + 8 - view), default=cur.top - head)
        return max(0, min(target, last))

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
        self.glide(self.scroll_to(lay, total, view))
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
            yy = track.top + int((track.height - h) * min(1, self.scroll_f / max(1, total - view)))
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
        if k == "custom":
            desc = custom_summary(*custom_of(self.app.cfg))
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
        y = scroll_text(c, self.f.body, desc, (x, p.top + 84, w, 3 * 25), theme.DIM, key=self.key(), line_h=25,
                        slot="theme-desc").bottom + 14
        y = draw_meanings(c, self.f, x, y, w) + 10
        k, app = self.key(), self.app
        shown = theme_for(k, app.cfg["theme_auto"], app.cfg["button_style"], app.input.names())
        if shown != k:
            note, col = "Match theme to controller is on: this controller shows %s instead." % THEMES[shown][0], \
                theme.AMBER
        elif self.items[self.sel] == "custom":
            note, col = "{A} opens it: pick your own colours there. {B} goes back to %s." % THEMES[self.was][0], \
                theme.FAINT
        elif k == self.was:
            note, col = "Your theme now.", theme.FAINT
        else:
            note, col = "The whole app shows it now. {A} keeps it, {B} goes back to %s." % THEMES[self.was][0], \
                theme.FAINT
        rows_top = p.bottom - 176                                   # the sample rows below
        copy = self.items[self.sel] != "custom"
        room = (rows_top - 6 - 40 - 8 if copy else rows_top - 6) - y   # whatever the fonts wrap to, nothing overlaps
        if room >= 23:                                              # up to three lines; a longer note scrolls
            y = scroll_text(c, self.f.small, note, (x, y, w, min(3 * 23, room)), col, key=self.key(), line_h=23,
                            slot="theme-note").bottom
        if copy:                                                    # touch: X's "Edit a copy" as a button
            self.copy_button = pygame.Rect(x, min(max(y + 8, p.bottom - 228), rows_top - 46), w, 40)
            rrect(c, theme.BG2, self.copy_button, 20)
            rrect(c, theme.LINE, self.copy_button, 20, 1)
            label = "{X} Edit a copy"
            lw = min(w - 20, rich_width(self.f, self.f.small, label))
            rich_text(c, self.f, self.f.small, label, (self.copy_button.centerx - lw // 2, self.copy_button.centery),
                      theme.DIM, "midleft", maxw=w - 20)
            self.app.hit(self.copy_button, self.edit_copy)
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


class CustomThemeScreen(Screen):
    """Settings → Theme → Custom: the player's own theme from four choices,
    the whole app showing it as they change. A keeps it (and uses it), B
    leaves it as it was. Started from a theme (the picker's X: start, its
    name: source), it shows that theme's look until A keeps it."""
    ROWS = (("accent", "Accent colour", "The focus, the logo, progress"),
            ("tint", "Background tint", "The hue the backgrounds and greys lean toward"),
            ("strength", "Tint strength", "From neutral grey to fully tinted"),
            ("mode", "Mode", "Dark, OLED (pure black) or Light"))
    HUE_STEP, BIG_HUE, STRENGTH_STEP = 15, 60, 10
    ROW_Y, ROW_H = 96, 86
    PANEL = pygame.Rect(776, 84, 456, 554)

    def __init__(self, app, start=None, source=None):
        super().__init__(app)
        a, t, st, m = start or custom_of(app.cfg)
        self.source = source
        self.v = {"accent": a, "tint": t, "strength": st, "mode": m if m in CUSTOM_MODES else "dark"}
        self.sel = 0
        self.preview()

    def crumbs(self):
        return self.trail("Custom")

    def enter(self):
        self.preview()

    def values(self):
        return self.v["accent"], self.v["tint"], self.v["strength"], self.v["mode"]

    def preview(self):
        set_custom(*self.values())
        apply_theme("custom")

    def change(self, d, big=False):
        row, v = self.ROWS[self.sel][0], self.v
        if row in ("accent", "tint"):
            step = self.BIG_HUE if big else self.HUE_STEP
            v[row] = (v[row] // self.HUE_STEP * self.HUE_STEP + d * step) % 360   # onto the steps, then round
        elif row == "strength":
            v[row] = max(0, min(100, v[row] // self.STRENGTH_STEP * self.STRENGTH_STEP
                                + d * self.STRENGTH_STEP * (3 if big else 1)))
        else:
            v[row] = CUSTOM_MODES[(CUSTOM_MODES.index(v[row]) + d) % len(CUSTOM_MODES)]
        self.preview()

    def set(self, row, value):
        """Touch: a swatch, a step of the strength bar or a mode picked straight away."""
        self.sel = [r[0] for r in self.ROWS].index(row)
        self.v[row] = value
        self.preview()

    def keep(self):
        app = self.app
        for k, v in zip(CUSTOM_KEYS, self.values()):
            app.cfg[k] = v
        app.cfg["theme"] = "custom"
        app.apply_config()
        app.sfx.play("toggle")
        shown = app.theme_key()
        if shown != "custom":
            app.toast("Saved. Match theme to controller is on: this controller shows %s" % THEMES[shown][0],
                      theme.AMBER)
        app.stack.pop()                     # not app.pop(): the picker would preview Custom again
        if isinstance(app.stack[-1], ThemePickerScreen):
            app.stack.pop()                 # chosen: back where the picker was opened
        app.stack[-1].enter()
        apply_theme(app.theme_key())        # what this controller shows (Match theme to controller)

    def hints(self):
        return [("A", "Use"), ("LEFT RIGHT", "Change"), ("L", "Bigger step back"), ("R", "Bigger step"),
                ("B", "Back")]

    def action(self, a):
        if a == "back":
            set_custom(*custom_of(self.app.cfg))     # as it was; the picker shows it again
            self.app.pop()
        elif a == "ok":
            self.keep()
        elif a in ("up", "down"):
            self.sel = max(0, min(len(self.ROWS) - 1, self.sel + (1 if a == "down" else -1)))
        elif a in ("left", "right", "l1", "r1"):
            self.change(1 if a in ("right", "r1") else -1, big=a in ("l1", "r1"))

    # ---- drawing
    def hue_swatches(self):
        """Each hue step's accent with the other choices as they are (what that step would give)."""
        key = (self.v["tint"], self.v["strength"], self.v["mode"])
        got = self.__dict__.get("_swatches")
        if not got or got[0] != key:
            got = self._swatches = (key, [custom_palette(h, *key)["ACCENT"] for h in range(0, 360, self.HUE_STEP)])
        return got[1]

    def tint_swatches(self):
        """Each hue step as the rules it would give at full strength (the backgrounds' hue, easy to see)."""
        key = (self.v["accent"], self.v["mode"])
        got = self.__dict__.get("_tints")
        if not got or got[0] != key:
            got = self._tints = (key, [custom_palette(key[0], h, 100, key[1])["LINE"]
                                       for h in range(0, 360, self.HUE_STEP)])
        return got[1]

    def value_text(self, row):
        v = self.v[row]
        if row in ("accent", "tint"):
            return "%s · %d°" % (hue_name(v), v)
        if row == "strength":
            return "None (grey)" if not v else "%d%%" % v
        return MODE_NAMES[v]

    def draw(self, c):
        for n, (row, label, sub) in enumerate(self.ROWS):
            r = pygame.Rect(48, self.ROW_Y + n * self.ROW_H, 700, self.ROW_H - 8)
            on = n == self.sel
            self.app.hit(r, lambda n=n: setattr(self, "sel", n))
            rrect(c, theme.PANEL_HI if on else theme.PANEL, r, 12)
            if on:
                rrect(c, theme.ACCENT, r, 12, 2)
            text(c, self.f.h3, label, (r.left + 22, r.top + 8), theme.TEXT if on else theme.DIM)
            vr = text(c, self.f.body, self.value_text(row), (r.right - 22, r.top + 20), theme.TEXT if on else theme.DIM,
                      "midright", maxw=240)
            if on:                                                  # ‹ › around the value
                for x0, d in ((vr.left - 12, -1), (r.right - 12, 1)):
                    polyline(c, theme.ACCENT_HI, [(x0 - 3 * d, r.top + 14), (x0 + 3 * d, r.top + 20),
                                                  (x0 - 3 * d, r.top + 26)], 2)
            strip = pygame.Rect(r.left + 22, r.top + 44, r.width - 44, 22)
            if row in ("accent", "tint"):
                cols = self.hue_swatches() if row == "accent" else self.tint_swatches()
                self.draw_strip(c, strip, cols, self.v[row] // self.HUE_STEP, row)
            elif row == "strength":
                self.draw_steps(c, strip)
            else:
                self.draw_modes(c, strip)
        y = self.ROW_Y + len(self.ROWS) * self.ROW_H + 6
        note = "Every colour comes from these four, nudged to stay as easy to read as Default. {L} and {R} take " \
               "bigger steps."
        if self.source:
            note = "Started from %s, as near as these four come. Nothing is saved until {A} keeps it; {B} " \
                   "changes nothing. " % self.source + note
        scroll_text(c, self.f.small, note, (52, y, 700, min(5 * 23, H - 56 - y)), theme.FAINT, bg=theme.BG,
                    line_h=23, slot="custom-note")
        self.draw_panel(c)

    def draw_strip(self, c, r, cols, at, row):
        w = r.width / len(cols)
        for n, col in enumerate(cols):
            x0, x1 = round(r.left + n * w), round(r.left + (n + 1) * w)
            cell = pygame.Rect(x0, r.top, x1 - x0 - 2, r.height)
            rrect(c, col, cell, 5)
            self.app.hit(cell.inflate(0, 10), lambda n=n: self.set(row, n * self.HUE_STEP))
            if n == at:
                rrect(c, theme.TEXT, cell.inflate(6, 6), 7, 2)

    def draw_steps(self, c, r):
        n = 100 // self.STRENGTH_STEP + 1
        w = r.width / n
        for k in range(n):
            x0, x1 = round(r.left + k * w), round(r.left + (k + 1) * w)
            cell = pygame.Rect(x0, r.top + 4, x1 - x0 - 4, r.height - 8)
            lit = k * self.STRENGTH_STEP <= self.v["strength"]
            rrect(c, theme.ACCENT if lit else theme.LINE, cell, 4)
            self.app.hit(cell.inflate(4, 18), lambda k=k: self.set("strength", k * self.STRENGTH_STEP))

    def draw_modes(self, c, r):
        w = (r.width - 2 * 12) // 3
        for k, m in enumerate(CUSTOM_MODES):
            pill = pygame.Rect(r.left + k * (w + 12), r.top - 2, w, r.height + 4)
            lit = m == self.v["mode"]
            rrect(c, theme.ACCENT if lit else theme.BG2, pill, 13)
            text(c, self.f.small, MODE_NAMES[m], pill.center, theme.ink_on(theme.ACCENT) if lit else theme.DIM,
                 "center")
            self.app.hit(pill, lambda m=m: self.set("mode", m))

    def draw_panel(self, c):
        p = self.PANEL
        rrect(c, theme.PANEL, p, 16)
        x, w = p.left + 24, p.width - 48
        text(c, self.f.small, "PREVIEW" if not self.source else "FROM %s" % self.source.upper(), (x, p.top + 22),
             theme.ACCENT_HI, maxw=w)
        text(c, self.f.h2, "Custom", (x, p.top + 44), theme.TEXT)
        scroll_text(c, self.f.body, custom_summary(*self.values()), (x, p.top + 84, w, 2 * 25), theme.DIM,
                    line_h=25, slot="custom-summary")
        pal = {k: getattr(theme, k) for k in theme.PALETTE}
        h = min(220, p.bottom - 60 - 3 * 31 - 18 - (p.top + 130))      # the colour key fits above the badge
        mini = pygame.Rect(0, p.top + 130, round(h * 176 / 118), h)
        mini.centerx = p.centerx
        draw_mini(c, mini, pal)
        draw_meanings(c, self.f, x, mini.bottom + 18, w)
        badge = pygame.Rect(x, p.bottom - 52, w, 32)
        rrect(c, theme.BG2, badge, 16)
        text(c, self.f.small, "Easy to read · text %.0f:1" % text_contrast(pal), badge.center, theme.GREEN, "center",
             maxw=w - 20)
