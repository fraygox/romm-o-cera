"""Game night: Surprise me as a screen. Who's playing, whether only the box's games count, the
pick big, and the rolls you passed on down the left (this visit only)."""
import random

import pygame
import rommlib
from rbui import pads, theme
from rbui.theme import W
from rbui.draw import card, hline, measure, rich_text, rom_title, scroll_text, text, wrap
from rbui.screens.base import Screen
from rbui.modals import ActionMenu
from rbui.widgets.chips import PILL_GAP, PILL_PAD, pill_row, player_dots, switch_pill, switch_pill_width
from rbui.widgets.hero import game_verb, meta_line, press_verb, verb_button, VERB_H
from rbui.screens.game import GameScreen
from rbui.widgets.tiles import cover, cover_tile

PARTY = [(0, "Anyone"), (2, "2 players"), (3, "3 players"), (4, "4 players")]
HERE = "Only what's on this box"
KEEP = 4                    # passed-on rolls kept on the left
TRIES = 12                  # games read from the box's own records before giving up on a roll


class Scope:
    """Where Game night rolls from. query(offset, limit, players): RomM's listing of it for
    that many players or more (0: anyone); random: Api.random_rom()'s arguments when nothing
    narrows it; pool: a list already loaded instead; systems: the Batocera systems when it is
    whole platforms (None with whole: every one), so "only what's on this box" can roll from
    the box's own records."""

    def __init__(self, where, query=None, random=None, pool=None, systems=None, whole=False):
        self.where, self.query, self.random, self.pool = where, query, random, pool
        self.systems, self.whole = systems, whole

    @classmethod
    def library(cls, app):
        pids = [p["id"] for p in app.platforms]
        return cls("your library", query=lambda off, lim, n: app.api.roms(
            platform_ids=pids, offset=off, limit=lim, player_counts=party_counts(app, n)),
            random={"platform_ids": pids}, whole=True)

    @classmethod
    def platform(cls, app, p):
        return cls(p.get("display_name") or p["name"], query=lambda off, lim, n: app.api.roms(
            platform_ids=[p["id"]], offset=off, limit=lim, player_counts=party_counts(app, n)),
            random={"platform_ids": [p["id"]]}, systems={p["_system"]})

    @classmethod
    def collection(cls, app, it):
        pids, col = [p["id"] for p in app.platforms], (it["param"], it["id"])
        return cls(it.get("full") or it.get("name") or "this collection", query=lambda off, lim, n: app.api.roms(
            platform_ids=pids, collection=col, offset=off, limit=lim, player_counts=party_counts(app, n)),
            random={"platform_ids": pids, "collection": col})


def party_counts(app, players):
    """RomM's player count values meaning `players` or more (None for anyone; [] when no game
    is for that many). RomM's values are asked for once a session (blocking)."""
    if not players:
        return None
    if app.player_counts is None:
        app.player_counts = [c for c in app.api.rom_filters().get("player_counts") or [] if isinstance(c, str)]
    return rommlib.party_values(app.player_counts, players)


def players_ok(rom, players):
    return not players or rommlib.max_players((rom.get("metadatum") or {}).get("player_count")) >= players


def box_ids(library, systems=None):
    """Ids of the games this box got from RomM (its install records) in these systems (None:
    any), one per disc set."""
    out, sets = [], set()
    for rid, rec in library.snapshot().items():
        if systems is not None and rec.get("system") not in systems:
            continue
        g = (rec.get("group") or {}).get("id")
        if g:
            if g in sets:
                continue
            sets.add(g)
        try:
            out.append(int(rid))
        except ValueError:
            pass
    return out


