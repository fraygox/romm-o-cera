"""A disc of a set fetched again (Compare's Get the newer one, Manage's Download this disc
again): the set's playlist stays in every disc's record, a disc under a new name is hidden
under the playlist, and replacing one disc never deletes the playlist or the other discs."""
import json
import os

import pytest

import rommlib
import rb_finalize

from test_multidisc import PSX_ES, _fake_chdman, _rom as _mock_rom, _setup, _wait


def _touch(path, size=1):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * size)
    return path


def _rom(rid, fs_name, size=100):
    stem, ext = os.path.splitext(fs_name)
    return {"id": rid, "name": stem, "fs_name": fs_name, "fs_name_no_ext": stem,
            "fs_extension": ext.lstrip("."), "fs_size_bytes": size}


def _queue():
    try:
        with open(rommlib.GAMELIST_QUEUE) as f:
            return [json.loads(ln) for ln in f if ln.strip()]
    except OSError:
        return []


def _hidden():
    return [p for e in _queue() for p in e.get("hide") or []]


def _set_of_two(sysdir, ext=".chd"):
    """Two discs recorded as download_set leaves them: each disc's record, then the playlist."""
    lib = rommlib.Library()
    roms = [_rom(1, "Set (USA) (Disc 1)" + ext), _rom(2, "Set (USA) (Disc 2)" + ext)]
    group = {"id": "g", "base": "Set (USA)", "rom_ids": [1, 2], "title": "Set"}
    d1 = _touch(os.path.join(sysdir, "Set (USA) (Disc 1).chd"))
    d2 = _touch(os.path.join(sysdir, "Set (USA) (Disc 2).chd"))
    lib.mark(roms[0], "psx", [d1], group=group)
    lib.mark(roms[1], "psx", [d2], group=group)
    d = rommlib.Downloader.__new__(rommlib.Downloader)
    d.library, d.es, d.unopenable = lib, {}, {}
    m3u = d._finish_group(group, "psx")
    assert m3u and all(m3u in r["paths"] for r in lib.snapshot().values())
    return d, lib, roms, group, d1, d2, m3u


def _own(roms, lib, rid):
    it = rommlib.compare_games(roms, lib.snapshot(), rommlib.local_games("psx"), "psx")["items"]
    parts = [x for i in it for x in i["parts"] if x["rom"]["id"] == rid and x["status"] == rommlib.NEWER]
    assert len(parts) == 1
    return parts[0]["paths"]


def _fetch_again(d, lib, rom, group, replace, results):
    """What _post and _settled do with a disc fetched again."""
    j = rommlib.Job(rom, "psx", group)
    j.replace = replace
    d._replace_old(j, results)
    lib.mark(rom, "psx", results, group=group)
    d._finish_group(group, "psx")


