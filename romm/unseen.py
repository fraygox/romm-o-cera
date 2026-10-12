"""Games EmulationStation can't see."""
import os
import subprocess
import zipfile

from . import (core as _core, discs as _discs, es as _es, ps3 as _ps3, convert as _convert,
               leftovers as _leftovers, downloader as _downloader)


# ------------------------------------------------- games ES can't see

# EmulationStation lists only files whose extension is in the system's
# <extension> list (es_systems.cfg); anything else in roms/<system>/ is
# silently ignored. find_unseen() names those files and why.
SYSTEM_LABELS = {"psx": "PlayStation", "ps2": "PS2", "ps3": "PS3", "psp": "PSP", "psvita": "PS Vita",
                 "gamecube": "GameCube", "wii": "Wii", "wiiu": "Wii U", "n64": "N64", "nds": "DS",
                 "3ds": "3DS", "switch": "Switch", "gb": "Game Boy", "gbc": "Game Boy Color",
                 "gba": "GBA", "nes": "NES", "snes": "SNES", "megadrive": "Mega Drive",
                 "mastersystem": "Master System", "megacd": "Mega-CD", "saturn": "Saturn",
                 "dreamcast": "Dreamcast", "xbox": "Xbox", "xbox360": "Xbox 360",
                 "pcengine": "PC Engine", "pcenginecd": "PC Engine CD", "neogeocd": "Neo Geo CD",
                 "triforce": "Triforce"}
# never games, whatever folder they sit in: notes, pictures, saves, Batocera's pad configs
UNSEEN_IGNORE = (".txt", ".nfo", ".diz", ".md", ".pdf", ".xml", ".htm", ".html", ".jpg", ".jpeg",
                 ".png", ".gif", ".bmp", ".webp", ".srm", ".sav", ".state", ".ini", ".log", ".db",
                 ".keys", ".p2k.cfg", ".sfv", ".md5", ".sha1", ".url", ".lnk")
# loose disc-image parts named like the game ES lists beside them (Game.ccd + Game.img/.sub)
UNSEEN_COMPANIONS = {".bin", ".img", ".sub", ".sbi", ".mdf", ".ecm"}
# housekeeping, never games: backups (gamelist.xml.bak-…, X.bak-gdi), parked or half-done files
UNSEEN_HOUSEKEEPING = (".disabled", ".sqlite", ".torrent", ".part", ".tmp", ".tmp.chd")
# launchers that list almost anything: never a reason to say "this belongs elsewhere"
UNSEEN_CATCH_ALL = {"ports", "pygame", "windows", "windows_installers", "steam", "flatpak", "lutris",
                    "epic", "heroic"}
UNSEEN_MAX_TO = 3      # .rvz: GameCube/Triforce/Wii; more (.bin, .iso) doesn't say which system
GC_MAGIC, WII_MAGIC = b"\xc2\x33\x9f\x3d", b"\x5d\x1c\x9e\xa3"


def system_label(system):
    return SYSTEM_LABELS.get(system, system)


def gc_or_wii(path):
    """'gamecube' / 'wii' from a plain disc image's header (.iso/.gcm), else None."""
    try:
        with open(path, "rb") as f:
            h = f.read(0x20)
    except OSError:
        return None
    if h[0x1C:0x20] == GC_MAGIC:
        return "gamecube"
    if h[0x18:0x1C] == WII_MAGIC:
        return "wii"
    return None


def _unseen_why(kind, system, ext, to, path=None):
    if kind == "archive" and system == "ps3" and ext == ".zip" and path and _ps3.ps3_zip_is_disc(path):
        try:
            need = _ps3.ps3_check(path)
        except _ps3.Ps3Error as e:
            return "PS3 disc in a zip: %s" % e
        except (OSError, zipfile.BadZipFile):
            return "PS3 disc in a zip that can't be read"
        return "PS3 disc in a zip: Unpack makes it a game folder%s" % (
            "" if need["plain"] else " (its disc key is here)")
    if kind == "archive":
        return "%s doesn't list %s — ES can't see it" % (system_label(system), ext)
    if kind == "wrong":
        names = "/".join([system_label(s) for s in to[:3]] + (["…"] if len(to) > 3 else []))
        what = "image" if ext in _core.DISC_EXTS else "game"
        return "%s %s %s in %s/" % ("an" if names[:1] in "AEIOU" else "a", names, what, system)
    if to:                               # "unknown" but other systems list it: too generic to pick one
        return "%s doesn't list %s — ES can't list it here" % (system_label(system), ext)
    return "ES can't list this file: no system takes %s" % ext


