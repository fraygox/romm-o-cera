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
    assert [p["name"] for p in lists] == ["Late night", "First tracks"] and lists[0]["is_public"]
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


def test_a_source_this_token_may_not_read_is_left_as_it_is_and_the_others_still_sync(api, fast_mock_url):
    game = api.music_all("games")[0]["rom_id"]
    rommlib.sync_music(api, sources(favourites=True, playlists=[1]))
    lists_only = placed_files()                      # what favourites and a playlist brought, with a token that may read them
    assert lists_only
    limited = rommlib.Api(fast_mock_url, "demo-no-lists")
    with pytest.raises(rommlib.ScopeMissing, match="playlists.read"):
        limited.music_playlists()
    with pytest.raises(rommlib.ScopeMissing):
        rommlib.music_wanted(limited, sources(favourites=True))          # asked directly, it still says so
    res = rommlib.sync_music(limited, sources(favourites=True, playlists=[1], games=[game]))
    assert res["error"] == "" and res["unreadable"] == ["favourites", "playlist:1"] and res["removed"] == 0
    assert set(lists_only) <= set(placed_files()) and res["downloaded"] > 0     # theirs stayed; the game's arrived
    assert limited.down is None                      # the token itself is fine: the header doesn't say otherwise
    # switched off, what only they asked for goes
    res = rommlib.sync_music(limited, sources(games=[game]))
    assert res["unreadable"] == [] and res["removed"] > 0
    mine = {t["rom_file_id"] for t in api.music_all("tracks", rom_id=game) if rommlib.music_playable(t)}
    assert {int(k) for k in rommlib.load_music_state()["tracks"]} == mine


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


# ---------------------------------------------------------------- listening in the app

def test_a_track_to_listen_to_is_fetched_once_and_one_in_the_menu_music_is_played_from_there(api, monkeypatch):
    tracks = [t for t in api.music_all("tracks") if rommlib.music_playable(t)]
    t = tracks[0]
    assert rommlib.music_local(t) is None
    path = rommlib.fetch_music(api, t)
    assert os.path.dirname(path) == rommlib.music_cache_dir() and os.path.getsize(path) > 100
    assert rommlib.music_local(t) == path and os.listdir(rommlib.music_cache_dir()) == [os.path.basename(path)]
    with monkeypatch.context() as m:
        m.setattr(api, "download_music", lambda *a: pytest.fail("fetched a track that's here"))
        assert rommlib.fetch_music(api, t) == path
    # a track the menu music holds is played from the music folder, never fetched a second time
    fav = api.music_all("favorites")[0]
    rommlib.sync_music(api, sources(favourites=True))
    placed = rommlib.fetch_music(api, fav)
    assert os.path.dirname(placed) == rommlib.music_dir()
    with pytest.raises(rommlib.RommError):
        rommlib.fetch_music(api, {"file_name": "x.mp3"})               # not a track of the jukebox


def test_the_listening_cache_drops_what_was_played_longest_ago_and_never_what_is_playing(api, monkeypatch):
    tracks = [t for t in api.music_all("tracks") if rommlib.music_playable(t)][:4]
    paths = []
    for i, t in enumerate(tracks[:3]):
        paths.append(rommlib.fetch_music(api, t))
        os.utime(paths[-1], (1000 + i, 1000 + i))                      # played in this order, long ago
    size = os.path.getsize(paths[0])
    monkeypatch.setattr(rommlib, "MUSIC_CACHE_MAX", int(size * 2.5))    # room for two and a bit
    dead = os.path.join(rommlib.music_cache_dir(), "9.wav.1.part")
    open(dead, "wb").write(b"x" * 10)
    os.utime(dead, (1000, 1000))                                        # a fetch that died long ago
    new = rommlib.fetch_music(api, tracks[3], keep=(paths[0],))
    left = sorted(os.listdir(rommlib.music_cache_dir()))
    # the oldest is playing and stays; the next oldest make room; the dead part is swept
    assert left == sorted(os.path.basename(p) for p in (paths[0], new))
    os.utime(paths[0], (1000, 1000))
    rommlib.fetch_music(api, tracks[0])                                 # listened to again: no longer the oldest
    assert os.path.getmtime(paths[0]) > 2000


def test_no_track_is_fetched_onto_a_drive_thats_all_but_full(api, monkeypatch):
    t = next(t for t in api.music_all("tracks") if rommlib.music_playable(t))
    kept = rommlib.fetch_music(api, t)
    other = next(x for x in api.music_all("tracks") if rommlib.music_playable(x) and x["rom_file_id"] != t["rom_file_id"])
    monkeypatch.setattr(rommlib, "free_bytes", lambda path=None: 1 << 20)
    with pytest.raises(rommlib.RommError, match="No room"):
        rommlib.fetch_music(api, other, keep=(kept,))
    assert os.listdir(rommlib.music_cache_dir()) == [os.path.basename(kept)]      # what's playing stayed; nothing half-fetched
    assert rommlib.fetch_music(api, t) == kept                                     # one that's here needs no room
    monkeypatch.setattr(rommlib, "free_bytes", lambda path=None: 1 << 30)          # room, though less than downloads keep free
    assert os.path.isfile(rommlib.fetch_music(api, other))


