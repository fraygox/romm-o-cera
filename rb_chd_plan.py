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
Wii U: with Decrypt Wii U discs on, .wud/.wux discs whose key is here are queued
for .wua; other .wud images for .wux (Settings -> Wii U to .wux), and a disc that
can't be decrypted is listed with the reason.

Finds cue/gdi (CD systems), iso (PS2/PSP) and cso (PSP) games that have no CHD yet, groups
discs into games (a folder holding the disc files, with two or more "(Disc N)"
subfolders, or bare "Disc N"/"CD2" ones whose sheets share a title, folded into
their parent; one set per title), and writes chd-queue tasks in the given system
order. A game whose output name is taken (by a file already there or another
game), a track is missing, or a file is still being written is listed as
skipped, with the reason. Then start rb_chd.py (it waits for ES to be idle by itself).
"""
import argparse
import json
import os
import re
import time
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rommlib  # noqa: E402
import rb_chd  # noqa: E402

# the order games are queued in (and --systems' default): each converter's
# priority, from the converter table (tests/test_logic.py pins it)
ORDER = rommlib.planner_order()
STILL_WRITTEN = 60          # seconds: a file changed this recently is still being unpacked or copied


def _hidden(name):
    """A dot file: macOS's AppleDouble "._Game.cue" beside every copied file, or the like."""
    return name.startswith(".")


def _stem(path):
    return os.path.splitext(os.path.basename(path))[0]


_TRAIL_DISC = re.compile(r"[\s_-]*(?:disc|disk|cd)\s*\d+$", re.I)
_TRAIL_TAG = re.compile(r"\s*(?:\([^()]*\)|\[[^\[\]]*\])$")


def _title_key(title):
    """A sheet's title for comparing with its folder's name: lower case, without a
    trailing disc word ("... CD1", "... - Disc 1") or bracket tags ("(Rev 1)", "[!]")."""
    t = title.strip()
    while True:
        n = _TRAIL_TAG.sub("", t)
        n = _TRAIL_DISC.sub("", n).strip()
        if n == t or not n:
            return t.lower()
        t = n


def _folder_disc(d, files, sysdir):
    """The game title when folder d is one disc of a game inside another folder:
    "Game/Game (Disc 2)/" -> "Game". A bare "Game/Disc 2/" (or "CD2/") only by what
    its sheets are called: all one title ignoring case and the disc tag ("Game (Disc 2).cue"
    -> "Game", the parent's spelling when it's the parent's name), so bare disc folders of
    different games ("Mega/CD1/Mega Man.cue", "Mega/CD2/Mega Race.cue") are never one set. None otherwise (or
    when its parent is the system folder)."""
    parent = os.path.dirname(d)
    if os.path.normpath(parent) == os.path.normpath(sysdir):
        return None
    name = os.path.basename(d)
    if not rb_chd.BARE_DISC.match(name):
        return rb_chd.disc_title(name)
    game = os.path.basename(parent)
    titles = {_title_key(rb_chd.disc_title(st) or st): rb_chd.disc_title(st) or st
              for st in (_stem(f) for f in files)}
    if titles and set(titles) == {_title_key(game)}:
        return game
    return next(iter(titles.values())) if len(titles) == 1 else None


def _flat(root, files, named):
    """Several sheets straight in one folder: each title's discs one set ("A (Disc 1)",
    "A (Disc 2)"), any other sheet a game of its own. named: the set's playlist is
    named after its title even when it's the folder's only set."""
    titles, singles = {}, []
    for f in files:
        t = rb_chd.disc_title(_stem(f))
        if t is None:
            singles.append(f)
        else:
            titles.setdefault(t.lower(), []).append(f)
    if not singles and len(titles) == 1 and not named:
        return [(root, files, None)]          # the folder is the game: its playlist takes the folder's name
    out = [(f, [f], None) for f in singles]
    for fs in titles.values():
        if len(fs) == 1:
            out.append((fs[0], fs, None))     # one disc of a set: on its own, under its own name
        else:
            out.append((root, sorted(fs), rb_chd.disc_title(_stem(fs[0]))))
    return out


