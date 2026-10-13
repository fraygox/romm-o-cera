"""The first-run Welcome: the server address, pairing with RomM, then the platform list.

Shown over Home when the box has no server or no token (or its token is turned away on the
first platform fetch); never on a box whose server and token come from the environment. The
steps on the left, the current one on the right with the shared keyboard. Everything it sets
goes through rbui.server, as Settings' rows do."""
import os
import urllib.parse

import pygame
import rommlib
from rbui import server, theme
from rbui.draw import button_glyph, card, hline, measure, scroll_text, text
from rbui.screens.base import Screen
from rbui.screens.home import PlatformScreen
from rbui.widgets.chips import button_chip, button_chip_width, PILL_GAP, PILL_PAD, pill_row, step_dot
from rbui.widgets.keyboard import Keyboard, code_slots, text_field
from rbui.widgets.layout import list_row, notice, Panel

STEPS = (("Server address", "Where your RomM lives"),
         ("Pair with RomM", "The 8-character code RomM shows"),
         ("Pick a platform", "Your library loads; browse or search"))
LEFT = pygame.Rect(48, 84, 372, 558)
RIGHT = pygame.Rect(452, 84, 780, 558)
TLS_LABELS = ("On", "Off", "CA bundle")
TLS_MODES = ("on", "off", "ca")

# a certificate that didn't verify: why, and what to do (TlsRejected.why)
CERT_SAYS = {
    "self-signed": "It's self-signed, which is usual for a home server. If that's what you expect, turn "
                   "verification off or add your CA bundle under Advanced.",
    "unknown": "It's signed by an authority this box doesn't know, which is usual for a home-lab certificate. "
               "If that's what you expect, turn verification off or add your CA bundle under Advanced.",
    "expired": "It has expired. Renew it on the server (or check this box's date and time), then try again.",
    "hostname": "It was made for a different name than this address. Type the name the certificate is for, "
                "or turn verification off under Advanced.",
    "other": "It couldn't be checked. For a self-signed or home-lab certificate, turn verification off or add "
             "your CA bundle under Advanced.",
}


def welcome_due(cfg):
    """Welcome at start: no server or no token, not put off before, not set by the environment."""
    if rommlib.env_configured() or rommlib.config_value(cfg, "welcome_seen") is True:
        return False
    return not ((cfg.get("url") or "").strip() and (cfg.get("token") or "").strip())


def welcome_on_rejected(app):
    """Welcome again when RomM turns the saved token away on the first platform fetch."""
    if rommlib.env_configured() or not (app.cfg.get("token") or "").strip():
        return False
    return not any(isinstance(s, WelcomeScreen) for s in app.stack)