# ---------------------------------------------------------------- the jukebox index: grouping, sorting, finding

def test_the_index_keeps_what_music_groups_sorts_and_searches_by(api):
    stats = api.music_stats()
    idx = rommlib.build_music_index(api, stats)
    games = api.music_all("games")
    albums = idx["albums"]
    assert len(albums) == len(games) and sum(a["count"] for a in albums) == stats["total_tracks"]
    assert rommlib.load_music_index() == idx and idx["stats"] == stats        # kept for the next start
    assert [a["name"] for a in albums] == sorted((a["name"] for a in albums), key=str.lower)
    a = albums[0]
    tracks = api.music_all("tracks", rom_id=a["rom_id"])
    assert a["count"] == len(tracks) and abs(a["seconds"] - sum(t["duration_seconds"] or 0 for t in tracks)) < 0.2
    assert a["platform"] == tracks[0]["platform_name"] and a["genres"] == tracks[0]["game_genres"]
    assert a["artist"] in {t["artist"] for t in tracks} and a["artist"] == a["artists"][0]
    assert a["year"] == min(t["year"] for t in tracks if t["year"]) and a["added"] == max(t["added_at"] for t in tracks)
    assert a["cover"] == tracks[0]["game_cover_url"]
    # the shelves each grouping gives
    by_system = rommlib.music_groups(albums, "system")
    assert sum(len(v) for _, v in by_system) == len(albums) and all(x["platform"] == lab for lab, v in by_system for x in v)
    assert [len(v) for _, v in by_system] == sorted((len(v) for _, v in by_system), reverse=True)
    by_genre = dict(rommlib.music_groups(albums, "genre"))
    assert all(x in by_genre[g] for x in albums for g in x["genres"]) and sum(len(v) for v in by_genre.values()) == sum(len(x["genres"]) for x in albums)
    by_decade = rommlib.music_groups(albums, "decade")
    assert [lab for lab, _ in by_decade] == sorted(lab for lab, _ in by_decade)
    assert all(lab == "%ds" % (x["year"] // 10 * 10) for lab, v in by_decade for x in v)
    by_letter = rommlib.music_groups(albums, "letter")
    assert [lab for lab, _ in by_letter] == sorted(lab for lab, _ in by_letter) and sum(len(v) for _, v in by_letter) == len(albums)
    assert all(rommlib.music_letter(x["name"]) == lab for lab, v in by_letter for x in v)
    by_composer = rommlib.music_groups(albums, "composer")
    assert by_composer and all(x["artist"] == lab for lab, v in by_composer for x in v)
    assert all([x["name"].lower() for x in v] == sorted(x["name"].lower() for x in v) for _, v in by_system)
    # the orders a wall can be in
    assert [x["added"] for x in rommlib.music_sorted(albums, "added")] == sorted((x["added"] for x in albums), reverse=True)
    assert [x["seconds"] for x in rommlib.music_sorted(albums, "length")] == sorted((x["seconds"] for x in albums), reverse=True)
    assert [x["year"] for x in rommlib.music_sorted(albums, "year")] == sorted(x["year"] for x in albums)
    assert rommlib.music_sorted(albums) == albums
    # finding
    one = albums[3]
    word = max(one["name"].split(), key=len)
    found, composers = rommlib.music_find(idx, "  " + word.upper())
    assert one in found and all(word.lower() in x["name"].lower() for x in found)
    found, composers = rommlib.music_find(idx, one["artist"].split()[0].lower())
    assert one["artist"] in [n for n, _ in composers] and one in dict(composers)[one["artist"]]
    assert rommlib.music_find(idx, " ") == ([], []) and rommlib.music_find(None, "x") == ([], [])
    assert rommlib.music_find(idx, word + " zzzz") == ([], [])               # every word has to be there
    # stopped, and a file that can't be read
    stop = threading.Event()
    stop.set()
    assert rommlib.build_music_index(api, stats, cancel=stop) is None
    with open(rommlib.music_index_path(), "w") as f:
        f.write("{")
    assert rommlib.load_music_index() is None


def test_groupings_leave_out_what_has_no_value_and_composers_need_two_when_there_are_enough():
    def al(i, **kw):
        return dict({"rom_id": i, "name": "Game %02d" % i, "platform": "P", "genres": [], "artist": "", "year": None,
                     "added": "", "seconds": 0, "artists": []}, **kw)
    albums = [al(1, artist="Ann", year=1994), al(2, artist="Ann", year=1999), al(3, artist="Bo"), al(4, artist="Bo"),
              al(5, artist="Cy"), al(6, artist="Cy", platform=""), al(7, artist="Dee"), al(8), al(9, name="7th Game"), al(10, name="")]
    comp = rommlib.music_groups(albums, "composer")
    assert [lab for lab, _ in comp] == ["Ann", "Bo", "Cy"]            # Dee has one; the unknown aren't a composer
    assert [lab for lab, _ in rommlib.music_groups(albums[:5] + albums[6:7], "composer")] == ["Ann", "Bo", "Cy", "Dee"]      # too few with two: everyone
    assert rommlib.music_groups(albums, "decade") == [("1990s", albums[:2])]
    assert dict(rommlib.music_groups(albums, "system"))["Other"] == [albums[5]]
    assert [lab for lab, _ in rommlib.music_groups(albums, "letter")] == ["#", "G"]
    assert rommlib.music_groups(albums, "genre") == [] and rommlib.music_groups([], "system") == []
    assert [rommlib.music_letter(n) for n in ("  the Thing", "007", "", None, "¡Ay!")] == ["T", "#", "#", "#", "A"]
    assert [x["rom_id"] for x in rommlib.music_sorted(albums, "year")][:2] == [1, 2] and rommlib.music_sorted(albums, "year")[-1]["year"] is None
    many = [al(i, artist="C%d" % (i % 70)) for i in range(140)]
    assert len(rommlib.music_groups(many, "composer")) == rommlib.MUSIC_COMPOSER_SHELVES


# ---------------------------------------------------------------- what the review found

def test_a_sync_that_died_mid_file_leaves_nothing_behind_for_long(api):
    rommlib.sync_music(api, sources(playlists=[1]))
    part = os.path.join(rommlib.music_dir(), "Some album - 03 Half.mp3.part")
    own = os.path.join(rommlib.music_dir(), "my own.part.mp3")
    for p in (part, own):
        with open(p, "wb") as f:
            f.write(b"x" * 100)
    rommlib.sync_music(api, sources(playlists=[1]))
    assert not os.path.exists(part) and os.path.exists(own)          # the half-written track is swept; the owner's file isn't
    os.remove(own)
    rommlib.sync_music(api, sources(), on=False)
    assert not os.path.exists(rommlib.music_dir())


def test_a_file_the_owner_put_where_a_track_was_is_never_removed(api):
    rommlib.sync_music(api, sources(playlists=[1]))
    state = rommlib.load_music_state()
    fid, rec = next(iter(state["tracks"].items()))
    path = os.path.join(rommlib.music_dir(), rec["file"])
    with open(path, "wb") as f:
        f.write(b"the owner's own recording, under the same name")
    res = rommlib.sync_music(api, sources(), on=False)               # everything the app placed goes...
    assert os.listdir(rommlib.music_dir()) == [rec["file"]] and open(path, "rb").read().startswith(b"the owner")     # ...this isn't its
    assert res["removed"] == len(state["tracks"]) - 1 and rommlib.load_music_state()["tracks"] == {}
    # wanted again, the track comes back beside it, under a name of its own
    rommlib.sync_music(api, sources(playlists=[1]))
    again = rommlib.load_music_state()["tracks"][fid]
    assert again["file"] != rec["file"] and "(%s)" % fid in again["file"] and open(path, "rb").read().startswith(b"the owner")
    assert rommlib.music_local({"rom_file_id": int(fid), "file_name": "x.wav"}).endswith(again["file"])


def test_another_romm_means_another_jukebox(api, fast_mock_url):
    rommlib.sync_music(api, sources(playlists=[1]))
    first = placed_files()
    t = next(t for t in api.music_all("tracks") if rommlib.music_playable(t) and str(t["rom_file_id"]) not in rommlib.load_music_state()["tracks"])
    cached = rommlib.fetch_music(api, t)
    assert os.path.dirname(cached) == rommlib.music_cache_dir() and rommlib.load_music_state()["server"] == api.base
    idx = rommlib.build_music_index(api, api.music_stats())
    assert rommlib.load_music_index(api.base) == idx and rommlib.load_music_index("http://elsewhere") is None
    other = rommlib.Api(fast_mock_url.replace("127.0.0.1", "localhost"), "demo")      # the same mock under another address
    assert other.base != api.base
    placed = next(iter(rommlib.load_music_state()["tracks"].values()))
    held = {"rom_file_id": int(next(iter(rommlib.load_music_state()["tracks"]))), "file_name": placed["file"]}
    assert rommlib.music_local(held, api.base) and rommlib.music_local(held, other.base) is None    # placed from the first: not the second's
    again = rommlib.fetch_music(other, t)
    assert os.path.exists(again) and open(rommlib.music_cache_dir() + ".server").read() == other.base
    assert os.listdir(rommlib.music_cache_dir()) == [os.path.basename(again)]        # the first server's cached track went
    res = rommlib.sync_music(other, sources(playlists=[1]))
    assert res["removed"] == len(first) and res["downloaded"] == len(first) and rommlib.load_music_state()["server"] == other.base
    assert rommlib.prune_music_cache(0) > 0 and os.listdir(rommlib.music_cache_dir()) == []


def test_track_numbers_and_names_of_any_type_never_stop_a_plan(api):
    odd = {1: {"rom_file_id": 1, "album": "A", "rom_id": 5, "track": "01", "disc": None, "duration_seconds": 10},
           2: {"rom_file_id": 2, "album": "A", "rom_id": 5, "track": 2, "disc": "1", "duration_seconds": 10},
           3: {"rom_file_id": 3, "album": None, "rom_id": None, "track": True, "disc": 1.5, "duration_seconds": "long"},
           4: {"rom_file_id": 4, "album": 7, "rom_id": "x", "track": None, "disc": 2}}
    keep, left = rommlib.music_plan(odd, {}, 1 << 40)
    assert sorted(keep) == [1, 2, 3, 4] and left == 0
    assert [rommlib.music_num(v) for v in (3, "3", None, True, 2.0)] == [3, 0, 0, 0, 0]


def test_no_record_of_any_game_here_keeps_what_games_on_this_box_brought(api):
    game = api.music_all("games")[0]["rom_id"]
    res = rommlib.sync_music(api, sources(on_box=True), library=Box(game))
    assert res["downloaded"] > 0 and res["unreadable"] == []
    before = placed_files()
    damaged = Box()
    damaged.unreadable = lambda: True                                            # the list of installed games can't be read
    res = rommlib.sync_music(api, sources(on_box=True), library=damaged)
    assert res["unreadable"] == ["on_box"] and res["removed"] == 0 and placed_files() == before
    res = rommlib.sync_music(api, sources(on_box=False), library=damaged)        # switched off: they go
    assert res["removed"] == len(before) and placed_files() == []


def test_many_games_on_the_box_are_asked_for_in_one_listing(api, monkeypatch):
    games = [g["rom_id"] for g in api.music_all("games")]
    monkeypatch.setattr(rommlib, "MUSIC_ON_BOX_EACH", 3)
    calls = []
    real = api.music_all
    monkeypatch.setattr(api, "music_all", lambda what, cancel=None, **f: calls.append((what, f)) or real(what, cancel=cancel, **f))
    few = rommlib.music_wanted(api, sources(on_box=True), library=Box(*games[:3]))
    assert [c for c in calls if c[0] == "tracks"] == [("tracks", {"rom_id": g}) for g in sorted(games[:3], key=[x["rom_id"] for x in real("games")].index)]
    del calls[:]
    many = rommlib.music_wanted(api, sources(on_box=True), library=Box(*games[:5]))
    assert [c for c in calls if c[0] == "tracks"] == [("tracks", {})] and {t["rom_id"] for t in many.values()} == set(games[:5])
    assert len(few) < len(many)


def test_one_page_of_a_listing_gives_the_first_and_the_count(api, fast_mock_url):
    items, total = api.music_page("tracks", 5)
    assert len(items) == 5 and total == api.music_stats()["total_tracks"]
    items, total = api.music_page("favorites", 1)
    assert len(items) == 1 and total == len(api.music_all("favorites"))
    found, total = api.music_page("tracks", 200, search="cave")
    assert found and total == len(found) and all("cave" in (t["title"] or "").lower() for t in found)
    with pytest.raises(rommlib.ScopeMissing):
        rommlib.Api(fast_mock_url, "demo-no-lists").music_page("favorites", 1)


def test_the_status_screens_draw_from_is_read_again_only_when_it_changed(api, monkeypatch):
    rommlib.sync_music(api, sources(playlists=[1]))
    st = rommlib.music_status()
    assert st["tracks"] == 12 and st["bytes"] > 0 and st["rate"] > 0 and st["last"]["downloaded"] == 12
    reads = []
    real = rommlib.load_music_state
    monkeypatch.setattr(rommlib, "load_music_state", lambda: reads.append(1) or real())
    for _ in range(50):
        assert rommlib.music_status() == st
    assert reads == []                                               # nothing read while the file is as it was
    rommlib.sync_music(api, sources())
    assert rommlib.music_status()["tracks"] == 0
