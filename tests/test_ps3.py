"""PS3 discs become .ps3 game folders (rommlib.Ps3Disc / ps3_to_folder, the
"ps3" converter): ISO 9660 read from a loose image or straight from a zip,
encrypted sectors decrypted with a disc key the user supplies (bios/ps3keys,
or copied from RomM's PS3 firmware). The discs here are made up and encrypted
with a made-up test key; no real key or game data is in these tests."""
import io
import json
import os
import struct
import zipfile

import pytest

import rommlib

pytest.importorskip("cryptography")
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # noqa: E402

S = 2048
KEY = bytes(range(16))                       # a test key, not a real disc's
OTHER = bytes(range(16, 32))
EBOOT = b"SCE\0" + b"lorem eboot " * 400
SFO = b"\0PSF" + b"ipsum sfo " * 30
BIG = [os.urandom(3 * S + 100), os.urandom(2 * S)]     # one file in two pieces (as past 4 GB)
FILES = {
    "PS3_DISC.SFB": b".SFB" + b"\0" * 60,
    "PS3_GAME/PARAM.SFO": SFO,
    "PS3_GAME/USRDIR/EBOOT.BIN": EBOOT,
    "PS3_GAME/USRDIR/EMPTY.DAT": bytes(S),            # an empty sector: left unencrypted on real discs
    "PS3_GAME/USRDIR/DATA/BIG.PSARC": BIG,
}


def _rec(name, lba, size, flags=0):
    n = name if isinstance(name, bytes) else name.encode()
    body = bytearray(33 + len(n) + (0 if len(n) % 2 else 1))
    body[0] = len(body)
    body[2:6], body[6:10] = struct.pack("<I", lba), struct.pack(">I", lba)
    body[10:14], body[14:18] = struct.pack("<I", size), struct.pack(">I", size)
    body[25], body[32] = flags, len(n)
    body[33:33 + len(n)] = n
    return bytes(body)


def make_disc(files=FILES, key=KEY, bad_name=None):
    """A PS3 disc image: region table (sector 0), "PlayStation3" (sector 1), ISO 9660
    with one directory per sector from 20, file data from the first encrypted sector."""
    dirs = {""}
    for p in files:
        parts = p.split("/")
        for i in range(1, len(parts)):
            dirs.add("/".join(parts[:i]))
    dirs = sorted(dirs, key=lambda d: (d.count("/"), d))
    dir_lba = {d: 20 + i for i, d in enumerate(dirs)}
    data_start = 20 + len(dirs) + 2
    lba, ext = data_start, {}
    for p, data in sorted(files.items()):
        pieces = data if isinstance(data, list) else [data]
        ext[p] = []
        for piece in pieces:
            ext[p].append((lba, len(piece), piece))
            lba += (len(piece) + S - 1) // S
    enc_end = lba
    total = max(enc_end + 1, 40)
    img = bytearray(total * S)
    img[0:4] = struct.pack(">I", 2)
    img[8:16] = struct.pack(">II", 0, data_start - 1)
    img[16:24] = struct.pack(">II", enc_end, total - 1)
    img[S:S + 12] = b"PlayStation3"
    pvd = bytearray(S)
    pvd[0], pvd[1:6], pvd[6] = 1, b"CD001", 1
    pvd[80:84] = struct.pack("<I", total)
    pvd[156:190] = _rec(b"\0", dir_lba[""], S, 2)
    img[16 * S:17 * S] = pvd
    img[17 * S:17 * S + 6] = b"\xffCD001"
    for d in dirs:
        recs = [_rec(b"\0", dir_lba[d], S, 2), _rec(b"\1", dir_lba[d.rsplit("/", 1)[0] if "/" in d else ""], S, 2)]
        kids = sorted({p[len(d) + 1 if d else 0:].split("/")[0] for p in list(files) + dirs[1:]
                       if p != d and p.startswith(d + "/" if d else "")})
        for k in kids:
            full = (d + "/" if d else "") + k
            if full in dir_lba:
                recs.append(_rec(k, dir_lba[full], S, 2))
            else:
                pieces = ext[full]
                for i, (plba, plen, _) in enumerate(pieces):
                    name = (bad_name if bad_name and k == "EMPTY.DAT" else k + ";1")
                    recs.append(_rec(name, plba, plen, 0x80 if i < len(pieces) - 1 else 0))
        blob = b"".join(recs)
        assert len(blob) <= S
        img[dir_lba[d] * S:dir_lba[d] * S + len(blob)] = blob
    for pieces in ext.values():
        for plba, plen, piece in pieces:
            img[plba * S:plba * S + plen] = piece
    if key:
        for n in range(data_start, enc_end):
            sec = bytes(img[n * S:(n + 1) * S])
            if sec != bytes(S):
                iv = b"\0" * 12 + struct.pack(">I", n)
                img[n * S:(n + 1) * S] = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor().update(sec)
    return bytes(img)


