"""Free up space skips launchers and names the files that go; Clean up leftovers counts what it removed."""
import os
import types

import rommlib
from rbui.screens.space import FreeUpScreen, freeable_games


def _w(p, data=b""):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    return p


def _games(*specs):
    return [{"system": s, "path": p, "name": os.path.basename(p), "lastplayed": None, "playcount": 0}
            for s, p in specs]


def test_free_up_space_skips_launcher_systems(isolated, monkeypatch):
    app_file = _w(os.path.join(rommlib.ROMS_ROOT, "pygame", "rommbrowser", "rommbrowser.pygame"), b"xx")
    game = _w(os.path.join(rommlib.ROMS_ROOT, "psx", "Foo.chd"), b"g" * 10)
    monkeypatch.setattr(rommlib, "es_game_list", lambda systems=None: _games(("pygame", app_file), ("psx", game)))
    assert [g["path"] for g in freeable_games(rommlib.Library())] == [game]


def test_free_up_confirm_names_the_files_of_games_put_here_another_way(isolated):
    d = os.path.join(rommlib.ROMS_ROOT, "psx")
    cue = _w(os.path.join(d, "Foo.cue"), b'FILE "Foo.bin" BINARY\n')
    _w(os.path.join(d, "Foo.bin"), b"b" * 10)
    chd = _w(os.path.join(d, "Bar.chd"), b"c" * 10)
    app = types.SimpleNamespace(library=rommlib.Library(), modal=None, toast=lambda *a, **k: None,
                                bg=lambda *a, **k: None)
    scr = FreeUpScreen.__new__(FreeUpScreen)
    scr.app = app
    g = _games(("psx", cue), ("psx", chd))
    for x in g:
        x.update(ours=False, size=10)
    scr.picked = lambda: g
    scr.delete_marked()
    body = app.modal.body
    assert "Not downloaded by RomM-o-Cera (2)" in body
    for n in ("Foo.cue", "Foo.bin", "Bar.chd"):
        assert n in body
    assert rommlib.ROMS_ROOT not in body


def test_delete_leftovers_counts_only_what_it_removed(isolated):
    d = os.path.join(rommlib.ROMS_ROOT, "psx")
    a = _w(os.path.join(d, "a.part"), b"a" * 100)
    b = _w(os.path.join(d, "b.part"), b"b" * 50)
    items = [{"path": a, "kind": "Partial download", "size": 100},
             {"path": b, "kind": "Partial download", "size": 50},
             {"path": os.path.join(d, "gone.part"), "kind": "Partial download", "size": 999}]
    res = rommlib.delete_leftovers(items)
    assert int(res) == 150 + 999 and res.removed == 3
    assert res.kept == 0 and not os.path.exists(a) and not os.path.exists(b)


def test_delete_leftovers_says_how_many_were_kept(isolated):
    d = os.path.join(rommlib.ROMS_ROOT, "psx")
    keep = _w(os.path.join(d, "Copy.cue"), b"x" * 30)
    gone = _w(os.path.join(d, "x.part"), b"x" * 70)
    items = [{"path": keep, "kind": "Copy of a converted game", "size": 30, "converted": os.path.join(d, "nope.chd")},
             {"path": gone, "kind": "Partial download", "size": 70}]
    res = rommlib.delete_leftovers(items)
    assert (int(res), res.removed, res.kept) == (70, 1, 1) and os.path.exists(keep) and not os.path.exists(gone)
