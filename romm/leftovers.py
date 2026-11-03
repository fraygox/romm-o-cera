"""Leftovers: files a download or conversion left behind."""
import json
import os
import re
import shutil
import stat
import struct
import threading
import time
import zipfile

from . import (core as _core, discs as _discs, es as _es, ps3 as _ps3, wiiu as _wiiu, convert as _convert,
               library as _library)


# ---------------------------------------------------------- leftovers

def chd_worker_busy():
    """True while rb_chd.py holds its lock (a conversion may be writing)."""
    try:
        with _core.try_lock(_core.chd_lock_path()) as fd:
            return fd is None
    except OSError:
        return False


# An archive is unpacked into a private folder beside where it lands (moved
# into place once all of it is out). A process killed half-way (power cut,
# quit, update) leaves that folder with a half-written copy: nothing lists it.
UNPACK_STAGE = ".rb-extract-"
_STAGE_RE = re.compile(r"^\.rb-extract-(\d+)-(\d+)$")
STALE_STAGE_AGE = 3600          # seconds: a stage whose owner can't be told is stale after this


def unpack_stage(target):
    """This thread's private unpack folder in target."""
    return os.path.join(target, "%s%d-%d" % (UNPACK_STAGE, os.getpid(), threading.get_ident()))


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True                            # someone else's process: it exists
    return True


def _untouched_for(path, age):
    """Nothing in the folder (nor the folder) changed for age seconds."""
    newest = 0
    for dp, dns, fns in os.walk(path):
        for n in [dp] + [os.path.join(dp, x) for x in dns + fns]:
            try:
                newest = max(newest, os.lstat(n).st_mtime)
            except OSError:
                pass
    return newest and time.time() - newest > age


def stage_stale(path):
    """An unpack folder no running unpack owns: its process is gone (or, in
    this process, its thread is). A name that doesn't say, or a pid that's
    alive (after a restart it can be another program's), once nothing in it
    has changed for STALE_STAGE_AGE: an unpack under way keeps writing."""
    m = _STAGE_RE.match(os.path.basename(path))
    if m and int(m.group(1)) == os.getpid():
        return int(m.group(2)) not in {t.ident for t in threading.enumerate()}
    if m and not _pid_alive(int(m.group(1))):
        return True
    return bool(_untouched_for(path, STALE_STAGE_AGE))


# Moving a stage's files into place is the one step that can't be rolled back
# (it's many renames, one per file). Before it starts, a marker beside where the
# game lands says which archive is being moved there and for which RomM game; it
# goes once that game is recorded. A process killed between two renames leaves
# a game folder with only some of its files: a later attempt to unpack the same
# archive for the same game finds the marker, so that folder is its own half-made
# copy to unpack over (every file is replaced again), not "already on the box".
MOVE_MARKER = ".rb-moving-"


def write_move_marker(where, lands, archive, rom_id, keep=False):
    """Say (on disk) that archive's files are being moved to lands for rom_id.
    keep: lands is a copy that was there before (one Check games replaces), never
    deleted as an unfinished unpack. Returns the marker's path: one per job and
    attempt (two jobs of one thread, one after the other, never share it)."""
    rid = None if rom_id is None else str(rom_id)
    p = os.path.join(where, "%s%d-%d-%s-%d" % (MOVE_MARKER, os.getpid(), threading.get_ident(),
                                               re.sub(r"\W+", "_", rid or "x"), time.time_ns()))
    _core.atomic_write_json(p, {"lands": os.path.normpath(lands), "archive": os.path.normpath(archive),
                                "rom": rid, "when": time.time(), "moved": False, "keep": bool(keep)})
    return p


def mark_moved(marker):
    """Every file of the marker's move is in place (the game is whole, only not
    recorded yet): Free up space never offers it as an unfinished unpack."""
    try:
        with open(marker) as f:
            m = json.load(f)
        m["moved"] = True
        _core.atomic_write_json(marker, m)
    except (OSError, ValueError, TypeError) as e:
        _core.log("UNPACK: marker %s not updated: %r" % (marker, e))


def read_move_marker(path):
    """A marker's {"lands", "archive", "rom", ...}, or None when it can't be read."""
    try:
        with open(path) as f:
            m = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(m, dict) or not isinstance(m.get("lands"), str) or not isinstance(m.get("archive"), str):
        return None
    return m


def recorded_paths():
    """Every path installed.json records, or None when it can't be read now (then
    nothing may be deleted as not being a recorded game's)."""
    try:
        recs = _library._read_records(_core.STATE_PATH)[0]
    except FileNotFoundError:
        recs = _library._read_backup() or {}
    except (OSError, ValueError):
        return None
    return [os.path.normpath(p) for rec in recs.values() for p in rec.get("paths") or []]


