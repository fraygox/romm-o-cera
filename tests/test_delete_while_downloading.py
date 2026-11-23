"""Deleting games while others download: from Free up space (the app's own games, one put here
another way, one in a subfolder) in the systems being downloaded to. The downloads finish whole
with their records, the deleted games and their records go, and Clean up leftovers never offers
what's being downloaded, during or after."""
import os
import time

import rommlib
from test_pipeline import _first, _setup


def _w(p, data=b"x" * 1000):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    return p


def test_deleting_games_while_others_download_in_the_same_systems(isolated, mock_url):
    api, d, plats = _setup(isolated, mock_url)
    lib = d.library
    cart = csys = None
    for slug, pid in plats.items():
        rs = [r for r in api.roms(platform_ids=[pid], limit=500)["items"]
              if not r["has_multiple_files"] and 8 << 20 < r["fs_size_bytes"] <= 64 << 20]
        if rs and slug in d.es:
            cart, csys = rs[0], slug
            break
    folder = _first(api, plats["dreamcast"], lambda r: r["has_multiple_files"])
    dc = os.path.join(rommlib.ROMS_ROOT, "dreamcast")
    ours = _w(os.path.join(dc, "Ours.chd"))
    lib.mark({"id": 900001, "name": "Ours", "fs_name": "Ours.chd"}, "dreamcast", [ours])
    sub = _w(os.path.join(dc, "Sub", "InSub.chd"))
    lib.mark({"id": 900002, "name": "InSub", "fs_name": "InSub.chd"}, "dreamcast", [sub])
    cue = _w(os.path.join(dc, "Theirs.cue"), b'FILE "Theirs.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
    binf = _w(os.path.join(dc, "Theirs.bin"))
    other = _w(os.path.join(rommlib.ROMS_ROOT, csys, "Other.zip"))
    lib.mark({"id": 900003, "name": "Other", "fs_name": "Other.zip"}, csys, [other])

    d.rate_limit = 3 << 20                     # slow enough that the deletes land mid-download
    jobs = [d.enqueue(folder, "dreamcast"), d.enqueue(cart, csys)]
    offered, deleted, t0 = [], False, time.time()
    while any(j.active for j in jobs):
        pend = [(j.rom, j.system) for j in list(d.jobs) if j.active or j.state == rommlib.Job.PAUSED]
        offered += [(it["kind"], it["path"]) for it in rommlib.find_leftovers(pend)]
        if not deleted and jobs[0].done_bytes > 2 << 20:
            assert jobs[0].state == rommlib.Job.DOWNLOADING
            for system, p in (("dreamcast", ours), ("dreamcast", cue), ("dreamcast", sub), (csys, other)):
                assert rommlib.delete_game(system, p, lib)[0]
            deleted = True
            d.rate_limit = 0
        time.sleep(0.05)
        assert time.time() - t0 < 200, [(j.state, j.done_bytes, j.message) for j in jobs]
    assert deleted
    assert [j.state for j in jobs] == ["Done", "Done"], [j.message for j in jobs]
    fdir = os.path.join(dc, folder["fs_name"])
    assert sorted(os.listdir(fdir)) == sorted(f["file_name"] for f in api.rom(folder["id"])["files"])
    inst = rommlib.read_installed()
    assert {str(folder["id"]), str(cart["id"])} <= set(inst)
    assert all(os.path.exists(p) for p in inst[str(cart["id"])]["paths"])
    assert not {"900001", "900002", "900003"} & set(inst)
    assert not any(os.path.exists(p) for p in (ours, cue, binf, sub, os.path.dirname(sub), other))
    assert not offered, offered
    assert not rommlib.find_leftovers([])
