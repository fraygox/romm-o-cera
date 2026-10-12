"""Check games on what this box converts: RVZ, squashfs, .ps3 game folders
(and .zcci/.zar through the same converter hooks). Each is checked against
RomM's copy of what it was made from, quick or full; a check that needs a
tool the box lacks counts as not verified."""
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import zlib

import pytest

import rommlib
from test_ps3 import FILES, make_disc

ISO = 6 << 20


class Api:
    """RomM listing one game per (fs_name, files) on platform 1."""

    def __init__(self, games):
        self.games = [{"id": i, "name": n, "fs_name": n, "fs_name_no_ext": rommlib.stem_of(n), "platform_id": 1,
                       "files": files} for i, (n, files) in enumerate(games)]

    def roms_all(self, keep=None, **kw):
        return [r for r in self.games if keep is None or keep(r)]

    def rom(self, rid):
        return dict(self.games[rid])


def _iso_rec(name, size, crc="", sha1=""):
    return {"file_name": name, "file_size_bytes": size, "crc_hash": crc, "sha1_hash": sha1, "category": None}


def _zip_rec(zname, member, size, crc=""):
    return {"file_name": zname, "file_size_bytes": size // 2, "category": None,
            "archive_members": [{"name": member, "size": size, "crc_hash": crc}]}


def _put(system, name, data=b""):
    d = os.path.join(rommlib.ROMS_ROOT, system)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name)
    with open(p, "wb") as f:
        f.write(data)
    return p


def _check(system, games, deep=False):
    findings, stats = rommlib.audit(Api(games), {system: [1]}, deep=deep)
    return [(os.path.basename(f["path"]), f["severity"], f["problem"]) for f in findings], stats


# ---------------------------------------------------------------- the table

def test_the_converters_say_how_their_output_is_checked():
    of = rommlib.output_converter
    assert of("/r/wii/A.rvz").mode == "rvz" and of("/r/xbox/A.iso.squashfs").mode == "squashfs"
    assert of("/r/ps3/A (USA).ps3").mode == "ps3" and of("A.zcci").mode == of("A.zcia").mode == "z3ds"
    assert of("A.zar").mode == "zar"
    assert of("A.chd") is None and of("A.iso") is None           # CHDs have their own check; sources none
    for c in rommlib.CONVERTERS:
        assert c.chd or c.audit, c.mode                            # every output has a check


# ---------------------------------------------------------------- RVZ

def make_rvz(path, iso_size, body=b"lorem ipsum " * 100, cut=0):
    """An RVZ whose headers Dolphin accepts (only the header fields matter here)."""
    h2 = b"\0" * 0xDC
    total = 0x48 + len(h2) + len(body)
    h1 = b"RVZ\x01" + struct.pack(">III", 0x00030000, 0x00030000, len(h2)) + hashlib.sha1(h2).digest() \
        + struct.pack(">QQ", iso_size, total)
    data = (h1 + hashlib.sha1(h1).digest() + h2 + body)[:total - cut]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


def test_rvz_quick_check_reads_its_header(isolated):
    d = os.path.join(rommlib.ROMS_ROOT, "wii")
    make_rvz(os.path.join(d, "Whole (USA).rvz"), ISO)
    make_rvz(os.path.join(d, "Short (USA).rvz"), ISO, cut=100)
    make_rvz(os.path.join(d, "Other (USA).rvz"), ISO - (1 << 20))
    make_rvz(os.path.join(d, "Scrubbed (USA).rvz"), 4700372992)
    _put("wii", "Junk (USA).rvz", b"RVZ\x01" + b"\0" * 200)
    got, stats = _check("wii", [("Whole (USA).iso", [_iso_rec("Whole (USA).iso", ISO)]),
                                ("Short (USA).zip", [_zip_rec("Short (USA).zip", "Short (USA).iso", ISO)]),
                                ("Other (USA).iso", [_iso_rec("Other (USA).iso", ISO)]),
                                ("Scrubbed (USA).wbfs", [_iso_rec("Scrubbed (USA).wbfs", ISO)]),
                                ("Junk (USA).iso", [_iso_rec("Junk (USA).iso", ISO)])])
    assert stats == {"checked": 5, "matched": 5, "unmatched": 0}
    assert sorted(got) == [
        ("Junk (USA).rvz", "damaged", "Not a readable RVZ (damaged header)"),
        ("Other (USA).rvz", "differs", "Packed from a different file than RomM's (5.0 MB vs 6.0 MB)"),
        ("Short (USA).rvz", "damaged", "Compressed file cut short")]    # a .wbfs's size isn't the disc's