def _gdi_pairs(files):
    """A .cue beside a .gdi of the same game (some DC dumps ship both): the gdi
    is the one converted. Returns (files to plan, {gdi: [cues set aside]}). Only
    a cue whose every track a gdi names is set aside (it's deleted with the game);
    any other cue (another game's, or a second dump with tracks of its own) is a
    game of its own."""
    gdis = [f for f in files if f.lower().endswith(".gdi")]
    if not gdis:
        return files, {}
    tracks = {g: {os.path.normpath(p) for p in rommlib.sheet_sources(g)} for g in gdis}
    keep, also = [], {}
    for f in files:
        if f in gdis:
            keep.append(f)
            continue
        if not f.lower().endswith(".cue"):
            keep.append(f)
            continue
        mine = {os.path.normpath(p) for p in rommlib.sheet_sources(f)}
        owner = next((g for g in gdis if mine and mine <= tracks[g]), None)
        if owner is None:
            keep.append(f)                     # another game's sheet
        else:
            also.setdefault(owner, []).append(f)
    return keep, also


def _games(system):
    """games_for, each with the sheets set aside for it: [(root, files, name, also)]."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, system)
    convs = rommlib.converters_for(system)
    if convs and not any(c.chd for c in convs):
        exts = tuple(e for c in convs for e in c.sources + c.renamed_sources)    # every image on its own
        games = tuple(o for c in convs for o in c.outputs)     # a finished game folder (X.ps3): never inside
        out = []
        for dp, dns, fns in os.walk(sysdir):
            dns[:] = [d for d in dns if d not in rommlib.MEDIA_DIRS and not d.startswith(".")
                      and not d.lower().endswith(games)]
            out += [(os.path.join(dp, f), [os.path.join(dp, f)], None, []) for f in fns
                    if f.lower().endswith(exts) and not _hidden(f)]
        return sorted(out, key=lambda x: x[0])
    # CHD: PS2/PSP ship both DVD isos and CD cue/bin games; CD systems (and a
    # system the table doesn't know, given with --systems): cue/gdi only
    exts = tuple(e for c in convs for e in c.sources) or rommlib.CONVERTER["createcd"].sources
    found = []
    for dp, dns, fns in os.walk(sysdir):
        dns[:] = [d for d in dns if d not in rommlib.MEDIA_DIRS and not d.startswith(".")]
        found += [os.path.join(dp, fn) for fn in fns if fn.lower().endswith(exts) and not _hidden(fn)]
    by_dir = {}
    for p in found:
        by_dir.setdefault(os.path.dirname(p), []).append(p)
    games, loose, also = {}, [], {}
    for d, files in by_dir.items():
        top = os.path.normpath(d) == os.path.normpath(sysdir)
        files, aside = _gdi_pairs(sorted(files))
        also.update(aside)
        if top:
            for f in files:                       # loose files: one game each
                loose.append((f, [f]))
            continue
        title = _folder_disc(d, files, sysdir)
        if title is not None:
            # Game/Game (Disc 2)/x.cue (or Game/Disc 2/x.cue) -> Game/, but only together
            # with discs of the same title (a "TOP 100" folder can hold many games' discs)
            key = (os.path.dirname(d), title.lower())
        else:
            key = (d, None)
        games.setdefault(key, []).append((d, files, title))
    for key in [k for k, parts in games.items() if k[1] is not None and len(parts) == 1]:
        # one disc folder on its own: its game is that folder, never the one around it
        # (that holds other things: manuals, covers, other games' folders)
        (d, files, _), = games.pop(key)
        games.setdefault((d, None), []).append((d, files, None))
    out = [(r, fs, None) for r, fs in loose]
    by_root = {}
    for (root, title), parts in games.items():
        by_root.setdefault(root, []).append((title, parts))
    for root, groups in by_root.items():
        several = len(groups) > 1
        # several titles' disc folders in one folder (e.g. a game folder holding the
        # original and the Dual Shock release): one set per title, its playlist named
        # after that title — never one set of all
        for t, parts in groups:
            files = sorted({f for _, fs, _ in parts for f in fs})
            if t is None:
                out += _flat(root, files, several) if len(files) > 1 else [(root, files, None)]
            else:                                 # two disc folders or more of one title
                out.append((root, files, parts[0][2] if several else None))
    return sorted(((r, fs, n, sorted(a for f in fs for a in also.get(f, ()))) for r, fs, n in out),
                  key=lambda x: (x[0], x[1]))


def games_for(system):
    """[(root, files, playlist name or None)]: each game to convert on this system."""
    return [(r, fs, n) for r, fs, n, _ in _games(system)]


def _tasks():
    try:
        names = os.listdir(rommlib.CHD_QUEUE_DIR)
    except OSError:
        return
    for t in names:
        if not t.endswith(".json"):
            continue
        try:
            with open(os.path.join(rommlib.CHD_QUEUE_DIR, t)) as f:
                task = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(task, dict):
            yield task


def _key(path):
    """An output's name as the drive sees it: a FAT/exFAT drive ignores case."""
    return os.path.normpath(path).lower()


