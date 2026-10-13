"""Converter tools RomM-o-Cera downloads: dolphin-tool, the Xbox 360 tools, wuatool."""
import os
import shutil
import struct
import subprocess
from dataclasses import dataclass
from typing import Callable

import requests

from . import core as _core, config as _config, net as _net


# ---------------------------------------------------------- dolphin-tool (GameCube / Wii -> RVZ)

# Built from the same Dolphin commit Batocera 43.1 ships (its dolphin-emu.mk
# turns the CLI tool off), by tools/dolphin-tool/make.sh; published as its own
# release on GitHub with Dolphin's license and the exact source it came from.
_DT_BASE = "https://github.com/fraygox/romm-o-cera/releases/download/dolphin-tool-2603a/"
DOLPHIN_TOOLS = {           # per CPU: the archive RomM-o-Cera downloads, and its pinned SHA-256
    "x86_64": {"url": _DT_BASE + "dolphin-tool-2603a-x86_64.tar.gz",
               "sha256": "19eab07e5ac5658fed51d56af5ed475831519fc28961ac95d1b43c4fb7d04ea7", "size": 7990471},
    "aarch64": {"url": _DT_BASE + "dolphin-tool-2603a-aarch64.tar.gz",
                "sha256": "655cb5520ea3742505ae30ec6100165a8f05c67ceacf6c52e68ba27b75162b06", "size": 7298926},
}


def _machine():
    """The CPU dolphin-tool must be built for. uname says what the kernel is;
    a 64-bit kernel can run 32-bit programs only (some ARM boards), and the
    tool must match the programs, so a 32-bit Python means "not supported"."""
    import platform
    import struct
    m = platform.machine()
    m = {"AMD64": "x86_64", "arm64": "aarch64"}.get(m, m)
    if struct.calcsize("P") != 8:                  # 32-bit userland
        return {"x86_64": "i686", "aarch64": "armv7l"}.get(m, m)
    return m


DOLPHIN_TOOL = dict({"version": "2603a", "commit": "5e7cc91d8c9a43ca189b288937f65c9763af9c22"},
                    **DOLPHIN_TOOLS.get(_machine(), {"url": "", "sha256": "", "size": 0}))
TOOLS_DIR = os.path.join(_core.DATA_DIR, "tools")
RVZ_ARGS = ["-f", "rvz", "-c", "zstd", "-l", "5", "-b", "131072"]   # Dolphin's recommended RVZ settings


# ---------------------------------------------------------- the tools, described once

@dataclass(frozen=True, eq=False)
class ToolSpec:
    """A program a converter needs that Batocera doesn't ship: downloaded when
    its setting is turned on, from its own GitHub release (one archive per CPU,
    pinned by SHA-256), into tools/<slug>-<version>. The converter that needs
    it names it (Converter.tool); Settings, Credits and the installer read it."""
    setting: str               # the converter's config key ("rvz", "zar")
    slug: str                  # its folder under tools/ is <slug>-<version> (the archive's top folder too, if any)
    label: str                 # in messages: "dolphin-tool", "Xbox 360 tools"
    subject: str               # starting a sentence: "dolphin-tool", "The Xbox 360 tools"
    pin: dict                  # version, the commits it's built from, this CPU's url / sha256 / size
    exes: tuple                # the programs in it
    probes: tuple              # per program: (arguments, a word its output holds when it runs here)
    env: str                   # ROMM_BROWSER_<env>_URL / _SHA256 replace the pin (tests, a local build)
    built_from: str            # the "installed" log line's detail
    help: dict                 # the Settings row's grey line: "cpu", "on", "missing", "off"
    supported: Callable        # () -> built for this box's CPU
    installed: Callable        # () -> what the converter runs (paths), or None when not installed

    @property
    def folder(self):
        return "%s-%s" % (self.slug, self.pin["version"])


def tool_dir(spec):
    return os.path.join(os.environ.get("ROMM_BROWSER_TOOLS", TOOLS_DIR), spec.folder)


def tool_paths(spec):
    """Every program of the tool, when all are installed; else None."""
    exe = [os.path.join(tool_dir(spec), n) for n in spec.exes]
    return tuple(exe) if all(os.path.isfile(p) and os.access(p, os.X_OK) for p in exe) else None


def tool_state(spec, cfg, setting=None):
    """{"label", "help"} for a Settings row whose switch needs the tool: spec's own
    setting, or another one sharing it (setting: Wii U to .wux and Decrypt Wii U
    discs share wuatool). On, its grey line is the Setting's own, if it has one."""
    if not spec.supported():
        return {"label": "Not on this CPU", "help": spec.help["cpu"]}
    key = setting or spec.setting
    on = _config.config_value(cfg, key)
    if on and spec.installed():
        return {"label": "On", "help": _config.SETTING[key].sub or spec.help["on"]}
    if on:
        return {"label": "Tools missing" if len(spec.exes) > 1 else "Tool missing", "help": spec.help["missing"]}
    return {"label": "Off", "help": spec.help["off"]}


def dolphin_tool_path():
    """The installed dolphin-tool, or None."""
    p = tool_paths(TOOL_DOLPHIN)
    return p[0] if p else None


