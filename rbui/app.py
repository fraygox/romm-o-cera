"""The App (screen stack, modal, toasts, the main loop, background work) and the cover loader."""
import os
import threading
import time
import zlib

import pygame
import rommlib
from rommlib import human_size
from rbui import APP_NAME, draw, pads, theme
from rbui.theme import apply_theme, FPS, H, ink_on, S, set_custom, W
from rbui.draw import (as_rgba, button_glyph, circle, COVER_DECODES, COVER_ENTRIES, cover_fit, Fonts, glyph_width,
                       hline, marquee, measure, rich_width, rom_title, rrect, shot_fit, surface_bytes, text)
from rbui.pads import FACE, style_from_names, theme_for, TOKEN_ACTION, TOKEN_BACK
from rbui.sfx import Sfx, SOUND_FOR
from rbui.input import Input
from rbui.screens.base import ListScreen
from rbui.screens.home import HomeScreen
from rbui.screens.downloads import DownloadsScreen
from rbui.screens.whatsnew import notes_at_start, WhatsNewScreen
from rbui.screens.welcome import WelcomeScreen, welcome_due, welcome_on_rejected
from rbui.modals import ActionMenu, Confirm
from rbui.startup import read_marker, write_marker


# ------------------------------------------------------------------- app

COVER_RETRY = (5, 15, 60)       # seconds before a cover RomM couldn't send is asked for again


class CoverLoader:
    """Covers come in on up to WORKERS threads, the newest request first, so
    the screen you're looking at fills before rows you scrolled past. A request
    for a cover nobody has drawn for STALE seconds is dropped when its turn
    comes; drawing that game again asks again."""
    WORKERS, STALE = 12, 1.5
    instances = []              # every loader made (tests settle them between tests)

    def __init__(self, app):
        CoverLoader.instances.append(self)
        self.app = app
        self.cv = threading.Condition()
        self.todo = []              # (rid, load, done, failed, dropped), newest last
        self.seen = {}              # rid -> when cover() last asked for it (drawn)
        self.busy = self.threads = 0

    def want(self, rid, load, done, failed, dropped, wanted):
        """wanted(): False once the app no longer waits for this request (the
        cover was evicted or the covers reset): it's then skipped silently."""
        with self.cv:
            self.todo.append((rid, load, done, failed, dropped, wanted))
            if self.threads < min(self.WORKERS, self.busy + len(self.todo)):
                self.threads += 1
                threading.Thread(target=self._work, daemon=True).start()
            self.cv.notify()

    def drawn(self, rid):
        now = self.seen[rid] = time.monotonic()
        if len(self.seen) > 2000:
            self.seen = {r: t for r, t in self.seen.items() if now - t < self.STALE}

    def settle(self, timeout=5):
        """Drop what's queued and wait for the requests already running."""
        end = time.monotonic() + timeout
        with self.cv:
            self.todo.clear()
            while self.busy and time.monotonic() < end:
                self.cv.wait(0.05)

    def pending(self):
        """Requests not finished yet (the screenshot harness waits for 0)."""
        with self.cv:
            return self.busy + len(self.todo)

    def run_one(self, wait=True):
        """Take the newest request and run it on this thread; False if there was none."""
        with self.cv:
            while not self.todo:
                if not wait:
                    return False
                self.cv.wait()
            rid, load, done, failed, dropped, wanted = self.todo.pop()
            self.busy += 1
        try:
            if not wanted():
                pass
            elif time.monotonic() - self.seen.get(rid, 0) > self.STALE:
                self.app.post(dropped)
            else:
                try:
                    res = load()
                except Exception as e:
                    if rid not in self.app.cover_retry:      # once, not every minute it stays down
                        rommlib.log("cover %s: %s" % (rid, e if isinstance(e, rommlib.RommError)
                                                      else e.__class__.__name__))
                    self.app.post(failed)
                else:
                    self.app.post(lambda: done(res))
        finally:
            with self.cv:
                self.busy -= 1
                self.cv.notify_all()
        return True

    def _work(self):
        while True:
            self.run_one()


def kept_note(paths):
    """After a delete: a game folder that stays because it holds the owner's own files."""
    kept = [p for p in paths or [] if os.path.isdir(p)]
    if not kept:
        return ""
    return ". Its folder %s stays: it holds your own files" % os.path.relpath(kept[0], os.path.dirname(
        rommlib.ROMS_ROOT))


class Rest:
    """When the main loop may slow down: nothing came in (input, a background result) and the
    frames drawn have been the same for a second. The frame itself is the judge, so anything
    that moves (a scrolling title, a progress bar, a toast, a cursor) keeps the full rate
    without having to say so; a still screen costs FPS frames a second instead of 60."""
    FPS = 8                 # frames a second while resting (input still wakes it at once)
    SAMPLE, STILL = 0.25, 4   # a frame is looked at this often at the full rate; this many alike in a row rest

    def __init__(self, on=True):
        self.on, self.resting = on, False
        self.seen, self.alike, self.next = None, 0, 0.0

    def frame(self, canvas, busy):
        """After a frame is drawn. busy: something came in for it."""
        if not self.on:
            return
        now = time.monotonic()
        if busy:
            self.resting, self.alike, self.seen = False, 0, None
            self.next = now + self.SAMPLE
            return
        if not self.resting and now < self.next:
            return
        self.next = now + self.SAMPLE
        try:
            mark = zlib.adler32(canvas.get_buffer())
        except Exception:               # a surface that can't be read: never rest
            self.on = self.resting = False
            return
        if mark == self.seen:
            self.alike += 1
            self.resting = self.alike >= self.STILL
        else:
            self.seen, self.alike, self.resting = mark, 0, False


def plural_games(jobs):
    """"1 game" / "3 games": what the closing wait names (a count, never a name that could overflow)."""
    return "1 game" if len(jobs) == 1 else "%d games" % len(jobs)


