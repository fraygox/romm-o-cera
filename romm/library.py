"""installed.json (Library), multi-disc sets and gamelist entries."""
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


# ------------------------------------------------------- installed tracking

STAMP_KEYS = (("fs_size_bytes", "size"), ("sha1_hash", "sha1"), ("md5_hash", "md5"), ("crc_hash", "crc"))


def romm_stamp(rom):
    """What RomM said about the game's file when it was installed (size and
    hashes), kept on its record so Compare can tell RomM's copy changed since."""
    return {k: rom[f] for f, k in STAMP_KEYS if rom.get(f)}


class Library:
    """What's already on the box: our own installs (installed.json) plus a
    filename-stem scan of roms/<sys>, so games pushed earlier (same RomM
    filenames) also show as installed."""

    def __init__(self):
        self.lock = threading.RLock()
        self.installed = {}
        self._stems = {}
        self._mtime = None
        self._reload()

    # installed.json is shared with rb_chd.py / rb_dl.py (other processes):
    # every change is a read-modify-write under an flock, and reads pick up
    # changes made elsewhere.
    def _reload(self):
        try:
            mt = os.stat(_core.STATE_PATH).st_mtime_ns
        except FileNotFoundError:
            if self._mtime is not None:          # it was deleted (a reset): don't write old records back
                self.installed, self._mtime = {}, None
            return
        except OSError:
            return
        if mt == self._mtime:
            return
        try:
            with open(_core.STATE_PATH) as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("not an object")
            self.installed, self._mtime = data, mt
        except OSError:
            pass
        except ValueError:
            _core._keep_unreadable(_core.STATE_PATH)         # the next write replaces it: keep what's there

    def snapshot(self):
        """installed.json's records as they are now ({rom id: record}), re-read
        first if another process changed it. A new dict, safe to iterate
        outside the lock; the records themselves are shared: read-only."""
        with self.lock:
            self._reload()
            return dict(self.installed)

    def update(self, fn):
        """Apply fn(installed) to the freshest installed.json and write it back."""
        import fcntl
        with self.lock:
            os.makedirs(_core.DATA_DIR, exist_ok=True)
            with open(_core.STATE_PATH + ".lock", "w") as lk:
                fcntl.flock(lk, fcntl.LOCK_EX)
                self._mtime = None
                self._reload()
                out = fn(self.installed)
                _core.atomic_write_json(_core.STATE_PATH, self.installed, indent=1)
                self._mtime = os.stat(_core.STATE_PATH).st_mtime_ns
            return out

    def _scan(self, system):
        stems = set()
        d = os.path.join(_core.ROMS_ROOT, system)
        try:
            for n in os.listdir(d):
                stems.add(_core.stem_of(n).lower())
                stems.add(n.lower())
        except OSError:
            pass
        self._stems[system] = stems
        return stems

    def is_installed(self, rom, system):
        with self.lock:
            self._reload()
            rec = self.installed.get(str(rom["id"]))
            if rec and any(os.path.exists(p) for p in rec.get("paths", [])):
                return True
            stems = self._stems.get(system)
            if stems is None:
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
        if meta:
            rec["meta"] = meta

        def put(inst):
            inst[str(rom["id"])] = rec
        self.update(put)
        self._stems.pop(system, None)

    def group_for(self, rom_id):
        with self.lock:
            self._reload()
        rec = self.installed.get(str(rom_id)) or {}
        return rec.get("group")

    def variants(self, rom, system):
        """Other versions of this game on the box (different region/revision),
        by filename: same title once every (...)/[...] tag is removed, but not
        the rom itself nor another disc of the same set."""
        mine = (rom.get("fs_name_no_ext") or "").lower()
        title = strip_tags(mine)
        dset = disc_info(rom.get("fs_name_no_ext") or "")
        out = []
        try:
            names = sorted(os.listdir(os.path.join(_core.ROMS_ROOT, system)))
        except OSError:
            return out
        for n in names:
            stem = _core.stem_of(n) if not os.path.isdir(os.path.join(_core.ROMS_ROOT, system, n)) else n
            low = stem.lower()
            if low == mine or n.lower().endswith((".m3u", ".xml", ".txt")) or strip_tags(low) != title:
                continue
            other = disc_info(stem)
            if dset and other and other[0].lower() == dset[0].lower():
                continue                               # another disc of the same set
            out.append(n)
        return out

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
            rec["paths"] = list(dict.fromkeys([p for p in rec.get("paths", []) if os.path.exists(p)] + paths))
            rec["group"] = group
            rec.setdefault("meta", gamelist_entry(rom, system, paths))
            inst[str(rom["id"])] = rec
        self.update(put)
        self._stems.pop(system, None)
        return True

    def rename_paths(self, rename, extra=()):
        """A CHD conversion replaced files: point records at the new ones
        (keeping their other paths, e.g. a set's .m3u)."""
        def fix(inst):
            for rec in inst.values():
                ps = rec.get("paths", [])
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
        record, else a filename match in roms/<system>; an .m3u brings its
        discs. Only ever paths inside roms/<system>."""
        sysdir = os.path.join(_core.ROMS_ROOT, system)
        with self.lock:
            self._reload()
        rec = self.installed.get(str(rom["id"])) or {}
        out = [p for p in rec.get("paths", []) if os.path.exists(p)]
        if not out:
            names = {(rom.get("fs_name") or "").lower(), (rom.get("fs_name_no_ext") or "").lower()}
            names.discard("")
            try:
                for n in sorted(os.listdir(sysdir)):
                    if n.lower() in names or os.path.splitext(n)[0].lower() in names:
                        out.append(os.path.join(sysdir, n))
            except OSError:
                pass
        for p in list(out):
            if p.lower().endswith(".m3u") and os.path.isfile(p):
                for q in _es.m3u_paths(p):
                    if os.path.exists(q) and q not in out:
                        out.append(q)
        return [p for p in out if _core._inside(p, sysdir)]

    def uninstall_group(self, group, system):
        """Delete every disc of a set (and its .m3u)."""
        freed, paths = 0, []
        with self.lock:
            self._reload()
        for rid in group.get("rom_ids", []):
            rec = self.installed.get(str(rid)) or {}
            p, f = self.uninstall({"id": rid, "name": rec.get("name"), "fs_name": rec.get("fs_name"),
                                   "fs_name_no_ext": os.path.splitext(rec.get("fs_name") or "")[0]}, system)
            paths += p
            freed += f
        return paths, freed

    def uninstall(self, rom, system):
        """Delete the rom's files (never saves/states, never outside
        roms/<system>) and queue its gamelist entries for removal."""
        sysdir = os.path.join(_core.ROMS_ROOT, system)
        paths = self.paths_for(rom, system)
        freed = 0
        for p in paths:
            if not _core._inside(p, sysdir):
                continue
            freed += _core.path_size(p)
            if os.path.isdir(p) and not os.path.islink(p):
                shutil.rmtree(p)
            elif os.path.lexists(p):
                os.remove(p)
        self.update(lambda inst: inst.pop(str(rom["id"]), None))
        self._stems.pop(system, None)
        if paths:
            _core.append_gamelist({"system": system, "remove": paths})
        _core.log("UNINSTALL %s: %s (%s)" % (rom.get("name"), paths, _core.human_size(freed)))
        return paths, freed


# ----------------------------------------------------------- multi-disc

DISC_RE = re.compile(r"\s*\((?:Disc|Disk|CD|DVD)\s*([0-9]{1,2}|[A-H])(?:\s*of\s*[0-9]{1,2})?\)", re.I)


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


def _holds(paths, src):
    src = os.path.normpath(src)
    return any(src == p or src.startswith(p.rstrip(os.sep) + os.sep) for p in paths)


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
    state = _convert.conversions_state(recent=1)
    for name, task in state["queue"]:
        if task and any(_holds(paths, s) for s in task.get("sources") or []):
            label = _convert.CONVERT_LABELS.get(task.get("mode"), "CHD")
            cur = state["current"]
            if cur.get("task") == name:
                pct = cur.get("pct")
                return None, "Being compressed to %s%s: it plays once that's done" % (
                    label, " (%d%%)" % pct if isinstance(pct, (int, float)) else "")
            return None, "Waiting to be compressed to %s: it plays once that's done" % label
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
    for p in paths:
        if os.path.isdir(p) and os.path.splitext(p)[1] and accepts(p):
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
    """{"system/rom id": {tag: {"path": server path, "size": bytes or None}}}."""
    try:
        with open(media_skips_path()) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _save_skips(d):
    while len(d) > SKIPS_MAX:
        del d[next(iter(d))]
    os.makedirs(_core.DATA_DIR, exist_ok=True)
    _core.atomic_write_json(media_skips_path(), d)


def record_media_skip(system, rom_id, tag, path, size=None):
    """Note that `tag` for this game couldn't be fetched from `path` (size: it was
    over the video cap, that big; None: the file didn't come)."""
    if not rom_id:
        return
    d = media_skips()
    key = "%s/%s" % (system, rom_id)
    game = d.pop(key, None) or {}
    game[tag] = {"path": path, "size": size}
    d[key] = game
    _save_skips(d)


def clear_media_skip(system, rom_id, tag):
    """The tag came after all: forget its note."""
    d = media_skips()
    key = "%s/%s" % (system, rom_id)
    if rom_id and tag in (d.get(key) or {}):
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
    noted = skips.get("%s/%s" % (entry.get("system"), entry.get("rom_id"))) or {}
    out = set()
    for tag, paths in (entry.get("media") or {}).items():
        n = noted.get(tag)
        if isinstance(n, dict) and paths and n.get("path") == paths[0]:
            if n.get("size") is None or n["size"] > es_video_max(cfg):
                out.add(tag)
    if "manual" in noted and entry.get("manual") and noted["manual"].get("path") == entry["manual"]:
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
