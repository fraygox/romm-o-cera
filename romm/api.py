"""The RomM API client, and pairing a box with a code."""
import os
import re

import requests

from . import core as _core, config as _config, covers as _covers


class Api:
    def __init__(self, url, token, verify_tls=True):
        self.base = (url or "").strip().rstrip("/")
        self.s = requests.Session()
        self.s.verify = _config.norm_verify(verify_tls)
        self.s.headers.update({"Authorization": "Bearer " + (token or "").strip(),
                               "User-Agent": _core.UA})

    @classmethod
    def from_config(cls, cfg):
        return cls(_config.config_value(cfg, "url"), _config.config_value(cfg, "token"), _config.config_value(cfg, "verify_tls"))

    def get(self, path, params=None, timeout=20):
        if not self.base:
            raise _core.RommError("No RomM server set — open Settings")
        if _config.url_has_userinfo(self.base):
            raise _core.RommError("Remove user:password@ from the server URL — the token is the login")
        try:
            r = self.s.get(self.base + path, params=params, timeout=timeout)
        except requests.exceptions.SSLError:
            raise _core.RommError("TLS certificate rejected — set verify_tls in Settings") from None
        except requests.RequestException as e:
            raise _core.RommError("Can't reach RomM (%s)" % e.__class__.__name__) from None
        except Exception as e:      # e.g. ValueError from a bad header: its text can hold the token
            raise _core.RommError("Request failed (%s)" % e.__class__.__name__) from None
        if r.status_code in (401, 403):
            raise _core.RommError("RomM rejected the token (HTTP %d)" % r.status_code)
        if r.status_code != 200:
            raise _core.RommError("RomM error HTTP %d on %s" % (r.status_code, path))
        try:
            return r.json()
        except ValueError:
            raise _core.RommError("Not a RomM API response — check the URL (auth proxy in front?)")

    def platforms(self):
        return self.get("/api/platforms")

    def collections(self):
        return self.get("/api/collections")

    def smart_collections(self):
        return self.get("/api/collections/smart")

    def virtual_collections(self, vtype):
        return self.get("/api/collections/virtual", {"type": vtype})

    def roms(self, platform_ids=None, search=None, collection=None,
             offset=0, limit=60, order_by="name", char_index=False, regions=None,
             verified=None, exclude_tags=None):
        """collection: (param, id) with param one of collection_id,
        smart_collection_id, virtual_collection_id."""
        params = {"offset": offset, "limit": limit,
                  "with_char_index": "true" if char_index else "false",
                  "with_filter_values": "false", "with_rom_id_index": "false",
                  "order_by": order_by, "order_dir": "asc"}
        if platform_ids:
            params["platform_ids"] = list(platform_ids)
        if search:
            params["search_term"] = search
            params["order_by"] = ""   # relevance order
        if collection:
            params[collection[0]] = collection[1]
        if regions:
            params["regions"] = list(regions)
            params["regions_logic"] = "any"
        if verified:
            params["verified"] = "true"
        if exclude_tags:
            params["tags"] = list(exclude_tags)
            params["tags_logic"] = "none"
        return self.get("/api/roms", params)

    def by_hash(self, **hashes):
        """RomM's game with this hash (crc_hash / md5_hash / sha1_hash / ra_hash), or None."""
        params = {k: v for k, v in hashes.items() if v}
        if not params:
            return None
        try:
            r = self.get("/api/roms/by-hash", params)
        except _core.RommError as e:
            if "HTTP 404" in str(e) or "HTTP 422" in str(e):
                return None
            raise
        if isinstance(r, list):
            r = r[0] if r else None
        return r if isinstance(r, dict) and r.get("id") else None

    def rom(self, rom_id):
        return self.get("/api/roms/%d" % rom_id)

    def similar(self, rom_id, limit=30):
        """RomM's similar games, as plain rom dicts (best match first)."""
        out = self.get("/api/roms/%d/similar" % rom_id, {"limit": limit})
        return [x["rom"] for x in out if isinstance(x, dict) and x.get("rom")]

    def all_tags(self):
        """Every filename tag in the library (for the "hide prototypes…" filter)."""
        page = self.get("/api/roms", {"limit": 1, "with_filter_values": "true",
                                      "with_char_index": "false", "with_rom_id_index": "false"})
        return (page.get("filter_values") or {}).get("tags") or []

    def firmware(self, platform_id):
        return self.get("/api/firmware", {"platform_id": platform_id})

    def fetch_cover(self, rom, large=False):
        """Cached cover for a rom (path or None). Blocking. large=True for
        high-DPI screens (the small cover is ~300 px tall)."""
        keys = ("path_cover_large", "path_cover_small") if large else \
            ("path_cover_small", "path_cover_large")
        path = rom.get(keys[0]) or rom.get(keys[1])
        if not path or not path.startswith("/") or path.startswith("//"):
            return None             # only server-relative paths: the session carries the token
        os.makedirs(_core.COVER_CACHE, exist_ok=True)
        dest = os.path.join(_core.COVER_CACHE, "%d%s.img" % (rom["id"], "-L" if large else ""))
        if os.path.exists(dest):
            _covers.touch_cached(dest)
            return dest
        try:
            r = self.s.get(self.base + path, timeout=15, stream=True)
            data = _read_capped(r, COVER_MAX) if r.status_code == 200 else b""
            if data:
                tmp = dest + ".tmp"
                _core.write_png(data, tmp)
                os.replace(tmp, dest)
                self._cache_written = getattr(self, "_cache_written", 0) + os.path.getsize(dest)
                if self._cache_written > _covers.PRUNE_EVERY and getattr(self, "cache_limit", 0):
                    self._cache_written = 0
                    _covers.prune_cache(self.cache_limit)
                return dest
        except (requests.RequestException, OSError, _core.RommError):
            pass
        return None

    def exchange_pair_code(self, code):
        return exchange_pair_code(self.base, code, self.s.verify)


