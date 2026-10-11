#!/usr/bin/env python3
"""Restore game info that RomM-o-Cera <= v0.6.3 could lose after CHD conversions.

Those versions moved a converted game's gamelist entry to its new file by
editing gamelist.xml; EmulationStation, saving its own lists in the
background, could overwrite that with a bare entry (name from the file name,
no description, cover or play history). The info itself survives in the
gamelist backups (gamelist.xml.bak*) and often as a stale entry for the old
file. This finds it again and gives it back to ES through ES's own API:

  python3 rb_repair.py --dry-run          # list what would be restored
  python3 rb_repair.py [--systems psx,saturn]

Only fills what's empty, keeps the higher play count / time and the later
last-played date, and re-uploads media files that still exist. Matching is
strict: the same file, or an entry whose file is gone that sat in the same
folder under the same name (a CHD conversion, a "NN. " prefix removed, a game
folder turned into one file) — disc numbers must match, other copies of a
game elsewhere are never used. Before applying, every gamelist.xml and ES's
journal are copied to <data dir>/repair-backup-<time>/.
"""
import argparse
import glob
import os
import re
import shutil
import sys
import time
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rommlib  # noqa: E402
import rb_finalize as fz  # noqa: E402

ES_RECOVERY = "/userdata/system/configs/emulationstation/recovery"


def norm(name):
    name = re.sub(r"^\d+\.\s+", "", name)                    # "15. Samba de Amigo" (TOP-N folders)
    name = re.sub(r"\s*\(Track \d+\)", "", name, flags=re.I)
    return name.strip().lower()


def places(sysdir, rel_path):
    """(folder, normalized name) pairs a gamelist path could stand for: the
    file itself, and — for a file inside a game folder — that folder."""
    p = os.path.normpath(os.path.join(sysdir, rel_path))
    stem = os.path.splitext(os.path.basename(p))[0]
    out = {(os.path.dirname(p), norm(stem))}
    parent = os.path.dirname(p)
    if os.path.normpath(parent) != os.path.normpath(sysdir):
        out.add((os.path.dirname(parent), norm(os.path.basename(parent))))
    return out


def candidates(sysdir):
    """Every <game> in the current gamelist.xml and its backups."""
    files = [os.path.join(sysdir, "gamelist.xml")] + sorted(glob.glob(os.path.join(sysdir, "gamelist.xml.bak*")))
    out = []
    for f in files:
        try:
            for g in ET.parse(f).getroot().findall("game"):
                p = g.findtext("path") or ""
                if p:
                    out.append((p, fz.node_info(sysdir, g), os.path.basename(f)))
        except (OSError, ET.ParseError):
            pass
    return out


def plan(system, esapi):
    """[(game, info, source, fields, media)] for one system."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, system)
    games = esapi.games(system, refresh=True) or {}
    cands = candidates(sysdir)
    by_place = {}
    for p, info, src in cands:
        f = os.path.normpath(os.path.join(sysdir, p))
        for pl in places(sysdir, p):
            by_place.setdefault(pl, []).append((f, info, src))
    todo = []
    for path, g in games.items():
        if fz.has(g, "desc") and g.get("image"):
            continue                                    # already has its info
        stem = norm(os.path.splitext(os.path.basename(path))[0])
        best = None
        for f, info, src in by_place.get((os.path.dirname(path), stem), []):
            same = os.path.normpath(f) == os.path.normpath(path)
            if not same and os.path.exists(f):
                continue                                # another copy that still exists: not ours
            if not info["text"].get("desc") and not info["media"]:
                continue
            if best is None or fz.richness(info) > fz.richness(best[1]):
                best = (f, info, src)
        if not best:
            continue
        info = best[1]
        fields = {k: v for k, v in info["text"].items() if not fz.has(g, k)}
        if fields.get("name") == os.path.splitext(os.path.basename(path))[0]:
            del fields["name"]                         # what ES shows anyway: nothing to restore
        fields.update(fz.merge_usage(g, info["usage"]))
        media = {k: p for k, p in info["media"].items() if not g.get(k)}
        if fields or media:
            todo.append((g, info, best[2], fields, media))
    return todo


def snapshot(systems):
    dest = os.path.join(rommlib.DATA_DIR, "repair-backup-%s" % time.strftime("%Y%m%d-%H%M%S"))
    for s in systems:
        src = os.path.join(rommlib.ROMS_ROOT, s, "gamelist.xml")
        if os.path.exists(src):
            os.makedirs(os.path.join(dest, s), exist_ok=True)
            shutil.copy2(src, os.path.join(dest, s, "gamelist.xml"))
    if os.path.isdir(ES_RECOVERY):
        shutil.copytree(ES_RECOVERY, os.path.join(dest, "_es-recovery"), dirs_exist_ok=True)
    return dest


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--systems", default="")
    a = ap.parse_args(argv)
    esapi = fz.EsApi()
    if fz.game_running():
        print("A game (or RomM-o-Cera) is running, or EmulationStation isn't reachable. Run this from "
              "the EmulationStation menu.")
        return 2
    esapi.reload()                                     # ES's lists match the files before we look
    time.sleep(8)
    if a.systems:
        systems = [s for s in a.systems.split(",") if s]
    else:
        systems = sorted(os.path.basename(os.path.dirname(f)) for f in
                         glob.glob(os.path.join(rommlib.ROMS_ROOT, "*", "gamelist.xml.bak*")))
        systems = sorted(set(systems))
    plans = {}
    for s in systems:
        if esapi.games(s, refresh=True) is None:
            continue                                    # ES doesn't list it (no games)
        p = plan(s, esapi)
        if p:
            plans[s] = p
    total = sum(len(v) for v in plans.values())
    for s, items in plans.items():
        print("%s: %d game%s" % (s, len(items), "" if len(items) == 1 else "s"))
        for g, info, src, fields, media in items:
            usage = {k: v for k, v in fields.items() if k in fz.USAGE_FIELDS}
            print("  %-60s +%s%s%s  (from %s)" % (
                os.path.relpath(g["path"], os.path.join(rommlib.ROMS_ROOT, s))[:60],
                ",".join(k for k in fields if k not in fz.USAGE_FIELDS) or "-",
                (" media:" + ",".join(media)) if media else "",
                (" usage:" + ",".join("%s=%s" % kv for kv in usage.items())) if usage else "", src))
    print("TOTAL: %d games" % total)
    if a.dry_run or not total:
        return 0
    dest = snapshot(list(plans))
    print("backup of gamelists + ES journal:", dest)
    ok = bad = 0
    for s, items in plans.items():
        for g, info, src, fields, media in items:
            good = esapi.set_meta(s, g["id"], fields)
            for k, p in media.items():
                good = fz.upload_file(esapi, s, g["id"], k, p) and good
            ok += good
            bad += not good
            if not good:
                print("  FAILED", g["path"])
    rommlib.log("repair: restored info for %d games (%d failed)" % (ok, bad))
    print("restored: %d, failed: %d" % (ok, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
