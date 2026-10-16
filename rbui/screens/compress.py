"""Compress games already here: by system, one system, what can't be converted."""
import os

import rommlib
from rommlib import human_size
from rbui import pads, theme
from rbui.theme import H, W
from rbui.draw import scroll_text, text
from rbui.screens.base import letter_of, ListScreen
from rbui.screens.box_data import plural
from rbui.modals import Confirm
from rbui.widgets.layout import (Panel, ROWS_TOP, bubble_at, hint_actions, list_head, list_row, need_width,
                                 split)


# what each converter does to the files, for Compress's panel (by its label)
CONVERTER_NOTE = {
    "CHD": "Each disc image becomes a CHD: the same game byte for byte, read directly by the emulator. "
           "The original goes only after the CHD checks out.",
    "RVZ": "Each disc becomes an RVZ, Dolphin's own format: lossless, with the disc's padding dropped. "
           "The original goes only after the RVZ checks out.",
    "squashfs": "Each Xbox image becomes a compressed squashfs the emulator reads as it is. "
                "The original goes only after it checks out.",
    "ZCCI": "Each 3DS game becomes a .zcci, compressed and read directly by Azahar. "
            "The original goes only after it checks out.",
    "ZAR": "Each Xbox 360 disc becomes a .zar archive the emulator reads directly. "
           "The original goes only after it checks out.",
    "WUA": "Each Wii U disc's game is decrypted into a .wua, which Cemu opens directly. "
           "The original goes only after it checks out.",
    "WUX": "Each Wii U disc becomes a .wux: the same disc, compressed, no keys needed. "
           "The original goes only after it checks out.",
    "PS3 folder": "Each disc image becomes a .ps3 game folder that EmulationStation lists right away. "
                  "It's the game unpacked, so about the same size.",
}


def conversion_ratios():
    """{mode: output / source} over the conversions this box has finished."""
    return rommlib.conversion_ratios(rommlib.conversions_state()["results"])


def panel_estimate(p, cands, ratios):
    """Now and after, as two bars: after from this box's own conversions, else "about"."""
    now = sum(c["size"] for c in cands)
    after, measured = rommlib.estimate_after(cands, ratios)
    p.pair("Now", human_size(now))
    p.bar(1.0, theme.FAINT)
    p.space(4)
    saved = now - after
    p.pair("After" if measured else "After, about",
           "%s, saves %s" % (human_size(after), human_size(saved)) if saved > 0 else human_size(after),
           theme.GREEN if saved > 0 else theme.DIM)
    p.bar(after / now if now else 0)
    p.space(6)


def compress_scan(skipped=None):
    """Games here that could still be compressed (see rb_chd_plan.candidates);
    skipped gets (system, file, reason) for the ones that can't be."""
    import rb_chd_plan
    return rb_chd_plan.candidates(skipped=skipped)


def cant_convert_rows(skipped):
    """[{"system", "n", "size", "skipped": [(file, reason)], "cant": True}] in the planner's order:
    images a converter applies to but that can't be converted (an encrypted dump, a bad .cso…)."""
    by = {}
    for system, f, why in skipped:
        r = by.setdefault(system, {"system": system, "n": 0, "size": 0, "skipped": [], "cant": True,
                                   "enabled": False})
        r["n"] += 1
        try:
            r["size"] += os.path.getsize(f)
        except OSError:
            pass
        r["skipped"].append((f, why))
    return list(by.values())


def needs_text(app, c):
    """What a candidate (or a system's row) waits for, starting lower case: its
    switch, or, with the switch on, the tool it needs ("wiiu-tools is
    downloading…", "wiiu-tools isn't installed yet: Settings → Wii U to .wux")."""
    conv = rommlib.CONVERTER.get(c.get("mode"))
    spec = conv.tool if conv else None
    if spec and rommlib.config_value(app.cfg, conv.setting) and spec.supported():
        if getattr(app, spec.setting + "_busy", False):
            return "%s is downloading…" % spec.slug
        if not spec.installed():
            return "%s isn't installed yet: Settings → %s" % (spec.slug, conv.setting_name)
    return "turn on %s in Settings" % c["setting"]


def needs_toast(app, c):
    t = needs_text(app, c)
    return "T" + t[1:] + " first" if t.startswith("turn on") else t


def compress_by_system(cands):
    """[{"system", "n", "size", "label", "enabled", "setting", "mode"}], biggest first
    (setting and mode: of a candidate that needs its switch, if any)."""
    by = {}
    for c in cands:
        r = by.setdefault(c["system"], {"system": c["system"], "n": 0, "size": 0, "labels": [],
                                        "enabled": c["enabled"], "setting": c["setting"], "mode": c["mode"]})
        r["n"] += 1
        r["size"] += c["size"]
        if c["label"] not in r["labels"]:
            r["labels"].append(c["label"])
        if r["enabled"] and not c["enabled"]:
            r["setting"], r["mode"] = c["setting"], c["mode"]
        r["enabled"] = r["enabled"] and c["enabled"]
    for r in by.values():
        r["label"] = "/".join(r["labels"])
    return sorted(by.values(), key=lambda r: -r["size"])


