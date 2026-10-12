"""deploy.sh swaps the whole app folder in one step, as the in-app updater
does: its on-box part (tools/deploy_swap.py) runs here against temp dirs, and
deploy.sh itself runs end to end with ssh/scp replaced by local stand-ins."""
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile

import pytest

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SWAP = os.path.join(APP, "tools", "deploy_swap.py")


def _swap_module():
    spec = importlib.util.spec_from_file_location("deploy_swap", SWAP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _stage_files(dest):
    subprocess.run(["bash", "-c", '. "$1/tools/app-files.sh" && copy_app_files "$1" "$2"', "_", APP, str(dest)],
                   check=True)


def _tree(root):
    """{relative path: bytes} of every file under root."""
    out = {}
    for d, _dirs, files in os.walk(root):
        for fn in files:
            p = os.path.join(d, fn)
            with open(p, "rb") as f:
                out[os.path.relpath(p, root)] = f.read()
    return out


@pytest.fixture
def box(tmp_path):
    """An installed old app plus a data dir holding the user's state."""
    app = tmp_path / "roms" / "pygame" / "rommbrowser"
    app.mkdir(parents=True)
    (app / "rommlib.py").write_text('VERSION = "0.0.1"\nBUILD = ""\n')
    (app / "rommbrowser.pygame").write_text("print('old')\n")
    data = tmp_path / "data"
    for rel, body in (("config.json", '{"url": "https://romm.example.com"}'), ("romm-browser.log", "log\n"),
                      ("tools/dolphin-tool/dolphin-tool", "bin"), ("covers/1.png", "png"),
                      ("app-backup/rommlib.py", 'VERSION = "0.0.0"\n')):
        (data / rel).parent.mkdir(parents=True, exist_ok=True)
        (data / rel).write_text(body)
    return app, data


def _user_state(data):
    return {k: v for k, v in _tree(data).items() if k.split(os.sep)[0] not in ("app-staging", "app-backup")}


def _run_remote(data, app):
    """As deploy.sh runs it on the box: the script on python3's stdin."""
    with open(SWAP, "rb") as f:
        return subprocess.run(["sh", "-c", 'python3 - "$1" "$2"', "_", str(data), str(app)], stdin=f,
                              capture_output=True, text=True, env=dict(os.environ, PATH=os.path.dirname(sys.executable)
                                                                       + os.pathsep + os.environ.get("PATH", "")))


def test_a_deploy_swaps_the_app_in_and_keeps_the_old_one_as_the_backup(box):
    app, data = box
    before = _user_state(data)
    _stage_files(data / "app-staging" / "rommbrowser")
    p = _run_remote(data, app)
    assert p.returncode == 0, p.stdout + p.stderr
    assert (app / "romm" / "core.py").read_bytes() == open(os.path.join(APP, "romm", "core.py"), "rb").read()
    assert (app / "fonts").is_dir() and (app / "LICENSE").exists()
    assert (data / "app-backup" / "rommbrowser.pygame").read_text() == "print('old')\n"   # "Go back" has the old app
    assert not (data / "app-staging").exists()
    assert not any("__pycache__" in k for k in _tree(app))
    assert _user_state(data) == before                                  # settings, logs, tools, caches untouched
    assert sorted(os.listdir(app.parent)) == ["rommbrowser"]


def test_a_staged_copy_that_doesnt_compile_leaves_the_box_as_it_was(box):
    app, data = box
    before_app, before_backup, before = _tree(app), _tree(data / "app-backup"), _user_state(data)
    staged = data / "app-staging" / "rommbrowser"
    _stage_files(staged)
    with open(staged / "romm" / "core.py", "a") as f:
        f.write("\ndef broken(:\n")
    p = _run_remote(data, app)
    assert p.returncode != 0 and "romm/core.py" in p.stderr and "unchanged" in p.stderr
    assert _tree(app) == before_app and _tree(data / "app-backup") == before_backup
    assert _user_state(data) == before and not (data / "app-staging").exists()


def test_an_incomplete_staged_copy_is_refused(box):
    app, data = box
    staged = data / "app-staging" / "rommbrowser"
    _stage_files(staged)
    os.remove(staged / "romm" / "__init__.py")
    p = _run_remote(data, app)
    assert p.returncode != 0 and "missing romm/__init__.py" in p.stderr
    assert (app / "rommbrowser.pygame").read_text() == "print('old')\n"


def test_a_first_deploy_installs_without_a_backup(tmp_path):
    app, data = tmp_path / "roms" / "pygame" / "rommbrowser", tmp_path / "data"
    _stage_files(data / "app-staging" / "rommbrowser")
    p = _run_remote(data, app)
    assert p.returncode == 0, p.stdout + p.stderr
    assert (app / "rommbrowser.pygame").exists() and not (data / "app-backup").exists()


def test_without_an_atomic_exchange_the_two_step_swap_still_keeps_a_backup(box, monkeypatch):
    """Filesystems without renameat2 RENAME_EXCHANGE (exFAT): two renames."""
    app, data = box
    mod = _swap_module()

    def no_exchange(a, b):
        raise OSError(22, "Invalid argument", a)
    monkeypatch.setattr(mod, "_exchange", no_exchange)
    _stage_files(data / "app-staging" / "rommbrowser")
    assert mod.main(str(data), str(app)) == 0
    assert (app / "romm" / "core.py").exists()
    assert (data / "app-backup" / "rommbrowser.pygame").read_text() == "print('old')\n"


def test_deploy_ships_the_release_zips_file_set(tmp_path):
    out = tmp_path / "dist"
    subprocess.run([os.path.join(APP, "tools", "build-release.sh")], env=dict(os.environ, OUT=str(out), ALLOW_OLD_DATE="1"),
                   check=True, capture_output=True)
    with zipfile.ZipFile(io.BytesIO((out / "romm-o-cera.zip").read_bytes())) as z:
        released = {n[len("rommbrowser/"):] for n in z.namelist() if not n.endswith("/")}
    _stage_files(tmp_path / "rommbrowser")
    assert released - {"INSTALL.txt"} == set(_tree(tmp_path / "rommbrowser"))


FAKE_SSH = r"""#!/bin/sh
# ssh stand-in: drop the options and the target, run the remote command here
while [ $# -gt 0 ]; do case "$1" in -o|-i) shift 2 ;; -*) shift ;; *) break ;; esac; done
shift
printf '%s\n' "$*" >> "$FAKE_LOG"
exec sh -c "$*"
"""
FAKE_SCP = r"""#!/bin/sh
# scp stand-in: copy locally; the last argument is target:path
for last in "$@"; do :; done
while [ $# -gt 1 ]; do
    case "$1" in -o|-i) shift ;; -*) ;; *) cp -r "$1" "${last#*:}" ;; esac
    shift
done
"""


def _deploy(tmp_path, app, data, repo=APP, **env):
    """deploy.sh from repo against app/data, with ssh and scp run locally."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    for name, body in (("ssh", FAKE_SSH), ("scp", FAKE_SCP)):
        (bindir / name).write_text(body)
        (bindir / name).chmod(0o755)
    path = os.pathsep.join((str(bindir), os.path.dirname(sys.executable), os.environ.get("PATH", "")))
    e = dict(os.environ, PATH=path, ROMM_O_CERA_DEST=str(app), ROMM_O_CERA_DATA=str(data),
             ROMM_O_CERA_ENV=str(tmp_path / "no.env"), FAKE_LOG=str(tmp_path / "ssh.log"), FORCE="1")
    for k in ("ROMM_URL", "ROMM_TOKEN", "ROMM_BROWSER_TOKEN", "ROMM_VERIFY_TLS", "SSH_KEY", "SSH_USER"):
        e.pop(k, None)
    e.update(env)
    return subprocess.run([os.path.join(str(repo), "deploy.sh"), "testbox"], env=e, capture_output=True, text=True)


def test_deploy_sh_end_to_end(box, tmp_path):
    """The whole script with ssh/scp run locally: swap, BUILD stamp, config
    merge with the token on stdin (never in a command line)."""
    app, data = box
    p = _deploy(tmp_path, app, data, ROMM_TOKEN="rmm_secret", ROMM_VERIFY_TLS="false")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "Deployed to testbox:" in p.stdout
    assert (data / "app-backup" / "rommbrowser.pygame").read_text() == "print('old')\n"
    assert not (data / "app-staging").exists()
    assert (app / "romm" / "core.py").exists() and (app / "LICENSE").exists()
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=APP, capture_output=True, text=True).stdout.strip()
    if head:
        assert re.search(r'^BUILD = "%s(-dirty)?" ' % head, (app / "rommlib.py").read_text(), re.M)
    cfg = json.loads((data / "config.json").read_text())
    assert cfg == {"url": "https://romm.example.com", "token": "rmm_secret", "verify_tls": False}
    assert "rmm_secret" not in (tmp_path / "ssh.log").read_text()
    assert (data / "romm-browser.log").read_text() == "log\n" and (data / "covers" / "1.png").exists()


def _checkout_copy(dest):
    """The files deploy.sh needs, outside git (so no BUILD stamp)."""
    _stage_files(dest)
    shutil.copy(os.path.join(APP, "deploy.sh"), str(dest))
    os.makedirs(os.path.join(str(dest), "tools"))
    for f in ("app-files.sh", "deploy_swap.py"):
        shutil.copy(os.path.join(APP, "tools", f), os.path.join(str(dest), "tools"))


def test_deploy_sh_leaves_the_box_alone_when_the_check_fails(box, tmp_path):
    """A checkout that doesn't compile never reaches the live app, and the
    config isn't changed either."""
    app, data = box
    before_app, before = _tree(app), _user_state(data)
    repo = tmp_path / "repo"
    _checkout_copy(repo)
    with open(repo / "rb_dl.py", "a") as f:
        f.write("\nif True\n")
    p = _deploy(tmp_path, app, data, repo=repo, ROMM_TOKEN="rmm_secret")
    assert p.returncode != 0 and "rb_dl.py doesn't compile" in p.stderr, p.stdout + p.stderr
    assert _tree(app) == before_app and _user_state(data) == before
    assert not (data / "app-staging").exists()


def test_deploy_sh_wont_deploy_under_a_running_game(box, tmp_path):
    app, data = box
    before_app = _tree(app)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "curl").write_text('#!/bin/sh\necho \'{"name": "Some Game"}\'\n')
    (bindir / "curl").chmod(0o755)
    p = _deploy(tmp_path, app, data, FORCE="")
    assert p.returncode != 0 and "Something is running" in p.stderr
    assert _tree(app) == before_app and not (data / "app-staging").exists()
