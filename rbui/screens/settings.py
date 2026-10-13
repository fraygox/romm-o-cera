"""Settings."""
import os
import re
import shutil
import time

import pygame
import rommlib
from rommlib import human_size
from rbui import APP_NAME, theme
from rbui.theme import S, THEMES
from rbui.draw import card, chevron, hline, marquee, measure, rich_text, rrect, scroll_text, scrollbar, text
from rbui.pads import style_from_names, STYLE_NAMES
from rbui.screens.base import Screen
from rbui.screens.text_input import TextInputScreen
from rbui.screens.about import CreditsScreen, docs_qr, DocsScreen, draw_qr
from rbui.screens.box_data import UPDATE_WAITS
from rbui.screens.bios import BiosScreen
from rbui.screens.themes import ThemePickerScreen
from rbui.modals import Confirm


class SettingsScreen(Screen):
    """Everything in config.json, editable with the pad. Saved immediately."""

    VISIBLE = 10

    def __init__(self, app):
        super().__init__(app)
        self.sel = 0
        self.scroll = 0
        self.cache_bytes = None
        self.prev = rommlib.backup_version()
        self._measure_cache()

    def _measure_cache(self):
        def size():
            tot = 0
            for dp, _, fns in os.walk(rommlib.COVER_CACHE):
                for f in fns:
                    try:
                        tot += os.path.getsize(os.path.join(dp, f))
                    except OSError:
                        pass
            return tot
        self.app.bg(size, lambda n: setattr(self, "cache_bytes", n))

    def items(self):
        cfg = self.app.cfg
        tok = cfg["token"] or ""
        masked = (tok[:4] + "…" + tok[-4:]) if len(tok) > 10 else ("set" if tok else "not set")
        tls = rommlib.norm_verify(cfg["verify_tls"])
        tls_s = "On" if tls is True else ("Off" if tls is False else "CA: " + os.path.basename(tls))
        arcade = dict(cfg["platform_map"] or {}).get("arcade") or rommlib.PLATFORM_MAP["arcade"]
        def onoff(v):
            return "On" if v else "Off"

        def tool_row(key):
            """A switch whose converter needs a download: its state, or how far the download is."""
            spec = rommlib.CONVERTER[key].tool
            if getattr(self.app, spec.setting + "_busy", False):        # per tool: two switches can share one
                pr = getattr(self.app, spec.setting + "_progress", None)
                return {"label": "Downloading %d%%" % (pr * 100) if pr is not None else "Downloading…",
                        "help": self.TOOL_TEXT[key]["fetching"]}
            return rommlib.tool_state(spec, cfg, key)
        rvz, zar, wux, wua = tool_row("rvz"), tool_row("zar"), tool_row("wux"), tool_row("wua")
        sec = SettingsScreen.section

        def row(rid, value, sub=None):            # a config.json row: name and grey line from the table
            st = SETTING_ROW[rid]
            return rid, st.label, value, st.sub if sub is None else sub

        def table(rid):                           # a generic row: all of it from the table
            st = SETTING_ROW[rid]
            return rid, st.label, self._value(st), st.sub
        return [
            sec("RomM server"),
            row("url", rommlib.display_url(cfg["url"]) or "not set",
                "Plain HTTP: the token isn't encrypted" if (cfg["url"] or "").lower().startswith("http://")
                else None),
            row("token", masked),
            ("pair", "Pair with RomM", "Enter code", "Type the code from RomM's Pair button"),
            row("tls", tls_s),
            ("test", "Test connection", "", "Fetch the platform list now"),
            sec("Controller"),
            row("style", self._style_value()),
            row("swap", "East (right)" if cfg["swap_ab"] else "South (bottom)"),
            sec("Downloads"),
            table("hours"),
            table("keepfree"),
            table("overlap"),
            table("bgdl"),
            table("speed"),
            table("notify"),
            table("manuals"),
            ("arcade", "Arcade games go to", "roms/" + arcade, "fbneo (forgiving) or mame (exact sets)"),
            sec("Disc compression"),
            table("chd"),
            row("rvz", rvz["label"], rvz["help"]),
            table("xboxboot"),
            table("squashfs"),
            table("z3ds"),
            row("zar", zar["label"], zar["help"]),
            row("wux", wux["label"], wux["help"]),
            row("wua", wua["label"], wua["help"]),
            table("ps3folders"),
            row("ps3decrypt", onoff(cfg["ps3_decrypt"]) if rommlib.ps3_decrypt_supported() else "Not available"),
            row("chdthreads", ("Auto (%d of %d)" % (rommlib.auto_chd_threads(), rommlib.cpu_threads()))
                if str(cfg["chd_threads"]) == "auto" else
                "%d of %d" % (rommlib.chd_threads(cfg), rommlib.cpu_threads())),
            row("chdlowmem", onoff(cfg["chd_low_memory"]) if isinstance(cfg["chd_low_memory"], bool) else
                "Auto (%s)" % ", ".join([onoff(rommlib.chd_low_memory(cfg))] +
                                        (["%s RAM" % human_size(rommlib.total_ram())] if rommlib.total_ram() else []))),
            sec("This app"),
            row("theme", self._theme_value()),
            table("themeauto"),
            table("sounds"),
            table("cachelimit"),
            ("cache", "Clear cover cache",
             human_size(self.cache_bytes) if self.cache_bytes is not None else "…",
             "Covers re-download as you browse"),
            ("bios", "BIOS files", "Open", "What each system needs, and what RomM has"),
            ("credits", "Credits and licenses", "Open", "The software RomM-o-Cera uses or downloads"),
            ("docs", "Documentation", "QR code", "Open it on your phone"),
            ("resetcfg", "Reset settings", "Can't be read" if rommlib.config_unreadable() else "Reset",
             "Start over from the defaults; the old file is kept"),
            sec("Updates"),
            table("channel"),
            table("checkupdates"),
            ("update", "Updates", ("%s available" % self.app.update_tag) if self.app.update_tag
             else "v" + rommlib.VERSION_LABEL,
             ("Download, check and install %s" % self.app.update_tag) if self.app.update_tag
             else "Check GitHub for a newer " + ("build" if cfg["update_channel"] == "nightly" else "release")),
        ] + ([("rollback", "Go back to the previous version", "v" + self.prev, "Undo the last update")]
             if self.prev else [])

    def pair(self):
        """Settings, Pair with RomM: type RomM's short code, get the token."""
        app, cfg = self.app, self.app.cfg
        if not (cfg["url"] or "").strip():
            app.toast("Set the server address first, then pair", theme.AMBER)
            return

        def paired(res):
            cfg["token"] = res["token"]
            app.apply_config(reconnect=True)
            app.toast("Paired with RomM" + (" (%s)" % res["name"] if res.get("name") else "")
                      + " — reconnecting", theme.GREEN)

        def submit(v):
            try:
                code = rommlib.normalize_pair_code(v)
            except rommlib.RommError as e:
                app.toast(str(e), theme.RED)
                return
            app.toast("Pairing…")
            url, verify = cfg["url"], cfg["verify_tls"]
            app.bg(lambda: rommlib.exchange_pair_code(url, code, verify), paired,
                   lambda e: app.toast(str(e), theme.RED))
        app.push(TextInputScreen(app, "Pairing code from RomM", "", submit, mode="code", primary="Pair",
                                 help=field_help("pair")))

    @staticmethod
    def section(title):
        return ("§", title, "", "")

    # generic rows that need something this box may lack: (is it here?, the toast when it isn't)
    NEEDS = {
        "squashfs": (lambda: rommlib.squashfs_supported(), "mksquashfs isn't on this system"),
        "z3ds": (lambda: rommlib.z3ds_supported(), "libzstd isn't on this system"),
    }
    # a choice row's value text, from the whole config
    SHOW = {
        "hours": lambda cfg: rommlib.hours_label(cfg["download_hours"]),
        "keepfree": lambda cfg: "%d GB" % float(cfg["keep_free_gb"]) if cfg["keep_free_gb"] else "Nothing (fill the disk)",
        "speed": lambda cfg: "%g MB/s" % float(cfg["speed_while_playing"]) if cfg["speed_while_playing"] else "No limit",
        "sounds": lambda cfg: {"normal": "On", "quiet": "Quiet", "off": "Off"}.get(cfg["sounds"], "On"),
        "channel": lambda cfg: {"stable": "Stable", "nightly": "Nightly"}.get(cfg["update_channel"], "Stable"),
        "cachelimit": lambda cfg: (rommlib.human_size(rommlib.cache_limit_bytes(cfg))
                                   if rommlib.cache_limit_bytes(cfg) else "No limit"),
    }

    def _wake(self, app):
        with app.downloader.cv:
            app.downloader.cv.notify_all()

    def _sounds(self, app):
        app.sfx.set_level(app.cfg["sounds"])
        app.sfx.play("toggle")

    def _cache_limit(self, app):
        lim = rommlib.cache_limit_bytes(app.cfg)
        app.api.cache_limit = lim
        if lim:
            app.bg(lambda: rommlib.prune_cache(lim), lambda r: self._measure_cache(), lambda e: None)

    def _check_updates(self, app):
        """Daily check back on: check now, not at the next start. Off: forget what it found."""
        if app.cfg["check_updates"]:
            self._update_channel(app)
        else:
            app.update_tag = None

    def _update_channel(self, app):
        """A new channel: forget what the old one offered and ask GitHub again."""
        app.update_tag = None

        def found(tag):
            app.update_tag = tag
            if tag:
                app.toast("%s is available" % tag, theme.BLUE)
        app.bg(lambda: rommlib.newer_release(dict(app.cfg, check_updates=True), force=True), found,
               lambda e: None)

    # what a change to a generic row sets off, by name (rommlib.Setting.effects): (screen, app)
    EFFECTS = {
        "downloader": lambda screen, app: setattr(app.downloader, "cfg", app.cfg),
        "wake": _wake,
        "sounds": _sounds,
        "cache_limit": _cache_limit,
        "update_channel": _update_channel,
        "check_updates": _check_updates,
    }

    def _available(self, st):
        need = self.NEEDS.get(st.row)
        return need is None or need[0]()

    def _value(self, st):
        """A generic row's value text."""
        if not self._available(st):
            return "Not available"
        if st.kind == "choice":
            return self.SHOW[st.row](self.app.cfg)
        return "On" if self.app.cfg[st.key] else "Off"

    STYLES = ["auto", "xbox", "playstation", "nintendo"]
    SPEEDS = rommlib.SPEEDS                   # MB/s, 0 = no limit
    THEME_CYCLE = list(THEMES)                       # what left and right cycle through
    LOW_MEMORY = ["auto", True, False]

    def _theme_value(self):
        cfg = self.app.cfg
        chosen = THEMES.get(cfg["theme"], THEMES["default"])[0]
        now = self.app.theme_key()
        if now != cfg["theme"] and now in THEMES:              # matched to the controller
            return "%s (now %s)" % (chosen, THEMES[now][0])
        return chosen

    def _style_value(self):
        want = self.app.cfg["button_style"] or "auto"
        if want == "auto":
            return "Auto (%s)" % STYLE_NAMES[style_from_names(self.app.input.names())]
        return STYLE_NAMES.get(want, want)

    def crumbs(self):
        return ["Settings"]

    ACTIONS = ("url", "token", "pair", "test", "cache", "update", "rollback", "bios", "credits", "docs")
    STEPLESS = ACTIONS + ("resetcfg",)        # rows left/right don't step through

    def hints(self):
        items = self.items()
        key = items[min(self.sel, len(items) - 1)][0]
        jump = [("L", "Page up"), ("R", "Page down")]      # L/R move six rows
        if key in self.STEPLESS:
            return [("A", "Open" if key in ("bios", "credits", "docs") else "Select")] + jump + [("B", "Back")]
        if key == "theme":
            return [("A", "All themes"), ("LEFT RIGHT", "Choose")] + jump + [("B", "Back")]
        return [("A", "Change"), ("LEFT RIGHT", "Choose")] + jump + [("B", "Back")]

    def selectable(self):
        return [i for i, it in enumerate(self.items()) if it[0] != "§"]

    tapped = None                  # the row a tap picked: drawing doesn't scroll it away

    def tap_now(self, i, a="ok"):
        """Touch: act on the row at once ("left" on the left of a value steps back)."""
        self.sel = self.tapped = i
        self.action(a)

    def action(self, a):
        idx = self.selectable()
        if self.sel not in idx:
            self.sel = idx[0]
        if a in ("up", "down", "l1", "r1"):
            self.tapped = None
            k = idx.index(self.sel)
            step = {"up": -1, "down": 1, "l1": -6, "r1": 6}[a]
            k = (k + step) % len(idx) if a in ("up", "down") else max(0, min(len(idx) - 1, k + step))
            self.sel = idx[k]
        elif a in ("ok", "left", "right"):
            key = self.items()[self.sel][0]
            app, cfg = self.app, self.app.cfg
            if key == "url" and a == "ok":
                def save_url(v):
                    if v and "://" not in v:
                        v = "https://" + v
                    if rommlib.url_has_userinfo(v):
                        app.toast("Leave user:password out of the URL — the token is the login", theme.RED)
                        return
                    cfg["url"] = v.rstrip("/")
                    app.apply_config(reconnect=True)
                    app.toast("Server saved — reconnecting", theme.GREEN)
                cur = cfg["url"] or "https://"
                if rommlib.url_has_userinfo(cur):
                    cur = "https://"          # never pre-fill an embedded password
                app.push(TextInputScreen(app, "RomM server URL", cur, save_url,
                                         context="url", help=field_help("url")))
            elif key == "token" and a == "ok":
                def save_tok(v):
                    cfg["token"] = v
                    app.apply_config(reconnect=True)
                    app.toast("Token saved — reconnecting", theme.GREEN)
                app.push(TextInputScreen(app, "RomM client API token", cfg["token"] or "", save_tok, secret=True,
                                         context="token", help=field_help("token")))
            elif key == "pair" and a == "ok":
                self.pair()
            elif key == "tls":
                cur = rommlib.norm_verify(cfg["verify_tls"])
                ca = cfg["verify_tls_ca"] or ""
                if isinstance(cur, str):
                    cfg["verify_tls_ca"] = ca = cur          # remember the bundle when cycling away
                cycle = [True, False] + ([ca] if ca else [])
                i = next((k for k, v in enumerate(cycle) if v == cur), 0)
                cfg["verify_tls"] = cycle[(i + (-1 if a == "left" else 1)) % len(cycle)]
                app.apply_config(reconnect=True)
                if cfg["verify_tls"] is False:
                    app.toast("Certificates aren't checked now — only for a self-signed RomM", theme.AMBER)
            elif key == "test" and a == "ok":
                app.toast("Testing…")
                app.bg(app.api.platforms,
                       lambda p: app.toast("Connected: %d platforms on the server" % len(p), theme.GREEN),
                       lambda e: app.toast(str(e), theme.RED))
            elif key in SETTING_ROW and SETTING_ROW[key].kind == "onoff":
                st = SETTING_ROW[key]
                if not self._available(st):
                    app.toast(self.NEEDS[key][1], theme.AMBER)
                else:
                    cfg[st.key] = not cfg[st.key]
                    app.apply_config()
                    for e in st.effects:
                        self.EFFECTS[e](self, app)
            elif key in SETTING_ROW and SETTING_ROW[key].kind == "choice":
                st = SETTING_ROW[key]
                opts, cur = list(st.choices), cfg[st.key]
                i = opts.index(cur) if cur in opts else opts.index(st.default)
                cfg[st.key] = opts[(i + (-1 if a == "left" else 1)) % len(opts)]
                app.apply_config()
                for e in st.effects:
                    self.EFFECTS[e](self, app)
            elif key == "style":
                cur = cfg["button_style"] or "auto"
                i = self.STYLES.index(cur) if cur in self.STYLES else 0
                cfg["button_style"] = self.STYLES[(i + (-1 if a == "left" else 1)) % len(self.STYLES)]
                app.apply_config()
            elif key == "swap":
                cfg["swap_ab"] = not cfg["swap_ab"]
                app.apply_config()
                app.toast("Confirm is now the %s button" % ("east" if cfg["swap_ab"] else "south"))
            elif key == "theme" and a == "ok":
                app.push(ThemePickerScreen(app))    # every theme as a card, previewed live
            elif key == "theme":
                keys = self.THEME_CYCLE
                cur = cfg["theme"] if cfg["theme"] in keys else "default"
                cfg["theme"] = keys[(keys.index(cur) + (-1 if a == "left" else 1)) % len(keys)]
                app.apply_config()                  # repaints in the new colours on the next frame
                app.sfx.play("toggle")
            elif key == "chdthreads":
                n = rommlib.cpu_threads()
                opts = ["auto"] + list(range(1, n + 1))
                cur = cfg["chd_threads"]
                cur = cur if cur in opts else "auto"
                cfg["chd_threads"] = opts[(opts.index(cur) + (-1 if a == "left" else 1)) % len(opts)]
                app.apply_config()
            elif key == "chdlowmem":
                opts, cur = self.LOW_MEMORY, cfg["chd_low_memory"]
                i = opts.index(cur) if isinstance(cur, bool) else 0
                cfg["chd_low_memory"] = opts[(i + (-1 if a == "left" else 1)) % len(opts)]
                app.apply_config()
            elif key in self.TOOL_TEXT:
                self._toggle_tool(key)
            elif key == "ps3decrypt":
                self._toggle_ps3_decrypt()
            elif key == "credits" and a == "ok":
                app.push(CreditsScreen(app))
            elif key == "docs" and a == "ok":
                app.push(DocsScreen(app))
            elif key == "resetcfg" and a == "ok":
                app.modal = Confirm(app, "Reset all settings?",
                                    "Every setting goes back to its default, including the server address and "
                                    "token. The current settings file is kept beside it.",
                                    "Reset settings", app.reset_config, danger=True)
            elif key == "arcade":
                pm = dict(cfg["platform_map"] or {})
                cur = pm.get("arcade") or rommlib.PLATFORM_MAP["arcade"]
                pm["arcade"] = "mame" if cur == "fbneo" else "fbneo"
                cfg["platform_map"] = pm
                app.apply_config(reconnect=True)
            elif key == "update" and a == "ok":
                if app.update_tag:
                    tag = app.update_tag
                    app.modal = Confirm(app, "Update to %s?" % tag,
                                        "Downloads and checks it, then restarts %s to install it. Downloads pause "
                                        "and carry on after; a disc being compressed starts over. v%s is kept so "
                                        "you can go back."
                                        % (APP_NAME, rommlib.VERSION_LABEL),
                                        "Update and restart", lambda: self._do_update())
                else:
                    app.toast("Checking GitHub…")

                    def checked(tag):
                        app.update_tag = tag
                        app.toast(("%s is available" % tag) if tag else
                                  "You have the latest version (v%s)" % rommlib.VERSION_LABEL,
                                  theme.BLUE if tag else theme.GREEN)
                    app.bg(lambda: rommlib.newer_release(dict(cfg, check_updates=True), force=True), checked)
            elif key == "rollback" and a == "ok":
                app.modal = Confirm(app, "Go back to v%s?" % self.prev,
                                    "Swaps v%s back in (v%s is kept so you can undo this), then restarts %s: "
                                    "downloads in progress pause and carry on when it's back."
                                    % (self.prev, rommlib.VERSION_LABEL, APP_NAME),
                                    "Go back and restart", lambda: self._swap(None, rommlib.rollback), danger=True)
            elif key == "bios" and a == "ok":
                app.push(BiosScreen(app))
            elif key == "cache" and a == "ok":
                shutil.rmtree(rommlib.COVER_CACHE, ignore_errors=True)
                app.forget_covers()
                self.cache_bytes = 0
                app.toast("Cover cache cleared", theme.GREEN)
        else:
            super().action(a)

    # switches whose converter needs a tool downloaded (Converter.tool): what they say
    TOOL_TEXT = {
        "rvz": {"cpu": "dolphin-tool is built for x86_64 and 64-bit ARM boxes only",
                "busy": "dolphin-tool is still downloading",
                "off": "GameCube and Wii conversion off (dolphin-tool stays installed)",
                "on": "GameCube and Wii games will be converted to RVZ",
                "title": "Download dolphin-tool?",
                "body": "RVZ needs Dolphin's dolphin-tool {version}{size}, built from the Dolphin Batocera uses "
                        "(GPL-2.0-or-later) and checked by SHA-256 before use.",
                "start": "Downloading dolphin-tool…",
                "fetching": "dolphin-tool, from RomM-o-Cera's GitHub releases",
                "done": "dolphin-tool {version} installed: GameCube and Wii games convert to RVZ",
                "failed": "dolphin-tool download failed ({error})"},
        "zar": {"cpu": "Xbox 360 emulation on Batocera is x86_64 only",
                "busy": "The Xbox 360 tools are still downloading",
                "off": "Xbox 360 conversion off (the tools stay installed)",
                "on": "Xbox 360 games will be converted to .zar",
                "title": "Download the Xbox 360 tools?",
                "body": "Converting to .zar needs extract-xiso and zarchive{size}, built from their published "
                        "source and checked by SHA-256 before use. A .zar keeps the game's files, not the whole disc.",
                "start": "Downloading the Xbox 360 tools…",
                "fetching": "extract-xiso and zarchive, from RomM-o-Cera's GitHub releases",
                "done": "Xbox 360 tools installed: Xbox 360 games convert to .zar",
                "failed": "Xbox 360 tools download failed ({error})"},
        "wux": {"cpu": "wuatool is built for x86_64 and 64-bit ARM boxes only",
                "busy": "wuatool is still downloading",
                "off": "Wii U to .wux off (wuatool stays installed)",
                "on": "Wii U disc images will be compressed to .wux",
                "title": "Download wuatool?",
                "body": "Compressing to .wux needs wuatool {version}{size}: Cemu's disc code and ZArchive "
                        "(MPL-2.0), checked by SHA-256 before use. A .wux is lossless and needs no keys.",
                "start": "Downloading wuatool…",
                "fetching": "wuatool, from RomM-o-Cera's GitHub releases",
                "done": "wuatool {version} installed: Wii U disc images compress to .wux",
                "failed": "wuatool download failed ({error})"},
        "wua": {"cpu": "wuatool is built for x86_64 and 64-bit ARM boxes only",
                "busy": "wuatool is still downloading",
                "off": "Decrypt Wii U discs off (wuatool stays installed)",
                "on": "Wii U discs with their key will become .wua",
                "title": "Download wuatool?",
                "body": "Decrypting needs wuatool {version}{size}: Cemu's disc code and ZArchive (MPL-2.0), checked "
                        "by SHA-256 before use. Each disc needs its key (games you own); a .wua holds the game, not "
                        "the whole disc.",
                "start": "Downloading wuatool…",
                "fetching": "wuatool, from RomM-o-Cera's GitHub releases",
                "done": "wuatool {version} installed: Wii U discs with their key become .wua",
                "failed": "wuatool download failed ({error})"},
    }

    def _toggle_tool(self, key):
        """Turn a converter on or off; the first time on, download its tool (asked first)."""
        app, cfg = self.app, self.app.cfg
        spec, say = rommlib.CONVERTER[key].tool, self.TOOL_TEXT[key]
        busy = spec.setting + "_busy"             # per tool: Wii U's two switches share wuatool
        if not spec.supported():
            app.toast(say["cpu"], theme.AMBER)
            return
        if getattr(app, busy, False):
            app.toast(say["busy"])
            return
        if spec.installed():
            cfg[key] = not cfg[key]
            app.apply_config()
            app.downloader.cfg = cfg
            if cfg[key]:
                app.toast(say["on"], theme.GREEN)
                self._after_tool_on(key)
            else:
                app.toast(say["off"])
            return
        t = spec.pin

        def install():
            def prog(got, total):
                setattr(app, spec.setting + "_progress", (got / total) if total else None)
            try:
                return rommlib.install_tool(spec, prog)
            finally:
                setattr(app, busy, False)

        def done(paths):
            cfg[key] = True
            app.apply_config()
            app.downloader.cfg = cfg
            app.toast(say["done"].format(version=t["version"]), theme.GREEN)
            self._after_tool_on(key)
            if rommlib.conversions_pending():
                app.spawn_helper("rb_chd.py")       # the conversions queued while it was coming

        def failed(e):
            app.toast(str(e) if isinstance(e, rommlib.RommError) else say["failed"].format(error=e.__class__.__name__),
                      theme.RED)
        size = (" (%s)" % human_size(t["size"])) if t.get("size") else ""
        app.modal = Confirm(app, say["title"], say["body"].format(version=t["version"], size=size), "Download",
                            lambda: start())

        def start():
            if getattr(app, busy, False):            # the app started fetching it meanwhile
                app.toast(say["busy"])
                return
            setattr(app, busy, True)                 # before the thread: never two installs at once
            app.toast(say["start"])
            app.bg(install, done, failed)

    def _after_tool_on(self, key):
        """Wii U: copy the disc keys in RomM's Wii U firmware (if any) into bios/wiiukeys."""
        if key != "wua":
            return
        app = self.app
        pid = next((p["id"] for p in app.platforms if p.get("_system") == "wiiu"), None)

        def done(n):
            if n:
                app.toast("%s Wii U disc key%s ready" % ("{:,}".format(n), "" if n == 1 else "s"), theme.GREEN)
            else:
                app.toast("No Wii U disc keys yet: put the keys for discs you own in Cemu's keys.txt "
                          "(bios/cemu) or bios/wiiukeys, or add them to RomM's Wii U firmware", theme.AMBER)
        app.bg(lambda: rommlib.fetch_wiiu_keys(app.api, pid) if pid and app.api else len(rommlib.wiiu_keys()),
               done, lambda e: (app.toast(str(e), theme.RED), done(len(rommlib.wiiu_keys()))))

    def _toggle_ps3_decrypt(self):
        """On: copy the disc keys in RomM's PS3 firmware (if any) into bios/ps3keys."""
        app, cfg = self.app, self.app.cfg
        if not rommlib.ps3_decrypt_supported():
            app.toast("Python's cryptography module isn't on this system", theme.AMBER)
            return
        cfg["ps3_decrypt"] = not cfg["ps3_decrypt"]
        app.apply_config()
        app.downloader.cfg = cfg
        if not cfg["ps3_decrypt"]:
            return
        pid = next((p["id"] for p in app.platforms if p.get("_system") == "ps3"), None)

        def done(n):
            if n:
                app.toast("%s disc keys ready in bios/ps3keys" % "{:,}".format(n), theme.GREEN)
            else:
                app.toast("No disc keys yet: put the keys for games you own in bios/ps3keys, or add "
                          "them to RomM's PS3 firmware", theme.AMBER)
        app.bg(lambda: rommlib.fetch_ps3_keys(app.api, pid) if pid and app.api else len(rommlib.ps3_keys()),
               done, lambda e: (app.toast(str(e), theme.RED), done(len(rommlib.ps3_keys()))))

    def _do_update(self):
        tag = self.app.update_tag
        self._swap(lambda: rommlib.stage_release(rommlib.release_info(tag)), rommlib.install_staged)

    def _swap(self, prepare, install):
        """Update (prepare = download and check, while everything still runs)
        or go back, then close: downloads pause (kept in the queue), the
        background helpers stop, the app folder is swapped and the app quits.
        Nothing of the old version is left running beside the new files."""
        app = self.app
        if getattr(app, "updating", False):
            app.toast("An update is already running")
            return
        app.updating = True
        closing = []

        def close(staged=None):
            closing.append(True)
            app.toast("Closing %s to install…" % APP_NAME)
            app.downloader.pause_all()

            def run():
                end = time.monotonic() + 15            # a transfer stops at once; unzipping may take a moment
                while time.monotonic() < end and any(j.state in UPDATE_WAITS for j in list(app.downloader.jobs)):
                    time.sleep(0.2)
                rommlib.stop_helpers()
                return install(staged) if staged is not None else install()
            app.bg(run, done, err)

        def done(ver):
            app.updated = ver
            back = rommlib.relaunch_when_closed(python="/usr/bin/python3" if os.path.exists("/usr/bin/python3") else None)
            rommlib.es_notify("%s is now v%s. %s" % (APP_NAME, ver, "Opening it again…" if back else
                                                     "Open it again from Pygame."))
            app._quit()                             # on_exit starts no helpers: all of it resumes when it's back

        def err(e):
            app.updating = False
            msg = str(e) if isinstance(e, (rommlib.UpdateError, rommlib.RommError)) else \
                "Update failed (%s)" % e.__class__.__name__
            app.toast(msg + ("; downloads are paused (Downloads, X resumes)" if closing else ""), theme.RED)
        if prepare:
            app.toast("Downloading and checking the update…")
            app.bg(prepare, close, err)
        else:
            close()

    ROW_H, HEAD_H, TOP, BOTTOM = 57, 40, 84, 638

    def draw(self, c):
        items = self.items()
        if items[min(self.sel, len(items) - 1)][0] == "§":
            self.sel = self.selectable()[0]
        ys, y = [], 0                      # layout in list space
        for key, *_ in items:
            ys.append(y)
            y += self.HEAD_H if key == "§" else self.ROW_H
        total, view = y, self.BOTTOM - self.TOP
        top_of = ys[self.sel] - (self.HEAD_H if self.sel and items[self.sel - 1][0] == "§" else 0)
        if self.tapped == self.sel and -2 <= ys[self.sel] - self.scroll <= view - 52:
            pass                               # a tapped row stays under the finger
        elif top_of < self.scroll:
            self.scroll = top_of
        elif ys[self.sel] + self.ROW_H > self.scroll + view:
            need = ys[self.sel] + self.ROW_H - view
            self.scroll = min(y for y in ys if y >= need)     # start on a whole row
        self.scroll = max(0, min(self.scroll, max(0, total - view)))
        clip = c.get_clip()
        c.set_clip(pygame.Rect(S(40), S(self.TOP - 4), S(730), S(view + 4)))
        for i, (key, label, value, sub) in enumerate(items):
            y = self.TOP + ys[i] - self.scroll
            h = self.HEAD_H if key == "§" else 52
            if y < self.TOP - 2 or y + h > self.BOTTOM:
                continue                              # whole rows only
            if key == "§":
                text(c, self.f.small, label.upper(), (52, y + 16), theme.ACCENT_HI)
                hline(c, theme.LINE, (60 + measure(self.f.small, label.upper()), y + 26), (748, y + 26))
                continue
            r = pygame.Rect(48, y, 700, 52)
            self.app.hit(r, lambda i=i: self.tap_now(i))
            on = i == self.sel
            card(c, r, on)
            text(c, self.f.h3, label, (r.left + 22, r.top + 4), theme.TEXT if on else theme.DIM)
            warn = sub.startswith("Plain HTTP")
            marquee(c, self.f.small, sub, (r.left + 22, r.top + 28), theme.AMBER if warn else theme.FAINT, maxw=420,
                    focused=on, slot="settings-sub")
            vcol = theme.ACCENT_HI if key in self.ACTIONS else (theme.TEXT if on else theme.DIM)
            vr = text(c, self.f.body, value, (r.right - 22, r.centery), vcol, "midright", maxw=240)
            if key not in self.STEPLESS:          # a tap left of the value's middle steps back
                bx = min(vr.left - 28, vr.centerx - 60)
                self.app.hit((bx, y, vr.centerx - bx, 52), lambda i=i: self.tap_now(i, "left"))
                if on:                                # ‹ › around the value: back and on
                    for x0, side in ((vr.left - 12, "left"), (r.right - 12, "right")):
                        chevron(c, theme.ACCENT_HI, (x0, r.centery), side)
        c.set_clip(clip)
        if total > view:
            scrollbar(c, (756, self.TOP, 4, view - 5), view / total, self.scroll / max(1, total - view))
        # right panel: what the selected setting does, then About
        p = pygame.Rect(776, 84, 472, 554)
        rrect(c, theme.PANEL, p, 16)
        key, label, value, sub = items[self.sel]
        sec = next((it[1] for it in reversed(items[:self.sel]) if it[0] == "§"), "")
        text(c, self.f.small, sec.upper(), (p.left + 28, p.top + 24), theme.ACCENT_HI)
        text(c, self.f.h2, label, (p.left + 28, p.top + 46), theme.TEXT, maxw=p.width - 56)
        yy = p.top + 92
        a = p.bottom - 150
        room = 4 * 26 if key == "docs" else a - 40 - yy            # the QR code needs the room; else up to "Now:"
        yy = scroll_text(c, self.f.body, SETTING_HELP.get(key, sub), (p.left + 28, yy, p.width - 56, room),
                         theme.DIM, key=key, line_h=26, slot="settings-help").bottom
        if key == "docs":
            draw_qr(c, docs_qr(), pygame.Rect(p.left + 28, yy + 10, p.width - 56, a - yy - 22))
        elif value:
            yy += 8
            text(c, self.f.small, "Now: " + value, (p.left + 28, yy), theme.ACCENT_HI, maxw=p.width - 56)
        hline(c, theme.LINE, (p.left + 28, a), (p.right - 28, a))
        text(c, self.f.h3, APP_NAME, (p.left + 28, a + 14), theme.TEXT)
        text(c, self.f.small, "v%s · %s · by %s" % (rommlib.VERSION_LABEL, rommlib.RELEASE_DATE, rommlib.AUTHOR),
             (p.left + 28, a + 44), theme.ACCENT_HI)
        upd = ("%s is out: Updates installs it." % self.app.update_tag) if self.app.update_tag else \
            rommlib.PROJECT_URL.replace("https://", "")
        text(c, self.f.small, upd, (p.left + 28, a + 68), theme.BLUE if self.app.update_tag else theme.FAINT,
             maxw=p.width - 56)
        text(c, self.f.small, "%s · %d platforms" % (self.app.error or self.app.status, len(self.app.platforms)),
             (p.left + 28, a + 92), theme.FAINT, maxw=p.width - 56)
        rich_text(c, self.f, self.f.small, "Hotkey + {START} exits", (p.left + 28, a + 116), theme.FAINT)


