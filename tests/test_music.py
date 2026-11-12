"""Menu music (romm/music.py): sources, the plan under the space cap, and the sync that
keeps the music folder in step, against the mock RomM's jukebox (tools/mock_romm.py)."""
import os
import threading

import pytest

import rommlib


@pytest.fixture
def api(isolated, fast_mock_url):
    return rommlib.Api(fast_mock_url, "demo")


def sources(**kw):
    return dict({"favourites": False, "on_box": False, "playlists": [], "games": [], "mixes": []}, **kw)


def placed_files():
    d = rommlib.music_dir()
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


class Box:
    """A library holding these rom ids (all sync_music asks of one is snapshot())."""
    def __init__(self, *ids):
        self.ids = ids

    def snapshot(self):
        return {i: {} for i in self.ids}


# ---------------------------------------------------------------- the jukebox the app reads

def test_the_jukebox_listing_comes_whole_and_filters_the_way_romm_does(api):
    tracks = api.music_all("tracks")
    assert len(tracks) == api.music_stats()["total_tracks"] > 40
    for k in ("rom_file_id", "rom_id", "file_name", "title", "artist", "album", "genre", "year", "track",
              "duration_seconds", "game_genres", "platform_id", "stream_url", "is_favorite"):
        assert k in tracks[0], k
    one = next(t for t in tracks if t["artist"])
    assert {t["artist"] for t in api.music_all("tracks", artist=one["artist"])} == {one["artist"]}
    assert {t["rom_id"] for t in api.music_all("tracks", rom_id=one["rom_id"])} == {one["rom_id"]}
    assert all(t["duration_seconds"] <= 120 for t in api.music_all("tracks", max_duration=120))
    for facet in ("albums", "artists", "genres", "years", "game-genres"):
        rows = api.music_all(facet)
        assert rows and all(set(r) == {"value", "count"} for r in rows), facet
    assert sum(r["count"] for r in api.music_all("platforms")) == len(tracks)
    assert sum(g["count"] for g in api.music_all("games")) == len(tracks)
    favs = api.music_all("favorites")
    assert favs and all(t["is_favorite"] for t in favs)
    lists = api.music_playlists()
    assert [p["name"] for p in lists] == ["Menu music", "First tracks"] and lists[0]["is_public"]
    assert len(api.music_all("playlists/%d/tracks" % lists[0]["id"])) == lists[0]["track_count"]


def test_a_listing_longer_than_a_page_is_read_page_by_page(api, monkeypatch):
    monkeypatch.setattr(rommlib, "MUSIC_PAGE", 7)
    assert len(api.music_all("tracks")) == api.music_stats()["total_tracks"]
    stop = threading.Event()
    stop.set()
    assert api.music_all("tracks", cancel=stop) is None


# ---------------------------------------------------------------- sync

def test_favourites_land_in_the_music_folder_and_a_second_sync_fetches_nothing(api):
    favs = api.music_all("favorites")
    res = rommlib.sync_music(api, sources(favourites=True))
    assert (res["wanted"], res["downloaded"], res["placed"], res["removed"], res["error"]) == (len(favs), len(favs), len(favs), 0, "")
    files = placed_files()
    assert len(files) == len(favs) and all(f.endswith(".wav") for f in files)
    assert rommlib.music_dir() == os.path.join(rommlib.USERDATA, "music", "romm-o-cera")
    t = favs[0]
    assert "%s - %02d %s.wav" % (t["album"], t["track"], t["title"]) in files
    st = rommlib.music_status()
    assert st["tracks"] == len(favs) and st["bytes"] == sum(os.path.getsize(os.path.join(rommlib.music_dir(), f)) for f in files)
    again = rommlib.sync_music(api, sources(favourites=True))
    assert (again["downloaded"], again["removed"], again["placed"]) == (0, 0, len(favs))
    assert rommlib.music_status()["last"] == again


def test_sources_add_up_and_a_track_two_of_them_name_is_kept_once(api):
    lists = api.music_playlists()
    favs = {t["rom_file_id"] for t in api.music_all("favorites")}
    first = {t["rom_file_id"] for t in api.music_all("playlists/%d/tracks" % lists[1]["id"])}
    assert favs & first, "the mock should have a favourite that is also a first track"
    res = rommlib.sync_music(api, sources(favourites=True, playlists=[lists[1]["id"]]))
    assert res["placed"] == len(favs | first) == len(placed_files())
    recs = rommlib.load_music_state()["tracks"]
    both = str(next(iter(favs & first)))
    assert recs[both]["sources"] == ["favourites", "playlist:%d" % lists[1]["id"]]
    # the playlist switched off: only what favourites alone asked for stays
    res = rommlib.sync_music(api, sources(favourites=True))
    assert res["removed"] == len(first - favs) and res["placed"] == len(favs) == len(placed_files())
    assert rommlib.load_music_state()["tracks"][both]["sources"] == ["favourites"]


