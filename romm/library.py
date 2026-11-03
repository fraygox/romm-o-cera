"""installed.json (Library), multi-disc sets and gamelist entries."""
import contextlib
import json
import os
import re
import shutil
import threading
import time

from . import core as _core, config as _config, es as _es, convert as _convert


# Filename tags that mark something other than a finished release. Matched as
# substrings against RomM's tag list, so "Proto 2", "Beta 1", "E3 demo" count.
# Homebrew, Aftermarket, Unlicensed stay visible on purpose.
JUNK_TAG_WORDS = ("proto", "beta", "alpha", "demo", "hack", "pirate", "sample", "kiosk",
                  "debug", "preview", "sampler", "bootleg")


def junk_tags(tags):
    return [t for t in tags if any(w in t.lower() for w in JUNK_TAG_WORDS)
            and "homebrew" not in t.lower()]


# ------------------------------------------------------- what counts as a game file

# Names of something on its way in or out, never a game: a download's .part, a
# converter's output while it's written (.chd.part, .cso-iso.part, .xiso.part,
# .ps3.part, chdman's .tmp.chd), a copy's .partial, an atomic write's X.tmp-<pid>-<tid>,
# an unpack's hidden .rb-extract-* folder (and any other hidden name: ES skips those).
TEMP_SUFFIXES = (".part", ".partial", ".tmp", ".tmp.chd")
_TEMP_RE = re.compile(r"\.tmp-\d+(?:-\d+)?$", re.I)
# Files beside a game that are never the game: saves and states (RetroArch can keep
# them beside the rom), images, videos, manuals, notes and checksums.
NOT_GAME_EXTS = frozenset({
    ".srm", ".sav", ".rtc", ".eep", ".fla", ".sra", ".mpk", ".dsv", ".mcr", ".p2s", ".sstate",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".mp4", ".avi", ".mkv", ".webm", ".pdf", ".cbz",
    ".txt", ".nfo", ".xml", ".md5", ".sfv", ".sha1", ".log", ".cfg", ".ini", ".url", ".bak"})
_STATE_RE = re.compile(r"\.state(?:\d+|\.auto)?$", re.I)        # .state, .state1, .state.auto
# folders named like a file ("Game.ps3"): the folder "extensions" ES lists as one game
FOLDER_GAME_EXTS = frozenset({".ps3", ".xbox360", ".wiiu", ".pc", ".psvita", ".daphne", ".scummvm"})


def in_progress_name(name):
    """True for a file or folder name that is still being written or unpacked."""
    low = os.path.basename((name or "").rstrip("/")).lower()
    return low.startswith(".") or low.endswith(TEMP_SUFFIXES) or bool(_TEMP_RE.search(low))


# Systems whose games have an extension NOT_GAME_EXTS holds (PICO-8 and Voxatron carts
# are .p8.png / .vx.png pictures): what they take when ES's own list can't be read.
GAME_EXTS_FALLBACK = {"pico8": frozenset({".p8", ".png"}), "voxatron": frozenset({".vx", ".png"})}
_ES_EXTS = {}          # es_systems.cfg in use -> {system: extensions ES lists}, read once


def _system_exts(system):
    """The extensions ES lists for a system (lowercased), or None when unknown."""
    if not system:
        return None
    src = os.environ.get("ROMM_BROWSER_ES_SYSTEMS", "")
    if src not in _ES_EXTS:
        _ES_EXTS[src] = _es.load_es_systems()
    return _ES_EXTS[src].get(system) or GAME_EXTS_FALLBACK.get(system)


def is_game_name(name, system=None):
    """True when a name in roms/<system> can be (part of) a game: not a file being
    written, a save or state, a media folder or file, or a note. An extension the
    system's ES list takes is always a game's (PICO-8's .p8.png carts are pictures)."""
    if not name or in_progress_name(name):
        return False
    low = os.path.basename(name.rstrip("/")).lower()
    if low in _core.MEDIA_DIRS or _STATE_RE.search(low):
        return False
    ext = os.path.splitext(low)[1]
    if ext not in NOT_GAME_EXTS:
        return True
    exts = _system_exts(system) or ()
    # a cart's double extension: "X.p8.png" is a .p8 game even where ES lists only .p8
    return ext in exts or (low.endswith((".p8.png", ".vx.png")) and os.path.splitext(low[:-4])[1] in exts)


# ------------------------------------------------------- installed tracking

STAMP_KEYS = (("fs_size_bytes", "size"), ("sha1_hash", "sha1"), ("md5_hash", "md5"), ("crc_hash", "crc"))


def romm_stamp(rom):
    """What RomM said about the game's file when it was installed (size and
    hashes), kept on its record so Compare can tell RomM's copy changed since."""
    return {k: rom[f] for f, k in STAMP_KEYS if rom.get(f)}


class LibraryUnreadable(_core.RommError):
    """installed.json is there but can't be read now (or is damaged and couldn't be
    copied aside): it's never written over (that would lose every record in it).
    From a delete, paths and freed say what went before that was found."""
    paths, freed, failed = (), 0, ()


class GameBusy(_core.RommError):
    """The game can't be deleted now (its conversion is queued or running)."""


class UninstallIncomplete(_es.DeleteIncomplete):
    """Some of a game's files couldn't be deleted; the rest are gone. paths: what
    went, freed: bytes freed, failed: [(path, error)]. (Deleting a game ES lists
    raises its base, es.DeleteIncomplete.)"""


def _file_sig(st):
    """What tells one version of a file from the next: a second write within the
    same timestamp tick still changes the size or (an atomic replace) the inode."""
    return st.st_mtime_ns, st.st_size, st.st_ino


def _clean_records(data, dropped=None):
    """installed.json's records with odd types made safe to read: records that
    aren't objects dropped (their ids added to dropped, when given), "paths" a list
    of strings, "group" an object or gone."""
    out = {}
    for rid, rec in data.items():
        if not isinstance(rec, dict):
            if dropped is not None:
                dropped.append(str(rid))
            continue
        ps = rec.get("paths")
        rec["paths"] = [p for p in ps if isinstance(p, str) and p] if isinstance(ps, list) else []
        if "group" in rec and not isinstance(rec["group"], dict):
            del rec["group"]
        out[str(rid)] = rec
    return out


def _read_records(path):
    """(records, signature) of an installed.json; OSError or ValueError when it
    can't be read or isn't a JSON object. Records that cleaning has to drop (JSON
    that parses but holds the wrong types) are logged, and the file is kept as
    <name>.bad-<when> first (once per content), as a damaged one is: a write
    from the cleaned records would otherwise lose them without a trace."""
    with open(path) as f:
        sig = _file_sig(os.fstat(f.fileno()))
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("not an object")
    dropped = []
    recs = _clean_records(data, dropped)
    if dropped and path == _core.STATE_PATH:
        _core.log("%s: %d records dropped for not being objects: %s" % (
            os.path.basename(path), len(dropped), ", ".join(dropped[:20])))
        _core._keep_unreadable(path)
    return recs, sig


