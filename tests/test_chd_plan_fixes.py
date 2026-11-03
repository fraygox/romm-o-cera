"""rb_chd_plan and rb_chd: what the planner offers, and what the worker may take
as its own (code review, 2026-11-02)."""
import json
import os
import sys
import time

import pytest

import rommlib
from test_worker_tools import box  # noqa: F401  (a fixture)

_HERE = "a file named %s is already here (another copy of it, or another disc or game)"
CUE = 'FILE "%s" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n'


def _quiet(monkeypatch, rb_chd):
    monkeypatch.setattr(rb_chd, "running_game", lambda: "")
    monkeypatch.setattr(rommlib, "es_busy", lambda app_busy=False, get=None: "")
    monkeypatch.setattr(rommlib, "disc_incomplete", lambda p: False)
    monkeypatch.setattr(rb_chd, "chdman", lambda mode, *a: (
        mode == "verify" or open(a[a.index("-o") + 1], "w").write("chd")) and (0, ""))


def _sheet(path, track="", size=2352):
    """A cue at path naming track (beside it); empty track: a sheet with no tracks."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write(CUE % track if track else "x")
    if track:
        open(os.path.join(os.path.dirname(path), track), "wb").write(b"\0" * size)
    return path


def _sets(system):
    import rb_chd_plan
    sysdir = os.path.join(rommlib.ROMS_ROOT, system)
    return sorted((os.path.relpath(r, sysdir), [os.path.basename(f) for f in fs], n)
                  for r, fs, n in rb_chd_plan.games_for(system))


# ------------------------------------------------------------ 1. output names

def test_a_game_folder_s_lone_disc_takes_the_folder_s_name(isolated, monkeypatch, fake_es):
    """Vela/Vela (Disc 1).cue -> Vela.chd, as libraries converted before and RomM's names expect.
    Disc 2 added later would land on that name: it's skipped with the reason, nothing touched."""
    import rb_chd
    import rb_chd_plan
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    cue = _sheet(os.path.join(sysdir, "Vela", "Vela (Disc 1).cue"), "Vela (Disc 1).bin")
    _quiet(monkeypatch, rb_chd)
    assert rb_chd.convert({"system": "psx", "mode": "createcd", "sources": [cue],
                           "roots": [os.path.dirname(cue)]}) is True
    assert sorted(os.listdir(sysdir)) == ["Vela.chd"]
    chd = open(os.path.join(sysdir, "Vela.chd")).read()
    d2 = _sheet(os.path.join(sysdir, "Vela", "Vela (Disc 2).cue"), "Vela (Disc 2).bin")
    _old(d2, os.path.join(sysdir, "Vela", "Vela (Disc 2).bin"))
    skipped = []
    assert rb_chd_plan.candidates(["psx"], skipped=skipped) == []
    assert skipped == [("psx", d2, _HERE % "Vela.chd")]
    assert rb_chd.convert({"system": "psx", "mode": "createcd", "sources": [d2],
                           "roots": [os.path.dirname(d2)]}) is False
    assert open(os.path.join(sysdir, "Vela.chd")).read() == chd and os.path.exists(d2)


def test_an_existing_file_of_another_game_is_never_taken_as_this_ones(isolated, monkeypatch, fake_es):
    """Lyra.chd is already there (another game's, newer than this cue/bin): the worker used to
    "resume" onto it, deleting Lyra.cue/bin and pointing its entry at the other game's CHD."""
    import rb_chd
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    cue = _sheet(os.path.join(sysdir, "Lyra.cue"), "Lyra.bin")
    old = time.time() - 3600
    for p in (cue, os.path.join(sysdir, "Lyra.bin")):
        os.utime(p, (old, old))
    other = os.path.join(sysdir, "Lyra.chd")
    open(other, "w").write("another game")
    _quiet(monkeypatch, rb_chd)
    assert rb_chd.convert({"system": "psx", "mode": "createcd", "sources": [cue], "roots": [cue]}) is False
    assert os.path.exists(cue) and os.path.exists(os.path.join(sysdir, "Lyra.bin"))
    assert open(other).read() == "another game"
    import rb_chd_plan
    skipped = []
    assert rb_chd_plan.candidates(["psx"], skipped=skipped) == []
    assert skipped == [("psx", cue, _HERE % "Lyra.chd")]


def test_an_output_this_game_made_before_an_interruption_is_finished(isolated, monkeypatch, fake_es):
    import rb_chd
    import rb_chd_plan
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    cue = _sheet(os.path.join(sysdir, "Lyra.cue"), "Lyra.bin")
    out = os.path.join(sysdir, "Lyra.chd")
    open(out + ".part", "w").write("chd")
    rb_chd.mark_made(out, [cue], out + ".part")       # what convert records before the rename
    os.replace(out + ".part", out)                     # ... and the power went
    assert [os.path.basename(c["files"][0]) for c in rb_chd_plan.candidates(["psx"])] == ["Lyra.cue"]
    _quiet(monkeypatch, rb_chd)
    made = []
    monkeypatch.setattr(rb_chd, "chdman", lambda mode, *a: (made.append(mode), (0, ""))[1])
    assert rb_chd.convert({"system": "psx", "mode": "createcd", "sources": [cue], "roots": [cue]}) is True
    assert made == ["verify"]                          # not converted again
    assert not os.path.exists(cue) and os.path.exists(out)
    assert not os.path.exists(rb_chd._made_path(out))   # done: a later file of this name is someone else's


def test_the_planner_never_offers_two_games_that_make_the_same_file(isolated):
    """Loose Mira.cue and a folder Mira/ both become Mira.chd: only the first is offered."""
    import rb_chd_plan
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    _sheet(os.path.join(sysdir, "Mira.cue"), "Mira.bin")
    _sheet(os.path.join(sysdir, "Mira", "Mira (USA).cue"), "Mira (USA).bin")
    skipped = []
    cands = rb_chd_plan.candidates(["psx"], skipped=skipped)
    assert len(cands) == 1
    assert len(skipped) == 1 and "would also become Mira.chd" in skipped[0][2]


def test_the_planner_leaves_out_a_game_whose_output_a_queued_task_makes(isolated):
    import rb_chd_plan
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    a = _sheet(os.path.join(sysdir, "Mira.cue"), "Mira.bin")
    _sheet(os.path.join(sysdir, "Mira", "Mira (USA).cue"), "Mira (USA).bin")
    os.makedirs(rommlib.CHD_QUEUE_DIR)
    json.dump({"system": "psx", "mode": "createcd", "sources": [a], "roots": [a]},
              open(os.path.join(rommlib.CHD_QUEUE_DIR, "1-00-00000.json"), "w"))
    skipped = []
    assert rb_chd_plan.candidates(["psx"], skipped=skipped) == []
    assert "would also become Mira.chd" in skipped[0][2]


# ------------------------------------------------------------ 2, 9. tracks

def test_a_track_still_being_copied_holds_its_game_back(isolated, monkeypatch):
    import rb_chd_plan
    monkeypatch.setattr(rb_chd_plan, "STILL_WRITTEN", 60)
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    cue = _sheet(os.path.join(sysdir, "Nadir.cue"), "Nadir.bin")
    old = time.time() - 600
    os.utime(cue, (old, old))                          # the sheet is old; its bin still growing
    skipped = []
    assert rb_chd_plan.candidates(["psx"], skipped=skipped) == []
    assert "still being written" in skipped[0][2]


def test_a_sheet_whose_track_is_missing_is_listed_with_why(isolated):
    import rb_chd_plan
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    cue = _sheet(os.path.join(sysdir, "Nadir.cue"), "Nadir.bin")
    os.remove(os.path.join(sysdir, "Nadir.bin"))
    skipped = []
    assert rb_chd_plan.candidates(["psx"], skipped=skipped) == []
    assert skipped == [("psx", cue, "a track is missing: Nadir.bin")]


def test_a_track_two_sheets_share_counts_once(isolated):
    import rb_chd_plan
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    _sheet(os.path.join(sysdir, "Nova.cue"), "Nova.bin", size=10000)
    open(os.path.join(sysdir, "Nova (Alt).cue"), "w").write(CUE % "Nova.bin")
    cands = rb_chd_plan.candidates(["psx"])
    assert len(cands) == 2
    cues = sum(os.path.getsize(os.path.join(sysdir, n)) for n in ("Nova.cue", "Nova (Alt).cue"))
    assert sum(c["size"] for c in cands) == 10000 + cues


# ------------------------------------------------------------ 3, 6, 8. grouping