def test_turning_menu_music_off_removes_what_the_app_placed_and_only_that(api):
    rommlib.sync_music(api, sources(favourites=True))
    mine = os.path.join(rommlib.music_dir(), "My own song.mp3")
    open(mine, "wb").write(b"mine")
    other = os.path.join(rommlib.USERDATA, "music", "Another.ogg")
    open(other, "wb").write(b"mine too")
    res = rommlib.sync_music(api, sources(favourites=True), on=False)
    assert res["placed"] == 0 and res["removed"] > 0 and not res["error"]
    assert placed_files() == ["My own song.mp3"] and os.path.exists(other)      # the folder stays: it isn't empty
    os.remove(mine)
    rommlib.sync_music(api, sources(favourites=True))
    rommlib.sync_music(api, sources(favourites=True), on=False)
    assert not os.path.exists(rommlib.music_dir())            # empty again: Batocera's own songs play


def test_a_file_of_the_owners_with_a_tracks_name_is_never_written_over(api):
    t = api.music_all("favorites")[0]
    os.makedirs(rommlib.music_dir())
    theirs = os.path.join(rommlib.music_dir(), rommlib.music_file_name(t))
    open(theirs, "wb").write(b"the owner's own file")
    res = rommlib.sync_music(api, sources(favourites=True))
    assert res["failed"] == 0 and open(theirs, "rb").read() == b"the owner's own file"
    rec = rommlib.load_music_state()["tracks"][str(t["rom_file_id"])]
    assert rec["file"] != os.path.basename(theirs) and str(t["rom_file_id"]) in rec["file"]
    rommlib.sync_music(api, sources(), on=False)
    assert placed_files() == [os.path.basename(theirs)]


def test_a_track_es_cant_play_is_left_on_romm_and_counted(api):
    odd = next(t for t in api.music_all("tracks") if t["file_name"].endswith(".spc"))
    album = api.music_all("tracks", rom_id=odd["rom_id"])
    res = rommlib.sync_music(api, sources(games=[odd["rom_id"]]))
    assert res["wanted"] == len(album) and res["skipped"] == 1 and res["placed"] == len(album) - 1
    assert not [f for f in placed_files() if f.endswith(".spc")]


def test_games_on_this_box_bring_their_soundtracks(api):
    games = api.music_all("games")
    here, away = games[0], games[1]
    res = rommlib.sync_music(api, sources(on_box=True), library=Box(here["rom_id"], 999999))
    recs = rommlib.load_music_state()["tracks"]
    assert res["wanted"] == here["count"] and {r["rom_id"] for r in recs.values()} == {here["rom_id"]}
    assert all(r["sources"] == ["on_box"] for r in recs.values())
    # the game deleted: its tracks go with the next sync, unless another source asks for them
    res = rommlib.sync_music(api, sources(on_box=True, games=[away["rom_id"]]), library=Box())
    assert {r["rom_id"] for r in rommlib.load_music_state()["tracks"].values()} == {away["rom_id"]}


def test_romm_not_answering_leaves_the_folder_as_it_is(api):
    rommlib.sync_music(api, sources(favourites=True))
    before = placed_files()
    down = rommlib.Api("http://127.0.0.1:9", "demo")
    res = rommlib.sync_music(down, sources(favourites=True))
    assert "reach RomM" in res["error"] and res["removed"] == 0 and placed_files() == before
    assert rommlib.music_status()["tracks"] == len(before)
    # with nothing chosen there is nothing to ask RomM: the tracks go, RomM up or not
    res = rommlib.sync_music(down, sources())
    assert res["error"] == "" and res["removed"] == len(before) and placed_files() == []


def test_a_token_that_may_not_read_favourites_says_so_and_changes_nothing(api, fast_mock_url):
    rommlib.sync_music(api, sources(games=[api.music_all("games")[0]["rom_id"]]))
    before = placed_files()
    limited = rommlib.Api(fast_mock_url, "demo-no-lists")
    with pytest.raises(rommlib.ScopeMissing, match="playlists.read"):
        limited.music_playlists()
    res = rommlib.sync_music(limited, sources(favourites=True))
    assert "favourites and playlists" in res["error"] and res["removed"] == 0 and placed_files() == before
    assert limited.down is None                      # the token itself is fine: the header doesn't say otherwise
    assert rommlib.sync_music(limited, sources(games=[api.music_all("games")[0]["rom_id"]]))["error"] == ""