def find_unseen(es, cancel=None):
    """Files in roms/<system>/ EmulationStation silently ignores: [{"system",
    "path", "kind", "ext", "to", "size", "why"}], by system then path.
    kind: "archive" (a .zip/.7z/.rar the system doesn't list: unpack it),
    "wrong" (a loose file in the system folder whose extension at most three
    other systems list: move it to one of "to"), "unknown" (no system lists it, only launchers like ports do, or so
    many do that it says nothing: .bin, .iso). Backups (.bak…), parked and
    numbered files are housekeeping, not findings. Local only, no hashing. Skipped: media
    folders, gamelist.xml, hidden and unfinished files, notes/pictures/saves,
    whatever ES lists (a folder ES lists as a game isn't entered), the tracks
    and discs a sheet or playlist names, a sheet whose tracks ES lists, the
    other files of a game folder that holds a game ES lists, and the inside of
    game folders on folder-game systems (PS3, ports…)."""
    out = []
    try:
        systems = sorted(os.listdir(_core.ROMS_ROOT))
    except OSError:
        return out
    for system in systems:
        exts = es.get(system)
        sysdir = os.path.join(_core.ROMS_ROOT, system)
        if exts is None or not os.path.isdir(sysdir) or os.path.islink(sysdir):
            continue
        folders = system in _leftovers.FOLDER_GAME_SYSTEMS
        refs = set()
        for dp, dns, fns in os.walk(sysdir):
            if cancel is not None and cancel.is_set():
                return out
            top = dp == sysdir
            dns[:] = sorted(d for d in dns if not d.startswith(".") and d not in _core.MEDIA_DIRS
                            and not folders and os.path.splitext(d)[1].lower() not in exts
                            and not os.path.islink(os.path.join(dp, d)))
            names = [n for n in fns if not n.startswith(".")]
            listed = [n for n in names if os.path.splitext(n)[1].lower() in exts]
            if listed and not top:
                continue                         # a game folder: the rest are its own files
            sheets = {}
            for n in names:
                if n.lower().endswith((".cue", ".gdi", ".m3u")):
                    p = os.path.join(dp, n)
                    srcs = [os.path.normpath(q) for q in
                            (_es.m3u_paths(p) if n.lower().endswith(".m3u") else _discs.sheet_sources(p))]
                    sheets[n] = srcs
                    refs.update(srcs)
            stems = {os.path.splitext(n)[0].lower() for n in listed}
            for n in sorted(names):
                low = n.lower()
                ext = os.path.splitext(low)[1]
                if not ext or ext in exts or low in ("gamelist.xml", "_info.txt") or ".bak" in low \
                        or low.endswith(UNSEEN_IGNORE + UNSEEN_HOUSEKEEPING) or ext[1:].isdigit():
                    continue
                p = os.path.join(dp, n)
                if os.path.normpath(p) in refs or os.path.islink(p):
                    continue
                if ext in UNSEEN_COMPANIONS and os.path.splitext(low)[0] in stems:
                    continue
                if any(os.path.splitext(q)[1].lower() in exts for q in sheets.get(n, ())):
                    continue                     # ES lists its tracks (PS2 lists .bin, not .cue)
                if ext in _core.ARCHIVE_EXTS:
                    kind, to = "archive", []
                else:
                    to = sorted(s for s, e in es.items()
                                if s != system and s not in UNSEEN_CATCH_ALL and ext in e)
                    if {"gamecube", "wii"} <= set(to) and ext in (".iso", ".gcm"):
                        hw = gc_or_wii(p)
                        to = [hw] if hw else to
                    to.sort(key=lambda s: not os.path.isdir(os.path.join(_core.ROMS_ROOT, s)))   # on the box first
                    kind = "wrong" if top and 0 < len(to) <= UNSEEN_MAX_TO else "unknown"   # in a folder: part of a set
                try:
                    size = os.path.getsize(p)
                except OSError:
                    continue
                why = _unseen_why(kind, system, ext, to, p)
                out.append({"system": system, "path": p, "kind": kind, "ext": ext,
                            "to": to if kind == "wrong" else [], "size": size, "why": why})
    return out


