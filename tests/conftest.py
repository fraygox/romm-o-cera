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
    }
    monkeypatch.setattr(rommlib, "_BIOS_DB", None)
    for k, v in vals.items():
        monkeypatch.setattr(rommlib, k, v)
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


@pytest.fixture(scope="session")
def mock_url():
    """tools/mock_romm.py on a free port for the whole session."""
    pytest.importorskip("PIL")
    port = _free_port()
    p = subprocess.Popen([sys.executable, os.path.join(APP, "tools", "mock_romm.py"),
                          "--port", str(port), "--rate-mb", "400"],
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