# ----------------------------------------------------------------- pairing
# RomM 5.x: the owner of a client API token presses Pair on it in RomM's web
# UI and gets a short single-use code (8 characters, valid about a minute);
# the device swaps it for the token, unauthenticated. RomM gives the token a
# NEW secret when it's exchanged, so anything else using that token stops
# working: one token per device.

PAIR_CODE_LEN = 8
PAIR_BAD_CODE = "That code isn't valid or has expired — make a new one in RomM"


def normalize_pair_code(code):
    """The code as RomM wants it (spaces/dashes dropped, upper case), or
    raise RommError with what's wrong. RomM's codes use A-Z and 2-9."""
    c = re.sub(r"[\s\-_.]", "", code or "").upper()
    if not c:
        raise _core.RommError("Type the pairing code from RomM")
    if not re.fullmatch(r"[A-Z0-9]+", c):
        raise _core.RommError("A pairing code is only letters and digits")
    if len(c) != PAIR_CODE_LEN:
        raise _core.RommError("A pairing code has %d characters (that was %d)" % (PAIR_CODE_LEN, len(c)))
    return c


def valid_token(tok):
    """A client API token the app can send: rmm_ + a sane, header-safe body."""
    return isinstance(tok, str) and tok.startswith("rmm_") and 20 <= len(tok) <= 512 \
        and re.fullmatch(r"[\x21-\x7e]+", tok) is not None


def exchange_pair_code(url, code, verify_tls=True, timeout=20):
    """Swap a RomM pairing code for a client API token. Returns
    {"token", "name", "scopes"}. Sends no Authorization header (the exchange
    is unauthenticated; a stale token has no business going along). Errors
    are RommError with a message for the user; the token never appears in one."""
    base = (url or "").strip().rstrip("/")
    if not base:
        raise _core.RommError("Set the server address first")
    if _config.url_has_userinfo(base):
        raise _core.RommError("Remove user:password@ from the server URL — the token is the login")
    c = normalize_pair_code(code)
    try:
        r = requests.post(base + "/api/client-tokens/exchange", json={"code": c},
                          headers={"User-Agent": _core.UA}, timeout=timeout,
                          verify=_config.norm_verify(verify_tls), allow_redirects=False)
    except requests.exceptions.SSLError:
        raise _core.RommError("TLS certificate rejected — set verify_tls in Settings") from None
    except requests.RequestException as e:
        raise _core.RommError("Can't reach RomM (%s)" % e.__class__.__name__) from None
    except Exception as e:
        raise _core.RommError("Request failed (%s)" % e.__class__.__name__) from None
    st = r.status_code
    if st in (400, 404, 410, 422):
        raise _core.RommError(PAIR_BAD_CODE)
    if st == 429:
        raise _core.RommError("Too many tries — wait a minute, then make a new code in RomM")
    if st == 403:
        raise _core.RommError("RomM refused: the token's owner is disabled (HTTP 403)")
    if st == 405 or 300 <= st < 400:
        raise _core.RommError("Not a RomM API response — check the URL (auth proxy in front?)")
    if st != 200:
        raise _core.RommError("RomM error HTTP %d while pairing" % st)
    try:
        body = r.json()
    except ValueError:
        raise _core.RommError("Not a RomM API response — check the URL (auth proxy in front?)") from None
    tok = body.get("raw_token") if isinstance(body, dict) else None
    if not valid_token(tok):
        raise _core.RommError("RomM's reply had no usable token (is this RomM 5 or newer?)")
    name = body.get("name") if isinstance(body.get("name"), str) else ""
    scopes = [s for s in body.get("scopes") or [] if isinstance(s, str)]
    _core.log("paired with RomM: token %r, %d scopes" % (name[:60], len(scopes)))
    return {"token": tok, "name": name[:60], "scopes": scopes}


COVER_MAX = 32 << 20      # a cover bigger than this isn't a cover
BIOS_MAX = 1 << 30         # the largest Batocera firmware files are a few hundred MB


def _read_capped(r, cap):
    """A streamed response's body, refusing (RommError) more than cap bytes:
    never let a server make the app read an endless body into memory."""
    out, n = [], 0
    try:
        for chunk in r.iter_content(1 << 16):
            n += len(chunk)
            if n > cap:
                raise _core.RommError("Response larger than %s; refused." % _core.human_size(cap))
            out.append(chunk)
    finally:
        r.close()
    return b"".join(out)