class App:
    # systems whose converter needs a downloaded tool: a finished download there may fetch it
    TOOL_SYSTEMS = frozenset(s for c in rommlib.CONVERTERS if c.tool for s in c.systems)

    def __init__(self, screen, headless=False):
        self.screen = screen
        if screen is not None:
            sw, sh = screen.get_size()
            theme.K = min(sw / W, sh / H)
        else:
            theme.K = float(os.environ.get("ROMM_BROWSER_SCALE", "1"))
        self.fps = FPS if theme.K <= 1.5 else 30   # 4K software blits: 30 fps is plenty for menus
        self.canvas = pygame.Surface((S(W), S(H)))
        self.scaled_covers = {}
        self.details, self.detail_busy = {}, False     # RomM's full record per rom id (files, archive members)
        self.here_count = {}                           # {platform id: its games on the box}, refreshed by Platforms
        self.fonts = Fonts()
        draw.FONTS["f"] = self.fonts
        self.cfg = rommlib.load_config()
        self.targets, self.modal_from, self.ptr = [], 0, None
        self.last_tap = (0.0, None, (0, 0))     # the last tap that acted: when, on what, where
        self.tapped, self.ptr_later = False, []    # a tap acted since the last frame; events held back
        self.sfx = Sfx(self.cfg["sounds"] if screen is not None else "off")
        self.input = Input(self.cfg["swap_ab"])
        self.log_pads()
        self.make_custom_theme()
        apply_theme(self.theme_key())
        self.headless = headless
        self.api = rommlib.Api.from_config(self.cfg)
        self.api.cache_limit = rommlib.cache_limit_bytes(self.cfg)
        self.loaded = False
        self.fetched = False          # the platform list was asked for once (a 401 then opens Welcome)
        self.junk = None              # filename tags hidden by "Hide prototypes, demos & hacks"
        self.player_counts = None     # RomM's player count values (the Players filter), asked for on first use
        self.recs = None              # Recommended for you: [(rom, why, seed name)], [] when RomM has none (Home asks once)
        self.conn_gen = 0             # bumped on every reconnect: per-server data from before it is dropped
        self.music_total = None       # tracks in RomM's jukebox (Home shows Music when there are any); None: not asked yet
        # menu music: the sync in the background and what the last one did (the Music screen shows it)
        self.music = {"busy": False, "done": 0, "total": 0, "again": False, "result": rommlib.music_status()["last"]}
        self.music_stop = threading.Event()
        self.update_tag = None
        self.library = rommlib.Library()
        self.downloader = rommlib.Downloader(self.api, self.library, self.cfg)
        self.platforms = []          # mapped platforms only
        self.platform_by_id = {}
        self.unmapped = []
        self.stack = []
        self.toasts = []
        self.modal = None
        self.events_from_threads = []
        self.lock = threading.Lock()
        self.covers = {}             # rom_id -> Surface | "loading" | None
        self.cover_bytes = 0         # what the covers' Surfaces take (trim_covers keeps it in budget)
        self.cover_order = []
        self.cover_req = {}          # rom_id -> the request the app waits for (stale ones are ignored)
        self.cover_retry = {}        # rom_id -> (monotonic time to ask again, failures): RomM was busy
        self.cover_loader = CoverLoader(self)
        self.running = True
        self.status = "Connecting…"
        self.error = None
        self.romm_version = None     # RomM's own version once Online ("5.4.0")
        self.t = 0.0
        self._chd_spawned = 0

    # ---- threading helpers
    def bg(self, fn, done=None, err=None):
        def run():
            try:
                res = fn()
                if done:
                    self.post(lambda: done(res))
            except Exception as e:
                if not isinstance(e, (rommlib.RommError, rommlib.UpdateError)):
                    # foreign exception text can carry request details: type only
                    e = rommlib.RommError("Error (%s)" % e.__class__.__name__)
                rommlib.log("bg error: %s" % e)
                # bind e now: Python 3 deletes `e` when the except block ends
                if err:
                    self.post(lambda e=e: err(e))
                else:
                    self.post(lambda e=e: self.toast(str(e), theme.RED))
        threading.Thread(target=run, daemon=True).start()

    def post(self, fn):
        with self.lock:
            self.events_from_threads.append(fn)

    def toast(self, msg, color=None, tap=None):
        """A message at the bottom for a few seconds; tap: what tapping it does."""
        color = theme.TEXT if color is None else color
        now = time.time()
        last = self.toasts[-1] if self.toasts else None
        if last and last[0] == msg and last[1] == color and last[2] > now:
            burst = last[2] - 3.5 > now - 0.5    # e.g. every load of a screen failing at once: one sound
            last[2] = now + 3.5                  # the same message again: one toast, its time renewed, ×N
            last[3] += 1
            if burst:
                return
        else:
            # one too long for its pill slides to show its end (draw_toasts), so it stays up that much longer
            over = rich_width(self.fonts, self.fonts.body, msg) + 52 - self.TOAST_W
            extra = draw.MARQUEE_WAIT + over / draw.MARQUEE_SPEED if over > 0 else 0
            self.toasts.append([msg, color, now + (6 if tap else 3.5) + extra, 1, tap])
        if color == theme.RED:
            self.sfx.play("deny")
        elif msg.startswith("Ready"):
            self.sfx.play("done")
        elif msg.startswith("Queued"):
            self.sfx.play("queued")

    # ---- data
    def load_platforms(self):
        gen = self.conn_gen

        def done(plats):
            if gen != self.conn_gen:
                return                        # the previous server's answer
            mp = dict(rommlib.PLATFORM_MAP)
            mp.update(self.cfg["platform_map"] or {})
            self.platforms, self.unmapped = [], []
            for p in plats:
                system = mp.get(p["fs_slug"])
                p["_system"] = system
                if not p.get("rom_count"):
                    continue
                (self.platforms if system else self.unmapped).append(p)
            self.platforms.sort(key=lambda p: (p.get("display_name") or p["name"]).lower())
            self.platform_by_id = {p["id"]: p for p in self.platforms}
            self.status = "Online"
            self.error = None
            self.loaded = True
            def about():
                """RomM's version, and how many tracks its jukebox holds: Home offers Music when
                there are any (a RomM without a jukebox: none). Asking for that never changes
                what the header says about the connection."""
                api, was = self.api, self.api.down
                try:
                    tracks = int(api.music_stats().get("total_tracks") or 0)
                except (rommlib.RommError, TypeError, ValueError):
                    tracks, api.down = 0, was
                return api.server_version(), tracks

            def told(v):
                if gen == self.conn_gen:
                    self.romm_version, self.music_total = v
            self.bg(about, told, lambda e: None)
            self.sync_music()

        first, self.fetched = not self.fetched, True

        def err(e):
            if gen != self.conn_gen:
                return
            self.status = "Offline"
            self.error = str(e)
            if first and isinstance(e, rommlib.AuthRejected) and welcome_on_rejected(self):
                self.push(WelcomeScreen(self, step=1 if self.cfg["url"] else 0, rejected=True))
        self.bg(self.api.platforms, done, err)

    def status_label(self):
        """The connection as the header and Settings say it: "Online · RomM 5.4.0"."""
        if self.status != "Online":
            return self.status
        # connected at the start, but its last answer since wasn't one: the header never says
        # Online over a screen that says it can't reach RomM
        return getattr(self.api, "down", None) or rommlib.online_label(self.romm_version)

    def system_name(self, system):
        """What a system folder is called on screen: RomM's name for its platform, as
        Platforms shows it, else ES's ("psx" -> "PlayStation")."""
        p = next((p for p in self.platforms if p.get("_system") == system), None)
        return p["name"] if p and p.get("name") else rommlib.system_name(system)

    def system_for(self, rom):
        p = self.platform_by_id.get(rom.get("platform_id"))
        return p["_system"] if p else None

    def free_after_queue(self):
        """Free space once the queue already waiting has landed (None if unknown)."""
        try:
            st = os.statvfs(rommlib.ROMS_ROOT)
        except OSError:
            return None
        return st.f_bavail * st.f_frsize - rommlib.queue_forecast(
            self.downloader.jobs, 0, 0, rommlib.chd_enabled(self.cfg), self.cfg)["need"]

    def plan_text(self, rom, system):
        """One game's download in a sentence: size, unpacked size, conversion,
        where it goes and the space left afterwards."""
        size = rom.get("fs_size_bytes") or 0
        lay = self.layout(rom)
        parts, lands = ["%s to download" % human_size(size)], size
        if lay and lay["unpacked"]:
            lands = lay["unpacked"]
            parts.append("%s%s once unpacked" % ("up to " if lay.get("upto") else "about " if lay["estimated"] else "",
                                                 human_size(lands)))
        elif (rom.get("fs_extension") or "").lower() in ("zip", "7z", "rar") and system in rommlib.EXPANDS:
            inner = rommlib.unpacked_size(rom, system)
            lands = rommlib.space_needed(rom, system) if inner is None else inner
            word = "up to " if rommlib.unpacked_upper_bound(rom, system) else "" if inner is not None else "about "
            parts.append("%s%s once unpacked" % (word, human_size(lands)))
        if lay and lay["convert"] == rommlib.CONVERTER["ps3"].label:
            parts.append("then made into a .ps3 game folder in the background (read from the zip)")
        elif lay and lay["convert"]:
            parts.append("then compressed to %s in the background" % lay["convert"])
        out = ", ".join(parts) + ". Saved to roms/%s" % system
        after, keep = self.free_after_queue(), rommlib.keep_free_bytes(self.cfg)
        if after is not None:
            after -= lands
            out += ("; %s free afterwards" % human_size(after) if after >= keep else
                    "; it waits for space (keeping %s free)" % human_size(keep))
        return out + "."

    def detail(self, rom):
        """RomM's full record for rom ({} if it couldn't be fetched), or None
        while it's being fetched. One fetch at a time: scrolling past a list
        fetches only where the selection stops."""
        rid = rom["id"]
        if rid in self.details or self.detail_busy:
            return self.details.get(rid)
        self.detail_busy = True

        def keep(d):
            self.detail_busy = False
            if len(self.details) > 400:
                self.details.clear()
            self.details[rid] = d or {}
        self.bg(lambda: self.api.rom(rid), keep, lambda e: keep({}))
        return None

    def layout(self, rom):
        """rommlib.disc_layout for rom once its record is here, else None."""
        system = self.system_for(rom)
        d = self.detail(rom)
        if not (d and system):
            return None
        return rommlib.disc_layout(d, system, self.cfg, self.downloader._accepts)

    def cover(self, rom):
        large = theme.K > 1.5
        return self.art(rom["id"], lambda: self.api.fetch_cover(rom, large=large), cover_fit)

    def screenshot(self, rom, i):
        """rom's i-th screenshot (Api.fetch_screenshot) in the covers' store and budget, under its
        own key: the Surface, or None while it loads (or when there's none: art_missing)."""
        return self.art(("shot", rom["id"], i), lambda: self.api.fetch_screenshot(rom, i), shot_fit)

    def collection_cover(self, coll, i):
        """The i-th cover of a collection's record (Api.fetch_collection_cover) in the covers'
        store, under ("coll", param, id, i): the Surface, or None while it loads (or none)."""
        key = ("coll", coll.get("param"), coll["id"], i)
        return self.art(key, lambda: self.api.fetch_collection_cover(coll, i), cover_fit)

    def art_missing(self, key):
        """True when RomM had no image for a key of art() (not merely still loading)."""
        return key in self.covers and self.covers[key] is None

    def art(self, rid, fetch, fit):
        """An image from RomM's assets by key (a rom id: its cover; ("shot", id, i)...): fetch()
        -> a cached file path or None (blocking, on the cover loader), fit(Surface) -> what's kept.
        Kept least-recently-drawn under COVER_BUDGET with every other one. None while loading."""
        self.cover_loader.drawn(rid)
        c = self.covers.get(rid)
        if c is not None or rid in self.covers:
            if self.cover_order[-1] != rid:            # most recently drawn last: trimmed last
                self.cover_order.remove(rid)
                self.cover_order.append(rid)
            return None if c == "loading" else c
        retry = self.cover_retry.get(rid)
        if retry and time.monotonic() < retry[0]:
            return None                   # couldn't be fetched a moment ago: "…" until it's time
        self.covers[rid] = "loading"
        req = self.cover_req[rid] = object()
        self.cover_order.append(rid)
        self.trim_covers()

        def current():
            return self.cover_req.get(rid) is req

        def load():
            for _ in (1, 2):
                path = fetch()
                if not path:
                    return None
                try:
                    with COVER_DECODES:
                        return fit(as_rgba(pygame.image.load(path)))
                except (pygame.error, OSError):
                    # trimmed from the cache between finding and reading it
                    # (or unreadable): drop it and fetch once more
                    try:
                        os.remove(path)
                    except OSError:
                        pass
            return None                     # fetched again and still not an image: no cover

        def done(img):
            if current():
                del self.cover_req[rid]
                self.covers[rid] = img
                self.cover_bytes += surface_bytes(img)
                self.cover_retry.pop(rid, None)
                self.trim_covers(keep=rid)

        def failed():                       # RomM busy or unreachable: ask again after a wait
            if current():
                del self.cover_req[rid]
                del self.covers[rid]
                self.cover_order.remove(rid)
                n = self.cover_retry.get(rid, (0, 0))[1] + 1
                now = time.monotonic()
                self.cover_retry[rid] = (now + COVER_RETRY[min(n, len(COVER_RETRY)) - 1], n)
                if len(self.cover_retry) > 2000:
                    self.cover_retry = {r: v for r, v in self.cover_retry.items() if v[0] > now - 600}

        def dropped():                  # scrolled away before its turn: asked again when drawn
            if current():
                del self.cover_req[rid]
                del self.covers[rid]
                self.cover_order.remove(rid)
        self.cover_loader.want(rid, load, done, failed, dropped, current)
        return None

    def trim_covers(self, keep=None):
        """Least recently drawn covers out of memory past COVER_BUDGET (or
        COVER_ENTRIES; it can run over by the cover just loaded when every
        other one was drawn more recently); a load still on its way for one
        is ignored. They come back from the disk cache when drawn again."""
        while self.cover_order and (self.cover_bytes > draw.COVER_BUDGET or len(self.cover_order) > COVER_ENTRIES):
            old = self.cover_order[0]
            if old == keep:
                break
            self.cover_order.pop(0)
            self.cover_bytes -= surface_bytes(self.covers.pop(old, None))
            self.cover_req.pop(old, None)
            self.scaled_covers.pop(old, None)

    def forget_covers(self):
        """Drop every cover in memory (new server, cache cleared); loads on their way are ignored."""
        self.cover_bytes = 0
        self.covers.clear()
        self.cover_order.clear()
        self.cover_req.clear()
        self.cover_retry.clear()
        self.scaled_covers.clear()

    def log_pads(self):
        """What controllers are connected and how they're read, for a report about buttons."""
        lines = self.input.describe()
        if lines != getattr(self, "pads_logged", None):      # both "added" events fire for one pad
            self.pads_logged = lines
            rommlib.log("pads: Batocera has mappings for %d" % self.input.es_mappings)
            for line in lines:
                rommlib.log(line)

    def make_custom_theme(self):
        """The Custom theme from config.json's four theme_custom_* settings."""
        c = self.cfg
        set_custom(c["theme_custom_accent"], c["theme_custom_tint"], c["theme_custom_strength"], c["theme_custom_mode"])

    def theme_key(self):
        return theme_for(self.cfg["theme"], self.cfg["theme_auto"], self.cfg["button_style"], self.input.names())

    def resolve_style(self):
        want = self.cfg["button_style"] or "auto"
        pads.PAD_STYLE["style"] = want if want in FACE else style_from_names(self.input.names())
        return pads.PAD_STYLE["style"]

    def apply_config(self, reconnect=False):
        """Called by Settings after a change: persist, re-wire live objects."""
        try:
            rommlib.save_config(self.cfg)
        except rommlib.ConfigUnreadable:
            self.toast("config.json can't be read: this change is not saved (Settings, Reset settings)", theme.AMBER)
        except OSError as e:                     # a full disk: the file stays as it was
            rommlib.log("config: not saved: %r" % e)
            self.toast("Settings couldn't be saved (%s): this change lasts until the app closes"
                       % ("the disk is full" if getattr(e, "errno", None) == 28 else e.strerror or e), theme.AMBER)
        self.input.set_swap(self.cfg["swap_ab"])
        self.make_custom_theme()
        apply_theme(self.theme_key())
        self.resolve_style()
        if reconnect:
            self.conn_gen += 1
            self.api = rommlib.Api.from_config(self.cfg)
            self.api.cache_limit = rommlib.cache_limit_bytes(self.cfg)
            self.downloader.api = self.api
            self.forget_covers()
            self.details.clear()
            self.player_counts = self.recs = self.music_total = None
            self.platforms, self.platform_by_id, self.loaded = [], {}, False
            self.status, self.error, self.romm_version = "Connecting…", None, None
            self.load_platforms()

    def _uninstall_failed(self, then):
        """A delete that couldn't finish (a busy file, a conversion, installed.json):
        say why, and when some of it went, the space and the list still follow."""
        def err(e):
            self.toast(str(e), theme.AMBER if getattr(e, "paths", None) else theme.RED)
            if getattr(e, "freed", 0):
                self.space_freed()
            if then:
                then()
        return err

    def uninstall(self, rom, system, then=None):
        def done(res):
            paths, freed = res
            self.toast("Deleted %s — %s freed%s" % (rom.get("name"), human_size(freed), kept_note(paths)),
                       theme.GREEN)
            self.space_freed()
            if then:
                then()
        self.bg(lambda: self.library.uninstall(rom, system), done, self._uninstall_failed(then))

    def delete_theirs(self, rom, system, files, then=None, pins=None):
        """A game put on the box another way: exactly the files its confirm listed."""
        def done(res):
            paths, freed = res
            self.toast("Deleted %s — %s freed" % (rom.get("name"), human_size(freed)), theme.GREEN)
            self.space_freed()
            if then:
                then()
        self.bg(lambda: self.library.delete_theirs(rom, system, files, pins), done, self._uninstall_failed(then))

    def uninstall_group(self, group, system, title, then=None):
        """Every disc of a set and its playlist; like uninstall(), conversions
        waiting for room hear about the space."""
        def done(res):
            self.toast("Deleted %s — %s freed%s" % (title, human_size(res[1]), kept_note(res[0])), theme.GREEN)
            self.space_freed()
            if then:
                then()
        self.bg(lambda: self.library.uninstall_group(group, system), done, self._uninstall_failed(then))

    def start_download(self, rom, replace=None, group=None, pins=None):
        """group: the disc set this game belongs to, kept when one disc is fetched again.
        pins: what the files of replace were when its confirm opened (rommlib.pin_files)."""
        system = self.system_for(rom)
        if not system:
            self.toast("No Batocera system for this platform", theme.RED)
            return
        self.downloader.enqueue(rom, system, replace=replace, pins=pins, **({'group': group} if group else {}))
        self.toast("Queued: %s" % rom_title(rom) + self.hours_note(), theme.GREEN)
        self.space_check()

    # ---- Play now: quit, and a waiter has EmulationStation start the game
    def play_entry(self, rom, system=None, title=None):
        """The "Play now" row for a game's menu: (label, grey line, action),
        the action None (and the grey line saying why) when it can't start now."""
        system = system or self.system_for(rom)
        title = title or rom_title(rom)
        why, path = None, None
        j = self.downloader.job_for(rom["id"])
        if getattr(self, "updating", False):
            why = "Wait for the update to finish"
        elif not system:
            why = "No Batocera system for this platform"
        elif j and j.active:
            why = "It's still downloading"
        else:
            path, why = rommlib.play_target(self.library, rom, system, getattr(self.downloader, "es", None))
        if why:
            return ("Play now", why, None)
        return ("Play now", "%s closes and the game starts" % APP_NAME, lambda: self.play_now(path, system, title))

    def play_now(self, path, system, title, bios_asked=False):
        """Say what happens, ask first when the system has none of its BIOS files or downloads
        are still running, then quit."""
        lack = [] if bios_asked else rommlib.bios_all_missing(system)
        if lack:
            names = ", ".join(os.path.basename(f) for f in lack[:3]) + ("…" if len(lack) > 3 else "")
            self.modal = Confirm(self, "%s may not start" % title,
                                 "%s needs a BIOS file and none is on this box (%s), so the emulator will "
                                 "probably close again. On this box › BIOS files lists them and gets the "
                                 "ones RomM has." % (self.system_name(system), names),
                                 "Play anyway", lambda: self.play_now(path, system, title, bios_asked=True))
            return
        d = self.downloader
        n = 0 if d.hold else d.active_count()
        if not n:
            return self._play(path, system, title)
        what = "%d download%s" % (n, "s" if n > 1 else "")
        if self.cfg["background_downloads"]:
            body = "%s closes and %s starts. %s still running keep%s going in the background while you play." % (
                APP_NAME, title, what, "s" if n == 1 else "")
        else:
            body = "%s closes and %s starts. %s still running %s paused and resume next time you open %s." % (
                APP_NAME, title, what, "is" if n == 1 else "are", APP_NAME)
        self.modal = Confirm(self, "Play %s now?" % title, body, "Play now", lambda: self._play(path, system, title))

    def _play(self, path, system, title):
        python = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else None
        if not rommlib.launch_when_closed(path, system=system, name=title, python=python):
            self.toast("Couldn't start %s: open it from EmulationStation" % title, theme.RED)
            return
        self.play_after = path
        rommlib.es_notify("Starting %s…" % title)
        rommlib.log("PLAY NOW %s" % path)
        self._quit()

    def space_check(self):
        """After queueing: warn when the whole queue won't fit (it waits for room)."""
        fc = rommlib.queue_forecast(self.downloader.jobs, rommlib.free_bytes(),
                                    rommlib.keep_free_bytes(self.cfg), rommlib.chd_enabled(self.cfg), self.cfg)
        if fc["jobs"] and not fc["fits"]:
            self.toast("The queue needs %s more space than this box has free (keeping %s free). "
                       "Downloads wait until there's room." % (human_size(fc["short"]), human_size(fc["keep"])),
                       theme.AMBER)

    def hours_note(self):
        if rommlib.in_download_hours(self.cfg):
            return ""
        return " — downloads run %s" % rommlib.hours_label(self.cfg["download_hours"])

    def rom_status(self, rom):
        """("active"|"failed"|"installed"|"remote", job or None). A finished
        download counts as installed only while its files are still there; a game
        on the box stays installed when a later download of it failed (Download
        again refused: what's there still plays, and can be deleted)."""
        j = self.downloader.job_for(rom["id"])
        if j and j.active:
            return "active", j
        system = self.system_for(rom)
        if system and self.library.is_installed(rom, system):
            return "installed", j if j and j.state == rommlib.Job.DONE else None
        if j and j.state == rommlib.Job.FAILED:
            return "failed", j
        return "remote", None

    def set_title(self, rom):
        name = rom.get("name") or rom.get("fs_name_no_ext") or ""
        d = rommlib.disc_info(name)
        return d[0] if d else name

    def make_group(self, rom, discs):
        plat = self.platform_by_id.get(rom.get("platform_id")) or {}
        return rommlib.disc_group(plat.get("fs_slug") or str(rom.get("platform_id")), discs,
                                  self.set_title(rom))

    def queue_set(self, group, items, system):
        """items: [(rom, system)] of one disc set. Discs already on the box
        join the set instead of downloading again. Returns discs queued."""
        lib, dl = self.library, self.downloader
        queued, unrecorded = 0, None
        for r, _ in items:
            j = dl.job_for(r["id"])
            if j and j.active:
                j.group = group
                continue
            try:
                if lib.is_installed(r, system) and lib.adopt(r, system, group):
                    continue
            except rommlib.RommError as e:       # installed.json can't be written now: it's here all the same
                rommlib.log("adopt %s: %s" % (r.get("id"), e))
                unrecorded = e
                continue
            dl.enqueue(r, system, group)
            queued += 1
        if unrecorded is not None:
            self.toast(str(unrecorded), theme.AMBER)
        elif not queued:                     # everything already here: just make the playlist
            self.bg(lambda: dl._finish_group(group, system),
                    lambda m3u: self.toast("Playlist made: %s" % os.path.basename(m3u) if m3u
                                           else "All discs are already on this box", theme.GREEN))
        dl.save_queue()
        return queued

    def download_set(self, rom, discs):
        system = self.system_for(rom)
        group = self.make_group(rom, discs)
        n = self.queue_set(group, [(r, system) for _, r in discs], system)
        if n:
            self.toast("Queued %d disc%s: %s" % (n, "" if n == 1 else "s", group["title"]), theme.GREEN)
            self.space_check()

    def expand_sets(self, items):
        """Bulk downloads: give every disc its whole set (found in RomM) and
        a shared group. Runs in a worker thread. Returns [(group|None, [(rom, system)])]."""
        out, seen = [], {}
        for r, sy in items:
            d = rommlib.disc_info(r.get("fs_name_no_ext") or "")
            if not d:
                out.append((None, [(r, sy)]))
                continue
            key = (r.get("platform_id"), d[0].lower())
            if key in seen:
                continue
            discs, _ = rommlib.find_disc_set(self.api, r)
            if not discs or len(discs) < 2:
                out.append((None, [(r, sy)]))
                continue
            seen[key] = True
            out.append((self.make_group(r, discs), [(x, sy) for _, x in discs]))
        return out

    def queue_many(self, roms, title, after=None):
        """Queue many games (a list, a collection, picked games) with the space
        check, the keep-free reserve and whole multi-disc sets."""
        app = self
        def busy(r):
            j = app.downloader.job_for(r["id"])
            return bool(j and j.active)
        roms = [r for r in roms if app.system_for(r) and not app.library.is_installed(r, app.system_for(r))
                and not busy(r)]
        if not roms:
            app.toast("Nothing to download — all on this box already", theme.AMBER)
            return
        size = sum(r.get("fs_size_bytes") or 0 for r in roms)
        items = [(r, app.system_for(r)) for r in roms]
        need = rommlib.space_for(items)          # the queue's real peak, as Downloads forecasts it
        keep = rommlib.keep_free_bytes(app.cfg)
        free = app.free_after_queue()

        def go(chosen):
            if after:
                after()
            if not any(rommlib.disc_info(r.get("fs_name_no_ext") or "") for r, _ in chosen):
                for r, sy in chosen:
                    app.downloader.enqueue(r, sy)
                app.toast("Queued %d games" % len(chosen) + app.hours_note(), theme.GREEN)
                app.space_check()
                return
            app.toast("Finding the other discs of multi-disc games…")

            def queued(groups):
                n = 0
                for group, members in groups:
                    if group:
                        n += app.queue_set(group, members, members[0][1])
                    else:
                        for r, sy in members:
                            app.downloader.enqueue(r, sy)
                            n += 1
                sets = sum(1 for g, _ in groups if g)
                app.toast("Queued %d download%s%s" % (n, "" if n == 1 else "s",
                          (", %d multi-disc set%s kept together" % (sets, "" if sets == 1 else "s"))
                          if sets else "") + app.hours_note(), theme.GREEN)
                app.space_check()
            app.bg(lambda: app.expand_sets(chosen), queued)

        reserve = keep or rommlib.space_margin(need)        # the downloader's own margin when keep-free is 0
        if free is not None and need + reserve > free:
            fit, used = rommlib.fit_in_space(items, free, reserve)
            entries = []
            if fit:
                entries.append(("Download the %d that fit" % len(fit),
                                "About %s once extracted" % human_size(used), lambda: go(fit)))
            entries.append(("Download all %d anyway" % len(items),
                            "Needs about %s; %s free after the queue, keeping %s free. Later ones wait "
                            "for space." % (human_size(need), human_size(max(0, free)), human_size(keep)),
                            lambda: go(items)))
            entries.append(("Cancel", "", lambda: None))
            app.modal = ActionMenu(app, "Not enough space for all %d" % len(items), entries)
            return
        body = "%d games, %s to download (about %s once extracted)%s." % (
            len(roms), human_size(size), human_size(need),
            "" if free is None else ". %s free on this box" % human_size(max(0, free)))
        app.modal = Confirm(app, title, body, "Download %d" % len(roms), lambda: go(items))

    def space_freed(self):
        """Space was just freed: conversions waiting for room shouldn't wait for
        the next download to be noticed. The worker is flock'd (no-op if running)."""
        if self.headless:
            return
        if rommlib.conversions_pending():
            self.spawn_helper("rb_chd.py")

    def spawn_helper(self, script):
        rommlib.spawn_detached(script, "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else None)

    # ---- navigation
    def push(self, screen):
        self.stack.append(screen)
        screen.enter()

    def pop(self):
        if len(self.stack) > 1:
            self.stack.pop()
            self.stack[-1].enter()
            self.show_notes()

    notes_pending = None             # (release, previous version): What's new, waiting for Home

    def _notes_found(self, res):
        self.notes_pending = res
        self.show_notes()

    def show_notes(self):
        """What's new over Home, when it's due and nothing else is open (else on the way back)."""
        if self.notes_pending and len(self.stack) == 1 and self.modal is None:
            rel, prev = self.notes_pending
            self.notes_pending = None
            if rommlib.notes_due(self.cfg):           # not read meanwhile (Settings, What's new)
                self.push(WhatsNewScreen(self, rel, prev))

    def request_quit(self):
        if getattr(self, "updating", False):
            self.toast("Finishing the update first: %s closes by itself in a moment" % APP_NAME)
            return
        if getattr(self.modal, "is_quit", False):
            return self._quit()                     # asked twice: quit
        n = self.downloader.active_count()
        if n and not self.cfg["background_downloads"]:
            self.modal = Confirm(self, "Quit %s?" % APP_NAME,
                                 "%d download%s still running. They're paused and resume "
                                 "next time you open RomM-o-Cera (or turn on \"Keep "
                                 "downloading after exit\" in Settings)." % (n, "s" if n > 1 else ""),
                                 "Quit", self._quit, danger=True)
            self.modal.is_quit = True
        else:
            self._quit()

    def _quit(self):
        self.running = False

    # ---- main loop
    def _update_found(self, tag):
        if not tag:
            return
        self.update_tag = tag
        if read_marker(".update-notified") == tag:
            return
        self.toast("%s %s is out. Settings shows how to update." % (APP_NAME, tag), theme.BLUE)
        write_marker(".update-notified", tag)

    def on_pad_change(self):
        if (self.cfg["button_style"] or "auto") == "auto":
            self.resolve_style()
        if self.cfg["theme_auto"]:
            apply_theme(self.theme_key())

    def take_over_downloads(self):
        """A background rb_dl.py may still be working: ask it to hand over,
        then resume whatever is queued (here or left from last time)."""
        self.bg(self.downloader.repair_groups, lambda n: None, lambda e: None)
        try:
            import rb_dl
            # no arguments: the same call works with an rb_dl.py of any version. A
            # worker stops within seconds (an unpack under way rolls back, see
            # rommlib.MOVE_WAIT); only a game being moved into place is waited for.
            was_running = rb_dl.handover()
        except Exception as e:
            rommlib.log("handover: %r" % e)
            was_running = False
        n = self.downloader.resume()
        if not self.headless and rommlib.conversions_pending():
            self.spawn_helper("rb_chd.py")        # discs left waiting last time (flock'd: no-op if running)
        if n and self.downloader.hold:
            self.toast("%d download%s waiting — paused (Activity, {X} Actions resumes)" % (n, "" if n == 1 else "s"), theme.AMBER)
        elif n:
            self.toast("Resumed %d download%s%s" % (n, "" if n == 1 else "s",
                                                    " from the background" if was_running else ""), theme.BLUE)

    def _notice(self, title, sub):
        """One plain frame with two centred lines, for a wait outside the main loop."""
        c = self.canvas
        c.fill(theme.BG)
        cx, cy = c.get_width() // 2, c.get_height() // 2
        text(c, self.fonts.body, title, (cx, cy - S(18)), theme.TEXT, "center")
        text(c, self.fonts.small, sub, (cx, cy + S(22)), theme.DIM, "center")
        if self.screen is not None:
            sw, sh = self.screen.get_size()
            self.screen.fill(theme.BG)
            self.screen.blit(c, ((sw - c.get_width()) // 2, (sh - c.get_height()) // 2))
            pygame.display.flip()

    def fetch_tools(self, systems=None):
        """Fetch, quietly in the background, a tool a turned-on setting needs when
        there's a reason to (rommlib.tools_to_fetch: an upgrade, or games here it
        would convert; systems: only those, after a download). Pinned by SHA-256
        like any tool download; one toast, and a failure is shown once per version
        (logged every time)."""
        if self.headless:
            return
        self.bg(lambda: rommlib.tools_to_fetch(self.cfg, systems),
                lambda found: [self._fetch_tool(spec, why) for spec, why in found], lambda e: None)

    def _fetch_tool(self, spec, why):
        busy = spec.setting + "_busy"              # the Settings rows that need it show the progress
        if getattr(self, busy, False):
            return
        setattr(self, busy, True)
        mark = "%s-%s" % (spec.slug, spec.pin["version"])
        told = self.__dict__.setdefault("_tools_told", set())
        if mark not in told and read_marker(".tool-fetch-failed") != mark:    # failed before: quietly now
            told.add(mark)                                                  # and once per session
            self.toast("Getting %s for %s…" % (spec.slug, spec.purpose or "compression"))
        rommlib.log("tools: fetching %s %s by itself (%s)" % (spec.slug, spec.pin["version"], why))

        def install():
            def prog(got, total):
                setattr(self, spec.setting + "_progress", (got / total) if total else None)
            try:
                return rommlib.install_tool(spec, prog)
            finally:
                setattr(self, busy, False)

        def done(paths):
            self.toast("%s %s is ready" % (spec.slug, spec.pin["version"]), theme.GREEN)
            if rommlib.conversions_pending():
                self.spawn_helper("rb_chd.py")      # the conversions queued while it was coming

        def failed(e):
            rommlib.log("tools: %s %s couldn't be fetched: %r" % (spec.slug, spec.pin["version"], e))
            if read_marker(".tool-fetch-failed") != mark:     # said once; retried quietly next time
                write_marker(".tool-fetch-failed", mark)
                self.toast("Couldn't get %s (%s); Settings can try again" % (
                    spec.slug, e if isinstance(e, rommlib.RommError) else e.__class__.__name__), theme.RED)
        self.bg(install, done, failed)

    def sync_music(self):
        """Bring the menu music folder in step with the Music screen's sources, in the
        background; asked again while one runs, it runs once more after. Off with nothing
        placed: nothing to do."""
        m = self.music
        if self.headless and not getattr(self, "music_in_tests", False):
            return
        if not self.cfg["menu_music"] and not rommlib.music_status()["tracks"]:
            return
        if m["busy"]:
            m["again"] = True
            return
        m.update(busy=True, done=0, total=0, again=False)
        cfg, sel, api, library = dict(self.cfg), rommlib.load_music_sources(), self.api, self.library

        def finished(res):
            m.update(busy=False, result=res if isinstance(res, dict) else {})
            if m["again"]:
                self.sync_music()
        self.bg(lambda: rommlib.sync_music(api, sel, library, on=cfg["menu_music"], cap=rommlib.music_cap_bytes(cfg),
                                           keep_free=rommlib.keep_free_bytes(cfg), cancel=self.music_stop,
                                           progress=lambda d, t: m.update(done=d, total=t)),
                finished, lambda e: finished({"error": str(e)}))

    def startup_checks(self):
        """Things the player must know before anything else."""
        if rommlib.config_unreadable():
            kept = sorted(n for n in os.listdir(rommlib.DATA_DIR) if n.startswith("config.json.bad-"))
            rommlib.load_config()                    # keeps a copy beside it if there isn't one
            kept = sorted(n for n in os.listdir(rommlib.DATA_DIR) if n.startswith("config.json.bad-")) or kept
            self.modal = Confirm(
                self, "Settings file can't be read",
                "config.json has an error, so defaults are in use and nothing is saved over it "
                "(copy kept: %s). Fix it over SSH and restart, or reset: you then enter the "
                "server and token again." % (kept[-1] if kept else "config.json.bad-…"),
                "Reset settings", self.reset_config, danger=True)

    def welcome(self):
        """The first-run Welcome over Home, when the box isn't set up yet (its version's notes
        then count as seen: What's new is for updates)."""
        rommlib.notes_start_here(self.cfg)
        if welcome_due(self.cfg) and not self.modal:
            self.push(WelcomeScreen(self))

    def reset_config(self):
        rommlib.reset_config()
        self.cfg.clear()
        self.cfg.update(rommlib.load_config())
        self.downloader.cfg = self.cfg
        self.apply_config(reconnect=True)
        self.toast("Settings reset to defaults; the old file is kept", theme.GREEN)

    def run(self, scripted=None):
        clock = pygame.time.Clock()
        self.startup_checks()
        self.resolve_style()
        apply_theme(self.theme_key())
        self.take_over_downloads()
        lim = rommlib.cache_limit_bytes(self.cfg)
        if lim:                                   # trim the cover cache once per start
            self.bg(lambda: rommlib.prune_cache(lim), lambda r: None, lambda e: None)
        self.load_platforms()
        if not self.headless:
            self.bg(lambda: rommlib.newer_release(self.cfg), self._update_found, lambda e: None)
        self.fetch_tools()
        self.push(HomeScreen(self))
        self.welcome()
        if not self.headless:                     # What's new, once after an update; offline: next time
            cfg = dict(self.cfg)
            self.bg(lambda: notes_at_start(cfg), self._notes_found, lambda e: None)
        rest = Rest(on=scripted is None and not self.headless)
        while self.running:
            early = []
            if rest.resting and not self.ptr_later:
                # nothing on screen is changing: a few frames a second, and at once on any input
                ev = pygame.event.wait(1000 // rest.FPS)
                early = [] if ev.type == pygame.NOEVENT else [ev]
                dt = clock.tick() / 1000.0
            else:
                dt = clock.tick(self.fps) / 1000.0
            self.t += dt
            actions = []
            want_text = not self.modal and getattr(self.stack[-1], "text_entry", False)
            if want_text != self.input.text_mode:
                self.input.text_mode = pads.INPUT_MODE["text"] = want_text
                try:
                    (pygame.key.start_text_input if want_text else pygame.key.stop_text_input)()
                except Exception:
                    pass
            events = self.pointer_later() + early + pygame.event.get()
            for ev in events:
                if ev.type == pygame.QUIT:
                    actions.append("quit")
                elif not self.pointer(ev, actions):
                    if ev.type in (pygame.KEYDOWN, pygame.CONTROLLERBUTTONDOWN, pygame.JOYBUTTONDOWN) \
                            and not self.headless:
                        pygame.mouse.set_visible(False)
                    self.input.handle(ev, actions)
            self.input.repeats(actions)
            if scripted is not None:
                actions += scripted(self)
            with self.lock:
                fns, self.events_from_threads = self.events_from_threads, []
            for fn in fns:
                fn()
            for a in actions:
                self.dispatch(a)
            self.tick_downloads()
            self.draw()
            rest.frame(self.canvas, bool(events or actions or fns))
        self.on_exit()

    # ---- touch / mouse: screens register tap targets while drawing
    def hit(self, rect, fn):
        self.targets.append((pygame.Rect(rect), fn))

    def to_logical(self, pos):
        if self.screen is None:
            return pos
        sw, sh = self.screen.get_size()
        cw, ch = self.canvas.get_size()
        return ((pos[0] - (sw - cw) // 2) / theme.K, (pos[1] - (sh - ch) // 2) / theme.K)

    DRAG_STEP = 46
    TAP_SETTLE, TAP_NEAR = 0.35, 28     # a double tap: this soon after a tap, this near it

    def pointer(self, ev, actions):
        """A tap or click acts on what was under it when pressed, if it's let go
        on the same thing; a finger's drag and the wheel scroll. Returns True
        if the event was a pointer event."""
        t = ev.type
        if t not in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.MOUSEMOTION, pygame.MOUSEWHEEL):
            return False
        if self.tapped or self.ptr_later:
            # a tap acted since the last frame was drawn: what's on screen is
            # about to change, so the rest waits for the next frame's targets
            self.ptr_later.append(ev)
            return True
        if t == pygame.MOUSEBUTTONDOWN and ev.button == 1:
            at, was, where = self.last_tap
            p = self.to_logical(ev.pos)
            if time.time() - at < self.TAP_SETTLE and abs(p[0] - where[0]) <= self.TAP_NEAR \
                    and abs(p[1] - where[1]) <= self.TAP_NEAR and not self.same_target(self.target_at(p), was):
                # the second tap of a double tap, and something else is under it now: it was aimed at
                # what the first one hit (a row, not the Delete button or the setting that took its place)
                self.ptr = None
                return True
            touch = bool(getattr(ev, "touch", False))
            # show the cursor for a real mouse click only (evmapy maps the right
            # stick to a mouse, which must not make it pop up; touch needs none)
            if not touch and not self.headless:
                pygame.mouse.set_visible(True)
            p = self.to_logical(ev.pos)
            self.ptr = {"start": p, "y": p[1], "moved": False, "touch": touch,
                        "hit": self.target_at(p), "on": (self.stack[-1], self.modal)}
        elif t == pygame.MOUSEMOTION:
            if self.ptr and ev.buttons and ev.buttons[0]:
                p = self.to_logical(ev.pos)
                if abs(p[1] - self.ptr["start"][1]) > 14 or abs(p[0] - self.ptr["start"][0]) > 14:
                    self.ptr["moved"] = True
                # only a finger drags the list (up: content moves on): a mouse has its
                # wheel, and a click must never turn into a scroll (the pointer can jump while held)
                while self.ptr["touch"] and p[1] - self.ptr["y"] <= -self.DRAG_STEP:
                    actions.append("down")
                    self.ptr["y"] -= self.DRAG_STEP
                while self.ptr["touch"] and p[1] - self.ptr["y"] >= self.DRAG_STEP:
                    actions.append("up")
                    self.ptr["y"] += self.DRAG_STEP
        elif t == pygame.MOUSEBUTTONUP and ev.button == 1:
            ptr, self.ptr = self.ptr, None
            if ptr and not (ptr["moved"] and ptr["touch"]) and ptr["on"] == (self.stack[-1], self.modal):
                hit = self.target_at(self.to_logical(ev.pos))
                if self.same_target(hit, ptr["hit"]):
                    self.tapped = True
                    self.last_tap = (time.time(), hit, self.to_logical(ev.pos))
                    self.press(hit)
        elif t == pygame.MOUSEWHEEL:
            actions += ["up" if ev.y > 0 else "down"] * min(5, abs(ev.y))
        return True

    def pointer_later(self):
        """Pointer events held back until a frame was drawn after a tap."""
        later, self.ptr_later = self.ptr_later, []
        return later

    def target_at(self, pt):
        """(rect, fn) of the topmost tap target under pt in the frame on
        screen (only the dialog's while one is open), or None."""
        cands = self.targets[self.modal_from:] if self.modal else self.targets
        return next(((r, fn) for r, fn in reversed(cands) if r.collidepoint(pt)), None)

    @staticmethod
    def same_target(a, b):
        """The same thing on screen: same place, and the same row (a target's
        fn is a lambda with its row in its defaults, made anew each frame)."""
        if a is None or b is None:
            return a is b
        fa, fb = a[1], b[1]
        return a[0] == b[0] and (fa is fb or (fa is not None and fb is not None and
                                             fa.__code__ is fb.__code__ and fa.__defaults__ == fb.__defaults__))

    def tap(self, pt):
        self.press(self.target_at(pt))

    def press(self, hit):
        if hit:
            if hit[1]:
                self.sfx.play("ok")
                hit[1]()
        elif self.modal:
            self.dispatch("back")              # tap outside a dialog closes it

    def dispatch(self, a):
        if a.startswith("text:"):
            top = self.stack[-1]
            if not self.modal and hasattr(top, "type_text"):
                self.sfx.play("move")
                top.type_text(a[5:])
            return
        snd = SOUND_FOR.get(a)
        moving = snd in ("move", "page")
        before = self.nav_state() if moving else None
        if snd and not moving:
            self.sfx.play(snd)
        if a == "padchange":
            self.log_pads()
            self.on_pad_change()
            return
        if a == "quit":
            self.request_quit()
            return
        if self.modal:
            self.modal.action(a)
            self._moved_sound(snd, before)
            return
        if a == "select" and not isinstance(self.stack[-1], DownloadsScreen):
            self.push(DownloadsScreen(self))
            return
        if a == "start" and len(self.stack) > 1 and not getattr(self.stack[-1], "own_start", False):
            del self.stack[1:]
            self.make_custom_theme()               # Custom's unsaved colours aren't kept either
            apply_theme(self.theme_key())          # a theme being previewed (Settings, Theme) isn't kept
            self.stack[0].enter()
            return
        self.stack[-1].action(a)
        self._moved_sound(snd, before)

    def nav_state(self):
        """What a move/page could change: focus, scroll, keyboard cursor,
        menu row, screen/dialog identity."""
        out = [len(self.stack), id(self.modal), id(self.stack[-1]), self.stack[-1].nav_key()]
        for o in (self.stack[-1], self.modal):
            if o is not None:
                out += [getattr(o, k, None) for k in ("sel", "scroll", "r", "c", "region_i")]
        ents = self.modal.entries if isinstance(self.modal, ActionMenu) else None
        if ents:                                   # in-place switches / choices in a menu
            out.append(tuple(e[3] if e and len(e) > 3 else None for e in ents))
        out.append(repr(sorted(self.cfg.items())))   # Settings rows cycled with left/right
        return tuple(out)

    def _moved_sound(self, snd, before):
        # held at the top/bottom of a list, repeats change nothing: stay quiet
        if before is not None and self.nav_state() != before:
            self.sfx.play(snd)

    def tick_downloads(self):
        for j in self.downloader.jobs:
            if getattr(j, "restored", False):
                continue                             # an earlier session's, back from the saved history
            if j.state == rommlib.Job.DONE and not getattr(j, "_announced", False):
                j._announced = True
                self.toast_ready(j)
                for scr in self.stack:
                    scr.__dict__.pop("_variants", None)
                if j.chd_queued and not self.headless:
                    self.spawn_helper("rb_chd.py")   # flock'd: no-op if already running
                if j.system in self.TOOL_SYSTEMS:
                    self.fetch_tools({j.system})     # a first Wii U game: its converter's tool
            elif j.state == rommlib.Job.FAILED and not getattr(j, "_announced", False):
                j._announced = True
                self.toast("Failed: %s — %s" % (j.label, j.message), theme.RED)
        self.watch_conversions()

    CONV_WATCH = 10.0           # seconds between looks for a conversion worker that died mid-task

    def watch_conversions(self, now=None):
        """A conversion worker killed or crashed mid-task (its chd-current.json names
        a process that's gone) is started again, so the task doesn't sit there as
        converting until the app is closed. One small file read every CONV_WATCH."""
        now = time.monotonic() if now is None else now
        if self.headless or now - getattr(self, "_conv_watch_at", -self.CONV_WATCH) < self.CONV_WATCH:
            return False
        self._conv_watch_at = now
        if not rommlib.conversion_worker_gone():
            return False
        rommlib.log("chd: the conversion worker stopped mid-task; starting it again")
        self.spawn_helper("rb_chd.py")           # it clears the stale file and takes the task up again
        return True

    def toast_ready(self, j):
        """A finished download: "Ready", and when it can start now, where Play
        now is (tapping the toast plays it)."""
        title = (j.group.get("title") or j.label) if j.group else j.label
        play = self.play_entry(j.rom, j.system, title)[2]
        if getattr(j, "chd_task", None) and rommlib.needed_to_play(getattr(j, "chd_mode", None)):
            # a disc that can't start until it's set up: not "Ready" yet (Activity shows it getting ready)
            self.toast("Downloaded: %s · it plays once it's set up" % j.label, theme.AMBER)
        elif play:
            self.toast("Ready: %s · Play now from {SELECT} Activity" % j.label, theme.GREEN, tap=play)
        else:
            self.toast("Ready: %s" % j.label, theme.GREEN)

    def on_exit(self):
        rommlib.stop_running_checks()             # a Check games tool (chdman, dolphin-tool…) must not outlive the app
        self.music_stop.set()                     # a menu-music sync ends after the track it's on; the next start goes on
        pending = 0 if self.downloader.hold else self.downloader.pending_count()
        # keeps them in download-queue.json, and kills a running 7z before it returns:
        # its thread dies with this process, and a 7z left running would go on
        # writing into a folder the next unpack (rb_dl) sweeps as abandoned
        self.downloader.pause_all()
        self._finish_moves()
        if self.headless or getattr(self, "updated", None):
            return                            # updated: it all carries on when the new version starts
        if pending and self.cfg["background_downloads"]:
            self.spawn_helper("rb_dl.py")     # finishes the queue while you play
            if getattr(self, "play_after", None) and os.path.exists(rommlib.GAMELIST_QUEUE):
                self.spawn_helper("rb_finalize.py")   # Play now: ES must list the game before the queue is done
            return                            # it runs CHD + gamelist merge when done
        if os.path.exists(rommlib.GAMELIST_QUEUE):
            self.spawn_helper("rb_finalize.py")
        if rommlib.conversions_pending():
            self.spawn_helper("rb_chd.py")

    def _finish_moves(self):
        """Pause all stops an unpack where it is (its private folder is swept
        later; the archive stays), except a game whose files are being moved into
        place: that is waited for, up to rommlib.MOVE_WAIT (30 s), with a
        frame saying why the app hasn't closed yet once it takes a moment."""
        if self.headless:
            return                                # tests and the screenshot harness never wait

        def tick(busy, waited):
            pygame.event.pump()
            if waited >= 1:
                self._notice("Putting %s in place" % plural_games(busy), "The app closes when it's done")
        self.downloader.wait_moves(rommlib.MOVE_WAIT, tick)

    # ---- drawing
    def draw(self):
        draw.NOW = self.t                       # the scrolling texts' clock
        c = self.canvas
        c.fill(theme.BG)
        self.targets, self.tapped = [], False
        # subtle top glow
        rrect(c, theme.BG2, (0, 0, W, 64), 0)
        hline(c, theme.LINE, (0, 64), (W, 64))
        top = self.stack[-1]
        # logo
        rrect(c, theme.ACCENT, (24, 16, 34, 34), 9)
        text(c, self.fonts.h3, "R", (41, 33), ink_on(theme.ACCENT), "center")
        text(c, self.fonts.h2, APP_NAME, (68, 33), theme.TEXT, "midleft")
        x = 68 + int(measure(self.fonts.h2, APP_NAME)) + 14
        self.hit((16, 8, x - 16, 50), lambda: self.dispatch("start") if len(self.stack) > 1 and not getattr(
            self.stack[-1], "own_start", False) else None)      # (there Start saves what's typed: the logo never does)
        if len(self.stack) > 1:
            self.hit((x, 8, 520, 50), lambda: self.dispatch("back"))
        # right side status, first: the crumbs get the room it leaves
        n = self.downloader.active_count()
        rx = W - 24
        label = self.status_label()
        dot = theme.GREEN if label.startswith("Online") else (theme.AMBER if label.startswith("Conn") else theme.RED)
        r = text(c, self.fonts.small, label, (rx, 33), theme.DIM, "midright")
        circle(c, dot, (r.left - 12, 33), 5)
        rx = r.left - 30
        if n:
            j = next((j for j in self.downloader.jobs if j.state == rommlib.Job.DOWNLOADING), None)
            label = "%d in queue" % n + (", %s/s" % human_size(j.speed) if j and j.speed and not j.stalled() else "")
            r = text(c, self.fonts.small, label, (rx, 33), theme.BLUE, "midright")
            rx = r.left
        crumbs = list(top.crumbs())

        def width(cs):
            return sum(18 + min(380, int(measure(self.fonts.h3, cr))) + 12 for cr in cs)
        for i in range(len(crumbs) - 1):          # too long: the earliest crumbs give way first
            if x + width(crumbs) <= rx - 24:
                break
            crumbs[i] = "…"
        for crumb in crumbs:
            text(c, self.fonts.h3, "›", (x, 32), theme.FAINT, "midleft")
            x += 18
            r = text(c, self.fonts.h3, crumb, (x, 33), theme.DIM, "midleft", maxw=max(0, min(380, rx - 24 - x)))
            x = r.right + 12
        top.draw(c)
        if isinstance(top, ListScreen):
            top.draw_bubble(c)                 # the letter (or system) an L2/R2 jump landed on
        # footer
        hline(c, theme.LINE, (0, H - 46), (W, H - 46))
        # right side first (the two global buttons, then the version): the hints get what's left
        rx = W - 24
        r = text(c, self.fonts.small, "v" + rommlib.VERSION_LABEL, (rx, H - 23), theme.FAINT, "midright")
        rx = r.left - 28
        globals_ = [("START", "Home"), ("SELECT", "Activity")]
        if getattr(top, "own_start", False) or len(self.stack) == 1:
            globals_ = [g for g in globals_ if g[0] != "START"]     # Start means something else here
        if isinstance(top, DownloadsScreen):
            globals_ = [g for g in globals_ if g[0] != "SELECT"]
        for btn, label in globals_:
            r = text(c, self.fonts.small, label, (rx, H - 23), theme.DIM, "midright")
            gw = glyph_width(self.fonts, btn)
            button_glyph(c, self.fonts, btn, r.left - 8 - gw, H - 34)
            self.hit((r.left - 16 - gw, H - 46, r.width + gw + 24, 46),
                     lambda a=TOKEN_ACTION[btn]: self.dispatch(a))
            rx = r.left - 8 - gw - 22
        hints = top.hints()

        def width(hs):
            return sum(glyph_width(self.fonts, b) + 8 + measure(self.fonts.small, lab) + 22 for b, lab in hs)
        if 24 + width(hints) > rx:                   # too long: page up/down go first (L1/R1 still work)
            hints = [h for h in hints if h[0] not in ("L", "R")]
        fx = 24
        for btn, label in hints:
            if fx + glyph_width(self.fonts, btn) + 8 + measure(self.fonts.small, label) > rx:
                break
            x0 = fx
            gw = button_glyph(c, self.fonts, btn, fx, H - 34)
            fx += gw + 8
            r = text(c, self.fonts.small, label, (fx, H - 23), theme.DIM, "midleft")
            if btn in TOKEN_ACTION:
                self.hit((x0 - 8, H - 46, r.right - x0 + 16, 46), lambda a=TOKEN_ACTION[btn]: self.dispatch(a))
            if btn in TOKEN_BACK:                    # a two-way control: its left half goes the other way
                self.hit((x0 - 8, H - 46, gw // 2 + 8, 46), lambda a=TOKEN_BACK[btn]: self.dispatch(a))
            fx = r.right + 22
        self.modal_from = len(self.targets)
        if self.modal:
            self.modal.draw(c)
        self.draw_toasts(c)
        if self.screen is not None:
            # canvas is already native-resolution; just centre it (letterbox if
            # the screen isn't 16:9)
            sw, sh = self.screen.get_size()
            cw, ch = c.get_size()
            if (sw, sh) != (cw, ch):
                self.screen.fill(theme.BG)
            self.screen.blit(c, ((sw - cw) // 2, (sh - ch) // 2))
            pygame.display.flip()

    TOAST_W = 900                  # the widest a toast's pill gets

    def draw_toasts(self, c):
        now = time.time()
        self.toasts = [t for t in self.toasts if t[2] > now][-3:]
        y = H - 64
        for t in reversed(self.toasts):
            msg, color = t[0], t[1]
            if t[3] > 1:
                msg = "%s  ×%d" % (msg, t[3])
            w = min(self.TOAST_W, rich_width(self.fonts, self.fonts.body, msg) + 52)
            r = pygame.Rect(0, 0, w, 40)
            r.midbottom = (W // 2, y)
            rrect(c, theme.PANEL_HI, r, 20)
            circle(c, color, (r.left + 18, r.centery), 5)
            marquee(c, self.fonts.body, msg, (r.left + 30, r.centery), theme.TEXT, maxw=w - 44, anchor="midleft",
                    slot=("toast", t[0]))
            if len(t) > 4 and t[4] and self.modal is None:     # never tapped through an open dialog
                self.hit(r, t[4])
            y -= 48
