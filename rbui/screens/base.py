"""What every screen builds on: Screen, ListScreen and the letter-jump helpers."""
import time

import pygame
from rbui import theme
from rbui.theme import H, W
from rbui.draw import measure, polyline, rrect, text
from rbui.modals import ActionMenu


# ------------------------------------------------------------------ screens

class Screen:
    def __init__(self, app):
        self.app = app
        self.f = app.fonts

    def enter(self):
        pass

    def crumbs(self):
        return []

    def trail(self, *more):
        """Crumbs that follow the way here: the screen below's, then ours."""
        st = self.app.stack
        i = next((k for k, s in enumerate(st) if s is self), len(st))
        return (st[i - 1].crumbs() if i > 0 else []) + list(more)

    def hints(self):
        return [("A", "Select"), ("B", "Back")]

    def action(self, a):
        if a == "back":
            self.app.pop()

    def draw(self, c):
        pass

    load_error = None               # why the screen's list couldn't load: drawn instead of "nothing here"
    LOAD_ERROR_HEAD = "Couldn't load this list"

    def load_failed(self, attr):
        """The err callback of a list load: an empty list, and the reason kept so
        draw() says what went wrong instead of a reassuring "nothing here"."""
        def failed(e):
            self.load_error = str(e) or e.__class__.__name__
            setattr(self, attr, [])
            self.app.toast(self.load_error, theme.RED)
        return failed

    def draw_load_error(self, c, y=86):
        """True (and drawn) when the last load failed."""
        if not self.load_error:
            return False
        text(c, self.f.h3, self.LOAD_ERROR_HEAD, (56, y), theme.RED)
        text(c, self.f.body, "%s. Go back and open it again to try once more." % self.load_error.rstrip("."),
             (56, y + 38), theme.DIM, maxw=W - 112)
        return True

    def tap_row(self, i):
        """Touch: first tap selects (detail panels update), second opens."""
        if getattr(self, "sel", None) == i:
            self.action("ok")
        else:
            self.sel = i

    def tap_now(self, i):
        """Touch: select and open in one tap (menus, settings, tiles)."""
        self.sel = i
        self.action("ok")


