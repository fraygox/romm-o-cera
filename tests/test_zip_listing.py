"""What an archive RomM listed no members for really unpacks to: its member
list read from the server with ranged GETs (the zip's central directory, at
its end), served here by tools/mock_romm.py's Range-aware download routes.
A Wii U zip whose listing can't be read still counts as a whole disc, "up to"."""
import importlib.util
import io
import os
import struct
import threading
import zipfile
from http.server import ThreadingHTTPServer

import pytest

import rommlib

GB = 1 << 30
WUD = 25_025_314_816
APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _mock_module():
    spec = importlib.util.spec_from_file_location("mock_romm_inproc", os.path.join(APP, "tools", "mock_romm.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Sparse:
    """A multi-GB download that is zeros but for its tail (a zip's directory):
    sliceable like bytes, so the mock serves it without holding it."""

    def __init__(self, size, tail):
        self.size, self.tail, self.tail_at = size, tail, size - len(tail)

    def __len__(self):
        return self.size

    def __getitem__(self, sl):
        start, stop = sl.start or 0, min(sl.stop, self.size)
        head = b"\0" * max(0, min(stop, self.tail_at) - start)
        return head + self.tail[max(start, self.tail_at) - self.tail_at:max(stop, self.tail_at) - self.tail_at]


def sparse_zip(members, size):
    """A zip of `size` bytes whose central directory lists members
    [(name, compressed, uncompressed)], laid out one after another from 0
    (Zip64 fields where a value doesn't fit 32 bits, as real tools write)."""
    big = 0xFFFFFFFF
    cd, off = b"", 0
    for name, comp, full in members:
        n = name.encode()
        extra = b""
        vals = [full if full >= big else None, comp if comp >= big else None, off if off >= big else None]
        if any(v is not None for v in vals):
            body = b"".join(struct.pack("<Q", v) for v in vals if v is not None)
            extra = struct.pack("<HH", 1, len(body)) + body
        cd += struct.pack("<4s4B4HL2L5H2L", b"PK\x01\x02", 45, 3, 45, 0, 0, 8, 0, 0x21, 0,
                          min(comp, big), min(full, big), len(n), len(extra), 0, 0, 0, 0, min(off, big)) + n + extra
        off += 30 + len(n) + comp
    zip64 = off >= big or any(c >= big or f >= big for _, c, f in members)
    tail_len = len(cd) + 22 + (56 + 20 if zip64 else 0)
    cd_at = size - tail_len
    assert cd_at >= off, "members don't fit the size"
    tail = cd
    if zip64:
        rec_at = cd_at + len(cd)
        tail += struct.pack("<4sQ2H2L4Q", b"PK\x06\x06", 44, 45, 45, 0, 0, len(members), len(members), len(cd), cd_at)
        tail += struct.pack("<4sLQL", b"PK\x06\x07", 0, rec_at, 1)
        tail += struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, 0xFFFF, 0xFFFF, big, big, 0)
    else:
        tail += struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, len(members), len(members), len(cd), cd_at, 0)
    return Sparse(size, tail)


def real_zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries:
            z.writestr(name, data)
    return buf.getvalue()


@pytest.fixture
def server():
    """tools/mock_romm.py in this process, serving the roms a test adds:
    add(rom_id, fs_name, data, files=None) -> the rom's list record."""
    mod = _mock_module()
    ranges = {"on": True, "seen": []}

    class H(mod.Handler):
        roms, by_id, files, rate = [], {}, {}, 1e12

        def _stream(self, size, data=None):
            ranges["seen"].append(self.headers.get("Range"))
            if not ranges["on"]:
                del self.headers["Range"]          # a server without Range: the whole file, 200
            return super()._stream(size, data)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    api = rommlib.Api("http://127.0.0.1:%d" % srv.server_port, "x")

    def add(rid, fs_name, data, files=None):
        r = {"id": rid, "platform_id": 1, "platform_slug": "x", "name": "Lorem %d" % rid, "fs_name": fs_name,
             "fs_name_no_ext": os.path.splitext(fs_name)[0], "fs_extension": fs_name.rsplit(".", 1)[-1],
             "fs_size_bytes": len(data), "_data": data}
        if files is not None:
            r["files"] = files
            r["fs_size_bytes"] = sum(f["file_size_bytes"] for f in files)
            H.files.update({f["id"]: f for f in files})
        H.by_id[rid] = r
        return {k: v for k, v in r.items() if k not in ("_data", "files")}
    add.api, add.ranges = api, ranges
    yield add
    srv.shutdown()


