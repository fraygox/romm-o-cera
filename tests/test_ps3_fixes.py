"""PS3 discs that are damaged, only partly decrypted, or hold names exFAT can't
tell apart are refused with a Ps3Error (never another exception, never a folder
of still-encrypted files), RomM's disc keys never replace the user's own key
file, and ps3_check's memo follows the keys folder. The discs are made up."""
import io
import os
import random
import struct
import zipfile

import pytest

import rommlib

pytest.importorskip("cryptography")
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # noqa: E402
from test_ps3 import FILES, KEY, OTHER, S, _Resp, _keys, _tree, _write, _zip, make_disc  # noqa: E402

ON = {"ps3_decrypt": True}


def _open(img, name="Ipsum Quest (USA)"):
    return rommlib.Ps3Disc(io.BytesIO(bytes(img)), len(img), name)


def _extents(img):
    return _open(make_disc(**img) if isinstance(img, dict) else img).files


def _regions(img, *plain):
    """img with its region table replaced: plain = [(first, last), ...] sectors."""
    img = bytearray(img)
    img[0:S] = bytes(S)
    img[0:4] = struct.pack(">I", len(plain))
    for i, (a, b) in enumerate(plain):
        img[8 + 8 * i:16 + 8 * i] = struct.pack(">II", a, b)
    return bytes(img)


def _only_ps3_errors(fn):
    try:
        fn()
    except rommlib.Ps3Error:
        pass


# ---------------------------------------------------------------- damaged images

def _dir_records(img):
    """Offsets of every directory record (past "." and "..") in the made-up disc."""
    out = []
    for lba in range(20, 32):
        base = lba * S
        if img[base] == 0:
            break
        p = base + img[base]
        p += img[p]
        while img[p]:
            out.append(p)
            p += img[p]
    return out


def test_short_or_overlong_directory_records_are_damaged_not_a_crash():
    img = make_disc(key=None)
    recs = _dir_records(img)
    assert recs
    for p in recs:
        for rl in list(range(1, 34)) + [200, 255]:
            bad = bytearray(img)
            bad[p] = rl
            _only_ps3_errors(lambda: _open(bad))
        bad = bytearray(img)
        bad[p + 32] = 250                              # a name longer than its record
        with pytest.raises(rommlib.Ps3Error, match="damaged"):
            _open(bad)
    bad = bytearray(img)
    bad[recs[0]] = 20
    with pytest.raises(rommlib.Ps3Error, match="damaged file system"):
        _open(bad)