def _zip(path, iso_name, data, extra=(), stored=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED) as z:
        z.writestr(iso_name, data)
        for n, d in extra:
            z.writestr(n, d)
    return path


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode())
    return path


def _keys(**named):
    d = rommlib.ps3_keys_dir()
    os.makedirs(d, exist_ok=True)
    for name, key in named.items():
        _write(os.path.join(d, name.replace("_", " ") + ".key"), key)
    return d


def _tree(root):
    out = {}
    for dp, _, fns in os.walk(root):
        for fn in fns:
            p = os.path.join(dp, fn)
            out[os.path.relpath(p, root).replace(os.sep, "/")] = open(p, "rb").read()
    return out


def _want():
    return {p: (b"".join(d) if isinstance(d, list) else d) for p, d in FILES.items()}


ON = {"ps3_decrypt": True}


# ---------------------------------------------------------------- the disc reader

def test_a_decrypted_image_becomes_a_folder_without_a_key(isolated, tmp_path):
    src = _write(str(tmp_path / "Lorem (USA).iso"), make_disc(key=None))
    assert rommlib.ps3_check(src, {}) == {"plain": True, "size": sum(len(v) for v in _want().values())}
    out = str(tmp_path / "Lorem (USA).ps3")
    assert rommlib.ps3_to_folder(src, out, {}) is None
    assert _tree(out) == _want()


def test_an_encrypted_zip_is_decrypted_with_its_key_read_in_place(isolated, tmp_path):
    _keys(Lorem_Quest_USA=OTHER, **{"Ipsum Quest (USA)": KEY})
    src = _zip(str(tmp_path / "Ipsum Quest (USA).zip"), "Ipsum Quest (USA).iso", make_disc(),
               extra=[("readme.txt", "notes")])
    assert rommlib.ps3_zip_is_disc(src)
    assert rommlib.ps3_check(src, ON)["key"] == "Ipsum Quest (USA)"
    out = str(tmp_path / "Ipsum Quest (USA).ps3")
    seen = []
    assert rommlib.ps3_to_folder(src, out, ON, progress=seen.append) == "Ipsum Quest (USA)"
    assert _tree(out) == _want()
    assert seen and seen[-1] == 1.0
    assert not [n for n in os.listdir(str(tmp_path)) if n.endswith(".iso")]     # no unzipped copy


def test_a_key_under_another_name_is_found_by_trying_it_on_the_disc(isolated, tmp_path):
    names = {"Dolor Racer (Europe)": OTHER, "Ipsum Quest - Deluxe (Japan)": KEY,
             "Sit Amet (USA)": bytes(16)}
    d = rommlib.ps3_keys_dir()
    os.makedirs(d)
    for n, k in names.items():
        _write(os.path.join(d, n + ".dkey"), k.hex().upper() + "\r\n")
    src = _write(str(tmp_path / "Ipsum Quest (USA) [scene].iso"), make_disc())
    assert rommlib.ps3_check(src, ON)["key"] == "Ipsum Quest - Deluxe (Japan)"
    keys = rommlib.ps3_keys()
    assert rommlib.ps3_key_order("Ipsum Quest (USA) [scene]", keys)[0] == "Ipsum Quest - Deluxe (Japan)"
    keys["ipsum quest (usa) [SCENE]"] = OTHER                   # an exact name (any case) comes first
    assert rommlib.ps3_key_order("Ipsum Quest (USA) [scene]", keys)[0] == "ipsum quest (usa) [SCENE]"
    assert sorted(rommlib.ps3_key_order("x", keys)) == sorted(keys)    # every key gets its turn


