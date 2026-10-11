#!/usr/bin/env python3
"""Headless screenshot driver for RomM Browser (no box, no display needed).

  ROMM_URL=http://127.0.0.1:8765 ROMM_TOKEN=demo \\
  ROMM_BROWSER_DATA=/tmp/rb/data ROMM_BROWSER_ROMS=/tmp/rb/roms [ROMM_BROWSER_SCALE=3] \\
    python tools/shoot.py OUT_DIR "wait:3,shot:home,act:down,act:ok,type:zelda,..."

  steps: wait:<s> | act:<up|down|left|right|ok|back|x|y|l1|r1|start|select>
         | type:<text> (into the focused search/edit field) | shot:<name>
  Pair with tools/mock_romm.py for a fake library.
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
state = {"i": 0, "until": 0}
def scripted(app):
    out = []
    now = time.time()
    if now < state["until"]:
        return out
    while state["i"] < len(steps):
        k, v = steps[state["i"]]; state["i"] += 1
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