def _backup_path():
    return _core.STATE_PATH + ".bak"


def _read_backup():
    """The records of installed.json.bak (installed.json as it was after its last
    good write), or None."""
    try:
        return _read_records(_backup_path())[0]
    except (OSError, ValueError):
        return None


BAK_COPY_EVERY = 60     # seconds: where .bak has to be a copy, at most one per this long


def _keep_backup(force=False):
    """installed.json (just written by us, so known good) is copied to installed.json.bak
    (under a temp name and renamed over .bak, so .bak is always whole). A real copy,
    never a hard link: a link would share installed.json's blocks, so damage to the
    file in place (a power cut on exFAT, an editor writing in place) would take .bak
    with it. Copied only when .bak is BAK_COPY_EVERY old, unless force: deleting
    200 games from Free up space would otherwise copy the whole file 200 times. A
    copied .bak up to a minute behind is as good: it's only read when installed.json
    is missing (then a minute's changes are lost instead of all of them) or damaged,
    where the salvage of the damaged file always wins and .bak only fills in what
    the damage took (a game recorded in that last minute and lost there still shows
    by its file name; one deleted then comes back as a record whose files are gone,
    which nothing lists), and the damaged bytes themselves are kept as
    installed.json.bad-* first. Never called for a file that wasn't just written
    here, so a damaged or unknown installed.json never becomes .bak."""
    bak = _backup_path()
    tmp = "%s.tmp-%d-%d" % (bak, os.getpid(), threading.get_ident())
    try:
        try:
            age = time.time() - os.stat(bak).st_mtime
        except OSError:
            age = None
        if not force and age is not None and 0 <= age < BAK_COPY_EVERY:
            return                          # a recent copy stands (copied aside, then renamed: always whole)
        shutil.copyfile(_core.STATE_PATH, tmp)
        os.replace(tmp, bak)
    except OSError as e:
        _core.log("installed.json.bak not kept: %r" % e)
    try:                                    # a copy that failed half-way
        os.remove(tmp)
    except OSError:
        pass


def _from_backup():
    """installed.json is missing but installed.json.bak reads fine: its records.
    Nothing in the app deletes installed.json (a write replaces it in one
    rename), so a missing one was lost (a filesystem check, a rename cut off, a
    hand): .bak is the only copy of every record left, never deleted with it."""
    recs = _read_backup()
    if recs:
        _core.log("installed.json is MISSING: carrying on from installed.json.bak (%d records)" % len(recs))
    return recs


_JSON = json.JSONDecoder()
_WS = re.compile(r"[ \t\n\r]*")
# a record's key at the top level of installed.json as we write it (indent=1): one
# space in, at the start of a line; nested keys are two or more in, and a newline
# never appears inside a JSON string
_TOP_KEY = re.compile(r'\n "((?:[^"\\\n]|\\.)*)": (?=\{)')


def salvage_records(text):
    """(records, whole) from the text of a damaged installed.json: every complete
    "id": {record} pair up to the damage, read one by one, then any after it that
    start a line the way our writes lay them out (a bad block in the middle). whole:
    the top-level object was read to its end (only junk after it)."""
    out, whole = {}, False
    text = text.lstrip(chr(0xFEFF))      # a BOM (chr: no BOM character in the source)
    i = _WS.match(text).end()
    if text.startswith("{", i):
        i = _WS.match(text, i + 1).end()
        try:
            if text.startswith("}", i):
                whole = True
            while not whole:
                key, j = _JSON.raw_decode(text, i)
                if not isinstance(key, str):
                    break
                j = _WS.match(text, j).end()
                if not text.startswith(":", j):
                    break
                val, j = _JSON.raw_decode(text, _WS.match(text, j + 1).end())
                out[key] = val
                i = _WS.match(text, j).end()
                if text.startswith(",", i):
                    i = _WS.match(text, i + 1).end()
                    continue
                whole = text.startswith("}", i)
                break
        except ValueError:                         # the damage: i is where it starts
            pass
    if not whole:
        for m in _TOP_KEY.finditer(text, max(0, i - 1)):
            try:
                key = json.loads('"%s"' % m.group(1))
                val = _JSON.raw_decode(text, m.end())[0]
            except ValueError:
                continue
            out[key] = val
    return _clean_records(out), whole


def _bad_copy_kept(raw):
    """True when an installed.json.bad-* copy holds exactly these bytes."""
    d, base = os.path.split(_core.STATE_PATH)
    try:
        names = [n for n in os.listdir(d) if n.startswith(os.path.basename(base) + ".bad-")]
    except OSError:
        return False
    for n in names:
        try:
            with open(os.path.join(d, n), "rb") as f:
                if f.read() == raw:
                    return True
        except OSError:
            pass
    return False


def _recover():
    """(records, safe) for an installed.json that is there but won't parse.

    Its exact bytes are kept as installed.json.bad-<when> first; safe is False when
    that copy couldn't be made (or it can't be read now), and then nothing may be
    written over it. The records are what can be salvaged from the damaged text;
    when it was cut short, installed.json.bak (the file after its last good write) fills
    in the records the damage took. The salvage always wins over .bak: it's the
    newer of the two, so a record it holds is never set back to an older one, and
    .bak only adds what the salvage lost (at worst a game deleted in the last writes
    comes back as a record whose files are gone, which nothing lists). .bak is
    a copy refreshed after a write at most a minute apart, and at once after a
    recovery (_keep_backup)."""
    try:
        with open(_core.STATE_PATH, "rb") as f:
            raw = f.read()
    except OSError as e:
        _core.log("installed.json couldn't be read (%r)" % e)
        return None, False
    _core._keep_unreadable(_core.STATE_PATH)
    safe = _bad_copy_kept(raw)
    recs, whole = salvage_records(raw.decode("utf-8", "replace"))
    found = len(recs)
    bak = None if whole else _read_backup()
    if bak:
        recs = dict(bak, **recs)
    _core.log("installed.json is damaged (%d bytes): %d records salvaged%s%s" % (
        len(raw), found, ", %d more from installed.json.bak" % (len(recs) - found) if bak else "",
        "" if safe else "; its copy couldn't be kept, so it is left as it is"))
    return recs, safe


