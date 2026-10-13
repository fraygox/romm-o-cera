"""What's on the box against RomM: Compare per platform or collection, which
backlog games fit now, and how often a game was played here."""
import os
import re
import time

from . import (core as _core, config as _config, es as _es, convert as _convert, library as _library,
               downloader as _downloader, unseen as _unseen, api as _api)


# ------------------------------------------------------------ Compare with RomM

MATCHES, COMPRESSED, NEWER, NOT_IN_ROMM = "matches", "compressed", "newer", "not_in_romm"
COMPARE_LABELS = {MATCHES: "Matches RomM", COMPRESSED: "Matches RomM (compressed)",
                  NEWER: "Newer in RomM", NOT_IN_ROMM: "Not in RomM"}
_RANK = {MATCHES: 0, COMPRESSED: 1, NEWER: 2}        # a set of discs shows its worst
# the RomM fields Compare reads (roms_all keeps only these: a big platform's
# full records are hundreds of MB)
COMPARE_FIELDS = ("id", "name", "fs_name", "fs_name_no_ext", "fs_extension", "fs_size_bytes", "sha1_hash",
                  "md5_hash", "crc_hash", "platform_id", "platform_slug", "path_cover_small", "path_cover_large", "updated_at")
_TRACK_TAG = re.compile(r"\s*\((?:track|disc track)\s*\d+\)$", re.I)
_TRACK_EXTS = {".bin", ".raw", ".wav", ".img", ".sub", ".sbi"}


def compressed_exts(system):
    """Extensions the app's converters make on this system (.chd, .rvz...)."""
    return {e for c in _convert.CONVERTERS if system in c.systems for e in c.outputs}


def _lower_ext(name):
    return os.path.splitext(name)[1].lower()


def format_label(ext):
    """A format as people say it: ".chd" -> "CHD"; no (or no short) extension -> "Folder"."""
    e = (ext or "").lstrip(".")
    return e.upper() if e and len(e) <= 5 else "Folder"


def path_format(path):
    """A file's format label (CHD, ZIP); a folder's "Folder"."""
    return format_label(_lower_ext(path or ""))


def rom_format(rom):
    """RomM's file format label (ZIP), or "Folder" for a game of several files."""
    return format_label(_rom_ext(rom))


def _key(name, is_dir):
    """Grouping key of a top-level entry: a file's stem; a folder's whole name
    ("Dr. Mario (USA)" has no extension), unless it ends in a short one (X.ps3)."""
    low = name.lower()
    if is_dir:
        ext = _lower_ext(low)
        return low[:-len(ext)] if ext and len(ext) <= 5 and ext[1:].isalnum() else low
    return _core.stem_of(low)


def local_games(system):
    """The games in roms/<system>, read from the folder (no hashing, folders
    not walked): [{"key", "name", "path", "paths", "size"}], one per game:
    files sharing a stem are one game (cue + bin), a playlist takes the discs
    it names, a sheet the "(Track N)" files beside it. path: the one ES would
    list (playlist, .chd... first); size: bytes, None when it holds a folder.
    Media folders, gamelist.xml, notes, saves and housekeeping files are skipped."""
    sysdir = os.path.join(_core.ROMS_ROOT, system)
    try:
        entries = list(os.scandir(sysdir))
    except OSError:
        return []
    groups = {}
    skip_ext = _unseen.UNSEEN_IGNORE + _unseen.UNSEEN_HOUSEKEEPING
    for e in entries:
        name, low = e.name, e.name.lower()
        if name.startswith(".") or low in ("gamelist.xml", "_info.txt") or ".bak" in low:
            continue
        try:
            is_dir = e.is_dir()
            if is_dir and name in _core.MEDIA_DIRS:
                continue
            if not is_dir and low.endswith(skip_ext):
                continue
            size = None if is_dir else e.stat().st_size
        except OSError:
            continue
        g = groups.setdefault(_key(name, is_dir), {"paths": [], "size": 0})
        g["paths"].append(os.path.join(sysdir, name))
        g["size"] = None if size is None or g["size"] is None else g["size"] + size
    sheets = {k for k, g in groups.items() if any(_lower_ext(p) in (".cue", ".gdi", ".ccd") for p in g["paths"])}
    for k in list(groups):
        g = groups.get(k)
        if g is None:
            continue
        m3us = [p for p in g["paths"] if p.lower().endswith(".m3u")]
        for m in m3us:                                    # the discs it names join it
            for d in _es.m3u_paths(m):
                dk = _key(os.path.basename(d), False)
                if os.path.dirname(os.path.normpath(d)) == sysdir and dk != k and dk in groups:
                    _merge(g, groups.pop(dk))
        base = _TRACK_TAG.sub("", k)
        if base != k and base in sheets and base in groups and \
                all(_lower_ext(p) in _TRACK_EXTS for p in g["paths"]):
            _merge(groups[base], groups.pop(k))
    out = []
    for k, g in groups.items():
        g["paths"].sort()
        path = _primary(g["paths"])
        out.append({"key": k, "name": os.path.basename(path), "path": path, "paths": g["paths"],
                    "size": g["size"]})
    return out


