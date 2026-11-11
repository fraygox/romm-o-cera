"""Only one RomM-o-Cera runs at a time: a second one would work the same download queue
into the same files."""
import os
import subprocess
import sys

import rommlib

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_a_second_copy_leaves_at_once(isolated):
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy", ROMM_BROWSER_DATA=rommlib.DATA_DIR)
    with rommlib.try_lock(os.path.join(rommlib.DATA_DIR, "app.lock")) as fd:      # "the app is running"
        assert fd is not None
        r = subprocess.run([sys.executable, os.path.join(HERE, "rommbrowser.pygame")], env=env,
                           capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "running already" in r.stdout
    log = open(os.path.join(rommlib.DATA_DIR, "romm-browser.log")).read()
    assert "running already" in log and "start v" not in log         # it never got as far as starting
