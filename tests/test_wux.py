"""Wii U to .wux and Decrypt Wii U discs together, with the real wuatool from
tools/wiiu-tools/out (skipped where it isn't built): wuatool's .wux (the
reference layout, read by our reader and Cemu's), the worker's checked
compression, which converter takes which disc (settings x keys) in the worker,
the download post-processing and the planner, and Check games on a .wux.
Synthetic discs (mkdisc.py) and made-up keys only."""
import os
import subprocess
import hashlib
import zlib

import pytest

import rommlib
from test_wua import KEYS, mkdisc, tool, _cfg, _idle  # noqa: F401  (tool: the fixture)

NOKEY = dict(KEYS, disc=bytes(16))                  # a disc whose key isn't in keys.txt


def _disc(name, keys=KEYS, wux=False):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "wiiu")
    os.makedirs(sysdir, exist_ok=True)
    path = os.path.join(sysdir, name)
    mkdisc.make_disc(path, keys, wux=wux)
    return path


def _run(rb_chd, path, mode):
    return rb_chd.convert({"system": "wiiu", "mode": mode, "sources": [path], "roots": [path]})


def test_wuatool_writes_the_reference_layout_that_both_readers_read(tool, tmp_path):
    disc = str(tmp_path / "d.wud")
    mkdisc.make_disc(disc, KEYS)
    raw = open(disc, "rb").read()
    out = str(tmp_path / "d.wux")
    subprocess.run([rommlib.wuatool_path(), "wux", disc, out], check=True, capture_output=True)
    data = open(out, "rb").read()
    assert data == mkdisc.to_wux(raw)                               # header, index, unique sectors
    assert data[:4] == b"WUX0" and int.from_bytes(data[0x10:0x18], "little") == len(raw)
    assert len(data) < len(raw)                                     # the empty sectors are stored once
    assert rommlib.wux_layout(out) == len(raw)
    rommlib.wiiu_check(out)                                         # our reader: the disc key is found
    a, b = str(tmp_path / "a.wua"), str(tmp_path / "b.wua")         # Cemu's reader: the same game
    keys = rommlib.cemu_keys_path()
    subprocess.run([rommlib.wuatool_path(), "pack", "-k", keys, disc, a], check=True, capture_output=True)
    subprocess.run([rommlib.wuatool_path(), "pack", "-k", keys, out, b], check=True, capture_output=True)
    assert open(a, "rb").read() == open(b, "rb").read()


def test_worker_compresses_a_wud_to_wux_and_checks_every_byte(tool, monkeypatch):
    rb_chd = _idle(monkeypatch)
    _cfg(monkeypatch)                                               # defaults: .wux on, decrypt off
    disc = _disc("Lorem Kart (USA).wud", NOKEY)                     # no key needed
    raw = open(disc, "rb").read()
    assert _run(rb_chd, disc, "wux") is True
    out = disc[:-4] + ".wux"
    assert not os.path.exists(disc) and not os.path.exists(out + ".part")
    assert open(out, "rb").read() == mkdisc.to_wux(raw)
    log = open(rommlib.LOG_PATH).read()
    assert "chd: OK Lorem Kart (USA).wud -> Lorem Kart (USA).wux" in log
    assert "CRC32 %08x" % zlib.crc32(raw) in log and hashlib.md5(raw).hexdigest() in log
    assert "isn't a whole Wii U disc's size (%s, not 25,025,314,816): may be cut short" % "{:,}".format(len(raw)) in log


def test_worker_wux_odd_sizes_and_refusals(tool, monkeypatch):
    rb_chd = _idle(monkeypatch)
    _cfg(monkeypatch)
    odd = _disc("Odd.wud")                                          # cut mid-sector: still exact
    with open(odd, "ab") as f:
        f.write(b"\x5a" * 1234)
    raw = open(odd, "rb").read()
    assert _run(rb_chd, odd, "wux") is True
    back = odd + ".back"
    subprocess.run([rommlib.wuatool_path(), "unwux", odd[:-4] + ".wux", back], check=True, capture_output=True)
    assert open(back, "rb").read() == raw
    short = _disc("Short.wud")                                      # cut before the disc header
    with open(short, "r+b") as f:
        f.truncate(0x8000)
    junk = os.path.join(os.path.dirname(odd), "Junk.wud")
    open(junk, "wb").write(b"\0" * 0x40000)
    for p in (short, junk):
        assert _run(rb_chd, p, "wux") is False and os.path.exists(p)
        assert not os.path.exists(p[:-4] + ".wux")
    assert "not a Wii U disc image" in open(rommlib.LOG_PATH).read()
    _cfg(monkeypatch, wux=False)                                    # off: nothing happens
    off = _disc("Off.wud")
    assert _run(rb_chd, off, "wux") is False and os.path.exists(off)


@pytest.mark.parametrize("wua, keys, wux, queued, made", [
    (True, KEYS, True, "wua", ".wua"),         # decrypt on, key here: .wua
    (True, NOKEY, True, "wua", ".wux"),        # decrypt on, no key: still .wux
    (False, KEYS, True, "wua", ".wux"),        # decrypt off: .wux
    (True, KEYS, True, "wux", ".wua"),         # queued as .wux, then decrypt turned on: .wua
    (True, NOKEY, False, "wua", None),         # no key and .wux off: stays as it is
    (False, KEYS, False, "wux", None),         # both off
])
def test_worker_settles_wua_or_wux_when_it_runs(tool, monkeypatch, wua, keys, wux, queued, made):
    rb_chd = _idle(monkeypatch)
    _cfg(monkeypatch, wua=wua, wux=wux)
    disc = _disc("Game.wud", keys)
    assert _run(rb_chd, disc, queued) is (made is not None)
    left = sorted(os.listdir(os.path.dirname(disc)))
    assert left == (["Game" + made] if made else ["Game.wud"])