def roll(app, scope, players=0, here=False, avoid=()):
    """One roll (blocking): (rom or None, why, pool). why: "empty" when nothing in the scope
    fits, "skipped" when every game tried was turned down; pool: how many games it rolls from
    (None when that isn't known). Games with a Batocera system only, for `players` or more,
    only ones on the box with `here`, and not one of `avoid` (ids, the latest last) while another
    is left, nor the latest one while any other is."""
    last = list(avoid)[-1] if avoid else None
    avoid = set(avoid)
    def fits(r):
        sy = app.system_for(r)
        return bool(sy) and (not here or app.library.is_installed(r, sy))

    if scope.pool is not None:
        got = [r for r in scope.pool if r and fits(r) and players_ok(r, players)]
        fresh = [r for r in got if r["id"] not in avoid] or [r for r in got if r["id"] != last] or got
        return (random.choice(fresh), None, len(got)) if got else (None, "empty", 0)
    if here and (scope.whole or scope.systems):
        ids = box_ids(app.library, None if scope.whole else scope.systems)
        order = random.sample(ids, len(ids))
        order.sort(key=lambda i: (i in avoid, i == last))
        for rid in order[:TRIES]:
            try:
                r = app.api.rom(rid)
            except rommlib.RommError:
                continue                            # gone from RomM since
            if isinstance(r, dict) and r.get("id") and fits(r) and players_ok(r, players):
                return r, None, len(ids)
        return None, "empty" if not ids else "skipped", len(ids)
    if players and not party_counts(app, players):
        return None, "empty", 0

    def page(off, lim):
        return scope.query(off, lim, players)
    total = page(0, 1).get("total") or 0
    if not total:
        return None, "empty", 0
    pick = scope.random if not players and not here else None
    rom, why = app.api.surprise(page, pick, lambda r: fits(r) and r["id"] not in avoid)
    if rom is None and avoid:
        rom, why = app.api.surprise(page, pick, lambda r: fits(r) and r["id"] != last)
    if rom is None and avoid:
        rom, why = app.api.surprise(page, pick, fits)
    return rom, why, None if here else total


