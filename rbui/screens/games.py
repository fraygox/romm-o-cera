"""A game list (platform, collection, search results) and Search."""
import os
import time

import pygame
import rommlib
from rommlib import human_size
from rbui import pads, theme
from rbui.draw import (_wide, arc, circle, COVER_BOX, focus_box, installed_mark, marquee, measure, rich_text,
                       rom_title, rrect, scroll_text, text, wrap)
from rbui.screens.base import letter_of, ListScreen
from rbui.screens.compare import compare_entry
from rbui.screens.game import GameScreen
from rbui.screens.gamenight import GameNightScreen, Scope
from rbui.screens.text_input import KeyboardScreen
from rbui.widgets.keyboard import Keyboard
from rbui.modals import ActionMenu, DownloadConfirm, InfoDialog, ManageMenu
from rbui.widgets.chips import pill_row, tag
from rbui.widgets.factsheet import facts
from rbui.widgets.hero import game_verb, rom_year, verb_button, VERB_H
from rbui.widgets.tiles import cover
from rbui.widgets.layout import bubble_at, CONTENT, split


REGION_ABBR = {"USA": "US", "Europe": "EU", "Japan": "JP", "World": "World", "Australia": "AU",
               "Brazil": "BR", "Korea": "KR", "China": "CN", "Germany": "DE", "France": "FR",
               "Spain": "ES", "Italy": "IT", "UK": "UK", "Asia": "Asia"}


GAME_LIST, GAME_PANEL = split(676)      # the rows (name, regions, size, platform tag) and RomM's record beside them
PILLS_Y = 84                            # a collection's box filter pills; its rows start under them
PILLS_AREA = pygame.Rect(CONTENT.left, 130, CONTENT.width, CONTENT.bottom - 130)   # 11 rows fit under its head
BOX_FILTERS = [("all", "All"), ("here", "On this box"), ("away", "Not here")]

JUNK_MARKERS = ("~hack~", "~unlicensed~", "~prototype~", "~demo~", "~homebrew~", "(proto",
                "(beta", "(demo", "(sample", "(hack", "(pirate", "(unl)")


def rank_search(query, items):
    """RomM's search order is effectively alphabetical ('~Hack~', 'BS …' first).
    Re-rank the first page: real titles containing every query word, USA/World
    releases, Latin names first; hacks/protos/BS broadcasts last. Stable."""
    words = [w for w in query.lower().split() if w]

    def key(rom):
        name = (rom.get("name") or rom.get("fs_name_no_ext") or "").lower()
        fs = (rom.get("fs_name") or "").lower()
        score = 0
        if not all(w in name for w in words):
            score += 4
        if name.startswith(query.lower()) or name.startswith("the " + query.lower()):
            score -= 1
        if any(m in name or m in fs for m in JUNK_MARKERS):
            score += 6
        if any(_wide(ch) for ch in name):
            score += 3
        regions = [r.lower() for r in (rom.get("regions") or [])]
        if any(r in ("usa", "world") for r in regions):
            score -= 2
        elif any(r in ("europe", "uk", "australia") for r in regions):
            score -= 1
        return score
    return sorted(items, key=key)


def more_like(app, rom):
    """RomM's similar games to rom, as a list of their own."""
    name = rom.get("name") or rom.get("fs_name_no_ext")

    def done(roms):
        if not roms:
            app.toast("RomM has no similar games for %s" % name, theme.AMBER)
            return
        app.push(GameListScreen(app, "Games like %s" % name, static=roms))
    app.bg(lambda: app.api.similar(rom["id"]), done)