def archive_contents(dl, path):
    """[(member name, size or None)] of a .zip/.7z/.rar, for the confirm."""
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            return [(i.filename, i.file_size) for i in z.infolist() if not i.is_dir()]
    out, name = [], None
    res = subprocess.run([dl._7z(), "l", "-slt", "-ba", path], capture_output=True, text=True,
                         errors="replace").stdout
    for line in res.splitlines() + [""]:
        if line.startswith("Path = "):
            name, size, folder = line[7:], None, False
        elif line.startswith("Size = ") and name is not None:
            size = int(line[7:]) if line[7:].strip().isdigit() else None
        elif line.startswith("Folder = +"):
            folder = True
        elif not line.strip() and name is not None:
            if not folder:
                out.append((name, size))
            name = None
    return out


def _recorded(library, paths):
    """True when an install record (ours: installed.json) holds one of paths."""
    recs = library.snapshot().values()
    return any(p in (rec.get("paths") or []) for rec in recs for p in paths)


def unpack_unseen(dl, finding):
    """Unpack an archive ES can't see exactly as after a download (the
    Downloader's safety checks, then the conversion it queues); the archive
    goes once it's unpacked. Returns (paths, conversion task name or None)."""
    path, system = finding["path"], finding["system"]
    sysdir = os.path.join(_core.ROMS_ROOT, system)
    if not os.path.isfile(path) or not _core._inside(path, sysdir):
        raise _core.RommError("%s isn't there any more" % os.path.basename(path))
    ext = os.path.splitext(path)[1].lower()
    if ext not in _core.ARCHIVE_EXTS or system in _core.ZIP_NATIVE or dl._accepts(system, ext):
        raise _core.RommError("ES lists %s files in %s/: nothing to unpack" % (ext, system))
    # not a RomM game: id None matches no install record, so "already on the
    # box under that name" refuses to unpack over any game that's there
    j = _downloader.Job({"id": None, "name": os.path.splitext(os.path.basename(path))[0],
             "fs_name": os.path.basename(path)}, system)
    ps3 = system == "ps3" and ext == ".zip" and _ps3.ps3_zip_is_disc(path) \
        and _convert.conversion_for(system, path, dl.cfg, dl._accepts) is not None
    if ps3:
        try:                                        # say now why it can't become a game folder
            _ps3.ps3_check(path, dl.cfg)
        except _ps3.Ps3Error as e:
            raise _core.RommError("%s: %s" % (os.path.basename(path), e)) from None
    out = dl._postprocess_file(j, path)
    if ps3 and j.chd_task:
        _core.log("UNSEEN queued %s for a PS3 game folder (%s)" % (path, j.chd_task))
        return out, j.chd_task                      # the worker reads the zip in place
    if os.path.exists(path):
        raise _core.RommError("%s wasn't unpacked" % os.path.basename(path))
    if _recorded(dl.library, [path]):
        dl.library.rename_paths({path: out[0]}, out[1:])              # it was ours: the record follows
    _core.append_gamelist({"system": system, "rename": {path: out[0]}})     # ES lists it after a reload
    _core.log("UNSEEN unpacked %s -> %s%s" % (path, out, " (queued %s)" % j.chd_task if j.chd_task else ""))
    return out, j.chd_task


def _move_file(src, dst):
    """Move without ever replacing dst: a hard link fails when dst exists.
    (Filesystems without hard links: rename, after checking dst again.)"""
    import errno
    try:
        os.link(src, dst)
    except OSError as e:
        if isinstance(e, FileExistsError) or e.errno == errno.EXDEV:
            raise
        if os.path.lexists(dst):
            raise FileExistsError(errno.EEXIST, "exists", dst) from None
        os.rename(src, dst)
        return
    os.unlink(src)


