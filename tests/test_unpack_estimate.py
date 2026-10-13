"""How much room a download takes once unpacked: the zip's real unpacked
size when RomM lists its members, at most a Wii U disc image's fixed size
when it doesn't (until the zip's own listing is read: test_zip_listing.py),
and the rough 2.2x factor only when nothing says."""
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
    assert lay["unpacked"] == WUD and lay["estimated"] and lay["upto"]


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
    assert "up to 23.3 GB once unpacked" in app.plan_text(rom, "wiiu")
    app.details.clear()
    app.detail_busy = True                         # record not here yet: the name alone
    assert "up to 23.3 GB once unpacked" in app.plan_text(dict(rom, fs_extension="zip"), "wiiu")


def _known(rid, zipped, unpacked):
    """A zip whose contents RomM listed: zipped bytes, unpacking to unpacked."""
    return _rom(zipped, [_zip("G%d.zip" % rid, zipped, [("G%d.wux" % rid, unpacked)])]) | {"id": rid}


def test_the_forecast_counts_one_zip_beside_its_contents_not_every_zip():
    """Each zip is deleted once unpacked: the peak is the biggest zip plus what
    every game unpacks to, not every zip and its contents added up."""
    games = [(_known(1, 6 * GB, 6 * GB), "wiiu"), (_known(2, 3 * GB, WUD), "wiiu"), (_known(3, 2 * GB, 4 * GB), "psx")]
    peak = 6 * GB + (6 * GB + WUD + 4 * GB)
    assert rommlib.space_for(games) == peak
    assert rommlib.queue_forecast([rommlib.Job(r, s) for r, s in games], 0, 0, chd=False)["need"] == peak
    assert peak < sum(rommlib.space_needed(r, s) for r, s in games)          # the old sum
    j = rommlib.Job(*games[2])
    j.done_bytes = GB                                                        # half of its zip is here
    assert rommlib.queue_forecast([j], 0, 0, chd=False)["need"] == 4 * GB - GB + 2 * GB
    # a CHD conversion starts from the unpacked files; the zip went before it
    assert rommlib.queue_forecast([rommlib.Job(*games[2])], 0, 0)["need"] == 2 * GB + 4 * GB + 4 * GB
    fit, used = rommlib.fit_in_space(games, free=peak + 2 * GB, reserve=2 * GB)
    assert fit == games and used == peak


def test_the_forecast_never_under_estimates_the_real_peak():
    """Disk use over the queue's run, the next download overlapping each unpack
    (one unpacking, one waiting, one downloading), never exceeds the forecast,
    including zips bigger than their contents."""
    import random
    rng = random.Random(5)
    for _ in range(300):
        games = []
        for rid in range(rng.randint(1, 6)):
            z = rng.randint(1, 30) * GB // 4
            u = max(1, int(z * rng.choice([0.9, 1.0, 1.01, 2.5, 8.0])))
            games.append((_known(rid, z, u), "wiiu"))
        zs = [r["fs_size_bytes"] for r, _ in games]
        us = [rommlib.unpacked_size(r, "wiiu") for r, _ in games]
        worst = 0
        for k in range(len(games)):          # game k unpacks; k+1 waits; k+2 downloads
            use = sum(us[:k]) + zs[k] + us[k] + sum(zs[k + 1:k + 3])
            worst = max(worst, use)
        fc = rommlib.queue_forecast([rommlib.Job(r, s) for r, s in games], 0, 0, chd=False)["need"]
        assert worst <= fc == rommlib.space_for(games)
