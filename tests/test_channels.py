"""Release channels: Stable follows GitHub's latest release; Nightly also
follows the rolling "nightly" prerelease (rebuilt from main as it changes). A
nightly is labelled VERSION+nYYYYMMDD.HHMM.commit and sorts after the release
it was built on and before the next one."""
import hashlib
import json
import os
import subprocess

import pytest
import requests

import rommlib
from conftest import APP
from test_update import _GH, _fake_app, fake_github, release_zip  # noqa: F401 (fixtures)


NIGHTLY = "n20261012.1903.abc1234"


def test_versions_order_across_channels():
    k = rommlib.version_key
    assert k("v0.8.1") < k("0.8.1+ab12cd3") < k("0.8.1+n20261012.1903.ab12cd3") < k("v0.8.2")
    assert k("0.8.1+n20261012.1903.ab12cd3") < k("0.8.1+n20261013.0001.0000000")    # by build time
    assert k("0.8.1+n20261012.1903.ab12cd3") > k("0.8.0+n20991231.2359.fffffff")    # base first
    assert k("v0.10.0") > k("0.9.9") and k("v1.2") == k("1.2.0")
    for junk in ("nightly", "dolphin-tool-2603a", "", None):
        assert k(junk) == (0,)


@pytest.fixture(scope="session")
def nightly_zip(tmp_path_factory):
    out = tmp_path_factory.mktemp("nightly")
    r = subprocess.run([os.path.join(APP, "tools", "build-release.sh")],
                       env=dict(os.environ, OUT=str(out), NIGHTLY=NIGHTLY), check=True,
                       capture_output=True, text=True)
    assert r.stdout.strip() == rommlib.VERSION + "+" + NIGHTLY
    return (out / "romm-o-cera.zip").read_bytes()


@pytest.fixture
def github(fake_github, release_zip, nightly_zip, monkeypatch):
    """publish(stable=tag, nightly=label): the fake GitHub's latest release and nightly."""
    base = rommlib.GITHUB_API

    def rel(tag, name, zipdata, n):
        return json.dumps({"tag_name": tag, "name": name, "prerelease": tag == "nightly", "assets": [
            {"name": "romm-o-cera.zip", "browser_download_url": base + "/dl/%s/zip" % n},
            {"name": "SHA256SUMS", "browser_download_url": base + "/dl/%s/sums" % n}]}).encode()

    def publish(stable=None, nightly=None, nightly_bytes=nightly_zip):
        fake_github("v0.0.1")
        files = {}
        if stable:
            files["/repos/someone/romm-o-cera/releases/latest"] = rel(stable, stable, release_zip, "s")
            files["/dl/s/zip"] = release_zip
            files["/dl/s/sums"] = ("%s  romm-o-cera.zip\n" % hashlib.sha256(release_zip).hexdigest()).encode()
        if nightly:
            files["/repos/someone/romm-o-cera/releases/tags/nightly"] = rel("nightly", "Nightly " + nightly,
                                                                           nightly_bytes, "n")
            files["/dl/n/zip"] = nightly_bytes
            files["/dl/n/sums"] = ("%s  romm-o-cera.zip\n" % hashlib.sha256(nightly_bytes).hexdigest()).encode()
        _GH.files = files
    return publish


def _cfg(channel):
    return dict(rommlib.DEFAULT_CONFIG, update_channel=channel)


def test_stable_never_offers_a_nightly(isolated, github, monkeypatch):
    monkeypatch.setattr(rommlib, "VERSION_LABEL", "0.8.1")
    github(stable="v0.8.1", nightly="v0.8.1+n20261012.1903.abc1234")
    assert rommlib.newer_release(_cfg("stable"), force=True) is None
    github(stable="v0.8.2", nightly="v0.8.1+n20261012.1903.abc1234")
    assert rommlib.newer_release(_cfg("stable"), force=True) == "v0.8.2"


def test_nightly_offers_the_newer_of_the_nightly_and_the_release(isolated, github, monkeypatch):
    monkeypatch.setattr(rommlib, "VERSION_LABEL", "0.8.1")
    github(stable="v0.8.1", nightly="v0.8.1+n20261012.1903.abc1234")
    assert rommlib.newer_release(_cfg("nightly"), force=True) == "v0.8.1+n20261012.1903.abc1234"
    github(stable="v0.8.2", nightly="v0.8.1+n20261012.1903.abc1234")      # a release passed it
    assert rommlib.newer_release(_cfg("nightly"), force=True) == "v0.8.2"
    monkeypatch.setattr(rommlib, "VERSION_LABEL", "0.8.1+n20261012.1903.abc1234")
    github(stable="v0.8.1", nightly="v0.8.1+n20261012.1903.abc1234")      # already on it
    assert rommlib.newer_release(_cfg("nightly"), force=True) is None


def test_back_on_stable_a_nightly_waits_for_the_next_release(isolated, github, monkeypatch):
    monkeypatch.setattr(rommlib, "VERSION_LABEL", "0.8.1+n20261012.1903.abc1234")
    github(stable="v0.8.1")
    assert rommlib.newer_release(_cfg("stable"), force=True) is None       # never a downgrade
    github(stable="v0.8.2")
    assert rommlib.newer_release(_cfg("stable"), force=True) == "v0.8.2"


