#!/usr/bin/env python3
"""Background CHD converter for disc games on the box.

Spawned detached (by the browser after a download, or after rb_chd_plan.py
queues existing library games); one instance (flock). Works through
chd-queue/*.json in name order. Only starts a disc while nothing but the
browser is running in ES, and SIGSTOPs its own chdman while a game is up
(SIGCONT when ES is idle again), so it never competes with a game; with
/isIdle (Batocera 36+) it also waits while ES scrapes, hashes, installs or
updates. Every CHD
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
import glob
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rommlib  # noqa: E402

# what a game folder may still hold and be removed with its game: files the OS made
# (thumbnail caches, folder settings, macOS resource forks). A manual, a cover, notes,
# a save or anything unknown is the owner's: the folder stays, with them in it
OS_JUNK = {"thumbs.db", "ehthumbs.db", ".ds_store", "desktop.ini", ".directory"}
CHECKSUM_EXTS = (".sfv", ".md5", ".sha1")
FINALIZE_EVERY = 15   # merge gamelist renames + reload ES after this many games
TOOL_POLL = 3         # seconds between progress / running-game / memory checks while a converter runs
MEMORY_RETRY = 60     # seconds before a conversion stopped for a game's memory is tried again


def running_game():
    """Path of the running game ('' when idle or only the browser runs);
    None when ES can't be asked (treated as busy)."""
    return rommlib.es_running_game()


def busy_reason():
    """Why conversions wait: "" they may run, "game" a game runs (or ES
    can't be asked), "es" ES scrapes, hashes, installs or updates (/isIdle,
    Batocera 36+; without it the running game alone decides), "hold" the
    owner paused conversions (Activity). The browser alone counts as idle
    (rommlib.es_busy without app_busy)."""
    if _new("conversions_held"):
        return "hold"
    return rommlib.es_busy()


def _new(name, *args):
    """rommlib.<name>(*args) for a name newer than the last release: False on an older rommlib
    still in memory during an update (test_package)."""
    fn = getattr(rommlib, name, None)
    return fn(*args) if fn else False


PAUSED = {"game": "a game runs", "es": "EmulationStation is busy", "hold": "you paused it"}


class Stopped(Exception):
    """The owner stopped this conversion (Activity, rommlib.stop_conversion)."""


class ChdmanMissing(Exception):
    """chdman isn't installed: stop, keeping the queue."""


class ToolMissing(Exception):
    """A converter other than chdman (dolphin-tool) isn't installed."""


class ToolWaiting(Exception):
    """A task's switch is on but its tool isn't here yet (the app fetches it):
    the task stays queued; args[0] is the tool (a ToolSpec)."""

    def __str__(self):
        return self.args[0].slug


def _switched_on(conv, system):
    """The converter's switch is on, its tool here or not (False on an older
    rommlib still in memory, which has no such notion)."""
    on = getattr(conv, "switched_on", None)
    return bool(on and getattr(conv, "tool", None) is not None and on(rommlib.load_config(), system))


def _drop_old_tools():
    """Other versions of the converter tools, once this version of each is
    installed: removed here, holding the conversion lock, so no worker is using
    one (an upgrade leaves them to this). Missing helpers (an older rommlib): nothing."""
    old_dirs = getattr(rommlib, "old_tool_dirs", None)
    specs = {id(c.tool): c.tool for c in rommlib.CONVERTERS if getattr(c, "tool", None) is not None}
    for spec in specs.values() if old_dirs else ():
        if not spec.installed():
            continue
        for d in old_dirs(spec):
            shutil.rmtree(d, ignore_errors=True)
            note("chd: removed %s (replaced by %s)" % (os.path.basename(d), spec.folder))


class MemoryGuard(Exception):
    """chdman was stopped to protect the box's memory. .retry: try again later
    (a game needed the RAM) rather than give up on this disc."""

    def __init__(self, msg, retry):
        super().__init__(msg)
        self.retry = retry


# chdman's FLAC codec (in createdvd's default list) leaks ~4 MB of RAM per MB of
# output on Batocera 43's chdman 0.285 — an 8 GB DVD needed ~30 GB and took the
# box down (2026-10-10). FLAC is for audio and useless on DVD data: leave it out.
# CDs keep their defaults (cdfl helps audio tracks and its leak is bounded by
# the disc, ~1-1.3 GB) unless the low-memory setting is on (default on boxes
# under 4 GB RAM): then cdfl is left out too.
DVD_CODECS = "lzma,zlib,huff"
CD_CODECS_LOW_MEMORY = "cdlz,cdzl"
def codecs_for(mode, cfg, system=None):
    if mode in ("createdvd", "cso"):          # cso: the ISO decoded from a PSP .cso
        # PPSSPP reads CHDs made with 2048-byte hunks (one sector) much faster
        return ["-c", DVD_CODECS] + (["-hs", "2048"] if system == "psp" else [])
    if rommlib.chd_low_memory(cfg):
        return ["-c", CD_CODECS_LOW_MEMORY]
    return []


MEM_SHARE_MAX = 0.5          # chdman may use at most this share of the box's RAM
MEM_FREE_MIN = 0.10          # ... and never push free memory below this share


def _meminfo():
    out = {}
    try:
        with open("/proc/meminfo") as f:
            for ln in f:
                k, v = ln.split(":", 1)
                out[k] = int(v.split()[0]) * 1024
    except (OSError, ValueError):
        pass
    return out


def _rss(pid):
    try:
        with open("/proc/%d/status" % pid) as f:
            for ln in f:
                if ln.startswith("VmRSS:"):
                    return int(ln.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return 0


def memory_check(pid, busy):
    """None if fine, else a MemoryGuard to stop chdman with."""
    mi = _meminfo()
    total, avail = mi.get("MemTotal", 0), mi.get("MemAvailable", 0)
    if not total:
        return None
    rss = _rss(pid)
    if rss > total * MEM_SHARE_MAX:
        return MemoryGuard("chdman needed %s of RAM (over half of this box's)" % rommlib.human_size(rss), False)
    if avail < total * MEM_FREE_MIN and rss > 256 << 20:
        return MemoryGuard("memory ran low (%s free, chdman %s)" % (rommlib.human_size(avail), rommlib.human_size(rss)),
                           busy)
    return None


RESULTS_KEEP = 200
_state = {"task": None, "since": 0.0, "file": "", "step": "", "pct": None, "paused": "", "last": "", "wait": ""}


def status(**kw):
    """What the worker is doing, for the app's Conversions screen."""
    _state.update(kw)
    cur = {k: _state[k] for k in ("task", "since", "file", "step", "pct", "paused")}
    cur["pid"] = os.getpid()          # the app tells a worker that died mid-task (killed, crashed) by it
    try:
        rommlib.atomic_write_json(rommlib.chd_current_path(), cur, sync=False)        # rewritten every few seconds
    except OSError:
        pass


NOTE_TAGS = tuple(sorted({c.tag + ": " for c in rommlib.CONVERTERS}))     # "chd: ", "rvz: ", ...


def note(msg):
    """Log a line and remember it as the reason this task ended the way it did."""
    _state["last"] = msg.split(": ", 1)[-1] if msg.startswith(NOTE_TAGS) else msg
    rommlib.log(msg)


def record(task_name, task, result):
    """Append one finished task to chd-results.jsonl (kept short)."""
    entry = {"time": time.time(), "task": task_name, "result": result, "message": _state["last"][:300],
             "system": task.get("system"), "mode": task.get("mode"), "sources": task.get("sources"),
             "retry": task if result in ("failed", "stopped") and task else None}
    sizes = _state.pop("sizes", None)
    if result == "ok" and sizes:
        entry["in_size"], entry["out_size"] = sizes          # Compress's "after" estimate reads these
    try:
        lines = []
        results = rommlib.chd_results_path()
        if os.path.exists(results):
            with open(results) as f:
                lines = f.readlines()[-(RESULTS_KEEP - 1):]
        lines.append(json.dumps(entry) + "\n")
        rommlib.atomic_write_text(results, "".join(lines))
    except OSError:
        pass


PCT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)%")


def chdman(*args):
    """Run chdman at nice 19; pause it (SIGSTOP) whenever a game starts."""
    return run_tool(["chdman"] + list(args))


def dolphin_tool(*args):
    """Run dolphin-tool like chdman, with its own throwaway Dolphin user folder
    (it would otherwise create one in Batocera's Dolphin config area), removed
    after the run so Clean up leftovers never finds it."""
    exe = rommlib.dolphin_tool_path()
    if not exe:
        raise ToolMissing("dolphin-tool")
    user = os.path.join(rommlib.tmp_dir(), "dolphin-user")
    os.makedirs(user, exist_ok=True)
    args = list(args)
    if args and args[0] in ("convert", "verify"):
        args[1:1] = ["-u", user]
    try:
        return run_tool([exe] + args, keep_stdout=True)
    finally:
        shutil.rmtree(user, ignore_errors=True)


def _die_with_parent():
    """In the child, before exec: the kernel kills it if this worker dies (even
    SIGKILL), so no converter runs on unpaused and unguarded, or twice."""
    try:
        import ctypes
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, 9)     # PR_SET_PDEATHSIG, SIGKILL
    except Exception:
        pass


