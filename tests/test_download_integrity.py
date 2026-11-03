"""A download is never "Ready to play" when it isn't the game: a sign-in or
proxy page answering instead of RomM is never written or resumed onto, a zip
kept as it is gets its CRCs read when RomM can't vouch for it, a job that
starts again drops its "Waiting for space" line, the space margin with no
keep-free reserve is small for a small game, and an archive member outside its
folder (or with Windows backslashes) is named and handled the same way."""
import io
import os
import threading
import zipfile

import pytest

import rommlib


class _Resp:
    def __init__(self, body, status=200, headers=None):
        self.body, self.status_code = body, status
        self.headers = {"Content-Type": "application/octet-stream"} if headers is None else headers
        self.closed = False

    def iter_content(self, n):
        for i in range(0, len(self.body), n or 1):
            yield self.body[i:i + n]

    def close(self):
        self.closed = True


class _Api:
    base = "http://romm"

    def __init__(self, answers, detail=None):
        self.answers, self.asked, self.detail = list(answers), [], detail
        self.s = self

    def rom(self, rid):
        return self.detail

    def get(self, url, headers=None, **k):
        self.asked.append(dict(headers or {}))
        return self.answers.pop(0)


def _downloader(api=None):
    d = rommlib.Downloader.__new__(rommlib.Downloader)     # no worker thread
    d.api, d.cfg, d.es, d.unopenable = api, dict(rommlib.DEFAULT_CONFIG), {}, {}
    d.library, d.persist, d.qlock, d.cv = rommlib.Library(), False, threading.Lock(), threading.Condition()
    d.hold, d.rate_limit, d.unpacking_bytes, d.jobs, d._closed = False, 0, 0, [], False
    return d


def _w(p, data):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    return p


def _zip_bytes(members, stored=True):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED) as z:
        for n, data in members.items():
            z.writestr(n, data)
    return buf.getvalue()


def _zip(path, members):
    return _w(path, _zip_bytes(members))


GAME = _zip_bytes({"Invented Cart (World).gba": bytes(range(256)) * 400})
PAGE = b"<html><body>Please sign in</body></html>"
REC = {"file_name": "Invented Cart (World).zip", "file_size_bytes": len(GAME)}


def _dest(system="gba"):
    return os.path.join(rommlib.ROMS_ROOT, system, "Invented Cart (World).zip")


# ------------------------------------------------------------ 1. a web page instead of the game

@pytest.mark.parametrize("page", [
    _Resp(PAGE, headers={"Content-Type": "text/html; charset=utf-8"}),        # said so
    _Resp(b"\n<!DOCTYPE html><html>" + b" " * 100, headers={}),                # didn't say
])
def test_a_web_page_is_never_written_to_the_part(isolated, page):
    dest = _dest()
    os.makedirs(os.path.dirname(dest))
    api = _Api([page, _Resp(GAME)])
    d, j = _downloader(api), rommlib.Job({"id": 1, "name": "G"}, "gba")
    with pytest.raises(rommlib.NetError) as e:
        d._fetch(j, "/g", dest, len(GAME), REC)
    assert "web page" in str(e.value) and "login or proxy" in str(e.value)
    assert not os.path.exists(dest + ".part") and not os.path.exists(dest)
    d._fetch(j, "/g", dest, len(GAME), REC)              # the next try: from the start
    assert api.asked[1] == {}
    assert open(dest, "rb").read() == GAME


def test_a_part_that_is_a_web_page_is_never_resumed_onto(isolated):
    dest = _dest()
    _w(dest + ".part", PAGE[:39])                         # what an older version kept
    api = _Api([_Resp(GAME)])
    _downloader(api)._fetch(rommlib.Job({"id": 1, "name": "G"}, "gba"), "/g", dest, len(GAME), REC)
    assert api.asked == [{}]                              # no Range: bytes=39-
    with zipfile.ZipFile(dest) as z:
        assert z.testzip() is None


def test_a_web_page_answering_a_resume_keeps_the_part(isolated):
    dest = _dest()
    _w(dest + ".part", GAME[:1000])
    api = _Api([_Resp(PAGE, 200, {"Content-Type": "text/html"})])
    with pytest.raises(rommlib.NetError):
        _downloader(api)._fetch(rommlib.Job({"id": 1, "name": "G"}, "gba"), "/g", dest, len(GAME), REC)
    assert api.asked == [{"Range": "bytes=1000-"}]
    assert open(dest + ".part", "rb").read() == GAME[:1000]