def _fake_tool(tmp_path, name, script):
    p = tmp_path / name
    p.write_text("#!%s\n%s" % (sys.executable, script))
    p.chmod(0o755)
    return str(p)


def _fake_dolphin(tmp_path, monkeypatch, out, rc=0):
    exe = _fake_tool(tmp_path, "dolphin-tool", "import sys\nsys.stdout.write(%r)\nsys.exit(%d)\n" % (out, rc))
    monkeypatch.setattr(rommlib, "dolphin_tool_path", lambda: exe)


def test_rvz_full_check_hashes_the_disc_with_dolphin_tool(isolated, tmp_path, monkeypatch):
    make_rvz(os.path.join(rommlib.ROMS_ROOT, "gamecube", "Lorem (USA).rvz"), ISO)
    games = [("Lorem (USA).iso", [_iso_rec("Lorem (USA).iso", ISO, "1271ADCD", "3d" * 20)])]
    monkeypatch.setattr(rommlib, "dolphin_tool_path", lambda: None)
    got, stats = _check("gamecube", games, deep=True)
    assert got == [] and stats["unverified"] == 1 and stats["unverified_tools"] == ["dolphin-tool"]
    assert _check("gamecube", games)[1].get("unverified") is None          # quick needs no tool

    _fake_dolphin(tmp_path, monkeypatch, "CRC32: 1271adcd\nMD5 not computed\nSHA1: %s\nProblems Found: Yes\n\n"
                  "Severity: High\nSummary: The filesystem is invalid or could not be read.\n\n"           # homebrew
                  "Severity: Low\nSummary: This disc image has an unusual size.\n" % ("3d" * 20))
    assert _check("gamecube", games, deep=True) == ([], {"checked": 1, "matched": 1, "unmatched": 0})
    _fake_dolphin(tmp_path, monkeypatch, "CRC32: 0badf00d\nSHA1: %s\nProblems Found: No\n" % ("3d" * 20))
    assert _check("gamecube", games, deep=True)[0] == [
        ("Lorem (USA).rvz", "differs", "Checksum doesn't match RomM's copy")]
    _fake_dolphin(tmp_path, monkeypatch, "CRC32 not computed\nMD5 not computed\nSHA1 not computed\n"
                  "Problems Found: Yes\n\nSeverity: Medium\nSummary: Some of the data could not be read.\n")
    assert _check("gamecube", games, deep=True)[0] == [
        ("Lorem (USA).rvz", "damaged", "Compressed file damaged (dolphin-tool couldn't read all of it)")]
    _fake_dolphin(tmp_path, monkeypatch, "", rc=1)                       # "Unable to open input file"
    assert _check("gamecube", games, deep=True)[0][0][1] == "damaged"