def run_tool(argv, keep_stdout=False):
    """Run a converter at nice 19; pause it (SIGSTOP) whenever a game starts;
    stop it if memory runs out (MemoryGuard). Returns (rc, last output)."""
    cmd = ["nice", "-n", "19"] + list(argv)   # no ionice in Batocera
    # stderr goes to a file, never a pipe: chdman prints progress all the way
    # through, and a pipe nobody drains until exit fills up (64 KB) and blocks
    # it forever — that froze long DVD conversions (2026-10-10).
    # The tool writes through its own append-only handle: sharing ours (one
    # file offset) let its writes land wherever we had just seeked to read
    # progress, overwriting text and losing the last lines.
    import tempfile
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    fd, path = tempfile.mkstemp(dir=rommlib.DATA_DIR, prefix="tool-", suffix=".log")
    os.close(fd)
    try:
        outf = open(path, "a")
        errf = open(path, "r", errors="replace")       # reads start at any byte offset
    finally:
        os.unlink(path)                           # both handles keep it until closed
    with outf:
        p = subprocess.Popen(cmd, stdout=outf if keep_stdout else subprocess.DEVNULL, stderr=outf, text=True,
                             preexec_fn=_die_with_parent)
    paused = stopped = False
    guard = None
    try:
        while p.poll() is None:
            time.sleep(TOOL_POLL)
            try:                                  # progress for the Conversions screen
                errf.seek(0, 2)
                errf.seek(max(0, errf.tell() - 400))
                m = PCT_RE.findall(errf.read())
                if m:
                    status(pct=min(100.0, float(m[-1])))
            except (OSError, ValueError):
                pass
            if _new("stop_requested", _state["task"]):
                stopped = True
                break                             # finally: resume + stop the tool
            why = busy_reason()
            busy = why != ""
            guard = memory_check(p.pid, busy)
            if guard:
                break                             # finally: resume + stop chdman
            if busy and not paused:
                p.send_signal(signal.SIGSTOP)
                paused = True
                status(paused=why)
                note("chd: paused (%s)" % PAUSED[why])
            elif not busy and paused:
                p.send_signal(signal.SIGCONT)
                paused = False
                status(paused="")
                note("chd: resumed")
    finally:
        if p.poll() is None:          # we're going down (killed, error): never leave chdman frozen
            try:
                p.send_signal(signal.SIGCONT)
                p.terminate()
                p.wait(10)
            except Exception:
                p.kill()
    if paused:
        status(paused="")
    if guard or stopped:
        errf.close()
        raise guard or Stopped()
    if p.returncode == 127:
        raise ChdmanMissing() if argv[0] == "chdman" else ToolMissing(argv[0])
    errf.seek(0, 2)
    errf.seek(max(0, errf.tell() - 4000))
    err = errf.read().replace("\r", "\n")
    errf.close()
    return p.returncode, err.strip()[-600 if keep_stdout else -300:]


def sources_of(cue_or_gdi):
    """The cue/gdi itself plus every track file it references."""
    if not cue_or_gdi.lower().endswith((".cue", ".gdi")):
        return [cue_or_gdi]
    return [cue_or_gdi] + rommlib.sheet_sources(cue_or_gdi, strict=True)     # unreadable: OSError


# next release: rommlib.disc_info recognises the same tags; a local copy so a worker
# loaded by an older rommlib (an in-app update) tells discs apart the same way.
# "(Disc 1)", "(Disc 1 of 2)", "(Disk 2)", "(CD1)", "(CD 2)", "(DVD 2)", "(Disc A)" —
# never "(CD32)" (Amiga CD32) or "(DVD5)"/"(DVD9)" (disc types).
DISC_TAG = re.compile(r"\s*\((?!CD\s*32\)|DVD\s*[59]\))(?:Disc|Disk|CD|DVD)\s*([0-9]{1,2}|[A-H])"
                      r"(?:\s*of\s*[0-9]{1,2})?\)", re.I)
# a disc subfolder named only for its disc: "Game/Disc 1/", "Game/CD2/"
BARE_DISC = re.compile(r"^(?:Disc|Disk|CD|DVD)\s*[0-9]{1,2}$", re.I)


def disc_title(name):
    """The title of a disc-tagged name ("Game (USA)" for "Game (USA) (Disc 2)",
    tags after the disc tag kept), None when it names no disc."""
    m = DISC_TAG.search(name or "")
    if not m:
        return None
    return re.sub(r"\s{2,}", " ", (name[:m.start()] + name[m.end():]).strip())


_downloaded = [None, frozenset()]                # (installed.json's stat, the paths it records)


def downloaded_paths():
    """Every path installed.json records (what the app downloaded), re-read when it changes."""
    try:
        st = os.stat(os.path.join(rommlib.DATA_DIR, "installed.json"))
        sig = (st.st_mtime_ns, st.st_size, st.st_ino)
    except OSError:
        sig = None
    if sig != _downloaded[0]:
        try:
            recs = rommlib.Library().installed.values()
            _downloaded[1] = frozenset(os.path.normpath(p) for r in recs for p in (r.get("paths") or []))
        except Exception:
            _downloaded[1] = frozenset()
        _downloaded[0] = sig
    return _downloaded[1]


def folder_names_game(folder_root, srcs):
    """A folder's name is its game's (what its disc's CHD or playlist is called) when the app
    downloaded that folder, or its disc is called that too. Else the disc keeps its own name:
    "Pyramid Plunder/MyOther.cue" is MyOther, whatever the folder is called."""
    name = os.path.basename(folder_root).lower()
    if any(DISC_TAG.split(os.path.splitext(os.path.basename(s))[0], 1)[0].rstrip().lower() == name
           for s in srcs):
        return True
    return os.path.normpath(folder_root) in downloaded_paths()


def plan_outputs(system, mode, srcs, roots):
    """Where a task's outputs go: (folder_root, out_dir, [(src, out)]). A game
    folder's one disc becomes <folder>.chd in its parent; several discs, or a
    loose file, keep each sheet's own name. Discs whose sheets share a name in
    their own folders ("Game/CD1/game.cue", "Game/CD2/game.cue") are named by
    their folder: "Game (CD1)", "Game (CD2)". Two outputs can still be the same
    file (the caller skips such a task)."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, system)
    folder_root = next((r.rstrip("/") for r in roots if os.path.isdir(r)), None)
    if folder_root and os.path.normpath(folder_root) == os.path.normpath(sysdir):
        folder_root = None        # never treat the system dir itself as a game folder
    out_dir = os.path.dirname(folder_root) if folder_root else os.path.dirname(srcs[0])
    multi = len(srcs) > 1
    stems = [os.path.splitext(os.path.basename(src))[0] if multi or not folder_root
             or not folder_names_game(folder_root, srcs)
             else os.path.basename(folder_root)                 # game name, not track name
             for src in srcs]
    if multi:
        seen = {}
        for st in stems:
            seen[st.lower()] = seen.get(st.lower(), 0) + 1
        for i, src in enumerate(srcs):
            d = os.path.dirname(src)
            if seen[stems[i].lower()] > 1 and folder_root \
                    and os.path.normpath(d) != os.path.normpath(folder_root):
                disc = os.path.basename(d)
                stems[i] = "%s (%s)" % (os.path.basename(folder_root), disc) if BARE_DISC.match(disc) else disc
    plan = [(src, os.path.join(out_dir, rommlib.conversion_output(mode, st, src))) for src, st in zip(srcs, stems)]
    return folder_root, out_dir, plan


# An output this worker made records which sources it came from (in the data
# folder, never beside the game): after an interruption only such an output is
# finished off. Any other file already at that name is left alone, and the
# task skipped: it may be another game's, or a copy the owner keeps.
MADE_DIR = "chd-made"


def _made_path(out):
    key = hashlib.sha1(os.path.normpath(out).encode("utf-8", "surrogateescape")).hexdigest()
    return os.path.join(rommlib.DATA_DIR, MADE_DIR, key + ".json")


def same_time(a, b):
    """Two modification times of one file: equal within FAT's 2 s, or apart by a
    whole number of quarter hours (a FAT drive keeps local time: a box booted in
    another time zone, or across a DST change, sees every file shifted)."""
    d = abs(a - b)
    return d < 2.5 or (d < 15 * 3600 and abs(d - round(d / 900) * 900) < 2.5)


def mark_made(out, srcs, tmp):
    """Record, before tmp becomes out, that this task's srcs make it."""
    st = os.stat(tmp)
    path = _made_path(out)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rommlib.atomic_write_json(path, {"out": os.path.normpath(out), "sources": [os.path.normpath(s) for s in srcs],
                                     "size": st.st_size, "mtime": st.st_mtime})


def _record_matches(path, out):
    with open(path) as f:
        rec = json.load(f)
    st = os.stat(out)
    return rec if rec.get("out") == os.path.normpath(out) and rec.get("size") == st.st_size \
        and same_time(rec.get("mtime", 0), st.st_mtime) else None


def made_from(out, src):
    """True when out is the file a run converting src made (its record matches
    the file there now: same size and time)."""
    try:
        rec = _record_matches(_made_path(out), out)
        return bool(rec) and os.path.normpath(src) in rec.get("sources", ())
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def already_here(out):
    """Why a game whose output name a file already has is skipped (the planner's reason
    too): that file may be another copy of the game, or another disc's or game's."""
    return "a file named %s is already here (another copy of it, or another disc or game)" \
        % os.path.basename(out)


def unmark(out):
    try:
        os.remove(_made_path(out))
    except OSError:
        pass


PRUNE_AFTER = 30 * 86400     # a record whose drive hasn't been back this long: dropped


def _prune_made():
    """Records whose output is gone (dropped, deleted since): removed. Only when
    the output's folder is there to look in: a drive that mounts late (or is
    unplugged for now) keeps its records, unless they're a month old."""
    d = os.path.join(rommlib.DATA_DIR, MADE_DIR)
    try:
        names = os.listdir(d)
    except OSError:
        return
    for n in names:
        p = os.path.join(d, n)
        try:
            with open(p) as f:
                out = json.load(f).get("out")
            if out and os.path.lexists(out):
                continue
            if out:
                try:
                    os.listdir(os.path.dirname(out))       # its drive is there: the output really is gone
                except OSError:
                    if time.time() - os.path.getmtime(p) < PRUNE_AFTER:
                        continue
        except (OSError, ValueError, AttributeError):
            pass
        try:
            os.remove(p)
        except OSError:
            pass


def _also_owner(sheet, srcs):
    """Which of srcs a sheet the planner set aside belongs to (a .cue beside the
    .gdi of the same game): the one that names every track the sheet names; None
    when none does (another game's sheet, or a second dump with tracks of its own:
    never taken)."""
    tracks = {os.path.normpath(p) for p in rommlib.sheet_sources(sheet)}
    if not tracks:
        return None
    for s in srcs:
        if tracks <= {os.path.normpath(p) for p in sources_of_quiet(s)[1:]}:
            return s
    return None


def sources_of_quiet(src):
    """sources_of, an unreadable sheet as just itself."""
    try:
        return sources_of(src)
    except OSError:
        return [src]


# what each cue/gdi names, by (path, size, mtime, inode), for this worker's run: a
# flat system folder holds thousands, and every converted game asks again (re-read
# from an SD card, that's tens of seconds a game). An edited sheet has a new key
_SHEETS = {}


def _sheet_tracks(sh):
    """The normalised paths a cue/gdi names (none when it isn't a file)."""
    try:
        st = os.stat(sh)
    except OSError:
        return ()
    if not stat.S_ISREG(st.st_mode):
        return ()
    key = (os.path.normpath(sh), st.st_size, st.st_mtime_ns, st.st_ino)
    got = _SHEETS.get(key)
    if got is None:
        got = tuple(os.path.normpath(p) for p in rommlib.sheet_sources(sh))
        if all(os.path.lexists(p) for p in got):
            # kept only when every track is there: a name that resolves to no file yet
            # (or only in another letter case) can resolve differently once one lands
            _SHEETS[key] = got
    return got