class Library:
    """What's already on the box: our own installs (installed.json) plus a
    filename-stem scan of roms/<sys>, so games pushed earlier (same RomM
    filenames) also show as installed."""

    # seconds: a folder changed this recently is listed again once it settles. FAT32
    # (most SD cards and USB sticks) keeps mtimes to 2 s and a CIFS share caches a
    # folder's attributes for ~1 s, so a second change can hide in that long a tick.
    STEMS_SETTLE = 3.0

    def __init__(self):
        self.lock = threading.RLock()
        self.installed = {}
        self._stems = {}         # system -> (its folder's signature, recheck at or None, names)
        self._sig = None         # installed.json's signature when last read
        self._bad = None         # ... when last found unreadable: not read again until it changes
        self._bak_sig = False    # installed.json.bak's when read in installed.json's place (False: never)
        self._reload()

    # installed.json is shared with rb_chd.py / rb_dl.py (other processes):
    # every change is a read-modify-write under an flock, and reads pick up
    # changes made elsewhere.
    def _reload(self):
        try:
            sig = _file_sig(os.stat(_core.STATE_PATH))
        except FileNotFoundError:          # lost: what .bak holds, else nothing
            try:
                bsig = _file_sig(os.stat(_backup_path()))
            except OSError:
                bsig = None
            if self._sig is not None or bsig != self._bak_sig:      # read (and logged) once per change
                self.installed, self._bak_sig = _from_backup() or {}, bsig
            self._sig, self._bad = None, None
            return
        except OSError:
            return
        if sig == self._sig or sig == self._bad:
            return
        try:
            self.installed, self._sig = _read_records(_core.STATE_PATH)
            self._bad = None
        except FileNotFoundError:
            self.installed, self._sig, self._bad = _from_backup() or {}, None, None
        except OSError:
            pass                           # can't open it now: what we had stands, try again later
        except ValueError:
            self._bad = sig
            recs = _recover()[0]
            if recs is not None:           # what the next write carries on from
                self.installed = recs

    def snapshot(self):
        """installed.json's records as they are now ({rom id: record}), re-read
        first if another process changed it. A new dict, safe to iterate
        outside the lock; the records themselves are shared: read-only."""
        with self.lock:
            self._reload()
            return dict(self.installed)

    def _load_for_write(self):
        """(records, state): state "good" (installed.json read fine), "missing"
        (what installed.json.bak holds, else none) or "recovered" (damaged: what
        _recover salvaged). A file that is there but can't
        be read is never written over with what this process happens to hold (a
        fresh rb_dl or rb_chd holds nothing): LibraryUnreadable when it can't be
        opened now (try again later) or its damaged bytes couldn't be kept aside."""
        try:
            return _read_records(_core.STATE_PATH)[0], "good"
        except FileNotFoundError:
            return _from_backup() or {}, "missing"
        except OSError as e:
            _core.log("installed.json couldn't be opened (%r); not writing over it" % e)
            raise LibraryUnreadable("installed.json can't be read right now, so nothing was "
                                    "recorded; try again")
        except ValueError:
            recs, safe = _recover()
            if not safe:
                raise LibraryUnreadable("installed.json is damaged and couldn't be copied aside, "
                                        "so nothing was recorded")
            return recs, "recovered"

    def update(self, fn):
        """Apply fn(installed) to the freshest installed.json and write it back.
        Raises LibraryUnreadable (nothing written) when it's there but can't be
        read now, or is damaged and couldn't be copied aside first."""
        import fcntl
        with self.lock:
            os.makedirs(_core.DATA_DIR, exist_ok=True)
            with open(_core.STATE_PATH + ".lock", "w") as lk:
                fcntl.flock(lk, fcntl.LOCK_EX)
                self.installed, state = self._load_for_write()
                self._sig = None                     # memory may now differ from the file
                out = fn(self.installed)
                _core.atomic_write_json(_core.STATE_PATH, self.installed, indent=1)
                _keep_backup(force=state != "good")   # the file just written is the last good state
                self._sig, self._bad = _file_sig(os.stat(_core.STATE_PATH)), None
            return out

    def _scan(self, system):
        """The lowercased names and stems of the game files in roms/<system>, listed
        again whenever the folder changes (a download, a copy over the network, a
        delete from ES), one stat per call. Call under self.lock."""
        d = os.path.join(_core.ROMS_ROOT, system)
        now = time.time()
        try:
            st = os.stat(d)
            sig = (st.st_mtime_ns, st.st_ino, st.st_size)
        except OSError:
            st = sig = None
        ent = self._stems.get(system)
        if ent and ent[0] == sig and (ent[1] is None or now < ent[1]):
            return ent[2]
        stems = set()
        try:
            for n in os.listdir(d):
                if is_game_name(n, system):
                    stems.add(_core.stem_of(n).lower())
                    stems.add(n.lower())
        except OSError:
            pass
        # changed within the last moment: another change in the same timestamp tick
        # wouldn't show in the signature, so list it once more after it settles. A
        # folder dated in the future (a box with no clock battery, before NTP) isn't
        # "just changed": it would be listed again on every call.
        recheck = None
        if st is not None and 0 <= now - st.st_mtime < self.STEMS_SETTLE:
            recheck = now + self.STEMS_SETTLE
        self._stems[system] = (sig, recheck, stems)
        return stems

    def is_installed(self, rom, system):
        with self.lock:
            self._reload()
            rec = self.installed.get(str(rom["id"]))
            if rec and any(os.path.exists(p) for p in rec.get("paths") or []):
                return True
            stems = self._scan(system)
        fs = (rom.get("fs_name") or "").lower()
        return fs in stems or (rom.get("fs_name_no_ext") or "").lower() in stems

    def mark(self, rom, system, paths, group=None, meta=None):
        rec = {"system": system, "paths": list(paths), "name": rom.get("name"),
               "fs_name": rom.get("fs_name"), "when": time.strftime("%Y-%m-%d %H:%M:%S")}
        stamp = romm_stamp(rom)
        if stamp:
            rec["romm"] = stamp
        if group:
            rec["group"] = group
            rec["hidden_disc"] = False            # not hidden under the playlist yet (Downloader._finish_group)
        if meta:
            rec["meta"] = meta
        m3u = os.path.join(_core.ROMS_ROOT, system, _core.safe_name(group["base"]) + ".m3u") \
            if group and group.get("base") else None

        def put(inst):
            # a disc of a set fetched again keeps the set's playlist, as every disc's record
            # holds it once the set is written: Compare tells a disc's own files by that
            if m3u and m3u not in rec["paths"] and os.path.isfile(m3u) and any(
                    m3u in (r.get("paths") or []) for r in inst.values()
                    if isinstance(r, dict) and (r.get("group") or {}).get("id") == group.get("id")):
                rec["paths"].append(m3u)
            inst[str(rom["id"])] = rec
        try:
            self.update(put)
        finally:
            self.rescan(system)

    def group_for(self, rom_id):
        with self.lock:
            self._reload()
            rec = self.installed.get(str(rom_id)) or {}
            return rec.get("group")

    def variants(self, rom, system):
        """Other versions of this game on the box (different region/revision),
        by filename: same title once every (...)/[...] tag is removed, but not
        the rom itself nor another disc of the same set. One name per game (a
        cue/bin set is its .cue), only game files (no saves, images or notes)."""
        mine = (rom.get("fs_name_no_ext") or "").lower()
        title = strip_tags(mine)
        dset = disc_info(rom.get("fs_name_no_ext") or "")
        sysdir = os.path.join(_core.ROMS_ROOT, system)
        try:
            names = sorted(os.listdir(sysdir))
        except OSError:
            return []
        games = {}                                     # one per game: its track-less stem -> name
        for n in names:
            if not is_game_name(n, system) or n.lower().endswith(".m3u"):
                continue
            stem = n if os.path.isdir(os.path.join(sysdir, n)) else _core.stem_of(n)
            low = _LIB_TRACK_TAG.sub("", stem).lower()
            if low == mine or strip_tags(low) != title:
                continue
            other = disc_info(stem)
            if dset and other and other[0].lower() == dset[0].lower():
                continue                               # another disc of the same set
            if low not in games or _name_rank(n) < _name_rank(games[low]):
                games[low] = n
        return list(games.values())

    def adopt(self, rom, system, group):
        """A disc that is already on the box joins a set being downloaded:
        record it (keeping an existing record's details) with the group."""
        paths = [p for p in self.paths_for(rom, system) if not p.lower().endswith(".m3u")]
        if not paths:
            return False

        def put(inst):
            rec = inst.get(str(rom["id"])) or {
                "system": system, "name": rom.get("name"), "fs_name": rom.get("fs_name"),
                "when": time.strftime("%Y-%m-%d %H:%M:%S")}
            rec["paths"] = list(dict.fromkeys([p for p in rec.get("paths") or [] if os.path.exists(p)] + paths))
            rec["group"] = group
            rec.setdefault("meta", gamelist_entry(rom, system, paths))
            inst[str(rom["id"])] = rec
        try:
            self.update(put)
        finally:
            self.rescan(system)
        return True

    def rename_paths(self, rename, extra=()):
        """A CHD conversion replaced files: point records at the new ones
        (keeping their other paths, e.g. a set's .m3u)."""
        def fix(inst):
            for rec in inst.values():
                ps = rec.get("paths") or []
                if not any(p in rename for p in ps):
                    continue
                out = []
                for p in ps:
                    for q in ([rename[p]] + list(extra)) if p in rename else [p]:
                        if q not in out:
                            out.append(q)
                rec["paths"] = out
        self.update(fix)

    def rescan(self, system):
        with self.lock:
            self._stems.pop(system, None)

    def paths_for(self, rom, system):
        """Files/folders that make up this rom on the box: our own install
        record, else its game files by name in roms/<system> (never its saves,
        states, images or notes, nor a file still being written); an .m3u brings
        its discs when it is the game (a record's, or RomM's file itself). Only
        ever paths inside roms/<system>."""
        sysdir = os.path.join(_core.ROMS_ROOT, system)
        with self.lock:
            self._reload()
            rec = self.installed.get(str(rom["id"])) or {}
            out = [p for p in rec.get("paths") or [] if os.path.exists(p)]
        follow = list(out)
        if not out:
            fs = (rom.get("fs_name") or "").lower()
            names = {fs, (rom.get("fs_name_no_ext") or "").lower()}
            names.discard("")
            try:
                for n in sorted(os.listdir(sysdir)):
                    if is_game_name(n, system) and (n.lower() in names or os.path.splitext(n)[0].lower() in names):
                        out.append(os.path.join(sysdir, n))
            except OSError:
                pass
            # a same-named playlist goes with the game, but its discs are only the
            # game's when the playlist is what RomM has
            follow = [p for p in out if os.path.basename(p).lower() == fs]
        for p in follow:
            if p.lower().endswith(".m3u") and os.path.isfile(p):
                for q in _es.m3u_paths(p):
                    if os.path.exists(q) and q not in out:
                        out.append(q)
        return [p for p in out if _core._inside(p, sysdir)]

    def _refuse_busy(self, paths):
        why = conversion_of(paths)
        if why:
            raise GameBusy(why[0] + (": delete it once that's done" if why[1]
                                     else ": take it out of Conversions to delete it now"))

    def uninstall_group(self, group, system):
        """Delete every disc of a set (and its .m3u). Nothing is deleted while one
        of its discs is being converted; a disc that couldn't all go doesn't stop
        the others (UninstallIncomplete after)."""
        recs = self.snapshot()
        roms = []
        for rid in group.get("rom_ids") or []:
            rec = recs.get(str(rid)) or {}
            roms.append({"id": rid, "name": rec.get("name"), "fs_name": rec.get("fs_name"),
                         "fs_name_no_ext": os.path.splitext(rec.get("fs_name") or "")[0]})
        self._refuse_busy([p for r in roms for p in self.paths_for(r, system)])
        freed, paths, failed, unrecorded = 0, [], [], None
        for r in roms:
            try:
                p, f = self.uninstall(r, system)
            except (UninstallIncomplete, LibraryUnreadable) as e:
                p, f = list(e.paths), e.freed
                failed += e.failed
                if isinstance(e, LibraryUnreadable):
                    unrecorded = e
            paths += p
            freed += f
        if unrecorded is not None:
            raise _unrecorded(group.get("title") or group.get("base"), paths, freed, failed, unrecorded)
        if failed:
            raise UninstallIncomplete(_incomplete_msg(group.get("title") or group.get("base"), failed),
                                      paths, freed, failed)
        return paths, freed

    def uninstall(self, rom, system):
        """Delete the rom's files (never saves/states, never outside
        roms/<system>), queue their gamelist entries for removal and drop its
        record. Refused (GameBusy) while its conversion is queued or running. A
        file that won't go doesn't stop the rest: the record keeps what's left
        and UninstallIncomplete says what that is."""
        sysdir = os.path.join(_core.ROMS_ROOT, system)
        paths = [p for p in self.paths_for(rom, system) if _core._inside(p, sysdir)]
        self._refuse_busy(paths)
        gone, failed, freed = [], [], 0
        for p in paths:
            size = _core.path_size(p)
            try:
                if os.path.isdir(p) and not os.path.islink(p):
                    shutil.rmtree(p)
                elif os.path.lexists(p):
                    os.remove(p)
            except OSError as e:
                failed.append((p, e))
                if os.path.lexists(p):
                    freed += max(0, size - _core.path_size(p))
                    continue
            gone.append(p)
            freed += size
        rid = str(rom["id"])
        gone_set = set(gone)

        def drop(inst):
            # the playlist can take other discs' files with it: their records follow what's on disk
            for oid, other in list(inst.items()):
                if oid == rid or not isinstance(other, dict):
                    continue
                ops = other.get("paths") or []
                if not any(p in gone_set for p in ops):
                    continue
                if any(os.path.lexists(p) for p in ops if p not in gone_set):
                    other["paths"] = [p for p in ops if p not in gone_set]
                else:
                    del inst[oid]
            rec = inst.get(rid)
            left = [p for p in (rec.get("paths") or []) if os.path.lexists(p)] if isinstance(rec, dict) else []
            if failed and left:
                rec["paths"] = left               # what's still there stays findable (and deletable)
            else:
                inst.pop(rid, None)
        try:
            if gone:
                _core.append_gamelist({"system": system, "remove": gone})
        finally:
            try:
                self.update(drop)
            except (LibraryUnreadable, OSError) as e:          # OSError: installed.json can't be written
                if not gone and not failed:
                    raise
                raise _unrecorded(rom.get("name"), gone, freed, failed, e) from None
            finally:
                self.rescan(system)
        _core.log("UNINSTALL %s: %s (%s)%s" % (rom.get("name"), gone, _core.human_size(freed),
                                                " FAILED %r" % failed if failed else ""))
        if failed:
            raise UninstallIncomplete(_incomplete_msg(rom.get("name"), failed), gone, freed, failed)
        return gone, freed


