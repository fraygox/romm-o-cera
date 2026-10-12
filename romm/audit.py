"""Check games: compare what's here with RomM; game info for games already here."""
import json
import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET
import zipfile

from . import (core as _core, discs as _discs, es as _es, api as _api, convert as _convert, library as _library,
               downloader as _downloader)


def disc_layout(detail, system, cfg, accepts=None):
    """What a download of this game is and becomes, from RomM's full record
    (files and archive members): {"label": "CUE/BIN, 3 tracks", "discs": n,
    "unpacked": bytes or None, "estimated": unpacked is a guess,
    "convert": "CHD" | "RVZ" | "squashfs" | "ZCCI" | "ZAR" | None}. None for a
    game that isn't a disc image (carts), or when RomM listed no files."""
    files = (detail or {}).get("files") or []
    if not files:
        return None
    members, sizes_known, archived = [], True, False
    for f in files:
        name = f.get("file_name") or ""
        if os.path.splitext(name)[1].lower() in _core.ARCHIVE_EXTS and system not in _core.ZIP_NATIVE:
            archived = True
            inner = f.get("archive_members") or []
            if not inner:
                sizes_known = False
            members += [(m.get("name") or "", m.get("size")) for m in inner]
        else:
            members.append((name, f.get("file_size_bytes")))
    exts = [os.path.splitext(n)[1].lower() for n, _ in members]
    if not any(e in _core.DISC_EXTS for e in exts):
        if archived and not sizes_known and system in _downloader.EXPANDS:     # an archive RomM didn't list
            return {"label": "", "discs": 0, "unpacked": _downloader.space_needed(detail, system), "estimated": True,
                    "convert": None}
        return None
    count = exts.count
    kinds = []
    if count(".cue"):
        tracks = count(".bin")
        kinds.append("CUE/BIN" + (", %d tracks" % tracks if tracks > count(".cue") else ""))
    if count(".gdi"):
        kinds.append("GDI, %d tracks" % (len(exts) - count(".gdi")) if len(exts) > count(".gdi") + 1 else "GDI")
    for e in (".iso", ".chd", ".rvz", ".wbfs", ".gcz", ".ciso", ".cso", ".pbp", ".ccd", ".cdi", ".3ds", ".cci",
              ".cxi", ".cia"):
        if count(e):
            kinds.append(e[1:].upper())
    discs = count(".cue") + count(".gdi") + count(".iso") + count(".chd") + count(".rvz") + count(".wbfs") \
        + count(".cso") + count(".cdi")
    unpacked, estimated = None, False
    if archived and sizes_known and all(s for _, s in members):
        unpacked = sum(s for _, s in members)        # loose files are in members too
    elif archived:
        unpacked, estimated = _downloader.space_needed(detail, system), True     # RomM didn't list sizes
    convs = (_convert.conversion_for(system, e, cfg, accepts) for e in exts)
    convert = next((c.label for c in convs if c is not None), None)
    return {"label": ", ".join(kinds), "discs": discs, "unpacked": unpacked, "estimated": estimated,
            "convert": convert}


TAG_RE = re.compile(r"\(([^)]*)\)")
NOT_THE_GAME = ("demo", "beta", "proto", "sample", "kiosk", "unl", "pirate", "hack")


def _tags(name):
    return {t.strip().lower() for g in TAG_RE.findall(name) for t in g.split(",")}


def find_in_romm(api, system, platform_ids, path, ra_hash=None):
    """RomM's copy of a game on the box whose file name RomM doesn't use:
    (rom, how) with how "ra" (same RetroAchievements hash: the same game data)
    or "name" (the one RomM title with the same name, disc and region — another
    revision or region spelling), else (None, None)."""
    if ra_hash and ra_hash.strip("0") and re.fullmatch(r"[0-9a-fA-F]{32}", ra_hash):
        # games without RetroAchievements: no hash in ES, or RomM has none -> the title match below
        try:
            r = api.by_hash(ra_hash=ra_hash.lower())
        except _core.RommError:
            r = None
        if r and (not platform_ids or r.get("platform_id") in platform_ids):
            return r, "ra"
    stem = os.path.splitext(os.path.basename(path))[0]
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
        if _library.strip_tags(n).lower() != title or any(any(b in t for b in NOT_THE_GAME) for t in tags):
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