def still_named(srcs, dirs, walk_root=None):
    """Files (normalised paths) that a .cue/.gdi/.m3u other than srcs names, in
    dirs or anywhere under walk_root: Game.cue and Game (alt).cue sharing
    Game.bin. Converting one sheet must leave the shared track for the other; the
    run that converts the last sheet naming it deletes it."""
    mine = {os.path.normpath(s) for s in srcs}
    sheets = set()
    for d in dirs:
        try:
            sheets.update(os.path.join(d, f) for f in os.listdir(d))
        except OSError:
            pass
    if walk_root:
        for dp, dns, fns in os.walk(walk_root):
            dns[:] = [x for x in dns if x not in rommlib.MEDIA_DIRS]
            sheets.update(os.path.join(dp, f) for f in fns)
    named = set()
    for sh in sheets:
        low = sh.lower()
        if not low.endswith((".cue", ".gdi", ".m3u")) or os.path.normpath(sh) in mine \
                or os.path.basename(sh).startswith(CHDMAN_CUE):     # our copy for chdman (a killed run's)
            continue
        if low.endswith((".cue", ".gdi")):
            named.update(_sheet_tracks(sh))
        elif os.path.isfile(sh):
            try:
                with open(sh, errors="surrogateescape") as f:
                    lines = [ln.strip() for ln in f]
            except OSError:
                continue
            named.update(os.path.normpath(os.path.join(os.path.dirname(sh), ln))
                         for ln in lines if ln and not ln.startswith("#"))
    return named


def _crc32(out):
    m = re.search(r"CRC32:\s*([0-9a-f]{8})", out) or re.search(r"(?m)^([0-9a-f]{8})\s*$", out)
    return m.group(1) if m else None


def convert_rvz(src, tmp):
    """GameCube/Wii image -> RVZ with dolphin-tool. dolphin-tool's convert
    succeeds even on junk or a cut-short file, so the source is verified
    first (a non-disc fails), and the RVZ must hash to the source's CRC32
    before the caller deletes anything. Returns (rc, message)."""
    status(step="Checking the original", pct=None)
    rc, out = dolphin_tool("verify", "-i", src)
    if rc != 0:
        return 2, "not a GameCube/Wii disc image (dolphin-tool verify failed)"
    want = _crc32(out)
    if "Problems Found: Yes" in out:
        note("rvz: note %s: %s" % (os.path.basename(src),
                    " ".join(out.split("Problems Found: Yes", 1)[1].split())[:200]))
    if not want:
        rc, out = dolphin_tool("verify", "-i", src, "-a", "crc32")
        want = _crc32(out)
    if not want:
        return 3, "couldn't read the source's CRC32"
    status(step="Converting", pct=None)
    rc, out = dolphin_tool("convert", "-i", src, "-o", tmp, *rommlib.RVZ_ARGS)
    if rc != 0 or not os.path.exists(tmp):
        return rc or 4, out
    status(step="Verifying", pct=None)
    rc, out = dolphin_tool("verify", "-i", tmp, "-a", "crc32")
    got = _crc32(out)
    if rc != 0 or got != want:
        return 5, "RVZ doesn't match the original (CRC32 %s vs %s)" % (got, want)
    return 0, "CRC32 %s" % want


def _crc32_file(path):
    """CRC32 of a file, read the way the worker runs: paused while a game plays."""
    import zlib
    crc, n = 0, 0
    with open(path, "rb") as f:
        while True:
            _idle()
            chunk = f.read(8 << 20)
            if not chunk:
                return "%08x" % (crc & 0xffffffff)
            crc = zlib.crc32(chunk, crc)
            n += len(chunk)


def convert_squashfs(src, tmp, threads):
    """Xbox image -> zstd squashfs that Batocera mounts at launch. Inside: the
    image (an XISO: a full redump image is cut to its game partition first,
    as xemu needs; refused if that's turned off) and a .ROM link to it, so Batocera hands xemu the file,
    not the folder. Mounted read-only afterwards: the image inside must hash
    to the original's CRC32 before the caller deletes anything."""
    status(step="Checking the original", pct=None)
    off = rommlib.xbox_partition(src)
    if off is None:
        return 2, "not an Xbox disc image"
    if off and not rommlib.load_config().get("xbox_bootable", rommlib.DEFAULT_CONFIG["xbox_bootable"]):
        # packing a full redump image would only make a smaller file xemu can't boot
        return 2, "a full disc image xemu can't boot (Settings: Make Xbox images bootable)"
    try:
        cut = rommlib.make_xiso(src)
    except (rommlib.NoSpace, OSError) as e:
        return 3, str(e)
    if cut:
        note("squashfs: %s cut to its game partition (-%s)" % (os.path.basename(src), rommlib.human_size(cut)))
    want = _crc32_file(src)
    status(step="Packing", pct=None)
    name = os.path.basename(src)
    rc, out = run_tool(["mksquashfs", src, tmp, *rommlib.SQUASH_ARGS, "-progress", "-processors", threads,
                        "-p", ".ROM s 777 0 0 %s" % name], keep_stdout=True)
    if rc != 0 or not os.path.exists(tmp):
        return rc or 4, out
    status(step="Verifying", pct=None)
    mnt = os.path.join(rommlib.tmp_dir(), "squashfs-check-%d" % os.getpid())
    os.makedirs(mnt, exist_ok=True)
    if subprocess.run(["mount", "-o", "loop,ro", tmp, mnt], capture_output=True).returncode != 0:
        os.rmdir(mnt)
        return 5, "couldn't mount the new squashfs"
    try:
        inner = os.path.join(mnt, name)
        ok_link = os.path.realpath(os.path.join(mnt, ".ROM")) == os.path.realpath(inner)
        got = _crc32_file(inner) if os.path.isfile(inner) else None
    finally:
        subprocess.run(["umount", mnt], capture_output=True)
        try:
            os.rmdir(mnt)
        except OSError:
            pass
    if got != want or not ok_link:
        return 5, "squashfs doesn't match the original (CRC32 %s vs %s%s)" % (
            got, want, "" if ok_link else ", .ROM link missing")
    return 0, "CRC32 %s" % want


def _idle():
    """Between frames of an in-process conversion: wait while a game runs
    (the subprocess tools get SIGSTOP instead)."""
    was = False
    while True:
        if _new("stop_requested", _state["task"]):
            raise Stopped()
        why = busy_reason()                # ES can't be asked: treated as busy
        if not why:
            if was:
                status(paused="")
            return
        was = True
        status(step="Paused while " + PAUSED[why], paused=why)
        time.sleep(10)


def convert_z3ds(src, tmp, threads):
    """Decrypted 3DS cartridge image -> Z3DS (.zcci/.zcxi) that Azahar opens
    directly. Written with the box's libzstd, then read back the way Azahar
    reads it (seek table, every frame decoded): the result must hash to the
    original's CRC32 before the caller deletes anything."""
    status(step="Checking the original", pct=None)
    try:
        rommlib.z3ds_info(src)
    except rommlib.Z3dsError as e:
        return 2, str(e)
    except OSError as e:
        return 2, "can't read it (%s)" % e
    def shown(step):
        last = [-1]

        def progress(frac):              # a frame is 256 KB: publish whole percents only
            pct = int(frac * 100)
            if pct != last[0]:
                last[0] = pct
                status(step=step, pct=pct)
        return progress
    try:
        want = rommlib.z3ds_compress(src, tmp, threads, pause=_idle, progress=shown("Compressing"))
        got, size = rommlib.z3ds_verify(tmp, pause=_idle, progress=shown("Verifying"))
    except (rommlib.Z3dsError, OSError) as e:
        return 4, str(e)
    if got != want or size != os.path.getsize(src):
        return 5, "the compressed copy doesn't match the original (CRC32 %s vs %s)" % (got, want)
    return 0, "CRC32 %s" % want


def _sync_dir(path):
    """Make a directory's entries (renames, new files) durable."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass                  # some filesystems refuse fsync on a directory
    finally:
        os.close(fd)


def _tree(root):
    """{relative path: absolute path} of every file under root."""
    out = {}
    for dp, _, fns in os.walk(root):
        for fn in fns:
            p = os.path.join(dp, fn)
            out[os.path.relpath(p, root)] = p
    return out


# A few retail Xbox 360 discs hold no game of their own: default.xex is a small
# stub that asks the console to start a package on the disc (an arcade title,
# content/0000000000000000/<title id>/000D0000/<name>), with the game's add-ons
# and update as more packages beside it. xenia-canary can't follow that from a
# disc Batocera names on its command line: it writes down what to start, asks
# to be restarted, and Batocera starts the stub again, every time, from the
# .iso and a .zar alike. Such a disc becomes what Batocera does start: its
# packages in a "Game.packages" folder and a one-line "Game.xbox360" launcher
# naming the title's package in it. The game's add-ons and update are unpacked
# there (xenia-canary reads those only as folders of files, under its content
# folder in saves/xbox360) and linked from that content folder, so the game
# finds them with nothing to install by hand.
PACKAGE_DIR_EXT = ".packages"
PACKAGE_LAUNCHER_EXT = ".xbox360"
PACKAGE_STUB_MAX = 1 << 20      # a launcher stub is tens of KB; a game's own default.xex is megabytes
PACKAGE_TITLE = 0x000D0000      # the content type of a package that is a game (an arcade title)
_PACKAGE_RE = re.compile(r"^content/0000000000000000/[0-9a-f]{8}/([0-9a-f]{8})/[^/]+$", re.I)
_PACKAGE_KEEP = ("content/", "$titleupdate/")      # what the folder keeps: the packages and the game's update
_PACKAGE_OF = {}                # this run's {".part" folder or launcher: the title's package in it / its folder}
_PACKAGE_LINKS = {}             # this run's {".part" folder: [(unpacked folder in it, its place in xenia's content)]}
PACKAGE_ADDON, PACKAGE_UPDATE = 0x00000002, 0x000B0000        # the content types xenia reads as unpacked folders
_ADDON_RE = re.compile(r"^content/0000000000000000/([0-9a-f]{8})/00000002/([^/]+)$", re.I)
_UPDATE_RE = re.compile(r"^\$titleupdate/([0-9a-f]{8})/([^/]+)$", re.I)
XENIA_CONTENT = os.path.join("xbox360", "0000000000000000")   # in saves/: content every profile shares
# next release: rommlib.stfs_kind, stfs_unpack, StfsError (romm/stfs.py); rommlib.SAVES_ROOT (romm/core.py).
# A worker of its own always has them; an older rommlib still in memory during an update has none,
# and then the add-ons stay packed (the game starts; they aren't found until it's converted again)
_stfs_kind = getattr(rommlib, "stfs_kind", None)
_stfs_unpack = getattr(rommlib, "stfs_unpack", None)
_StfsError = getattr(rommlib, "StfsError", OSError)


def _content_place(rel, path):
    """Where xenia looks for the add-on or update package at rel on the disc
    (a path under its shared content folder), or None for any other file:
    only a package whose header says the same type and title as its place."""
    for rx, kind in ((_ADDON_RE, PACKAGE_ADDON), (_UPDATE_RE, PACKAGE_UPDATE)):
        m = rx.match(rel.replace(os.sep, "/"))
        if m and _stfs_kind is not None and _stfs_kind(path) == (kind, int(m.group(1), 16)):
            return os.path.join(m.group(1).upper(), "%08X" % kind, m.group(2))
    return None


def link_packages(folder, links):
    """Link a landed packages folder's unpacked add-ons and update into
    xenia's content folder, where it looks for them. Never over anything there
    but a link that leads nowhere (a game deleted earlier): an add-on installed
    from xenia's own menu stays as it is. Returns how many are in place."""
    done = 0
    saves = getattr(rommlib, "SAVES_ROOT", "/userdata/saves")
    for rel, place in links:
        target, link = os.path.join(folder, rel), os.path.join(saves, XENIA_CONTENT, place)
        try:
            if os.path.islink(link) and not os.path.exists(link):
                os.remove(link)
            if os.path.lexists(link):
                same = os.path.realpath(link) == os.path.realpath(target)
                if not same:
                    note("chd: kept %s: xenia already has that add-on" % link)
                done += same
                continue
            os.makedirs(os.path.dirname(link), exist_ok=True)
            os.symlink(target, link)
            done += 1
        except OSError as e:
            note("chd: couldn't link %s for xenia (%s): that add-on won't be found" % (
                os.path.basename(rel), e.strerror or e))
    return done