def _unrecorded(name, paths, freed, failed, err):
    """The LibraryUnreadable a delete raises when its files went (some or all) but
    installed.json couldn't be updated: it says what was deleted."""
    _core.log("UNINSTALL %s: %s deleted, installed.json not updated: %s" % (name, paths, err))
    what = _incomplete_msg(name, failed) if failed else "Deleted %s — %s freed" % (
        name or "the game", _core.human_size(freed))
    e = LibraryUnreadable("%s, but installed.json can't be updated now" % what)
    e.paths, e.freed, e.failed = list(paths), freed, list(failed)
    return e


def _incomplete_msg(name, failed):
    return _es._not_all_deleted(name, failed)


_LIB_TRACK_TAG = re.compile(r"\s*\((?:track|disc track)\s*\d+\)", re.I)


def _name_rank(name):
    """How well a file stands for its game in a list (a .cue before its .bin)."""
    e = os.path.splitext(name)[1].lower()
    return (PRIMARY_ORDER.index(e) if e in PRIMARY_ORDER else 99, len(name))


def _holds(paths, src):
    src = os.path.normpath(src)
    return any(src == p or src.startswith(p.rstrip(os.sep) + os.sep) for p in paths)


def conversion_of(paths):
    """(why, running) when a conversion of these files is queued or running, else
    None: why is "Being compressed to CHD (40%)" or "Waiting to be compressed to RVZ"."""
    paths = [os.path.normpath(p) for p in paths]
    if not paths:
        return None
    state = _convert.conversions_state(recent=1)
    for name, task in state["queue"]:
        if isinstance(task, dict) and any(isinstance(s, str) and _holds(paths, s) for s in task.get("sources") or []):
            label = _convert.CONVERT_LABELS.get(task.get("mode"), "CHD")
            cur = state["current"] if isinstance(state["current"], dict) else {}
            if cur.get("task") == name:
                pct = cur.get("pct")
                return "Being compressed to %s%s" % (
                    label, " (%d%%)" % pct if isinstance(pct, (int, float)) else ""), True
            return "Waiting to be compressed to %s" % label, False
    return None


