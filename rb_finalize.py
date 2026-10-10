#!/usr/bin/env python3
"""Merge RomM Browser's pending gamelist entries into ES once ES is idle.

Spawned detached by the browser on exit (and by the CHD worker after a
conversion). Waits until no game is running — never touches ES while one is —
then: /reloadgames (ES flushes its dirty in-memory lists to disk first), merge
our entries / path renames into roms/<sys>/gamelist.xml, /reloadgames again so
ES re-parses them. Without the first reload ES's own flush clobbers our write
(observed on Batocera 43).
"""
import fcntl
import json
import os
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rommlib  # noqa: E402

ES = os.environ.get("ROMM_BROWSER_ES", "http://127.0.0.1:1234")
LOCK = os.path.join(rommlib.DATA_DIR, "finalize.lock")


def es_get(path, timeout=5):
    with urllib.request.urlopen(ES + path, timeout=timeout) as r:
        return r.status, r.read()


def game_running():
    """True when ES reports a running game (or ES is unreachable)."""
    try:
        status, body = es_get("/runningGame")
    except Exception:
        return True
    if status != 200 or not body.strip():
        return False
    try:
        return bool(json.loads(body).get("path"))
    except ValueError:
        return False


def wait_idle(max_wait=6 * 3600):
    t0 = time.time()
    idle_since = None
    while time.time() - t0 < max_wait:
        if game_running():
            idle_since = None
        elif idle_since is None:
            idle_since = time.time()
        elif time.time() - idle_since >= 5:   # ES settled back in the menu
            return True
        time.sleep(2)
    return False


def rel(sysdir, path):
    return "./" + os.path.relpath(path, sysdir)


PRIMARY_ORDER = [".m3u", ".chd", ".gdi", ".cue", ".cdi", ".iso", ".rvz"]


def primary_path(system, paths, es):
    """The one file ES lists for this rom."""
    exts = es.get(system)
    cands = []
    for p in paths:
        if os.path.isdir(p):
            for dp, _, fns in os.walk(p):
                cands += [os.path.join(dp, f) for f in fns]
        elif os.path.exists(p):
            cands.append(p)
    if exts is not None:
        cands = [c for c in cands if os.path.splitext(c)[1].lower() in exts]
    if not cands:
        return None

    def key(c):
        e = os.path.splitext(c)[1].lower()
        return (PRIMARY_ORDER.index(e) if e in PRIMARY_ORDER else 99, len(c))
    return sorted(cands, key=key)[0]


def fetch_cover(api, url_path, dest):
    if not url_path or not url_path.startswith("/") or url_path.startswith("//"):
        return os.path.exists(dest)       # only server-relative paths (token-bearing session)
    if os.path.exists(dest):
        return True
    try:
        r = api.s.get(api.base + url_path, timeout=20)
        if r.status_code == 200 and r.content:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            rommlib.write_png(r.content, dest)
            return True
    except Exception:
        pass
    return False


def set_text(game, tag, value):
    if value in (None, "", []):
        return
    el = game.find(tag)
    if el is None:
        el = ET.SubElement(game, tag)
    el.text = str(value)


def load_gamelist(path):
    if os.path.exists(path):
        try:
            return ET.parse(path)
        except ET.ParseError:
            os.replace(path, path + ".broken-%d" % time.time())
    return ET.ElementTree(ET.Element("gameList"))