def compress_confirm(app, title, cands, done):
    """Ask before queueing a whole system: it can run for hours."""
    size = sum(c["size"] for c in cands)
    app.modal = Confirm(app, title,
                        "%s, %s to work through: this can take hours. It runs in the background, pauses "
                        "while you play, and each original goes only after its copy checks out."
                        % (plural(len(cands), "game"), human_size(size)),
                        "Compress %d" % len(cands), lambda: compress_queue(app, cands, done))


def compress_queue(app, cands, done=None):
    import rb_chd_plan

    def run():
        return rb_chd_plan.queue(cands)

    def ok(n):
        prep = sum(1 for c in cands if c.get("prep") and c["enabled"])
        if n > prep:
            app.spawn_helper("rb_chd.py")
            app.toast("Queued %s: they compress in the background (On this box, Conversions)"
                      % plural(n - prep, "game") + ("; %d Xbox made bootable" % prep if prep else ""), theme.GREEN)
        else:
            app.toast("Made %s bootable" % plural(n, "Xbox game"), theme.GREEN)
        if done:
            done()
    app.bg(run, ok)


class CompressScreen(ListScreen):
    """Compress games already here: every system with disc images a converter
    still applies to, biggest first. A: that system's games; X: all of them."""
    ROWS, ROW_H = 9, 58

    def __init__(self, app):
        super().__init__(app)
        self.cands, self.rows = None, []
        self.sel = self.scroll = 0

    def enter(self):
        self.load_error = None
        failed = self.load_failed("cands")

        def scan():
            skipped = []
            return compress_scan(skipped), skipped, conversion_ratios()
        self.app.bg(scan, lambda r: self._loaded(*r), lambda e: (setattr(self, "rows", []), failed(e)))

    ratios = {}

    def _loaded(self, cands, skipped=(), ratios=None):
        self.cands = cands
        self.ratios = ratios or {}
        self.rows = compress_by_system(cands) + cant_convert_rows(skipped)    # those it can't: last, never queued
        self.clamp()

    def listed(self):
        return self.rows                 # set with cands, which every caller checks first

    def crumbs(self):
        return self.trail("Compress")

    def hints(self):
        if not self.rows:
            return [("B", "Back")]
        r = self.rows[self.sel]
        if r.get("cant"):
            return self.list_hints(("A", "Why"))
        return self.list_hints(("A", "Games"), *[("X", "Compress all %s" % self.app.system_name(r["system"]))] if r["enabled"] else [])

    def action(self, a):
        if not self.rows:
            return super().action(a)
        if self.nav(a):
            return
        r = self.rows[self.sel]
        if r.get("cant") and a in ("ok", "x"):
            if a == "ok":
                self.app.push(CantConvertScreen(self.app, r["system"], r["skipped"]))
            else:
                self.app.toast("These can't be converted: {A} shows why", theme.AMBER)
        elif a == "ok":
            self.app.push(CompressSystemScreen(self.app, r["system"]))
        elif a == "x":
            if not r["enabled"]:
                self.app.toast(needs_toast(self.app, r), theme.AMBER)
            else:
                compress_confirm(self.app, "Compress every %s game here?" % self.app.system_name(r["system"]),
                                 [c for c in self.cands if c["system"] == r["system"]], self.enter)
        else:
            super().action(a)

    def status(self, r):
        """A row's status: what it becomes, or what it waits for."""
        if r.get("cant"):
            return "can't convert", theme.AMBER
        if r["enabled"]:
            return "to " + r["label"], theme.ACCENT_HI
        return ("needs a setting" if needs_text(self.app, r).startswith("turn on") else "needs its tool"), theme.FAINT

    def draw(self, c):
        if self.cands is None:
            text(c, self.f.h2, "Looking through every system…", (W // 2, H // 2), theme.DIM, "center")
            return
        if self.draw_load_error(c):
            return
        if not self.rows:
            text(c, self.f.h3, "Nothing left to compress.", (56, 86), theme.GREEN)
            scroll_text(c, self.f.body, "Everything a converter supports is compressed or queued. Formats with a "
                        "Settings switch show up once it's on.", (56, 122, W - 112, 3 * 26), theme.DIM, bg=theme.BG,
                        line_h=26, slot="compress-empty")
            return
        subs = ["%s, %s" % (plural(r["n"], "game"), human_size(r["size"])) for r in self.rows]
        lst, pan = split(need_width(self.f, [(self.app.system_name(r["system"]), sub, self.status(r)[0]) for r, sub in zip(self.rows, subs)]))
        ok = [r for r in self.rows if r["enabled"]]
        if ok:
            list_head(c, self.f, lst, "%s could be smaller" % plural(sum(r["n"] for r in ok), "game"),
                      "%s to work through" % human_size(sum(r["size"] for r in ok)))
        else:
            list_head(c, self.f, lst, "Nothing left to compress", color=theme.GREEN)
        for i, r, on in self.row_rects(ROWS_TOP, x=lst.left, w=lst.width):
            row = self.rows[i]
            st, col = self.status(row)
            list_row(c, self.f, r, on, self.app.system_name(row["system"]), subs[i], st, col,
                     label_color=None if row["enabled"] or row.get("cant") else theme.FAINT, slot="compress")
        self.draw_panel(c, pan, self.rows[self.sel])

    def draw_panel(self, c, pan, row):
        p = Panel(c, self.f, pan, app=self.app, key=self.sel, slot="compress-panel")
        p.actions(hint_actions(self))
        p.title(self.app.system_name(row["system"]), "%s, %s" % (plural(row["n"], "game"), human_size(row["size"])))
        if row.get("cant"):
            p.prose("A converter takes these, but they can't be converted as they are, so they're left alone.",
                    lines=3)
            for f, why in row["skipped"][:4]:
                p.prose(why[:1].upper() + why[1:], label=os.path.basename(f), lines=2)
            return
        games = [cd for cd in self.cands if cd["system"] == row["system"]]
        if not row["enabled"]:
            p.prose("First %s. Then these compress like the others." % needs_text(self.app, row), lines=4)
        panel_estimate(p, games, self.ratios)
        p.prose(CONVERTER_NOTE.get(row["labels"][0], "Converted to %s in the background." % row["label"]), lines=4)
        p.rule()
        if p.room() >= 48:
            text(c, self.f.small, "Biggest", (p.x, p.y), theme.FAINT)
            p.space(24)
        games.sort(key=lambda cd: -cd["size"])
        shown = 0
        for cd in games:
            if p.room() < (24 if shown == len(games) - 1 else 48):      # room for "and N more" under it
                break
            p.pair(cd["name"] or os.path.splitext(os.path.basename(cd["root"]))[0], human_size(cd["size"]),
                   theme.FAINT, theme.DIM)
            shown += 1
        if shown < len(games):
            text(c, self.f.small, "and %d more" % (len(games) - shown), (p.x, p.y), theme.FAINT)


class CantConvertScreen(ListScreen):
    """One system's images a converter applies to but can't convert (an
    encrypted 3DS dump, a PS3 disc without its key, a damaged .cso…), each
    with the reason. Nothing to do here: they're left as they are."""
    ROWS, ROW_H = 9, 58

    def __init__(self, app, system, skipped):
        super().__init__(app)
        self.system, self.items = system, list(skipped)
        self.sel = self.scroll = 0
        self.rows = [(os.path.basename(f), why[:1].upper() + why[1:], self.size(f)) for f, why in self.items]

    def crumbs(self):
        return self.trail("%s: can't convert" % self.app.system_name(self.system))

    def hints(self):
        return [("L", "Page up"), ("R", "Page down"), ("B", "Back")] if len(self.items) > self.ROWS \
            else [("B", "Back")]

    def action(self, a):
        if not self.nav(a):
            super().action(a)

    @staticmethod
    def size(f):
        try:
            return human_size(os.path.getsize(f))
        except OSError:
            return ""

    def draw(self, c):
        rows = self.rows
        lst, pan = split(need_width(self.f, rows))
        list_head(c, self.f, lst, "%s can't be converted" % plural(len(self.items), "game"))
        for i, r, on in self.row_rects(ROWS_TOP, x=lst.left, w=lst.width):
            name, why, size = rows[i]
            list_row(c, self.f, r, on, name, why, size, sub_color=theme.AMBER, slot="cant")
        if not rows:
            return
        name, why, size = rows[self.sel]
        fix = {"3ds": "a decrypted dump", "ps3": "the disc's key in bios/ps3keys"}.get(self.system, "a good copy")
        p = Panel(c, self.f, pan, key=self.sel, slot="cant-panel")
        p.title(name, size)
        p.prose(why + ".", label="Why", color=theme.AMBER, lines=4)
        p.prose("It's left as it is. Replace it with %s and it shows up in Compress." % fix, label="What to do",
                lines=4)
        p.fact("Where", "roms/%s/%s" % (self.system, os.path.relpath(self.items[self.sel][0], os.path.join(
            rommlib.ROMS_ROOT, self.system))))


class CompressSystemScreen(ListScreen):
    """One system's games that could still be compressed: A queues one; the
    X Actions menu queues them all, or the picked ones (asks first: it can take hours)."""
    ROWS, ROW_H = 9, 58
    PICKS, PICK_WHAT, JUMP = True, "games", "A–Z"

    def __init__(self, app, system):
        super().__init__(app)
        self.system = system
        self.cands = None
        self.sel = self.scroll = 0

    def enter(self):
        import rb_chd_plan
        self.load_error = None
        self.app.bg(lambda: (rb_chd_plan.candidates([self.system]), conversion_ratios()), lambda r: self._loaded(*r),
                    self.load_failed("cands"))

    ratios = {}

    def _loaded(self, cands, ratios=None):
        self.cands = cands
        self.ratios = ratios or {}
        self.clamp()

    def listed(self):
        return self.cands or []

    def crumbs(self):
        return self.trail(self.app.system_name(self.system))

    def title_of(self, c):
        return c["name"] or os.path.splitext(os.path.basename(c["root"]))[0]

    def pick_key(self, c):
        return c["root"]

    def jump_label(self, c):
        return letter_of(self.title_of(c))

    def hints(self):
        if not self.cands:
            return [("B", "Back")]
        if not self.cands[0]["enabled"]:
            return self.list_hints()
        return self.list_hints(("A", "Compress this one"), ("X", pads.ACTIONS))

    def list_actions(self):
        return [("Compress all %d" % len(self.cands), "%s · asks first: it can take hours" % human_size(
            sum(x["size"] for x in self.cands)), lambda: compress_confirm(
                self.app, "Compress all %s here?" % plural(len(self.cands), "%s game" % self.app.system_name(self.system)),
                list(self.cands), self.enter))]

    def pick_entries(self, cands):
        def done():
            self.picks.clear()
            self.picking = False
            self.enter()
        return [("Compress %d picked" % len(cands), human_size(sum(x["size"] for x in cands)),
                 lambda: compress_confirm(self.app, "Compress %s?" % plural(len(cands), "picked game"),
                                          cands, done))]

    def action(self, a):
        if not self.cands:
            return super().action(a)
        if self.nav(a):
            return
        cand = self.cands[self.sel]
        if a in ("ok", "x") and not cand["enabled"]:
            self.app.toast(needs_toast(self.app, cand), theme.AMBER)
        elif self.pick_action(a):
            return
        elif a == "ok":
            compress_queue(self.app, [cand], self.enter)
        else:
            super().action(a)

    def draw(self, c):
        if self.cands is None:
            text(c, self.f.h2, "Looking…", (W // 2, H // 2), theme.DIM, "center")
            return
        if self.draw_load_error(c):
            return
        if not self.cands:
            text(c, self.f.h3, "Nothing left to compress here.", (56, 86), theme.GREEN)
            return
        row = lambda cand: (self.title_of(cand), self.sub(cand), human_size(cand["size"]))   # noqa: E731
        lst, pan = split(need_width(self.f, [row(cand) for cand in self.cands[:200]], mark=self.showing_picks()))
        self.BUBBLE_AT = bubble_at(lst)
        list_head(c, self.f, lst, "%s to %s" % (plural(len(self.cands), "game"), self.cands[0]["label"]),
                  human_size(sum(x["size"] for x in self.cands)))
        for i, r, on in self.row_rects(ROWS_TOP, x=lst.left, w=lst.width):
            cand = self.cands[i]
            title, sub, size = row(cand)
            list_row(c, self.f, r, on, title, sub, size, mark=lambda ctr, cand=cand: self.draw_pick(c, cand, ctr),
                     slot="compress-system")
        cand = self.cands[self.sel]
        p = Panel(c, self.f, pan, app=self.app, key=self.sel, slot="compress-system-panel")
        p.actions(hint_actions(self))
        p.title(self.title_of(cand), self.sub(cand))
        if not cand["enabled"]:
            p.prose("First %s." % needs_text(self.app, cand), lines=3)
        panel_estimate(p, [cand], self.ratios)
        if cand["cut"]:
            p.prose("Made bootable first: the image is cut to its game partition (%s less)."
                    % human_size(cand["cut"]), lines=3)
        p.prose(CONVERTER_NOTE.get(cand["label"], "Converted to %s in the background." % cand["label"]), lines=4)
        p.fact("Where", os.path.relpath(cand["root"], os.path.dirname(rommlib.ROMS_ROOT)))

    def sub(self, cand):
        """Its format, discs, and where it is when the name doesn't say."""
        discs = len(cand["files"])
        ext = os.path.splitext(cand["files"][0])[1].lower().lstrip(".")
        sub = {"cue": "CUE/BIN", "gdi": "GDI"}.get(ext, ext.upper())
        if discs > 1:
            sub = "%d discs, %s" % (discs, sub)
        where = os.path.relpath(cand["root"], os.path.join(rommlib.ROMS_ROOT, self.system))
        if os.path.splitext(where)[0] != self.title_of(cand):          # only when it says something new
            sub += " · " + where
        return sub