# ----------------------------------------------------------- multi-disc

# "(CD32)" is Amiga CD32's platform tag and "(DVD5)"/"(DVD9)" a disc's type, never a disc number
DISC_RE = re.compile(r"\s*\((?:Disc|Disk|CD(?!32\))|DVD(?![59]\)))\s*([0-9]{1,2}|[A-H])(?:\s*of\s*[0-9]{1,2})?\)", re.I)


def rom_is_folder(detail, files=None):
    """True when a RomM game is a folder (cue/bin set, GDI, a folder holding
    one file, per-disc subfolders...): some file of it lives at another path
    than the game itself. RomM's has_multiple_files / has_nested_single_file
    only count files directly in the folder, so a game whose files all sit in
    subfolders (Game/Game (Disc 1)/...) reports neither."""
    if detail.get("has_multiple_files") or detail.get("has_nested_single_file"):
        return True
    files = files if files is not None else (detail.get("files") or [])
    gp = (detail.get("full_path") or "").rstrip("/")
    return bool(files) and bool(gp) and any((f.get("full_path") or "").rstrip("/") != gp for f in files)


def disc_info(name):
    """("Base Name (USA)", n) for "Base Name (USA) (Disc 2)"; None when not a disc."""
    m = DISC_RE.search(name or "")
    if not m:
        return None
    v = m.group(1)
    n = int(v) if v.isdigit() else ord(v.upper()) - ord("A") + 1
    base = (name[:m.start()] + name[m.end():]).strip()
    return re.sub(r"\s{2,}", " ", base), n


def strip_tags(name):
    return re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", name or "").strip().lower()


def find_disc_set(api, rom):
    """Every disc of the set `rom` belongs to, in disc order: [(n, rom)].
    Candidates come from RomM's siblings and a title search on the same
    platform; only names that match exactly apart from the disc tag count
    (so other regions, revisions and unrelated "siblings" are excluded).
    Returns (discs, missing_numbers) or (None, None) if `rom` isn't a disc."""
    me = disc_info(rom.get("fs_name_no_ext") or "")
    if not me:
        return None, None
    base = me[0].lower()
    pid = rom.get("platform_id")
    cands = {rom["id"]: rom}
    title = strip_tags(me[0]) or me[0]
    try:
        for r in api.roms(platform_ids=[pid], search=title, limit=250).get("items", []):
            cands.setdefault(r["id"], r)
    except _core.RommError:
        pass
    try:
        for sib in (api.rom(rom["id"]).get("sibling_roms") or []):
            if sib.get("id") not in cands:
                d = disc_info(sib.get("fs_name_no_ext") or "")
                if d and d[0].lower() == base:
                    cands[sib["id"]] = api.rom(sib["id"])
    except _core.RommError:
        pass
    # The same set is sometimes in RomM twice ("Disc 01" and "Disc 1"): prefer
    # the copies tagged in the same style as the disc picked, never mix them.
    def tag_style(name):
        m = DISC_RE.search(name)
        g = m.group(0)                     # "(Disc 01)" vs "(Disc 1)": the digit count is the style
        return (g[:m.start(1) - m.start(0)] + "#" * len(m.group(1)) + g[m.end(1) - m.start(0):]).strip().lower()
    style = tag_style(rom.get("fs_name_no_ext") or "")
    discs = {}
    for r in sorted(cands.values(), key=lambda r: r["id"]):
        name = r.get("fs_name_no_ext") or ""
        d = disc_info(name)
        if not d or d[0].lower() != base or r.get("platform_id") != pid:
            continue
        same_style = tag_style(name) == style
        if d[1] not in discs or (same_style and not discs[d[1]][1]):
            discs[d[1]] = (r, same_style)
    discs = {n: v[0] for n, v in discs.items()}
    discs[me[1]] = rom                                # always the one actually picked
    ordered = sorted(discs.items())
    first = 0 if 0 in discs else 1                    # some sets have a "Disc 0" (e.g. Enemy Zero)
    missing = [n for n in range(first, max(discs) + 1) if n not in discs]
    return ordered, missing


