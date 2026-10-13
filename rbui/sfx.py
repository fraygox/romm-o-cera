"""The UI's sounds."""
import pygame
import rommlib


class Sfx:
    """Small UI sounds, synthesised at start-up (no audio files): soft sine
    blips with a fast attack and exponential decay. Every failure is silent —
    a box without audio just stays quiet."""
    # name: (notes in Hz, ms per note, peak amplitude, wave)
    SPEC = {
        "move": ((1046,), 26, 0.09, "sine"),
        "page": ((784, 988), 22, 0.10, "sine"),
        "ok": ((784, 1175), 42, 0.14, "sine"),
        "back": ((1175, 784), 42, 0.12, "sine"),
        "toggle": ((988,), 40, 0.12, "sine"),
        "deny": ((196, 185), 70, 0.14, "triangle"),
        "queued": ((659, 880), 55, 0.13, "sine"),
        "done": ((523, 659, 784, 1046), 70, 0.12, "sine"),
        "reveal": ((659, 784, 988, 1319), 34, 0.12, "sine"),
    }
    LEVEL = {"normal": 1.0, "quiet": 0.4, "off": 0.0}

    def __init__(self, level="normal"):
        self.sounds, self.channels = {}, {}
        self.level = self.LEVEL.get(level, 1.0)
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(44100, -16, 2, 512)
            pygame.mixer.set_num_channels(max(8, len(self.SPEC)))
            for i, (name, spec) in enumerate(self.SPEC.items()):
                self.sounds[name] = pygame.mixer.Sound(file=self._wav(*spec))
                self.channels[name] = pygame.mixer.Channel(i)
        except Exception as e:                 # no audio device, no mixer: stay silent
            rommlib.log("sound off: %s" % e.__class__.__name__)
            self.sounds = {}

    @staticmethod
    def _wav(notes, ms, amp, wave_kind, rate=44100):
        import io
        import math
        import struct
        import wave
        n = int(rate * ms / 1000)
        out = []
        for k, f in enumerate(notes):
            for i in range(n):
                t = i / rate
                ph = (f * t) % 1.0
                v = math.sin(2 * math.pi * ph) if wave_kind == "sine" else (4 * abs(ph - 0.5) - 1)
                env = min(1.0, i / (rate * 0.002)) * math.exp(-5.0 * i / n)   # 2 ms attack, decay
                out.append(int(32767 * amp * env * v))
        tail = int(rate * 0.01)                 # 10 ms of silence: no click at the end
        out += [0] * tail
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(struct.pack("<%dh" % len(out), *out))
        buf.seek(0)
        return buf

    def set_level(self, level):
        self.level = self.LEVEL.get(level, 1.0)

    def play(self, name):
        snd = self.sounds.get(name)
        if not snd or not self.level:
            return
        try:
            snd.set_volume(self.level)
            self.channels[name].play(snd)        # one channel per sound: repeats restart, never pile up
        except Exception:
            pass


SOUND_FOR = {"up": "move", "down": "move", "left": "move", "right": "move",
             "l1": "page", "r1": "page", "l2": "page", "r2": "page",
             "ok": "ok", "x": "ok", "y": "ok", "start": "ok", "select": "ok", "back": "back"}
