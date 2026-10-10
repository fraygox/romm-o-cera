#!/usr/bin/env python3
"""Background CHD converter for disc games on the box.

Spawned detached (by the browser after a download, or after rb_chd_plan.py
queues existing library games); one instance (flock). Works through
chd-queue/*.json in name order. Only starts a disc while nothing but the
browser is running in ES, and SIGSTOPs its own chdman while a game is up
(SIGCONT when ES is idle again), so it never competes with a game. Every CHD
is `chdman verify`d before the sources are deleted; on any failure the
originals stay (they play fine).

Per converted game it also:
- places the CHD next to the original (a game folder becomes <folder>.chd in
  its parent, so e.g. "TOP 100 …/38. Evolution 2 (USA)/" stays in TOP 100);
- copies RetroArch/emulator saves + states to the new file name when the name
  changes (copies, never moves — the old ones stay);
- rewrites any .m3u that pointed at the old cue/gdi, or writes a new .m3u for
  a multi-disc folder (discs hidden in the gamelist);
- queues the gamelist path rename for rb_finalize (flush → merge → reload,
  only when ES is idle).
"""
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rommlib  # noqa: E402
import rb_finalize  # noqa: E402

LOCK = os.path.join(rommlib.DATA_DIR, "chd.lock")
THREADS = str(max(1, (os.cpu_count() or 4) - 2))   # leave 2 threads free; only runs while no game is up
SAVES_ROOT = os.environ.get("ROMM_BROWSER_SAVES", "/userdata/saves")
DISC_EXTS = (".cue", ".gdi", ".iso", ".bin", ".raw", ".img", ".chd", ".cdi", ".ccd", ".sub")
JUNK_EXTS = {".txt", ".nfo", ".md5", ".sfv", ".sha1", ".jpg", ".jpeg", ".png", ".m3u",
             ".dat", ".url", ".log", ".xml", ".pdf", ".cfg", ".ini", ".lic"}
FINALIZE_EVERY = 15   # merge gamelist renames + reload ES after this many games


def running_game():
    """Path of the running game ('' when idle or only the browser runs);
    None when ES can't be asked (treated as busy)."""
    try:
        status, body = rb_finalize.es_get("/runningGame")
    except Exception:
        return None
    if status != 200 or not body.strip():
        return ""
    try:
        path = json.loads(body).get("path") or ""
    except ValueError:
        return ""
    return "" if path.endswith("rommbrowser.pygame") else path


def chdman(*args):
    """Run chdman at nice 19; pause it (SIGSTOP) whenever a game starts."""
    cmd = ["nice", "-n", "19", "chdman"] + list(args)   # no ionice in Batocera
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    paused = False
    while p.poll() is None:
        time.sleep(3)
        busy = running_game() != ""
        if busy and not paused:
            p.send_signal(signal.SIGSTOP)
            paused = True
            rommlib.log("chd: paused (game running)")
        elif not busy and paused:
            p.send_signal(signal.SIGCONT)
            paused = False
            rommlib.log("chd: resumed")
    err = p.stderr.read() if p.stderr else ""
    return p.returncode, (err or "")[-300:]


def sources_of(cue_or_gdi):
    """The cue/gdi itself plus every track file it references."""
    d = os.path.dirname(cue_or_gdi)
    out = [cue_or_gdi]
    if not cue_or_gdi.lower().endswith((".cue", ".gdi")):
        return out
    with open(cue_or_gdi, errors="replace") as f:
        text = f.read()
    if cue_or_gdi.lower().endswith(".cue"):
        for m in re.finditer(r'FILE\s+"([^"]+)"', text, re.I):
            out.append(os.path.join(d, m.group(1)))
    else:
        for line in text.splitlines()[1:]:
            m = re.match(r'\s*\d+\s+\d+\s+\d+\s+\d+\s+("([^"]+)"|(\S+))', line)
            if m:
                out.append(os.path.join(d, m.group(2) or m.group(3)))
    return out


def migrate_saves(system, old_path, new_path):
    """Copy saves/states keyed on the old file name to the new one. RetroArch
    keys on the stem ("Game (USA).srm"), PCSX2 states on the full basename
    ("Game (USA).iso.p2s"), so both forms are mapped."""
    root = os.path.join(SAVES_ROOT, system)
    if not os.path.isdir(root):
        return 0
    ob, nb = os.path.basename(old_path), os.path.basename(new_path)
    pairs = [(ob, nb), (os.path.splitext(ob)[0], os.path.splitext(nb)[0])]
    pairs = [(o, n) for o, n in pairs if o != n]
    copied = 0
    for dp, _, fns in os.walk(root):
        for fn in fns:
            for o, n in pairs:
                if fn.startswith(o + "."):
                    dst = os.path.join(dp, n + fn[len(o):])
                    if not os.path.exists(dst):
                        shutil.copy2(os.path.join(dp, fn), dst)
                        copied += 1
                    break
    if copied:
        rommlib.log("chd: copied %d save file(s) %s -> %s" % (copied, ob, nb))
    return copied


def fix_m3us(sysdir, old_to_new):
    """Point existing .m3u playlists at the new CHDs. Returns m3us touched."""
    touched = []
    for dp, _, fns in os.walk(sysdir):
        for fn in fns:
            if not fn.lower().endswith(".m3u"):
                continue
            p = os.path.join(dp, fn)
            try:
                lines = open(p, errors="replace").read().splitlines()
            except OSError:
                continue
            changed = False
            out = []
            for ln in lines:
                target = os.path.normpath(os.path.join(dp, ln.strip())) if ln.strip() else ""
                if target in old_to_new:
                    out.append(os.path.relpath(old_to_new[target], dp))
                    changed = True
                else:
                    out.append(ln)
            if changed:
                with open(p, "w") as f:
                    f.write("\n".join(out) + "\n")
                touched.append(p)
    return touched