def disc_group(platform_slug, discs, title=None):
    """The group record a disc set's jobs share."""
    base = disc_info(discs[0][1].get("fs_name_no_ext") or "")[0]
    return {"id": "%s:%s" % (platform_slug, base.lower()), "base": base,
            "title": title or re.sub(r"\s*[\(\[].*$", "", base) or base,
            "rom_ids": [r["id"] for _, r in discs]}


PRIMARY_ORDER = [".m3u", ".chd", ".gdi", ".cue", ".cdi", ".iso", ".rvz"]


def primary_path(system, paths, es):
    """The one file ES lists for a rom made of `paths` (files or folders)."""
    exts = es.get(system)
    cands = []
    for p in paths:
        if os.path.isdir(p):
            for dp, _, fns in os.walk(p):
                cands += [os.path.join(dp, f) for f in fns]
        elif os.path.exists(p):
            cands.append(p)
    if exts is not None:
        cands = [c for c in cands if os.path.splitext(c)[1].lower() in exts]
    cands = [c for c in cands if not c.lower().endswith(".m3u")] or cands
    if not cands:
        return None

    def key(c):
        e = os.path.splitext(c)[1].lower()
        return (PRIMARY_ORDER.index(e) if e in PRIMARY_ORDER else 99, len(c))
    return sorted(cands, key=key)[0]


def play_target(library, rom, system, es=None):
    """What EmulationStation would start for a game on the box: (path, None),
    or (None, why not now). The path is the one ES lists: a multi-disc set's
    .m3u, a game folder ES lists as one game (a PS3 .ps3 folder), else the
    game's primary file (.chd, .rvz, .zar, .wua... once converted). Never a
    game whose conversion is queued or running: its files are about to be
    replaced. es: {system: extensions} (load_es_systems() when None)."""
    paths = [os.path.normpath(p) for p in library.paths_for(rom, system)]
    if not paths:
        return None, "It isn't on this box"
    busy = conversion_of(paths)
    if busy:
        return None, busy[0] + ": it plays once that's done"
    es = es if es is not None else _es.load_es_systems()
    exts = es.get(system)
    accepts = (lambda p: exts is None or os.path.splitext(p)[1].lower() in exts)
    m3u = [p for p in paths if p.lower().endswith(".m3u") and os.path.isfile(p) and accepts(p)]
    if m3u:
        return m3u[0], None
    group = library.group_for(rom["id"])
    if group and (exts is None or ".m3u" in exts):
        for rid in group.get("rom_ids") or []:
            if not library.paths_for({"id": rid, "fs_name": None, "fs_name_no_ext": None}, system):
                return None, "Its other discs aren't on the box yet"
    for p in paths:                      # "Game.ps3", never "Dr. Mario (USA)" (". Mario (USA)" isn't one)
        ext = os.path.splitext(p)[1].lower()
        if os.path.isdir(p) and ext and (ext in exts if exts is not None else ext in FOLDER_GAME_EXTS):
            return p, None
    prim = primary_path(system, paths, es)
    return (prim, None) if prim else (None, "EmulationStation can't start these files")


RESOURCES = "/assets/romm/resources/"


def resource_path(p, exts=None):
    """A file RomM keeps in its resources, as a server path the app's session
    fetches ("/assets/romm/resources/roms/1/2/logo/logo.png"), or None. RomM's
    records give these as "roms/1/2/..." (ss_metadata's *_path, path_video) or
    already under /assets/romm/resources/. Anything else (an upstream scraper
    URL, a path climbing out) is None: the app only ever asks RomM."""
    if not p or not isinstance(p, str) or "://" in p or p.startswith("//") or "\\" in p:
        return None
    p = p.split("?")[0].lstrip("/")
    if p.startswith(RESOURCES[1:]):
        p = p[len(RESOURCES) - 1:]
    if not p or ".." in p.split("/") or p.startswith("assets/"):
        return None
    if exts and not p.lower().endswith(tuple(exts)):
        return None
    return RESOURCES + p


def manual_path(rom):
    """Server path of the rom's manual PDF in RomM's assets, or None. (Never
    url_manual: that's the upstream scraper URL, not RomM.)"""
    return resource_path(rom.get("path_manual"), (".pdf",)) if rom.get("has_manual") else None


# ES's media tags RomM can fill beyond image (a screenshot) and thumbnail (the box art), and
# where RomM keeps each: its ScreenScraper media first, then what it read from a gamelist.xml
# or LaunchBox — the order RomM's own gamelist export uses. These *_path fields are files RomM
# stored itself (config.yml scan: media:), never the *_url ones (upstream links). ES's
# "marquee" is the game's logo (ES calls it Logo), "boxart" its alternative (3D) box.
ES_MEDIA_SOURCES = (
    ("marquee", (("ss_metadata", "logo_path"), ("ss_metadata", "marquee_path"),
                 ("gamelist_metadata", "marquee_path"))),
    ("titleshot", (("ss_metadata", "title_screen_path"), ("gamelist_metadata", "title_screen_path"))),
    ("boxart", (("ss_metadata", "box3d_path"), ("gamelist_metadata", "box3d_path"),
                ("launchbox_metadata", "box3d_path"))),
    ("boxback", (("ss_metadata", "box2d_back_path"), ("gamelist_metadata", "box2d_back_path"),
                 ("launchbox_metadata", "box2d_back_path"))),
    ("fanart", (("ss_metadata", "fanart_path"), ("gamelist_metadata", "fanart_path"))),
    ("cartridge", (("ss_metadata", "physical_path"), ("gamelist_metadata", "physical_path"))),
)
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def _first(paths):
    return list(dict.fromkeys(p for p in paths if p))


def es_media(rom):
    """{ES media tag: RomM server paths, best first} for what RomM has of this
    game: image = its first screenshot (the cover when it has none), thumbnail
    = the box art (the cover, else ScreenScraper's 2D box), video, and the
    ES_MEDIA_SOURCES. Tags RomM has nothing for are left out. A record saved
    with the download queue carries this already ("_es_media")."""
    if isinstance(rom.get("_es_media"), dict):
        return rom["_es_media"]
    cover = rom.get("path_cover_large") or rom.get("path_cover_small")
    cover = cover if isinstance(cover, str) and cover.startswith("/") and not cover.startswith("//") \
        and ".." not in cover.split("?")[0].split("/") else None
    md = {k: rom.get(k) if isinstance(rom.get(k), dict) else {}
          for k in ("ss_metadata", "gamelist_metadata", "launchbox_metadata")}
    shots = [resource_path(p, IMAGE_EXTS) for p in (rom.get("merged_screenshots") or [])[:1]]
    out = {"image": _first(shots + [cover]),
           "thumbnail": _first([cover, resource_path(md["ss_metadata"].get("box2d_path"), IMAGE_EXTS)]),
           "video": _first([resource_path(rom.get("path_video"), (".mp4",))])}
    for tag, sources in ES_MEDIA_SOURCES:
        out[tag] = _first(resource_path(md[k].get(f), IMAGE_EXTS) for k, f in sources)
    return {k: v for k, v in out.items() if v}


