"""Keyboard, game controllers and raw joysticks -> actions."""
import time

import pygame
from rbui import pads

try:
    from pygame._sdl2 import controller as sdlcontroller
except Exception:  # pragma: no cover - older pygame
    sdlcontroller = None


# -------------------------------------------------------------------- input

class Input:
    """Maps keyboard, SDL game controllers and raw joysticks to actions.
    Game controllers use SDL's standard layout, so a DualSense and an 8BitDo
    behave the same. Batocera's ES confirms with the south button; swap_ab in
    config.json flips that."""
    REPEAT_DELAY, REPEAT_RATE = 0.32, 0.055

    NAV_KEYS = None   # filled lazily (pygame constants need pygame imported)

    def __init__(self, swap_ab):
        self.set_swap(swap_ab)
        self.last = {}          # action -> (time, "pad"|"key"): drops evmapy's key echoes of pad presses
        self.pad_t = 0.0        # last pad activity of any kind
        self.pending = []       # keyboard actions held back briefly: an echo can arrive before its pad event
        self.text_mode = False  # a text field has focus: letters type instead of acting
        self.controllers = {}
        self.joysticks = {}
        self.held = {}
        self.key_action = {}    # pygame key -> the action its press queued
        self.key_up = {}        # action -> when its key was last let go
        self.axis_dir = {}
        self.raw_triggers = {}  # raw joystick instance id -> its [L2, R2] trigger axes
        if sdlcontroller is not None:
            sdlcontroller.init()
        for i in range(pygame.joystick.get_count()):
            self._open(i)

    def set_swap(self, swap_ab):
        self.ok_btn, self.back_btn = ("east", "south") if swap_ab else ("south", "east")
        pads.PAD_STYLE["ok"], pads.PAD_STYLE["back"] = self.ok_btn, self.back_btn

    def names(self):
        out = []
        for j in list(self.joysticks.values()):
            try:
                out.append(j.get_name())
            except Exception:
                pass
        for jid in self.controllers:
            for i in range(pygame.joystick.get_count()):
                try:
                    js = pygame.joystick.Joystick(i)
                    if js.get_instance_id() == jid:
                        out.append(js.get_name())
                except Exception:
                    pass
        return out

    def _open(self, index):
        try:
            jid = pygame.joystick.Joystick(index).get_instance_id()
            if jid in self.controllers or jid in self.joysticks:
                return                            # both "added" events fire for one pad
        except Exception:
            pass
        try:
            if sdlcontroller is not None and sdlcontroller.is_controller(index):
                c = sdlcontroller.Controller(index)
                jid = pygame.joystick.Joystick(index).get_instance_id()
                self.controllers[jid] = c
                return
        except Exception:
            pass
        try:
            j = pygame.joystick.Joystick(index)
            j.init()
            self.joysticks[j.get_instance_id()] = j
            # triggers on a raw joystick are the axes past the left stick that rest
            # all the way back (-1), first L2 then R2; a right stick rests at 0
            self.raw_triggers[j.get_instance_id()] = \
                [a for a in range(2, j.get_numaxes()) if j.get_axis(a) < -0.5][:2]
        except Exception:
            pass

    def _face(self, name):
        if name == self.ok_btn:
            return "ok"
        if name == self.back_btn:
            return "back"
        return {"west": "x", "north": "y"}.get(name)

    ECHO = 0.3
    KEY_HOLD = 0.06             # keyboard actions wait this long for a pad event they'd be the echo of

    def _key(self, action, out):
        """Queue a keyboard action; flush() commits it unless pad activity
        happened within ECHO of it (Batocera's evmapy echoes pad presses as
        keys; which one arrives first varies)."""
        now = time.time()
        if now - self.pad_t < self.ECHO:
            return
        self.pending.append((now, action))

    def flush(self, out):
        now = time.time()
        keep = []
        for t, action in self.pending:
            if abs(self.pad_t - t) < self.ECHO:
                continue                  # a pad event turned up: it was an echo
            if now - t < self.KEY_HOLD:
                keep.append((t, action))
            elif action.startswith("text:"):
                pads.INPUT_MODE["kb"] = True
                out.append(action)
            else:
                # a tap let go before it was committed moves once: nothing is held to repeat
                self._press(action, out, "key", hold=self.key_up.get(action, 0) < t)
        self.pending = keep

    def _press(self, action, out, src="pad", hold=True):
        now = time.time()
        t, other = self.last.get(action, (0, src))
        if other != src and now - t < self.ECHO:
            return                    # the same press arriving twice (pad + its keyboard echo)
        self.last[action] = (now, src)
        pads.INPUT_MODE["kb"] = src == "key"
        if hold and action in ("up", "down", "left", "right", "l1", "r1", "l2", "r2"):
            self.held[action] = now + self.REPEAT_DELAY
        out.append(action)

    def _release(self, action):
        self.held.pop(action, None)

    def handle(self, ev, out):
        t = ev.type
        if t in (pygame.CONTROLLERBUTTONDOWN, pygame.CONTROLLERBUTTONUP, pygame.JOYBUTTONDOWN,
                 pygame.JOYBUTTONUP, pygame.JOYHATMOTION) or \
                (t in (pygame.CONTROLLERAXISMOTION, pygame.JOYAXISMOTION) and abs(ev.value) > 0.5 *
                 (32767 if t == pygame.CONTROLLERAXISMOTION else 1)):
            self.pad_t = time.time()
        if t == pygame.TEXTINPUT:
            if self.text_mode and ev.text and ev.text.isprintable():
                self._key("text:" + ev.text, out)
            return
        if t == pygame.KEYDOWN:
            if ev.key == pygame.K_F4 and ev.mod & pygame.KMOD_ALT:
                out.append("quit")
                return
            if self.text_mode:
                km = {pygame.K_UP: "up", pygame.K_DOWN: "down", pygame.K_LEFT: "left",
                      pygame.K_RIGHT: "right", pygame.K_RETURN: "start", pygame.K_KP_ENTER: "start",
                      pygame.K_ESCAPE: "back", pygame.K_BACKSPACE: "x", pygame.K_TAB: "select"}
            else:
                km = {pygame.K_UP: "up", pygame.K_DOWN: "down", pygame.K_LEFT: "left",
                      pygame.K_RIGHT: "right", pygame.K_RETURN: "ok", pygame.K_KP_ENTER: "ok",
                      pygame.K_SPACE: "ok", pygame.K_ESCAPE: "back", pygame.K_BACKSPACE: "back",
                      pygame.K_x: "x", pygame.K_y: "y", pygame.K_PAGEUP: "l1", pygame.K_PAGEDOWN: "r1",
                      pygame.K_TAB: "select", pygame.K_HOME: "start",
                      pygame.K_LEFTBRACKET: "l2", pygame.K_RIGHTBRACKET: "r2"}
            if ev.key in km:
                self.key_action[ev.key] = km[ev.key]
                self._key(km[ev.key], out)
        elif t == pygame.KEYUP:
            a = self.key_action.pop(ev.key, None)
            if a:
                self.key_up[a] = time.time()
            for a in ("up", "down", "left", "right", "l1", "r1", "l2", "r2"):
                if self.last.get(a, (0, ""))[1] == "key":
                    self._release(a)
        elif sdlcontroller is not None and t == pygame.CONTROLLERDEVICEADDED:
            self._open(ev.device_index)
            out.append("padchange")
        elif t == pygame.JOYDEVICEADDED:
            # plain joysticks (no SDL mapping) only announce themselves here
            self._open(ev.device_index)
            out.append("padchange")
        elif t in (pygame.JOYDEVICEREMOVED, getattr(pygame, "CONTROLLERDEVICEREMOVED", -1)):
            iid = getattr(ev, "instance_id", None)
            self.controllers.pop(iid, None)
            self.joysticks.pop(iid, None)
            out.append("padchange")
        elif t in (pygame.CONTROLLERBUTTONDOWN, pygame.CONTROLLERBUTTONUP):
            m = {pygame.CONTROLLER_BUTTON_A: "south", pygame.CONTROLLER_BUTTON_B: "east",
                 pygame.CONTROLLER_BUTTON_X: "west", pygame.CONTROLLER_BUTTON_Y: "north",
                 pygame.CONTROLLER_BUTTON_DPAD_UP: "up",
                 pygame.CONTROLLER_BUTTON_DPAD_DOWN: "down",
                 pygame.CONTROLLER_BUTTON_DPAD_LEFT: "left",
                 pygame.CONTROLLER_BUTTON_DPAD_RIGHT: "right",
                 pygame.CONTROLLER_BUTTON_LEFTSHOULDER: "l1",
                 pygame.CONTROLLER_BUTTON_RIGHTSHOULDER: "r1",
                 pygame.CONTROLLER_BUTTON_START: "start",
                 pygame.CONTROLLER_BUTTON_BACK: "select",
                 pygame.CONTROLLER_BUTTON_GUIDE: "guide"}
            name = m.get(ev.button)
            if name in ("south", "east", "west", "north"):
                name = self._face(name)
            if name:
                if t == pygame.CONTROLLERBUTTONDOWN:
                    self._press(name, out)
                else:
                    self._release(name)
        elif t == pygame.CONTROLLERAXISMOTION:
            if ev.axis in (pygame.CONTROLLER_AXIS_LEFTX, pygame.CONTROLLER_AXIS_LEFTY):
                self._axis(("c", ev.instance_id, ev.axis), ev.axis == pygame.CONTROLLER_AXIS_LEFTX,
                           ev.value / 32767.0, out)
            elif ev.axis in (pygame.CONTROLLER_AXIS_TRIGGERLEFT, pygame.CONTROLLER_AXIS_TRIGGERRIGHT):
                key = ("t", ev.instance_id, ev.axis)
                on = ev.value > 16000
                trig = "l2" if ev.axis == pygame.CONTROLLER_AXIS_TRIGGERLEFT else "r2"
                if on and not self.axis_dir.get(key):
                    self._press(trig, out)
                elif not on and self.axis_dir.get(key):
                    self._release(trig)
                self.axis_dir[key] = on
        # raw joystick fallback (only for devices SDL has no mapping for)
        elif t in (pygame.JOYBUTTONDOWN, pygame.JOYBUTTONUP):
            if ev.instance_id in self.controllers:
                return
            m = {0: "south", 1: "east", 2: "west", 3: "north", 4: "l1", 5: "r1",
                 6: "select", 7: "start", 8: "select", 9: "start"}
            j = self.joysticks.get(ev.instance_id)
            try:
                if j is not None and j.get_numbuttons() >= 10:
                    m.update({6: "l2", 7: "r2"})      # 10+ buttons: L2 R2 then Select Start (generic pads)
            except Exception:
                pass
            name = m.get(ev.button)
            if name in ("south", "east", "west", "north"):
                name = self._face(name)
            if name:
                if t == pygame.JOYBUTTONDOWN:
                    self._press(name, out)
                else:
                    self._release(name)
        elif t == pygame.JOYHATMOTION:
            if ev.instance_id in self.controllers:
                return
            x, y = ev.value
            for a in ("up", "down", "left", "right"):
                self._release(a)
            if y == 1:
                self._press("up", out)
            elif y == -1:
                self._press("down", out)
            if x == -1:
                self._press("left", out)
            elif x == 1:
                self._press("right", out)
        elif t == pygame.JOYAXISMOTION:
            if ev.instance_id in self.controllers:
                return
            trig = self.raw_triggers.get(ev.instance_id, [])
            if ev.axis in trig:
                key, on = ("jt", ev.instance_id, ev.axis), ev.value > 0
                name = ("l2", "r2")[trig.index(ev.axis)]
                if on and not self.axis_dir.get(key):
                    self._press(name, out)
                elif not on and self.axis_dir.get(key):
                    self._release(name)
                self.axis_dir[key] = on
            elif ev.axis <= 1:
                self._axis(("j", ev.instance_id, ev.axis), ev.axis == 0, ev.value, out)

    def _axis(self, key, horizontal, v, out):
        d = None
        if v < -0.6:
            d = "left" if horizontal else "up"
        elif v > 0.6:
            d = "right" if horizontal else "down"
        prev = self.axis_dir.get(key)
        if d == prev:
            return
        if prev:
            self._release(prev)
        if d:
            self._press(d, out)
        self.axis_dir[key] = d

    def repeats(self, out):
        self.flush(out)
        now = time.time()
        for a, due in list(self.held.items()):
            if now >= due:
                out.append(a)
                self.held[a] = now + self.REPEAT_RATE
