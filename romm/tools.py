"""Converter tools RomM-o-Cera downloads: dolphin-tool, the Xbox 360 tools."""
import os
import shutil
import struct
import subprocess

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


def dolphin_tool_dir():
    return os.path.join(os.environ.get("ROMM_BROWSER_TOOLS", TOOLS_DIR), "dolphin-tool-" + DOLPHIN_TOOL["version"])


def dolphin_tool_path():
    """The installed dolphin-tool, or None."""
    p = os.path.join(dolphin_tool_dir(), "dolphin-tool")
    return p if os.path.isfile(p) and os.access(p, os.X_OK) else None


def rvz_supported():
    return _machine() in DOLPHIN_TOOLS


def rvz_state(cfg):
    """{"label", "help"} for the Settings row."""
    if not rvz_supported():
        return {"label": "Not on this CPU", "help": "dolphin-tool is built for x86_64 and 64-bit ARM only"}
    if _config.config_value(cfg, "rvz") and dolphin_tool_path():
        return {"label": "On", "help": "Lossless, usually much smaller (dolphin-tool %s)" % DOLPHIN_TOOL["version"]}
    if _config.config_value(cfg, "rvz"):
        return {"label": "Tool missing", "help": "{A} downloads dolphin-tool again"}
    return {"label": "Off", "help": "Turning it on downloads dolphin-tool once"}


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


def install_dolphin_tool(progress=None, url=None, sha256=None):
    """Download, check and unpack dolphin-tool into DATA_DIR/tools. Returns
    its path. The archive holds the binary, Dolphin's license and SOURCE.txt."""
    if not rvz_supported():
        raise _core.RommError("dolphin-tool isn't built for this CPU (%s)." % _machine())
    url = url or os.environ.get("ROMM_BROWSER_DOLPHIN_URL") or DOLPHIN_TOOL["url"]
    want = (sha256 or os.environ.get("ROMM_BROWSER_DOLPHIN_SHA256") or DOLPHIN_TOOL["sha256"]).lower()
    if not want:
        raise _core.RommError("This version doesn't know dolphin-tool's checksum yet.")
    dest = dolphin_tool_dir()
    staging = _fetch_tool_archive("dolphin-tool", url, want, dest, progress)
    exe = os.path.join(staging, "dolphin-tool")
    if not os.path.isfile(exe):
        raise _core.RommError("The dolphin-tool archive has no dolphin-tool in it.")
    os.chmod(exe, 0o755)
    try:
        out = subprocess.run([exe, "--help"], capture_output=True, text=True, timeout=30)
    except OSError as e:
        shutil.rmtree(staging, ignore_errors=True)
        raise _core.RommError("dolphin-tool doesn't run on this box (%s)." % e.strerror) from None
    if "convert" not in (out.stdout + out.stderr):
        shutil.rmtree(staging, ignore_errors=True)
        raise _core.RommError("dolphin-tool doesn't run on this box.")
    shutil.rmtree(dest, ignore_errors=True)
    os.replace(staging, dest)
    _core.log("dolphin-tool %s installed (%s)" % (DOLPHIN_TOOL["version"], DOLPHIN_TOOL["commit"][:10]))
    return os.path.join(dest, "dolphin-tool")


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


def x360_tools_dir():
    return os.path.join(os.environ.get("ROMM_BROWSER_TOOLS", TOOLS_DIR), "xbox360-tools-" + X360_TOOLS["version"])


def x360_tools():
    """(extract-xiso, zarchive) paths when both are installed, else None."""
    d = x360_tools_dir()
    exe = [os.path.join(d, n) for n in ("extract-xiso", "zarchive")]
    return tuple(exe) if all(os.path.isfile(p) and os.access(p, os.X_OK) for p in exe) else None


def zar_supported():
    return _machine() in X360_TOOLS_ALL


def zar_state(cfg):
    """{"label", "help"} for the Settings row."""
    if not zar_supported():
        return {"label": "Not on this CPU", "help": "Xbox 360 emulation on Batocera is x86_64 only"}
    if _config.config_value(cfg, "zar") and x360_tools():
        return {"label": "On", "help": "Smaller files xenia-canary opens directly"}
    if _config.config_value(cfg, "zar"):
        return {"label": "Tools missing", "help": "{A} downloads them again"}
    return {"label": "Off", "help": "Turning it on downloads two small tools once"}


def install_x360_tools(progress=None, url=None, sha256=None):
    """Download, check and unpack extract-xiso + zarchive into DATA_DIR/tools.
    Returns the (extract-xiso, zarchive) paths."""
    if not zar_supported():
        raise _core.RommError("The Xbox 360 tools aren't built for this CPU (%s)." % _machine())
    url = url or os.environ.get("ROMM_BROWSER_X360_URL") or X360_TOOLS["url"]
    want = (sha256 or os.environ.get("ROMM_BROWSER_X360_SHA256") or X360_TOOLS["sha256"]).lower()
    dest = x360_tools_dir()
    staging = _fetch_tool_archive("Xbox 360 tools", url, want, dest, progress)
    top = os.path.join(staging, "xbox360-tools-" + X360_TOOLS["version"])
    root = top if os.path.isdir(top) else staging
    exe = [os.path.join(root, n) for n in ("extract-xiso", "zarchive")]
    if not all(os.path.isfile(p) for p in exe):
        shutil.rmtree(staging, ignore_errors=True)
        raise _core.RommError("The Xbox 360 tools archive is missing a tool.")
    for p in exe:
        os.chmod(p, 0o755)
    try:
        a = subprocess.run([exe[0], "-h"], capture_output=True, text=True, timeout=30)
        b = subprocess.run([exe[1]], capture_output=True, text=True, timeout=30)
    except OSError as e:
        shutil.rmtree(staging, ignore_errors=True)
        raise _core.RommError("The Xbox 360 tools don't run on this box (%s)." % e.strerror) from None
    if "extract-xiso" not in (a.stdout + a.stderr) or "zarchive" not in (b.stdout + b.stderr).lower():
        shutil.rmtree(staging, ignore_errors=True)
        raise _core.RommError("The Xbox 360 tools don't run on this box.")
    shutil.rmtree(dest, ignore_errors=True)
    os.replace(root, dest)
    shutil.rmtree(staging, ignore_errors=True)
    _core.log("xbox360-tools %s installed (extract-xiso %s, zarchive %s)" % (
        X360_TOOLS["version"], X360_TOOLS["extract_xiso"][:7], X360_TOOLS["zarchive"][:7]))
    return x360_tools()