def package_disc(listing):
    """The one title package a disc's stub starts (its path on the disc), or
    None for a disc that is a game itself. listing: {path: size}, the disc's
    files. Only when the answer is plain: a small default.xex, no other
    program on the disc, and exactly one title package."""
    low = {p.lower(): p for p in listing}
    stub = low.get("default.xex")
    if stub is None or listing[stub] > PACKAGE_STUB_MAX \
            or any(p.endswith(".xex") and p != "default.xex" for p in low):
        return None
    titles = [p for l, p in low.items() if _PACKAGE_RE.match(l)
              and int(_PACKAGE_RE.match(l).group(1), 16) == PACKAGE_TITLE]
    return titles[0] if len(titles) == 1 else None


def _title_package(path):
    """path is a signed Xbox 360 package (CON, LIVE or PIRS) holding a game."""
    try:
        with open(path, "rb") as f:
            head = f.read(0x348)
    except OSError:
        return False
    return head[:4] in (b"CON ", b"LIVE", b"PIRS") and len(head) == 0x348 \
        and int.from_bytes(head[0x344:0x348], "big") == PACKAGE_TITLE


def _package_folder(work, files, crcs, title, tmp):
    """The packages of a disc that only starts one (package_disc), moved from
    the extracted files to the folder tmp; each compared with its CRC32 where
    it landed, and on disk before the caller deletes the image."""
    _drop(tmp)                                      # half a folder from an interrupted run
    keep = sorted(r for r in files if r.lower().startswith(_PACKAGE_KEEP))
    for i, r in enumerate(keep):
        status(step="Keeping the game's packages", pct=round(100 * i / len(keep)))
        to = os.path.join(tmp, r)
        os.makedirs(os.path.dirname(to), exist_ok=True)
        shutil.move(files[r], to)
        if _crc32_file(to) != crcs[r]:
            return 5, "a package didn't copy whole (%s)" % r
        rommlib.sync_file(to)
    # add-ons and the update: unpacked where they are, each block checked against the package's own SHA-1
    links = []
    for i, r in enumerate(keep):
        to = os.path.join(tmp, r)
        place = None if r == title else _content_place(r, to)
        if not place:
            continue

        def progress(frac, i=i):
            status(step="Unpacking the game's add-ons", pct=round(100 * (i + frac) / len(keep)))
        progress(0)
        _drop(to + ".unpacking")
        try:
            _stfs_unpack(to, to + ".unpacking", progress=progress, pause=_idle)
        except _StfsError as e:
            return 5, "the package %s %s" % (r, e)
        os.remove(to)
        os.rename(to + ".unpacking", to)
        links.append((r, place))
    for dp, _, _ in os.walk(tmp):
        _sync_dir(dp)
    _PACKAGE_OF[tmp] = title
    _PACKAGE_LINKS[tmp] = links
    return 0, "%d packages, CRC32 of each checked, %d unpacked for xenia" % (len(keep), len(links))


def convert_zar(src, tmp):
    """Xbox 360 disc image -> .zar that xenia-canary opens directly: the game
    partition's files (extract-xiso), packed with zarchive. Neither tool's exit
    code is trusted alone: the extracted files must match the image's own file
    list (extract-xiso exits 0 on a cut-short image), the archive's footer and
    SHA-256 must check out (zarchive never checks them), and the archive is
    unpacked again and every file's CRC32 compared before the caller deletes
    the image. The .zar holds the game's files, not the disc's other
    partitions, so it can't be turned back into the original image. A disc
    that only starts a package (package_disc) leaves a folder of its packages
    at tmp instead, for the caller to land with a launcher; a .zar no smaller
    than the image is no gain, and the image stays."""
    tools = rommlib.x360_tools()
    if not tools:
        raise ToolMissing("xbox360-tools")
    xiso, zarc = tools
    status(step="Checking the original", pct=None)
    want = rommlib.xiso_listing(xiso, src)
    if not want:
        return 2, "not an Xbox 360 disc image (extract-xiso can't read it)"
    if not any(p.lower() == "default.xex" for p in want):
        return 2, "no default.xex at the top of the disc: not a game disc xenia can start"
    work = os.path.join(rommlib.tmp_dir(), "zar-%d" % os.getpid())
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(os.path.dirname(work), exist_ok=True)
    try:
        status(step="Extracting", pct=None)
        rc, out = run_tool([xiso, "-q", "-d", work, src])
        if rc != 0:
            return 3, "extract-xiso failed: %s" % out.strip()[-200:]
        files = _tree(work)
        got = {r: os.path.getsize(p) for r, p in files.items()}
        if got != want:
            short = sum(1 for r, n in want.items() if got.get(r) != n)
            return 3, "the image is cut short or damaged (%d of %d files don't match its file list)" % (
                short, len(want))
        crcs = {r: _crc32_file(p) for r, p in files.items()}
        title = package_disc(want)
        if title and _title_package(files[title]):
            return _package_folder(work, files, crcs, title, tmp)
        status(step="Packing", pct=None)
        rc, out = run_tool([zarc, work, tmp], keep_stdout=True)     # zarchive reports errors on stdout
        if rc != 0 or not os.path.exists(tmp):
            return 4, "zarchive failed (rc %d): %s" % (rc, out.strip()[-200:])
        status(step="Verifying", pct=None)
        bad = rommlib.zar_check(tmp, full=True)
        if bad:
            return 5, ".zar %s" % bad
        shutil.rmtree(work, ignore_errors=True)
        rc, out = run_tool([zarc, tmp, work], keep_stdout=True)
        back = _tree(work) if rc == 0 else {}
        if set(back) != set(crcs) or any(_crc32_file(back[r]) != c for r, c in crcs.items()):
            return 5, ".zar doesn't unpack to the disc's files (rc %d)" % rc
        if os.path.getsize(tmp) >= os.path.getsize(src):
            return 6, "the .zar would be no smaller than the image (%s): the image stays as it is" % \
                rommlib.human_size(os.path.getsize(tmp))
        return 0, "%d files, CRC32 of each checked" % len(crcs)
    finally:
        shutil.rmtree(work, ignore_errors=True)


WUA_FAIL = {2: "not a Wii U game disc wuatool can read", 3: "no disc key for it (Cemu's keys.txt in bios/cemu, "
            "or bios/wiiukeys)", 5: "the image is damaged or cut short (a block doesn't match its hash)",
            6: "the .wua couldn't be written (disk full?)"}


def convert_wua(src, tmp):
    """Wii U disc image (.wud/.wux) -> .wua that Cemu opens directly: the game
    partition's files, decrypted with the disc's key (the user's own, gathered
    into a key list only this user can read, deleted after) and packed with
    ZArchive by wuatool. Its pack exits 0 only when every block it read matched
    its hash; then wuatool verify checks the archive's SHA-256 and compares every
    file with the disc again before the caller deletes the image. The .wua holds
    the game partition (and the disc's update and DLC partitions, if it carries
    them, each its own title), not the whole disc, so it can't become the image
    again."""
    exe = rommlib.wuatool_path()
    if not exe:
        raise ToolMissing("wuatool")
    keys = os.path.join(rommlib.tmp_dir(), "wiiu-keys-%d.txt" % os.getpid())
    os.makedirs(os.path.dirname(keys), exist_ok=True)
    try:
        if not rommlib.wiiu_key_file(keys):
            return 3, WUA_FAIL[3]
        status(step="Packing", pct=None)
        rc, out = run_tool([exe, "pack", "-k", keys, src, tmp], keep_stdout=True)
        if rc != 0 or not os.path.exists(tmp):
            return rc or 4, WUA_FAIL.get(rc) or "wuatool failed (rc %d): %s" % (rc, out.strip()[-200:])
        titles = re.search(r"(?m)^packed \d+ files, \d+ bytes, as (.+)$", out)
        if titles and "," in titles.group(1):          # the disc's own update and DLC went in beside the game
            note("chd: %s: the .wua holds %s" % (os.path.basename(src), titles.group(1)))
        status(step="Verifying", pct=None)
        rc, out = run_tool([exe, "verify", "-k", keys, src, tmp], keep_stdout=True)
        if rc != 0:
            return 5, ".wua doesn't match the disc (rc %d): %s" % (rc, out.strip()[-200:])
        done = re.search(r"verified (\d+) files", out)
        return 0, "%s files, each compared with the disc" % (done.group(1) if done else "all")
    finally:
        try:
            os.remove(keys)
        except OSError:
            pass


WUD_SIZE = 25025314816        # every retail Wii U disc image (single layer)


