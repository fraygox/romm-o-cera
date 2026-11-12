#!/usr/bin/env python3
"""The format harness's box side: runs ON a throwaway Batocera test box, driven by
tools/formats/run.py over SSH. Standard library plus the app's own modules (rommlib,
rb_chd_plan, rb_chd), Python 3.12 (Batocera 43.1) and 3.14 (44).

  boxside.py info                          the box: Batocera version, Python, CPU, free space
  boxside.py prepare                       park conversion tasks the harness didn't queue
  boxside.py bios SYSTEM                   the BIOS files Batocera lists for SYSTEM, and which are missing
  boxside.py derive HOW SRC DST            make an input from another form with the app's tool
  boxside.py checksum PATH                 size, CRC32, MD5, SHA-1 of a file
  boxside.py compress CASE.json            compress the way "Compress games already here" does
  boxside.py play SYSTEM PATH SHOTDIR [S]  have ES launch PATH (as Play now does), judge it, stop it
  boxside.py clean KEY                     remove everything of case KEY from roms/

Every command prints one line "FMTJSON <json>" with its result (and may print
other lines before it). Only files named fmt-<key>... are ever made, changed or
deleted; results name nothing else (the box may hold other games).
"""
import binascii
import glob
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

APP = os.environ.get("FMT_APP", "/userdata/roms/pygame/rommbrowser")
sys.path.insert(0, APP)
import rommlib  # noqa: E402

ES = rommlib.ES_API
LOGS = "/userdata/system/logs"
LAUNCH_LOGS = ("es_launch_stdout.log", "es_launch_stderr.log", "batocera-launch.log", "batocera-launch-emulator.log")
PREFIX = "fmt-"
HELD = os.path.join(rommlib.DATA_DIR, "chd-queue-held-by-fmt")
# lines in the launch logs that say the game didn't load (an emulator's ordinary stderr is logged
# at ERROR level on 44, and configgen notes missing config files on a first launch: never these alone)
LOAD_FAIL = re.compile(r"failed to load (content|game|rom|disc|iso)|could not load (content|game|rom)|"
                       r"couldn't load (content|game)|unable to load (content|game|rom)|error loading (content|game|rom)|"
                       r"failed to open (the )?(disc|image|iso|file|game)|please insert|invalid (disc|image|rom)|"
                       r"bios.{0,40}(missing|not found)|no bootable|unknown (disc|image) format|required files are missing", re.I)
NOT_A_FAILURE = re.compile(r"unixSettings\.py|No such file or directory: '/userdata/system/configs/| DEBUG \(")
# lines kept as evidence only
LOG_NOTE = re.compile(r"traceback|exception|error|exitcode|exit code|status [1-9]", re.I)
# new processes that are never the emulator itself
NOT_EMULATOR = {"python", "python3", "sh", "bash", "sleep", "ffmpeg", "xrandr", "batocera-screen",
                "batocera-resolu", "batocera-settin", "batocera-audio", "evmapy", "xdotool", "grep",
                "awk", "sed", "cut", "cat", "head", "tail", "find", "ls", "pactl", "amixer", "udevadm",
                "systemctl", "dbus-send", "timeout", "nice", "ionice", "taskset", "env", "mount", "umount",
                "fuse-overlayfs", "squashfuse", "unsquashfs", "xset", "unclutter", "hotkeygen", "wmctrl",
                "batocera-mouse", "xinput", "mangohud", "pidof", "pgrep", "ps", "kill", "killall", "true",
                "udevd", "(udev-worker)", "inotifywait", "th-cmd", "emulatorlaunche", "dbus-daemon", "xdg-open",
                "emulationstatio", "edid-decode", "sort", "tr", "glxinfo", "uniq", "wc", "xwininfo", "xprop"}


def out(obj):
    print("FMTJSON " + json.dumps(obj, sort_keys=True))
    sys.stdout.flush()


