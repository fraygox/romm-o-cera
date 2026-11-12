"""Menu music: tracks from RomM's jukebox kept in a folder EmulationStation
plays from. The owner picks sources (their RomM favourites, playlists, the games
on this box, single games, saved mixes); sync_music() makes the folder hold what
they add up to, under a space cap. It writes and removes only its own files.

Adding a kind of source is one entry in MUSIC_SOURCES; another way of laying the
files out is another music_file_name()."""
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable

from . import core as _core, config as _config

MUSIC_FOLDER = "romm-o-cera"                               # inside ES's music folder: ES plays its subfolders too
MUSIC_PLAYABLE = (".mp3", ".ogg", ".flac", ".wav", ".opus")   # what Batocera's ES plays (41+ for .opus)
MUSIC_RATE = 32000              # bytes a second assumed for a track not here yet (a 256 kbit/s MP3)
MUSIC_UNKNOWN_SECONDS = 180     # ... and its length when RomM read none
MUSIC_FILE_MAX = 512 << 20      # no single track is larger
MUSIC_FAILS = 3                 # downloads failing in a row before a sync gives up
MUSIC_CAP_CHOICES = (1, 2, 5, 10, 20, 50, 0)       # GB the Music screen offers; 0: no limit
MUSIC_MIX_FACETS = ("game_genre", "genre", "artist", "platform", "decade")     # a mix's lists; "max_minutes" beside them
_MUSIC_BUSY = threading.Lock()


def music_dir():
    """Where the app keeps its tracks: ES picks its songs from here (and anything else under music/)."""
    return os.path.join(_core.USERDATA, "music", MUSIC_FOLDER)


def music_state_path():
    return os.path.join(_core.DATA_DIR, "music.json")


def music_sources_path():
    return os.path.join(_core.DATA_DIR, "music-sources.json")


def music_cap_bytes(cfg):
    """The space menu music may take (menu_music_cap_gb; 0 or unreadable-as-zero: no limit)."""
    try:
        gb = float(_config.config_value(cfg, "menu_music_cap_gb") or 0)
    except (TypeError, ValueError, OverflowError):
        gb = _config.DEFAULT_CONFIG["menu_music_cap_gb"]
    return int(gb * (1 << 30)) if 0 < gb < 1e6 else 1 << 62


# ---------------------------------------------------------------- what the owner chose

def _music_ints(v):
    out = []
    for x in v if isinstance(v, list) else []:
        try:
            if int(x) not in out:
                out.append(int(x))
        except (TypeError, ValueError):
            pass
    return out


def music_mix(m):
    """A saved mix as the app keeps it: {"name", "on", "filters": {facet: [values], "max_minutes"}},
    or None for something that isn't one."""
    if not isinstance(m, dict) or not isinstance(m.get("name"), str) or not m["name"].strip():
        return None
    f, out = m.get("filters") if isinstance(m.get("filters"), dict) else {}, {}
    for k in MUSIC_MIX_FACETS:
        v = f.get(k) if isinstance(f.get(k), list) else []
        v = _music_ints(v) if k in ("platform", "decade") else [x for x in dict.fromkeys(v) if isinstance(x, str) and x]
        if v:
            out[k] = v
    try:
        if float(f.get("max_minutes") or 0) > 0:
            out["max_minutes"] = float(f["max_minutes"])
    except (TypeError, ValueError):
        pass
    return {"name": m["name"].strip(), "on": bool(m.get("on")), "filters": out}


