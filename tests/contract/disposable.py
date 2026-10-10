#!/usr/bin/env python3
"""Run tests/test_contract.py against a throwaway RomM of a given version.

  python tests/contract/disposable.py --tag 5.3.1 [--keep] [--image rommapp/romm]

Starts MariaDB + rommapp/romm:<tag> in their own docker network (RomM
published on 127.0.0.1 only), generates a small library (carts across A–Z,
a cue/bin folder, a GDI folder, a folder holding one ISO), creates the first
admin, scans over RomM's websocket (the same call the web UI makes), creates a
collection and a read-only client token, runs the contract tests, and tears it
all down. No metadata providers are enabled: every game stays "unidentified",
which is fine — the tests check API shape, not matching.

Exit code = pytest's (0 pass, 1 tests failed); 3 = the harness itself failed
(couldn't start/scan RomM). Needs docker, requests, pytest, python-socketio.
"""
import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import zipfile

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(os.path.dirname(HERE))
SCOPES = ["me.read", "roms.read", "platforms.read", "assets.read", "collections.read",
          "firmware.read", "roms.user.read"]
NATO = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel", "India",
        "Juliett", "Kilo", "Lima", "Mike", "November", "Oscar", "Papa", "Quebec", "Romeo",
        "Sierra", "Tango", "Uniform", "Victor", "Whiskey", "Xray", "Yankee", "Zulu"]


class HarnessError(Exception):
    pass


def log(msg):
    print("[disposable] " + msg, flush=True)