def _merge(into, g):
    into["paths"] += g["paths"]
    into["size"] = None if into["size"] is None or g["size"] is None else into["size"] + g["size"]


def _primary(paths):
    order = _library.PRIMARY_ORDER

    def rank(p):
        e = _lower_ext(p)
        return (order.index(e) if e in order else len(order), e in _TRACK_EXTS, p)
    return min(paths, key=rank)


def box_copy(library, rom, system):
    """The box's copy of a game: {"paths", "size" (bytes), "format" ("CHD", "ISO"... from its
    main file; "folder" for one without an extension)}, or None when it isn't here. Blocking:
    sizes folders."""
    paths = library.paths_for(rom, system)
    if not paths:
        return None
    ext = _lower_ext(_primary([p for p in paths if _lower_ext(p) != ".m3u"] or paths))
    return {"paths": paths, "size": sum(_core.path_size(p) for p in paths),
            "format": ext[1:].upper() if ext else "folder"}


def _top(path, sysdir):
    """The top-level entry of roms/<system> a path is or lies in, or None."""
    rel = os.path.relpath(os.path.normpath(path), sysdir)
    if rel.startswith("..") or rel == ".":
        return None
    return os.path.join(sysdir, rel.split(os.sep)[0])


def _rom_ext(rom):
    ext = (rom.get("fs_extension") or "").lower()
    return "." + ext if ext else _lower_ext(rom.get("fs_name") or "")


def _changed(rom, rec):
    """RomM's file isn't the one this record installed: its size or a hash
    changed (records from before the stamp: its file name changed)."""
    stamp = rec.get("romm")
    if isinstance(stamp, dict) and stamp:
        for field, k in _library.STAMP_KEYS:
            old, new = stamp.get(k), rom.get(field)
            if old and new and str(old).lower() != str(new).lower():
                return True
        return False
    old, new = rec.get("fs_name"), rom.get("fs_name")
    return bool(old and new and old != new)


def _compressed(rom, paths, outs):
    exts = {_lower_ext(p) for p in paths} - {".m3u"}
    return bool(exts & outs) and _rom_ext(rom) not in exts


