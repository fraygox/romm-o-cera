"""PSP .cso (compressed ISO) -> CHD: the CSO reader (built against a tiny
encoder here), the worker with a fake chdman, and how downloads, the
download prompt and the planner pick it up through the converter table."""
import hashlib
import json
import os
import random
import stat
import struct
import sys
import zlib

import pytest

import rommlib

SECTOR = 2048


def make_iso(sectors=48, seed=1):
    """A synthetic ISO: an ISO9660 marker at 0x8001, compressible sectors
    (zeros, repeated text) and incompressible (random) ones."""
    rnd = random.Random(seed)
    out = bytearray()
    for i in range(sectors):
        if i % 5 == 3:
            out += bytes(rnd.getrandbits(8) for _ in range(SECTOR))
        elif i % 2:
            out += ((b"lorem ipsum %d " % i) * SECTOR)[:SECTOR]
        else:
            out += b"\0" * SECTOR
    out[0x8000:0x8006] = b"\x01CD001"
    return bytes(out)


def make_cso(iso, block=SECTOR, align=0, version=1, header_size=0x18, stored=(), lz4=()):
    """Encode iso as a CSO. Blocks in stored (or that don't shrink) are kept
    as they are (v1: top bit; v2: a full block's span); lz4 marks blocks with
    the top bit (v2's LZ4 flag) without changing their data."""
    n = -(-len(iso) // block)
    body = bytearray()
    index_end = 0x18 + 4 * (n + 1)
    entries = []

    def pos():
        return index_end + len(body)

    def pad():
        while pos() % (1 << align):
            body.append(0)
    for i in range(n):
        pad()
        chunk = iso[i * block:(i + 1) * block]
        c = zlib.compressobj(9, zlib.DEFLATED, -15)
        packed = c.compress(chunk) + c.flush()
        flag = 0
        if i in stored or len(packed) >= len(chunk):
            data = chunk
            flag = 0x80000000 if version < 2 else 0
        else:
            data = packed
        if i in lz4:
            flag = 0x80000000
        entries.append((pos() >> align) | flag)
        body += data
    pad()
    entries.append(pos() >> align)
    head = b"CISO" + struct.pack("<IQIBB2x", header_size, len(iso), block, version, align)
    return head + struct.pack("<%dI" % (n + 1), *entries) + bytes(body)


def _write(p, data):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    return p


@pytest.mark.parametrize("kw", [dict(), dict(align=2), dict(block=8192), dict(block=8192, align=4),
                                dict(version=0, header_size=0), dict(version=2), dict(stored={0, 7, 16})])
def test_cso_round_trip(tmp_path, kw):
    iso = make_iso(sectors=41)                     # 41 sectors: an 8 KB-block file ends on a partial block
    src = _write(str(tmp_path / "a.cso"), make_cso(iso, **kw))
    dst = str(tmp_path / "a.iso")
    seen = []
    sha = rommlib.cso_decode(src, dst, pause=lambda: seen.append("p"), progress=seen.append)
    assert open(dst, "rb").read() == iso
    assert sha == hashlib.sha1(iso).hexdigest()
    assert "p" in seen and seen[-1] == 1.0
    assert os.path.getsize(src) < len(iso)          # it really was compressed
    info = rommlib.cso_info(src)
    assert info["total"] == len(iso)


def test_cso_last_block_padded_to_full_size(tmp_path):
    """Some encoders compress the last block padded with zeros to the block size."""
    iso = make_iso(sectors=41)
    padded = iso + b"\0" * (8192 * 6 - len(iso))
    data = bytearray(make_cso(padded, block=8192))
    struct.pack_into("<Q", data, 8, len(iso))      # header says the real size
    src = _write(str(tmp_path / "a.cso"), bytes(data))
    rommlib.cso_decode(src, str(tmp_path / "a.iso"))
    assert open(str(tmp_path / "a.iso"), "rb").read() == iso


def _refused(tmp_path, data, match):
    src = _write(str(tmp_path / "bad.cso"), data)
    with pytest.raises(rommlib.CsoError, match=match):
        rommlib.cso_decode(src, str(tmp_path / "bad.iso"))
    return src


def test_cso_v2_with_lz4_and_zso_are_refused(tmp_path):
    iso = make_iso()
    _refused(tmp_path, make_cso(iso, version=2, lz4={3}), "needs LZ4")
    zso = b"ZISO" + make_cso(iso)[4:]
    _refused(tmp_path, zso, "needs LZ4")
    with pytest.raises(rommlib.CsoError, match="needs LZ4"):
        rommlib.cso_info(str(tmp_path / "bad.cso"))


def test_cso_damage_is_refused(tmp_path):
    iso = make_iso()
    good = make_cso(iso)
    _refused(tmp_path, good[:-100], "cut short")                         # truncated download
    _refused(tmp_path, good[:0x18 + 8], "cut short")                     # not even the index
    _refused(tmp_path, b"DAX\0" + good[4:], "not a CSO")
    bad = bytearray(good)                                                # index going backwards
    e5 = struct.unpack_from("<I", bad, 0x18 + 4 * 5)[0]
    struct.pack_into("<I", bad, 0x18 + 4 * 6, e5 - 1)
    _refused(tmp_path, bytes(bad), "damaged block index")
    bad = bytearray(good)                                                # two entries equal: empty block
    struct.pack_into("<I", bad, 0x18 + 4 * 6, e5)
    _refused(tmp_path, bytes(bad), "damaged block index")
    bad = bytearray(good)                                                # garbage in a deflate block
    start = struct.unpack_from("<I", bad, 0x18)[0] & 0x7FFFFFFF
    bad[start:start + 8] = b"\xff" * 8
    _refused(tmp_path, bytes(bad), "damaged block 0")
    bad = bytearray(good)
    struct.pack_into("<Q", bad, 8, len(iso) + 100)                       # not whole sectors
    _refused(tmp_path, bytes(bad), "2048-byte sectors")
    bad = bytearray(good)
    struct.pack_into("<I", bad, 16, 3000)                                # block size not a power of two
    _refused(tmp_path, bytes(bad), "block size")
    bad = bytearray(good)
    bad[20] = 3                                                          # unknown version
    _refused(tmp_path, bytes(bad), "version 3")


def test_cso_block_of_the_wrong_length_is_refused(tmp_path):
    """A block that decodes to less than the block size would shift every
    later byte: never written out as if it were fine."""
    iso = make_iso()
    good = bytearray(make_cso(iso))
    short = zlib.compressobj(9, zlib.DEFLATED, -15)
    packed = short.compress(b"\0" * 1000) + short.flush()        # block 0 is zeros: 1000 instead of 2048
    s0 = struct.unpack_from("<I", good, 0x18)[0]
    s1 = struct.unpack_from("<I", good, 0x18 + 4)[0] & 0x7FFFFFFF
    assert len(packed) <= s1 - s0
    good[s0:s1] = packed + b"\0" * (s1 - s0 - len(packed))        # trailing junk past the deflate end too
    _refused(tmp_path, bytes(good), "damaged block 0")


def test_cso_stored_block_too_short_is_refused(tmp_path):
    """A block flagged as stored whose span is shorter than the block."""
    data = bytearray(make_cso(make_iso()))
    e0 = struct.unpack_from("<I", data, 0x18)[0]
    struct.pack_into("<I", data, 0x18, e0 | 0x80000000)          # block 0 is deflate data, a few bytes
    _refused(tmp_path, bytes(data), "damaged block 0 of 48 \\(stored")


# ---------------------------------------------------------------- the worker

FAKE_CHDMAN = r'''#!%s
import hashlib, os, sys
a = sys.argv[1:]
log = os.environ.get("FAKE_LOG")
if log:
    open(log, "a").write(" ".join(a) + "\n")
if a[0] == "verify":
    sys.exit(0)
if a[0] == "info":
    data = open(a[a.index("-i") + 1], "rb").read()[4:]
    sha = hashlib.sha1(data).hexdigest()
    if os.environ.get("FAKE_SHA") == "wrong":
        sha = "0" * 40
    print("Logical size: %%d bytes\nSHA1:         %%s\nData SHA1:    %%s\nMetadata:     Tag='DVD '" %% (
        len(data), "f" * 40, sha))
    sys.exit(0)
src, out = a[a.index("-i") + 1], a[a.index("-o") + 1]
open(out, "wb").write(b"CHD:" + open(src, "rb").read())
'''


@pytest.fixture
def chd(isolated, tmp_path, monkeypatch):
    b = tmp_path / "bin"
    b.mkdir()
    exe = b / "chdman"
    exe.write_text(FAKE_CHDMAN % sys.executable)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", "%s:%s" % (b, os.environ["PATH"]))
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "chdman.log"))
    import rb_chd
    monkeypatch.setattr(rb_chd, "running_game", lambda: "")
    monkeypatch.setattr(rb_chd.time, "sleep", lambda s: None)
    monkeypatch.setattr(rommlib, "keep_free_bytes", lambda cfg: 0)
    return rb_chd