def display_env():
    try:          # Batocera's own answer (44 runs X on :1)
        disp = subprocess.run(["getLocalXDisplay"], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        disp = ""
    env = dict(os.environ, DISPLAY=disp or ":0")
    if os.path.exists("/var/lib/.Xauthority"):          # Batocera 44
        env.update(XAUTHORITY="/var/lib/.Xauthority", XDG_RUNTIME_DIR="/var/run")
    return env


# ------------------------------------------------------------------ ES

def es(path, data=None, timeout=10):
    req = urllib.request.Request(ES + path, data=data.encode() if isinstance(data, str) else data,
                                 method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def running():
    """The path ES runs ('' when none); None when ES can't be asked."""
    try:
        return json.loads(es("/runningGame", timeout=3)[1] or b"{}").get("path") or ""
    except ValueError:
        return ""
    except Exception:
        return None


def listed(system, path):
    try:
        games = json.loads(es("/systems/" + urllib.parse.quote(system) + "/games", timeout=60)[1])
        return any(g.get("path") == path for g in games)
    except Exception:
        return False


def takes(system, path):
    """False only when ES's own list of the system's extensions is there and
    hasn't the file's: ES will never list it. True whenever that can't be told."""
    try:
        exts = rommlib.load_es_systems().get(system)
    except Exception:
        return True
    return not exts or os.path.splitext(path)[1].lower() in exts


def finalizing():
    """rb_finalize holds its lock (read from /proc/locks, never taken)."""
    lock = rommlib.finalize_lock_path()
    try:
        st = os.stat(lock)
        key = "%02x:%02x:%d" % (os.major(st.st_dev), os.minor(st.st_dev), st.st_ino)
        with open("/proc/locks") as f:
            return any(key in line.split() for line in f if "FLOCK" in line)
    except OSError:
        return False


def wait_listed(system, path, limit=300):
    """Until ES lists path: wait for rb_finalize, then /reloadgames once if needed."""
    end = time.time() + limit
    while finalizing() and time.time() < end:
        time.sleep(2)
    if listed(system, path):
        return True
    try:
        es("/reloadgames", timeout=300)
    except Exception:
        pass
    while time.time() < end:
        if listed(system, path):
            return True
        time.sleep(2)
    return False


# ------------------------------------------------------------------ processes

def procs():
    """{pid: comm} of every process."""
    got = {}
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                with open("/proc/%s/comm" % d) as f:
                    got[int(d)] = f.read().strip()
            except OSError:
                pass
    return got


def cmdline(pid):
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            return [a.decode("utf-8", "replace") for a in f.read().split(b"\0") if a]
    except OSError:
        return []


VERIFY_TOOLS = {"chdman": ("verify", "info"), "dolphin-tool": ("verify",), "wuatool": ("verify", "hash", "compare"),
                "zarchive": (), "unsquashfs": (), "extract-xiso": ()}


def verify_running():
    """A converter's checking tool runs now (chdman verify, dolphin-tool verify, ...)."""
    me = os.getpid()
    for pid in procs():
        if pid == me:
            continue
        argv = cmdline(pid)
        for i, a in enumerate(argv[:3]):
            base = os.path.basename(a)
            if base in VERIFY_TOOLS:
                words = VERIFY_TOOLS[base]
                if not words or any(w in argv[i + 1:i + 2] for w in words):
                    return True
    return False


# ------------------------------------------------------------------ small commands

def cmd_info():
    try:
        ver = open("/usr/share/batocera/batocera.version").read().strip()
    except OSError:
        ver = ""
    out({"batocera": ver, "python": sys.version.split()[0], "machine": os.uname().machine,
         "free": shutil.disk_usage(rommlib.ROMS_ROOT).free, "app": rommlib.VERSION_LABEL,
         "es": running() is not None})


def cmd_prepare():
    """Conversion tasks already queued on the box (not ours) are parked, so the
    worker runs only what the harness queues; the harness never runs them."""
    os.makedirs(HELD, exist_ok=True)
    parked = 0
    for t in glob.glob(os.path.join(rommlib.CHD_QUEUE_DIR, "*.json")):
        try:
            srcs = json.load(open(t)).get("sources") or []
        except (OSError, ValueError):
            srcs = []
        if not all(os.path.basename(s).startswith(PREFIX) or ("/" + PREFIX) in s for s in srcs) or not srcs:
            os.replace(t, os.path.join(HELD, os.path.basename(t)))
            parked += 1
    os.makedirs(os.path.join(rommlib.USERDATA, "screenshots"), exist_ok=True)
    out({"parked": parked, "es_bios_prompt": bios_prompt_off()})


ES_SETTINGS = "/userdata/system/configs/emulationstation/es_settings.cfg"
NO_BIOS_PROMPT = '<bool name="CheckBiosesAtLaunch" value="false" />'


def bios_prompt_off():
    """ES asks "missing BIOS, launch anyway?" before a game whose system lacks any
    of its BIOS files, and waits for an answer (a player says yes). The harness
    places what the owner has and records what's missing, so the question is
    turned off once (ES restarted); "off" when it is."""
    try:
        text = open(ES_SETTINGS).read()
    except OSError:
        text = '<?xml version="1.0"?>\n<config>\n</config>\n'
    if NO_BIOS_PROMPT in text:
        return "off"
    text = re.sub(r'\s*<bool name="CheckBiosesAtLaunch"[^>]*/>', "", text)
    text = text.replace("</config>", "\t%s\n</config>" % NO_BIOS_PROMPT)
    os.makedirs(os.path.dirname(ES_SETTINGS), exist_ok=True)
    with open(ES_SETTINGS, "w") as f:
        f.write(text)
    try:      # its output stays open in ES itself (44): never read, waited on for a while only
        subprocess.run(["/etc/init.d/S31emulationstation", "restart"], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, timeout=120)
    except subprocess.TimeoutExpired:
        pass
    end = time.time() + 180
    time.sleep(5)
    while time.time() < end and running() is None:
        time.sleep(2)
    time.sleep(10)                          # ES's menus up
    return "turned off (ES restarted)"


def _missing_bios(system):
    """Batocera's list for the system, read the way rommlib.missing_bios does, except that an
    entry may give several accepted MD5s (a list: Batocera 44's psx). For when the app's raises."""
    out = []
    db = rommlib.bios_requirements()
    for key in [system] + rommlib.BIOS_ALIASES.get(system, []):
        for b in (db.get(key) or {}).get("biosFiles", []):
            if b.get("zippedFile") or not b.get("file") or not b.get("md5"):
                continue
            md5s = [m.lower() for m in ([b["md5"]] if isinstance(b["md5"], str) else b["md5"])]
            path = os.path.join(rommlib.USERDATA, b["file"])
            try:
                if os.path.isfile(path) and rommlib.file_md5(path) in md5s:
                    continue
            except OSError:
                pass
            out += [{"path": path, "md5": m, "file": b["file"]} for m in md5s]
    return out


def cmd_bios(system):
    res = {"required": rommlib.bios_required(system)}
    try:
        res["missing"] = rommlib.missing_bios(system)
        res["all_missing"] = bool(rommlib.bios_all_missing(system))
    except Exception as e:            # the app's own check failed: say so, and use the harness's reading
        res["app_error"] = "rommlib.missing_bios(%r): %s: %s" % (system, e.__class__.__name__, e)
        res["missing"] = _missing_bios(system)
        res["all_missing"] = len({m["file"] for m in res["missing"]}) >= len(res["required"])
    res["zipped"] = _zipped_bios(system)
    out(res)


def _zipped_bios(system):
    """BIOS zips (MAME-style sets) Batocera lists by the MD5s of files inside them, when the
    zip isn't here: [{"file", "path", "md5s"}] (the app's own check leaves these out)."""
    out = {}
    db = rommlib.bios_requirements()
    for key in [system] + rommlib.BIOS_ALIASES.get(system, []):
        for b in (db.get(key) or {}).get("biosFiles", []):
            if b.get("zippedFile") and b.get("file") and b.get("md5"):
                path = os.path.join(rommlib.USERDATA, b["file"])
                if not os.path.exists(path):
                    e = out.setdefault(b["file"], {"file": b["file"], "path": path, "md5s": []})
                    e["md5s"] += [b["md5"]] if isinstance(b["md5"], str) else list(b["md5"])
    return list(out.values())


def hashes(path):
    crc, md5, sha1, size = 0, hashlib.md5(), hashlib.sha1(), 0
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(4 << 20), b""):
            crc = binascii.crc32(b, crc)
            md5.update(b)
            sha1.update(b)
            size += len(b)
    return {"name": os.path.basename(path), "size": size, "crc": "%08x" % crc, "md5": md5.hexdigest(),
            "sha1": sha1.hexdigest()}


def _crc_from(path, offset):
    crc = 0
    with open(path, "rb") as f:
        f.seek(offset)
        for b in iter(lambda: f.read(4 << 20), b""):
            crc = binascii.crc32(b, crc)
    return "%08x" % crc


def cmd_checksum(path):
    out(hashes(path))


def tool_spec(slug):
    return next(c.tool for c in rommlib.CONVERTERS if c.tool and c.tool.slug == slug)


def ensure_tool(slug):
    """The app's own install (download, SHA-256 check, probe); None when it's here."""
    spec = tool_spec(slug)
    if spec.installed():
        return spec, None
    try:
        rommlib.install_tool(spec)
        return spec, None
    except Exception as e:
        return spec, "%s: %s" % (e.__class__.__name__, e)


def cmd_derive(how, src, dst):
    """An input the library has only in another form, made from the owner's own file
    with the app's tool: GameCube/Wii .rvz -> .iso, Wii U .wux -> .wud."""
    t0 = time.time()
    slug = {"dolphin-iso": "dolphin-tool", "unwux": "wiiu-tools"}[how]
    spec, err = ensure_tool(slug)
    if err:
        return out({"ok": False, "why": "tool: " + err})
    exe = rommlib.tool_paths(spec)[0]
    part = dst + ".part"
    if how == "dolphin-iso":
        user = os.path.join(rommlib.tmp_dir(), "fmt-dolphin")
        os.makedirs(user, exist_ok=True)
        argv = [exe, "convert", "-u", user, "-i", src, "-o", part, "-f", "iso"]
    else:
        argv = [exe, "unwux", src, part]
    r = subprocess.run(argv, capture_output=True, text=True, errors="replace")
    if r.returncode or not os.path.isfile(part):
        try:
            os.remove(part)
        except OSError:
            pass
        return out({"ok": False, "why": "%s exited %d: %s" % (os.path.basename(exe), r.returncode,
                                                             (r.stderr or r.stdout)[-300:])})
    os.replace(part, dst)
    os.remove(src)
    old = time.time() - 3600
    os.utime(dst, (old, old))               # the planner leaves files written in the last minute alone
    out({"ok": True, "size": os.path.getsize(dst), "seconds": round(time.time() - t0, 1)})


def cmd_clean(key):
    gone = []
    for sysdir in glob.glob(os.path.join(rommlib.ROMS_ROOT, "*")):
        for p in glob.glob(os.path.join(sysdir, PREFIX + key + "*")) + \
                glob.glob(os.path.join(sysdir, PREFIX + key + "*", "")):
            p = p.rstrip("/")
            if not os.path.basename(p).startswith(PREFIX + key):
                continue
            if os.path.isdir(p) and not os.path.islink(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                try:
                    os.remove(p)
                except OSError:
                    continue
            gone.append(os.path.relpath(p, rommlib.ROMS_ROOT))
    for t in glob.glob(os.path.join(rommlib.CHD_QUEUE_DIR, "*.json")):      # a task of ours left queued
        try:
            srcs = json.load(open(t)).get("sources") or []
        except (OSError, ValueError):
            continue
        if srcs and all(os.path.basename(x).startswith(PREFIX + key) for x in srcs):
            os.remove(t)
            gone.append("chd-queue/" + os.path.basename(t))
    out({"removed": sorted(set(gone))})


# ------------------------------------------------------------------ compress

def _ours(text, key):
    return [ln for ln in text.splitlines() if PREFIX + key in ln]


def _log_size():
    try:
        return os.path.getsize(rommlib.LOG_PATH)
    except OSError:
        return 0


def _log_since(offset, key):
    try:
        with open(rommlib.LOG_PATH, "rb") as f:
            if os.path.getsize(rommlib.LOG_PATH) < offset:
                offset = 0                       # rotated meanwhile
            f.seek(offset)
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    return [ln[:400] for ln in _ours(text, key)][-60:]


def _results(names):
    got = []
    try:
        with open(rommlib.chd_results_path()) as f:
            for ln in f:
                try:
                    e = json.loads(ln)
                except ValueError:
                    continue
                if e.get("task") in names:
                    got.append({k: e.get(k) for k in ("task", "result", "message", "mode", "in_size", "out_size")})
    except OSError:
        pass
    return got


def _size(p):
    if os.path.isdir(p):
        return sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(p) for f in fs)
    return os.path.getsize(p) if os.path.exists(p) else 0


def _outputs(case, sysdir):
    """What the case made: fmt-<key>... files with one of its output extensions (a
    .ps3 folder too), never .part leftovers."""
    exts = tuple(case["outputs"])
    got = []
    for n in sorted(os.listdir(sysdir)):
        if n.startswith(PREFIX + case["key"]) and n.lower().endswith(exts):
            got.append(os.path.join(sysdir, n))
    return got


def _app_check(case, path, orig):
    """The app's own check of an output: Check games' full (deep) check."""
    if case["mode"] in ("createcd", "createdvd", "cso"):
        bad = rommlib.chd_header_problem(path)
        if bad:
            return {"ok": False, "check": "chd header", "why": bad}
        ok = rommlib._chd_verify("chdman", path)
        return {"ok": ok is True, "check": "chdman verify (Check games)", "why": "" if ok else "chdman verify: %r" % ok}
    if case["mode"] == "xiso":
        part = rommlib.xbox_partition(path)
        return {"ok": part == 0, "check": "xbox_partition", "why": "" if part == 0 else "not an XISO (%r)" % part}
    mode = "ps3" if case["mode"] == "ps3decrypt" else case["mode"]
    conv = rommlib.CONVERTER[mode]
    try:
        bad = conv.audit(path, orig, True)
    except Exception as e:
        return {"ok": False, "check": "%s audit" % mode, "why": "%s: %s" % (e.__class__.__name__, e)}
    return {"ok": not bad, "check": "%s audit (Check games, full)" % mode, "why": "" if not bad else " / ".join(bad)}


class Watch(threading.Thread):
    """While the worker runs: when the sources went, when a checking tool ran."""

    def __init__(self, sources):
        super().__init__(daemon=True)
        self.sources, self.t0, self.stop = sources, time.time(), False
        self.gone = None
        self.verify = []          # seconds (since start) a checking tool was seen running

    def run(self):
        while not self.stop:
            if self.gone is None and not any(os.path.exists(s) for s in self.sources):
                self.gone = round(time.time() - self.t0, 1)
            if verify_running():
                self.verify.append(round(time.time() - self.t0, 1))
            time.sleep(0.25)


def cmd_compress(case_file):
    with open(case_file) as f:
        case = json.load(f)
    key, system = case["key"], case["system"]
    sysdir = os.path.join(rommlib.ROMS_ROOT, system)
    sources = [os.path.join(sysdir, s) for s in case["inputs"]]
    res = {"key": key, "system": system, "mode": case["mode"]}
    # the switches a user turns on in Settings (everything else of the converters off)
    cfg = rommlib.load_config()
    cfg.update(case["settings"])
    # a test box's disk is small: room is left for the input and its output, not the 20 GB default
    cfg["keep_free_gb"] = case.get("keep_free_gb", 1)
    rommlib.save_config(cfg)
    if case.get("tool"):
        _, err = ensure_tool(case["tool"])
        res["tool"] = case["tool"]
        if err:
            res.update(compressed=False, verified=False, why="the app's install of %s failed: %s" % (case["tool"], err))
            return out(res)
    files = [s for s in sources if os.path.isfile(s)]
    res["size_before"] = sum(_size(s) for s in sources)
    orig = hashes(files[0]) if len(files) == 1 else None
    if case["mode"] == "xiso" and orig:
        off = rommlib.xbox_partition(files[0])
        res["partition"] = off
        if off:
            res["partition_crc_expected"] = _crc_from(files[0], off)    # what the cut image must hold
    log0 = _log_size()
    queue0 = set(os.listdir(rommlib.CHD_QUEUE_DIR)) if os.path.isdir(rommlib.CHD_QUEUE_DIR) else set()
    t0 = time.time()
    plan = subprocess.run([sys.executable, os.path.join(APP, "rb_chd_plan.py"), "--systems", system,
                           "--only", PREFIX + key], cwd=APP, capture_output=True, text=True, errors="replace")
    res["planner"] = _ours(plan.stdout + plan.stderr, key)[-20:]
    res["planner_rc"] = plan.returncode
    tasks = sorted(set(os.listdir(rommlib.CHD_QUEUE_DIR)) - queue0)
    res["tasks"] = len(tasks)
    if tasks:
        w = Watch(files)
        w.start()
        worker = subprocess.Popen([sys.executable, os.path.join(APP, "rb_chd.py")], cwd=APP,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        end = time.time() + case.get("timeout", 4 * 3600)
        while worker.poll() is None:
            # a task kept for later (no room on this box, memory a game needs) would wait for good
            if time.time() > end or any(r["result"] == "waiting" for r in _results(set(tasks))):
                worker.terminate()
                try:
                    worker.wait(60)
                except subprocess.TimeoutExpired:
                    worker.kill()
                res["worker_stopped"] = "timeout" if time.time() > end else "waiting"
                break
            time.sleep(2)
        res["worker_rc"] = worker.wait()
        w.stop = True
        w.join()
        res["sources_gone_at"] = w.gone
        res["verify_seen"] = [w.verify[0], w.verify[-1]] if w.verify else []
    res["seconds"] = round(time.time() - t0, 1)
    res["results"] = _results(set(tasks))
    res["log"] = _log_since(log0, key)
    outs = _outputs(case, sysdir)
    if case["mode"] == "xiso":
        outs = [s for s in sources if os.path.exists(s)] or outs
    res["outputs"] = [os.path.relpath(p, sysdir) for p in outs]
    res["size_after"] = sum(_size(p) for p in outs)
    res["sources_left"] = [os.path.relpath(s, sysdir) for s in sources if os.path.exists(s)]
    ok_result = bool(res["results"]) and all(r["result"] == "ok" for r in res["results"])
    if case["mode"] == "xiso":
        ok_result = bool(outs) and res.get("partition") is not None and \
            res["size_after"] == res["size_before"] - res["partition"]
        res["compressed"] = ok_result
    else:
        res["compressed"] = ok_result and bool(outs) and not res["sources_left"]
    if not res["compressed"]:
        why = [r["message"] for r in res["results"] if r.get("message")]
        res["why"] = why[-1] if why else (res["planner"][-1] if res["planner"] else
                                          "the planner queued nothing" if not tasks else "no output")
    # the original went only after a check: the result is "ok" (the worker checks before it deletes),
    # and where the check is a separate tool, it was seen running while the original was still there
    if res["compressed"] and res.get("verify_seen"):
        gone = res.get("sources_gone_at")      # None: deleted after the last look (the worker's last step)
        res["checked_before_delete"] = gone is None or res["verify_seen"][0] <= gone
    elif res["compressed"]:
        res["checked_before_delete"] = None        # checked in the worker's own process: the log says
    checks = [_app_check(case, p, orig) for p in outs] if res["compressed"] else []
    res["checks"] = checks
    res["verified"] = bool(checks) and all(c["ok"] for c in checks)
    if case["mode"] == "xiso" and res["compressed"] and res.get("partition_crc_expected"):
        res["partition_crc"] = _crc_from(outs[0], 0)       # byte for byte the original's game partition
        res["verified"] = res["verified"] and res["partition_crc"] == res["partition_crc_expected"]
    out(res)


# ------------------------------------------------------------------ play

def shot(path):
    """batocera-screenshot to path; True when a PNG came out."""
    try:
        subprocess.run(["batocera-screenshot", path], env=display_env(), capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return os.path.isfile(path) and os.path.getsize(path) > 0


THUMB = (64, 36)


def thumb(path, whole=False):
    """The middle of the screenshot (the game's picture, without the bezel art Batocera
    draws around it; whole: all of it) as 64x36 grey bytes (ffmpeg, Batocera's own), or None."""
    crop = "" if whole else "crop=iw*0.6:ih*0.8,"
    try:
        r = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", path, "-vf",
                            crop + "scale=%d:%d,format=gray" % THUMB, "-f", "rawvideo", "-"],
                           capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if len(r.stdout) == THUMB[0] * THUMB[1] else None


def window_not_game(path):
    """The screen is mostly one flat light colour: an emulator's own window or an error
    box on it (RPCS3's "Booting failed", Cemu's "Could not decrypt title"), not a game."""
    t = thumb(path, whole=True) if path and os.path.exists(path) else None
    if not t:
        return False
    mid = sorted(t)[len(t) // 2]
    return mid >= 170 and sum(1 for v in t if abs(v - mid) <= 6) >= 0.6 * len(t)


def compare(a, b):
    """(fraction of thumbnail pixels that changed, the after picture's spread)."""
    if not a or not b:
        return None, None
    changed = sum(1 for x, y in zip(a, b) if abs(x - y) > 16) / len(a)
    mean = sum(b) / len(b)
    sd = (sum((v - mean) ** 2 for v in b) / len(b)) ** 0.5
    return round(changed, 3), round(sd, 1)


def _launch_logs():
    got = {}
    for n in LAUNCH_LOGS:
        try:
            with open(os.path.join(LOGS, n), "rb") as f:
                got[n] = f.read().decode("utf-8", "replace")
        except OSError:
            got[n] = ""
    return got


# standalone emulators' own logs (rewritten at each start): read when written after the launch
EMU_LOGS = ("/userdata/system/cache/rpcs3/RPCS3.log", "/userdata/system/logs/cemu-log.txt",
            "/userdata/system/configs/pcsx2/logs/emulog.txt", "/userdata/system/configs/xemu/xemu.log")
EMU_FAIL = re.compile(r"^\W?F \d|booting .{0,200} failed|fatal error|failed to (boot|load|open)", re.I)


def _emu_log_fails(since, path):
    got = []
    for p in EMU_LOGS:
        try:
            if os.path.getmtime(p) < since:
                continue
            with open(p, "rb") as f:
                text = f.read().decode("utf-8", "replace")
        except OSError:
            continue
        got += ["%s: %s" % (os.path.basename(p), _scrub(ln, path)) for ln in text.splitlines() if EMU_FAIL.search(ln)]
    return got[:10]


def _scrub(line, path):
    """A log line without the game's path, and with anything quoted cut (emulators quote a
    game's own title, which must never reach the results)."""
    line = line.replace(path, "<game>")
    return re.sub(r'"[^"]*"|\'[^\']*\'', '"…"', line)[:300]


def stop_game(new_pids):
    """Stop the emulator the way ES does it (/emukill), then make sure."""
    try:
        es("/emukill", timeout=10)
    except Exception:
        pass
    end = time.time() + 30
    while time.time() < end and running():
        time.sleep(1)
    if running():
        for sig in (signal.SIGTERM, signal.SIGKILL):
            for pid in new_pids:
                try:
                    os.kill(pid, sig)
                except OSError:
                    pass
            time.sleep(5)
            if not running():
                break
    end = time.time() + 30
    while time.time() < end and running() is None:
        time.sleep(1)                       # ES answering again
    return running()


def cmd_play(system, path, shotdir, wait=25):
    wait = float(wait)
    os.makedirs(shotdir, exist_ok=True)
    res = {"system": system, "file": os.path.basename(path)}
    if running() is None:
        res.update(plays=False, judged=False, why="EmulationStation doesn't answer")
        return out(res)
    if running():
        stop_game(())
    if not takes(system, path):
        # ES never lists it (a PS2 .cue, a PS3 .zip): said now, not after wait_listed's five minutes
        res.update(plays=False, judged=True, listed=False,
                   why="EmulationStation doesn't list the file (%s isn't an extension this system takes)"
                       % (os.path.splitext(path)[1].lower() or "a name without one"))
        return out(res)
    if not wait_listed(system, path):
        res.update(plays=False, judged=True, listed=False,
                   why="EmulationStation doesn't list the file (not an extension this system takes?)")
        return out(res)
    res["listed"] = True
    try:
        res["bios_all_missing"] = rommlib.bios_all_missing(system)
    except Exception as e:            # what Play now would hit (reported, not worked around)
        res["app_error"] = "rommlib.bios_all_missing(%r): %s: %s" % (system, e.__class__.__name__, e)
    time.sleep(3)
    before_png = os.path.join(shotdir, "before.png")
    shot(before_png)
    before = thumb(before_png)
    try:
        os.remove(before_png)               # ES's own screen: only its thumbnail is kept
    except OSError:
        pass
    pids0 = procs()
    logs0 = _launch_logs()
    t0 = time.time()
    try:
        es("/launch", path, timeout=10)
    except Exception as e:
        res.update(plays=False, judged=False, why="POST /launch failed: %s" % e)
        return out(res)
    seen, started_path, comms = set(), False, {}
    mid_png = os.path.join(shotdir, "mid.png")
    while time.time() - t0 < wait:
        time.sleep(1)
        now = procs()
        for pid, comm in now.items():
            if pid not in pids0 and comm not in NOT_EMULATOR and not comm.startswith(("kworker", "batocera-")):
                seen.add(pid)
                comms[comm] = comms.get(comm, round(time.time() - t0, 1))
        if running() == path:
            started_path = True
        if not os.path.exists(mid_png) and time.time() - t0 >= wait * 0.6:
            shot(mid_png)
    now = procs()
    alive = sorted(now[p] for p in seen if p in now)
    still = running() == path
    after_png = os.path.join(shotdir, "after.png")
    got_shot = shot(after_png)
    after = thumb(after_png) if got_shot else None
    changed, spread = compare(before, after)
    mid_changed, mid_spread = compare(before, thumb(mid_png)) if os.path.exists(mid_png) else (None, None)
    if mid_spread is not None and (spread is None or mid_spread > spread):
        changed, spread = mid_changed, mid_spread         # a game may be between two screens at the end
    logs = _launch_logs()
    fails, notes, resized = _emu_log_fails(t0, path), [], 0
    for n in LAUNCH_LOGS:
        new = logs[n][len(logs0[n]):] if logs[n].startswith(logs0[n]) else logs[n]
        fails += [_scrub(ln, path) for ln in new.splitlines() if LOAD_FAIL.search(ln) and not NOT_A_FAILURE.search(ln)]
        notes += [_scrub(ln, path) for ln in new.splitlines() if LOG_NOTE.search(ln) and not NOT_A_FAILURE.search(ln)
                  and "Resized fullscreen" not in ln]
        resized += new.count("Resized fullscreen resolution")
    left = stop_game(seen)
    res.update({"es_ran_it": started_path, "running_at_end": still, "emulators": sorted(comms),
                "alive_at_end": alive, "screen_changed": changed, "screen_spread": spread,
                "screenshot": got_shot, "load_failures": fails[-15:], "log_notes": notes[-15:], "seconds": round(time.time() - t0, 1),
                "es_back": left == ""})
    picture = changed is not None and changed >= 0.05 and spread >= 6
    res["emulator_window"] = window_not_game(after_png) and (not os.path.exists(mid_png) or window_not_game(mid_png))
    picture = picture and not res["emulator_window"]
    res["runs"] = bool(started_path and still and alive and not fails)
    # RetroArch on a VM whose screen mode it can't set (Batocera 44 here) switches modes over and
    # over and shows nothing: the picture can't be judged there, only that it runs
    _, menu_spread = compare(before, before)
    res["picture_blocked"] = ("the emulator kept switching screen modes (%d times)" % resized if resized >= 10 else
                              "screenshots come out black here, even of ES's own menu" if menu_spread is not None
                              and menu_spread < 3 else "")
    res["plays"] = bool(res["runs"] and picture)
    res["judged"] = True
    if not res["plays"]:
        res["why"] = ("ES never ran it" if not started_path else
                      "the emulator quit within %ds" % wait if not (still and alive) else
                      "the launch log reports a failure" if fails else
                      "the emulator shows its own window or an error box, not a game" if res["emulator_window"] else
                      "no screenshot" if not got_shot else
                      "the screen stayed the same or near blank (changed %s, spread %s)" % (changed, spread))
    out(res)


def main():
    cmd, args = sys.argv[1], sys.argv[2:]
    fn = {"info": cmd_info, "prepare": cmd_prepare, "bios": cmd_bios, "derive": cmd_derive,
          "checksum": cmd_checksum, "compress": cmd_compress, "play": cmd_play, "clean": cmd_clean}[cmd]
    fn(*args)


if __name__ == "__main__":
    main()