def test_conversion_for_follows_the_switches_and_the_keys(tool):
    disc, other, wux = _disc("K.wud"), _disc("N.wud", NOKEY), _disc("X.wux", wux=True)
    cfg = dict(rommlib.DEFAULT_CONFIG)
    mode = lambda p, **kw: getattr(rommlib.conversion_for("wiiu", p, dict(cfg, **kw)), "mode", None)  # noqa: E731
    assert (mode(disc), mode(other), mode(wux), mode(".wud"), mode(".wux")) == ("wux", "wux", None, "wux", None)
    assert (mode(disc, wua=True), mode(other, wua=True), mode(wux, wua=True)) == ("wua", "wux", "wua")
    assert (mode(".wud", wua=True), mode(".wux", wua=True)) == ("wua", "wua")      # no file yet: decrypt
    assert (mode(disc, wux=False), mode(other, wua=True, wux=False)) == (None, None)
    assert rommlib.conversion_for("wiiu", disc, cfg, enabled=False).mode == "wua"
    assert rommlib.CONVERTER["wux"].space_factor == 1 and rommlib.CONVERTER["wux"].space is None


def _plan(**kw):
    import rb_chd_plan
    skipped = []
    cands = rb_chd_plan.candidates(["wiiu"], dict(rommlib.DEFAULT_CONFIG, **kw), skipped)
    return ({os.path.basename(c["files"][0]): (c["label"], c["enabled"]) for c in cands},
            {os.path.basename(f): why for _, f, why in skipped})


def test_planner_labels_each_disc_and_says_why_one_cant_be_decrypted(tool):
    _disc("A.wud"), _disc("B.wud", NOKEY), _disc("C.wux", wux=True), _disc("D.wux", NOKEY, wux=True)
    _disc("E.wud")
    subprocess.run([rommlib.wuatool_path(), "wux", _disc("F.wud"), os.path.join(rommlib.ROMS_ROOT, "wiiu", "F.wux")],
                   check=True, capture_output=True)                 # F.wux is the .wux of F.wud: done
    open(os.path.join(rommlib.ROMS_ROOT, "wiiu", "E.wua"), "wb").write(b"made before")
    # defaults: every .wud to .wux; a .wux with its key needs Decrypt; one without has nothing to offer
    cands, skipped = _plan()
    assert cands == {"A.wud": ("WUX", True), "B.wud": ("WUX", True), "C.wux": ("WUA", False),
                     "E.wud": ("WUX", True), "F.wux": ("WUA", False)} and skipped == {}
    # Decrypt on: discs with their key to .wua, the rest of the .wud to .wux, with the reason
    cands, skipped = _plan(wua=True)
    assert cands == {"A.wud": ("WUA", True), "B.wud": ("WUX", True), "C.wux": ("WUA", True),
                     "F.wux": ("WUA", True), "F.wud": ("WUA", True)}
    assert skipped["B.wud"].startswith("can't decrypt it: no disc key for it") and "offered as WUX" in skipped["B.wud"]
    assert skipped["D.wux"].startswith("no disc key for it") and set(skipped) == {"B.wud", "D.wux"}
    # both off: .wud offered as .wux, needing its switch
    cands, _ = _plan(wux=False)
    assert cands["A.wud"] == ("WUX", False)


def test_check_games_on_a_wux(tool, tmp_path, monkeypatch):
    disc = str(tmp_path / "G.wud")
    mkdisc.make_disc(disc, KEYS)
    raw = open(disc, "rb").read()
    out = str(tmp_path / "G.wux")
    subprocess.run([rommlib.wuatool_path(), "wux", disc, out], check=True, capture_output=True)
    orig = {"name": "G.wud", "size": len(raw), "crc": "%08x" % zlib.crc32(raw), "sha1": hashlib.sha1(raw).hexdigest()}
    assert rommlib.output_converter(out).mode == "wux"
    assert rommlib.wux_audit(out, orig, False) is None and rommlib.wux_audit(out, orig, True) is None
    assert rommlib.wux_audit(out, None, True) is None
    assert rommlib.wux_audit(out, dict(orig, crc="00000000"), True) == ("Checksum doesn't match RomM's copy", "differs")
    assert rommlib.wux_audit(out, dict(orig, size=len(raw) + 1), False)[1] == "differs"
    data = open(out, "rb").read()
    bad = str(tmp_path / "bad.wux")
    open(bad, "wb").write(data[:-0x8000])                           # cut short: a sector is missing
    assert rommlib.wux_audit(bad, orig, False)[1] == "damaged"
    open(bad, "wb").write(data[:0x20] + (1 << 30).to_bytes(4, "little") + data[0x24:])
    problem, severity = rommlib.wux_audit(bad, orig, False)
    assert severity == "damaged" and "points past the end" in problem
    open(bad, "wb").write(data[:-5] + bytes([data[-5] ^ 1]) + data[-4:])     # a stored byte changed
    assert rommlib.wux_audit(bad, orig, False) is None                       # quick: the layout only
    assert rommlib.wux_audit(bad, orig, True) == ("Checksum doesn't match RomM's copy", "differs")
    monkeypatch.setattr(rommlib, "wuatool_path", lambda: None)
    with pytest.raises(rommlib.NotVerified):
        rommlib.wux_audit(out, orig, True)