class ListScreen(Screen):
    """A list of rows: sel/scroll, up/down a row, L/R a page, the visible
    window (scroll follows sel when drawn) and each row's tap target.
    `base`: tiles above the list, which sel counts first. All state here is
    class attributes: no __init__ or action/draw of its own, so a screen
    that skips its parents' __init__ or action still works."""
    ROWS, ROW_H = 9, 56
    STEP = None                  # L/R page step when it isn't ROWS (not PAGE: GameList's is its fetch size)
    base = 0
    sel = scroll = 0

    def listed(self):
        """The rows; a screen keeping them elsewhere overrides this."""
        return getattr(self, "items", None) or []

    def clamp(self, scroll=False):
        """After a reload: sel onto the last row if the list got shorter
        (and, with scroll, the window back over it at once)."""
        self.sel = max(0, min(self.sel, self.base + len(self.listed()) - 1))
        if scroll:
            self.scroll = min(self.scroll, self.sel)

    def move(self, d):
        self.sel = max(self.base, min(self.base + len(self.listed()) - 1, self.sel + d))

    def nav(self, a):
        """up/down a row, L/R and left/right a page, L2/R2 to the previous or
        next letter (or system) in a list with jumps; False when `a` isn't
        one of them or nothing's listed."""
        step = self.STEP or self.ROWS
        d = {"up": -1, "down": 1, "l1": -step, "r1": step, "left": -step, "right": step}.get(a)
        if not self.listed():
            return False
        if a in ("l2", "r2") and self.JUMP:
            if not self.jump(a == "r2") and len(self.letter_starts()) < 2 and self.JUMP_OFF:
                self.app.toast(self.JUMP_OFF)
            return True
        if d is None:
            return False
        self.move(d)
        return True

    # -- L2/R2: letters in a list sorted by name, systems in one grouped by system
    JUMP = None                  # the hint ("A–Z", "System"); None: no jumps
    JUMP_OFF = None              # said when the list isn't in an order that jumps right now
    BUBBLE_AT = (W // 2, H // 2)
    bubble, bubble_until = None, 0.0

    def jump_label(self, row):
        """The run a row belongs to; None while the list isn't in an order that jumps."""
        return letter_of(row.get("name"))

    def letter_starts(self):
        """[(sel, label)] where each run of rows with the same label starts.
        Kept per list (and sort): hints() asks every frame."""
        rows = self.listed()
        key = (id(rows), len(rows), getattr(self, "sort", None))
        got = self.__dict__.get("_starts")
        if got and got[0] == key:
            return got[1]
        out = []
        for i, r in enumerate(rows):
            lab = self.jump_label(r) if r is not None else None
            if lab is None:
                out = []
                break
            if not out or out[-1][1] != lab:
                out.append((i + self.base, lab))
        self.__dict__["_starts"] = (key, out)
        return out

    def jump(self, forward):
        """To the first row of the next/previous run, put at the top of the list."""
        hit = letter_jump(self.letter_starts(), self.sel, forward)
        if hit:
            self.sel = hit[0]
            self.scroll = max(0, min(hit[0] - self.base, len(self.listed()) - self.ROWS))
            self.bubble, self.bubble_until = hit[1], time.time() + 0.7
        return hit

    def draw_bubble(self, c):
        draw_bubble(c, self.f, self.bubble, self.bubble_until, self.BUBBLE_AT)

    def list_hints(self, *own, back="Back"):
        """A list's hint bar: its own buttons (while picking, A and X say what
        they do then), L2/R2 and L/R when they do something here, then B."""
        own = list(own)
        if self.picking or self.PICK_ALWAYS:
            n = self.pick_count()
            own = [("A", "Pick"), ("X", "%d picked" % n if n else "Menu")] + \
                [h for h in own if h[0] not in ("A", "X")]
            if not self.PICK_ALWAYS:
                back = "Stop picking"
        n = len(self.listed())
        if self.JUMP and n and len(self.letter_starts()) > 1:
            own.append(("L2 R2", self.JUMP))
        if n > self.ROWS:
            own += [("L", "Page up"), ("R", "Page down")]
        return own + [("B", back)]

    # -- picking several rows: one scheme on every list that acts on more than
    # one. X opens the list's menu, where "Pick several" starts picking. While
    # picking, A picks or unpicks the row and moves on, X opens the menu (what
    # to do with the picks first), B stops (the picks stay ticked until they're
    # used or cleared). Deleting or cancelling picks still asks first.
    PICKS = False                # this list can pick rows
    PICK_ALWAYS = False          # a list that's only for picking (Free up space): A always picks, B goes back
    PICK_ALL = True              # "Pick all" in the menu (not on game lists: RomM-sized)
    PICK_WHAT = "rows"           # "Pick several <what>"
    picking = False

    @property
    def picks(self):
        """pick_key -> row of the picked rows (made on first use: no __init__ here)."""
        return self.__dict__.setdefault("_picks", {})

    def pick_key(self, row):
        return row["path"]

    def pickable(self, row):
        return True

    def unpickable_why(self, row):
        return "This one can't be picked"

    def picked(self):
        """The picked rows still listed, in list order."""
        p = self.picks
        return [r for r in self.listed() if r is not None and self.pick_key(r) in p] if p else []

    def pick_count(self):
        return len(self.picked())

    def showing_picks(self):
        return self.PICKS and (self.picking or self.PICK_ALWAYS or bool(self.picks))

    def toggle_pick(self, move=True):
        rows, i = self.listed(), self.sel - self.base
        if not 0 <= i < len(rows) or rows[i] is None:
            return
        r = rows[i]
        k = self.pick_key(r)
        if k in self.picks:
            del self.picks[k]
        elif self.pickable(r):
            self.picks[k] = r
        else:
            self.app.toast(self.unpickable_why(r), theme.AMBER)
        if move:
            self.move(1)

    def pick_all(self):
        """Pick every row that can be (all picked already: clear them)."""
        rows = [r for r in self.listed() if r is not None and self.pickable(r)]
        if rows and len(self.picked()) == len(rows):
            self.picks.clear()
        else:
            self.picks.update((self.pick_key(r), r) for r in rows)

    def pick_action(self, a):
        """X: the list's menu; while picking, A picks and B stops. False when
        `a` isn't one of those here."""
        if a == "x":
            self.open_menu()
        elif a == "ok" and (self.picking or self.PICK_ALWAYS):
            self.toggle_pick()
        elif a == "back" and self.picking and not self.PICK_ALWAYS:
            self.picking = False
        else:
            return False
        return True

    def tap_row(self, i):
        """Touch while picking: one tap picks or unpicks the row."""
        if self.picking or self.PICK_ALWAYS:
            self.sel = i
            self.toggle_pick(move=False)
        else:
            super().tap_row(i)

    def pick_entries(self, rows):
        """Menu rows for what to do with the picked rows (the screen's)."""
        return []

    def list_actions(self):
        """Menu rows acting on the whole list (the screen's)."""
        return []

    def list_controls(self):
        """Menu rows that change the list in place: sort, filters (the screen's)."""
        return []

    def menu_title(self):
        n = self.pick_count()
        return "%d picked" % n if n else (self.crumbs() or [""])[-1]

    def menu_entries(self):
        """What to do with the picks, the whole-list actions, picking itself,
        then (below a line) what changes the list in place."""
        rows = self.picked()
        e = self.pick_entries(rows) if rows else []
        e += self.list_actions()
        if self.PICKS:
            if self.picking and not self.PICK_ALWAYS:
                e.append(("Stop picking", "", lambda: setattr(self, "picking", False)))
            elif not self.PICK_ALWAYS:
                e.append(("Pick several %s" % self.PICK_WHAT, "Then {A} picks, {X} acts on the picks, {B} stops",
                          lambda: setattr(self, "picking", True)))
            can = [r for r in self.listed() if r is not None and self.pickable(r)] if self.PICK_ALL else []
            if can and len(rows) < len(can):
                e.append(("Pick all %d" % len(can), "", lambda: (self.pick_all(), setattr(self, "picking", True))))
            if rows:
                e.append(("Clear picks", "", self.picks.clear))
        ctl = self.list_controls()
        return e + ([None] + ctl if ctl else [])

    def open_menu(self):
        e = self.menu_entries()
        if e:
            self.app.modal = ActionMenu(self.app, self.menu_title(), e, refresh=self.menu_entries)

    def draw_pick(self, c, row, center):
        """A row's tick box while picks show; True when drawn (it takes the
        place of the row's own dot or icon)."""
        if not self.showing_picks():
            return False
        x, y = center
        on = self.pick_key(row) in self.picks
        rrect(c, theme.ACCENT if on else theme.LINE, (x - 9, y - 9, 18, 18), 4, 0 if on else 2)
        if on:
            polyline(c, theme.TEXT, [(x - 4, y), (x - 1, y + 3), (x + 4, y - 3)], 2)
        return True

    def visible(self, rows=None):
        """The indexes on screen, after scrolling so sel's row is one of them."""
        rows = rows or self.ROWS
        k = max(0, self.sel - self.base)
        if k < self.scroll:
            self.scroll = k
        elif k >= self.scroll + rows:
            self.scroll = k - rows + 1
        return range(self.scroll, min(len(self.listed()), self.scroll + rows))

    def tile_nav(self, a, wrap=False):
        """The `base` tiles above the rows: left/right among them (wrap: past
        the end to the other side), down into the rows, up from the first row
        to tile 0 (on a tile: stays), L/R and left/right page only in the
        rows. False when `a` isn't one of those here."""
        b, n = self.base, len(self.listed())
        if a in ("left", "right") and self.sel < b:
            d = 1 if a == "right" else -1
            self.sel = (self.sel + d) % b if wrap else max(0, min(b - 1, self.sel + d))
        elif a == "down" and n:
            self.sel = b if self.sel < b else min(b - 1 + n, self.sel + 1)
        elif a == "up":
            self.sel = 0 if self.sel == b else max(0, self.sel - 1) if self.sel > b else self.sel
        elif a in ("l1", "r1", "left", "right") and self.sel >= b:
            step = self.STEP or self.ROWS
            self.sel = max(b, min(b - 1 + n, self.sel + (step if a in ("r1", "right") else -step)))
        else:
            return False
        return True

    def row_rects(self, y, x=48, w=None, gap=4, rows=None, tap=None):
        """(i, rect, on) for each visible row from y down, its tap target
        (tap_row unless given, at i + base) registered before it's drawn."""
        for i in self.visible(rows):
            r = pygame.Rect(x, y + (i - self.scroll) * self.ROW_H, w or W - 96, self.ROW_H - gap)
            self.app.hit(r, lambda i=i: (tap or self.tap_row)(i + self.base))
            yield i, r, i + self.base == self.sel


def letter_of(name):
    """Jump-bucket for a title: A–Z, '#' for digits/symbols, else the first
    character itself (kana/kanji/hangul get their own buckets)."""
    for ch in (name or ""):
        if ch.isspace() or ch in "'\"[(~.!¡¿-_*":
            continue
        if ch.isdigit():
            return "#"
        if "a" <= ch.lower() <= "z":
            return ch.upper()
        return ch if ch.isalpha() else "#"
    return "#"


def letter_jump(starts, sel, forward):
    """starts: sorted [(index, label)]. Next/previous letter from sel; going
    back from inside a letter first returns to that letter's start."""
    if not starts:
        return None
    g = max((k for k, (i, _) in enumerate(starts) if i <= sel), default=0)
    if forward:
        return starts[g + 1] if g + 1 < len(starts) else None
    if sel > starts[g][0]:
        return starts[g]
    return starts[g - 1] if g > 0 else None


def draw_bubble(c, f, label, until, center):
    if label and time.time() < until:
        r = pygame.Rect(0, 0, max(120, int(measure(f.h1, label)) + 48), 120)    # a system name is wider
        r.center = center
        rrect(c, theme.ACCENT, r, 24)
        text(c, f.h1, label, r.center, theme.TEXT, "center")
