"""Release checks and the in-app updater."""
import io
import json
import os
import re
import shutil
import signal
import time
import zipfile

import requests

import rommlib
from . import core as _core, config as _config, es as _es, net as _net


UPDATE_REPO = os.environ.get("ROMM_BROWSER_UPDATE_REPO", "fraygox/romm-o-cera")
UPDATE_CACHE = os.path.join(_core.DATA_DIR, "update-check.json")


# ----------------------------------------------------------- updates

def _vtuple(v):
    """(major, minor, patch) for a RomM-o-Cera version/tag ("v0.6.5", "0.6.5+abc");
    anything else (e.g. the "dolphin-tool-2603a" release) is (0,): never an update."""
    m = re.match(r"^v?(\d+)\.(\d+)(?:\.(\d+))?(?:[+-].*)?$", (v or "").strip())
    return tuple(int(x or 0) for x in m.groups()) if m else (0,)


NIGHTLY_TAG = "nightly"      # the rolling prerelease the nightly channel follows
_NIGHTLY = re.compile(r"\bv?(\d+\.\d+\.\d+\+n\d{8}\.\d{4}\.[0-9a-f]+)\b")


def version_key(label):
    """How versions order, release channels included: 0.8.1 < a nightly built
    after it (0.8.1+n20261012.1903.ab12cd3, ordered by its UTC stamp) < 0.8.2.
    An internal build (0.8.1+ab12cd3, deploy.sh) sorts after 0.8.1 and before
    any nightly of it. Anything else is (0,): never an update."""
    m = re.match(r"^v?(\d+)\.(\d+)(?:\.(\d+))?(?:\+(\S+))?$", (label or "").strip())
    if not m:
        return (0,)
    base = tuple(int(x or 0) for x in m.groups()[:3])
    build = m.group(4) or ""
    if not build:
        return base + (0, "")
    if re.match(r"^n\d{8}\.\d{4}\.", build):
        return base + (2, build)
    return base + (1, "")


def _gh_json(path):
    r = _net.plain_get("%s/repos/%s/%s" % (GITHUB_API, UPDATE_REPO, path), timeout=6,
                       headers={"User-Agent": _core.UA, "Accept": "application/vnd.github+json"})
    if r.status_code != 200:
        _net.discard(r)
        return None
    return r.json()


def _latest_on(channel):
    """The newest version labels the channel offers: the latest release's tag,
    plus on nightly the nightly build's label (from its release title)."""
    out = []
    rel = _gh_json("releases/latest")
    if rel and rel.get("tag_name"):
        out.append(rel["tag_name"])
    if channel == "nightly":
        nightly = _gh_json("releases/tags/" + NIGHTLY_TAG)
        m = _NIGHTLY.search((nightly or {}).get("name") or "")
        if m:
            out.append("v" + m.group(1))
    return out


def newer_release(cfg, force=False):
    """The newest version on the configured channel (update_channel) if it is
    newer than this one, else None: a release tag ("v0.8.2"), or a nightly's
    label ("v0.8.1+n20261012.1903.ab12cd3"). The nightly channel offers a
    release too when it's newer. Checked at most once a day, and again when
    the channel changes (cached); never raises."""
    if not _config.config_value(cfg, "check_updates"):
        return None
    channel = _config.config_value(cfg, "update_channel")
    now = time.time()
    cache = {}
    try:
        with open(UPDATE_CACHE) as f:
            cache = json.load(f)
    except (OSError, ValueError):
        pass
    if not isinstance(cache, dict):
        cache = {}
    checked = cache.get("checked", 0)
    if force or now - checked > 86400 or checked > now + 3600 or cache.get("channel", "stable") != channel:
        try:
            found = _latest_on(channel)
            latest = max(found, key=version_key) if found else cache.get("latest", "")
        except Exception:
            latest = cache.get("latest", "") if cache.get("channel", "stable") == channel else ""
        cache = {"checked": now, "latest": latest, "channel": channel}      # offline: try again tomorrow
        try:
            os.makedirs(_core.DATA_DIR, exist_ok=True)
            _core.atomic_write_json(UPDATE_CACHE, cache)
        except OSError:
            pass
    latest = cache.get("latest") or ""
    return latest if version_key(latest) > version_key(rommlib.VERSION_LABEL) else None


