#!/usr/bin/env python3
"""Format harness: compress and play every format the app touches, on a throwaway
Batocera test box, the way a user does it. Host side; tools/formats/boxside.py runs
on the box. The cases come from the converter table (tools/formats/cases.py).

  run.py --pick --library DIR [--library DIR]... --bios DIR [--lab DIR]
        choose each case's input (the smallest suitable file; a --lab folder of
        homebrew is preferred) and write local/formats/inputs.json
  run.py --box HOST:PORT[,HOST:PORT...] --base 43.1|44 [--cases K1,K2 | --changed-since REF | --all]
        [--inputs local/formats/inputs.json] [--out build/formats] [--keep] [--ssh-key FILE]
        several boxes of the same Batocera share the cases: each takes the next one
        when it's free, so the run takes about as long as its share (a box plays
        one game at a time; nothing about a case changes)

Per case, on the box: the input goes into roms/<system>/ as fmt-<key>.<ext> (cue and
gdi sheets rewritten to the neutral track names), the BIOS files Batocera lists for
the system are placed from --bios by MD5, the original is launched through ES as a
control, then the app's own planner (rb_chd_plan.py) and worker (rb_chd.py) compress
it, the app's own check (Check games, full) checks the result, and the result is
launched the same way. Results: <out>/<base>/<key>.json, summary.json, summary.md,
and screenshots; local/formats/passed.json records the outcome per case with a hash
of its areas (tools/release.py stable reads it), as each case ends: a run that is
stopped keeps what it finished.

Inputs come only from the given folders, read-only; nothing is downloaded but the
converter tools the app itself downloads. Exit status 1 when a case that could be
judged failed.
"""
import argparse
import collections
import fcntl
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cases as _cases  # noqa: E402

REPO = _cases.REPO
rommlib = _cases.rommlib
INPUTS = os.path.join(REPO, "local", "formats", "inputs.json")
PASSED = os.path.join(REPO, "local", "formats", "passed.json")
BIOS_INDEX = os.path.join(REPO, "local", "formats", "bios-md5.json")
APP_FILES = ["romm", "rbui", "rommlib.py", "rommbrowser.pygame", "fonts", "icon.png", "LICENSE"]
BOX_APP = "/userdata/roms/pygame/rommbrowser"
BOX_TOOL = "/userdata/system/fmt-harness"
PREFIX = "fmt-"
CUE_FILE = re.compile(r'^([﻿ \t]*FILE[ \t]+)(?:"([^"]+)"|(\S+))(.*)$', re.I)
GDI_LINE = re.compile(r'^(\s*\d+\s+\d+\s+\d+\s+\d+\s+)("([^"]+)"|(\S+))(.*)$')
PLAY_WAIT = {"ps2": 40, "ps3": 45, "xbox": 40, "xbox360": 45, "wiiu": 45, "wii": 35, "gamecube": 35}
MIN_DERIVE = 32 << 20       # a smaller .rvz/.wux is a near-empty disc (a channel or data disc), no game to play


_who = threading.local()      # .box: the box this thread runs cases on, when there are several


def say(msg):
    print("[formats] " + getattr(_who, "box", "") + msg, flush=True)


# ------------------------------------------------------------------ the box

class Box:
    def __init__(self, spec, key=None):
        self.host, _, port = spec.rpartition(":")
        self.port = port or "22"
        self.host = self.host or "127.0.0.1"
        opts = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
                "-o", "LogLevel=ERROR", "-o", "ServerAliveInterval=30"] + (["-i", key] if key else [])
        self.ssh_argv = ["ssh", "-p", self.port] + opts + ["root@" + self.host]
        self.scp_argv = ["scp", "-q", "-P", self.port] + opts

    def run(self, cmd, stdin=None, check=False, timeout=None):
        r = subprocess.run(self.ssh_argv + [cmd], stdin=stdin, capture_output=True, text=True, errors="replace",
                           timeout=timeout)
        if check and r.returncode:
            raise RuntimeError("box: %s -> %d %s" % (cmd[:80], r.returncode, r.stderr[-300:]))
        return r

    def side(self, *args, timeout=None):
        """boxside.py ARGS on the box: its FMTJSON result."""
        r = self.run("cd %s && python3 %s/boxside.py %s" % (BOX_APP, BOX_TOOL, " ".join(shlex.quote(str(a)) for a in args)),
                     timeout=timeout)
        for line in reversed(r.stdout.splitlines()):
            if line.startswith("FMTJSON "):
                return json.loads(line[8:])
        raise RuntimeError("boxside %s: no result (rc %d): %s" % (args[0], r.returncode, (r.stderr or r.stdout)[-600:]))

    def put_stream(self, src, remote, mtime=None):
        """Copy a readable stream to remote (via .part, then renamed)."""
        q = shlex.quote(remote)
        p = subprocess.Popen(self.ssh_argv + ["mkdir -p %s && cat > %s.part && mv %s.part %s" % (
            shlex.quote(os.path.dirname(remote)), q, q, q)], stdin=subprocess.PIPE)
        while True:
            b = src.read(4 << 20)
            if not b:
                break
            p.stdin.write(b)
        p.stdin.close()
        if p.wait():
            raise RuntimeError("copying to the box failed (%s)" % os.path.basename(remote))
        if mtime:
            self.run("touch -d @%d %s" % (mtime, q), check=True)

    def put_bytes(self, data, remote, mtime=None):
        self.put_stream(io.BytesIO(data), remote, mtime)

    def alive(self):
        try:
            return self.run("true", timeout=30).returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            return False

    def get(self, remote, local):
        os.makedirs(os.path.dirname(local), exist_ok=True)
        return subprocess.run(self.scp_argv + ["root@%s:%s" % (self.host, remote), local],
                              capture_output=True).returncode == 0