def _chd_vs_romm(api, chd, rom, chdman, detail_of=None):
    """A CHD converted on the box against RomM's original (zip of cue/bin, or
    iso): None if it can't be compared; False if complete; else (have, need).
    detail_of: how to get RomM's record (default api.rom)."""
    try:
        detail = (detail_of or api.rom)(rom["id"])
    except _core.RommError:
        return None
    sizes = {}
    for f in detail.get("files") or []:
        if f.get("category") not in (None, "game"):
            continue
        for m in f.get("archive_members") or [{"name": f.get("file_name"), "size": f.get("file_size_bytes")}]:
            ext = os.path.splitext((m.get("name") or "").lower())[1]
            if m.get("size"):
                sizes.setdefault(ext, 0)
                sizes[ext] += m["size"]
    got = _chd_frames(chd, chdman)
    if not got:
        return None
    kind, have = got
    if kind == "cd" and sizes.get(".bin"):
        need = sizes[".bin"] // 2352
    elif kind == "dvd" and sizes.get(".iso"):
        need = sizes[".iso"] // 2048
    else:
        return None
    return (have, need) if have + 300 < need else False


def _images_in(path):
    """Disc images (.cue, .iso, .chd) at or under path, skipping media folders."""
    if os.path.isfile(path):
        return [path] if path.lower().endswith((".cue", ".iso", ".chd")) else []
    out = []
    for dp, dns, fns in os.walk(path):
        dns[:] = [x for x in dns if x not in _core.MEDIA_DIRS]
        out += [os.path.join(dp, f) for f in fns if f.lower().endswith((".cue", ".iso", ".chd"))]
    return out


def _not_a_game(n):
    low = n.lower()
    return n.startswith(".") or n in _core.MEDIA_DIRS or \
        low.endswith((".xml", ".txt", ".m3u", ".part", ".tmp.chd", ".chd.part"))


def _slim(rom):
    return {k: rom.get(k) for k in ("id", "name", "fs_name", "fs_name_no_ext", "platform_id")}


