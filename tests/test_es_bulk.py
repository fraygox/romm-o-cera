"""A big download handed to EmulationStation (#11): on Batocera 44 and later the new games'
info and media go in one /addgames per system, instead of one call per game and file, each
of which makes ES rebuild the list on screen. 43.1's ES takes no body there: one at a time,
as before. A big batch says in ES that it's under way, and when it's done, on every version."""
import os

import pytest

import rommlib
import rb_finalize
from test_es_media import JPG, MP4, PNG, all_files, rom_54, served  # noqa: F401 (served: a fixture)

ES = {"nes": {".zip"}}


@pytest.fixture
def es(isolated, fake_es):
    e = fake_es(ES)
    old = os.path.join(rommlib.ROMS_ROOT, "nes", "Old.zip")
    os.makedirs(os.path.dirname(old))
    open(old, "w").close()
    e.reload()                                  # a system ES lists already (no 201 reload)
    return e


def _download(served, name="Lorem Mages.zip"):
    files, _ = served
    rom = rom_54()
    files.update(all_files(rom))
    path = os.path.join(rommlib.ROMS_ROOT, "nes", name)
    open(path, "wb").close()
    return path, rommlib.gamelist_entry(rom, "nes", [path])


def _per_game(es):
    return [p for p in es.posts if p[0] in ("meta", "media")]


def test_on_batocera_44_new_games_go_to_es_in_one_call(es, served, monkeypatch):
    monkeypatch.setattr(rb_finalize, "batocera_major", lambda: 44)
    paths = []
    for k in range(3):
        path, e = _download(served, "Lorem Mages %d.zip" % k)
        paths.append((path, e))
    assert rb_finalize.process([e for _, e in paths], None, ES) == []
    assert _per_game(es) == []                                       # no call per game or file
    assert es.calls.count(("POST", "/addgames/nes")) == 2           # listed, then everything at once
    assert not [c for c in es.calls if c == ("GET", "/reloadgames")]
    g = es.game("nes", "Lorem Mages 1.zip")
    assert g["name"] == "Lorem Mages" and g["region"] == "wr" and g["family"] == "Lorem Saga"
    images = os.path.join(rommlib.ROMS_ROOT, "nes", "images")
    assert g["thumbnail"] == "./images/Lorem Mages 1-thumb.png"     # where ES itself would put it
    assert open(os.path.join(images, "Lorem Mages 1-thumb.png"), "rb").read() == PNG
    assert open(os.path.join(images, "Lorem Mages 1-image.jpg"), "rb").read() == JPG
    assert open(os.path.join(rommlib.ROMS_ROOT, "nes", "videos", "Lorem Mages 1-video.mp4"), "rb").read() == MP4
    assert g["manual"] == "./manuals/Lorem Mages 1-manual.pdf"
    assert not [f for d, _, fs in os.walk(rommlib.ROMS_ROOT) for f in fs if f.endswith(".part")]


def test_on_batocera_43_1_each_game_goes_one_at_a_time_as_before(es, served, monkeypatch):
    """43.1's ES answers 204 to every /addgames body: a full reload, then a call per game and file."""
    monkeypatch.setattr(rb_finalize, "batocera_major", lambda: 43)
    es.addgames_bug = True
    path, e = _download(served)
    assert rb_finalize.process([e], None, ES) == []
    assert es.calls.count(("GET", "/reloadgames")) == 1
    assert ("media", "nes", "Lorem Mages", "thumbnail") in _per_game(es)
    assert es.game("nes", "Lorem Mages.zip")["region"] == "wr"


def test_an_es_that_lists_games_but_is_older_than_44_goes_one_at_a_time(es, served, monkeypatch):
    monkeypatch.setattr(rb_finalize, "batocera_major", lambda: 43)
    path, e = _download(served)
    assert rb_finalize.process([e], None, ES) == []
    assert es.calls.count(("POST", "/addgames/nes")) == 1 and _per_game(es)


def test_what_the_one_call_didnt_set_goes_one_at_a_time_after_all(es, served, monkeypatch):
    monkeypatch.setattr(rb_finalize, "batocera_major", lambda: 44)
    real = es.add_games

    def no_video(system, body):              # an ES that drops one tag from the body
        return real(system, body.replace(b"<video>", b"<x>").replace(b"</video>", b"</x>"))
    monkeypatch.setattr(es, "add_games", no_video)
    path, e = _download(served)
    assert rb_finalize.process([e], None, ES) == []
    assert _per_game(es) == [("media", "nes", "Lorem Mages", "video")]
    assert es.game("nes", "Lorem Mages.zip")["video"] == "./videos/Lorem Mages-video.mp4"


def test_a_game_es_doesnt_list_after_the_one_call_is_kept_and_its_files_go(es, served, monkeypatch):
    monkeypatch.setattr(rb_finalize, "batocera_major", lambda: 44)
    path, e = _download(served)
    real, n = es.add_games, []

    def forget(system, body):                # listed, then gone again before the second call
        n.append(1)
        if len(n) == 2:
            with es.lock:
                es.mem["nes"].pop(path, None)
            return 204, "204 No game added / updated"
        return real(system, body)
    monkeypatch.setattr(es, "add_games", forget)
    left = rb_finalize.process([e], None, ES)
    assert [x["paths"] for x in left] == [[path]]
    images = os.path.join(rommlib.ROMS_ROOT, "nes", "images")
    assert not [f for f in os.listdir(images) if f.startswith("Lorem Mages-")]   # sent as usual next time