def test_replacing_each_disc_in_turn_keeps_the_set(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    d, lib, roms, group, d1, d2, m3u = _set_of_two(sysdir)
    roms[1]["fs_size_bytes"] = 99                         # disc 2 newer: fetched over itself
    own = _own(roms, lib, 2)
    assert own == [d2]
    _fetch_again(d, lib, roms[1], group, own, [d2])
    assert m3u in lib.snapshot()["2"]["paths"], "the record of a disc fetched again keeps the playlist"
    roms[0]["fs_size_bytes"] = 77                         # later disc 1 newer
    own = _own(roms, lib, 1)
    assert own == [d1], "only disc 1's own file: never the playlist"
    open(rommlib.GAMELIST_QUEUE, "w").close()
    _fetch_again(d, lib, roms[0], group, own, [d1])
    assert os.path.exists(d1) and os.path.exists(d2) and os.path.exists(m3u)
    assert open(m3u).read().splitlines() == ["Set (USA) (Disc 1).chd", "Set (USA) (Disc 2).chd"]
    recs = lib.snapshot()
    assert recs["1"]["paths"] == [d1, m3u] and recs["2"]["paths"] == [d2, m3u]
    assert not any("remove" in e for e in _queue())
    assert d1 in _hidden()


def test_a_disc_replaced_under_a_new_name_is_hidden_under_the_playlist(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    d, lib, roms, group, d1, d2, m3u = _set_of_two(sysdir, ext=".zip")
    roms[1]["fs_size_bytes"] = 99
    own = _own(roms, lib, 2)
    assert own == [d2]
    cue = _touch(os.path.join(sysdir, "Set (USA) (Disc 2).cue"))
    bin_ = _touch(os.path.join(sysdir, "Set (USA) (Disc 2).bin"))
    _fetch_again(d, lib, roms[1], group, own, [cue, bin_])
    assert not os.path.exists(d2) and os.path.exists(d1)
    assert open(m3u).read().splitlines() == ["Set (USA) (Disc 1).chd", "Set (USA) (Disc 2).cue"]
    assert cue in _hidden(), "the new disc 2 is hidden under the playlist"
    assert lib.snapshot()["2"]["paths"] == [cue, bin_, m3u]
    n = len(_queue())
    assert d._finish_group(group, "psx") == m3u and len(_queue()) == n      # settled: nothing more


def test_download_this_disc_again_keeps_the_playlist_in_its_record(isolated):
    """Manage's Download this disc again: no replace, same file; the record keeps the playlist
    and the disc is hidden again."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    d, lib, roms, group, d1, d2, m3u = _set_of_two(sysdir)
    open(rommlib.GAMELIST_QUEUE, "w").close()
    lib.mark(roms[0], "psx", [d1], group=group)
    assert lib.snapshot()["1"]["paths"] == [d1, m3u]
    assert d.repair_groups() == 1
    assert d1 in _hidden()


def test_a_set_left_broken_by_an_earlier_version_is_repaired(isolated):
    """A record that lost the playlist (an earlier version's Download this disc again):
    repair_groups puts it back, and Compare still never offers the playlist as a disc's own."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    d, lib, roms, group, d1, d2, m3u = _set_of_two(sysdir)

    def drop(inst):
        inst["1"]["paths"] = [d1]
        inst["1"].pop("hidden_disc", None)
        inst["2"].pop("hidden_disc", None)
    lib.update(drop)
    roms[1]["fs_size_bytes"] = 99
    assert _own(roms, lib, 2) == [d2]
    d.repair_groups()
    assert lib.snapshot()["1"]["paths"] == [d1, m3u]


def test_replacing_a_disc_never_deletes_the_playlist_or_the_other_discs(isolated):
    """Even handed the playlist (Compare's list from an earlier version), the replacement only
    deletes the old disc's own file."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    d, lib, roms, group, d1, d2, m3u = _set_of_two(sysdir)
    new1 = _touch(os.path.join(sysdir, "Set (USA) (Disc 1) (Rev 1).chd"))
    j = rommlib.Job(roms[0], "psx", group)
    j.replace = [d1, m3u]
    assert d._replace_old(j, [new1]) is None
    assert not os.path.exists(d1)
    assert os.path.exists(m3u) and os.path.exists(d2) and os.path.exists(new1)
    assert open(m3u).read().splitlines() == ["Set (USA) (Disc 1) (Rev 1).chd", "Set (USA) (Disc 2).chd"]
    assert "2" in lib.snapshot()


def test_a_sheet_naming_another_discs_track_keeps_that_track(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    d, lib, roms, group, d1, d2, m3u = _set_of_two(sysdir)
    shared = _touch(os.path.join(sysdir, "Shared.bin"))
    lib.update(lambda inst: inst["2"]["paths"].insert(0, shared))
    old = os.path.join(sysdir, "Old (Disc 1).cue")
    with open(old, "w") as f:
        f.write('FILE "Old (Disc 1).bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n'
                'FILE "Shared.bin" BINARY\n  TRACK 02 AUDIO\n    INDEX 01 00:00:00\n')
    oldbin = _touch(os.path.join(sysdir, "Old (Disc 1).bin"))
    j = rommlib.Job(roms[0], "psx", group)
    j.replace = [old]
    d._replace_old(j, [d1])
    assert not os.path.exists(old) and not os.path.exists(oldbin)
    assert os.path.exists(shared)
    assert any(e.get("remove") == [old, oldbin] for e in _queue())


def test_get_the_newer_one_of_each_disc_of_a_downloaded_set(isolated, mock_url, tmp_path, monkeypatch, fake_es):
    """End to end: a 2-disc set downloaded and converted to CHD; Compare finds disc 1 newer
    (fetched again as RomM's cue/bin: a new name), then disc 2. Both discs, the playlist,
    the records and ES's hidden discs come out right."""
    api, d = _setup(mock_url)
    discs, _ = rommlib.find_disc_set(api, _mock_rom(api, "Lorem Odyssey (Europe) (Disc 1)"))
    group = rommlib.disc_group("ps1", discs, "Lorem Odyssey")
    full = [r for _, r in discs]
    _wait([d.enqueue(r, "psx", group) for r in full])
    _fake_chdman(tmp_path, monkeypatch)
    import rb_chd
    monkeypatch.setattr(rb_chd, "running_game", lambda: "")
    monkeypatch.setattr(rommlib, "es_busy", lambda app_busy=False, get=None: "")
    for t in sorted(os.listdir(rommlib.CHD_QUEUE_DIR)):
        assert rb_chd.convert(json.load(open(os.path.join(rommlib.CHD_QUEUE_DIR, t))))
        os.remove(os.path.join(rommlib.CHD_QUEUE_DIR, t))
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    m3u = os.path.join(sysdir, "Lorem Odyssey (Europe).m3u")
    chd = [os.path.join(sysdir, "Lorem Odyssey (Europe) (Disc %d).chd" % n) for n in (1, 2)]
    assert open(m3u).read().splitlines() == [os.path.basename(p) for p in chd]
    es = fake_es(PSX_ES)

    def to_es():                                          # rb_finalize hands each batch to ES
        assert rb_finalize.process(_queue(), None, PSX_ES) == []
        open(rommlib.GAMELIST_QUEUE, "w").close()
        es.save()
        es.reload()
        return sorted(os.path.relpath(p, sysdir) for p, md in es.mem["psx"].items() if md.get("hidden") != "true")
    assert to_es() == ["Lorem Odyssey (Europe).m3u"]

    def newer(i):
        roms = [dict(r) for r in full]
        roms[i]["fs_size_bytes"] += 1                     # RomM's copy changed
        items = rommlib.compare_games(roms, d.library.snapshot(), rommlib.local_games("psx"), "psx")["items"]
        parts = [x for it in items for x in it["parts"] if x["rom"]["id"] == full[i]["id"]
                 and x["status"] == rommlib.NEWER and x["paths"]]
        assert len(parts) == 1
        return parts[0]["paths"]

    own1 = newer(0)
    assert own1 == [chd[0]]
    _wait([d.enqueue(full[0], "psx", d.library.group_for(full[0]["id"]), replace=own1)])
    cue1 = os.path.join(sysdir, "Lorem Odyssey (Europe) (Disc 1)", "Lorem Odyssey (Europe) (Disc 1).cue")
    assert open(m3u).read().splitlines() == [os.path.relpath(cue1, sysdir), os.path.basename(chd[1])]
    assert to_es() == ["Lorem Odyssey (Europe).m3u"]
    own2 = newer(1)
    assert own2 == [chd[1]], "disc 2's own file only: never the playlist"
    _wait([d.enqueue(full[1], "psx", d.library.group_for(full[1]["id"]), replace=own2)])
    cue2 = os.path.join(sysdir, "Lorem Odyssey (Europe) (Disc 2)", "Lorem Odyssey (Europe) (Disc 2).cue")
    assert os.path.isfile(m3u) and os.path.isfile(cue1) and os.path.isfile(cue2)
    assert not any(os.path.exists(p) for p in chd)
    assert open(m3u).read().splitlines() == [os.path.relpath(p, sysdir) for p in (cue1, cue2)]
    inst = json.load(open(rommlib.STATE_PATH))
    for r, folder in zip(full, (cue1, cue2)):
        assert inst[str(r["id"])]["paths"] == [os.path.dirname(folder), m3u]
    assert to_es() == ["Lorem Odyssey (Europe).m3u"]        # the playlist shown, both discs hidden


# ------------------------------------------------------------ what Compare's panel says

def _item(status, parts):
    ps = [{"rom": {"id": i}, "status": st, "paths": paths} for i, (st, paths) in enumerate(parts, 1)]
    return {"status": status, "rom": ps[0]["rom"], "parts": ps, "paths": [p for x in ps for p in x["paths"]]}


def test_compare_says_which_discs_getting_them_replaces(rb):
    N, M = rommlib.NEWER, rommlib.MATCHES
    say = rb.compare.say
    assert say(_item(N, [(N, ["a"]), (N, ["b"])])) == (
        "RomM has a newer copy of 2 of this set's discs. Getting them replaces those discs here; saves are kept.")
    assert say(_item(N, [(N, ["a"]), (M, ["b"])])) == (
        "RomM has a newer copy of one of this set's discs. Getting it replaces that disc here; saves are kept.")
    # only some changed discs have files of their own: say only those are fetched
    some = _item(N, [(N, ["a"]), (N, [])])
    assert [x["rom"]["id"] for x in rb.compare.replaceable(some)] == [1]
    assert say(some) == ("RomM has a newer copy of 2 of this set's discs. Only one has files of its own here: "
                         "getting it replaces that disc; saves are kept.")
    assert "Only 2 have files of their own here: getting them replaces those discs" in \
        say(_item(N, [(N, ["a"]), (N, ["b"]), (N, [])]))
    # none: nothing is offered, and it doesn't say "Getting ..."
    for it in (_item(N, [(N, []), (N, [])]), _item(N, [(N, []), (M, ["b"])]), _item(N, [(N, [])]),
               _item(rommlib.DIFFERS, [(rommlib.DIFFERS, [])])):
        assert not rb.compare.replaceable(it)
        assert "Getting" not in say(it) and "nothing of" in say(it)
    assert "Getting it replaces the file here" in say(_item(N, [(N, ["a"])]), recorded=True)


def test_a_lone_games_old_playlist_goes_alone(isolated):
    """Not a set: an old playlist in the replace list is deleted by itself, never the discs
    it names (here the new copy's)."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    new = _touch(os.path.join(sysdir, "Game (USA) (Rev 1).chd"))
    old = os.path.join(sysdir, "Game (USA).m3u")
    with open(old, "w") as f:
        f.write("Game (USA) (Rev 1).chd\n")
    d = rommlib.Downloader.__new__(rommlib.Downloader)
    d.library, d.es, d.unopenable = rommlib.Library(), {}, {}
    j = rommlib.Job(_rom(5, "Game (USA) (Rev 1).chd"), "psx")
    j.replace = [old]
    assert d._replace_old(j, [_touch(os.path.join(sysdir, "Other.chd"))]) is None
    assert not os.path.exists(old) and os.path.exists(new)


def test_get_the_newer_one_of_each_disc_with_es_taking_it_all_in_one_batch(isolated, mock_url, tmp_path,
                                                                          monkeypatch, fake_es):
    """As above, but ES is busy all along (a game running, this app included): the download,
    the CHD conversion and both discs fetched again go to ES in one batch. The conversion's
    rename of each disc's cue folder must not send the cue fetched again under that name back
    to the CHD it replaced: both discs end up hidden under the playlist."""
    api, d = _setup(mock_url)
    discs, _ = rommlib.find_disc_set(api, _mock_rom(api, "Lorem Odyssey (Europe) (Disc 1)"))
    full = [r for _, r in discs]
    _wait([d.enqueue(r, "psx", rommlib.disc_group("ps1", discs, "Lorem Odyssey")) for r in full])
    _fake_chdman(tmp_path, monkeypatch)
    import rb_chd
    monkeypatch.setattr(rb_chd, "running_game", lambda: "")
    monkeypatch.setattr(rommlib, "es_busy", lambda app_busy=False, get=None: "")
    for t in sorted(os.listdir(rommlib.CHD_QUEUE_DIR)):
        assert rb_chd.convert(json.load(open(os.path.join(rommlib.CHD_QUEUE_DIR, t))))
        os.remove(os.path.join(rommlib.CHD_QUEUE_DIR, t))
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    for i in (0, 1):
        roms = [dict(r) for r in full]
        roms[i]["fs_size_bytes"] += 1
        items = rommlib.compare_games(roms, d.library.snapshot(), rommlib.local_games("psx"), "psx")["items"]
        own = [x["paths"] for it in items for x in it["parts"] if x["rom"]["id"] == full[i]["id"]
               and x["status"] == rommlib.NEWER and x["paths"]]
        assert own == [[os.path.join(sysdir, "Lorem Odyssey (Europe) (Disc %d).chd" % (i + 1))]]
        _wait([d.enqueue(full[i], "psx", d.library.group_for(full[i]["id"]), replace=own[0])])
    q = _queue()
    assert sum("rename" in e for e in q) == 2 and sum("remove" in e for e in q) == 2
    es = fake_es(PSX_ES)
    assert rb_finalize.process(q, None, PSX_ES) == []
    es.save()
    es.reload()
    shown = sorted(os.path.relpath(p, sysdir) for p, md in es.mem["psx"].items() if md.get("hidden") != "true")
    assert shown == ["Lorem Odyssey (Europe).m3u"]
    assert sorted(os.path.relpath(p, sysdir) for p in es.mem["psx"]) == [
        "Lorem Odyssey (Europe) (Disc %d)/Lorem Odyssey (Europe) (Disc %d).cue" % (n, n) for n in (1, 2)] + [
        "Lorem Odyssey (Europe).m3u"]


def test_a_hide_that_cant_be_queued_leaves_the_discs_not_hidden(isolated, monkeypatch):
    """The records said hidden before the hide was queued: one that failed was never
    queued again, and both discs stayed in ES's list beside the playlist."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    lib = rommlib.Library()
    roms = [_rom(1, "Set (USA) (Disc 1).chd"), _rom(2, "Set (USA) (Disc 2).chd")]
    group = {"id": "g", "base": "Set (USA)", "rom_ids": [1, 2], "title": "Set"}
    discs = [_touch(os.path.join(sysdir, r["fs_name"])) for r in roms]
    for r, p in zip(roms, discs):
        lib.mark(r, "psx", [p], group=group)
    d = rommlib.Downloader.__new__(rommlib.Downloader)
    d.library, d.es, d.unopenable = lib, {}, {}
    real = rommlib.append_gamelist

    def full(*a, **k):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(rommlib, "append_gamelist", full)
    with pytest.raises(OSError):
        d._finish_group(group, "psx")
    assert not any(r.get("hidden_disc") for r in lib.snapshot().values())
    monkeypatch.setattr(rommlib, "append_gamelist", real)
    assert d.repair_groups() == 1                                         # the next check queues it
    assert sorted(_hidden()) == sorted(discs)
    assert all(r.get("hidden_disc") for r in lib.snapshot().values())


def test_a_playlist_replaced_alone_tells_what_went_when_a_file_wont_go(isolated, monkeypatch):
    """_delete_own stopped at the first file that wouldn't go: the ones already deleted
    were never queued for ES's removal nor dropped from installed.json."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    a = _touch(os.path.join(sysdir, "Old A.m3u"))
    b = _touch(os.path.join(sysdir, "Old B.m3u"))
    lib = rommlib.Library()
    lib.update(lambda inst: inst.update({"5": {"system": "psx", "paths": [a]},
                                         "6": {"system": "psx", "paths": [b]}}))
    d = rommlib.Downloader.__new__(rommlib.Downloader)
    d.library, d.es, d.unopenable = lib, {}, {}
    real = os.remove

    def remove(p, *x, **k):
        if p == b:
            raise PermissionError(13, "Permission denied", p)
        return real(p, *x, **k)
    monkeypatch.setattr(os, "remove", remove)
    with pytest.raises(PermissionError):
        d._delete_own("psx", [a, b])
    assert not os.path.exists(a) and os.path.exists(b)
    assert {"system": "psx", "remove": [a], "keep_media": True} in _queue()
    assert set(lib.snapshot()) == {"6"}


def test_delete_game_says_what_went_when_installed_json_cant_be_written(isolated, monkeypatch):
    """A full disk writing installed.json (or the game list queue) after the files went
    came out as a bare OSError: "not deleted, nothing freed"."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    g = _touch(os.path.join(sysdir, "Game.iso"), 5000)
    rommlib.Library().mark({"id": 1, "name": "Game", "fs_name": "Game.iso"}, "psx", [g])

    def full(self, fn):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(rommlib.Library, "update", full)
    with pytest.raises(rommlib.RommError) as ei:
        rommlib.delete_game("psx", g, rommlib.Library())
    e = ei.value
    assert not os.path.exists(g) and e.paths == [g] and e.freed == 5000 and not e.failed
    assert "installed.json can't be updated now" in str(e)
    assert {"system": "psx", "remove": [g], "keep_media": True} in _queue()


def test_delete_game_says_what_went_when_the_game_list_cant_be_told(isolated, monkeypatch):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    g = _touch(os.path.join(sysdir, "Game.iso"), 5000)
    lib = rommlib.Library()
    lib.update(lambda inst: inst.update({"5": {"system": "psx", "paths": [g]}}))

    def full(*a, **k):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(rommlib, "append_gamelist", full)
    with pytest.raises(rommlib.RommError) as ei:
        rommlib.delete_game("psx", g, lib)
    e = ei.value
    assert e.paths == [g] and e.freed == 5000 and "game list" in str(e)
    assert "5" not in lib.snapshot()                                     # its record went all the same
