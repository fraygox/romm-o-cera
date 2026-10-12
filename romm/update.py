"""Release checks and the in-app updater."""
import io
import json
import os
import re
import shutil
import time
import zipfile

import requests

import rommlib
from . import core as _core, config as _config


UPDATE_REPO = os.environ.get("ROMM_BROWSER_UPDATE_REPO", "fraygox/romm-o-cera")
UPDATE_CACHE = os.path.join(_core.DATA_DIR, "update-check.json")


# ----------------------------------------------------------- updates

def _vtuple(v):
    """(major, minor, patch) for a RomM-o-Cera version/tag ("v0.6.5", "0.6.5+abc");
    anything else (e.g. the "dolphin-tool-2603a" release) is (0,): never an update."""
    m = re.match(r"^v?(\d+)\.(\d+)(?:\.(\d+))?(?:[+-].*)?$", (v or "").strip())
    return tuple(int(x or 0) for x in m.groups()) if m else (0,)


def newer_release(cfg, force=False):
    """Latest release tag on GitHub if it is newer than VERSION, else None.
    Checked at most once a day (cached); never raises."""
    if not _config.config_value(cfg, "check_updates"):
        return None
    now = time.time()
    cache = {}
    try:
        with open(UPDATE_CACHE) as f:
            cache = json.load(f)
    except (OSError, ValueError):
        pass
    checked = cache.get("checked", 0)
    if force or now - checked > 86400 or checked > now + 3600:
        try:
            r = requests.get("%s/repos/%s/releases/latest" % (GITHUB_API, UPDATE_REPO),
                             timeout=6, headers={"User-Agent": _core.UA,
                                                 "Accept": "application/vnd.github+json"})
            if r.status_code == 200:
                cache = {"checked": now, "latest": r.json().get("tag_name", "")}
            else:
                cache = {"checked": now, "latest": cache.get("latest", "")}
        except Exception:
            cache = {"checked": now, "latest": cache.get("latest", "")}    # offline: try again tomorrow
        try:
            os.makedirs(_core.DATA_DIR, exist_ok=True)
            _core.atomic_write_json(UPDATE_CACHE, cache)
        except OSError:
            pass
    latest = cache.get("latest") or ""
    return latest if _vtuple(latest) > _vtuple(rommlib.VERSION) else None


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
        r = requests.get(url, headers=headers, **kw)
    except requests.RequestException as e:
        raise UpdateError("Can't reach GitHub (%s)" % e.__class__.__name__) from None
    if r.status_code != 200:
        raise UpdateError("GitHub answered HTTP %d" % r.status_code)
    return r


def release_info(tag=None):
    """{"tag", "zip", "sums"} for the latest (or a given) release."""
    path = "releases/tags/%s" % tag if tag else "releases/latest"
    data = _gh_get("%s/repos/%s/%s" % (GITHUB_API, UPDATE_REPO, path)).json()
    assets = {a.get("name"): a.get("browser_download_url") for a in data.get("assets") or []}
    if not assets.get("romm-o-cera.zip") or not assets.get("SHA256SUMS"):
        raise UpdateError("Release %s has no romm-o-cera.zip / SHA256SUMS" % data.get("tag_name"))
    return {"tag": data.get("tag_name") or "", "zip": assets["romm-o-cera.zip"],
            "sums": assets["SHA256SUMS"]}


def _version_in(app_dir):
    try:
        with open(os.path.join(app_dir, "rommlib.py")) as f:
            m = re.search(r'^VERSION = "([^"]+)"', f.read(), re.M)
        return m.group(1) if m else None
    except OSError:
        return None


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
    for chunk in r.iter_content(1 << 16):
        buf.write(chunk)
        if buf.tell() > MAX_RELEASE_BYTES:
            raise UpdateError("Release zip is unexpectedly large")
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
    _core.log("UPDATE installed %s (previous %s kept for rollback)" % (_version_in(app_dir), _version_in(backup)))
    return _version_in(app_dir)


def backup_version(backup=None):
    backup = backup or APP_BACKUP
    return _version_in(backup) if os.path.isdir(backup) else None


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
    _core.log("ROLLBACK now %s (backup %s)" % (_version_in(app_dir), _version_in(backup)))
    return _version_in(app_dir)