def compare_games(roms, records, local, system, scope="platform"):
    """Each game on the box against RomM's games (pure; quick on 10,000-game
    platforms). roms: RomM's games of the platform(s) or collection (list
    records, COMPARE_FIELDS is enough); records: Library.snapshot();
    local: local_games(system). A game the app installed is matched by its
    record, any other by file name. Statuses: "matches"; "compressed" (the
    app converted RomM's file: .chd from RomM's .zip/.cue); "newer" (RomM's
    file changed since it was installed: size or hash, or for a game the app
    didn't install, the same file name at another size); "not_in_romm" (no
    RomM game for it). scope="collection": games that aren't in it are left
    out instead of "not_in_romm". Returns {"items": [{"status", "label",
    "name", "path", "paths", "size", "rom" (RomM's record or None),
    "rom_ids"}], by name, "counts": {status: n}}."""
    sysdir = os.path.join(_core.ROMS_ROOT, system)
    by_id, by_name = {}, {}
    for r in roms:
        if r.get("id") is None:
            continue
        by_id[str(r["id"])] = r
        for n in (r.get("fs_name"), r.get("fs_name_no_ext")):
            if n:
                by_name.setdefault(n.lower(), r)
    outs = compressed_exts(system)
    owner = {}
    for g in local:
        for p in g["paths"]:
            owner[p] = g
    found = {}                                          # id(game) -> item

    def put(g, status, rom):
        it = found.setdefault(id(g), {"status": None, "game": g, "rom": None, "rom_ids": []})
        if rom:
            if rom["id"] not in it["rom_ids"]:
                it["rom_ids"].append(rom["id"])
            it["rom"] = it["rom"] or rom
        if status == NOT_IN_ROMM:
            it["status"] = it["status"] or NOT_IN_ROMM
        elif it["status"] in (None, NOT_IN_ROMM) or _RANK[status] > _RANK[it["status"]]:
            it["status"] = status

    def by_names(g):
        for p in g["paths"]:
            b = os.path.basename(p).lower()
            for n in (b, _core.stem_of(b)):
                if n in by_name:
                    return by_name[n]
        return None

    claimed = set()
    for rid, rec in records.items():
        if not isinstance(rec, dict) or rec.get("system") != system:
            continue
        tops = {_top(p, sysdir) for p in rec.get("paths") or []}
        games = list({id(owner[t]): owner[t] for t in tops if t in owner}.values())
        if not games:
            continue                                    # not on the box any more
        claimed.update(id(g) for g in games)
        rom = by_id.get(str(rid))
        if rom is None and scope == "platform":
            rom = by_names(games[0])
        for g in games:
            if rom is None:
                if scope == "platform":
                    put(g, NOT_IN_ROMM, None)
                continue
            paths = list(rec.get("paths") or []) + g["paths"]
            status = NEWER if _changed(rom, rec) else COMPRESSED if _compressed(rom, paths, outs) else MATCHES
            put(g, status, rom)
    for g in local:
        if id(g) in claimed:
            continue
        rom = by_names(g)
        if rom is None:
            if scope == "platform":
                put(g, NOT_IN_ROMM, None)
            continue
        if _compressed(rom, g["paths"], outs):
            status = COMPRESSED
        elif len(g["paths"]) == 1 and g["size"] and rom.get("fs_size_bytes") and \
                os.path.basename(g["path"]).lower() == (rom.get("fs_name") or "").lower() and \
                g["size"] != rom["fs_size_bytes"]:
            status = NEWER
        else:
            status = MATCHES
        put(g, status, rom)
    items = []
    counts = dict.fromkeys(COMPARE_LABELS, 0)
    for it in found.values():
        g = it["game"]
        counts[it["status"]] += 1
        items.append({"status": it["status"], "label": COMPARE_LABELS[it["status"]], "name": g["name"],
                      "path": g["path"], "paths": g["paths"], "size": g["size"], "rom": it["rom"],
                      "rom_ids": it["rom_ids"]})
    items.sort(key=lambda it: it["name"].lower())
    return {"items": items, "counts": counts}


def compare_platform(api, library, system, platform_ids, cancel=None):
    """compare_games for one Batocera system against every RomM platform that
    maps to it (blocking: RomM's listing, PAGE_WORKERS pages at a time). None
    if cancel was set."""
    roms = api.roms_all(platform_ids=list(platform_ids), cancel=cancel, fields=COMPARE_FIELDS)
    if roms is None:
        return None
    return compare_games(roms, library.snapshot(), local_games(system), system)


def compare_collection(api, library, collection, system_of, cancel=None):
    """compare_games for the games of a collection ((param, id) as
    Api.roms() takes) that are on the box, across its systems. system_of(rom):
    the Batocera system a RomM game goes to (None: not on this box). Same
    shape as compare_games; None if cancel was set."""
    roms = api.roms_all(collection=collection, cancel=cancel, fields=COMPARE_FIELDS)
    if roms is None:
        return None
    by_system = {}
    for r in roms:
        s = system_of(r)
        if s:
            by_system.setdefault(s, []).append(r)
    snap = library.snapshot()
    out = {"items": [], "counts": dict.fromkeys(COMPARE_LABELS, 0)}
    for s, rs in sorted(by_system.items()):
        if cancel is not None and cancel.is_set():
            return None
        got = compare_games(rs, snap, local_games(s), s, scope="collection")
        out["items"] += got["items"]
        for k, n in got["counts"].items():
            out["counts"][k] += n
    out["items"].sort(key=lambda it: it["name"].lower())
    return out


