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


class ChdmanMissing(Exception):
    """chdman isn't installed: stop, keeping the queue."""


class ToolMissing(Exception):
    """A converter other than chdman (dolphin-tool) isn't installed."""


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
    if mode == "createdvd":
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


CURRENT = os.path.join(rommlib.DATA_DIR, "chd-current.json")
RESULTS = os.path.join(rommlib.DATA_DIR, "chd-results.jsonl")
RESULTS_KEEP = 200
_state = {"task": None, "since": 0.0, "file": "", "step": "", "pct": None, "last": ""}


def status(**kw):
    """What the worker is doing, for the app's Conversions screen."""
    _state.update(kw)
    try:
        tmp = CURRENT + ".tmp"
        with open(tmp, "w") as f:
            json.dump({k: _state[k] for k in ("task", "since", "file", "step", "pct")}, f)
        os.replace(tmp, CURRENT)
    except OSError:
        pass


def note(msg):
    """Log a line and remember it as the reason this task ended the way it did."""
    _state["last"] = msg.split(": ", 1)[-1] if msg.startswith(("chd: ", "rvz: ", "squashfs: ", "z3ds: ", "zar: ")) \
        else msg
    rommlib.log(msg)


def record(task_name, task, result):
    """Append one finished task to chd-results.jsonl (kept short)."""
    entry = {"time": time.time(), "task": task_name, "result": result, "message": _state["last"][:300],
             "system": task.get("system"), "mode": task.get("mode"), "sources": task.get("sources"),
             "retry": task if result == "failed" else None}
    try:
        lines = []
        if os.path.exists(RESULTS):
            with open(RESULTS) as f:
                lines = f.readlines()[-(RESULTS_KEEP - 1):]
        lines.append(json.dumps(entry) + "\n")
        tmp = RESULTS + ".tmp"
        with open(tmp, "w") as f:
            f.writelines(lines)
        os.replace(tmp, RESULTS)
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
    user = os.path.join(rommlib.DATA_DIR, "tmp", "dolphin-user")
    os.makedirs(user, exist_ok=True)
    args = list(args)
    if args and args[0] in ("convert", "verify"):
        args[1:1] = ["-u", user]
    return run_tool([exe] + args, keep_stdout=True)


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
        p = subprocess.Popen(cmd, stdout=outf if keep_stdout else subprocess.DEVNULL, stderr=outf, text=True)
    paused = False
    guard = None
    try:
        while p.poll() is None:
            time.sleep(3)
            try:                                  # progress for the Conversions screen
                errf.seek(0, 2)
                errf.seek(max(0, errf.tell() - 400))
                m = PCT_RE.findall(errf.read())
                if m:
                    status(pct=min(100.0, float(m[-1])))
            except (OSError, ValueError):
                pass
            busy = running_game() != ""
            guard = memory_check(p.pid, busy)
            if guard:
                break                             # finally: resume + stop chdman
            if busy and not paused:
                p.send_signal(signal.SIGSTOP)
                paused = True
                note("chd: paused (game running)")
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
        note("chd: copied %d save file(s) %s -> %s" % (copied, ob, nb))
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
                with open(p, errors="surrogateescape") as f:      # odd bytes in names survive as-is
                    lines = f.read().splitlines()
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
                with open(p, "w", errors="surrogateescape") as f:
                    f.write("\n".join(out) + "\n")
                touched.append(p)
    return touched


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
            while running_game() != "":        # None: ES can't be asked, treated as busy
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
    if off and not rommlib.load_config().get("xbox_bootable", True):
        # packing a full redump image would only make a smaller file xemu can't boot
        return 2, "a full disc image xemu can't boot (Settings: Make Xbox images bootable)"
    try:
        cut = rommlib.make_xiso(src)
    except rommlib.NoSpace as e:
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
    mnt = os.path.join(rommlib.DATA_DIR, "tmp", "squashfs-check-%d" % os.getpid())
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
    while running_game() != "":            # None: ES can't be asked, treated as busy
        status(step="Paused while a game runs")
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
    work = os.path.join(rommlib.DATA_DIR, "tmp", "zar-%d" % os.getpid())
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


