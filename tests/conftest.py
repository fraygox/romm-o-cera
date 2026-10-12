"""Shared fixtures. Run from the repo root:  python -m pytest tests

  tests/test_logic.py     pure logic (no network)
  tests/test_pipeline.py  downloads + headless UI smoke against tools/mock_romm.py
  tests/test_contract.py  the RomM API surface the app relies on — needs
                          ROMM_CONTRACT_URL + ROMM_CONTRACT_TOKEN (skipped otherwise);
                          tests/contract/disposable.py runs it against a throwaway
                          RomM of any version.
"""
import importlib.machinery
import os
import socket
import subprocess
import sys
import time

import pytest

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import rommlib  # noqa: E402

PATH_ATTRS = ("DATA_DIR", "ROMS_ROOT", "CONFIG_PATH", "STATE_PATH", "GAMELIST_QUEUE",
              "CHD_QUEUE_DIR", "COVER_CACHE", "LOG_PATH")


@pytest.fixture(autouse=True)
def _fast_polls(monkeypatch):
    """Poll intervals that only set how soon a wait notices it is over: the
    download/unpack workers re-check their queue (a retry or space re-check
    falling due) every WORKER_POLL s, and rb_chd checks a running converter
    every TOOL_POLL s. Production keeps 1 s / 3 s; tests run the same loops,
    just without idling up to a second or three per wait."""
    import rb_chd
    monkeypatch.setattr(rommlib, "WORKER_POLL", 0.05)
    monkeypatch.setattr(rb_chd, "TOOL_POLL", 0.05)


@pytest.fixture(autouse=True)
def _stop_workers():
    """Each test's download workers stop before the next test starts (they'd
    otherwise keep writing into whatever paths the next test patches in)."""
    yield
    while rommlib.Downloader.instances:
        rommlib.Downloader.instances.pop().close()


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Point every rommlib path at a fresh temp tree (data dir NOT created,
    like a first run on a new box)."""
    data, roms = tmp_path / "data", tmp_path / "roms"
    roms.mkdir()
    vals = {
        "DATA_DIR": str(data), "ROMS_ROOT": str(roms),
        "CONFIG_PATH": str(data / "config.json"), "STATE_PATH": str(data / "installed.json"),
        "GAMELIST_QUEUE": str(data / "gamelist-pending.jsonl"),
        "CHD_QUEUE_DIR": str(data / "chd-queue"), "COVER_CACHE": str(data / "cache" / "covers"),
        "LOG_PATH": str(data / "romm-browser.log"),
        "QUEUE_PATH": str(data / "download-queue.json"), "DL_LOCK": str(data / "downloads.lock"),
        "DL_STOP": str(data / "downloads.stop"), "DL_STATUS": str(data / "downloads-status.json"),
        "USERDATA": str(tmp_path), "BIOS_DB": str(tmp_path / "batocera-systems"),
        "SAVES_ROOT": str(tmp_path / "saves"),
        # joined from DATA_DIR once, at import
        "UPDATE_CACHE": str(data / "update-check.json"), "APP_STAGING": str(data / "app-staging"),
        "APP_BACKUP": str(data / "app-backup"), "TOOLS_DIR": str(data / "tools"),
    }
    monkeypatch.setattr(rommlib, "_BIOS_DB", None)
    for k, v in vals.items():
        monkeypatch.setattr(rommlib, k, v)
    import rb_finalize
    monkeypatch.setattr(rb_finalize, "LOCK", str(data / "finalize.lock"))
    monkeypatch.setattr(rb_finalize, "DEFERRED", str(data / "gamelist-deferred.jsonl"))
    for env in ("ROMM_URL", "ROMM_TOKEN", "ROMM_VERIFY_TLS"):
        monkeypatch.delenv(env, raising=False)
    return tmp_path


@pytest.fixture(scope="session")
def rb():
    """The UI module (rommbrowser.pygame); skipped where pygame isn't installed."""
    pytest.importorskip("pygame")
    return importlib.machinery.SourceFileLoader(
        "rommbrowser", os.path.join(APP, "rommbrowser.pygame")).load_module()


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_mock(rate_mb):
    """tools/mock_romm.py on a free port; yields its URL, stops it after."""
    pytest.importorskip("PIL")
    port = _free_port()
    p = subprocess.Popen([sys.executable, os.path.join(APP, "tools", "mock_romm.py"),
                          "--port", str(port), "--rate-mb", str(rate_mb)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = "http://127.0.0.1:%d" % port
    import requests
    for _ in range(100):
        try:
            if requests.get(url + "/api/platforms", timeout=1).ok:
                break
        except requests.RequestException:
            time.sleep(0.1)
    else:
        p.kill()
        pytest.fail("mock RomM did not start")
    yield url
    p.terminate()
    p.wait(5)


@pytest.fixture(scope="session")
def mock_url():
    """tools/mock_romm.py on a free port for the whole session, throttled to
    400 MB/s: pause/cancel/handover tests rely on a big transfer still being
    under way when they act."""
    yield from _run_mock(400)


@pytest.fixture(scope="session")
def fast_mock_url():
    """The same mock library, unthrottled: for tests that only check what a
    finished multi-GB download left behind."""
    yield from _run_mock(100000)


@pytest.fixture
def fake_es(monkeypatch):
    """Start tests/fake_es.FakeES for a {system: {exts}} map and point
    rb_finalize at it (short waits)."""
    from fake_es import FakeES
    import rb_finalize
    made = []

    def make(systems):
        es = FakeES(rommlib.ROMS_ROOT, systems)
        monkeypatch.setattr(rb_finalize, "ES", es.url)
        monkeypatch.setattr(rb_finalize.time, "sleep", lambda s: None)
        made.append(es)
        return es
    yield make
    for es in made:
        es.close()
