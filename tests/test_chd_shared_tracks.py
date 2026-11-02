"""rb_chd after a conversion: what it deletes and where each disc's game entry
goes (code review, 2026-11-02)."""
import os

import rommlib


def _quiet(monkeypatch, rb_chd):
    monkeypatch.setattr(rb_chd, "running_game", lambda: "")
    monkeypatch.setattr(rommlib, "es_busy", lambda app_busy=False, get=None: "")
    monkeypatch.setattr(rommlib, "disc_incomplete", lambda p: False)
    monkeypatch.setattr(rb_chd, "chdman", lambda mode, *a: (
        mode == "verify" or open(a[a.index("-o") + 1], "w").write("chd")) and (0, ""))


def test_a_track_another_cue_still_uses_is_kept_until_the_last_one_goes(isolated, monkeypatch, fake_es):
    """Nova.cue and Nova (Alt).cue both name Nova.bin: compressing one keeps the bin
    for the other; compressing the other then removes it."""
    import rb_chd
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    os.makedirs(sysdir)
    cue, alt = os.path.join(sysdir, "Nova.cue"), os.path.join(sysdir, "Nova (Alt).cue")
    bin_ = os.path.join(sysdir, "Nova.bin")
    for c in (cue, alt):
        open(c, "w").write('FILE "Nova.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
    open(bin_, "wb").write(b"\0" * 2352)
    _quiet(monkeypatch, rb_chd)
    assert rb_chd.convert({"system": "psx", "mode": "createcd", "sources": [cue], "roots": [cue]}) is True
    assert os.path.exists(os.path.join(sysdir, "Nova.chd"))
    assert not os.path.exists(cue)
    assert os.path.exists(bin_) and os.path.exists(alt)          # the other sheet still works
    assert rb_chd.convert({"system": "psx", "mode": "createcd", "sources": [alt], "roots": [alt]}) is True
    assert os.path.exists(os.path.join(sysdir, "Nova (Alt).chd"))
    assert not os.path.exists(bin_) and not os.path.exists(alt)   # nothing names it now


def test_a_track_a_playlist_names_directly_is_kept(isolated, monkeypatch, fake_es):
    import rb_chd
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    os.makedirs(sysdir)
    cue, bin_ = os.path.join(sysdir, "Orbit.cue"), os.path.join(sysdir, "Orbit.bin")
    open(cue, "w").write('FILE "Orbit.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
    open(bin_, "wb").write(b"\0" * 2352)
    open(os.path.join(sysdir, "Orbit Raw.m3u"), "w").write("# raw image\nOrbit.bin\n")
    _quiet(monkeypatch, rb_chd)
    assert rb_chd.convert({"system": "psx", "mode": "createcd", "sources": [cue], "roots": [cue]}) is True
    assert os.path.exists(bin_) and not os.path.exists(cue)


def test_m3u_name_taken_each_disc_entry_follows_its_own_chd(isolated, monkeypatch, fake_es):
    """The playlist name belongs to another copy of the game: the discs stay listed one
    by one, so each disc's entry must become its own CHD, not all disc 1's."""
    import rb_chd
    sysdir = os.path.join(rommlib.ROMS_ROOT, "psx")
    os.makedirs(sysdir)
    fs = [os.path.join(sysdir, "Pulsar (Disc %d).cue" % n) for n in (1, 2)]
    for f in fs:
        open(f, "w").write("x")
    taken = os.path.join(sysdir, "Pulsar.m3u")
    open(taken, "w").write("elsewhere/Pulsar (Disc 1).cue\n")
    _quiet(monkeypatch, rb_chd)
    seen = []
    monkeypatch.setattr(rommlib, "append_gamelist", lambda change: seen.append(change))
    assert rb_chd.convert({"system": "psx", "mode": "createcd", "sources": fs,
                           "roots": fs, "name": "Pulsar"}) is True
    assert open(taken).read() == "elsewhere/Pulsar (Disc 1).cue\n"
    (change,) = seen
    assert change["hide"] == []
    assert change["rename"] == {f: os.path.splitext(f)[0] + ".chd" for f in fs}
