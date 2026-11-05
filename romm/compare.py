"""What's on the box against RomM: Compare per platform or collection, which
backlog games fit now, and how often a game was played here."""
import hashlib
import os
import re
import time
import zipfile
import zlib

from . import (core as _core, config as _config, es as _es, convert as _convert, library as _library,
               downloader as _downloader, unseen as _unseen, api as _api, audit as _audit)


# ------------------------------------------------------------ Compare with RomM

MATCHES, COMPRESSED, NEWER, DIFFERS, NOT_IN_ROMM = "matches", "compressed", "newer", "differs", "not_in_romm"
COMPARE_LABELS = {MATCHES: "Matches RomM", COMPRESSED: "Matches RomM (compressed)",
                  NEWER: "Newer in RomM", DIFFERS: "Differs from RomM's", NOT_IN_ROMM: "Not in RomM"}
_RANK = {MATCHES: 0, COMPRESSED: 1, DIFFERS: 2, NEWER: 3}        # a set of discs shows its worst
# folders named like a file ("Game.ps3"): the only folder "extensions" that aren't part of the name
_FOLDER_EXTS = {".ps3", ".xbox360", ".wiiu", ".pc", ".psvita", ".daphne", ".scummvm", ".squashfs"}
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
    ("Game v1.2" and "Dr. Mario (USA)" have no extension), unless it ends in a
    game-folder one (X.ps3)."""
    low = name.lower()
    if is_dir:
        ext = _lower_ext(low)
        return low[:-len(ext)] if ext in _FOLDER_EXTS else low
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
            if is_dir and (name in _core.MEDIA_DIRS or not _library.holds_game(e.path, system)):
                continue                                  # a folder of notes or a manual: not a game
            if not is_dir and low.endswith(skip_ext):
                continue
            st = e.stat()
            size = None if is_dir else st.st_size
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
    main = _primary([p for p in paths if _lower_ext(p) != ".m3u"] or paths)
    ext = _lower_ext(main)
    if os.path.isdir(main) and ext not in _library.FOLDER_GAME_EXTS:
        ext = ""                                 # "Lorem 1.1 (USA)/": a folder, not a ".1 (usa)" file
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


def _cut(rom, g):
    """The box's copy is one file in RomM's format at a size RomM's file isn't: not the file
    RomM has, as it is now (cut short, or changed since it came here)."""
    want = rom.get("fs_size_bytes")
    return bool(len(g["paths"]) == 1 and g["size"] and want and g["size"] != want
                and _lower_ext(g["path"]) == _rom_ext(rom))


# the hashes RomM keeps of a game's file, checked first to last (a zip's are its member's)
_HASHES = (("sha1_hash", "sha1"), ("md5_hash", "md5"), ("crc_hash", "crc"))


def _same_bytes(path, roms):
    """The first of roms (RomM's games at this file's size) a hash RomM keeps says is this
    very file, or None. Reads the file once (a single-member zip: its member's CRC from the
    zip's directory when RomM keeps a crc, else the member); a zip of several files can't be
    told (RomM's hash is of them all together)."""
    def got(algo):
        if algo not in cache:
            cache[algo] = _digest(path, algo)
        return cache[algo]
    cache = {}
    for r in roms:
        for field, algo in _HASHES:
            want = (r.get(field) or "").lower()
            if want:
                have = got(algo)
                if have is not None and have == (want.zfill(8) if algo == "crc" else want):
                    return r
                break                                # the best hash RomM keeps decides
    return None


def _digest(path, algo):
    """A file's hash as RomM keeps it (hex; a crc 8 digits), or None when it can't be read
    or told (a zip of several files, an archive RomM hashes by what's inside)."""
    low = path.lower()
    try:
        if low.endswith(".zip"):
            with zipfile.ZipFile(path) as z:
                infos = [i for i in z.infolist() if not i.is_dir()]
                if len(infos) != 1:
                    return None
                if algo == "crc":
                    return "%08x" % infos[0].CRC
                with z.open(infos[0]) as fh:
                    return _hash_stream(fh, algo)
        if low.endswith(_downloader.CONTENT_HASHED):
            return None
        with open(path, "rb") as fh:
            return _hash_stream(fh, algo)
    except (OSError, zipfile.BadZipFile, NotImplementedError, RuntimeError, EOFError, zlib.error):
        return None


def _hash_stream(fh, algo):
    if algo == "crc":
        c = 0
        for b in iter(lambda: fh.read(1 << 22), b""):
            c = zlib.crc32(b, c)
        return "%08x" % c
    h = hashlib.new(algo)
    for b in iter(lambda: fh.read(1 << 22), b""):
        h.update(b)
    return h.hexdigest()


def _compressed(rom, paths, outs):
    """RomM's game compressed here: a converted format RomM's file isn't in, whose
    CHDs are whole ones (a header read: random bytes or a cut-short CHD named like
    the game differ from RomM's, never match it)."""
    exts = {_lower_ext(p) for p in paths} - {".m3u"}
    return bool(exts & outs) and _rom_ext(rom) not in exts and not any(
        _lower_ext(p) == ".chd" and os.path.isfile(p) and _audit.chd_header_problem(p) for p in paths)


def _bad_conversion(rom, paths, outs):
    """In a converted format RomM's file isn't in, but not a whole one (_compressed)."""
    exts = {_lower_ext(p) for p in paths} - {".m3u"}
    return bool(exts & outs) and _rom_ext(rom) not in exts and not _compressed(rom, paths, outs)


def compare_games(roms, records, local, system, scope="platform", cancel=None):
    """Each game on the box against RomM's games (quick on 10,000-game
    platforms: no file is read, but for the hash below). roms: RomM's games of the platform(s) or collection (list
    records, COMPARE_FIELDS is enough); records: Library.snapshot();
    local: local_games(system). A game the app installed is matched by its
    record, any other by file name, or else (one file at the size of a RomM
    game no other copy here is) by a hash RomM keeps: "renamed" on the item,
    the one case that reads a file. Statuses: "matches"; "compressed" (the
    app converted RomM's file: .chd from RomM's .zip/.cue); "newer" (RomM's
    file changed since the app installed it: size or hash); "differs" (one file
    in RomM's format at another size than RomM's: one the app installed was cut
    or changed since; for one it didn't, which is newer can't be told, RomM's
    dates change on any edit and the file's is the copy's); "not_in_romm" (no
    RomM game for it). scope="collection": games that aren't in it are left
    out instead of "not_in_romm". cancel: stops hashing (the rest are "not_in_romm").
    Returns {"items": [{"status", "label",
    "name", "path", "paths", "size", "renamed" (True: RomM's file under another
    name, matched by its hash), "rom" (RomM's record, of the
    worst-off disc for a set, or None), "rom_ids", "parts": [{"rom", "status", "paths"
    (the files of that RomM game alone: a record's own, never another record's
    or another disc's, nor a set's playlist)}]}], by name, "counts": {status: n}}."""
    sysdir = os.path.join(_core.ROMS_ROOT, system)
    by_id, by_name = {}, {}
    for r in roms:
        if r.get("id") is None:
            continue
        by_id[str(r["id"])] = r
        for n in (r.get("fs_name"), r.get("fs_name_no_ext")):
            if n:
                cands = by_name.setdefault(n.lower(), [])
                if r not in cands:
                    cands.append(r)
    outs = compressed_exts(system)
    owner = {}
    for g in local:
        for p in g["paths"]:
            owner[p] = g
    found = {}                                          # id(game) -> item

    def put(g, status, rom, paths=None):
        it = found.setdefault(id(g), {"status": None, "game": g, "parts": []})
        if rom:
            part = next((x for x in it["parts"] if x["rom"]["id"] == rom["id"]), None)
            if part is None:
                it["parts"].append({"rom": rom, "status": status, "paths": list(g["paths"] if paths is None else paths)})
            else:
                if _RANK[status] > _RANK[part["status"]]:
                    part["status"] = status
                part["paths"] += [p for p in paths or () if p not in part["paths"]]
        if status == NOT_IN_ROMM:
            it["status"] = it["status"] or NOT_IN_ROMM
        elif it["status"] in (None, NOT_IN_ROMM) or _RANK[status] > _RANK[it["status"]]:
            it["status"] = status

    def by_names(g):
        """RomM's game for a local one by file name. Two platforms mapped to one
        system can both have the name: the one whose size agrees wins."""
        cands = []
        for p in g["paths"]:
            b = os.path.basename(p).lower()
            for n in (b, _core.stem_of(b)):
                for r in by_name.get(n, ()):
                    if r not in cands:
                        cands.append(r)
        if len(cands) > 1:
            def fit(r):
                same = os.path.basename(g["path"]).lower() == (r.get("fs_name") or "").lower()
                return (bool(g["size"] and r.get("fs_size_bytes") == g["size"]), same)
            return max(cands, key=fit)           # max keeps the first of equals
        return cands[0] if cands else None

    def g_tops(g):
        return {_top(p, sysdir) for p in g["paths"]}

    claimed, claims = set(), {}
    for rid, rec in records.items():                    # how many records hold each file
        if isinstance(rec, dict) and rec.get("system") == system:
            for p in set(rec.get("paths") or []):
                claims[p] = claims.get(p, 0) + 1
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
            if rom is not None and str(rom["id"]) in records:
                rom = None                              # that game has a record of its own
        for g in games:
            if rom is None:
                if scope == "platform":
                    put(g, NOT_IN_ROMM, None)
                continue
            paths = list(rec.get("paths") or []) + g["paths"]
            status = NEWER if _changed(rom, rec) else COMPRESSED if _compressed(rom, paths, outs) else \
                DIFFERS if _cut(rom, g) or _bad_conversion(rom, paths, outs) else MATCHES
            # a disc of a set never owns the set's playlist (deleting it takes every disc)
            put(g, status, rom, [p for p in rec.get("paths") or []
                                 if _top(p, sysdir) in g_tops(g) and claims.get(p) == 1
                                 and not (rec.get("group") and _lower_ext(p) == ".m3u")])
    unnamed = []
    for g in local:
        if id(g) in claimed:
            continue
        rom = by_names(g)
        if rom is None or not all(_library.named_game(p, system) for p in g["paths"] if os.path.isdir(p)):
            unnamed.append(g)                   # (a folder named like RomM's game holding another one)
            continue
        if _compressed(rom, g["paths"], outs):
            status = COMPRESSED
        elif _bad_conversion(rom, g["paths"], outs):
            status = DIFFERS            # a damaged CHD (or random bytes) under RomM's game's name
        elif len(g["paths"]) == 1 and g["size"] and rom.get("fs_size_bytes") and \
                os.path.basename(g["path"]).lower() == (rom.get("fs_name") or "").lower() and \
                g["size"] != rom["fs_size_bytes"]:
            status = DIFFERS            # which one is newer can't be told: RomM's date changes on any edit, ours is the copy's
        else:
            status = MATCHES
        put(g, status, rom)
    # a copy under a name of its own: RomM's game at its size that nothing here is yet, told by its hash
    taken = {str(x["rom"]["id"]) for it in found.values() for x in it["parts"]} | \
        {str(rid) for rid, rec in records.items() if isinstance(rec, dict) and rec.get("system") == system
         and any(os.path.exists(p) for p in rec.get("paths") or [])}
    by_size = {}
    for r in by_id.values():
        if r.get("fs_size_bytes") and str(r["id"]) not in taken:
            by_size.setdefault(r["fs_size_bytes"], []).append(r)
    for g in unnamed:
        rom = None
        if len(g["paths"]) == 1 and g["size"] and by_size.get(g["size"]) and \
                not (cancel is not None and cancel.is_set()):
            rom = _same_bytes(g["path"], by_size[g["size"]])
        if rom is None:
            if scope == "platform":
                put(g, NOT_IN_ROMM, None)
            continue
        by_size[g["size"]].remove(rom)
        put(g, MATCHES, rom)
        found[id(g)]["renamed"] = True
    items = []
    counts = dict.fromkeys(COMPARE_LABELS, 0)
    for it in found.values():
        g, parts = it["game"], it["parts"]
        counts[it["status"]] += 1
        if len(parts) > 1:                             # each disc keeps only the files that are its own
            held = [list(x["paths"]) for x in parts]
            for i, x in enumerate(parts):
                others = {p for j, h in enumerate(held) if j != i for p in h}
                # never the set's playlist: it names (and deleting it takes) every disc
                x["paths"] = [p for p in held[i] if p not in others and _lower_ext(p) != ".m3u"]
        worst = max(parts, key=lambda x: _RANK[x["status"]], default=None)      # first of the worst
        items.append({"status": it["status"], "label": COMPARE_LABELS[it["status"]], "name": g["name"],
                      "path": g["path"], "paths": g["paths"], "size": g["size"], "renamed": bool(it.get("renamed")),
                      "rom": worst["rom"] if worst else None, "rom_ids": [x["rom"]["id"] for x in parts],
                      "parts": parts})
    items.sort(key=lambda it: it["name"].lower())
    return {"items": items, "counts": counts}


def recheck(item, library, system):
    """One row compared again (blocking: reads the folder): the item
    compare_games makes now for the same RomM games, or None when they are no
    longer here as one game. Run after "Get the newer one" finished."""
    roms = [x["rom"] for x in item.get("parts") or ()]
    if not roms:
        return None
    snap = library.snapshot()
    sysdir = os.path.join(_core.ROMS_ROOT, system)
    tops = set()
    for x in roms:
        tops |= {_top(p, sysdir) for p in (snap.get(str(x["id"])) or {}).get("paths") or []}
    local = [g for g in local_games(system) if tops & {_top(p, sysdir) for p in g["paths"]}]
    got = compare_games(roms, snap, local, system, scope="collection")["items"]
    return got[0] if len(got) == 1 else None


def compare_platform(api, library, system, platform_ids, cancel=None):
    """compare_games for one Batocera system against every RomM platform that
    maps to it (blocking: RomM's listing, PAGE_WORKERS pages at a time). None
    if cancel was set."""
    roms = api.roms_all(platform_ids=list(platform_ids), cancel=cancel, fields=COMPARE_FIELDS)
    if roms is None:
        return None
    return compare_games(roms, library.snapshot(), local_games(system), system, cancel=cancel)


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
        got = compare_games(rs, snap, local_games(s), s, scope="collection", cancel=cancel)
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
    reserve = _config.keep_free_bytes(cfg) or _downloader.space_margin(
        sum(r.get("fs_size_bytes") or 0 for r, _ in items))      # the downloader's own margin when keep-free is 0
    return backlog_fit(items, free, reserve, on_box)


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