def test_encrypted_discs_need_the_setting_and_a_key_that_opens_them(isolated, tmp_path):
    src = _write(str(tmp_path / "Ipsum Quest (USA).iso"), make_disc())
    with pytest.raises(rommlib.Ps3Error, match="Decrypt PS3 discs.*games you own"):
        rommlib.ps3_check(src, {})
    with pytest.raises(rommlib.Ps3Error, match="no disc key for it.*game you own"):
        rommlib.ps3_check(src, ON)
    _keys(**{"Ipsum Quest (USA)": OTHER})                       # the right name, the wrong key
    with pytest.raises(rommlib.Ps3Error, match="no disc key"):
        rommlib.ps3_check(src, ON)
    assert not os.path.exists(str(tmp_path / "Ipsum Quest (USA).ps3"))


def test_key_files_in_every_form_and_junk_skipped(isolated):
    d = rommlib.ps3_keys_dir()
    os.makedirs(d)
    _write(os.path.join(d, "A (USA).key"), KEY)                       # Redump's 16 bytes
    _write(os.path.join(d, "B (USA).dkey"), OTHER.hex() + "\n")       # 32 hex digits
    _write(os.path.join(d, "C (USA).key"), b"short")                  # not a key
    with zipfile.ZipFile(os.path.join(d, "keys.zip"), "w") as z:
        z.writestr("D (Europe).key", KEY)
        z.writestr("notes.txt", "hello")
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w") as zi:                         # Redump's set: a zip per disc
            zi.writestr("E (Japan).dkey", KEY.hex())
        z.writestr("E (Japan).zip", inner.getvalue())
    _write(os.path.join(d, "broken.zip"), b"PK not really")
    keys = rommlib.ps3_keys()
    assert keys == {"A (USA)": KEY, "B (USA)": OTHER, "D (Europe)": KEY, "E (Japan)": KEY}
    _write(os.path.join(d, "F (USA).key"), OTHER)                     # a new file: read again
    assert rommlib.ps3_keys()["F (USA)"] == OTHER


def test_damaged_zip_or_disc_is_refused(isolated, tmp_path):
    _keys(**{"Ipsum Quest (USA)": KEY})
    src = _zip(str(tmp_path / "Ipsum Quest (USA).zip"), "Ipsum Quest (USA).iso", make_disc(), stored=True)
    data = bytearray(open(src, "rb").read())
    at = data.index(b"PlayStation3") + 30 * S                         # inside the disc image, past the files
    data[at] ^= 0xFF
    _write(src, bytes(data))
    with pytest.raises(rommlib.Ps3Error, match="damaged"):
        rommlib.ps3_to_folder(src, str(tmp_path / "out.ps3"), ON)
    notps3 = bytearray(make_disc(key=None))
    notps3[S:S + 12] = b"NotAPS3Disc!"
    with pytest.raises(rommlib.Ps3Error, match="not a PS3"):
        rommlib.ps3_check(_write(str(tmp_path / "x.iso"), bytes(notps3)), ON)
    cut = make_disc(key=None)[:-4 * S]
    with pytest.raises(rommlib.Ps3Error):
        rommlib.ps3_to_folder(_write(str(tmp_path / "cut.iso"), cut), str(tmp_path / "cut.ps3"), ON)
    two = _zip(str(tmp_path / "Two.zip"), "a.iso", b"x", extra=[("b.iso", b"y")])
    assert not rommlib.ps3_zip_is_disc(two)


def test_names_that_would_leave_the_folder_are_refused(isolated, tmp_path):
    src = _write(str(tmp_path / "Evil.iso"), make_disc(key=None, bad_name=b".."))
    with pytest.raises(rommlib.Ps3Error, match="unsafe name"):
        rommlib.ps3_check(src, {})


# ---------------------------------------------------------------- worker, download, unseen, RomM

@pytest.fixture
def worker(isolated, monkeypatch):
    import rb_chd
    monkeypatch.setattr(rb_chd, "running_game", lambda: "")
    monkeypatch.setattr(rommlib, "es_busy", lambda app_busy=False, get=None: "")   # nothing busy in ES
    monkeypatch.setattr(rb_chd.time, "sleep", lambda s: None)
    monkeypatch.setattr(rommlib, "keep_free_bytes", lambda cfg: 0)
    cfg = dict(rommlib.DEFAULT_CONFIG, ps3_decrypt=True)
    monkeypatch.setattr(rommlib, "load_config", lambda: dict(cfg))
    return rb_chd


