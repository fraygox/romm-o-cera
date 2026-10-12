#!/usr/bin/env python3
"""Queue existing library disc games on the box for background CHD.

  rb_chd_plan.py [--systems psx,dreamcast,...] [--only SUBSTR] [--limit N]
                 [--exclude SUBSTR]... [--dry-run]

Xbox (Settings -> Make Xbox images bootable, on by default): a full redump
image (xemu can't boot it) is cut to its game partition right away and
"X.xiso" is renamed "X.iso" (ES lists .iso); with Xbox to squashfs on,
images are then queued for packing. 3DS (Settings -> 3DS to .zcci): decrypted
.3ds/.cci/.cxi are queued for Z3DS; encrypted dumps are listed and skipped.
Xbox 360 (Settings -> Xbox 360 to .zar, x86_64): .iso images are queued for .zar.

Finds cue/gdi (CD systems), iso (PS2/PSP) and cso (PSP) games that have no CHD yet, groups
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

# the order games are queued in (and --systems' default): each converter's
# priority, from the converter table (tests/test_logic.py pins it)
ORDER = rommlib.planner_order()
DISC_DIR = re.compile(r"\(Disc \d+\)", re.I)


def games_for(system):
    sysdir = os.path.join(rommlib.ROMS_ROOT, system)
    convs = rommlib.converters_for(system)
    if convs and not any(c.chd for c in convs):
        exts = tuple(e for c in convs for e in c.sources + c.renamed_sources)    # every image on its own
        games = tuple(o for c in convs for o in c.outputs)     # a finished game folder (X.ps3): never inside
        out = []
        for dp, dns, fns in os.walk(sysdir):
            dns[:] = [d for d in dns if d not in rommlib.MEDIA_DIRS and not d.startswith(".")
                      and not d.lower().endswith(games)]
            out += [(os.path.join(dp, f), [os.path.join(dp, f)], None) for f in fns
                    if f.lower().endswith(exts)]
        return sorted(out, key=lambda x: x[0])
    # CHD: PS2/PSP ship both DVD isos and CD cue/bin games; CD systems (and a
    # system the table doesn't know, given with --systems): cue/gdi only
    exts = tuple(e for c in convs for e in c.sources) or rommlib.CONVERTER["createcd"].sources
    found = []
    for dp, dns, fns in os.walk(sysdir):
        dns[:] = [d for d in dns if d not in rommlib.MEDIA_DIRS and not d.startswith(".")]
        found += [os.path.join(dp, fn) for fn in fns if fn.lower().endswith(exts)]
    # a .cue next to a .gdi of the same folder (some DC dumps ship both): keep the gdi
    by_dir = {}
    for p in found:
        by_dir.setdefault(os.path.dirname(p), []).append(p)
    games, loose = {}, []
    for d, files in by_dir.items():
        if any(f.lower().endswith(".gdi") for f in files):
            files = [f for f in files if f.lower().endswith(".gdi")]
        if os.path.normpath(d) == os.path.normpath(sysdir):
            for f in files:                       # loose files: one game each
                loose.append((f, [f]))
            continue
        base = rommlib.disc_info(os.path.basename(d))
        if base and os.path.normpath(os.path.dirname(d)) != os.path.normpath(sysdir):
            # Game/Game (Disc 2)/x.cue -> Game/, but only together with discs
            # of the same title (a "TOP 100" folder can hold many games' discs)
            key = (os.path.dirname(d), base[0].lower())
        else:
            key = (d, None)
        games.setdefault(key, []).append((d, files, base[0] if base else None))
    out = [(r, fs, None) for r, fs in loose]
    by_root = {}
    for (root, title), parts in games.items():
        by_root.setdefault(root, []).append((title, parts))
    for root, groups in by_root.items():
        if len(groups) > 1:
            # several titles' disc folders in one folder (e.g. a game folder
            # holding the original and the Dual Shock release): one set per
            # title, its playlist named after that title — never one set of all
            for t, parts in groups:
                files = sorted({f for _, fs, _ in parts for f in fs})
                if t is not None and len(files) > 1:
                    out.append((root, files, parts[0][2]))
                else:
                    for d, fs, _ in parts:
                        out.append((d, sorted(set(fs)), None))
            continue
        title, parts = groups[0]
        files = sorted({f for _, fs, _ in parts for f in fs})
        if title is None and len(files) > 1 and \
                not all(DISC_DIR.search(os.path.basename(f)) for f in files):
            for f in files:                      # unrelated games sharing a folder
                out.append((f, [f], None))
        elif title is not None and len(parts) == 1 and len(files) == 1:
            out.append((parts[0][0], files, None))    # a lone disc folder: convert it in place
        else:
            out.append((root, files, None))
    return sorted(out, key=lambda x: (x[0], x[1]))


def queued_sources():
    """Every source file a queued conversion task already covers."""
    out = set()
    try:
        names = os.listdir(rommlib.CHD_QUEUE_DIR)
    except OSError:
        return out
    for t in names:
        if not t.endswith(".json"):
            continue
        try:
            with open(os.path.join(rommlib.CHD_QUEUE_DIR, t)) as f:
                out.update(json.load(f)["sources"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return out


def candidates(systems=None, cfg=None, skipped=None):
    """Games on the box that could still be compressed, without changing
    anything: [{"system", "root", "files", "name", "mode", "label", "size",
    "enabled", "setting", "xiso", "cut"}]. enabled is False when the format
    needs its Settings switch (RVZ, .zar: they download tools); CHD isn't held
    back by its switch here (asking for it is the point). Xbox: "xiso" when the
    file is still named .xiso, "cut" the bytes a full redump image will lose
    to become bootable (both done by queue(), only with Make Xbox images
    bootable on). With that on but Xbox to squashfs off, an image that still
    needs either is offered as "prep": made bootable, nothing compressed.
    skipped: a list that gets (system, file, reason) for images that can't be
    converted (e.g. encrypted 3DS dumps)."""
    cfg = cfg if cfg is not None else rommlib.load_config()
    queued = queued_sources()
    out = []
    for system in (systems or ORDER):
        if not os.path.isdir(os.path.join(rommlib.ROMS_ROOT, system)):
            continue
        for root, files, name in games_for(system):
            xiso = cut = 0
            if system in rommlib.SQUASH_SYSTEMS:
                bootable = cfg.get("xbox_bootable", rommlib.DEFAULT_CONFIG["xbox_bootable"])
                xiso = files[0].lower().endswith(".xiso")
                if xiso and not bootable:
                    continue                     # ES doesn't list .xiso; renaming is part of the setting
                cut = (rommlib.xbox_partition(files[0]) or 0) if bootable else 0
            # a system the table doesn't know (--systems): its cue/gdi as CD images
            conv = rommlib.conversion_for(system, files[0], enabled=False, renamed=True) \
                or rommlib.CONVERTER["createcd"]
            if conv.planner_skips_done and os.path.exists(conv.output_path(files[0])):
                continue                         # already packed (the original stays until verified)
            if conv.check:
                try:
                    conv.check(files[0])
                except rommlib.CHECK_ERRORS as e:
                    if skipped is not None:
                        skipped.append((system, files[0], str(e)))
                    continue
            if any(f in queued for f in files):
                continue
            size = sum(os.path.getsize(p) for f in files for p in rb_chd.sources_of(f) if os.path.isfile(p))
            enabled = conv.chd or conv.enabled(cfg, system)
            prep = not enabled and bool(xiso or cut)          # bootable without squashfs: just the prep
            out.append({"system": system, "root": root, "files": files, "name": name, "mode": conv.mode,
                        "label": "bootable .iso" if prep else conv.label, "size": size,
                        "setting": conv.setting_name, "enabled": enabled or prep, "prep": prep,
                        "xiso": xiso, "cut": cut})
    return out


def queue(cands, seq=None, say=None):
    """Queue candidates (enabled ones only) for the worker, doing Xbox's
    bootable prep first (.xiso -> .iso, cut to the game partition; a "prep"
    candidate stops there). Returns how many were queued or made bootable.
    say(text): progress lines (the command line)."""
    import time
    seq = seq or int(time.time() * 1000)
    order = {s: i for i, s in enumerate(ORDER)}
    queued = queued_sources()                 # the list may be older than the queue: never twice
    n = 0
    for c in cands:
        if not c["enabled"] or any(f in queued for f in c["files"]):
            continue
        files, root = list(c["files"]), c["root"]
        if c["xiso"]:
            files = [rommlib.xiso_to_iso(files[0])]
            root = files[0]
            if say:
                say("%-9s renamed to .iso: %s" % (c["system"], os.path.basename(files[0])))
        if c["cut"]:
            try:
                rommlib.make_xiso(files[0])
            except (rommlib.NoSpace, OSError) as e:
                if say:
                    say("%-9s couldn't cut %s: %s" % (c["system"], os.path.basename(files[0]), e))
                continue
            if say:
                say("%-9s cut to the game partition (-%s): %s" % (
                    c["system"], rommlib.human_size(c["cut"]), os.path.basename(files[0])))
        if c.get("prep"):                     # bootable now; squashfs is off: nothing to queue
            n += 1
            continue
        task = {"system": c["system"], "mode": c["mode"], "sources": files, "roots": [root], "queued": seq}
        if c["name"]:
            task["name"] = c["name"]          # playlist name: the folder holds other titles too
        k = n
        while not rommlib.publish_queue_task(task, "%d-%02d-%05d.json" % (seq, order.get(c["system"], 99), k),
                                             exclusive=True):
            k += 1                            # never clobber a task from an earlier run
        n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--systems", default=",".join(ORDER))
    ap.add_argument("--only")
    ap.add_argument("--exclude", action="append", default=[])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    os.makedirs(rommlib.CHD_QUEUE_DIR, exist_ok=True)
    skipped = []
    cands = [c for c in candidates(a.systems.split(","), skipped=skipped) if c["enabled"]
             and (not a.only or a.only.lower() in c["root"].lower())
             and not any(x.lower() in c["root"].lower() for x in a.exclude)]
    if a.limit:
        cands = cands[:a.limit]
    for system, f, why in skipped:
        print("%-9s skipped (%s): %s" % (system, why, os.path.basename(f)))
    tot = 0
    for c in cands:
        rel = os.path.relpath(c["root"], rommlib.ROMS_ROOT)
        print("%-9s %9s  %d disc%s  %s%s" % (c["system"], rommlib.human_size(c["size"]), len(c["files"]),
                                              "" if len(c["files"]) == 1 else "s", rel,
                                              " [%s]" % c["name"] if c["name"] else ""))
        tot += c["size"]
    if not a.dry_run:
        queue(cands, say=print)
    print("%s %d games, %s" % ("would queue" if a.dry_run else "queued", len(cands), rommlib.human_size(tot)))


if __name__ == "__main__":
    main()