def test_changing_channel_asks_again_without_waiting_a_day(isolated, github, monkeypatch):
    monkeypatch.setattr(rommlib, "VERSION_LABEL", "0.8.1")
    github(stable="v0.8.1", nightly="v0.8.1+n20261012.1903.abc1234")
    assert rommlib.newer_release(_cfg("stable"), force=True) is None
    assert rommlib.newer_release(_cfg("nightly")) == "v0.8.1+n20261012.1903.abc1234"   # not cached
    assert rommlib.newer_release(_cfg("stable")) is None


def test_a_nightly_installs_and_shows_its_label(isolated, github, tmp_path):
    github(stable="v0.8.1", nightly="v%s+%s" % (rommlib.VERSION, NIGHTLY))
    info = rommlib.release_info("v%s+%s" % (rommlib.VERSION, NIGHTLY))
    assert info["tag"] == "nightly" and info["label"] == rommlib.VERSION + "+" + NIGHTLY
    app = _fake_app(tmp_path, "0.0.1")
    staged = rommlib.stage_release(info, staging=str(tmp_path / "stage"))
    rommlib.install_staged(staged, app_dir=str(app), backup=str(tmp_path / "backup"))
    assert rommlib._version_in(str(app), label=True) == rommlib.VERSION + "+" + NIGHTLY


def test_a_nightly_replaced_mid_download_is_refused(isolated, github, release_zip, tmp_path):
    """The title says one build, the zip (replaced in between) holds another."""
    github(nightly="v%s+n20261013.0800.def5678" % rommlib.VERSION)
    info = rommlib.release_info("nightly")
    with pytest.raises(rommlib.UpdateError, match="changed while it downloaded"):
        rommlib.stage_release(info, staging=str(tmp_path / "stage"))
    assert not (tmp_path / "stage").exists()


def test_a_nightly_without_a_label_is_refused(isolated, github):
    github(nightly="v0.8.1+n20261012.1903.abc1234")
    _GH.files["/repos/someone/romm-o-cera/releases/tags/nightly"] = json.dumps(
        {"tag_name": "nightly", "name": "Nightly", "assets": [
            {"name": "romm-o-cera.zip", "browser_download_url": "x"},
            {"name": "SHA256SUMS", "browser_download_url": "y"}]}).encode()
    with pytest.raises(rommlib.UpdateError, match="no version"):
        rommlib.release_info("nightly")


def test_the_update_button_installs_what_it_offered(isolated, rb, github, monkeypatch):
    """Settings' Update used release_info() (the latest release) whatever was
    offered: on nightly it would have installed the release instead."""
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    app = rb.App(None, headless=True)
    asked = []
    monkeypatch.setattr(rommlib, "release_info", lambda tag=None: asked.append(tag) or {})
    monkeypatch.setattr(rommlib, "stage_release", lambda info: "x")
    monkeypatch.setattr(rommlib, "install_staged", lambda staged: "9")
    app.bg = lambda fn, done=None, err=None: done(fn()) if done else fn()
    app.update_tag = "v0.8.1+n20261012.1903.abc1234"
    scr = rb.SettingsScreen(app)
    scr._do_update()
    assert asked == ["v0.8.1+n20261012.1903.abc1234"]


def test_a_check_that_couldnt_reach_github_asks_again_within_the_hour(isolated, github, monkeypatch):
    """A daily check that failed (GitHub slow or unreachable at start-up, a rate-limit answer)
    used to count as done for a day, hiding a new release. It asks again after CHECK_RETRY."""
    import time
    monkeypatch.setattr(rommlib, "VERSION_LABEL", "0.8.1")
    github(stable="v0.8.2")
    real = rommlib.plain_get

    def down(*a, **k):
        raise requests.exceptions.ConnectTimeout("no route")
    monkeypatch.setattr(rommlib, "plain_get", down)
    assert rommlib.newer_release(_cfg("stable")) is None
    monkeypatch.setattr(rommlib, "plain_get", real)
    assert rommlib.newer_release(_cfg("stable")) is None                    # not again at once
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now + rommlib.CHECK_RETRY + 5)
    assert rommlib.newer_release(_cfg("stable")) == "v0.8.2"                # within the hour, not tomorrow


def test_github_requests_give_up_connecting_quickly(isolated, monkeypatch):
    seen = {}

    class R:
        status_code = 200

        @staticmethod
        def json():
            return {"tag_name": "v9.9.9", "assets": [{"name": "romm-o-cera.zip", "browser_download_url": "z"},
                                                     {"name": "SHA256SUMS", "browser_download_url": "s"}]}
    monkeypatch.setattr(rommlib, "plain_get", lambda url, **kw: seen.update(kw) or R())
    rommlib.release_info()
    assert seen["timeout"] == (6, 20)                                       # connect 6 s, read 20 s