def convert(task):
    system, mode = task["system"], task["mode"]
    sysdir = os.path.join(rommlib.ROMS_ROOT, system)
    srcs = sorted(s for s in task["sources"] if os.path.exists(s))
    if not srcs:
        return False
    if mode == "rvz" and not rommlib.rvz_enabled(rommlib.load_config()):
        note("chd: SKIP rvz %s (turned off, or dolphin-tool isn't installed)" % os.path.basename(srcs[0]))
        return False
    if mode == "squashfs" and not rommlib.squashfs_enabled(rommlib.load_config(), system):
        note("chd: SKIP squashfs %s (turned off, or mksquashfs is missing)" % os.path.basename(srcs[0]))
        return False
    if mode == "zar" and not rommlib.zar_enabled(rommlib.load_config(), system):
        note("chd: SKIP zar %s (turned off, or the Xbox 360 tools aren't installed)" % os.path.basename(srcs[0]))
        return False
    if mode == "z3ds" and not rommlib.z3ds_enabled(rommlib.load_config(), system):
        note("chd: SKIP z3ds %s (turned off, or libzstd is missing)" % os.path.basename(srcs[0]))
        return False
    if mode == "z3ds":
        for src in srcs:
            try:
                rommlib.z3ds_info(src)
            except (rommlib.Z3dsError, OSError) as e:
                # nothing to retry: an encrypted dump stays encrypted (and Azahar can't run it either)
                note("chd: SKIP z3ds %s: %s" % (os.path.basename(src), e))
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
    multi = len(srcs) > 1
    plan = []
    out_ext = {"rvz": ".rvz", "squashfs": ".squashfs", "zar": ".zar"}.get(mode, ".chd")
    for src in srcs:
        if multi or not folder_root:
            stem = os.path.splitext(os.path.basename(src))[0]
        else:
            stem = os.path.basename(folder_root)      # game name, not track name
        if mode == "squashfs":
            stem += os.path.splitext(src)[1].lower()  # "X.iso.squashfs": the image's own extension stays
        ext = os.path.splitext(rommlib.z3ds_target(src) or ".zcci")[1] if mode == "z3ds" else out_ext
        plan.append((src, os.path.join(out_dir, stem + ext)))
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
        if os.path.exists(out):
            if rommlib.disc_incomplete(out):
                note("chd: replacing incomplete %s" % out)   # a fresh copy came down
                continue
            note("chd: SKIP exists %s" % out)
            return False
    cfg = rommlib.load_config()
    need = sum(os.path.getsize(p) for s in srcs for p in sources_of(s) if os.path.isfile(p))
    if mode == "zar":
        need *= 2        # the extracted files and the .zar sit next to the image until it's verified
    free, keep = rommlib.free_bytes(out_dir), rommlib.keep_free_bytes(cfg)
    if free - need < keep:
        # the CHD sits next to the original until it's verified: wait for room
        note("chd: WAIT for space %s (needs ~%s, %s free, keeping %s free)" % (
            os.path.basename(srcs[0]), rommlib.human_size(need), rommlib.human_size(free),
            rommlib.human_size(keep)))
        return None
    made = []
    for src, out in plan:
        tmp = out + ".part"                    # not a name ES lists as a game while it's written
        t0 = time.time()
        threads = str(rommlib.chd_threads(rommlib.load_config()))    # read per disc: Settings apply next disc
        codecs = codecs_for(mode, rommlib.load_config(), system)
        try:
            status(file=os.path.basename(src), step="Converting", pct=None)
            if mode == "rvz":
                rc, err = convert_rvz(src, tmp)
            elif mode == "squashfs":
                rc, err = convert_squashfs(src, tmp, threads)
            elif mode == "z3ds":
                rc, err = convert_z3ds(src, tmp, threads)
            elif mode == "zar":
                rc, err = convert_zar(src, tmp)
            else:
                rc, err = chdman(mode, "-np", threads, *codecs, "-f", "-i", src, "-o", tmp)
                if rc == 0:
                    status(step="Verifying", pct=None)
                    rc, err = chdman("verify", "-i", tmp)
        except MemoryGuard as g:
            note("chd: STOPPED %s: %s; %s" % (os.path.basename(src), g,
                        "will try again later" if g.retry else "leaving it as it is"))
            for m in made + [tmp]:
                if os.path.exists(m):
                    os.remove(m)
            if g.retry:
                return None
            return False
        if rc != 0:
            note("chd: FAIL %s rc=%d %s" % (src, rc, err))
            for m in made + [tmp]:
                if os.path.exists(m):
                    os.remove(m)
            return False
        os.replace(tmp, out)
        made.append(out)
        note("chd: disc done %s (%.0fs)" % (os.path.basename(out), time.time() - t0))

    old_to_new = {os.path.normpath(s): o for s, o in plan}
    in_size = sum(os.path.getsize(p) for s in srcs for p in sources_of(s) if os.path.isfile(p))
    out_size = sum(os.path.getsize(m) for m in made)
    m3us = fix_m3us(sysdir, old_to_new)
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
    note("chd: OK %s -> %s  (%.2f GB -> %.2f GB)" % (
        os.path.relpath(srcs[0], sysdir), os.path.relpath(primary, sysdir),
        in_size / 1e9, out_size / 1e9))
    return True