def path_overlaps(path, others):
    """path is one of others (normalised paths), inside one, or holds one."""
    q = os.path.normpath(path)
    return any(o == q or o.startswith(q + os.sep) or q.startswith(o + os.sep) for o in others)


MOVE_MTIME_SLACK = 2.0          # seconds: FAT keeps a file's time to 2 s


def _touched_since(path, when):
    """A file under path (or path, a file) changed after the marker was written
    (when): the moves only rename files unpacked before it, so one newer was put
    there since (by hand, over the network). Folders don't count: the moves make them."""
    try:
        when = float(when)
    except (TypeError, ValueError):
        return True                            # can't tell: never deleted without asking
    if os.path.isdir(path) and not os.path.islink(path):
        names = [os.path.join(dp, n) for dp, _, fns in os.walk(path) for n in fns]
    else:
        names = [path]
    for n in names:
        try:
            st = os.lstat(n)
        except OSError:
            continue
        if not stat.S_ISDIR(st.st_mode) and st.st_mtime > when + MOVE_MTIME_SLACK:
            return True
    return False


def drop_unfinished_move(marker, sysdir, keep=(), if_untouched=False):
    """Delete a half-moved game (where an unpack killed while moving its files in
    left only some of them) and its marker. Why that's safe: a marker is written
    only right before the moves, onto a landing path _postprocess_file let the
    unpack use: nothing there, an empty folder, a copy this same game is recorded
    at, or one Check games replaces. The last two are never deleted (recorded
    in installed.json, keep / the marker's own "keep"); so what's there is only
    what that unpack moved in. Only inside sysdir, never sysdir itself, and not
    while installed.json can't be read. if_untouched (a cancel, nobody asked): not
    when a file there is newer than the marker (_touched_since), a folder filled by
    hand since; the marker stays, so Free up space offers it and deletes it only
    when asked. Returns bytes freed (None: not deleted)."""
    m = read_move_marker(marker)
    if m is None or m.get("keep"):
        return None
    lands = os.path.normpath(m["lands"])
    rec = recorded_paths()
    if rec is None or path_overlaps(lands, rec) or path_overlaps(lands, [os.path.normpath(k) for k in keep]):
        return None
    if lands == os.path.normpath(sysdir) or not _core._inside(lands, sysdir):
        return None
    if if_untouched and os.path.lexists(lands) and _touched_since(lands, m.get("when")):
        _core.log("UNPACK: unfinished unpack %s has files newer than it; kept (Free up space offers it)" % lands)
        return None
    freed = _core.path_size(lands) if os.path.lexists(lands) else 0
    try:
        if os.path.isdir(lands) and not os.path.islink(lands):
            shutil.rmtree(lands)
        elif os.path.lexists(lands):
            os.remove(lands)
    except OSError as e:
        _core.log("UNPACK: unfinished unpack %s not removed: %r" % (lands, e))
        return None
    drop_move_markers([marker])
    _core.log("UNPACK: removed the unfinished unpack %s (%s)" % (lands, _core.human_size(freed)))
    return freed


def move_markers(where, archive, rom_id):
    """{marker path: lands} of the moves of archive for rom_id that markers in
    where say were started (finished or not: a marker goes only once the game is
    recorded). The same archive file and the same RomM game (or no RomM game
    either time: an archive unpacked from Games ES can't see)."""
    out = {}
    try:
        names = [n for n in os.listdir(where) if n.startswith(MOVE_MARKER)]
    except OSError:
        return out
    rid = None if rom_id is None else str(rom_id)
    for n in names:
        p = os.path.join(where, n)
        try:
            with open(p) as f:
                m = json.load(f)
            if m.get("archive") == os.path.normpath(archive) and m.get("rom") == rid \
                    and isinstance(m.get("lands"), str):
                out[p] = m["lands"]
        except (OSError, ValueError, AttributeError):
            continue
    return out


def drop_move_markers(paths):
    for p in paths:
        try:
            os.remove(p)
        except FileNotFoundError:
            pass
        except OSError as e:
            _core.log("UNPACK: marker %s not removed: %r" % (p, e))


def sweep_stages(target):
    """Delete the stale unpack folders in target (see stage_stale). Returns them."""
    gone = []
    try:
        names = os.listdir(target)
    except OSError:
        return gone
    for n in names:
        p = os.path.join(target, n)
        if n.startswith(UNPACK_STAGE) and os.path.isdir(p) and not os.path.islink(p) and stage_stale(p):
            shutil.rmtree(p, ignore_errors=True)
            if not os.path.lexists(p):
                gone.append(p)
    if gone:
        _core.log("UNPACK: removed %d unfinished unpack folder(s) in %s" % (len(gone), target))
    return gone