def merge(entries, api, es):
    # A CHD conversion may have finished before the download's own entry was
    # merged: route those entries' paths through the batch's renames.
    renames = {}
    for e in entries:
        renames.update(e.get("rename") or {})
    for e in entries:
        if "paths" in e:
            e["paths"] = [renames.get(p, p) for p in e["paths"]]
    by_sys = {}
    for e in entries:
        by_sys.setdefault(e["system"], []).append(e)
    for system, items in by_sys.items():
        sysdir = os.path.join(rommlib.ROMS_ROOT, system)
        gl_path = os.path.join(sysdir, "gamelist.xml")
        tree = load_gamelist(gl_path)
        root = tree.getroot()
        index = {g.findtext("path"): g for g in root.findall("game")}
        for e in items:
            if "remove" in e:          # uninstalled from the box
                for old in e["remove"]:
                    r = rel(sysdir, old)
                    for p, g in list(index.items()):
                        if p and (p == r or p.startswith(r.rstrip("/") + "/")):
                            root.remove(g)
                            index.pop(p)
                continue
            if "rename" in e:          # CHD worker: old path(s) -> new path
                for old, new in e["rename"].items():
                    old_rel, new_rel = rel(sysdir, old), rel(sysdir, new)
                    for p, g in list(index.items()):
                        if p and (p == old_rel or p.startswith(old_rel.rstrip("/") + "/")):
                            index.pop(p)
                            keep = index.get(new_rel)
                            if keep is not None and keep is not g:
                                # e.g. ES had both the .cue and its .bin listed:
                                # one entry per file, keep the richer one
                                if len(list(g)) > len(list(keep)):
                                    keep, g = g, keep
                                root.remove(g)
                                g = keep
                            g.find("path").text = new_rel
                            index[new_rel] = g
                for h in e.get("hide", []):
                    hr = rel(sysdir, h)
                    g = index.get(hr)
                    if g is None:          # (an Element's truthiness is its child count)
                        g = ET.SubElement(root, "game")
                    set_text(g, "path", hr)
                    set_text(g, "hidden", "true")
                    index[hr] = g
                continue
            prim = primary_path(system, e["paths"], es)
            if not prim:
                continue
            p = rel(sysdir, prim)
            g = index.get(p)
            if g is None:
                g = ET.SubElement(root, "game")
                set_text(g, "path", p)
                index[p] = g
            set_text(g, "name", e.get("name"))
            set_text(g, "desc", e.get("desc"))
            ms = e.get("releasedate")
            if ms:
                set_text(g, "releasedate", time.strftime("%Y%m%dT000000", time.gmtime(ms / 1000)))
            set_text(g, "genre", ", ".join(e.get("genres") or []))
            set_text(g, "developer", e.get("developer"))
            set_text(g, "publisher", e.get("publisher"))
            set_text(g, "players", e.get("players"))
            if e.get("rating") is not None:
                set_text(g, "rating", "%.2f" % (float(e["rating"]) / 100))
            stem = os.path.splitext(os.path.basename(prim))[0]
            img = os.path.join(sysdir, "images", stem + "-image.png")
            if e.get("image_local") and os.path.exists(e["image_local"]):
                os.makedirs(os.path.dirname(img), exist_ok=True)
                with open(e["image_local"], "rb") as src, open(img, "wb") as dst:
                    dst.write(src.read())
                set_text(g, "image", rel(sysdir, img))
            elif g.find("image") is None and fetch_cover(api, e.get("cover"), img):
                set_text(g, "image", rel(sysdir, img))
        if os.path.exists(gl_path):
            bak = gl_path + ".bak-rommbrowser"
            if not os.path.exists(bak):
                with open(gl_path, "rb") as src, open(bak, "wb") as dst:
                    dst.write(src.read())
        tmp = gl_path + ".tmp"
        tree.write(tmp, encoding="utf-8", xml_declaration=True)
        os.replace(tmp, gl_path)


def main():
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    lockf = open(LOCK, "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return  # another finalize is already waiting; it will pick ours up
    while True:
        if not os.path.exists(rommlib.GAMELIST_QUEUE) or \
                os.path.getsize(rommlib.GAMELIST_QUEUE) == 0:
            return
        if not wait_idle():
            rommlib.log("finalize: gave up waiting for ES to go idle")
            return
        # claim the queue atomically; anything appended later is a new batch
        work = rommlib.GAMELIST_QUEUE + ".work"
        if os.path.exists(work):
            # left by an interrupted run: keep it, in front of the new batch
            with open(work) as f:
                old = f.read()
            with open(rommlib.GAMELIST_QUEUE) as f:
                new = f.read()
            with open(work, "w") as f:
                f.write(old + new)
            os.remove(rommlib.GAMELIST_QUEUE)
        else:
            os.replace(rommlib.GAMELIST_QUEUE, work)
        with open(work) as f:
            entries = [json.loads(l) for l in f if l.strip()]
        cfg = rommlib.load_config()
        api = rommlib.Api.from_config(cfg)
        try:
            if game_running():      # re-check right before touching ES
                raise RuntimeError("game started")
            es_get("/reloadgames", timeout=120)   # flush ES's dirty lists
            time.sleep(3)
            merge(entries, api, rommlib.load_es_systems())
            if not game_running():
                es_get("/reloadgames", timeout=120)
            os.remove(work)
            rommlib.log("finalize: merged %d entries" % len(entries))
        except Exception as e:
            # put the batch back in front of anything new and retry later
            rommlib.log("finalize: deferred (%r)" % e)
            with open(work) as f:
                old = f.read()
            newer = ""
            if os.path.exists(rommlib.GAMELIST_QUEUE):
                with open(rommlib.GAMELIST_QUEUE) as f:
                    newer = f.read()
            with open(rommlib.GAMELIST_QUEUE, "w") as f:
                f.write(old + newer)
            os.remove(work)
            time.sleep(30)


if __name__ == "__main__":
    main()