def rvz_supported():
    return _machine() in DOLPHIN_TOOLS


TOOL_DOLPHIN = ToolSpec(
    "rvz", "dolphin-tool", "dolphin-tool", "dolphin-tool", DOLPHIN_TOOL, ("dolphin-tool",),
    ((("--help",), "convert"),), "DOLPHIN", DOLPHIN_TOOL["commit"][:10],
    {"cpu": "dolphin-tool is built for x86_64 and 64-bit ARM only",
     "on": "Lossless, usually much smaller (dolphin-tool %s)" % DOLPHIN_TOOL["version"],
     "missing": "{A} downloads dolphin-tool again", "off": "Turning it on downloads dolphin-tool once"},
    lambda: rvz_supported(), lambda: dolphin_tool_path())


def _fetch_tool_archive(label, url, want, dest, progress=None):
    """Download a tool archive, check its pinned SHA-256 and unpack it into
    dest + ".new" (returned). Refuses links, absolute paths and "..";"""
    import hashlib
    import tarfile
    if not want:
        raise _core.RommError("This version doesn't know %s's checksum yet." % label)
    tmpdir = _core.tmp_dir()
    os.makedirs(tmpdir, exist_ok=True)
    tgz = os.path.join(tmpdir, os.path.basename(dest) + ".tar.gz")
    h = hashlib.sha256()
    try:
        with _net.plain_get(url, stream=True, timeout=30) as r:
            if r.status_code != 200:
                _net.discard(r)
                raise _core.RommError("Couldn't download %s (HTTP %d)." % (label, r.status_code))
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            with open(tgz, "wb") as f:
                for chunk in r.iter_content(1 << 16):
                    f.write(chunk)
                    h.update(chunk)
                    got += len(chunk)
                    if progress:
                        progress(got, total)
    except requests.RequestException as e:
        raise _core.RommError("Couldn't download %s (%s)." % (label, e.__class__.__name__)) from None
    if h.hexdigest() != want:
        os.remove(tgz)
        raise _core.RommError("The %s download didn't match its checksum; not installed." % label)
    staging = dest + ".new"
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging)
    with tarfile.open(tgz) as t:
        for m in t.getmembers():
            if not (m.isfile() or m.isdir()) or m.name.startswith(("/", "..")) or "/../" in m.name:
                raise _core.RommError("Unexpected file in the %s archive: %s" % (label, m.name))
        try:
            t.extractall(staging, filter="data")       # Python 3.12+: tarfile's own safety net too
        except TypeError:
            t.extractall(staging)
    os.remove(tgz)
    return staging


def install_tool(spec, progress=None, url=None, sha256=None):
    """Download, check and unpack the tool into DATA_DIR/tools, and run each
    program once. Returns spec.installed(). The archive holds the programs,
    their licenses and their exact source (SOURCE.txt)."""
    are = len(spec.exes) > 1
    if not spec.supported():
        raise _core.RommError("%s %s built for this CPU (%s)." % (spec.subject, "aren't" if are else "isn't", _machine()))
    url = url or os.environ.get("ROMM_BROWSER_%s_URL" % spec.env) or spec.pin["url"]
    want = (sha256 or os.environ.get("ROMM_BROWSER_%s_SHA256" % spec.env) or spec.pin["sha256"]).lower()
    dest = tool_dir(spec)
    staging = _fetch_tool_archive(spec.label, url, want, dest, progress)
    top = os.path.join(staging, spec.folder)
    root = top if os.path.isdir(top) else staging
    exe = [os.path.join(root, n) for n in spec.exes]
    missing = [n for n, p in zip(spec.exes, exe) if not os.path.isfile(p)]
    if missing:
        shutil.rmtree(staging, ignore_errors=True)
        raise _core.RommError("The %s archive has no %s in it." % (spec.label, missing[0]))
    for p in exe:
        os.chmod(p, 0o755)
    try:
        outs = [subprocess.run([p] + list(args), capture_output=True, text=True, timeout=30)
                for p, (args, _) in zip(exe, spec.probes)]
    except OSError as e:
        shutil.rmtree(staging, ignore_errors=True)
        raise _core.RommError("%s %s run on this box (%s)." % (spec.subject, "don't" if are else "doesn't",
                                                               e.strerror)) from None
    if not all(word in (o.stdout + o.stderr).lower() for o, (_, word) in zip(outs, spec.probes)):
        shutil.rmtree(staging, ignore_errors=True)
        raise _core.RommError("%s %s run on this box." % (spec.subject, "don't" if are else "doesn't"))
    shutil.rmtree(dest, ignore_errors=True)
    os.replace(root, dest)
    shutil.rmtree(staging, ignore_errors=True)
    _core.log("%s %s installed (%s)" % (spec.slug, spec.pin["version"], spec.built_from))
    return spec.installed()


# ---------------------------------------------------------- Xbox 360 -> .zar (ZArchive)