class WelcomeScreen(Screen):
    """step 0 the address (view "keys", "advanced" or "cert"), 1 the pairing code, 2 the
    library loading, then Platforms."""
    own_start = True

    def __init__(self, app, step=0):
        super().__init__(app)
        self.step, self.view, self.back_to = step, "keys", "keys"
        self.field = "url"
        cur = app.cfg["url"] or "https://"
        if rommlib.url_has_userinfo(cur):
            cur = "https://"                       # never pre-fill an embedded password
        self.url_kb = Keyboard("Connect", self.connect, cur, context="url", y_key=None)
        self.token_kb = Keyboard("Connect", self.connect, "", context="token", y_key=None)
        self.code_kb = Keyboard("Check", self.check_code, "", mode="code", maxlen=rommlib.PAIR_CODE_LEN,
                                on_change=self.code_changed)
        self.adv_sel = self.err_sel = 0
        self.cert = None            # TlsRejected.why while the certificate error shows
        self.busy = None            # what's being checked ("Connecting to …")
        self.done = False

    def nav_key(self):
        keys = self.kb.nav_key() if self.text_entry else None
        return (self.step, self.view, self.field, self.adv_sel, self.err_sel, keys)

    # ---- state
    @property
    def kb(self):
        if self.step == 1:
            return self.code_kb
        return self.token_kb if self.field == "token" else self.url_kb

    @property
    def text_entry(self):
        return self.step < 2 and self.view == "keys"

    def url(self):
        try:
            return rommlib.clean_server_url(self.url_kb.value)
        except rommlib.RommError:
            return ""

    def host(self):
        return urllib.parse.urlsplit(self.url()).hostname or ""

    def type_text(self, s):
        if self.text_entry:
            self.kb.type_text(s)

    def mark_seen(self):
        self.app.cfg["welcome_seen"] = True
        self.app.apply_config()

    def later(self):
        self.mark_seen()
        if self.app.stack[-1] is self:
            self.app.pop()

    def finish(self):
        if self.app.stack[-1] is not self:
            return
        self.mark_seen()
        self.app.pop()
        self.app.push(PlatformScreen(self.app))

    # ---- step 1: the address
    def connect(self):
        if self.busy:
            return
        app = self.app
        try:
            url = rommlib.clean_server_url(self.url_kb.value)
        except rommlib.RommError as e:
            app.toast(str(e), theme.RED)
            return
        if not urllib.parse.urlsplit(url).hostname:
            app.toast("Type your RomM's address first", theme.AMBER)
            self.field, self.view = "url", "keys"
            return
        token = self.token_kb.value.strip() if self.field == "token" else None
        if token == "":
            app.toast("Type the token first", theme.AMBER)
            return
        self.busy = "Connecting to %s…" % urllib.parse.urlsplit(url).hostname
        self.cert, self.view = None, "keys"

        def ok(_plats, _version):
            self.busy = None
            if token:
                app.cfg["token"] = token
            server.save_url(app, url, say=False)
            self.step = 2

        def failed(e):
            self.busy = None
            if isinstance(e, rommlib.TlsRejected):
                self.cert, self.view, self.err_sel = e.why, "cert", 0
            elif isinstance(e, rommlib.AuthRejected) and not token:
                server.save_url(app, url, say=False)       # RomM is there: now the code
                self.step = 1
            else:
                app.toast(str(e), theme.RED)
        server.check_server(app, url, ok, failed, token=token)

    def open_advanced(self):
        if self.step == 0 and self.view != "advanced":
            self.back_to, self.view, self.adv_sel = self.view, "advanced", 0

    def open_token(self):
        self.field, self.view = "token", "keys"

    def edit_address(self):
        self.field, self.view, self.cert = "url", "keys", None

    def tls_choice(self, i):
        """Advanced: pick On, Off or the CA bundle (known only once config.json names one)."""
        choices = rommlib.tls_choices(self.app.cfg)
        self.adv_sel = 0
        if i >= len(choices):
            self.app.toast("Put your CA bundle's path in config.json as verify_tls first", theme.AMBER)
            return
        server.set_verify(self.app, choices[i])

    # ---- step 2: the code
    def code_changed(self):
        if len(self.code_kb.value) == rommlib.PAIR_CODE_LEN:
            self.check_code()

    def check_code(self):
        if self.busy:
            return
        app = self.app
        self.busy = "Checking the code…"

        def paired(res):
            self.busy = None
            app.toast("Paired with RomM" + (" (%s)" % res["name"] if res.get("name") else ""), theme.GREEN)
            self.step = 2

        def failed(e):
            self.busy = None
            app.toast(str(e), theme.RED)
            if len(self.code_kb.value) == rommlib.PAIR_CODE_LEN:
                self.code_kb.clear()               # a refused code: start the next one afresh
        server.pair(app, self.code_kb.value, paired, failed)

    # ---- input
    def hints(self):
        if self.step == 2:
            return ([("A", "Try again")] if self.app.error else []) + [("B", "Later")]
        if self.view == "advanced":
            return [("A", "Choose"), ("LEFT RIGHT", "On / Off / CA"), ("B", "Close")]
        if self.view == "cert":
            return [("A", "Select"), ("Y", "Advanced"), ("X", "Edit the address"), ("B", "Later")]
        hs = self.kb.hints()
        if self.step == 0:
            hs = hs[:-1] + [("Y", "Advanced")] + hs[-1:]
            return hs + [("B", "Later" if self.field == "url" else "Back")]
        return hs + [("B", "Back")]

    def action(self, a):
        if self.step == 2:
            if a == "back":
                self.later()
            elif a == "ok" and self.app.error:
                self.app.load_platforms()
            return
        if self.view == "advanced":
            return self.advanced_action(a)
        if self.view == "cert":
            opts = self.cert_options()
            if a in ("up", "down"):
                self.err_sel = (self.err_sel + (1 if a == "down" else -1)) % len(opts)
            elif a == "ok":
                opts[self.err_sel][3]()
            elif a in ("y", "x"):
                next(o for o in opts if o[0].lower() == a)[3]()
            elif a == "back":
                self.later()
            return
        if a == "back":
            if self.step == 1:
                self.step = 0
            elif self.field == "token":
                self.field = "url"
            else:
                self.later()
        elif a == "y" and self.step == 0:
            self.open_advanced()
        else:
            self.kb.action(a)

    def advanced_action(self, a):
        if a in ("up", "down"):
            self.adv_sel = 1 - self.adv_sel
        elif a in ("left", "right") and self.adv_sel == 0:
            server.step_verify(self.app, -1 if a == "left" else 1)
        elif a == "ok" and self.adv_sel == 0:
            server.step_verify(self.app, 1)
        elif a == "ok":
            self.open_token()
        elif a in ("back", "y"):
            self.view = self.back_to if self.cert else "keys"

    def cert_options(self):
        return [("Y", "Advanced", "Turn verification off, or add a CA bundle", self.open_advanced),
                ("A", "Try again", "Connect once more, as it is", self.connect),
                ("X", "Edit the address", "Back to the keyboard", self.edit_address)]

    # ---- drawing
    def draw(self, c):
        if self.step == 2 and self.app.loaded and not self.done:
            self.done = True
            self.app.post(self.finish)
        self.draw_steps(c)
        p = Panel(c, self.f, RIGHT, app=self.app, slot="welcome")
        (self.draw_server, self.draw_pair, self.draw_loading)[self.step](c, p)

    def draw_steps(self, c):
        f = self.f
        text(c, f.h1, "Welcome", (LEFT.left, LEFT.top + 2), theme.TEXT)
        text(c, f.body, "Three steps and your library's here.", (LEFT.left, LEFT.top + 48), theme.DIM,
             maxw=LEFT.width)
        y = LEFT.top + 100
        for i, (label, sub) in enumerate(STEPS):
            r = pygame.Rect(LEFT.left, y, LEFT.width, 92)
            state = "done" if i < self.step else "now" if i == self.step else "next"
            if state == "done" and i == 0:
                if self.step == 1:
                    self.app.hit(r, lambda: setattr(self, "step", 0))
                sub = self.host() or sub
            elif state == "done":
                sub = "Paired"
            card(c, r, state == "now", 14)
            list_row(c, f, r, False, label, sub, mark=lambda ctr, i=i, st=state: step_dot(c, f, ctr, i + 1, st) or True,
                     label_color=theme.TEXT if state == "now" else theme.DIM if state == "done" else theme.FAINT,
                     sub_color=theme.GREEN if state == "done" else theme.DIM if state == "now" else theme.FAINT,
                     slot="welcome-step-%d" % i)
            y += 102
        text(c, f.small, "Settings has all of this later, too.", (LEFT.left, LEFT.bottom - 82), theme.FAINT,
             maxw=LEFT.width)

    def blink(self):
        return int(self.app.t * 2) % 2 == 0

    def draw_server(self, c, p):
        f, token = self.f, self.field == "token"
        if self.view != "cert":
            p.foot(self.busy or "{START} connects when the %s is complete." % ("token" if token else "address"))
        if token:
            p.title("Type an API token", "In RomM: Profile › Client API tokens › New.", lines=1)
        else:
            p.title("Where is your RomM?", "The address you open in a browser.", lines=1)
        cw = button_chip_width(f, "Y", "Advanced")
        fr = pygame.Rect(p.x, p.y, p.w - cw - 12, 56)
        keys = self.view == "keys"
        text_field(c, f, fr, self.kb.value if keys or not token else self.url_kb.value,
                   "rmm_…" if token else "https://romm.example.com", focused=keys, cursor=keys and self.blink(),
                   secret=token and keys)
        chip = pygame.Rect(fr.right + 12, fr.centery - 18, cw, 36)
        self.app.hit(chip, self.open_advanced)
        button_chip(c, f, chip.topleft, "Y", "Advanced", hot=self.view == "advanced")
        area = pygame.Rect(p.x, fr.bottom + 16, p.w, p.bottom - fr.bottom - 16)
        if self.view == "advanced":
            self.draw_advanced(c, area)
        elif self.view == "cert":
            self.draw_cert(c, area)
        else:
            self.kb.draw(c, f, area, True, self.app.hit)

    def draw_advanced(self, c, area):
        f, app = self.f, self.app
        card(c, area, False, 14, bg=theme.BG2)
        x, w, y = area.left + 24, area.width - 48, area.top + 18
        text(c, f.h3, "Verify TLS certificate", (x, y), theme.TEXT if self.adv_sel == 0 else theme.DIM, maxw=w)
        mode = TLS_MODES.index(server.tls_mode(app.cfg["verify_tls"]))
        x1 = x + sum(measure(f.body, lab) + 2 * PILL_PAD for lab in TLS_LABELS) + PILL_GAP * (len(TLS_LABELS) - 1)
        pill_row(c, f.body, list(TLS_LABELS), x, int(x1), y + 34, focus=mode if self.adv_sel == 0 else None,
                 active=mode, app=app, on_tap=self.tls_choice)
        says = server.TLS_SAYS[TLS_MODES[mode]]
        if mode == 2:
            says += " Now: " + os.path.basename(rommlib.norm_verify(app.cfg["verify_tls"]))
        scroll_text(c, f.small, says, (x, y + 86, w, 44), theme.DIM, line_h=21, bg=theme.BG2, slot="welcome-tls")
        hline(c, theme.LINE, (x, y + 140), (x + w, y + 140))
        r = pygame.Rect(area.left + 12, y + 152, area.width - 24, 64)
        app.hit(r, self.open_token)
        list_row(c, f, r, self.adv_sel == 1, "Type an API token instead",
                 "For a RomM that can't pair: Profile › Client API tokens › New", chevron=True,
                 slot="welcome-adv-token")
        text(c, f.small, "Both are in Settings later, too.", (x, area.bottom - 22), theme.FAINT, "midleft", maxw=w)

    def draw_cert(self, c, area):
        f = self.f
        box = notice(c, f, (area.left, area.top, area.width, 128),
                     "Couldn't verify %s's certificate" % (self.host() or "the server"),
                     CERT_SAYS.get(self.cert, CERT_SAYS["other"]), slot="welcome-cert")
        y = box.bottom + 16
        for i, (tok, label, sub, fn) in enumerate(self.cert_options()):
            r = pygame.Rect(area.left, y, area.width, 64)
            self.app.hit(r, fn)
            card(c, r, False, 12, bg=theme.BG2)
            list_row(c, f, r, i == self.err_sel, label, sub, slot="welcome-cert-%d" % i,
                     mark=lambda ctr, t=tok: button_glyph(c, f, t, ctr[0] - 11, ctr[1] - 11) and True)
            y += 74

    def draw_pair(self, c, p):
        f = self.f
        p.foot(self.busy or "{START} checks the code; eight characters check by themselves.")
        p.title("Type the code RomM shows", "In RomM: Profile › Client API tokens › Pair. It's good for a minute.",
                lines=1)
        slots = code_slots(c, f, p.rect.centerx, p.y, self.code_kb.value)
        text(c, f.small, "Letters and digits; the dash goes in by itself.", (p.rect.centerx, slots.bottom + 10),
             theme.FAINT, "midtop", maxw=p.w)
        top = slots.bottom + 46
        kh = (p.bottom - top - 4 * 8) / 5
        kw = min(p.w / self.code_kb.units - 8, kh * 1.2)
        kr = pygame.Rect(0, top, round(self.code_kb.width_for(kw)), p.bottom - top)
        kr.centerx = p.rect.centerx
        self.code_kb.draw(c, f, kr, True, self.app.hit)

    def draw_loading(self, c, p):
        if self.app.error:
            p.foot("{A} tries again · {B} goes to Home")
            p.title("Pick a platform", "Your RomM answered; the library didn't load.", lines=1)
            notice(c, self.f, (p.x, p.y, p.w, 120), "Couldn't load your library", self.app.error,
                   slot="welcome-load")
        else:
            p.title("Pick a platform", "Your library is loading…", lines=1)
            p.prose("The platform list opens by itself as soon as RomM sends it. Pick one to browse its games, "
                    "or press {Y} there to search it.")