def convert_wux(src, tmp):
    """Wii U disc image (.wud) -> .wux, the lossless container Cemu reads (each
    distinct 32 KB sector once), written by wuatool. No keys. Then wuatool
    expands the .wux through Cemu's own reader and compares every byte with the
    .wud before the caller deletes it; its CRC32 and MD5 go in the log."""
    exe = rommlib.wuatool_path()
    if not exe:
        raise ToolMissing("wuatool")
    if os.path.getsize(src) != WUD_SIZE:
        note("chd: note %s isn't a whole Wii U disc's size (%s, not %s): may be cut short; compressed as it is"
             % (os.path.basename(src), "{:,}".format(os.path.getsize(src)), "{:,}".format(WUD_SIZE)))
    status(step="Compressing", pct=None)
    rc, out = run_tool([exe, "wux", src, tmp], keep_stdout=True)
    if rc != 0 or not os.path.exists(tmp):
        return rc or 4, {2: "not a Wii U disc image", 5: "the image changed or can't be read to the end",
                         6: "the .wux couldn't be written (disk full?)"}.get(rc) or \
            "wuatool failed (rc %d): %s" % (rc, out.strip()[-200:])
    status(step="Verifying", pct=None)
    rc, out = run_tool([exe, "compare", src, tmp], keep_stdout=True)
    got = dict(l.split(" ", 1) for l in out.splitlines() if l.startswith(("crc32 ", "md5 ")))
    if rc != 0 or "crc32" not in got:
        return 5, ".wux doesn't expand to the image (rc %d): %s" % (rc, out.strip()[-200:])
    msg = "every byte compared with the .wud (CRC32 %s, MD5 %s)" % (got["crc32"], got.get("md5", "?"))
    note("chd: %s: %s" % (os.path.basename(src), msg))
    return 0, msg


def _data_sha1(out):
    """The CHD's "Data SHA1" from chdman info: the SHA-1 of the data it holds
    (for a DVD CHD, exactly the ISO's bytes; "SHA1" also covers metadata)."""
    m = re.search(r"(?mi)^\s*Data SHA1:\s*([0-9a-f]{40})\s*$", out)
    return m.group(1).lower() if m else None


def convert_cso(src, tmp, threads):
    """PSP .cso -> CHD: decoded (in-process, paused while a game runs) to a
    temporary ISO beside the CHD, which chdman packs like a PSP .iso. The CHD
    must pass chdman verify and hold exactly the decoded ISO (its Data SHA1)
    before the caller deletes the .cso. The temporary ISO never outlives this."""
    status(step="Checking the original", pct=None)
    try:
        rommlib.cso_info(src)
    except rommlib.CsoError as e:
        return 2, str(e)
    except OSError as e:
        return 2, "can't read it (%s)" % e
    iso = tmp[:-len(".part")] if tmp.endswith(".part") else tmp
    iso = os.path.splitext(iso)[0] + rommlib.CSO_TEMP_SUFFIX
    last = [-1]

    def progress(frac):
        pct = int(frac * 100)
        if pct != last[0]:
            last[0] = pct
            status(step="Decoding the .cso", pct=pct)
    try:
        try:
            want = rommlib.cso_decode(src, iso, pause=_idle, progress=progress)
        except rommlib.CsoError as e:
            return 3, str(e)
        except OSError as e:
            return 3, "couldn't decode it (%s)" % e
        with open(iso, "rb") as f:
            f.seek(0x8001)
            if f.read(5) != b"CD001":
                return 3, "the decoded image isn't a disc image (no ISO9660 header)"
        status(step="Converting", pct=None)
        codecs = codecs_for("cso", rommlib.load_config(), "psp")
        rc, err = chdman("createdvd", "-np", threads, *codecs, "-f", "-i", iso, "-o", tmp)
        if rc != 0 or not os.path.exists(tmp):
            return rc or 4, err
        status(step="Verifying", pct=None)
        rc, err = chdman("verify", "-i", tmp)
        if rc != 0:
            return rc, err
        try:
            info = subprocess.run(["chdman", "info", "-i", tmp], capture_output=True, text=True,
                                  errors="replace", timeout=300)      # reads the header only: instant
            got = _data_sha1(info.stdout) if info.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            got = None
        if got != want:
            return 5, "CHD doesn't hold the decoded image (SHA-1 %s vs %s)" % (got, want)
        return 0, "SHA-1 %s" % want
    finally:
        try:
            os.remove(iso)
        except OSError:
            pass


# the converters other than chdman, by task mode (which mode applies: rommlib.CONVERTERS)
def convert_ps3(src, tmp):
    """PS3 disc image (loose or the .iso in a zip, read in place) -> the
    "Name.ps3" game folder RPCS3 boots, decrypted with the disc's key when it's
    encrypted. Every file is written from the image; EBOOT.BIN and PARAM.SFO
    are checked, and a zip's checksum is checked by reading it to the end."""
    def progress(frac):
        status(step="Making the game folder", pct=round(frac * 100))
    _drop(tmp)                                      # half a folder from an interrupted run
    try:
        key = rommlib.ps3_to_folder(src, tmp, pause=_idle, progress=progress)
    except (rommlib.Ps3Error, OSError, zipfile.BadZipFile) as e:
        return 1, str(e)
    for dp, _, fns in os.walk(tmp):                 # on disk before the image is deleted
        for fn in fns:
            rommlib.sync_file(os.path.join(dp, fn))
        _sync_dir(dp)
    if key:
        note("chd: decrypted %s with the disc key %s" % (os.path.basename(src), key))
    return 0, ""


def _drop(path):
    """Remove a finished or partial output: a file, or a game folder (PS3); and
    the packages folder this run made beside a launcher (package_disc)."""
    unmark(path)
    beside = _PACKAGE_OF.pop(path, None)
    if beside and os.path.isabs(beside):
        _drop(beside)
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path, ignore_errors=True)
    elif os.path.lexists(path):
        os.remove(path)


RUNNERS = {
    "rvz": lambda src, tmp, threads: convert_rvz(src, tmp),
    "squashfs": lambda src, tmp, threads: convert_squashfs(src, tmp, threads),
    "z3ds": lambda src, tmp, threads: convert_z3ds(src, tmp, threads),
    "zar": lambda src, tmp, threads: convert_zar(src, tmp),
    "wua": lambda src, tmp, threads: convert_wua(src, tmp),
    "wux": lambda src, tmp, threads: convert_wux(src, tmp),
    "cso": lambda src, tmp, threads: convert_cso(src, tmp, threads),
    "ps3": lambda src, tmp, threads: convert_ps3(src, tmp),
}


def _size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def _wux_size(path):
    """The disc size a .wux's header gives (0x10), or None when it isn't one."""
    try:
        with open(path, "rb") as f:
            h = f.read(0x18)
    except OSError:
        return None
    return int.from_bytes(h[0x10:0x18], "little") if len(h) == 0x18 and h[:4] == b"WUX0" else None


def _wux_instead(mode, rc, system, src, plan):
    """Where the .wux goes when wuatool found no key for a .wud (rc 3: Python's
    cryptography module wasn't there to check before) and Wii U to .wux would
    take it; else None."""
    wux = rommlib.CONVERTER.get("wux")
    if mode != "wua" or rc != 3 or wux is None or len(plan) != 1 or not src.lower().endswith(".wud") \
            or not wux.enabled(rommlib.load_config(), system):
        return None
    out = os.path.splitext(plan[0][1])[0] + ".wux"
    return None if os.path.exists(out) else out


# chdman reads a cue's FILE names exactly as written, relative to the cue's folder:
# a byte-order mark, old Mac line ends (it never finishes), a name in another letter
# case or with Windows backslashes all fail ("couldn't find bin file"). Such a sheet
# is handed over as a cleaned copy beside it (a hidden name, gone when chdman is done)
CHDMAN_CUE = ".rb-chdman-"
CUE_FILE = re.compile(r'^([ \t]*FILE[ \t]+)(?:"([^"]+)"|(\S+))', re.I)


def _chdman_sheet(src):
    """A cleaned copy of cue src for chdman (its path), or None when src works as it
    is. The copy names the very files sheet_sources resolves, so what's packed is
    what's deleted after."""
    if not src.lower().endswith(".cue"):
        return None
    try:
        with open(src, "rb") as f:
            text = f.read(1 << 20).decode("utf-8", "surrogateescape")
    except OSError:
        return None
    clean = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    fix = text.startswith("\ufeff") or re.search(r"\r(?!\n)", text) is not None    # (chdman reads CRLF)
    d = os.path.dirname(src)
    tracks = rommlib.sheet_sources(src)          # each FILE line's file, in order
    lines = clean.split("\n")
    if sum(1 for ln in lines if CUE_FILE.match(ln)) != len(tracks):
        return None                              # read differently: leave it to chdman as it is
    it = iter(tracks)
    for i, ln in enumerate(lines):
        m = CUE_FILE.match(ln)
        if not m:
            continue
        name = m.group(2) or m.group(3)
        rel = os.path.relpath(next(it), d).replace(os.sep, "/")
        if rel != name:                          # another letter case, or backslashes
            lines[i] = '%s"%s"%s' % (m.group(1), rel, ln[m.end():])
            fix = True
    tmp = os.path.join(d, CHDMAN_CUE + os.path.basename(src))
    if not fix:
        if os.path.lexists(tmp):
            _gone(tmp)                           # a killed run's copy
        return None
    try:
        with open(tmp, "w", encoding="utf-8", errors="surrogateescape") as f:
            f.write("\n".join(lines))
    except OSError as e:                         # a folder it can't write to: chdman gets the original
        note("chd: note couldn't write a cleaned copy of %s (%s)" % (os.path.basename(src), e.strerror))
        _gone(tmp)
        return None
    return tmp


def _gone(path):
    try:
        os.remove(path)
    except OSError:
        pass


BSD_SUM = re.compile(r"^(?:MD5|SHA1)\s*\((.+)\)\s*=\s*[0-9a-f]+\s*$", re.I)


def _listed(path):
    """The file names a .sfv/.md5/.sha1 lists, or None when it can't be read as one."""
    try:
        if os.path.getsize(path) > 1 << 20:
            return None
        with open(path, encoding="utf-8-sig", errors="surrogateescape") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    names = []
    sfv = path.lower().endswith(".sfv")
    for ln in lines:
        ln = ln.strip()
        if not ln or ln.startswith((";", "#")):
            continue
        m = BSD_SUM.match(ln)
        if m:
            names.append(m.group(1))
            continue
        parts = ln.rsplit(None, 1) if sfv else ln.split(None, 1)
        if len(parts) != 2:
            return None
        name, digest = (parts[0], parts[1]) if sfv else (parts[1].lstrip("*"), parts[0])
        if not re.fullmatch(r"[0-9a-fA-F]{8,128}", digest):
            return None                           # not a checksum line: someone's own file
        names.append(name)
    return names or None