EXTRA_ART = tuple(tag for tag, _ in ES_MEDIA_SOURCES)       # Settings, Metadata: Extra art


def es_video_max(cfg):
    """Bytes a game's video clip may take (Settings, Metadata, Videos); 0: no videos."""
    try:
        mb = int(_config.config_value(cfg, "es_video_mb"))
    except (TypeError, ValueError):
        mb = _config.DEFAULT_CONFIG["es_video_mb"]
    return max(0, mb) << 20


def es_pick(media, cfg):
    """es_media's tags as Settings' Metadata section asks for them: the box art as the
    image when asked (the thumbnail stays the box art), no video when they're off, no
    extra art when that is."""
    out = dict(media)
    if _config.config_value(cfg, "es_image") == "boxart" and out.get("thumbnail"):
        out["image"] = out["thumbnail"]
    if not es_video_max(cfg):
        out.pop("video", None)
    if not _config.config_value(cfg, "es_extra_art"):
        for tag in EXTRA_ART:
            out.pop(tag, None)
    return out


# -- media RomM offers that never arrives (a video over the size cap, a file that 404s):
# noted per game and tag, so Fill in game info stops listing the game as incomplete for
# it. A different file at RomM (a new path) or a cap it fits under is tried again.
SKIPS_MAX = 2000                  # games remembered (the oldest forgotten first)


def media_skips_path():
    return os.path.join(_core.DATA_DIR, "media-skipped.json")


def media_skips():
    """{"system/rom id": {tag: {"path": server path, "size": bytes or None}}}; notes
    of another shape (a hand edit, a damaged file) are left out."""
    try:
        with open(media_skips_path()) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {}
    out = {}
    for key, game in (d.items() if isinstance(d, dict) else ()):
        if not isinstance(game, dict):
            continue
        game = {t: n for t, n in game.items() if isinstance(n, dict)
                and (n.get("size") is None or (isinstance(n["size"], int) and not isinstance(n["size"], bool)))}
        if game:
            out[key] = game
    return out


_SKIPS_LOCK = threading.Lock()


