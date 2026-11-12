"""What's playing in the app: a queue of tracks from RomM's jukebox. Each is fetched into
the listening cache (or found on the box already) and played through the mixer's music
stream; the next one is fetched while the current plays. It goes on while the owner
browses, and stops with the app."""
import random
import time

import pygame
import rommlib
from rbui import theme


def track_title(t):
    """What a track is called: its title, else its file's name."""
    return t.get("title") or (t.get("file_name") or "").rpartition(".")[0] or t.get("file_name") or "Track"


class Player:
    FAILS = 3                 # tracks failing in a row before the queue gives up
    FETCHES = 2               # tracks on their way at once: skipping quickly through a list doesn't start one each
    RESTART = 3.0             # Previous this far into a track goes to its start; sooner, to the one before
    SETTLE = 0.4              # a stream just started isn't asked whether it has ended

    def __init__(self, app):
        self.app = app
        self.out = None           # the music stream: pygame's when its mixer is up (tests give their own)
        self.queue = []           # the tracks, in their list's order
        self.order = []           # the queue's indexes in the order they play
        self.at = 0               # where in order
        self.state = "stopped"    # "loading", "playing", "paused"
        self.shuffle = False
        self.what, self.key = "", None      # the list the queue came from: its name, and what tells it apart
        self.path = None          # the file being played
        self.fetching = set()     # track ids on their way
        self.fails = 0
        self.error = ""           # why it stopped, when it wasn't asked to
        self.began = 0.0
        self.rng = random.Random()

    def stream(self):
        if self.out is not None:
            return self.out
        return pygame.mixer.music if pygame.mixer.get_init() else None

    def current(self):
        return self.queue[self.order[self.at]] if self.queue and self.at < len(self.order) else None

    def active(self):
        return self.state != "stopped"

    def playing(self, t):
        """t is the track that's loading, playing or paused."""
        cur = self.current() if self.active() else None
        return bool(cur and t and cur.get("rom_file_id") == t.get("rom_file_id"))

    # ---------------------------------------------------------------- what plays

    def play(self, tracks, start=None, what="", key=None, shuffle=None):
        """Make tracks the queue (the ones the box can play) and play: from start (one of
        them), else the first, or any when shuffled. shuffle: None keeps it as it is.
        False, with a word why, when nothing can play."""
        tracks = [t for t in tracks or [] if isinstance(t.get("rom_file_id"), int) and rommlib.music_playable(t)]
        if not tracks:
            self.app.toast("Nothing here is in a format the app can play", theme.AMBER)
            return False
        if self.stream() is None:
            self.app.toast("The app has no sound on this box, so it can't play music", theme.AMBER)
            return False
        if shuffle is not None:
            self.shuffle = bool(shuffle)
        self.queue, self.what, self.key = tracks, what, key
        fid = start.get("rom_file_id") if start else None
        self.reorder(next((i for i, t in enumerate(tracks) if t["rom_file_id"] == fid), None))
        self.fails = 0
        self.start()
        return True

    def reorder(self, first=None):
        """Lay out the order the queue plays in; first (an index of the queue) is where it is now."""
        n = len(self.queue)
        if self.shuffle:
            rest = [i for i in range(n) if i != first]
            self.rng.shuffle(rest)
            self.order, self.at = ([] if first is None else [first]) + rest, 0
        else:
            self.order, self.at = list(range(n)), first or 0

    def set_shuffle(self, on):
        self.shuffle = bool(on)
        if self.queue:
            self.reorder(self.order[self.at] if self.at < len(self.order) else None)
            nxt = self.peek()
            if nxt and self.state == "playing":
                self.fetch(nxt)

    def peek(self):
        return self.queue[self.order[self.at + 1]] if self.at + 1 < len(self.order) else None

    # ---------------------------------------------------------------- one track

    def start(self):
        t = self.current()
        if t is None:
            return self.stop()
        self.quiet()
        self.state, self.error = "loading", ""
        self.fetch(t)

    def fetch(self, t):
        fid = t["rom_file_id"]
        if fid in self.fetching:
            return                               # on its way: landed() starts it if it's still the one
        if len(self.fetching) >= self.FETCHES:
            return                               # enough on their way: tick() asks again for the one that's wanted
        self.fetching.add(fid)
        api, keep = self.app.api, (self.path,) if self.path else ()
        self.app.bg(lambda: rommlib.fetch_music(api, t, keep),
                    lambda path: self.landed(fid, path), lambda e: self.failed(fid, e))

    def waiting_for(self, fid):
        t = self.current()
        return self.state == "loading" and t is not None and t["rom_file_id"] == fid

    def landed(self, fid, path):
        self.fetching.discard(fid)
        if self.waiting_for(fid):
            self.begin(path)

    def failed(self, fid, e):
        self.fetching.discard(fid)
        if self.waiting_for(fid):
            self.skip(str(e))

    def begin(self, path):
        out, t = self.stream(), self.current()
        try:
            if out is None:
                raise pygame.error("no mixer")
            out.load(path)
            out.play()
        except pygame.error as e:
            rommlib.log("player: %s wouldn't play (%s)" % (path, e))
            return self.skip("\"%s\" couldn't be played" % track_title(t))
        self.state, self.path, self.began, self.fails = "playing", path, time.monotonic(), 0
        nxt = self.peek()
        if nxt:
            self.fetch(nxt)                      # here by the time this one ends

    def skip(self, why):
        """The current track can't be had: on to the next, unless too many failed in a row."""
        self.fails += 1
        if self.fails >= self.FAILS or self.at + 1 >= len(self.order):
            self.stop()
            self.error = why
            self.app.toast(why, theme.RED)
            return
        self.app.toast(why, theme.AMBER)
        self.at += 1
        self.start()

    # ---------------------------------------------------------------- controls

    def toggle(self):
        """Pause, or play on: a paused track from where it was, a stopped queue from its track."""
        out = self.stream()
        try:
            if self.state == "playing" and out:
                out.pause()
                self.state = "paused"
            elif self.state == "paused" and out:
                out.unpause()
                self.state, self.began = "playing", time.monotonic()
            elif self.state == "stopped" and self.queue:
                self.fails = 0
                self.start()
        except pygame.error:
            self.stop()

    def next(self, auto=False):
        """The next track; past the last one the queue stops (auto: the track ended by itself)."""
        if self.at + 1 < len(self.order):
            self.at += 1
            self.start()
        elif auto:
            self.stop()
        else:
            self.app.toast("That's the last track")

    def prev(self):
        if self.at > 0 and not (self.state == "playing" and self.position() > self.RESTART):
            self.at -= 1
        if self.queue:
            self.start()

    def position(self):
        """Seconds into the track."""
        out = self.stream()
        if out is None or self.state not in ("playing", "paused"):
            return 0.0
        try:
            return max(0, out.get_pos()) / 1000.0
        except pygame.error:
            return 0.0

    def quiet(self):
        out = self.stream()
        if out is not None:
            try:
                out.stop()
                out.unload()
            except (pygame.error, AttributeError):
                pass
        self.path = None

    def stop(self):
        self.quiet()
        self.state = "stopped"

    def tick(self):
        """Every frame: a track that has played out makes way for the next, and the one that's
        waited for is asked for once there's room."""
        if self.state == "loading":
            t = self.current()
            if t is not None and t["rom_file_id"] not in self.fetching:
                self.fetch(t)
            return
        if self.state != "playing" or time.monotonic() - self.began < self.SETTLE:
            return
        out = self.stream()
        try:
            busy = out is not None and out.get_busy()
        except pygame.error:
            busy = False
        if not busy:
            self.next(auto=True)