# Systems whose games are folders full of data (or that aren't games at all):
# their empty folders can be part of a game — never offered for cleanup.
FOLDER_GAME_SYSTEMS = {"ps3", "psvita", "xbox360", "wiiu", "switch", "windows", "windows_installers",
                       "steam", "flatpak", "ports", "pygame", "lutris", "epic", "heroic", "vita3k"}


# files an OS or file manager leaves in a folder by itself: a folder of only these is empty
OS_JUNK = {"thumbs.db", "ehthumbs.db", ".ds_store", "desktop.ini", ".directory"}
# rb_chd's cleaned cue copy (rb_chd.CHDMAN_CUE): a killed run's is removed by the next compression
CHDMAN_CUE = ".rb-chdman-"


def os_junk_only(d):
    """{name: size} of what's in folder d when it holds nothing but OS_JUNK
    files and macOS "._" files ({} when empty); None when it holds anything
    else (a game's files, a manual, a cover, a save, a subfolder) or can't be read."""
    try:
        names = os.listdir(d)
        out = {}
        for n in names:
            st = os.lstat(os.path.join(d, n))
            if not stat.S_ISREG(st.st_mode) or not (n.lower() in OS_JUNK or n.startswith("._")):
                return None
            out[n] = st.st_size
        return out
    except OSError:
        return None


def _leftover_folder(sysdir, d):
    """An empty folder RomM-o-Cera could have left: a game folder directly in
    the system folder, or a "(Disc N)" folder a CHD conversion emptied —
    never a folder inside a game's own data (".ps3" etc.)."""
    if os.path.splitext(d)[1].lower() in (".ps3", ".xbox360", ".wiiu", ".pc", ".psvita", ".nsp"):
        return False
    rel = os.path.relpath(d, sysdir).split(os.sep)
    if any(os.path.splitext(x)[1].lower() in (".ps3", ".xbox360", ".wiiu", ".pc", ".psvita") for x in rel):
        return False
    return len(rel) == 1 or bool(_library.DISC_RE.search(os.path.basename(d)))


# an archive's single top entry that may never land directly in roms/<system>/
ARCHIVE_RESERVED_TOPS = {"", ".", "gamelist.xml", "_info.txt"} | _core.MEDIA_DIRS
# unfinished outputs of the converters other than chdman (whose .chd.part is listed on its own),
# and the ISO a .cso is decoded to
CONVERT_PARTS = tuple(dict.fromkeys([e + ".part" for c in _convert.CONVERTERS if not c.chd for e in c.outputs] +
                                    [e + ".part" for e in _convert.Z3DS_EXTS] + [_discs.CSO_TEMP_SUFFIX]))


# a converted game folder (PS3): never looked inside; its unfinished one is a leftover
FOLDER_OUTPUTS = tuple(e for c in _convert.CONVERTERS for e in c.outputs if e == ".ps3")
# what can sit beside a converted copy of the same game: originals, archives, loose tracks (never a
# .sbi/.sub: COPY_SIDECARS)
COPY_SOURCES = tuple(dict.fromkeys(list(_core.ARCHIVE_EXTS) + [e for c in _convert.CONVERTERS for e in c.sources + c.renamed_sources]
                                   + [".bin", ".img", ".raw"]))
COPY_OUTPUTS = tuple(dict.fromkeys(e for c in _convert.CONVERTERS for e in c.outputs))
_TRACK = re.compile(r"\s*\(track \d+\)$", re.I)


def _converted_copy(name, here):
    """The converted game in the same folder that the file name is another copy of
    ("X.7z" beside "X.rvz", "X (Track 2).bin" beside "X.chd", "X.iso" beside
    "X.iso.squashfs" or "X.ps3"), from here: {lower name: name}; else None."""
    stem, ext = os.path.splitext(name)
    if ext.lower() not in COPY_SOURCES or ext.lower() in COPY_SIDECARS:
        return None
    for base in dict.fromkeys((stem, _TRACK.sub("", stem))):
        for out in [base + e for e in COPY_OUTPUTS] + [name + ".squashfs"]:
            hit = here.get(out.lower())
            if hit and hit.lower() != name.lower():
                return hit
    return None


# ---- proving a converted copy whole and the same game before its original is offered

CHD_MAGIC = b"MComprHD"
CHD_V5_HEADER = 124
CHD_META_MAX = 4096             # metadata entries walked at most (a CD has one per track)
# bytes of a CD track's sector as its .bin holds it (chdman's cdrom track types)
CD_SECTOR_BYTES = {"MODE1": 2048, "MODE1/2048": 2048, "MODE1_RAW": 2352, "MODE1/2352": 2352,
                   "MODE2": 2336, "MODE2/2336": 2336, "MODE2_FORM1": 2048, "MODE2/2048": 2048,
                   "MODE2_FORM2": 2324, "MODE2/2324": 2324, "MODE2_FORM_MIX": 2336,
                   "MODE2_RAW": 2352, "MODE2/2352": 2352, "CDI/2352": 2352, "AUDIO": 2352}
