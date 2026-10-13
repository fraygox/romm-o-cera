"""Wii U disc keys and the disc check (romm/wiiu.py), with synthetic discs from
tools/wiiu-tools/mkdisc.py and made-up keys: Cemu's keys.txt rules, bios/wiiukeys
(.key files, lists, zips of zips), the key list wuatool gets, finding a disc's
key in a .wud and a .wux, and copying keys from RomM's Wii U firmware."""
import io
import os
import stat
import sys
import zipfile

import pytest

import rommlib

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(APP, "tools", "wiiu-tools"))
pytest.importorskip("cryptography")
import mkdisc  # noqa: E402

K1, K2, K3 = bytes(range(16)), bytes(range(16, 32)), bytes(range(32, 48))      # made up


def _cemu_keys(text):
    p = rommlib.cemu_keys_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(text)
    return p


def test_keys_follow_cemus_rules(isolated):
    assert rommlib.parse_keys(("# comment\n%s # trailing\n ; %s\n%s-%s\nnot a key\n" % (
        K1.hex(), K2.hex(), K3.hex()[:16], K3.hex()[16:].upper())).encode()) == [K1, K3]
    assert rommlib.parse_keys(K2) == [K2]                                  # a raw 16-byte .key
    assert rommlib.parse_keys(b"\xff" * 20) == []


def test_keys_from_cemu_and_the_keys_folder(isolated):
    assert rommlib.wiiu_keys() == []
    _cemu_keys("# Cemu's own file\n%s\n" % K1.hex())
    d = rommlib.wiiu_keys_dir()
    os.makedirs(d)
    open(os.path.join(d, "Lorem (Europe).key"), "wb").write(K2)
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("Ipsum (USA).key", K3)
    with zipfile.ZipFile(os.path.join(d, "disc keys.zip"), "w") as z:
        z.writestr("Ipsum (USA).zip", inner.getvalue())                  # Redump's set: a zip per disc
        z.writestr("again.txt", K1.hex() + "\n")
    open(os.path.join(d, "broken.zip"), "wb").write(b"PK junk")
    assert rommlib.wiiu_keys() == [K1, K2, K3]                            # keys.txt first, each once
    out = os.path.join(str(isolated), "list.txt")
    assert rommlib.wiiu_key_file(out) == 3
    assert open(out).read().split() == [K1.hex(), K2.hex(), K3.hex()]
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600                    # keys: this user only
    os.remove(os.path.join(d, "Lorem (Europe).key"))
    assert rommlib.wiiu_keys() == [K1, K3]                                # the cache follows the folder


@pytest.mark.parametrize("wux", [False, True])
def test_check_finds_the_disc_key(isolated, wux):
    keys = {"disc": K2, "title": K3, "common": K1}
    disc = os.path.join(str(isolated), "Lorem.wux" if wux else "Lorem.wud")
    mkdisc.make_disc(disc, keys, wux=wux)
    with pytest.raises(rommlib.WiiuError, match="no disc key"):
        rommlib.wiiu_check(disc)
    _cemu_keys("%s\n%s\n" % (K1.hex(), K2.hex()))
    rommlib.wiiu_check(disc)                                              # now it has one
    with open(disc, "rb") as f:
        assert rommlib.disc_key_for(f, rommlib.wiiu_keys()) == K2
    junk = os.path.join(str(isolated), "Junk.wud")
    open(junk, "wb").write(b"\0" * 0x20000)
    with pytest.raises(rommlib.WiiuError, match="not a Wii U disc image"):
        rommlib.wiiu_check(junk)
    with pytest.raises(rommlib.WiiuError, match="can't read it"):
        rommlib.wiiu_check(junk + ".gone")


def test_wiiu_discs_map_to_wua(isolated):
    cfg = {"wua": True}
    assert rommlib.conversion_for("wiiu", ".wud", cfg, enabled=False).mode == "wua"
    assert rommlib.conversion_for("wiiu", "/r/G.WUX", cfg, enabled=False).output_path("/r/G.WUX") == "/r/G.wua"
    assert rommlib.conversion_for("wiiu", ".wua", cfg, enabled=False) is None
    assert rommlib.conversion_for("wiiu", ".wud", cfg) is None             # wuatool isn't installed here
    assert rommlib.output_converter("/r/G.wua").mode == "wua"
    assert issubclass(rommlib.WiiuError, rommlib.CHECK_ERRORS) or rommlib.WiiuError in rommlib.CHECK_ERRORS
    lay = rommlib.disc_layout({"files": [{"file_name": "G.wux", "file_size_bytes": 5}]}, "wiiu", cfg)
    assert lay["label"] == "WUX" and lay["discs"] == 1


class _Resp:
    def __init__(self, body):
        self.body, self.status_code, self.headers = body, 200, {"Content-Length": str(len(body))}

    def iter_content(self, n):
        yield self.body

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def test_keys_are_copied_from_romms_wiiu_firmware(isolated):
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("Dolor (USA).key", K3)
    bodies = {1: ("# from RomM\n%s\n" % K2.hex()).encode(), 2: b"firmware", 3: b"no keys here", 4: z.getvalue()}

    class Api(rommlib.Api):                     # the real download_firmware over a fake session
        base = "http://romm"

        def __init__(self):
            pass

        def firmware(self, pid):
            assert pid == 7
            return [{"id": 1, "file_name": "keys.txt", "file_size_bytes": len(bodies[1]), "md5_hash": None},
                    {"id": 2, "file_name": "otp.bin"},
                    {"id": 3, "file_name": "more-keys.txt"},
                    {"id": 4, "file_name": "disc keys.zip"}]

        class s:
            @staticmethod
            def get(url, **kw):
                return _Resp(bodies[int(url.split("/api/firmware/")[1].split("/")[0])])
    _cemu_keys(K1.hex() + "\n")
    assert [f["id"] for f in rommlib.wiiu_key_files(Api(), 7)] == [1, 3, 4]      # never other firmware
    assert rommlib.fetch_wiiu_keys(Api(), 7) == 3
    assert sorted(os.listdir(rommlib.wiiu_keys_dir())) == ["disc keys.zip", "keys.txt"]
    assert open(rommlib.cemu_keys_path()).read() == K1.hex() + "\n"      # Cemu's own file is never touched


def test_cemus_example_line_is_not_a_key():
    """A fresh keys.txt from Cemu holds one sample line: counting it said
    "1 Wii U disc key ready" when there were none."""
    from romm import wiiu
    sample = "%s # example key (can be deleted)\n" % ("ab" * 16)
    assert wiiu.parse_keys(sample.encode()) == []
    real = "%s # Lorem Ipsum (USA)\n" % ("cd" * 16)
    assert wiiu.parse_keys((sample + real).encode()) == [bytes.fromhex("cd" * 16)]