def test_media_the_owner_has_is_never_written_over(es, served, monkeypatch):
    monkeypatch.setattr(rb_finalize, "batocera_major", lambda: 44)
    path, e = _download(served)
    images = os.path.join(rommlib.ROMS_ROOT, "nes", "images")
    os.makedirs(images)
    with open(os.path.join(images, "Lorem Mages-thumb.png"), "wb") as f:
        f.write(b"theirs")
    assert rb_finalize.process([e], None, ES) == []
    assert open(os.path.join(images, "Lorem Mages-thumb.png"), "rb").read() == b"theirs"
    assert "thumbnail" not in es.game("nes", "Lorem Mages.zip")


@pytest.mark.parametrize("version", [43, 44])
def test_a_big_batch_says_in_es_that_its_under_way_and_when_its_done(es, served, monkeypatch, version):
    monkeypatch.setattr(rb_finalize, "batocera_major", lambda: version)
    monkeypatch.setattr(rb_finalize, "wait_idle", lambda *a: True)
    monkeypatch.setattr(rb_finalize, "game_running", lambda: False)
    monkeypatch.setattr(rb_finalize, "NOTIFY_FROM", 2)
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    es.addgames_bug = version < 44
    entries = [_download(served, "Lorem Mages %d.zip" % k)[1] for k in range(2)]
    rommlib.append_gamelist(*entries)
    assert rb_finalize.drain() is True
    assert es.notes[0].startswith("RomM-o-Cera is updating 2 games in EmulationStation")
    assert es.notes[-1] == "RomM-o-Cera is done updating EmulationStation: 2 games."


def test_a_small_batch_says_nothing(es, served, monkeypatch):
    monkeypatch.setattr(rb_finalize, "wait_idle", lambda *a: True)
    monkeypatch.setattr(rb_finalize, "game_running", lambda: False)
    os.makedirs(rommlib.DATA_DIR, exist_ok=True)
    rommlib.append_gamelist(_download(served)[1])
    assert rb_finalize.drain() is True and es.notes == []


@pytest.mark.parametrize("text, major", [("44 2026/11/16 12:25\n", 44), ("43.1 2025/06/01\n", 43), ("", 0),
                                         ("butterfly", 0)])
def test_batocera_major(tmp_path, monkeypatch, text, major):
    f = tmp_path / "batocera.version"
    f.write_text(text)
    monkeypatch.setattr(rb_finalize, "VERSION_FILE", str(f))
    assert rb_finalize.batocera_major() == major
    monkeypatch.setattr(rb_finalize, "VERSION_FILE", str(tmp_path / "none"))
    assert rb_finalize.batocera_major() == 0


def test_media_written_before_a_kill_is_sent_next_time(es, served, monkeypatch):
    """Killed between writing the media and ES taking it: the next run removes what ES doesn't
    name (else it would pass for someone else's file and never be sent) and sends it as usual."""
    monkeypatch.setattr(rb_finalize, "batocera_major", lambda: 44)
    path, e = _download(served)

    def killed(self):
        raise RuntimeError("killed")
    with monkeypatch.context() as m:
        m.setattr(rb_finalize.BulkEs, "flush", killed)
        with pytest.raises(RuntimeError):
            rb_finalize.process([dict(e)], None, ES)
    assert os.path.exists(rb_finalize.bulk_journal())
    assert rb_finalize.process([dict(e)], None, ES) == []          # listed now: one at a time
    assert not os.path.exists(rb_finalize.bulk_journal())
    g = es.game("nes", "Lorem Mages.zip")
    assert g["thumbnail"] == "./images/Lorem Mages-thumb.png" and g["video"] == "./videos/Lorem Mages-video.mp4"


def test_a_list_es_cant_send_after_the_one_call_keeps_the_batch_and_the_files(es, served, monkeypatch):
    """Not known what ES took: nothing is taken for lost (its files stay, ES may name them);
    the batch goes again and the next run sorts it out."""
    monkeypatch.setattr(rb_finalize, "batocera_major", lambda: 44)
    path, e = _download(served)
    real_add, real_get, n = es.add_games, rb_finalize.GameLists.get, []

    def add(system, body):
        n.append(1)
        return real_add(system, body)

    def get(self, system):
        return None if len(n) >= 2 else real_get(self, system)
    monkeypatch.setattr(es, "add_games", add)
    with monkeypatch.context() as m:
        m.setattr(rb_finalize.GameLists, "get", get)
        with pytest.raises(rommlib.EsError):
            rb_finalize.process([dict(e)], None, ES)
    thumb = os.path.join(rommlib.ROMS_ROOT, "nes", "images", "Lorem Mages-thumb.png")
    assert os.path.isfile(thumb)                                     # ES has it: kept
    assert rb_finalize.process([dict(e)], None, ES) == []
    assert os.path.isfile(thumb) and es.game("nes", "Lorem Mages.zip")["thumbnail"] == "./images/Lorem Mages-thumb.png"


def test_the_first_games_of_a_system_go_in_one_call_too(isolated, fake_es, served, monkeypatch):
    """A system ES didn't have: it loads it and reloads every list by itself (201); then the
    games' info and media still go in one call."""
    monkeypatch.setattr(rb_finalize, "batocera_major", lambda: 44)
    es = fake_es(ES)
    os.makedirs(os.path.join(rommlib.ROMS_ROOT, "nes"))
    path, e = _download(served)
    assert rb_finalize.process([e], None, ES) == []
    assert _per_game(es) == []
    assert es.game("nes", "Lorem Mages.zip")["thumbnail"] == "./images/Lorem Mages-thumb.png"
