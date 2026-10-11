"""3DS -> Z3DS (.zcci): the writer and reader (box libzstd via ctypes), the
worker's verified conversion, queueing after download, the planner, and the
download prompt's format line."""
import json
import os
import random
import shutil
import subprocess
import zlib

import pytest

import rommlib

pytestmark = pytest.mark.skipif(not rommlib.z3ds_supported(), reason="no libzstd here")
MB = 1 << 20


def _cart(path, size=3 * MB + 12345, crypto=False, seed=1):
    """A fake decrypted CCI: NCSD header, the game's NCCH at 0x4000, then a
    body that's half compressible, half noise (and not a whole number of frames)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rnd = random.Random(seed)
    data = bytearray(size)
    data[0x100:0x104] = b"NCSD"
    data[0x120:0x124] = (0x4000 // 0x200).to_bytes(4, "little")
    n = 0x4000
    data[n + 0x100:n + 0x104] = b"NCCH"
    data[n + 0x104:n + 0x108] = ((size - n) // 0x200).to_bytes(4, "little")
    data[n + 0x112:n + 0x114] = (3).to_bytes(2, "little")
    data[n + 0x118:n + 0x120] = (0x0004000000045F00).to_bytes(8, "little")
    data[n + 0x18F] = 0x00 if crypto else 0x04          # NoCrypto flag
    half = (size - 0x5000) // 2
    data[0x5000:0x5000 + half] = (b"lorem ipsum dolor " * (half // 18 + 1))[:half]
    data[0x5000 + half:] = bytes(rnd.getrandbits(8) for _ in range(size - 0x5000 - half))
    with open(path, "wb") as f:
        f.write(data)
    return path


def _crc(path):
    return "%08x" % (zlib.crc32(open(path, "rb").read()) & 0xffffffff)


def test_round_trip_and_damage_detected(isolated, tmp_path):
    src = _cart(str(tmp_path / "Lorem Quest (USA).3ds"))
    dst = str(tmp_path / "Lorem Quest (USA).zcci")
    assert rommlib.z3ds_target(src) == dst
    assert rommlib.z3ds_target("x/Game.cxi").endswith("Game.zcxi") and rommlib.z3ds_target("Game.cia") is None
    seen = []
    crc = rommlib.z3ds_compress(src, dst, threads=3, progress=seen.append)
    assert crc == _crc(src) and seen[-1] == 1.0
    assert os.path.getsize(dst) < os.path.getsize(src)
    assert rommlib.z3ds_verify(dst) == (crc, os.path.getsize(src))
    assert rommlib.z3ds_header(dst) == {"size": os.path.getsize(src), "complete": True}
    raw = open(dst, "rb").read()
    assert raw[:8] == b"Z3DS" + b"NCSD" and raw[8] == 1
    if shutil.which("zstd"):           # the payload is a standard zstd stream: stock tools agree
        base = int.from_bytes(raw[0x0A:0x0C], "little") + int.from_bytes(raw[0x0C:0x10], "little")
        out = subprocess.run(["zstd", "-dcq"], input=raw[base:], capture_output=True).stdout
        assert "%08x" % (zlib.crc32(out) & 0xffffffff) == crc
    cut = str(tmp_path / "cut.zcci")
    open(cut, "wb").write(raw[:-5000])
    assert rommlib.z3ds_header(cut)["complete"] is False
    with pytest.raises(rommlib.Z3dsError):
        rommlib.z3ds_verify(cut)
    assert rommlib.z3ds_header(src) is None


def test_encrypted_and_foreign_files_refused(isolated, tmp_path):
    enc = _cart(str(tmp_path / "Enc.3ds"), crypto=True)
    with pytest.raises(rommlib.Z3dsError, match="encrypted"):
        rommlib.z3ds_info(enc)
    junk = tmp_path / "Junk.3ds"
    junk.write_bytes(b"\0" * 0x5000)
    with pytest.raises(rommlib.Z3dsError, match="not a 3DS"):
        rommlib.z3ds_info(str(junk))


def test_worker_converts_verifies_and_keeps_refused(isolated, monkeypatch):
    import rb_chd
    monkeypatch.setattr(rb_chd, "running_game", lambda: "")
    monkeypatch.setattr(rommlib, "load_config", lambda: dict(rommlib.DEFAULT_CONFIG, z3ds=True, keep_free_gb=0))
    sysdir = os.path.join(rommlib.ROMS_ROOT, "3ds")
    good = _cart(os.path.join(sysdir, "Lorem Quest (USA).3ds"))
    want = _crc(good)
    assert rb_chd.convert({"system": "3ds", "mode": "z3ds", "sources": [good], "roots": [good]}) is True
    out = os.path.join(sysdir, "Lorem Quest (USA).zcci")
    assert os.path.exists(out) and not os.path.exists(good) and not os.path.exists(out + ".part")
    assert rommlib.z3ds_verify(out)[0] == want
    enc = _cart(os.path.join(sysdir, "Enc (USA).3ds"), crypto=True)
    assert rb_chd.convert({"system": "3ds", "mode": "z3ds", "sources": [enc], "roots": [enc]}) is False
    assert os.path.exists(enc) and not os.path.exists(os.path.join(sysdir, "Enc (USA).zcci"))
    assert "encrypted" in open(rommlib.LOG_PATH).read()
    # a fresh copy next to a damaged .zcci (Check games' download again) replaces it
    again = _cart(os.path.join(sysdir, "Lorem Quest (USA).3ds"))
    open(out, "r+b").truncate(1000)
    assert rb_chd.convert({"system": "3ds", "mode": "z3ds", "sources": [again], "roots": [again]}) is True
    assert rommlib.z3ds_verify(out)[0] == want and not os.path.exists(again)
    # turned off: nothing happens
    monkeypatch.setattr(rommlib, "load_config", lambda: dict(rommlib.DEFAULT_CONFIG, z3ds=False))
    off = _cart(os.path.join(sysdir, "Off (USA).3ds"))
    assert rb_chd.convert({"system": "3ds", "mode": "z3ds", "sources": [off], "roots": [off]}) is False
    assert os.path.exists(off)


def test_downloads_queue_planner_and_prompt(isolated, monkeypatch):
    import rb_chd_plan
    cfg = dict(rommlib.DEFAULT_CONFIG, z3ds=True)
    d = rommlib.Downloader(None, rommlib.Library(), cfg, persist=False)
    d.es = {"3ds": {".3ds", ".cci", ".cxi", ".cia", ".zcci", ".zcxi", ".zcia"}}
    src = _cart(os.path.join(rommlib.ROMS_ROOT, "3ds", "Ipsum (USA).3ds"), size=0x8000)
    j = rommlib.Job({"id": 5}, "3ds")
    assert d._postprocess_file(j, src) == [src] and j.chd_task
    t = json.load(open(os.path.join(rommlib.CHD_QUEUE_DIR, j.chd_task)))
    assert t["mode"] == "z3ds" and t["sources"] == [src]
    cfg["z3ds"] = False
    j2 = rommlib.Job({"id": 6}, "3ds")
    d._postprocess_file(j2, src)
    assert j2.chd_task is None
    # planner: decrypted images without a .zcci; encrypted ones skipped
    monkeypatch.setattr(rommlib, "load_config", lambda: dict(rommlib.DEFAULT_CONFIG, z3ds=True))
    _cart(os.path.join(rommlib.ROMS_ROOT, "3ds", "Done (USA).3ds"), size=0x8000)
    open(os.path.join(rommlib.ROMS_ROOT, "3ds", "Done (USA).zcci"), "wb").close()
    found = [os.path.basename(r) for r, _, _ in rb_chd_plan.games_for("3ds")]
    assert "Ipsum (USA).3ds" in found
    # the download prompt says what it becomes
    det = {"files": [{"file_name": "Ipsum (USA).zip", "file_size_bytes": 100,
                      "archive_members": [{"name": "Ipsum (USA).3ds", "size": 512 * MB}]}]}
    assert rommlib.disc_layout(det, "3ds", {"z3ds": True})["convert"] == "ZCCI"
    assert rommlib.disc_layout(det, "3ds", {"z3ds": False})["convert"] is None
    assert rommlib.disc_layout(det, "3ds", {"z3ds": True})["label"] == "3DS"


def test_unfinished_conversion_is_a_leftover_only_when_idle(isolated):
    p = os.path.join(rommlib.ROMS_ROOT, "3ds", "Half (USA).zcci.part")
    os.makedirs(os.path.dirname(p))
    open(p, "wb").write(b"x" * 10)
    assert [i["kind"] for i in rommlib.find_leftovers(chd_busy=False)] == ["Unfinished conversion"]
    assert rommlib.find_leftovers(chd_busy=True) == []


def test_damage_inside_a_frame_or_the_seek_table_is_caught(isolated, tmp_path):
    """Frames carry zstd's checksum, so a flipped byte fails on its own; a
    mangled seek table is refused before any buffer is sized from it."""
    src = _cart(str(tmp_path / "Lorem.3ds"), size=2 * MB)
    dst = str(tmp_path / "Lorem.zcci")
    rommlib.z3ds_compress(src, dst)
    raw = bytearray(open(dst, "rb").read())
    base = int.from_bytes(raw[0x0A:0x0C], "little") + int.from_bytes(raw[0x0C:0x10], "little")

    def broken(mutate):
        b = bytearray(raw)
        mutate(b)
        p = str(tmp_path / "bad.zcci")
        open(p, "wb").write(b)
        with pytest.raises(rommlib.Z3dsError):
            rommlib.z3ds_verify(p)
    broken(lambda b: b.__setitem__(base + 200, b[base + 200] ^ 0xFF))          # inside the first frame
    broken(lambda b: b.__setitem__(len(b) - 6, b[len(b) - 6] ^ 0x80))          # frame count's high bit
    n = int.from_bytes(raw[-9:-5], "little")
    entry = len(raw) - 9 - n * 8 + 4                                           # first frame's decoded size
    broken(lambda b: b.__setitem__(entry + 3, b[entry + 3] | 0x80))
