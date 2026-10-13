"""How much room a download takes once unpacked: the zip's real unpacked
size when RomM lists its members, a Wii U disc image's fixed size when it
doesn't, and the rough 2.2x factor only when nothing says."""
import pytest

import rommlib

GB = 1 << 30
WUD = 25_025_314_816


def _zip(name, size, members=None):
    f = {"category": "game", "file_name": name, "file_size_bytes": size}
    if members is not None:
        f["archive_members"] = [{"name": n, "size": s} for n, s in members]
    return f


def _rom(size, files=None, fs_name="Lorem Odyssey (USA).zip"):
    r = {"id": 1, "fs_name": fs_name, "fs_size_bytes": size}
    if files is not None:
        r["files"] = files
    return r


def test_a_zipped_wii_u_disc_unpacks_to_a_whole_disc():
    rom = _rom(3 * GB, [_zip("Lorem Odyssey (USA).zip", 3 * GB)])     # RomM lists no members for Wii U
    assert rommlib.unpacked_size(rom, "wiiu") == WUD
    assert rommlib.space_needed(rom, "wiiu") == 3 * GB + WUD             # zip and disc side by side
    assert rommlib.space_needed(rom, "wiiu") > int(2.2 * 3 * GB)        # the old guess was far too low


def test_a_queued_wii_u_zip_without_a_file_list_counts_a_whole_disc():
    rom = _rom(2 * GB)                                                   # a list record: name only
    assert rommlib.space_needed(rom, "wiiu") == 2 * GB + WUD
    j = rommlib.Job(rom, "wiiu")
    assert rommlib.queue_forecast([j], 100 * GB, 0)["need"] == 2 * GB + WUD


def test_listed_members_give_the_real_size():
    rom = _rom(GB, [_zip("Lorem.zip", GB, [("Lorem.cue", 200), ("Lorem.bin", 700 << 20)])])
    assert rommlib.unpacked_size(rom, "psx") == 200 + (700 << 20)
    assert rommlib.space_needed(rom, "psx") == GB + 200 + (700 << 20)
    wux = _rom(5 * GB, [_zip("Ipsum.zip", 5 * GB, [("Ipsum.wux", 6 * GB)])])
    assert rommlib.unpacked_size(wux, "wiiu") == 6 * GB                 # a .wux's own size, never the disc's


def test_unknown_contents_keep_the_rough_factor():
    rom = _rom(GB, [_zip("Sit.zip", GB)])
    assert rommlib.unpacked_size(rom, "psx") is None
    assert rommlib.space_needed(rom, "psx") == int(2.2 * GB)
    some = _rom(GB, [_zip("A.zip", GB // 2, [("A.iso", GB)]), _zip("B.zip", GB // 2)])
    assert rommlib.unpacked_size(some, "psx") is None                   # one zip unlisted: not known
    assert rommlib.space_needed({"fs_size_bytes": GB}, "psx") == int(2.2 * GB)


@pytest.mark.parametrize("system", ["wiiu", "psx"])
def test_loose_files_need_their_own_size(system):
    rom = _rom(4 * GB, [{"category": "game", "file_name": "Amet.wud", "file_size_bytes": 4 * GB}], "Amet.wud")
    assert rommlib.unpacked_size(rom, system) is None
    assert rommlib.space_needed(rom, system) == 4 * GB
    assert rommlib.space_needed(_rom(4 * GB, fs_name="Amet.iso"), system) == 4 * GB


def test_carts_and_zip_native_systems_are_not_unpacked():
    assert rommlib.space_needed(_rom(GB), "snes") == GB
    assert rommlib.unpacked_size(_rom(GB, [_zip("x.zip", GB, [("x.bin", 2 * GB)])]), "mame") is None


def test_the_wii_u_disc_layout_and_prompt_say_a_whole_disc():
    detail = _rom(3 * GB, [_zip("Lorem Odyssey (USA).zip", 3 * GB)])
    lay = rommlib.disc_layout(detail, "wiiu", {"chd": True})
    assert lay["unpacked"] == WUD and lay["estimated"]


def test_the_download_prompt_uses_the_whole_disc(isolated, rb, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    app = rb.App(None, headless=True)
    app.platforms = [{"id": 7, "name": "Wii U", "_system": "wiiu"}]
    app.platform_by_id = {7: app.platforms[0]}
    rom = dict(_rom(3 * GB, [_zip("Lorem Odyssey (USA).zip", 3 * GB)]), platform_id=7, name="Lorem Odyssey")
    app.details[1] = {"files": rom["files"], "fs_size_bytes": 3 * GB}
    assert "about 23.3 GB once unpacked" in app.plan_text(rom, "wiiu")
    app.details.clear()
    app.detail_busy = True                         # record not here yet: the name alone
    assert "about 23.3 GB once unpacked" in app.plan_text(dict(rom, fs_extension="zip"), "wiiu")
