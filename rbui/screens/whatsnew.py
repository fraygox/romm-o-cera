"""What's new: this version's release notes, once after an update (and from Settings, Updates)."""
import time

import rommlib
from rbui import theme
from rbui.draw import scroll_nudge
from rbui.screens.about import DocsScreen
from rbui.screens.base import Screen
from rbui.widgets.layout import Panel, split

NOTES_SLOT = "whatsnew-notes"
NUDGE = 2.0                    # seconds of reading a scroll press moves the notes


def previous_version(cfg):
    """The version this one replaced: the backup an update keeps, else the last notes shown."""
    prev = rommlib.backup_version()
    if not prev:
        seen = rommlib.config_value(cfg, "notes_seen") or ""
        prev = seen if seen and seen != rommlib.VERSION_LABEL else None
    return prev if prev != rommlib.VERSION_LABEL else None


def notes_at_start(cfg):
    """(release, previous version) when What's new is due and GitHub has the notes, else None
    (offline too: asked again next start). Blocking: run it off the UI thread."""
    rel = rommlib.whats_new(cfg)
    return (rel, previous_version(cfg)) if rel else None


def read_time(lines):
    words = sum(len(s.split()) for _, s in lines)
    mins = max(1, round(words / 200.0))
    return "a minute's read" if mins == 1 else "%d minutes' read" % mins


class WhatsNewScreen(Screen):
    """The notes on the left (headings and bullets, scrolling), the version jump and channel on
    the right. A or B: Continue (remembered: shown once per version). X: the full notes on the
    phone (QR code). rel None: the notes are read from GitHub first."""

    def __init__(self, app, rel=None, prev=None):
        super().__init__(app)
        self.rel, self.prev = rel, prev
        self.lines = rommlib.release_note_lines(rel["body"]) if rel else None
        self.failed = False
        self.over = False
        if rel is None:
            def done(res):
                rel, self.prev = res
                self.failed = rel is None
                self.rel, self.lines = rel, rommlib.release_note_lines(rel["body"]) if rel else []
            app.bg(lambda: (rommlib.release_notes(), previous_version(app.cfg)), done,
                   lambda e: setattr(self, "failed", True))

    @property
    def url(self):
        return (self.rel or {}).get("url") or rommlib.PROJECT_URL + "/releases"

    def crumbs(self):
        return ["What's new"]

    def hints(self):
        h = [("A", "Continue"), ("X", "Full notes")]
        if self.over:
            h += [("L", "Scroll up"), ("R", "Scroll down")]
        return h

    def done(self):
        rommlib.mark_notes_seen(self.app.cfg)
        self.app.pop()

    def full_notes(self):
        self.app.push(DocsScreen(self.app, self.url, "Release notes",
                                 "Every change in v%s, with its links, on GitHub." % rommlib.VERSION_LABEL,
                                 crumbs=("What's new", "Full notes")))

    def action(self, a):
        if a in ("ok", "back"):
            self.done()
        elif a == "x":
            self.full_notes()
        elif a in ("up", "down", "l1", "r1"):
            scroll_nudge(NOTES_SLOT, NUDGE * (3 if a in ("l1", "r1") else 1) * (-1 if a in ("up", "l1") else 1))
        elif a == "quit":
            super().action(a)

    def draw(self, c):
        f = self.f
        lst, pan = split(None)
        p = Panel(c, f, lst, self.app, key=rommlib.VERSION_LABEL, slot="whatsnew-left")
        if self.lines:
            p.title("What's new", note=read_time(self.lines))
            self.over = p.doc(self.lines, slot=NOTES_SLOT)
        else:
            p.title("What's new")
            p.prose("GitHub couldn't be reached for this version's notes. {X} shows where to read them."
                    if self.failed or self.lines == [] else "Reading the notes from GitHub…", lines=3)
            self.over = False
        r = Panel(c, f, pan, self.app, key="whatsnew", slot="whatsnew-right")
        r.actions([("X", "Full notes on your phone", self.full_notes)])
        r.verb("Continue", "go", fn=self.done)
        nightly = rommlib.config_value(self.app.cfg, "update_channel") == "nightly"
        base, build = rommlib.version_parts(rommlib.VERSION_LABEL)     # a nightly's label won't fit a title
        r.title("v" + base, sub=build or None)
        released = time.strptime(rommlib.RELEASE_DATE, "%Y-%m-%d")
        r.facts([("Channel", "Nightly" if nightly else "Stable"),
                 ("Released", "%d %s %d" % (released.tm_mday, time.strftime("%b", released), released.tm_year))]
                + ([("Updated from", rommlib.short_version(self.prev))] if self.prev else []),
                cols=2, lines=2 if self.prev else 1)
        r.rule()
        r.prose("Your games, saves and settings are as they were. Nothing to do.", color=theme.DIM)