def finalize_later():
    """Merge gamelist changes in a separate process. rb_finalize waits for ES
    to be idle — with RomM-o-Cera open that means until it closes — and this
    worker must not sit on chd.lock meanwhile (new conversions queued by
    downloads would wait behind it). rb_finalize is flock'd: extra spawns no-op."""
    subprocess.Popen([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "rb_finalize.py")],
                     start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True)


def pending():
    try:
        return any(t.endswith(".json") for t in os.listdir(rommlib.CHD_QUEUE_DIR))
    except OSError:
        return False


def _sigterm(signum, frame):
    raise SystemExit(0)                         # runs chdman()'s cleanup on the way out


def main():
    signal.signal(signal.SIGTERM, _sigterm)
    os.umask(0o022)       # CHDs readable like any other rom, however this worker was started
    os.makedirs(rommlib.CHD_QUEUE_DIR, exist_ok=True)
    while True:
        lockf = open(LOCK, "w")
        try:
            fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return                              # another worker has it
        work(lockf)
        lockf.close()
        # a task queued while we were giving up the lock would find it taken
        # and not start a worker: look once more after releasing it
        if not pending():
            return


def work(lockf):
    since_finalize = 0
    try:
        os.remove(os.path.join(rommlib.DATA_DIR, "chd-current.json"))   # left by a killed run
    except OSError:
        pass
    while True:
        tasks = sorted(t for t in os.listdir(rommlib.CHD_QUEUE_DIR) if t.endswith(".json"))
        if not tasks:
            if not since_finalize:
                break
            finalize_later()        # merge what's done (once ES is idle), in its own process
            break
        if running_game() != "":
            time.sleep(20)
            continue
        path = os.path.join(rommlib.CHD_QUEUE_DIR, tasks[0])
        cur = os.path.join(rommlib.DATA_DIR, "chd-current.json")
        try:
            with open(path) as f:
                task = json.load(f)
            _state["last"] = ""
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
        except ToolMissing as e:
            note("chd: %s isn't installed; dropping %s (Settings, %s)" % (
                e, tasks[0], "Xbox to squashfs" if "squashfs" in str(e) else
                "Xbox 360 to .zar" if "xbox360" in str(e) else "GameCube and Wii to RVZ"))
        except ChdmanMissing:
            note("chd: chdman not found; leaving %d task(s) queued" % len(tasks))
            try:
                os.remove(cur)
            except OSError:
                pass
            break
        except Exception as e:
            note("chd: error on %s: %r" % (tasks[0], e))
            record(tasks[0], locals().get("task") or {}, "failed")
        os.remove(path)
        try:
            os.remove(cur)
        except OSError:
            pass
        if since_finalize >= FINALIZE_EVERY:
            finalize_later()
            since_finalize = 0


if __name__ == "__main__":
    main()