def load_music_sources():
    """What feeds the menu music: {"favourites": bool, "on_box": bool, "playlists": [ids],
    "games": [rom ids], "mixes": [mix]}. Nothing chosen when the file isn't there or can't be read."""
    try:
        with open(music_sources_path(), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    if not isinstance(d, dict):
        d = {}
    mixes, names = [], set()
    for m in d.get("mixes") if isinstance(d.get("mixes"), list) else []:
        m = music_mix(m)
        if m and m["name"].lower() not in names:
            names.add(m["name"].lower())
            mixes.append(m)
    return {"favourites": bool(d.get("favourites")), "on_box": bool(d.get("on_box")),
            "playlists": _music_ints(d.get("playlists")), "games": _music_ints(d.get("games")), "mixes": mixes}


def save_music_sources(sel):
    os.makedirs(_core.DATA_DIR, exist_ok=True)
    _core.atomic_write_json(music_sources_path(), sel, indent=1)


# ---------------------------------------------------------------- sources

@dataclass(frozen=True)
class MusicSource:
    """One kind of source. keys(sel): the source keys switched on ("favourites",
    "playlist:3", ...); tracks(api, key, ctx): that key's tracks, RomM's track records.
    ctx: {"sel", "library", "cancel"}."""
    kind: str
    keys: Callable
    tracks: Callable


def _music_on_box(api, key, ctx):
    """The soundtracks of the games installed here: RomM's list of games that have one,
    narrowed to the ones on the box."""
    here = set()
    for rid in ctx["library"].snapshot() if ctx.get("library") is not None else ():
        try:
            here.add(int(rid))
        except (TypeError, ValueError):
            pass
    if not here:
        return []
    games = api.music_all("games", cancel=ctx.get("cancel"))
    out = []
    for g in games or []:
        if g.get("rom_id") in here:
            out += api.music_all("tracks", cancel=ctx.get("cancel"), rom_id=g["rom_id"]) or []
    return out


def _music_mix_of(sel, key):
    name = key.partition(":")[2]
    return next((m for m in sel["mixes"] if m["name"] == name), None)


MUSIC_SOURCES = (
    MusicSource("favourites", lambda sel: ["favourites"] if sel["favourites"] else [],
                lambda api, key, ctx: api.music_all("favorites", cancel=ctx.get("cancel"))),
    MusicSource("on_box", lambda sel: ["on_box"] if sel["on_box"] else [], _music_on_box),
    MusicSource("playlist", lambda sel: ["playlist:%d" % i for i in sel["playlists"]],
                lambda api, key, ctx: api.music_all("playlists/%s/tracks" % key.partition(":")[2], cancel=ctx.get("cancel"))),
    MusicSource("game", lambda sel: ["game:%d" % i for i in sel["games"]],
                lambda api, key, ctx: api.music_all("tracks", cancel=ctx.get("cancel"), rom_id=int(key.partition(":")[2]))),
    MusicSource("mix", lambda sel: ["mix:" + m["name"] for m in sel["mixes"] if m["on"]],
                lambda api, key, ctx: music_mix_tracks(api, (_music_mix_of(ctx["sel"], key) or {}).get("filters") or {},
                                                       ctx.get("cancel"))),
)


def music_source_keys(sel):
    """Every source switched on, as (key, MusicSource), in MUSIC_SOURCES' order."""
    return [(k, src) for src in MUSIC_SOURCES for k in src.keys(sel)]


def music_mix_takes(filters, t):
    """A track fits a mix: one of the picked values in every facet that has any (a game
    counts for each of its genres), and no longer than the mix allows."""
    f = filters
    if f.get("artist") and t.get("artist") not in f["artist"]:
        return False
    if f.get("genre") and t.get("genre") not in f["genre"]:
        return False
    if f.get("game_genre") and not set(f["game_genre"]) & set(t.get("game_genres") or ()):
        return False
    if f.get("platform") and t.get("platform_id") not in f["platform"]:
        return False
    if f.get("decade") and not (isinstance(t.get("year"), int) and t["year"] // 10 * 10 in f["decade"]):
        return False
    if f.get("max_minutes") and not (isinstance(t.get("duration_seconds"), (int, float))
                                     and t["duration_seconds"] <= f["max_minutes"] * 60):
        return False
    return True


def music_mix_tracks(api, filters, cancel=None):
    """A mix's tracks. RomM filters by one value of a facet at a time, so the first facet
    with values picked is asked value by value (platforms and the length go along with
    each call) and what comes back is checked against the whole mix here."""
    f = filters or {}
    base = {}
    if f.get("platform"):
        base["platform_ids"] = list(f["platform"])
    if f.get("max_minutes"):
        base["max_duration"] = f["max_minutes"] * 60
    calls = [{}]
    for facet in ("artist", "genre", "game_genre"):
        if f.get(facet):
            calls = [{facet: v} for v in f[facet]]
            break
    else:
        if f.get("decade"):
            calls = [{"min_year": d, "max_year": d + 9} for d in f["decade"]]
    out = {}
    for c in calls:
        got = api.music_all("tracks", cancel=cancel, **dict(base, **c))
        if got is None:
            return None
        for t in got:
            if music_mix_takes(f, t):
                out[t.get("rom_file_id")] = t
    return list(out.values())


def music_wanted(api, sel, library=None, cancel=None):
    """{rom_file_id: track} over every source switched on; each track carries "_sources",
    the keys that asked for it. None if cancel was set. RommError when RomM can't be asked."""
    ctx = {"sel": sel, "library": library, "cancel": cancel}
    out = {}
    for key, src in music_source_keys(sel):
        got = src.tracks(api, key, ctx)
        if got is None or (cancel is not None and cancel.is_set()):
            return None
        for t in got:
            fid = t.get("rom_file_id")
            if not isinstance(fid, int):
                continue
            kept = out.setdefault(fid, dict(t, _sources=[]))
            if key not in kept["_sources"]:
                kept["_sources"].append(key)
    return out


# ---------------------------------------------------------------- the files

def music_playable(t):
    return os.path.splitext(t.get("file_name") or "")[1].lower() in MUSIC_PLAYABLE


def music_file_name(t, taken=()):
    """The name a track gets in the music folder: "<Album> - <NN> <Title>.<ext>". ES shows
    an MP3's own title and the file name of anything else, so it's written to be read.
    taken: names (lower case) already used: a second track of the same name gets its id."""
    ext = os.path.splitext(t.get("file_name") or "")[1].lower()
    album = t.get("album") or t.get("game_name") or "Soundtrack"
    title = t.get("title") or os.path.splitext(t.get("file_name") or "")[0] or "Track"
    n = t.get("track")
    base = "%s - %s%s" % (album, "%02d " % n if isinstance(n, int) else "", title)
    base = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "-", base).strip(" .") or "Track"
    base = base.encode("utf-8")[:180].decode("utf-8", "ignore")       # with its ending, inside a 255-byte name
    name = base + ext
    if name.lower() in taken:
        name = "%s (%s)%s" % (base, t.get("rom_file_id"), ext)
    return name


def load_music_state():
    """{"tracks": {rom_file_id (str): {"file", "bytes", "seconds", "title", "album",
    "artist", "rom_id", "sources"}}, "last": the last sync's result}."""
    try:
        with open(music_state_path(), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    tracks = d.get("tracks") if isinstance(d, dict) and isinstance(d.get("tracks"), dict) else {}
    tracks = {k: v for k, v in tracks.items() if isinstance(v, dict) and isinstance(v.get("file"), str) and v["file"]}
    return {"tracks": tracks, "last": d.get("last") if isinstance(d, dict) and isinstance(d.get("last"), dict) else {}}


def _save_music_state(state):
    os.makedirs(_core.DATA_DIR, exist_ok=True)
    _core.atomic_write_json(music_state_path(), state, indent=1)


def _music_placed_path(rec):
    """Where a placed track is: always a plain file name inside the app's own folder."""
    return os.path.join(music_dir(), os.path.basename(rec["file"]))


def music_status():
    """{"tracks": how many are in place, "bytes", "last": the last sync's result}."""
    st = load_music_state()
    return {"tracks": len(st["tracks"]), "bytes": sum(int(r.get("bytes") or 0) for r in st["tracks"].values()),
            "last": st["last"]}


def music_rate(placed):
    """Bytes a second of the tracks already here (what the next ones will be about), else MUSIC_RATE."""
    known = [(r.get("bytes") or 0, r.get("seconds") or 0) for r in placed.values()]
    known = [(b, s) for b, s in known if b > 0 and s > 0]
    if len(known) < 5:
        return MUSIC_RATE
    return sum(b for b, _ in known) / sum(s for _, s in known)


def music_plan(wanted, placed, cap):
    """Which of the wanted tracks fit under cap bytes: (ids in the order they're taken, how
    many were left out). A track here already counts its real size, another its length times
    music_rate(). Past the cap, tracks are taken one from each album in turn, so every album
    keeps some."""
    rate = music_rate(placed)

    def size(fid, t):
        rec = placed.get(str(fid))
        if rec and rec.get("bytes"):
            return int(rec["bytes"])
        secs = t.get("duration_seconds")
        return int((secs if isinstance(secs, (int, float)) and secs > 0 else MUSIC_UNKNOWN_SECONDS) * rate)
    albums = {}
    for fid, t in wanted.items():
        albums.setdefault(((t.get("album") or t.get("game_name") or "").lower(), t.get("rom_id") or 0), []).append((fid, t))
    rows = [sorted(v, key=lambda x: (x[1].get("disc") or 0, x[1].get("track") or 0, x[0])) for _, v in sorted(albums.items())]
    order = [row[i] for i in range(max((len(r) for r in rows), default=0)) for row in rows if i < len(row)]
    keep, used = [], 0
    for fid, t in order:
        n = size(fid, t)
        if used + n > cap:
            continue                      # a shorter one further on may still fit
        used += n
        keep.append(fid)
    return keep, len(order) - len(keep)


def sync_music(api, sel, library=None, on=True, cap=10 << 30, keep_free=0, progress=None, cancel=None):
    """Make the music folder hold what the sources add up to: fetch what's missing, remove
    what's no longer wanted. on False: remove everything the app placed. Returns the result
    (also kept as music_status()["last"]): {"on", "wanted", "placed", "bytes", "downloaded",
    "removed", "left_out", "skipped", "failed", "no_room", "stopped", "error", "when"}; {"busy":
    True} when a sync is already running. RomM not answering changes nothing here.
    progress(done, total) after each download."""
    if not _MUSIC_BUSY.acquire(False):
        return {"busy": True}
    try:
        os.makedirs(_core.DATA_DIR, exist_ok=True)
        with _core.try_lock(os.path.join(_core.DATA_DIR, "music.lock")) as fd:
            if fd is None:                      # another copy of the app
                return {"busy": True}
            return _sync_music(api, sel, library, on, cap, keep_free, progress, cancel)
    finally:
        _MUSIC_BUSY.release()


def _sync_music(api, sel, library, on, cap, keep_free, progress, cancel):
    state = load_music_state()
    placed = state["tracks"]
    res = {"on": bool(on), "wanted": 0, "placed": 0, "bytes": 0, "downloaded": 0, "removed": 0, "left_out": 0,
           "skipped": 0, "failed": 0, "no_room": False, "stopped": False, "error": "", "when": int(time.time())}

    def done():
        res["placed"] = len(placed)
        res["bytes"] = sum(int(r.get("bytes") or 0) for r in placed.values())
        state["last"] = res
        _save_music_state(state)
        return res

    wanted = {}
    if on:
        try:
            wanted = music_wanted(api, sel, library, cancel)
        except _core.RommError as e:
            res["error"] = str(e)               # what's here stays and keeps playing
            return done()
        if wanted is None:
            res["stopped"] = True
            return done()
    res["wanted"] = len(wanted)
    playable = {fid: t for fid, t in wanted.items() if music_playable(t)}
    res["skipped"] = len(wanted) - len(playable)
    keep, res["left_out"] = music_plan(playable, placed, cap)
    keeping = {str(fid) for fid in keep}
    folder = music_dir()
    for fid in list(placed):
        path = _music_placed_path(placed[fid])
        if fid in keeping and os.path.isfile(path):
            placed[fid]["sources"] = list(playable[int(fid)]["_sources"])
            continue
        if fid not in keeping:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            except OSError as e:
                _core.log("music: couldn't remove %s (%s); it stays listed" % (path, e.strerror or e))
                continue
            res["removed"] += 1
        del placed[fid]                         # (a kept one whose file is gone is fetched again)
    _save_music_state(state)
    todo = [fid for fid in keep if str(fid) not in placed]
    taken = {os.path.basename(r["file"]).lower() for r in placed.values()}
    rate, fails = music_rate(placed), 0
    for i, fid in enumerate(todo):
        if cancel is not None and cancel.is_set():
            res["stopped"] = True
            break
        t = playable[fid]
        secs = t.get("duration_seconds")
        guess = int((secs if isinstance(secs, (int, float)) and secs > 0 else MUSIC_UNKNOWN_SECONDS) * rate)
        os.makedirs(folder, exist_ok=True)
        if _core.free_bytes(folder) - guess < keep_free:
            res["no_room"] = True
            break
        name = music_file_name(t, taken)
        path = os.path.join(folder, name)
        if os.path.lexists(path):               # a file of that name the app didn't place: never written over
            name = music_file_name(t, taken | {name.lower()})
            path = os.path.join(folder, name)
        try:
            if os.path.lexists(path):
                raise _core.RommError("a file of that name is already there")
            size = api.download_music(t, path + ".part", MUSIC_FILE_MAX)     # ES takes no ".part" for a song
            os.replace(path + ".part", path)
        except (_core.RommError, OSError) as e:
            res["failed"] += 1
            res["error"] = str(e) if isinstance(e, _core.RommError) else "Couldn't write to the music folder (%s)" % (e.strerror or e)
            fails += 1
            _core.log("music: %s not fetched (%s)" % (name, res["error"]))
            if fails >= MUSIC_FAILS or isinstance(e, (_core.AuthRejected, OSError)):
                break
            continue
        fails = 0
        taken.add(name.lower())
        placed[str(fid)] = {"file": name, "bytes": size, "seconds": secs if isinstance(secs, (int, float)) else 0,
                            "title": t.get("title") or "", "album": t.get("album") or t.get("game_name") or "",
                            "artist": t.get("artist") or "", "rom_id": t.get("rom_id"), "sources": list(t["_sources"])}
        res["downloaded"] += 1
        _save_music_state(state)
        if progress:
            progress(i + 1, len(todo))
    if not placed:
        try:
            os.rmdir(folder)                    # only when empty: nothing of anyone's is in it
        except OSError:
            pass
    return done()