def test_a_small_wux_zip_unpacks_to_its_own_size(server):
    wux = 6 * GB + 123
    rom = server(1, "Lorem Wux (USA).zip", sparse_zip([("Lorem Wux (USA).wux", 6 * GB, wux)], 6 * GB + 4096))
    assert rommlib.needs_listing(rom, "wiiu") == ["Lorem Wux (USA).zip"]
    assert rommlib.unpacked_upper_bound(rom, "wiiu")                  # before the listing: a whole disc
    got = rommlib.zip_listing(server.api, rom, "wiiu")
    assert got == {"Lorem Wux (USA).zip": [{"name": "Lorem Wux (USA).wux", "size": wux}]}
    assert rommlib.unpacked_size(rom, "wiiu") == wux and not rommlib.unpacked_upper_bound(rom, "wiiu")
    assert rommlib.space_needed(rom, "wiiu") == rom["fs_size_bytes"] + wux
    assert all(r and r.startswith("bytes=") and not r.endswith("-") for r in server.ranges["seen"])
    assert len(server.ranges["seen"]) <= 3                            # the tail (+ the directory)
    fresh = {"id": 1, "fs_name": rom["fs_name"], "fs_size_bytes": rom["fs_size_bytes"]}
    assert rommlib.unpacked_size(fresh, "wiiu") == wux                # a record fetched again finds it


def test_a_zipped_wud_is_a_whole_disc_however_small_the_zip(server):
    rom = server(2, "Ipsum Disc (Europe).zip", sparse_zip([("Ipsum Disc (Europe).wud", 3 * GB, WUD)], 3 * GB + 4096))
    rommlib.zip_listing(server.api, rom, "wiiu")
    assert rommlib.unpacked_size(rom, "wiiu") == WUD and not rommlib.unpacked_upper_bound(rom, "wiiu")


def test_a_folder_dump_zip_lists_its_files_not_its_folders(server):
    data = real_zip([("Dolor/code/app.xml", b"<app/>" * 50), ("Dolor/content/data.bin", os.urandom(4000)),
                     ("Dolor/meta/meta.xml", b"<meta/>"), ("Dolor/meta/", b"")])
    files = [{"id": 31, "file_name": "Dolor (USA).zip", "file_size_bytes": len(data), "category": "game",
              "_data": data}]
    server(3, "Dolor (USA).zip", data, files)
    detail = server.api.rom(3)                     # the full record, as a download starts with
    assert "_data" not in detail["files"][0]
    got = rommlib.zip_listing(server.api, detail, "wiiu")["Dolor (USA).zip"]
    assert sorted(m["name"] for m in got) == ["Dolor/code/app.xml", "Dolor/content/data.bin", "Dolor/meta/meta.xml"]
    assert rommlib.unpacked_size(detail, "wiiu") == 300 + 4000 + 7
    lay = rommlib.disc_layout(detail, "wiiu", {"chd": True})
    assert lay is None or not lay["estimated"]


def test_each_archive_of_a_folder_rom_is_read_from_its_own_file(server):
    a = real_zip([("Sit (Disc 1).iso", b"a" * 5000)])
    b = real_zip([("Sit (Disc 2).iso", b"b" * 7000)])
    files = [{"id": 41, "file_name": "Sit (Disc 1).zip", "file_size_bytes": len(a), "category": "game", "_data": a},
             {"id": 42, "file_name": "Sit (Disc 2).zip", "file_size_bytes": len(b), "category": "game", "_data": b,
              "archive_members": [{"name": "Sit (Disc 2).iso", "size": 7000}]}]       # RomM listed this one
    server(4, "Sit", b"", files)
    detail = server.api.rom(4)
    assert rommlib.needs_listing(detail, "psx") == ["Sit (Disc 1).zip"]
    rommlib.zip_listing(server.api, detail, "psx")
    assert rommlib.unpacked_size(detail, "psx") == 12000
    lay = rommlib.disc_layout(detail, "psx", {"chd": True})
    assert lay["unpacked"] == 12000 and not lay["estimated"] and lay["discs"] == 2


def test_a_zip64_archive_lists_sizes_past_4_gib(server):
    members = [("Amet/part1.bin", 5 * GB, 5 * GB), ("Amet/part2.bin", 2 * GB, 9 * GB)]   # part2 starts past 4 GiB
    rom = server(5, "Amet (USA).zip", sparse_zip(members, 7 * GB + 8192))
    got = rommlib.zip_listing(server.api, rom, "ps3")["Amet (USA).zip"]
    assert got == [{"name": "Amet/part1.bin", "size": 5 * GB}, {"name": "Amet/part2.bin", "size": 9 * GB}]
    assert rommlib.space_needed(rom, "ps3") == rom["fs_size_bytes"] + 14 * GB


def test_a_listing_that_cant_be_read_falls_back(server):
    data = sparse_zip([("Elit.wux", GB, GB)], GB + 4096)
    server.ranges["on"] = False                     # the server ignores Range: never pull the whole zip
    rom = server(6, "Elit (USA).zip", data)
    assert rommlib.zip_listing(server.api, rom, "wiiu") is False
    assert rommlib.unpacked_size(rom, "wiiu") == WUD and rommlib.unpacked_upper_bound(rom, "wiiu")
    assert rommlib.needs_listing(rom, "wiiu") == []                   # tried once; not again
    server.ranges["on"] = True
    junk = server(7, "Elit (Japan).zip", b"not a zip at all" * 100)
    assert rommlib.zip_listing(server.api, junk, "psx") is False
    assert rommlib.space_needed(junk, "psx") == int(2.2 * junk["fs_size_bytes"])   # the old guess stands
    seven = server(8, "Elit (Korea).7z", b"7z" * 100)
    assert rommlib.zip_listing(server.api, seven, "wiiu") is False and rommlib.unpacked_size(seven, "wiiu") == WUD


