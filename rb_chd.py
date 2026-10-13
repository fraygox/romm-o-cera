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
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rommlib  # noqa: E402

JUNK_EXTS = {".txt", ".nfo", ".md5", ".sfv", ".sha1", ".jpg", ".jpeg", ".png", ".m3u",
             ".dat", ".url", ".log", ".xml", ".pdf", ".cfg", ".ini", ".lic"}
FINALIZE_EVERY = 15   # merge gamelist renames + reload ES after this many games
TOOL_POLL = 3         # seconds between progress / running-game / memory checks while a converter runs


def running_game():
    """Path of the running game ('' when idle or only the browser runs);
    None when ES can't be asked (treated as busy)."""
    return rommlib.es_running_game()


def busy_reason():
    """Why conversions wait: "" they may run, "game" a game runs (or ES
    can't be asked), "es" ES scrapes, hashes, installs or updates (/isIdle,
    Batocera 36+; without it the running game alone decides). The browser
    alone counts as idle (rommlib.es_busy without app_busy)."""
    return rommlib.es_busy()


PAUSED = {"game": "a game runs", "es": "EmulationStation is busy"}


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
_state = {"task": None, "since": 0.0, "file": "", "step": "", "pct": None, "last": ""}


def status(**kw):
    """What the worker is doing, for the app's Conversions screen."""
    _state.update(kw)
    try:
        rommlib.atomic_write_json(rommlib.chd_current_path(), {k: _state[k] for k in ("task", "since", "file", "step", "pct")},
                                  sync=False)        # rewritten every few seconds
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
             "retry": task if result == "failed" and task else None}
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
    (it would otherwise create one in Batocera's Dolphin config area)."""
    exe = rommlib.dolphin_tool_path()
    if not exe:
        raise ToolMissing("dolphin-tool")
    user = os.path.join(rommlib.tmp_dir(), "dolphin-user")
    os.makedirs(user, exist_ok=True)
    args = list(args)
    if args and args[0] in ("convert", "verify"):
        args[1:1] = ["-u", user]
    return run_tool([exe] + args, keep_stdout=True)


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
    paused = False
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
            why = busy_reason()
            busy = why != ""
            guard = memory_check(p.pid, busy)
            if guard:
                break                             # finally: resume + stop chdman
            if busy and not paused:
                p.send_signal(signal.SIGSTOP)
                paused = True
                note("chd: paused (%s)" % PAUSED[why])
            elif not busy and paused:
                p.send_signal(signal.SIGCONT)
                paused = False
                note("chd: resumed")
    finally:
        if p.poll() is None:          # we're going down (killed, error): never leave chdman frozen
            try:
                p.send_signal(signal.SIGCONT)
                p.terminate()
                p.wait(10)
            except Exception:
                p.kill()
    if guard:
        errf.close()
        raise guard
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
            while busy_reason():               # ES can't be asked: treated as busy
                time.sleep(10)
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
    while True:
        why = busy_reason()                # ES can't be asked: treated as busy
        if not why:
            return
        status(step="Paused while " + PAUSED[why])
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


def _sync_file(path):
    """Make a file's data durable (a rename to a new name doesn't flush it on ext4)."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


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


def convert_zar(src, tmp):
    """Xbox 360 disc image -> .zar that xenia-canary opens directly: the game
    partition's files (extract-xiso), packed with zarchive. Neither tool's exit
    code is trusted alone: the extracted files must match the image's own file
    list (extract-xiso exits 0 on a cut-short image), the archive's footer and
    SHA-256 must check out (zarchive never checks them), and the archive is
    unpacked again and every file's CRC32 compared before the caller deletes
    the image. The .zar holds the game's files, not the disc's other
    partitions, so it can't be turned back into the original image."""
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
            _sync_file(os.path.join(dp, fn))
        _sync_dir(dp)
    if key:
        note("chd: decrypted %s with the disc key %s" % (os.path.basename(src), key))
    return 0, ""


def _drop(path):
    """Remove a finished or partial output: a file, or a game folder (PS3)."""
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


def convert(task):
    system, mode = task["system"], task["mode"]
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
    playing = running_game()
    if playing and playing in srcs:
        note("chd: SKIP in use %s" % playing)
        return False
    roots = task["roots"]
    folder_root = next((r.rstrip("/") for r in roots if os.path.isdir(r)), None)
    if folder_root and os.path.normpath(folder_root) == os.path.normpath(sysdir):
        folder_root = None        # never treat the system dir itself as a game folder
    out_dir = os.path.dirname(folder_root) if folder_root else os.path.dirname(srcs[0])
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
    plan = []
    for src in srcs:
        if multi or not folder_root:
            stem = os.path.splitext(os.path.basename(src))[0]
        else:
            stem = os.path.basename(folder_root)      # game name, not track name
        plan.append((src, os.path.join(out_dir, rommlib.conversion_output(mode, stem, src))))
    if len({out for _, out in plan}) != len(plan):
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
    # an output already there that an interrupted run made (killed after it landed,
    # before the rename was recorded): finish the job instead of skipping forever
    resume = set()

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
                note("chd: replacing damaged or different %s" % out)
                continue
            resume.add(out)                          # exactly this copy: just finish the job
            continue
        if os.path.exists(out) and mode == "wux" and _wux_size(out) != _size(next(s for s, o in plan if o == out)):
            note("chd: replacing %s: made from a different (cut-short?) image" % out)   # a fresh copy came down
            continue
        if os.path.exists(out):
            if rommlib.disc_incomplete(out):
                note("chd: replacing incomplete %s" % out)   # a fresh copy came down
                continue
            src = next(s for s, o in plan if o == out)
            if conv and conv.chd and newer_than_sources(out, src) \
                    and chdman("verify", "-i", out)[0] == 0:
                resume.add(out)
                continue
            note("chd: SKIP exists %s" % out)
            return False
    cfg = rommlib.load_config()
    need = sum(os.path.getsize(p) for s in srcs for p in sources_of(s) if os.path.isfile(p))
    need *= conv.space_factor if conv else 1     # e.g. .zar: extracted files + archive beside the image
    if conv and conv.space:
        try:
            need = max(need, sum(conv.space(s) for s in srcs))    # e.g. .cso: the decoded ISO + the CHD
        except rommlib.CHECK_ERRORS:
            pass                                  # checked above; the converter reports it
    free, keep = rommlib.free_bytes(out_dir), rommlib.keep_free_bytes(cfg)
    if free - need < keep:
        # the CHD sits next to the original until it's verified: wait for room
        note("chd: WAIT for space %s (needs ~%s, %s free, keeping %s free)" % (
            os.path.basename(srcs[0]), rommlib.human_size(need), rommlib.human_size(free),
            rommlib.human_size(keep)))
        return None
    # what the sources are now: if one is replaced (a fresh download landing) or
    # deleted (Free up space) while this runs, nothing may be committed on top
    def sig(p):
        st = os.stat(p)
        return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
    before = {p: sig(p) for s in srcs for p in sources_of(s) if os.path.exists(p)}
    made = []
    for src, out in plan:
        if out in resume:
            note("chd: using %s, made before an interruption" % os.path.basename(out))
            _sync_file(out)
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
                rc, err = chdman(mode, "-np", threads, *codecs, "-f", "-i", src, "-o", tmp)
                if rc == 0:
                    status(step="Verifying", pct=None)
                    rc, err = chdman("verify", "-i", tmp)
        except MemoryGuard as g:
            note("chd: STOPPED %s: %s; %s" % (os.path.basename(src), g,
                        "will try again later" if g.retry else "leaving it as it is"))
            for m in made + [tmp]:
                _drop(m)
            if g.retry:
                return None
            return False
        except BaseException:
            # stopped (SIGTERM raises SystemExit; the tool itself is stopped by
            # run_tool), a converter missing, or anything else: the half-written
            # output goes, never the sources
            try:
                _drop(tmp)
            except OSError:
                pass
            raise
        if rc != 0:
            note("chd: FAIL %s rc=%d %s" % (src, rc, err))
            for m in made + [tmp]:
                _drop(m)
            return False
        # the output must be on disk before any original is deleted: after a power
        # cut a renamed-but-unflushed file comes back empty (seen on ext4)
        os.replace(tmp, out)
        _sync_file(out)
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
    in_size = sum(os.path.getsize(p) for s in srcs for p in sources_of(s) if os.path.isfile(p))
    out_size = sum(rommlib.path_size(m) for m in made)
    m3us = rommlib.fix_m3us(sysdir, old_to_new)
    hide = []
    if multi:
        # an existing playlist (now rewritten) stays the entry; else write one
        outside = [m for m in m3us if not (folder_root and m.startswith(folder_root + "/"))]
        if outside:
            primary = outside[0]
        else:
            name = task.get("name") or os.path.basename(folder_root) if folder_root else \
                re.sub(r"\s*\(Disc \d+\).*$", "", os.path.splitext(os.path.basename(srcs[0]))[0])
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
        primary = primary or made[0]
        rommlib.migrate_saves(system, srcs[0], primary)
        for s, o in plan:
            rommlib.migrate_saves(system, s, o)
    else:
        primary = made[0]
        rommlib.migrate_saves(system, srcs[0], primary)

    # record the rename before deleting anything: a crash after this leaves
    # duplicates at worst, never a CHD whose game info can't follow it
    rename = {}
    gone = set()
    for src in srcs:
        for p in sources_of(src):
            rename[p] = primary
            gone.add(os.path.normpath(p))
    folder_goes = False
    if folder_root and os.path.isdir(folder_root):
        rest = [os.path.join(dp, f) for dp, _, fs in os.walk(folder_root) for f in fs
                if os.path.normpath(os.path.join(dp, f)) not in gone]
        folder_goes = all(os.path.splitext(f)[1].lower() in JUNK_EXTS for f in rest)
    if folder_goes:
        rename[folder_root] = primary            # the whole game folder becomes this file
    rommlib.append_gamelist({"system": system, "rename": rename, "hide": hide})
    rommlib.Library().rename_paths(rename, hide)
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
        if folder_goes:
            shutil.rmtree(folder_root, ignore_errors=True)
        else:
            # other games / files stay in it: only the converted files were renamed
            note("chd: kept folder (non-junk leftovers) %s" % folder_root)
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
            work(fd, waiting)
        # a task queued while we were giving up the lock would find it taken
        # and not start a worker: look once more after releasing it (the app
        # starts one again once a missing tool is here)
        _readmit(waiting)
        if not pending(waiting):
            return


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
    while True:
        _readmit(waiting)
        tasks = sorted(t for t in os.listdir(rommlib.CHD_QUEUE_DIR) if t.endswith(".json") and t not in waiting)
        if not tasks:
            if not since_finalize:
                break
            finalize_later()        # merge what's done (once ES is idle), in its own process
            break
        if busy_reason():
            time.sleep(20)
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
                   step="Starting", pct=None)
            res = convert(task)
            if res is None:                   # memory needed by a game, or no room: keep it, try later
                record(tasks[0], task, "waiting")
                try:
                    os.remove(cur)
                except OSError:
                    pass
                break
            record(tasks[0], task, "ok" if res else ("skipped" if "SKIP" in _state["last"] or
                                                     "skip" in _state["last"].lower() else "failed"))
            if res:
                since_finalize += 1
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
            break
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
        if since_finalize >= FINALIZE_EVERY:
            finalize_later()
            since_finalize = 0


if __name__ == "__main__":
    main()