# what a kept game folder still holding one of these is still a game (rommlib.is_game_name's
# disc and cart files, for a UI older than it)
_GAME_FILE_EXTS = (".cue", ".bin", ".img", ".iso", ".gdi", ".ccd", ".mdf", ".mds", ".toc", ".chd", ".cso",
                   ".pbp", ".ecm", ".raw", ".m3u", ".zip", ".7z", ".rvz", ".wbfs", ".nrg", ".cdi")


def _still_a_game(path, system):
    """A file left in a converted game's kept folder is (part of) a game: the folder is
    still one, and its gamelist entry stays where it is."""
    test = getattr(rommlib, "is_game_name", None)      # next release: rommlib.is_game_name only
    if test is not None:
        try:
            return test(path, system)
        except Exception:
            pass
    return path.lower().endswith(_GAME_FILE_EXTS)


def _disposable(path, gone, made):
    """A file left in a game folder that may go with it once the game is one file:
    OS junk, a checksum list naming only the converted tracks, or a playlist
    (rewritten by fix_m3us) naming only the converted discs. gone/made: normalised,
    lower-case paths."""
    name = os.path.basename(path).lower()
    if name in OS_JUNK or name.startswith(("._", CHDMAN_CUE)):
        return True
    d = os.path.dirname(path)

    def resolve(n):
        return os.path.normpath(os.path.join(d, n.replace("\\", "/"))).lower()
    if name.endswith(CHECKSUM_EXTS):
        names = _listed(path)
        return bool(names) and all(resolve(n) in gone for n in names)
    if name.endswith(".m3u"):
        try:
            with open(path, encoding="utf-8-sig", errors="surrogateescape") as f:
                entries = [ln.strip() for ln in f.read().splitlines()]
        except OSError:
            return False
        entries = [e for e in entries if e and not e.startswith("#")]
        return bool(entries) and all(resolve(e) in made for e in entries)
    return False


def _move_playlist(src, to):
    """Write playlist src at to, its entries pointing at the same files from there."""
    d = os.path.dirname(src)
    with open(src, encoding="utf-8-sig", errors="surrogateescape") as f:
        lines = [ln.strip() for ln in f.read().splitlines()]
    lines = [os.path.relpath(os.path.normpath(os.path.join(d, ln.replace("\\", "/"))), os.path.dirname(to))
             if ln and not ln.startswith("#") else ln for ln in lines]
    rommlib.atomic_write_text(to, "\n".join(lines).strip("\n") + "\n", errors="surrogateescape")
    _sync_dir(os.path.dirname(to))


