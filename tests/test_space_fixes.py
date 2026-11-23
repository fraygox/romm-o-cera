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


def test_free_up_space_reads_the_box_once_for_many_games_not_once_each(isolated, monkeypatch):
    """Picking hundreds of games put here another way froze the screen when the confirm opened:
    each game re-read every file the app downloaded and every sheet in its system (#13)."""
    from romm import library as _library
    d = os.path.join(rommlib.ROMS_ROOT, "fbneo")
    games = _games(*[("fbneo", _w(os.path.join(d, "a%03d.zip" % i), b"x")) for i in range(40)])
    lib = rommlib.Library()
    calls = {"owned": 0, "sheets": 0}
    real_owned, real_refs = lib.owned, _library._sheet_refs

    def owned():
        calls["owned"] += 1
        return real_owned()

    def refs(sysdir):
        calls["sheets"] += 1
        return real_refs(sysdir)
    monkeypatch.setattr(lib, "owned", owned)
    monkeypatch.setattr(_library, "_sheet_refs", refs)
    monkeypatch.setattr(rommlib, "es_game_list", lambda systems=None: [dict(g) for g in games])
    listed = freeable_games(lib)
    assert len(listed) == 40 and calls == {"owned": 1, "sheets": 1}

    calls.update(owned=0, sheets=0)
    app = types.SimpleNamespace(library=lib, modal=None, toast=lambda *a, **k: None, bg=lambda *a, **k: None)
    scr = FreeUpScreen.__new__(FreeUpScreen)
    scr.app = app
    for g in listed:
        g["ours"] = False
    scr.picked = lambda: listed
    scr.delete_marked()
    assert "Not downloaded by RomM-o-Cera (40)" in app.modal.body
    assert calls == {"owned": 1, "sheets": 1}


def test_a_macs_file_changed_while_the_question_is_open_stays(isolated, monkeypatch):
    d = os.path.join(rommlib.ROMS_ROOT, "fbneo")
    game = _w(os.path.join(d, "Game.zip"), b"zip")
    mac = _w(os.path.join(d, "._Game.zip"), b"\x00\x05\x16\x07" + b"\x00" * 60)
    ran = []
    app = types.SimpleNamespace(library=rommlib.Library(), modal=None, toast=lambda *a, **k: None,
                                bg=lambda run, done, err: ran.append(run()), work=None, space_freed=lambda: None)
    scr = FreeUpScreen.__new__(FreeUpScreen)
    scr.app, scr.busy = app, False
    g = _games(("fbneo", game))
    g[0].update(ours=False, size=3)
    scr.picked = lambda: g
    scr.delete_marked()
    assert "._Game.zip" in app.modal.body
    with open(mac, "ab") as f:                                 # changed while the question was up
        f.write(b"more")
    app.modal.on_ok()
    assert ran and not os.path.exists(game) and os.path.exists(mac)


def test_a_folder_with_no_games_es_lists_says_why_not_just_no_games_found(isolated, monkeypatch):
    """Space › mame_backup (not a Batocera system) opened on "No games found." and nothing else (#13)."""
    import pygame
    seen = []
    import rbui.screens.space as space
    monkeypatch.setattr(space, "scroll_text", lambda c, f, s, *a, **k: seen.append(s))
    monkeypatch.setattr(space, "text", lambda *a, **k: None)
    scr = FreeUpScreen.__new__(FreeUpScreen)
    scr.app, scr.systems, scr.items, scr.busy, scr.load_error = types.SimpleNamespace(), ["mame_backup"], [], False, None
    scr.f = types.SimpleNamespace(h3=None, body=None)
    scr.draw(pygame.Surface((10, 10)))
    assert seen and "roms/mame_backup" in seen[0] and "\\\\BATOCERA\\share\\roms\\mame_backup" in seen[0]


def test_a_folders_games_with_es_not_answering_is_an_error_not_no_games(isolated, monkeypatch):
    """The empty state says ES lists no games there: only when ES answered (else the load error)."""
    from romm import es as _es

    def down(path, timeout=10):
        raise OSError("connection refused")
    monkeypatch.setattr(_es, "_es_json", down)
    try:
        rommlib.es_game_list(["nes"])
        assert False, "should raise"
    except rommlib.RommError:
        pass
    monkeypatch.setattr(_es, "_es_json", lambda path, timeout=10: [] if path == "/systems" else down(path))
    assert rommlib.es_game_list(["mame_backup"]) == []          # ES answers, just not for this folder


def test_big_counts_read_like_the_batch_line_on_top(isolated):
    """The top bar said "Deleting 118 of 2,259 games" while the question said "Delete 2259 games?"."""
    from rbui.screens.box_data import plural, work_label
    assert plural(2259, "game") == "2,259 games" and plural(1, "game") == "1 game"
    assert work_label({"verb": "Deleting", "n": 118, "total": 2259, "what": "game"}) == "Deleting 118 of 2,259 games"
    scr = FreeUpScreen.__new__(FreeUpScreen)
    games = [{"size": 1}] * 2259
    assert scr.pick_entries(games)[0][0] == "Delete 2,259 picked"