def test_a_network_error_keeps_nothing_so_it_is_read_again():
    api = rommlib.Api("http://127.0.0.1:9", "x")
    rom = {"id": 9, "fs_name": "Tempor.zip", "fs_size_bytes": GB}
    with pytest.raises(rommlib.NetError):
        rommlib.zip_listing(api, rom, "wiiu")
    assert "_members" not in rom and rommlib.needs_listing(rom, "wiiu") == ["Tempor.zip"]


def test_a_download_starts_with_room_for_the_wux_not_a_whole_disc(isolated, server, monkeypatch):
    """6 GB .wux zip, 20 GB free (nothing kept free): it starts instead of waiting for 25 GB."""
    rom = server(10, "Lorem Wux (USA).zip", sparse_zip([("Lorem Wux (USA).wux", 6 * GB, 6 * GB)], 6 * GB + 4096))
    lib = rommlib.Library()
    d = rommlib.Downloader(server.api, lib, dict(rommlib.DEFAULT_CONFIG, keep_free_gb=0), persist=False)
    monkeypatch.setattr(d, "_free_bytes", lambda p: 20 * GB)

    class Started(Exception):
        pass

    def fetch(*a, **k):
        raise Started()
    monkeypatch.setattr(d, "_fetch", fetch)
    j = rommlib.Job(dict(rom), "wiiu")
    with pytest.raises(Started):
        d._process(j)
    assert j.rom["_members"]["Lorem Wux (USA).zip"][0]["size"] == 6 * GB
    monkeypatch.setattr(d, "_free_bytes", lambda p: 10 * GB)          # really too little: it waits
    with pytest.raises(rommlib.NoSpace):
        d._process(rommlib.Job(dict(rom), "wiiu"))


def test_the_forecast_reads_waiting_jobs_listings_in_the_background(isolated, server):
    rom = server(11, "Ipsum Wux (USA).zip", sparse_zip([("Ipsum Wux (USA).wux", 6 * GB, 6 * GB + 8192)], 6 * GB + 4096))
    d = rommlib.Downloader(server.api, rommlib.Library(), dict(rommlib.DEFAULT_CONFIG), persist=False)
    d.hold_all()                                   # nothing downloads; the listing still comes
    j = d.enqueue(rom, "wiiu")
    import time
    for _ in range(200):
        if rommlib.needs_listing(j.rom, "wiiu") == []:
            break
        time.sleep(0.02)
    assert j.listing_tried and j.rom["_members"]
    assert rommlib.queue_forecast([j], 100 * GB, 0)["need"] == rom["fs_size_bytes"] + 6 * GB + 8192
    assert len(server.ranges["seen"]) <= 3                            # once per job


def test_the_prompt_says_up_to_a_whole_disc_until_the_listing_is_read(isolated, rb, server, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    app = rb.App(None, headless=True)
    app.platforms = [{"id": 7, "name": "Wii U", "_system": "wiiu"}]
    app.platform_by_id = {7: app.platforms[0]}
    rom = dict(server(12, "Dolor Wux (USA).zip", sparse_zip([("Dolor Wux (USA).wux", 4 * GB, 5 * GB)], 4 * GB + 4096)),
               platform_id=7)
    app.detail_busy = True                         # record not here yet: the name alone
    assert "up to 23.3 GB once unpacked" in app.plan_text(rom, "wiiu")
    rommlib.zip_listing(server.api, dict(rom), "wiiu")   # the downloader read it (another copy of the record)
    text = app.plan_text(rom, "wiiu")
    assert "5.0 GB once unpacked" in text and "about" not in text and "up to" not in text


def test_a_listing_read_under_the_roms_name_serves_its_full_record():
    """The forecast reads a list record (the rom's name only); the download's
    full record names the zip's file, maybe spelled differently."""
    listed = {"Lorem (USA).zip": [{"name": "Lorem (USA).wux", "size": 5 * GB}]}
    detail = {"id": 13, "fs_name": "Lorem (USA).zip", "fs_size_bytes": 4 * GB, "_members": listed,
              "files": [{"category": "game", "file_name": "Lorem (USA) [v16].zip", "file_size_bytes": 4 * GB}]}
    assert rommlib.unpacked_size(detail, "wiiu") == 5 * GB and rommlib.needs_listing(detail, "wiiu") == []
    assert rommlib.disc_layout(detail, "wiiu", {"chd": True})["unpacked"] == 5 * GB
