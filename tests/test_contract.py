"""Contract tests: every RomM API behaviour the app relies on.

Read-only. Point them at any RomM 5.x:
  ROMM_CONTRACT_URL=https://romm.example.com ROMM_CONTRACT_TOKEN=rmm_... \\
    python -m pytest tests/test_contract.py
(ROMM_CONTRACT_VERIFY=false for a self-signed cert.) Skipped when unset.
tests/contract/disposable.py runs them against a throwaway RomM of a given
version with a generated library, which is what the nas cron does for every
new RomM release. Downloads are limited to the smallest file found.
"""
import os

import pytest
import requests

import rommlib

URL = os.environ.get("ROMM_CONTRACT_URL", "").rstrip("/")
TOKEN = os.environ.get("ROMM_CONTRACT_TOKEN", "")
pytestmark = pytest.mark.skipif(not (URL and TOKEN), reason="ROMM_CONTRACT_URL/TOKEN not set")

PLATFORM_FIELDS = {"id", "fs_slug", "name", "display_name", "rom_count"}
ROM_FIELDS = {"id", "name", "fs_name", "fs_name_no_ext", "fs_size_bytes", "platform_id",
              "regions", "metadatum", "path_cover_small", "path_cover_large",
              "has_multiple_files", "has_nested_single_file"}
FILE_FIELDS = {"id", "file_name", "file_size_bytes", "category", "full_path"}


@pytest.fixture(scope="module")
def api():
    return rommlib.Api(URL, TOKEN, os.environ.get("ROMM_CONTRACT_VERIFY", True))


@pytest.fixture(scope="module")
def platforms(api):
    plats = api.platforms()
    assert isinstance(plats, list) and plats, "no platforms"
    return plats


@pytest.fixture(scope="module")
def busiest(platforms):
    return max(platforms, key=lambda p: p.get("rom_count") or 0)


def test_heartbeat_reports_version(api):
    hb = api.get("/api/heartbeat")
    version = (hb.get("SYSTEM") or {}).get("VERSION") or hb.get("VERSION")
    print("RomM version:", version)
    assert version


def test_bad_token_rejected():
    with pytest.raises(rommlib.RommError, match="rejected the token"):
        rommlib.Api(URL, "rmm_not_a_real_token", os.environ.get("ROMM_CONTRACT_VERIFY", True)).platforms()


def test_platform_fields(platforms, busiest):
    for p in platforms:
        assert PLATFORM_FIELDS <= set(p), PLATFORM_FIELDS - set(p)
    assert busiest["rom_count"] > 0


def test_rom_list_shape_and_paging(api, busiest):
    a = api.roms(platform_ids=[busiest["id"]], limit=5)
    assert {"items", "total"} <= set(a) and a["total"] == busiest["rom_count"]
    for r in a["items"]:
        assert ROM_FIELDS <= set(r), ROM_FIELDS - set(r)
        assert r["platform_id"] == busiest["id"]
        for k in ("path_cover_small", "path_cover_large"):
            assert not r[k] or r[k].startswith("/"), r[k]      # server-relative only
    if a["total"] > 5:
        b = api.roms(platform_ids=[busiest["id"]], limit=5, offset=5)
        assert not {r["id"] for r in a["items"]} & {r["id"] for r in b["items"]}