def sh(*cmd, check=True, capture=False):
    r = subprocess.run(list(cmd), check=False, text=True,
                       stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                       stderr=subprocess.PIPE)
    if check and r.returncode != 0:
        raise HarnessError("%s failed: %s" % (" ".join(cmd[:3]), (r.stderr or "").strip()[-400:]))
    return (r.stdout or "").strip()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_library(root):
    """Small, deterministic, legally meaningless library (zero/random bytes)."""
    rnd = lambda n: secrets.token_bytes(n)       # noqa: E731
    roms = os.path.join(root, "roms")
    os.makedirs(os.path.join(roms, "nes"))
    for i, word in enumerate(NATO):               # A–Z, so char_index has 26 letters
        name = "%s Contract Test (USA)" % word
        with zipfile.ZipFile(os.path.join(roms, "nes", name + ".zip"), "w") as z:
            z.writestr(name + ".nes", b"NES\x1a" + rnd(16 * 1024 + i))
    os.makedirs(os.path.join(roms, "gba"))
    with zipfile.ZipFile(os.path.join(roms, "gba", "Contract Cart (World).zip"), "w") as z:
        z.writestr("Contract Cart (World).gba", rnd(64 * 1024))
    psx = os.path.join(roms, "psx", "Contract Disc (USA)")
    os.makedirs(psx)
    open(os.path.join(psx, "Contract Disc (USA).bin"), "wb").write(rnd(2352 * 64))
    open(os.path.join(psx, "Contract Disc (USA).cue"), "w").write(
        'FILE "Contract Disc (USA).bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
    dc = os.path.join(roms, "dreamcast", "Contract GD (USA)")
    os.makedirs(dc)
    for t in ("track01.bin", "track02.raw", "track03.bin"):
        open(os.path.join(dc, t), "wb").write(rnd(2352 * 32))
    open(os.path.join(dc, "Contract GD (USA).gdi"), "w").write(
        "3\n1 0 4 2352 track01.bin 0\n2 600 0 2352 track02.raw 0\n3 45000 4 2352 track03.bin 0\n")
    ps2 = os.path.join(roms, "ps2", "Contract Nested (USA)")
    os.makedirs(ps2)
    open(os.path.join(ps2, "Contract Nested (USA).iso"), "wb").write(rnd(2048 * 64))
    os.makedirs(os.path.join(root, "bios"))
    cfg = os.path.join(root, "config")
    os.makedirs(cfg)
    with open(os.path.join(cfg, "config.yml"), "w") as f:
        f.write("filesystem:\n  structure:\n    default: roms/{platform}/{game}\n"
                "    firmware: bios/{platform}\n")


def wait_http(url, timeout):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if requests.get(url, timeout=3).status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(2)
    raise HarnessError("RomM did not come up at %s within %ds" % (url, timeout))


def scan(base, user, pw):
    import socketio
    s = requests.Session()
    r = s.post(base + "/api/login", auth=(user, pw), timeout=20)
    if r.status_code not in (200, 204):
        raise HarnessError("login failed: HTTP %d" % r.status_code)
    cookie = "; ".join("%s=%s" % (c.name, c.value) for c in s.cookies)
    done = {"ok": None, "msg": ""}
    sio = socketio.Client(reconnection=False)

    @sio.on("scan:done")
    def _ok(*_):
        done["ok"] = True

    @sio.on("scan:done_ko")
    def _ko(msg=None, *_):
        done["ok"], done["msg"] = False, str(msg)

    sio.connect(base, socketio_path="/ws/socket.io", headers={"Cookie": cookie},
                transports=["websocket"], wait_timeout=20)
    sio.emit("scan", {"platforms": [], "type": "complete", "apis": []})
    t0 = time.time()
    while done["ok"] is None and time.time() - t0 < 600:
        time.sleep(1)
    sio.disconnect()
    if done["ok"] is None:
        raise HarnessError("scan did not finish within 10 min")
    if not done["ok"]:
        raise HarnessError("scan refused: " + done["msg"])
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True, help="rommapp/romm image tag, e.g. 5.3.1")
    ap.add_argument("--image", default="rommapp/romm")
    ap.add_argument("--db-image", default="mariadb:11.4")
    ap.add_argument("--keep", action="store_true", help="leave containers running (debug)")
    ap.add_argument("pytest_args", nargs="*")
    a = ap.parse_args()

    run = "romm-contract-" + secrets.token_hex(4)
    work = tempfile.mkdtemp(prefix=run + "-")
    os.chmod(work, 0o755)
    port = free_port()
    base = "http://127.0.0.1:%d" % port
    dbpw = secrets.token_hex(16)
    user, pw = "contract", "Contract-" + secrets.token_hex(8)
    rc = 3
    try:
        make_library(work)
        for d in ("resources", "assets"):
            os.makedirs(os.path.join(work, d))
        log("pulling %s:%s" % (a.image, a.tag))
        sh("docker", "pull", "-q", "%s:%s" % (a.image, a.tag))
        sh("docker", "network", "create", run)
        sh("docker", "run", "-d", "--name", run + "-db", "--network", run,
           "-e", "MARIADB_ROOT_PASSWORD=" + dbpw, "-e", "MARIADB_DATABASE=romm",
           "-e", "MARIADB_USER=romm", "-e", "MARIADB_PASSWORD=" + dbpw, a.db_image)
        t0 = time.time()
        while sh("docker", "exec", run + "-db", "mariadb-admin", "ping", "-uroot",
                 "-p" + dbpw, "--silent", check=False, capture=True) != "mysqld is alive":
            if time.time() - t0 > 120:
                raise HarnessError("MariaDB did not start")
            time.sleep(2)
        sh("docker", "run", "-d", "--name", run + "-romm", "--network", run,
           "-p", "127.0.0.1:%d:8080" % port,
           "-e", "DB_HOST=%s-db" % run, "-e", "DB_NAME=romm", "-e", "DB_USER=romm",
           "-e", "DB_PASSWD=" + dbpw, "-e", "ROMM_AUTH_SECRET_KEY=" + secrets.token_hex(32),
           "-e", "DISABLE_CSRF_PROTECTION=true", "-e", "TZ=UTC",
           "-v", "%s:/romm/library:ro" % work, "-v", "%s/config:/romm/config" % work,
           "-v", "%s/resources:/romm/resources" % work, "-v", "%s/assets:/romm/assets" % work,
           "%s:%s" % (a.image, a.tag))
        log("waiting for RomM %s on %s" % (a.tag, base))
        wait_http(base + "/api/heartbeat", 300)

        r = requests.post(base + "/api/users", json={
            "username": user, "email": "contract@example.invalid", "password": pw, "role": "admin"},
            timeout=20)
        if r.status_code not in (200, 201):
            raise HarnessError("creating first admin failed: HTTP %d %s" % (r.status_code, r.text[:200]))
        log("scanning")
        sess = scan(base, user, pw)
        plats = requests.get(base + "/api/platforms", auth=(user, pw), timeout=20).json()
        counts = {p["fs_slug"]: p["rom_count"] for p in plats}
        log("scanned: %s" % counts)
        if sum(counts.values()) < 30:
            raise HarnessError("scan found too few roms: %s" % counts)

        # a collection, so collection filtering is exercised
        roms = requests.get(base + "/api/roms", auth=(user, pw), timeout=20,
                            params={"limit": 3, "with_char_index": "false"}).json()["items"]
        c = sess.post(base + "/api/collections", data={"name": "Theme: Contract"}, timeout=20)
        if c.ok:
            sess.post(base + "/api/collections/%d/roms" % c.json()["id"],
                      json={"rom_ids": [x["id"] for x in roms]}, timeout=20)
        else:
            log("collection create failed (HTTP %d) — collection test will skip" % c.status_code)

        t = requests.post(base + "/api/client-tokens", auth=(user, pw), timeout=20,
                          json={"name": "contract", "scopes": SCOPES, "expires_in": "never"})
        if t.status_code not in (200, 201):
            raise HarnessError("client token failed: HTTP %d %s" % (t.status_code, t.text[:200]))
        token = t.json()["raw_token"]

        log("running contract tests")
        env = dict(os.environ, ROMM_CONTRACT_URL=base, ROMM_CONTRACT_TOKEN=token)
        rc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rs",
                             os.path.join(APP, "tests", "test_contract.py")] + a.pytest_args,
                            cwd=APP, env=env).returncode
        rc = 0 if rc == 0 else 1
    except HarnessError as e:
        log("HARNESS ERROR: %s" % e)
        logs = sh("docker", "logs", "--tail", "40", run + "-romm", check=False, capture=True)
        if logs:
            print(logs[-3000:], file=sys.stderr)
        rc = 3
    finally:
        if a.keep:
            log("kept: %s (RomM %s, user %s / %s)" % (run, base, user, pw))
        else:
            for c in (run + "-romm", run + "-db"):
                sh("docker", "rm", "-f", "-v", c, check=False)
            sh("docker", "network", "rm", run, check=False)
            # files the containers wrote may be root-owned: clean via a container
            sh("docker", "run", "--rm", "-v", "%s:/w" % work, "alpine:3.20", "rm", "-rf",
               "/w/config", "/w/resources", "/w/assets", check=False)
            shutil.rmtree(work, ignore_errors=True)
    sys.exit(rc)


if __name__ == "__main__":
    main()