# ----------------------------------------------------------- self-update
# Manual, from GitHub releases only: the release zip must match the release's
# SHA256SUMS, contain only rommbrowser/…, compile, and carry the version its tag
# names. It is staged next to the app and swapped in with two renames; the
# previous version is kept for one-step rollback. The RomM token is never sent
# (plain requests, not the RomM session). SHA256SUMS comes from the same
# release, so it guards against corruption, not against a compromised repo.
GITHUB_API = os.environ.get("ROMM_BROWSER_GITHUB_API", "https://api.github.com")
APP_STAGING = os.path.join(_core.DATA_DIR, "app-staging")
APP_BACKUP = os.path.join(_core.DATA_DIR, "app-backup")     # outside roms/: ES must not list it
MAX_RELEASE_BYTES = 40 << 20


class UpdateError(Exception):
    pass


def _gh_get(url, **kw):
    kw.setdefault("timeout", 20)
    headers = {"User-Agent": _core.UA, "Accept": "application/vnd.github+json"}
    try:
        r = _net.plain_get(url, headers=headers, **kw)
    except requests.RequestException as e:
        raise UpdateError("Can't reach GitHub (%s)" % e.__class__.__name__) from None
    if r.status_code != 200:
        _net.discard(r)
        raise UpdateError("GitHub answered HTTP %d" % r.status_code)
    return r


def release_info(tag=None):
    """{"tag", "zip", "sums", "label"} for the latest (or a given) release.
    A nightly label ("v0.8.1+n…") or the nightly tag means the nightly
    build; its "label" is what its zip must hold (stage_release checks it),
    since the nightly is replaced in place by the next one."""
    if tag and (tag == NIGHTLY_TAG or _NIGHTLY.search(tag)):
        tag = NIGHTLY_TAG
    path = "releases/tags/%s" % tag if tag else "releases/latest"
    data = _gh_get("%s/repos/%s/%s" % (GITHUB_API, UPDATE_REPO, path)).json()
    assets = {a.get("name"): a.get("browser_download_url") for a in data.get("assets") or []}
    if not assets.get("romm-o-cera.zip") or not assets.get("SHA256SUMS"):
        raise UpdateError("Release %s has no romm-o-cera.zip / SHA256SUMS" % data.get("tag_name"))
    info = {"tag": data.get("tag_name") or "", "zip": assets["romm-o-cera.zip"], "sums": assets["SHA256SUMS"]}
    if info["tag"] == NIGHTLY_TAG:
        m = _NIGHTLY.search(data.get("name") or "")
        if not m:
            raise UpdateError("The nightly build has no version in its title")
        info["label"] = m.group(1)
    return info


def _version_in(app_dir, label=False):
    """The app's VERSION, or with label=True its VERSION_LABEL (VERSION+BUILD)."""
    try:
        with open(os.path.join(app_dir, "rommlib.py")) as f:
            src = f.read()
    except OSError:
        return None
    m = re.search(r'^VERSION = "([^"]+)"', src, re.M)
    if not m or not label:
        return m.group(1) if m else None
    b = re.search(r'^BUILD = "([^"]*)"', src, re.M)
    return m.group(1) + ("+" + b.group(1) if b and b.group(1) else "")