def _task(src):
    return {"system": "psp", "mode": "cso", "sources": [src], "roots": [src]}


def test_worker_turns_a_cso_into_chd(chd, tmp_path):
    iso = make_iso()
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psp")
    src = _write(os.path.join(sysdir, "Lorem Homebrew (World).cso"), make_cso(iso, align=1))
    assert chd.convert(_task(src)) is True
    out = os.path.join(sysdir, "Lorem Homebrew (World).chd")
    assert open(out, "rb").read() == b"CHD:" + iso          # chdman was given the decoded ISO
    assert sorted(os.listdir(sysdir)) == ["Lorem Homebrew (World).chd"]     # .cso and temp ISO gone
    calls = open(str(tmp_path / "chdman.log")).read().splitlines()
    create = next(c for c in calls if c.startswith("createdvd"))
    assert "-hs 2048" in create and "-c %s" % chd.DVD_CODECS in create
    assert any(c.startswith("verify") for c in calls) and any(c.startswith("info") for c in calls)
    q = open(rommlib.GAMELIST_QUEUE).read()
    assert "Lorem Homebrew (World).chd" in q


def test_worker_keeps_the_cso_when_the_chd_holds_something_else(chd, monkeypatch):
    monkeypatch.setenv("FAKE_SHA", "wrong")
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psp")
    data = make_cso(make_iso())
    src = _write(os.path.join(sysdir, "Ipsum (World).cso"), data)
    assert chd.convert(_task(src)) is False
    assert os.listdir(sysdir) == ["Ipsum (World).cso"]       # no CHD, no .part, no temp ISO
    assert open(src, "rb").read() == data
    assert "SHA-1" in chd._state["last"]