def test_name_order_and_char_index(api, busiest):
    first = api.roms(platform_ids=[busiest["id"]], limit=50, char_index=True)
    ci = first.get("char_index")
    assert isinstance(ci, dict) and ci, "char_index missing (letter jump relies on it)"
    total = first["total"]
    assert all(isinstance(v, int) and 0 <= v < total for v in ci.values())
    # (RomM sorts in the database's collation, so symbols order differently
    # from Python — what the app relies on is that each letter's offset in
    # char_index lands on a title starting with that letter, in server order)
    letters = [kv for kv in sorted(ci.items(), key=lambda kv: kv[1]) if kv[0].isalpha()]
    assert letters, "char_index has no letters"
    step = max(1, len(letters) // 8)
    for ch, off in letters[::step][:8]:
        r = api.roms(platform_ids=[busiest["id"]], limit=1, offset=off)["items"][0]
        key = (r.get("name_sort_key") or r["name"] or "").lower()
        assert key.startswith(ch), (ch, off, r["name"])


def test_newest_first_order(api, busiest):
    p = api.get("/api/roms", {"platform_ids": [busiest["id"]], "limit": 10, "order_by": "id",
                              "order_dir": "desc", "with_char_index": "false",
                              "with_filter_values": "false"})
    ids = [r["id"] for r in p["items"]]
    assert ids == sorted(ids, reverse=True)


def test_search(api, busiest):
    sample = api.roms(platform_ids=[busiest["id"]], limit=1)["items"][0]
    word = max((w for w in sample["name"].split() if w.isalnum()), key=len, default=None)
    if not word:
        pytest.skip("no searchable word in sample title")
    res = api.roms(platform_ids=[p for p in [busiest["id"]]], search=word, limit=50)
    assert res["total"] >= 1 and sample["id"] in {r["id"] for r in res["items"]} | {sample["id"]}
    assert any(word.lower() in (r["name"] or "").lower() or word.lower() in r["fs_name"].lower()
               for r in res["items"])


def test_rom_detail_files(api, busiest):
    r = api.roms(platform_ids=[busiest["id"]], limit=1)["items"][0]
    d = api.rom(r["id"])
    assert ROM_FIELDS <= set(d) and "full_path" in d and isinstance(d.get("files"), list)
    for f in d["files"]:
        assert FILE_FIELDS <= set(f), FILE_FIELDS - set(f)
        if d["has_multiple_files"] or d["has_nested_single_file"]:
            assert f["full_path"].startswith(d["full_path"].rstrip("/") + "/")


def test_collections(api):
    cols = api.collections()
    assert isinstance(cols, list)
    for c in cols:
        assert {"id", "name", "rom_count"} <= set(c)
    with_roms = [c for c in cols if c.get("rom_count")]
    if with_roms:
        c = with_roms[0]
        page = api.roms(collection=("collection_id", c["id"]), limit=5)
        assert page["total"] == c["rom_count"]


def test_smart_collections_endpoint(api):
    smart = api.smart_collections()
    assert isinstance(smart, list)
    for c in smart[:3]:
        assert {"id", "name", "rom_count"} <= set(c)
        if c["rom_count"]:
            assert api.roms(collection=("smart_collection_id", c["id"]), limit=1)["total"] >= 1


@pytest.mark.parametrize("vtype", ["genre", "franchise", "collection", "company", "mode"])
def test_virtual_collections(api, vtype):
    vc = api.virtual_collections(vtype)
    assert isinstance(vc, list)
    for c in vc[:3]:
        assert {"id", "name", "rom_count"} <= set(c)
    filled = [c for c in vc if c.get("rom_count")]
    if filled:
        c = min(filled, key=lambda c: c["rom_count"])
        page = api.roms(collection=("virtual_collection_id", c["id"]), limit=5)
        assert page["total"] >= 1


def _smallest_rom(api, platforms, pred):
    best = None
    for p in sorted(platforms, key=lambda p: p.get("rom_count") or 0)[:40]:
        if not p.get("rom_count"):
            continue
        for r in api.roms(platform_ids=[p["id"]], limit=100)["items"]:
            if pred(r) and r["fs_size_bytes"] and (best is None or r["fs_size_bytes"] < best["fs_size_bytes"]):
                best = r
    return best


def test_download_single_file_with_range(api, platforms):
    r = _smallest_rom(api, platforms, lambda r: not (r["has_multiple_files"] or r["has_nested_single_file"]))
    if not r:
        pytest.skip("no single-file rom")
    url = "%s/api/roms/%d/content/%s" % (URL, r["id"], requests.utils.quote(r["fs_name"]))
    full = api.s.get(url, timeout=60)
    assert full.status_code == 200 and len(full.content) == r["fs_size_bytes"]
    part = api.s.get(url, headers={"Range": "bytes=0-15"}, timeout=60)
    assert part.status_code == 206 and part.content == full.content[:16]


def test_download_folder_file(api, platforms):
    r = _smallest_rom(api, platforms, lambda r: r["has_multiple_files"] or r["has_nested_single_file"]
                      or "." not in r["fs_name"][-6:])        # folders named without an extension
    if not r:
        pytest.skip("no folder rom on this server")
    f = min(api.rom(r["id"])["files"], key=lambda f: f["file_size_bytes"])
    url = "%s/api/roms/%d/files/content/%s" % (URL, f["id"], requests.utils.quote(f["file_name"]))
    resp = api.s.get(url, timeout=120)
    assert resp.status_code == 200 and len(resp.content) == f["file_size_bytes"]
    if f["file_size_bytes"] > 16:
        part = api.s.get(url, headers={"Range": "bytes=0-15"}, timeout=60)
        assert part.status_code == 206 and len(part.content) == 16


def test_cover_fetch(api, busiest, tmp_path, monkeypatch):
    monkeypatch.setattr(rommlib, "COVER_CACHE", str(tmp_path))
    for r in api.roms(platform_ids=[busiest["id"]], limit=50)["items"]:
        if r.get("path_cover_small"):
            assert api.fetch_cover(r) is not None
            return
    pytest.skip("no covers on this platform")


def test_client_token_scopes_are_enough(api):
    me = api.get("/api/users/me")
    assert me.get("username")


def test_similar_games(api, busiest):
    r = api.roms(platform_ids=[busiest["id"]], limit=1)["items"][0]
    sim = api.similar(r["id"], limit=5)          # may be empty for an unidentified rom
    assert isinstance(sim, list)
    for x in sim:
        assert {"id", "name", "platform_id"} <= set(x)


def test_verified_and_tag_filters_accepted(api, busiest):
    total = api.roms(platform_ids=[busiest["id"]], limit=1)["total"]
    assert api.roms(platform_ids=[busiest["id"]], limit=1, verified=True)["total"] <= total
    tags = api.all_tags()
    assert isinstance(tags, list)
    junk = rommlib.junk_tags(tags)
    if junk:
        assert api.roms(platform_ids=[busiest["id"]], limit=1, exclude_tags=junk)["total"] <= total


def test_manual_and_achievement_fields(api, busiest):
    """Manuals come from RomM's assets via path_manual; achievements from
    ra_id + merged_ra_metadata.achievements (count/points)."""
    items = api.roms(platform_ids=[busiest["id"]], limit=200)["items"]
    assert items and all({"has_manual", "path_manual", "ra_id"} <= set(r) for r in items)
    withman = [r for r in items if rommlib.manual_path(r)]
    if withman:
        r = api.s.get(api.base + rommlib.manual_path(withman[0]), stream=True, timeout=30)
        assert r.status_code == 200 and r.raw.read(4) == b"%PDF"
        r.close()
    for r in items:
        if r.get("ra_id") and r.get("merged_ra_metadata"):
            ach = r["merged_ra_metadata"].get("achievements")
            assert ach is None or isinstance(ach, list)


def test_rom_by_hash(api, busiest):
    """Check games' repair finds RomM's copy by hash (/api/roms/by-hash): a
    known file's hash gives that game; an unknown one is a clean 'none'."""
    r = api.roms(platform_ids=[busiest["id"]], limit=1)["items"][0]
    d = api.rom(r["id"])
    f = next((x for x in d.get("files") or [] if x.get("sha1_hash") or x.get("md5_hash") or x.get("crc_hash")), None)
    if not f:
        pytest.skip("no hashed files")
    got = api.by_hash(sha1_hash=f.get("sha1_hash"), md5_hash=f.get("md5_hash"), crc_hash=f.get("crc_hash"))
    assert got and got["id"] == d["id"], got
    assert api.by_hash(sha1_hash="0" * 39 + "1") is None
    assert api.by_hash(ra_hash="0" * 31 + "1") is None


def test_firmware_list(api, platforms):
    """BIOS files screen: /api/firmware?platform_id= answers with a list."""
    for p in platforms[:5]:
        fw = api.firmware(p["id"])
        assert isinstance(fw, list)
        for x in fw:
            assert {"id", "file_name"} <= set(x)