def stage_release(info, staging=None):
    """Download, verify and unpack a release into staging; returns the staged
    app folder. Raises UpdateError and leaves nothing behind on any problem."""
    import hashlib
    import io
    staging = staging or APP_STAGING
    sums = _gh_get(info["sums"]).text
    want = None
    for ln in sums.splitlines():
        parts = ln.split()
        if len(parts) == 2 and parts[1].lstrip("*") == "romm-o-cera.zip":
            want = parts[0].lower()
    if not want:
        raise UpdateError("SHA256SUMS doesn't list romm-o-cera.zip")
    r = _gh_get(info["zip"], stream=True)
    buf = io.BytesIO()
    try:
        for chunk in r.iter_content(1 << 16):
            buf.write(chunk)
            if buf.tell() > MAX_RELEASE_BYTES:
                raise UpdateError("Release zip is unexpectedly large")
    except requests.RequestException as e:
        raise UpdateError("The download from GitHub broke off (%s)" % e.__class__.__name__) from None
    finally:
        r.close()
    data = buf.getvalue()
    if hashlib.sha256(data).hexdigest() != want:
        raise UpdateError("Checksum mismatch — download corrupted, nothing changed")
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for n in z.namelist():
                parts = n.replace("\\", "/").split("/")
                if n.startswith("/") or ".." in parts or parts[0] != "rommbrowser":
                    raise UpdateError("Unexpected path in release: %s" % n)
            z.extractall(staging)
        app = os.path.join(staging, "rommbrowser")
        for need in ("rommbrowser.pygame", "rommlib.py", "romm/__init__.py", "romm/core.py"):
            if not os.path.isfile(os.path.join(app, need)):
                raise UpdateError("Release is missing %s" % need)
        for root, _dirs, files in os.walk(app):         # romm/ too: most of the engine lives there
            for fn in files:
                if fn.endswith((".py", ".pygame")):
                    path = os.path.join(root, fn)
                    with open(path, encoding="utf-8") as f:
                        compile(f.read(), os.path.relpath(path, app), "exec")
        if info.get("label"):
            got = _version_in(app, label=True)
            if got != info["label"]:
                raise UpdateError("The nightly build changed while it downloaded (%s, not %s); try again"
                                  % (got, info["label"]))
            return app
        ver = _version_in(app)
        if not ver or _vtuple(ver) != _vtuple(info["tag"]):
            raise UpdateError("Release %s contains version %s" % (info["tag"], ver))
        return app
    except UpdateError:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    except (zipfile.BadZipFile, SyntaxError, OSError, UnicodeDecodeError) as e:
        shutil.rmtree(staging, ignore_errors=True)
        raise UpdateError("Release failed checks (%s)" % e.__class__.__name__) from None


def _move(src, dst):
    try:
        os.rename(src, dst)                 # atomic on the same filesystem
    except OSError:
        shutil.move(src, dst)


def _exchange(a, b):
    """Swap two existing paths in one atomic step (Linux renameat2
    RENAME_EXCHANGE). OSError where the filesystem can't (exFAT, or a and b
    on different filesystems)."""
    import ctypes
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    if libc.renameat2(-100, os.fsencode(a), -100, os.fsencode(b), 2) != 0:     # AT_FDCWD, RENAME_EXCHANGE
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), a)


def install_staged(staged_app, app_dir=rommlib.APP_DIR, backup=None):
    """Swap the staged app in; the current one becomes the backup. One atomic
    exchange where the filesystem allows it, so a power cut can never leave no
    app at all (the old two-step swap had that moment); else the two steps."""
    backup = backup or APP_BACKUP
    shutil.rmtree(backup, ignore_errors=True)
    try:
        _exchange(staged_app, app_dir)              # app_dir: new version; staged_app: the old one
        exchanged = True
    except (OSError, AttributeError):
        exchanged = False
    if exchanged:
        _move(staged_app, backup)
    else:
        _move(app_dir, backup)
        try:
            _move(staged_app, app_dir)
        except Exception:
            _move(backup, app_dir)              # put the old version back
            raise
    shutil.rmtree(os.path.dirname(staged_app), ignore_errors=True)
    _core.log("UPDATE installed %s (previous %s kept for rollback)" % (_version_in(app_dir, True),
                                                                     _version_in(backup, True)))
    return _version_in(app_dir, True)


# The background helpers this app starts (spawn_detached: each its own session).
# An update stops them first, so no old copy keeps running beside the new one.
HELPERS = ("rb_dl.py", "rb_chd.py", "rb_finalize.py")