def test_a_placed_file_that_went_missing_is_fetched_again(api):
    rommlib.sync_music(api, sources(favourites=True))
    gone = placed_files()[0]
    os.remove(os.path.join(rommlib.music_dir(), gone))
    res = rommlib.sync_music(api, sources(favourites=True))
    assert res["downloaded"] == 1 and res["removed"] == 0 and gone in placed_files()


def test_a_sync_stops_before_the_space_to_keep_free_is_touched(api, monkeypatch):
    monkeypatch.setattr(rommlib, "free_bytes", lambda p=None: 5 << 20)
    res = rommlib.sync_music(api, sources(favourites=True), keep_free=1 << 30)
    assert res["no_room"] and res["downloaded"] == 0 and placed_files() == []


def test_only_one_sync_runs_at_a_time(api, monkeypatch):
    inside = {}

    def tracks(a, key, ctx):
        inside["second"] = rommlib.sync_music(api, sources(favourites=True))
        return []
    monkeypatch.setattr(rommlib, "MUSIC_SOURCES", (rommlib.MusicSource("favourites", lambda sel: ["favourites"], tracks),))
    rommlib.sync_music(api, sources(favourites=True))
    assert inside["second"] == {"busy": True}


def test_a_stopped_sync_keeps_what_it_fetched(api):
    stop = threading.Event()
    res = rommlib.sync_music(api, sources(favourites=True), progress=lambda done, total: stop.set(), cancel=stop)
    assert res["stopped"] and res["downloaded"] == 1 == len(placed_files())
    res = rommlib.sync_music(api, sources(favourites=True))
    assert res["downloaded"] == res["wanted"] - 1


# ---------------------------------------------------------------- the cap

def track(fid, album, n, secs=100.0, rom=None):
    return {"rom_file_id": fid, "rom_id": rom or hash(album) % 1000, "album": album, "track": n, "disc": 1,
            "duration_seconds": secs, "file_name": "%02d.mp3" % n}


def test_past_the_cap_every_album_keeps_some_tracks():
    wanted = {}
    for a, album in enumerate(["Alpha", "Bravo", "Charlie"]):
        for n in range(1, 7):
            wanted[a * 10 + n] = track(a * 10 + n, album, n)
    size = 100 * rommlib.MUSIC_RATE
    keep, left = rommlib.music_plan(wanted, {}, cap=7 * size)
    assert len(keep) == 7 and left == 11
    assert keep == [1, 11, 21, 2, 12, 22, 3]                  # one from each album in turn, in album order
    assert rommlib.music_plan(wanted, {}, cap=100 * size) == (keep + [13, 23, 4, 14, 24, 5, 15, 25, 6, 16, 26], 0)
    assert rommlib.music_plan(wanted, {}, cap=0) == ([], 18)


def test_the_plan_counts_a_track_here_at_its_real_size_and_learns_the_rate():
    wanted = {i: track(i, "Alpha", i) for i in range(1, 9)}
    placed = {str(i): {"file": "x", "bytes": 1000, "seconds": 100.0} for i in range(1, 6)}      # 10 bytes a second
    assert rommlib.music_rate(placed) == 10 and rommlib.music_rate({}) == rommlib.MUSIC_RATE
    keep, left = rommlib.music_plan(wanted, placed, cap=7000)
    assert (keep, left) == ([1, 2, 3, 4, 5, 6, 7], 1)         # 5 here at 1000 each, then 1000 a piece by the learned rate
    wanted[9] = track(9, "Alpha", 9, secs=None)
    assert 9 not in rommlib.music_plan(wanted, placed, cap=7000)[0]      # no length known: counted as three minutes
    keep, left = rommlib.music_plan({9: wanted[9]}, placed, cap=180 * 10)
    assert (keep, left) == ([9], 0)


def test_the_cap_holds_on_the_box(api):
    favs = api.music_all("favorites")
    # room for about two tracks by their length (nothing is here yet to learn a rate from)
    res = rommlib.sync_music(api, sources(favourites=True), cap=2 * 400 * rommlib.MUSIC_RATE)
    assert 0 < res["placed"] < len(favs) and res["left_out"] == len(favs) - res["placed"]
    assert len(placed_files()) == res["placed"]


# ---------------------------------------------------------------- mixes

