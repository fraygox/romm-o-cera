#!/usr/bin/env python3
"""Queue existing library disc games on the box for background CHD.

  rb_chd_plan.py [--systems psx,dreamcast,...] [--only SUBSTR] [--limit N]
                 [--exclude SUBSTR]... [--dry-run]

Finds cue/gdi (CD systems) and iso (PS2) games that have no CHD yet, groups
discs into games (a folder holding the disc files, with "(Disc N)" subfolders
folded into their parent), and writes chd-queue tasks in the given system
order. Then start rb_chd.py (it waits for ES to be idle by itself).
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rommlib  # noqa: E402
import rb_chd  # noqa: E402

ORDER = ["psx", "dreamcast", "ps2", "neogeocd", "saturn", "megacd", "pcenginecd",
         "3do", "amigacd32", "amigacdtv", "cdi", "pcfx"]
SKIP_DIRS = {"images", "videos", "manuals", "media", "downloaded_images", "downloaded_videos"}
DISC_DIR = re.compile(r"\(Disc \d+\)", re.I)


def games_for(system):
    sysdir = os.path.join(rommlib.ROMS_ROOT, system)
    dvd = system in rommlib.CHD_DVD_SYSTEMS
    found = []
    for dp, dns, fns in os.walk(sysdir):
        dns[:] = [d for d in dns if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in fns:
            low = fn.lower()
            # PS2 ships both DVD isos and CD cue/bin games; CD systems: cue/gdi only
            if low.endswith((".cue", ".gdi")) or (dvd and low.endswith(".iso")):
                found.append(os.path.join(dp, fn))
    # a .cue next to a .gdi of the same folder (some DC dumps ship both): keep the gdi
    by_dir = {}
    for p in found:
        by_dir.setdefault(os.path.dirname(p), []).append(p)
    games = {}
    for d, files in by_dir.items():
        if any(f.lower().endswith(".gdi") for f in files):
            files = [f for f in files if f.lower().endswith(".gdi")]
        if os.path.normpath(d) == os.path.normpath(sysdir):
            for f in files:                       # loose files: one game each
                games[f] = [f]
            continue
        root = d
        if DISC_DIR.search(os.path.basename(d)) and \
                os.path.normpath(os.path.dirname(d)) != os.path.normpath(sysdir):
            root = os.path.dirname(d)             # Game/Game (Disc 2)/x.cue -> Game/
        # a folder with many unrelated games (e.g. "TOP 100 …" holding loose
        # discs) must not become one multi-disc game: only group when the
        # discs look like one title's discs
        games.setdefault(root, []).extend(files)
    out = []
    for root, files in games.items():
        files = sorted(set(files))
        if len(files) > 1 and not all(DISC_DIR.search(f) for f in files):
            for f in files:                      # unrelated games sharing a folder
                out.append((f, [f]))
        else:
            out.append((root, files))
    return sorted(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--systems", default=",".join(ORDER))
    ap.add_argument("--only")
    ap.add_argument("--exclude", action="append", default=[])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    os.makedirs(rommlib.CHD_QUEUE_DIR, exist_ok=True)
    queued = set()
    for t in os.listdir(rommlib.CHD_QUEUE_DIR):
        try:
            queued.update(json.load(open(os.path.join(rommlib.CHD_QUEUE_DIR, t)))["sources"])
        except (OSError, ValueError, KeyError):
            pass
    n = 0
    tot = 0
    seq = int(__import__("time").time())
    for si, system in enumerate(a.systems.split(",")):
        if not os.path.isdir(os.path.join(rommlib.ROMS_ROOT, system)):
            continue
        for root, files in games_for(system):
            mode = "createdvd" if files[0].lower().endswith(".iso") else "createcd"
            if a.only and a.only.lower() not in root.lower():
                continue
            if any(x.lower() in root.lower() for x in a.exclude):
                continue
            if any(f in queued for f in files):
                continue
            size = sum(os.path.getsize(p) for f in files for p in rb_chd.sources_of(f)
                       if os.path.isfile(p))
            rel = os.path.relpath(root, rommlib.ROMS_ROOT)
            print("%-9s %6.2f GB  %d disc%s  %s" % (system, size / 1e9, len(files),
                                                   "" if len(files) == 1 else "s", rel))
            tot += size
            if not a.dry_run:
                task = {"system": system, "mode": mode, "sources": files,
                        "roots": [root], "queued": seq}
                k = n
                while True:   # never clobber a task from an earlier run
                    name = "%d-%02d-%05d.json" % (seq, si, k)
                    path = os.path.join(rommlib.CHD_QUEUE_DIR, name)
                    try:
                        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
                        break
                    except FileExistsError:
                        k += 1
                with os.fdopen(fd, "w") as f:
                    json.dump(task, f)
            n += 1
            if a.limit and n >= a.limit:
                break
        if a.limit and n >= a.limit:
            break
    print("%s %d games, %.1f GB" % ("would queue" if a.dry_run else "queued", n, tot / 1e9))


if __name__ == "__main__":
    main()
