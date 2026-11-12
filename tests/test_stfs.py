"""Xbox 360 packages (STFS) read in Python: what one holds, and its files
unpacked with every block checked against the SHA-1 the package keeps."""
import os

import pytest

import rommlib
from stfs_build import build_package

FILES = {"spa.bin": b"S" * 10, "art/01.png": os.urandom(5000), "art/deep/x.dat": b"x" * 4096,
         "big.ttarch2": os.urandom(250 * 4096 + 7), "empty": b""}


def _package(tmp_path, data):
    p = tmp_path / "pkg"
    p.write_bytes(data)
    return str(p)


def test_a_package_unpacks_to_its_files(tmp_path):
    """Folders, an empty file, and a file past the first hash table's 170 blocks."""
    p = _package(tmp_path, build_package(FILES, 0x00000002, 0x4D2A0001, folders=("art", "art/deep")))
    assert rommlib.stfs_kind(p) == (rommlib.STFS_ADDON, 0x4D2A0001)
    assert rommlib.stfs_listing(p) == {k: len(v) for k, v in FILES.items()}
    out = str(tmp_path / "out")
    seen = []
    pauses = []
    got = rommlib.stfs_unpack(p, out, progress=seen.append, pause=lambda: pauses.append(1))
    assert 1 <= len(pauses) <= 3          # now and then (it may ask ES what's running): never per block
    assert got == {"files": len(FILES), "bytes": sum(len(v) for v in FILES.values())}
    for k, v in FILES.items():
        assert open(os.path.join(out, k), "rb").read() == v
    assert all(0 <= f <= 1 for f in seen)


@pytest.mark.parametrize("magic", [b"CON ", b"LIVE", b"PIRS"])
def test_every_signed_kind_is_read(tmp_path, magic):
    p = _package(tmp_path, build_package({"a": b"1"}, magic=magic))
    assert rommlib.stfs_listing(p) == {"a": 1}


def test_a_changed_block_is_found(tmp_path):
    data = bytearray(build_package(FILES, folders=("art", "art/deep")))
    data[-3000] ^= 1                                           # in the big file's last blocks
    p = _package(tmp_path, bytes(data))
    with pytest.raises(rommlib.StfsError, match="doesn't match its SHA-1"):
        rommlib.stfs_unpack(p, str(tmp_path / "out"))


def test_a_cut_short_package_is_found(tmp_path):
    data = build_package(FILES, folders=("art", "art/deep"))
    p = _package(tmp_path, data[:len(data) - 8192])
    with pytest.raises(rommlib.StfsError, match="cut short"):
        rommlib.stfs_unpack(p, str(tmp_path / "out"))


def test_what_isnt_a_package_is_refused(tmp_path):
    for data in (b"", b"PIRS", b"XEX2" + bytes(0x1000)):
        with pytest.raises(rommlib.StfsError):
            rommlib.stfs_info(_package(tmp_path, data))
    assert rommlib.stfs_kind(_package(tmp_path, b"nothing")) == (None, None)
    svod = bytearray(build_package({"a": b"1"}))
    svod[0x3A9:0x3AD] = (1).to_bytes(4, "big")                 # a game on demand: disc data, not STFS
    with pytest.raises(rommlib.StfsError, match="not an STFS package"):
        rommlib.stfs_info(_package(tmp_path, bytes(svod)))


@pytest.mark.parametrize("name", ["..", "a/b", "a\\b", "."])
def test_a_name_that_would_leave_the_folder_is_refused(tmp_path, name):
    """A file table entry is a bare name: never a path."""
    data = bytearray(build_package({"zzzz": b"1"}))
    at = data.index(b"zzzz", 0xA000)
    raw = name.encode()
    data[at:at + 0x28] = raw.ljust(0x28, b"\0")
    data[at + 0x28] = len(raw) | 0x40
    # the table block changed: give it its new SHA-1 so only the name is wrong
    import hashlib
    block = at // 0x1000 * 0x1000
    data[0xA000:0xA014] = hashlib.sha1(bytes(data[block:block + 0x1000])).digest()
    with pytest.raises(rommlib.StfsError, match="can't be right"):
        rommlib.stfs_unpack(_package(tmp_path, bytes(data)), str(tmp_path / "out"))
    assert not os.path.exists(tmp_path / "x")


def test_unpacking_never_writes_into_a_folder_already_there(tmp_path):
    p = _package(tmp_path, build_package({"a": b"1"}))
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(rommlib.StfsError):
        rommlib.stfs_unpack(p, str(out))
    assert os.listdir(out) == []


def test_a_file_table_listing_more_than_the_package_holds_is_refused(tmp_path):
    """A size no package of that many blocks could hold (with a block chain that loops it
    would be written out until the disk is full)."""
    import hashlib
    data = bytearray(build_package({"zzzz": b"1"}))
    at = data.index(b"zzzz", 0xA000)
    data[at + 0x34:at + 0x38] = (0xFFFFFF00).to_bytes(4, "big")
    block = at // 0x1000 * 0x1000
    data[0xA000:0xA014] = hashlib.sha1(bytes(data[block:block + 0x1000])).digest()
    with pytest.raises(rommlib.StfsError, match="more in its files than it holds"):
        rommlib.stfs_unpack(_package(tmp_path, bytes(data)), str(tmp_path / "out"))
    assert not os.path.exists(tmp_path / "out")
