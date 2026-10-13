"""Compress games already here: by system, one system, what can't be converted."""
import os

import rommlib
from rommlib import human_size
from rbui import theme
from rbui.theme import H, W
from rbui.draw import focus_box, text
from rbui.screens.base import letter_of, ListScreen
from rbui.screens.box_data import plural
from rbui.modals import Confirm


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


def compress_by_system(cands):
    """[{"system", "n", "size", "label", "enabled", "setting"}], biggest first."""
    by = {}
    for c in cands:
        r = by.setdefault(c["system"], {"system": c["system"], "n": 0, "size": 0, "labels": [],
                                        "enabled": c["enabled"], "setting": c["setting"]})
        r["n"] += 1
        r["size"] += c["size"]
        if c["label"] not in r["labels"]:
            r["labels"].append(c["label"])
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
            return compress_scan(skipped), skipped
        self.app.bg(scan, lambda r: self._loaded(*r), lambda e: (setattr(self, "rows", []), failed(e)))

    def _loaded(self, cands, skipped=()):
        self.cands = cands
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
        return self.list_hints(("A", "Games"), *[("X", "Compress all %s" % r["system"])] if r["enabled"] else [])

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
                self.app.toast("Turn on %s in Settings first" % r["setting"], theme.AMBER)
            else:
                compress_confirm(self.app, "Compress every %s game here?" % r["system"],
                                 [c for c in self.cands if c["system"] == r["system"]], self.enter)
        else:
            super().action(a)

    def draw(self, c):
        if self.cands is None:
            text(c, self.f.h2, "Looking through every system…", (W // 2, H // 2), theme.DIM, "center")
            return
        if self.draw_load_error(c):
            return
        if not self.cands:
            text(c, self.f.h3, "Nothing left to compress.", (56, 86), theme.GREEN)
            text(c, self.f.body, "Every disc game here that a converter supports is compressed already, "
                 "or queued. Formats with a Settings switch (RVZ, squashfs, .zcci, .zar, .wux, .wua, PS3 folders) show "
                 "up once it's on.", (56, 124), theme.DIM, maxw=W - 112)
            if not self.rows:
                return
        else:
            ok = [r for r in self.rows if r["enabled"]]
            text(c, self.f.h3, "%s could be smaller, %s to work through" % (
                plural(sum(r["n"] for r in ok), "game"), human_size(sum(r["size"] for r in ok))), (56, 86), theme.TEXT)
        for i, r, on in self.row_rects(164 if not self.cands else 124):
            r_ = self.rows[i]
            if on:
                focus_box(c, r)
            if r_.get("cant"):
                text(c, self.f.row, r_["system"], (r.left + 22, r.top + 5), theme.TEXT if on else theme.DIM)
                text(c, self.f.small, "%s, %s · left as they are" % (plural(r_["n"], "game"),
                     human_size(r_["size"])), (r.left + 22, r.top + 30), theme.FAINT, maxw=r.width - 200)
                text(c, self.f.small, "can't convert", (r.right - 20, r.centery), theme.AMBER, "midright")
                continue
            col = (theme.TEXT if on else theme.DIM) if r_["enabled"] else theme.FAINT
            text(c, self.f.row, r_["system"], (r.left + 22, r.top + 5), col)
            sub = "%s, %s" % (plural(r_["n"], "game"), human_size(r_["size"]))
            text(c, self.f.small, sub if r_["enabled"] else sub + " · turn on %s in Settings" % r_["setting"],
                 (r.left + 22, r.top + 30), theme.FAINT, maxw=r.width - 200)
            text(c, self.f.small, ("to " + r_["label"]) if r_["enabled"] else "needs a setting",
                 (r.right - 20, r.centery), theme.ACCENT_HI if r_["enabled"] else theme.FAINT, "midright")


class CantConvertScreen(ListScreen):
    """One system's images a converter applies to but can't convert (an
    encrypted 3DS dump, a PS3 disc without its key, a damaged .cso…), each
    with the reason. Nothing to do here: they're left as they are."""
    ROWS, ROW_H = 9, 58

    def __init__(self, app, system, skipped):
        super().__init__(app)
        self.system, self.items = system, list(skipped)
        self.sel = self.scroll = 0

    def crumbs(self):
        return self.trail("%s: can't convert" % self.system)

    def hints(self):
        return [("L", "Page up"), ("R", "Page down"), ("B", "Back")] if len(self.items) > self.ROWS \
            else [("B", "Back")]

    def action(self, a):
        if not self.nav(a):
            super().action(a)

    def draw(self, c):
        text(c, self.f.h3, "%s in roms/%s can't be converted" % (plural(len(self.items), "game"), self.system),
             (56, 86), theme.TEXT)
        fix = {"3ds": "a decrypted dump", "ps3": "the disc's key in bios/ps3keys"}.get(self.system, "a good copy")
        text(c, self.f.small, "They're left as they are. Fix or replace the file (%s) and it shows up in "
             "Compress." % fix, (56, 120), theme.DIM, maxw=W - 112)
        for i, r, on in self.row_rects(156):
            f, why = self.items[i]
            if on:
                focus_box(c, r)
            text(c, self.f.row, os.path.basename(f), (r.left + 22, r.top + 5), theme.TEXT if on else theme.DIM,
                 maxw=r.width - 200)
            text(c, self.f.small, why[:1].upper() + why[1:], (r.left + 22, r.top + 30), theme.AMBER, maxw=r.width - 200)
            try:
                size = human_size(os.path.getsize(f))
            except OSError:
                size = ""
            text(c, self.f.small, size, (r.right - 20, r.centery), theme.DIM, "midright")


class CompressSystemScreen(ListScreen):
    """One system's games that could still be compressed: A queues one; the
    X menu queues them all, or the picked ones (asks first: it can take hours)."""
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
        self.app.bg(lambda: rb_chd_plan.candidates([self.system]), self._loaded, self.load_failed("cands"))

    def _loaded(self, cands):
        self.cands = cands
        self.clamp()

    def listed(self):
        return self.cands or []

    def crumbs(self):
        return self.trail(self.system)

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
        return self.list_hints(("A", "Compress this one"), ("X", "Menu"))

    def list_actions(self):
        return [("Compress all %d" % len(self.cands), "%s · asks first: it can take hours" % human_size(
            sum(x["size"] for x in self.cands)), lambda: compress_confirm(
                self.app, "Compress all %s here?" % plural(len(self.cands), "%s game" % self.system),
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
            self.app.toast("Turn on %s in Settings first" % cand["setting"], theme.AMBER)
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
        text(c, self.f.h3, "%s, %s · to %s" % (plural(len(self.cands), "game"),
             human_size(sum(x["size"] for x in self.cands)), self.cands[0]["label"]), (56, 86), theme.TEXT)
        for i, r, on in self.row_rects(124):
            cand = self.cands[i]
            if on:
                focus_box(c, r)
            tx = r.left + (48 if self.draw_pick(c, cand, (r.left + 22, r.centery)) else 22)
            text(c, self.f.row, self.title_of(cand), (tx, r.top + 5), theme.TEXT if on else theme.DIM,
                 maxw=r.width - 200)
            discs = len(cand["files"])
            ext = os.path.splitext(cand["files"][0])[1].lower().lstrip(".")
            sub = {"cue": "CUE/BIN", "gdi": "GDI"}.get(ext, ext.upper())
            if discs > 1:
                sub = "%d discs, %s" % (discs, sub)
            where = os.path.relpath(cand["root"], os.path.join(rommlib.ROMS_ROOT, self.system))
            if os.path.splitext(where)[0] != self.title_of(cand):          # only when it says something new
                sub += " · " + where
            if cand["cut"]:
                sub += " · made bootable first"
            text(c, self.f.small, sub, (tx, r.top + 30), theme.FAINT, maxw=r.width - 200)
            text(c, self.f.small, human_size(cand["size"]), (r.right - 20, r.centery), theme.DIM, "midright")