def audit(api, platforms_by_system, deep=False, progress=None, cancel=None, chdman="chdman"):
    """Check games on the box against RomM. Quick: missing files and sizes.
    deep: also checksums and `chdman verify`. Returns (findings, stats).
    A finding: {"system", "path", "problem", "severity", "rom" (or None)};
    severity "damaged" (unplayable/corrupt) or "differs" (not RomM's copy:
    another dump, a trimmed image… — may well play)."""
    findings, stats = [], {"checked": 0, "matched": 0, "unmatched": 0}

    def add(system, path, problem, rom=None, severity="damaged"):
        findings.append({"system": system, "path": path, "problem": problem,
                         "severity": severity, "rom": _slim(rom) if rom else None})

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

    def check_z3ds(system, path, files, rom):
        """A .zcci packed here: RomM has the .3ds it came from (loose or in a zip)."""
        stem = os.path.splitext(os.path.basename(path))[0].lower()
        orig = None
        for f in files:
            for name, size, crc in [(f.get("file_name"), f.get("file_size_bytes"), f.get("crc_hash"))] + \
                    [(m.get("name"), m.get("size"), m.get("crc_hash")) for m in (f.get("archive_members") or [])]:
                base, ext = os.path.splitext(os.path.basename(name or "").lower())
                if base == stem and ext in _convert.Z3DS_SOURCES:
                    orig = (size, (crc or "").lower())
        hdr = _convert.z3ds_header(path)
        if hdr is None:
            add(system, path, "Not a readable .zcci (damaged header)", rom)
        elif not hdr["complete"]:
            add(system, path, "Compressed file cut short", rom)
        elif orig and orig[0] and hdr["size"] != orig[0]:
            add(system, path, "Packed from a different file than RomM's (%s vs %s)" % (
                _core.human_size(hdr["size"]), _core.human_size(orig[0])), rom, "differs")
        elif deep:
            try:
                got, _ = _convert.z3ds_verify(path)
            except (_convert.Z3dsError, OSError) as e:
                add(system, path, "Compressed file damaged: %s" % e, rom)
                return
            if orig and orig[1] and got != orig[1]:
                add(system, path, "Checksum doesn't match RomM's copy", rom, "differs")

    def check_zip(system, path, files, rom):
        """A zip kept as-is: judge the ROM inside, not the container (the same
        ROM zips to different sizes)."""
        try:
            with zipfile.ZipFile(path) as z:
                local = {i.filename.lower(): i for i in z.infolist() if not i.is_dir()}
                if deep and z.testzip() is not None:
                    add(system, path, "Damaged zip (a file inside fails its CRC)", rom)
                    return
        except (zipfile.BadZipFile, OSError):
            add(system, path, "Damaged zip (can't be opened)", rom)
            return
        want = members_of(files)
        if not want:                                # RomM didn't record the contents
            if files and files[0].get("file_size_bytes") and \
                    os.path.getsize(path) != files[0]["file_size_bytes"]:
                add(system, path, "Differs from RomM's copy (size)", rom, "differs")
            return
        by_base = {os.path.basename(k): v for k, v in local.items()}
        for name, m in want.items():
            i = local.get(name) or by_base.get(os.path.basename(name))
            if i is None:
                add(system, path, "Missing inside the zip: %s" % m["name"], rom)
            elif m.get("crc_hash") and ("%08x" % i.CRC) != m["crc_hash"].lower().zfill(8):
                add(system, path, "Different ROM than RomM's copy (%s)" % m["name"], rom, "differs")

    for system in sorted(platforms_by_system):
        sysdir = os.path.join(_core.ROMS_ROOT, system)
        if not os.path.isdir(sysdir):
            continue
        index = {}
        for pid in platforms_by_system[system]:
            listed = api.roms_all(platform_ids=[pid], cancel=cancel)
            if listed is None:
                return findings, stats
            for r in listed:
                index.setdefault((r.get("fs_name_no_ext") or "").lower(), r)
                index.setdefault((r.get("fs_name") or "").lower(), r)
        names = [n for n in sorted(os.listdir(sysdir)) if not _not_a_game(n)]
        # RomM's record of each game, fetched a few at a time just ahead of the loop
        ids = []
        for n in names:
            rom = index.get(n.lower()) or index.get(
                (n if os.path.isdir(os.path.join(sysdir, n)) else _core.stem_of(n)).lower())
            if rom:
                ids.append(rom["id"])
        with _api.Prefetch(api.rom, ids) as records:
            for n in names:
                if cancel is not None and cancel.is_set():
                    return findings, stats
                path = os.path.join(sysdir, n)
                low = n.lower()
                if progress:
                    progress(system, n)
                stats["checked"] += 1
                # 1) disc sheets must find every track (needs no RomM at all)
                for sheet in _discs._sheets_in(path):
                    for ref in _discs.sheet_sources(sheet):
                        if not os.path.exists(ref):
                            add(system, sheet, "Missing track file: %s" % os.path.basename(ref),
                                index.get(os.path.splitext(n)[0].lower()) or index.get(low))
                # 1b) disc images cut short (an interrupted copy): the game can't
                # load what's past the end. Converted CHDs are compared with RomM's
                # original exactly (below); here, the disc's own filesystem says so
                for img in _images_in(path):
                    st = os.path.splitext(os.path.basename(img))[0].lower()
                    irom = index.get(st) or index.get(os.path.splitext(n)[0].lower()) or index.get(low)
                    if img.lower().endswith(".chd") and irom and \
                            not (irom.get("fs_name") or "").lower().endswith(".chd"):
                        exact = _chd_vs_romm(api, img, irom, chdman, records.get)
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
                stem = n if os.path.isdir(path) else _core.stem_of(n)
                rom = index.get(low) or index.get(stem.lower())
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
                    stats["unmatched"] += 1
                    continue
                stats["matched"] += 1
                if os.path.isfile(path) and low.endswith(".chd") and \
                        not (rom.get("fs_name") or "").lower().endswith(".chd"):
                    if deep:                                   # converted here: chdman checks itself
                        p = subprocess.run(["nice", "-n", "19", chdman, "verify", "-i", path],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
                        if p.returncode != 0:
                            add(system, path, "CHD failed chdman verify", rom)
                    continue
                try:
                    detail = records.get(rom["id"])
                except _core.RommError as e:
                    add(system, path, "Couldn't fetch RomM record (%s)" % e, None)
                    continue
                files = [f for f in (detail.get("files") or []) if f.get("category") in (None, "game")]
                if os.path.isfile(path) and low.endswith(_convert.Z3DS_EXTS):
                    check_z3ds(system, path, files, rom)
                    continue
                if os.path.isfile(path) and low.endswith(".zar"):
                    # packed here from the disc's files: RomM's image hashes can't match by design
                    bad = _convert.zar_check(path, full=deep)
                    if bad:
                        add(system, path, "Compressed file %s" % bad, rom)
                    continue
                if os.path.isfile(path):
                    if len(files) == 1 and files[0]["file_name"].lower() == low and low.endswith(".zip"):
                        check_zip(system, path, files, rom)
                        continue
                    if len(files) == 1 and files[0]["file_name"].lower() == low:
                        want = [(path, files[0])]               # same file as RomM (iso, chd…)
                    else:
                        members = {m["name"].lower(): m for f in files for m in (f.get("archive_members") or [])
                                   if m.get("name")}
                        m = members.get(low)
                        want = [(path, {"file_size_bytes": m.get("size"), "sha1_hash": m.get("sha1_hash"),
                                        "md5_hash": m.get("md5_hash"), "crc_hash": m.get("crc_hash")})] if m else []
                else:
                    if _library.rom_is_folder(detail, files):
                        prefix = (detail.get("full_path") or "").rstrip("/") + "/"
                        want = []
                        for f in files:
                            rel = f.get("full_path", "")
                            rel = rel[len(prefix):] if rel.startswith(prefix) else f["file_name"]
                            want.append((os.path.join(path, *rel.split("/")), f))
                    else:
                        want = [(os.path.join(path, *m["name"].split("/")),
                                 {"file_size_bytes": m.get("size"), "sha1_hash": m.get("sha1_hash"),
                                  "md5_hash": m.get("md5_hash"), "crc_hash": m.get("crc_hash")})
                                for f in files for m in (f.get("archive_members") or [])]
                    # a disc folder later turned into <folder>.chd keeps nothing to compare
                # discs converted to CHD on the box: the tracks are gone on purpose
                chds = {os.path.splitext(x)[0].lower(): os.path.join(path, x)
                        for x in (os.listdir(path) if os.path.isdir(path) else []) if x.lower().endswith(".chd")}
                converted = set()
                for fp, f in want:
                    st = os.path.splitext(os.path.basename(fp))[0].lower()
                    st = re.sub(r"\s*\(track \d+\)$", "", st)
                    if not os.path.exists(fp) and st in chds:
                        converted.add(chds[st])
                for c in converted:
                    if deep and subprocess.run(["nice", "-n", "19", chdman, "verify", "-i", c],
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
                        add(system, c, "CHD failed chdman verify", rom)
                for fp, f in want:
                    if cancel is not None and cancel.is_set():
                        return findings, stats
                    st = re.sub(r"\s*\(track \d+\)$", "", os.path.splitext(os.path.basename(fp))[0].lower())
                    if not os.path.exists(fp):
                        if st not in chds:
                            add(system, fp, "Missing file: %s" % os.path.basename(fp), rom)
                    elif _discs.xbox_cut(system, fp, f):
                        continue          # cut to its game partition here: RomM's size and hashes are the full disc's
                    elif f.get("file_size_bytes") and os.path.getsize(fp) != f["file_size_bytes"]:
                        small = os.path.getsize(fp) < f["file_size_bytes"]
                        # a smaller .iso is usually a trimmed image (plays); a short
                        # track or cart file is a broken copy
                        trimmed = small and fp.lower().endswith((".iso", ".xiso"))
                        add(system, fp, "%s than RomM's copy: %s vs %s%s" % (
                            "Smaller" if small else "Bigger", _core.human_size(os.path.getsize(fp)),
                            _core.human_size(f["file_size_bytes"]),
                            " (trimmed or incomplete)" if trimmed else " (incomplete)" if small else ""),
                            rom, "differs" if (trimmed or not small) else "damaged")
                    elif deep and _downloader.file_hash_ok(fp, f)[0] is False:
                        add(system, fp, "Checksum doesn't match RomM", rom)
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
    replaced) drops out on load."""
    out = []
    for f in findings:
        try:
            st = os.stat(f["path"])
            sig = [st.st_size, int(st.st_mtime)]
        except OSError:
            sig = None
        out.append(dict(f, sig=sig))
    os.makedirs(_core.DATA_DIR, exist_ok=True)
    _core.atomic_write_json(audit_path(), {"time": time.time(), "deep": bool(deep), "stats": stats, "findings": out})


def load_audit():
    """The last finished check, or None: {"time", "deep", "stats", "findings"},
    without findings whose file is gone or changed since."""
    try:
        with open(audit_path()) as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    keep = []
    for f in d.get("findings") or []:
        if not isinstance(f, dict) or not all(isinstance(f.get(k), str) for k in ("path", "system", "severity",
                                                                                  "problem")):
            continue
        try:
            st = os.stat(f["path"])
        except OSError:
            if f.get("sig") is None:            # missing when checked, still missing ("Missing file: …")
                keep.append(f)
            continue
        if f.get("sig") in (None, [st.st_size, int(st.st_mtime)]):
            keep.append(f)
    d["findings"] = keep
    if not isinstance(d.get("stats"), dict):
        d["stats"] = {}
    return d


# ----------------------------------------------------------- game info for games already here

INFO_TAGS = ("name", "desc", "image")


def _gamelist_info(sysdir):
    """{relative path: set of filled tags} from roms/<sys>/gamelist.xml (read only)."""
    out = {}
    try:
        root = ET.parse(os.path.join(sysdir, "gamelist.xml")).getroot()
    except (OSError, ET.ParseError):
        return out
    for g in root.findall("game"):
        p = g.findtext("path")
        if p:
            out[p] = {t for t in INFO_TAGS + ("manual",) if (g.findtext(t) or "").strip()}
    return out


def plan_info(api, system, platform_ids, es, manuals=True, cancel=None):
    """Gamelist entries for games already in roms/<system> that RomM knows
    (matched by file name, like "already on this box") and whose ES entry
    lacks a name, description or cover. Entries carry "fill": the merge only
    sets what's missing. Returns (entries, stats)."""
    sysdir = os.path.join(_core.ROMS_ROOT, system)
    stats = {"on_box": 0, "matched": 0, "complete": 0, "queued": 0}
    if not os.path.isdir(sysdir):
        return [], stats
    index, sets = {}, {}
    for pid in platform_ids:
        if cancel is not None and cancel.is_set():
            return [], stats
        listed = api.roms_all(platform_ids=[pid], cancel=cancel)
        if listed is None:
            return [], stats
        for r in listed:
            index.setdefault((r.get("fs_name_no_ext") or "").lower(), r)
            index.setdefault((r.get("fs_name") or "").lower(), r)
            d = _library.disc_info(r.get("fs_name_no_ext") or "")
            if d and (d[0].lower() not in sets or d[1] < sets[d[0].lower()][0]):
                sets[d[0].lower()] = (d[1], r)          # a set's .m3u gets disc 1's info
    have = _gamelist_info(sysdir)
    names = sorted(os.listdir(sysdir))
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
        got = have.get("./" + os.path.relpath(prim, sysdir), set())
        if set(INFO_TAGS) <= got and (not manuals or not _library.manual_path(rom) or "manual" in got):
            stats["complete"] += 1
            continue
        e = _library.gamelist_entry(rom, system, [prim], manual=manuals)
        e["fill"] = True
        if title:
            e["name"] = title
        entries.append(e)
    stats["queued"] = len(entries)
    return entries, stats


def queue_info(entries):
    if entries:
        os.makedirs(_core.DATA_DIR, exist_ok=True)
        _core.append_gamelist(*entries)