def convert(task):
    system, mode = task["system"], task["mode"]
    _PACKAGE_OF.clear()
    _PACKAGE_LINKS.clear()
    sysdir = os.path.join(rommlib.ROMS_ROOT, system)
    srcs = sorted(s for s in task["sources"] if os.path.exists(s))
    if not srcs:
        note("chd: SKIP %s: its files are gone" % os.path.basename((task["sources"] or ["?"])[0]))
        return False
    conv = rommlib.CONVERTER.get(mode)        # None: an unknown mode goes to chdman as it is
    # Wii U: decrypt to .wua or compress to .wux is settled now, by today's switches and keys
    # (getattr: an older rommlib still in memory during an update has no fallbacks)
    head = rommlib.conversion_for(system, srcs[0], enabled=False) if conv else None
    if head is not None and getattr(head, "fallback", "") and mode in (head.mode, head.fallback):
        taker, why, off = rommlib.settle(head, system, srcs[0], rommlib.load_config())
        if (not why or taker is not head) and taker.mode != mode:
            note("chd: %s %s instead of %s: %s" % (taker.label, os.path.basename(srcs[0]),
                                                   rommlib.CONVERTER[mode].label,
                                                   why or "%s takes it now" % head.setting_name))
            conv, mode = taker, taker.mode
            task["mode"] = mode                 # the results list shows what it became
    # CHD tasks run even with CHD turned off since they were queued; the others need their switch
    if conv and not conv.chd and not conv.enabled(rommlib.load_config(), system):
        on = getattr(conv, "switched_on", None)        # (an older rommlib in memory has none)
        if on and getattr(conv, "tool", None) is not None and on(rommlib.load_config(), system):
            raise ToolWaiting(conv.tool)               # switched on, its tool still coming: keep the task
        note("chd: SKIP %s %s (%s)" % (mode, os.path.basename(srcs[0]), conv.off_reason))
        return False
    if conv and conv.check:
        for src in srcs:
            try:
                conv.check(src)
            except rommlib.CHECK_ERRORS as e:
                # nothing to retry: an encrypted dump stays encrypted (and Azahar can't run it either)
                note("chd: SKIP %s %s: %s" % (mode, os.path.basename(src), e))
                return False
    # sheets the planner set aside for one of these (a .cue beside the game's .gdi)
    # are this game's too: they go with it. Only a sheet whose every track the game's
    # own sheet names: never another game's, or a second dump with tracks of its own
    also = {}
    for a in task.get("also") or ():
        if isinstance(a, str) and os.path.isfile(a) and a not in srcs \
                and a.lower().endswith((".cue", ".gdi")):
            owner = _also_owner(a, srcs)
            if owner is not None:
                also.setdefault(owner, []).append(a)
    extra = [a for v in also.values() for a in v]
    playing = running_game()
    if playing and playing in srcs + extra:
        note("chd: SKIP in use %s" % playing)
        return False
    folder_root, out_dir, plan = plan_outputs(system, mode, srcs, task["roots"])
    # every file a cue/gdi names must belong to this game: its folder, or the
    # sheet's own folder for a loose one. A sheet pointing elsewhere (another
    # game, or any file on the box) would get that file packed and then deleted.
    for src in srcs:
        home = os.path.realpath(folder_root or os.path.dirname(src))
        try:
            parts = sources_of(src)
        except OSError as e:
            note("chd: SKIP unreadable %s (%s)" % (os.path.basename(src), e.strerror))
            return False
        for p in parts:
            if not os.path.realpath(p).startswith(home + os.sep):
                note("chd: SKIP %s: a track is outside the game's folder (%s)" % (os.path.basename(src), p))
                return False
    multi = len(srcs) > 1
    if len({out.lower() for _, out in plan}) != len(plan):
        # two sources would become the same .chd: converting would overwrite
        # one with the other and then delete both originals
        note("chd: SKIP name clash (%s)" % ", ".join(os.path.basename(s) for s in srcs))
        return False
    for src in srcs:
        cut = rommlib.disc_incomplete(src)
        if cut and cut[2]:
            # an interrupted copy: a CHD of it would be just as broken (chdman's
            # verify only proves the CHD matches its source)
            note("chd: SKIP incomplete disc image %s (%d of %d sectors); "
                        "Check games can download it again" % (src, cut[0], cut[1]))
            return False
        if cut:          # only a file entry past the end (could be a dummy): convert, but say so
            note("chd: note %s may be cut short (a file reaches sector %d of %d)" % (src, cut[1], cut[0]))
    # an output already there that an interrupted run of this game made (killed after
    # it landed, before the rename was recorded): finish the job instead of skipping
    # forever. Only one its record proves this game made: any other file of that name
    # (another game's, an older worker's, a copy the owner keeps) is never touched,
    # replaced or taken; the task is skipped and the planner lists it with the reason
    resume = set()
    replacing = set()                            # a damaged or different copy a fresh download replaces

    def newer_than_sources(out, src):
        try:
            return os.path.getmtime(out) >= max(os.path.getmtime(p) for p in sources_of(src)
                                                 if os.path.exists(p))
        except (OSError, ValueError):
            return False
    for _, out in plan:
        if os.path.exists(out) and mode == "z3ds":
            # a fresh copy next to an existing .zcci (Check games' download again): keep the
            # .zcci only if it decodes cleanly to exactly this copy
            src = next(s for s, o in plan if o == out)
            try:
                same = rommlib.z3ds_verify(out)[0] == _crc32_file(src)
            except (rommlib.Z3dsError, OSError):
                same = False
            if not same:
                note("chd: replacing damaged or different %s" % os.path.basename(out))
                replacing.add(out)
                continue
            resume.add(out)                          # exactly this copy: just finish the job
            continue
        if os.path.exists(out) and mode == "wux" and _size(next(s for s, o in plan if o == out)) == WUD_SIZE \
                and _wux_size(out) != WUD_SIZE:
            # a full .wud beside a .wux of a cut-short one: a fresh copy came down. Never the
            # other way round (a good .wux beside a cut-short .wud stays)
            note("chd: replacing %s: made from a cut-short image" % os.path.basename(out))
            replacing.add(out)
            continue
        if os.path.lexists(out):
            cut = os.path.exists(out) and rommlib.disc_incomplete(out)
            if cut and cut[2]:                   # certainly cut short (not just a dummy entry past the end)
                note("chd: replacing incomplete %s" % os.path.basename(out))   # a fresh copy came down
                replacing.add(out)
                continue
            src = next(s for s, o in plan if o == out)
            if made_from(out, src):
                if conv and conv.chd and newer_than_sources(out, src) and chdman("verify", "-i", out)[0] == 0:
                    resume.add(out)
                    continue
                # ours, but it can't be finished (broken by a power cut, its sources newer, or a
                # format only made again): made again over it, or it'd be offered and skipped forever
                note("chd: replacing %s: made by an interrupted run, making it again" % os.path.basename(out))
                replacing.add(out)
                continue
            note("chd: SKIP %s: %s" % (os.path.basename(src), already_here(out)))
            return False
    cfg = rommlib.load_config()
    todo = [s for s, o in plan if o not in resume]  # outputs an earlier run made already take no more room
    need = sum(os.path.getsize(p) for s in todo for p in sources_of(s) if os.path.isfile(p))
    need *= conv.space_factor if conv else 1     # e.g. .zar: extracted files + archive beside the image
    if conv and conv.space:
        try:
            need = max(need, sum(conv.space(s) for s in todo))    # e.g. .cso: the decoded ISO + the CHD
        except rommlib.CHECK_ERRORS:
            pass                                  # checked above; the converter reports it
    free, keep = rommlib.free_bytes(out_dir), rommlib.keep_free_bytes(cfg)
    if free - need < keep:
        # the CHD sits next to the original until it's verified: wait for room
        note("chd: WAIT for space %s (needs ~%s, %s free, keeping %s free)" % (
            os.path.basename(srcs[0]), rommlib.human_size(need), rommlib.human_size(free),
            rommlib.human_size(keep)))
        _state["wait"], _state["room"] = "space", (out_dir, need)
        return None
    # what the sources are now: if one is replaced (a fresh download landing) or
    # deleted (Free up space) while this runs, nothing may be committed on top
    def sig(p):
        st = os.stat(p)
        return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
    before = {p: sig(p) for s in srcs + extra for p in sources_of_quiet(s) if os.path.exists(p)}
    made = []
    for src, out in plan:
        if out in resume:
            note("chd: using %s, made before an interruption" % os.path.basename(out))
            rommlib.sync_file(out)
            made.append(out)
            continue
        tmp = out + ".part"                    # not a name ES lists as a game while it's written
        t0 = time.time()
        threads = str(rommlib.chd_threads(rommlib.load_config()))    # read per disc: Settings apply next disc
        codecs = codecs_for(mode, rommlib.load_config(), system)
        try:
            status(file=os.path.basename(src), step="Converting", pct=None)
            run = RUNNERS.get(mode)
            if run:
                rc, err = run(src, tmp, threads)
                alt = _wux_instead(mode, rc, system, src, plan)
                if alt:                                # no key for it after all: a .wux, as settle would have
                    note("chd: WUX %s instead of WUA: %s" % (os.path.basename(src), err))
                    _drop(tmp)
                    conv, mode, out = rommlib.CONVERTER["wux"], "wux", alt
                    task["mode"] = mode
                    plan[0] = (src, out)
                    tmp = out + ".part"
                    rc, err = RUNNERS[mode](src, tmp, threads)
            else:
                sheet = _chdman_sheet(src)       # a copy chdman can read, when the owner's can't be
                try:
                    rc, err = chdman(mode, "-np", threads, *codecs, "-f", "-i", sheet or src, "-o", tmp)
                finally:
                    if sheet:
                        _gone(sheet)
                if rc == 0:
                    status(step="Verifying", pct=None)
                    rc, err = chdman("verify", "-i", tmp)
        except Stopped:
            note("chd: STOPPED %s: you stopped it; the original stays as it is" % os.path.basename(src))
            for m in made + [tmp]:
                _drop(m)
            raise
        except MemoryGuard as g:
            note("chd: STOPPED %s: %s; %s" % (os.path.basename(src), g,
                        "will try again later" if g.retry else "leaving it as it is"))
            for m in made + [tmp]:
                _drop(m)
            if g.retry:
                _state["wait"] = "memory"
                return None
            return False
        except BaseException:
            # stopped (SIGTERM raises SystemExit; the tool itself is stopped by
            # run_tool), a converter missing, or anything else: the half-written
            # output goes, never the sources; earlier discs' outputs too, or ES
            # would list them beside the originals they never replaced
            for m in made + [tmp]:
                try:
                    _drop(m)
                except OSError:
                    pass
            raise
        if rc != 0:
            note("chd: FAIL %s rc=%d %s" % (src, rc, err))
            for m in made + [tmp]:
                _drop(m)
            return False
        title = _PACKAGE_OF.pop(tmp, None)
        if title:
            # the disc only starts a package (package_disc): its packages folder lands
            # first, then the launcher naming the title's package takes the .zar's place
            folder = os.path.splitext(out)[0] + PACKAGE_DIR_EXT
            launcher = os.path.splitext(out)[0] + PACKAGE_LAUNCHER_EXT
            taken = None
            for q in (folder, launcher):
                if os.path.lexists(q) and made_from(q, src):
                    _drop(q)                       # an interrupted run of this game made it: made again
                if os.path.lexists(q):
                    taken = taken or q
            if taken:                              # anyone else's: never replaced
                note("chd: SKIP %s: %s" % (os.path.basename(src), already_here(taken)))
                for m in made + [tmp]:
                    _drop(m)
                return False
            try:
                mark_made(folder, srcs, tmp)
            except OSError:
                pass
            links = _PACKAGE_LINKS.pop(tmp, [])
            os.replace(tmp, folder)
            _sync_dir(out_dir)
            if links:
                note("chd: %d of %d add-ons and updates of %s linked for xenia" % (
                    link_packages(folder, links), len(links), os.path.basename(src)))
            plan[plan.index((src, out))] = (src, launcher)
            out, tmp = launcher, launcher + ".part"
            _PACKAGE_OF[tmp] = _PACKAGE_OF[out] = folder        # goes with the launcher if it's dropped
            rommlib.atomic_write_text(tmp, "%s/%s\n" % (os.path.basename(folder), title.replace(os.sep, "/")),
                                      errors="surrogateescape")
            note("chd: %s only starts a package: keeping its packages and a launcher, not a .zar"
                 % os.path.basename(src))
        # the output must be on disk before it takes its name, and before any original
        # is deleted: after a power cut a renamed-but-unflushed file comes back empty or
        # broken (seen on ext4). A folder output (PS3) was synced file by file as it was made
        if os.path.isfile(tmp):
            rommlib.sync_file(tmp)
        if os.path.lexists(out) and out not in replacing:
            # appeared while this disc converted (checked before): never replaced
            note("chd: SKIP %s: %s" % (os.path.basename(src), already_here(out)))
            for m in made + [tmp]:
                _drop(m)
            return False
        if out in replacing and os.path.isdir(out) and not os.path.islink(out):
            _drop(out)                         # a game folder (PS3) can't be replaced in one step
            if os.path.lexists(out):           # couldn't be removed all the way: never renamed over
                note("chd: FAIL %s: the old folder couldn't be removed" % os.path.basename(out))
                for m in made + [tmp]:
                    _drop(m)
                unmark(out)
                return False
        try:
            mark_made(out, srcs, tmp)          # before it lands: a resume must know it's this game's
        except OSError:
            pass                               # no record: an interruption means converting again
        os.replace(tmp, out)
        rommlib.sync_file(out)                 # (already flushed: cheap)
        _sync_dir(out_dir)
        made.append(out)
        note("chd: disc done %s (%.0fs)" % (os.path.basename(out), time.time() - t0))

    changed = []
    for p, was in before.items():
        try:
            now = sig(p)
        except OSError:
            changed.append(p)
            continue
        # squashfs cuts its own source to the game partition first: only its presence counts
        if mode != "squashfs" and now != was:
            changed.append(p)
    if changed:
        note("chd: SKIP %s changed or was deleted while converting; keeping what's there now"
             % os.path.basename(changed[0]))
        for m in made:
            try:
                _drop(m)
            except OSError:
                pass
        return False
    old_to_new = {os.path.normpath(s): o for s, o in plan}
    old_to_new.update({os.path.normpath(a): o for s, o in plan for a in also.get(s, ())})
    m3us = rommlib.fix_m3us(sysdir, old_to_new)
    # what goes with each disc: its sheet, and each track no other sheet or playlist
    # still names (after fix_m3us, so a playlist of these discs names their CHDs now)
    # and that really is this game's (inside its folder; checked above, but a
    # deletion is checked where it's made)
    shared = still_named(srcs + extra, {os.path.dirname(s) for s in srcs} | {out_dir}, folder_root)
    goes = {}
    for src in srcs:
        home = os.path.realpath(folder_root or os.path.dirname(src))
        parts = sources_of(src) + [p for a in also.get(src, ()) for p in sources_of_quiet(a)]
        goes[src] = [p for p in dict.fromkeys(parts) if p == src or (
            (p in also.get(src, ()) or os.path.normpath(p) not in shared)
            and os.path.realpath(p).startswith(home + os.sep))]
        for p in dict.fromkeys(parts):
            if p not in goes[src]:
                note("chd: keeping %s: another disc image still uses it" % os.path.basename(p)
                     if os.path.normpath(p) in shared else
                     "chd: keeping %s: not this game's file" % p)
    in_size = sum(os.path.getsize(p) for s in srcs for p in goes[s] if os.path.isfile(p))
    # the packages folder beside a launcher (package_disc) is the game's too
    beside = [f for f in dict.fromkeys(_PACKAGE_OF.get(m) for m in made) if f]
    out_size = sum(rommlib.path_size(m) for m in made + beside)
    hide = []
    moved = set()                                # playlists of the folder now written beside the CHDs
    # a playlist in the game's folder naming just these discs (the download's, or the owner's
    # own under a name of their choosing) moves out beside the CHDs as the entry: never deleted
    inner_list = None
    made_low = {os.path.normpath(m).lower() for m in made}
    for m in m3us:
        to = os.path.join(out_dir, os.path.basename(m))
        if folder_root and m.startswith(folder_root + "/") and _disposable(m, set(), made_low) \
                and not os.path.lexists(to) \
                and not [o for o in m3us if not o.startswith(folder_root + "/")]:   # one entry
            _move_playlist(m, to)
            moved.add(os.path.normpath(m))
            note("chd: moved playlist %s beside the CHDs" % os.path.relpath(m, folder_root))
            inner_list = to
            break
    if multi:
        # an existing playlist (now rewritten) stays the entry; else write one
        outside = [m for m in m3us if not (folder_root and m.startswith(folder_root + "/"))]
        if outside:
            primary = outside[0]
        elif inner_list:
            primary = inner_list
        else:
            name = task.get("name") or (os.path.basename(folder_root)
                                        if folder_root and folder_names_game(folder_root, srcs) else
                                        DISC_TAG.split(os.path.splitext(os.path.basename(srcs[0]))[0],
                                                       1)[0].rstrip())
            primary = os.path.join(out_dir, name + ".m3u")
            if os.path.exists(primary):
                # a playlist of that name belongs to another copy of the game:
                # never overwrite it — leave these discs listed individually
                note("chd: m3u exists, not overwriting %s" % primary)
                primary = None
            else:
                # whole or not at all: a cut-short playlist would never be rewritten (it exists)
                rommlib.atomic_write_text(primary, "\n".join(os.path.basename(m) for m in made) + "\n",
                                          errors="surrogateescape")
                _sync_dir(out_dir)
        hide = made if primary else []
        rommlib.migrate_saves(system, srcs[0], primary or made[0])
        for s, o in plan:
            rommlib.migrate_saves(system, s, o)
    else:
        primary = inner_list or made[0]
        hide = made if inner_list else []
        rommlib.migrate_saves(system, srcs[0], primary)
    # each disc's entry follows the playlist when there is one, else its own CHD
    # (an .m3u name already taken: the discs stay listed one by one)
    entry = {s: primary or o for s, o in plan}
    primary = primary or made[0]

    # record the rename before deleting anything: a crash after this leaves
    # duplicates at worst, never a CHD whose game info can't follow it
    for s, o in plan:
        # a PS1 LibCrypt patch (Game.sbi) works only beside the image, under its name
        sbi, to = os.path.splitext(s)[0] + ".sbi", os.path.splitext(o)[0] + ".sbi"
        if os.path.isfile(sbi) and not os.path.lexists(to):
            os.replace(sbi, to)
    rename = {}
    gone = set()
    for src in srcs:
        for p in goes[src]:
            rename[p] = entry[src]
            gone.add(os.path.normpath(p))
    folder_goes = False
    rest = spare = lists = []                    # lists: playlists in the folder naming only these discs' CHDs
    if folder_root and os.path.isdir(folder_root):
        rest = [os.path.join(dp, f) for dp, _, fs in os.walk(folder_root) for f in fs
                if os.path.normpath(os.path.join(dp, f)) not in gone]
        low_gone = {g.lower() for g in gone}
        # (a playlist only when there's one outside to list the game, or one CHD: the
        # discs listed one by one keep theirs)
        low_made = {os.path.normpath(m).lower() for m in made} if hide or not multi else set()
        # (a playlist goes only once it's beside the CHDs: moved out, or the one CHD's entry)
        spare = [f for f in rest if _disposable(f, low_gone, low_made)
                 and (not f.lower().endswith(".m3u") or os.path.normpath(f) in moved)]
        lists = [f for f in spare if f.lower().endswith(".m3u")]
        folder_goes = len(spare) == len(rest)
        for m in lists:
            rename[m] = primary                  # a second listing of the game: the CHD's entry now
    if folder_goes:
        rename[folder_root] = primary            # the whole game folder becomes this file
    elif folder_root and rest and not any(_still_a_game(f, system) for f in rest if f not in spare):
        # kept only for the user's own files (a manual, notes): no game is in it any more, so
        # an entry naming the folder (the download's, handed over later) follows the CHD
        rename[folder_root] = primary
    rommlib.append_gamelist({"system": system, "rename": rename, "hide": hide})
    # our record of the game points at the new file even when its folder stays for other files
    try:
        rommlib.Library().rename_paths(dict(rename, **{folder_root: primary}) if folder_root else rename,
                                       list(hide) + beside)
    except Exception as e:                        # installed.json can't be opened right now: the CHD is in and
        rommlib.log("chd: %s: couldn't update installed.json (%s); it still names the "
                    "game's old files" % (os.path.basename(primary), e))   # ES has its rename: finish
    for src in srcs:
        for p in goes[src]:
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
        if folder_goes:
            shutil.rmtree(folder_root, ignore_errors=True)
        else:
            # the owner's files (a manual, a cover, notes, another game) stay in it; only the
            # converted files went, and a playlist of them (else ES would list the game twice)
            for m in lists:
                try:
                    os.remove(m)
                except OSError:
                    pass
            note("chd: kept folder %s: it holds other files (%s)" % (
                folder_root, ", ".join(sorted(os.path.relpath(f, folder_root) for f in rest
                                              if f not in spare)[:5])))
    for m in made + beside:
        unmark(m)                                # finished: a later file of this name is someone else's
    _PACKAGE_OF.clear()
    _state["sizes"] = (in_size, out_size)
    note("chd: OK %s -> %s  (%s -> %s)" % (
        os.path.relpath(srcs[0], sysdir), os.path.relpath(primary, sysdir),
        rommlib.human_size(in_size), rommlib.human_size(out_size)))
    return True


