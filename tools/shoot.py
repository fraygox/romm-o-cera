#!/usr/bin/env python3
"""Headless screenshot driver for RomM Browser (no box, no display needed).

  ROMM_URL=http://127.0.0.1:8765 ROMM_TOKEN=demo \\
  ROMM_BROWSER_DATA=/tmp/rb/data ROMM_BROWSER_ROMS=/tmp/rb/roms [ROMM_BROWSER_SCALE=3] \\
    python tools/shoot.py OUT_DIR "wait:3,shot:home,act:down,act:ok,type:zelda,..."

  steps: wait:<s> | act:<up|down|left|right|ok|back|x|y|l1|r1|start|select>
         | type:<text> (into the focused search/edit field) | shot:<name>
  Pair with tools/mock_romm.py for a fake library.

  SHOOT_SETTLE=1: a wait:<s> (and the 0.12 s after each act) ends early once
  the app has settled (tools/settle.py: no background load in flight or
  queued, no search pending, downloads quiet) for 3 frames; <s> stays the
  upper bound. SHOOT_TRACE=<file>: log the app's state before every step.
"""
import os, sys, time, importlib.machinery
# dummy by default; Batocera's SDL has no dummy/offscreen driver, so there pass SDL_VIDEODRIVER=x11 DISPLAY=:0
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
import pygame
rb = importlib.machinery.SourceFileLoader("rb", APP + "/rommbrowser.pygame").load_module()
OUT = sys.argv[1]
# script: list of ("wait", secs) | ("act", name) | ("shot", name)
steps = []
for tok in sys.argv[2].split(","):
    k, _, v = tok.partition(":")
    steps.append((k, v))
pygame.init()
app = rb.App(None, headless=True)
state = {"i": 0, "until": 0, "calm": 0}
SETTLE_FRAMES = 3
settled = None
if os.environ.get("SHOOT_SETTLE") == "1":
    import settle  # tools/settle.py
    settled = settle.install(app, downloads=True)
trace = open(os.environ["SHOOT_TRACE"], "w") if os.environ.get("SHOOT_TRACE") else None
def snapshot(app):
    def nav(o):
        if o is None:
            return None
        items = getattr(o, "items", None)
        return (type(o).__name__, [getattr(o, k, None) for k in ("sel", "scroll", "r", "c", "region_i", "query")],
                len(items) if isinstance(items, (list, tuple)) else None)
    return repr(([type(s).__name__ for s in app.stack], nav(app.stack[-1]), nav(app.modal),
                 [(j.label, j.state) for j in app.downloader.jobs]))
def scripted(app):
    out = []
    now = time.time()
    if settled is not None:
        state["calm"] = state["calm"] + 1 if settled() else 0
    if now < state["until"] and state["calm"] < SETTLE_FRAMES:
        return out
    state["calm"] = 0
    while state["i"] < len(steps):
        k, v = steps[state["i"]]; state["i"] += 1
        if trace:
            trace.write("%d %s:%s %s\n" % (state["i"] - 1, k, v, snapshot(app)))
        if k == "wait":
            state["until"] = now + float(v); return out
        if k == "act":
            out.append(v); state["until"] = now + 0.12; return out
        if k == "type":
            scr = app.stack[-1]
            if hasattr(scr, "query"):
                scr.query += v
                scr.pending_at = now
            elif hasattr(scr, "value"):
                scr.value += v
            continue
        if k == "shot":
            app.draw(); pygame.image.save(app.canvas, os.path.join(OUT, v + ".png")); continue
    app.running = False
    return out
app.run(scripted)