def test_worker_turns_a_zipped_disc_into_a_game_folder(worker):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "ps3")
    _keys(**{"Ipsum Quest (USA)": KEY})
    src = _zip(os.path.join(sysdir, "Ipsum Quest (USA).zip"), "Ipsum Quest (USA).iso", make_disc())
    os.makedirs(os.path.join(sysdir, "Ipsum Quest (USA).ps3.part", "junk"))     # an interrupted run's
    lib = rommlib.Library()
    lib.mark({"id": 9, "name": "Ipsum Quest", "fs_name": "Ipsum Quest (USA).zip"}, "ps3", [src])
    assert worker.convert({"system": "ps3", "mode": "ps3", "sources": [src], "roots": [src]}) is True
    out = os.path.join(sysdir, "Ipsum Quest (USA).ps3")
    assert sorted(os.listdir(sysdir)) == ["Ipsum Quest (USA).ps3"]
    assert _tree(out) == _want()
    assert json.load(open(rommlib.STATE_PATH))["9"]["paths"] == [out]
    assert "disc key Ipsum Quest (USA)" in open(rommlib.LOG_PATH).read()


def test_worker_leaves_an_encrypted_disc_with_no_key_alone(worker):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "ps3")
    src = _zip(os.path.join(sysdir, "Ipsum Quest (USA).zip"), "Ipsum Quest (USA).iso", make_disc())
    assert worker.convert({"system": "ps3", "mode": "ps3", "sources": [src], "roots": [src]}) is False
    assert os.listdir(sysdir) == ["Ipsum Quest (USA).zip"]
    assert "no disc key" in worker._state["last"]


def test_a_downloaded_zip_stays_zipped_and_is_queued(isolated):
    cfg = dict(rommlib.DEFAULT_CONFIG)
    d = rommlib.Downloader(None, rommlib.Library(), cfg, persist=False)
    d.es = {"ps3": {".ps3", ".psn", ".iso", ".squashfs"}}
    src = _zip(os.path.join(rommlib.ROMS_ROOT, "ps3", "Lorem (USA).zip"), "Lorem (USA).iso", make_disc(key=None))
    j = rommlib.Job({"id": 7}, "ps3")
    assert d._postprocess_file(j, src) == [src]
    t = json.load(open(os.path.join(rommlib.CHD_QUEUE_DIR, j.chd_task)))
    assert t["mode"] == "ps3" and t["sources"] == [src]
    iso = _write(os.path.join(rommlib.ROMS_ROOT, "ps3", "Dolor (USA).iso"), make_disc(key=None))
    j2 = rommlib.Job({"id": 8}, "ps3")
    d._postprocess_file(j2, iso)                                       # a loose image: queued too
    assert json.load(open(os.path.join(rommlib.CHD_QUEUE_DIR, j2.chd_task)))["mode"] == "ps3"
    off = rommlib.Downloader(None, rommlib.Library(), dict(cfg, ps3_folders=False), persist=False)
    off.es = d.es
    j3 = rommlib.Job({"id": 9}, "ps3")
    src2 = _zip(os.path.join(rommlib.ROMS_ROOT, "ps3", "Amet (USA).zip"), "Amet (USA).iso", make_disc(key=None))
    assert off._postprocess_file(j3, src2) == [os.path.join(rommlib.ROMS_ROOT, "ps3", "Amet (USA).iso")]


def test_games_es_cant_see_explains_and_unpacks_ps3_zips(isolated, monkeypatch):
    es = {"ps3": {".ps3", ".psn", ".iso", ".squashfs"}}
    src = _zip(os.path.join(rommlib.ROMS_ROOT, "ps3", "Ipsum Quest (USA).zip"), "Ipsum Quest (USA).iso",
               make_disc())
    cfg = dict(rommlib.DEFAULT_CONFIG)
    monkeypatch.setattr(rommlib, "load_config", lambda: dict(cfg))
    f = rommlib.find_unseen(es)[0]
    assert f["kind"] == "archive" and "games you own" in f["why"]
    d = rommlib.Downloader(None, rommlib.Library(), cfg, persist=False)
    d.es = es
    with pytest.raises(rommlib.RommError, match="Decrypt PS3 discs"):
        rommlib.unpack_unseen(d, f)
    cfg["ps3_decrypt"] = True
    _keys(**{"Ipsum Quest (USA)": KEY})
    assert "its disc key is here" in rommlib.find_unseen(es)[0]["why"]
    d.cfg = cfg
    out, task = rommlib.unpack_unseen(d, f)
    assert out == [src] and json.load(open(os.path.join(rommlib.CHD_QUEUE_DIR, task)))["mode"] == "ps3"


