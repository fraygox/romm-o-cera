#!/usr/bin/env python3
"""wuatool's self-test: synthetic discs (mkdisc.py) with made-up keys.

  selftest.py [--keep DIR] [runner...] <wuatool>

runner: a command to run wuatool under (qemu-aarch64-static -L ...). The keys
are derived from fixed strings, so the same wuatool code makes the same .wua on
every CPU: --keep DIR leaves the discs and archives there for comparing. None
of them is a real key. Exits non-zero on the first thing that isn't right."""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mkdisc  # noqa: E402

args = sys.argv[1:]
keep = None
if args[:1] == ["--keep"]:
    keep, args = args[1], args[2:]
CMD = args
KEYS = {k: hashlib.sha256(b"wuatool selftest " + k.encode()).digest()[:16] for k in ("disc", "title", "common")}


def run(*a):
    p = subprocess.run(CMD + list(a), capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def expect(rc_want, *a):
    rc, out = run(*a)
    if rc != rc_want:
        sys.exit("selftest: wuatool %s -> %d, wanted %d\n%s" % (" ".join(a), rc, rc_want, out[-2000:]))
    return out


def unpack_check(wua, game):
    """Every file Cemu would read is in the archive, byte for byte (read back with wuatool check)."""
    out = expect(0, "check", wua)
    top = "%016x_v%d" % (mkdisc.TITLE_ID, mkdisc.TITLE_VERSION)
    if "title " + top not in out or "checked %d files, %d bytes" % (len(game), sum(map(len, game.values()))) not in out:
        sys.exit("selftest: check output unexpected:\n" + out)
    for path, data in game.items():
        p = subprocess.run(CMD + ["cat", wua, top + "/" + path], capture_output=True)
        if p.returncode != 0 or p.stdout != data:
            sys.exit("selftest: %s in the archive isn't the disc's (rc %d, %d of %d bytes)" % (
                path, p.returncode, len(p.stdout), len(data)))


d = keep or tempfile.mkdtemp(prefix="wuatool-selftest-")
os.makedirs(d, exist_ok=True)
try:
    keys = os.path.join(d, "keys.txt")
    with open(keys, "w") as f:
        f.write("# made-up keys for the self-test\n%s # another\n%s\n" % (os.urandom(16).hex(), KEYS["disc"].hex()))
    common = os.path.join(d, "common.txt")
    with open(common, "w") as f:
        f.write(KEYS["common"].hex() + "\n")
    outs = {}
    for ext in (".wud", ".wux"):
        disc = os.path.join(d, "disc" + ext)
        game = mkdisc.make_disc(disc, KEYS, wux=ext == ".wux")
        wua = os.path.join(d, "disc%s.wua" % ext)
        out = expect(0, "pack", "-k", keys, "-T", common, disc, wua)
        assert "packed %d files" % len(game) in out, out
        expect(0, "verify", "-k", keys, "-T", common, disc, wua)
        out = expect(0, "probe", "-k", keys, "-T", common, disc)
        assert "files %d" % len(game) in out, out
        unpack_check(wua, game)
        outs[ext] = open(wua, "rb").read()
    if outs[".wud"] != outs[".wux"]:
        sys.exit("selftest: .wud and .wux of the same disc made different archives")
    disc, wua = os.path.join(d, "disc.wud"), os.path.join(d, "disc.wud.wua")
    # .wud -> .wux (no keys) and back, read through Cemu's own reader
    raw = open(disc, "rb").read()
    wux = os.path.join(d, "ours.wux")
    out = expect(0, "wux", disc, wux)
    if open(wux, "rb").read() != mkdisc.to_wux(raw):
        sys.exit("selftest: wuatool's .wux isn't the reference layout (mkdisc.to_wux)")
    want = "size %d\ncrc32 %08x\nmd5 %s\nsha1 %s\n" % (len(raw), zlib.crc32(raw), hashlib.md5(raw).hexdigest(),
                                                       hashlib.sha1(raw).hexdigest())
    for a in (("hash", disc), ("hash", wux), ("compare", disc, wux), ("compare", wux, disc)):
        if "".join(l + "\n" for l in expect(0, *a).splitlines() if not l.endswith("%")) != want:
            sys.exit("selftest: %s printed other hashes" % " ".join(a))
    back = os.path.join(d, "back.wud")
    expect(0, "unwux", wux, back)
    if open(back, "rb").read() != raw:
        sys.exit("selftest: unwux didn't give the image back")
    expect(0, "pack", "-k", keys, "-T", common, wux, os.path.join(d, "ours.wux.wua"))
    if open(os.path.join(d, "ours.wux.wua"), "rb").read() != outs[".wud"]:
        sys.exit("selftest: our .wux packed to another .wua")
    odd = os.path.join(d, "odd.wud")                                  # a size that isn't whole sectors
    with open(odd, "wb") as f:
        f.write(raw + raw[:mkdisc.SECTOR] + b"\xab" * 1000)
    expect(0, "wux", odd, odd + ".wux")
    expect(0, "compare", odd, odd + ".wux")
    expect(0, "unwux", odd + ".wux", odd + ".back")
    if open(odd + ".back", "rb").read() != open(odd, "rb").read():
        sys.exit("selftest: an odd-sized image didn't round-trip")
    print("selftest: wux/unwux/compare/hash ok (%d-byte .wux of a %d-byte image, sha256 %s)" % (
        os.path.getsize(wux), len(raw), hashlib.sha256(open(wux, "rb").read()).hexdigest()))
    print("selftest: pack/verify/check/probe ok (%d-byte .wua, sha256 %s)" % (
        len(outs[".wud"]), hashlib.sha256(outs[".wud"]).hexdigest()))

    # a disc that carries its update and DLC (and a console-firmware partition): all
    # three titles go into one .wua, each in its own <title id>_v<version> folder
    multi = os.path.join(d, "multi.wud")
    titles = {}
    mkdisc.make_disc(multi, KEYS, extra=[(mkdisc.UPDATE_ID, 48, 3), (mkdisc.DLC_ID, 16, 4)], up=True, titles=titles)
    mwua = os.path.join(d, "multi.wua")
    out = expect(0, "pack", "-k", keys, "-T", common, multi, mwua)
    if "packed %d files" % sum(map(len, titles.values())) not in out:
        sys.exit("selftest: multi-title pack output unexpected:\n" + out)
    out = expect(0, "probe", "-k", keys, "-T", common, multi)
    if [l[6:] for l in out.splitlines() if l.startswith("title ")] != list(titles):
        sys.exit("selftest: probe didn't list the game, update and DLC in order:\n" + out)
    out = expect(0, "verify", "-k", keys, "-T", common, multi, mwua)
    assert "3 titles" in out, out
    tops = [l[6:] for l in expect(0, "check", mwua).splitlines() if l.startswith("title ")]
    if sorted(tops) != sorted(titles):
        sys.exit("selftest: the multi-title .wua holds %s" % tops)
    for top, files in titles.items():
        for path, data in files.items():
            p = subprocess.run(CMD + ["cat", mwua, top + "/" + path], capture_output=True)
            if p.returncode != 0 or p.stdout != data:
                sys.exit("selftest: %s/%s in the multi-title .wua isn't the disc's" % (top, path))
    expect(7, "verify", "-k", keys, "-T", common, multi, wua)          # the game alone: titles missing
    expect(7, "verify", "-k", keys, "-T", common, disc, mwua)          # titles the disc doesn't have
    print("selftest: multi-title ok (game, update, DLC; firmware partition left out; sha256 %s)" % (
        hashlib.sha256(open(mwua, "rb").read()).hexdigest()))

    # refusals
    expect(3, "probe", "-k", common, "-T", common, disc)              # no disc key among the keys
    expect(2, "probe", "-k", keys, disc)                              # Cemu's common key: not this made-up disc's
    junk = os.path.join(d, "junk.wud")
    with open(junk, "wb") as f:
        f.write(b"\0" * (mkdisc.SECTOR * 8))
    expect(2, "probe", "-k", keys, "-T", common, junk)
    bad = os.path.join(d, "bad.wud")                                  # one byte flipped in a hashed block
    shutil.copy(disc, bad)
    gm, data = mkdisc.hashed_cluster_offset(bad)
    mkdisc.flip(bad, gm + 8 * mkdisc.SECTOR + 5 * mkdisc.BLOCK + 0x1234)   # inside content/big.bin
    rc, out = run("pack", "-k", keys, "-T", common, bad, os.path.join(d, "bad.wua"))
    if rc not in (5, 2) or os.path.exists(os.path.join(d, "bad.wua")):
        sys.exit("selftest: a damaged disc packed (rc %d)\n%s" % (rc, out[-2000:]))
    expect(5 if rc == 5 else 2, "verify", "-k", keys, "-T", common, bad, wua)
    cut = os.path.join(d, "cut.wud")                                  # an interrupted copy
    with open(disc, "rb") as f, open(cut, "wb") as g:
        g.write(f.read(os.path.getsize(disc) - 3 * mkdisc.SECTOR))
    rc, out = run("pack", "-k", keys, "-T", common, cut, os.path.join(d, "cut.wua"))
    if rc == 0:
        sys.exit("selftest: a cut-short disc packed\n" + out[-2000:])
    other = os.path.join(d, "other.wud")                              # same title, different files
    mkdisc.make_disc(other, KEYS, seed=2)
    expect(7, "verify", "-k", keys, "-T", common, other, wua)
    short = os.path.join(d, "short.wua")
    with open(short, "wb") as f:
        f.write(outs[".wud"][:-100])
    expect(8, "check", short)
    expect(8, "verify", "-k", keys, "-T", common, disc, short)
    tamper = os.path.join(d, "tamper.wua")
    with open(tamper, "wb") as f:
        f.write(outs[".wud"])
    mkdisc.flip(tamper, 100)
    expect(8, "check", tamper)
    expect(2, "wux", wux, os.path.join(d, "again.wux"))              # a .wux already
    expect(2, "wux", junk, os.path.join(d, "junk.wux"))               # not a Wii U disc
    expect(7, "compare", other, wux)                                  # another disc
    cutwux = os.path.join(d, "cut.wux")
    with open(cutwux, "wb") as f:
        f.write(open(wux, "rb").read()[:-mkdisc.SECTOR])
    expect(8, "hash", cutwux)
    expect(8, "compare", disc, cutwux)
    badidx = os.path.join(d, "badidx.wux")                            # an index past the stored sectors
    data = bytearray(open(wux, "rb").read())
    data[0x20:0x24] = (0x7FFFFFFF).to_bytes(4, "little")
    open(badidx, "wb").write(bytes(data))
    expect(8, "unwux", badidx, os.path.join(d, "badidx.wud"))
    if os.path.exists(os.path.join(d, "badidx.wud")):
        sys.exit("selftest: unwux left a file from a damaged .wux")
    flipped = os.path.join(d, "flipped.wux")                          # a stored byte changed
    open(flipped, "wb").write(open(wux, "rb").read())
    mkdisc.flip(flipped, os.path.getsize(flipped) - 5)
    expect(7, "compare", disc, flipped)
    print("selftest: refusals ok (no disc key, wrong common key, junk, damaged, cut short, different disc, "
          "damaged archive, .wux of a .wux or junk, damaged .wux)")
finally:
    if not keep:
        shutil.rmtree(d, ignore_errors=True)