def test_rvz_full_check_without_romms_hashes_trusts_the_block_hashes(isolated, tmp_path, monkeypatch):
    make_rvz(os.path.join(rommlib.ROMS_ROOT, "wii", "Lorem (USA).rvz"), ISO)
    games = [("Lorem (USA).wbfs", [_iso_rec("Lorem (USA).wbfs", ISO // 2, "12345678")])]
    _fake_dolphin(tmp_path, monkeypatch, "CRC32: 1271adcd\nSHA1: %s\nProblems Found: No\n" % ("3d" * 20))
    assert _check("wii", games, deep=True)[0] == []                      # a .wbfs's CRC isn't the disc's
    _fake_dolphin(tmp_path, monkeypatch, "CRC32: 1271adcd\nSHA1: %s\nProblems Found: Yes\n\nSeverity: Medium\n"
                  "Summary: Errors were found in 12 blocks in the Data partition.\n" % ("3d" * 20))
    assert _check("wii", games, deep=True)[0] == [
        ("Lorem (USA).rvz", "differs", "dolphin-tool found errors in 12 blocks (damaged, or a bad dump)")]


def test_an_rvz_romm_keeps_itself_is_compared_like_any_file(isolated):
    p = make_rvz(os.path.join(rommlib.ROMS_ROOT, "wii", "Lorem (USA).rvz"), ISO)
    got, _ = _check("wii", [("Lorem (USA).rvz", [_iso_rec("Lorem (USA).rvz", os.path.getsize(p) + 1)])])
    assert [g[2].split(":")[0] for g in got] == ["Smaller than RomM's copy"]


# ---------------------------------------------------------------- squashfs

FAKE_UNSQUASHFS = r'''
import json, os, sys
a = sys.argv[1:]
side = lambda p: os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(p))), "fake", os.path.basename(p))
if a[0] == "-lls":
    meta = json.load(open(side(a[1]) + ".json"))
    print("Parallel unsquashfs: Using 4 processors\n2 inodes (4 blocks) to write\n")
    print("drwxr-xr-x root/root 50 2026-10-12 15:23 squashfs-root")
    for name, (size, link) in meta.items():
        print("%s root/root %d 2026-10-12 15:23 squashfs-root/%s%s" % (
            "lrwxrwxrwx" if link else "-rw-r--r--", size, name, " -> " + link if link else ""))
elif os.environ.get("FAKE_NO_CAT"):
    sys.stderr.write("SYNTAX: unsquashfs [options] filesystem [directories or files to extract]\n")
    sys.exit(1)
else:
    assert a[:2] == ["-no-wildcards", "-cat"], a
    data = open(side(a[2]) + ".bin", "rb").read()
    sys.stdout.buffer.write(data)
    sys.exit(int(os.environ.get("FAKE_RC", "0")))
'''


def make_squashfs(path, inner=None, data=b"", used=None, pad=4096):
    """A squashfs superblock (what's checked without unsquashfs), plus what the
    fake unsquashfs lists and reads out of it (kept out of roms/, beside it)."""
    used = used or 4096
    sb = b"hsqs" + b"\0" * 24 + struct.pack("<HH", 4, 0) + b"\0" * 8 + struct.pack("<Q", used)
    with open(path, "wb") as f:
        f.write(sb.ljust(max(pad, 96), b"\0"))
    name = os.path.basename(path)[:-len(".squashfs")]
    side = os.path.join(os.path.dirname(rommlib.ROMS_ROOT), "fake", os.path.basename(path))
    os.makedirs(os.path.dirname(side), exist_ok=True)
    with open(side + ".json", "w") as f:
        json.dump({".ROM": [len(name), name], name: [len(data), None]} if inner is None else inner, f)
    with open(side + ".bin", "wb") as f:
        f.write(data)
    return path


@pytest.fixture
def unsquashfs(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_tool(bin_dir, "unsquashfs", FAKE_UNSQUASHFS)
    monkeypatch.setenv("PATH", "%s:%s" % (bin_dir, os.environ["PATH"]))


def test_squashfs_quick_check_reads_the_superblock_and_whats_inside(isolated, unsquashfs):
    d = os.path.join(rommlib.ROMS_ROOT, "xbox")
    os.makedirs(d)
    cut = rommlib.XBOX_PARTITIONS[2]
    img = b"x" * 3000
    make_squashfs(os.path.join(d, "Whole (USA).iso.squashfs"), data=img)
    make_squashfs(os.path.join(d, "Cut (USA).iso.squashfs"), data=img)
    make_squashfs(os.path.join(d, "Other (USA).iso.squashfs"), data=img)
    make_squashfs(os.path.join(d, "Short (USA).iso.squashfs"), data=img, used=8192)
    make_squashfs(os.path.join(d, "Empty (USA).iso.squashfs"), inner={".ROM": [3, "x"]})
    _put("xbox", "Junk (USA).iso.squashfs", b"hsqs" + b"\0" * 10)
    rec = lambda n, size: (n + ".iso", [_iso_rec(n + ".iso", size)])          # noqa: E731
    got, stats = _check("xbox", [rec("Whole (USA)", 3000), rec("Cut (USA)", 3000 + cut), rec("Other (USA)", 4000),
                                 rec("Short (USA)", 3000), rec("Empty (USA)", 3000), rec("Junk (USA)", 3000)])
    assert stats == {"checked": 6, "matched": 6, "unmatched": 0}
    assert sorted(got) == [
        ("Empty (USA).iso.squashfs", "damaged", "The disc image inside is missing (Empty (USA).iso)"),
        ("Junk (USA).iso.squashfs", "damaged", "Not a readable squashfs (damaged header)"),
        ("Other (USA).iso.squashfs", "differs", "Packed from a different file than RomM's (3 KB vs 4 KB)"),
        ("Short (USA).iso.squashfs", "damaged", "Compressed file cut short (4 KB of 8 KB)")]


def test_squashfs_without_unsquashfs_is_not_verified(isolated, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None)
    make_squashfs(_put("xbox", "Lorem (USA).iso.squashfs"), data=b"x" * 3000)
    got, stats = _check("xbox", [("Lorem (USA).iso", [_iso_rec("Lorem (USA).iso", 3000)])])
    assert got == [] and stats["unverified"] == 1 and stats["unverified_tools"] == ["unsquashfs"]


def test_squashfs_full_check_reads_the_image_out(isolated, unsquashfs, monkeypatch):
    img = b"lorem ipsum " * 300
    crc = "%08x" % zlib.crc32(img)
    make_squashfs(_put("xbox", "Lorem (USA).iso.squashfs"), data=img)
    games = [("Lorem (USA).iso", [_iso_rec("Lorem (USA).iso", len(img), crc.upper())])]
    assert _check("xbox", games, deep=True) == ([], {"checked": 1, "matched": 1, "unmatched": 0})
    bad = [("Lorem (USA).iso", [_iso_rec("Lorem (USA).iso", len(img), "0badf00d")])]
    assert _check("xbox", bad, deep=True)[0] == [
        ("Lorem (USA).iso.squashfs", "differs", "Checksum doesn't match RomM's copy")]
    cut = [("Lorem (USA).iso", [_iso_rec("Lorem (USA).iso", len(img) + rommlib.XBOX_PARTITIONS[0], "0badf00d")])]
    assert _check("xbox", cut, deep=True)[0] == []                       # cut here: RomM's CRC is the full disc's
    monkeypatch.setenv("FAKE_RC", "2")
    assert _check("xbox", games, deep=True)[0] == [
        ("Lorem (USA).iso.squashfs", "damaged", "Compressed file damaged (unsquashfs couldn't read all of it)")]
    monkeypatch.setenv("FAKE_NO_CAT", "1")
    got, stats = _check("xbox", games, deep=True)
    assert got == [] and stats["unverified_tools"] == ["unsquashfs 4.5+"]


@pytest.mark.skipif(not (shutil.which("mksquashfs") and shutil.which("unsquashfs")), reason="no squashfs-tools")
def test_squashfs_checks_against_the_real_tools(isolated, tmp_path):
    src = tmp_path / "Lorem (USA).iso"
    img = os.urandom(3 << 20)
    src.write_bytes(img)
    out = os.path.join(rommlib.ROMS_ROOT, "xbox", "Lorem (USA).iso.squashfs")
    os.makedirs(os.path.dirname(out))
    subprocess.run(["mksquashfs", str(src), out, *rommlib.SQUASH_ARGS, "-p", ".ROM s 777 0 0 Lorem (USA).iso"],
                   check=True, capture_output=True)
    assert rommlib.squashfs_listing(out) == {"Lorem (USA).iso": (len(img), None), ".ROM": (15, "Lorem (USA).iso")}
    assert rommlib.squashfs_header(out)["complete"]
    games = [("Lorem (USA).iso", [_iso_rec("Lorem (USA).iso", len(img), "%08x" % zlib.crc32(img))])]
    assert _check("xbox", games, deep=True)[0] == []
    with open(out, "r+b") as f:
        f.truncate(os.path.getsize(out) // 2)
    assert _check("xbox", games)[0][0][1] == "damaged"


# ---------------------------------------------------------------- .ps3 game folders

def make_sfo(**entries):
    keys, vals, index = b"", b"", b""
    for k, v in entries.items():
        data = struct.pack("<I", v) if isinstance(v, int) else v.encode() + b"\0"
        index += struct.pack("<HHIII", len(keys), 0x0404 if isinstance(v, int) else 0x0204, len(data), len(data),
                             len(vals))
        keys += k.encode() + b"\0"
        vals += data
    kstart = 20 + len(index)
    keys += b"\0" * (-len(keys) % 4)
    return b"\0PSF" + struct.pack("<IIII", 0x101, kstart, kstart + len(keys), len(entries)) + index + keys + vals


SFO = make_sfo(CATEGORY="DG", TITLE_ID="BLUS00000", TITLE="Lorem Ipsum", PARENTAL_LEVEL=3)


def make_folder(name, files=None):
    root = os.path.join(rommlib.ROMS_ROOT, "ps3", name)
    for rel, data in (files or {"PS3_DISC.SFB": b".SFB" + b"\0" * 60, "PS3_GAME/PARAM.SFO": SFO,
                                "PS3_GAME/USRDIR/EBOOT.BIN": b"SCE\0" + b"\0" * 400,
                                "PS3_GAME/USRDIR/DATA.PSARC": b"lorem" * 100}).items():
        p = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
    return root


def test_param_sfo_is_read(tmp_path):
    p = tmp_path / "PARAM.SFO"
    p.write_bytes(SFO)
    assert rommlib.ps3_sfo(str(p)) == {"CATEGORY": "DG", "TITLE_ID": "BLUS00000", "TITLE": "Lorem Ipsum",
                                       "PARENTAL_LEVEL": 3}
    p.write_bytes(SFO[:-8])
    assert rommlib.ps3_sfo(str(p)) is None


def test_ps3_folders_are_matched_to_romms_disc_and_checked(isolated):
    make_folder("Whole (USA).ps3")
    no_eboot = make_folder("NoBoot (USA).ps3")
    os.remove(os.path.join(no_eboot, "PS3_GAME", "USRDIR", "EBOOT.BIN"))
    with open(os.path.join(make_folder("BadSfo (USA).ps3"), "PS3_GAME", "PARAM.SFO"), "wb") as f:
        f.write(SFO[:30])
    os.remove(os.path.join(make_folder("NoSfb (USA).ps3"), "PS3_DISC.SFB"))
    make_folder("Homebrew.ps3")                                           # not in RomM
    disc = lambda n: (n + ".zip", [_zip_rec(n + ".zip", n + ".iso", 1 << 30)])     # noqa: E731
    got, stats = _check("ps3", [disc("Whole (USA)"), disc("NoBoot (USA)"), disc("BadSfo (USA)"),
                                ("NoSfb (USA).iso", [_iso_rec("NoSfb (USA).iso", 1 << 30)])])
    assert stats == {"checked": 5, "matched": 4, "unmatched": 1}
    assert sorted(got) == [
        ("BadSfo (USA).ps3", "damaged", "Damaged file: PS3_GAME/PARAM.SFO"),
        ("NoBoot (USA).ps3", "damaged", "Missing file: PS3_GAME/USRDIR/EBOOT.BIN"),
        ("NoSfb (USA).ps3", "differs", "Missing file: PS3_DISC.SFB")]


def test_ps3_full_check_reads_every_file(isolated, monkeypatch):
    root = make_folder("Lorem (USA).ps3")
    games = [("Lorem (USA).zip", [_zip_rec("Lorem (USA).zip", "Lorem (USA).iso", 1 << 30)])]
    assert _check("ps3", games, deep=True)[0] == []
    real = open

    def flaky(p, *a, **k):
        if str(p).endswith("DATA.PSARC"):
            raise OSError(5, "Input/output error")
        return real(p, *a, **k)
    monkeypatch.setattr("builtins.open", flaky)
    assert _check("ps3", games)[0] == []                                  # quick: only the boot files
    assert _check("ps3", games, deep=True)[0] == [
        ("Lorem (USA).ps3", "damaged", "Can't read PS3_GAME/USRDIR/DATA.PSARC (Input/output error)")]
    assert os.path.isdir(root)


def test_a_folder_made_from_a_disc_passes_its_check(isolated, tmp_path):
    files = dict(FILES, **{"PS3_GAME/PARAM.SFO": SFO})
    src = tmp_path / "Lorem (USA).iso"
    src.write_bytes(make_disc(files, key=None))
    os.makedirs(os.path.join(rommlib.ROMS_ROOT, "ps3"))
    rommlib.ps3_to_folder(str(src), os.path.join(rommlib.ROMS_ROOT, "ps3", "Lorem (USA).ps3"), {})
    games = [("Lorem (USA).iso", [_iso_rec("Lorem (USA).iso", src.stat().st_size)])]
    assert _check("ps3", games, deep=True) == ([], {"checked": 1, "matched": 1, "unmatched": 0})


# ---------------------------------------------------------------- .zcci / .zar through the same hooks

def test_zcci_and_zar_are_still_checked(isolated):
    hdr = b"Z3DS" + b"NCSD" + bytes([1, 0]) + struct.pack("<HI", 0x20, 0) + struct.pack("<QQ", 1000, 5000)
    _put("3ds", "Lorem (USA).zcci", hdr + b"\0" * 10)
    _put("xbox360", "Ipsum (USA).zar", b"\0" * 200)
    got, _ = _check("3ds", [("Lorem (USA).3ds", [_iso_rec("Lorem (USA).3ds", 5000)])])
    assert got == [("Lorem (USA).zcci", "damaged", "Compressed file cut short")]
    got, _ = _check("xbox360", [("Ipsum (USA).iso", [_iso_rec("Ipsum (USA).iso", 5000)])])
    assert got == [("Ipsum (USA).zar", "damaged", "Compressed file not a .zar (no ZArchive footer)")]


# ---------------------------------------------------------------- the Check screen

def test_the_check_screen_names_the_missing_tools(isolated, rb, monkeypatch):
    from test_review_batch import _drawn, _sync
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    app = rb.App(None, headless=True)
    app.bg = lambda fn, done=None, err=None: _sync(fn, done, err, app)
    monkeypatch.setattr(rommlib, "audit", lambda *a, **k: ([], {"checked": 3, "matched": 3, "unmatched": 0,
                                                               "unverified": 3,
                                                               "unverified_tools": ["dolphin-tool", "unsquashfs"]}))
    monkeypatch.setattr(rommlib, "save_audit", lambda *a: None)
    scr = rb.AuditScreen(app, deep=True)
    app.push(scr)
    assert "3 games not verified (missing dolphin-tool, unsquashfs)" in _drawn(rb, monkeypatch, scr)