def queued_outputs():
    """{output (_key): its task's sources} for every queued task."""
    out = {}
    for task in _tasks():
        try:
            srcs = sorted(task["sources"])
            if srcs:
                _, _, plan = rb_chd.plan_outputs(task["system"], task["mode"], srcs, task.get("roots") or [])
                mine = {os.path.normpath(p) for p in srcs}
                out.update((_key(o), mine) for _, o in plan)
        except (KeyError, TypeError, AttributeError, ValueError, OSError):
            pass
    return out


def queued_sources():
    """Every source file a queued conversion task already covers (not one waiting
    for its tool: Compress keeps listing that game, as needing the tool)."""
    out = set()
    for task in _tasks():
        try:
            conv = rommlib.CONVERTER.get(task.get("mode"))
            tool = getattr(conv, "tool", None)
            if tool is not None and not tool.installed():
                continue
            out.update(task["sources"])
            out.update(task.get("also") or ())
        except (KeyError, TypeError):
            pass
    return out


try:
    _WORKER_TIME = os.path.getmtime(rb_chd.__file__)      # when this version of the worker landed
except OSError:
    _WORKER_TIME = 0
_RC = re.compile(r"^FAIL .*? rc=-?\d+\s*", re.S)


def _failures():
    """{normalised source: (time, chdman's words)}: the sources whose last conversion
    failed (the converter refused them), from chd-results.jsonl. Converting them as
    they are would only fail again: offered again once a file of the game changes."""
    out = {}
    try:
        with open(rommlib.chd_results_path(), errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return out
    for ln in lines:
        try:
            e = json.loads(ln)
            srcs = [os.path.normpath(s) for s in e.get("sources") or ()]
            msg = e.get("message") or ""
        except (ValueError, TypeError, AttributeError):
            continue
        failed = e.get("result") == "failed" and _RC.match(msg)
        for s in srcs:
            out.pop(s, None)                      # a later result replaces an earlier failure
            if failed:
                out[s] = (e.get("time") or 0, _why_failed(_RC.sub("", msg, 1)))
    return out


def _why_failed(err):
    """The line of a converter's output that says what went wrong."""
    lines = [ln.strip() for ln in err.splitlines() if ln.strip()]
    said = [ln for ln in lines if "error" in ln.lower() and not ln.lower().startswith("fatal error occurred")]
    why = (said or lines or ["no reason given"])[0]
    return why if len(why) <= 160 else why[:157] + "..."


def _cut_short(files):
    """Why a game's disc can't be converted because it's certainly cut short (an
    interrupted copy: its own header says the disc is bigger), else ""."""
    for f in files:
        if not f.lower().endswith((".cue", ".iso")):
            continue                              # (a .chd would need chdman: the worker checks)
        cut = rommlib.disc_incomplete(f)
        if not (cut and cut[2]) or cut[0] <= 0:
            continue
        try:
            parts = rb_chd.sources_of(f)
            size = sum(os.path.getsize(p) for p in parts[1:] or parts)      # a sheet's tracks, or the image
        except OSError:
            continue
        return "cut short: %s of %s; Check games can download it again" % (
            rommlib.human_size(size), rommlib.human_size(size * cut[1] // cut[0]))
    return ""


def candidates(systems=None, cfg=None, skipped=None):
    """Games on the box that could still be compressed, without changing
    anything: [{"system", "root", "files", "name", "mode", "label", "size",
    "enabled", "setting", "xiso", "cut", "also"}]. also: sheets of the same
    game set aside (a .cue beside its .gdi), deleted with it. enabled is False when the format
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
    taken = queued_outputs()                  # output path -> the sources that make it
    counted = set()                           # tracks already in a size: two sheets sharing one count it once
    failed = _failures()
    out = []

    def skip(system, f, why):
        if skipped is not None:
            skipped.append((system, f, why))
    for system in (systems or ORDER):
        if not os.path.isdir(os.path.join(rommlib.ROMS_ROOT, system)):
            continue
        for root, files, name, also in _games(system):
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
            if getattr(conv, "fallback", ""):
                # Wii U: decrypted to .wua when that's on and the key is here, else offered as .wux
                taker, why, off = rommlib.settle(conv, system, files[0], cfg, alt_enabled=False)
                if taker is not conv:
                    # on, but this disc can't be decrypted (nothing to say of a file that just isn't
                    # the first converter's kind: an ordinary Xbox 360 disc isn't a package disc)
                    if not off and skipped is not None and not getattr(conv, "words", ()):
                        skipped.append((system, files[0], "can't decrypt it: %s; offered as %s instead"
                                        % (why, taker.label)))
                    conv = taker
                elif off and conv.check:
                    try:                         # off, nothing else takes it: offered only if it could be done
                        conv.check(files[0])
                    except rommlib.CHECK_ERRORS:
                        continue
            if conv.planner_skips_done and os.path.exists(conv.output_path(files[0])):
                continue                         # already packed (the original stays until verified)
            if conv.check:
                try:
                    conv.check(files[0])
                except rommlib.CHECK_ERRORS as e:
                    if skipped is not None and not getattr(e, "quiet", False):    # (quiet: just not its kind)
                        skipped.append((system, files[0], str(e)))
                    continue
            if any(f in queued for f in files):
                continue
            try:
                parts = [p for f in files for p in rb_chd.sources_of(f)]
            except OSError as e:                 # a sheet unreadable (or gone) since the walk: skip just this game
                skip(system, files[0], "can't read it: %s" % (e.strerror or e))
                continue
            missing = next((p for p in parts if not os.path.isfile(p)), None)
            if missing:                          # the worker could only fail on it, every time
                skip(system, files[0], "a track is missing: %s" % os.path.basename(missing))
                continue
            for a in also:                       # set-aside sheets: their own tracks go too
                parts += [p for p in rb_chd.sources_of_quiet(a) if os.path.isfile(p)]
            try:
                now = time.time()
                # a time ahead of the clock (a box whose clock is behind the copying
                # computer's) counts only within the window: never held back for good
                fresh = any(now - STILL_WRITTEN < os.path.getmtime(p) <= now + STILL_WRITTEN for p in parts)
                sizes = {os.path.normpath(p): os.path.getsize(p) for p in parts}
            except OSError as e:
                skip(system, files[0], "can't read it: %s" % (e.strerror or e))
                continue
            if fresh:                            # a sheet or a track still being unpacked or copied:
                skip(system, files[0], "it's still being written; try again in a minute")
                continue                         # the worker would only find it changed when it's done
            short = _cut_short(files)
            if short:                            # the worker skips it every time: say why here
                skip(system, files[0], short)
                continue
            fail = next((failed[os.path.normpath(f)] for f in files if os.path.normpath(f) in failed), None)
            try:
                # (an update since: the new worker may read it, so it's offered again)
                same = fail is not None and fail[0] >= max([os.path.getmtime(p) for p in parts] + [_WORKER_TIME])
            except (OSError, ValueError):
                same = False
            if same:                             # nothing changed since it failed: it would fail again
                skip(system, files[0], "converting it failed: %s" % fail[1])
                continue
            # what the worker will make: never a name a file already has (another game's:
            # the worker would take it for its own) or that another game here makes too
            src = [os.path.splitext(f)[0] + ".iso" if xiso else f for f in files]
            _, _, plan = rb_chd.plan_outputs(system, conv.mode, sorted(src), [src[0] if xiso else root])
            clash = None
            mine = {os.path.normpath(p) for p in files + src}
            outs = [_key(o) for _, o in plan]
            if len(set(outs)) != len(outs):      # the worker would only skip it, every time
                dup = next(o for _, o in plan if outs.count(_key(o)) > 1)
                clash = "two of its discs would both become %s; rename one of them" % os.path.basename(dup)
            for s_, o in [] if clash else plan:
                other = taken.get(_key(o))
                if other is not None and not other & mine:
                    clash = "another game here would also become %s" % os.path.basename(o)
                elif not conv.planner_skips_done and os.path.lexists(o) and not rb_chd.made_from(o, s_):
                    # not one this worker made of this game and was interrupted finishing
                    # (that one is offered: the worker finishes it, or makes it again when it
                    # can't): never touched (another disc's or game's, or a copy kept on purpose)
                    clash = rb_chd.already_here(o)
                if clash:
                    break
            if clash:
                skip(system, files[0], clash)
                continue
            taken.update((_key(o), mine) for _, o in plan)
            size = sum(v for k, v in sizes.items() if k not in counted)
            counted.update(sizes)
            enabled = conv.chd or conv.enabled(cfg, system)
            prep = not enabled and bool(xiso or cut)          # bootable without squashfs: just the prep
            out.append({"system": system, "root": root, "files": files, "name": name, "mode": conv.mode,
                        "label": "bootable .iso" if prep else conv.label, "size": size,
                        "setting": conv.setting_name, "enabled": enabled or prep, "prep": prep,
                        "xiso": xiso, "cut": cut, "also": also})
    return out


def queue(cands, seq=None, say=None):
    """Queue candidates (enabled ones only) for the worker, doing Xbox's
    bootable prep first (.xiso -> .iso, cut to the game partition; a "prep"
    candidate stops there). Returns how many were queued or made bootable.
    say(text): progress lines (the command line)."""
    seq = seq or int(time.time() * 1000)
    order = {s: i for i, s in enumerate(ORDER)}
    queued = queued_sources()                 # the list may be older than the queue: never twice
    n = 0
    for c in cands:
        if not c["enabled"] or any(f in queued for f in c["files"]):
            continue
        try:
            n += _queue_one(c, seq, order, n, say)
        except OSError as e:                  # this one game (a rename refused, the queue unwritable): the rest go on
            if say:
                say("%-9s couldn't queue %s: %s" % (c["system"], os.path.basename(c["files"][0]), e))
    return n


def _queue_one(c, seq, order, n, say):
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
            return 0
        if say:
            say("%-9s cut to the game partition (-%s): %s" % (
                c["system"], rommlib.human_size(c["cut"]), os.path.basename(files[0])))
    if c.get("prep"):                         # bootable now; squashfs is off: nothing to queue
        return 1
    task = {"system": c["system"], "mode": c["mode"], "sources": files, "roots": [root], "queued": seq}
    if c["name"]:
        task["name"] = c["name"]              # playlist name: the folder holds other titles too
    if c.get("also"):
        task["also"] = list(c["also"])        # sheets of the same game set aside (a .cue beside its .gdi)
    k = n
    while not rommlib.publish_queue_task(task, "%d-%02d-%05d.json" % (seq, order.get(c["system"], 99), k),
                                         exclusive=True):
        k += 1                                # never clobber a task from an earlier run
    return 1


def main():
    try:
        sys.stdout.reconfigure(errors="replace")     # a file name that isn't UTF-8 never stops the run
    except (AttributeError, ValueError):
        pass
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
    if not a.dry_run and any(c["xiso"] or c["cut"] for c in cands):
        playing = rommlib.es_running_game()  # None: ES can't be asked, so a game may be running
        if playing or playing is None:
            # renaming or cutting an image a game may be reading: leave the Xbox ones for later
            for c in cands:
                if c["xiso"] or c["cut"]:
                    print("%-9s skipped (a game is running, or ES can't be asked): %s"
                          % (c["system"], os.path.basename(c["files"][0])))
            cands = [c for c in cands if not (c["xiso"] or c["cut"])]
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
    if a.dry_run:
        print("would queue %d games, %s" % (len(cands), rommlib.human_size(tot)))
    else:
        print("queued %d games, %s" % (queue(cands, say=print), rommlib.human_size(tot)))


if __name__ == "__main__":
    main()
