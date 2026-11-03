"""Check games: compare what's here with RomM; game info for games already here."""
import errno
import hashlib
import json
import os
import re
import subprocess
import time
import traceback
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
import zlib

from . import (core as _core, discs as _discs, es as _es, api as _api, convert as _convert, library as _library,
               downloader as _downloader)


def _guess_unpacked(detail, system):
    """An archive RomM listed no member sizes for: a Wii U disc image's fixed
    size, else the rough unpacking estimate."""
    inner = _downloader.unpacked_size(detail, system)
    return inner if inner is not None else _downloader.space_needed(detail, system)


def disc_layout(detail, system, cfg, accepts=None):
    """What a download of this game is and becomes, from RomM's full record
    (files and archive members, or a listing read from the server):
    {"label": "CUE/BIN, 3 tracks", "discs": n, "unpacked": bytes or None,
    "estimated": unpacked is a guess, "upto": the guess is only the most it
    can be (a Wii U zip counted as a whole disc),
    "convert": "CHD" | "RVZ" | "squashfs" | "ZCCI" | "ZAR" | "WUA" | "WUX" | None}. None for a
    game that isn't a disc image (carts), or when RomM listed no files."""
    files = (detail or {}).get("files") or []
    if not files:
        return None
    members, sizes_known, archived = [], True, False
    listed = _downloader._listing(detail) or {}           # read from the server (zip_listing)
    arcs = [f for f in files if os.path.splitext(f.get("file_name") or "")[1].lower() in _core.ARCHIVE_EXTS]
    for f in files:
        name = f.get("file_name") or ""
        if os.path.splitext(name)[1].lower() in _core.ARCHIVE_EXTS and system not in _core.ZIP_NATIVE:
            archived = True
            inner = f.get("archive_members") or _downloader._listed_members(listed, name, arcs)
            if inner is None:
                inner, sizes_known = [], False
            members += [(m.get("name") or "", m.get("size")) for m in inner]
        else:
            members.append((name, f.get("file_size_bytes")))
    exts = [os.path.splitext(n)[1].lower() for n, _ in members]
    if not any(e in _core.DISC_EXTS for e in exts):
        if archived and not sizes_known and system in _downloader.EXPANDS:     # an archive RomM didn't list
            return {"label": "", "discs": 0, "unpacked": _guess_unpacked(detail, system), "estimated": True,
                    "upto": _downloader.unpacked_upper_bound(detail, system), "convert": None}
        return None
    count = exts.count
    kinds = []
    if count(".cue"):
        tracks = count(".bin")
        kinds.append("CUE/BIN" + (", %d tracks" % tracks if tracks > count(".cue") else ""))
    if count(".gdi"):
        kinds.append("GDI, %d tracks" % (len(exts) - count(".gdi")) if len(exts) > count(".gdi") + 1 else "GDI")
    for e in (".iso", ".chd", ".rvz", ".wbfs", ".gcz", ".ciso", ".cso", ".pbp", ".ccd", ".cdi", ".3ds", ".cci",
              ".cxi", ".cia", ".wud", ".wux"):
        if count(e):
            kinds.append(e[1:].upper())
    discs = count(".cue") + count(".gdi") + count(".iso") + count(".chd") + count(".rvz") + count(".wbfs") \
        + count(".cso") + count(".cdi") + count(".wud") + count(".wux")
    unpacked, estimated = None, False
    if archived and sizes_known and all(isinstance(s, int) for _, s in members):
        unpacked = sum(s for _, s in members)        # loose files are in members too
    elif archived:
        unpacked, estimated = _guess_unpacked(detail, system), True     # RomM didn't list sizes
    convs = (_convert.conversion_for(system, e, cfg, accepts) for e in exts)
    convert = next((c.label for c in convs if c is not None), None)
    return {"label": ", ".join(kinds), "discs": discs, "unpacked": unpacked, "estimated": estimated,
            "upto": estimated and _downloader.unpacked_upper_bound(detail, system), "convert": convert}


TAG_RE = re.compile(r"\(([^)]*)\)")
NOT_THE_GAME = ("demo", "beta", "proto", "sample", "kiosk", "unl", "pirate", "hack")
# GoodTools' codes in square brackets for a changed copy: [h1] hack, [b] bad dump, [t1] trainer,
# [p1] pirate, [o1] overdump, [T+Eng] translation ([!] verified, [a1] alternate, [f1] fixed are the game)
BRACKET_RE = re.compile(r"\[([^\]]*)\]")
NOT_THE_GAME_CODE = re.compile(r"[bhopt](?:\d|[A-Z]|$)|T[+-]")


def _tags(name):
    return {t.strip().lower() for g in TAG_RE.findall(name) for t in g.split(",")}


def _not_the_game(name):
    """A demo, beta, hack, bad dump… by its (tags) or GoodTools [codes]: never the retail game of its title."""
    return any(any(b in t for b in NOT_THE_GAME) for t in _tags(name)) or \
        any(NOT_THE_GAME_CODE.match(c.strip()) for c in BRACKET_RE.findall(name))


def find_in_romm(api, system, platform_ids, path, ra_hash=None):
    """RomM's copy of a game on the box whose file name RomM doesn't use:
    (rom, how) with how "ra" (same RetroAchievements hash: the same game data)
    or "name" (the one RomM title with the same name, disc and region — another
    revision or region spelling), else (None, None). A demo, beta, prototype,
    hack… here is matched by its hash only: by name it would become the retail game."""
    if ra_hash and ra_hash.strip("0") and re.fullmatch(r"[0-9a-fA-F]{32}", ra_hash):
        # games without RetroAchievements: no hash in ES, or RomM has none -> the title match below
        try:
            r = api.by_hash(ra_hash=ra_hash.lower())
        except _core.RommError:
            r = None
        if r and (not platform_ids or r.get("platform_id") in platform_ids):
            return r, "ra"
    stem = os.path.splitext(os.path.basename(path))[0]
    if _not_the_game(stem):
        return None, None               # a demo, beta, hack…: the retail game of that name isn't it
    title = _library.strip_tags(stem).lower()
    disc = _library.disc_info(stem)
    want_regions = _tags(stem) & {"usa", "europe", "japan", "world", "canada", "australia", "korea",
                                  "asia", "germany", "france", "spain", "italy", "brazil"}
    try:
        items = api.roms(platform_ids=platform_ids, search=_library.strip_tags(stem), limit=100).get("items", [])
    except _core.RommError:
        return None, None
    cands = []
    for r in items:
        n = r.get("fs_name_no_ext") or ""
        tags = _tags(n)
        if _library.strip_tags(n).lower() != title or _not_the_game(n):
            continue
        d = _library.disc_info(n)
        if (d[1] if d else None) != (disc[1] if disc else None):
            continue
        if want_regions and not (want_regions & tags):
            continue
        cands.append(r)
    if len(cands) == 1:
        return cands[0], "name"
    if cands:                                   # several revisions: the latest one
        revs = sorted(cands, key=lambda r: [int(x) for x in re.findall(r"\(Rev (\d+)\)",
                                                                       r.get("fs_name_no_ext") or "")] or [0])
        top = revs[-1]
        if sum(1 for r in cands if _tags(r.get("fs_name_no_ext") or "") == _tags(top.get("fs_name_no_ext") or "")) == 1:
            return top, "name"
    return None, None