def test_a_folder_of_several_multi_disc_games_is_one_set_per_title(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    for n in ("Arca (Disc 1)", "Arca (Disc 2)", "Bora (CD1)", "Bora (CD2)", "Cirro"):
        _sheet(os.path.join(sysdir, "Pack", n + ".cue"))
    assert _sets("psx") == [
        ("Pack", ["Arca (Disc 1).cue", "Arca (Disc 2).cue"], "Arca"),
        ("Pack", ["Bora (CD1).cue", "Bora (CD2).cue"], "Bora"),
        ("Pack/Cirro.cue", ["Cirro.cue"], None)]


def test_every_disc_tag_disc_info_knows_makes_a_set(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    for n in ("Duna (Disc 1 of 2)", "Duna (Disc 2 of 2)"):
        _sheet(os.path.join(sysdir, "Duna", n + ".cue"))
    for n in ("Eos (Disk A)", "Eos (Disk B)"):
        _sheet(os.path.join(sysdir, "Eos", "Eos (USA)", n + ".cue"))
    for n in ("Fenix (USA) (CD 1)", "Fenix (USA) (CD 2)"):
        _sheet(os.path.join(sysdir, "Fenix", n, n + ".cue"))
    assert _sets("psx") == [
        ("Duna", ["Duna (Disc 1 of 2).cue", "Duna (Disc 2 of 2).cue"], None),
        ("Eos/Eos (USA)", ["Eos (Disk A).cue", "Eos (Disk B).cue"], None),
        ("Fenix", ["Fenix (USA) (CD 1).cue", "Fenix (USA) (CD 2).cue"], None)]


def test_cd32_and_dvd5_are_not_disc_numbers(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "amigacd32")
    for n in ("Gala (CD32)", "Hydra (CD32)"):
        _sheet(os.path.join(sysdir, "Pack", n + ".cue"))
    assert [fs for _, fs, _ in _sets("amigacd32")] == [["Gala (CD32).cue"], ["Hydra (CD32).cue"]]
    import rb_chd
    assert rb_chd.disc_title("Iris (DVD9)") is None and rb_chd.disc_title("Iris (DVD 2)") == "Iris"


def test_bare_disc_folders_are_discs_of_their_game(isolated):
    """Bare disc folders fold into their game only when their sheets share a title (named after
    the game's folder, or one name ignoring the disc tag); a lone one is a game of its own."""
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    _sheet(os.path.join(sysdir, "Juno", "Disc 1", "Juno (Disc 1).cue"))
    _sheet(os.path.join(sysdir, "Juno", "Disc 2", "Juno (Disc 2).cue"))
    _sheet(os.path.join(sysdir, "Lark (USA)", "Disc 1", "lark (Disc 1).cue"))
    _sheet(os.path.join(sysdir, "Lark (USA)", "Disc 2", "Lark (Disc 2).cue"))
    _sheet(os.path.join(sysdir, "Kora", "CD1", "kora.cue"))
    assert _sets("psx") == [("Juno", ["Juno (Disc 1).cue", "Juno (Disc 2).cue"], None),
                            ("Kora/CD1", ["kora.cue"], None),
                            ("Lark (USA)", ["lark (Disc 1).cue", "Lark (Disc 2).cue"], None)]


def test_bare_cd_folders_of_two_games_are_two_games(isolated):
    """Burned/CD1/Alpha.cue and Burned/CD2/Beta.cue: two games burned to one folder, never a set."""
    import rb_chd_plan
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    _sheet(os.path.join(sysdir, "Burned", "CD1", "Alpha.cue"), "Alpha.bin")
    _sheet(os.path.join(sysdir, "Burned", "CD2", "Beta.cue"), "Beta.bin")
    _old(*[os.path.join(dp, f) for dp, _, fs in os.walk(sysdir) for f in fs])
    assert sorted((os.path.relpath(c["root"], sysdir), [os.path.basename(f) for f in c["files"]], c["name"])
                  for c in rb_chd_plan.candidates(["psx"])) == [
        ("Burned/CD1", ["Alpha.cue"], None), ("Burned/CD2", ["Beta.cue"], None)]


def test_a_lone_bare_disc_folder_never_takes_its_game_folder_with_it(isolated, monkeypatch, fake_es):
    """Hale/Disc 1/x.cue alone: its game folder is Disc 1/, so Hale/'s manual and cover stay."""
    import rb_chd
    import rb_chd_plan
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    _sheet(os.path.join(sysdir, "Hale", "Disc 1", "x.cue"), "x.bin")
    for n in ("manual.pdf", "cover.jpg"):
        open(os.path.join(sysdir, "Hale", n), "w").write(n)
    _old(*[os.path.join(dp, f) for dp, _, fs in os.walk(sysdir) for f in fs])
    (c,) = rb_chd_plan.candidates(["psx"])
    assert c["root"] == os.path.join(sysdir, "Hale", "Disc 1")
    rb_chd_plan.queue([c])
    (name,) = os.listdir(rommlib.CHD_QUEUE_DIR)
    _quiet(monkeypatch, rb_chd)
    assert rb_chd.convert(json.load(open(os.path.join(rommlib.CHD_QUEUE_DIR, name)))) is True
    assert sorted(os.listdir(os.path.join(sysdir, "Hale"))) == ["Disc 1.chd", "cover.jpg", "manual.pdf"]


def test_the_worker_names_a_loose_set_s_playlist_by_any_disc_tag(isolated, monkeypatch, fake_es):
    import rb_chd
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    fs = [_sheet(os.path.join(sysdir, "Zeta (CD%d) (Rev 1).cue" % n)) for n in (1, 2)]
    _quiet(monkeypatch, rb_chd)
    assert rb_chd.convert({"system": "psx", "mode": "createcd", "sources": fs, "roots": [sysdir]}) is True
    assert os.path.exists(os.path.join(sysdir, "Zeta.m3u"))


# ------------------------------------------------------------ 4. a .cue beside its .gdi

def _gdi_game(sysdir):
    d = os.path.join(sysdir, "Lumen")
    os.makedirs(d)
    open(os.path.join(d, "track01.bin"), "wb").write(b"\0" * 2352)
    open(os.path.join(d, "Lumen.gdi"), "w").write("1\n1 0 4 2352 track01.bin 0\n")
    open(os.path.join(d, "Lumen.cue"), "w").write(CUE % "track01.bin")
    return d


def test_a_cue_beside_its_gdi_goes_with_the_game(isolated, monkeypatch, fake_es):
    """The planner converts the .gdi; its .cue named the same tracks, so the worker kept
    everything and ES listed the game twice. The cue now travels with the task."""
    import rb_chd
    import rb_chd_plan
    sysdir = os.path.join(rommlib.ROMS_ROOT, "dreamcast")
    d = _gdi_game(sysdir)
    (c,) = rb_chd_plan.candidates(["dreamcast"])
    assert [os.path.basename(f) for f in c["files"]] == ["Lumen.gdi"]
    assert c["also"] == [os.path.join(d, "Lumen.cue")]
    assert rb_chd_plan.queue([c]) == 1
    (name,) = os.listdir(rommlib.CHD_QUEUE_DIR)
    task = json.load(open(os.path.join(rommlib.CHD_QUEUE_DIR, name)))
    assert task["also"] == c["also"]
    _quiet(monkeypatch, rb_chd)
    assert rb_chd.convert(task) is True
    assert os.path.exists(os.path.join(sysdir, "Lumen.chd")) and not os.path.exists(d)


def test_a_task_without_also_keeps_the_old_behaviour(isolated, monkeypatch, fake_es):
    """A task from an older app: the cue still names the track, so the track stays."""
    import rb_chd
    sysdir = os.path.join(rommlib.ROMS_ROOT, "dreamcast")
    d = _gdi_game(sysdir)
    _quiet(monkeypatch, rb_chd)
    assert rb_chd.convert({"system": "dreamcast", "mode": "createcd", "sources": [os.path.join(d, "Lumen.gdi")],
                           "roots": [d]}) is True
    assert os.path.exists(os.path.join(d, "track01.bin")) and os.path.exists(os.path.join(d, "Lumen.cue"))


def test_a_loose_cue_beside_an_unrelated_loose_gdi_stays_a_game(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "dreamcast")
    os.makedirs(sysdir)
    open(os.path.join(sysdir, "Mono.gdi"), "w").write("1\n")
    _sheet(os.path.join(sysdir, "Nimbus.cue"))
    assert [fs for _, fs, _ in _sets("dreamcast")] == [["Mono.gdi"], ["Nimbus.cue"]]


# ------------------------------------------------------------ 5. AppleDouble files

def test_dot_files_are_never_games(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    _sheet(os.path.join(sysdir, "Orla.cue"))
    _sheet(os.path.join(sysdir, "._Orla.cue"))
    _sheet(os.path.join(sysdir, "Pavo", "Pavo.cue"))
    _sheet(os.path.join(sysdir, "Pavo", "._Pavo.cue"))
    os.makedirs(os.path.join(rommlib.ROMS_ROOT, "gamecube"))
    for n in ("Quill.iso", "._Quill.iso"):
        open(os.path.join(rommlib.ROMS_ROOT, "gamecube", n), "wb").write(b"\0" * 64)
    assert _sets("psx") == [("Orla.cue", ["Orla.cue"], None), ("Pavo", ["Pavo.cue"], None)]
    assert [fs for _, fs, _ in _sets("gamecube")] == [["Quill.iso"]]


# ------------------------------------------------------------ 10. queue and the command line

def test_one_game_that_cant_be_queued_doesnt_stop_the_rest(isolated, monkeypatch):
    import rb_chd_plan
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    _sheet(os.path.join(sysdir, "Rho.cue"))
    _sheet(os.path.join(sysdir, "Sigma.cue"))
    real = rommlib.publish_queue_task

    def publish(task, name, **kw):
        if "Rho" in task["sources"][0]:
            raise OSError(28, "No space left on device")
        return real(task, name, **kw)
    monkeypatch.setattr(rommlib, "publish_queue_task", publish)
    said = []
    assert rb_chd_plan.queue(rb_chd_plan.candidates(["psx"]), say=said.append) == 1
    assert any("couldn't queue Rho.cue" in s for s in said)


def test_the_command_line_leaves_xbox_images_alone_while_a_game_may_run(isolated, monkeypatch, capsys):
    import rb_chd_plan
    sysdir = os.path.join(rommlib.ROMS_ROOT, "xbox")
    os.makedirs(sysdir)
    x = os.path.join(sysdir, "Tau (USA).xiso")
    open(x, "wb").write(b"\0" * 4096)
    _sheet(os.path.join(rommlib.ROMS_ROOT, "psx", "Ursa.cue"))
    monkeypatch.setattr(rommlib, "es_running_game", lambda: None)       # ES can't be asked
    monkeypatch.setattr(sys, "argv", ["rb_chd_plan.py", "--systems", "xbox,psx"])
    rb_chd_plan.main()
    assert os.path.exists(x)
    out = capsys.readouterr().out
    assert "queued 1 games" in out and "a game is running" in out
    monkeypatch.setattr(rommlib, "es_running_game", lambda: "")
    rb_chd_plan.main()
    assert not os.path.exists(x) and os.path.exists(os.path.join(sysdir, "Tau (USA).iso"))
    assert "queued 1 games" in capsys.readouterr().out     # the .iso made bootable; the psx one was queued


# ------------------------------------------------------------ review round 2

def _fake_chd(path, body=b"same disc"):
    open(path, "wb").write(b"MComprHD" + body)
    return path


def _chdman_writing_chds(monkeypatch, rb_chd, body=b"same disc"):
    """chdman that makes a CHD-looking file."""
    _quiet(monkeypatch, rb_chd)
    monkeypatch.setattr(rb_chd, "chdman", lambda mode, *a: (
        mode == "verify" or _fake_chd(a[a.index("-o") + 1], body)) and (0, ""))


def _old(*paths):
    t = time.time() - 3600
    for p in paths:
        os.utime(p, (t, t))


def test_an_unrelated_cue_in_a_folder_with_one_gdi_is_its_own_game(isolated, monkeypatch, fake_es):
    """Homebrew/Alpha.gdi and Homebrew/Beta.cue (another game): Beta used to be set aside as
    Alpha's "also" sheet and deleted with it."""
    import rb_chd
    import rb_chd_plan
    sysdir = os.path.join(rommlib.ROMS_ROOT, "dreamcast")
    d = os.path.join(sysdir, "Homebrew")
    os.makedirs(d)
    open(os.path.join(d, "Alpha.gdi"), "w").write("1\n1 0 4 2352 track01.bin 0\n")
    open(os.path.join(d, "track01.bin"), "wb").write(b"\0" * 2352)
    beta = _sheet(os.path.join(d, "Beta.cue"), "Beta.bin")
    _old(*[os.path.join(d, n) for n in os.listdir(d)])
    cands = rb_chd_plan.candidates(["dreamcast"])
    assert sorted(os.path.basename(c["files"][0]) for c in cands) == ["Alpha.gdi", "Beta.cue"]
    assert all(c["also"] == [] for c in cands)
    # a task naming Beta.cue as Alpha's anyway (forged, or an older planner's): the worker never takes it
    _quiet(monkeypatch, rb_chd)
    assert rb_chd.convert({"system": "dreamcast", "mode": "createcd", "sources": [os.path.join(d, "Alpha.gdi")],
                           "roots": [os.path.join(d, "Alpha.gdi")], "also": [beta]}) is True
    assert os.path.exists(beta) and os.path.exists(os.path.join(d, "Beta.bin"))


def test_a_cue_of_the_gdi_s_name_with_tracks_of_its_own_is_its_own_game(isolated, monkeypatch, fake_es):
    """Lumen/Lumen.gdi and Lumen/Lumen.cue naming "Lumen (Track 1).bin" (a second, full dump):
    the cue was set aside by its name and deleted, its tracks with it."""
    import rb_chd
    import rb_chd_plan
    sysdir = os.path.join(rommlib.ROMS_ROOT, "dreamcast")
    d = os.path.join(sysdir, "Lumen")
    os.makedirs(d)
    open(os.path.join(d, "Lumen.gdi"), "w").write("1\n1 0 4 2352 track01.bin 0\n")
    open(os.path.join(d, "track01.bin"), "wb").write(b"\0" * 2352)
    cue = _sheet(os.path.join(d, "Lumen.cue"), "Lumen (Track 1).bin", size=4096)
    _old(*[os.path.join(d, n) for n in os.listdir(d)])
    skipped = []
    cands = rb_chd_plan.candidates(["dreamcast"], skipped=skipped)
    assert [(os.path.basename(c["files"][0]), c["also"]) for c in cands] == [("Lumen.cue", [])]
    assert "would also become Lumen.chd" in skipped[0][2]          # both make Lumen/Lumen.chd: one waits
    _quiet(monkeypatch, rb_chd)
    assert rb_chd.convert({"system": "dreamcast", "mode": "createcd", "sources": [os.path.join(d, "Lumen.gdi")],
                           "roots": [os.path.join(d, "Lumen.gdi")], "also": [cue]}) is True
    assert os.path.exists(cue) and os.path.exists(os.path.join(d, "Lumen (Track 1).bin"))


def test_a_cue_naming_the_gdi_s_tracks_and_one_more_is_its_own_game(isolated):
    import rb_chd_plan
    sysdir = os.path.join(rommlib.ROMS_ROOT, "dreamcast")
    d = _gdi_game(sysdir)
    open(os.path.join(d, "extra.bin"), "wb").write(b"\0" * 2352)
    open(os.path.join(d, "Lumen.cue"), "w").write(CUE % "track01.bin" + CUE % "extra.bin")
    files, also = rb_chd_plan._gdi_pairs(sorted(os.path.join(d, n) for n in ("Lumen.cue", "Lumen.gdi")))
    assert also == {} and len(files) == 2


def test_a_set_aside_sheet_in_use_holds_the_game_back(isolated, monkeypatch, fake_es):
    import rb_chd
    sysdir = os.path.join(rommlib.ROMS_ROOT, "dreamcast")
    d = _gdi_game(sysdir)
    _quiet(monkeypatch, rb_chd)
    cue = os.path.join(d, "Lumen.cue")
    monkeypatch.setattr(rb_chd, "running_game", lambda: cue)
    assert rb_chd.convert({"system": "dreamcast", "mode": "createcd", "sources": [os.path.join(d, "Lumen.gdi")],
                           "roots": [d], "also": [cue]}) is False
    assert "in use" in rb_chd._state["last"] and os.path.exists(cue)


def test_a_set_aside_sheet_changed_while_converting_keeps_everything(isolated, monkeypatch, fake_es):
    import rb_chd
    sysdir = os.path.join(rommlib.ROMS_ROOT, "dreamcast")
    d = _gdi_game(sysdir)
    cue = os.path.join(d, "Lumen.cue")
    _quiet(monkeypatch, rb_chd)

    def chdman(mode, *a):
        if mode != "verify":
            open(a[a.index("-o") + 1], "w").write("chd")
            open(cue, "a").write("REM edited\n")            # a fresh copy of the cue lands meanwhile
        return 0, ""
    monkeypatch.setattr(rb_chd, "chdman", chdman)
    assert rb_chd.convert({"system": "dreamcast", "mode": "createcd", "sources": [os.path.join(d, "Lumen.gdi")],
                           "roots": [d], "also": [cue]}) is False
    assert os.path.exists(cue) and os.path.exists(os.path.join(d, "track01.bin"))
    assert not os.path.exists(os.path.join(sysdir, "Lumen.chd"))


def test_bare_disc_folders_whose_sheets_share_a_name_get_a_name_each(isolated, monkeypatch, fake_es):
    """Orbis/CD1/orbis.cue and Orbis/CD2/orbis.cue both became orbis.chd: skipped as a name clash, every time."""
    import rb_chd
    import rb_chd_plan
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    for n in (1, 2):
        _sheet(os.path.join(sysdir, "Orbis", "CD%d" % n, "orbis.cue"), "orbis.bin")
    _old(*[os.path.join(dp, f) for dp, _, fs in os.walk(sysdir) for f in fs])
    (c,) = rb_chd_plan.candidates(["psx"])
    assert rb_chd_plan.queue([c]) == 1
    (name,) = os.listdir(rommlib.CHD_QUEUE_DIR)
    _quiet(monkeypatch, rb_chd)
    assert rb_chd.convert(json.load(open(os.path.join(rommlib.CHD_QUEUE_DIR, name)))) is True
    assert sorted(os.listdir(sysdir)) == ["Orbis (CD1).chd", "Orbis (CD2).chd", "Orbis.m3u"]


def test_a_set_whose_discs_would_share_a_file_is_listed_with_why(isolated):
    import rb_chd_plan
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    _sheet(os.path.join(sysdir, "Pack", "Pyxis (Disc 1).cue"))
    _sheet(os.path.join(sysdir, "Pack", "pyxis (disc 1).cue"))        # the same name on a FAT drive
    skipped = []
    assert rb_chd_plan.candidates(["psx"], skipped=skipped) == []
    assert "two of its discs would both become" in skipped[0][2]


def _lyra(sysdir):
    cue = _sheet(os.path.join(sysdir, "Lyra.cue"), "Lyra.bin")
    out = _fake_chd(os.path.join(sysdir, "Lyra.chd"))       # an older worker's: no record
    _old(cue, os.path.join(sysdir, "Lyra.bin"))
    return cue, out


def test_a_chd_beside_its_originals_that_no_record_names_is_never_touched(isolated, monkeypatch, fake_es):
    """Lyra.chd (an older worker's, a copy kept on purpose, or another game's) beside Lyra.cue/bin:
    the worker used to convert again and adopt or replace it. Now: listed, nothing converted or
    deleted, every time."""
    import pytest
    import rb_chd
    import rb_chd_plan
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    cue, out = _lyra(sysdir)
    _quiet(monkeypatch, rb_chd)
    monkeypatch.setattr(rb_chd, "chdman", lambda *a: pytest.fail("must not convert"))
    for _ in range(2):
        skipped = []
        assert rb_chd_plan.candidates(["psx"], skipped=skipped) == []
        assert skipped == [("psx", cue, _HERE % "Lyra.chd")]
        assert rb_chd.convert({"system": "psx", "mode": "createcd", "sources": [cue], "roots": [cue]}) is False
        assert "a file named Lyra.chd is already here" in rb_chd._state["last"]
    assert sorted(os.listdir(sysdir)) == ["Lyra.bin", "Lyra.chd", "Lyra.cue"]
    assert open(out, "rb").read() == b"MComprHDsame disc"


def test_a_set_never_touches_a_chd_of_one_of_its_discs_already_there(isolated, monkeypatch, fake_es):
    """Set (Disc 1).chd is the owner's own; disc 2 then failed: the old check adopted the existing
    CHD into what the task made, and dropped it with the rest."""
    import rb_chd
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    d1 = _sheet(os.path.join(sysdir, "Set", "Set (Disc 1).cue"), "Set (Disc 1).bin")
    d2 = _sheet(os.path.join(sysdir, "Set", "Set (Disc 2).cue"), "Set (Disc 2).bin")
    pre = _fake_chd(os.path.join(sysdir, "Set (Disc 1).chd"))
    _chdman_writing_chds(monkeypatch, rb_chd)
    assert rb_chd.convert({"system": "psx", "mode": "createcd", "sources": [d1, d2],
                           "roots": [os.path.join(sysdir, "Set")]}) is False
    assert sorted(os.listdir(sysdir)) == ["Set", "Set (Disc 1).chd"]
    assert open(pre, "rb").read() == b"MComprHDsame disc" and os.path.exists(d1) and os.path.exists(d2)


def test_an_output_that_appears_while_converting_is_never_replaced(isolated, monkeypatch, fake_es):
    import rb_chd
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    cue = _sheet(os.path.join(sysdir, "Lyra.cue"), "Lyra.bin")
    out = os.path.join(sysdir, "Lyra.chd")
    _quiet(monkeypatch, rb_chd)

    def chdman(mode, *a):
        if mode != "verify":
            open(a[a.index("-o") + 1], "w").write("chd")
            open(out, "w").write("someone else's")
        return 0, ""
    monkeypatch.setattr(rb_chd, "chdman", chdman)
    assert rb_chd.convert({"system": "psx", "mode": "createcd", "sources": [cue], "roots": [cue]}) is False
    assert open(out).read() == "someone else's" and os.path.exists(cue)
    assert sorted(os.listdir(sysdir)) == ["Lyra.bin", "Lyra.chd", "Lyra.cue"]


def test_the_new_output_is_on_disk_before_it_takes_its_name(isolated, monkeypatch, fake_es):
    """A power cut right after the rename must not leave a broken file under the game's name."""
    import rb_chd
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    cue = _sheet(os.path.join(sysdir, "Lyra.cue"), "Lyra.bin")
    out = os.path.join(sysdir, "Lyra.chd")
    _quiet(monkeypatch, rb_chd)
    seen = []
    real_sync, real_replace = rommlib.sync_file, os.replace
    monkeypatch.setattr(rommlib, "sync_file", lambda p: (seen.append(("sync", p)), real_sync(p)))
    monkeypatch.setattr(rb_chd, "_sync_dir", lambda p: seen.append(("dir", p)))
    monkeypatch.setattr(rb_chd.os, "replace", lambda a, b: (seen.append(("replace", a, b)), real_replace(a, b)))
    assert rb_chd.convert({"system": "psx", "mode": "createcd", "sources": [cue], "roots": [cue]}) is True
    i = seen.index(("replace", out + ".part", out))
    assert ("sync", out + ".part") in seen[:i] and ("dir", sysdir) in seen[i + 1:]


def test_two_games_whose_outputs_differ_only_in_case_are_not_both_offered(isolated):
    """Mira.cue -> Mira.chd and the folder mira/ -> mira.chd: one file on a FAT/exFAT drive."""
    import rb_chd_plan
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    a = _sheet(os.path.join(sysdir, "Mira.cue"), "Mira.bin")
    _sheet(os.path.join(sysdir, "mira", "x.cue"), "x.bin")
    _old(*[os.path.join(dp, f) for dp, _, fs in os.walk(sysdir) for f in fs])
    skipped = []
    assert len(rb_chd_plan.candidates(["psx"], skipped=skipped)) == 1
    assert "would also become" in skipped[0][2]
    os.makedirs(rommlib.CHD_QUEUE_DIR)                       # ... nor beside a queued task's
    json.dump({"system": "psx", "mode": "createcd", "sources": [a], "roots": [a]},
              open(os.path.join(rommlib.CHD_QUEUE_DIR, "1-00-00000.json"), "w"))
    skipped = []
    assert rb_chd_plan.candidates(["psx"], skipped=skipped) == []
    assert skipped[0][2] == "another game here would also become mira.chd"


def test_a_file_dated_in_the_future_doesnt_hold_its_game_back_for_good(isolated, monkeypatch):
    """A box whose clock is behind: a copied file's time is ahead of now, for days maybe."""
    import rb_chd_plan
    monkeypatch.setattr(rb_chd_plan, "STILL_WRITTEN", 60)
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    cue = _sheet(os.path.join(sysdir, "Nadir.cue"), "Nadir.bin")
    t = time.time() + 86400
    for p in (cue, os.path.join(sysdir, "Nadir.bin")):
        os.utime(p, (t, t))
    assert len(rb_chd_plan.candidates(["psx"])) == 1
    t = time.time() + 30                                   # just ahead: a copy landing now, a clock a bit off
    os.utime(cue, (t, t))
    skipped = []
    assert rb_chd_plan.candidates(["psx"], skipped=skipped) == []
    assert "still being written" in skipped[0][2]


def test_records_of_a_drive_not_mounted_yet_are_kept(isolated, tmp_path):
    import rb_chd
    here = str(tmp_path / "roms" / "psx")
    os.makedirs(here, exist_ok=True)
    gone = os.path.join(here, "Gone.chd")
    late = str(tmp_path / "usb" / "roms" / "psx" / "Late.chd")       # its drive isn't there yet
    old_late = str(tmp_path / "usb2" / "Old.chd")
    for out in (gone, late, old_late):
        p = rb_chd._made_path(out)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        json.dump({"out": out, "sources": [], "size": 1, "mtime": 0}, open(p, "w"))
    t = time.time() - rb_chd.PRUNE_AFTER - 60
    os.utime(rb_chd._made_path(old_late), (t, t))
    rb_chd._prune_made()
    assert not os.path.exists(rb_chd._made_path(gone))
    assert os.path.exists(rb_chd._made_path(late))
    assert not os.path.exists(rb_chd._made_path(old_late))


def test_a_record_survives_a_fat_time_zone_shift(isolated):
    import rb_chd
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    cue = _sheet(os.path.join(sysdir, "Lyra.cue"), "Lyra.bin")
    out = os.path.join(sysdir, "Lyra.chd")
    open(out + ".part", "w").write("chd")
    rb_chd.mark_made(out, [cue], out + ".part")
    os.replace(out + ".part", out)
    m = os.path.getmtime(out)
    for shift, ok in ((3600, True), (-7200 + 1, True), (5.5 * 3600, True), (3600 + 30, False), (60, False)):
        os.utime(out, (m + shift, m + shift))
        assert rb_chd.made_from(out, cue) is ok, shift
    assert rb_chd.same_time(0, 1.5) and not rb_chd.same_time(0, 20 * 3600)


def test_cant_convert_advice_fits_each_reason():
    from rbui.screens.compress import cant_convert_advice as advice
    assert "Put the missing track back" in advice("psx", "a track is missing: X.bin")
    assert "Wait until" in advice("psx", "it's still being written; try again in a minute")
    assert "Rename one of the two" in advice("psx", "another game here would also become X.chd")
    assert "each disc gets" in advice("psx", "two of its discs would both become X.chd; rename one of them")
    assert "delete the other" in advice("psx", _HERE % "X.chd")
    assert "rename one of them" in advice("psx", _HERE % "X.chd")
    assert "Nothing to do" in advice("wiiu", "can't decrypt it: no key; offered as .wux instead")
    assert "decrypted dump" in advice("3ds", "an encrypted dump (Azahar runs decrypted ones only)")
    assert "good copy" in advice("psp", "not a CSO file")


def test_cant_convert_sizes_are_read_in_the_scan_not_when_drawn(isolated, rb, monkeypatch):
    import pygame
    import rbui.screens.compress as compress
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_TOKEN", "x")
    pygame.init()
    app = rb.App(None, headless=True)
    sc = rb.CompressScreen(app)
    app.push(sc)
    monkeypatch.setattr(compress, "game_size", lambda f: 1 / 0)       # never on the UI thread
    sc._loaded([], [("psx", "/r/psx/x.cue", "a track is missing: x.bin")], {}, {"/r/psx/x.cue": 5 << 20})
    assert sc.rows[0]["size"] == 5 << 20
    sc.draw(app.canvas)
    sc.action("ok")
    why = app.stack[-1]
    assert isinstance(why, rb.CantConvertScreen)
    why.draw(app.canvas)
    assert why.size(0) == "5.0 MB"


def test_the_worker_refuses_two_discs_whose_files_differ_only_in_case(isolated, monkeypatch):
    """On a FAT/exFAT drive "Pyxis (Disc 1).chd" and "pyxis (disc 1).chd" are one file."""
    import pytest
    import rb_chd
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    srcs = sorted(_sheet(os.path.join(sysdir, "Pack", n + ".cue")) for n in ("Pyxis (Disc 1)", "pyxis (disc 1)"))
    _quiet(monkeypatch, rb_chd)
    monkeypatch.setattr(rb_chd, "chdman", lambda *a: pytest.fail("must not convert"))
    assert rb_chd.convert({"system": "psx", "mode": "createcd", "sources": srcs,
                           "roots": [os.path.join(sysdir, "Pack")]}) is False
    assert "name clash" in rb_chd._state["last"]


def test_a_chd_finishes_when_installed_json_cant_be_updated(isolated, monkeypatch):
    """The CHD is in and ES has its rename: an unreadable installed.json must not
    leave the originals beside it (double the space) with the task dropped."""
    import rb_chd
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    os.makedirs(sysdir)
    open(os.path.join(sysdir, "Nova.bin"), "wb").write(b"\0" * 4096)
    open(os.path.join(sysdir, "Nova.cue"), "w").write('FILE "Nova.bin" BINARY\n  TRACK 01 MODE2/2352\n')

    made = []

    def chdman(*args):                                   # rb_chd's own argv: write what follows -o
        if "-o" in args:
            out = args[args.index("-o") + 1]
            open(out, "wb").write(b"MComprHD" + b"\0" * 100)
            made.append(out)
        return 0, ""
    monkeypatch.setattr(rb_chd, "chdman", chdman)
    monkeypatch.setattr(rb_chd, "running_game", lambda: "")
    monkeypatch.setattr(rommlib, "es_busy", lambda app_busy=False, get=None: "")

    def unreadable(self, *a, **k):
        raise rommlib.LibraryUnreadable("installed.json can't be read now; try again")
    monkeypatch.setattr(rommlib.Library, "rename_paths", unreadable)
    rb_chd.convert({"system": "psx", "mode": "createcd", "sources": [os.path.join(sysdir, "Nova.cue")],
                    "roots": []})
    assert made and all(os.path.dirname(m).startswith(rommlib.ROMS_ROOT) for m in made)   # never into the repo
    assert os.path.exists(os.path.join(sysdir, "Nova.chd"))
    assert not os.path.exists(os.path.join(sysdir, "Nova.bin"))


# ------------------------------------------------------------ review round 4

def test_a_lone_disc_s_name_taken_by_another_disc_never_advises_deleting_one(isolated, monkeypatch, fake_es):
    """Vela.chd is disc 1; disc 2 would become Vela.chd too. Both play: the reason and the advice
    never say to delete one of them as if they were copies."""
    from rbui.screens.compress import cant_convert_advice
    import rb_chd
    import rb_chd_plan
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    d2 = _sheet(os.path.join(sysdir, "Vela", "Vela (Disc 2).cue"), "Vela (Disc 2).bin")
    _old(d2, os.path.join(sysdir, "Vela", "Vela (Disc 2).bin"))
    _fake_chd(os.path.join(sysdir, "Vela.chd"))
    skipped = []
    assert rb_chd_plan.candidates(["psx"], skipped=skipped) == []
    (_, _, why), = skipped
    assert "another disc or game" in why and "delete" not in why
    assert "rename one of them" in cant_convert_advice("psx", why)
    _quiet(monkeypatch, rb_chd)
    assert rb_chd.convert({"system": "psx", "mode": "createcd", "sources": [d2],
                           "roots": [os.path.dirname(d2)]}) is False
    assert rb_chd._state["last"] == "SKIP Vela (Disc 2).cue: " + _HERE % "Vela.chd"     # names, not paths


def _own(out, srcs):
    """out as an interrupted run of srcs left it: recorded, then renamed, then the power went."""
    os.replace(out, out + ".part")
    import rb_chd
    rb_chd.mark_made(out, srcs, out + ".part")
    os.replace(out + ".part", out)


def test_an_own_chd_that_fails_its_verify_is_made_again(isolated, monkeypatch, fake_es):
    """A power cut broke the CHD after its rename: the planner offers it (it's ours) and the worker
    used to skip it as "both here", every time. Now it converts again over it."""
    import rb_chd
    import rb_chd_plan
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    cue = _sheet(os.path.join(sysdir, "Lyra.cue"), "Lyra.bin")
    _old(cue, os.path.join(sysdir, "Lyra.bin"))
    out = _fake_chd(os.path.join(sysdir, "Lyra.chd"), b"broken")
    _own(out, [cue])
    assert len(rb_chd_plan.candidates(["psx"])) == 1
    _quiet(monkeypatch, rb_chd)
    monkeypatch.setattr(rb_chd, "chdman", lambda mode, *a: (
        (1, "bad") if mode == "verify" and not a[-1].endswith(".part") else
        ((mode == "verify" or _fake_chd(a[a.index("-o") + 1], b"good")) and (0, ""))))
    assert rb_chd.convert({"system": "psx", "mode": "createcd", "sources": [cue], "roots": [cue]}) is True
    assert open(out, "rb").read() == b"MComprHDgood" and sorted(os.listdir(sysdir)) == ["Lyra.chd"]
    assert not rb_chd.made_from(out, cue) and rb_chd_plan.candidates(["psx"]) == []


def test_an_own_chd_older_than_its_sources_is_made_again(isolated, monkeypatch, fake_es):
    """Sources dated after the CHD (a fresh copy, or a computer's clock ahead): never resumed, and
    no longer skipped forever either."""
    import rb_chd
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    cue = _sheet(os.path.join(sysdir, "Lyra.cue"), "Lyra.bin")
    out = _fake_chd(os.path.join(sysdir, "Lyra.chd"), b"old")
    _own(out, [cue])
    _old(out)
    _chdman_writing_chds(monkeypatch, rb_chd, b"new")
    assert rb_chd.convert({"system": "psx", "mode": "createcd", "sources": [cue], "roots": [cue]}) is True
    assert open(out, "rb").read() == b"MComprHDnew"


def _wiiu(monkeypatch, size):
    import rb_chd
    from romm import tools as T
    monkeypatch.setattr(T, "wuatool_path", lambda: "/fake/wuatool")
    monkeypatch.setattr(T, "wua_supported", lambda: True)
    monkeypatch.setattr(rb_chd, "running_game", lambda: "")
    monkeypatch.setattr(rommlib, "es_busy", lambda app_busy=False, get=None: "")
    monkeypatch.setattr(rommlib, "load_config", lambda: dict(rommlib.DEFAULT_CONFIG, keep_free_gb=0, wux=True))
    monkeypatch.setattr(rommlib, "wiiu_check", lambda src: None)
    monkeypatch.setattr(rb_chd, "WUD_SIZE", 0x30000)              # a full disc, at test size
    ran = []

    def fake_wux(src, tmp, threads):
        ran.append(src)
        open(tmp, "wb").write(b"WUX0" + b"\0" * 12 + os.path.getsize(src).to_bytes(8, "little"))
        return 0, "ok"
    monkeypatch.setitem(rb_chd.RUNNERS, "wux", fake_wux)
    disc = os.path.join(rommlib.ROMS_ROOT, "wiiu", "Lorem.wud")
    os.makedirs(os.path.dirname(disc))
    data = bytearray(size)
    data[0x10000:0x10004] = (0xCC549EB9).to_bytes(4, "big")          # a Wii U disc's magic
    open(disc, "wb").write(bytes(data))
    _old(disc)
    return rb_chd, disc, disc[:-4] + ".wux", ran


def _wux(path, size):
    open(path, "wb").write(b"WUX0" + b"\0" * 12 + size.to_bytes(8, "little"))
    return path


def test_a_full_wux_beside_a_cut_short_wud_is_never_replaced(isolated, monkeypatch, fake_es):
    rb_chd, disc, out, ran = _wiiu(monkeypatch, 0x20000)
    _wux(out, 0x30000)
    task = {"system": "wiiu", "mode": "wux", "sources": [disc], "roots": [disc]}
    assert rb_chd.convert(task) is False and ran == []
    assert rb_chd._state["last"] == "SKIP Lorem.wud: " + _HERE % "Lorem.wux"
    assert int.from_bytes(open(out, "rb").read()[0x10:0x18], "little") == 0x30000


def test_a_wux_of_a_cut_short_wud_is_replaced_by_a_full_one(isolated, monkeypatch, fake_es):
    rb_chd, disc, out, ran = _wiiu(monkeypatch, 0x30000)
    _wux(out, 0x20000)
    task = {"system": "wiiu", "mode": "wux", "sources": [disc], "roots": [disc]}
    assert rb_chd.convert(task) is True and ran == [disc], rb_chd._state["last"]
    assert int.from_bytes(open(out, "rb").read()[0x10:0x18], "little") == 0x30000


def test_an_own_output_other_than_a_chd_is_made_again(isolated, monkeypatch, fake_es):
    """A .wux this worker made, interrupted before its .wud went: only CHDs are resumed, so it used
    to be offered and skipped forever."""
    rb_chd, disc, out, ran = _wiiu(monkeypatch, 0x30000)
    _own(_wux(out, 0x30000), [disc])
    task = {"system": "wiiu", "mode": "wux", "sources": [disc], "roots": [disc]}
    assert rb_chd.convert(task) is True and ran == [disc] and not os.path.exists(disc)


def test_an_output_maybe_cut_short_is_not_replaced(isolated, monkeypatch, fake_es):
    """disc_incomplete's uncertain verdict (a file entry past the end, maybe a dummy) on a file
    already there: never replaced (it may be a good copy, or another game's)."""
    import rb_chd
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    cue, out = _lyra(sysdir)
    _chdman_writing_chds(monkeypatch, rb_chd, b"new")
    for verdict, ok in (((10, 12, False), False), ((10, 12, True), True)):
        monkeypatch.setattr(rommlib, "disc_incomplete", lambda p, v=verdict: v if p == out else False)
        assert rb_chd.convert({"system": "psx", "mode": "createcd", "sources": [cue], "roots": [cue]}) is ok
        assert (open(out, "rb").read() == b"MComprHDnew") is ok


def test_bare_disc_folders_of_different_titles_are_never_one_set(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    _sheet(os.path.join(sysdir, "Mega", "CD1", "Mega Man.cue"), "a.bin")
    _sheet(os.path.join(sysdir, "Mega", "CD2", "Mega Race.cue"), "b.bin")
    _sheet(os.path.join(sysdir, "Orb", "CD1", "Orb (Disc 1).cue"), "a.bin")
    _sheet(os.path.join(sysdir, "Orb", "CD2", "ORB (Disc 2).cue"), "b.bin")
    assert _sets("psx") == [("Mega/CD1", ["Mega Man.cue"], None), ("Mega/CD2", ["Mega Race.cue"], None),
                            ("Orb", ["Orb (Disc 1).cue", "ORB (Disc 2).cue"], None)]


def test_still_named_reads_each_sheet_once_until_it_changes(isolated, monkeypatch):
    import rb_chd
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    for i in range(5):
        _sheet(os.path.join(sysdir, "G%d.cue" % i), "G%d.bin" % i)
    reads = []
    real = rommlib.sheet_sources
    monkeypatch.setattr(rommlib, "sheet_sources", lambda sh, *a, **k: reads.append(sh) or real(sh, *a, **k))
    monkeypatch.setattr(rb_chd, "_SHEETS", {})
    mine = [os.path.join(sysdir, "G0.cue")]
    first = rb_chd.still_named(mine, {sysdir})
    assert len(reads) == 4 and os.path.join(sysdir, "G1.bin") in first
    assert rb_chd.still_named(mine, {sysdir}) == first and len(reads) == 4
    open(os.path.join(sysdir, "G1.bin2"), "wb").close()
    open(os.path.join(sysdir, "G1.cue"), "w").write(CUE % "G1.bin2")       # edited: read again
    named = rb_chd.still_named(mine, {sysdir})
    assert len(reads) == 5 and os.path.join(sysdir, "G1.bin2") in named and os.path.join(sysdir, "G1.bin") not in named


# ------------------------------------------- bare disc folders, sheets not Redump-named

@pytest.mark.parametrize("a,b", [
    ("Lumen Saga CD1.cue", "Lumen Saga CD2.cue"),
    ("Lumen Saga (Disc 1) (Rev 1).cue", "Lumen Saga (Disc 2).cue"),
    ("Lumen Saga - Disc 1.cue", "Lumen Saga - Disc 2.cue"),
])
def test_bare_disc_folders_group_when_the_sheets_are_not_redump_named(isolated, monkeypatch, fake_es, a, b):
    _sheet(os.path.join(rommlib.ROMS_ROOT, "psx", "Lumen Saga", "CD1", a), "t1.bin")
    _sheet(os.path.join(rommlib.ROMS_ROOT, "psx", "Lumen Saga", "CD2", b), "t2.bin")
    sets = _sets("psx")
    assert len(sets) == 1 and sets[0][0] == "Lumen Saga" and len(sets[0][1]) == 2


def test_bare_disc_folders_of_different_games_stay_apart(isolated, monkeypatch, fake_es):
    _sheet(os.path.join(rommlib.ROMS_ROOT, "psx", "Mega", "CD1", "Mega Man.cue"), "t1.bin")
    _sheet(os.path.join(rommlib.ROMS_ROOT, "psx", "Mega", "CD2", "Mega Race.cue"), "t2.bin")
    assert len(_sets("psx")) == 2


def test_a_game_folder_that_cant_be_removed_is_never_renamed_over(box, monkeypatch):
    rb_chd, disc = box["rb_chd"], box["disc"]
    box["have"][0] = True
    out = disc[:-4] + ".wux"
    os.makedirs(out)
    open(os.path.join(out, "keep.bin"), "wb").write(b"x")
    monkeypatch.setattr(rb_chd, "WUD_SIZE", 0x20000)
    monkeypatch.setattr(rb_chd, "_wux_size", lambda p: 1)
    monkeypatch.setattr(rb_chd.shutil, "rmtree", lambda p, ignore_errors=False: None)   # fails to remove anything
    assert rb_chd.convert(box["task"]) is False
    assert os.path.isdir(out) and os.path.exists(disc)
    assert not [n for n in os.listdir(os.path.dirname(disc)) if n.endswith(".part") or ".part" in n]
    assert "FAIL" in open(rommlib.LOG_PATH).read()


# ------------------------------------------- a game folder's other files (black-box, 2026-11-02)

def _renames():
    out = {}
    try:
        lines = open(rommlib.GAMELIST_QUEUE).readlines()
    except OSError:
        return out
    for ln in lines:
        out.update(json.loads(ln).get("rename") or {})
    return out


def _folder_game(sysdir, extra, data=b"x" * 100):
    """psx/Orin (USA)/Orin (USA).cue + .bin, plus extra (a path under the folder)."""
    d = os.path.join(sysdir, "Orin (USA)")
    cue = _sheet(os.path.join(d, "Orin (USA).cue"), "Orin (USA).bin")
    p = os.path.join(d, extra)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "wb").write(data)
    return d, cue


@pytest.mark.parametrize("extra", [
    "manual.pdf", "cover.jpg", "readme.txt", "notes.txt", "info.nfo", "Orin - Manual.pdf", "boxart.png",
    "folder.jpg", "My Hacks Guide.pdf", "big_manual.pdf", "Orin (USA).txt", "Orin (USA).srm", "game.sfv",
    "Orin.md5", "list.sha1", "Orin.xml", "settings.cfg", "Orin.url", "unknown.zzz", "Extras/poster.png",
])
def test_a_game_folder_s_own_files_are_never_deleted_with_it(isolated, monkeypatch, fake_es, extra):
    """Orin (USA)/ becomes Orin (USA).chd beside it: a manual, a cover, notes, a save or anything
    else in the folder stays, in the folder (it used to be deleted with it as "junk")."""
    import rb_chd
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    data = b"x" * (5 << 20 if extra.startswith("big") else 100)
    d, cue = _folder_game(sysdir, extra, data)
    _quiet(monkeypatch, rb_chd)
    assert rb_chd.convert({"system": "psx", "mode": "createcd", "sources": [cue], "roots": [d]}) is True
    assert open(os.path.join(d, extra), "rb").read() == data
    assert sorted(os.listdir(sysdir)) == ["Orin (USA)", "Orin (USA).chd"]
    assert not os.path.exists(cue) and not os.path.exists(os.path.join(d, "Orin (USA).bin"))
    ren = _renames()                     # the game's entry follows its CHD; the folder isn't renamed
    assert ren[cue] == os.path.join(sysdir, "Orin (USA).chd") and d not in ren
    assert "kept folder" in open(rommlib.LOG_PATH).read()


@pytest.mark.parametrize("extra", ["Thumbs.db", ".DS_Store", "desktop.ini", "._Orin (USA).cue", ".directory",
                                   "Disc/Thumbs.db"])
def test_a_game_folder_holding_only_os_junk_goes(isolated, monkeypatch, fake_es, extra):
    import rb_chd
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    d, cue = _folder_game(sysdir, extra)
    _quiet(monkeypatch, rb_chd)
    assert rb_chd.convert({"system": "psx", "mode": "createcd", "sources": [cue], "roots": [d]}) is True
    assert sorted(os.listdir(sysdir)) == ["Orin (USA).chd"]
    assert _renames()[d] == os.path.join(sysdir, "Orin (USA).chd")


@pytest.mark.parametrize("name,text,goes", [
    ("Orin (USA).sfv", "; made by a tool\nOrin (USA).bin 0123ABCD\n", True),
    ("Orin (USA).md5", "0123456789abcdef0123456789abcdef *Orin (USA).bin\n", True),
    ("Orin (USA).sha1", "SHA1 (Orin (USA).bin) = " + "a" * 40 + "\n", True),
    ("Orin (USA).sfv", "Orin (USA).bin 0123ABCD\nOrin - Manual.pdf 89ABCDEF\n", False),   # lists another file
    ("Orin (USA).md5", "my own notes about this game\n", False),                         # not a checksum list
])
def test_a_checksum_list_goes_only_when_it_lists_just_the_converted_tracks(isolated, monkeypatch, fake_es,
                                                                         name, text, goes):
    import rb_chd
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    d, cue = _folder_game(sysdir, name, text.encode())
    _quiet(monkeypatch, rb_chd)
    assert rb_chd.convert({"system": "psx", "mode": "createcd", "sources": [cue], "roots": [d]}) is True
    assert os.path.isdir(d) is not goes
    if not goes:
        assert open(os.path.join(d, name)).read() == text


@pytest.mark.parametrize("manual", [False, True])
def test_a_playlist_inside_a_kept_game_folder_never_lists_the_game_twice(isolated, monkeypatch, fake_es, manual):
    """Pell/ with two discs and Pell.m3u inside: the CHDs and a new Pell.m3u land beside the folder.
    The folder's own playlist (rewritten to the CHDs) would be a second entry for the game in ES:
    it goes, also when a manual keeps the folder."""
    import rb_chd
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    d = os.path.join(sysdir, "Pell")
    cues = [_sheet(os.path.join(d, "Pell (Disc %d).cue" % n), "Pell (Disc %d).bin" % n) for n in (1, 2)]
    inner = os.path.join(d, "Pell.m3u")
    open(inner, "w").write("Pell (Disc 1).cue\nPell (Disc 2).cue\n")
    if manual:
        open(os.path.join(d, "manual.pdf"), "w").write("pdf")
    _quiet(monkeypatch, rb_chd)
    assert rb_chd.convert({"system": "psx", "mode": "createcd", "sources": cues, "roots": [d]}) is True
    assert sorted(os.listdir(sysdir)) == (["Pell"] if manual else []) + [
        "Pell (Disc 1).chd", "Pell (Disc 2).chd", "Pell.m3u"]
    assert not os.path.exists(inner)
    if manual:
        assert os.listdir(d) == ["manual.pdf"]
    assert _renames()[inner] == os.path.join(sysdir, "Pell.m3u")


# ------------------------------------------- cut-short images (header says more, no directory)

def _pvd_only(nsectors):
    """Sector 16 of a disc whose header says nsectors and whose root directory record is empty."""
    s = bytearray(2048)
    s[0], s[1:6], s[6] = 1, b"CD001", 1
    s[80:84] = nsectors.to_bytes(4, "little")
    return bytes(s)


def _empty_dir_bin(path, have, header):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        for lba in range(have):
            sec = bytearray(2352)
            sec[0], sec[1:11], sec[11], sec[15] = 0, b"\xff" * 10, 0, 2
            if lba == 16:
                sec[24:24 + 2048] = _pvd_only(header)
            f.write(sec)


def _empty_dir_iso(path, have, header):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.seek(16 * 2048)
        f.write(_pvd_only(header))
        f.truncate(have * 2048)


def test_a_cut_short_image_whose_directory_lists_nothing_is_certainly_cut_short(isolated):
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    _empty_dir_bin(os.path.join(sysdir, "Zell (USA).bin"), 300, 600)
    cue = os.path.join(sysdir, "Zell (USA).cue")
    open(cue, "w").write(CUE % "Zell (USA).bin")
    assert rommlib.disc_incomplete(cue) == (300, 600, True)
    iso = os.path.join(rommlib.ROMS_ROOT, "ps2", "Cut (USA).iso")
    _empty_dir_iso(iso, 2000, 4000)
    assert rommlib.disc_incomplete(iso) == (2000, 4000, True)
    whole = os.path.join(rommlib.ROMS_ROOT, "ps2", "Whole (USA).iso")
    _empty_dir_iso(whole, 3000, 3000)
    assert rommlib.disc_incomplete(whole) is None
    _empty_dir_iso(whole, 3000, 3600)                     # 20% over: within what whole discs show
    assert rommlib.disc_incomplete(whole) is None


def test_a_trimmed_image_whose_files_all_fit_is_still_not_cut_short(isolated):
    """The header says 600 sectors, the files end at 200 and the image holds 300 (a trimmed
    copy): unchanged by the empty-directory rule."""
    from test_disk import _iso
    iso = os.path.join(rommlib.ROMS_ROOT, "ps2", "Trim (USA).iso")
    os.makedirs(os.path.dirname(iso))
    _iso(iso, 300, 200, header=600)
    assert rommlib.disc_incomplete(iso) is None


def test_check_games_reports_the_empty_directory_cut_short_image(isolated, mock_url):
    from test_multidisc import _setup
    api, _ = _setup(mock_url)
    plats = {p["fs_slug"]: p["id"] for p in api.platforms()}
    d = os.path.join(rommlib.ROMS_ROOT, "psx", "Zell (USA)")
    _empty_dir_bin(os.path.join(d, "Zell (USA).bin"), 300, 600)
    open(os.path.join(d, "Zell (USA).cue"), "w").write(CUE % "Zell (USA).bin")
    findings, _ = rommlib.audit(api, {"psx": [plats["ps1"]]})
    got = [(x["severity"], x["problem"]) for x in findings if "Zell" in x["path"]]
    assert ("damaged", "Incomplete disc image: 50% missing (300 of 600 sectors)") in got


def test_the_planner_lists_a_cut_short_image_and_the_worker_never_converts_it(isolated, monkeypatch, fake_es):
    import rb_chd
    import rb_chd_plan
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    _empty_dir_bin(os.path.join(sysdir, "Zell (USA).bin"), 300, 600)
    cue = os.path.join(sysdir, "Zell (USA).cue")
    open(cue, "w").write(CUE % "Zell (USA).bin")
    iso = os.path.join(rommlib.ROMS_ROOT, "ps2", "Cut (USA).iso")
    _empty_dir_iso(iso, 2000, 4000)
    _old(cue, os.path.join(sysdir, "Zell (USA).bin"), iso)
    skipped = []
    assert rb_chd_plan.candidates(["psx", "ps2"], skipped=skipped) == []
    assert sorted(skipped) == [
        ("ps2", iso, "cut short: 3.9 MB of 7.8 MB; Check games can download it again"),
        ("psx", cue, "cut short: 689 KB of 1.3 MB; Check games can download it again")]
    monkeypatch.setattr(rb_chd, "running_game", lambda: "")
    monkeypatch.setattr(rommlib, "es_busy", lambda app_busy=False, get=None: "")
    monkeypatch.setattr(rb_chd, "chdman", lambda *a: pytest.fail("must not convert a cut-short image"))
    for src, system in ((cue, "psx"), (iso, "ps2")):
        assert rb_chd.convert({"system": system, "mode": "createcd" if system == "psx" else "createdvd",
                               "sources": [src], "roots": [src]}) is False
        assert os.path.exists(src)


# ------------------------------------------- sheets chdman can't read as they are

BOM_CUE = '﻿FILE "Rho (USA).bin" BINARY\r\n  TRACK 01 MODE2/2352\r\n    INDEX 01 00:00:00\r\n'


@pytest.mark.parametrize("text,track,want", [
    (BOM_CUE, "Rho (USA).bin", 'FILE "Rho (USA).bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n'),
    (BOM_CUE.replace("\r\n", "\r"), "Rho (USA).bin",           # old Mac line ends (chdman never finished)
     'FILE "Rho (USA).bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n'),
    (CUE % "rho (usa).bin", "RHO (USA).BIN", CUE % "RHO (USA).BIN"),
    (CUE % "Tracks\\Rho (USA).bin", "Tracks/Rho (USA).bin", CUE % "Tracks/Rho (USA).bin"),
    ('FILE rho.bin BINARY\n  TRACK 01 MODE2/2352\n', "RHO.BIN", 'FILE "RHO.BIN" BINARY\n  TRACK 01 MODE2/2352\n'),
])
def test_chdman_gets_a_cleaned_copy_of_a_sheet_it_cant_read(isolated, monkeypatch, fake_es, text, track, want):
    """A byte-order mark, old Mac line ends, a name in another letter case or with backslashes:
    chdman can't find the track ("couldn't find bin file []"). It gets a cleaned copy beside the
    sheet (removed after); the owner's sheet and track are what's deleted once it's done."""
    import rb_chd
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    cue = os.path.join(sysdir, "Rho (USA).cue")
    os.makedirs(os.path.dirname(os.path.join(sysdir, track)), exist_ok=True)
    open(os.path.join(sysdir, track), "wb").write(b"\0" * 2352)
    open(cue, "w", encoding="utf-8", newline="").write(text)
    _quiet(monkeypatch, rb_chd)
    seen = []

    def chdman(mode, *a):
        if mode != "verify":
            i = a[a.index("-i") + 1]
            seen.append((os.path.dirname(i), os.path.basename(i), open(i, encoding="utf-8", newline="").read()))
            open(a[a.index("-o") + 1], "w").write("chd")
        return 0, ""
    monkeypatch.setattr(rb_chd, "chdman", chdman)
    assert rb_chd.convert({"system": "psx", "mode": "createcd", "sources": [cue], "roots": [cue]}) is True
    assert seen == [(sysdir, ".rb-chdman-Rho (USA).cue", want)]
    assert not os.path.exists(cue) and not os.path.exists(os.path.join(sysdir, track))
    assert not [n for dp, _, fs in os.walk(sysdir) for n in fs if n.startswith(".rb-chdman-")]


def test_a_sheet_chdman_reads_as_it_is_is_handed_over_unchanged(isolated):
    import rb_chd
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    cue = _sheet(os.path.join(sysdir, "Rho.cue"), "Rho.bin")
    open(cue, "w", newline="").write((CUE % "Rho.bin").replace("\n", "\r\n"))      # CRLF alone: chdman's fine
    stale = os.path.join(sysdir, ".rb-chdman-Rho.cue")
    open(stale, "w").write(CUE % "Rho.bin")                    # a killed run's copy
    assert rb_chd._chdman_sheet(cue) is None and not os.path.exists(stale)
    assert rb_chd._chdman_sheet(os.path.join(sysdir, "Rho.bin")) is None


def test_a_killed_run_s_copy_never_keeps_a_track_from_going(isolated):
    import rb_chd
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    cue = _sheet(os.path.join(sysdir, "Rho.cue"), "Rho.bin")
    open(os.path.join(sysdir, ".rb-chdman-Rho.cue"), "w").write(CUE % "Rho.bin")
    assert rb_chd.still_named([cue], {sysdir}) == set()


def test_a_sheet_chdman_refused_is_listed_with_its_words_until_it_changes(isolated, monkeypatch, fake_es):
    """chdman failed on it: it used to stay offered (and fail) forever. Now Compress lists it as
    can't convert with chdman's message; once the sheet or a track changes it's offered again."""
    import rb_chd
    import rb_chd_plan
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    cue = _sheet(os.path.join(sysdir, "Sigma.cue"), "Sigma.bin")
    _old(cue, os.path.join(sysdir, "Sigma.bin"))
    _quiet(monkeypatch, rb_chd)
    monkeypatch.setattr(rb_chd, "chdman", lambda *a: (1, "ERROR: couldn't find bin file []\nFatal error occurred: 1"))
    task = {"system": "psx", "mode": "createcd", "sources": [cue], "roots": [cue]}
    rb_chd._state["last"] = ""
    assert rb_chd.convert(task) is False
    rb_chd.record("1.json", task, "failed")
    skipped = []
    assert rb_chd_plan.candidates(["psx"], skipped=skipped) == []
    assert skipped == [("psx", cue, "converting it failed: ERROR: couldn't find bin file []")]
    t = time.time() + 5
    os.utime(cue, (t, t))                                   # edited (and settled) since
    monkeypatch.setattr(rb_chd_plan, "STILL_WRITTEN", 0)
    assert [c["files"] for c in rb_chd_plan.candidates(["psx"])] == [[cue]]
    _old(cue)
    rb_chd.record("2.json", task, "ok")                     # a later result replaces the failure
    assert [c["files"] for c in rb_chd_plan.candidates(["psx"])] == [[cue]]
