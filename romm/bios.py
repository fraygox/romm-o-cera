"""BIOS / firmware: what ES needs, what's missing, fetching it from RomM."""
import json
import os
import time

from . import core as _core, api as _api


# ----------------------------------------------------------- BIOS

# Batocera's BIOS list (batocera-systems) is keyed by system name; a few of
# our roms folders use a different key there.
BIOS_ALIASES = {"megacd": ["segacd"], "pcenginecd": ["pcengine"], "fbneo": ["neogeo"],
                "msx1": ["msx"]}
_BIOS_DB = None
_MD5 = {}


def bios_requirements():
    """{system: {"biosFiles": [{"md5", "file"}...]}} from Batocera's own list
    (empty off-box)."""
    global _BIOS_DB
    if _BIOS_DB is None:
        try:
            import runpy
            _BIOS_DB = runpy.run_path(_core.BIOS_DB, run_name="romm_browser_bios").get("systems", {})
        except Exception:
            _BIOS_DB = {}
    return _BIOS_DB


def file_md5(path):
    st = os.stat(path)
    key = (path, st.st_size, st.st_mtime)
    if key not in _MD5:
        import hashlib
        h = hashlib.md5()
        with open(path, "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""):
                h.update(b)
        _MD5[key] = h.hexdigest()
    return _MD5[key]


def _md5s(b):
    """The checksums Batocera accepts for one BIOS entry, lower-case. Its list gives one as a
    string, and from Batocera 44 a list of them where several dumps of a file are good."""
    v = b.get("md5")
    return [m.lower() for m in ([v] if isinstance(v, str) else v or []) if isinstance(m, str) and m]


def _accepted(m):
    """Every checksum that is right for one missing_bios() entry."""
    return m.get("md5s") or [m["md5"]]


def missing_bios(system):
    """BIOS files Batocera lists for this system that are absent or have the
    wrong checksum. (Some systems list alternatives — any one works.) Each entry has
    "md5s", every checksum accepted for the file, and "md5", the first of them."""
    out, seen = [], set()
    db = bios_requirements()
    for key in [system] + BIOS_ALIASES.get(system, []):
        for b in (db.get(key) or {}).get("biosFiles", []):
            md5s = _md5s(b)
            if b.get("zippedFile") or not b.get("file") or not md5s:
                continue
            path = os.path.join(_core.USERDATA, b["file"])
            if path in seen:
                continue
            seen.add(path)
            try:
                if os.path.isfile(path) and file_md5(path) in md5s:
                    continue
            except OSError:
                pass
            out.append({"path": path, "md5": md5s[0], "md5s": md5s, "file": b["file"]})
    return out


def bios_offer(api, system, platform_id):
    """[(missing entry, RomM firmware)] that RomM can supply, matched by MD5."""
    missing = missing_bios(system)
    if not missing or not platform_id:
        return []
    by_md5 = {}
    for fw in api.firmware(platform_id):
        if fw.get("md5_hash"):
            by_md5.setdefault(fw["md5_hash"].lower(), fw)
    return [(m, by_md5[h]) for m in missing for h in [next((h for h in _accepted(m) if h in by_md5), None)] if h]


def bios_fetched():
    """{"bios/x.bin": {"when", "from", "system"}} — what RomM Browser fetched."""
    try:
        with open(os.path.join(_core.DATA_DIR, "bios-fetched.json")) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _record_bios(entries):
    data = bios_fetched()
    data.update(entries)
    os.makedirs(_core.DATA_DIR, exist_ok=True)
    _core.atomic_write_json(os.path.join(_core.DATA_DIR, "bios-fetched.json"), data, indent=1)


def bios_all_missing(system):
    """The BIOS files Batocera lists for this system when not one of them is here (as names:
    "bios/x.bin"), else []. Some systems list alternatives (a file per region: any one works),
    so only "none at all" says a game there probably won't start."""
    required = bios_required(system)
    return required if required and len(missing_bios(system)) >= len(required) else []


def bios_required(system):
    """Distinct BIOS files Batocera lists for this system (with checksums)."""
    out, seen = [], set()
    db = bios_requirements()
    for key in [system] + BIOS_ALIASES.get(system, []):
        for b in (db.get(key) or {}).get("biosFiles", []):
            if b.get("zippedFile") or not b.get("file") or not _md5s(b) or b["file"] in seen:
                continue
            seen.add(b["file"])
            out.append(b["file"])
    return out


def bios_status(api, platforms_by_system, systems_with_games=()):
    """Per system that needs BIOS: [{"system", "name", "required", "missing",
    "offers", "fetched"}], for systems with games on the box or BIOS that
    RomM can supply. missing = entries absent/wrong; offers = the subset RomM
    has (matched by MD5)."""
    db = bios_requirements()
    rec = bios_fetched()
    out = []
    systems = [s for s in sorted(set(platforms_by_system) | set(systems_with_games)) if bios_required(s)]
    missing_in = {s: missing_bios(s) for s in systems}
    # every platform's firmware list at once (one request each)
    firmware = _api.fetch_many(api.firmware, {pid for s in systems if missing_in[s]
                                              for pid in platforms_by_system.get(s, [])}) if api is not None else {}
    for system in systems:
        required = bios_required(system)
        missing = missing_in[system]
        offers, seen = [], set()
        if missing and api is not None:
            want = {h: m for m in missing for h in _accepted(m)}
            for pid in platforms_by_system.get(system, []):
                try:
                    fws = _api.fetched(firmware[pid])
                except _core.RommError:
                    continue
                for fw in fws:
                    md5 = (fw.get("md5_hash") or "").lower()
                    if md5 in want and want[md5]["file"] not in seen:
                        seen.add(want[md5]["file"])
                        offers.append((want[md5], fw))
        if system not in systems_with_games and not offers:
            continue
        name = next(((db.get(k) or {}).get("name") for k in [system] + BIOS_ALIASES.get(system, [])
                     if (db.get(k) or {}).get("name")), system)
        out.append({"system": system, "name": name, "required": required, "missing": missing,
                    "offers": offers,
                    "fetched": {f: r for f, r in rec.items() if f in required}})
    return out


def fetch_bios(api, offers, system=None):
    """Download offered BIOS files to Batocera's paths; MD5-verified, an
    existing wrong file is kept as .bak-romm. Returns the files written."""
    written = []
    record = {}
    try:
        _fetch_bios_into(api, offers, system, written, record)
    finally:
        if record:
            _record_bios(record)              # what got written, even if a later file failed
    return written


def _fetch_bios_into(api, offers, system, written, record):
    biosdir = os.path.realpath(os.path.join(_core.USERDATA, "bios"))
    for m, fw in offers:
        if not os.path.realpath(m["path"]).startswith(biosdir + os.sep):
            continue
        os.makedirs(os.path.dirname(m["path"]), exist_ok=True)
        part = m["path"] + ".part"
        api.download_firmware(fw["id"], fw["file_name"], part, _api.BIOS_MAX, "BIOS",
                              "BIOS download larger than %s; refused." % _core.human_size(_api.BIOS_MAX))
        if file_md5(part) not in _accepted(m):
            os.remove(part)
            raise _core.RommError("BIOS checksum mismatch for %s" % m["file"])
        if os.path.exists(m["path"]):
            os.replace(m["path"], m["path"] + ".bak-romm")
        os.replace(part, m["path"])
        written.append(m["file"])
        record[m["file"]] = {"when": time.strftime("%Y-%m-%d %H:%M"), "from": fw.get("file_name"),
                             "system": system}
        _core.log("BIOS %s <- RomM firmware %s" % (m["file"], fw["file_name"]))