def field_help(key):
    """A text setting's help, beside its keyboard (where {A} types, not edits)."""
    return re.sub(r"\s*\{A\} edits it\.", "", SETTING_HELP[key])


# Settings rows for a config.json key take their name, grey line and help from rommlib.SETTINGS
SETTING_ROW = {s.row: s for s in rommlib.SETTINGS if s.row}
SETTING_HELP = dict({s.row: s.help for s in rommlib.SETTINGS if s.row}, **{
    "pair": "Sign in without typing the token: in RomM open Profile, Client API tokens, press Pair, and "
            "type the 8-character code here within a minute. Give each box its own token.",
    "test": "Asks RomM for its platform list right now and says how many it found.",
    "arcade": "Where arcade games go: fbneo is forgiving about romset versions, mame needs exact sets.",
    "resetcfg": "Puts every setting back to its default, server and token included. The old file is kept "
                "as config.json.bad-…, so nothing is lost.",
    "cache": "Deletes every cached cover; they download again as you browse.",
    "bios": "What each system needs, what's missing, and what your RomM can supply.",
    "credits": "The people and projects RomM-o-Cera is built on or downloads, and their licenses.",
    "docs": "Point your phone's camera at the code to open the documentation. {A} shows it bigger.",
    "update": "Checks GitHub for a newer version, then downloads it, checks it and restarts to install it. "
              "Downloads pause meanwhile, and the current version is kept so you can go back.",
    "rollback": "Swaps the previous version back in and restarts the app. The current one is kept, so this can "
                "be undone too.",
})
