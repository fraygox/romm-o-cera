"""deploy.sh's on-box step: check the staged app, then swap it in.

    python3 - <data_dir> <app_dir> < tools/deploy_swap.py      (what deploy.sh runs over ssh)

deploy.sh has uploaded the new app to <data_dir>/app-staging/rommbrowser. It
must compile (every .py and the .pygame) on the box's own Python; a failed
check removes the staging copy and leaves the live app and its backup as they
were. Then it is swapped in the way the in-app updater does it
(romm/update.py install_staged): one atomic renameat2 RENAME_EXCHANGE where the
filesystem allows it, else two renames with the old app put back on failure.
The previous app becomes <data_dir>/app-backup, so the app's "Go back" row
works after a deploy. Nothing else in <data_dir> is touched.

Standalone (stdlib only), not an import of the box's installed updater: the
installed copy may be an older version, or missing on a first install.
"""
import os
import shutil
import sys

NEED = ("rommbrowser.pygame", "rommlib.py", "romm/__init__.py", "romm/core.py")


class DeployError(Exception):
    pass


def check(app):
    """Raise DeployError unless the staged app is complete and compiles."""
    for need in NEED:
        if not os.path.isfile(os.path.join(app, need)):
            raise DeployError("staged app is missing %s" % need)
    for root, _dirs, files in os.walk(app):
        for fn in sorted(files):
            if fn.endswith((".py", ".pygame")):
                path = os.path.join(root, fn)
                try:
                    with open(path, encoding="utf-8") as f:
                        compile(f.read(), os.path.relpath(path, app), "exec")   # no __pycache__ left behind
                except (SyntaxError, ValueError, UnicodeDecodeError) as e:
                    raise DeployError("%s doesn't compile: %s" % (os.path.relpath(path, app), e)) from None


def _move(src, dst):
    try:
        os.rename(src, dst)                 # atomic on the same filesystem
    except OSError:
        shutil.move(src, dst)


def _exchange(a, b):
    """Swap two existing paths in one atomic step (Linux renameat2
    RENAME_EXCHANGE). OSError where the filesystem can't."""
    import ctypes
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    if libc.renameat2(-100, os.fsencode(a), -100, os.fsencode(b), 2) != 0:     # AT_FDCWD, RENAME_EXCHANGE
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), a)


def install(staged, app_dir, backup):
    """romm/update.py install_staged, plus a first install (no app yet: the
    staged copy just moves into place, and an existing backup stays)."""
    if not os.path.isdir(app_dir):
        os.makedirs(os.path.dirname(app_dir), exist_ok=True)
        _move(staged, app_dir)
        return False
    shutil.rmtree(backup, ignore_errors=True)
    try:
        _exchange(staged, app_dir)          # app_dir: new version; staged: the old one
        exchanged = True
    except (OSError, AttributeError):
        exchanged = False
    if exchanged:
        _move(staged, backup)
    else:
        _move(app_dir, backup)
        try:
            _move(staged, app_dir)
        except Exception:
            _move(backup, app_dir)          # put the old version back
            raise
    return True


def main(data_dir, app_dir):
    staging = os.path.join(data_dir, "app-staging")
    staged = os.path.join(staging, "rommbrowser")
    backup = os.path.join(data_dir, "app-backup")
    try:
        check(staged)
    except (DeployError, OSError) as e:
        shutil.rmtree(staging, ignore_errors=True)
        print("deploy: %s; the app on the box is unchanged" % e, file=sys.stderr)
        return 1
    try:
        had_app = install(staged, app_dir, backup)
    except OSError as e:
        print("deploy: the swap failed (%s)" % e, file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    print("deploy: swapped in%s" % (" (previous app kept in %s)" % backup if had_app else ""))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: deploy_swap.py <data_dir> <app_dir>")
    sys.exit(main(sys.argv[1], sys.argv[2]))