def convert(task):
    system, mode = task["system"], task["mode"]
    sysdir = os.path.join(rommlib.ROMS_ROOT, system)
    srcs = sorted(s for s in task["sources"] if os.path.exists(s))
    if not srcs:
        return False
    playing = running_game()
    if playing and playing in srcs:
        rommlib.log("chd: SKIP in use %s" % playing)
        return False
    roots = task["roots"]
    folder_root = next((r.rstrip("/") for r in roots if os.path.isdir(r)), None)
    if folder_root and os.path.normpath(folder_root) == os.path.normpath(sysdir):
        folder_root = None        # never treat the system dir itself as a game folder
    out_dir = os.path.dirname(folder_root) if folder_root else os.path.dirname(srcs[0])
    multi = len(srcs) > 1
    plan = []
    for src in srcs:
        if multi or not folder_root:
            stem = os.path.splitext(os.path.basename(src))[0]
        else:
            stem = os.path.basename(folder_root)      # game name, not track name
        plan.append((src, os.path.join(out_dir, stem + ".chd")))
    for _, out in plan:
        if os.path.exists(out):
            rommlib.log("chd: SKIP exists %s" % out)
            return False
    made = []
    for src, out in plan:
        tmp = out + ".tmp.chd"
        t0 = time.time()
        rc, err = chdman(mode, "-np", THREADS, "-f", "-i", src, "-o", tmp)
        if rc == 0:
            rc, err = chdman("verify", "-i", tmp)
        if rc != 0:
            rommlib.log("chd: FAIL %s rc=%d %s" % (src, rc, err))
            for m in made + [tmp]:
                if os.path.exists(m):
                    os.remove(m)
            return False
        os.replace(tmp, out)
        made.append(out)
        rommlib.log("chd: disc done %s (%.0fs)" % (os.path.basename(out), time.time() - t0))

    old_to_new = {os.path.normpath(s): o for s, o in plan}
    in_size = sum(os.path.getsize(p) for s in srcs for p in sources_of(s) if os.path.isfile(p))
    out_size = sum(os.path.getsize(m) for m in made)
    m3us = fix_m3us(sysdir, old_to_new)
    rename, hide = {}, []
    if multi:
        # an existing playlist (now rewritten) stays the entry; else write one
        outside = [m for m in m3us if not (folder_root and m.startswith(folder_root + "/"))]
        if outside:
            primary = outside[0]
        else:
            name = os.path.basename(folder_root) if folder_root else \
                re.sub(r"\s*\(Disc \d+\).*$", "", os.path.splitext(os.path.basename(srcs[0]))[0])
            primary = os.path.join(out_dir, name + ".m3u")
            if os.path.exists(primary):
                # a playlist of that name belongs to another copy of the game:
                # never overwrite it — leave these discs listed individually
                rommlib.log("chd: m3u exists, not overwriting %s" % primary)
                primary = None
            else:
                with open(primary, "w") as f:
                    f.write("\n".join(os.path.basename(m) for m in made) + "\n")
        hide = made if primary else []
        primary = primary or made[0]
        migrate_saves(system, srcs[0], primary)
        for s, o in plan:
            migrate_saves(system, s, o)
    else:
        primary = made[0]
        migrate_saves(system, srcs[0], primary)

    for src in srcs:
        for p in sources_of(src):
            if os.path.isfile(p):
                os.remove(p)
    if folder_root and os.path.isdir(folder_root):
        # drop subfolders the conversion emptied (e.g. "Game (Disc 2)/")
        for dp, _, _ in sorted(os.walk(folder_root), key=lambda w: -len(w[0])):
            if dp != folder_root:
                try:
                    os.rmdir(dp)          # only succeeds when empty
                except OSError:
                    pass
        leftovers = [os.path.join(dp, f) for dp, _, fs in os.walk(folder_root) for f in fs]
        if all(os.path.splitext(f)[1].lower() in JUNK_EXTS for f in leftovers):
            shutil.rmtree(folder_root, ignore_errors=True)
        else:
            rommlib.log("chd: kept folder (non-junk leftovers) %s" % folder_root)
        rename[folder_root] = primary
    else:
        for src in srcs:
            rename[src] = primary
    with open(rommlib.GAMELIST_QUEUE, "a") as f:
        f.write(json.dumps({"system": system, "rename": rename, "hide": hide}) + "\n")
    rommlib.Library().rename_paths(rename, hide)
    rommlib.log("chd: OK %s -> %s  (%.2f GB -> %.2f GB)" % (
        os.path.relpath(srcs[0], sysdir), os.path.relpath(primary, sysdir),
        in_size / 1e9, out_size / 1e9))
    return True


def main():
    os.makedirs(rommlib.CHD_QUEUE_DIR, exist_ok=True)
    lockf = open(LOCK, "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return
    since_finalize = 0
    while True:
        tasks = sorted(t for t in os.listdir(rommlib.CHD_QUEUE_DIR) if t.endswith(".json"))
        if not tasks:
            if not since_finalize:
                break
            rb_finalize.main()      # merge what's done, then look for new tasks
            since_finalize = 0
            continue
        if running_game() != "":
            time.sleep(20)
            continue
        path = os.path.join(rommlib.CHD_QUEUE_DIR, tasks[0])
        try:
            with open(path) as f:
                task = json.load(f)
            if convert(task):
                since_finalize += 1
        except Exception as e:
            rommlib.log("chd: error on %s: %r" % (tasks[0], e))
        os.remove(path)
        if since_finalize >= FINALIZE_EVERY:
            rb_finalize.main()      # waits for ES idle; flock'd
            since_finalize = 0


if __name__ == "__main__":
    main()
