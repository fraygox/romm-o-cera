"""installed.json and the roms folder scan (romm/library.py): what counts as on the
box, what a delete takes, and that a damaged installed.json is never written over."""
import json
import os
import threading

import pytest

import rommlib


def _file(system, name, data=b"x"):
    p = os.path.join(rommlib.ROMS_ROOT, system, name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    return p


def _rom(rid, fs_name, name=None):
    return {"id": rid, "name": name or os.path.splitext(fs_name)[0], "fs_name": fs_name,
            "fs_name_no_ext": os.path.splitext(fs_name)[0]}


def _damage(text):
    """installed.json replaced by damaged text (a new file: .bak, a hard link to the
    good one, keeps its bytes)."""
    with open(rommlib.STATE_PATH + ".dmg", "w") as f:
        f.write(text)
    os.replace(rommlib.STATE_PATH + ".dmg", rommlib.STATE_PATH)


def _records(n):
    return {str(i): {"name": "Game %d" % i, "system": "psx", "paths": []} for i in range(n)}


# -- 1, 10: a damaged or deleted installed.json

def test_a_damaged_installed_json_carries_on_from_its_last_good_copy(isolated, monkeypatch):
    monkeypatch.setattr(rommlib, "BAK_COPY_EVERY", 0)    # each write copied: this is about what .bak holds
    lib = rommlib.Library()
    lib.update(lambda inst: inst.update(_records(39)))
    lib.update(lambda inst: inst.update({"39": {"name": "Game 39", "system": "psx", "paths": []}}))
    text = open(rommlib.STATE_PATH).read()
    _damage(text[: len(text) // 2])                                      # cut short (a power cut)
    fresh = rommlib.Library()                                            # e.g. rb_dl starting now
    assert len(fresh.snapshot()) == 40                                   # salvage + the copy after the last write
    fresh.update(lambda inst: inst.update({"99": {"name": "New", "system": "psx", "paths": []}}))
    data = json.load(open(rommlib.STATE_PATH))
    assert len(data) == 41 and "99" in data
    kept = [n for n in os.listdir(rommlib.DATA_DIR) if n.startswith("installed.json.bad-")]
    assert len(kept) == 1


def test_a_damaged_installed_json_whose_copy_cant_be_kept_is_never_written_over(isolated, monkeypatch):
    os.makedirs(rommlib.DATA_DIR)
    full = json.dumps(_records(39))
    open(rommlib.STATE_PATH, "w").write(full[: len(full) // 2])
    monkeypatch.setattr(rommlib, "_keep_unreadable", lambda p: None)          # e.g. the disk is full
    lib = rommlib.Library()
    with pytest.raises(rommlib.LibraryUnreadable):
        lib.update(lambda inst: inst.update({"99": {"name": "New", "system": "psx", "paths": []}}))
    assert open(rommlib.STATE_PATH).read() == full[: len(full) // 2]
    assert isinstance(rommlib.LibraryUnreadable("x"), rommlib.RommError)      # the UI shows its text


def test_an_installed_json_that_cant_be_opened_is_never_written_over(isolated, monkeypatch):
    lib = rommlib.Library()
    lib.update(lambda inst: inst.update(_records(3)))
    before = open(rommlib.STATE_PATH).read()
    import builtins
    real = builtins.open

    def fake(path, mode="r", *a, **k):
        if path == rommlib.STATE_PATH and "r" in mode:
            raise PermissionError(13, "Permission denied")
        return real(path, mode, *a, **k)
    with monkeypatch.context() as m:
        m.setattr(builtins, "open", fake)
        with pytest.raises(rommlib.LibraryUnreadable):
            rommlib.Library().update(lambda inst: inst.clear())
    assert open(rommlib.STATE_PATH).read() == before


def test_a_deleted_installed_json_isnt_written_back_by_the_next_change(isolated):
    lib = rommlib.Library()
    lib.update(lambda inst: inst.update(_records(5)))
    assert len(lib.snapshot()) == 5
    os.remove(rommlib.STATE_PATH)                                        # reset elsewhere
    os.remove(rommlib.STATE_PATH + ".bak")
    lib.update(lambda inst: inst.update({"9": {"name": "New", "system": "psx", "paths": []}}))
    assert list(json.load(open(rommlib.STATE_PATH))) == ["9"]


# -- 4: two writes within one timestamp tick

def test_a_second_write_in_the_same_tick_is_still_seen(isolated):
    lib = rommlib.Library()
    lib.update(lambda inst: inst.update(_records(1)))
    st = os.stat(rommlib.STATE_PATH)
    rommlib.atomic_write_json(rommlib.STATE_PATH, _records(4), indent=1)    # another process
    os.utime(rommlib.STATE_PATH, ns=(st.st_atime_ns, st.st_mtime_ns))     # same time stamp
    assert len(lib.snapshot()) == 4


# -- 5: a damaged file is looked at once, not on every call

def test_a_damaged_installed_json_isnt_reread_on_every_call(isolated, monkeypatch):
    os.makedirs(rommlib.DATA_DIR)
    open(rommlib.STATE_PATH, "w").write("{")
    calls = []
    real = rommlib._keep_unreadable
    monkeypatch.setattr(rommlib, "_keep_unreadable", lambda p: calls.append(p) or real(p))
    lib = rommlib.Library()
    for _ in range(5):
        lib.snapshot()
        lib.is_installed(_rom(1, "Nope.chd"), "psx")
    assert len(calls) == 1
    rommlib.atomic_write_json(rommlib.STATE_PATH, _records(2))           # fixed: read again
    assert len(lib.snapshot()) == 2


# -- 11: odd shapes in the files

def test_odd_records_dont_break_reading(isolated):
    os.makedirs(rommlib.DATA_DIR)
    p = _file("psx", "Odd (USA).chd")
    json.dump({"1": {"system": "psx", "paths": None}, "2": ["not", "a", "record"],
               "3": {"system": "psx", "paths": [7, None, p], "group": "x"}, "4": "text"},
              open(rommlib.STATE_PATH, "w"))
    lib = rommlib.Library()
    assert not lib.is_installed(_rom(1, "Gone.chd"), "psx")
    assert lib.is_installed(_rom(3, "Odd (USA).chd"), "psx")
    assert lib.paths_for(_rom(1, "Gone.chd"), "psx") == []
    assert lib.paths_for(_rom(3, "Odd (USA).chd"), "psx") == [p]
    assert lib.group_for(3) is None and lib.group_for(2) is None
    lib.rename_paths({p: p + ".new"})
    assert json.load(open(rommlib.STATE_PATH))["3"]["paths"] == [p + ".new"]


def test_odd_media_skip_notes_dont_break_reading(isolated):
    os.makedirs(rommlib.DATA_DIR)
    json.dump({"psx/1": ["video"], "psx/2": {"video": "x", "manual": [1]}, "psx/3": "s",
               "psx/4": {"video": {"path": "/v.mp4", "size": "big"}}},
              open(rommlib.media_skips_path(), "w"))
    for rid in (1, 2, 3, 4):
        rommlib.clear_media_skip("psx", rid, "video")
        entry = {"system": "psx", "rom_id": rid, "media": {"video": ["/v.mp4"]}, "manual": "/m.pdf"}
        assert rommlib.skipped_tags(entry, dict(rommlib.DEFAULT_CONFIG)) == set()
    raw = {"psx/9": ["video"], "psx/8": {"manual": "x"}}
    for rid in (8, 9):
        entry = {"system": "psx", "rom_id": rid, "media": {"video": ["/v.mp4"]}, "manual": "/m.pdf"}
        assert rommlib.skipped_tags(entry, dict(rommlib.DEFAULT_CONFIG), raw) == set()


def test_media_skips_noted_at_once_are_all_kept(isolated):
    threads = [threading.Thread(target=rommlib.record_media_skip, args=("psx", i, "video", "/v%d.mp4" % i))
               for i in range(1, 21)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(rommlib.media_skips()) == 20


# -- 2, 6: files being written, saves and media are never the game

def test_a_paused_download_isnt_on_the_box(isolated):
    lib = rommlib.Library()
    rom = _rom(1, "Lorem (USA).zip")
    part = _file("snes", "Lorem (USA).zip.part")
    os.makedirs(os.path.join(rommlib.ROMS_ROOT, "snes", ".rb-extract-1-2"))
    _file("snes", "Lorem (USA).zip.tmp-12-34")
    assert not lib.is_installed(rom, "snes")
    assert lib.paths_for(rom, "snes") == []
    assert not lib.adopt(rom, "snes", {"id": "g"})
    lib.uninstall(rom, "snes")
    assert os.path.exists(part)
    for name in ("Ipsum (USA).chd.part", "Ipsum (USA).cso-iso.part", "Ipsum (USA).ps3.part", "Ipsum (USA).partial",
                 "Ipsum (USA).tmp.chd"):
        assert not rommlib.is_game_name(name), name
    assert rommlib.is_game_name("Ipsum (USA).chd") and rommlib.is_game_name("Ipsum (USA).ps3")


def test_deleting_a_game_found_by_name_keeps_its_saves_media_and_other_discs(isolated):
    lib = rommlib.Library()
    rom = _rom(1, "Dolor (USA).cue")
    cue = _file("psx", "Dolor (USA).cue")
    keep = [_file("psx", n) for n in ("Dolor (USA).srm", "Dolor (USA).sav", "Dolor (USA).state1",
                                      "Dolor (USA).state.auto", "Dolor (USA).png", "Dolor (USA).txt")]
    other = _file("psx", "Other (USA).chd")
    m3u = _file("psx", "Dolor (USA).m3u", b"Other (USA).chd\n")
    paths = lib.paths_for(rom, "psx")
    assert sorted(paths) == sorted([cue, m3u])                  # the playlist goes, its "discs" don't
    with pytest.raises(rommlib.NotDownloadedHere):              # not downloaded by the app: never deleted
        lib.uninstall(rom, "psx")
    assert all(os.path.exists(p) for p in keep + [other, cue, m3u])
    lib.mark(rom, "psx", paths)                                 # the app's own: it goes, saves and the rest stay
    lib.uninstall(rom, "psx")
    assert all(os.path.exists(p) for p in keep + [other]) and not os.path.exists(cue)


def test_a_playlist_that_is_the_game_brings_its_discs(isolated):
    lib = rommlib.Library()
    d1, d2 = _file("psx", "Sit (USA) (Disc 1).chd"), _file("psx", "Sit (USA) (Disc 2).chd")
    m3u = _file("psx", "Sit (USA).m3u", b"Sit (USA) (Disc 1).chd\nSit (USA) (Disc 2).chd\n")
    assert sorted(lib.paths_for(_rom(1, "Sit (USA).m3u"), "psx")) == sorted([m3u, d1, d2])


# -- 3: the folder scan notices changes made elsewhere

def test_games_copied_in_or_deleted_elsewhere_show_at_once(isolated):
    lib = rommlib.Library()
    rom = _rom(1, "Amet (USA).chd")
    os.makedirs(os.path.join(rommlib.ROMS_ROOT, "psx"))
    assert not lib.is_installed(rom, "psx")
    p = _file("psx", "Amet (USA).chd")                           # scp, Syncthing, rb_dl
    assert lib.is_installed(rom, "psx")
    os.remove(p)                                                 # ES's delete, ssh
    assert not lib.is_installed(rom, "psx")


# -- 7: a delete that can't finish, or mustn't start

def test_a_file_that_wont_go_doesnt_stop_the_rest(isolated, monkeypatch):
    lib = rommlib.Library()
    rom = _rom(1, "Elit (USA).cue")
    cue, b1, b2 = (_file("psx", n) for n in ("Elit (USA).cue", "Elit (USA) (Track 1).bin", "Elit (USA) (Track 2).bin"))
    lib.mark(rom, "psx", [cue, b1, b2])
    real = os.remove
    with monkeypatch.context() as m:
        m.setattr(os, "remove", lambda p: (_ for _ in ()).throw(PermissionError(13, "busy"))
                  if p == b1 else real(p))
        with pytest.raises(rommlib.UninstallIncomplete) as e:
            lib.uninstall(rom, "psx")
    assert "Elit (USA) (Track 1).bin" in str(e.value)
    assert sorted(e.value.paths) == sorted([cue, b2])
    assert not os.path.exists(cue) and not os.path.exists(b2) and os.path.exists(b1)
    assert lib.snapshot()["1"]["paths"] == [b1]                  # what's left stays findable
    queued = [json.loads(ln) for ln in open(rommlib.GAMELIST_QUEUE)]
    assert queued[-1] == {"system": "psx", "remove": [cue, b2], "keep_media": True}
    assert lib.uninstall(rom, "psx")[0] == [b1]                  # deleting again finishes it
    assert "1" not in lib.snapshot()


def test_no_delete_while_the_game_is_being_converted(isolated):
    lib = rommlib.Library()
    rom = _rom(6, "Amet (USA).cue")
    cue = _file("psx", "Amet (USA).cue")
    lib.mark(rom, "psx", [cue])
    rommlib.publish_queue_task({"system": "psx", "mode": "chd", "sources": [cue], "roots": [], "queued": 0},
                               "1-6-psx.json")
    with pytest.raises(rommlib.GameBusy) as e:
        lib.uninstall(rom, "psx")
    assert str(e.value) == "Waiting to be compressed to CHD: take it out of Conversions to delete it now"
    rommlib.atomic_write_json(rommlib.chd_current_path(), {"task": "1-6-psx.json", "pct": 42})
    with pytest.raises(rommlib.GameBusy) as e:
        lib.uninstall_group({"rom_ids": [6]}, "psx")
    assert str(e.value) == "Being compressed to CHD (42%): delete it once that's done"
    assert os.path.exists(cue) and "6" in lib.snapshot()


# -- 8: disc tags

@pytest.mark.parametrize("name,want", [
    ("Quux (USA) (CD1)", ("Quux (USA)", 1)), ("Quux (USA) (CD 2)", ("Quux (USA)", 2)),
    ("Quux (USA) (Disc 1)", ("Quux (USA)", 1)), ("Quux (USA) (Disc 1 of 2)", ("Quux (USA)", 1)),
    ("Quux (USA) (Disk 2)", ("Quux (USA)", 2)), ("Quux (USA) (Disc A)", ("Quux (USA)", 1)),
    ("Quux (Europe) (CD32)", None), ("Quux (USA) (DVD5)", None), ("Quux (USA) (DVD9)", None),
    ("Quux (Europe) (CD32) (Disc 2)", ("Quux (Europe) (CD32)", 2)),
])
def test_disc_tags(name, want):
    assert rommlib.disc_info(name) == want


# -- 9: other versions on the box

def test_other_versions_are_one_entry_per_game(isolated):
    lib = rommlib.Library()
    for n in ("Sed (Europe).cue", "Sed (Europe) (Track 1).bin", "Sed (Europe) (Track 2).bin",
              "Sed (Europe).png", "Sed (Europe).srm", "Sed (Japan).chd", "Sed (USA).chd", "Sed (USA).srm",
              "Sed (Japan).chd.part"):
        _file("psx", n)
    assert sorted(lib.variants(_rom(1, "Sed (USA).chd"), "psx")) == ["Sed (Europe).cue", "Sed (Japan).chd"]


def test_a_cue_bin_game_isnt_its_own_other_version(isolated):
    lib = rommlib.Library()
    for n in ("Sed (USA).cue", "Sed (USA) (Track 1).bin", "Sed (USA) (Track 2).bin"):
        _file("psx", n)
    assert lib.variants(_rom(1, "Sed (USA).cue"), "psx") == []


# -- 12: a folder whose name has a dot isn't a folder game

def test_a_folder_with_a_dot_in_its_name_isnt_a_folder_game(isolated):
    lib = rommlib.Library()
    rom = _rom(1, "Mr. Pallet (USA)")
    inner = _file("dos", "Mr. Pallet (USA)/pallet.exe")
    lib.mark(rom, "dos", [os.path.dirname(inner)])
    assert rommlib.play_target(lib, rom, "dos", {}) == (inner, None)
    ps3 = os.path.join(rommlib.ROMS_ROOT, "ps3", "Tor (USA).ps3")
    _file("ps3", "Tor (USA).ps3/PS3_GAME/x")
    lib.mark(_rom(2, "Tor (USA).ps3"), "ps3", [ps3])
    assert rommlib.play_target(lib, _rom(2, "Tor (USA).ps3"), "ps3", {}) == (ps3, None)



def test_fill_in_game_info_leaves_a_batch_being_applied_alone(isolated):
    """rb_finalize moves the queue to .work while it applies it: those games are
    still pending, so Fill in game info doesn't offer them again."""
    import json
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    with open(rommlib.GAMELIST_QUEUE + ".work", "w") as f:
        f.write(json.dumps({"system": "gba", "paths": ["/roms/gba/Lumen Drift.gba"]}) + "\n")
    assert rommlib.pending_gamelist_paths("gba") == {"/roms/gba/Lumen Drift.gba"}


def test_a_game_compress_cant_convert_shows_its_whole_size(tmp_path):
    """Compress's "can't convert" rows size a cue/bin game by its tracks too, not the few-byte .cue."""
    from rbui.screens.compress import cant_convert_rows
    (tmp_path / "Lumen Drift.bin").write_bytes(b"\0" * 5000)
    cue = tmp_path / "Lumen Drift.cue"
    cue.write_text('FILE "Lumen Drift.bin" BINARY\n  TRACK 01 MODE2/2352\n')
    rows = cant_convert_rows([("psx", str(cue), "a track is missing")])
    assert rows[0]["size"] == 5000 + cue.stat().st_size


# -- round 2 (review of the fixes above)

def _layout(n):
    """installed.json as Library.update writes it, with nested objects in records."""
    recs = {}
    for i in range(n):
        recs[str(100 + i)] = {"system": "psx", "paths": ["/roms/psx/Game %d.chd" % i], "name": "Game {%d}, \"x\"" % i,
                              "romm": {"size": i, "sha1": "ab%d" % i}, "group": {"id": "g", "rom_ids": [1, 2]},
                              "meta": {"paths": ["a"], "desc": "line\nnext: {\"y\"}"}}
    return recs, json.dumps(recs, indent=1)


def test_salvage_keeps_every_whole_record_at_any_cut():
    recs, text = _layout(12)
    ends = [text.index(json.dumps(k) + ":") for k in recs]           # where each record's key starts
    for cut in range(0, len(text) + 1, 7):
        got, whole = rommlib.salvage_records(text[:cut])
        for k, v in got.items():
            assert v == recs[k], (cut, k)                            # never a half record
        complete = [k for k, e in zip(recs, ends[1:] + [len(text)]) if e <= cut]
        assert set(complete) <= set(got), cut                        # every record cut off after its end
    assert rommlib.salvage_records(text) == (recs, True)


def test_salvage_reads_past_a_bad_block_in_the_middle():
    recs, text = _layout(10)
    a = text.index('"103":')
    b = text.index('"105":')
    damaged = text[:a + 40] + "\0" * 300 + text[b - 2:]               # 103 and 104 hit
    got, whole = rommlib.salvage_records(damaged)
    assert not whole
    assert set(got) == set(recs) - {"103", "104"}
    assert all(got[k] == recs[k] for k in got)
    assert rommlib.salvage_records("﻿" + text)[0] == recs
    assert rommlib.salvage_records(text + "\0\0garbage")[1] is True
    for junk in ("", "[]", "null", "\0" * 50, "{", '{"1": [1, 2]}'):
        assert rommlib.salvage_records(junk)[0] == {}


@pytest.mark.parametrize("damage", ["", "[1, 2]", "half", "\xff\xfe binary"])
def test_a_damaged_installed_json_with_no_good_copy_carries_on_from_what_is_salvaged(isolated, damage):
    """First run after upgrading (no .bak yet), or .bak damaged too: every write used
    to refuse, forever (every download Failed, deletes half done)."""
    os.makedirs(rommlib.DATA_DIR)
    recs, text = _layout(20)
    bad = text[: len(text) // 2] if damage == "half" else damage
    open(rommlib.STATE_PATH, "wb").write(bad.encode("utf-8", "surrogateescape") if damage != "\xff\xfe binary"
                                         else b"\xff\xfe binary")
    open(rommlib.STATE_PATH + ".bak", "w").write("{")               # damaged too
    raw = open(rommlib.STATE_PATH, "rb").read()
    p = _file("psx", "Fresh.chd")
    lib = rommlib.Library()
    for i in range(3):
        lib.mark({"id": 1 + i, "name": "Fresh", "fs_name": "Fresh.chd"}, "psx", [p])
    data = json.load(open(rommlib.STATE_PATH))
    assert {"1", "2", "3"} <= set(data)
    if damage == "half":
        assert len(data) > 3 + 5 and all(data[k] == recs[k] for k in data if k in recs)
    kept = [n for n in os.listdir(rommlib.DATA_DIR) if n.startswith("installed.json.bad-")]
    assert len(kept) == 1 and open(os.path.join(rommlib.DATA_DIR, kept[0]), "rb").read() == raw


def test_the_salvage_wins_over_an_older_bak(isolated):
    lib = rommlib.Library()
    lib.update(lambda inst: inst.update({"1": {"name": "Old", "system": "psx", "paths": []},
                                         "2": {"name": "Two", "system": "psx", "paths": []}}))
    lib.update(lambda inst: inst["1"].update(name="New"))                # .bak: name Old
    lib.update(lambda inst: inst.update({"3": {"name": "Three", "system": "psx", "paths": []}}))
    text = open(rommlib.STATE_PATH).read()
    open(rommlib.STATE_PATH, "w").write(text[: text.index('"3":')])     # 3 cut off
    snap = rommlib.Library().snapshot()
    assert snap["1"]["name"] == "New" and "2" in snap


def test_a_lost_installed_json_carries_on_from_its_bak(isolated, monkeypatch):
    monkeypatch.setattr(rommlib, "BAK_COPY_EVERY", 0)    # each write copied: this is about what .bak holds
    """Nothing in the app deletes installed.json: a missing one was lost (fsck, a
    rename cut off). Its .bak is the only copy of every record: read, never deleted."""
    lib = rommlib.Library()
    lib.update(lambda inst: inst.update(_records(5)))
    lib.update(lambda inst: inst.update(_records(6)))                    # .bak holds all 6
    os.remove(rommlib.STATE_PATH)
    assert len(rommlib.Library().snapshot()) == 6                       # a fresh reader sees them
    assert len(lib.snapshot()) == 6                                     # and so does one that had read it
    lib.update(lambda inst: inst.update({"new": {"name": "New", "system": "psx", "paths": []}}))
    data = json.load(open(rommlib.STATE_PATH))
    assert set(_records(6)) <= set(data) and "new" in data
    assert os.path.exists(rommlib.STATE_PATH + ".bak")
    assert "MISSING" in open(rommlib.LOG_PATH).read()


def test_a_lost_installed_json_with_no_good_bak_starts_empty(isolated):
    lib = rommlib.Library()
    lib.update(lambda inst: inst.update(_records(2)))
    lib.update(lambda inst: inst.update(_records(3)))
    os.remove(rommlib.STATE_PATH)
    open(rommlib.STATE_PATH + ".bak", "w").write("{")                   # damaged too
    assert rommlib.Library().snapshot() == {}
    lib.update(lambda inst: inst.update({"new": {"name": "New", "system": "psx", "paths": []}}))
    assert list(json.load(open(rommlib.STATE_PATH))) == ["new"]


def test_an_installed_json_that_cant_be_opened_now_works_again_later(isolated, monkeypatch):
    lib = rommlib.Library()
    lib.update(lambda inst: inst.update(_records(3)))
    import builtins
    real = builtins.open
    fail = [True]

    def fake(path, mode="r", *a, **k):
        if fail[0] and path == rommlib.STATE_PATH and "r" in mode:
            raise OSError(5, "Input/output error")
        return real(path, mode, *a, **k)
    monkeypatch.setattr(builtins, "open", fake)
    with pytest.raises(rommlib.LibraryUnreadable) as e:
        lib.update(lambda inst: inst.update(_records(4)))
    assert "try again" in str(e.value)
    fail[0] = False
    lib.update(lambda inst: inst.update(_records(4)))
    assert len(json.load(real(rommlib.STATE_PATH))) == 4


def test_a_bak_already_the_same_file_leaves_no_temp_file(isolated):
    lib = rommlib.Library()
    lib.update(lambda inst: inst.update(_records(1)))
    os.remove(rommlib.STATE_PATH + ".bak")
    os.link(rommlib.STATE_PATH, rommlib.STATE_PATH + ".bak")           # .bak already the same file
    lib.update(lambda inst: inst.update(_records(2)))
    assert [n for n in os.listdir(rommlib.DATA_DIR) if ".tmp-" in n] == []


def _unwritable(monkeypatch):
    """installed.json that can't be updated now (as a transient read error)."""
    def refuse(self, fn):
        raise rommlib.LibraryUnreadable("installed.json can't be read right now, so nothing was recorded; try again")
    monkeypatch.setattr(rommlib.Library, "update", refuse)


def test_a_delete_that_cant_be_recorded_says_the_files_went(isolated, monkeypatch):
    lib = rommlib.Library()
    p = _file("psx", "Game.chd", b"x" * 10)
    lib.mark(_rom(1, "Game.chd"), "psx", [p])
    _unwritable(monkeypatch)
    with pytest.raises(rommlib.LibraryUnreadable) as e:
        lib.uninstall(_rom(1, "Game.chd"), "psx")
    assert not os.path.exists(p)
    assert str(e.value).startswith("Deleted Game") and e.value.freed == 10 and e.value.paths == [p]
    assert json.loads(open(rommlib.GAMELIST_QUEUE).readline()) == {"system": "psx", "remove": [p], "keep_media": True}


def test_a_set_delete_that_cant_be_recorded_still_deletes_every_disc(isolated, monkeypatch):
    lib = rommlib.Library()
    d1, d2 = _file("psx", "Sit (Disc 1).chd"), _file("psx", "Sit (Disc 2).chd")
    lib.mark(_rom(1, "Sit (Disc 1).chd"), "psx", [d1])
    lib.mark(_rom(2, "Sit (Disc 2).chd"), "psx", [d2])
    _unwritable(monkeypatch)
    with pytest.raises(rommlib.LibraryUnreadable) as e:
        lib.uninstall_group({"rom_ids": [1, 2], "title": "Sit"}, "psx")
    assert not os.path.exists(d1) and not os.path.exists(d2)
    assert sorted(e.value.paths) == sorted([d1, d2]) and str(e.value).startswith("Deleted Sit")


def test_es_deletes_and_moves_queue_their_gamelist_change_even_if_unrecorded(isolated, monkeypatch):
    lib = rommlib.Library()
    a = _file("psx", "Alpha.chd")
    b = _file("psx", "Beta.chd")
    c = _file("psx", "Gamma.chd")
    for i, p in enumerate((a, b, c)):
        lib.mark(_rom(i + 1, os.path.basename(p)), "psx", [p])
    _unwritable(monkeypatch)
    with pytest.raises(rommlib.LibraryUnreadable):
        rommlib.delete_game("psx", a, lib)
    with pytest.raises(rommlib.LibraryUnreadable):
        rommlib.delete_unseen({"path": b, "system": "psx"}, lib)
    monkeypatch.setattr(rommlib.Library, "rename_paths", lambda self, *a, **k: self.update(None))
    with pytest.raises(rommlib.LibraryUnreadable):
        rommlib.move_unseen({"path": c, "system": "psx"}, "saturn", lib)
    queued = [json.loads(ln) for ln in open(rommlib.GAMELIST_QUEUE)]
    assert {"system": "psx", "remove": [a]} in queued and {"system": "psx", "remove": [b]} in queued
    assert any(q.get("rename") == {c: os.path.join(rommlib.ROMS_ROOT, "saturn", "Gamma.chd")} for q in queued)


def test_a_folder_dated_in_the_future_isnt_listed_on_every_call(isolated, monkeypatch):
    for i in range(5):
        _file("psx", "G%d.chd" % i)
    d = os.path.join(rommlib.ROMS_ROOT, "psx")
    fut = __import__("time").time() + 86400 * 365
    os.utime(d, (fut, fut))                                              # no clock battery, before NTP
    lib = rommlib.Library()
    assert lib.is_installed(_rom(5, "G1.chd"), "psx")
    assert lib._stems["psx"][1] is None                                  # no "list again soon"
    now = __import__("time").time()
    os.utime(d, (now, now))                                              # changed just now: it is
    assert lib.is_installed(_rom(5, "G1.chd"), "psx") and lib._stems["psx"][1] is not None


def test_a_folder_changed_just_now_is_listed_again_after_a_fat32_tick():
    assert rommlib.Library.STEMS_SETTLE >= 2.5


def test_pico8_carts_are_games(isolated, monkeypatch):
    p = _file("pico8", "Celeste Lite.p8.png")
    img = _file("psx", "Lorem.png")
    lib = rommlib.Library()
    rom = {"id": 7, "fs_name": "Celeste Lite.p8.png", "fs_name_no_ext": "Celeste Lite.p8"}
    assert lib.is_installed(rom, "pico8") and lib.paths_for(rom, "pico8") == [p]
    assert not rommlib.is_game_name("Lorem.png", "psx") and os.path.exists(img)
    cfg = os.path.join(rommlib.DATA_DIR, "es_systems.cfg")
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    open(cfg, "w").write("<systemList><system><name>pico8</name><path>/userdata/roms/pico8</path>"
                         "<extension>.p8 .P8</extension></system><system><name>x</name>"
                         "<path>/userdata/roms/pix</path><extension>.png .zip</extension></system></systemList>")
    monkeypatch.setenv("ROMM_BROWSER_ES_SYSTEMS", cfg)
    assert rommlib.is_game_name("Celeste Lite.p8.png", "pico8")          # ES lists .p8 only
    assert not rommlib.is_game_name("Shot.png", "pico8")
    assert rommlib.is_game_name("Pic.png", "pix")                       # a system that lists .png


@pytest.fixture
def app(isolated, rb, monkeypatch):
    import pygame
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    a = rb.App(None, headless=True)

    def bg(fn, done=None, err=None):
        try:
            res = fn()
        except Exception as e:
            if err:
                err(e)
            return
        if done:
            done(res)
    a.bg = bg
    return a


def test_a_delete_that_couldnt_finish_still_frees_space_and_reloads(app, monkeypatch):
    woke, after, toasts = [], [], []
    monkeypatch.setattr(app, "space_freed", lambda: woke.append(1))
    monkeypatch.setattr(app, "toast", lambda m, *a, **k: toasts.append(m))

    def partly(*a):
        raise rommlib.UninstallIncomplete("Couldn't delete all of Saga: x.bin is still there", ["/y"], 5, [("/x", None)])
    monkeypatch.setattr(app.library, "uninstall_group", partly)
    monkeypatch.setattr(app.library, "uninstall", partly)
    app.uninstall_group("g1", "psx", "Saga", lambda: after.append(1))
    app.uninstall({"id": 1, "name": "Saga"}, "psx", lambda: after.append(1))
    assert woke == [1, 1] and after == [1, 1] and toasts[0].startswith("Couldn't delete")
    monkeypatch.setattr(app.library, "uninstall", lambda *a: (_ for _ in ()).throw(rommlib.GameBusy("Being compressed")))
    app.uninstall({"id": 1, "name": "Saga"}, "psx", lambda: after.append(1))
    assert woke == [1, 1] and after == [1, 1, 1]


def test_a_set_whose_disc_cant_be_recorded_doesnt_crash_the_ui(isolated, rb):
    toasts, queued = [], []

    class Lib:
        is_installed = staticmethod(lambda r, s: True)

        @staticmethod
        def adopt(r, s, g):
            raise rommlib.LibraryUnreadable("installed.json can't be read right now, so nothing was recorded; try again")

    class Dl:
        job_for = staticmethod(lambda rid: None)
        enqueue = staticmethod(lambda *a: queued.append(a))
        save_queue = staticmethod(lambda: None)

    class FakeApp:
        library, downloader = Lib, Dl
        bg = staticmethod(lambda *a, **k: None)
        toast = staticmethod(lambda m, *a, **k: toasts.append(m))
    assert rb.App.queue_set(FakeApp, {"rom_ids": [1]}, [({"id": 1}, "psx")], "psx") == 0
    assert queued == [] and "try again" in toasts[0]


def test_fill_in_game_info_leaves_a_batch_being_settled_alone(isolated):
    """.work.done is one {"left": [...], "bad": [...]} object, not JSON lines."""
    import json
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    with open(rommlib.GAMELIST_QUEUE + ".work.done", "w") as f:
        json.dump({"left": [{"system": "gba", "paths": ["/roms/gba/Lumen Drift.gba"]}], "bad": []}, f)
    assert rommlib.pending_gamelist_paths("gba") == {"/roms/gba/Lumen Drift.gba"}


# -- backup scheme, wrong-typed records, a disc of a set

def test_bak_holds_the_state_after_the_last_write(isolated, monkeypatch):
    monkeypatch.setattr(rommlib, "BAK_COPY_EVERY", 0)    # each write copied: this is about what .bak holds
    lib = rommlib.Library()
    lib.update(lambda inst: inst.update(_records(2)))
    lib.update(lambda inst: inst.update({"7": {"name": "Last", "system": "psx", "paths": []}}))
    assert json.load(open(rommlib.STATE_PATH + ".bak")) == json.load(open(rommlib.STATE_PATH))
    assert "7" in json.load(open(rommlib.STATE_PATH + ".bak"))


def test_bak_is_not_refreshed_from_a_damaged_file_but_after_a_recovery(isolated):
    lib = rommlib.Library()
    lib.update(lambda inst: inst.update(_records(3)))
    good = open(rommlib.STATE_PATH + ".bak").read()
    _damage("{")
    rommlib.Library().snapshot()                                         # a reader finding it damaged
    assert open(rommlib.STATE_PATH + ".bak").read() == good
    lib2 = rommlib.Library()
    lib2.update(lambda inst: inst.update({"9": {"name": "N", "system": "psx", "paths": []}}))
    assert "9" in json.load(open(rommlib.STATE_PATH + ".bak"))          # forced at once after a recovery


def test_bak_without_links_is_a_copy_of_the_new_file_and_forced_after_recovery(isolated, monkeypatch):
    def no_links(*a, **k):
        raise OSError("no links here")
    monkeypatch.setattr(os, "link", no_links)
    lib = rommlib.Library()
    lib.update(lambda inst: inst.update(_records(1)))
    lib.update(lambda inst: inst.update(_records(3)))                    # within the minute: throttled
    assert len(json.load(open(rommlib.STATE_PATH + ".bak"))) == 1
    _damage("{")
    rommlib.Library().update(lambda inst: inst.update({"9": {"name": "N", "system": "psx", "paths": []}}))
    assert "9" in json.load(open(rommlib.STATE_PATH + ".bak"))


def test_wrong_typed_records_are_logged_and_the_file_kept(isolated):
    os.makedirs(rommlib.DATA_DIR)
    open(rommlib.STATE_PATH, "w").write('{"14": "oops", "15": {"name": "Ok", "paths": []}}')
    lib = rommlib.Library()
    assert list(lib.snapshot()) == ["15"]
    kept = [n for n in os.listdir(rommlib.DATA_DIR) if n.startswith("installed.json.bad-")]
    assert len(kept) == 1
    assert '"14": "oops"' in open(os.path.join(rommlib.DATA_DIR, kept[0])).read()
    assert "14" in open(rommlib.LOG_PATH).read()
    lib.snapshot()
    lib.update(lambda inst: None)
    assert len([n for n in os.listdir(rommlib.DATA_DIR) if n.startswith("installed.json.bad-")]) == 1


def test_deleting_one_disc_of_a_set_leaves_no_records_of_the_files_it_took(isolated):
    d = [_file("psx", "Orb Quest (Disc %d).chd" % i) for i in (1, 2, 3)]
    m3u = os.path.join(rommlib.ROMS_ROOT, "psx", "Orb Quest.m3u")
    with open(m3u, "w") as f:
        f.write("\n".join(os.path.basename(p) for p in d) + "\n")
    lib = rommlib.Library()
    g = {"id": "g", "base": "Orb Quest", "rom_ids": [1, 2, 3]}
    for i, p in enumerate(d, 1):
        lib.mark(_rom(i, os.path.basename(p)), "psx", [p, m3u], group=g)
    lib.uninstall(_rom(2, "Orb Quest (Disc 2).chd"), "psx")
    assert not any(os.path.exists(p) for p in d + [m3u])
    assert lib.snapshot() == {}


def test_deleting_a_hand_copied_game_queues_its_removal_keeping_media(isolated):
    lib = rommlib.Library()
    mine, theirs = _file("psx", "Mine.chd", b"x"), _file("psx", "Theirs.chd", b"x")
    lib.mark(_rom(1, "Mine.chd"), "psx", [mine])
    rommlib.delete_game("psx", theirs, lib)
    rommlib.delete_game("psx", mine, lib)
    queued = [json.loads(ln) for ln in open(rommlib.GAMELIST_QUEUE)]
    assert {"system": "psx", "remove": [theirs], "keep_media": True} in queued
    assert {"system": "psx", "remove": [mine]} in queued