def test_a_file_past_the_end_of_the_disc_is_damaged():
    img = bytearray(make_disc(key=None))
    p = _dir_records(img)[0]
    img[p + 2:p + 6] = struct.pack("<I", len(img) // S + 5)
    with pytest.raises(rommlib.Ps3Error, match="past the end"):
        _open(img)


@pytest.mark.parametrize("key", [None, KEY])
def test_fuzzed_images_raise_only_ps3_errors(isolated, tmp_path, key):
    _keys(**{"Ipsum Quest (USA)": KEY})
    img = make_disc(key=key)
    rng = random.Random(7)
    keys = rommlib.ps3_keys()
    for i in range(400):
        bad = bytearray(img)
        for _ in range(rng.randint(1, 6)):
            at = rng.randrange(0, 34 * S) if rng.random() < 0.8 else rng.randrange(len(bad))
            bad[at] = rng.randrange(256)

        def run():
            d = _open(bad)
            if not d.plain:
                d.key = keys.get(d.find_key(keys))
            d.extract(str(tmp_path / ("out%d" % i)))
        _only_ps3_errors(run)
    for cut in (S, 17 * S, 21 * S, 30 * S, len(img) - S, len(img) - 1):
        src = _write(str(tmp_path / ("cut%d.iso" % cut)), img[:cut])
        _only_ps3_errors(lambda: rommlib.ps3_to_folder(src, str(tmp_path / ("cut%d.ps3" % cut)), ON))


def test_damaged_zips_raise_only_ps3_errors(isolated, tmp_path):
    _keys(**{"Ipsum Quest (USA)": KEY})
    good = open(_zip(str(tmp_path / "good.zip"), "Ipsum Quest (USA).iso", make_disc()), "rb").read()
    rng = random.Random(3)
    cases = [good[:n] for n in (10, 40, len(good) // 3, len(good) - 30)]
    for _ in range(60):
        bad = bytearray(good)
        at = rng.randrange(30, len(good) - 100)               # inside the deflated disc
        bad[at:at + rng.randint(1, 40)] = bytes(rng.randrange(256) for _ in range(40))[:rng.randint(1, 40)]
        cases.append(bytes(bad))
    for i, data in enumerate(cases):
        src = _write(str(tmp_path / "z" / ("Ipsum Quest (USA) %d.zip" % i)), data)
        _only_ps3_errors(lambda: rommlib.ps3_check(src, ON))
        _only_ps3_errors(lambda: rommlib.ps3_to_folder(src, str(tmp_path / "z" / ("o%d.ps3" % i)), ON))
        _only_ps3_errors(lambda: rommlib.ps3_image_size(src))
        assert rommlib.ps3_zip_is_disc(src) in (True, False)


def test_a_damaged_deflate_stream_is_a_ps3_error(isolated, tmp_path):
    img = make_disc(key=None)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("g.iso", img)
    data = bytearray(buf.getvalue())
    start = 30 + len("g.iso")                                 # the local header, then the stream
    data[start + 8:start + 200] = b"\xff" * 192
    src = _write(str(tmp_path / "Lorem.zip"), bytes(data))
    with pytest.raises(rommlib.Ps3Error):
        rommlib.ps3_check(src, {})


def test_fuzzed_param_sfo_is_none_or_a_dict(tmp_path):
    sfo = bytearray(b"\0PSF\x01\x01\0\0")
    sfo += struct.pack("<III", 20 + 16 * 2, 20 + 16 * 2 + 16, 2)
    sfo += struct.pack("<HHIII", 0, 0x0204, 10, 16, 0) + struct.pack("<HHIII", 9, 0x0404, 4, 4, 16)
    sfo += b"TITLE_ID\0VER\0\0\0\0" + b"BLUS00000\0" + bytes(6) + struct.pack("<I", 7)
    assert rommlib.ps3_sfo(_write(str(tmp_path / "p.sfo"), bytes(sfo)))["TITLE_ID"] == "BLUS00000"
    rng = random.Random(5)
    for i in range(300):
        bad = bytearray(sfo)
        for _ in range(rng.randint(1, 4)):
            bad[rng.randrange(len(bad))] = rng.randrange(256)
        bad = bad[:rng.randint(len(bad) // 2, len(bad))] if rng.random() < 0.3 else bad
        assert isinstance(rommlib.ps3_sfo(_write(str(tmp_path / ("s%d" % i)), bytes(bad))), (dict, type(None)))


# ---------------------------------------------------------------- partly decrypted

LATE = dict(FILES, **{"PS3_GAME/USRDIR/ZZ.SPRX": b"SCE\0" + b"late module " * 300})


def _partly(files, still_encrypted):
    """A disc decrypted except for the files in still_encrypted."""
    plain, enc = make_disc(files, key=None), make_disc(files, key=KEY)
    out = bytearray(plain)
    for path in still_encrypted:
        for lba, length in _extents(plain)[path]:
            n = (length + S - 1) // S
            out[lba * S:(lba + n) * S] = enc[lba * S:(lba + n) * S]
    return bytes(out)


def test_an_image_decrypted_only_at_its_start_is_refused(isolated, tmp_path):
    img = _partly(LATE, ["PS3_GAME/USRDIR/ZZ.SPRX"])
    src = _write(str(tmp_path / "Lorem (USA).iso"), img)
    with pytest.raises(rommlib.Ps3Error, match="partly decrypted"):
        rommlib.ps3_check(src, {})


def test_a_zip_found_partly_decrypted_on_the_way_stops_before_finishing(isolated, tmp_path, monkeypatch):
    monkeypatch.setattr(rommlib.Ps3Disc, "PROBE_NEAR", 0)       # a big disc: only its start read at check
    img = _partly(LATE, ["PS3_GAME/USRDIR/ZZ.SPRX"])
    src = _zip(str(tmp_path / "Lorem (USA).zip"), "Lorem (USA).iso", img)
    assert rommlib.ps3_check(src, {})["plain"] is True
    with pytest.raises(rommlib.Ps3Error, match="still encrypted"):
        rommlib.ps3_to_folder(src, str(tmp_path / "Lorem (USA).ps3"), {})
    assert os.path.exists(src)


def test_an_encrypted_zip_with_a_part_already_decrypted_stops(isolated, tmp_path, monkeypatch):
    monkeypatch.setattr(rommlib.Ps3Disc, "PROBE_NEAR", 0)
    plain, enc = make_disc(LATE, key=None), bytearray(make_disc(LATE, key=KEY))
    for lba, length in _extents(plain)["PS3_GAME/USRDIR/ZZ.SPRX"]:
        enc[lba * S:(lba + (length + S - 1) // S) * S] = plain[lba * S:(lba + (length + S - 1) // S) * S]
    _keys(**{"Lorem (USA)": KEY})
    src = _zip(str(tmp_path / "Lorem (USA).zip"), "Lorem (USA).iso", bytes(enc))
    assert rommlib.ps3_check(src, ON)["key"] == "Lorem (USA)"
    with pytest.raises(rommlib.Ps3Error, match="didn't come out right"):
        rommlib.ps3_to_folder(src, str(tmp_path / "Lorem (USA).ps3"), ON)


def _two_parts(files, key):
    """files on a disc with two encrypted parts: the second holds only ZZ.*, after
    a plain one holding EMPTY.DAT (an empty sector, the same either way)."""
    img = make_disc(files, key=key)
    ext = _extents(make_disc(files, key=None))
    empty = ext["PS3_GAME/USRDIR/EMPTY.DAT"][0][0]
    zz = [v for p, v in ext.items() if "/ZZ." in p][0][0][0]
    assert zz == empty + 1
    ds = min(v[0][0] for v in ext.values())
    total = len(img) // S
    end = max(lba + (n + S - 1) // S for v in ext.values() for lba, n in v)
    return _regions(img, (0, ds - 1), (empty, empty), (end, total - 1))


@pytest.mark.parametrize("zz", [b"SCE\0" + b"x" * 5000, os.urandom(2 * S + 100)], ids=["known-start", "zero-tail"])
def test_every_encrypted_part_is_checked_and_a_good_disc_still_converts(isolated, tmp_path, zz):
    files = dict(FILES, **{"PS3_GAME/USRDIR/ZZ.SPRX" if zz[:4] == b"SCE\0" else "PS3_GAME/USRDIR/ZZ.DAT": zz})
    d = _open(_two_parts(files, None))
    assert len(d.regions) == 2 and d.plain
    assert any(lba >= d.regions[1][0] for lba in d.checks)       # the second part is checked too
    _keys(**{"Lorem (USA)": KEY})
    src = _write(str(tmp_path / "Lorem (USA).iso"), _two_parts(files, KEY))
    out = str(tmp_path / "Lorem (USA).ps3")
    assert rommlib.ps3_to_folder(src, out, ON) == "Lorem (USA)"
    want = {p: (b"".join(v) if isinstance(v, list) else v) for p, v in files.items()}
    assert _tree(out) == want


TEXT = b"lorem ipsum dolor sit amet, " * 300          # plain data with no known start


def _aligned(n, data=None):
    """n sectors exactly: no bytes past the file's end to check."""
    return (data or os.urandom(n * S))[:n * S]


def _mixed(files, key, plain_parts):
    """_two_parts(files, key), with the parts in plain_parts (0 or 1) as the plain
    image has them: a disc decrypted only partly (key) or still partly encrypted."""
    img, plain = bytearray(_two_parts(files, key)), _two_parts(files, None)
    for i, (a, b) in enumerate(_open(plain).regions):
        if i in plain_parts:
            img[a * S:b * S] = plain[a * S:b * S]
    return bytes(img)


def _want_of(files):
    return {p: (b"".join(v) if isinstance(v, list) else v) for p, v in files.items()}


@pytest.mark.parametrize("zz", [_aligned(4), _aligned(4, TEXT)], ids=["random", "text"])
def test_a_part_with_no_known_sector_doesnt_refuse_a_good_disc(isolated, tmp_path, zz):
    """A big sector-aligned archive (a packed file with no known start, nothing past
    its end) alone in a part: its disc converts encrypted or already decrypted."""
    files = dict(FILES, **{"PS3_GAME/USRDIR/ZZ.CPK": zz})
    _keys(**{"Lorem (USA)": KEY})
    for key, cfg in ((None, {}), (KEY, ON)):
        img = _two_parts(files, key)
        d = _open(img)
        assert len(d.regions) == 2 and not any(d.checks[lba][1] is not None
                                               for lba in d.checks if lba >= d.regions[1][0])
        src = _write(str(tmp_path / str(bool(key)) / "Lorem (USA).iso"), img)
        out = str(tmp_path / str(bool(key)) / "Lorem (USA).ps3")
        assert rommlib.ps3_to_folder(src, out, cfg) == ("Lorem (USA)" if key else None)
        assert _tree(out) == _want_of(files)


def test_the_reviewers_disc_with_an_archive_after_a_plain_file_converts(isolated, tmp_path):
    files = dict(FILES)
    del files["PS3_GAME/USRDIR/DATA/BIG.PSARC"]
    files["PS3_GAME/USRDIR/M.TXT"] = b"plain middle file " * 10
    files["PS3_GAME/USRDIR/Z.CPK"] = _aligned(4)
    plain = make_disc(files, key=None)
    ext = _extents(plain)
    m, z = ext["PS3_GAME/USRDIR/M.TXT"][0][0], ext["PS3_GAME/USRDIR/Z.CPK"][0][0]
    first, total = min(e[0][0] for e in ext.values()), len(plain) // S
    enc = bytearray(_regions(plain, (0, first - 1), (m, m), (z + 4, total - 1)))
    for n in list(range(first, m)) + list(range(m + 1, z + 4)):
        sec = bytes(enc[n * S:(n + 1) * S])
        if sec != bytes(S):
            enc[n * S:(n + 1) * S] = Cipher(algorithms.AES(KEY), modes.CBC(b"\0" * 12 + struct.pack(">I", n))) \
                .encryptor().update(sec)
    _keys(**{"Lorem (USA)": KEY})
    src = _write(str(tmp_path / "Lorem (USA).iso"), bytes(enc))
    assert rommlib.ps3_to_folder(src, str(tmp_path / "Lorem (USA).ps3"), ON) == "Lorem (USA)"
    assert _tree(str(tmp_path / "Lorem (USA).ps3")) == _want_of(files)
    src = _write(str(tmp_path / "d" / "Lorem (USA).iso"), _regions(plain, (0, first - 1), (m, m), (z + 4, total - 1)))
    assert rommlib.ps3_to_folder(src, str(tmp_path / "d" / "Lorem (USA).ps3"), {}) is None


def test_a_part_already_decrypted_is_told_by_its_data_not_looking_random(isolated, tmp_path):
    """An encrypted disc whose second part (no known sector in it) is plain already."""
    files = dict(FILES, **{"PS3_GAME/USRDIR/ZZ.DAT": _aligned(4, TEXT)})
    _keys(**{"Lorem (USA)": KEY})
    src = _write(str(tmp_path / "Lorem (USA).iso"), _mixed(files, KEY, {1}))
    with pytest.raises(rommlib.Ps3Error, match="partly decrypted"):
        rommlib.ps3_check(src, ON)


def test_a_part_already_decrypted_is_caught_on_the_way_through_a_zip(isolated, tmp_path, monkeypatch):
    monkeypatch.setattr(rommlib.Ps3Disc, "PROBE_NEAR", 0)       # only the first part read at check
    files = dict(FILES, **{"PS3_GAME/USRDIR/ZZ.DAT": _aligned(4, TEXT)})
    _keys(**{"Lorem (USA)": KEY})
    src = _zip(str(tmp_path / "Lorem (USA).zip"), "Lorem (USA).iso", _mixed(files, KEY, {1}))
    assert rommlib.ps3_check(src, ON)["key"] == "Lorem (USA)"
    with pytest.raises(rommlib.Ps3Error, match="decrypted already"):
        rommlib.ps3_to_folder(src, str(tmp_path / "Lorem (USA).ps3"), ON)


def test_a_part_still_encrypted_with_no_known_sector_is_caught_by_its_key(isolated, tmp_path):
    """A decrypted disc whose second part is still encrypted, with nothing in it to
    tell by but that the disc's key turns it into plain data."""
    files = dict(FILES, **{"PS3_GAME/USRDIR/ZZ.DAT": _aligned(4, TEXT)})
    src = _write(str(tmp_path / "Lorem (USA).iso"), _mixed(files, KEY, {0}))
    assert rommlib.ps3_check(src, {})["plain"] is True              # no key to tell by: goes by the rest
    _keys(**{"Lorem (USA)": KEY})
    with pytest.raises(rommlib.Ps3Error, match="partly decrypted"):
        rommlib.ps3_check(src, ON)
    rommlib.ps3_check(_write(str(tmp_path / "ok" / "Lorem (USA).iso"), _two_parts(files, None)), ON)


@pytest.mark.parametrize("zz", [{"PS3_GAME/USRDIR/ZZ.DAT": os.urandom(2 * S + 100)},
                                {"PS3_GAME/USRDIR/ZZ.%d.SPRX" % i: _aligned(1, b"SCE\0" + os.urandom(S))
                                 for i in range(3)}],
                         ids=["a-files-end", "starts"])
def test_a_part_still_encrypted_is_caught_by_its_known_sectors(isolated, tmp_path, zz):
    files = dict(FILES, **zz)
    src = _write(str(tmp_path / "Lorem (USA).iso"), _mixed(files, KEY, {0}))
    with pytest.raises(rommlib.Ps3Error, match="partly decrypted"):
        rommlib.ps3_check(src, {})


def test_a_part_that_cant_be_told_either_way_goes_by_the_rest(isolated, tmp_path):
    """Random-looking data alone in a part, as it is or decrypted: nothing to tell by."""
    files = dict(FILES, **{"PS3_GAME/USRDIR/ZZ.CPK": _aligned(4)})
    _keys(**{"Lorem (USA)": KEY})
    for i, (img, cfg) in enumerate(((_mixed(files, KEY, {1}), ON), (_mixed(files, KEY, {0}), ON))):
        src = _write(str(tmp_path / str(i) / "Lorem (USA).iso"), img)
        rommlib.ps3_to_folder(src, str(tmp_path / str(i) / "Lorem (USA).ps3"), cfg)


# ---------------------------------------------------------------- a file that isn't what its name says

FAKE = {"PS3_GAME/USRDIR/ICON.PNG": b"not a picture " * 300}   # named .PNG, isn't one


@pytest.mark.parametrize("key", [None, KEY], ids=["decrypted", "encrypted"])
def test_one_file_that_isnt_what_its_name_says_doesnt_refuse_the_disc(isolated, tmp_path, key):
    files = dict(FILES, **FAKE)
    d = _open(make_disc(files, key=None))
    assert d.files["PS3_GAME/USRDIR/ICON.PNG"][0][0] in d.checks              # it is checked
    _keys(**{"Lorem (USA)": KEY})
    src = _write(str(tmp_path / "Lorem (USA).iso"), make_disc(files, key=key))
    out = str(tmp_path / "Lorem (USA).ps3")
    assert rommlib.ps3_to_folder(src, out, ON) == ("Lorem (USA)" if key else None)
    assert _tree(out) == _want_of(files)


def test_a_part_whose_known_starts_mostly_dont_fit_is_refused(isolated, tmp_path):
    """Several files named for a known start that don't have it, and nothing else in
    the part fits the key: damaged, not one odd file."""
    files = dict(FILES, **{"PS3_GAME/USRDIR/ZZ.%d.SPRX" % i: _aligned(1) for i in range(3)})
    _keys(**{"Lorem (USA)": KEY})
    src = _write(str(tmp_path / "Lorem (USA).iso"), _two_parts(files, KEY))
    with pytest.raises(rommlib.Ps3Error, match="damaged, or the wrong key"):
        rommlib.ps3_check(src, ON)


EBOOTISH = struct.pack(">%dI" % (S // 4), *range(S // 4))       # numbers counting up: plain, but every byte value


def test_ciphertext_never_looks_structured_and_plain_data_does():
    rng = random.Random(11)
    assert not any(rommlib._ps3_structured(bytes(rng.randrange(256) for _ in range(S))) for _ in range(200))
    assert rommlib._ps3_structured(TEXT[:S]) and rommlib._ps3_structured(EBOOTISH)



# ---------------------------------------------------------------- the key found is the key used

def test_the_key_found_at_check_is_the_one_used_even_if_the_folder_changes(isolated, tmp_path, monkeypatch):
    _keys(**{"Lorem (USA)": KEY})
    src = _write(str(tmp_path / "Lorem (USA).iso"), make_disc())
    real, calls = rommlib.ps3_keys, []

    def keys_then_gone(folder=None):                   # Settings fetching keys halfway through
        calls.append(1)
        return real(folder) if len(calls) <= 2 else {"Lorem (USA)": OTHER}
    monkeypatch.setattr(rommlib, "ps3_keys", keys_then_gone)
    out = str(tmp_path / "Lorem (USA).ps3")
    assert rommlib.ps3_to_folder(src, out, ON) == "Lorem (USA)"
    assert _tree(out) == _want_of(FILES)


# ---------------------------------------------------------------- names

@pytest.mark.parametrize("names", [("PS3_GAME/USRDIR/a.dat", "PS3_GAME/USRDIR/A.DAT"),
                                   ("PS3_GAME/USRDIR/Data/X.BIN", "PS3_GAME/USRDIR/DATA/Y.BIN"),
                                   ("PS3_GAME/USRDIR/Data", "PS3_GAME/USRDIR/DATA/Y.BIN")])
def test_names_that_only_differ_in_case_are_refused(isolated, tmp_path, names):
    files = dict(FILES, **{n: b"lorem %d" % i for i, n in enumerate(names)})
    src = _write(str(tmp_path / "Lorem.iso"), make_disc(files, key=None))
    with pytest.raises(rommlib.Ps3Error, match="two files named"):
        rommlib.ps3_check(src, {})


# ---------------------------------------------------------------- keys

def _api(bodies, listing):
    class Api(rommlib.Api):
        base = "http://romm"

        def __init__(self):
            self.got = []

        def firmware(self, pid):
            return listing

        @property
        def s(api):
            class S:
                @staticmethod
                def get(url, **kw):
                    fid = int(url.split("/api/firmware/")[1].split("/")[0])
                    api.got.append(fid)
                    return _Resp(bodies[fid])
            return S
    return Api()


def _keyzip(**named):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n, k in named.items():
            z.writestr(n.replace("_", " ") + ".key", k)
    return b.getvalue()


def test_romms_keys_never_replace_the_users_own_file(isolated, tmp_path):
    d = rommlib.ps3_keys_dir()
    os.makedirs(d)
    mine = _keyzip(**{"Lorem (USA)": OTHER, "Dolor (USA)": OTHER})
    _write(os.path.join(d, "keys.zip"), mine)
    _write(os.path.join(d, "Amet (USA).key"), OTHER)
    romms = _keyzip(**{"Lorem (USA)": KEY, "Ipsum (USA)": KEY})
    api = _api({1: romms, 2: KEY}, [{"id": 1, "file_name": "keys.zip", "file_size_bytes": len(romms),
                                     "md5_hash": None},
                                    {"id": 2, "file_name": "Amet (USA).key", "file_size_bytes": 16,
                                     "md5_hash": None}])
    rommlib.fetch_ps3_keys(api, 16)
    assert open(os.path.join(d, "keys.zip"), "rb").read() == mine
    assert open(os.path.join(d, "Amet (USA).key"), "rb").read() == OTHER
    assert sorted(os.listdir(d)) == ["Amet (USA) (RomM).key", "Amet (USA).key", "keys (RomM).zip", "keys.zip"]
    keys = rommlib.ps3_keys()
    assert keys["Lorem (USA)"] == OTHER and keys["Lorem (USA) (2)"] == KEY       # both kept, the user's first
    assert keys["Ipsum (USA)"] == KEY and keys["Dolor (USA)"] == OTHER
    assert keys["Amet (USA)"] == OTHER and keys["Amet (USA) (RomM)"] == KEY
    src = _write(str(tmp_path / "Lorem (USA).iso"), make_disc())
    assert rommlib.ps3_check(src, ON)["key"] == "Lorem (USA) (2)"
    rommlib.fetch_ps3_keys(api, 16)                                  # again: nothing new kept
    assert sorted(os.listdir(d)) == ["Amet (USA) (RomM).key", "Amet (USA).key", "keys (RomM).zip", "keys.zip"]


def test_a_damaged_member_in_a_key_zip_skips_only_that_member(isolated):
    d = rommlib.ps3_keys_dir()
    os.makedirs(d)
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("Bad (USA).dkey", KEY.hex() * 1)
        z.writestr("Good (USA).key", KEY)
    data = bytearray(b.getvalue())
    at = data.index(b"Bad (USA).dkey") + len("Bad (USA).dkey")
    data[at:at + 8] = b"\xff" * 8                                    # its deflate stream
    _write(os.path.join(d, "keys.zip"), bytes(data))
    assert rommlib.ps3_keys() == {"Good (USA)": KEY}


# ---------------------------------------------------------------- the check's memo

def test_the_check_follows_the_keys_folder(isolated, tmp_path):
    src = _write(str(tmp_path / "Lorem (USA).iso"), make_disc())
    d = _keys(**{"Lorem (USA)": KEY})
    assert rommlib.ps3_check(src, ON)["key"] == "Lorem (USA)"
    for n in os.listdir(d):
        os.remove(os.path.join(d, n))
    _keys(**{"Lorem (Europe)": OTHER})                               # as many keys, none right
    with pytest.raises(rommlib.Ps3Error, match="no disc key"):
        rommlib.ps3_check(src, ON)
    _keys(**{"Lorem (USA)": KEY})
    assert rommlib.ps3_check(src, ON)["key"] == "Lorem (USA)"
    for n in os.listdir(d):
        os.remove(os.path.join(d, n))
    os.rmdir(d)                                                      # no folder at all
    with pytest.raises(rommlib.Ps3Error, match="no disc key"):
        rommlib.ps3_check(src, ON)


# ---------------------------------------------------------------- a zip read only near its start

def _encrypt(img, key=KEY):
    """img with every non-empty sector of its encrypted parts encrypted with key."""
    img = bytearray(img)
    for a, b in _open(bytes(img)).regions:
        for n in range(a, b):
            sec = bytes(img[n * S:(n + 1) * S])
            if sec != bytes(S):
                iv = b"\0" * 12 + struct.pack(">I", n)
                img[n * S:(n + 1) * S] = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor().update(sec)
    return bytes(img)


@pytest.mark.parametrize("first", [bytes(8 * S), os.urandom(8 * S)], ids=["blank", "packed"])
def test_a_zip_whose_start_tells_nothing_is_read_on_to_a_known_sector(isolated, tmp_path, monkeypatch, first):
    """The first file of the encrypted part is a blank padding file or a big packed
    one, as long as all the zip's check reads at first: it reads on to the first
    file it can tell by, and converts as the loose image does, encrypted or not."""
    monkeypatch.setattr(rommlib.Ps3Disc, "PROBE_NEAR", 4)          # stands for 256 MB
    files = dict(FILES, **{"A/BIG.DAT": first})
    _keys(**{"Lorem (USA)": KEY})
    for key in (KEY, None):
        sub = tmp_path / str(bool(key))
        src = _zip(str(sub / "Lorem (USA).zip"), "Lorem (USA).iso", make_disc(files, key=key))
        d, close = rommlib.ps3_disc(src)
        try:
            known = [lba for lba in d.check_lbas if d.checks[lba][2] is not None]
            assert d.plain is (key is None)
            assert d.check_lbas[0] + 4 < max(lba for lba, _ in d.probes) <= known[0]    # read on, no further
        finally:
            close()
        for cfg in (ON, {}):
            if key and not cfg:
                with pytest.raises(rommlib.Ps3Error, match="turn on Decrypt"):
                    rommlib.ps3_check(src, cfg)
                continue
            out = str(sub / ("Lorem (USA).%d.ps3" % bool(cfg)))
            assert rommlib.ps3_to_folder(src, out, cfg) == ("Lorem (USA)" if key else None)
            assert _tree(out) == _want_of(files)


# ---------------------------------------------------------------- what's past a file's end

def _relink(img, name, lba, size):
    """img with the directory record of the file called name pointed at lba, size."""
    img, n = bytearray(img), name.encode() + b";1"
    for p in _dir_records(img):
        if bytes(img[p + 33:p + 33 + img[p + 32]]) == n:
            img[p + 2:p + 6], img[p + 6:p + 10] = struct.pack("<I", lba), struct.pack(">I", lba)
            img[p + 10:p + 14], img[p + 14:p + 18] = struct.pack("<I", size), struct.pack(">I", size)
            return bytes(img)
    raise AssertionError(name)


@pytest.mark.parametrize("key", [None, KEY], ids=["decrypted", "encrypted"])
def test_two_files_sharing_data_dont_refuse_the_disc(isolated, tmp_path, key):
    """A short entry over the start of a longer file: past its end is the other
    file's data, not zeros, so its end isn't checked for them."""
    files = dict(FILES, **{"PS3_GAME/USRDIR/LINK.DAT": b"x" * 100})
    plain = make_disc(files, key=None)
    big = _extents(plain)["PS3_GAME/USRDIR/DATA/BIG.PSARC"][0][0]
    plain = _relink(plain, "LINK.DAT", big, 100)
    d = _open(plain)
    assert d.files["PS3_GAME/USRDIR/LINK.DAT"] == [(big, 100)]
    assert big not in d.checks or d.checks[big][1] is None             # stats only: no end checked there
    _keys(**{"Lorem (USA)": KEY})
    src = _write(str(tmp_path / "Lorem (USA).iso"), _encrypt(plain) if key else plain)
    out = str(tmp_path / "Lorem (USA).ps3")
    assert rommlib.ps3_to_folder(src, out, ON) == ("Lorem (USA)" if key else None)
    want = dict(_want_of(FILES), **{"PS3_GAME/USRDIR/LINK.DAT": FILES["PS3_GAME/USRDIR/DATA/BIG.PSARC"][0][:100]})
    assert _tree(out) == want


@pytest.mark.parametrize("key", [None, KEY], ids=["decrypted", "encrypted"])
def test_junk_past_a_files_end_doesnt_refuse_the_disc(isolated, tmp_path, key):
    files = dict(FILES, **{"PS3_GAME/USRDIR/NOTE.TXT": TEXT[:1000]})
    plain = bytearray(make_disc(files, key=None))
    lba = _extents(bytes(plain))["PS3_GAME/USRDIR/NOTE.TXT"][0][0]
    assert lba in _open(bytes(plain)).checks                           # its end is checked
    plain[lba * S + 1000:(lba + 1) * S] = (b"mastering junk " * 200)[:S - 1000]
    plain = bytes(plain)
    _keys(**{"Lorem (USA)": KEY})
    src = _write(str(tmp_path / "Lorem (USA).iso"), _encrypt(plain) if key else plain)
    out = str(tmp_path / "Lorem (USA).ps3")
    assert rommlib.ps3_to_folder(src, out, ON) == ("Lorem (USA)" if key else None)
    assert _tree(out) == _want_of(files)


# ---------------------------------------------------------------- a part nothing tells about, and every key

def test_a_part_still_encrypted_is_caught_by_any_key_only_with_decrypting_on(isolated, tmp_path):
    """The key that opens the still-encrypted part is named nothing like the disc,
    behind fifty that are: tried with Decrypt PS3 discs on, not off (ranked keys only)."""
    files = dict(FILES, **{"PS3_GAME/USRDIR/ZZ.DAT": _aligned(4, TEXT)})
    src = _write(str(tmp_path / "Lorem (USA).iso"), _mixed(files, KEY, {0}))
    assert rommlib.ps3_check(src, {})["plain"] is True                  # no keys: nothing to tell by
    _keys(**dict({"Lorem (USA) disc %d" % i: os.urandom(16) for i in range(60)}, Unrelated=KEY))
    assert "Unrelated" not in rommlib.ps3_key_order("Lorem (USA)", rommlib.ps3_keys())[:rommlib.PS3_RANKED]
    assert rommlib.ps3_check(src, {})["plain"] is True        # off: only the ranked keys are tried
    with pytest.raises(rommlib.Ps3Error, match="partly decrypted"):
        rommlib.ps3_check(src, ON)                                # on: every key


# ---------------------------------------------------------------- a wrong key that fits by chance

def test_a_wrong_key_that_fits_one_sector_by_chance_goes_on_to_the_next(isolated, tmp_path, monkeypatch):
    real = rommlib.Ps3Disc._opens
    monkeypatch.setattr(rommlib.Ps3Disc, "_opens", lambda self, key, probes: key == OTHER or real(self, key, probes))
    src = _write(str(tmp_path / "Lorem (USA).iso"), make_disc())
    _keys(**{"Lorem (USA)": OTHER, "Lorem (Europe)": KEY})              # the wrong one tried first
    assert rommlib.ps3_check(src, ON)["key"] == "Lorem (Europe)"
    os.remove(os.path.join(rommlib.ps3_keys_dir(), "Lorem (Europe).key"))
    with pytest.raises(rommlib.Ps3Error, match="opens only some of it"):
        rommlib.ps3_check(src, ON)


# ---------------------------------------------------------------- bounded cost

def test_the_key_sweep_stops_at_its_time_cap_and_answers_as_before(isolated, tmp_path, monkeypatch):
    files = dict(FILES, **{"PS3_GAME/USRDIR/ZZ.DAT": _aligned(4, TEXT)})
    src = _write(str(tmp_path / "Lorem (USA).iso"), _mixed(files, KEY, {0}))
    _keys(**dict({"Lorem (USA) disc %d" % i: os.urandom(16) for i in range(60)}, Unrelated=KEY))
    monkeypatch.setattr(rommlib, "PS3_SWEEP_SECONDS", -1.0)
    assert rommlib.ps3_check(src, ON)["plain"] is True            # cut short: taken as plain, never an error


def test_a_zip_is_read_on_only_so_far(isolated, tmp_path, monkeypatch):
    monkeypatch.setattr(rommlib.Ps3Disc, "PROBE_NEAR", 4)
    monkeypatch.setattr(rommlib, "PS3_READ_MORE", 4 * S)           # stands for 1 GB
    files = dict(FILES, **{"A/BIG.DAT": os.urandom(40 * S)})
    src = _zip(str(tmp_path / "Lorem (USA).zip"), "Lorem (USA).iso", make_disc(files, key=None))
    d, close = rommlib.ps3_disc(src)
    try:
        assert max(lba for lba, _ in d.probes) <= d.check_lbas[0] + 8
        assert d.plain is True or d.plain is False               # decided from what was read, no refusal
    finally:
        close()


def test_ps3_keys_doesnt_stat_every_key_file_again_for_an_unchanged_folder(isolated, monkeypatch):
    _keys(**{"A (USA)": KEY, "B (USA)": OTHER})
    assert rommlib.ps3_keys()
    calls = []
    real = os.stat
    monkeypatch.setattr(os, "stat", lambda p, *a, **k: calls.append(p) or real(p, *a, **k))
    assert len(rommlib.ps3_keys()) == 2
    assert len(calls) == 1                                        # the folder only


def test_an_unreadable_param_sfo_raises_for_check_games(isolated, tmp_path, monkeypatch):
    """PARAM.SFO opens for its first four bytes, then the drive fails on the second
    read: "can't be read" (the OSError), not "damaged"; other callers still get None."""
    import builtins
    d = tmp_path / "Lorem.ps3"
    for rel, data in (("PS3_GAME/USRDIR/EBOOT.BIN", b"SCE\0"), ("PS3_GAME/PARAM.SFO", b"\0PSF"), ("PS3_DISC.SFB", b".SFB")):
        _write(str(d / rel), data)
    real, n = builtins.open, [0]

    def flaky(path, *a, **k):
        if str(path).endswith("PARAM.SFO"):
            n[0] += 1
            if n[0] > 1:
                raise OSError(5, "Input/output error")
        return real(path, *a, **k)
    monkeypatch.setattr(builtins, "open", flaky)
    with pytest.raises(OSError):
        rommlib.ps3_folder_check(str(d))
    assert rommlib.ps3_sfo(str(d / "PS3_GAME/PARAM.SFO")) is None


def test_a_key_file_overwritten_in_place_is_noticed_within_a_minute(isolated, monkeypatch):
    d = rommlib.ps3_keys_dir()
    os.makedirs(d)
    p = os.path.join(d, "Lorem (USA).key")
    _write(p, KEY)
    assert list(rommlib.ps3_keys().values()) == [KEY]
    _write(p, OTHER)                                 # same name, same size; the folder's own stat is unchanged
    st = os.stat(d)
    os.utime(d, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert list(rommlib.ps3_keys().values()) == [KEY]            # within the minute: the folder shortcut
    from romm import ps3
    monkeypatch.setitem(ps3._PS3_KEYS, "at", ps3._PS3_KEYS["at"] - 61)
    os.utime(p, ns=(st.st_mtime_ns + 5_000_000_000,) * 2)
    assert list(rommlib.ps3_keys().values()) == [OTHER]
