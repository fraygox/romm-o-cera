"""Leftovers: files a download or conversion left behind."""
import json
import os
import re
import shutil

from . import core as _core, discs as _discs, ps3 as _ps3, convert as _convert, library as _library


# ---------------------------------------------------------- leftovers

def chd_worker_busy():
    """True while rb_chd.py holds its lock (a conversion may be writing)."""
    try:
        with _core.try_lock(_core.chd_lock_path()) as fd:
            return fd is None
    except OSError:
        return False


# Systems whose games are folders full of data (or that aren't games at all):
# their empty folders can be part of a game — never offered for cleanup.
FOLDER_GAME_SYSTEMS = {"ps3", "psvita", "xbox360", "wiiu", "switch", "windows", "windows_installers",
                       "steam", "flatpak", "ports", "pygame", "lutris", "epic", "heroic", "vita3k"}


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
# what can sit beside a converted copy of the same game: originals, archives, loose tracks
COPY_SOURCES = tuple(dict.fromkeys(list(_core.ARCHIVE_EXTS) + [e for c in _convert.CONVERTERS for e in c.sources + c.renamed_sources]
                                   + [".bin", ".img", ".sub", ".sbi", ".raw"]))
COPY_OUTPUTS = tuple(dict.fromkeys(e for c in _convert.CONVERTERS for e in c.outputs))
_TRACK = re.compile(r"\s*\(track \d+\)$", re.I)


def _converted_copy(name, here):
    """The converted game in the same folder that the file name is another copy of
    ("X.7z" beside "X.rvz", "X (Track 2).bin" beside "X.chd", "X.iso" beside
    "X.iso.squashfs" or "X.ps3"), from here: {lower name: name}; else None."""
    stem, ext = os.path.splitext(name)
    if ext.lower() not in COPY_SOURCES:
        return None
    for base in dict.fromkeys((stem, _TRACK.sub("", stem))):
        for out in [base + e for e in COPY_OUTPUTS] + [name + ".squashfs"]:
            hit = here.get(out.lower())
            if hit and hit.lower() != name.lower():
                return hit
    return None


def _converted_ok(path, checked):
    """A converted game that looks whole: a game folder with its EBOOT.BIN, an
    image or CHD that isn't cut short (disc_incomplete). Memoised in checked."""
    if path not in checked:
        if os.path.isdir(path):
            checked[path] = os.path.isfile(os.path.join(path, *_ps3.PS3_EBOOT.split("/")))
        else:
            try:
                checked[path] = os.path.getsize(path) > 0 and not _discs.disc_incomplete(path)
            except OSError:
                checked[path] = False
    return checked[path]


def find_leftovers(pending_roms=(), chd_busy=None):
    """Files and folders interrupted downloads/conversions left behind, and
    copies of games already here converted: [{"path", "kind", "size"}, plus
    "note" naming the converted game for a copy]. Never a partial download
    that's still queued or paused (it resumes from it), never a CHD being
    written, never a file a queued conversion will use, never a copy whose
    converted game looks broken, nothing inside a converted game folder (X.ps3)."""
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
    keep = set()
    for rom, system in pending_roms:
        dest = _core.rom_dest(rom, system)
        if dest:
            keep.add(os.path.normpath(dest))
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
        for dp, dns, fns in os.walk(sysdir, topdown=False):
            parts = os.path.relpath(dp, sysdir).split(os.sep)
            if any(part in _core.MEDIA_DIRS or part.lower().endswith(FOLDER_OUTPUTS)
                   or part.lower().endswith(tuple(e + ".part" for e in FOLDER_OUTPUTS)) for part in parts):
                continue                                 # inside a game folder (or a half-made one)
            for dn in dns:
                p = os.path.join(dp, dn)
                if dn.lower().endswith(tuple(e + ".part" for e in FOLDER_OUTPUTS)) and not busy \
                        and not os.path.islink(p) and not kept(p):
                    out.append({"path": p, "kind": "Unfinished conversion", "size": _core.path_size(p)})
            here = {n.lower(): n for n in fns + dns}
            for fn in fns:
                p = os.path.join(dp, fn)
                low = fn.lower()
                copy = _converted_copy(fn, here) if copies and not kept(p) and os.path.normpath(p) not in queued \
                    else None
                if copy and not _converted_ok(os.path.join(dp, copy), checked):
                    copy = None                          # the converted one may be the broken copy: keep this
                if copy:
                    try:
                        out.append({"path": p, "kind": "Copy of a converted game", "size": os.path.getsize(p),
                                    "note": "Already here as %s" % copy})
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
                try:
                    if not os.listdir(dp):
                        out.append({"path": dp, "kind": "Empty folder", "size": 0})
                except OSError:
                    pass
    tmp = _core.tmp_dir()
    if os.path.isdir(tmp) and not busy:
        for n in sorted(os.listdir(tmp)):
            p = os.path.join(tmp, n)
            out.append({"path": p, "kind": "RomM-o-Cera scratch", "size": _core.path_size(p)})
    return out


def delete_leftovers(items):
    """Delete find_leftovers() entries; returns bytes freed."""
    freed = 0
    for it in items:
        p = it["path"]
        if not (_core._inside(p, _core.ROMS_ROOT) or _core._inside(p, _core.DATA_DIR)):
            continue
        try:
            if os.path.isdir(p) and not os.path.islink(p):
                if it["kind"] == "Empty folder":
                    os.rmdir(p)                      # only if still empty
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