def _pct(cut):
    have, need = cut[0], cut[1]
    return (round(100 * (1 - have / need)), "{:,}".format(have), "{:,}".format(need))


def _chd_frames(path, chdman):
    try:
        info = subprocess.run([chdman, "info", "-i", path], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    if "Tag='DVD" in info:
        m = re.search(r"Logical size:\s*([\d,]+)", info)
        return ("dvd", int(m.group(1).replace(",", "")) // 2048) if m else None
    f = re.findall(r"FRAMES:(\d+)", info)
    return ("cd", sum(int(x) for x in f)) if f else None


_Stopped = _convert.CheckStopped        # the check was stopped (B) in the middle of a game: unwinds out of it


class CheckAborted(_core.RommError):
    """The drive with the games stopped answering (unplugged, failing) or was
    swapped in the middle of a check: every file would read as missing, so the
    check ends there and nothing of it is saved."""


class _ZipUnsupported(Exception):
    """A zip packed in a way Python can't unpack (Deflate64, encrypted)."""


def _stop_point(cancel):
    _convert.check_stop_point(cancel)


CHD_STALL = 600                     # s: chdman verify that reports no progress for this long is given up on
CHD_CAP = 24 * 3600                 # s: backstop for a verify that keeps reporting yet never ends


def _chd_verify(chdman, path, cancel=None):
    """chdman verify: True if the CHD checks out, False if it doesn't, None if
    chdman can't run here (not installed: that says nothing about the disc),
    UNVERIFIED_SLOW if it stopped making progress and was given up on.
    cancel: chdman is killed as soon as it's set (raises _Stopped)."""
    argv = ["nice", "-n", "19", chdman, "verify", "-i", path]
    try:
        if cancel is None:
            rc = subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
        else:
            # killed on B, when the app quits, and when its progress (stderr) stalls: a slow disc
            # that keeps reporting is left to finish, a hang on a damaged hunk is not damage
            rc = _convert.run_check_tool(argv, cancel, timeout=CHD_CAP, stall=CHD_STALL)[0]
            if rc is None:
                _core.log("check: chdman verify of %s made no progress for %d min (or ran over %d h)"
                          % (path, CHD_STALL // 60, CHD_CAP // 3600))
                return UNVERIFIED_SLOW
    except OSError:
        return None
    return None if rc in (126, 127) else rc == 0     # nice: couldn't run it


TRACK_RE = re.compile(r"\s*\(track \d+\)$", re.I)


def _base(name):
    return os.path.splitext(os.path.basename(name))[0].lower()


def _disc_no(name):
    d = _library.disc_info(name)
    return d[1] if d else None


def _this_disc(names, stem):
    """Of several discs' files (sheets or images), the one a CHD named stem was
    made from: the same name, else the only one with its disc number; None when
    that can't be told."""
    same = [n for n in names if _base(n) == stem]
    if len(same) == 1:
        return same[0]
    want = _disc_no(stem)
    nums = [n for n in names if want is not None and _disc_no(_base(n)) == want]
    return nums[0] if len(nums) == 1 else None


def _disc_need(detail, chd, kind):
    """Bytes of RomM's copy of the disc a CHD here was made from: its .bin
    tracks (kind "cd") or its .iso ("dvd"). Of a game of several discs only
    this CHD's disc counts (by its sheet's or image's name, else its disc
    number); None when that can't be told or RomM lists no sizes, never a sum
    of every disc that would call this one incomplete."""
    members = []
    for f in detail.get("files") or []:
        if f.get("category") not in (None, "game"):
            continue
        for m in f.get("archive_members") or [{"name": f.get("file_name"), "size": f.get("file_size_bytes")}]:
            if m.get("name"):
                members.append((m["name"].replace("\\", "/"), m.get("size")))
    stem = _base(chd)
    imgs = [(n, s) for n, s in members if n.lower().endswith(".bin" if kind == "cd" else ".iso")]
    if kind != "cd":
        if len(imgs) > 1:
            one = _this_disc([n for n, _ in imgs], stem)
            imgs = [(n, s) for n, s in imgs if n == one]
    else:
        sheets = [n for n, _ in members if n.lower().endswith((".cue", ".gdi"))]
        if len(sheets) > 1:                         # one sheet per disc
            sheet = _this_disc(sheets, stem)
            if sheet is None:
                return None
            d = os.path.dirname(sheet)
            alone = sum(1 for s in sheets if os.path.dirname(s) == d) == 1      # each disc in its own folder
            imgs = [(n, s) for n, s in imgs if (alone and os.path.dirname(n) == d)
                    or TRACK_RE.sub("", _base(n)) == _base(sheet)]
        elif len({_disc_no(TRACK_RE.sub("", _base(n))) for n, _ in imgs} - {None}) > 1:
            want = _disc_no(stem)                   # several discs' tracks and no sheets to tell them apart
            imgs = [(n, s) for n, s in imgs if want is not None and _disc_no(TRACK_RE.sub("", _base(n))) == want]
    return sum(s for _, s in imgs if isinstance(s, int)) or None


def _chd_vs_romm(api, chd, rom, chdman, detail_of=None):
    """A CHD converted on the box against RomM's original (zip of cue/bin, or
    iso): None if it can't be compared; False if complete; else (have, need).
    detail_of: how to get RomM's record (default api.rom)."""
    try:
        detail = (detail_of or api.rom)(rom["id"])
    except _core.RommError:
        return None
    got = _chd_frames(chd, chdman)
    if not got:
        return None
    kind, have = got
    need = _disc_need(detail, chd, kind)
    if not need:
        return None
    need //= 2352 if kind == "cd" else 2048
    return (have, need) if have + 300 < need else False


def _walk_error(e):
    if e.errno not in (errno.ENOENT, errno.ENOTDIR):        # gone since the listing: nothing there
        raise e


def _files_in(path, keep, cancel=None, strict=False):
    """Files at or under path for which keep(name) holds, skipping media
    folders; stops (raises _Stopped) when cancel is set. strict: a folder that
    can't be read raises its OSError (else it reads as empty)."""
    if os.path.isfile(path):
        return [path] if keep(os.path.basename(path)) else []
    out = []
    for dp, dns, fns in os.walk(path, onerror=_walk_error if strict else None):
        _stop_point(cancel)
        dns[:] = sorted(x for x in dns if x not in _core.MEDIA_DIRS)
        out += [os.path.join(dp, f) for f in sorted(fns) if keep(f)]
    return out


def _images_in(path, cancel=None, strict=False):
    """Disc images (.cue, .iso, .chd) at or under path, skipping media folders."""
    return _files_in(path, lambda f: f.lower().endswith((".cue", ".iso", ".chd")), cancel, strict)


def _missing(path):
    """The file isn't there. A file the box can't tell about (an I/O error, no
    permission) isn't missing: its OSError is raised."""
    try:
        os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return True
    return False


def _not_a_game(n):
    low = n.lower()
    return n.startswith(".") or n in _core.MEDIA_DIRS or \
        low.endswith((".xml", ".txt", ".m3u", ".part", ".tmp.chd", ".chd.part"))


def _slim(rom):
    return {k: rom.get(k) for k in ("id", "name", "fs_name", "fs_name_no_ext", "platform_id")}


def _original(path, files, conv):
    """RomM's copy of what a file converted here was made from: the game file or
    archive member with its name and one of the converter's source extensions,
    as {"name", "size", "crc", "sha1"}; None if RomM has none."""
    stem = _core.stem_of(os.path.basename(path)).lower()
    exts = set(conv.sources) | set(conv.renamed_sources)
    orig = None
    for f in files:
        for name, m in [(f.get("file_name"), f)] + [(m.get("name"), m) for m in (f.get("archive_members") or [])]:
            base, ext = os.path.splitext(os.path.basename(name or "").lower())
            if base == stem and ext in exts:            # an archive's member wins over the archive
                crc = (m.get("crc_hash") or "").lower()
                orig = {"name": os.path.basename(name), "size": m.get("file_size_bytes") or m.get("size"),
                        "crc": crc.zfill(8) if crc else "", "sha1": (m.get("sha1_hash") or "").lower()}
    return orig


def _read_through(fh, cancel, update=None):
    for b in iter(lambda: fh.read(1 << 22), b""):
        _stop_point(cancel)
        if update:
            update(b)


def _zip_reads(z, cancel):
    """Every file in the zip unpacks and passes its CRC (testzip, but stopped
    between blocks on cancel). Raises BadZipFile (a CRC), zlib.error or
    EOFError (a damaged stream), _ZipUnsupported (a method
    or encryption Python can't unpack: raised as _ZipUnsupported)."""
    for i in z.infolist():
        if not i.is_dir():
            try:
                fh = z.open(i)
            except (NotImplementedError, RuntimeError) as e:
                raise _ZipUnsupported(e) from e
            with fh:
                try:
                    _read_through(fh, cancel)
                except (NotImplementedError, RuntimeError) as e:
                    raise _ZipUnsupported(e) from e


def _zip_name(n):
    return (n or "").replace("\\", "/").strip("/").lower()


def _member_finder(infos, wanted):
    """find(RomM's member name): the zip's entry for it, by its path (either
    slash, any case), else by its file name alone when only one entry in the
    zip and one of RomM's names (wanted) have it; None otherwise."""
    by_path = {_zip_name(i.filename): i for i in infos}
    bases = {}
    for i in infos:
        bases.setdefault(_zip_name(i.filename).rsplit("/", 1)[-1], []).append(i)
    romm = {}
    for n in wanted:
        b = _zip_name(n).rsplit("/", 1)[-1]
        romm[b] = romm.get(b, 0) + 1

    def find(name):
        n = _zip_name(name)
        if n in by_path:
            return by_path[n]
        b = n.rsplit("/", 1)[-1]
        hits = bases.get(b) or []
        return hits[0] if len(hits) == 1 and romm.get(b) == 1 else None
    return find


def _hash_ok(path, f, cancel=None):
    """downloader.file_hash_ok, stopped between blocks on cancel: (ok, algo),
    ok None when RomM's record can't vouch for the file. A zip RomM recorded
    without its members goes to file_hash_ok as is."""
    f = f or {}
    low = path.lower()
    if low.endswith(".zip"):
        members = f.get("archive_members") or []
        if not members:
            return _downloader.file_hash_ok(path, f)
        with zipfile.ZipFile(path) as z:
            find = _member_finder([i for i in z.infolist() if not i.is_dir()], [m.get("name") for m in members])
            for m in members:
                i = find(m.get("name"))
                if i is None or (m.get("size") is not None and i.file_size != m["size"]) or \
                        (m.get("crc_hash") and ("%08x" % i.CRC) != m["crc_hash"].lower().zfill(8)):
                    return False, "zip"
            try:
                _zip_reads(z, cancel)
            except zipfile.BadZipFile:
                return False, "zip"
        return True, "zip"
    if low.endswith(_downloader.CONTENT_HASHED):
        return None, None
    for key, algo in (("sha1_hash", "sha1"), ("md5_hash", "md5"), ("crc_hash", "crc")):
        want = f.get(key)
        if not want:
            continue
        crc = [0]
        h = hashlib.new(algo) if algo != "crc" else None
        with open(path, "rb") as fh:
            _read_through(fh, cancel, h.update if h else (lambda b: crc.__setitem__(0, zlib.crc32(b, crc[0]))))
        if h:
            return h.hexdigest() == want.lower(), algo
        return ("%08x" % crc[0]) == want.lower().zfill(8), algo
    return None, None


# a disc's own files: gone on purpose once it's converted (CHD, RVZ…) in its folder
TRACK_EXTS = (".bin", ".cue", ".gdi", ".img", ".raw", ".sub", ".sbi", ".ccd", ".wav", ".ogg", ".mp3", ".flac",
              ".iso")
NOT_GAME_FILES = (".sub", ".sbi", ".srm", ".sav")    # sit beside a game, never one: not counted as checked
HEADER_SIZES = (16, 512, 1024)       # a cart copier header (NES, SNES, Lynx…): the same ROM with or without it
UNVERIFIED_ROMM = "RomM"             # in stats["unverified_tools"]: RomM didn't send the game's record
UNVERIFIED_ZIP = "zip"               # a zip packed in a way Python can't unpack (Deflate64, encrypted)
UNVERIFIED_SLOW = "chdman-timeout"  # a chdman verify that stopped making progress (see the log)
UNVERIFIED_ERROR = "error"           # the check itself went wrong on the game (a bug: in the log)
_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)    # a folder held open (pinned) during a check
IO_ERRNOS = {errno.EIO, errno.ENXIO, errno.ENODEV, errno.ESTALE, getattr(errno, "EREMOTEIO", errno.EIO)}


_AUX_EXTS = tuple(e for e in TRACK_EXTS if e not in (".cue", ".gdi", ".iso"))   # tracks, sub files: not a disc


def _out_stem(out):
    """An output's name as its source's: "x (disc 1)" for X (Disc 1).chd and X (Disc 1).iso.squashfs."""
    b = _base(out)
    s, e = os.path.splitext(b)
    return s if e in _core.DISC_EXTS else b


def _converted_into(missing, wanted, outs):
    """{missing file: the outputs (CHD, RVZ…) made here from it}: an empty set
    for a file really missing. A file counts as converted when it's a disc's
    own kind of file (track, sheet, image) and an output has its name (less
    "(Track n)"), else its disc number; with no disc numbers at all, only when
    the game is one disc. One disc converted never hides another one missing."""
    took = set(TRACK_EXTS) | {e for c in _convert.CONVERTERS if c.chd for e in c.sources}
    for o in outs:
        c = _convert.output_converter(o)
        if c:
            took |= set(c.sources) | set(c.renamed_sources)
    stems = {o: _out_stem(o) for o in outs}
    sheets = [fp for fp in wanted if fp.lower().endswith((".cue", ".gdi"))]
    discs = len(sheets) if sheets else \
        sum(1 for fp in wanted if os.path.splitext(fp)[1].lower() in took and not fp.lower().endswith(_AUX_EXTS))
    numbered = any(_disc_no(s) is not None for s in stems.values()) or \
        any(_disc_no(TRACK_RE.sub("", _base(fp))) is not None for fp in wanted)
    out = {}
    for fp in missing:
        stem = TRACK_RE.sub("", _base(fp))
        same = {o for o, s in stems.items() if s == stem}
        if any(o.lower().endswith(".chd") for o in same):
            out[fp] = same                      # a CHD of its name: whatever the file was
            continue
        if os.path.splitext(fp)[1].lower() not in took:
            out[fp] = set()
            continue
        if same:
            out[fp] = same
            continue
        no = _disc_no(stem)
        if no is not None:
            out[fp] = {o for o, s in stems.items() if _disc_no(s) == no}
        elif not numbered and discs == 1:
            out[fp] = set(outs)
        else:
            out[fp] = set()
    return out


def _why_unreadable(e):
    if e.errno in IO_ERRNOS or not e.strerror:
        return "I/O error"
    return e.strerror[:1].lower() + e.strerror[1:]


def audit(api, platforms_by_system, deep=False, progress=None, cancel=None, chdman="chdman"):
    """Check games on the box against RomM. Quick: missing files and sizes.
    deep: also checksums, `chdman verify` and the converters' full checks.
    Returns (findings, stats). A finding: {"system", "path", "problem",
    "severity", "rom" (or None)}; severity "damaged" (unplayable/corrupt) or
    "differs" (not RomM's copy: another dump, a trimmed image… — may well
    play). "deep": True on what only a full check finds (a later quick check
    keeps it while the file is unchanged, see save_audit); "io_error": True
    when the box couldn't read the file: a failing or unplugged drive as likely
    as a bad copy, so never part of "download all damaged again".
    stats["checked"]/"matched"/"unmatched" count games (not each track or sub
    file). stats["unverified"]: games left unchecked, for the reasons in
    stats["unverified_tools"] (a missing tool, UNVERIFIED_ROMM, UNVERIFIED_ZIP,
    UNVERIFIED_SLOW, UNVERIFIED_ERROR) counted in stats["unverified_by"], each game once. A
    game that can't be read is a finding or unverified, never the end of the
    check; the drive going away is (CheckAborted: every file would read as
    missing)."""
    findings, stats = [], {"checked": 0, "matched": 0, "unmatched": 0}
    game_now = [None]                   # the game being checked: unverified counts games, not files
    unv = {}                            # tool -> the games it left unverified

    def unverified(tool):
        games = unv.setdefault(tool, set())
        if game_now[0] in games:
            return
        games.add(game_now[0])
        if tool not in stats.setdefault("unverified_tools", []):
            stats["unverified_tools"].append(tool)
        stats.setdefault("unverified_by", {})[tool] = len(games)
        stats["unverified"] = len(set().union(*unv.values()))

    def shown(p):
        rel = os.path.relpath(p, _core.ROMS_ROOT)
        return "roms" if rel == "." else "roms/" + rel

    def still_there(sysdir, sys_fd):
        """Raise CheckAborted when the games folder or this system's folder
        can't be read any more, or isn't the one the check started on (a drive
        unplugged, failing, or swapped; the system's folder moved or deleted):
        what reads as missing then isn't. Each folder is held open (pinned)
        while it's checked and compared with what its path leads to now: an
        exFAT or FAT drive (or a share, FUSE) numbers a folder anew each time
        it's read back into memory, so its number then says nothing."""
        for p, fd in ((_core.ROMS_ROOT, root_fd), (sysdir, sys_fd)):
            mine = p != _core.ROMS_ROOT        # the system's folder, on a games folder still there
            try:
                got = os.stat(p)
                want = os.fstat(fd)
            except (FileNotFoundError, NotADirectoryError) as e:
                if mine:
                    raise CheckAborted("The folder %s was moved or deleted during the check" % shown(p)) from None
                raise CheckAborted("The drive with the games stopped answering (%s: %s)"
                                   % (shown(p), _why_unreadable(e))) from None
            except OSError as e:
                raise CheckAborted("The drive with the games stopped answering (%s: %s)"
                                   % (shown(p), _why_unreadable(e))) from None
            if (got.st_dev, got.st_ino) != (want.st_dev, want.st_ino):
                raise CheckAborted("The folder %s changed during the check" % shown(p) if mine else
                                   "The drive with the games changed during the check (%s)" % shown(p))

    def add(system, path, problem, rom=None, severity="damaged", deep_only=False, io_error=False):
        f = {"system": system, "path": path, "problem": problem, "severity": severity,
             "rom": _slim(rom) if rom else None}
        if deep_only:
            f["deep"] = True
        if io_error:
            f["io_error"] = True
        findings.append(f)

    def verified(system, path, rom):
        """A converted CHD in a full check: flagged only when chdman says it's bad."""
        ok = _chd_verify(chdman, path, cancel)
        if ok == UNVERIFIED_SLOW:
            unverified(UNVERIFIED_SLOW)
        elif ok is None:
            unverified("chdman")
        elif not ok:
            add(system, path, "CHD failed chdman verify", rom, deep_only=True)

    def check_output(system, out, files, rom):
        """A file converted inside a game's folder: chdman verify (full check)
        for a CHD, its converter's own check for the rest."""
        if out.lower().endswith(".chd"):
            if deep:
                verified(system, out, rom)
            return
        conv = _convert.output_converter(out)
        try:
            bad = conv.audit(out, _original(out, files, conv), deep, cancel=cancel)
        except _convert.NotVerified as e:
            unverified(str(e))
            return
        if bad:
            add(system, out, bad[0], rom, bad[1], deep_only=deep)

    def identify(system):
        """Damaged games RomM doesn't list under their file name: find RomM's copy
        by RetroAchievements hash, or the one matching title/disc/region."""
        hashes = None
        for f in findings:
            if f["system"] != system or f["rom"] or f["severity"] != "damaged":
                continue
            if hashes is None:
                hashes = _es.es_hashes(system)
            rom, how = find_in_romm(api, system, platforms_by_system.get(system) or [], f["path"],
                                    hashes.get(f["path"]))
            if rom:
                f["rom"], f["via"] = dict(_slim(rom), fs_name_no_ext=rom.get("fs_name_no_ext")), how
                f["problem"] += " · RomM has it as %s%s" % (rom.get("fs_name") or rom.get("name"),
                                                           " (same RetroAchievements hash)" if how == "ra" else "")

    def members_of(files):
        return {m["name"].lower(): m for f in files for m in (f.get("archive_members") or []) if m.get("name")}

    def check_zip(system, path, files, rom):
        """A zip kept as-is: judge the ROM inside, not the container (the same
        ROM zips to different sizes)."""
        try:
            with zipfile.ZipFile(path) as z:
                local = [i for i in z.infolist() if not i.is_dir()]
                if deep:
                    try:
                        _zip_reads(z, cancel)
                    except zipfile.BadZipFile:
                        add(system, path, "Damaged zip (a file inside fails its CRC)", rom, deep_only=True)
                        return
                    except (zlib.error, EOFError):
                        add(system, path, "Damaged zip (a file inside can't be unpacked)", rom, deep_only=True)
                        return
                    except _ZipUnsupported:
                        unverified(UNVERIFIED_ZIP)         # its list of contents is still compared below
        except zipfile.BadZipFile:
            add(system, path, "Damaged zip (can't be opened)", rom)
            return
        except OSError as e:
            # EINVAL: a seek before the start (a central directory pointing outside the file):
            # the zip itself. Anything else (I/O, a share timing out…) is the drive's, not the zip's
            if e.errno != errno.EINVAL:
                raise                                   # the caller says it can't be read
            add(system, path, "Damaged zip (can't be opened)", rom)
            return
        want = members_of(files)
        if not want:                                # RomM didn't record the contents
            if files and files[0].get("file_size_bytes") and \
                    os.path.getsize(path) != files[0]["file_size_bytes"]:
                add(system, path, "Differs from RomM's copy (size)", rom, "differs")
            return
        find = _member_finder(local, [m["name"] for m in want.values()])
        for m in want.values():
            i = find(m["name"])
            if i is None:
                add(system, path, "Missing inside the zip: %s" % m["name"], rom)
            elif m.get("crc_hash") and ("%08x" % i.CRC) != m["crc_hash"].lower().zfill(8):
                add(system, path, "Different ROM than RomM's copy (%s)" % m["name"], rom, "differs")

    def member_rec(m):
        return {"file_size_bytes": m.get("size"), "sha1_hash": m.get("sha1_hash"),
                "md5_hash": m.get("md5_hash"), "crc_hash": m.get("crc_hash")}

    def check_game(system, sysdir, n, rom, game, records, find, cur, guard):
        """One game (a file or folder in the system's folder); cur["path"] is
        the file being read, for when reading it fails. guard(): raises
        CheckAborted when the drive went away (called before anything is
        called missing)."""
        path = os.path.join(sysdir, n)
        low = n.lower()
        details = {}

        def detail_of(rid):
            """RomM's record, fetched once for the game (a CHD in its folder asks too)."""
            if rid not in details:
                try:
                    details[rid] = records.get(rid)
                except _core.RommError as e:
                    details[rid] = e
            if isinstance(details[rid], Exception):
                raise details[rid]
            return details[rid]
        # 1) disc sheets must find every track (needs no RomM at all); a sheet
        # that can't be read is "Can't be read", not a disc with no tracks missing
        for sheet in _files_in(path, lambda f: f.lower().endswith((".cue", ".gdi")), cancel, strict=True):
            cur["path"] = sheet
            for ref in _discs.sheet_sources(sheet, strict=True):
                if _missing(ref):
                    guard()
                    add(system, sheet, "Missing track file: %s" % os.path.basename(ref),
                        find(os.path.splitext(n)[0], low))
        # 1b) disc images cut short (an interrupted copy): the game can't
        # load what's past the end. Converted CHDs are compared with RomM's
        # original exactly (below); here, the disc's own filesystem says so
        for img in _images_in(path, cancel, strict=True):
            cur["path"] = img
            irom = find(_base(img), os.path.splitext(n)[0], low, path=img)
            if img.lower().endswith(".chd") and irom and \
                    not (irom.get("fs_name") or "").lower().endswith(".chd"):
                exact = _chd_vs_romm(api, img, irom, chdman, detail_of)
                if exact is not None:
                    if exact:
                        add(system, img, "Incomplete disc image: %d%% missing (%s of %s sectors in RomM's "
                            "copy)" % _pct(exact), irom)
                    continue
            cut = _discs.disc_incomplete(img, chdman)
            if cut and cut[2]:
                add(system, img, "Incomplete disc image: %d%% missing (%s of %s sectors)" % _pct(cut), irom)
            elif cut:
                add(system, img, "May be cut short: a file on the disc reaches past its end (%s of %s "
                    "sectors; some discs list dummy files)" % ("{:,}".format(cut[0]), "{:,}".format(cut[1])),
                    irom, "differs")
        # 2) against RomM's record of the game
        cur["path"] = path
        conv = _convert.output_converter(path)
        if system in _convert.Z3DS_SYSTEMS and os.path.isfile(path) and _convert.z3ds_target(path):
            try:
                _convert.z3ds_info(path)
            except _convert.Z3dsError as e:          # whether RomM lists it or not: it won't run
                if "encrypted" in str(e):
                    add(system, path, "Encrypted dump: Azahar can't run it (needs a decrypted copy)",
                        rom, "differs")
            except OSError:
                pass
        if not rom:
            stats["unmatched"] += game
            return
        stats["matched"] += game
        if os.path.isfile(path) and low.endswith(".chd") and \
                not (rom.get("fs_name") or "").lower().endswith(".chd"):
            if deep:                                   # converted here: chdman checks itself
                verified(system, path, rom)
            return
        try:
            detail = detail_of(rom["id"])
        except _core.RommError as e:                   # nothing was compared: not damage
            _core.log("audit %s: %s" % (path, e))
            unverified(UNVERIFIED_ROMM)
            return
        files = [f for f in (detail.get("files") or []) if f.get("category") in (None, "game")]
        if conv and (rom.get("fs_name") or "").lower() != low:
            # converted here from RomM's copy: its converter knows how to check what it made
            try:
                bad = conv.audit(path, _original(path, files, conv), deep, cancel=cancel)
            except _convert.NotVerified as e:
                unverified(str(e))
                return
            if bad:
                add(system, path, bad[0], rom, bad[1], deep_only=deep)
            return
        want = []
        if os.path.isfile(path):
            same = len(files) == 1 and (files[0].get("file_name") or "").lower() == low
            if same and low.endswith(".zip"):
                check_zip(system, path, files, rom)
                return
            if same:
                want = [(path, files[0])]               # same file as RomM (iso, chd…)
            else:
                m = members_of(files).get(low)
                want = [(path, member_rec(m))] if m else []
        elif _library.rom_is_folder(detail, files):
            prefix = (detail.get("full_path") or "").rstrip("/") + "/"
            for f in files:
                rel = f.get("full_path") or ""
                rel = rel[len(prefix):] if rel.startswith(prefix) else (f.get("file_name") or "")
                if rel:
                    want.append((os.path.join(path, *rel.split("/")), f))
        else:
            mem = [m for f in files for m in (f.get("archive_members") or []) if m.get("name")]
            parts = [[p for p in m["name"].replace("\\", "/").split("/") if p] for m in mem]
            # one top folder in the zip unpacks straight into the system folder (as the
            # downloader does): "Game/Game.cue" is roms/<sys>/Game/Game.cue
            single_top = bool(parts) and len({p[0] for p in parts if p}) == 1 and all(len(p) > 1 for p in parts)
            for p, m in zip(parts, mem):
                if not p:
                    continue
                fp = os.path.join(path, *p)
                if single_top and _missing(fp):
                    fp = os.path.join(sysdir, *p)
                want.append((fp, member_rec(m)))
        missing = [fp for fp, _ in want if _missing(fp)]
        if missing and len(missing) == len(want) and _missing(path):
            guard()                             # deleted or moved since the listing: not damage
            _core.log("audit %s: gone since the listing" % path)
            return
        # discs converted on the box (CHD, RVZ…): their tracks and sheets are gone on purpose
        outs = _files_in(path, lambda x: x.lower().endswith(".chd") or bool(_convert.output_converter(x)),
                         cancel, strict=True) if missing and os.path.isdir(path) else []
        covered = _converted_into(missing, [fp for fp, _ in want], outs)
        for c in sorted(set().union(*covered.values())):
            cur["path"] = c
            check_output(system, c, files, rom)
        if any(not covered[fp] for fp in missing):
            guard()
        for fp, f in want:
            _stop_point(cancel)
            cur["path"] = fp
            if fp in covered:
                if not covered[fp]:
                    add(system, fp, "Missing file: %s" % os.path.basename(fp), rom)
                continue
            if _discs.xbox_cut(system, fp, f):
                continue          # cut to its game partition here: RomM's size and hashes are the full disc's
            size, need = os.path.getsize(fp), f.get("file_size_bytes")
            if need and size != need:
                small = size < need
                # a smaller .iso is usually a trimmed image (plays); a short
                # track is a broken copy; a cart 16/512/1024 bytes off is the
                # same ROM with or without a copier header
                trimmed = small and fp.lower().endswith((".iso", ".xiso"))
                header = abs(need - size) in HEADER_SIZES and \
                    not fp.lower().endswith(TRACK_EXTS + tuple(_core.DISC_EXTS) + (".xiso",))
                if header:
                    add(system, fp, "%s than RomM's copy by %d bytes (%s)" % (
                        "Smaller" if small else "Bigger", abs(need - size),
                        "a copy without its header" if small else "a copy with a header added"), rom, "differs")
                    continue
                add(system, fp, "%s than RomM's copy: %s vs %s%s" % (
                    "Smaller" if small else "Bigger", _core.human_size(size), _core.human_size(need),
                    " (trimmed or incomplete)" if trimmed else " (incomplete)" if small else ""),
                    rom, "differs" if (trimmed or not small) else "damaged")
            elif deep and _hash_ok(fp, f, cancel)[0] is False:
                add(system, fp, "Checksum doesn't match RomM", rom, deep_only=True)

    try:
        root_fd = os.open(_core.ROMS_ROOT, _DIR_FLAGS)      # held for the whole check (see still_there)
    except (FileNotFoundError, NotADirectoryError):
        stats["no_games_folder"] = True                    # nothing to check, and the screen says why
        return findings, stats
    except OSError as e:
        raise CheckAborted("Can't read the folder roms (%s)" % _why_unreadable(e)) from None
    try:
        for system in sorted(platforms_by_system):
            sysdir = os.path.join(_core.ROMS_ROOT, system)
            try:
                names = [n for n in sorted(os.listdir(sysdir)) if not _not_a_game(n)]
            except (FileNotFoundError, NotADirectoryError):
                still_there(_core.ROMS_ROOT, root_fd)      # no folder for the system, or no drive at all?
                continue
            except OSError as e:        # a folder the box can't read: its games can't be vouched for
                _core.log("audit %s: %r" % (sysdir, e))
                raise CheckAborted("Can't read the folder %s (%s)" % (shown(sysdir), _why_unreadable(e))) from None
            if not names:               # Batocera makes every system's folder: don't list RomM's games for none
                continue
            try:
                sys_fd = os.open(sysdir, _DIR_FLAGS)       # held while its games are checked
            except OSError as e:
                still_there(_core.ROMS_ROOT, root_fd)      # the drive gone?
                raise CheckAborted(("The folder %s was moved or deleted during the check" % shown(sysdir))
                                   if isinstance(e, (FileNotFoundError, NotADirectoryError)) else
                                   "The drive with the games stopped answering (%s: %s)"
                                   % (shown(sysdir), _why_unreadable(e))) from None
            try:
                index = {}
                here = _names_here(sysdir, names)
                for pid in platforms_by_system[system]:
                    listed = api.roms_all(platform_ids=[pid], cancel=cancel, limit=LIST_PAGE,
                                          keep=lambda r: _listed_as(r) & here)
                    if listed is None:
                        return findings, stats
                    for r in listed:
                        for k in _listed_as(r) - {""}:
                            index.setdefault(k, []).append(r)

                def find(*keys, path=None, index=index):
                    """RomM's game under the first of keys it lists; of one name on two
                    platforms of this system, the one with this file's size."""
                    for k in keys:
                        cands = index.get(k.lower())
                        if not cands:
                            continue
                        if len(cands) > 1 and path and os.path.isfile(path):
                            try:
                                size = os.path.getsize(path)
                            except OSError:
                                size = None
                            same = [r for r in cands if size is not None and r.get("fs_size_bytes") == size]
                            if same:
                                return same[0]
                        return cands[0]
                    return None
                # a sheet's tracks and a game's sub files aren't games of their own
                parts = set()
                for n in names:
                    if n.lower().endswith((".cue", ".gdi")):
                        parts.update(os.path.basename(r).lower() for r in _discs.sheet_sources(os.path.join(sysdir, n)))
                roms = {}
                for n in names:
                    path = os.path.join(sysdir, n)
                    folder = os.path.isdir(path)
                    stem = _core.stem_of(n) if _convert.output_converter(path) or not folder else n  # "X.ps3": RomM's X.zip
                    roms[n] = find(n, stem, os.path.splitext(n)[0], path=path)
                # RomM's record of each game, fetched a few at a time just ahead of the loop
                with _api.Prefetch(api.rom, [r["id"] for r in roms.values() if r]) as records:
                    for n in names:
                        if cancel is not None and cancel.is_set():
                            return findings, stats
                        if progress:
                            progress(system, n)
                        game = n.lower() not in parts and not n.lower().endswith(NOT_GAME_FILES)
                        stats["checked"] += game
                        cur = {"path": os.path.join(sysdir, n)}
                        rom = roms[n]
                        game_now[0] = (system, n)

                        def guard(sysdir=sysdir, sys_fd=sys_fd):
                            still_there(sysdir, sys_fd)
                        try:
                            check_game(system, sysdir, n, rom, game, records, find, cur, guard)
                        except _Stopped:
                            return findings, stats
                        except CheckAborted:
                            raise
                        except (zlib.error, EOFError, zipfile.BadZipFile) as e:
                            _core.log("audit %s: %r" % (cur["path"], e))
                            add(system, cur["path"], "Can't be read: the data in it is damaged", rom, deep_only=deep)
                        except _ZipUnsupported as e:                         # a zip Python can't unpack
                            _core.log("audit %s: %r" % (cur["path"], e))
                            unverified(UNVERIFIED_ZIP)
                        except OSError as e:
                            _core.log("audit %s: %r" % (cur["path"], e))
                            guard()                                          # the drive itself, or this file?
                            if e.errno != errno.ENOENT:                      # gone since the listing: nothing to check
                                add(system, cur["path"], "Can't be read (%s)" % _why_unreadable(e), rom,
                                    deep_only=deep, io_error=True)
                        except Exception as e:           # the check went wrong, not the game: neither damage nor a pass
                            _core.log("audit %s: internal error %r\n%s" % (cur["path"], e, traceback.format_exc()))
                            unverified(UNVERIFIED_ERROR)
                        guard()                          # after each game: a drive gone mid-game ends the check
            finally:
                os.close(sys_fd)
    finally:
        os.close(root_fd)
    for system in {f["system"] for f in findings}:
        if cancel is not None and cancel.is_set():
            break
        try:
            identify(system)
        except _core.RommError:
            pass
    return findings, stats


def audit_path():
    return os.path.join(_core.DATA_DIR, "audit-last.json")     # follows DATA_DIR (tests re-point it)


def save_audit(findings, stats, deep):
    """Keep the last finished check, so On this box can show it later. Each
    finding remembers its file's size and mtime: a file changed since (fixed,
    replaced) drops out on load. A quick check keeps the last full check's
    findings that only a full check makes ("deep": checksums, chdman verify…)
    while their file is unchanged and the quick check found nothing else
    there; each one carried over keeps "full_time", when that full check ran."""
    now = time.time()
    findings = list(findings)
    if not deep:
        prev = load_audit()
        if prev:
            prev_time = prev.get("time") if prev.get("deep") else None
            seen = {f["path"] for f in findings}
            for f in prev["findings"]:
                when = f.get("full_time") or prev_time
                if f.get("deep") and f["path"] not in seen and isinstance(when, (int, float)):
                    findings.append(dict(f, full_time=when))
    out = []
    for f in findings:
        try:
            st = os.stat(f["path"])
            sig = [st.st_size, int(st.st_mtime)]
        except OSError:
            sig = None
        out.append(dict(f, sig=sig))
    os.makedirs(_core.DATA_DIR, exist_ok=True)
    _core.atomic_write_json(audit_path(), {"time": now, "deep": bool(deep), "v": AUDIT_FORMAT, "stats": stats,
                                           "findings": out})


AUDIT_FORMAT = 2          # in audit-last.json: 2 marks each full-check-only finding "deep" itself
# what only a full check finds, by its text: a full check saved before AUDIT_FORMAT 2 flagged none of them
DEEP_PROBLEMS = ("CHD failed chdman verify", "Checksum doesn't match RomM", "Damaged zip (a file inside",
                 "Can't be read: the data in it is damaged", "Compressed file damaged: ",
                 "Compressed file damaged (dolphin-tool", "Compressed file damaged (wuatool",
                 "Compressed file damaged (unsquashfs", "Compressed file damaged (its SHA-256",
                 "dolphin-tool found errors")
DEEP_PROBLEM_ENDS = (" to its end",)    # a PS3 folder's file that can't be read to its end ("Can't read X to its end")


def deep_problem(problem):
    """A finding's text is one only a full check makes (DEEP_PROBLEMS, DEEP_PROBLEM_ENDS)."""
    return problem.startswith(DEEP_PROBLEMS) or problem.endswith(DEEP_PROBLEM_ENDS)


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def load_audit():
    """The last finished check, or None: {"time", "deep", "stats", "findings"},
    without findings whose file is gone or changed since, or, for a file that
    was missing ("Missing file: …"), is there now."""
    try:
        with open(audit_path()) as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    if not _number(d.get("time")):
        d["time"] = 0
    old_full = d.get("deep") and d.get("v") is None       # a full check from before findings were flagged
    keep = []
    for f in d.get("findings") if isinstance(d.get("findings"), list) else []:
        if not isinstance(f, dict) or not all(isinstance(f.get(k), str) for k in ("path", "system", "severity",
                                                                                  "problem")):
            continue
        if "full_time" in f and not _number(f["full_time"]):
            del f["full_time"]
        if old_full and "deep" not in f and deep_problem(f["problem"]):
            f["deep"] = True
        try:
            st = os.stat(f["path"])
        except OSError:
            if f.get("sig") is None:            # missing when checked, still missing ("Missing file: …")
                keep.append(f)
            continue
        if f.get("sig") == [st.st_size, int(st.st_mtime)]:     # missing when checked (None): there now
            keep.append(f)
    d["findings"] = keep
    if not isinstance(d.get("stats"), dict):
        d["stats"] = {}
    return d


# ----------------------------------------------------------- game info for games already here

INFO_TAGS = ("name", "desc", "image")
# what a game "lacks" is only ever what RomM has for it: these, when its entry offers them
# (rating left out: ES may keep RomM's 0 as no rating at all)
OFFER_TEXT = ("name", "desc", "releasedate", "developer", "publisher", "genre", "players", "region", "lang",
              "family")
OFFER_MEDIA = ("image", "thumbnail", "marquee", "titleshot", "boxart", "boxback", "fanart", "cartridge",
               "video", "manual")


def offered(entry):
    """The gamelist tags a game's entry (library.gamelist_entry) would fill: its
    text RomM has, and the media RomM keeps for it (manual if asked for)."""
    out = {k for k in OFFER_TEXT if entry.get(k) not in (None, "", [])}
    if entry.get("genres"):
        out.add("genre")
    out |= {k for k, v in (entry.get("media") or {}).items() if k in OFFER_MEDIA and v}
    if entry.get("manual"):
        out.add("manual")
    return out


LIST_PAGE = 250       # games per listing page: RomM's records are ~16 KB, and 5 pages are in flight


def _names_here(sysdir, names):
    """Every name a game here can be listed under in RomM: its file or folder
    name and its stem, and the disc images inside a folder, lower case."""
    out = set()
    for n in names:
        out.add(n.lower())
        path = os.path.join(sysdir, n)
        if not os.path.isdir(path):
            out.add(_core.stem_of(n).lower())
            out.add(os.path.splitext(n)[0].lower())
        else:                               # discs kept in a game's folder are looked up by their own names
            out.update(os.path.splitext(os.path.basename(i))[0].lower() for i in _images_in(path))
            if _convert.output_converter(n):            # a game folder made here ("X.ps3"): RomM's X.zip
                out.add(_core.stem_of(n).lower())
    return out


def _listed_as(r):
    return {(r.get("fs_name_no_ext") or "").lower(), (r.get("fs_name") or "").lower()}


def _gamelist_info(sysdir):
    """{relative path: set of filled tags} from roms/<sys>/gamelist.xml (read only); a
    media tag counts when its file is there."""
    out = {}
    try:
        root = ET.parse(os.path.join(sysdir, "gamelist.xml")).getroot()
    except (OSError, ET.ParseError):
        return out
    for g in root.findall("game"):
        p = g.findtext("path")
        if p:
            out[p] = {t for t in OFFER_TEXT if (g.findtext(t) or "").strip()}
            out[p] |= {t for t in OFFER_MEDIA if (g.findtext(t) or "").strip()
                       and os.path.isfile(os.path.normpath(os.path.join(sysdir, g.findtext(t).strip())))}
    return out


def _es_info(system, sysdir):
    """({relative path: set of filled tags}, {relative path: ES game id}) from ES's own list
    of the system (GET /systems/<sys>/games): what ES holds now. It keeps new info in a
    journal and writes gamelist.xml only now and then (on quitting, a reload), so a game
    just given its info can look bare in the file. ({}, {}) when ES can't be asked."""
    try:
        games = _es.EsClient(timeout=15).json("/systems/%s/games" % urllib.parse.quote(system)) or []
    except Exception:
        return {}, {}
    tags, ids = {}, {}
    for g in games if isinstance(games, list) else []:
        if not isinstance(g, dict) or not isinstance(g.get("path"), str):
            continue
        got = set()
        for t in OFFER_TEXT + OFFER_MEDIA:          # a name counts as it does in gamelist.xml (the file's, too)
            v = str(g.get(t) or "").strip()
            if v and v not in ("0", "0.000000", "not-a-date-time", "false"):
                got.add(t)
        rel = "./" + os.path.relpath(g["path"], sysdir)
        tags[rel] = got
        if g.get("id"):
            ids[rel] = g["id"]
    return tags, ids


def es_image_is_thumb(system, gid, base=None):
    """ES's image of the game is byte for byte its thumbnail (GET .../media/<tag>);
    None when ES can't say."""
    got = []
    for tag in ("image", "thumbnail"):
        try:
            r = _es.EsClient(base, timeout=15).get(
                "/systems/%s/games/%s/media/%s" % (urllib.parse.quote(system), gid, tag), errors=False)
        except Exception:
            return None
        if r.status != 200:
            return None
        got.append(r.body)
    return got[0] == got[1]


def plan_info(api, system, platform_ids, es, cfg=None, cancel=None):
    """Gamelist entries for games already in roms/<system> that RomM knows
    (matched by file name, like "already on this box") and whose ES entry
    lacks something RomM has for it: text, images, video, manual (offered(),
    as Settings' Metadata section asks: cfg). Media noted as never arriving
    (library.skipped_tags) doesn't count as lacking. What the game has is read from
    gamelist.xml and from ES itself (it saves the file only now and then); an image that
    is the box art again, as versions before 0.11 left it, counts as lacking. Entries
    carry "fill": the merge only sets what's missing. A game whose info is still waiting to be
    saved (pending_gamelist_paths) isn't offered again. Returns (entries, stats)."""
    sysdir = os.path.join(_core.ROMS_ROOT, system)
    stats = {"on_box": 0, "matched": 0, "complete": 0, "queued": 0, "waiting": 0}
    if not os.path.isdir(sysdir):
        return [], stats
    index, sets = {}, {}
    names = sorted(os.listdir(sysdir))
    here = _names_here(sysdir, names)

    def wanted(r):                          # on the box under its file name, or as a disc of a playlist here
        if _listed_as(r) & here:
            return True
        d = _library.disc_info(r.get("fs_name_no_ext") or "")
        return bool(d) and d[0].lower() in here
    for pid in platform_ids:
        if cancel is not None and cancel.is_set():
            return [], stats
        listed = api.roms_all(platform_ids=[pid], cancel=cancel, limit=LIST_PAGE, keep=wanted)
        if listed is None:
            return [], stats
        for r in listed:
            index.setdefault((r.get("fs_name_no_ext") or "").lower(), r)
            index.setdefault((r.get("fs_name") or "").lower(), r)
            d = _library.disc_info(r.get("fs_name_no_ext") or "")
            if d and (d[0].lower() not in sets or d[1] < sets[d[0].lower()][0]):
                sets[d[0].lower()] = (d[1], r)          # a set's .m3u gets disc 1's info
    have = _gamelist_info(sysdir)
    es_have, es_ids = _es_info(system, sysdir)
    for p, tags in es_have.items():
        got = have.setdefault(p, set())
        for t in tags - got:
            if t in OFFER_MEDIA and p in es_ids and not _library.es_media_file_here(sysdir, p, t) \
                    and _es.es_media_present(system, es_ids[p], t) is not True:
                continue                    # a path to a file that's gone (a game deleted, downloaded again)
            got.add(t)
    skips = _library.media_skips()
    pending = _core.pending_gamelist_paths(system)      # filled (or downloaded) already, saved on leaving
    in_m3u = set()
    for n in names:
        if n.lower().endswith(".m3u"):
            try:
                in_m3u.update(_es.m3u_paths(os.path.join(sysdir, n)))
            except OSError:
                pass
    exts = es.get(system)
    entries = []
    for n in names:
        path = os.path.join(sysdir, n)
        low = n.lower()
        if n.startswith(".") or n in _core.MEDIA_DIRS or low.endswith((".xml", ".txt", ".part", ".tmp.chd")) \
                or ".bak" in low or path in in_m3u \
                or any(q.startswith(path + os.sep) for q in in_m3u):       # a disc folder of a playlist
            continue
        isdir = os.path.isdir(path)
        if not isdir and exts is not None and os.path.splitext(low)[1] not in exts:
            continue
        stats["on_box"] += 1
        stem = n if isdir else os.path.splitext(n)[0]
        rom = index.get(low) or index.get(stem.lower())
        title = None
        if rom is None and low.endswith(".m3u"):
            hit = sets.get(stem.lower())
            if hit:
                rom = hit[1]
                d = _library.disc_info(rom.get("name") or "")
                title = d[0] if d else rom.get("name")
        if rom is None:
            continue
        stats["matched"] += 1
        prim = path if (not isdir or low.endswith(".m3u")) else _library.primary_path(system, [path], es)
        if not prim:
            continue
        if prim in pending:
            stats["waiting"] += 1
            continue
        rel = "./" + os.path.relpath(prim, sysdir)
        got = have.get(rel, set())
        e = _library.gamelist_entry(rom, system, [prim], cfg=cfg)
        e["fill"] = True
        if title:
            e["name"] = title
        if "image" in got and _library.wants_own_image(e["media"]) and _library.image_is_thumb_again(sysdir, prim) \
                and (rel not in es_ids or es_image_is_thumb(system, es_ids[rel]) is not False):
            got = got - {"image"}            # the box art twice (an older version's image): the screenshot is due
        if offered(e) <= got | _library.skipped_tags(e, cfg, skips):
            stats["complete"] += 1
            continue
        entries.append(e)
    stats["queued"] = len(entries)
    return entries, stats


def queue_info(entries):
    if entries:
        os.makedirs(_core.DATA_DIR, exist_ok=True)
        _core.append_gamelist(*entries)