def test_a_web_page_that_keeps_coming_says_so(isolated):
    dest = _dest()
    os.makedirs(os.path.dirname(dest))
    pages = [_Resp(PAGE, headers={"Content-Type": "text/html"}) for _ in rommlib.RETRY_DELAYS + (0,)]
    d, j = _downloader(_Api(pages)), rommlib.Job({"id": 1, "name": "G"}, "gba")
    for _ in pages:
        try:
            d._fetch(j, "/g", dest, len(GAME), REC)
        except rommlib.NetError as e:
            d._failed(j, e)
    assert j.state == rommlib.Job.FAILED
    assert j.message == rommlib.WEB_PAGE
    assert "stopped early" not in j.message


def test_a_game_file_that_is_a_web_page_still_downloads(isolated):
    dest = os.path.join(rommlib.ROMS_ROOT, "ports", "Invented.html")
    os.makedirs(os.path.dirname(dest))
    api = _Api([_Resp(PAGE, headers={"Content-Type": "text/html"})])
    _downloader(api)._fetch(rommlib.Job({"id": 1, "name": "G"}, "ports"), "/g", dest, len(PAGE), None)
    assert open(dest, "rb").read() == PAGE


# ------------------------------------------------------------ 2. a damaged zip kept as it is

def _flipped():
    b = bytearray(GAME)
    b[len(b) // 2] ^= 0xFF                                # inside the member's data: same length
    return bytes(b)


@pytest.mark.parametrize("system", ["mame", "gba"])     # arcade (never unpacked), a cart zip the core reads
def test_a_zip_kept_as_is_with_a_bad_crc_is_damaged(isolated, system):
    path = _w(_dest(system), _flipped())
    d = _downloader()
    d.es = {system: {".zip"}}
    j = rommlib.Job({"id": 1, "name": "G"}, system)
    j.made, j.verified = [path], False                     # no RomM checksum vouched for it
    with pytest.raises(rommlib.DamagedArchive) as e:
        d._post(j, {"kind": "file", "path": path, "heavy": False})
    d._failed(j, e.value)
    assert j.state == rommlib.Job.FAILED and "the download is damaged" in j.message
    assert not os.path.exists(path) and path not in j.made   # Retry downloads it again


def test_a_whole_zip_kept_as_is_is_ready(isolated):
    path = _w(_dest("mame"), GAME)
    d = _downloader()
    j = rommlib.Job({"id": 1, "name": "G", "fs_name": os.path.basename(path)}, "mame")
    j.made, j.verified = [path], False
    assert d._postprocess_file(j, path) == [path]


def test_a_zip_romm_vouched_for_isnt_read_again(isolated, monkeypatch):
    path = _w(_dest("mame"), GAME)
    monkeypatch.setattr(rommlib, "zip_crc_bad", lambda *a, **k: pytest.fail("read again"))
    j = rommlib.Job({"id": 1, "name": "G"}, "mame")
    j.verified = True
    assert _downloader()._postprocess_file(j, path) == [path]


def test_the_crc_read_stops_on_a_pause(isolated):
    path = _w(_dest("mame"), GAME)
    stop = threading.Event()
    stop.set()
    with pytest.raises(rommlib.Cancelled):
        rommlib.zip_crc_bad(path, stop)
    assert rommlib.zip_crc_bad(path) is None
    assert rommlib.zip_crc_bad(_w(path, b"\0" * 500)) == ""


# ------------------------------------------------------------ 3, 4. space

def _space_job(d, size, free, monkeypatch, message="Waiting for space: free 1.2 GB more"):
    detail = {"id": 1, "name": "Invented Cart", "fs_name": "Invented Cart (World).gba", "fs_size_bytes": size,
              "files": [{"file_name": "Invented Cart (World).gba", "file_size_bytes": size}]}
    d.api = _Api([], detail)
    monkeypatch.setattr(d, "_free_bytes", lambda p: free)
    seen = {}

    def fetch(j, url, dest, size_hint, rec):
        seen.update(message=j.message, short=j.short_space, state=j.state)
    monkeypatch.setattr(d, "_fetch_new", fetch)
    j = rommlib.Job(dict(detail), "gba")
    j.short_space, j.message = 1 << 30, message
    return j, seen


def test_a_job_that_starts_again_drops_its_waiting_for_space_line(isolated, monkeypatch):
    d = _downloader()
    j, seen = _space_job(d, 600 << 10, 100 << 30, monkeypatch)
    d._process(j)
    assert seen == {"message": "", "short": 0, "state": rommlib.Job.DOWNLOADING}


@pytest.mark.parametrize("free, fits", [(50 << 20, True), (20 << 20, False)])
def test_keep_free_0_leaves_a_small_margin_for_a_small_game(isolated, monkeypatch, free, fits):
    d = _downloader()
    d.cfg["keep_free_gb"] = 0
    j, seen = _space_job(d, 600 << 10, free, monkeypatch)
    if fits:
        d._process(j)
        assert seen["state"] == rommlib.Job.DOWNLOADING
    else:
        with pytest.raises(rommlib.NoSpace) as e:
            d._process(j)
        assert e.value.short < 32 << 20                    # not "free 2.0 GB more"


def test_the_margin_is_proportional_and_capped():
    m = rommlib.space_margin
    assert m(600 << 10) == 32 << 20
    assert m(4 << 30) == (4 << 30) // 20
    assert m(500 << 30) == rommlib.SPACE_MARGIN


def test_the_keep_free_reserve_replaces_the_margin(isolated, monkeypatch):
    d = _downloader()
    d.cfg["keep_free_gb"] = 1
    j, _ = _space_job(d, 600 << 10, 1 << 30, monkeypatch)
    with pytest.raises(rommlib.NoSpace):
        d._process(j)


# ------------------------------------------------------------ 5. members outside the folder, backslashes

@pytest.mark.parametrize("member, shown", [
    ("../../escape.iso", "../../escape.iso"),
    ("/tmp/abs.iso", "/tmp/abs.iso"),
    ("..\\escape.iso", "../escape.iso"),
    ("C:\\escape.iso", "C:/escape.iso"),
])
def test_a_member_outside_its_folder_is_named(isolated, member, shown):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "ps2")
    arc = _zip(os.path.join(sysdir, "Invented.zip"), {member: b"x" * 2048})
    d = _downloader()
    d.es = {"ps2": {".iso"}}
    finding = {"system": "ps2", "path": arc}
    with pytest.raises(rommlib.RommError) as e:
        rommlib.unpack_unseen(d, finding)
    assert str(e.value) == "The archive has a file outside its folder (%s); left as it is" % shown
    assert os.listdir(sysdir) == ["Invented.zip"]