class _Resp:
    def __init__(self, body, code=200):
        self.body, self.status_code = body, code

    def iter_content(self, n):
        yield self.body

    def close(self):
        pass


def test_disc_keys_are_copied_from_romms_ps3_firmware(isolated):
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("Ipsum Quest (USA).key", KEY)
    nokeys = io.BytesIO()
    with zipfile.ZipFile(nokeys, "w") as z:
        z.writestr("readme.txt", "no keys here")
    bodies = {1: inner.getvalue(), 2: b"firmware", 3: nokeys.getvalue(), 4: OTHER.hex().encode()}

    class Api(rommlib.Api):                     # the real download_firmware over a fake session
        base = "http://romm"

        def __init__(self):
            pass

        def firmware(self, pid):
            assert pid == 16
            return [{"id": 1, "file_name": "keys.zip", "file_size_bytes": len(bodies[1]), "md5_hash": None},
                    {"id": 2, "file_name": "PS3UPDAT.PUP"},
                    {"id": 3, "file_name": "more-keys.zip"},
                    {"id": 4, "file_name": "Dolor (USA).dkey"}]

        class s:
            @staticmethod
            def get(url, **kw):
                return _Resp(bodies[int(url.split("/api/firmware/")[1].split("/")[0])])
    assert [f["id"] for f in rommlib.ps3_key_files(Api(), 16)] == [1, 3, 4]       # never the firmware
    assert rommlib.fetch_ps3_keys(Api(), 16) == 2
    assert sorted(os.listdir(rommlib.ps3_keys_dir())) == ["Dolor (USA).dkey", "keys.zip"]


@pytest.fixture
def ui(isolated, rb, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    app = rb.App(None, headless=True)

    def bg(fn, done=None, err=None):                         # run background work inline
        try:
            res = fn()
        except rommlib.RommError as e:
            return (err or (lambda e: app.toast(str(e), rb.RED)))(e)
        if done:
            done(res)
    monkeypatch.setattr(app, "bg", bg)
    return app


def test_settings_turning_decrypt_on_copies_romms_keys(ui, rb, monkeypatch):
    app = ui
    app.platforms = [{"id": 16, "_system": "ps3", "name": "PlayStation 3"}]
    app.api = object()
    calls = []
    monkeypatch.setattr(rommlib, "fetch_ps3_keys", lambda api, pid: calls.append(pid) or 4771)
    st = rb.SettingsScreen(app)
    st._toggle_ps3_decrypt()
    assert app.cfg["ps3_decrypt"] is True and calls == [16]
    assert app.toasts[-1][0] == "4,771 disc keys ready in bios/ps3keys"
    st._toggle_ps3_decrypt()
    assert app.cfg["ps3_decrypt"] is False and calls == [16]


def test_planner_lists_ps3_discs_but_never_looks_inside_game_folders(isolated, monkeypatch):
    import rb_chd_plan
    monkeypatch.setattr(rommlib, "load_config", lambda: dict(rommlib.DEFAULT_CONFIG, ps3_decrypt=True))
    sysdir = os.path.join(rommlib.ROMS_ROOT, "ps3")
    _keys(**{"Ipsum Quest (USA)": KEY})
    good = _zip(os.path.join(sysdir, "Ipsum Quest (USA).zip"), "Ipsum Quest (USA).iso", make_disc())
    plain = _write(os.path.join(sysdir, "Lorem (USA).iso"), make_disc(key=None))
    nokey = _write(os.path.join(sysdir, "Dolor (USA).iso"), make_disc(key=OTHER))
    _write(os.path.join(sysdir, "Sit (USA).ps3", "PS3_GAME", "USRDIR", "data.zip"), b"game data")
    skipped = []
    cands = rb_chd_plan.candidates(["ps3"], skipped=skipped)
    assert sorted(c["files"][0] for c in cands) == sorted([good, plain])
    assert {c["mode"] for c in cands} == {"ps3"} and {c["label"] for c in cands} == {"PS3 folder"}
    assert [s[1] for s in skipped] == [nokey] and "no disc key" in skipped[0][2]