def finalize_later():
    """Merge gamelist changes in a separate process. rb_finalize waits for ES
    to be idle — with RomM-o-Cera open that means until it closes — and this
    worker must not sit on chd.lock meanwhile (new conversions queued by
    downloads would wait behind it). rb_finalize is flock'd: extra spawns no-op."""
    rommlib.spawn_detached("rb_finalize.py")


def pending(skip=()):
    """A queued task (not one of skip: those waiting for their tool)."""
    try:
        return any(t.endswith(".json") and t not in skip for t in os.listdir(rommlib.CHD_QUEUE_DIR))
    except OSError:
        return False


def _sigterm(signum, frame):
    raise SystemExit(0)                         # runs the cleanups on the way out: the tool stopped, its output dropped


def main():
    signal.signal(signal.SIGTERM, _sigterm)
    os.umask(0o022)       # CHDs readable like any other rom, however this worker was started
    os.makedirs(rommlib.CHD_QUEUE_DIR, exist_ok=True)
    waiting = {}
    while True:
        with rommlib.try_lock(rommlib.chd_lock_path()) as fd:
            if fd is None:
                return                          # another worker has it
            gave_up = work(fd, waiting)
        if gave_up:
            if _state["wait"] == "space" and wait_for_room():
                continue                        # room now (or the queue changed): try again
            return                              # no chdman, or another worker waits already: tasks stay queued
        # a task queued while we were giving up the lock would find it taken
        # and not start a worker: look once more after releasing it (the app
        # starts one again once a missing tool is here)
        _readmit(waiting)
        if not pending(waiting):
            return


ROOM_POLL = 30        # seconds between free-space checks while a conversion waits for room


def _queue_head():
    try:
        return min((t for t in os.listdir(rommlib.CHD_QUEUE_DIR) if t.endswith(".json")), default=None)
    except OSError:
        return None


def wait_for_room(sleep=None):
    """A conversion is waiting for space: wait here, without the conversion lock
    (Clean up leftovers and Start now aren't blocked by a worker that's only
    waiting), until there's room for it however it was freed (in the app, over
    the network, by hand), or the queue changes. True: look at the queue again;
    False: another worker waits already (its own lock), or the queue is empty."""
    head = _queue_head()
    out_dir, need = _state.get("room") or (rommlib.ROMS_ROOT, 0)
    with rommlib.try_lock(os.path.join(rommlib.DATA_DIR, "chd-wait.lock")) as fd:
        if fd is None:
            return False
        while True:
            (sleep or time.sleep)(ROOM_POLL)
            now = _queue_head()
            if now is None:
                return False
            if now != head:
                return True                     # put first, taken out, or done by another worker
            cfg = rommlib.load_config()
            if rommlib.free_bytes(out_dir) - need >= rommlib.keep_free_bytes(cfg):
                return True


def _wait(waiting, name, spec, cur):
    """Keep a task queued until its tool is here (logged once per tool)."""
    if all(s is not spec for s in waiting.values()):    # the tasks behind it still run
        note("chd: %s isn't installed yet; its conversions stay queued until the app has fetched it" % spec.slug)
    waiting[name] = spec
    try:
        os.remove(cur)
    except OSError:
        pass


def _readmit(waiting):
    """Tasks whose tool has arrived meanwhile run again (one check per tool)."""
    here = {}
    for name, spec in list(waiting.items()):
        if here.setdefault(id(spec), bool(spec.installed())):
            del waiting[name]


def work(fd, waiting=None):
    since_finalize = 0
    waiting = {} if waiting is None else waiting   # tasks whose tool isn't here yet, this run: name -> tool
    try:
        os.remove(rommlib.chd_current_path())   # left by a killed run
    except OSError:
        pass
    for left in glob.glob(os.path.join(rommlib.tmp_dir(), "wiiu-keys-*.txt")):
        try:
            os.remove(left)                     # a Wii U key list a killed run (power cut, kill -9) left
        except OSError:
            pass
    _drop_old_tools()
    _prune_made()
    while True:
        _readmit(waiting)
        tasks = sorted(t for t in os.listdir(rommlib.CHD_QUEUE_DIR) if t.endswith(".json") and t not in waiting)
        if not tasks:
            if not since_finalize:
                break
            finalize_later()        # merge what's done (once ES is idle), in its own process
            break
        why = busy_reason()
        if why:
            time.sleep(3 if why == "hold" else 20)      # a resume shows at once
            continue
        path = os.path.join(rommlib.CHD_QUEUE_DIR, tasks[0])
        cur = rommlib.chd_current_path()
        task = None              # never the previous task's: a broken file must not inherit it
        try:
            with open(path) as f:
                task = json.load(f)
            _state["last"] = ""
            _state.pop("sizes", None)
            status(task=tasks[0], since=time.time(), file=os.path.basename((task.get("sources") or [""])[0]),
                   step="Starting", pct=None, paused="")
            if _new("stop_requested", tasks[0]):
                raise Stopped()
            _state["wait"] = ""
            res = convert(task)
            if res is None:                   # memory needed by a game, or no room: keep it, try later
                record(tasks[0], task, "waiting")
                try:
                    os.remove(cur)
                except OSError:
                    pass
                if _state["wait"] == "memory":
                    time.sleep(MEMORY_RETRY)  # a game needs the memory: look again in a while
                    continue
                return True                   # no room: main() waits for some (wait_for_room)
            record(tasks[0], task, "ok" if res else ("skipped" if "SKIP" in _state["last"] or
                                                     "skip" in _state["last"].lower() else "failed"))
            if res:
                since_finalize += 1
        except Stopped:
            if not _state["last"].startswith("STOPPED"):
                note("chd: STOPPED %s: you stopped it; the original stays as it is" % tasks[0])
            record(tasks[0], task, "stopped")
        except ToolWaiting as e:
            _wait(waiting, tasks[0], e.args[0], cur)
            continue
        except ToolMissing as e:
            conv = rommlib.CONVERTER.get(task.get("mode")) if isinstance(task, dict) else None
            if conv is not None and _switched_on(conv, task.get("system")) and not conv.tool.installed():
                _wait(waiting, tasks[0], conv.tool, cur)    # gone mid-task: keep the task, never drop it
                continue
            note("chd: %s isn't installed; dropping %s (Settings, %s)" % (
                e, tasks[0], conv.setting_name if conv else rommlib.CONVERTER["rvz"].setting_name))
        except ChdmanMissing:
            note("chd: chdman not found; leaving %d task(s) queued" % len(tasks))
            try:
                os.remove(cur)
            except OSError:
                pass
            return True
        except Exception as e:
            note("chd: error on %s: %r" % (tasks[0], e))
            record(tasks[0], task if isinstance(task, dict) else {}, "failed")
        try:
            os.remove(path)
        except FileNotFoundError:           # taken out of the queue (Conversions) while it ran
            pass
        try:
            os.remove(cur)
        except OSError:
            pass
        _new("clear_stop")                    # a stop for a task that finished first must not linger
        if since_finalize >= FINALIZE_EVERY:
            finalize_later()
            since_finalize = 0


if __name__ == "__main__":
    main()