# ------------------------------------------------------------ backlog fit

def backlog_fit(items, free, reserve=_core.SPACE_MARGIN, on_box=None):
    """Which games of a list [(rom, system)] in RomM's order fit in `free`
    bytes now, keeping `reserve` free, and which wait: each taken in list
    order when it still fits (as fit_in_space counts a batch: unpacked sizes
    plus the biggest archive beside them). on_box(rom, system): True leaves a
    game out (already here); a game without a system is left out too.
    Returns {"fit": [(rom, system)], "wait": [...], "skipped": [...], "need":
    bytes the fitting ones take, "free": free}."""
    todo, skipped = [], []
    for rom, system in items:
        (todo if system and not (on_box and on_box(rom, system)) else skipped).append((rom, system))
    fit, need = _downloader.fit_in_space(todo, free, reserve)
    taken = {id(r) for r, _ in fit}
    return {"fit": fit, "wait": [(r, s) for r, s in todo if id(r) not in taken], "skipped": skipped,
            "need": need, "free": free}


def backlog_fit_listed(api, items, cfg=None, free=None, on_box=None, cancel=None):
    """backlog_fit with exact sizes: the archives RomM lists no members for
    are read from the server first (zip_listing, a few small ranged reads
    each, LOOKUP_WORKERS at a time; one that can't be read keeps the
    estimate). free: the roms folder's free space when None; the reserve is
    Settings' keep-free (or a small margin). Blocking. None if cancel was set."""
    want = [(r, s) for r, s in items if s and not (on_box and on_box(r, s)) and _downloader.needs_listing(r, s)]

    def read(i):
        rom, system = want[i]
        try:
            _downloader.zip_listing(api, rom, system)
        except _downloader.NetError:
            pass
    _api.fetch_many(read, range(len(want)), cancel=cancel)
    if cancel is not None and cancel.is_set():
        return None
    free = _core.free_bytes(_core.ROMS_ROOT) if free is None else free
    return backlog_fit(items, free, _config.keep_free_bytes(cfg) or _core.SPACE_MARGIN, on_box)


# ------------------------------------------------------------ played here

PLAY_STATS_AGE = 30          # seconds ES's play counts are reused for
_PLAY = {}


def play_stats(system, max_age=PLAY_STATS_AGE):
    """ES's play records for a system by top-level entry of roms/<system>:
    {path: [_played records]} (a game folder holds the file ES lists), None
    when ES can't be asked. Asked again after max_age (an unreachable ES too:
    each try can take ES's timeout)."""
    now = time.monotonic()
    got = _PLAY.get(system)
    if got and now - got[0] < max_age:
        return got[1]
    stats = _es.es_play_stats(system)
    index = None
    if stats is not None:
        sysdir = os.path.join(_core.ROMS_ROOT, system)
        index = {}
        for p, rec in stats.items():
            t = _top(p, sysdir)
            if t:
                index.setdefault(t, []).append(rec)
    if len(_PLAY) > 64:
        _PLAY.clear()
    _PLAY[system] = (now, index)
    return index


def played_here(library, rom, system, stats=None):
    """How much a game on the box was played here, from EmulationStation:
    {"playcount", "lastplayed" (ES's "YYYYmmddTHHMMSS" or ""), "last_played"
    (epoch seconds or None), "gametime" (seconds), "es": True}, its files
    joined to ES's games by path (a set's playlist, a game folder's file).
    Not played: zeros. ES can't be asked: the same zeros with "es": False.
    None when the game isn't on the box. stats: play_stats(system)."""
    sysdir = os.path.join(_core.ROMS_ROOT, system)
    tops = {_top(p, sysdir) for p in library.paths_for(rom, system)} - {None}
    if not tops:
        return None
    stats = play_stats(system) if stats is None else stats
    out = {"playcount": 0, "lastplayed": "", "last_played": None, "gametime": 0, "es": stats is not None}
    for t in sorted(tops):
        for rec in (stats or {}).get(t, ()):
            out["playcount"] += rec["playcount"]
            out["gametime"] += rec["gametime"]
            out["lastplayed"] = max(out["lastplayed"], rec["lastplayed"])
    out["last_played"] = _es.lastplayed_seconds(out["lastplayed"]) if out["lastplayed"] else None
    return out