def _no_backslash(root):
    for dp, dns, fns in os.walk(root):
        assert not [n for n in dns + fns if "\\" in n], (dp, dns, fns)


def test_backslashes_are_folders_when_unpacking_a_zip(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "ps2")
    arc = _zip(os.path.join(sysdir, "Invented.zip"), {"Sub\\Back (World).iso": b"x" * 2048})
    d = _downloader()
    d.es = {"ps2": {".iso"}}
    assert rommlib.archive_contents(d, arc) == [("Sub/Back (World).iso", 2048)]
    out, _ = rommlib.unpack_unseen(d, {"system": "ps2", "path": arc})
    assert out == [os.path.join(sysdir, "Sub")]
    assert open(os.path.join(sysdir, "Sub", "Back (World).iso"), "rb").read() == b"x" * 2048
    _no_backslash(rommlib.ROMS_ROOT)


def test_backslashes_are_folders_when_7z_unpacks(isolated, monkeypatch):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "ps2")
    arc = _w(os.path.join(sysdir, "Invented.7z"), b"7z stand-in")
    d = _downloader()

    def run_7z(path, stage, j):                            # 7z keeps the backslash in the name
        _w(os.path.join(stage, "Sub\\Back (World).iso"), b"y" * 100)
    monkeypatch.setattr(d, "_run_7z", run_7z)
    monkeypatch.setattr(d, "_list_archive", lambda p: ["Sub/Back (World).iso"])
    d._extract(arc, sysdir, rommlib.Job({"id": 1, "name": "G"}, "ps2"))
    assert open(os.path.join(sysdir, "Sub", "Back (World).iso"), "rb").read() == b"y" * 100
    _no_backslash(rommlib.ROMS_ROOT)
