"""Self-update: stage from a (fake, local) GitHub release, install, roll back."""
import hashlib
import shutil
import http.server
import io
import json
import os
import subprocess
import threading
import zipfile

import pytest

import rommlib
from conftest import APP


class _GH(http.server.BaseHTTPRequestHandler):
    files = {}
    seen_auth = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        self.seen_auth.append(self.headers.get("Authorization"))
        body = self.files.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="session")
def release_zip(tmp_path_factory):
    """tools/build-release.sh's zip, built once per session (read-only bytes)."""
    out = tmp_path_factory.mktemp("dist")
    subprocess.run([os.path.join(APP, "tools", "build-release.sh")], env=dict(os.environ, OUT=str(out), ALLOW_OLD_DATE="1"),
                   check=True, capture_output=True)
    return (out / "romm-o-cera.zip").read_bytes()


@pytest.fixture
def fake_github(release_zip, monkeypatch):
    srv = http.server.HTTPServer(("127.0.0.1", 0), _GH)
    threading.Thread(target=srv.serve_forever, args=(0.05,), daemon=True).start()   # quick shutdown()
    base = "http://127.0.0.1:%d" % srv.server_port
    monkeypatch.setattr(rommlib, "GITHUB_API", base)
    monkeypatch.setattr(rommlib, "UPDATE_REPO", "someone/romm-o-cera")
    zipdata = release_zip

    def publish(tag, zip_bytes=zipdata, sums=None):
        sums = sums or "%s  romm-o-cera.zip\n" % hashlib.sha256(zip_bytes).hexdigest()
        _GH.files = {
            "/repos/someone/romm-o-cera/releases/latest": json.dumps({"tag_name": tag, "assets": [
                {"name": "romm-o-cera.zip", "browser_download_url": base + "/dl/zip"},
                {"name": "SHA256SUMS", "browser_download_url": base + "/dl/sums"}]}).encode(),
            "/dl/zip": zip_bytes, "/dl/sums": sums.encode()}
        _GH.seen_auth.clear()
    yield publish
    srv.shutdown()


def _fake_app(tmp_path, version):
    app = tmp_path / "roms" / "pygame" / "rommbrowser"
    app.mkdir(parents=True)
    (app / "rommlib.py").write_text('VERSION = "%s"\n' % version)
    (app / "rommbrowser.pygame").write_text("print('old')\n")
    return app


def test_update_install_and_rollback(isolated, fake_github, tmp_path):
    fake_github("v" + rommlib.VERSION)
    app = _fake_app(tmp_path, "0.0.1")
    staged = rommlib.stage_release(rommlib.release_info(), staging=str(tmp_path / "stage"))
    ver = rommlib.install_staged(staged, app_dir=str(app), backup=str(tmp_path / "backup"))
    assert ver == rommlib.VERSION and (app / "fonts").is_dir() and (app / "rb_dl.py").exists()
    assert rommlib.backup_version(str(tmp_path / "backup")) == "0.0.1"
    assert not (tmp_path / "stage").exists()
    assert all(a is None for a in _GH.seen_auth)               # RomM token never sent to GitHub
    assert rommlib.rollback(app_dir=str(app), backup=str(tmp_path / "backup")) == "0.0.1"
    assert rommlib.rollback(app_dir=str(app), backup=str(tmp_path / "backup")) == rommlib.VERSION


def test_update_rejects_bad_releases(isolated, fake_github, tmp_path):
    app = _fake_app(tmp_path, "0.0.1")
    stage = str(tmp_path / "stage")
    fake_github("v" + rommlib.VERSION, sums="0" * 64 + "  romm-o-cera.zip\n")
    with pytest.raises(rommlib.UpdateError, match="Checksum"):
        rommlib.stage_release(rommlib.release_info(), staging=stage)
    fake_github("v9.9.9")                                       # tag doesn't match the code inside
    with pytest.raises(rommlib.UpdateError, match="contains version"):
        rommlib.stage_release(rommlib.release_info(), staging=stage)
    evil = io.BytesIO()
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("rommbrowser/rommlib.py", 'VERSION = "%s"\n' % rommlib.VERSION)
        z.writestr("../escape.sh", "x")
    fake_github("v" + rommlib.VERSION, zip_bytes=evil.getvalue())
    with pytest.raises(rommlib.UpdateError, match="Unexpected path"):
        rommlib.stage_release(rommlib.release_info(), staging=stage)
    assert not (tmp_path / "escape.sh").exists() and not os.path.exists(stage)
    assert (app / "rommbrowser.pygame").read_text() == "print('old')\n"   # untouched


def _serve_release(files):
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = files.get(self.path)
            if body is None:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, args=(0.05,), daemon=True).start()
    return srv, "http://127.0.0.1:%d" % srv.server_port


def _install_sh(tmp_path, base):
    env = dict(os.environ, ROMM_O_CERA_BASE=base, ROMM_O_CERA_DEST=str(tmp_path / "roms" / "pygame" / "rommbrowser"),
               ROMM_O_CERA_DATA=str(tmp_path / "data"))
    return subprocess.run(["sh", os.path.join(APP, "tools", "install.sh")], env=env, capture_output=True, text=True)


@pytest.mark.skipif(not shutil.which("curl"), reason="install.sh needs curl")
def test_install_sh_swaps_in_one_step_and_restores_a_lost_app(release_zip, tmp_path):
    """install.sh had the same two-step swap window as the in-app updater, and
    nothing restored an app folder an interrupted swap had left missing."""
    srv, base = _serve_release({"/romm-o-cera.zip": release_zip,
                                "/SHA256SUMS": ("%s  romm-o-cera.zip\n" % hashlib.sha256(release_zip).hexdigest()).encode()})
    try:
        app = _fake_app(tmp_path, "0.0.1")
        p = _install_sh(tmp_path, base)
        assert p.returncode == 0, p.stdout + p.stderr
        assert "VERSION = \"%s\"" % rommlib.VERSION in (app / "rommlib.py").read_text()
        assert sorted(os.listdir(app.parent)) == ["rommbrowser"]          # no .new/.old left behind
    finally:
        srv.shutdown()
    # an update cut off between its two renames: no app folder, the old one in app-backup
    backup = tmp_path / "data" / "app-backup"
    shutil.move(str(app), str(backup))
    p = _install_sh(tmp_path, "http://127.0.0.1:9")                       # and no network now
    assert p.returncode != 0
    assert (app / "rommbrowser.pygame").exists(), "the lost app wasn't restored: " + p.stdout + p.stderr


def test_the_update_check_asks_the_same_github_as_the_download(isolated, fake_github, monkeypatch):
    """newer_release hard-coded api.github.com while the download used
    GITHUB_API (ROMM_BROWSER_GITHUB_API): the check and the download could ask
    different servers, and the update offer couldn't be tested end to end."""
    fake_github("v99.0.0")
    assert rommlib.newer_release(dict(rommlib.DEFAULT_CONFIG), force=True) == "v99.0.0"