# xenia-canary (Batocera 43.1, x86_64 only) opens a ".zar": a ZArchive of the
# disc's files. Made with two small tools RomM-o-Cera downloads when the setting
# is turned on (like dolphin-tool): extract-xiso (game partition -> folder) and
# zarchive (folder -> .zar), built by tools/xbox360-tools/ and published as their
# own GitHub release with their licenses and exact source. Neither tool's exit
# code is enough on its own: extract-xiso exits 0 on a cut-short image and
# zarchive never checks its archive's SHA-256, so the worker checks both itself.
_X360_BASE = "https://github.com/fraygox/romm-o-cera/releases/download/xbox360-tools-2610a/"
X360_TOOLS_ALL = {          # per CPU (Batocera has no Xbox 360 off x86_64)
    "x86_64": {"url": _X360_BASE + "xbox360-tools-2610a-x86_64.tar.gz",
               "sha256": "b5b63fd0fd7b13a69b2c75c51059810df4bf89cda70cd711f0a4a267967bdd8f", "size": 757610},
}
X360_TOOLS = dict({"version": "2610a", "extract_xiso": "3f5b62cfe68f000b0e3c8a30104973f3a297948e",
                   "zarchive": "b467f7aa342895e66a1a003377ed63b3c1be3de3"},
                  **X360_TOOLS_ALL.get(_machine(), {"url": "", "sha256": "", "size": 0}))
ZAR_SYSTEMS = {"xbox360"}
ZAR_MAGIC, ZAR_VERSION = 0x169f52d6, 0x61bf3a01


def x360_tools():
    """(extract-xiso, zarchive) paths when both are installed, else None."""
    return tool_paths(TOOL_X360)


def zar_supported():
    return _machine() in X360_TOOLS_ALL


TOOL_X360 = ToolSpec(
    "zar", "xbox360-tools", "Xbox 360 tools", "The Xbox 360 tools", X360_TOOLS, ("extract-xiso", "zarchive"),
    ((("-h",), "extract-xiso"), ((), "zarchive")), "X360",
    "extract-xiso %s, zarchive %s" % (X360_TOOLS["extract_xiso"][:7], X360_TOOLS["zarchive"][:7]),
    {"cpu": "Xbox 360 emulation on Batocera is x86_64 only", "on": "Smaller files xenia-canary opens directly",
     "missing": "{A} downloads them again", "off": "Turning it on downloads two small tools once"},
    lambda: zar_supported(), lambda: x360_tools())


# ---------------------------------------------------------- Wii U -> .wux, .wua (ZArchive)

# Cemu (Batocera 43.1: x86_64; master: aarch64 too) opens a ".wux" (a .wud with
# each distinct 32 KB sector stored once; wuatool wux writes it, no keys) and a
# ".wua": a ZArchive of the game partition of a Wii U disc. wuatool reads the disc
# with Cemu's own disc code and writes the archive with the ZArchive commit Cemu
# reads it with, then reads both back and compares every file. Built by tools/wiiu-tools/ (x86_64, and
# aarch64 for plain ARMv8) and published as its own GitHub release with its
# licenses and exact source. Disc keys are the user's (romm/wiiu.py).
_WIIU_BASE = "https://github.com/fraygox/romm-o-cera/releases/download/wiiu-tools-2610b/"
WIIU_TOOLS_ALL = {          # per CPU: the archive RomM-o-Cera downloads, and its pinned SHA-256
    "x86_64": {"url": _WIIU_BASE + "wiiu-tools-2610b-x86_64.tar.gz",
               "sha256": "8ab94774249cf8845430d1ac96d60b57ee9a7bf6a903a0f5d688cf29b7e12c3f", "size": 2702842},
    "aarch64": {"url": _WIIU_BASE + "wiiu-tools-2610b-aarch64.tar.gz",
                "sha256": "9e473a291414f1d4daef76d576ef1aaf65d1c6e941ce427e60dc9b9d20611ba9", "size": 2522697},
}
WIIU_TOOLS = dict({"version": "2610b", "cemu": "b9b46ecb65e2eb1c15793380048309df73d2084e",
                   "zarchive": "d2c717730092c7bf8cbb033b12fd4001b7c4d932"},
                  **WIIU_TOOLS_ALL.get(_machine(), {"url": "", "sha256": "", "size": 0}))
WUA_SYSTEMS = {"wiiu"}


def wuatool_path():
    """The installed wuatool, or None."""
    p = tool_paths(TOOL_WIIU)
    return p[0] if p else None


def wua_supported():
    return _machine() in WIIU_TOOLS_ALL


TOOL_WIIU = ToolSpec(
    "wua", "wiiu-tools", "wuatool", "wuatool", WIIU_TOOLS, ("wuatool",), ((("--version",), "wuatool"),), "WIIU",
    "Cemu %s, zarchive %s" % (WIIU_TOOLS["cemu"][:7], WIIU_TOOLS["zarchive"][:7]),
    {"cpu": "wuatool is built for x86_64 and 64-bit ARM only", "on": "Smaller files Cemu opens directly",
     "missing": "{A} downloads wuatool (once, about 2 MB)", "off": "Turning it on downloads wuatool once"},
    lambda: wua_supported(), lambda: wuatool_path())