class GameListScreen(ListScreen):
    PAGE = 60
    ROWS = 12
    ROW_H = 44

    REGION_CHOICES = [(None, "All regions"), (["USA", "World"], "USA + World"),
                      (["Europe", "World"], "Europe + World"), (["Japan"], "Japan")]
    PLAYER_CHOICES = [(0, "Any"), (2, "2+"), (3, "3+"), (4, "4+")]     # the party filter: n or more players
    BULK_MAX = 1000
    PICKS, PICK_ALL, PICK_WHAT = True, False, "games"
    JUMP, JUMP_OFF = "A–Z", "A–Z jump works in full lists sorted by name"
    BUBBLE_AT = bubble_at(GAME_LIST)
    lst, pan = GAME_LIST, GAME_PANEL  # the rows and the panel (lower, under the pills, for a whole collection)

    def __init__(self, app, title, platform=None, collection=None, search=None,
                 order_by="name", static=None, why=None, crumb="More like this", group=None, whole=False):
        """collection with whole: every game of it is fetched (up to BULK_MAX) and the list is held
        here, with the box filter pills (All / On this box / Not here); without: paged from RomM
        as a platform's list is. group: the collection's shelf, for the crumbs."""
        super().__init__(app)
        self.static = static          # a fixed list (e.g. "More like this"): no paging/filters
        self.why = why or {}          # rom id -> why it's listed (Recommended for you), on the detail panel
        self.crumb = crumb            # a fixed list's breadcrumb
        self.group = group
        self.only_verified = False
        self.hide_junk = False
        self.title = title
        self.platform = platform
        self.collection = collection
        self.search = search
        self.order_by = order_by
        self.region_i = 0
        self.players = 0              # the party filter: only games for this many players or more (0: any)
        self.hide_installed = False
        self.box_filter = "all"       # the pills: "all", "here" (on this box), "away" (not here)
        self.counts = {}              # the pills' counts, from the whole collection
        self.bios = None              # [(missing, firmware)] RomM can supply
        self.whole = bool(collection and whole)
        if self.whole:
            self.lst, self.pan = split(676, content=PILLS_AREA)
            self.BUBBLE_AT = bubble_at(self.lst)
            self.ROWS = (PILLS_AREA.height - 40) // self.ROW_H     # under the pills: fewer rows fit
        self.fetching_whole = self.whole and static is None
        self.reset()
        if self.fetching_whole:
            col = (collection["param"], collection["id"])

            def got(roms):
                self.fetching_whole = False
                self.static = roms or []
                self.reset()

            def failed(e):
                self.fetching_whole = False
                self.static = []
                self.load_failed("items")(e)
                self.total = 0
            app.bg(lambda: app.api.roms_all(limit=250, cap=self.BULK_MAX, collection=col), got, failed)
        if platform and platform.get("_system"):
            app.bg(lambda: rommlib.bios_offer(app.api, platform["_system"], platform["id"]),
                   lambda offers: setattr(self, "bios", offers), lambda e: None)

    @property
    def pills(self):
        """The box filter pills show: a whole collection, once its games are in."""
        return self.whole and self.static is not None

    def box_ok(self, rom):
        return self.box_filter == "all" or (self.box_filter == "here") == self.installed(rom)

    def set_box_filter(self, i):
        self.box_filter = BOX_FILTERS[i][0]
        self.reset()

    def cycle_box_filter(self):
        keys = [k for k, _ in BOX_FILTERS]
        self.set_box_filter((keys.index(self.box_filter) + 1) % len(keys))

    @property
    def regions(self):
        return self.REGION_CHOICES[self.region_i][0]

    @property
    def dense(self):
        # hiding installed games can't use sparse paging (offsets shift as rows
        # are filtered out), so that mode loads sequentially and filters
        return self.hide_installed

    def reset(self):
        # items is a sparse list (None = row not fetched yet) once total is known,
        # so jumping to a letter loads just that page, not everything before it
        self.items = []
        self.total = None
        self.load_error = None
        self.pending = set()
        self.failed = {}          # offset -> (retry after, failures)
        self.starts = []          # [(offset, letter)] from RomM's char_index
        self.sel = 0
        self.scroll = 0
        self.gen = getattr(self, "gen", 0) + 1
        self.bubble, self.bubble_until = None, 0.0
        self.server_off, self.server_total, self.exhausted = 0, None, False
        if self.fetching_whole:
            return                    # the whole collection is on its way: nothing to page
        if self.static is not None:
            if self.whole:
                here = sum(1 for r in self.static if self.installed(r))
                self.counts = {"all": len(self.static), "here": here, "away": len(self.static) - here}
            self.items = [r for r in self.static if not (self.hide_installed and self.installed(r))
                          and self.party_ok(r) and self.box_ok(r)]
            self.total = self.server_total = len(self.items)
            self.exhausted = True
            return
        self.fetch_page(0)

    def crumbs(self):
        if self.collection:             # the full name is shown (unclipped) in the list header
            return ["Collections"] + ([self.group] if self.group else [])
        if self.static is not None:
            return [self.crumb]
        out = []
        if self.platform and not self.search:
            out.append("Platforms")
        if self.search:
            out.append("Search")
            if self.platform:
                out.append(self.platform.get("display_name") or self.platform["name"])
            out.append('"%s"' % self.search)
        else:
            out.append(self.title)
        return out

    @property
    def loading(self):
        return self.total is None

    def _pids(self):
        if self.platform:
            return [self.platform["id"]]
        if self.search or self.order_by == "id_desc":
            return [p["id"] for p in self.app.platforms]
        return None

    def _query(self, offset, limit, char_index=False, players=None):
        """One page from RomM with this list's filters (blocking); players: another party
        filter than the list's own (Game night's)."""
        app, pids = self.app, self._pids()
        players = self.players if players is None else players
        counts = rommlib.party_values(app.player_counts, players) if players else None
        if players and not counts:
            return {"items": [], "total": 0}           # no game in the library is for that many players
        if self.order_by == "id_desc":
            p = {"offset": offset, "limit": limit, "with_char_index": "false",
                 "with_filter_values": "false", "with_rom_id_index": "false",
                 "order_by": "id", "order_dir": "desc", "platform_ids": pids}
            if self.regions:
                p["regions"], p["regions_logic"] = self.regions, "any"
            if self.only_verified:
                p["verified"] = "true"
            if self.hide_junk and app.junk:
                p["tags"], p["tags_logic"] = app.junk, "none"
            if counts:
                p["player_counts"], p["player_counts_logic"] = counts, "any"
            return app.api.get("/api/roms", p)
        return app.api.roms(platform_ids=pids, search=self.search, offset=offset, limit=limit,
                            order_by=self.order_by, char_index=char_index, regions=self.regions,
                            verified=self.only_verified,
                            exclude_tags=app.junk if self.hide_junk else None, player_counts=counts,
                            collection=(self.collection["param"], self.collection["id"])
                            if self.collection else None)

    def party_ok(self, rom):
        """A game the party filter keeps (a fixed list filters here; RomM filters the rest)."""
        return not self.players or \
            rommlib.max_players((rom.get("metadatum") or {}).get("player_count")) >= self.players

    def installed(self, rom):
        system = self.app.system_for(rom)
        return bool(system) and self.app.library.is_installed(rom, system)

    def letter_starts(self):
        return self.starts if not self.dense else []      # RomM's char_index: rows not loaded yet jump too

    def pick_key(self, rom):
        return rom["id"]

    def pickable(self, rom):
        return bool(self.app.system_for(rom)) and self.rom_status(rom)[0] != "installed"

    def unpickable_why(self, rom):
        return "Already on this box" if self.app.system_for(rom) else "No Batocera system for this platform"

    def picked(self):
        return list(self.picks.values())        # rows far from the cursor are dropped (forget_far): picks stay

    def fetch_page(self, offset):
        if self.static is not None:
            return
        if self.dense:
            return self._fetch_dense()
        if offset in self.pending:
            return
        if self.failed.get(offset, (0, 0))[0] > time.time():
            return                                 # failed a moment ago: back off
        self.pending.add(offset)
        app, gen = self.app, self.gen
        first = self.total is None
        want_ci = first and self.order_by == "name" and not self.search
        limit = 250 if self.search and offset == 0 else self.PAGE

        def done(page):
            if gen != self.gen:
                return
            self.pending.discard(offset)
            self.load_error = None
            total = page.get("total", len(page.get("items", [])))
            self.server_total = total
            if self.total is None or total != self.total:
                self.items = (self.items + [None] * total)[:total]
                self.total = total
            got = page.get("items", [])
            if self.search and offset == 0:
                got = rank_search(self.search, got)
            for k, rom in enumerate(got):
                if offset + k < total:
                    self.items[offset + k] = rom
            self.forget_far()
            self.refresh_menu()
            if want_ci and page.get("char_index"):
                merged = {}
                for ch, off in page["char_index"].items():
                    lab = letter_of(ch)
                    if off < total and (lab not in merged or off < merged[lab]):
                        merged[lab] = off
                self.starts = sorted((off, lab) for lab, off in merged.items())

        def err(e):
            if gen != self.gen:
                return
            self.pending.discard(offset)
            if self.total is None:
                self.total = 0
                self.load_error = str(e) or e.__class__.__name__     # not "No games found": it couldn't ask
            n = self.failed.get(offset, (0, 0))[1] + 1
            self.failed[offset] = (time.time() + min(60, 5 * 2 ** (n - 1)), n)
            if n == 1:
                app.toast(str(e), theme.RED)             # once per page, not every retry
        app.bg(lambda: self._query(offset, limit, want_ci), done, err)

    def _fetch_dense(self):
        """Sequential pages, installed games filtered out, until there's a
        screenful past the cursor (or the list ends)."""
        if "dense" in self.pending or self.exhausted:
            return
        self.pending.add("dense")
        app, gen, off = self.app, self.gen, self.server_off

        def done(page):
            if gen != self.gen:
                return
            self.pending.discard("dense")
            got = page.get("items", [])
            self.server_total = page.get("total", 0)
            self.server_off = off + len(got)
            self.exhausted = not got or self.server_off >= self.server_total
            self.items.extend(r for r in got if not self.installed(r))
            self.total = len(self.items)
            self.refresh_menu()
            if not self.exhausted and len(self.items) < self.sel + 3 * self.ROWS:
                self._fetch_dense()

        def err(e):
            self.pending.discard("dense")
            self.exhausted = True
            self.total = len(self.items)
            app.toast(str(e), theme.RED)
        app.bg(lambda: self._query(off, 120), done, err)

    KEEP_ROWS = 600     # rows kept either side of the cursor: RomM's records are ~16 KB each

    def forget_far(self):
        """Drop loaded pages far from the cursor (paging a 60,000-game platform
        end to end kept every record: ~1 GB); ensure() fetches them again on
        the way back. Only the sparse, page-addressed lists."""
        if self.static is not None or self.dense or len(self.items) <= 2 * self.KEEP_ROWS:
            return
        lo = max(0, (self.sel - self.KEEP_ROWS) // self.PAGE * self.PAGE)
        hi = (self.sel + self.KEEP_ROWS) // self.PAGE * self.PAGE + self.PAGE
        for start in range(0, len(self.items), self.PAGE):
            if (start + self.PAGE <= lo or start >= hi) and self.items[start] is not None:
                self.items[start:start + self.PAGE] = [None] * len(self.items[start:start + self.PAGE])

    def ensure(self, i):
        """Make sure the page holding row i is loaded (or loading)."""
        if self.static is not None:
            return
        if self.dense:
            if i >= len(self.items) - self.ROWS:
                self._fetch_dense()
            return
        if self.total is None or not (0 <= i < self.total):
            return
        start = (i // self.PAGE) * self.PAGE
        if any(r is None for r in self.items[start:start + self.PAGE]):
            self.fetch_page(start)

    # -- actions ------------------------------------------------------------
    def action(self, a):
        n = len(self.items)
        if self.nav(a):
            if a in ("l2", "r2"):
                self.ensure(self.sel)              # the letter's page (the top of the list now)
            self.ensure(self.sel + self.ROWS)
        elif self.pick_action(a):
            if a == "ok":
                self.ensure(self.sel + self.ROWS)
        elif a == "ok" and n and self.items[self.sel] is not None:
            rom = self.items[self.sel]
            self.app.push(GameScreen(self.app, rom, self.why.get(rom["id"]), source=self))
        elif a == "y" and self.pills:
            self.cycle_box_filter()
        elif a == "y":
            self.app.push(SearchScreen(self.app, platform=self.platform))
        else:
            super().action(a)

    def panel_verb(self, rom):
        """A tap on the panel's button: its dialog at once ({A} opens the game's page)."""
        if self.picking:
            return self.action("ok")
        self.app.modal = ManageMenu(self.app, rom) if self.rom_status(rom)[0] == "installed" else \
            DownloadConfirm(self.app, rom)

    def page_rom(self, i):
        """Row i's game for the game page's previous/next (None while its page loads)."""
        self.ensure(i)
        return self.items[i] if 0 <= i < len(self.items) else None

    def open_menu(self):
        self.menu_rom = self.items[self.sel] if self.items and self.sel < len(self.items) else None
        super().open_menu()

    def menu_title(self):
        if self.collection:
            return self.collection.get("full") or self.title
        if self.static is not None or self.search:
            return self.title if self.static is not None else 'Results for "%s"' % self.search
        return self.title

    def pick_entries(self, roms):
        size = sum(r.get("fs_size_bytes") or 0 for r in roms)
        return [("Download %d picked" % len(roms), human_size(size), self._download_marked)]

    def list_actions(self):
        """Actions first (they close the menu); list_controls() change the
        list in place while the menu stays open."""
        e = []
        cur = getattr(self, "menu_rom", None)
        if cur is not None:
            e.append(("More like this", cur.get("name") or cur.get("fs_name_no_ext") or "",
                      lambda: self._more_like(cur)))
        shown = self.total if self.dense or self.static is not None else self.server_total
        if shown != 0:
            e.append(("Game night", ("Roll one of these {:,} games for tonight".format(shown) if shown else
                                     "Roll a game from this list for tonight"), self.game_night))
        tot = self.server_total
        if self.pills:
            away = [r for r in self.static if not self.installed(r)]
            e.append(("Download the rest", "{:,} game{} · {}".format(
                len(away), "" if len(away) == 1 else "s", human_size(sum(r.get("fs_size_bytes") or 0 for r in away)))
                if away else "Every game is on this box already", self._download_rest if away else None))
        elif tot is None:
            e.append(("Download every game in this list", "Counting games…", None))
        elif tot:
            if tot <= self.BULK_MAX:
                e.append(("Download every game in this list",
                          "{:,} games, skipping any already on this box".format(tot), self._download_all))
            else:
                e.append(("Download every game in this list",
                          "{:,} is more than 1,000. Narrow it with the filters below.".format(tot), None))
        if (self.platform or self.collection) and not self.search and (self.static is None or self.whole):
            e.append(compare_entry(self.app, self.menu_title(), self.platform, self.collection))
        if self.platform:
            sy = self.platform["_system"]
            e.append(("Fill in game info for roms/%s" % sy,
                      "Covers, descriptions, manuals from RomM for games already here",
                      lambda: InfoDialog.start(self.app, [(sy, [self.platform["id"]])])))
        if self.bios:
            n = len(self.bios)
            e.append(("Get BIOS for %s" % (self.platform.get("display_name") or self.platform["_system"]),
                      "%d file%s from RomM, checksum-verified" % (n, "" if n == 1 else "s"), self._fetch_bios))
        return e

    def list_controls(self):
        e = []
        if self.static is None and not self.search and not self.collection:
            e.append(("Sort", "", lambda d=1: self._toggle_sort(),
                      "Newest first" if self.order_by == "id_desc" else "A to Z", "choice"))
        if self.static is None:
            e.append(("Region", "", self._cycle_region, self.REGION_CHOICES[self.region_i][1], "choice"))
        e.append(("Players", "", self._cycle_players, self.player_label(), "choice"))
        if not self.pills:                          # the pills do this on a whole collection
            e.append(("Hide games on this box", "", self._toggle_hide, self.hide_installed, "switch"))
        if self.static is None:
            e.append(("Hide prototypes, demos and hacks", "", self._toggle_junk, self.hide_junk, "switch"))
            e.append(("Only checksum-verified dumps", "", self._toggle_verified, self.only_verified, "switch"))
        return e

    def refresh_menu(self):
        m = self.app.modal
        if isinstance(m, ActionMenu) and getattr(m.refresh, "__self__", None) is self:
            m.reload()

    def _more_like(self, rom):
        more_like(self.app, rom)

    def night_scope(self):
        """Where Game night rolls from when opened here: this list with its filters, its party
        filter left to Game night's own."""
        where = self.menu_title()
        if self.static is not None:
            return Scope(where, pool=list(self.static))
        filtered = self.search or self.regions or self.hide_junk or self.only_verified
        col = (self.collection["param"], self.collection["id"]) if self.collection else None
        pids = self._pids() or [p["id"] for p in self.app.platforms]
        whole = self.platform and not filtered and not col
        return Scope(where, query=lambda off, lim, n: self._query(off, lim, players=n),
                     random=None if filtered else {"platform_ids": pids, "collection": col},
                     systems={self.platform["_system"]} if whole else None)

    def game_night(self):
        self.app.push(GameNightScreen(self.app, self.night_scope()))

    def _toggle_junk(self):
        app = self.app
        if not self.hide_junk and app.junk is None:
            def done(tags):
                app.junk = rommlib.junk_tags(tags)
                self.hide_junk = True
                self.reset()
                if app.stack[-1] is self:
                    self.refresh_menu()
            app.bg(app.api.all_tags, done)
            return
        self.hide_junk = not self.hide_junk
        self.reset()

    def _toggle_verified(self):
        self.only_verified = not self.only_verified
        self.reset()

    def _toggle_sort(self):
        self.order_by = "name" if self.order_by == "id_desc" else "id_desc"
        self.reset()

    def _toggle_hide(self):
        self.hide_installed = not self.hide_installed
        self.reset()

    def player_label(self):
        return next(lab for n, lab in self.PLAYER_CHOICES if n == self.players)

    def _cycle_players(self, step=1):
        """The party filter's next (or previous) size. RomM filters by its own
        player count values, so the first use asks it for them."""
        app = self.app
        ns = [n for n, _ in self.PLAYER_CHOICES]
        want = ns[(ns.index(self.players) + step) % len(ns)]
        if want and app.player_counts is None and self.static is None:
            if getattr(self, "_asking_players", False):
                return
            self._asking_players = True

            def done(f):
                self._asking_players = False
                app.player_counts = [c for c in f.get("player_counts") or [] if isinstance(c, str)]
                self.players = want
                self.reset()
                if app.stack[-1] is self:
                    self.refresh_menu()

            def failed(e):
                self._asking_players = False
                app.toast(str(e), theme.RED)
            app.bg(app.api.rom_filters, done, failed)
            return
        self.players = want
        self.reset()

    def _cycle_region(self, step=1):
        self.region_i = (self.region_i + step) % len(self.REGION_CHOICES)
        self.reset()

    def _queue_many(self, roms, title):
        def picked():
            self.picks.clear()
            self.picking = False
        self.app.queue_many(roms, title, picked)

    def _download_marked(self):
        self._queue_many(self.picked(), "Download picked games?")

    def _download_rest(self):
        """A whole collection: what isn't on the box yet, whatever the pills show."""
        self._queue_many([r for r in self.static if not self.installed(r)],
                         "Download %s?" % (self.collection.get("full") or self.title))

    def _download_all(self):
        app = self.app
        if self.static is not None:                # "More like this": exactly what's shown
            return self._queue_many([r for r in self.items if r], "Download this whole list?")
        app.toast("Listing every game…")

        def collect():
            out, off = [], 0
            while True:
                page = self._query(off, 250)
                got = page.get("items", [])
                out.extend(got)
                off += len(got)
                if not got or off >= page.get("total", 0) or off >= self.BULK_MAX:
                    return out
        def listed(roms):
            if app.modal is not None or app.stack[-1] is not self:
                app.toast("List ready — open the menu again to download it", theme.AMBER)   # don't cover another dialog
                return
            self._queue_many(roms, "Download this whole list?")
        app.bg(collect, listed)

    def _fetch_bios(self):
        app, offers, system = self.app, self.bios, self.platform["_system"]

        def done(written):
            self.bios = [o for o in offers if o[0]["file"] not in written]
            app.toast("BIOS installed: %s" % ", ".join(os.path.basename(w) for w in written), theme.GREEN)
        app.bg(lambda: rommlib.fetch_bios(app.api, offers, system), done)

    def hints(self):
        return self.list_hints(("A", "Open"), ("X", pads.ACTIONS), ("Y", "Filter" if self.pills else "Search"))

    def rom_status(self, rom):
        return self.app.rom_status(rom)

    def draw_pills(self, c):
        """The box filter pills of a whole collection, each with its count, the current one lit."""
        labels = ["%s · {:,}".format(self.counts.get(k, 0)) % lab for k, lab in BOX_FILTERS]
        idx = [k for k, _ in BOX_FILTERS].index(self.box_filter)
        pill_row(c, self.f.body, labels, CONTENT.left, CONTENT.right, PILLS_Y, focus=idx,
                 dots=[None, theme.GREEN, theme.FAINT], app=self.app, on_tap=self.set_box_filter)

    def draw(self, c):
        app = self.app
        if self.pills:
            self.draw_pills(c)
        lx, ly, lw = self.lst.left, self.lst.top, self.lst.width
        # list header
        if self.total is None:
            cnt = "Loading…"
        elif self.dense:
            cnt = "{:,}{} not on this box".format(self.total, "" if self.exhausted else "+")
        else:
            cnt = "{:,} games".format(self.total) if self.total else \
                "Couldn't load: %s" % self.load_error if self.load_error else "No games found"
        flt = [lab for on, lab in ((self.regions, self.REGION_CHOICES[self.region_i][1]),
                                   (self.players, "%d+ players" % self.players),
                                   (self.picks, "%d picked" % len(self.picks))) if on]
        if flt:
            cnt = ", ".join(flt + [cnt])
        title = self.title if not self.search else 'Results for "%s"' % self.search
        failed = not self.total and self.load_error
        # the title and the filters/count share the header: the title keeps at least 260
        room = max(260, min(520, lw - 40 - int(measure(self.f.small, cnt))))
        if measure(self.f.h3, title) <= room:
            tw = measure(self.f.h3, title)
            text(c, self.f.h3, title, (lx + 8, ly + 4), theme.TEXT)
        else:
            lines = wrap(self.f.small, title, room)[:2]
            tw = max(measure(self.f.small, ln) for ln in lines)
            for k, ln in enumerate(lines):
                text(c, self.f.small, ln, (lx + 8, ly - 2 + k * 18), theme.TEXT)
        text(c, self.f.small, cnt, (lx + lw - 8, ly + 8), theme.RED if failed else theme.DIM, "topright",
             maxw=300 if failed else lw - 40 - int(tw))
        ly += 40
        multi_plat = self.platform is None
        for i, r, on in self.row_rects(ly, x=lx, w=lw):
            rom = self.items[i]
            if on:
                focus_box(c, r)
            if rom is None:
                self.ensure(i)
                text(c, self.f.row, "…", (r.left + 44, r.centery), theme.FAINT, "midleft")
                continue
            st, j = self.rom_status(rom)
            ix = r.left + 24
            if st != "installed" and self.draw_pick(c, rom, (ix, r.centery)):
                pass
            elif st == "installed":
                installed_mark(c, (ix, r.centery))
            elif st == "active":
                frac = (j.done_bytes / j.total_bytes) if j.total_bytes else 0
                circle(c, theme.LINE, (ix, r.centery), 9, 2)
                if frac > 0:
                    arc(c, theme.BLUE, (ix - 9, r.centery - 9, 18, 18),
                                    1.5708 - 6.2832 * frac, 1.5708, 3)
            elif st == "failed":
                circle(c, theme.RED, (ix, r.centery), 9)
                text(c, self.f.tiny, "!", (ix, r.centery), theme.BG, "center")
            else:
                circle(c, theme.LINE, (ix, r.centery), 4)
            right_w = 0
            size = human_size(rom.get("fs_size_bytes"))
            sr = text(c, self.f.small, size, (r.right - 14, r.centery), theme.DIM, "midright")
            right_w = sr.width + 14
            if multi_plat:
                p = app.platform_by_id.get(rom.get("platform_id"))
                tr = tag(c, self.f.tiny, p["_system"] if p else rom.get("platform_slug") or "?",
                         (r.right - right_w - 12, r.centery), "midright", warn=not p)
                right_w += tr.width + 12
            name = rom_title(rom)
            nr = text(c, self.f.row, name, (r.left + 44, r.centery), theme.TEXT if on else theme.DIM,
                      "midleft", maxw=r.width - 44 - right_w - 70)
            regions = rom.get("regions") or []
            if regions:
                rg = "/".join(REGION_ABBR.get(x, x[:3]) for x in regions[:2])
                text(c, self.f.tiny, rg, (nr.right + 8, r.centery + 1), theme.DIM, "midleft")
        if self.loading:
            text(c, self.f.body, "Loading…", (lx + lw // 2, ly + 120), theme.DIM, "center")
        # scrollbar
        self.draw_scrollbar(c, lx + lw + 6, ly, n=self.total or 0)
        # detail panel
        if self.items and self.items[self.sel] is not None:
            self.draw_detail(c, self.items[self.sel])
        elif self.items:
            rrect(c, theme.PANEL, self.pan, 16)
            text(c, self.f.body, "Loading…", self.pan.center, theme.DIM, "center")

    def draw_detail(self, c, rom):
        app = self.app
        p = self.pan
        rrect(c, theme.PANEL, p, 16)
        cov = pygame.Rect((p.left + 24, p.top + 24), COVER_BOX)
        if app.cover(rom):
            cover(c, app, rom, cov, fit=True)
        else:
            rrect(c, theme.BG2, cov, 10)
            text(c, self.f.small, "no cover" if rom["id"] in app.covers and
                 app.covers[rom["id"]] is None else "…", cov.center, theme.FAINT, "center")
        tx, tw = cov.right + 20, p.right - cov.right - 44
        y = p.top + 24
        title = rom_title(rom)
        tl = wrap(self.f.h3, title, tw)
        tf, lh = (self.f.h3, 25) if len(tl) <= 3 else (self.f.body, 22)
        y = scroll_text(c, tf, title, (tx, y, tw, 5 * lh), theme.TEXT, key=rom["id"], line_h=lh,
                        slot="detail-title").bottom + 8
        why = getattr(self, "why", {}).get(rom["id"])    # (Games from RomM draws this panel too)
        if why:                                    # Recommended for you: RomM's reason, first
            text(c, self.f.small, "Why", (tx, y), theme.FAINT)
            used = scroll_text(c, self.f.cond, why, (tx, y + 17, tw, 3 * 20), theme.ACCENT_HI, key=rom["id"],
                               line_h=20, slot="detail-why")
            y += 25 + used.height
        md = rom.get("metadatum") or {}
        plat = app.platform_by_id.get(rom.get("platform_id"))
        year = rom_year(rom)
        size, fmt = human_size(rom.get("fs_size_bytes")), ""
        lay = app.layout(rom)
        if lay:
            if lay["unpacked"]:
                size += " · %s%s unpacked" % ("~" if lay["estimated"] else "", human_size(lay["unpacked"]))
            fmt = ("%d discs, " % lay["discs"] if lay["discs"] > 1 else "") + lay["label"]
            if fmt and lay["convert"]:
                fmt += " (becomes %s)" % lay["convert"]        # Rubik has no arrow glyph
        # action area
        ar = pygame.Rect(p.left + 24, p.bottom - 76, p.width - 48, VERB_H)
        self.app.hit(ar, lambda: self.panel_verb(rom))
        st, j = self.rom_status(rom)
        system = app.system_for(rom)
        notes = (st == "failed") + bool(system and not self.picking and st not in ("installed", "active")
                                        and self.variants(rom, system))
        bottom = ar.top - 12 - 22 * max(1, notes)       # above the button and its notes; a long one scrolls
        y = facts(c, self.f, [
            ("Platform", (plat.get("display_name") or plat["name"]) if plat else
             (rom.get("platform_display_name") or "?")),
            ("Released", year),
            ("Genre", ", ".join((md.get("genres") or [])[:2])),
            ("Players", md.get("player_count") or ""),
            ("Size", size),
            ("Format", fmt),
            ("Achievements", ("%d · %d points" % ra) if (ra := rommlib.ra_summary(rom)) and ra[0] else ""),
            ("Manual", "Included" if rommlib.manual_path(rom) else ""),
        ], (tx, y, tw, max(42, bottom - y)), cols=1, key=rom["id"], slot="detail-facts").bottom
        # summary
        sy = max(y, cov.bottom) + 18
        summ = rom.get("summary") or ""
        if summ and bottom - sy >= 20:                  # a long fact list leaves none: no summary over the button
            scroll_text(c, self.f.small, summ, (p.left + 24, sy, p.width - 48, bottom - sy), theme.DIM,
                        key=rom["id"], line_h=20, slot="detail-summary")
        if system and self.picking and st != "installed":           # A picks here
            on = self.pick_key(rom) in self.picks
            verb_button(c, self.f, ar, "Unpick" if on else "Pick", "quiet" if on else "go",
                        "%d picked" % self.pick_count())
            return
        verb_button(c, self.f, ar, glyph="", **game_verb(app, rom, (st, j), play="On this box",
                                                         play_detail="roms/%s" % system,
                                                         go_detail="to roms/%s" % system))
        if system and st not in ("installed", "active"):
            yy = ar.top - 22
            if st == "failed":
                text(c, self.f.small, j.message, (ar.left, yy), theme.RED, maxw=ar.width)
                yy -= 22
            var = self.variants(rom, system)
            if var:
                text(c, self.f.small, "Already here: " + var[0] + (" +%d more" % (len(var) - 1) if len(var) > 1 else ""),
                     (ar.left, yy), theme.AMBER, maxw=ar.width)

    def variants(self, rom, system):
        """Other versions of this game on the box (cached per rom)."""
        cache = self.__dict__.setdefault("_variants", {})
        if rom["id"] not in cache:
            if len(cache) > 300:
                cache.clear()
            cache[rom["id"]] = self.app.library.variants(rom, system)
        return cache[rom["id"]]


class SearchScreen(KeyboardScreen):
    """Search on the shared keyboard, the Top matches beside it (live, a moment after typing)."""
    MATCH_Y, MATCH_H = KeyboardScreen.PANEL.top + 56, 58

    def __init__(self, app, platform=None):
        super().__init__(app)
        self.platform = platform
        self.kb = Keyboard("Search", self.go, context="search", maxlen=60, y_key="space",
                           on_change=self.typed, on_touch=self.unpick)
        self.preview = []
        self.preview_total = None
        self.preview_q = None
        self.pending_at = None
        self.pick = None                  # the focused Top match; None: the keyboard has focus

    @property
    def query(self):
        return self.kb.value

    @query.setter
    def query(self, v):
        self.kb.value = v

    def crumbs(self):
        out = ["Search"]
        if self.platform:
            out.append(self.platform.get("display_name") or self.platform["name"])
        return out

    def hints(self):
        if self.pick is not None:
            return [("A", "Open"), ("B", "Keyboard")] + ([] if pads.INPUT_MODE["kb"] else [("START", "Search")])
        return self.kb.hints() + [("B", "Back")]

    def typed(self):
        self.pending_at = time.time() + 0.45
        if not self.query:
            self.preview, self.preview_total = [], None

    @property
    def text_entry(self):
        """A physical keyboard types here, except on the Top matches (Enter opens one)."""
        return self.pick is None

    def type_text(self, s):
        self.pick = None
        self.kb.type_text(s)

    def unpick(self):
        self.pick = None                  # a key tapped after a match was open types, not reopens it

    def open(self, rom):
        """A Top match opens the game's page (its verb and menu are there)."""
        self.app.push(GameScreen(self.app, rom))

    def tap_pick(self, i):
        self.pick = None                  # a tap opens it; focus stays with the keyboard
        self.open(self.preview[i])

    def match_y(self, i):
        return self.MATCH_Y + i * self.MATCH_H

    def pick_action(self, a):
        """Focus on the Top matches: up/down among them, left/right back to the
        keyboard row beside the focused one, B too."""
        n = len(self.preview[:8])
        if a in ("up", "down") and n:
            self.pick = max(0, min(n - 1, self.pick + (1 if a == "down" else -1)))
        elif a in ("left", "right", "back"):
            row = self.kb.row_at(self.match_y(self.pick) + 22, self.kb_rect())
            self.kb.enter("left" if a == "right" else "right", row)
            self.pick = None
        elif a == "ok" and n:
            self.open(self.preview[self.pick])
        elif a in ("start", "r1"):
            self.go()

    def action(self, a):
        if self.pick is not None:
            return self.pick_action(a)
        n = len(self.preview[:8])
        if a in ("left", "right") and n and self.kb.at_edge(a):
            # past the keyboard's edge: the match beside the focused row
            self.pick = max(0, min(n - 1, round((self.kb.row_mid(self.kb_rect()) - 22 - self.MATCH_Y) / self.MATCH_H)))
        elif a == "back":
            self.app.pop()
        else:
            self.kb.action(a)

    def go(self):
        q = self.query.strip()
        if len(q) < 2:
            self.app.toast("Type at least 2 characters", theme.AMBER)
            return
        self.app.push(GameListScreen(self.app, "Search", platform=self.platform, search=q))

    def live_preview(self):
        if self.pending_at and time.time() >= self.pending_at:
            self.pending_at = None
            q = self.query.strip()
            if len(q) < 2:
                self.preview, self.preview_total = [], None
                return
            app = self.app
            pids = [self.platform["id"]] if self.platform else [p["id"] for p in app.platforms]

            def done(page, q=q):
                if q == self.query.strip():
                    self.preview = page.get("items", [])
                    self.preview_total = page.get("total")
                    if self.pick is not None:
                        self.pick = min(self.pick, len(self.preview[:8]) - 1) if self.preview else None
            def call():
                page = app.api.roms(platform_ids=pids, search=q, limit=250)
                page["items"] = rank_search(q, page.get("items", []))[:8]
                return page
            app.bg(call, done)

    def draw(self, c):
        self.live_preview()
        f = self.f
        where = (self.platform.get("display_name") or self.platform["name"]) if self.platform else "all games"
        p = self.draw_keyboard(c, "Search " + where, focused=self.pick is None, font=f.h2,
                               note="{L} symbols · {R} or {START} searches")
        x, w = p.left + 24, p.width - 48
        text(c, f.h3, "Top matches", (x, p.top + 20), theme.DIM)
        if self.preview_total is not None:
            text(c, f.small, "{:,} total".format(self.preview_total), (p.right - 24, p.top + 22), theme.FAINT,
                 "topright")
        if not self.preview:
            msg = "Start typing and matches show up here." if len(self.query.strip()) < 2 else \
                ("Searching…" if self.preview_total is None or self.pending_at else "No matches")
            scroll_text(c, f.body, msg, (x, self.MATCH_Y, w, 52), theme.DIM, line_h=26, bg=theme.PANEL,
                        slot="search-empty")
        for i, rom in enumerate(self.preview[:8]):
            y = self.match_y(i)
            r = (p.left + 12, y - 6, p.width - 24, self.MATCH_H - 2)
            self.app.hit(r, lambda i=i: self.tap_pick(i))
            on = i == self.pick
            if on:
                focus_box(c, r, 12)
            pl = self.app.platform_by_id.get(rom.get("platform_id"))
            marquee(c, f.row, rom_title(rom), (x, y), theme.TEXT, maxw=w, focused=on, key=rom.get("id"))
            sr = text(c, f.small, pl["_system"] if pl else "?", (x, y + 26), theme.FAINT)
            text(c, f.small, human_size(rom.get("fs_size_bytes")), (sr.right + 16, y + 26), theme.FAINT)
        if self.preview:
            rich_text(c, f, f.small, "{START} shows all of them", (x, p.bottom - 34), theme.ACCENT_HI, maxw=w)
