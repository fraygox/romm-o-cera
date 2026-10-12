"""BIOS / firmware: what ES needs, what's missing, fetching it from RomM."""
import json
import os
import time
import urllib.parse

import requests

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


def missing_bios(system):
    """BIOS files Batocera lists for this system that are absent or have the
    wrong checksum. (Some systems list alternatives — any one works.)"""
    out, seen = [], set()
    db = bios_requirements()
    for key in [system] + BIOS_ALIASES.get(system, []):
        for b in (db.get(key) or {}).get("biosFiles", []):
            if b.get("zippedFile") or not b.get("file") or not b.get("md5"):
                continue
            path = os.path.join(_core.USERDATA, b["file"])
            if path in seen:
                continue
            seen.add(path)
            try:
                if os.path.isfile(path) and file_md5(path) == b["md5"].lower():
                    continue
            except OSError:
                pass
            out.append({"path": path, "md5": b["md5"].lower(), "file": b["file"]})
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
    return [(m, by_md5[m["md5"]]) for m in missing if m["md5"] in by_md5]


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


def bios_required(system):
    """Distinct BIOS files Batocera lists for this system (with checksums)."""
    out, seen = [], set()
    db = bios_requirements()
    for key in [system] + BIOS_ALIASES.get(system, []):
        for b in (db.get(key) or {}).get("biosFiles", []):
            if b.get("zippedFile") or not b.get("file") or not b.get("md5") or b["file"] in seen:
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
    for system in sorted(set(platforms_by_system) | set(systems_with_games)):
        required = bios_required(system)
        if not required:
            continue
        missing = missing_bios(system)
        offers, seen = [], set()
        if missing and api is not None:
            want = {m["md5"]: m for m in missing}
            for pid in platforms_by_system.get(system, []):
                try:
                    fws = api.firmware(pid)
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
        try:
            r = api.s.get("%s/api/firmware/%d/content/%s" % (
                api.base, fw["id"], urllib.parse.quote(fw["file_name"])), timeout=60, stream=True)
        except Exception as e:
            raise _core.RommError("BIOS download failed (%s)" % e.__class__.__name__) from None
        if r.status_code != 200:
            r.close()
            raise _core.RommError("BIOS download failed: HTTP %d" % r.status_code)
        try:                                     # streamed to disk, capped: never whole in memory
            n = 0
            with open(part, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    n += len(chunk)
                    if n > _api.BIOS_MAX:
                        raise _core.RommError("BIOS download larger than %s; refused." % _core.human_size(_api.BIOS_MAX))
                    f.write(chunk)
        except (_core.RommError, requests.RequestException, OSError) as e:
            try:
                os.remove(part)
            except OSError:
                pass
            if isinstance(e, _core.RommError):
                raise
            raise _core.RommError("BIOS download failed (%s)" % e.__class__.__name__) from None
        finally:
            r.close()
        if file_md5(part) != m["md5"]:
            os.remove(part)
            raise _core.RommError("BIOS checksum mismatch for %s" % m["file"])
        if os.path.exists(m["path"]):
            os.replace(m["path"], m["path"] + ".bak-romm")
        os.replace(part, m["path"])
        written.append(m["file"])
        record[m["file"]] = {"when": time.strftime("%Y-%m-%d %H:%M"), "from": fw.get("file_name"),
                             "system": system}
        _core.log("BIOS %s <- RomM firmware %s" % (m["file"], fw["file_name"]))