def test_a_mix_takes_one_value_of_every_facet_that_has_any(api):
    tracks = [t for t in api.music_all("tracks") if t["title"]]
    genres = sorted({g for t in tracks for g in t["game_genres"]})[:2]
    artists = sorted({t["artist"] for t in tracks})[:2]
    decades = sorted({t["year"] // 10 * 10 for t in tracks})[:1]
    f = {"game_genre": genres, "artist": artists, "decade": decades, "max_minutes": 4}
    want = {t["rom_file_id"] for t in tracks if set(genres) & set(t["game_genres"]) and t["artist"] in artists
            and t["year"] // 10 * 10 in decades and t["duration_seconds"] <= 240}
    got = {t["rom_file_id"] for t in rommlib.music_mix_tracks(api, f)}
    assert got == want
    for f in ({"platform": [tracks[0]["platform_id"]]}, {"decade": decades}, {"genre": [tracks[0]["genre"]], "platform": [tracks[0]["platform_id"]]},
              {"max_minutes": 2}, {}):
        want = {t["rom_file_id"] for t in api.music_all("tracks") if rommlib.music_mix_takes(f, t)}
        assert {t["rom_file_id"] for t in rommlib.music_mix_tracks(api, f)} == want, f
    assert want == {t["rom_file_id"] for t in api.music_all("tracks")}        # an empty mix is everything


def test_a_saved_mix_feeds_the_menu_music_only_while_its_on(api):
    genre = next(t["genre"] for t in api.music_all("tracks") if t["genre"])
    n = len(api.music_all("tracks", genre=genre))
    mix = {"name": "Just " + genre, "on": True, "filters": {"genre": [genre]}}
    res = rommlib.sync_music(api, sources(mixes=[mix]))
    assert res["placed"] == n
    assert all(r["sources"] == ["mix:Just " + genre] for r in rommlib.load_music_state()["tracks"].values())
    res = rommlib.sync_music(api, sources(mixes=[dict(mix, on=False)]))
    assert res["placed"] == 0 and res["removed"] == n


# ---------------------------------------------------------------- what's kept on the box

def test_the_choice_of_sources_is_saved_and_a_damaged_file_means_nothing_chosen(isolated):
    assert rommlib.load_music_sources() == sources()
    sel = sources(favourites=True, playlists=[3, "4", "x", 3], games=[7],
                  mixes=[{"name": " Night drive ", "on": 1, "filters": {"genre": ["Synthwave", "Synthwave", 5], "decade": ["1990", 2000],
                                                                         "platform": [], "max_minutes": "6", "junk": 1}},
                         {"name": "night drive", "on": True, "filters": {}}, {"name": ""}, "nope"])
    rommlib.save_music_sources(sel)
    got = rommlib.load_music_sources()
    assert got["playlists"] == [3, 4] and got["games"] == [7] and got["favourites"] is True
    assert got["mixes"] == [{"name": "Night drive", "on": True, "filters": {"genre": ["Synthwave"], "decade": [1990, 2000], "max_minutes": 6.0}}]
    open(rommlib.music_sources_path(), "w").write("{not json")
    assert rommlib.load_music_sources() == sources()


def test_file_names_read_well_and_never_leave_the_folder():
    t = {"rom_file_id": 5, "album": "A/B: The \"Game\"", "track": 3, "title": "Boss?  <Final>", "file_name": "03 - x.MP3"}
    assert rommlib.music_file_name(t) == "A-B- The -Game- - 03 Boss-  -Final-.mp3"
    assert rommlib.music_file_name(t, {"a-b- the -game- - 03 boss-  -final-.mp3"}).endswith(" (5).mp3")
    assert rommlib.music_file_name({"rom_file_id": 6, "file_name": "../../etc/x.ogg", "game_name": "Game"}) == "Game - ..-..-etc-x.ogg"
    long = rommlib.music_file_name({"rom_file_id": 7, "album": "é" * 200, "title": "t", "file_name": "a.flac"})
    assert len(long.encode()) <= 190 and long.endswith(".flac")
    assert rommlib.music_playable({"file_name": "a.OPUS"}) and not rommlib.music_playable({"file_name": "a.m4a"})
    assert not rommlib.music_playable({})


def test_a_record_naming_a_path_outside_the_folder_removes_nothing_there(api, isolated):
    outside = isolated / "precious.mp3"
    outside.write_bytes(b"keep")
    os.makedirs(rommlib.DATA_DIR)
    rommlib.atomic_write_json(rommlib.music_state_path(), {"tracks": {"1": {"file": "../../precious.mp3", "bytes": 4}}})
    res = rommlib.sync_music(api, sources(), on=False)
    assert outside.read_bytes() == b"keep" and res["placed"] == 0