def move_unseen(finding, dest, library=None, es=None):
    """Move a game in the wrong system folder to roms/<dest>/ (same place
    inside it), with the tracks its .cue/.gdi or the discs its .m3u name.
    All or nothing, never over an existing file. An install record follows
    (and its game info); ES lists the game after a reload. Returns the new path."""
    src, system = os.path.normpath(finding["path"]), finding["system"]
    sysdir, dsys = os.path.join(_core.ROMS_ROOT, system), os.path.join(_core.ROMS_ROOT, dest)
    if dest == system or os.sep in dest or dest in ("", ".", "..") or (es is not None and dest not in es):
        raise _core.RommError("Can't move to %s" % dest)
    if not os.path.isfile(src) or not _core._inside(src, sysdir):
        raise _core.RommError("%s isn't there any more" % os.path.basename(src))
    if os.path.islink(dsys) or (os.path.exists(dsys) and not os.path.isdir(dsys)):
        raise _core.RommError("roms/%s isn't a plain folder; not moving into it" % dest)
    files = [src] + [p for p in _es.game_files(src) if p != src]
    moves = []
    for p in files:
        if not os.path.isfile(p) or os.path.islink(p):
            raise _core.RommError("%s isn't a plain file; not moving it" % os.path.basename(p))
        moves.append((p, os.path.join(dsys, os.path.relpath(p, sysdir))))
    for _, d in moves:
        if os.path.lexists(d):
            raise _core.RommError("%s is already in %s/; not moving over it" % (os.path.basename(d), dest))
    done = []
    try:
        for s, d in moves:
            os.makedirs(os.path.dirname(d), exist_ok=True)
            _move_file(s, d)
            done.append((s, d))
    except OSError as e:
        for s, d in reversed(done):                  # all or nothing
            try:
                _move_file(d, s)
            except OSError:
                pass
        if isinstance(e, FileExistsError):
            raise _core.RommError("Something is already in %s/ under that name; not moving over it" % dest) from None
        raise _core.RommError("Couldn't move %s (%s)" % (os.path.basename(src), e.strerror or e)) from None
    for d in sorted({os.path.dirname(s) for s, _ in moves}, key=len, reverse=True):
        while _core._inside(d, sysdir) and os.path.isdir(d) and not os.listdir(d):
            os.rmdir(d)
            d = os.path.dirname(d)
    rename = dict(moves)
    new = rename[src]
    entries = [{"system": dest, "rename": {src: new}}]
    if library is not None and _recorded(library, rename):
        library.rename_paths(rename)
        moved = set(rename.values())

        def resystem(inst):
            for rec in inst.values():
                ps = rec.get("paths") or []
                if rec.get("system") == system and ps and any(p in moved for p in ps) \
                        and all(_core._inside(p, dsys) for p in ps):
                    rec["system"] = dest
                    if isinstance(rec.get("meta"), dict):
                        rec["meta"] = dict(rec["meta"], system=dest, paths=list(ps))
                        entries.append(rec["meta"])          # its game info follows
        library.update(resystem)
        library.rescan(system)
        library.rescan(dest)
    _core.append_gamelist(*entries)
    _core.log("UNSEEN moved %s -> %s" % (src, new))
    return new


def delete_unseen(finding, library=None):
    """Delete a file ES can't see, with the tracks its .cue/.gdi or the discs
    its .m3u name (game_files: never outside its system folder). Saves and
    states live elsewhere and stay. Our install record forgets those files
    (and goes once none are left), and their gamelist entries go. Returns (paths, bytes freed)."""
    src, system = os.path.normpath(finding["path"]), finding["system"]
    sysdir = os.path.join(_core.ROMS_ROOT, system)
    if os.path.islink(src) or not os.path.isfile(src) or not _core._inside(src, sysdir):
        raise _core.RommError("%s isn't there any more" % os.path.basename(src))
    paths = [p for p in [src] + [q for q in _es.game_files(src) if q != src]
             if os.path.isfile(p) and not os.path.islink(p) and _core._inside(p, sysdir)]
    freed = 0
    for p in paths:
        try:
            size = os.path.getsize(p)
            os.remove(p)
        except FileNotFoundError:
            continue
        except OSError as e:
            raise _core.RommError("Couldn't delete %s (%s)" % (os.path.basename(p), e.strerror or e)) from None
        freed += size
    for d in sorted({os.path.dirname(p) for p in paths}, key=len, reverse=True):
        while _core._inside(d, sysdir) and os.path.isdir(d) and not os.listdir(d):
            os.rmdir(d)
            d = os.path.dirname(d)
    if library is not None and _recorded(library, paths):
        gone = set(paths)

        def forget(inst):
            for k, rec in list(inst.items()):
                ps = rec.get("paths") or []
                if gone & set(ps):
                    left = [p for p in ps if p not in gone]
                    if left:
                        rec["paths"] = left
                    else:
                        inst.pop(k)
        library.update(forget)
        library.rescan(system)
    _core.append_gamelist({"system": system, "remove": paths})
    _core.log("UNSEEN deleted %s (%s)" % (paths, _core.human_size(freed)))
    return paths, freed