@contextlib.contextmanager
def _skips_locked():
    """media-skipped.json's read-modify-write, one at a time across threads and
    processes (the app and rb_finalize both note skips)."""
    import fcntl
    with _SKIPS_LOCK:
        os.makedirs(_core.DATA_DIR, exist_ok=True)
        with open(media_skips_path() + ".lock", "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            yield


def _save_skips(d):
    while len(d) > SKIPS_MAX:
        del d[next(iter(d))]
    os.makedirs(_core.DATA_DIR, exist_ok=True)
    _core.atomic_write_json(media_skips_path(), d, sync=False)   # a note: lost, it costs one retry


def record_media_skip(system, rom_id, tag, path, size=None):
    """Note that `tag` for this game couldn't be fetched from `path` (size: it was
    over the video cap, that big; None: the file didn't come)."""
    if not rom_id:
        return
    with _skips_locked():
        d = media_skips()
        key = "%s/%s" % (system, rom_id)
        game = d.pop(key, None) or {}
        game[tag] = {"path": path, "size": size}
        d[key] = game
        _save_skips(d)


def clear_media_skip(system, rom_id, tag):
    """The tag came after all: forget its note."""
    if not rom_id:
        return
    key = "%s/%s" % (system, rom_id)
    if tag not in (media_skips().get(key) or {}):
        return                                         # the usual case: nothing to write
    with _skips_locked():
        d = media_skips()
        if tag in (d.get(key) or {}):
            del d[key][tag]
            if not d[key]:
                del d[key]
            _save_skips(d)


# -- a game's ES image that is only its box art again: versions before 0.11 gave ES the cover
# as the image, and Fill in game info then added the same cover as the thumbnail. ES names
# the files it is sent images/<stem>-image.<ext> and <stem>-thumb.<ext> (Batocera's scraper
# too); a new image under another extension leaves the old file behind.
ART_EXTS = (".png", ".jpg", ".jpeg", ".gif")


def es_art_files(sysdir, game_path, kind):
    """ES's files of one kind ("image", "thumb") for a game: images/<stem>-<kind>.<ext>."""
    stem = os.path.splitext(os.path.basename(game_path))[0]
    return [p for p in (os.path.join(sysdir, "images", "%s-%s%s" % (stem, kind, x)) for x in ART_EXTS)
            if os.path.isfile(p)]


ES_FILE_NAMES = {"thumbnail": "thumb", "boxart": "box"}     # its file name for these tags; the rest: the tag
ES_MEDIA_FILE_EXTS = {"video": (".mp4", ".avi", ".mkv", ".webm"), "manual": (".pdf", ".cbz")}


def es_media_file(sysdir, game_path, tag):
    """The file ES names for a game's media tag, when it's there: images/<stem>-<name>.<ext>
    (videos/, manuals/ for those); None otherwise."""
    stem = os.path.splitext(os.path.basename(game_path))[0]
    d = {"video": "videos", "manual": "manuals"}.get(tag, "images")
    for x in ES_MEDIA_FILE_EXTS.get(tag, ART_EXTS):
        p = os.path.join(sysdir, d, "%s-%s%s" % (stem, ES_FILE_NAMES.get(tag, tag), x))
        if os.path.isfile(p):
            return p
    return None


def es_media_file_here(sysdir, game_path, tag):
    return es_media_file(sysdir, game_path, tag) is not None


def same_file(a, b):
    try:
        if os.path.getsize(a) != os.path.getsize(b):
            return False
        with open(a, "rb") as fa, open(b, "rb") as fb:
            return fa.read() == fb.read()
    except OSError:
        return False


def image_is_thumb_again(sysdir, game_path):
    """True when one of the game's image files is the same file as one of its thumbnails:
    a sign ES's image is the box art twice (ES's own answer is the sure one)."""
    thumbs = es_art_files(sysdir, game_path, "thumb")
    return any(same_file(i, t) for i in es_art_files(sysdir, game_path, "image") for t in thumbs)


def wants_own_image(media):
    """The entry offers an image that isn't its box art (a screenshot, as Settings asks)."""
    img, thumb = (media or {}).get("image"), (media or {}).get("thumbnail")
    return bool(img) and bool(thumb) and img[0] != thumb[0]


def skipped_tags(entry, cfg, skips=None):
    """The entry's media tags a recorded skip still covers: the same first path at RomM,
    and (for a video over the cap) still bigger than today's cap."""
    skips = media_skips() if skips is None else skips
    noted = skips.get("%s/%s" % (entry.get("system"), entry.get("rom_id")))
    noted = noted if isinstance(noted, dict) else {}
    media = entry.get("media")
    out = set()
    for tag, paths in (media.items() if isinstance(media, dict) else ()):
        n = noted.get(tag)
        if isinstance(n, dict) and isinstance(paths, (list, tuple)) and paths and n.get("path") == paths[0]:
            size = n.get("size")
            if size is None or (isinstance(size, int) and size > es_video_max(cfg)):
                out.add(tag)
    man = noted.get("manual")
    if isinstance(man, dict) and entry.get("manual") and man.get("path") == entry["manual"]:
        out.add("manual")
    return out


# RomM's region and language names (its filename tags and providers) as the codes
# EmulationStation keeps. Regions: Batocera's ES shows a region's flag by its code
# (resources/flags/<code>.png: "us", "eu", "jp", "wr" for World, "es" Spain, "sw" Sweden...),
# so those codes where it has a flag, ScreenScraper's otherwise. Languages: ScreenScraper's
# codes, as Batocera's own scraper writes them ("en", "fr", "jp")
ES_REGIONS = {
    "usa": "us", "europe": "eu", "japan": "jp", "world": "wr", "australia": "au", "brazil": "br",
    "canada": "ca", "china": "cn", "england": "uk", "uk": "uk", "united kingdom": "uk", "france": "fr",
    "germany": "de", "greece": "gr", "holland": "nl", "netherlands": "nl", "italy": "it", "korea": "kr",
    "norway": "no", "russia": "ru", "spain": "es", "sweden": "sw", "portugal": "pt", "poland": "pl",
    "india": "in", "asia": "asi", "finland": "fi", "hong kong": "hk", "taiwan": "tw", "americas": "ame",
    "denmark": "dk", "new zealand": "nz", "oceania": "oce", "middle east": "mor", "turkey": "tr",
    "israel": "il", "czech republic": "cz", "hungary": "hu", "slovakia": "sk", "bulgaria": "bg",
    "chile": "cl", "peru": "pe", "kuwait": "kw",
}
ES_LANGS = {
    "english": "en", "french": "fr", "german": "de", "spanish": "es", "italian": "it",
    "japanese": "jp", "korean": "ko", "chinese": "zh", "dutch": "nl", "portuguese": "pt",
    "russian": "ru", "swedish": "sv", "danish": "da", "finnish": "fi", "norwegian": "no",
    "polish": "pl", "greek": "el", "arabic": "ar", "serbian": "sr",
}


def _codes(names, table):
    out = []
    for n in names or []:
        if not isinstance(n, str):
            continue
        low = n.strip().lower()
        c = table.get(low) or (low if low in table.values() else None)
        if c and c not in out:
            out.append(c)
    return out


def es_region(rom):
    """The game's region as ES keeps it ("us", "eu", "jp", "wr"...): the first
    of RomM's regions ES has a code for (not Unlicensed, Unknown...), or None."""
    return (_codes(rom.get("regions"), ES_REGIONS) or [None])[0]


def es_lang(rom):
    """The game's languages as ES keeps them ("en", "en,it"), or None."""
    return ",".join(_codes(rom.get("languages"), ES_LANGS)) or None


def ra_summary(rom):
    """(achievements, points) from RomM's RetroAchievements data, or None."""
    if not rom.get("ra_id"):
        return None
    ach = (rom.get("merged_ra_metadata") or {}).get("achievements") or []
    return len(ach), sum(int(a.get("points") or 0) for a in ach)


def gamelist_entry(rom, system, paths, manual=None, cfg=None):
    """What ES gets for the game, as Settings' Metadata section asks (cfg; its defaults
    without one). manual: given, overrides the Download manuals setting."""
    md = rom.get("metadatum") or {}
    fam = [f for f in (md.get("franchises") or []) if isinstance(f, str) and f.strip()]
    if manual is None:
        manual = bool(_config.config_value(cfg, "manuals"))
    tags = bool(_config.config_value(cfg, "es_tags"))
    return {
        "manual": manual_path(rom) if manual else None,
        "system": system, "paths": paths, "rom_id": rom["id"],
        "name": rom.get("name"), "desc": rom.get("summary"),
        "releasedate": md.get("first_release_date"),
        "genres": md.get("genres") or [],
        "developer": ", ".join(md.get("developers") or md.get("companies") or []),
        "publisher": ", ".join(md.get("publishers") or []),
        "players": md.get("player_count"),
        "rating": md.get("average_rating"),
        "region": es_region(rom) if tags else None, "lang": es_lang(rom) if tags else None,
        "family": fam[0] if fam and tags else None,
        "cover": rom.get("path_cover_large") or rom.get("path_cover_small"),
        "media": es_pick(es_media(rom), cfg),
    }


# -- a game under a new file name (conversions, a replaced broken copy)
def migrate_saves(system, old_path, new_path):
    """Copy saves/states keyed on the old file name to the new one. RetroArch
    keys on the stem ("Game (USA).srm"), PCSX2 states on the full basename
    ("Game (USA).iso.p2s"), so both forms are mapped."""
    root = os.path.join(_core.SAVES_ROOT, system)
    if not os.path.isdir(root):
        return 0
    ob, nb = os.path.basename(old_path), os.path.basename(new_path)
    pairs = [(ob, nb), (os.path.splitext(ob)[0], os.path.splitext(nb)[0])]
    pairs = [(o, n) for o, n in pairs if o != n]
    copied = 0
    for dp, _, fns in os.walk(root):
        for fn in fns:
            for o, n in pairs:
                if fn.startswith(o + "."):
                    dst = os.path.join(dp, n + fn[len(o):])
                    if not os.path.exists(dst):
                        # whole or not at all: a cut-short copy under the new name
                        # would never be redone and the emulator would load it
                        tmp = "%s.tmp-%d" % (dst, os.getpid())
                        try:
                            shutil.copy2(os.path.join(dp, fn), tmp)
                            _core.sync_file(tmp)
                            os.replace(tmp, dst)
                        except BaseException:
                            try:
                                os.remove(tmp)
                            except OSError:
                                pass
                            raise
                        copied += 1
                    break
    if copied:
        _core.log("saves: copied %d file(s) %s -> %s" % (copied, ob, nb))
    return copied


def fix_m3us(sysdir, old_to_new):
    """Point existing .m3u playlists at the new files. Returns m3us touched."""
    touched = []
    for dp, _, fns in os.walk(sysdir):
        for fn in fns:
            if not fn.lower().endswith(".m3u"):
                continue
            p = os.path.join(dp, fn)
            try:
                with open(p, errors="surrogateescape") as f:      # odd bytes in names survive as-is
                    lines = f.read().splitlines()
            except OSError:
                continue
            changed = False
            out = []
            for ln in lines:
                entry = _es.m3u_entry(ln)
                target = os.path.normpath(os.path.join(dp, entry)) if entry else ""
                if target in old_to_new:
                    out.append(os.path.relpath(old_to_new[target], dp))
                    changed = True
                else:
                    out.append(ln)
            if changed:
                _core.atomic_write_text(p, "\n".join(out) + "\n", errors="surrogateescape")
                touched.append(p)
    return touched
