"""Found on a Batocera VM (2026-11-03): a delete left the game's art, video and manual
behind and said nothing of the owner's playlist naming the game; a delete took a game
folder's owner files with it; Quick check passed a file that only had a .chd name; the
games a check couldn't match weren't named; the Platforms cards counted what ES lists, not
what the lists tick; a nightly showed its base release's date; dialog titles slid out of
view; Settings told a keyboard to use Hotkey + Start."""
import os

import pytest

import rommlib
from conftest import whole_chd
from test_audit_fixes import Api, _put, _rec, _run, no_es  # noqa: F401
from test_text_fits import _spy, ui  # noqa: F401


def _file(system, rel, data=b"x"):
    p = os.path.join(rommlib.ROMS_ROOT, system, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    return p


def _rom(rid, fs_name):
    return {"id": rid, "name": os.path.splitext(fs_name)[0], "fs_name": fs_name,
            "fs_name_no_ext": os.path.splitext(fs_name)[0]}


# ------------------------------------------------------------ the media the app had ES make

def test_deleting_a_downloaded_game_takes_the_media_made_for_it_and_nothing_else(isolated):
    lib = rommlib.Library()
    game = _file("pcengine", "Lorem Engine (USA).chd", b"g" * 100)
    made = [_file("pcengine", "images/Lorem Engine (USA)-%s.png" % k, b"i" * 10)
            for k in ("image", "thumb", "marquee", "titleshot", "box", "boxback", "fanart", "cartridge")]
    made += [_file("pcengine", "videos/Lorem Engine (USA)-video.mp4", b"v" * 1000),
             _file("pcengine", "manuals/Lorem Engine (USA)-manual.pdf", b"m" * 50)]
    mine = _file("pcengine", "images/Lorem Engine (USA) my scan.png")        # the owner's own name: kept
    shared = _file("pcengine", "images/Ipsum Run (USA)-image.png")
    _file("pcengine", "Ipsum Run (USA).chd")
    lib.mark(_rom(1, "Lorem Engine (USA).chd"), "pcengine", [game])
    assert sorted(lib.made_media(_rom(1, "Lorem Engine (USA).chd"), "pcengine")) == sorted(made)
    gone, freed = lib.uninstall(_rom(1, "Lorem Engine (USA).chd"), "pcengine")
    assert all(os.path.exists(p) for p in made) and not os.path.exists(game)   # art stays: never deleted
    assert freed == 100
    assert os.path.exists(mine) and os.path.exists(shared)


def test_a_folder_kept_for_the_owners_notes_doesnt_keep_the_games_media(isolated):
    """The CHD beside the game's old folder, kept for notes.txt: the folder has the game's name
    but no game in it, so deleting the CHD takes its media (they stayed forever); the notes stay."""
    lib = rommlib.Library()
    game = _file("megacd", "Lorem Engine (USA).chd", b"g" * 100)
    notes = _file("megacd", "Lorem Engine (USA)/notes.txt", b"n")
    made = [_file("megacd", "images/Lorem Engine (USA)-image.png", b"i"),
            _file("megacd", "videos/Lorem Engine (USA)-video.mp4", b"v")]
    lib.mark(_rom(1, "Lorem Engine (USA).zip"), "megacd", [game])
    lib.uninstall(_rom(1, "Lorem Engine (USA).zip"), "megacd")
    assert all(os.path.exists(p) for p in made) and not os.path.exists(game) and os.path.exists(notes)
    # a folder that still holds a game of that name keeps them
    game = _file("megacd", "Lorem Engine (USA).chd", b"g" * 100)
    _file("megacd", "Lorem Engine (USA)/Lorem Engine (USA).cue", b"FILE x BINARY")
    made = [_file("megacd", "images/Lorem Engine (USA)-image.png", b"i")]
    lib.mark(_rom(1, "Lorem Engine (USA).zip"), "megacd", [game])
    lib.uninstall(_rom(1, "Lorem Engine (USA).zip"), "megacd")
    assert os.path.exists(made[0])


def test_media_another_game_still_uses_stays(isolated):
    lib = rommlib.Library()
    game = _file("gba", "Dolor Quest (USA).gba")
    _file("gba", "Dolor Quest (USA).zip")                        # same name, another copy: ES names its media the same
    img = _file("gba", "images/Dolor Quest (USA)-image.png")
    lib.mark(_rom(2, "Dolor Quest (USA).gba"), "gba", [game])
    assert lib.made_media(_rom(2, "Dolor Quest (USA).gba"), "gba") == []
    os.remove(os.path.join(rommlib.ROMS_ROOT, "gba", "Dolor Quest (USA).zip"))
    with open(os.path.join(rommlib.ROMS_ROOT, "gba", "gamelist.xml"), "w") as f:     # another entry names it
        f.write("<gameList><game><path>./Other.gba</path><image>./images/Dolor Quest (USA)-image.png</image>"
                "</game></gameList>")
    assert lib.made_media(_rom(2, "Dolor Quest (USA).gba"), "gba") == []
    lib.uninstall(_rom(2, "Dolor Quest (USA).gba"), "gba")
    assert os.path.exists(img)


def test_a_game_the_app_didnt_download_keeps_its_media(isolated):
    lib = rommlib.Library()
    _file("snes", "Amet Cart (USA).sfc")
    img = _file("snes", "images/Amet Cart (USA)-image.png")
    assert lib.made_media(_rom(3, "Amet Cart (USA).sfc"), "snes") == []      # found by name only: no record
    with pytest.raises(rommlib.NotDownloadedHere):                          # nor is the game itself deleted
        lib.uninstall(_rom(3, "Amet Cart (USA).sfc"), "snes")
    assert os.path.exists(img)


def test_the_owners_playlist_naming_the_game_is_named_and_kept(isolated):
    game = _file("psx", "Sit Amet (USA).chd")
    mine = _file("psx", "Sit Amet (My playlist).m3u", b"Sit Amet (USA).chd\n")
    _file("psx", "Other (My list).m3u", b"Else.chd\n")
    assert rommlib.playlists_naming("psx", [game]) == ["Sit Amet (My playlist).m3u"]
    assert rommlib.playlists_naming("psx", [game, mine]) == []           # its own playlist: part of the game


# ------------------------------------------------------------ a game folder with the owner's files in it

def _folder_game(system="megacd"):
    cue = _file(system, "Consec Tur (USA)/Consec Tur (USA).cue", b'FILE "Consec Tur (USA).bin" BINARY\n')
    bin_ = _file(system, "Consec Tur (USA)/Consec Tur (USA).bin", b"b" * 200)
    notes = _file(system, "Consec Tur (USA)/notes.txt", b"my notes")
    manual = _file(system, "Consec Tur (USA)/Consec Tur manual.pdf", b"pdf")
    return os.path.dirname(cue), [cue, bin_], [notes, manual]


def test_uninstall_leaves_the_owners_files_and_their_folder(isolated):
    lib = rommlib.Library()
    folder, game, mine = _folder_game()
    lib.mark(_rom(4, "Consec Tur (USA).zip"), "megacd", [folder])
    gone, freed = lib.uninstall(_rom(4, "Consec Tur (USA).zip"), "megacd")
    assert not any(os.path.exists(p) for p in game)
    assert all(os.path.exists(p) for p in mine) and freed == len(b'FILE "Consec Tur (USA).bin" BINARY\n') + 200
    assert rommlib.Library().snapshot() == {}


def test_delete_game_leaves_the_owners_files_and_their_folder(isolated):
    folder, game, mine = _folder_game()
    lib = rommlib.Library()
    lib.mark(_rom(5, "Consec Tur (USA).zip"), "megacd", [folder])
    rommlib.delete_game("megacd", folder, lib)
    assert not any(os.path.exists(p) for p in game) and all(os.path.exists(p) for p in mine)


def test_a_folder_holding_only_the_game_still_goes_whole(isolated):
    lib = rommlib.Library()
    folder, game, mine = _folder_game()
    for p in mine:
        os.remove(p)
    lib.mark(_rom(5, "Consec Tur (USA).zip"), "megacd", [folder])
    lib.uninstall(_rom(5, "Consec Tur (USA).zip"), "megacd")
    assert not os.path.exists(folder)


# ------------------------------------------------------------ Quick check reads a CHD's header

def test_chd_header_problem(isolated):
    good = _file("psx", "Good (USA).chd", whole_chd())
    junk = _file("psx", "Junk (USA).chd", os.urandom(4096))
    cut = bytearray(whole_chd())
    cut[40:48] = (1 << 20).to_bytes(8, "big")               # its map past the end: cut short
    short = _file("psx", "Short (USA).chd", bytes(cut))
    v9 = _file("psx", "Odd (USA).chd", b"MComprHD" + (124).to_bytes(4, "big") + (9).to_bytes(4, "big") + bytes(200))
    assert rommlib.chd_header_problem(good) is None
    assert rommlib.chd_header_problem(junk).startswith("Not a CHD")
    assert "cut short" in rommlib.chd_header_problem(short)
    assert rommlib.chd_header_problem(v9).startswith("Damaged CHD")


def test_quick_check_calls_a_file_with_only_a_chd_name_damaged(isolated, no_es):
    _put("psx", "Breaker Ipsum (USA).chd", os.urandom(1 << 16))
    _put("psx", "Fine Disc (USA).chd", whole_chd())
    api = Api([(1, "Breaker Ipsum (USA).cue", [_rec("Breaker Ipsum (USA).cue", 10)]),
               (1, "Fine Disc (USA).cue", [_rec("Fine Disc (USA).cue", 10)])])
    got, _ = _run(api, {"psx": [1]})
    assert list(got) == ["Breaker Ipsum (USA).chd"]
    assert got["Breaker Ipsum (USA).chd"]["severity"] == "damaged" and not got["Breaker Ipsum (USA).chd"].get("deep")


def test_a_check_names_the_games_it_couldnt_match(isolated, no_es):
    _put("gba", "Homebrew A.gba", b"x")
    _put("gba", "Homebrew B.gba", b"x")
    _, stats = _run(Api([]), {"gba": [1]})
    assert stats["unmatched"] == 2 and stats["unmatched_names"] == ["gba/Homebrew A.gba", "gba/Homebrew B.gba"]


# ------------------------------------------------------------ a nightly's date

def test_a_nightly_shows_the_day_it_was_built(monkeypatch):
    monkeypatch.setattr(rommlib, "RELEASE_DATE", "2026-10-17")
    word, day = rommlib.version_date("0.11.2+n20261103.0143.c0a5797")
    assert word == "Built" and (day.tm_year, day.tm_mon, day.tm_mday) == (2026, 11, 3)
    word, day = rommlib.version_date("0.11.2")
    assert word == "Released" and (day.tm_mon, day.tm_mday) == (10, 17)
    assert rommlib.version_date("0.11.2+5433a7c")[0] == "Released"


def test_settings_shows_a_nightlys_build_day_and_the_keyboards_exit(ui, rb, monkeypatch):  # noqa: F811
    monkeypatch.setattr(rommlib, "VERSION_LABEL", "0.11.2+n20261103.0143.c0a5797")
    monkeypatch.setattr(rommlib, "RELEASE_DATE", "2026-10-17")
    ui.push(rb.SettingsScreen(ui))
    calls = _spy(rb, monkeypatch, "text")
    rich = _spy(rb, monkeypatch, "rich_text") if hasattr(rb, "rich_text") else []
    rb.pads.INPUT_MODE["kb"] = True
    ui.draw()
    lines = [s for s, _, _ in calls if s.startswith("v0.11.2+n20261103") and " · " in s]
    assert lines and "3 Nov 2026" in lines[0] and "17 Oct 2026" not in lines[0]
    drawn = [s for s, _, _ in calls] + [c[1][0] for c in rich]          # rich_text(c, fonts, font, s)
    assert any("Alt+F4 exits" in s for s in drawn) and not any("Hotkey" in s for s in drawn)


# ------------------------------------------------------------ Platforms cards count what the lists tick

def test_platform_cards_count_romm_games_on_the_box(isolated, rb):
    lib = rommlib.Library()
    _file("megadrive", "Tangle (World).md")                     # RomM's, copied by hand: ticked by name
    rec = _file("megadrive", "Abbey (World).md")
    lib.mark(_rom(11, "Abbey (World).md"), "megadrive", [rec])
    _file("megadrive", "Not In RomM.md")                        # ES lists it; the list never ticks it
    games = [_rom(10, "Tangle (World).md"), _rom(11, "Abbey (World).md"), _rom(12, "Away (World).md")]

    class A:
        library = lib
        platforms = [{"id": 1, "_system": "megadrive", "rom_count": 3},
                     {"id": 2, "_system": "gba", "rom_count": 5}]

        class api:
            @staticmethod
            def roms_all(platform_ids=None, keep=None, fields=None, **kw):
                assert platform_ids == [1]                       # gba has nothing here: never asked
                return [{"id": r["id"]} for r in games if keep(r)]
    assert rb.here_counts(A()) == {1: 2, 2: 0}


@pytest.mark.parametrize("cls", ["Confirm", "ActionMenu"])
def test_a_long_dialog_title_takes_the_smaller_heading(ui, rb, monkeypatch, cls):  # noqa: F811
    title = "Download Lorem Ipsum - Gray Dolor Sit Amet Legion again?"
    m = rb.Confirm(ui, title, "Body.", "OK", lambda: None) if cls == "Confirm" else \
        rb.ActionMenu(ui, title, [("Row", "", None)])
    calls = []
    real = rb.marquee

    def spy(c, font, s, *a, **k):
        calls.append((s, font))
        return real(c, font, s, *a, **k)
    rb.patch(monkeypatch, "marquee", spy)
    ui.push(rb.HomeScreen(ui))
    ui.modal = m
    ui.draw()
    font = next(f for s, f in calls if s == title)
    assert font is ui.fonts.h3 and rb.measure(ui.fonts.h3, title) <= 576