def helper_pids(app_dir=rommlib.APP_DIR, proc="/proc"):
    """{pid: script} for this app's helpers running now, any version: matched
    by the script path on their command line (never by a loose pattern)."""
    want = {os.path.join(app_dir, s): s for s in HELPERS}
    out = {}
    try:
        names = os.listdir(proc)
    except OSError:
        return out
    for d in names:
        if not d.isdigit() or int(d) == os.getpid():
            continue
        try:
            with open(os.path.join(proc, d, "cmdline"), "rb") as f:
                argv = f.read().split(b"\0")
        except OSError:
            continue
        for a in argv[1:3]:                      # python3 [-u] /…/rb_x.py
            if os.fsdecode(a) in want:
                out[int(d)] = want[os.fsdecode(a)]
                break
    return out


def _alive(pid, proc="/proc"):
    """Running (a zombie, gone but not reaped by its parent, counts as gone)."""
    try:
        with open(os.path.join(proc, str(pid), "stat")) as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return False


def stop_helpers(timeout=20, app_dir=rommlib.APP_DIR, proc="/proc"):
    """Stop this app's background helpers before an update swaps their files.
    rb_dl is asked to hand over (as when the app starts: it saves the queue,
    the partial file stays for a resume); rb_chd and rb_finalize get SIGTERM
    (rb_chd stops its tool and keeps the originals; the task stays queued).
    Whatever is still running after `timeout` is killed with its session (a
    paused chdman too). Returns the scripts that were running."""
    pids = helper_pids(app_dir, proc)
    if "rb_dl.py" in pids.values():
        try:
            open(_core.DL_STOP, "w").close()
        except OSError:
            pass
    for pid, script in pids.items():
        if script != "rb_dl.py":
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
    end = time.monotonic() + timeout
    while time.monotonic() < end and any(_alive(p, proc) for p in pids):
        time.sleep(0.2)
    for pid in pids:
        if not _alive(pid, proc):
            continue
        try:
            if os.getpgid(pid) == pid:           # its own session: its tools go too
                os.killpg(pid, signal.SIGCONT)
                os.killpg(pid, signal.SIGKILL)
            else:
                os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    if pids:
        _core.log("UPDATE stopped %s" % ", ".join(sorted(set(pids.values()))))
    return sorted(set(pids.values()))


# Reopen after an update: a detached waiter (stdlib only, its own session)
# asks ES to launch the app again once ES no longer reports it running. It
# gives up when anything else starts meanwhile, or after a minute.
_RELAUNCH = r"""
import json, sys, time, urllib.request
es, path = sys.argv[1], sys.argv[2]
def running():
    try:
        with urllib.request.urlopen(es + "/runningGame", timeout=3) as r:
            return (json.loads(r.read() or b"{}").get("path") or "")
    except ValueError:
        return ""
    except Exception:
        return None
end = time.time() + 60
while time.time() < end and running() != "":
    time.sleep(0.3)
time.sleep(1.5)                       # ES back in its menus
if time.time() < end and running() == "":
    urllib.request.urlopen(urllib.request.Request(es + "/launch", data=path.encode(), method="POST"), timeout=5)
"""


def relaunch_when_closed(app_dir=rommlib.APP_DIR, python=None):
    """Start the waiter that reopens the app through ES once it has quit."""
    import subprocess
    import sys
    try:
        subprocess.Popen([python or sys.executable, "-c", _RELAUNCH, _es.ES_API,
                          os.path.join(app_dir, "rommbrowser.pygame")], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def backup_version(backup=None):
    backup = backup or APP_BACKUP
    return _version_in(backup, True) if os.path.isdir(backup) else None


def rollback(app_dir=rommlib.APP_DIR, backup=None):
    """Swap app and backup (so a rollback can itself be undone)."""
    backup = backup or APP_BACKUP
    if not os.path.isdir(backup):
        raise UpdateError("No previous version to go back to")
    try:
        _exchange(app_dir, backup)              # one atomic step: never a moment without an app
    except (OSError, AttributeError):
        tmp = backup + ".swap"
        shutil.rmtree(tmp, ignore_errors=True)
        _move(app_dir, tmp)
        try:
            _move(backup, app_dir)
        except Exception:
            _move(tmp, app_dir)                  # put the current version back
            raise UpdateError("Couldn't swap versions; nothing changed")
        _move(tmp, backup)
    _core.log("ROLLBACK now %s (backup %s)" % (_version_in(app_dir, True), _version_in(backup, True)))
    return _version_in(app_dir, True)