class GameNightScreen(Screen):
    """Y rolls, A plays or downloads the pick, left/right says who's playing, up/down brings back
    a roll passed on, X: the actions (players, only what's on this box)."""
    SIDE = pygame.Rect(48, 150, 288, 492)           # passed-on rolls and the "rolling from" line
    PANEL = pygame.Rect(360, 150, W - 48 - 360, 492)
    TILE, PITCH = (66, 88), 102

    def __init__(self, app, scope):
        super().__init__(app)
        self.scope = scope
        self.opts = app.__dict__.setdefault("night_opts", {"players": 0, "here": False})
        self.picks = []                 # the rolls kept, oldest first
        self.rolls = 0                  # rolls so far this visit
        self.sel = 0
        self.rolling = False
        self.gen = 0
        self.pool = None
        self.why = None                 # the last roll found nothing: "empty" / "skipped"
        self.error = None
        self.roll()

    def crumbs(self):
        return self.trail("Game night")

    # -- rolling
    def roll(self):
        app = self.app
        self.gen += 1
        gen, players, here = self.gen, self.opts["players"], self.opts["here"]
        avoid = [r["id"] for r in self.picks]
        self.rolling = True

        def done(res):
            if gen != self.gen:
                return
            rom, why, pool = res
            self.rolling, self.pool, self.why, self.error = False, pool, why, None
            if rom is None:
                return
            self.picks = (self.picks + [rom])[-(KEEP + 1):]
            self.rolls += 1
            self.sel = len(self.picks) - 1
            app.sfx.play("reveal")

        def err(e):
            if gen != self.gen:
                return
            self.rolling, self.error = False, str(e) or e.__class__.__name__
        app.bg(lambda: roll(app, self.scope, players, here, avoid), done, err)

    def set_players(self, n):
        if n != self.opts["players"]:
            self.opts["players"] = n
            self.roll()

    def step_players(self, d=1):
        ns = [n for n, _ in PARTY]
        self.set_players(ns[(ns.index(self.opts["players"]) + d) % len(ns)])

    def toggle_here(self):
        self.opts["here"] = not self.opts["here"]
        self.roll()

    def current(self):
        return self.picks[self.sel] if self.picks else None

    # -- input
    def menu_entries(self):
        rom = self.current()
        return [("Roll again", "Another game from %s" % self.scope.where, self.roll)] + \
            ([("Open the game page", "Facts, screenshots and your RomM notes", self.open_page)] if rom else []) + [
                None,
                ("Players", "", self.step_players, dict(PARTY)[self.opts["players"]], "choice"),
                (HERE, "", self.toggle_here, self.opts["here"], "switch")]

    def open_page(self):
        self.app.push(GameScreen(self.app, self.current()))

    def hints(self):
        h = [("A", "Open")] if self.current() is not None else []
        return h + [("Y", "Roll again"), ("X", pads.ACTIONS), ("LEFT RIGHT", "Players"), ("B", "Back")]

    def action(self, a):
        rom = self.current()
        if a == "ok" and rom is not None:
            self.open_page()
        elif a == "y":
            self.roll()
        elif a == "x":
            self.app.modal = ActionMenu(self.app, "Game night", self.menu_entries(), refresh=self.menu_entries)
        elif a in ("left", "right"):
            self.step_players(1 if a == "right" else -1)
        elif a in ("up", "down") and self.picks:
            self.sel = max(0, min(len(self.picks) - 1, self.sel + (1 if a == "down" else -1)))
        else:
            super().action(a)

    # -- words
    def who(self):
        n = self.opts["players"]
        return ("for %d+ players" % n) if n else ""

    def rolling_from(self):
        """The line under the passed-on rolls: what the rolls come from."""
        who, where = self.who(), self.scope.where
        if self.pool is None and self.opts["here"]:
            s = "Rolling from the games on this box in %s" % where
        elif not self.pool:
            return ""
        elif self.opts["here"] and (self.scope.whole or self.scope.systems):
            s = "Rolling from {:,} game{} on this box".format(self.pool, "" if self.pool == 1 else "s")
            if not self.scope.whole:
                s += " in %s" % where
            if who:
                return s + ", keeping the ones " + who
        else:
            s = "Rolling from {:,} game{} in {}".format(self.pool, "" if self.pool == 1 else "s", where)
        return s + (", " + who if who else "")

    def nothing(self):
        """(heading, line) when the last roll found nothing."""
        who = self.who()
        where = "on this box" if self.opts["here"] else "in %s" % self.scope.where
        if self.why == "skipped":
            return ("Nothing turned up", "None of the games tried %s fit. Roll again, or change who's playing."
                    % where)
        if self.opts["here"]:
            return ("No game %s %s" % (where, who) if who else "No game from %s on this box yet" % self.scope.where,
                    "Turn off %s in {X} Actions, or download a few first." % HERE)
        return ("No game %s %s" % (where, who) if who else "No game %s to pick from" % where,
                "Fewer players widen it." if who else "")

    # -- drawing
    def draw(self, c):
        self.draw_top(c)
        self.draw_side(c)
        self.draw_pick(c)

    def draw_top(self, c):
        app, f = self.app, self.f
        y = 84
        lead = text(c, f.h3, "Tonight", (48, y + 19), theme.TEXT, "midleft").right + 24
        sw = switch_pill_width(f.body, HERE)
        sr = pygame.Rect(W - 48 - sw, y, sw, 38)
        labels = [lab for _, lab in PARTY]
        x1 = lead + sum(measure(f.body, lab) + 2 * PILL_PAD for lab in labels) + PILL_GAP * (len(labels) - 1)
        cur = [n for n, _ in PARTY].index(self.opts["players"])
        pill_row(c, f.body, labels, lead, min(int(x1), sr.left - 24), y, focus=cur, app=app,
                 on_tap=lambda i: self.set_players(PARTY[i][0]))
        app.hit(sr, self.toggle_here)
        switch_pill(c, f.body, HERE, sr, self.opts["here"])

    def draw_side(self, c):
        app, f, side = self.app, self.f, self.SIDE
        passed = self.picks[:-1]
        if passed:
            text(c, f.small, "Passed on", (side.left, side.top), theme.FAINT)
        tw, th = self.TILE
        for k, rom in enumerate(passed):
            r = pygame.Rect(side.left, side.top + 28 + k * self.PITCH, tw, th)
            app.hit(pygame.Rect(r.left, r.top, side.width, th), lambda k=k: setattr(self, "sel", k))
            on = k == self.sel
            cover_tile(c, app, rom, r, focused=on)
            tx = r.right + 14
            for j, ln in enumerate(wrap(f.small, rom_title(rom), side.right - tx, 2)):
                text(c, f.small, ln, (tx, r.top + 4 + j * 21), theme.TEXT if on else theme.DIM)
            pl = app.platform_by_id.get(rom.get("platform_id")) or {}
            text(c, f.tiny, pl.get("display_name") or pl.get("name") or "", (tx, r.top + 52), theme.FAINT,
                 maxw=side.right - tx)
        line = self.rolling_from()
        if line:
            lines = wrap(f.small, line, side.width, 3)
            for j, ln in enumerate(lines):
                text(c, f.small, ln, (side.left, side.bottom - len(lines) * 21 + j * 21), theme.FAINT)

    def draw_pick(self, c):
        app, f, p = self.app, self.f, self.PANEL
        rom = self.current()
        newest = rom is not None and self.sel == len(self.picks) - 1
        card(c, p, newest, 16)
        bg = theme.PANEL_HI if newest else theme.PANEL
        if rom is None:
            return self.draw_empty(c, p, bg)
        cov = pygame.Rect(p.left + 28, p.top + 28, 300, 400)
        cover(c, app, rom, cov)
        tx = cov.right + 28
        tw = p.right - 28 - tx
        y = p.top + 26
        text(c, f.small, "Roll %d of %d" % (self.rolls - len(self.picks) + self.sel + 1, self.rolls), (tx, y),
             theme.ACCENT_HI)
        y += 26
        y = scroll_text(c, f.h1, rom_title(rom), (tx, y, tw, 2 * 42), theme.TEXT, key=rom["id"], line_h=42,
                        bg=bg, slot="night-title").bottom + 6
        meta = meta_line(app, rom, players=False)          # the dots say who can play
        if meta:
            y = scroll_text(c, f.body, meta, (tx, y, tw, 2 * 25), theme.DIM, key=rom["id"], line_h=25, bg=bg,
                            slot="night-meta").bottom + 10
        n = rommlib.max_players((rom.get("metadatum") or {}).get("player_count"))
        if n:
            lit = min(n, self.opts["players"] or n)
            player_dots(c, f, n, lit, (tx, y + 11), "%d can play" % n if n > 1 else "One player")
            y += 36
        vb = pygame.Rect(tx, cov.bottom - VERB_H, tw, VERB_H)
        summary = (rom.get("summary") or "").strip()
        if summary and vb.top - 16 - y >= 40:
            scroll_text(c, f.small, summary, (tx, y, tw, vb.top - 16 - y), theme.DIM, key=rom["id"], line_h=20,
                        bg=bg, slot="night-summary")
        app.hit(vb, lambda: press_verb(app, rom))
        verb_button(c, f, vb, glyph=None, **game_verb(app, rom))     # a tap: the verb; A opens the page
        self.draw_foot(c, p, cov.bottom + 20)

    def draw_foot(self, c, p, y):
        f = self.f
        hline(c, theme.LINE, (p.left + 28, y), (p.right - 28, y))
        if self.rolling:
            s, col = "Rolling…", theme.DIM
        elif self.error:
            s, col = "Couldn't roll: %s" % self.error, theme.RED
        elif self.why:
            s, col = self.nothing()[0] + ". {Y} tries again.", theme.AMBER
        else:
            s, col = "{Y} rolls again · the ones you pass on stay on the left", theme.DIM
        rich_text(c, f, f.body, s, (p.left + 28, (y + p.bottom) // 2), col, "midleft", maxw=p.width - 56)

    def draw_empty(self, c, p, bg):
        f = self.f
        x, w = p.left + 40, p.width - 80
        if self.rolling:
            text(c, f.h2, "Rolling…", p.center, theme.DIM, "center")
            return
        if self.error:
            head, line, col = "Couldn't roll", self.error, theme.RED
        else:
            (head, line), col = self.nothing(), theme.TEXT
        y = p.centery - 60
        y = scroll_text(c, f.h2, head, (x, y, w, 2 * 32), col, line_h=32, bg=bg, slot="night-empty").bottom + 10
        if line:
            scroll_text(c, f.body, line, (x, y, w, 3 * 26), theme.DIM, line_h=26, bg=bg, slot="night-empty-line")
        rich_text(c, f, f.body, "{Y} rolls again", (x, p.bottom - 44), theme.DIM, "midleft")