CD_SUB_BYTES = 96               # a track stored with its subchannel (SUBTYPE RW / RW_RAW)
# a GD-ROM's (CHGD) FRAMES count the PAD frames its track files don't hold
_CD_TRACK_RE = re.compile(r"TRACK:(\d+) TYPE:(\S+) SUBTYPE:(\S+) FRAMES:(\d+)(?: PAD:(\d+))?")
CD_TRACK_FILES = (".bin", ".img", ".raw")
# never offered: a converted game still needs them beside it (rb_chd moves a PS1 LibCrypt
# patch, Game.sbi, next to the CHD), or they belong to a set the converter doesn't take (.sub)
COPY_SIDECARS = (".sbi", ".sub", ".lsd")


def chd_layout(path):
    """What a CHD holds, read from its own header and metadata, when the file is
    a whole standalone CHD v5: {"kind": "cd" | "dvd", "bytes": what its source
    image held (a CD: each track's frames x its sector size, as the .bin files
    hold them, a GD-ROM's padding left out; a DVD: the image's size), "tracks": tracks}. None when it isn't
    one or isn't whole: no "MComprHD" magic, not version 5 with its 124-byte
    header, a parent CHD it needs, a hunk map or metadata past the end of the
    file (chdman writes the compressed map after every hunk, so a CHD cut short
    loses it), an uncompressed map pointing past the end, sizes that disagree, or
    no CD track / DVD tag. Its data is checked by chdman verify (_chd_verified)."""
    try:
        with open(path, "rb") as f:
            end = os.fstat(f.fileno()).st_size
            h = f.read(CHD_V5_HEADER)
            if len(h) < CHD_V5_HEADER or h[:8] != CHD_MAGIC:
                return None
            hlen, version = struct.unpack(">II", h[8:16])
            if hlen != CHD_V5_HEADER or version != 5 or any(h[104:124]):
                return None                      # another version, or a delta CHD that needs its parent
            compressed = any(h[16:20])
            logical, mapoff, metaoff = struct.unpack(">QQQ", h[32:56])
            hunk, unit = struct.unpack(">II", h[56:64])
            if not logical or not hunk or not unit or hunk % unit or mapoff < hlen or mapoff >= end:
                return None
            hunks = -(-logical // hunk)
            f.seek(mapoff)
            if compressed:
                mh = f.read(16)
                if len(mh) < 16:
                    return None
                maplen, first = int.from_bytes(mh[:4], "big"), int.from_bytes(mh[4:10], "big")
                if not maplen or mapoff + 16 + maplen > end or not hlen <= first <= mapoff:
                    return None
            else:
                raw = f.read(4 * hunks)
                if len(raw) != 4 * hunks:
                    return None
                last = max(struct.unpack(">%dI" % hunks, raw), default=0)
                if (last + 1) * hunk > end:
                    return None
            tracks, dvd, seen, at = [], False, 0, metaoff
            while at:
                if at < hlen or at + 16 > end or seen >= CHD_META_MAX:
                    return None
                f.seek(at)
                e = f.read(16)
                n = int.from_bytes(e[5:8], "big")
                if len(e) < 16 or at + 16 + n > end:
                    return None
                tag, data = e[:4], f.read(n)
                if tag in (b"CHT2", b"CHTR", b"CHGD"):
                    m = _CD_TRACK_RE.search(data.rstrip(b"\0").decode("ascii", "replace"))
                    if not m:
                        return None
                    pad = int(m.group(5) or 0)
                    if pad > int(m.group(4)):
                        return None
                    tracks.append((m.group(2), m.group(3), int(m.group(4)), pad))
                elif tag == b"DVD ":
                    dvd = True
                at, seen = int.from_bytes(e[8:16], "big"), seen + 1
    except (OSError, struct.error):
        return None
    if dvd and not tracks:
        return {"kind": "dvd", "bytes": logical, "tracks": 1}
    if not tracks or dvd or unit != 2448:
        return None                              # a hard disk, a laserdisc, or both: not a disc copy
    frames, size = 0, 0
    for kind, sub, n, pad in tracks:
        if kind not in CD_SECTOR_BYTES:
            return None
        frames += n
        size += (n - pad) * (CD_SECTOR_BYTES[kind] + (CD_SUB_BYTES if sub != "NONE" else 0))
    # chdman pads each track to 4 frames: the logical size holds every frame, and not much more
    if not frames * unit <= logical <= (frames + 4 * len(tracks)) * unit:
        return None
    return {"kind": "cd", "bytes": size, "tracks": len(tracks)}


_CHD_VERIFIED = {}                  # (path, size, mtime_ns) -> chdman verify passed (only definite answers)
_CHD_VERIFY_LOCK = threading.Lock()  # one chdman verify at a time (On this box and Free up space both look)
# s: a verify that reports no progress for this long proves nothing (chdman spins at 0% for good on
# some damaged hunks; a healthy one reports every few seconds even on a slow drive)
CHD_VERIFY_STALL = 60
CHD_VERIFY_CAP = 6 * 3600


def _chd_verified(path):
    """chdman verify passes on the CHD (its data matches the SHA-1 in its
    header). Without chdman here: True (its header and map were checked).
    Remembered per (path, size, mtime) for the life of the process, a verify
    that stalls too (False: it proves nothing, and would stall again); one the
    app quitting stopped, or that couldn't start: False, not remembered."""
    exe = shutil.which("chdman")
    if not exe:
        return True
    try:
        st = os.stat(path)
    except OSError:
        return False
    key = (os.path.normpath(path), st.st_size, st.st_mtime_ns)
    with _CHD_VERIFY_LOCK:
        if key not in _CHD_VERIFIED:
            try:
                rc = _convert.run_check_tool(["nice", "-n", "19", exe, "verify", "-i", path], None,
                                             timeout=CHD_VERIFY_CAP, stall=CHD_VERIFY_STALL)[0]
            except (OSError, _convert.CheckStopped):
                return False
            if rc in (126, 127):
                return False                     # nice couldn't run it
            _CHD_VERIFIED[key] = rc == 0
            if rc is None:
                _core.log("LEFTOVERS: chdman verify of %s made no progress for %d s: its originals aren't "
                          "offered" % (path, CHD_VERIFY_STALL))
            elif rc:
                _core.log("LEFTOVERS: %s fails chdman verify: its originals aren't offered" % path)
        return _CHD_VERIFIED[key]


def _copy_zip_members(path):
    """[(lower-case member name, size)] of the files in a zip; None if it can't be read."""
    try:
        with zipfile.ZipFile(path) as z:
            return [(i.filename.replace("\\", "/").lower(), i.file_size) for i in z.infolist() if not i.is_dir()]
    except (OSError, zipfile.BadZipFile, ValueError, NotImplementedError):
        return None


def _copy_image_bytes(path, exts):
    """Bytes of the one disc or cartridge image path is (its extension in exts),
    or the zip holds (exactly one member with such an extension); a .cso: the
    ISO it decodes to. None when that can't be told (a 7z, a rar, several images)."""
    low = path.lower()
    try:
        if low.endswith(".cso") and ".cso" in exts:
            return _discs.cso_info(path)["total"]
        if low.endswith(exts):
            return os.path.getsize(path)
    except (OSError, _discs.CsoError):
        return None
    if low.endswith(".zip"):
        hit = [s for n, s in _copy_zip_members(path) or [] if n.endswith(exts)]
        return hit[0] if len(hit) == 1 else None
    return None


def _copy_cd_bytes(path, here, dp):
    """Bytes of the CD image a file belongs to, as its track files hold them:
    a .cue/.gdi's tracks (all there); a track file: the sheet beside it that
    lists it, else every loose track of its name; a zip: its tracks (one sheet
    in it at most); an .iso: itself. None when that can't be told."""
    low = path.lower()
    stem, ext = os.path.splitext(os.path.basename(path))
    try:
        if low.endswith((".cue", ".gdi")):
            tracks = list(dict.fromkeys(os.path.normpath(t) for t in _discs.sheet_sources(path, strict=True)))
            if not tracks or not all(os.path.isfile(t) for t in tracks):
                return None
            return sum(os.path.getsize(t) for t in tracks)
        if low.endswith(CD_TRACK_FILES):
            base = _TRACK.sub("", stem)
            for sh in (base + ".cue", base + ".gdi"):
                hit = here.get(sh.lower())
                if hit:
                    sheet = os.path.join(dp, hit)
                    listed = {os.path.normpath(t).lower() for t in _discs.sheet_sources(sheet, strict=True)}
                    if os.path.normpath(path).lower() in listed:
                        return _copy_cd_bytes(sheet, here, dp)
                    return None                  # a sheet of that name that doesn't list it: can't tell
            loose = [n for k, n in here.items() if k.endswith(CD_TRACK_FILES)
                     and _TRACK.sub("", os.path.splitext(n)[0]).lower() == base.lower()]
            return sum(os.path.getsize(os.path.join(dp, n)) for n in loose)
        if low.endswith(".iso"):
            return os.path.getsize(path)
    except OSError:
        return None
    if low.endswith(".zip"):
        members = _copy_zip_members(path)
        if members is None or sum(1 for n, _ in members if n.endswith((".cue", ".gdi"))) > 1:
            return None
        sizes = [s for n, s in members if n.endswith(CD_TRACK_FILES)]
        return sum(sizes) if sizes else None
    return None


def _converted_ok(path, orig, here, dp, checked):
    """The converted game at path is proven whole and the same game as orig (a
    file beside it), so orig may be offered as its copy. A CHD: chd_layout
    (header, map, metadata), the bytes its tracks or DVD image held within a
    sector a track of orig's (track files, cue/gdi tracks, the ISO, a .cso's
    ISO, a zip's), then chdman verify. An RVZ, .zcci/.zcxi, .wux, X.iso.squashfs:
    the format's own check that the file is whole (Check games' quick one), and
    the disc or cartridge size it holds equal to orig's. A .zar, .wua: their
    footer; a .ps3 folder: what RPCS3 boots it by (neither keeps the original's
    size). Anything else, or a check that can't run: False. Memoised in checked."""
    key = (path, orig)
    if key in checked:
        return checked[key]
    ok = False
    low = path.lower()
    try:
        if os.path.isdir(path):
            ok = low.endswith(".ps3") and _ps3.ps3_folder_check(path) is None
        elif low.endswith(".chd"):
            lay = chd_layout(path)
            if lay is not None:
                want = _copy_cd_bytes(orig, here, dp) if lay["kind"] == "cd" else _copy_image_bytes(orig, (".iso", ".cso"))
                slack = lay["tracks"] * 2352 if lay["kind"] == "cd" else 0
                ok = want is not None and abs(want - lay["bytes"]) <= slack and _chd_verified(path)
        elif low.endswith(".rvz"):
            hdr = _convert.rvz_header(path)
            ok = bool(hdr and hdr["complete"]) and hdr["size"] == _copy_image_bytes(orig, (".iso", ".gcm"))
        elif low.endswith(_convert.Z3DS_EXTS):
            hdr = _convert.z3ds_header(path)
            ok = bool(hdr and hdr["complete"]) and hdr["size"] == _copy_image_bytes(orig, tuple(_convert.Z3DS_SOURCES))
        elif low.endswith(".wux"):
            want = _copy_image_bytes(orig, (".wud",))
            ok = want is not None and _wiiu.wux_layout(path) == want
        elif low.endswith(".squashfs"):
            hdr = _convert.squashfs_header(path)
            inner = os.path.basename(path)[:-len(".squashfs")]
            want = _copy_image_bytes(orig, (".iso",))
            if hdr and hdr["complete"] and want and inner.lower().endswith(".iso"):
                listing = _convert.squashfs_listing(path) or {}
                size, link = listing.get(inner, (None, "missing"))
                ok = link is None and (size == want or want - size in _core.XBOX_PARTITIONS)
        elif low.endswith((".zar", ".wua")):
            ok = _convert.zar_check(path) is None
    except (OSError, _convert.NotVerified, _convert.CheckStopped, _wiiu.WiiuError, ValueError):
        ok = False
    checked[key] = ok
    return ok


def _copy_refs(path):
    """Lower-case normalised paths a playlist or sheet in the games folder
    points at: an .m3u's discs, a .cue/.gdi's tracks, a CloneCD .ccd's image
    and subchannel (named after it). None when it can't be read."""
    low = path.lower()
    try:
        if low.endswith(".m3u"):
            got = _es.m3u_paths(path)
        elif low.endswith((".cue", ".gdi")):
            got = _discs.sheet_sources(path, strict=True)
        elif low.endswith(".ccd"):
            got = [os.path.splitext(path)[0] + e for e in (".img", ".sub")]
        else:
            return []
    except OSError:
        return None
    return {os.path.normpath(p).lower() for p in got}


def _copy_unreferenced(cands, refs):
    """The candidates (lower-case path -> entry) no playlist or sheet still
    points at, unless that playlist or sheet goes too: deleting a disc an .m3u
    lists, or a track another game's .cue shares, breaks that game. Repeated
    until nothing changes (a sheet kept keeps its tracks)."""
    offered = dict(cands)
    while True:
        held = set()
        for r, got in refs.items():
            if r not in offered:
                held |= got & offered.keys()
        if not held:
            return offered
        for k in held:
            del offered[k]


def find_leftovers(pending_roms=(), chd_busy=None):
    """Files and folders interrupted downloads/conversions left behind, and
    copies of games already here converted: [{"path", "kind", "size"}, plus
    "note" naming the converted game for a copy]. Never a partial download
    that's still queued or paused (it resumes from it), never a CHD being
    written, never a file a queued conversion will use, never a copy whose
    converted game isn't proven whole and the same game (_converted_ok), never
    one a playlist or sheet still points at (_copy_unreferenced), never a .sbi,
    nothing inside a converted game folder (X.ps3)."""
    busy = chd_worker_busy() if chd_busy is None else chd_busy
    queued, sure = set(), not busy               # sure: we know every source a queued task will use
    checked = {}
    if sure:
        try:
            tasks = [t for t in os.listdir(_core.CHD_QUEUE_DIR) if t.endswith(".json")]
        except FileNotFoundError:
            tasks = []
        except OSError:
            tasks, sure = [], False
        for t in tasks:
            try:
                with open(os.path.join(_core.CHD_QUEUE_DIR, t)) as f:
                    queued.update(os.path.normpath(x) for x in json.load(f).get("sources") or [])
            except FileNotFoundError:
                continue                             # done meanwhile
            except (OSError, ValueError, AttributeError, TypeError):
                sure = False                         # can't tell what's queued: no copies offered
    keep, pending = set(), set()
    for rom, system in pending_roms:
        dest = _core.rom_dest(rom, system)
        if dest:
            keep.add(os.path.normpath(dest))
        pending.add((None if rom.get("id") is None else str(rom.get("id")), system))
    recorded = recorded_paths()                  # None: can't tell, so no unfinished move is offered
    out = []

    def kept(p):
        q = os.path.normpath(p)
        return any(q == k or q == k + ".part" or q.startswith(k + os.sep) for k in keep)
    try:
        systems = sorted(os.listdir(_core.ROMS_ROOT))
    except OSError:
        systems = []
    for system in systems:
        sysdir = os.path.join(_core.ROMS_ROOT, system)
        if not os.path.isdir(sysdir) or os.path.islink(sysdir):
            continue
        # copies only where a converter works (arcade sets keep game.zip beside game.chd)
        copies = bool(_convert.converters_for(system)) and sure
        cands, refs = {}, {}                     # copies offered once no playlist or sheet still needs them
        for dp, dns, fns in os.walk(sysdir, topdown=False):
            parts = os.path.relpath(dp, sysdir).split(os.sep)
            if any(part.startswith(UNPACK_STAGE) for part in parts):
                continue                                 # inside an unpack folder: offered whole (below)
            if any(part in _core.MEDIA_DIRS or part.lower().endswith(FOLDER_OUTPUTS)
                   or part.lower().endswith(tuple(e + ".part" for e in FOLDER_OUTPUTS)) for part in parts):
                continue                                 # inside a game folder (or a half-made one)
            for dn in dns:
                p = os.path.join(dp, dn)
                if dn.startswith(UNPACK_STAGE) and not os.path.islink(p) and stage_stale(p):
                    out.append({"path": p, "kind": "Unfinished unpack", "size": _core.path_size(p)})
                    continue
                if dn.lower().endswith(tuple(e + ".part" for e in FOLDER_OUTPUTS)) and not busy \
                        and not os.path.islink(p) and not kept(p):
                    out.append({"path": p, "kind": "Unfinished conversion", "size": _core.path_size(p)})
            here = {n.lower(): n for n in fns + dns}
            for fn in fns:
                p = os.path.join(dp, fn)
                low = fn.lower()
                if fn.startswith(CHDMAN_CUE) and low.endswith(".cue"):
                    continue                             # a killed compression's cue copy: the next one removes it
                if fn.startswith(MOVE_MARKER):
                    it = _unfinished_move(p, sysdir, system, pending, recorded)
                    if it:
                        out.append(it)
                    continue
                if copies and low.endswith((".m3u", ".cue", ".gdi", ".ccd")):
                    got = _copy_refs(p)
                    if got is None:
                        copies = False                   # a playlist or sheet that can't be read: no copies here
                    else:
                        refs[os.path.normpath(p).lower()] = got
                copy = _converted_copy(fn, here) if copies and not kept(p) and os.path.normpath(p) not in queued \
                    else None
                if copy and not _converted_ok(os.path.join(dp, copy), p, here, dp, checked):
                    copy = None                          # not proven whole and the same game: keep this
                if copy:
                    try:
                        cands[os.path.normpath(p).lower()] = {"path": p, "kind": "Copy of a converted game",
                                                              "size": os.path.getsize(p),
                                                              "note": "Already here as %s" % copy,
                                                              "converted": os.path.join(dp, copy)}
                    except OSError:
                        pass
                    continue
                if low.endswith((".chd.part", ".tmp.chd")):
                    kind = "Unfinished CHD"
                    if busy:
                        continue
                elif low.endswith(CONVERT_PARTS):
                    kind = "Unfinished conversion"
                    if busy:
                        continue
                elif low.endswith(".part"):
                    kind = "Partial download"
                elif low.endswith(".tmp") and fn.startswith("."):
                    kind = "Temporary file"
                else:
                    continue
                if kept(p):
                    continue
                try:
                    out.append({"path": p, "kind": kind, "size": os.path.getsize(p)})
                except OSError:
                    pass
            if dp != sysdir and not kept(dp) and system not in FOLDER_GAME_SYSTEMS \
                    and _leftover_folder(sysdir, dp):
                junk = os_junk_only(dp)
                if junk is not None:
                    out.append({"path": dp, "kind": "Empty folder", "size": sum(junk.values())})
        if copies:
            out += _copy_unreferenced(cands, refs).values()
    tmp = _core.tmp_dir()
    if os.path.isdir(tmp) and not busy:
        for n in sorted(os.listdir(tmp)):
            p = os.path.join(tmp, n)
            out.append({"path": p, "kind": "RomM-o-Cera scratch", "size": _core.path_size(p)})
    moved = {it["path"] for it in out if it.get("marker")}       # not listed twice (an empty one)
    return [it for it in out if it.get("marker") or it["path"] not in moved]


def _unfinished_move(marker, sysdir, system, pending, recorded):
    """The find_leftovers entry for a move marker whose unpack was cut off while
    moving its files in, with nothing left to finish it: its archive is gone (a
    cancel deleted it, or it was never the app's to keep), no pending download
    of that game will unpack it again, and installed.json doesn't record the
    landing path (then it's that game, recorded: see drop_unfinished_move for why
    deleting the rest is safe). "path" is where it landed. None otherwise."""
    m = read_move_marker(marker)
    if m is None or recorded is None or m.get("moved") is True or m.get("keep"):
        return None
    if os.path.lexists(m["archive"]) or (m.get("rom"), system) in pending:
        return None
    lands = os.path.normpath(m["lands"])
    if lands == os.path.normpath(sysdir) or not _core._inside(lands, sysdir) or path_overlaps(lands, recorded):
        return None
    size = _core.path_size(lands) if os.path.lexists(lands) else 0
    return {"path": lands, "kind": "Unfinished unpack", "size": size, "marker": marker}


def delete_leftovers(items):
    """Delete find_leftovers() entries; returns bytes freed. A copy of a
    converted game is checked again first, all of them before any is deleted
    (a track's check reads its sheet): the converted game may have changed since."""
    freed = 0
    still, checked = set(), {}
    for it in items:
        conv, p = it.get("converted"), it["path"]
        if it["kind"] != "Copy of a converted game" or not conv or os.path.dirname(conv) != os.path.dirname(p):
            continue
        try:
            here = {n.lower(): n for n in os.listdir(os.path.dirname(p))}
        except OSError:
            continue
        if _converted_ok(conv, p, here, os.path.dirname(p), checked):
            still.add(id(it))
    for it in items:
        p = it["path"]
        if not (_core._inside(p, _core.ROMS_ROOT) or _core._inside(p, _core.DATA_DIR)):
            continue
        if it.get("marker"):                         # a half-moved game: checked again, then it and its marker
            mk = it["marker"]
            sysdir = os.path.join(_core.ROMS_ROOT, os.path.relpath(mk, _core.ROMS_ROOT).split(os.sep)[0])
            m = read_move_marker(mk)
            if m is None or os.path.normpath(m["lands"]) != os.path.normpath(p) or m.get("moved") is True \
                    or os.path.lexists(m["archive"]) or not _core._inside(mk, _core.ROMS_ROOT):
                continue
            got = drop_unfinished_move(mk, sysdir)
            freed += got or 0
            continue
        if it["kind"] == "Unfinished unpack" and not stage_stale(p):
            continue                                 # an unpack started there since (a thread id came back)
        if it["kind"] == "Copy of a converted game" and id(it) not in still:
            continue
        try:
            if os.path.isdir(p) and not os.path.islink(p):
                if it["kind"] == "Empty folder":
                    junk = os_junk_only(p)           # only if still empty, or only the system's own files
                    if junk is None:
                        continue
                    for n in junk:
                        os.remove(os.path.join(p, n))
                    os.rmdir(p)
                else:
                    shutil.rmtree(p)
            elif os.path.lexists(p):
                os.remove(p)
            freed += it.get("size") or 0
        except OSError:
            continue
        d = os.path.dirname(p)                          # a game folder only the leftover kept
        while _core._inside(d, _core.ROMS_ROOT) and os.path.dirname(d) != os.path.normpath(_core.ROMS_ROOT) \
                and os.path.isdir(d) and not os.listdir(d):
            os.rmdir(d)
            d = os.path.dirname(d)
    _core.log("LEFTOVERS: removed %d (%s)" % (len(items), _core.human_size(freed)))
    return freed
