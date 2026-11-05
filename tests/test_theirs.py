"""Games put on the box another way (copied over by hand): the app deletes, frees and replaces
them only when asked, and its confirm names exactly the files that go: the game file and the
tracks its sheet names (in a folder, the files named like it), a playlist alone. Notes, saves,
patches, cheats and other games beside or in the folder stay."""
import os

import rommlib
from test_downloader_round2 import app  # noqa: F401  (the headless app fixture)


def _w(p, data=b"x"):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    return p


def _sys():
    return os.path.join(rommlib.ROMS_ROOT, "psx")


GAME = "Lamp Run (USA)"
ROM = {"id": 5, "name": "Lamp Run", "fs_name": GAME + ".zip", "fs_name_no_ext": GAME}


def test_their_files_are_the_game_and_its_tracks_only(isolated):
    d = os.path.join(_sys(), GAME)
    cue = _w(os.path.join(d, GAME + ".cue"), ('FILE "%s (Track 1).bin" BINARY\nFILE "%s (Track 2).bin" BINARY\n'
                                             % (GAME, GAME)).encode())
    t1, t2 = _w(os.path.join(d, GAME + " (Track 1).bin")), _w(os.path.join(d, GAME + " (Track 2).bin"))
    keep = [_w(os.path.join(d, n)) for n in ("notes.txt", GAME + ".ips", GAME + ".cht", "Other Game.chd",
                                              GAME + ".srm")]
    got = rommlib.Library().their_files(ROM, "psx")
    assert sorted(got) == sorted([cue, t1, t2])
    assert not set(keep) & set(got)


def test_a_playlist_of_theirs_goes_alone(isolated):
    other = _w(os.path.join(_sys(), "Other (USA).chd"))
    m3u = _w(os.path.join(_sys(), "My Mix.m3u"), b"Other (USA).chd\n")
    assert rommlib.their_files([m3u], "psx") == [m3u]
    assert os.path.exists(other)


def test_delete_theirs_takes_exactly_the_listed_files(isolated):
    d = os.path.join(_sys(), GAME)
    cue = _w(os.path.join(d, GAME + ".cue"), ('FILE "%s.bin" BINARY\n' % GAME).encode())
    b = _w(os.path.join(d, GAME + ".bin"), b"b" * 100)
    notes = _w(os.path.join(d, "notes.txt"), b"mine")
    lib = rommlib.Library()
    files = lib.their_files(ROM, "psx")
    gone, freed = lib.delete_theirs(ROM, "psx", files)
    assert sorted(gone) == sorted([cue, b]) and freed > 0
    assert os.listdir(d) == ["notes.txt"] and open(notes, "rb").read() == b"mine"


def test_delete_theirs_never_takes_one_of_the_apps_files(isolated):
    """An app download sharing a name with a sheet of theirs: listed or not, it stays."""
    ours = _w(os.path.join(_sys(), GAME + ".bin"), b"app")
    lib = rommlib.Library()
    lib.mark({"id": 9, "name": "Other", "fs_name": "Other.zip"}, "psx", [ours])
    cue = _w(os.path.join(_sys(), GAME + ".cue"), ('FILE "%s.bin" BINARY\n' % GAME).encode())
    assert lib.their_files(ROM, "psx") == [cue]
    lib.delete_theirs(ROM, "psx", [cue, ours])
    assert os.path.exists(ours) and not os.path.exists(cue)


def test_the_game_menu_offers_delete_and_download_again_naming_the_files(app, rb, monkeypatch):
    rom = {"id": 5, "name": "Lamp Run", "fs_name": GAME + ".chd", "fs_name_no_ext": GAME, "platform_id": 7}
    chd = _w(os.path.join(_sys(), GAME + ".chd"), b"c" * 10)
    app.platform_by_id = {7: {"_system": "psx"}}
    monkeypatch.setattr(app, "system_for", lambda r: "psx")
    monkeypatch.setattr(app.library, "group_for", lambda rid: None)
    menu = rb.ManageMenu(app, rom)
    labels = {e[0]: e for e in menu.entries if e}
    assert labels["Delete from this box"][2] is not None and "Not downloaded by RomM-o-Cera" in labels[
        "Delete from this box"][1]
    labels["Delete from this box"][2]()
    assert "Lamp Run (USA).chd" in app.modal.body and "Not downloaded by RomM-o-Cera" in app.modal.body
    labels["Download again"][2]()
    assert app.modal._replace == [chd] and "these go: Lamp Run (USA).chd" in app.modal.body


def test_a_translation_or_revision_in_the_folder_is_never_one_of_the_games_files(isolated):
    """Round 10: Pyramid Plunder's folder with a [T-En] translation cue/bin and a (Rev 1): Delete
    took them as the game's (they start with its name)."""
    d = os.path.join(_sys(), GAME)
    cue = _w(os.path.join(d, GAME + ".cue"), b'FILE "Track 01.bin" BINARY\n')
    t1 = _w(os.path.join(d, "Track 01.bin"))
    disc2 = _w(os.path.join(d, GAME + " (Disc 2).cue"), b'FILE "Track 02.bin" BINARY\n')
    t2 = _w(os.path.join(d, "Track 02.bin"))
    keep = [_w(os.path.join(d, GAME + " [T-En].cue"), b'FILE "Track 01 (T-En).bin" BINARY\n'),
            _w(os.path.join(d, "Track 01 (T-En).bin")), _w(os.path.join(d, GAME + " (Rev 1).cue")),
            _w(os.path.join(d, GAME + " (Hack).bin")), _w(os.path.join(d, "Track 99.bin"))]
    got = rommlib.Library().their_files(ROM, "psx")
    assert sorted(got) == sorted([cue, t1, disc2, t2])
    assert not set(keep) & set(got)


def test_a_file_swapped_after_the_confirm_is_never_replaced(isolated):
    """Round 10: Download again confirmed, queued for later; the owner put their own file in its
    place meanwhile; when the download ran it deleted it. What was confirmed is pinned."""
    import threading
    p = _w(os.path.join(_sys(), GAME + ".chd"), b"theirs, confirmed")
    d = rommlib.Downloader.__new__(rommlib.Downloader)
    d.api, d.cfg, d.es, d.unopenable = None, dict(rommlib.DEFAULT_CONFIG, chd=False), {}, {}
    d.library, d.persist, d.qlock, d.cv = rommlib.Library(), False, threading.Lock(), threading.Condition()
    d.hold, d.rate_limit, d.unpacking_bytes, d.jobs, d._closed = False, 0, 0, [], False
    j = d.enqueue(dict(ROM), "psx", replace=[p], save=False)
    assert d._ours(j, p)
    os.remove(p)
    _w(p, b"a different file put there later")
    assert not d._ours(j, p)