def install_app(box):
    """The working tree's app and boxside.py onto the box."""
    files = [f for f in APP_FILES if os.path.exists(os.path.join(REPO, f))]
    files += sorted(f for f in os.listdir(REPO) if f.startswith("rb_") and f.endswith(".py"))
    tar = subprocess.Popen(["tar", "cf", "-", "--exclude=__pycache__"] + files, cwd=REPO, stdout=subprocess.PIPE)
    r = subprocess.run(box.ssh_argv + ["mkdir -p %s && cd %s && tar xf -" % (BOX_APP, BOX_APP)], stdin=tar.stdout,
                       capture_output=True)
    tar.wait()
    if r.returncode:
        raise RuntimeError("installing the app on the box failed")
    with open(os.path.join(HERE, "boxside.py"), "rb") as f:
        box.put_bytes(f.read(), BOX_TOOL + "/boxside.py")


# ------------------------------------------------------------------ inputs

def load_inputs(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _rename_tracks(text, key, sheet_ext):
    """A cue/gdi sheet with every track file renamed fmt-<key>...: (new text, {old: new})."""
    refs = []
    lines = text.splitlines()
    rx = CUE_FILE if sheet_ext == ".cue" else GDI_LINE
    for ln in lines[(0 if sheet_ext == ".cue" else 1):]:
        m = rx.match(ln)
        if m:
            refs.append(m.group(2) if sheet_ext == ".cue" and m.group(2) else
                        m.group(3) if sheet_ext == ".cue" else (m.group(3) or m.group(4)))
    uniq = list(dict.fromkeys(refs))
    mapping = {}
    for i, ref in enumerate(uniq, 1):
        ext = os.path.splitext(ref)[1].lower()
        mapping[ref] = "%s%s%s" % (PREFIX + key, "" if len(uniq) == 1 else " (Track %02d)" % i, ext)
    out = []
    for n, ln in enumerate(lines):
        m = rx.match(ln) if (sheet_ext == ".cue" or n > 0) else None
        if m and sheet_ext == ".cue":
            ref = m.group(2) or m.group(3)
            ln = '%s"%s"%s' % (m.group(1), mapping[ref], m.group(4))
        elif m:
            ref = m.group(3) or m.group(4)
            ln = '%s"%s"%s' % (m.group(1), mapping[ref], m.group(5))
        out.append(ln)
    return "\r\n".join(out) + "\r\n", mapping


def _member_lookup(z, ref):
    """The zip member a sheet's track name points at (folder-less, any case)."""
    want = ref.replace("\\", "/").split("/")[-1].lower()
    for i in z.infolist():
        if not i.is_dir() and i.filename.split("/")[-1].lower() == want:
            return i
    return None


def stage(box, case, entry, sysdir):
    """Put the case's input on the box under neutral names. Returns (main file
    relative to the system folder, every input file relative to it)."""
    key, src = case.key, entry["path"]
    mtime = int(time.time()) - 3600                 # the planner leaves files written in the last minute alone
    if entry["kind"] == "loose":
        ext = os.path.splitext(src)[1].lower()
        name = PREFIX + key + ext
        with open(src, "rb") as f:
            box.put_stream(f, "%s/%s" % (sysdir, name), mtime)
        return name, [name]
    z = zipfile.ZipFile(src)
    if entry["kind"] == "zip-keep":
        # the zip as RomM keeps it, its one disc image renamed (same compression)
        info = z.getinfo(entry["member"])
        name = PREFIX + key + ".zip"
        r, w = os.pipe()
        rd = os.fdopen(r, "rb")

        def writer():
            with os.fdopen(w, "wb") as out, zipfile.ZipFile(out, "w") as zo:
                zi = zipfile.ZipInfo(PREFIX + key + os.path.splitext(info.filename)[1].lower(), info.date_time)
                zi.compress_type = info.compress_type
                with z.open(info) as fi, zo.open(zi, "w", force_zip64=True) as fo:
                    for b in iter(lambda: fi.read(4 << 20), b""):
                        fo.write(b)
        t = threading.Thread(target=writer, daemon=True)
        t.start()
        box.put_stream(rd, "%s/%s" % (sysdir, name), mtime)
        t.join()
        return name, [name]
    main = z.getinfo(entry["member"])
    ext = os.path.splitext(main.filename)[1].lower()
    if ext in (".cue", ".gdi"):
        text = z.read(main).decode("utf-8", "surrogateescape")
        new, mapping = _rename_tracks(text, key, ext)
        sheet = PREFIX + key + ext
        names = [sheet]
        for ref, neutral in mapping.items():
            info = _member_lookup(z, ref)
            if info is None:
                raise RuntimeError("a track the sheet names isn't in the zip")
            with z.open(info) as f:
                box.put_stream(f, "%s/%s" % (sysdir, neutral), mtime)
            names.append(neutral)
        box.put_bytes(new.encode("utf-8", "surrogateescape"), "%s/%s" % (sysdir, sheet), mtime)
        return sheet, names
    name = PREFIX + key + ext
    with z.open(main) as f:
        box.put_stream(f, "%s/%s" % (sysdir, name), mtime)
    return name, [name]


# ------------------------------------------------------------------ BIOS and keys

def bios_index(root):
    """{md5: path} over the owner's BIOS folder (cached by size and mtime)."""
    try:
        cache = json.load(open(BIOS_INDEX))
    except (OSError, ValueError):
        cache = {}
    out, fresh = {}, {}
    for d, _, fs in os.walk(root):
        for n in fs:
            p = os.path.join(d, n)
            try:
                st = os.stat(p)
            except OSError:
                continue
            tag = "%d:%d" % (st.st_size, int(st.st_mtime))
            hit = cache.get(p)
            if hit and hit[0] == tag:
                md5 = hit[1]
            elif st.st_size > 64 << 20:
                continue
            else:
                h = hashlib.md5()
                with open(p, "rb") as f:
                    for b in iter(lambda: f.read(1 << 20), b""):
                        h.update(b)
                md5 = h.hexdigest()
            members = hit[2] if hit and hit[0] == tag and len(hit) > 2 else None
            if members is None and n.lower().endswith(".zip"):
                members = []
                try:
                    with zipfile.ZipFile(p) as z:
                        for i in z.infolist():
                            if not i.is_dir() and i.file_size < 64 << 20:
                                members.append(hashlib.md5(z.read(i)).hexdigest())
                except (zipfile.BadZipFile, OSError, NotImplementedError):
                    pass
            fresh[p] = [tag, md5] + ([members] if members is not None else [])
            out.setdefault(md5, p)
            for m in members or ():
                out.setdefault("zip:" + m, []).append(p)
    os.makedirs(os.path.dirname(BIOS_INDEX), exist_ok=True)
    with open(BIOS_INDEX, "w") as f:
        json.dump(fresh, f)
    return out


def place_bios(box, system, index):
    """Batocera's own list (on the box) says what the system wants; files of the
    owner's with those MD5s go where it wants them."""
    need = box.side("bios", system)
    placed, unmatched = [], []
    for m in need["missing"]:
        src = index.get(m["md5"])
        if not src:
            unmatched.append(m["file"])
            continue
        if m["file"] in placed:
            continue
        with open(src, "rb") as f:
            box.put_stream(f, m["path"])
        placed.append(m["file"])
    for zb in need.get("zipped", []):
        # the owner's zip holding the most of the files Batocera names by MD5
        score = {}
        for m in zb["md5s"]:
            for p in index.get("zip:" + m.lower(), []):
                score[p] = score.get(p, 0) + 1
        if not score:
            unmatched.append(zb["file"])
            continue
        best = max(sorted(score), key=lambda p: score[p])
        with open(best, "rb") as f:
            box.put_stream(f, zb["path"])
        placed.append("%s (%d of %d files by MD5)" % (zb["file"], score[best], len(zb["md5s"])))
    after = box.side("bios", system) if placed else need
    placed_files = set(placed)
    unmatched = sorted(set(unmatched) - placed_files)          # one of several accepted MD5s was enough
    res = {"required": len(need["required"]), "placed": placed, "unmatched": unmatched,
           "all_missing": after["all_missing"]}
    if need.get("app_error"):
        res["app_error"] = need["app_error"]
    return res


def place_key(box, case, entry):
    """A PS3 disc key from the owner's key set, by the disc's own name, saved under the
    case's neutral name (bios/ps3keys/fmt-<key>.key): the app checks it against the disc."""
    k = entry.get("key")
    if not k:
        return "none"
    with zipfile.ZipFile(k["zip"]) as z:
        data = z.read(k["member"])
    box.put_bytes(data, "/userdata/bios/ps3keys/%s%s%s" % (PREFIX, case.key, os.path.splitext(k["member"])[1].lower()))
    return "the disc's own key, from the owner's key set"


# ------------------------------------------------------------------ one case

def judge(case_res):
    """pass / fail / unjudged, and why."""
    c, orig, conv = case_res.get("compress") or {}, case_res.get("original_play") or {}, case_res.get("converted_play") or {}
    if not c.get("compressed") and c.get("worker_stopped") == "waiting":
        # the worker kept it for later: this box hasn't the room (or memory) the app asks for
        return "unjudged", "not compressed on this box: %s" % c.get("why", "?")
    if not c.get("compressed"):
        return "fail", "not compressed: %s" % c.get("why", "?")
    if not c.get("verified"):
        return "fail", "the app's check of the result failed: %s" % "; ".join(x.get("why", "") for x in c.get("checks", []))
    if conv.get("plays"):
        return "pass", ""
    if orig.get("plays"):
        return "fail", "the original plays and the result doesn't: %s" % conv.get("why", "?")
    if orig.get("runs") and conv and not conv.get("runs"):
        return "fail", "the original's emulator keeps running and the result's doesn't: %s" % conv.get("why", "?")
    if orig.get("runs") and conv.get("runs") and (orig.get("picture_blocked") or conv.get("picture_blocked")):
        return "unjudged", ("both run %ds without a load failure, but the picture can't be judged on this box (%s)"
                            % (int(conv.get("seconds") or 0), conv.get("picture_blocked") or orig.get("picture_blocked")))
    ctl = orig.get("why") or "no control"
    return "unjudged", "neither plays on this box (original: %s; result: %s)" % (ctl, conv.get("why", "?"))


def run_case(box, case, entry, outdir, index, keep=False):
    key = case.key
    sysdir = "/userdata/roms/" + case.system
    res = {"key": key, "system": case.system, "mode": case.mode, "areas_hash": case.areas_hash(),
           "input_kind": entry["kind"], "derived": entry.get("derive") or ""}
    shots = "/userdata/system/fmt-harness/shots/" + key
    box.side("clean", key)
    box.run("rm -rf %s" % shlex.quote(shots))
    t0 = time.time()
    main, files = stage(box, case, entry, sysdir)
    res["stage_seconds"] = round(time.time() - t0, 1)
    if entry.get("derive"):
        frm, how, to = case.derive
        d = box.side("derive", how, "%s/%s" % (sysdir, main), "%s/%s%s%s" % (sysdir, PREFIX, key, to))
        res["derive"] = d
        if not d.get("ok"):
            res.update(status="fail", note="couldn't make the input: %s" % d.get("why"))
            if not keep:
                box.side("clean", key)
            return res
        main, files = PREFIX + key + to, [PREFIX + key + to]
    res["bios"] = place_bios(box, case.system, index) if index is not None else {}
    if case.keys == "ps3keys":
        res["disc_key"] = place_key(box, case, entry)
    elif case.keys:
        res["disc_key"] = "none"
    wait = PLAY_WAIT.get(case.system, 25)
    spec = {"key": key, "system": case.system, "mode": case.mode, "settings": case.settings,
            "tool": case.tool, "inputs": files, "outputs": list(case.outputs)}
    if case.mode == "squashfs":
        # made bootable first (as the planner does), so the control plays the image xemu boots
        prep = dict(spec, mode="xiso", settings=_cases.BY_KEY["xiso-xbox"].settings, outputs=[".iso"])
        box.put_bytes(json.dumps(prep).encode(), BOX_TOOL + "/case.json")
        res["prep"] = box.side("compress", BOX_TOOL + "/case.json", timeout=4 * 3600)
    say("%s: playing the original (control)" % key)
    res["original_play"] = box.side("play", case.system, "%s/%s" % (sysdir, main), shots + "/original", wait,
                                    timeout=900)
    say("%s: compressing" % key)
    box.put_bytes(json.dumps(spec).encode(), BOX_TOOL + "/case.json")
    res["compress"] = box.side("compress", BOX_TOOL + "/case.json", timeout=6 * 3600)
    c = res["compress"]
    if c.get("compressed") and c.get("outputs"):
        target = sorted(c["outputs"], key=lambda n: (not n.lower().endswith(tuple(case.outputs)), n))[0]
        say("%s: playing the result" % key)
        res["converted_play"] = box.side("play", case.system, "%s/%s" % (sysdir, target), shots + "/converted", wait,
                                         timeout=900)
    shutil.rmtree(os.path.join(outdir, key), ignore_errors=True)       # never an older run's screenshots
    for which in ("original", "converted"):
        for n in ("mid.png", "after.png"):
            box.get("%s/%s/%s" % (shots, which, n), os.path.join(outdir, key, "%s-%s" % (which, n)))
    res["status"], res["note"] = judge(res)
    if (res.get("bios") or {}).get("app_error"):
        res["note"] = ("APP ERROR " + res["bios"]["app_error"] + ("; " + res["note"] if res["note"] else ""))
    res["seconds"] = round(time.time() - t0, 1)
    if not keep:
        box.side("clean", key)
        box.run("rm -f /userdata/bios/ps3keys/%s%s.*" % (PREFIX, key))
    return res


# ------------------------------------------------------------------ summary

def summary_row(r):
    c = r.get("compress") or {}
    o, v = r.get("original_play") or {}, r.get("converted_play") or {}

    def play(p):
        if not p:
            return "-"
        if p.get("listed") is False:
            return "not listed"
        return "yes" if p.get("plays") else "runs, no picture" if p.get("runs") else "no"
    return {"key": r["key"], "compressed": "yes" if c.get("compressed") else ("-" if r["status"] == "no-input" else "no"),
            "verified": "yes" if c.get("verified") else ("-" if not c else "no"),
            "original plays": play(o), "converted plays": play(v), "status": r["status"],
            "note": " ".join((r.get("note") or "").split())[:160]}


def write_summary(outdir, rows, info):
    cols = ["key", "compressed", "verified", "original plays", "converted plays", "status", "note"]
    md = ["# Format harness: %s" % info.get("label", ""), "",
          "Batocera %s, Python %s, app %s" % (info.get("batocera"), info.get("python"), info.get("app")), "",
          "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        md.append("| " + " | ".join(str(r[c]).replace("|", "/") for c in cols) + " |")
    with open(os.path.join(outdir, "summary.md"), "w") as f:
        f.write("\n".join(md) + "\n")
    with open(os.path.join(outdir, "summary.json"), "w") as f:
        json.dump({"box": info, "rows": rows}, f, indent=1)
    print("\n".join(md))


def record_passed(base, results):
    """Add results to passed.json. Under a lock, and whole or not at all: the
    other base's run, and this run's other boxes, write it too."""
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
    os.makedirs(os.path.dirname(PASSED), exist_ok=True)
    with open(PASSED + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            data = json.load(open(PASSED))
        except (OSError, ValueError):
            data = {}
        runs = data.setdefault(base, {})
        for k in [k for k in runs if k not in _cases.BY_KEY]:
            del runs[k]                                     # a case the table no longer has
        for r in results:
            runs[r["key"]] = {"status": r["status"], "areas_hash": r.get("areas_hash", ""), "commit": head,
                              "when": time.strftime("%Y-%m-%d %H:%M"), "note": (r.get("note") or "")[:200]}
        with open(PASSED + ".part", "w") as f:
            json.dump(data, f, indent=1, sort_keys=True)
        os.replace(PASSED + ".part", PASSED)


def changed_since(ref):
    a = subprocess.run(["git", "diff", "--name-only", ref], cwd=REPO, capture_output=True, text=True, check=True).stdout
    b = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], cwd=REPO, capture_output=True,
                       text=True).stdout
    return [p for p in (a + b).splitlines() if p]


def cmd_run(a):
    inputs = load_inputs(a.inputs)
    if a.all:
        todo = list(_cases.CASES)
    elif a.cases:
        want = [k.strip() for k in a.cases.split(",") if k.strip()]
        bad = [k for k in want if k not in _cases.BY_KEY]
        if bad:
            sys.exit("no such case: %s" % ", ".join(bad))
        todo = [_cases.BY_KEY[k] for k in want]
    elif a.changed_since:
        todo = _cases.cases_touched(changed_since(a.changed_since))
        say("changed since %s: %d case(s)" % (a.changed_since, len(todo)))
    else:
        sys.exit("say which cases: --cases, --changed-since or --all")
    if not todo:
        say("nothing to run")
        return 0
    specs = list(dict.fromkeys(x.strip() for b in a.box for x in b.split(",") if x.strip()))
    outdir = os.path.join(a.out, a.base)
    os.makedirs(outdir, exist_ok=True)
    bios_root = (inputs.get("_roots") or {}).get("bios")
    index = bios_index(bios_root) if bios_root and os.path.isdir(bios_root) else None
    if len(specs) > 1:
        todo.sort(key=lambda c: -_weight(c, inputs, outdir))     # the long ones first: the boxes end together
    queue = collections.deque(todo)
    cv = threading.Condition()
    state = {"busy": 0, "info": None}
    results = []

    def take():
        """The next case, or None when none is left and no box could still hand one back."""
        with cv:
            while not queue and state["busy"]:
                cv.wait(5)
            if not queue:
                return None
            state["busy"] += 1
            return queue.popleft()

    def done(back=None):
        with cv:
            state["busy"] -= 1
            if back is not None:
                queue.appendleft(back)
            cv.notify_all()

    def worker(spec):
        if len(specs) > 1:
            _who.box = "[%s] " % spec
        try:
            box = Box(spec, a.ssh_key)
            install_app(box)
            info = box.side("info")
            info["label"] = a.base
            say("box: Batocera %s, Python %s, app %s" % (info["batocera"], info["python"], info["app"]))
            info["es_bios_prompt"] = box.side("prepare", timeout=400).get("es_bios_prompt")
        except Exception as e:
            say("this box can't be used: %s: %s" % (e.__class__.__name__, e))
            return
        with cv:
            state["info"] = state["info"] or info
        while True:
            case = take()
            if case is None:
                return
            entry = (inputs.get("cases") or {}).get(case.key)
            if not entry or not entry.get("path"):
                r = {"key": case.key, "system": case.system, "mode": case.mode, "status": "no-input",
                     "areas_hash": case.areas_hash(),
                     "note": (entry or {}).get("why") or "no input picked (run --pick)"}
            else:
                say("%s: start" % case.key)
                try:
                    r = run_case(box, case, entry, outdir, index, keep=a.keep)
                except Exception as e:                       # one case's trouble never stops the rest
                    if not box.alive():
                        # the box is gone, not the case: another box takes it (none left: failed below)
                        say("%s: this box stopped answering (%s); it runs no more cases" % (case.key, e))
                        done(back=case)
                        return
                    r = {"key": case.key, "system": case.system, "mode": case.mode, "status": "fail",
                         "areas_hash": case.areas_hash(), "note": "harness error: %s: %s" % (e.__class__.__name__, e)}
                    try:
                        box.side("clean", case.key)
                    except Exception:
                        pass
            say("%s: %s %s" % (case.key, r["status"], r.get("note", "")))
            with cv:
                with open(os.path.join(outdir, case.key + ".json"), "w") as f:
                    json.dump(r, f, indent=1, sort_keys=True)
                results.append(r)
                record_passed(a.base, [r])              # now: a run that's stopped keeps what it finished
            done()

    threads = [threading.Thread(target=worker, args=(spec,)) for spec in specs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if state["info"] is None:
        sys.exit("no box could be used")
    for case in queue:                                   # every box that could run it went away
        r = {"key": case.key, "system": case.system, "mode": case.mode, "status": "fail",
             "areas_hash": case.areas_hash(), "note": "harness error: no box left to run it"}
        say("%s: %s %s" % (case.key, r["status"], r["note"]))
        with open(os.path.join(outdir, case.key + ".json"), "w") as f:
            json.dump(r, f, indent=1, sort_keys=True)
        results.append(r)
        record_passed(a.base, [r])
    prev = {}
    try:
        prev = {row["key"]: row for row in json.load(open(os.path.join(outdir, "summary.json")))["rows"]}
    except (OSError, ValueError, KeyError):
        pass
    prev.update({r["key"]: summary_row(r) for r in results})
    prev = {k: v for k, v in prev.items() if k in _cases.BY_KEY}
    order = {c.key: i for i, c in enumerate(_cases.CASES)}
    write_summary(outdir, sorted(prev.values(), key=lambda r: order.get(r["key"], 999)), state["info"])
    return 1 if any(r["status"] == "fail" for r in results) else 0


def _weight(case, inputs, outdir):
    """About how long a case takes: what its last run here took, else a guess from
    its input's size (only the order of a run on several boxes hangs on it)."""
    try:
        with open(os.path.join(outdir, case.key + ".json")) as f:
            return float(json.load(f)["seconds"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    entry = (inputs.get("cases") or {}).get(case.key) or {}
    try:
        return 90 + os.path.getsize(entry["path"]) / (40 << 20)
    except (OSError, KeyError, TypeError):
        return 0


# ------------------------------------------------------------------ --pick

def _probe_head(case, z, info, n=0x10000):
    """The first n bytes of a candidate (zip member or file)."""
    if z is None:
        with open(info, "rb") as f:
            return f.read(n)
    with z.open(info) as f:
        return f.read(n)


def _ok_3ds(case, z, info, scratch):
    head = _probe_head(case, z, info, 0x8000)
    p = os.path.join(scratch, "probe.3ds")
    with open(p, "wb") as f:
        f.write(head)
    try:
        rommlib.z3ds_info(p)
        return True, ""
    except Exception as e:
        return False, str(e)
    finally:
        os.remove(p)


def _ok_xbox_full(case, z, info, scratch):
    """A full disc image (game partition after the video one): what make_xiso cuts."""
    opener = (lambda: z.open(info)) if z is not None else (lambda: open(info, "rb"))
    offs = sorted(rommlib.XBOX_PARTITIONS)
    with opener() as f:
        pos = 0
        for off in offs:
            want = off + 0x10000
            while pos < want:
                b = f.read(min(4 << 20, want - pos))
                if not b:
                    return False, "short"
                pos += len(b)
            magic = f.read(len(rommlib.XDVDFS_MAGIC))
            pos += len(magic)
            if magic == rommlib.XDVDFS_MAGIC:
                return True, ""
    return False, "already an XISO"


def _key_for(stem, keyzips):
    """The owner's PS3 key named exactly like the disc: {"zip", "member"} or None."""
    want = stem.casefold()
    for kz in keyzips:
        with zipfile.ZipFile(kz) as z:
            for n in z.namelist():
                base, ext = os.path.splitext(n.split("/")[-1])
                if ext.lower() in (".key", ".dkey") and base.casefold() == want:
                    return {"zip": kz, "member": n}
    return None


def _candidates(case, folders):
    """(size on disk, path) of every top-level file in the case's library folders, smallest first."""
    out = []
    for root in folders:
        for slug in case.library:
            d = os.path.join(root, slug)
            try:
                names = os.listdir(d)
            except OSError:
                continue
            for n in names:
                p = os.path.join(d, n)
                if n.startswith(".") or not os.path.isfile(p):
                    continue
                out.append((os.path.getsize(p), p))
    return sorted(out)


def _sheet_complete(z, sheet):
    text = z.read(sheet).decode("utf-8", "surrogateescape")
    ext = os.path.splitext(sheet.filename)[1].lower()
    refs = []
    for i, ln in enumerate(text.splitlines()):
        m = CUE_FILE.match(ln) if ext == ".cue" else (GDI_LINE.match(ln) if i else None)
        if m:
            refs.append((m.group(2) or m.group(3)) if ext == ".cue" else (m.group(3) or m.group(4)))
    return bool(refs) and all(_member_lookup(z, r) is not None for r in refs)


def pick_one(case, folders, keyzips, scratch, limit=40):
    """The smallest file in the library that is a suitable input for the case."""
    srcs = tuple(e for e in case.sources if e != ".zip")
    derive_from = case.derive[0] if case.derive else None
    direct, derived, tried = None, None, 0
    for size, p in _candidates(case, folders):
        if tried >= limit or direct:
            break
        ext = os.path.splitext(p)[1].lower()
        z, info, kind = None, None, None
        if ext in srcs and ext not in (".cue", ".gdi"):
            info, kind = p, "loose"
        elif ext == derive_from and derived is None and size >= MIN_DERIVE:
            info, kind = p, "loose-derive"
        elif ext == ".zip":
            try:
                z = zipfile.ZipFile(p)
            except (zipfile.BadZipFile, OSError):
                continue
            members = [i for i in z.infolist() if not i.is_dir()]
            hits = [i for i in members if os.path.splitext(i.filename)[1].lower() in srcs]
            if len(hits) != 1:
                if derive_from and derived is None and not hits:
                    dh = [i for i in members if os.path.splitext(i.filename)[1].lower() == derive_from
                          and i.file_size >= MIN_DERIVE]
                    if len(dh) == 1:
                        info, kind = dh[0], "zip-derive"
                if kind is None:
                    continue
            else:
                info = hits[0]
                kind = "zip-keep" if case.zipped else "zip"
                if os.path.splitext(info.filename)[1].lower() in (".cue", ".gdi") and not _sheet_complete(z, info):
                    continue
        else:
            continue
        tried += 1
        stem = os.path.splitext((info.filename if z is not None else os.path.basename(info)).split("/")[-1])[0]
        ok = True
        try:            # (ok, why): a dump the converter refuses, or an Xbox image already cut, isn't an input
            if case.mode == "z3ds":
                ok = _ok_3ds(case, z, info, scratch)[0]
            elif case.mode in ("xiso", "squashfs"):
                ok = _ok_xbox_full(case, z, info, scratch)[0]
        except Exception:
            ok = False
        if not ok:
            continue
        entry = {"path": p, "kind": kind.replace("-derive", ""), "size": size}
        if z is not None:
            entry["member"] = info.filename
        if case.keys == "ps3keys":
            k = _key_for(stem, keyzips)
            if not k:
                continue
            entry["key"] = k
        if kind.endswith("-derive"):
            entry["derive"] = case.derive[1]
            derived = derived or entry
        else:
            direct = entry
    return direct or derived


def cmd_pick(a):
    folders = (a.lab or []) + a.library
    keyzips = []
    for d, _, fs in os.walk(a.bios):
        keyzips += [os.path.join(d, n) for n in fs if n.lower().endswith(".zip") and "key" in n.lower()
                    and "ps3" in d.lower()]
    scratch = os.path.join(REPO, "build", "formats", "pick")
    os.makedirs(scratch, exist_ok=True)
    out = {"_roots": {"library": a.library, "lab": a.lab or [], "bios": a.bios}, "cases": {}}
    before = load_inputs(a.inputs).get("cases") or {}
    for case in _cases.CASES:
        if case.mode == "xpkg":
            # a disc that only starts a package: nothing in a file's name or first bytes says so, and
            # reading its file list means reading the image. Picked by hand: an entry already there stays
            e = before.get(case.key)
            if not (isinstance(e, dict) and e.get("path") and os.path.isfile(e["path"])):
                e = {"why": "pick it by hand: add a disc that only starts a package to %s"
                     % os.path.relpath(a.inputs, REPO)}
            out["cases"][case.key] = e
            say("%-22s %s" % (case.key, "kept as it was" if "path" in e else "no input (%s)" % e["why"]))
            continue
        if case.keys == "wiiukeys":
            have = any(os.path.isdir(os.path.join(a.bios, d)) and os.listdir(os.path.join(a.bios, d))
                       for d in ("wiiukeys", "wiiu", "cemu"))
            if not have:
                out["cases"][case.key] = {"why": "no Wii U disc keys in the BIOS folder"}
                say("%-22s no input (no Wii U disc keys)" % case.key)
                continue
        e = None
        for group in ([a.lab] if a.lab else []) + [a.library]:      # the homebrew folder first
            e = pick_one(case, group, keyzips, scratch)
            if e:
                break
        if e is None:
            have = any(os.path.isdir(os.path.join(r, s)) for r in folders for s in case.library)
            why = ("no suitable %s file (or key) in the library" % "/".join(case.sources)) if have else \
                "no library folder for %s" % case.system
            out["cases"][case.key] = {"why": why}
            say("%-22s no input (%s)" % (case.key, why))
        else:
            out["cases"][case.key] = e
            say("%-22s %s, %d MB%s" % (case.key, e["kind"], e["size"] >> 20,
                                       ", derived (%s)" % e["derive"] if e.get("derive") else ""))
    os.makedirs(os.path.dirname(a.inputs), exist_ok=True)
    with open(a.inputs, "w") as f:
        json.dump(out, f, indent=1, sort_keys=True)
    say("wrote %s" % os.path.relpath(a.inputs, REPO))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--box", action="append",
                    help="HOST:PORT of the test box (root over SSH); several, comma-separated or repeated, "
                         "share the cases")
    ap.add_argument("--base", default="43.1", help="label for the box's Batocera: 43.1 or 44")
    ap.add_argument("--inputs", default=INPUTS)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--cases")
    g.add_argument("--changed-since", metavar="REF")
    g.add_argument("--all", action="store_true")
    g.add_argument("--pick", action="store_true")
    ap.add_argument("--out", default=os.path.join(REPO, "build", "formats"))
    ap.add_argument("--keep", action="store_true", help="leave the case's files on the box")
    ap.add_argument("--ssh-key", default=os.environ.get("FMT_SSH_KEY"))
    ap.add_argument("--library", action="append", default=[], help="--pick: a roms folder (RomM platform folders)")
    ap.add_argument("--lab", action="append", help="--pick: a homebrew roms folder, preferred")
    ap.add_argument("--bios", help="--pick: the BIOS folder")
    a = ap.parse_args()
    if a.pick:
        if not a.library or not a.bios:
            sys.exit("--pick needs --library and --bios")
        sys.exit(cmd_pick(a))
    if not a.box:
        sys.exit("--box HOST:PORT")
    sys.exit(cmd_run(a))


if __name__ == "__main__":
    main()