def test_worker_refuses_damaged_cso_and_non_iso(chd):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psp")
    cut = _write(os.path.join(sysdir, "Cut (World).cso"), make_cso(make_iso())[:-50])
    assert chd.convert(_task(cut)) is False
    assert "cut short" in chd._state["last"]
    noiso = bytearray(make_iso())
    noiso[0x8001:0x8006] = b"XXXXX"
    src = _write(os.path.join(sysdir, "Dolor (World).cso"), make_cso(bytes(noiso)))
    assert chd.convert(_task(src)) is False
    assert "ISO9660" in chd._state["last"]
    assert sorted(os.listdir(sysdir)) == ["Cut (World).cso", "Dolor (World).cso"]


def test_worker_waits_for_room_for_the_decoded_iso(chd, monkeypatch):
    """The decoded ISO is bigger than the .cso: the space check counts it."""
    iso = make_iso(sectors=200)
    src = _write(os.path.join(rommlib.ROMS_ROOT, "psp", "Sit (World).cso"), make_cso(iso))
    size = os.path.getsize(src)
    assert len(iso) > 2 * size
    monkeypatch.setattr(rommlib, "free_bytes", lambda p: len(iso) + size - 1)
    assert chd.convert(_task(src)) is None                   # WAIT, try later
    monkeypatch.setattr(rommlib, "free_bytes", lambda p: len(iso) + size)
    assert chd.convert(_task(src)) is True


def test_temp_iso_is_an_unfinished_conversion_leftover(isolated):
    p = _write(os.path.join(rommlib.ROMS_ROOT, "psp", "Amet (World)" + rommlib.CSO_TEMP_SUFFIX), b"x")
    assert [i["kind"] for i in rommlib.find_leftovers(chd_busy=False)] == ["Unfinished conversion"]
    assert rommlib.find_leftovers(chd_busy=True) == []
    assert p


# ---------------------------------------------------------------- the table's other users

def test_download_queues_cso_and_prompt_says_chd(isolated):
    cfg = dict(rommlib.DEFAULT_CONFIG)
    d = rommlib.Downloader(None, rommlib.Library(), cfg, persist=False)
    d.es = {"psp": {".iso", ".cso", ".pbp", ".chd"}}
    src = _write(os.path.join(rommlib.ROMS_ROOT, "psp", "Lorem (World).cso"), b"x")
    j = rommlib.Job({"id": 7}, "psp")
    d._maybe_chd(j, "psp", [src], [src])
    t = json.load(open(os.path.join(rommlib.CHD_QUEUE_DIR, j.chd_task)))
    assert t["mode"] == "cso" and t["sources"] == [src]
    det = {"files": [{"file_name": "Lorem (World).zip", "file_size_bytes": 1,
                      "archive_members": [{"name": "Lorem (World).cso", "size": 300 << 20}]}]}
    assert rommlib.disc_layout(det, "psp", cfg)["convert"] == "CHD"
    assert rommlib.disc_layout(det, "psp", dict(cfg, chd=False))["convert"] is None
    assert rommlib.disc_layout(det, "psp", cfg)["label"] == "CSO"
    assert rommlib.conversion_for("ps2", "X.cso", cfg) is None          # PSP only
    cfg_off = dict(cfg, chd=False)
    j2 = rommlib.Job({"id": 8}, "psp")
    rommlib.Downloader(None, rommlib.Library(), cfg_off, persist=False)._maybe_chd(j2, "psp", [src], [src])
    assert j2.chd_task is None


def test_planner_lists_cso_games(isolated, monkeypatch):
    import rb_chd_plan
    monkeypatch.setattr(rommlib, "load_config", lambda: dict(rommlib.DEFAULT_CONFIG))
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psp")
    good = _write(os.path.join(sysdir, "Lorem (World).cso"), make_cso(make_iso()))
    _write(os.path.join(sysdir, "Ipsum (World).cso"), make_cso(make_iso(), version=2, lz4={2}))
    skipped = []
    cands = rb_chd_plan.candidates(["psp"], skipped=skipped)
    assert [(c["files"], c["mode"], c["label"], c["enabled"]) for c in cands] == [([good], "cso", "CHD", True)]
    assert len(skipped) == 1 and "LZ4" in skipped[0][2]
    assert rb_chd_plan.queue(cands) == 1
    t = json.load(open(os.path.join(rommlib.CHD_QUEUE_DIR, os.listdir(rommlib.CHD_QUEUE_DIR)[0])))
    assert t["mode"] == "cso" and t["sources"] == [good]
